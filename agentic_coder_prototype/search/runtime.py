from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .assessment import SearchAssessmentRegistry
from .compaction import SearchCompactionRegistry
from .schema import (
    SearchAssessment,
    SearchCandidate,
    SearchCarryState,
    SearchEvent,
    SearchFrontier,
    SearchMessage,
    SearchMetrics,
    SearchRun,
)


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class BarrieredSchedulerConfig:
    search_id: str
    max_rounds: int
    population_size: int
    subset_size: int
    random_seed: int = 0
    recipe_kind: str = "rsa"
    scheduler_id: str = "barriered_round_scheduler.v1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.search_id or "").strip():
            raise ValueError("search_id must be non-empty")
        max_rounds = int(self.max_rounds)
        population_size = int(self.population_size)
        subset_size = int(self.subset_size)
        random_seed = int(self.random_seed)
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        if population_size <= 0:
            raise ValueError("population_size must be positive")
        if subset_size <= 0:
            raise ValueError("subset_size must be positive")
        if subset_size > population_size:
            raise ValueError("subset_size may not exceed population_size")
        object.__setattr__(self, "max_rounds", max_rounds)
        object.__setattr__(self, "population_size", population_size)
        object.__setattr__(self, "subset_size", subset_size)
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "search_id", str(self.search_id).strip())
        object.__setattr__(self, "recipe_kind", str(self.recipe_kind).strip() or "rsa")
        object.__setattr__(self, "scheduler_id", str(self.scheduler_id).strip() or "barriered_round_scheduler.v1")
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class AggregationProposal:
    candidate: SearchCandidate
    message: Optional[SearchMessage] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


AggregationFn = Callable[[Sequence[SearchCandidate], int, int], AggregationProposal]
SelectionFn = Callable[[Sequence[SearchCandidate]], SearchCandidate]
MessagePassingFn = Callable[[Sequence[SearchCandidate], Optional[SearchCarryState], int, int], AggregationProposal]
SynthesisFn = Callable[[Sequence[SearchCandidate], SearchCarryState, int], SearchCandidate]


def _default_selector(candidates: Sequence[SearchCandidate]) -> SearchCandidate:
    return max(
        candidates,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            -int(item.round_index),
            item.candidate_id,
        ),
    )


