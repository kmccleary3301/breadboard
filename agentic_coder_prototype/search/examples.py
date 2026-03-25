from __future__ import annotations

from typing import Dict, List, Sequence

from agentic_coder_prototype.optimize import (
    BenchmarkRunManifest,
    BenchmarkSplit,
    CandidateComparisonResult,
    ObjectiveBreakdownResult,
    PromotionEvidenceSummary,
    TransferCohortManifest,
    TransferSliceManifest,
    build_paired_candidate_comparison,
)

from .assessment import SearchAssessmentRegistry, build_default_search_assessment_registry
from .compaction import SearchCompactionRegistry, build_default_search_compaction_registry
from .export import build_search_offline_dataset, export_search_trajectory
from .runtime import (
    AggregationProposal,
    AssessmentGateConfig,
    BarrieredRoundScheduler,
    BarrieredSchedulerConfig,
    BoundedMessagePassingScheduler,
    MessagePassingSchedulerConfig,
    run_barriered_assessment_gate,
)
from .schema import (
    SearchBranchState,
    SearchAssessment,
    SearchCandidate,
    SearchEvent,
    SearchMessage,
    SearchRun,
    SearchWorkspaceSnapshot,
)


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


def build_pacore_search_runtime_example() -> Dict[str, object]:
    search_id = "search.pacore_mvp.v1"
    config = MessagePassingSchedulerConfig(
        search_id=search_id,
        max_rounds=2,
        population_size=3,
        subset_size=2,
        compaction_backend_kind="bounded_candidate_rollup.v1",
        random_seed=13,
        recipe_kind="pacore_message_passing",
        metadata={"phase": "dag_v1_phase3", "non_kernel": True, "scheduler_mode": "barriered"},
    )
    seeds = _seed_candidates(search_id)
    registry: SearchCompactionRegistry = build_default_search_compaction_registry()

    def _message_pass(
        subset: Sequence[SearchCandidate],
        carry_state,
        round_index: int,
        proposal_index: int,
    ) -> AggregationProposal:
        avg_score = sum(float(item.score_vector.get("correctness_score", 0.0)) for item in subset) / float(len(subset))
        carry_bonus = 0.02 if carry_state is not None else 0.0
        improved_score = min(1.0, avg_score + 0.05 + carry_bonus)
        candidate = SearchCandidate(
            candidate_id=f"{search_id}.cand.msg.r{round_index}.{proposal_index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.{round_index}",
            parent_ids=[item.candidate_id for item in subset],
            round_index=round_index,
            depth=round_index,
            payload_ref=f"artifacts/search/{search_id}/round_{round_index}_candidate_{proposal_index}.json",
            message_ref=carry_state.message_ids[0] if carry_state is not None else None,
            workspace_ref=f"artifacts/search/{search_id}/workspace_round_{round_index}_{proposal_index}.json",
            score_vector={"correctness_score": improved_score},
            usage={
                "prompt_tokens": 56 + (round_index * 9),
                "completion_tokens": 27 + proposal_index,
            },
            status="active",
            reasoning_summary_ref=f"artifacts/search/{search_id}/round_{round_index}_candidate_{proposal_index}.md",
            metadata={
                "recipe": "pacore",
                "proposal_index": proposal_index,
                "carry_state_id": carry_state.state_id if carry_state is not None else None,
            },
        )
        return AggregationProposal(candidate=candidate)

    run = BoundedMessagePassingScheduler(config, registry).run(
        initial_candidates=seeds,
        message_fn=_message_pass,
    )
    assert isinstance(run, SearchRun)
    return {
        "config": config,
        "seeds": seeds,
        "registry_backend_kinds": registry.list_backend_kinds(),
        "run": run,
    }


def build_pacore_search_runtime_example_payload() -> Dict[str, object]:
    example = build_pacore_search_runtime_example()
    return {
        "config": {
            "search_id": example["config"].search_id,
            "max_rounds": example["config"].max_rounds,
            "population_size": example["config"].population_size,
            "subset_size": example["config"].subset_size,
            "compaction_backend_kind": example["config"].compaction_backend_kind,
            "random_seed": example["config"].random_seed,
            "max_active_carry_states": example["config"].max_active_carry_states,
            "recipe_kind": example["config"].recipe_kind,
            "scheduler_id": example["config"].scheduler_id,
            "metadata": dict(example["config"].metadata),
        },
        "registry_backend_kinds": list(example["registry_backend_kinds"]),
        "seeds": [item.to_dict() for item in example["seeds"]],
        "run": example["run"].to_dict(),
    }


def build_stateful_branch_search_example() -> Dict[str, object]:
    base = build_pacore_search_runtime_example()
    base_run = base["run"]
    final_population = [item for item in base_run.candidates if item.frontier_id == base_run.frontiers[-2].frontier_id]
    winning_candidate = max(
        final_population,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            -int(item.round_index),
            item.candidate_id,
        ),
    )
    discarded_candidate = min(
        final_population,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            item.round_index,
            item.candidate_id,
        ),
    )
    merged_snapshot = SearchWorkspaceSnapshot(
        snapshot_id=f"{base_run.search_id}.snapshot.branch.merge",
        search_id=base_run.search_id,
        branch_id=f"{base_run.search_id}.branch.merge",
        artifact_ref=f"artifacts/search/{base_run.search_id}/branch_merge_snapshot.json",
        parent_snapshot_id=winning_candidate.workspace_ref,
        derived_from_candidate_id=winning_candidate.candidate_id,
        metadata={"lane": "winning_branch", "action": "merge"},
    )
    discarded_snapshot = SearchWorkspaceSnapshot(
        snapshot_id=f"{base_run.search_id}.snapshot.branch.discard",
        search_id=base_run.search_id,
        branch_id=f"{base_run.search_id}.branch.discard",
        artifact_ref=f"artifacts/search/{base_run.search_id}/branch_discard_snapshot.json",
        parent_snapshot_id=discarded_candidate.workspace_ref,
        derived_from_candidate_id=discarded_candidate.candidate_id,
        metadata={"lane": "discarded_branch", "action": "discard"},
    )
    merged_branch = SearchBranchState(
        branch_id=merged_snapshot.branch_id,
        search_id=base_run.search_id,
        candidate_id=winning_candidate.candidate_id,
        snapshot_ids=[merged_snapshot.snapshot_id],
        head_snapshot_id=merged_snapshot.snapshot_id,
        status="merged",
        metadata={"merge_target": "main_branch", "review_status": "accepted"},
    )
    discarded_branch = SearchBranchState(
        branch_id=discarded_snapshot.branch_id,
        search_id=base_run.search_id,
        candidate_id=discarded_candidate.candidate_id,
        snapshot_ids=[discarded_snapshot.snapshot_id],
        head_snapshot_id=discarded_snapshot.snapshot_id,
        status="discarded",
        metadata={"discard_reason": "lower_score", "review_status": "rejected"},
    )
    execute_event = SearchEvent(
        event_id=f"{base_run.search_id}.event.execute.branch_local",
        search_id=base_run.search_id,
        frontier_id=base_run.frontiers[-2].frontier_id,
        round_index=base_run.frontiers[-2].round_index,
        operator_kind="execute",
        input_candidate_ids=[winning_candidate.candidate_id, discarded_candidate.candidate_id],
        output_candidate_ids=[winning_candidate.candidate_id, discarded_candidate.candidate_id],
        metadata={
            "branch_ids": [merged_branch.branch_id, discarded_branch.branch_id],
            "state_mode": "branch_local",
        },
    )
    merge_event = SearchEvent(
        event_id=f"{base_run.search_id}.event.merge.branch",
        search_id=base_run.search_id,
        frontier_id=base_run.frontiers[-1].frontier_id,
        round_index=base_run.frontiers[-1].round_index,
        operator_kind="merge",
        input_candidate_ids=[winning_candidate.candidate_id],
        output_candidate_ids=[base_run.selected_candidate_id],
        metadata={"branch_id": merged_branch.branch_id, "snapshot_id": merged_snapshot.snapshot_id},
    )
    discard_event = SearchEvent(
        event_id=f"{base_run.search_id}.event.discard.branch",
        search_id=base_run.search_id,
        frontier_id=base_run.frontiers[-1].frontier_id,
        round_index=base_run.frontiers[-1].round_index,
        operator_kind="discard",
        input_candidate_ids=[discarded_candidate.candidate_id],
        metadata={"branch_id": discarded_branch.branch_id, "snapshot_id": discarded_snapshot.snapshot_id},
    )
    run = SearchRun(
        search_id=base_run.search_id,
        recipe_kind="stateful_branch_local_search",
        candidates=list(base_run.candidates),
        frontiers=list(base_run.frontiers),
        events=[*base_run.events, execute_event, merge_event, discard_event],
        messages=list(base_run.messages),
        carry_states=list(base_run.carry_states),
        workspace_snapshots=[merged_snapshot, discarded_snapshot],
        branch_states=[merged_branch, discarded_branch],
        metrics=base_run.metrics,
        selected_candidate_id=base_run.selected_candidate_id,
        metadata={**dict(base_run.metadata), "stateful_branching": True},
    )
    return {
        "base_run": base_run,
        "run": run,
        "merged_branch": merged_branch,
        "discarded_branch": discarded_branch,
        "merged_snapshot": merged_snapshot,
        "discarded_snapshot": discarded_snapshot,
    }


def build_stateful_branch_search_example_payload() -> Dict[str, object]:
    example = build_stateful_branch_search_example()
    return {
        "run": example["run"].to_dict(),
        "merged_branch": example["merged_branch"].to_dict(),
        "discarded_branch": example["discarded_branch"].to_dict(),
        "merged_snapshot": example["merged_snapshot"].to_dict(),
        "discarded_snapshot": example["discarded_snapshot"].to_dict(),
    }


def build_search_trajectory_export_example() -> Dict[str, object]:
    stateful = build_stateful_branch_search_example()
    run = stateful["run"]
    trajectory = export_search_trajectory(
        run,
        metadata={"phase": "dag_v1_phase5", "non_kernel": True},
    )
    dataset = build_search_offline_dataset(
        [trajectory],
        dataset_id=f"{run.search_id}.offline_dataset.v1",
        metadata={"phase": "dag_v1_phase5", "bounded": True},
    )
    return {
        "run": run,
        "trajectory": trajectory,
        "dataset": dataset,
    }


def build_search_trajectory_export_example_payload() -> Dict[str, object]:
    example = build_search_trajectory_export_example()
    return {
        "run": example["run"].to_dict(),
        "trajectory": example["trajectory"].to_dict(),
        "dataset": example["dataset"].to_dict(),
    }


