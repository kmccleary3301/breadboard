from __future__ import annotations

from typing import Any, Dict

from .branch_receipt_scoring import score_row_with_branch_receipts
from .closure_rule_registry import build_phase18_task_closure_rule_map


def score_row_with_finish_closure(
    row: Dict[str, Any],
    *,
    closure_rule_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    rules = closure_rule_map or build_phase18_task_closure_rule_map()
    scored = score_row_with_branch_receipts(row, closure_rule_map=rules)
    signals = dict(scored.get("receipt_signals") or {})
    outcome = dict(scored.get("receipt_outcome") or {})
    completion_summary = dict(scored.get("completion_summary") or {})
    explicit_finish_request_observed = bool(signals.get("explicit_finish_request_observed"))
    accepted_finish = bool(completion_summary.get("completed")) and explicit_finish_request_observed
    return {
        **scored,
        "finish_closure_outcome": {
            "explicit_finish_request_observed": explicit_finish_request_observed,
            "closure_ready_state_observed": bool(
                outcome.get("branch_selected")
                and (
                    signals.get("edit_receipt_observed")
                    or signals.get("explicit_proof_receipt_observed")
                )
            ),
            "accepted_finish": accepted_finish,
        },
    }
