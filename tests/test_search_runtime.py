from __future__ import annotations

from agentic_coder_prototype.optimize import (
    BenchmarkRunManifest,
    CandidateComparisonResult,
    MutationProposal,
    ObjectiveBreakdownResult,
    PromotionEvidenceSummary,
    ReflectionDecision,
    TransferCohortManifest,
)
from agentic_coder_prototype.search import (
    SearchOfflineDataset,
    SearchAssessment,
    SearchBranchState,
    SearchCandidate,
    SearchCarryState,
    SearchEvent,
    SearchFrontier,
    SearchMessage,
    SearchRewardSignal,
    build_default_search_assessment_registry,
    build_branch_execute_verify_reference_recipe,
    build_branch_execute_verify_reference_recipe_payload,
    build_branch_execute_verify_pressure_cell,
    build_dag_v2_e4_widening_packet,
    build_dag_v2_e4_widening_packet_payload,
    build_dag_v2_phase0_pressure_packet,
    build_dag_v2_phase0_pressure_packet_payload,
    build_dag_v2_stop_go_synthesis,
    build_dag_v2_stop_go_synthesis_payload,
    build_default_search_compaction_registry,
    build_exact_verifier_assessment_example,
    build_exact_verifier_assessment_example_payload,
    build_frontier_verify_gate_example,
    build_frontier_verify_gate_example_payload,
    build_judge_pairwise_assessment_example,
    build_judge_pairwise_assessment_example_payload,
    build_judge_reducer_pressure_cell,
    build_judge_reduce_gate_example,
    build_judge_reduce_gate_example_payload,
    build_pacore_search_runtime_example,
    build_pacore_search_runtime_example_payload,
    build_post_v2_study_01_verifier_patch_branch,
    build_post_v2_study_01_verifier_patch_branch_payload,
    build_post_v2_study_02_judge_reducer_rounds,
    build_post_v2_study_02_judge_reducer_rounds_payload,
    build_post_v2_study_03_branch_execute_verify_deeper,
    build_post_v2_study_03_branch_execute_verify_deeper_payload,
    build_post_v2_study_04_optimize_adapter_probe,
    build_post_v2_study_04_optimize_adapter_probe_payload,
    build_post_v2_study_05_rl_facing_probe,
    build_post_v2_study_05_rl_facing_probe_payload,
    build_post_v2_study_06_darwin_boundary_probe,
    build_post_v2_study_06_darwin_boundary_probe_payload,
    build_post_v2_study_07_message_passing_adjudication,
    build_post_v2_study_07_message_passing_adjudication_payload,
    build_post_v2_study_08_verifier_judge_handoff,
    build_post_v2_study_08_verifier_judge_handoff_payload,
    build_post_v2_study_09_optimize_objective_breakdown_probe,
    build_post_v2_study_09_optimize_objective_breakdown_probe_payload,
    build_post_v2_study_10_optimize_benchmark_promotion_probe,
    build_post_v2_study_10_optimize_benchmark_promotion_probe_payload,
    build_post_v2_study_11_branch_carry_hybrid,
    build_post_v2_study_11_branch_carry_hybrid_payload,
    build_post_v2_study_12_optimize_comparison_probe,
    build_post_v2_study_12_optimize_comparison_probe_payload,
    build_post_v2_study_13_multi_candidate_tournament,
    build_post_v2_study_13_multi_candidate_tournament_payload,
    build_post_v2_study_14_optimize_transfer_cohort_probe,
    build_post_v2_study_14_optimize_transfer_cohort_probe_payload,
    build_post_v2_study_15_reducer_after_tournament,
    build_post_v2_study_15_reducer_after_tournament_payload,
    build_post_v2_study_16_optimize_reflection_probe,
    build_post_v2_study_16_optimize_reflection_probe_payload,
    build_post_v2_study_17_repair_loop_after_reducer,
    build_post_v2_study_17_repair_loop_after_reducer_payload,
    build_post_v2_study_18_optimize_mutation_proposal_probe,
    build_post_v2_study_18_optimize_mutation_proposal_probe_payload,
    SearchRun,
    SearchTrajectoryExport,
    SearchWorkspaceSnapshot,
    build_rsa_search_runtime_example,
    build_rsa_search_runtime_example_payload,
    build_search_trajectory_export_example,
    build_search_trajectory_export_example_payload,
    build_stateful_branch_search_example,
    build_stateful_branch_search_example_payload,
    build_typed_compaction_registry_example,
    build_typed_compaction_registry_example_payload,
    build_verifier_guided_pressure_cell,
    export_search_trajectory,
)


