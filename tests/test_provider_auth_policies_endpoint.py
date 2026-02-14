from __future__ import annotations


def test_provider_auth_policies_endpoint_returns_manifests() -> None:
    from fastapi.testclient import TestClient

    from agentic_coder_prototype.api.cli_bridge.app import create_app

    client = TestClient(create_app())
    resp = client.get("/v1/provider-auth/policies")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "policies" in payload
    assert isinstance(payload["policies"], list)
    policy_ids = {(p.get("provider_id"), p.get("plan_id")) for p in payload["policies"] if isinstance(p, dict)}
    assert ("openai", "codex_chatgpt_subscription") in policy_ids
    assert ("anthropic", "consumer_subscription") in policy_ids

    enablement = payload.get("enablement") or {}
    assert "subscription_auth_enabled" in enablement
    assert "per_provider_enabled" in enablement

