from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .store import CTreeStore


_ID_RE = re.compile(r"^ctn_(\d+)$")
_GENERIC_LEXICAL_TERMS = {
    "objective",
    "task",
    "message",
    "probe",
    "phase",
    "work",
}


def _sort_node_ids(ids: List[str]) -> List[str]:
    def _key(value: str) -> tuple[int, int | str]:
        match = _ID_RE.match(value)
        if match:
            try:
                return (0, int(match.group(1)))
            except Exception:
                return (0, value)
        return (1, value)

    return sorted(ids, key=_key)


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


def _candidate_item(node: Dict[str, Any], *, lane: str, reason: str, score: int) -> Dict[str, Any]:
    return {
        "node_id": str(node.get("id") or ""),
        "title": str(node.get("title") or ""),
        "status": str(node.get("status") or ""),
        "path": str(node.get("path") or ""),
        "lane": lane,
        "reason": reason,
        "score": int(score),
        "targets": list(node.get("targets") or []),
        "workspace_scope": list(node.get("workspace_scope") or []),
        "artifact_refs": list(node.get("artifact_refs") or []),
        "blocker_refs": list(node.get("blocker_refs") or []),
        "constraint_count": len(list(node.get("constraints") or [])),
    }


def _clip_candidates(candidates: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("node_id") or ""),
        ),
    )
    return ordered[: max(limit, 0)]


def _focus_terms(store: CTreeStore, focus_node_id: Optional[str], active_path: List[str]) -> List[str]:
    terms: List[str] = []
    candidate_ids: List[str] = []
    if focus_node_id:
        candidate_ids.append(str(focus_node_id))
    elif active_path:
        candidate_ids.append(str(active_path[-1]))
    if len(active_path) >= 2:
        candidate_ids.append(str(active_path[-2]))
    seen = set()
    for node_id in candidate_ids:
        node = _node_by_id(store, str(node_id))
        if not isinstance(node, dict):
            continue
        for term in list(node.get("lexical_terms") or []):
            text = str(term).strip().lower()
            if not text or text in seen:
                continue
            if text in _GENERIC_LEXICAL_TERMS:
                continue
            if text.isdigit():
                continue
            if len(text) <= 2:
                continue
            seen.add(text)
            terms.append(text)
    return terms


def _collect_structural_candidates(
    store: CTreeStore,
    *,
    focus_node_id: Optional[str],
    active_path: List[str],
) -> List[Dict[str, Any]]:
    seen = set()
    candidates: List[Dict[str, Any]] = []

    if focus_node_id:
        focus_node = _node_by_id(store, str(focus_node_id))
        if isinstance(focus_node, dict):
            seen.add(str(focus_node.get("id") or ""))
            candidates.append(_candidate_item(focus_node, lane="structural", reason="focus_node", score=100))

    for offset, node_id in enumerate(reversed(active_path), start=1):
        node = _node_by_id(store, str(node_id))
        if not isinstance(node, dict):
            continue
        normalized_id = str(node.get("id") or "")
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        candidates.append(
            _candidate_item(
                node,
                lane="structural",
                reason="active_path" if offset == 1 else "ancestor",
                score=max(10, 95 - offset),
            )
        )

    for node in list(store.ready_nodes()):
        normalized_id = str(node.get("id") or "")
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        candidates.append(_candidate_item(node, lane="structural", reason="ready_neighbor", score=30))

    return candidates


def _collect_lexical_candidates(
    store: CTreeStore,
    *,
    focus_node_id: Optional[str],
    active_path: List[str],
) -> List[Dict[str, Any]]:
    terms = set(_focus_terms(store, focus_node_id, active_path))
    candidates: List[Dict[str, Any]] = []
    if not terms:
        return candidates
    for node in list(getattr(store, "nodes", []) or []):
        node_id = str(node.get("id") or "")
        if focus_node_id and node_id == str(focus_node_id):
            continue
        lexical_terms = {str(term).strip().lower() for term in list(node.get("lexical_terms") or []) if str(term).strip()}
        overlap = sorted(terms & lexical_terms)
        if not overlap:
            continue
        score = len(overlap) * 10
        if str(node.get("status") or "") in {"superseded", "abandoned", "archived"}:
            score -= 15
        if score < 30:
            continue
        candidates.append(
            {
                **_candidate_item(node, lane="lexical", reason="lexical_overlap", score=score),
                "matched_terms": overlap,
            }
        )
    return candidates


