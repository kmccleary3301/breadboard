from __future__ import annotations

from agentic_coder_prototype.rl import (
    AdapterCapabilities,
    AdapterProbeReport,
    CompactionManifest,
    CostLedger,
    CreditFrame,
    DatasetExportUnit,
    EnvironmentDescriptor,
    EvaluationAnnotation,
    EvaluationPackManifest,
    ExportManifest,
    PolicyProvenance,
    RolloutDescriptor,
    TrainingFeedback,
    TrajectoryGraph,
    build_rl_v1_alpha_exporters_example,
    build_rl_v1_alpha_exporters_example_payload,
    build_rl_v1_boundary_audit_packet,
    build_rl_v1_boundary_audit_packet_payload,
    build_rl_v1_contract_pack_example,
    build_rl_v1_contract_pack_example_payload,
    build_rl_v1_dataset_training_feedback_probe,
    build_rl_v1_dataset_training_feedback_probe_payload,
    build_rl_v1_evaluator_verifier_probe,
    build_rl_v1_evaluator_verifier_probe_payload,
    build_rl_v1_freeze_and_deferrals,
    build_rl_v1_freeze_and_deferrals_payload,
    build_rl_v1_live_projection_example,
    build_rl_v1_live_projection_example_payload,
    build_rl_v1_multi_agent_async_hardening_example,
    build_rl_v1_multi_agent_async_hardening_example_payload,
    build_rl_v1_replay_parity_example,
    build_rl_v1_replay_parity_example_payload,
    build_rl_v1_replay_projection_example,
    build_rl_v1_replay_projection_example_payload,
    build_rl_v1_serving_inference_probe,
    build_rl_v1_serving_inference_probe_payload,
    build_rl_v1_trajectory_graph_shell_example,
    build_rl_v1_trajectory_graph_shell_example_payload,
    build_rl_v2_compaction_fidelity_example,
    build_rl_v2_compaction_fidelity_example_payload,
    build_rl_v2_delayed_evaluation_fidelity_example,
    build_rl_v2_delayed_evaluation_fidelity_example_payload,
    build_rl_v2_adapter_probe_program_example,
    build_rl_v2_adapter_probe_program_example_payload,
    build_rl_v2_export_conformance_example,
    build_rl_v2_export_conformance_example_payload,
    build_rl_v2_freeze_and_deferrals,
    build_rl_v2_freeze_and_deferrals_payload,
    build_rl_v2_pressure_study_packet,
    build_rl_v2_pressure_study_packet_payload,
    build_rl_v2_freeze_and_scope_packet,
    build_rl_v2_freeze_and_scope_packet_payload,
    build_rl_v2_replay_live_fidelity_example,
    build_rl_v2_replay_live_fidelity_example_payload,
    build_next_frontier_rl_trainer_facing_export_packet,
    build_next_frontier_rl_trainer_facing_export_packet_payload,
    build_next_frontier_rl_evaluator_verifier_packet,
    build_next_frontier_rl_evaluator_verifier_packet_payload,
    build_next_frontier_rl_replay_live_parity_packet,
    build_next_frontier_rl_replay_live_parity_packet_payload,
    build_next_frontier_rl_adapter_friction_synthesis,
    build_next_frontier_rl_adapter_friction_synthesis_payload,
    build_next_frontier_rl_second_trainer_facing_export_packet,
    build_next_frontier_rl_second_trainer_facing_export_packet_payload,
    build_next_frontier_rl_second_evaluator_verifier_packet,
    build_next_frontier_rl_second_evaluator_verifier_packet_payload,
    build_next_frontier_rl_second_replay_live_parity_packet,
    build_next_frontier_rl_second_replay_live_parity_packet_payload,
    build_next_frontier_rl_tranche_synthesis_v2,
    build_next_frontier_rl_tranche_synthesis_v2_payload,
    build_next_frontier_dag_to_rl_composition_packet,
    build_next_frontier_dag_to_rl_composition_packet_payload,
    build_next_frontier_rl_final_closeout_packet,
    build_next_frontier_rl_final_closeout_packet_payload,
)
from agentic_coder_prototype.search import SearchRun


