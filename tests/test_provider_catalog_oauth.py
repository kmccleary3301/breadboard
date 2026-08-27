from __future__ import annotations

import json
import time
import threading
import urllib.parse
import pytest

from breadboard_engine.provider_broker import (
    ProviderBroker,
    SQLiteCredentialStore,
    get_provider_catalog_entry,
    provider_catalog,
)
from breadboard_engine.security import redaction


def _transport_factory(responses):
    calls = []

    def transport(url, *, method, headers, body=None):
        calls.append((url, method, dict(headers), body))
        response = responses.pop(0)
        return response

    return transport, calls


class _FakeClock:
    def __init__(self, now_ms: int) -> None:
        self.now_ms = now_ms

    def __call__(self) -> float:
        return self.now_ms / 1000

    def advance_ms(self, amount: int) -> None:
        self.now_ms += amount


def test_catalog_is_data_driven_and_only_source_established_flows_are_available():
    entries = {entry.provider_id: entry for entry in provider_catalog()}
    assert entries["openai"].oauth_flows == ()
    assert (
        entries["codex"].oauth_flows[0].auth_url
        == "https://auth.openai.com/oauth/authorize"
    )
    assert (
        entries["anthropic"].oauth_flows[0].token_url
        == "https://api.anthropic.com/v1/oauth/token"
    )
    assert (
        entries["google-gemini-cli"].oauth_flows[0].callback_path == "/oauth2callback"
    )
    assert entries["google-antigravity"].oauth_flows[0].callback_port == 51121


def test_google_oauth_requires_a_deployment_owned_client_id(monkeypatch, tmp_path):
    env_name = "BREADBOARD_GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID"
    monkeypatch.delenv(env_name, raising=False)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "unconfigured.sqlite3"))

    unavailable = broker.beginLogin({"provider_id": "google-gemini-cli"})
    assert unavailable["status"] == "unavailable"
    assert unavailable["problem"]["code"] == "flow_unavailable"
    assert unavailable["problem"]["details"]["configuration_key"] == env_name
    assert not any(
        item["provider_id"] == "google-gemini-cli"
        for item in broker.listProviders()
    )
    entry = get_provider_catalog_entry("google-gemini-cli")
    assert entry is not None
    assert entry.support_tier == "deferred"
    assert entry.oauth_flows[0].resolved_client_id() is None

    monkeypatch.setenv(env_name, "fixture-google-client-id")
    configured = ProviderBroker(SQLiteCredentialStore(tmp_path / "configured.sqlite3"))
    started = configured.beginLogin({"provider_id": "google-gemini-cli"})
    params = urllib.parse.parse_qs(
        urllib.parse.urlsplit(started["authorization_url"])[3]
    )
    assert started["status"] == "pending"
    assert params["client_id"] == ["fixture-google-client-id"]
    assert not any(
        item["provider_id"] == "google-gemini-cli"
        for item in configured.listProviders()
    )
    entry = get_provider_catalog_entry("google-gemini-cli")
    assert entry is not None
    assert entry.oauth_flows[0].resolved_client_id() == "fixture-google-client-id"


