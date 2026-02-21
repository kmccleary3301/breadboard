from __future__ import annotations

import os
from typing import Any, Mapping


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_LONGRUN_POLICY_PROFILES = {"conservative", "balanced", "aggressive"}


def _normalize_bool_env(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return None


def is_longrun_enabled(config: Mapping[str, Any] | None) -> bool:
    """
    Resolve whether long-running controller behavior is enabled.

    Precedence:
    1) BREADBOARD_LONGRUN_ENABLE env override (if valid boolean-like value).
    2) config.long_running.enabled (default False).
    """
    env_override = _normalize_bool_env("BREADBOARD_LONGRUN_ENABLE")
    if env_override is not None:
        return env_override

    cfg = config or {}
    long_running = cfg.get("long_running") if isinstance(cfg, Mapping) else None
    if not isinstance(long_running, Mapping):
        return False
    return bool(long_running.get("enabled", False))


def resolve_episode_max_steps(config: Mapping[str, Any] | None, default_max_steps: int) -> int:
    """
    Resolve per-episode max-steps override.

    Override is honored only when long-running mode is enabled.
    """
    try:
        default_value = int(default_max_steps)
    except Exception:
        default_value = 1
    if default_value <= 0:
        default_value = 1

    if not is_longrun_enabled(config):
        return default_value

    cfg = config or {}
    long_running = cfg.get("long_running") if isinstance(cfg, Mapping) else None
    if not isinstance(long_running, Mapping):
        return default_value
    episode = long_running.get("episode")
    if not isinstance(episode, Mapping):
        return default_value
    override = episode.get("max_steps_override")
    if override is None:
        return default_value
    try:
        parsed = int(override)
    except Exception:
        return default_value
    return parsed if parsed > 0 else default_value


def resolve_longrun_policy_profile(config: Mapping[str, Any] | None, default: str = "balanced") -> str:
    """
    Resolve longrun policy profile selection.

    Precedence:
    1) BREADBOARD_LONGRUN_POLICY_PROFILE env override when valid.
    2) config.long_running.policy_profile when valid.
    3) provided default (normalized to known profile set; fallback: balanced).
    """
    fallback = str(default or "balanced").strip().lower()
    if fallback not in _LONGRUN_POLICY_PROFILES:
        fallback = "balanced"

    env_raw = os.environ.get("BREADBOARD_LONGRUN_POLICY_PROFILE")
    if isinstance(env_raw, str) and env_raw.strip():
        env_value = env_raw.strip().lower()
        if env_value in _LONGRUN_POLICY_PROFILES:
            return env_value

    cfg = config or {}
    long_running = cfg.get("long_running") if isinstance(cfg, Mapping) else None
    if isinstance(long_running, Mapping):
        raw = long_running.get("policy_profile")
        if isinstance(raw, str):
            value = raw.strip().lower()
            if value in _LONGRUN_POLICY_PROFILES:
                return value

    return fallback
