from __future__ import annotations

from typing import Any, Dict


def evaluate_reanchor_policy(
    state: Dict[str, Any],
    *,
    verify_failed: bool,
    drift_detected: bool,
    max_edit_attempts_before_reanchor: int,
) -> Dict[str, Any]:
    edit_attempts = int(state.get("edit_attempts") or 0)
    should_reanchor = bool(verify_failed or drift_detected or edit_attempts >= max_edit_attempts_before_reanchor)
    reason = "no_reanchor"
    if verify_failed:
        reason = "verify_failed"
    elif drift_detected:
        reason = "support_state_drift"
    elif edit_attempts >= max_edit_attempts_before_reanchor:
        reason = "edit_attempt_budget_exhausted"
    return {
        "should_reanchor": should_reanchor,
        "reason": reason,
    }

