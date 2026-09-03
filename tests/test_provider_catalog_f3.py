from __future__ import annotations

import pytest

from breadboard_engine.api.cli_bridge.model_catalog import build_model_catalog
from breadboard_engine.provider.routing import ProviderRouteError, ProviderRouter
from breadboard_engine.provider.adapters import (
    AnthropicAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    provider_adapter_manager,
)
from breadboard_engine.provider.capabilities import CAPABILITY_MATRIX
from breadboard_engine.provider.runtime import provider_registry
from breadboard_engine.provider_broker.catalog import (
    get_provider_catalog_entry,
    get_provider_catalog_entry_for_adapter,
    routable_provider_catalog,
)
from breadboard_engine.provider_broker.broker import ProviderBroker
from breadboard_engine.provider_broker.store import SQLiteCredentialStore


def test_product_provider_view_is_exact_bounded_core_and_secret_free(
    tmp_path, monkeypatch
):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))

    rows = broker.listProviders()

    assert [row["provider_id"] for row in rows] == [
        "codex",
        "openai",
        "anthropic",
        "openrouter",
    ]
    assert all(row["support_tier"] == "core" for row in rows)
    assert all(row["model_discovery"] == "configured_only" for row in rows)
    codex = rows[0]
    assert codex == {
        "provider_id": "codex",
        "aliases": ["openai-codex"],
        "display_name": "Codex",
        "support_tier": "core",
        "auth_owner": "provider",
        "auth_schemes": ["provider_managed"],
        "available": True,
        "availability_reason": "provider_managed",
        "login_available": False,
        "oauth_flows": [],
        "model_discovery": "configured_only",
        "runtime_id": "codex_app_server",
        "compatible_protocol": "openai",
        "base_url": None,
    }
    assert {row["availability_reason"] for row in rows[1:]} == {"missing_auth"}
    assert not any(row["provider_id"].startswith("google-") for row in rows)
    assert not any(
        row["provider_id"] in {"mock", "cli_mock", "smoke", "replay"} for row in rows
    )

    monkeypatch.setenv("OPENAI_API_KEY", "secret-not-returned")
    openai = next(
        row for row in broker.listProviders() if row["provider_id"] == "openai"
    )
    assert openai["available"] is True
    assert openai["availability_reason"] is None
    assert "secret-not-returned" not in repr(openai)


def test_router_is_catalog_derived_canonicalizes_codex_alias_and_fails_closed(tmp_path):
    router = ProviderRouter(ProviderBroker(SQLiteCredentialStore(tmp_path / "router.sqlite3")))
    entries = {entry.provider_id: entry for entry in routable_provider_catalog()}

    assert (
        set(router.providers)
        == set(entries)
        == {
            "codex",
            "openai",
            "openrouter",
            "anthropic",
            "mock",
            "cli_mock",
            "smoke",
            "replay",
        }
    )
    for provider_id, config in router.providers.items():
        entry = entries[provider_id]
        assert config.runtime_id == entry.runtime_id
        assert config.base_url == entry.base_url
        assert config.api_key_env == entry.api_key_env
        assert config.default_api_variant == entry.default_api_variant

    assert set(provider_adapter_manager.adapters) == set(entries)
    for provider_id in entries:
        assert (
            provider_adapter_manager.get_adapter(provider_id).get_provider_id()
            == provider_id
        )
    tool = {
        "provider_routing": {
            "openai": {"native_primary": True},
            "codex": {"native_primary": False},
        }
    }
    assert (
        get_provider_catalog_entry_for_adapter("anthropic").provider_id == "anthropic"
    )
    assert (
        get_provider_catalog_entry_for_adapter("openai_responses").provider_id
        == "openai"
    )
    assert get_provider_catalog_entry_for_adapter("mock_chat") is None
    assert provider_adapter_manager.get_adapter("openai").should_use_native_tool(tool)
    assert not provider_adapter_manager.get_adapter("codex").should_use_native_tool(
        tool
    )
    assert not provider_adapter_manager.get_adapter(
        "openrouter"
    ).should_use_native_tool(tool)
    with pytest.raises(ValueError, match="unsupported provider adapter"):
        provider_adapter_manager.get_adapter("unknown")
    assert get_provider_catalog_entry("openai-codex").provider_id == "codex"
    assert router.parse_model_id("openai-codex/gpt-5.4") == (
        "codex",
        "gpt-5.4",
        "direct",
    )
    assert router.parse_model_id("openrouter/deepseek/deepseek-v4-flash-0731") == (
        "openrouter",
        "deepseek/deepseek-v4-flash-0731",
        "routed",
    )
    assert router.parse_model_id("gpt-4.1") == ("openai", "gpt-4.1", "direct")

    expected = {
        "": "invalid_model_id",
        " openai/gpt-4.1": "invalid_model_id",
        "openai//gpt-4.1": "invalid_model_id",
        "openai/gpt-4.1/extra": "invalid_model_id",
        "openrouter/gpt-4.1": "invalid_model_id",
        "unknown/gpt-4.1": "unknown_provider",
        "google-gemini-cli/gemini-3": "unsupported_provider",
        "openai/" + "x" * 513: "invalid_model_id",
    }
    for route, code in expected.items():
        with pytest.raises(ProviderRouteError) as caught:
            router.get_runtime_descriptor(route)
        assert caught.value.code == code


