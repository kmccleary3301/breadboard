"""Deterministic product-owned Harness Definition compilation."""
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any
from .explain import HarnessExplanation
from .lock import EffectiveHarnessLock, _copy, graph_content_hash, sha256_json
from .model import HarnessDefinition
from .validate import HarnessDefinitionValidationError, parse_harness_definition
LoadReference = Callable[[str, str], tuple[str, Mapping[str, Any]]]
class HarnessCompileError(ValueError):
    """Raised before a compilation exists when an input cannot be resolved."""
@dataclass(frozen=True, slots=True, init=False)
class HarnessCompilation:
    """One immutable authority projected as values, lock, and explanation."""
    lock: EffectiveHarnessLock
    explanation: HarnessExplanation
    _effective: Mapping[str, Any] = field(repr=False)
    _resolved_author: Mapping[str, Any] = field(repr=False)
    @classmethod
    def _create(cls, effective: Mapping[str, Any], author: Mapping[str, Any],
                lock: EffectiveHarnessLock, explanation: HarnessExplanation):
        instance = object.__new__(cls)
        object.__setattr__(instance, "lock", lock)
        object.__setattr__(instance, "explanation", explanation)
        object.__setattr__(instance, "_effective", _copy(effective, freeze=True))
        object.__setattr__(instance, "_resolved_author", _copy(author, freeze=True))
        return instance
    def as_dict(self) -> dict[str, Any]:
        return _copy(self._effective, freeze=False)
    def resolved_author_dict(self) -> dict[str, Any]:
        return _copy(self._resolved_author, freeze=False)
