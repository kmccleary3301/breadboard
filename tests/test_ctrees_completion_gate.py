from __future__ import annotations

from agentic_coder_prototype.ctrees.completion_gate import evaluate_completion_gate


def test_completion_gate_marks_ungrounded_stop_as_not_satisfied() -> None:
    payload = evaluate_completion_gate(
        {
            "grounded": {
                "grounded_completion": False,
                "ungrounded_stop": True,
            },
            "controller_metrics": {
                "verified_completion": False,
            },
        }
    )
    assert payload["outcome"] == "ungrounded_stop"
    assert payload["satisfied"] is False


def test_completion_gate_marks_grounded_completion_as_satisfied() -> None:
    payload = evaluate_completion_gate(
        {
            "grounded": {
                "grounded_completion": True,
                "ungrounded_stop": False,
            },
            "controller_metrics": {
                "verified_completion": False,
            },
        }
    )
    assert payload["outcome"] == "grounded_completion"
    assert payload["satisfied"] is True


def test_completion_gate_marks_budget_exhaustion_when_action_budget_flags_it() -> None:
    payload = evaluate_completion_gate(
        {
            "grounded": {
                "grounded_completion": False,
                "ungrounded_stop": False,
            },
            "controller_metrics": {
                "verified_completion": False,
            },
        },
        action_budget={
            "inspection_over_budget": True,
            "mandatory_write_missed": True,
        },
    )
    assert payload["outcome"] == "inspection_budget_exhausted"
    assert payload["mandatory_write_missed"] is True
