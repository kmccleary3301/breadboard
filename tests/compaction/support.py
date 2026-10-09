"""Shared helpers for compaction tests: the OMP recipe and fallback pipelines."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from breadboard_engine.compaction.methods import CompactionContext
from breadboard_engine.compaction.pipeline import AlgorithmStep, CompactionOutcome, ComposedStep, Pipeline, Stage
from breadboard_engine.compaction.presets import CompactionConfig, Recipe, build_recipe
from breadboard_engine.compaction.settings import (
    CompactionSettings,
    resolve_budget_reserve_tokens,
    resolve_threshold_tokens,
)
from breadboard_engine.compaction.tokens import estimate_messages_tokens


def omp_recipe(settings: CompactionSettings) -> Recipe:
    """The ``omp@18.4.5`` recipe built from ``settings``."""
    return build_recipe(CompactionConfig(True, "omp@18.4.5", settings, {}, None))


def omp_target_tokens(context: CompactionContext) -> int:
    """OMP pass target: the threshold, or window minus reserve on overflow."""
    if context.reason == "overflow":
        return max(1, context.context_window - resolve_budget_reserve_tokens(context.context_window, context.settings))
    return resolve_threshold_tokens(context.context_window, context.settings)


def run_pipeline(
    context: CompactionContext,
    steps: Mapping[str, Any],
    target_tokens: Optional[int] = None,
    order: Optional[Sequence[str]] = None,
) -> CompactionOutcome:
    """Run ``steps`` (methods or steps, by stage id) as an OMP ``fallback`` pipeline.

    Stages follow ``context.settings.method_order``, then any ids it does not name.
    """
    ids = [i for i in context.settings.method_order if i in steps]
    ids += [i for i in steps if i not in ids]
    stages = [
        Stage(i, steps[i] if isinstance(steps[i], (AlgorithmStep, ComposedStep)) else AlgorithmStep(steps[i]))
        for i in ids
    ]
    pipeline = Pipeline(stages, mode="fallback", count=estimate_messages_tokens)
    return pipeline.run(
        context,
        target_tokens=omp_target_tokens(context) if target_tokens is None else target_tokens,
        order=order,
    )
