from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.edge_id:
            raise ValueError("edge_id must be non-empty")
        if not self.source_id:
            raise ValueError("source_id must be non-empty")
        if not self.target_id:
            raise ValueError("target_id must be non-empty")
        if not self.edge_kind:
            raise ValueError("edge_kind must be non-empty")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_kind": self.edge_kind,
            "metadata": dict(self.metadata),
        }