def test_search_records_round_trip() -> None:
    candidate = SearchCandidate(
        candidate_id="search.test.candidate.1",
        search_id="search.test",
        frontier_id="search.test.frontier.0",
        parent_ids=["search.test.parent.1", "search.test.parent.2"],
        round_index=1,
        depth=1,
        payload_ref="artifacts/search/test/candidate_1.json",
        score_vector={"correctness_score": 0.7},
        usage={"prompt_tokens": 10},
        status="active",
    )
    message = SearchMessage(
        message_id="search.test.message.1",
        schema_kind="summary.v1",
        source_candidate_ids=["search.test.candidate.1"],
        summary_payload={"summary": "candidate summary"},
        confidence=0.8,
    )
    frontier = SearchFrontier(
        frontier_id="search.test.frontier.1",
        search_id="search.test",
        round_index=1,
        candidate_ids=[candidate.candidate_id],
        status="active",
    )
    event = SearchEvent(
        event_id="search.test.event.1",
        search_id="search.test",
        frontier_id=frontier.frontier_id,
        round_index=1,
        operator_kind="aggregate",
        input_candidate_ids=["search.test.parent.1", "search.test.parent.2"],
        output_candidate_ids=[candidate.candidate_id],
        message_ids=[message.message_id],
    )
    run = SearchRun(
        search_id="search.test",
        recipe_kind="rsa_population_recombination",
        candidates=[candidate],
        frontiers=[frontier],
        events=[event],
        messages=[message],
        selected_candidate_id=candidate.candidate_id,
    )

    assert SearchCandidate.from_dict(candidate.to_dict()) == candidate
    assert SearchMessage.from_dict(message.to_dict()) == message
    assert SearchFrontier.from_dict(frontier.to_dict()) == frontier
    assert SearchEvent.from_dict(event.to_dict()) == event
    assert SearchRun.from_dict(run.to_dict()) == run

    carry_state = SearchCarryState(
        state_id="search.test.carry.1",
        search_id="search.test",
        message_ids=[message.message_id],
        artifact_refs=[candidate.payload_ref],
        bounded_by="single_summary_message",
        token_budget=128,
    )
    assert SearchCarryState.from_dict(carry_state.to_dict()) == carry_state

    snapshot = SearchWorkspaceSnapshot(
        snapshot_id="search.test.snapshot.1",
        search_id="search.test",
        branch_id="search.test.branch.1",
        artifact_ref="artifacts/search/test/snapshot_1.json",
        derived_from_candidate_id=candidate.candidate_id,
    )
    branch = SearchBranchState(
        branch_id="search.test.branch.1",
        search_id="search.test",
        candidate_id=candidate.candidate_id,
        snapshot_ids=[snapshot.snapshot_id],
        head_snapshot_id=snapshot.snapshot_id,
        status="active",
    )
    assert SearchWorkspaceSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert SearchBranchState.from_dict(branch.to_dict()) == branch


def test_rsa_search_runtime_example_runs_end_to_end() -> None:
    example = build_rsa_search_runtime_example()
    run = example["run"]

    assert run.recipe_kind == "rsa_population_recombination"
    assert run.selected_candidate_id is not None
    assert len(run.frontiers) == 3
    assert len(run.events) == 3
    assert run.metrics is not None
    assert run.metrics.aggregability_gap > 0.0
    assert run.metrics.mixing_rate > 0.0


def test_rsa_search_runtime_payload_round_trips() -> None:
    payload = build_rsa_search_runtime_example_payload()
    run = SearchRun.from_dict(payload["run"])

    assert payload["config"]["search_id"] == "search.rsa_mvp.v1"
    assert payload["config"]["random_seed"] == 7
    assert run.recipe_kind == "rsa_population_recombination"
    assert run.metrics is not None
    assert run.metrics.metadata["final_average_score"] >= run.metrics.metadata["initial_average_score"]
    assert payload["run"]["selected_candidate_id"] == run.selected_candidate_id


def test_typed_compaction_registry_example_is_bounded_and_inspectable() -> None:
    example = build_typed_compaction_registry_example()
    run = example["run"]
    carry_state = example["carry_state"]
    message = example["message"]

    assert example["registry_backend_kinds"] == ["bounded_candidate_rollup.v1"]
    assert len(run.carry_states) == 1
    assert run.carry_states[0] == carry_state
    assert carry_state.bounded_by == "single_summary_message"
    assert carry_state.token_budget == 192
    assert len(carry_state.message_ids) == 1
    assert len(carry_state.artifact_refs) <= 3
    assert message.schema_kind == "candidate_rollup.v1"
    assert "full_reasoning_trace" in message.dropped_fields
    assert any(item.operator_kind == "compact" for item in run.events)


def test_typed_compaction_registry_payload_round_trips() -> None:
    payload = build_typed_compaction_registry_example_payload()
    run = SearchRun.from_dict(payload["run"])
    carry_state = SearchCarryState.from_dict(payload["carry_state"])

    assert payload["registry_backend_kinds"] == ["bounded_candidate_rollup.v1"]
    assert len(run.carry_states) == 1
    assert run.carry_states[0] == carry_state
    assert carry_state.metadata["candidate_count"] > 0
    assert payload["message"]["schema_kind"] == "candidate_rollup.v1"


def test_compaction_registry_rejects_unknown_backend() -> None:
    registry = build_default_search_compaction_registry()
    example = build_rsa_search_runtime_example()
    final_frontier = example["run"].frontiers[-1]
    final_candidates = [
        item for item in example["run"].candidates if item.frontier_id == final_frontier.frontier_id
    ]

    try:
        registry.compact(
            backend_kind="missing_backend",
            search_id="search.test",
            carry_state_id="search.test.carry.missing",
            message_id="search.test.message.missing",
            candidates=final_candidates,
        )
    except ValueError as exc:
        assert "unknown compaction backend" in str(exc)
    else:
        raise AssertionError("expected unknown compaction backend to raise")


