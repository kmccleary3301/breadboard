from __future__ import annotations

from typing import Any, Dict, Optional

from .store import CTreeStore


def compile_ctree(store: CTreeStore, *, prompt_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Placeholder compiler producing a deterministic summary payload."""

    hashes = {}
    try:
        hashes = store.hashes()
    except Exception:
        hashes = {}
    return {
        "kind": "stub",
        "node_count": len(getattr(store, "nodes", []) or []),
        "has_prompt_summary": bool(prompt_summary),
        "hashes": hashes,
    }
