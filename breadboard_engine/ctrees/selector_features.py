from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .policy import _candidate_preference
from .store import CTreeStore


def node_by_id(store: CTreeStore, node_id: str) -> Optional[Dict[str, Any]]:
    mapping = getattr(store, "_node_by_id", None)
    if isinstance(mapping, dict) and node_id in mapping:
        value = mapping.get(node_id)
        if isinstance(value, dict):
            return value
    for node in list(getattr(store, "nodes", []) or []):
        if isinstance(node, dict) and str(node.get("id") or "") == str(node_id):
            return node
    return None


def selected_limit(mode: str) -> int:
    return {
        "active_continuation": 2,
        "resume": 2,
        "pivot": 2,
        "dependency_lookup": 4,
    }.get(str(mode or "active_continuation"), 2)


def candidate_lookup_from_substrate(
    retrieval_substrate: Dict[str, Any],
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    candidate_lookup: Dict[str, Dict[str, Any]] = {}
    candidate_order: List[str] = []
    for lane in ("structural", "lexical", "dense", "graph_link", "graph_neighborhood"):
        for candidate in list((retrieval_substrate.get("candidate_support") or {}).get(lane) or []):
            if not isinstance(candidate, dict):
                continue
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
    return candidate_order, candidate_lookup


def collect_active_path_state(store: CTreeStore, active_path: List[str]) -> Dict[str, Any]:
    active_target_set: List[str] = []
    active_workspace_scope: List[str] = []
    active_artifact_refs: List[str] = []
    active_constraint_summaries: List[str] = []
    unresolved_blocker_refs: List[str] = []
    conflict_node_ids: List[str] = []

    seen_targets = set()
    seen_workspace = set()
    seen_artifacts = set()
    seen_constraints = set()
    seen_blockers = set()

    for node in list(getattr(store, "nodes", []) or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        status = str(node.get("status") or "")
        if status in {"superseded", "abandoned"} and node_id:
            conflict_node_ids.append(node_id)
        if node_id not in active_path:
            continue
        for target in list(node.get("targets") or []):
            text = str(target).strip()
            if text and text not in seen_targets:
                seen_targets.add(text)
                active_target_set.append(text)
        for scope in list(node.get("workspace_scope") or []):
            text = str(scope).strip()
            if text and text not in seen_workspace:
                seen_workspace.add(text)
                active_workspace_scope.append(text)
        for artifact in list(node.get("artifact_refs") or []):
            text = str(artifact).strip()
            if text and text not in seen_artifacts:
                seen_artifacts.add(text)
                active_artifact_refs.append(text)
        for constraint in list(node.get("constraints") or []):
            if not isinstance(constraint, dict):
                continue
            text = str(constraint.get("summary") or "").strip()
            if text and text not in seen_constraints:
                seen_constraints.add(text)
                active_constraint_summaries.append(text)
        for blocker in list(node.get("blocker_refs") or []):
            text = str(blocker).strip()
            if text and text not in seen_blockers:
                seen_blockers.add(text)
                unresolved_blocker_refs.append(text)

    return {
        "active_target_set": active_target_set,
        "active_workspace_scope": active_workspace_scope,
        "active_artifact_refs": active_artifact_refs,
        "active_constraint_summaries": active_constraint_summaries,
        "unresolved_blocker_refs": unresolved_blocker_refs,
        "conflict_node_ids": sorted(conflict_node_ids),
    }


def build_selector_feature_table(
    store: CTreeStore,
    *,
    mode: str,
    retrieval_substrate: Dict[str, Any],
) -> Dict[str, Any]:
    focus_node_id = str(retrieval_substrate.get("focus_node_id") or "")
    active_path = [str(item) for item in list(retrieval_substrate.get("active_path_node_ids") or []) if str(item)]
    reduced_state = collect_active_path_state(store, active_path)
    active_target_set = {str(item) for item in list(reduced_state.get("active_target_set") or []) if str(item)}
    active_workspace_set = {str(item) for item in list(reduced_state.get("active_workspace_scope") or []) if str(item)}
    active_artifact_set = {str(item) for item in list(reduced_state.get("active_artifact_refs") or []) if str(item)}
    active_constraint_set = {str(item) for item in list(reduced_state.get("active_constraint_summaries") or []) if str(item)}
    unresolved_blockers = {str(item) for item in list(reduced_state.get("unresolved_blocker_refs") or []) if str(item)}
    conflict_node_ids = {str(item) for item in list(reduced_state.get("conflict_node_ids") or []) if str(item)}
    candidate_order, candidate_lookup = candidate_lookup_from_substrate(retrieval_substrate)
    rows: List[Dict[str, Any]] = []

    for node_id in candidate_order:
        candidate = dict(candidate_lookup.get(node_id) or {})
        node = node_by_id(store, node_id)
        node_type = str((node or {}).get("node_type") or "")
        reason = str(candidate.get("reason") or "")
        lane = str(candidate.get("lane") or "")
        graph_type = str(candidate.get("graph_type") or "")
        status = str(candidate.get("status") or "")
        candidate_targets = [str(item) for item in list(candidate.get("targets") or []) if str(item)]
        candidate_workspace_scope = [str(item) for item in list(candidate.get("workspace_scope") or []) if str(item)]
        candidate_artifacts = [str(item) for item in list(candidate.get("artifact_refs") or []) if str(item)]
        candidate_constraints = []
        for constraint in list((node or {}).get("constraints") or []):
            if isinstance(constraint, dict):
                text = str(constraint.get("summary") or "").strip()
                if text:
                    candidate_constraints.append(text)
        blocker_ref = str(candidate.get("blocker_ref") or "").strip()
        features = {
            "focus_node_anchor": node_id == focus_node_id,
            "active_path_member": node_id in set(active_path),
            "direct_structural_relevance": lane == "structural" and reason == "focus_node",
            "ancestor_relation": reason == "ancestor",
            "ready_neighbor": reason == "ready_neighbor",
            "objective_node": node_type == "objective",
            "stale_or_conflict": status in {"superseded", "abandoned", "archived"} or node_id in conflict_node_ids,
            "wrong_neighbor_marker": reason in {"ready_neighbor", "ancestor"} or (
                mode == "pivot" and lane == "graph_neighborhood"
            ),
            "target_overlap_count": len(active_target_set & set(candidate_targets)),
            "workspace_overlap_count": len(active_workspace_set & set(candidate_workspace_scope)),
            "artifact_overlap_count": len(active_artifact_set & set(candidate_artifacts)),
            "constraint_overlap_count": len(active_constraint_set & set(candidate_constraints)),
            "artifact_support": bool(candidate_artifacts),
            "blocker_context": bool(list(candidate.get("blocker_refs") or [])),
            "unresolved_blocker_match": bool(blocker_ref and blocker_ref in unresolved_blockers),
            "validation_priority": graph_type == "validates",
            "supersession_priority": graph_type == "supersedes",
            "dense_support": lane == "dense",
            "lexical_support": lane == "lexical",
            "direct_graph_support": lane == "graph_link",
            "neighbor_graph_support": lane == "graph_neighborhood",
        }
        rows.append(
            {
                "node_id": node_id,
                "title": str(candidate.get("title") or ""),
                "lane": lane,
                "reason": reason,
                "graph_type": graph_type,
                "status": status,
                "base_score": int(candidate.get("score") or 0),
                "targets": candidate_targets,
                "workspace_scope": candidate_workspace_scope,
                "artifact_refs": candidate_artifacts,
                "constraint_summaries": candidate_constraints,
                "blocker_ref": blocker_ref,
                "features": features,
            }
        )

    return {
        "schema_version": "ctree_selector_feature_table_v1",
        "mode": str(mode or "active_continuation"),
        "focus_node_id": focus_node_id or None,
        "active_path_node_ids": active_path,
        "selected_limit": selected_limit(mode),
        "reduced_state": reduced_state,
        "candidate_order": candidate_order,
        "rows": rows,
    }


def build_support_bundle_from_selection(
    store: CTreeStore,
    *,
    mode: str,
    retrieval_policy: Dict[str, Any],
    candidate_lookup: Dict[str, Dict[str, Any]],
    selected_node_ids: List[str],
    lean_artifacts: bool = False,
    lean_active_context: bool = False,
    selector_reasons_by_id: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    active_path = [str(item) for item in list(retrieval_policy.get("active_path_node_ids") or []) if str(item)]
    focus_node_id = str(retrieval_policy.get("focus_node_id") or "")

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

    if not lean_active_context:
        for node_id in active_path:
            node = node_by_id(store, node_id)
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
                text = str(target).strip()
                if text and text not in seen_targets:
                    seen_targets.add(text)
                    targets.append(text)
            for scope in list(node.get("workspace_scope") or []):
                text = str(scope).strip()
                if text and text not in seen_workspace_scope:
                    seen_workspace_scope.add(text)
                    workspace_scope.append(text)
            if not lean_artifacts:
                for artifact in list(node.get("artifact_refs") or []):
                    text = str(artifact).strip()
                    if text and text not in seen_artifacts:
                        seen_artifacts.add(text)
                        artifact_refs.append(text)

    focus_node = node_by_id(store, focus_node_id) if focus_node_id else None
    if not constraints and isinstance(focus_node, dict) and not lean_active_context:
        constraints = list(focus_node.get("constraints") or [])
        targets = [str(item) for item in list(focus_node.get("targets") or []) if str(item)]
        workspace_scope = [str(item) for item in list(focus_node.get("workspace_scope") or []) if str(item)]
        if not lean_artifacts:
            artifact_refs = [str(item) for item in list(focus_node.get("artifact_refs") or []) if str(item)]

    if mode == "pivot":
        constraints = constraints[:3]
        if not lean_artifacts:
            artifact_refs = artifact_refs[:3]
        workspace_scope = workspace_scope[:1]

    selected_candidates: List[Dict[str, Any]] = []
    for node_id in list(selected_node_ids or []):
        candidate = dict(candidate_lookup.get(node_id) or {})
        if not candidate:
            continue
        selected_candidates.append(
            {
                "node_id": node_id,
                "lane": str(candidate.get("lane") or ""),
                "reason": str(candidate.get("reason") or ""),
                "score": int(candidate.get("score") or 0),
            }
        )
        if mode == "dependency_lookup":
            blocker_text = str(candidate.get("blocker_ref") or "").strip()
            if blocker_text and blocker_text not in seen_blocker_refs:
                seen_blocker_refs.add(blocker_text)
                blocker_refs.append(blocker_text)
            graph_type = str(candidate.get("graph_type") or "")
            if graph_type == "validates" and node_id not in seen_validations:
                seen_validations.add(node_id)
                validations.append(node_id)
            if str(candidate.get("lane") or "") == "graph_neighborhood" and node_id not in seen_graph_neighbor_ids:
                seen_graph_neighbor_ids.add(node_id)
                graph_neighbor_ids.append(node_id)
            if graph_type == "supersedes":
                source_id = str(candidate.get("neighbor_source_id") or "").strip()
                if source_id and source_id not in seen_superseded_source_ids:
                    seen_superseded_source_ids.add(source_id)
                    superseded_source_ids.append(source_id)
        for target in list(candidate.get("targets") or []):
            text = str(target).strip()
            if text and text not in seen_targets:
                seen_targets.add(text)
                targets.append(text)
        for scope in list(candidate.get("workspace_scope") or []):
            text = str(scope).strip()
            if text and text not in seen_workspace_scope:
                seen_workspace_scope.add(text)
                workspace_scope.append(text)
        for artifact in list(candidate.get("artifact_refs") or []):
            text = str(artifact).strip()
            if not text or text in seen_artifacts:
                continue
            selector_reasons = list((selector_reasons_by_id or {}).get(node_id) or [])
            should_keep_artifact = not lean_artifacts
            if lean_artifacts:
                should_keep_artifact = bool(
                    mode == "dependency_lookup"
                    or "artifact_overlap" in selector_reasons
                    or "blocker_context" in selector_reasons
                    or "constraint_overlap" in selector_reasons
                    or "validation_priority" in selector_reasons
                    or "supersession_priority" in selector_reasons
                )
            if should_keep_artifact:
                seen_artifacts.add(text)
                artifact_refs.append(text)

    if mode == "dependency_lookup" and superseded_source_ids:
        superseded_artifacts = set()
        for source_id in superseded_source_ids:
            source_node = node_by_id(store, source_id)
            if not isinstance(source_node, dict):
                continue
            for artifact in list(source_node.get("artifact_refs") or []):
                text = str(artifact).strip()
                if text:
                    superseded_artifacts.add(text)
        if superseded_artifacts:
            artifact_refs = [artifact for artifact in artifact_refs if artifact not in superseded_artifacts]

    restore_bundle = {
        "active_path_node_ids": active_path,
        "constraints": constraints,
        "targets": targets,
        "workspace_scope": workspace_scope,
        "artifact_refs": artifact_refs,
        "blocker_refs": blocker_refs,
        "validations": validations,
        "graph_neighbor_ids": graph_neighbor_ids,
        "superseded_source_ids": superseded_source_ids,
        "promoted_candidates": selected_candidates,
        "summary_support": [],
    }
    rehydration_bundle = {
        "schema_version": "ctree_support_bundle_v1",
        "mode": mode,
        "focus_node_id": retrieval_policy.get("focus_node_id"),
        "active_path_node_ids": active_path,
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
        "summary_support": [],
    }
    return {
        "restore_bundle": restore_bundle,
        "rehydration_bundle": rehydration_bundle,
    }
