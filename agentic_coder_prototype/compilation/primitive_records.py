from __future__ import annotations

from dataclasses import dataclass
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

    The boundary only stamps the schema version, applies a pure normalizer when supplied,
    computes a self content hash when the spec declares one, and validates the result. It
    never fills semantic defaults or coerces invalid values.
    """

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
        _validate_record(spec, result)

    return result


_SCHEMA_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("bb.effective_config_graph.v1", "graph_id", "graph_hash"),
    ("bb.config_mutation_record.v1", "mutation_id", None),
    ("bb.context_resource_pack.v1", "pack_id", None),
    ("bb.capability_registry.v1", "registry_id", None),
    ("bb.extension_hook_execution.v1", "execution_id", None),
    ("bb.resource_ref.v1", "uri", None),
    ("bb.resource_access.v1", "access_id", None),
    ("bb.blob_ref.v1", "blob_id", None),
    ("bb.external_protocol_session.v1", "session_id", None),
    ("bb.provider_route.v1", "route_id", None),
    ("bb.memory_compaction_plan.v1", "plan_id", None),
    ("bb.work_item.v1", "work_item_id", None),
    ("bb.side_effect_broker.v1", "broker_id", None),
    ("bb.projection_event.v1", "projection_event_id", None),
    ("bb.effective_operation_policy.v1", "policy_id", None),
    ("bb.effective_tool_surface.v1", "surface_id", "surface_hash"),
    ("bb.e4.support_claim.v2", "claim_id", None),
    ("bb.e4.support_claim.v3", "claim_id", None),
    ("bb.e4.support_claim.v4", "claim_id", None),
    ("bb.e4.lane_def.v1", "lane_id", None),
    ("bb.e4.lane_def.v2", "lane_id", None),
    ("bb.coordination_slice.v2", "slice_id", None),
    ("bb.coordination_pack.v3", "pack_id", None),
    ("bb.e4.lane_inventory.v2", "inventory_id", None),
    ("bb.e4.target_coverage.v2", "target_family", None),
    ("bb.environment_selector.v2", "selector_id", None),
    ("bb.kernel_event.v2", "event_id", None),
    ("bb.tool_call.v2", "call_id", None),
    ("bb.tool_execution_outcome.v2", "call_id", None),
    ("bb.tool_model_render.v2", "call_id", None),
    ("bb.tool_spec.v2", "name", None),
    ("bb.session_transcript.v2", "session_id", None),
    ("bb.e4.artifact_catalog.v1", "catalog_id", None),
    ("bb.e4.artifact_catalog.v2", "catalog_id", None),
    ("bb.e4.lane_inventory.v1", "inventory_id", None),
    ("bb.registry.v1", "registry_id", None),
)

SPEC_REGISTRY: dict[str, PrimitiveSpec] = {
    schema_version: PrimitiveSpec(
        schema_version=schema_version,
        schema_path=_SCHEMAS_DIR / f"{schema_version}.schema.json",
        id_field=id_field,
        hash_field=hash_field,
    )
    for schema_version, id_field, hash_field in _SCHEMA_SPECS
}


def get_spec(schema_version: str) -> PrimitiveSpec:
    try:
        return SPEC_REGISTRY[schema_version]
    except KeyError as exc:
        raise PrimitiveCompileError(
            schema_version=schema_version,
            record_id=None,
            errors=[("", "unknown schema_version")],
            hint="use one of SPEC_REGISTRY.keys()",
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

    for schema_version, _, _ in _SCHEMA_SPECS:
        schema_name = f"{schema_version}.schema.json"
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


def _validate_record(spec: PrimitiveSpec, record: Mapping[str, Any]) -> None:
    validator = _validator(spec.schema_path)
    errors = [(_json_pointer(error.absolute_path), error.message) for error in validator.iter_errors(record)]
    if errors:
        raise PrimitiveCompileError(
            schema_version=spec.schema_version,
            record_id=_record_id(record, spec.id_field),
            errors=errors,
        )


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
    "SPEC_REGISTRY",
    "canonical_record_bytes",
    "finalize_record",
    "get_spec",
    "sha256_ref",
]