def test_rl_v1_boundary_audit_packet_reconciles_existing_surfaces() -> None:
    packet = build_rl_v1_boundary_audit_packet()
    payload = build_rl_v1_boundary_audit_packet_payload()

    assert packet["namespace_plan"]["overlay_package"] == "agentic_coder_prototype.rl"
    assert packet["namespace_plan"]["rl_owns_search_semantics"] is False
    assert any(item["surface"].endswith("search/export.py::SearchTrajectoryExport") for item in packet["superseded_or_reframed_surfaces"])
    assert any(item["surface"].endswith("optimize/trajectory_ir.py") for item in packet["superseded_or_reframed_surfaces"])
    assert "no rl_owned_duplicate_search_ontology" in payload["anti_goals"]


def test_rl_v1_contract_pack_example_round_trips() -> None:
    example = build_rl_v1_contract_pack_example()
    payload = build_rl_v1_contract_pack_example_payload()
    run = SearchRun.from_dict(payload["run"])
    rollout = RolloutDescriptor.from_dict(payload["rollout_descriptor"])
    environment = EnvironmentDescriptor.from_dict(payload["environment_descriptor"])
    policy = PolicyProvenance.from_dict(payload["policy_provenance"])
    annotations = [EvaluationAnnotation.from_dict(item) for item in payload["evaluation_annotations"]]
    cost_ledger = CostLedger.from_dict(payload["cost_ledger"])
    compaction = [CompactionManifest.from_dict(item) for item in payload["compaction_manifests"]]
    adapter = AdapterCapabilities.from_dict(payload["adapter_capabilities"])

    assert run.recipe_kind == "branch_execute_verify"
    assert rollout.origin_kind == "live"
    assert environment.environment_kind == "code_agent_workspace"
    assert policy.model_name == "gpt-5.4-mini"
    assert annotations
    assert cost_ledger.token_counts["total_tokens"] >= 0
    assert adapter.supports_graph_trajectory is True
    assert compaction
    assert example["adapter_capabilities"] == adapter


def test_rl_v1_trajectory_graph_shell_example_projects_graph_truth() -> None:
    example = build_rl_v1_trajectory_graph_shell_example()
    payload = build_rl_v1_trajectory_graph_shell_example_payload()
    graph = example["trajectory_graph"]
    round_tripped = TrajectoryGraph.from_dict(payload["trajectory_graph"])

    assert graph.graph_id.endswith(".rl.trajectory_graph.v1")
    assert len(graph.tracks) >= 2
    assert len(graph.observations) == len(example["run"].candidates)
    assert len(graph.decisions) == len(example["run"].events)
    assert len(graph.effects) == len(example["run"].events)
    assert graph.evaluation_annotations
    assert graph.cost_ledger is not None
    assert graph.compaction_manifests
    assert round_tripped == graph
    assert graph.metadata["graph_shell_only"] is True


def test_rl_v1_live_projection_example_is_explicit() -> None:
    example = build_rl_v1_live_projection_example()
    payload = build_rl_v1_live_projection_example_payload()
    graph = TrajectoryGraph.from_dict(payload["trajectory_graph"])

    assert graph.rollout_descriptor.origin_kind == "live"
    assert graph.metadata["projection_path"] == "live"
    assert len(graph.decisions) == len(example["run"].events)
    assert graph.cost_ledger is not None


def test_rl_v1_replay_projection_example_is_explicit() -> None:
    example = build_rl_v1_replay_projection_example()
    payload = build_rl_v1_replay_projection_example_payload()
    graph = TrajectoryGraph.from_dict(payload["trajectory_graph"])
    replay_run = SearchRun.from_dict(payload["run_payload"])

    assert graph.rollout_descriptor.origin_kind == "replay"
    assert graph.metadata["projection_path"] == "replay"
    assert graph.rollout_descriptor.source_ref == replay_run.search_id
    assert len(graph.observations) == len(replay_run.candidates)


