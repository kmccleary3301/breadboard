from __future__ import annotations

from typing import Any, Dict

from .compiler import compile_ctree
from .policy import build_rehydration_plan, build_retrieval_substrate, resolve_retrieval_policy
from .store import CTreeStore


def build_probe_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record(
        "objective",
        {
            "title": "Phase 9",
            "constraints": [{"summary": "Keep state deterministic", "scope": "global"}],
        },
        turn=1,
    )
    store.record(
        "task",
        {
            "title": "Draft architecture spec",
            "parent_id": root_id,
            "constraints": [{"summary": "Preserve deterministic reducers"}],
            "targets": ["ctrees/compiler.py", "docs_tmp/c_trees/phase_9/PHASE_9_ARCHITECTURE_SPEC.md"],
            "artifact_refs": ["architecture_spec.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Blocked retrieval lane follow-up",
            "parent_id": root_id,
            "blocker_refs": ["dep-querylake-lane-matrix"],
            "artifact_refs": ["retrieval_matrix.md"],
        },
        turn=3,
    )
    return store


def build_target_supersession_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Supersession probe"}, turn=1)
    store.record(
        "task",
        {
            "title": "Old target plan",
            "parent_id": root_id,
            "status": "superseded",
            "targets": ["legacy_target.py"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "New target plan",
            "parent_id": root_id,
            "targets": ["active_target.py"],
            "constraints": [{"summary": "Use the new target only"}],
        },
        turn=3,
    )
    return store


def build_false_neighbor_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Neighbor suppression probe"}, turn=1)
    store.record(
        "task",
        {
            "title": "Relevant retrieval contract work",
            "parent_id": root_id,
            "targets": ["ctrees/policy.py"],
            "artifact_refs": ["retrieval_contract.md"],
            "workspace_scope": ["agentic_coder_prototype/ctrees"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Irrelevant batch router audit",
            "parent_id": root_id,
            "targets": ["router/audit.py"],
            "artifact_refs": ["batch_router.md"],
            "workspace_scope": ["agentic_coder_prototype/router"],
        },
        turn=3,
    )
    store.record(
        "task",
        {
            "title": "Continue retrieval contract work",
            "parent_id": root_id,
            "targets": ["ctrees/policy.py"],
            "artifact_refs": ["retrieval_contract.md"],
            "constraints": [{"summary": "Suppress false lexical neighbors"}],
        },
        turn=4,
    )
    return store


def build_stale_verification_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Verification dominance probe"}, turn=1)
    store.record(
        "task",
        {
            "title": "Old verification note",
            "parent_id": root_id,
            "status": "superseded",
            "artifact_refs": ["verification_old.md"],
            "constraints": [{"summary": "Old check result"}],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Fresh verification note",
            "parent_id": root_id,
            "artifact_refs": ["verification_fresh.md"],
            "constraints": [{"summary": "Current verification result"}],
            "targets": ["tests/test_ctrees_policy.py"],
        },
        turn=3,
    )
    return store


def build_blocker_clearance_pair() -> Dict[str, CTreeStore]:
    blocked = CTreeStore()
    root_id = blocked.record("objective", {"title": "Blocker transition probe"}, turn=1)
    blocked.record(
        "task",
        {
            "title": "Blocked task",
            "parent_id": root_id,
            "blocker_refs": ["dep-schema"],
            "targets": ["ctrees/schema.py"],
        },
        turn=2,
    )

    cleared = CTreeStore()
    root_id = cleared.record("objective", {"title": "Blocker transition probe"}, turn=1)
    cleared.record(
        "task",
        {
            "title": "Cleared task",
            "parent_id": root_id,
            "targets": ["ctrees/schema.py"],
        },
        turn=2,
    )
    return {"blocked": blocked, "cleared": cleared}


def build_dependency_lookup_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Dependency lookup probe"}, turn=1)
    blocker_id = store.record(
        "task",
        {
            "title": "Schema prerequisite",
            "parent_id": root_id,
            "targets": ["ctrees/schema.py"],
            "artifact_refs": ["schema_spec.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "related_links": [{"type": "validates", "target": blocker_id}],
            "targets": ["ctrees/compiler.py"],
        },
        turn=3,
    )
    return store


def build_graph_neighborhood_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Graph neighborhood probe"}, turn=1)
    validation_id = store.record(
        "evidence_bundle",
        {
            "title": "Schema validation packet",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["schema_validation.md"],
        },
        turn=2,
    )
    prerequisite_id = store.record(
        "task",
        {
            "title": "Token budget prerequisite",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["token_budget.md"],
            "targets": ["ctrees/policy.py"],
        },
        turn=3,
    )
    replacement_id = store.record(
        "task",
        {
            "title": "Replacement schema contract",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["replacement_schema.md"],
            "targets": ["ctrees/schema.py"],
        },
        turn=4,
    )
    blocker_id = store.record(
        "task",
        {
            "title": "Legacy schema prerequisite",
            "parent_id": root_id,
            "artifact_refs": ["legacy_schema.md"],
            "blocker_refs": [prerequisite_id],
            "related_links": [
                {"type": "validates", "target": validation_id},
                {"type": "supersedes", "target": replacement_id},
            ],
            "targets": ["ctrees/schema.py"],
        },
        turn=5,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "targets": ["ctrees/compiler.py"],
        },
        turn=6,
    )
    return store


def run_continuation_probe() -> Dict[str, Any]:
    store = build_probe_store()
    compiled = compile_ctree(store)
    retrieval = build_retrieval_substrate(store, mode="active_continuation")
    return {
        "probe": "continuation",
        "node_count": store.snapshot()["node_count"],
        "active_path_node_ids": retrieval.get("active_path_node_ids"),
        "ready_node_ids": compiled["stages"]["SPEC"]["ready_node_ids"],
        "unresolved_blocker_count": compiled["stages"]["HEADER"]["unresolved_blocker_count"],
        "prompt_planes": compiled["prompt_planes"],
        "retrieval_substrate": retrieval,
    }


def run_resume_probe() -> Dict[str, Any]:
    store = build_probe_store()
    compiled = compile_ctree(store)
    rehydration = build_rehydration_plan(store, mode="resume")
    return {
        "probe": "resume",
        "focus_node_id": rehydration.get("focus_node_id"),
        "restore_bundle": rehydration.get("restore_bundle"),
        "ready_node_ids": compiled["stages"]["SPEC"]["ready_node_ids"],
        "prompt_planes": compiled["prompt_planes"],
        "support_bundle": rehydration.get("rehydration_bundle"),
    }


def run_pivot_probe() -> Dict[str, Any]:
    store = build_probe_store()
    rehydration = build_rehydration_plan(store, mode="pivot")
    retrieval = resolve_retrieval_policy(store, mode="pivot")
    return {
        "probe": "pivot",
        "focus_node_id": rehydration.get("focus_node_id"),
        "enabled_lanes": retrieval.get("enabled_lanes"),
        "restore_targets": retrieval.get("restore_targets"),
        "artifact_refs": (rehydration.get("restore_bundle") or {}).get("artifact_refs") or [],
        "workspace_scope": (rehydration.get("restore_bundle") or {}).get("workspace_scope") or [],
        "promoted_candidates": (rehydration.get("rehydration_bundle") or {}).get("support_node_ids") or [],
    }


def run_target_supersession_probe() -> Dict[str, Any]:
    store = build_target_supersession_store()
    compiled = compile_ctree(store)
    support_bundle = compiled["prompt_planes"]["support_bundle"]
    return {
        "probe": "target_supersession",
        "active_target_set": compiled["prompt_planes"]["reduced_task_state"]["active_target_set"],
        "support_targets": support_bundle.get("targets") or [],
        "conflict_node_ids": compiled["prompt_planes"]["reduced_task_state"]["conflict_node_ids"],
    }


def run_blocker_clearance_probe() -> Dict[str, Any]:
    pair = build_blocker_clearance_pair()
    blocked_compiled = compile_ctree(pair["blocked"])
    cleared_compiled = compile_ctree(pair["cleared"])
    return {
        "probe": "blocker_clearance",
        "blocked_ready_node_ids": blocked_compiled["stages"]["SPEC"]["ready_node_ids"],
        "cleared_ready_node_ids": cleared_compiled["stages"]["SPEC"]["ready_node_ids"],
        "blocked_needs_resolution": blocked_compiled["prompt_planes"]["reduced_task_state"]["needs_resolution"],
        "cleared_needs_resolution": cleared_compiled["prompt_planes"]["reduced_task_state"]["needs_resolution"],
    }


def run_stale_verification_probe() -> Dict[str, Any]:
    store = build_stale_verification_store()
    substrate = build_retrieval_substrate(store, mode="active_continuation")
    support_bundle = build_rehydration_plan(store, mode="active_continuation").get("rehydration_bundle") or {}
    lexical_ids = [str(item.get("node_id") or "") for item in list((substrate.get("candidate_support") or {}).get("lexical") or [])]
    return {
        "probe": "stale_verification_dominance",
        "lexical_candidate_ids": lexical_ids,
        "support_node_ids": support_bundle.get("support_node_ids") or [],
        "artifact_refs": support_bundle.get("artifact_refs") or [],
    }


def run_false_neighbor_probe() -> Dict[str, Any]:
    store = build_false_neighbor_store()
    substrate = build_retrieval_substrate(store, mode="active_continuation")
    lexical = list((substrate.get("candidate_support") or {}).get("lexical") or [])
    return {
        "probe": "false_neighbor_suppression",
        "lexical_candidate_ids": [str(item.get("node_id") or "") for item in lexical],
        "matched_terms": {str(item.get("node_id") or ""): list(item.get("matched_terms") or []) for item in lexical},
    }


def run_pivot_minimality_probe() -> Dict[str, Any]:
    store = build_probe_store()
    bundle = build_rehydration_plan(store, mode="pivot").get("rehydration_bundle") or {}
    return {
        "probe": "pivot_minimality",
        "support_node_ids": bundle.get("support_node_ids") or [],
        "workspace_scope": bundle.get("workspace_scope") or [],
        "artifact_refs": bundle.get("artifact_refs") or [],
        "candidate_provenance": bundle.get("candidate_provenance") or [],
    }


def run_dependency_lookup_probe() -> Dict[str, Any]:
    store = build_dependency_lookup_store()
    substrate = build_retrieval_substrate(store, mode="dependency_lookup")
    bundle = build_rehydration_plan(store, mode="dependency_lookup").get("rehydration_bundle") or {}
    graph_candidates = list((substrate.get("candidate_support") or {}).get("graph_link") or [])
    return {
        "probe": "dependency_lookup",
        "enabled_lanes": list((substrate.get("retrieval_policy") or {}).get("enabled_lanes") or []),
        "graph_candidate_ids": [str(item.get("node_id") or "") for item in graph_candidates],
        "graph_candidate_reasons": [str(item.get("reason") or "") for item in graph_candidates],
        "blocker_refs": bundle.get("blocker_refs") or [],
        "validations": bundle.get("validations") or [],
        "artifact_refs": bundle.get("artifact_refs") or [],
        "support_node_ids": bundle.get("support_node_ids") or [],
    }


def run_graph_neighborhood_probe() -> Dict[str, Any]:
    store = build_graph_neighborhood_store()
    substrate = build_retrieval_substrate(store, mode="dependency_lookup", graph_neighborhood_enabled=True)
    bundle = build_rehydration_plan(
        store,
        mode="dependency_lookup",
        graph_neighborhood_enabled=True,
    ).get("rehydration_bundle") or {}
    neighbors = list((substrate.get("candidate_support") or {}).get("graph_neighborhood") or [])
    return {
        "probe": "graph_neighborhood_dependency_lookup",
        "enabled_lanes": list((substrate.get("retrieval_policy") or {}).get("enabled_lanes") or []),
        "graph_neighbor_ids": [str(item.get("node_id") or "") for item in neighbors],
        "graph_neighbor_reasons": [str(item.get("reason") or "") for item in neighbors],
        "neighbor_source_ids": [str(item.get("neighbor_source_id") or "") for item in neighbors],
        "restore_targets": list((substrate.get("retrieval_policy") or {}).get("restore_targets") or []),
        "artifact_refs": bundle.get("artifact_refs") or [],
        "validations": bundle.get("validations") or [],
        "graph_neighbor_bundle_ids": bundle.get("graph_neighbor_ids") or [],
        "support_node_ids": bundle.get("support_node_ids") or [],
    }