def test_pacore_search_runtime_example_is_bounded_and_replayable() -> None:
    example = build_pacore_search_runtime_example()
    run = example["run"]

    assert example["registry_backend_kinds"] == ["bounded_candidate_rollup.v1"]
    assert run.recipe_kind == "pacore_message_passing"
    assert len(run.carry_states) == 2
    assert all(item.bounded_by == "single_summary_message" for item in run.carry_states)
    assert sum(1 for item in run.events if item.operator_kind == "compact") == 2
    assert run.selected_candidate_id == run.candidates[-1].candidate_id
    assert run.candidates[-1].message_ref == run.carry_states[-1].message_ids[0]
    assert run.metrics is not None
    assert run.metrics.mixing_rate > 0.0


def test_pacore_search_runtime_payload_round_trips() -> None:
    payload = build_pacore_search_runtime_example_payload()
    run = SearchRun.from_dict(payload["run"])

    assert payload["config"]["search_id"] == "search.pacore_mvp.v1"
    assert payload["config"]["compaction_backend_kind"] == "bounded_candidate_rollup.v1"
    assert payload["registry_backend_kinds"] == ["bounded_candidate_rollup.v1"]
    assert len(run.carry_states) == 2
    assert run.recipe_kind == "pacore_message_passing"
    assert run.carry_states[-1].metadata["candidate_count"] > 0


def test_stateful_branch_search_example_tracks_merge_and_discard() -> None:
    example = build_stateful_branch_search_example()
    run = example["run"]

    assert run.recipe_kind == "stateful_branch_local_search"
    assert len(run.branch_states) == 2
    assert len(run.workspace_snapshots) == 2
    assert {item.status for item in run.branch_states} == {"merged", "discarded"}
    assert any(item.operator_kind == "merge" for item in run.events)
    assert any(item.operator_kind == "discard" for item in run.events)
    assert all(item.head_snapshot_id in item.snapshot_ids for item in run.branch_states)


def test_stateful_branch_search_payload_round_trips() -> None:
    payload = build_stateful_branch_search_example_payload()
    run = SearchRun.from_dict(payload["run"])
    merged_branch = SearchBranchState.from_dict(payload["merged_branch"])
    merged_snapshot = SearchWorkspaceSnapshot.from_dict(payload["merged_snapshot"])

    assert run.recipe_kind == "stateful_branch_local_search"
    assert len(run.branch_states) == 2
    assert len(run.workspace_snapshots) == 2
    assert merged_branch.status == "merged"
    assert merged_snapshot.snapshot_id == merged_branch.head_snapshot_id


def test_search_trajectory_export_example_preserves_operator_conditioning() -> None:
    example = build_search_trajectory_export_example()
    trajectory = example["trajectory"]
    dataset = example["dataset"]

    assert trajectory.metadata["operator_conditioned"] is True
    assert len(trajectory.steps) == len(example["run"].events)
    assert any(item.scope == "local" for item in trajectory.reward_signals)
    assert any(item.scope == "global" for item in trajectory.reward_signals)
    assert dataset.metadata["trajectory_count"] == 1
    assert dataset.trajectories[0] == trajectory


def test_search_trajectory_export_payload_round_trips() -> None:
    payload = build_search_trajectory_export_example_payload()
    trajectory = SearchTrajectoryExport.from_dict(payload["trajectory"])
    dataset = SearchOfflineDataset.from_dict(payload["dataset"])
    first_signal = SearchRewardSignal.from_dict(payload["trajectory"]["reward_signals"][0])

    assert trajectory.metadata["operator_conditioned"] is True
    assert len(dataset.trajectories) == 1
    assert dataset.trajectories[0] == trajectory
    assert first_signal.scope in {"local", "global"}


def test_dag_v2_phase0_pressure_cells_expose_same_missing_shape() -> None:
    verifier = build_verifier_guided_pressure_cell()
    judge = build_judge_reducer_pressure_cell()
    branch = build_branch_execute_verify_pressure_cell()

    assert verifier["summary"]["awkwardness_kind"] == "assessment_evaluator_truth"
    assert judge["summary"]["awkwardness_kind"] == "assessment_evaluator_truth"
    assert branch["summary"]["awkwardness_kind"] == "assessment_evaluator_truth"
    assert any(item.operator_kind == "verify" for item in verifier["run"].events)
    assert any(item.operator_kind == "verify" for item in judge["run"].events)
    assert any(item.operator_kind == "verify" for item in branch["run"].events)


def test_dag_v2_phase0_pressure_packet_goes_green() -> None:
    packet = build_dag_v2_phase0_pressure_packet()

    assert packet["go_decision"] is True
    assert packet["repeated_shape_kind"] == "assessment_evaluator_truth"
    assert packet["conclusion"]["new_message_primitive_needed"] is False
    assert packet["conclusion"]["new_state_primitive_needed"] is False
    assert packet["conclusion"]["async_forced"] is False
    assert packet["conclusion"]["study_cell_count"] == 3


