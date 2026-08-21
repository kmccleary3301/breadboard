from __future__ import annotations

import asyncio
import json
from typing import List

import httpx
import pytest

from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.cli_bridge.events import EventType, SessionEvent
from breadboard_engine.api.cli_bridge.models import SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
from breadboard_engine.api.cli_bridge.service import SessionService


@pytest.mark.asyncio
async def test_sse_endpoint_streams_events_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "0")
    app = create_app(service)

    record = SessionRecord(session_id="sess-sse", status=SessionStatus.RUNNING)
    await registry.create(record)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(SessionEvent(EventType.TURN_START, "sess-sse", {"turn": 1}, turn=1))
        await asyncio.sleep(0.01)
        await record.event_queue.put(SessionEvent(EventType.ASSISTANT_MESSAGE, "sess-sse", {"text": "hi"}, turn=1))
        await asyncio.sleep(0.01)
        await record.event_queue.put(None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        task = asyncio.create_task(producer())
        events: List[dict] = []
        current_id: str | None = None
        async with client.stream("GET", "/v1/sessions/sess-sse/events") as response:
            async for line in response.aiter_lines():
                if line.startswith("id: "):
                    current_id = line.removeprefix("id: ").strip()
                    continue
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                if current_id is not None:
                    payload["_sse_id"] = current_id
                    current_id = None
                events.append(payload)
                if len(events) >= 3:
                    await response.aclose()
                    break
        await task

    assert events[0]["type"] == EventType.STREAM_OPEN.value
    assert events[0]["stable_cursor"] is False
    assert "_sse_id" not in events[0]
    assert events[0]["payload"]["headSequence"] == 0
    assert events[1]["type"] == EventType.TURN_START.value
    assert events[2]["type"] == EventType.ASSISTANT_MESSAGE.value
    assert events[2]["payload"]["text"] == "hi"
    assert int(events[1]["_sse_id"]) == events[1]["seq"]
    assert int(events[2]["_sse_id"]) == events[2]["seq"]
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task


@pytest.mark.asyncio
async def test_sse_endpoint_handles_completion_event(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "0")
    app = create_app(service)

    record = SessionRecord(session_id="sess-complete", status=SessionStatus.RUNNING)
    await registry.create(record)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(
            SessionEvent(EventType.COMPLETION, "sess-complete", {"summary": {"completed": True}})
        )
        await asyncio.sleep(0.01)
        await record.event_queue.put(None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        task = asyncio.create_task(producer())
        async with client.stream("GET", "/v1/sessions/sess-complete/events") as response:
            events: List[dict] = []
            current_id: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("id: "):
                    current_id = line.removeprefix("id: ").strip()
                    continue
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                if current_id is not None:
                    payload["_sse_id"] = current_id
                    current_id = None
                events.append(payload)
                if len(events) >= 2:
                    await response.aclose()
                    break
            assert events[0]["type"] == EventType.STREAM_OPEN.value
            assert events[0]["stable_cursor"] is False
            assert "_sse_id" not in events[0]
            assert events[1]["type"] == EventType.COMPLETION.value
            assert events[1]["payload"]["summary"]["completed"]
            assert int(events[1]["_sse_id"]) == events[1]["seq"]
        await task

    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task


@pytest.mark.asyncio
async def test_sse_endpoint_replay_buffer() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)

    record = SessionRecord(session_id="sess-replay", status=SessionStatus.RUNNING)
    await registry.create(record)

    record.event_log.append(SessionEvent(EventType.TURN_START, "sess-replay", {"turn": 1}, turn=1))
    record.event_log.append(
        SessionEvent(EventType.ASSISTANT_MESSAGE, "sess-replay", {"text": "hello"}, turn=1)
    )

    generator = service.event_stream("sess-replay", replay=True)
    events = []
    async for event in generator:
        events.append(event)
        if len(events) >= 2:
            break
    await generator.aclose()
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task

    assert events[0].type == EventType.TURN_START
    assert events[1].type == EventType.ASSISTANT_MESSAGE


@pytest.mark.asyncio
async def test_sse_resume_missing_event_id_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "0")
    app = create_app(service)

    record = SessionRecord(session_id="sess-resume", status=SessionStatus.RUNNING)
    await registry.create(record)
    record.event_log.append(SessionEvent(EventType.TURN_START, "sess-resume", {"turn": 1}, turn=1))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/sessions/sess-resume/events",
            headers={"Last-Event-ID": "9999"},
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "resume_window_exceeded"
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task
