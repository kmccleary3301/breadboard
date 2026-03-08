from __future__ import annotations

from agentic_coder_prototype.conductor_execution import (
    build_tool_execution_outcome_record,
    build_tool_model_render_record,
)


def test_tool_execution_outcome_and_model_render_are_distinct() -> None:
    outcome = build_tool_execution_outcome_record("run_shell", {"stdout": "hi", "exit": 0})
    render = build_tool_model_render_record("run_shell", {"stdout": "hi", "exit": 0})

    assert outcome["tool"] == "run_shell"
    assert outcome["ok"] is True
    assert outcome["raw"]["stdout"] == "hi"

    assert render == {
        "tool": "run_shell",
        "status": "ok",
        "error": None,
    }