def test_codex_browser_login_and_exchange_use_exact_source_endpoints_without_leaking_tokens(
    tmp_path,
):
    access = "eyJ.fake-access-token"
    refresh = "refresh-secret-canary"
    responses = [
        (
            200,
            {},
            json.dumps(
                {"access_token": access, "refresh_token": refresh, "expires_in": 3600}
            ).encode(),
        )
    ]
    transport, calls = _transport_factory(responses)
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "credentials.sqlite3"),
        oauth_transport=transport,
    )
    started = broker.beginLogin({"provider_id": "codex"})
    assert started["status"] == "pending"
    assert started["authorization_url"].startswith(
        "https://auth.openai.com/oauth/authorize?"
    )
    params = urllib.parse.parse_qs(
        urllib.parse.urlsplit(started["authorization_url"])[3]
    )
    assert params["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert params["originator"] == ["pi"]
    flow = broker.store.get_login(started["login_session_id"], include_flow=True)[
        "flow"
    ]
    assert "authorization_url" not in flow
    assert params["code_challenge"][0] not in json.dumps(flow)
    assert started["authorization_url"] not in (
        tmp_path / "credentials.sqlite3"
    ).read_bytes().decode("utf-8", errors="ignore")
    status_view = broker.getLogin(started["login_session_id"])
    assert flow["state"] not in json.dumps(status_view)
    assert flow["verifier"] not in json.dumps(status_view)
    assert "authorization_url" not in status_view
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
    assert completed["credential"]["source"] == "login"
    assert refresh not in json.dumps(completed)
    assert calls[0][0] == "https://auth.openai.com/oauth/token"
    assert b"grant_type=authorization_code" in calls[0][3]
    with broker.execution_material("codex") as material:
        assert material and material["api_key"] == access
        assert material["credential_origin"] == {
            "kind": "oauth",
            "account_id": completed["credential"]["account_id"],
            "credential_id": completed["credential"]["credential_id"],
            "source": "login",
        }
    assert material == {}


def test_openai_api_key_has_typed_flow_unavailable_and_anthropic_refresh_is_single_refresher(
    tmp_path,
):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    unavailable = broker.beginLogin({"provider_id": "openai"})
    assert unavailable["status"] == "unavailable"
    assert unavailable["problem"]["code"] == "flow_unavailable"
    old_access = "anthropic-access"
    new_access = "anthropic-refreshed"
    transport, calls = _transport_factory(
        [
            (
                200,
                {},
                json.dumps(
                    {
                        "access_token": new_access,
                        "refresh_token": "anthropic-refresh-new",
                        "expires_in": 3600,
                    }
                ).encode(),
            )
        ]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "anthropic.sqlite3"), oauth_transport=transport
    )
    expires_at = int(time.time() * 1000) + 1000
    credential = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="account",
        material={
            "access_token": old_access,
            "refresh_token": "anthropic-refresh-old",
            "expires_at_ms": expires_at,
        },
        expires_at_ms=expires_at,
    )
    origin = broker.get_credential_origin("anthropic")
    assert origin == {
        "kind": "oauth",
        "account_id": credential["account_id"],
        "credential_id": credential["credential_id"],
        "source": "broker",
    }
    with broker.execution_material(
        "anthropic",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5000,
    ) as material:
        assert material and material["api_key"] == new_access
        assert material["credential_origin"] == origin
    assert calls[0][0] == "https://api.anthropic.com/v1/oauth/token"
    assert calls[0][2]["anthropic-beta"] == "oauth-2025-04-20"
    assert all(
        "anthropic-refresh-old" not in json.dumps(event)
        for event in broker.audit_events()
    )


def test_codex_device_flow_uses_source_endpoints(tmp_path):
    access = "device-access"
    responses = [
        (
            200,
            {},
            json.dumps(
                {"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": 1}
            ).encode(),
        ),
        (
            200,
            {},
            json.dumps(
                {
                    "authorization_code": "device-code",
                    "code_verifier": "device-verifier",
                }
            ).encode(),
        ),
        (
            200,
            {},
            json.dumps(
                {
                    "access_token": access,
                    "refresh_token": "device-refresh",
                    "expires_in": 3600,
                }
            ).encode(),
        ),
    ]
    transport, calls = _transport_factory(responses)
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "device.sqlite3"), oauth_transport=transport
    )
    started = broker.beginLogin({"provider_id": "codex", "flow": "device"})
    assert started["flow_kind"] == "device"
    assert started["user_code"] == "ABCD-EFGH"
    status_view = broker.getLogin(started["login_session_id"])
    assert "user_code" not in status_view
    assert "instructions" not in status_view
    assert "device-1" not in json.dumps(status_view)
    completed = broker.completeLogin({"login_session_id": started["login_session_id"]})
    assert completed["status"] == "completed"
    assert [call[0] for call in calls] == [
        "https://auth.openai.com/api/accounts/deviceauth/usercode",
        "https://auth.openai.com/api/accounts/deviceauth/token",
        "https://auth.openai.com/oauth/token",
    ]


def test_refresh_response_without_rotated_refresh_token_preserves_stored_token(
    tmp_path,
):
    transport, _calls = _transport_factory(
        [
            (
                200,
                {},
                json.dumps({"access_token": "new-access", "expires_in": 3600}).encode(),
            ),
        ]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "refresh.sqlite3"), oauth_transport=transport
    )
    expires_at = int(time.time() * 1000) + 1000
    credential = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="account",
        material={"access_token": "old-access", "refresh_token": "stored-refresh"},
        expires_at_ms=expires_at,
    )
    with broker.execution_material(
        "anthropic",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5000,
    ) as material:
        assert material and material["api_key"] == "new-access"
        assert material["refresh_token"] == "stored-refresh"


