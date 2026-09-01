from __future__ import annotations

import copy
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import stat
import threading
import uuid
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .references import ArtifactRef

_RECORD_BYTE_LIMIT = 1024 * 1024
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_ROOT_LOCKS: dict[str, threading.RLock] = {}

_ATOMIC_DIRECTORY = threading.local()

class ArtifactStoreError(RuntimeError):
    """Persistent artifact storage failed or returned invalid data."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Stored artifact bytes or metadata failed integrity validation."""


class ArtifactConflictError(ArtifactStoreError):
    """An immutable artifact identifier was reused for different content."""


@runtime_checkable
class CASReader(Protocol):
    """Structural read-only interface for immutable artifact stores."""

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        """Return the immutable record bound to ``artifact_id``."""

    def get_bytes(
        self,
        artifact_ref: ArtifactRef | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Return verified bytes without reading more than ``max_bytes``."""


def _copy_ref(ref: ArtifactRef) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=ref.artifact_id,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
        metadata=copy.deepcopy(ref.metadata),
    )


def _same_ref(left: ArtifactRef, right: ArtifactRef) -> bool:
    return (
        left.artifact_id == right.artifact_id
        and left.sha256 == right.sha256
        and left.size_bytes == right.size_bytes
        and left.media_type == right.media_type
        and left.metadata == right.metadata
    )


def _validate_read_limit(max_bytes: int | None) -> int | None:
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0
    ):
        raise ValueError("max_bytes must be a non-negative integer or None")
    return max_bytes


def _resolve_supplied_ref(
    stored: ArtifactRef, supplied: ArtifactRef | str
) -> ArtifactRef:
    if isinstance(supplied, ArtifactRef) and not _same_ref(stored, supplied):
        raise ArtifactIntegrityError("artifact reference does not match the stored record")
    return stored




class InMemoryCAS:
    """Small immutable content-addressed store for local replay tests."""

    def __init__(self) -> None:
        self._bytes_by_id: dict[str, bytes] = {}
        self._refs_by_id: dict[str, ArtifactRef] = {}
        self._lock = threading.RLock()

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
        candidate = ArtifactRef(
            artifact_id=resolved_id,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            metadata=copy.deepcopy(metadata or {}),
        )
        with self._lock:
            existing = self._refs_by_id.get(resolved_id)
            if existing is not None:
                if not _same_ref(existing, candidate):
                    raise ArtifactConflictError("CAS artifact overwrite rejected")
                return _copy_ref(existing)
            self._bytes_by_id[resolved_id] = payload
            self._refs_by_id[resolved_id] = candidate
            return _copy_ref(candidate)

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        with self._lock:
            return _copy_ref(self._refs_by_id[artifact_id])

    def get_bytes(
        self,
        artifact_ref: ArtifactRef | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        limit = _validate_read_limit(max_bytes)
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        with self._lock:
            stored = _resolve_supplied_ref(self._refs_by_id[artifact_id], artifact_ref)
            if limit is not None and stored.size_bytes > limit:
                raise ArtifactIntegrityError("artifact exceeds the bounded read limit")
            payload = self._bytes_by_id[artifact_id]
            if (
                len(payload) != stored.size_bytes
                or "sha256:" + hashlib.sha256(payload).hexdigest() != stored.sha256
            ):
                raise ArtifactIntegrityError("CAS artifact integrity check failed")
            return payload

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        with self._lock:
            return artifact_id in self._refs_by_id


def _filesystem_operation(method: Any) -> Any:
    @functools.wraps(method)
    def guarded(self: "FilesystemCAS", *args: Any, **kwargs: Any) -> Any:
        with self._lifecycle_lock:
            if self._closed:
                raise ArtifactStoreError("CAS is closed")
            return method(self, *args, **kwargs)

    return guarded


class FilesystemCAS:
    """Durable immutable artifact store backed by content-addressed files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.blobs = self.root / "blobs"
        self.locks = self.root / "locks"
        self.records = self.root / "records"
        self.blobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.locks.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_key = os.fspath(self.root)
        with _PROCESS_LOCKS_GUARD:
            self._process_lock = _PROCESS_ROOT_LOCKS.setdefault(
                root_key, threading.RLock()
            )
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_CLOEXEC"):
            directory_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        try:
            root_fd = os.open(self.root, directory_flags)
            directory_fds = [root_fd]
            for name in ("blobs", "records", "locks"):
                directory_fds.append(os.open(name, directory_flags, dir_fd=root_fd))
        except OSError as exc:
            for fd in locals().get("directory_fds", ()):
                os.close(fd)
            raise ArtifactStoreError("CAS directories cannot be opened safely") from exc
        self._root_fd, self._blobs_fd, self._records_fd, self._locks_fd = directory_fds
        self._directory_identities = tuple(
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in directory_fds
        )
        self._descriptor_cleanup = weakref.finalize(
            self, FilesystemCAS._close_descriptors, tuple(directory_fds)
        )

    @staticmethod
    def _close_descriptors(descriptors: tuple[int, ...]) -> None:
        for fd in descriptors:
            with contextlib.suppress(OSError):
                os.close(fd)

    def close(self) -> None:
        """Stop admission and close pinned descriptors after active operations."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._descriptor_cleanup()

    def _validate_directories(self) -> None:
        descriptors = (
            self._root_fd,
            self._blobs_fd,
            self._records_fd,
            self._locks_fd,
        )
        try:
            root_path = os.stat(self.root, follow_symlinks=False)
            if (root_path.st_dev, root_path.st_ino) != self._directory_identities[0]:
                raise ArtifactIntegrityError("CAS root identity changed")
            for index, (name, fd) in enumerate(
                zip(("blobs", "records", "locks"), descriptors[1:]), start=1
            ):
                opened = os.fstat(fd)
                linked = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
                identity = (opened.st_dev, opened.st_ino)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or not stat.S_ISDIR(linked.st_mode)
                    or identity != self._directory_identities[index]
                    or (linked.st_dev, linked.st_ino) != identity
                ):
                    raise ArtifactIntegrityError("CAS directory identity changed")
        except ArtifactIntegrityError:
            raise
        except OSError as exc:
            raise ArtifactIntegrityError("CAS directory identity cannot be validated") from exc

    @contextlib.contextmanager
    def _atomic_directory(self, directory_fd: int) -> Iterator[None]:
        previous = getattr(_ATOMIC_DIRECTORY, "fd", None)
        _ATOMIC_DIRECTORY.fd = directory_fd
        try:
            yield
        finally:
            _ATOMIC_DIRECTORY.fd = previous

    def _record_key(self, artifact_id: str) -> str:
        return hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()

    def _record_path(self, artifact_id: str) -> Path:
        return self.records / f"{self._record_key(artifact_id)}.json"

    def _blob_path(self, digest: str) -> Path:
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise ArtifactIntegrityError(
                "artifact digest must be a full lowercase sha256 digest"
            )
        return self.blobs / digest.removeprefix("sha256:")

    @contextlib.contextmanager
    def _artifact_lock(self, artifact_id: str) -> Iterator[None]:
        lock_name = f"{self._record_key(artifact_id)}.lock"
        flags = os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self._validate_directories()
        try:
            try:
                fd = os.open(lock_name, flags, dir_fd=self._locks_fd)
            except FileNotFoundError:
                try:
                    fd = os.open(
                        lock_name,
                        flags | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=self._locks_fd,
                    )
                except FileExistsError:
                    fd = os.open(lock_name, flags, dir_fd=self._locks_fd)
        except OSError as exc:
            raise ArtifactStoreError("CAS artifact lock cannot be opened safely") from exc
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ArtifactIntegrityError("CAS artifact lock is unsafe")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _atomic_create(path: Path, data: bytes) -> bool:
        directory_fd = getattr(_ATOMIC_DIRECTORY, "fd", None)
        if directory_fd is None:
            raise ArtifactStoreError("CAS atomic publication is not directory-anchored")
        temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = -1
        try:
            fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            view = memoryview(data)
            written = 0
            while written < len(view):
                written += os.write(fd, view[written:])
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(
                    temporary_name,
                    path.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return False
            os.fsync(directory_fd)
            return True
        finally:
            if fd >= 0:
                os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=directory_fd)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Compatibility seam for callers that require exclusive publication."""

        if not FilesystemCAS._atomic_create(path, data):
            raise FileExistsError(path)

    @staticmethod
    def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_ctime_ns,
            metadata.st_size,
            metadata.st_mtime_ns,
        )

    def _read_record_bytes(self, artifact_id: str) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            self._validate_directories()
            fd = os.open(
                f"{self._record_key(artifact_id)}.json",
                flags,
                dir_fd=self._records_fd,
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactIntegrityError(
                "CAS artifact record cannot be opened safely"
            ) from exc
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ArtifactIntegrityError("CAS artifact record is unsafe")
            if opened.st_size > _RECORD_BYTE_LIMIT:
                raise ArtifactIntegrityError("CAS artifact record exceeds byte limit")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    fd, min(64 * 1024, _RECORD_BYTE_LIMIT + 1 - total)
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _RECORD_BYTE_LIMIT:
                    raise ArtifactIntegrityError(
                        "CAS artifact record exceeds byte limit"
                    )
            after = os.fstat(fd)
            if self._file_identity(after) != self._file_identity(opened):
                raise ArtifactIntegrityError(
                    "CAS artifact record changed while reading"
                )
            return b"".join(chunks)
        finally:
            os.close(fd)

    def _read_blob(self, ref: ArtifactRef, *, max_bytes: int | None) -> bytes:
        limit = _validate_read_limit(max_bytes)
        if limit is not None and ref.size_bytes > limit:
            raise ArtifactIntegrityError("artifact exceeds the bounded read limit")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            self._validate_directories()
            fd = os.open(
                ref.sha256.removeprefix("sha256:"),
                flags,
                dir_fd=self._blobs_fd,
            )
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("CAS artifact blob is missing") from exc
        except OSError as exc:
            raise ArtifactIntegrityError(
                "CAS artifact blob cannot be opened safely"
            ) from exc
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != ref.size_bytes:
                raise ArtifactIntegrityError("CAS artifact integrity check failed")
            if limit is not None and opened.st_size > limit:
                raise ArtifactIntegrityError("artifact exceeds the bounded read limit")
            chunks: list[bytes] = []
            total = 0
            read_limit = ref.size_bytes
            while True:
                chunk = os.read(fd, min(64 * 1024, read_limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > read_limit:
                    raise ArtifactIntegrityError("CAS artifact integrity check failed")
            payload = b"".join(chunks)
        finally:
            os.close(fd)
        if (
            len(payload) != ref.size_bytes
            or "sha256:" + hashlib.sha256(payload).hexdigest() != ref.sha256
        ):
            raise ArtifactIntegrityError("CAS artifact integrity check failed")
        return payload

    @_filesystem_operation
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
        candidate = ArtifactRef(
            artifact_id=resolved_id,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            metadata=copy.deepcopy(metadata or {}),
        )
        record_path = self._record_path(resolved_id)
        blob_path = self._blob_path(digest)
        record_bytes = json.dumps(
            candidate.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(record_bytes) > _RECORD_BYTE_LIMIT:
            raise ArtifactStoreError("CAS artifact record exceeds byte limit")
        with self._process_lock:
            with self._artifact_lock(resolved_id):
                try:
                    existing = self.get_ref(resolved_id)
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    if not _same_ref(existing, candidate):
                        raise ArtifactConflictError("CAS artifact overwrite rejected")
                    return existing
                self._validate_directories()
                try:
                    with self._atomic_directory(self._blobs_fd):
                        self._atomic_write(blob_path, payload)
                except FileExistsError:
                    self._read_blob(candidate, max_bytes=len(payload))
                except OSError as exc:
                    raise ArtifactStoreError("CAS artifact blob cannot be published") from exc
                self._validate_directories()
                try:
                    with self._atomic_directory(self._records_fd):
                        self._atomic_write(record_path, record_bytes)
                except FileExistsError:
                    existing = self.get_ref(resolved_id)
                    if not _same_ref(existing, candidate):
                        raise ArtifactConflictError("CAS artifact overwrite rejected")
                    return existing
                except OSError as exc:
                    raise ArtifactStoreError(
                        "CAS artifact record cannot be published"
                    ) from exc
        return _copy_ref(candidate)

    @_filesystem_operation
    def get_ref(self, artifact_id: str) -> ArtifactRef:
        try:
            payload = json.loads(
                self._read_record_bytes(artifact_id).decode("utf-8")
            )
            if not isinstance(payload, dict) or payload.get("artifact_id") != artifact_id:
                raise ValueError("identity mismatch")
            sha256 = payload["sha256"]
            size_bytes = payload["size_bytes"]
            media_type = payload.get("media_type", "application/octet-stream")
            metadata = payload.get("metadata", {})
            if (
                not isinstance(sha256, str)
                or _SHA256_RE.fullmatch(sha256) is None
                or not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or size_bytes < 0
                or not isinstance(media_type, str)
                or not media_type
                or not isinstance(metadata, dict)
            ):
                raise ValueError("invalid record field")
            return ArtifactRef(
                artifact_id=artifact_id,
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                metadata=copy.deepcopy(metadata),
            )
        except FileNotFoundError:
            raise
        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise ArtifactIntegrityError("CAS artifact record is invalid") from exc

    @_filesystem_operation
    def get_bytes(
        self,
        artifact_ref: ArtifactRef | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        stored = _resolve_supplied_ref(self.get_ref(artifact_id), artifact_ref)
        return self._read_blob(stored, max_bytes=max_bytes)

    @_filesystem_operation
    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if isinstance(artifact_ref, ArtifactRef)
            else artifact_ref
        )
        try:
            self._validate_directories()
            os.stat(
                f"{self._record_key(artifact_id)}.json",
                dir_fd=self._records_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return True


__all__ = [
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactStoreError",
    "CASReader",
    "FilesystemCAS",
    "InMemoryCAS",
]