def test_model_catalog_is_configured_only_and_reports_every_rejected_source():
    models, issues = build_model_catalog(
        [
            {
                "id": "openai/gpt-4.1",
                "params": {"temperature": 0.2, "api_key": "sk-f3-model-canary-secret"},
                "routing": {
                    "Authorization": "Bearer sk-f3-model-canary-secret",
                    "region": "us-east",
                },
                "metadata": {"token": "sk-f3-model-canary-secret", "label": "safe"},
            },
            {"id": "openai-codex/gpt-5.4", "provider": "codex", "name": "Codex 5.4"},
            {"id": "openrouter/deepseek/deepseek-v4", "provider": "openrouter"},
            {"id": "deepseek/deepseek-native", "provider": "openrouter"},
            {"id": "google-gemini-cli/gemini-3", "provider": "google-gemini-cli"},
            {"id": "unknown/native-model", "provider": "unknown"},
            "openai/gpt-4.1",
        ],
        dynamic_models=["anthropic/dynamically-discovered"],
        credential_origin=lambda route: (
            {"kind": "env"} if route.startswith("openrouter/") else None
        ),
    )

    assert [model.id for model in models] == [
        "openai/gpt-4.1",
        "openai-codex/gpt-5.4",
        "openrouter/deepseek/deepseek-v4",
        "deepseek/deepseek-native",
        "google-gemini-cli/gemini-3",
        "unknown/native-model",
    ]
    by_id = {model.id: model for model in models}
    assert by_id["openai/gpt-4.1"].availability_reason == "missing_auth"
    assert "sk-f3-model-canary-secret" not in repr(by_id["openai/gpt-4.1"])
    assert by_id["openai/gpt-4.1"].params["api_key"] == "***REDACTED***"
    assert by_id["openai/gpt-4.1"].routing["Authorization"] == "***REDACTED***"
    assert by_id["openai/gpt-4.1"].metadata["label"] == "safe"
    assert by_id["openai-codex/gpt-5.4"].canonical_provider == "codex"
    assert by_id["openai-codex/gpt-5.4"].availability_reason == "provider_managed"
    assert by_id["openrouter/deepseek/deepseek-v4"].available is True
    assert by_id["deepseek/deepseek-native"].canonical_provider == "openrouter"
    assert by_id["deepseek/deepseek-native"].available is True
    assert (
        by_id["google-gemini-cli/gemini-3"].availability_reason == "deferred_provider"
    )
    assert by_id["unknown/native-model"].availability_reason == "unsupported_provider"
    assert [issue.code for issue in issues] == [
        "deferred_provider",
        "unsupported_provider",
        "duplicate_model",
        "stale_dynamic_catalog",
    ]
    assert not any(model.id == "anthropic/dynamically-discovered" for model in models)


