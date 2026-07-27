from __future__ import annotations

import json
import os

import httpx
import pytest

from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.auth.material import EngineAuthMaterial
from agentic_coder_prototype.auth.store import DEFAULT_PROVIDER_AUTH_STORE

from agentic_coder_prototype.provider.routing import ProviderRouter


def test_openai_env_projection_overrides_base_url_and_headers(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-token")
    monkeypatch.setenv("BREADBOARD_OPENAI_AUTH_BASE_URL", "https://proxy.example.test/v1")
    monkeypatch.setenv(
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
        json.dumps({"Authorization": "Bearer env-token", "X-Test": "1"}),
    )

    router = ProviderRouter()
    client_config = router.create_client_config("openai/gpt-5.4-mini")

    assert client_config["api_key"] == "env-token"
    assert client_config["base_url"] == "https://proxy.example.test/v1"
    assert client_config["default_headers"]["Authorization"] == "Bearer env-token"
    assert client_config["default_headers"]["X-Test"] == "1"


def test_in_memory_auth_overlay_detach_restores_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "baseline-test-value")
    monkeypatch.delenv("BREADBOARD_OPENAI_AUTH_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_OPENAI_AUTH_HEADERS_JSON", raising=False)
    DEFAULT_PROVIDER_AUTH_STORE.detach("openai")
    material = EngineAuthMaterial(
        provider_id="openai",
        api_key="attached-test-value",
        base_url="https://attached.example.test/v1",
        headers={"X-Attached-Test": "present"},
    )
    DEFAULT_PROVIDER_AUTH_STORE.attach(material)
    router = ProviderRouter()
    try:
        attached = router.create_client_config("openai/gpt-5.4-mini")
        assert attached["api_key"] == "attached-test-value"
        assert attached["base_url"] == "https://attached.example.test/v1"
        assert attached["default_headers"]["X-Attached-Test"] == "present"
    finally:
        assert DEFAULT_PROVIDER_AUTH_STORE.detach("openai") is True

    detached = router.create_client_config("openai/gpt-5.4-mini")
    assert detached["api_key"] == "baseline-test-value"
    assert detached.get("base_url") != "https://attached.example.test/v1"
    assert "X-Attached-Test" not in detached.get("default_headers", {})


def test_expired_in_memory_auth_overlay_is_not_reused(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "baseline-test-value")
    DEFAULT_PROVIDER_AUTH_STORE.detach("openai")
    DEFAULT_PROVIDER_AUTH_STORE.attach(
        EngineAuthMaterial(
            provider_id="openai",
            api_key="expired-test-value",
            expires_at_ms=1,
        )
    )

    client_config = ProviderRouter().create_client_config("openai/gpt-5.4-mini")

    assert client_config["api_key"] == "baseline-test-value"
    assert DEFAULT_PROVIDER_AUTH_STORE.get("openai") is None


@pytest.mark.asyncio
async def test_provider_auth_routes_do_not_project_attached_material_to_process_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "baseline-test-value")
    monkeypatch.delenv("BREADBOARD_OPENAI_AUTH_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_OPENAI_AUTH_HEADERS_JSON", raising=False)
    DEFAULT_PROVIDER_AUTH_STORE.detach("openai")
    app = create_app(SessionService(state_root=tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        attached = await client.post(
            "/v1/provider-auth/attach",
            json={
                "material": {
                    "provider_id": "openai",
                    "api_key": "attached-test-value",
                    "base_url": "https://attached.example.test/v1",
                    "headers": {"X-Attached-Test": "present"},
                }
            },
        )
        assert attached.status_code == 200
        assert os.environ["OPENAI_API_KEY"] == "baseline-test-value"
        assert "BREADBOARD_OPENAI_AUTH_BASE_URL" not in os.environ
        assert "BREADBOARD_OPENAI_AUTH_HEADERS_JSON" not in os.environ
        assert ProviderRouter().create_client_config("openai/gpt-5.4-mini")["api_key"] == "attached-test-value"

        detached = await client.post("/v1/provider-auth/detach", json={"provider_id": "openai"})
        assert detached.status_code == 200
        assert ProviderRouter().create_client_config("openai/gpt-5.4-mini")["api_key"] == "baseline-test-value"
