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
    SearchATPDeploymentReadinessKit,
    SearchATPBundlePublicationPacket,
    SearchATPConsumerHandoffStabilizationPacket,
    SearchATPProductionLanePacket,
    SearchATPStageBCloseoutPacket,
    SearchATPOperatorProofTriagePacket,
    SearchATPOperatorTriageKit,
    SearchCTreesBoundaryCanaryPacket,
    SearchCrossExecutionCellResult,
    SearchCrossExecutionHarnessComparisonPacket,
    SearchCrossExecutionMatrixPacket,
    SearchCrossExecutionOptimizeExecutionPacket,
    SearchCrossExecutionOptimizeRegressionLedgerPacket,
    SearchCrossExecutionRLExecutionPacket,
    SearchCrossExecutionRLRegressionLedgerPacket,
    SearchCrossExecutionCloseoutPacket,
    SearchCrossExecutionDivergenceLedgerPacket,
    SearchCrossExecutionNextLocusPacket,
    SearchCrossExecutionRepeatedRunSummaryPacket,
    SearchLiveCommandObservation,
    SearchLiveCommandSpec,
    SearchLiveHarnessCommandMatrixPacket,
    SearchLiveHarnessSmokePacket,
    SearchLiveOptimizeExecutionPacket,
    SearchLiveRLExecutionPacket,
    SearchLiveConsumerConvergencePacket,
    SearchLiveCloseoutPacket,
    SearchOfflineDataset,
    SearchAssessment,
    SearchBranchState,
    SearchCandidate,
    SearchCarryState,
    SearchCrossSystemArtifactIntegrityPacket,
    SearchCrossSystemArtifactIntegrityRow,
    SearchCrossSystemHandoffContract,
    SearchConsumerProofRow,
    SearchConsumerSeamDiagnostic,
    SearchDomainBoundaryControlPacket,
    SearchDomainFrictionSummary,
    SearchDomainPilotPacket,
    SearchEvent,
    SearchFrontier,
    SearchMessage,
    SearchOptimizeComparisonKit,
    SearchOptimizeConsumerExpansionPacket,
    SearchOptimizeHandoffKit,
    SearchOperatorCompareScreen,
    SearchOperatorScreen,
    SearchPlatformContractBundle,
    SearchPlatformCommandBundle,
    SearchPlatformCommandSpec,
    SearchPlatformFixturePublicationPacket,
    SearchPlatformRegressionEntrypoint,
    SearchPlatformValidatorPacket,
    SearchPublishedFixtureRow,
    SearchReferenceFixtureBundle,
    SearchDeploymentRegressionCheck,
    SearchDeploymentRegressionHarness,
    SearchRepairLoopDeploymentReadinessKit,
    SearchOptimizeRLHandoffRegression,
    SearchRewardSignal,
    SearchRLHandoffKit,
    SearchRLConsumerExpansionPacket,
    SearchRLReplayParityKit,
    SearchStageCCloseoutPacket,
    SearchStageCConsumerConvergencePacket,
    SearchStageCOptimizeConsumerizationPacket,
    SearchStageCRLConsumerizationPacket,
    SearchStageCRepairLoopConsumerLanePacket,
    SearchStageCRepairLoopContainmentPacket,
    SearchSelectiveResearchControlPacket,
    SearchExecutionBudgetCell,
    SearchSlicePackagingHygieneNote,
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
    build_default_search_study_kits,
    build_default_search_study_registry,
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
    build_search_assessment_chain_view,
    build_search_consumer_seam_diagnostic,
    build_search_cross_system_artifact_integrity_packet,
    build_search_cross_system_deployment_readiness_packet,
    build_search_cross_system_handoff_contract,
    build_search_cross_execution_harness_comparison_packet,
    build_search_cross_execution_matrix_packet,
    build_search_cross_execution_optimize_execution_packet,
    build_search_cross_execution_optimize_regression_ledger_packet,
    build_search_cross_execution_rl_execution_packet,
    build_search_cross_execution_rl_regression_ledger_packet,
    build_search_cross_execution_divergence_ledger_packet,
    build_search_cross_execution_repeated_run_summary_packet,
    build_search_cross_execution_closeout_packet,
    build_search_cross_execution_next_locus_packet,
    build_search_live_harness_command_matrix_packet,
    build_search_live_harness_smoke_packet,
    build_search_live_optimize_execution_packet,
    build_search_live_rl_execution_packet,
    build_search_live_consumer_convergence_packet,
    build_search_live_closeout_packet,
    build_search_atp_boundary_control_packet,
    build_search_atp_boundary_control_v2,
    build_search_atp_bundle_publication_packet,
    build_search_atp_consumer_handoff_stabilization_packet,
    build_search_atp_domain_pilot,
    build_search_atp_deployment_readiness_kit,
    build_search_atp_operator_proof_triage_packet,
    build_search_atp_operator_triage_kit,
    build_search_atp_production_lane_packet,
    build_search_atp_stage_b_closeout_packet,
    build_search_stage_c_optimize_consumerization_packet,
    build_search_stage_c_repair_loop_consumer_lane_packet,
    build_search_stage_c_consumer_convergence_packet,
    build_search_stage_c_closeout_packet,
    build_search_stage_c_repair_loop_containment_packet,
    build_search_stage_c_rl_consumerization_packet,
    build_search_ctrees_boundary_canary,
    build_search_domain_pilot_friction_summary,
    build_search_general_agent_control_packet,
    build_search_lineage_view,
    build_search_optimize_comparison_kit,
    build_search_optimize_consumer_expansion,
    build_search_optimize_handoff_kit,
    build_search_optimize_rl_handoff_regression,
    build_search_operator_compare_screen,
    build_search_operator_screen,
    build_search_replay_export_summary,
    build_search_platform_command_bundle,
    build_search_platform_contract_bundle,
    build_search_platform_contract_publication_packet,
    build_search_platform_fixture_publication_packet,
    build_search_platform_reference_fixture_bundle,
    build_search_platform_regression_entrypoint,
    build_search_platform_regression_harness,
    build_search_platform_validator_packet,
    build_search_repair_loop_domain_pilot,
    build_search_repair_loop_deployment_readiness_kit,
    build_search_rl_handoff_kit,
    build_search_rl_consumer_expansion,
    build_search_rl_replay_parity_kit,
    build_search_slice_packaging_hygiene_note,
    build_search_tool_planning_tree_control_packet,
    build_search_trajectory_export_example,
    build_search_trajectory_export_example_payload,
    build_stateful_branch_search_example,
    build_stateful_branch_search_example_payload,
    build_typed_compaction_registry_example,
    build_typed_compaction_registry_example_payload,
    build_verifier_guided_pressure_cell,
    compare_search_study_runs,
    compute_fidelity_metrics,
    diff_search_replay_export_summaries,
    export_search_trajectory,
    inspect_search_study,
    render_search_operator_compare_screen_text,
    render_search_operator_screen_text,
    run_search_study,
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


def test_default_search_study_registry_lists_canonical_families() -> None:
    registry = build_default_search_study_registry()
    entries = registry.list_entries()
    keys = [entry.study_key for entry in entries]
    assert keys == sorted(keys)
    assert "dag_v3_rsa_replication" in keys
    assert "dag_replication_v1_got_sorting" in keys
    assert "dag_v4_bavt" in keys
    assert "dag_v4_team_of_thoughts" in keys
    assert "dag_v4_final_adjudication" in keys
    assert "search_platform_contract_publication" in keys
    assert "search_platform_fixture_publication" in keys
    assert "search_platform_regression_harness" in keys
    assert "search_platform_regression_entrypoint" in keys
    assert "search_platform_command_bundle" in keys
    assert "search_platform_validator" in keys
    assert "search_atp_bundle_publication" in keys
    assert "search_atp_production_lane" in keys
    assert "search_atp_operator_proof_triage" in keys
    assert "search_atp_consumer_handoff_stabilization" in keys
    assert "search_atp_stage_b_closeout" in keys
    assert "search_stage_c_optimize_consumerization" in keys
    assert "search_stage_c_rl_consumerization" in keys
    assert "search_stage_c_repair_loop_consumer_lane" in keys
    assert "search_stage_c_consumer_convergence" in keys
    assert "search_stage_c_repair_loop_containment" in keys
    assert "search_stage_c_closeout" in keys
    assert "search_cross_execution_matrix" in keys
    assert "search_cross_execution_harness_comparison" in keys
    assert "search_cross_execution_optimize_execution" in keys
    assert "search_cross_execution_optimize_regression_ledger" in keys
    assert "search_cross_execution_rl_execution" in keys
    assert "search_cross_execution_rl_regression_ledger" in keys
    assert "search_cross_execution_divergence_ledger" in keys
    assert "search_cross_execution_repeated_run_summary" in keys
    assert "search_cross_execution_closeout" in keys
    assert "search_cross_execution_next_locus" in keys
    assert "search_live_harness_command_matrix" in keys
    assert "search_live_harness_smoke" in keys
    assert "dag_v5_atp_domain_pilot" in keys
    assert "dag_v5_repair_loop_domain_pilot" in keys


def test_run_search_study_spec_mode_has_small_summaries_and_no_payload() -> None:
    result = run_search_study("dag_replication_v1_got_sorting", mode="spec")
    assert result.payload is None
    assert result.summary_json["study_key"] == "dag_replication_v1_got_sorting"
    assert result.summary_json["mode"] == "spec"
    assert result.summary_json["artifact_refs"]
    assert "study_key: dag_replication_v1_got_sorting" in result.summary_txt
    assert "mode: spec" in result.summary_txt


