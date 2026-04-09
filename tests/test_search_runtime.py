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
    AssessmentLineagePacket,
    BaselineComparisonPacket,
    BenchmarkControlPacket,
    CompositionSeamPacket,
    ComputeBudgetLedger,
    ConsumerHandoffPacket,
    FidelityScorecard,
    FrontierPolicyAudit,
    PaperRecipeManifest,
    RepeatedShapeRegisterEntry,
    ReplayExportIntegrityPacket,
    ReplicationDeviationLedger,
    SearchOfflineDataset,
    SearchAssessment,
    SearchBranchState,
    SearchCandidate,
    SearchCarryState,
    SearchEvent,
    SearchFrontier,
    SearchMessage,
    SearchRewardSignal,
    TopologyAudit,
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
    build_dag_v3_pacore_paper_profile,
    build_dag_v3_pacore_paper_profile_payload,
    build_dag_v3_pacore_parallel_vs_sequential_packet,
    build_dag_v3_pacore_parallel_vs_sequential_packet_payload,
    build_dag_v3_pacore_replication_packet,
    build_dag_v3_pacore_replication_packet_payload,
    build_dag_v3_pacore_round_profile_packet,
    build_dag_v3_pacore_round_profile_packet_payload,
    build_dag_v3_pacore_message_ablation_packet,
    build_dag_v3_pacore_message_ablation_packet_payload,
    build_dag_v3_pacore_conclusion_only_compaction_baseline,
    build_dag_v3_pacore_conclusion_only_compaction_baseline_payload,
    build_dag_v3_cross_paper_synthesis_packet,
    build_dag_v3_cross_paper_synthesis_packet_payload,
    build_dag_v3_darwin_boundary_update_packet,
    build_dag_v3_darwin_boundary_update_packet_payload,
    build_dag_v3_freeze_decision_gate_packet,
    build_dag_v3_freeze_decision_gate_packet_payload,
    build_dag_v3_optimize_ready_comparison_packet,
    build_dag_v3_optimize_ready_comparison_packet_payload,
    build_dag_v3_phase1_smoke_packet,
    build_dag_v3_phase1_smoke_packet_payload,
    build_dag_replication_v1_got_sorting_packet,
    build_dag_replication_v1_got_sorting_packet_payload,
    build_dag_replication_v1_moa_layered_packet,
    build_dag_replication_v1_moa_layered_packet_payload,
    build_dag_replication_v1_tot_game24_packet,
    build_dag_replication_v1_tot_game24_packet_payload,
    build_dag_replication_v1_codetree_packet,
    build_dag_replication_v1_codetree_packet_payload,
    build_dag_v4_phase1_control_packet,
    build_dag_v4_phase1_control_packet_payload,
    build_dag_v4_got_v2_packet,
    build_dag_v4_got_v2_packet_payload,
    build_dag_v4_tot_v2_packet,
    build_dag_v4_tot_v2_packet_payload,
    build_dag_v4_moa_v2_packet,
    build_dag_v4_moa_v2_packet_payload,
    build_dag_v4_codetree_v2_packet,
    build_dag_v4_codetree_v2_packet_payload,
    build_dag_v4_bavt_packet,
    build_dag_v4_bavt_packet_payload,
    build_dag_v4_adaptive_parallel_mcts_lite_packet,
    build_dag_v4_adaptive_parallel_mcts_lite_packet_payload,
    build_dag_v4_team_of_thoughts_packet,
    build_dag_v4_team_of_thoughts_packet_payload,
    build_dag_v4_dci_packet,
    build_dag_v4_dci_packet_payload,
    build_dag_v4_optimize_consumer_packet,
    build_dag_v4_optimize_consumer_packet_payload,
    build_dag_v4_rl_consumer_packet,
    build_dag_v4_rl_consumer_packet_payload,
    build_dag_v4_cross_system_seam_packet,
    build_dag_v4_cross_system_seam_packet_payload,
    build_dag_v4_helper_exhaustion_counterfactual_packet,
    build_dag_v4_helper_exhaustion_counterfactual_packet_payload,
    build_dag_v4_final_adjudication_packet,
    build_dag_v4_final_adjudication_packet_payload,
    build_dag_v3_rl_facing_export_slice_packet,
    build_dag_v3_rl_facing_export_slice_packet_payload,
    build_dag_v3_rsa_budget_matched_baseline_packet,
    build_dag_v3_rsa_budget_matched_baseline_packet_payload,
    build_dag_v3_rsa_nkt_sweep_packet,
    build_dag_v3_rsa_nkt_sweep_packet_payload,
    build_dag_v3_rsa_paper_profile,
    build_dag_v3_rsa_paper_profile_payload,
    build_dag_v3_rsa_replication_packet,
    build_dag_v3_rsa_replication_packet_payload,
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
    compute_fidelity_metrics,
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


