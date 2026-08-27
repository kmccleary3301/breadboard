from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from breadboard.product.harness.lock import sha256_json
from breadboard_engine.model_roles import (
    ModelRoleResolutionError,
    compile_model_roles,
    embed_model_role_lock,
    resolve_role_name,
    restore_model_role_lock,
    validate_model_role_lock,
    select_role_target,
)
from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore


def _target(provider: str = "mock", model: str = "reference", **extra: object) -> dict:
    return {"provider_id": provider, "model_id": model, **extra}


def _binding(
    primary: dict | None = None,
    *,
    fallbacks: list[dict] | None = None,
    fallback_on: list[str] | None = None,
    **extra: object,
) -> dict:
    return {
        "primary": primary or _target(),
        "fallbacks": list(fallbacks or []),
        "fallback_on": list(fallback_on or []),
        **extra,
    }


def _document(
    *,
    roles: dict[str, dict] | None = None,
    default_role: str = "default",
    known_unbound: str = "error",
    cross_provider: str = "forbidden",
    account_failover: str = "forbidden",
    allow_overrides: bool = False,
) -> dict:
    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {
            "role": default_role,
            "known_but_unbound_role": known_unbound,
            "unknown_role": "error",
        },
        "roles": roles or {"default": _binding()},
        "dispatch": {"subagents": {}, "lanes": {"main": default_role}},
        "policy": {
            "allow_environment_overrides": allow_overrides,
            "cross_provider_fallback": cross_provider,
            "account_failover": account_failover,
        },
    }


def _account_document(
    *,
    pin: str = "lock",
    selector_mode: str = "default",
    alias: str | None = None,
) -> dict:
    selector = {"mode": selector_mode, "pin": pin}
    if alias is not None:
        selector["alias"] = alias
    return _document(
        roles={
            "default": _binding(
                _target(
                    "openai",
                    "gpt-5.4-mini",
                    account_selector=selector,
                )
            )
        }
    )


def _broker(tmp_path, **kwargs: object) -> ProviderBroker:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return ProviderBroker(
        SQLiteCredentialStore(tmp_path / "credentials.sqlite3"),
        codex_auth_path=tmp_path / "no-codex-auth.json",
        **kwargs,
    )

def _error_code(callable_) -> str:
    with pytest.raises(ModelRoleResolutionError) as error:
        callable_()
    return error.value.problem.code


def test_compile_emits_immutable_classified_lock_and_embeds_graph_hash() -> None:
    document = _document(
        roles={
            "default": _binding(),
            "commit": _binding(_target("mock", "commit")),
            "critic": _binding(_target("mock", "critic")),
        }
    )
    document["dispatch"]["subagents"]["critic"] = "critic"
    lock = compile_model_roles(document)

    assert lock["schema_version"] == "bb.effective_model_role_lock.v1"
    assert lock["roles"]["default"]["classification"] == "public"
    assert lock["roles"]["commit"]["classification"] == "internal"
    assert lock["roles"]["critic"]["classification"] == "custom"
    assert lock["roles"]["default"]["primary"]["route_id"] == "mock/reference"
    assert lock["roles"]["default"]["primary"]["account_binding"] == {
        "kind": "synthetic",
        "pin": "session",
    }
    with pytest.raises(TypeError):
        lock["roles"]["default"]["primary"]["model_id"] = "changed"

    graph = {
        "schema_version": "bb.effective_config_graph.v1",
        "effective_values": [],
        "graph_hash": None,
    }
    embedded = embed_model_role_lock(graph, lock)
    assert embedded["model_role_lock"]["lock_hash"] == lock.lock_hash
    assert embedded["graph_hash"] == sha256_json(
        {**embedded, "graph_hash": None}
    )


