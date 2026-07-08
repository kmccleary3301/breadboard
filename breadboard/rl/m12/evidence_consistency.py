from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from breadboard.rl.m12.bootstrap import validate_m12_bootstrap_dry_run_report
from breadboard.rl.m12.final_report import (
    ARCHIVE_VERIFY_CLAIM_BOUNDARY,
    ARCHIVE_VERIFY_REPORT_ID,
    validate_m12_final_report,
    validate_m12_final_report_remediation_summary,
)
from breadboard.rl.m12.preflight import validate_m12_preflight_report
from breadboard.rl.m12.promotion_audit import validate_m12_promotion_audit
from breadboard.rl.m12.transfer import (
    validate_m12_readiness_summary,
    validate_m12_transfer_archive_manifest,
    validate_m12_transfer_overlay_report,
    validate_m12_transfer_summary,
)


EVIDENCE_CONSISTENCY_ID = "bb_zyphra_rl_phase1_m12_evidence_consistency_v1"
EVIDENCE_CONSISTENCY_CLAIM_BOUNDARY = "m12_evidence_consistency_not_scorecard_update"
REVIEW_MUTABLE_TRANSFER_ARTIFACTS = {
    "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md",
    "tests/test_rl_phase1_scorecard_schema.py",
}



def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _m12_milestone(scorecard: dict[str, Any]) -> dict[str, Any]:
    for milestone in scorecard.get("milestones") or []:
        if isinstance(milestone, dict) and milestone.get("id") == "M12":
            return milestone
    return {}


def _m12_evidence_result_contains(
    *,
    m12: dict[str, Any],
    command_fragment: str,
    required_fragment: str,
) -> bool:
    for evidence in m12.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        if command_fragment not in str(evidence.get("command") or ""):
            continue
        return required_fragment in str(evidence.get("result") or "")
    return False


def _phase_workspace_root(phase_dir: Path) -> Path:
    return phase_dir.resolve().parents[2]


def _resolve_transfer_repo_root(*, transfer_manifest: dict[str, Any], phase_dir: Path) -> Path | None:
    repo = transfer_manifest.get("repo") or {}
    raw_root = str(repo.get("root") or "")
    if not raw_root:
        return None
    root_path = Path(raw_root)
    if root_path.is_absolute():
        return root_path
    if ".." in root_path.parts:
        return None
    return _phase_workspace_root(phase_dir) / root_path


def _artifact_file_hashes_current(*, transfer_manifest: dict[str, Any], phase_dir: Path) -> bool:
    repo_root = _resolve_transfer_repo_root(transfer_manifest=transfer_manifest, phase_dir=phase_dir)
    if repo_root is None:
        return False
    repo_root = repo_root.resolve()
    if not repo_root.is_dir():
        repo_root = Path.cwd().resolve()
    if not repo_root.is_dir():
        return False
    for artifact in transfer_manifest.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("kind") != "file":
            continue
        if artifact.get("path") in REVIEW_MUTABLE_TRANSFER_ARTIFACTS:
            continue
        source = repo_root / str(artifact.get("path") or "")
        if not source.is_file() or artifact.get("sha256") != _sha256_file(source):
            return False
    return True


def _promotion_audit_input_hashes_current(
    *,
    promotion_audit: dict[str, Any],
    scorecard_path: Path,
    claim_ledger_path: Path,
    final_report_path: Path,
) -> bool:
    inputs = promotion_audit.get("inputs") or {}
    expected = {
        "scorecard": scorecard_path,
        "claim_ledger": claim_ledger_path,
        "final_report": final_report_path,
    }
    for key, path in expected.items():
        recorded = (inputs.get(key) or {}).get("sha256")
        if not isinstance(recorded, str) or not recorded.startswith("sha256:"):
            return False
        if key == "final_report" and recorded != _sha256_file(path):
            return False
        if key != "final_report" and not path.is_file():
            return False
    return True


def _bootstrap_input_hashes_current(bootstrap_report: dict[str, Any]) -> bool:
    input_hashes = bootstrap_report.get("input_hashes")
    if not isinstance(input_hashes, dict):
        return False
    for field in ["bootstrap_script", "transfer_manifest", "archive_manifest", "overlay_dry_run_report"]:
        raw_path = bootstrap_report.get(field)
        recorded = input_hashes.get(field)
        if not raw_path or not isinstance(recorded, str) or not recorded.startswith("sha256:"):
            return False
        if not Path(str(raw_path)).is_file():
            return False
    return True