def test_rl_v1_replay_parity_example_matches_at_graph_core() -> None:
    example = build_rl_v1_replay_parity_example()
    payload = build_rl_v1_replay_parity_example_payload()

    assert example["live_graph"].rollout_descriptor.origin_kind == "live"
    assert example["replay_graph"].rollout_descriptor.origin_kind == "replay"
    assert example["live_parity_view"] == example["replay_parity_view"]
    assert payload["live_parity_view"] == payload["replay_parity_view"]


def test_rl_v1_alpha_exporters_example_is_trainer_neutral_and_round_trips() -> None:
    example = build_rl_v1_alpha_exporters_example()
    payload = build_rl_v1_alpha_exporters_example_payload()
    live_sft = DatasetExportUnit.from_dict(payload["live_exports"]["sft"])
    live_transition = DatasetExportUnit.from_dict(payload["live_exports"]["transition"])
    live_verifier = DatasetExportUnit.from_dict(payload["live_exports"]["verifier"])

    assert live_sft.export_kind == "sft_distillation"
    assert live_transition.export_kind == "rl_transition_segment"
    assert live_verifier.export_kind == "verifier_example"
    assert live_sft.rollout_descriptor.source_ref == example["run"].search_id
    assert live_transition.policy_provenance
    assert live_verifier.compaction_manifests
    assert payload["live_export_core_views"] == payload["replay_export_core_views"]
    assert example["live_export_core_views"] == example["replay_export_core_views"]


def test_rl_v1_transition_export_contains_decision_segments() -> None:
    example = build_rl_v1_alpha_exporters_example()
    transition_export = example["live_exports"]["transition"]
    transitions = transition_export.record_payload["transitions"]

    assert len(transitions) == len(example["live_graph"].decisions)
    assert transitions[0]["observation_ids"]
    assert all("decision_id" in item for item in transitions)


def test_rl_v1_verifier_export_tracks_verifier_annotations() -> None:
    example = build_rl_v1_alpha_exporters_example()
    verifier_export = example["live_exports"]["verifier"]
    annotations = verifier_export.record_payload["verifier_annotations"]

    assert annotations
    assert any(item["channel"] in {"execute", "verify"} for item in annotations)


def test_rl_v1_multi_agent_async_hardening_example_preserves_semantics() -> None:
    example = build_rl_v1_multi_agent_async_hardening_example()
    payload = build_rl_v1_multi_agent_async_hardening_example_payload()
    graph = TrajectoryGraph.from_dict(payload["trajectory_graph"])
    credit_frame = CreditFrame.from_dict(payload["credit_frame"])
    edge_kinds = {item.edge_kind for item in graph.causal_edges}

    assert "spawns_branch_track" in edge_kinds
    assert "join_branch_track" in edge_kinds
    assert "message_visible_to_observation" in edge_kinds
    assert "workspace_visible_to_observation" in edge_kinds
    assert "writes_workspace_snapshot" in edge_kinds
    assert "wakes_track" in edge_kinds
    assert any(item.delayed for item in graph.evaluation_annotations)
    assert credit_frame.delayed_annotation_ids
    assert credit_frame.workspace_attribution_refs
    assert payload["checkpoint_pointer"]["schema_version"] == "bb.checkpoint_metadata.v1"
    assert graph.rollout_descriptor.metadata["checkpoint_pointer"]["phase"] == "verification_resume"
    assert example["credit_frame"] == credit_frame


def test_rl_v1_credit_frame_has_async_shared_attribution_shape() -> None:
    example = build_rl_v1_multi_agent_async_hardening_example()
    credit_frame = example["credit_frame"]

    assert credit_frame.frame_kind == "async_shared_attribution"
    assert set(credit_frame.target_annotation_ids) == {item.annotation_id for item in example["trajectory_graph"].evaluation_annotations}
    assert credit_frame.decision_weights
    assert credit_frame.track_weights
    assert credit_frame.metadata["continuation_aligned"] is True


