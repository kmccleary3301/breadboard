from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
from email.message import Message

import pytest

from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore, provider_catalog


def _transport_factory(responses):
    calls = []

    def transport(url, *, method, headers, body=None):
        calls.append((url, method, dict(headers), body))
        response = responses.pop(0)
        return response

    return transport, calls


class _FailingResponseBody:
    def __init__(self, error):
        self.error = error

    def read(self, *_args, **_kwargs):
        raise self.error


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
    completed = broker.completeLogin(
        {
            "login_session_id": started["login_session_id"],
            "code": None,
            "authorization_code": "auth-code",
            "state": flow["state"],
        }
    )
    assert completed["status"] == "completed"
    assert completed["credential"]["updated_at_ms"] > 0
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

def test_refresh_response_without_rotated_refresh_token_preserves_stored_token(tmp_path):
    transport, _calls = _transport_factory([
        (200, {}, json.dumps({"access_token": "new-access", "expires_in": 3600}).encode()),
    ])
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "refresh.sqlite3"), oauth_transport=transport)
    expires_at = int(time.time() * 1000) + 1000
    credential = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="account",
        material={"access_token": "old-access", "refresh_token": "stored-refresh"},
        expires_at_ms=expires_at,
    )
    material = broker.issue_execution_material(
        "anthropic",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5000,
    )
    assert material and material["api_key"] == "new-access"
    assert material["refresh_token"] == "stored-refresh"


@pytest.mark.parametrize(
    ("transport_error", "expected_code", "expected_cause_type"),
    [
        (
            urllib.error.URLError(OSError("network-secret-canary")),
            "oauth_transport_error",
            "OSError",
        ),
        (TimeoutError("timeout-secret-canary"), "oauth_transport_timeout", "TimeoutError"),
        (
            urllib.error.URLError(TimeoutError("timeout-secret-canary")),
            "oauth_transport_timeout",
            "TimeoutError",
        ),
    ],
)
def test_oauth_transport_failures_return_typed_broker_problems(
    monkeypatch,
    tmp_path,
    transport_error,
    expected_code,
    expected_cause_type,
):
    def fail_transport(*_args, **_kwargs):
        raise transport_error

    monkeypatch.setattr(
        "breadboard_engine.provider_broker.oauth.urllib.request.urlopen",
        fail_transport,
    )
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / f"{expected_cause_type}.sqlite3"))

    result = broker.beginLogin({"provider_id": "codex", "flow": "device"})

    assert result["status"] == "unavailable"
    assert result["problem"] == {
        "code": expected_code,
        "message": (
            "OAuth endpoint request timed out"
            if expected_code == "oauth_transport_timeout"
            else "OAuth endpoint request failed"
        ),
        "details": {
            "provider_id": "codex",
            "cause_type": expected_cause_type,
        },
    }
    assert "secret-canary" not in json.dumps(result)


@pytest.mark.parametrize(
    ("transport_error", "expected_code"),
    [
        (urllib.error.URLError(OSError("injected-url-secret")), "oauth_transport_error"),
        (TimeoutError("injected-timeout-secret"), "oauth_transport_timeout"),
        (
            urllib.error.URLError(TimeoutError("injected-timeout-secret")),
            "oauth_transport_timeout",
        ),
    ],
)
def test_injected_transport_failures_return_typed_broker_problems(
    tmp_path,
    transport_error,
    expected_code,
):
    def fail_transport(*_args, **_kwargs):
        raise transport_error

    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "injected-error.sqlite3"),
        oauth_transport=fail_transport,
    )

    result = broker.beginLogin({"provider_id": "codex", "flow": "device"})

    assert result["status"] == "unavailable"
    assert result["problem"]["code"] == expected_code
    assert result["problem"]["details"]["cause_type"] == (
        "TimeoutError" if expected_code == "oauth_transport_timeout" else "OSError"
    )
    assert "injected-" not in json.dumps(result)


def test_injected_transport_http_error_preserves_flow_specific_failure(tmp_path):
    def fail_transport(url, **_kwargs):
        raise urllib.error.HTTPError(
            url,
            503,
            "Service Unavailable",
            Message(),
            io.BytesIO(b'{"error":"unavailable"}'),
        )

    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "injected-http-error.sqlite3"),
        oauth_transport=fail_transport,
    )

    result = broker.beginLogin({"provider_id": "codex", "flow": "device"})

    assert result["status"] == "unavailable"
    assert result["problem"] == {
        "code": "oauth_device_start_failed",
        "message": "Device authorization initiation failed",
        "details": {
            "provider_id": "codex",
            "status": 503,
        },
    }


@pytest.mark.parametrize(
    ("body_error", "expected_code"),
    [
        (urllib.error.URLError(OSError("body-url-secret")), "oauth_transport_error"),
        (TimeoutError("body-timeout-secret"), "oauth_transport_timeout"),
    ],
)
def test_injected_http_error_body_failures_are_typed(
    tmp_path,
    body_error,
    expected_code,
):
    def fail_transport(url, **_kwargs):
        raise urllib.error.HTTPError(
            url,
            503,
            "Service Unavailable",
            Message(),
            _FailingResponseBody(body_error),
        )

    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "http-body-error.sqlite3"),
        oauth_transport=fail_transport,
    )

    result = broker.beginLogin({"provider_id": "codex", "flow": "device"})

    assert result["status"] == "unavailable"
    assert result["problem"]["code"] == expected_code
    assert "body-" not in json.dumps(result)
