from __future__ import annotations
import re

from collections.abc import Mapping
from typing import Any

from breadboard.rl.phase3.evidence import PHASE3_TARGET_RUN_ID_PATTERN
from breadboard.rl.phase3.final_report import PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY, PHASE3_ACTIVE_SCOPE_SCHEMA, PHASE3_ACTIVE_MILESTONES, PHASE3_FINAL_CLAIM_BOUNDARY, PHASE3_FINAL_REPORT_ID, PHASE3_MILESTONES

PHASE3_PROMOTION_AUDIT_ID = "bb_zyphra_rl_phase3_promotion_audit_v1"
PHASE3_PROMOTION_CLAIM_BOUNDARY = "phase3_promotion_review_only_not_scorecard_update"
PHASE3_EXACT_SCOPE_REVIEW_READY_MEANING = (
    "Artifact-audit boundary is clean for the promoted exact-scope Phase 3 claim; broader successor claims remain separately gated."
)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any) -> list[str]:
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def build_phase3_promotion_audit(*, target_run_id: str, final_report: Mapping[str, Any], scorecard: Mapping[str, Any], claim_ledger_text: str, bd_epic_closed: bool) -> dict[str, Any]:
    if not isinstance(final_report, Mapping):
        final_report = {"validation_errors": ["final report must be a JSON object"]}
    target_run_id = str(target_run_id or "")
    claim_ledger_text = claim_ledger_text if isinstance(claim_ledger_text, str) else ""
    summaries_raw = final_report.get("milestone_summaries", [])
    summaries = [item for item in summaries_raw if isinstance(item, Mapping)] if isinstance(summaries_raw, list) else []
    completed = [item["milestone_id"] for item in summaries if isinstance(item.get("milestone_id"), str) and item.get("passed") is True]
    blocked = [item["milestone_id"] for item in summaries if isinstance(item.get("milestone_id"), str) and item.get("passed") is not True]
    validation_errors_raw = final_report.get("validation_errors", [])
    if isinstance(validation_errors_raw, list):
        final_report_validation_errors = [str(error) for error in validation_errors_raw]
    elif validation_errors_raw:
        final_report_validation_errors = [str(validation_errors_raw)]
    else:
        final_report_validation_errors = []
    target_run_id_valid = re.match(PHASE3_TARGET_RUN_ID_PATTERN, target_run_id) is not None
    active_scope = final_report.get("active_scope", final_report.get("core_readiness", {}))
    active_scope = dict(active_scope) if isinstance(active_scope, Mapping) else {}
    active_completed = [milestone for milestone in completed if milestone in PHASE3_ACTIVE_MILESTONES]
    active_blocked = _string_list(active_scope.get("blocked_active_milestones", []))
    active_claim_ledger_anchored = PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY in claim_ledger_text
    active_milestones = active_scope.get("active_milestones", active_scope.get("core_milestones"))
    active_scope_current = active_milestones == list(PHASE3_ACTIVE_MILESTONES)
    active_review_ready = (
        final_report_validation_errors == []
        and target_run_id_valid
        and active_scope.get("ready") is True
        and active_scope_current
        and set(active_completed) == set(PHASE3_ACTIVE_MILESTONES)
        and len(active_completed) == len(PHASE3_ACTIVE_MILESTONES)
        and not active_blocked
        and active_claim_ledger_anchored
    )
    promotion_ready = (
        final_report_validation_errors == []
        and target_run_id_valid
        and set(completed) == set(PHASE3_MILESTONES)
        and len(completed) == len(PHASE3_MILESTONES)
        and not blocked
        and target_run_id in claim_ledger_text
        and PHASE3_FINAL_REPORT_ID in claim_ledger_text
        and PHASE3_FINAL_CLAIM_BOUNDARY in claim_ledger_text
        and bd_epic_closed
    )
    audit = {
        "schema_version": "bb.rl.phase3.promotion_audit.v1",
        "report_id": PHASE3_PROMOTION_AUDIT_ID,
        "claim_boundary": PHASE3_PROMOTION_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "completed_milestones": completed,
        "blocked_milestones": blocked,
        "final_report_validation_errors": final_report_validation_errors,
        "bd_epic_closed": bd_epic_closed,
        "promotion_review_ready": promotion_ready,
        "active_scope": active_scope,
        "active_completed_milestones": active_completed,
        "active_blocked_milestones": active_blocked,
        "active_claim_ledger_anchored": active_claim_ledger_anchored,
        "active_review_ready": active_review_ready,
        "active_artifact_audit_clean": active_review_ready,
        "active_review_ready_meaning": PHASE3_EXACT_SCOPE_REVIEW_READY_MEANING,
        "core_readiness": active_scope,
        "core_completed_milestones": active_completed,
        "core_blocked_milestones": active_blocked,
        "core_claim_ledger_anchored": active_claim_ledger_anchored,
        "core_review_ready": active_review_ready,
        "core_artifact_audit_clean": active_review_ready,
        "core_review_ready_meaning": PHASE3_EXACT_SCOPE_REVIEW_READY_MEANING,
        "core_scorecard_update_allowed": False,
        "scorecard_update_allowed": False,
    }
    audit["core_validation_errors"] = validate_phase3_core_promotion_audit(audit)
    return audit