def test_run_search_study_debug_mode_includes_payload_and_inspection() -> None:
    result = run_search_study("dag_v4_bavt", mode="debug")
    assert result.payload is not None
    inspection = result.inspect()
    assert inspection["payload_available"] is True
    assert inspection["summary_json"]["study_key"] == "dag_v4_bavt"
    assert "frontier_policy_audit" in inspection["packet_keys"]


def test_inspect_search_study_returns_registry_and_summary_views() -> None:
    inspection = inspect_search_study("dag_v4_team_of_thoughts", mode="spec")
    assert inspection["registry_entry"]["study_key"] == "dag_v4_team_of_thoughts"
    assert inspection["summary_json"]["packet_family"] == "dag_v4_team_of_thoughts"
    assert inspection["payload_available"] is False
    assert inspection["lineage_view"]["recipe_kind"] == "team_of_thoughts_layered_packet"
    assert inspection["assessment_chain_view"]["topology_class"] == "H"
    assert inspection["replay_export_summary"]["search_id"] == inspection["lineage_view"]["source_id"]


def test_compare_search_study_runs_reports_shared_and_distinct_surface() -> None:
    left = run_search_study("dag_v4_bavt", mode="spec")
    right = run_search_study("dag_v4_team_of_thoughts", mode="debug")
    comparison = compare_search_study_runs(left, right)
    assert comparison["left_study_key"] == "dag_v4_bavt"
    assert comparison["right_study_key"] == "dag_v4_team_of_thoughts"
    assert comparison["left_mode"] == "spec"
    assert comparison["right_mode"] == "debug"
    assert isinstance(comparison["shared_artifact_refs"], list)
    assert comparison["left_top_level_metrics"]["topology_class"] == "F"
    assert comparison["right_top_level_metrics"]["topology_class"] == "H"


def test_search_study_open_artifact_returns_known_ref_and_rejects_unknown() -> None:
    result = run_search_study("dag_replication_v1_got_sorting", mode="spec")
    artifact_ref = result.open_artifact()
    assert artifact_ref in result.artifact_refs
    try:
        result.open_artifact("artifacts/search/unknown/not_present.json")
    except KeyError as exc:
        assert "not present" in str(exc)
    else:
        raise AssertionError("expected unknown artifact ref to raise KeyError")


def test_build_search_operator_screen_projects_study_surface_for_operator_consumers() -> None:
    screen = build_search_operator_screen("dag_v4_team_of_thoughts", mode="debug")

    assert isinstance(screen, SearchOperatorScreen)
    assert screen.study_key == "dag_v4_team_of_thoughts"
    assert screen.mode == "debug"
    assert any(panel.panel_id == "overview" for panel in screen.panels)
    assert any(panel.panel_id == "controls" for panel in screen.panels)
    assert any(panel.panel_id == "inspection" for panel in screen.panels)
    assert "dag_v4_team_of_thoughts" in render_search_operator_screen_text(screen)


def test_build_search_operator_screen_surfaces_domain_friction_focus_panel() -> None:
    screen = build_search_operator_screen("dag_v5_atp_domain_pilot", mode="spec")

    focus_panel = next(panel for panel in screen.panels if panel.panel_id == "focus")
    assert isinstance(screen, SearchOperatorScreen)
    assert any("domain_friction:" in line for line in focus_panel.lines)
    assert any(command.startswith("run_search_study(") for command in screen.commands)


def test_build_search_operator_compare_screen_projects_compare_and_replay_diff() -> None:
    screen = build_search_operator_compare_screen(
        "dag_v4_bavt",
        "dag_v4_team_of_thoughts",
        left_mode="spec",
        right_mode="debug",
    )

    assert isinstance(screen, SearchOperatorCompareScreen)
    assert screen.left_study_key == "dag_v4_bavt"
    assert screen.right_study_key == "dag_v4_team_of_thoughts"
    assert any(panel.panel_id == "comparison" for panel in screen.panels)
    assert any(panel.panel_id == "replay_export_diff" for panel in screen.panels)
    assert "dag_v4_bavt:spec vs dag_v4_team_of_thoughts:debug" in render_search_operator_compare_screen_text(screen)


def test_selective_research_control_packets_stay_non_kernel_and_study_runnable() -> None:
    tool_control = build_search_tool_planning_tree_control_packet()
    general_control = build_search_general_agent_control_packet()

    tool_packet = tool_control["control_packet"]
    general_packet = general_control["control_packet"]

    assert isinstance(tool_packet, SearchSelectiveResearchControlPacket)
    assert isinstance(general_packet, SearchSelectiveResearchControlPacket)
    assert tool_packet.control_family == "tool_planning_tree"
    assert general_packet.control_family == "general_agent_control"
    assert tool_packet.kernel_change_required is False
    assert general_packet.kernel_change_required is False
    assert tool_control["decision"] == "bounded_control_family_only"
    assert general_control["decision"] == "control_lens_only"

    registry = build_default_search_study_registry()
    keys = [entry.study_key for entry in registry.list_entries()]
    assert "dag_v5_tool_planning_tree_control" in keys
    assert "dag_v5_general_agent_control" in keys

    tool_result = run_search_study("dag_v5_tool_planning_tree_control", mode="spec")
    general_result = run_search_study("dag_v5_general_agent_control", mode="debug")
    assert tool_result.summary_json["packet_family"] == "dag_v5_tool_planning_tree_control"
    assert general_result.summary_json["packet_family"] == "dag_v5_general_agent_control"
    assert general_result.payload is not None
    assert tool_result.summary_json["top_level_metrics"]["decision"] == "bounded_control_family_only"
    assert general_result.summary_json["top_level_metrics"]["decision"] == "control_lens_only"


def test_build_search_atp_boundary_control_packet_keeps_atp_semantics_out_of_kernel() -> None:
    packet = build_search_atp_boundary_control_packet()

    assert isinstance(packet, SearchDomainBoundaryControlPacket)
    assert packet.domain_kind == "atp"
    assert packet.ontology_blending_forbidden is True
    assert "dag_replication_v1_tot_game24" in packet.source_study_keys
    assert "dag_v4_team_of_thoughts" in packet.source_study_keys
    assert "exact_verifier_ground_truth" in packet.control_requirements
    assert "proof_state_kernel_truth_in_search" in packet.forbidden_domain_semantics
    assert "scripts/run_bb_atp_adapter_slice_v1.py" in packet.external_surface_refs


def test_build_search_atp_domain_pilot_stays_adapter_local_and_study_runnable() -> None:
    example = build_search_atp_domain_pilot()
    pilot = example["pilot_packet"]

    assert isinstance(pilot, SearchDomainPilotPacket)
    assert pilot.domain_kind == "atp"
    assert pilot.friction_locus == "adapter_and_harness_local_only"
    assert pilot.kernel_change_required is False
    assert pilot.ontology_blending_detected is False
    assert "artifacts/benchmarks/hilbert_comparison_packs_v1/*/cross_system_manifest.json" in pilot.expected_artifact_refs

    result = run_search_study("dag_v5_atp_domain_pilot", mode="spec")
    assert result.summary_json["study_key"] == "dag_v5_atp_domain_pilot"
    assert result.summary_json["packet_family"] == "dag_v5_atp_domain_pilot"
    assert result.summary_json["artifact_refs"]