def test_lock_pin_resolves_exact_account_and_secret_rotation_keeps_hash(tmp_path) -> None:
    broker = _broker(tmp_path)
    credential = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "main",
            "api_key": "sk-before",
        }
    )
    document = _account_document(pin="lock")
    before = compile_model_roles(document, broker=broker)
    target = before["roles"]["default"]["primary"]

    assert target["account_binding"] == {
        "kind": "account",
        "pin": "lock",
        "account_id": credential["account_id"],
    }
    serialized = json.dumps(before.as_dict())
    assert "sk-before" not in serialized
    assert "credential_id" not in serialized
    assert "secret_version" not in serialized

    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "main",
            "api_key": "sk-after",
        }
    )
    after = compile_model_roles(document, broker=broker)
    assert after.as_dict() == before.as_dict()
    with broker.execution_material("openai") as material:
        assert material and material["api_key"] == "sk-after"


def test_session_pin_binds_once_per_provider_and_restores_across_rotation(tmp_path) -> None:
    broker = _broker(tmp_path)
    credential = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "main",
            "api_key": "sk-before",
        }
    )
    document = _account_document(pin="session")
    lock = compile_model_roles(
        document,
        broker=broker,
        session_id="session-a",
        bind_session_accounts=True,
    )
    binding = lock["roles"]["default"]["primary"]["account_binding"]

    assert binding == {
        "kind": "account",
        "pin": "session",
        "binding_ref": {
            "provider_id": "openai",
            "session_id": "session-a",
        },
    }
    assert broker.get_session_account_binding(
        "session-a", "openai"
    )["account_id"] == credential["account_id"]

    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "main",
            "api_key": "sk-after",
        }
    )
    assert restore_model_role_lock(
        lock.as_dict(), broker=broker, session_id="session-a"
    ).lock_hash == lock.lock_hash
    second = compile_model_roles(
        document,
        broker=broker,
        session_id="session-b",
        bind_session_accounts=True,
    )
    assert (
        second["roles"]["default"]["primary"]["account_binding"]["binding_ref"][
            "session_id"
        ]
        == "session-b"
    )


def test_conflicting_session_accounts_fail_before_any_binding(tmp_path) -> None:
    broker = _broker(tmp_path)
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "first",
            "alias": "first",
            "api_key": "sk-first",
        }
    )
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "second",
            "alias": "second",
            "api_key": "sk-second",
        }
    )
    document = _document(
        roles={
            "default": _binding(
                _target(
                    "openai",
                    "gpt-5.4-mini",
                    account_selector={
                        "mode": "alias",
                        "alias": "first",
                        "pin": "session",
                    },
                ),
                fallbacks=[
                    _target(
                        "openai",
                        "gpt-4.1",
                        account_selector={
                            "mode": "alias",
                            "alias": "second",
                            "pin": "session",
                        },
                    )
                ],
                fallback_on=["provider_unavailable"],
            )
        },
        account_failover="explicit_only",
    )

    assert (
        _error_code(
            lambda: compile_model_roles(
                document,
                broker=broker,
                session_id="conflict",
                bind_session_accounts=True,
            )
        )
        == "session_account_conflict"
    )
    assert broker.get_session_account_binding("conflict", "openai") is None


