from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.core.core import ToolCallParsed
from agentic_coder_prototype.execution.composite import CompositeToolCaller
from agentic_coder_prototype.state.session_state import SessionState


def test_composite_tool_caller_tracks_text_tool_calls_and_todo_seed() -> None:
    state = SessionState("ws", "image", {})
    state.begin_turn(1)

    caller = CompositeToolCaller([])
    parsed_calls = [
        ToolCallParsed(function="todo.create", arguments={"title": "seed"}),
        SimpleNamespace(function="read_file", arguments={"path": "README.md"}),
    ]

    caller.track_tool_usage(parsed_calls, session_state=state)

    assert state.get_provider_metadata("todo_seed_completed") is True
    stored = state.get_provider_metadata("text_tool_calls")
    assert isinstance(stored, list)
    assert any(entry.get("name") == "todo.create" for entry in stored)

