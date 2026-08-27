from __future__ import annotations

import errno
import hashlib
import itertools
import json
import os
import pathlib
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from breadboard_engine.provider_broker import (
    AUTH_SOURCE_PRECEDENCE,
    REMOTE_BROKER_URL_ENV,
    ProviderBroker,
    ProviderBrokerConfigurationError,
    SQLiteCredentialStore,
    default_store_path,
    get_provider_broker,
)
from breadboard_engine.security import redaction


_E4_SOURCE_KEYS = {source: f"e4-{source}-canary" for source in AUTH_SOURCE_PRECEDENCE}


def _e4_precedence_broker(
    root: pathlib.Path,
    enabled_sources: set[str],
) -> tuple[ProviderBroker, dict[str, str]]:
    store = SQLiteCredentialStore(root / "credentials.sqlite3")

    def fallback(provider_id: str):
        if provider_id == "openai" and "fallback" in enabled_sources:
            return {"api_key": _E4_SOURCE_KEYS["fallback"]}
        return None

    broker = ProviderBroker(
        store,
        fallback_resolver=fallback,
        fallback_origins=(
            {"openai": "resolver"} if "fallback" in enabled_sources else None
        ),
    )
    if "runtime" in enabled_sources:
        broker.set_runtime_api_key("openai", _E4_SOURCE_KEYS["runtime"])
    if "config" in enabled_sources:
        broker.set_config_api_key("openai", _E4_SOURCE_KEYS["config"])
    if "oauth" in enabled_sources:
        expires_at_ms = int(time.time() * 1000) + 60_000
        store.put_oauth(
            provider_id="openai",
            auth_scheme_id="oauth2",
            label="e4-oauth",
            source="login",
            expires_at_ms=expires_at_ms,
            material={
                "access_token": _E4_SOURCE_KEYS["oauth"],
                "refresh_token": "e4-oauth-refresh-canary",
                "expires_at_ms": expires_at_ms,
            },
        )
    if "login_api_key" in enabled_sources:
        broker.putApiKey(
            {
                "provider_id": "openai",
                "account_label": "e4-login-api-key",
                "api_key": _E4_SOURCE_KEYS["login_api_key"],
            }
        )
    if "stored_api_key" in enabled_sources:
        store.put_api_key(
            provider_id="openai",
            auth_scheme_id="api_key",
            label="e4-stored-api-key",
            source="stored",
            material={"api_key": _E4_SOURCE_KEYS["stored_api_key"]},
        )
    environment = (
        {"OPENAI_API_KEY": _E4_SOURCE_KEYS["env"]} if "env" in enabled_sources else {}
    )
    return broker, environment


def _e4_origin_leg(origin: dict[str, str]) -> str:
    if origin["kind"] == "api_key":
        return "login_api_key" if origin.get("source") == "login" else "stored_api_key"
    return origin["kind"]


def test_default_store_path_uses_state_dir_as_the_state_directory(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("BREADBOARD_CREDENTIAL_STORE_PATH", raising=False)
    monkeypatch.delenv("BREADBOARD_CREDENTIAL_DB", raising=False)
    state_dir = tmp_path / "configured-state"
    monkeypatch.setenv("BREADBOARD_STATE_DIR", str(state_dir))

    assert default_store_path() == state_dir / "credentials.sqlite3"


def test_broker_nine_method_surface_and_plain_data(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    methods = (
        "listProviders",
        "listCredentials",
        "beginLogin",
        "getLogin",
        "completeLogin",
        "cancelLogin",
        "putApiKey",
        "logout",
        "revoke",
    )
    assert all(callable(getattr(broker, name)) for name in methods)

    credential = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "work",
            "api_key": "sk-broker-contract-secret",
        }
    )
    assert credential["provider_id"] == "openai"
    assert credential["source"] == "login"
    assert "api_key" not in credential
    assert "material" not in credential
    assert broker.listCredentials("openai")[0]["account_id"] == credential["account_id"]

    login = broker.beginLogin({"provider_id": "unknown-provider"})
    assert login["status"] == "unavailable"
    assert login["problem"]["code"] == "flow_unavailable"
    assert broker.getLogin(login["login_session_id"])["status"] == "unavailable"
    assert (
        broker.completeLogin({"login_session_id": login["login_session_id"]})[
            "problem"
        ]["code"]
        == "flow_unavailable"
    )
    assert broker.cancelLogin(login["login_session_id"])["ok"] is False
    assert broker.getLogin(login["login_session_id"])["status"] == "unavailable"

    assert broker.logout({"account_id": credential["account_id"]})["ok"] is True
    assert broker.revoke({"account_id": credential["account_id"]})["ok"] is True
    assert broker.listCredentials("openai")[0]["status"] == "revoked"

def test_broker_rejects_secret_bearing_account_id(tmp_path):
    secret = "credential-account-id-canary-8p4ws"
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "credentials.sqlite3")
    )

    with pytest.raises(
        ValueError,
        match="credential identity fields cannot contain credential material",
    ):
        broker.putApiKey(
            {
                "provider_id": "openai",
                "account_label": "main",
                "account_id": secret,
                "api_key": secret,
            }
        )

    assert broker.listCredentials() == []
    assert broker.audit_events() == []
    assert secret not in redaction.iter_registered_secret_values()



def test_store_separates_secret_material_and_enforces_expiring_leases(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(db))
    broker.putApiKey(
        {
            "provider_id": "anthropic",
            "account_label": "lease",
            "api_key": "anthropic-lease-secret",
            "ttl_seconds": 60,
        }
    )
    inspected = broker.store.inspect_accounts()
    assert inspected and "anthropic-lease-secret" not in json.dumps(inspected)
    with broker.execution_material(
        "anthropic",
        session_id="session-1",
        endpoint_id="messages",
    ) as material:
        assert material and material["api_key"] == "anthropic-lease-secret"
        lease_id = material["lease_id"]
        assert "anthropic-lease-secret" in redaction.iter_registered_secret_values()
    assert material == {}
    assert broker.store.release_lease(lease_id) is False


