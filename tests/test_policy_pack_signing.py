from __future__ import annotations

import os

import pytest

from agentic_coder_prototype.policy_pack import PolicyPack, sign_policy_payload


def test_signed_policy_pack_verifies_and_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("BREADBOARD_POLICY_HMAC_SECRET", secret)

    payload = {
        "models": {"allow": ["openrouter/*"]},
        "tools": {"deny": ["run_shell"]},
    }
    signature = sign_policy_payload(payload, secret)

    pack = PolicyPack.from_config({"policies": {"signed": {"payload": payload, "signature": signature}}})
    assert pack.is_model_allowed("openrouter/openai/gpt-5-nano") is True
    assert pack.is_model_allowed("openai/gpt-4.1") is False
    assert pack.is_tool_allowed("run_shell") is False


def test_signed_policy_pack_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREADBOARD_POLICY_HMAC_SECRET", "secret")
    payload = {"models": {"allow": ["openrouter/*"]}}
    with pytest.raises(ValueError):
        PolicyPack.from_config({"policies": {"signed": {"payload": payload, "signature": "bad"}}})


def test_signed_policy_pack_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BREADBOARD_POLICY_HMAC_SECRET", raising=False)
    payload = {"models": {"allow": ["openrouter/*"]}}
    with pytest.raises(ValueError):
        PolicyPack.from_config({"policies": {"signed": {"payload": payload, "signature": "sig"}}})

