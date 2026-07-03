from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from collections.abc import Mapping, Sequence
from typing import Any

from .primitive_records import finalize_record, get_spec

SCHEMA_VERSION = "bb.effective_config_graph.v1"
_HASH_REPLACEMENT = None


class ConfigGraphCompileError(ValueError):
    """Raised when an effective config graph input cannot be represented."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical JSON encoding used for graph hashes."""

    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def graph_content_hash(record: Mapping[str, Any]) -> str:
    """Hash a graph record with graph_hash treated as unset using the legacy graph preimage."""

    preimage = copy.deepcopy(dict(record))
    preimage["graph_hash"] = _HASH_REPLACEMENT
    return sha256_json(preimage)


def _normalize_effective_config_graph(record: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    result["schema_version"] = SCHEMA_VERSION
    result["source_layers"] = sorted(
        [dict(layer) for layer in result.get("source_layers", [])],
        key=lambda item: (int(item.get("precedence", 0)), str(item.get("layer_id", ""))),
    )
    result["effective_values"] = sorted(
        [dict(value) for value in result.get("effective_values", [])],
        key=lambda item: (str(item.get("path", "")), str(item.get("source_layer_id", ""))),
    )
    visibility = result.get("visibility")
    if isinstance(visibility, Mapping):
        result["visibility"] = {
            "host_only_paths": sorted(str(path) for path in visibility.get("host_only_paths", [])),
            "model_visible_paths": sorted(str(path) for path in visibility.get("model_visible_paths", [])),
            "redacted_paths": sorted(str(path) for path in visibility.get("redacted_paths", [])),
        }
    result["env_gates"] = sorted(
        [dict(gate) for gate in result.get("env_gates", [])],
        key=lambda item: str(item.get("gate_id", "")),
    )
    result["migrations"] = [dict(migration) for migration in result.get("migrations", [])]
    return result


def finalize_effective_config_graph(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize, legacy-hash, and validate a bb.effective_config_graph.v1 through the primitive boundary."""

    normalized = _normalize_effective_config_graph(record)
    normalized["graph_hash"] = graph_content_hash(normalized)
    return finalize_record(replace(get_spec(SCHEMA_VERSION), hash_field=None), normalized)


def compile_effective_config_graph(
    *,
    graph_id: str,
    layers: Sequence[Mapping[str, Any]],
    merge_policy: Mapping[str, str] | None = None,
    migrations: Sequence[Mapping[str, Any]] | None = None,
    redacted_paths: Sequence[str] = (),
    host_only_paths: Sequence[str] = (),
    env_required: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Compile ordered config layers into a bb.effective_config_graph.v1 record.

    Layers are deep-merged by ascending ``precedence``; later/higher-precedence
    layers win for scalar and list leaves. Each layer must provide ``layer_id``,
    ``source_kind``, ``scope``, and ``precedence``. Layer ``values`` are flattened
    into dotted effective-value paths while retaining the winning source layer.
    Secret values are never copied into the graph: any path listed in
    ``redacted_paths`` or carrying ``env_name`` metadata becomes a ``secret-ref``.
    """

    if not graph_id:
        raise ConfigGraphCompileError("graph_id is required")
    env_required = dict(env_required or {})
    redacted = set(redacted_paths)
    host_only = set(host_only_paths)

    source_layers: list[dict[str, Any]] = []
    merged_values: Any = {}
    merged_sources: Any = {}
    merged_metadata: Any = {}
    env_gates_by_id: dict[str, dict[str, Any]] = {}

    ordered_layers = sorted(layers, key=lambda item: (int(item.get("precedence", 0)), str(item.get("layer_id", ""))))
    for layer in ordered_layers:
        layer_id = _require_str(layer, "layer_id")
        precedence = _require_int(layer, "precedence")
        values = layer.get("values", {})
        layer_hash = layer.get("layer_hash")
        if not isinstance(layer_hash, str) or not layer_hash.startswith("sha256:"):
            layer_hash = sha256_json(values)
        source_layers.append(
            {
                "host_visible": bool(layer.get("host_visible", True)),
                "layer_hash": layer_hash,
                "layer_id": layer_id,
                "model_visible": bool(layer.get("model_visible", True)),
                "precedence": precedence,
                "scope": _require_str(layer, "scope"),
                "source_kind": _require_str(layer, "source_kind"),
                "source_ref": layer.get("source_ref"),
            }
        )
        merged_values, merged_sources, merged_metadata = _merge_layer_values(
            merged_values,
            merged_sources,
            merged_metadata,
            values,
            layer_id,
        )

    effective_values: list[dict[str, Any]] = []
    for path, value, source_layer_id, metadata in _flatten_merged_values(merged_values, merged_sources, merged_metadata):
        env_name = metadata.get("env_name")
        is_redacted = path in redacted or bool(env_name) or _looks_secret_path(path)
        gate_ids: list[str] = []
        output_value = value
        value_kind = _value_kind(value)
        visibility = "model-visible"
        if path in host_only:
            visibility = "host-only"
        if is_redacted:
            visibility = "redacted"
            value_kind = "secret-ref"
            if env_name:
                gate_id = f"env.{env_name}"
                gate_ids.append(gate_id)
                env_gates_by_id[gate_id] = {
                    "env_name": str(env_name),
                    "gate_id": gate_id,
                    "required": bool(env_required.get(str(env_name), True)),
                    "satisfied": bool(metadata.get("env_satisfied", False)),
                }
                output_value = f"secret://env/{env_name}"
            else:
                output_value = "secret://redacted/" + path.replace(".", "/")
        effective_values.append(
            {
                "env_gate_ids": gate_ids,
                "path": path,
                "source_layer_id": source_layer_id,
                "value": output_value,
                "value_kind": value_kind,
                "visibility": visibility,
            }
        )
    visibility = {
        "host_only_paths": sorted(value["path"] for value in effective_values if value["visibility"] == "host-only"),
        "model_visible_paths": sorted(value["path"] for value in effective_values if value["visibility"] == "model-visible"),
        "redacted_paths": sorted(value["path"] for value in effective_values if value["visibility"] == "redacted"),
    }
    record = {
        "effective_values": effective_values,
        "env_gates": list(env_gates_by_id.values()),
        "graph_hash": None,
        "graph_id": graph_id,
        "merge_policy": dict(
            merge_policy
            or {
                "conflict_resolution": "highest-precedence",
                "policy_id": "precedence_order_deep_merge",
                "strategy": "deep-merge",
            }
        ),
        "migrations": [dict(item) for item in (migrations or _default_migrations())],
        "schema_version": SCHEMA_VERSION,
        "source_layers": source_layers,
        "visibility": visibility,
    }
    return finalize_effective_config_graph(record)


def _default_migrations() -> list[dict[str, Any]]:
    return [
        {
            "applied": True,
            "from_version": None,
            "migration_id": "initial-bb-effective-config-graph-v1",
            "to_version": SCHEMA_VERSION,
        }
    ]


def _merge_layer_values(
    current_values: Any,
    current_sources: Any,
    current_metadata: Any,
    layer_values: Any,
    layer_id: str,
) -> tuple[Any, Any, Any]:
    if isinstance(layer_values, Mapping) and _is_leaf_metadata(layer_values):
        return (
            copy.deepcopy(layer_values.get("value")),
            layer_id,
            {key: copy.deepcopy(item) for key, item in layer_values.items() if key != "value"},
        )
    if isinstance(layer_values, Mapping):
        if not isinstance(current_values, Mapping):
            next_values: dict[str, Any] = {}
            next_sources: dict[str, Any] = {}
            next_metadata: dict[str, Any] = {}
        else:
            next_values = copy.deepcopy(dict(current_values))
            next_sources = copy.deepcopy(dict(current_sources)) if isinstance(current_sources, Mapping) else {}
            next_metadata = copy.deepcopy(dict(current_metadata)) if isinstance(current_metadata, Mapping) else {}
        for key in sorted(layer_values):
            child_value, child_source, child_metadata = _merge_layer_values(
                next_values.get(key),
                next_sources.get(key),
                next_metadata.get(key),
                layer_values[key],
                layer_id,
            )
            next_values[key] = child_value
            next_sources[key] = child_source
            next_metadata[key] = child_metadata
        return next_values, next_sources, next_metadata
    return copy.deepcopy(layer_values), layer_id, {}


def _flatten_merged_values(
    values: Any,
    sources: Any,
    metadata: Any,
    prefix: str = "",
) -> list[tuple[str, Any, str, dict[str, Any]]]:
    if isinstance(sources, str):
        return [(prefix, values, sources, copy.deepcopy(dict(metadata)) if isinstance(metadata, Mapping) else {})]
    if isinstance(values, Mapping):
        flattened: list[tuple[str, Any, str, dict[str, Any]]] = []
        for key in sorted(values):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            child_sources = sources.get(key) if isinstance(sources, Mapping) else None
            child_metadata = metadata.get(key) if isinstance(metadata, Mapping) else None
            flattened.extend(_flatten_merged_values(values[key], child_sources, child_metadata, child_prefix))
        return flattened
    if not prefix:
        return []
    return [(prefix, values, str(sources or "unknown"), copy.deepcopy(dict(metadata)) if isinstance(metadata, Mapping) else {})]



def _is_leaf_metadata(value: Mapping[str, Any]) -> bool:
    return "value" in value and ("env_name" in value or "env_satisfied" in value)


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _looks_secret_path(path: str) -> bool:
    segments = [segment.replace("-", "_") for segment in path.lower().split(".")]
    for segment in segments:
        if segment in {"api_key", "apikey", "password", "secret", "token"}:
            return True
        if "api_key" in segment or "apikey" in segment:
            return True
        if segment.endswith(("_password", "_secret", "_token")):
            return True
    return False


def _require_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigGraphCompileError(f"{key} must be a non-empty string")
    return value


def _require_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool):
        raise ConfigGraphCompileError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except Exception as exc:  # noqa: BLE001 - normalized into domain error
        raise ConfigGraphCompileError(f"{key} must be an integer") from exc
    if parsed < 0:
        raise ConfigGraphCompileError(f"{key} must be non-negative")
    return parsed