def test_refresh_single_flight_is_durable_across_broker_instances(tmp_path):
    db = tmp_path / "single-flight.sqlite3"
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(url, *, method, headers, body=None):
        calls.append((url, method, dict(headers), body))
        entered.set()
        assert release.wait(2)
        return (
            200,
            {},
            json.dumps(
                {
                    "access_token": "single-flight-access-new",
                    "refresh_token": "single-flight-refresh-new",
                    "expires_in": 3600,
                }
            ).encode(),
        )

    first = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    second = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    for broker in (first, second):
        broker._refresh_lease_ms = 500
        broker._refresh_poll_seconds = 0.005
    expires_at = int(time.time() * 1000) + 1_000
    credential = first.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="single-flight",
        material={
            "access_token": "single-flight-access-old",
            "refresh_token": "single-flight-refresh-old",
        },
        expires_at_ms=expires_at,
    )
    start = threading.Barrier(3)
    results = {}
    errors = []

    def resolve(name, broker):
        try:
            start.wait()
            with broker.execution_material(
                "anthropic",
                session_id=f"session-{name}",
                account_selector={"account_id": credential["account_id"]},
                minimum_validity_ms=5_000,
            ) as material:
                results[name] = (
                    material["api_key"],
                    material["refresh_token"],
                    material["secret_version"],
                )
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=resolve, args=("first", first)),
        threading.Thread(target=resolve, args=("second", second)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    assert entered.wait(2)
    time.sleep(0.8)
    release.set()
    for thread in threads:
        thread.join(2)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(calls) == 1
    assert results == {
        "first": (
            "single-flight-access-new",
            "single-flight-refresh-new",
            2,
        ),
        "second": (
            "single-flight-access-new",
            "single-flight-refresh-new",
            2,
        ),
    }


def _stored_oauth_material(
    store: SQLiteCredentialStore,
    account_id: str,
) -> dict[str, str]:
    with store._transaction() as connection:
        row = connection.execute(
            "SELECT material FROM secrets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    assert row is not None
    return json.loads(row["material"])


def test_refresh_terminal_operations_reject_owner_after_lease_takeover(
    tmp_path,
    monkeypatch,
):
    import breadboard_engine.provider_broker.store as store_module

    base_ms = store_module.now_ms()
    monkeypatch.setattr(store_module, "now_ms", lambda: base_ms)
    db = tmp_path / "terminal-takeover.sqlite3"
    stale = SQLiteCredentialStore(db)
    current = SQLiteCredentialStore(db)
    credential = stale.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="terminal-takeover",
        material={
            "access_token": "takeover-access-current",
            "refresh_token": "takeover-refresh-current",
        },
        expires_at_ms=base_ms + 10_000,
    )
    account_id = credential["account_id"]
    assert stale.claim_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="stale-owner",
        lease_duration_ms=100,
    )["status"] == "acquired"

    monkeypatch.setattr(store_module, "now_ms", lambda: base_ms + 101)
    takeover = current.claim_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="current-owner",
        lease_duration_ms=100,
    )
    assert takeover["status"] == "acquired"
    assert takeover["recovered_stale_lease"] is True

    completed = stale.complete_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="stale-owner",
        material={
            "access_token": "takeover-access-stale",
            "refresh_token": "takeover-refresh-stale",
        },
        expires_at_ms=base_ms + 20_000,
    )
    failed = stale.fail_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="stale-owner",
        failure_class="definitive",
        failure_code="invalid_grant",
    )

    assert completed == {"status": "claim_lost"}
    assert failed is False
    assert _stored_oauth_material(current, account_id) == {
        "access_token": "takeover-access-current",
        "refresh_token": "takeover-refresh-current",
    }
    assert current.inspect_refresh_state(account_id)["status"] == "refreshing"


def test_refresh_terminal_operations_preserve_concurrent_rotation(
    tmp_path,
):
    db = tmp_path / "terminal-rotation.sqlite3"
    stale = SQLiteCredentialStore(db)
    current = SQLiteCredentialStore(db)
    now = int(time.time() * 1000)
    credential = stale.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="terminal-rotation",
        material={
            "access_token": "rotation-access-old",
            "refresh_token": "rotation-refresh-old",
        },
        expires_at_ms=now + 10_000,
    )
    account_id = credential["account_id"]
    assert stale.claim_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="stale-owner",
        lease_duration_ms=10_000,
    )["status"] == "acquired"
    rotated = current.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="terminal-rotation",
        account_id=account_id,
        material={
            "access_token": "rotation-access-current",
            "refresh_token": "rotation-refresh-current",
        },
        expires_at_ms=now + 20_000,
    )

    completed = stale.complete_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="stale-owner",
        material={
            "access_token": "rotation-access-stale",
            "refresh_token": "rotation-refresh-stale",
        },
        expires_at_ms=now + 30_000,
    )
    failed = stale.fail_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="stale-owner",
        failure_class="definitive",
        failure_code="invalid_grant",
    )

    assert rotated["secret_version"] == 2
    assert completed == {"status": "claim_lost"}
    assert failed is False
    assert _stored_oauth_material(current, account_id) == {
        "access_token": "rotation-access-current",
        "refresh_token": "rotation-refresh-current",
    }
    assert current.inspect_refresh_state(account_id) == {"status": "idle"}


