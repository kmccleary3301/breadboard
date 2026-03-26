from __future__ import annotations

from typing import Any, Dict


def evaluate_completion_gate(live_summary: Dict[str, Any], *, action_budget: Dict[str, Any] | None = None) -> Dict[str, Any]:
    grounded = dict(live_summary.get("grounded") or {})
    controller_metrics = dict(live_summary.get("controller_metrics") or {})
    budget = dict(action_budget or {})

    grounded_completion = bool(grounded.get("grounded_completion"))
    verified_completion = bool(controller_metrics.get("verified_completion"))
    ungrounded_stop = bool(grounded.get("ungrounded_stop"))

    if verified_completion:
        outcome = "verified_completion"
    elif grounded_completion:
        outcome = "grounded_completion"
    elif ungrounded_stop:
        outcome = "ungrounded_stop"
    elif bool(budget.get("inspection_over_budget")):
        outcome = "inspection_budget_exhausted"
    elif bool(budget.get("mandatory_write_missed")):
        outcome = "mandatory_write_missed"
    else:
        outcome = "incomplete"

    return {
        "schema_version": "phase12_completion_gate_v1",
        "outcome": outcome,
        "satisfied": grounded_completion,
        "verified_completion": verified_completion,
        "grounded_completion": grounded_completion,
        "ungrounded_stop": ungrounded_stop,
        "inspection_over_budget": bool(budget.get("inspection_over_budget")),
        "mandatory_write_missed": bool(budget.get("mandatory_write_missed")),
    }
