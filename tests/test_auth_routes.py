from __future__ import annotations

import json
import sqlite3
import pytest

from fastapi.testclient import TestClient

from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore
from breadboard_engine.security import redaction


def _roles() -> dict:
    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {
            "role": "default",
            "known_but_unbound_role": "error",
            "unknown_role": "error",
        },
        "roles": {
            "default": {
                "primary": {
                    "provider_id": "openai",
                    "model_id": "gpt-5.4-mini",
                    "account_selector": {"mode": "default", "pin": "lock"},
                },
                "fallbacks": [],
                "fallback_on": [],
            }
        },
        "dispatch": {"subagents": {}, "lanes": {}},
    }


def test_auth_routes_are_typed_and_never_return_secret(tmp_path, monkeypatch, caplog):
    import breadboard_engine.provider_broker.broker as broker_module

    secret = "opaque-credential-canary-7q9mx"
    database = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(database))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app())

    providers = client.get("/v1/auth/providers")
    assert providers.status_code == 200
    provider_rows = providers.json()
    assert [item["provider_id"] for item in provider_rows] == [
        "codex",
        "openai",
        "anthropic",
        "openrouter",
    ]
    assert provider_rows[0]["auth_schemes"] == ["provider_managed"]
    assert provider_rows[0]["availability_reason"] == "provider_managed"
    assert all(item["support_tier"] == "core" for item in provider_rows)

    stored = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={
            "api_key": secret,
            "auth_scheme_id": "api_key",
            "alias": "primary",
            "headers": {"Authorization": f"Bearer {secret}"},
            "metadata": {
                "description": secret,
                "nested": {"note": secret},
            },
        },
    )
    assert stored.status_code == 200
    credential = stored.json()
    assert credential["auth_scheme_id"] == "api_key"
    assert credential["label"] == "main"
    assert credential["alias"] == "primary"
    assert credential["provider_id"] == "openai"
    assert credential["refresh_state"]["status"] == "idle"
    assert credential["refresh_state"]["retry_not_before_ms"] is None
    assert credential["metadata"] == {
        "description": "***REDACTED***",
        "nested": {"note": "***REDACTED***"},
    }
    assert secret not in stored.text

    listed = client.get("/v1/auth/credentials", params={"provider_id": "openai"})
    assert listed.status_code == 200
    assert listed.json()[0]["refresh_state"]["status"] == "idle"
    assert secret not in listed.text
    with sqlite3.connect(database) as connection:
        metadata_json = connection.execute(
            "SELECT metadata_json FROM accounts WHERE account_id = ?",
            (credential["account_id"],),
        ).fetchone()[0]
    assert secret not in metadata_json
    assert json.loads(metadata_json) == credential["metadata"]

    role = client.post(
        "/v1/model-roles/resolve",
        json={
            "model_roles": _roles(),
            "model_catalog": [
                {
                    "id": "gpt-5.4-mini",
                    "provider": "openai",
                    "canonical_provider": "openai",
                    "support_tier": "core",
                    "available": True,
                    "discovery": "configured_only",
                    "source": "configured",
                }
            ],
        },
    )
    assert role.status_code == 200
    role_json = role.json()
    assert (
        role_json["lock"]["roles"]["default"]["primary"]["account_binding"]["account_id"]
        == credential["account_id"]
    )
    assert secret not in role.text

    login = client.post("/v1/auth/login-sessions", json={"provider_id": "openai"})
    assert login.status_code == 200
    login_id = login.json()["login_session_id"]
    assert login.json()["problem"]["code"] == "flow_unavailable"
    assert client.get(f"/v1/auth/login-sessions/{login_id}").status_code == 200
    assert (
        client.post(f"/v1/auth/login-sessions/{login_id}/complete", json={}).status_code
        == 200
    )
    assert client.delete(f"/v1/auth/login-sessions/{login_id}").status_code == 200

    assert (
        client.delete(f"/v1/auth/credentials/{credential['credential_id']}").json()[
            "ok"
        ]
        is True
    )
    assert secret not in caplog.text
    assert secret not in json.dumps(broker.audit_events())


