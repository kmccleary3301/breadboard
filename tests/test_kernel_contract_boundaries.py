from __future__ import annotations

from agentic_coder_prototype.conductor_execution import (
    classify_tool_terminal_state,
    build_tool_execution_outcome_record,
    build_tool_model_render_record,
)


def test_tool_execution_outcome_and_model_render_are_distinct() -> None:
    outcome = build_tool_execution_outcome_record("run_shell", {"stdout": "hi", "exit": 0})
    render = build_tool_model_render_record("run_shell", {"stdout": "hi", "exit": 0})

    assert outcome["tool"] == "run_shell"
    assert outcome["terminal_state"] == "completed"
    assert outcome["ok"] is True
    assert outcome["raw"]["stdout"] == "hi"

    assert render["tool"] == "run_shell"
    assert render["terminal_state"] == "completed"
    assert render["status"] == "ok"
    assert render["error"] is None
    assert render["preview"] == "hi"
    assert render["truncated"] is False


def test_tool_terminal_state_classification_covers_denied_and_cancelled() -> None:
    assert classify_tool_terminal_state({"guardrail": "workspace_guard_violation"}) == "denied"
    assert classify_tool_terminal_state({"cancelled": True}) == "cancelled"
    assert classify_tool_terminal_state({"error": "boom"}) == "failed"