@pytest.mark.parametrize("terminal", ["complete", "definitive"])
def test_refresh_terminal_transition_holds_write_ownership(
    tmp_path,
    monkeypatch,
    terminal,
):
    import breadboard_engine.provider_broker.store as store_module

    db = tmp_path / f"terminal-lock-{terminal}.sqlite3"
    stale = SQLiteCredentialStore(db)
    current = SQLiteCredentialStore(db)
    now = store_module.now_ms()
    credential = stale.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label=f"terminal-lock-{terminal}",
        material={
            "access_token": "terminal-lock-access-old",
            "refresh_token": "terminal-lock-refresh-old",
        },
        expires_at_ms=now + 10_000,
    )
    account_id = credential["account_id"]
    assert stale.claim_oauth_refresh(
        account_id=account_id,
        expected_secret_version=1,
        owner_id="terminal-owner",
        lease_duration_ms=10_000,
    )["status"] == "acquired"

    terminal_entered = threading.Event()
    release_terminal = threading.Event()
    rotation_started = threading.Event()
    rotation_done = threading.Event()
    terminal_results = []
    rotation_results = []
    terminal_errors = []
    rotation_errors = []
    terminal_thread = None
    original_now_ms = store_module.now_ms

    def controlled_now_ms():
        if threading.current_thread() is terminal_thread:
            terminal_entered.set()
            assert release_terminal.wait(2)
        return original_now_ms()

    monkeypatch.setattr(store_module, "now_ms", controlled_now_ms)

    def apply_terminal():
        try:
            if terminal == "complete":
                terminal_results.append(
                    stale.complete_oauth_refresh(
                        account_id=account_id,
                        expected_secret_version=1,
                        owner_id="terminal-owner",
                        material={
                            "access_token": "terminal-lock-access-refreshed",
                            "refresh_token": "terminal-lock-refresh-refreshed",
                        },
                        expires_at_ms=now + 20_000,
                    )
                )
            else:
                terminal_results.append(
                    stale.fail_oauth_refresh(
                        account_id=account_id,
                        expected_secret_version=1,
                        owner_id="terminal-owner",
                        failure_class="definitive",
                        failure_code="invalid_grant",
                    )
                )
        except BaseException as error:
            terminal_errors.append(error)

    def rotate():
        rotation_started.set()
        try:
            with current.atomic():
                rotation_results.append(
                    current.put_oauth(
                        provider_id="anthropic",
                        auth_scheme_id="oauth2",
                        label=f"terminal-lock-{terminal}",
                        account_id=account_id,
                        material={
                            "access_token": "terminal-lock-access-current",
                            "refresh_token": "terminal-lock-refresh-current",
                        },
                        expires_at_ms=now + 30_000,
                    )
                )
        except BaseException as error:
            rotation_errors.append(error)
        finally:
            rotation_done.set()

    terminal_thread = threading.Thread(target=apply_terminal)
    rotation_thread = threading.Thread(target=rotate)
    terminal_thread.start()
    assert terminal_entered.wait(2)
    rotation_thread.start()
    assert rotation_started.wait(2)
    assert not rotation_done.wait(0.1)
    release_terminal.set()
    terminal_thread.join(2)
    rotation_thread.join(2)

    assert not terminal_errors
    assert not terminal_thread.is_alive()
    assert not rotation_thread.is_alive()
    if terminal == "complete":
        assert terminal_results[0]["status"] == "completed"
        assert rotation_errors == []
        assert rotation_results[0]["secret_version"] == 3
        assert _stored_oauth_material(current, account_id) == {
            "access_token": "terminal-lock-access-current",
            "refresh_token": "terminal-lock-refresh-current",
        }
    else:
        assert terminal_results == [True]
        assert rotation_results == []
        assert len(rotation_errors) == 1
        assert isinstance(rotation_errors[0], ValueError)
        with current._transaction() as connection:
            account = connection.execute(
                "SELECT status FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            secret_count = connection.execute(
                "SELECT COUNT(*) FROM secrets WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
        assert account["status"] == "revoked"
        assert secret_count == 0


def test_stale_refresh_owner_is_recovered_after_restart(
    tmp_path,
    monkeypatch,
):
    import breadboard_engine.provider_broker.store as store_module

    db = tmp_path / "stale-refresh.sqlite3"
    store = SQLiteCredentialStore(db)
    base_ms = store_module.now_ms()
    credential = store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="stale",
        material={
            "access_token": "stale-access-old",
            "refresh_token": "stale-refresh-old",
        },
        expires_at_ms=base_ms + 100,
    )
    claim = store.claim_oauth_refresh(
        account_id=credential["account_id"],
        expected_secret_version=1,
        owner_id="crashed-owner",
        lease_duration_ms=100,
    )
    assert claim["status"] == "acquired"
    monkeypatch.setattr(store_module, "now_ms", lambda: base_ms + 200)
    transport, calls = _transport_factory(
        [
            (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "stale-access-new",
                        "refresh_token": "stale-refresh-new",
                        "expires_in": 3600,
                    }
                ).encode(),
            )
        ]
    )
    restarted = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
    )
    assert restarted.get_credential_origin(
        "anthropic",
        account_selector={"account_id": credential["account_id"]},
    ) == {
        "kind": "oauth",
        "account_id": credential["account_id"],
        "credential_id": credential["credential_id"],
        "source": "broker",
    }

    complete_refresh = restarted.store.complete_oauth_refresh

    def complete_with_registered_secrets(**kwargs):
        assert {
            "stale-access-new",
            "stale-refresh-new",
        } <= set(redaction.iter_registered_secret_values())
        return complete_refresh(**kwargs)

    monkeypatch.setattr(
        restarted.store,
        "complete_oauth_refresh",
        complete_with_registered_secrets,
    )

    with restarted.execution_material(
        "anthropic",
        account_selector={"account_id": credential["account_id"]},
    ) as material:
        assert material["api_key"] == "stale-access-new"
        assert material["secret_version"] == 2

    assert len(calls) == 1
    started = [
        event
        for event in restarted.audit_events()
        if event["event"] == "provider_credential_refresh_started"
    ]
    assert started[-1]["recovered_stale_lease"] is True


