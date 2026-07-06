from __future__ import annotations
from collections.abc import Mapping

import json
from pathlib import Path
from typing import Any

from breadboard.rl.phase2.final_report import (
    PHASE2_FINAL_CLAIM_BOUNDARY,
    PHASE2_FINAL_REPORT_ID,
    validate_phase2_final_report,
)


PHASE2_PROMOTION_AUDIT_ID = "bb_zyphra_rl_phase2_promotion_audit_v1"
PHASE2_PROMOTION_CLAIM_BOUNDARY = "phase2_promotion_review_only_not_scorecard_update"


def _dict_value(data: Mapping[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current

def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text_or_mapping_contains(source: Mapping[str, Any] | str, expected: str) -> bool:
    if isinstance(source, str):
        return expected in source
    encoded = json.dumps(source, sort_keys=True)
    return expected in encoded


def _ledger_matches(claim_ledger: Mapping[str, Any] | str, *, target_run_id: str, final_report_id: str) -> dict[str, bool]:
    return {
        "claim_boundary_matches": _text_or_mapping_contains(claim_ledger, PHASE2_FINAL_CLAIM_BOUNDARY),
        "final_report_id_matches": _text_or_mapping_contains(claim_ledger, final_report_id),
        "target_run_id_matches": _text_or_mapping_contains(claim_ledger, target_run_id),
    }


def build_phase2_promotion_audit(
    *,
    target_run_id: str,
    scorecard: Mapping[str, Any],
    claim_ledger: Mapping[str, Any] | str,
    final_report: Mapping[str, Any],
) -> dict[str, Any]:
    final_report_errors = validate_phase2_final_report(final_report)
    final_report_id = str(final_report.get("report_id") or "")
    scorecard_checks = {
        "claim_boundary_matches": _text_or_mapping_contains(scorecard, PHASE2_FINAL_CLAIM_BOUNDARY),
        "final_report_id_matches": str(scorecard.get("final_report_id") or _dict_value(scorecard, "phase2", "final_report_id") or "") == final_report_id,
        "scorecard_update_allowed_false": scorecard.get("scorecard_update_allowed", _dict_value(scorecard, "phase2", "scorecard_update_allowed")) is False,
        "target_run_id_matches": str(scorecard.get("target_run_id") or _dict_value(scorecard, "phase2", "target_run_id") or "") == target_run_id,
        "total_points_complete": _int_or_zero(scorecard.get("total_points") or _dict_value(scorecard, "phase2", "total_points")) == 1000,
    }
    ledger_checks = _ledger_matches(claim_ledger, target_run_id=target_run_id, final_report_id=final_report_id)
    final_checks = {
        "claim_boundary_matches": final_report.get("claim_boundary") == PHASE2_FINAL_CLAIM_BOUNDARY,
        "final_report_id_matches": final_report_id == PHASE2_FINAL_REPORT_ID,
        "final_report_ready": final_report.get("final_report_ready") is True,
        "scorecard_update_allowed_false": final_report.get("scorecard_update_allowed") is False,
        "target_run_id_matches": final_report.get("target_run_id") == target_run_id,
        "validates": not final_report_errors,
    }
    missing_requirements = [f"scorecard.{key}" for key, passed in scorecard_checks.items() if not passed]
    missing_requirements.extend(f"claim_ledger.{key}" for key, passed in ledger_checks.items() if not passed)
    missing_requirements.extend(f"final_report.{key}" for key, passed in final_checks.items() if not passed)
    return {
        "checks": {
            "claim_ledger": ledger_checks,
            "final_report": final_checks,
            "scorecard": scorecard_checks,
        },
        "claim_boundary": PHASE2_PROMOTION_CLAIM_BOUNDARY,
        "final_report_errors": final_report_errors,
        "missing_requirements": missing_requirements,
        "promotion_review_ready": not missing_requirements,
        "report_id": PHASE2_PROMOTION_AUDIT_ID,
        "scorecard_update_allowed": False,
        "target_run_id": target_run_id,
    }


def validate_phase2_promotion_audit(audit: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if audit.get("report_id") != PHASE2_PROMOTION_AUDIT_ID:
        errors.append("report_id must be phase2 promotion audit v1")
    if audit.get("claim_boundary") != PHASE2_PROMOTION_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be promotion review boundary")
    if audit.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if not str(audit.get("target_run_id") or ""):
        errors.append("target_run_id must be non-empty")
    if audit.get("missing_requirements"):
        errors.append("missing_requirements must be empty")
    if audit.get("promotion_review_ready") is not True:
        errors.append("promotion_review_ready must be true")
    return errors


def write_phase2_promotion_audit(
    path: Path,
    *,
    target_run_id: str,
    scorecard: Mapping[str, Any],
    claim_ledger: Mapping[str, Any] | str,
    final_report: Mapping[str, Any],
) -> dict[str, Any]:
    audit = build_phase2_promotion_audit(
        target_run_id=target_run_id,
        scorecard=scorecard,
        claim_ledger=claim_ledger,
        final_report=final_report,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit
