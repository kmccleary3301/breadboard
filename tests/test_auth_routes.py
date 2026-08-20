from __future__ import annotations

import json

from fastapi.testclient import TestClient

from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore


def _roles() -> dict:
    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {"role": "default", "known_but_unbound_role": "error", "unknown_role": "error"},
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

    secret = "sk-api-route-canary-secret"
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app())

    providers = client.get("/v1/auth/providers")
    assert providers.status_code == 200
    assert any(item["provider_id"] == "openai" for item in providers.json())

    stored = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": secret, "headers": {"Authorization": f"Bearer {secret}"}},
    )
    assert stored.status_code == 200
    credential = stored.json()
    assert credential["provider_id"] == "openai"
    assert secret not in stored.text

    listed = client.get("/v1/auth/credentials", params={"provider_id": "openai"})
    assert listed.status_code == 200
    assert secret not in listed.text

    role = client.post("/v1/model-roles/resolve", json={"model_roles": _roles()})
    assert role.status_code == 200
    role_json = role.json()
    assert role_json["lock"]["roles"]["default"]["primary"]["account_id"] == credential["account_id"]
    assert secret not in role.text

    login = client.post("/v1/auth/login-sessions", json={"provider_id": "openai"})
    assert login.status_code == 200
    login_id = login.json()["login_session_id"]
    assert login.json()["problem"]["code"] == "flow_unavailable"
    assert client.get(f"/v1/auth/login-sessions/{login_id}").status_code == 200
    assert client.post(f"/v1/auth/login-sessions/{login_id}/complete", json={}).status_code == 200
    assert client.delete(f"/v1/auth/login-sessions/{login_id}").status_code == 200

    assert client.delete(f"/v1/auth/credentials/{credential['credential_id']}").json()["ok"] is True
    assert secret not in caplog.text
    assert secret not in json.dumps(broker.audit_events())


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
    before = client.post("/v1/model-roles/resolve", json={"model_roles": _roles()}).json()
    second = client.put(
        "/v1/auth/credentials/openai/main/api-key",
        json={"api_key": "sk-api-new"},
    ).json()
    after = client.post("/v1/model-roles/resolve", json={"model_roles": _roles()}).json()
    assert second["account_id"] == first["account_id"]
    assert after["lock_hash"] == before["lock_hash"]
    assert "sk-api-old" not in json.dumps(after)
    assert "sk-api-new" not in json.dumps(after)
    assert client.post(f"/v1/auth/credentials/{second['account_id']}/revoke").json()["ok"] is True