def test_transient_refresh_failure_defers_retry_without_revoking(
    tmp_path,
    monkeypatch,
):
    import breadboard_engine.provider_broker.store as store_module

    db = tmp_path / "transient-refresh.sqlite3"
    transport, calls = _transport_factory(
        [
            (
                503,
                {"retry-after": "2"},
                json.dumps({"error": "temporarily_unavailable"}).encode(),
            ),
            (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "transient-access-new",
                        "refresh_token": "transient-refresh-new",
                        "expires_in": 3600,
                    }
                ).encode(),
            ),
        ]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
    )
    expires_at = int(time.time() * 1000) + 1_000
    credential = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="transient",
        material={
            "access_token": "transient-access-old",
            "refresh_token": "transient-refresh-old",
        },
        expires_at_ms=expires_at,
    )

    with broker.execution_material(
        "anthropic",
        session_id="transient-session",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5_000,
    ) as material:
        assert material is None
    view = broker.listCredentials("anthropic")[0]
    assert view["status"] == "active"
    assert view["refresh_state"]["last_failure_class"] == "transient"
    retry_at = view["refresh_state"]["retry_not_before_ms"]
    binding = broker.get_session_account_binding(
        "transient-session",
        "anthropic",
    )
    assert binding["availability"] == "refresh_deferred"
    assert binding["refresh_failure_class"] == "transient"

    restarted = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
    )
    with restarted.execution_material(
        "anthropic",
        session_id="transient-session",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5_000,
    ) as material:
        assert material is None
    assert len(calls) == 1

    monkeypatch.setattr(store_module, "now_ms", lambda: retry_at + 1)
    with restarted.execution_material(
        "anthropic",
        session_id="transient-session",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5_000,
    ) as material:
        assert material["api_key"] == "transient-access-new"
    assert len(calls) == 2


def test_expired_legacy_oauth_without_refresh_token_is_tombstoned(
    tmp_path,
) -> None:
    store = SQLiteCredentialStore(tmp_path / "legacy-refresh.sqlite3")
    expires_at = int(time.time() * 1000) + 1_000
    credential = store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="legacy",
        material={
            "access_token": "legacy-access-old",
            "refresh_token": "legacy-refresh-removed",
        },
        expires_at_ms=expires_at,
    )
    with store._transaction() as connection:
        connection.execute(
            """UPDATE secrets SET material = ?
               WHERE account_id = ? AND revoked_at_ms IS NULL""",
            (
                json.dumps({"access_token": "legacy-access-old"}),
                credential["account_id"],
            ),
        )
    broker = ProviderBroker(store)

    with broker.execution_material(
        "anthropic",
        session_id="legacy-session",
    ) as material:
        assert material is None

    view = broker.listCredentials("anthropic")[0]
    assert view["status"] == "revoked"
    assert view["refresh_state"]["last_failure_class"] == "definitive"
    assert (
        view["refresh_state"]["last_failure_code"]
        == "oauth_refresh_unavailable"
    )