def test_dag_v3_fidelity_artifacts_round_trip() -> None:
    manifest = PaperRecipeManifest(
        manifest_id="manifest.test",
        paper_key="paper.test",
        paper_title="Paper Test",
        family_kind="aggregation_search",
        runtime_recipe_kind="rsa_population_recombination",
        fidelity_target="medium_fidelity",
        model_policy="gpt_5_4_mini_default",
        benchmark_packet="paper.test.slice.v1",
        control_profile={"population_size": 4},
        baseline_ids=["baseline_a"],
    )
    scorecard = FidelityScorecard(
        scorecard_id="scorecard.test",
        paper_key="paper.test",
        fidelity_label="medium_fidelity",
        dimensions={"structural_fidelity": "pass"},
    )
    ledger = ComputeBudgetLedger(
        ledger_id="ledger.test",
        paper_key="paper.test",
        model_tier="gpt_5_4_mini",
        entries=[{"entry_id": "entry.test", "kind": "prompt_tokens", "label": "prompt_tokens", "quantity": 12, "unit": "tokens"}],
        normalization_rule="trajectory_count_matched",
    )
    baseline_packet = BaselineComparisonPacket(
        packet_id="baseline.test",
        paper_key="paper.test",
        normalization_rule="trajectory_count_matched",
        baseline_ids=["baseline_a"],
    )
    deviations = ReplicationDeviationLedger(
        ledger_id="deviations.test",
        paper_key="paper.test",
        deviations=[{"deviation_id": "dev.test", "severity": "low", "summary": "model substitution"}],
    )

    assert PaperRecipeManifest.from_dict(manifest.to_dict()) == manifest
    assert FidelityScorecard.from_dict(scorecard.to_dict()) == scorecard
    assert ComputeBudgetLedger.from_dict(ledger.to_dict()) == ledger
    assert BaselineComparisonPacket.from_dict(baseline_packet.to_dict()) == baseline_packet
    assert ReplicationDeviationLedger.from_dict(deviations.to_dict()) == deviations
    assert ledger.total_for_unit("tokens") == 12.0


def test_dag_v4_phase1_helper_artifacts_round_trip() -> None:
    repeated_shape = RepeatedShapeRegisterEntry(
        gap_label="frontier_decision_truth",
        target_family="tot_v2",
        topology_class="F",
        where_it_appears="bounded frontier rerun",
        current_workaround="frontier policy audit",
        why_workaround_is_insufficient="helper exhaustion not attempted yet",
        effect_on_fidelity_tier="bounded_medium_only",
        effect_on_replay_export="none observed",
        primary_locus="helper_and_evaluator_control_only",
        seen_in_other_targets=["bavt_candidate"],
        seen_in_consumers=["optimize_preflight"],
        helper_exhausted=False,
        counts_toward_review=False,
    )
    topology_audit = TopologyAudit(
        audit_id="topology.audit.test",
        target_family="got_v2",
        topology_class="G",
        parentage_reconstructable=True,
        fan_flow_reconstructable=True,
        feedback_loop_reconstructable=True,
        shadow_state_required=False,
    )
    frontier_audit = FrontierPolicyAudit(
        audit_id="frontier.audit.test",
        target_family="tot_v2",
        topology_class="F",
        select_prune_reconstructable=True,
        budget_conditioned_reconstructable=True,
        reopen_backtrack_reconstructable=False,
        consumer_can_explain_frontier=True,
        shadow_policy_required=False,
    )
    lineage_packet = AssessmentLineagePacket(
        packet_id="assessment.lineage.test",
        target_family="codetree_v2",
        topology_class="W",
        assessment_kinds=["judge", "execute"],
        action_links=[{"assessment_id": "a1", "action_id": "select.1"}],
        mixed_chain_reconstructable=True,
    )
    replay_packet = ReplayExportIntegrityPacket(
        packet_id="replay.export.test",
        target_family="got_v2",
        topology_class="G",
        export_modes=["trajectory_export"],
        preserved_semantics=["parentage", "assessment linkage"],
        lost_semantics=[],
        shadow_assumptions_required=False,
    )
    benchmark_control = BenchmarkControlPacket(
        packet_id="benchmark.control.test",
        target_family="tot_v2",
        control_kind="evaluator_strength",
        evaluator_stack=["packet_review_judge"],
        controls=["match_call_budget"],
        known_confound_risks=["weak evaluator"],
    )
    handoff_packet = ConsumerHandoffPacket(
        packet_id="handoff.test",
        target_family="got_v2",
        consumer_kind="optimize",
        artifact_kinds=["SearchRun", "FidelityScorecard"],
        handoff_contract=["no shadow semantics"],
        shadow_semantics_required=False,
    )
    seam_packet = CompositionSeamPacket(
        packet_id="seam.test",
        source_family="got_v2",
        target_kind="optimize",
        seam_labels=["assessment_to_action_continuity"],
        issues=[{"issue_id": "seam.1", "status": "not_observed"}],
        repeated_shape_candidate=False,
    )

    assert RepeatedShapeRegisterEntry.from_dict(repeated_shape.to_dict()) == repeated_shape
    assert TopologyAudit.from_dict(topology_audit.to_dict()) == topology_audit
    assert FrontierPolicyAudit.from_dict(frontier_audit.to_dict()) == frontier_audit
    assert AssessmentLineagePacket.from_dict(lineage_packet.to_dict()) == lineage_packet
    assert ReplayExportIntegrityPacket.from_dict(replay_packet.to_dict()) == replay_packet
    assert BenchmarkControlPacket.from_dict(benchmark_control.to_dict()) == benchmark_control
    assert ConsumerHandoffPacket.from_dict(handoff_packet.to_dict()) == handoff_packet
    assert CompositionSeamPacket.from_dict(seam_packet.to_dict()) == seam_packet


