#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

try:
    from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
    from agentic_coder_prototype.compilation.tool_registry import load_tool_registry
    from agentic_coder_prototype.compilation.v2_loader import load_agent_config_view
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
    from agentic_coder_prototype.compilation.tool_registry import load_tool_registry
    from agentic_coder_prototype.compilation.v2_loader import load_agent_config_view

SCHEMA_VERSION = "bb.config_explanation.v1"
GENERATED_AT_UTC = "2026-07-09T00:00:00Z"
REGISTRY_PATH = Path("contracts/kernel/registries/config_surface_fields.v1.json")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _repo_rel(path: Path) -> str:
    resolved = path.resolve()
    root = _repo_root().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("agent config must be a mapping")
    return loaded


def _load_field_registry() -> dict[str, dict[str, Any]]:
    payload = json.loads((_repo_root() / REGISTRY_PATH).read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return {}
    return {str(entry.get("id")): dict(entry) for entry in entries if isinstance(entry, dict) and entry.get("id")}


def _resolve_from_config(config_path: Path, raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    config_relative = (config_path.parent / candidate).resolve()
    if config_relative.exists():
        return config_relative
    repo_relative = (_repo_root() / candidate).resolve()
    if repo_relative.exists():
        return repo_relative
    return config_relative


def _extends_chain(config_path: Path, seen: set[Path] | None = None) -> list[str]:
    seen = set() if seen is None else seen
    resolved = config_path.resolve()
    if resolved in seen:
        return []
    seen.add(resolved)
    try:
        doc = _load_yaml(resolved)
    except Exception:
        return []
    raw_extends = doc.get("extends")
    values: list[str]
    if isinstance(raw_extends, str):
        values = [raw_extends]
    elif isinstance(raw_extends, list):
        values = [str(item) for item in raw_extends if item]
    else:
        values = []
    out: list[str] = []
    for value in values:
        parent = _resolve_from_config(resolved, value)
        out.append(_repo_rel(parent))
        out.extend(_extends_chain(parent, seen))
    return out


def _dotted_items(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(child_path)
            paths.extend(_dotted_items(child, child_path))
        return paths
    if isinstance(value, list):
        paths = []
        for index, child in enumerate(value):
            child_path = f"{prefix}.{index}" if prefix else str(index)
            paths.append(child_path)
            paths.extend(_dotted_items(child, child_path))
        return paths
    return []


def _mode_ids(config: Mapping[str, Any]) -> list[str]:
    modes = config.get("modes")
    if isinstance(modes, Mapping):
        ids: list[str] = []
        for key, value in modes.items():
            if isinstance(value, Mapping):
                ids.append(str(value.get("id") or value.get("name") or key))
            else:
                ids.append(str(key))
        return sorted(dict.fromkeys(ids))
    if isinstance(modes, list):
        ids = []
        for index, value in enumerate(modes):
            if isinstance(value, Mapping):
                ids.append(str(value.get("id") or value.get("name") or value.get("mode") or index))
            else:
                ids.append(str(value))
        return sorted(dict.fromkeys(ids))
    return []


def _collect_prompt_refs(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}.{index}" if path else str(index))
        elif isinstance(value, str):
            lowered = value.lower()
            if value.startswith("@pack("):
                refs.append((path, value))
            elif lowered.endswith((".md", ".txt", ".prompt", ".prompt.md")) or "/prompts/" in lowered or "prompts/" in lowered:
                refs.append((path, value))

    visit(config.get("prompts") or {}, "prompts")
    visit(config.get("modes") or {}, "modes")
    return refs


def _resolve_prompt_ref(config: Mapping[str, Any], ref: str) -> tuple[str | None, str | None]:
    if not ref.startswith("@pack("):
        return ref, None
    close = ref.find(")")
    if close <= len("@pack(") or close + 1 >= len(ref) or ref[close + 1] != ".":
        return None, f"malformed prompt pack reference: {ref}"
    pack_name = ref[len("@pack("):close]
    slot_name = ref[close + 2:]
    prompts = config.get("prompts") if isinstance(config.get("prompts"), Mapping) else {}
    packs = prompts.get("packs") if isinstance(prompts.get("packs"), Mapping) else {}
    pack = packs.get(pack_name) if isinstance(packs, Mapping) else None
    if not isinstance(pack, Mapping):
        return None, f"prompt pack does not exist: {pack_name}"
    slot = pack.get(slot_name)
    if not isinstance(slot, str) or not slot:
        return None, f"prompt pack slot does not exist: {pack_name}.{slot_name}"
    return slot, None


def _tool_registry(config_path: Path, config: Mapping[str, Any], diagnostics: list[dict[str, str]]) -> tuple[set[str], int]:
    tools_cfg = config.get("tools") if isinstance(config.get("tools"), Mapping) else {}
    registry_cfg = tools_cfg.get("registry") if isinstance(tools_cfg.get("registry"), Mapping) else {}
    paths = registry_cfg.get("paths") if isinstance(registry_cfg, Mapping) else None
    defs_dir: Path | None = None
    if isinstance(paths, list) and paths:
        defs_dir = _resolve_from_config(config_path, str(paths[0]))
    elif tools_cfg.get("defs_dir"):
        defs_dir = _resolve_from_config(config_path, str(tools_cfg.get("defs_dir")))
    aliases = {str(key): str(value) for key, value in (tools_cfg.get("aliases") or {}).items()}
    try:
        registry = load_tool_registry(defs_dir, aliases=aliases) if defs_dir is not None else load_tool_registry(aliases=aliases)
    except Exception as exc:
        diagnostics.append({
            "severity": "error",
            "class": "dead_ref",
            "path": "tools.registry.paths",
            "message": str(exc),
        })
        return set(), 0
    names = set(registry.tools_by_name) | set(registry.aliases)
    return names, len(registry.tools_by_name)


def _configured_tool_names(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    tools = config.get("tools") if isinstance(config.get("tools"), Mapping) else {}
    registry = tools.get("registry") if isinstance(tools.get("registry"), Mapping) else {}
    include = registry.get("include") if isinstance(registry, Mapping) else None
    if isinstance(include, list):
        refs.extend((f"tools.registry.include.{idx}", str(item)) for idx, item in enumerate(include) if str(item) != "*")
    modes = config.get("modes")
    mode_rows = modes.values() if isinstance(modes, Mapping) else modes if isinstance(modes, list) else []
    for mode_index, mode in enumerate(mode_rows):
        if not isinstance(mode, Mapping):
            continue
        enabled = mode.get("tools_enabled")
        if isinstance(enabled, list):
            mode_name = str(mode.get("name") or mode.get("id") or mode_index)
            refs.extend((f"modes.{mode_name}.tools_enabled.{idx}", str(item)) for idx, item in enumerate(enabled) if str(item) != "*")
    return refs


def _path_refs(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    scalar_path_keys = {
        "config_ref",
        "file",
        "path",
        "root",
        "session_path",
        "template_ref",
    }
    collection_path_keys = {"files", "paths"}

    def is_scalar_path_key(key: str) -> bool:
        return key in scalar_path_keys or key.endswith(("_file", "_path"))

    def is_collection_path_key(key: str) -> bool:
        return key in collection_path_keys or key.endswith(("_files", "_paths"))

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                if not path and key_text == "dossier":
                    continue
                child_path = f"{path}.{key_text}" if path else key_text
                if isinstance(child, str) and is_scalar_path_key(key_text):
                    refs.append((child_path, child))
                elif isinstance(child, list) and is_collection_path_key(key_text):
                    refs.extend(
                        (f"{child_path}.{index}", item)
                        for index, item in enumerate(child)
                        if isinstance(item, str)
                    )
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}.{index}" if path else str(index))

    visit(config, "")
    return refs


def build_explanation(config_arg: str) -> dict[str, Any]:
    root = _repo_root()
    config_path = Path(config_arg)
    if not config_path.is_absolute():
        config_path = (root / config_path).resolve()
    diagnostics: list[dict[str, str]] = []
    field_registry = _load_field_registry()
    try:
        raw = _load_yaml(config_path)
        view = load_agent_config_view(str(config_path))
        config = view.as_dict()
    except Exception as exc:
        raw = {}
        config = {}
        view = None
        diagnostics.append({
            "severity": "error",
            "class": "schema_violation",
            "path": "<root>",
            "message": str(exc),
        })

    if view is None:
        graph_paths = []
    else:
        graph_paths = [str(item.get("path")) for item in view.graph.get("effective_values", []) if item.get("path")]
    field_paths = sorted(set(graph_paths or _dotted_items(config)))
    fields: list[dict[str, Any]] = []
    seen_top: set[str] = set()
    for path in field_paths:
        top = path.split(".", 1)[0]
        seen_top.add(top)
        row = field_registry.get(top)
        if row is None:
            classification = "unknown"
            consumer = None
        else:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            classification = str(metadata.get("classification") or "unknown")
            consumer = metadata.get("consumer")
            if not isinstance(consumer, str):
                consumer = None
        fields.append({
            "path": path,
            "source_layer": (view.source_for(path) if view is not None else None) or "inline",
            "classification": classification if classification in {"operational", "dossier_only"} else "unknown",
            "consumer_ref": consumer,
        })
    for top in sorted(set(config) - set(field_registry)):
        diagnostics.append({
            "severity": "warning",
            "class": "unconsumed_field",
            "path": top,
            "message": f"top-level config key {top!r} is absent from config_surface_fields registry",
        })
    for top in sorted(seen_top):
        if top not in field_registry:
            diagnostics.append({
                "severity": "warning",
                "class": "registry_miss",
                "path": top,
                "message": f"field path root {top!r} has no config_surface_fields registry entry",
            })

    prompt_refs = _collect_prompt_refs(config)
    if isinstance(raw, Mapping):
        prompt_refs.extend(_collect_prompt_refs(raw))
    prompt_refs = sorted(set(prompt_refs))
    prompt_files: list[str] = []
    for path, ref in prompt_refs:
        concrete_ref, prompt_error = _resolve_prompt_ref(config, ref)
        if (prompt_error is not None or concrete_ref is None) and isinstance(raw, Mapping):
            concrete_ref, prompt_error = _resolve_prompt_ref(raw, ref)
        if prompt_error is not None or concrete_ref is None:
            diagnostics.append({
                "severity": "error",
                "class": "dead_ref",
                "path": path,
                "message": prompt_error or f"prompt reference does not resolve: {ref}",
            })
            continue
        resolved = _resolve_from_config(config_path, concrete_ref)
        prompt_files.append(_repo_rel(resolved))
        if not resolved.is_file():
            diagnostics.append({
                "severity": "error",
                "class": "missing_prompt",
                "path": path,
                "message": f"prompt file does not exist: {concrete_ref}",
            })

    tool_names, tool_count = _tool_registry(config_path, config, diagnostics)
    for path, name in _configured_tool_names(config):
        if name not in tool_names:
            diagnostics.append({
                "severity": "warning",
                "class": "unknown_tool",
                "path": path,
                "message": f"tool name {name!r} is absent from loaded registry",
            })

    raw_extends = raw.get("extends") if isinstance(raw, Mapping) else None
    if isinstance(raw_extends, str):
        raw_extends_values = [raw_extends]
    elif isinstance(raw_extends, list):
        raw_extends_values = [str(item) for item in raw_extends if item]
    else:
        raw_extends_values = []
    for index, ref in enumerate(raw_extends_values):
        if not _resolve_from_config(config_path, ref).is_file():
            diagnostics.append({
                "severity": "error",
                "class": "dead_ref",
                "path": f"extends.{index}",
                "message": f"extends reference does not exist: {ref}",
            })
    for path, ref in sorted(set(_path_refs(config))):
        if ref.startswith(("http://", "https://", "sha256:", "secret://")):
            continue
        if not _resolve_from_config(config_path, ref).exists():
            diagnostics.append({
                "severity": "error",
                "class": "dead_ref",
                "path": path,
                "message": f"path reference does not exist: {ref}",
            })

    surface_schema_version = str(config.get("schema_version") or "bb.agent_config_surface.v1")
    if surface_schema_version not in {"bb.agent_config_surface.v1", "bb.agent_config_surface.v2"}:
        surface_schema_version = "bb.agent_config_surface.v1"
    has_error = any(item["severity"] == "error" for item in diagnostics)
    record = {
        "schema_version": SCHEMA_VERSION,
        "explanation_id": "config-explanation-" + config_path.stem.lower().replace("_", "-").replace(".", "-"),
        "config_path": _repo_rel(config_path),
        "config_sha256": _sha256_file(config_path) if config_path.is_file() else "sha256:" + "0" * 64,
        "generated_at_utc": GENERATED_AT_UTC,
        "surface_schema_version": surface_schema_version,
        "resolved_summary": {
            "provider_default_model": str(((config.get("providers") or {}) if isinstance(config.get("providers"), Mapping) else {}).get("default_model") or ""),
            "mode_ids": _mode_ids(config),
            "tool_count": tool_count,
            "prompt_files": sorted(dict.fromkeys(prompt_files)),
            "extends_chain": _extends_chain(config_path),
        },
        "fields": fields,
        "diagnostics": diagnostics,
        "ok": not has_error,
    }
    return finalize_record(get_spec(SCHEMA_VERSION), record)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain a BreadBoard agent config surface.")
    parser.add_argument("--config", required=True, help="Agent config YAML path, repo-root-relative or absolute.")
    parser.add_argument("--json", dest="json_out", help="Optional output JSON path.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when the explanation has warnings but no errors.")
    args = parser.parse_args(argv)
    record = build_explanation(args.config)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    has_errors = any(item["severity"] == "error" for item in record["diagnostics"])
    has_warnings = any(item["severity"] == "warning" for item in record["diagnostics"])
    if has_errors:
        return 1
    if args.strict and has_warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
