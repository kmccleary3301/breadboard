from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from breadboard.rl.m12 import (
    build_m12_evidence_consistency_report,
    validate_m12_evidence_consistency_report,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE_DIR = REPO_ROOT.parent / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1"


REQUIRED_PHASE_FILES = [
    "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml",
    "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md",
    "BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md",
    "BB_ZYPHRA_RL_PHASE_1_HANDOFF.md",
    "runs/m12_transfer_prep/m12_transfer_summary.json",
    "runs/m12_transfer_prep/m12_transfer_manifest.json",
    "runs/m12_transfer_prep/m12_transfer_archive_manifest.json",
    "runs/m12_transfer_prep/m12_archive_verify_report.json",
    "runs/m12_transfer_prep/m12_transfer_evidence_pack.tar.gz",
    "runs/m12_transfer_prep/m12_transfer_evidence_pack.tar.gz.sha256",
    "runs/m12_overlay_apply_probe/m12_overlay_apply_report.json",
    "runs/m12_bootstrap_dry_run/m12_bootstrap_dry_run_report.json",
    "runs/m12_target_preflight/m12_preflight_report.json",
    "runs/m12_transfer_prep/m12_readiness_summary.json",
    "runs/m12_final_report/m12_final_report.json",
    "runs/m12_final_report/m12_remediation_summary.json",
    "runs/m12_promotion_audit/m12_promotion_audit.json",
]


def _copy_required_phase_files(dst: Path) -> None:
    for rel in REQUIRED_PHASE_FILES:
        src = PHASE_DIR / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def test_m12_evidence_consistency_current_target_validated_state_passes() -> None:
    report = build_m12_evidence_consistency_report(phase_dir=PHASE_DIR)

    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_evidence_consistency_v1"
    assert report["claim_boundary"] == "m12_evidence_consistency_not_scorecard_update"
    assert report["scorecard_update_allowed"] is False
    assert report["m12_points_awarded"] is False
    assert report["consistent"] is True
    assert report["errors"] == []
    assert report["counts"]["scorecard_current_verified_points"] == 1000
    assert report["counts"]["m12_verified_points"] == 80
    assert report["counts"]["transfer_artifacts"] == 28
    assert report["counts"]["archive_entries"] == 277
    assert report["counts"]["overlay_would_write"] == 277
    assert report["counts"]["bootstrap_overlay_would_write"] == 277
    assert report["counts"]["overlay_existing_destinations"] > 0
    assert report["counts"]["transfer_commands"] == 10
    assert report["counts"]["transfer_expected_outputs"] == 15
    assert report["counts"]["archive_verify_entries"] == 277
    assert report["counts"]["local_final_missing_gates"] == 0
    assert report["counts"]["local_remediation_summary_actions"] == 0
    assert report["counts"]["local_promotion_missing_requirements"] == 0
    assert report["checks"]["claim_ledger"]["has_final_report_manifest_validation_claim"] is True
    assert report["checks"]["claim_ledger"]["has_promotion_row_equality_claim"] is True
    assert report["checks"]["claim_ledger"]["has_promotion_explicit_score_inputs_claim"] is True
    assert report["checks"]["claim_ledger"]["has_overlay_report_self_consistency_claim"] is True
    assert report["checks"]["claim_ledger"]["has_bootstrap_overlay_file_validation_claim"] is True
    assert report["checks"]["claim_ledger"]["has_promotion_final_report_path_gate_claim"] is True
    assert report["checks"]["claim_ledger"]["has_promotion_control_input_path_gates_claim"] is True
    assert report["checks"]["claim_ledger"]["has_promotion_output_path_gate_claim"] is True
    assert report["checks"]["claim_ledger"]["has_promotion_target_script_path_gate_claim"] is True
    assert report["checks"]["claim_ledger"]["has_target_run_log_reuse_guard_claim"] is True
    assert report["checks"]["claim_ledger"]["has_target_closeout_artifact_reuse_guard_claim"] is True
    assert report["checks"]["claim_ledger"]["has_cli_log_dir_preexecution_claim"] is True
    assert report["checks"]["claim_ledger"]["has_unknown_command_row_rejection_claim"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_cli_log_dir_preexecution"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_unknown_command_row_rejection"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_overlay_report_self_consistency"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_bootstrap_overlay_file_validation"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_promotion_final_report_path_gate"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_promotion_control_input_path_gates"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_promotion_output_path_gate"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_promotion_target_script_path_gates"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_target_run_log_reuse_guard"] is True
    assert report["checks"]["scorecard"]["allowed_claim_records_target_closeout_artifact_reuse_guard"] is True
    assert report["checks"]["m12_report"]["states_manifest_validation_gate"] is True
    assert report["checks"]["m12_report"]["states_promotion_row_equality_gate"] is True
    assert report["checks"]["m12_report"]["states_promotion_explicit_score_inputs"] is True
    assert report["checks"]["m12_report"]["states_cli_log_dir_preexecution"] is True
    assert report["checks"]["m12_report"]["states_unknown_command_row_rejection"] is True
    assert report["checks"]["m12_report"]["states_promotion_final_report_path_gate"] is True
    assert report["checks"]["m12_report"]["states_promotion_control_input_path_gates"] is True
    assert report["checks"]["m12_report"]["states_promotion_output_path_gate"] is True
    assert report["checks"]["m12_report"]["states_promotion_target_script_path_gate"] is True
    assert report["checks"]["m12_report"]["states_target_run_log_reuse_guard"] is True
    assert report["checks"]["m12_report"]["states_target_closeout_artifact_reuse_guard"] is True
    assert report["checks"]["handoff"]["states_manifest_validation_gate"] is True
    assert report["checks"]["handoff"]["states_promotion_row_equality_gate"] is True
    assert report["checks"]["handoff"]["states_promotion_explicit_score_inputs"] is True
    assert report["checks"]["handoff"]["states_cli_log_dir_preexecution"] is True
    assert report["checks"]["handoff"]["states_unknown_command_row_rejection"] is True
    assert report["checks"]["handoff"]["states_overlay_report_self_consistency"] is True
    assert report["checks"]["handoff"]["states_bootstrap_overlay_file_validation"] is True
    assert report["checks"]["handoff"]["states_promotion_final_report_path_gate"] is True
    assert report["checks"]["handoff"]["states_promotion_control_input_path_gates"] is True
    assert report["checks"]["handoff"]["states_promotion_output_path_gate"] is True
    assert report["checks"]["handoff"]["states_promotion_target_script_path_gate"] is True
    assert report["checks"]["handoff"]["states_target_run_log_reuse_guard"] is True
    assert report["checks"]["handoff"]["states_target_closeout_artifact_reuse_guard"] is True
    assert report["checks"]["local_consistency_boundary"]["consistency_report_not_transfer_artifact"] is True
    assert report["checks"]["local_consistency_boundary"]["bootstrap_report_not_transfer_artifact"] is True
    assert report["checks"]["local_consistency_boundary"]["consistency_checker_not_target_command"] is True
    assert report["checks"]["local_consistency_boundary"]["bootstrap_dry_run_not_target_command"] is True
    assert report["checks"]["archive"]["repo_root_path_portable"] is True
    assert report["checks"]["archive"]["source_paths_portable"] is True
    assert report["checks"]["archive"]["archive_paths_portable"] is True
    assert report["checks"]["archive_verify_report"]["validator_passed"] is True
    assert report["checks"]["archive_verify_report"]["status_passed"] is True
    assert report["checks"]["archive_verify_report"]["archive_sha_matches_manifest"] is True
    assert report["checks"]["archive_verify_report"]["entry_count_matches_manifest"] is True
    assert report["checks"]["transfer"]["readiness_summary_validator_passed"] is True
    assert report["checks"]["transfer"]["transfer_summary_validator_passed"] is True
    assert report["checks"]["transfer"]["readiness_summary_fail_closed"] is True
    assert report["checks"]["overlay_apply"]["dry_run_only"] is True
    assert report["checks"]["overlay_apply"]["would_write_matches_archive"] is True
    assert report["checks"]["overlay_apply"]["would_write_matches_entries"] is True
    assert report["checks"]["overlay_apply"]["written_count_bounded"] is True
    assert report["checks"]["overlay_apply"]["existing_destination_count_matches_entries"] is True
    assert report["checks"]["bootstrap_dry_run"]["repo_head_verified"] is True
    assert report["checks"]["bootstrap_dry_run"]["target_commands_skipped"] is True
    assert report["checks"]["bootstrap_dry_run"]["input_hashes_present"] is True
    assert report["checks"]["bootstrap_dry_run"]["input_hashes_current"] is True
    assert report["checks"]["bootstrap_dry_run"]["overlay_would_write_matches_archive"] is True
    assert report["checks"]["bootstrap_dry_run"]["overlay_summary_matches_overlay_file"] is True
    assert report["checks"]["bootstrap_dry_run"]["overlay_file_validator_passed"] is True
    assert report["checks"]["final_report"]["missing_gates_empty"] is True
    assert report["checks"]["final_report"]["target_artifact_paths_match"] is True
    assert report["checks"]["final_report"]["target_run_ids_match_command_logs"] is True
    assert report["checks"]["final_report"]["single_target_run_id_recorded"] is True
    assert report["checks"]["remediation_summary"]["validator_passed"] is True
    assert report["checks"]["remediation_summary"]["score_eligible_matches_final_report"] is True
    assert report["checks"]["remediation_summary"]["missing_gate_count_matches_final_report"] is True
    assert report["checks"]["remediation_summary"]["remediation_count_matches_final_report"] is True
    assert report["checks"]["remediation_summary"]["action_gate_count_matches_final_report"] is True
    assert report["checks"]["remediation_summary"]["target_hardware_action_absent_after_pass"] is True
    assert report["checks"]["remediation_summary"]["command_log_action_absent_after_pass"] is True
    assert report["checks"]["scorecard"]["m12_final_report_result_records_target_artifact_path_gate"] is True
    assert report["checks"]["transfer"]["promotion_audit_explicit_score_inputs"] is True
    assert report["checks"]["transfer"]["promotion_audit_explicit_target_paths"] is True
    assert report["checks"]["transfer"]["target_run_log_reuse_guard"] is True
    assert report["checks"]["transfer"]["target_closeout_artifact_reuse_guard"] is True
    assert report["checks"]["promotion_audit"]["review_ready_true"] is True
    assert report["checks"]["promotion_audit"]["final_report_path_gate_satisfied"] is True
    assert report["checks"]["promotion_audit"]["command_log_manifest_path_gate_satisfied"] is True
    assert report["checks"]["promotion_audit"]["scorecard_path_gate_satisfied"] is True
    assert report["checks"]["promotion_audit"]["claim_ledger_path_gate_satisfied"] is True
    assert report["checks"]["promotion_audit"]["output_path_gate_satisfied"] is True
    assert report["checks"]["promotion_audit"]["m12_state_pre_review_unawarded"] is True
    assert validate_m12_evidence_consistency_report(report) == []


def test_m12_evidence_consistency_report_rejects_stale_summary_fields() -> None:
    report = build_m12_evidence_consistency_report(phase_dir=PHASE_DIR)
    report["checks"]["scorecard"]["current_points_1000"] = False

    errors = validate_m12_evidence_consistency_report(report)

    assert "errors must include every failed embedded check" in errors
    assert "consistent must match evidence-consistency errors" not in errors

    stale_error_report = build_m12_evidence_consistency_report(phase_dir=PHASE_DIR)
    stale_error_report["consistent"] = False
    stale_error_report["errors"] = ["scorecard.current_points_1000"]
    errors = validate_m12_evidence_consistency_report(stale_error_report)
    assert "errors must not include passed embedded checks" in errors

    unknown_error_report = build_m12_evidence_consistency_report(phase_dir=PHASE_DIR)
    unknown_error_report["consistent"] = False
    unknown_error_report["errors"] = ["synthetic.unknown"]
    errors = validate_m12_evidence_consistency_report(unknown_error_report)
    assert "errors contains unknown entries" in errors


def test_m12_evidence_consistency_report_requires_boolean_checks() -> None:
    report = build_m12_evidence_consistency_report(phase_dir=PHASE_DIR)
    report["checks"]["scorecard"]["current_points_1000"] = "yes"

    errors = validate_m12_evidence_consistency_report(report)

    assert "checks.scorecard.current_points_1000 must be boolean" in errors
    assert "errors must include every failed embedded check" in errors


def test_m12_evidence_consistency_cli_writes_report(tmp_path) -> None:
    output_path = tmp_path / "m12_evidence_consistency.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(PHASE_DIR),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert output_path.exists()
    assert "consistent=True" in result.stdout
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["consistent"] is True
    assert written["scorecard_update_allowed"] is False


def test_m12_evidence_consistency_detects_scorecard_regression_after_promotion(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    scorecard_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
    scorecard = yaml.safe_load(scorecard_path.read_text(encoding="utf-8"))
    scorecard["current_verified_points"] = 920
    scorecard["status"] = "m12_transfer_prepared_target_preflight_blocked"
    for milestone in scorecard["milestones"]:
        if milestone["id"] == "M12":
            milestone["verified_points"] = 0
            milestone["status"] = "transfer_prepared_target_preflight_blocked"
    scorecard_path.write_text(yaml.safe_dump(scorecard, sort_keys=False), encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["consistent"] is False
    assert "scorecard.current_points_1000" in written["errors"]
    assert "scorecard.m12_awarded" in written["errors"]
    assert "scorecard.m12_status_completed" in written["errors"]

def test_m12_evidence_consistency_detects_stale_transfer_manifest_hashes(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    transfer_manifest_path = tmp_path / "runs/m12_transfer_prep/m12_transfer_manifest.json"
    transfer_manifest = json.loads(transfer_manifest_path.read_text(encoding="utf-8"))
    stale_artifact = next(
        artifact
        for artifact in transfer_manifest["artifacts"]
        if artifact["path"] == "requirements.txt"
    )
    stale_artifact["sha256"] = "sha256:" + ("0" * 64)
    transfer_manifest_path.write_text(json.dumps(transfer_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "archive.transfer_manifest_file_artifact_hashes_current" in written["errors"]


def test_m12_evidence_consistency_detects_stale_readiness_summary(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    readiness_path = tmp_path / "runs/m12_transfer_prep/m12_readiness_summary.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["artifact_count"] += 1
    readiness["target_script_fail_closed"]["preflight_requires_pass"] = False
    readiness_path.write_text(json.dumps(readiness, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "transfer.readiness_summary_validator_passed" in written["errors"]
    assert "transfer.readiness_summary_matches_manifest_artifacts" in written["errors"]
    assert "transfer.readiness_summary_fail_closed" in written["errors"]
    assert any(
        error.startswith("readiness_summary_validator.artifact_count must match")
        for error in written["errors"]
    )


def test_m12_evidence_consistency_detects_stale_overlay_report(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    overlay_path = tmp_path / "runs/m12_overlay_apply_probe/m12_overlay_apply_report.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    overlay["existing_destination_count"] += 1
    overlay_path.write_text(json.dumps(overlay, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "overlay_apply.existing_destination_count_matches_entries" in written["errors"]
    assert any(
        error == "overlay_validator.existing_destination_count must equal entries with exists=true"
        for error in written["errors"]
    )


def test_m12_evidence_consistency_detects_missing_overlay_report_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "transfer overlay report status, error-list, write-count, and existing-destination self-consistency",
            "transfer overlay report claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_overlay_report_self_consistency_claim" in written["errors"]


def test_m12_evidence_consistency_detects_missing_bootstrap_overlay_file_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "bootstrap dry-run reports validate the referenced overlay dry-run report",
            "bootstrap dry-run referenced-overlay claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_bootstrap_overlay_file_validation_claim" in written["errors"]


def test_m12_evidence_consistency_detects_missing_promotion_final_report_path_gate_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "promotion-audit canonical final-report input-path gating",
            "promotion-audit final-report path claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_promotion_final_report_path_gate_claim" in written["errors"]


def test_m12_evidence_consistency_detects_missing_promotion_control_input_path_gate_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "promotion-audit canonical control-input path gating",
            "promotion-audit control input path claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_promotion_control_input_path_gates_claim" in written["errors"]


def test_m12_evidence_consistency_detects_missing_promotion_output_path_gate_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "promotion-audit canonical output-path gating",
            "promotion-audit output path claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_promotion_output_path_gate_claim" in written["errors"]


def test_m12_evidence_consistency_detects_missing_promotion_target_script_path_gate_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "target-script promotion-audit explicit target-path gating",
            "target-script promotion audit target path claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_promotion_target_script_path_gate_claim" in written["errors"]


def test_m12_evidence_consistency_detects_missing_target_run_log_reuse_guard_claim(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    claim_ledger_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    claim_ledger_path.write_text(
        claim_ledger.replace(
            "target-run command-log reuse guard",
            "target run command log reuse claim removed",
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "claim_ledger.has_target_run_log_reuse_guard_claim" in written["errors"]


def test_m12_evidence_consistency_detects_nonportable_transfer_repo_root(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    transfer_manifest_path = tmp_path / "runs/m12_transfer_prep/m12_transfer_manifest.json"
    transfer_manifest = json.loads(transfer_manifest_path.read_text(encoding="utf-8"))
    transfer_manifest["repo"]["root"] = str(REPO_ROOT)
    transfer_manifest["repo"]["root_path_portable"] = False
    transfer_manifest_path.write_text(json.dumps(transfer_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "archive.repo_root_path_portable" in written["errors"]


def test_m12_evidence_consistency_detects_stale_archive_verify_report(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    archive_verify_path = tmp_path / "runs/m12_transfer_prep/m12_archive_verify_report.json"
    archive_verify = json.loads(archive_verify_path.read_text(encoding="utf-8"))
    archive_verify["archive_sha256"] = "sha256:" + ("0" * 64)
    archive_verify["included_entry_count"] += 1
    archive_verify_path.write_text(json.dumps(archive_verify, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "archive_verify_report.archive_sha_matches_manifest" in written["errors"]
    assert "archive_verify_report.entry_count_matches_manifest" in written["errors"]
    assert any(
        error.startswith("archive_verify_report_validator.archive_sha256 must match archive manifest")
        for error in written["errors"]
    )


def test_m12_evidence_consistency_detects_ineligible_final_report(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    final_report_path = tmp_path / "runs/m12_final_report/m12_final_report.json"
    final_report = json.loads(final_report_path.read_text(encoding="utf-8"))
    final_report["m12_score_eligible"] = False
    final_report_path.write_text(json.dumps(final_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "final_report.score_eligible_true" in written["errors"]


def test_m12_evidence_consistency_detects_stale_remediation_summary(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    remediation_summary_path = tmp_path / "runs/m12_final_report/m12_remediation_summary.json"
    remediation_summary = json.loads(remediation_summary_path.read_text(encoding="utf-8"))
    remediation_summary["missing_gate_count"] += 1
    remediation_summary_path.write_text(
        json.dumps(remediation_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "remediation_summary.missing_gate_count_matches_final_report" in written["errors"]
    assert any(
        error.startswith("remediation_summary_validator.missing_gate_count must match")
        for error in written["errors"]
    )


def test_m12_evidence_consistency_detects_stale_scorecard_final_report_result(tmp_path) -> None:
    _copy_required_phase_files(tmp_path)
    scorecard_path = tmp_path / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
    scorecard = yaml.safe_load(scorecard_path.read_text(encoding="utf-8"))
    for milestone in scorecard["milestones"]:
        if milestone["id"] != "M12":
            continue
        for evidence in milestone["evidence"]:
            if "build_m12_final_report.py" in str(evidence.get("command") or ""):
                evidence["result"] = (
                    str(evidence["result"])
                    .replace("artifact_paths_match_target_defaults,", "")
                    .replace(",artifact_paths_match_target_defaults", "")
                )
                break
    scorecard_path.write_text(yaml.safe_dump(scorecard, sort_keys=False), encoding="utf-8")

    output_path = tmp_path / "out" / "m12_evidence_consistency.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/check_m12_evidence_consistency.py",
            "--phase-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--require-consistent",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 4
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "scorecard.m12_final_report_result_records_target_artifact_path_gate" in written["errors"]