def test_dag_v3_rsa_profile_and_smoke_packet_are_non_kernel() -> None:
    example = build_dag_v3_rsa_paper_profile()
    payload = build_dag_v3_rsa_paper_profile_payload()

    assert example["recipe_manifest"].paper_key == "rsa_recursive_self_aggregation"
    assert example["scorecard"].dimensions["structural_fidelity"] == "pass"
    assert example["compute_ledger"].total_for_unit("tokens") > 0.0
    assert example["baseline_packet"].normalization_rule == "trajectory_count_matched"
    assert example["deviation_ledger"].deviations[0]["severity"] in {"medium", "low"}
    assert example["smoke_packet"]["model_tier"] == "gpt_5_4_mini"
    assert payload["recipe_manifest"]["runtime_recipe_kind"] == "rsa_population_recombination"


def test_dag_v3_pacore_profile_and_smoke_packet_are_non_kernel() -> None:
    example = build_dag_v3_pacore_paper_profile()
    payload = build_dag_v3_pacore_paper_profile_payload()

    assert example["recipe_manifest"].paper_key == "pacore_parallel_coordinated_reasoning"
    assert example["recipe_manifest"].control_profile["compaction_backend_kind"] == "bounded_candidate_rollup.v1"
    assert example["scorecard"].notes["compaction_requirement"] == "explicit and auditable"
    assert example["compute_ledger"].total_for_unit("messages") >= 0.0
    assert payload["recipe_manifest"]["runtime_recipe_kind"] == "pacore_message_passing"


def test_dag_v3_phase1_smoke_packet_uses_existing_dag_artifacts() -> None:
    example = build_dag_v3_phase1_smoke_packet()
    payload = build_dag_v3_phase1_smoke_packet_payload()

    assert example["kernel_change_required"] is False
    assert len(example["paper_profiles"]) == 2
    assert example["metadata"]["frozen_kernel"] is True
    assert payload["metadata"]["model_tier_default"] == "gpt_5_4_mini"
    assert "rsa" in payload["shared_metric_snapshot"]
    assert "pacore" in payload["shared_metric_snapshot"]


def test_dag_v3_common_fidelity_metrics_are_reproducible_from_existing_runs() -> None:
    rsa = build_rsa_search_runtime_example()["run"]
    pacore = build_pacore_search_runtime_example()["run"]
    rsa_metrics = compute_fidelity_metrics(rsa)
    pacore_metrics = compute_fidelity_metrics(pacore)

    assert rsa_metrics["aggregability_gap"] > 0.0
    assert rsa_metrics["aggregation_gain"] >= 0.0
    assert pacore_metrics["message_efficiency"] > 0.0
    assert pacore_metrics["verifier_yield"] >= 0.0


def test_dag_v3_rsa_nkt_sweep_packet_is_fixed_seed_and_structured() -> None:
    example = build_dag_v3_rsa_nkt_sweep_packet()
    payload = build_dag_v3_rsa_nkt_sweep_packet_payload()

    assert example["paper_key"] == "rsa_recursive_self_aggregation"
    assert example["metadata"]["fixed_seed"] == 17
    assert len(example["sweep_rows"]) == 4
    assert {row["random_seed"] for row in example["sweep_rows"]} == {17}
    assert any(row["N"] == 8 and row["K"] == 4 and row["T"] == 3 for row in example["sweep_rows"])
    assert payload["model_tier"] == "gpt_5_4_mini"


def test_dag_v3_rsa_budget_matched_baseline_packet_is_normalized() -> None:
    example = build_dag_v3_rsa_budget_matched_baseline_packet()
    payload = build_dag_v3_rsa_budget_matched_baseline_packet_payload()

    assert example["baseline_packet"].paper_key == "rsa_recursive_self_aggregation"
    assert example["baseline_packet"].normalization_rule == "trajectory_count_matched"
    assert len(example["baseline_rows"]) == 4
    assert all(row["matched_by"] == "trajectory_count_matched" for row in example["baseline_rows"])
    assert payload["selected_sweep_row"]["search_id"] == example["selected_sweep_row"]["search_id"]


def test_dag_v3_rsa_replication_packet_carries_phase2_outputs() -> None:
    example = build_dag_v3_rsa_replication_packet()
    payload = build_dag_v3_rsa_replication_packet_payload()

    assert example["recipe_manifest"].paper_key == "rsa_recursive_self_aggregation"
    assert example["scorecard"].fidelity_label == "medium_fidelity"
    assert example["compute_ledger"].total_for_unit("tokens") > 0.0
    assert len(example["sweep_rows"]) == 4
    assert example["qualitative_synthesis"]["best_nkt"]["N"] in {4, 8}
    assert payload["metadata"]["kernel_change_required"] is False


