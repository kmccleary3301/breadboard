from __future__ import annotations

import json

import pytest

from breadboard_engine.provider.routing import ProviderRouter


def test_openai_environment_key_is_broker_resolved_and_scoped(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore
    from breadboard_engine.security import redaction

    redaction.clear_registered_secret_values()
    api_key = "e4-env-canary"
    ignored_header = "Bearer e4-legacy-overlay-canary"
    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    monkeypatch.setenv(
        "BREADBOARD_OPENAI_AUTH_BASE_URL",
        "https://legacy-overlay.example.test/v1",
    )
    monkeypatch.setenv(
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
        json.dumps({"Authorization": ignored_header}),
    )
    monkeypatch.setattr(
        broker_module,
        "_default_broker",
        ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3")),
    )

    router = ProviderRouter()
    metadata = router.create_client_config("openai/gpt-5.4-mini")
    assert metadata["api_key"] is None
    assert api_key not in json.dumps(metadata)
    assert router.get_credential_origin("openai/gpt-5.4-mini") == {
        "kind": "env",
        "env_var": "OPENAI_API_KEY",
    }

    with router.execution_client_config(
        "openai/gpt-5.4-mini",
    ) as client_config:
        assert client_config["api_key"] == api_key
        assert client_config["credential_origin"] == {
            "kind": "env",
            "env_var": "OPENAI_API_KEY",
        }
        assert "base_url" not in client_config
        assert "default_headers" not in client_config
        assert api_key in redaction.iter_registered_secret_values()
        assert ignored_header not in redaction.iter_registered_secret_values()
    assert client_config == {}
    with pytest.raises(RuntimeError, match="provider call failed") as error:
        with router.execution_client_config(
            "openai/gpt-5.4-mini",
        ) as error_config:
            raise RuntimeError(f"provider call failed {api_key}")
    assert error_config == {}
    assert api_key not in str(error.value)
    assert redaction.iter_registered_secret_values() == ()


def test_stored_alternate_credentials_are_scoped_through_provider_errors(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore
    from breadboard_engine.security import redaction

    redaction.clear_registered_secret_values()
    header_secret = "prefixed-header-secret"
    short_header_secret = "a"
    encoded_url_secret = "url%2Dsecret"
    decoded_url_secret = "url-secret"
    numeric_secret = "48273195"
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "alternate",
            "api_key": "primary-secret-material",
            "headers": {
                "X-Authorization": f"Bearer {header_secret}",
                "X-Custom": short_header_secret,
            },
            "base_url": (f"https://url-user:{encoded_url_secret}@example.test/v1"),
            "routing": {"access_token": int(numeric_secret)},
        }
    )
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    router = ProviderRouter()

    with pytest.raises(RuntimeError) as error:
        with router.execution_client_config("openai/gpt-5.4-mini"):
            assert {
                header_secret,
                short_header_secret,
                encoded_url_secret,
                decoded_url_secret,
                numeric_secret,
            } <= set(redaction.iter_registered_secret_values())
            raise RuntimeError(
                (
                    f"provider call failed {short_header_secret} "
                    f"{header_secret} {encoded_url_secret} "
                    f"{decoded_url_secret} {numeric_secret}"
                )
            )

    for secret in (
        header_secret,
        short_header_secret,
        encoded_url_secret,
        decoded_url_secret,
        numeric_secret,
    ):
        assert secret not in str(error.value)
    assert redaction.iter_registered_secret_values() == ()


def test_config_override_wins_and_applies_endpoint_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore

    environment_key = "e4-lower-env-canary"
    config_key = "e4-config-override-canary"
    config_header = "Bearer e4-config-header-canary"
    monkeypatch.setenv("OPENAI_API_KEY", environment_key)
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "credentials.sqlite3"),
    )
    broker.set_config_api_key(
        "openai",
        config_key,
        base_url="https://gateway.example.test/v1",
        headers={"Authorization": config_header},
    )
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    router = ProviderRouter()

    assert router.get_credential_origin("openai/gpt-5.4-mini") == {
        "kind": "config",
    }
    with router.execution_client_config(
        "openai/gpt-5.4-mini",
    ) as client_config:
        assert client_config["api_key"] == config_key
        assert client_config["base_url"] == "https://gateway.example.test/v1"
        assert client_config["default_headers"]["Authorization"] == config_header
        assert client_config["credential_origin"] == {"kind": "config"}
        assert environment_key not in json.dumps(client_config)
    assert client_config == {}


def test_router_session_id_reuses_durable_account_binding_after_broker_restart(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore

    db = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(db))
    for index in range(2):
        broker.putApiKey(
            {
                "provider_id": "openai",
                "account_label": f"router-{index}",
                "api_key": f"e5-router-{index}-canary",
            }
        )
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    router = ProviderRouter()
    session_id = "e5-router-session"

    predicted = router.get_credential_origin(
        "openai/gpt-5.4-mini",
        session_id=session_id,
    )
    assert predicted is not None
    assert predicted["account_id"].startswith("bbacct_")
    assert "binding_kind" not in predicted
    with router.execution_client_config(
        "openai/gpt-5.4-mini",
        session_id=session_id,
    ) as client_config:
        bound_origin = dict(client_config["credential_origin"])
    assert bound_origin["account_id"] == predicted["account_id"]
    assert bound_origin["binding_kind"] == "default"

    monkeypatch.setattr(
        broker_module,
        "_default_broker",
        ProviderBroker(SQLiteCredentialStore(db)),
    )
    with router.execution_client_config(
        "openai/gpt-5.4-mini",
        session_id=session_id,
    ) as client_config:
        assert client_config["credential_origin"] == bound_origin


def test_codex_execution_lease_scopes_broker_access_token(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore

    secret = "codex-router-access-token"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": secret}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        broker_module,
        "_default_broker",
        ProviderBroker(
            SQLiteCredentialStore(tmp_path / "credentials.sqlite3"),
            codex_auth_path=auth_path,
        ),
    )
    router = ProviderRouter()

    with router.execution_client_config("codex/gpt-5.4") as client_config:
        assert client_config["access_token"] == secret
        assert client_config["credential_origin"] == {
            "kind": "fallback",
            "source": "codex_auth_file",
        }
    assert client_config == {}


@pytest.mark.parametrize(
    ("route", "api_key", "source"),
    (("mock/dev", "mock", "synthetic"),),
)
def test_provider_managed_exceptions_do_not_consult_broker(
    route,
    api_key,
    source,
    monkeypatch,
) -> None:
    from breadboard_engine.provider_broker import REMOTE_BROKER_URL_ENV

    monkeypatch.setenv(
        REMOTE_BROKER_URL_ENV,
        "https://unavailable-broker.example.test",
    )
    router = ProviderRouter()

    assert router.get_credential_origin(route) == {
        "kind": "fallback",
        "source": source,
    }
    with router.execution_client_config(route) as client_config:
        assert client_config["api_key"] == api_key
        assert client_config["credential_origin"] == {
            "kind": "fallback",
            "source": source,
        }
    assert client_config == {}
