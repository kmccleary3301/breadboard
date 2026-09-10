from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.cli_bridge.service import SessionService


def test_app_shutdown_quiesces_runtime_owners(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SessionService(state_root=tmp_path)
    calls: list[str] = []

    async def shutdown_runtime_owners() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(
        service,
        "shutdown_runtime_owners",
        shutdown_runtime_owners,
    )

    with TestClient(create_app(service)):
        pass

    assert calls == ["shutdown"]


@pytest.mark.asyncio
async def test_ready_reports_codex_runtime_when_registered() -> None:
    app = create_app(SessionService())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert "codex_app_server" in payload["provider_runtimes"]
    assert isinstance(payload.get("started_at"), str)
    assert isinstance(payload.get("started_at_unix"), (int, float))
    assert isinstance(payload.get("pid"), int)
    served_revision = payload.get("served_revision")
    assert isinstance(served_revision, dict)
    assert isinstance(served_revision.get("repo_root"), str)


@pytest.mark.asyncio
async def test_health_and_status_expose_served_revision_metadata() -> None:
    app = create_app(SessionService())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/health")
        status = await client.get("/status")

    assert health.status_code == 200
    assert status.status_code == 200

    health_payload = health.json()
    status_payload = status.json()

    for payload in (health_payload, status_payload):
        assert isinstance(payload.get("started_at"), str)
        assert isinstance(payload.get("started_at_unix"), (int, float))
        assert isinstance(payload.get("pid"), int)
        served_revision = payload.get("served_revision")
        assert isinstance(served_revision, dict)
        assert isinstance(served_revision.get("repo_root"), str)
        assert "commit" in served_revision
        assert "branch" in served_revision
        assert "dirty" in served_revision
