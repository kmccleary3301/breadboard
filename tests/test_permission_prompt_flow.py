from __future__ import annotations

import queue
from types import SimpleNamespace

import pytest

from agentic_coder_prototype.permission_broker import PermissionBroker, PermissionDeniedError
from agentic_coder_prototype.state.session_state import SessionState


def test_permission_prompt_allows_and_records_wildcard() -> None:
    events: list[tuple[str, dict, int | None]] = []

    def emitter(event_type: str, payload: dict, turn: int | None = None) -> None:
        events.append((event_type, payload, turn))

    permission_queue: queue.Queue[object] = queue.Queue()
    permission_queue.put("always")

    session_state = SessionState(workspace="/tmp", image="python-dev:latest", config={}, event_emitter=emitter)
    session_state.set_provider_metadata("permission_queue", permission_queue)

    broker = PermissionBroker(
        {
            "options": {"mode": "prompt"},
            "shell": {"default": "allow", "ask": ["npm install*"]},
        }
    )

    call = SimpleNamespace(function="bash", arguments={"command": "npm install foo"}, call_id="call_1")
    broker.ensure_allowed(session_state, [call])

    state = session_state.get_provider_metadata(PermissionBroker.STATE_KEY) or {}
    assert isinstance(state, dict)
    approved = (state.get("approved") or {}).get("shell") or []
    assert "npm install *" in approved

    evt_types = [e[0] for e in events]
    assert "permission_request" in evt_types
    assert "permission_response" in evt_types

    # Subsequent matching calls should not re-prompt when approved "always"
    events.clear()
    call2 = SimpleNamespace(function="bash", arguments={"command": "npm install bar"}, call_id="call_2")
    broker.ensure_allowed(session_state, [call2])
    assert "permission_request" not in [e[0] for e in events]


def test_permission_prompt_rejects() -> None:
    permission_queue: queue.Queue[object] = queue.Queue()
    permission_queue.put("reject")
    session_state = SessionState(workspace="/tmp", image="python-dev:latest", config={})
    session_state.set_provider_metadata("permission_queue", permission_queue)

    broker = PermissionBroker(
        {
            "options": {"mode": "prompt"},
            "shell": {"default": "ask"},
        }
    )

    call = SimpleNamespace(function="bash", arguments={"command": "echo hi"}, call_id="call_1")
    with pytest.raises(PermissionDeniedError) as excinfo:
        broker.ensure_allowed(session_state, [call])
    assert "The user rejected permission" in str(excinfo.value)


def test_permission_prompt_batches_multiple_calls_with_per_item_decisions() -> None:
    events: list[tuple[str, dict, int | None]] = []

    permission_queue: queue.Queue[object] = queue.Queue()

    def emitter(event_type: str, payload: dict, turn: int | None = None) -> None:
        events.append((event_type, payload, turn))
        if event_type != "permission_request":
            return
        items = payload.get("items") or []
        if not isinstance(items, list) or len(items) < 2:
            return
        item_ids: list[str] = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("item_id"), str):
                item_ids.append(item["item_id"])
        if len(item_ids) < 2:
            return
        permission_queue.put(
            {
                "request_id": payload.get("request_id") or payload.get("id"),
                "responses": {"items": {item_ids[0]: "once", item_ids[1]: "always"}},
            }
        )

    session_state = SessionState(workspace="/tmp", image="python-dev:latest", config={}, event_emitter=emitter)
    session_state.set_provider_metadata("permission_queue", permission_queue)

    broker = PermissionBroker(
        {
            "options": {"mode": "prompt"},
            "shell": {"default": "allow", "ask": ["npm install*"]},
        }
    )

    call1 = SimpleNamespace(function="bash", arguments={"command": "npm install foo"}, call_id="call_1")
    call2 = SimpleNamespace(function="bash", arguments={"command": "npm install bar"}, call_id="call_2")
    broker.ensure_allowed(session_state, [call1, call2])

    assert len([e for e in events if e[0] == "permission_request"]) == 1
    assert len([e for e in events if e[0] == "permission_response"]) == 1

    state = session_state.get_provider_metadata(PermissionBroker.STATE_KEY) or {}
    assert isinstance(state, dict)
    approved = (state.get("approved") or {}).get("shell") or []
    assert "npm install *" in approved