def test_definitive_refresh_failure_tombstones_and_relogin_creates_new_account(
    tmp_path,
):
    transport, calls = _transport_factory(
        [
            (
                400,
                {},
                json.dumps({"error": "invalid_grant"}).encode(),
            ),
            (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "relogin-access-new",
                        "refresh_token": "relogin-refresh-new",
                        "expires_in": 3600,
                    }
                ).encode(),
            ),
        ]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "definitive-refresh.sqlite3"),
        oauth_transport=transport,
    )
    expires_at = int(time.time() * 1000) + 1_000
    old = broker.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="account",
        material={
            "access_token": "relogin-access-old",
            "refresh_token": "relogin-refresh-old",
        },
        expires_at_ms=expires_at,
    )

    with broker.execution_material(
        "anthropic",
        session_id="definitive-session",
        minimum_validity_ms=5_000,
    ) as material:
        assert material is None
    old_view = next(
        item
        for item in broker.listCredentials("anthropic")
        if item["account_id"] == old["account_id"]
    )
    assert old_view["status"] == "revoked"
    assert old_view["refresh_state"]["last_failure_class"] == "definitive"
    failed = [
        event
        for event in broker.audit_events()
        if event["event"] == "provider_credential_refresh_failed"
    ]
    assert failed[-1]["failure_class"] == "definitive"

    started = broker.beginLogin({"provider_id": "anthropic"})
    flow = broker.store.get_login(
        started["login_session_id"],
        include_flow=True,
    )["flow"]
    completed = broker.completeLogin(
        {
            "login_session_id": started["login_session_id"],
            "authorization_code": "new-code",
            "state": flow["state"],
            "account_label": "account",
        }
    )

    assert completed["status"] == "completed"
    assert completed["credential"]["account_id"] != old["account_id"]
    views = broker.listCredentials("anthropic")
    assert {item["status"] for item in views} == {"active", "revoked"}
    with broker.execution_material(
        "anthropic",
        session_id="definitive-session",
    ) as material:
        assert material["account_id"] == completed["credential"]["account_id"]
        assert material["credential_origin"]["binding_kind"] == "automatic"
        assert (
            material["credential_origin"]["binding_reason"]
            == "bound_account_unavailable"
        )
    assert len(calls) == 2


def test_revoke_during_refresh_cannot_resurrect_tombstone(tmp_path):
    db = tmp_path / "revoke-race.sqlite3"
    entered = threading.Event()
    release = threading.Event()

    def transport(_url, *, method, headers, body=None):
        _ = (method, headers, body)
        entered.set()
        assert release.wait(2)
        return (
            200,
            {},
            json.dumps(
                {
                    "access_token": "race-access-new",
                    "refresh_token": "race-refresh-new",
                    "expires_in": 3600,
                }
            ).encode(),
        )

    refresher = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    revoker = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    expires_at = int(time.time() * 1000) + 1_000
    credential = refresher.store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="race",
        material={
            "access_token": "race-access-old",
            "refresh_token": "race-refresh-old",
        },
        expires_at_ms=expires_at,
    )
    result = []
    errors = []

    def refresh():
        try:
            with refresher.execution_material(
                "anthropic",
                account_selector={"account_id": credential["account_id"]},
                minimum_validity_ms=5_000,
            ) as material:
                result.append(material)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=refresh)
    thread.start()
    assert entered.wait(2)
    assert revoker.revoke({"account_id": credential["account_id"]})["ok"] is True
    release.set()
    thread.join(2)

    assert not errors
    assert not thread.is_alive()
    assert result == [None]
    view = next(
        item
        for item in revoker.listCredentials("anthropic")
        if item["account_id"] == credential["account_id"]
    )
    assert view["status"] == "revoked"
    assert view["secret_version"] == 1


def _login_storage_row(store, login_session_id: str) -> tuple[str, int, int, str | None]:
    with store._transaction() as connection:
        row = connection.execute(
            """SELECT status, created_at_ms, updated_at_ms, flow_json
               FROM login_sessions WHERE login_session_id = ?""",
            (login_session_id,),
        ).fetchone()
    assert row is not None
    return (
        str(row["status"]),
        int(row["created_at_ms"]),
        int(row["updated_at_ms"]),
        row["flow_json"],
    )


def test_completed_oauth_login_clears_internal_flow_json(tmp_path, monkeypatch):
    clock = _FakeClock(1_700_000_000_000)
    monkeypatch.setattr(time, "time", clock)
    transport, _calls = _transport_factory(
        [
            (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "completed-access-canary",
                        "refresh_token": "completed-refresh-canary",
                        "expires_in": 3600,
                    }
                ).encode(),
            )
        ]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "completed.sqlite3"),
        oauth_transport=transport,
    )
    started = broker.beginLogin({"provider_id": "codex"})
    assert started["status"] == "pending"
    status, _created_at_ms, _updated_at_ms, flow_json = _login_storage_row(
        broker.store, started["login_session_id"]
    )
    assert status == "pending"
    assert flow_json

    flow = broker.store.get_login(
        started["login_session_id"], include_flow=True
    )["flow"]
    completed = broker.completeLogin(
        {
            "login_session_id": started["login_session_id"],
            "authorization_code": "completed-code",
            "state": flow["state"],
        }
    )

    assert completed["status"] == "completed"
    status, _created_at_ms, _updated_at_ms, flow_json = _login_storage_row(
        broker.store, started["login_session_id"]
    )
    assert status == "completed"
    assert flow_json is None
    assert broker.cancelLogin(started["login_session_id"])["ok"] is False
    assert (
        _login_storage_row(broker.store, started["login_session_id"])[0] == "completed"
    )


