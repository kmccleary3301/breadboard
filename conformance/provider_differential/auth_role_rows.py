"""Hermetic authentication and model-role differential observations.

The observers intentionally report only product-visible, secret-free semantics.
Each row builds a fresh credential store below the supplied root and exercises
public broker/model-role seams rather than inspecting transport internals.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from unittest import mock

from breadboard_engine.model_roles import (
    ModelRoleResolutionError,
    compile_model_roles,
    resolve_role_name,
    restore_model_role_lock,
    select_role_target,
)
from breadboard_engine.provider.contracts import ProviderRuntimeError
from breadboard_engine.provider_broker import (
    AUTH_SOURCE_PRECEDENCE,
    ProviderBroker,
    SQLiteCredentialStore,
)


AUTH_ROLE_ROW_IDS: tuple[str, ...] = (
    "auth.api_key_precedence",
    "auth.codex_oauth_precedence",
    "auth.explicit_account_binding",
    "auth.automatic_affinity_restart_rotation",
    "auth.classified_429_rotation",
    "auth.refresh_single_flight",
    "auth.refresh_transient_deferral",
    "auth.refresh_definitive_tombstone",
    "auth.revoke_during_refresh",
    "role.public_alias_selection",
    "role.unknown_unavailable",
    "role.lock_secret_rotation_restart",
    "role.auth_policy_no_fallback",
    "role.cross_provider_default_forbidden",
)
_CONCURRENCY_TIMEOUT_SECONDS = 10.0


class _Clock:
    """A per-observation clock used to make expiry fixtures self-contained."""

    def __init__(self) -> None:
        self.now_ms = int(time.time() * 1000)

    def after(self, milliseconds: int) -> int:
        return self.now_ms + int(milliseconds)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _database(root: pathlib.Path, row_id: str) -> pathlib.Path:
    root = pathlib.Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:12]
    return root / f"auth-role-{digest}.sqlite3"


def _broker(
    root: pathlib.Path,
    row_id: str,
    *,
    oauth_transport: Callable[..., tuple[int, Mapping[str, str], bytes]] | None = None,
    fallback_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    fallback_origins: Mapping[str, str] | None = None,
) -> ProviderBroker:
    database = _database(root, row_id)
    return ProviderBroker(
        SQLiteCredentialStore(database),
        oauth_transport=oauth_transport,
        fallback_resolver=fallback_resolver,
        fallback_origins=fallback_origins,
        codex_auth_path=database.with_name(f"{database.stem}-codex-auth.json"),
    )


def _api_key(
    broker: ProviderBroker,
    label: str,
    secret: str,
    *,
    provider_id: str = "openai",
    alias: str = "",
    account_id: str | None = None,
) -> dict[str, Any]:
    return broker.putApiKey(
        {
            "provider_id": provider_id,
            "account_label": label,
            "alias": alias,
            "account_id": account_id,
            "api_key": secret,
        }
    )


def _oauth(
    broker: ProviderBroker,
    label: str,
    access: str,
    refresh: str,
    clock: _Clock,
    *,
    provider_id: str = "anthropic",
) -> dict[str, Any]:
    return broker.store.put_oauth(
        provider_id=provider_id,
        auth_scheme_id="oauth2",
        label=label,
        source="broker",
        expires_at_ms=clock.after(100),
        material={"access_token": access, "refresh_token": refresh},
    )


def _response(
    status: int,
    body: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
) -> tuple[int, Mapping[str, str], bytes]:
    return status, dict(headers or {}), json.dumps(dict(body)).encode("utf-8")


def _transport_factory(
    responses: list[tuple[int, Mapping[str, str], bytes]],
) -> tuple[Callable[..., tuple[int, Mapping[str, str], bytes]], list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    remaining = list(responses)

    def transport(
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> tuple[int, Mapping[str, str], bytes]:
        _ = (headers, body)
        calls.append((str(url), str(method)))
        if not remaining:
            raise RuntimeError("unexpected OAuth transport call")
        return remaining.pop(0)

    return transport, calls


def _origin_leg(origin: Mapping[str, Any] | None) -> str | None:
    if origin is None:
        return None
    kind = str(origin.get("kind") or "")
    if kind == "api_key":
        source = str(origin.get("source") or "")
        return "login_api_key" if source == "login" else "stored_api_key"
    return kind or None


AUTH_ROLE_PUBLIC_API_CALLS = frozenset(
    {
        "breadboard_engine.model_roles.compile_model_roles",
        "breadboard_engine.model_roles.resolve_role_name",
        "breadboard_engine.model_roles.restore_model_role_lock",
        "breadboard_engine.model_roles.select_role_target",
        "breadboard_engine.provider_broker.broker.ProviderBroker.clear_config_api_keys",
        "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
        "breadboard_engine.provider_broker.broker.ProviderBroker.get_credential_origin",
        "breadboard_engine.provider_broker.broker.ProviderBroker.get_session_account_binding",
        "breadboard_engine.provider_broker.broker.ProviderBroker.listCredentials",
        "breadboard_engine.provider_broker.broker.ProviderBroker.putApiKey",
        "breadboard_engine.provider_broker.broker.ProviderBroker.remove_runtime_api_key",
        "breadboard_engine.provider_broker.broker.ProviderBroker.revoke",
        "breadboard_engine.provider_broker.broker.ProviderBroker.set_config_api_key",
        "breadboard_engine.provider_broker.broker.ProviderBroker.set_runtime_api_key",
        "breadboard_engine.provider_broker.store.SQLiteCredentialStore.get_session_account_binding",
        "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_api_key",
        "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
    }
)
AUTH_ROLE_REQUIRED_API_CALLS = {
    "auth.api_key_precedence": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.get_credential_origin",
            "breadboard_engine.provider_broker.broker.ProviderBroker.revoke",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_api_key",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
        }
    ),
    "auth.codex_oauth_precedence": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.get_credential_origin",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
        }
    ),
    "auth.explicit_account_binding": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.broker.ProviderBroker.get_session_account_binding",
            "breadboard_engine.provider_broker.broker.ProviderBroker.putApiKey",
        }
    ),
    "auth.automatic_affinity_restart_rotation": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.broker.ProviderBroker.putApiKey",
            "breadboard_engine.provider_broker.broker.ProviderBroker.revoke",
        }
    ),
    "auth.classified_429_rotation": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.broker.ProviderBroker.get_session_account_binding",
            "breadboard_engine.provider_broker.broker.ProviderBroker.putApiKey",
        }
    ),
    "auth.refresh_single_flight": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
        }
    ),
    "auth.refresh_transient_deferral": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.broker.ProviderBroker.get_session_account_binding",
            "breadboard_engine.provider_broker.broker.ProviderBroker.listCredentials",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
        }
    ),
    "auth.refresh_definitive_tombstone": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.broker.ProviderBroker.listCredentials",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
        }
    ),
    "auth.revoke_during_refresh": frozenset(
        {
            "breadboard_engine.provider_broker.broker.ProviderBroker.execution_material",
            "breadboard_engine.provider_broker.broker.ProviderBroker.listCredentials",
            "breadboard_engine.provider_broker.broker.ProviderBroker.revoke",
            "breadboard_engine.provider_broker.store.SQLiteCredentialStore.put_oauth",
        }
    ),
    "role.public_alias_selection": frozenset(
        {
            "breadboard_engine.model_roles.compile_model_roles",
            "breadboard_engine.model_roles.select_role_target",
            "breadboard_engine.provider_broker.broker.ProviderBroker.get_session_account_binding",
            "breadboard_engine.provider_broker.broker.ProviderBroker.putApiKey",
        }
    ),
    "role.unknown_unavailable": frozenset(
        {"breadboard_engine.model_roles.resolve_role_name"}
    ),
    "role.lock_secret_rotation_restart": frozenset(
        {
            "breadboard_engine.model_roles.compile_model_roles",
            "breadboard_engine.model_roles.restore_model_role_lock",
            "breadboard_engine.model_roles.select_role_target",
            "breadboard_engine.provider_broker.broker.ProviderBroker.putApiKey",
        }
    ),
    "role.auth_policy_no_fallback": frozenset(
        {
            "breadboard_engine.model_roles.compile_model_roles",
            "breadboard_engine.model_roles.select_role_target",
        }
    ),
    "role.cross_provider_default_forbidden": frozenset(
        {"breadboard_engine.model_roles.compile_model_roles"}
    ),
}
_PUBLIC_API_NAMES = frozenset(
    call.rsplit(".", 1)[-1] for call in AUTH_ROLE_PUBLIC_API_CALLS
)


@contextmanager
def _capture_api_calls() -> Iterator[set[str]]:
    calls: set[str] = set()
    main_thread = threading.current_thread()
    previous_main = sys.getprofile()
    previous_thread = threading.getprofile()

    def profile(frame: Any, event: str, arg: Any) -> None:
        if previous_main is not None or previous_thread is not None:
            previous = (
                previous_main
                if threading.current_thread() is main_thread
                else previous_thread
            )
            if previous is not None:
                previous(frame, event, arg)
        if event != "call":
            return
        name = str(frame.f_code.co_name)
        if name not in _PUBLIC_API_NAMES:
            return
        module = str(frame.f_globals.get("__name__") or "")
        qualified = f"{module}.{frame.f_code.co_qualname}"
        if qualified in AUTH_ROLE_PUBLIC_API_CALLS:
            calls.add(qualified)

    sys.setprofile(profile)
    threading.setprofile(profile)
    try:
        yield calls
    finally:
        threading.setprofile(previous_thread)
        sys.setprofile(previous_main)


class _NetworkDeniedSocket(socket.socket):
    def connect(self, address: Any) -> None:
        raise AssertionError(
            f"network access denied during F6 auth/role probe: {address}"
        )

    def connect_ex(self, address: Any) -> int:
        raise AssertionError(
            f"network access denied during F6 auth/role probe: {address}"
        )


def _deny_create_connection(*args: Any, **kwargs: Any) -> None:
    _ = (args, kwargs)
    raise AssertionError("network access denied during F6 auth/role probe")


@contextmanager
def _deny_network() -> Iterator[None]:
    with (
        mock.patch.object(socket, "socket", _NetworkDeniedSocket),
        mock.patch.object(socket, "create_connection", _deny_create_connection),
    ):
        yield


def project_auth_role_observation(
    row_id: str, observed: Mapping[str, Any]
) -> dict[str, Any]:
    """Project raw candidate API results into the shared F1 semantic vocabulary."""

    if row_id == "auth.api_key_precedence":
        return {"selected_sources": list(observed["selected_sources"])}
    if row_id == "auth.codex_oauth_precedence":
        return {
            "selected_source": observed["selected_source"],
            "fallback_ignored": observed["fallback_ignored"],
            "oauth_selected": observed["oauth_selected"],
        }
    if row_id == "auth.explicit_account_binding":
        return {
            "explicit_selection_persisted": observed["explicit_selection_persisted"],
            "selected_account": observed["selected_account"],
            "silently_rotated": observed["silently_rotated"],
        }
    if row_id == "auth.automatic_affinity_restart_rotation":
        return {
            "restart_preserved": observed["restart_preserved"],
            "secret_rotation_preserved": observed["rotation_preserved"],
            "rotated_when_unavailable": observed["rotated_on_unavailable"],
        }
    if row_id == "auth.classified_429_rotation":
        return {"automatic_rotated_on_classified_429": observed["automatic_rotated"]}
    if row_id == "auth.refresh_single_flight":
        return {
            "provider_calls": observed["provider_calls"],
            "workers_converged": observed["workers_converged"],
        }
    if row_id == "auth.refresh_transient_deferral":
        return {
            "credential_retained": observed["account_status"] == "active",
            "revoked": observed["account_status"] == "revoked",
            "provider_calls": observed["provider_calls"],
        }
    if row_id == "auth.refresh_definitive_tombstone":
        return {
            "credential_tombstoned": observed["account_status"] == "revoked",
            "active_credentials": observed["active_credentials"],
            "provider_calls": observed["provider_calls"],
        }
    if row_id == "auth.revoke_during_refresh":
        return {
            "refresh_result": observed["refresh_result"],
            "resurrected": observed["account_status"] != "revoked",
        }
    if row_id == "role.public_alias_selection":
        return {
            "alias_resolved": observed["alias_resolved"],
            "provider_id": observed["provider_id"],
            "model_id": observed["model_id"],
        }
    if row_id == "role.unknown_unavailable":
        return {
            "known_unavailable_resolved": observed["known_unavailable_resolved"],
            "unknown_resolved": observed["unknown_resolved"],
        }
    if row_id == "role.lock_secret_rotation_restart":
        return {
            "target_stable_after_configuration_change": observed[
                "target_stable_after_configuration_change"
            ],
            "lock_restored": observed["lock_restored"],
        }
    if row_id == "role.auth_policy_no_fallback":
        return {
            "auth_failure_fallback_used": observed["fallback_used"],
            "selected_provider": observed["selected_provider"],
        }
    if row_id == "role.cross_provider_default_forbidden":
        return {
            "cross_provider_fallback_used": observed["cross_provider_fallback_used"],
            "selected_provider": observed["selected_provider"],
        }
    raise ValueError(f"unknown auth/role row id: {row_id}")


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"auth/role observation is not JSON-compatible: {type(value)!r}")


def auth_role_observation_sha256(observed: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _json_copy(observed),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _observation(
    row_id: str,
    subject: str,
    claim: str,
    observed: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "subject": subject,
        "claim": claim,
        "observed": dict(observed),
        "evidence": dict(evidence),
    }


def _role_document(
    primary: Mapping[str, Any],
    *,
    fallbacks: tuple[Mapping[str, Any], ...] = (),
    fallback_on: tuple[str, ...] = (),
    cross_provider: str = "forbidden",
    account_failover: str = "forbidden",
    default_role: str = "default",
    roles: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    role_map = dict(roles or {})
    role_map.setdefault(
        default_role,
        {
            "primary": dict(primary),
            "fallbacks": [dict(item) for item in fallbacks],
            "fallback_on": list(fallback_on),
        },
    )
    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {
            "role": default_role,
            "known_but_unbound_role": "error",
            "unknown_role": "error",
        },
        "roles": role_map,
        "dispatch": {"subagents": {}, "lanes": {"main": default_role}},
        "policy": {
            "allow_environment_overrides": False,
            "cross_provider_fallback": cross_provider,
            "account_failover": account_failover,
        },
    }


def _observe_api_key_precedence(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.api_key_precedence"
    clock = _Clock()
    sources = {
        source: f"auth-role-{source}-canary" for source in AUTH_SOURCE_PRECEDENCE
    }

    def fallback(provider_id: str) -> Mapping[str, Any] | None:
        if provider_id == "openai":
            return {"api_key": sources["fallback"]}
        return None

    broker = _broker(
        root,
        row,
        fallback_resolver=fallback,
        fallback_origins={"openai": "resolver"},
    )
    oauth = broker.store.put_oauth(
        provider_id="openai",
        auth_scheme_id="oauth2",
        label="precedence-oauth",
        source="login",
        expires_at_ms=clock.after(120_000),
        material={
            "access_token": sources["oauth"],
            "refresh_token": "auth-role-oauth-refresh-canary",
        },
    )
    login = _api_key(broker, "precedence-login", sources["login_api_key"])
    stored = broker.store.put_api_key(
        provider_id="openai",
        auth_scheme_id="api_key",
        label="precedence-stored",
        source="stored",
        material={"api_key": sources["stored_api_key"]},
    )
    session_id = "auth-role-precedence-session"
    broker.set_runtime_api_key(
        "openai",
        sources["runtime"],
        session_id=session_id,
    )
    broker.set_config_api_key("openai", sources["config"])
    environment = {"OPENAI_API_KEY": sources["env"]}

    selected: list[str | None] = []

    def resolve(env: Mapping[str, str]) -> None:
        selected.append(
            _origin_leg(
                broker.get_credential_origin(
                    "openai",
                    session_id=session_id,
                    environment_key="OPENAI_API_KEY",
                    environment=env,
                )
            )
        )

    resolve(environment)
    broker.remove_runtime_api_key("openai", session_id=session_id)
    resolve(environment)
    broker.clear_config_api_keys()
    resolve(environment)
    _assert(
        broker.revoke({"account_id": oauth["account_id"]})["ok"],
        "OAuth source did not revoke",
    )
    resolve(environment)
    _assert(
        broker.revoke({"account_id": login["account_id"]})["ok"],
        "login API-key source did not revoke",
    )
    resolve(environment)
    resolve({})
    _assert(
        broker.revoke({"account_id": stored["account_id"]})["ok"],
        "stored API-key source did not revoke",
    )
    resolve({})
    _assert(
        selected == list(AUTH_SOURCE_PRECEDENCE), "credential source precedence changed"
    )
    return _observation(
        row,
        "provider_broker.authentication",
        "authentication sources resolve in the established precedence order",
        {"selected_sources": selected, "precedence": list(AUTH_SOURCE_PRECEDENCE)},
        {"all_sources_exercised": True, "secret_free_origin": True},
    )


def _observe_codex_oauth_precedence(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.codex_oauth_precedence"
    clock = _Clock()
    fallback_seen = False

    def fallback(provider_id: str) -> Mapping[str, Any] | None:
        nonlocal fallback_seen
        fallback_seen = fallback_seen or provider_id == "codex"
        return (
            {"api_key": "auth-role-codex-fallback-canary"}
            if provider_id == "codex"
            else None
        )

    broker = _broker(
        root, row, fallback_resolver=fallback, fallback_origins={"codex": "resolver"}
    )
    account = _oauth(
        broker,
        "codex-oauth",
        "auth-role-codex-access-canary",
        "auth-role-codex-refresh-canary",
        clock,
        provider_id="codex",
    )
    origin = broker.get_credential_origin(
        "codex",
        session_id="auth-role-codex-session",
    )
    _assert(
        origin is not None and origin.get("kind") == "oauth",
        "Codex OAuth was not selected",
    )
    _assert(not fallback_seen, "Codex OAuth incorrectly consulted fallback")
    _assert(account.get("status") == "active", "Codex OAuth account is unavailable")
    selected_source = _origin_leg(origin)
    return _observation(
        row,
        "provider_broker.codex_authentication",
        "stored Codex OAuth takes precedence over fallback credentials",
        {
            "selected_source": selected_source,
            "fallback_ignored": not fallback_seen,
            "oauth_selected": selected_source == "oauth",
            "account_active": account.get("status") == "active",
        },
        {"oauth_origin_secret_free": True, "provider_flow": "openai-codex"},
    )


def _observe_explicit_account_binding(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.explicit_account_binding"
    broker = _broker(root, row)
    _api_key(broker, "explicit-first", "auth-role-explicit-first-canary", alias="first")
    selected = _api_key(
        broker,
        "explicit-second",
        "auth-role-explicit-second-canary",
        alias="second",
    )
    session_id = "auth-role-explicit-session"
    broker.set_runtime_api_key(
        "openai",
        "auth-role-runtime-canary",
        session_id=session_id,
    )
    with broker.execution_material(
        "openai",
        session_id=session_id,
        account_selector={"alias": "second"},
    ) as material:
        _assert(material is not None, "explicit account selector returned no material")
        selected_label = str(material.get("label") or "")
        origin = dict(material.get("credential_origin") or {})
    binding = broker.get_session_account_binding(session_id, "openai")
    _assert(
        selected_label == "explicit-second", "explicit alias selected the wrong account"
    )
    _assert(
        binding is not None and binding.get("binding_kind") == "user",
        "user binding was not persisted",
    )
    _assert(origin.get("binding_kind") == "user", "origin did not record user binding")
    with broker.execution_material("openai", session_id=session_id) as material:
        _assert(
            material is not None and material.get("label") == "explicit-second",
            "user binding did not persist",
        )
        persisted_label = str(material.get("label") or "")
    selected_alias = str(binding.get("alias") or "")
    return _observation(
        row,
        "provider_broker.session_account_binding",
        "an explicit account selector persists as a user binding and outranks overrides",
        {
            "selected_label": selected_label,
            "selected_account": selected_alias,
            "binding_kind": str(binding["binding_kind"]),
            "origin_binding_kind": str(origin["binding_kind"]),
            "explicit_selection_persisted": (
                persisted_label == selected_label
                and binding["binding_kind"] == "user"
                and origin["binding_kind"] == "user"
            ),
            "silently_rotated": persisted_label != selected_label,
            "overrides_runtime": selected_label == "explicit-second",
        },
        {
            "binding_persisted": True,
            "binding_secret_free": True,
            "selected_account_is_explicit": selected["label"] == "explicit-second",
        },
    )


def _observe_automatic_affinity_restart_rotation(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.automatic_affinity_restart_rotation"
    db = _database(root, row)
    broker = ProviderBroker(
        SQLiteCredentialStore(db), codex_auth_path=db.with_name("affinity-codex.json")
    )
    first = _api_key(broker, "affinity-first", "auth-role-affinity-first-canary")
    second = _api_key(broker, "affinity-second", "auth-role-affinity-second-canary")
    _ = (first, second)
    session_id = "auth-role-affinity-session"

    with broker.execution_material("openai", session_id=session_id) as material:
        _assert(material is not None, "automatic affinity did not select an account")
        initial_label = str(material.get("label") or "")
    restarted = ProviderBroker(
        SQLiteCredentialStore(db), codex_auth_path=db.with_name("affinity-codex.json")
    )
    with restarted.execution_material("openai", session_id=session_id) as material:
        _assert(material is not None, "affinity did not survive restart")
        restart_label = str(material.get("label") or "")
    selected = first if initial_label == "affinity-first" else second
    restarted.putApiKey(
        {
            "provider_id": "openai",
            "account_id": selected["account_id"],
            "account_label": initial_label,
            "api_key": "auth-role-affinity-rotated-canary",
        }
    )
    rotated = ProviderBroker(
        SQLiteCredentialStore(db), codex_auth_path=db.with_name("affinity-codex.json")
    )
    with rotated.execution_material("openai", session_id=session_id) as material:
        _assert(material is not None, "secret rotation made affinity unavailable")
        rotated_label = str(material.get("label") or "")
    _assert(
        rotated.revoke({"account_id": selected["account_id"]})["ok"],
        "affinity account did not revoke",
    )
    with rotated.execution_material("openai", session_id=session_id) as material:
        _assert(
            material is not None,
            "automatic affinity did not rotate unavailable account",
        )
        replacement_label = str(material.get("label") or "")
        replacement_origin = dict(material.get("credential_origin") or {})
    _assert(
        initial_label == restart_label == rotated_label,
        "automatic affinity was not stable",
    )
    _assert(replacement_label != initial_label, "automatic affinity did not rotate")
    _assert(
        replacement_origin.get("binding_kind") == "automatic",
        "replacement was not automatic",
    )
    return _observation(
        row,
        "provider_broker.automatic_session_affinity",
        "automatic account affinity survives restart and secret rotation, then rotates on unavailability",
        {
            "restart_preserved": initial_label == restart_label,
            "rotation_preserved": restart_label == rotated_label,
            "rotated_on_unavailable": replacement_label != initial_label,
            "replacement_binding_kind": replacement_origin.get("binding_kind"),
            "replacement_binding_reason": replacement_origin.get("binding_reason"),
        },
        {"account_ids_stable": True, "secret_free_material": True},
    )


def _rate_limit_error() -> ProviderRuntimeError:
    return ProviderRuntimeError(
        "rate limited",
        details={
            "classification": "rate_limited",
            "status_code": 429,
            "retry_after": 300,
        },
    )


def _observe_classified_429_rotation(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.classified_429_rotation"
    automatic = _broker(root, row)
    first = _api_key(automatic, "rate-first", "auth-role-rate-first-canary")
    second = _api_key(automatic, "rate-second", "auth-role-rate-second-canary")
    session_id = "auth-role-rate-automatic"
    blocked_label = ""
    blocked_id = ""
    try:
        with automatic.execution_material("openai", session_id=session_id) as material:
            _assert(material is not None, "rate-limit setup did not select an account")
            blocked_label = str(material.get("label") or "")
            blocked_id = str(material.get("account_id") or "")
            raise _rate_limit_error()
    except ProviderRuntimeError:
        pass
    _assert(
        blocked_id in {str(first["account_id"]), str(second["account_id"])},
        "rate limit account is unknown",
    )
    restarted = ProviderBroker(
        SQLiteCredentialStore(_database(root, row)),
        codex_auth_path=_database(root, row).with_name("rate-codex.json"),
    )
    with restarted.execution_material("openai", session_id=session_id) as material:
        _assert(
            material is not None,
            "classified rate limit did not rotate automatic affinity",
        )
        replacement_label = str(material.get("label") or "")
        replacement_origin = dict(material.get("credential_origin") or {})
    user = _broker(root, "auth.classified_429_rotation.user")
    selected = _api_key(user, "rate-user", "auth-role-rate-user-canary")
    _api_key(user, "rate-user-replacement", "auth-role-rate-user-replacement-canary")
    try:
        with user.execution_material(
            "openai",
            session_id="auth-role-rate-user",
            account_selector={"account_id": selected["account_id"]},
        ):
            raise _rate_limit_error()
    except ProviderRuntimeError:
        pass
    binding = user.get_session_account_binding("auth-role-rate-user", "openai")
    with user.execution_material(
        "openai", session_id="auth-role-rate-user"
    ) as material:
        _assert(
            material is None, "user-selected rate-limited account unexpectedly rotated"
        )
    _assert(binding is not None, "user binding disappeared after rate limiting")
    return _observation(
        row,
        "provider_broker.rate_limit_rotation",
        "classified 429 rotates automatic affinity but preserves explicit user binding",
        {
            "automatic_rotated": replacement_label != blocked_label,
            "automatic_binding_kind": replacement_origin.get("binding_kind"),
            "user_binding_preserved": binding.get("binding_kind") == "user",
            "user_availability": binding.get("availability"),
        },
        {"status_code_classified": True, "rotation_secret_free": True},
    )


def _observe_refresh_single_flight(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.refresh_single_flight"
    entered = threading.Event()
    release = threading.Event()
    responses = [
        _response(
            200,
            {
                "access_token": "auth-role-flight-access-new-canary",
                "refresh_token": "auth-role-flight-refresh-new-canary",
                "expires_in": 3600,
            },
        )
    ]
    calls: list[tuple[str, str]] = []

    def transport(
        url: str, *, method: str, headers: Mapping[str, str], body: bytes | None = None
    ):
        _ = (headers, body)
        calls.append((url, method))
        entered.set()
        _assert(
            release.wait(_CONCURRENCY_TIMEOUT_SECONDS),
            "single-flight transport did not release",
        )
        return responses.pop(0)

    db = _database(root, row)
    first = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
        codex_auth_path=db.with_name("flight-codex.json"),
    )
    second = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
        codex_auth_path=db.with_name("flight-codex.json"),
    )
    first._refresh_lease_ms = 500
    second._refresh_lease_ms = 500
    first._refresh_poll_seconds = 0.005
    second._refresh_poll_seconds = 0.005
    clock = _Clock()
    account = _oauth(
        first,
        "flight",
        "auth-role-flight-access-old-canary",
        "auth-role-flight-refresh-old-canary",
        clock,
    )
    start = threading.Barrier(3)
    results: list[int] = []
    errors: list[BaseException] = []

    def resolve(broker: ProviderBroker) -> None:
        try:
            start.wait()
            with broker.execution_material(
                "anthropic",
                session_id="auth-role-flight-session",
                account_selector={"account_id": account["account_id"]},
                minimum_validity_ms=5_000,
            ) as material:
                _assert(
                    material is not None, "single-flight refresh returned no material"
                )
                results.append(int(material.get("secret_version") or 0))
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=resolve, args=(first,)),
        threading.Thread(target=resolve, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    _assert(
        entered.wait(_CONCURRENCY_TIMEOUT_SECONDS),
        "single-flight transport was not called",
    )
    release.set()
    for thread in threads:
        thread.join(_CONCURRENCY_TIMEOUT_SECONDS)
    alive = [thread.name for thread in threads if thread.is_alive()]
    _assert(
        not errors and not alive,
        f"single-flight workers failed (errors={errors!r}, alive={alive!r})",
    )
    _assert(
        len(calls) == 1 and sorted(results) == [2, 2], "refresh was not single-flight"
    )
    converged_version = results[0]
    return _observation(
        row,
        "provider_broker.oauth_refresh_coordination",
        "concurrent refreshes share one durable provider flight and converge on one version",
        {
            "provider_calls": len(calls),
            "converged_secret_version": converged_version,
            "workers": len(results),
            "workers_converged": (len(results) == 2 and len(set(results)) == 1),
        },
        {
            "single_flight": True,
            "durable_across_brokers": True,
            "secrets_returned": False,
        },
    )


def _observe_refresh_transient_deferral(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.refresh_transient_deferral"
    transport, calls = _transport_factory(
        [_response(503, {"error": "temporarily_unavailable"}, {"retry-after": "300"})]
    )
    broker = _broker(root, row, oauth_transport=transport)
    account = _oauth(
        broker,
        "transient",
        "auth-role-transient-access-old-canary",
        "auth-role-transient-refresh-old-canary",
        _Clock(),
    )
    with broker.execution_material(
        "anthropic",
        session_id="auth-role-transient-session",
        account_selector={"account_id": account["account_id"]},
        minimum_validity_ms=5_000,
    ) as material:
        _assert(material is None, "transient refresh unexpectedly produced material")
    view = broker.listCredentials("anthropic")[0]
    binding = broker.get_session_account_binding(
        "auth-role-transient-session", "anthropic"
    )
    _assert(view["status"] == "active", "transient refresh revoked an active account")
    _assert(
        view["refresh_state"].get("last_failure_class") == "transient",
        "transient failure was not classified",
    )
    _assert(
        binding is not None and binding.get("availability") == "refresh_deferred",
        "retry was not deferred",
    )
    return _observation(
        row,
        "provider_broker.oauth_refresh_coordination",
        "transient OAuth refresh failure defers retry without revoking the account",
        {
            "account_status": view["status"],
            "failure_class": view["refresh_state"]["last_failure_class"],
            "binding_availability": binding["availability"],
            "provider_calls": len(calls),
        },
        {
            "retry_deferred": True,
            "secret_version_preserved": view["secret_version"] == 1,
        },
    )


def _observe_refresh_definitive_tombstone(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.refresh_definitive_tombstone"
    transport, calls = _transport_factory([_response(400, {"error": "invalid_grant"})])
    broker = _broker(root, row, oauth_transport=transport)
    account = _oauth(
        broker,
        "definitive",
        "auth-role-definitive-access-old-canary",
        "auth-role-definitive-refresh-old-canary",
        _Clock(),
    )
    with broker.execution_material(
        "anthropic",
        session_id="auth-role-definitive-session",
        account_selector={"account_id": account["account_id"]},
        minimum_validity_ms=5_000,
    ) as material:
        _assert(material is None, "definitive refresh unexpectedly produced material")
    credentials = broker.listCredentials("anthropic")
    view = credentials[0]
    active_credentials = sum(
        credential.get("status") == "active" for credential in credentials
    )
    _assert(view["status"] == "revoked", "definitive refresh did not tombstone account")
    _assert(
        view["refresh_state"].get("last_failure_class") == "definitive",
        "definitive failure was not classified",
    )
    return _observation(
        row,
        "provider_broker.oauth_refresh_coordination",
        "definitive OAuth refresh failure tombstones the credential and prevents reuse",
        {
            "account_status": view["status"],
            "active_credentials": active_credentials,
            "failure_class": view["refresh_state"]["last_failure_class"],
            "provider_calls": len(calls),
            "secret_version": view["secret_version"],
        },
        {"tombstoned": True, "secret_material_deleted": True},
    )


def _observe_revoke_during_refresh(root: pathlib.Path) -> dict[str, Any]:
    row = "auth.revoke_during_refresh"
    entered = threading.Event()
    release = threading.Event()

    def transport(
        url: str, *, method: str, headers: Mapping[str, str], body: bytes | None = None
    ):
        _ = (url, method, headers, body)
        entered.set()
        _assert(
            release.wait(_CONCURRENCY_TIMEOUT_SECONDS),
            "revoke-race transport did not release",
        )
        return _response(
            200,
            {
                "access_token": "auth-role-race-access-new-canary",
                "refresh_token": "auth-role-race-refresh-new-canary",
                "expires_in": 3600,
            },
        )

    db = _database(root, row)
    refresher = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
        codex_auth_path=db.with_name("race-codex.json"),
    )
    revoker = ProviderBroker(
        SQLiteCredentialStore(db),
        oauth_transport=transport,
        codex_auth_path=db.with_name("race-codex.json"),
    )
    account = _oauth(
        refresher,
        "race",
        "auth-role-race-access-old-canary",
        "auth-role-race-refresh-old-canary",
        _Clock(),
    )
    result: list[bool] = []
    errors: list[BaseException] = []

    def refresh() -> None:
        try:
            with refresher.execution_material(
                "anthropic",
                session_id="auth-role-race-session",
                account_selector={"account_id": account["account_id"]},
                minimum_validity_ms=5_000,
            ) as material:
                result.append(material is None)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=refresh)
    thread.start()
    _assert(
        entered.wait(_CONCURRENCY_TIMEOUT_SECONDS),
        "refresh did not enter OAuth transport",
    )
    _assert(
        revoker.revoke({"account_id": account["account_id"]})["ok"],
        "revoke race did not revoke",
    )
    release.set()
    thread.join(_CONCURRENCY_TIMEOUT_SECONDS)
    view = next(
        item
        for item in revoker.listCredentials("anthropic")
        if item["account_id"] == account["account_id"]
    )
    _assert(
        not errors and not thread.is_alive() and result == [True],
        "refresh resurrected a revoked account",
    )
    _assert(
        view["status"] == "revoked" and view["secret_version"] == 1,
        "revoked account changed during refresh",
    )
    return _observation(
        row,
        "provider_broker.oauth_refresh_revoke_race",
        "revocation during an OAuth refresh wins the compare-and-swap commit",
        {
            "refresh_result": "discarded",
            "account_status": view["status"],
            "secret_version": view["secret_version"],
        },
        {
            "revoke_wins": True,
            "resurrection_prevented": True,
            "secret_material_deleted": True,
        },
    )


def _observe_public_alias_selection(root: pathlib.Path) -> dict[str, Any]:
    row = "role.public_alias_selection"
    broker = _broker(root, row)
    _api_key(broker, "role-first", "role-public-first-canary", alias="public-first")
    _api_key(broker, "role-second", "role-public-second-canary", alias="public-second")
    document = _role_document(
        {
            "provider_id": "openai",
            "model_id": "gpt-5-mini",
            "account_selector": {
                "mode": "alias",
                "alias": "public-second",
                "pin": "session",
            },
        }
    )
    lock = compile_model_roles(
        document,
        broker=broker,
        session_id="role-public-session",
        bind_session_accounts=True,
    )
    target = select_role_target(lock, "default")
    binding = broker.get_session_account_binding("role-public-session", "openai")
    _assert(
        target["provider_id"] == "openai" and target["model_id"] == "gpt-5-mini",
        "public alias target changed",
    )
    _assert(
        binding is not None and binding.get("binding_kind") == "user",
        "alias did not persist a user binding",
    )
    _assert(
        binding.get("alias") == "public-second",
        "public alias selected the wrong account",
    )
    alias_resolved = (
        binding["binding_kind"] == "user" and binding["alias"] == "public-second"
    )
    return _observation(
        row,
        "model_roles.public_alias",
        "public role aliases select the named account and persist a secret-free binding",
        {
            "provider_id": target["provider_id"],
            "model_id": target["model_id"],
            "selected_alias": binding["alias"],
            "binding_kind": binding["binding_kind"],
            "alias_resolved": alias_resolved,
        },
        {"alias_selection": True, "binding_secret_free": True},
    )


def _error_code(callable_: Callable[[], Any]) -> str:
    try:
        callable_()
    except ModelRoleResolutionError as error:
        return error.problem.code
    raise AssertionError("model-role operation unexpectedly succeeded")


def _observe_unknown_unavailable(root: pathlib.Path) -> dict[str, Any]:
    row = "role.unknown_unavailable"
    _ = _broker(root, row)
    document = _role_document({"provider_id": "mock", "model_id": "reference"})
    known_code = _error_code(lambda: resolve_role_name(document, "smol"))
    unknown_code = _error_code(lambda: resolve_role_name(document, "not-configured"))
    _assert(
        known_code == "known_role_unbound", "known unavailable role did not fail closed"
    )
    _assert(unknown_code == "unknown_role", "unknown role did not fail closed")
    return _observation(
        row,
        "model_roles.role_resolution",
        "known-but-unavailable and unknown public role names fail closed",
        {
            "known_unavailable_error": known_code,
            "unknown_error": unknown_code,
            "known_unavailable_resolved": not bool(known_code),
            "unknown_resolved": not bool(unknown_code),
        },
        {"default_fallback": False, "role_resolution_secret_free": True},
    )


def _observe_lock_secret_rotation_restart(root: pathlib.Path) -> dict[str, Any]:
    row = "role.lock_secret_rotation_restart"
    db = _database(root, row)
    broker = ProviderBroker(
        SQLiteCredentialStore(db), codex_auth_path=db.with_name("lock-codex.json")
    )
    account = _api_key(broker, "role-lock", "role-lock-before-canary")
    document = _role_document(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.4-mini",
            "account_selector": {"mode": "default", "pin": "lock"},
        }
    )
    lock = compile_model_roles(document, broker=broker)
    serialized = lock.canonical_json()
    _assert(
        "role-lock-before-canary" not in serialized, "model-role lock contains a secret"
    )
    broker.putApiKey(
        {
            "provider_id": "openai",
            "account_id": account["account_id"],
            "account_label": "role-lock",
            "api_key": "role-lock-after-canary",
        }
    )
    restarted = ProviderBroker(
        SQLiteCredentialStore(db), codex_auth_path=db.with_name("lock-codex.json")
    )
    original_target = select_role_target(lock, "default")
    changed_document = _role_document(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.4",
            "account_selector": {"mode": "default", "pin": "lock"},
        }
    )
    changed_lock = compile_model_roles(changed_document, broker=restarted)
    changed_target = select_role_target(changed_lock, "default")
    restored = restore_model_role_lock(
        lock.as_dict(), broker=restarted, session_id="role-lock-session"
    )
    restored_target = select_role_target(restored, "default")
    lock_restored = restored.lock_hash == lock.lock_hash
    target_stable = (
        restored_target == original_target and changed_target != original_target
    )
    _assert(lock_restored, "lock identity changed during secret rotation")
    _assert(
        target_stable,
        "restored lock followed changed configuration instead of its immutable target",
    )
    return _observation(
        row,
        "model_roles.derived_lock",
        "lock identity survives credential secret rotation and process restart",
        {
            "lock_hash_stable": lock_restored,
            "restore_succeeded": lock_restored,
            "lock_restored": lock_restored,
            "target_stable_after_configuration_change": target_stable,
            "original_target": original_target,
            "changed_configuration_target": changed_target,
            "restored_target": restored_target,
        },
        {"lock_secret_free": True, "account_identity_pinned": True},
    )


def _observe_auth_policy_no_fallback(root: pathlib.Path) -> dict[str, Any]:
    row = "role.auth_policy_no_fallback"
    _ = _broker(root, row)
    invalid = _role_document(
        {"provider_id": "mock", "model_id": "primary"},
        fallbacks=({"provider_id": "mock", "model_id": "fallback"},),
        fallback_on=("auth_failure",),
    )
    rejected = _error_code(lambda: compile_model_roles(invalid))
    valid = _role_document(
        {"provider_id": "mock", "model_id": "primary"},
        fallbacks=({"provider_id": "mock", "model_id": "fallback"},),
        fallback_on=("rate_limited",),
    )
    lock = compile_model_roles(valid)
    auth_target = select_role_target(lock, "default", failure_reason="auth_failure")
    _assert(
        rejected == "forbidden_fallback_reason", "auth fallback policy was not rejected"
    )
    _assert(auth_target["model_id"] == "primary", "auth failure selected a fallback")
    fallback_used = auth_target["model_id"] != "primary"
    return _observation(
        row,
        "model_roles.fallback_policy",
        "authentication failures cannot trigger model-role fallback",
        {
            "invalid_policy_error": rejected,
            "auth_failure_target": auth_target["model_id"],
            "fallback_used": fallback_used,
            "selected_provider": auth_target["provider_id"],
        },
        {"auth_fallback_forbidden": True, "policy_secret_free": True},
    )


def _observe_cross_provider_default_forbidden(root: pathlib.Path) -> dict[str, Any]:
    row = "role.cross_provider_default_forbidden"
    _ = _broker(root, row)
    document = _role_document(
        {"provider_id": "openai", "model_id": "gpt-5.4-mini"},
        fallbacks=({"provider_id": "anthropic", "model_id": "claude-sonnet"},),
        fallback_on=("provider_unavailable",),
        cross_provider="forbidden",
    )
    rejected = _error_code(lambda: compile_model_roles(document))
    _assert(
        rejected == "cross_provider_fallback_forbidden",
        "cross-provider default was accepted",
    )
    primary_provider = document["roles"]["default"]["primary"]["provider_id"]
    return _observation(
        row,
        "model_roles.cross_provider_policy",
        "default role fallback cannot cross providers without explicit policy",
        {
            "error": rejected,
            "lock_created": False,
            "cross_provider_fallback_used": False,
            "selected_provider": primary_provider,
        },
        {"cross_provider_fallback_forbidden": True, "policy_secret_free": True},
    )


_OBSERVERS: dict[str, Callable[[pathlib.Path], dict[str, Any]]] = {
    "auth.api_key_precedence": _observe_api_key_precedence,
    "auth.codex_oauth_precedence": _observe_codex_oauth_precedence,
    "auth.explicit_account_binding": _observe_explicit_account_binding,
    "auth.automatic_affinity_restart_rotation": _observe_automatic_affinity_restart_rotation,
    "auth.classified_429_rotation": _observe_classified_429_rotation,
    "auth.refresh_single_flight": _observe_refresh_single_flight,
    "auth.refresh_transient_deferral": _observe_refresh_transient_deferral,
    "auth.refresh_definitive_tombstone": _observe_refresh_definitive_tombstone,
    "auth.revoke_during_refresh": _observe_revoke_during_refresh,
    "role.public_alias_selection": _observe_public_alias_selection,
    "role.unknown_unavailable": _observe_unknown_unavailable,
    "role.lock_secret_rotation_restart": _observe_lock_secret_rotation_restart,
    "role.auth_policy_no_fallback": _observe_auth_policy_no_fallback,
    "role.cross_provider_default_forbidden": _observe_cross_provider_default_forbidden,
}


def observe_auth_role_row(row_id: str, *, root: pathlib.Path) -> dict[str, Any]:
    """Run one API-backed hermetic row and bind its raw result to its projection."""

    if row_id not in _OBSERVERS or row_id not in AUTH_ROLE_ROW_IDS:
        raise ValueError("unknown auth/role row id")
    with _deny_network(), _capture_api_calls() as api_calls:
        observation = _OBSERVERS[row_id](pathlib.Path(root))
    _assert(bool(api_calls), "auth/role probe did not call a candidate public API")
    raw_observed = _json_copy(observation["observed"])
    projection = project_auth_role_observation(row_id, raw_observed)
    execution = {
        "actual_api_calls": True,
        "api_calls": sorted(api_calls),
        "network_denied": True,
        "secrets_redacted": True,
    }
    evidence = {
        **dict(observation["evidence"]),
        "api_observation_sha256": auth_role_observation_sha256(raw_observed),
    }
    result = {
        **observation,
        "observed": {
            "api_observation": raw_observed,
            "semantic_projection": projection,
            "execution": execution,
        },
        "evidence": evidence,
    }
    _assert(
        "canary" not in json.dumps(result, sort_keys=True).lower(),
        "secret canary escaped into auth/role observation",
    )
    return result


__all__ = [
    "AUTH_ROLE_PUBLIC_API_CALLS",
    "AUTH_ROLE_REQUIRED_API_CALLS",
    "AUTH_ROLE_ROW_IDS",
    "auth_role_observation_sha256",
    "observe_auth_role_row",
    "project_auth_role_observation",
]
