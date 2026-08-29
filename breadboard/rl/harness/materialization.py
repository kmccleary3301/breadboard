from __future__ import annotations
from builtins import BaseExceptionGroup

from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
import secrets
import shutil
import stat
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes

from .contracts import EffectiveExecutionPlan, MountAccess
from .runners.base import RunnerToolBinding

_DIGEST_PREFIX = "sha256:"


def _digest(value: Any) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value).hexdigest()


_READ_CHUNK_BYTES = 64 * 1024


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stable_directory_identity(
    metadata: os.stat_result,
) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _is_sparse(metadata: os.stat_result) -> bool:
    return metadata.st_size > 0 and metadata.st_blocks * 512 < metadata.st_size


def _write_all(fd: int, data: bytes, *, failure: str) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise RuntimeError(failure)
        remaining = remaining[written:]


def _directory_open_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _file_open_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


class _DirFd:
    """Owned directory descriptor with component-wise, no-follow operations."""

    __slots__ = ("fd",)

    def __init__(self, fd: int, *, duplicate: bool = True) -> None:
        self.fd = os.dup(fd) if duplicate else fd
        metadata = os.fstat(self.fd)
        if not stat.S_ISDIR(metadata.st_mode):
            self.close()
            raise ValueError("storage root must be a directory")

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @staticmethod
    def parts(relative: str) -> tuple[str, ...]:
        normalized = _logical_path(relative)
        parts = PurePosixPath(normalized).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("storage_relative_path_invalid")
        return parts

    def open_dir(self, relative: str = "") -> int:
        current = os.dup(self.fd)
        try:
            for part in () if not relative else self.parts(relative):
                child = os.open(part, _directory_open_flags(), dir_fd=current)
                os.close(current)
                current = child
            return current
        except BaseException:
            os.close(current)
            raise

    def mkdir(self, relative: str, *, mode: int = 0o700, parents: bool = False) -> None:
        parts = self.parts(relative)
        parent = os.dup(self.fd)
        try:
            for index, part in enumerate(parts):
                last = index == len(parts) - 1
                try:
                    os.mkdir(part, mode=mode if last else 0o700, dir_fd=parent)
                except FileExistsError:
                    if last and not parents:
                        raise
                if not last:
                    child = os.open(part, _directory_open_flags(), dir_fd=parent)
                    os.close(parent)
                    parent = child
        finally:
            os.close(parent)

    def exists(self, relative: str) -> bool:
        parts = self.parts(relative)
        parent = (
            self.open_dir("/".join(parts[:-1])) if len(parts) > 1 else os.dup(self.fd)
        )
        try:
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return True
        finally:
            os.close(parent)

    def open_file(self, relative: str, flags: int, mode: int = 0o600) -> int:
        parts = self.parts(relative)
        parent = (
            self.open_dir("/".join(parts[:-1])) if len(parts) > 1 else os.dup(self.fd)
        )
        try:
            return os.open(
                parts[-1], flags | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=parent
            )
        finally:
            os.close(parent)

    def replace(self, source: str, destination: str) -> None:
        source_parts = self.parts(source)
        destination_parts = self.parts(destination)
        source_parent = (
            self.open_dir("/".join(source_parts[:-1]))
            if len(source_parts) > 1
            else os.dup(self.fd)
        )
        destination_parent = (
            self.open_dir("/".join(destination_parts[:-1]))
            if len(destination_parts) > 1
            else os.dup(self.fd)
        )
        try:
            os.replace(
                source_parts[-1],
                destination_parts[-1],
                src_dir_fd=source_parent,
                dst_dir_fd=destination_parent,
            )
            os.fsync(destination_parent)
        finally:
            os.close(destination_parent)
            os.close(source_parent)

    def remove_tree(self, relative: str, *, missing_ok: bool = False) -> None:
        parts = self.parts(relative)
        parent = (
            self.open_dir("/".join(parts[:-1])) if len(parts) > 1 else os.dup(self.fd)
        )
        name = parts[-1]
        try:
            try:
                directory = os.open(name, _directory_open_flags(), dir_fd=parent)
            except FileNotFoundError:
                if missing_ok:
                    return
                raise
            os.fchmod(directory, stat.S_IMODE(os.fstat(directory).st_mode) | 0o700)
            try:
                for child_name in tuple(os.listdir(directory)):
                    metadata = os.stat(
                        child_name, dir_fd=directory, follow_symlinks=False
                    )
                    if stat.S_ISDIR(metadata.st_mode):
                        child_owner = _DirFd(directory)
                        try:
                            child_owner.remove_tree(child_name)
                        finally:
                            child_owner.close()
                    else:
                        os.unlink(child_name, dir_fd=directory)
                os.fsync(directory)
            finally:
                os.close(directory)
            os.rmdir(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)

    def fsync_dir(self, relative: str = "") -> None:
        directory = self.open_dir(relative)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _deep_freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if type(value) in {list, tuple}:
        return tuple(_deep_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _logical_path(value: str, *, allow_root: bool = False) -> str:
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        raise ValueError("path_invalid")
    if unicodedata.normalize("NFC", value) != value or value.startswith("/"):
        raise ValueError("path_invalid")
    path = PurePosixPath(value)
    parts = path.parts
    if any(part in {"", ".", ".."} or len(part.encode()) > 255 for part in parts):
        raise ValueError("path_invalid")
    rendered = path.as_posix()
    if rendered != value or (rendered == "." and not allow_root):
        raise ValueError("path_invalid")
    return rendered


def _check_path_set(paths: tuple[str, ...]) -> None:
    folded: dict[str, str] = {}
    for raw in paths:
        path = _logical_path(raw)
        alias = path.casefold()
        if alias in folded:
            raise ValueError("mount_collision")
        folded[alias] = path
    for path in folded.values():
        parts = PurePosixPath(path).parts
        for length in range(1, len(parts)):
            if "/".join(parts[:length]).casefold() in folded:
                raise ValueError("mount_collision")


class CacheLeaseState(str, Enum):
    BUILDING = "building"
    ACTIVE = "active"
    RELEASED = "released"
    FENCED = "fenced"
    QUARANTINED = "quarantined"


class WorkspaceLeaseState(str, Enum):
    ACTIVE = "active"
    QUIESCING = "quiescing"
    RELEASING = "releasing"
    RELEASED = "released"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class SnapshotState(str, Enum):
    SEALED = "sealed"
    RELEASED = "released"
    QUARANTINED = "quarantined"


class CleanupState(str, Enum):
    RELEASED = "released"
    ALREADY_RELEASED = "already_released"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class IsolationDisposition(str, Enum):
    ISOLATED = "isolated"
    TRUSTED_PROCESS = "trusted_process"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class WorkspaceOpenRequest:
    episode_id: str
    effective_plan: EffectiveExecutionPlan
    effective_plan_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.episode_id) is not str or not self.episode_id.strip():
            raise ValueError("episode_id must be non-empty")
        if type(self.effective_plan) is not EffectiveExecutionPlan:
            raise TypeError("effective_plan must be an exact EffectiveExecutionPlan")
        object.__setattr__(
            self, "effective_plan_digest", self.effective_plan.canonical_digest()
        )


@dataclass(frozen=True, slots=True)
class SourceManifestEntry:
    logical_path: str
    kind: str
    byte_count: int
    mode: int
    content_digest: str | None = None

    def __post_init__(self) -> None:
        _logical_path(self.logical_path)
        if self.kind not in {"file", "directory"}:
            raise ValueError("source manifest kind must be file or directory")
        if self.byte_count < 0 or self.mode < 0 or self.mode > 0o777:
            raise ValueError("invalid source manifest metadata")
        if self.kind == "file" and not (self.content_digest or "").startswith(
            _DIGEST_PREFIX
        ):
            raise ValueError("file entry requires a content digest")
        if self.kind == "directory" and (
            self.byte_count != 0 or self.content_digest is not None
        ):
            raise ValueError("directory entry cannot carry bytes")

    def projection(self) -> dict[str, Any]:
        return {
            "path": self.logical_path,
            "kind": self.kind,
            "bytes": self.byte_count,
            "mode": self.mode,
            "digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class SealedSourceManifest:
    source_digest: str
    schema_identity: str
    media_identity: str
    entries: tuple[SourceManifestEntry, ...]
    total_bytes: int
    total_files: int
    manifest_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.source_digest.startswith(_DIGEST_PREFIX):
            raise ValueError("invalid source digest")
        entries = tuple(self.entries)
        if entries != tuple(sorted(entries, key=lambda item: item.logical_path)):
            raise ValueError("source manifest entries must be sorted")
        aliases = tuple(entry.logical_path.casefold() for entry in entries)
        if len(aliases) != len(set(aliases)):
            raise ValueError("source manifest paths must be unique")
        if self.total_bytes != sum(entry.byte_count for entry in entries):
            raise ValueError("source manifest byte total mismatch")
        if self.total_files != sum(entry.kind == "file" for entry in entries):
            raise ValueError("source manifest file total mismatch")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "manifest_digest", _digest(self.projection()))

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_identity,
            "media_type": self.media_identity,
            "source_digest": self.source_digest,
            "entries": [entry.projection() for entry in self.entries],
            "total_bytes": self.total_bytes,
            "total_files": self.total_files,
        }


@dataclass(frozen=True, slots=True)
class MaterializationEntry:
    source_digest: str
    target_logical_path: str
    access: MountAccess
    max_bytes: int
    role: str

    def __post_init__(self) -> None:
        _logical_path(self.target_logical_path)
        if type(self.access) is not MountAccess or self.max_bytes <= 0:
            raise ValueError("invalid materialization entry")
        if self.role not in {"repository", "dataset", "input", "mount", "setup_input"}:
            raise ValueError("invalid materialization role")

    def projection(self) -> dict[str, Any]:
        return {
            "source_digest": self.source_digest,
            "target_logical_path": self.target_logical_path,
            "access": self.access.value,
            "max_bytes": self.max_bytes,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationPlan:
    episode_id: str
    subject_digest: str
    final_receipt_digest: str
    effective_plan_digest: str
    sandbox_projection: Mapping[str, Any]
    task_projection: Mapping[str, Any]
    setup_projections: tuple[Mapping[str, Any], ...]
    entries: tuple[MaterializationEntry, ...]
    tool_bindings: tuple[RunnerToolBinding, ...]
    resources_projection: Mapping[str, Any]
    limits_projection: Mapping[str, Any]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if entries != tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.target_logical_path,
                    item.source_digest,
                    item.role,
                ),
            )
        ):
            raise ValueError("materialization entries must be canonical")
        _check_path_set(tuple(entry.target_logical_path for entry in entries))
        object.__setattr__(self, "entries", entries)
        for name in (
            "sandbox_projection",
            "task_projection",
            "resources_projection",
            "limits_projection",
        ):
            object.__setattr__(self, name, _deep_freeze(dict(getattr(self, name))))
        object.__setattr__(
            self,
            "setup_projections",
            tuple(_deep_freeze(dict(value)) for value in self.setup_projections),
        )

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.workspace-materialization-plan.v1",
            "episode_id": self.episode_id,
            "subject_digest": self.subject_digest,
            "final_receipt_digest": self.final_receipt_digest,
            "effective_plan_digest": self.effective_plan_digest,
            "sandbox": _plain(self.sandbox_projection),
            "task": _plain(self.task_projection),
            "setups": [_plain(value) for value in self.setup_projections],
            "entries": [entry.projection() for entry in self.entries],
            "tools": [
                {
                    "tool_id": item.tool_id,
                    "implementation_digest": item.implementation_digest,
                    "capability_ids": list(item.capability_ids),
                }
                for item in self.tool_bindings
            ],
            "resources": _plain(self.resources_projection),
            "limits": _plain(self.limits_projection),
        }


