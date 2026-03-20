from __future__ import annotations

from typing import Dict, List, Sequence

from .compaction import SearchCompactionRegistry, build_default_search_compaction_registry
from .runtime import AggregationProposal, BarrieredRoundScheduler, BarrieredSchedulerConfig
from .schema import SearchCandidate, SearchEvent, SearchMessage, SearchRun


def _seed_candidates(search_id: str) -> List[SearchCandidate]:
    return [
        SearchCandidate(
            candidate_id=f"{search_id}.cand.seed.{index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            parent_ids=[],
            round_index=0,
            depth=0,
            payload_ref=f"artifacts/search/{search_id}/seed_{index}.json",
            score_vector={"correctness_score": score},
            usage={"prompt_tokens": 32 + (index * 3), "completion_tokens": 18 + index},
            status="seeded",
            reasoning_summary_ref=f"artifacts/search/{search_id}/seed_{index}_summary.md",
            metadata={"seed_index": index},
        )
        for index, score in enumerate((0.41, 0.58, 0.52, 0.47), start=1)
    ]


def build_rsa_search_runtime_example() -> Dict[str, object]:
    search_id = "search.rsa_mvp.v1"
    config = BarrieredSchedulerConfig(
        search_id=search_id,
        max_rounds=2,
        population_size=4,
        subset_size=2,
        random_seed=7,
        recipe_kind="rsa_population_recombination",
        metadata={"phase": "dag_v1", "non_kernel": True, "scheduler_mode": "barriered"},
    )
    seeds = _seed_candidates(search_id)

    def _aggregate(subset: Sequence[SearchCandidate], round_index: int, proposal_index: int) -> AggregationProposal:
        avg_score = sum(float(item.score_vector.get("correctness_score", 0.0)) for item in subset) / float(len(subset))
        improved_score = min(1.0, avg_score + 0.08)
        candidate = SearchCandidate(
            candidate_id=f"{search_id}.cand.r{round_index}.{proposal_index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.{round_index}",
            parent_ids=[item.candidate_id for item in subset],
            round_index=round_index,
            depth=round_index,
            payload_ref=f"artifacts/search/{search_id}/round_{round_index}_candidate_{proposal_index}.json",
            message_ref=f"{search_id}.msg.r{round_index}.{proposal_index}",
            score_vector={"correctness_score": improved_score},
            usage={
                "prompt_tokens": 48 + (round_index * 8),
                "completion_tokens": 24 + proposal_index,
            },
            status="active",
            reasoning_summary_ref=f"artifacts/search/{search_id}/round_{round_index}_candidate_{proposal_index}.md",
            metadata={"recipe": "rsa", "proposal_index": proposal_index},
        )
        message = SearchMessage(
            message_id=f"{search_id}.msg.r{round_index}.{proposal_index}",
            schema_kind="candidate_summary.v1",
            source_candidate_ids=[item.candidate_id for item in subset],
            summary_payload={
                "summary": f"Aggregated proposal {proposal_index} for round {round_index}",
                "avg_parent_score": round(avg_score, 4),
                "improved_score": round(improved_score, 4),
            },
            dropped_fields=["full_trace"],
            omitted_artifact_refs=[item.payload_ref for item in subset],
            confidence=0.74,
            unresolved_gaps=["verifier_not_run"],
            usage={"compaction_tokens": 12},
            metadata={"round_index": round_index},
        )
        return AggregationProposal(candidate=candidate, message=message)

    run = BarrieredRoundScheduler(config).run(
        initial_candidates=seeds,
        aggregate_fn=_aggregate,
    )
    assert isinstance(run, SearchRun)
    return {
        "config": config,
        "seeds": seeds,
        "run": run,
    }


def build_rsa_search_runtime_example_payload() -> Dict[str, object]:
    example = build_rsa_search_runtime_example()
    return {
        "config": {
            "search_id": example["config"].search_id,
            "max_rounds": example["config"].max_rounds,
            "population_size": example["config"].population_size,
            "subset_size": example["config"].subset_size,
            "random_seed": example["config"].random_seed,
            "recipe_kind": example["config"].recipe_kind,
            "scheduler_id": example["config"].scheduler_id,
            "metadata": dict(example["config"].metadata),
        },
        "seeds": [item.to_dict() for item in example["seeds"]],
        "run": example["run"].to_dict(),
    }


def build_typed_compaction_registry_example() -> Dict[str, object]:
    example = build_rsa_search_runtime_example()
    base_run = example["run"]
    final_frontier = base_run.frontiers[-1]
    final_candidates = [
        item for item in base_run.candidates if item.frontier_id == final_frontier.frontier_id
    ]
    registry: SearchCompactionRegistry = build_default_search_compaction_registry()
    message, carry_state = registry.compact(
        backend_kind="bounded_candidate_rollup.v1",
        search_id=base_run.search_id,
        carry_state_id=f"{base_run.search_id}.carry.final",
        message_id=f"{base_run.search_id}.msg.compact.final",
        candidates=final_candidates,
        metadata={"phase": "dag_v1_phase2", "frontier_id": final_frontier.frontier_id},
    )
    compaction_event = SearchEvent(
        event_id=f"{base_run.search_id}.event.compact.final",
        search_id=base_run.search_id,
        frontier_id=final_frontier.frontier_id,
        round_index=final_frontier.round_index,
        operator_kind="compact",
        input_candidate_ids=[item.candidate_id for item in final_candidates],
        message_ids=[message.message_id],
        metadata={
            "backend_kind": "bounded_candidate_rollup.v1",
            "carry_state_id": carry_state.state_id,
        },
    )
    run = SearchRun(
        search_id=base_run.search_id,
        recipe_kind=base_run.recipe_kind,
        candidates=list(base_run.candidates),
        frontiers=list(base_run.frontiers),
        events=[*base_run.events, compaction_event],
        messages=[*base_run.messages, message],
        carry_states=[carry_state],
        metrics=base_run.metrics,
        selected_candidate_id=base_run.selected_candidate_id,
        metadata={**dict(base_run.metadata), "compaction_backend_kind": "bounded_candidate_rollup.v1"},
    )
    return {
        "config": example["config"],
        "seeds": example["seeds"],
        "base_run": base_run,
        "registry_backend_kinds": registry.list_backend_kinds(),
        "run": run,
        "message": message,
        "carry_state": carry_state,
    }


def build_typed_compaction_registry_example_payload() -> Dict[str, object]:
    example = build_typed_compaction_registry_example()
    return {
        "config": {
            "search_id": example["config"].search_id,
            "max_rounds": example["config"].max_rounds,
            "population_size": example["config"].population_size,
            "subset_size": example["config"].subset_size,
            "random_seed": example["config"].random_seed,
            "recipe_kind": example["config"].recipe_kind,
            "scheduler_id": example["config"].scheduler_id,
            "metadata": dict(example["config"].metadata),
        },
        "registry_backend_kinds": list(example["registry_backend_kinds"]),
        "run": example["run"].to_dict(),
        "message": example["message"].to_dict(),
        "carry_state": example["carry_state"].to_dict(),
    }