def _graph_candidate_item(
    *,
    blocker_ref: str,
    lane: str,
    reason: str,
    score: int,
    node: Optional[Dict[str, Any]] = None,
    graph_type: str = "blocks",
) -> Dict[str, Any]:
    if isinstance(node, dict):
        payload = _candidate_item(node, lane=lane, reason=reason, score=score)
    else:
        payload = {
            "node_id": f"blocker::{blocker_ref}",
            "title": blocker_ref,
            "status": "external_blocker",
            "path": "",
            "lane": lane,
            "reason": reason,
            "score": int(score),
            "targets": [],
            "workspace_scope": [],
            "artifact_refs": [],
            "blocker_refs": [],
            "constraint_count": 0,
        }
    payload["blocker_ref"] = blocker_ref
    payload["graph_type"] = graph_type
    return payload


def _collect_graph_link_candidates(
    store: CTreeStore,
    *,
    focus_node_id: Optional[str],
    active_path: List[str],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen = set()
    ordered_focus_ids: List[str] = []
    if focus_node_id:
        ordered_focus_ids.append(str(focus_node_id))
    for node_id in reversed(active_path):
        normalized = str(node_id)
        if normalized not in ordered_focus_ids:
            ordered_focus_ids.append(normalized)

    for node_id in ordered_focus_ids:
        node = _node_by_id(store, node_id)
        if not isinstance(node, dict):
            continue
        for blocker_ref in list(node.get("blocker_refs") or []):
            blocker_text = str(blocker_ref).strip()
            if not blocker_text or blocker_text in seen:
                continue
            seen.add(blocker_text)
            blocker_node = _node_by_id(store, blocker_text)
            candidates.append(
                _graph_candidate_item(
                    blocker_ref=blocker_text,
                    lane="graph_link",
                    reason="direct_blocker_node" if isinstance(blocker_node, dict) else "external_blocker_ref",
                    score=90 if isinstance(blocker_node, dict) else 70,
                    node=blocker_node,
                    graph_type="blocks",
                )
            )
        for link in list(node.get("related_links") or []):
            if not isinstance(link, dict):
                continue
            link_type = str(link.get("type") or "relates_to")
            target = str(link.get("target") or "").strip()
            if link_type not in {"blocks", "waits_for", "conditional_blocks", "validates"} or not target:
                continue
            key = f"{link_type}:{target}"
            if key in seen:
                continue
            seen.add(key)
            target_node = _node_by_id(store, target)
            candidates.append(
                _graph_candidate_item(
                    blocker_ref=target,
                    lane="graph_link",
                    reason=f"direct_{link_type}_link",
                    score=85 if isinstance(target_node, dict) else 65,
                    node=target_node,
                    graph_type=link_type,
                )
            )
    return candidates


def _collect_graph_neighborhood_candidates(
    store: CTreeStore,
    *,
    focus_node_id: Optional[str],
    active_path: List[str],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen = set()
    direct_targets = set()
    ordered_focus_ids: List[str] = []
    if focus_node_id:
        ordered_focus_ids.append(str(focus_node_id))
    for node_id in reversed(active_path):
        normalized = str(node_id)
        if normalized not in ordered_focus_ids:
            ordered_focus_ids.append(normalized)

    seed_nodes: List[Dict[str, Any]] = []
    seed_seen = set()

    for node_id in ordered_focus_ids:
        node = _node_by_id(store, node_id)
        if not isinstance(node, dict):
            continue
        for blocker_ref in list(node.get("blocker_refs") or []):
            blocker_text = str(blocker_ref).strip()
            if not blocker_text:
                continue
            direct_targets.add(blocker_text)
            blocker_node = _node_by_id(store, blocker_text)
            if isinstance(blocker_node, dict):
                blocker_id = str(blocker_node.get("id") or "")
                if blocker_id and blocker_id not in seed_seen:
                    seed_seen.add(blocker_id)
                    seed_nodes.append(blocker_node)
        for link in list(node.get("related_links") or []):
            if not isinstance(link, dict):
                continue
            link_type = str(link.get("type") or "relates_to")
            target = str(link.get("target") or "").strip()
            if link_type not in {"blocks", "waits_for", "conditional_blocks", "validates"} or not target:
                continue
            direct_targets.add(target)
            target_node = _node_by_id(store, target)
            if isinstance(target_node, dict):
                target_id = str(target_node.get("id") or "")
                if target_id and target_id not in seed_seen:
                    seed_seen.add(target_id)
                    seed_nodes.append(target_node)

    for seed_node in seed_nodes:
        seed_node_id = str(seed_node.get("id") or "")
        if not seed_node_id:
            continue

        for blocker_ref in list(seed_node.get("blocker_refs") or []):
            blocker_text = str(blocker_ref).strip()
            if not blocker_text or blocker_text in direct_targets:
                continue
            key = f"neighbor_blocker:{seed_node_id}:{blocker_text}"
            if key in seen:
                continue
            seen.add(key)
            blocker_node = _node_by_id(store, blocker_text)
            candidate = _graph_candidate_item(
                blocker_ref=blocker_text,
                lane="graph_neighborhood",
                reason="neighbor_blocker_ref",
                score=72 if isinstance(blocker_node, dict) else 54,
                node=blocker_node,
                graph_type="blocks",
            )
            candidate["neighbor_source_id"] = seed_node_id
            candidate["neighbor_hops"] = 1
            candidates.append(candidate)

        for link in list(seed_node.get("related_links") or []):
            if not isinstance(link, dict):
                continue
            link_type = str(link.get("type") or "relates_to")
            target = str(link.get("target") or "").strip()
            if link_type not in {"validates", "waits_for", "conditional_blocks", "supersedes"} or not target:
                continue
            if target in direct_targets:
                continue
            key = f"neighbor_link:{seed_node_id}:{link_type}:{target}"
            if key in seen:
                continue
            seen.add(key)
            target_node = _node_by_id(store, target)
            candidate = _graph_candidate_item(
                blocker_ref=target,
                lane="graph_neighborhood",
                reason=f"neighbor_{link_type}_link",
                score=70 if isinstance(target_node, dict) else 52,
                node=target_node,
                graph_type=link_type,
            )
            candidate["neighbor_source_id"] = seed_node_id
            candidate["neighbor_hops"] = 1
            candidates.append(candidate)

    return candidates


def active_path_node_ids(store: CTreeStore) -> List[str]:
    nodes = list(getattr(store, "nodes", []) or [])
    if not nodes:
        return []
    last_node = nodes[-1]
    if not isinstance(last_node, dict):
        return []
    ordered: List[str] = []
    seen = set()
    current: Optional[Dict[str, Any]] = last_node
    while isinstance(current, dict):
        current_id = str(current.get("id") or "")
        if not current_id or current_id in seen:
            break
        seen.add(current_id)
        ordered.append(current_id)
        parent_id = current.get("parent_id")
        if not parent_id:
            break
        current = _node_by_id(store, str(parent_id))
    ordered.reverse()
    return ordered


def resolve_retrieval_policy(
    store: CTreeStore,
    *,
    mode: str = "active_continuation",
    token_budget: Optional[int] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
) -> Dict[str, Any]:
    active_path = active_path_node_ids(store)
    snapshot = store.snapshot()
    last_node = snapshot.get("last_node") if isinstance(snapshot, dict) else None
    last_node_id = str(last_node.get("id") or "") if isinstance(last_node, dict) else None
    structural_sources = ["active_node", "ancestors", "latest_child_summaries"]
    lexical_sources = ["constraints", "targets", "workspace_scope", "artifact_refs", "titles", "paths"]
    graph_sources = ["blockers", "validates", "supersedes", "duplicates", "relates_to", "replies_to"]

    enabled_lanes = ["structural", "lexical"]
    lane_limits: Dict[str, int] = {"structural": 8, "lexical": 8}
    allowed_sources: Dict[str, List[str]] = {
        "structural": structural_sources,
        "lexical": lexical_sources,
    }
    restore_targets = ["active_path", "constraints", "targets"]

    if mode == "resume":
        lane_limits = {"structural": 10, "lexical": 10}
        restore_targets = ["active_path", "constraints", "targets", "workspace_scope"]
    elif mode == "pivot":
        lane_limits = {"structural": 6, "lexical": 10}
        restore_targets = ["constraints", "targets", "artifact_refs"]
    elif mode == "dependency_lookup":
        enabled_lanes = ["structural", "graph_link"] if graph_enabled else ["structural"]
        lane_limits = {"structural": 8, "graph_link": 8} if graph_enabled else {"structural": 8}
        allowed_sources = {"structural": ["active_node", "ancestors", "direct_blockers"]}
        if graph_enabled:
            allowed_sources["graph_link"] = graph_sources
            if graph_neighborhood_enabled:
                enabled_lanes.append("graph_neighborhood")
                lane_limits["graph_neighborhood"] = 4
                allowed_sources["graph_neighborhood"] = [
                    "one_hop_blocker_neighbors",
                    "one_hop_validation_neighbors",
                    "one_hop_supersession_neighbors",
                ]
        restore_targets = ["blockers", "validations"] if graph_enabled else ["blockers"]
        if graph_enabled and graph_neighborhood_enabled:
            restore_targets.append("neighbor_context")

    effective_budget = token_budget if token_budget is not None and token_budget > 0 else 1200
    return {
        "schema_version": "ctree_retrieval_policy_v1",
        "mode": mode,
        "enabled_lanes": enabled_lanes,
        "lane_limits": lane_limits,
        "allowed_sources": allowed_sources,
        "token_budget": effective_budget,
        "active_path_node_ids": active_path,
        "focus_node_id": last_node_id,
        "restore_targets": restore_targets,
    }


def build_retrieval_substrate(
    store: CTreeStore,
    *,
    mode: str = "active_continuation",
    token_budget: Optional[int] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
) -> Dict[str, Any]:
    retrieval_policy = resolve_retrieval_policy(
        store,
        mode=mode,
        token_budget=token_budget,
        graph_enabled=graph_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
    )
    active_path = list(retrieval_policy.get("active_path_node_ids") or [])
    focus_node_id = retrieval_policy.get("focus_node_id")
    lane_limits = dict(retrieval_policy.get("lane_limits") or {})
    candidate_support: Dict[str, List[Dict[str, Any]]] = {}

    if "structural" in list(retrieval_policy.get("enabled_lanes") or []):
        structural = _collect_structural_candidates(store, focus_node_id=focus_node_id, active_path=active_path)
        candidate_support["structural"] = _clip_candidates(structural, int(lane_limits.get("structural") or 0))

    if "lexical" in list(retrieval_policy.get("enabled_lanes") or []):
        lexical = _collect_lexical_candidates(store, focus_node_id=focus_node_id, active_path=active_path)
        candidate_support["lexical"] = _clip_candidates(lexical, int(lane_limits.get("lexical") or 0))

    if "graph_link" in list(retrieval_policy.get("enabled_lanes") or []):
        graph_candidates = _collect_graph_link_candidates(store, focus_node_id=focus_node_id, active_path=active_path)
        candidate_support["graph_link"] = _clip_candidates(graph_candidates, int(lane_limits.get("graph_link") or 0))

    if "graph_neighborhood" in list(retrieval_policy.get("enabled_lanes") or []):
        graph_neighbors = _collect_graph_neighborhood_candidates(store, focus_node_id=focus_node_id, active_path=active_path)
        candidate_support["graph_neighborhood"] = _clip_candidates(
            graph_neighbors,
            int(lane_limits.get("graph_neighborhood") or 0),
        )

    return {
        "schema_version": "ctree_retrieval_substrate_v1",
        "mode": mode,
        "focus_node_id": focus_node_id,
        "active_path_node_ids": active_path,
        "retrieval_policy": retrieval_policy,
        "candidate_support": candidate_support,
        "provenance": {
            "candidate_counts": {lane: len(items) for lane, items in candidate_support.items()},
            "lane_limits": lane_limits,
        },
    }


def build_rehydration_plan(
    store: CTreeStore,
    *,
    mode: str = "active_continuation",
    token_budget: Optional[int] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
) -> Dict[str, Any]:
    retrieval_substrate = build_retrieval_substrate(
        store,
        mode=mode,
        token_budget=token_budget,
        graph_enabled=graph_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
    )
    retrieval_policy = retrieval_substrate.get("retrieval_policy") or {}
    snapshot = store.snapshot()
    last_node = snapshot.get("last_node") if isinstance(snapshot, dict) else None
    focus_node_id = retrieval_policy.get("focus_node_id")
    active_path = list(retrieval_policy.get("active_path_node_ids") or [])
    constraints: List[Dict[str, Any]] = []
    targets: List[str] = []
    workspace_scope: List[str] = []
    artifact_refs: List[str] = []
    blocker_refs: List[str] = []
    validations: List[str] = []
    graph_neighbor_ids: List[str] = []
    superseded_source_ids: List[str] = []

    seen_constraints = set()
    seen_targets = set()
    seen_workspace_scope = set()
    seen_artifacts = set()
    seen_blocker_refs = set()
    seen_validations = set()
    seen_graph_neighbor_ids = set()
    seen_superseded_source_ids = set()

    for node_id in active_path:
        node = _node_by_id(store, str(node_id))
        if not isinstance(node, dict):
            continue
        for constraint in list(node.get("constraints") or []):
            if not isinstance(constraint, dict):
                continue
            key = (
                str(constraint.get("constraint_id") or ""),
                str(constraint.get("summary") or ""),
                str(constraint.get("scope") or ""),
            )
            if key in seen_constraints:
                continue
            seen_constraints.add(key)
            constraints.append(dict(constraint))
        for target in list(node.get("targets") or []):
            text = str(target)
            if text and text not in seen_targets:
                seen_targets.add(text)
                targets.append(text)
        for scope in list(node.get("workspace_scope") or []):
            text = str(scope)
            if text and text not in seen_workspace_scope:
                seen_workspace_scope.add(text)
                workspace_scope.append(text)
        for artifact in list(node.get("artifact_refs") or []):
            text = str(artifact)
            if text and text not in seen_artifacts:
                seen_artifacts.add(text)
                artifact_refs.append(text)

    if not constraints and isinstance(last_node, dict):
        constraints = list(last_node.get("constraints") or [])
        targets = list(last_node.get("targets") or [])
        workspace_scope = list(last_node.get("workspace_scope") or [])
        artifact_refs = list(last_node.get("artifact_refs") or [])
    if mode == "pivot":
        constraints = constraints[:3]
        artifact_refs = artifact_refs[:3]
        workspace_scope = workspace_scope[:1]

    promoted_candidates: List[Dict[str, Any]] = []
    seen_candidate_ids = set()
    for lane in ("structural", "lexical", "graph_link", "graph_neighborhood"):
        for candidate in list((retrieval_substrate.get("candidate_support") or {}).get(lane) or []):
            node_id = str(candidate.get("node_id") or "")
            if mode == "dependency_lookup":
                blocker_text = str(candidate.get("blocker_ref") or "").strip()
                if blocker_text and blocker_text not in seen_blocker_refs:
                    seen_blocker_refs.add(blocker_text)
                    blocker_refs.append(blocker_text)
                graph_type = str(candidate.get("graph_type") or "")
                if graph_type == "validates" and node_id not in seen_validations:
                    seen_validations.add(node_id)
                    validations.append(node_id)
                if lane == "graph_neighborhood" and node_id and node_id not in seen_graph_neighbor_ids:
                    seen_graph_neighbor_ids.add(node_id)
                    graph_neighbor_ids.append(node_id)
                if lane == "graph_neighborhood" and graph_type == "supersedes":
                    source_id = str(candidate.get("neighbor_source_id") or "").strip()
                    if source_id and source_id not in seen_superseded_source_ids:
                        seen_superseded_source_ids.add(source_id)
                        superseded_source_ids.append(source_id)
                for artifact in list(candidate.get("artifact_refs") or []):
                    text = str(artifact).strip()
                    if text and text not in seen_artifacts:
                        seen_artifacts.add(text)
                        artifact_refs.append(text)
                for target in list(candidate.get("targets") or []):
                    text = str(target).strip()
                    if text and text not in seen_targets:
                        seen_targets.add(text)
                        targets.append(text)
            if not node_id or node_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(node_id)
            promoted_candidates.append(
                {
                    "node_id": node_id,
                    "lane": str(candidate.get("lane") or lane),
                    "reason": str(candidate.get("reason") or ""),
                    "score": int(candidate.get("score") or 0),
                }
            )

    if mode == "pivot":
        promoted_candidates = promoted_candidates[:4]

    if mode == "dependency_lookup" and superseded_source_ids:
        superseded_artifacts = set()
        for source_id in superseded_source_ids:
            source_node = _node_by_id(store, source_id)
            if not isinstance(source_node, dict):
                continue
            for artifact in list(source_node.get("artifact_refs") or []):
                text = str(artifact).strip()
                if text:
                    superseded_artifacts.add(text)
        if superseded_artifacts:
            artifact_refs = [artifact for artifact in artifact_refs if artifact not in superseded_artifacts]

    return {
        "schema_version": "ctree_rehydration_bundle_v1",
        "mode": mode,
        "focus_node_id": focus_node_id,
        "token_budget": retrieval_policy.get("token_budget"),
        "dependency_coverage_target": "direct_only" if mode in {"active_continuation", "resume"} else "minimal",
        "restore_bundle": {
            "active_path_node_ids": list(retrieval_policy.get("active_path_node_ids") or []),
            "constraints": constraints,
            "targets": targets,
            "workspace_scope": workspace_scope,
            "artifact_refs": artifact_refs,
            "blocker_refs": blocker_refs,
            "validations": validations,
            "graph_neighbor_ids": graph_neighbor_ids,
            "superseded_source_ids": superseded_source_ids,
            "promoted_candidates": promoted_candidates,
        },
        "retrieval_substrate": retrieval_substrate,
        "rehydration_bundle": {
            "schema_version": "ctree_support_bundle_v1",
            "mode": mode,
            "focus_node_id": focus_node_id,
            "active_path_node_ids": list(retrieval_policy.get("active_path_node_ids") or []),
            "support_node_ids": [item["node_id"] for item in promoted_candidates],
            "constraints": constraints,
            "targets": targets,
            "workspace_scope": workspace_scope,
            "artifact_refs": artifact_refs,
            "blocker_refs": blocker_refs,
            "validations": validations,
            "graph_neighbor_ids": graph_neighbor_ids,
            "superseded_source_ids": superseded_source_ids,
            "candidate_provenance": promoted_candidates,
        },
        "retrieval_policy": retrieval_policy,
    }


def collapse_policy(store: CTreeStore, *, target: Optional[int] = None) -> Dict[str, Any]:
    """Deterministic tranche-1 collapse and retrieval policy surface."""

    nodes = getattr(store, "nodes", []) or []
    node_ids = [str(node.get("id")) for node in nodes if node.get("id") is not None]
    ordered_ids = _sort_node_ids(node_ids)
    node_count = len(ordered_ids)
    normalized_target = target if target is not None and target >= 0 else None

    drop: List[str] = []
    if normalized_target is not None and node_count > normalized_target:
        drop = ordered_ids[: node_count - normalized_target]

    remaining = [node_id for node_id in ordered_ids if node_id not in set(drop)]
    collapse: List[str] = []
    if len(remaining) > 1:
        collapse = remaining[:-1]

    stages = [
        {"stage": "RAW", "target": normalized_target, "drop": [], "collapse": [], "node_count": node_count},
        {"stage": "SPEC", "target": normalized_target, "drop": drop, "collapse": [], "node_count": node_count},
        {"stage": "HEADER", "target": normalized_target, "drop": drop, "collapse": collapse, "node_count": node_count},
        {
            "stage": "FROZEN",
            "target": normalized_target,
            "drop": drop,
            "collapse": collapse,
            "node_count": node_count,
        },
    ]

    retrieval_substrate = build_retrieval_substrate(store, mode="active_continuation")
    rehydration_plan = build_rehydration_plan(store, mode="active_continuation")

    return {
        "kind": "tranche1_policy",
        "schema_version": "ctree_policy_v1",
        "target": normalized_target,
        "drop": drop,
        "collapse": collapse,
        "node_count": node_count,
        "active_path_node_ids": active_path_node_ids(store),
        "ready_node_ids": [str(node.get("id")) for node in store.ready_nodes()],
        "retrieval_policy": retrieval_substrate.get("retrieval_policy"),
        "retrieval_substrate": retrieval_substrate,
        "rehydration_plan": rehydration_plan,
        "rehydration_bundle": rehydration_plan.get("rehydration_bundle"),
        "stages": stages,
    }