@dataclass(frozen=True, slots=True)
class MaterializationKey:
    schema_version: str
    digest: str

    @classmethod
    def from_plan(cls, plan: WorkspaceMaterializationPlan) -> MaterializationKey:
        return cls("bb.rl.materialization-key.v1", _digest(plan.projection()))


@dataclass(frozen=True, slots=True)
class CacheLeaseToken:
    cache_key: MaterializationKey
    lease_id: str
    holder_id: str
    owner_token: str
    epoch: int
    issued_at: datetime
    expires_at: datetime
    state: CacheLeaseState


@dataclass(frozen=True, slots=True)
class CacheLeaseReceipt:
    cache_key: MaterializationKey
    immutable_object_manifest_digest: str
    holder_id: str
    epoch: int
    acquisition: str
    release_state: CacheLeaseState


@dataclass(frozen=True, slots=True)
class MaterializedMount:
    logical_destination: str
    backing_id: str
    access: MountAccess
    source_manifest_digest: str


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationReceipt:
    workspace_id: str
    cache_lease_id: str
    effective_plan_digest: str
    materialization_digest: str
    manifest_digest: str
    tool_binding_digest: str
    mounted_entries: tuple[MaterializedMount, ...]
    unique_identity: str


@dataclass(frozen=True, slots=True)
class SnapshotManifestEntry:
    logical_path: str
    kind: str
    mode: int
    size: int
    file_digest: str | None

    def projection(self) -> dict[str, Any]:
        return {
            "path": self.logical_path,
            "kind": self.kind,
            "mode": self.mode,
            "size": self.size,
            "digest": self.file_digest,
        }


@dataclass(frozen=True, slots=True)
class VerifierSnapshotReceipt:
    snapshot_id: str
    source_workspace_id: str
    source_lease_id: str
    effective_plan_digest: str
    task_digest: str
    verifier_digest: str
    manifest_digest: str
    root_digest: str
    file_count: int
    inode_count: int
    byte_count: int
    immutable_storage_object_id: str


@dataclass(frozen=True, slots=True)
class CleanupStepReceipt:
    resource: str
    state: CleanupState
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SandboxCleanupReceipt:
    lease_id: str
    steps: tuple[CleanupStepReceipt, ...]
    state: CleanupState

    @classmethod
    def from_steps(
        cls, lease_id: str, steps: tuple[CleanupStepReceipt, ...]
    ) -> SandboxCleanupReceipt:
        states = {step.state for step in steps}
        if CleanupState.QUARANTINED in states:
            aggregate = CleanupState.QUARANTINED
        elif CleanupState.FAILED in states:
            aggregate = CleanupState.FAILED
        elif states == {CleanupState.ALREADY_RELEASED}:
            aggregate = CleanupState.ALREADY_RELEASED
        else:
            aggregate = CleanupState.RELEASED
        return cls(lease_id, steps, aggregate)


class MaterializationSourceReader(Protocol):
    def load_manifest(self, digest: str, *, max_bytes: int) -> SealedSourceManifest: ...
    def read_member(
        self, digest: str, logical_path: str, *, max_bytes: int
    ) -> bytes: ...


class MaterializationClock(Protocol):
    def current(self) -> datetime: ...


class WorkspaceStorageBackend(Protocol):
    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path: ...
    def measure(self, backing: Path) -> Mapping[str, Any]: ...
    def release(self, backing: Path) -> None: ...
    def verify_absent(self, backing: Path) -> bool: ...


class DirectoryStorageBackend:
    """Directory workspace allocation rooted at an owned descriptor."""

    def __init__(self) -> None:
        self._owner: _DirFd | None = None

    def bind_root(self, descriptor: int) -> None:
        owner = getattr(self, "_owner", None)
        if owner is not None:
            owner.close()
        self._owner = _DirFd(descriptor)

    def close_root(self) -> None:
        owner = getattr(self, "_owner", None)
        if owner is not None:
            owner.close()
            self._owner = None

    def _workspace_id(self, backing: Path) -> str:
        name = backing.name
        if _logical_path(name) != name or len(PurePosixPath(name).parts) != 1:
            raise ValueError("workspace_path_invalid")
        return name

    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path:
        owner = getattr(self, "_owner", None)
        if owner is None:
            backing = root / workspace_id
            backing.mkdir(mode=0o700, parents=False, exist_ok=False)
            return backing
        owner.mkdir(workspace_id)
        return root / workspace_id

    def measure(self, backing: Path) -> Mapping[str, Any]:
        owner = getattr(self, "_owner", None)
        if owner is None:
            current = backing.stat()
        else:
            descriptor = owner.open_dir(self._workspace_id(backing))
            try:
                current = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        return {
            "owner_uid": current.st_uid,
            "owner_gid": current.st_gid,
            "mode": stat.S_IMODE(current.st_mode),
            "quota_enforced": False,
        }

    def release(self, backing: Path) -> None:
        owner = getattr(self, "_owner", None)
        if owner is None:
            shutil.rmtree(backing)
        else:
            owner.remove_tree(self._workspace_id(backing))

    def verify_absent(self, backing: Path) -> bool:
        owner = getattr(self, "_owner", None)
        if owner is None:
            return not backing.exists()
        return not owner.exists(self._workspace_id(backing))


class TmpfsQuotaStorageBackend(DirectoryStorageBackend):
    """Descriptor-rooted workspaces backed by exact-size Linux tmpfs mounts."""

    _MS_NOSUID = 2
    _MS_NODEV = 4
    _MNT_DETACH = 2

    def __init__(self) -> None:
        super().__init__()
        self._mounts: dict[str, tuple[int, int, int]] = {}
        self._mount_lock = threading.RLock()

    @staticmethod
    def _libc() -> Any:
        if os.name != "posix" or not Path("/proc/self/fd").is_dir():
            raise OSError(errno.ENOTSUP, "tmpfs quota workspaces require Linux procfs")
        return ctypes.CDLL(None, use_errno=True)

    @classmethod
    def _mount_tmpfs(cls, target: str, max_bytes: int) -> None:
        libc = cls._libc()
        mount = libc.mount
        mount.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        )
        mount.restype = ctypes.c_int
        options = f"size={max_bytes},mode=0700".encode("ascii")
        if (
            mount(
                b"breadboard-workspace",
                os.fsencode(target),
                b"tmpfs",
                cls._MS_NOSUID | cls._MS_NODEV,
                options,
            )
            != 0
        ):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), target)

    @classmethod
    def _unmount_tmpfs(cls, target: str, *, detach: bool = False) -> None:
        libc = cls._libc()
        umount2 = libc.umount2
        umount2.argtypes = (ctypes.c_char_p, ctypes.c_int)
        umount2.restype = ctypes.c_int
        flags = cls._MNT_DETACH if detach else 0
        if umount2(os.fsencode(target), flags) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), target)

    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path:
        owner = getattr(self, "_owner", None)
        if owner is None:
            raise RuntimeError("tmpfs quota backend requires a bound workspace root")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if type(max_bytes) is not int or max_bytes <= 0 or max_bytes % page_size:
            raise ValueError(
                "workspace quota must be a positive page-aligned byte count"
            )
        backing = super().allocate(
            workspace_id=workspace_id,
            root=root,
            max_bytes=max_bytes,
        )
        descriptor = owner.open_dir(workspace_id)
        target = f"/proc/self/fd/{owner.fd}/{workspace_id}"
        mounted = False
        try:
            self._mount_tmpfs(target, max_bytes)
            mounted = True
            mounted_descriptor = owner.open_dir(workspace_id)
            os.close(descriptor)
            descriptor = mounted_descriptor
            metadata = os.fstat(descriptor)
            filesystem = os.fstatvfs(descriptor)
            capacity = filesystem.f_blocks * filesystem.f_frsize
            if capacity != max_bytes:
                raise RuntimeError(
                    "tmpfs workspace quota does not match requested bytes"
                )
            with self._mount_lock:
                if workspace_id in self._mounts:
                    raise RuntimeError("workspace quota authority already exists")
                self._mounts[workspace_id] = (
                    metadata.st_dev,
                    metadata.st_ino,
                    max_bytes,
                )
            return backing
        except BaseException:
            if mounted:
                os.close(descriptor)
                descriptor = -1
                try:
                    self._unmount_tmpfs(target)
                except OSError:
                    pass
            super().release(backing)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def measure(self, backing: Path) -> Mapping[str, Any]:
        owner = getattr(self, "_owner", None)
        if owner is None:
            raise RuntimeError("tmpfs quota backend requires a bound workspace root")
        workspace_id = self._workspace_id(backing)
        descriptor = owner.open_dir(workspace_id)
        try:
            metadata = os.fstat(descriptor)
            capacity = (
                os.fstatvfs(descriptor).f_blocks * os.fstatvfs(descriptor).f_frsize
            )
        finally:
            os.close(descriptor)
        with self._mount_lock:
            authority = self._mounts.get(workspace_id)
        if authority is None or authority != (
            metadata.st_dev,
            metadata.st_ino,
            capacity,
        ):
            raise RuntimeError("tmpfs workspace quota authority changed")
        return {
            "authority_id": f"linux-tmpfs:{metadata.st_dev}:{metadata.st_ino}",
            "owner_uid": metadata.st_uid,
            "owner_gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "quota_enforced": True,
            "quota_bytes": capacity,
        }

    def release(self, backing: Path) -> None:
        owner = getattr(self, "_owner", None)
        if owner is None:
            raise RuntimeError("tmpfs quota backend requires a bound workspace root")
        workspace_id = self._workspace_id(backing)
        descriptor = owner.open_dir(workspace_id)
        target = f"/proc/self/fd/{owner.fd}/{workspace_id}"
        try:
            metadata = os.fstat(descriptor)
            with self._mount_lock:
                authority = self._mounts.get(workspace_id)
            if authority is None or authority[:2] != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise RuntimeError("tmpfs workspace quota authority changed")
        finally:
            os.close(descriptor)
        self._unmount_tmpfs(target)
        with self._mount_lock:
            del self._mounts[workspace_id]
        super().release(backing)


@dataclass(frozen=True, slots=True)
class _TmpfsMountRecord:
    mount_id: int
    device: bytes
    root: bytes
    mount_options: frozenset[bytes]
    filesystem_type: bytes
    source: bytes
    super_options: frozenset[bytes]


def _mountinfo_path(value: str) -> bytes:
    encoded = os.fsencode(value)
    return (
        encoded.replace(b"\\", b"\\134")
        .replace(b" ", b"\\040")
        .replace(b"\t", b"\\011")
        .replace(b"\n", b"\\012")
    )


def _tmpfs_mount_record(path: str) -> _TmpfsMountRecord:
    target = _mountinfo_path(path)
    matches: list[_TmpfsMountRecord] = []
    with open("/proc/self/mountinfo", "rb", buffering=0) as handle:
        for line in handle:
            fields = line.rstrip(b"\n").split()
            if len(fields) < 10 or fields[4] != target:
                continue
            try:
                separator = fields.index(b"-", 6)
                mount_id = int(fields[0])
            except (ValueError, IndexError) as exc:
                raise RuntimeError("tmpfs quota mountinfo is malformed") from exc
            if separator + 3 >= len(fields):
                raise RuntimeError("tmpfs quota mountinfo is malformed")
            matches.append(
                _TmpfsMountRecord(
                    mount_id=mount_id,
                    device=fields[2],
                    root=fields[3],
                    mount_options=frozenset(fields[5].split(b",")),
                    filesystem_type=fields[separator + 1],
                    source=fields[separator + 2],
                    super_options=frozenset(fields[separator + 3].split(b",")),
                )
            )
    if len(matches) != 1:
        raise RuntimeError("tmpfs quota mountinfo authority is not exact")
    return matches[0]


def _mount_id_present(mount_id: int) -> bool:
    prefix = str(mount_id).encode("ascii") + b" "
    with open("/proc/self/mountinfo", "rb", buffering=0) as handle:
        return any(line.startswith(prefix) for line in handle)


