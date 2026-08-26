"""Strict model-role compilation, immutable locks, and restoration.

The source ``bb.model_roles.v1`` document is the policy input.  Compilation
validates that document with its checked-in Draft 2020-12 schema, resolves
exact provider/model routes, and emits a closed effective lock.  Locks carry
only account identities and session binding references; credential material is
owned by the provider broker and never enters this module's output.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from breadboard.product.harness.lock import _copy, graph_content_hash, sha256_json


_PUBLIC_ROLES = frozenset({"default", "smol", "slow", "vision", "plan", "designer", "task"})
_INTERNAL_ROLES = frozenset({"commit", "tiny", "advisor"})
_SYNTHETIC_PROVIDERS = frozenset({"mock", "cli_mock", "smoke", "replay"})
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
_SECRET_KEYS = frozenset(
    {
        "credential_id",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "key",
        "version",
        "secret_version",
    }
)


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

    def role(self, role: str, *, failure_reason: str | Any = None) -> dict[str, Any]:
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


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.strip().lower() in _SECRET_KEYS:
                raise _problem(
                    "secret_material_forbidden",
                    "credential material and credential-version identifiers are forbidden in model-role records",
                    f"{path}.{key}",
                )
            _reject_secret_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "kernel"
        / "schemas"
        / "bb.kernel.common.v1.schema.json"
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
    except Exception as error:
        raise RuntimeError(
            f"common kernel schema is unavailable: {schema_path}"
        ) from error
    return Registry().with_resources(
        (
            (schema_path.name, resource),
            (str(schema["$id"]), resource),
        )
    )


@lru_cache(maxsize=1)
def _source_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / "kernel" / "schemas" / "bb.model_roles.v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise RuntimeError(f"model-role source schema is unavailable: {schema_path}") from error
    return Draft202012Validator(schema, registry=_schema_registry())


@lru_cache(maxsize=1)
def _effective_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / "kernel" / "schemas" / "bb.effective_model_role_lock.v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise RuntimeError(f"effective model-role schema is unavailable: {schema_path}") from error
    return Draft202012Validator(schema, registry=_schema_registry())


def _pointer(error: Any) -> str:
    path = tuple(str(item) for item in error.absolute_path)
    return "/" + "/".join(path) if path else "/"


def _validate_source(value: Mapping[str, Any]) -> dict[str, Any]:
    root = _mapping(value, "model_roles")
    _reject_secret_keys(root, "$")
    for role, binding in (root.get("roles") or {}).items():
        if not isinstance(binding, Mapping):
            continue
        reasons = binding.get("fallback_on")
        if isinstance(reasons, Sequence) and any(
            reason in _FORBIDDEN_FALLBACK_REASONS for reason in reasons
        ):
            raise _problem(
                "forbidden_fallback_reason",
                "fallback is forbidden for auth failure and policy rejection",
                f"$.roles.{role}.fallback_on",
            )
    errors = sorted(_source_validator().iter_errors(root), key=lambda item: tuple(str(part) for part in item.absolute_path))
    if errors:
        error = errors[0]
        error_path = tuple(str(part) for part in error.absolute_path)
        if error_path and error_path[-1] == "fallback_on":
            try:
                reasons = root["roles"][error_path[1]]["fallback_on"]
            except (KeyError, TypeError, IndexError):
                reasons = ()
            if any(reason in _FORBIDDEN_FALLBACK_REASONS for reason in reasons):
                raise _problem("forbidden_fallback_reason", "fallback is forbidden for auth failure and policy rejection", _pointer(error))
        raise _problem(
            "invalid_model_roles",
            f"invalid bb.model_roles.v1 document: {error.message}",
            _pointer(error),
        )
    return root


def _validate_effective(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _mapping(value, "model_role_lock")
    _reject_secret_keys(record, "$")
    errors = sorted(_effective_validator().iter_errors(record), key=lambda item: tuple(str(part) for part in item.absolute_path))
    if errors:
        error = errors[0]
        raise _problem("invalid_model_role_lock", f"invalid effective model-role lock: {error.message}", _pointer(error))
    return record


def _target(value: Any, path: str) -> dict[str, Any]:
    if isinstance(value, str):
        raw = value.strip()
        if "/" not in raw:
            raise _problem("invalid_model_target", "model target string must be provider/model", path)
        provider_id, model_id = raw.split("/", 1)
        value = {"provider_id": provider_id, "model_id": model_id}
    target = _mapping(value, path)
    provider_id = target.get("provider_id")
    model_id = target.get("model_id")
    if not isinstance(provider_id, str) or not isinstance(model_id, str) or not provider_id or not model_id:
        raise _problem("invalid_model_target", "provider_id and model_id are required", path)
    route_id = f"{provider_id}/{model_id}"
    try:
        from breadboard_engine.provider.routing import provider_router

        canonical_provider, native_model, _route_kind = provider_router.parse_model_id(route_id)
    except Exception:
        raise _problem(
            "invalid_model_target",
            "provider/model target is not a canonical supported route",
            path,
            route_id=route_id,
        ) from None
    if canonical_provider != provider_id or native_model != model_id:
        raise _problem(
            "invalid_model_target",
            "provider/model target does not preserve canonical provider-native identity",
            path,
            route_id=route_id,
        )
    target["provider_id"] = provider_id
    target["model_id"] = model_id
    return target


def _route_id(target: Mapping[str, Any]) -> str:
    return f"{target['provider_id']}/{target['model_id']}"


def _provider_entry(provider_id: str) -> Any:
    try:
        from breadboard_engine.provider_broker.catalog import get_provider_catalog_entry

        return get_provider_catalog_entry(provider_id)
    except Exception:
        return None


def _catalog_entries(catalog: Any) -> tuple[dict[str, Any], ...]:
    if catalog is None:
        return ()
    raw_entries: Any = catalog
    if isinstance(catalog, Mapping):
        raw_entries = catalog.get("models", catalog.get("entries", catalog.get("catalog", ())))
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes, bytearray)):
        raise _problem("invalid_model_catalog", "model_catalog must be an array of entries", "$.catalog")
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(raw_entries):
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        elif hasattr(item, "dict") and not isinstance(item, Mapping):
            item = item.dict()
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, Mapping):
            raise _problem("invalid_model_catalog", "catalog entries must be objects", f"$.catalog[{index}]")
        entries.append(_mapping(item, f"$.catalog[{index}]"))
    return tuple(entries)


def _entry_route(entry: Mapping[str, Any]) -> str | None:
    model_id = entry.get("model_id", entry.get("id"))
    provider_id = entry.get(
        "canonical_provider", entry.get("provider", entry.get("provider_id"))
    )
    if not isinstance(model_id, str) or not model_id:
        return None
    if isinstance(provider_id, str) and provider_id:
        try:
            from breadboard_engine.provider.routing import provider_router

            parsed_provider, native_model, _route_kind = provider_router.parse_model_id(
                model_id
            )
        except Exception:
            parsed_provider = native_model = None
        if parsed_provider == provider_id and isinstance(native_model, str):
            return f"{parsed_provider}/{native_model}"
        return f"{provider_id}/{model_id}"
    if "/" in model_id:
        return model_id
    return None


def _capabilities(
    entry: Mapping[str, Any], target: Mapping[str, Any]
) -> Mapping[str, Any]:
    merged: dict[str, Any] = {}
    try:
        from breadboard_engine.provider.routing import provider_router

        capability = provider_router.get_capabilities(_route_id(target))
        merged.update(
            {
                "tools": capability.tool_calls != "none",
                "parallel_tools": capability.tool_calls == "parallel",
                "streaming": capability.streaming != "none",
                "structured_output": capability.json_mode != "none",
                "prompt_cache": capability.caching != "none",
            }
        )
    except Exception:
        pass
    for key in ("capabilities", "supports"):
        value = entry.get(key)
        if isinstance(value, Mapping):
            merged.update(value)
    for parent_key in ("metadata", "routing", "params"):
        parent = entry.get(parent_key)
        if isinstance(parent, Mapping):
            for key in ("capabilities", "supports"):
                value = parent.get(key)
                if isinstance(value, Mapping):
                    merged.update(value)
    return merged


def _validate_catalog_target(
    target: Mapping[str, Any],
    role_binding: Mapping[str, Any],
    catalog_by_route: Mapping[str, Mapping[str, Any]],
    path: str,
) -> None:
    route = _route_id(target)
    entry = catalog_by_route.get(route)
    if entry is None:
        raise _problem("missing_catalog_target", "model target is not present in the configured catalog", path, route_id=route)
    source = str(entry.get("source") or "configured")
    if source == "dynamic" or entry.get("discovery") not in (None, "configured_only"):
        raise _problem("stale_catalog_target", "dynamic or stale catalog targets are not admissible", path, route_id=route)
    support_tier = str(entry.get("support_tier") or "core")
    if support_tier == "deferred" or entry.get("availability_reason") == "deferred_provider":
        raise _problem("deferred_catalog_target", "deferred provider targets are not admissible", path, route_id=route)
    if support_tier in {"unsupported", "evidence"} and entry.get("available") is False:
        raise _problem("unsupported_catalog_target", "unsupported catalog targets are not admissible", path, route_id=route)
    if entry.get("available") is False or entry.get("availability_reason") in {"missing_auth", "unsupported_provider"}:
        raise _problem("missing_catalog_target", "catalog target is unavailable", path, route_id=route)
    required = role_binding.get("requires", {})
    if isinstance(required, Mapping):
        available = _capabilities(entry, target)
        for capability, wanted in required.items():
            if wanted is True and available.get(capability) is not True:
                raise _problem(
                    "capability_mismatch",
                    f"catalog target does not provide required capability {capability}",
                    f"{path}.requires.{capability}",
                    route_id=route,
                    capability=capability,
                )


def _validate_catalog(catalog: Any) -> dict[str, dict[str, Any]]:
    entries = _catalog_entries(catalog)
    by_route: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        route = _entry_route(entry)
        if route is None:
            continue
        if route in by_route:
            raise _problem("duplicate_catalog_target", "catalog contains duplicate target routes", f"$.catalog[{index}]", route_id=route)
        by_route[route] = entry
    if isinstance(catalog, Mapping):
        issues = catalog.get("issues", ())
        if isinstance(issues, Sequence) and not isinstance(
            issues, (str, bytes, bytearray)
        ):
            for issue in issues:
                if not isinstance(issue, Mapping):
                    continue
                code = str(issue.get("code") or "")
                if code == "stale_dynamic_catalog":
                    raise _problem(
                        "stale_catalog_target",
                        "catalog contains stale dynamic targets",
                        "$.catalog.issues",
                    )
                if code == "duplicate_model":
                    raise _problem(
                        "duplicate_catalog_target",
                        "configured catalog contains duplicate target routes",
                        "$.catalog.issues",
                    )
                if code == "invalid_model":
                    raise _problem(
                        "invalid_model_catalog",
                        "configured catalog contains an invalid target",
                        "$.catalog.issues",
                    )
                if code == "deferred_provider":
                    raise _problem(
                        "deferred_catalog_target",
                        "configured catalog contains a deferred provider target",
                        "$.catalog.issues",
                    )
                if code == "unsupported_provider":
                    raise _problem(
                        "unsupported_catalog_target",
                        "configured catalog contains an unsupported provider target",
                        "$.catalog.issues",
                    )
    return by_route


def _credential_origin(
    broker: Any,
    provider_id: str,
    selector: Mapping[str, Any],
    session_id: str | None,
    path: str,
) -> dict[str, str]:
    if broker is None:
        raise _problem(
            "account_binding_unavailable",
            "the provider broker is required to resolve this account selector",
            path,
            provider_id=provider_id,
        )
    method = getattr(broker, "get_credential_origin", None)
    if method is None:
        raise _problem(
            "account_binding_unavailable",
            "the provider broker cannot report credential origin",
            path,
            provider_id=provider_id,
        )
    effective_selector: Mapping[str, Any] = selector
    if selector.get("mode") == "default" and session_id:
        getter = getattr(broker, "get_session_account_binding", None)
        try:
            existing = getter(session_id, provider_id) if getter is not None else None
        except Exception:
            raise _problem(
                "account_resolution_failed",
                "the existing session account binding could not be read",
                path,
                provider_id=provider_id,
            ) from None
        if isinstance(existing, Mapping) and existing.get("account_id"):
            effective_selector = {"account_id": str(existing["account_id"])}
    entry = _provider_entry(provider_id)
    environment_key = getattr(entry, "api_key_env", None) if entry is not None else None
    try:
        origin = method(
            provider_id,
            session_id="",
            account_selector=effective_selector,
            environment_key=environment_key,
        )
    except Exception:
        raise _problem(
            "account_resolution_failed",
            "credential origin resolution failed",
            path,
            provider_id=provider_id,
        ) from None
    if not isinstance(origin, Mapping):
        raise _problem(
            "account_binding_unavailable",
            "no eligible credential source is available for the locked target",
            path,
            provider_id=provider_id,
        )
    return {str(key): str(value) for key, value in origin.items() if key and value}


def _origin_binding_kind(origin: Mapping[str, str], path: str) -> str:
    kind = str(origin.get("kind") or "")
    if kind in {"oauth", "api_key"}:
        return "account"
    if kind in {"runtime", "config"}:
        return "configured"
    if kind == "env":
        return "environment"
    if kind == "fallback":
        return "fallback"
    raise _problem(
        "account_binding_unavailable",
        "credential origin cannot be represented by the model-role lock",
        path,
        origin_kind=kind,
    )


def _provider_binding_kind(provider_id: str) -> str:
    if provider_id in _SYNTHETIC_PROVIDERS:
        return "synthetic"
    entry = _provider_entry(provider_id)
    if entry is not None and getattr(entry, "auth_owner", None) == "provider":
        return "provider_managed"
    return "account"


def _resolve_target(
    value: Any,
    path: str,
    role_binding: Mapping[str, Any],
    broker: Any,
    session_id: str | None,
) -> tuple[
    dict[str, Any],
    str | None,
    tuple[str, str, Mapping[str, Any] | None] | None,
]:
    target = _target(value, path)
    provider_id = str(target["provider_id"])
    target["route_id"] = _route_id(target)
    selector = target.get("account_selector")
    kind = _provider_binding_kind(provider_id)
    if not isinstance(selector, Mapping):
        selector = (
            {"mode": "none", "pin": "session"}
            if kind in {"provider_managed", "synthetic"}
            else {"mode": "default", "pin": "session"}
        )
        target["account_selector"] = dict(selector)
    mode = selector.get("mode")
    pin = str(selector.get("pin"))
    if kind in {"provider_managed", "synthetic"}:
        if mode != "none" or pin != "session":
            raise _problem(
                "account_selector_unsupported",
                "provider-managed and synthetic targets require mode=none with pin=session",
                path,
                provider_id=provider_id,
            )
        target["account_binding"] = {"kind": kind, "pin": pin}
        return target, None, None
    if mode == "none":
        raise _problem(
            "account_selection_required",
            "account-owned providers require a default or alias account selector",
            path,
            provider_id=provider_id,
        )
    origin = _credential_origin(broker, provider_id, selector, session_id, path)
    binding_kind = _origin_binding_kind(origin, path)
    account_id = str(origin.get("account_id") or "") or None
    if pin == "lock":
        if binding_kind != "account" or not account_id:
            raise _problem(
                "concrete_account_required",
                "pin=lock requires one concrete broker account identity",
                path,
                provider_id=provider_id,
            )
        target["account_binding"] = {
            "kind": "account",
            "pin": "lock",
            "account_id": account_id,
        }
        return target, account_id, None
    binding: dict[str, Any] = {
        "kind": binding_kind,
        "pin": "session",
        "binding_ref": {
            "provider_id": provider_id,
            "session_id": str(session_id) if session_id else None,
        },
    }
    claim_identity: str
    binder_selector: Mapping[str, Any] | None = None
    if binding_kind == "account":
        if not account_id:
            raise _problem(
                "account_binding_unavailable",
                "the selected credential source has no stable account identity",
                path,
                provider_id=provider_id,
            )
        claim_identity = f"account:{account_id}"
        binder_selector = {"account_id": account_id}
    elif binding_kind == "environment":
        source = str(origin.get("env_var") or "")
        if not source:
            raise _problem(
                "account_binding_unavailable",
                "environment credential origin has no stable variable identity",
                path,
                provider_id=provider_id,
            )
        binding["source"] = source
        claim_identity = f"environment:{source}"
    else:
        source = (
            str(origin.get("kind") or "")
            if binding_kind == "configured"
            else str(origin.get("source") or origin.get("kind") or "")
        )
        if not source:
            raise _problem(
                "account_binding_unavailable",
                "credential origin has no stable source identity",
                path,
                provider_id=provider_id,
            )
        binding["source"] = source
        claim_identity = f"{binding_kind}:{source}"
    target["account_binding"] = binding
    return (
        target,
        account_id,
        (provider_id, claim_identity, binder_selector),
    )


def _classify(role: str) -> str:
    if role in _PUBLIC_ROLES:
        return "public"
    if role in _INTERNAL_ROLES:
        return "internal"
    return "custom"


def _role_reference_problem(role: str, path: str) -> ModelRoleResolutionError:
    if role in _PUBLIC_ROLES or role in _INTERNAL_ROLES:
        return _problem("known_role_unbound", "known model role is not configured", path, role=role)
    return _problem("unconfigured_custom_role", "custom model role is not configured", path, role=role)


def resolve_role_name(document: Mapping[str, Any], requested_role: str | None = None) -> str:
    schema_version = document.get("schema_version") if isinstance(document, Mapping) else None
    root = (
        _validate_effective(document)
        if schema_version == "bb.effective_model_role_lock.v1"
        else _validate_source(document)
    )
    roles = root["roles"]
    defaults = root["defaults"]
    role = str(requested_role or defaults["role"])
    if role in roles:
        return role
    if requested_role is not None:
        if role in _PUBLIC_ROLES or role in _INTERNAL_ROLES:
            if role == "vision":
                raise _problem(
                    "known_role_unbound",
                    "vision role has no image-capable configured target",
                    "$.roles",
                    role=role,
                )
            if (
                defaults["known_but_unbound_role"] == "use_default"
                and defaults["role"] in roles
            ):
                return str(defaults["role"])
            raise _problem(
                "known_role_unbound",
                "known model role is not configured",
                "$.roles",
                role=role,
            )
        raise _problem(
            "unknown_role", f"unknown model role: {role}", "$.roles", role=role
        )
    if role in _PUBLIC_ROLES or role in _INTERNAL_ROLES:
        raise _problem(
            "known_role_unbound",
            "the default role is not bound",
            "$.defaults.role",
            role=role,
        )
    raise _problem(
        "unknown_role",
        "defaults.role must name a configured role",
        "$.defaults.role",
        role=role,
    )


def _effective_source_with_overrides(root: Mapping[str, Any], role_overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    effective = _copy(root, freeze=False)
    if role_overrides is None:
        return dict(effective)
    if not isinstance(role_overrides, Mapping):
        raise _problem("invalid_role_override", "role_overrides must be an object", "$.role_overrides")
    roles = effective["roles"]
    for role, override in role_overrides.items():
        if not isinstance(role, str) or role not in roles:
            raise _problem("unknown_role_override", "role override names an unconfigured role", "$.role_overrides", role=role)
        if isinstance(override, str):
            if "/" not in override:
                raise _problem("invalid_role_override", "target override must be provider/model", f"$.role_overrides.{role}")
            provider_id, model_id = override.split("/", 1)
            override = {"provider_id": provider_id, "model_id": model_id}
        if isinstance(override, Mapping) and "provider_id" in override and "primary" not in override:
            override = {"primary": dict(override), "fallbacks": [], "fallback_on": []}
        roles[role] = _copy(override, freeze=False)
    _validate_source(effective)
    return dict(effective)


def _binding(
    role: str,
    value: Mapping[str, Any],
    path: str,
    broker: Any,
    session_id: str | None,
    catalog_by_route: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    list[tuple[str, str, Mapping[str, Any] | None]],
]:
    binding = _mapping(value, path)
    raw_primary = _target(binding["primary"], f"{path}.primary")
    raw_fallbacks = [
        _target(item, f"{path}.fallbacks[{index}]")
        for index, item in enumerate(binding["fallbacks"])
    ]
    if any(
        fallback["provider_id"] != raw_primary["provider_id"]
        for fallback in raw_fallbacks
    ) and policy.get("cross_provider_fallback", "forbidden") != "explicit_only":
        raise _problem(
            "cross_provider_fallback_forbidden",
            "cross-provider fallback requires explicit_only policy",
            path,
        )
    primary, primary_id, primary_claim = _resolve_target(
        binding["primary"], f"{path}.primary", binding, broker, session_id
    )
    fallbacks: list[dict[str, Any]] = []
    fallback_ids: list[str | None] = []
    claims: list[tuple[str, str, Mapping[str, Any] | None]] = []
    if primary_claim:
        claims.append(primary_claim)
    routes = {primary["route_id"]}
    if catalog_by_route:
        _validate_catalog_target(
            primary, binding, catalog_by_route, f"{path}.primary"
        )
    for index, fallback_value in enumerate(binding["fallbacks"]):
        fallback, fallback_id, fallback_claim = _resolve_target(
            fallback_value,
            f"{path}.fallbacks[{index}]",
            binding,
            broker,
            session_id,
        )
        if fallback_claim:
            claims.append(fallback_claim)
        fallback_ids.append(fallback_id)
        route = fallback["route_id"]
        if route in routes:
            raise _problem(
                "duplicate_target_route",
                "role target routes must be unique",
                f"{path}.fallbacks[{index}]",
                route_id=route,
            )
        routes.add(route)
        if catalog_by_route:
            _validate_catalog_target(
                fallback,
                binding,
                catalog_by_route,
                f"{path}.fallbacks[{index}]",
            )
        fallbacks.append(fallback)
    reasons = list(binding["fallback_on"])
    if any(reason in _FORBIDDEN_FALLBACK_REASONS for reason in reasons):
        raise _problem(
            "forbidden_fallback_reason",
            "fallback is forbidden for auth failure and policy rejection",
            f"{path}.fallback_on",
        )
    if policy.get("account_failover", "forbidden") != "explicit_only":
        if any(
            fallback["provider_id"] == primary["provider_id"]
            and fallback_id is not None
            and primary_id is not None
            and fallback_id != primary_id
            for fallback, fallback_id in zip(
                fallbacks, fallback_ids, strict=True
            )
        ):
            raise _problem(
                "account_failover_forbidden",
                "fallback cannot switch accounts under the active policy",
                path,
            )
    result: dict[str, Any] = {
        "classification": _classify(role),
        "primary": primary,
        "fallbacks": fallbacks,
        "fallback_on": reasons,
    }
    for field_name in (
        "reasoning",
        "generation",
        "requires",
        "service_tier",
        "metadata",
    ):
        if field_name in binding:
            result[field_name] = _copy(binding[field_name], freeze=False)
    return result, claims


def compile_model_roles(
    document: Mapping[str, Any],
    *,
    broker: Any = None,
    role_overrides: Mapping[str, Any] | None = None,
    session_started: bool = False,
    lock_id: str | None = None,
    session_id: str | None = None,
    catalog: Any = None,
    bind_session_accounts: bool = False,
) -> DerivedModelRoleLock:
    """Compile a strict source role map into an immutable effective lock."""
    root = _validate_source(document)
    if session_started and role_overrides:
        raise _problem(
            "lock_immutable",
            "model-role overrides are rejected after session.start",
            "$.role_overrides",
        )
    effective = _effective_source_with_overrides(root, role_overrides)
    defaults = effective["defaults"]
    roles_raw = effective["roles"]
    dispatch = effective["dispatch"]
    policy = {
        "allow_environment_overrides": False,
        "cross_provider_fallback": "forbidden",
        "account_failover": "forbidden",
        **dict(effective.get("policy", {})),
    }
    if (
        role_overrides
        and not policy["allow_environment_overrides"]
    ):
        raise _problem(
            "environment_override_forbidden",
            "role overrides are disabled by model-role policy",
            "$.role_overrides",
        )
    catalog_by_route = _validate_catalog(catalog) if catalog is not None else {}
    default_role = str(defaults["role"])
    if default_role not in roles_raw:
        if default_role in _PUBLIC_ROLES or default_role in _INTERNAL_ROLES:
            raise _problem("known_role_unbound", "default role is not configured", "$.defaults.role", role=default_role)
        raise _problem("unknown_role", "defaults.role must name a configured role", "$.defaults.role", role=default_role)
    for section in ("subagents", "lanes"):
        for key, role in dispatch[section].items():
            if role not in roles_raw:
                raise _role_reference_problem(str(role), f"$.dispatch.{section}.{key}")
    bindings: dict[str, Any] = {}
    claims: list[tuple[str, str, Mapping[str, Any] | None]] = []
    for role in sorted(roles_raw):
        compiled, role_claims = _binding(
            role,
            roles_raw[role],
            f"$.roles.{role}",
            broker,
            session_id,
            catalog_by_route,
            policy,
        )
        bindings[role] = compiled
        claims.extend(role_claims)
    by_provider: dict[str, tuple[str, Mapping[str, Any] | None]] = {}
    for provider_id, identity, binder_selector in claims:
        previous = by_provider.get(provider_id)
        if previous is not None and previous[0] != identity:
            raise _problem(
                "session_account_conflict",
                "session-pinned roles select conflicting credential identities for one provider",
                "$.roles",
                provider_id=provider_id,
            )
        by_provider[provider_id] = (identity, binder_selector)
    if bind_session_accounts and not session_id:
        raise _problem(
            "invalid_session_binding",
            "session_id is required to bind session-pinned accounts",
            "$.session_id",
        )
    role_record: dict[str, Any] = {
        "schema_version": "bb.effective_model_role_lock.v1",
        "lock_id": lock_id or f"model_role_lock:{sha256_json(effective)[7:23]}",
        "defaults": {
            "role": default_role,
            "known_but_unbound_role": defaults["known_but_unbound_role"],
            "unknown_role": "error",
        },
        "roles": bindings,
        "dispatch": {
            "subagents": {str(key): str(value) for key, value in sorted(dispatch["subagents"].items())},
            "lanes": {str(key): str(value) for key, value in sorted(dispatch["lanes"].items())},
        },
        "policy": policy,
        "source_model_roles_hash": sha256_json(effective),
        "lock_hash": None,
    }
    role_record["lock_hash"] = sha256_json(role_record)
    _validate_effective(role_record)
    _validate_effective_semantics(role_record)
    if bind_session_accounts and broker is not None:
        store = getattr(broker, "store", None)
        context = (
            store.atomic()
            if store is not None and hasattr(store, "atomic")
            else nullcontext()
        )
        with context:
            for provider_id, (_identity, binder_selector) in by_provider.items():
                if binder_selector is None:
                    continue
                account_id = str(binder_selector.get("account_id") or "")
                getter = getattr(broker, "get_session_account_binding", None)
                try:
                    existing = (
                        getter(session_id, provider_id)
                        if getter is not None
                        else None
                    )
                except Exception:
                    raise _problem(
                        "account_binding_failed",
                        "existing session account binding could not be read",
                        "$.session_id",
                        provider_id=provider_id,
                    ) from None
                if (
                    isinstance(existing, Mapping)
                    and existing.get("account_id")
                    and str(existing["account_id"]) != account_id
                ):
                    raise _problem(
                        "session_account_conflict",
                        "session already binds a different account",
                        "$.session_id",
                        provider_id=provider_id,
                    )
                binder = getattr(broker, "bind_session_account", None)
                if binder is None:
                    raise _problem(
                        "account_binding_failed",
                        "broker cannot persist session account bindings",
                        "$.session_id",
                    )
                try:
                    selected = binder(
                        session_id, provider_id, {"account_id": account_id}
                    )
                except Exception:
                    raise _problem(
                        "account_binding_failed",
                        "session account binding was rejected",
                        "$.session_id",
                        provider_id=provider_id,
                    ) from None
                if (
                    not isinstance(selected, Mapping)
                    or str(selected.get("account_id") or "") != account_id
                ):
                    raise _problem(
                        "account_binding_failed",
                        "broker did not confirm the selected session account",
                        "$.session_id",
                        provider_id=provider_id,
                    )
    return DerivedModelRoleLock._from_record(role_record)


def _validate_effective_semantics(record: Mapping[str, Any]) -> None:
    roles = record["roles"]
    defaults = record["defaults"]
    if defaults["role"] not in roles:
        raise _problem(
            "invalid_model_role_lock",
            "effective default role is not configured",
            "$.defaults.role",
        )
    for section in ("subagents", "lanes"):
        for key, role in record["dispatch"][section].items():
            if role not in roles:
                raise _role_reference_problem(
                    str(role), f"$.dispatch.{section}.{key}"
                )
    cross_provider = record["policy"].get(
        "cross_provider_fallback", "forbidden"
    )
    for role, binding in roles.items():
        role_path = f"$.roles.{role}"
        if binding["classification"] != _classify(str(role)):
            raise _problem(
                "invalid_model_role_lock",
                "role classification does not match role identity",
                f"{role_path}.classification",
            )
        targets = (binding["primary"], *binding["fallbacks"])
        routes: set[str] = set()
        for index, target in enumerate(targets):
            target_path = (
                f"{role_path}.primary"
                if index == 0
                else f"{role_path}.fallbacks[{index - 1}]"
            )
            parsed = _target(target, target_path)
            route_id = _route_id(parsed)
            if target["route_id"] != route_id or route_id in routes:
                raise _problem(
                    "invalid_model_role_lock",
                    "effective target route identity is inconsistent or duplicated",
                    f"{target_path}.route_id",
                )
            routes.add(route_id)
            provider_id = target["provider_id"]
            account_binding = target["account_binding"]
            expected_kind = _provider_binding_kind(provider_id)
            kind = account_binding["kind"]
            if expected_kind in {"provider_managed", "synthetic"}:
                if kind != expected_kind:
                    raise _problem(
                        "invalid_model_role_lock",
                        "effective credential binding kind does not match provider ownership",
                        f"{target_path}.account_binding.kind",
                    )
            elif kind in {"none", "provider_managed", "synthetic"}:
                raise _problem(
                    "invalid_model_role_lock",
                    "account-owned provider has an invalid credential binding kind",
                    f"{target_path}.account_binding.kind",
                )
            ref = account_binding.get("binding_ref")
            if isinstance(ref, Mapping) and ref.get("provider_id") != provider_id:
                raise _problem(
                    "invalid_model_role_lock",
                    "session binding reference provider does not match target",
                    f"{target_path}.account_binding.binding_ref.provider_id",
                )
        if (
            cross_provider != "explicit_only"
            and any(
                target["provider_id"] != binding["primary"]["provider_id"]
                for target in binding["fallbacks"]
            )
        ):
            raise _problem(
                "cross_provider_fallback_forbidden",
                "effective cross-provider fallback is not authorized",
                role_path,
            )


def _restore_origin(
    broker: Any,
    target: Mapping[str, Any],
    session_id: str,
    path: str,
) -> Mapping[str, Any]:
    provider_id = str(target["provider_id"])
    binding = target["account_binding"]
    selector = (
        {"account_id": str(binding["account_id"])}
        if binding["pin"] == "lock" and binding.get("account_id")
        else None
    )
    entry = _provider_entry(provider_id)
    environment_key = getattr(entry, "api_key_env", None) if entry is not None else None
    method = getattr(broker, "get_credential_origin", None)
    if method is None:
        raise _problem(
            "account_binding_unavailable",
            "broker cannot restore credential origin",
            path,
            provider_id=provider_id,
        )
    try:
        origin = method(
            provider_id,
            session_id=session_id,
            account_selector=selector,
            environment_key=environment_key,
        )
    except Exception:
        raise _problem(
            "account_binding_unavailable",
            "credential origin could not be restored",
            path,
            provider_id=provider_id,
        ) from None
    if not isinstance(origin, Mapping):
        raise _problem(
            "account_binding_unavailable",
            "locked credential origin is unavailable",
            path,
            provider_id=provider_id,
        )
    return origin


def credential_origin_matches_binding(
    origin: Mapping[str, Any], binding: Mapping[str, Any]
) -> bool:
    """Return whether secret-free runtime provenance satisfies one locked binding."""
    origin_kind = str(origin.get("kind") or "")
    kind = binding["kind"]
    if kind == "account":
        if origin_kind not in {"oauth", "api_key"}:
            return False
        expected = binding.get("account_id")
        return expected is None or str(origin.get("account_id") or "") == str(
            expected
        )
    if kind == "environment":
        return (
            origin_kind == "env"
            and str(origin.get("env_var") or "")
            == str(binding.get("source") or "")
        )
    if kind == "configured":
        return origin_kind in {"runtime", "config"} and origin_kind == str(
            binding.get("source") or ""
        )
    if kind == "fallback":
        return origin_kind == "fallback" and str(
            origin.get("source") or ""
        ) == str(binding.get("source") or "")
    if kind == "provider_managed":
        return (
            origin_kind == "fallback"
            and origin.get("source") == "provider_managed"
        )
    if kind == "synthetic":
        return origin_kind == "fallback" and origin.get("source") == "synthetic"
    return False
def restore_model_role_lock(
    record: Mapping[str, Any],
    *,
    broker: Any = None,
    session_id: str | None = None,
) -> DerivedModelRoleLock:
    """Validate an effective lock and fail closed on current account bindings."""
    checked = _validate_effective(record)
    expected = _copy(checked, freeze=False)
    supplied_hash = expected.get("lock_hash")
    expected["lock_hash"] = None
    if supplied_hash != sha256_json(expected):
        raise _problem(
            "lock_hash_mismatch",
            "effective model-role lock hash does not match its content",
            "$.lock_hash",
        )
    _validate_effective_semantics(checked)
    for role, role_binding in checked["roles"].items():
        targets = (role_binding["primary"], *role_binding["fallbacks"])
        for index, target in enumerate(targets):
            path = (
                f"$.roles.{role}.primary.account_binding"
                if index == 0
                else f"$.roles.{role}.fallbacks[{index - 1}].account_binding"
            )
            binding = target["account_binding"]
            kind = binding["kind"]
            if kind in {"provider_managed", "synthetic"}:
                continue
            if broker is None:
                raise _problem(
                    "account_binding_unavailable",
                    "broker is required to restore a credential-bound role lock",
                    path,
                )
            ref = binding.get("binding_ref")
            ref_session = (
                str(ref.get("session_id") or "")
                if isinstance(ref, Mapping)
                else ""
            )
            if session_id and ref_session and str(session_id) != ref_session:
                raise _problem(
                    "account_binding_unavailable",
                    "restored session id does not match the lock binding reference",
                    path,
                )
            bound_session = str(session_id or ref_session)
            if binding["pin"] == "session" and not bound_session:
                raise _problem(
                    "account_binding_unavailable",
                    "session id is required to restore a session-pinned role lock",
                    path,
                )
            if kind == "account" and binding["pin"] == "session":
                getter = getattr(broker, "get_session_account_binding", None)
                try:
                    current = (
                        getter(bound_session, target["provider_id"])
                        if getter is not None
                        else None
                    )
                except Exception:
                    current = None
                if (
                    not isinstance(current, Mapping)
                    or current.get("availability") not in (None, "active")
                ):
                    raise _problem(
                        "account_binding_unavailable",
                        "session account binding is missing, revoked, disabled, or unavailable",
                        path,
                        provider_id=target["provider_id"],
                    )
            origin = _restore_origin(broker, target, bound_session, path)
            if not credential_origin_matches_binding(origin, binding):
                raise _problem(
                    "account_binding_unavailable",
                    "current credential origin does not match the immutable lock",
                    path,
                    provider_id=target["provider_id"],
                )
    return DerivedModelRoleLock._from_record(checked)


def derive_model_role_lock(*args: Any, **kwargs: Any) -> DerivedModelRoleLock:
    return compile_model_roles(*args, **kwargs)


def select_role_target(
    lock: Mapping[str, Any],
    role: str,
    *,
    failure_reason: str | Any = None,
    current_route_id: str | None = None,
) -> dict[str, Any]:
    roles = lock.get("roles") if isinstance(lock.get("roles"), Mapping) else {}
    if role not in roles:
        raise _problem(
            "unknown_role", f"unknown model role: {role}", "$.roles", role=role
        )
    binding = roles[role]
    targets = [binding["primary"], *binding.get("fallbacks", ())]
    current_index = 0
    if current_route_id is not None:
        current_index = next(
            (
                index
                for index, target in enumerate(targets)
                if target["route_id"] == current_route_id
            ),
            0,
        )
    reason = failure_reason
    if reason is not None and not isinstance(reason, str):
        if getattr(reason, "output_emitted", False) or getattr(
            reason, "has_output", False
        ):
            reason = None
        else:
            reason = getattr(reason, "model_fallback_reason", None)
    if (
        reason in _FORBIDDEN_FALLBACK_REASONS
        or reason not in binding.get("fallback_on", [])
        or current_index + 1 >= len(targets)
    ):
        return dict(targets[current_index])
    return dict(targets[current_index + 1])


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
    "credential_origin_matches_binding",
    "derive_model_role_lock",
    "embed_model_role_lock",
    "resolve_role_name",
    "restore_model_role_lock",
    "select_role_target",
]
