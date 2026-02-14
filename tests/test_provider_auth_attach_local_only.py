from __future__ import annotations

import os


def test_provider_auth_attach_subscription_plan_local_only_default() -> None:
    from fastapi.testclient import TestClient

    from agentic_coder_prototype.api.cli_bridge.app import create_app

    client = TestClient(create_app())
    payload = {
        "material": {
            "provider_id": "openai",
            "api_key": "secret",
            "headers": {},
            "is_subscription_plan": True,
        }
    }
    resp = client.post("/v1/provider-auth/attach", json=payload)
    # TestClient host is not loopback, so this should be rejected by default.
    assert resp.status_code == 403, resp.text


def test_provider_auth_attach_subscription_plan_allow_remote_override() -> None:
    from fastapi.testclient import TestClient

    from agentic_coder_prototype.api.cli_bridge.app import create_app

    os.environ["BREADBOARD_ALLOW_SUBSCRIPTION_AUTH_REMOTE"] = "1"
    try:
        client = TestClient(create_app())
        payload = {
            "material": {
                "provider_id": "openai",
                "api_key": "secret",
                "headers": {},
                "is_subscription_plan": True,
            }
        }
        resp = client.post("/v1/provider-auth/attach", json=payload)
        assert resp.status_code == 200, resp.text
    finally:
        os.environ.pop("BREADBOARD_ALLOW_SUBSCRIPTION_AUTH_REMOTE", None)

