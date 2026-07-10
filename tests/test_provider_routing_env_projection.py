from __future__ import annotations

import json

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
