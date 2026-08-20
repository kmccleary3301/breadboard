from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

CTREE_SCHEMA_VERSION = "0.2"

CTREE_NODE_TYPES = (
    "objective",
    "task",
    "artifact",
    "evidence_bundle",
    "checkpoint",
    "message",
)

CTREE_NODE_STATUSES = (
    "planned",
    "active",
    "blocked",
    "done",
    "frozen",
    "archived",
    "superseded",
    "abandoned",
)

CTREE_GRAPH_LINK_TYPES = (
    "blocks",
    "waits_for",
    "conditional_blocks",
    "validates",
    "caused_by",
    "relates_to",
    "duplicates",
    "supersedes",
    "replies_to",
)


@dataclass
class CTreeEvent:
    event_id: Optional[str]
    event_type: str
    kind: str
    payload: Any
    turn: Optional[int] = None
    node_id: Optional[str] = None
    actor: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "kind": self.kind,
            "payload": self.payload,
            "turn": self.turn,
            "node_id": self.node_id,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CTreeEvent":
        return cls(
            event_id=data.get("event_id"),
            event_type=str(data.get("event_type", "node_recorded")),
            kind=str(data.get("kind", "")),
            payload=data.get("payload"),
            turn=data.get("turn"),
            node_id=data.get("node_id"),
            actor=data.get("actor"),
        )


@dataclass
class CTreeNode:
    node_id: str
    kind: str
    node_type: str
    title: str
    status: str
    turn: Optional[int]
    payload: Any
    parent_id: Optional[str] = None
    path: Optional[str] = None
    owner: Optional[str] = None
    constraints: Optional[List[Dict[str, Any]]] = None
    targets: Optional[List[str]] = None
    workspace_scope: Optional[List[str]] = None
    artifact_refs: Optional[List[str]] = None
    blocker_refs: Optional[List[str]] = None
    related_links: Optional[List[Dict[str, Any]]] = None
    final_spec: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "kind": self.kind,
            "node_type": self.node_type,
            "title": self.title,
            "status": self.status,
            "turn": self.turn,
            "payload": self.payload,
            "parent_id": self.parent_id,
            "path": self.path,
            "owner": self.owner,
            "constraints": list(self.constraints or []),
            "targets": list(self.targets or []),
            "workspace_scope": list(self.workspace_scope or []),
            "artifact_refs": list(self.artifact_refs or []),
            "blocker_refs": list(self.blocker_refs or []),
            "related_links": list(self.related_links or []),
            "final_spec": self.final_spec,
        }
