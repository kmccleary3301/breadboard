from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Union


def _load_yaml(path: Union[str, Path]) -> Dict[str, Any]:
    import yaml  # lazy import
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two dicts recursively. Lists/tuples are replaced, not merged.
    Scalars replace.
    """
    out: Dict[str, Any] = dict(base)
    for k, v in (override or {}).items():
        if k not in out:
            out[k] = v
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_extends(doc: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    """Resolve one or more levels of `extends` declarations.

    Supports single string or list for `extends`, and resolves nested bases
    recursively so that the final document contains all inherited sections
    (e.g., `version`, `tools`, `modes`, `loop`).
    """
    extends_val = doc.get("extends")
    if not extends_val:
        return doc

    base_docs: List[Dict[str, Any]] = []
    if isinstance(extends_val, (list, tuple)):
        paths = list(extends_val)
    else:
        paths = [extends_val]

    for rel in paths:
        base_path = (config_path.parent / str(rel)).resolve()
        base_raw = _load_yaml(base_path)
        # Recursively resolve base's own extends if present
        base_resolved = _resolve_extends(base_raw, base_path) if isinstance(base_raw, dict) and base_raw.get("extends") else base_raw
        base_docs.append(base_resolved)

    merged: Dict[str, Any] = {}
    for b in base_docs:
        merged = _deep_merge(merged, b)
    merged = _deep_merge(merged, {k: v for k, v in doc.items() if k != "extends"})
    return merged


def _validate_v2(doc: Dict[str, Any]) -> None:
    required_top = ["version", "workspace", "providers", "modes", "loop"]
    for key in required_top:
        if key not in doc:
            raise ValueError(f"v2 config missing required section: {key}")

    if int(doc.get("version", 0)) != 2:
        raise ValueError("version must be 2 for v2 schema")

    providers = doc.get("providers") or {}
    if not providers.get("default_model"):
        raise ValueError("providers.default_model is required")
    models = providers.get("models") or []
    if not isinstance(models, list) or not models:
        raise ValueError("providers.models must be a non-empty list")
    for m in models:
        if not m.get("id") or not m.get("adapter"):
            raise ValueError("each providers.models[] needs id and adapter")

    loop = doc.get("loop") or {}
    if not loop.get("sequence"):
        raise ValueError("loop.sequence must be provided")


def _normalize_for_runtime(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add compatibility fields expected by current runtime while keeping v2 structure.
    - tools.defs_dir: map from tools.registry.paths[0]
    """
    out = dict(doc)
    tools = out.setdefault("tools", {}) or {}
    registry = tools.get("registry") or {}
    paths = registry.get("paths") or []
    if paths and not tools.get("defs_dir"):
        # pick the first registry path for current loader capabilities
        tools["defs_dir"] = str(paths[0])
    out["tools"] = tools

    # ensure subkeys exist
    out.setdefault("turn_strategy", doc.get("loop", {}).get("turn_strategy", {}))
    out.setdefault("concurrency", doc.get("concurrency", {}))
    out.setdefault("completion", doc.get("completion", {}))

    return out


def is_v2_config(doc: Dict[str, Any]) -> bool:
    try:
        return int(doc.get("version", 0)) == 2
    except Exception:
        return False


def load_agent_config(config_path_str: str) -> Dict[str, Any]:
    """
    Load agent config with v2 support (extends + validation + minimal normalization).
    Env override: if AGENT_SCHEMA_V2_ENABLED=1, treat as v2 when version==2 or 'modes'+'loop' present.
    """
    config_path = Path(config_path_str).resolve()
    raw = _load_yaml(config_path)

    # Prefer resolving extends first (so child files inherit version/mode/loop)
    doc = _resolve_extends(raw, config_path) if (isinstance(raw, dict) and raw.get("extends")) else raw

    # Gate on version and env
    env_enabled = os.environ.get("AGENT_SCHEMA_V2_ENABLED", "0") == "1"
    if is_v2_config(doc) or (env_enabled and ("modes" in doc or "loop" in doc)):
        _validate_v2(doc)
        return _normalize_for_runtime(doc)

    # Fallback: legacy load path, return as-is
    return raw

