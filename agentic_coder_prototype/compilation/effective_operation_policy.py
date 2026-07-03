from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping

from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
from agentic_coder_prototype.permissions.policy_pack import PolicyPack

SCHEMA_VERSION = "bb.effective_operation_policy.v1"
_DEFAULT_GENERATED_AT_UTC = "1970-01-01T00:00:00Z"
_VALID_AUTHORITIES = {"config", "parity", "effective"}


def _raw_policy_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = config.get("policies") or config.get("policy") or {}
    return raw if isinstance(raw, Mapping) else {}


def _policy_pack_projection(pack: PolicyPack) -> dict[str, list[str] | None]:
    return {
        "tool_allowlist": list(pack.tool_allowlist) if pack.tool_allowlist is not None else None,
        "tool_denylist": list(pack.tool_denylist) if pack.tool_denylist is not None else None,
        "model_allowlist": list(pack.model_allowlist) if pack.model_allowlist is not None else None,
        "model_denylist": list(pack.model_denylist) if pack.model_denylist is not None else None,
        "webfetch_allowlist": list(pack.webfetch_allowlist) if pack.webfetch_allowlist is not None else None,
        "webfetch_denylist": list(pack.webfetch_denylist) if pack.webfetch_denylist is not None else None,
    }


def comparable_policy_pack_projection(pack: PolicyPack) -> dict[str, list[str] | None]:
    """Return the deterministic comparison surface for authority parity checks."""

    return _policy_pack_projection(pack)


def _rule(surface: str, decision: str, index: int, *, kind: str, pattern: str) -> dict[str, Any]:
    return {
        "rule_id": f"{surface}.{decision}.{index:04d}",
        "match": {"kind": kind, "pattern": pattern, "scope_ref": None},
        "decision": decision,
        "approval_required": False,
        "redaction_ref": None,
        "evidence_refs": [],
    }


def _surface(
    surface: str,
    *,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    match_kind: str | None = None,
) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    kind = match_kind or surface
    if allowlist is not None:
        for index, pattern in enumerate(allowlist, start=1):
            rules.append(_rule(surface, "allow", index, kind=kind, pattern=pattern))
    for index, pattern in enumerate(denylist or [], start=1):
        rules.append(_rule(surface, "deny", index, kind=kind, pattern=pattern))
    return {
        "surface": surface,
        "default_decision": "deny" if allowlist is not None else "allow",
        "rules": rules,
        "evidence_refs": [],
    }


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _nonnegative_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return value
    return None


def _mapping_at(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}

