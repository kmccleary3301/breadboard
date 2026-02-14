from __future__ import annotations

import json
import time

from agentic_coder_prototype.auth.material import EngineAuthMaterial, EmulationProfileRequirement
from agentic_coder_prototype.auth.store import ProviderAuthStore
from agentic_coder_prototype.auth.enforcer import compute_conformance_hash, check_conformance


def test_provider_auth_store_attach_detach_and_status_sanitizes() -> None:
    store = ProviderAuthStore()
    mat = EngineAuthMaterial(
        provider_id="openai",
        alias="alpha",
        api_key="secret",
        headers={"Authorization": "Bearer secret", "X-Other": "ok"},
        base_url="https://example.com",
        routing={"a": 1},
        is_subscription_plan=True,
    )
    store.attach(mat, ttl_seconds=60)
    status = store.status()
    assert status
    assert status[0]["provider_id"] == "openai"
    assert status[0]["has_api_key"] is True
    assert "Authorization" in status[0]["header_keys"]
    assert status[0]["alias"] == "alpha"
    assert status[0]["api_key_fingerprint"].startswith("sha256:")
    assert status[0]["secret_fingerprints"]["api_key"] == status[0]["api_key_fingerprint"]
    assert "Authorization" in status[0]["secret_fingerprints"]
    # Values are never returned, only keys.
    serialized = json.dumps(status, sort_keys=True)
    assert "Bearer secret" not in serialized
    assert '"secret"' not in serialized

    got = store.get("openai", alias="alpha")
    assert got is not None
    assert store.detach("openai", alias="alpha") is True
    assert store.get("openai", alias="alpha") is None


def test_provider_auth_store_alias_isolation() -> None:
    store = ProviderAuthStore()
    store.attach(EngineAuthMaterial(provider_id="openai", alias="a", api_key="k1"))
    store.attach(EngineAuthMaterial(provider_id="openai", alias="b", api_key="k2"))

    assert store.get("openai", alias="a") is not None
    assert store.get("openai", alias="b") is not None
    assert store.detach("openai", alias="a") is True
    assert store.get("openai", alias="a") is None
    assert store.get("openai", alias="b") is not None


def test_provider_auth_store_ttl_expires() -> None:
    store = ProviderAuthStore()
    now_ms = int(time.time() * 1000)
    mat = EngineAuthMaterial(provider_id="openai", expires_at_ms=now_ms - 1)
    store.attach(mat)
    assert store.get("openai") is None


def test_sealed_profile_conformance_hash_and_mismatch() -> None:
    cfg = {"provider": "openai", "dialect": "pythonic", "nested": {"x": 1}}
    pointers = ["/provider", "/dialect"]
    expected = compute_conformance_hash(cfg, pointers)
    ok = check_conformance(config=cfg, locked_json_pointers=pointers, expected_hash=expected)
    assert ok.ok is True

    cfg2 = dict(cfg)
    cfg2["dialect"] = "other"
    bad = check_conformance(config=cfg2, locked_json_pointers=pointers, expected_hash=expected)
    assert bad.ok is False