def test_dag_v3_pacore_round_profile_packet_has_low_med_high_profiles() -> None:
    example = build_dag_v3_pacore_round_profile_packet()
    payload = build_dag_v3_pacore_round_profile_packet_payload()

    assert example["paper_key"] == "pacore_parallel_coordinated_reasoning"
    assert [item["label"] for item in example["profiles"]] == ["low", "medium", "high"]
    assert payload["metadata"]["model_tier_default"] == "gpt_5_4_mini"


def test_dag_v3_pacore_conclusion_only_compaction_baseline_is_explicit() -> None:
    example = build_dag_v3_pacore_conclusion_only_compaction_baseline()
    payload = build_dag_v3_pacore_conclusion_only_compaction_baseline_payload()

    assert example["baseline_packet"].paper_key == "pacore_parallel_coordinated_reasoning"
    assert example["baseline_payload"]["mode"] == "conclusion_only"
    assert example["baseline_payload"]["auditable"] is True
    assert payload["baseline_payload"]["source_message_id"] == example["baseline_payload"]["source_message_id"]


def test_dag_v3_pacore_message_ablation_packet_compares_with_and_without_messages() -> None:
    example = build_dag_v3_pacore_message_ablation_packet()
    payload = build_dag_v3_pacore_message_ablation_packet_payload()

    assert len(example["rows"]) == 2
    assert example["rows"][0]["variant"] == "with_message_passing"
    assert example["rows"][1]["variant"] == "without_message_passing"
    assert payload["metadata"]["comparison"] == "with_without_message_passing"


def test_dag_v3_pacore_parallel_vs_sequential_packet_defines_coding_transfer_runner() -> None:
    example = build_dag_v3_pacore_parallel_vs_sequential_packet()
    payload = build_dag_v3_pacore_parallel_vs_sequential_packet_payload()

    assert example["coding_transfer_runner"]["benchmark_packet"] == "pacore.coding_transfer.slice.v1"
    assert example["parallel_variant"]["variant"] == "with_message_passing"
    assert example["sequential_variant"]["variant"] == "without_message_passing"
    assert payload["metadata"]["comparison"] == "parallel_vs_sequential"


def test_dag_v3_pacore_replication_packet_carries_phase3_outputs() -> None:
    example = build_dag_v3_pacore_replication_packet()
    payload = build_dag_v3_pacore_replication_packet_payload()

    assert example["recipe_manifest"].paper_key == "pacore_parallel_coordinated_reasoning"
    assert example["scorecard"].fidelity_label == "medium_fidelity"
    assert len(example["round_profiles"]) == 3
    assert len(example["message_ablation_rows"]) == 2
    assert example["coding_transfer_runner"]["runner_id"] == "dag_v3.pacore.coding_transfer.v1"
    assert payload["metadata"]["kernel_change_required"] is False


def test_dag_v3_optimize_ready_comparison_packet_stays_adapter_level() -> None:
    example = build_dag_v3_optimize_ready_comparison_packet()
    payload = build_dag_v3_optimize_ready_comparison_packet_payload()

    assert example["consumer"] == "optimize"
    assert len(example["recipes"]) == 2
    assert example["adapter_boundary"]["introduced_optimize_public_nouns_into_dag"] is False
    assert payload["metadata"]["kernel_change_required"] is False


def test_dag_v3_rl_facing_export_slice_packet_stays_bounded() -> None:
    example = build_dag_v3_rl_facing_export_slice_packet()
    payload = build_dag_v3_rl_facing_export_slice_packet_payload()

    assert len(example["slices"]) == 2
    assert example["rl_boundary"]["public_rl_control_surface_added"] is False
    assert payload["metadata"]["kernel_change_required"] is False


def test_dag_v3_darwin_boundary_update_packet_keeps_outer_loop_outside_dag() -> None:
    example = build_dag_v3_darwin_boundary_update_packet()
    payload = build_dag_v3_darwin_boundary_update_packet_payload()

    assert "paper_recipe_manifests" in example["still_dag_local"]
    assert "many-run campaign orchestration" in example["still_not_dag_local"]
    assert payload["evidence"]["repeated_dag_local_public_shape_pressure"] == 0


def test_dag_v3_cross_paper_synthesis_packet_classifies_shared_vs_outside_work() -> None:
    example = build_dag_v3_cross_paper_synthesis_packet()
    payload = build_dag_v3_cross_paper_synthesis_packet_payload()

    assert "fidelity helper artifacts instead of kernel expansion" in example["shared"]
    assert "N / K / T sweeps" in example["paper_specific"]["rsa"]
    assert "training-aware replication claims" in example["outside_dag"]
    assert payload["metadata"]["kernel_change_required"] is False


def test_dag_v3_freeze_decision_gate_packet_classifies_remaining_pressure() -> None:
    example = build_dag_v3_freeze_decision_gate_packet()
    payload = build_dag_v3_freeze_decision_gate_packet_payload()

    assert example["freeze_decision"]["current_decision"] == "freeze_and_reclassify"
    assert example["freeze_decision"]["open_dag_v4_now"] is False
    assert example["remaining_pressure"]["dag_kernel"]["status"] == "frozen"
    assert example["remaining_pressure"]["rl"]["status"] == "ready_for_resume"
    assert payload["metadata"]["kernel_change_required"] is False


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


