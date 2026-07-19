from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from pydantic import ValidationError

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from scripts.rl_phase5.run_f7_topology_gate import (
    F4_CONFIG_IDS,
    F7CleanupObservation,
    F7FinalizeSpec,
    F7ImmutableJSONRef,
    F7NodeMetrics,
    F7PinnedIdentity,
    F7TaskLocalSpec,
    F7TopologyEvidenceSpec,
    F7TopologyGateError,
    F7TopologyGateInput,
    run_f7_task_local,
    run_f7_topology_gate,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _identity(label: str = "pinned") -> F7PinnedIdentity:
    return F7PinnedIdentity(
        runtime_digest=_digest(f"{label}-runtime"),
        config_digest=_digest(f"{label}-config"),
        task_digest=_digest(f"{label}-task"),
        model_digest=_digest(f"{label}-model"),
        tokenizer_digest=_digest(f"{label}-tokenizer"),
        checkpoint_digest=_digest(f"{label}-checkpoint"),
        image_digest=_digest(f"{label}-image"),
        verifier_digest=_digest(f"{label}-verifier"),
        authority_digest=_digest(f"{label}-authority"),
    )


def _cleanup() -> F7CleanupObservation:
    return F7CleanupObservation(
        active_lease_ids=(),
        orphan_resource_ids=(),
        remaining_process_ids=(),
        remaining_container_ids=(),
        cleanup_errors=(),
    )


def _write_canonical(path: Path, payload: Any) -> F7ImmutableJSONRef:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    raw = canonical_json_bytes(payload)
    path.write_bytes(raw)
    return F7ImmutableJSONRef(
        path=str(path.resolve()), digest="sha256:" + hashlib.sha256(raw).hexdigest()
    )


def _terminal_records() -> list[dict[str, Any]]:
    return [
        {
            "attempt_id": f"soak-attempt-{index}",
            "episode_id": f"soak-episode-{index}",
            "config_id": F4_CONFIG_IDS[index % len(F4_CONFIG_IDS)],
            "task_id": "R-SWE-001" if index < 64 else "R-GENERIC-001",
            "fault_injected": False,
            "disposition": "succeeded",
        }
        for index in range(256)
    ]


Mutation = Callable[[int, str, dict[str, Any]], None]


def _build_topology(
    root: Path,
    node_count: int,
    identity: F7PinnedIdentity,
    *,
    head_local: bool = False,
    mutate: Mutation | None = None,
) -> F7TopologyEvidenceSpec:
    topology_id = "two-node" if node_count == 2 else "four-node"
    hosts = tuple(f"n{node_count}-{index}" for index in range(node_count))
    target_run_id = f"20260713T000000Z-slurm-{node_count}00"
    requested_target_run_id = "20260713T000000Z-slurm-pending"
    command_id = f"f7-{topology_id}"
    job_id = f"{node_count}00"
    topology_root = root / topology_id
    topology_root.mkdir()
    command_log = topology_root / "command_logs" / f"{command_id}.log"
    command_log.parent.mkdir()
    command_log.write_text("f7 target command log\n")

    metric_refs: list[F7ImmutableJSONRef] = []
    for rank, host in enumerate(hosts):
        metrics = F7NodeMetrics(
            schema_version="bb.rl.phase5-f7-node-metrics.v1",
            topology_id=topology_id,
            node_count=node_count,
            hostname=host,
            task_rank=rank,
            identity=identity,
            throughput_eps=1.0,
            p95_latency_ms=25.0 + rank,
            error_count=0,
            failure_count=0,
            rss_start_bytes=1_000_000,
            rss_peak_bytes=1_100_000 + rank,
            rss_end_bytes=1_010_000,
            episode_joins=(
                {
                    "episode_id": f"episode-{node_count}-{rank}",
                    "attempt_id": f"attempt-{node_count}-{rank}",
                    "task_digest": identity.task_digest,
                    "model_digest": identity.model_digest,
                    "config_digest": identity.config_digest,
                    "output_digest": _digest(f"output-{node_count}-{rank}"),
                    "evidence_digest": _digest(f"evidence-{node_count}-{rank}"),
                    "disposition": "succeeded",
                },
            ),
            lease_ids=(f"lease-{node_count}-{rank}",) if (not head_local or rank == 0) else (),
            cleanup=_cleanup(),
        )
        metric_refs.append(_write_canonical(topology_root / f"metrics-{rank}.json", metrics))

    task_input = F7TopologyGateInput(
        schema_version="bb.rl.phase5-f7-topology-gate-input.v1",
        mode="task-local",
        task_local=F7TaskLocalSpec(
            topology_id=topology_id,
            node_count=node_count,
            requested_hosts=hosts,
            target_run_id=requested_target_run_id,
            command_id=command_id,
            slurm_job_id_source="SLURM_JOB_ID",
            expected_identity=identity,
            node_metrics_by_rank=tuple(metric_refs),
        ),
    )
    task_input_ref = _write_canonical(topology_root / "task-input.json", task_input)
    input_digest = task_input_ref.digest
    artifact_refs: list[F7ImmutableJSONRef] = []
    for rank, host in enumerate(hosts):
        output = topology_root / f"node-{rank}.json"
        artifact = run_f7_task_local(
            task_input,
            input_digest=input_digest,
            output_path=str(output.resolve()),
            environment={
                "SLURM_PROCID": str(rank),
                "SLURM_LOCALID": "0",
                "SLURM_JOB_ID": job_id,
                "PHASE3_TARGET_RUN_ID": requested_target_run_id,
                "PHASE3_COMMAND_ID": command_id,
            },
            observed_hostname=host,
        )
        artifact_payload = artifact.model_dump(mode="json")
        artifact_payload["target_run_id"] = target_run_id
        runner_report = topology_root / f"runner-node-{rank}.json"
        artifact_payload["artifact_paths"].update(
            {
                "component_report_json": str(runner_report.resolve()),
                "command_log": str(command_log.resolve()),
            }
        )
        if mutate is not None:
            mutate(node_count, "node", artifact_payload)
        runner_report.write_text(json.dumps(artifact_payload, sort_keys=True, indent=2) + "\n")
        raw = runner_report.read_bytes()
        artifact_refs.append(
            F7ImmutableJSONRef(
                path=str(runner_report.resolve()),
                digest="sha256:" + hashlib.sha256(raw).hexdigest(),
            )
        )

    command_argv = (
        "run_phase3_target_command.py",
        f"--nodes={node_count}",
        f"--ntasks={node_count}",
        "--ntasks-per-node=1",
        f"--nodelist={','.join(hosts)}",
        f"--target-run-id={requested_target_run_id}",
        f"--command-id={command_id}",
    )
    manifest_payload = {
        "schema_version": "bb.rl.phase3.command_log_manifest.v1",
        "target_run_id": target_run_id,
        "commands": [
            {
                "command_id": command_id,
                "argv": list(command_argv),
                "raw_log_path": f"command_logs/{command_id}.log",
                "raw_log_sha256": "sha256:" + hashlib.sha256(command_log.read_bytes()).hexdigest(),
                "slurm_job_id": job_id,
                "target_run_id": target_run_id,
                "node": hosts[0],
                "nodes": list(hosts),
                "allocated_hosts": list(hosts),
                "started_at": "2026-07-13T00:00:00Z",
                "completed_at": "2026-07-13T02:01:00Z",
                "exit_code": 0,
                "status": "passed",
                "blocked_reason": "",
                "component_passed": True,
                "component_failed_count": 0,
                "component_blocked_reasons": [],
            }
        ],
    }
    if mutate is not None:
        mutate(node_count, "manifest", manifest_payload)
    manifest_path = topology_root / "phase3_command_log_manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload, sort_keys=True, indent=2) + "\n")
    manifest_raw = manifest_path.read_bytes()
    manifest_ref = F7ImmutableJSONRef(
        path=str(manifest_path.resolve()),
        digest="sha256:" + hashlib.sha256(manifest_raw).hexdigest(),
    )

    control_payload: dict[str, Any] = {
        "schema_version": "bb.rl.phase5-f7-control-observation.v1",
        "topology_id": topology_id,
        "node_count": node_count,
        "target_run_id": target_run_id,
        "command_id": command_id,
        "slurm_job_id": job_id,
        "head_hostname": hosts[0],
        "identity": identity.model_dump(mode="json"),
        "cached_selection_p99_ms": 2.0,
        "effective_plan_resolution_p95_ms": 10.0,
        "cold_compile_p95_ms": 500.0,
        "baseline_control_plane_p95_ms": 100.0,
        "config_native_control_plane_p95_ms": 110.0,
        "baseline_throughput_eps": 100.0,
        "config_native_throughput_eps": 90.0,
        "evidence_sample_count": 256,
        "evidence_exact_join_count": 256,
        "evidence_sample_ids": [f"sample-{index}" for index in range(256)],
        "evidence_join_sample_ids": [f"sample-{index}" for index in range(256)],
        "identity_mismatch_count": 0,
        "mixed_config_latency": [
            {"config_id": config_id, "p95_latency_ms": 100.0, "declared_row_timeout_ms": 101.0}
            for config_id in F4_CONFIG_IDS
        ],
        "policy_version_integrity": True,
        "queue_backpressure_integrity": True,
        "load_ladder": [
            {
                "target_sessions": level,
                "status": "passed",
                "completed_sessions": level,
                "throughput_eps": float(level),
                "p95_latency_ms": 100.0,
                "error_count": 0,
                "failure_count": 0,
            }
            for level in (1, 2, 4, 8, 16, 32)
        ],
        "soak_duration_seconds": 7200,
        "soak_warmup_seconds": 900,
        "soak_measured_seconds": 6300,
        "soak_sample_interval_seconds": 15,
        "soak_rss_sample_count": 480,
        "soak_terminal_records": _terminal_records(),
        "first_30m_rss_p95_bytes": 1_000_000,
        "final_30m_rss_p95_bytes": 1_050_000,
        "five_minute_rss_medians_bytes": [1_000_000] * 24,
        "integrity_failure_count": 0,
        "identity_failure_count": 0,
        "cleanup_failure_count": 0,
        "secret_leak_failure_count": 0,
        "aggregate_throughput_eps": float(node_count),
        "cleanup": _cleanup().model_dump(mode="json"),
    }
    if mutate is not None:
        mutate(node_count, "control", control_payload)
    control_ref = _write_canonical(topology_root / "control.json", control_payload)

    return F7TopologyEvidenceSpec(
        topology_id=topology_id,
        node_count=node_count,
        requested_hosts=hosts,
        requested_target_run_id=requested_target_run_id,
        target_run_id=target_run_id,
        command_id=command_id,
        slurm_job_id=job_id,
        task_input=task_input_ref,
        phase3_manifest=manifest_ref,
        control_observation=control_ref,
        node_artifacts=tuple(artifact_refs),
    )


def _build_gate(
    tmp_path: Path,
    *,
    head_local: bool = False,
    mutate: Mutation | None = None,
) -> tuple[F7TopologyGateInput, Path]:
    identity = _identity()
    two = _build_topology(tmp_path, 2, identity, head_local=head_local, mutate=mutate)
    four = _build_topology(tmp_path, 4, identity, head_local=head_local, mutate=mutate)
    spec = F7TopologyGateInput(
        schema_version="bb.rl.phase5-f7-topology-gate-input.v1",
        mode="finalize",
        finalize=F7FinalizeSpec(
            gate_id="f7-production-topology",
            expected_identity=identity,
            topologies=(two, four),
        ),
    )
    return spec, tmp_path / "f7-report.json"


def _finalize(spec: F7TopologyGateInput, output: Path):
    return run_f7_topology_gate(
        spec,
        input_digest=_digest("final-input"),
        output_path=str(output.resolve()),
    )


def test_ordered_two_then_four_success_has_complete_hosts_tasks_and_thresholds(
    tmp_path: Path,
) -> None:
    spec, output = _build_gate(tmp_path)

    report = _finalize(spec, output)

    assert report.topology_order == ("two-node", "four-node")
    assert report.topologies[1].predecessor == "two-node:passed"
    for topology in report.topologies:
        assert set(topology.requested_hosts) == set(topology.allocated_hosts)
        assert set(topology.requested_hosts) == set(topology.observed_hosts)
        assert topology.task_ranks == tuple(range(topology.requested_node_count))
        assert len({node.hostname for node in topology.per_node}) == topology.requested_node_count
        assert topology.aggregate_throughput_eps == sum(
            node.throughput_eps for node in topology.per_node
        )
        assert topology.lease_topology.label == "distributed"
        assert topology.lease_topology.distributed_execution_claim is True
    assert report.thresholds["load_ladder_sessions"] == [1, 2, 4, 8, 16, 32]
    assert report.thresholds["soak_total_seconds_gte"] == 7200
    assert report.promotion_authority is False
    assert report.scorecard_authority is False
    assert output.read_bytes() == canonical_json_bytes(report.model_dump(mode="json"))
    with pytest.raises(FileExistsError):
        _finalize(spec, output)


def test_unproven_distributed_leases_are_exactly_head_local(tmp_path: Path) -> None:
    spec, output = _build_gate(tmp_path, head_local=True)

    report = _finalize(spec, output)

    for topology in report.topologies:
        assert topology.lease_topology.label == "head_local"
        assert topology.lease_topology.distributed_execution_claim is False
        assert topology.lease_topology.lease_hosts == (topology.requested_hosts[0],)


def test_rejects_hidden_single_node_execution(tmp_path: Path) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 4 and kind == "manifest":
            payload["commands"][0]["nodes"] = ["n4-0", "n4-0", "n4-0", "n4-0"]

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(F7TopologyGateError, match="observed host list"):
        _finalize(spec, output)
    assert not output.exists()


def test_rejects_incomplete_allocated_host_list(tmp_path: Path) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 2 and kind == "manifest":
            payload["commands"][0]["allocated_hosts"] = ["n2-0"]

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(F7TopologyGateError, match="allocated host list"):
        _finalize(spec, output)


def test_rejects_phase3_raw_log_digest_drift(tmp_path: Path) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 2 and kind == "manifest":
            payload["commands"][0]["raw_log_sha256"] = _digest("wrong-log")

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(F7TopologyGateError, match="raw command log digest"):
        _finalize(spec, output)


def test_rejects_node_artifact_immutable_input_digest_drift(tmp_path: Path) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 2 and kind == "node" and payload["task_rank"] == 1:
            payload["input_digest"] = _digest("wrong-input")

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(F7TopologyGateError, match="immutable input digest"):
        _finalize(spec, output)


def test_rejects_identity_drift(tmp_path: Path) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 4 and kind == "control":
            payload["identity"]["runtime_digest"] = _digest("drifted-runtime")

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(F7TopologyGateError, match="identity drift"):
        _finalize(spec, output)


def test_rejects_four_node_evidence_without_ordered_two_node_predecessor(tmp_path: Path) -> None:
    identity = _identity()
    four = _build_topology(tmp_path, 4, identity)

    with pytest.raises(ValidationError, match="ordered two-node then four-node"):
        F7FinalizeSpec(
            gate_id="early-four",
            expected_identity=identity,
            topologies=(four, four),
        )


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("soak_duration_seconds", 7199, "canonical soak"),
        ("cached_selection_p99_ms", 2.01, "cached selection"),
        ("effective_plan_resolution_p95_ms", 10.01, "effective-plan"),
        ("cold_compile_p95_ms", 500.01, "cold compile"),
        ("config_native_control_plane_p95_ms", 110.01, "overhead"),
        ("config_native_throughput_eps", 89.99, "throughput regressed"),
        ("identity_mismatch_count", 1, "identity mismatch"),
        ("integrity_failure_count", 1, "integrity/identity/cleanup/secret"),
    ],
)
def test_rejects_frozen_control_latency_throughput_identity_and_error_breaches(
    tmp_path: Path, field: str, bad_value: Any, message: str
) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 2 and kind == "control":
            payload[field] = bad_value

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(ValidationError, match=message):
        _finalize(spec, output)