def test_config_environment_and_fallback_origins_are_stable_and_fail_closed(
    tmp_path, monkeypatch
) -> None:
    config_broker = _broker(tmp_path / "config")
    config_broker.set_config_api_key("openai", "sk-config")
    document = _account_document(pin="session")
    configured = compile_model_roles(
        document, broker=config_broker, session_id="configured"
    )
    assert configured["roles"]["default"]["primary"]["account_binding"][
        "kind"
    ] == "configured"
    assert configured["roles"]["default"]["primary"]["account_binding"][
        "source"
    ] == "config"
    config_broker.remove_config_api_key("openai")
    assert (
        _error_code(
            lambda: restore_model_role_lock(
                configured.as_dict(),
                broker=config_broker,
                session_id="configured",
            )
        )
        == "account_binding_unavailable"
    )

    environment_broker = _broker(tmp_path / "environment")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-one")
    environmental = compile_model_roles(
        document, broker=environment_broker, session_id="environment"
    )
    binding = environmental["roles"]["default"]["primary"]["account_binding"]
    assert binding["kind"] == "environment"
    assert binding["source"] == "OPENAI_API_KEY"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-two")
    assert (
        restore_model_role_lock(
            environmental.as_dict(),
            broker=environment_broker,
            session_id="environment",
        ).lock_hash
        == environmental.lock_hash
    )
    assert (
        compile_model_roles(
            document, broker=environment_broker, session_id="environment"
        ).lock_hash
        == environmental.lock_hash
    )

    monkeypatch.delenv("OPENAI_API_KEY")
    fallback_broker = _broker(
        tmp_path / "fallback",
        fallback_resolver=lambda provider: (
            {"api_key": "sk-fallback"}
            if provider == "openai"
            else None
        ),
        fallback_origins={"openai": "test-fallback"},
    )
    fallback = compile_model_roles(
        document, broker=fallback_broker, session_id="fallback"
    )
    assert fallback["roles"]["default"]["primary"]["account_binding"] == {
        "kind": "fallback",
        "pin": "session",
        "binding_ref": {
            "provider_id": "openai",
            "session_id": "fallback",
        },
        "source": "test-fallback",
    }


def test_account_ownership_and_pin_modes_fail_closed(tmp_path) -> None:
    broker = _broker(tmp_path)
    assert (
        _error_code(
            lambda: compile_model_roles(
                _account_document(pin="session", selector_mode="none"),
                broker=broker,
            )
        )
        == "account_selection_required"
    )
    assert (
        _error_code(
            lambda: compile_model_roles(
                _document(
                    roles={
                        "default": _binding(
                            _target(
                                "mock",
                                "reference",
                                account_selector={
                                    "mode": "default",
                                    "pin": "session",
                                },
                            )
                        )
                    }
                )
            )
        )
        == "account_selector_unsupported"
    )
    config_broker = _broker(tmp_path / "config")
    config_broker.set_config_api_key("openai", "sk-config")
    assert (
        _error_code(
            lambda: compile_model_roles(
                _account_document(pin="lock"), broker=config_broker
            )
        )
        == "concrete_account_required"
    )


def test_role_overrides_are_policy_owned_and_immutable_after_start() -> None:
    document = _document()
    override = {"default": "mock/alternate"}
    assert (
        _error_code(
            lambda: compile_model_roles(document, role_overrides=override)
        )
        == "environment_override_forbidden"
    )
    allowed = copy.deepcopy(document)
    allowed["policy"]["allow_environment_overrides"] = True
    base = compile_model_roles(allowed)
    derived = compile_model_roles(allowed, role_overrides=override)
    assert derived.lock_hash != base.lock_hash
    assert derived["source_model_roles_hash"] != base["source_model_roles_hash"]
    assert (
        _error_code(
            lambda: compile_model_roles(
                allowed,
                role_overrides=override,
                session_started=True,
            )
        )
        == "lock_immutable"
    )
    assert (
        _error_code(
            lambda: compile_model_roles(
                allowed, role_overrides={"typo": "mock/alternate"}
            )
        )
        == "unknown_role_override"
    )


def test_known_unbound_roles_follow_declared_policy_but_typos_never_do() -> None:
    document = _document(known_unbound="use_default")
    lock = compile_model_roles(document)
    assert resolve_role_name(document, "slow") == "default"
    assert resolve_role_name(lock, "slow") == "default"
    assert _error_code(lambda: resolve_role_name(lock, "typo")) == "unknown_role"

    strict = _document()
    assert (
        _error_code(lambda: resolve_role_name(strict, "slow"))
        == "known_role_unbound"
    )