def test_build_search_repair_loop_domain_pilot_stays_workspace_local_and_bounded() -> None:
    example = build_search_repair_loop_domain_pilot()
    pilot = example["pilot_packet"]

    assert isinstance(pilot, SearchDomainPilotPacket)
    assert pilot.domain_kind == "repair_loop"
    assert pilot.friction_locus == "patching_and_workspace_local_only"
    assert pilot.kernel_change_required is False
    assert pilot.ontology_blending_detected is False
    assert "agentic_coder_prototype/conductor/patching.py" in pilot.adapter_surface_refs
    assert "artifacts/search/search.replication_v1.codetree_patch/final.json" in pilot.expected_artifact_refs

    result = run_search_study("dag_v5_repair_loop_domain_pilot", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "dag_v5_repair_loop_domain_pilot"


def test_build_search_domain_pilot_friction_summary_keeps_domain_work_out_of_kernel_review() -> None:
    summary = build_search_domain_pilot_friction_summary()

    assert isinstance(summary, SearchDomainFrictionSummary)
    assert summary.domain_kinds == ["atp", "repair_loop"]
    assert summary.friction_loci == ["adapter_and_harness_local_only", "patching_and_workspace_local_only"]
    assert summary.ontology_blending_detected is False
    assert summary.repeated_shape_gap_detected is False
    assert summary.next_decision == "continue_domain_pilots_without_kernel_review"


def test_build_search_cross_system_handoff_contract_unifies_atp_optimize_and_rl_contracts() -> None:
    contract = build_search_cross_system_handoff_contract()

    assert isinstance(contract, SearchCrossSystemHandoffContract)
    assert contract.contract_id == "search.platform.cross_system_handoff_contract.v1"
    assert contract.source_domain == "atp_deployment_adjacent"
    assert contract.target_consumers == ("optimize", "rl", "atp_operator")
    assert "assessment_lineage_visibility" in contract.preserved_fields
    assert "evaluation_pack_identity" in contract.preserved_fields
    assert "comparison_packet_handoff" in contract.seam_labels
    assert "trajectory_projection_handoff" in contract.seam_labels
    assert contract.final_classification == "stable_helper_and_consumer_contract"


def test_build_search_cross_system_artifact_integrity_packet_stays_bounded_and_runnable() -> None:
    packet = build_search_cross_system_artifact_integrity_packet()

    assert isinstance(packet, SearchCrossSystemArtifactIntegrityPacket)
    assert packet.contract_id == "search.platform.cross_system_handoff_contract.v1"
    assert len(packet.rows) == 3
    assert all(isinstance(row, SearchCrossSystemArtifactIntegrityRow) for row in packet.rows)
    assert packet.stable_lanes == ("atp", "optimize", "rl")
    assert packet.unstable_lanes == ()
    assert packet.replay_or_export_corruption_detected is False
    assert packet.final_classification == "stable_with_bounded_local_friction"

    result = run_search_study("dag_v6_cross_system_deployment_readiness", mode="spec")
    assert result.summary_json["study_key"] == "dag_v6_cross_system_deployment_readiness"
    assert result.summary_json["packet_family"] == "dag_v6_cross_system_deployment_readiness"
    assert result.summary_json["artifact_refs"]


def test_build_search_atp_operator_triage_kit_projects_existing_operator_surface() -> None:
    kit = build_search_atp_operator_triage_kit()

    assert isinstance(kit, SearchATPOperatorTriageKit)
    assert kit.kit_id == "search.domain.atp.operator_triage_kit.v1"
    assert kit.study_key == "dag_v5_atp_domain_pilot"
    assert "run_search_study('dag_v5_atp_domain_pilot'" not in kit.primary_commands
    assert any(command.startswith("run_search_study(") for command in kit.primary_commands)
    assert "boundary_control_integrity" in kit.triage_focus
    assert kit.kernel_change_required is False
    assert kit.final_classification == "operator_triage_ready_without_kernel_change"

    result = run_search_study("dag_v6_atp_operator_triage", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "dag_v6_atp_operator_triage"


def test_build_search_atp_boundary_control_v2_promotes_v2_artifact_contracts_without_ontology_blending() -> None:
    boundary = build_search_atp_boundary_control_v2()

    assert isinstance(boundary, SearchDomainBoundaryControlPacket)
    assert boundary.packet_id == "search.domain.atp.boundary_control.v2"
    assert "dag_v6_cross_system_deployment_readiness" in boundary.source_study_keys
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in boundary.benchmark_manifest_refs
    assert "scripts/build_hilbert_comparison_packs_v2.py" in boundary.external_surface_refs
    assert "cross_system_handoff_contract_visibility" in boundary.preserved_dag_truth
    assert "proof_bundle_storage_visibility" in boundary.control_requirements
    assert boundary.ontology_blending_forbidden is True

    result = run_search_study("dag_v6_atp_boundary_control_v2", mode="spec")
    assert result.summary_json["study_key"] == "dag_v6_atp_boundary_control_v2"


def test_build_search_atp_deployment_readiness_kit_keeps_atp_tranche_adapter_local_and_runnable() -> None:
    kit = build_search_atp_deployment_readiness_kit()

    assert isinstance(kit, SearchATPDeploymentReadinessKit)
    assert kit.kit_id == "search.domain.atp.deployment_readiness.v2"
    assert kit.boundary_control_id == "search.domain.atp.boundary_control.v2"
    assert kit.operator_triage_kit_id == "search.domain.atp.operator_triage_kit.v1"
    assert "bundle_manifest_identity" in kit.readiness_checks
    assert "cross_system_validation_visibility" in kit.deferred_checks
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2" in kit.expected_artifact_roots
    assert "agentic_coder_prototype/api/cli_bridge/atp_router.py" in kit.adapter_surface_refs
    assert kit.kernel_change_required is False
    assert kit.final_decision == "continue_atp_deployment_without_kernel_review"

    result = run_search_study("dag_v6_atp_deployment_readiness", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "dag_v6_atp_deployment_readiness"
    assert result.summary_json["packet_family"] == "dag_v6_atp_deployment_readiness"


def test_build_search_atp_bundle_publication_packet_promotes_bundle_and_baseline_visibility() -> None:
    packet = build_search_atp_bundle_publication_packet()

    assert isinstance(packet, SearchATPBundlePublicationPacket)
    assert packet.packet_id == "search.domain.atp.bundle_publication.v1"
    assert packet.boundary_control_id == "search.domain.atp.boundary_control.v2"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.bundle_manifest_refs
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json" in packet.baseline_refs
    assert "artifacts/benchmarks/cross_system/bb_atp/proofs" in packet.proof_bundle_refs
    assert packet.publishable is True
    assert packet.final_decision == "publish_atp_bundles_and_baselines_as_stage_b_source_family"
    assert packet.dominant_locus == "adapter_local"

    result = run_search_study("search_atp_bundle_publication", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_atp_bundle_publication"
    assert result.summary_json["packet_family"] == "search_atp_bundle_publication.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision


def test_build_search_atp_production_lane_packet_closes_visibility_gap_without_kernel_review() -> None:
    packet = build_search_atp_production_lane_packet()

    assert isinstance(packet, SearchATPProductionLanePacket)
    assert packet.packet_id == "search.domain.atp.production_lane.v1"
    assert packet.readiness_kit_id == "search.domain.atp.deployment_readiness.v2"
    assert packet.bundle_publication_packet_id == "search.domain.atp.bundle_publication.v1"
    assert packet.handoff_contract_id == "search.platform.cross_system_handoff_contract.v1"
    assert packet.operator_triage_kit_id == "search.domain.atp.operator_triage_kit.v1"
    assert packet.handoff_regression_id == "search.consumer.optimize_rl_handoff_regression.v2"
    assert "cross_system_validation_visibility" in packet.resolved_checks
    assert packet.remaining_deferred_checks == ()
    assert packet.stable_consumers == ("atp_operator", "optimize", "rl")
    assert packet.final_decision == "continue_atp_production_lane_and_treat_atp_as_stage_b_source_family"
    assert packet.dominant_locus == "adapter_local"

    result = run_search_study("search_atp_production_lane", mode="spec")
    assert result.summary_json["study_key"] == "search_atp_production_lane"
    assert result.summary_json["packet_family"] == "search_atp_production_lane.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "adapter_local"


def test_build_search_atp_operator_proof_triage_packet_anchors_real_proof_bundles_and_commands() -> None:
    packet = build_search_atp_operator_proof_triage_packet()

    assert isinstance(packet, SearchATPOperatorProofTriagePacket)
    assert packet.packet_id == "search.domain.atp.operator_proof_triage.v1"
    assert packet.production_lane_id == "search.domain.atp.production_lane.v1"
    assert packet.triage_kit_id == "search.domain.atp.operator_triage_kit.v1"
    assert "artifacts/benchmarks/cross_system/bb_atp/proofs" in packet.proof_bundle_refs
    assert "cross_system_handoff_visibility" in packet.triage_focus
    assert packet.handoff_ready_consumers == ("atp_operator", "optimize", "rl")
    assert packet.final_decision == "use_operator_triage_on_real_atp_proof_bundles_before_consumer_expansion"
    assert packet.dominant_locus == "operator_local"

    result = run_search_study("search_atp_operator_proof_triage", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_atp_operator_proof_triage"
    assert result.summary_json["packet_family"] == "search_atp_operator_proof_triage.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "operator_local"


def test_build_search_atp_consumer_handoff_stabilization_packet_keeps_stage_b_handoffs_stable() -> None:
    packet = build_search_atp_consumer_handoff_stabilization_packet()

    assert isinstance(packet, SearchATPConsumerHandoffStabilizationPacket)
    assert packet.packet_id == "search.domain.atp.consumer_handoff_stabilization.v1"
    assert packet.production_lane_id == "search.domain.atp.production_lane.v1"
    assert packet.handoff_contract_id == "search.platform.cross_system_handoff_contract.v1"
    assert packet.handoff_regression_id == "search.consumer.optimize_rl_handoff_regression.v2"
    assert "assessment_lineage_visibility" in packet.preserved_field_union
    assert "trajectory_projection_handoff" in packet.stabilized_handoff_checks
    assert packet.stable_consumers == ("atp_operator", "optimize", "rl")
    assert packet.final_decision == "treat_atp_as_stable_upstream_source_for_stage_c_consumers"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_atp_consumer_handoff_stabilization", mode="spec")
    assert result.summary_json["study_key"] == "search_atp_consumer_handoff_stabilization"
    assert result.summary_json["packet_family"] == "search_atp_consumer_handoff_stabilization.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "consumer_local"


def test_build_search_atp_stage_b_closeout_packet_blesses_atp_for_stage_c_consumers() -> None:
    packet = build_search_atp_stage_b_closeout_packet()

    assert isinstance(packet, SearchATPStageBCloseoutPacket)
    assert packet.packet_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.bundle_publication_packet_id == "search.domain.atp.bundle_publication.v1"
    assert packet.production_lane_id == "search.domain.atp.production_lane.v1"
    assert packet.operator_proof_triage_id == "search.domain.atp.operator_proof_triage.v1"
    assert packet.consumer_handoff_stabilization_id == "search.domain.atp.consumer_handoff_stabilization.v1"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.source_family_refs
    assert packet.stage_c_ready_consumers == ("atp_operator", "optimize", "rl")
    assert "consumer_handoff_stabilized" in packet.exit_checks
    assert packet.remaining_deferred_checks == ()
    assert packet.final_decision == "exit_stage_b_and_use_atp_as_stage_c_upstream_source_family"
    assert packet.dominant_locus == "adapter_local"

    result = run_search_study("search_atp_stage_b_closeout", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_atp_stage_b_closeout"
    assert result.summary_json["packet_family"] == "search_atp_stage_b_closeout.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "adapter_local"


def test_build_search_stage_c_optimize_consumerization_packet_uses_stage_b_source_family() -> None:
    packet = build_search_stage_c_optimize_consumerization_packet()

    assert isinstance(packet, SearchStageCOptimizeConsumerizationPacket)
    assert packet.packet_id == "search.platform.stage_c.optimize_consumerization.v1"
    assert packet.stage_b_closeout_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.optimize_handoff_kit_id == "search.consumer.optimize.handoff_kit.v1"
    assert packet.optimize_comparison_kit_id == "search.consumer.optimize.comparison_kit.v1"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.source_family_refs
    assert "assessment_lineage_visibility" in packet.required_contract_fields
    assert packet.source_consumers == ("atp_operator", "optimize", "rl")
    assert packet.final_decision == "continue_optimize_consumerization_over_published_atp_source_family"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_stage_c_optimize_consumerization", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_stage_c_optimize_consumerization"
    assert result.summary_json["packet_family"] == "search_stage_c_optimize_consumerization.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "consumer_local"


def test_build_search_stage_c_rl_consumerization_packet_uses_stage_b_source_family() -> None:
    packet = build_search_stage_c_rl_consumerization_packet()

    assert isinstance(packet, SearchStageCRLConsumerizationPacket)
    assert packet.packet_id == "search.platform.stage_c.rl_consumerization.v1"
    assert packet.stage_b_closeout_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.rl_handoff_kit_id == "search.consumer.rl.handoff_kit.v1"
    assert packet.rl_replay_parity_kit_id == "search.consumer.rl.replay_parity_kit.v1"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.source_family_refs
    assert "assessment_lineage_visibility" in packet.required_contract_fields
    assert packet.source_consumers == ("atp_operator", "optimize", "rl")
    assert packet.final_decision == "continue_rl_consumerization_over_published_atp_source_family"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_stage_c_rl_consumerization", mode="spec")
    assert result.summary_json["study_key"] == "search_stage_c_rl_consumerization"
    assert result.summary_json["packet_family"] == "search_stage_c_rl_consumerization.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "consumer_local"


def test_build_search_stage_c_repair_loop_consumer_lane_packet_keeps_repair_bounded() -> None:
    packet = build_search_stage_c_repair_loop_consumer_lane_packet()

    assert isinstance(packet, SearchStageCRepairLoopConsumerLanePacket)
    assert packet.packet_id == "search.platform.stage_c.repair_loop_consumer_lane.v1"
    assert packet.stage_b_closeout_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.repair_readiness_kit_id == "search.domain.repair_loop.deployment_readiness.v1"
    assert "artifacts/search/search.replication_v1.codetree_patch" in packet.expected_artifact_roots
    assert "repair_lane_stays_bounded" in packet.bounded_lane_checks
    assert packet.source_consumers == ("atp_operator", "optimize", "rl")
    assert packet.final_decision == "continue_bounded_repair_loop_consumer_lane_over_published_atp_source_family"
    assert packet.dominant_locus == "adapter_local"

    result = run_search_study("search_stage_c_repair_loop_consumer_lane", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_stage_c_repair_loop_consumer_lane"
    assert result.summary_json["packet_family"] == "search_stage_c_repair_loop_consumer_lane.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "adapter_local"


def test_build_search_stage_c_consumer_convergence_packet_preserves_cross_consumer_contracts() -> None:
    packet = build_search_stage_c_consumer_convergence_packet()

    assert isinstance(packet, SearchStageCConsumerConvergencePacket)
    assert packet.packet_id == "search.platform.stage_c.consumer_convergence.v1"
    assert packet.optimize_consumerization_id == "search.platform.stage_c.optimize_consumerization.v1"
    assert packet.rl_consumerization_id == "search.platform.stage_c.rl_consumerization.v1"
    assert packet.seam_diagnostic_id == "search.consumer.seam_diagnostic.v1"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.shared_source_family_refs
    assert packet.shared_source_consumers == ("atp_operator", "optimize", "rl")
    assert "assessment_lineage_visibility" in packet.converged_contract_fields
    assert packet.consumer_loci == ("consumer_local",)
    assert "cross_consumer_seam_read_preserved" in packet.convergence_checks
    assert packet.final_decision == "continue_cross_consumer_convergence_over_published_atp_source_family"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_stage_c_consumer_convergence", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_stage_c_consumer_convergence"
    assert result.summary_json["packet_family"] == "search_stage_c_consumer_convergence.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "consumer_local"


def test_build_search_stage_c_repair_loop_containment_packet_keeps_repair_from_semantic_expansion() -> None:
    packet = build_search_stage_c_repair_loop_containment_packet()

    assert isinstance(packet, SearchStageCRepairLoopContainmentPacket)
    assert packet.packet_id == "search.platform.stage_c.repair_loop_containment.v1"
    assert packet.repair_loop_consumer_lane_id == "search.platform.stage_c.repair_loop_consumer_lane.v1"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.source_family_refs
    assert "repair_lane_stays_bounded" in packet.bounded_lane_checks
    assert "dag_kernel_patch_semantics_import" in packet.excluded_semantic_imports
    assert packet.containment_status == "bounded_and_adapter_local_only"
    assert packet.final_decision == "keep_repair_loop_consumer_lane_bounded_and_out_of_platform_semantic_expansion"
    assert packet.dominant_locus == "adapter_local"

    result = run_search_study("search_stage_c_repair_loop_containment", mode="spec")
    assert result.summary_json["study_key"] == "search_stage_c_repair_loop_containment"
    assert result.summary_json["packet_family"] == "search_stage_c_repair_loop_containment.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "adapter_local"


def test_build_search_stage_c_closeout_packet_exits_stage_c_cleanly() -> None:
    packet = build_search_stage_c_closeout_packet()

    assert isinstance(packet, SearchStageCCloseoutPacket)
    assert packet.packet_id == "search.platform.stage_c.closeout.v1"
    assert packet.optimize_consumerization_id == "search.platform.stage_c.optimize_consumerization.v1"
    assert packet.rl_consumerization_id == "search.platform.stage_c.rl_consumerization.v1"
    assert packet.repair_loop_consumer_lane_id == "search.platform.stage_c.repair_loop_consumer_lane.v1"
    assert packet.consumer_convergence_id == "search.platform.stage_c.consumer_convergence.v1"
    assert packet.repair_loop_containment_id == "search.platform.stage_c.repair_loop_containment.v1"
    assert "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json" in packet.source_family_refs
    assert packet.accepted_source_consumers == ("atp_operator", "optimize", "rl")
    assert "cross_consumer_convergence_explicit" in packet.exit_checks
    assert packet.remaining_deferred_checks == ()
    assert packet.final_decision == "exit_stage_c_and_treat_published_atp_backed_consumers_as_stable_platform_surface"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_stage_c_closeout", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_stage_c_closeout"
    assert result.summary_json["packet_family"] == "search_stage_c_closeout.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "consumer_local"



def test_build_search_live_harness_command_matrix_packet_records_real_invocation_contracts() -> None:
    packet = build_search_live_harness_command_matrix_packet()

    assert isinstance(packet, SearchLiveHarnessCommandMatrixPacket)
    assert packet.packet_id == "search.platform.phase3.live_harness_command_matrix.v1"
    assert packet.source_family_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.stage_c_closeout_id == "search.platform.stage_c.closeout.v1"
    assert len(packet.command_specs) == 4
    assert all(isinstance(spec, SearchLiveCommandSpec) for spec in packet.command_specs)
    assert packet.command_specs[0].command_id == "build_hilbert_comparison_packs_v2_smoke"
    assert packet.command_specs[0].argv[1] == "scripts/build_hilbert_comparison_packs_v2.py"
    assert packet.command_specs[-1].command_id == "run_bb_atp_adapter_slice_v1_help_probe"
    assert packet.final_decision == "execute_harness_live_smoke_now"

    result = run_search_study("search_live_harness_command_matrix", mode="debug")
    assert result.summary_json["study_key"] == "search_live_harness_command_matrix"
    assert result.summary_json["packet_family"] == "search_live_harness_command_matrix.v1"



def test_build_search_live_harness_smoke_packet_classifies_repaired_positive_paths() -> None:
    packet = build_search_live_harness_smoke_packet()

    assert isinstance(packet, SearchLiveHarnessSmokePacket)
    assert packet.packet_id == "search.platform.phase3.live_harness_smoke.v1"
    assert packet.command_matrix_id == "search.platform.phase3.live_harness_command_matrix.v1"
    assert len(packet.observations) == 4
    assert all(isinstance(obs, SearchLiveCommandObservation) for obs in packet.observations)
    assert packet.positive_path_ids == (
        "build_hilbert_comparison_packs_v2_smoke",
        "build_hilbert_bb_comparison_bundle_v1_probe",
        "build_atp_hilbert_canonical_baselines_v1_bounded_publish",
        "run_bb_atp_adapter_slice_v1_help_probe",
    )
    assert packet.failure_path_ids == ()
    assert packet.final_decision == "proceed_to_optimize_live_and_rl_live_widening"
    assert packet.dominant_locus == "harness_local"

    result = run_search_study("search_live_harness_smoke", mode="spec")
    assert result.summary_json["study_key"] == "search_live_harness_smoke"
    assert result.summary_json["packet_family"] == "search_live_harness_smoke.v1"


def test_build_search_live_optimize_execution_packet_records_repaired_atp_readiness() -> None:
    packet = build_search_live_optimize_execution_packet()

    assert isinstance(packet, SearchLiveOptimizeExecutionPacket)
    assert packet.packet_id == "search.platform.phase3.live_optimize_execution.v1"
    assert packet.smoke_packet_id == "search.platform.phase3.live_harness_smoke.v1"
    assert packet.source_family_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.optimize_comparison_id == "comparison.next_frontier.cohort.dag_packet_compare.v1"
    assert packet.optimize_live_cell_id == "optimize.next_frontier.live_cell.dag_packets.v1"
    assert packet.budget_cell_id == "search.cross_execution.cell.audit_small.v1"
    assert "canonical_baseline_bounded_publication_available" in packet.readiness_rows
    assert packet.blocker_rows == ()
    assert packet.final_decision == "execute_bounded_optimize_live_on_published_atp_artifacts"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_live_optimize_execution", mode="spec")
    assert result.summary_json["study_key"] == "search_live_optimize_execution"
    assert result.summary_json["packet_family"] == "search_live_optimize_execution.v1"


def test_build_search_live_rl_execution_packet_records_repaired_atp_readiness() -> None:
    packet = build_search_live_rl_execution_packet()

    assert isinstance(packet, SearchLiveRLExecutionPacket)
    assert packet.packet_id == "search.platform.phase3.live_rl_execution.v1"
    assert packet.smoke_packet_id == "search.platform.phase3.live_harness_smoke.v1"
    assert packet.source_family_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.rl_trainer_export_manifest_id == "bb.rl.next_frontier.export_manifest.trainer.v1"
    assert packet.rl_parity_live_manifest_id == "bb.rl.next_frontier.export_manifest.parity.live.v1"
    assert packet.rl_parity_replay_manifest_id == "bb.rl.next_frontier.export_manifest.parity.replay.v1"
    assert packet.budget_cell_id == "search.cross_execution.cell.audit_small.v1"
    assert "rl_manifest_family_known" in packet.readiness_rows
    assert packet.blocker_rows == ()
    assert packet.final_decision == "execute_bounded_rl_live_on_published_atp_artifacts"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_live_rl_execution", mode="spec")
    assert result.summary_json["study_key"] == "search_live_rl_execution"
    assert result.summary_json["packet_family"] == "search_live_rl_execution.v1"


def test_build_search_live_consumer_convergence_packet_closes_paired_live_lanes() -> None:
    packet = build_search_live_consumer_convergence_packet()

    assert isinstance(packet, SearchLiveConsumerConvergencePacket)
    assert packet.packet_id == "search.platform.phase3.live_consumer_convergence.v1"
    assert packet.optimize_execution_id == "search.platform.phase3.live_optimize_execution.v1"
    assert packet.rl_execution_id == "search.platform.phase3.live_rl_execution.v1"
    assert packet.shared_source_family_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.shared_budget_cell_id == "search.cross_execution.cell.audit_small.v1"
    assert "paired_consumer_readiness_green" in packet.convergence_rows
    assert "shared_bounded_canonical_publication" in packet.convergence_rows
    assert packet.remaining_blocker_rows == ()
    assert packet.final_decision == "close_live_consumer_pair_with_bounded_publication_green"
    assert packet.dominant_locus == "consumer_local"

    result = run_search_study("search_live_consumer_convergence", mode="spec")
    assert result.summary_json["study_key"] == "search_live_consumer_convergence"
    assert result.summary_json["packet_family"] == "search_live_consumer_convergence.v1"


def test_build_search_live_closeout_packet_preserves_platform_follow_on_scope() -> None:
    packet = build_search_live_closeout_packet()

    assert isinstance(packet, SearchLiveCloseoutPacket)
    assert packet.packet_id == "search.platform.phase3.live_closeout.v1"
    assert packet.harness_smoke_id == "search.platform.phase3.live_harness_smoke.v1"
    assert packet.optimize_execution_id == "search.platform.phase3.live_optimize_execution.v1"
    assert packet.rl_execution_id == "search.platform.phase3.live_rl_execution.v1"
    assert packet.consumer_convergence_id == "search.platform.phase3.live_consumer_convergence.v1"
    assert "paired_consumer_convergence_green" in packet.ready_rows
    assert packet.remaining_follow_on_rows == ()
    assert packet.final_decision == "close_live_execution_tranche_with_bounded_publication_green"
    assert packet.dominant_locus == "platform_local"

    result = run_search_study("search_live_closeout", mode="spec")
    assert result.summary_json["study_key"] == "search_live_closeout"
    assert result.summary_json["packet_family"] == "search_live_closeout.v1"


def test_build_search_cross_execution_matrix_packet_locks_first_matched_cells() -> None:
    packet = build_search_cross_execution_matrix_packet()

    assert isinstance(packet, SearchCrossExecutionMatrixPacket)
    assert packet.packet_id == "search.platform.phase2.execution_matrix.v1"
    assert packet.stage_b_closeout_id == "search.domain.atp.stage_b_closeout.v1"
    assert packet.stage_c_closeout_id == "search.platform.stage_c.closeout.v1"
    assert packet.optimize_comparison_id == "comparison.next_frontier.cohort.dag_packet_compare.v1"
    assert packet.optimize_live_cell_id == "optimize.next_frontier.live_cell.dag_packets.v1"
    assert packet.rl_trainer_export_manifest_id == "bb.rl.next_frontier.export_manifest.trainer.v1"
    assert packet.rl_parity_live_manifest_id == "bb.rl.next_frontier.export_manifest.parity.live.v1"
    assert packet.rl_parity_replay_manifest_id == "bb.rl.next_frontier.export_manifest.parity.replay.v1"
    assert len(packet.budget_cells) == 2
    assert all(isinstance(cell, SearchExecutionBudgetCell) for cell in packet.budget_cells)
    assert packet.budget_cells[0].target_consumers == ("optimize", "rl")
    assert packet.final_decision == "execute_matched_optimize_rl_cells_over_published_atp_source_family"

    result = run_search_study("search_cross_execution_matrix", mode="debug")
    assert result.summary_json["study_key"] == "search_cross_execution_matrix"
    assert result.summary_json["packet_family"] == "search_cross_execution_matrix.v1"


def test_build_search_cross_execution_harness_comparison_packet_owns_first_divergence_read() -> None:
    packet = build_search_cross_execution_harness_comparison_packet()

    assert isinstance(packet, SearchCrossExecutionHarnessComparisonPacket)
    assert packet.packet_id == "search.platform.phase2.harness_comparison.v1"
    assert packet.execution_matrix_id == "search.platform.phase2.execution_matrix.v1"
    assert packet.consumer_convergence_id == "search.platform.stage_c.consumer_convergence.v1"
    assert packet.compared_budget_cells == ("search.cross_execution.cell.audit_small.v1", "search.cross_execution.cell.audit_medium.v1")
    assert "matched_budget_cells_explicit" in packet.regression_checks
    assert packet.final_decision == "use_harness_owned_comparison_before_expanding_execution_matrix"

    result = run_search_study("search_cross_execution_harness_comparison", mode="spec")
    assert result.summary_json["study_key"] == "search_cross_execution_harness_comparison"
    assert result.summary_json["packet_family"] == "search_cross_execution_harness_comparison.v1"


def test_build_search_cross_execution_optimize_execution_packet_reuses_matrix_cells() -> None:
    packet = build_search_cross_execution_optimize_execution_packet()

    assert isinstance(packet, SearchCrossExecutionOptimizeExecutionPacket)
    assert packet.packet_id == "search.platform.phase2.optimize_execution.v1"
    assert packet.execution_matrix_id == "search.platform.phase2.execution_matrix.v1"
    assert packet.stage_c_optimize_consumerization_id == "search.platform.stage_c.optimize_consumerization.v1"
    assert packet.harness_comparison_id == "search.platform.phase2.harness_comparison.v1"
    assert len(packet.executed_cell_results) == 2
    assert all(isinstance(result, SearchCrossExecutionCellResult) for result in packet.executed_cell_results)
    assert packet.executed_cell_results[0].cell_id == "search.cross_execution.cell.audit_small.v1"
    assert packet.executed_cell_results[0].optimize_artifact_id == "comparison.next_frontier.cohort.dag_packet_compare.v1"
    assert packet.executed_cell_results[1].cell_id == "search.cross_execution.cell.audit_medium.v1"
    assert packet.executed_cell_results[1].optimize_artifact_id == "optimize.next_frontier.live_cell.dag_packets.v1"
    assert packet.shared_budget_rule == "reuse_matrix_cells_before_any_budget_growth"
    assert packet.final_decision == "repeat_optimize_cells_before_expanding_rl_budget_surface"

    result = run_search_study("search_cross_execution_optimize_execution", mode="debug")
    assert result.summary_json["study_key"] == "search_cross_execution_optimize_execution"
    assert result.summary_json["packet_family"] == "search_cross_execution_optimize_execution.v1"


def test_build_search_cross_execution_optimize_regression_ledger_packet_tracks_first_repeat_rule() -> None:
    packet = build_search_cross_execution_optimize_regression_ledger_packet()

    assert isinstance(packet, SearchCrossExecutionOptimizeRegressionLedgerPacket)
    assert packet.packet_id == "search.platform.phase2.optimize_regression_ledger.v1"
    assert packet.optimize_execution_id == "search.platform.phase2.optimize_execution.v1"
    assert packet.compared_cell_ids == (
        "search.cross_execution.cell.audit_small.v1",
        "search.cross_execution.cell.audit_medium.v1",
    )
    assert "no_unclassified_decision_regression" in packet.regression_guards
    assert packet.repeated_run_rule == "require_same_cell_regression_twice_before_reclassification"
    assert packet.final_decision == "treat_optimize_regression_as_harness_managed_until_cross_consumer_repeat"

    result = run_search_study("search_cross_execution_optimize_regression_ledger", mode="spec")
    assert result.summary_json["study_key"] == "search_cross_execution_optimize_regression_ledger"
    assert result.summary_json["packet_family"] == "search_cross_execution_optimize_regression_ledger.v1"


def test_build_search_cross_execution_rl_execution_packet_reuses_matrix_cells() -> None:
    packet = build_search_cross_execution_rl_execution_packet()

    assert isinstance(packet, SearchCrossExecutionRLExecutionPacket)
    assert packet.packet_id == "search.platform.phase3.rl_execution.v1"
    assert packet.execution_matrix_id == "search.platform.phase2.execution_matrix.v1"
    assert packet.stage_c_rl_consumerization_id == "search.platform.stage_c.rl_consumerization.v1"
    assert packet.harness_comparison_id == "search.platform.phase2.harness_comparison.v1"
    assert len(packet.executed_cell_results) == 2
    assert packet.executed_cell_results[0].cell_id == "search.cross_execution.cell.audit_small.v1"
    assert packet.executed_cell_results[0].optimize_artifact_id == "bb.rl.next_frontier.export_manifest.trainer.v1"
    assert packet.executed_cell_results[1].cell_id == "search.cross_execution.cell.audit_medium.v1"
    assert packet.executed_cell_results[1].optimize_artifact_id == "bb.rl.next_frontier.export_manifest.parity.live.v1"
    assert packet.shared_budget_rule == "reuse_matrix_cells_before_any_rl_budget_growth"
    assert packet.final_decision == "repeat_rl_cells_before_expanding_harness_divergence_matrix"

    result = run_search_study("search_cross_execution_rl_execution", mode="debug")
    assert result.summary_json["study_key"] == "search_cross_execution_rl_execution"
    assert result.summary_json["packet_family"] == "search_cross_execution_rl_execution.v1"


def test_build_search_cross_execution_rl_regression_ledger_packet_tracks_first_repeat_rule() -> None:
    packet = build_search_cross_execution_rl_regression_ledger_packet()

    assert isinstance(packet, SearchCrossExecutionRLRegressionLedgerPacket)
    assert packet.packet_id == "search.platform.phase3.rl_regression_ledger.v1"
    assert packet.rl_execution_id == "search.platform.phase3.rl_execution.v1"
    assert packet.compared_cell_ids == (
        "search.cross_execution.cell.audit_small.v1",
        "search.cross_execution.cell.audit_medium.v1",
    )
    assert "no_export_manifest_identity_loss" in packet.regression_guards
    assert packet.repeated_run_rule == "require_same_cell_rl_regression_twice_before_reclassification"
    assert packet.final_decision == "treat_rl_regression_as_harness_managed_until_cross_consumer_repeat"

    result = run_search_study("search_cross_execution_rl_regression_ledger", mode="spec")
    assert result.summary_json["study_key"] == "search_cross_execution_rl_regression_ledger"
    assert result.summary_json["packet_family"] == "search_cross_execution_rl_regression_ledger.v1"


def test_build_search_cross_execution_divergence_ledger_packet_joins_optimize_and_rl_ledgers() -> None:
    packet = build_search_cross_execution_divergence_ledger_packet()

    assert isinstance(packet, SearchCrossExecutionDivergenceLedgerPacket)
    assert packet.packet_id == "search.platform.phase4.divergence_ledger.v1"
    assert packet.harness_comparison_id == "search.platform.phase2.harness_comparison.v1"
    assert packet.optimize_regression_ledger_id == "search.platform.phase2.optimize_regression_ledger.v1"
    assert packet.rl_regression_ledger_id == "search.platform.phase3.rl_regression_ledger.v1"
    assert packet.compared_cell_ids == (
        "search.cross_execution.cell.audit_small.v1",
        "search.cross_execution.cell.audit_medium.v1",
    )
    assert packet.classification_rule == "require repeated same-cell cross-consumer divergence before locus escalation"
    assert packet.final_decision == "keep divergence ledger harness_owned_until repeated cross_consumer evidence appears"

    result = run_search_study("search_cross_execution_divergence_ledger", mode="debug")
    assert result.summary_json["study_key"] == "search_cross_execution_divergence_ledger"
    assert result.summary_json["packet_family"] == "search_cross_execution_divergence_ledger.v1"


def test_build_search_cross_execution_repeated_run_summary_packet_keeps_next_locus_narrow() -> None:
    packet = build_search_cross_execution_repeated_run_summary_packet()

    assert isinstance(packet, SearchCrossExecutionRepeatedRunSummaryPacket)
    assert packet.packet_id == "search.platform.phase4.repeated_run_summary.v1"
    assert packet.divergence_ledger_id == "search.platform.phase4.divergence_ledger.v1"
    assert packet.optimize_execution_id == "search.platform.phase2.optimize_execution.v1"
    assert packet.rl_execution_id == "search.platform.phase3.rl_execution.v1"
    assert packet.next_locus_classification == "harness_local_pending_more_repetition"
    assert packet.final_decision == "continue_execution_tranche_without_new_architecture_or_planner_round"

    result = run_search_study("search_cross_execution_repeated_run_summary", mode="spec")
    assert result.summary_json["study_key"] == "search_cross_execution_repeated_run_summary"
    assert result.summary_json["packet_family"] == "search_cross_execution_repeated_run_summary.v1"


def test_build_search_cross_execution_closeout_packet_closes_without_widening_scope() -> None:
    packet = build_search_cross_execution_closeout_packet()

    assert isinstance(packet, SearchCrossExecutionCloseoutPacket)
    assert packet.packet_id == "search.platform.phase5.closeout.v1"
    assert packet.divergence_ledger_id == "search.platform.phase4.divergence_ledger.v1"
    assert packet.repeated_run_summary_id == "search.platform.phase4.repeated_run_summary.v1"
    assert packet.compared_cell_ids == (
        "search.cross_execution.cell.audit_small.v1",
        "search.cross_execution.cell.audit_medium.v1",
    )
    assert "same_budget_cells_preserved" in packet.closeout_checks
    assert packet.final_decision == "close_cross_system_execution_tranche_and_keep_next_work_platform_local"

    result = run_search_study("search_cross_execution_closeout", mode="debug")
    assert result.summary_json["study_key"] == "search_cross_execution_closeout"
    assert result.summary_json["packet_family"] == "search_cross_execution_closeout.v1"


def test_build_search_cross_execution_next_locus_packet_keeps_follow_on_non_architectural() -> None:
    packet = build_search_cross_execution_next_locus_packet()

    assert isinstance(packet, SearchCrossExecutionNextLocusPacket)
    assert packet.packet_id == "search.platform.phase5.next_locus.v1"
    assert packet.closeout_id == "search.platform.phase5.closeout.v1"
    assert packet.next_locus_classification == "platform_or_harness_local_before_any_new_planner_round"
    assert packet.recommended_next_move == "run_repeated_execution_on_same_cells_before_expanding_source_family"
    assert "no_new_planner_round_until_repeated_divergence_exists" in packet.deferred_moves
    assert packet.final_decision == "keep_follow_on_work_execution_local_and_non_architectural"

    result = run_search_study("search_cross_execution_next_locus", mode="spec")
    assert result.summary_json["study_key"] == "search_cross_execution_next_locus"
    assert result.summary_json["packet_family"] == "search_cross_execution_next_locus.v1"

def test_build_search_repair_loop_deployment_readiness_kit_stays_workspace_local_and_runnable() -> None:
    kit = build_search_repair_loop_deployment_readiness_kit()

    assert isinstance(kit, SearchRepairLoopDeploymentReadinessKit)
    assert kit.kit_id == "search.domain.repair_loop.deployment_readiness.v1"
    assert kit.boundary_control_id == "search.domain.repair_loop.boundary_control.v1"
    assert "workspace_snapshot_boundary_explicit" in kit.readiness_checks
    assert "artifacts/search/search.replication_v1.codetree_patch" in kit.expected_artifact_roots
    assert "agentic_coder_prototype/conductor/patching.py" in kit.adapter_surface_refs
    assert kit.kernel_change_required is False
    assert kit.final_decision == "continue_repair_loop_deployment_without_kernel_review"

    result = run_search_study("dag_v6_repair_loop_deployment_readiness", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "dag_v6_repair_loop_deployment_readiness"


def test_build_search_optimize_rl_handoff_regression_keeps_atp_adjacency_out_of_kernel_review() -> None:
    regression = build_search_optimize_rl_handoff_regression()

    assert isinstance(regression, SearchOptimizeRLHandoffRegression)
    assert regression.packet_id == "search.consumer.optimize_rl_handoff_regression.v2"
    assert regression.source_domain == "atp"
    assert regression.target_consumers == ("optimize", "rl")
    assert "assessment_lineage_visibility" in regression.preserved_field_union
    assert "evaluation_pack_identity" in regression.preserved_field_union
    assert regression.stable_contract is True
    assert regression.repeated_shape_gap_detected is False
    assert regression.final_classification == "bounded_glue_only"

    result = run_search_study("dag_v6_optimize_rl_handoff_regression", mode="spec")
    assert result.summary_json["study_key"] == "dag_v6_optimize_rl_handoff_regression"


def test_build_search_optimize_consumer_expansion_keeps_phase3_consumer_growth_bounded() -> None:
    packet = build_search_optimize_consumer_expansion()

    assert isinstance(packet, SearchOptimizeConsumerExpansionPacket)
    assert packet.packet_id == "search.consumer.optimize.expansion.v1"
    assert packet.source_domain == "atp_and_repair_loop"
    assert packet.atp_ready is True
    assert packet.repair_ready is True
    assert "assessment_lineage_visibility" in packet.preserved_field_union
    assert "evaluation_pack_identity" in packet.preserved_field_union
    assert packet.stable_contract is True
    assert packet.final_classification == "bounded_consumer_expansion_only"

    result = run_search_study("dag_v6_optimize_consumer_expansion", mode="spec")
    assert result.summary_json["study_key"] == "dag_v6_optimize_consumer_expansion"


def test_build_search_rl_consumer_expansion_keeps_phase3_consumer_growth_bounded() -> None:
    packet = build_search_rl_consumer_expansion()

    assert isinstance(packet, SearchRLConsumerExpansionPacket)
    assert packet.packet_id == "search.consumer.rl.expansion.v1"
    assert packet.source_domain == "atp_and_repair_loop"
    assert packet.atp_ready is True
    assert packet.repair_ready is True
    assert "assessment_lineage_visibility" in packet.preserved_field_union
    assert "evaluation_pack_identity" in packet.preserved_field_union
    assert packet.stable_contract is True
    assert packet.final_classification == "bounded_consumer_expansion_only"

    result = run_search_study("dag_v6_rl_consumer_expansion", mode="spec")
    assert result.summary_json["study_key"] == "dag_v6_rl_consumer_expansion"


def test_build_search_ctrees_boundary_canary_keeps_ctrees_as_boundary_only_and_study_runnable() -> None:
    canary = build_search_ctrees_boundary_canary()

    assert isinstance(canary, SearchCTreesBoundaryCanaryPacket)
    assert canary.packet_id == "search.platform.ctrees_boundary_canary.v1"
    assert canary.source_domain == "atp_repair_optimize_rl_boundary"
    assert "dag_v6_atp_deployment_readiness" in canary.source_study_keys
    assert "dag_v6_repair_loop_deployment_readiness" in canary.source_study_keys
    assert "agentic_coder_prototype/ctrees/branch_receipt_contract.py" in canary.ctree_surface_refs
    assert "docs/contracts/cli_bridge/schemas/session_event_payload_ctree_snapshot.schema.json" in canary.ctree_contract_refs
    assert "ctree_finish_closure_semantics_in_dag_kernel" in canary.forbidden_imports
    assert canary.kernel_change_required is False
    assert canary.final_classification == "ctrees_boundary_canary_only"

    result = run_search_study("dag_v6_ctrees_boundary_canary", mode="spec")
    assert result.summary_json["study_key"] == "dag_v6_ctrees_boundary_canary"
    assert result.summary_json["packet_family"] == "dag_v6_ctrees_boundary_canary"
    assert result.summary_json["top_level_metrics"]["decision"] == "keep_ctrees_as_boundary_canary"


def test_build_search_slice_packaging_hygiene_note_keeps_slice_gaps_out_of_dag_review() -> None:
    note = build_search_slice_packaging_hygiene_note()

    assert isinstance(note, SearchSlicePackagingHygieneNote)
    assert note.note_id == "search.platform.slice_packaging_hygiene_note.v1"
    assert note.classification == "platform_local_packaging_hygiene_only"
    assert note.dag_kernel_relevance == "none_detected"
    assert len(note.recommended_actions) == 3


def test_build_search_platform_contract_bundle_publishes_stage_a_reference_surface() -> None:
    bundle = build_search_platform_contract_bundle()

    assert isinstance(bundle, SearchPlatformContractBundle)
    assert bundle.bundle_id == "search.platform.contract_bundle.v1"
    assert bundle.bundle_version == "v1"
    assert "search.platform.cross_system_handoff_contract.v1" in bundle.contract_ids
    assert "search.domain.atp.deployment_readiness.v2" in bundle.readiness_kit_ids
    assert "search.platform.ctrees_boundary_canary.v1" in bundle.regression_packet_ids
    assert "dag_v6_atp_deployment_readiness" in bundle.canonical_study_keys
    assert "artifacts/platform/contracts/search_platform_contract_bundle_v1.json" in bundle.artifact_roots
    assert bundle.final_decision == "publish_contract_bundle_and_use_as_stage_a_reference_surface"
    assert bundle.dominant_locus == "platform_local"

    packet = build_search_platform_contract_publication_packet()
    assert packet["decision"] == bundle.final_decision
    assert packet["dominant_locus"] == "platform_local"
    assert packet["published_artifact_refs"]

    result = run_search_study("search_platform_contract_publication", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_platform_contract_publication"
    assert result.summary_json["packet_family"] == "search_platform_contract_publication.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == bundle.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "platform_local"


def test_build_search_platform_reference_fixture_bundle_exposes_stage_a_fixture_families() -> None:
    bundle = build_search_platform_reference_fixture_bundle()

    assert isinstance(bundle, SearchReferenceFixtureBundle)
    assert bundle.bundle_id == "search.platform.reference_fixture_bundle.v1"
    assert "dag_v6_optimize_consumer_expansion" in bundle.source_study_keys
    assert "artifacts/platform/fixtures/atp_deployment_readiness_fixture_v1.json" in bundle.fixture_refs
    assert "search.platform.cross_system_handoff_contract.v1" in bundle.contract_ids
    assert bundle.final_decision == "publish_fixture_bundle_for_stage_b_and_stage_c_consumers"
    assert bundle.dominant_locus == "platform_local"


def test_build_search_platform_fixture_publication_packet_marks_rows_loadable_and_smoke_ready() -> None:
    packet = build_search_platform_fixture_publication_packet()

    assert isinstance(packet, SearchPlatformFixturePublicationPacket)
    assert packet.packet_id == "search.platform.fixture_publication_packet.v1"
    assert packet.bundle_id == "search.platform.reference_fixture_bundle.v1"
    assert len(packet.rows) == 5
    assert all(isinstance(row, SearchPublishedFixtureRow) for row in packet.rows)
    assert packet.published_fixture_count == 5
    assert packet.loadable_fixture_count == 5
    assert "optimize" in packet.smoke_ready_consumers
    assert "rl" in packet.smoke_ready_consumers
    assert packet.final_decision == "publish_fixture_rows_and_use_them_as_stage_a_loadable_reference_surface"
    assert packet.dominant_locus == "platform_local"

    result = run_search_study("search_platform_fixture_publication", mode="spec")
    assert result.summary_json["study_key"] == "search_platform_fixture_publication"
    assert result.summary_json["packet_family"] == "search_platform_fixture_publication.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "platform_local"


def test_build_search_platform_regression_harness_keeps_stage_a_failures_small_and_local() -> None:
    harness = build_search_platform_regression_harness()

    assert isinstance(harness, SearchDeploymentRegressionHarness)
    assert harness.harness_id == "search.platform.deployment_regression_harness.v1"
    assert harness.contract_bundle_id == "search.platform.contract_bundle.v1"
    assert harness.fixture_bundle_id == "search.platform.reference_fixture_bundle.v1"
    assert all(isinstance(check, SearchDeploymentRegressionCheck) for check in harness.checks)
    assert len(harness.checks) == 4
    assert harness.blocking_check_ids == ()
    assert harness.final_decision == "stage_a_publication_surface_ready_for_atp_lane_hardening"
    assert harness.dominant_locus == "platform_local"

    result = run_search_study("search_platform_regression_harness", mode="spec")
    assert result.summary_json["study_key"] == "search_platform_regression_harness"
    assert result.summary_json["packet_family"] == "search_platform_regression_harness.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == harness.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "platform_local"


def test_build_search_platform_regression_entrypoint_binds_contract_fixture_and_harness_checks() -> None:
    entrypoint = build_search_platform_regression_entrypoint()

    assert isinstance(entrypoint, SearchPlatformRegressionEntrypoint)
    assert entrypoint.entrypoint_id == "search.platform.regression_entrypoint.v1"
    assert entrypoint.contract_publication_study_key == "search_platform_contract_publication"
    assert entrypoint.fixture_publication_study_key == "search_platform_fixture_publication"
    assert entrypoint.regression_harness_study_key == "search_platform_regression_harness"
    assert len(entrypoint.validator_commands) == 3
    assert "contract_bundle_visible" in entrypoint.smoke_check_ids
    assert "fixture.atp_deployment_readiness.v1" in entrypoint.smoke_check_ids
    assert entrypoint.status == "ready"
    assert entrypoint.final_decision == "use_stage_a_regression_entrypoint_before_starting_stage_b"
    assert entrypoint.dominant_locus == "platform_local"

    result = run_search_study("search_platform_regression_entrypoint", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_platform_regression_entrypoint"
    assert result.summary_json["packet_family"] == "search_platform_regression_entrypoint.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == entrypoint.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "platform_local"


def test_build_search_platform_command_bundle_publishes_stable_stage_a_invocation_contract() -> None:
    bundle = build_search_platform_command_bundle()

    assert isinstance(bundle, SearchPlatformCommandBundle)
    assert bundle.bundle_id == "search.platform.command_bundle.v1"
    assert len(bundle.commands) == 3
    assert all(isinstance(command, SearchPlatformCommandSpec) for command in bundle.commands)
    assert bundle.contract_bundle_id == "search.platform.contract_bundle.v1"
    assert bundle.fixture_bundle_id == "search.platform.reference_fixture_bundle.v1"
    assert bundle.regression_entrypoint_id == "search.platform.regression_entrypoint.v1"
    assert bundle.final_decision == "publish_platform_facing_command_contract_for_stage_a_surfaces"
    assert bundle.dominant_locus == "platform_local"

    result = run_search_study("search_platform_command_bundle", mode="debug")
    assert result.payload is not None
    assert result.summary_json["study_key"] == "search_platform_command_bundle"
    assert result.summary_json["packet_family"] == "search_platform_command_bundle.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == bundle.final_decision


def test_build_search_platform_validator_packet_validates_stage_a_command_and_fixture_surface() -> None:
    packet = build_search_platform_validator_packet()

    assert isinstance(packet, SearchPlatformValidatorPacket)
    assert packet.packet_id == "search.platform.validator_packet.v1"
    assert packet.command_bundle_id == "search.platform.command_bundle.v1"
    assert "platform.contract_publication.debug" in packet.validated_command_ids
    assert "search_platform_regression_entrypoint" in packet.validated_study_keys
    assert "artifacts/platform/contracts/search_platform_contract_bundle_v1.json" in packet.validated_artifact_refs
    assert packet.blocking_validation_ids == ()
    assert packet.status == "ready"
    assert packet.final_decision == "stage_a_platform_surface_validated_and_ready_to_exit"
    assert packet.dominant_locus == "platform_local"

    result = run_search_study("search_platform_validator", mode="spec")
    assert result.summary_json["study_key"] == "search_platform_validator"
    assert result.summary_json["packet_family"] == "search_platform_validator.v1"
    assert result.summary_json["top_level_metrics"]["decision"] == packet.final_decision
    assert result.summary_json["top_level_metrics"]["dominant_locus"] == "platform_local"


def test_build_search_optimize_handoff_kit_standardizes_existing_consumer_packet() -> None:
    kit = build_search_optimize_handoff_kit()

    assert isinstance(kit, SearchOptimizeHandoffKit)
    assert kit.consumer_kind == "optimize"
    assert kit.composition_id == "composition.next_frontier.dag_to_optimize.v1"
    assert kit.final_decision == "keep_optimize_frozen"
    assert len(kit.rows) == 3
    assert all(isinstance(row, SearchConsumerProofRow) for row in kit.rows)
    assert {row.topology_class for row in kit.rows} == {"G", "F", "H"}
    assert all(row.measured_shadow_semantics_required is False for row in kit.rows)
    assert kit.repeated_shape_gap_detected is False


def test_build_search_optimize_comparison_kit_exposes_comparison_and_handoff_contract() -> None:
    kit = build_search_optimize_comparison_kit()

    assert isinstance(kit, SearchOptimizeComparisonKit)
    assert kit.comparison_protocol == "paired_downstream_consumer_compare.v1"
    assert "adapter_readiness" in kit.objective_keys
    assert "replay_stability" in kit.objective_keys
    assert "recipe_manifest_identity" in kit.handoff_preserved_fields
    assert kit.helper_only_handoff is True
    assert kit.repeated_shape_gap_detected is False
    assert kit.final_decision == "keep_optimize_frozen"


def test_build_search_rl_handoff_kit_standardizes_existing_consumer_packet() -> None:
    kit = build_search_rl_handoff_kit()

    assert isinstance(kit, SearchRLHandoffKit)
    assert kit.consumer_kind == "rl"
    assert kit.composition_id == "composition.next_frontier.dag_to_rl.v1"
    assert kit.final_decision == "keep_rl_frozen"
    assert len(kit.rows) == 3
    assert all(isinstance(row, SearchConsumerProofRow) for row in kit.rows)
    assert {row.topology_class for row in kit.rows} == {"F", "W", "D"}
    assert all(row.measured_shadow_semantics_required is False for row in kit.rows)
    assert kit.repeated_shape_gap_detected is False


def test_build_search_rl_replay_parity_kit_confirms_parity_and_closeout() -> None:
    kit = build_search_rl_replay_parity_kit()

    assert isinstance(kit, SearchRLReplayParityKit)
    assert kit.workload_family == "dag_codetree_patch"
    assert kit.live_export_manifest_id == "bb.rl.next_frontier.export_manifest.parity.live.v1"
    assert kit.replay_export_manifest_id == "bb.rl.next_frontier.export_manifest.parity.replay.v1"
    assert kit.graph_parity_equal is True
    assert kit.export_manifest_parity_equal is True
    assert "evaluation_pack_identity" in kit.handoff_preserved_fields
    assert kit.repeated_shape_gap_detected is False
    assert kit.final_decision == "keep_rl_frozen"


def test_build_search_consumer_seam_diagnostic_classifies_non_runtime_gaps_cleanly() -> None:
    diagnostic = build_search_consumer_seam_diagnostic()

    assert isinstance(diagnostic, SearchConsumerSeamDiagnostic)
    assert "comparison_packet_handoff" in diagnostic.optimize_seam_labels
    assert "trajectory_projection_handoff" in diagnostic.rl_seam_labels
    assert diagnostic.optimize_issue_loci == ("consumer_local_only", "helper_level_only")
    assert diagnostic.rl_issue_loci == ("consumer_local_only", "helper_level_only")
    assert diagnostic.combined_issue_loci == ("consumer_local_only", "helper_level_only")
    assert diagnostic.repeated_shape_gap_detected is False
    assert diagnostic.dag_runtime_missing_truth_detected is False
    assert diagnostic.final_classification == "consumer_and_helper_only"


def test_build_search_lineage_view_supports_run_and_export_sources() -> None:
    run = build_dag_v4_got_v2_packet()["run"]
    exported = export_search_trajectory(run)

    run_view = build_search_lineage_view(run)
    export_view = build_search_lineage_view(exported)

    assert run_view.source_kind == "run"
    assert export_view.source_kind == "trajectory_export"
    assert run_view.source_id == export_view.source_id
    assert run_view.recipe_kind == export_view.recipe_kind
    assert run_view.event_count == export_view.event_count
    assert run_view.first_event_id == export_view.first_event_id
    assert run_view.last_event_id == export_view.last_event_id


def test_build_search_assessment_chain_view_summarizes_links_cleanly() -> None:
    packet = build_dag_v4_dci_packet()["assessment_lineage_packet"]
    view = build_search_assessment_chain_view(packet)

    assert view.packet_id == packet.packet_id
    assert view.target_family == "dci_typed_epistemic_acts"
    assert view.topology_class == "D"
    assert view.action_link_count >= 1
    assert "verify" in view.assessment_kinds
    assert view.mixed_chain_reconstructable is True


def test_build_search_replay_export_summary_and_diff_capture_preserved_and_lost_info() -> None:
    got_packet = build_dag_v4_got_v2_packet()
    dci_packet = build_dag_v4_dci_packet()

    got_summary = build_search_replay_export_summary(
        got_packet["run"],
        integrity_packet=got_packet["replay_export_integrity_packet"],
    )
    dci_summary = build_search_replay_export_summary(
        dci_packet["run"],
        integrity_packet=dci_packet["replay_export_integrity_packet"],
    )
    diff = diff_search_replay_export_summaries(got_summary, dci_summary)

    assert got_summary.source_kind == "replay_export_integrity_packet"
    assert "trajectory_export" in got_summary.export_modes
    assert dci_summary.source_kind == "replay_export_integrity_packet"
    assert dci_summary.target_family == "dci_typed_epistemic_acts"
    assert "workspace_snapshot_lineage" in dci_summary.preserved_semantics
    assert "multi_parent_lineage" in diff.left_only_preserved_semantics
    assert diff.right_only_preserved_semantics
    assert diff.topology_class_changed is True


def test_replay_export_summary_supports_optimize_and_rl_source_families() -> None:
    optimize_source = build_dag_v4_bavt_packet()
    rl_source = build_dag_v4_codetree_v2_packet()

    optimize_summary = build_search_replay_export_summary(optimize_source["run"])
    rl_summary = build_search_replay_export_summary(
        rl_source["run"],
        integrity_packet=rl_source["replay_export_integrity_packet"],
    )

    assert optimize_summary.recipe_kind == "bavt_budget_aware_frontier_packet"
    assert optimize_summary.step_count >= 1
    assert rl_summary.recipe_kind == "codetree_stage_patch_packet_v2"
    assert rl_summary.preserved_semantics
    assert rl_summary.shadow_assumptions_required is False


def test_default_search_study_kits_cover_canonical_families_with_standard_controls() -> None:
    kits = build_default_search_study_kits()
    assert len(kits) >= 6
    by_key = {kit.study_key: kit for kit in kits}
    assert "dag_replication_v1_tot_game24" in by_key
    assert "dag_v4_dci" in by_key
    for kit in kits:
        assert kit.required_controls == (
            "compute_normalization",
            "baseline_discipline",
            "evaluator_control",
            "artifact_integrity",
        )
        assert kit.artifact_contract.summary_json is True
        assert kit.artifact_contract.summary_txt is True
        assert kit.artifact_contract.inspect_supported is True
        assert kit.artifact_contract.compare_supported is True


def test_canonical_study_kit_marks_ready_controls_from_existing_packet_evidence() -> None:
    kits = {kit.study_key: kit for kit in build_default_search_study_kits()}
    bavt = kits["dag_v4_bavt"]
    got = kits["dag_replication_v1_got_sorting"]
    bavt_controls = {item.control_key: item for item in bavt.control_templates}
    got_controls = {item.control_key: item for item in got.control_templates}
    assert bavt_controls["compute_normalization"].status == "ready"
    assert bavt_controls["baseline_discipline"].status == "ready"
    assert bavt_controls["evaluator_control"].status == "ready"
    assert bavt_controls["artifact_integrity"].status == "ready"
    assert got_controls["compute_normalization"].status == "ready"
    assert got_controls["baseline_discipline"].status == "ready"
    assert got_controls["evaluator_control"].status == "standardized_followup"
    assert got_controls["artifact_integrity"].status == "ready"


def test_canonical_study_kit_preserves_top_level_metrics_for_distinct_topologies() -> None:
    kits = {kit.study_key: kit for kit in build_default_search_study_kits()}
    assert kits["dag_v4_bavt"].top_level_metrics["topology_class"] == "F"
    assert kits["dag_v4_team_of_thoughts"].top_level_metrics["topology_class"] == "H"
    assert kits["dag_v4_dci"].top_level_metrics["topology_class"] == "D"


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
