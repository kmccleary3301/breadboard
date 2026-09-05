#!/usr/bin/env python3
"""Generate public client bindings from the immutable operation catalog."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Final, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
CATALOG_RELATIVE: Final = Path("contracts/public/operations.v2.json")
KERNEL_EVENT_REGISTRY_RELATIVE: Final = Path(
    "contracts/kernel/registries/kernel_event_kinds.v1.json"
)
PROJECTION_MODULE_RELATIVE: Final = Path(
    "breadboard/product/runtime/public_event_projection.py"
)
EVENT_BINDINGS_RELATIVE: Final = Path(
    "sdk/ts/src/generated/session-event-bindings.ts"
)
PYTHON_EVENT_BINDINGS_RELATIVE: Final = Path(
    "breadboard_sdk/generated/session_event_bindings.py"
)
GENERATOR_PATH: Final = "scripts/quality/generate_public_bindings.py"
GENERATOR_VERSION: Final = "5"
SCHEMA_VERSION: Final = "bb.public_client_binding_manifest.v1"
GENERATED_FILE_MODE: Final = 0o644
DOCUMENT_MARKER: Final = "<!-- GENERATED FILE - do not edit by hand. -->"
DOCUMENT_COMMON_METADATA: Final = (
    "generator",
    "generator-version",
    "catalog-id",
    "catalog-sha256",
    "document-kind",
)
DOCUMENT_OPERATION_METADATA: Final = DOCUMENT_COMMON_METADATA + (
    "operation-id",
    "slug",
)
OPERATION_DOCUMENT_KIND: Final = "operation-reference"
INDEX_DOCUMENT_KIND: Final = "operation-index"
APPROVED_SCHEMA_LINK_TARGETS: Final = {
    "bb.problem.v1": "contracts/public/schemas/bb.problem.v1.schema.json",
    "bb.public_session_event.v1": "contracts/public/schemas/bb.public_session_event.v1.schema.json",
}
_DOC_SLUG_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VALID_LIFECYCLES = frozenset({"sync", "async"})
_VALID_EFFECTS = frozenset({"read", "write", "verify", "execute"})
_VALID_STABILITIES = frozenset({"experimental"})
_VALID_IDEMPOTENCY_MODES = frozenset({"idempotent", "keyed"})
_VALID_AUTH_MODES = frozenset({"none", "capability_gated"})
_VALID_SECRET_REFERENCES = frozenset({"references_only"})
NORMALIZED_FIELDS: Final = (
    "operation_id",
    "status",
    "http_method",
    "path",
    "cli_command",
    "python_client",
    "python_method",
    "typescript_client",
    "typescript_method",
    "action_id",
    "action_kind",
)
POLICY_FIELDS: Final = (
    "lifecycle",
    "idempotency_mode",
    "auth_mode",
    "required_capabilities",
)
_HTTP_METHODS: Final = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_EVENT_DECODER_CLASSIFICATIONS: Final[Mapping[str, tuple[str, str | None]]] = {
    "assistant_message": ("assistant-message", None),
    "user_message": ("input-text", "input_observed"),
    "provider_response": ("deprecated", None),
    "provider_response_v2": ("unsupported", None),
    "tool_call": ("tool-called", "tool_called"),
    "tool_result": ("tool-result-or-todo", "tool_result_observed"),
    "permission_request": ("permission-requested", "permission_requested"),
    "permission_response": ("permission-responded", "permission_responded"),
    "task_event": ("task-observed", "task_event_observed"),
    "session_control": ("session-control", "session_control_observed"),
    "turn_start": ("turn-start", "turn_started"),
    "guardrail_event": ("unsupported", None),
    "lifecycle_event": ("unsupported", None),
    "ctree_node": ("ctree-node", "ctree_node_observed"),
    "todo_event": ("todo", "todo_updated"),
    "ctree_snapshot": ("ctree-snapshot", "ctree_snapshot_observed"),
    "stream.gap": ("gap", "stream_gap_observed"),
    "assistant.message.start": ("assistant-start", "assistant_message_started"),
    "assistant.message.delta": ("text", "assistant_text_delta"),
    "assistant.message.end": ("optional-text", "assistant_text_completed"),
    "assistant.reasoning.delta": ("text", "assistant_reasoning_delta"),
    "assistant.thought_summary.delta": ("text", "assistant_thought_summary_delta"),
    "assistant.tool_call.start": ("assistant-tool-start", "assistant_tool_call_started"),
    "assistant.tool_call.delta": ("assistant-tool-delta", "assistant_tool_call_delta"),
    "assistant.tool_call.end": ("assistant-tool-end", "assistant_tool_call_completed"),
    "tool.exec.start": ("tool-exec-start", "tool_execution_started"),
    "tool.exec.stdout.delta": ("tool-exec-stdout", "tool_execution_stdout_delta"),
    "tool.exec.stderr.delta": ("tool-exec-stderr", "tool_execution_stderr_delta"),
    "tool.exec.end": ("tool-exec-end", "tool_execution_completed"),
    "assistant_delta": ("text", "assistant_text_delta"),
    "conversation.compaction.start": ("compaction-start", "conversation_compaction_started"),
    "conversation.compaction.end": ("compaction-end", "conversation_compaction_completed"),
    "checkpoint_list": ("checkpoint-list", "checkpoint_list_observed"),
    "checkpoint_restored": ("checkpoint-restored", "checkpoint_restored"),
    "skills_catalog": ("skills-catalog", "skills_catalog_observed"),
    "skills_selection": ("skills-selection", "skills_selection_observed"),
    "warning": ("warning", "warning_observed"),
    "reward_update": ("reward", "reward_updated"),
    "limits_update": ("limits", "limits_updated"),
    "completion": ("completion", "completion_observed"),
    "log_link": ("log-link", "log_linked"),
    "error": ("runtime-error", "runtime_error_observed"),
    "run_finished": ("run-finished", "run_finished"),
    "coordination_signal": ("unsupported", None),
    "coordination_review_verdict": ("unsupported", None),
    "coordination_directive": ("unsupported", None),
    "tool.result": ("tool-result-or-todo", "tool_result_observed"),
    "turn_completed": ("turn-completed", "turn_completed"),
    "turn_failed": ("turn-failed", "turn_failed"),
    "turn_cancelled": ("turn-cancelled", "turn_cancelled"),
}



class CatalogError(ValueError):
    """Raised when the catalog cannot be normalized into public bindings."""


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON bytes used for catalog hashing and manifests."""

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _require_string(value: Any, field: str, operation_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{operation_id}: {field} must be a non-empty string")
    return value


def _binding(
    row: Mapping[str, Any], surface: str, operation_id: str
) -> Mapping[str, Any]:
    bindings = row.get("bindings")
    if not isinstance(bindings, Mapping):
        raise CatalogError(f"{operation_id}: bindings must be an object")
    value = bindings.get(surface)
    if not isinstance(value, Mapping):
        raise CatalogError(f"{operation_id}: missing {surface} binding")
    return value


def _require_object(value: Any, field: str, operation_id: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{operation_id}: {field} must be an object")
    return value


def _require_enum(
    value: Any, field: str, operation_id: str, choices: frozenset[str]
) -> str:
    result = _require_string(value, field, operation_id)
    if result not in choices:
        raise CatalogError(f"{operation_id}: {field} must be one of {sorted(choices)}")
    return result


def _validate_docs_slug(value: Any, operation_id: str) -> str:
    slug = _require_string(value, "bindings.docs.slug", operation_id)
    path = PurePosixPath(slug)
    parts = slug.split("/")
    if (
        "\\" in slug
        or slug.startswith("/")
        or path.is_absolute()
        or str(path) != slug
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) != 3
        or parts[0] != "operations"
        or any(not _DOC_SLUG_SEGMENT.fullmatch(part) for part in parts)
    ):
        raise CatalogError(
            f"{operation_id}: bindings.docs.slug is not canonical: {slug!r}"
        )
    return slug


def _normalize_catalog(catalog: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(catalog, Mapping):
        raise CatalogError("catalog root must be an object")
    if catalog.get("contract_id") != "bb.public_operation_catalog.v2":
        raise CatalogError("catalog contract_id must be bb.public_operation_catalog.v2")
    if catalog.get("version") != 2 or catalog.get("status") != "current":
        raise CatalogError("catalog version/status must be 2/current")
    operations = catalog.get("operations")
    if not isinstance(operations, list):
        raise CatalogError("catalog operations must be an array")
    if len(operations) != 26:
        raise CatalogError(
            f"catalog must contain exactly 26 operations, got {len(operations)}"
        )

    normalized: list[dict[str, Any]] = []
    seen: dict[str, dict[tuple[str, ...], str]] = {
        "http_method_path": {},
        "cli_command": {},
        "python_method": {},
        "typescript_method": {},
        "action_id": {},
        "docs_slug": {},
    }
    seen_operation_ids: set[str] = set()
    for index, row in enumerate(operations):
        if not isinstance(row, Mapping):
            raise CatalogError(f"operations[{index}] must be an object")
        operation_id = _require_string(
            row.get("operation_id"), "operation_id", f"operations[{index}]"
        )
        if not _OPERATION_ID.fullmatch(operation_id):
            raise CatalogError(f"{operation_id}: malformed operation_id")
        if operation_id in seen_operation_ids:
            raise CatalogError(f"duplicate operation_id: {operation_id}")
        seen_operation_ids.add(operation_id)
        status = _require_string(row.get("status"), "status", operation_id)
        if status != "candidate":
            raise CatalogError(f"{operation_id}: status must be candidate")

        summary = _require_string(row.get("summary"), "summary", operation_id)
        lifecycle = _require_enum(
            row.get("lifecycle"), "lifecycle", operation_id, _VALID_LIFECYCLES
        )
        effects = _require_enum(
            row.get("effects"), "effects", operation_id, _VALID_EFFECTS
        )
        stability = _require_enum(
            row.get("stability"), "stability", operation_id, _VALID_STABILITIES
        )

        idempotency = _require_object(
            row.get("idempotency"), "idempotency", operation_id
        )
        idempotency_mode = _require_enum(
            idempotency.get("mode"),
            "idempotency.mode",
            operation_id,
            _VALID_IDEMPOTENCY_MODES,
        )
        idempotency_rule = _require_string(
            idempotency.get("rule"), "idempotency.rule", operation_id
        )
        auth_policy = _require_object(
            row.get("auth_policy"), "auth_policy", operation_id
        )
        auth_mode = _require_enum(
            auth_policy.get("mode"),
            "auth_policy.mode",
            operation_id,
            _VALID_AUTH_MODES,
        )
        secret_references = _require_enum(
            auth_policy.get("secret_references"),
            "auth_policy.secret_references",
            operation_id,
            _VALID_SECRET_REFERENCES,
        )
        capabilities_value = row.get("required_capabilities")
        if not isinstance(capabilities_value, list) or any(
            not isinstance(capability, str) or not capability
            for capability in capabilities_value
        ):
            raise CatalogError(
                f"{operation_id}: required_capabilities must be an array of strings"
            )
        if len(capabilities_value) != len(set(capabilities_value)):
            raise CatalogError(f"{operation_id}: required_capabilities must be unique")
        if auth_mode == "none" and capabilities_value:
            raise CatalogError(
                f"{operation_id}: auth_policy.mode=none requires no capabilities"
            )
        if auth_mode == "capability_gated" and not capabilities_value:
            raise CatalogError(
                f"{operation_id}: auth_policy.mode=capability_gated requires at least one capability"
            )
        capabilities = tuple(sorted(capabilities_value))
        schema_ids: dict[str, str | None] = {}
        for field in ("input_schema", "output_schema", "error_schema"):
            schema_ids[field] = _require_string(row.get(field), field, operation_id)
        event_schema = row.get("event_schema")
        if event_schema is not None:
            event_schema = _require_string(event_schema, "event_schema", operation_id)
        schema_ids["event_schema"] = event_schema

        openapi = _binding(row, "openapi", operation_id)
        method = _require_string(openapi.get("method"), "openapi.method", operation_id)
        if method not in _HTTP_METHODS:
            raise CatalogError(f"{operation_id}: malformed HTTP method {method!r}")
        path = _require_string(openapi.get("path"), "openapi.path", operation_id)
        if not path.startswith("/v1/"):
            raise CatalogError(f"{operation_id}: malformed HTTP path {path!r}")
        if openapi.get("operation_id") != operation_id:
            raise CatalogError(
                f"{operation_id}: openapi.operation_id must match operation_id"
            )

        bbh = _binding(row, "bbh", operation_id)
        cli_command = _require_string(bbh.get("command"), "bbh.command", operation_id)
        if not cli_command.startswith("bbh "):
            raise CatalogError(f"{operation_id}: malformed CLI command {cli_command!r}")

        python = _binding(row, "python_sdk", operation_id)
        python_client = _require_string(
            python.get("client"), "python_sdk.client", operation_id
        )
        python_method = _require_string(
            python.get("method"), "python_sdk.method", operation_id
        )
        if python_client != "BreadBoardClient" or not _IDENTIFIER.fullmatch(
            python_method
        ):
            raise CatalogError(f"{operation_id}: malformed Python SDK identity")

        typescript = _binding(row, "typescript_sdk", operation_id)
        typescript_client = _require_string(
            typescript.get("client"), "typescript_sdk.client", operation_id
        )
        typescript_method = _require_string(
            typescript.get("method"), "typescript_sdk.method", operation_id
        )
        if typescript_client != "BreadBoardClient" or not _IDENTIFIER.fullmatch(
            typescript_method
        ):
            raise CatalogError(f"{operation_id}: malformed TypeScript SDK identity")

        tui = _binding(row, "tui", operation_id)
        action_id = _require_string(tui.get("action_id"), "tui.action_id", operation_id)
        action_kind = _require_string(tui.get("kind"), "tui.kind", operation_id)
        if not action_id.startswith("public.") or action_kind not in {"action", "view"}:
            raise CatalogError(f"{operation_id}: malformed TUI identity")

        docs = _binding(row, "docs", operation_id)
        docs_owner = _require_string(docs.get("owner"), "docs.owner", operation_id)
        docs_status = _require_string(docs.get("status"), "docs.status", operation_id)
        if docs_status != "candidate":
            raise CatalogError(f"{operation_id}: docs.status must be candidate")
        docs_slug = _validate_docs_slug(docs.get("slug"), operation_id)

        values: dict[str, Any] = {
            "operation_id": operation_id,
            "status": status,
            "http_method": method,
            "path": path,
            "cli_command": cli_command,
            "python_client": python_client,
            "python_method": python_method,
            "typescript_client": typescript_client,
            "typescript_method": typescript_method,
            "action_id": action_id,
            "action_kind": action_kind,
            "summary": summary,
            "lifecycle": lifecycle,
            "effects": effects,
            "stability": stability,
            "idempotency_mode": idempotency_mode,
            "idempotency_rule": idempotency_rule,
            "auth_mode": auth_mode,
            "secret_references": secret_references,
            "required_capabilities": capabilities,
            "input_schema": schema_ids["input_schema"],
            "output_schema": schema_ids["output_schema"],
            "error_schema": schema_ids["error_schema"],
            "event_schema": schema_ids["event_schema"],
            "docs_owner": docs_owner,
            "docs_status": docs_status,
            "docs_slug": docs_slug,
        }
        identities = {
            "http_method_path": (method, path),
            "cli_command": (cli_command,),
            "python_method": (python_method,),
            "typescript_method": (typescript_method,),
            "action_id": (action_id,),
            "docs_slug": (docs_slug,),
        }
        for name, identity in identities.items():
            previous = seen[name].get(identity)
            if previous is not None:
                raise CatalogError(
                    f"duplicate {name} identity for {previous} and {operation_id}: {identity}"
                )
            seen[name][identity] = operation_id
        normalized.append(values)

    normalized.sort(key=lambda item: item["operation_id"])
    return tuple(normalized)


def _load_catalog(root: Path) -> Any:
    path = root / CATALOG_RELATIVE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"unable to load catalog {path}: {exc}") from exc

def _load_event_registry(root: Path) -> Any | None:
    path = root / KERNEL_EVENT_REGISTRY_RELATIVE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"unable to load event registry {path}: {exc}") from exc


