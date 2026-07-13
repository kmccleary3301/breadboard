from __future__ import annotations
from collections.abc import Mapping

import json
from pathlib import Path
from typing import Any

from breadboard.rl.phase2.hardening import HARDENING_CLAIM_BOUNDARY
from breadboard.rl.phase2.observability import OBSERVABILITY_CLAIM_BOUNDARY
from breadboard.rl.phase2.service import SERVICE_CLAIM_BOUNDARY


PHASE2_FINAL_REPORT_ID = "bb_zyphra_rl_phase2_final_report_v1"
PHASE2_FINAL_CLAIM_BOUNDARY = "phase2_target_validation_candidate_not_scorecard_update"
PHASE2_COMPONENT_MILESTONES = tuple(f"P2-M{index}" for index in range(12))
EXPECTED_MILESTONE_CLAIM_BOUNDARIES: dict[str, str] = {
    "P2-M0": "p2_m0_baseline_freeze_not_scorecard_update",
    "P2-M1": "phase2_verl_batch_dry_run_only_not_training_evidence",
    "P2-M2": "phase2_trainer_dry_run_no_slurm_no_weight_update",
    "P2-M3": "p2_m3_closed_loop_prototype_not_production_rl_claim",
    "P2-M4": "p2_m4_scale_ladder_not_arbitrary_production_scale",
    "P2-M5": "p2_m5_named_benchmark_slice_not_general_benchmark_claim",
    "P2-M6": "p2_m6_live_verifier_probe_not_general_verifier_claim",
    "P2-M7": "p2_m7_benchflow_probe_not_full_security_coverage_claim",
    "P2-M8": "p2_m8_second_environment_probe_not_general_env_support_claim",
    "P2-M9": SERVICE_CLAIM_BOUNDARY,
    "P2-M10": OBSERVABILITY_CLAIM_BOUNDARY,
    "P2-M11": HARDENING_CLAIM_BOUNDARY,
}


def build_phase2_component_report(
    *,
    milestone_id: str,
    report_id: str,
    claim_boundary: str,
    target_run_id: str,
    passed: bool,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "claim_boundary": claim_boundary,
        "milestone_id": milestone_id,
        "passed": passed,
        "report_id": report_id,
        "scorecard_update_allowed": False,
        "summary": dict(summary or {}),
        "target_run_id": target_run_id,
    }

def _component_passed(report: Mapping[str, Any]) -> bool:
    if "passed" in report:
        return report.get("passed") is True
    if "ready" in report:
        return report.get("ready") is True
    if "hardening_passed" in report:
        return report.get("hardening_passed") is True
    dry_run_result = report.get("dry_run_result")
    if isinstance(dry_run_result, Mapping):
        return dry_run_result.get("accepted") is True
    errors = report.get("errors")
    if isinstance(errors, list):
        return not errors
    return True


def _component_summary(milestone_id: str, report: Mapping[str, Any] | None, target_run_id: str) -> dict[str, Any]:
    expected_boundary = EXPECTED_MILESTONE_CLAIM_BOUNDARIES[milestone_id]
    if report is None:
        return {
            "claim_boundary_match": False,
            "expected_claim_boundary": expected_boundary,
            "milestone_id": milestone_id,
            "passed": False,
            "present": False,
            "report_id": None,
            "scorecard_update_allowed": None,
            "target_run_id": None,
            "target_run_id_match": False,
        }
    observed_target_run_id = str(report.get("target_run_id") or "")
    return {
        "claim_boundary_match": report.get("claim_boundary") == expected_boundary,
        "expected_claim_boundary": expected_boundary,
        "milestone_id": milestone_id,
        "observed_claim_boundary": report.get("claim_boundary"),
        "passed": _component_passed(report),
        "present": True,
        "report_id": report.get("report_id"),
        "scorecard_update_allowed": report.get("scorecard_update_allowed"),
        "target_run_id": observed_target_run_id,
        "target_run_id_match": observed_target_run_id == target_run_id,
    }