def test_dag_replication_v1_got_sorting_packet_is_bounded_and_lineage_auditable() -> None:
    example = build_dag_replication_v1_got_sorting_packet()
    run = example["run"]

    assert run.recipe_kind == "got_sorting_graph_packet"
    assert run.selected_candidate_id is not None
    assert example["recipe_manifest"].paper_key == "graph_of_thoughts"
    assert example["scorecard"].fidelity_label == "high_structural_fidelity"
    assert example["compute_ledger"].total_for_unit("calls") <= 16.0
    assert example["baseline_packet"].baseline_ids[0] == "direct_answer"
    assert example["replay_audit"]["shadow_state_required"] is False
    assert any(len(row["parent_node_ids"]) == 2 for row in example["lineage_rows"])


def test_dag_replication_v1_got_sorting_payload_round_trips() -> None:
    payload = build_dag_replication_v1_got_sorting_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    scorecard = FidelityScorecard.from_dict(payload["scorecard"])
    compute_ledger = ComputeBudgetLedger.from_dict(payload["compute_ledger"])

    assert run.recipe_kind == "got_sorting_graph_packet"
    assert payload["recipe_manifest"]["paper_key"] == "graph_of_thoughts"
    assert scorecard.fidelity_label == "high_structural_fidelity"
    assert compute_ledger.total_for_unit("calls") <= 16.0
    assert payload["replay_audit"]["merged_parentage_reconstructable"] is True


def test_dag_replication_v1_tot_game24_packet_is_frontier_controlled() -> None:
    example = build_dag_replication_v1_tot_game24_packet()
    run = example["run"]

    assert run.recipe_kind == "tot_game24_frontier_packet"
    assert run.selected_candidate_id is not None
    assert example["recipe_manifest"].paper_key == "tree_of_thoughts"
    assert example["scorecard"].notes["backtrack_scope"] == "reopen disabled in first packet to isolate frontier provenance before adding backtracking"
    assert example["frontier_audit"]["frontier_policy_reconstructable"] is True
    assert example["frontier_audit"]["evaluator_confound_controlled"] is True
    assert example["compute_ledger"].total_for_unit("branches") <= 3.0


def test_dag_replication_v1_tot_game24_payload_round_trips() -> None:
    payload = build_dag_replication_v1_tot_game24_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    scorecard = FidelityScorecard.from_dict(payload["scorecard"])
    baseline_packet = BaselineComparisonPacket.from_dict(payload["baseline_packet"])

    assert run.recipe_kind == "tot_game24_frontier_packet"
    assert payload["recipe_manifest"]["paper_key"] == "tree_of_thoughts"
    assert scorecard.fidelity_label == "high_structural_fidelity"
    assert "discriminator_control" in baseline_packet.baseline_ids
    assert payload["frontier_audit"]["prune_history_reconstructable"] is True


def test_dag_replication_v1_moa_layered_packet_is_lineage_bounded() -> None:
    example = build_dag_replication_v1_moa_layered_packet()
    run = example["run"]

    assert run.recipe_kind == "moa_layered_fan_in_packet"
    assert run.selected_candidate_id is not None
    assert example["recipe_manifest"].paper_key == "mixture_of_agents"
    assert example["scorecard"].fidelity_label == "medium_structural_fidelity"
    assert example["fan_in_audit"]["layered_fan_in_reconstructable"] is True
    assert example["fan_in_audit"]["shared_runtime_gap_detected"] is False
    assert example["compute_ledger"].total_for_unit("layers") == 3.0


def test_dag_replication_v1_moa_layered_payload_round_trips() -> None:
    payload = build_dag_replication_v1_moa_layered_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    scorecard = FidelityScorecard.from_dict(payload["scorecard"])
    baseline_packet = BaselineComparisonPacket.from_dict(payload["baseline_packet"])

    assert run.recipe_kind == "moa_layered_fan_in_packet"
    assert payload["recipe_manifest"]["paper_key"] == "mixture_of_agents"
    assert scorecard.fidelity_label == "medium_structural_fidelity"
    assert "one_layer_moa" in baseline_packet.baseline_ids
    assert payload["layer_roster_manifest"]["homogeneous_or_heterogeneous"] == "heterogeneous"


def test_dag_replication_v1_codetree_packet_is_stage_auditable() -> None:
    example = build_dag_replication_v1_codetree_packet()
    run = example["run"]

    assert run.recipe_kind == "codetree_stage_patch_packet"
    assert run.selected_candidate_id is not None
    assert example["recipe_manifest"].paper_key == "codetree"
    assert example["scorecard"].fidelity_label == "medium_structural_fidelity"
    assert example["codetree_audit"]["critic_action_reconstructable"] is True
    assert example["codetree_audit"]["shared_runtime_gap_detected"] is False
    assert example["execution_feedback_packet"]["visible_tests_passed_after_repair"] == 3


