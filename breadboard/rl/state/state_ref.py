from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    sha256: str
    size_bytes: int
    media_type: str = "application/octet-stream"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.artifact_id:
            raise ValueError("artifact_id must be non-empty")
        if not self.sha256.startswith("sha256:"):
            raise ValueError("sha256 must start with sha256:")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class StateRef:
    state_id: str
    state_hash: str
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.state_id:
            raise ValueError("state_id must be non-empty")
        if not self.state_hash.startswith("sha256:"):
            raise ValueError("state_hash must start with sha256:")
        object.__setattr__(self, "artifact_refs", list(self.artifact_refs))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "state_hash": self.state_hash,
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "metadata": dict(self.metadata),
        }
