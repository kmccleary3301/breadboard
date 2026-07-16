from collections.abc import Mapping
from typing import TypeGuard

from .extensions import CompositionError, _Mod, _Ops, _owned

_ROOTS = frozenset({"long_running", "turn_strategy", "completion"})


def _validate(document: Mapping[str, object]) -> None:
    long_running = document.get("long_running")
    if not isinstance(long_running, Mapping):
        return
    resume = long_running.get("resume")
    if isinstance(resume, Mapping) and resume.get("enabled") is True:
        state_path = resume.get("state_path")
        if not isinstance(state_path, str) or not state_path:
            raise CompositionError("enabled resume requires a nonempty state_path")
    recovery = long_running.get("recovery")
    if isinstance(recovery, Mapping):
        base = recovery.get("backoff_base_seconds", 0.25)
        maximum = recovery.get("backoff_max_seconds", 2.0)
        if base == 0 or maximum == 0:
            raise CompositionError("recovery backoffs must be positive when provided")
        if _number(base) and _number(maximum) and base > maximum:
            raise CompositionError("recovery backoff base cannot exceed maximum")
    verification = long_running.get("verification")
    if isinstance(verification, Mapping) and isinstance(
        verification.get("tiers"), (list, tuple)
    ):
        names = [
            tier.get("name", f"tier_{index}")
            for index, tier in enumerate(verification["tiers"])
            if isinstance(tier, Mapping)
        ]
        if len(names) != len(set(names)):
            raise CompositionError("verification tier names must be unique")
    if long_running.get("enabled") is True:
        budgets = long_running.get("budgets")
        if not isinstance(budgets, Mapping):
            budgets = {}
        tokens = budgets.get("total_tokens", budgets.get("max_total_tokens"))
        cost = budgets.get("total_cost_usd", budgets.get("max_total_cost_usd"))
        if not any(_number(value) and value > 0 for value in (tokens, cost)):
            raise CompositionError("positive stopping budget required")


def _number(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def build_longrun_module(operations: _Ops, precedence: int = 60) -> _Mod:
    return _owned("longrun", precedence, operations, _ROOTS, _validate)
