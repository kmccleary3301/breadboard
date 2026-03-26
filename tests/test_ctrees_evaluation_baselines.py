from __future__ import annotations

from agentic_coder_prototype.ctrees.evaluation_baselines import build_deterministic_reranker_plan
from agentic_coder_prototype.ctrees.microprobes import build_false_neighbor_store, build_graph_neighborhood_store
from agentic_coder_prototype.ctrees.policy import build_rehydration_plan


def test_ctree_deterministic_reranker_prunes_structural_spillover() -> None:
    store = build_false_neighbor_store()

    baseline = build_rehydration_plan(store, mode="active_continuation")
    reranked = build_deterministic_reranker_plan(store, mode="active_continuation")

    baseline_ids = (baseline.get("rehydration_bundle") or {}).get("support_node_ids") or []
    reranked_ids = (reranked.get("rehydration_bundle") or {}).get("support_node_ids") or []

    assert len(reranked_ids) < len(baseline_ids)
    assert "ctn_000001" in baseline_ids
    assert "ctn_000001" not in reranked_ids
    assert reranked["reranker_proposal"]["selected_support_node_ids"] == reranked_ids
    assert "retrieval_contract.md" in (reranked.get("rehydration_bundle") or {}).get("artifact_refs") or []


def test_ctree_deterministic_reranker_preserves_dependency_grounding() -> None:
    store = build_graph_neighborhood_store()

    reranked = build_deterministic_reranker_plan(
        store,
        mode="dependency_lookup",
        graph_neighborhood_enabled=True,
    )
    bundle = reranked.get("rehydration_bundle") or {}

    assert reranked["focus_node_id"] == "ctn_000006"
    assert "ctn_000005" in bundle["support_node_ids"]
    assert "schema_validation.md" in bundle["artifact_refs"]
    assert reranked["reranker_proposal"]["selected_support_node_ids"] == bundle["support_node_ids"]