def _mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessCompileError(f"{label} must be a mapping")
    try:
        return _copy(value, freeze=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise HarnessCompileError(f"{label} is not canonical JSON: {error}") from None
def _refs(document: Mapping[str, Any], source_ref: str) -> tuple[str, ...]:
    if "extends" not in document:
        return ()
    declared = document["extends"]
    values = declared if isinstance(declared, (list, tuple)) else (declared,)
    refs: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise HarnessCompileError(f"invalid reference declared by {source_ref}")
        refs.append(value)
    return tuple(refs)
def _runtime_values(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items()
            if key not in {"extends", "dossier"}}
def _metadata_leaf(value: Any) -> bool:
    return isinstance(value, Mapping) and "value" in value and (
        "env_name" in value or "env_satisfied" in value)
def _merge(current: Any, sources: Any, incoming: Any, layer_id: str,
           *, metadata: bool = True) -> tuple[Any, Any]:
    if metadata and _metadata_leaf(incoming):
        meta = {key: _copy(value, freeze=False) for key, value in incoming.items() if key != "value"}
        if "env_name" in meta and (not isinstance(meta["env_name"], str) or not meta["env_name"]):
            raise HarnessCompileError("env_name metadata must be a non-empty string")
        if "env_satisfied" in meta and type(meta["env_satisfied"]) is not bool:
            raise HarnessCompileError("env_satisfied metadata must be a boolean")
        return _copy(incoming["value"], freeze=False), (layer_id, meta)
    if isinstance(incoming, Mapping):
        if not incoming and (not isinstance(current, Mapping) or not current):
            return {}, (layer_id, {})
        values = dict(current) if isinstance(current, Mapping) else {}
        provenance = dict(sources) if isinstance(sources, Mapping) else {}
        for key in sorted(incoming):
            values[key], provenance[key] = _merge(
                values.get(key), provenance.get(key), incoming[key], layer_id,
                metadata=metadata)
        return values, provenance
    return _copy(incoming, freeze=False), (layer_id, {})
def _flatten(values: Any, sources: Any, prefix: str = "") -> list[tuple[str, Any, str, dict[str, Any]]]:
    if isinstance(sources, tuple):
        return [(prefix, _copy(values, freeze=False), sources[0], sources[1])]
    rows: list[tuple[str, Any, str, dict[str, Any]]] = []
    if isinstance(values, Mapping):
        for key in sorted(values):
            path = f"{prefix}.{key}" if prefix else key
            rows.extend(_flatten(values[key],
                                 sources.get(key) if isinstance(sources, Mapping) else None,
                                 path))
    return rows
def _source_rows(sources: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(sources, tuple):
        return [(prefix, sources[0])]
    return [(item) for key in sorted(sources) for item in _source_rows(
        sources[key], f"{prefix}.{key}" if prefix else key)] if isinstance(sources, Mapping) else []
def _kind(value: Any) -> str:
    kind = type(value)
    return ("null" if value is None else "boolean" if kind is bool
            else "number" if kind in (int, float) else "string"
            if isinstance(value, str) else "object" if isinstance(value, Mapping) else "array")
def _summary(values: Mapping[str, Any], extends_chain: Sequence[str]) -> dict[str, Any]:
    providers, modes, prompts = (values.get(key) for key in ("providers", "modes", "prompts"))
    mode_rows = modes if isinstance(modes, list) else []
    tools = {tool for mode in mode_rows if isinstance(mode, Mapping)
             for tool in mode.get("tools_enabled", ()) if isinstance(tool, str)}
    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, Mapping):
            return [item for key in sorted(value) for item in strings(value[key])]
        if isinstance(value, list):
            return [item for child in value for item in strings(child)]
        return []
    packs = prompts.get("packs") if isinstance(prompts, Mapping) else None
    return {
        "provider_default_model": str(providers.get("default_model", ""))
        if isinstance(providers, Mapping) else "",
        "mode_ids": sorted(str(mode["name"]) for mode in mode_rows
                           if isinstance(mode, Mapping) and "name" in mode),
        "tool_count": len(tools), "prompt_files": sorted(set(strings(packs))),
        "extends_chain": list(extends_chain),
    }
def compile_harness_definition(
    definition: Mapping[str, Any] | HarnessDefinition, *, source_ref: str,
    load_ref: LoadReference | None = None, defaults: Mapping[str, Any] | None = None,
    overlays: Sequence[Mapping[str, Any]] = (),
) -> HarnessCompilation:
    """Resolve, merge, hash, and freeze one Harness Definition."""
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise HarnessCompileError("source_ref must be a non-empty string")
    root = _mapping(definition.as_dict() if isinstance(definition, HarnessDefinition)
                    else definition, "definition")
    layers: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    extends_chain: list[str] = []
    source_hashes = {source_ref: sha256_json(root)}
    source_number = 0
    def add_source(document: dict[str, Any], ref: str) -> None:
        nonlocal source_number
        name = PurePath(ref).name or "source"
        values = _runtime_values(document)
        record = {
            "host_visible": True, "layer_hash": sha256_json(values),
            "layer_id": f"agent-config:{source_number:04d}:{name}",
            "model_visible": True, "scope": "agent", "source_kind": "project",
            "source_ref": ref,
        }
        source_number += 1
        layers.append((record, values,
                       {key: value for key, value in document.items() if key != "extends"}))
    def visit(document: dict[str, Any], ref: str, stack: tuple[str, ...]) -> None:
        for declared in _refs(document, ref):
            if load_ref is None:
                raise HarnessCompileError(f"no loader for reference {declared!r} from {ref!r}")
            try:
                resolved, loaded = load_ref(ref, declared)
            except (FileNotFoundError, KeyError):
                raise HarnessCompileError(
                    f"missing reference {declared!r} from {ref!r}") from None
            except Exception:
                raise HarnessCompileError(
                    f"failed to load reference {declared!r} from {ref!r}") from None
            if not isinstance(resolved, str) or not resolved.strip():
                raise HarnessCompileError("loader returned an invalid resolved reference")
            child = _mapping(loaded, f"reference {resolved!r}")
            digest = sha256_json(child)
            prior = source_hashes.setdefault(resolved, digest)
            if prior != digest:
                raise HarnessCompileError(f"inconsistent content for resolved reference {resolved!r}")
            if resolved in stack:
                raise HarnessCompileError(
                    "cyclic reference: " + " -> ".join((*stack, resolved)))
            extends_chain.append(resolved)
            visit(child, resolved, (*stack, resolved))
        add_source(document, ref)

    if defaults is not None:
        default = _runtime_values(_mapping(defaults, "defaults"))
        layers.append(({
            "host_visible": True, "layer_hash": sha256_json(default),
            "layer_id": "harness-default:0000", "model_visible": True,
            "scope": "agent", "source_kind": "default", "source_ref": None,
        }, default, None))
    visit(root, source_ref, (source_ref,))
    for index, overlay in enumerate(overlays):
        value = _runtime_values(_mapping(overlay, f"overlay {index}"))
        layers.append(({
            "host_visible": True, "layer_hash": sha256_json(value),
            "layer_id": f"harness-overlay:{index:04d}", "model_visible": True,
            "scope": "agent", "source_kind": "runtime", "source_ref": f"overlay:{index}",
        }, value, None))

    effective: Any = {}
    provenance: Any = {}
    author: Any = {}
    author_sources: Any = {}
    diagnostics: list[dict[str, Any]] = []
    source_layers: list[dict[str, Any]] = []
    for precedence, (record, values, authored) in enumerate(layers):
        record["precedence"] = precedence * 10
        source_layers.append(record)
        if authored is not None:
            author, author_sources = _merge(
                author, author_sources, authored, record["layer_id"], metadata=False)
        before = dict(_source_rows(provenance))
        effective, provenance = _merge(effective, provenance, values, record["layer_id"])
        after = dict(_source_rows(provenance))
        if record["source_kind"] == "default":
            diagnostics.extend(_effect(path, "info", "defaulted", record["layer_id"])
                               for path, source in after.items()
                               if source == record["layer_id"])
        diagnostics.extend(_override(path, source, record["layer_id"])
                           for path, source in before.items()
                           if after.get(path) != source)

    if author.get("schema_version") == "bb.harness_definition.v1":
        try:
            parse_harness_definition(author)
        except HarnessDefinitionValidationError as error:
            raise HarnessCompileError(f"invalid Harness Definition: {error}") from None
    rows = _flatten(effective, provenance)
    if not rows or any(not path for path, *_ in rows):
        raise HarnessCompileError("effective configuration must contain at least one value")
    env_gates: dict[str, dict[str, Any]] = {}
    effective_values: list[dict[str, Any]] = []
    for path, value, source, metadata in rows:
        env_name = metadata.get("env_name")
        redacted = bool(env_name) or _looks_secret(path)
        gate_ids = [f"env.{env_name}"] if env_name else []
        if env_name:
            env_gates[gate_ids[0]] = {"env_name": str(env_name), "gate_id": gate_ids[0],
                                     "required": True,
                                     "satisfied": bool(metadata.get("env_satisfied", False))}
        effective_values.append({
            "env_gate_ids": gate_ids, "path": path, "source_layer_id": source,
            "value": (f"secret://env/{env_name}" if env_name else
                      "secret://redacted/" + path.replace(".", "/")) if redacted else value,
            "value_kind": "secret-ref" if redacted else _kind(value),
            "visibility": "redacted" if redacted else "model-visible",
        })
    for path, value, source, _ in rows:
        diagnostics.append(_effect(path, "info", "selected", source))
        if (path == "capabilities" or path.startswith("capabilities.")
                or ".capabilities." in path or path.endswith(".capabilities")):
            effect = "capability_enabled" if value is True else "capability_configured"
            diagnostics.append(_effect(path, "info", effect, source))
            if value is False:
                diagnostics.append(_effect(
                    path, "warning", "capability_disabled", source, blocker=True))

    redacted_paths = [row["path"] for row in effective_values
                      if row["visibility"] == "redacted"]
    graph = {
        "effective_values": effective_values,
        "env_gates": [env_gates[key] for key in sorted(env_gates)], "graph_hash": None,
        "graph_id": f"agent_config:{PurePath(source_ref).stem or 'harness'}",
        "merge_policy": {"conflict_resolution": "highest-precedence",
                         "policy_id": "precedence_order_deep_merge",
                         "strategy": "deep-merge"},
        "migrations": [{"applied": True, "from_version": "agent-config-yaml",
                        "migration_id": "agent-config-yaml-to-effective-config-graph-v1",
                        "to_version": "bb.effective_config_graph.v1"}],
        "schema_version": "bb.effective_config_graph.v1", "source_layers": source_layers,
        "visibility": {"host_only_paths": [],
                       "model_visible_paths": [row["path"] for row in effective_values
                                               if row["visibility"] == "model-visible"],
                       "redacted_paths": redacted_paths},
    }
    graph["graph_hash"] = graph_content_hash(graph)
    surface = effective.get("schema_version")
    if surface not in {"bb.agent_config_surface.v1", "bb.agent_config_surface.v2"}:
        surface = "bb.agent_config_surface.v1"
    explanation_record = {
        "schema_version": "bb.config_explanation.v1",
        "explanation_id": "harness_explanation:" + sha256_json(source_ref)[7:23],
        "config_path": source_ref, "config_sha256": sha256_json(author),
        "generated_at_utc": "1970-01-01T00:00:00Z",
        "surface_schema_version": surface, "resolved_summary": _summary(effective, extends_chain),
        "fields": [{"classification": "operational", "consumer_ref": None,
                    "path": path, "source_layer": source}
                   for path, _, source, _ in rows],
        "diagnostics": sorted(diagnostics, key=lambda item: (item["path"], item["message"])),
        "ok": True,
    }
    return HarnessCompilation._create(
        effective, author, EffectiveHarnessLock._from_record(graph),
        HarnessExplanation._from_record(explanation_record))
def _looks_secret(path: str) -> bool:
    segments = (part.replace("-", "_") for part in path.lower().split("."))
    return any(part in {"api_key", "apikey", "password", "secret", "token"}
               or "api_key" in part or "apikey" in part
               or part.endswith(("_password", "_secret", "_token")) for part in segments)
def _effect(path: str, severity: str, effect: str, source: str,
            *, blocker: bool = False) -> dict[str, Any]:
    label = "blocker" if blocker else "effect"
    return {"severity": severity, "class": "other", "path": path,
            "message": f"{label}={effect}; source={source}"}


def _override(path: str, source: str, winner: str) -> dict[str, Any]:
    return {"severity": "info", "class": "other", "path": path,
            "message": f"effect=overridden; source={source}; winner={winner}"}