def build_verifier_guided_pressure_cell() -> Dict[str, object]:
    base = build_stateful_branch_search_example()
    run = base["run"]
    selected_candidate_id = run.selected_candidate_id
    verify_event = SearchEvent(
        event_id=f"{run.search_id}.event.verify.frontier_stub",
        search_id=run.search_id,
        frontier_id=run.frontiers[-1].frontier_id,
        round_index=run.frontiers[-1].round_index,
        operator_kind="verify",
        input_candidate_ids=[selected_candidate_id],
        output_candidate_ids=[selected_candidate_id],
        metadata={
            "backend_kind": "exact_tests.v1",
            "schema_kind": "code.test_report.v1",
            "verdict": "pass",
            "artifact_refs": [f"artifacts/search/{run.search_id}/verify_frontier_report.json"],
            "awkward_storage": ["event.metadata", "candidate.score_vector", "message.summary_payload"],
            "missing_public_shape": "assessment_evaluator_truth",
        },
    )
    summary = {
        "cell_id": "phase0.verifier_guided_code_patch_search",
        "family": "verifier_guided_code_patch_search",
        "awkwardness_kind": "assessment_evaluator_truth",
        "why_v1_is_awkward": [
            "exact verifier verdict is stored in event metadata",
            "test artifact references are not first-class runtime truth",
            "pass/fail semantics do not have a typed shared record",
        ],
    }
    return {
        "run": SearchRun(
            search_id=run.search_id,
            recipe_kind=run.recipe_kind,
            candidates=list(run.candidates),
            frontiers=list(run.frontiers),
            events=[*run.events, verify_event],
            messages=list(run.messages),
            carry_states=list(run.carry_states),
            workspace_snapshots=list(run.workspace_snapshots),
            branch_states=list(run.branch_states),
            metrics=run.metrics,
            selected_candidate_id=run.selected_candidate_id,
            metadata={**dict(run.metadata), "phase0_cell": summary["cell_id"]},
        ),
        "summary": summary,
    }


def build_judge_reducer_pressure_cell() -> Dict[str, object]:
    base = build_pacore_search_runtime_example()
    run = base["run"]
    final_candidates = [item for item in run.candidates if item.frontier_id == run.frontiers[-2].frontier_id]
    candidate_ids = [item.candidate_id for item in final_candidates[:2]]
    verify_event = SearchEvent(
        event_id=f"{run.search_id}.event.verify.judge_stub",
        search_id=run.search_id,
        frontier_id=run.frontiers[-2].frontier_id,
        round_index=run.frontiers[-2].round_index,
        operator_kind="verify",
        input_candidate_ids=candidate_ids,
        output_candidate_ids=[candidate_ids[0]],
        metadata={
            "backend_kind": "judge_pairwise.v1",
            "schema_kind": "judge.verdict.v1",
            "verdict": "prefer_a",
            "preferred_candidate_id": candidate_ids[0],
            "artifact_refs": [f"artifacts/search/{run.search_id}/judge_verdict.json"],
            "awkward_storage": ["event.metadata", "message.summary_payload"],
            "missing_public_shape": "assessment_evaluator_truth",
        },
    )
    summary = {
        "cell_id": "phase0.judge_reducer_reasoning_search",
        "family": "judge_reducer_reasoning_search",
        "awkwardness_kind": "assessment_evaluator_truth",
        "why_v1_is_awkward": [
            "pairwise judge verdict is embedded in event metadata",
            "preference judgments do not have a typed shared record",
            "reducer-facing evidence is not linked as first-class runtime truth",
        ],
    }
    return {
        "run": SearchRun(
            search_id=run.search_id,
            recipe_kind=run.recipe_kind,
            candidates=list(run.candidates),
            frontiers=list(run.frontiers),
            events=[*run.events, verify_event],
            messages=list(run.messages),
            carry_states=list(run.carry_states),
            metrics=run.metrics,
            selected_candidate_id=run.selected_candidate_id,
            metadata={**dict(run.metadata), "phase0_cell": summary["cell_id"]},
        ),
        "summary": summary,
    }


def build_branch_execute_verify_pressure_cell() -> Dict[str, object]:
    base = build_stateful_branch_search_example()
    run = base["run"]
    branch_ids = [item.branch_id for item in run.branch_states]
    candidate_ids = [item.candidate_id for item in run.branch_states]
    verify_event = SearchEvent(
        event_id=f"{run.search_id}.event.verify.branch_execute_stub",
        search_id=run.search_id,
        frontier_id=run.frontiers[-1].frontier_id,
        round_index=run.frontiers[-1].round_index,
        operator_kind="verify",
        input_candidate_ids=candidate_ids,
        output_candidate_ids=[run.selected_candidate_id],
        metadata={
            "backend_kind": "branch_execute_verify.v1",
            "schema_kind": "branch.execute_report.v1",
            "verdict": "prefer_a",
            "branch_ids": branch_ids,
            "artifact_refs": [f"artifacts/search/{run.search_id}/branch_execute_verify.json"],
            "awkward_storage": ["event.metadata", "branch_state.metadata", "trajectory.metadata"],
            "missing_public_shape": "assessment_evaluator_truth",
        },
    )
    summary = {
        "cell_id": "phase0.branch_execute_verify_search",
        "family": "branch_execute_verify_search",
        "awkwardness_kind": "assessment_evaluator_truth",
        "why_v1_is_awkward": [
            "branch execute/verify results are hidden in event and branch metadata",
            "merge/discard decisions cannot point to a typed evaluator truth record",
            "trajectory export cannot attach grounded verification truth directly",
        ],
    }
    return {
        "run": SearchRun(
            search_id=run.search_id,
            recipe_kind=run.recipe_kind,
            candidates=list(run.candidates),
            frontiers=list(run.frontiers),
            events=[*run.events, verify_event],
            messages=list(run.messages),
            carry_states=list(run.carry_states),
            workspace_snapshots=list(run.workspace_snapshots),
            branch_states=list(run.branch_states),
            metrics=run.metrics,
            selected_candidate_id=run.selected_candidate_id,
            metadata={**dict(run.metadata), "phase0_cell": summary["cell_id"]},
        ),
        "summary": summary,
    }


def build_dag_v2_phase0_pressure_packet() -> Dict[str, object]:
    cells = [
        build_verifier_guided_pressure_cell(),
        build_judge_reducer_pressure_cell(),
        build_branch_execute_verify_pressure_cell(),
    ]
    awkwardness_kinds = {cell["summary"]["awkwardness_kind"] for cell in cells}
    repeated_shape = awkwardness_kinds == {"assessment_evaluator_truth"}
    return {
        "cells": cells,
        "go_decision": repeated_shape and len(cells) >= 3,
        "repeated_shape_kind": "assessment_evaluator_truth" if repeated_shape else None,
        "conclusion": {
            "missing_public_shape": "assessment_evaluator_truth" if repeated_shape else "unclear",
            "new_message_primitive_needed": False,
            "new_state_primitive_needed": False,
            "async_forced": False,
            "study_cell_count": len(cells),
        },
    }


def build_dag_v2_phase0_pressure_packet_payload() -> Dict[str, object]:
    packet = build_dag_v2_phase0_pressure_packet()
    return {
        "cells": [
            {
                "run": cell["run"].to_dict(),
                "summary": dict(cell["summary"]),
            }
            for cell in packet["cells"]
        ],
        "go_decision": packet["go_decision"],
        "repeated_shape_kind": packet["repeated_shape_kind"],
        "conclusion": dict(packet["conclusion"]),
    }


def build_exact_verifier_assessment_example() -> Dict[str, object]:
    base = build_verifier_guided_pressure_cell()
    run = base["run"]
    selected_candidate = next(item for item in run.candidates if item.candidate_id == run.selected_candidate_id)
    registry: SearchAssessmentRegistry = build_default_search_assessment_registry()
    assessment = registry.assess(
        backend_kind="exact_tests.v1",
        assessment_id=f"{run.search_id}.assessment.exact_tests.1",
        search_id=run.search_id,
        frontier_id=run.frontiers[-1].frontier_id,
        round_index=run.frontiers[-1].round_index,
        candidates=[selected_candidate],
        metadata={"phase": "dag_v2_phase1", "cell_id": "verifier_guided_code_patch_search"},
    )
    verify_event = next(item for item in run.events if item.event_id.endswith("verify.frontier_stub"))
    linked_event = SearchEvent(
        event_id=verify_event.event_id,
        search_id=verify_event.search_id,
        frontier_id=verify_event.frontier_id,
        round_index=verify_event.round_index,
        operator_kind=verify_event.operator_kind,
        input_candidate_ids=list(verify_event.input_candidate_ids),
        output_candidate_ids=list(verify_event.output_candidate_ids),
        message_ids=list(verify_event.message_ids),
        assessment_ids=[assessment.assessment_id],
        metadata=dict(verify_event.metadata),
    )
    events = [
        linked_event if item.event_id == verify_event.event_id else item
        for item in run.events
    ]
    run = SearchRun(
        search_id=run.search_id,
        recipe_kind=run.recipe_kind,
        candidates=list(run.candidates),
        frontiers=list(run.frontiers),
        events=events,
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=[assessment],
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "assessment_enabled": True},
    )
    return {
        "run": run,
        "assessment": assessment,
        "registry_backend_kinds": registry.list_backend_kinds(),
    }


def build_exact_verifier_assessment_example_payload() -> Dict[str, object]:
    example = build_exact_verifier_assessment_example()
    return {
        "run": example["run"].to_dict(),
        "assessment": example["assessment"].to_dict(),
        "registry_backend_kinds": list(example["registry_backend_kinds"]),
    }


def build_judge_pairwise_assessment_example() -> Dict[str, object]:
    base = build_judge_reducer_pressure_cell()
    run = base["run"]
    verify_event = next(item for item in run.events if item.event_id.endswith("verify.judge_stub"))
    candidates = [
        next(item for item in run.candidates if item.candidate_id == candidate_id)
        for candidate_id in verify_event.input_candidate_ids
    ]
    registry: SearchAssessmentRegistry = build_default_search_assessment_registry()
    assessment = registry.assess(
        backend_kind="judge_pairwise.v1",
        assessment_id=f"{run.search_id}.assessment.judge_pairwise.1",
        search_id=run.search_id,
        frontier_id=verify_event.frontier_id,
        round_index=verify_event.round_index,
        candidates=candidates,
        metadata={"phase": "dag_v2_phase1", "cell_id": "judge_reducer_reasoning_search"},
    )
    linked_event = SearchEvent(
        event_id=verify_event.event_id,
        search_id=verify_event.search_id,
        frontier_id=verify_event.frontier_id,
        round_index=verify_event.round_index,
        operator_kind=verify_event.operator_kind,
        input_candidate_ids=list(verify_event.input_candidate_ids),
        output_candidate_ids=list(verify_event.output_candidate_ids),
        message_ids=list(verify_event.message_ids),
        assessment_ids=[assessment.assessment_id],
        metadata=dict(verify_event.metadata),
    )
    events = [
        linked_event if item.event_id == verify_event.event_id else item
        for item in run.events
    ]
    run = SearchRun(
        search_id=run.search_id,
        recipe_kind=run.recipe_kind,
        candidates=list(run.candidates),
        frontiers=list(run.frontiers),
        events=events,
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=[assessment],
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "assessment_enabled": True},
    )
    return {
        "run": run,
        "assessment": assessment,
        "registry_backend_kinds": registry.list_backend_kinds(),
    }


def build_judge_pairwise_assessment_example_payload() -> Dict[str, object]:
    example = build_judge_pairwise_assessment_example()
    return {
        "run": example["run"].to_dict(),
        "assessment": example["assessment"].to_dict(),
        "registry_backend_kinds": list(example["registry_backend_kinds"]),
    }


