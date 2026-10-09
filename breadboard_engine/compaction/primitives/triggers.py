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

    def tokens(self, window: int, max_output: Optional[int], max_input: Optional[int] = None) -> int:
        if self.use == "threshold":
            return resolve_threshold_tokens(window, self.settings)
        return max(1, window - resolve_budget_reserve_tokens(window, self.settings))


class WindowMinusReserve:
    """``window - reserve`` (Pi ``contextWindow - reserveTokens``)."""

    kind = "window_minus_reserve"

    def __init__(self, params: Params) -> None:
        self.reserve = params.int("reserve", minimum=0)
        params.done()

    def tokens(self, window: int, max_output: Optional[int], max_input: Optional[int] = None) -> int:
        return window - self.reserve


class WindowFraction:
    """``floor(window * percent / 100)``."""

    kind = "window_fraction"

    def __init__(self, params: Params) -> None:
        self.percent = params.number("percent")
        params.done()
        if not 0 < self.percent <= 100:
            raise PresetError(f"{params.where}.percent must be in (0, 100]")

    def tokens(self, window: int, max_output: Optional[int], max_input: Optional[int] = None) -> int:
        return math.floor(window * self.percent / 100)


class OutputReservePlusBuffer:
    """``window - min(max_output or default_output, output_cap) - buffer``."""

    kind = "output_reserve_plus_buffer"

    def __init__(self, params: Params) -> None:
        self.output_cap = params.int("output_cap", minimum=0)
        self.default_output = params.int("default_output", minimum=0)
        self.buffer = params.int("buffer", 0, minimum=0)
        params.done()

    def tokens(self, window: int, max_output: Optional[int], max_input: Optional[int] = None) -> int:
        output = max_output if max_output else self.default_output
        return window - min(output, self.output_cap) - self.buffer


class InputOrWindowReserve:
    """Use an input limit with a buffer, otherwise reserve the model's output."""

    kind = "input_or_window_reserve"

    def __init__(self, params: Params) -> None:
        self.output_cap = params.int("output_cap", 32000, minimum=1)
        self.input_buffer = params.int("input_buffer", 20000, minimum=0)
        self.reserved = params.int("reserved", None, minimum=0)
        params.done()

    def tokens(self, window: int, max_output: Optional[int], max_input: Optional[int] = None) -> int:
        output = min(max_output or self.output_cap, self.output_cap)
        if max_input:
            reserve = self.reserved if self.reserved is not None else min(self.input_buffer, output)
            return max_input - reserve
        return window - output


class FixedLimit:
    kind = "fixed"

    def __init__(self, params: Params) -> None:
        self.value = params.int("tokens", minimum=1)
        params.done()

    def tokens(self, window: int, max_output: Optional[int], max_input: Optional[int] = None) -> int:
        return self.value


LIMIT_KINDS = {
    cls.kind: cls
    for cls in (OmpSettingsLimit, WindowMinusReserve, WindowFraction, OutputReservePlusBuffer, FixedLimit,
                InputOrWindowReserve)
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
    context_window: int
    max_output_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None
    reason: str = "threshold"


@dataclass(frozen=True)
class Pressure:
    fires: bool
    tokens: Optional[int]
    limit: int
    source: str
    severity: str
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
        self.enabled = params.bool("enabled", True)
        self.disable_zero_window = params.bool("disable_zero_window", False)
        self.provider_overflow = params.bool("provider_overflow", False)
        params.done()

    def in_phase(self, history: Sequence[Mapping[str, Any]]) -> bool:
        if self.phase == "every_request":
            return True
        return bool(history) and history[-1].get("role") == "user"

    def evaluate(self, data: TriggerInput, history: Sequence[Mapping[str, Any]]) -> Pressure:
        limit = self.limit.tokens(data.context_window, data.max_output_tokens, data.max_input_tokens)
        in_phase = self.in_phase(history)
        measured = self.accounting.measure(data.occupancy)
        if measured is None:
            return Pressure(False, None, limit, "no_usage", self.severity, in_phase)
        if self.provider_overflow and data.reason == "overflow":
            return Pressure(True, measured.tokens, limit, measured.source, "hard", in_phase)
        enabled = self.enabled and not (self.disable_zero_window and data.context_window == 0)
        fires = enabled and in_phase and COMPARATORS[self.compare](measured.tokens, limit)
        return Pressure(fires, measured.tokens, limit, measured.source, self.severity, in_phase)


TRIGGER_KINDS = {ThresholdTrigger.kind: ThresholdTrigger}


def build_trigger(params: Params) -> Any:
    kind = params.kind(TRIGGER_KINDS)
    return TRIGGER_KINDS[kind](params)