class TmpfsQuotaRootAuthority:
    """Pinned authority for one exact-size tmpfs mounted before runtime forks."""

    def __init__(self, path: str | Path, max_bytes: int) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        page_size = os.sysconf("SC_PAGE_SIZE")
        if type(max_bytes) is not int or max_bytes <= 0 or max_bytes % page_size:
            raise ValueError(
                "workspace quota must be a positive page-aligned byte count"
            )
        if not self.path.is_absolute() or os.path.normpath(
            os.fspath(self.path)
        ) != os.fspath(self.path):
            raise ValueError("tmpfs quota root path is not exact")
        covered_descriptor = os.open(
            self.path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            before = os.fstat(covered_descriptor)
            current = os.stat(self.path, follow_symlinks=False)
            if (
                _metadata_identity(current) != _metadata_identity(before)
                or not stat.S_ISDIR(before.st_mode)
                or os.listdir(covered_descriptor)
            ):
                raise RuntimeError("tmpfs quota root is not an exact empty directory")
        except BaseException:
            os.close(covered_descriptor)
            raise
        self._covered_descriptor = covered_descriptor
        self._covered_identity = _metadata_identity(before)
        self._mounted_identity: tuple[int, ...] | None = None
        self._record: _TmpfsMountRecord | None = None
        self._descriptor = -1
        self._mounted = False

    @property
    def mount_id(self) -> int:
        record = self._record
        if record is None:
            raise RuntimeError("tmpfs quota root authority is not mounted")
        return record.mount_id

    @property
    def mounted_identity(self) -> tuple[int, ...]:
        identity = self._mounted_identity
        if identity is None:
            raise RuntimeError("tmpfs quota root authority is not mounted")
        return identity

    def _validate_record(
        self,
        record: _TmpfsMountRecord,
        metadata: os.stat_result,
    ) -> None:
        expected_device = (
            str(os.major(metadata.st_dev)).encode("ascii")
            + b":"
            + str(os.minor(metadata.st_dev)).encode("ascii")
        )
        if (
            record.device != expected_device
            or record.root != b"/"
            or record.filesystem_type != b"tmpfs"
            or record.source != b"breadboard-workspace"
            or not {b"rw", b"nosuid", b"nodev"}.issubset(record.mount_options)
            or b"rw" not in record.super_options
            or not any(option.startswith(b"size=") for option in record.super_options)
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise RuntimeError("tmpfs quota root mount authority changed")

    def _capture(self) -> None:
        descriptor = os.open(
            self.path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            metadata = os.fstat(descriptor)
            record = _tmpfs_mount_record(os.fspath(self.path))
            self._validate_record(record, metadata)
            filesystem = os.fstatvfs(descriptor)
            if filesystem.f_blocks * filesystem.f_frsize != self.max_bytes:
                raise RuntimeError("tmpfs quota root capacity is inexact")
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        self._mounted_identity = _stable_directory_identity(metadata)
        self._record = record

    def _verify_covered(self) -> None:
        if self._covered_descriptor < 0:
            raise RuntimeError("tmpfs quota covered root authority is closed")
        held = os.fstat(self._covered_descriptor)
        current = os.stat(self.path, follow_symlinks=False)
        if (
            _metadata_identity(held) != self._covered_identity
            or _metadata_identity(current) != self._covered_identity
            or os.listdir(self._covered_descriptor)
            or tuple(os.scandir(self.path))
        ):
            raise RuntimeError("tmpfs quota covered root authority changed")

    def _prove_restored(self) -> None:
        self._verify_covered()

    def _detach_owned_mount(self) -> None:
        if self._descriptor >= 0:
            target = f"/proc/self/fd/{self._descriptor}"
        elif self._covered_descriptor >= 0:
            target = f"/proc/self/fd/{self._covered_descriptor}"
        else:
            target = os.fspath(self.path)
        TmpfsQuotaStorageBackend._unmount_tmpfs(target, detach=True)
        record = self._record
        if record is not None and _mount_id_present(record.mount_id):
            raise RuntimeError("tmpfs quota mount remained after unmount")
        self._mounted = False
        self._record = None
        self._mounted_identity = None
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1
        restoration_errors: list[BaseException] = []
        try:
            self._prove_restored()
        except BaseException as exc:
            restoration_errors.append(exc)
        try:
            os.close(self._covered_descriptor)
            self._covered_descriptor = -1
        except BaseException as exc:
            restoration_errors.append(exc)
        if restoration_errors:
            if len(restoration_errors) == 1:
                raise restoration_errors[0]
            raise BaseExceptionGroup(
                "tmpfs quota covered root cleanup failed",
                restoration_errors,
            )

    def mount(self) -> None:
        if self._mounted:
            raise RuntimeError("tmpfs quota root is already mounted")
        self._verify_covered()
        TmpfsQuotaStorageBackend._mount_tmpfs(
            f"/proc/self/fd/{self._covered_descriptor}",
            self.max_bytes,
        )
        self._mounted = True
        try:
            self._capture()
            self.verify()
        except BaseException as primary:
            try:
                self._detach_owned_mount()
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "tmpfs quota mount establishment and cleanup failed",
                    [primary, cleanup],
                ) from None
            raise

    def verify(self) -> None:
        if (
            not self._mounted
            or self._descriptor < 0
            or self._record is None
            or self._mounted_identity is None
        ):
            raise RuntimeError("tmpfs quota root authority is not mounted")
        held = os.fstat(self._descriptor)
        current = os.stat(self.path, follow_symlinks=False)
        record = _tmpfs_mount_record(os.fspath(self.path))
        held_filesystem = os.fstatvfs(self._descriptor)
        path_filesystem = os.statvfs(self.path)
        held_capacity = held_filesystem.f_blocks * held_filesystem.f_frsize
        path_capacity = path_filesystem.f_blocks * path_filesystem.f_frsize
        self._validate_record(record, current)
        if (
            _stable_directory_identity(held) != self._mounted_identity
            or _stable_directory_identity(current) != self._mounted_identity
            or record != self._record
            or held_capacity != self.max_bytes
            or path_capacity != self.max_bytes
        ):
            raise RuntimeError("tmpfs quota root authority changed")

    def close(self) -> None:
        if not self._mounted:
            if self._descriptor >= 0:
                os.close(self._descriptor)
                self._descriptor = -1
            if self._covered_descriptor >= 0:
                os.close(self._covered_descriptor)
                self._covered_descriptor = -1
            return
        errors: list[BaseException] = []
        try:
            self.verify()
        except BaseException as exc:
            errors.append(exc)
        try:
            self._detach_owned_mount()
        except BaseException as exc:
            errors.append(exc)
        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise BaseExceptionGroup(
                "tmpfs quota verification and cleanup failed",
                errors,
            )


class PreMountedTmpfsQuotaStorageBackend(DirectoryStorageBackend):
    """Per-workspace accounting within a pinned pre-mounted tmpfs authority."""

    def __init__(self, authority: TmpfsQuotaRootAuthority) -> None:
        super().__init__()
        self._authority = authority
        self._mounts: dict[str, tuple[int, int, int, int]] = {}
        self._pending_cleanup: set[str] = set()
        self._mount_lock = threading.RLock()

    def _clear_pending_allocation(self, workspace_id: str) -> None:
        owner = self._owner
        if owner is None:
            raise RuntimeError("pre-mounted quota backend root is unbound")
        backing = self._authority.path / workspace_id
        if owner.exists(workspace_id):
            DirectoryStorageBackend.release(self, backing)
        else:
            owner.fsync_dir()
        if owner.exists(workspace_id):
            raise RuntimeError("failed workspace allocation remains present")
        with self._mount_lock:
            self._pending_cleanup.discard(workspace_id)

    def _verify_root(self) -> None:
        self._authority.verify()
        owner = getattr(self, "_owner", None)
        if owner is None:
            raise RuntimeError("pre-mounted quota backend root is unbound")
        if (
            _stable_directory_identity(os.fstat(owner.fd))
            != self._authority.mounted_identity
        ):
            raise RuntimeError("pre-mounted quota backend root authority changed")

    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path:
        with self._mount_lock:
            pending = workspace_id in self._pending_cleanup
        if pending:
            self._clear_pending_allocation(workspace_id)
        if max_bytes != self._authority.max_bytes or os.path.normpath(
            os.fspath(root)
        ) != os.fspath(self._authority.path):
            raise RuntimeError("plan and pre-mounted workspace quotas differ")
        self._verify_root()
        backing = DirectoryStorageBackend.allocate(
            self,
            workspace_id=workspace_id,
            root=root,
            max_bytes=max_bytes,
        )
        try:
            owner = self._owner
            if owner is None:
                raise RuntimeError("pre-mounted quota backend root is unbound")
            descriptor = owner.open_dir(workspace_id)
            try:
                metadata = os.fstat(descriptor)
                filesystem = os.fstatvfs(descriptor)
            finally:
                os.close(descriptor)
            capacity = filesystem.f_blocks * filesystem.f_frsize
            if capacity != max_bytes:
                raise RuntimeError("workspace quota capacity is inexact")
            with self._mount_lock:
                if workspace_id in self._mounts:
                    raise RuntimeError("workspace quota authority already exists")
                self._mounts[workspace_id] = (
                    metadata.st_dev,
                    metadata.st_ino,
                    capacity,
                    self._authority.mount_id,
                )
        except BaseException as primary:
            try:
                self._clear_pending_allocation(workspace_id)
            except BaseException as cleanup:
                with self._mount_lock:
                    self._pending_cleanup.add(workspace_id)
                raise BaseExceptionGroup(
                    "workspace quota allocation and cleanup failed",
                    [primary, cleanup],
                ) from None
            raise
        return backing

    def measure(self, backing: Path) -> Mapping[str, Any]:
        self._verify_root()
        owner = self._owner
        if owner is None:
            raise RuntimeError("pre-mounted quota backend root is unbound")
        workspace_id = self._workspace_id(backing)
        descriptor = owner.open_dir(workspace_id)
        try:
            metadata = os.fstat(descriptor)
            filesystem = os.fstatvfs(descriptor)
        finally:
            os.close(descriptor)
        capacity = filesystem.f_blocks * filesystem.f_frsize
        with self._mount_lock:
            authority = self._mounts.get(workspace_id)
        if authority != (
            metadata.st_dev,
            metadata.st_ino,
            capacity,
            self._authority.mount_id,
        ):
            raise RuntimeError("workspace quota authority changed")
        return {
            "authority_id": (
                f"linux-tmpfs:{self._authority.mount_id}:"
                f"{metadata.st_dev}:{metadata.st_ino}"
            ),
            "owner_uid": metadata.st_uid,
            "owner_gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "quota_enforced": True,
            "quota_bytes": capacity,
        }

    def release(self, backing: Path) -> None:
        self._verify_root()
        owner = self._owner
        if owner is None:
            raise RuntimeError("pre-mounted quota backend root is unbound")
        workspace_id = self._workspace_id(backing)
        with self._mount_lock:
            authority = self._mounts.get(workspace_id)
        if authority is None:
            raise RuntimeError("workspace quota authority is absent")
        if not owner.exists(workspace_id):
            owner.fsync_dir()
            with self._mount_lock:
                if self._mounts.get(workspace_id) != authority:
                    raise RuntimeError("workspace quota authority changed")
                del self._mounts[workspace_id]
            return
        self.measure(backing)
        DirectoryStorageBackend.release(self, backing)
        if not DirectoryStorageBackend.verify_absent(self, backing):
            raise RuntimeError("workspace quota release did not prove absence")
        with self._mount_lock:
            if self._mounts.get(workspace_id) != authority:
                raise RuntimeError("workspace quota authority changed")
            del self._mounts[workspace_id]

    def close_root(self) -> None:
        pending_errors: list[BaseException] = []
        with self._mount_lock:
            pending = tuple(self._pending_cleanup)
        for workspace_id in pending:
            try:
                self._clear_pending_allocation(workspace_id)
            except BaseException as exc:
                pending_errors.append(exc)
        if pending_errors:
            raise BaseExceptionGroup(
                "pending workspace allocation cleanup failed",
                pending_errors,
            )
        with self._mount_lock:
            if self._mounts:
                raise RuntimeError(
                    "pre-mounted quota backend has live workspace authorities"
                )
        super().close_root()


@dataclass(slots=True)
class MaterializedWorkspace:
    receipt: WorkspaceMaterializationReceipt
    workspace_path: Path
    cache_token: CacheLeaseToken
    cache_receipt: CacheLeaseReceipt
    _store: FilesystemMaterializationStore
    _workspace_fd: int | None = field(repr=False)
    _workspace_identity: tuple[int, int] = field(repr=False)
    _fd_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def duplicate_workspace_fd(self) -> int:
        with self._fd_lock:
            descriptor = self._workspace_fd
            if descriptor is None:
                raise RuntimeError("workspace_released")
            return os.dup(descriptor)

    @property
    def workspace_identity(self) -> tuple[int, int]:
        return self._workspace_identity

    def _close_workspace_fd(self) -> None:
        with self._fd_lock:
            descriptor = self._workspace_fd
            if descriptor is not None:
                os.close(descriptor)
                self._workspace_fd = None

    def close(self) -> CacheLeaseReceipt:
        return self._store.release(self)


class FilesystemMaterializationStore:
    _authority_admitted_hook: Callable[[], None] | None = None

    def __init__(
        self,
        *,
        cache_root: str | Path,
        workspace_root: str | Path,
        source_reader: MaterializationSourceReader,
        clock: MaterializationClock,
        lease_ttl: timedelta,
        storage_backend: WorkspaceStorageBackend,
        random_bytes: Any = secrets.token_bytes,
        cache_root_fd: int | None = None,
        workspace_root_fd: int | None = None,
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        original_cache_root = Path(cache_root).resolve(strict=True)
        original_workspace_root = Path(workspace_root).resolve(strict=True)
        self._cache_root_fd = (
            os.dup(cache_root_fd)
            if cache_root_fd is not None
            else os.open(original_cache_root, _directory_open_flags())
        )
        try:
            self._workspace_root_fd = (
                os.dup(workspace_root_fd)
                if workspace_root_fd is not None
                else os.open(original_workspace_root, _directory_open_flags())
            )
        except BaseException:
            os.close(self._cache_root_fd)
            self._cache_root_fd = None
            raise
        try:
            cache_metadata = os.fstat(self._cache_root_fd)
            workspace_metadata = os.fstat(self._workspace_root_fd)
            if not stat.S_ISDIR(cache_metadata.st_mode) or not stat.S_ISDIR(
                workspace_metadata.st_mode
            ):
                raise ValueError("storage roots must be directories")
            if cache_root_fd is not None:
                named = os.stat(original_cache_root, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != (
                    cache_metadata.st_dev,
                    cache_metadata.st_ino,
                ):
                    raise ValueError("cache root descriptor identity mismatch")
            if workspace_root_fd is not None:
                named = os.stat(original_workspace_root, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != (
                    workspace_metadata.st_dev,
                    workspace_metadata.st_ino,
                ):
                    raise ValueError("workspace root descriptor identity mismatch")
        except BaseException:
            self.close()
            raise
        self.cache_root = original_cache_root
        self.workspace_root = original_workspace_root
        if (cache_metadata.st_dev, cache_metadata.st_ino) == (
            workspace_metadata.st_dev,
            workspace_metadata.st_ino,
        ):
            self.close()
            raise ValueError("cache and workspace roots must differ")
        self._cache = _DirFd(self._cache_root_fd)
        try:
            self._workspace = _DirFd(self._workspace_root_fd)
        except BaseException:
            self._cache.close()
            self.close()
            raise
        self.source_reader = source_reader
        self.clock = clock
        self.lease_ttl = lease_ttl
        self.storage_backend = storage_backend
        if isinstance(storage_backend, DirectoryStorageBackend):
            storage_backend.bind_root(self._workspace_root_fd)
        self._random_bytes = random_bytes
        self._lock = threading.RLock()
        self._released: dict[str, CacheLeaseReceipt] = {}
        self._active_workspaces: dict[str, MaterializedWorkspace] = {}
        try:
            hook = type(self)._authority_admitted_hook
            if hook is not None:
                hook()
            for name in (
                "objects",
                "snapshot-objects",
                "leases",
                "staging",
                "quarantine",
                "snapshot-references",
                "snapshot-staging",
            ):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=self._cache_root_fd)
                except FileExistsError:
                    metadata = os.stat(
                        name, dir_fd=self._cache_root_fd, follow_symlinks=False
                    )
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise
            os.fsync(self._cache_root_fd)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for workspace in tuple(getattr(self, "_active_workspaces", {}).values()):
            workspace._close_workspace_fd()
        getattr(self, "_active_workspaces", {}).clear()
        backend = getattr(self, "storage_backend", None)
        if isinstance(backend, DirectoryStorageBackend):
            backend.close_root()
        for owner_name, descriptor_name in (
            ("_workspace", "_workspace_root_fd"),
            ("_cache", "_cache_root_fd"),
        ):
            owner = getattr(self, owner_name, None)
            if owner is not None:
                owner.close()
                setattr(self, owner_name, None)
            descriptor = getattr(self, descriptor_name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, descriptor_name, None)

    def _nonce(self, size: int = 16) -> str:
        value = self._random_bytes(size)
        if type(value) is not bytes or len(value) < size:
            raise ValueError("random source returned insufficient bytes")
        return value.hex()

    def _record_path(self, key: MaterializationKey) -> Path:
        return self.cache_root / "leases" / key.digest.removeprefix(_DIGEST_PREFIX)

    def _record_relative(self, key: MaterializationKey) -> str:
        return "leases/" + key.digest.removeprefix(_DIGEST_PREFIX)

    def _object_path(self, key: MaterializationKey) -> Path:
        return self.cache_root / "objects" / key.digest.removeprefix(_DIGEST_PREFIX)

    def _object_relative(self, key: MaterializationKey) -> str:
        return "objects/" + key.digest.removeprefix(_DIGEST_PREFIX)

    def _read_regular(self, relative: str, *, max_bytes: int, failure: str) -> bytes:
        descriptor = self._cache.open_file(relative, _file_open_flags())
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size < 0
                or metadata.st_size > max_bytes
                or _is_sparse(metadata)
            ):
                raise RuntimeError(failure)
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, _READ_CHUNK_BYTES))
                if not chunk:
                    raise RuntimeError(failure)
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1) or _metadata_identity(
                os.fstat(descriptor)
            ) != _metadata_identity(metadata):
                raise RuntimeError(failure)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _write_record(self, path: Path, payload: dict[str, Any]) -> None:
        if path.parent != self.cache_root / "leases":
            raise ValueError("cache_record_path_invalid")
        relative = "leases/" + path.name
        body = canonical_json_bytes(payload)
        envelope = canonical_json_bytes(
            {"payload": payload, "checksum": _bytes_digest(body)}
        )
        temporary = relative + ".tmp-" + self._nonce(8)
        descriptor: int | None = None
        try:
            descriptor = self._cache.open_file(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            self._cache.replace(temporary, relative)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            parts = self._cache.parts(temporary)
            parent = self._cache.open_dir("/".join(parts[:-1]))
            try:
                try:
                    os.unlink(parts[-1], dir_fd=parent)
                except FileNotFoundError:
                    pass
            finally:
                os.close(parent)

    def _read_record(self, path: Path) -> dict[str, Any] | None:
        if path.parent != self.cache_root / "leases":
            raise ValueError("cache_record_path_invalid")
        relative = "leases/" + path.name
        try:
            raw = self._read_regular(
                relative, max_bytes=64 * 1024, failure="cache_record_corrupt"
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            self._cache.replace(
                relative, "quarantine/" + path.name + "-" + self._nonce(8)
            )
            raise RuntimeError("cache_record_corrupt") from exc
        try:
            envelope = json.loads(raw)
            payload = envelope["payload"]
            if envelope["checksum"] != _bytes_digest(canonical_json_bytes(payload)):
                raise ValueError
            if payload["schema_version"] != "bb.rl.cache-lease.v1":
                raise ValueError
            return payload
        except Exception as exc:
            self._cache.replace(
                relative, "quarantine/" + path.name + "-" + self._nonce(8)
            )
            raise RuntimeError("cache_record_corrupt") from exc

    def _verify_tree(
        self,
        root: str,
        manifest: SealedSourceManifest,
        *,
        root_owner: _DirFd | None = None,
        destination: str | None = None,
        destination_owner: _DirFd | None = None,
        read_only: bool = False,
    ) -> None:
        root_owner = self._cache if root_owner is None else root_owner
        if destination is not None and destination_owner is None:
            raise ValueError("destination owner required")
        expected = {entry.logical_path: entry for entry in manifest.entries}
        seen: set[str] = set()

        def walk(directory_fd: int, destination_fd: int | None, prefix: str) -> None:
            names = tuple(sorted(os.listdir(directory_fd)))
            for name in names:
                relative = f"{prefix}/{name}" if prefix else name
                try:
                    _logical_path(relative)
                    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except (OSError, ValueError) as exc:
                    raise RuntimeError("materialization_tampered") from exc
                item = expected.get(relative)
                kind = (
                    "directory"
                    if stat.S_ISDIR(before.st_mode)
                    else "file"
                    if stat.S_ISREG(before.st_mode)
                    else "special"
                )
                if (
                    item is None
                    or item.kind != kind
                    or stat.S_IMODE(before.st_mode) != item.mode
                    or (kind == "file" and before.st_nlink != 1)
                ):
                    raise RuntimeError("materialization_tampered")
                seen.add(relative)
                if kind == "directory":
                    try:
                        child_fd = os.open(
                            name, _directory_open_flags(), dir_fd=directory_fd
                        )
                    except OSError as exc:
                        raise RuntimeError("materialization_tampered") from exc
                    child_destination_fd: int | None = None
                    try:
                        if _metadata_identity(os.fstat(child_fd)) != _metadata_identity(
                            before
                        ):
                            raise RuntimeError("materialization_tampered")
                        if destination_fd is not None:
                            os.mkdir(name, mode=0o700, dir_fd=destination_fd)
                            child_destination_fd = os.open(
                                name, _directory_open_flags(), dir_fd=destination_fd
                            )
                        walk(child_fd, child_destination_fd, relative)
                        if _metadata_identity(os.fstat(child_fd)) != _metadata_identity(
                            before
                        ):
                            raise RuntimeError("materialization_tampered")
                        if child_destination_fd is not None:
                            os.fsync(child_destination_fd)
                    except OSError as exc:
                        raise RuntimeError("materialization_tampered") from exc
                    finally:
                        if child_destination_fd is not None:
                            os.close(child_destination_fd)
                        os.close(child_fd)
                    continue
                if before.st_size != item.byte_count:
                    raise RuntimeError("materialization_tampered")
                try:
                    file_fd = os.open(name, _file_open_flags(), dir_fd=directory_fd)
                except OSError as exc:
                    raise RuntimeError("materialization_tampered") from exc
                destination_file_fd: int | None = None
                try:
                    opened = os.fstat(file_fd)
                    if (
                        _metadata_identity(opened) != _metadata_identity(before)
                        or not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                    ):
                        raise RuntimeError("materialization_tampered")
                    if destination_fd is not None:
                        destination_file_fd = os.open(
                            name,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | getattr(os, "O_NOFOLLOW", 0),
                            0o600,
                            dir_fd=destination_fd,
                        )
                    digest = hashlib.sha256()
                    remaining = item.byte_count
                    while remaining:
                        chunk = os.read(file_fd, min(remaining, _READ_CHUNK_BYTES))
                        if not chunk:
                            raise RuntimeError("materialization_tampered")
                        digest.update(chunk)
                        if destination_file_fd is not None:
                            _write_all(
                                destination_file_fd,
                                chunk,
                                failure="materialization_tampered",
                            )
                        remaining -= len(chunk)
                    if os.read(file_fd, 1):
                        raise RuntimeError("materialization_tampered")
                    actual_digest = _DIGEST_PREFIX + digest.hexdigest()
                    if actual_digest != item.content_digest or _metadata_identity(
                        os.fstat(file_fd)
                    ) != _metadata_identity(opened):
                        raise RuntimeError("materialization_tampered")
                    if destination_file_fd is not None:
                        os.fchmod(
                            destination_file_fd,
                            0o400 if read_only else item.mode,
                        )
                        os.fsync(destination_file_fd)
                except OSError as exc:
                    raise RuntimeError("materialization_tampered") from exc
                finally:
                    if destination_file_fd is not None:
                        os.close(destination_file_fd)
                    os.close(file_fd)
            if tuple(sorted(os.listdir(directory_fd))) != names:
                raise RuntimeError("materialization_tampered")

        try:
            root_fd = root_owner.open_dir(root)
        except OSError as exc:
            raise RuntimeError("materialization_tampered") from exc
        destination_root_fd: int | None = None
        try:
            root_before = os.fstat(root_fd)
            if not stat.S_ISDIR(root_before.st_mode):
                raise RuntimeError("materialization_tampered")
            if destination is not None:
                assert destination_owner is not None
                destination_owner.mkdir(destination)
                destination_root_fd = destination_owner.open_dir(destination)
            walk(root_fd, destination_root_fd, "")
            if _metadata_identity(os.fstat(root_fd)) != _metadata_identity(root_before):
                raise RuntimeError("materialization_tampered")
            if destination_root_fd is not None:
                os.fsync(destination_root_fd)
        finally:
            if destination_root_fd is not None:
                os.close(destination_root_fd)
            os.close(root_fd)
        if seen != set(expected):
            raise RuntimeError("materialization_tampered")
        if destination is not None:
            assert destination_owner is not None
            destination_fd = destination_owner.open_dir(destination)
            try:
                directories = (
                    item for item in manifest.entries if item.kind == "directory"
                )
                for item in sorted(
                    directories,
                    key=lambda entry: len(PurePosixPath(entry.logical_path).parts),
                    reverse=True,
                ):
                    os.chmod(
                        item.logical_path,
                        0o500 if read_only else item.mode,
                        dir_fd=destination_fd,
                        follow_symlinks=False,
                    )
                os.fsync(destination_fd)
            finally:
                os.close(destination_fd)

    def _publish_source(
        self, entry: MaterializationEntry, destination: str
    ) -> SealedSourceManifest:
        manifest = self.source_reader.load_manifest(
            entry.source_digest, max_bytes=entry.max_bytes
        )
        if (
            manifest.source_digest != entry.source_digest
            or manifest.total_bytes > entry.max_bytes
        ):
            raise RuntimeError("source_digest_mismatch")
        self._cache.mkdir(destination)
        for item in manifest.entries:
            relative = destination + "/" + _logical_path(item.logical_path)
            if item.kind == "directory":
                self._cache.mkdir(relative, mode=item.mode, parents=True)
                continue
            parent = str(PurePosixPath(relative).parent)
            self._cache.mkdir(parent, parents=True)
            data = self.source_reader.read_member(
                entry.source_digest, item.logical_path, max_bytes=item.byte_count
            )
            if (
                len(data) != item.byte_count
                or _bytes_digest(data) != item.content_digest
            ):
                raise RuntimeError("source_digest_mismatch")
            descriptor = self._cache.open_file(
                relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL, item.mode
            )
            try:
                _write_all(descriptor, data, failure="atomic_publish_failed")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        self._verify_tree(destination, manifest)
        self._cache.fsync_dir(destination)
        return manifest

    def materialize(self, plan: WorkspaceMaterializationPlan) -> MaterializedWorkspace:
        import fcntl

        key = MaterializationKey.from_plan(plan)
        lock_relative = "leases/" + key.digest.removeprefix(_DIGEST_PREFIX) + ".lock"
        lock_fd = self._cache.open_file(lock_relative, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("cache_lock_invalid")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return self._materialize_locked(plan)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def _materialize_locked(
        self, plan: WorkspaceMaterializationPlan
    ) -> MaterializedWorkspace:
        key = MaterializationKey.from_plan(plan)
        now = _utc(self.clock.current())
        holder = "holder-" + self._nonce()
        token_value = self._nonce()
        lease_id = "cache-" + self._nonce()
        with self._lock:
            record_path = self._record_path(key)
            prior = self._read_record(record_path)
            epoch = 1
            if prior is not None:
                epoch = int(prior["epoch"])
                state = prior["state"]
                expiry = datetime.fromisoformat(prior["expires_at"])
                if state == CacheLeaseState.BUILDING.value and now < expiry:
                    raise RuntimeError("cache_lease_busy")
                if state == CacheLeaseState.BUILDING.value:
                    epoch += 1
            payload = {
                "schema_version": "bb.rl.cache-lease.v1",
                "key": key.digest,
                "lease_id": lease_id,
                "holder_id": holder,
                "owner_token": token_value,
                "epoch": epoch,
                "issued_at": now.isoformat(),
                "expires_at": (now + self.lease_ttl).isoformat(),
                "state": CacheLeaseState.BUILDING.value,
            }
            self._write_record(record_path, payload)
            object_relative = self._object_relative(key)
            acquisition = "hit"
            manifests: list[SealedSourceManifest] = []
            try:
                if self._cache.exists(object_relative):
                    for index, entry in enumerate(plan.entries):
                        manifest = self.source_reader.load_manifest(
                            entry.source_digest, max_bytes=entry.max_bytes
                        )
                        self._verify_tree(
                            object_relative + f"/source-{index}", manifest
                        )
                        manifests.append(manifest)
                    expected_meta = canonical_json_bytes(
                        {
                            "schema_version": "bb.rl.materialized-object.v1",
                            "key": key.digest,
                            "source_manifests": [
                                item.manifest_digest for item in manifests
                            ],
                        }
                    )
                    actual_meta = self._read_regular(
                        object_relative + "/.bb-manifest.json",
                        max_bytes=len(expected_meta),
                        failure="materialization_tampered",
                    )
                    if actual_meta != expected_meta:
                        raise RuntimeError("materialization_tampered")
                else:
                    acquisition = "built"
                    staging = "staging/build-" + self._nonce()
                    self._cache.mkdir(staging)
                    try:
                        for index, entry in enumerate(plan.entries):
                            manifests.append(
                                self._publish_source(
                                    entry, staging + f"/source-{index}"
                                )
                            )
                        manifest_payload = {
                            "schema_version": "bb.rl.materialized-object.v1",
                            "key": key.digest,
                            "source_manifests": [
                                item.manifest_digest for item in manifests
                            ],
                        }
                        manifest_fd = self._cache.open_file(
                            staging + "/.bb-manifest.json",
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                        )
                        try:
                            _write_all(
                                manifest_fd,
                                canonical_json_bytes(manifest_payload),
                                failure="atomic_publish_failed",
                            )
                            os.fsync(manifest_fd)
                        finally:
                            os.close(manifest_fd)
                        self._cache.fsync_dir(staging)
                        current = self._read_record(record_path)
                        if (
                            current is None
                            or current["owner_token"] != token_value
                            or current["epoch"] != epoch
                        ):
                            raise RuntimeError("cache_lease_fenced")
                        self._cache.replace(staging, object_relative)
                    except BaseException:
                        self._cache.remove_tree(staging, missing_ok=True)
                        raise
                workspace_id = "workspace-" + self._nonce()
                if isinstance(self.storage_backend, DirectoryStorageBackend):
                    workspace = self.storage_backend.allocate(
                        workspace_id=workspace_id,
                        root=self.workspace_root,
                        max_bytes=int(plan.resources_projection["storage_bytes"]),
                    )
                else:
                    self._workspace.mkdir(workspace_id)
                    workspace = self.workspace_root / workspace_id
                mounts: list[MaterializedMount] = []
                try:
                    for index, entry in enumerate(plan.entries):
                        target = (
                            workspace_id
                            + "/"
                            + _logical_path(entry.target_logical_path)
                        )
                        parent = str(PurePosixPath(target).parent)
                        self._workspace.mkdir(parent, parents=True)
                        self._verify_tree(
                            object_relative + f"/source-{index}",
                            manifests[index],
                            destination=target,
                            destination_owner=self._workspace,
                            read_only=entry.access is MountAccess.READ_ONLY,
                        )
                        mounts.append(
                            MaterializedMount(
                                entry.target_logical_path,
                                f"private-{workspace_id}-{index}",
                                entry.access,
                                manifests[index].manifest_digest,
                            )
                        )
                except BaseException:
                    if isinstance(self.storage_backend, DirectoryStorageBackend):
                        self.storage_backend.release(workspace)
                    else:
                        self._workspace.remove_tree(workspace_id, missing_ok=True)
                    raise
                manifest_digest = _digest([item.manifest_digest for item in manifests])
                receipt = WorkspaceMaterializationReceipt(
                    workspace_id,
                    lease_id,
                    plan.effective_plan_digest,
                    key.digest,
                    manifest_digest,
                    _digest(
                        [
                            {
                                "tool_id": item.tool_id,
                                "implementation_digest": item.implementation_digest,
                                "capability_ids": list(item.capability_ids),
                            }
                            for item in plan.tool_bindings
                        ]
                    ),
                    tuple(mounts),
                    _digest({"workspace_id": workspace_id, "nonce": self._nonce()}),
                )
                token = CacheLeaseToken(
                    key,
                    lease_id,
                    holder,
                    token_value,
                    epoch,
                    now,
                    now + self.lease_ttl,
                    CacheLeaseState.ACTIVE,
                )
                receipt_cache = CacheLeaseReceipt(
                    key,
                    manifest_digest,
                    holder,
                    epoch,
                    acquisition,
                    CacheLeaseState.ACTIVE,
                )
                payload.update(
                    {
                        "cache_manifest_digest": manifest_digest,
                        "workspace_id": workspace_id,
                        "workspace_path": str(workspace),
                        "effective_plan_digest": plan.effective_plan_digest,
                        "source_digests": [
                            entry.source_digest for entry in plan.entries
                        ],
                    }
                )
                payload["state"] = CacheLeaseState.ACTIVE.value
                self._write_record(record_path, payload)
                workspace_fd = self._workspace.open_dir(workspace_id)
                workspace_metadata = os.fstat(workspace_fd)
                materialized = MaterializedWorkspace(
                    receipt,
                    workspace,
                    token,
                    receipt_cache,
                    self,
                    workspace_fd,
                    (workspace_metadata.st_dev, workspace_metadata.st_ino),
                )
                self._active_workspaces[lease_id] = materialized
                return materialized
            except BaseException:
                payload["state"] = CacheLeaseState.QUARANTINED.value
                self._write_record(record_path, payload)
                raise

    def release(self, workspace: MaterializedWorkspace) -> CacheLeaseReceipt:
        import fcntl

        relative = (
            "leases/"
            + workspace.cache_token.cache_key.digest.removeprefix(_DIGEST_PREFIX)
            + ".lock"
        )
        lock_fd = self._cache.open_file(relative, os.O_RDWR)
        try:
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("cache_lock_invalid")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return self._release_locked(workspace)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def _release_locked(self, workspace: MaterializedWorkspace) -> CacheLeaseReceipt:
        with self._lock:
            existing = self._released.get(workspace.cache_token.lease_id)
            if existing is not None:
                workspace._close_workspace_fd()
                return existing
            try:
                if isinstance(self.storage_backend, DirectoryStorageBackend):
                    self.storage_backend.release(workspace.workspace_path)
                    absent = self.storage_backend.verify_absent(
                        workspace.workspace_path
                    )
                else:
                    self._workspace.remove_tree(workspace.receipt.workspace_id)
                    absent = not self._workspace.exists(workspace.receipt.workspace_id)
                if not absent:
                    raise RuntimeError("workspace_removal_failed")
                state = CacheLeaseState.RELEASED
            except FileNotFoundError:
                state = CacheLeaseState.RELEASED
            workspace._close_workspace_fd()
            self._active_workspaces.pop(workspace.cache_token.lease_id, None)
            record_path = self._record_path(workspace.cache_token.cache_key)
            payload = self._read_record(record_path)
            if payload is None:
                raise RuntimeError("cache_lease_fenced")
            expected = {
                "key": workspace.cache_token.cache_key.digest,
                "lease_id": workspace.cache_token.lease_id,
                "holder_id": workspace.cache_token.holder_id,
                "owner_token": workspace.cache_token.owner_token,
                "epoch": workspace.cache_token.epoch,
                "cache_manifest_digest": workspace.cache_receipt.immutable_object_manifest_digest,
                "workspace_id": workspace.receipt.workspace_id,
                "workspace_path": str(workspace.workspace_path),
                "effective_plan_digest": workspace.receipt.effective_plan_digest,
            }
            if (
                payload.get("schema_version") != "bb.rl.cache-lease.v1"
                or any(payload.get(name) != value for name, value in expected.items())
                or payload.get("state")
                not in {CacheLeaseState.ACTIVE.value, CacheLeaseState.RELEASED.value}
            ):
                raise RuntimeError("cache_lease_fenced")
            payload["state"] = CacheLeaseState.RELEASED.value
            self._write_record(record_path, payload)
            receipt = CacheLeaseReceipt(
                workspace.cache_token.cache_key,
                workspace.cache_receipt.immutable_object_manifest_digest,
                workspace.cache_token.holder_id,
                workspace.cache_token.epoch,
                workspace.cache_receipt.acquisition,
                state,
            )
            self._released[workspace.cache_token.lease_id] = receipt
            self._active_workspaces.pop(workspace.cache_token.lease_id, None)
            return receipt

    def recover_stale_cache_holder(
        self, record: Mapping[str, Any]
    ) -> CleanupStepReceipt:
        import fcntl

        try:
            frozen_record = dict(record) if isinstance(record, Mapping) else None
        except Exception:
            frozen_record = None
        if frozen_record is None:
            return CleanupStepReceipt(
                "cache_holder",
                CleanupState.QUARANTINED,
                "stale_identity_uncertain",
            )
        source_values = frozen_record.get("cache_source_digests")
        if type(source_values) is list:
            frozen_record["cache_source_digests"] = list(source_values)
        key_digest = frozen_record.get("cache_key_digest")
        if (
            type(key_digest) is not str
            or not key_digest.startswith(_DIGEST_PREFIX)
            or len(key_digest) != len(_DIGEST_PREFIX) + 64
        ):
            return self._recover_stale_cache_holder_locked(frozen_record)
        try:
            int(key_digest.removeprefix(_DIGEST_PREFIX), 16)
        except ValueError:
            return self._recover_stale_cache_holder_locked(frozen_record)
        lock_relative = "leases/" + key_digest.removeprefix(_DIGEST_PREFIX) + ".lock"
        lock_fd: int | None = None
        try:
            lock_fd = self._cache.open_file(lock_relative, os.O_RDWR)
            lock_metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1:
                raise OSError("cache lock identity uncertain")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return self._recover_stale_cache_holder_locked(frozen_record)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            return CleanupStepReceipt(
                "cache_holder",
                CleanupState.QUARANTINED,
                "stale_identity_uncertain",
            )
        finally:
            if lock_fd is not None:
                os.close(lock_fd)

    def _recover_stale_cache_holder_locked(
        self, record: Mapping[str, Any]
    ) -> CleanupStepReceipt:
        quarantined = CleanupStepReceipt(
            "cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"
        )
        required_types = {
            "cache_lease_id": str,
            "cache_holder_id": str,
            "cache_token_value": str,
            "cache_epoch": int,
            "cache_key_digest": str,
            "cache_manifest_digest": str,
            "workspace_id": str,
            "workspace_path": str,
            "effective_plan_digest": str,
            "cache_source_digests": list,
        }
        if type(record) is not dict and not isinstance(record, Mapping):
            return quarantined
        if any(
            type(record.get(name)) is not kind for name, kind in required_types.items()
        ):
            return quarantined
        source_digests = tuple(record["cache_source_digests"])
        if any(
            type(digest) is not str
            or not digest.startswith(_DIGEST_PREFIX)
            or len(digest) != len(_DIGEST_PREFIX) + 64
            for digest in source_digests
        ):
            return quarantined
        try:
            for digest in source_digests:
                int(digest.removeprefix(_DIGEST_PREFIX), 16)
        except ValueError:
            return quarantined
        workspace_id = record["workspace_id"]
        try:
            if (
                _logical_path(workspace_id) != workspace_id
                or len(PurePosixPath(workspace_id).parts) != 1
            ):
                return quarantined
        except ValueError:
            return quarantined
        workspace_path = Path(record["workspace_path"])
        expected_workspace = self.workspace_root / workspace_id
        if Path(os.path.abspath(workspace_path)) != expected_workspace:
            return quarantined
        try:
            if self._workspace.exists(workspace_id):
                return quarantined
        except Exception:
            return quarantined
        key_digest = record["cache_key_digest"]
        if (
            not key_digest.startswith(_DIGEST_PREFIX)
            or len(key_digest) != len(_DIGEST_PREFIX) + 64
        ):
            return quarantined
        try:
            int(key_digest.removeprefix(_DIGEST_PREFIX), 16)
        except ValueError:
            return quarantined
        cache_key = MaterializationKey("bb.rl.materialization-key.v1", key_digest)
        record_path = self._record_path(cache_key)
        with self._lock:
            try:
                raw = self._read_regular(
                    self._record_relative(cache_key),
                    max_bytes=64 * 1024,
                    failure="stale_identity_uncertain",
                )
                envelope = json.loads(raw)
                payload = envelope["payload"]
                if (
                    envelope["checksum"] != _bytes_digest(canonical_json_bytes(payload))
                    or payload["schema_version"] != "bb.rl.cache-lease.v1"
                ):
                    return quarantined
            except Exception:
                return quarantined
            bindings = {
                "key": record["cache_key_digest"],
                "lease_id": record["cache_lease_id"],
                "holder_id": record["cache_holder_id"],
                "owner_token": record["cache_token_value"],
                "epoch": record["cache_epoch"],
                "cache_manifest_digest": record["cache_manifest_digest"],
                "workspace_id": record["workspace_id"],
                "workspace_path": record["workspace_path"],
                "effective_plan_digest": record["effective_plan_digest"],
            }
            if any(payload.get(name) != value for name, value in bindings.items()):
                return quarantined
            persisted_sources = payload.get("source_digests")
            if (
                type(persisted_sources) is not list
                or tuple(persisted_sources) != source_digests
            ):
                return quarantined
            if payload.get("state") == CacheLeaseState.RELEASED.value:
                return CleanupStepReceipt("cache_holder", CleanupState.ALREADY_RELEASED)
            if payload.get("state") != CacheLeaseState.ACTIVE.value:
                return quarantined
            released = dict(payload)
            released["state"] = CacheLeaseState.RELEASED.value
            try:
                self._write_record(record_path, released)
            except Exception:
                return quarantined
            return CleanupStepReceipt("cache_holder", CleanupState.RELEASED)

    def _snapshot_tree(
        self,
        source: str,
        *,
        source_owner: _DirFd,
        destination: str | None,
        destination_owner: _DirFd | None,
        max_depth: int,
        max_files: int,
        max_inodes: int,
        max_bytes: int,
    ) -> tuple[tuple[SnapshotManifestEntry, ...], int, int, int]:
        if destination is not None and destination_owner is None:
            raise ValueError("destination owner required")
        if min(max_depth, max_files, max_inodes, max_bytes) < 0:
            raise RuntimeError("snapshot_tampered")
        entries: list[SnapshotManifestEntry] = []
        inode_ids: set[tuple[int, int]] = set()
        aliases: set[str] = set()
        file_count = 0
        total_bytes = 0

        def walk(source_fd: int, destination_fd: int | None, prefix: str) -> None:
            nonlocal file_count, total_bytes
            names = tuple(sorted(os.listdir(source_fd)))
            for name in names:
                relative = f"{prefix}/{name}" if prefix else name
                try:
                    _logical_path(relative)
                    before = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
                except (OSError, ValueError) as exc:
                    raise RuntimeError("snapshot_tampered") from exc
                alias = relative.casefold()
                inode = (before.st_dev, before.st_ino)
                depth = len(PurePosixPath(relative).parts) - 1
                if (
                    alias in aliases
                    or inode in inode_ids
                    or depth > max_depth
                    or len(inode_ids) + 1 > max_inodes
                    or not (
                        stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)
                    )
                    or stat.S_ISLNK(before.st_mode)
                    or (stat.S_ISREG(before.st_mode) and before.st_nlink != 1)
                ):
                    raise RuntimeError("snapshot_tampered")
                aliases.add(alias)
                inode_ids.add(inode)
                normalized_mode = stat.S_IMODE(before.st_mode) & 0o555

                if stat.S_ISDIR(before.st_mode):
                    try:
                        child_source_fd = os.open(
                            name, _directory_open_flags(), dir_fd=source_fd
                        )
                    except OSError as exc:
                        raise RuntimeError("snapshot_tampered") from exc
                    child_destination_fd: int | None = None
                    try:
                        opened = os.fstat(child_source_fd)
                        if _metadata_identity(opened) != _metadata_identity(
                            before
                        ) or not stat.S_ISDIR(opened.st_mode):
                            raise RuntimeError("snapshot_race")
                        if destination_fd is not None:
                            os.mkdir(name, mode=0o700, dir_fd=destination_fd)
                            child_destination_fd = os.open(
                                name, _directory_open_flags(), dir_fd=destination_fd
                            )
                        entries.append(
                            SnapshotManifestEntry(
                                relative, "directory", normalized_mode, 0, None
                            )
                        )
                        walk(child_source_fd, child_destination_fd, relative)
                        if _metadata_identity(
                            os.fstat(child_source_fd)
                        ) != _metadata_identity(opened):
                            raise RuntimeError("snapshot_race")
                        if child_destination_fd is not None:
                            os.fsync(child_destination_fd)
                    except OSError as exc:
                        raise RuntimeError("snapshot_tampered") from exc
                    finally:
                        if child_destination_fd is not None:
                            os.close(child_destination_fd)
                        os.close(child_source_fd)
                    continue

                admitted_total = total_bytes + before.st_size
                if (
                    before.st_size < 0
                    or _is_sparse(before)
                    or file_count + 1 > max_files
                    or admitted_total > max_bytes
                ):
                    raise RuntimeError("snapshot_tampered")
                file_count += 1
                total_bytes = admitted_total
                try:
                    source_file_fd = os.open(name, _file_open_flags(), dir_fd=source_fd)
                except OSError as exc:
                    raise RuntimeError("snapshot_tampered") from exc
                destination_file_fd: int | None = None
                try:
                    opened = os.fstat(source_file_fd)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or _is_sparse(opened)
                    ):
                        raise RuntimeError("snapshot_tampered")
                    if _metadata_identity(opened) != _metadata_identity(before):
                        raise RuntimeError("snapshot_race")
                    if destination_fd is not None:
                        destination_file_fd = os.open(
                            name,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | getattr(os, "O_NOFOLLOW", 0),
                            0o600,
                            dir_fd=destination_fd,
                        )
                    digest = hashlib.sha256()
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(
                            source_file_fd, min(remaining, _READ_CHUNK_BYTES)
                        )
                        if not chunk:
                            raise RuntimeError("snapshot_race")
                        digest.update(chunk)
                        if destination_file_fd is not None:
                            _write_all(
                                destination_file_fd,
                                chunk,
                                failure="snapshot_tampered",
                            )
                        remaining -= len(chunk)
                    if os.read(source_file_fd, 1):
                        raise RuntimeError("snapshot_race")
                    if _metadata_identity(
                        os.fstat(source_file_fd)
                    ) != _metadata_identity(opened):
                        raise RuntimeError("snapshot_race")
                    if destination_file_fd is not None:
                        os.fchmod(destination_file_fd, normalized_mode)
                        os.fsync(destination_file_fd)
                    entries.append(
                        SnapshotManifestEntry(
                            relative,
                            "file",
                            normalized_mode,
                            before.st_size,
                            _DIGEST_PREFIX + digest.hexdigest(),
                        )
                    )
                except OSError as exc:
                    raise RuntimeError("snapshot_tampered") from exc
                finally:
                    if destination_file_fd is not None:
                        os.close(destination_file_fd)
                    os.close(source_file_fd)
            if tuple(sorted(os.listdir(source_fd))) != names:
                raise RuntimeError("snapshot_race")

        try:
            source_root_fd = source_owner.open_dir(source)
        except OSError as exc:
            raise RuntimeError("snapshot_tampered") from exc
        destination_root_fd: int | None = None
        try:
            source_root_before = os.fstat(source_root_fd)
            if not stat.S_ISDIR(source_root_before.st_mode):
                raise RuntimeError("snapshot_tampered")
            if destination is not None:
                assert destination_owner is not None
                destination_root_fd = destination_owner.open_dir(destination)
            walk(source_root_fd, destination_root_fd, "")
            if _metadata_identity(os.fstat(source_root_fd)) != _metadata_identity(
                source_root_before
            ):
                raise RuntimeError("snapshot_race")
            if destination_root_fd is not None:
                os.fsync(destination_root_fd)
        finally:
            if destination_root_fd is not None:
                os.close(destination_root_fd)
            os.close(source_root_fd)
        entries.sort(key=lambda item: item.logical_path)
        return tuple(entries), file_count, len(inode_ids), total_bytes

    @contextmanager
    def _snapshot_lock(self, suffix: str) -> Iterator[None]:
        import fcntl

        if len(suffix) != 64 or any(character not in "0123456789abcdef" for character in suffix):
            raise RuntimeError("snapshot_tampered")
        lock_fd = self._cache.open_file(
            "leases/snapshot-" + suffix + ".lock",
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("snapshot_lock_invalid")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


    @contextmanager
    def _snapshot_staging_lock(self, snapshot_id: str) -> Iterator[None]:
        import fcntl

        if (
            not snapshot_id.startswith("snapshot-")
            or len(snapshot_id) != 41
            or any(
                character not in "0123456789abcdef"
                for character in snapshot_id[9:]
            )
        ):
            raise RuntimeError("snapshot_tampered")
        lock_fd = self._cache.open_file(
            "leases/staging-" + snapshot_id + ".lock",
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("snapshot_lock_invalid")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


    def verify_snapshot(
        self,
        receipt: VerifierSnapshotReceipt,
        path: Path,
        *,
        max_depth: int,
        max_files: int,
        max_inodes: int,
        max_bytes: int,
    ) -> None:
        if type(receipt) is not VerifierSnapshotReceipt:
            raise RuntimeError("snapshot_tampered")
        expected_suffix = receipt.root_digest.removeprefix(_DIGEST_PREFIX)
        expected_path = self.cache_root / "snapshot-objects" / expected_suffix
        if (
            not receipt.root_digest.startswith(_DIGEST_PREFIX)
            or receipt.immutable_storage_object_id
            != "snapshot-object-" + expected_suffix
            or Path(os.path.abspath(path)) != expected_path
        ):
            raise RuntimeError("snapshot_tampered")
        entries, file_count, inode_count, byte_count = self._snapshot_tree(
            "snapshot-objects/" + expected_suffix,
            source_owner=self._cache,
            destination=None,
            destination_owner=None,
            max_depth=max_depth,
            max_files=max_files,
            max_inodes=max_inodes,
            max_bytes=max_bytes,
        )
        projections = [item.projection() for item in entries]
        manifest_digest = _digest(projections)
        root_digest = _digest(
            {
                "schema_version": "bb.rl.verifier-snapshot.v1",
                "entries": projections,
            }
        )
        if (
            manifest_digest != receipt.manifest_digest
            or root_digest != receipt.root_digest
            or file_count != receipt.file_count
            or inode_count != receipt.inode_count
            or byte_count != receipt.byte_count
        ):
            raise RuntimeError("snapshot_tampered")

    @staticmethod
    def _snapshot_reference_payload(
        *,
        root_digest: str,
        snapshot_id: str,
        source_lease_id: str,
    ) -> bytes:
        return canonical_json_bytes(
            {
                "root_digest": root_digest,
                "snapshot_id": snapshot_id,
                "source_lease_id": source_lease_id,
            }
        )

    def _read_snapshot_reference(
        self,
        *,
        suffix: str,
        snapshot_id: str,
    ) -> Mapping[str, str]:
        if (
            len(suffix) != 64
            or any(character not in "0123456789abcdef" for character in suffix)
            or not snapshot_id.startswith("snapshot-")
            or len(snapshot_id) != 41
            or any(
                character not in "0123456789abcdef"
                for character in snapshot_id.removeprefix("snapshot-")
            )
        ):
            raise RuntimeError("snapshot_tampered")
        relative = "snapshot-references/" + suffix + "/" + snapshot_id
        marker_fd = self._cache.open_file(relative, os.O_RDONLY)
        try:
            metadata = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > 1024
            ):
                raise RuntimeError("snapshot_tampered")
            observed = os.read(marker_fd, metadata.st_size + 1)
        finally:
            os.close(marker_fd)
        try:
            projection = json.loads(observed)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("snapshot_tampered") from exc
        if (
            type(projection) is not dict
            or set(projection) != {
                "root_digest",
                "snapshot_id",
                "source_lease_id",
            }
            or type(projection.get("root_digest")) is not str
            or type(projection.get("snapshot_id")) is not str
            or type(projection.get("source_lease_id")) is not str
            or projection["root_digest"] != _DIGEST_PREFIX + suffix
            or projection["snapshot_id"] != snapshot_id
            or not projection["source_lease_id"]
            or len(projection["source_lease_id"]) > 256
            or canonical_json_bytes(projection) != observed
        ):
            raise RuntimeError("snapshot_tampered")
        return MappingProxyType(projection)

    @staticmethod
    def _snapshot_staging_payload(
        *,
        snapshot_id: str,
        source_lease_id: str,
    ) -> bytes:
        return canonical_json_bytes(
            {
                "snapshot_id": snapshot_id,
                "source_lease_id": source_lease_id,
            }
        )

    def _install_snapshot_staging_record(
        self,
        *,
        snapshot_id: str,
        source_lease_id: str,
    ) -> bytes:
        payload = self._snapshot_staging_payload(
            snapshot_id=snapshot_id,
            source_lease_id=source_lease_id,
        )
        relative = "snapshot-staging/" + snapshot_id
        with self._lock, self._snapshot_staging_lock(snapshot_id):
            descriptor = self._cache.open_file(
                relative,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                _write_all(descriptor, payload, failure="snapshot_tampered")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._cache.fsync_dir("snapshot-staging")
        return payload

    def _remove_snapshot_staging_record(
        self,
        *,
        snapshot_id: str,
        payload: bytes,
    ) -> None:
        relative = "snapshot-staging/" + snapshot_id
        staging = "staging/" + snapshot_id
        with self._lock, self._snapshot_staging_lock(snapshot_id):
            try:
                descriptor = self._cache.open_file(relative, os.O_RDONLY)
            except FileNotFoundError:
                if self._cache.exists(staging):
                    raise RuntimeError("snapshot_tampered")
                return
            try:
                metadata = os.fstat(descriptor)
                observed = os.read(descriptor, len(payload) + 1)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or observed != payload
                ):
                    raise RuntimeError("snapshot_tampered")
            finally:
                os.close(descriptor)
            self._cache.remove_tree(staging, missing_ok=True)
            if self._cache.exists(staging):
                raise RuntimeError("snapshot_release_failed")
            staging_root_fd = self._cache.open_dir("snapshot-staging")
            try:
                os.unlink(snapshot_id, dir_fd=staging_root_fd)
                os.fsync(staging_root_fd)
            finally:
                os.close(staging_root_fd)

    def _release_snapshot_staging_for_lease(
        self,
        source_lease_id: str,
    ) -> tuple[str, ...]:
        staging_root_fd = self._cache.open_dir("snapshot-staging")
        try:
            snapshot_ids = tuple(sorted(os.listdir(staging_root_fd)))
        finally:
            os.close(staging_root_fd)
        released: list[str] = []
        for snapshot_id in snapshot_ids:
            if (
                not snapshot_id.startswith("snapshot-")
                or len(snapshot_id) != 41
                or any(
                    character not in "0123456789abcdef"
                    for character in snapshot_id[9:]
                )
            ):
                raise RuntimeError("snapshot_tampered")
            relative = "snapshot-staging/" + snapshot_id
            with self._lock, self._snapshot_staging_lock(snapshot_id):
                try:
                    descriptor = self._cache.open_file(relative, os.O_RDONLY)
                except FileNotFoundError:
                    continue
                try:
                    metadata = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or stat.S_IMODE(metadata.st_mode) != 0o600
                        or metadata.st_size > 1024
                    ):
                        raise RuntimeError("snapshot_tampered")
                    observed = os.read(descriptor, metadata.st_size + 1)
                finally:
                    os.close(descriptor)
            try:
                projection = json.loads(observed)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("snapshot_tampered") from exc
            if (
                type(projection) is not dict
                or set(projection) != {"snapshot_id", "source_lease_id"}
                or projection.get("snapshot_id") != snapshot_id
                or type(projection.get("source_lease_id")) is not str
                or not projection["source_lease_id"]
                or len(projection["source_lease_id"]) > 256
                or canonical_json_bytes(projection) != observed
            ):
                raise RuntimeError("snapshot_tampered")
            if projection["source_lease_id"] != source_lease_id:
                continue
            self._remove_snapshot_staging_record(
                snapshot_id=snapshot_id,
                payload=observed,
            )
            released.append(snapshot_id)
        return tuple(released)


    def _release_snapshot_reference(
        self,
        *,
        root_digest: str,
        snapshot_id: str,
        source_lease_id: str,
    ) -> bool:
        suffix = root_digest.removeprefix(_DIGEST_PREFIX)
        with self._lock, self._snapshot_lock(suffix):
            return self._release_snapshot_reference_locked(
                suffix=suffix,
                root_digest=root_digest,
                snapshot_id=snapshot_id,
                source_lease_id=source_lease_id,
            )

    def _release_snapshot_reference_locked(
        self,
        *,
        suffix: str,
        root_digest: str,
        snapshot_id: str,
        source_lease_id: str,
    ) -> bool:
        relative = "snapshot-objects/" + suffix
        reference_directory = "snapshot-references/" + suffix
        reference_relative = reference_directory + "/" + snapshot_id
        marker_payload = self._snapshot_reference_payload(
            root_digest=root_digest,
            snapshot_id=snapshot_id,
            source_lease_id=source_lease_id,
        )
        try:
            marker_fd = self._cache.open_file(reference_relative, os.O_RDONLY)
        except FileNotFoundError:
            return not self._cache.exists(relative)
        try:
            metadata = os.fstat(marker_fd)
            observed = os.read(marker_fd, len(marker_payload) + 1)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or observed != marker_payload
            ):
                raise RuntimeError("snapshot_tampered")
        finally:
            os.close(marker_fd)
        reference_fd = self._cache.open_dir(reference_directory)
        try:
            other_references = tuple(
                name
                for name in os.listdir(reference_fd)
                if name != snapshot_id
            )
            if other_references:
                os.unlink(snapshot_id, dir_fd=reference_fd)
                os.fsync(reference_fd)
                return True
            self._cache.remove_tree(relative, missing_ok=True)
            if self._cache.exists(relative):
                return False
            os.unlink(snapshot_id, dir_fd=reference_fd)
            os.fsync(reference_fd)
        finally:
            os.close(reference_fd)
        self._cache.remove_tree(reference_directory)
        self._cache.fsync_dir("snapshot-references")
        return True

    def _reject_unreferenced_snapshot_object(self, suffix: str) -> None:
        reference_directory = "snapshot-references/" + suffix
        immutable_relative = "snapshot-objects/" + suffix
        with self._lock, self._snapshot_lock(suffix):
            try:
                reference_fd = self._cache.open_dir(reference_directory)
            except FileNotFoundError:
                if self._cache.exists(immutable_relative):
                    raise RuntimeError("snapshot_tampered")
                return
            try:
                references = tuple(os.listdir(reference_fd))
            finally:
                os.close(reference_fd)
            if references:
                return
            if self._cache.exists(immutable_relative):
                raise RuntimeError("snapshot_tampered")
            self._cache.remove_tree(reference_directory)
            self._cache.fsync_dir("snapshot-references")

    def release_snapshot(
        self,
        receipt: VerifierSnapshotReceipt,
        path: Path,
    ) -> bool:
        if type(receipt) is not VerifierSnapshotReceipt:
            raise RuntimeError("snapshot_tampered")
        expected_suffix = receipt.root_digest.removeprefix(_DIGEST_PREFIX)
        expected_path = self.cache_root / "snapshot-objects" / expected_suffix
        if (
            not receipt.root_digest.startswith(_DIGEST_PREFIX)
            or receipt.immutable_storage_object_id
            != "snapshot-object-" + expected_suffix
            or Path(os.path.abspath(path)) != expected_path
        ):
            raise RuntimeError("snapshot_tampered")
        return self._release_snapshot_reference(
            root_digest=receipt.root_digest,
            snapshot_id=receipt.snapshot_id,
            source_lease_id=receipt.source_lease_id,
        )

    def release_snapshots_for_lease(self, source_lease_id: str) -> tuple[str, ...]:
        if (
            type(source_lease_id) is not str
            or not source_lease_id
            or len(source_lease_id) > 256
        ):
            raise RuntimeError("snapshot_tampered")
        released = set(
            self._release_snapshot_staging_for_lease(source_lease_id)
        )
        reference_root_fd = self._cache.open_dir("snapshot-references")
        try:
            suffixes = tuple(sorted(os.listdir(reference_root_fd)))
        finally:
            os.close(reference_root_fd)
        for suffix in suffixes:
            if (
                len(suffix) != 64
                or any(
                    character not in "0123456789abcdef" for character in suffix
                )
            ):
                raise RuntimeError("snapshot_tampered")
            with self._lock, self._snapshot_lock(suffix):
                try:
                    reference_fd = self._cache.open_dir(
                        "snapshot-references/" + suffix
                    )
                except FileNotFoundError:
                    snapshot_ids = ()
                else:
                    try:
                        snapshot_ids = tuple(sorted(os.listdir(reference_fd)))
                    finally:
                        os.close(reference_fd)
                for snapshot_id in snapshot_ids:
                    try:
                        marker = self._read_snapshot_reference(
                            suffix=suffix,
                            snapshot_id=snapshot_id,
                        )
                    except FileNotFoundError:
                        continue
                    if marker["source_lease_id"] != source_lease_id:
                        continue
                    if not self._release_snapshot_reference_locked(
                        suffix=suffix,
                        root_digest=marker["root_digest"],
                        snapshot_id=snapshot_id,
                        source_lease_id=source_lease_id,
                    ):
                        raise RuntimeError("snapshot_release_failed")
                    released.add(snapshot_id)
            self._reject_unreferenced_snapshot_object(suffix)
        return tuple(sorted(released))

    def copy_snapshot(
        self,
        receipt: VerifierSnapshotReceipt,
        path: Path,
        destination: Path,
        *,
        max_depth: int,
        max_files: int,
        max_inodes: int,
        max_bytes: int,
    ) -> None:
        if type(receipt) is not VerifierSnapshotReceipt:
            raise RuntimeError("snapshot_tampered")
        expected_suffix = receipt.root_digest.removeprefix(_DIGEST_PREFIX)
        expected_path = self.cache_root / "snapshot-objects" / expected_suffix
        if (
            not receipt.root_digest.startswith(_DIGEST_PREFIX)
            or receipt.immutable_storage_object_id
            != "snapshot-object-" + expected_suffix
            or Path(os.path.abspath(path)) != expected_path
        ):
            raise RuntimeError("snapshot_tampered")
        try:
            destination_name = destination.relative_to(self.workspace_root).as_posix()
            _logical_path(destination_name)
        except (ValueError, OSError) as exc:
            raise RuntimeError("snapshot_destination_invalid") from exc
        if len(PurePosixPath(destination_name).parts) < 2:
            raise RuntimeError("snapshot_destination_invalid")
        destination_owner = self._workspace
        with self._lock:
            destination_owner.mkdir(destination_name)
            try:
                entries, file_count, inode_count, byte_count = self._snapshot_tree(
                    "snapshot-objects/" + expected_suffix,
                    source_owner=self._cache,
                    destination=destination_name,
                    destination_owner=destination_owner,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_inodes=max_inodes,
                    max_bytes=max_bytes,
                )
                projections = [item.projection() for item in entries]
                manifest_digest = _digest(projections)
                root_digest = _digest(
                    {
                        "schema_version": "bb.rl.verifier-snapshot.v1",
                        "entries": projections,
                    }
                )
                if (
                    manifest_digest != receipt.manifest_digest
                    or root_digest != receipt.root_digest
                    or file_count != receipt.file_count
                    or inode_count != receipt.inode_count
                    or byte_count != receipt.byte_count
                ):
                    raise RuntimeError("snapshot_tampered")
                destination_fd = destination_owner.open_dir(destination_name)
                try:
                    for item in sorted(
                        entries,
                        key=lambda entry: len(
                            PurePosixPath(entry.logical_path).parts
                        ),
                        reverse=True,
                    ):
                        if item.kind == "directory":
                            os.chmod(
                                item.logical_path,
                                item.mode,
                                dir_fd=destination_fd,
                                follow_symlinks=False,
                            )
                    os.fsync(destination_fd)
                finally:
                    os.close(destination_fd)
            except BaseException:
                destination_owner.remove_tree(destination_name, missing_ok=True)
                raise

    def seal_snapshot(
        self,
        workspace: MaterializedWorkspace,
        *,
        source_lease_id: str,
        effective_plan_digest: str,
        task_digest: str,
        verifier_digest: str,
        max_depth: int,
        max_files: int,
        max_inodes: int,
        max_bytes: int,
    ) -> tuple[VerifierSnapshotReceipt, Path]:
        snapshot_id = "snapshot-" + self._nonce()
        staging = "staging/" + snapshot_id
        staging_record = self._install_snapshot_staging_record(
            snapshot_id=snapshot_id,
            source_lease_id=source_lease_id,
        )
        try:
            self._cache.mkdir(staging)
            entries, file_count, inode_count, byte_count = self._snapshot_tree(
                workspace.receipt.workspace_id,
                source_owner=self._workspace,
                destination=staging,
                destination_owner=self._cache,
                max_depth=max_depth,
                max_files=max_files,
                max_inodes=max_inodes,
                max_bytes=max_bytes,
            )
            staging_fd = self._cache.open_dir(staging)
            try:
                for item in sorted(
                    entries,
                    key=lambda entry: len(PurePosixPath(entry.logical_path).parts),
                    reverse=True,
                ):
                    if item.kind == "directory":
                        os.chmod(
                            item.logical_path,
                            item.mode,
                            dir_fd=staging_fd,
                            follow_symlinks=False,
                        )
                os.fsync(staging_fd)
            finally:
                os.close(staging_fd)
            projections = [item.projection() for item in entries]
            manifest_digest = _digest(projections)
            root_digest = _digest(
                {
                    "schema_version": "bb.rl.verifier-snapshot.v1",
                    "entries": projections,
                }
            )
            suffix = root_digest.removeprefix(_DIGEST_PREFIX)
            immutable_relative = "snapshot-objects/" + suffix
            immutable = self.cache_root / "snapshot-objects" / suffix
            receipt = VerifierSnapshotReceipt(
                snapshot_id,
                workspace.receipt.workspace_id,
                source_lease_id,
                effective_plan_digest,
                task_digest,
                verifier_digest,
                manifest_digest,
                root_digest,
                file_count,
                inode_count,
                byte_count,
                "snapshot-object-" + suffix,
            )
            marker_payload = self._snapshot_reference_payload(
                root_digest=receipt.root_digest,
                snapshot_id=receipt.snapshot_id,
                source_lease_id=receipt.source_lease_id,
            )
            reference_directory = "snapshot-references/" + suffix
            reference_relative = reference_directory + "/" + snapshot_id
            with self._lock, self._snapshot_lock(suffix):
                reference_created = False
                marker_created = False
                object_created = False
                try:
                    try:
                        self._cache.mkdir(reference_directory)
                        reference_created = True
                        self._cache.fsync_dir("snapshot-references")
                    except FileExistsError:
                        reference_fd = self._cache.open_dir(reference_directory)
                        os.close(reference_fd)
                    marker_fd = self._cache.open_file(
                        reference_relative,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    marker_created = True
                    try:
                        _write_all(
                            marker_fd,
                            marker_payload,
                            failure="snapshot_tampered",
                        )
                        os.fsync(marker_fd)
                    finally:
                        os.close(marker_fd)
                    self._cache.fsync_dir(reference_directory)
                    if self._cache.exists(immutable_relative):
                        self.verify_snapshot(
                            receipt,
                            immutable,
                            max_depth=max_depth,
                            max_files=max_files,
                            max_inodes=max_inodes,
                            max_bytes=max_bytes,
                        )
                        self._cache.remove_tree(staging)
                    else:
                        self._cache.replace(staging, immutable_relative)
                        object_created = True
                        self.verify_snapshot(
                            receipt,
                            immutable,
                            max_depth=max_depth,
                            max_files=max_files,
                            max_inodes=max_inodes,
                            max_bytes=max_bytes,
                        )
                except BaseException:
                    if object_created:
                        self._cache.remove_tree(
                            immutable_relative,
                            missing_ok=True,
                        )
                    if marker_created:
                        reference_fd = self._cache.open_dir(reference_directory)
                        try:
                            os.unlink(snapshot_id, dir_fd=reference_fd)
                            os.fsync(reference_fd)
                        finally:
                            os.close(reference_fd)
                    if reference_created:
                        self._cache.remove_tree(reference_directory)
                        self._cache.fsync_dir("snapshot-references")
                    raise
            return receipt, immutable
        finally:
            self._cache.remove_tree(staging, missing_ok=True)
            self._remove_snapshot_staging_record(
                snapshot_id=snapshot_id,
                payload=staging_record,
            )


__all__ = [
    "CacheLeaseReceipt",
    "CacheLeaseState",
    "CacheLeaseToken",
    "CleanupState",
    "CleanupStepReceipt",
    "DirectoryStorageBackend",
    "FilesystemMaterializationStore",
    "IsolationDisposition",
    "MaterializationClock",
    "MaterializationEntry",
    "MaterializationKey",
    "MaterializationSourceReader",
    "MaterializedMount",
    "MaterializedWorkspace",
    "SandboxCleanupReceipt",
    "SealedSourceManifest",
    "SnapshotManifestEntry",
    "SnapshotState",
    "SourceManifestEntry",
    "VerifierSnapshotReceipt",
    "WorkspaceLeaseState",
    "WorkspaceMaterializationPlan",
    "WorkspaceMaterializationReceipt",
    "WorkspaceOpenRequest",
    "WorkspaceStorageBackend",
]
