from __future__ import annotations

import asyncio
import hashlib
import runpy
import threading
from pathlib import Path
from typing import Callable

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
from scripts.rl_phase5.build_f7_target_launch_packet import (
    F7AuthorityClosure,
    F7AuthorityRef,
    F7BaselineObservation,
    F7ConfigAuthority,
    F7FrozenWorkload,
    F7FinalizerTemplate,
    F7ImmutableFileRef,
    F7TargetLaunchAuthoringInput,
    F7TargetWorkloadPayload,
    F7TopologyAuthoring,
    build_f7_target_launch_packet,
    _authority_from_f4,
    _source_entries,
)
from scripts.rl_phase5.run_f7_target_workload import (
    F7LeaseObservation,
    F7LoadObservation,
    F7MeasuredEpisode,
    F7RankOperationArtifact,
    F7ResourceSample,
    F7SlurmAuthority,
    F7TargetWorkloadError,
    _atomic_write,
    _control_observation,
    _run_f7_target_workload_for_test,
    finalize_f7_topology_gate_input,
    _validate_joined_operations,
)
from scripts.rl_phase5.run_f7_topology_gate import (
    F7CleanupObservation,
    F7ImmutableJSONRef,
    F7NodeMetrics,
    F7PinnedIdentity,
    F7TopologyGateInput,
    run_f7_topology_gate,
)


