from __future__ import annotations

from typing import Any, Dict

from .store import CTreeStore


def collapse_ctree(store: CTreeStore) -> Dict[str, Any]:
    """Placeholder collapse stage producing a deterministic summary payload."""

    hashes = {}
    try:
        hashes = store.hashes()
    except Exception:
        hashes = {}
    return {
        "kind": "stub",
        "collapsed": True,
        "node_count": len(getattr(store, "nodes", []) or []),
        "hashes": hashes,
    }
