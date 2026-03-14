from __future__ import annotations

from agentic_coder_prototype.ctrees.microprobes import (
    run_blocker_clearance_probe,
    run_continuation_probe,
    run_dependency_lookup_probe,
    run_false_neighbor_probe,
    run_graph_neighborhood_probe,
    run_helper_dependency_lookup_probe,
    run_helper_false_neighbor_probe,
    run_helper_resume_probe,
    run_helper_subtree_summary_probe,
    run_pivot_probe,
    run_pivot_minimality_probe,
    run_resume_probe,
    run_stale_verification_probe,
    run_target_supersession_probe,
)


def test_ctree_continuation_microprobe_exposes_ready_and_blocker_state() -> None:
    payload = run_continuation_probe()

    assert payload["probe"] == "continuation"
    assert payload["active_path_node_ids"]
    assert payload["ready_node_ids"]
    assert payload["unresolved_blocker_count"] >= 1
    assert payload["prompt_planes"]["reduced_task_state"]["unresolved_blocker_refs"]
    assert payload["retrieval_substrate"]["candidate_support"]["structural"]


def test_ctree_resume_microprobe_exposes_restore_bundle() -> None:
    payload = run_resume_probe()

    assert payload["probe"] == "resume"
    restore_bundle = payload["restore_bundle"]
    assert payload["focus_node_id"]
    assert restore_bundle["active_path_node_ids"]
    assert restore_bundle["constraints"]
    assert payload["support_bundle"]["support_node_ids"]


def test_ctree_pivot_microprobe_prefers_artifact_and_constraint_restore() -> None:
    payload = run_pivot_probe()

    assert payload["probe"] == "pivot"
    assert payload["enabled_lanes"] == ["structural", "lexical"]
    assert "artifact_refs" in payload["restore_targets"]
    assert payload["artifact_refs"]
    assert len(payload["workspace_scope"]) <= 1
    assert len(payload["promoted_candidates"]) <= 4


def test_ctree_target_supersession_probe_prefers_active_target() -> None:
    payload = run_target_supersession_probe()

    assert payload["probe"] == "target_supersession"
    assert "active_target.py" in payload["active_target_set"]
    assert "legacy_target.py" not in payload["support_targets"]
    assert payload["conflict_node_ids"]


def test_ctree_blocker_clearance_probe_tracks_ready_transition() -> None:
    payload = run_blocker_clearance_probe()

    assert payload["probe"] == "blocker_clearance"
    assert payload["blocked_needs_resolution"] is True
    assert payload["cleared_needs_resolution"] is False
    assert len(payload["cleared_ready_node_ids"]) >= len(payload["blocked_ready_node_ids"])


def test_ctree_stale_verification_probe_prefers_fresh_support() -> None:
    payload = run_stale_verification_probe()

    assert payload["probe"] == "stale_verification_dominance"
    assert payload["support_node_ids"]
    assert "verification_fresh.md" in payload["artifact_refs"]
    assert "verification_old.md" not in payload["artifact_refs"]


def test_ctree_false_neighbor_probe_suppresses_irrelevant_neighbor() -> None:
    payload = run_false_neighbor_probe()

    assert payload["probe"] == "false_neighbor_suppression"
    assert payload["lexical_candidate_ids"]
    matched_terms = payload["matched_terms"]
    assert any(any("ctrees" in term or "policy" in term for term in terms) for terms in matched_terms.values())
    assert len(payload["lexical_candidate_ids"]) == 1


def test_ctree_pivot_minimality_probe_clips_support() -> None:
    payload = run_pivot_minimality_probe()

    assert payload["probe"] == "pivot_minimality"
    assert len(payload["workspace_scope"]) <= 1
    assert len(payload["support_node_ids"]) <= 4
    assert payload["candidate_provenance"]


def test_ctree_dependency_lookup_probe_surfaces_blocker_graph_support() -> None:
    payload = run_dependency_lookup_probe()

    assert payload["probe"] == "dependency_lookup"
    assert payload["enabled_lanes"] == ["structural", "graph_link"]
    assert payload["graph_candidate_ids"]
    assert any(reason.startswith("direct_") for reason in payload["graph_candidate_reasons"])
    assert payload["blocker_refs"]
    assert payload["validations"]
    assert "schema_spec.md" in payload["artifact_refs"]


def test_ctree_graph_neighborhood_probe_surfaces_one_hop_neighbor_support() -> None:
    payload = run_graph_neighborhood_probe()

    assert payload["probe"] == "graph_neighborhood_dependency_lookup"
    assert payload["enabled_lanes"] == ["structural", "graph_link", "graph_neighborhood"]
    assert "neighbor_context" in payload["restore_targets"]
    assert payload["graph_neighbor_ids"]
    assert any(reason.startswith("neighbor_") for reason in payload["graph_neighbor_reasons"])
    assert payload["neighbor_source_ids"]
    assert "schema_validation.md" in payload["artifact_refs"]
    assert payload["graph_neighbor_bundle_ids"]


def test_ctree_helper_resume_probe_prunes_baseline_support() -> None:
    payload = run_helper_resume_probe()

    assert payload["probe"] == "helper_resume"
    assert len(payload["helper_support_node_ids"]) < len(payload["baseline_support_node_ids"])
    assert payload["helper_proposal"]["selected_support_node_ids"] == payload["helper_support_node_ids"]


def test_ctree_helper_false_neighbor_probe_keeps_relevant_and_drops_irrelevant_nodes() -> None:
    payload = run_helper_false_neighbor_probe()

    assert payload["probe"] == "helper_false_neighbor"
    assert len(payload["helper_support_node_ids"]) < len(payload["baseline_support_node_ids"])
    assert "retrieval_contract.md" in payload["helper_artifact_refs"]
    assert payload["helper_proposal"]["selected_support_node_ids"] == payload["helper_support_node_ids"]


def test_ctree_helper_dependency_lookup_probe_keeps_dependency_grounding() -> None:
    payload = run_helper_dependency_lookup_probe()

    assert payload["probe"] == "helper_dependency_lookup"
    assert len(payload["helper_support_node_ids"]) < len(payload["baseline_support_node_ids"])
    assert "schema_validation.md" in payload["helper_artifact_refs"]
    assert "replacement_schema.md" in payload["helper_artifact_refs"]
    assert payload["helper_proposal"]["selected_support_node_ids"] == payload["helper_support_node_ids"]


def test_ctree_helper_subtree_summary_probe_surfaces_grounded_summary() -> None:
    payload = run_helper_subtree_summary_probe()

    assert payload["probe"] == "helper_subtree_summary"
    assert payload["helper_prompt_plane_has_summaries"] is True
    assert payload["helper_summary"]["selected_child_ids"]
    assert "Blocked child" in payload["helper_summary"]["header_summary"]