def test_dag_v2_phase0_pressure_packet_payload_round_trips() -> None:
    payload = build_dag_v2_phase0_pressure_packet_payload()
    runs = [SearchRun.from_dict(item["run"]) for item in payload["cells"]]

    assert payload["go_decision"] is True
    assert payload["repeated_shape_kind"] == "assessment_evaluator_truth"
    assert len(runs) == 3
    assert all(any(event.operator_kind == "verify" for event in run.events) for run in runs)


def test_search_assessment_registry_examples_round_trip() -> None:
    registry = build_default_search_assessment_registry()
    verifier = build_exact_verifier_assessment_example()
    judge = build_judge_pairwise_assessment_example()

    assert registry.list_backend_kinds() == ["exact_tests.v1", "judge_pairwise.v1"]
    assert verifier["assessment"].assessment_kind == "verify"
    assert judge["assessment"].assessment_kind == "judge"
    assert SearchAssessment.from_dict(verifier["assessment"].to_dict()) == verifier["assessment"]
    assert SearchAssessment.from_dict(judge["assessment"].to_dict()) == judge["assessment"]
    assert any(event.assessment_ids for event in verifier["run"].events if event.operator_kind == "verify")
    assert any(event.assessment_ids for event in judge["run"].events if event.operator_kind == "verify")


def test_search_assessment_example_payloads_round_trip() -> None:
    verifier_payload = build_exact_verifier_assessment_example_payload()
    judge_payload = build_judge_pairwise_assessment_example_payload()
    verifier_run = SearchRun.from_dict(verifier_payload["run"])
    judge_run = SearchRun.from_dict(judge_payload["run"])
    verifier_assessment = SearchAssessment.from_dict(verifier_payload["assessment"])
    judge_assessment = SearchAssessment.from_dict(judge_payload["assessment"])

    assert verifier_payload["registry_backend_kinds"] == ["exact_tests.v1", "judge_pairwise.v1"]
    assert judge_payload["registry_backend_kinds"] == ["exact_tests.v1", "judge_pairwise.v1"]
    assert len(verifier_run.assessments) == 1
    assert len(judge_run.assessments) == 1
    assert verifier_run.assessments[0] == verifier_assessment
    assert judge_run.assessments[0] == judge_assessment


def test_frontier_verify_gate_example_is_barriered_and_inspectable() -> None:
    example = build_frontier_verify_gate_example()
    run = example["run"]

    assert run.recipe_kind == "frontier_verify"
    assert len(run.assessments) == 1
    assert run.selected_candidate_id is not None
    assert run.metadata["gate_mode"] == "require_before_select"
    assert any(event.assessment_ids for event in run.events if event.operator_kind == "verify")
    assert any(event.operator_kind == "select" for event in run.events)


def test_frontier_verify_gate_payload_round_trips() -> None:
    payload = build_frontier_verify_gate_example_payload()
    run = SearchRun.from_dict(payload["run"])

    assert payload["gate_config"]["mode"] == "require_before_select"
    assert payload["gate_config"]["max_assessments"] == 1
    assert len(run.assessments) == 1
    assert payload["outcome"]["terminated"] is False


def test_judge_reduce_gate_example_prunes_and_selects() -> None:
    example = build_judge_reduce_gate_example()
    run = example["run"]

    assert run.recipe_kind == "judge_reduce"
    assert len(run.assessments) == 1
    assert run.metadata["gate_mode"] == "prune_on_verdict"
    assert len(example["outcome"].pruned_candidate_ids) == 1
    assert example["outcome"].selected_candidate_id is not None
    assert any(event.assessment_ids for event in run.events if event.operator_kind == "verify")


def test_judge_reduce_gate_payload_round_trips() -> None:
    payload = build_judge_reduce_gate_example_payload()
    run = SearchRun.from_dict(payload["run"])

    assert payload["gate_config"]["mode"] == "prune_on_verdict"
    assert payload["gate_config"]["max_assessments"] == 2
    assert len(run.assessments) == 1
    assert len(payload["outcome"]["pruned_candidate_ids"]) == 1


def test_branch_execute_verify_reference_recipe_is_credible_and_barriered() -> None:
    example = build_branch_execute_verify_reference_recipe()
    run = example["run"]

    assert run.recipe_kind == "branch_execute_verify"
    assert len(run.assessments) >= 1
    assert run.metadata["gate_mode"] == "prune_on_verdict"
    assert len(run.branch_states) == 2
    assert any(event.assessment_ids for event in run.events if event.operator_kind == "verify")
    assert example["outcome"].selected_candidate_id is not None


def test_branch_execute_verify_reference_recipe_payload_round_trips() -> None:
    payload = build_branch_execute_verify_reference_recipe_payload()
    run = SearchRun.from_dict(payload["run"])

    assert payload["gate_config"]["backend_kind"] == "exact_tests.v1"
    assert payload["gate_config"]["mode"] == "prune_on_verdict"
    assert payload["gate_config"]["max_assessments"] == 2
    assert len(run.assessments) >= 1
    assert payload["outcome"]["selected_candidate_id"] is not None


