"""Compaction configuration and threshold arithmetic.

Key names and defaults follow oh-my-pi (``packages/agent/src/compaction/
compaction.ts`` and ``coding-agent/src/config/settings.ts``). The agent config
``compaction:`` block accepts OMP camelCase keys or snake_case equivalents.

BreadBoard differs from OMP in one default: compaction is disabled unless the
config enables it, because existing E4 parity targets assume no compaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Literal, Mapping, Optional, Tuple

CompactionMethodName = Literal["remote", "snapcompact", "handoff", "shake", "soft"]

COMPACTION_METHODS: Tuple[str, ...] = ("remote", "snapcompact", "handoff", "shake", "soft")
DEFAULT_METHOD_ORDER: Tuple[str, ...] = ("remote", "snapcompact", "handoff", "shake", "soft")

DEFAULT_RESERVE_TOKENS = 16384
MAX_SUMMARY_TOKENS = DEFAULT_RESERVE_TOKENS
DEFAULT_KEEP_RECENT_TOKENS = 20000
V2_RETAINED_MESSAGE_TOKEN_BUDGET = 64_000
"""OMP ``compaction-v2-streaming.ts:45``; also the cap applied to configured values."""

LEGACY_STRATEGIES = ("context-full", "handoff", "shake", "snapcompact", "off")

OverflowPolicy = Literal["compact", "terminal"]


def _method_order_for_strategy(strategy: str, remote_enabled: bool) -> Tuple[str, ...]:
    """OMP ``settings.ts`` migration from ``strategy``/``remoteEnabled``."""
    if strategy == "shake-summary":
        strategy = "shake"
    if strategy == "context-full":
        return ("remote", "soft") if remote_enabled else ("soft",)
    if strategy in {"handoff", "shake", "snapcompact"}:
        return (strategy, "remote", "soft") if remote_enabled else (strategy, "soft")
    if strategy == "off":
        return ()
    raise ValueError(f"unknown compaction strategy: {strategy!r}")


@dataclass(frozen=True)
class PruneSettings:
    """Tool-result pruning applied at request projection (OMP ``pruning.ts``).

    Defaults mirror ``DEFAULT_PRUNE_CONFIG``.
    """

    enabled: bool = False
    supersede_reads: bool = True
    prune_useless: bool = True
    protect_tokens: int = 40000
    minimum_savings: int = 20000


@dataclass(frozen=True)
class ShakeSettings:
    """Heavy-content elision (OMP ``shake.ts`` ``DEFAULT_SHAKE_CONFIG``)."""

    protect_tokens: int = 16000
    min_savings: int = 4000
    fence_min_tokens: int = 400


@dataclass(frozen=True)
class SnapcompactSettings:
    """Bitmap archive rendering (OMP ``snapcompact.ts``).

    ``system_prompt`` and ``tool_results`` select inline imaging scopes:
    ``"none"`` disables inline imaging for that scope.
    """

    system_prompt: str = "none"
    tool_results: str = "none"
    inline_min_tokens: int = 4000
    shape: str = "auto"


@dataclass(frozen=True)
class CompactionSettings:
    enabled: bool = False
    method_order: Tuple[str, ...] = DEFAULT_METHOD_ORDER
    threshold_percent: float = -1
    threshold_tokens: int = -1
    reserve_tokens: Optional[int] = None
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS
    mid_turn_enabled: bool = True
    overflow_policy: OverflowPolicy = "compact"
    remote_endpoint: Optional[str] = None
    remote_streaming_v2_enabled: bool = True
    v2_retained_message_budget: int = V2_RETAINED_MESSAGE_TOKEN_BUDGET
    context_window: Optional[int] = None
    summary_model: Optional[str] = None
    custom_instructions: Optional[str] = None
    max_passes_per_turn: int = 2
    prune: PruneSettings = field(default_factory=PruneSettings)
    shake: ShakeSettings = field(default_factory=ShakeSettings)
    snapcompact: SnapcompactSettings = field(default_factory=SnapcompactSettings)

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.method_order)

    def with_method_order(self, order: Tuple[str, ...]) -> "CompactionSettings":
        return replace(self, method_order=_validate_method_order(order))


_TOP_KEYS = {
    "enabled": "enabled",
    "methodOrder": "method_order",
    "method_order": "method_order",
    "strategy": "strategy",
    "remoteEnabled": "remote_enabled",
    "remote_enabled": "remote_enabled",
    "thresholdPercent": "threshold_percent",
    "threshold_percent": "threshold_percent",
    "thresholdTokens": "threshold_tokens",
    "threshold_tokens": "threshold_tokens",
    "reserveTokens": "reserve_tokens",
    "reserve_tokens": "reserve_tokens",
    "keepRecentTokens": "keep_recent_tokens",
    "keep_recent_tokens": "keep_recent_tokens",
    "midTurnEnabled": "mid_turn_enabled",
    "mid_turn_enabled": "mid_turn_enabled",
    "overflowPolicy": "overflow_policy",
    "overflow_policy": "overflow_policy",
    "remoteEndpoint": "remote_endpoint",
    "remote_endpoint": "remote_endpoint",
    "remoteStreamingV2Enabled": "remote_streaming_v2_enabled",
    "remote_streaming_v2_enabled": "remote_streaming_v2_enabled",
    "v2RetainedMessageBudget": "v2_retained_message_budget",
    "v2_retained_message_budget": "v2_retained_message_budget",
    "contextWindow": "context_window",
    "context_window": "context_window",
    "summaryModel": "summary_model",
    "summary_model": "summary_model",
    "customInstructions": "custom_instructions",
    "custom_instructions": "custom_instructions",
    "maxPassesPerTurn": "max_passes_per_turn",
    "max_passes_per_turn": "max_passes_per_turn",
    "autoContinue": None,
    "prune": "prune",
    "shake": "shake",
    "snapcompact": "snapcompact",
}


def _snake(name: str) -> str:
    out = []
    for char in name:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out)


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"compaction.{name} must be a boolean")
    return value


def _int(value: Any, name: str, *, allow_negative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"compaction.{name} must be an integer")
    if value < 0 and not allow_negative:
        raise ValueError(f"compaction.{name} must be non-negative")
    return value


def _validate_method_order(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("compaction.methodOrder must be a list")
    order: list[str] = []
    for method in value:
        if method not in COMPACTION_METHODS:
            raise ValueError(f"unknown compaction method: {method!r}")
        if method not in order:
            order.append(method)
    return tuple(order)


def _nested(cls: Any, raw: Any, name: str) -> Any:
    if raw is None:
        return cls()
    if not isinstance(raw, Mapping):
        raise ValueError(f"compaction.{name} must be a mapping")
    known = {f for f in cls.__dataclass_fields__}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        attr = _snake(str(key))
        if attr not in known:
            raise ValueError(f"unknown compaction.{name} key: {key!r}")
        default = getattr(cls(), attr)
        if isinstance(default, bool):
            values[attr] = _bool(value, f"{name}.{key}")
        elif isinstance(default, int):
            values[attr] = _int(value, f"{name}.{key}")
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise ValueError(f"compaction.{name}.{key} must be a string")
            values[attr] = value
        else:
            values[attr] = value
    return cls(**values)


def settings_from_config(raw: Any) -> CompactionSettings:
    """Parse the agent config ``compaction`` block. ``None`` means disabled."""
    if raw is None:
        return CompactionSettings()
    if isinstance(raw, bool):
        return CompactionSettings(enabled=raw)
    if not isinstance(raw, Mapping):
        raise ValueError("compaction config must be a mapping")
    values: dict[str, Any] = {}
    seen: dict[str, str] = {}
    for key, value in raw.items():
        if key not in _TOP_KEYS:
            raise ValueError(f"unknown compaction key: {key!r}")
        attr = _TOP_KEYS[key]
        if attr is None:
            continue
        if attr in seen:
            raise ValueError(f"compaction keys {seen[attr]!r} and {key!r} conflict")
        seen[attr] = key
        values[attr] = value

    strategy = values.pop("strategy", None)
    remote_enabled = values.pop("remote_enabled", None)
    if remote_enabled is not None:
        remote_enabled = _bool(remote_enabled, "remoteEnabled")
    if "method_order" in values:
        if strategy is not None:
            raise ValueError("compaction.methodOrder and compaction.strategy are exclusive")
        order = _validate_method_order(values.pop("method_order"))
        if remote_enabled is False:
            order = tuple(m for m in order if m != "remote")
    elif strategy is not None:
        if not isinstance(strategy, str):
            raise ValueError("compaction.strategy must be a string")
        order = _method_order_for_strategy(strategy, remote_enabled is not False)
    elif remote_enabled is False:
        order = tuple(m for m in DEFAULT_METHOD_ORDER if m != "remote")
    else:
        order = DEFAULT_METHOD_ORDER

    kwargs: dict[str, Any] = {"method_order": order}
    for attr, value in values.items():
        if attr in {"enabled", "mid_turn_enabled", "remote_streaming_v2_enabled"}:
            kwargs[attr] = _bool(value, attr)
        elif attr in {"threshold_tokens"}:
            kwargs[attr] = _int(value, attr, allow_negative=True)
        elif attr == "threshold_percent":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("compaction.thresholdPercent must be a number")
            kwargs[attr] = float(value)
        elif attr in {"keep_recent_tokens", "v2_retained_message_budget", "max_passes_per_turn"}:
            kwargs[attr] = _int(value, attr)
        elif attr in {"reserve_tokens", "context_window"}:
            kwargs[attr] = None if value is None else _int(value, attr)
        elif attr == "overflow_policy":
            if value not in ("compact", "terminal"):
                raise ValueError("compaction.overflowPolicy must be 'compact' or 'terminal'")
            kwargs[attr] = value
        elif attr in {"remote_endpoint", "summary_model", "custom_instructions"}:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"compaction.{attr} must be a string")
            kwargs[attr] = value
        elif attr == "prune":
            kwargs[attr] = _nested(PruneSettings, value, "prune")
        elif attr == "shake":
            kwargs[attr] = _nested(ShakeSettings, value, "shake")
        elif attr == "snapcompact":
            kwargs[attr] = _nested(SnapcompactSettings, value, "snapcompact")
    if kwargs.get("context_window") == 0:
        raise ValueError("compaction.contextWindow must be positive")
    return CompactionSettings(**kwargs)


def effective_reserve_tokens(context_window: int, settings: CompactionSettings) -> int:
    return max(
        math.floor(context_window * 0.15),
        settings.reserve_tokens if settings.reserve_tokens is not None else DEFAULT_RESERVE_TOKENS,
    )


def resolve_budget_reserve_tokens(context_window: int, settings: CompactionSettings) -> int:
    reserve = effective_reserve_tokens(context_window, settings)
    proportional = max(1, math.floor(context_window * 0.15))
    defaulted = settings.reserve_tokens is None
    if (defaulted and reserve >= context_window - proportional) or reserve >= context_window:
        return proportional
    return reserve


def resolve_threshold_tokens(context_window: int, settings: CompactionSettings) -> int:
    if settings.threshold_tokens > 0:
        return min(context_window - 1, max(1, settings.threshold_tokens))
    percent = settings.threshold_percent
    if not math.isfinite(percent) or percent <= 0:
        return max(0, min(context_window - 1, context_window - resolve_budget_reserve_tokens(context_window, settings)))
    clamped = min(99.0, max(1.0, percent))
    return math.floor(context_window * (clamped / 100))


def should_compact(context_tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    if not settings.active or context_window <= 0:
        return False
    return context_tokens > resolve_threshold_tokens(context_window, settings)


def summary_max_tokens(context_window: int, settings: CompactionSettings) -> int:
    return max(1, min(MAX_SUMMARY_TOKENS, math.floor(0.8 * resolve_budget_reserve_tokens(context_window, settings))))
