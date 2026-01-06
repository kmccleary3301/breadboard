from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CTreeNode:
    id: str
    kind: str
    turn: Optional[int]
    payload: Any


class CTreeStore:
    """Minimal in-memory store used as a placeholder for future C-Tree work."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []

    def record(self, kind: str, payload: Any, *, turn: Optional[int] = None) -> str:
        canonical = json.dumps({"kind": kind, "turn": turn, "payload": payload}, sort_keys=True, default=str)
        node_id = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
        entry: Dict[str, Any] = {"id": node_id, "kind": kind, "turn": turn, "payload": payload}
        self.nodes.append(entry)
        return node_id

    def snapshot(self) -> Dict[str, Any]:
        last_id = self.nodes[-1]["id"] if self.nodes else None
        return {"node_count": len(self.nodes), "last_id": last_id}

