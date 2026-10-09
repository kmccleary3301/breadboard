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
            reset_context=selection.reset_context,
            coalesce_user=bool(getattr(self.placement, "coalesce_user", False)),
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
    severity: Optional[str] = None
    failure_next_on: Optional[str] = None
    failure_kinds: Tuple[str, ...] = ()


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
        current = self.count(context.state.project(context.messages, context.target, coalesce=False))
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
            if stage.severity is not None and context.severity != stage.severity:
                results.append(StageResult(stage_id, "noop", f"severity {context.severity!r} not in when.severity"))
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
                permitted = (
                    stage.failure_next_on == context.severity
                    and type(exc).__name__ in stage.failure_kinds
                )
                if self.mode != "fallback" and not permitted:
                    stopped = f"stopped after {stage_id} failed"
                continue
            probe = CompactionState([*context.state.records, output.record])
            after = self.count(probe.project(context.messages, context.target, coalesce=False))
            if stage.accept == "progress" and after >= current:
                results.append(StageResult(stage_id, "noop", "no progress", after))
                continue
            record = replace(output.record, tokens_after=after)
            context.state.append(record, context.messages)
            records.append(record)
            current = after
            status: StageStatus = "committed" if record.is_boundary else "edited"
            results.append(StageResult(stage_id, status, None, after, record.record_id))
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
    severity = None
    if when is not None:
        when_params = params.child(when, "when")
        raw_reasons = when_params.list("reasons", REASONS)
        bad = [r for r in raw_reasons if r not in REASONS]
        if bad:
            raise ValueError(f"{when_params.where}.reasons has unknown reasons {bad}; expected {list(REASONS)}")
        reasons = frozenset(raw_reasons)
        severity = when_params.choice("severity", ("soft", "hard", None), None)
        when_params.done()
    accept = params.choice("accept", ("progress", "any"), "progress")
    failure = params.mapping("on_failure", None)
    failure_next_on = None
    failure_kinds = ()
    if failure is not None:
        policy = params.child(failure, "on_failure")
        failure_next_on = policy.choice("next_on_severity", ("soft", "hard"))
        failure_kinds = tuple(policy.list("kinds"))
        if not all(isinstance(kind, str) for kind in failure_kinds):
            raise ValueError(f"{policy.where}.kinds must contain exception names")
        policy.done()
    algorithm = params.str("algorithm", None)
    if algorithm is not None:
        if algorithm not in algorithms:
            raise ValueError(f"{params.where}.algorithm {algorithm!r} is unknown; expected one of {sorted(algorithms)}")
        params.done()
        return Stage(stage_id, AlgorithmStep(algorithms[algorithm]()), reasons, accept, severity, failure_next_on, failure_kinds)
    selector = build_selector(params.child(params.mapping("select"), "select"))
    reducer = build_reducer(params.child(params.mapping("reduce"), "reduce"))
    raw_place = params.mapping("place", None)
    placement = None if raw_place is None else build_placement(params.child(raw_place, "place"))
    params.done()
    return Stage(stage_id, ComposedStep(stage_id, selector, reducer, placement), reasons, accept, severity, failure_next_on, failure_kinds)
