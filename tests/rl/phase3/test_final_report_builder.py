from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from breadboard.rl.phase3.final_report import PHASE3_CORE_CLAIM_BOUNDARY, PHASE3_CORE_READINESS_SCHEMA, PHASE3_FINAL_CLAIM_BOUNDARY, PHASE3_FINAL_REPORT_ID, PHASE3_MILESTONE_BLOCKED_CLAIM_BOUNDARIES, PHASE3_MILESTONE_CLAIM_BOUNDARIES, PHASE3_MILESTONES, build_phase3_core_readiness, build_phase3_final_report, validate_phase3_final_report
from scripts.rl_phase3.build_phase3_final_report import _collect_milestone_reports, _load_json, _scorecard


def write_report(path: Path, *, milestone_id: str, report_id: str, passed: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"milestone_id": milestone_id, "report_id": report_id, "passed": passed}) + "\n")


def valid_final_report(tmp_path: Path) -> dict:
    target = "20260624T040000Z-slurm-243958"
    reports = {}
    for milestone in PHASE3_MILESTONES:
        report_path = tmp_path / f"{milestone}.json"
        report_path.write_text("{}")
        reports[milestone] = {
            "schema_version": "bb.rl.phase3.component_report.v1",
            "report_id": f"report-{milestone}",
            "milestone_id": milestone,
            "component": "component",
            "claim_boundary": PHASE3_MILESTONE_CLAIM_BOUNDARIES[milestone],
            "target_run_id": target,
            "points": 1,
            "passed": True,
            "input_hashes": {"input": "sha256:abc"},
            "artifact_paths": {"artifact": str(report_path)},
            "required_artifact_keys": ["artifact"],
            "scorecard_update_allowed": False,
        }
    harbor_routes = [
        "GET /health",
        "GET /metrics.json",
        "GET /list_tasks",
        "POST /score",
        "POST /trial/create",
        "POST /trial/{trial_id}/exec",
        "GET /trial/{trial_id}",
        "POST /trial/{trial_id}/finalize",
    ]
    reports["P3-M9"]["claim_boundary"] = PHASE3_MILESTONE_CLAIM_BOUNDARIES["P3-M9"]
    reports["P3-M9"]["provider_kind"] = "harbor_facade"
    reports["P3-M9"]["provider_report"] = {
        "schema_version": "bb.rl.phase3.harbor_service_proof.v1",
        "report_id": "phase3_harbor_service_proof",
        "claim_boundary": "phase3_harbor_nemo_gym_named_endpoint_scope",
        "target_run_id": target,
        "attestation_backend": "harbor_facade",
        "provider_kind": "harbor_facade",
        "endpoint_identity": "https://harbor.example",
        "backend_identity": "harbor-test",
        "env_package_sha256": "sha256:env",
        "task_name": "phase3-harbor-smoke",
        "trial_id_sha256": "sha256:trial",
        "harbor_routes": harbor_routes,
        "harbor_calls": [
            {
                "method": route.split(" ", 1)[0],
                "route_template": route,
                "path": route.split(" ", 1)[1],
                "status_code": 200,
                "request_sha256": "sha256:req",
                "response_sha256": "sha256:resp",
                "latency_seconds": 0.1,
                "passed": True,
                "blocked_reason": "",
            }
            for route in harbor_routes
        ],
        "passed": True,
        "scorecard_update_allowed": False,
    }
    reports["P3-M11"]["observability_evidence"] = {
        "passed": True,
        "errors": [],
        "verifier_latency": 0.1,
        "metric_sections": {
            "object_store_metrics": {
                "object_store": "production_object_store",
            },
            "scheduler_metrics": {
                "source": "scheduler_control",
                "scheduler_control": {
                    "endpoint_present": True,
                    "token_present": True,
                },
            },
        },
    }
    reports["P3-M8"]["provider_kind"] = "none"
    reports["P3-M8"]["retirement_accepted"] = True
    reports["P3-M8"]["rubric_decision"] = "phase3_p3m8_provider_milestone_retired_and_accepted_20260707"
    reports["P3-M8"]["provider_report"] = {
        "schema_version": "bb.rl.phase3.retired_provider_milestone.v1",
        "report_id": "phase3_p3m8_retired_provider_milestone_accepted",
        "claim_boundary": PHASE3_MILESTONE_CLAIM_BOUNDARIES["P3-M8"],
        "target_run_id": target,
        "provider_kind": "none",
        "retirement_accepted": True,
        "rubric_decision": "phase3_p3m8_provider_milestone_retired_and_accepted_20260707",
        "passed": True,
        "scorecard_update_allowed": False,
    }
    runs_root = tmp_path / "ZYPHRA" / "RL_PHASE_3" / "runs"
    command_log = runs_root / "command_logs" / "cmd.log"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    command_log.write_text("ok")
    cmd_sha = "sha256:" + hashlib.sha256(command_log.read_bytes()).hexdigest()
    parity_artifacts = {}
    for name in (
        "reward_function",
        "accepted_projection_rows",
        "evidence_manifest",
        "metrics",
        "introspection_report",
        "runtime_ppo_script",
        "runtime_grpo_script",
        "runtime_closed_loop_script",
        "runtime_install_report",
    ):
        path = tmp_path / "parity" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n")
        parity_artifacts[name] = str(path)
    parity_path = tmp_path / "parity" / "phase3_parity_report.json"
    parity_payload = {
        "schema_version": "bb.rl.phase3.parity_report.v1",
        "report_id": "phase3_parity_report",
        "claim_boundary": "phase3_ppo_grpo_closed_loop_parity_named_scope",
        "target_run_id": target,
        "scorecard_update_allowed": False,
        "passed": True,
        "scorer": {},
        "rollout": {"rollout_name": "vllm"},
        "token_logprob": {},
        "checkpoint": {
            key: {
                "optimizer_step_count": 1,
                "checkpoint_changed": True,
                "checkpoint_before_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "checkpoint_after_sha256": "sha256:after",
                "device_count": 8,
                "n_gpus_per_node": 8,
            }
            for key in ("ppo", "grpo", "closed_loop")
        },
        "model_merge": {},
        "infra": {
            "runtime_evidence": {"container_image": "vllm/vllm-openai-rocm:nightly", "runtime_path": "/shared/bb-p3-root/phase3_vllm_verl_py312"},
            "introspection": {
                "verl_version": "0.8.0",
                "torch_version": "2.9.1+rocm6.4",
                "cuda_available": True,
                "device_count": 8,
                "devices": ["AMD Instinct MI300X"] * 8,
            },
        },
        "dataproto": {"dataproto_ok": True},
        "limitations": [],
        "checklist": {
            "C7_checkpoint_parity": {"status": "satisfied"},
            "C10_infrastructure_parity": {"status": "satisfied"},
        },
        "artifact_paths": parity_artifacts,
        "errors": [],
    }
    parity_path.write_text(json.dumps(parity_payload) + "\n")
    parity_sha = "sha256:" + hashlib.sha256(parity_path.read_bytes()).hexdigest()
    for milestone in ("P3-M2", "P3-M3", "P3-M4"):
        reports[milestone]["artifact_paths"]["parity_report"] = str(parity_path)
        reports[milestone]["input_hashes"]["parity_report"] = parity_sha
        reports[milestone]["parity_report_id"] = "phase3_parity_report"
    return build_phase3_final_report(
        target_run_id=target,
        milestone_reports=reports,
        command_log_manifest={
            "schema_version": "bb.rl.phase3.command_log_manifest.v1",
            "target_run_id": target,
            "commands": [
                {
                    "command_id": "cmd",
                    "argv": ["x"],
                    "raw_log_path": "command_logs/cmd.log",
                    "raw_log_sha256": cmd_sha,
                    "slurm_job_id": "1",
                    "target_run_id": target,
                    "node": "n",
                    "started_at": "a",
                    "completed_at": "b",
                    "exit_code": 0,
                    "status": "passed",
                }
            ],
        },
        scorecard={"reviewed_final_report_id": PHASE3_FINAL_REPORT_ID},
        claim_ledger_text=f"{target}\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
    )