def test_dispatch_references_and_secret_metadata_are_rejected() -> None:
    invalid_dispatch = _document()
    invalid_dispatch["dispatch"]["lanes"]["bad"] = "custom_missing"
    assert (
        _error_code(lambda: compile_model_roles(invalid_dispatch))
        == "unconfigured_custom_role"
    )
    secret = _document()
    secret["roles"]["default"]["metadata"] = {"api_key": "not-allowed"}
    assert (
        _error_code(lambda: compile_model_roles(secret))
        == "secret_material_forbidden"
    )


def test_catalog_admission_rejects_missing_duplicate_stale_deferred_and_capability() -> None:
    document = _document()
    assert (
        _error_code(
            lambda: compile_model_roles(
                document,
                catalog=[
                    {
                        "id": "other",
                        "provider": "mock",
                        "available": True,
                    }
                ],
            )
        )
        == "missing_catalog_target"
    )
    duplicate = [
        {"id": "reference", "provider": "mock", "available": True},
        {"id": "reference", "provider": "mock", "available": True},
    ]
    assert (
        _error_code(lambda: compile_model_roles(document, catalog=duplicate))
        == "duplicate_catalog_target"
    )
    stale = [
        {
            "id": "reference",
            "provider": "mock",
            "available": True,
            "source": "dynamic",
            "discovery": "dynamic",
        }
    ]
    assert (
        _error_code(lambda: compile_model_roles(document, catalog=stale))
        == "stale_catalog_target"
    )
    deferred = [
        {
            "id": "reference",
            "provider": "mock",
            "available": False,
            "support_tier": "deferred",
            "availability_reason": "deferred_provider",
        }
    ]
    assert (
        _error_code(lambda: compile_model_roles(document, catalog=deferred))
        == "deferred_catalog_target"
    )
    requires_vision = _document(
        roles={"default": _binding(requires={"vision": True})}
    )
    assert (
        _error_code(
            lambda: compile_model_roles(
                requires_vision,
                catalog=[
                    {
                        "id": "reference",
                        "provider": "mock",
                        "available": True,
                    }
                ],
            )
        )
        == "capability_mismatch"
    )
    issue_catalog = {
        "models": [
            {
                "id": "reference",
                "provider": "mock",
                "available": True,
            }
        ],
        "issues": [{"code": "duplicate_model"}],
    }
    assert (
        _error_code(
            lambda: compile_model_roles(document, catalog=issue_catalog)
        )
        == "duplicate_catalog_target"
    )


def test_openrouter_native_model_identity_is_not_double_prefixed(tmp_path) -> None:
    broker = _broker(tmp_path)
    broker.set_runtime_api_key("openrouter", "sk-router")
    document = _document(
        roles={
            "default": _binding(
                _target("openrouter", "openai/gpt-5.4")
            )
        }
    )
    lock = compile_model_roles(
        document,
        broker=broker,
        catalog=[
            {
                "id": "openai/gpt-5.4",
                "provider": "openrouter",
                "canonical_provider": "openrouter",
                "available": True,
            }
        ],
    )
    assert (
        lock["roles"]["default"]["primary"]["route_id"]
        == "openrouter/openai/gpt-5.4"
    )

def test_catalog_provider_alias_maps_to_canonical_target(tmp_path) -> None:
    broker = _broker(tmp_path)
    broker.set_runtime_api_key("codex", "sk-codex")
    document = _document(
        roles={"default": _binding(_target("codex", "gpt-5.4"))}
    )

    lock = compile_model_roles(
        document,
        broker=broker,
        catalog=[
            {
                "id": "openai-codex/gpt-5.4",
                "provider": "codex",
                "canonical_provider": "codex",
                "available": True,
            }
        ],
    )

    assert lock["roles"]["default"]["primary"]["route_id"] == "codex/gpt-5.4"