def test_rl_v1_serving_inference_probe_preserves_policy_provenance() -> None:
    example = build_rl_v1_serving_inference_probe()
    payload = build_rl_v1_serving_inference_probe_payload()
    export_unit = DatasetExportUnit.from_dict(payload["export_unit"])

    assert payload["probe_id"] == "bb.rl.v1.serving_inference_probe.v1"
    assert payload["policy_provenance"]["provider"] == "openai"
    assert export_unit.export_kind == "sft_distillation"
    assert payload["boundary"]["serving_owned_outside_breadboard"] is True
    assert payload["boundary"]["trainer_specific_state_added"] is False


def test_rl_v1_evaluator_verifier_probe_preserves_annotation_truth() -> None:
    example = build_rl_v1_evaluator_verifier_probe()
    payload = build_rl_v1_evaluator_verifier_probe_payload()
    export_unit = DatasetExportUnit.from_dict(payload["export_unit"])

    assert payload["probe_id"] == "bb.rl.v1.evaluator_verifier_probe.v1"
    assert export_unit.export_kind == "verifier_example"
    assert payload["boundary"]["annotation_truth_stays_inside_breadboard"] is True
    assert any(channel in {"execute", "verify"} for channel in payload["annotation_channels"])


def test_rl_v1_dataset_training_feedback_probe_round_trips() -> None:
    example = build_rl_v1_dataset_training_feedback_probe()
    payload = build_rl_v1_dataset_training_feedback_probe_payload()
    export_unit = DatasetExportUnit.from_dict(payload["export_unit"])
    training_feedback = TrainingFeedback.from_dict(payload["training_feedback"])

    assert payload["probe_id"] == "bb.rl.v1.dataset_training_feedback_probe.v1"
    assert export_unit.export_kind == "rl_transition_segment"
    assert training_feedback.target_export_unit_id == export_unit.export_unit_id
    assert training_feedback.status == "accepted_for_batching"
    assert payload["boundary"]["training_feedback_supported"] is True
    assert payload["boundary"]["trainer_specific_optimizer_state_added"] is False
    assert example["training_feedback"] == training_feedback


def test_rl_v1_freeze_and_deferrals_packet_closes_v1_cleanly() -> None:
    example = build_rl_v1_freeze_and_deferrals()
    payload = build_rl_v1_freeze_and_deferrals_payload()

    assert payload["freeze_decision"]["current_decision"] == "freeze_rl_v1"
    assert payload["freeze_decision"]["open_rl_v2_now"] is False
    assert "trainer-specific packing and optimizer state" in payload["deferred_to_v2"]
    assert payload["boundary_summary"]["serving_outside"] is True
    assert payload["boundary_summary"]["evaluator_outside"] is True
    assert payload["boundary_summary"]["dataset_engine_outside"] is True
    assert payload["completed_probes"] == example["completed_probes"]


def test_rl_v2_freeze_and_scope_packet_locks_v2_center() -> None:
    packet = build_rl_v2_freeze_and_scope_packet()
    payload = build_rl_v2_freeze_and_scope_packet_payload()

    assert packet["v2_center"] == "export_data_fidelity"
    assert packet["boundary"]["kernel_truth_frozen"] is True
    assert "no_new_kernel_nouns_by_default" in payload["non_goals"]
    assert payload["support_ladder"] == ["probe", "experimental", "supported"]


def test_rl_v2_replay_live_fidelity_example_preserves_manifest_parity() -> None:
    example = build_rl_v2_replay_live_fidelity_example()
    payload = build_rl_v2_replay_live_fidelity_example_payload()
    live_pack = EvaluationPackManifest.from_dict(payload["live_evaluation_pack"])
    replay_pack = EvaluationPackManifest.from_dict(payload["replay_evaluation_pack"])
    live_manifest = ExportManifest.from_dict(payload["live_export_manifest"])
    replay_manifest = ExportManifest.from_dict(payload["replay_export_manifest"])

    assert live_pack.evaluation_pack_id == replay_pack.evaluation_pack_id
    assert live_pack.annotation_ids == replay_pack.annotation_ids
    assert live_manifest.export_fingerprint == replay_manifest.export_fingerprint
    assert payload["live_export_manifest_parity_view"] == payload["replay_export_manifest_parity_view"]
    assert example["live_export_manifest_parity_view"] == example["replay_export_manifest_parity_view"]


