from __future__ import annotations

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
    build_branch_execute_verify_pressure_cell,
    build_dag_v2_phase0_pressure_packet,
    build_dag_v2_phase0_pressure_packet_payload,
    build_default_search_compaction_registry,
    build_exact_verifier_assessment_example,
    build_exact_verifier_assessment_example_payload,
    build_judge_pairwise_assessment_example,
    build_judge_pairwise_assessment_example_payload,
    build_judge_reducer_pressure_cell,
    build_pacore_search_runtime_example,
    build_pacore_search_runtime_example_payload,
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