def _replace_summary_from_report(report: dict, milestone_id: str) -> None:
    component = report["milestone_reports"][milestone_id]
    for summary in report["milestone_summaries"]:
        if summary["milestone_id"] == milestone_id:
            summary.update(
                {
                    "report_id": component.get("report_id"),
                    "schema_version": component.get("schema_version"),
                    "claim_boundary": component.get("claim_boundary"),
                    "passed": component.get("passed") is True,
                    "blocked_reason": component.get("blocked_reason", ""),
                    "claim_ready": component.get("passed") is True and not component.get("blocked_reason"),
                    "scorecard_update_allowed": component.get("scorecard_update_allowed"),
                }
            )
            return
    raise AssertionError(f"{milestone_id} summary missing")


def _defer_milestone(report: dict, milestone_id: str, blocked_reason: str) -> None:
    component = report["milestone_reports"][milestone_id]
    component["passed"] = False
    component["blocked_reason"] = blocked_reason
    component["points"] = 0
    component["claim_boundary"] = PHASE3_MILESTONE_BLOCKED_CLAIM_BOUNDARIES.get(milestone_id, PHASE3_MILESTONE_CLAIM_BOUNDARIES[milestone_id])
    component["scorecard_update_allowed"] = False
    _replace_summary_from_report(report, milestone_id)


