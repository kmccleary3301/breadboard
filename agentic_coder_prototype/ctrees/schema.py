from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

CTREE_SCHEMA_VERSION = "0.1"


@dataclass
class CTreeEvent:
    kind: str
    payload: Any
    turn: Optional[int] = None
    node_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "turn": self.turn,
            "node_id": self.node_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CTreeEvent":
        return cls(
            kind=str(data.get("kind", "")),
            payload=data.get("payload"),
            turn=data.get("turn"),
            node_id=data.get("node_id"),
        )