def test_failed_oauth_login_clears_internal_flow_json(tmp_path):
    transport, _calls = _transport_factory(
        [(400, {}, json.dumps({"error": "access_denied"}).encode())]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "failed.sqlite3"),
        oauth_transport=transport,
    )
    started = broker.beginLogin({"provider_id": "codex"})
    flow = broker.store.get_login(
        started["login_session_id"], include_flow=True
    )["flow"]

    failed = broker.completeLogin(
        {
            "login_session_id": started["login_session_id"],
            "authorization_code": "failed-code",
            "state": flow["state"],
        }
    )

    assert failed["status"] == "failed"
    status, _created_at_ms, _updated_at_ms, flow_json = _login_storage_row(
        broker.store, started["login_session_id"]
    )
    assert status == "failed"
    assert flow_json is None
    assert broker.cancelLogin(started["login_session_id"])["ok"] is False
    assert _login_storage_row(broker.store, started["login_session_id"])[0] == "failed"


def test_cancelled_oauth_login_clears_internal_flow_json(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "cancelled.sqlite3"))
    started = broker.beginLogin({"provider_id": "codex"})
    assert started["status"] == "pending"
    assert _login_storage_row(broker.store, started["login_session_id"])[3]

    cancelled = broker.cancelLogin(started["login_session_id"])

    assert cancelled["ok"] is True
    status, _created_at_ms, _updated_at_ms, flow_json = _login_storage_row(
        broker.store, started["login_session_id"]
    )
    assert status == "cancelled"
    assert flow_json is None
    assert broker.cancelLogin(started["login_session_id"])["ok"] is False


def test_cancel_during_oauth_completion_wins_without_persisting_material(tmp_path):
    db = tmp_path / "cancel-completion-race.sqlite3"
    entered = threading.Event()
    release = threading.Event()
    access_canary = "cancel-race-access-canary"
    refresh_canary = "cancel-race-refresh-canary"

    def transport(_url, *, method, headers, body=None):
        _ = (method, headers, body)
        entered.set()
        assert release.wait(2)
        return (
            200,
            {},
            json.dumps(
                {
                    "access_token": access_canary,
                    "refresh_token": refresh_canary,
                    "expires_in": 3600,
                }
            ).encode(),
        )

    completer = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    canceller = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    started = completer.beginLogin({"provider_id": "codex"})
    flow = completer.store.get_login(started["login_session_id"], include_flow=True)[
        "flow"
    ]
    results = []
    errors = []

    def complete():
        try:
            results.append(
                completer.completeLogin(
                    {
                        "login_session_id": started["login_session_id"],
                        "authorization_code": "cancel-race-code",
                        "state": flow["state"],
                    }
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=complete)
    thread.start()
    assert entered.wait(2)
    assert canceller.cancelLogin(started["login_session_id"])["ok"] is True
    release.set()
    thread.join(2)

    assert not errors
    assert not thread.is_alive()
    assert [result["status"] for result in results] == ["cancelled"]
    assert all("credential" not in result for result in results)
    status, _created_at_ms, _updated_at_ms, flow_json = _login_storage_row(
        canceller.store, started["login_session_id"]
    )
    assert status == "cancelled"
    assert flow_json is None
    assert canceller.listCredentials() == []
    with canceller.store._transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0
    audit = canceller.audit_events()
    assert not any(event["event"] == "provider_login_completed" for event in audit)
    visible = json.dumps(
        {
            "results": results,
            "login": canceller.getLogin(started["login_session_id"]),
            "credentials": canceller.listCredentials(),
            "audit": audit,
        }
    )
    database_bytes = b"".join(
        path.read_bytes()
        for path in (db, db.with_name(f"{db.name}-wal"))
        if path.exists()
    )
    for canary in (access_canary, refresh_canary):
        assert canary not in visible
        assert canary.encode() not in database_bytes


def test_cancel_during_device_poll_stops_before_token_exchange(tmp_path):
    db = tmp_path / "cancel-device-poll.sqlite3"
    poll_entered = threading.Event()
    release_poll = threading.Event()
    token_exchange = threading.Event()
    authorization_canary = "cancel-device-authorization-canary"
    verifier_canary = "cancel-device-verifier-canary"
    calls = []

    def transport(url, *, method, headers, body=None):
        _ = (method, headers, body)
        calls.append(url)
        if url.endswith("/deviceauth/usercode"):
            return (
                200,
                {},
                json.dumps(
                    {
                        "device_auth_id": "cancel-device-id",
                        "user_code": "CANCEL-DEVICE",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ).encode(),
            )
        if url.endswith("/deviceauth/token"):
            poll_entered.set()
            assert release_poll.wait(2)
            return (
                200,
                {},
                json.dumps(
                    {
                        "authorization_code": authorization_canary,
                        "code_verifier": verifier_canary,
                    }
                ).encode(),
            )
        if url.endswith("/oauth/token"):
            token_exchange.set()
            return (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "must-not-be-issued",
                        "refresh_token": "must-not-be-issued",
                        "expires_in": 3600,
                    }
                ).encode(),
            )
        raise AssertionError(f"unexpected OAuth endpoint: {url}")

    completer = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    canceller = ProviderBroker(SQLiteCredentialStore(db), oauth_transport=transport)
    started = completer.beginLogin({"provider_id": "codex", "flow": "device"})
    assert started["flow_kind"] == "device"
    results = []
    errors = []

    def complete():
        try:
            results.append(
                completer.completeLogin(
                    {"login_session_id": started["login_session_id"]}
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=complete)
    thread.start()
    assert poll_entered.wait(2)
    assert canceller.cancelLogin(started["login_session_id"])["ok"] is True
    release_poll.set()
    thread.join(2)

    assert not errors
    assert not thread.is_alive()
    assert [result["status"] for result in results] == ["cancelled"]
    assert token_exchange.is_set() is False
    assert calls == [
        "https://auth.openai.com/api/accounts/deviceauth/usercode",
        "https://auth.openai.com/api/accounts/deviceauth/token",
    ]
    assert canceller.listCredentials() == []
    audit = canceller.audit_events()
    assert not any(
        event["event"] in {"provider_login_completed", "provider_login_failed"}
        for event in audit
    )
    visible = json.dumps(
        {
            "results": results,
            "login": canceller.getLogin(started["login_session_id"]),
            "credentials": canceller.listCredentials(),
            "audit": audit,
        }
    )
    database_bytes = b"".join(
        path.read_bytes()
        for path in (db, db.with_name(f"{db.name}-wal"))
        if path.exists()
    )
    for canary in (authorization_canary, verifier_canary):
        assert canary not in visible
        assert canary.encode() not in database_bytes


def test_pending_oauth_login_has_at_most_ten_minute_deadline(tmp_path, monkeypatch):
    clock = _FakeClock(1_700_000_000_000)
    monkeypatch.setattr(time, "time", clock)
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "deadline.sqlite3"))

    started = broker.beginLogin({"provider_id": "codex"})

    assert started["status"] == "pending"
    assert 0 < started["expires_at_ms"] - started["created_at_ms"] <= 10 * 60 * 1000