@pytest.mark.parametrize(
    ("false_win", "message"),
    [
        ("duplicate_evidence_join", "evidence joins"),
        ("mixed_config_timeout", "mixed-config"),
        ("load_error", "Input should be 0"),
        ("wrong_sample_interval", "15s interval"),
        ("duplicate_terminal", "unique terminal"),
        ("thin_config", "at least 32"),
        ("thin_swe", "at least 64"),
        ("low_completion", "99.5"),
    ],
)
def test_rejects_credible_frozen_control_and_soak_false_wins(
    tmp_path: Path, false_win: str, message: str
) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count != 2 or kind != "control":
            return
        if false_win == "duplicate_evidence_join":
            payload["evidence_join_sample_ids"][-1] = payload["evidence_join_sample_ids"][0]
        elif false_win == "mixed_config_timeout":
            payload["mixed_config_latency"][0]["p95_latency_ms"] = 101.0
        elif false_win == "load_error":
            payload["load_ladder"][3]["error_count"] = 1
        elif false_win == "wrong_sample_interval":
            payload["soak_sample_interval_seconds"] = 30
        elif false_win == "duplicate_terminal":
            payload["soak_terminal_records"][-1]["attempt_id"] = payload[
                "soak_terminal_records"
            ][0]["attempt_id"]
        elif false_win == "thin_config":
            changed = 0
            for record in payload["soak_terminal_records"]:
                if record["config_id"] == F4_CONFIG_IDS[0] and changed < 12:
                    record["config_id"] = F4_CONFIG_IDS[1]
                    changed += 1
        elif false_win == "thin_swe":
            payload["soak_terminal_records"][0]["task_id"] = "R-GENERIC-001"
        else:
            payload["soak_terminal_records"][0]["disposition"] = "failed"
            payload["soak_terminal_records"][1]["disposition"] = "failed"

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(ValidationError, match=message):
        _finalize(spec, output)


