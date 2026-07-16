from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Union

from jsonschema import Draft202012Validator
from breadboard.product.harness.compile import HarnessCompilation, compile_harness_definition
from breadboard.product.harness.validate import parse_harness_definition

from .effective_config_graph import sha256_json

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

def _load_config_ref(parent_source_ref: str, declared_ref: str) -> tuple[str, Dict[str, Any]]:
    ref_path = Path(declared_ref)
    resolved = ref_path.resolve() if ref_path.is_absolute() else (Path(parent_source_ref).parent / ref_path).resolve()
    document = _load_yaml(resolved)
    if not isinstance(document, dict):
        raise ValueError(f"extended agent config must be a mapping: {resolved}")
    return str(resolved), document

def _compile_config(raw: object, config_path: Path) -> HarnessCompilation:
    if not isinstance(raw, dict):
        raise ValueError("agent config must be a mapping")
    return compile_harness_definition(raw, source_ref=str(config_path.resolve()), load_ref=_load_config_ref)



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
    if schema_version == "bb.harness_definition.v1":
        return "bb.harness_definition.v1"
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


def _dotted_config_path(parts: Any) -> str:
    dotted = ""
    for part in parts:
        if isinstance(part, int):
            dotted += f"[{part}]"
        else:
            dotted += ("." if dotted else "") + str(part)
    return dotted