def test_auth_api_key_rejects_secret_bearing_metadata_property_names(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module

    secret = "metadata-property-canary-4j8qn"
    database = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(database))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app())

    response = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={
            "api_key": secret,
            "metadata": {
                "nested": {f"description-{secret}": "ordinary value"},
            },
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "detail": "metadata keys cannot contain credential material",
        "path": None,
    }
    assert secret not in response.text
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert secret not in json.dumps(broker.audit_events())
    assert secret not in redaction.iter_registered_secret_values()


def test_auth_api_key_rejects_values_below_exact_redaction_minimum(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module

    secret = "x"
    database = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(database))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app())

    response = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": secret, "metadata": {"description": secret}},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "detail": "api_key must contain at least four non-whitespace characters",
        "path": None,
    }
    assert secret not in response.text
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert secret not in json.dumps(broker.audit_events())
    assert secret not in redaction.iter_registered_secret_values()


@pytest.mark.parametrize(
    "identity_field",
    ["provider_id", "account_label", "alias", "auth_scheme_id"],
)
def test_auth_api_key_rejects_secret_bearing_credential_identity_fields(
    tmp_path,
    monkeypatch,
    identity_field,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module

    secret = "credential-identity-canary-6m2vk"
    database = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(database))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app())
    provider_id = secret if identity_field == "provider_id" else "openai"
    account_label = secret if identity_field == "account_label" else "main"
    payload = {
        "api_key": secret,
        "alias": secret if identity_field == "alias" else "primary",
        "auth_scheme_id": (
            secret if identity_field == "auth_scheme_id" else "api_key"
        ),
    }

    response = client.put(
        f"/v1/auth/credentials/{provider_id}/{account_label}/api-key",
        json=payload,
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "detail": (
            "credential identity fields cannot contain credential material"
        ),
        "path": None,
    }
    assert secret not in response.text
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert secret not in json.dumps(broker.audit_events())
    assert secret not in redaction.iter_registered_secret_values()


def test_model_catalog_route_projects_only_configured_provider_truth(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module

    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    config_path = tmp_path / "catalog.yaml"
    config_path.write_text(
        """
version: 2
workspace:
  root: .
providers:
  default_model: codex/gpt-5.4
  models:
    - id: codex/gpt-5.4
      adapter: openai
    - id: openai/gpt-4.1
      adapter: openai
    - id: claude-sonnet-4-5
      adapter: anthropic
    - id: openrouter/deepseek/deepseek-v4
      adapter: openai
    - id: google-gemini-cli/gemini-3
      adapter: openai
    - id: mock/reference
      adapter: mock_chat
modes:
  - name: build
    prompt: "noop"
loop:
  sequence:
    - mode: build
""",
        encoding="utf-8",
    )

    response = TestClient(create_app()).get(
        "/v1/models",
        params={"config_path": str(config_path)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["discovery_policy"] == "configured_only"
    assert payload["default_model"] == "codex/gpt-5.4"
    assert [row["canonical_provider"] for row in payload["models"]] == [
        "codex",
        "openai",
        "anthropic",
        "openrouter",
        "google-gemini-cli",
        "mock",
    ]
    assert [row["available"] for row in payload["models"]] == [
        True,
        False,
        False,
        False,
        False,
        True,
    ]
    assert [issue["code"] for issue in payload["issues"]] == ["deferred_provider"]


def test_credential_state_rejects_rebinding_and_cross_site_requests_without_token(
    tmp_path,
    monkeypatch,
) -> None:
    import breadboard_engine.provider_broker.broker as broker_module

    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
    monkeypatch.delenv("BREADBOARD_API_TOKEN", raising=False)
    client = TestClient(create_app())

    allowed = client.post(
        "/v1/auth/login-sessions",
        json={"provider_id": "openai"},
        headers={"Host": "127.0.0.1:8077"},
    )
    assert allowed.status_code == 200
    login_id = allowed.json()["login_session_id"]

    attacker_headers = {"Host": "attacker.example"}
    rejected_login = client.post(
        "/v1/auth/login-sessions",
        json={"provider_id": "openai"},
        headers=attacker_headers,
    )
    rejected_key = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": "must-not-be-stored"},
        headers=attacker_headers,
    )
    rejected_credentials = client.get(
        "/v1/auth/credentials",
        headers=attacker_headers,
    )
    rejected_login_read = client.get(
        f"/v1/auth/login-sessions/{login_id}",
        headers=attacker_headers,
    )
    rejected_roles = client.post(
        "/v1/model-roles/resolve",
        json={"model_roles": _roles()},
        headers=attacker_headers,
    )
    cross_site_key = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": "also-must-not-be-stored"},
        headers={
            "Host": "127.0.0.1:8077",
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    rejected_legacy_attach = client.post(
        "/v1/provider-auth/attach",
        json={
            "material": {
                "provider_id": "openai",
                "api_key": "legacy-must-not-be-stored",
                "base_url": "https://attacker.example",
            }
        },
        headers=attacker_headers,
    )
    rejected_legacy_status = client.get(
        "/v1/provider-auth/status",
        headers=attacker_headers,
    )
    rejected_legacy_detach = client.post(
        "/v1/provider-auth/detach",
        json={"provider_id": "openai"},
        headers=attacker_headers,
    )

    assert rejected_login.status_code == 403
    assert rejected_key.status_code == 403
    assert rejected_credentials.status_code == 403
    assert rejected_login_read.status_code == 403
    assert rejected_roles.status_code == 403
    assert cross_site_key.status_code == 403
    assert rejected_legacy_attach.status_code == 403
    assert rejected_legacy_status.status_code == 403
    assert rejected_legacy_detach.status_code == 403
    assert broker.listCredentials() == []


def test_api_rotation_preserves_resolved_role_lock_hash(tmp_path, monkeypatch):
    import breadboard_engine.provider_broker.broker as broker_module

    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app())
    first = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": "sk-api-old"},
    ).json()
    before = client.post(
        "/v1/model-roles/resolve", json={"model_roles": _roles()}
    ).json()
    second = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": "sk-api-new"},
    ).json()
    after = client.post(
        "/v1/model-roles/resolve", json={"model_roles": _roles()}
    ).json()
    assert second["account_id"] == first["account_id"]
    assert after["lock_hash"] == before["lock_hash"]
    assert "sk-api-old" not in json.dumps(after)
    assert "sk-api-new" not in json.dumps(after)
    assert (
        client.post(f"/v1/auth/credentials/{second['account_id']}/revoke").json()["ok"]
        is True
    )