def test_restarted_broker_scopes_leased_secrets_for_redaction(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    original = ProviderBroker(SQLiteCredentialStore(db))
    original.putApiKey(
        {
            "provider_id": "anthropic",
            "account_label": "restart",
            "api_key": "anthropic-restart-secret",
            "headers": {"X-Custom-Auth": "custom-header-secret"},
            "base_url": (
                "https://url-user:url-password@example.test/v1"
                "?api_key=query-secret"
            ),
            "routing": {"refresh_token": "routing-secret"},
        }
    )
    redaction.clear_registered_secret_values()

    restarted = ProviderBroker(SQLiteCredentialStore(db))
    with restarted.execution_material("anthropic") as material:
        assert material is not None
        assert {
            "anthropic-restart-secret",
            "X-Custom-Auth",
            "custom-header-secret",
            "url-user",
            "url-password",
            "query-secret",
            "refresh_token",
            "routing-secret",
        } <= set(redaction.iter_registered_secret_values())

    assert material == {}
    assert redaction.iter_registered_secret_values() == ()


def test_session_start_child_inherits_no_credential_environment(tmp_path, monkeypatch):
    from breadboard_engine.security import build_child_environment

    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "child",
            "api_key": "e3-child-store-canary",
        }
    )
    canaries = {
        "OPENAI_API_KEY": "e3-openai-env-canary",
        "OPENROUTER_API_KEY": "e3-openrouter-env-canary",
        "ANTHROPIC_API_KEY": "e3-anthropic-env-canary",
        "GOOGLE_API_KEY": "e3-google-env-canary",
        "GEMINI_API_KEY": "e3-gemini-env-canary",
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON": '{"Authorization":"e3-header-canary"}',
        "BREADBOARD_CREDENTIAL_STORE_PATH": str(tmp_path / "credentials.sqlite3"),
        "BREADBOARD_AUTH_BROKER_TOKEN": "e4-remote-broker-token-canary",
    }
    for key, value in canaries.items():
        monkeypatch.setenv(key, value)
    broker_url = "https://remote-broker.example.test"
    monkeypatch.setenv(REMOTE_BROKER_URL_ENV, broker_url)

    child_env = build_child_environment()
    assert not set(canaries).intersection(child_env)
    assert child_env[REMOTE_BROKER_URL_ENV] == "configured"
    assert child_env["BREADBOARD_AUTH_BROKER_CONFIGURED"] == "1"
    assert broker_url not in json.dumps(child_env)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, sys; print(json.dumps({'env': dict(os.environ), 'argv': sys.argv}))",
        ],
        env=child_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert all(value not in result.stdout for value in canaries.values())


def test_child_environment_replaces_remote_broker_url_with_sentinel() -> None:
    from breadboard_engine.security import build_child_environment

    endpoint = "https://remote-broker.example.test/credential-shaped"
    with redaction.secret_value_scope(endpoint):
        child = build_child_environment(source={REMOTE_BROKER_URL_ENV: endpoint})

    assert child[REMOTE_BROKER_URL_ENV] == "configured"
    assert child["BREADBOARD_AUTH_BROKER_CONFIGURED"] == "1"
    assert endpoint not in json.dumps(child)


def test_agent_config_does_not_cross_inline_provider_credentials(monkeypatch):
    from breadboard_engine.agent import AgenticCoder

    canaries = {
        "provider_auth_runtime": {
            "openai": {
                "api_key": "e3-agent-config-canary",
                "headers": {"Authorization": "e3-agent-header-canary"},
            }
        },
        "wrapper": {
            "provider_auth_runtime.openai.api_key": "e3-nested-config-canary",
        },
        "workspace": {"root": "/tmp/e3-agent-workspace"},
    }
    monkeypatch.setattr(AgenticCoder, "_load_config", lambda _self: canaries)

    agent = AgenticCoder("unused.json", force_local_mode=True)

    assert not redaction.contains_provider_auth_runtime(agent.config)
    assert "e3-agent-config-canary" not in json.dumps(agent.config)
    assert (
        agent.apply_runtime_overrides(
            {"provider_auth_runtime.openai.api_key": "e3-runtime-override-canary"}
        )
        is False
    )
    assert "e3-runtime-override-canary" not in json.dumps(agent.config)
    assert (
        agent.apply_runtime_overrides(
            {
                "providers.provider_auth_runtime.openai.api_key": (
                    "e3-prefixed-runtime-override-canary"
                )
            }
        )
        is False
    )


def test_provider_client_construction_uses_one_operation_broker_lease(
    tmp_path, monkeypatch
):
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider import sdk_bindings
    from breadboard_engine.provider.routing import ProviderDescriptor, ProviderRouter
    from breadboard_engine.provider.runtimes.openai import OpenAIChatRuntime

    secret = "sk-runtime-secret"
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "runtime",
            "api_key": secret,
        }
    )
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    router = ProviderRouter()
    metadata = router.create_client_config("openai/gpt-5.4-mini")
    assert metadata["api_key"] is None
    assert secret not in json.dumps(metadata)

    calls = []
    released = []
    release = broker.store.release_lease

    def record_release(lease_id):
        released.append(lease_id)
        return release(lease_id)

    monkeypatch.setattr(broker.store, "release_lease", record_release)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(sdk_bindings.provider_sdk_bindings, "openai", FakeOpenAI)
    runtime = OpenAIChatRuntime(
        ProviderDescriptor(
            provider_id="openai",
            runtime_id="openai_chat",
            default_api_variant="chat",
            supports_native_tools=True,
            supports_streaming=True,
            supports_reasoning_traces=True,
            supports_cache_control=False,
            tool_schema_format="openai",
            base_url=None,
            api_key_env="OPENAI_API_KEY",
            default_headers={},
        )
    )
    with router.execution_client_config("openai/gpt-5.4-mini") as leased_config:
        assert leased_config["api_key"] == secret
        runtime.create_client(
            leased_config["api_key"],
            base_url=leased_config.get("base_url"),
            default_headers=leased_config.get("default_headers"),
        )
    assert leased_config == {}
    assert calls == [{"api_key": secret}]
    assert len(released) == 1


