from __future__ import annotations

import errno
import os
import platform
import secrets
import stat
import struct
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .materialization import DirectoryStorageBackend


# Linux ABI constants.  They are kept as literals so importing this module does
# not import Linux-only modules on Darwin.
_FS_IOC_FSGETXATTR = 0x801C581F
_FS_IOC_FSSETXATTR = 0x401C5820
_FSXATTR = struct.Struct("=5I8s")
_PROJINHERIT = 0x200
_Q_GETQUOTA = 0x800007
_Q_SETQUOTA = 0x800008
_PRJQUOTA = 2
_QIF_BLIMITS = 1
_QUOTACTL_SYSCALL_X86_64 = 443
_DQBLK = struct.Struct("=8QI4x")


@dataclass(frozen=True, slots=True)
class _LinuxSupport:
    ctypes: Any
    fcntl: Any
    syscall: Any


@dataclass(frozen=True, slots=True)
class _QuotaState:
    bhardlimit: int
    bsoftlimit: int
    curspace: int
    ihardlimit: int
    isoftlimit: int
    curinodes: int
    btime: int
    itime: int
    valid: int


@dataclass(slots=True)
class _WorkspaceQuota:
    workspace_id: str
    project_id: int
    max_bytes: int
    hard_blocks: int
    root_device: int
    root_inode: int
    quota_reserved: bool = False
    workspace_created: bool = False
    workspace_device: int | None = None
    workspace_inode: int | None = None
    project_set: bool = False