def test_fallback_is_declared_reason_gated_and_advances_exact_chain() -> None:
    document = _document(
        roles={
            "default": _binding(
                _target("mock", "primary"),
                fallbacks=[
                    _target("mock", "secondary"),
                    _target("mock", "tertiary"),
                ],
                fallback_on=["provider_unavailable", "rate_limited"],
            )
        }
    )
    lock = compile_model_roles(document)
    assert select_role_target(
        lock,
        "default",
        failure_reason="provider_unavailable",
        current_route_id="mock/primary",
    )["route_id"] == "mock/secondary"
    assert select_role_target(
        lock,
        "default",
        failure_reason="provider_unavailable",
        current_route_id="mock/secondary",
    )["route_id"] == "mock/tertiary"
    assert select_role_target(
        lock,
        "default",
        failure_reason="provider_unavailable",
        current_route_id="mock/tertiary",
    )["route_id"] == "mock/tertiary"
    emitted = SimpleNamespace(
        output_emitted=True,
        model_fallback_reason="provider_unavailable",
    )
    assert select_role_target(
        lock,
        "default",
        failure_reason=emitted,
        current_route_id="mock/secondary",
    )["route_id"] == "mock/secondary"

    cross = _document(
        roles={
            "default": _binding(
                _target("mock", "primary"),
                fallbacks=[_target("cli_mock", "secondary")],
                fallback_on=["provider_unavailable"],
            )
        }
    )
    assert (
        _error_code(lambda: compile_model_roles(cross))
        == "cross_provider_fallback_forbidden"
    )
    cross["policy"]["cross_provider_fallback"] = "explicit_only"
    assert compile_model_roles(cross)["roles"]["default"]["fallbacks"]

    forbidden = _document()
    forbidden["roles"]["default"]["fallback_on"] = ["auth_failure"]
    assert (
        _error_code(lambda: compile_model_roles(forbidden))
        == "forbidden_fallback_reason"
    )


def test_restore_rejects_hash_semantic_origin_and_account_revocation(tmp_path) -> None:
    lock = compile_model_roles(_document())
    tampered_hash = lock.as_dict()
    tampered_hash["roles"]["default"]["primary"]["model_id"] = "changed"
    assert (
        _error_code(lambda: restore_model_role_lock(tampered_hash))
        == "lock_hash_mismatch"
    )

    semantic = lock.as_dict()
    semantic["roles"]["default"]["classification"] = "custom"
    semantic["lock_hash"] = None
    semantic["lock_hash"] = sha256_json(semantic)
    assert (
        _error_code(lambda: restore_model_role_lock(semantic))
        == "invalid_model_role_lock"
    )

    route = lock.as_dict()
    route["roles"]["default"]["primary"]["route_id"] = "mock/other"
    route["lock_hash"] = None
    route["lock_hash"] = sha256_json(route)
    assert (
        _error_code(lambda: restore_model_role_lock(route))
        == "invalid_model_role_lock"
    )

    broker = _broker(tmp_path / "account")
    credential = broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "main",
            "api_key": "sk-restore",
        }
    )
    account_lock = compile_model_roles(
        _account_document(pin="lock"), broker=broker
    )
    broker.revoke(
        {
            "provider_id": "openai",
            "account_id": credential["account_id"],
        }
    )
    assert (
        _error_code(
            lambda: restore_model_role_lock(
                account_lock.as_dict(), broker=broker
            )
        )
        == "account_binding_unavailable"
    )
    assert (
        validate_model_role_lock(account_lock.as_dict()).lock_hash
        == account_lock.lock_hash
    )


def test_failed_catalog_validation_does_not_partially_bind_session(tmp_path) -> None:
    broker = _broker(tmp_path)
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_label": "main",
            "api_key": "sk-bind",
        }
    )
    assert (
        _error_code(
            lambda: compile_model_roles(
                _account_document(pin="session"),
                broker=broker,
                session_id="not-bound",
                bind_session_accounts=True,
                catalog=[
                    {
                        "id": "other",
                        "provider": "openai",
                        "available": True,
                    }
                ],
            )
        )
        == "missing_catalog_target"
    )
    assert broker.get_session_account_binding("not-bound", "openai") is None