def test_auth_source_precedence_covers_every_pairwise_conflict(tmp_path) -> None:
    assert AUTH_SOURCE_PRECEDENCE == (
        "runtime",
        "config",
        "oauth",
        "login_api_key",
        "env",
        "stored_api_key",
        "fallback",
    )
    pairs = tuple(itertools.combinations(AUTH_SOURCE_PRECEDENCE, 2))
    assert len(pairs) == 21

    for index, pair in enumerate(pairs):
        broker, environment = _e4_precedence_broker(
            tmp_path / f"pair-{index}",
            set(pair),
        )
        expected = pair[0]
        origin = broker.get_credential_origin(
            "openai",
            environment_key="OPENAI_API_KEY",
            environment=environment,
        )

        assert origin is not None
        assert _e4_origin_leg(origin) == expected
        assert not any(
            secret in json.dumps(origin) for secret in _E4_SOURCE_KEYS.values()
        )
        if expected in {"oauth", "login_api_key", "stored_api_key"}:
            assert origin["account_id"].startswith("bbacct_")
            assert origin["credential_id"].startswith("bbcred_")
        else:
            assert "account_id" not in origin
            assert "credential_id" not in origin

        with broker.execution_material(
            "openai",
            environment_key="OPENAI_API_KEY",
            environment=environment,
        ) as material:
            assert material is not None
            assert material["api_key"] == _E4_SOURCE_KEYS[expected]
            assert material["credential_origin"] == origin
        assert material == {}


@pytest.mark.parametrize(
    "stored_source",
    ("oauth", "login_api_key", "stored_api_key"),
)
@pytest.mark.parametrize("state", ("missing", "disabled", "revoked"))
def test_inactive_stored_source_falls_through_to_environment(
    tmp_path,
    stored_source,
    state,
) -> None:
    enabled = {"env"} if state == "missing" else {"env", stored_source}
    broker, environment = _e4_precedence_broker(tmp_path, enabled)
    if state != "missing":
        credential = broker.listCredentials("openai")[0]
        action = broker.logout if state == "disabled" else broker.revoke
        assert action({"account_id": credential["account_id"]})["ok"] is True

    origin = broker.get_credential_origin(
        "openai",
        environment_key="OPENAI_API_KEY",
        environment=environment,
    )

    assert origin == {"kind": "env", "env_var": "OPENAI_API_KEY"}
    with broker.execution_material(
        "openai",
        environment_key="OPENAI_API_KEY",
        environment=environment,
    ) as material:
        assert material is not None
        assert material["api_key"] == _E4_SOURCE_KEYS["env"]
        assert material["credential_origin"] == origin


def test_override_removal_re_resolves_lower_sources(tmp_path) -> None:
    broker, environment = _e4_precedence_broker(
        tmp_path,
        {"runtime", "config", "env"},
    )

    def resolve():
        return broker.get_credential_origin(
            "openai",
            environment_key="OPENAI_API_KEY",
            environment=environment,
        )

    assert resolve() == {"kind": "runtime"}
    broker.remove_runtime_api_key("openai")
    assert resolve() == {"kind": "config"}
    broker.clear_config_api_keys()
    assert resolve() == {
        "kind": "env",
        "env_var": "OPENAI_API_KEY",
    }


def test_missing_auth_has_no_origin_or_execution_material(tmp_path) -> None:
    broker, environment = _e4_precedence_broker(tmp_path, set())

    assert (
        broker.get_credential_origin(
            "no-such-provider",
            environment_key="NO_SUCH_PROVIDER_API_KEY",
            environment=environment,
        )
        is None
    )
    with broker.execution_material(
        "no-such-provider",
        environment_key="NO_SUCH_PROVIDER_API_KEY",
        environment=environment,
    ) as material:
        assert material is None


def test_codex_auth_file_never_substitutes_openai_credentials(
    tmp_path,
) -> None:
    secret = "e4-codex-file-fallback-canary"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": secret}}),
        encoding="utf-8",
    )
    store = SQLiteCredentialStore(tmp_path / "credentials.sqlite3")
    broker = ProviderBroker(store, codex_auth_path=auth_path)

    assert broker.get_credential_origin("openai", environment={}) is None
    origin = broker.get_credential_origin("codex", environment={})
    assert origin == {
        "kind": "fallback",
        "source": "codex_auth_file",
    }
    assert secret not in json.dumps(origin)
    with broker.execution_material("codex", environment={}) as material:
        assert material is not None
        assert material["api_key"] == secret
        assert material["credential_origin"] == origin
    assert material == {}

    store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="stored-openai",
        source="stored",
        material={"api_key": "e4-stored-openai-canary"},
    )
    assert broker.get_credential_origin("openai", environment={})["source"] == "stored"


def test_fallback_cleanup_never_mutates_resolver_owned_material(tmp_path) -> None:
    owned_buffer = bytearray(b"resolver-owned-buffer")
    cached = {
        "api_key": "e4-resolver-owned-key-canary",
        "headers": {"X-Nested": {"value": "preserve"}},
        "routing": {"constraints": [{"region": "preserve"}]},
        "opaque": {"buffer": owned_buffer},
    }
    resolver_calls: list[str] = []

    def fallback(provider_id: str):
        resolver_calls.append(provider_id)
        return cached

    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "credentials.sqlite3"),
        fallback_resolver=fallback,
        fallback_origins={"openai": "resolver"},
    )

    assert broker.get_credential_origin("openai", environment={}) == {
        "kind": "fallback",
        "source": "resolver",
    }
    assert resolver_calls == []
    assert cached["headers"] == {"X-Nested": {"value": "preserve"}}
    assert cached["routing"] == {"constraints": [{"region": "preserve"}]}
    assert owned_buffer == b"resolver-owned-buffer"

    with broker.execution_material("openai", environment={}) as material:
        assert material is not None
        assert material["routing"] is not cached["routing"]
        assert material["opaque"] is not cached["opaque"]
    assert resolver_calls == ["openai"]
    assert material == {}
    assert cached["headers"] == {"X-Nested": {"value": "preserve"}}
    assert cached["routing"] == {"constraints": [{"region": "preserve"}]}
    assert owned_buffer == b"resolver-owned-buffer"


def test_explicit_remote_broker_configuration_never_falls_back_local(
    monkeypatch,
) -> None:
    endpoint = "https://remote-broker.example.test"
    token = "e4-remote-broker-token-canary"
    monkeypatch.setenv(REMOTE_BROKER_URL_ENV, endpoint)
    monkeypatch.setenv("BREADBOARD_AUTH_BROKER_TOKEN", token)

    with pytest.raises(ProviderBrokerConfigurationError) as failure:
        get_provider_broker()

    assert endpoint not in str(failure.value)
    assert token not in str(failure.value)


