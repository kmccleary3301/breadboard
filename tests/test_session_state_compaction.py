from __future__ import annotations

import os
from typing import Any, Dict, List

from agentic_coder_prototype.state.session_state import SessionState


def _capture_events() -> tuple[list[dict], callable]:
    events: List[Dict[str, Any]] = []

    def _emit(event_type: str, payload: Dict[str, Any], *, turn: int | None = None) -> None:
        events.append({"type": event_type, "payload": dict(payload), "turn": turn})

    return events, _emit


def test_session_state_emits_compaction_summary(monkeypatch) -> None:
    events, emitter = _capture_events()
    monkeypatch.setenv("BREADBOARD_SESSION_COMPACTION", "1")
    monkeypatch.setenv("BREADBOARD_SESSION_COMPACTION_MESSAGE_THRESHOLD", "2")
    monkeypatch.setenv("BREADBOARD_SESSION_COMPACTION_MAX_CHARS", "200")

    state = SessionState(workspace="/tmp", image="test", config={}, event_emitter=emitter)
    state._active_turn_index = 1
    state.messages.extend(
        [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there."},
        ]
    )
    state.transcript.append({"turn": 1})

    summary = state.maybe_emit_compaction_summary(reason="test")
    assert summary
    assert any(evt["type"] == "session.compaction" for evt in events)