def test_dag_v2_e4_widening_packet_is_assessment_led() -> None:
    packet = build_dag_v2_e4_widening_packet()
    recipe_kinds = {item["recipe_kind"] for item in packet["recipes"]}

    assert packet["credible_family_count"] == 3
    assert packet["widening_due_to_assessment_layer"] is True
    assert packet["new_public_noun_families_added"] == 1
    assert recipe_kinds == {"frontier_verify", "judge_reduce", "branch_execute_verify"}


def test_dag_v2_e4_widening_packet_payload_round_trips() -> None:
    payload = build_dag_v2_e4_widening_packet_payload()

    assert payload["credible_family_count"] == 3
    assert payload["widening_due_to_assessment_layer"] is True
    assert payload["new_public_noun_families_added"] == 1
    assert len(payload["recipes"]) == 3


def test_assessment_linkage_survives_trajectory_export() -> None:
    example = build_branch_execute_verify_reference_recipe()
    exported = export_search_trajectory(example["run"])

    assert any(step.assessment_ids for step in exported.steps)
    assert exported.recipe_kind == "branch_execute_verify"


def test_dag_v2_stop_go_synthesis_is_frozen_and_narrow() -> None:
    synthesis = build_dag_v2_stop_go_synthesis()

    assert synthesis["trajectory_export"]["assessment_ids_linked"] is True
    assert synthesis["optimize_adapter"]["outside_dag_kernel"] is True
    assert synthesis["optimize_adapter"]["introduces_optimize_public_nouns_into_dag"] is False
    assert synthesis["darwin_boundary"]["campaign_nouns_added_to_dag"] is False
    assert synthesis["rl_facing_note"]["training_framework_added"] is False
    assert synthesis["stop_go"]["current_decision"] == "stop_and_freeze"
    assert synthesis["stop_go"]["only_new_public_noun_family"] == "SearchAssessment"


def test_dag_v2_stop_go_synthesis_payload_round_trips() -> None:
    payload = build_dag_v2_stop_go_synthesis_payload()

    assert payload["trajectory_export"]["assessment_ids_linked"] is True
    assert payload["optimize_adapter"]["outside_dag_kernel"] is True
    assert payload["darwin_boundary"]["campaign_nouns_added_to_dag"] is False
    assert payload["rl_facing_note"]["training_framework_added"] is False
    assert payload["stop_go"]["async_public_mode_added"] is False


def test_post_v2_study_01_verifier_patch_branch_is_recipe_level_pressure() -> None:
    example = build_post_v2_study_01_verifier_patch_branch()
    run = example["run"]

    assert run.recipe_kind == "verifier_patch_branch_pressure_pass"
    assert len(run.assessments) == 3
    assert len(run.branch_states) == 3
    assert example["outcome"].selected_candidate_id == example["repair_candidate_id"]
    assert example["evidence"]["repeated_shape"] is False
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "recipe_level"


def test_post_v2_study_01_verifier_patch_branch_payload_round_trips() -> None:
    payload = build_post_v2_study_01_verifier_patch_branch_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "verifier_patch_branch_pressure_pass"
    assert len(run.assessments) == 3
    assert payload["outcome"]["selected_candidate_id"] == payload["repair_candidate_id"]
    assert payload["evidence"]["repeated_shape"] is False
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_02_judge_reducer_rounds_stays_narrow() -> None:
    example = build_post_v2_study_02_judge_reducer_rounds()
    run = example["run"]

    assert run.recipe_kind == "judge_reducer_rounds_pressure_pass"
    assert len(run.assessments) == 2
    assert example["round1_outcome"].selected_candidate_id is not None
    assert example["round2_outcome"].selected_candidate_id == example["synthesis_candidate_id"]
    assert example["evidence"]["repeated_shape"] is False
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_02_judge_reducer_rounds_payload_round_trips() -> None:
    payload = build_post_v2_study_02_judge_reducer_rounds_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "judge_reducer_rounds_pressure_pass"
    assert len(run.assessments) == 2
    assert payload["round1_outcome"]["selected_candidate_id"] is not None
    assert payload["round2_outcome"]["selected_candidate_id"] == payload["synthesis_candidate_id"]
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_03_branch_execute_verify_deeper_stays_narrow() -> None:
    example = build_post_v2_study_03_branch_execute_verify_deeper()
    run = example["run"]

    assert run.recipe_kind == "branch_execute_verify_deeper_pressure_pass"
    assert len(run.assessments) == 6
    assert len(run.branch_states) == 4
    assert example["judge_outcome"].selected_candidate_id is not None
    assert example["evidence"]["repeated_shape"] is False
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_03_branch_execute_verify_deeper_payload_round_trips() -> None:
    payload = build_post_v2_study_03_branch_execute_verify_deeper_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "branch_execute_verify_deeper_pressure_pass"
    assert len(run.assessments) == 6
    assert payload["judge_outcome"]["selected_candidate_id"] is not None
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_04_optimize_adapter_probe_stays_outside_dag_kernel() -> None:
    example = build_post_v2_study_04_optimize_adapter_probe()
    trajectory = example["trajectory"]
    adapter_payload = example["optimize_adapter_payload"]

    assert trajectory.selected_candidate_id == adapter_payload["selected_candidate_id"]
    assert len(adapter_payload["trajectory_assessment_ids"]) >= 1
    assert set(adapter_payload["assessment_backend_kinds"]) == {"exact_tests.v1", "judge_pairwise.v1"}
    assert adapter_payload["adapter_boundary"]["outside_dag_kernel"] is True
    assert adapter_payload["adapter_boundary"]["introduced_optimize_public_nouns_into_dag"] is False
    assert example["evidence"]["repeated_shape"] is False
    assert example["evidence"]["owner_boundary"] == "adapter_level"


