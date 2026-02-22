from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import (
    SessionCommandResponse,
    SessionCreateResponse,
    SessionInputResponse,
    SessionStatus,
    SessionSummary,
)
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord


@pytest.mark.asyncio
async def test_operator_http_smoke_create_input_stop_stream() -> None:
    session_id = "sess-http-smoke"

    async def stream_events(_session_id: str, **_kwargs):  # pragma: no cover - trivial generator
        yield SessionEvent(EventType.TURN_START, session_id, {"turn": 1}, turn=1)
        yield SessionEvent(EventType.ASSISTANT_MESSAGE, session_id, {"text": "hello from smoke"}, turn=1)

    mock_service = MagicMock()
    mock_service.create_session = AsyncMock(
        return_value=SessionCreateResponse(session_id=session_id, status=SessionStatus.RUNNING, created_at="2026-02-22T00:00:00Z")
    )
    mock_service.list_sessions = AsyncMock(
        return_value=[
            SessionSummary(
                session_id=session_id,
                status=SessionStatus.RUNNING,
                created_at="2026-02-22T00:00:00Z",
                last_activity_at="2026-02-22T00:00:01Z",
                completion_summary=None,
                reward_summary=None,
                logging_dir=None,
                metadata={"mode": "build"},
            )
        ]
    )
    mock_service.ensure_session = AsyncMock(return_value=SessionRecord(session_id=session_id, status=SessionStatus.RUNNING))
    mock_service.send_input = AsyncMock(return_value=SessionInputResponse(status="accepted"))
    mock_service.execute_command = AsyncMock(return_value=SessionCommandResponse(status="ok", detail={"stopping": True}))
    mock_service.event_stream.side_effect = stream_events

    app = create_app(mock_service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/sessions",
            json={"config_path": "agent_configs/base_v2.yaml", "task": "operator smoke", "stream": False},
        )
        assert created.status_code == 200
        assert created.json()["session_id"] == session_id

        listed = await client.get("/sessions")
        assert listed.status_code == 200
        assert listed.json()[0]["session_id"] == session_id

        posted_input = await client.post(f"/sessions/{session_id}/input", json={"content": "hi"})
        assert posted_input.status_code == 202
        assert posted_input.json()["status"] == "accepted"

        posted_command = await client.post(f"/sessions/{session_id}/command", json={"command": "stop"})
        assert posted_command.status_code == 202
        assert posted_command.json()["status"] == "ok"

        seen_types: list[str] = []
        async with client.stream("GET", f"/sessions/{session_id}/events") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                seen_types.append(str(payload.get("type")))
                if len(seen_types) >= 2:
                    await response.aclose()
                    break
        assert seen_types == ["turn_start", "assistant_message"]
