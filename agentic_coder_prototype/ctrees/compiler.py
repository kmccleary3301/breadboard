from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .policy import active_path_node_ids, build_rehydration_plan, build_retrieval_substrate
from .store import CTreeStore
from .schema import CTREE_SCHEMA_VERSION


def _canonical_dumps(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def _hash_payload(value: Any) -> str:
    blob = _canonical_dumps(value)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _reduce_local_state(node: Dict[str, Any]) -> Dict[str, Any]:
    constraints = list(node.get("constraints") or [])
    unresolved_constraints = [
        constraint
        for constraint in constraints
        if isinstance(constraint, dict) and str(constraint.get("status") or "unresolved") != "resolved"
    ]
    return {
        "node_id": node.get("id"),
        "title": node.get("title"),
        "status": node.get("status"),
        "node_type": node.get("node_type"),
        "path": node.get("path"),
        "parent_id": node.get("parent_id"),
        "targets": list(node.get("targets") or []),
        "workspace_scope": list(node.get("workspace_scope") or []),
        "artifact_refs": list(node.get("artifact_refs") or []),
        "blocker_refs": list(node.get("blocker_refs") or []),
        "constraint_count": len(constraints),
        "unresolved_constraints": unresolved_constraints,
        "needs_resolution": bool(unresolved_constraints or list(node.get("blocker_refs") or [])),
        "related_links": list(node.get("related_links") or []),
        "final_spec_present": node.get("final_spec") is not None,
    }


def _reduce_parent_state(node: Dict[str, Any], child_states: List[Dict[str, Any]]) -> Dict[str, Any]:
    child_status_counts = Counter(str(child.get("status") or "unknown") for child in child_states)
    active_targets = sorted(
        {
            str(target)
            for child in child_states
            for target in list(child.get("targets") or [])
            if target is not None and str(target)
        }
    )
    unresolved_blockers = sorted(
        {
            str(blocker)
            for child in child_states
            for blocker in list(child.get("blocker_refs") or [])
            if blocker is not None and str(blocker)
        }
    )
    artifact_refs = sorted(
        {
            str(artifact)
            for child in child_states
            for artifact in list(child.get("artifact_refs") or [])
            if artifact is not None and str(artifact)
        }
    )
    notable_decisions = []
    for child in child_states:
        if child.get("final_spec_present"):
            notable_decisions.append(
                {
                    "node_id": child.get("node_id"),
                    "title": child.get("title"),
                    "kind": "final_spec_present",
                }
            )
    notable_decisions = sorted(notable_decisions, key=lambda item: (str(item.get("node_id") or ""), str(item.get("kind") or "")))
    return {
        "node_id": node.get("id"),
        "title": node.get("title"),
        "child_count": len(child_states),
        "child_status_counts": dict(child_status_counts),
        "active_target_set": active_targets,
        "unresolved_blocker_set": unresolved_blockers,
        "artifact_summary": artifact_refs,
        "notable_decisions": notable_decisions,
        "aggregate_summary": {
            "title": node.get("title"),
            "active_children": child_status_counts.get("active", 0),
            "blocked_children": child_status_counts.get("blocked", 0),
            "done_children": child_status_counts.get("done", 0) + child_status_counts.get("frozen", 0),
            "target_count": len(active_targets),
            "artifact_count": len(artifact_refs),
        },
    }


def _build_prompt_planes(
    store: CTreeStore,
    *,
    local_states: List[Dict[str, Any]],
    parent_reductions: List[Dict[str, Any]],
    retrieval_substrate: Dict[str, Any],
    rehydration_bundle: Dict[str, Any],
    prompt_summary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    active_path = active_path_node_ids(store)
    local_by_id = {str(state.get("node_id")): state for state in local_states}
    active_states = [local_by_id[node_id] for node_id in active_path if node_id in local_by_id]
    reduced_target_set = sorted(
        {
            str(target)
            for state in active_states
            for target in list(state.get("targets") or [])
            if target is not None and str(target)
        }
    )
    conflict_node_ids = sorted(
        {
            str(state.get("node_id") or "")
            for state in local_states
            if str(state.get("status") or "") in {"superseded", "abandoned"}
        }
    )
    reduced_task_state = {
        "active_path_node_ids": active_path,
        "focus_node_id": retrieval_substrate.get("focus_node_id"),
        "ready_node_ids": [str(node.get("id")) for node in store.ready_nodes()],
        "blocked_node_ids": sorted(
            {
                str(state.get("node_id") or "")
                for state in local_states
                if list(state.get("blocker_refs") or [])
            }
        ),
        "unresolved_blocker_refs": sorted(
            {
                str(blocker)
                for state in active_states
                for blocker in list(state.get("blocker_refs") or [])
                if blocker is not None and str(blocker)
            }
        ),
        "unresolved_constraints": [
            constraint
            for state in active_states
            for constraint in list(state.get("unresolved_constraints") or [])
        ],
        "active_target_set": reduced_target_set,
        "conflict_node_ids": [node_id for node_id in conflict_node_ids if node_id],
        "needs_resolution": any(bool(state.get("needs_resolution")) for state in active_states),
    }
    prompt_planes: Dict[str, Any] = {
        "schema_version": "ctree_prompt_planes_v2",
        "stable_rules": {
            "ctree_enabled": True,
            "tree_backbone": True,
            "main_write_lock": True,
        },
        "active_path": active_states,
        "reduced_task_state": reduced_task_state,
        "support_bundle": dict(rehydration_bundle or {}),
    }
    if prompt_summary:
        prompt_planes["live_session_delta"] = {
            "prompt_summary": prompt_summary,
            "parent_reduction_count": len(parent_reductions),
        }
    return prompt_planes


def compile_ctree(store: CTreeStore, *, prompt_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compile the current task-state surface into stable staged payloads."""

    hashes = store.hashes()
    nodes = list(getattr(store, "nodes", []) or [])
    events = list(getattr(store, "events", []) or [])
    node_count = len(nodes)
    event_count = len(events)
    snapshot = store.snapshot()
    stable_snapshot = {
        "schema_version": snapshot.get("schema_version"),
        "node_count": snapshot.get("node_count"),
        "event_count": snapshot.get("event_count"),
        "link_count": snapshot.get("link_count"),
        "top_level_count": snapshot.get("top_level_count"),
        "ready_count": snapshot.get("ready_count"),
        "status_counts": snapshot.get("status_counts"),
        "last_id": snapshot.get("last_id"),
    }
    children_by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    local_states: List[Dict[str, Any]] = []
    ready_nodes = list(store.ready_nodes())

    for node in nodes:
        local_states.append(_reduce_local_state(node))
        parent_id = node.get("parent_id")
        if parent_id:
            children_by_parent[str(parent_id)].append(node)

    parent_reductions: List[Dict[str, Any]] = []
    for node in nodes:
        children = children_by_parent.get(str(node.get("id") or ""), [])
        if not children:
            continue
        child_states = [_reduce_local_state(child) for child in children]
        parent_reductions.append(_reduce_parent_state(node, child_states))

    top_level_ids = [str(node.get("id")) for node in store.top_level_nodes()]
    status_counts = Counter(str(node.get("status") or "unknown") for node in nodes)
    retrieval_substrate = build_retrieval_substrate(store, mode="active_continuation")
    rehydration_plan = build_rehydration_plan(store, mode="active_continuation")
    rehydration_bundle = dict(rehydration_plan.get("rehydration_bundle") or {})
    prompt_planes = _build_prompt_planes(
        store,
        local_states=local_states,
        parent_reductions=parent_reductions,
        retrieval_substrate=retrieval_substrate,
        rehydration_bundle=rehydration_bundle,
        prompt_summary=prompt_summary,
    )

    raw_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "hashes": hashes,
        "snapshot": stable_snapshot,
        "local_states": local_states,
        "parent_reductions": parent_reductions,
        "retrieval_substrate": retrieval_substrate,
        "rehydration_bundle": rehydration_bundle,
        "prompt_planes": prompt_planes,
    }
    if prompt_summary:
        raw_payload["prompt_summary"] = prompt_summary

    spec_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "hashes": hashes,
        "top_level_ids": top_level_ids,
        "ready_node_ids": [str(node.get("id")) for node in ready_nodes],
        "status_counts": dict(status_counts),
        "parent_reductions": parent_reductions,
        "retrieval_substrate": retrieval_substrate,
        "rehydration_bundle": rehydration_bundle,
        "prompt_planes": prompt_planes,
    }
    header_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "ready_count": len(ready_nodes),
        "top_level_count": len(top_level_ids),
        "status_counts": dict(status_counts),
        "active_path_node_ids": active_path_node_ids(store),
        "unresolved_blocker_count": len(prompt_planes["reduced_task_state"]["unresolved_blocker_refs"]),
        "needs_resolution": bool(prompt_planes["reduced_task_state"]["needs_resolution"]),
    }
    frozen_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "ready_node_ids": [str(node.get("id")) for node in ready_nodes],
        "top_level_ids": top_level_ids,
        "parent_digest": _hash_payload(parent_reductions),
        "prompt_plane_digest": _hash_payload(prompt_planes),
    }

    z1 = _hash_payload(raw_payload)
    z2 = _hash_payload(spec_payload)
    z3 = _hash_payload(header_payload)
    z4 = _hash_payload(frozen_payload)

    return {
        "kind": "htsg_r_preview",
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "has_prompt_summary": bool(prompt_summary),
        "prompt_planes": prompt_planes,
        "hashes": {
            **hashes,
            "z1": z1,
            "z2": z2,
            "z3": z3,
            "z4": z4,
        },
        "stages": {
            "RAW": raw_payload,
            "SPEC": spec_payload,
            "HEADER": header_payload,
            "FROZEN": frozen_payload,
        },
    }