def test_post_v2_study_04_optimize_adapter_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_04_optimize_adapter_probe_payload()
    run = SearchRun.from_dict(payload["run"])
    trajectory = SearchTrajectoryExport.from_dict(payload["trajectory"])

    assert run.recipe_kind == "branch_execute_verify_deeper_pressure_pass"
    assert trajectory.selected_candidate_id == payload["optimize_adapter_payload"]["selected_candidate_id"]
    assert len(payload["optimize_adapter_payload"]["trajectory_assessment_ids"]) >= 1
    assert payload["optimize_adapter_payload"]["adapter_boundary"]["outside_dag_kernel"] is True
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_05_rl_facing_probe_stays_downstream() -> None:
    example = build_post_v2_study_05_rl_facing_probe()
    packet = example["rl_consumption_packet"]

    assert packet["trajectory_count"] == 1
    assert packet["step_count"] == len(example["trajectory"].steps)
    assert packet["reward_signal_count"] == len(example["trajectory"].reward_signals)
    assert packet["assessment_linked_step_count"] >= 1
    assert packet["rl_boundary"]["training_framework_added"] is False
    assert packet["rl_boundary"]["public_rl_control_surface_added"] is False
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "downstream_consumer_level"


def test_post_v2_study_05_rl_facing_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_05_rl_facing_probe_payload()
    run = SearchRun.from_dict(payload["run"])
    trajectory = SearchTrajectoryExport.from_dict(payload["trajectory"])
    dataset = SearchOfflineDataset.from_dict(payload["dataset"])

    assert run.recipe_kind == "branch_execute_verify_deeper_pressure_pass"
    assert dataset.dataset_id == payload["rl_consumption_packet"]["dataset_id"]
    assert trajectory.selected_candidate_id == payload["rl_consumption_packet"]["selected_candidate_id"]
    assert payload["rl_consumption_packet"]["assessment_linked_step_count"] >= 1
    assert payload["rl_consumption_packet"]["rl_boundary"]["training_framework_added"] is False


def test_post_v2_study_06_darwin_boundary_probe_keeps_dag_frozen() -> None:
    example = build_post_v2_study_06_darwin_boundary_probe()
    owners = {item["owner"] for item in example["scenarios"]}

    assert "dag_local" in owners
    assert "darwin_local" in owners
    assert example["evidence"]["repeated_shape"] is False
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["synthesis"]["no_v3_now"] is True
    assert example["synthesis"]["dag_v2_should_remain_frozen"] is True


def test_post_v2_study_06_darwin_boundary_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_06_darwin_boundary_probe_payload()

    assert len(payload["scenarios"]) == 4
    assert payload["evidence"]["future_v3_evidence"] is False
    assert payload["synthesis"]["no_v3_now"] is True
    assert payload["synthesis"]["repeated_shape_gap_count"] == 0


def test_post_v2_study_07_message_passing_adjudication_stays_narrow() -> None:
    example = build_post_v2_study_07_message_passing_adjudication()
    run = example["run"]

    assert run.recipe_kind == "message_passing_adjudication_pressure_pass"
    assert len(run.carry_states) >= 1
    assert len(run.assessments) == 1
    assert run.selected_candidate_id == example["adjudicated_candidate_id"]
    assert example["carry_state_id"] == run.metadata["carry_state_id"]
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_07_message_passing_adjudication_payload_round_trips() -> None:
    payload = build_post_v2_study_07_message_passing_adjudication_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "message_passing_adjudication_pressure_pass"
    assert len(run.assessments) == 1
    assert payload["outcome"]["selected_candidate_id"] == payload["adjudicated_candidate_id"]
    assert payload["carry_state_id"] == run.metadata["carry_state_id"]
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_08_verifier_judge_handoff_stays_narrow() -> None:
    example = build_post_v2_study_08_verifier_judge_handoff()
    run = example["run"]

    assert run.recipe_kind == "message_passing_verifier_judge_handoff"
    assert len(example["verifier_outcome"].assessments) == 2
    assert len(example["judge_outcome"].assessments) == 1
    assert len(run.assessments) == 3
    assert run.selected_candidate_id == example["adjudicated_candidate_id"]
    assert example["carry_state_id"] == run.metadata["carry_state_id"]
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "recipe_level"


