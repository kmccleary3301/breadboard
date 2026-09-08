from __future__ import annotations

import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard_engine.api.cli_bridge.events import EventType
from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
from breadboard_engine.api.cli_bridge.session_control import SessionControlController
from breadboard_engine.api.cli_bridge.session_lifecycle import SessionLifecycleOwner
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.api.cli_bridge.task_execution import TaskExecutionOwner
from breadboard_engine.todo import TodoDraft, TodoPatch, TodoStore




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
        calls.append("fail")

    monkeypatch.setattr(owner, "_fail", fail)

    await owner.run()

    assert host._closed is True
    assert calls == ["fail", "terminate"]




def test_task_execution_aggregates_usage_and_delivers_queued_events() -> None:
    session = SimpleNamespace(session_id="session-1")
    host = SimpleNamespace(
        session=session,
        _published_events=0,
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
async def test_debug_permission_request_publishes_pending_permission() -> None:
    published: list[EventType] = []
    session = SimpleNamespace(metadata={})

    async def publish(event_type: EventType, _payload: dict) -> None:
        published.append(event_type)

    host = SimpleNamespace(
        session=session,
        _product_session_lock=threading.RLock(),
        _consumed_permission_responses={},
        _persist_metadata_snapshot_threadsafe=lambda: None,
        publish_event_async=publish,
    )
    owner = SessionControlController(host)

    payload = await owner.emit_debug_permission_request({"request_id": "permission-1"})
    assert payload["request_id"] == "permission-1"
    assert published == [EventType.PERMISSION_REQUEST]
    assert session.metadata["pending_permissions"][0]["request_id"] == "permission-1"


def test_task_execution_recovers_persisted_todo_snapshot(tmp_path: Path) -> None:
    store = TodoStore(str(tmp_path))
    todo = store.create([TodoDraft(title="Review retained work")])[0]
    store.update(todo.id, TodoPatch(status="in_progress"))
    runner = SessionRunner(
        session=SessionRecord(session_id="todo-recovery", status=SessionStatus.RUNNING),
        registry=SessionRegistry(),
        request=SessionCreateRequest(
            config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml"
        ),
    )

    envelope = runner._task_execution.load_todo_envelope_from_disk(tmp_path)

    assert envelope is not None
    assert envelope["op"] == "snapshot"
    assert envelope["scope_key"] == "main"
    assert envelope["revision"] == 2
    assert [
        (item["id"], item["title"], item["status"]) for item in envelope["items"]
    ] == [(todo.id, "Review retained work", "in_progress")]
