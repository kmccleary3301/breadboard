from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import CTreeStore


def _node_by_id(store: CTreeStore, node_id: str) -> Optional[Dict[str, Any]]:
    mapping = getattr(store, "_node_by_id", None)
    if isinstance(mapping, dict) and node_id in mapping:
        value = mapping.get(node_id)
        if isinstance(value, dict):
            return value
    for node in list(getattr(store, "nodes", []) or []):
        if isinstance(node, dict) and str(node.get("id") or "") == str(node_id):
            return node
    return None


def _flatten_candidates(retrieval_substrate: Dict[str, Any]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    candidate_support = retrieval_substrate.get("candidate_support") or {}
    for lane, items in candidate_support.items():
        for item in list(items or []):
            if not isinstance(item, dict):
                continue
            payload = dict(item)
            payload.setdefault("lane", str(lane))
            flattened.append(payload)
    return flattened


def build_helper_rehydration_input(
    store: CTreeStore,
    *,
    mode: str,
    retrieval_substrate: Dict[str, Any],
) -> Dict[str, Any]:
    active_path_node_ids = list(retrieval_substrate.get("active_path_node_ids") or [])
    active_target_set: List[str] = []
    unresolved_blocker_refs: List[str] = []
    conflict_node_ids: List[str] = []
    seen_targets = set()
    seen_blockers = set()

    for node in list(getattr(store, "nodes", []) or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        status = str(node.get("status") or "")
        if status in {"superseded", "abandoned"} and node_id:
            conflict_node_ids.append(node_id)
        if node_id not in active_path_node_ids:
            continue
        for target in list(node.get("targets") or []):
            text = str(target).strip()
            if text and text not in seen_targets:
                seen_targets.add(text)
                active_target_set.append(text)
        for blocker in list(node.get("blocker_refs") or []):
            text = str(blocker).strip()
            if text and text not in seen_blockers:
                seen_blockers.add(text)
                unresolved_blocker_refs.append(text)

    return {
        "schema_version": "ctree_helper_rehydration_input_v1",
        "mode": mode,
        "focus_node_id": retrieval_substrate.get("focus_node_id"),
        "active_path_node_ids": active_path_node_ids,
        "token_budget": (retrieval_substrate.get("retrieval_policy") or {}).get("token_budget"),
        "reduced_task_state": {
            "active_target_set": active_target_set,
            "unresolved_blocker_refs": unresolved_blocker_refs,
            "conflict_node_ids": sorted(conflict_node_ids),
            "needs_resolution": bool(unresolved_blocker_refs),
        },
        "retrieval_substrate": retrieval_substrate,
        "candidate_count": sum(len(list(items or [])) for items in list((retrieval_substrate.get("candidate_support") or {}).values())),
    }


def build_helper_rehydration_proposal(
    store: CTreeStore,
    helper_input: Dict[str, Any],
) -> Dict[str, Any]:
    mode = str(helper_input.get("mode") or "active_continuation")
    focus_node_id = str(helper_input.get("focus_node_id") or "")
    reduced_state = helper_input.get("reduced_task_state") or {}
    active_target_set = {str(item) for item in list(reduced_state.get("active_target_set") or []) if str(item)}
    unresolved_blockers = {str(item) for item in list(reduced_state.get("unresolved_blocker_refs") or []) if str(item)}
    conflict_node_ids = {str(item) for item in list(reduced_state.get("conflict_node_ids") or []) if str(item)}
    candidates = _flatten_candidates(helper_input.get("retrieval_substrate") or {})

    limit_map = {
        "active_continuation": 2,
        "resume": 2,
        "pivot": 2,
        "dependency_lookup": 4,
    }
    selected_limit = int(limit_map.get(mode, 2))
    decisions: List[Dict[str, Any]] = []
    ranked: List[Dict[str, Any]] = []

    for candidate in candidates:
        node_id = str(candidate.get("node_id") or "")
        reason = str(candidate.get("reason") or "")
        lane = str(candidate.get("lane") or "")
        graph_type = str(candidate.get("graph_type") or "")
        status = str(candidate.get("status") or "")
        title = str(candidate.get("title") or "")
        node = _node_by_id(store, node_id)
        node_type = str((node or {}).get("node_type") or "")
        score = int(candidate.get("score") or 0)
        helper_score = score
        helper_reasons: List[str] = []

        if node_id == focus_node_id:
            helper_score += 120
            helper_reasons.append("focus_node_anchor")

        if lane == "lexical":
            helper_score += 20
            helper_reasons.append("lexical_support")

        if lane == "graph_link":
            helper_score += 35
            helper_reasons.append("direct_graph_support")

        if lane == "graph_neighborhood":
            helper_score += 30
            helper_reasons.append("neighbor_graph_support")

        if reason == "ready_neighbor":
            helper_score -= 40
            helper_reasons.append("ready_neighbor_penalty")

        if reason == "ancestor":
            helper_score -= 35
            helper_reasons.append("ancestor_penalty")

        if node_type == "objective":
            helper_score -= 40
            helper_reasons.append("objective_penalty")

        if status in {"superseded", "abandoned", "archived"} or node_id in conflict_node_ids:
            helper_score -= 60
            helper_reasons.append("stale_or_conflict_penalty")

        candidate_targets = {str(item) for item in list(candidate.get("targets") or []) if str(item)}
        if candidate_targets & active_target_set:
            helper_score += 30
            helper_reasons.append("target_overlap")

        if list(candidate.get("artifact_refs") or []):
            helper_score += 10
            helper_reasons.append("artifact_support")

        if list(candidate.get("blocker_refs") or []):
            helper_score += 10
            helper_reasons.append("blocker_context")

        if graph_type == "validates":
            helper_score += 25
            helper_reasons.append("validation_priority")

        if graph_type == "supersedes":
            helper_score += 30
            helper_reasons.append("supersession_priority")

        blocker_ref = str(candidate.get("blocker_ref") or "").strip()
        if blocker_ref and blocker_ref in unresolved_blockers:
            helper_score += 20
            helper_reasons.append("unresolved_blocker_match")

        if mode in {"resume", "pivot", "active_continuation"} and lane == "structural" and reason == "focus_node":
            helper_score += 15
            helper_reasons.append("continuation_focus")

        if mode == "pivot" and lane == "graph_neighborhood":
            helper_score -= 20
            helper_reasons.append("pivot_neighbor_penalty")

        if mode == "dependency_lookup" and lane in {"graph_link", "graph_neighborhood"}:
            helper_score += 15
            helper_reasons.append("dependency_graph_priority")

        if mode == "dependency_lookup" and lane == "structural" and reason == "ancestor":
            helper_score -= 25
            helper_reasons.append("dependency_ancestor_penalty")

        ranked.append(
            {
                "node_id": node_id,
                "helper_score": helper_score,
                "lane": lane,
                "reason": reason,
                "graph_type": graph_type,
                "title": title,
                "helper_reasons": helper_reasons,
            }
        )

    ranked.sort(key=lambda item: (-int(item.get("helper_score") or 0), str(item.get("node_id") or "")))

    selected_node_ids: List[str] = []
    seen_ids = set()
    if focus_node_id:
        selected_node_ids.append(focus_node_id)
        seen_ids.add(focus_node_id)
        decisions.append(
            {
                "node_id": focus_node_id,
                "decision": "keep",
                "helper_reason": "focus_node_anchor",
            }
        )

    for item in ranked:
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in seen_ids:
            continue
        if int(item.get("helper_score") or 0) < 35:
            decisions.append(
                {
                    "node_id": node_id,
                    "decision": "drop",
                    "helper_reason": "below_threshold",
                }
            )
            continue
        if len(selected_node_ids) >= selected_limit:
            decisions.append(
                {
                    "node_id": node_id,
                    "decision": "drop",
                    "helper_reason": "limit_reached",
                }
            )
            continue
        selected_node_ids.append(node_id)
        seen_ids.add(node_id)
        decisions.append(
            {
                "node_id": node_id,
                "decision": "keep",
                "helper_reason": ",".join(list(item.get("helper_reasons") or [])) or "ranked_keep",
            }
        )

    return {
        "schema_version": "ctree_helper_rehydration_proposal_v1",
        "mode": mode,
        "focus_node_id": focus_node_id or None,
        "selected_support_node_ids": selected_node_ids,
        "selected_limit": selected_limit,
        "decisions": decisions,
        "ranked_candidates": ranked,
    }
