from __future__ import annotations

from typing import Any, Dict

from .closure_rule_registry import build_phase17_task_closure_rule_map
from .receipt_scoring import score_row_with_receipts


def score_row_with_branch_receipts(
    row: Dict[str, Any],
    *,
    closure_rule_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    rules = closure_rule_map or build_phase17_task_closure_rule_map()
    scored = score_row_with_receipts(row, closure_rule_map=rules)
    rule = dict(scored.get("closure_rule") or {})
    signals = dict(scored.get("receipt_signals") or {})
    outcome = dict(scored.get("receipt_outcome") or {})
    explicit_branch_receipt_observed = bool(signals.get("explicit_branch_receipt_observed"))
    shell_selected_as_branch_proxy = bool(
        outcome.get("branch_selected")
        and not explicit_branch_receipt_observed
        and bool(signals.get("proof_like_evidence_observed"))
    )
    return {
        **scored,
        "branch_receipt_outcome": {
            "required_branch_mode": str(rule.get("required_branch_mode") or ""),
            "explicit_branch_receipt_observed": explicit_branch_receipt_observed,
            "shell_selected_as_branch_proxy": shell_selected_as_branch_proxy,
        },
    }
