from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .helper_rehydration import build_helper_rehydration_input, build_helper_rehydration_proposal
from .helper_subtree_summary import build_helper_subtree_summary_input, build_helper_subtree_summary_proposal
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
_DENSE_TERM_ALIASES = {
    "verify": "validate",
    "verified": "validate",
    "verification": "validate",
    "validate": "validate",
    "validated": "validate",
    "validates": "validate",
    "validation": "validate",
    "check": "validate",
    "checks": "validate",
    "checked": "validate",
    "compiler": "compile",
    "compile": "compile",
    "compiling": "compile",
    "compiled": "compile",
    "compilation": "compile",
    "continuation": "resume",
    "continue": "resume",
    "continued": "resume",
    "continuing": "resume",
    "resume": "resume",
    "resumed": "resume",
    "rehydrate": "resume",
    "rehydration": "resume",
    "restore": "resume",
    "restored": "resume",
    "restoration": "resume",
    "planner": "plan",
    "planning": "plan",
}
_DENSE_SUFFIXES = ("ation", "tion", "ment", "ing", "ers", "ies", "ied", "ed", "er", "or", "ly", "es", "s")
_LANE_PROFILES = {
    "frozen_core": {
        "graph_enabled": True,
        "graph_neighborhood_enabled": False,
        "dense_enabled": False,
        "helper_enabled": False,
        "helper_summary_coupling_enabled": False,
    },
    "graph_neighborhood": {
        "graph_enabled": True,
        "graph_neighborhood_enabled": True,
        "dense_enabled": False,
        "helper_enabled": False,
        "helper_summary_coupling_enabled": False,
    },
    "helper_rehydration": {
        "graph_enabled": True,
        "graph_neighborhood_enabled": False,
        "dense_enabled": False,
        "helper_enabled": True,
        "helper_summary_coupling_enabled": False,
    },
    "summary_coupling": {
        "graph_enabled": True,
        "graph_neighborhood_enabled": False,
        "dense_enabled": False,
        "helper_enabled": True,
        "helper_summary_coupling_enabled": True,
    },
    "dense_retrieval": {
        "graph_enabled": True,
        "graph_neighborhood_enabled": False,
        "dense_enabled": True,
        "helper_enabled": True,
        "helper_summary_coupling_enabled": False,
    },
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


def resolve_focus_node_id(store: CTreeStore, focus_node_id: Optional[str] = None) -> Optional[str]:
    if focus_node_id:
        node = _node_by_id(store, str(focus_node_id))
        if isinstance(node, dict):
            normalized_id = str(node.get("id") or "").strip()
            if normalized_id:
                return normalized_id
    snapshot = store.snapshot()
    last_node = snapshot.get("last_node") if isinstance(snapshot, dict) else None
    last_node_id = str(last_node.get("id") or "").strip() if isinstance(last_node, dict) else ""
    return last_node_id or None


def resolve_lane_profile(
    lane_profile: Optional[str] = None,
    *,
    graph_enabled: bool,
    graph_neighborhood_enabled: bool,
    dense_enabled: bool,
    helper_enabled: bool,
    helper_summary_coupling_enabled: bool,
) -> Dict[str, Any]:
    profile_name = str(lane_profile or "custom").strip() or "custom"
    profile_defaults = dict(_LANE_PROFILES.get(profile_name) or {})
    if not profile_defaults:
        return {
            "profile": "custom",
            "graph_enabled": bool(graph_enabled),
            "graph_neighborhood_enabled": bool(graph_neighborhood_enabled),
            "dense_enabled": bool(dense_enabled),
            "helper_enabled": bool(helper_enabled),
            "helper_summary_coupling_enabled": bool(helper_summary_coupling_enabled),
        }
    return {
        "profile": profile_name,
        "graph_enabled": bool(profile_defaults.get("graph_enabled")),
        "graph_neighborhood_enabled": bool(profile_defaults.get("graph_neighborhood_enabled")),
        "dense_enabled": bool(profile_defaults.get("dense_enabled")),
        "helper_enabled": bool(profile_defaults.get("helper_enabled")),
        "helper_summary_coupling_enabled": bool(profile_defaults.get("helper_summary_coupling_enabled")),
    }


def _simple_child_state(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "node_id": str(node.get("id") or ""),
        "title": str(node.get("title") or ""),
        "status": str(node.get("status") or ""),
        "artifact_refs": list(node.get("artifact_refs") or []),
        "targets": list(node.get("targets") or []),
        "blocker_refs": list(node.get("blocker_refs") or []),
        "final_spec_present": node.get("final_spec") is not None,
    }


def _simple_parent_reduction(parent_node: Dict[str, Any], child_states: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "node_id": str(parent_node.get("id") or ""),
        "title": str(parent_node.get("title") or ""),
        "unresolved_blocker_set": sorted(
            {
                str(blocker)
                for child in child_states
                for blocker in list(child.get("blocker_refs") or [])
                if str(blocker)
            }
        ),
        "artifact_summary": sorted(
            {
                str(artifact)
                for child in child_states
                for artifact in list(child.get("artifact_refs") or [])
                if str(artifact)
            }
        ),
        "active_target_set": sorted(
            {
                str(target)
                for child in child_states
                for target in list(child.get("targets") or [])
                if str(target)
            }
        ),
    }


def _build_active_path_subtree_summary_proposals(store: CTreeStore, active_path: List[str]) -> List[Dict[str, Any]]:
    children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for node in list(getattr(store, "nodes", []) or []):
        if not isinstance(node, dict):
            continue
        parent_id = str(node.get("parent_id") or "").strip()
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(node)

    proposals: List[Dict[str, Any]] = []
    for node_id in active_path:
        children = children_by_parent.get(str(node_id), [])
        if not children:
            continue
        parent_node = _node_by_id(store, str(node_id))
        if not isinstance(parent_node, dict):
            continue
        child_states = [_simple_child_state(child) for child in children]
        helper_input = build_helper_subtree_summary_input(
            parent_node=parent_node,
            parent_reduction=_simple_parent_reduction(parent_node, child_states),
            child_states=child_states,
        )
        proposals.append(build_helper_subtree_summary_proposal(helper_input))
    return proposals


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


def _candidate_preference(candidate: Dict[str, Any]) -> tuple[int, int, int]:
    lane = str(candidate.get("lane") or "")
    reason = str(candidate.get("reason") or "")
    graph_type = str(candidate.get("graph_type") or "")

    lane_weight = {
        "graph_link": 4,
        "dense": 3,
        "graph_neighborhood": 3,
        "lexical": 2,
        "structural": 1,
    }.get(lane, 0)
    reason_weight = {
        "focus_node": 5,
        "active_path": 4,
        "direct_validates_link": 4,
        "direct_blocker_node": 4,
        "neighbor_supersedes_link": 4,
        "neighbor_validates_link": 4,
        "lexical_overlap": 3,
        "semantic_overlap": 3,
        "ancestor": 2,
        "neighbor_blocker_ref": 2,
        "ready_neighbor": 1,
        "external_blocker_ref": 1,
    }.get(reason, 0)
    graph_weight = {
        "validates": 3,
        "supersedes": 3,
        "blocks": 2,
        "waits_for": 1,
        "conditional_blocks": 1,
    }.get(graph_type, 0)
    return (lane_weight, reason_weight, graph_weight)


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


def _normalize_dense_term(term: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "", str(term).strip().lower())
    if not text:
        return ""
    alias = _DENSE_TERM_ALIASES.get(text)
    if alias:
        return alias
    for suffix in _DENSE_SUFFIXES:
        if len(text) > len(suffix) + 2 and text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return _DENSE_TERM_ALIASES.get(text, text)


def _dense_terms_from_tokens(tokens: List[str]) -> List[str]:
    seen = set()
    dense_terms: List[str] = []
    for token in tokens:
        normalized = _normalize_dense_term(token)
        if not normalized or normalized in seen:
            continue
        if normalized in _GENERIC_LEXICAL_TERMS:
            continue
        if len(normalized) <= 3:
            continue
        seen.add(normalized)
        dense_terms.append(normalized)
    return dense_terms


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


def _collect_dense_candidates(
    store: CTreeStore,
    *,
    focus_node_id: Optional[str],
    active_path: List[str],
) -> List[Dict[str, Any]]:
    focus_raw_terms = set(_focus_terms(store, focus_node_id, active_path))
    focus_dense_terms = set(_dense_terms_from_tokens(list(focus_raw_terms)))
    if not focus_dense_terms:
        return []

    candidates: List[Dict[str, Any]] = []
    normalized_focus_raw = {_normalize_dense_term(term) for term in focus_raw_terms if _normalize_dense_term(term)}
    for node in list(getattr(store, "nodes", []) or []):
        node_id = str(node.get("id") or "")
        if focus_node_id and node_id == str(focus_node_id):
            continue
        raw_terms = {str(term).strip().lower() for term in list(node.get("lexical_terms") or []) if str(term).strip()}
        exact_overlap = sorted(focus_raw_terms & raw_terms)
        dense_terms = set(_dense_terms_from_tokens(list(raw_terms)))
        dense_overlap = sorted(focus_dense_terms & dense_terms)
        semantic_only = [term for term in dense_overlap if term not in normalized_focus_raw or term not in exact_overlap]
        if not semantic_only:
            continue
        score = 24 + (len(semantic_only) * 12)
        if list(node.get("artifact_refs") or []):
            score += 6
        if list(node.get("constraints") or []):
            score += 6
        if str(node.get("status") or "") in {"superseded", "abandoned", "archived"}:
            score -= 18
        if score < 30:
            continue
        candidates.append(
            {
                **_candidate_item(node, lane="dense", reason="semantic_overlap", score=score),
                "matched_dense_terms": semantic_only,
                "matched_exact_terms": exact_overlap,
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


def active_path_node_ids(store: CTreeStore, focus_node_id: Optional[str] = None) -> List[str]:
    effective_focus_node_id = resolve_focus_node_id(store, focus_node_id)
    if not effective_focus_node_id:
        return []
    ordered: List[str] = []
    seen = set()
    current = _node_by_id(store, effective_focus_node_id)
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
    focus_node_id: Optional[str] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
    dense_enabled: bool = False,
) -> Dict[str, Any]:
    effective_focus_node_id = resolve_focus_node_id(store, focus_node_id)
    active_path = active_path_node_ids(store, effective_focus_node_id)
    structural_sources = ["active_node", "ancestors", "latest_child_summaries"]
    lexical_sources = ["constraints", "targets", "workspace_scope", "artifact_refs", "titles", "paths"]
    dense_sources = ["normalized_titles", "normalized_constraints", "normalized_artifacts", "normalized_targets"]
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

    if dense_enabled and mode in {"active_continuation", "resume", "pivot"}:
        enabled_lanes.append("dense")
        lane_limits["dense"] = 4
        allowed_sources["dense"] = dense_sources

    effective_budget = token_budget if token_budget is not None and token_budget > 0 else 1200
    return {
        "schema_version": "ctree_retrieval_policy_v1",
        "mode": mode,
        "enabled_lanes": enabled_lanes,
        "lane_limits": lane_limits,
        "allowed_sources": allowed_sources,
        "token_budget": effective_budget,
        "active_path_node_ids": active_path,
        "focus_node_id": effective_focus_node_id,
        "focus_selection": {
            "strategy": "explicit" if focus_node_id else "last_node_fallback",
            "requested_focus_node_id": str(focus_node_id or ""),
            "resolved_focus_node_id": effective_focus_node_id,
        },
        "restore_targets": restore_targets,
    }


def build_retrieval_substrate(
    store: CTreeStore,
    *,
    mode: str = "active_continuation",
    token_budget: Optional[int] = None,
    focus_node_id: Optional[str] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
    dense_enabled: bool = False,
) -> Dict[str, Any]:
    retrieval_policy = resolve_retrieval_policy(
        store,
        mode=mode,
        token_budget=token_budget,
        focus_node_id=focus_node_id,
        graph_enabled=graph_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
        dense_enabled=dense_enabled,
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

    if "dense" in list(retrieval_policy.get("enabled_lanes") or []):
        dense = _collect_dense_candidates(store, focus_node_id=focus_node_id, active_path=active_path)
        candidate_support["dense"] = _clip_candidates(dense, int(lane_limits.get("dense") or 0))

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
    focus_node_id: Optional[str] = None,
    lane_profile: Optional[str] = None,
    graph_enabled: bool = True,
    graph_neighborhood_enabled: bool = False,
    dense_enabled: bool = False,
    helper_enabled: bool = False,
    helper_summary_coupling_enabled: bool = False,
) -> Dict[str, Any]:
    lane_config = resolve_lane_profile(
        lane_profile,
        graph_enabled=graph_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
        dense_enabled=dense_enabled,
        helper_enabled=helper_enabled,
        helper_summary_coupling_enabled=helper_summary_coupling_enabled,
    )
    retrieval_substrate = build_retrieval_substrate(
        store,
        mode=mode,
        token_budget=token_budget,
        focus_node_id=focus_node_id,
        graph_enabled=bool(lane_config.get("graph_enabled")),
        graph_neighborhood_enabled=bool(lane_config.get("graph_neighborhood_enabled")),
        dense_enabled=bool(lane_config.get("dense_enabled")),
    )
    retrieval_policy = retrieval_substrate.get("retrieval_policy") or {}
    focus_node_id = retrieval_policy.get("focus_node_id")
    focus_node = _node_by_id(store, str(focus_node_id)) if focus_node_id else None
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
    candidate_lookup: Dict[str, Dict[str, Any]] = {}

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

    if not constraints and isinstance(focus_node, dict):
        constraints = list(focus_node.get("constraints") or [])
        targets = list(focus_node.get("targets") or [])
        workspace_scope = list(focus_node.get("workspace_scope") or [])
        artifact_refs = list(focus_node.get("artifact_refs") or [])
    if mode == "pivot":
        constraints = constraints[:3]
        artifact_refs = artifact_refs[:3]
        workspace_scope = workspace_scope[:1]

    candidate_order: List[str] = []
    for lane in ("structural", "lexical", "dense", "graph_link", "graph_neighborhood"):
        for candidate in list((retrieval_substrate.get("candidate_support") or {}).get(lane) or []):
            node_id = str(candidate.get("node_id") or "")
            if not node_id:
                continue
            payload = dict(candidate)
            payload.setdefault("lane", str(lane))
            if node_id not in candidate_lookup:
                candidate_order.append(node_id)
                candidate_lookup[node_id] = payload
                continue
            if _candidate_preference(payload) > _candidate_preference(candidate_lookup[node_id]):
                candidate_lookup[node_id] = payload

    promoted_candidates: List[Dict[str, Any]] = [
        {
            "node_id": node_id,
            "lane": str((candidate_lookup.get(node_id) or {}).get("lane") or ""),
            "reason": str((candidate_lookup.get(node_id) or {}).get("reason") or ""),
            "score": int((candidate_lookup.get(node_id) or {}).get("score") or 0),
        }
        for node_id in candidate_order
        if node_id in candidate_lookup
    ]

    if mode == "pivot":
        promoted_candidates = promoted_candidates[:4]

    helper_proposal = None
    summary_support: List[Dict[str, Any]] = []
    selected_candidates = list(promoted_candidates)
    if bool(lane_config.get("helper_enabled")):
        subtree_summary_proposals: List[Dict[str, Any]] = []
        if bool(lane_config.get("helper_summary_coupling_enabled")):
            subtree_summary_proposals = _build_active_path_subtree_summary_proposals(store, active_path)
            summary_support = [
                {
                    "parent_node_id": str(item.get("parent_node_id") or ""),
                    "header_summary": str(item.get("header_summary") or ""),
                    "selected_child_ids": list(item.get("selected_child_ids") or []),
                }
                for item in subtree_summary_proposals
            ]
        helper_input = build_helper_rehydration_input(
            store,
            mode=mode,
            retrieval_substrate=retrieval_substrate,
            subtree_summary_proposals=subtree_summary_proposals,
        )
        helper_proposal = build_helper_rehydration_proposal(store, helper_input)
        selected_candidates = [
            {
                "node_id": node_id,
                "lane": str((candidate_lookup.get(node_id) or {}).get("lane") or ""),
                "reason": str((candidate_lookup.get(node_id) or {}).get("reason") or ""),
                "score": int((candidate_lookup.get(node_id) or {}).get("score") or 0),
            }
            for node_id in list(helper_proposal.get("selected_support_node_ids") or [])
            if str(node_id) in candidate_lookup
        ]

    for item in selected_candidates:
        node_id = str(item.get("node_id") or "")
        candidate = candidate_lookup.get(node_id) or {}
        if mode == "dependency_lookup":
            blocker_text = str(candidate.get("blocker_ref") or "").strip()
            if blocker_text and blocker_text not in seen_blocker_refs:
                seen_blocker_refs.add(blocker_text)
                blocker_refs.append(blocker_text)
            graph_type = str(candidate.get("graph_type") or "")
            if graph_type == "validates" and node_id not in seen_validations:
                seen_validations.add(node_id)
                validations.append(node_id)
            if str(candidate.get("lane") or "") == "graph_neighborhood" and node_id and node_id not in seen_graph_neighbor_ids:
                seen_graph_neighbor_ids.add(node_id)
                graph_neighbor_ids.append(node_id)
            if str(candidate.get("lane") or "") == "graph_neighborhood" and graph_type == "supersedes":
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
        "focus_node_id": retrieval_policy.get("focus_node_id"),
        "lane_profile": str(lane_config.get("profile") or "custom"),
        "lane_config": lane_config,
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
            "promoted_candidates": selected_candidates,
            "summary_support": summary_support,
        },
        "retrieval_substrate": retrieval_substrate,
        "rehydration_bundle": {
            "schema_version": "ctree_support_bundle_v1",
            "mode": mode,
            "focus_node_id": focus_node_id,
            "active_path_node_ids": list(retrieval_policy.get("active_path_node_ids") or []),
            "support_node_ids": [item["node_id"] for item in selected_candidates],
            "constraints": constraints,
            "targets": targets,
            "workspace_scope": workspace_scope,
            "artifact_refs": artifact_refs,
            "blocker_refs": blocker_refs,
            "validations": validations,
            "graph_neighbor_ids": graph_neighbor_ids,
            "superseded_source_ids": superseded_source_ids,
            "candidate_provenance": selected_candidates,
            "summary_support": summary_support,
        },
        "helper_proposal": helper_proposal,
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
