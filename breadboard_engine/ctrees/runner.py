from __future__ import annotations

from typing import Any, Dict

from .policy import active_path_node_ids
from .store import CTreeStore


class TreeRunner:
    """Small manifest surface for tranche-1 C-Tree execution state."""

    def __init__(self, store: CTreeStore, *, branches: int = 2) -> None:
        self.store = store
        self.branches = int(branches) if branches and branches > 0 else 2

    def build_manifest(self) -> Dict[str, Any]:
        snapshot = self.store.snapshot()
        return {
            "kind": "tranche1_runner",
            "schema_version": "ctree_runner_manifest_v1",
            "enabled": True,
            "branches": self.branches,
            "node_count": len(getattr(self.store, "nodes", []) or []),
            "ready_count": snapshot.get("ready_count"),
            "top_level_count": snapshot.get("top_level_count"),
            "active_path_node_ids": active_path_node_ids(self.store),
        }
