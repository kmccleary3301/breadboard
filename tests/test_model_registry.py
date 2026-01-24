from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.provider_routing import ProviderRouter


def test_model_registry_overrides_and_auth(tmp_path: Path, monkeypatch) -> None:
    registry_path = tmp_path / "models.json"
    key_path = tmp_path / "keys.json"
    registry_payload = {
        "default_provider": "anthropic",
        "providers": {"openai": {"api_key_env": "OPENAI_KEY_CUSTOM"}},
        "models": [
            {
                "id": "fast",
                "route": "openrouter/openai/gpt-5-nano",
                "supports_native_tools": False,
            },
            {
                "id": "alias-model",
                "provider": "openai",
                "model_id": "gpt-4.1",
            },
        ],
    }
    registry_path.write_text(json.dumps(registry_payload), encoding="utf-8")
    key_path.write_text(json.dumps({"openai": "sk-test"}), encoding="utf-8")

    monkeypatch.delenv("OPENAI_KEY_CUSTOM", raising=False)
    router = ProviderRouter(registry_path=str(registry_path), auth_paths=[str(key_path)])

    cfg, actual_model, supports_native = router.get_provider_config("fast")
    assert cfg.provider_id == "openrouter"
    assert actual_model == "openai/gpt-5-nano"
    assert supports_native is False

    cfg2, actual_model2, _ = router.get_provider_config("alias-model")
    assert cfg2.provider_id == "openai"
    assert actual_model2 == "gpt-4.1"
    assert cfg2.api_key_env == "OPENAI_KEY_CUSTOM"

    client_cfg = router.create_client_config("alias-model")
    assert client_cfg["api_key"] == "sk-test"