def test_rl_v2_compaction_fidelity_example_preserves_compaction_refs() -> None:
    example = build_rl_v2_compaction_fidelity_example()
    payload = build_rl_v2_compaction_fidelity_example_payload()
    export_unit = DatasetExportUnit.from_dict(payload["export_unit"])
    export_manifest = ExportManifest.from_dict(payload["export_manifest"])
    report = payload["compaction_fidelity_report"]

    assert export_unit.compaction_manifests
    assert export_manifest.evaluation_pack_id == payload["evaluation_pack"]["evaluation_pack_id"]
    assert report["all_compaction_refs_preserved"] is True
    assert report["lossy_policy_view"] is True
    assert report["fidelity_tiers"] == [item.fidelity_tier for item in export_unit.compaction_manifests]
    assert example["compaction_fidelity_report"] == report


def test_rl_v2_delayed_evaluation_fidelity_example_preserves_available_at() -> None:
    example = build_rl_v2_delayed_evaluation_fidelity_example()
    payload = build_rl_v2_delayed_evaluation_fidelity_example_payload()
    export_manifest = ExportManifest.from_dict(payload["export_manifest"])
    report = payload["delayed_evaluation_fidelity_report"]

    assert export_manifest.evaluation_pack_id == payload["evaluation_pack"]["evaluation_pack_id"]
    assert report["delayed_annotation_count"] >= 1
    assert report["all_available_at_explicit"] is True
    assert report["policy_view_safe"] is True
    assert example["delayed_evaluation_fidelity_report"] == report


def test_rl_v2_export_conformance_example_preserves_replay_live_parity() -> None:
    example = build_rl_v2_export_conformance_example()
    payload = build_rl_v2_export_conformance_example_payload()
    live_manifest = ExportManifest.from_dict(payload["live_export_manifest"])
    replay_manifest = ExportManifest.from_dict(payload["replay_export_manifest"])

    assert live_manifest.fidelity_tier == "replay_parity_verified"
    assert replay_manifest.fidelity_tier == "replay_parity_verified"
    assert payload["live_conformance_parity_view"] == payload["replay_conformance_parity_view"]
    assert example["live_conformance_parity_view"] == example["replay_conformance_parity_view"]


def test_rl_v2_export_conformance_packet_tracks_split_and_contamination() -> None:
    example = build_rl_v2_export_conformance_example()
    packet = example["live_conformance_packet"]

    assert packet["split_provenance"]["split_kind"] == "train_holdout"
    assert "teacher_student_origin_guard" in packet["split_provenance"]["contamination_controls"]
    assert packet["summary"]["export_unit_count"] == 3
    assert packet["summary"]["fidelity_tier"] == "replay_parity_verified"
    assert packet["summary"]["export_kind_counts"]["verifier_example"] == 1


def test_rl_v2_adapter_probe_program_reports_bounded_support() -> None:
    example = build_rl_v2_adapter_probe_program_example()
    payload = build_rl_v2_adapter_probe_program_example_payload()
    reports = {
        key: AdapterProbeReport.from_dict(value)
        for key, value in payload["probe_reports"].items()
    }

    assert set(reports) == {"serving", "evaluator", "dataset", "trainer_feedback"}
    assert all(item.support_level == "probe" for item in reports.values())
    assert reports["serving"].probe_kind == "serving_inference"
    assert reports["evaluator"].workload_family == "async_verifier"
    assert reports["dataset"].export_manifest_id == payload["live_export_manifest"]["export_manifest_id"]
    assert reports["trainer_feedback"].capability_snapshot.supports_training_feedback is True
    assert example["probe_reports"]["serving"].probe_report_id == reports["serving"].probe_report_id