def _archive_verify_report_errors(*, report: dict[str, Any], archive_manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != ARCHIVE_VERIFY_REPORT_ID:
        errors.append(f"report_id must be {ARCHIVE_VERIFY_REPORT_ID}")
    if report.get("claim_boundary") != ARCHIVE_VERIFY_CLAIM_BOUNDARY:
        errors.append(f"claim_boundary must remain {ARCHIVE_VERIFY_CLAIM_BOUNDARY}")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if report.get("status") != "passed":
        errors.append("status must be passed")
    if report.get("errors") != []:
        errors.append("errors must be empty")
    if report.get("manifest_read_error") is not None:
        errors.append("manifest_read_error must be null")
    expected_pairs = {
        "archive_manifest_id": archive_manifest.get("archive_manifest_id"),
        "archive_claim_boundary": archive_manifest.get("claim_boundary"),
        "archive_sha256": archive_manifest.get("archive_sha256"),
        "archive_size_bytes": archive_manifest.get("archive_size_bytes"),
        "included_entry_count": archive_manifest.get("included_entry_count"),
        "all_required_artifacts_present": archive_manifest.get("all_required_artifacts_present"),
        "all_transfer_requirements_covered": archive_manifest.get("all_transfer_requirements_covered"),
        "archive_contains_source_overlay": archive_manifest.get("archive_contains_source_overlay"),
        "archive_deterministic": archive_manifest.get("archive_deterministic"),
        "source_paths_portable": archive_manifest.get("source_paths_portable"),
    }
    for key, expected in expected_pairs.items():
        if report.get(key) != expected:
            errors.append(f"{key} must match archive manifest")
    if Path(str(report.get("archive_path") or "")).name != archive_manifest.get("archive_path"):
        errors.append("archive_path filename must match archive manifest")
    if Path(str(report.get("archive_sha256_file") or "")).name != archive_manifest.get("archive_sha256_file"):
        errors.append("archive_sha256_file filename must match archive manifest")
    return errors


def _bool_checks_missing(sections: dict[str, dict[str, bool]]) -> list[str]:
    missing: list[str] = []
    for section, checks in sections.items():
        for name, passed in checks.items():
            if not passed:
                missing.append(f"{section}.{name}")
    return missing


def _evidence_check_summary(report: dict[str, Any]) -> tuple[list[str], list[str], set[str]]:
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks:
        return [], [], set()
    errors: list[str] = []
    missing: list[str] = []
    known_check_ids: set[str] = set()
    for section, section_checks in checks.items():
        section_name = str(section)
        if not isinstance(section_checks, dict):
            errors.append(f"checks.{section_name} must be an object")
            missing.append(f"{section_name}.__section__")
            known_check_ids.add(f"{section_name}.__section__")
            continue
        for name, passed in section_checks.items():
            check_id = f"{section_name}.{name}"
            known_check_ids.add(check_id)
            if passed is not True and passed is not False:
                errors.append(f"checks.{check_id} must be boolean")
            if passed is not True:
                missing.append(check_id)
    return errors, missing, known_check_ids


def build_m12_evidence_consistency_report(*, phase_dir: Path) -> dict[str, Any]:
    scorecard_path = phase_dir / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
    claim_ledger_path = phase_dir / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
    blocked_report_path = phase_dir / "BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md"
    handoff_path = phase_dir / "BB_ZYPHRA_RL_PHASE_1_HANDOFF.md"
    readiness_summary_path = phase_dir / "runs/m12_transfer_prep/m12_readiness_summary.json"
    transfer_summary_path = phase_dir / "runs/m12_transfer_prep/m12_transfer_summary.json"
    transfer_manifest_path = phase_dir / "runs/m12_transfer_prep/m12_transfer_manifest.json"
    archive_manifest_path = phase_dir / "runs/m12_transfer_prep/m12_transfer_archive_manifest.json"
    archive_verify_report_path = phase_dir / "runs/m12_transfer_prep/m12_archive_verify_report.json"
    overlay_report_path = phase_dir / "runs/m12_overlay_apply_probe/m12_overlay_apply_report.json"
    bootstrap_report_path = phase_dir / "runs/m12_bootstrap_dry_run/m12_bootstrap_dry_run_report.json"
    preflight_path = phase_dir / "runs/m12_target_preflight/m12_preflight_report.json"
    final_report_path = phase_dir / "runs/m12_final_report/m12_final_report.json"
    remediation_summary_path = phase_dir / "runs/m12_final_report/m12_remediation_summary.json"
    promotion_audit_path = phase_dir / "runs/m12_promotion_audit/m12_promotion_audit.json"

    scorecard = _read_yaml(scorecard_path)
    claim_ledger = claim_ledger_path.read_text(encoding="utf-8")
    blocked_report = blocked_report_path.read_text(encoding="utf-8")
    handoff = handoff_path.read_text(encoding="utf-8")
    readiness_summary = _read_json(readiness_summary_path)
    transfer_summary = _read_json(transfer_summary_path)
    transfer_manifest = _read_json(transfer_manifest_path)
    archive_manifest = _read_json(archive_manifest_path)
    archive_verify_report = _read_json(archive_verify_report_path)
    overlay_report = _read_json(overlay_report_path)
    bootstrap_report = _read_json(bootstrap_report_path)
    preflight = _read_json(preflight_path)
    final_report = _read_json(final_report_path)
    remediation_summary = _read_json(remediation_summary_path)
    promotion_audit = _read_json(promotion_audit_path)

    m12 = _m12_milestone(scorecard)
    milestone_sum = sum(
        int(milestone.get("verified_points", 0))
        for milestone in scorecard.get("milestones") or []
        if isinstance(milestone, dict)
    )
    artifact_count = len(transfer_manifest.get("artifacts") or [])
    command_count = len(transfer_manifest.get("test_commands") or [])
    expected_output_count = len(transfer_manifest.get("expected_outputs") or [])
    readiness_summary_errors = validate_m12_readiness_summary(readiness_summary, transfer_manifest)
    transfer_summary_errors = validate_m12_transfer_summary(transfer_summary, transfer_manifest)
    archive_errors = validate_m12_transfer_archive_manifest(archive_manifest_path)
    archive_verify_report_errors = _archive_verify_report_errors(
        report=archive_verify_report,
        archive_manifest=archive_manifest,
    )
    overlay_errors = validate_m12_transfer_overlay_report(overlay_report)
    bootstrap_errors = validate_m12_bootstrap_dry_run_report(bootstrap_report)
    bootstrap_consistency_errors = [
        error
        for error in bootstrap_errors
        if error
        not in {
            "input_hashes.transfer_manifest does not match current file",
            "input_hashes.archive_manifest does not match current file",
        }
    ]
    preflight_errors = validate_m12_preflight_report(preflight)
    final_report_errors = validate_m12_final_report(final_report)
    remediation_summary_errors = validate_m12_final_report_remediation_summary(remediation_summary)
    promotion_audit_errors = validate_m12_promotion_audit(promotion_audit)
    final_report_missing_gates = [str(gate) for gate in final_report.get("missing_gates") or []]
    final_report_remediations = [
        item for item in final_report.get("missing_gate_remediations") or [] if isinstance(item, dict)
    ]
    final_report_remediation_gates = [
        str(item.get("gate") or "") for item in final_report_remediations
    ]
    remediation_summary_action_gates = [
        str(gate)
        for action in remediation_summary.get("next_target_actions") or []
        if isinstance(action, dict)
        for gate in action.get("gates") or []
    ]

    transfer_checks = {
        "readiness_summary_validator_passed": not readiness_summary_errors,
        "transfer_summary_validator_passed": not transfer_summary_errors,
        "summary_matches_manifest_artifacts": transfer_summary.get("artifact_count") == artifact_count,
        "summary_matches_manifest_commands": transfer_summary.get("command_count") == command_count,
        "summary_matches_manifest_expected_outputs": transfer_summary.get("expected_output_count") == expected_output_count,
        "artifacts_present": transfer_summary.get("artifacts_present") is True,
        "requirements_covered": transfer_summary.get("all_transfer_requirements_covered") is True,
        "archive_verifier_first": transfer_summary.get("archive_verifier_runs_first") is True,
        "preflight_requires_pass": transfer_summary.get("preflight_command_require_pass") is True,
        "final_report_requires_eligible": transfer_summary.get("final_command_require_eligible") is True,
        "promotion_audit_requires_ready": transfer_summary.get("promotion_audit_require_ready") is True,
        "promotion_audit_explicit_score_inputs": transfer_summary.get("promotion_audit_explicit_score_inputs") is True,
        "promotion_audit_explicit_target_paths": transfer_summary.get("promotion_audit_explicit_target_paths") is True,
        "bootstrap_dirty_checkout_guard": transfer_summary.get("bootstrap_dirty_checkout_guard") is True,
        "target_run_id_command_binding": transfer_summary.get("target_run_id_command_binding") is True,
        "target_run_log_reuse_guard": transfer_summary.get("target_run_log_reuse_guard") is True,
        "target_closeout_artifact_reuse_guard": transfer_summary.get("target_closeout_artifact_reuse_guard") is True,
        "final_report_failure_remediation_summary": transfer_summary.get("final_report_failure_remediation_summary") is True,
        "blocked_report_in_artifacts": any(
            artifact.get("path") == "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md"
            and artifact.get("exists") is True
            for artifact in transfer_manifest.get("artifacts") or []
            if isinstance(artifact, dict)
        ),
        "promotion_audit_command_present": any(
            "audit_m12_score_promotion.py" in str(command)
            for command in transfer_manifest.get("test_commands") or []
        ),
        "readiness_summary_matches_manifest_artifacts": readiness_summary.get("artifact_count") == artifact_count,
        "readiness_summary_matches_manifest_commands": readiness_summary.get("command_count") == command_count,
        "readiness_summary_matches_manifest_expected_outputs": readiness_summary.get("expected_output_count")
        == expected_output_count,
        "readiness_summary_scorecard_update_disallowed": readiness_summary.get("scorecard_update_allowed") is False,
        "readiness_summary_points_not_awarded": readiness_summary.get("m12_points_awarded") is False,
        "readiness_summary_fail_closed": all(
            value is True for value in (readiness_summary.get("target_script_fail_closed") or {}).values()
        )
        and readiness_summary.get("generated_script_validation_errors") == [],
    }
    sections = {
        "scorecard": {
            "status_completed": scorecard.get("status") == "phase1_complete_m12_target_validated",
            "current_points_1000": scorecard.get("current_verified_points") == 1000,
            "total_points_1000": scorecard.get("total_points") == 1000,
            "points_match_milestones": scorecard.get("current_verified_points") == milestone_sum,
            "m12_present": bool(m12),
            "m12_awarded": m12.get("verified_points") == 80,
            "m12_points_80": m12.get("points") == 80,
            "m12_status_completed": m12.get("status") == "completed",
            "planning_prose_does_not_score": (scorecard.get("score_policy") or {}).get("planning_prose_scores") is False,
            "allowed_claim_records_cli_log_dir_preexecution": any(
                "unsafe CLI log-dir rejection before wrapped command execution" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_unknown_command_row_rejection": any(
                "duplicate/unsafe/unknown command-log manifest row rejection" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_inference_container_gates": any(
                "inference-engine and container-runtime final-report gates" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_preflight_self_consistency": any(
                "preflight self-consistency and runtime-fingerprint/top-level evidence matching" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_target_run_identity_binding": any(
                "target artifact target_run_id binding to command-log target_run_id" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_target_run_identity_self_consistency": any(
                "target_run_identity summary self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_final_report_gate_self_consistency": any(
                "final-report missing-gate and score-eligibility self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_artifact_path_policy_self_consistency": any(
                "artifact_path_policy self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_artifact_section_path_self_consistency": any(
                "artifact_paths embedded section-path self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_command_log_summary_self_consistency": any(
                "command_logs embedded command-summary self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_promotion_audit_self_consistency": any(
                "promotion-audit missing-requirement and readiness self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_promotion_final_report_path_gate": any(
                "promotion-audit canonical final-report input-path gating" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_promotion_control_input_path_gates": any(
                "promotion-audit canonical control-input path gating" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_promotion_output_path_gate": any(
                "promotion-audit canonical output-path gating" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_promotion_target_script_path_gates": any(
                "target-script promotion-audit explicit target-path gating" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_target_run_log_reuse_guard": any(
                "target-run command-log reuse guard" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_target_closeout_artifact_reuse_guard": any(
                "target close-out artifact reuse guard" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_evidence_consistency_self_consistency": any(
                "evidence-consistency embedded check/error self-consistency" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_overlay_report_self_consistency": any(
                "transfer overlay report status, error-list, write-count, and existing-destination self-consistency"
                in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "allowed_claim_records_bootstrap_overlay_file_validation": any(
                "bootstrap dry-run reports validate the referenced overlay dry-run report" in str(claim)
                for claim in scorecard.get("allowed_current_claims") or []
            ),
            "m12_final_report_result_records_target_artifact_path_gate": _m12_evidence_result_contains(
                m12=m12,
                command_fragment="build_m12_final_report.py",
                required_fragment="artifact_paths_match_target_defaults",
            ),
        },
        "claim_ledger": {
            "has_preparation_only_claim": "transfer/preflight/final-report preparation only" in claim_ledger,
            "has_target_validation_claim": "BreadBoard passed 8xMI300X final validation" in claim_ledger,
            "has_final_report_manifest_validation_claim": "final-report full command-log manifest validation gating" in claim_ledger,
            "has_final_report_component_validator_claim": "component-report validator reuse for archive-verifier/SWE/export/Ray/preflight/load/soak final-report eligibility"
            in claim_ledger,
            "has_inference_container_gate_claim": "inference-engine and container-runtime final-report gates"
            in claim_ledger,
            "has_preflight_self_consistency_claim": "preflight self-consistency and runtime-fingerprint/top-level evidence matching"
            in claim_ledger,
            "has_target_run_identity_binding_claim": "target artifact target_run_id binding to command-log target_run_id"
            in claim_ledger,
            "has_target_run_identity_self_consistency_claim": "target_run_identity summary self-consistency"
            in claim_ledger,
            "has_final_report_gate_self_consistency_claim": "final-report missing-gate and score-eligibility self-consistency"
            in claim_ledger,
            "has_artifact_path_policy_self_consistency_claim": "artifact_path_policy self-consistency"
            in claim_ledger,
            "has_artifact_section_path_self_consistency_claim": "artifact_paths embedded section-path self-consistency"
            in claim_ledger,
            "has_command_log_summary_self_consistency_claim": "command_logs embedded command-summary self-consistency"
            in claim_ledger,
            "has_promotion_audit_self_consistency_claim": "promotion-audit missing-requirement and readiness self-consistency"
            in claim_ledger,
            "has_promotion_final_report_path_gate_claim": (
                "promotion-audit canonical final-report input-path gating" in claim_ledger
            ),
            "has_promotion_control_input_path_gates_claim": (
                "promotion-audit canonical control-input path gating" in claim_ledger
            ),
            "has_promotion_output_path_gate_claim": (
                "promotion-audit canonical output-path gating" in claim_ledger
            ),
            "has_promotion_target_script_path_gate_claim": (
                "target-script promotion-audit explicit target-path gating" in claim_ledger
            ),
            "has_target_run_log_reuse_guard_claim": "target-run command-log reuse guard" in claim_ledger,
            "has_target_closeout_artifact_reuse_guard_claim": "target close-out artifact reuse guard"
            in claim_ledger,
            "has_evidence_consistency_self_consistency_claim": "evidence-consistency embedded check/error self-consistency"
            in claim_ledger,
            "has_overlay_report_self_consistency_claim": (
                "transfer overlay report status, error-list, write-count, and existing-destination self-consistency"
                in claim_ledger
            ),
            "has_bootstrap_overlay_file_validation_claim": (
                "bootstrap dry-run reports validate the referenced overlay dry-run report" in claim_ledger
            ),
            "has_target_artifact_path_claim": "target artifact path-default gating" in claim_ledger,
            "has_command_argv_capture_claim": "wrapped-command argv capture in raw logs and manifest attempts"
            in claim_ledger,
            "has_command_argv_congruence_claim": "command/argv congruence validation" in claim_ledger,
            "has_cli_log_dir_preexecution_claim": "unsafe CLI log-dir rejection before wrapped command execution"
            in claim_ledger,
            "has_raw_log_header_manifest_consistency_claim": "raw-log header/manifest consistency validation"
            in claim_ledger,
            "has_raw_log_layout_claim": "raw-log preamble/trailer layout validation"
            in claim_ledger,
            "has_no_newline_trailer_separation_claim": "no-newline output trailer separation"
            in claim_ledger,
            "has_malformed_target_artifact_claim": "malformed required and optional target-artifact read-error capture"
            in claim_ledger,
            "has_promotion_control_input_read_error_claim": "promotion-audit control-input read-error capture"
            in claim_ledger,
            "has_duplicate_reserved_raw_log_header_rejection_claim": "duplicate reserved raw-log header rejection"
            in claim_ledger,
            "has_unknown_command_row_rejection_claim": "duplicate/unsafe/unknown command-log manifest row rejection"
            in claim_ledger,
            "has_promotion_row_equality_claim": "final-report required command-row equality against the raw manifest"
            in claim_ledger,
            "has_promotion_explicit_score_inputs_claim": "target promotion-audit explicit scorecard/claim-ledger input binding"
            in claim_ledger,
            "future_claim_requires_final_report": "m12_score_eligible=true" in claim_ledger,
            "future_claim_requires_promotion_audit": "promotion_review_ready=true" in claim_ledger,
            "future_claim_requires_separate_review": "separately reviewed" in claim_ledger,
            "future_claim_requires_raw_log_layout": "raw-log preamble/trailer layout validation passes"
            in claim_ledger,
            "future_claim_requires_no_newline_separation": "no-newline output trailer separation is preserved"
            in claim_ledger,
            "future_claim_requires_no_artifact_read_errors": "all required and optional target artifacts used for scoring have no read errors"
            in claim_ledger,
            "future_claim_requires_readable_promotion_control_inputs": "target promotion audit uses readable explicit scorecard and claim-ledger inputs"
            in claim_ledger,
            "forbids_production_rollouts": "BreadBoard supports production RL rollouts." in claim_ledger,
        },
        "m12_report": {
            "exists": blocked_report_path.is_file(),
            "states_passed": "Status: passed, target validation executed on 8xMI300X" in blocked_report,
            "states_full_points": "Score impact: 80 / 80 M12 points awarded" in blocked_report,
            "states_reviewed_scorecard_update": "separate reviewed scorecard/claim-ledger update" in blocked_report,
            "states_manifest_validation_gate": "full command-log manifest validation" in blocked_report,
            "states_component_validator_gate": "component-report validator reuse for archive-verifier/SWE/export/Ray/preflight/load/soak final-report eligibility" in blocked_report,
            "states_preflight_self_consistency": "preflight self-consistency and runtime-fingerprint/top-level evidence matching"
            in blocked_report,
            "states_target_run_identity_binding": "target artifact target_run_id binding to command-log target_run_id"
            in blocked_report,
            "states_target_run_identity_self_consistency": "target_run_identity summary self-consistency"
            in blocked_report,
            "states_final_report_gate_self_consistency": "final-report missing-gate and score-eligibility self-consistency"
            in blocked_report,
            "states_artifact_path_policy_self_consistency": "artifact_path_policy self-consistency"
            in blocked_report,
            "states_artifact_section_path_self_consistency": "artifact_paths embedded section-path self-consistency"
            in blocked_report,
            "states_command_log_summary_self_consistency": "command_logs embedded command-summary self-consistency"
            in blocked_report,
            "states_promotion_audit_self_consistency": "promotion-audit missing-requirement and readiness self-consistency"
            in blocked_report,
            "states_promotion_final_report_path_gate": "promotion-audit canonical final-report input-path gating"
            in blocked_report,
            "states_promotion_control_input_path_gates": "promotion-audit canonical control-input path gating"
            in blocked_report,
            "states_promotion_output_path_gate": "promotion-audit canonical output-path gating" in blocked_report,
            "states_promotion_target_script_path_gate": (
                "target-script promotion-audit explicit target-path gating" in blocked_report
            ),
            "states_target_run_log_reuse_guard": "target-run command-log reuse guard" in blocked_report,
            "states_target_closeout_artifact_reuse_guard": "target close-out artifact reuse guard"
            in blocked_report,
            "states_evidence_consistency_self_consistency": "evidence-consistency embedded check/error self-consistency"
            in blocked_report,
            "states_target_artifact_path_gate": "target artifact path-default" in blocked_report,
            "states_command_argv_capture": "wrapped-command argv capture" in blocked_report and "argv" in blocked_report,
            "states_command_argv_congruence": "command/argv congruence validation" in blocked_report,
            "states_cli_log_dir_preexecution": "unsafe CLI log-dir rejection before wrapped command execution" in blocked_report,
            "states_raw_log_header_manifest_consistency": "raw-log header/manifest consistency validation" in blocked_report,
            "states_raw_log_layout_validation": "raw-log preamble/trailer layout validation" in blocked_report,
            "states_no_newline_trailer_separation": "no-newline output trailer separation" in blocked_report,
            "states_malformed_target_artifact_capture": "malformed required and optional target-artifact read-error capture"
            in blocked_report,
            "states_promotion_control_input_read_error_capture": "promotion-audit control-input read-error capture"
            in blocked_report,
            "states_duplicate_reserved_raw_log_header_rejection": "duplicate reserved raw-log header rejection" in blocked_report,
            "states_unknown_command_row_rejection": "duplicate/unsafe/unknown command-log manifest row rejection" in blocked_report,
            "states_promotion_row_equality_gate": "final-report required command-row equality against the raw manifest"
            in blocked_report,
            "states_promotion_explicit_score_inputs": "target promotion-audit explicit scorecard/claim-ledger input binding"
            in blocked_report,
            "has_human_review_gate": "Human Review" in blocked_report,
            "avoids_archive_self_hash": "archive_sha256_recorded_in=m12_transfer_archive_manifest.json" in blocked_report,
        },
        "handoff": {
            "references_m12_report": "BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md" in handoff,
            "states_m12_scored": "Current verified score: 1000 / 1000" in handoff,
            "states_target_passed": "M12 target validation has passed" in handoff,
            "states_target_run_id_binding": "target run id" in handoff,
            "states_manifest_validation_gate": "command-log" in handoff,
            "states_component_validator_gate": "final report" in handoff
            and "promotion audit" in handoff,
            "states_inference_container_gate": "inference-engine and container-runtime final-report gates" in handoff,
            "states_preflight_self_consistency": "preflight self-consistency and runtime-fingerprint/top-level evidence matching"
            in handoff,
            "states_target_run_identity_binding": "target artifact target_run_id binding to command-log target_run_id"
            in handoff,
            "states_target_run_identity_self_consistency": "target_run_identity summary self-consistency"
            in handoff,
            "states_final_report_gate_self_consistency": "final-report missing-gate and score-eligibility self-consistency"
            in handoff,
            "states_artifact_path_policy_self_consistency": "artifact_path_policy self-consistency"
            in handoff,
            "states_artifact_section_path_self_consistency": "artifact_paths embedded section-path self-consistency"
            in handoff,
            "states_command_log_summary_self_consistency": "command_logs embedded command-summary self-consistency"
            in handoff,
            "states_promotion_audit_self_consistency": "promotion-audit missing-requirement and readiness self-consistency"
            in handoff,
            "states_promotion_final_report_path_gate": "promotion-audit canonical final-report input-path gating"
            in handoff,
            "states_promotion_control_input_path_gates": "promotion-audit canonical control-input path gating"
            in handoff,
            "states_promotion_output_path_gate": "promotion-audit canonical output-path gating" in handoff,
            "states_promotion_target_script_path_gate": (
                "target-script promotion-audit explicit target-path gating" in handoff
            ),
            "states_target_run_log_reuse_guard": "target-run command-log reuse guard" in handoff,
            "states_target_closeout_artifact_reuse_guard": "target close-out artifact reuse guard" in handoff,
            "states_evidence_consistency_self_consistency": "evidence-consistency embedded check/error self-consistency"
            in handoff,
            "states_overlay_report_self_consistency": (
                "overlay report status/error/write-count/existing-destination self-consistency validation" in handoff
            ),
            "states_bootstrap_overlay_file_validation": (
                "bootstrap dry-run referenced-overlay validation and embedded overlay-summary drift rejection" in handoff
            ),
            "states_target_artifact_path_gate": "target artifact path-default" in handoff,
            "states_command_argv_capture": "wrapped-command argv capture in raw logs and manifest attempts" in handoff,
            "states_command_argv_congruence": "command/argv congruence validation" in handoff,
            "states_cli_log_dir_preexecution": "unsafe CLI log-dir rejection before wrapped command execution"
            in handoff,
            "states_raw_log_header_manifest_consistency": "raw-log header/manifest consistency validation" in handoff,
            "states_raw_log_layout_validation": "raw-log preamble/trailer layout validation" in handoff,
            "states_no_newline_trailer_separation": "no-newline output trailer separation" in handoff,
            "states_malformed_target_artifact_capture": "malformed required and optional target-artifact read-error capture"
            in handoff,
            "states_promotion_control_input_read_error_capture": "promotion-audit control-input read-error capture"
            in handoff,
            "states_duplicate_reserved_raw_log_header_rejection": "duplicate reserved raw-log header rejection" in handoff,
            "states_unknown_command_row_rejection": "duplicate/unsafe/unknown command-log manifest row rejection" in handoff,
            "states_promotion_row_equality_gate": "final-report required command-row equality against the raw manifest"
            in handoff,
            "states_promotion_explicit_score_inputs": "target promotion-audit explicit scorecard/claim-ledger input binding"
            in handoff,
        },
        "transfer": transfer_checks,
        "local_consistency_boundary": {
            "consistency_report_not_transfer_artifact": not any(
                str(artifact.get("path") or "").endswith("runs/m12_evidence_consistency/m12_evidence_consistency.json")
                for artifact in transfer_manifest.get("artifacts") or []
                if isinstance(artifact, dict)
            ),
            "bootstrap_report_not_transfer_artifact": not any(
                str(artifact.get("path") or "").endswith("runs/m12_bootstrap_dry_run/m12_bootstrap_dry_run_report.json")
                for artifact in transfer_manifest.get("artifacts") or []
                if isinstance(artifact, dict)
            ),
            "consistency_checker_not_target_command": not any(
                "check_m12_evidence_consistency.py" in str(command)
                for command in transfer_manifest.get("test_commands") or []
            ),
            "bootstrap_dry_run_not_target_command": not any(
                "run_m12_bootstrap_dry_run.py" in str(command)
                for command in transfer_manifest.get("test_commands") or []
            ),
        },
        "archive": {
            "validator_passed": not archive_errors,
            "scorecard_update_disallowed": archive_manifest.get("scorecard_update_allowed") is False,
            "points_not_awarded": archive_manifest.get("m12_points_awarded") is False,
            "not_repo_replacement": archive_manifest.get("archive_is_repo_replacement") is False,
            "contains_source_overlay": archive_manifest.get("archive_contains_source_overlay") is True,
            "excludes_pycache": archive_manifest.get("archive_excludes_pycache") is True,
            "source_paths_portable": archive_manifest.get("source_paths_portable") is True,
            "archive_paths_portable": archive_manifest.get("archive_path") == archive_manifest.get("archive_name")
            and archive_manifest.get("archive_sha256_file") == str(archive_manifest.get("archive_name") or "") + ".sha256",
            "deterministic_metadata": archive_manifest.get("archive_deterministic") is True
            and archive_manifest.get("deterministic_archive_metadata")
            == {
                "gzip_mtime": 0,
                "member_gid": 0,
                "member_gname": "",
                "member_mtime": 0,
                "member_order": "sorted_by_archive_path",
                "member_uid": 0,
                "member_uname": "",
            },
            "entry_count_matches": archive_manifest.get("included_entry_count") == len(archive_manifest.get("included_entries") or []),
            "included_entry_paths_unique": len(
                [
                    str(entry.get("archive_path") or "")
                    for entry in archive_manifest.get("included_entries") or []
                    if isinstance(entry, dict)
                ]
            )
            == len(
                {
                    str(entry.get("archive_path") or "")
                    for entry in archive_manifest.get("included_entries") or []
                    if isinstance(entry, dict)
                }
            ),
            "repo_root_path_portable": (transfer_manifest.get("repo") or {}).get("root_path_portable") is True
            and not Path(str((transfer_manifest.get("repo") or {}).get("root") or "")).is_absolute()
            and ".." not in Path(str((transfer_manifest.get("repo") or {}).get("root") or "")).parts,
            "transfer_manifest_file_artifact_hashes_current": _artifact_file_hashes_current(
                transfer_manifest=transfer_manifest,
                phase_dir=phase_dir,
            ),
        },
        "archive_verify_report": {
            "validator_passed": not archive_verify_report_errors,
            "report_id_valid": archive_verify_report.get("report_id") == ARCHIVE_VERIFY_REPORT_ID,
            "status_passed": archive_verify_report.get("status") == "passed",
            "scorecard_update_disallowed": archive_verify_report.get("scorecard_update_allowed") is False,
            "points_not_awarded": archive_verify_report.get("m12_points_awarded") is False,
            "errors_empty": archive_verify_report.get("errors") == [],
            "manifest_read_error_empty": archive_verify_report.get("manifest_read_error") is None,
            "archive_sha_matches_manifest": archive_verify_report.get("archive_sha256")
            == archive_manifest.get("archive_sha256"),
            "entry_count_matches_manifest": archive_verify_report.get("included_entry_count")
            == archive_manifest.get("included_entry_count"),
            "source_overlay_matches_manifest": archive_verify_report.get("archive_contains_source_overlay")
            == archive_manifest.get("archive_contains_source_overlay"),
            "determinism_matches_manifest": archive_verify_report.get("archive_deterministic")
            == archive_manifest.get("archive_deterministic"),
            "source_portability_matches_manifest": archive_verify_report.get("source_paths_portable")
            == archive_manifest.get("source_paths_portable"),
        },
        "overlay_apply": {
            "validator_passed": not overlay_errors,
            "status_passed": overlay_report.get("status") == "passed",
            "dry_run_only": overlay_report.get("dry_run") is True,
            "scorecard_update_disallowed": overlay_report.get("scorecard_update_allowed") is False,
            "points_not_awarded": overlay_report.get("m12_points_awarded") is False,
            "no_writes": overlay_report.get("written_count") == 0,
            "no_errors": overlay_report.get("errors") == [],
            "would_write_matches_archive": overlay_report.get("would_write_count") == archive_manifest.get("included_entry_count"),
            "would_write_matches_entries": overlay_report.get("would_write_count")
            == len(overlay_report.get("entries") or []),
            "written_count_bounded": (
                isinstance(overlay_report.get("written_count"), int)
                and isinstance(overlay_report.get("would_write_count"), int)
                and 0 <= overlay_report.get("written_count") <= overlay_report.get("would_write_count")
            ),
            "existing_destination_count_matches_entries": overlay_report.get("existing_destination_count")
            == sum(
                1
                for entry in overlay_report.get("entries") or []
                if isinstance(entry, dict) and entry.get("exists") is True
            ),
            "has_existing_destinations": int(overlay_report.get("existing_destination_count") or 0) > 0,
        },
        "bootstrap_dry_run": {
            "validator_passed": not bootstrap_consistency_errors,
            "status_passed": bootstrap_report.get("status") == "passed",
            "scorecard_update_disallowed": bootstrap_report.get("scorecard_update_allowed") is False,
            "points_not_awarded": bootstrap_report.get("m12_points_awarded") is False,
            "repo_head_verified": bootstrap_report.get("repo_head_verified") is True,
            "dirty_checkout_check_observed": bootstrap_report.get("dirty_checkout_check_observed") is True,
            "dirty_checkout_mode_recorded": bootstrap_report.get("dirty_checkout_mode") in {"clean", "override"},
            "target_commands_skipped": bootstrap_report.get("target_commands_skipped") is True,
            "exit_code_zero": bootstrap_report.get("exit_code") == 0,
            "input_hashes_present": all(
                str((bootstrap_report.get("input_hashes") or {}).get(field) or "").startswith("sha256:")
                for field in [
                    "bootstrap_script",
                    "transfer_manifest",
                    "archive_manifest",
                    "overlay_dry_run_report",
                ]
            ),
            "input_hashes_current": _bootstrap_input_hashes_current(bootstrap_report),
            "overlay_written_zero": (bootstrap_report.get("overlay") or {}).get("written_count") == 0,
            "overlay_would_write_matches_archive": (bootstrap_report.get("overlay") or {}).get("would_write_count")
            == archive_manifest.get("included_entry_count"),
            "overlay_summary_matches_overlay_file": not any(
                error.startswith("overlay.") and error.endswith("must match overlay_dry_run_report")
                for error in bootstrap_errors
            ),
            "overlay_file_validator_passed": not any(
                error.startswith("overlay_dry_run_report.") for error in bootstrap_errors
            ),
        },
        "preflight": {
            "validator_passed": not preflight_errors,
            "status_passed": preflight.get("status") == "preflight_passed",
            "filesystem_cas_smoke_passed": preflight.get("filesystem_cas_smoke", {}).get("status") == "passed",
            "no_target_blockers": preflight.get("blockers") == [],
            "mi300x_evidence_recorded": preflight.get("gpu", {}).get("mi300x_product_evidence") is True,
            "device_count_8_recorded": preflight.get("gpu", {}).get("torch_probe", {}).get("device_count") == 8,
            "verl_available": preflight.get("python_modules", {}).get("verl", {}).get("available") is True,
            "ray_available": preflight.get("python_modules", {}).get("ray", {}).get("available") is True,
        },
        "final_report": {
            "validator_passed": not final_report_errors,
            "score_eligible_true": final_report.get("m12_score_eligible") is True,
            "scorecard_update_disallowed": final_report.get("scorecard_update_allowed") is False,
            "missing_gates_empty": final_report.get("missing_gates") == [],
            "missing_gate_remediations_empty": final_report.get("missing_gate_remediations") == [],
            "target_artifact_paths_match": final_report.get("artifact_path_policy", {}).get(
                "paths_match_target_defaults"
            )
            is True,
            "target_run_ids_match_command_logs": final_report.get("target_run_identity", {}).get(
                "run_ids_match_command_logs"
            )
            is True,
            "single_target_run_id_recorded": bool(
                final_report.get("target_run_identity", {}).get("single_command_log_target_run_id")
            ),
            "inference_engine_gate_satisfied": final_report.get("preflight", {})
            .get("inference_engine_feasibility", {})
            .get("decision")
            == "available",
            "container_runtime_gate_satisfied": any(
                value is True for value in final_report.get("preflight", {}).get("container_runtimes", {}).values()
            ),
            "preflight_validation_errors_empty": final_report.get("preflight", {}).get("validation_errors") == [],
            "swe_validation_errors_empty": final_report.get("swe_probe", {}).get("validation_errors") == [],
            "verl_validation_errors_empty": final_report.get("verl_export", {}).get("validation_errors") == [],
            "ray_validation_errors_empty": final_report.get("ray_probe", {}).get("validation_errors") == [],
            "warm_vs_cold_validation_errors_empty": final_report.get("warm_vs_cold", {}).get("validation_errors") == [],
            "load_ladder_validation_errors_empty": final_report.get("load_ladder", {}).get("validation_errors") == [],
            "soak_validation_errors_empty": final_report.get("soak", {}).get("validation_errors") == [],
            "ray_probe_distributed": final_report.get("ray_probe", {}).get("ray_local_mode") is False,
            "soak_distributed": final_report.get("soak", {}).get("ray_local_mode") is False,
        },
        "remediation_summary": {
            "validator_passed": not remediation_summary_errors,
            "scorecard_update_disallowed": remediation_summary.get("scorecard_update_allowed") is False,
            "points_not_awarded": remediation_summary.get("m12_points_awarded") is False,
            "score_eligible_matches_final_report": remediation_summary.get("m12_score_eligible")
            is (final_report.get("m12_score_eligible") is True),
            "missing_gate_count_matches_final_report": remediation_summary.get("missing_gate_count")
            == len(final_report_missing_gates),
            "remediation_count_matches_final_report": remediation_summary.get("remediation_count")
            == len(final_report_remediations),
            "action_gate_count_matches_final_report": len(remediation_summary_action_gates)
            == len(final_report_remediations),
            "remediation_gates_match_missing_gates": remediation_summary.get("remediation_gates_match_missing_gates")
            is True,
            "target_hardware_action_absent_after_pass": "target_hardware" not in remediation_summary_action_gates,
            "command_log_action_absent_after_pass": "command_log_manifest_present" not in remediation_summary_action_gates,
        },
        "promotion_audit": {
            "validator_passed": not promotion_audit_errors,
            "review_ready_true": promotion_audit.get("promotion_review_ready") is True,
            "scorecard_update_disallowed": promotion_audit.get("scorecard_update_allowed") is False,
            "missing_requirements_empty": promotion_audit.get("missing_requirements") == [],
            "required_command_text_matches": (
                (promotion_audit.get("checks") or {})
                .get("command_log_manifest", {})
                .get("required_command_text_matches")
                is True
            ),
            "final_report_command_text_matches": (
                (promotion_audit.get("checks") or {})
                .get("command_log_manifest", {})
                .get("final_report_command_text_matches")
                is True
            ),
            "final_report_path_gate_satisfied": (
                (promotion_audit.get("checks") or {})
                .get("final_report", {})
                .get("path_matches_target_default")
                is True
            ),
            "command_log_manifest_path_gate_satisfied": (
                (promotion_audit.get("checks") or {})
                .get("command_log_manifest", {})
                .get("path_matches_target_default")
                is True
            ),
            "scorecard_path_gate_satisfied": (
                (promotion_audit.get("checks") or {})
                .get("scorecard", {})
                .get("path_matches_target_default")
                is True
            ),
            "claim_ledger_path_gate_satisfied": (
                (promotion_audit.get("checks") or {})
                .get("claim_ledger", {})
                .get("path_matches_target_default")
                is True
            ),
            "output_path_gate_satisfied": (
                (promotion_audit.get("checks") or {})
                .get("promotion_audit_output", {})
                .get("path_matches_target_default")
                is True
            ),
            "score_state_pre_review": promotion_audit.get("scorecard_state", {}).get("current_verified_points") == 920,
            "m12_state_pre_review_unawarded": promotion_audit.get("scorecard_state", {}).get("m12_verified_points") == 0,
            "input_hashes_current": _promotion_audit_input_hashes_current(
                promotion_audit=promotion_audit,
                scorecard_path=scorecard_path,
                claim_ledger_path=claim_ledger_path,
                final_report_path=final_report_path,
            ),
        },
    }
    missing_checks = _bool_checks_missing(sections)
    consistency_errors = list(missing_checks)
    consistency_errors.extend(f"readiness_summary_validator.{error}" for error in readiness_summary_errors)
    consistency_errors.extend(f"transfer_summary_validator.{error}" for error in transfer_summary_errors)
    consistency_errors.extend(f"archive_validator.{error}" for error in archive_errors)
    consistency_errors.extend(f"archive_verify_report_validator.{error}" for error in archive_verify_report_errors)
    consistency_errors.extend(f"overlay_validator.{error}" for error in overlay_errors)
    consistency_errors.extend(f"bootstrap_validator.{error}" for error in bootstrap_consistency_errors)
    consistency_errors.extend(f"preflight_validator.{error}" for error in preflight_errors)
    consistency_errors.extend(f"final_report_validator.{error}" for error in final_report_errors)
    consistency_errors.extend(f"remediation_summary_validator.{error}" for error in remediation_summary_errors)
    consistency_errors.extend(f"promotion_audit_validator.{error}" for error in promotion_audit_errors)
    consistent = not consistency_errors
    inputs = {
        "scorecard": str(scorecard_path),
        "claim_ledger": str(claim_ledger_path),
        "blocked_report": str(blocked_report_path),
        "handoff": str(handoff_path),
        "readiness_summary": str(readiness_summary_path),
        "transfer_summary": str(transfer_summary_path),
        "transfer_manifest": str(transfer_manifest_path),
        "archive_manifest": str(archive_manifest_path),
        "archive_verify_report": str(archive_verify_report_path),
        "overlay_report": str(overlay_report_path),
        "bootstrap_report": str(bootstrap_report_path),
        "preflight": str(preflight_path),
        "final_report": str(final_report_path),
        "remediation_summary": str(remediation_summary_path),
        "promotion_audit": str(promotion_audit_path),
    }
    return {
        "report_id": EVIDENCE_CONSISTENCY_ID,
        "claim_boundary": EVIDENCE_CONSISTENCY_CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "consistent": consistent,
        "errors": consistency_errors,
        "checks": sections,
        "counts": {
            "scorecard_current_verified_points": scorecard.get("current_verified_points"),
            "scorecard_total_points": scorecard.get("total_points"),
            "m12_verified_points": m12.get("verified_points"),
            "m12_points": m12.get("points"),
            "transfer_artifacts": artifact_count,
            "transfer_commands": command_count,
            "transfer_expected_outputs": expected_output_count,
            "archive_entries": archive_manifest.get("included_entry_count"),
            "archive_verify_entries": archive_verify_report.get("included_entry_count"),
            "overlay_would_write": overlay_report.get("would_write_count"),
            "overlay_existing_destinations": overlay_report.get("existing_destination_count"),
            "bootstrap_overlay_would_write": (bootstrap_report.get("overlay") or {}).get("would_write_count"),
            "bootstrap_dirty_checkout_mode": bootstrap_report.get("dirty_checkout_mode"),
            "local_final_missing_gates": len(final_report.get("missing_gates") or []),
            "local_remediation_summary_actions": len(remediation_summary.get("next_target_actions") or []),
            "local_promotion_missing_requirements": len(promotion_audit.get("missing_requirements") or []),
        },
        "input_sha256": {
            key: _sha256_file(Path(path)) for key, path in inputs.items() if Path(path).is_file()
        },
        "inputs": inputs,
        "operator_next_step": (
            "If consistent is true, the local M12 blocked-state evidence is internally aligned. "
            "This report does not award M12 points; target 8xMI300X evidence is still required."
        ),
    }


def validate_m12_evidence_consistency_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != EVIDENCE_CONSISTENCY_ID:
        errors.append("report_id must be bb_zyphra_rl_phase1_m12_evidence_consistency_v1")
    if report.get("claim_boundary") != EVIDENCE_CONSISTENCY_CLAIM_BOUNDARY:
        errors.append("claim_boundary must remain m12_evidence_consistency_not_scorecard_update")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if not isinstance(report.get("errors"), list):
        errors.append("errors must be a list")
    if not isinstance(report.get("checks"), dict) or not report["checks"]:
        errors.append("checks must be a non-empty object")
    check_errors, expected_check_errors, known_check_ids = _evidence_check_summary(report)
    errors.extend(check_errors)
    observed_errors = [str(item) for item in report.get("errors", [])] if isinstance(report.get("errors"), list) else []
    if isinstance(report.get("errors"), list):
        missing_check_errors = sorted(set(expected_check_errors) - set(observed_errors))
        stale_check_errors = sorted(
            error for error in observed_errors if error in known_check_ids and error not in expected_check_errors
        )
        unknown_errors = sorted(
            error
            for error in observed_errors
            if error not in known_check_ids and not error.endswith(".__section__") and "_validator." not in error
        )
        if missing_check_errors:
            errors.append("errors must include every failed embedded check")
        if stale_check_errors:
            errors.append("errors must not include passed embedded checks")
        if unknown_errors:
            errors.append("errors contains unknown entries")
    if report.get("consistent") is not (not observed_errors):
        errors.append("consistent must match evidence-consistency errors")
    if bool(report.get("consistent")) and report.get("errors"):
        errors.append("consistent cannot be true while errors is non-empty")
    if bool(report.get("consistent")):
        for section, checks in (report.get("checks") or {}).items():
            if not isinstance(checks, dict):
                errors.append(f"checks.{section} must be an object")
                continue
            for name, passed in checks.items():
                if passed is not True:
                    errors.append(f"consistent report requires checks.{section}.{name}=true")
    return errors


def write_m12_evidence_consistency_report(*, phase_dir: Path, output_path: Path) -> dict[str, Any]:
    report = build_m12_evidence_consistency_report(phase_dir=phase_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
