from __future__ import annotations

from typing import Dict, List, Sequence

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