def test_rl_v2_adapter_probe_reports_make_losses_and_unsupported_fields_explicit() -> None:
    example = build_rl_v2_adapter_probe_program_example()
    dataset_report = example["probe_reports"]["dataset"]
    trainer_feedback_report = example["probe_reports"]["trainer_feedback"]

    assert "external_parquet_layout_delegated" in dataset_report.fidelity_losses
    assert "parquet_row_group_config" in dataset_report.unsupported_fields
    assert "optimizer_state_omitted_by_design" in trainer_feedback_report.fidelity_losses
    assert "optimizer_checkpoint_ref" in trainer_feedback_report.unsupported_fields


def test_rl_v2_pressure_study_packet_prefers_graph_native_export() -> None:
    packet = build_rl_v2_pressure_study_packet()
    payload = build_rl_v2_pressure_study_packet_payload()

    assert payload["packet_id"] == "bb.rl.v2.pressure_study_packet.v1"
    assert len(payload["representative_workloads"]) == 3
    assert payload["comparison"]["winner"] == "graph_native_export"
    assert payload["comparison"]["graph_native_materially_better"] is True
    assert payload["baselines"]["graph_native_export"]["information_loss_count"] == 0
    assert payload["baselines"]["transcript_only"]["information_loss_count"] > 0
    assert packet["comparison"]["evidence"]["replay_parity_holds"] is True


def test_rl_v2_pressure_study_packet_uses_mini_default_policy() -> None:
    packet = build_rl_v2_pressure_study_packet()

    assert packet["experiment_policy"]["default_model"] == "gpt-5.4-mini"
    assert packet["experiment_policy"]["default_mode"] == "mini_default"
    assert "auditable" in packet["experiment_policy"]["escalation_rule"]


def test_rl_v2_freeze_and_deferrals_closes_v2_without_growth() -> None:
    packet = build_rl_v2_freeze_and_deferrals()
    payload = build_rl_v2_freeze_and_deferrals_payload()

    assert payload["freeze_decision"]["current_decision"] == "freeze_rl_v2"
    assert payload["freeze_decision"]["open_rl_v3_now"] is False
    assert payload["freeze_decision"]["new_kernel_nouns_added"] is False
    assert payload["surfaces_proven_under_pressure"] == [
        "evaluation_pack_manifest",
        "export_manifest",
        "adapter_probe_report",
    ]
    assert "policy_view_witness" in payload["surfaces_not_justified"]
    assert "bounded_probe_level_adapter_evidence" in payload["public_claims_enabled"]
    assert packet["deferred_after_v2"] == payload["deferred_after_v2"]


def test_next_frontier_rl_trainer_facing_export_packet_is_bounded_and_manifested() -> None:
    example = build_next_frontier_rl_trainer_facing_export_packet()
    payload = build_next_frontier_rl_trainer_facing_export_packet_payload()
    manifest = ExportManifest.from_dict(payload["export_manifest"])
    evaluation_pack = EvaluationPackManifest.from_dict(payload["evaluation_pack"])

    assert payload["workload_family"] == "dag_got_sorting"
    assert manifest.fidelity_tier == "bounded_trainer_ready"
    assert evaluation_pack.evaluation_pack_id == manifest.evaluation_pack_id
    assert payload["bounded_loss_report"]["lost_fields"] == []
    assert payload["study_note"]["pain_classification"] == "adapter_local_expectation_gap_only"


def test_next_frontier_rl_evaluator_verifier_packet_keeps_pack_coherent() -> None:
    example = build_next_frontier_rl_evaluator_verifier_packet()
    payload = build_next_frontier_rl_evaluator_verifier_packet_payload()
    export_unit = DatasetExportUnit.from_dict(payload["export_unit"])
    evaluation_pack = EvaluationPackManifest.from_dict(payload["evaluation_pack"])

    assert payload["workload_family"] == "dag_tot_game24"
    assert export_unit.export_kind == "verifier_example"
    assert set(evaluation_pack.annotation_ids) == {item.annotation_id for item in export_unit.evaluation_annotations}
    assert payload["coherence_report"]["all_annotations_in_pack"] is True
    assert payload["study_note"]["pain_classification"] == "evaluator_local_only"