def test_post_v2_study_08_verifier_judge_handoff_payload_round_trips() -> None:
    payload = build_post_v2_study_08_verifier_judge_handoff_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "message_passing_verifier_judge_handoff"
    assert len(payload["verifier_outcome"]["assessment_ids"]) == 2
    assert len(payload["judge_outcome"]["assessment_ids"]) == 1
    assert payload["judge_outcome"]["selected_candidate_id"] == payload["adjudicated_candidate_id"]
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_09_optimize_objective_breakdown_probe_stays_adapter_local() -> None:
    example = build_post_v2_study_09_optimize_objective_breakdown_probe()
    result = example["objective_breakdown_result"]

    assert isinstance(result, ObjectiveBreakdownResult)
    assert result.candidate_id == example["selected_candidate_id"]
    assert result.metadata["outside_dag_kernel"] is True
    assert example["adapter_boundary"]["outside_dag_kernel"] is True
    assert example["adapter_boundary"]["introduced_optimize_public_nouns_into_dag"] is False
    assert example["evidence"]["repeated_shape"] is False


def test_post_v2_study_09_optimize_objective_breakdown_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_09_optimize_objective_breakdown_probe_payload()
    run = SearchRun.from_dict(payload["run"])
    result = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert run.selected_candidate_id == payload["selected_candidate_id"]
    assert result.candidate_id == payload["selected_candidate_id"]
    assert payload["adapter_boundary"]["used_real_optimize_records"] is True
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_10_optimize_benchmark_promotion_probe_stays_adapter_local() -> None:
    example = build_post_v2_study_10_optimize_benchmark_promotion_probe()
    manifest = example["benchmark_manifest"]
    summary = example["promotion_summary"]

    assert isinstance(manifest, BenchmarkRunManifest)
    assert isinstance(summary, PromotionEvidenceSummary)
    assert summary.candidate_id == example["selected_candidate_id"]
    assert manifest.hidden_hold_sample_ids() == ["sample.verifier_judge_handoff.hidden"]
    assert example["adapter_boundary"]["promotion_logic_stayed_adapter_local"] is True
    assert example["evidence"]["repeated_shape"] is False


def test_post_v2_study_10_optimize_benchmark_promotion_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_10_optimize_benchmark_promotion_probe_payload()
    manifest = BenchmarkRunManifest.from_dict(payload["benchmark_manifest"])
    summary = PromotionEvidenceSummary.from_dict(payload["promotion_summary"])

    assert manifest.hidden_hold_sample_ids() == ["sample.verifier_judge_handoff.hidden"]
    assert summary.candidate_id == payload["selected_candidate_id"]
    assert payload["adapter_boundary"]["used_real_optimize_records"] is True
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_11_branch_carry_hybrid_stays_narrow() -> None:
    example = build_post_v2_study_11_branch_carry_hybrid()
    run = example["run"]

    assert run.recipe_kind == "branch_carry_hybrid_pressure_pass"
    assert len(run.carry_states) >= 1
    assert len(example["outcome"].assessments) == 1
    assert run.selected_candidate_id == example["review_candidate_id"]
    assert example["carry_state_id"] == run.metadata["carry_state_id"]
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_11_branch_carry_hybrid_payload_round_trips() -> None:
    payload = build_post_v2_study_11_branch_carry_hybrid_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "branch_carry_hybrid_pressure_pass"
    assert payload["outcome"]["selected_candidate_id"] == payload["review_candidate_id"]
    assert payload["base_selected_candidate_id"] != payload["review_candidate_id"]
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_12_optimize_comparison_probe_stays_adapter_local() -> None:
    example = build_post_v2_study_12_optimize_comparison_probe()
    manifest = example["benchmark_manifest"]
    comparison = example["comparison_result"]

    assert isinstance(manifest, BenchmarkRunManifest)
    assert isinstance(comparison, CandidateComparisonResult)
    assert comparison.parent_candidate_id != comparison.child_candidate_id
    assert comparison.outcome == "non_inferior"
    assert example["adapter_boundary"]["comparison_logic_stayed_adapter_local"] is True
    assert example["evidence"]["repeated_shape"] is False


