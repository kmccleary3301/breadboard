from __future__ import annotations

import asyncio
import json
from typing import List

import httpx
import pytest

from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.service import SessionService


@pytest.mark.asyncio
async def test_sse_endpoint_streams_events_in_order() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
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
        async with client.stream("GET", "/sessions/sess-sse/events") as response:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                events.append(payload)
                if len(events) >= 2:
                    await response.aclose()
                    break
        await task

    assert events[0]["type"] == EventType.TURN_START.value
    assert events[1]["type"] == EventType.ASSISTANT_MESSAGE.value
    assert events[1]["payload"]["text"] == "hi"


@pytest.mark.asyncio
async def test_sse_endpoint_handles_completion_event() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
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
        async with client.stream("GET", "/sessions/sess-complete/events") as response:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                assert payload["type"] == EventType.COMPLETION.value
                assert payload["payload"]["summary"]["completed"]
                await response.aclose()
                break
        await task
