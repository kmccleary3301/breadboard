from __future__ import annotations

from typing import Any, Dict

from .store import CTreeStore


class TreeRunner:
    """Placeholder runner for future branching execution."""

    def __init__(self, store: CTreeStore, *, branches: int = 2) -> None:
        self.store = store
        self.branches = int(branches) if branches and branches > 0 else 2

    def build_manifest(self) -> Dict[str, Any]:
        return {
            "kind": "stub",
            "enabled": True,
            "branches": self.branches,
            "node_count": len(getattr(self.store, "nodes", []) or []),
        }