def test_explicit_account_selectors_persist_user_binding_and_override_implicit_sources(
    tmp_path,
) -> None:
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "first",
            "alias": "first-alias",
            "api_key": "e5-first-account-canary",
        }
    )
    selected = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "second",
            "alias": "second-alias",
            "api_key": "e5-second-account-canary",
        }
    )
    broker.set_runtime_api_key("openai", "e5-runtime-override-canary")
    selectors = (
        {"account_id": selected["account_id"]},
        {"credential_id": selected["credential_id"]},
        {"label": selected["label"]},
        {"alias": selected["alias"]},
    )

    for index, selector in enumerate(selectors):
        session_id = f"e5-explicit-{index}"
        with broker.execution_material(
            "openai",
            session_id=session_id,
            account_selector=selector,
        ) as material:
            assert material is not None
            assert material["api_key"] == "e5-second-account-canary"
            assert material["credential_origin"] == {
                "kind": "api_key",
                "account_id": selected["account_id"],
                "credential_id": selected["credential_id"],
                "source": "login",
                "binding_kind": "user",
                "binding_reason": "user_selected",
            }

        binding = broker.get_session_account_binding(session_id, "openai")
        assert binding is not None
        assert binding["account_id"] == selected["account_id"]
        assert binding["credential_id"] == selected["credential_id"]
        assert binding["binding_kind"] == "user"
        assert binding["availability"] == "active"
        assert not any(
            canary in json.dumps(binding)
            for canary in (
                "e5-first-account-canary",
                "e5-second-account-canary",
                "e5-runtime-override-canary",
            )
        )

        with broker.execution_material("openai", session_id=session_id) as material:
            assert material is not None
            assert material["api_key"] == "e5-second-account-canary"
        assert broker.clear_session_account_binding(session_id, "openai") is True
        with broker.execution_material("openai", session_id=session_id) as material:
            assert material is not None
            assert material["api_key"] == "e5-runtime-override-canary"


def test_deterministic_session_binding_survives_restart_and_secret_rotation(
    tmp_path,
) -> None:
    db = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(db))
    for index in range(3):
        broker.putApiKey(
            {
                "provider_id": "openai",
                "account_label": f"account-{index}",
                "api_key": f"e5-restart-account-{index}-canary",
            }
        )
    session_id = "e5-restart-session"

    with broker.execution_material("openai", session_id=session_id) as material:
        assert material is not None
        selected_account_id = material["account_id"]
        selected_label = material["label"]
        selected_origin = dict(material["credential_origin"])
    assert selected_origin["binding_kind"] == "default"
    assert selected_origin["binding_reason"] == "deterministic_default"
    history = {
        "session_id": session_id,
        "account_id": selected_account_id,
        "events": [{"type": "session.start"}],
    }
    history_hash = hashlib.sha256(
        json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    restarted = ProviderBroker(SQLiteCredentialStore(db))
    with restarted.execution_material("openai", session_id=session_id) as material:
        assert material is not None
        assert material["account_id"] == selected_account_id
        assert material["credential_origin"]["binding_kind"] == "default"

    restarted.putApiKey(
        {
            "provider_id": "openai",
            "account_id": selected_account_id,
            "account_label": selected_label,
            "api_key": "e5-rotated-secret-canary",
        }
    )
    after_rotation = ProviderBroker(SQLiteCredentialStore(db))
    with after_rotation.execution_material("openai", session_id=session_id) as material:
        assert material is not None
        assert material["account_id"] == selected_account_id
        assert material["api_key"] == "e5-rotated-secret-canary"
    assert (
        hashlib.sha256(
            json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == history_hash
    )


def test_default_binding_rotates_only_when_bound_account_becomes_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.store as store_module

    db = tmp_path / "credentials.sqlite3"
    store = SQLiteCredentialStore(db)
    broker = ProviderBroker(store)
    base_ms = store_module.now_ms()
    first = store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="first",
        source="login",
        expires_at_ms=base_ms + 10_000,
        material={"api_key": "e5-unavailable-first-canary"},
    )
    session_id = "e5-automatic-session"
    with broker.execution_material("openai", session_id=session_id) as material:
        assert material["account_id"] == first["account_id"]

    second = store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="second",
        source="login",
        material={"api_key": "e5-unavailable-second-canary"},
    )
    assert broker.logout({"account_id": first["account_id"]})["ok"] is True
    with broker.execution_material("openai", session_id=session_id) as material:
        assert material["account_id"] == second["account_id"]
        assert material["credential_origin"]["binding_kind"] == "automatic"
        assert (
            material["credential_origin"]["binding_reason"]
            == "bound_account_unavailable"
        )

    third = store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="third",
        source="login",
        material={"api_key": "e5-unavailable-third-canary"},
    )
    assert broker.revoke({"account_id": second["account_id"]})["ok"] is True
    with broker.execution_material("openai", session_id=session_id) as material:
        assert material["account_id"] == third["account_id"]

    store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="third",
        account_id=third["account_id"],
        source="login",
        expires_at_ms=base_ms + 1_000,
        material={"api_key": "e5-expiring-third-canary"},
    )
    fourth = store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="fourth",
        source="login",
        material={"api_key": "e5-unavailable-fourth-canary"},
    )
    monkeypatch.setattr(store_module, "now_ms", lambda: base_ms + 2_000)
    with broker.execution_material("openai", session_id=session_id) as material:
        assert material["account_id"] == fourth["account_id"]
        assert material["credential_origin"]["binding_kind"] == "automatic"


