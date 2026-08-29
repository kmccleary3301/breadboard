from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard_engine.api.cli_bridge.events import EventType
from breadboard_engine.api.cli_bridge.session_control import SessionControlController
from breadboard_engine.api.cli_bridge.session_lifecycle import SessionLifecycleOwner
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.api.cli_bridge.task_execution import TaskExecutionOwner


@pytest.mark.asyncio
async def test_session_runner_run_delegates_to_lifecycle_owner() -> None:
    runner = object.__new__(SessionRunner)
    calls: list[str] = []

    class Lifecycle:
        async def run(self) -> None:
            calls.append("run")

    runner._lifecycle_owner = Lifecycle()
    await runner._run()
    assert calls == ["run"]


@pytest.mark.asyncio
async def test_lifecycle_terminalizes_when_running_transition_fails(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Registry:
        async def update_status(self, *_args, **_kwargs) -> None:
            raise RuntimeError("registry unavailable")

    async def enqueue_termination() -> None:
        calls.append("terminate")

    host = SimpleNamespace(
        session=SimpleNamespace(session_id="session-1"),
        registry=Registry(),
        _closed=False,
        _enqueue_termination=enqueue_termination,
    )
    owner = SessionLifecycleOwner(host, SimpleNamespace())

    async def fail(_state, exc) -> None:
        assert str(exc) == "registry unavailable"
        calls.append("fail")

    monkeypatch.setattr(owner, "_fail", fail)

    await owner.run()

    assert host._closed is True
    assert calls == ["fail", "terminate"]


@pytest.mark.asyncio
async def test_task_execution_replay_uses_owner_parse_and_correlation(
    tmp_path: Path,
) -> None:
    replay = tmp_path / "events.jsonl"
    replay.write_text(
        json.dumps({"type": "completion", "payload": {"summary": {"completed": True}}})
        + "\n",
        encoding="utf-8",
    )
    turn = SimpleNamespace(input_id="input-1", terminal_outcome=None)
    session = SimpleNamespace(
        session_id="session-1",
        metadata={},
        turns_by_id={"turn-1": turn},
        active_turn_id="turn-1",
    )

    class Registry:
        async def update_metadata(self, *_args, **_kwargs) -> None:
            return None

    host = SimpleNamespace(
        session=session,
        registry=Registry(),
        _stop_event=asyncio.Event(),
        _mode=None,
        _parse_replay_path=lambda *_args: (_ for _ in ()).throw(
            AssertionError("host alias called")
        ),
        _require_execution_correlation=lambda *_args: (_ for _ in ()).throw(
            AssertionError("host alias called")
        ),
        _translate_runtime_event=lambda event_type, payload, turn: (
            EventType(event_type),
            payload,
            turn,
            {},
        ),
        publish_event_async=lambda *_args, **_kwargs: None,
    )
    owner = TaskExecutionOwner(host)

    result = await owner.execute_replay_task(
        f"replay:{replay}", input_id="input-1", turn_id="turn-1"
    )
    assert result["completion_summary"]["reason"] == "replay"
    assert len(result["_terminal_events"]) == 2


def test_task_execution_usage_and_queue_methods_do_not_use_host_aliases() -> None:
    session = SimpleNamespace(session_id="session-1")
    host = SimpleNamespace(
        session=session,
        _published_events=0,
        _normalize_usage_payload=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("host alias called")
        ),
        _drain_event_queue=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("host alias called")
        ),
    )
    owner = TaskExecutionOwner(host)

    usage = owner.usage_from_run_summary(
        {"turn_diagnostics": [{"usage": {"prompt_tokens": 2, "completion_tokens": 3}}]}
    )
    assert usage["total_tokens"] == 5

    seen: list[tuple[str, dict, object]] = []
    events = queue.Queue()
    events.put(("assistant_message", {"text": "hello"}, None))
    owner.drain_event_queue(
        events,
        lambda event_type, payload, *, turn: seen.append((event_type, payload, turn)),
    )
    assert seen == [("assistant_message", {"text": "hello"}, None)]


@pytest.mark.asyncio
async def test_control_permission_projection_uses_owner_helper_directly() -> None:
    published: list[EventType] = []
    session = SimpleNamespace(metadata={})

    async def publish(event_type: EventType, _payload: dict) -> None:
        published.append(event_type)

    host = SimpleNamespace(
        session=session,
        _product_session_lock=threading.RLock(),
        _consumed_permission_responses={},
        _update_pending_permissions=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("host alias called")
        ),
        _persist_metadata_snapshot_threadsafe=lambda: None,
        publish_event_async=publish,
    )
    owner = SessionControlController(host)

    payload = await owner.emit_debug_permission_request({"request_id": "permission-1"})
    assert payload["request_id"] == "permission-1"
    assert published == [EventType.PERMISSION_REQUEST]
    assert session.metadata["pending_permissions"][0]["request_id"] == "permission-1"
