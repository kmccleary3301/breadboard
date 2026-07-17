from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping
import copy
import hashlib
import json

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS_DIR = _REPO_ROOT / "contracts" / "kernel" / "schemas"
_COMMON_SCHEMA_NAME = "bb.kernel.common.v1.schema.json"
_COMMON_SCHEMA_URI = f"https://breadboard.dev/contracts/kernel/schemas/{_COMMON_SCHEMA_NAME}"
_E4_COMMON_SCHEMA_NAME = "bb.e4.common.v1.schema.json"
_E4_COMMON_SCHEMA_URI = f"https://breadboard.dev/contracts/kernel/schemas/{_E4_COMMON_SCHEMA_NAME}"
_VALIDATION_ONLY_ID_FIELDS = {
    "bb.coordination_view.v1": "view_id",
    "bb.work_placement.v1": "placement_id",
    "bb.work_item.v1": "work_item_id",
    "bb.coordination_slice.v2": "slice_id",
    "bb.coordination_pack.v3": "pack_id",
}
_FROZEN_VALIDATION_ID_FIELDS = {
    "bb.task.v1": "task_id",
    "bb.distributed_task_descriptor.v1": "task_id",
    "bb.coordination_reference_slice.v1": "scenario_id",
    "bb.coordination_longrun_reference_slice.v1": "scenario_id",
    "bb.coordination_multi_worker_reference_slice.v1": "scenario_id",
    "bb.coordination_delegated_verification_reference_slice.v1": "scenario_id",
    "bb.coordination_intervention_reference_slice.v1": "scenario_id",
}
VALIDATION_ONLY_SCHEMA_VERSIONS = frozenset(_VALIDATION_ONLY_ID_FIELDS | _FROZEN_VALIDATION_ID_FIELDS)


class PrimitiveCompileError(ValueError):
    """Raised when a primitive record cannot be compiled or validated.

    Attributes:
        schema_version: the contract that rejected the record.
        record_id: value of the spec's id_field if extractable, else None.
        errors: list of (json_pointer, message), sorted by pointer.
        hint: optional remediation hint.
    """

    def __init__(
        self,
        *,
        schema_version: str,
        record_id: str | None,
        errors: list[tuple[str, str]],
        hint: str | None = None,
    ) -> None:
        self.schema_version = schema_version
        self.record_id = record_id
        self.errors = sorted(errors, key=lambda item: (item[0], item[1]))
        self.hint = hint

        detail = "; ".join(f"{pointer or '/'}: {message}" for pointer, message in self.errors)
        if not detail:
            detail = "primitive compilation failed"
        subject = f"{schema_version}"
        if record_id is not None:
            subject = f"{subject} record {record_id}"
        if hint:
            detail = f"{detail}; hint: {hint}"
        super().__init__(f"{subject}: {detail}")


