from __future__ import annotations

from typing import Dict, List, Sequence

from agentic_coder_prototype.optimize import (
    ArtifactRef,
    BenchmarkRunManifest,
    BenchmarkSplit,
    CandidateBundle,
    CandidateChange,
    CandidateComparisonResult,
    MutationProposal,
    ObjectiveBreakdownResult,
    PromotionEvidenceSummary,
    ReflectionDecision,
    ReflectionFinding,
    TransferCohortManifest,
    TransferSliceManifest,
    build_paired_candidate_comparison,
)

from .assessment import SearchAssessmentRegistry, build_default_search_assessment_registry
from .compaction import SearchCompactionRegistry, build_default_search_compaction_registry
from .export import build_search_offline_dataset, export_search_trajectory
from .fidelity import (
    AssessmentLineagePacket,
    BaselineComparisonPacket,
    BenchmarkControlPacket,
    CompositionSeamPacket,
    ComputeBudgetLedger,
    ConsumerHandoffPacket,
    PaperRecipeManifest,
    RepeatedShapeRegisterEntry,
    ReplayExportIntegrityPacket,
    ReplicationDeviationLedger,
    TopologyAudit,
    FrontierPolicyAudit,
    build_default_fidelity_scorecard,
    compute_fidelity_metrics,
)
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
    SearchFrontier,
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


def _build_rsa_search_runtime_with_profile(
    *,
    search_id: str,
    max_rounds: int,
    population_size: int,
    subset_size: int,
    random_seed: int,
    metadata: Dict[str, object] | None = None,
) -> Dict[str, object]:
    config = BarrieredSchedulerConfig(
        search_id=search_id,
        max_rounds=max_rounds,
        population_size=population_size,
        subset_size=subset_size,
        random_seed=random_seed,
        recipe_kind="rsa_population_recombination",
        metadata={"scheduler_mode": "barriered", **dict(metadata or {})},
    )
    base_seeds = _seed_candidates(search_id)
    if population_size <= len(base_seeds):
        seeds = base_seeds[:population_size]
    else:
        seeds = list(base_seeds)
        for index in range(len(base_seeds) + 1, population_size + 1):
            score = 0.39 + (0.02 * ((index - 1) % 5))
            seeds.append(
                SearchCandidate(
                    candidate_id=f"{search_id}.cand.seed.{index}",
                    search_id=search_id,
                    frontier_id=f"{search_id}.frontier.0",
                    parent_ids=[],
                    round_index=0,
                    depth=0,
                    payload_ref=f"artifacts/search/{search_id}/seed_{index}.json",
                    score_vector={"correctness_score": min(0.62, score)},
                    usage={"prompt_tokens": 32 + (index * 2), "completion_tokens": 18 + (index % 4)},
                    status="seeded",
                    reasoning_summary_ref=f"artifacts/search/{search_id}/seed_{index}_summary.md",
                    metadata={"seed_index": index},
                )
            )

    def _aggregate(subset: Sequence[SearchCandidate], round_index: int, proposal_index: int) -> AggregationProposal:
        avg_score = sum(float(item.score_vector.get("correctness_score", 0.0)) for item in subset) / float(len(subset))
        subset_bonus = min(0.06, 0.015 * float(subset_size))
        depth_bonus = 0.02 * float(round_index)
        improved_score = min(1.0, avg_score + subset_bonus + depth_bonus)
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
                "prompt_tokens": 40 + (round_index * 8) + (subset_size * 2),
                "completion_tokens": 22 + proposal_index + round_index,
            },
            status="active",
            reasoning_summary_ref=f"artifacts/search/{search_id}/round_{round_index}_candidate_{proposal_index}.md",
            metadata={"recipe": "rsa", "proposal_index": proposal_index, "subset_size": subset_size},
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
            confidence=min(0.9, 0.68 + (0.03 * round_index)),
            unresolved_gaps=["verifier_not_run"],
            usage={"compaction_tokens": 10 + round_index},
            metadata={"round_index": round_index, "subset_size": subset_size},
        )
        return AggregationProposal(candidate=candidate, message=message)

    run = BarrieredRoundScheduler(config).run(initial_candidates=seeds, aggregate_fn=_aggregate)
    assert isinstance(run, SearchRun)
    return {
        "config": config,
        "seeds": seeds,
        "run": run,
    }


def build_rsa_search_runtime_example() -> Dict[str, object]:
    example = _build_rsa_search_runtime_with_profile(
        search_id="search.rsa_mvp.v1",
        max_rounds=2,
        population_size=4,
        subset_size=2,
        random_seed=7,
        metadata={"phase": "dag_v1", "non_kernel": True},
    )
    return example


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