class ProjectQuotaStorageBackend(DirectoryStorageBackend):
    """Descriptor-rooted Linux project-quota workspaces.

    The only filesystem authority is the descriptor inherited from
    ``DirectoryStorageBackend``.  In particular, the ``root`` argument to
    ``allocate`` is only used to construct the returned display path.
    """

    def __init__(self) -> None:
        super().__init__()
        self._linux_support: _LinuxSupport | None = None
        self._root_identity: tuple[int, int] | None = None
        self._allocations: dict[str, _WorkspaceQuota] = {}
        self._quota_lock = threading.RLock()

    @staticmethod
    def _load_linux_support() -> _LinuxSupport:
        if os.name != "posix" or not sys_platform_linux():
            raise OSError(
                errno.ENOTSUP,
                "project quota storage requires Linux x86_64",
            )
        if platform.machine().lower() not in {"x86_64", "amd64"}:
            raise OSError(
                errno.ENOTSUP,
                "project quota storage requires the Linux x86_64 quotactl ABI",
            )
        import ctypes
        import fcntl

        libc = ctypes.CDLL(None, use_errno=True)
        syscall = getattr(libc, "syscall", None)
        if syscall is None:
            raise OSError(
                errno.ENOTSUP,
                "Linux libc does not expose the quotactl syscall entry point",
            )
        # quotactl_fd is not exported by the supported libc.  syscall(443,
        # fd, command, project-id, dqblk*) is the x86_64 kernel ABI.
        syscall.argtypes = [
            ctypes.c_long,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
        syscall.restype = ctypes.c_long
        return _LinuxSupport(ctypes, fcntl, syscall)

    def _support(self) -> _LinuxSupport:
        support = self._linux_support
        if support is None:
            support = self._load_linux_support()
            self._linux_support = support
        return support

    def _owner_or_fail(self) -> Any:
        owner = getattr(self, "_owner", None)
        if owner is None or owner.fd < 0:
            raise RuntimeError("project quota storage root is not bound")
        identity = self._root_identity
        if identity is None:
            raise RuntimeError("project quota storage root identity is not pinned")
        try:
            metadata = os.fstat(owner.fd)
        except OSError as exc:
            raise RuntimeError("project quota storage root descriptor is invalid") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != identity
        ):
            raise RuntimeError("project quota storage root identity drift")
        return owner

    @contextmanager
    def _root_lock(self) -> Iterator[Any]:
        owner = self._owner_or_fail()
        support = self._support()
        try:
            support.fcntl.flock(owner.fd, support.fcntl.LOCK_EX)
        except OSError as exc:
            raise RuntimeError("project quota root lock is unavailable") from exc
        try:
            yield owner
        finally:
            support.fcntl.flock(owner.fd, support.fcntl.LOCK_UN)

    def bind_root(self, descriptor: int) -> None:
        with self._quota_lock:
            if self._allocations:
                raise RuntimeError("cannot rebind project quota root with live workspaces")
            self._support()
            try:
                super().bind_root(descriptor)
            except BaseException:
                self._root_identity = None
                raise
            owner = getattr(self, "_owner", None)
            if owner is None:
                raise RuntimeError("project quota root binding failed")
            try:
                metadata = os.fstat(owner.fd)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError("project quota storage root must be a directory")
                self._root_identity = (metadata.st_dev, metadata.st_ino)
            except BaseException:
                super().close_root()
                self._root_identity = None
                raise

    def close_root(self) -> None:
        with self._quota_lock:
            if self._allocations:
                raise RuntimeError("cannot close project quota root with live workspaces")
            if getattr(self, "_owner", None) is None:
                return
            self._owner_or_fail()
            super().close_root()
            self._root_identity = None

    @staticmethod
    def _validate_workspace_id(workspace_id: str) -> None:
        if (
            type(workspace_id) is not str
            or not workspace_id
            or workspace_id in {".", ".."}
            or "/" in workspace_id
            or "\\" in workspace_id
            or "\x00" in workspace_id
        ):
            raise ValueError("workspace_id_invalid")

    @staticmethod
    def _validate_bound(max_bytes: int) -> int:
        if type(max_bytes) is not int or max_bytes <= 0 or max_bytes % 1024:
            raise ValueError("workspace quota must be a positive 1024-byte-aligned count")
        hard_blocks = max_bytes // 1024
        if hard_blocks > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("workspace quota exceeds the Linux quota ABI")
        return hard_blocks

    @staticmethod
    def _unused_quota(state: _QuotaState) -> bool:
        return not any(
            (
                state.bhardlimit,
                state.bsoftlimit,
                state.curspace,
                state.ihardlimit,
                state.isoftlimit,
                state.curinodes,
                state.btime,
                state.itime,
            )
        )

    def _quota_syscall(
        self, operation: int, project_id: int, payload: bytes | None
    ) -> bytes:
        owner = self._owner_or_fail()
        support = self._support()
        if payload is None:
            buffer = (support.ctypes.c_ubyte * _DQBLK.size)()
        else:
            if len(payload) != _DQBLK.size:
                raise ValueError("quota payload has the wrong size")
            buffer = (support.ctypes.c_ubyte * _DQBLK.size).from_buffer_copy(payload)
        command = (operation << 8) | _PRJQUOTA
        result = support.syscall(
            _QUOTACTL_SYSCALL_X86_64,
            owner.fd,
            command,
            project_id,
            support.ctypes.byref(buffer),
        )
        if int(result) == -1:
            code = support.ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        return bytes(buffer)

    def _quota_get(self, project_id: int) -> _QuotaState:
        try:
            raw = self._quota_syscall(_Q_GETQUOTA, project_id, None)
        except OSError as exc:
            # An uninstantiated quota record is an unused project ID.  Other
            # errors (including an unconfigured prjquota mount) fail closed.
            if exc.errno in {errno.ENOENT, errno.ESRCH}:
                return _QuotaState(0, 0, 0, 0, 0, 0, 0, 0, 0)
            raise
        return _QuotaState(*_DQBLK.unpack(raw))

    def _quota_set(self, project_id: int, *, hard_blocks: int) -> None:
        values = (hard_blocks, 0, 0, 0, 0, 0, 0, 0, _QIF_BLIMITS)
        self._quota_syscall(_Q_SETQUOTA, project_id, _DQBLK.pack(*values))

    def _find_unused_project(self) -> int:
        for _ in range(256):
            project_id = secrets.randbits(32)
            if project_id == 0:
                continue
            if self._unused_quota(self._quota_get(project_id)):
                return project_id
        raise RuntimeError("could not reserve an unused project quota ID")

    def _read_fsxattr(self, descriptor: int) -> tuple[int, int, int, int, int, bytes]:
        support = self._support()
        buffer = bytearray(_FSXATTR.size)
        support.fcntl.ioctl(descriptor, _FS_IOC_FSGETXATTR, buffer, True)
        return _FSXATTR.unpack(bytes(buffer))

    def _set_project_identity(self, descriptor: int, project_id: int) -> None:
        support = self._support()
        fsx = list(self._read_fsxattr(descriptor))
        fsx[0] |= _PROJINHERIT
        fsx[3] = project_id
        support.fcntl.ioctl(
            descriptor,
            _FS_IOC_FSSETXATTR,
            bytearray(_FSXATTR.pack(*fsx)),
            True,
        )
        after = self._read_fsxattr(descriptor)
        if after[3] != project_id or not (after[0] & _PROJINHERIT):
            raise RuntimeError("project quota directory identity was not installed")

    def _verify_quota(self, allocation: _WorkspaceQuota, state: _QuotaState) -> None:
        if (
            state.bhardlimit != allocation.hard_blocks
            or state.bsoftlimit != 0
            or state.ihardlimit != 0
            or state.isoftlimit != 0
            or state.btime != 0
            or state.itime != 0
            or not (state.valid & _QIF_BLIMITS)
            or state.curspace > allocation.max_bytes
        ):
            raise RuntimeError("project quota identity drift")

    def _verify_workspace(
        self, allocation: _WorkspaceQuota, descriptor: int, *, verify_quota: bool = True
    ) -> os.stat_result:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or allocation.workspace_device != metadata.st_dev
            or allocation.workspace_inode != metadata.st_ino
        ):
            raise RuntimeError("project quota workspace identity drift")
        if verify_quota:
            fsx = self._read_fsxattr(descriptor)
            if fsx[3] != allocation.project_id or not (fsx[0] & _PROJINHERIT):
                raise RuntimeError("project quota project identity drift")
            self._verify_quota(allocation, self._quota_get(allocation.project_id))
        return metadata

    def _clear_quota(self, allocation: _WorkspaceQuota) -> None:
        state = self._quota_get(allocation.project_id)
        if self._unused_quota(state):
            if allocation.quota_reserved:
                raise RuntimeError("project quota reservation disappeared")
            return
        self._verify_quota(allocation, state)
        if state.curspace != 0 or state.curinodes != 0:
            raise RuntimeError("project quota is still in use")
        self._quota_set(allocation.project_id, hard_blocks=0)
        if not self._unused_quota(self._quota_get(allocation.project_id)):
            raise RuntimeError("project quota reservation remained after release")

    def _release_locked(self, allocation: _WorkspaceQuota, *, strict: bool) -> None:
        owner = self._owner_or_fail()
        root_metadata = os.fstat(owner.fd)
        if (root_metadata.st_dev, root_metadata.st_ino) != (
            allocation.root_device,
            allocation.root_inode,
        ):
            raise RuntimeError("project quota root identity drift")
        if allocation.workspace_created:
            if allocation.workspace_device is None or allocation.workspace_inode is None:
                if strict:
                    raise RuntimeError("project quota workspace identity is incomplete")
                return
            try:
                descriptor = owner.open_dir(allocation.workspace_id)
            except FileNotFoundError:
                if strict and allocation.quota_reserved and self._unused_quota(
                    self._quota_get(allocation.project_id)
                ):
                    raise RuntimeError("project quota reservation disappeared")
                allocation.workspace_created = False
            else:
                try:
                    self._verify_workspace(
                        allocation,
                        descriptor,
                        verify_quota=allocation.project_set,
                    )
                finally:
                    os.close(descriptor)
                super().release(Path(allocation.workspace_id))
                allocation.workspace_created = False
        self._clear_quota(allocation)
        self._allocations.pop(allocation.workspace_id, None)

    def _best_effort_partial_cleanup(self, allocation: _WorkspaceQuota) -> None:
        try:
            self._release_locked(allocation, strict=False)
        except BaseException:
            # Keeping the allocation record is intentional: close_root and a
            # later release still retain ownership of any live quota.
            return

    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path:
        self._validate_workspace_id(workspace_id)
        hard_blocks = self._validate_bound(max_bytes)
        with self._quota_lock:
            with self._root_lock() as owner:
                if workspace_id in self._allocations:
                    raise RuntimeError("workspace is already owned by project quota backend")
                project_id = self._find_unused_project()
                root_metadata = os.fstat(owner.fd)
                allocation = _WorkspaceQuota(
                    workspace_id,
                    project_id,
                    max_bytes,
                    hard_blocks,
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                )
                self._allocations[workspace_id] = allocation
                try:
                    self._quota_set(project_id, hard_blocks=hard_blocks)
                    allocation.quota_reserved = True
                    state = self._quota_get(project_id)
                    self._verify_quota(allocation, state)
                    backing = super().allocate(
                        workspace_id=workspace_id,
                        root=root,
                        max_bytes=max_bytes,
                    )
                    allocation.workspace_created = True
                    descriptor = owner.open_dir(workspace_id)
                    try:
                        metadata = os.fstat(descriptor)
                        allocation.workspace_device = metadata.st_dev
                        allocation.workspace_inode = metadata.st_ino
                        self._set_project_identity(descriptor, project_id)
                        allocation.project_set = True
                    finally:
                        os.close(descriptor)
                    return backing
                except BaseException:
                    self._best_effort_partial_cleanup(allocation)
                    raise

    def measure(self, backing: Path) -> Mapping[str, Any]:
        workspace_id = self._workspace_id(backing)
        with self._quota_lock:
            with self._root_lock() as owner:
                allocation = self._allocations.get(workspace_id)
                if allocation is None:
                    raise RuntimeError("project quota workspace is not owned")
                descriptor = owner.open_dir(workspace_id)
                try:
                    metadata = self._verify_workspace(allocation, descriptor)
                    fsx = self._read_fsxattr(descriptor)
                    quota = self._quota_get(allocation.project_id)
                finally:
                    os.close(descriptor)
                return {
                    "authority_id": (
                        f"linux-project-quota:{metadata.st_dev}:"
                        f"{metadata.st_ino}:{fsx[3]}"
                    ),
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                    "project_id": fsx[3],
                    "owner_uid": metadata.st_uid,
                    "owner_gid": metadata.st_gid,
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "quota_enforced": True,
                    "quota_bytes": quota.bhardlimit * 1024,
                    "quota_hard_blocks": quota.bhardlimit,
                    "quota_usage_bytes": quota.curspace,
                    "quota_usage_inodes": quota.curinodes,
                }

    def release(self, backing: Path) -> None:
        workspace_id = self._workspace_id(backing)
        with self._quota_lock:
            with self._root_lock():
                allocation = self._allocations.get(workspace_id)
                if allocation is None:
                    raise RuntimeError("project quota workspace is not owned")
                self._release_locked(allocation, strict=True)

    def verify_absent(self, backing: Path) -> bool:
        workspace_id = self._workspace_id(backing)
        with self._quota_lock:
            with self._root_lock() as owner:
                allocation = self._allocations.get(workspace_id)
                if allocation is None:
                    return not owner.exists(workspace_id)
                if allocation.workspace_created:
                    try:
                        descriptor = owner.open_dir(workspace_id)
                    except FileNotFoundError:
                        allocation.workspace_created = False
                        if allocation.quota_reserved and self._unused_quota(
                            self._quota_get(allocation.project_id)
                        ):
                            raise RuntimeError("project quota reservation disappeared")
                    else:
                        try:
                            self._verify_workspace(allocation, descriptor)
                        finally:
                            os.close(descriptor)
                        return False
                state = self._quota_get(allocation.project_id)
                if self._unused_quota(state):
                    if allocation.quota_reserved:
                        raise RuntimeError("project quota reservation disappeared")
                    self._allocations.pop(workspace_id, None)
                    return True
                self._verify_quota(allocation, state)
                return False


def sys_platform_linux() -> bool:
    # Kept as a function to make the platform check explicit and avoid any
    # Linux-only import at module import time.
    import sys

    return sys.platform == "linux"


__all__ = ["ProjectQuotaStorageBackend"]