def test_final_report_embeds_core_readiness_without_promoting_scorecard(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)

    core = report["core_readiness"]

    assert core["schema_version"] == PHASE3_CORE_READINESS_SCHEMA
    assert core["claim_boundary"] == PHASE3_CORE_CLAIM_BOUNDARY
    assert core["ready"] is True
    assert core["report_label"] == "active-artifact-audit-clean"
    assert core["artifact_audit_clean"] is True
    assert core["ready_meaning"] == "Existing artifacts satisfy the promoted exact-scope Phase 3 boundary; broader or successor claims require separate canonical promotion."
    assert core["scorecard_update_allowed"] is False
    assert core["retired_milestones"] == []
    assert "P3-M8" in core["active_milestones"]
    assert "P3-M9" in core["active_milestones"]
    assert "P3-M11" in core["active_milestones"]
    assert "P3-M11" in {status["milestone_id"] for status in core["milestone_statuses"]}
    assert core["blocked_active_milestones"] == []
    assert report["scorecard_update_allowed"] is False


def test_final_report_core_readiness_blocks_when_a_milestone_is_deferred(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    _defer_milestone(report, "P3-M11", "missing_live_observability_scheduler_store")
    report["core_readiness"] = build_phase3_core_readiness(report["milestone_reports"])
    report["active_scope"] = report["core_readiness"]

    core = report["core_readiness"]
    p3m11_summary = next(summary for summary in report["milestone_summaries"] if summary["milestone_id"] == "P3-M11")

    assert report["milestone_reports"]["P3-M11"]["passed"] is False
    assert p3m11_summary["passed"] is False
    assert core["ready"] is False
    assert core["report_label"] == "active-artifact-audit-blocked"
    assert core["artifact_audit_clean"] is False
    assert core["deferred_milestones"] == []
    assert "P3-M11" in core["core_milestones"]
    assert "P3-M11" in core["blocked_core_milestones"]


def test_final_report_core_readiness_blocks_p3m7_fixture_benchmark_credit(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M7"]["benchmark_report"] = {"source_pin": {"benchmark_version": "fixture-v2"}}
    report["core_readiness"] = build_phase3_core_readiness(report["milestone_reports"])
    report["active_scope"] = report["core_readiness"]

    core = report["core_readiness"]
    p3m7 = next(status for status in core["milestone_statuses"] if status["milestone_id"] == "P3-M7")

    assert core["ready"] is False
    assert core["report_label"] == "active-artifact-audit-blocked"
    assert core["blocked_active_milestones"] == ["P3-M7"]
    assert p3m7["blocker"] == "fixture_benchmark_not_external_core_credit"
    assert validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path) == []



def test_final_report_core_readiness_blocks_hand_written_benchmark_candidates(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M7"]["benchmark_report"] = {
        "prompt_solution_leakage_scan": {
            "candidate_source": "hand_written_baseline_in_target_payload",
        }
    }
    report["core_readiness"] = build_phase3_core_readiness(report["milestone_reports"])
    report["active_scope"] = report["core_readiness"]

    core = report["core_readiness"]
    p3m7 = next(status for status in core["milestone_statuses"] if status["milestone_id"] == "P3-M7")

    assert core["ready"] is False
    assert core["blocked_active_milestones"] == ["P3-M7"]
    assert p3m7["blocker"] == "benchmark_candidate_not_phase3_model_pipeline"
    assert validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path) == [
        "P3-M7 benchmark candidate source must come from the Phase 3 model pipeline"
    ]

