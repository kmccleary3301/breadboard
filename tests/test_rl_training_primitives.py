from __future__ import annotations

from agentic_coder_prototype.rl import (
    AdapterCapabilities,
    CompactionManifest,
    CostLedger,
    EnvironmentDescriptor,
    EvaluationAnnotation,
    PolicyProvenance,
    RolloutDescriptor,
    TrajectoryGraph,
    build_rl_v1_boundary_audit_packet,
    build_rl_v1_boundary_audit_packet_payload,
    build_rl_v1_contract_pack_example,
    build_rl_v1_contract_pack_example_payload,
    build_rl_v1_live_projection_example,
    build_rl_v1_live_projection_example_payload,
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
