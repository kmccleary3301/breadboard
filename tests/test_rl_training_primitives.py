from __future__ import annotations

from agentic_coder_prototype.rl import (
    AdapterCapabilities,
    CompactionManifest,
    CostLedger,
    CreditFrame,
    DatasetExportUnit,
    EnvironmentDescriptor,
    EvaluationAnnotation,
    PolicyProvenance,
    RolloutDescriptor,
    TrajectoryGraph,
    build_rl_v1_alpha_exporters_example,
    build_rl_v1_alpha_exporters_example_payload,
    build_rl_v1_boundary_audit_packet,
    build_rl_v1_boundary_audit_packet_payload,
    build_rl_v1_contract_pack_example,
    build_rl_v1_contract_pack_example_payload,
    build_rl_v1_live_projection_example,
    build_rl_v1_live_projection_example_payload,
    build_rl_v1_multi_agent_async_hardening_example,
    build_rl_v1_multi_agent_async_hardening_example_payload,
    build_rl_v1_replay_parity_example,
    build_rl_v1_replay_parity_example_payload,
    build_rl_v1_replay_projection_example,
    build_rl_v1_replay_projection_example_payload,
    build_rl_v1_trajectory_graph_shell_example,
    build_rl_v1_trajectory_graph_shell_example_payload,
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