def _normalize_event_registry(
    registry: Any,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(registry, Mapping):
        raise CatalogError("event registry root must be an object")
    if registry.get("schema_version") != "bb.registry.v1":
        raise CatalogError("event registry has an unsupported schema_version")
    if registry.get("registry_id") != "kernel_event_kinds":
        raise CatalogError("event registry has an unexpected registry_id")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise CatalogError("event registry entries must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise CatalogError(f"event registry entry {index} must be an object")
        event_id = entry.get("id")
        status = entry.get("status")
        metadata = entry.get("metadata")
        if not isinstance(event_id, str) or not event_id:
            raise CatalogError(f"event registry entry {index} has an invalid id")
        if event_id in seen:
            raise CatalogError(f"event registry has duplicate id {event_id}")
        if status not in {"active", "deprecated"}:
            raise CatalogError(f"{event_id}: event registry status is invalid")
        if not isinstance(metadata, Mapping):
            raise CatalogError(f"{event_id}: event registry metadata must be an object")
        disposition = _EVENT_DECODER_CLASSIFICATIONS.get(event_id)
        if disposition is None:
            raise CatalogError(
                f"{event_id}: missing explicit event decoder classification"
            )
        decoder, normalized_kind = disposition
        if status == "deprecated" and decoder != "deprecated":
            raise CatalogError(
                f"{event_id}: deprecated event requires a deprecated disposition"
            )
        seen.add(event_id)
        normalized.append(
            {
                "event_type": event_id,
                "registry_id": event_id,
                "source": "kernel_registry",
                "status": status,
                "classification": str(metadata.get("classification") or "unknown"),
                "decoder": decoder,
                "normalized_kind": normalized_kind,
                "payload_schema_version": (
                    str(metadata["payload_schema_version"])
                    if isinstance(metadata.get("payload_schema_version"), str)
                    else None
                ),
            }
        )
    return tuple(sorted(normalized, key=lambda row: row["event_type"]))


def _load_public_payload_schemas(root: Path) -> dict[str, str] | None:
    path = root / PROJECTION_MODULE_RELATIVE
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "_breadboard_public_event_projection_codegen",
        path,
    )
    if spec is None or spec.loader is None:
        raise CatalogError(f"unable to load projection module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CatalogError(f"unable to load projection module {path}: {exc}") from exc
    finally:
        sys.path.pop(0)
    schemas = getattr(module, "PUBLIC_PAYLOAD_SCHEMAS", None)
    if not isinstance(schemas, Mapping) or not schemas:
        raise CatalogError("projection module has no PUBLIC_PAYLOAD_SCHEMAS mapping")
    if any(
        not isinstance(kind, str)
        or not kind
        or not isinstance(schema, str)
        or not schema
        for kind, schema in schemas.items()
    ):
        raise CatalogError("projection module has invalid public payload schemas")
    return dict(sorted(schemas.items()))


def _canonical_event_registry_bytes(registry: Mapping[str, Any]) -> bytes:
    entries = registry.get("entries")
    if isinstance(entries, list) and all(
        isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
        for entry in entries
    ):
        registry = dict(registry)
        registry["entries"] = sorted(entries, key=lambda entry: entry["id"])
    return canonical_bytes(registry)


def canonical_event_registry_sha256(registry: Mapping[str, Any]) -> str:
    return _sha256(_canonical_event_registry_bytes(registry))


def _render_session_event_bindings(
    rows: Sequence[Mapping[str, Any]],
    public_payload_schemas: Mapping[str, str],
    registry_sha256: str,
    projection_sha256: str,
    catalog_id: str,
    catalog_sha256: str,
) -> bytes:
    event_types = " | ".join(_ts_string(str(row["event_type"])) for row in rows)
    decoders = " | ".join(
        _ts_string(decoder)
        for decoder in sorted({str(row["decoder"]) for row in rows})
    )
    out = [
        "// GENERATED FILE - do not edit by hand.",
        f"// generator: {GENERATOR_PATH}",
        f"// generator-version: {GENERATOR_VERSION}",
        f"// catalog-id: {catalog_id}",
        f"// catalog-sha256: {catalog_sha256}",
        f"// kernel-event-registry: {KERNEL_EVENT_REGISTRY_RELATIVE.as_posix()}",
        f"// kernel-event-registry-sha256: {registry_sha256}",
        f"// public-projection-module: {PROJECTION_MODULE_RELATIVE.as_posix()}",
        f"// public-projection-sha256: {projection_sha256}",
        "",
        f"export type GeneratedEventType = {event_types};",
        f"export type GeneratedEventDecoder = {decoders};",
        "",
        "export interface GeneratedEventKindMetadata {",
        "  readonly eventType: GeneratedEventType;",
        "  readonly registryId: string | null;",
        '  readonly source: "kernel_registry" | "bridge";',
        '  readonly status: "active" | "deprecated";',
        "  readonly classification: string;",
        "  readonly decoder: GeneratedEventDecoder;",
        "  readonly normalizedKind: string | null;",
        "  readonly payloadSchemaVersion: string | null;",
        "}",
        "",
        "export const GENERATED_EVENT_KIND_METADATA: readonly GeneratedEventKindMetadata[] = [",
    ]
    for row in rows:
        out.extend(
            [
                "  {",
                f"    eventType: {_ts_string(str(row['event_type']))},",
                f"    registryId: {_ts_string(str(row['registry_id'])) if row['registry_id'] is not None else 'null'},",
                f"    source: {_ts_string(str(row['source']))},",
                f"    status: {_ts_string(str(row['status']))},",
                f"    classification: {_ts_string(str(row['classification']))},",
                f"    decoder: {_ts_string(str(row['decoder']))},",
                f"    normalizedKind: {_ts_string(str(row['normalized_kind'])) if row['normalized_kind'] is not None else 'null'},",
                f"    payloadSchemaVersion: {_ts_string(str(row['payload_schema_version'])) if row['payload_schema_version'] is not None else 'null'},",
                "  },",
            ]
        )
    out.extend(
        [
            "] as const;",
            "",
            "export const GENERATED_EVENT_KIND_METADATA_BY_TYPE: Readonly<Record<string, GeneratedEventKindMetadata>> = {",
        ]
    )
    for index, row in enumerate(rows):
        out.append(
            f"  {_ts_string(str(row['event_type']))}: GENERATED_EVENT_KIND_METADATA[{index}],"
        )
    out.extend(
        [
            "} as const;",
            "",
            "export const PUBLIC_SESSION_EVENT_PAYLOAD_SCHEMAS = {",
        ]
    )
    for kind, schema in sorted(public_payload_schemas.items()):
        out.append(f"  {_ts_string(kind)}: {_ts_string(schema)},")
    out.extend(
        [
            "} as const satisfies Readonly<Record<string, string>>;",
            "export type PublicSessionEventKind = keyof typeof PUBLIC_SESSION_EVENT_PAYLOAD_SCHEMAS;",
            "export type PublicSessionEventPayloadSchema = (typeof PUBLIC_SESSION_EVENT_PAYLOAD_SCHEMAS)[PublicSessionEventKind];",
            "",
        ]
    )
    return "\n".join(out).encode("utf-8")


def _render_python_session_event_bindings(
    schemas: Mapping[str, str], projection_sha256: str
) -> bytes:
    out = [
        "# GENERATED FILE - do not edit by hand.",
        f"# generator: {GENERATOR_PATH}",
        f"# generator-version: {GENERATOR_VERSION}",
        f"# public-projection-sha256: {projection_sha256}",
        "",
        "from types import MappingProxyType",
        "from typing import Final, Literal, Mapping",
        "",
        "PublicSessionEventKind = Literal[",
        *(f"    {_py_string(kind)}," for kind in schemas),
        "]",
        "PublicSessionEventPayloadSchema = Literal[",
        *(f"    {_py_string(schema)}," for schema in sorted(set(schemas.values()))),
        "]",
        "PublicSessionLifecycleEventKind = Literal[",
        *(
            f"    {_py_string(kind)},"
            for kind, schema in schemas.items()
            if schema == "bb.payload.product_session.lifecycle.v1"
        ),
        "]",
        "",
        "PUBLIC_SESSION_EVENT_PAYLOAD_SCHEMAS: Final[Mapping[str, str]] = MappingProxyType(",
        "    {",
        *(
            f"        {_py_string(kind)}: {_py_string(schema)},"
            for kind, schema in schemas.items()
        ),
        "    }",
        ")",
        "",
    ]
    return "\n".join(out).encode("utf-8")


def _event_bindings_outputs(
    root: Path,
    catalog_id: str,
    catalog_sha256: str,
) -> dict[Path, bytes]:
    registry = _load_event_registry(root)
    if registry is None:
        return {}
    rows = list(_normalize_event_registry(registry))
    known = {str(row["event_type"]) for row in rows}
    for event_type, disposition in _EVENT_DECODER_CLASSIFICATIONS.items():
        if event_type in known:
            continue
        decoder, normalized_kind = disposition
        rows.append(
            {
                "event_type": event_type,
                "registry_id": None,
                "source": "bridge",
                "status": "active",
                "classification": "bridge",
                "decoder": decoder,
                "normalized_kind": normalized_kind,
                "payload_schema_version": None,
            }
        )
    rows.sort(key=lambda row: str(row["event_type"]))
    schemas = _load_public_payload_schemas(root)
    if schemas is None:
        raise CatalogError(
            f"event registry requires projection module {PROJECTION_MODULE_RELATIVE}"
        )
    projection_sha256 = _sha256(canonical_bytes(schemas))
    return {
        root / EVENT_BINDINGS_RELATIVE: _render_session_event_bindings(
            rows,
            schemas,
            canonical_event_registry_sha256(registry),
            projection_sha256,
            catalog_id,
            catalog_sha256,
        ),
        root / PYTHON_EVENT_BINDINGS_RELATIVE: _render_python_session_event_bindings(
            schemas, projection_sha256
        ),
    }


def _canonical_catalog_bytes(catalog: Any) -> bytes:
    if isinstance(catalog, Mapping) and isinstance(catalog.get("operations"), list):
        operations = catalog["operations"]
        if all(
            isinstance(row, Mapping) and isinstance(row.get("operation_id"), str)
            for row in operations
        ):
            normalized_operations = []
            for row in operations:
                normalized = dict(row)
                capabilities = normalized.get("required_capabilities")
                if isinstance(capabilities, list) and all(
                    isinstance(capability, str) for capability in capabilities
                ):
                    normalized["required_capabilities"] = sorted(capabilities)
                normalized_operations.append(normalized)
            catalog = dict(catalog)
            catalog["operations"] = sorted(
                normalized_operations,
                key=lambda row: row["operation_id"],
            )
    return canonical_bytes(catalog)


def canonical_catalog_sha256(catalog: Any) -> str:
    """Hash the catalog without semantically irrelevant collection order."""
    return _sha256(_canonical_catalog_bytes(catalog))


def _py_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _py_capabilities(value: Sequence[str]) -> str:
    return (
        "("
        + ", ".join(_py_string(capability) for capability in value)
        + ("," if len(value) == 1 else "")
        + ")"
    )


def _render_python_module(
    rows: Sequence[Mapping[str, Any]], catalog_id: str, catalog_sha256: str
) -> bytes:
    out = [
        "# GENERATED FILE - do not edit by hand.",
        f"# generator: {GENERATOR_PATH}",
        f"# generator-version: {GENERATOR_VERSION}",
        f"# catalog-id: {catalog_id}",
        f"# catalog-sha256: {catalog_sha256}",
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from types import MappingProxyType",
        "from typing import Final, Literal, Mapping",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class PublicOperationBinding:",
        "    operation_id: str",
        "    status: str",
        "    http_method: str",
        "    path: str",
        "    cli_command: str",
        "    python_client: str",
        "    python_method: str",
        "    typescript_client: str",
        "    typescript_method: str",
        "    action_id: str",
        "    action_kind: str",
        '    lifecycle: Literal["sync", "async"]',
        '    idempotency_mode: Literal["idempotent", "keyed"]',
        '    auth_mode: Literal["none", "capability_gated"]',
        "    required_capabilities: tuple[str, ...]",
        "",
        "",
        "PUBLIC_OPERATION_BINDINGS: Final[tuple[PublicOperationBinding, ...]] = (",
    ]
    for row in rows:
        out.append("    PublicOperationBinding(")
        for field in NORMALIZED_FIELDS + POLICY_FIELDS[:-1]:
            out.append(f"        {field}={_py_string(row[field])},")
        out.append(
            f"        required_capabilities={_py_capabilities(row['required_capabilities'])},"
        )
        out.append("    ),")
    out.extend(
        [
            ")",
            "",
            "PUBLIC_BINDINGS_BY_OPERATION_ID: Final[Mapping[str, PublicOperationBinding]] = (",
            "    MappingProxyType(",
            "        {",
        ]
    )
    for index, row in enumerate(rows):
        operation_id = row["operation_id"]
        out.append(
            f"            {_py_string(operation_id)}: PUBLIC_OPERATION_BINDINGS[{index}],"
        )
    out.extend(
        [
            "        }",
            "    )",
            ")",
            "",
            "__all__ = [",
            '    "PublicOperationBinding",',
            '    "PUBLIC_OPERATION_BINDINGS",',
            '    "PUBLIC_BINDINGS_BY_OPERATION_ID",',
            "]",
            "",
        ]
    )
    return "\n".join(out).encode("utf-8")


def _render_python_init(catalog_id: str, catalog_sha256: str) -> bytes:
    return (
        "# GENERATED FILE - do not edit by hand.\n"
        f"# generator: {GENERATOR_PATH}\n"
        f"# generator-version: {GENERATOR_VERSION}\n"
        f"# catalog-id: {catalog_id}\n"
        f"# catalog-sha256: {catalog_sha256}\n\n"
        "from .public_bindings import (\n"
        "    PUBLIC_BINDINGS_BY_OPERATION_ID,\n"
        "    PUBLIC_OPERATION_BINDINGS,\n"
        "    PublicOperationBinding,\n"
        ")\n\n"
        "__all__ = [\n"
        '    "PublicOperationBinding",\n'
        '    "PUBLIC_OPERATION_BINDINGS",\n'
        '    "PUBLIC_BINDINGS_BY_OPERATION_ID",\n'
        "]\n"
    ).encode("utf-8")


def _ts_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_typescript(
    rows: Sequence[Mapping[str, Any]], catalog_id: str, catalog_sha256: str
) -> bytes:
    http_methods = " | ".join(
        _ts_string(method) for method in sorted({row["http_method"] for row in rows})
    )
    operation_ids = " | ".join(_ts_string(row["operation_id"]) for row in rows)
    action_ids = " | ".join(_ts_string(row["action_id"]) for row in rows)
    out = [
        "// GENERATED FILE - do not edit by hand.",
        f"// generator: {GENERATOR_PATH}",
        f"// generator-version: {GENERATOR_VERSION}",
        f"// catalog-id: {catalog_id}",
        f"// catalog-sha256: {catalog_sha256}",
        "",
        f"export type HttpMethod = {http_methods};",
        f"export type PublicOperationId = {operation_ids};",
        f"export type PublicActionId = {action_ids};",
        "",
        "export interface PublicOperationBinding {",
        "  readonly operationId: PublicOperationId;",
        '  readonly status: "candidate";',
        "  readonly httpMethod: HttpMethod;",
        "  readonly path: string;",
        "  readonly cliCommand: string;",
        '  readonly pythonClient: "BreadBoardClient";',
        "  readonly pythonMethod: string;",
        '  readonly typescriptClient: "BreadBoardClient";',
        "  readonly typescriptMethod: string;",
        "  readonly actionId: PublicActionId;",
        '  readonly actionKind: "action" | "view";',
        '  readonly lifecycle: "sync" | "async";',
        '  readonly idempotencyMode: "idempotent" | "keyed";',
        '  readonly authMode: "none" | "capability_gated";',
        "  readonly requiredCapabilities: readonly string[];",
        "}",
        "",
        "export const PUBLIC_OPERATION_BINDINGS: readonly PublicOperationBinding[] = [",
    ]
    for row in rows:
        capabilities = ", ".join(
            _ts_string(capability) for capability in row["required_capabilities"]
        )
        out.extend(
            [
                "  {",
                f"    operationId: {_ts_string(row['operation_id'])},",
                f"    status: {_ts_string(row['status'])},",
                f"    httpMethod: {_ts_string(row['http_method'])},",
                f"    path: {_ts_string(row['path'])},",
                f"    cliCommand: {_ts_string(row['cli_command'])},",
                f"    pythonClient: {_ts_string(row['python_client'])},",
                f"    pythonMethod: {_ts_string(row['python_method'])},",
                f"    typescriptClient: {_ts_string(row['typescript_client'])},",
                f"    typescriptMethod: {_ts_string(row['typescript_method'])},",
                f"    actionId: {_ts_string(row['action_id'])},",
                f"    actionKind: {_ts_string(row['action_kind'])},",
                f"    lifecycle: {_ts_string(row['lifecycle'])},",
                f"    idempotencyMode: {_ts_string(row['idempotency_mode'])},",
                f"    authMode: {_ts_string(row['auth_mode'])},",
                f"    requiredCapabilities: [{capabilities}],",
                "  },",
            ]
        )
    out.extend(
        [
            "] as const;",
            "",
            "export const PUBLIC_BINDINGS_BY_OPERATION_ID: Readonly<Record<PublicOperationId, PublicOperationBinding>> = {",
        ]
    )
    for index, row in enumerate(rows):
        out.append(
            f"  {_ts_string(row['operation_id'])}: PUBLIC_OPERATION_BINDINGS[{index}],"
        )
    out.extend(
        [
            "} as const;",
            "",
            "export const PUBLIC_BINDINGS_BY_ACTION_ID: Readonly<Record<PublicActionId, PublicOperationBinding>> = {",
        ]
    )
    for index, row in enumerate(rows):
        out.append(
            f"  {_ts_string(row['action_id'])}: PUBLIC_OPERATION_BINDINGS[{index}],"
        )
    out.extend(
        [
            "} as const;",
            "",
            "export interface PublicRouteEntry {",
            "  readonly path: string;",
            "  readonly method: HttpMethod;",
            "  readonly operationId: PublicOperationId;",
            "}",
            "",
            "export const PUBLIC_ROUTES: readonly PublicRouteEntry[] = [",
        ]
    )
    for row in rows:
        out.append(
            f"  {{ path: {_ts_string(row['path'])}, method: {_ts_string(row['http_method'])}, operationId: {_ts_string(row['operation_id'])} }},"
        )
    out.extend(
        [
            "] as const;",
            "",
        ]
    )
    return "\n".join(out).encode("utf-8")


_DOCUMENT_METADATA_LINE = re.compile(r"^<!-- ([a-z][a-z0-9-]*): ([^<>\r\n]+) -->$")


def parse_generated_document_metadata(content: bytes | str) -> dict[str, str]:
    """Parse and strictly validate the generated-document metadata preamble."""
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogError("generated document is not valid UTF-8") from exc
    elif isinstance(content, str):
        text = content
    else:
        raise CatalogError("generated document content must be bytes or text")

    lines = text.splitlines()
    if not lines or lines[0] != DOCUMENT_MARKER:
        raise CatalogError("generated document is missing its ownership marker")
    metadata: dict[str, str] = {}
    index = 1
    while index < len(lines) and lines[index]:
        match = _DOCUMENT_METADATA_LINE.fullmatch(lines[index])
        if match is None:
            raise CatalogError("generated document has malformed metadata")
        key, value = match.groups()
        if key in metadata:
            raise CatalogError(f"generated document repeats metadata key {key}")
        metadata[key] = value
        index += 1
    if index >= len(lines):
        raise CatalogError("generated document metadata is missing its body separator")
    kind = metadata.get("document-kind")
    if kind == OPERATION_DOCUMENT_KIND:
        expected = set(DOCUMENT_OPERATION_METADATA)
    elif kind == INDEX_DOCUMENT_KIND:
        expected = set(DOCUMENT_COMMON_METADATA)
    else:
        raise CatalogError(f"generated document has unknown document-kind {kind!r}")
    if set(metadata) != expected:
        missing = sorted(expected - set(metadata))
        extra = sorted(set(metadata) - expected)
        raise CatalogError(
            f"generated document metadata keys mismatch: missing={missing}, extra={extra}"
        )
    return metadata


def validate_generated_document_metadata(
    content: bytes | str,
    *,
    catalog_id: str,
    catalog_sha256: str,
    operation_id: str | None = None,
    slug: str | None = None,
    document_kind: str | None = None,
) -> dict[str, str]:
    """Validate metadata identity and return its parsed fields."""
    metadata = parse_generated_document_metadata(content)
    expected_kind = document_kind or (
        OPERATION_DOCUMENT_KIND if operation_id is not None else INDEX_DOCUMENT_KIND
    )
    expected = {
        "generator": GENERATOR_PATH,
        "generator-version": GENERATOR_VERSION,
        "catalog-id": catalog_id,
        "catalog-sha256": catalog_sha256,
        "document-kind": expected_kind,
    }
    if operation_id is not None:
        expected["operation-id"] = operation_id
    if slug is not None:
        expected["slug"] = _validate_docs_slug(slug, operation_id or "<document>")
    if metadata != expected:
        mismatches = [
            key
            for key in sorted(set(metadata) | set(expected))
            if metadata.get(key) != expected.get(key)
        ]
        raise CatalogError(
            "generated document metadata identity mismatch: " + ", ".join(mismatches)
        )
    return metadata


def _schema_display(schema_id: str | None, source: Path) -> str:
    if schema_id is None:
        return "none"
    target = APPROVED_SCHEMA_LINK_TARGETS.get(schema_id)
    if target is None:
        return f"`{schema_id}`"
    relative = os.path.relpath(target, start=source.parent).replace(os.sep, "/")
    return f"[`{schema_id}`]({relative})"


def _document_header(
    *,
    catalog_id: str,
    catalog_sha256: str,
    operation_id: str | None = None,
    slug: str | None = None,
    document_kind: str,
) -> list[str]:
    metadata = {
        "generator": GENERATOR_PATH,
        "generator-version": GENERATOR_VERSION,
        "catalog-id": catalog_id,
        "catalog-sha256": catalog_sha256,
        "document-kind": document_kind,
    }
    if operation_id is not None:
        metadata["operation-id"] = operation_id
    if slug is not None:
        metadata["slug"] = slug
    keys = (
        DOCUMENT_OPERATION_METADATA
        if operation_id is not None
        else DOCUMENT_COMMON_METADATA
    )
    return [DOCUMENT_MARKER, *(f"<!-- {key}: {metadata[key]} -->" for key in keys), ""]


def _render_operation_document(
    row: Mapping[str, Any], catalog_id: str, catalog_sha256: str
) -> bytes:
    slug = str(row["docs_slug"])
    source = Path("docs/reference/public") / f"{slug}.md"
    out = _document_header(
        catalog_id=catalog_id,
        catalog_sha256=catalog_sha256,
        operation_id=str(row["operation_id"]),
        slug=slug,
        document_kind=OPERATION_DOCUMENT_KIND,
    )
    out.extend(
        [
            f"# {row['summary']}",
            "",
            f"Candidate public operation reference for `{row['operation_id']}`.",
            "",
            "## Contract",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Status | `{row['status']}` |",
            f"| HTTP | `{row['http_method']} {row['path']}` |",
            f"| CLI | `{row['cli_command']}` |",
            f"| Lifecycle | `{row['lifecycle']}` |",
            f"| Effects | `{row['effects']}` |",
            f"| Stability | `{row['stability']}` |",
            f"| Idempotency | `{row['idempotency_mode']}` — {row['idempotency_rule']} |",
            f"| Authentication | `{row['auth_mode']}` (`{row['secret_references']}`) |",
            f"| Capabilities | {', '.join(f'`{item}`' for item in row['required_capabilities']) or 'none'} |",
            "",
            "## Bindings",
            "",
            f"- OpenAPI: `{row['http_method']} {row['path']}` (`{row['operation_id']}`)",
            f"- Python: `{row['python_client']}.{row['python_method']}`",
            f"- TypeScript: `{row['typescript_client']}.{row['typescript_method']}`",
            f"- TUI: `{row['action_id']}` (`{row['action_kind']}`)",
            f"- CLI: `{row['cli_command']}`",
            f"- Documentation: `{row['docs_owner']}` (`{row['docs_status']}`)",
            "",
            "## Schemas",
            "",
            f"- Input catalog ID (unpublished): `{row['input_schema']}`",
            f"- Output catalog ID (unpublished): `{row['output_schema']}`",
            (
                "- Response transport: SSE `text/event-stream`"
                if row["operation_id"] == "session.events"
                else "- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)"
            ),
            f"- Error: {_schema_display(row['error_schema'], source)}",
            f"- Event: {_schema_display(row['event_schema'], source)}",
            "",
        ]
    )
    return "\n".join(out).encode("utf-8")


def _render_document_index(
    rows: Sequence[Mapping[str, Any]], catalog_id: str, catalog_sha256: str
) -> bytes:
    out = _document_header(
        catalog_id=catalog_id,
        catalog_sha256=catalog_sha256,
        document_kind=INDEX_DOCUMENT_KIND,
    )
    out.extend(
        [
            "# Public operation reference",
            "",
            "Candidate documentation generated from the public operation catalog.",
            "",
            "## Operations",
            "",
        ]
    )
    for row in rows:
        out.append(
            f"- [`{row['operation_id']}`]({row['docs_slug']}.md) — {row['summary']}"
        )
    out.append("")
    return "\n".join(out).encode("utf-8")


def _render_manifest(
    rows: Sequence[Mapping[str, Any]],
    surface: str,
    catalog_id: str,
    catalog_sha256: str,
) -> bytes:
    if surface == "python_sdk":
        operations = [
            {
                "client": row["python_client"],
                "method": row["python_method"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]
    elif surface == "typescript_sdk":
        operations = [
            {
                "client": row["typescript_client"],
                "method": row["typescript_method"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]
    elif surface == "tui":
        operations = [
            {
                "action_id": row["action_id"],
                "kind": row["action_kind"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]
    else:  # pragma: no cover - targets are fixed below
        raise AssertionError(surface)
    return canonical_bytes(
        {
            "audience": "public",
            "candidate_status": "candidate",
            "catalog_id": catalog_id,
            "catalog_sha256": catalog_sha256,
            "execution_claimed": False,
            "generated_by": GENERATOR_PATH,
            "generator_version": GENERATOR_VERSION,
            "operations": operations,
            "parity_claimed": False,
            "schema_version": SCHEMA_VERSION,
            "surface": surface,
        }
    )


def build_outputs(root: Path | str | None = None) -> dict[Path, bytes]:
    """Build every catalog-owned output without writing to disk."""
    repo_root = Path(ROOT if root is None else root).resolve()

    catalog = _load_catalog(repo_root)
    rows = _normalize_catalog(catalog)
    catalog_id = str(catalog["contract_id"])
    catalog_sha256 = canonical_catalog_sha256(catalog)
    outputs: dict[Path, bytes] = {
        repo_root
        / "breadboard/product/operations/generated_bindings.py": _render_python_module(
            rows, catalog_id, catalog_sha256
        ),
        repo_root
        / "breadboard_sdk/generated/public_bindings.py": _render_python_module(
            rows, catalog_id, catalog_sha256
        ),
        repo_root / "breadboard_sdk/generated/__init__.py": _render_python_init(
            catalog_id, catalog_sha256
        ),
        repo_root / "sdk/ts/src/generated/public-bindings.ts": _render_typescript(
            rows, catalog_id, catalog_sha256
        ),
        repo_root
        / "breadboard_sdk/generated/public_surface_manifest.v1.json": _render_manifest(
            rows, "python_sdk", catalog_id, catalog_sha256
        ),
        repo_root
        / "sdk/ts/src/generated/public_surface_manifest.v1.json": _render_manifest(
            rows, "typescript_sdk", catalog_id, catalog_sha256
        ),
        repo_root
        / "tui_skeleton/src/generated/public_surface_manifest.v1.json": _render_manifest(
            rows, "tui", catalog_id, catalog_sha256
        ),
    }
    outputs.update(_event_bindings_outputs(repo_root, catalog_id, catalog_sha256))
    for row in rows:
        docs_path = repo_root / "docs/reference/public" / f"{row['docs_slug']}.md"
        outputs[docs_path] = _render_operation_document(row, catalog_id, catalog_sha256)
    outputs[repo_root / "docs/reference/public/index.md"] = _render_document_index(
        rows, catalog_id, catalog_sha256
    )
    return outputs


def path_has_symlink_component(path: Path, root: Path) -> bool:
    """Return whether an existing component below root is a symbolic link."""
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _check_existing_document_ownership(
    outputs: Mapping[Path, bytes],
) -> None:
    """Refuse to replace human-owned Markdown before any output is written."""
    repo_root = Path(ROOT).resolve()
    docs_root = repo_root / "docs" / "reference" / "public"
    for path, content in outputs.items():
        if path.suffix != ".md":
            continue
        if not path.is_relative_to(docs_root) or path_has_symlink_component(
            path, repo_root
        ):
            raise CatalogError(f"refusing to write document through a symlink: {path}")
        if not path.exists():
            continue
        if not path.is_file():
            raise CatalogError(f"refusing to overwrite non-regular document: {path}")
        try:
            existing = parse_generated_document_metadata(path.read_bytes())
            expected = parse_generated_document_metadata(content)
        except CatalogError as exc:
            raise CatalogError(
                f"refusing to overwrite non-generated document: {path}"
            ) from exc
        if any(
            existing.get(key) != expected.get(key)
            for key in expected
            if key not in {"catalog-sha256", "generator-version"}
        ):
            raise CatalogError(
                f"refusing to overwrite document owned by another generator: {path}"
            )


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # chmod(path) is available on all supported platforms; apply it to the
        # same-directory temporary inode before the atomic replacement.
        os.chmod(temporary, GENERATED_FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify generated outputs without writing"
    )
    args = parser.parse_args(argv)
    try:
        outputs = build_outputs()
        if not args.check:
            _check_existing_document_ownership(outputs)
    except CatalogError as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2

    stale = [
        path
        for path, content in outputs.items()
        if not path.is_file() or path.read_bytes() != content
    ]
    if args.check:
        if stale:
            for path in sorted(stale, key=lambda item: str(item.relative_to(ROOT))):
                print(f"stale generated public binding: {path.relative_to(ROOT)}")
            return 1
        print(f"public binding codegen check: OK ({len(outputs)} files current)")
        return 0

    for path, content in sorted(outputs.items(), key=lambda item: str(item[0])):
        _write_atomic(path, content)
    print(f"public binding codegen: wrote {len(outputs)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