@pytest.mark.parametrize("rss_mode", ["ratio", "monotonic"])
def test_rejects_frozen_rss_breaches(tmp_path: Path, rss_mode: str) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 2 and kind == "control":
            if rss_mode == "ratio":
                payload["final_30m_rss_p95_bytes"] = 1_050_001
            else:
                payload["five_minute_rss_medians_bytes"][:5] = [1, 2, 3, 4, 5]

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(ValidationError, match="RSS"):
        _finalize(spec, output)


def test_rejects_orphan_cleanup(tmp_path: Path) -> None:
    def mutate(node_count: int, kind: str, payload: dict[str, Any]) -> None:
        if node_count == 4 and kind == "control":
            payload["cleanup"]["orphan_resource_ids"] = ["orphan-1"]

    spec, output = _build_gate(tmp_path, mutate=mutate)

    with pytest.raises(ValidationError, match="cleanup proof"):
        _finalize(spec, output)


def test_task_local_cli_writes_exclusive_canonical_artifact_and_emits_component_json(
    tmp_path: Path,
) -> None:
    hostname = socket.gethostname()
    identity = _identity("cli")
    metrics = F7NodeMetrics(
        schema_version="bb.rl.phase5-f7-node-metrics.v1",
        topology_id="two-node",
        node_count=2,
        hostname=hostname,
        task_rank=0,
        identity=identity,
        throughput_eps=1.0,
        p95_latency_ms=25.0,
        error_count=0,
        failure_count=0,
        rss_start_bytes=1,
        rss_peak_bytes=2,
        rss_end_bytes=1,
        episode_joins=(
            {
                "episode_id": "cli-episode",
                "attempt_id": "cli-attempt",
                "task_digest": identity.task_digest,
                "model_digest": identity.model_digest,
                "config_digest": identity.config_digest,
                "output_digest": _digest("cli-output"),
                "evidence_digest": _digest("cli-evidence"),
                "disposition": "succeeded",
            },
        ),
        lease_ids=("cli-lease",),
        cleanup=_cleanup(),
    )
    rank_zero = _write_canonical(tmp_path / "rank-zero.json", metrics)
    other_payload = metrics.model_dump(mode="json")
    other_payload["hostname"] = "other-node"
    other_payload["task_rank"] = 1
    other_payload["episode_joins"][0]["episode_id"] = "other-episode"
    other_payload["episode_joins"][0]["attempt_id"] = "other-attempt"
    rank_one = _write_canonical(tmp_path / "rank-one.json", other_payload)
    spec = F7TopologyGateInput(
        schema_version="bb.rl.phase5-f7-topology-gate-input.v1",
        mode="task-local",
        task_local=F7TaskLocalSpec(
            topology_id="two-node",
            node_count=2,
            requested_hosts=(hostname, "other-node"),
            target_run_id="20260713T000000Z-slurm-900",
            command_id="f7-cli",
            slurm_job_id_source="SLURM_JOB_ID",
            expected_identity=identity,
            node_metrics_by_rank=(rank_zero, rank_one),
        ),
    )
    input_path = tmp_path / "input.json"
    input_path.write_bytes(canonical_json_bytes(spec.model_dump(mode="json")))
    output_path = tmp_path / "node-output.json"
    script = Path(__file__).resolve().parents[3] / "scripts/rl_phase5/run_f7_topology_gate.py"
    env = {
        **os.environ,
        "SLURM_PROCID": "0",
        "SLURM_LOCALID": "0",
        "SLURM_JOB_ID": "900",
        "PHASE3_TARGET_RUN_ID": "20260713T000000Z-slurm-900",
        "PHASE3_COMMAND_ID": "f7-cli",
    }

    result = subprocess.run(
        [sys.executable, str(script), "--input", str(input_path), "--output", str(output_path)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PHASE3_COMPONENT_REPORT_JSON=")
    component = json.loads(result.stdout.split("=", 1)[1])
    assert component["passed"] is True
    assert component["hostname"] == hostname
    assert component["promotion_authority"] is False
    assert component["scorecard_authority"] is False
    assert output_path.read_bytes() == canonical_json_bytes(component)