def test_model_catalog_deduplicates_canonical_routes_across_aliases():
    models, issues = build_model_catalog(
        ["codex/gpt-5.4", "openai-codex/gpt-5.4"],
        credential_origin=lambda _route: None,
    )

    assert [model.id for model in models] == ["codex/gpt-5.4"]
    assert [(issue.code, issue.provider_id) for issue in issues] == [
        ("duplicate_model", "codex")
    ]


def test_model_catalog_derives_bare_routes_from_cataloged_config_adapters():
    models, issues = build_model_catalog(
        [
            {"id": "claude-sonnet-4-5", "adapter": "anthropic"},
            {"id": "gpt-5.4", "adapter": "openai_responses"},
            {"id": "unknown/native-model", "adapter": "openai"},
            {"id": "ambiguous-evidence", "adapter": "mock_chat"},
            {"id": "unsupported-test", "adapter": "test"},
        ],
        credential_origin=lambda _route: None,
    )

    assert [(model.id, model.canonical_provider) for model in models] == [
        ("claude-sonnet-4-5", "anthropic"),
        ("gpt-5.4", "openai"),
        ("unknown/native-model", None),
    ]
    assert models[2].support_tier == "unsupported"
    assert [issue.code for issue in issues] == [
        "unsupported_provider",
        "invalid_model",
        "invalid_model",
    ]
    assert [issue.index for issue in issues] == [2, 3, 4]


def test_model_catalog_classifies_malformed_known_provider_routes_as_invalid():
    models, issues = build_model_catalog(
        [
            "openai/gpt-4.1/extra",
            "openrouter/vendor-only",
            {"id": "native/extra", "provider": "anthropic"},
        ],
        credential_origin=lambda _route: None,
    )

    assert models == []
    assert [issue.code for issue in issues] == [
        "invalid_model",
        "invalid_model",
        "invalid_model",
    ]
    assert [issue.provider_id for issue in issues] == [
        "openai",
        "openrouter",
        "anthropic",
    ]


def test_model_catalog_empty_and_malformed_inputs_are_deterministic():
    assert build_model_catalog([], credential_origin=lambda _route: None) == ([], [])
    evidence_models, evidence_issues = build_model_catalog(
        ["mock/reference"],
        credential_origin=lambda _route: None,
    )
    assert evidence_issues == []
    assert evidence_models[0].support_tier == "evidence"
    assert evidence_models[0].available is True
    models, issues = build_model_catalog(
        [None, "", {"provider": "openai"}],
        credential_origin=lambda _route: None,
    )
    assert models == []
    assert [issue.code for issue in issues] == [
        "invalid_model",
        "invalid_model",
        "invalid_model",
    ]
    assert [issue.index for issue in issues] == [0, 1, 2]


def test_routable_provider_definition_projects_all_provider_surfaces(tmp_path):
    router = ProviderRouter(ProviderBroker(SQLiteCredentialStore(tmp_path / "router.sqlite3")))
    adapter_types = {
        "openai": OpenAIAdapter,
        "openrouter": OpenRouterAdapter,
        "anthropic": AnthropicAdapter,
    }

    for entry in routable_provider_catalog():
        descriptor, _ = router.get_runtime_descriptor(entry.provider_id)
        config = router.providers[entry.provider_id]
        adapter = provider_adapter_manager.get_adapter(entry.provider_id)

        assert entry.capabilities is not None
        assert CAPABILITY_MATRIX[entry.provider_id] is entry.capabilities
        assert config.runtime_id == entry.runtime_id
        assert descriptor.supports_native_tools == entry.supports_native_tools
        assert descriptor.supports_streaming == entry.supports_streaming
        assert descriptor.supports_reasoning_traces == entry.supports_reasoning_traces
        assert descriptor.supports_cache_control == entry.supports_cache_control
        assert isinstance(adapter, adapter_types[entry.tool_adapter_kind])
        assert adapter.get_provider_id() == entry.provider_id
        assert provider_registry.get_runtime_class(entry.runtime_id) is not None
