"""Limit and trigger primitives.

A *limit* turns the model window (and output cap) into a token count. A
*trigger* decides, before a request, whether to compact. Comparators and
phases differ per harness (Pi ``>`` once per user prompt, Codex ``>=``
before a turn, OMP ``>`` before every request), so both are explicit.
Overflow and manual compaction are not triggers: the controller starts them
from a provider error or a caller.
"""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from ..params import Params, PresetError
from ..settings import resolve_budget_reserve_tokens, resolve_threshold_tokens
from .accounting import OccupancyInput, build_occupancy

COMPARATORS: Dict[str, Callable[[int, int], bool]] = {">": operator.gt, ">=": operator.ge}


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


class OmpSettingsLimit:
    """OMP limits from ``CompactionSettings``.

    ``use: threshold`` — ``resolveThresholdTokens`` (thresholdTokens,
    thresholdPercent, else window minus the budget reserve).
    ``use: overflow_budget`` — window minus ``resolveBudgetReserveTokens``.
    """

    kind = "omp_settings"

    def __init__(self, params: Params) -> None:
        self.use = params.choice("use", ("threshold", "overflow_budget"))
        params.done()
        self.settings = params.env.settings

    def tokens(self, window: int, max_output: Optional[int]) -> int:
        if self.use == "threshold":
            return resolve_threshold_tokens(window, self.settings)
        return max(1, window - resolve_budget_reserve_tokens(window, self.settings))


class WindowMinusReserve:
    """``window - reserve`` (Pi ``contextWindow - reserveTokens``)."""

    kind = "window_minus_reserve"

    def __init__(self, params: Params) -> None:
        self.reserve = params.int("reserve", minimum=0)
        params.done()

    def tokens(self, window: int, max_output: Optional[int]) -> int:
        return window - self.reserve


class WindowFraction:
    """``floor(window * percent / 100)``."""

    kind = "window_fraction"

    def __init__(self, params: Params) -> None:
        self.percent = params.number("percent")
        params.done()
        if not 0 < self.percent <= 100:
            raise PresetError(f"{params.where}.percent must be in (0, 100]")

    def tokens(self, window: int, max_output: Optional[int]) -> int:
        return math.floor(window * self.percent / 100)


class OutputReservePlusBuffer:
    """``window - min(max_output or default_output, output_cap) - buffer``."""

    kind = "output_reserve_plus_buffer"

    def __init__(self, params: Params) -> None:
        self.output_cap = params.int("output_cap", minimum=0)
        self.default_output = params.int("default_output", minimum=0)
        self.buffer = params.int("buffer", 0, minimum=0)
        params.done()

    def tokens(self, window: int, max_output: Optional[int]) -> int:
        output = max_output if max_output else self.default_output
        return window - min(output, self.output_cap) - self.buffer


class FixedLimit:
    kind = "fixed"

    def __init__(self, params: Params) -> None:
        self.value = params.int("tokens", minimum=1)
        params.done()

    def tokens(self, window: int, max_output: Optional[int]) -> int:
        return self.value


LIMIT_KINDS = {
    cls.kind: cls
    for cls in (OmpSettingsLimit, WindowMinusReserve, WindowFraction, OutputReservePlusBuffer, FixedLimit)
}


def build_limit(params: Params) -> Any:
    kind = params.kind(LIMIT_KINDS)
    return LIMIT_KINDS[kind](params)


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------

PHASES = ("every_request", "user_turn_start")
"""``user_turn_start``: only when the last history message is a user message,
i.e. before the first request after new user input."""


@dataclass(frozen=True)
class TriggerInput:
    occupancy: OccupancyInput
    context_window: Optional[int]
    max_output_tokens: Optional[int] = None
    reason: str = "threshold"
    max_input_tokens: Optional[int] = None


@dataclass(frozen=True)
class Pressure:
    fires: bool
    tokens: Optional[int]
    limit: int
    source: str
    severity: Optional[str]
    in_phase: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"fires": self.fires, "tokens": self.tokens, "limit": self.limit, "severity": self.severity}


class ThresholdTrigger:
    """Fire when measured occupancy compares true against the limit.

    ``phase: omp_mid_turn_setting`` resolves to ``every_request`` when
    ``midTurnEnabled`` is true and ``user_turn_start`` otherwise.
    """

    kind = "threshold"

    def __init__(self, params: Params) -> None:
        self.accounting = build_occupancy(params.child(params.mapping("accounting"), "accounting"))
        self.limit = build_limit(params.child(params.mapping("limit"), "limit"))
        self.compare = params.choice("compare", tuple(COMPARATORS))
        phase = params.choice("phase", (*PHASES, "omp_mid_turn_setting"))
        if phase == "omp_mid_turn_setting":
            phase = "every_request" if params.env.settings.mid_turn_enabled else "user_turn_start"
        self.phase = phase
        self.severity = params.choice("severity", ("soft", "hard"), "soft")
        params.done()

    def in_phase(self, history: Sequence[Mapping[str, Any]]) -> bool:
        if self.phase == "every_request":
            return True
        return bool(history) and history[-1].get("role") == "user"

    def evaluate(self, data: TriggerInput, history: Sequence[Mapping[str, Any]]) -> Pressure:
        if data.context_window is None or data.context_window <= 0:
            return Pressure(False, None, 0, "missing_context_window", self.severity, False)
        limit = self.limit.tokens(data.context_window, data.max_output_tokens)
        in_phase = self.in_phase(history)
        measured = self.accounting.measure(data.occupancy)
        if measured is None:
            return Pressure(False, None, limit, "no_usage", self.severity, in_phase)
        fires = in_phase and COMPARATORS[self.compare](measured.tokens, limit)
        return Pressure(fires, measured.tokens, limit, measured.source, self.severity, in_phase)


class EventCountTrigger:
    """Strict event pressure with optional tokenizer pressure and explicit requests."""

    kind = "event_count"

    def __init__(self, params: Params) -> None:
        self.maximum = params.int("maximum", 240, minimum=1)
        self.max_tokens = params.int("max_tokens", None)
        from .accounting import build_estimator

        self.count = build_estimator(params.str("estimator"))
        params.done()

    def evaluate(self, data: TriggerInput, history: Sequence[Mapping[str, Any]]) -> Pressure:
        # Event pressure is independent of the optional model input cap.

        events = data.occupancy.messages
        request = data.reason in {"manual", "overflow"}
        caps = [v for v in (self.max_tokens, data.max_input_tokens) if v is not None]
        cap = min(caps) if data.max_input_tokens is not None else None
        total = self.count(events) if cap is not None else None
        token_pressure = total is not None and total > cap
        fires = request or token_pressure or len(events) > self.maximum
        severity = "hard" if request or token_pressure else "soft" if fires else None
        return Pressure(
            fires, total if total is not None else len(events) if fires else 0,
            cap if total is not None else self.maximum,
            "estimate" if total is not None else "events; token pressure unavailable: missing max_input_tokens",
            severity, True,
        )


TRIGGER_KINDS = {ThresholdTrigger.kind: ThresholdTrigger, EventCountTrigger.kind: EventCountTrigger}


def build_trigger(params: Params) -> Any:
    kind = params.kind(TRIGGER_KINDS)
    return TRIGGER_KINDS[kind](params)