def test_dag_replication_v1_codetree_payload_round_trips() -> None:
    payload = build_dag_replication_v1_codetree_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    scorecard = FidelityScorecard.from_dict(payload["scorecard"])
    compute_ledger = ComputeBudgetLedger.from_dict(payload["compute_ledger"])

    assert run.recipe_kind == "codetree_stage_patch_packet"
    assert payload["recipe_manifest"]["paper_key"] == "codetree"
    assert scorecard.fidelity_label == "medium_structural_fidelity"
    assert compute_ledger.total_for_unit("actions") == 1.0
    assert payload["role_stage_manifest"]["critic_stage"] == "execution_plus_review"


def test_dag_v4_phase1_control_packet_reruns_old_packets_under_new_audit_stack() -> None:
    example = build_dag_v4_phase1_control_packet()
    payload = build_dag_v4_phase1_control_packet_payload()

    assert example["kernel_change_required"] is False
    assert example["metadata"]["old_packet_rerun_count"] == 2
    assert len(example["repeated_shape_register"]) == 2
    assert len(example["topology_audits"]) == 2
    assert len(example["frontier_policy_audits"]) == 2
    assert example["benchmark_control_packet"].control_kind == "evaluator_strength_and_budget_control"
    assert example["replay_export_integrity_packet"].shadow_assumptions_required is False
    assert len(example["consumer_handoff_packets"]) == 2
    assert example["composition_seam_packet"].repeated_shape_candidate is False
    assert payload["source_packets"]["got"]["recipe_manifest"]["paper_key"] == "graph_of_thoughts"
    assert payload["source_packets"]["tot"]["recipe_manifest"]["paper_key"] == "tree_of_thoughts"
    assert payload["consumer_handoff_packets"][0]["consumer_kind"] == "optimize"
    assert payload["consumer_handoff_packets"][1]["consumer_kind"] == "rl"


