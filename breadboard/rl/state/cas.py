from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from breadboard.rl.state.state_ref import ArtifactRef


class ArtifactStoreError(RuntimeError):
    """Persistent artifact storage failed or returned invalid data."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Stored artifact bytes or metadata failed integrity validation."""


class ArtifactConflictError(ArtifactStoreError):
    """An immutable artifact identifier was reused for different content."""


class InMemoryCAS:
    """Small immutable content-addressed store for local replay tests."""

    def __init__(self) -> None:
        self._bytes_by_id: dict[str, bytes] = {}
        self._hash_by_id: dict[str, str] = {}
        self._refs_by_id: dict[str, ArtifactRef] = {}

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
            raise ArtifactConflictError("CAS artifact overwrite rejected")
        payload = data if isinstance(data, bytes) else bytes(data)
        self._bytes_by_id[resolved_id] = payload
        self._hash_by_id[resolved_id] = digest
        ref = ArtifactRef(
            artifact_id=resolved_id,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            metadata=dict(metadata or {}),
        )
        self._refs_by_id[resolved_id] = ref
        return ref

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        return self._refs_by_id[artifact_id]

    def get_bytes(self, artifact_ref: ArtifactRef | str) -> bytes:
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        return self._bytes_by_id[artifact_id]

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        return artifact_id in self._bytes_by_id


class FilesystemCAS:
    """Durable immutable artifact store backed by content-addressed files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.blobs = self.root / "blobs"
        self.records = self.root / "records"
        self.blobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.Lock()

    def _record_path(self, artifact_id: str) -> Path:
        key = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
        return self.records / f"{key}.json"

    def _blob_path(self, digest: str) -> Path:
        if not digest.startswith("sha256:"):
            raise ArtifactIntegrityError("artifact digest must use sha256")
        return self.blobs / digest.removeprefix("sha256:")

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def put_bytes(
        self,
        data: bytes,
        *,
        artifact_id: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        payload = data if isinstance(data, bytes) else bytes(data)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        resolved_id = artifact_id or digest
        ref = ArtifactRef(
            artifact_id=resolved_id,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            metadata=dict(metadata or {}),
        )
        record_path = self._record_path(resolved_id)
        blob_path = self._blob_path(digest)
        record_bytes = json.dumps(
            ref.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with self._lock:
            if record_path.exists():
                existing = self.get_ref(resolved_id)
                if existing.sha256 != digest:
                    raise ArtifactConflictError("CAS artifact overwrite rejected")
                return existing
            if blob_path.exists():
                existing_payload = blob_path.read_bytes()
                if hashlib.sha256(existing_payload).hexdigest() != digest.removeprefix(
                    "sha256:"
                ):
                    raise ArtifactIntegrityError("CAS blob integrity check failed")
            else:
                self._atomic_write(blob_path, payload)
            self._atomic_write(record_path, record_bytes)
        return ref

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        try:
            payload = json.loads(
                self._record_path(artifact_id).read_text(encoding="utf-8")
            )
            if (
                not isinstance(payload, dict)
                or payload.get("artifact_id") != artifact_id
            ):
                raise ValueError("identity mismatch")
            return ArtifactRef(
                artifact_id=str(payload["artifact_id"]),
                sha256=str(payload["sha256"]),
                size_bytes=int(payload["size_bytes"]),
                media_type=str(payload.get("media_type") or "application/octet-stream"),
                metadata=dict(payload.get("metadata") or {}),
            )
        except FileNotFoundError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("CAS artifact record is invalid") from exc

    def get_bytes(self, artifact_ref: ArtifactRef | str) -> bytes:
        ref = (
            artifact_ref
            if isinstance(artifact_ref, ArtifactRef)
            else self.get_ref(artifact_ref)
        )
        try:
            payload = self._blob_path(ref.sha256).read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("CAS artifact blob is missing") from exc
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if digest != ref.sha256 or len(payload) != ref.size_bytes:
            raise ArtifactIntegrityError("CAS artifact integrity check failed")
        return payload

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        return self._record_path(artifact_id).exists()


__all__ = [
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactStoreError",
    "FilesystemCAS",
    "InMemoryCAS",
]
