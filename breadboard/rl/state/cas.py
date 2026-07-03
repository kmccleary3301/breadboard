from __future__ import annotations

import hashlib
from typing import Any

from breadboard.rl.state.state_ref import ArtifactRef


class InMemoryCAS:
    """Small immutable content-addressed store for local replay tests."""

    def __init__(self) -> None:
        self._bytes_by_id: dict[str, bytes] = {}
        self._hash_by_id: dict[str, str] = {}

    def put_bytes(
        self,
        data: bytes,
        *,
        artifact_id: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        resolved_id = artifact_id or digest
        existing_hash = self._hash_by_id.get(resolved_id)
        if existing_hash and existing_hash != digest:
            raise ValueError("CAS artifact overwrite rejected")
        self._bytes_by_id[resolved_id] = bytes(data)
        self._hash_by_id[resolved_id] = digest
        return ArtifactRef(
            artifact_id=resolved_id,
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
            metadata=dict(metadata or {}),
        )

    def get_bytes(self, artifact_ref: ArtifactRef | str) -> bytes:
        artifact_id = artifact_ref.artifact_id if isinstance(artifact_ref, ArtifactRef) else artifact_ref
        return self._bytes_by_id[artifact_id]

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        artifact_id = artifact_ref.artifact_id if isinstance(artifact_ref, ArtifactRef) else artifact_ref
        return artifact_id in self._bytes_by_id
