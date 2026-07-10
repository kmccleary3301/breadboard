from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Union

from jsonschema import Draft202012Validator

from .effective_config_graph import compile_effective_config_graph, sha256_json

_VALID_CONFIG_AUTHORITIES = {"config", "parity", "effective"}

def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _default_config_authority() -> str:
    """Keep production on config while letting dev/CI exercise effective authority."""
    explicit = os.environ.get("BREADBOARD_CONFIG_AUTHORITY")
    if explicit is not None:
        return explicit.strip().lower() or "config"
    if _env_truthy("CI") or _env_truthy("BREADBOARD_CONFIG_EFFECTIVE_DEFAULT"):
        return "effective"
    return "config"


class ConfigView(Mapping[str, Any]):
    """Read-only view over a compiled agent config and its effective graph."""

    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        graph: Mapping[str, Any],
        config_path: Path,
    ) -> None:
        self._values = copy.deepcopy(dict(values))
        self.graph = copy.deepcopy(dict(graph))
        self.config_path = config_path
        self._sources_by_path = {
            str(item.get("path")): str(item.get("source_layer_id"))
            for item in self.graph.get("effective_values", [])
            if item.get("path")
        }

    def __getitem__(self, key: str) -> Any:
        return copy.deepcopy(self._values[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get_path(self, dotted_path: str, default: Any = None) -> Any:
        current: Any = self._values
        for part in dotted_path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return copy.deepcopy(current)

    def source_for(self, dotted_path: str) -> str | None:
        return self._sources_by_path.get(dotted_path)

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._values)


def _resolve_config_path(config_path_str: str) -> Path:
    """Resolve config paths across pre-merge and integrated repo layouts."""
    candidate = Path(config_path_str)
    if candidate.is_absolute():
        return candidate

    resolved = candidate.resolve()
    if resolved.exists():
        return resolved

    parts = candidate.parts
    if len(parts) > 1:
        stripped = Path(*parts[1:]).resolve()
        if stripped.exists():
            return stripped

    return resolved


def _load_yaml(path: Union[str, Path]) -> Dict[str, Any]:
    import yaml  # lazy import
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def _agent_config_surface_validator(schema_version: str = "bb.agent_config_surface.v1") -> Draft202012Validator:
    schema_name = (
        "bb.agent_config_surface.v2.schema.json"
        if schema_version == "bb.agent_config_surface.v2"
        else "bb.agent_config_surface.v1.schema.json"
    )
    schema_path = _repo_root() / "contracts" / "kernel" / "schemas" / schema_name
    with open(schema_path, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _surface_schema_version(doc: Mapping[str, Any]) -> str:
    schema_version = doc.get("schema_version")
    if schema_version is None:
        return "bb.agent_config_surface.v1"
    if schema_version == "bb.agent_config_surface.v2":
        return "bb.agent_config_surface.v2"
    raise ValueError(f"unsupported agent config surface schema_version: {schema_version}")

def _has_agent_config_version_2(doc: Mapping[str, Any]) -> bool:
    try:
        return int(doc.get("version", 0)) == 2
    except Exception:
        return False


def _json_pointer(parts: Any) -> str:
    tokens = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(tokens) if tokens else "/"


def _format_config_schema_error(error: Any) -> str:
    path = list(error.absolute_path)
    if error.validator == "required":
        missing = str(error.message).split("'")[1] if "'" in error.message else ""
        if missing:
            path.append(missing)
    return f"agent config schema error at {_json_pointer(path)}: {error.message}"


def _validate_agent_config_surface(doc: Dict[str, Any], schema_version: str | None = None) -> None:
    selected_schema_version = schema_version or _surface_schema_version(doc)
    errors = sorted(
        _agent_config_surface_validator(selected_schema_version).iter_errors(doc),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    if errors:
        raise ValueError(_format_config_schema_error(errors[0]))

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


def _extends_paths(doc: Mapping[str, Any]) -> list[str]:
    extends_val = doc.get("extends")
    if not extends_val:
        return []
    if isinstance(extends_val, (list, tuple)):
        return [str(item) for item in extends_val]
    return [str(extends_val)]


def _config_graph_layers(doc: Dict[str, Any], config_path: Path, *, _seen: tuple[Path, ...] = ()) -> list[Dict[str, Any]]:
    layers: list[Dict[str, Any]] = []
    _append_config_graph_layers(doc, config_path, layers, _seen=_seen)
    return layers


def _append_config_graph_layers(
    doc: Dict[str, Any],
    config_path: Path,
    layers: list[Dict[str, Any]],
    *,
    _seen: tuple[Path, ...] = (),
) -> None:
    resolved_path = config_path.resolve()
    if resolved_path in _seen:
        chain = " -> ".join(str(path) for path in (*_seen, resolved_path))
        raise ValueError(f"cyclic config extends chain: {chain}")

    for rel in _extends_paths(doc):
        base_path = (config_path.parent / rel).resolve()
        base_raw = _load_yaml(base_path)
        _append_config_graph_layers(base_raw, base_path, layers, _seen=(*_seen, resolved_path))

    layer_values = {key: value for key, value in doc.items() if key not in {"extends", "dossier"}}
    order = len(layers)
    layers.append(
        {
            "layer_id": f"agent-config:{order:04d}:{config_path.name}",
            "source_kind": "project",
            "scope": "agent",
            "precedence": order * 10,
            "source_ref": str(config_path),
            "layer_hash": sha256_json(layer_values),
            "values": layer_values,
        }
    )


def _effective_values_to_dict(effective_values: list[Mapping[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in effective_values:
        dotted_path = str(item.get("path") or "")
        if not dotted_path:
            continue
        current: Dict[str, Any] = out
        parts = dotted_path.split(".")
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = copy.deepcopy(item.get("value"))
    return out


def build_config_view(config_path_str: str) -> ConfigView:
    """Build the v2 ConfigView foundation without changing legacy defaults."""

    config_path = _resolve_config_path(config_path_str)
    raw = _load_yaml(config_path)
    if not isinstance(raw, dict):
        raise ValueError("agent config must be a mapping")

    effective_doc = _resolve_extends(raw, config_path) if raw.get("extends") else copy.deepcopy(raw)
    surface_schema_version = _surface_schema_version(effective_doc)
    if surface_schema_version == "bb.agent_config_surface.v2":
        _validate_v2(effective_doc)
        runtime_doc = _normalize_for_runtime(effective_doc)
    else:
        if _has_agent_config_version_2(effective_doc):
            _validate_agent_config_surface(effective_doc, "bb.agent_config_surface.v1")
        runtime_doc = effective_doc

    layers = _config_graph_layers(raw, config_path)
    graph = compile_effective_config_graph(
        graph_id=f"agent_config:{config_path.stem}",
        layers=layers,
        migrations=[
            {
                "migration_id": "agent-config-yaml-to-effective-config-graph-v1",
                "from_version": "agent-config-yaml",
                "to_version": "bb.effective_config_graph.v1",
                "applied": True,
            }
        ],
    )
    return ConfigView(runtime_doc, graph=graph, config_path=config_path)


def load_agent_config_view(config_path_str: str) -> ConfigView:
    return build_config_view(config_path_str)


def _log_config_divergence(
    config_path: Path,
    legacy_doc: Mapping[str, Any],
    view_doc: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
) -> None:
    active_logger = logger or logging.getLogger(__name__)
    legacy_hash = sha256_json(legacy_doc)
    effective_hash = sha256_json(view_doc)
    payload = {
        "config_path": str(config_path),
        "legacy_hash": legacy_hash,
        "effective_hash": effective_hash,
        "legacy": legacy_doc,
        "effective": view_doc,
    }
    message = (
        f"Effective config divergence for {config_path}: "
        f"legacy_hash={legacy_hash} effective_hash={effective_hash}"
    )
    active_logger.warning(message)
    log_path = os.environ.get("BREADBOARD_CONFIG_DIVERGENCE_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    default_dir = _repo_root() / "artifacts" / "config_parity"
    default_dir.mkdir(parents=True, exist_ok=True)
    safe_name = config_path.stem.replace("/", "_")
    with open(default_dir / f"{safe_name}.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _validate_v2(doc: Dict[str, Any]) -> None:
    _validate_agent_config_surface(doc, "bb.agent_config_surface.v2")

    # These mixed compatibility rules remain hand-validated: policy-profile
    # normalization and budget-alias precedence are not equivalent to the
    # explicit-v2 schema's per-field checks.
    long_running = doc.get("long_running")
    if long_running is not None:
        if not isinstance(long_running, dict):
            raise ValueError("long_running must be a mapping when provided")
        enabled = long_running.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("long_running.enabled must be a boolean when provided")
        policy_profile = long_running.get("policy_profile")
        if policy_profile is not None and str(policy_profile).strip().lower() not in {
            "conservative",
            "balanced",
            "aggressive",
        }:
            raise ValueError("long_running.policy_profile must be one of: conservative, balanced, aggressive")
        reset_policy = long_running.get("reset_policy")
        if reset_policy is not None and str(reset_policy) not in {"fresh", "compact", "continue"}:
            raise ValueError("long_running.reset_policy must be one of: fresh, compact, continue")
        budgets = long_running.get("budgets")
        if budgets is not None and not isinstance(budgets, dict):
            raise ValueError("long_running.budgets must be a mapping when provided")
        if isinstance(budgets, dict):
            total_tokens = budgets.get("total_tokens", budgets.get("max_total_tokens"))
            if total_tokens is not None:
                try:
                    parsed = int(total_tokens)
                except Exception as exc:
                    raise ValueError(
                        "long_running.budgets.total_tokens must be a non-negative integer when provided"
                    ) from exc
                if parsed < 0:
                    raise ValueError("long_running.budgets.total_tokens must be a non-negative integer when provided")
            total_cost_usd = budgets.get("total_cost_usd", budgets.get("max_total_cost_usd"))
            if total_cost_usd is not None:
                try:
                    parsed = float(total_cost_usd)
                except Exception as exc:
                    raise ValueError("long_running.budgets.total_cost_usd must be numeric >= 0 when provided") from exc
                if parsed < 0:
                    raise ValueError("long_running.budgets.total_cost_usd must be numeric >= 0 when provided")


def _normalize_for_runtime(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add compatibility fields expected by current runtime while keeping v2 structure.
    - tools.defs_dir: map from tools.registry.paths[0]
    """
    out = {key: value for key, value in doc.items() if key != "dossier"}
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
    out.setdefault("long_running", doc.get("long_running", {}))

    return out


def is_v2_config(doc: Dict[str, Any]) -> bool:
    return doc.get("schema_version") == "bb.agent_config_surface.v2"


def load_agent_config(config_path_str: str) -> Dict[str, Any]:
    """
    Load agent config with v1/v2 support (extends + validation + minimal normalization).
    V1 configs omit schema_version. V2 configs declare schema_version=bb.agent_config_surface.v2.
    BREADBOARD_CONFIG_AUTHORITY mirrors policy authority. Production default is config; CI or
    BREADBOARD_CONFIG_EFFECTIVE_DEFAULT=1 defaults to effective unless the authority is explicit.
    """
    config_path = _resolve_config_path(config_path_str)
    raw = _load_yaml(config_path)

    # Prefer resolving extends first (so child files inherit version/mode/loop)
    doc = _resolve_extends(raw, config_path) if (isinstance(raw, dict) and raw.get("extends")) else raw

    metadata = {
        "config_path": str(config_path),
        "config_dir": str(config_path.parent),
        "repo_root": str(config_path.parent.parent.parent),
    }

    def _with_metadata(doc_out):
        if isinstance(doc_out, dict):
            doc_out = dict(doc_out)
            doc_out["_config_metadata"] = metadata
        return doc_out

    surface_schema_version = _surface_schema_version(doc)
    if surface_schema_version == "bb.agent_config_surface.v2":
        _validate_v2(doc)
        legacy_doc = _normalize_for_runtime(doc)
    else:
        # Schema-less legacy YAML without version remains raw under config authority; version:2 agent configs resolve like ConfigView.
        legacy_doc = doc if _has_agent_config_version_2(doc) else raw

    authority = _default_config_authority()
    active_logger = logging.getLogger(__name__)
    if authority not in _VALID_CONFIG_AUTHORITIES:
        active_logger.warning("Unknown BREADBOARD_CONFIG_AUTHORITY=%r; using config", authority)
        authority = "config"
    if authority == "config":
        return _with_metadata(legacy_doc)

    view = build_config_view(str(config_path))
    effective_doc = view.as_dict()
    if legacy_doc != effective_doc:
        _log_config_divergence(config_path, legacy_doc, effective_doc, logger=active_logger)
    if authority == "parity":
        return _with_metadata(legacy_doc)
    return _with_metadata(effective_doc)
