from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .schema import SearchCandidate, SearchEvent, SearchFrontier, SearchMessage, SearchMetrics, SearchRun


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


def _default_selector(candidates: Sequence[SearchCandidate]) -> SearchCandidate:
    return max(
        candidates,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            -int(item.round_index),
            item.candidate_id,
        ),
    )


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
