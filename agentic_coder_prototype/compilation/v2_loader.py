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

    features = doc.get("features")
    if features is not None and not isinstance(features, dict):
        raise ValueError("features must be a mapping when provided")
    if isinstance(features, dict):
        rlm = features.get("rlm")
        if rlm is not None:
            if not isinstance(rlm, dict):
                raise ValueError("features.rlm must be a mapping when provided")
            enabled = rlm.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise ValueError("features.rlm.enabled must be a boolean when provided")
            budget = rlm.get("budget")
            if budget is not None:
                if not isinstance(budget, dict):
                    raise ValueError("features.rlm.budget must be a mapping when provided")
                for key in ("max_depth", "max_subcalls", "max_total_tokens", "max_wallclock_seconds"):
                    value = budget.get(key)
                    if value is None:
                        continue
                    try:
                        parsed = int(value)
                    except Exception as exc:
                        raise ValueError(f"features.rlm.budget.{key} must be a non-negative integer when provided") from exc
                    if parsed < 0:
                        raise ValueError(f"features.rlm.budget.{key} must be a non-negative integer when provided")
                max_total_cost = budget.get("max_total_cost_usd")
                if max_total_cost is not None:
                    try:
                        parsed = float(max_total_cost)
                    except Exception as exc:
                        raise ValueError("features.rlm.budget.max_total_cost_usd must be numeric >= 0 when provided") from exc
                    if parsed < 0:
                        raise ValueError("features.rlm.budget.max_total_cost_usd must be numeric >= 0 when provided")
                per_branch = budget.get("per_branch")
                if per_branch is not None:
                    if not isinstance(per_branch, dict):
                        raise ValueError("features.rlm.budget.per_branch must be a mapping when provided")
                    for key in ("max_subcalls", "max_total_tokens"):
                        value = per_branch.get(key)
                        if value is None:
                            continue
                        try:
                            parsed = int(value)
                        except Exception as exc:
                            raise ValueError(
                                f"features.rlm.budget.per_branch.{key} must be a non-negative integer when provided"
                            ) from exc
                        if parsed < 0:
                            raise ValueError(
                                f"features.rlm.budget.per_branch.{key} must be a non-negative integer when provided"
                            )
                    max_branch_cost = per_branch.get("max_total_cost_usd")
                    if max_branch_cost is not None:
                        try:
                            parsed = float(max_branch_cost)
                        except Exception as exc:
                            raise ValueError(
                                "features.rlm.budget.per_branch.max_total_cost_usd must be numeric >= 0 when provided"
                            ) from exc
                        if parsed < 0:
                            raise ValueError(
                                "features.rlm.budget.per_branch.max_total_cost_usd must be numeric >= 0 when provided"
                            )
            blob_store = rlm.get("blob_store")
            if blob_store is not None:
                if not isinstance(blob_store, dict):
                    raise ValueError("features.rlm.blob_store must be a mapping when provided")
                for key in ("max_total_bytes", "max_blob_bytes", "mvi_excerpt_bytes"):
                    value = blob_store.get(key)
                    if value is None:
                        continue
                    try:
                        parsed = int(value)
                    except Exception as exc:
                        raise ValueError(
                            f"features.rlm.blob_store.{key} must be a non-negative integer when provided"
                        ) from exc
                    if parsed < 0:
                        raise ValueError(f"features.rlm.blob_store.{key} must be a non-negative integer when provided")
            subcall = rlm.get("subcall")
            if subcall is not None:
                if not isinstance(subcall, dict):
                    raise ValueError("features.rlm.subcall must be a mapping when provided")
                max_completion_tokens = subcall.get("max_completion_tokens")
                if max_completion_tokens is not None:
                    try:
                        parsed = int(max_completion_tokens)
                    except Exception as exc:
                        raise ValueError(
                            "features.rlm.subcall.max_completion_tokens must be a non-negative integer when provided"
                        ) from exc
                    if parsed < 0:
                        raise ValueError(
                            "features.rlm.subcall.max_completion_tokens must be a non-negative integer when provided"
                        )
                timeout_seconds = subcall.get("timeout_seconds")
                if timeout_seconds is not None:
                    try:
                        parsed = float(timeout_seconds)
                    except Exception as exc:
                        raise ValueError("features.rlm.subcall.timeout_seconds must be numeric >= 0 when provided") from exc
                    if parsed < 0:
                        raise ValueError("features.rlm.subcall.timeout_seconds must be numeric >= 0 when provided")
                retries = subcall.get("retries")
                if retries is not None:
                    try:
                        parsed = int(retries)
                    except Exception as exc:
                        raise ValueError("features.rlm.subcall.retries must be a non-negative integer when provided") from exc
                    if parsed < 0:
                        raise ValueError("features.rlm.subcall.retries must be a non-negative integer when provided")
            scheduling = rlm.get("scheduling")
            if scheduling is not None:
                if not isinstance(scheduling, dict):
                    raise ValueError("features.rlm.scheduling must be a mapping when provided")
                mode = scheduling.get("mode")
                if mode is not None and str(mode) not in {"sync", "batch"}:
                    raise ValueError("features.rlm.scheduling.mode must be one of: sync, batch")
                batch = scheduling.get("batch")
                if batch is not None:
                    if not isinstance(batch, dict):
                        raise ValueError("features.rlm.scheduling.batch must be a mapping when provided")
                    enabled_batch = batch.get("enabled")
                    if enabled_batch is not None and not isinstance(enabled_batch, bool):
                        raise ValueError("features.rlm.scheduling.batch.enabled must be a boolean when provided")
                    max_concurrency = batch.get("max_concurrency")
                    if max_concurrency is not None:
                        try:
                            parsed = int(max_concurrency)
                        except Exception as exc:
                            raise ValueError(
                                "features.rlm.scheduling.batch.max_concurrency must be a non-negative integer when provided"
                            ) from exc
                        if parsed < 0:
                            raise ValueError(
                                "features.rlm.scheduling.batch.max_concurrency must be a non-negative integer when provided"
                            )
                    max_concurrency_per_branch = batch.get("max_concurrency_per_branch")
                    if max_concurrency_per_branch is not None:
                        try:
                            parsed = int(max_concurrency_per_branch)
                        except Exception as exc:
                            raise ValueError(
                                "features.rlm.scheduling.batch.max_concurrency_per_branch must be a non-negative integer when provided"
                            ) from exc
                        if parsed < 0:
                            raise ValueError(
                                "features.rlm.scheduling.batch.max_concurrency_per_branch must be a non-negative integer when provided"
                            )
                    retries = batch.get("retries")
                    if retries is not None:
                        try:
                            parsed = int(retries)
                        except Exception as exc:
                            raise ValueError(
                                "features.rlm.scheduling.batch.retries must be a non-negative integer when provided"
                            ) from exc
                        if parsed < 0:
                            raise ValueError(
                                "features.rlm.scheduling.batch.retries must be a non-negative integer when provided"
                            )
                    timeout_seconds = batch.get("timeout_seconds")
                    if timeout_seconds is not None:
                        try:
                            parsed = float(timeout_seconds)
                        except Exception as exc:
                            raise ValueError(
                                "features.rlm.scheduling.batch.timeout_seconds must be numeric >= 0 when provided"
                            ) from exc
                        if parsed < 0:
                            raise ValueError(
                                "features.rlm.scheduling.batch.timeout_seconds must be numeric >= 0 when provided"
                            )
                    fail_fast = batch.get("fail_fast")
                    if fail_fast is not None and not isinstance(fail_fast, bool):
                        raise ValueError("features.rlm.scheduling.batch.fail_fast must be a boolean when provided")
            routing = rlm.get("routing")
            if routing is not None:
                if not isinstance(routing, dict):
                    raise ValueError("features.rlm.routing must be a mapping when provided")
                default_lane = routing.get("default_lane")
                if default_lane is not None and str(default_lane) not in {"tool_heavy", "long_context", "balanced"}:
                    raise ValueError("features.rlm.routing.default_lane must be one of: tool_heavy, long_context, balanced")
                for key in ("long_context_prompt_chars", "long_context_blob_refs"):
                    value = routing.get(key)
                    if value is None:
                        continue
                    try:
                        parsed = int(value)
                    except Exception as exc:
                        raise ValueError(f"features.rlm.routing.{key} must be a non-negative integer when provided") from exc
                    if parsed < 0:
                        raise ValueError(f"features.rlm.routing.{key} must be a non-negative integer when provided")

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
                    raise ValueError("long_running.budgets.total_tokens must be a non-negative integer when provided") from exc
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
        episode = long_running.get("episode")
        if episode is not None:
            if not isinstance(episode, dict):
                raise ValueError("long_running.episode must be a mapping when provided")
            max_steps_override = episode.get("max_steps_override")
            if max_steps_override is not None:
                try:
                    parsed = int(max_steps_override)
                except Exception as exc:
                    raise ValueError("long_running.episode.max_steps_override must be a positive integer when provided") from exc
                if parsed <= 0:
                    raise ValueError("long_running.episode.max_steps_override must be a positive integer when provided")
        recovery = long_running.get("recovery")
        if recovery is not None:
            if not isinstance(recovery, dict):
                raise ValueError("long_running.recovery must be a mapping when provided")
            backoff_base = recovery.get("backoff_base_seconds")
            if backoff_base is not None:
                try:
                    if float(backoff_base) < 0:
                        raise ValueError("long_running.recovery.backoff_base_seconds must be >= 0 when provided")
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError("long_running.recovery.backoff_base_seconds must be numeric when provided") from exc
            backoff_max = recovery.get("backoff_max_seconds")
            if backoff_max is not None:
                try:
                    if float(backoff_max) < 0:
                        raise ValueError("long_running.recovery.backoff_max_seconds must be >= 0 when provided")
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError("long_running.recovery.backoff_max_seconds must be numeric when provided") from exc
            backoff_disable_jitter = recovery.get("backoff_disable_jitter")
            if backoff_disable_jitter is not None and not isinstance(backoff_disable_jitter, bool):
                raise ValueError("long_running.recovery.backoff_disable_jitter must be a boolean when provided")
            no_progress_signature_repeats = recovery.get("no_progress_signature_repeats")
            if no_progress_signature_repeats is not None:
                try:
                    parsed = int(no_progress_signature_repeats)
                except Exception as exc:
                    raise ValueError(
                        "long_running.recovery.no_progress_signature_repeats must be a non-negative integer when provided"
                    ) from exc
                if parsed < 0:
                    raise ValueError(
                        "long_running.recovery.no_progress_signature_repeats must be a non-negative integer when provided"
                    )
        observability = long_running.get("observability")
        if observability is not None:
            if not isinstance(observability, dict):
                raise ValueError("long_running.observability must be a mapping when provided")
            emit_macro_events = observability.get("emit_macro_events")
            if emit_macro_events is not None and not isinstance(emit_macro_events, bool):
                raise ValueError("long_running.observability.emit_macro_events must be a boolean when provided")
        reviewer = long_running.get("reviewer")
        if reviewer is not None:
            if not isinstance(reviewer, dict):
                raise ValueError("long_running.reviewer must be a mapping when provided")
            reviewer_enabled = reviewer.get("enabled")
            if reviewer_enabled is not None and not isinstance(reviewer_enabled, bool):
                raise ValueError("long_running.reviewer.enabled must be a boolean when provided")
            reviewer_mode = reviewer.get("mode")
            if reviewer_mode is not None and str(reviewer_mode) != "read_only":
                raise ValueError("long_running.reviewer.mode must be read_only when provided")
        resume = long_running.get("resume")
        if resume is not None:
            if not isinstance(resume, dict):
                raise ValueError("long_running.resume must be a mapping when provided")
            resume_enabled = resume.get("enabled")
            if resume_enabled is not None and not isinstance(resume_enabled, bool):
                raise ValueError("long_running.resume.enabled must be a boolean when provided")
            state_path = resume.get("state_path")
            if state_path is not None and not isinstance(state_path, str):
                raise ValueError("long_running.resume.state_path must be a string when provided")
        verification = long_running.get("verification")
        if verification is not None:
            if not isinstance(verification, dict):
                raise ValueError("long_running.verification must be a mapping when provided")
            tiers = verification.get("tiers")
            if tiers is not None:
                if not isinstance(tiers, list):
                    raise ValueError("long_running.verification.tiers must be a list when provided")
                for idx, tier in enumerate(tiers):
                    if not isinstance(tier, dict):
                        raise ValueError(f"long_running.verification.tiers[{idx}] must be a mapping")
                    commands = tier.get("commands")
                    if not isinstance(commands, list) or not commands:
                        raise ValueError(f"long_running.verification.tiers[{idx}].commands must be a non-empty list")
                    timeout_seconds = tier.get("timeout_seconds")
                    if timeout_seconds is not None:
                        try:
                            if float(timeout_seconds) <= 0:
                                raise ValueError(
                                    f"long_running.verification.tiers[{idx}].timeout_seconds must be > 0 when provided"
                                )
                        except ValueError:
                            raise
                        except Exception as exc:
                            raise ValueError(
                                f"long_running.verification.tiers[{idx}].timeout_seconds must be numeric when provided"
                            ) from exc
                    hard_fail = tier.get("hard_fail")
                    if hard_fail is not None and not isinstance(hard_fail, bool):
                        raise ValueError(f"long_running.verification.tiers[{idx}].hard_fail must be a boolean when provided")


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
    out.setdefault("long_running", doc.get("long_running", {}))

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
