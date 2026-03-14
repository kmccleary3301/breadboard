from __future__ import annotations

from typing import Any, Dict, Optional

from .store import CTreeStore
from .policy import build_rehydration_plan, build_retrieval_substrate, collapse_policy


def collapse_ctree(store: CTreeStore, *, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build tranche-1 collapse output with explicit retrieval/rehydration metadata."""

    hashes = {}
    try:
        hashes = store.hashes()
    except Exception:
        hashes = {}
    if policy is None:
        policy = collapse_policy(store)
    stage = None
    if isinstance(policy, dict):
        stages = policy.get("stages")
        if isinstance(stages, list) and stages:
            stage = stages[-1].get("stage") if isinstance(stages[-1], dict) else None
    retrieval_substrate = build_retrieval_substrate(store, mode="resume")
    rehydration_plan = build_rehydration_plan(store, mode="resume")
    return {
        "kind": "tranche1_collapse",
        "schema_version": "ctree_collapse_v1",
        "collapsed": True,
        "stage": stage or "FROZEN",
        "node_count": len(getattr(store, "nodes", []) or []),
        "hashes": hashes,
        "policy": policy,
        "retrieval_policy": retrieval_substrate.get("retrieval_policy"),
        "retrieval_substrate": retrieval_substrate,
        "rehydration_plan": rehydration_plan,
        "rehydration_bundle": rehydration_plan.get("rehydration_bundle"),
    }
