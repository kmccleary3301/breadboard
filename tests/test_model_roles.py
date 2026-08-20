from __future__ import annotations

import json

import pytest

from breadboard.product.harness.lock import sha256_json
from breadboard_engine.model_roles import (
    ModelRoleResolutionError,
    compile_model_roles,
    embed_model_role_lock,
    select_role_target,
)
from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore


def _document() -> dict:
    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {"role": "default", "known_but_unbound_role": "error", "unknown_role": "error"},
        "roles": {
            "default": {
                "primary": {
                    "provider_id": "openai",
                    "model_id": "gpt-5.4-mini",
                    "endpoint_id": "chat",
                    "auth_scheme_id": "api_key",
                    "account_selector": {"mode": "default", "pin": "lock"},
                },
                "fallbacks": [{"provider_id": "openai", "model_id": "gpt-4.1"}],
                "fallback_on": ["provider_unavailable", "rate_limited"],
            },
            "review": {
                "primary": {"provider_id": "anthropic", "model_id": "claude-sonnet"},
                "fallbacks": [],
                "fallback_on": [],
            },
        },
        "dispatch": {"subagents": {"reviewer": "review"}, "lanes": {"main": "default"}},
        "policy": {"cross_provider_fallback": "forbidden", "account_failover": "forbidden"},
    }


def test_role_chain_compiles_model_map_into_immutable_lock_with_account_identity(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    credential = broker.putApiKey({"provider_id": "openai", "account_label": "main", "api_key": "sk-role-secret"})
    lock = compile_model_roles(_document(), broker=broker)
    assert lock["schema_version"] == "bb.effective_model_role_lock.v1"
    assert lock["roles"]["default"]["primary"]["model_id"] == "gpt-5.4-mini"
    assert lock["roles"]["default"]["primary"]["account_id"] == credential["account_id"]
    assert "sk-role-secret" not in json.dumps(lock.as_dict())
    with pytest.raises(TypeError):
        lock["roles"]["default"]["primary"]["model_id"] = "changed"
    graph = {"schema_version": "bb.effective_config_graph.v1", "effective_values": [], "graph_hash": None}
    embedded = embed_model_role_lock(graph, lock)
    assert embedded["model_role_lock"]["lock_hash"] == lock.lock_hash
    assert embedded["graph_hash"] == sha256_json({**embedded, "graph_hash": None})


def test_credential_rotation_does_not_change_role_lock_hash(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    broker.putApiKey({"provider_id": "openai", "account_label": "main", "api_key": "sk-before"})
    before = compile_model_roles(_document(), broker=broker)
    broker.putApiKey({"provider_id": "openai", "account_label": "main", "api_key": "sk-after"})
    after = compile_model_roles(_document(), broker=broker)
    assert before.lock_hash == after.lock_hash
    assert before.as_dict() == after.as_dict()
    material = broker.issue_execution_material("openai")
    assert material and material["api_key"] == "sk-after"


def test_pre_start_role_override_derives_new_lock_and_post_start_is_typed_problem(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    override = {"default": {"provider_id": "openai", "model_id": "gpt-4.1"}}
    base = compile_model_roles(_document(), broker=broker)
    derived = compile_model_roles(_document(), broker=broker, role_overrides=override)
    assert derived.lock_hash != base.lock_hash
    with pytest.raises(ModelRoleResolutionError) as error:
        compile_model_roles(_document(), broker=broker, role_overrides=override, session_started=True)
    assert error.value.problem.code == "lock_immutable"


def test_fallback_policy_is_same_provider_and_never_auth_or_policy(tmp_path):
    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / "credentials.sqlite3"))
    lock = compile_model_roles(_document(), broker=broker)
    assert select_role_target(lock, "default", failure_reason="provider_unavailable")["model_id"] == "gpt-4.1"
    assert select_role_target(lock, "default", failure_reason="auth_failure")["model_id"] == "gpt-5.4-mini"
    assert select_role_target(lock, "default", failure_reason="policy_rejection")["model_id"] == "gpt-5.4-mini"
    invalid = _document()
    invalid["roles"]["default"]["fallbacks"] = [{"provider_id": "anthropic", "model_id": "claude-sonnet"}]
    with pytest.raises(ModelRoleResolutionError) as error:
        compile_model_roles(invalid, broker=broker)
    assert error.value.problem.code == "cross_provider_fallback_forbidden"
    invalid = _document()
    invalid["roles"]["default"]["fallback_on"] = ["auth_failure"]
    with pytest.raises(ModelRoleResolutionError) as error:
        compile_model_roles(invalid, broker=broker)
    assert error.value.problem.code == "forbidden_fallback_reason"


def test_role_override_attempt_is_rejected_by_session_runner_boundary():
    from breadboard_engine.api.cli_bridge.models import SessionCreateRequest
    from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
    from breadboard_engine.api.cli_bridge.session_runner import SessionRunner

    record = SessionRecord(session_id="role-session", status="starting", metadata={"model_role_lock_hash": "sha256:abc"})
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="config.yaml", task="task"),
    )
    import asyncio

    with pytest.raises(ModelRoleResolutionError) as error:
        asyncio.run(runner.handle_command("set_role", {"role": "review"}))
    assert error.value.problem.to_dict()["error_code"] == "lock_immutable"
