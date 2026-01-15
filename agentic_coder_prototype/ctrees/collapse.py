from __future__ import annotations

from typing import Any, Dict, Optional

from .store import CTreeStore
from .policy import collapse_policy


def collapse_ctree(store: CTreeStore, *, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Placeholder collapse stage producing a deterministic summary payload."""

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
    return {
        "kind": "stub",
        "collapsed": True,
        "stage": stage or "FROZEN",
        "node_count": len(getattr(store, "nodes", []) or []),
        "hashes": hashes,
        "policy": policy,
    }