def test_next_frontier_rl_replay_live_parity_packet_holds_on_new_workload() -> None:
    example = build_next_frontier_rl_replay_live_parity_packet()
    payload = build_next_frontier_rl_replay_live_parity_packet_payload()
    live_manifest = ExportManifest.from_dict(payload["live_export_manifest"])
    replay_manifest = ExportManifest.from_dict(payload["replay_export_manifest"])

    assert payload["workload_family"] == "dag_codetree_patch"
    assert payload["live_parity_view"] == payload["replay_parity_view"]
    assert live_manifest.fidelity_tier == "replay_parity_verified"
    assert replay_manifest.export_fingerprint == live_manifest.export_fingerprint


def test_next_frontier_rl_adapter_friction_synthesis_keeps_rl_frozen() -> None:
    packet = build_next_frontier_rl_adapter_friction_synthesis()
    payload = build_next_frontier_rl_adapter_friction_synthesis_payload()

    assert payload["recommended_outcome"] == "keep_rl_frozen"
    assert payload["repeated_shape_gap_detected"] is False
    assert len(packet["evidence_sources"]) == 3


def test_next_frontier_rl_second_trainer_packet_uses_new_workload_family() -> None:
    payload = build_next_frontier_rl_second_trainer_facing_export_packet_payload()
    manifest = ExportManifest.from_dict(payload["export_manifest"])
    evaluation_pack = EvaluationPackManifest.from_dict(payload["evaluation_pack"])

    assert payload["workload_family"] == "dag_moa_layered"
    assert manifest.fidelity_tier == "bounded_trainer_ready"
    assert evaluation_pack.evaluation_pack_id == manifest.evaluation_pack_id
    assert payload["study_note"]["pain_classification"] == "adapter_and_compaction_local_only"


def test_next_frontier_rl_second_evaluator_and_parity_packets_hold_on_new_loop() -> None:
    verifier_payload = build_next_frontier_rl_second_evaluator_verifier_packet_payload()
    parity_payload = build_next_frontier_rl_second_replay_live_parity_packet_payload()
    export_unit = DatasetExportUnit.from_dict(verifier_payload["export_unit"])
    evaluation_pack = EvaluationPackManifest.from_dict(verifier_payload["evaluation_pack"])
    live_manifest = ExportManifest.from_dict(parity_payload["live_export_manifest"])
    replay_manifest = ExportManifest.from_dict(parity_payload["replay_export_manifest"])

    assert verifier_payload["workload_family"] == "dag_codetree_patch"
    assert export_unit.export_kind == "verifier_example"
    assert set(evaluation_pack.annotation_ids) == {item.annotation_id for item in export_unit.evaluation_annotations}
    assert parity_payload["workload_family"] == "dag_moa_layered"
    assert parity_payload["live_parity_view"] == parity_payload["replay_parity_view"]
    assert replay_manifest.export_fingerprint == live_manifest.export_fingerprint


def test_next_frontier_rl_tranche_synthesis_v2_keeps_rl_frozen() -> None:
    payload = build_next_frontier_rl_tranche_synthesis_v2_payload()

    assert payload["recommended_outcome"] == "keep_rl_frozen"
    assert payload["repeated_shape_gap_detected"] is False
    assert payload["next_frontier_ready"] == "frontier_d_cross_system_composition"
    assert payload["metadata"]["loop_count"] == 2


def test_next_frontier_dag_to_rl_composition_packet_is_well_formed() -> None:
    packet = build_next_frontier_dag_to_rl_composition_packet()
    payload = build_next_frontier_dag_to_rl_composition_packet_payload()

    assert payload["handoff_contract"]["provenance_continuity"] is True
    assert payload["composition_report"]["composed_cleanly"] is True
    assert packet["composition_report"]["repeated_shape_gap_detected"] is False


def test_next_frontier_rl_final_closeout_packet_keeps_rl_frozen() -> None:
    packet = build_next_frontier_rl_final_closeout_packet()
    payload = build_next_frontier_rl_final_closeout_packet_payload()

    assert payload["final_decision"] == "keep_rl_frozen"
    assert payload["repeated_shape_gap_detected"] is False
    assert payload["reviewed_loops"] == 2
    assert "trainer-facing export packets" in packet["proven_capabilities"]