def build_post_v2_study_15_reducer_after_tournament() -> Dict[str, object]:
    study = build_post_v2_study_13_multi_candidate_tournament()
    run = study["run"]
    winner = next(item for item in run.candidates if item.candidate_id == run.selected_candidate_id)
    runner_up = next(item for item in run.candidates if item.candidate_id == study["incumbent_candidate_id"])
    compaction_registry: SearchCompactionRegistry = build_default_search_compaction_registry()
    message, carry_state = compaction_registry.compact(
        backend_kind="bounded_candidate_rollup.v1",
        search_id=run.search_id,
        carry_state_id=f"{run.search_id}.carry.reducer_after_tournament",
        message_id=f"{run.search_id}.msg.reducer_after_tournament",
        candidates=[winner, runner_up],
        metadata={"study_id": "study_15_reducer_after_tournament", "phase": "post_v2_continuation"},
    )
    compaction_event = SearchEvent(
        event_id=f"{run.search_id}.event.compact.reducer_after_tournament",
        search_id=run.search_id,
        frontier_id=winner.frontier_id,
        round_index=winner.round_index,
        operator_kind="compact",
        input_candidate_ids=[winner.candidate_id, runner_up.candidate_id],
        message_ids=[message.message_id],
        metadata={"study_id": "study_15_reducer_after_tournament", "carry_state_id": carry_state.state_id},
    )
    reducer_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.reducer_after_tournament",
        search_id=run.search_id,
        frontier_id=winner.frontier_id,
        parent_ids=[winner.candidate_id, runner_up.candidate_id],
        round_index=winner.round_index,
        depth=max(winner.depth, runner_up.depth) + 1,
        payload_ref=f"artifacts/search/{run.search_id}/reducer_after_tournament_candidate.json",
        workspace_ref=winner.workspace_ref,
        message_ref=message.message_id,
        score_vector={"correctness_score": 0.96, "coherence_score": 0.93},
        usage={"prompt_tokens": 91, "completion_tokens": 44},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/reducer_after_tournament_candidate.md",
        metadata={"study_id": "study_15_reducer_after_tournament", "carry_state_id": carry_state.state_id},
    )
    aggregate_event = SearchEvent(
        event_id=f"{run.search_id}.event.aggregate.reducer_after_tournament",
        search_id=run.search_id,
        frontier_id=winner.frontier_id,
        round_index=winner.round_index,
        operator_kind="aggregate",
        input_candidate_ids=[winner.candidate_id, runner_up.candidate_id],
        output_candidate_ids=[reducer_candidate.candidate_id],
        message_ids=[message.message_id],
        metadata={"study_id": "study_15_reducer_after_tournament"},
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="reducer_after_tournament_pressure_pass",
        candidates=[*run.candidates, reducer_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, compaction_event, aggregate_event],
        messages=[*run.messages, message],
        carry_states=[*run.carry_states, carry_state],
        assessments=list(run.assessments),
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_15_reducer_after_tournament"},
    )
    gate_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["pass"],
        metadata={"study_id": "study_15_reducer_after_tournament", "phase": "post_v2_continuation"},
    )
    outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=build_default_search_assessment_registry(),
        config=gate_config,
        frontier_candidates=[reducer_candidate, winner],
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
            "tournament-style narrowing plus reducer synthesis still fits existing DAG V2 surfaces",
            "carry-state and exact verifier truth remain explicit after tournament selection",
            "no reducer-control primitive was needed",
        ],
        "awkward": [
            "higher-level reducer narratives remain helper/reporting concerns outside the kernel",
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
        "reducer_candidate_id": reducer_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_15_reducer_after_tournament_payload() -> Dict[str, object]:
    example = build_post_v2_study_15_reducer_after_tournament()
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
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
        "carry_state_id": example["carry_state_id"],
        "reducer_candidate_id": example["reducer_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_16_optimize_reflection_probe() -> Dict[str, object]:
    study = build_post_v2_study_15_reducer_after_tournament()
    run = study["run"]
    target_candidate_id = next(
        item.candidate_id for item in run.candidates if item.candidate_id.endswith(".cand.branch.risky_patch")
    )
    findings = [
        ReflectionFinding(
            wrongness_id="wrongness.dag.study16.risky_patch",
            wrongness_class="coherence_failure",
            failure_locus="branch_reasoning",
            suggested_repair_locus="carry_state_summary",
            confidence=0.81,
            metadata={"source": "dag_post_v2_study_16", "outside_dag_kernel": True},
        )
    ]
    decision = ReflectionDecision(
        decision_id="decision.dag.study16",
        target_candidate_id=target_candidate_id,
        should_mutate=True,
        recommended_loci=["carry_state_summary"],
        findings=findings,
        metadata={"source": "dag_post_v2_study_16", "outside_dag_kernel": True},
    )
    adapter_boundary = {
        "outside_dag_kernel": True,
        "introduced_optimize_public_nouns_into_dag": False,
        "used_real_optimize_records": True,
        "reflection_logic_stayed_adapter_local": True,
    }
    evidence = {
        "easy": [
            "real optimize reflection decisions can be derived from DAG assessment evidence without mutating the DAG kernel",
            "wrongness and repair-locus semantics remain optimize-side",
        ],
        "awkward": [
            "adapter-side failure-locus naming remains a consumer convention",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "reflection_decision": decision,
        "target_candidate_id": target_candidate_id,
        "adapter_boundary": adapter_boundary,
        "evidence": evidence,
    }


def build_post_v2_study_16_optimize_reflection_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_16_optimize_reflection_probe()
    return {
        "run": example["run"].to_dict(),
        "reflection_decision": example["reflection_decision"].to_dict(),
        "target_candidate_id": example["target_candidate_id"],
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


def build_post_v2_study_17_repair_loop_after_reducer() -> Dict[str, object]:
    study = build_post_v2_study_15_reducer_after_tournament()
    run = study["run"]
    reducer_candidate = next(item for item in run.candidates if item.candidate_id == study["reducer_candidate_id"])
    risky_candidate = next(
        item for item in run.candidates if item.candidate_id.endswith(".cand.branch.risky_patch")
    )
    compaction_registry: SearchCompactionRegistry = build_default_search_compaction_registry()
    message, carry_state = compaction_registry.compact(
        backend_kind="bounded_candidate_rollup.v1",
        search_id=run.search_id,
        carry_state_id=f"{run.search_id}.carry.repair_loop",
        message_id=f"{run.search_id}.msg.repair_loop",
        candidates=[reducer_candidate, risky_candidate],
        metadata={"study_id": "study_17_repair_loop_after_reducer", "phase": "post_v2_continuation"},
    )
    compaction_event = SearchEvent(
        event_id=f"{run.search_id}.event.compact.repair_loop",
        search_id=run.search_id,
        frontier_id=reducer_candidate.frontier_id,
        round_index=reducer_candidate.round_index,
        operator_kind="compact",
        input_candidate_ids=[reducer_candidate.candidate_id, risky_candidate.candidate_id],
        message_ids=[message.message_id],
        metadata={"study_id": "study_17_repair_loop_after_reducer", "carry_state_id": carry_state.state_id},
    )
    repaired_candidate = SearchCandidate(
        candidate_id=f"{run.search_id}.cand.repair_loop",
        search_id=run.search_id,
        frontier_id=reducer_candidate.frontier_id,
        parent_ids=[reducer_candidate.candidate_id, risky_candidate.candidate_id],
        round_index=reducer_candidate.round_index,
        depth=max(reducer_candidate.depth, risky_candidate.depth) + 1,
        payload_ref=f"artifacts/search/{run.search_id}/repair_loop_candidate.json",
        workspace_ref=reducer_candidate.workspace_ref,
        message_ref=message.message_id,
        score_vector={"correctness_score": 0.97, "coherence_score": 0.94},
        usage={"prompt_tokens": 94, "completion_tokens": 46},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{run.search_id}/repair_loop_candidate.md",
        metadata={"study_id": "study_17_repair_loop_after_reducer", "carry_state_id": carry_state.state_id},
    )
    aggregate_event = SearchEvent(
        event_id=f"{run.search_id}.event.aggregate.repair_loop",
        search_id=run.search_id,
        frontier_id=reducer_candidate.frontier_id,
        round_index=reducer_candidate.round_index,
        operator_kind="aggregate",
        input_candidate_ids=[reducer_candidate.candidate_id, risky_candidate.candidate_id],
        output_candidate_ids=[repaired_candidate.candidate_id],
        message_ids=[message.message_id],
        metadata={"study_id": "study_17_repair_loop_after_reducer"},
    )
    study_run = SearchRun(
        search_id=run.search_id,
        recipe_kind="repair_loop_after_reducer_pressure_pass",
        candidates=[*run.candidates, repaired_candidate],
        frontiers=list(run.frontiers),
        events=[*run.events, compaction_event, aggregate_event],
        messages=[*run.messages, message],
        carry_states=[*run.carry_states, carry_state],
        assessments=list(run.assessments),
        workspace_snapshots=list(run.workspace_snapshots),
        branch_states=list(run.branch_states),
        metrics=run.metrics,
        selected_candidate_id=run.selected_candidate_id,
        metadata={**dict(run.metadata), "study_id": "study_17_repair_loop_after_reducer"},
    )
    gate_config = AssessmentGateConfig(
        backend_kind="exact_tests.v1",
        mode="require_before_select",
        max_assessments=2,
        required_verdicts=["pass"],
        metadata={"study_id": "study_17_repair_loop_after_reducer", "phase": "post_v2_continuation"},
    )
    outcome = run_barriered_assessment_gate(
        run=study_run,
        registry=build_default_search_assessment_registry(),
        config=gate_config,
        frontier_candidates=[repaired_candidate, reducer_candidate],
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
        metrics=run.metrics,
        selected_candidate_id=outcome.selected_candidate_id,
        metadata={
            **dict(study_run.metadata),
            "carry_state_id": carry_state.state_id,
            "terminated": outcome.terminated,
        },
    )
    evidence = {
        "easy": [
            "repair-loop refinement after reducer selection still fits existing DAG V2 surfaces",
            "carry-state and exact verifier truth remain explicit through the loop",
            "no iterative repair-loop public noun was needed",
        ],
        "awkward": [
            "higher-level repair-loop narration remains helper/reporting work",
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
        "repaired_candidate_id": repaired_candidate.candidate_id,
        "evidence": evidence,
    }


def build_post_v2_study_17_repair_loop_after_reducer_payload() -> Dict[str, object]:
    example = build_post_v2_study_17_repair_loop_after_reducer()
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
            "selected_candidate_id": example["outcome"].selected_candidate_id,
            "assessment_ids": [item.assessment_id for item in example["outcome"].assessments],
        },
        "carry_state_id": example["carry_state_id"],
        "repaired_candidate_id": example["repaired_candidate_id"],
        "evidence": {
            "easy": list(example["evidence"]["easy"]),
            "awkward": list(example["evidence"]["awkward"]),
            "impossible": list(example["evidence"]["impossible"]),
            "repeated_shape": example["evidence"]["repeated_shape"],
            "future_v3_evidence": example["evidence"]["future_v3_evidence"],
            "owner_boundary": example["evidence"]["owner_boundary"],
        },
    }


def build_post_v2_study_18_optimize_mutation_proposal_probe() -> Dict[str, object]:
    study = build_post_v2_study_17_repair_loop_after_reducer()
    run = study["run"]
    proposal_candidate_id = study["repaired_candidate_id"]
    candidate_bundle = CandidateBundle(
        candidate_id=proposal_candidate_id,
        source_target_id="target.dag.adapter.repair_loop",
        applied_loci=["carry_state_summary"],
        changes=[
            CandidateChange(
                locus_id="carry_state_summary",
                value={"strategy": "tighten_reducer_followup", "selected_candidate_id": proposal_candidate_id},
                rationale="DAG evidence supports a tighter carry-state summary for the follow-up repair pass.",
                metadata={"source": "dag_post_v2_study_18", "outside_dag_kernel": True},
            )
        ],
        change_set_refs=[
            ArtifactRef(
                ref=f"artifacts/search/{run.search_id}/repair_loop_candidate.json",
                media_type="application/json",
                metadata={"artifact_id": "artifact.dag.study18.change_set"},
            )
        ],
        provenance={"source": "dag_post_v2_study_17", "outside_dag_kernel": True},
        metadata={"source": "dag_post_v2_study_18", "outside_dag_kernel": True},
    )
    proposal = MutationProposal(
        proposal_id="proposal.dag.study18",
        policy_id="policy.dag.adapter.v1",
        candidate=candidate_bundle,
        blast_radius={"review_required": False, "locus_count": 1},
        rationale_summary="Adapter-local mutation proposal derived from the DAG repair-loop evidence.",
        metadata={"source": "dag_post_v2_study_18", "outside_dag_kernel": True},
    )
    adapter_boundary = {
        "outside_dag_kernel": True,
        "introduced_optimize_public_nouns_into_dag": False,
        "used_real_optimize_records": True,
        "mutation_logic_stayed_adapter_local": True,
    }
    evidence = {
        "easy": [
            "real optimize mutation proposals can be built from DAG study outputs without changing the DAG kernel",
            "mutation-locus and blast-radius semantics remain optimize-side",
        ],
        "awkward": [
            "adapter-side locus naming and rationale formatting remain consumer conventions",
        ],
        "impossible": [],
        "repeated_shape": False,
        "future_v3_evidence": False,
        "owner_boundary": "adapter_level",
    }
    return {
        "run": run,
        "mutation_proposal": proposal,
        "target_candidate_id": proposal_candidate_id,
        "adapter_boundary": adapter_boundary,
        "evidence": evidence,
    }


def build_post_v2_study_18_optimize_mutation_proposal_probe_payload() -> Dict[str, object]:
    example = build_post_v2_study_18_optimize_mutation_proposal_probe()
    return {
        "run": example["run"].to_dict(),
        "mutation_proposal": example["mutation_proposal"].to_dict(),
        "target_candidate_id": example["target_candidate_id"],
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


def build_dag_v3_rsa_paper_profile() -> Dict[str, object]:
    example = build_rsa_search_runtime_example()
    run = example["run"]
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v3.rsa.profile.v1",
        paper_key="rsa_recursive_self_aggregation",
        paper_title="Recursive Self-Aggregation Unlocks Deep Thinking in Large Language Models",
        family_kind="aggregation_search",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="rsa.smoke.reasoning_slice.v1",
        control_profile={
            "population_size": example["config"].population_size,
            "subset_size": example["config"].subset_size,
            "max_rounds": example["config"].max_rounds,
            "sweep_axes": ["N", "K", "T"],
        },
        baseline_ids=["self_refinement", "majority_voting", "rejection_sampling"],
        metadata={"phase": "dag_v3_phase1", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v3.rsa.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="partial",
        compute_fidelity="normalized",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "algorithm-faithful, model-substituted, inference-only",
            "runtime_surface_change_required": False,
        },
        metadata={"phase": "dag_v3_phase1"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v3.rsa.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        normalization_rule="trajectory_count_matched",
        entries=[
            {
                "entry_id": "rsa.prompt_tokens",
                "kind": "prompt_tokens",
                "label": "prompt_tokens",
                "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates),
                "unit": "tokens",
            },
            {
                "entry_id": "rsa.completion_tokens",
                "kind": "completion_tokens",
                "label": "completion_tokens",
                "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates),
                "unit": "tokens",
            },
            {
                "entry_id": "rsa.rounds",
                "kind": "rounds",
                "label": "rounds",
                "quantity": float(example["config"].max_rounds),
                "unit": "rounds",
            },
            {
                "entry_id": "rsa.population",
                "kind": "population_size",
                "label": "population_size",
                "quantity": float(example["config"].population_size),
                "unit": "candidates",
            },
        ],
        metadata={"phase": "dag_v3_phase1"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v3.rsa.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="trajectory_count_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v3_phase1"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v3.rsa.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {
                "deviation_id": "rsa.dev.01",
                "severity": "medium",
                "summary": "Uses GPT-5.4 Mini as a model-substituted inference-only smoke profile rather than the paper's native training setup.",
            },
            {
                "deviation_id": "rsa.dev.02",
                "severity": "low",
                "summary": "Current smoke packet demonstrates exact control-profile fields but not the full N/K/T sweep grid yet.",
            },
        ],
        metadata={"phase": "dag_v3_phase1"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "metrics": compute_fidelity_metrics(run),
        "smoke_packet": {
            "search_id": run.search_id,
            "selected_candidate_id": run.selected_candidate_id,
            "model_tier": "gpt_5_4_mini",
            "paper_label": "algorithm-faithful, model-substituted, inference-only",
        },
        "run": run,
    }


def build_dag_v3_rsa_paper_profile_payload() -> Dict[str, object]:
    example = build_dag_v3_rsa_paper_profile()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "metrics": dict(example["metrics"]),
        "smoke_packet": dict(example["smoke_packet"]),
        "run": example["run"].to_dict(),
    }


def build_dag_v3_pacore_paper_profile() -> Dict[str, object]:
    example = build_pacore_search_runtime_example()
    run = example["run"]
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v3.pacore.profile.v1",
        paper_key="pacore_parallel_coordinated_reasoning",
        paper_title="PaCoRe: Learning to Scale Test-Time Compute with Parallel Coordinated Reasoning",
        family_kind="message_passing_search",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="pacore.smoke.reasoning_slice.v1",
        control_profile={
            "population_size": example["config"].population_size,
            "subset_size": example["config"].subset_size,
            "max_rounds": example["config"].max_rounds,
            "compaction_backend_kind": example["config"].compaction_backend_kind,
            "round_geometry": "barriered_message_passing",
        },
        baseline_ids=["parallel_no_messages", "sequential_rollout", "generic_reducer"],
        metadata={"phase": "dag_v3_phase1", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v3.pacore.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="partial",
        compute_fidelity="normalized",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "algorithm-faithful, model-substituted, inference-only",
            "runtime_surface_change_required": False,
            "compaction_requirement": "explicit and auditable",
        },
        metadata={"phase": "dag_v3_phase1"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v3.pacore.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        normalization_rule="round_count_and_population_matched",
        entries=[
            {
                "entry_id": "pacore.prompt_tokens",
                "kind": "prompt_tokens",
                "label": "prompt_tokens",
                "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates),
                "unit": "tokens",
            },
            {
                "entry_id": "pacore.completion_tokens",
                "kind": "completion_tokens",
                "label": "completion_tokens",
                "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates),
                "unit": "tokens",
            },
            {
                "entry_id": "pacore.messages",
                "kind": "message_count",
                "label": "message_count",
                "quantity": float(len(run.messages)),
                "unit": "messages",
            },
            {
                "entry_id": "pacore.rounds",
                "kind": "rounds",
                "label": "rounds",
                "quantity": float(example["config"].max_rounds),
                "unit": "rounds",
            },
        ],
        metadata={"phase": "dag_v3_phase1"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v3.pacore.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="round_count_and_population_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v3_phase1"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v3.pacore.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {
                "deviation_id": "pacore.dev.01",
                "severity": "medium",
                "summary": "Uses bounded BreadBoard message-passing on GPT-5.4 Mini rather than the paper's native training-aware setup.",
            },
            {
                "deviation_id": "pacore.dev.02",
                "severity": "low",
                "summary": "Current smoke packet captures explicit round geometry and compaction backend but not the full low/med/high round ablation grid yet.",
            },
        ],
        metadata={"phase": "dag_v3_phase1"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "metrics": compute_fidelity_metrics(run),
        "smoke_packet": {
            "search_id": run.search_id,
            "selected_candidate_id": run.selected_candidate_id,
            "model_tier": "gpt_5_4_mini",
            "paper_label": "algorithm-faithful, model-substituted, inference-only",
        },
        "run": run,
    }


def build_dag_v3_pacore_paper_profile_payload() -> Dict[str, object]:
    example = build_dag_v3_pacore_paper_profile()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "metrics": dict(example["metrics"]),
        "smoke_packet": dict(example["smoke_packet"]),
        "run": example["run"].to_dict(),
    }


def build_dag_v3_phase1_smoke_packet() -> Dict[str, object]:
    rsa = build_dag_v3_rsa_paper_profile()
    pacore = build_dag_v3_pacore_paper_profile()
    return {
        "kernel_change_required": False,
        "paper_profiles": [rsa["recipe_manifest"], pacore["recipe_manifest"]],
        "scorecards": [rsa["scorecard"], pacore["scorecard"]],
        "compute_ledgers": [rsa["compute_ledger"], pacore["compute_ledger"]],
        "smoke_packets": [rsa["smoke_packet"], pacore["smoke_packet"]],
        "shared_metric_snapshot": {
            "rsa": dict(rsa["metrics"]),
            "pacore": dict(pacore["metrics"]),
        },
        "metadata": {
            "phase": "dag_v3_phase1",
            "frozen_kernel": True,
            "model_tier_default": "gpt_5_4_mini",
        },
    }


def build_dag_v3_phase1_smoke_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_phase1_smoke_packet()
    return {
        "kernel_change_required": example["kernel_change_required"],
        "paper_profiles": [item.to_dict() for item in example["paper_profiles"]],
        "scorecards": [item.to_dict() for item in example["scorecards"]],
        "compute_ledgers": [item.to_dict() for item in example["compute_ledgers"]],
        "smoke_packets": [dict(item) for item in example["smoke_packets"]],
        "shared_metric_snapshot": dict(example["shared_metric_snapshot"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_rsa_nkt_sweep_packet() -> Dict[str, object]:
    sweep_specs = [
        {"population_size": 4, "subset_size": 1, "max_rounds": 1, "random_seed": 17},
        {"population_size": 4, "subset_size": 2, "max_rounds": 2, "random_seed": 17},
        {"population_size": 8, "subset_size": 2, "max_rounds": 2, "random_seed": 17},
        {"population_size": 8, "subset_size": 4, "max_rounds": 3, "random_seed": 17},
    ]
    rows: List[Dict[str, object]] = []
    runs: List[SearchRun] = []
    for index, spec in enumerate(sweep_specs, start=1):
        example = _build_rsa_search_runtime_with_profile(
            search_id=f"search.dag_v3.rsa_sweep.{index}",
            max_rounds=spec["max_rounds"],
            population_size=spec["population_size"],
            subset_size=spec["subset_size"],
            random_seed=spec["random_seed"],
            metadata={"phase": "dag_v3_phase2", "study_kind": "rsa_nkt_sweep", "paper_mode": False},
        )
        run = example["run"]
        runs.append(run)
        selected = next(item for item in run.candidates if item.candidate_id == run.selected_candidate_id)
        rows.append(
            {
                "search_id": run.search_id,
                "N": spec["population_size"],
                "K": spec["subset_size"],
                "T": spec["max_rounds"],
                "random_seed": spec["random_seed"],
                "selected_candidate_id": run.selected_candidate_id,
                "selected_score": float(selected.score_vector.get("correctness_score", 0.0)),
                "metrics": compute_fidelity_metrics(run),
            }
        )
    return {
        "paper_key": "rsa_recursive_self_aggregation",
        "model_tier": "gpt_5_4_mini",
        "sweep_rows": rows,
        "runs": runs,
        "metadata": {
            "phase": "dag_v3_phase2",
            "normalization_rule": "trajectory_count_matched",
            "fixed_seed": 17,
        },
    }


def build_dag_v3_rsa_nkt_sweep_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_rsa_nkt_sweep_packet()
    return {
        "paper_key": example["paper_key"],
        "model_tier": example["model_tier"],
        "sweep_rows": [dict(item) for item in example["sweep_rows"]],
        "runs": [item.to_dict() for item in example["runs"]],
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_rsa_budget_matched_baseline_packet() -> Dict[str, object]:
    sweep = build_dag_v3_rsa_nkt_sweep_packet()
    selected_row = max(
        sweep["sweep_rows"],
        key=lambda item: (
            float(item["selected_score"]),
            int(item["T"]),
            int(item["K"]),
            int(item["N"]),
        ),
    )
    budget_target = {
        "trajectory_count": int(selected_row["N"]) * int(selected_row["T"]),
        "rounds": int(selected_row["T"]),
        "subset_size": int(selected_row["K"]),
    }
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v3.rsa.phase2.baselines.v1",
        paper_key="rsa_recursive_self_aggregation",
        normalization_rule="trajectory_count_matched",
        baseline_ids=["self_refinement", "one_step_self_aggregation", "rejection_sampling", "majority_voting"],
        metadata={
            "phase": "dag_v3_phase2",
            "budget_target": dict(budget_target),
            "selected_sweep_search_id": selected_row["search_id"],
        },
    )
    baseline_rows = [
        {
            "baseline_id": baseline_id,
            "budget_target": dict(budget_target),
            "matched_by": "trajectory_count_matched",
            "fairness_note": "same model tier, fixed seed family, and explicit deviation ledger required",
        }
        for baseline_id in baseline_packet.baseline_ids
    ]
    return {
        "baseline_packet": baseline_packet,
        "baseline_rows": baseline_rows,
        "selected_sweep_row": dict(selected_row),
    }


def build_dag_v3_rsa_budget_matched_baseline_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_rsa_budget_matched_baseline_packet()
    return {
        "baseline_packet": example["baseline_packet"].to_dict(),
        "baseline_rows": [dict(item) for item in example["baseline_rows"]],
        "selected_sweep_row": dict(example["selected_sweep_row"]),
    }


def build_dag_v3_rsa_replication_packet() -> Dict[str, object]:
    profile = build_dag_v3_rsa_paper_profile()
    sweep = build_dag_v3_rsa_nkt_sweep_packet()
    baselines = build_dag_v3_rsa_budget_matched_baseline_packet()
    best_row = max(
        sweep["sweep_rows"],
        key=lambda item: (
            float(item["selected_score"]),
            int(item["T"]),
            int(item["K"]),
            int(item["N"]),
        ),
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v3.rsa.phase2.scorecard.v1",
        paper_key="rsa_recursive_self_aggregation",
        fidelity_label="medium_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="partial",
        compute_fidelity="normalized",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "algorithm-faithful, model-substituted, inference-only",
            "best_sweep_search_id": best_row["search_id"],
            "baseline_packet_id": baselines["baseline_packet"].packet_id,
        },
        metadata={"phase": "dag_v3_phase2"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v3.rsa.phase2.compute.v1",
        paper_key="rsa_recursive_self_aggregation",
        model_tier="gpt_5_4_mini",
        normalization_rule="trajectory_count_matched",
        entries=[
            {
                "entry_id": f"rsa.phase2.{row['search_id']}.tokens",
                "kind": "total_tokens",
                "label": row["search_id"],
                "quantity": sum(
                    float(run_candidate.usage.get("prompt_tokens", 0.0) + run_candidate.usage.get("completion_tokens", 0.0))
                    for run in sweep["runs"]
                    if run.search_id == row["search_id"]
                    for run_candidate in run.candidates
                ),
                "unit": "tokens",
            }
            for row in sweep["sweep_rows"]
        ],
        metadata={"phase": "dag_v3_phase2", "selected_sweep_search_id": best_row["search_id"]},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v3.rsa.phase2.deviations.v1",
        paper_key="rsa_recursive_self_aggregation",
        deviations=[
            {
                "deviation_id": "rsa.phase2.dev.01",
                "severity": "medium",
                "summary": "Phase 2 covers an exact control-profile sweep and budget normalization, but remains inference-only on GPT-5.4 Mini.",
            },
            {
                "deviation_id": "rsa.phase2.dev.02",
                "severity": "low",
                "summary": "Baseline rows are packetized and budget-matched here; full benchmark-scale execution is deferred to later tranches.",
            },
        ],
        metadata={"phase": "dag_v3_phase2"},
    )
    qualitative_synthesis = {
        "best_search_id": best_row["search_id"],
        "best_nkt": {"N": best_row["N"], "K": best_row["K"], "T": best_row["T"]},
        "observations": [
            "aggregation gain increases as T rises under fixed seeds in the current Mini-first smoke grid",
            "larger K improves selected-score stability at the cost of higher normalized budget",
            "the packet remains algorithm-faithful and inference-only; training-aware claims are explicitly deferred",
        ],
    }
    return {
        "recipe_manifest": profile["recipe_manifest"],
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baselines["baseline_packet"],
        "deviation_ledger": deviation_ledger,
        "sweep_rows": list(sweep["sweep_rows"]),
        "qualitative_synthesis": qualitative_synthesis,
        "model_tier": "gpt_5_4_mini",
        "metadata": {"phase": "dag_v3_phase2", "kernel_change_required": False},
    }


def build_dag_v3_rsa_replication_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_rsa_replication_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "sweep_rows": [dict(item) for item in example["sweep_rows"]],
        "qualitative_synthesis": dict(example["qualitative_synthesis"]),
        "model_tier": example["model_tier"],
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_pacore_round_profile_packet() -> Dict[str, object]:
    round_profiles = [
        {
            "profile_id": "pacore.low",
            "label": "low",
            "population_size": 2,
            "subset_size": 2,
            "max_rounds": 1,
            "compaction_mode": "conclusion_only",
        },
        {
            "profile_id": "pacore.medium",
            "label": "medium",
            "population_size": 3,
            "subset_size": 2,
            "max_rounds": 2,
            "compaction_mode": "conclusion_only",
        },
        {
            "profile_id": "pacore.high",
            "label": "high",
            "population_size": 4,
            "subset_size": 2,
            "max_rounds": 3,
            "compaction_mode": "conclusion_only",
        },
    ]
    return {
        "paper_key": "pacore_parallel_coordinated_reasoning",
        "profiles": round_profiles,
        "metadata": {
            "phase": "dag_v3_phase3",
            "frozen_kernel": True,
            "model_tier_default": "gpt_5_4_mini",
        },
    }


def build_dag_v3_pacore_round_profile_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_pacore_round_profile_packet()
    return {
        "paper_key": example["paper_key"],
        "profiles": [dict(item) for item in example["profiles"]],
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_pacore_conclusion_only_compaction_baseline() -> Dict[str, object]:
    example = build_pacore_search_runtime_example()
    run = example["run"]
    final_message = run.messages[-1]
    baseline_payload = {
        "mode": "conclusion_only",
        "source_message_id": final_message.message_id,
        "summary": final_message.summary_payload.get("summary"),
        "dropped_fields": ["reasoning_steps", "full_trace", "intermediate_branch_notes"],
        "preserved_fields": ["summary", "improved_score"],
        "auditable": True,
    }
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v3.pacore.conclusion_only_baseline.v1",
        paper_key="pacore_parallel_coordinated_reasoning",
        normalization_rule="round_count_and_population_matched",
        baseline_ids=["conclusion_only_compaction", "bounded_candidate_rollup"],
        metadata={"phase": "dag_v3_phase3", "source_search_id": run.search_id},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v3.pacore.conclusion_only.deviations.v1",
        paper_key="pacore_parallel_coordinated_reasoning",
        deviations=[
            {
                "deviation_id": "pacore.compaction.dev.01",
                "severity": "low",
                "summary": "The conclusion-only baseline is expressed as an explicit helper artifact rather than a new runtime compaction backend.",
            }
        ],
        metadata={"phase": "dag_v3_phase3"},
    )
    return {
        "baseline_packet": baseline_packet,
        "baseline_payload": baseline_payload,
        "deviation_ledger": deviation_ledger,
        "run": run,
    }


def build_dag_v3_pacore_conclusion_only_compaction_baseline_payload() -> Dict[str, object]:
    example = build_dag_v3_pacore_conclusion_only_compaction_baseline()
    return {
        "baseline_packet": example["baseline_packet"].to_dict(),
        "baseline_payload": dict(example["baseline_payload"]),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
    }


def build_dag_v3_pacore_message_ablation_packet() -> Dict[str, object]:
    with_messages = build_pacore_search_runtime_example()["run"]
    without_messages = _build_rsa_search_runtime_with_profile(
        search_id="search.dag_v3.pacore.ablation.no_messages",
        max_rounds=2,
        population_size=3,
        subset_size=2,
        random_seed=13,
        metadata={"phase": "dag_v3_phase3", "study_kind": "pacore_without_messages"},
    )["run"]
    parallel_vs_sequential = [
        {
            "variant": "with_message_passing",
            "search_id": with_messages.search_id,
            "message_count": len(with_messages.messages),
            "selected_score": float(
                next(item for item in with_messages.candidates if item.candidate_id == with_messages.selected_candidate_id).score_vector.get(
                    "correctness_score", 0.0
                )
            ),
            "metrics": compute_fidelity_metrics(with_messages),
        },
        {
            "variant": "without_message_passing",
            "search_id": without_messages.search_id,
            "message_count": len(without_messages.messages),
            "selected_score": float(
                next(item for item in without_messages.candidates if item.candidate_id == without_messages.selected_candidate_id).score_vector.get(
                    "correctness_score", 0.0
                )
            ),
            "metrics": compute_fidelity_metrics(without_messages),
        },
    ]
    return {
        "paper_key": "pacore_parallel_coordinated_reasoning",
        "rows": parallel_vs_sequential,
        "metadata": {
            "phase": "dag_v3_phase3",
            "comparison": "with_without_message_passing",
            "model_tier": "gpt_5_4_mini",
        },
        "runs": [with_messages, without_messages],
    }


def build_dag_v3_pacore_message_ablation_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_pacore_message_ablation_packet()
    return {
        "paper_key": example["paper_key"],
        "rows": [dict(item) for item in example["rows"]],
        "metadata": dict(example["metadata"]),
        "runs": [item.to_dict() for item in example["runs"]],
    }


def build_dag_v3_pacore_parallel_vs_sequential_packet() -> Dict[str, object]:
    ablation = build_dag_v3_pacore_message_ablation_packet()
    coding_transfer_runner = {
        "runner_id": "dag_v3.pacore.coding_transfer.v1",
        "benchmark_packet": "pacore.coding_transfer.slice.v1",
        "model_tier": "gpt_5_4_mini",
        "claim_limit": "bounded coding-transfer smoke only",
        "required_artifacts": ["compute_ledger", "fidelity_scorecard", "deviation_ledger"],
    }
    return {
        "paper_key": "pacore_parallel_coordinated_reasoning",
        "parallel_variant": dict(ablation["rows"][0]),
        "sequential_variant": dict(ablation["rows"][1]),
        "coding_transfer_runner": coding_transfer_runner,
        "metadata": {"phase": "dag_v3_phase3", "comparison": "parallel_vs_sequential"},
    }


def build_dag_v3_pacore_parallel_vs_sequential_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_pacore_parallel_vs_sequential_packet()
    return {
        "paper_key": example["paper_key"],
        "parallel_variant": dict(example["parallel_variant"]),
        "sequential_variant": dict(example["sequential_variant"]),
        "coding_transfer_runner": dict(example["coding_transfer_runner"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_pacore_replication_packet() -> Dict[str, object]:
    profile = build_dag_v3_pacore_paper_profile()
    round_profiles = build_dag_v3_pacore_round_profile_packet()
    compaction_baseline = build_dag_v3_pacore_conclusion_only_compaction_baseline()
    ablation = build_dag_v3_pacore_message_ablation_packet()
    comparison = build_dag_v3_pacore_parallel_vs_sequential_packet()
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v3.pacore.phase3.scorecard.v1",
        paper_key="pacore_parallel_coordinated_reasoning",
        fidelity_label="medium_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="partial",
        compute_fidelity="normalized",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "algorithm-faithful, model-substituted, inference-only",
            "compaction_baseline_packet_id": compaction_baseline["baseline_packet"].packet_id,
            "coding_transfer_runner_id": comparison["coding_transfer_runner"]["runner_id"],
        },
        metadata={"phase": "dag_v3_phase3"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v3.pacore.phase3.compute.v1",
        paper_key="pacore_parallel_coordinated_reasoning",
        model_tier="gpt_5_4_mini",
        normalization_rule="round_count_and_population_matched",
        entries=[
            {
                "entry_id": f"pacore.phase3.{row['search_id']}.tokens",
                "kind": "total_tokens",
                "label": row["variant"],
                "quantity": sum(
                    float(run_candidate.usage.get("prompt_tokens", 0.0) + run_candidate.usage.get("completion_tokens", 0.0))
                    for run in ablation["runs"]
                    if run.search_id == row["search_id"]
                    for run_candidate in run.candidates
                ),
                "unit": "tokens",
            }
            for row in ablation["rows"]
        ],
        metadata={"phase": "dag_v3_phase3"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v3.pacore.phase3.deviations.v1",
        paper_key="pacore_parallel_coordinated_reasoning",
        deviations=[
            {
                "deviation_id": "pacore.phase3.dev.01",
                "severity": "medium",
                "summary": "Phase 3 reproduces exact round/message helper profiles and explicit ablations, but remains inference-only on GPT-5.4 Mini.",
            },
            {
                "deviation_id": "pacore.phase3.dev.02",
                "severity": "low",
                "summary": "The coding-transfer slice runner is defined and bounded here; full coding-transfer execution is deferred to later packets.",
            },
        ],
        metadata={"phase": "dag_v3_phase3"},
    )
    behavior_packet = {
        "with_message_passing_score": ablation["rows"][0]["selected_score"],
        "without_message_passing_score": ablation["rows"][1]["selected_score"],
        "parallel_variant_search_id": comparison["parallel_variant"]["search_id"],
        "sequential_variant_search_id": comparison["sequential_variant"]["search_id"],
        "round_profile_labels": [item["label"] for item in round_profiles["profiles"]],
    }
    return {
        "recipe_manifest": profile["recipe_manifest"],
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "deviation_ledger": deviation_ledger,
        "round_profiles": list(round_profiles["profiles"]),
        "compaction_baseline": compaction_baseline["baseline_packet"],
        "message_ablation_rows": list(ablation["rows"]),
        "parallel_vs_sequential": {
            "parallel_variant": dict(comparison["parallel_variant"]),
            "sequential_variant": dict(comparison["sequential_variant"]),
        },
        "coding_transfer_runner": dict(comparison["coding_transfer_runner"]),
        "behavior_packet": behavior_packet,
        "metadata": {"phase": "dag_v3_phase3", "kernel_change_required": False},
    }


def build_dag_v3_pacore_replication_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_pacore_replication_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "round_profiles": [dict(item) for item in example["round_profiles"]],
        "compaction_baseline": example["compaction_baseline"].to_dict(),
        "message_ablation_rows": [dict(item) for item in example["message_ablation_rows"]],
        "parallel_vs_sequential": dict(example["parallel_vs_sequential"]),
        "coding_transfer_runner": dict(example["coding_transfer_runner"]),
        "behavior_packet": dict(example["behavior_packet"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_optimize_ready_comparison_packet() -> Dict[str, object]:
    rsa = build_dag_v3_rsa_replication_packet()
    pacore = build_dag_v3_pacore_replication_packet()
    comparison_packet = {
        "packet_id": "dag_v3.optimize_ready.comparison.v1",
        "consumer": "optimize",
        "recipes": [
            {
                "paper_key": rsa["recipe_manifest"].paper_key,
                "fidelity_label": rsa["scorecard"].fidelity_label,
                "compute_ledger_id": rsa["compute_ledger"].ledger_id,
                "baseline_packet_id": rsa["baseline_packet"].packet_id,
                "deviation_ledger_id": rsa["deviation_ledger"].ledger_id,
            },
            {
                "paper_key": pacore["recipe_manifest"].paper_key,
                "fidelity_label": pacore["scorecard"].fidelity_label,
                "compute_ledger_id": pacore["compute_ledger"].ledger_id,
                "compaction_baseline_packet_id": pacore["compaction_baseline"].packet_id,
                "deviation_ledger_id": pacore["deviation_ledger"].ledger_id,
            },
        ],
        "shared_metrics": {
            "rsa": {
                "best_aggregation_gain": max(float(row["metrics"]["aggregation_gain"]) for row in rsa["sweep_rows"]),
                "best_emergent_correctness": max(float(row["metrics"]["emergent_correctness"]) for row in rsa["sweep_rows"]),
            },
            "pacore": {
                "message_passing_gain": float(pacore["behavior_packet"]["with_message_passing_score"])
                - float(pacore["behavior_packet"]["without_message_passing_score"]),
                "message_rounds": len(pacore["round_profiles"]),
            },
        },
        "adapter_boundary": {
            "outside_dag_kernel": True,
            "introduced_optimize_public_nouns_into_dag": False,
            "consumes": [
                "PaperRecipeManifest",
                "FidelityScorecard",
                "ComputeBudgetLedger",
                "BaselineComparisonPacket",
                "ReplicationDeviationLedger",
            ],
        },
        "metadata": {"phase": "dag_v3_phase4", "kernel_change_required": False},
    }
    return comparison_packet


def build_dag_v3_optimize_ready_comparison_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_optimize_ready_comparison_packet()
    return {
        "packet_id": example["packet_id"],
        "consumer": example["consumer"],
        "recipes": [dict(item) for item in example["recipes"]],
        "shared_metrics": dict(example["shared_metrics"]),
        "adapter_boundary": dict(example["adapter_boundary"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_rl_facing_export_slice_packet() -> Dict[str, object]:
    rsa = build_dag_v3_rsa_replication_packet()
    pacore = build_dag_v3_pacore_replication_packet()
    export_slices = [
        {
            "slice_id": "dag_v3.rl_slice.rsa_aggregation_segments",
            "paper_key": rsa["recipe_manifest"].paper_key,
            "unit_kind": "aggregation_example",
            "source_packet": "rsa_replication_packet",
            "claim_limit": "bounded RL-facing export opportunity only",
        },
        {
            "slice_id": "dag_v3.rl_slice.pacore_message_segments",
            "paper_key": pacore["recipe_manifest"].paper_key,
            "unit_kind": "communication_example",
            "source_packet": "pacore_replication_packet",
            "claim_limit": "bounded RL-facing export opportunity only",
        },
    ]
    return {
        "packet_id": "dag_v3.rl_facing_export_slices.v1",
        "slices": export_slices,
        "rl_boundary": {
            "training_framework_added": False,
            "public_rl_control_surface_added": False,
            "uses_existing_export_truth": True,
            "requires_future_rl_program": True,
        },
        "metadata": {"phase": "dag_v3_phase4", "kernel_change_required": False},
    }


def build_dag_v3_rl_facing_export_slice_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_rl_facing_export_slice_packet()
    return {
        "packet_id": example["packet_id"],
        "slices": [dict(item) for item in example["slices"]],
        "rl_boundary": dict(example["rl_boundary"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_darwin_boundary_update_packet() -> Dict[str, object]:
    rsa = build_dag_v3_rsa_replication_packet()
    pacore = build_dag_v3_pacore_replication_packet()
    return {
        "packet_id": "dag_v3.darwin_boundary_update.v1",
        "still_dag_local": [
            "paper_recipe_manifests",
            "fidelity_scorecards",
            "compute_ledgers",
            "replication_deviation_ledgers",
            "bounded replication helpers",
        ],
        "still_not_dag_local": [
            "many-run campaign orchestration",
            "archive or island semantics",
            "population-level novelty/diversity management",
            "persistent outer-loop experiment search",
        ],
        "evidence": {
            "rsa_packet_id": rsa["compute_ledger"].ledger_id,
            "pacore_packet_id": pacore["compute_ledger"].ledger_id,
            "repeated_dag_local_public_shape_pressure": 0,
        },
        "metadata": {"phase": "dag_v3_phase4", "kernel_change_required": False},
    }


def build_dag_v3_darwin_boundary_update_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_darwin_boundary_update_packet()
    return {
        "packet_id": example["packet_id"],
        "still_dag_local": list(example["still_dag_local"]),
        "still_not_dag_local": list(example["still_not_dag_local"]),
        "evidence": dict(example["evidence"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_cross_paper_synthesis_packet() -> Dict[str, object]:
    rsa = build_dag_v3_rsa_replication_packet()
    pacore = build_dag_v3_pacore_replication_packet()
    return {
        "packet_id": "dag_v3.cross_paper_synthesis.v1",
        "shared": [
            "fidelity helper artifacts instead of kernel expansion",
            "compute-normalized replication packets",
            "Mini-first model substitution policy",
            "explicit inference-only claim labeling",
        ],
        "paper_specific": {
            "rsa": ["N / K / T sweeps", "budget-matched aggregation baselines"],
            "pacore": ["round geometry", "message-passing ablations", "compaction baselines"],
        },
        "outside_dag": [
            "training-aware replication claims",
            "optimize objective/promotion logic",
            "RL training pipeline ownership",
            "DARWIN outer-loop orchestration",
        ],
        "references": {
            "rsa_scorecard_id": rsa["scorecard"].scorecard_id,
            "pacore_scorecard_id": pacore["scorecard"].scorecard_id,
        },
        "metadata": {"phase": "dag_v3_phase4", "kernel_change_required": False},
    }


def build_dag_v3_cross_paper_synthesis_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_cross_paper_synthesis_packet()
    return {
        "packet_id": example["packet_id"],
        "shared": list(example["shared"]),
        "paper_specific": dict(example["paper_specific"]),
        "outside_dag": list(example["outside_dag"]),
        "references": dict(example["references"]),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v3_freeze_decision_gate_packet() -> Dict[str, object]:
    optimize_packet = build_dag_v3_optimize_ready_comparison_packet()
    rl_packet = build_dag_v3_rl_facing_export_slice_packet()
    darwin_packet = build_dag_v3_darwin_boundary_update_packet()
    synthesis = build_dag_v3_cross_paper_synthesis_packet()
    remaining_pressure = {
        "dag_kernel": {
            "status": "frozen",
            "justification": "No repeated DAG-local public-shape pressure appeared across the replication and composition tranches.",
        },
        "recipe_helper": {
            "status": "active",
            "next_pressure": "benchmark/evaluator quality and richer paper-faithful helper packets",
        },
        "optimize": {
            "status": "ready",
            "next_pressure": "optimize can consume replication packets without DAG kernel changes",
            "packet_id": optimize_packet["packet_id"],
        },
        "rl": {
            "status": "ready_for_resume",
            "next_pressure": "bounded RL-facing export opportunities are now concrete and claim-labeled",
            "packet_id": rl_packet["packet_id"],
        },
        "darwin": {
            "status": "still_outside",
            "next_pressure": "outer-loop orchestration remains a DARWIN concern, not DAG-local runtime pressure",
            "packet_id": darwin_packet["packet_id"],
        },
        "harness_environment": {
            "status": "active",
            "next_pressure": "higher-fidelity benchmark and evaluator packets will matter more than new DAG runtime shape",
        },
    }
    freeze_decision = {
        "current_decision": "freeze_and_reclassify",
        "open_dag_v4_now": False,
        "reason": "The remaining work is mostly benchmark/evaluator quality, recipe/helper depth, optimize consumption, and future RL work rather than missing DAG runtime shape.",
        "evidence": {
            "optimize_packet_id": optimize_packet["packet_id"],
            "rl_packet_id": rl_packet["packet_id"],
            "darwin_packet_id": darwin_packet["packet_id"],
            "cross_paper_synthesis_id": synthesis["packet_id"],
            "repeated_dag_local_public_shape_pressure": 0,
        },
    }
    return {
        "packet_id": "dag_v3.freeze_decision_gate.v1",
        "remaining_pressure": remaining_pressure,
        "freeze_decision": freeze_decision,
        "metadata": {"phase": "dag_v3_phase5", "kernel_change_required": False},
    }


def build_dag_v3_freeze_decision_gate_packet_payload() -> Dict[str, object]:
    example = build_dag_v3_freeze_decision_gate_packet()
    return {
        "packet_id": example["packet_id"],
        "remaining_pressure": dict(example["remaining_pressure"]),
        "freeze_decision": dict(example["freeze_decision"]),
        "metadata": dict(example["metadata"]),
    }


def _build_got_sorting_seed_candidates(search_id: str, *, instance_class: str) -> List[SearchCandidate]:
    base_scores = {
        "mixed_signed_duplicates": (0.38, 0.44, 0.41, 0.36),
        "heavy_duplicates": (0.42, 0.46, 0.43, 0.39),
        "near_sorted_perturbation": (0.49, 0.52, 0.5, 0.47),
    }
    score_row = base_scores.get(instance_class, base_scores["mixed_signed_duplicates"])
    return [
        SearchCandidate(
            candidate_id=f"{search_id}.cand.got.seed.{index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            parent_ids=[],
            round_index=0,
            depth=0,
            payload_ref=f"artifacts/search/{search_id}/got_seed_{index}.json",
            score_vector={
                "correctness_score": score,
                "sortedness_score": max(0.0, score - 0.04),
                "preservation_score": min(1.0, score + 0.08),
            },
            usage={"prompt_tokens": 28 + (index * 4), "completion_tokens": 16 + index},
            status="seeded",
            reasoning_summary_ref=f"artifacts/search/{search_id}/got_seed_{index}_summary.md",
            metadata={"instance_class": instance_class, "seed_index": index, "node_kind": "proposal"},
        )
        for index, score in enumerate(score_row, start=1)
    ]


def build_dag_replication_v1_got_sorting_packet() -> Dict[str, object]:
    search_id = "search.replication_v1.got_sorting"
    seeds = _build_got_sorting_seed_candidates(search_id, instance_class="mixed_signed_duplicates")
    candidate_a, candidate_b, candidate_c, candidate_d = seeds
    merge_ab = SearchCandidate(
        candidate_id=f"{search_id}.cand.merge.ab",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[candidate_a.candidate_id, candidate_b.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/merge_ab.json",
        score_vector={"correctness_score": 0.61, "sortedness_score": 0.66, "preservation_score": 0.92},
        usage={"prompt_tokens": 51, "completion_tokens": 23},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/merge_ab.md",
        metadata={"node_kind": "merged_sort_segment", "transform_type": "merge", "instance_class": "mixed_signed_duplicates"},
    )
    merge_cd = SearchCandidate(
        candidate_id=f"{search_id}.cand.merge.cd",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[candidate_c.candidate_id, candidate_d.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/merge_cd.json",
        score_vector={"correctness_score": 0.59, "sortedness_score": 0.63, "preservation_score": 0.9},
        usage={"prompt_tokens": 50, "completion_tokens": 22},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/merge_cd.md",
        metadata={"node_kind": "merged_sort_segment", "transform_type": "merge", "instance_class": "mixed_signed_duplicates"},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[merge_ab.candidate_id, merge_cd.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/final_candidate.json",
        score_vector={"correctness_score": 0.88, "sortedness_score": 0.95, "preservation_score": 0.99},
        usage={"prompt_tokens": 64, "completion_tokens": 29},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final_candidate.md",
        metadata={"node_kind": "final_candidate", "transform_type": "merge", "instance_class": "mixed_signed_duplicates"},
    )
    refine_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final.refine",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[final_candidate.candidate_id],
        round_index=2,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/final_candidate_refine.json",
        score_vector={"correctness_score": 0.91, "sortedness_score": 0.98, "preservation_score": 1.0},
        usage={"prompt_tokens": 33, "completion_tokens": 14},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final_candidate_refine.md",
        metadata={"node_kind": "refined_segment", "transform_type": "refine", "instance_class": "mixed_signed_duplicates"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.split",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            round_index=0,
            operator_kind="expand",
            input_candidate_ids=[item.candidate_id for item in seeds],
            output_candidate_ids=[item.candidate_id for item in seeds],
            metadata={"recipe": "got_sorting", "transform_type": "split", "instance_class": "mixed_signed_duplicates"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.merge.1",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="aggregate",
            input_candidate_ids=[candidate_a.candidate_id, candidate_b.candidate_id, candidate_c.candidate_id, candidate_d.candidate_id],
            output_candidate_ids=[merge_ab.candidate_id, merge_cd.candidate_id],
            metadata={"recipe": "got_sorting", "transform_type": "merge_pairwise", "max_fan_in": 2},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.merge.2",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="aggregate",
            input_candidate_ids=[merge_ab.candidate_id, merge_cd.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "got_sorting", "transform_type": "final_merge", "max_fan_in": 2},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.refine",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="verify",
            input_candidate_ids=[final_candidate.candidate_id],
            output_candidate_ids=[refine_candidate.candidate_id],
            metadata={"recipe": "got_sorting", "transform_type": "refine", "bounded_refine_pass": 1},
        ),
    ]
    frontiers = [
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.0",
            search_id=search_id,
            round_index=0,
            candidate_ids=[item.candidate_id for item in seeds],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.1",
            search_id=search_id,
            round_index=1,
            candidate_ids=[merge_ab.candidate_id, merge_cd.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.2",
            search_id=search_id,
            round_index=2,
            candidate_ids=[final_candidate.candidate_id, refine_candidate.candidate_id],
            status="completed",
        ),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="got_sorting_graph_packet",
        candidates=[*seeds, merge_ab, merge_cd, final_candidate, refine_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=refine_candidate.candidate_id,
        metadata={
            "phase": "next_frontier_frontier_a",
            "paper_key": "graph_of_thoughts",
            "packet_id": "got_sorting_v1",
            "task_class": "sorting",
            "instance_class": "mixed_signed_duplicates",
            "max_llm_calls": 16,
            "target_peak_parallel_width": 4,
            "target_total_nodes": 20,
            "target_refine_steps": 1,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_replication_v1.got.profile.v1",
        paper_key="graph_of_thoughts",
        paper_title="Graph of Thoughts: Solving Elaborate Problems with Large Language Models",
        family_kind="graph_structured_reasoning",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="high_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="got.sorting.64_number.slice.v1",
        control_profile={
            "task_class": "sorting",
            "instance_class": "mixed_signed_duplicates",
            "max_fan_in": 2,
            "max_refine_steps": 1,
            "budget_envelope": {"max_llm_calls": 16, "peak_parallel_width": 4, "max_nodes": 20},
        },
        baseline_ids=[
            "direct_answer",
            "cot",
            "tot_budget_matched",
            "got_no_refine",
            "got_no_multi_parent_fusion",
            "linear_reducer",
        ],
        metadata={"phase": "next_frontier_frontier_a", "packet_id": "got_sorting_v1", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_replication_v1.got.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="high_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="pass",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "sorting-oriented bounded packet only",
            "replay_audit": "graph topology and lineage are reconstructable in the first synthetic packet",
            "repeated_shape_watch": ["multi_parent_lineage", "graph_transform_provenance", "refine_loop_provenance"],
        },
        metadata={"phase": "next_frontier_frontier_a"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_replication_v1.got.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "got.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 8, "unit": "calls"},
            {"entry_id": "got.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "got.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "got.peak_parallel_width", "kind": "peak_parallel_width", "label": "peak_parallel_width", "quantity": 4.0, "unit": "branches"},
            {"entry_id": "got.nodes", "kind": "node_count", "label": "node_count", "quantity": float(len(run.candidates)), "unit": "nodes"},
            {"entry_id": "got.refine_steps", "kind": "refine_steps", "label": "refine_steps", "quantity": 1.0, "unit": "steps"},
        ],
        normalization_rule="bounded_sorting_packet_matched",
        metadata={"phase": "next_frontier_frontier_a"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_replication_v1.got.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="bounded_sorting_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "next_frontier_frontier_a", "task_class": "sorting"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_replication_v1.got.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {
                "deviation_id": "got.dev.01",
                "severity": "medium",
                "summary": "First packet is a bounded sorting-only slice rather than a broad GoT task-family reproduction.",
            },
            {
                "deviation_id": "got.dev.02",
                "severity": "low",
                "summary": "First packet uses a fixed bounded split/merge/refine recipe to isolate topology and lineage pressure.",
            },
        ],
        metadata={"phase": "next_frontier_frontier_a"},
    )
    replay_audit = {
        "topology_reconstructable": True,
        "merged_parentage_reconstructable": True,
        "refine_parentage_reconstructable": True,
        "shadow_state_required": False,
    }
    lineage_rows = [
        {
            "node_id": item.candidate_id,
            "node_kind": item.metadata.get("node_kind", "proposal"),
            "parent_node_ids": list(item.parent_ids),
            "transform_type": item.metadata.get("transform_type", "seed"),
            "is_selected": item.candidate_id == run.selected_candidate_id,
        }
        for item in run.candidates
    ]
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "replay_audit": replay_audit,
        "lineage_rows": lineage_rows,
    }


def build_dag_replication_v1_got_sorting_packet_payload() -> Dict[str, object]:
    example = build_dag_replication_v1_got_sorting_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "replay_audit": dict(example["replay_audit"]),
        "lineage_rows": [dict(item) for item in example["lineage_rows"]],
    }


def build_dag_replication_v1_tot_game24_packet() -> Dict[str, object]:
    search_id = "search.replication_v1.tot_game24"
    seeds = [
        SearchCandidate(
            candidate_id=f"{search_id}.cand.seed.{index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            parent_ids=[],
            round_index=0,
            depth=0,
            payload_ref=f"artifacts/search/{search_id}/seed_{index}.json",
            score_vector={"correctness_score": score, "frontier_rank_score": rank},
            usage={"prompt_tokens": 24 + index, "completion_tokens": 14 + index},
            status="seeded",
            reasoning_summary_ref=f"artifacts/search/{search_id}/seed_{index}_summary.md",
            metadata={"thought_kind": "initial_state", "game24_instance": "8,8,3,3", "seed_index": index},
        )
        for index, (score, rank) in enumerate(((0.31, 0.42), (0.37, 0.48), (0.34, 0.45)), start=1)
    ]
    expand_a = SearchCandidate(
        candidate_id=f"{search_id}.cand.expand.a",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[seeds[1].candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/expand_a.json",
        score_vector={"correctness_score": 0.56, "frontier_rank_score": 0.68},
        usage={"prompt_tokens": 37, "completion_tokens": 19},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/expand_a.md",
        metadata={"thought_kind": "expanded_state", "action": "expand"},
    )
    expand_b = SearchCandidate(
        candidate_id=f"{search_id}.cand.expand.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[seeds[2].candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/expand_b.json",
        score_vector={"correctness_score": 0.52, "frontier_rank_score": 0.61},
        usage={"prompt_tokens": 36, "completion_tokens": 18},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/expand_b.md",
        metadata={"thought_kind": "expanded_state", "action": "expand"},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[expand_a.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/final_candidate.json",
        score_vector={"correctness_score": 0.87, "frontier_rank_score": 0.83},
        usage={"prompt_tokens": 42, "completion_tokens": 16},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final_candidate.md",
        metadata={"thought_kind": "solution_state", "action": "select_final"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.expand",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[item.candidate_id for item in seeds],
            output_candidate_ids=[expand_a.candidate_id, expand_b.candidate_id],
            metadata={"recipe": "tot_game24", "frontier_policy": "expand_rank_prune"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.prune",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="discard",
            input_candidate_ids=[expand_b.candidate_id],
            output_candidate_ids=[],
            metadata={"recipe": "tot_game24", "pruned_candidate_ids": [expand_b.candidate_id], "rationale": "lower frontier rank"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.final",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="select",
            input_candidate_ids=[expand_a.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "tot_game24", "termination": "valid_24_expression"},
        ),
    ]
    frontiers = [
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.0",
            search_id=search_id,
            round_index=0,
            candidate_ids=[item.candidate_id for item in seeds],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.1",
            search_id=search_id,
            round_index=1,
            candidate_ids=[expand_a.candidate_id, expand_b.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.2",
            search_id=search_id,
            round_index=2,
            candidate_ids=[final_candidate.candidate_id],
            status="completed",
        ),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="tot_game24_frontier_packet",
        candidates=[*seeds, expand_a, expand_b, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "next_frontier_frontier_a",
            "paper_key": "tree_of_thoughts",
            "packet_id": "tot_game24_v1",
            "task_class": "game_of_24",
            "frontier_policy": "expand_rank_prune",
            "max_llm_calls": 12,
            "target_peak_parallel_width": 3,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_replication_v1.tot.profile.v1",
        paper_key="tree_of_thoughts",
        paper_title="Tree of Thoughts: Deliberate Problem Solving with Large Language Models",
        family_kind="adaptive_frontier_search",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="high_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="tot.game24.slice.v1",
        control_profile={
            "task_class": "game_of_24",
            "frontier_policy": "expand_rank_prune",
            "reopen_policy": "disabled_in_first_packet",
            "evaluator_control": "required",
        },
        baseline_ids=[
            "cot",
            "self_consistency",
            "reranking",
            "fixed_width_no_backtracking",
            "no_self_eval",
            "discriminator_control",
        ],
        metadata={"phase": "next_frontier_frontier_a", "packet_id": "tot_game24_v1", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_replication_v1.tot.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="high_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="controlled",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "game-of-24 packet only",
            "frontier_replay": "frontier expansion and prune events are reconstructable",
            "backtrack_scope": "reopen disabled in first packet to isolate frontier provenance before adding backtracking",
        },
        metadata={"phase": "next_frontier_frontier_a"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_replication_v1.tot.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "tot.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 6, "unit": "calls"},
            {"entry_id": "tot.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "tot.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "tot.peak_parallel_width", "kind": "peak_parallel_width", "label": "peak_parallel_width", "quantity": 3.0, "unit": "branches"},
            {"entry_id": "tot.pruned_nodes", "kind": "pruned_nodes", "label": "pruned_nodes", "quantity": 1.0, "unit": "nodes"},
        ],
        normalization_rule="game24_frontier_packet_matched",
        metadata={"phase": "next_frontier_frontier_a"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_replication_v1.tot.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="game24_frontier_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "next_frontier_frontier_a", "task_class": "game_of_24"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_replication_v1.tot.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {
                "deviation_id": "tot.dev.01",
                "severity": "medium",
                "summary": "First packet narrows ToT to a Game-of-24 slice with explicit evaluator controls instead of attempting broad task-family coverage.",
            },
            {
                "deviation_id": "tot.dev.02",
                "severity": "low",
                "summary": "First packet disables explicit reopen behavior to isolate frontier ranking and pruning before adding richer backtracking.",
            },
        ],
        metadata={"phase": "next_frontier_frontier_a"},
    )
    frontier_audit = {
        "frontier_policy_reconstructable": True,
        "prune_history_reconstructable": True,
        "reopen_required": False,
        "evaluator_confound_controlled": True,
    }
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "frontier_audit": frontier_audit,
    }


def build_dag_replication_v1_tot_game24_packet_payload() -> Dict[str, object]:
    example = build_dag_replication_v1_tot_game24_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "frontier_audit": dict(example["frontier_audit"]),
    }


def build_dag_replication_v1_moa_layered_packet() -> Dict[str, object]:
    search_id = "search.replication_v1.moa_layered"
    seeds = [
        SearchCandidate(
            candidate_id=f"{search_id}.cand.layer0.{index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            parent_ids=[],
            round_index=0,
            depth=0,
            payload_ref=f"artifacts/search/{search_id}/layer0_{index}.json",
            score_vector={"correctness_score": score, "judge_rank_score": judge},
            usage={"prompt_tokens": 34 + index, "completion_tokens": 21 + index},
            status="seeded",
            reasoning_summary_ref=f"artifacts/search/{search_id}/layer0_{index}.md",
            metadata={
                "layer_index": 0,
                "roster_role": role,
                "model_family": model,
                "task_slice": "bounded_multihop_qa",
            },
        )
        for index, (role, model, score, judge) in enumerate(
            (
                ("planner", "gpt_5_4_mini", 0.48, 0.55),
                ("retriever", "claude_sonnet", 0.52, 0.58),
                ("synthesizer", "gemini_flash", 0.5, 0.57),
            ),
            start=1,
        )
    ]
    layer1_a = SearchCandidate(
        candidate_id=f"{search_id}.cand.layer1.a",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[item.candidate_id for item in seeds],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/layer1_a.json",
        score_vector={"correctness_score": 0.66, "judge_rank_score": 0.7},
        usage={"prompt_tokens": 58, "completion_tokens": 25},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/layer1_a.md",
        metadata={"layer_index": 1, "aggregation_style": "cross_layer_fan_in", "roster_role": "aggregator_a"},
    )
    layer1_b = SearchCandidate(
        candidate_id=f"{search_id}.cand.layer1.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[item.candidate_id for item in seeds],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/layer1_b.json",
        score_vector={"correctness_score": 0.68, "judge_rank_score": 0.72},
        usage={"prompt_tokens": 60, "completion_tokens": 24},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/layer1_b.md",
        metadata={"layer_index": 1, "aggregation_style": "cross_layer_fan_in", "roster_role": "aggregator_b"},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/final.json",
        score_vector={"correctness_score": 0.81, "judge_rank_score": 0.84},
        usage={"prompt_tokens": 44, "completion_tokens": 18},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final.md",
        metadata={"layer_index": 2, "roster_role": "final_synthesizer", "aggregation_style": "judge_synthesis"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.layer1",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="aggregate",
            input_candidate_ids=[item.candidate_id for item in seeds],
            output_candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
            metadata={"recipe": "moa_layered", "fan_in_size": len(seeds), "layer_transition": "0_to_1"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.final",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="select",
            input_candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "moa_layered", "layer_transition": "1_to_2", "judge_required": True},
        ),
    ]
    frontiers = [
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.0",
            search_id=search_id,
            round_index=0,
            candidate_ids=[item.candidate_id for item in seeds],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.1",
            search_id=search_id,
            round_index=1,
            candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.2",
            search_id=search_id,
            round_index=2,
            candidate_ids=[final_candidate.candidate_id],
            status="completed",
        ),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="moa_layered_fan_in_packet",
        candidates=[*seeds, layer1_a, layer1_b, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "next_frontier_frontier_a",
            "paper_key": "mixture_of_agents",
            "packet_id": "moa_layered_v1",
            "task_class": "bounded_multihop_qa",
            "max_llm_calls": 10,
            "target_peak_parallel_width": 3,
            "layer_count": 3,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_replication_v1.moa.profile.v1",
        paper_key="mixture_of_agents",
        paper_title="Mixture-of-Agents Enhances Large Language Model Capabilities",
        family_kind="layered_fan_in_reasoning",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="moa.bounded_multihop_qa.slice.v1",
        control_profile={
            "task_class": "bounded_multihop_qa",
            "layer_count": 3,
            "agents_per_layer": [3, 2, 1],
            "roster_policy": "heterogeneous",
        },
        baseline_ids=[
            "best_single_model",
            "ensemble_vote_judge",
            "one_layer_moa",
            "homogeneous_roster",
            "sequential_summarization",
        ],
        metadata={"phase": "next_frontier_frontier_a", "packet_id": "moa_layered_v1", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_replication_v1.moa.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="bounded",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "bounded layered-fan-in packet only",
            "fan_in_replay": "cross-layer fan-in and final synthesis lineage are reconstructable",
            "judge_limit": "benchmark and judge stack are medium-fidelity only in the first packet",
        },
        metadata={"phase": "next_frontier_frontier_a"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_replication_v1.moa.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "moa.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 7, "unit": "calls"},
            {"entry_id": "moa.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "moa.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "moa.peak_parallel_width", "kind": "peak_parallel_width", "label": "peak_parallel_width", "quantity": 3.0, "unit": "branches"},
            {"entry_id": "moa.layer_count", "kind": "layer_count", "label": "layer_count", "quantity": 3.0, "unit": "layers"},
        ],
        normalization_rule="bounded_moa_layered_packet_matched",
        metadata={"phase": "next_frontier_frontier_a"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_replication_v1.moa.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="bounded_moa_layered_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "next_frontier_frontier_a", "task_class": "bounded_multihop_qa"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_replication_v1.moa.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {
                "deviation_id": "moa.dev.01",
                "severity": "medium",
                "summary": "First packet is a bounded layered-fan-in slice rather than a benchmark-scale judged replication.",
            },
            {
                "deviation_id": "moa.dev.02",
                "severity": "low",
                "summary": "The first packet fixes a small heterogeneous roster to isolate layered provenance and fan-in geometry.",
            },
        ],
        metadata={"phase": "next_frontier_frontier_a"},
    )
    layer_roster_manifest = {
        "layer_count": 3,
        "agents_per_layer": [3, 2, 1],
        "homogeneous_or_heterogeneous": "heterogeneous",
        "model_roster": ["gpt_5_4_mini", "claude_sonnet", "gemini_flash"],
        "final_synthesis_stage": "layer_2_final_synthesizer",
    }
    cross_layer_fan_in_packet = {
        "per_layer_fan_in_size": {"layer_1": 3, "layer_2": 2},
        "context_budget_assumption": "bounded_summary_only",
        "contribution_tracking_policy": "candidate_parent_ids_plus_layer_role_metadata",
        "layer_transition_notes": ["layer_0_to_1 all-to-all fan-in", "layer_1_to_2 judge-backed final synthesis"],
    }
    judge_benchmark_note = {
        "judge_stack": "bounded single-pass judge",
        "benchmark_scope": "bounded_multihop_qa",
        "benchmark_limit": "first packet does not claim full benchmark equivalence",
    }
    fan_in_audit = {
        "layered_fan_in_reconstructable": True,
        "roster_provenance_reconstructable": True,
        "shared_runtime_gap_detected": False,
    }
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "layer_roster_manifest": layer_roster_manifest,
        "cross_layer_fan_in_packet": cross_layer_fan_in_packet,
        "judge_benchmark_note": judge_benchmark_note,
        "fan_in_audit": fan_in_audit,
    }


def build_dag_replication_v1_moa_layered_packet_payload() -> Dict[str, object]:
    example = build_dag_replication_v1_moa_layered_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "layer_roster_manifest": dict(example["layer_roster_manifest"]),
        "cross_layer_fan_in_packet": dict(example["cross_layer_fan_in_packet"]),
        "judge_benchmark_note": dict(example["judge_benchmark_note"]),
        "fan_in_audit": dict(example["fan_in_audit"]),
    }


def build_dag_replication_v1_codetree_packet() -> Dict[str, object]:
    search_id = "search.replication_v1.codetree_patch"
    seed = SearchCandidate(
        candidate_id=f"{search_id}.cand.strategy",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.0",
        parent_ids=[],
        round_index=0,
        depth=0,
        payload_ref=f"artifacts/search/{search_id}/strategy.json",
        score_vector={"correctness_score": 0.42, "repairability_score": 0.56},
        usage={"prompt_tokens": 31, "completion_tokens": 18},
        status="seeded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/strategy.md",
        metadata={"stage_role": "thinker", "benchmark_slice": "toy_patch_pair"},
    )
    solver = SearchCandidate(
        candidate_id=f"{search_id}.cand.solver",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[seed.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/solver.json",
        score_vector={"correctness_score": 0.58, "repairability_score": 0.62},
        usage={"prompt_tokens": 48, "completion_tokens": 26},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/solver.md",
        metadata={"stage_role": "solver", "tree_action": "expand_patch"},
    )
    critic = SearchCandidate(
        candidate_id=f"{search_id}.cand.critic",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[solver.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/critic.json",
        score_vector={"correctness_score": 0.49, "repairability_score": 0.71},
        usage={"prompt_tokens": 29, "completion_tokens": 17},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/critic.md",
        metadata={"stage_role": "critic", "tree_action": "critique_patch"},
    )
    debugger = SearchCandidate(
        candidate_id=f"{search_id}.cand.debugger",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[solver.candidate_id, critic.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/debugger.json",
        score_vector={"correctness_score": 0.78, "repairability_score": 0.83},
        usage={"prompt_tokens": 44, "completion_tokens": 24},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/debugger.md",
        metadata={"stage_role": "debugger", "tree_action": "repair_after_feedback"},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.4",
        parent_ids=[debugger.candidate_id],
        round_index=4,
        depth=4,
        payload_ref=f"artifacts/search/{search_id}/final.json",
        score_vector={"correctness_score": 0.86, "repairability_score": 0.9},
        usage={"prompt_tokens": 21, "completion_tokens": 11},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final.md",
        metadata={"stage_role": "selector", "tree_action": "accept_patch"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.expand",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[seed.candidate_id],
            output_candidate_ids=[solver.candidate_id],
            metadata={"recipe": "codetree_patch", "stage_transition": "thinker_to_solver"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.verify",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="verify",
            input_candidate_ids=[solver.candidate_id],
            output_candidate_ids=[critic.candidate_id],
            metadata={"recipe": "codetree_patch", "stage_transition": "solver_to_critic", "feedback_kind": "execution_plus_review"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.repair",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="execute",
            input_candidate_ids=[solver.candidate_id, critic.candidate_id],
            output_candidate_ids=[debugger.candidate_id],
            metadata={"recipe": "codetree_patch", "stage_transition": "critic_to_debugger"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.select",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.4",
            round_index=4,
            operator_kind="select",
            input_candidate_ids=[debugger.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "codetree_patch", "termination": "all_visible_tests_pass"},
        ),
    ]
    frontiers = [
        SearchFrontier(frontier_id=f"{search_id}.frontier.0", search_id=search_id, round_index=0, candidate_ids=[seed.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.1", search_id=search_id, round_index=1, candidate_ids=[solver.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.2", search_id=search_id, round_index=2, candidate_ids=[critic.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.3", search_id=search_id, round_index=3, candidate_ids=[debugger.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.4", search_id=search_id, round_index=4, candidate_ids=[final_candidate.candidate_id], status="completed"),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="codetree_stage_patch_packet",
        candidates=[seed, solver, critic, debugger, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "next_frontier_frontier_a",
            "paper_key": "codetree",
            "packet_id": "codetree_patch_v1",
            "task_class": "bounded_code_patch",
            "max_llm_calls": 9,
            "target_peak_parallel_width": 1,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_replication_v1.codetree.profile.v1",
        paper_key="codetree",
        paper_title="CodeTree",
        family_kind="stage_heterogeneous_code_tree",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="codetree.toy_patch_pair.slice.v1",
        control_profile={
            "task_class": "bounded_code_patch",
            "visible_hidden_test_policy": "visible_only_in_first_packet",
            "execution_feedback": "required",
            "critic_role": "explicit",
        },
        baseline_ids=[
            "direct_single_shot_code",
            "cot_code",
            "iterative_self_debug",
            "strategy_solver_pair",
            "execution_only_critic",
            "no_tree_iterative_refine",
        ],
        metadata={"phase": "next_frontier_frontier_a", "packet_id": "codetree_patch_v1", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_replication_v1.codetree.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="bounded",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "bounded code-search-tree packet only",
            "execution_feedback_scope": "visible tests only in first packet",
            "critic_lineage": "critic and debugger actions are reconstructable from events and parent ids",
        },
        metadata={"phase": "next_frontier_frontier_a"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_replication_v1.codetree.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "codetree.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 5, "unit": "calls"},
            {"entry_id": "codetree.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "codetree.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "codetree.execution_rounds", "kind": "execution_rounds", "label": "execution_rounds", "quantity": 2.0, "unit": "rounds"},
            {"entry_id": "codetree.critic_actions", "kind": "critic_actions", "label": "critic_actions", "quantity": 1.0, "unit": "actions"},
        ],
        normalization_rule="bounded_codetree_patch_packet_matched",
        metadata={"phase": "next_frontier_frontier_a"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_replication_v1.codetree.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="bounded_codetree_patch_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "next_frontier_frontier_a", "task_class": "bounded_code_patch"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_replication_v1.codetree.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {
                "deviation_id": "codetree.dev.01",
                "severity": "medium",
                "summary": "First packet uses a bounded toy patch slice rather than a broad benchmark-scale code tree evaluation.",
            },
            {
                "deviation_id": "codetree.dev.02",
                "severity": "low",
                "summary": "Visible-test-only execution feedback is used in the first packet to isolate stage transitions and critic lineage.",
            },
        ],
        metadata={"phase": "next_frontier_frontier_a"},
    )
    role_stage_manifest = {
        "thinker_stage": "strategy_seed",
        "solver_stage": "initial_patch",
        "debugger_stage": "repair_after_feedback",
        "critic_stage": "execution_plus_review",
        "stage_transition_rules": ["thinker_to_solver", "solver_to_critic", "critic_to_debugger", "debugger_to_selector"],
    }
    code_harness_manifest = {
        "benchmark_slice": "toy_patch_pair",
        "visible_hidden_test_policy": "visible_only",
        "execution_environment_assumption": "local_python_harness",
        "replayability_notes": "execution outcomes recorded as packet metadata rather than hidden environment state",
    }
    critic_action_ledger = {
        "critic_actions": [{"action_id": "critic.1", "kind": "execution_review", "target_candidate_id": solver.candidate_id, "produced_candidate_id": critic.candidate_id}],
        "repair_actions": [{"action_id": "debugger.1", "kind": "patch_repair", "target_candidate_id": solver.candidate_id, "produced_candidate_id": debugger.candidate_id}],
    }
    execution_feedback_packet = {
        "visible_tests_run": 3,
        "visible_tests_passed_before_repair": 1,
        "visible_tests_passed_after_repair": 3,
        "feedback_artifact_policy": "recorded_in_event_metadata",
    }
    task_scope_note = {
        "claim_limit": "bounded code-search-tree packet only",
        "scope_note": "first packet isolates stage heterogeneity and critic/execution lineage, not benchmark scale",
    }
    codetree_audit = {
        "critic_action_reconstructable": True,
        "execution_feedback_reconstructable": True,
        "stage_transition_reconstructable": True,
        "shared_runtime_gap_detected": False,
    }
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "role_stage_manifest": role_stage_manifest,
        "code_harness_manifest": code_harness_manifest,
        "critic_action_ledger": critic_action_ledger,
        "execution_feedback_packet": execution_feedback_packet,
        "task_scope_note": task_scope_note,
        "codetree_audit": codetree_audit,
    }


def build_dag_replication_v1_codetree_packet_payload() -> Dict[str, object]:
    example = build_dag_replication_v1_codetree_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "role_stage_manifest": dict(example["role_stage_manifest"]),
        "code_harness_manifest": dict(example["code_harness_manifest"]),
        "critic_action_ledger": dict(example["critic_action_ledger"]),
        "execution_feedback_packet": dict(example["execution_feedback_packet"]),
        "task_scope_note": dict(example["task_scope_note"]),
        "codetree_audit": dict(example["codetree_audit"]),
    }


def build_dag_v4_phase1_control_packet() -> Dict[str, object]:
    """Build the first DAG V4 helper/control packet without changing kernel truth."""

    got = build_dag_replication_v1_got_sorting_packet()
    tot = build_dag_replication_v1_tot_game24_packet()

    repeated_shape_entries = [
        RepeatedShapeRegisterEntry(
            gap_label="irregular_graph_feedback_lineage",
            target_family="got_v2_preflight",
            topology_class="G",
            where_it_appears="bounded graph-transform rerun over prior GoT packet",
            current_workaround="lineage audit plus explicit graph packet manifests",
            why_workaround_is_insufficient="not yet exhausted; current packet remains reconstructable under helper-local audits",
            effect_on_fidelity_tier="no_downgrade",
            effect_on_replay_export="none observed in bounded rerun",
            primary_locus="helper_level_only",
            seen_in_other_targets=[],
            seen_in_consumers=["optimize_handoff_preflight", "rl_handoff_preflight"],
            helper_exhausted=False,
            counts_toward_review=False,
            notes={"packet_id": got["recipe_manifest"].manifest_id},
            metadata={"phase": "dag_v4_phase1"},
        ),
        RepeatedShapeRegisterEntry(
            gap_label="adaptive_frontier_reopen_truth",
            target_family="tot_v2_preflight",
            topology_class="F",
            where_it_appears="frontier-controlled rerun over prior ToT packet with explicit audit expectations",
            current_workaround="frontier policy audit plus evaluator-strength controls",
            why_workaround_is_insufficient="harder reopen/backtrack slices are still pending, so no repeated-shape claim is allowed yet",
            effect_on_fidelity_tier="bounded_medium_only",
            effect_on_replay_export="none observed in bounded rerun",
            primary_locus="helper_and_evaluator_control_only",
            seen_in_other_targets=[],
            seen_in_consumers=["optimize_handoff_preflight", "rl_handoff_preflight"],
            helper_exhausted=False,
            counts_toward_review=False,
            notes={"packet_id": tot["recipe_manifest"].manifest_id},
            metadata={"phase": "dag_v4_phase1"},
        ),
    ]
    topology_audits = [
        TopologyAudit(
            audit_id="dag_v4.phase1.audit.got.topology.v1",
            target_family="got_v2_preflight",
            topology_class="G",
            parentage_reconstructable=True,
            fan_flow_reconstructable=True,
            feedback_loop_reconstructable=True,
            shadow_state_required=False,
            notes={"lineage_basis": "candidate parent ids plus helper packet graph rows"},
            metadata={"phase": "dag_v4_phase1"},
        ),
        TopologyAudit(
            audit_id="dag_v4.phase1.audit.tot.topology.v1",
            target_family="tot_v2_preflight",
            topology_class="F",
            parentage_reconstructable=True,
            fan_flow_reconstructable=True,
            feedback_loop_reconstructable=False,
            shadow_state_required=False,
            notes={"current_limit": "bounded ToT slice does not yet exercise reopen loops"},
            metadata={"phase": "dag_v4_phase1"},
        ),
    ]
    frontier_policy_audits = [
        FrontierPolicyAudit(
            audit_id="dag_v4.phase1.audit.got.frontier.v1",
            target_family="got_v2_preflight",
            topology_class="G",
            select_prune_reconstructable=True,
            budget_conditioned_reconstructable=False,
            reopen_backtrack_reconstructable=False,
            consumer_can_explain_frontier=True,
            shadow_policy_required=False,
            notes={"reason": "GoT preflight is graph-local rather than budget-tree-local"},
            metadata={"phase": "dag_v4_phase1"},
        ),
        FrontierPolicyAudit(
            audit_id="dag_v4.phase1.audit.tot.frontier.v1",
            target_family="tot_v2_preflight",
            topology_class="F",
            select_prune_reconstructable=True,
            budget_conditioned_reconstructable=True,
            reopen_backtrack_reconstructable=False,
            consumer_can_explain_frontier=True,
            shadow_policy_required=False,
            notes={"current_limit": "reopen/backtrack not yet exercised in the bounded packet"},
            metadata={"phase": "dag_v4_phase1"},
        ),
    ]
    benchmark_control_packet = BenchmarkControlPacket(
        packet_id="dag_v4.phase1.control.evaluator_strength.v1",
        target_family="got_tot_preflight",
        control_kind="evaluator_strength_and_budget_control",
        evaluator_stack=["packet_review_judge", "fidelity_checker", "bounded_compute_checker"],
        controls=[
            "match_llm_call_budget",
            "explicit_discriminator_strength_note",
            "record_holdout_confound_risks",
        ],
        known_confound_risks=["weak evaluator overstates frontier gap", "sorting/game24 task simplification hides reopen pressure"],
        notes={"purpose": "keep bounded reruns interpretable before harder V4 packets"},
        metadata={"phase": "dag_v4_phase1"},
    )
    replay_export_integrity_packet = ReplayExportIntegrityPacket(
        packet_id="dag_v4.phase1.control.replay_export.v1",
        target_family="got_tot_preflight",
        topology_class="G+F",
        export_modes=["trajectory_export", "offline_dataset_projection", "flattened_transcript_control"],
        preserved_semantics=["selected_candidate_identity", "assessment linkage", "frontier sequence", "candidate parentage"],
        lost_semantics=["none material in bounded rerun"],
        shadow_assumptions_required=False,
        notes={"flattened_control": "included only as a loss-detection baseline"},
        metadata={"phase": "dag_v4_phase1"},
    )
    consumer_handoff_packets = [
        ConsumerHandoffPacket(
            packet_id="dag_v4.phase1.handoff.optimize.v1",
            target_family="got_tot_preflight",
            consumer_kind="optimize",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ComputeBudgetLedger", "BaselineComparisonPacket"],
            handoff_contract=["comparison-ready packet ids", "bounded claim-limit annotations", "no shadow frontier semantics"],
            shadow_semantics_required=False,
            notes={"consumer_read": "optimize should compare packets without DAG-private assumptions"},
            metadata={"phase": "dag_v4_phase1"},
        ),
        ConsumerHandoffPacket(
            packet_id="dag_v4.phase1.handoff.rl.v1",
            target_family="got_tot_preflight",
            consumer_kind="rl",
            artifact_kinds=["trajectory_export", "assessment linkage", "control baselines"],
            handoff_contract=["replay/export integrity must remain explicit", "flattened control must expose any semantic loss"],
            shadow_semantics_required=False,
            notes={"consumer_read": "RL should not need DAG-private topology semantics for bounded exports"},
            metadata={"phase": "dag_v4_phase1"},
        ),
    ]
    composition_seam_packet = CompositionSeamPacket(
        packet_id="dag_v4.phase1.seam.preflight.v1",
        source_family="got_tot_preflight",
        target_kind="optimize_and_rl",
        seam_labels=["frontier_decision_continuity", "assessment_to_action_continuity", "export_integrity"],
        issues=[
            {"issue_id": "seam.frontier.1", "status": "not_observed", "locus": "none"},
            {"issue_id": "seam.export.1", "status": "bounded_clean", "locus": "helper_level_only"},
        ],
        repeated_shape_candidate=False,
        notes={"purpose": "first cross-system preflight before harder V4 packets"},
        metadata={"phase": "dag_v4_phase1"},
    )
    return {
        "kernel_change_required": False,
        "phase": "dag_v4_phase1",
        "source_packets": {
            "got": got,
            "tot": tot,
        },
        "repeated_shape_register": repeated_shape_entries,
        "topology_audits": topology_audits,
        "frontier_policy_audits": frontier_policy_audits,
        "benchmark_control_packet": benchmark_control_packet,
        "replay_export_integrity_packet": replay_export_integrity_packet,
        "consumer_handoff_packets": consumer_handoff_packets,
        "composition_seam_packet": composition_seam_packet,
        "metadata": {
            "purpose": "phase1_helper_and_control_hardening",
            "frozen_kernel": True,
            "old_packet_rerun_count": 2,
            "next_targets": ["got_v2", "tot_v2"],
        },
    }


def build_dag_v4_phase1_control_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_phase1_control_packet()
    return {
        "kernel_change_required": example["kernel_change_required"],
        "phase": example["phase"],
        "source_packets": {
            "got": {
                "recipe_manifest": example["source_packets"]["got"]["recipe_manifest"].to_dict(),
                "scorecard": example["source_packets"]["got"]["scorecard"].to_dict(),
                "compute_ledger": example["source_packets"]["got"]["compute_ledger"].to_dict(),
                "baseline_packet": example["source_packets"]["got"]["baseline_packet"].to_dict(),
                "deviation_ledger": example["source_packets"]["got"]["deviation_ledger"].to_dict(),
            },
            "tot": {
                "recipe_manifest": example["source_packets"]["tot"]["recipe_manifest"].to_dict(),
                "scorecard": example["source_packets"]["tot"]["scorecard"].to_dict(),
                "compute_ledger": example["source_packets"]["tot"]["compute_ledger"].to_dict(),
                "baseline_packet": example["source_packets"]["tot"]["baseline_packet"].to_dict(),
                "deviation_ledger": example["source_packets"]["tot"]["deviation_ledger"].to_dict(),
            },
        },
        "repeated_shape_register": [item.to_dict() for item in example["repeated_shape_register"]],
        "topology_audits": [item.to_dict() for item in example["topology_audits"]],
        "frontier_policy_audits": [item.to_dict() for item in example["frontier_policy_audits"]],
        "benchmark_control_packet": example["benchmark_control_packet"].to_dict(),
        "replay_export_integrity_packet": example["replay_export_integrity_packet"].to_dict(),
        "consumer_handoff_packets": [item.to_dict() for item in example["consumer_handoff_packets"]],
        "composition_seam_packet": example["composition_seam_packet"].to_dict(),
        "metadata": dict(example["metadata"]),
    }


def build_dag_v4_got_v2_packet() -> Dict[str, object]:
    """Build the GoT v2 re-probe packet that removes the bounded single-refine assumption."""

    base = build_dag_replication_v1_got_sorting_packet()
    base_run = base["run"]
    search_id = "search.dag_v4.got_v2"
    base_candidates = [
        SearchCandidate.from_dict({**candidate.to_dict(), "search_id": search_id})
        for candidate in base_run.candidates
    ]
    candidate_index = {candidate.candidate_id.split(".cand.", 1)[-1]: candidate for candidate in base_candidates}
    merge_ab = candidate_index["merge.ab"]
    final_refine = candidate_index["final.refine"]
    feedback_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.feedback.merge",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[merge_ab.candidate_id, final_refine.candidate_id],
        round_index=3,
        depth=4,
        payload_ref=f"artifacts/search/{search_id}/feedback_merge.json",
        score_vector={"correctness_score": 0.93, "sortedness_score": 0.985, "preservation_score": 1.0},
        usage={"prompt_tokens": 36, "completion_tokens": 15},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/feedback_merge.md",
        metadata={"node_kind": "feedback_merge", "transform_type": "graph_feedback_merge", "instance_class": "mixed_signed_duplicates"},
    )
    events = [SearchEvent.from_dict({**event.to_dict(), "search_id": search_id}) for event in base_run.events]
    events.append(
        SearchEvent(
            event_id=f"{search_id}.event.feedback.merge",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="aggregate",
            input_candidate_ids=[merge_ab.candidate_id, final_refine.candidate_id],
            output_candidate_ids=[feedback_candidate.candidate_id],
            metadata={"recipe": "got_sorting_v2", "transform_type": "feedback_merge", "max_fan_in": 2, "feedback_loop": True},
        )
    )
    frontiers = [SearchFrontier.from_dict({**frontier.to_dict(), "search_id": search_id}) for frontier in base_run.frontiers]
    frontiers.append(
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.3",
            search_id=search_id,
            round_index=3,
            candidate_ids=[feedback_candidate.candidate_id],
            status="completed",
        )
    )
    run = SearchRun(
        search_id=search_id,
        recipe_kind="got_sorting_graph_packet_v2",
        candidates=[*base_candidates, feedback_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=feedback_candidate.candidate_id,
        metadata={
            "phase": "dag_v4_phase2",
            "paper_key": "graph_of_thoughts",
            "packet_id": "got_sorting_v2",
            "task_class": "sorting",
            "removed_assumptions": ["single_refine_step_only", "no_feedback_merge_after_refine"],
            "max_llm_calls": 18,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.got_v2.profile.v1",
        paper_key="graph_of_thoughts",
        paper_title="Graph of Thoughts: Solving Elaborate Problems with Large Language Models",
        family_kind="graph_structured_reasoning",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="high_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="got.sorting.64_number.slice.v2",
        control_profile={
            "task_class": "sorting",
            "feedback_merge_enabled": True,
            "multi_parent_fusion_depth": 2,
            "consumer_replay_required": True,
        },
        baseline_ids=[
            "direct_answer",
            "cot",
            "tot_budget_matched",
            "got_no_feedback_merge",
            "got_no_multi_parent_fusion",
            "linear_reducer",
        ],
        metadata={"phase": "dag_v4_phase2", "packet_id": "got_sorting_v2", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.got_v2.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="high_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="pass",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "harder bounded sorting packet only",
            "removed_assumptions": ["single_refine_step_only", "no_feedback_merge_after_refine"],
            "repeated_shape_watch": ["irregular_graph_feedback_lineage", "multi_parent_provenance"],
        },
        metadata={"phase": "dag_v4_phase2"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.got_v2.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "got_v2.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 9, "unit": "calls"},
            {"entry_id": "got_v2.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "got_v2.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "got_v2.feedback_loops", "kind": "feedback_loops", "label": "feedback_loops", "quantity": 1.0, "unit": "loops"},
            {"entry_id": "got_v2.nodes", "kind": "node_count", "label": "node_count", "quantity": float(len(run.candidates)), "unit": "nodes"},
        ],
        normalization_rule="bounded_sorting_feedback_packet_matched",
        metadata={"phase": "dag_v4_phase2"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.got_v2.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="bounded_sorting_feedback_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase2", "task_class": "sorting"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.got_v2.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "got_v2.dev.01", "severity": "medium", "summary": "Still a sorting-family packet rather than a broad GoT task suite."},
            {"deviation_id": "got_v2.dev.02", "severity": "low", "summary": "Feedback merge remains bounded to a single additional graph-transform loop."},
        ],
        metadata={"phase": "dag_v4_phase2"},
    )
    topology_audit = TopologyAudit(
        audit_id="dag_v4.got_v2.topology.v1",
        target_family="got_v2",
        topology_class="G",
        parentage_reconstructable=True,
        fan_flow_reconstructable=True,
        feedback_loop_reconstructable=True,
        shadow_state_required=False,
        notes={"selected_candidate_id": run.selected_candidate_id},
        metadata={"phase": "dag_v4_phase2"},
    )
    replay_export_integrity = ReplayExportIntegrityPacket(
        packet_id="dag_v4.got_v2.replay_export.v1",
        target_family="got_v2",
        topology_class="G",
        export_modes=["trajectory_export", "offline_dataset_projection", "flattened_transcript_control"],
        preserved_semantics=["multi_parent_lineage", "feedback_loop_parentage", "selected_candidate_identity"],
        lost_semantics=[],
        shadow_assumptions_required=False,
        notes={"flattened_control_expected_loss": "none material in bounded packet"},
        metadata={"phase": "dag_v4_phase2"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="irregular_graph_feedback_lineage",
        target_family="got_v2",
        topology_class="G",
        where_it_appears="feedback merge after refine over bounded sorting graph",
        current_workaround="topology audit plus replay/export integrity packet",
        why_workaround_is_insufficient="no insufficiency observed in bounded v2 packet",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_level_only",
        seen_in_other_targets=[],
        seen_in_consumers=["optimize_preflight", "rl_preflight"],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"selected_candidate_id": run.selected_candidate_id},
        metadata={"phase": "dag_v4_phase2"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "topology_audit": topology_audit,
        "replay_export_integrity_packet": replay_export_integrity,
        "repeated_shape_entry": repeated_shape_entry,
        "removed_assumptions": ["single_refine_step_only", "no_feedback_merge_after_refine"],
    }


def build_dag_v4_got_v2_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_got_v2_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "topology_audit": example["topology_audit"].to_dict(),
        "replay_export_integrity_packet": example["replay_export_integrity_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
        "removed_assumptions": list(example["removed_assumptions"]),
    }


def build_dag_v4_tot_v2_packet() -> Dict[str, object]:
    """Build the ToT v2 re-probe packet that introduces bounded reopen behavior."""

    search_id = "search.dag_v4.tot_v2"
    seeds = [
        SearchCandidate(
            candidate_id=f"{search_id}.cand.seed.{index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            parent_ids=[],
            round_index=0,
            depth=0,
            payload_ref=f"artifacts/search/{search_id}/seed_{index}.json",
            score_vector={"correctness_score": score, "frontier_rank_score": rank},
            usage={"prompt_tokens": 24 + index, "completion_tokens": 14 + index},
            status="seeded",
            reasoning_summary_ref=f"artifacts/search/{search_id}/seed_{index}_summary.md",
            metadata={"thought_kind": "initial_state", "game24_instance": "8,8,3,3", "seed_index": index},
        )
        for index, (score, rank) in enumerate(((0.31, 0.42), (0.37, 0.48), (0.34, 0.45)), start=1)
    ]
    expand_a = SearchCandidate(
        candidate_id=f"{search_id}.cand.expand.a",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[seeds[1].candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/expand_a.json",
        score_vector={"correctness_score": 0.56, "frontier_rank_score": 0.68},
        usage={"prompt_tokens": 37, "completion_tokens": 19},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/expand_a.md",
        metadata={"thought_kind": "expanded_state", "action": "expand"},
    )
    expand_b = SearchCandidate(
        candidate_id=f"{search_id}.cand.expand.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[seeds[2].candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/expand_b.json",
        score_vector={"correctness_score": 0.52, "frontier_rank_score": 0.61},
        usage={"prompt_tokens": 36, "completion_tokens": 18},
        status="discarded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/expand_b.md",
        metadata={"thought_kind": "expanded_state", "action": "expand"},
    )
    reopen_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.reopen.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[expand_b.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/reopen_b.json",
        score_vector={"correctness_score": 0.74, "frontier_rank_score": 0.79},
        usage={"prompt_tokens": 33, "completion_tokens": 16},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/reopen_b.md",
        metadata={"thought_kind": "reopened_state", "action": "reopen_expand", "reopened_from_candidate_id": expand_b.candidate_id},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[reopen_candidate.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/final_candidate.json",
        score_vector={"correctness_score": 0.9, "frontier_rank_score": 0.85},
        usage={"prompt_tokens": 41, "completion_tokens": 17},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final_candidate.md",
        metadata={"thought_kind": "solution_state", "action": "select_final"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.expand",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[item.candidate_id for item in seeds],
            output_candidate_ids=[expand_a.candidate_id, expand_b.candidate_id],
            metadata={"recipe": "tot_game24_v2", "frontier_policy": "expand_rank_prune_reopen"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.prune",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="discard",
            input_candidate_ids=[expand_b.candidate_id],
            output_candidate_ids=[],
            metadata={"recipe": "tot_game24_v2", "pruned_candidate_ids": [expand_b.candidate_id], "rationale": "lower frontier rank"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.reopen",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="expand",
            input_candidate_ids=[expand_b.candidate_id],
            output_candidate_ids=[reopen_candidate.candidate_id],
            metadata={"recipe": "tot_game24_v2", "reopen_from_candidate_id": expand_b.candidate_id, "budget_conditioned_reopen": True},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.final",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="select",
            input_candidate_ids=[reopen_candidate.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "tot_game24_v2", "termination": "valid_24_expression"},
        ),
    ]
    frontiers = [
        SearchFrontier(frontier_id=f"{search_id}.frontier.0", search_id=search_id, round_index=0, candidate_ids=[item.candidate_id for item in seeds], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.1", search_id=search_id, round_index=1, candidate_ids=[expand_a.candidate_id, expand_b.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.2", search_id=search_id, round_index=2, candidate_ids=[reopen_candidate.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.3", search_id=search_id, round_index=3, candidate_ids=[final_candidate.candidate_id], status="completed"),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="tot_game24_frontier_packet_v2",
        candidates=[*seeds, expand_a, expand_b, reopen_candidate, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "dag_v4_phase2",
            "paper_key": "tree_of_thoughts",
            "packet_id": "tot_game24_v2",
            "task_class": "game_of_24",
            "frontier_policy": "expand_rank_prune_reopen",
            "removed_assumptions": ["reopen_disabled", "single_frontier_pass_only"],
            "max_llm_calls": 14,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.tot_v2.profile.v1",
        paper_key="tree_of_thoughts",
        paper_title="Tree of Thoughts: Deliberate Problem Solving with Large Language Models",
        family_kind="adaptive_frontier_search",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="high_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="tot.game24.slice.v2",
        control_profile={
            "task_class": "game_of_24",
            "frontier_policy": "expand_rank_prune_reopen",
            "reopen_policy": "enabled_bounded_once",
            "evaluator_control": "required",
        },
        baseline_ids=[
            "cot",
            "self_consistency",
            "reranking",
            "fixed_width_no_backtracking",
            "no_self_eval",
            "discriminator_control",
        ],
        metadata={"phase": "dag_v4_phase2", "packet_id": "tot_game24_v2", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.tot_v2.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="high_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="controlled",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "harder bounded game-of-24 packet only",
            "removed_assumptions": ["reopen_disabled", "single_frontier_pass_only"],
            "frontier_watch": ["reopen_truth", "budget_conditioned_frontier_policy"],
        },
        metadata={"phase": "dag_v4_phase2"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.tot_v2.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "tot_v2.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 7, "unit": "calls"},
            {"entry_id": "tot_v2.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "tot_v2.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "tot_v2.reopen_count", "kind": "reopen_count", "label": "reopen_count", "quantity": 1.0, "unit": "events"},
            {"entry_id": "tot_v2.peak_parallel_width", "kind": "peak_parallel_width", "label": "peak_parallel_width", "quantity": 3.0, "unit": "branches"},
        ],
        normalization_rule="game24_frontier_reopen_packet_matched",
        metadata={"phase": "dag_v4_phase2"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.tot_v2.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="game24_frontier_reopen_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase2", "task_class": "game_of_24"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.tot_v2.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "tot_v2.dev.01", "severity": "medium", "summary": "Still a Game-of-24-first packet rather than broad ToT coverage."},
            {"deviation_id": "tot_v2.dev.02", "severity": "low", "summary": "Reopen behavior is enabled only once to isolate bounded frontier semantics."},
        ],
        metadata={"phase": "dag_v4_phase2"},
    )
    frontier_policy_audit = FrontierPolicyAudit(
        audit_id="dag_v4.tot_v2.frontier.v1",
        target_family="tot_v2",
        topology_class="F",
        select_prune_reconstructable=True,
        budget_conditioned_reconstructable=True,
        reopen_backtrack_reconstructable=True,
        consumer_can_explain_frontier=True,
        shadow_policy_required=False,
        notes={"reopen_count": 1, "reopen_candidate_id": reopen_candidate.candidate_id},
        metadata={"phase": "dag_v4_phase2"},
    )
    benchmark_control_packet = BenchmarkControlPacket(
        packet_id="dag_v4.tot_v2.control.v1",
        target_family="tot_v2",
        control_kind="evaluator_strength_and_discriminator_control",
        evaluator_stack=["packet_review_judge", "frontier_rank_checker", "bounded_compute_checker"],
        controls=["match_call_budget", "record_reopen_policy", "explicit_discriminator_control"],
        known_confound_risks=["weak evaluator overstates frontier gap", "Game-of-24 task simplification"],
        notes={"purpose": "distinguish frontier truth from evaluator weakness"},
        metadata={"phase": "dag_v4_phase2"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="adaptive_frontier_reopen_truth",
        target_family="tot_v2",
        topology_class="F",
        where_it_appears="bounded single-reopen Game-of-24 packet",
        current_workaround="frontier policy audit plus benchmark control packet",
        why_workaround_is_insufficient="harder adaptive-frontier families still pending before any repeated-shape claim",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_evaluator_control_only",
        seen_in_other_targets=[],
        seen_in_consumers=["optimize_preflight", "rl_preflight"],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"reopen_count": 1},
        metadata={"phase": "dag_v4_phase2"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "frontier_policy_audit": frontier_policy_audit,
        "benchmark_control_packet": benchmark_control_packet,
        "repeated_shape_entry": repeated_shape_entry,
        "removed_assumptions": ["reopen_disabled", "single_frontier_pass_only"],
    }


def build_dag_v4_tot_v2_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_tot_v2_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "frontier_policy_audit": example["frontier_policy_audit"].to_dict(),
        "benchmark_control_packet": example["benchmark_control_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
        "removed_assumptions": list(example["removed_assumptions"]),
    }


def build_dag_v4_moa_v2_packet() -> Dict[str, object]:
    """Build the MoA v2 re-probe packet that deepens layered heterogeneity."""

    search_id = "search.dag_v4.moa_v2"
    seeds = [
        SearchCandidate(
            candidate_id=f"{search_id}.cand.seed.{index}",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.0",
            parent_ids=[],
            round_index=0,
            depth=0,
            payload_ref=f"artifacts/search/{search_id}/seed_{index}.json",
            score_vector={"correctness_score": score, "judge_rank_score": rank},
            usage={"prompt_tokens": 28 + index, "completion_tokens": 16 + index},
            status="seeded",
            reasoning_summary_ref=f"artifacts/search/{search_id}/seed_{index}.md",
            metadata={"layer_index": 0, "roster_role": role, "model_name": model},
        )
        for index, (score, rank, role, model) in enumerate(
            (
                (0.41, 0.51, "researcher_a", "gpt_5_4_mini"),
                (0.43, 0.53, "researcher_b", "claude_sonnet"),
                (0.4, 0.5, "researcher_c", "gemini_flash"),
                (0.44, 0.55, "researcher_d", "gpt_5_4_mini"),
            ),
            start=1,
        )
    ]
    layer1_a = SearchCandidate(
        candidate_id=f"{search_id}.cand.layer1.a",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[item.candidate_id for item in seeds],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/layer1_a.json",
        score_vector={"correctness_score": 0.64, "judge_rank_score": 0.71},
        usage={"prompt_tokens": 64, "completion_tokens": 24},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/layer1_a.md",
        metadata={"layer_index": 1, "aggregation_style": "cross_layer_fan_in", "roster_role": "aggregator_a", "model_name": "gpt_5_4_mini"},
    )
    layer1_b = SearchCandidate(
        candidate_id=f"{search_id}.cand.layer1.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[item.candidate_id for item in seeds],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/layer1_b.json",
        score_vector={"correctness_score": 0.66, "judge_rank_score": 0.73},
        usage={"prompt_tokens": 63, "completion_tokens": 24},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/layer1_b.md",
        metadata={"layer_index": 1, "aggregation_style": "cross_layer_fan_in", "roster_role": "aggregator_b", "model_name": "claude_sonnet"},
    )
    judge_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.layer2.judge",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/layer2_judge.json",
        score_vector={"correctness_score": 0.74, "judge_rank_score": 0.81},
        usage={"prompt_tokens": 47, "completion_tokens": 18},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/layer2_judge.md",
        metadata={"layer_index": 2, "roster_role": "judge", "model_name": "gpt_5_4_mini"},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[layer1_a.candidate_id, layer1_b.candidate_id, judge_candidate.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/final.json",
        score_vector={"correctness_score": 0.84, "judge_rank_score": 0.88},
        usage={"prompt_tokens": 38, "completion_tokens": 16},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final.md",
        metadata={"layer_index": 3, "roster_role": "final_synthesizer", "model_name": "gpt_5_4_mini"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.layer1",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="aggregate",
            input_candidate_ids=[item.candidate_id for item in seeds],
            output_candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
            metadata={"recipe": "moa_v2", "fan_in_size": len(seeds), "layer_transition": "0_to_1"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.judge",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="verify",
            input_candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id],
            output_candidate_ids=[judge_candidate.candidate_id],
            metadata={"recipe": "moa_v2", "layer_transition": "1_to_2", "judge_required": True},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.final",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="select",
            input_candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id, judge_candidate.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "moa_v2", "layer_transition": "2_to_3", "judge_backed": True},
        ),
    ]
    frontiers = [
        SearchFrontier(frontier_id=f"{search_id}.frontier.0", search_id=search_id, round_index=0, candidate_ids=[item.candidate_id for item in seeds], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.1", search_id=search_id, round_index=1, candidate_ids=[layer1_a.candidate_id, layer1_b.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.2", search_id=search_id, round_index=2, candidate_ids=[judge_candidate.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.3", search_id=search_id, round_index=3, candidate_ids=[final_candidate.candidate_id], status="completed"),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="moa_layered_fan_in_packet_v2",
        candidates=[*seeds, layer1_a, layer1_b, judge_candidate, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "dag_v4_phase2",
            "paper_key": "mixture_of_agents",
            "packet_id": "moa_layered_v2",
            "removed_assumptions": ["small_fixed_roster_only", "single_judge_pass_only"],
            "max_llm_calls": 12,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.moa_v2.profile.v1",
        paper_key="mixture_of_agents",
        paper_title="Mixture-of-Agents Enhances Large Language Model Capabilities",
        family_kind="layered_fan_in_reasoning",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="moa.bounded_multihop_qa.slice.v2",
        control_profile={
            "task_class": "bounded_multihop_qa",
            "layer_count": 4,
            "agents_per_layer": [4, 2, 1, 1],
            "roster_policy": "heterogeneous",
            "judge_required": True,
        },
        baseline_ids=[
            "best_single_model",
            "ensemble_vote_judge",
            "one_layer_moa",
            "homogeneous_roster",
            "sequential_summarization",
        ],
        metadata={"phase": "dag_v4_phase2", "packet_id": "moa_layered_v2", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.moa_v2.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="bounded",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "harder bounded layered-fan-in packet only",
            "removed_assumptions": ["small_fixed_roster_only", "single_judge_pass_only"],
            "fan_in_watch": ["layered_heterogeneity_provenance", "judge_backed_fan_in"],
        },
        metadata={"phase": "dag_v4_phase2"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.moa_v2.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "moa_v2.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 8, "unit": "calls"},
            {"entry_id": "moa_v2.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "moa_v2.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "moa_v2.layer_count", "kind": "layer_count", "label": "layer_count", "quantity": 4.0, "unit": "layers"},
            {"entry_id": "moa_v2.judge_steps", "kind": "judge_steps", "label": "judge_steps", "quantity": 1.0, "unit": "steps"},
        ],
        normalization_rule="bounded_moa_layered_v2_packet_matched",
        metadata={"phase": "dag_v4_phase2"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.moa_v2.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="bounded_moa_layered_v2_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase2"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.moa_v2.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "moa_v2.dev.01", "severity": "medium", "summary": "Still bounded to a synthetic multihop-QA packet rather than a broad judged benchmark stack."},
            {"deviation_id": "moa_v2.dev.02", "severity": "low", "summary": "Judge-backed synthesis is explicit but remains single-pass in the v2 packet."},
        ],
        metadata={"phase": "dag_v4_phase2"},
    )
    topology_audit = TopologyAudit(
        audit_id="dag_v4.moa_v2.topology.v1",
        target_family="moa_v2",
        topology_class="H",
        parentage_reconstructable=True,
        fan_flow_reconstructable=True,
        feedback_loop_reconstructable=False,
        shadow_state_required=False,
        notes={"fan_in_size": 4, "judge_stage_present": True},
        metadata={"phase": "dag_v4_phase2"},
    )
    assessment_lineage = AssessmentLineagePacket(
        packet_id="dag_v4.moa_v2.assessment_lineage.v1",
        target_family="moa_v2",
        topology_class="H",
        assessment_kinds=["judge"],
        action_links=[{"assessment_id": "judge.layer2", "action_id": "select.final", "target_candidate_id": final_candidate.candidate_id}],
        mixed_chain_reconstructable=True,
        notes={"judge_stage": judge_candidate.candidate_id},
        metadata={"phase": "dag_v4_phase2"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="layered_heterogeneity_provenance",
        target_family="moa_v2",
        topology_class="H",
        where_it_appears="heterogeneous layered fan-in with explicit judge stage",
        current_workaround="topology audit plus assessment lineage packet",
        why_workaround_is_insufficient="no insufficiency observed yet; broader heterogeneity tranche still pending",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_benchmark_scope_only",
        seen_in_other_targets=[],
        seen_in_consumers=["optimize_preflight", "rl_preflight"],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"judge_stage_present": True},
        metadata={"phase": "dag_v4_phase2"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "topology_audit": topology_audit,
        "assessment_lineage_packet": assessment_lineage,
        "repeated_shape_entry": repeated_shape_entry,
        "removed_assumptions": ["small_fixed_roster_only", "single_judge_pass_only"],
    }


def build_dag_v4_moa_v2_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_moa_v2_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "topology_audit": example["topology_audit"].to_dict(),
        "assessment_lineage_packet": example["assessment_lineage_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
        "removed_assumptions": list(example["removed_assumptions"]),
    }


def build_dag_v4_codetree_v2_packet() -> Dict[str, object]:
    """Build the CodeTree v2 re-probe packet that deepens repair and workspace lineage pressure."""

    search_id = "search.dag_v4.codetree_v2"
    seed = SearchCandidate(
        candidate_id=f"{search_id}.cand.strategy",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.0",
        parent_ids=[],
        round_index=0,
        depth=0,
        payload_ref=f"artifacts/search/{search_id}/strategy.json",
        score_vector={"correctness_score": 0.43, "repairability_score": 0.57},
        usage={"prompt_tokens": 32, "completion_tokens": 18},
        status="seeded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/strategy.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_strategy.json",
        metadata={"stage_role": "thinker", "benchmark_slice": "toy_patch_pair_v2"},
    )
    solver = SearchCandidate(
        candidate_id=f"{search_id}.cand.solver",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[seed.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/solver.json",
        score_vector={"correctness_score": 0.59, "repairability_score": 0.63},
        usage={"prompt_tokens": 49, "completion_tokens": 27},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/solver.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_solver.json",
        metadata={"stage_role": "solver", "tree_action": "expand_patch"},
    )
    critic = SearchCandidate(
        candidate_id=f"{search_id}.cand.critic",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[solver.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/critic.json",
        score_vector={"correctness_score": 0.5, "repairability_score": 0.72},
        usage={"prompt_tokens": 30, "completion_tokens": 17},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/critic.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_solver.json",
        metadata={"stage_role": "critic", "tree_action": "critique_patch"},
    )
    debugger = SearchCandidate(
        candidate_id=f"{search_id}.cand.debugger",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[solver.candidate_id, critic.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/debugger.json",
        score_vector={"correctness_score": 0.79, "repairability_score": 0.84},
        usage={"prompt_tokens": 46, "completion_tokens": 25},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/debugger.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_debugger.json",
        metadata={"stage_role": "debugger", "tree_action": "repair_after_feedback"},
    )
    verifier = SearchCandidate(
        candidate_id=f"{search_id}.cand.verifier",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.4",
        parent_ids=[debugger.candidate_id],
        round_index=4,
        depth=4,
        payload_ref=f"artifacts/search/{search_id}/verifier.json",
        score_vector={"correctness_score": 0.82, "repairability_score": 0.86},
        usage={"prompt_tokens": 26, "completion_tokens": 14},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/verifier.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_debugger.json",
        metadata={"stage_role": "verifier", "tree_action": "recheck_after_repair"},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.5",
        parent_ids=[debugger.candidate_id, verifier.candidate_id],
        round_index=5,
        depth=5,
        payload_ref=f"artifacts/search/{search_id}/final.json",
        score_vector={"correctness_score": 0.89, "repairability_score": 0.92},
        usage={"prompt_tokens": 22, "completion_tokens": 12},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_debugger.json",
        metadata={"stage_role": "selector", "tree_action": "accept_patch"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.expand",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[seed.candidate_id],
            output_candidate_ids=[solver.candidate_id],
            metadata={"recipe": "codetree_v2", "stage_transition": "thinker_to_solver"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.verify.critic",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="verify",
            input_candidate_ids=[solver.candidate_id],
            output_candidate_ids=[critic.candidate_id],
            metadata={"recipe": "codetree_v2", "stage_transition": "solver_to_critic", "feedback_kind": "execution_plus_review"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.repair",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="execute",
            input_candidate_ids=[solver.candidate_id, critic.candidate_id],
            output_candidate_ids=[debugger.candidate_id],
            metadata={"recipe": "codetree_v2", "stage_transition": "critic_to_debugger"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.verify.repair",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.4",
            round_index=4,
            operator_kind="verify",
            input_candidate_ids=[debugger.candidate_id],
            output_candidate_ids=[verifier.candidate_id],
            metadata={"recipe": "codetree_v2", "stage_transition": "debugger_to_verifier", "feedback_kind": "post_repair_recheck"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.select",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.5",
            round_index=5,
            operator_kind="select",
            input_candidate_ids=[debugger.candidate_id, verifier.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "codetree_v2", "termination": "all_visible_tests_pass_after_repair"},
        ),
    ]
    frontiers = [
        SearchFrontier(frontier_id=f"{search_id}.frontier.0", search_id=search_id, round_index=0, candidate_ids=[seed.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.1", search_id=search_id, round_index=1, candidate_ids=[solver.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.2", search_id=search_id, round_index=2, candidate_ids=[critic.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.3", search_id=search_id, round_index=3, candidate_ids=[debugger.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.4", search_id=search_id, round_index=4, candidate_ids=[verifier.candidate_id], status="completed"),
        SearchFrontier(frontier_id=f"{search_id}.frontier.5", search_id=search_id, round_index=5, candidate_ids=[final_candidate.candidate_id], status="completed"),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="codetree_stage_patch_packet_v2",
        candidates=[seed, solver, critic, debugger, verifier, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "dag_v4_phase2",
            "paper_key": "codetree",
            "packet_id": "codetree_patch_v2",
            "removed_assumptions": ["single_repair_pass_only", "no_post_repair_verifier_stage"],
            "max_llm_calls": 11,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.codetree_v2.profile.v1",
        paper_key="codetree",
        paper_title="CodeTree",
        family_kind="stage_heterogeneous_code_tree",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="codetree.toy_patch_pair.slice.v2",
        control_profile={
            "task_class": "bounded_code_patch",
            "visible_hidden_test_policy": "visible_only_in_v2",
            "execution_feedback": "required",
            "critic_role": "explicit",
            "post_repair_verifier_stage": True,
        },
        baseline_ids=[
            "direct_single_shot_code",
            "cot_code",
            "iterative_self_debug",
            "strategy_solver_pair",
            "execution_only_critic",
            "no_tree_iterative_refine",
        ],
        metadata={"phase": "dag_v4_phase2", "packet_id": "codetree_patch_v2", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.codetree_v2.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="bounded",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "harder bounded code-search-tree packet only",
            "removed_assumptions": ["single_repair_pass_only", "no_post_repair_verifier_stage"],
            "workspace_watch": ["workspace_borrow_merge_lineage", "mixed_assessment_chain"],
        },
        metadata={"phase": "dag_v4_phase2"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.codetree_v2.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "codetree_v2.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 6, "unit": "calls"},
            {"entry_id": "codetree_v2.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "codetree_v2.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "codetree_v2.execution_rounds", "kind": "execution_rounds", "label": "execution_rounds", "quantity": 3.0, "unit": "rounds"},
            {"entry_id": "codetree_v2.verify_rounds", "kind": "verify_rounds", "label": "verify_rounds", "quantity": 2.0, "unit": "rounds"},
        ],
        normalization_rule="bounded_codetree_patch_v2_packet_matched",
        metadata={"phase": "dag_v4_phase2"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.codetree_v2.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="bounded_codetree_patch_v2_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase2"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.codetree_v2.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "codetree_v2.dev.01", "severity": "medium", "summary": "Still a toy patch slice rather than benchmark-scale code search."},
            {"deviation_id": "codetree_v2.dev.02", "severity": "low", "summary": "Workspace lineage is bounded to explicit workspace refs rather than broad repository-state borrowing."},
        ],
        metadata={"phase": "dag_v4_phase2"},
    )
    topology_audit = TopologyAudit(
        audit_id="dag_v4.codetree_v2.topology.v1",
        target_family="codetree_v2",
        topology_class="W",
        parentage_reconstructable=True,
        fan_flow_reconstructable=True,
        feedback_loop_reconstructable=True,
        shadow_state_required=False,
        notes={"workspace_refs_present": True},
        metadata={"phase": "dag_v4_phase2"},
    )
    assessment_lineage = AssessmentLineagePacket(
        packet_id="dag_v4.codetree_v2.assessment_lineage.v1",
        target_family="codetree_v2",
        topology_class="W",
        assessment_kinds=["verify", "execute"],
        action_links=[
            {"assessment_id": "critic.verify", "action_id": "debugger.repair", "target_candidate_id": debugger.candidate_id},
            {"assessment_id": "verifier.recheck", "action_id": "select.final", "target_candidate_id": final_candidate.candidate_id},
        ],
        mixed_chain_reconstructable=True,
        notes={"workspace_refs": [seed.workspace_ref, solver.workspace_ref, debugger.workspace_ref]},
        metadata={"phase": "dag_v4_phase2"},
    )
    replay_export_integrity = ReplayExportIntegrityPacket(
        packet_id="dag_v4.codetree_v2.replay_export.v1",
        target_family="codetree_v2",
        topology_class="W",
        export_modes=["trajectory_export", "offline_dataset_projection", "flattened_transcript_control"],
        preserved_semantics=["assessment_to_action_chain", "workspace_ref_lineage", "selected_candidate_identity"],
        lost_semantics=[],
        shadow_assumptions_required=False,
        notes={"visible_hidden_test_policy": "visible_only_in_v2"},
        metadata={"phase": "dag_v4_phase2"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="workspace_borrow_merge_lineage",
        target_family="codetree_v2",
        topology_class="W",
        where_it_appears="repair and verifier stages sharing explicit workspace lineage",
        current_workaround="workspace refs plus assessment lineage packet plus replay/export audit",
        why_workaround_is_insufficient="no insufficiency observed yet; shared-workspace class is still only partially pressured",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_harness_scope_only",
        seen_in_other_targets=[],
        seen_in_consumers=["optimize_preflight", "rl_preflight"],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"post_repair_verifier_stage": True},
        metadata={"phase": "dag_v4_phase2"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "topology_audit": topology_audit,
        "assessment_lineage_packet": assessment_lineage,
        "replay_export_integrity_packet": replay_export_integrity,
        "repeated_shape_entry": repeated_shape_entry,
        "removed_assumptions": ["single_repair_pass_only", "no_post_repair_verifier_stage"],
    }


def build_dag_v4_codetree_v2_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_codetree_v2_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "topology_audit": example["topology_audit"].to_dict(),
        "assessment_lineage_packet": example["assessment_lineage_packet"].to_dict(),
        "replay_export_integrity_packet": example["replay_export_integrity_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
        "removed_assumptions": list(example["removed_assumptions"]),
    }


def build_dag_v4_bavt_packet() -> Dict[str, object]:
    """Build the BAVT packet that pressures budget-aware frontier value selection."""

    search_id = "search.dag_v4.bavt"
    root = SearchCandidate(
        candidate_id=f"{search_id}.cand.root",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.0",
        parent_ids=[],
        round_index=0,
        depth=0,
        payload_ref=f"artifacts/search/{search_id}/root.json",
        score_vector={"correctness_score": 0.34, "value_estimate": 0.46},
        usage={"prompt_tokens": 24, "completion_tokens": 13},
        status="seeded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/root.md",
        metadata={"state_kind": "root", "task_class": "budgeted_reasoning_tree"},
    )
    branch_a = SearchCandidate(
        candidate_id=f"{search_id}.cand.branch.a",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[root.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/branch_a.json",
        score_vector={"correctness_score": 0.58, "value_estimate": 0.72},
        usage={"prompt_tokens": 31, "completion_tokens": 17},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/branch_a.md",
        metadata={"state_kind": "expanded_branch", "estimated_cost": 2, "value_band": "high"},
    )
    branch_b = SearchCandidate(
        candidate_id=f"{search_id}.cand.branch.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[root.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/branch_b.json",
        score_vector={"correctness_score": 0.52, "value_estimate": 0.64},
        usage={"prompt_tokens": 29, "completion_tokens": 16},
        status="discarded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/branch_b.md",
        metadata={"state_kind": "expanded_branch", "estimated_cost": 4, "value_band": "medium"},
    )
    branch_a_refine = SearchCandidate(
        candidate_id=f"{search_id}.cand.branch.a.refine",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[branch_a.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/branch_a_refine.json",
        score_vector={"correctness_score": 0.77, "value_estimate": 0.83},
        usage={"prompt_tokens": 27, "completion_tokens": 15},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/branch_a_refine.md",
        metadata={"state_kind": "refined_branch", "budget_reallocated": True, "estimated_cost": 2},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[branch_a_refine.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/final.json",
        score_vector={"correctness_score": 0.88, "value_estimate": 0.87},
        usage={"prompt_tokens": 23, "completion_tokens": 12},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final.md",
        metadata={"state_kind": "selected_solution", "selection_rule": "budget_aware_value_max"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.expand",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[root.candidate_id],
            output_candidate_ids=[branch_a.candidate_id, branch_b.candidate_id],
            metadata={"recipe": "bavt_budget_packet", "frontier_policy": "value_per_cost_rank"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.prune.low_value_cost",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="discard",
            input_candidate_ids=[branch_b.candidate_id],
            output_candidate_ids=[],
            metadata={
                "recipe": "bavt_budget_packet",
                "discard_reason": "lower_value_per_cost",
                "budget_spend_after_discard": 2,
            },
        ),
        SearchEvent(
            event_id=f"{search_id}.event.reallocate.refine",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="expand",
            input_candidate_ids=[branch_a.candidate_id],
            output_candidate_ids=[branch_a_refine.candidate_id],
            metadata={
                "recipe": "bavt_budget_packet",
                "budget_reallocation": "from_discarded_branch_to_branch_a",
                "frontier_policy": "budget_aware_refine",
            },
        ),
        SearchEvent(
            event_id=f"{search_id}.event.select",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="select",
            input_candidate_ids=[branch_a_refine.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "bavt_budget_packet", "termination": "best_value_under_budget"},
        ),
    ]
    frontiers = [
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.0",
            search_id=search_id,
            round_index=0,
            candidate_ids=[root.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.1",
            search_id=search_id,
            round_index=1,
            candidate_ids=[branch_a.candidate_id, branch_b.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.2",
            search_id=search_id,
            round_index=2,
            candidate_ids=[branch_a_refine.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.3",
            search_id=search_id,
            round_index=3,
            candidate_ids=[final_candidate.candidate_id],
            status="completed",
        ),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="bavt_budget_aware_frontier_packet",
        candidates=[root, branch_a, branch_b, branch_a_refine, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "dag_v4_phase3",
            "paper_key": "budget_aware_value_tree_search",
            "packet_id": "bavt_budget_packet",
            "topology_class": "F",
            "frontier_policy": "value_per_cost_rank_then_reallocate",
            "budget_limit": 4,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.bavt.profile.v1",
        paper_key="budget_aware_value_tree_search",
        paper_title="Budget-Aware Value Tree Search",
        family_kind="adaptive_frontier_search",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="bavt.budgeted.reasoning.slice.v1",
        control_profile={
            "task_class": "budgeted_reasoning_tree",
            "frontier_policy": "value_per_cost_rank_then_reallocate",
            "budget_limit": 4,
            "reallocation_policy": "single_reallocate_after_discard",
        },
        baseline_ids=[
            "cot",
            "fixed_width_tree",
            "greedy_value_only",
            "budget_blind_search",
            "single_path_refine",
        ],
        metadata={"phase": "dag_v4_phase3", "packet_id": "bavt_budget_packet", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.bavt.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="controlled",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "bounded budget-aware frontier packet only",
            "frontier_watch": ["value_per_cost_truth", "budget_reallocation_trace"],
        },
        metadata={"phase": "dag_v4_phase3"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.bavt.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "bavt.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 5, "unit": "calls"},
            {"entry_id": "bavt.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "bavt.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "bavt.budget_limit", "kind": "budget_limit", "label": "budget_limit", "quantity": 4.0, "unit": "budget_units"},
            {"entry_id": "bavt.reallocation_steps", "kind": "reallocation_steps", "label": "reallocation_steps", "quantity": 1.0, "unit": "steps"},
        ],
        normalization_rule="budget_aware_frontier_packet_matched",
        metadata={"phase": "dag_v4_phase3"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.bavt.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="budget_aware_frontier_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase3"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.bavt.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "bavt.dev.01", "severity": "medium", "summary": "Packet is bounded to one explicit budget reallocation rather than long adaptive rollout."},
            {"deviation_id": "bavt.dev.02", "severity": "low", "summary": "Value estimates are synthetic packet values, not benchmark-calibrated estimates."},
        ],
        metadata={"phase": "dag_v4_phase3"},
    )
    frontier_policy_audit = FrontierPolicyAudit(
        audit_id="dag_v4.bavt.frontier.v1",
        target_family="bavt",
        topology_class="F",
        select_prune_reconstructable=True,
        budget_conditioned_reconstructable=True,
        reopen_backtrack_reconstructable=True,
        consumer_can_explain_frontier=True,
        shadow_policy_required=False,
        notes={"policy": "value_per_cost_rank_then_reallocate", "budget_limit": 4},
        metadata={"phase": "dag_v4_phase3"},
    )
    benchmark_control_packet = BenchmarkControlPacket(
        packet_id="dag_v4.bavt.control.v1",
        target_family="bavt",
        control_kind="budget_conditioned_frontier_and_evaluator_control",
        evaluator_stack=["value_rank_checker", "budget_trace_checker", "packet_review_judge"],
        controls=["match_budget_limit", "record_value_per_cost_inputs", "compare_against_budget_blind_control"],
        known_confound_risks=["weak value estimator overstating frontier need", "synthetic task under-representing long horizon"],
        notes={"purpose": "distinguish frontier truth from evaluator or estimator weakness"},
        metadata={"phase": "dag_v4_phase3"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="budget_conditioned_frontier_truth",
        target_family="bavt",
        topology_class="F",
        where_it_appears="budget-aware value-ranked frontier with explicit reallocation",
        current_workaround="frontier policy audit plus benchmark control packet",
        why_workaround_is_insufficient="no insufficiency observed in bounded BAVT packet yet",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_evaluator_control_only",
        seen_in_other_targets=["tot_v2"],
        seen_in_consumers=[],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"review_threshold_missing": "same-class only, no consumer confirmation"},
        metadata={"phase": "dag_v4_phase3"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "frontier_policy_audit": frontier_policy_audit,
        "benchmark_control_packet": benchmark_control_packet,
        "repeated_shape_entry": repeated_shape_entry,
    }


def build_dag_v4_bavt_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_bavt_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "frontier_policy_audit": example["frontier_policy_audit"].to_dict(),
        "benchmark_control_packet": example["benchmark_control_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
    }


def build_dag_v4_adaptive_parallel_mcts_lite_packet() -> Dict[str, object]:
    """Build a bounded adaptive-parallel-MCTS-lite packet for later-phase frontier pressure."""

    search_id = "search.dag_v4.apmcts_lite"
    root = SearchCandidate(
        candidate_id=f"{search_id}.cand.root",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.0",
        parent_ids=[],
        round_index=0,
        depth=0,
        payload_ref=f"artifacts/search/{search_id}/root.json",
        score_vector={"correctness_score": 0.33, "uct_score": 0.44},
        usage={"prompt_tokens": 25, "completion_tokens": 14},
        status="seeded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/root.md",
        metadata={"state_kind": "root", "task_class": "bounded_parallel_tree_search"},
    )
    rollout_a = SearchCandidate(
        candidate_id=f"{search_id}.cand.rollout.a",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[root.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/rollout_a.json",
        score_vector={"correctness_score": 0.57, "uct_score": 0.71},
        usage={"prompt_tokens": 34, "completion_tokens": 19},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/rollout_a.md",
        metadata={"state_kind": "parallel_rollout", "visit_count": 2, "parallel_slot": 1},
    )
    rollout_b = SearchCandidate(
        candidate_id=f"{search_id}.cand.rollout.b",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[root.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/rollout_b.json",
        score_vector={"correctness_score": 0.54, "uct_score": 0.66},
        usage={"prompt_tokens": 33, "completion_tokens": 18},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/rollout_b.md",
        metadata={"state_kind": "parallel_rollout", "visit_count": 1, "parallel_slot": 2},
    )
    backprop = SearchCandidate(
        candidate_id=f"{search_id}.cand.backprop",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[rollout_a.candidate_id, rollout_b.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/backprop.json",
        score_vector={"correctness_score": 0.72, "uct_score": 0.79},
        usage={"prompt_tokens": 28, "completion_tokens": 15},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/backprop.md",
        metadata={"state_kind": "backprop_update", "parallel_merge": True, "visit_count": 3},
    )
    final_candidate = SearchCandidate(
        candidate_id=f"{search_id}.cand.final",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[backprop.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/final.json",
        score_vector={"correctness_score": 0.84, "uct_score": 0.81},
        usage={"prompt_tokens": 22, "completion_tokens": 11},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/final.md",
        metadata={"state_kind": "selected_solution", "selection_rule": "best_parallel_uct_after_backprop"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.parallel_expand",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[root.candidate_id],
            output_candidate_ids=[rollout_a.candidate_id, rollout_b.candidate_id],
            metadata={"recipe": "adaptive_parallel_mcts_lite", "frontier_policy": "parallel_uct_expand"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.backprop",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="aggregate",
            input_candidate_ids=[rollout_a.candidate_id, rollout_b.candidate_id],
            output_candidate_ids=[backprop.candidate_id],
            metadata={
                "recipe": "adaptive_parallel_mcts_lite",
                "frontier_policy": "parallel_backprop_update",
                "budget_conditioned_parallelism": True,
            },
        ),
        SearchEvent(
            event_id=f"{search_id}.event.select",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="select",
            input_candidate_ids=[backprop.candidate_id],
            output_candidate_ids=[final_candidate.candidate_id],
            metadata={"recipe": "adaptive_parallel_mcts_lite", "termination": "best_parallel_uct"},
        ),
    ]
    frontiers = [
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.0",
            search_id=search_id,
            round_index=0,
            candidate_ids=[root.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.1",
            search_id=search_id,
            round_index=1,
            candidate_ids=[rollout_a.candidate_id, rollout_b.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.2",
            search_id=search_id,
            round_index=2,
            candidate_ids=[backprop.candidate_id],
            status="completed",
        ),
        SearchFrontier(
            frontier_id=f"{search_id}.frontier.3",
            search_id=search_id,
            round_index=3,
            candidate_ids=[final_candidate.candidate_id],
            status="completed",
        ),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="adaptive_parallel_mcts_lite_packet",
        candidates=[root, rollout_a, rollout_b, backprop, final_candidate],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=final_candidate.candidate_id,
        metadata={
            "phase": "dag_v4_phase3",
            "paper_key": "adaptive_parallel_mcts_lite",
            "packet_id": "adaptive_parallel_mcts_lite_packet",
            "topology_class": "F",
            "frontier_policy": "parallel_uct_expand_then_backprop",
            "parallel_budget_slots": 2,
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.apmcts_lite.profile.v1",
        paper_key="adaptive_parallel_mcts_lite",
        paper_title="Adaptive Parallel MCTS-lite",
        family_kind="adaptive_frontier_search",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="adaptive_parallel_mcts.reasoning.slice.v1",
        control_profile={
            "task_class": "bounded_parallel_tree_search",
            "frontier_policy": "parallel_uct_expand_then_backprop",
            "parallel_budget_slots": 2,
            "backprop_required": True,
        },
        baseline_ids=[
            "cot",
            "sequential_mcts_lite",
            "fixed_width_parallel_tree",
            "no_backprop_parallel_expand",
            "best_single_rollout",
        ],
        metadata={"phase": "dag_v4_phase3", "packet_id": "adaptive_parallel_mcts_lite_packet", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.apmcts_lite.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="controlled",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "bounded adaptive parallel MCTS-lite packet only",
            "frontier_watch": ["parallel_backprop_trace", "parallel_budget_slot_truth"],
        },
        metadata={"phase": "dag_v4_phase3"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.apmcts_lite.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "apmcts.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 5, "unit": "calls"},
            {"entry_id": "apmcts.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "apmcts.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "apmcts.parallel_budget_slots", "kind": "parallel_budget_slots", "label": "parallel_budget_slots", "quantity": 2.0, "unit": "slots"},
            {"entry_id": "apmcts.backprop_steps", "kind": "backprop_steps", "label": "backprop_steps", "quantity": 1.0, "unit": "steps"},
        ],
        normalization_rule="adaptive_parallel_frontier_packet_matched",
        metadata={"phase": "dag_v4_phase3"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.apmcts_lite.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="adaptive_parallel_frontier_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase3"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.apmcts_lite.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "apmcts.dev.01", "severity": "medium", "summary": "Packet uses a single bounded backprop update rather than an extended simulation loop."},
            {"deviation_id": "apmcts.dev.02", "severity": "medium", "summary": "UCT-style scores are packet-level stand-ins rather than benchmark-derived values."},
        ],
        metadata={"phase": "dag_v4_phase3"},
    )
    frontier_policy_audit = FrontierPolicyAudit(
        audit_id="dag_v4.apmcts_lite.frontier.v1",
        target_family="adaptive_parallel_mcts_lite",
        topology_class="F",
        select_prune_reconstructable=True,
        budget_conditioned_reconstructable=True,
        reopen_backtrack_reconstructable=True,
        consumer_can_explain_frontier=True,
        shadow_policy_required=False,
        notes={"parallel_budget_slots": 2, "backprop_present": True},
        metadata={"phase": "dag_v4_phase3"},
    )
    benchmark_control_packet = BenchmarkControlPacket(
        packet_id="dag_v4.apmcts_lite.control.v1",
        target_family="adaptive_parallel_mcts_lite",
        control_kind="parallel_frontier_and_evaluator_control",
        evaluator_stack=["uct_trace_checker", "parallel_budget_checker", "packet_review_judge"],
        controls=["match_parallel_budget_slots", "compare_against_sequential_control", "record_backprop_inputs"],
        known_confound_risks=["parallel-serving effects masquerading as frontier truth", "synthetic UCT scores"],
        notes={"purpose": "separate frontier pressure from serving or harness artifacts"},
        metadata={"phase": "dag_v4_phase3"},
    )
    replay_export_integrity = ReplayExportIntegrityPacket(
        packet_id="dag_v4.apmcts_lite.replay_export.v1",
        target_family="adaptive_parallel_mcts_lite",
        topology_class="F",
        export_modes=["trajectory_export", "offline_dataset_projection", "flattened_transcript_control"],
        preserved_semantics=["parallel_branch_identity", "backprop_update", "selected_candidate_identity"],
        lost_semantics=[],
        shadow_assumptions_required=False,
        notes={"parallel_slots_recorded": 2},
        metadata={"phase": "dag_v4_phase3"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="adaptive_parallel_frontier_truth",
        target_family="adaptive_parallel_mcts_lite",
        topology_class="F",
        where_it_appears="parallel expand plus bounded backprop under explicit slot budget",
        current_workaround="frontier policy audit plus replay/export integrity plus benchmark control",
        why_workaround_is_insufficient="no insufficiency observed in bounded packet; class diversity and consumer confirmation still absent",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_harness_control_only",
        seen_in_other_targets=["tot_v2", "bavt"],
        seen_in_consumers=[],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"review_threshold_missing": "same-class only, no helper exhaustion, no consumer confirmation"},
        metadata={"phase": "dag_v4_phase3"},
    )
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "frontier_policy_audit": frontier_policy_audit,
        "benchmark_control_packet": benchmark_control_packet,
        "replay_export_integrity_packet": replay_export_integrity,
        "repeated_shape_entry": repeated_shape_entry,
    }


def build_dag_v4_adaptive_parallel_mcts_lite_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_adaptive_parallel_mcts_lite_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "frontier_policy_audit": example["frontier_policy_audit"].to_dict(),
        "benchmark_control_packet": example["benchmark_control_packet"].to_dict(),
        "replay_export_integrity_packet": example["replay_export_integrity_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
    }


def build_dag_v4_team_of_thoughts_packet() -> Dict[str, object]:
    """Build the Team of Thoughts packet that pressures layered role heterogeneity."""

    search_id = "search.dag_v4.team_of_thoughts"
    planner = SearchCandidate(
        candidate_id=f"{search_id}.cand.planner",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.0",
        parent_ids=[],
        round_index=0,
        depth=0,
        payload_ref=f"artifacts/search/{search_id}/planner.json",
        score_vector={"correctness_score": 0.38, "coordination_score": 0.49},
        usage={"prompt_tokens": 28, "completion_tokens": 16},
        status="seeded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/planner.md",
        metadata={"role": "planner", "team_stage": "plan", "task_class": "bounded_team_reasoning"},
    )
    analyst = SearchCandidate(
        candidate_id=f"{search_id}.cand.analyst",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[planner.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/analyst.json",
        score_vector={"correctness_score": 0.61, "coordination_score": 0.68},
        usage={"prompt_tokens": 34, "completion_tokens": 18},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/analyst.md",
        metadata={"role": "analyst", "team_stage": "specialist_response", "roster_kind": "heterogeneous"},
    )
    skeptic = SearchCandidate(
        candidate_id=f"{search_id}.cand.skeptic",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[planner.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/skeptic.json",
        score_vector={"correctness_score": 0.55, "coordination_score": 0.66},
        usage={"prompt_tokens": 33, "completion_tokens": 17},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/skeptic.md",
        metadata={"role": "skeptic", "team_stage": "specialist_response", "roster_kind": "heterogeneous"},
    )
    synthesizer = SearchCandidate(
        candidate_id=f"{search_id}.cand.synthesizer",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[analyst.candidate_id, skeptic.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/synthesizer.json",
        score_vector={"correctness_score": 0.77, "coordination_score": 0.81},
        usage={"prompt_tokens": 29, "completion_tokens": 15},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/synthesizer.md",
        metadata={"role": "synthesizer", "team_stage": "merge_specialists", "roster_kind": "heterogeneous"},
    )
    reviewer = SearchCandidate(
        candidate_id=f"{search_id}.cand.reviewer",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[synthesizer.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/reviewer.json",
        score_vector={"correctness_score": 0.83, "coordination_score": 0.86},
        usage={"prompt_tokens": 24, "completion_tokens": 13},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/reviewer.md",
        metadata={"role": "reviewer", "team_stage": "final_team_review", "roster_kind": "heterogeneous"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.dispatch",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="expand",
            input_candidate_ids=[planner.candidate_id],
            output_candidate_ids=[analyst.candidate_id, skeptic.candidate_id],
            metadata={"recipe": "team_of_thoughts_packet", "dispatch_policy": "heterogeneous_pair"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.synthesize",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="aggregate",
            input_candidate_ids=[analyst.candidate_id, skeptic.candidate_id],
            output_candidate_ids=[synthesizer.candidate_id],
            metadata={"recipe": "team_of_thoughts_packet", "merge_policy": "planner_absent_specialist_merge"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.review",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="verify",
            input_candidate_ids=[synthesizer.candidate_id],
            output_candidate_ids=[reviewer.candidate_id],
            metadata={"recipe": "team_of_thoughts_packet", "review_policy": "final_team_review"},
        ),
    ]
    frontiers = [
        SearchFrontier(f"{search_id}.frontier.0", search_id, 0, [planner.candidate_id], "completed"),
        SearchFrontier(f"{search_id}.frontier.1", search_id, 1, [analyst.candidate_id, skeptic.candidate_id], "completed"),
        SearchFrontier(f"{search_id}.frontier.2", search_id, 2, [synthesizer.candidate_id], "completed"),
        SearchFrontier(f"{search_id}.frontier.3", search_id, 3, [reviewer.candidate_id], "completed"),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="team_of_thoughts_layered_packet",
        candidates=[planner, analyst, skeptic, synthesizer, reviewer],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=reviewer.candidate_id,
        metadata={
            "phase": "dag_v4_phase4",
            "paper_key": "team_of_thoughts",
            "packet_id": "team_of_thoughts_packet",
            "topology_class": "H",
            "heterogeneous_roles": ["planner", "analyst", "skeptic", "synthesizer", "reviewer"],
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.team_of_thoughts.profile.v1",
        paper_key="team_of_thoughts",
        paper_title="Team of Thoughts",
        family_kind="layered_fan_in_reasoning",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="team_of_thoughts.bounded_reasoning.slice.v1",
        control_profile={
            "task_class": "bounded_team_reasoning",
            "role_set": ["planner", "analyst", "skeptic", "synthesizer", "reviewer"],
            "heterogeneous_roster": True,
            "final_review_stage": True,
        },
        baseline_ids=[
            "single_agent_cot",
            "homogeneous_team_vote",
            "planner_plus_synthesizer_only",
            "no_skeptic_role",
            "single_merge_then_select",
        ],
        metadata={"phase": "dag_v4_phase4", "packet_id": "team_of_thoughts_packet", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.team_of_thoughts.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="controlled",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "bounded heterogeneous team packet only",
            "heterogeneity_watch": ["layered_role_provenance", "roster_semantics_truth"],
        },
        metadata={"phase": "dag_v4_phase4"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.team_of_thoughts.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "tot_team.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 5, "unit": "calls"},
            {"entry_id": "tot_team.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "tot_team.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "tot_team.role_count", "kind": "role_count", "label": "role_count", "quantity": 5.0, "unit": "roles"},
            {"entry_id": "tot_team.merge_steps", "kind": "merge_steps", "label": "merge_steps", "quantity": 1.0, "unit": "steps"},
        ],
        normalization_rule="team_of_thoughts_packet_matched",
        metadata={"phase": "dag_v4_phase4"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.team_of_thoughts.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="team_of_thoughts_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase4"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.team_of_thoughts.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "tot_team.dev.01", "severity": "medium", "summary": "Packet is bounded to a small heterogeneous roster rather than large team scales."},
            {"deviation_id": "tot_team.dev.02", "severity": "low", "summary": "Role semantics are packet-local manifests rather than full public runtime constructs."},
        ],
        metadata={"phase": "dag_v4_phase4"},
    )
    topology_audit = TopologyAudit(
        audit_id="dag_v4.team_of_thoughts.topology.v1",
        target_family="team_of_thoughts",
        topology_class="H",
        parentage_reconstructable=True,
        fan_flow_reconstructable=True,
        feedback_loop_reconstructable=False,
        shadow_state_required=False,
        notes={"role_count": 5, "heterogeneous_roster": True},
        metadata={"phase": "dag_v4_phase4"},
    )
    assessment_lineage = AssessmentLineagePacket(
        packet_id="dag_v4.team_of_thoughts.assessment_lineage.v1",
        target_family="team_of_thoughts",
        topology_class="H",
        assessment_kinds=["verify"],
        action_links=[{"assessment_id": "review.final", "action_id": "select.reviewer", "target_candidate_id": reviewer.candidate_id}],
        mixed_chain_reconstructable=True,
        notes={"final_review_role": "reviewer"},
        metadata={"phase": "dag_v4_phase4"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="layered_role_provenance",
        target_family="team_of_thoughts",
        topology_class="H",
        where_it_appears="heterogeneous specialist roster with explicit final review stage",
        current_workaround="topology audit plus role-stage manifest plus assessment lineage packet",
        why_workaround_is_insufficient="no insufficiency observed yet; D-class tranche still pending",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_manifest_scope_only",
        seen_in_other_targets=["moa_v2"],
        seen_in_consumers=[],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"review_threshold_missing": "cross-class support and helper exhaustion absent"},
        metadata={"phase": "dag_v4_phase4"},
    )
    role_stage_manifest = {
        "manifest_id": "dag_v4.team_of_thoughts.roles.v1",
        "roles": [
            {"role": "planner", "stage": "plan"},
            {"role": "analyst", "stage": "specialist_response"},
            {"role": "skeptic", "stage": "specialist_response"},
            {"role": "synthesizer", "stage": "merge_specialists"},
            {"role": "reviewer", "stage": "final_team_review"},
        ],
        "heterogeneous": True,
    }
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "topology_audit": topology_audit,
        "assessment_lineage_packet": assessment_lineage,
        "repeated_shape_entry": repeated_shape_entry,
        "role_stage_manifest": role_stage_manifest,
    }


def build_dag_v4_team_of_thoughts_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_team_of_thoughts_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "topology_audit": example["topology_audit"].to_dict(),
        "assessment_lineage_packet": example["assessment_lineage_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
        "role_stage_manifest": dict(example["role_stage_manifest"]),
    }


def build_dag_v4_dci_packet() -> Dict[str, object]:
    """Build the DCI packet that pressures typed acts and shared workspace semantics."""

    search_id = "search.dag_v4.dci"
    root_snapshot = SearchWorkspaceSnapshot(
        snapshot_id=f"{search_id}.snapshot.root",
        search_id=search_id,
        branch_id=f"{search_id}.branch.shared",
        artifact_ref=f"artifacts/search/{search_id}/workspace_root.json",
        derived_from_candidate_id=None,
        metadata={"workspace_kind": "shared_deliberation_board"},
    )
    proposer = SearchCandidate(
        candidate_id=f"{search_id}.cand.proposer",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.0",
        parent_ids=[],
        round_index=0,
        depth=0,
        payload_ref=f"artifacts/search/{search_id}/proposer.json",
        score_vector={"correctness_score": 0.41, "deliberation_score": 0.51},
        usage={"prompt_tokens": 27, "completion_tokens": 15},
        status="seeded",
        reasoning_summary_ref=f"artifacts/search/{search_id}/proposer.md",
        workspace_ref=root_snapshot.artifact_ref,
        metadata={"typed_act": "propose_claim", "workspace_mode": "shared"},
    )
    challenger = SearchCandidate(
        candidate_id=f"{search_id}.cand.challenger",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.1",
        parent_ids=[proposer.candidate_id],
        round_index=1,
        depth=1,
        payload_ref=f"artifacts/search/{search_id}/challenger.json",
        score_vector={"correctness_score": 0.53, "deliberation_score": 0.67},
        usage={"prompt_tokens": 31, "completion_tokens": 17},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/challenger.md",
        workspace_ref=root_snapshot.artifact_ref,
        metadata={"typed_act": "raise_objection", "workspace_mode": "shared", "objection_kind": "constraint_violation"},
    )
    reviser = SearchCandidate(
        candidate_id=f"{search_id}.cand.reviser",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.2",
        parent_ids=[proposer.candidate_id, challenger.candidate_id],
        round_index=2,
        depth=2,
        payload_ref=f"artifacts/search/{search_id}/reviser.json",
        score_vector={"correctness_score": 0.71, "deliberation_score": 0.78},
        usage={"prompt_tokens": 30, "completion_tokens": 16},
        status="active",
        reasoning_summary_ref=f"artifacts/search/{search_id}/reviser.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_revision.json",
        metadata={"typed_act": "revise_claim", "workspace_mode": "borrow_then_update"},
    )
    closer = SearchCandidate(
        candidate_id=f"{search_id}.cand.closer",
        search_id=search_id,
        frontier_id=f"{search_id}.frontier.3",
        parent_ids=[reviser.candidate_id],
        round_index=3,
        depth=3,
        payload_ref=f"artifacts/search/{search_id}/closer.json",
        score_vector={"correctness_score": 0.84, "deliberation_score": 0.85},
        usage={"prompt_tokens": 23, "completion_tokens": 12},
        status="selected",
        reasoning_summary_ref=f"artifacts/search/{search_id}/closer.md",
        workspace_ref=f"artifacts/search/{search_id}/workspace_revision.json",
        metadata={"typed_act": "close_deliberation", "workspace_mode": "shared_reopen_closed"},
    )
    revision_snapshot = SearchWorkspaceSnapshot(
        snapshot_id=f"{search_id}.snapshot.revision",
        search_id=search_id,
        branch_id=f"{search_id}.branch.shared",
        artifact_ref=f"artifacts/search/{search_id}/workspace_revision.json",
        derived_from_candidate_id=reviser.candidate_id,
        metadata={"workspace_kind": "shared_deliberation_board", "borrowed_from_snapshot_id": root_snapshot.snapshot_id},
    )
    branch = SearchBranchState(
        branch_id=f"{search_id}.branch.shared",
        search_id=search_id,
        candidate_id=closer.candidate_id,
        snapshot_ids=[root_snapshot.snapshot_id, revision_snapshot.snapshot_id],
        head_snapshot_id=revision_snapshot.snapshot_id,
        status="merged",
        metadata={"workspace_mode": "shared"},
    )
    events = [
        SearchEvent(
            event_id=f"{search_id}.event.object",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.1",
            round_index=1,
            operator_kind="verify",
            input_candidate_ids=[proposer.candidate_id],
            output_candidate_ids=[challenger.candidate_id],
            metadata={"recipe": "dci_typed_deliberation_packet", "typed_act": "raise_objection"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.revise",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.2",
            round_index=2,
            operator_kind="aggregate",
            input_candidate_ids=[proposer.candidate_id, challenger.candidate_id],
            output_candidate_ids=[reviser.candidate_id],
            metadata={"recipe": "dci_typed_deliberation_packet", "typed_act": "revise_claim", "reopen_condition": "objection_unresolved"},
        ),
        SearchEvent(
            event_id=f"{search_id}.event.close",
            search_id=search_id,
            frontier_id=f"{search_id}.frontier.3",
            round_index=3,
            operator_kind="select",
            input_candidate_ids=[reviser.candidate_id],
            output_candidate_ids=[closer.candidate_id],
            metadata={"recipe": "dci_typed_deliberation_packet", "typed_act": "close_deliberation"},
        ),
    ]
    frontiers = [
        SearchFrontier(f"{search_id}.frontier.0", search_id, 0, [proposer.candidate_id], "completed"),
        SearchFrontier(f"{search_id}.frontier.1", search_id, 1, [challenger.candidate_id], "completed"),
        SearchFrontier(f"{search_id}.frontier.2", search_id, 2, [reviser.candidate_id], "completed"),
        SearchFrontier(f"{search_id}.frontier.3", search_id, 3, [closer.candidate_id], "completed"),
    ]
    run = SearchRun(
        search_id=search_id,
        recipe_kind="dci_typed_deliberation_packet",
        candidates=[proposer, challenger, reviser, closer],
        frontiers=frontiers,
        events=events,
        messages=[],
        selected_candidate_id=closer.candidate_id,
        branch_states=[branch],
        workspace_snapshots=[root_snapshot, revision_snapshot],
        metadata={
            "phase": "dag_v4_phase4",
            "paper_key": "dci_typed_epistemic_acts",
            "packet_id": "dci_typed_deliberation_packet",
            "topology_class": "D",
            "typed_acts": ["propose_claim", "raise_objection", "revise_claim", "close_deliberation"],
        },
    )
    recipe_manifest = PaperRecipeManifest(
        manifest_id="dag_v4.dci.profile.v1",
        paper_key="dci_typed_epistemic_acts",
        paper_title="DCI / typed epistemic acts",
        family_kind="typed_collective_deliberation",
        runtime_recipe_kind=run.recipe_kind,
        fidelity_target="medium_structural_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="dci.typed_deliberation.slice.v1",
        control_profile={
            "task_class": "bounded_typed_deliberation",
            "typed_acts": ["propose_claim", "raise_objection", "revise_claim", "close_deliberation"],
            "shared_workspace": True,
            "reopen_condition": "objection_unresolved",
        },
        baseline_ids=[
            "single_agent_cot",
            "untagged_dialogue",
            "no_shared_workspace",
            "proposal_plus_review_only",
            "no_reopen_condition",
        ],
        metadata={"phase": "dag_v4_phase4", "packet_id": "dci_typed_deliberation_packet", "paper_mode": False},
    )
    scorecard = build_default_fidelity_scorecard(
        scorecard_id="dag_v4.dci.scorecard.v1",
        paper_key=recipe_manifest.paper_key,
        fidelity_label="medium_structural_fidelity",
        structural_fidelity="pass",
        evaluator_fidelity="controlled",
        compute_fidelity="bounded",
        training_aware_fidelity="inference_only_labeled",
        notes={
            "claim_limit": "bounded typed deliberation packet only",
            "typed_act_watch": ["typed_collective_act_truth", "shared_workspace_reopen_semantics"],
        },
        metadata={"phase": "dag_v4_phase4"},
    )
    compute_ledger = ComputeBudgetLedger(
        ledger_id="dag_v4.dci.compute.v1",
        paper_key=recipe_manifest.paper_key,
        model_tier="gpt_5_4_mini",
        entries=[
            {"entry_id": "dci.calls", "kind": "llm_calls", "label": "llm_calls", "quantity": 4, "unit": "calls"},
            {"entry_id": "dci.prompt_tokens", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": sum(float(item.usage.get("prompt_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "dci.completion_tokens", "kind": "completion_tokens", "label": "completion_tokens", "quantity": sum(float(item.usage.get("completion_tokens", 0.0)) for item in run.candidates), "unit": "tokens"},
            {"entry_id": "dci.typed_act_count", "kind": "typed_act_count", "label": "typed_act_count", "quantity": 4.0, "unit": "acts"},
            {"entry_id": "dci.workspace_snapshots", "kind": "workspace_snapshots", "label": "workspace_snapshots", "quantity": 2.0, "unit": "snapshots"},
        ],
        normalization_rule="typed_deliberation_packet_matched",
        metadata={"phase": "dag_v4_phase4"},
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="dag_v4.dci.baselines.v1",
        paper_key=recipe_manifest.paper_key,
        normalization_rule="typed_deliberation_packet_matched",
        baseline_ids=list(recipe_manifest.baseline_ids),
        metadata={"phase": "dag_v4_phase4"},
    )
    deviation_ledger = ReplicationDeviationLedger(
        ledger_id="dag_v4.dci.deviations.v1",
        paper_key=recipe_manifest.paper_key,
        deviations=[
            {"deviation_id": "dci.dev.01", "severity": "medium", "summary": "Packet is bounded to four typed acts rather than a large typed collective act system."},
            {"deviation_id": "dci.dev.02", "severity": "low", "summary": "Typed acts remain packet-local labels rather than promoted runtime nouns."},
        ],
        metadata={"phase": "dag_v4_phase4"},
    )
    topology_audit = TopologyAudit(
        audit_id="dag_v4.dci.topology.v1",
        target_family="dci_typed_epistemic_acts",
        topology_class="D",
        parentage_reconstructable=True,
        fan_flow_reconstructable=True,
        feedback_loop_reconstructable=True,
        shadow_state_required=False,
        notes={"shared_workspace": True, "typed_act_count": 4},
        metadata={"phase": "dag_v4_phase4"},
    )
    assessment_lineage = AssessmentLineagePacket(
        packet_id="dag_v4.dci.assessment_lineage.v1",
        target_family="dci_typed_epistemic_acts",
        topology_class="D",
        assessment_kinds=["verify"],
        action_links=[{"assessment_id": "objection.1", "action_id": "revise_claim", "target_candidate_id": reviser.candidate_id}],
        mixed_chain_reconstructable=True,
        notes={"reopen_condition": "objection_unresolved"},
        metadata={"phase": "dag_v4_phase4"},
    )
    replay_export_integrity = ReplayExportIntegrityPacket(
        packet_id="dag_v4.dci.replay_export.v1",
        target_family="dci_typed_epistemic_acts",
        topology_class="D",
        export_modes=["trajectory_export", "offline_dataset_projection", "flattened_transcript_control"],
        preserved_semantics=["typed_act_identity", "workspace_snapshot_lineage", "reopen_condition"],
        lost_semantics=[],
        shadow_assumptions_required=False,
        notes={"shared_workspace_snapshot_ids": [root_snapshot.snapshot_id, revision_snapshot.snapshot_id]},
        metadata={"phase": "dag_v4_phase4"},
    )
    repeated_shape_entry = RepeatedShapeRegisterEntry(
        gap_label="typed_collective_act_truth",
        target_family="dci_typed_epistemic_acts",
        topology_class="D",
        where_it_appears="typed shared-workspace deliberation with explicit objection and revision acts",
        current_workaround="typed act ledger plus workspace snapshots plus replay/export integrity packet",
        why_workaround_is_insufficient="no insufficiency observed yet; helper-vs-runtime boundary remains intact in bounded packet",
        effect_on_fidelity_tier="no_downgrade",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_workspace_manifest_only",
        seen_in_other_targets=["team_of_thoughts"],
        seen_in_consumers=[],
        helper_exhausted=False,
        counts_toward_review=False,
        notes={"review_threshold_missing": "cross-class evidence and helper exhaustion absent"},
        metadata={"phase": "dag_v4_phase4"},
    )
    typed_act_ledger = {
        "ledger_id": "dag_v4.dci.typed_acts.v1",
        "acts": [
            {"candidate_id": proposer.candidate_id, "typed_act": "propose_claim"},
            {"candidate_id": challenger.candidate_id, "typed_act": "raise_objection"},
            {"candidate_id": reviser.candidate_id, "typed_act": "revise_claim"},
            {"candidate_id": closer.candidate_id, "typed_act": "close_deliberation"},
        ],
        "shared_workspace": True,
    }
    return {
        "recipe_manifest": recipe_manifest,
        "scorecard": scorecard,
        "compute_ledger": compute_ledger,
        "baseline_packet": baseline_packet,
        "deviation_ledger": deviation_ledger,
        "run": run,
        "topology_audit": topology_audit,
        "assessment_lineage_packet": assessment_lineage,
        "replay_export_integrity_packet": replay_export_integrity,
        "repeated_shape_entry": repeated_shape_entry,
        "typed_act_ledger": typed_act_ledger,
        "workspace_snapshot_ids": [root_snapshot.snapshot_id, revision_snapshot.snapshot_id],
    }


def build_dag_v4_dci_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_dci_packet()
    return {
        "recipe_manifest": example["recipe_manifest"].to_dict(),
        "scorecard": example["scorecard"].to_dict(),
        "compute_ledger": example["compute_ledger"].to_dict(),
        "baseline_packet": example["baseline_packet"].to_dict(),
        "deviation_ledger": example["deviation_ledger"].to_dict(),
        "run": example["run"].to_dict(),
        "topology_audit": example["topology_audit"].to_dict(),
        "assessment_lineage_packet": example["assessment_lineage_packet"].to_dict(),
        "replay_export_integrity_packet": example["replay_export_integrity_packet"].to_dict(),
        "repeated_shape_entry": example["repeated_shape_entry"].to_dict(),
        "typed_act_ledger": dict(example["typed_act_ledger"]),
        "workspace_snapshot_ids": list(example["workspace_snapshot_ids"]),
    }


def build_dag_v4_optimize_consumer_packet() -> Dict[str, object]:
    """Build the optimize consumer packet over three structurally distinct DAG V4 families."""

    got = build_dag_v4_got_v2_packet()
    bavt = build_dag_v4_bavt_packet()
    team = build_dag_v4_team_of_thoughts_packet()
    source_rows = [
        {
            "target_family": "got_v2",
            "topology_class": "G",
            "selected_candidate_id": got["run"].selected_candidate_id,
            "benchmark_packet": got["recipe_manifest"].benchmark_packet,
        },
        {
            "target_family": "bavt",
            "topology_class": "F",
            "selected_candidate_id": bavt["run"].selected_candidate_id,
            "benchmark_packet": bavt["recipe_manifest"].benchmark_packet,
        },
        {
            "target_family": "team_of_thoughts",
            "topology_class": "H",
            "selected_candidate_id": team["run"].selected_candidate_id,
            "benchmark_packet": team["recipe_manifest"].benchmark_packet,
        },
    ]
    handoff_packets = [
        ConsumerHandoffPacket(
            packet_id="dag_v4.optimize.got_v2.handoff.v1",
            target_family="got_v2",
            consumer_kind="optimize",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ComputeBudgetLedger", "ReplayExportIntegrityPacket"],
            handoff_contract=["selected_candidate_identity", "multi_parent_lineage", "bounded_compute_visibility"],
            shadow_semantics_required=False,
            notes={"measurement_basis": "explicit replay/export packet"},
            metadata={"phase": "dag_v4_phase5"},
        ),
        ConsumerHandoffPacket(
            packet_id="dag_v4.optimize.bavt.handoff.v1",
            target_family="bavt",
            consumer_kind="optimize",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ComputeBudgetLedger", "FrontierPolicyAudit"],
            handoff_contract=["selected_candidate_identity", "budget_trace_visibility", "frontier_policy_visibility"],
            shadow_semantics_required=False,
            notes={"measurement_basis": "frontier policy audit plus compute ledger"},
            metadata={"phase": "dag_v4_phase5"},
        ),
        ConsumerHandoffPacket(
            packet_id="dag_v4.optimize.team_of_thoughts.handoff.v1",
            target_family="team_of_thoughts",
            consumer_kind="optimize",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ComputeBudgetLedger", "AssessmentLineagePacket"],
            handoff_contract=["selected_candidate_identity", "role_stage_manifest_visibility", "final_review_lineage"],
            shadow_semantics_required=False,
            notes={"measurement_basis": "role-stage manifest plus lineage packet"},
            metadata={"phase": "dag_v4_phase5"},
        ),
    ]
    integrity_rows = [
        {
            "target_family": "got_v2",
            "topology_class": "G",
            "measured_shadow_semantics_required": False,
            "evidence_sources": ["ReplayExportIntegrityPacket", "ConsumerHandoffPacket"],
            "lost_semantics_count": len(got["replay_export_integrity_packet"].lost_semantics),
        },
        {
            "target_family": "bavt",
            "topology_class": "F",
            "measured_shadow_semantics_required": False,
            "evidence_sources": ["FrontierPolicyAudit", "ComputeBudgetLedger", "ConsumerHandoffPacket"],
            "lost_semantics_count": 0,
        },
        {
            "target_family": "team_of_thoughts",
            "topology_class": "H",
            "measured_shadow_semantics_required": False,
            "evidence_sources": ["TopologyAudit", "AssessmentLineagePacket", "role_stage_manifest", "ConsumerHandoffPacket"],
            "lost_semantics_count": 0,
        },
    ]
    seam_packet = CompositionSeamPacket(
        packet_id="dag_v4.optimize.consumer.seam.v1",
        source_family="dag_v4_consumer_bundle",
        target_kind="optimize",
        seam_labels=["comparison_packet_handoff", "objective_breakdown_visibility", "budget_alignment"],
        issues=[
            {
                "issue_id": "optimize.seam.01",
                "status": "consumer_local_only",
                "summary": "Optimize wants normalized comparison rows, but source DAG packets already expose enough bounded truth.",
            },
            {
                "issue_id": "optimize.seam.02",
                "status": "helper_level_only",
                "summary": "BAVT handoff is most naturally consumed through frontier-policy audit rather than a new DAG runtime field.",
            },
        ],
        repeated_shape_candidate=False,
        notes={"review_threshold_result": "consumer confirmation present but no cross-class missing DAG truth"},
        metadata={"phase": "dag_v4_phase5"},
    )
    consumer_issue_classification = [
        {"target_family": "got_v2", "locus": "consumer_local_only", "summary": "Optimize can read the packet without new DAG nouns."},
        {"target_family": "bavt", "locus": "helper_level_only", "summary": "Budget/frontier handoff is satisfied by audit artifacts."},
        {"target_family": "team_of_thoughts", "locus": "consumer_local_only", "summary": "Role roster semantics stay manifest-local."},
    ]
    return {
        "consumer_kind": "optimize",
        "source_rows": source_rows,
        "consumer_handoff_packets": handoff_packets,
        "integrity_rows": integrity_rows,
        "composition_seam_packet": seam_packet,
        "consumer_issue_classification": consumer_issue_classification,
        "repeated_shape_update": {
            "review_counting_gap_labels": [],
            "consumer_confirmation_without_cross_class_missing_truth": True,
            "dag_kernel_change_required": False,
        },
    }


def build_dag_v4_optimize_consumer_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_optimize_consumer_packet()
    return {
        "consumer_kind": example["consumer_kind"],
        "source_rows": [dict(item) for item in example["source_rows"]],
        "consumer_handoff_packets": [item.to_dict() for item in example["consumer_handoff_packets"]],
        "integrity_rows": [dict(item) for item in example["integrity_rows"]],
        "composition_seam_packet": example["composition_seam_packet"].to_dict(),
        "consumer_issue_classification": [dict(item) for item in example["consumer_issue_classification"]],
        "repeated_shape_update": dict(example["repeated_shape_update"]),
    }


def build_dag_v4_rl_consumer_packet() -> Dict[str, object]:
    """Build the RL consumer packet over three structurally distinct DAG V4 families."""

    apmcts = build_dag_v4_adaptive_parallel_mcts_lite_packet()
    codetree = build_dag_v4_codetree_v2_packet()
    dci = build_dag_v4_dci_packet()
    source_rows = [
        {
            "target_family": "adaptive_parallel_mcts_lite",
            "topology_class": "F",
            "selected_candidate_id": apmcts["run"].selected_candidate_id,
            "benchmark_packet": apmcts["recipe_manifest"].benchmark_packet,
        },
        {
            "target_family": "codetree_v2",
            "topology_class": "W",
            "selected_candidate_id": codetree["run"].selected_candidate_id,
            "benchmark_packet": codetree["recipe_manifest"].benchmark_packet,
        },
        {
            "target_family": "dci_typed_epistemic_acts",
            "topology_class": "D",
            "selected_candidate_id": dci["run"].selected_candidate_id,
            "benchmark_packet": dci["recipe_manifest"].benchmark_packet,
        },
    ]
    handoff_packets = [
        ConsumerHandoffPacket(
            packet_id="dag_v4.rl.apmcts.handoff.v1",
            target_family="adaptive_parallel_mcts_lite",
            consumer_kind="rl",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ReplayExportIntegrityPacket", "FrontierPolicyAudit"],
            handoff_contract=["selected_candidate_identity", "parallel_branch_identity", "backprop_visibility"],
            shadow_semantics_required=False,
            notes={"measurement_basis": "replay/export packet plus frontier policy audit"},
            metadata={"phase": "dag_v4_phase5"},
        ),
        ConsumerHandoffPacket(
            packet_id="dag_v4.rl.codetree_v2.handoff.v1",
            target_family="codetree_v2",
            consumer_kind="rl",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ReplayExportIntegrityPacket", "AssessmentLineagePacket"],
            handoff_contract=["selected_candidate_identity", "workspace_ref_lineage", "assessment_to_action_chain"],
            shadow_semantics_required=False,
            notes={"measurement_basis": "replay/export packet plus assessment lineage"},
            metadata={"phase": "dag_v4_phase5"},
        ),
        ConsumerHandoffPacket(
            packet_id="dag_v4.rl.dci.handoff.v1",
            target_family="dci_typed_epistemic_acts",
            consumer_kind="rl",
            artifact_kinds=["SearchRun", "FidelityScorecard", "ReplayExportIntegrityPacket", "AssessmentLineagePacket"],
            handoff_contract=["selected_candidate_identity", "typed_act_identity", "workspace_snapshot_lineage"],
            shadow_semantics_required=False,
            notes={"measurement_basis": "replay/export packet plus typed-act ledger"},
            metadata={"phase": "dag_v4_phase5"},
        ),
    ]
    integrity_rows = [
        {
            "target_family": "adaptive_parallel_mcts_lite",
            "topology_class": "F",
            "measured_shadow_semantics_required": False,
            "evidence_sources": ["ReplayExportIntegrityPacket", "ConsumerHandoffPacket"],
            "lost_semantics_count": len(apmcts["replay_export_integrity_packet"].lost_semantics),
        },
        {
            "target_family": "codetree_v2",
            "topology_class": "W",
            "measured_shadow_semantics_required": False,
            "evidence_sources": ["ReplayExportIntegrityPacket", "AssessmentLineagePacket", "ConsumerHandoffPacket"],
            "lost_semantics_count": len(codetree["replay_export_integrity_packet"].lost_semantics),
        },
        {
            "target_family": "dci_typed_epistemic_acts",
            "topology_class": "D",
            "measured_shadow_semantics_required": False,
            "evidence_sources": ["ReplayExportIntegrityPacket", "typed_act_ledger", "ConsumerHandoffPacket"],
            "lost_semantics_count": len(dci["replay_export_integrity_packet"].lost_semantics),
        },
    ]
    seam_packet = CompositionSeamPacket(
        packet_id="dag_v4.rl.consumer.seam.v1",
        source_family="dag_v4_consumer_bundle",
        target_kind="rl",
        seam_labels=["trajectory_projection_handoff", "annotation_pack_visibility", "replay_projection_integrity"],
        issues=[
            {
                "issue_id": "rl.seam.01",
                "status": "consumer_local_only",
                "summary": "RL wants packet slicing choices, but the DAG packet truth is already sufficient for bounded export.",
            },
            {
                "issue_id": "rl.seam.02",
                "status": "helper_level_only",
                "summary": "Typed-act and workspace lineage stay in helper-layer artifacts without forcing DAG promotion.",
            },
        ],
        repeated_shape_candidate=False,
        notes={"review_threshold_result": "consumer confirmation present but no cross-class missing DAG truth"},
        metadata={"phase": "dag_v4_phase5"},
    )
    consumer_issue_classification = [
        {"target_family": "adaptive_parallel_mcts_lite", "locus": "consumer_local_only", "summary": "RL can consume bounded parallel frontier traces without new DAG truth."},
        {"target_family": "codetree_v2", "locus": "consumer_local_only", "summary": "Workspace and assessment chains are exportable as-is."},
        {"target_family": "dci_typed_epistemic_acts", "locus": "helper_level_only", "summary": "Typed acts remain packet-local and replay-safe."},
    ]
    return {
        "consumer_kind": "rl",
        "source_rows": source_rows,
        "consumer_handoff_packets": handoff_packets,
        "integrity_rows": integrity_rows,
        "composition_seam_packet": seam_packet,
        "consumer_issue_classification": consumer_issue_classification,
        "repeated_shape_update": {
            "review_counting_gap_labels": [],
            "consumer_confirmation_without_cross_class_missing_truth": True,
            "dag_kernel_change_required": False,
        },
    }


def build_dag_v4_rl_consumer_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_rl_consumer_packet()
    return {
        "consumer_kind": example["consumer_kind"],
        "source_rows": [dict(item) for item in example["source_rows"]],
        "consumer_handoff_packets": [item.to_dict() for item in example["consumer_handoff_packets"]],
        "integrity_rows": [dict(item) for item in example["integrity_rows"]],
        "composition_seam_packet": example["composition_seam_packet"].to_dict(),
        "consumer_issue_classification": [dict(item) for item in example["consumer_issue_classification"]],
        "repeated_shape_update": dict(example["repeated_shape_update"]),
    }


def build_dag_v4_cross_system_seam_packet() -> Dict[str, object]:
    """Build the cross-system seam packet over the optimize and RL consumer bundles."""

    optimize_packet = build_dag_v4_optimize_consumer_packet()
    rl_packet = build_dag_v4_rl_consumer_packet()
    seam_packet = CompositionSeamPacket(
        packet_id="dag_v4.cross_system.seam.v1",
        source_family="dag_v4_phase5_consumer_pressure",
        target_kind="cross_system",
        seam_labels=[
            "consumer_handoff_contracts",
            "replay_export_integrity_visibility",
            "frontier_and_workspace_audit_visibility",
        ],
        issues=[
            {
                "issue_id": "cross_system.seam.01",
                "status": "helper_level_only",
                "summary": "Optimize and RL both prefer pre-normalized comparison views, but the underlying DAG packet truth remains sufficient.",
            },
            {
                "issue_id": "cross_system.seam.02",
                "status": "consumer_local_only",
                "summary": "Consumer packet slicing choices differ, but do not imply missing DAG runtime truth.",
            },
        ],
        repeated_shape_candidate=False,
        notes={
            "optimize_issue_count": len(optimize_packet["consumer_issue_classification"]),
            "rl_issue_count": len(rl_packet["consumer_issue_classification"]),
            "cross_class_review_counting_gap_labels": [],
        },
        metadata={"phase": "dag_v4_phase6"},
    )
    seam_rows = [
        {
            "consumer_kind": "optimize",
            "topology_classes": ["G", "F", "H"],
            "all_shadow_semantics_required": False,
            "dominant_locus": "consumer_local_or_helper_only",
        },
        {
            "consumer_kind": "rl",
            "topology_classes": ["F", "W", "D"],
            "all_shadow_semantics_required": False,
            "dominant_locus": "consumer_local_or_helper_only",
        },
    ]
    return {
        "optimize_packet": optimize_packet,
        "rl_packet": rl_packet,
        "cross_system_seam_packet": seam_packet,
        "seam_rows": seam_rows,
        "repeated_shape_update": {
            "review_counting_gap_labels": [],
            "cross_class_consumer_confirmation_without_runtime_loss": True,
            "dag_kernel_change_required": False,
        },
    }


def build_dag_v4_cross_system_seam_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_cross_system_seam_packet()
    return {
        "optimize_packet": build_dag_v4_optimize_consumer_packet_payload(),
        "rl_packet": build_dag_v4_rl_consumer_packet_payload(),
        "cross_system_seam_packet": example["cross_system_seam_packet"].to_dict(),
        "seam_rows": [dict(item) for item in example["seam_rows"]],
        "repeated_shape_update": dict(example["repeated_shape_update"]),
    }


def build_dag_v4_helper_exhaustion_counterfactual_packet() -> Dict[str, object]:
    """Build the helper-exhaustion packet over the leading DAG V4 seam candidates."""

    bavt = build_dag_v4_bavt_packet()
    dci = build_dag_v4_dci_packet()
    optimize_packet = build_dag_v4_optimize_consumer_packet()
    rl_packet = build_dag_v4_rl_consumer_packet()
    helper_fix_attempts = [
        {
            "gap_label": "budget_conditioned_frontier_truth",
            "target_family": "bavt",
            "topology_class": "F",
            "helper_fix": "frontier_policy_audit plus budget-normalized control row and explicit handoff contract",
            "consumer_rerun": "optimize",
            "result": "still helper-local, no runtime loss",
        },
        {
            "gap_label": "typed_collective_act_truth",
            "target_family": "dci_typed_epistemic_acts",
            "topology_class": "D",
            "helper_fix": "typed_act_ledger plus workspace snapshots plus replay/export integrity packet",
            "consumer_rerun": "rl",
            "result": "still helper-local, no runtime loss",
        },
    ]
    rerun_rows = [
        {
            "target_family": "bavt",
            "consumer_kind": "optimize",
            "helper_fix_applied": True,
            "shadow_semantics_required_after_fix": False,
            "replay_loss_after_fix": False,
            "classification_after_fix": "helper_level_only",
        },
        {
            "target_family": "dci_typed_epistemic_acts",
            "consumer_kind": "rl",
            "helper_fix_applied": True,
            "shadow_semantics_required_after_fix": False,
            "replay_loss_after_fix": False,
            "classification_after_fix": "helper_level_only",
        },
    ]
    seam_packet = CompositionSeamPacket(
        packet_id="dag_v4.helper_exhaustion.seam.v1",
        source_family="dag_v4_phase6_helper_exhaustion",
        target_kind="helper_exhaustion",
        seam_labels=["helper_counterfactual_rerun", "consumer_rerun_after_helper_fix"],
        issues=[
            {
                "issue_id": "helper_exhaustion.01",
                "status": "resolved_by_helper_only",
                "summary": "Budget-conditioned frontier seam remains interpretable after helper-only audit and handoff normalization.",
            },
            {
                "issue_id": "helper_exhaustion.02",
                "status": "resolved_by_helper_only",
                "summary": "Typed shared-workspace seam remains interpretable after helper-only ledger and replay/export refinement.",
            },
        ],
        repeated_shape_candidate=False,
        notes={"helper_exhaustion_attempted": True, "consumer_rerun_count": 2},
        metadata={"phase": "dag_v4_phase6"},
    )
    return {
        "helper_fix_attempts": helper_fix_attempts,
        "rerun_rows": rerun_rows,
        "source_packets": {
            "bavt": bavt,
            "dci": dci,
            "optimize_consumer": optimize_packet,
            "rl_consumer": rl_packet,
        },
        "composition_seam_packet": seam_packet,
        "repeated_shape_update": {
            "helper_exhaustion_attempted": True,
            "review_counting_gap_labels": [],
            "dag_kernel_change_required": False,
        },
    }


def build_dag_v4_helper_exhaustion_counterfactual_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_helper_exhaustion_counterfactual_packet()
    return {
        "helper_fix_attempts": [dict(item) for item in example["helper_fix_attempts"]],
        "rerun_rows": [dict(item) for item in example["rerun_rows"]],
        "source_packets": {
            "bavt": build_dag_v4_bavt_packet_payload(),
            "dci": build_dag_v4_dci_packet_payload(),
            "optimize_consumer": build_dag_v4_optimize_consumer_packet_payload(),
            "rl_consumer": build_dag_v4_rl_consumer_packet_payload(),
        },
        "composition_seam_packet": example["composition_seam_packet"].to_dict(),
        "repeated_shape_update": dict(example["repeated_shape_update"]),
    }


def build_dag_v4_final_adjudication_packet() -> Dict[str, object]:
    """Build the final DAG V4 adjudication packet."""

    optimize_consumer = build_dag_v4_optimize_consumer_packet()
    rl_consumer = build_dag_v4_rl_consumer_packet()
    seam_packet = build_dag_v4_cross_system_seam_packet()
    helper_exhaustion = build_dag_v4_helper_exhaustion_counterfactual_packet()
    repeated_shape_register = [
        build_dag_v4_got_v2_packet()["repeated_shape_entry"],
        build_dag_v4_tot_v2_packet()["repeated_shape_entry"],
        build_dag_v4_moa_v2_packet()["repeated_shape_entry"],
        build_dag_v4_codetree_v2_packet()["repeated_shape_entry"],
        build_dag_v4_bavt_packet()["repeated_shape_entry"],
        build_dag_v4_adaptive_parallel_mcts_lite_packet()["repeated_shape_entry"],
        build_dag_v4_team_of_thoughts_packet()["repeated_shape_entry"],
        build_dag_v4_dci_packet()["repeated_shape_entry"],
    ]
    locus_summary = {
        "dag_kernel": {
            "status": "frozen",
            "justification": "No review-counting repeated shape survived cross-class pressure, consumer pressure, and helper exhaustion.",
        },
        "helper_fidelity": {
            "status": "active",
            "next_pressure": "continue using packet-local manifests, audits, and replay/export integrity artifacts rather than promoting new DAG nouns.",
        },
        "consumer_layer": {
            "status": "active",
            "next_pressure": "consumer-specific packet shaping remains a downstream concern, not DAG-local runtime truth.",
            "optimize_issue_count": len(optimize_consumer["consumer_issue_classification"]),
            "rl_issue_count": len(rl_consumer["consumer_issue_classification"]),
        },
        "benchmark_evaluator": {
            "status": "active",
            "next_pressure": "future work should keep using evaluator controls before any architecture claims are reopened.",
        },
        "harness_environment": {
            "status": "active",
            "next_pressure": "parallelism and shared-workspace serving concerns remain harness-local unless they start corrupting replay/export integrity.",
        },
    }
    repeated_shape_summary = {
        "total_entries": len(repeated_shape_register),
        "review_counting_gap_labels": [],
        "topology_classes_covered": ["G", "F", "H", "W", "D"],
        "consumer_confirmation_present": True,
        "helper_exhaustion_attempted": True,
        "replay_export_corruption_detected": False,
    }
    freeze_decision = {
        "current_decision": "keep_dag_frozen",
        "open_dag_v5_now": False,
        "reason": "The full V4 proof-by-pressure program did not produce a review-counting repeated shape, and the leading seam candidates resolved to helper-level or consumer-local interpretations after helper exhaustion.",
        "reopen_criteria": [
            "the same missing DAG truth appears across at least two topology classes and one downstream consumer",
            "helper exhaustion has been attempted and failed",
            "replay, export, or provenance integrity is materially corrupted",
        ],
    }
    return {
        "packet_id": "dag_v4.final_adjudication.v1",
        "source_packets": {
            "optimize_consumer_packet": optimize_consumer,
            "rl_consumer_packet": rl_consumer,
            "cross_system_seam_packet": seam_packet,
            "helper_exhaustion_counterfactual_packet": helper_exhaustion,
        },
        "reviewed_topology_classes": ["G", "F", "H", "W", "D"],
        "reviewed_target_count": 8,
        "repeated_shape_summary": repeated_shape_summary,
        "locus_summary": locus_summary,
        "freeze_decision": freeze_decision,
        "metadata": {"phase": "dag_v4_phase7", "kernel_change_required": False},
    }


def build_dag_v4_final_adjudication_packet_payload() -> Dict[str, object]:
    example = build_dag_v4_final_adjudication_packet()
    return {
        "packet_id": example["packet_id"],
        "source_packets": {
            "optimize_consumer_packet": build_dag_v4_optimize_consumer_packet_payload(),
            "rl_consumer_packet": build_dag_v4_rl_consumer_packet_payload(),
            "cross_system_seam_packet": build_dag_v4_cross_system_seam_packet_payload(),
            "helper_exhaustion_counterfactual_packet": build_dag_v4_helper_exhaustion_counterfactual_packet_payload(),
        },
        "reviewed_topology_classes": list(example["reviewed_topology_classes"]),
        "reviewed_target_count": example["reviewed_target_count"],
        "repeated_shape_summary": dict(example["repeated_shape_summary"]),
        "locus_summary": dict(example["locus_summary"]),
        "freeze_decision": dict(example["freeze_decision"]),
        "metadata": dict(example["metadata"]),
    }
