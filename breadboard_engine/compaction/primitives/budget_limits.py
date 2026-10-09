"""Compound input-budget limits, parameterized independently of presets."""

from ..params import Params


class EffectiveBudget:
    kind = "effective_budget"

    def __init__(self, params: Params) -> None:
        self.percent = params.number("percent", 0.5)
        self.small_window = params.int("small_window", 512000, minimum=0)
        self.small_percent = params.number("small_percent", 0.75)
        self.floor = params.int("floor", 64000, minimum=0)
        self.safety_ratio = params.number("safety_ratio", 0.85)
        self.output_reserve = params.int("output_reserve", 0, minimum=0)
        self.window = params.int("window", None, minimum=1)
        self.cap = params.int("cap", None, minimum=1)
        params.done()

    def tokens(self, window: int, max_output: int | None) -> int:
        window = self.window or window
        usable = window - self.output_reserve
        if usable <= 0:
            usable = window
        percent = max(self.percent, self.small_percent) if window < self.small_window else self.percent
        base = int(usable * percent)
        limit = max(base, self.floor)
        safety = int(usable * self.safety_ratio)
        if usable > 0 and limit > base and limit > safety:
            limit = max(base, safety)
        if usable > 0 and limit >= usable:
            limit = max(1, min(safety, usable - 1))
        return min(limit, self.cap, window) if self.cap is not None else limit


class FlooredCappedReserve:
    kind = "floored_capped_reserve"

    def __init__(self, params: Params) -> None:
        self.reserve = params.int("reserve", 16384, minimum=0)
        self.floor = params.int("floor", 20000, minimum=0)
        self.cap_ratio = params.number("cap_ratio", 0.25)
        params.done()

    def tokens(self, window: int, max_output: int | None) -> int:
        return window - min(max(self.reserve, self.floor), int(window * self.cap_ratio))
