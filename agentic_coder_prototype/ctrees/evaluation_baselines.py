from __future__ import annotations

from typing import Any, Dict, List, Optional

from .policy import build_retrieval_substrate
from .selector_features import (
    build_selector_feature_table,
    build_support_bundle_from_selection,
    candidate_lookup_from_substrate,
)
from .store import CTreeStore


def _deterministic_score_row(row: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
    features = dict(row.get("features") or {})
    rerank_score = int(row.get("base_score") or 0)
    rerank_reasons: List[str] = []

    if features.get("focus_node_anchor"):
        rerank_score += 110
        rerank_reasons.append("focus_node_anchor")

    if features.get("lexical_support"):
        rerank_score += 15
        rerank_reasons.append("lexical_support")
    if features.get("dense_support"):
        rerank_score += 20
        rerank_reasons.append("dense_support")
    if features.get("direct_graph_support"):
        rerank_score += 25
        rerank_reasons.append("direct_graph_support")
    if features.get("neighbor_graph_support"):
        rerank_score += 20
        rerank_reasons.append("neighbor_graph_support")

    if features.get("ready_neighbor"):
        rerank_score -= 35
        rerank_reasons.append("ready_neighbor_penalty")
    if features.get("ancestor_relation"):
        rerank_score -= 25
        rerank_reasons.append("ancestor_penalty")
    if features.get("objective_node"):
        rerank_score -= 30
        rerank_reasons.append("objective_penalty")
    if features.get("stale_or_conflict"):
        rerank_score -= 50
        rerank_reasons.append("stale_or_conflict_penalty")

    if int(features.get("target_overlap_count") or 0) > 0:
        rerank_score += 25
        rerank_reasons.append("target_overlap")
    if features.get("artifact_support"):
        rerank_score += 10
        rerank_reasons.append("artifact_support")
    if features.get("blocker_context"):
        rerank_score += 10
        rerank_reasons.append("blocker_context")
    if features.get("unresolved_blocker_match"):
        rerank_score += 20
        rerank_reasons.append("unresolved_blocker_match")
    if features.get("validation_priority"):
        rerank_score += 20
        rerank_reasons.append("validation_priority")
    if features.get("supersession_priority"):
        rerank_score += 25
        rerank_reasons.append("supersession_priority")

    if mode in {"resume", "pivot", "active_continuation"} and features.get("direct_structural_relevance"):
        rerank_score += 10
        rerank_reasons.append("continuation_focus")
    if mode == "pivot" and features.get("neighbor_graph_support"):
        rerank_score -= 15
        rerank_reasons.append("pivot_neighbor_penalty")
    if mode == "dependency_lookup" and (features.get("direct_graph_support") or features.get("neighbor_graph_support")):
        rerank_score += 12
        rerank_reasons.append("dependency_graph_priority")
    if mode == "dependency_lookup" and features.get("ancestor_relation"):
        rerank_score -= 20
        rerank_reasons.append("dependency_ancestor_penalty")

    payload = dict(row)
    payload["rerank_score"] = rerank_score
    payload["rerank_reasons"] = rerank_reasons
    return payload


def build_deterministic_reranker_proposal(
    store: CTreeStore,
    *,
    mode: str,
    retrieval_substrate: Dict[str, Any],
) -> Dict[str, Any]:
    feature_table = build_selector_feature_table(store, mode=mode, retrieval_substrate=retrieval_substrate)
    candidate_order = list(feature_table.get("candidate_order") or [])
    candidate_lookup = {
        str(row.get("node_id") or ""): {
            **dict(row),
            "score": int(row.get("base_score") or 0),
        }
        for row in list(feature_table.get("rows") or [])
        if str(row.get("node_id") or "")
    }
    focus_node_id = str(feature_table.get("focus_node_id") or "")
    selected_limit = int(feature_table.get("selected_limit") or 2)
    ranked: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []

    for node_id in candidate_order:
        ranked.append(_deterministic_score_row(candidate_lookup.get(node_id) or {}, mode=mode))

    ranked.sort(key=lambda item: (-int(item.get("rerank_score") or 0), str(item.get("node_id") or "")))

    selected_node_ids: List[str] = []
    seen_ids = set()
    if focus_node_id:
        selected_node_ids.append(focus_node_id)
        seen_ids.add(focus_node_id)
        decisions.append({"node_id": focus_node_id, "decision": "keep", "rerank_reason": "focus_node_anchor"})

    for item in ranked:
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in seen_ids:
            continue
        if int(item.get("rerank_score") or 0) < 30:
            decisions.append({"node_id": node_id, "decision": "drop", "rerank_reason": "below_threshold"})
            continue
        if len(selected_node_ids) >= selected_limit:
            decisions.append({"node_id": node_id, "decision": "drop", "rerank_reason": "limit_reached"})
            continue
        selected_node_ids.append(node_id)
        seen_ids.add(node_id)
        decisions.append(
            {
                "node_id": node_id,
                "decision": "keep",
                "rerank_reason": ",".join(list(item.get("rerank_reasons") or [])) or "ranked_keep",
            }
        )

    return {
        "schema_version": "ctree_deterministic_reranker_proposal_v1",
        "mode": mode,
        "focus_node_id": focus_node_id,
        "selected_limit": selected_limit,
        "selected_support_node_ids": selected_node_ids,
        "decisions": decisions,
        "ranked_candidates": ranked,
        "selector_feature_table": feature_table,
    }


def build_deterministic_reranker_plan(
    store: CTreeStore,
    *,
    mode: str = "active_continuation",
    token_budget: Optional[int] = None,
    focus_node_id: Optional[str] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
    dense_enabled: bool = False,
) -> Dict[str, Any]:
    retrieval_substrate = build_retrieval_substrate(
        store,
        mode=mode,
        token_budget=token_budget,
        focus_node_id=focus_node_id,
        graph_enabled=graph_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
        dense_enabled=dense_enabled,
    )
    proposal = build_deterministic_reranker_proposal(store, mode=mode, retrieval_substrate=retrieval_substrate)
    retrieval_policy = retrieval_substrate.get("retrieval_policy") or {}
    _, candidate_lookup = candidate_lookup_from_substrate(retrieval_substrate)
    packed = build_support_bundle_from_selection(
        store,
        mode=mode,
        retrieval_policy=retrieval_policy,
        candidate_lookup=candidate_lookup,
        selected_node_ids=[str(item) for item in list(proposal.get("selected_support_node_ids") or []) if str(item)],
        lean_artifacts=False,
        selector_reasons_by_id={
            str(item.get("node_id") or ""): list(item.get("rerank_reasons") or [])
            for item in list(proposal.get("ranked_candidates") or [])
            if str(item.get("node_id") or "")
        },
    )
    return {
        "schema_version": "ctree_deterministic_reranker_plan_v1",
        "mode": mode,
        "focus_node_id": retrieval_policy.get("focus_node_id"),
        "retrieval_policy": retrieval_policy,
        "retrieval_substrate": retrieval_substrate,
        "reranker_proposal": proposal,
        "restore_bundle": dict(packed.get("restore_bundle") or {}),
        "rehydration_bundle": dict(packed.get("rehydration_bundle") or {}),
    }