@dataclass(frozen=True)
class AssessmentGateConfig:
    backend_kind: str
    mode: str
    max_assessments: int
    required_verdicts: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        backend_kind = str(self.backend_kind or "").strip()
        mode = str(self.mode or "").strip().lower()
        max_assessments = int(self.max_assessments)
        if not backend_kind:
            raise ValueError("backend_kind must be non-empty")
        if mode not in {"require_before_select", "prune_on_verdict", "terminate_on_verdict"}:
            raise ValueError(
                "mode must be one of: ['prune_on_verdict', 'require_before_select', 'terminate_on_verdict']"
            )
        if max_assessments <= 0:
            raise ValueError("max_assessments must be positive")
        object.__setattr__(self, "backend_kind", backend_kind)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "max_assessments", max_assessments)
        object.__setattr__(
            self,
            "required_verdicts",
            [str(item or "").strip().lower() for item in self.required_verdicts if str(item or "").strip()],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class AssessmentGateOutcome:
    assessments: List[SearchAssessment]
    pruned_candidate_ids: List[str]
    selected_candidate_id: Optional[str]
    terminated: bool
    gate_event: SearchEvent
    selection_event: Optional[SearchEvent] = None


def _sample_subsets(
    population: Sequence[SearchCandidate],
    *,
    subset_size: int,
    sample_count: int,
    rng: Random,
) -> List[List[SearchCandidate]]:
    indexed = list(population)
    subsets: List[List[SearchCandidate]] = []
    for _ in range(sample_count):
        if subset_size == len(indexed):
            subset = list(indexed)
        else:
            subset = rng.sample(indexed, subset_size)
        subsets.append(subset)
    return subsets


def _compute_metrics(
    initial_frontier: SearchFrontier,
    final_frontier: SearchFrontier,
    candidates: Sequence[SearchCandidate],
) -> SearchMetrics:
    initial_candidates = [item for item in candidates if item.frontier_id == initial_frontier.frontier_id]
    final_candidates = [item for item in candidates if item.frontier_id == final_frontier.frontier_id]
    initial_scores = [float(item.score_vector.get("correctness_score", 0.0)) for item in initial_candidates]
    final_scores = [float(item.score_vector.get("correctness_score", 0.0)) for item in final_candidates]
    initial_avg = sum(initial_scores) / len(initial_scores) if initial_scores else 0.0
    final_avg = sum(final_scores) / len(final_scores) if final_scores else 0.0

    initial_payloads = {item.payload_ref for item in initial_candidates}
    final_payloads = {item.payload_ref for item in final_candidates}
    initial_diversity = len(initial_payloads) or 1
    final_diversity = len(final_payloads)
    diversity_decay = max(0.0, 1.0 - (float(final_diversity) / float(initial_diversity)))

    derived_candidates = [item for item in candidates if item.parent_ids]
    mixed = [item for item in derived_candidates if len(item.parent_ids) > 1]
    mixing_rate = float(len(mixed)) / float(len(derived_candidates) or 1)

    return SearchMetrics(
        aggregability_gap=max(0.0, final_avg - initial_avg),
        diversity_decay=diversity_decay,
        mixing_rate=mixing_rate,
        metadata={
            "initial_average_score": initial_avg,
            "final_average_score": final_avg,
            "initial_diversity": initial_diversity,
            "final_diversity": final_diversity,
        },
    )


def run_barriered_assessment_gate(
    *,
    run: SearchRun,
    registry: SearchAssessmentRegistry,
    config: AssessmentGateConfig,
    frontier_candidates: Sequence[SearchCandidate],
) -> AssessmentGateOutcome:
    if len(frontier_candidates) > config.max_assessments:
        raise ValueError("frontier_candidates exceeds max_assessments budget")
    if not frontier_candidates:
        raise ValueError("frontier_candidates must contain at least one candidate")

    backend = registry.get_backend(config.backend_kind)
    assessments: List[SearchAssessment] = []
    if backend.subject_arity == "pair":
        if len(frontier_candidates) != 2:
            raise ValueError("pair assessment gates require exactly two frontier candidates")
        assessments.append(
            registry.assess(
                backend_kind=config.backend_kind,
                assessment_id=f"{run.search_id}.assessment.{config.mode}.1",
                search_id=run.search_id,
                frontier_id=frontier_candidates[0].frontier_id,
                round_index=frontier_candidates[0].round_index,
                candidates=list(frontier_candidates),
                metadata={"gate_mode": config.mode, **dict(config.metadata)},
            )
        )
    else:
        for index, candidate in enumerate(frontier_candidates, start=1):
            assessments.append(
                registry.assess(
                    backend_kind=config.backend_kind,
                    assessment_id=f"{run.search_id}.assessment.{config.mode}.{index}",
                    search_id=run.search_id,
                    frontier_id=candidate.frontier_id,
                    round_index=candidate.round_index,
                    candidates=[candidate],
                    metadata={"gate_mode": config.mode, **dict(config.metadata)},
                )
            )

    pruned_candidate_ids: List[str] = []
    selected_candidate_id: Optional[str] = None
    terminated = False

    if config.mode == "require_before_select":
        if backend.subject_arity == "pair":
            assessment = assessments[0]
            if config.required_verdicts and assessment.verdict not in config.required_verdicts:
                passing = []
            else:
                preferred_candidate_id = assessment.summary_payload.get("preferred_candidate_id")
                passing = [preferred_candidate_id] if preferred_candidate_id else []
        else:
            passing = [
                item.subject_candidate_ids[0]
                for item in assessments
                if not config.required_verdicts or item.verdict in config.required_verdicts
            ]
        if not passing:
            terminated = True
        else:
            selected_candidate_id = max(
                [item for item in frontier_candidates if item.candidate_id in passing],
                key=lambda candidate: (
                    float(candidate.score_vector.get("correctness_score", 0.0)),
                    -int(candidate.round_index),
                    candidate.candidate_id,
                ),
            ).candidate_id
    elif config.mode == "prune_on_verdict":
        if backend.subject_arity == "pair":
            assessment = assessments[0]
            if config.required_verdicts and assessment.verdict in config.required_verdicts:
                preferred_candidate_id = assessment.summary_payload.get("preferred_candidate_id")
                pruned_candidate_ids = [
                    item.candidate_id for item in frontier_candidates if item.candidate_id != preferred_candidate_id
                ]
        else:
            pruned_candidate_ids = [
                item.subject_candidate_ids[0]
                for item in assessments
                if config.required_verdicts and item.verdict in config.required_verdicts
            ]
        survivors = [item for item in frontier_candidates if item.candidate_id not in pruned_candidate_ids]
        selected_candidate_id = survivors[0].candidate_id if survivors else None
        terminated = not survivors
    elif config.mode == "terminate_on_verdict":
        terminated = any(
            not config.required_verdicts or item.verdict in config.required_verdicts
            for item in assessments
        )
        if not terminated:
            selected_candidate_id = frontier_candidates[0].candidate_id

    gate_event = SearchEvent(
        event_id=f"{run.search_id}.event.gate.{config.mode}",
        search_id=run.search_id,
        frontier_id=frontier_candidates[0].frontier_id,
        round_index=frontier_candidates[0].round_index,
        operator_kind="verify",
        input_candidate_ids=[item.candidate_id for item in frontier_candidates],
        output_candidate_ids=[item.candidate_id for item in frontier_candidates if item.candidate_id not in pruned_candidate_ids],
        assessment_ids=[item.assessment_id for item in assessments],
        metadata={
            "gate_mode": config.mode,
            "backend_kind": config.backend_kind,
            "max_assessments": config.max_assessments,
            "terminated": terminated,
            "pruned_candidate_ids": list(pruned_candidate_ids),
            **dict(config.metadata),
        },
    )

    selection_event: Optional[SearchEvent] = None
    if selected_candidate_id is not None:
        selection_event = SearchEvent(
            event_id=f"{run.search_id}.event.select.{config.mode}",
            search_id=run.search_id,
            frontier_id=frontier_candidates[0].frontier_id,
            round_index=frontier_candidates[0].round_index,
            operator_kind="select",
            input_candidate_ids=[item.candidate_id for item in frontier_candidates if item.candidate_id not in pruned_candidate_ids],
            output_candidate_ids=[selected_candidate_id],
            assessment_ids=[item.assessment_id for item in assessments],
            metadata={"gate_mode": config.mode, "backend_kind": config.backend_kind},
        )

    return AssessmentGateOutcome(
        assessments=assessments,
        pruned_candidate_ids=pruned_candidate_ids,
        selected_candidate_id=selected_candidate_id,
        terminated=terminated,
        gate_event=gate_event,
        selection_event=selection_event,
    )


class BarrieredRoundScheduler:
    def __init__(self, config: BarrieredSchedulerConfig) -> None:
        self.config = config

    def run(
        self,
        *,
        initial_candidates: Sequence[SearchCandidate],
        aggregate_fn: AggregationFn,
        selector: Optional[SelectionFn] = None,
    ) -> SearchRun:
        if not initial_candidates:
            raise ValueError("initial_candidates must contain at least one candidate")

        selector = selector or _default_selector
        rng = Random(self.config.random_seed)

        candidates: List[SearchCandidate] = list(initial_candidates)
        messages: List[SearchMessage] = []
        events: List[SearchEvent] = []

        initial_frontier = SearchFrontier(
            frontier_id=f"{self.config.search_id}.frontier.0",
            search_id=self.config.search_id,
            round_index=0,
            candidate_ids=[item.candidate_id for item in initial_candidates],
            status="active",
            metadata={"scheduler_id": self.config.scheduler_id, "seeded": True},
        )
        frontiers: List[SearchFrontier] = [initial_frontier]

        current_population = list(initial_candidates)
        for round_index in range(1, self.config.max_rounds + 1):
            subsets = _sample_subsets(
                current_population,
                subset_size=self.config.subset_size,
                sample_count=self.config.population_size,
                rng=rng,
            )
            next_candidates: List[SearchCandidate] = []
            next_messages: List[SearchMessage] = []
            event_input_ids: List[str] = []
            event_output_ids: List[str] = []
            event_message_ids: List[str] = []

            for proposal_index, subset in enumerate(subsets, start=1):
                proposal = aggregate_fn(subset, round_index, proposal_index)
                next_candidates.append(proposal.candidate)
                event_input_ids.extend(item.candidate_id for item in subset)
                event_output_ids.append(proposal.candidate.candidate_id)
                if proposal.message is not None:
                    next_messages.append(proposal.message)
                    event_message_ids.append(proposal.message.message_id)

            frontier_id = f"{self.config.search_id}.frontier.{round_index}"
            frontier = SearchFrontier(
                frontier_id=frontier_id,
                search_id=self.config.search_id,
                round_index=round_index,
                candidate_ids=[item.candidate_id for item in next_candidates],
                status="active" if round_index < self.config.max_rounds else "completed",
                metadata={"scheduler_id": self.config.scheduler_id, "population_size": len(next_candidates)},
            )
            event = SearchEvent(
                event_id=f"{self.config.search_id}.event.aggregate.{round_index}",
                search_id=self.config.search_id,
                frontier_id=frontier.frontier_id,
                round_index=round_index,
                operator_kind="aggregate",
                input_candidate_ids=event_input_ids,
                output_candidate_ids=event_output_ids,
                message_ids=event_message_ids,
                metadata={"scheduler_id": self.config.scheduler_id, "sample_count": len(subsets)},
            )

            candidates.extend(next_candidates)
            messages.extend(next_messages)
            frontiers.append(frontier)
            events.append(event)
            current_population = next_candidates

        selected = selector(current_population)
        selection_event = SearchEvent(
            event_id=f"{self.config.search_id}.event.select.final",
            search_id=self.config.search_id,
            frontier_id=frontiers[-1].frontier_id,
            round_index=frontiers[-1].round_index,
            operator_kind="select",
            input_candidate_ids=[item.candidate_id for item in current_population],
            output_candidate_ids=[selected.candidate_id],
            metadata={"scheduler_id": self.config.scheduler_id, "selector": getattr(selector, "__name__", "custom")},
        )
        events.append(selection_event)

        metrics = _compute_metrics(initial_frontier, frontiers[-1], candidates)
        return SearchRun(
            search_id=self.config.search_id,
            recipe_kind=self.config.recipe_kind,
            candidates=candidates,
            frontiers=frontiers,
            events=events,
            messages=messages,
            metrics=metrics,
            selected_candidate_id=selected.candidate_id,
            metadata={
                "scheduler_id": self.config.scheduler_id,
                "random_seed": self.config.random_seed,
                "max_rounds": self.config.max_rounds,
                "population_size": self.config.population_size,
                "subset_size": self.config.subset_size,
                **dict(self.config.metadata),
            },
        )


@dataclass(frozen=True)
class MessagePassingSchedulerConfig:
    search_id: str
    max_rounds: int
    population_size: int
    subset_size: int
    compaction_backend_kind: str
    random_seed: int = 0
    max_active_carry_states: int = 1
    recipe_kind: str = "pacore_message_passing"
    scheduler_id: str = "bounded_message_passing_scheduler.v1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.search_id or "").strip():
            raise ValueError("search_id must be non-empty")
        max_rounds = int(self.max_rounds)
        population_size = int(self.population_size)
        subset_size = int(self.subset_size)
        random_seed = int(self.random_seed)
        max_active_carry_states = int(self.max_active_carry_states)
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        if population_size <= 0:
            raise ValueError("population_size must be positive")
        if subset_size <= 0:
            raise ValueError("subset_size must be positive")
        if subset_size > population_size:
            raise ValueError("subset_size may not exceed population_size")
        if max_active_carry_states <= 0:
            raise ValueError("max_active_carry_states must be positive")
        object.__setattr__(self, "search_id", str(self.search_id).strip())
        object.__setattr__(self, "max_rounds", max_rounds)
        object.__setattr__(self, "population_size", population_size)
        object.__setattr__(self, "subset_size", subset_size)
        object.__setattr__(self, "compaction_backend_kind", str(self.compaction_backend_kind).strip())
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "max_active_carry_states", max_active_carry_states)
        object.__setattr__(self, "recipe_kind", str(self.recipe_kind).strip() or "pacore_message_passing")
        object.__setattr__(
            self,
            "scheduler_id",
            str(self.scheduler_id).strip() or "bounded_message_passing_scheduler.v1",
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


def _default_synthesis(
    population: Sequence[SearchCandidate],
    carry_state: SearchCarryState,
    round_index: int,
) -> SearchCandidate:
    ordered = sorted(
        population,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            -int(item.round_index),
            item.candidate_id,
        ),
        reverse=True,
    )
    parents = ordered[:2] if len(ordered) > 1 else ordered
    synthesized_score = max(float(item.score_vector.get("correctness_score", 0.0)) for item in parents) + 0.03
    best_parent = parents[0]
    return SearchCandidate(
        candidate_id=f"{best_parent.search_id}.cand.synth.final",
        search_id=best_parent.search_id,
        frontier_id=f"{best_parent.search_id}.frontier.synth",
        parent_ids=[item.candidate_id for item in parents],
        round_index=round_index,
        depth=max(item.depth for item in parents) + 1,
        payload_ref=f"artifacts/search/{best_parent.search_id}/synth_final.json",
        message_ref=carry_state.message_ids[0],
        score_vector={"correctness_score": min(1.0, synthesized_score)},
        usage={"prompt_tokens": 44, "completion_tokens": 23},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{best_parent.search_id}/synth_final.md",
        metadata={"recipe": "pacore", "synthesis": True, "carry_state_id": carry_state.state_id},
    )


