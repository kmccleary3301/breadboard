from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys

from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore
from breadboard_engine.provider_broker.broker import (
    LeaseCapabilityChannel,
    LeaseCapabilityServer,
)


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
    assert "api_key" not in credential
    assert "material" not in credential
    assert broker.listCredentials("openai")[0]["account_id"] == credential["account_id"]

    login = broker.beginLogin({"provider_id": "unknown-provider"})
    assert login["status"] == "unavailable"
    assert login["problem"]["code"] == "flow_unavailable"
    assert broker.getLogin(login["login_session_id"])["status"] == "unavailable"
    assert broker.completeLogin({"login_session_id": login["login_session_id"]})["problem"]["code"] == "flow_unavailable"
    assert broker.cancelLogin(login["login_session_id"])["ok"] is True

    assert broker.logout({"account_id": credential["account_id"]})["ok"] is True
    assert broker.revoke({"account_id": credential["account_id"]})["ok"] is True
    assert broker.listCredentials("openai")[0]["status"] == "revoked"


def test_store_separates_secret_material_and_enforces_expiring_leases(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(db))
    credential = broker.putApiKey(
        {
            "provider_id": "anthropic",
            "account_label": "lease",
            "api_key": "anthropic-lease-secret",
            "ttl_seconds": 60,
        }
    )
    inspected = broker.store.inspect_accounts()
    assert inspected and "anthropic-lease-secret" not in json.dumps(inspected)
    material = broker.issue_execution_material("anthropic", session_id="session-1", endpoint_id="messages")
    assert material and material["api_key"] == "anthropic-lease-secret"
    redeemed = broker.redeem_execution_material(
        material["lease_id"],
        provider_id="anthropic",
        endpoint_id="messages",
    )
    assert redeemed and redeemed["api_key"] == "anthropic-lease-secret"
    assert redeemed["lease_id"] == material["lease_id"]
    assert (
        broker.redeem_execution_material(
            material["lease_id"],
            provider_id="openai",
            endpoint_id="messages",
        )
        is None
    )
    assert (
        broker.redeem_execution_material(
            material["lease_id"],
            provider_id="anthropic",
            endpoint_id="different",
        )
        is None
    )
    assert broker.store.release_lease(material["lease_id"]) is True
    assert broker.store.release_lease(material["lease_id"]) is False
    assert (
        broker.redeem_execution_material(
            material["lease_id"],
            provider_id="anthropic",
            endpoint_id="messages",
        )
        is None
    )


def test_lease_capability_channel_is_bound_and_has_no_broker_authority(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    broker = ProviderBroker(SQLiteCredentialStore(db))
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "primary",
            "api_key": "openai-authority-secret",
        }
    )
    broker.putApiKey(
        {
            "provider_id": "anthropic",
            "account_label": "other",
            "api_key": "anthropic-authority-secret",
        }
    )
    material = broker.issue_execution_material(
        "openai",
        session_id="session-authority",
        endpoint_id="openai/gpt-5.4-mini",
    )
    assert material is not None
    requests: queue.Queue = queue.Queue()
    responses: queue.Queue = queue.Queue()
    channel = LeaseCapabilityChannel(
        request_queue=requests,
        response_queue=responses,
        capability_token="test-capability",
        provider_id="openai",
        endpoint_id="openai/gpt-5.4-mini",
    )
    server = LeaseCapabilityServer(
        broker=broker,
        request_queue=requests,
        response_queue=responses,
        capability_token="test-capability",
        lease_id=material["lease_id"],
        provider_id="openai",
        endpoint_id="openai/gpt-5.4-mini",
    )
    server.start()
    try:
        redeemed = channel.redeem(
            provider_id="openai",
            endpoint_id="openai/gpt-5.4-mini",
        )
        assert redeemed and redeemed["api_key"] == "openai-authority-secret"
        assert channel.redeem(
            provider_id="anthropic",
            endpoint_id="openai/gpt-5.4-mini",
        ) is None
        assert channel.redeem(
            provider_id="openai",
            endpoint_id="openai/different",
        ) is None
        assert not hasattr(channel, "_broker")
        assert str(db) not in repr(vars(channel))

        requests.put(
            {
                "request_id": "lateral",
                "capability_token": "test-capability",
                "operation": "issue",
                "provider_id": "anthropic",
                "endpoint_id": "anthropic/claude-sonnet-4",
            }
        )
        response = responses.get(timeout=1)
        assert response == {"request_id": "lateral", "material": None}
    finally:
        assert broker.release_execution_material(material["lease_id"]) is True
        server.stop()


def test_session_start_child_inherits_no_credential_environment(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey({"provider_id": "openai", "account_label": "child", "api_key": "sk-child-secret"})
    child_env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        child_env.pop(key, None)
    child_env["BREADBOARD_CREDENTIAL_STORE_PATH"] = str(tmp_path / "credentials.sqlite3")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; print('credential-env=', any(k.endswith('API_KEY') for k in os.environ))",
        ],
        env=child_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "credential-env= False"


def test_provider_client_construction_uses_broker_material_and_sdk_seam(tmp_path, monkeypatch):
    import breadboard_engine.provider_broker.broker as broker_module
    from breadboard_engine.provider import sdk_bindings
    from breadboard_engine.provider.routing import ProviderRouter
    from breadboard_engine.provider.runtimes.openai import OpenAIChatRuntime
    from breadboard_engine.provider.routing import ProviderDescriptor

    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey({"provider_id": "openai", "account_label": "runtime", "api_key": "sk-runtime-secret"})
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    config = ProviderRouter().create_client_config("openai/gpt-5.4-mini")
    assert config["api_key"] == "sk-runtime-secret"

    calls = []

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
    runtime.create_client(config["api_key"])
    assert calls == [{"api_key": "sk-runtime-secret"}]


def test_rotation_preserves_session_history_hashes(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    first = broker.putApiKey({"provider_id": "openai", "account_label": "rotate", "api_key": "sk-old-secret"})
    history = {
        "session_id": "session-rotate",
        "lock": {"lock_id": "lock-1", "model": "openai/gpt-5.4-mini", "account_id": first["account_id"]},
        "graph": {"nodes": ["prompt", "provider"], "edges": [["prompt", "provider"]]},
        "events": [{"type": "session.start", "lock_id": "lock-1"}],
    }
    encoded = json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
    lock_hash = hashlib.sha256(json.dumps(history["lock"], sort_keys=True).encode()).hexdigest()
    graph_hash = hashlib.sha256(json.dumps(history["graph"], sort_keys=True).encode()).hexdigest()
    broker.putApiKey({"provider_id": "openai", "account_label": "rotate", "api_key": "sk-new-secret"})
    assert hashlib.sha256(json.dumps(history, sort_keys=True, separators=(",", ":")).encode()).digest() == hashlib.sha256(encoded).digest()
    assert hashlib.sha256(json.dumps(history["lock"], sort_keys=True).encode()).hexdigest() == lock_hash
    assert hashlib.sha256(json.dumps(history["graph"], sort_keys=True).encode()).hexdigest() == graph_hash
    material = broker.issue_execution_material("openai")
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
