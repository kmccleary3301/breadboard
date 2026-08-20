from __future__ import annotations

import json
import time
import urllib.parse

from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore, provider_catalog


def _transport_factory(responses):
    calls = []

    def transport(url, *, method, headers, body=None):
        calls.append((url, method, dict(headers), body))
        response = responses.pop(0)
        return response

    return transport, calls


def test_catalog_is_data_driven_and_only_source_established_flows_are_available():
    entries = {entry.provider_id: entry for entry in provider_catalog()}
    assert entries["openai"].oauth_flows == ()
    assert entries["codex"].oauth_flows[0].auth_url == "https://auth.openai.com/oauth/authorize"
    assert entries["anthropic"].oauth_flows[0].token_url == "https://api.anthropic.com/v1/oauth/token"
    assert entries["google-gemini-cli"].oauth_flows[0].callback_path == "/oauth2callback"
    assert entries["google-antigravity"].oauth_flows[0].callback_port == 51121


def test_google_oauth_requires_a_deployment_owned_client_id(monkeypatch, tmp_path):
    env_name = "BREADBOARD_GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID"
    monkeypatch.delenv(env_name, raising=False)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "unconfigured.sqlite3"))

    unavailable = broker.beginLogin({"provider_id": "google-gemini-cli"})
    assert unavailable["status"] == "unavailable"
    assert unavailable["problem"]["code"] == "flow_unavailable"
    assert unavailable["problem"]["details"]["configuration_key"] == env_name
    provider = next(item for item in broker.listProviders() if item["provider_id"] == "google-gemini-cli")
    assert provider["login_available"] is False

    monkeypatch.setenv(env_name, "fixture-google-client-id")
    configured = ProviderBroker(SQLiteCredentialStore(tmp_path / "configured.sqlite3"))
    started = configured.beginLogin({"provider_id": "google-gemini-cli"})
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(started["authorization_url"])[3])
    assert started["status"] == "pending"
    assert params["client_id"] == ["fixture-google-client-id"]
    provider = next(item for item in configured.listProviders() if item["provider_id"] == "google-gemini-cli")
    assert provider["login_available"] is True


def test_codex_browser_login_and_exchange_use_exact_source_endpoints_without_leaking_tokens(tmp_path):
    access = "eyJ.fake-access-token"
    refresh = "refresh-secret-canary"
    responses = [(200, {}, json.dumps({"access_token": access, "refresh_token": refresh, "expires_in": 3600}).encode())]
    transport, calls = _transport_factory(responses)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"), oauth_transport=transport)
    started = broker.beginLogin({"provider_id": "codex"})
    assert started["status"] == "pending"
    assert started["authorization_url"].startswith("https://auth.openai.com/oauth/authorize?")
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(started["authorization_url"])[3])
    assert params["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert params["originator"] == ["pi"]
    flow = broker.store.get_login(started["login_session_id"], include_flow=True)["flow"]
    completed = broker.completeLogin({"login_session_id": started["login_session_id"], "code": "auth-code", "state": flow["state"]})
    assert completed["status"] == "completed"
    assert refresh not in json.dumps(completed)
    assert calls[0][0] == "https://auth.openai.com/oauth/token"
    assert b"grant_type=authorization_code" in calls[0][3]
    material = broker.issue_execution_material("codex")
    assert material and material["api_key"] == access


def test_openai_api_key_has_typed_flow_unavailable_and_anthropic_refresh_is_single_refresher(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    unavailable = broker.beginLogin({"provider_id": "openai"})
    assert unavailable["status"] == "unavailable"
    assert unavailable["problem"]["code"] == "flow_unavailable"
    old_access = "anthropic-access"
    new_access = "anthropic-refreshed"
    transport, calls = _transport_factory([
        (200, {}, json.dumps({"access_token": new_access, "refresh_token": "anthropic-refresh-new", "expires_in": 3600}).encode())
    ])
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "anthropic.sqlite3"), oauth_transport=transport)
    expires_at = int(time.time() * 1000) + 1000
    credential = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="account",
        material={"access_token": old_access, "refresh_token": "anthropic-refresh-old", "expires_at_ms": expires_at},
        expires_at_ms=expires_at,
    )
    material = broker.issue_execution_material("anthropic", account_selector={"account_id": credential["account_id"]}, minimum_validity_ms=5000)
    assert material and material["api_key"] == new_access
    assert calls[0][0] == "https://api.anthropic.com/v1/oauth/token"
    assert calls[0][2]["anthropic-beta"] == "oauth-2025-04-20"
    assert all("anthropic-refresh-old" not in json.dumps(event) for event in broker.audit_events())


def test_codex_device_flow_uses_source_endpoints(tmp_path):
    access = "device-access"
    responses = [
        (200, {}, json.dumps({"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": 1}).encode()),
        (200, {}, json.dumps({"authorization_code": "device-code", "code_verifier": "device-verifier"}).encode()),
        (200, {}, json.dumps({"access_token": access, "refresh_token": "device-refresh", "expires_in": 3600}).encode()),
    ]
    transport, calls = _transport_factory(responses)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "device.sqlite3"), oauth_transport=transport)
    started = broker.beginLogin({"provider_id": "codex", "flow": "device"})
    assert started["flow_kind"] == "device"
    assert started["user_code"] == "ABCD-EFGH"
    completed = broker.completeLogin({"login_session_id": started["login_session_id"]})
    assert completed["status"] == "completed"
    assert [call[0] for call in calls] == [
        "https://auth.openai.com/api/accounts/deviceauth/usercode",
        "https://auth.openai.com/api/accounts/deviceauth/token",
        "https://auth.openai.com/oauth/token",
    ]