def test_remote_credential_snapshot_redacts_refresh_coordination(
    tmp_path,
    monkeypatch,
):
    import time

    import breadboard_engine.provider_broker.broker as broker_module

    access_token = "remote-refresh-access-canary"
    refresh_token = "remote-refresh-token-canary"
    owner_id = "remote-refresh-owner-canary"
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    credential = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="remote-refresh",
        material={
            "access_token": access_token,
            "refresh_token": refresh_token,
        },
        expires_at_ms=int(time.time() * 1000) + 60_000,
    )
    claim = broker.store.claim_oauth_refresh(
        account_id=credential["account_id"],
        expected_secret_version=credential["secret_version"],
        owner_id=owner_id,
        lease_duration_ms=30_000,
    )
    assert claim["status"] == "acquired"
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")

    response = TestClient(create_app()).get(
        "/v1/auth/credentials",
        params={"provider_id": "anthropic"},
    )

    assert response.status_code == 200
    assert response.json()[0]["refresh_state"]["status"] == "refreshing"
    assert response.json()[0]["refresh_state"]["expected_secret_version"] == 1
    assert access_token not in response.text
    assert refresh_token not in response.text
    assert owner_id not in response.text


def test_direct_external_bind_requires_explicit_unsupported_override(
    monkeypatch,
) -> None:
    from breadboard_engine.api.cli_bridge.server import (
        build_uvicorn_config,
    )

    monkeypatch.setenv("BREADBOARD_CLI_HOST", "0.0.0.0")
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "api-control-canary")
    monkeypatch.delenv(
        "BREADBOARD_ALLOW_INSECURE_REMOTE",
        raising=False,
    )
    with pytest.raises(SystemExit, match="does not provide TLS"):
        build_uvicorn_config()

    monkeypatch.setenv("BREADBOARD_ALLOW_INSECURE_REMOTE", "1")
    assert build_uvicorn_config()["host"] == "0.0.0.0"

    monkeypatch.delenv("BREADBOARD_API_TOKEN")
    with pytest.raises(SystemExit, match="does not provide TLS"):
        build_uvicorn_config()
