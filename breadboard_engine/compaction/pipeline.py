"""Compaction pipeline: ordered stages with explicit per-status routing.

Every stage reports exactly one :data:`StageStatus`; nothing is skipped
silently. ``mode`` names the routing table:

==================  ===========  =======  ==========  ===============  ====
mode                unavailable  failed   edited      committed        noop
==================  ===========  =======  ==========  ===============  ====
``fallback``        next         next     keep, next  stop at target   next
``sequence``        next         stop     keep, next  keep, next       next
``until_boundary``  next         stop     keep, next  stop             next
==================  ===========  =======  ==========  ===============  ====

``fallback`` is OMP's method cascade: once a record exists and the view is
at or under the target, remaining stages report ``noop`` ("target reached").
:class:`CompactionCancelled` always propagates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Tuple

from .methods import CompactionCancelled, CompactionContext, CompactionMethod, MethodUnavailable
from .primitives.placement import build_placement
from .primitives.reducers import build_reducer
from .primitives.selectors import Selection, build_selector
from .params import Params
from .state import CompactionRecord, CompactionState
from .primitives.accounting import OccupancyInput, normalize_usage
from .primitives.triggers import TriggerInput, build_trigger

StageStatus = Literal["committed", "edited", "noop", "unavailable", "failed"]
MODES = ("fallback", "sequence", "until_boundary")
REASONS = ("threshold", "overflow", "manual")


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: StageStatus
    detail: Optional[str] = None
    tokens_after: Optional[int] = None
    record_id: Optional[str] = None
    selection: Optional[Selection] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.stage,
            "status": self.status,
            "detail": self.detail,
            "tokens_after": self.tokens_after,
            "record_id": self.record_id,
        }


@dataclass(frozen=True)
class CompactionOutcome:
    records: Tuple[CompactionRecord, ...]
    stages: Tuple[StageResult, ...]
    tokens_before: int
    tokens_after: int
    target_tokens: int
    reached_target: bool

    @property
    def compacted(self) -> bool:
        return bool(self.records)

    @property
    def status(self) -> str:
        """``committed`` (a boundary), ``edited`` (edits only), ``failed`` or ``noop``."""
        if any(r.is_boundary for r in self.records):
            return "committed"
        if self.records:
            return "edited"
        if any(s.status == "failed" for s in self.stages):
            return "failed"
        return "noop"


@dataclass(frozen=True)
class StepOutput:
    record: CompactionRecord
    selection: Optional[Selection] = None


class ComposedStep:
    """Selector → reducer → placement, recorded as one boundary or edit record."""

    def __init__(self, name: str, selector: Any, reducer: Any, placement: Optional[Any]) -> None:
        self.name = name
        self.selector = selector
        self.reducer = reducer
        self.placement = placement

    def run(self, context: CompactionContext) -> StepOutput:
        selection = self.selector.select(context)
        reduction = self.reducer.reduce(context, selection)
        summary_messages: Sequence[Mapping[str, Any]] = ()
        if selection.first_kept_index is not None and self.placement is not None:
            summary_messages = self.placement.place(context, selection, reduction)
        record = context.new_record(
            method=self.name,
            first_kept_index=selection.first_kept_index,
            prefix_end=selection.prefix_end,
            summary=reduction.summary,
            short_summary=reduction.short_summary,
            summary_messages=summary_messages,
            native=reduction.native,
            edits=reduction.edits,
            details=reduction.details,
        )
        return StepOutput(record, selection)


class AlgorithmStep:
    """A whole-method stage (OMP ``remote``, ``snapcompact``, ``handoff``, ``shake``)."""

    def __init__(self, method: CompactionMethod) -> None:
        self.name = method.name
        self.method = method

    def run(self, context: CompactionContext) -> StepOutput:
        return StepOutput(self.method.run(context))


@dataclass(frozen=True)
class Stage:
    id: str
    step: Any
    reasons: Optional[FrozenSet[str]] = None
    """``when.reasons``: compaction reasons the stage runs for (all if ``None``)."""
    accept: Literal["progress", "any"] = "progress"
    trigger: Optional[Any] = None
    commit_detail: Optional[str] = None


class Pipeline:
    def __init__(
        self,
        stages: Sequence[Stage],
        mode: str,
        count: Callable[[Sequence[Mapping[str, Any]]], int],
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown pipeline mode {mode!r}")
        self.stages = {stage.id: stage for stage in stages}
        self.order: Tuple[str, ...] = tuple(stage.id for stage in stages)
        self.mode = mode
        self.count = count

    def run(
        self,
        context: CompactionContext,
        *,
        target_tokens: int,
        order: Optional[Sequence[str]] = None,
    ) -> CompactionOutcome:
        results: List[StageResult] = []
        records: List[CompactionRecord] = []
        current = self.count(context.projected())
        stopped: Optional[str] = None
        for stage_id in order if order is not None else self.order:
            if stopped is None and self.mode == "fallback" and records and current <= target_tokens:
                stopped = "target reached"
            if stopped is not None:
                results.append(StageResult(stage_id, "noop", stopped))
                continue
            stage = self.stages.get(stage_id)
            if stage is None:
                results.append(StageResult(stage_id, "unavailable", "stage not in preset"))
                continue
            if stage.reasons is not None and context.reason not in stage.reasons:
                results.append(StageResult(stage_id, "noop", f"reason {context.reason!r} not in when.reasons"))
                continue
            if stage.trigger is not None and context.reason == "threshold":
                data = TriggerInput(
                    OccupancyInput(context.projected(), normalize_usage(context.last_usage), True),
                    context.context_window, context.max_output_tokens,
                )
                if not stage.trigger.evaluate(data, context.messages).fires:
                    results.append(StageResult(stage_id, "noop", "stage trigger did not fire"))
                    continue
            try:
                output = stage.step.run(context)
                context.state.validate(output.record, context.messages)
            except CompactionCancelled:
                raise
            except MethodUnavailable as exc:
                results.append(StageResult(stage_id, "unavailable", str(exc) or None))
                continue
            except Exception as exc:
                results.append(StageResult(stage_id, "failed", f"{type(exc).__name__}: {exc}"))
                if self.mode != "fallback":
                    stopped = f"stopped after {stage_id} failed"
                continue
            probe = CompactionState([*context.state.records, output.record])
            after = self.count(probe.project(context.messages, context.target))
            if stage.accept == "progress" and after >= current:
                results.append(StageResult(stage_id, "noop", "no progress", after))
                continue
            record = replace(output.record, tokens_after=after)
            context.state.append(record, context.messages)
            records.append(record)
            current = after
            status: StageStatus = "committed" if record.is_boundary else "edited"
            results.append(StageResult(stage_id, status, stage.commit_detail, after, record.record_id, output.selection))
            if status == "committed" and self.mode == "until_boundary":
                stopped = f"stopped after {stage_id} committed"
        return CompactionOutcome(
            records=tuple(records),
            stages=tuple(results),
            tokens_before=context.tokens_before,
            tokens_after=current,
            target_tokens=target_tokens,
            reached_target=bool(records) and current <= target_tokens,
        )


def build_stage(params: Params, algorithms: Mapping[str, Callable[[], CompactionMethod]]) -> Stage:
    stage_id = params.str("id")
    when = params.mapping("when", None)
    reasons: Optional[FrozenSet[str]] = None
    if when is not None:
        when_params = params.child(when, "when")
        raw_reasons = when_params.list("reasons")
        bad = [r for r in raw_reasons if r not in REASONS]
        if bad:
            raise ValueError(f"{when_params.where}.reasons has unknown reasons {bad}; expected {list(REASONS)}")
        reasons = frozenset(raw_reasons)
        when_params.done()
    raw_trigger = params.mapping("trigger", None)
    trigger = None if raw_trigger is None else build_trigger(params.child(raw_trigger, "trigger"))
    accept = params.choice("accept", ("progress", "any"), "progress")
    commit_detail = params.str("commit_detail", None)
    algorithm = params.str("algorithm", None)
    if algorithm is not None:
        if algorithm not in algorithms:
            raise ValueError(f"{params.where}.algorithm {algorithm!r} is unknown; expected one of {sorted(algorithms)}")
        params.done()
        return Stage(stage_id, AlgorithmStep(algorithms[algorithm]()), reasons, accept, trigger, commit_detail)
    selector = build_selector(params.child(params.mapping("select"), "select"))
    reducer = build_reducer(params.child(params.mapping("reduce"), "reduce"))
    raw_place = params.mapping("place", None)
    placement = None if raw_place is None else build_placement(params.child(raw_place, "place"))
    params.done()
    return Stage(stage_id, ComposedStep(stage_id, selector, reducer, placement), reasons, accept, trigger, commit_detail)