def test_rate_limit_rotates_default_binding_but_not_user_binding(tmp_path) -> None:
    from breadboard_engine.provider.contracts import ProviderRuntimeError

    automatic_db = tmp_path / "automatic.sqlite3"
    automatic = ProviderBroker(SQLiteCredentialStore(automatic_db))
    automatic_accounts = [
        automatic.putApiKey(
            {
                "provider_id": "openai",
                "account_label": f"automatic-{index}",
                "api_key": f"e5-rate-automatic-{index}-canary",
            }
        )
        for index in range(2)
    ]
    session_id = "e5-rate-automatic"
    with pytest.raises(ProviderRuntimeError):
        with automatic.execution_material("openai", session_id=session_id) as material:
            blocked_account_id = material["account_id"]
            raise ProviderRuntimeError(
                "rate limited",
                details={
                    "classification": "rate_limited",
                    "status_code": 429,
                    "retry_after": 300,
                },
            )
    automatic = ProviderBroker(SQLiteCredentialStore(automatic_db))
    with automatic.execution_material("openai", session_id=session_id) as material:
        assert material is not None
        assert material["account_id"] != blocked_account_id
        replacement_account_id = material["account_id"]
        assert material["credential_origin"]["binding_kind"] == "automatic"
    assert {item["account_id"] for item in automatic_accounts} == {
        blocked_account_id,
        replacement_account_id,
    }

    user = ProviderBroker(SQLiteCredentialStore(tmp_path / "user.sqlite3"))
    selected = user.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "selected",
            "api_key": "e5-rate-user-selected-canary",
        }
    )
    replacement = user.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "replacement",
            "api_key": "e5-rate-user-replacement-canary",
        }
    )
    with pytest.raises(ProviderRuntimeError):
        with user.execution_material(
            "openai",
            session_id="e5-rate-user",
            account_selector={"account_id": selected["account_id"]},
        ):
            raise ProviderRuntimeError(
                "rate limited",
                details={
                    "classification": "rate_limited",
                    "status_code": 429,
                    "retry_after": 300,
                },
            )
    with user.execution_material("openai", session_id="e5-rate-user") as material:
        assert material is None
    binding = user.get_session_account_binding("e5-rate-user", "openai")
    assert binding["account_id"] == selected["account_id"]
    assert binding["binding_kind"] == "user"
    assert binding["availability"] == "rate_limited"

    rebound = user.bind_session_account(
        "e5-rate-user",
        "openai",
        {"credential_id": replacement["credential_id"]},
    )
    assert rebound is not None
    assert rebound["account_id"] == replacement["account_id"]
    with user.execution_material("openai", session_id="e5-rate-user") as material:
        assert material["api_key"] == "e5-rate-user-replacement-canary"


@pytest.mark.parametrize(
    "details",
    (
        {"classification": "rate_limited", "retry_after": 300},
        {"status_code": 429, "retry_after": 300},
        {"classification": "rate_limited", "status_code": 200, "retry_after": 300},
    ),
)
def test_account_rate_limit_requires_classified_http_429(details) -> None:
    from breadboard_engine.provider.contracts import ProviderRuntimeError

    error = ProviderRuntimeError("not a classified 429", details=details)
    assert ProviderBroker._rate_limit_deadline_ms(error) is None


