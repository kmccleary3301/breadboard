from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict
from urllib.parse import urlparse

from .primitive_records import PrimitiveCompileError, finalize_record, get_spec

SCHEMA_CONTEXT_RESOURCE_PACK = "bb.context_resource_pack.v1"
SCHEMA_CAPABILITY_REGISTRY = "bb.capability_registry.v1"
SCHEMA_EXTENSION_HOOK_EXECUTION = "bb.extension_hook_execution.v1"
SCHEMA_RESOURCE_REF = "bb.resource_ref.v1"
SCHEMA_RESOURCE_ACCESS = "bb.resource_access.v1"
SCHEMA_BLOB_REF = "bb.blob_ref.v1"
SCHEMA_EXTERNAL_PROTOCOL_SESSION = "bb.external_protocol_session.v1"
SCHEMA_PROVIDER_ROUTE = "bb.provider_route.v1"
SCHEMA_EFFECTIVE_OPERATION_POLICY = "bb.effective_operation_policy.v1"
SCHEMA_MEMORY_COMPACTION_PLAN = "bb.memory_compaction_plan.v1"
SCHEMA_WORK_ITEM = "bb.work_item.v1"
SCHEMA_PROJECTION_EVENT = "bb.projection_event.v1"
SCHEMA_SIDE_EFFECT_BROKER = "bb.side_effect_broker.v1"

_ALLOWED_SOURCE_KINDS = {
    "system-prompt",
    "developer-instruction",
    "user-input",
    "project-file",
    "workspace-state",
    "memory",
    "tool-output",
    "mcp-resource",
    "policy",
    "config",
}
_ALLOWED_CAPABILITY_TYPES = {"tool", "model", "resource_resolver", "extension_hook", "runtime", "permission", "storage"}
_ALLOWED_VISIBILITY = {"model_visible", "provider_visible", "host_only", "policy_hidden"}
_ALLOWED_DISCOVERY_MODES = {"static_manifest", "runtime_probe", "host_advertised", "provider_advertised", "manual"}
_ALLOWED_EXPOSURE_MODES = {"model_visible", "provider_visible", "host_only", "policy_hidden", "conditional"}
_ALLOWED_EXPOSURE_STATES = {"exposed", "hidden", "conditional"}
_ALLOWED_EFFECT_STATUSES = {"applied", "suppressed", "failed", "pending"}
_ALLOWED_ACCESS_OPERATIONS = {"read", "write", "append", "delete", "stat", "list", "resolve"}


class ContextSourceInput(TypedDict, total=False):
    source_id: str
    source_kind: str
    uri: str
    content: str
    content_hash: str
    order: int
    model_visible: bool
    host_visible: bool


class CapabilityDeclarationInput(TypedDict, total=False):
    capability_id: str
    capability_type: str
    name: str
    disabled: bool
    provider: dict[str, Any] | None
    discovery_mode: str
    evidence_ref: str | None
    exposure_state: str
    exposure_mode: str
    model_visible: bool
    surface_refs: list[str]
    reason: str | None
    scopes: list[str]
    visibility: str
    metadata: dict[str, Any]


class HookEffectInput(TypedDict, total=False):
    effect_id: str
    effect_type: str
    effect_ref: str
    status: str


class ResourceAccessInput(TypedDict, total=False):
    access_id: str
    uri: str
    content: str
    operation: str
    boundary: str
    returned_size_bytes: int | None
    redacted: bool
    approval_required: bool
    resolver_id: str
    scope_id: str
    root_uri: str
    media_type: str
    storage_uri: str
    storage_resolver_id: str
    encrypted_at_rest: bool
    retention_policy: str
    sidecars: list[dict[str, Any]]


def _compile_error(schema_version: str, record_id: str | None, pointer: str, message: str) -> PrimitiveCompileError:
    return PrimitiveCompileError(schema_version=schema_version, record_id=record_id, errors=[(pointer, message)])


def _require_text(value: Any, *, schema_version: str, record_id: str | None, pointer: str) -> str:
    if not isinstance(value, str) or not value:
        raise _compile_error(schema_version, record_id, pointer, "must be a non-empty string")
    return value