def build_frontier_verify_gate_example() -> Dict[str, object]:
    base = build_verifier_guided_pressure_cell()
    run = base["run"]
    frontier_candidates = [
        item
        for item in run.candidates
        if item.candidate_id == run.selected_candidate_id
    ]
    registry = build_default_search_assessment_registry()
    gate_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="require_before_select",
        max_assessments=1,
        required_verdicts=["pass"],
        metadata={"recipe": "frontier_verify", "phase": "dag_v2_phase2"},
    )
    outcome = run_barriered_assessment_gate(
        run=run,
        registry=registry,
        config=gate_config,
        frontier_candidates=frontier_candidates,
    )
    events = [*run.events, outcome.gate_event]
    if outcome.selection_event is not None:
        events.append(outcome.selection_event)
    gated_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="frontier_verify",
        candidates=list(run.candidates),
        frontiers=list(run.frontiers),
        events=events,
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=list(outcome.assessments),
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(run.metadata),
            "gate_mode": gate_config.mode,
            "max_assessments": gate_config.max_assessments,
            "terminated": outcome.terminated,
        },
    )
    return {
        "run": gated_run,
        "gate_config": gate_config,
        "outcome": outcome,
    }


def build_frontier_verify_gate_example_payload() -> Dict[str, object]:
    example = build_frontier_verify_gate_example()
    return {
        "run": example["run"].to_dict(),
        "gate_config": {
            "backend_kind": example["gate_config"].backend_kind,
            "mode": example["gate_config"].mode,
            "max_assessments": example["gate_config"].max_assessments,
            "required_verdicts": list(example["gate_config"].required_verdicts),
            "metadata": dict(example["gate_config"].metadata),
        },
        "outcome": {
            "pruned_candidate_ids": list(example["outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "terminated": example["outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
    }


def build_judge_reduce_gate_example() -> Dict[str, object]:
    base = build_judge_reducer_pressure_cell()
    run = base["run"]
    verify_event = next(item for item in run.events if item.event_id.endswith("verify.judge_stub"))
    frontier_candidates = [
        next(item for item in run.candidates if item.candidate_id == candidate_id)
        for candidate_id in verify_event.input_candidate_ids
    ]
    registry = build_default_search_assessment_registry()
    gate_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="prune_on_verdict",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"recipe": "judge_reduce", "phase": "dag_v2_phase2"},
    )
    outcome = run_barriered_assessment_gate(
        run=run,
        registry=registry,
        config=gate_config,
        frontier_candidates=frontier_candidates,
    )
    events = [*run.events, outcome.gate_event]
    if outcome.selection_event is not None:
        events.append(outcome.selection_event)
    gated_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="judge_reduce",
        candidates=list(run.candidates),
        frontiers=list(run.frontiers),
        events=events,
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=list(outcome.assessments),
        metrics=run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(run.metadata),
            "gate_mode": gate_config.mode,
            "max_assessments": gate_config.max_assessments,
            "terminated": outcome.terminated,
        },
    )
    return {
        "run": gated_run,
        "gate_config": gate_config,
        "outcome": outcome,
    }


def build_judge_reduce_gate_example_payload() -> Dict[str, object]:
    example = build_judge_reduce_gate_example()
    return {
        "run": example["run"].to_dict(),
        "gate_config": {
            "backend_kind": example["gate_config"].backend_kind,
            "mode": example["gate_config"].mode,
            "max_assessments": example["gate_config"].max_assessments,
            "required_verdicts": list(example["gate_config"].required_verdicts),
            "metadata": dict(example["gate_config"].metadata),
        },
        "outcome": {
            "pruned_candidate_ids": list(example["outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "terminated": example["outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
    }


def build_branch_execute_verify_reference_recipe() -> Dict[str, object]:
    base = build_branch_execute_verify_pressure_cell()
    run = base["run"]
    frontier_candidates = [
        next(item for item in run.candidates if item.candidate_id == branch.candidate_id)
        for branch in run.branch_states
    ]
    registry = build_default_search_assessment_registry()
    gate_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="prune_on_verdict",
        max_assessments=2,
        required_verdicts=["fail"],
        metadata={"recipe": "branch_execute_verify", "phase": "dag_v2_phase3"},
    )
    outcome = run_barriered_assessment_gate(
        run=run,
        registry=registry,
        config=gate_config,
        frontier_candidates=frontier_candidates,
    )
    events = [*run.events, outcome.gate_event]
    if outcome.selection_event is not None:
        events.append(outcome.selection_event)
    gated_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="branch_execute_verify",
        candidates=list(run.candidates),
        frontiers=list(run.frontiers),
        events=events,
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=list(outcome.assessments),
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(run.metadata),
            "gate_mode": gate_config.mode,
            "max_assessments": gate_config.max_assessments,
            "terminated": outcome.terminated,
        },
    )
    return {
        "run": gated_run,
        "gate_config": gate_config,
        "outcome": outcome,
    }