def _first_present(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _budgets(config: Mapping[str, Any], raw_policy: Mapping[str, Any]) -> dict[str, int | float | None]:
    source = _mapping_at(raw_policy.get("budgets"), raw_policy.get("budget"), config.get("budgets"), config.get("budget"))
    return {
        "token_budget": _nonnegative_int(_first_present(source, "token_budget", "tokens", "max_tokens")),
        "cost_budget_usd": _nonnegative_number(_first_present(source, "cost_budget_usd", "cost_usd", "max_cost_usd")),
        "wall_time_seconds": _nonnegative_int(_first_present(source, "wall_time_seconds", "wall_seconds", "timeout_seconds")),
    }


def _limits(config: Mapping[str, Any], raw_policy: Mapping[str, Any]) -> dict[str, int | None]:
    source = _mapping_at(raw_policy.get("limits"), raw_policy.get("limit"), config.get("limits"), config.get("limit"))
    return {
        "concurrency": _nonnegative_int(_first_present(source, "concurrency", "max_concurrency")),
        "file_bytes": _nonnegative_int(_first_present(source, "file_bytes", "max_file_bytes")),
        "network_bytes": _nonnegative_int(_first_present(source, "network_bytes", "max_network_bytes")),
        "tool_calls": _nonnegative_int(_first_present(source, "tool_calls", "max_tool_calls")),
    }


def _approvals(config: Mapping[str, Any], raw_policy: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping_at(raw_policy.get("approvals"), raw_policy.get("approval"), config.get("approvals"), config.get("approval"))
    mode = source.get("mode")
    if mode not in {"none", "on_demand", "preapproved", "always_required"}:
        mode = "none"
    refs = source.get("approval_refs") or source.get("refs") or []
    return {
        "mode": mode,
        "approval_refs": [str(item) for item in refs if str(item)] if isinstance(refs, list) else [],
        "approver_scope": str(source["approver_scope"]) if source.get("approver_scope") else None,
        "evidence_refs": [],
    }


def _command_policy(raw_policy: Mapping[str, Any]) -> dict[str, Any]:
    commands = raw_policy.get("commands") or raw_policy.get("command") or {}
    if not isinstance(commands, Mapping):
        commands = {}
    allow = commands.get("allow") or commands.get("allowlist")
    deny = commands.get("deny") or commands.get("denylist") or commands.get("block")
    allowlist = [str(item) for item in allow if str(item)] if isinstance(allow, list) else None
    denylist = [str(item) for item in deny if str(item)] if isinstance(deny, list) else None
    return _surface("command", allowlist=allowlist, denylist=denylist, match_kind="command")


def compile_effective_operation_policy(
    config: Mapping[str, Any],
    *,
    session_id: str,
    config_path: str | None,
    generated_at_utc: str,
) -> dict[str, Any]:
    """Compile the config-derived operation policy primitive for a session."""

    cfg: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    raw_policy = _raw_policy_config(cfg)
    pack = PolicyPack.from_config(dict(cfg))
    record = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": f"{session_id}_effective_operation_policy",
        "applies_to": {
            "operation_id": session_id,
            "run_id": session_id,
            "principal_ref": "runtime://principal/cli_bridge.session_service",
            "provider_route_ref": None,
        },
        "command_policy": _command_policy(raw_policy),
        "file_policy": _surface("file"),
        "network_policy": _surface(
            "network",
            allowlist=pack.webfetch_allowlist,
            denylist=pack.webfetch_denylist,
            match_kind="url",
        ),
        "tool_policy": _surface(
            "tool",
            allowlist=pack.tool_allowlist,
            denylist=pack.tool_denylist,
            match_kind="tool",
        ),
        "resource_policy": _surface(
            "resource",
            allowlist=pack.model_allowlist,
            denylist=pack.model_denylist,
            match_kind="model",
        ),
        "side_effect_policy": _surface("side_effect"),
        "approvals": _approvals(cfg, raw_policy),
        "budgets": _budgets(cfg, raw_policy),
        "limits": _limits(cfg, raw_policy),
        "sandbox_refs": [],
        "placement_refs": [],
        "conditional_rules": [],
        "redaction_visibility": {
            "model_visible_paths": [],
            "provider_visible_paths": [],
            "host_only_paths": [],
            "redacted_paths": [],
            "redaction_policy_refs": [],
        },
        "enforcement_evidence_refs": [],
        "metadata": {
            "config_path": config_path,
            "generated_at_utc": generated_at_utc,
        },
    }
    return finalize_record(get_spec(SCHEMA_VERSION), record)


def _allow_deny_from_surface(surface: Mapping[str, Any], *, match_kind: str) -> tuple[list[str] | None, list[str] | None]:
    allow: list[str] = []
    deny: list[str] = []
    for item in surface.get("rules") or []:
        if not isinstance(item, Mapping):
            continue
        match = item.get("match")
        if not isinstance(match, Mapping) or match.get("kind") != match_kind:
            continue
        pattern = match.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            continue
        if item.get("decision") == "allow":
            allow.append(pattern)
        elif item.get("decision") == "deny":
            deny.append(pattern)
    allowlist: list[str] | None
    if surface.get("default_decision") == "deny" or allow:
        allowlist = allow
    else:
        allowlist = None
    return allowlist, (deny or None)


def policy_pack_from_effective_record(record: Mapping[str, Any]) -> PolicyPack:
    """Load the enforceable PolicyPack projection from a compiled operation policy record."""

    tool_allow, tool_deny = _allow_deny_from_surface(_mapping_at(record.get("tool_policy")), match_kind="tool")
    model_allow, model_deny = _allow_deny_from_surface(_mapping_at(record.get("resource_policy")), match_kind="model")
    web_allow, web_deny = _allow_deny_from_surface(_mapping_at(record.get("network_policy")), match_kind="url")
    return PolicyPack(
        tool_allowlist=tool_allow,
        tool_denylist=tool_deny,
        model_allowlist=model_allow,
        model_denylist=model_deny,
        webfetch_allowlist=web_allow,
        webfetch_denylist=web_deny,
    )


def policy_pack_for_config_authority(
    config: Mapping[str, Any] | None,
    *,
    session_id: str,
    config_path: str | None,
    generated_at_utc: str = _DEFAULT_GENERATED_AT_UTC,
    logger: logging.Logger | None = None,
) -> PolicyPack:
    """Resolve the PolicyPack according to BREADBOARD_POLICY_AUTHORITY."""

    cfg = dict(config or {})
    authority = os.environ.get("BREADBOARD_POLICY_AUTHORITY", "config").strip().lower() or "config"
    active_logger = logger or logging.getLogger(__name__)
    if authority not in _VALID_AUTHORITIES:
        active_logger.warning("Unknown BREADBOARD_POLICY_AUTHORITY=%r; using config", authority)
        authority = "config"

    config_pack = PolicyPack.from_config(cfg)
    if authority == "config":
        return config_pack

    effective_record = compile_effective_operation_policy(
        cfg,
        session_id=session_id,
        config_path=config_path,
        generated_at_utc=generated_at_utc,
    )
    effective_pack = policy_pack_from_effective_record(effective_record)
    config_projection = _policy_pack_projection(config_pack)
    effective_projection = _policy_pack_projection(effective_pack)
    if config_projection != effective_projection:
        active_logger.warning(
            "Effective operation policy divergence: config=%s effective=%s",
            json.dumps(config_projection, sort_keys=True, separators=(",", ":")),
            json.dumps(effective_projection, sort_keys=True, separators=(",", ":")),
        )
    if authority == "parity":
        return config_pack
    return effective_pack
