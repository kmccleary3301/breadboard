from __future__ import annotations
import json

from pathlib import Path

from breadboard.rl.phase3.evidence import PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, PHASE3_COMPONENT_REPORT_SCHEMA, sha256_file, validate_phase3_command_log_manifest, validate_phase3_component_report

TARGET = "20260623T000000Z-slurm-234555"


def _manifest(tmp_path: Path, text: str = "ok") -> dict:
    raw = tmp_path / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    raw.parent.mkdir(parents=True)
    raw.write_text(text)
    return {
        "schema_version": PHASE3_COMMAND_LOG_MANIFEST_SCHEMA,
        "target_run_id": TARGET,
        "commands": [{
            "command_id": "cmd",
            "argv": ["python"],
            "raw_log_path": "command_logs/cmd.log",
            "raw_log_sha256": sha256_file(raw),
            "slurm_job_id": "234555",
            "target_run_id": TARGET,
            "node": "mi300x-1",
            "started_at": "t0",
            "completed_at": "t1",
            "exit_code": 0,
            "status": "passed",
        }],
    }


def test_stale_raw_log_hash_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    raw = tmp_path / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    raw.write_text("changed")
    errors = validate_phase3_command_log_manifest(manifest, target_run_id=TARGET, repo_root=tmp_path, evidence_root=tmp_path)
    assert any("raw_log_sha256" in error for error in errors)


def test_missing_slurm_fields_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["commands"][0]["slurm_job_id"] = ""
    errors = validate_phase3_command_log_manifest(manifest, target_run_id=TARGET, repo_root=tmp_path, evidence_root=tmp_path)
    assert any("slurm_job_id" in error for error in errors)


def test_duplicate_command_id_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["commands"].append(dict(manifest["commands"][0]))
    errors = validate_phase3_command_log_manifest(manifest, target_run_id=TARGET, repo_root=tmp_path, evidence_root=tmp_path)
    assert any("unique" in error for error in errors)



def test_command_manifest_rejects_inline_report_without_passed_true(tmp_path: Path) -> None:
    payload = {
        "schema_version": PHASE3_COMPONENT_REPORT_SCHEMA,
        "component": "gate",
        "claim_boundary": "boundary",
        "target_run_id": TARGET,
        "report_id": "r",
    }
    manifest = _manifest(tmp_path, f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(payload)}\n")

    errors = validate_phase3_command_log_manifest(manifest, target_run_id=TARGET, repo_root=tmp_path, evidence_root=tmp_path)

    assert "commands[1].inline_reports[1].passed must be true" in errors


def test_command_manifest_accepts_legacy_passed_inline_without_component_flag(tmp_path: Path) -> None:
    payload = {
        "schema_version": PHASE3_COMPONENT_REPORT_SCHEMA,
        "component": "gate",
        "claim_boundary": "boundary",
        "target_run_id": TARGET,
        "report_id": "r",
        "passed": True,
    }
    manifest = _manifest(tmp_path, f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(payload)}\n")

    errors = validate_phase3_command_log_manifest(manifest, target_run_id=TARGET, repo_root=tmp_path, evidence_root=tmp_path)

    assert errors == []

def test_generic_passed_true_component_rejected(tmp_path: Path) -> None:
    report = {"schema_version": PHASE3_COMPONENT_REPORT_SCHEMA, "claim_boundary": "boundary", "target_run_id": TARGET, "passed": True, "scorecard_update_allowed": False, "report_id": "r"}
    errors = validate_phase3_component_report(report, expected_schema=PHASE3_COMPONENT_REPORT_SCHEMA, expected_claim_boundary="boundary", target_run_id=TARGET, required_artifact_keys=("artifact",), evidence_root=tmp_path)
    assert any("component" in error or "input_hashes" in error or "artifact_paths" in error for error in errors)


def test_component_report_with_real_artifact_passes(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}")
    report = {
        "schema_version": PHASE3_COMPONENT_REPORT_SCHEMA,
        "component": "gate",
        "claim_boundary": "boundary",
        "target_run_id": TARGET,
        "passed": True,
        "scorecard_update_allowed": False,
        "report_id": "r",
        "input_hashes": {"artifact": sha256_file(artifact)},
        "artifact_paths": {"artifact": "artifact.json"},
    }
    assert validate_phase3_component_report(report, expected_schema=PHASE3_COMPONENT_REPORT_SCHEMA, expected_claim_boundary="boundary", target_run_id=TARGET, required_artifact_keys=("artifact",), evidence_root=tmp_path) == []
