from __future__ import annotations

import json
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path

from breadboard.rl.m12 import (
    build_m12_final_report,
    build_m12_promotion_audit,
    record_command_log_result,
    validate_m12_promotion_audit,
    write_m12_promotion_audit,
)
from breadboard.rl.m12.final_report import (
    OPTIONAL_COMMAND_LOG_COMMANDS,
    REQUIRED_COMMAND_LOG_COMMANDS,
    REQUIRED_COMMAND_LOG_IDS,
    TARGET_ARTIFACT_PATHS,
    TARGET_COMMAND_LOG_MANIFEST_PATH,
    TARGET_FINAL_REPORT_PATH,
    TARGET_PROMOTION_AUDIT_PATH,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
PHASE_DIR = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1"
PHASE_RUNS = PHASE_DIR / "runs"
SCORECARD = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
CLAIM_LEDGER = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"


def _target_path(tmp_path: Path, target_path: str) -> Path:
    path = tmp_path / target_path.removeprefix("../")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _target_final_report_path(tmp_path: Path) -> Path:
    return _target_path(tmp_path, TARGET_FINAL_REPORT_PATH)


def _target_command_manifest_path(tmp_path: Path) -> Path:
    return _target_path(tmp_path, TARGET_COMMAND_LOG_MANIFEST_PATH)


def _target_promotion_audit_path(tmp_path: Path) -> Path:
    return _target_path(tmp_path, TARGET_PROMOTION_AUDIT_PATH)


def _fingerprint_sha256(fingerprint: dict) -> str:
    payload = dict(fingerprint)
    payload.pop("sha256", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _write_wrapper_style_log(
    log_path: Path,
    *,
    command_id: str,
    command: str,
    exit_code: int = 0,
    started_at: str = "2026-06-17T00:00:00Z",
    completed_at: str = "2026-06-17T00:00:01Z",
    target_run_id: str = "m12-target-run-test",
) -> None:
    log_path.write_text(
        "\n".join(
            [
                f"# command_id: {command_id}",
                f"# target_run_id: {target_run_id}",
                f"# command: {command}",
                f"# argv_json: {json.dumps(shlex.split(command), ensure_ascii=True)}",
                f"# started_at: {started_at}",
                f"{command_id} passed",
                f"# completed_at: {completed_at}",
                f"# exit_code: {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _local_report() -> dict:
    return build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
    )


def _write_complete_command_manifest(tmp_path: Path) -> Path:
    manifest_path = _target_command_manifest_path(tmp_path)
    log_dir = manifest_path.parent / "logs"
    log_dir.mkdir()
    for command_id in [*REQUIRED_COMMAND_LOG_IDS, "final_report"]:
        log_path = log_dir / f"{command_id}.log"
        command = REQUIRED_COMMAND_LOG_COMMANDS.get(
            command_id,
            OPTIONAL_COMMAND_LOG_COMMANDS.get(command_id, f"echo {command_id}"),
        )
        _write_wrapper_style_log(log_path, command_id=command_id, command=command)
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=command,
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id="m12-target-run-test",
        )
    return manifest_path


def _eligible_report(command_manifest_path: Path) -> dict:
    target_run_id = "m12-target-run-test"
    report = _local_report()
    manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    command_entries = [item for item in manifest["commands"] if item["command_id"] in REQUIRED_COMMAND_LOG_IDS]
    report["m12_score_eligible"] = True
    report["missing_gates"] = []
    report["missing_gate_remediations"] = []
    report["artifact_paths"] = dict(TARGET_ARTIFACT_PATHS)
    report["artifact_path_policy"]["paths_match_target_defaults"] = True
    report["archive_verify"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["archive_verify_report"],
        "read_error": None,
        "report_id": "bb_zyphra_rl_phase1_m12_archive_verify_report_v1",
        "claim_boundary": "transfer_archive_verification_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "status": "passed",
        "archive_manifest_id": "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1",
        "archive_claim_boundary": "transfer_archive_only_not_m12_validation",
        "archive_sha256": "sha256:" + ("1" * 64),
        "included_entry_count": 213,
        "all_required_artifacts_present": True,
        "all_transfer_requirements_covered": True,
        "archive_contains_source_overlay": True,
        "archive_deterministic": True,
        "source_paths_portable": True,
        "errors": [],
        "validation_errors": [],
    }
    report["preflight"]["path"] = TARGET_ARTIFACT_PATHS["preflight_report"]
    report["swe_probe"]["path"] = TARGET_ARTIFACT_PATHS["swe_run_summary"]
    report["verl_export"]["path"] = TARGET_ARTIFACT_PATHS["verl_smoke_report"]
    report["ray_probe"]["path"] = TARGET_ARTIFACT_PATHS["ray_probe_report"]
    report["warm_vs_cold"]["path"] = TARGET_ARTIFACT_PATHS["warm_vs_cold_report"]
    report["preflight"]["status"] = "preflight_passed"
    report["preflight"]["target_run_id"] = target_run_id
    report["preflight"]["blockers"] = []
    report["preflight"]["gpu"]["rocminfo_available"] = True
    report["preflight"]["gpu"]["mi300x_product_evidence"] = True
    report["preflight"]["gpu"]["torch_probe"]["device_count"] = 8
    report["preflight"]["gpu"]["torch_probe"]["device_names"] = ["AMD Instinct MI300X"] * 8
    report["preflight"]["python_modules"]["verl"]["available"] = True
    report["preflight"]["python_modules"]["ray"]["available"] = True
    report["preflight"]["filesystem_cas_smoke"]["status"] = "passed"
    fingerprint = report["preflight"]["runtime_fingerprint"]
    fingerprint["tool_presence"] = {
        "rocm_smi": report["preflight"]["gpu"]["rocm_smi_available"],
        "rocminfo": report["preflight"]["gpu"]["rocminfo_available"],
        "docker": report["preflight"]["container_runtimes"]["docker"],
        "gvisor_runsc": report["preflight"]["container_runtimes"]["gvisor_runsc"],
        "firecracker": report["preflight"]["container_runtimes"]["firecracker"],
    }
    fingerprint["python_modules"] = report["preflight"]["python_modules"]
    fingerprint["gpu_summary"] = {
        "rocm_smi_output": report["preflight"]["gpu"]["rocm_smi_output"],
        "torch_probe": report["preflight"]["gpu"]["torch_probe"],
    }
    fingerprint["ray_summary"] = {"status_output": report["preflight"]["ray_cluster"]["status_output"]}
    fingerprint["sha256"] = _fingerprint_sha256(fingerprint)
    report["ray_probe"]["ray_local_mode"] = False
    report["swe_probe"]["target_run_id"] = target_run_id
    report["verl_export"]["target_run_id"] = target_run_id
    report["ray_probe"]["target_run_id"] = target_run_id
    report["warm_vs_cold"]["target_run_id"] = target_run_id
    report["load_ladder"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["load_ladder_report"],
        "target_run_id": target_run_id,
        "validation_errors": [],
        "required_levels": [5, 20, 50],
        "optional_levels": [100],
        "concurrency_levels": [
            {"target_sessions": 5, "status": "passed", "ray_local_mode": False},
            {"target_sessions": 20, "status": "passed", "ray_local_mode": False},
            {"target_sessions": 50, "status": "passed", "ray_local_mode": False},
        ],
        "resource_skips": [{"target_sessions": 100, "reason": "resource constrained target window"}],
        "policy_version_integrity": True,
        "queue_backpressure_integrity": True,
    }
    report["soak"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["soak_report"],
        "target_run_id": target_run_id,
        "validation_errors": [],
        "minimum_duration_seconds": 7200,
        "duration_seconds": 7200,
        "status": "passed",
        "runtime_failure_count": 0,
        "ray_local_mode": False,
    }
    report["command_logs"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["command_log_manifest"],
        "manifest_id": manifest["manifest_id"],
        "manifest_validation_errors": [],
        "required_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "manifest_required_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "archived_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "hash_verified_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "missing_command_log_ids": [],
        "target_run_ids": [target_run_id],
        "single_target_run_id": target_run_id,
        "command_text_mismatches": [],
        "all_required_logs_archived": True,
        "all_required_commands_passed": True,
        "command_count": len(command_entries),
        "commands": command_entries,
    }
    report["target_run_identity"] = {
        "single_command_log_target_run_id": target_run_id,
        "artifact_target_run_ids": {
            "preflight_report": target_run_id,
            "swe_run_summary": target_run_id,
            "verl_smoke_report": target_run_id,
            "ray_probe_report": target_run_id,
            "warm_vs_cold_report": target_run_id,
            "load_ladder_report": target_run_id,
            "soak_report": target_run_id,
        },
        "missing_artifact_target_run_ids": [],
        "mismatched_artifact_target_run_ids": {},
        "run_ids_match_command_logs": True,
    }
    return report


def test_m12_promotion_audit_rejects_local_noneligible_report(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    final_report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=tmp_path / "missing_command_log_manifest.json",
    )

    assert audit["audit_id"] == "bb_zyphra_rl_phase1_m12_promotion_audit_v1"
    assert audit["claim_boundary"] == "promotion_review_only_not_scorecard_update"
    assert audit["scorecard_update_allowed"] is False
    assert audit["promotion_review_ready"] is False
    assert "final_report.score_eligible" in audit["missing_requirements"]
    assert "command_log_manifest.path_exists" in audit["missing_requirements"]
    assert "scorecard.m12_state_valid_for_review" not in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_stale_missing_requirements(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    final_report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=tmp_path / "missing_command_log_manifest.json",
    )

    audit["missing_requirements"] = []
    audit["promotion_review_ready"] = True
    audit["checks"]["final_report"]["score_eligible"] = "yes"

    errors = validate_m12_promotion_audit(audit)

    assert "checks.final_report.score_eligible must be boolean" in errors
    assert "missing_requirements must match promotion-audit embedded checks" in errors
    assert "promotion_review_ready must match promotion-audit embedded checks" in errors


def test_m12_promotion_audit_rejects_malformed_command_log_manifest_without_crashing(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    command_manifest_path = tmp_path / "command_log_manifest.json"
    final_report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    command_manifest_path.write_text("{not valid command log json", encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["command_log_manifest_read_error"]
    assert "JSONDecodeError" in audit["command_log_manifest_read_error"]
    assert audit["command_log_manifest_errors"] == [
        f"command log manifest unreadable: {audit['command_log_manifest_read_error']}"
    ]
    assert "command_log_manifest.required_manifest_valid" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_malformed_final_report_without_crashing(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    command_manifest_path = tmp_path / "command_log_manifest.json"
    final_report_path.write_text("{not valid final report json", encoding="utf-8")
    command_manifest_path.write_text(json.dumps({"manifest_id": "bad"}), encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["final_report_read_error"]
    assert "JSONDecodeError" in audit["final_report_read_error"]
    assert audit["final_report_validation_errors"] == [
        f"final report unreadable: {audit['final_report_read_error']}"
    ]
    assert "final_report.schema_valid" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_malformed_scorecard_without_crashing(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    scorecard_path = tmp_path / "scorecard.yaml"
    command_manifest_path = tmp_path / "command_log_manifest.json"
    final_report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    scorecard_path.write_text("current_verified_points: [unterminated\n", encoding="utf-8")
    command_manifest_path.write_text(json.dumps({"manifest_id": "bad"}), encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=scorecard_path,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["scorecard_read_error"]
    assert "ParserError" in audit["scorecard_read_error"]
    assert audit["checks"]["scorecard"]["readable"] is False
    assert "scorecard.readable" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_missing_claim_ledger_without_crashing(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    command_manifest_path = tmp_path / "command_log_manifest.json"
    missing_claim_ledger_path = tmp_path / "missing_claim_ledger.md"
    final_report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    command_manifest_path.write_text(json.dumps({"manifest_id": "bad"}), encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=missing_claim_ledger_path,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["claim_ledger_read_error"] == "FileNotFoundError: text artifact is missing"
    assert audit["checks"]["claim_ledger"]["readable"] is False
    assert "claim_ledger.path_exists" in audit["missing_requirements"]
    assert "claim_ledger.readable" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_accepts_synthetic_complete_target_evidence(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = write_m12_promotion_audit(
        output_path=_target_promotion_audit_path(tmp_path),
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is True
    assert audit["missing_requirements"] == []
    assert audit["scorecard_update_allowed"] is False
    assert audit["checks"]["command_log_manifest"]["required_command_text_matches"] is True
    assert audit["checks"]["command_log_manifest"]["final_report_required_rows_match_manifest"] is True
    assert audit["checks"]["command_log_manifest"]["final_report_command_text_matches"] is True
    assert audit["checks"]["command_log_manifest"]["final_report_command_hash_verified"] is True
    assert audit["checks"]["promotion_audit_output"]["path_matches_target_default"] is True
    assert audit["scorecard_state"]["m12_verified_points"] == 80
    assert audit["scorecard_state"]["current_verified_points"] == 1000
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_arbitrary_output_path_for_complete_evidence(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = write_m12_promotion_audit(
        output_path=tmp_path / "m12_promotion_audit.json",
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["promotion_audit_output"]["path_matches_target_default"] is False
    assert "promotion_audit_output.path_matches_target_default" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_eligible_report_from_arbitrary_local_path(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    final_report_path = tmp_path / "m12_final_report.json"
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["final_report"]["path_matches_target_default"] is False
    assert "final_report.path_matches_target_default" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_copied_command_manifest_path(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    copied_manifest_path = tmp_path / "command_log_manifest.json"
    copied_manifest_path.write_text(command_manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=copied_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["command_log_manifest"]["path_matches_target_default"] is False
    assert "command_log_manifest.path_matches_target_default" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_copied_scorecard_and_claim_ledger_paths(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    copied_scorecard_path = tmp_path / "scorecard.yaml"
    copied_claim_ledger_path = tmp_path / "claim_ledger.md"
    copied_scorecard_path.write_text(SCORECARD.read_text(encoding="utf-8"), encoding="utf-8")
    copied_claim_ledger_path.write_text(CLAIM_LEDGER.read_text(encoding="utf-8"), encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=copied_scorecard_path,
        claim_ledger_path=copied_claim_ledger_path,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["scorecard"]["path_matches_target_default"] is False
    assert audit["checks"]["claim_ledger"]["path_matches_target_default"] is False
    assert "scorecard.path_matches_target_default" in audit["missing_requirements"]
    assert "claim_ledger.path_matches_target_default" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_passed_final_report_command_with_nonzero_exit_code(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    final_report_entry = next(item for item in manifest["commands"] if item["command_id"] == "final_report")
    final_report_log_path = command_manifest_path.parent / final_report_entry["log_path"]
    _write_wrapper_style_log(
        final_report_log_path,
        command_id="final_report",
        command=final_report_entry["command"],
        exit_code=4,
    )
    final_report_log_sha = "sha256:" + hashlib.sha256(final_report_log_path.read_bytes()).hexdigest()
    final_report_entry["status"] = "passed"
    final_report_entry["exit_code"] = 4
    final_report_entry["sha256"] = final_report_log_sha
    final_report_entry["attempts"][0]["status"] = "passed"
    final_report_entry["attempts"][0]["exit_code"] = 4
    final_report_entry["attempts"][0]["sha256"] = final_report_log_sha
    command_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert "command_log_manifest.required_manifest_valid" in audit["missing_requirements"]
    assert any(
        "invalid status/exit_code for final_report: passed commands must have exit_code 0" in error
        for error in audit["command_log_manifest_errors"]
    )
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_command_log_boundary_drift(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    manifest["claim_boundary"] = "scorecard_update_allowed"
    manifest["scorecard_update_allowed"] = True
    manifest["m12_points_awarded"] = True
    command_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert "command_log_manifest.required_manifest_valid" in audit["missing_requirements"]
    assert "claim_boundary must remain target_command_logs_not_scorecard_update" in audit["command_log_manifest_errors"]
    assert "scorecard_update_allowed must be false" in audit["command_log_manifest_errors"]
    assert "m12_points_awarded must be false" in audit["command_log_manifest_errors"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rechecks_required_command_text_from_raw_manifest(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    preflight_entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    preflight_entry["command"] = "python -c 'print(\"not target preflight\")'"
    command_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    final_report_path = _target_final_report_path(tmp_path)
    final_report = _eligible_report(command_manifest_path)
    final_report["command_logs"]["command_text_mismatches"] = []
    final_report_path.write_text(json.dumps(final_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["command_log_manifest"]["required_command_text_matches"] is False
    assert "command_log_manifest.required_command_text_matches" in audit["missing_requirements"]
    assert audit["required_command_text_mismatches"] == [
        {
            "command_id": "target_preflight",
            "expected": REQUIRED_COMMAND_LOG_COMMANDS["target_preflight"],
            "observed": "python -c 'print(\"not target preflight\")'",
        }
    ]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_final_report_without_canonical_required_ids(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    final_report = _eligible_report(command_manifest_path)
    final_report["command_logs"]["manifest_required_command_ids"] = ["target_preflight"]
    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(json.dumps(final_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert "final_report.schema_valid" in audit["missing_requirements"]
    assert "eligible report requires canonical command log required_command_ids" in audit["final_report_validation_errors"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_final_report_required_row_drift(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    final_report = _eligible_report(command_manifest_path)
    target_entry = next(
        item for item in final_report["command_logs"]["commands"] if item["command_id"] == "target_preflight"
    )
    target_entry["sha256"] = "sha256:" + ("0" * 64)
    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(json.dumps(final_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["command_log_manifest"]["final_report_required_rows_match_manifest"] is False
    assert "command_log_manifest.final_report_required_rows_match_manifest" in audit["missing_requirements"]
    assert audit["final_report_required_command_row_mismatches"] == [
        {"command_id": "target_preflight", "reason": "row_differs_from_manifest"}
    ]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_rejects_wrong_final_report_command_text(tmp_path) -> None:
    command_manifest_path = _write_complete_command_manifest(tmp_path)
    manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    final_report_entry = next(item for item in manifest["commands"] if item["command_id"] == "final_report")
    final_report_entry["command"] = "python -c 'print(\"not the final report builder\")'"
    command_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    final_report_path = _target_final_report_path(tmp_path)
    final_report_path.write_text(
        json.dumps(_eligible_report(command_manifest_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=SCORECARD,
        claim_ledger_path=CLAIM_LEDGER,
        command_log_manifest_path=command_manifest_path,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["checks"]["command_log_manifest"]["final_report_command_text_matches"] is False
    assert "command_log_manifest.final_report_command_text_matches" in audit["missing_requirements"]
    assert validate_m12_promotion_audit(audit) == []


def test_m12_promotion_audit_cli_require_ready_fails_closed_for_local_report(tmp_path) -> None:
    final_report_path = tmp_path / "m12_final_report.json"
    output_path = tmp_path / "m12_promotion_audit.json"
    final_report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/audit_m12_score_promotion.py",
            "--output",
            str(output_path),
            "--final-report",
            str(final_report_path),
            "--scorecard",
            str(SCORECARD),
            "--claim-ledger",
            str(CLAIM_LEDGER),
            "--command-log-manifest",
            str(tmp_path / "missing_command_log_manifest.json"),
            "--require-ready",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    assert output_path.exists()
    assert "promotion_review_ready=False" in result.stdout