def test_post_v2_study_12_optimize_comparison_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_12_optimize_comparison_probe_payload()
    manifest = BenchmarkRunManifest.from_dict(payload["benchmark_manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])

    assert manifest.hidden_hold_sample_ids() == ["sample.branch_carry_hybrid.hidden"]
    assert comparison.child_candidate_id == payload["selected_candidate_id"]
    assert payload["adapter_boundary"]["used_real_optimize_records"] is True
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_13_multi_candidate_tournament_stays_narrow() -> None:
    example = build_post_v2_study_13_multi_candidate_tournament()
    run = example["run"]

    assert run.recipe_kind == "multi_candidate_tournament_pressure_pass"
    assert len(example["semifinal_outcome"].assessments) == 1
    assert len(example["final_outcome"].assessments) == 1
    assert run.selected_candidate_id == example["review_candidate_id"]
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_13_multi_candidate_tournament_payload_round_trips() -> None:
    payload = build_post_v2_study_13_multi_candidate_tournament_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "multi_candidate_tournament_pressure_pass"
    assert payload["final_outcome"]["selected_candidate_id"] == payload["review_candidate_id"]
    assert len(payload["semifinal_outcome"]["assessment_ids"]) == 1
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_14_optimize_transfer_cohort_probe_stays_adapter_local() -> None:
    example = build_post_v2_study_14_optimize_transfer_cohort_probe()
    summary = example["promotion_summary"]
    cohort = example["transfer_cohort"]

    assert isinstance(summary, PromotionEvidenceSummary)
    assert isinstance(cohort, TransferCohortManifest)
    assert summary.claim_tier == "cohort_supported"
    assert cohort.cohort_id in summary.transfer_cohort_ids
    assert example["adapter_boundary"]["transfer_logic_stayed_adapter_local"] is True
    assert example["evidence"]["repeated_shape"] is False


def test_post_v2_study_14_optimize_transfer_cohort_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_14_optimize_transfer_cohort_probe_payload()
    summary = PromotionEvidenceSummary.from_dict(payload["promotion_summary"])
    cohort = TransferCohortManifest.from_dict(payload["transfer_cohort"])

    assert summary.claim_tier == "cohort_supported"
    assert cohort.cohort_id in summary.transfer_cohort_ids
    assert payload["adapter_boundary"]["used_real_optimize_records"] is True
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_15_reducer_after_tournament_stays_narrow() -> None:
    example = build_post_v2_study_15_reducer_after_tournament()
    run = example["run"]

    assert run.recipe_kind == "reducer_after_tournament_pressure_pass"
    assert run.selected_candidate_id == example["reducer_candidate_id"]
    assert example["carry_state_id"] == run.metadata["carry_state_id"]
    assert len(example["outcome"].assessments) == 2
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_15_reducer_after_tournament_payload_round_trips() -> None:
    payload = build_post_v2_study_15_reducer_after_tournament_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "reducer_after_tournament_pressure_pass"
    assert payload["outcome"]["selected_candidate_id"] == payload["reducer_candidate_id"]
    assert len(payload["outcome"]["assessment_ids"]) == 2
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_16_optimize_reflection_probe_stays_adapter_local() -> None:
    example = build_post_v2_study_16_optimize_reflection_probe()
    decision = example["reflection_decision"]

    assert isinstance(decision, ReflectionDecision)
    assert decision.target_candidate_id == example["target_candidate_id"]
    assert decision.should_mutate is True
    assert decision.recommended_loci == ["carry_state_summary"]
    assert example["adapter_boundary"]["reflection_logic_stayed_adapter_local"] is True
    assert example["evidence"]["repeated_shape"] is False


def test_post_v2_study_16_optimize_reflection_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_16_optimize_reflection_probe_payload()
    decision = ReflectionDecision.from_dict(payload["reflection_decision"])

    assert decision.target_candidate_id == payload["target_candidate_id"]
    assert decision.should_mutate is True
    assert payload["adapter_boundary"]["used_real_optimize_records"] is True
    assert payload["evidence"]["future_v3_evidence"] is False


def test_post_v2_study_17_repair_loop_after_reducer_stays_narrow() -> None:
    example = build_post_v2_study_17_repair_loop_after_reducer()
    run = example["run"]

    assert run.recipe_kind == "repair_loop_after_reducer_pressure_pass"
    assert run.selected_candidate_id == example["repaired_candidate_id"]
    assert example["carry_state_id"] == run.metadata["carry_state_id"]
    assert len(example["outcome"].assessments) == 2
    assert example["evidence"]["future_v3_evidence"] is False
    assert example["evidence"]["owner_boundary"] == "private_helper_level"


def test_post_v2_study_17_repair_loop_after_reducer_payload_round_trips() -> None:
    payload = build_post_v2_study_17_repair_loop_after_reducer_payload()
    run = SearchRun.from_dict(payload["run"])

    assert run.recipe_kind == "repair_loop_after_reducer_pressure_pass"
    assert payload["outcome"]["selected_candidate_id"] == payload["repaired_candidate_id"]
    assert len(payload["outcome"]["assessment_ids"]) == 2
    assert payload["evidence"]["repeated_shape"] is False


def test_post_v2_study_18_optimize_mutation_proposal_probe_stays_adapter_local() -> None:
    example = build_post_v2_study_18_optimize_mutation_proposal_probe()
    proposal = example["mutation_proposal"]

    assert isinstance(proposal, MutationProposal)
    assert proposal.candidate.candidate_id == example["target_candidate_id"]
    assert proposal.candidate.applied_loci == ["carry_state_summary"]
    assert example["adapter_boundary"]["mutation_logic_stayed_adapter_local"] is True
    assert example["evidence"]["repeated_shape"] is False


def test_post_v2_study_18_optimize_mutation_proposal_probe_payload_round_trips() -> None:
    payload = build_post_v2_study_18_optimize_mutation_proposal_probe_payload()
    proposal = MutationProposal.from_dict(payload["mutation_proposal"])

    assert proposal.candidate.candidate_id == payload["target_candidate_id"]
    assert proposal.candidate.applied_loci == ["carry_state_summary"]
    assert payload["adapter_boundary"]["used_real_optimize_records"] is True
    assert payload["evidence"]["future_v3_evidence"] is False