def test_dag_v4_got_v2_packet_reprobes_feedback_lineage_cleanly() -> None:
    example = build_dag_v4_got_v2_packet()
    payload = build_dag_v4_got_v2_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    topology_audit = TopologyAudit.from_dict(payload["topology_audit"])
    replay_packet = ReplayExportIntegrityPacket.from_dict(payload["replay_export_integrity_packet"])

    assert run.recipe_kind == "got_sorting_graph_packet_v2"
    assert payload["recipe_manifest"]["paper_key"] == "graph_of_thoughts"
    assert payload["removed_assumptions"] == [
        "single_refine_step_only",
        "no_feedback_merge_after_refine",
    ]
    assert topology_audit.topology_class == "G"
    assert topology_audit.feedback_loop_reconstructable is True
    assert replay_packet.shadow_assumptions_required is False
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_tot_v2_packet_reprobes_reopen_policy_cleanly() -> None:
    example = build_dag_v4_tot_v2_packet()
    payload = build_dag_v4_tot_v2_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    frontier_audit = FrontierPolicyAudit.from_dict(payload["frontier_policy_audit"])
    control_packet = BenchmarkControlPacket.from_dict(payload["benchmark_control_packet"])

    assert run.recipe_kind == "tot_game24_frontier_packet_v2"
    assert payload["recipe_manifest"]["paper_key"] == "tree_of_thoughts"
    assert payload["removed_assumptions"] == [
        "reopen_disabled",
        "single_frontier_pass_only",
    ]
    assert frontier_audit.topology_class == "F"
    assert frontier_audit.reopen_backtrack_reconstructable is True
    assert control_packet.control_kind == "evaluator_strength_and_discriminator_control"
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_moa_v2_packet_reprobes_layered_judge_lineage_cleanly() -> None:
    example = build_dag_v4_moa_v2_packet()
    payload = build_dag_v4_moa_v2_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    topology_audit = TopologyAudit.from_dict(payload["topology_audit"])
    lineage_packet = AssessmentLineagePacket.from_dict(payload["assessment_lineage_packet"])

    assert run.recipe_kind == "moa_layered_fan_in_packet_v2"
    assert payload["recipe_manifest"]["paper_key"] == "mixture_of_agents"
    assert payload["removed_assumptions"] == [
        "small_fixed_roster_only",
        "single_judge_pass_only",
    ]
    assert topology_audit.topology_class == "H"
    assert lineage_packet.topology_class == "H"
    assert lineage_packet.mixed_chain_reconstructable is True
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_codetree_v2_packet_reprobes_workspace_lineage_cleanly() -> None:
    example = build_dag_v4_codetree_v2_packet()
    payload = build_dag_v4_codetree_v2_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    topology_audit = TopologyAudit.from_dict(payload["topology_audit"])
    lineage_packet = AssessmentLineagePacket.from_dict(payload["assessment_lineage_packet"])
    replay_packet = ReplayExportIntegrityPacket.from_dict(payload["replay_export_integrity_packet"])

    assert run.recipe_kind == "codetree_stage_patch_packet_v2"
    assert payload["recipe_manifest"]["paper_key"] == "codetree"
    assert payload["removed_assumptions"] == [
        "single_repair_pass_only",
        "no_post_repair_verifier_stage",
    ]
    assert topology_audit.topology_class == "W"
    assert lineage_packet.topology_class == "W"
    assert replay_packet.topology_class == "W"
    assert replay_packet.shadow_assumptions_required is False
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_bavt_packet_reprobes_budget_aware_frontier_cleanly() -> None:
    example = build_dag_v4_bavt_packet()
    payload = build_dag_v4_bavt_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    frontier_audit = FrontierPolicyAudit.from_dict(payload["frontier_policy_audit"])
    control_packet = BenchmarkControlPacket.from_dict(payload["benchmark_control_packet"])

    assert run.recipe_kind == "bavt_budget_aware_frontier_packet"
    assert payload["recipe_manifest"]["paper_key"] == "budget_aware_value_tree_search"
    assert frontier_audit.topology_class == "F"
    assert frontier_audit.budget_conditioned_reconstructable is True
    assert frontier_audit.reopen_backtrack_reconstructable is True
    assert control_packet.control_kind == "budget_conditioned_frontier_and_evaluator_control"
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert example["repeated_shape_entry"].seen_in_other_targets == ["tot_v2"]
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_adaptive_parallel_mcts_lite_packet_stays_bounded_and_auditable() -> None:
    example = build_dag_v4_adaptive_parallel_mcts_lite_packet()
    payload = build_dag_v4_adaptive_parallel_mcts_lite_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    frontier_audit = FrontierPolicyAudit.from_dict(payload["frontier_policy_audit"])
    control_packet = BenchmarkControlPacket.from_dict(payload["benchmark_control_packet"])
    replay_packet = ReplayExportIntegrityPacket.from_dict(payload["replay_export_integrity_packet"])

    assert run.recipe_kind == "adaptive_parallel_mcts_lite_packet"
    assert payload["recipe_manifest"]["paper_key"] == "adaptive_parallel_mcts_lite"
    assert frontier_audit.topology_class == "F"
    assert frontier_audit.budget_conditioned_reconstructable is True
    assert frontier_audit.reopen_backtrack_reconstructable is True
    assert control_packet.control_kind == "parallel_frontier_and_evaluator_control"
    assert replay_packet.shadow_assumptions_required is False
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert example["repeated_shape_entry"].seen_in_other_targets == ["tot_v2", "bavt"]
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_team_of_thoughts_packet_reprobes_layered_role_heterogeneity_cleanly() -> None:
    example = build_dag_v4_team_of_thoughts_packet()
    payload = build_dag_v4_team_of_thoughts_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    topology_audit = TopologyAudit.from_dict(payload["topology_audit"])
    lineage_packet = AssessmentLineagePacket.from_dict(payload["assessment_lineage_packet"])

    assert run.recipe_kind == "team_of_thoughts_layered_packet"
    assert payload["recipe_manifest"]["paper_key"] == "team_of_thoughts"
    assert topology_audit.topology_class == "H"
    assert topology_audit.fan_flow_reconstructable is True
    assert lineage_packet.topology_class == "H"
    assert payload["role_stage_manifest"]["heterogeneous"] is True
    assert payload["role_stage_manifest"]["roles"][0]["role"] == "planner"
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert example["repeated_shape_entry"].seen_in_other_targets == ["moa_v2"]
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_dci_packet_reprobes_typed_shared_workspace_cleanly() -> None:
    example = build_dag_v4_dci_packet()
    payload = build_dag_v4_dci_packet_payload()
    run = SearchRun.from_dict(payload["run"])
    topology_audit = TopologyAudit.from_dict(payload["topology_audit"])
    lineage_packet = AssessmentLineagePacket.from_dict(payload["assessment_lineage_packet"])
    replay_packet = ReplayExportIntegrityPacket.from_dict(payload["replay_export_integrity_packet"])

    assert run.recipe_kind == "dci_typed_deliberation_packet"
    assert payload["recipe_manifest"]["paper_key"] == "dci_typed_epistemic_acts"
    assert topology_audit.topology_class == "D"
    assert topology_audit.feedback_loop_reconstructable is True
    assert lineage_packet.topology_class == "D"
    assert replay_packet.topology_class == "D"
    assert replay_packet.shadow_assumptions_required is False
    assert payload["typed_act_ledger"]["shared_workspace"] is True
    assert payload["typed_act_ledger"]["acts"][1]["typed_act"] == "raise_objection"
    assert len(payload["workspace_snapshot_ids"]) == 2
    assert example["repeated_shape_entry"].counts_toward_review is False
    assert example["repeated_shape_entry"].seen_in_other_targets == ["team_of_thoughts"]
    assert run.selected_candidate_id == payload["run"]["selected_candidate_id"]


def test_dag_v4_optimize_consumer_packet_covers_three_structurally_distinct_families() -> None:
    example = build_dag_v4_optimize_consumer_packet()
    payload = build_dag_v4_optimize_consumer_packet_payload()
    seam_packet = CompositionSeamPacket.from_dict(payload["composition_seam_packet"])
    handoffs = [ConsumerHandoffPacket.from_dict(item) for item in payload["consumer_handoff_packets"]]

    assert payload["consumer_kind"] == "optimize"
    assert len(payload["source_rows"]) == 3
    assert {item["topology_class"] for item in payload["source_rows"]} == {"G", "F", "H"}
    assert len(handoffs) == 3
    assert all(item.consumer_kind == "optimize" for item in handoffs)
    assert all(item.shadow_semantics_required is False for item in handoffs)
    assert all("evidence_sources" in item for item in payload["integrity_rows"])
    assert all(item["measured_shadow_semantics_required"] is False for item in payload["integrity_rows"])
    assert seam_packet.target_kind == "optimize"
    assert seam_packet.repeated_shape_candidate is False
    assert example["repeated_shape_update"]["review_counting_gap_labels"] == []
    assert example["repeated_shape_update"]["dag_kernel_change_required"] is False


