"""Model-role resolution and immutable derived locks.

Role policy is compiled before a session starts.  The resulting record embeds
model/provider behavior and broker account *identities* only; it never embeds
credential material.  A session can therefore rotate a broker secret without
changing the lock that authorized its model behavior.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from breadboard.product.harness.lock import _copy, graph_content_hash, sha256_json


_FALLBACK_REASONS = frozenset(
    {
        "provider_unavailable",
        "rate_limited",
        "model_unavailable",
        "capability_drift",
        "timeout_before_output",
    }
)
_FORBIDDEN_FALLBACK_REASONS = frozenset({"auth_failure", "policy_rejection"})


@dataclass(frozen=True, slots=True)
class ModelRoleProblem:
    """Typed problem returned by role resolution instead of a secret-bearing error."""

    code: str
    message: str
    path: str = "$"
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.problem.v1",
            "error_code": self.code,
            "message": self.message,
            "path": self.path,
            "details": dict(self.details),
        }


class ModelRoleResolutionError(ValueError):
    def __init__(self, problem: ModelRoleProblem):
        super().__init__(problem.message)
        self.problem = problem


@dataclass(frozen=True, slots=True, init=False)
class DerivedModelRoleLock(Mapping[str, Any]):
    """Recursively immutable role lock with a stable canonical hash."""

    _record: Mapping[str, Any] = field(repr=False)

    @classmethod
    def _from_record(cls, record: Mapping[str, Any]) -> "DerivedModelRoleLock":
        instance = object.__new__(cls)
        object.__setattr__(instance, "_record", _copy(record, freeze=True))
        return instance

    def __getitem__(self, key: str) -> Any:
        return self._record[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._record)

    def __len__(self) -> int:
        return len(self._record)

    def as_dict(self) -> dict[str, Any]:
        return _copy(self._record, freeze=False)

    @property
    def lock_hash(self) -> str:
        return str(self._record["lock_hash"])

    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), allow_nan=False, sort_keys=True, separators=(",", ":"))

    def role(self, role: str, *, failure_reason: str | None = None) -> dict[str, Any]:
        return select_role_target(self, role, failure_reason=failure_reason)


def _problem(code: str, message: str, path: str = "$", **details: Any) -> ModelRoleResolutionError:
    return ModelRoleResolutionError(ModelRoleProblem(code, message, path, details))


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _problem("invalid_model_roles", f"{path} must be an object", path)
    try:
        copied = _copy(value, freeze=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise _problem("invalid_model_roles", f"{path} is not canonical JSON", path, reason=str(error)) from None
    return dict(copied)


def _target(value: Any, path: str) -> dict[str, Any]:
    if isinstance(value, str):
        raw = value.strip()
        if "/" not in raw:
            raise _problem("invalid_model_target", "model target string must be provider/model", path)
        provider_id, model_id = raw.split("/", 1)
        value = {"provider_id": provider_id, "model_id": model_id}
    target = _mapping(value, path)
    provider_id = str(target.get("provider_id") or "").strip().lower()
    model_id = str(target.get("model_id") or "").strip()
    if not provider_id or not model_id:
        raise _problem("invalid_model_target", "provider_id and model_id are required", path)
    target["provider_id"] = provider_id
    target["model_id"] = model_id
    for key in ("model_revision", "endpoint_id", "auth_scheme_id"):
        if key in target and target[key] is not None:
            target[key] = str(target[key])
    selector = target.get("account_selector")
    if selector is not None:
        target["account_selector"] = _mapping(selector, f"{path}.account_selector")
    return target


def _select_account_id(target: Mapping[str, Any], broker: Any) -> str | None:
    if broker is None:
        return None
    selector = target.get("account_selector")
    selector = selector if isinstance(selector, Mapping) else {}
    provider_id = str(target["provider_id"])
    try:
        credentials = broker.listCredentials(provider_id)
    except Exception:
        return None
    active = [item for item in credentials if isinstance(item, Mapping) and item.get("status") == "active"]
    if selector.get("mode") == "alias":
        alias = str(selector.get("alias") or "")
        active = [item for item in active if item.get("alias") == alias or item.get("label") == alias]
    if not active:
        return None
    account_id = active[0].get("account_id")
    return str(account_id) if account_id else None


def _target_with_account(target: Mapping[str, Any], broker: Any, path: str) -> dict[str, Any]:
    resolved = _target(target, path)
    account_id = _select_account_id(resolved, broker)
    if account_id:
        resolved["account_id"] = account_id
    explicit = resolved.get("account_id")
    if explicit is not None:
        resolved["account_id"] = str(explicit)
    return resolved


def _binding(value: Any, path: str, broker: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and "primary" not in value and "provider_id" in value:
        value = {"primary": value, "fallbacks": [], "fallback_on": []}
    binding = _mapping(value, path)
    if "primary" not in binding:
        raise _problem("invalid_role_binding", "primary is required", path)
    primary = _target_with_account(binding["primary"], broker, f"{path}.primary")
    fallbacks_raw = binding.get("fallbacks", [])
    if not isinstance(fallbacks_raw, (list, tuple)):
        raise _problem("invalid_role_binding", "fallbacks must be an array", f"{path}.fallbacks")
    fallbacks = [_target_with_account(item, broker, f"{path}.fallbacks[{index}]") for index, item in enumerate(fallbacks_raw)]
    for index, fallback in enumerate(fallbacks):
        if fallback["provider_id"] != primary["provider_id"]:
            raise _problem(
                "cross_provider_fallback_forbidden",
                "fallbacks must remain on the primary provider",
                f"{path}.fallbacks[{index}].provider_id",
                primary_provider=primary["provider_id"],
                fallback_provider=fallback["provider_id"],
            )
    reasons = binding.get("fallback_on", [])
    if not isinstance(reasons, (list, tuple)) or any(not isinstance(reason, str) for reason in reasons):
        raise _problem("invalid_fallback_policy", "fallback_on must be an array of reason strings", f"{path}.fallback_on")
    if any(reason in _FORBIDDEN_FALLBACK_REASONS for reason in reasons):
        raise _problem("forbidden_fallback_reason", "fallback is forbidden for auth failure and policy rejection", f"{path}.fallback_on")
    unknown = [reason for reason in reasons if reason not in _FALLBACK_REASONS]
    if unknown:
        raise _problem("invalid_fallback_policy", "fallback reason is not supported", f"{path}.fallback_on", reasons=unknown)
    binding["primary"] = primary
    binding["fallbacks"] = fallbacks
    binding["fallback_on"] = list(dict.fromkeys(reasons))
    return binding


def resolve_role_name(document: Mapping[str, Any], requested_role: str | None = None) -> str:
    roles = document.get("roles") if isinstance(document.get("roles"), Mapping) else {}
    defaults = document.get("defaults") if isinstance(document.get("defaults"), Mapping) else {}
    role = str(requested_role or defaults.get("role") or "").strip()
    if role in roles:
        return role
    if requested_role and str(defaults.get("known_but_unbound_role")) == "use_default":
        default_role = str(defaults.get("role") or "").strip()
        if default_role in roles:
            return default_role
    if requested_role:
        raise _problem("unknown_role", f"unknown model role: {role}", "$.roles", role=role)
    raise _problem("known_role_unbound", "the default role is not bound", "$.defaults.role", role=role)


def compile_model_roles(
    document: Mapping[str, Any],
    *,
    broker: Any = None,
    role_overrides: Mapping[str, Any] | None = None,
    session_started: bool = False,
    lock_id: str | None = None,
) -> DerivedModelRoleLock:
    """Resolve a ``bb.model_roles.v1`` document into a frozen role lock."""
    root = _mapping(document, "model_roles")
    if root.get("schema_version") != "bb.model_roles.v1":
        raise _problem("invalid_model_roles", "schema_version must be bb.model_roles.v1", "$.schema_version")
    defaults = _mapping(root.get("defaults"), "$.defaults")
    roles_raw = root.get("roles")
    if not isinstance(roles_raw, Mapping) or not roles_raw:
        raise _problem("invalid_model_roles", "roles must be a non-empty object", "$.roles")
    dispatch = _mapping(root.get("dispatch"), "$.dispatch")
    for key in ("subagents", "lanes"):
        if not isinstance(dispatch.get(key), Mapping):
            raise _problem("invalid_model_roles", f"dispatch.{key} must be an object", f"$.dispatch.{key}")
    policy = _mapping(root.get("policy", {}), "$.policy")
    if policy.get("cross_provider_fallback") not in (None, "forbidden", "explicit_only"):
        raise _problem("invalid_model_roles", "invalid cross_provider_fallback policy", "$.policy.cross_provider_fallback")
    if policy.get("account_failover") not in (None, "forbidden", "explicit_only"):
        raise _problem("invalid_model_roles", "invalid account_failover policy", "$.policy.account_failover")
    if session_started and role_overrides:
        raise _problem("lock_immutable", "model-role overrides are rejected after session.start", "$.role_overrides")
    if role_overrides is not None and not isinstance(role_overrides, Mapping):
        raise _problem("invalid_role_override", "role_overrides must be an object", "$.role_overrides")
    broker = broker
    if broker is None:
        try:
            from breadboard_engine.provider_broker import get_provider_broker

            broker = get_provider_broker()
        except Exception:
            broker = None
    bindings: dict[str, Any] = {}
    for role, raw_binding in roles_raw.items():
        if not isinstance(role, str) or not role.strip():
            raise _problem("invalid_model_roles", "role names must be non-empty strings", "$.roles")
        value = role_overrides.get(role, raw_binding) if isinstance(role_overrides, Mapping) and role in role_overrides else raw_binding
        bindings[role] = _binding(value, f"$.roles.{role}", broker)
    default_role = str(defaults.get("role") or "")
    if default_role not in bindings:
        if defaults.get("known_but_unbound_role") == "use_default":
            raise _problem("known_role_unbound", "default role is not bound", "$.defaults.role", role=default_role)
        raise _problem("unknown_role", "defaults.role must name a bound role", "$.defaults.role", role=default_role)
    record: dict[str, Any] = {
        "schema_version": "bb.effective_model_role_lock.v1",
        "lock_id": lock_id or "model_role_lock",
        "defaults": {
            "role": default_role,
            "known_but_unbound_role": defaults.get("known_but_unbound_role", "error"),
            "unknown_role": "error",
        },
        "roles": {role: bindings[role] for role in sorted(bindings)},
        "dispatch": {
            "subagents": {str(key): str(value) for key, value in sorted(dispatch["subagents"].items())},
            "lanes": {str(key): str(value) for key, value in sorted(dispatch["lanes"].items())},
        },
        "policy": policy,
        "source_model_roles_hash": sha256_json(root),
        "lock_hash": None,
    }
    if lock_id is None:
        record["lock_id"] = f"model_role_lock:{sha256_json(root)[7:23]}"
    record["lock_hash"] = None
    record_for_hash = _copy(record, freeze=False)
    record_for_hash["lock_hash"] = None
    record["lock_hash"] = sha256_json(record_for_hash)
    return DerivedModelRoleLock._from_record(record)


def derive_model_role_lock(*args: Any, **kwargs: Any) -> DerivedModelRoleLock:
    return compile_model_roles(*args, **kwargs)


def select_role_target(
    lock: Mapping[str, Any], role: str, *, failure_reason: str | None = None
) -> dict[str, Any]:
    roles = lock.get("roles") if isinstance(lock.get("roles"), Mapping) else {}
    if role not in roles:
        raise _problem("unknown_role", f"unknown model role: {role}", "$.roles", role=role)
    binding = roles[role]
    primary = dict(binding["primary"])
    if failure_reason in _FORBIDDEN_FALLBACK_REASONS or failure_reason not in binding.get("fallback_on", []):
        return primary
    fallbacks = binding.get("fallbacks", [])
    return dict(fallbacks[0]) if fallbacks else primary


def embed_model_role_lock(runtime_graph: Mapping[str, Any], role_lock: DerivedModelRoleLock) -> dict[str, Any]:
    """Embed the role map in an effective config graph and recompute its hash."""
    graph = _copy(runtime_graph, freeze=False)
    graph["model_role_lock"] = role_lock.as_dict()
    graph["graph_hash"] = graph_content_hash(graph)
    return graph


__all__ = [
    "DerivedModelRoleLock",
    "ModelRoleProblem",
    "ModelRoleResolutionError",
    "compile_model_roles",
    "derive_model_role_lock",
    "embed_model_role_lock",
    "resolve_role_name",
    "select_role_target",
]