def test_concurrent_sessions_keep_independent_durable_account_bindings(
    tmp_path,
) -> None:
    db = tmp_path / "credentials.sqlite3"
    seed = ProviderBroker(SQLiteCredentialStore(db))
    for index in range(3):
        seed.putApiKey(
            {
                "provider_id": "openai",
                "account_label": f"concurrent-{index}",
                "api_key": f"e5-concurrent-{index}-canary",
            }
        )
    session_ids = [f"e5-concurrent-session-{index}" for index in range(12)]
    results: dict[str, list[str]] = {}
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def resolve_twice(session_id: str) -> None:
        try:
            broker = ProviderBroker(SQLiteCredentialStore(db))
            account_ids = []
            for _ in range(2):
                with broker.execution_material(
                    "openai",
                    session_id=session_id,
                ) as material:
                    account_ids.append(material["account_id"])
            with result_lock:
                results[session_id] = account_ids
        except BaseException as error:
            with result_lock:
                failures.append(error)

    threads = [
        threading.Thread(target=resolve_twice, args=(session_id,))
        for session_id in session_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures
    assert set(results) == set(session_ids)
    assert all(len(set(account_ids)) == 1 for account_ids in results.values())
    assert len({account_ids[0] for account_ids in results.values()}) > 1
    restarted = ProviderBroker(SQLiteCredentialStore(db))
    for session_id, account_ids in results.items():
        binding = restarted.get_session_account_binding(session_id, "openai")
        assert binding is not None
        assert binding["account_id"] == account_ids[0]
        assert binding["binding_kind"] == "default"


def test_execution_material_clears_after_release_failure(tmp_path, monkeypatch):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey(
        {
            "provider_id": "anthropic",
            "account_label": "failure",
            "api_key": "e3-release-failure-canary",
            "headers": {"X-Credential": "e3-release-header-canary"},
        }
    )
    release = broker.store.release_lease

    def release_then_fail(lease_id):
        assert release(lease_id) is True
        raise RuntimeError("release failed")

    monkeypatch.setattr(broker.store, "release_lease", release_then_fail)
    with pytest.raises(RuntimeError, match="release failed"):
        with broker.execution_material("anthropic") as material:
            assert material["api_key"] == "e3-release-failure-canary"
    assert material == {}
    assert redaction.iter_registered_secret_values() == ()


def test_rotation_preserves_session_history_hashes(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    first = broker.putApiKey(
        {"provider_id": "openai", "account_label": "rotate", "api_key": "sk-old-secret"}
    )
    history = {
        "session_id": "session-rotate",
        "lock": {
            "lock_id": "lock-1",
            "model": "openai/gpt-5.4-mini",
            "account_id": first["account_id"],
        },
        "graph": {"nodes": ["prompt", "provider"], "edges": [["prompt", "provider"]]},
        "events": [{"type": "session.start", "lock_id": "lock-1"}],
    }
    encoded = json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
    lock_hash = hashlib.sha256(
        json.dumps(history["lock"], sort_keys=True).encode()
    ).hexdigest()
    graph_hash = hashlib.sha256(
        json.dumps(history["graph"], sort_keys=True).encode()
    ).hexdigest()
    broker.putApiKey(
        {"provider_id": "openai", "account_label": "rotate", "api_key": "sk-new-secret"}
    )
    assert (
        hashlib.sha256(
            json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
        ).digest()
        == hashlib.sha256(encoded).digest()
    )
    assert (
        hashlib.sha256(json.dumps(history["lock"], sort_keys=True).encode()).hexdigest()
        == lock_hash
    )
    assert (
        hashlib.sha256(
            json.dumps(history["graph"], sort_keys=True).encode()
        ).hexdigest()
        == graph_hash
    )
    with broker.execution_material("openai") as material:
        assert material and material["api_key"] == "sk-new-secret"


def test_broker_audit_events_are_scrubbed(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    secret = "sk-audit-canary-secret"
    credential = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "audit",
            "api_key": secret,
            "headers": {"Authorization": f"Bearer {secret}"},
        }
    )
    broker.logout({"account_id": credential["account_id"]})
    events = broker.audit_events()
    assert events
    assert secret not in json.dumps(events)
    assert all("api_key" not in event for event in events)


def test_secure_creation_ignores_permissive_umask(tmp_path):
    db = tmp_path / "nested" / "credentials.sqlite3"
    repository = pathlib.Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(repository), existing_pythonpath) if part
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; os.umask(0); "
            "from breadboard_engine.provider_broker import SQLiteCredentialStore; "
            "SQLiteCredentialStore(sys.argv[1])",
            str(db),
        ],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert stat.S_IMODE(os.stat(db.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600


def test_existing_database_mode_repair_preserves_data(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o755)
    db = state_dir / "credentials.sqlite3"
    original = SQLiteCredentialStore(db)
    original.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="preserved",
        material={"api_key": "preserved-secret"},
    )
    before = original.inspect_accounts()
    os.chmod(state_dir, 0o755)
    os.chmod(db, 0o400)

    restarted = SQLiteCredentialStore(db)

    assert stat.S_IMODE(os.stat(state_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
    assert restarted.inspect_accounts() == before
    lease = restarted.acquire_lease(
        provider_id="openai",
        session_id="mode-repair",
        endpoint_id="test",
    )
    assert lease and lease["api_key"] == "preserved-secret"


def test_parent_and_database_symlinks_are_refused(tmp_path):
    import breadboard_engine.provider_broker.store as store_module

    actual = tmp_path / "actual"
    actual.mkdir()
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(store_module._StoragePathError):
        SQLiteCredentialStore(parent_link / "credentials.sqlite3")

    target = actual / "target.sqlite3"
    SQLiteCredentialStore(target)
    database_link = actual / "database-link.sqlite3"
    database_link.symlink_to(target)
    with pytest.raises(store_module._StoragePathError):
        SQLiteCredentialStore(database_link)


def test_group_writable_owned_ancestor_is_refused(tmp_path):
    import breadboard_engine.provider_broker.store as store_module

    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o775)
    os.chmod(unsafe_parent, 0o775)
    with pytest.raises(store_module._StoragePathError) as failure:
        SQLiteCredentialStore(unsafe_parent / "state" / "credentials.sqlite3")
    assert failure.value.operation == "ancestor-mode"
    assert failure.value.path.endswith("unsafe-parent")


def test_non_regular_and_hardlinked_databases_are_refused(tmp_path):
    import breadboard_engine.provider_broker.store as store_module

    fifo = tmp_path / "credentials.fifo"
    os.mkfifo(fifo)
    with pytest.raises(store_module._StoragePathError):
        SQLiteCredentialStore(fifo)

    real = tmp_path / "real.sqlite3"
    SQLiteCredentialStore(real)
    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(real, hardlink)
    with pytest.raises(store_module._StoragePathError):
        SQLiteCredentialStore(hardlink)


def test_unsupported_path_operations_use_typed_secret_free_error(tmp_path, monkeypatch):
    import breadboard_engine.provider_broker.store as store_module

    def unsupported_fchmod(fd, mode):
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr(store_module.os, "fchmod", unsupported_fchmod)
    with pytest.raises(store_module._StoragePathError) as failure:
        SQLiteCredentialStore(tmp_path / "state" / "credentials.sqlite3")
    assert failure.value.operation == "directory-mode"
    assert failure.value.path.endswith("state")
    assert failure.value.args == ("directory-mode", failure.value.path)
    assert failure.value.__cause__ is not None
    assert failure.value.__cause__.errno == errno.ENOTSUP


def test_wal_auxiliaries_are_hardened_during_concurrent_access(tmp_path):
    db = tmp_path / "credentials.sqlite3"

    stores = [SQLiteCredentialStore(db), SQLiteCredentialStore(db)]
    reader = sqlite3.connect(str(db), timeout=30.0)
    reader.execute("PRAGMA journal_mode = WAL")
    reader.execute("BEGIN")
    failures = []

    def write_credential(index):
        try:
            stores[index % len(stores)].put_api_key(
                provider_id="openai",
                auth_scheme_id="api_key",
                label=f"concurrent-{index}",
                material={"api_key": f"concurrent-secret-{index}"},
            )
        except BaseException as error:
            failures.append(error)

    threads = [
        threading.Thread(target=write_credential, args=(index,)) for index in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures

    assert len(SQLiteCredentialStore(db).list_accounts("openai")) == 8

    for auxiliary in (db, pathlib.Path(f"{db}-wal"), pathlib.Path(f"{db}-shm")):
        if auxiliary.exists():
            metadata = os.stat(auxiliary)
            assert stat.S_ISREG(metadata.st_mode)
            assert metadata.st_nlink == 1
            assert stat.S_IMODE(metadata.st_mode) == 0o600
    reader.rollback()
    reader.close()


def test_rollback_journal_is_hardened(tmp_path) -> None:
    db = tmp_path / "credentials.sqlite3"
    store = SQLiteCredentialStore(db)
    journal = pathlib.Path(f"{db}-journal")
    journal.write_bytes(b"")
    os.chmod(journal, 0o666)

    store._harden_database_files()

    metadata = journal.stat()
    assert stat.S_ISREG(metadata.st_mode)
    assert metadata.st_nlink == 1
    assert stat.S_IMODE(metadata.st_mode) == 0o600


def test_wal_hardening_retries_an_unlinked_auxiliary_fd(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.store as store_module

    db = tmp_path / "credentials.sqlite3"
    store = SQLiteCredentialStore(db)
    wal = pathlib.Path(f"{db}-wal")
    wal.write_bytes(b"")
    os.chmod(wal, 0o666)
    original_fstat = store_module._fstat
    raced = False

    def race_once(fd, operation, path):
        nonlocal raced
        metadata = original_fstat(fd, operation, path)
        if not raced and operation == "stat-database-auxiliary" and path == str(wal):
            raced = True
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_uid=metadata.st_uid,
                st_nlink=0,
            )
        return metadata

    monkeypatch.setattr(store_module, "_fstat", race_once)
    store._harden_database_file(wal.name, str(wal))

    assert raced is True
    assert stat.S_IMODE(wal.stat().st_mode) == 0o600


def test_metadata_inspection_and_listing_never_include_secret_material(tmp_path):
    store = SQLiteCredentialStore(tmp_path / "credentials.sqlite3")
    secret = "metadata-secret-must-not-escape"
    store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="metadata",
        material={"api_key": secret},
    )

    inspected = json.dumps(store.inspect_accounts())
    listed = json.dumps(store.list_accounts())
    assert secret not in inspected
    assert secret not in listed


def test_logout_is_reversible_but_revoke_tombstone_cannot_be_reactivated(
    tmp_path,
) -> None:
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    original = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "lifecycle",
            "api_key": "lifecycle-secret-old",
        }
    )

    assert broker.logout({"account_id": original["account_id"]}) == {
        "ok": True,
        "disabled": 1,
    }
    logout_event = broker.audit_events()[-1]
    assert (
        logout_event["action"],
        logout_event["secret_disposition"],
        logout_event["tombstone"],
    ) == ("disable", "retained", False)

    reactivated = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "lifecycle",
            "account_id": original["account_id"],
            "api_key": "lifecycle-secret-reactivated",
        }
    )
    assert reactivated["account_id"] == original["account_id"]
    assert reactivated["status"] == "active"

    assert broker.revoke({"account_id": original["account_id"]}) == {
        "ok": True,
        "revoked": 1,
    }
    revoke_event = broker.audit_events()[-1]
    assert (
        revoke_event["action"],
        revoke_event["secret_disposition"],
        revoke_event["tombstone"],
    ) == ("revoke", "revoked", True)
    with pytest.raises(ValueError, match="revoked account cannot be reactivated"):
        broker.putApiKey(
            {
                "provider_id": "openai",
                "account_label": "lifecycle",
                "account_id": original["account_id"],
                "api_key": "lifecycle-secret-forbidden",
            }
        )

    replacement = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "lifecycle",
            "api_key": "lifecycle-secret-new-login",
        }
    )
    assert replacement["account_id"] != original["account_id"]
    views = broker.listCredentials("openai")
    assert {item["status"] for item in views} == {"active", "revoked"}


