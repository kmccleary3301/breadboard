from __future__ import annotations

import asyncio
import queue
from typing import Any, Dict, List, Optional, Tuple

import pytest

from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner
from agentic_coder_prototype.state.session_state import SessionState


class EventCollector:
    def __init__(self) -> None:
        self.events: List[Tuple[str, Dict[str, Any], Optional[int]]] = []

    def __call__(self, event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None) -> None:
        self.events.append((event_type, payload, turn))

    def of_type(self, event_type: str) -> List[Tuple[str, Dict[str, Any], Optional[int]]]:
        return [evt for evt in self.events if evt[0] == event_type]


def test_session_state_emits_unique_assistant_events_per_turn() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)

    # Initial user prompt (no turn yet) should emit a user_message event once.
    state.add_message({"role": "user", "content": "hello"}, to_provider=True)
    user_events = collector.of_type("user_message")
    assert len(user_events) == 1
    assert user_events[0][1]["message"]["content"] == "hello"
    assert user_events[0][2] is None  # No active turn yet

    # Begin a turn and emit assistant messages with both transcript and provider copies.
    collector.events.clear()
    state.begin_turn(1)
    state.add_message({"role": "assistant", "content": "draft patch"}, to_provider=False)
    state.add_message({"role": "assistant", "content": "draft patch"}, to_provider=True)

    assistant_events = collector.of_type("assistant_message")
    assert len(assistant_events) == 1
    assert assistant_events[0][1]["message"]["content"] == "draft patch"
    assert assistant_events[0][2] == 1


def test_session_state_tool_events_cover_calls_and_results() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)
    state.begin_turn(2)

    state.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}
            ],
        },
        to_provider=False,
    )
    state.record_tool_event(2, "run_shell", success=True, metadata={"is_run_shell": True})
    state.add_message({"role": "tool", "content": "ok"}, to_provider=False)

    tool_call_events = collector.of_type("tool_call")
    assert len(tool_call_events) == 1
    assert tool_call_events[0][1]["call"]["function"]["name"] == "run_shell"

    tool_result_events = collector.of_type("tool_result")
    assert len(tool_result_events) == 2  # one from record_tool_event, one from tool role message
    names = {evt[1].get("tool") or evt[1].get("message", {}).get("role") for evt in tool_result_events}
    assert "run_shell" in names


def test_session_runner_translates_runtime_events() -> None:
    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-1", status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path="cfg.yaml", task="do work")

    runner = SessionRunner(session=record, registry=registry, request=request)
    translated = runner._translate_runtime_event(
        "assistant_message",
        {"message": {"role": "assistant", "content": "hi"}},
        turn=3,
    )
    assert translated is not None
    evt_type, payload, turn = translated
    assert evt_type is EventType.ASSISTANT_MESSAGE
    assert payload["text"] == "hi"
    assert turn == 3

    assert runner._translate_runtime_event("unknown", {}, turn=None) is None


def test_session_runner_queue_pump_processes_events() -> None:
    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-2", status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path="cfg.yaml", task="do work")

    runner = SessionRunner(session=record, registry=registry, request=request)

    captured: List[Tuple[str, Dict[str, Any], Optional[int]]] = []

    def capture(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
        captured.append((event_type, payload, turn))

    class FakeQueue:
        def __init__(self) -> None:
            self._queue: "queue.Queue[Tuple[Any, Any, Any]]" = queue.Queue()

        def put(self, item: Tuple[Any, Any, Any]) -> None:
            self._queue.put(item)

        def get(self, timeout: float | None = None) -> Tuple[Any, Any, Any]:
            return self._queue.get(timeout=timeout)

        def get_nowait(self) -> Tuple[Any, Any, Any]:
            return self._queue.get_nowait()

    fake_queue = FakeQueue()
    stop_event, thread = runner._start_queue_pump(fake_queue, capture)
    fake_queue.put(("assistant_message", {"message": {"content": "stream"}}, 5))
    fake_queue.put((None, None, None))
    stop_event.set()
    thread.join(timeout=1)
    runner._drain_event_queue(fake_queue, capture)

    assert captured
    assert captured[0][0] == "assistant_message"
    assert captured[0][1]["message"]["content"] == "stream"


@pytest.mark.asyncio
async def test_session_service_event_stream_yields_ordered_events() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(session_id="sess-stream", status=SessionStatus.RUNNING)
    await registry.create(record)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(SessionEvent(EventType.TURN_START, "sess-stream", {"turn": 1}, turn=1))
        await record.event_queue.put(SessionEvent(EventType.ASSISTANT_MESSAGE, "sess-stream", {"text": "hi"}, turn=1))

    task = asyncio.create_task(producer())
    stream = service.event_stream("sess-stream")
    first = await stream.__anext__()
    second = await stream.__anext__()
    await stream.aclose()
    await task

    assert first.type is EventType.TURN_START
    assert second.type is EventType.ASSISTANT_MESSAGE
    assert second.payload["text"] == "hi"


@pytest.mark.asyncio
async def test_session_service_event_stream_handles_completion() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(session_id="sess-complete", status=SessionStatus.RUNNING)
    await registry.create(record)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(SessionEvent(EventType.COMPLETION, "sess-complete", {"summary": {"completed": True}}))

    task = asyncio.create_task(producer())
    stream = service.event_stream("sess-complete")
    completion = await stream.__anext__()
    await stream.aclose()
    await task

    assert completion.type is EventType.COMPLETION
    assert completion.payload["summary"]["completed"]