class BoundedMessagePassingScheduler:
    def __init__(self, config: MessagePassingSchedulerConfig, compaction_registry: SearchCompactionRegistry) -> None:
        self.config = config
        self.compaction_registry = compaction_registry

    def run(
        self,
        *,
        initial_candidates: Sequence[SearchCandidate],
        message_fn: MessagePassingFn,
        synthesis_fn: Optional[SynthesisFn] = None,
    ) -> SearchRun:
        if not initial_candidates:
            raise ValueError("initial_candidates must contain at least one candidate")

        synthesis_fn = synthesis_fn or _default_synthesis
        rng = Random(self.config.random_seed)
        candidates: List[SearchCandidate] = list(initial_candidates)
        messages: List[SearchMessage] = []
        carry_states: List[SearchCarryState] = []
        events: List[SearchEvent] = []

        initial_frontier = SearchFrontier(
            frontier_id=f"{self.config.search_id}.frontier.0",
            search_id=self.config.search_id,
            round_index=0,
            candidate_ids=[item.candidate_id for item in initial_candidates],
            status="active",
            metadata={"scheduler_id": self.config.scheduler_id, "seeded": True},
        )
        frontiers: List[SearchFrontier] = [initial_frontier]

        current_population = list(initial_candidates)
        active_carry_state: Optional[SearchCarryState] = None
        for round_index in range(1, self.config.max_rounds + 1):
            subsets = _sample_subsets(
                current_population,
                subset_size=self.config.subset_size,
                sample_count=self.config.population_size,
                rng=rng,
            )
            next_candidates: List[SearchCandidate] = []
            aggregate_input_ids: List[str] = []
            aggregate_output_ids: List[str] = []

            for proposal_index, subset in enumerate(subsets, start=1):
                proposal = message_fn(subset, active_carry_state, round_index, proposal_index)
                next_candidates.append(proposal.candidate)
                aggregate_input_ids.extend(item.candidate_id for item in subset)
                aggregate_output_ids.append(proposal.candidate.candidate_id)

            frontier_id = f"{self.config.search_id}.frontier.{round_index}"
            frontier = SearchFrontier(
                frontier_id=frontier_id,
                search_id=self.config.search_id,
                round_index=round_index,
                candidate_ids=[item.candidate_id for item in next_candidates],
                status="active",
                metadata={"scheduler_id": self.config.scheduler_id, "population_size": len(next_candidates)},
            )
            aggregate_event = SearchEvent(
                event_id=f"{self.config.search_id}.event.aggregate.{round_index}",
                search_id=self.config.search_id,
                frontier_id=frontier.frontier_id,
                round_index=round_index,
                operator_kind="aggregate",
                input_candidate_ids=aggregate_input_ids,
                output_candidate_ids=aggregate_output_ids,
                metadata={
                    "scheduler_id": self.config.scheduler_id,
                    "carry_state_id": active_carry_state.state_id if active_carry_state else None,
                },
            )
            message, carry_state = self.compaction_registry.compact(
                backend_kind=self.config.compaction_backend_kind,
                search_id=self.config.search_id,
                carry_state_id=f"{self.config.search_id}.carry.{round_index}",
                message_id=f"{self.config.search_id}.msg.compact.{round_index}",
                candidates=next_candidates,
                metadata={"round_index": round_index, "scheduler_id": self.config.scheduler_id},
            )
            compact_event = SearchEvent(
                event_id=f"{self.config.search_id}.event.compact.{round_index}",
                search_id=self.config.search_id,
                frontier_id=frontier.frontier_id,
                round_index=round_index,
                operator_kind="compact",
                input_candidate_ids=[item.candidate_id for item in next_candidates],
                message_ids=[message.message_id],
                metadata={
                    "scheduler_id": self.config.scheduler_id,
                    "carry_state_id": carry_state.state_id,
                    "max_active_carry_states": self.config.max_active_carry_states,
                },
            )

            candidates.extend(next_candidates)
            messages.append(message)
            carry_states.append(carry_state)
            frontiers.append(frontier)
            events.extend([aggregate_event, compact_event])
            current_population = next_candidates
            active_carry_state = carry_states[-self.config.max_active_carry_states]

        assert active_carry_state is not None
        synthesis_round_index = self.config.max_rounds + 1
        synthesized_candidate = synthesis_fn(current_population, active_carry_state, synthesis_round_index)
        synth_frontier = SearchFrontier(
            frontier_id=f"{self.config.search_id}.frontier.synth",
            search_id=self.config.search_id,
            round_index=synthesis_round_index,
            candidate_ids=[synthesized_candidate.candidate_id],
            status="completed",
            metadata={"scheduler_id": self.config.scheduler_id, "synthesis": True},
        )
        synth_event = SearchEvent(
            event_id=f"{self.config.search_id}.event.aggregate.synth",
            search_id=self.config.search_id,
            frontier_id=synth_frontier.frontier_id,
            round_index=synthesis_round_index,
            operator_kind="aggregate",
            input_candidate_ids=[item.candidate_id for item in current_population],
            output_candidate_ids=[synthesized_candidate.candidate_id],
            message_ids=[active_carry_state.message_ids[0]],
            metadata={"scheduler_id": self.config.scheduler_id, "synthesis": True},
        )
        select_event = SearchEvent(
            event_id=f"{self.config.search_id}.event.select.final",
            search_id=self.config.search_id,
            frontier_id=synth_frontier.frontier_id,
            round_index=synthesis_round_index,
            operator_kind="select",
            input_candidate_ids=[synthesized_candidate.candidate_id],
            output_candidate_ids=[synthesized_candidate.candidate_id],
            metadata={"scheduler_id": self.config.scheduler_id, "selector": "synthesis_path"},
        )

        candidates.append(synthesized_candidate)
        frontiers.append(synth_frontier)
        events.extend([synth_event, select_event])
        metrics = _compute_metrics(initial_frontier, synth_frontier, candidates)
        return SearchRun(
            search_id=self.config.search_id,
            recipe_kind=self.config.recipe_kind,
            candidates=candidates,
            frontiers=frontiers,
            events=events,
            messages=messages,
            carry_states=carry_states,
            metrics=metrics,
            selected_candidate_id=synthesized_candidate.candidate_id,
            metadata={
                "scheduler_id": self.config.scheduler_id,
                "random_seed": self.config.random_seed,
                "max_rounds": self.config.max_rounds,
                "population_size": self.config.population_size,
                "subset_size": self.config.subset_size,
                "compaction_backend_kind": self.config.compaction_backend_kind,
                "max_active_carry_states": self.config.max_active_carry_states,
                **dict(self.config.metadata),
            },
        )
