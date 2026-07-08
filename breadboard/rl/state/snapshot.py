from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.runtime.base import RuntimeSnapshot
from breadboard.rl.state.state_ref import ArtifactRef, StateRef


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SnapshotManifest:
    snapshot_id: str
    package_hash: str
    runtime_backend: str
    state_ref: StateRef
    event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "package_hash": self.package_hash,
            "runtime_backend": self.runtime_backend,
            "state_ref": self.state_ref.to_dict(),
            "event_ids": list(self.event_ids),
            "metadata": dict(self.metadata),
        }


def build_snapshot_manifest(
    *,
    snapshot: RuntimeSnapshot,
    package_hash: str,
    runtime_backend: str,
    artifact_refs: list[ArtifactRef] | None = None,
    event_ids: list[str] | None = None,
) -> SnapshotManifest:
    state_ref = StateRef(
        state_id=f"{snapshot.snapshot_id}.state",
        state_hash=_stable_hash(snapshot.state),
        artifact_refs=list(artifact_refs or []),
        metadata={"snapshot_id": snapshot.snapshot_id},
    )
    return SnapshotManifest(
        snapshot_id=snapshot.snapshot_id,
        package_hash=package_hash,
        runtime_backend=runtime_backend,
        state_ref=state_ref,
        event_ids=list(event_ids or []),
        metadata=dict(snapshot.metadata),
    )
