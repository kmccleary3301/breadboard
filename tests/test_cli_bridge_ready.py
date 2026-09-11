from __future__ import annotations

import signal
import httpx
import pytest
from fastapi.testclient import TestClient

import breadboard_engine.api.cli_bridge.app as app_module
from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.cli_bridge.service import SessionService


def test_ray_initialization_preserves_server_signal_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        signal.SIGINT: object(),
        signal.SIGTERM: object(),
    }
    current = dict(expected)

    def fake_getsignal(managed_signal: signal.Signals) -> object:
        return current[managed_signal]

    def fake_signal(managed_signal: signal.Signals, handler: object) -> object:
        previous = current[managed_signal]
        current[managed_signal] = handler
        return previous

    class FakeRay:
        @staticmethod
        def init(*, address: str, include_dashboard: bool) -> None:
            assert address == "local"
            assert include_dashboard is False
            app_module.signal.signal(signal.SIGINT, object())
            app_module.signal.signal(signal.SIGTERM, object())

    monkeypatch.setattr(app_module.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(app_module.signal, "signal", fake_signal)

    app_module._initialize_local_ray(FakeRay())

    assert current == expected


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