def test_api_key_and_oauth_rotation_delete_superseded_secret_rows(tmp_path):
    store = SQLiteCredentialStore(tmp_path / "rotation-cleanup.sqlite3")
    broker = ProviderBroker(store)
    old_api = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "rotation",
            "api_key": "rotation-api-old-canary",
        }
    )
    new_api = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "rotation",
            "api_key": "rotation-api-new-canary",
        }
    )
    old_oauth = store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="rotation",
        expires_at_ms=1_800_000_000_000,
        material={
            "access_token": "rotation-oauth-old-canary",
            "refresh_token": "rotation-refresh-old-canary",
        },
    )
    new_oauth = store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="rotation",
        expires_at_ms=1_800_000_000_000,
        material={
            "access_token": "rotation-oauth-new-canary",
            "refresh_token": "rotation-refresh-new-canary",
        },
    )

    assert new_api["account_id"] == old_api["account_id"]
    assert new_oauth["account_id"] == old_oauth["account_id"]
    with store._transaction() as connection:
        rows = connection.execute(
            """SELECT account_id, material, revoked_at_ms
               FROM secrets ORDER BY account_id, secret_version"""
        ).fetchall()
    assert len(rows) == 2
    by_account = {str(row["account_id"]): row for row in rows}
    assert set(by_account) == {new_api["account_id"], new_oauth["account_id"]}
    assert all(row["revoked_at_ms"] is None for row in rows)
    materials = json.dumps([row["material"] for row in rows])
    assert "rotation-api-old-canary" not in materials
    assert "rotation-oauth-old-canary" not in materials
    assert "rotation-refresh-old-canary" not in materials
    assert "rotation-api-new-canary" in materials
    assert "rotation-oauth-new-canary" in materials
    assert "rotation-refresh-new-canary" in materials


def test_local_revoke_deletes_secrets_but_retains_account_and_refresh_tombstones(
    tmp_path,
):
    store = SQLiteCredentialStore(tmp_path / "revoke-cleanup.sqlite3")
    broker = ProviderBroker(store)
    api_key = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "revoke-api",
            "api_key": "revoke-api-canary",
        }
    )
    oauth = store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="revoke-oauth",
        expires_at_ms=1_800_000_000_000,
        material={
            "access_token": "revoke-oauth-canary",
            "refresh_token": "revoke-refresh-canary",
        },
    )
    assert (
        store.claim_oauth_refresh(
            account_id=oauth["account_id"],
            expected_secret_version=oauth["secret_version"],
            owner_id="revoke-refresh-owner",
            lease_duration_ms=30_000,
        )["status"]
        == "acquired"
    )

    assert broker.revoke({"account_id": api_key["account_id"]})["ok"] is True
    assert broker.revoke({"account_id": oauth["account_id"]})["ok"] is True

    views = {item["account_id"]: item for item in broker.listCredentials()}
    assert views[api_key["account_id"]]["status"] == "revoked"
    assert views[oauth["account_id"]]["status"] == "revoked"
    refresh_state = store.inspect_refresh_state(oauth["account_id"])
    assert refresh_state["status"] == "idle"
    assert "updated_at_ms" in refresh_state
    with store._transaction() as connection:
        rows = connection.execute(
            "SELECT account_id, material FROM secrets WHERE account_id IN (?, ?)",
            (api_key["account_id"], oauth["account_id"]),
        ).fetchall()
    assert rows == []


def test_initialization_purges_legacy_revoked_secret_rows(tmp_path):
    db = tmp_path / "legacy-revoked.sqlite3"
    store = SQLiteCredentialStore(db)
    credential = store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="legacy-revoked",
        material={"api_key": "legacy-revoked-canary"},
    )
    with store._transaction() as connection:
        connection.execute(
            "UPDATE accounts SET status = 'revoked' WHERE account_id = ?",
            (credential["account_id"],),
        )
        connection.execute(
            """UPDATE secrets SET revoked_at_ms = ?
               WHERE account_id = ?""",
            (1_700_000_000_000, credential["account_id"]),
        )

    restarted = SQLiteCredentialStore(db)

    account = next(
        item
        for item in restarted.inspect_accounts()
        if item["account_id"] == credential["account_id"]
    )
    assert account["status"] == "revoked"
    with restarted._transaction() as connection:
        rows = connection.execute(
            "SELECT material FROM secrets WHERE account_id = ?",
            (credential["account_id"],),
        ).fetchall()
    assert rows == []


