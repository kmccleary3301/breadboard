from __future__ import annotations

from agentic_coder_prototype.ctrees.action_budget import evaluate_action_budget


def test_action_budget_marks_inspection_over_budget_without_write() -> None:
    payload = evaluate_action_budget(
        {
            "controller_metrics": {
                "first_write_step": None,
                "first_verify_step": 1,
                "no_progress_streak_max": 5,
            },
            "tool_counts": {
                "write_calls": 0,
                "test_commands": 0,
                "successful_tests": 0,
            },
        },
        max_inspection_turns=2,
        mandatory_write_by_turn=4,
        escalation_after_no_progress=2,
    )
    assert payload["inspection_over_budget"] is True
    assert payload["mandatory_write_missed"] is True
    assert payload["escalation_stage"] == "forced_closure_attempt"