def _validate_agent_config_surface(doc: Dict[str, Any], schema_version: str | None = None) -> None:
    selected_schema_version = schema_version or _surface_schema_version(doc)
    errors = sorted(
        _agent_config_surface_validator(selected_schema_version).iter_errors(doc),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    if errors:
        error = errors[0]
        rendered = _format_config_schema_error(error)
        dotted_path = _dotted_config_path(error.absolute_path)
        if dotted_path:
            rendered = f"{rendered} [field {dotted_path}]"
        raise ValueError(rendered)


def _compat_mapping(parent: Mapping[str, Any], key: str, path: str) -> Mapping[str, Any] | None:
    value = parent.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping when provided")
    return value


def _compat_bool(parent: Mapping[str, Any], key: str, path: str) -> None:
    value = parent.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean when provided")


def _compat_int(
    parent: Mapping[str, Any],
    key: str,
    path: str,
    *,
    positive: bool = False,
) -> None:
    value = parent.get(key)
    if value is None:
        return
    try:
        parsed = int(value)
    except Exception as exc:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{path} must be a {qualifier} integer when provided") from exc
    if (positive and parsed <= 0) or (not positive and parsed < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{path} must be a {qualifier} integer when provided")


def _compat_number(
    parent: Mapping[str, Any],
    key: str,
    path: str,
    *,
    positive: bool = False,
) -> None:
    value = parent.get(key)
    if value is None:
        return
    try:
        parsed = float(value)
    except Exception as exc:
        comparison = "> 0" if positive else ">= 0"
        raise ValueError(f"{path} must be numeric {comparison} when provided") from exc
    if (positive and parsed <= 0) or (not positive and parsed < 0):
        comparison = "> 0" if positive else ">= 0"
        raise ValueError(f"{path} must be numeric {comparison} when provided")


def _validate_schema_less_v2_compatibility(doc: Dict[str, Any]) -> None:
    """Validate historical typed-v2 fields while retaining its open legacy surface."""
    _validate_agent_config_surface(doc, "bb.agent_config_surface.v1")

    features = _compat_mapping(doc, "features", "features")
    rlm = _compat_mapping(features, "rlm", "features.rlm") if features is not None else None
    if rlm is not None:
        _compat_bool(rlm, "enabled", "features.rlm.enabled")
        budget = _compat_mapping(rlm, "budget", "features.rlm.budget")
        if budget is not None:
            for key in ("max_depth", "max_subcalls", "max_total_tokens", "max_wallclock_seconds"):
                _compat_int(budget, key, f"features.rlm.budget.{key}")
            _compat_number(budget, "max_total_cost_usd", "features.rlm.budget.max_total_cost_usd")
            per_branch = _compat_mapping(budget, "per_branch", "features.rlm.budget.per_branch")
            if per_branch is not None:
                for key in ("max_subcalls", "max_total_tokens"):
                    _compat_int(per_branch, key, f"features.rlm.budget.per_branch.{key}")
                _compat_number(
                    per_branch,
                    "max_total_cost_usd",
                    "features.rlm.budget.per_branch.max_total_cost_usd",
                )
        blob_store = _compat_mapping(rlm, "blob_store", "features.rlm.blob_store")
        if blob_store is not None:
            for key in ("max_total_bytes", "max_blob_bytes", "mvi_excerpt_bytes"):
                _compat_int(blob_store, key, f"features.rlm.blob_store.{key}")
        subcall = _compat_mapping(rlm, "subcall", "features.rlm.subcall")
        if subcall is not None:
            _compat_int(subcall, "max_completion_tokens", "features.rlm.subcall.max_completion_tokens")
            _compat_int(subcall, "retries", "features.rlm.subcall.retries")
            _compat_number(subcall, "timeout_seconds", "features.rlm.subcall.timeout_seconds")
        scheduling = _compat_mapping(rlm, "scheduling", "features.rlm.scheduling")
        if scheduling is not None:
            mode = scheduling.get("mode")
            if mode is not None and str(mode) not in {"sync", "batch"}:
                raise ValueError("features.rlm.scheduling.mode must be one of: sync, batch")
            batch = _compat_mapping(scheduling, "batch", "features.rlm.scheduling.batch")
            if batch is not None:
                _compat_bool(batch, "enabled", "features.rlm.scheduling.batch.enabled")
                _compat_bool(batch, "fail_fast", "features.rlm.scheduling.batch.fail_fast")
                for key in ("max_concurrency", "max_concurrency_per_branch", "retries"):
                    _compat_int(batch, key, f"features.rlm.scheduling.batch.{key}")
                _compat_number(batch, "timeout_seconds", "features.rlm.scheduling.batch.timeout_seconds")
        routing = _compat_mapping(rlm, "routing", "features.rlm.routing")
        if routing is not None:
            default_lane = routing.get("default_lane")
            if default_lane is not None and str(default_lane) not in {
                "tool_heavy",
                "long_context",
                "balanced",
            }:
                raise ValueError(
                    "features.rlm.routing.default_lane must be one of: tool_heavy, long_context, balanced"
                )
            for key in ("long_context_prompt_chars", "long_context_blob_refs"):
                _compat_int(routing, key, f"features.rlm.routing.{key}")

    long_running = _compat_mapping(doc, "long_running", "long_running")
    if long_running is None:
        return
    _compat_bool(long_running, "enabled", "long_running.enabled")
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
    budgets = _compat_mapping(long_running, "budgets", "long_running.budgets")
    if budgets is not None:
        token_key = "total_tokens" if "total_tokens" in budgets else "max_total_tokens"
        cost_key = "total_cost_usd" if "total_cost_usd" in budgets else "max_total_cost_usd"
        _compat_int(budgets, token_key, "long_running.budgets.total_tokens")
        _compat_number(budgets, cost_key, "long_running.budgets.total_cost_usd")
    episode = _compat_mapping(long_running, "episode", "long_running.episode")
    if episode is not None:
        _compat_int(
            episode,
            "max_steps_override",
            "long_running.episode.max_steps_override",
            positive=True,
        )
    recovery = _compat_mapping(long_running, "recovery", "long_running.recovery")
    if recovery is not None:
        _compat_number(recovery, "backoff_base_seconds", "long_running.recovery.backoff_base_seconds")
        _compat_number(recovery, "backoff_max_seconds", "long_running.recovery.backoff_max_seconds")
        _compat_bool(
            recovery,
            "backoff_disable_jitter",
            "long_running.recovery.backoff_disable_jitter",
        )
        _compat_int(
            recovery,
            "no_progress_signature_repeats",
            "long_running.recovery.no_progress_signature_repeats",
        )
    observability = _compat_mapping(long_running, "observability", "long_running.observability")
    if observability is not None:
        _compat_bool(
            observability,
            "emit_macro_events",
            "long_running.observability.emit_macro_events",
        )
    reviewer = _compat_mapping(long_running, "reviewer", "long_running.reviewer")
    if reviewer is not None:
        _compat_bool(reviewer, "enabled", "long_running.reviewer.enabled")
        mode = reviewer.get("mode")
        if mode is not None and str(mode) != "read_only":
            raise ValueError("long_running.reviewer.mode must be read_only when provided")
    resume = _compat_mapping(long_running, "resume", "long_running.resume")
    if resume is not None:
        _compat_bool(resume, "enabled", "long_running.resume.enabled")
        state_path = resume.get("state_path")
        if state_path is not None and not isinstance(state_path, str):
            raise ValueError("long_running.resume.state_path must be a string when provided")
    verification = _compat_mapping(long_running, "verification", "long_running.verification")
    if verification is not None and verification.get("tiers") is not None:
        tiers = verification["tiers"]
        if not isinstance(tiers, list):
            raise ValueError("long_running.verification.tiers must be a list when provided")
        for index, tier in enumerate(tiers):
            path = f"long_running.verification.tiers[{index}]"
            if not isinstance(tier, Mapping):
                raise ValueError(f"{path} must be a mapping")
            commands = tier.get("commands")
            if not isinstance(commands, list) or not commands:
                raise ValueError(f"{path}.commands must be a non-empty list")
            _compat_number(tier, "timeout_seconds", f"{path}.timeout_seconds", positive=True)
            _compat_bool(tier, "hard_fail", f"{path}.hard_fail")



def _config_view_from_compilation(compilation: HarnessCompilation, config_path: Path) -> ConfigView:
    effective_doc = compilation.resolved_author_dict()
    surface_schema_version = _surface_schema_version(effective_doc)
    if surface_schema_version == "bb.harness_definition.v1":
        parse_harness_definition(effective_doc)
        runtime_doc = _normalize_for_runtime(effective_doc)
    elif surface_schema_version == "bb.agent_config_surface.v2":
        _validate_v2(effective_doc)
        runtime_doc = _normalize_for_runtime(effective_doc)
    elif _has_agent_config_version_2(effective_doc):
        if _env_truthy("AGENT_SCHEMA_V2_ENABLED"):
            _validate_schema_less_v2_compatibility(effective_doc)
            runtime_doc = _normalize_for_runtime(effective_doc)
        else:
            _validate_agent_config_surface(effective_doc, "bb.agent_config_surface.v1")
            runtime_doc = effective_doc
    else:
        runtime_doc = effective_doc
    return ConfigView(runtime_doc, graph=compilation.lock.as_dict(), config_path=config_path)

def build_config_view(config_path_str: str) -> ConfigView:
    """Compile one product-owned result and adapt it to the legacy read-only view."""
    config_path = _resolve_config_path(config_path_str)
    compilation = _compile_config(_load_yaml(config_path), config_path)
    return _config_view_from_compilation(compilation, config_path)


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


def _config_resolution_base_dirs(config_path_str: str) -> tuple[Path, Path]:
    """Return private prompt-resolution roots without decorating the public config."""

    config_path = _resolve_config_path(config_path_str)
    return config_path.parent, config_path.parent.parent.parent


def load_agent_config(config_path_str: str) -> Dict[str, Any]:
    """
    Load agent config with v1/v2 support (extends + validation + minimal normalization).
    V1 configs omit schema_version. V2 configs declare schema_version=bb.agent_config_surface.v2.
    BREADBOARD_CONFIG_AUTHORITY mirrors policy authority. Production default is config; CI or
    BREADBOARD_CONFIG_EFFECTIVE_DEFAULT=1 defaults to effective unless the authority is explicit.
    """
    config_path = _resolve_config_path(config_path_str)
    raw = _load_yaml(config_path)
    compilation = _compile_config(raw, config_path)
    doc = compilation.resolved_author_dict()



    surface_schema_version = _surface_schema_version(doc)
    if surface_schema_version == "bb.harness_definition.v1":
        parse_harness_definition(doc)
        legacy_doc = _normalize_for_runtime(doc)
    elif surface_schema_version == "bb.agent_config_surface.v2":
        _validate_v2(doc)
        legacy_doc = _normalize_for_runtime(doc)
    else:
        # The historical opt-in validates schema-less version:2 configs against
        # the typed surface while preserving their schema-less runtime shape.
        if _has_agent_config_version_2(doc) and _env_truthy("AGENT_SCHEMA_V2_ENABLED"):
            _validate_schema_less_v2_compatibility(doc)
            legacy_doc = _normalize_for_runtime(doc)
        else:
            legacy_doc = doc if _has_agent_config_version_2(doc) else raw
    if (
        surface_schema_version == "bb.agent_config_surface.v1"
        and not _has_agent_config_version_2(doc)
        and not raw.get("extends")
    ):
        return raw

    authority = _default_config_authority()
    active_logger = logging.getLogger(__name__)
    if authority not in _VALID_CONFIG_AUTHORITIES:
        active_logger.warning("Unknown BREADBOARD_CONFIG_AUTHORITY=%r; using config", authority)
        authority = "config"
    if authority == "config":
        return legacy_doc

    view = _config_view_from_compilation(compilation, config_path)
    effective_doc = view.as_dict()
    if legacy_doc != effective_doc:
        _log_config_divergence(config_path, legacy_doc, effective_doc, logger=active_logger)
    if authority == "parity":
        return legacy_doc
    return effective_doc
