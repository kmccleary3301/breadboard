from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PLAN_SCHEMA_VERSION = "bb.replay_plan.v1"
HASH_BINDING_NAMES = (
    "scenario_sha256",
    "interaction_script_sha256",
    "initial_workspace_sha256",
    "provider_route_lock_sha256",
    "capability_probe_sha256",
    "model_policy_sha256",
    "initial_messages_sha256",
    "tool_schema_lock_sha256",
    "operation_policy_sha256",
    "tool_executor_identity_sha256",
    "host_driver_identity_sha256",
    "host_platform_sha256",
    "environment_allowlist_sha256",
    "secret_references_sha256",
    "normalizer_config_sha256",
    "comparator_config_sha256",
    "schema_registry_sha256",
)
DEADLINE_NAMES = ("total", "idle", "provider_call", "tool_call")
BUDGET_NAMES = ("turns", "provider_calls", "tool_calls", "tokens", "cost")
_PLAN_FIELDS = {
    "schema_version", "plan_id", "lane_lock_sha256", "harness_lock_sha256",
    "hash_bindings", "deadlines_ms", "budgets", "cancellation_grace_ms", "mode",
    "scenario_ref", "provider_route_ref", "operation_policy_ref", "host_ref",
    "toolset_lock_ref", "reuse_attestation_ref",
}


class ReplayPlanError(ValueError):
    pass


def _json_value(value: Any, *, pointer: str = "$", seen: frozenset[int] = frozenset()) -> Any:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ReplayPlanError(f"{pointer}: non-finite number")
        return value
    if isinstance(value, Mapping):
        if id(value) in seen or any(type(key) is not str for key in value):
            raise ReplayPlanError(f"{pointer}: cyclic object or non-string key")
        return {key: _json_value(item, pointer=f"{pointer}.{key}", seen=seen | {id(value)}) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if id(value) in seen:
            raise ReplayPlanError(f"{pointer}: cyclic array")
        return [_json_value(item, pointer=f"{pointer}[{index}]", seen=seen | {id(value)}) for index, item in enumerate(value)]
    raise ReplayPlanError(f"{pointer}: value is not JSON-compatible")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(_json_value(value), allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:") or any(char not in "0123456789abcdef" for char in value[7:]):
        raise ReplayPlanError(f"{name} must be an exact lowercase sha256 hash")
    return value


def _portable_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or not value[0].isalnum() or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in value):
        raise ReplayPlanError(f"{name} must be a portable identifier")
    return value


def _plan_id(unsigned: Mapping[str, Any]) -> str:
    return "replay_plan:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()


def _validate_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _json_value(value)
    if not isinstance(record, dict) or set(record) != _PLAN_FIELDS or record.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ReplayPlanError("record fields do not match bb.replay_plan.v1")
    _portable_id(record.get("plan_id"), "plan_id")
    _sha256(record.get("lane_lock_sha256"), "lane_lock_sha256")
    _sha256(record.get("harness_lock_sha256"), "harness_lock_sha256")
    bindings = record.get("hash_bindings")
    if not isinstance(bindings, dict) or set(bindings) != set(HASH_BINDING_NAMES):
        raise ReplayPlanError("hash_bindings must contain the frozen replay binding set")
    for name in HASH_BINDING_NAMES:
        _sha256(bindings[name], f"hash_bindings.{name}")
    deadlines = record.get("deadlines_ms")
    if not isinstance(deadlines, dict) or set(deadlines) != set(DEADLINE_NAMES) or any(type(deadlines[name]) is not int or deadlines[name] < 1 for name in DEADLINE_NAMES):
        raise ReplayPlanError("deadlines_ms must contain positive integer total, idle, provider_call, and tool_call values")
    budgets = record.get("budgets")
    if not isinstance(budgets, dict) or set(budgets) != set(BUDGET_NAMES):
        raise ReplayPlanError("budgets must contain the frozen replay budget set")
    if any(type(budgets[name]) is not int or budgets[name] < 1 for name in BUDGET_NAMES[:-1]) or isinstance(budgets["cost"], bool) or not isinstance(budgets["cost"], (int, float)) or budgets["cost"] <= 0:
        raise ReplayPlanError("replay budgets must be positive")
    if type(record.get("cancellation_grace_ms")) is not int or record["cancellation_grace_ms"] < 1:
        raise ReplayPlanError("cancellation_grace_ms must be a positive integer")
    mode = record.get("mode")
    if mode not in ("execute", "reuse"):
        raise ReplayPlanError("mode must be execute or reuse")
    execute_refs = ("scenario_ref", "provider_route_ref", "operation_policy_ref", "host_ref", "toolset_lock_ref")
    if mode == "execute":
        if any(not isinstance(record[name], str) or not record[name] for name in execute_refs) or record.get("reuse_attestation_ref") is not None:
            raise ReplayPlanError("execute plans require runtime references and prohibit reuse_attestation_ref")
    elif any(record.get(name) is not None for name in execute_refs) or not isinstance(record.get("reuse_attestation_ref"), str) or not record["reuse_attestation_ref"]:
        raise ReplayPlanError("reuse plans require only reuse_attestation_ref")
    unsigned = dict(record)
    actual_id = unsigned.pop("plan_id")
    if actual_id != _plan_id(unsigned):
        raise ReplayPlanError("plan_id does not match the frozen plan content")
    return record


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    _canonical: bytes

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayPlan":
        return cls(canonical_json(_validate_record(value)))

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical)

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical).hexdigest()

    @property
    def plan_id(self) -> str:
        return self.as_dict()["plan_id"]

    @property
    def mode(self) -> str:
        return self.as_dict()["mode"]

    def verify_bindings(self, binding_inputs: Mapping[str, Any]) -> None:
        if set(binding_inputs) != set(HASH_BINDING_NAMES):
            raise ReplayPlanError("binding inputs must contain the frozen replay binding set")
        expected = {name: sha256_json(binding_inputs[name]) for name in HASH_BINDING_NAMES}
        if expected != self.as_dict()["hash_bindings"]:
            raise ReplayPlanError("replay inputs changed after plan creation")


def build_replay_plan(
    *,
    lane_lock_sha256: str,
    harness_lock_sha256: str,
    binding_inputs: Mapping[str, Any],
    deadlines_ms: Mapping[str, int],
    budgets: Mapping[str, int | float],
    cancellation_grace_ms: int,
    mode: str = "execute",
    scenario_ref: str | None = None,
    provider_route_ref: str | None = None,
    operation_policy_ref: str | None = None,
    host_ref: str | None = None,
    toolset_lock_ref: str | None = None,
    reuse_attestation_ref: str | None = None,
) -> ReplayPlan:
    if set(binding_inputs) != set(HASH_BINDING_NAMES):
        raise ReplayPlanError("binding inputs must contain the frozen replay binding set")
    unsigned: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "lane_lock_sha256": lane_lock_sha256,
        "harness_lock_sha256": harness_lock_sha256,
        "hash_bindings": {name: sha256_json(binding_inputs[name]) for name in HASH_BINDING_NAMES},
        "deadlines_ms": dict(deadlines_ms),
        "budgets": dict(budgets),
        "cancellation_grace_ms": cancellation_grace_ms,
        "mode": mode,
        "scenario_ref": scenario_ref,
        "provider_route_ref": provider_route_ref,
        "operation_policy_ref": operation_policy_ref,
        "host_ref": host_ref,
        "toolset_lock_ref": toolset_lock_ref,
        "reuse_attestation_ref": reuse_attestation_ref,
    }
    return ReplayPlan.from_dict({"plan_id": _plan_id(unsigned), **unsigned})