@dataclass(frozen=True)
class PrimitiveSpec:
    schema_version: str
    schema_path: Path
    id_field: str
    hash_field: str | None = None
    hash_omit_fields: tuple[str, ...] = ()
    normalize: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def canonical_record_bytes(value: Any) -> bytes:
    """Return the canonical JSON byte form for new primitive hashes."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_ref(data: bytes) -> str:
    """Return a lowercase sha256 digest with the required prefix."""

    return "sha256:" + hashlib.sha256(data).hexdigest()


def finalize_record(spec: PrimitiveSpec, record: Mapping[str, Any], *, validate: bool = True) -> dict[str, Any]:
    """Finalize one primitive record at the compile boundary.

    The boundary rejects validation-only specs; otherwise it stamps the schema version, applies a pure normalizer,
    computes a self content hash when the spec declares one, and validates the result. It
    never fills semantic defaults or coerces invalid values.
    """

    if spec.schema_version in VALIDATION_ONLY_SCHEMA_VERSIONS: raise PrimitiveCompileError(schema_version=spec.schema_version, record_id=_record_id(record, spec.id_field), errors=[("", "schema is validation-only and cannot be finalized")], hint="use validate_record")
    result = copy.deepcopy(dict(record))
    result["schema_version"] = spec.schema_version

    if spec.normalize is not None:
        result = spec.normalize(result)
        if not isinstance(result, dict):
            raise PrimitiveCompileError(
                schema_version=spec.schema_version,
                record_id=_record_id(result, spec.id_field),
                errors=[("", "normalize must return a dict")],
            )

    if spec.hash_field:
        omitted = set(spec.hash_omit_fields)
        omitted.add(spec.hash_field)
        preimage = {key: value for key, value in result.items() if key not in omitted}
        result[spec.hash_field] = sha256_ref(canonical_record_bytes(preimage))

    if validate:
        _validate_record(spec, result, semantic=True)

    return result
def validate_record(spec: PrimitiveSpec, record: Mapping[str, Any]) -> dict[str, Any]: result = copy.deepcopy(dict(record)); _validate_record(spec, result, semantic=True); return result


_CORE_SCHEMA_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("bb.effective_config_graph.v1", "graph_id", "graph_hash"),
    ("bb.config_mutation_record.v1", "mutation_id", None),
    ("bb.config_explanation.v1", "explanation_id", None),
    ("bb.context_resource_pack.v1", "pack_id", None),
    ("bb.capability_registry.v1", "registry_id", None),
    ("bb.extension_hook_execution.v1", "execution_id", None),
    ("bb.resource_ref.v1", "uri", None),
    ("bb.resource_access.v1", "access_id", None),
    ("bb.blob_ref.v1", "blob_id", None),
    ("bb.external_protocol_session.v1", "session_id", None),
    ("bb.provider_route.v1", "route_id", None),
    ("bb.memory_compaction_plan.v1", "plan_id", None),
    ("bb.transcript_continuation_patch.v1", "patch_id", None),
    ("bb.work_item.v2", "work_item_id", None),
    ("bb.side_effect_broker.v1", "broker_id", None),
    ("bb.projection_event.v1", "projection_event_id", None),
    ("bb.effective_operation_policy.v1", "policy_id", None),
    ("bb.effective_tool_surface.v1", "surface_id", "surface_hash"),
    ("bb.environment_selector.v2", "selector_id", None),
    ("bb.kernel_event.v2", "event_id", None),
    ("bb.tool_call.v2", "call_id", None),
    ("bb.tool_execution_outcome.v2", "call_id", None),
    ("bb.tool_model_render.v2", "call_id", None),
    ("bb.tool_spec.v2", "name", None),
    ("bb.session_transcript.v2", "session_id", None),
    ("bb.registry.v1", "registry_id", None),
    ("bb.lane_validation_report.v1", "lane_id", None),
) + tuple((version, id_field, None) for version, id_field in _VALIDATION_ONLY_ID_FIELDS.items())

_E4_SCHEMA_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("bb.e4.support_claim.v2", "claim_id", None),
    ("bb.e4.support_claim.v3", "claim_id", None),
    ("bb.e4.support_claim.v4", "claim_id", None),
    ("bb.e4.lane_def.v1", "lane_id", None),
    ("bb.e4.lane_def.v2", "lane_id", None),
    ("bb.e4.lane_inventory.v2", "inventory_id", None),
    ("bb.e4.target_coverage.v2", "target_family", None),
    ("bb.e4.artifact_catalog.v1", "catalog_id", None),
    ("bb.e4.artifact_catalog.v2", "catalog_id", None),
    ("bb.e4.lane_inventory.v1", "inventory_id", None),
)

_SCHEMA_SPECS: tuple[tuple[str, str, str | None], ...] = _CORE_SCHEMA_SPECS + _E4_SCHEMA_SPECS


def _build_spec_registry(schema_specs: tuple[tuple[str, str, str | None], ...]) -> dict[str, PrimitiveSpec]:
    return {
        schema_version: PrimitiveSpec(
            schema_version=schema_version,
            schema_path=_SCHEMAS_DIR / f"{schema_version}.schema.json",
            id_field=id_field,
            hash_field=hash_field,
        )
        for schema_version, id_field, hash_field in schema_specs
    }

CORE_SPEC_REGISTRY: dict[str, PrimitiveSpec] = _build_spec_registry(_CORE_SCHEMA_SPECS)
E4_SPEC_REGISTRY: dict[str, PrimitiveSpec] = _build_spec_registry(_E4_SCHEMA_SPECS)
SPEC_REGISTRY: dict[str, PrimitiveSpec] = {**CORE_SPEC_REGISTRY, **E4_SPEC_REGISTRY}
FROZEN_VALIDATION_SPEC_REGISTRY: dict[str, PrimitiveSpec] = _build_spec_registry(
    tuple((version, id_field, None) for version, id_field in _FROZEN_VALIDATION_ID_FIELDS.items())
)


def get_spec(schema_version: str) -> PrimitiveSpec:
    try:
        return SPEC_REGISTRY.get(schema_version) or FROZEN_VALIDATION_SPEC_REGISTRY[schema_version]
    except KeyError as exc:
        raise PrimitiveCompileError(
            schema_version=schema_version,
            record_id=None,
            errors=[("", "unknown schema_version")],
            hint="use one of SPEC_REGISTRY.keys() or a frozen validation schema",
        ) from exc


@lru_cache(maxsize=None)
def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def _schema_registry() -> Registry:
    resources: list[tuple[str, Resource]] = []
    registered_names = {_COMMON_SCHEMA_NAME, _E4_COMMON_SCHEMA_NAME}
    common_schema = _load_json(_SCHEMAS_DIR / _COMMON_SCHEMA_NAME)
    common_resource = Resource.from_contents(common_schema)
    e4_common_schema = _load_json(_SCHEMAS_DIR / _E4_COMMON_SCHEMA_NAME)
    e4_common_resource = Resource.from_contents(e4_common_schema)
    resources.extend(
        [
            (_COMMON_SCHEMA_NAME, common_resource),
            (_COMMON_SCHEMA_URI, common_resource),
            (common_schema["$id"], common_resource),
            (_E4_COMMON_SCHEMA_NAME, e4_common_resource),
            (_E4_COMMON_SCHEMA_URI, e4_common_resource),
            (e4_common_schema["$id"], e4_common_resource),
        ]
    )

    for schema_path in sorted(_SCHEMAS_DIR.glob("bb.*.schema.json")):
        schema_name = schema_path.name
        if schema_name in registered_names:
            continue
        schema = _load_json(_SCHEMAS_DIR / schema_name)
        resource = Resource.from_contents(schema)
        resources.append((schema_name, resource))
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id != schema_name:
            resources.append((schema_id, resource))
        registered_names.add(schema_name)

    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _validator(schema_path: Path) -> Draft202012Validator:
    schema = _load_json(schema_path)
    return Draft202012Validator(
        schema,
        registry=_schema_registry(),
        format_checker=FormatChecker(),
    )


def _validate_record(spec: PrimitiveSpec, record: Mapping[str, Any], *, semantic: bool = False) -> None:
    validator = _validator(spec.schema_path)
    errors = [(_json_pointer(error.absolute_path), error.message) for error in validator.iter_errors(record)]
    if semantic and spec.schema_version == "bb.work_item.v2":
        errors.extend(_work_item_v2_semantic_errors(record))
    if errors:
        raise PrimitiveCompileError(
            schema_version=spec.schema_version,
            record_id=_record_id(record, spec.id_field),
            errors=errors,
        )


def _work_item_v2_semantic_errors(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    attempts = record.get("attempts")
    if not isinstance(attempts, list):
        return errors
    check = lambda condition, pointer, message: errors.append((pointer, message)) if condition else None
    seen = {field: set() for field in ("attempt_id", "session_ref")}
    for index, attempt in ((index, attempt) for index, attempt in enumerate(attempts) if isinstance(attempt, Mapping)):
        check(attempt.get("number") != index + 1, f"/attempts/{index}/number", f"attempt number must be {index + 1} at this position")
        errors.extend((f"/attempts/{index}/{field}", f"{field} must be unique across attempts") for field, identities in seen.items() if isinstance(identity := attempt.get(field), str) and (identity in identities or identities.add(identity)))
    status, current = record.get("status"), attempts[-1] if attempts and isinstance(attempts[-1], Mapping) else None
    dependencies, satisfied, budget, work_item_id, children, active_lease, used_lease_ids, resume_policy = (record.get(field) for field in ("dependency_refs", "satisfied_dependency_refs", "budget", "work_item_id", "child_work_item_ids", "active_lease", "used_lease_ids", "resume_policy"))
    check(status in (current_messages := {"completed": "completed Work Item requires a matching closed current attempt", "failed": "failed Work Item requires a matching closed current attempt", "waiting": "waiting Work Item requires a matching closed current attempt", "paused": "paused Work Item requires a matching closed current attempt", "running": "running Work Item requires the running attempt to be current"}) and (current is None or current.get("status") != status), "/attempts", current_messages.get(status, ""))
    check(status in {"completed", "failed", "canceled"} and current is not None and current.get("status") == status and record.get("terminal_reason") != current.get("reason"), "/terminal_reason", f"must match the current {status} attempt reason")
    check(status == "running" and current is not None and current.get("status") == "running" and isinstance(active_lease, Mapping) and active_lease.get("worker_id") != current.get("worker_id"), "/active_lease/worker_id", "must match the current running attempt worker_id")
    resume_mode = resume_policy.get("mode") if isinstance(resume_policy, Mapping) else None
    check(status in {"waiting", "paused"} and resume_mode == "never", "/resume_policy/mode", f"{status} Work Item cannot use never resume policy"); check(status in {"waiting", "paused"} and resume_mode == "checkpoint" and current is not None and not isinstance(current.get("checkpoint_ref"), str), f"/attempts/{len(attempts) - 1}/checkpoint_ref", "checkpoint resume policy requires the current attempt checkpoint_ref")
    if isinstance(active_lease, Mapping):
        lease_id, acquired_at, expires_at = (active_lease.get(field) for field in ("lease_id", "acquired_at", "expires_at"))
        check(isinstance(used_lease_ids, list) and isinstance(lease_id, str) and (not used_lease_ids or used_lease_ids[-1] != lease_id), "/active_lease/lease_id", "must be the last used_lease_ids entry")
        if isinstance(acquired_at, str) and isinstance(expires_at, str):
            with suppress(TypeError, ValueError):
                check(datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.fromisoformat(acquired_at.replace("Z", "+00:00")), "/active_lease/expires_at", "must be later than active_lease.acquired_at")
                check(status == "running" and current is not None and isinstance(current.get("started_at"), str) and datetime.fromisoformat(current["started_at"].replace("Z", "+00:00")) >= datetime.fromisoformat(expires_at.replace("Z", "+00:00")), f"/attempts/{len(attempts) - 1}/started_at", "must be earlier than active_lease.expires_at")
    check(isinstance(used_lease_ids, list) and len(used_lease_ids) < len(attempts), "/used_lease_ids", "must contain at least one acquired lease ID per attempt")
    if isinstance(dependencies, list) and isinstance(satisfied, list):
        dependency_set, satisfied_set = ({value for value in refs if isinstance(value, str)} for refs in (dependencies, satisfied))
        errors.extend((f"/satisfied_dependency_refs/{index}", "must also appear in dependency_refs") for index, dependency_ref in enumerate(satisfied) if isinstance(dependency_ref, str) and dependency_ref not in dependency_set)
        check(status == "blocked" and dependency_set == satisfied_set, "/status", "blocked Work Item requires at least one unsatisfied dependency")
        check(status not in {"blocked", "canceled"} and dependency_set != satisfied_set, "/satisfied_dependency_refs", f"all dependencies must be satisfied while status is {status}")
    limits, usage = (budget.get("limits"), budget.get("usage")) if isinstance(budget, Mapping) else (None, None)
    for limit_name, usage_name in ((("token_limit", "tokens"), ("cost_limit_microusd", "cost_microusd"), ("wall_time_limit_ms", "wall_time_ms")) if isinstance(limits, Mapping) and isinstance(usage, Mapping) else ()):
        limit, amount = limits.get(limit_name), usage.get(usage_name)
        check(isinstance(limit, int) and not isinstance(limit, bool) and isinstance(amount, int) and amount > limit, f"/budget/usage/{usage_name}", f"must not exceed budget.limits.{limit_name}")
    check(isinstance(work_item_id, str) and record.get("parent_work_item_id") == work_item_id, "/parent_work_item_id", "must not equal work_item_id")
    errors.extend((f"/child_work_item_ids/{index}", "must not equal work_item_id") for index, child_work_item_id in enumerate(children if isinstance(children, list) else ()) if isinstance(work_item_id, str) and child_work_item_id == work_item_id)
    check(record.get("latest_checkpoint_ref") != next((attempt.get("checkpoint_ref") for attempt in reversed(attempts) if isinstance(attempt, Mapping) and isinstance(attempt.get("checkpoint_ref"), str)), None), "/latest_checkpoint_ref", "must match the latest non-null attempt checkpoint_ref")
    return errors


def _record_id(record: Any, id_field: str) -> str | None:
    if not isinstance(record, Mapping):
        return None
    value = record.get(id_field)
    if value is None:
        return None
    return str(value)


def _json_pointer(path: Any) -> str:
    parts = []
    for part in path:
        text = str(part).replace("~", "~0").replace("/", "~1")
        parts.append(text)
    if not parts:
        return ""
    return "/" + "/".join(parts)


__all__ = [
    "PrimitiveCompileError",
    "PrimitiveSpec",
    "CORE_SPEC_REGISTRY",
    "E4_SPEC_REGISTRY",
    "SPEC_REGISTRY",
    "canonical_record_bytes",
    "finalize_record",
    "validate_record",
    "get_spec",
    "sha256_ref",
]