def test_dag_v4_rl_consumer_packet_covers_three_structurally_distinct_families() -> None:
    example = build_dag_v4_rl_consumer_packet()
    payload = build_dag_v4_rl_consumer_packet_payload()
    seam_packet = CompositionSeamPacket.from_dict(payload["composition_seam_packet"])
    handoffs = [ConsumerHandoffPacket.from_dict(item) for item in payload["consumer_handoff_packets"]]

    assert payload["consumer_kind"] == "rl"
    assert len(payload["source_rows"]) == 3
    assert {item["topology_class"] for item in payload["source_rows"]} == {"F", "W", "D"}
    assert len(handoffs) == 3
    assert all(item.consumer_kind == "rl" for item in handoffs)
    assert all(item.shadow_semantics_required is False for item in handoffs)
    assert all("evidence_sources" in item for item in payload["integrity_rows"])
    assert all(item["measured_shadow_semantics_required"] is False for item in payload["integrity_rows"])
    assert seam_packet.target_kind == "rl"
    assert seam_packet.repeated_shape_candidate is False
    assert example["repeated_shape_update"]["review_counting_gap_labels"] == []
    assert example["repeated_shape_update"]["dag_kernel_change_required"] is False


def test_dag_v4_cross_system_seam_packet_unifies_consumer_read_without_promoting_gaps() -> None:
    example = build_dag_v4_cross_system_seam_packet()
    payload = build_dag_v4_cross_system_seam_packet_payload()
    seam_packet = CompositionSeamPacket.from_dict(payload["cross_system_seam_packet"])

    assert seam_packet.target_kind == "cross_system"
    assert seam_packet.repeated_shape_candidate is False
    assert len(payload["seam_rows"]) == 2
    assert payload["seam_rows"][0]["consumer_kind"] == "optimize"
    assert payload["seam_rows"][1]["consumer_kind"] == "rl"
    assert payload["repeated_shape_update"]["review_counting_gap_labels"] == []
    assert payload["repeated_shape_update"]["dag_kernel_change_required"] is False
    assert payload["optimize_packet"]["consumer_kind"] == "optimize"
    assert payload["rl_packet"]["consumer_kind"] == "rl"


def test_dag_v4_helper_exhaustion_counterfactual_packet_attempts_helper_only_fixes_and_consumer_reruns() -> None:
    example = build_dag_v4_helper_exhaustion_counterfactual_packet()
    payload = build_dag_v4_helper_exhaustion_counterfactual_packet_payload()
    seam_packet = CompositionSeamPacket.from_dict(payload["composition_seam_packet"])

    assert len(payload["helper_fix_attempts"]) == 2
    assert payload["helper_fix_attempts"][0]["gap_label"] == "budget_conditioned_frontier_truth"
    assert payload["helper_fix_attempts"][1]["gap_label"] == "typed_collective_act_truth"
    assert len(payload["rerun_rows"]) == 2
    assert all(item["helper_fix_applied"] is True for item in payload["rerun_rows"])
    assert all(item["shadow_semantics_required_after_fix"] is False for item in payload["rerun_rows"])
    assert all(item["replay_loss_after_fix"] is False for item in payload["rerun_rows"])
    assert seam_packet.target_kind == "helper_exhaustion"
    assert seam_packet.repeated_shape_candidate is False
    assert payload["repeated_shape_update"]["helper_exhaustion_attempted"] is True
    assert payload["repeated_shape_update"]["review_counting_gap_labels"] == []
    assert payload["repeated_shape_update"]["dag_kernel_change_required"] is False


def test_dag_v4_final_adjudication_packet_keeps_freeze_after_full_v4_program() -> None:
    example = build_dag_v4_final_adjudication_packet()
    payload = build_dag_v4_final_adjudication_packet_payload()

    assert payload["packet_id"] == "dag_v4.final_adjudication.v1"
    assert payload["reviewed_topology_classes"] == ["G", "F", "H", "W", "D"]
    assert payload["reviewed_target_count"] == 8
    assert payload["repeated_shape_summary"]["review_counting_gap_labels"] == []
    assert payload["repeated_shape_summary"]["consumer_confirmation_present"] is True
    assert payload["repeated_shape_summary"]["helper_exhaustion_attempted"] is True
    assert payload["repeated_shape_summary"]["replay_export_corruption_detected"] is False
    assert payload["freeze_decision"]["current_decision"] == "keep_dag_frozen"
    assert payload["freeze_decision"]["open_dag_v5_now"] is False
    assert payload["metadata"]["kernel_change_required"] is False
    assert payload["source_packets"]["cross_system_seam_packet"]["repeated_shape_update"]["dag_kernel_change_required"] is False
    assert payload["source_packets"]["helper_exhaustion_counterfactual_packet"]["repeated_shape_update"]["helper_exhaustion_attempted"] is True