def test_collect_milestone_reports_prefers_canonical_directory(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    write_report(runs / "live_provider_reports" / "P3-M8_retired_provider_milestone.json", milestone_id="P3-M8", report_id="auxiliary", passed=True)
    write_report(runs / "milestone_reports" / "P3-M8_retired_provider_milestone.json", milestone_id="P3-M8", report_id="canonical", passed=False)

    reports = _collect_milestone_reports(runs)

    assert reports["P3-M8"]["report_id"] == "canonical"
    assert reports["P3-M8"]["passed"] is False

def test_collect_milestone_reports_ignores_unknown_milestone_ids(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    write_report(runs / "milestone_reports" / "stray.json", milestone_id="P3-M99", report_id="stray", passed=True)
    write_report(runs / "milestone_reports" / "P3-M8_retired_provider_milestone.json", milestone_id="P3-M8", report_id="canonical", passed=False)

    reports = _collect_milestone_reports(runs)

    assert list(reports) == ["P3-M8"]
    assert reports["P3-M8"]["report_id"] == "canonical"


def test_final_report_script_ignores_non_object_json_inputs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "milestone_reports").mkdir(parents=True)
    (runs / "phase3_command_log_manifest.json").write_text("[]")
    (runs / "milestone_reports" / "array.json").write_text("[]")
    (runs / "milestone_reports" / "object_milestone.json").write_text(json.dumps({"milestone_id": ["P3-M9"], "passed": True}))
    write_report(runs / "milestone_reports" / "P3-M8_retired_provider_milestone.json", milestone_id="P3-M8", report_id="canonical", passed=True)

    reports = _collect_milestone_reports(runs)

    assert _load_json(runs / "phase3_command_log_manifest.json") == {}
    assert list(reports) == ["P3-M8"]
    assert reports["P3-M8"]["report_id"] == "canonical"

def test_final_report_cli_handles_directory_inputs(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "phase3_command_log_manifest.json").mkdir()
    (phase_dir / "BB_ZYPHRA_RL_PHASE_3_CLAIM_LEDGER.md").mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/build_phase3_final_report.py",
            "--phase-dir",
            str(phase_dir),
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    report = json.loads((runs / "p3_m12_final_report.json").read_text())
    assert {
        "schema_version must be bb.rl.phase3.command_log_manifest.v1",
        "target_run_id must match Phase 3 Slurm target run id pattern",
        "commands must contain at least one command row",
    }.issubset(set(report["validation_errors"]))
    assert report["command_log_manifest"] == {}

def test_scorecard_malformed_total_points_defaults_to_zero(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    phase_dir.mkdir(parents=True)
    (phase_dir / "BB_ZYPHRA_RL_PHASE_3_SCORECARD.yaml").write_text(
        "current_verified_points: 1000\n"
        "total_points: not-a-number\n"
        f"reviewed_final_report_id: {PHASE3_FINAL_REPORT_ID}\n"
    )

    scorecard = _scorecard(phase_dir)

    assert scorecard["current_verified_points"] == 1000
    assert scorecard["total_points"] == 0


def test_final_report_validator_rejects_wrong_schema(tmp_path: Path) -> None:
    report = build_phase3_final_report(
        target_run_id="20260624T040000Z-slurm-243958",
        milestone_reports={},
        command_log_manifest={},
        scorecard={},
        claim_ledger_text="",
    )
    report["schema_version"] = "stale"

    assert "schema_version must be Phase 3 final report schema" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_requires_final_report_id_in_ledger(tmp_path: Path) -> None:
    report = build_phase3_final_report(
        target_run_id="20260624T040000Z-slurm-243958",
        milestone_reports={},
        command_log_manifest={},
        scorecard={},
        claim_ledger_text=f"20260624T040000Z-slurm-243958\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
    )

    assert "claim ledger must contain target_run_id, final report id, and final claim boundary" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_builder_coerces_non_mapping_inputs() -> None:
    report = build_phase3_final_report(
        target_run_id="20260624T040000Z-slurm-243958",
        milestone_reports=[],  # type: ignore[arg-type]
        command_log_manifest=[],  # type: ignore[arg-type]
        scorecard=[],  # type: ignore[arg-type]
        claim_ledger_text={"bad": "container"},  # type: ignore[arg-type]
    )

    assert report["milestone_reports"] == {}
    assert report["command_log_manifest"] == {}
    assert report["scorecard"] == {}
    assert report["claim_ledger_text"] == ""


def test_final_report_validator_rejects_non_string_claim_ledger(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["claim_ledger_text"] = {"bad": "container"}

    assert "claim ledger must contain target_run_id, final report id, and final claim boundary" in validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)


def test_valid_final_report_fixture_has_no_errors(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)

    assert validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path) == []


def test_final_report_validator_rejects_mutated_milestone_claim_boundary(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M8"]["claim_boundary"] = "phase3_wrong_scope"

    assert (
        "P3-M8 claim_boundary must be phase3_retired_provider_milestone_accepted_scope"
        in validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)
    )



def test_final_report_validator_rejects_missing_p3m9_provider_kind(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    del report["milestone_reports"]["P3-M9"]["provider_kind"]

    assert "P3-M9 provider_kind must be harbor_facade for the active Harbor/NeMo Gym path" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_p3m9_native_benchflow_backend(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M9"]["provider_report"]["attestation_backend"] = "native_benchflow"

    errors = validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )
    assert "P3-M9 provider_report.attestation_backend must be harbor_facade for the active Harbor/NeMo Gym path" in errors
    assert "P3-M9 native BenchFlow evidence is contract-only and cannot satisfy the active Harbor/NeMo Gym milestone" in errors


def test_final_report_validator_rejects_missing_p3m9_report_backend(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    del report["milestone_reports"]["P3-M9"]["provider_report"]["attestation_backend"]

    assert "P3-M9 provider_report.attestation_backend must be harbor_facade for the active Harbor/NeMo Gym path" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_p3m9_missing_trial_hash(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    del report["milestone_reports"]["P3-M9"]["provider_report"]["trial_id_sha256"]

    assert "P3-M9 harbor provider_report.trial_id_sha256 must be present" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )

def test_final_report_validator_rejects_p3m9_harbor_missing_finalize_route(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    p3m9 = report["milestone_reports"]["P3-M9"]
    p3m9["provider_report"]["harbor_routes"] = [
        "GET /health",
        "GET /metrics.json",
        "POST /trial/create",
        "POST /trial/{trial_id}/exec",
    ]

    assert "P3-M9 harbor provider_report.harbor_routes must match the Harbor service proof route sequence" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )



def test_final_report_validator_accepts_p3m9_harbor_facade_routes(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)

    assert validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path) == []


def test_final_report_validator_rejects_p3m11_observability_errors(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M11"]["observability_evidence"]["errors"] = ["production_object_store_endpoint_missing"]

    assert "P3-M11 observability_evidence.errors must be empty" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_p3m11_local_object_store_backend(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    object_store_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["object_store_metrics"]
    object_store_metrics["object_store"] = "local_object_store"

    assert "P3-M11 object_store_metrics.object_store must be a production object-store backend" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_p3m11_target_workspace_local_object_store_backend(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    object_store_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["object_store_metrics"]
    object_store_metrics["object_store"] = "target_workspace_local_object_store"

    assert "P3-M11 object_store_metrics.object_store must be a production object-store backend" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_p3m11_local_object_store_class_backend(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    object_store_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["object_store_metrics"]
    object_store_metrics["object_store"] = "LocalObjectStore"

    assert "P3-M11 object_store_metrics.object_store must be a production object-store backend" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_p3m11_local_metric_urls(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    metric_sections = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]
    metric_sections["verifier_metrics"] = {"endpoint": "http://localhost:8080/verifier"}
    metric_sections["object_store_metrics"].update(
        {
            "endpoint": "http://127.0.0.1:9000",
            "put_endpoint": "http://127.0.0.1:9000/put",
            "get_endpoint": "http://127.0.0.1:9000/get",
            "delete_endpoint": "http://127.0.0.1:9000/delete",
        }
    )
    metric_sections["scheduler_metrics"]["scheduler_control"]["endpoint"] = "http://localhost:8081/scheduler"

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert {
        "P3-M11 verifier_metrics.endpoint must not be local",
        "P3-M11 object_store_metrics.endpoint must not be local",
        "P3-M11 object_store_metrics.put_endpoint must not be local",
        "P3-M11 object_store_metrics.get_endpoint must not be local",
        "P3-M11 object_store_metrics.delete_endpoint must not be local",
        "P3-M11 scheduler_metrics.scheduler_control.endpoint must not be local",
    }.issubset(errors)


def test_final_report_validator_rejects_p3m11_node_hostname_metric_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SLURMD_NODENAME", "cnode-143")
    report = valid_final_report(tmp_path)
    object_store_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["object_store_metrics"]
    object_store_metrics["endpoint"] = "https://cnode-143:9000"

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert "P3-M11 object_store_metrics.endpoint must not be local" in errors


def test_final_report_validator_rejects_p3m11_schemeless_local_metric_url(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    object_store_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["object_store_metrics"]
    object_store_metrics["endpoint"] = "127.0.0.1:9000"

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert "P3-M11 object_store_metrics.endpoint must not be local" in errors


def test_final_report_validator_rejects_p3m11_missing_scheduler_endpoint(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    scheduler_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["scheduler_metrics"]
    scheduler_metrics["scheduler_control"]["endpoint_present"] = False

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert "P3-M11 observability_evidence.metric_sections.scheduler_metrics.scheduler_control_endpoint_missing" in errors


def test_final_report_validator_rejects_p3m11_missing_scheduler_token(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    scheduler_metrics = report["milestone_reports"]["P3-M11"]["observability_evidence"]["metric_sections"]["scheduler_metrics"]
    scheduler_metrics["scheduler_control"]["token_present"] = False

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert "P3-M11 observability_evidence.metric_sections.scheduler_metrics.scheduler_control_token_missing" in errors

def test_final_report_validator_rejects_misfiled_milestone_id(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M10"]["milestone_id"] = "P3-M9"

    assert "P3-M10 report milestone_id must match outer milestone key" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_unknown_milestone_report_key(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    report["milestone_reports"]["P3-M99"] = dict(report["milestone_reports"]["P3-M0"])

    assert "P3-M99 report is not a Phase 3 milestone" in validate_phase3_final_report(
        report,
        repo_root=tmp_path,
        evidence_root=tmp_path,
    )


def test_final_report_validator_rejects_bad_parity_artifact_on_disk(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    parity_path = Path(report["milestone_reports"]["P3-M2"]["artifact_paths"]["parity_report"])
    parity_payload = json.loads(parity_path.read_text())
    parity_payload["infra"]["introspection"]["devices"] = ["AMD Instinct MI300X"] * 7
    parity_path.write_text(json.dumps(parity_payload) + "\n")
    bad_sha = "sha256:" + hashlib.sha256(parity_path.read_bytes()).hexdigest()
    for milestone in ("P3-M2", "P3-M3", "P3-M4"):
        report["milestone_reports"][milestone]["input_hashes"]["parity_report"] = bad_sha

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert "parity_report: infra.introspection.devices must list 8 AMD Instinct MI300X devices" in errors


def test_final_report_validator_rejects_stale_parity_hash(tmp_path: Path) -> None:
    report = valid_final_report(tmp_path)
    parity_path = Path(report["milestone_reports"]["P3-M2"]["artifact_paths"]["parity_report"])
    parity_payload = json.loads(parity_path.read_text())
    parity_payload["limitations"].append("changed after milestone snapshot")
    parity_path.write_text(json.dumps(parity_payload) + "\n")

    errors = validate_phase3_final_report(report, repo_root=tmp_path, evidence_root=tmp_path)

    assert "parity_report input hash must match artifact content" in errors