def _optional_text(value: Any, *, schema_version: str, record_id: str | None, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _compile_error(schema_version, record_id, pointer, "must be a string or null")
    return value


def _require_bool(value: Any, *, schema_version: str, record_id: str | None, pointer: str) -> bool:
    if not isinstance(value, bool):
        raise _compile_error(schema_version, record_id, pointer, "must be a boolean")
    return value


def _require_int(value: Any, *, schema_version: str, record_id: str | None, pointer: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _compile_error(schema_version, record_id, pointer, f"must be an integer >= {minimum}")
    return value


def _require_sequence(value: Any, *, schema_version: str, record_id: str | None, pointer: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _compile_error(schema_version, record_id, pointer, "must be an array")
    return value


def _require_mapping(value: Any, *, schema_version: str, record_id: str | None, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _compile_error(schema_version, record_id, pointer, "must be an object")
    return value


def _copy_mapping(value: Any, *, schema_version: str, record_id: str | None, pointer: str) -> dict[str, Any]:
    if value is None:
        return {}
    return dict(_require_mapping(value, schema_version=schema_version, record_id=record_id, pointer=pointer))


def _copy_record(value: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(value))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _hash_no_prefix(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _actor(kind: str, actor_id: str) -> dict[str, str]:
    return {"actor_kind": kind, "actor_id": actor_id}


def compile_context_resource_pack(
    *,
    pack_id: str,
    sources: Sequence[Mapping[str, Any]],
    render_profile: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a bb.context_resource_pack.v1 from explicit context source rows.

    Required inputs: pack_id plus source_id/source_kind/uri/order/visibility and either content or content_hash per source.
    Hash behavior: content_hash is preserved when supplied, otherwise derived from the supplied content bytes.
    Error behavior: invalid source kinds, missing semantic fields, and schema failures raise PrimitiveCompileError.
    """

    record_id = pack_id if isinstance(pack_id, str) else None
    _require_text(pack_id, schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer="/pack_id")
    source_rows = _require_sequence(sources, schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer="/sources")
    ordered: list[dict[str, Any]] = []
    for index, raw_source in enumerate(source_rows):
        source = _require_mapping(raw_source, schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}")
        source_id = _require_text(source.get("source_id"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/source_id")
        source_kind = _require_text(source.get("source_kind"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/source_kind")
        if source_kind not in _ALLOWED_SOURCE_KINDS:
            raise _compile_error(SCHEMA_CONTEXT_RESOURCE_PACK, record_id, f"/sources/{index}/source_kind", f"expected one of: {', '.join(sorted(_ALLOWED_SOURCE_KINDS))}")
        uri = _require_text(source.get("uri"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/uri")
        order = _require_int(source.get("order"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/order")
        model_visible = _require_bool(source.get("model_visible"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/model_visible")
        host_visible = _require_bool(source.get("host_visible"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/host_visible")
        content_hash = source.get("content_hash")
        if content_hash is None:
            content = _require_text(source.get("content"), schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/content")
            content_hash = _sha256_text(content)
        else:
            content_hash = _require_text(content_hash, schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer=f"/sources/{index}/content_hash")
        ordered.append(
            {
                "source_id": source_id,
                "source_kind": source_kind,
                "uri": uri,
                "content_hash": content_hash,
                "order": order,
                "model_visible": model_visible,
                "host_visible": host_visible,
            }
        )
    ordered.sort(key=lambda item: (item["order"], item["source_id"]))
    render_order = [source["source_id"] for source in ordered]
    merged_metadata = _copy_mapping(metadata, schema_version=SCHEMA_CONTEXT_RESOURCE_PACK, record_id=record_id, pointer="/metadata") if metadata is not None else {}
    if render_profile is not None:
        merged_metadata["render_profile"] = render_profile
    record = {
        "pack_id": pack_id,
        "sources": ordered,
        "render_order": render_order,
        "render_plan_hash": _sha256_json({"pack_id": pack_id, "render_order": render_order, "source_hashes": [source["content_hash"] for source in ordered]}),
        "model_visibility": {
            "visible_source_ids": [source["source_id"] for source in ordered if source["model_visible"]],
            "redacted_source_ids": [source["source_id"] for source in ordered if not source["model_visible"]],
        },
        "host_visibility": {
            "visible_source_ids": [source["source_id"] for source in ordered if source["host_visible"]],
            "hidden_source_ids": [source["source_id"] for source in ordered if not source["host_visible"]],
        },
        "metadata": merged_metadata,
    }
    return finalize_record(get_spec(SCHEMA_CONTEXT_RESOURCE_PACK), record)


def compile_capability_registry(
    *,
    registry_id: str,
    run_id: str | None,
    environment_id: str,
    declarations: Sequence[Mapping[str, Any]],
    generated_at: str,
    registry_revision: int = 1,
    basis_registry_ref: str | None = None,
) -> dict[str, Any]:
    """Compile a bb.capability_registry.v1 from explicit capability declarations.

    Required inputs: registry/run/environment ids, generated_at, and declarations with capability_id/capability_type/name.
    Hash behavior: this primitive has no content hash; finalize_record performs schema validation only.
    Error behavior: invalid capability_type/visibility/exposure/discovery values raise PrimitiveCompileError, never coercion.
    """

    record_id = registry_id if isinstance(registry_id, str) else None
    _require_text(registry_id, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer="/registry_id")
    _optional_text(run_id, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer="/subject/run_id")
    _require_text(environment_id, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer="/subject/environment_id")
    _require_text(generated_at, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer="/generated_at")
    _require_int(registry_revision, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer="/registry_revision")
    declaration_rows = _require_sequence(declarations, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer="/declarations")
    seen_capability_ids: set[str] = set()
    capabilities: list[dict[str, Any]] = []
    mutation_log: list[dict[str, Any]] = []
    for index, raw_declaration in enumerate(declaration_rows):
        declaration = _require_mapping(raw_declaration, schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}")
        capability_id = _require_text(declaration.get("capability_id"), schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/capability_id")
        capability_type = _require_text(declaration.get("capability_type"), schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/capability_type")
        if capability_type not in _ALLOWED_CAPABILITY_TYPES:
            raise _compile_error(SCHEMA_CAPABILITY_REGISTRY, record_id, f"/declarations/{index}/capability_type", f"expected one of: {', '.join(sorted(_ALLOWED_CAPABILITY_TYPES))}")
        name = _require_text(declaration.get("name"), schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/name")
        discovery_mode = str(declaration.get("discovery_mode") or "static_manifest")
        if discovery_mode not in _ALLOWED_DISCOVERY_MODES:
            raise _compile_error(SCHEMA_CAPABILITY_REGISTRY, record_id, f"/declarations/{index}/discovery_mode", f"expected one of: {', '.join(sorted(_ALLOWED_DISCOVERY_MODES))}")
        model_visible = bool(declaration.get("model_visible", True))
        exposure_mode = str(declaration.get("exposure_mode") or ("model_visible" if model_visible else "host_only"))
        if exposure_mode not in _ALLOWED_EXPOSURE_MODES:
            raise _compile_error(SCHEMA_CAPABILITY_REGISTRY, record_id, f"/declarations/{index}/exposure_mode", f"expected one of: {', '.join(sorted(_ALLOWED_EXPOSURE_MODES))}")
        exposure_state = str(declaration.get("exposure_state") or "exposed")
        if exposure_state not in _ALLOWED_EXPOSURE_STATES:
            raise _compile_error(SCHEMA_CAPABILITY_REGISTRY, record_id, f"/declarations/{index}/exposure_state", f"expected one of: {', '.join(sorted(_ALLOWED_EXPOSURE_STATES))}")
        visibility = str(declaration.get("visibility") or ("model_visible" if model_visible else "host_only"))
        if visibility not in _ALLOWED_VISIBILITY:
            raise _compile_error(SCHEMA_CAPABILITY_REGISTRY, record_id, f"/declarations/{index}/visibility", f"expected one of: {', '.join(sorted(_ALLOWED_VISIBILITY))}")
        disabled = bool(declaration.get("disabled", False))
        duplicate = capability_id in seen_capability_ids
        outcome = "ignored" if disabled else ("shadowed" if duplicate else "applied")
        operation = "disable" if disabled else ("shadow" if duplicate else "discover")
        mutation_log.append(
            {
                "mutation_id": f"{registry_id}_mutation_{index}",
                "operation": operation,
                "target_type": capability_type,
                "target_ref": capability_id,
                "phase": "discovery",
                "source": {
                    "source_type": discovery_mode,
                    "source_ref": str(declaration.get("evidence_ref") or f"capability-source://{capability_id}"),
                },
                "outcome": outcome,
                "occurred_at": generated_at,
                "basis_ref": None,
                "affected_capability_ids": [capability_id],
                "before_ref": None,
                "after_ref": None if outcome != "applied" else f"bb.capability_registry.v1:{registry_id}#{capability_id}",
                "reason": "disabled declaration" if disabled else ("shadowed by first declaration" if duplicate else "discovered"),
                "details": {"shadowed": duplicate, "disabled": disabled},
            }
        )
        if duplicate or disabled:
            continue
        seen_capability_ids.add(capability_id)
        capabilities.append(
            {
                "capability_id": capability_id,
                "capability_type": capability_type,
                "name": name,
                "provider": _copy_mapping(declaration.get("provider"), schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/provider") or None,
                "discovery": {
                    "state": "discovered",
                    "mode": discovery_mode,
                    "evidence_ref": _optional_text(declaration.get("evidence_ref"), schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/evidence_ref"),
                    "discovered_at": generated_at,
                },
                "exposure": {
                    "state": exposure_state,
                    "mode": exposure_mode,
                    "model_visible": model_visible,
                    "surface_refs": list(declaration.get("surface_refs") or ([] if not model_visible else ["surface_default"])),
                    "reason": _optional_text(declaration.get("reason") or "First declaration wins for the effective registry.", schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/reason"),
                },
                "scopes": list(declaration.get("scopes") or []),
                "visibility": visibility,
                "metadata": _copy_mapping(declaration.get("metadata"), schema_version=SCHEMA_CAPABILITY_REGISTRY, record_id=record_id, pointer=f"/declarations/{index}/metadata"),
            }
        )
    record = {
        "registry_id": registry_id,
        "generated_at": generated_at,
        "subject": {"environment_id": environment_id, "run_id": run_id},
        "capabilities": capabilities,
        "registry_revision": registry_revision,
        "basis_registry_ref": basis_registry_ref,
        "mutation_log": mutation_log,
    }
    return finalize_record(get_spec(SCHEMA_CAPABILITY_REGISTRY), record)


def compile_extension_hook_execution(
    *,
    execution_id: str,
    hook_id: str,
    hook_type: str,
    event_id: str,
    event_type: str,
    effects: Sequence[Mapping[str, Any]],
    status: str,
    started_at: str,
    duration_ms: int,
    model_provider_visibility: Mapping[str, Any],
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a bb.extension_hook_execution.v1 from explicit hook event/effect rows.

    Required inputs: execution/hook/event identifiers, effect rows, status, started_at, duration_ms, and visibility.
    Hash behavior: this primitive has no content hash; effect refs are caller-owned semantic refs.
    Error behavior: invalid effect status or malformed semantic input raises PrimitiveCompileError.
    """

    record_id = execution_id if isinstance(execution_id, str) else None
    for pointer, value in (("/execution_id", execution_id), ("/hook_id", hook_id), ("/hook_type", hook_type), ("/event_id", event_id), ("/event_type", event_type), ("/status", status), ("/started_at", started_at)):
        _require_text(value, schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer=pointer)
    _require_int(duration_ms, schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer="/duration_ms")
    visibility = _copy_mapping(model_provider_visibility, schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer="/model_provider_visibility")
    effect_rows = []
    for index, raw_effect in enumerate(_require_sequence(effects, schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer="/effects")):
        effect = _require_mapping(raw_effect, schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer=f"/effects/{index}")
        effect_status = _require_text(effect.get("status"), schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer=f"/effects/{index}/status")
        if effect_status not in _ALLOWED_EFFECT_STATUSES:
            raise _compile_error(SCHEMA_EXTENSION_HOOK_EXECUTION, record_id, f"/effects/{index}/status", f"expected one of: {', '.join(sorted(_ALLOWED_EFFECT_STATUSES))}")
        effect_rows.append(
            {
                "effect_id": _require_text(effect.get("effect_id"), schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer=f"/effects/{index}/effect_id"),
                "effect_type": _require_text(effect.get("effect_type"), schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer=f"/effects/{index}/effect_type"),
                "effect_ref": _require_text(effect.get("effect_ref"), schema_version=SCHEMA_EXTENSION_HOOK_EXECUTION, record_id=record_id, pointer=f"/effects/{index}/effect_ref"),
                "status": effect_status,
            }
        )
    record = {
        "execution_id": execution_id,
        "hook_id": hook_id,
        "hook_type": hook_type,
        "event": {
            "event_id": event_id,
            "event_type": event_type,
            "event_ref": f"event://{event_id}",
            "emitted_at": started_at,
            "resource_refs": [effect["effect_ref"] for effect in effect_rows],
        },
        "status": status,
        "started_at": started_at,
        "duration_ms": duration_ms,
        "effects": effect_rows,
        "model_provider_visibility": visibility,
        "error": _copy_record(error) if error is not None else None,
    }
    return finalize_record(get_spec(SCHEMA_EXTENSION_HOOK_EXECUTION), record)


def compile_resource_access_bundle(
    *,
    access_id: str,
    uri: str,
    content: str,
    operation: str,
    boundary: str,
    returned_size_bytes: int | None,
    redacted: bool,
    approval_required: bool,
    resolver_id: str,
    scope_id: str,
    root_uri: str,
    media_type: str,
    storage_uri: str | None,
    storage_resolver_id: str | None,
    encrypted_at_rest: bool,
    retention_policy: str,
    sidecars: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compile bb.resource_ref.v1, bb.blob_ref.v1, and bb.resource_access.v1 from an explicit access event.

    Required inputs: access identity, URI/content, operation, resolver/scope/storage/retention facts, visibility, and sidecars.
    Hash behavior: content_hash/digest are derived from supplied content; no evidence-file hashes are computed here.
    Error behavior: invalid operation or malformed semantic input raises PrimitiveCompileError.
    """

    record_id = access_id if isinstance(access_id, str) else None
    for pointer, value in (("/access_id", access_id), ("/uri", uri), ("/content", content), ("/operation", operation), ("/boundary", boundary), ("/resolver_id", resolver_id), ("/scope_id", scope_id), ("/root_uri", root_uri), ("/media_type", media_type)):
        _require_text(value, schema_version=SCHEMA_RESOURCE_ACCESS, record_id=record_id, pointer=pointer)
    if operation not in _ALLOWED_ACCESS_OPERATIONS:
        raise _compile_error(SCHEMA_RESOURCE_ACCESS, record_id, "/operation", f"expected one of: {', '.join(sorted(_ALLOWED_ACCESS_OPERATIONS))}")
    _require_bool(redacted, schema_version=SCHEMA_RESOURCE_ACCESS, record_id=record_id, pointer="/redacted")
    _require_bool(approval_required, schema_version=SCHEMA_RESOURCE_ACCESS, record_id=record_id, pointer="/approval_required")
    _require_bool(encrypted_at_rest, schema_version=SCHEMA_BLOB_REF, record_id=f"blob_{access_id}", pointer="/storage/encrypted_at_rest")
    sidecar_rows = _require_sequence(sidecars, schema_version=SCHEMA_BLOB_REF, record_id=f"blob_{access_id}", pointer="/sidecars")
    content_bytes = content.encode("utf-8")
    returned = len(content_bytes) if returned_size_bytes is None else _require_int(returned_size_bytes, schema_version=SCHEMA_RESOURCE_ACCESS, record_id=record_id, pointer="/returned_size_bytes")
    parsed = urlparse(uri)
    content_digest = _hash_no_prefix(content)
    content_hash = f"sha256:{content_digest}"
    parent = parsed.path.rsplit("/", 1)[0] or "/"
    resource = finalize_record(
        get_spec(SCHEMA_RESOURCE_REF),
        {
            "uri": uri,
            "scheme": parsed.scheme or "file",
            "resolver_id": resolver_id,
            "authority": parsed.netloc or None,
            "path": parsed.path,
            "query_hash": _sha256_text(parsed.query) if parsed.query else None,
            "fragment": parsed.fragment or None,
            "scope": {"scope_id": scope_id, "kind": "workspace", "boundary": boundary},
            "visibility": "model_visible",
            "immutability": {"mode": "snapshot", "content_hash": content_hash},
            "retention": {"policy": retention_policy, "expires_at": None},
            "containment": {"root_uri": root_uri, "parent_uri": f"file://{parent}", "relationship": "descendant"},
        },
    )
    blob = finalize_record(
        get_spec(SCHEMA_BLOB_REF),
        {
            "blob_id": f"blob_{access_id}",
            "digest": {"algorithm": "sha256", "value": content_digest},
            "media_type": media_type,
            "size_bytes": len(content_bytes),
            "storage": {
                "storage_class": "content_addressed",
                "uri": storage_uri or f"bb+blob://sha256/{content_digest}",
                "resolver_id": storage_resolver_id,
                "encrypted_at_rest": encrypted_at_rest,
            },
            "retention": {"policy": retention_policy, "expires_at": None, "legal_hold": False},
            "sidecars": [_copy_record(_require_mapping(sidecar, schema_version=SCHEMA_BLOB_REF, record_id=f"blob_{access_id}", pointer=f"/sidecars/{index}")) for index, sidecar in enumerate(sidecar_rows)],
        },
    )
    access = finalize_record(
        get_spec(SCHEMA_RESOURCE_ACCESS),
        {
            "access_id": access_id,
            "resource": resource,
            "operation": operation,
            "status": "completed",
            "content_hash": content_hash,
            "blob_refs": [blob],
            "truncation": {
                "truncated": returned < len(content_bytes),
                "original_size_bytes": len(content_bytes),
                "returned_size_bytes": returned,
                "strategy": "byte_limit" if returned < len(content_bytes) else None,
            },
            "redaction": {"redacted": redacted, "policy_ids": ["redact-secrets"] if redacted else [], "redaction_refs": ["redaction://default-secrets"] if redacted else []},
            "approval": {
                "required": approval_required,
                "status": "approved" if approval_required else "not_required",
                "approval_id": f"approval_{access_id}" if approval_required else None,
                "approver": "workspace-owner" if approval_required else None,
            },
            "model_provider_visibility": {"model_visible": True, "provider_visible": False},
        },
    )
    return {"resource_ref": resource, "blob_ref": blob, "resource_access": access}


def compile_protocol_provider_policy_bundle(
    *,
    protocol_session: Mapping[str, Any],
    provider_route: Mapping[str, Any],
    operation_policy: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Finalize explicit protocol, provider-route, and operation-policy records.

    Required inputs: complete semantic records for bb.external_protocol_session.v1, bb.provider_route.v1, and bb.effective_operation_policy.v1.
    Hash behavior: these primitives have no content hash; any hashes inside the records are caller-owned semantic values.
    Error behavior: schema validation failures raise PrimitiveCompileError with JSON pointers.
    """

    return {
        "external_protocol_session": finalize_record(get_spec(SCHEMA_EXTERNAL_PROTOCOL_SESSION), protocol_session),
        "provider_route": finalize_record(get_spec(SCHEMA_PROVIDER_ROUTE), provider_route),
        "effective_operation_policy": finalize_record(get_spec(SCHEMA_EFFECTIVE_OPERATION_POLICY), operation_policy),
    }


def compile_memory_work_bundle(*, memory_plan: Mapping[str, Any], work_item: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Finalize explicit memory compaction and work-item records.

    Required inputs: complete semantic records for bb.memory_compaction_plan.v1 and bb.work_item.v1.
    Hash behavior: these primitives have no content hash; embedded refs/hashes are caller-owned semantic values.
    Error behavior: schema validation failures raise PrimitiveCompileError with JSON pointers.
    """

    return {
        "memory_compaction_plan": finalize_record(get_spec(SCHEMA_MEMORY_COMPACTION_PLAN), memory_plan),
        "work_item": finalize_record(get_spec(SCHEMA_WORK_ITEM), work_item),
    }


def compile_projection_broker_bundle(*, projection_event: Mapping[str, Any], side_effect_broker: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Finalize explicit projection-event and side-effect-broker records.

    Required inputs: complete semantic records for bb.projection_event.v1 and bb.side_effect_broker.v1.
    Hash behavior: these primitives have no content hash; embedded refs/hashes are caller-owned semantic values.
    Error behavior: schema validation failures raise PrimitiveCompileError with JSON pointers.
    """

    return {
        "projection_event": finalize_record(get_spec(SCHEMA_PROJECTION_EVENT), projection_event),
        "side_effect_broker": finalize_record(get_spec(SCHEMA_SIDE_EFFECT_BROKER), side_effect_broker),
    }


__all__ = [
    "CapabilityDeclarationInput",
    "ContextSourceInput",
    "HookEffectInput",
    "ResourceAccessInput",
    "compile_capability_registry",
    "compile_context_resource_pack",
    "compile_extension_hook_execution",
    "compile_memory_work_bundle",
    "compile_projection_broker_bundle",
    "compile_protocol_provider_policy_bundle",
    "compile_resource_access_bundle",
]
