from __future__ import annotations

from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.service import SessionService


def test_session_limits_endpoint_returns_latest_snapshot() -> None:
    from fastapi.testclient import TestClient

    from agentic_coder_prototype.api.cli_bridge.app import create_app

    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-1", status="running")  # type: ignore[arg-type]
    record.event_log.append(
        SessionEvent(
            type=EventType.LIMITS_UPDATE,
            session_id="sess-1",
            payload={"provider": "openai", "observed_at_ms": 0, "buckets": []},
        )
    )
    # Create service with our injected registry/record.
    service = SessionService(registry=registry)

    async def _setup() -> None:
        await registry.create(record)

    import asyncio

    asyncio.run(_setup())
    client = TestClient(create_app(service))
    resp = client.get("/sessions/sess-1/limits")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["snapshot"]["provider"] == "openai"
