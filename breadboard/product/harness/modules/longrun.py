from collections.abc import Mapping, Sequence
from typing import TypeGuard

from .extensions import CompositionError, ModuleContribution, Operation, _owned

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
        base = recovery.get("backoff_base_seconds")
        maximum = recovery.get("backoff_max_seconds")
        if _number(base) and _number(maximum) and base > maximum:
            raise CompositionError("recovery backoff base cannot exceed maximum")
    verification = long_running.get("verification")
    if isinstance(verification, Mapping) and isinstance(
        verification.get("tiers"), (list, tuple)
    ):
        names = [
            tier["name"]
            for tier in verification["tiers"]
            if isinstance(tier, Mapping) and isinstance(tier.get("name"), str)
        ]
        if len(names) != len(set(names)):
            raise CompositionError("verification tier names must be unique")
    if long_running.get("enabled") is True:
        budgets = long_running.get("budgets")
        if not isinstance(budgets, Mapping):
            budgets = {}
        tokens = budgets.get("total_tokens", budgets.get("max_total_tokens"))
        cost = budgets.get("total_cost_usd", budgets.get("max_total_cost_usd"))
        if not (_positive(tokens) or _positive(cost)):
            raise CompositionError(
                "enabled long-running execution requires a positive stopping budget"
            )


def _number(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def _positive(value: object) -> bool:
    return _number(value) and value > 0


def build_longrun_module(
    operations: Sequence[Operation], precedence: int = 60
) -> ModuleContribution:
    return _owned("longrun", precedence, operations, _ROOTS, _validate)