def _d(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _ref(label: str) -> F7AuthorityRef:
    digest = _d(label)
    return F7AuthorityRef(reference=f"breadboard://f7/{label}@{digest}", digest=digest)


def _authority() -> F7AuthorityClosure:
    return F7AuthorityClosure(
        runtime=_ref("runtime"),
        configs=tuple(
            F7ConfigAuthority(
                config_id=config_id,
                config_bundle_ref=_ref(f"config-{config_id}"),
                declared_row_timeout_ms=10.0,
            )
            for config_id in (
                "codex-like",
                "claude-like",
                "pi-like",
                "opencode",
                "oh-my-opencode",
                "unknown-name",
            )
        ),
        task=_ref("task"),
        model=_ref("model"),
        tokenizer=_ref("tokenizer"),
        checkpoint=_ref("checkpoint"),
        image=_ref("image"),
        verifier=_ref("verifier"),
        authority=_ref("authority"),
    )


def _file_ref(path: Path, media_type: str) -> F7ImmutableFileRef:
    raw = path.read_bytes()
    return F7ImmutableFileRef(
        path=str(path.resolve()),
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        media_type=media_type,
    )


def _json_ref(path: Path, value: object) -> F7ImmutableJSONRef:
    raw = canonical_json_bytes(value)
    path.write_bytes(raw)
    return F7ImmutableJSONRef(
        path=str(path.resolve()),
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def _workload() -> F7FrozenWorkload:
    return F7FrozenWorkload(
        load_levels=(1, 2, 4, 8, 16, 32),
        soak_total_seconds=7200,
        soak_warmup_seconds=900,
        soak_measured_seconds=6300,
        sample_interval_seconds=15,
        minimum_terminal_attempts=256,
        minimum_attempts_per_config=32,
        minimum_r_swe_attempts=64,
    )


def _payload(tmp_path: Path, node_count: int = 2) -> F7TargetWorkloadPayload:
    authority = _authority()
    f4 = tmp_path / "f4.json"
    f4.write_bytes(b"{}")
    scontrol = tmp_path / "scontrol"
    scontrol.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    scontrol.chmod(0o550)
    identity = authority.pinned_identity()
    baseline = F7BaselineObservation(
        schema_version="bb.rl.phase5-f7-baseline-observation.v1",
        identity=identity,
        elapsed_seconds=100.0,
        completed_episodes=1,
        control_plane_p95_ms=1.0,
        throughput_eps=0.05,
        episode_ids=("baseline-episode",),
        evidence_digests=(_d("baseline-evidence"),),
    )
    baseline_ref = _json_ref(
        tmp_path / "baseline.json", baseline.model_dump(mode="json")
    )
    topology_id = "two-node" if node_count == 2 else "four-node"
    return F7TargetWorkloadPayload(
        schema_version="bb.rl.phase5-f7-target-workload-payload.v1",
        packet_id="f7-packet",
        gate_id="f7-gate",
        topology=F7TopologyAuthoring(
            topology_id=topology_id,
            node_count=node_count,
            requested_target_run_id=f"f7-{topology_id}-pending",
            command_id=f"f7-{topology_id}",
            job_name=f"f7-{topology_id}",
        ),
        predecessor="none" if node_count == 2 else "two-node:passed",
        predecessor_receipt_relative_path=(
            None if node_count == 2 else "two-node/topology-complete.json"
        ),
        f4_target_input=_file_ref(f4, "application/json"),
        baseline_observation=baseline_ref,
        scontrol=_file_ref(scontrol, "application/x-executable"),
        authority=authority,
        expected_identity=identity,
        source_entries=_source_entries(),
        workload=_workload(),
        driver_rank=0,
        tasks_per_node=1,
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_update_allowed=False,
    )


def _slurm(node_count: int, rank: int) -> F7SlurmAuthority:
    hosts = tuple(f"n{node_count}-{index}" for index in range(node_count))
    return F7SlurmAuthority(
        job_id=str(node_count * 100),
        task_rank=rank,
        node_rank=rank,
        local_rank=0,
        node_count=node_count,
        task_count=node_count,
        hostname=hosts[rank],
        allocated_hosts=hosts,
        nodelist_expression=f"n{node_count}-[0-{node_count - 1}]",
    )


def _episode(
    identity: F7PinnedIdentity,
    *,
    rank: int = 0,
    sequence: int = 0,
    config_id: str = "codex-like",
    lease_observations: tuple[F7LeaseObservation, ...] = (),
) -> F7MeasuredEpisode:
    start = 1_000_000_000 + sequence * 2_000_000
    return F7MeasuredEpisode(
        attempt_id=f"attempt-r{rank}-{sequence}",
        episode_id=f"episode-r{rank}-{sequence}",
        config_id=config_id,
        task_id="R-SWE-001",
        task_digest=identity.task_digest,
        model_digest=identity.model_digest,
        config_digest=identity.config_digest,
        output_digest=_d(f"output-{rank}-{sequence}"),
        evidence_digest=_d(f"evidence-{rank}-{sequence}"),
        monotonic_start_ns=start,
        monotonic_end_ns=start + 1_000_000,
        selection_latency_ms=0.25,
        effective_plan_latency_ms=0.5,
        total_latency_ms=1.0,
        disposition="succeeded",
        fault_injected=False,
        lease_observations=lease_observations,
    )


def _operation(
    payload: F7TargetWorkloadPayload,
    rank: int,
    *,
    episodes: tuple[F7MeasuredEpisode, ...] | None = None,
    driver: bool | None = None,
) -> F7RankOperationArtifact:
    slurm = _slurm(payload.topology.node_count, rank)
    if episodes is None:
        episodes = tuple(
            _episode(
                payload.expected_identity,
                rank=rank,
                sequence=index,
                config_id=(
                    "codex-like",
                    "claude-like",
                    "pi-like",
                    "opencode",
                    "oh-my-opencode",
                    "unknown-name",
                )[index % 6],
            )
            for index in range(210 if payload.topology.node_count == 2 else 105)
        )
    samples = tuple(
        F7ResourceSample(
            sample_id=f"sample-{payload.topology.topology_id}-r{rank}-{index}",
            monotonic_ns=(index + 1) * 15_000_000_000,
            rss_bytes=100_000_000,
            cpu_time_ns=index * 1_000_000,
            fd_count=10,
            queue_depth=0,
            cache_entries=index,
            active_resource_count=0,
        )
        for index in range(480)
    )
    driver_row = rank == 0 if driver is None else driver
    load_observations = ()
    if driver_row:
        load_observations = tuple(
            F7LoadObservation(
                target_sessions=level,
                monotonic_start_ns=level * 1_000_000_000,
                monotonic_end_ns=level * 1_000_000_000 + level * 1_000_000,
                episodes=tuple(
                    _episode(
                        payload.expected_identity,
                        rank=rank,
                        sequence=900_000 + level * 100 + index,
                        config_id="codex-like",
                    )
                    for index in range(level)
                ),
            )
            for level in (1, 2, 4, 8, 16, 32)
        )
    return F7RankOperationArtifact(
        schema_version="bb.rl.phase5-f7-rank-operation.v1",
        packet_id=payload.packet_id,
        payload_digest=_d("payload"),
        topology_id=payload.topology.topology_id,
        target_run_id=f"f7-{payload.topology.topology_id}-actual",
        command_id=payload.topology.command_id,
        slurm=slurm,
        identity=payload.expected_identity,
        driver_ranks=(0,) if driver_row else (),
        monotonic_start_ns=0,
        monotonic_end_ns=7_200_000_000_000,
        warmup_seconds=900,
        measured_seconds=6300,
        sample_interval_seconds=15,
        resource_samples=samples,
        warmup_episodes=(
            _episode(
                payload.expected_identity,
                rank=rank,
                sequence=800_000,
                config_id="codex-like",
            ),
        ),
        measured_episodes=episodes,
        load_observations=load_observations,
        cleanup=F7CleanupObservation(
            active_lease_ids=(),
            orphan_resource_ids=(),
            remaining_process_ids=(),
            remaining_container_ids=(),
            cleanup_errors=(),
        ),
    )


class _VirtualClock:
    def __init__(self) -> None:
        self.nanoseconds = 0
        self._lock = threading.Lock()

    def ns(self) -> int:
        with self._lock:
            return self.nanoseconds

    def seconds(self) -> float:
        return self.ns() / 1_000_000_000

    async def sleep(self, seconds: float) -> None:
        with self._lock:
            self.nanoseconds += max(0, int(seconds * 1_000_000_000))
        await asyncio.sleep(0)

    def advance(self, nanoseconds: int) -> None:
        with self._lock:
            self.nanoseconds += nanoseconds


class _Driver:
    def __init__(self, clock: _VirtualClock) -> None:
        self.clock = clock
        self.cache_entries = 0

    async def start(self) -> None:
        return None

    async def execute(
        self,
        *,
        config_index: int,
        attempt_id: str,
        episode_id: str,
        identity: F7PinnedIdentity,
        hostname: str,
        monotonic_ns: Callable[[], int],
    ) -> F7MeasuredEpisode:
        del hostname, monotonic_ns
        start = self.clock.ns()
        self.clock.advance(1_000_000)
        self.cache_entries += 1
        return F7MeasuredEpisode(
            attempt_id=attempt_id,
            episode_id=episode_id,
            config_id=(
                "codex-like",
                "claude-like",
                "pi-like",
                "opencode",
                "oh-my-opencode",
                "unknown-name",
            )[config_index],
            task_id="R-SWE-001",
            task_digest=identity.task_digest,
            model_digest=identity.model_digest,
            config_digest=identity.config_digest,
            output_digest=_d("output-" + attempt_id),
            evidence_digest=_d("evidence-" + attempt_id),
            monotonic_start_ns=start,
            monotonic_end_ns=self.clock.ns(),
            selection_latency_ms=0.25,
            effective_plan_latency_ms=0.5,
            total_latency_ms=1.0,
            disposition="succeeded",
            fault_injected=False,
            lease_observations=(),
        )

    def resource_sample(self, sample_id: str, monotonic_ns: int) -> F7ResourceSample:
        return F7ResourceSample(
            sample_id=sample_id,
            monotonic_ns=monotonic_ns,
            rss_bytes=100_000_000,
            cpu_time_ns=monotonic_ns // 100,
            fd_count=10,
            queue_depth=0,
            cache_entries=self.cache_entries,
            active_resource_count=0,
        )

    async def close(self) -> F7CleanupObservation:
        return F7CleanupObservation(
            active_lease_ids=(),
            orphan_resource_ids=(),
            remaining_process_ids=(),
            remaining_container_ids=(),
            cleanup_errors=(),
        )

    def secret_values(self) -> tuple[bytes, ...]:
        return ()


def test_builder_is_deterministic_and_freezes_ordered_payloads(tmp_path: Path) -> None:
    sibling = Path(__file__).with_name("test_run_f4_target_canaries.py")
    campaign = runpy.run_path(str(sibling))["_campaign"]
    f4, _, _ = campaign(tmp_path)
    f4_path = tmp_path / "f4-target-input.json"
    f4_path.write_bytes(canonical_json_bytes(f4.model_dump(mode="json")))
    scontrol = tmp_path / "scontrol"
    scontrol.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    scontrol.chmod(0o550)
    timeout = {config_id: 10.0 for config_id in (
        "codex-like", "claude-like", "pi-like", "opencode", "oh-my-opencode", "unknown-name"
    )}
    tokenizer = _ref("tokenizer-authority")
    identity = _authority_from_f4(f4, tokenizer, timeout).pinned_identity()
    baseline = F7BaselineObservation(
        schema_version="bb.rl.phase5-f7-baseline-observation.v1",
        identity=identity,
        elapsed_seconds=10.0,
        completed_episodes=1,
        control_plane_p95_ms=1.0,
        throughput_eps=0.05,
        episode_ids=("baseline-episode",),
        evidence_digests=(_d("baseline-evidence"),),
    )
    baseline_ref = _json_ref(tmp_path / "builder-baseline.json", baseline.model_dump(mode="json"))
    spec = F7TargetLaunchAuthoringInput(
        schema_version="bb.rl.phase5-f7-target-launch-authoring-input.v1",
        packet_id="packet",
        gate_id="gate",
        f4_target_input=_file_ref(f4_path, "application/json"),
        tokenizer_ref=tokenizer,
        baseline_observation=baseline_ref,
        scontrol=_file_ref(scontrol, "application/x-executable"),
        config_timeout_ms=timeout,
        two_node=F7TopologyAuthoring(
            topology_id="two-node", node_count=2, requested_target_run_id="two-pending", command_id="two", job_name="two"
        ),
        four_node=F7TopologyAuthoring(
            topology_id="four-node", node_count=4, requested_target_run_id="four-pending", command_id="four", job_name="four"
        ),
    )
    first = build_f7_target_launch_packet(spec, str((tmp_path / "packet-a").resolve()))
    second = build_f7_target_launch_packet(spec, str((tmp_path / "packet-b").resolve()))
    assert first.payload_zip.digest == second.payload_zip.digest
    assert first.two_node_payload.digest == second.two_node_payload.digest
    assert first.four_node_payload.digest == second.four_node_payload.digest
    assert first.topology_order == ("two-node", "four-node")


def test_frozen_workload_rejects_short_soak_and_sample_cadence() -> None:
    with pytest.raises(ValidationError):
        F7FrozenWorkload(
            load_levels=(1, 2, 4, 8, 16, 32),
            soak_total_seconds=60,
            soak_warmup_seconds=0,
            soak_measured_seconds=60,
            sample_interval_seconds=1,
            minimum_terminal_attempts=1,
            minimum_attempts_per_config=1,
            minimum_r_swe_attempts=1,
        )


def test_slurm_authority_rejects_host_collapse_and_rank_mismatch() -> None:
    with pytest.raises(ValidationError, match="duplicate host"):
        F7SlurmAuthority(
            job_id="200", task_rank=0, node_rank=0, local_rank=0,
            node_count=2, task_count=2, hostname="n0", allocated_hosts=("n0", "n0"), nodelist_expression="n0"
        )
    with pytest.raises(ValidationError, match="rank"):
        F7SlurmAuthority(
            job_id="200", task_rank=0, node_rank=1, local_rank=0,
            node_count=2, task_count=2, hostname="n1", allocated_hosts=("n0", "n1"), nodelist_expression="n[0-1]"
        )


def test_measured_episode_rejects_fake_metrics_and_duplicate_leases() -> None:
    identity = _authority().pinned_identity()
    with pytest.raises(ValidationError, match="not derived"):
        _episode(identity).model_copy(update={"total_latency_ms": 2.0}).model_dump()
        F7MeasuredEpisode.model_validate(
            {**_episode(identity).model_dump(mode="python"), "total_latency_ms": 2.0}, strict=True
        )
    lease = F7LeaseObservation(
        lease_id="lease", hostname="n0", label="distributed", distributed_execution_claim=True
    )
    with pytest.raises(ValidationError, match="duplicate lease"):
        F7MeasuredEpisode.model_validate(
            {**_episode(identity).model_dump(mode="python"), "lease_observations": (lease, lease)}, strict=True
        )


def test_head_local_lease_cannot_claim_distributed_execution() -> None:
    with pytest.raises(ValidationError, match="claim disagree"):
        F7LeaseObservation(
            lease_id="lease", hostname="head", label="head_local", distributed_execution_claim=True
        )


def test_cleanup_residue_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cleanup proof"):
        F7CleanupObservation(
            active_lease_ids=("lease",), orphan_resource_ids=(), remaining_process_ids=(),
            remaining_container_ids=(), cleanup_errors=()
        )


def test_rank_operation_rejects_driver_duplication_and_short_samples(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    row = _operation(payload, 1)
    with pytest.raises(ValidationError, match="driver placement"):
        F7RankOperationArtifact.model_validate(
            {**row.model_dump(mode="python"), "driver_ranks": (0,)}, strict=True
        )
    with pytest.raises(ValidationError, match="sample cadence"):
        F7RankOperationArtifact.model_validate(
            {**row.model_dump(mode="python"), "sample_interval_seconds": 16}, strict=True
        )


def test_join_rejects_duplicate_rank_and_identity_drift(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    rows = (_operation(payload, 0), _operation(payload, 1))
    slurm = _slurm(2, 0)
    with pytest.raises(F7TargetWorkloadError, match="rank"):
        _validate_joined_operations(
            payload, _d("payload"), slurm, "f7-two-node-actual", (rows[0], rows[0])
        )
    drifted = rows[1].model_copy(update={"identity": rows[1].identity.model_copy(update={"model_digest": _d("drift")})})
    with pytest.raises(F7TargetWorkloadError, match="identity drift"):
        _validate_joined_operations(
            payload, _d("payload"), slurm, "f7-two-node-actual", (rows[0], drifted)
        )


def test_control_rejects_missing_config_and_swe_quotas(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    episodes = tuple(
        _episode(payload.expected_identity, rank=rank, sequence=index, config_id="codex-like")
        for rank in range(2)
        for index in range(210)
    )
    rows = (
        _operation(payload, 0, episodes=episodes[:210]),
        _operation(payload, 1, episodes=episodes[210:]),
    )
    metrics = tuple(F7NodeMetrics(
        schema_version="bb.rl.phase5-f7-node-metrics.v1", topology_id="two-node", node_count=2,
        hostname=row.slurm.hostname, task_rank=row.slurm.task_rank, identity=row.identity,
        throughput_eps=1.0, p95_latency_ms=1.0, error_count=0, failure_count=0,
        rss_start_bytes=1, rss_peak_bytes=1, rss_end_bytes=1,
        episode_joins=tuple(episode.gate_join() for episode in row.measured_episodes),
        lease_ids=(), cleanup=row.cleanup,
    ) for row in rows)
    baseline = F7BaselineObservation.model_validate_json(Path(payload.baseline_observation.path).read_bytes(), strict=True)
    with pytest.raises(F7TargetWorkloadError, match="quota"):
        _control_observation(payload, rows[0].slurm, "f7-two-node-actual", rows, metrics, baseline)
    balanced = (_operation(payload, 0), _operation(payload, 1))
    no_swe = tuple(
        F7RankOperationArtifact.model_validate(
            {
                **row.model_dump(mode="python"),
                "measured_episodes": tuple(
                    episode.model_copy(update={"task_id": "R-HARD-001"})
                    for episode in row.measured_episodes
                ),
            },
            strict=True,
        )
        for row in balanced
    )
    balanced_metrics = tuple(
        F7NodeMetrics(
            schema_version="bb.rl.phase5-f7-node-metrics.v1",
            topology_id="two-node",
            node_count=2,
            hostname=row.slurm.hostname,
            task_rank=row.slurm.task_rank,
            identity=row.identity,
            throughput_eps=1.0,
            p95_latency_ms=1.0,
            error_count=0,
            failure_count=0,
            rss_start_bytes=1,
            rss_peak_bytes=1,
            rss_end_bytes=1,
            episode_joins=tuple(
                episode.gate_join() for episode in row.measured_episodes
            ),
            lease_ids=(),
            cleanup=row.cleanup,
        )
        for row in no_swe
    )
    with pytest.raises(F7TargetWorkloadError, match="R-SWE-001 quota"):
        _control_observation(
            payload,
            no_swe[0].slurm,
            "f7-two-node-actual",
            no_swe,
            balanced_metrics,
            baseline,
        )


def test_atomic_artifact_rejects_stale_output(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    value = F7BaselineObservation(
        schema_version="bb.rl.phase5-f7-baseline-observation.v1",
        identity=_authority().pinned_identity(), elapsed_seconds=1.0, completed_episodes=1,
        control_plane_p95_ms=1.0, throughput_eps=1.0, episode_ids=("episode",),
        evidence_digests=(_d("evidence"),),
    )
    _atomic_write(path, value)
    with pytest.raises(F7TargetWorkloadError, match="stale or duplicate"):
        _atomic_write(path, value)


def test_two_node_virtual_smoke_emits_current_joined_evidence(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    root = tmp_path / "campaign"
    results: list[object] = [None, None]
    errors: list[BaseException] = []

    def run(rank: int) -> None:
        clock = _VirtualClock()
        try:
            results[rank] = _run_f7_target_workload_for_test(
                payload,
                payload_digest=_d("payload"),
                campaign_root=str(root.resolve()),
                slurm=_slurm(2, rank),
                target_run_id="f7-two-node-actual",
                command_id="f7-two-node",
                driver=_Driver(clock),
                monotonic_ns=clock.ns,
                monotonic=clock.seconds,
                sleep=clock.sleep,
                join_timeout_seconds=1_000_000,
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(rank,)) for rank in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    rank_zero = results[0]
    assert rank_zero is not None
    assert rank_zero.control_observation_ref is not None
    assert rank_zero.completion_receipt_ref is not None
    assert (root / "two-node/topology-complete.json").is_file()


def test_virtual_campaign_finalizes_into_unchanged_gate_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "campaign-final"

    def run_topology(payload: F7TargetWorkloadPayload) -> None:
        node_count = payload.topology.node_count
        errors: list[BaseException] = []

        def run_rank(rank: int) -> None:
            clock = _VirtualClock()
            try:
                _run_f7_target_workload_for_test(
                    payload,
                    payload_digest=_d(f"payload-{node_count}"),
                    campaign_root=str(root.resolve()),
                    slurm=_slurm(node_count, rank),
                    target_run_id=f"f7-{payload.topology.topology_id}-actual",
                    command_id=payload.topology.command_id,
                    driver=_Driver(clock),
                    monotonic_ns=clock.ns,
                    monotonic=clock.seconds,
                    sleep=clock.sleep,
                    join_timeout_seconds=1_000_000,
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run_rank, args=(rank,))
            for rank in range(node_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []

    two = _payload(tmp_path, 2)
    run_topology(two)
    four = _payload(tmp_path, 4)
    run_topology(four)

    manifest_paths: list[Path] = []
    for payload in (two, four):
        node_count = payload.topology.node_count
        phase3 = root / payload.topology.topology_id / "phase3"
        command_logs = phase3 / "command_logs"
        command_logs.mkdir(parents=True)
        command_log = command_logs / f"{payload.topology.command_id}.log"
        command_log.write_text("real virtual workload command log\n", encoding="utf-8")
        hosts = tuple(
            f"n{node_count}-{index}" for index in range(node_count)
        )
        target_run_id = f"f7-{payload.topology.topology_id}-actual"
        manifest = {
            "schema_version": "bb.rl.phase3.command_log_manifest.v1",
            "target_run_id": target_run_id,
            "commands": [
                {
                    "command_id": payload.topology.command_id,
                    "argv": [
                        "run_phase3_target_command.py",
                        f"--nodes={node_count}",
                        f"--ntasks={node_count}",
                        "--ntasks-per-node=1",
                        f"--nodelist={','.join(hosts)}",
                        (
                            "--target-run-id="
                            + payload.topology.requested_target_run_id
                        ),
                        f"--command-id={payload.topology.command_id}",
                    ],
                    "raw_log_path": (
                        f"command_logs/{payload.topology.command_id}.log"
                    ),
                    "raw_log_sha256": (
                        "sha256:"
                        + hashlib.sha256(command_log.read_bytes()).hexdigest()
                    ),
                    "slurm_job_id": str(node_count * 100),
                    "target_run_id": target_run_id,
                    "node": hosts[0],
                    "nodes": list(hosts),
                    "allocated_hosts": list(hosts),
                    "started_at": "2026-07-15T00:00:00Z",
                    "completed_at": "2026-07-15T02:01:00Z",
                    "exit_code": 0,
                    "status": "passed",
                    "blocked_reason": "",
                    "component_passed": True,
                    "component_failed_count": 0,
                    "component_blocked_reasons": [],
                }
            ],
        }
        manifest_path = phase3 / "command-log-manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        manifest_paths.append(manifest_path)

    template = F7FinalizerTemplate(
        schema_version="bb.rl.phase5-f7-finalizer-template.v1",
        packet_id=two.packet_id,
        gate_id=two.gate_id,
        expected_identity=two.expected_identity,
        topology_order=("two-node", "four-node"),
        completion_receipts=(
            "two-node/topology-complete.json",
            "four-node/topology-complete.json",
        ),
        phase3_manifest_placeholders=(
            "PHASE3_TWO_NODE_MANIFEST",
            "PHASE3_FOUR_NODE_MANIFEST",
        ),
        promotion_authority=False,
        scorecard_update_allowed=False,
    )
    template_path = tmp_path / "finalizer-template.json"
    template_path.write_bytes(
        canonical_json_bytes(template.model_dump(mode="json"))
    )
    final_input_path = tmp_path / "final-input.json"
    final_ref = finalize_f7_topology_gate_input(
        template_path=str(template_path.resolve()),
        campaign_root=str(root.resolve()),
        two_node_manifest_path=str(manifest_paths[0].resolve()),
        four_node_manifest_path=str(manifest_paths[1].resolve()),
        output_path=str(final_input_path.resolve()),
    )
    final_input = F7TopologyGateInput.model_validate_json(
        final_input_path.read_bytes(), strict=True
    )
    report = run_f7_topology_gate(
        final_input,
        input_digest=final_ref.digest,
        output_path=str((tmp_path / "final-report.json").resolve()),
    )
    assert report.passed is True
    assert report.topology_order == ("two-node", "four-node")