def test_initialization_clears_legacy_oauth_flows_and_expires_stale_pending(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.store as store_module

    db = tmp_path / "legacy-login-sessions.sqlite3"
    seed = SQLiteCredentialStore(db)
    current_ms = 1_800_000_000_000
    stale_created_ms = current_ms - store_module._LOGIN_EXPIRY_MS - 1
    fresh_created_ms = current_ms - 1_000
    canary = "legacy-oauth-flow-canary-e7"
    with seed._transaction() as connection:
        connection.executemany(
            """INSERT INTO login_sessions
               (login_session_id, provider_id, status, created_at_ms,
                updated_at_ms, expires_at_ms, problem_json, flow_json)
               VALUES (?, 'openai', ?, ?, ?, NULL, '{}', ?)""",
            (
                (
                    "legacy-completed",
                    "completed",
                    stale_created_ms,
                    stale_created_ms,
                    json.dumps({"pkce_verifier": canary}),
                ),
                (
                    "legacy-stale",
                    "pending",
                    stale_created_ms,
                    stale_created_ms,
                    json.dumps({"pkce_verifier": canary}),
                ),
                (
                    "legacy-fresh",
                    "pending",
                    fresh_created_ms,
                    fresh_created_ms,
                    json.dumps({"pkce_verifier": "fresh-flow"}),
                ),
            ),
        )
    monkeypatch.setattr(store_module, "now_ms", lambda: current_ms)

    restarted = SQLiteCredentialStore(db)

    completed = restarted.get_login("legacy-completed", include_flow=True)
    stale = restarted.get_login("legacy-stale", include_flow=True)
    fresh = restarted.get_login("legacy-fresh", include_flow=True)
    assert completed["status"] == "completed"
    assert completed["flow"] == {}
    assert stale["status"] == "expired"
    assert stale["problem"]["code"] == "oauth_login_expired"
    assert stale["flow"] == {}
    assert fresh["status"] == "pending"
    assert fresh["expires_at_ms"] == (fresh_created_ms + store_module._LOGIN_EXPIRY_MS)
    assert fresh["flow"] == {"pkce_verifier": "fresh-flow"}
    with sqlite3.connect(db) as connection:
        serialized_rows = json.dumps(
            connection.execute(
                "SELECT flow_json, problem_json FROM login_sessions"
            ).fetchall()
        )
    assert canary not in serialized_rows


def test_audit_events_are_durable_and_use_fixed_secret_free_fields(
    tmp_path, monkeypatch
):
    import breadboard_engine.provider_broker.broker as broker_module
    import breadboard_engine.provider_broker.store as store_module

    occurred_at_ms = 1_700_123_456_789
    monkeypatch.setattr(
        broker_module.time,
        "time_ns",
        lambda: occurred_at_ms * 1_000_000,
    )
    monkeypatch.setattr(
        store_module.time,
        "time",
        lambda: occurred_at_ms / 1000,
    )
    monkeypatch.setattr(store_module, "now_ms", lambda: occurred_at_ms)
    secret = "durable-audit-secret-canary"
    db = tmp_path / "durable-audit.sqlite3"
    first = ProviderBroker(SQLiteCredentialStore(db))
    first.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "durable-audit",
            "api_key": secret,
            "headers": {"Authorization": f"Bearer {secret}"},
        }
    )

    restarted = ProviderBroker(SQLiteCredentialStore(db))
    events = restarted.audit_events()
    stored = next(event for event in events if event["event"] == "credential_stored")
    assert stored["event_id"]
    assert stored["occurred_at_ms"] == occurred_at_ms
    assert stored["actor"] == "local_process"
    assert stored["origin"] == "provider_broker"
    assert stored["outcome"] == "success"
    assert secret not in json.dumps(events)


def test_audit_events_preserve_insertion_order_when_timestamps_collide(tmp_path):
    store = SQLiteCredentialStore(tmp_path / "audit-order.sqlite3")
    store.append_audit_event(
        {"event_id": "evt-z", "event": "first", "occurred_at_ms": 1_700_000_000_000}
    )
    store.append_audit_event(
        {"event_id": "evt-a", "event": "second", "occurred_at_ms": 1_700_000_000_000}
    )

    assert [event["event"] for event in store.list_audit_events()] == [
        "first",
        "second",
    ]


def test_audit_append_failure_raises_typed_secret_free_error(tmp_path, monkeypatch):
    import breadboard_engine.provider_broker.broker as broker_module

    secret = "audit-persistence-failure-canary"
    store = SQLiteCredentialStore(tmp_path / "audit-failure.sqlite3")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError(f"durable audit failed for {secret}")

    monkeypatch.setattr(store, "append_audit_event", fail_append, raising=False)
    broker = ProviderBroker(store)
    with pytest.raises(broker_module.CredentialAuditPersistenceError) as failure:
        broker.putApiKey(
            {
                "provider_id": "openai",
                "account_label": "audit-failure",
                "api_key": secret,
            }
        )

    assert secret not in str(failure.value)
    assert secret not in repr(failure.value)
    assert store.inspect_accounts() == []
    assert broker.audit_events() == []
    with store._transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["logout", "revoke"])
def test_audit_append_failure_rolls_back_existing_credential_mutation(
    tmp_path,
    monkeypatch,
    mutation,
):
    import breadboard_engine.provider_broker.broker as broker_module

    store = SQLiteCredentialStore(tmp_path / f"{mutation}-audit-failure.sqlite3")
    broker = ProviderBroker(store)
    credential = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": f"{mutation}-audit-failure",
            "api_key": "existing-audit-rollback-canary",
        }
    )

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("durable audit unavailable")

    monkeypatch.setattr(store, "append_audit_event", fail_append, raising=False)
    with pytest.raises(broker_module.CredentialAuditPersistenceError):
        getattr(broker, mutation)({"account_id": credential["account_id"]})

    [account] = store.inspect_accounts("openai")
    assert account["status"] == "active"
    with store._transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM secrets WHERE account_id = ?",
                (credential["account_id"],),
            ).fetchone()[0]
            == 1
        )
    assert [event["event"] for event in broker.audit_events()] == ["credential_stored"]