def test_expired_oauth_login_is_rejected_without_transport(tmp_path, monkeypatch):
    clock = _FakeClock(1_700_000_000_000)
    monkeypatch.setattr(time, "time", clock)
    transport, calls = _transport_factory(
        [(500, {}, json.dumps({"error": "must-not-be-called"}).encode())]
    )
    broker = ProviderBroker(
        SQLiteCredentialStore(tmp_path / "expired.sqlite3"),
        oauth_transport=transport,
    )
    started = broker.beginLogin({"provider_id": "codex"})
    flow = broker.store.get_login(
        started["login_session_id"], include_flow=True
    )["flow"]

    clock.advance_ms(10 * 60 * 1000 + 1)
    expired = broker.completeLogin(
        {
            "login_session_id": started["login_session_id"],
            "authorization_code": "stale-code",
            "state": flow["state"],
        }
    )

    assert expired["status"] == "expired"
    assert expired["problem"]["code"] == "oauth_login_expired"
    assert calls == []
    status, _created_at_ms, _updated_at_ms, flow_json = _login_storage_row(
        broker.store, started["login_session_id"]
    )
    assert status == "expired"
    assert flow_json is None


def test_definitive_oauth_refresh_removes_secret_rows_but_keeps_tombstones(
    tmp_path, monkeypatch
):
    clock = _FakeClock(1_700_000_000_000)
    monkeypatch.setattr(time, "time", clock)
    transport, _calls = _transport_factory(
        [(400, {}, json.dumps({"error": "invalid_grant"}).encode())]
    )
    store = SQLiteCredentialStore(tmp_path / "definitive-tombstone.sqlite3")
    broker = ProviderBroker(store, oauth_transport=transport)
    credential = store.put_oauth(
        provider_id="anthropic",
        auth_scheme_id="oauth2",
        label="definitive",
        material={
            "access_token": "definitive-access-canary",
            "refresh_token": "definitive-refresh-canary",
        },
        expires_at_ms=clock.now_ms + 1,
    )

    with broker.execution_material(
        "anthropic",
        session_id="definitive-tombstone",
        account_selector={"account_id": credential["account_id"]},
        minimum_validity_ms=5_000,
    ) as material:
        assert material is None

    account = next(
        item
        for item in broker.listCredentials("anthropic")
        if item["account_id"] == credential["account_id"]
    )
    assert account["status"] == "revoked"
    refresh_state = store.inspect_refresh_state(credential["account_id"])
    assert refresh_state["last_failure_class"] == "definitive"
    assert refresh_state["last_failure_code"] == "oauth_refresh_failed"
    with store._transaction() as connection:
        secret_rows = connection.execute(
            "SELECT material FROM secrets WHERE account_id = ?",
            (credential["account_id"],),
        ).fetchall()
    assert secret_rows == []
