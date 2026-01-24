from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agentic_coder_prototype.agent_llm_openai import _wrap_event_emitter_with_before_emit
from agentic_coder_prototype.hooks.example_hooks import ExampleBeforeEmitTaggerHook
from agentic_coder_prototype.hooks.manager import HookManager
from agentic_coder_prototype.state.session_state import SessionState


def test_before_emit_hook_transforms_payload() -> None:
    hook_manager = HookManager([ExampleBeforeEmitTaggerHook()])
    captured: List[Tuple[str, Dict[str, Any], Optional[int]]] = []

    def emitter(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
        captured.append((event_type, dict(payload), turn))

    session_state = SessionState("ws", "img", {}, event_emitter=None)
    session_state.set_event_emitter(
        _wrap_event_emitter_with_before_emit(hook_manager, session_state=session_state, emitter=emitter)
    )

    session_state._emit_event("assistant_message", {"text": "hi"}, turn=1)  # type: ignore[attr-defined]

    assert captured
    event_type, payload, turn = captured[0]
    assert event_type == "assistant_message"
    assert turn == 1
    assert payload.get("seq") == 1
    assert payload.get("debug", {}).get("tag") == "example"