def validate_phase3_core_promotion_audit(audit: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if audit.get("core_scorecard_update_allowed") is not False:
        errors.append("core_scorecard_update_allowed must be false")
    if audit.get("active_claim_ledger_anchored") is not True:
        errors.append("active claim boundary must be anchored in claim ledger")
    if not re.match(PHASE3_TARGET_RUN_ID_PATTERN, str(audit.get("target_run_id") or "")):
        errors.append("target_run_id must match Phase 3 Slurm target run id pattern")
    final_report_errors = audit.get("final_report_validation_errors", [])
    if final_report_errors != _string_list(final_report_errors):
        errors.append("final_report_validation_errors must be a list of strings")
    elif final_report_errors:
        errors.append("active audit requires clean final_report_validation_errors")
    active_scope = audit.get("active_scope", {})
    if not isinstance(active_scope, Mapping):
        errors.append("active_scope must be an object")
    else:
        if active_scope.get("schema_version") != PHASE3_ACTIVE_SCOPE_SCHEMA:
            errors.append("active_scope.schema_version must be Phase 3 active scope schema")
        if active_scope.get("claim_boundary") != PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY:
            errors.append("active_scope.claim_boundary must be Phase 3 active scope boundary")
        if active_scope.get("scorecard_update_allowed") is not False:
            errors.append("active_scope.scorecard_update_allowed must be false")
        active_milestones = active_scope.get("active_milestones", active_scope.get("core_milestones"))
        if active_milestones != list(PHASE3_ACTIVE_MILESTONES):
            errors.append("active_scope.active_milestones must match current Phase 3 active milestones")
        if active_scope.get("ready") is not True:
            errors.append("active_scope.ready must be true")
    active_completed = audit.get("active_completed_milestones", [])
    if active_completed != _string_list(active_completed):
        errors.append("active_completed_milestones must be a list of strings")
    elif set(active_completed) != set(PHASE3_ACTIVE_MILESTONES) or len(active_completed) != len(PHASE3_ACTIVE_MILESTONES):
        errors.append("every active P3 milestone must be completed")
    active_blocked = audit.get("active_blocked_milestones", [])
    if active_blocked != _string_list(active_blocked):
        errors.append("active_blocked_milestones must be a list of strings")
    elif active_blocked:
        errors.append("active audit must not list active_blocked_milestones")
    if audit.get("active_review_ready") is not True:
        errors.append("active_review_ready must be true")
    return errors

def validate_phase3_promotion_audit(audit: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if audit.get("schema_version") != "bb.rl.phase3.promotion_audit.v1":
        errors.append("schema_version must be Phase 3 promotion audit schema")
    if audit.get("report_id") != PHASE3_PROMOTION_AUDIT_ID:
        errors.append("report_id must be Phase 3 promotion audit id")
    if audit.get("core_scorecard_update_allowed") is not False:
        errors.append("core_scorecard_update_allowed must be false")
    active_scope = audit.get("active_scope", {})
    if active_scope:
        if not isinstance(active_scope, Mapping):
            errors.append("active_scope must be an object when present")
        else:
            if active_scope.get("schema_version") != PHASE3_ACTIVE_SCOPE_SCHEMA:
                errors.append("active_scope.schema_version must be Phase 3 active scope schema")
            if active_scope.get("claim_boundary") != PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY:
                errors.append("active_scope.claim_boundary must be Phase 3 active scope boundary")
            if active_scope.get("scorecard_update_allowed") is not False:
                errors.append("active_scope.scorecard_update_allowed must be false")
            active_milestones = active_scope.get("active_milestones", active_scope.get("core_milestones"))
            if active_milestones != list(PHASE3_ACTIVE_MILESTONES):
                errors.append("active_scope.active_milestones must match current Phase 3 active milestones")
    if audit.get("active_review_ready") is True:
        if not isinstance(active_scope, Mapping) or active_scope.get("ready") is not True:
            errors.append("ready active audit must contain ready active_scope")
    if audit.get("claim_boundary") != PHASE3_PROMOTION_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be promotion review only")
    if not re.match(PHASE3_TARGET_RUN_ID_PATTERN, str(audit.get("target_run_id") or "")):
        errors.append("target_run_id must match Phase 3 Slurm target run id pattern")
    if audit.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    blocked_milestones = audit.get("blocked_milestones", [])
    final_report_errors = audit.get("final_report_validation_errors", [])
    if blocked_milestones != _string_list(blocked_milestones):
        errors.append("blocked_milestones must be a list of strings")
    if final_report_errors != _string_list(final_report_errors):
        errors.append("final_report_validation_errors must be a list of strings")
    if audit.get("promotion_review_ready") is not True:
        errors.append("promotion_review_ready must be true")
    if audit.get("promotion_review_ready") is True:
        if blocked_milestones:
            errors.append("ready promotion audit must not list blocked_milestones")
        if final_report_errors:
            errors.append("ready promotion audit must not list final_report_validation_errors")
    completed = audit.get("completed_milestones", [])
    completed_ids = [item for item in completed if isinstance(item, str)] if isinstance(completed, list) else []
    if not isinstance(completed, list) or len(completed) != len(completed_ids) or set(completed_ids) != set(PHASE3_MILESTONES) or len(completed_ids) != len(PHASE3_MILESTONES):
        errors.append("every P3 milestone must be completed")
    if audit.get("bd_epic_closed") is not True:
        errors.append("bd epic must be closed")
    return errors
