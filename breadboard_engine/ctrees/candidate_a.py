from __future__ import annotations

from typing import Any, Dict, List, Optional

from .policy import build_retrieval_substrate
from .selector_features import (
    build_selector_feature_table,
    build_support_bundle_from_selection,
    candidate_lookup_from_substrate,
    node_by_id,
)
from .store import CTreeStore


def _dependency_satellite_candidates(
    store: CTreeStore,
    *,
    selected_node_ids: List[str],
    candidate_lookup: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    satellites: List[Dict[str, Any]] = []
    seen_ids = set(str(item) for item in list(selected_node_ids or []) if str(item))

    def _append(node_id: str, *, graph_type: str, reason: str, source_id: str, score: int) -> None:
        normalized_id = str(node_id or "").strip()
        if not normalized_id or normalized_id in seen_ids:
            return
        node = node_by_id(store, normalized_id)
        if not isinstance(node, dict):
            return
        status = str(node.get("status") or "")
        if status in {"superseded", "abandoned", "archived"} and graph_type != "supersedes":
            return
        seen_ids.add(normalized_id)
        satellites.append(
            {
                "node_id": normalized_id,
                "title": str(node.get("title") or ""),
                "lane": "dependency_satellite",
                "reason": reason,
                "graph_type": graph_type,
                "status": status,
                "base_score": int(score),
                "targets": [str(item) for item in list(node.get("targets") or []) if str(item)],
                "workspace_scope": [str(item) for item in list(node.get("workspace_scope") or []) if str(item)],
                "artifact_refs": [str(item) for item in list(node.get("artifact_refs") or []) if str(item)],
                "constraint_summaries": [
                    str(item.get("summary") or "").strip()
                    for item in list(node.get("constraints") or [])
                    if isinstance(item, dict) and str(item.get("summary") or "").strip()
                ],
                "blocker_ref": "",
                "neighbor_source_id": source_id,
                "features": {
                    "focus_node_anchor": False,
                    "active_path_member": False,
                    "direct_structural_relevance": False,
                    "ancestor_relation": False,
                    "ready_neighbor": False,
                    "objective_node": str(node.get("node_type") or "") == "objective",
                    "stale_or_conflict": status in {"superseded", "abandoned", "archived"} and graph_type != "supersedes",
                    "wrong_neighbor_marker": False,
                    "target_overlap_count": 0,
                    "workspace_overlap_count": 0,
                    "artifact_overlap_count": 0,
                    "constraint_overlap_count": 0,
                    "artifact_support": bool(list(node.get("artifact_refs") or [])),
                    "blocker_context": bool(list(node.get("blocker_refs") or [])),
                    "unresolved_blocker_match": False,
                    "validation_priority": graph_type == "validates",
                    "supersession_priority": graph_type == "supersedes",
                    "dense_support": False,
                    "lexical_support": False,
                    "direct_graph_support": False,
                    "neighbor_graph_support": False,
                },
            }
        )
        candidate_lookup[normalized_id] = satellites[-1]

    for node_id in list(selected_node_ids or []):
        node = node_by_id(store, str(node_id))
        if not isinstance(node, dict):
            continue
        for link in list(node.get("related_links") or []):
            if not isinstance(link, dict):
                continue
            link_type = str(link.get("type") or "").strip()
            target_id = str(link.get("target") or "").strip()
            if link_type == "validates":
                _append(target_id, graph_type="validates", reason="direct_validation_satellite", source_id=str(node_id), score=118)
            elif link_type == "supersedes":
                _append(target_id, graph_type="supersedes", reason="direct_supersession_satellite", source_id=str(node_id), score=116)
        for blocker_ref in list(node.get("blocker_refs") or []):
            blocker_id = str(blocker_ref or "").strip()
            if blocker_id:
                _append(blocker_id, graph_type="", reason="direct_prerequisite_satellite", source_id=str(node_id), score=108)

    return satellites


def _score_candidate_a_row(row: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
    features = dict(row.get("features") or {})
    score = int(row.get("base_score") or 0)
    reasons: List[str] = []

    if features.get("focus_node_anchor"):
        score += 120
        reasons.append("focus_node_anchor")
    if features.get("active_path_member"):
        score += 18
        reasons.append("active_path_membership")
    if features.get("direct_structural_relevance"):
        score += 12
        reasons.append("direct_structural_relevance")
    if features.get("lexical_support"):
        score += 10
        reasons.append("lexical_support")
    if features.get("direct_graph_support"):
        score += 16
        reasons.append("direct_graph_support")
    if features.get("neighbor_graph_support"):
        score += 8
        reasons.append("neighbor_graph_support")

    target_overlap_count = int(features.get("target_overlap_count") or 0)
    workspace_overlap_count = int(features.get("workspace_overlap_count") or 0)
    artifact_overlap_count = int(features.get("artifact_overlap_count") or 0)
    constraint_overlap_count = int(features.get("constraint_overlap_count") or 0)

    if target_overlap_count:
        score += 24 * target_overlap_count
        reasons.append("target_overlap")
    if workspace_overlap_count:
        score += 18 * workspace_overlap_count
        reasons.append("workspace_overlap")
    if artifact_overlap_count:
        score += 8 * artifact_overlap_count
        reasons.append("artifact_overlap")
    if constraint_overlap_count:
        score += 20 * constraint_overlap_count
        reasons.append("constraint_overlap")

    if features.get("blocker_context"):
        score += 10
        reasons.append("blocker_context")
    if features.get("unresolved_blocker_match"):
        score += 18
        reasons.append("unresolved_blocker_match")
    if features.get("validation_priority"):
        score += 18
        reasons.append("validation_priority")
    if features.get("supersession_priority"):
        score += 20
        reasons.append("supersession_priority")

    if features.get("ready_neighbor"):
        score -= 40
        reasons.append("ready_neighbor_penalty")
    if features.get("ancestor_relation"):
        score -= 22
        reasons.append("ancestor_penalty")
    if features.get("objective_node"):
        score -= 35
        reasons.append("objective_penalty")
    if features.get("wrong_neighbor_marker"):
        score -= 18
        reasons.append("wrong_neighbor_penalty")
    if features.get("stale_or_conflict"):
        score -= 70
        reasons.append("stale_or_conflict_penalty")

    if (
        mode in {"resume", "pivot", "active_continuation"}
        and not features.get("focus_node_anchor")
        and not target_overlap_count
        and not workspace_overlap_count
        and not constraint_overlap_count
        and not features.get("blocker_context")
        and not features.get("validation_priority")
        and not features.get("supersession_priority")
    ):
        score -= 20
        reasons.append("structural_spillover_penalty")

    if mode == "pivot" and features.get("neighbor_graph_support"):
        score -= 20
        reasons.append("pivot_neighbor_penalty")
    if mode == "dependency_lookup" and (features.get("direct_graph_support") or features.get("neighbor_graph_support")):
        score += 10
        reasons.append("dependency_graph_priority")
    if mode == "dependency_lookup" and features.get("ancestor_relation"):
        score -= 16
        reasons.append("dependency_ancestor_penalty")

    payload = dict(row)
    payload["candidate_a_score"] = score
    payload["candidate_a_reasons"] = reasons
    return payload


def build_candidate_a_proposal(
    store: CTreeStore,
    *,
    mode: str,
    retrieval_substrate: Dict[str, Any],
) -> Dict[str, Any]:
    feature_table = build_selector_feature_table(store, mode=mode, retrieval_substrate=retrieval_substrate)
    selected_limit = int(feature_table.get("selected_limit") or 2)
    if mode == "active_continuation":
        selected_limit = max(selected_limit, 3)
    focus_node_id = str(feature_table.get("focus_node_id") or "")
    ranked = [_score_candidate_a_row(row, mode=mode) for row in list(feature_table.get("rows") or [])]
    ranked.sort(key=lambda item: (-int(item.get("candidate_a_score") or 0), str(item.get("node_id") or "")))

    selected_node_ids: List[str] = []
    decisions: List[Dict[str, Any]] = []
    seen_ids = set()
    if focus_node_id:
        selected_node_ids.append(focus_node_id)
        seen_ids.add(focus_node_id)
        decisions.append({"node_id": focus_node_id, "decision": "keep", "selector_reason": "focus_node_anchor"})

    for item in ranked:
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in seen_ids:
            continue
        score = int(item.get("candidate_a_score") or 0)
        if score < 38:
            decisions.append({"node_id": node_id, "decision": "drop", "selector_reason": "below_threshold"})
            continue
        if len(selected_node_ids) >= selected_limit:
            decisions.append({"node_id": node_id, "decision": "drop", "selector_reason": "limit_reached"})
            continue
        selected_node_ids.append(node_id)
        seen_ids.add(node_id)
        decisions.append(
            {
                "node_id": node_id,
                "decision": "keep",
                "selector_reason": ",".join(list(item.get("candidate_a_reasons") or [])) or "candidate_a_keep",
            }
        )

    candidate_lookup = {
        str(row.get("node_id") or ""): dict(row)
        for row in list(ranked or [])
        if str(row.get("node_id") or "")
    }
    if mode == "dependency_lookup" and len(selected_node_ids) < selected_limit:
        for satellite in _dependency_satellite_candidates(
            store,
            selected_node_ids=selected_node_ids,
            candidate_lookup=candidate_lookup,
        ):
            satellite_id = str(satellite.get("node_id") or "")
            if not satellite_id or len(selected_node_ids) >= selected_limit:
                continue
            selected_node_ids.append(satellite_id)
            decisions.append(
                {
                    "node_id": satellite_id,
                    "decision": "keep",
                    "selector_reason": str(satellite.get("reason") or "dependency_satellite"),
                }
            )
            ranked.append(
                {
                    **dict(satellite),
                    "candidate_a_score": int(satellite.get("base_score") or 0),
                    "candidate_a_reasons": [str(satellite.get("reason") or "dependency_satellite")],
                }
            )

    return {
        "schema_version": "ctree_candidate_a_proposal_v1",
        "mode": mode,
        "focus_node_id": focus_node_id or None,
        "selected_limit": selected_limit,
        "selected_support_node_ids": selected_node_ids,
        "decisions": decisions,
        "ranked_candidates": ranked,
        "selector_feature_table": feature_table,
    }


def build_candidate_a_plan(
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
    proposal = build_candidate_a_proposal(store, mode=mode, retrieval_substrate=retrieval_substrate)
    retrieval_policy = dict(retrieval_substrate.get("retrieval_policy") or {})
    _, candidate_lookup = candidate_lookup_from_substrate(retrieval_substrate)
    for item in list(proposal.get("ranked_candidates") or []):
        node_id = str(item.get("node_id") or "")
        if node_id:
            candidate_lookup[node_id] = dict(item)
    packed = build_support_bundle_from_selection(
        store,
        mode=mode,
        retrieval_policy=retrieval_policy,
        candidate_lookup=candidate_lookup,
        selected_node_ids=[str(item) for item in list(proposal.get("selected_support_node_ids") or []) if str(item)],
        lean_artifacts=True,
        lean_active_context=True,
        selector_reasons_by_id={
            str(item.get("node_id") or ""): list(item.get("candidate_a_reasons") or [])
            for item in list(proposal.get("ranked_candidates") or [])
            if str(item.get("node_id") or "")
        },
    )
    return {
        "schema_version": "ctree_candidate_a_plan_v1",
        "mode": mode,
        "focus_node_id": retrieval_policy.get("focus_node_id"),
        "retrieval_policy": retrieval_policy,
        "retrieval_substrate": retrieval_substrate,
        "candidate_a_proposal": proposal,
        "restore_bundle": dict(packed.get("restore_bundle") or {}),
        "rehydration_bundle": dict(packed.get("rehydration_bundle") or {}),
    }