def build_phase2_final_report(
    *,
    target_run_id: str,
    milestone_reports: Mapping[str, Mapping[str, Any]],
    command_log_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summaries = [_component_summary(milestone_id, milestone_reports.get(milestone_id), target_run_id) for milestone_id in PHASE2_COMPONENT_MILESTONES]
    missing = [summary["milestone_id"] for summary in summaries if not summary["present"]]
    failed = [summary["milestone_id"] for summary in summaries if summary["present"] and not summary["passed"]]
    boundary_mismatches = [summary["milestone_id"] for summary in summaries if not summary["claim_boundary_match"]]
    scorecard_updates = [summary["milestone_id"] for summary in summaries if summary["scorecard_update_allowed"] is not False]
    target_mismatches = [summary["milestone_id"] for summary in summaries if not summary["target_run_id_match"]]
    command_manifest = dict(command_log_manifest or {})
    command_manifest_present = bool(command_manifest)
    command_manifest_target_run_id = str(command_manifest.get("target_run_id") or "")
    command_manifest_target_run_id_match = command_manifest_present and command_manifest_target_run_id == target_run_id
    final_ready = not (missing or failed or boundary_mismatches or scorecard_updates or target_mismatches) and command_manifest_target_run_id_match
    return {
        "claim_boundary": PHASE2_FINAL_CLAIM_BOUNDARY,
        "command_log_manifest_present": command_manifest_present,
        "command_log_manifest_target_run_id": command_manifest_target_run_id,
        "command_log_manifest_target_run_id_match": command_manifest_target_run_id_match,
        "component_reports": summaries,
        "failed_milestones": failed,
        "final_report_ready": final_ready,
        "missing_milestones": missing,
        "phase": "RL_PHASE_2",
        "report_id": PHASE2_FINAL_REPORT_ID,
        "scorecard_update_allowed": False,
        "target_run_id": target_run_id,
        "validation": {
            "boundary_mismatches": boundary_mismatches,
            "scorecard_update_attempts": scorecard_updates,
            "target_run_id_mismatches": target_mismatches,
        },
    }


def validate_phase2_final_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != PHASE2_FINAL_REPORT_ID:
        errors.append("report_id must be phase2 final report v1")
    if report.get("claim_boundary") != PHASE2_FINAL_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be phase2 target-validation boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if not str(report.get("target_run_id") or ""):
        errors.append("target_run_id must be non-empty")
    if report.get("command_log_manifest_present") is not True:
        errors.append("command_log_manifest_present must be true")
    if report.get("command_log_manifest_target_run_id_match") is not True:
        errors.append("command_log_manifest_target_run_id_match must be true")
    if report.get("missing_milestones"):
        errors.append("missing_milestones must be empty")
    if report.get("failed_milestones"):
        errors.append("failed_milestones must be empty")
    validation = report.get("validation") if isinstance(report.get("validation"), Mapping) else {}
    for key in ["boundary_mismatches", "scorecard_update_attempts", "target_run_id_mismatches"]:
        if validation.get(key):
            errors.append(f"validation.{key} must be empty")
    summaries = report.get("component_reports") if isinstance(report.get("component_reports"), list) else []
    if [summary.get("milestone_id") for summary in summaries if isinstance(summary, Mapping)] != list(PHASE2_COMPONENT_MILESTONES):
        errors.append("component_reports must include every P2-M0 through P2-M11 row in order")
    if report.get("final_report_ready") is not True:
        errors.append("final_report_ready must be true")
    return errors


def write_phase2_final_report(
    path: Path,
    *,
    target_run_id: str,
    milestone_reports: Mapping[str, Mapping[str, Any]],
    command_log_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_phase2_final_report(
        target_run_id=target_run_id,
        milestone_reports=milestone_reports,
        command_log_manifest=command_log_manifest,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