def build_branch_execute_verify_reference_recipe_payload() -> Dict[str, object]:
    example = build_branch_execute_verify_reference_recipe()
    return {
        "run": example["run"].to_dict(),
        "gate_config": {
            "backend_kind": example["gate_config"].backend_kind,
            "mode": example["gate_config"].mode,
            "max_assessments": example["gate_config"].max_assessments,
            "required_verdicts": list(example["gate_config"].required_verdicts),
            "metadata": dict(example["gate_config"].metadata),
        },
        "outcome": {
            "pruned_candidate_ids": list(example["outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "terminated": example["outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
    }


def build_dag_v2_e4_widening_packet() -> Dict[str, object]:
    frontier_verify = build_frontier_verify_gate_example()
    judge_reduce = build_judge_reduce_gate_example()
    branch_execute_verify = build_branch_execute_verify_reference_recipe()
    return {
        "recipes": [
            {
                "recipe_kind": frontier_verify["run"].recipe_kind,
                "credible_family": "verifier_guided_frontier_search",
                "assessment_count": len(frontier_verify["run"].assessments),
            },
            {
                "recipe_kind": judge_reduce["run"].recipe_kind,
                "credible_family": "judge_reducer_reasoning_search",
                "assessment_count": len(judge_reduce["run"].assessments),
            },
            {
                "recipe_kind": branch_execute_verify["run"].recipe_kind,
                "credible_family": "branch_execute_verify_search",
                "assessment_count": len(branch_execute_verify["run"].assessments),
            },
        ],
        "credible_family_count": 3,
        "widening_due_to_assessment_layer": True,
        "new_public_noun_families_added": 1,
    }


def build_dag_v2_e4_widening_packet_payload() -> Dict[str, object]:
    packet = build_dag_v2_e4_widening_packet()
    return {
        "recipes": [dict(item) for item in packet["recipes"]],
        "credible_family_count": packet["credible_family_count"],
        "widening_due_to_assessment_layer": packet["widening_due_to_assessment_layer"],
        "new_public_noun_families_added": packet["new_public_noun_families_added"],
    }


def build_dag_v2_stop_go_synthesis() -> Dict[str, object]:
    return {
        "trajectory_export": {
            "assessment_ids_linked": True,
            "stable_consumer_surface": [
                "SearchAssessment",
                "SearchRun.assessments",
                "SearchEvent.assessment_ids",
                "SearchTrajectoryStep.assessment_ids",
            ],
        },
        "optimize_adapter": {
            "outside_dag_kernel": True,
            "consumes": [
                "SearchRun.assessments",
                "SearchTrajectoryExport.steps[].assessment_ids",
                "SearchTrajectoryExport.selected_candidate_id",
            ],
            "introduces_optimize_public_nouns_into_dag": False,
        },
        "darwin_boundary": {
            "dag_runtime_role": "bounded_per_task_search",
            "darwin_role": "outer_loop_campaign_orchestration",
            "handoff_signals": [
                "many_cohort_async_pressure",
                "persistent_diversity_archive_pressure",
                "cross_task_budget_allocation_pressure",
            ],
            "campaign_nouns_added_to_dag": False,
        },
        "rl_facing_note": {
            "available_now": [
                "SearchTrajectoryExport",
                "SearchRewardSignal",
                "SearchAssessment",
            ],
            "deferred": [
                "training_framework",
                "online_policy_learning",
                "public_rl_control_surface",
            ],
            "training_framework_added": False,
        },
        "stop_go": {
            "current_decision": "stop_and_freeze",
            "only_new_public_noun_family": "SearchAssessment",
            "async_public_mode_added": False,
            "proceed_only_if": [
                "repeated_shape_pressure_exceeds_assessment_layer",
                "multiple_families_need_same_new_public_shape",
                "docs_or_private_helpers_no_longer_suffice",
            ],
        },
    }


def build_dag_v2_stop_go_synthesis_payload() -> Dict[str, object]:
    synthesis = build_dag_v2_stop_go_synthesis()
    return {
        "trajectory_export": dict(synthesis["trajectory_export"]),
        "optimize_adapter": dict(synthesis["optimize_adapter"]),
        "darwin_boundary": dict(synthesis["darwin_boundary"]),
        "rl_facing_note": dict(synthesis["rl_facing_note"]),
        "stop_go": dict(synthesis["stop_go"]),
    }


def build_post_v2_study_01_verifier_patch_branch() -> Dict[str, object]:
    base = build_stateful_branch_search_example()
    run = base["run"]
    merged_candidate = next(item for item in run.candidates if item.candidate_id == base["merged_branch"].candidate_id)
    discarded_candidate = next(item for item in run.candidates if item.candidate_id == base["discarded_branch"].candidate_id)
    repair_snapshot = SearchWorkspaceSnapshot(
        snapshot_id=f"{run.search_id}.snapshot.branch.repair_patch",
        search_id=run.search_id,
        branch_id=f"{run.search_id}.branch.repair_patch",
        artifact_ref=f"artifacts/search/{run.search_id}/repair_patch_snapshot.json",
        parent_snapshot_id=base["merged_snapshot"].snapshot_id,
        derived_from_candidate_id=merged_candidate.candidate_id,
        metadata={"lane": "repair_patch_branch", "action": "patch_iterate"},
    )
    repair_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.branch.repair_patch",
        search_id=run.search_id,
        frontier_id=merged_candidate.frontier_id,
        parent_ids=[merged_candidate.candidate_id],
        round_index=merged_candidate.round_index,
        depth=merged_candidate.depth + 1,
        payload_ref=f"artifacts/search/{run.search_id}/repair_patch_candidate.json",
        workspace_ref=repair_snapshot.snapshot_id,
        score_vector={"correctness_score": 0.91, "patch_risk": 0.24},
        usage={"prompt_tokens": 79, "completion_tokens": 41},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/repair_patch_candidate.md",
        metadata={"study_id": "study_01_verifier_patch_branch", "lane": "repair_patch_branch"},
    )
    repair_branch = SearchBranchState(
        branch_id=repair_snapshot.branch_id,
        search_id=run.search_id,
        candidate_id=repair_candidate.candidate_id,
        snapshot_ids=[repair_snapshot.snapshot_id],
        head_snapshot_id=repair_snapshot.snapshot_id,
        status="active",
        metadata={"review_status": "pending", "study_id": "study_01_verifier_patch_branch"},
    )
    execute_event = SearchEvent(
        event_id=f"{run.search_id}.event.execute.verifier_patch_branch",
        search_id=run.search_id,
        frontier_id=merged_candidate.frontier_id,
        round_index=merged_candidate.round_index,
        operator_kind="execute",
        input_candidate_ids=[
            merged_candidate.candidate_id,
            discarded_candidate.candidate_id,
            repair_candidate.candidate_id,
        ],
        output_candidate_ids=[
            merged_candidate.candidate_id,
            discarded_candidate.candidate_id,
            repair_candidate.candidate_id,
        ],
        metadata={
            "study_id": "study_01_verifier_patch_branch",
            "branch_ids": [
                base["merged_branch"].branch_id,
                base["discarded_branch"].branch_id,
                repair_branch.branch_id,
            ],
            "state_mode": "branch_local_patch_iteration",
        },
    )
    verify_stub_event = SearchEvent(
        event_id=f"{run.search_id}.event.verify.verifier_patch_branch",
        search_id=run.search_id,
        frontier_id=merged_candidate.frontier_id,
        round_index=merged_candidate.round_index,
        operator_kind="verify",
        input_candidate_ids=[
            merged_candidate.candidate_id,
            discarded_candidate.candidate_id,
            repair_candidate.candidate_id,
        ],
        output_candidate_ids=[repair_candidate.candidate_id],
        metadata={
            "study_id": "study_01_verifier_patch_branch",
            "backend_kind": "exact_tests.v1",
            "schema_kind": "code.test_report.v1",
            "candidate_count": 3,
            "branch_local_patch_pass": True,
        },
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="verifier_patch_branch_pressure_pass",
        candidates=[*run.candidates, repair_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, execute_event, verify_stub_event],
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        workspace_snapshots=[*run.workspace_snapshots, repair_snapshot],
        branch_states=[*run.branch_states, repair_branch],
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_01_verifier_patch_branch"},
    )
    gate_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="require_before_select",
        max_assessments=3,
        required_verdicts=["pass"],
        metadata={"study_id": "study_01_verifier_patch_branch", "phase": "post_v2_usage"},
    )
    registry = build_default_search_assessment_registry()
    frontier_candidates = [merged_candidate, discarded_candidate, repair_candidate]
    outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=gate_config,
        frontier_candidates=frontier_candidates,
    )
    events = [*study_run.events, outcome.gate_event]
    if outcome.selection_event is not None:
        events.append(outcome.selection_event)
    gated_run = SearchRun(
        search_id=study_run.search_id,
        recipe_kind=study_run.recipe_kind,
        candidates=list(study_run.candidates),
        frontiers=list(study_run.frontiers),
        events=events,
        messages=list(study_run.messages),
        carry_states=list(study_run.carry_states),
        assessments=list(outcome.assessments),
        workspace_snapshots=list(study_run.workspace_snapshots),
        branch_states=list(study_run.branch_states),
        metrics=study_run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "gate_mode": gate_config.mode,
            "max_assessments": gate_config.max_assessments,
            "terminated": outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "branch-local patch candidates fit existing SearchCandidate and SearchBranchState records",
            "exact verifier truth stays first-class through SearchAssessment",
            "selection remains attributable to barriered assessment gates",
        ],
        "awkward": [
            "grouping multiple verifier reports into one higher-level study summary is still a docs-level concern",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "recipe_level",
    }
    return {
        "run": gated_run,
        "gate_config": gate_config,
        "outcome": outcome,
        "repair_candidate_id": repair_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_01_verifier_patch_branch_payload() -> Dict[str, object]:
    example = build_post_v2_study_01_verifier_patch_branch()
    return {
        "run": example["run"].to_dict(),
        "gate_config": {
            "backend_kind": example["gate_config"].backend_kind,
            "mode": example["gate_config"].mode,
            "max_assessments": example["gate_config"].max_assessments,
            "required_verdicts": list(example["gate_config"].required_verdicts),
            "metadata": dict(example["gate_config"].metadata),
        },
        "outcome": {
            "pruned_candidate_ids": list(example["outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "terminated": example["outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
        "repair_candidate_id": example["repair_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_02_judge_reducer_rounds() -> Dict[str, object]:
    base = build_judge_reducer_pressure_cell()
    run = base["run"]
    verify_event = next(item for item in run.events if item.event_id.endswith("verify.judge_stub"))
    candidate_a = next(item for item in run.candidates if item.candidate_id == verify_event.input_candidate_ids[0])
    candidate_b = next(item for item in run.candidates if item.candidate_id == verify_event.input_candidate_ids[1])
    synthesis_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.judge_reduce.synthesis",
        search_id=run.search_id,
        frontier_id=candidate_a.frontier_id,
        parent_ids=[candidate_a.candidate_id, candidate_b.candidate_id],
        round_index=candidate_a.round_index,
        depth=max(candidate_a.depth, candidate_b.depth) + 1,
        payload_ref=f"artifacts/search/{run.search_id}/judge_reduce_synthesis.json",
        score_vector={"correctness_score": 0.93, "coherence_score": 0.88},
        usage={"prompt_tokens": 71, "completion_tokens": 34},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/judge_reduce_synthesis.md",
        metadata={"study_id": "study_02_judge_reducer_rounds", "lane": "synthesis_candidate"},
    )
    aggregate_event = SearchEvent(
        event_id=f"{run.search_id}.event.aggregate.judge_reduce_round2",
        search_id=run.search_id,
        frontier_id=candidate_a.frontier_id,
        round_index=candidate_a.round_index,
        operator_kind="aggregate",
        input_candidate_ids=[candidate_a.candidate_id, candidate_b.candidate_id],
        output_candidate_ids=[synthesis_candidate.candidate_id],
        metadata={
            "study_id": "study_02_judge_reducer_rounds",
            "reduction_round": 2,
            "bounded_summary": True,
        },
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="judge_reducer_rounds_pressure_pass",
        candidates=[*run.candidates, synthesis_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, aggregate_event],
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_02_judge_reducer_rounds"},
    )
    registry = build_default_search_assessment_registry()
    round1_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="prune_on_verdict",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={
            "study_id": "study_02_judge_reducer_rounds",
            "reduction_round": 1,
            "phase": "post_v2_usage",
        },
    )
    round1_outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=round1_config,
        frontier_candidates=[candidate_a, candidate_b],
    )
    round1_selected = next(
        item for item in study_run.candidates if item.candidate_id == round1_outcome.selected_candidate_id
    )
    round2_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={
            "study_id": "study_02_judge_reducer_rounds",
            "reduction_round": 2,
            "phase": "post_v2_usage",
        },
    )
    round2_outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=round2_config,
        frontier_candidates=[round1_selected, synthesis_candidate],
    )
    events = [
        *study_run.events,
        round1_outcome.gate_event,
        *( [round1_outcome.selection_event] if round1_outcome.selection_event is not None else [] ),
        round2_outcome.gate_event,
        *( [round2_outcome.selection_event] if round2_outcome.selection_event is not None else [] ),
    ]
    final_run = SearchRun(
        search_id=study_run.search_id,
        recipe_kind=study_run.recipe_kind,
        candidates=list(study_run.candidates),
        frontiers=list(study_run.frontiers),
        events=events,
        messages=list(study_run.messages),
        carry_states=list(study_run.carry_states),
        assessments=[*round1_outcome.assessments, *round2_outcome.assessments],
        metrics=study_run.metrics,
        selected_candidate_id=round2_outcome.selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "reduction_round_count": 2,
            "terminated": round2_outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "repeated adjudication rounds reuse SearchAssessment without new runtime nouns",
            "pairwise judge verdicts remain explicit and linkable across rounds",
            "bounded reduction can still terminate through barriered selection",
        ],
        "awkward": [
            "bundling several assessment rounds into one study summary wants a helper, but not a new public primitive",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "private_helper_level",
    }
    return {
        "run": final_run,
        "round1_config": round1_config,
        "round2_config": round2_config,
        "round1_outcome": round1_outcome,
        "round2_outcome": round2_outcome,
        "synthesis_candidate_id": synthesis_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_02_judge_reducer_rounds_payload() -> Dict[str, object]:
    example = build_post_v2_study_02_judge_reducer_rounds()
    return {
        "run": example["run"].to_dict(),
        "round1_config": {
            "backend_kind": example["round1_config"].backend_kind,
            "mode": example["round1_config"].mode,
            "max_assessments": example["round1_config"].max_assessments,
            "required_verdicts": list(example["round1_config"].required_verdicts),
            "metadata": dict(example["round1_config"].metadata),
        },
        "round2_config": {
            "backend_kind": example["round2_config"].backend_kind,
            "mode": example["round2_config"].mode,
            "max_assessments": example["round2_config"].max_assessments,
            "required_verdicts": list(example["round2_config"].required_verdicts),
            "metadata": dict(example["round2_config"].metadata),
        },
        "round1_outcome": {
            "pruned_candidate_ids": list(example["round1_outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["round1_outcome"].selected_candidate_id,
            "terminated": example["round1_outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["round1_outcome"].assessments],
        },
        "round2_outcome": {
            "pruned_candidate_ids": list(example["round2_outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["round2_outcome"].selected_candidate_id,
            "terminated": example["round2_outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["round2_outcome"].assessments],
        },
        "synthesis_candidate_id": example["synthesis_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_03_branch_execute_verify_deeper() -> Dict[str, object]:
    base = build_post_v2_study_01_verifier_patch_branch()
    run = base["run"]
    repair_candidate = next(item for item in run.candidates if item.candidate_id == base["repair_candidate_id"])
    risky_snapshot = SearchWorkspaceSnapshot(
        snapshot_id=f"{run.search_id}.snapshot.branch.risky_patch",
        search_id=run.search_id,
        branch_id=f"{run.search_id}.branch.risky_patch",
        artifact_ref=f"artifacts/search/{run.search_id}/risky_patch_snapshot.json",
        parent_snapshot_id=repair_candidate.workspace_ref,
        derived_from_candidate_id=repair_candidate.candidate_id,
        metadata={"lane": "risky_patch_branch", "action": "patch_iterate"},
    )
    risky_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.branch.risky_patch",
        search_id=run.search_id,
        frontier_id=repair_candidate.frontier_id,
        parent_ids=[repair_candidate.candidate_id],
        round_index=repair_candidate.round_index,
        depth=repair_candidate.depth + 1,
        payload_ref=f"artifacts/search/{run.search_id}/risky_patch_candidate.json",
        workspace_ref=risky_snapshot.snapshot_id,
        score_vector={"correctness_score": 0.84, "patch_risk": 0.61},
        usage={"prompt_tokens": 83, "completion_tokens": 47},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/risky_patch_candidate.md",
        metadata={"study_id": "study_03_branch_execute_verify_deeper", "lane": "risky_patch_branch"},
    )
    risky_branch = SearchBranchState(
        branch_id=risky_snapshot.branch_id,
        search_id=run.search_id,
        candidate_id=risky_candidate.candidate_id,
        snapshot_ids=[risky_snapshot.snapshot_id],
        head_snapshot_id=risky_snapshot.snapshot_id,
        status="active",
        metadata={"review_status": "pending", "study_id": "study_03_branch_execute_verify_deeper"},
    )
    execute_event = SearchEvent(
        event_id=f"{run.search_id}.event.execute.branch_execute_verify_deeper",
        search_id=run.search_id,
        frontier_id=repair_candidate.frontier_id,
        round_index=repair_candidate.round_index,
        operator_kind="execute",
        input_candidate_ids=[repair_candidate.candidate_id, risky_candidate.candidate_id],
        output_candidate_ids=[repair_candidate.candidate_id, risky_candidate.candidate_id],
        metadata={
            "study_id": "study_03_branch_execute_verify_deeper",
            "branch_ids": [next(item for item in run.branch_states if item.candidate_id == repair_candidate.candidate_id).branch_id, risky_branch.branch_id],
            "execution_bundle_kind": "branch_execute_verify_pair.v1",
        },
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="branch_execute_verify_deeper_pressure_pass",
        candidates=[*run.candidates, risky_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, execute_event],
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=list(run.assessments),
        workspace_snapshots=[*run.workspace_snapshots, risky_snapshot],
        branch_states=[*run.branch_states, risky_branch],
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_03_branch_execute_verify_deeper"},
    )
    registry = build_default_search_assessment_registry()
    verify_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["pass"],
        metadata={"study_id": "study_03_branch_execute_verify_deeper", "phase": "post_v2_usage"},
    )
    verify_outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=verify_config,
        frontier_candidates=[repair_candidate, risky_candidate],
    )
    judge_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"study_id": "study_03_branch_execute_verify_deeper", "phase": "post_v2_usage"},
    )
    judge_outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=judge_config,
        frontier_candidates=[repair_candidate, risky_candidate],
    )
    selected_candidate_id = judge_outcome.selected_candidate_id or verify_outcome.selected_candidate_id
    merge_event = SearchEvent(
        event_id=f"{run.search_id}.event.merge.branch_execute_verify_deeper",
        search_id=run.search_id,
        frontier_id=repair_candidate.frontier_id,
        round_index=repair_candidate.round_index,
        operator_kind="merge",
        input_candidate_ids=[selected_candidate_id] if selected_candidate_id else [],
        output_candidate_ids=[selected_candidate_id] if selected_candidate_id else [],
        metadata={"study_id": "study_03_branch_execute_verify_deeper"},
    )
    final_events = [
        *study_run.events,
        verify_outcome.gate_event,
        *( [verify_outcome.selection_event] if verify_outcome.selection_event is not None else [] ),
        judge_outcome.gate_event,
        *( [judge_outcome.selection_event] if judge_outcome.selection_event is not None else [] ),
        merge_event,
    ]
    final_run = SearchRun(
        search_id=study_run.search_id,
        recipe_kind=study_run.recipe_kind,
        candidates=list(study_run.candidates),
        frontiers=list(study_run.frontiers),
        events=final_events,
        messages=list(study_run.messages),
        carry_states=list(study_run.carry_states),
        assessments=[*study_run.assessments, *verify_outcome.assessments, *judge_outcome.assessments],
        workspace_snapshots=list(study_run.workspace_snapshots),
        branch_states=list(study_run.branch_states),
        metrics=study_run.metrics,
        selected_candidate_id=selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "deeper_branch_execute_verify": True,
            "terminated": verify_outcome.terminated or judge_outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "deeper branch-local execute/verify flow still fits current branch state plus assessment records",
            "assessment-backed ranking among viable branches stays explicit",
            "merge remains attributable to inspectable branch and assessment artifacts",
        ],
        "awkward": [
            "combining branch-local execute reports with follow-on ranking summaries is still a reporting/helper problem",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "private_helper_level",
    }
    return {
        "run": final_run,
        "verify_config": verify_config,
        "judge_config": judge_config,
        "verify_outcome": verify_outcome,
        "judge_outcome": judge_outcome,
        "risky_candidate_id": risky_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_03_branch_execute_verify_deeper_payload() -> Dict[str, object]:
    example = build_post_v2_study_03_branch_execute_verify_deeper()
    return {
        "run": example["run"].to_dict(),
        "verify_config": {
            "backend_kind": example["verify_config"].backend_kind,
            "mode": example["verify_config"].mode,
            "max_assessments": example["verify_config"].max_assessments,
            "required_verdicts": list(example["verify_config"].required_verdicts),
            "metadata": dict(example["verify_config"].metadata),
        },
        "judge_config": {
            "backend_kind": example["judge_config"].backend_kind,
            "mode": example["judge_config"].mode,
            "max_assessments": example["judge_config"].max_assessments,
            "required_verdicts": list(example["judge_config"].required_verdicts),
            "metadata": dict(example["judge_config"].metadata),
        },
        "verify_outcome": {
            "pruned_candidate_ids": list(example["verify_outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["verify_outcome"].selected_candidate_id,
            "terminated": example["verify_outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["verify_outcome"].assessments],
        },
        "judge_outcome": {
            "pruned_candidate_ids": list(example["judge_outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["judge_outcome"].selected_candidate_id,
            "terminated": example["judge_outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["judge_outcome"].assessments],
        },
        "risky_candidate_id": example["risky_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_04_optimize_adapter_probe() -> Dict[str, object]:
    study = build_post_v2_study_03_branch_execute_verify_deeper()
    run = study["run"]
    trajectory = export_search_trajectory(
        run,
        metadata={"study_id": "study_04_optimize_adapter_probe", "consumer": "optimize_adapter"},
    )
    selected_candidate = next(item for item in run.candidates if item.candidate_id == run.selected_candidate_id)
    assessment_backend_kinds = sorted({item.backend_kind for item in run.assessments})
    assessment_ids = sorted({assessment_id for step in trajectory.steps for assessment_id in step.assessment_ids})
    optimize_adapter_payload = {
        "source_search_id": run.search_id,
        "source_recipe_kind": run.recipe_kind,
        "selected_candidate_id": trajectory.selected_candidate_id,
        "selected_candidate_payload_ref": selected_candidate.payload_ref,
        "assessment_backend_kinds": assessment_backend_kinds,
        "trajectory_assessment_ids": assessment_ids,
        "selected_candidate_score_vector": dict(selected_candidate.score_vector),
        "adapter_boundary": {
            "outside_dag_kernel": True,
            "introduced_optimize_public_nouns_into_dag": False,
            "consumes": [
                "SearchAssessment",
                "SearchTrajectoryExport.steps[].assessment_ids",
                "SearchTrajectoryExport.selected_candidate_id",
            ],
        },
    }
    evidence = {
        "easy": [
            "assessment-bearing trajectory export is sufficient for a simple optimize-side evidence packet",
            "selected candidate and assessment linkage survive export without extra DAG primitives",
            "no optimize public noun is needed inside the DAG kernel",
        ],
        "awkward": [
            "optimize-side aggregation of assessment summaries is still an adapter concern rather than DAG truth",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "trajectory": trajectory,
        "optimize_adapter_payload": optimize_adapter_payload,
        "evidence": evidence,
    }


def build_post_v2_study_04_optimize_adapter_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_04_optimize_adapter_probe()
    return {
        "run": example["run"].to_dict(),
        "trajectory": example["trajectory"].to_dict(),
        "optimize_adapter_payload": {
            "source_search_id": example["optimize_adapter_payload"]["source_search_id"],
            "source_recipe_kind": example["optimize_adapter_payload"]["source_recipe_kind"],
            "selected_candidate_id": example["optimize_adapter_payload"]["selected_candidate_id"],
            "selected_candidate_payload_ref": example["optimize_adapter_payload"]["selected_candidate_payload_ref"],
            "assessment_backend_kinds": list(example["optimize_adapter_payload"]["assessment_backend_kinds"]),
            "trajectory_assessment_ids": list(example["optimize_adapter_payload"]["trajectory_assessment_ids"]),
            "selected_candidate_score_vector": dict(example["optimize_adapter_payload"]["selected_candidate_score_vector"]),
            "adapter_boundary": dict(example["optimize_adapter_payload"]["adapter_boundary"]),
        },
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_05_rl_facing_probe() -> Dict[str, object]:
    study = build_post_v2_study_03_branch_execute_verify_deeper()
    run = study["run"]
    trajectory = export_search_trajectory(
        run,
        metadata={"study_id": "study_05_rl_facing_probe", "consumer": "rl_adjacent"},
    )
    dataset = build_search_offline_dataset(
        [trajectory],
        dataset_id=f"{run.search_id}.post_v2_rl_probe",
        metadata={"study_id": "study_05_rl_facing_probe", "operator_conditioned": True},
    )
    rl_consumption_packet = {
        "dataset_id": dataset.dataset_id,
        "trajectory_count": len(dataset.trajectories),
        "step_count": len(trajectory.steps),
        "reward_signal_count": len(trajectory.reward_signals),
        "assessment_linked_step_count": sum(1 for step in trajectory.steps if step.assessment_ids),
        "selected_candidate_id": trajectory.selected_candidate_id,
        "rl_boundary": {
            "training_framework_added": False,
            "public_rl_control_surface_added": False,
            "downstream_wrapper_required": True,
            "uses_existing_surfaces": [
                "SearchTrajectoryExport",
                "SearchRewardSignal",
                "SearchAssessment",
            ],
        },
    }
    evidence = {
        "easy": [
            "trajectory export already carries operator-conditioned steps, reward signals, and assessment linkage",
            "offline dataset packaging is enough for a narrow RL-adjacent consumer packet",
            "no learner or actor surface is required inside DAG",
        ],
        "awkward": [
            "episode packaging and learner-specific tensorization remain downstream wrapper concerns",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "downstream_consumer_level",
    }
    return {
        "run": run,
        "trajectory": trajectory,
        "dataset": dataset,
        "rl_consumption_packet": rl_consumption_packet,
        "evidence": evidence,
    }


def build_post_v2_study_05_rl_facing_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_05_rl_facing_probe()
    return {
        "run": example["run"].to_dict(),
        "trajectory": example["trajectory"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "rl_consumption_packet": {
            "dataset_id": example["rl_consumption_packet"]["dataset_id"],
            "trajectory_count": example["rl_consumption_packet"]["trajectory_count"],
            "step_count": example["rl_consumption_packet"]["step_count"],
            "reward_signal_count": example["rl_consumption_packet"]["reward_signal_count"],
            "assessment_linked_step_count": example["rl_consumption_packet"]["assessment_linked_step_count"],
            "selected_candidate_id": example["rl_consumption_packet"]["selected_candidate_id"],
            "rl_boundary": dict(example["rl_consumption_packet"]["rl_boundary"]),
        },
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_06_darwin_boundary_probe() -> Dict[str, object]:
    scenarios = [
        {
            "scenario_id": "dag_local_branch_episode",
            "description": "single bounded branch-local execute/verify search episode",
            "owner": "dag_local",
            "why": "fits current per-task search truth, assessments, and branch-local state",
        },
        {
            "scenario_id": "multi_cohort_async_campaign",
            "description": "many bounded searches coordinated across cohorts with shared budget and asynchronous scheduling",
            "owner": "darwin_local",
            "why": "requires outer-loop campaign orchestration and cross-run budget allocation",
        },
        {
            "scenario_id": "persistent_diversity_archive",
            "description": "cross-task archive or island pressure over many runs",
            "owner": "darwin_local",
            "why": "requires persistent archive semantics outside DAG runtime",
        },
        {
            "scenario_id": "assessment_export_to_optimize",
            "description": "assessment-bearing DAG export consumed by optimize-side evaluation",
            "owner": "adapter_local",
            "why": "works through exports and adapters without a DAG kernel change",
        },
    ]
    evidence = {
        "easy": [
            "DAG-local vs DARWIN-local ownership is classifiable with the current V2 boundary memo",
            "multi-cohort async budget allocation clearly belongs outside DAG",
            "export-driven composition remains adapter-local rather than DAG-local",
        ],
        "awkward": [
            "some future scenarios may mix DAG-local and DARWIN-local concerns, but that ambiguity is a classification concern, not current kernel pressure",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "boundary_clarification_level",
    }
    synthesis = {
        "no_v3_now": True,
        "repeated_shape_gap_count": 0,
        "dag_v2_should_remain_frozen": True,
        "next_default_move": "use_dag_v2_on_more_targets",
    }
    return {
        "scenarios": scenarios,
        "evidence": evidence,
        "synthesis": synthesis,
    }


def build_post_v2_study_06_darwin_boundary_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_06_darwin_boundary_probe()
    return {
        "scenarios": [dict(item) for item in example["scenarios"]],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
        "synthesis": dict(example["synthesis"]),
    }


def build_post_v2_study_07_message_passing_adjudication() -> Dict[str, object]:
    base = build_pacore_search_runtime_example()
    run = base["run"]
    assessment_frontier = run.frontiers[-2]
    frontier_candidates = [
        item for item in run.candidates if item.frontier_id == assessment_frontier.frontier_id
    ][:2]
    if len(frontier_candidates) != 2:
        raise ValueError("study_07 requires exactly two pre-synthesis frontier candidates")
    carry_state = run.carry_states[-1]
    incumbent = frontier_candidates[0]
    challenger = frontier_candidates[1]
    adjudicated_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.message_adjudication.1",
        search_id=run.search_id,
        frontier_id=assessment_frontier.frontier_id,
        parent_ids=[incumbent.candidate_id, challenger.candidate_id],
        round_index=assessment_frontier.round_index,
        depth=max(incumbent.depth, challenger.depth) + 1,
        payload_ref=f"artifacts/search/{run.search_id}/message_adjudication_candidate.json",
        message_ref=carry_state.message_ids[0],
        score_vector={"correctness_score": 0.9, "coherence_score": 0.89},
        usage={"prompt_tokens": 77, "completion_tokens": 36},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/message_adjudication_candidate.md",
        metadata={"study_id": "study_07_message_passing_adjudication", "carry_state_id": carry_state.state_id},
    )
    aggregate_event = SearchEvent(
        event_id=f"{run.search_id}.event.aggregate.message_adjudication",
        search_id=run.search_id,
        frontier_id=assessment_frontier.frontier_id,
        round_index=assessment_frontier.round_index,
        operator_kind="aggregate",
        input_candidate_ids=[incumbent.candidate_id, challenger.candidate_id],
        output_candidate_ids=[adjudicated_candidate.candidate_id],
        message_ids=list(carry_state.message_ids),
        metadata={
            "study_id": "study_07_message_passing_adjudication",
            "carry_state_id": carry_state.state_id,
            "bounded_message_passing": True,
        },
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="message_passing_adjudication_pressure_pass",
        candidates=[*run.candidates, adjudicated_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, aggregate_event],
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_07_message_passing_adjudication"},
    )
    gate_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"study_id": "study_07_message_passing_adjudication", "phase": "post_v2_wave2"},
    )
    registry = build_default_search_assessment_registry()
    outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=gate_config,
        frontier_candidates=[adjudicated_candidate, incumbent],
    )
    events = [*study_run.events, outcome.gate_event]
    if outcome.selection_event is not None:
        events.append(outcome.selection_event)
    final_run = SearchRun(
        search_id=study_run.search_id,
        recipe_kind=study_run.recipe_kind,
        candidates=list(study_run.candidates),
        frontiers=list(study_run.frontiers),
        events=events,
        messages=list(study_run.messages),
        carry_states=list(study_run.carry_states),
        assessments=list(outcome.assessments),
        metrics=study_run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "carry_state_id": carry_state.state_id,
            "terminated": outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "bounded carry-state plus assessments can support a message-passing adjudication recipe",
            "carry-over remains explicit without a new message primitive",
            "selection remains attributable to existing assessment gates",
        ],
        "awkward": [
            "multi-round adjudication summaries over carry-state still want helper/reporting code",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "private_helper_level",
    }
    return {
        "run": final_run,
        "gate_config": gate_config,
        "outcome": outcome,
        "carry_state_id": carry_state.state_id,
        "adjudicated_candidate_id": adjudicated_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_07_message_passing_adjudication_payload() -> Dict[str, object]:
    example = build_post_v2_study_07_message_passing_adjudication()
    return {
        "run": example["run"].to_dict(),
        "gate_config": {
            "backend_kind": example["gate_config"].backend_kind,
            "mode": example["gate_config"].mode,
            "max_assessments": example["gate_config"].max_assessments,
            "required_verdicts": list(example["gate_config"].required_verdicts),
            "metadata": dict(example["gate_config"].metadata),
        },
        "outcome": {
            "pruned_candidate_ids": list(example["outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "terminated": example["outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
        "carry_state_id": example["carry_state_id"],
        "adjudicated_candidate_id": example["adjudicated_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_08_verifier_judge_handoff() -> Dict[str, object]:
    base = build_pacore_search_runtime_example()
    run = base["run"]
    assessment_frontier = run.frontiers[-2]
    frontier_candidates = [
        item for item in run.candidates if item.frontier_id == assessment_frontier.frontier_id
    ][:2]
    if len(frontier_candidates) != 2:
        raise ValueError("study_08 requires exactly two pre-synthesis frontier candidates")
    carry_state = run.carry_states[-1]
    incumbent = frontier_candidates[0]
    challenger = frontier_candidates[1]
    adjudicated_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.message_handoff.1",
        search_id=run.search_id,
        frontier_id=assessment_frontier.frontier_id,
        parent_ids=[incumbent.candidate_id, challenger.candidate_id],
        round_index=assessment_frontier.round_index,
        depth=max(incumbent.depth, challenger.depth) + 1,
        payload_ref=f"artifacts/search/{run.search_id}/message_handoff_candidate.json",
        message_ref=carry_state.message_ids[0],
        score_vector={"correctness_score": 0.91, "coherence_score": 0.9},
        usage={"prompt_tokens": 82, "completion_tokens": 39},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/message_handoff_candidate.md",
        metadata={"study_id": "study_08_verifier_judge_handoff", "carry_state_id": carry_state.state_id},
    )
    aggregate_event = SearchEvent(
        event_id=f"{run.search_id}.event.aggregate.message_handoff",
        search_id=run.search_id,
        frontier_id=assessment_frontier.frontier_id,
        round_index=assessment_frontier.round_index,
        operator_kind="aggregate",
        input_candidate_ids=[incumbent.candidate_id, challenger.candidate_id],
        output_candidate_ids=[adjudicated_candidate.candidate_id],
        message_ids=list(carry_state.message_ids),
        metadata={
            "study_id": "study_08_verifier_judge_handoff",
            "carry_state_id": carry_state.state_id,
            "bounded_message_passing": True,
        },
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="message_passing_verifier_judge_handoff",
        candidates=[*run.candidates, adjudicated_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, aggregate_event],
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_08_verifier_judge_handoff"},
    )
    verifier_gate_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["pass"],
        metadata={"study_id": "study_08_verifier_judge_handoff", "phase": "post_v2_wave2"},
    )
    registry = build_default_search_assessment_registry()
    verifier_outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=registry,
        config=verifier_gate_config,
        frontier_candidates=[adjudicated_candidate, incumbent],
    )
    verifier_events = [*study_run.events, verifier_outcome.gate_event]
    verifier_run = SearchRun(
        search_id=study_run.search_id,
        recipe_kind=study_run.recipe_kind,
        candidates=list(study_run.candidates),
        frontiers=list(study_run.frontiers),
        events=verifier_events,
        messages=list(study_run.messages),
        carry_states=list(study_run.carry_states),
        assessments=list(verifier_outcome.assessments),
        metrics=study_run.metrics,
        selected_candidate_id=study_run.selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "carry_state_id": carry_state.state_id,
            "verifier_pruned_candidate_ids": list(verifier_outcome.pruned_candidate_ids),
        },
    )
    surviving_candidates = [
        item
        for item in [adjudicated_candidate, incumbent]
        if item.candidate_id not in set(verifier_outcome.pruned_candidate_ids)
    ]
    if len(surviving_candidates) != 2:
        raise ValueError("study_08 requires two surviving candidates after verifier gate")
    judge_gate_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"study_id": "study_08_verifier_judge_handoff", "phase": "post_v2_wave2"},
    )
    judge_outcome = run_barriered_assessment_gate(
        run=verifier_run,
        registry=registry,
        config=judge_gate_config,
        frontier_candidates=surviving_candidates,
    )
    final_events = [*verifier_run.events, judge_outcome.gate_event]
    if judge_outcome.selection_event is not None:
        final_events.append(judge_outcome.selection_event)
    final_run = SearchRun(
        search_id=verifier_run.search_id,
        recipe_kind=verifier_run.recipe_kind,
        candidates=list(verifier_run.candidates),
        frontiers=list(verifier_run.frontiers),
        events=final_events,
        messages=list(verifier_run.messages),
        carry_states=list(verifier_run.carry_states),
        assessments=[*verifier_run.assessments, *judge_outcome.assessments],
        metrics=verifier_run.metrics,
        selected_candidate_id=judge_outcome.selected_candidate_id,
        metadata={
            **dict(verifier_run.metadata),
            "terminated": judge_outcome.terminated,
            "selected_after_handoff": judge_outcome.selected_candidate_id,
        },
    )
    evidence = {
        "easy": [
            "exact verifier and judge pairwise truth can compose through the same SearchAssessment surface",
            "bounded carry-state plus two-stage assessment remains attributable without a second runtime noun family",
            "selection still stays barriered and deterministic",
        ],
        "awkward": [
            "higher-level handoff reports want adapter-side bundling, but the runtime truth surface is already sufficient",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "recipe_level",
    }
    return {
        "run": final_run,
        "verifier_gate_config": verifier_gate_config,
        "judge_gate_config": judge_gate_config,
        "verifier_outcome": verifier_outcome,
        "judge_outcome": judge_outcome,
        "carry_state_id": carry_state.state_id,
        "adjudicated_candidate_id": adjudicated_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_08_verifier_judge_handoff_payload() -> Dict[str, object]:
    example = build_post_v2_study_08_verifier_judge_handoff()
    return {
        "run": example["run"].to_dict(),
        "verifier_gate_config": {
            "backend_kind": example["verifier_gate_config"].backend_kind,
            "mode": example["verifier_gate_config"].mode,
            "max_assessments": example["verifier_gate_config"].max_assessments,
            "required_verdicts": list(example["verifier_gate_config"].required_verdicts),
            "metadata": dict(example["verifier_gate_config"].metadata),
        },
        "judge_gate_config": {
            "backend_kind": example["judge_gate_config"].backend_kind,
            "mode": example["judge_gate_config"].mode,
            "max_assessments": example["judge_gate_config"].max_assessments,
            "required_verdicts": list(example["judge_gate_config"].required_verdicts),
            "metadata": dict(example["judge_gate_config"].metadata),
        },
        "verifier_outcome": {
            "pruned_candidate_ids": list(example["verifier_outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["verifier_outcome"].selected_candidate_id,
            "terminated": example["verifier_outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["verifier_outcome"].assessments],
        },
        "judge_outcome": {
            "pruned_candidate_ids": list(example["judge_outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["judge_outcome"].selected_candidate_id,
            "terminated": example["judge_outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["judge_outcome"].assessments],
        },
        "carry_state_id": example["carry_state_id"],
        "adjudicated_candidate_id": example["adjudicated_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_09_optimize_objective_breakdown_probe() -> Dict[str, object]:
    example = build_post_v2_study_08_verifier_judge_handoff()
    run = example["run"]
    selected_candidate_id = example["judge_outcome"].selected_candidate_id or run.selected_candidate_id
    if not selected_candidate_id:
        raise ValueError("study_09 requires a selected candidate id")
    final_assessment = example["judge_outcome"].assessments[0]
    objective_result = ObjectiveBreakdownResult(
        result_id="dag.study09.objective_breakdown",
        objective_suite_id="objective.dag.adapter.v1",
        manifest_id="manifest.dag.adapter.v1",
        candidate_id=selected_candidate_id,
        per_sample_components={
            "sample.verifier_judge_handoff": {
                "assessment_verdict": final_assessment.verdict,
                "correctness_score": 0.93,
                "coherence_score": 0.9,
                "verifier_gate_count": len(example["verifier_outcome"].assessments),
            }
        },
        per_bucket_components={
            "bucket.verifier_judge_handoff": {
                "assessment_count": len(run.assessments),
                "carry_state_count": len(run.carry_states),
            }
        },
        aggregate_objectives={"correctness": 0.93, "coherence": 0.9},
        signal_status={
            "exact_tests.v1": {"status": "complete", "count": len(example["verifier_outcome"].assessments)},
            "judge_pairwise.v1": {"status": "complete", "count": len(example["judge_outcome"].assessments)},
        },
        metadata={"source": "dag_post_v2_study_08", "outside_dag_kernel": True},
    )
    adapter_boundary = {
        "outside_dag_kernel": True,
        "introduced_optimize_public_nouns_into_dag": False,
        "used_real_optimize_records": True,
    }
    evidence = {
        "easy": [
            "DAG assessment truth can be mapped into ObjectiveBreakdownResult without mutating the DAG kernel",
            "multi-stage verifier and judge evidence survives the adapter boundary cleanly",
        ],
        "awkward": [
            "adapter-side objective aggregation still wants local conventions, but that remains outside DAG",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "objective_breakdown_result": objective_result,
        "selected_candidate_id": selected_candidate_id,
        "assessment_ids": [item.assessment_id for item in run.assessments],
        "adapter_boundary": adapter_boundary,
        "evidence": evidence,
    }


def build_post_v2_study_09_optimize_objective_breakdown_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_09_optimize_objective_breakdown_probe()
    return {
        "run": example["run"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "selected_candidate_id": example["selected_candidate_id"],
        "assessment_ids": list(example["assessment_ids"]),
        "adapter_boundary": dict(example["adapter_boundary"]),
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_10_optimize_benchmark_promotion_probe() -> Dict[str, object]:
    objective_example = build_post_v2_study_09_optimize_objective_breakdown_probe()
    run = objective_example["run"]
    selected_candidate_id = objective_example["selected_candidate_id"]
    manifest = BenchmarkRunManifest(
        manifest_id="manifest.dag.study10",
        benchmark_kind="dag_adapter_probe",
        target_id="search.verifier_judge_handoff",
        dataset_id="dataset.dag.study10",
        dataset_version="v1",
        baseline_candidate_id=run.candidates[0].candidate_id,
        environment_domain="dag_runtime",
        evaluator_stack=["exact_tests.v1", "judge_pairwise.v1", "trajectory_export.v1"],
        comparison_protocol="fixed_adapter_check",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=["sample.verifier_judge_handoff.train"],
                visibility="mutation_visible",
            ),
            BenchmarkSplit(
                split_name="hidden_eval",
                sample_ids=["sample.verifier_judge_handoff.hidden"],
                visibility="hidden_hold",
            ),
        ],
        bucket_tags={
            "sample.verifier_judge_handoff.train": ["verifier", "judge", "carry_state"],
            "sample.verifier_judge_handoff.hidden": ["judge", "hidden_hold"],
        },
        contamination_notes=["adapter-only probe; no optimize public noun enters DAG kernel"],
        metadata={"source": "dag_post_v2_study_08", "outside_dag_kernel": True},
    )
    summary = PromotionEvidenceSummary(
        summary_id="summary.dag.study10",
        candidate_id=selected_candidate_id,
        manifest_ids=[manifest.manifest_id],
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        compared_regression_sample_ids=["sample.verifier_judge_handoff.train"],
        outcome_counts={"non_inferior": 1},
        evaluation_suite_ids=["evaluation.dag.adapter.v1"],
        objective_suite_ids=[objective_example["objective_breakdown_result"].objective_suite_id],
        objective_breakdown_result_ids=[objective_example["objective_breakdown_result"].result_id],
        applicability_scope={"target_kind": "dag_runtime_adapter", "bounded_to": "study_08_verifier_judge_handoff"},
        review_class="adapter_only",
        objective_breakdown_status="complete",
        metadata={"source": "dag_post_v2_study_08", "outside_dag_kernel": True},
    )
    adapter_boundary = {
        "outside_dag_kernel": True,
        "introduced_optimize_public_nouns_into_dag": False,
        "used_real_optimize_records": True,
        "promotion_logic_stayed_adapter_local": True,
    }
    evidence = {
        "easy": [
            "DAG trajectory and assessment truth can populate BenchmarkRunManifest and PromotionEvidenceSummary cleanly",
            "promotion and benchmark evidence can stay adapter-local without expanding the DAG kernel",
        ],
        "awkward": [
            "adapter-level sample/bucket naming remains a consumer convention rather than a DAG runtime concept",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "objective_breakdown_result": objective_example["objective_breakdown_result"],
        "benchmark_manifest": manifest,
        "promotion_summary": summary,
        "selected_candidate_id": selected_candidate_id,
        "adapter_boundary": adapter_boundary,
        "evidence": evidence,
    }


def build_post_v2_study_10_optimize_benchmark_promotion_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_10_optimize_benchmark_promotion_probe()
    return {
        "run": example["run"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "benchmark_manifest": example["benchmark_manifest"].to_dict(),
        "promotion_summary": example["promotion_summary"].to_dict(),
        "selected_candidate_id": example["selected_candidate_id"],
        "adapter_boundary": dict(example["adapter_boundary"]),
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_11_branch_carry_hybrid() -> Dict[str, object]:
    study = build_post_v2_study_03_branch_execute_verify_deeper()
    run = study["run"]
    selected_candidate_id = run.selected_candidate_id
    if not selected_candidate_id:
        raise ValueError("study_11 requires a selected candidate from study_03")
    selected_candidate = next(item for item in run.candidates if item.candidate_id == selected_candidate_id)
    risky_candidate = next(item for item in run.candidates if item.candidate_id == study["risky_candidate_id"])
    compaction_registry: SearchCompactionRegistry = build_default_search_compaction_registry()
    message, carry_state = compaction_registry.compact(
        backend_kind="bounded_candidate_rollup.v1",
        search_id=run.search_id,
        carry_state_id=f"{run.search_id}.carry.branch_hybrid",
        message_id=f"{run.search_id}.msg.branch_hybrid",
        candidates=[selected_candidate, risky_candidate],
        metadata={"study_id": "study_11_branch_carry_hybrid", "phase": "post_v2_continuation"},
    )
    compaction_event = SearchEvent(
        event_id=f"{run.search_id}.event.compact.branch_hybrid",
        search_id=run.search_id,
        frontier_id=selected_candidate.frontier_id,
        round_index=selected_candidate.round_index,
        operator_kind="compact",
        input_candidate_ids=[selected_candidate.candidate_id, risky_candidate.candidate_id],
        message_ids=[message.message_id],
        metadata={"study_id": "study_11_branch_carry_hybrid", "carry_state_id": carry_state.state_id},
    )
    review_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.branch_hybrid.review",
        search_id=run.search_id,
        frontier_id=selected_candidate.frontier_id,
        parent_ids=[selected_candidate.candidate_id, risky_candidate.candidate_id],
        round_index=selected_candidate.round_index,
        depth=max(selected_candidate.depth, risky_candidate.depth) + 1,
        payload_ref=f"artifacts/search/{run.search_id}/branch_hybrid_review_candidate.json",
        workspace_ref=selected_candidate.workspace_ref,
        message_ref=message.message_id,
        score_vector={"correctness_score": 0.94, "coherence_score": 0.91},
        usage={"prompt_tokens": 88, "completion_tokens": 42},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/branch_hybrid_review_candidate.md",
        metadata={"study_id": "study_11_branch_carry_hybrid", "carry_state_id": carry_state.state_id},
    )
    aggregate_event = SearchEvent(
        event_id=f"{run.search_id}.event.aggregate.branch_hybrid",
        search_id=run.search_id,
        frontier_id=selected_candidate.frontier_id,
        round_index=selected_candidate.round_index,
        operator_kind="aggregate",
        input_candidate_ids=[selected_candidate.candidate_id, risky_candidate.candidate_id],
        output_candidate_ids=[review_candidate.candidate_id],
        message_ids=[message.message_id],
        metadata={"study_id": "study_11_branch_carry_hybrid", "hybrid_mode": "branch_plus_carry"},
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="branch_carry_hybrid_pressure_pass",
        candidates=[*run.candidates, review_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, compaction_event, aggregate_event],
        messages=[*run.messages, message],
        carry_states=[*run.carry_states, carry_state],
        assessments=list(run.assessments),
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_11_branch_carry_hybrid"},
    )
    gate_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"study_id": "study_11_branch_carry_hybrid", "phase": "post_v2_continuation"},
    )
    outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=build_default_search_assessment_registry(),
        config=gate_config,
        frontier_candidates=[review_candidate, selected_candidate],
    )
    final_events = [*study_run.events, outcome.gate_event]
    if outcome.selection_event is not None:
        final_events.append(outcome.selection_event)
    final_run = SearchRun(
        search_id=study_run.search_id,
        recipe_kind=study_run.recipe_kind,
        candidates=list(study_run.candidates),
        frontiers=list(study_run.frontiers),
        events=final_events,
        messages=list(study_run.messages),
        carry_states=list(study_run.carry_states),
        assessments=[*study_run.assessments, *outcome.assessments],
        workspace_snapshots=list(study_run.workspace_snapshots),
        branch_states=list(study_run.branch_states),
        metrics=study_run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "carry_state_id": carry_state.state_id,
            "terminated": outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "branch-local state and carry-state can coexist in one bounded search pass",
            "hybrid branch-plus-carry selection still fits the existing assessment gate surface",
            "no new hybrid orchestration primitive was needed",
        ],
        "awkward": [
            "hybrid reporting wants helper-level bundling, but the runtime truth surface remains sufficient",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "private_helper_level",
    }
    return {
        "run": final_run,
        "gate_config": gate_config,
        "outcome": outcome,
        "base_selected_candidate_id": selected_candidate.candidate_id,
        "carry_state_id": carry_state.state_id,
        "review_candidate_id": review_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_11_branch_carry_hybrid_payload() -> Dict[str, object]:
    example = build_post_v2_study_11_branch_carry_hybrid()
    return {
        "run": example["run"].to_dict(),
        "gate_config": {
            "backend_kind": example["gate_config"].backend_kind,
            "mode": example["gate_config"].mode,
            "max_assessments": example["gate_config"].max_assessments,
            "required_verdicts": list(example["gate_config"].required_verdicts),
            "metadata": dict(example["gate_config"].metadata),
        },
        "outcome": {
            "pruned_candidate_ids": list(example["outcome"].pruned_candidate_ids),
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "terminated": example["outcome"].terminated,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
        "base_selected_candidate_id": example["base_selected_candidate_id"],
        "carry_state_id": example["carry_state_id"],
        "review_candidate_id": example["review_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_12_optimize_comparison_probe() -> Dict[str, object]:
    study = build_post_v2_study_11_branch_carry_hybrid()
    run = study["run"]
    selected_candidate_id = study["outcome"].selected_candidate_id or run.selected_candidate_id
    if not selected_candidate_id:
        raise ValueError("study_12 requires a selected candidate id")
    manifest = BenchmarkRunManifest(
        manifest_id="manifest.dag.study12",
        benchmark_kind="dag_adapter_comparison_probe",
        target_id="search.branch_carry_hybrid",
        dataset_id="dataset.dag.study12",
        dataset_version="v1",
        baseline_candidate_id=run.candidates[0].candidate_id,
        environment_domain="dag_runtime",
        evaluator_stack=["judge_pairwise.v1", "trajectory_export.v1"],
        comparison_protocol="adapter_pairwise_protocol",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=["sample.branch_carry_hybrid.train"],
                visibility="comparison_visible",
            ),
            BenchmarkSplit(
                split_name="hidden_eval",
                sample_ids=["sample.branch_carry_hybrid.hidden"],
                visibility="hidden_hold",
            ),
        ],
        bucket_tags={
            "sample.branch_carry_hybrid.train": ["branch_state", "carry_state", "judge"],
            "sample.branch_carry_hybrid.hidden": ["hidden_hold", "judge"],
        },
        contamination_notes=["adapter-only comparison probe; no optimize noun enters DAG kernel"],
        metadata={"source": "dag_post_v2_study_11", "outside_dag_kernel": True},
    )
    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.dag.study12",
        parent_candidate_id=study["base_selected_candidate_id"],
        child_candidate_id=study["review_candidate_id"],
        outcome="non_inferior",
        compared_sample_ids=["sample.branch_carry_hybrid.train", "sample.branch_carry_hybrid.hidden"],
        held_out_sample_ids=["sample.branch_carry_hybrid.hidden"],
        trial_count=1,
        rationale="Adapter-local comparison shows the branch-plus-carry review candidate remains non-inferior under held-out pressure.",
        metric_deltas={"correctness": 0.03, "coherence": 0.02},
        metadata={"source": "dag_post_v2_study_11", "outside_dag_kernel": True},
    )
    adapter_boundary = {
        "outside_dag_kernel": True,
        "introduced_optimize_public_nouns_into_dag": False,
        "used_real_optimize_records": True,
        "comparison_logic_stayed_adapter_local": True,
    }
    evidence = {
        "easy": [
            "real optimize comparison records can be built from DAG V2 study outputs without kernel expansion",
            "held-out and compared-sample semantics stay on the optimize side",
        ],
        "awkward": [
            "adapter-level protocol naming and rationale formatting remain consumer concerns",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "benchmark_manifest": manifest,
        "comparison_result": comparison,
        "selected_candidate_id": selected_candidate_id,
        "adapter_boundary": adapter_boundary,
        "evidence": evidence,
    }


def build_post_v2_study_12_optimize_comparison_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_12_optimize_comparison_probe()
    return {
        "run": example["run"].to_dict(),
        "benchmark_manifest": example["benchmark_manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "selected_candidate_id": example["selected_candidate_id"],
        "adapter_boundary": dict(example["adapter_boundary"]),
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_13_multi_candidate_tournament() -> Dict[str, object]:
    study = build_post_v2_study_11_branch_carry_hybrid()
    run = study["run"]
    review_candidate = next(item for item in run.candidates if item.candidate_id == study["review_candidate_id"])
    incumbent_candidate = next(item for item in run.candidates if item.candidate_id == study["base_selected_candidate_id"])
    risky_candidate = next(item for item in run.candidates if item.candidate_id.endswith(".cand.branch.risky_patch"))
    registry = build_default_search_assessment_registry()
    semifinal_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"study_id": "study_13_multi_candidate_tournament", "phase": "post_v2_continuation"},
    )
    semifinal_outcome = run_barriered_assessment_gate(
        run=run,
        registry=registry,
        config=semifinal_config,
        frontier_candidates=[review_candidate, incumbent_candidate],
    )
    semifinal_winner_id = semifinal_outcome.selected_candidate_id or review_candidate.candidate_id
    semifinal_winner = next(item for item in run.candidates if item.candidate_id == semifinal_winner_id)
    final_config = AssessmentGateConfig(
        backend_kind="judge_pairwise.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["prefer_a"],
        metadata={"study_id": "study_13_multi_candidate_tournament", "phase": "post_v2_continuation"},
    )
    final_outcome = run_barriered_assessment_gate(
        run=run,
        registry=registry,
        config=final_config,
        frontier_candidates=[semifinal_winner, risky_candidate],
    )
    final_events = [*run.events, semifinal_outcome.gate_event]
    if semifinal_outcome.selection_event is not None:
        final_events.append(semifinal_outcome.selection_event)
    final_events.append(final_outcome.gate_event)
    if final_outcome.selection_event is not None:
        final_events.append(final_outcome.selection_event)
    final_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="multi_candidate_tournament_pressure_pass",
        candidates=list(run.candidates),
        frontiers=list(run.frontiers),
        events=final_events,
        messages=list(run.messages),
        carry_states=list(run.carry_states),
        assessments=[*run.assessments, *semifinal_outcome.assessments, *final_outcome.assessments],
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=final_outcome.selected_candidate_id,
        metadata={
            **dict(run.metadata),
            "tournament_semifinal_winner_id": semifinal_winner_id,
            "terminated": semifinal_outcome.terminated or final_outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "multi-candidate adjudication can be expressed as bounded sequential assessment gates",
            "tournament-style narrowing still fits the existing assessment/event surface",
            "no bracket or tournament public noun was needed",
        ],
        "awkward": [
            "higher-level tournament summaries still want helper/reporting code outside the kernel",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "private_helper_level",
    }
    return {
        "run": final_run,
        "semifinal_config": semifinal_config,
        "semifinal_outcome": semifinal_outcome,
        "final_config": final_config,
        "final_outcome": final_outcome,
        "review_candidate_id": review_candidate.candidate_id,
        "incumbent_candidate_id": incumbent_candidate.candidate_id,
        "risky_candidate_id": risky_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_13_multi_candidate_tournament_payload() -> Dict[str, object]:
    example = build_post_v2_study_13_multi_candidate_tournament()
    return {
        "run": example["run"].to_dict(),
        "semifinal_config": {
            "backend_kind": example["semifinal_config"].backend_kind,
            "mode": example["semifinal_config"].mode,
            "max_assessments": example["semifinal_config"].max_assessments,
            "required_verdicts": list(example["semifinal_config"].required_verdicts),
            "metadata": dict(example["semifinal_config"].metadata),
        },
        "semifinal_outcome": {
            "selected_candidate_id": example["semifinal_outcome"].selected_candidate_id,
            "assessment_ids": [item.assessment_id for item in example["semifinal_outcome"].assessments],
        },
        "final_config": {
            "backend_kind": example["final_config"].backend_kind,
            "mode": example["final_config"].mode,
            "max_assessments": example["final_config"].max_assessments,
            "required_verdicts": list(example["final_config"].required_verdicts),
            "metadata": dict(example["final_config"].metadata),
        },
        "final_outcome": {
            "selected_candidate_id": example["final_outcome"].selected_candidate_id,
            "assessment_ids": [item.assessment_id for item in example["final_outcome"].assessments],
        },
        "review_candidate_id": example["review_candidate_id"],
        "incumbent_candidate_id": example["incumbent_candidate_id"],
        "risky_candidate_id": example["risky_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_14_optimize_transfer_cohort_probe() -> Dict[str, object]:
    study = build_post_v2_study_12_optimize_comparison_probe()
    run = study["run"]
    selected_candidate_id = study["selected_candidate_id"]
    slices = [
        TransferSliceManifest(
            slice_id="slice.dag.codex_like",
            slice_kind="package",
            selector={"package_name": "codex_dossier_like"},
            promotion_role="required",
            visibility="comparison_visible",
            metadata={"source": "dag_post_v2_study_14", "outside_dag_kernel": True},
        ),
        TransferSliceManifest(
            slice_id="slice.dag.opencodish",
            slice_kind="package",
            selector={"package_name": "opencode_1_2_17_like"},
            promotion_role="claim_supporting",
            visibility="hidden_hold",
            metadata={"source": "dag_post_v2_study_14", "outside_dag_kernel": True},
        ),
    ]
    cohort = TransferCohortManifest(
        cohort_id="cohort.dag.study14",
        cohort_kind="paired_transfer_probe",
        member_slice_ids=[item.slice_id for item in slices],
        claim_scope={"bounded_to": "dag_runtime_adapter", "target_family": "branch_carry_hybrid"},
        coverage_policy={"requires_hidden_hold": True, "minimum_member_count": 2},
        metadata={"source": "dag_post_v2_study_14", "outside_dag_kernel": True},
    )
    summary = PromotionEvidenceSummary(
        summary_id="summary.dag.study14",
        candidate_id=selected_candidate_id,
        manifest_ids=[study["benchmark_manifest"].manifest_id],
        held_out_sample_ids=study["benchmark_manifest"].hidden_hold_sample_ids(),
        outcome_counts={"non_inferior": 1},
        evaluation_suite_ids=["evaluation.dag.transfer_probe.v1"],
        transfer_slice_ids=[item.slice_id for item in slices],
        transfer_slices=slices,
        transfer_cohort_ids=[cohort.cohort_id],
        transfer_cohorts=[cohort],
        transfer_cohort_status={
            cohort.cohort_id: {"status": "supported", "member_count": len(slices), "hidden_hold_covered": True}
        },
        transfer_slice_status={
            slices[0].slice_id: {"status": "pass", "role": slices[0].promotion_role},
            slices[1].slice_id: {"status": "pass", "role": slices[1].promotion_role},
        },
        claim_tier="cohort_supported",
        applicability_scope={"target_kind": "dag_runtime_adapter", "bounded_to": "study_11_branch_carry_hybrid"},
        review_class="adapter_only",
        objective_breakdown_status="complete",
        metadata={"source": "dag_post_v2_study_14", "outside_dag_kernel": True},
    )
    adapter_boundary = {
        "outside_dag_kernel": True,
        "introduced_optimize_public_nouns_into_dag": False,
        "used_real_optimize_records": True,
        "transfer_logic_stayed_adapter_local": True,
    }
    evidence = {
        "easy": [
            "real optimize transfer slices and cohorts can be populated from DAG study outputs without touching the DAG kernel",
            "cohort and claim-tier semantics remain adapter-side rather than becoming DAG concepts",
        ],
        "awkward": [
            "slice naming and claim-scope wording remain adapter conventions",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "transfer_slices": slices,
        "transfer_cohort": cohort,
        "promotion_summary": summary,
        "selected_candidate_id": selected_candidate_id,
        "adapter_boundary": adapter_boundary,
        "evidence": evidence,
    }


def build_post_v2_study_14_optimize_transfer_cohort_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_14_optimize_transfer_cohort_probe()
    return {
        "run": example["run"].to_dict(),
        "transfer_slices": [item.to_dict() for item in example["transfer_slices"]],
        "transfer_cohort": example["transfer_cohort"].to_dict(),
        "promotion_summary": example["promotion_summary"].to_dict(),
        "selected_candidate_id": example["selected_candidate_id"],
        "adapter_boundary": dict(example["adapter_boundary"]),
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }
