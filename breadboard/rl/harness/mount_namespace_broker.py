from __future__ import annotations

import array
import asyncio
import base64
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import secrets
import selectors
import signal
import socket
import stat
import subprocess
import threading
import time
from builtins import BaseExceptionGroup
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .sandbox_docker import (
    DockerAdapterError,
    DockerCommandResult,
    ExecutableInvocation,
    PrivateDockerDaemonBinding,
    StagedDockerDescriptorMount,
)

_MAX_MESSAGE = 4 * 1024 * 1024
_MAX_FDS = 4
_MAX_ADMITTED_OUTPUT = 16 * 1024 * 1024
_MAX_OUTPUT = 4 * ((_MAX_ADMITTED_OUTPUT + 1 + 2) // 3) + 128
_CLONE_NEWNS = 0x00020000
_MS_BIND = 4096
_MS_REMOUNT = 32
_MS_RDONLY = 1
_MS_NOSUID = 2
_MS_NODEV = 4
_MS_NOEXEC = 8
_MS_NOATIME = 1024
_MS_NODIRATIME = 2048
_MS_RELATIME = 1 << 21
_MS_PRIVATE = 1 << 18
_MS_REC = 16384
_MNT_DETACH = 2
_RUNTIME_AUTHORITY_LIMIT = 64 * 1024 * 1024
_RUNTIME_TMPFS_OVERHEAD = 1024 * 1024
_ERROR_PROJECTION_MAX_DEPTH = 8
_ERROR_PROJECTION_MAX_LEAVES = 32
_ERROR_PROJECTION_MAX_BYTES = 32 * 1024
_ERROR_DETAIL_MAX_DEPTH = 4
_ERROR_DETAIL_MAX_ITEMS = 16
_ERROR_DETAIL_STRING_LIMIT = 256
_ERROR_MESSAGE_LIMIT = 512
_ERROR_DETAIL_REDACTED_KEYS = (
    "secret",
    "token",
    "password",
    "credential",
    "private",
    "stdout",
    "stderr",
    "bytes",
)


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
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


class MountNamespaceBrokerError(DockerAdapterError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerObservation:
    pid: int
    starttime: str
    mount_namespace: str
    mountinfo_digest: str
    stage_root: str
    stage_root_device: int
    stage_root_inode: int
    executable_digest: str
    executable_device: int
    executable_inode: int
    executable_ctime_ns: int
    executable_size: int
    runtime_path: str | None = None
    runtime_device: int | None = None
    runtime_inode: int | None = None
    runtime_mount_id: int | None = None
    runtime_readonly: bool | None = None
    runtime_tmpfs_size: int | None = None
    runtime_mount_source: str | None = None
    runtime_mount_options: str | None = None
    runtime_super_options: str | None = None
    runtime_source_digest: str | None = None
    runtime_source_device: int | None = None
    runtime_source_inode: int | None = None
    runtime_source_ctime_ns: int | None = None
    runtime_source_size: int | None = None
    runtime_source_mode: int | None = None


@dataclass(slots=True)
class _Stage:
    receipt: StagedDockerDescriptorMount
    fd: int
    mount_fd: int
    mount_id: int
    readonly: bool
    directory: bool
    initial_identity: tuple[int, ...]


def _canonical(document: Mapping[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker message is not canonical"
        ) from exc
    if len(payload) > _MAX_MESSAGE:
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker message exceeds the fixed bound"
        )
    return payload


def _decode(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > _MAX_MESSAGE:
        raise ValueError("invalid broker message size")
    value = json.loads(payload.decode("ascii"))
    if type(value) is not dict or _canonical(value) != payload:
        raise ValueError("broker message is not canonical")
    return value


def _send(
    sock: socket.socket, document: Mapping[str, Any], fds: Sequence[int] = ()
) -> None:
    if len(fds) > _MAX_FDS:
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "too many broker descriptors"
        )
    ancillary = []
    if fds:
        packed = array.array("i", fds)
        ancillary.append((socket.SOL_SOCKET, socket.SCM_RIGHTS, packed))
    sent = sock.sendmsg([_canonical(document)], ancillary)
    if sent != len(_canonical(document)):
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker message was truncated"
        )


def _receive(sock: socket.socket) -> tuple[dict[str, Any], tuple[int, ...]]:
    payload, ancillary, flags, _ = sock.recvmsg(
        _MAX_MESSAGE + 1, socket.CMSG_SPACE(_MAX_FDS * array.array("i").itemsize)
    )
    received: list[int] = []
    try:
        for level, kind, data in ancillary:
            if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                raise ValueError("unexpected broker control message")
            values = array.array("i")
            usable = len(data) - (len(data) % values.itemsize)
            values.frombytes(data[:usable])
            received.extend(values)
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise ValueError("truncated broker message")
        if len(received) > _MAX_FDS:
            raise ValueError("too many broker descriptors")
        return _decode(payload), tuple(received)
    except BaseException:
        for fd in received:
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _accept_startup_snapshot_fd(fds: Sequence[int], *, required: bool) -> int | None:
    expected = 1 if required else 0
    if len(fds) != expected:
        for fd in fds:
            os.close(fd)
        raise MountNamespaceBrokerError(
            "runtime_unsupported",
            "broker startup descriptor count is invalid",
        )
    return fds[0] if required else None


def _proc_starttime(pid: int) -> str:
    payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    fields = payload[payload.rindex(")") + 2 :].split()
    if len(fields) < 20 or not fields[19].isdecimal():
        raise OSError("incomplete proc stat")
    return fields[19]


def _digest_fd_exact(fd: int) -> str:
    hasher = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(fd, 1024 * 1024, offset)
        if not chunk:
            break
        hasher.update(chunk)
        offset += len(chunk)
    return "sha256:" + hasher.hexdigest()


def _raise_after_startup_cleanup(primary: BaseException, cleanup: Any) -> None:
    try:
        cleanup()
    except BaseException as cleanup_error:
        raise BaseExceptionGroup(
            "broker startup and residue cleanup failed",
            [primary, cleanup_error],
        ) from None
    raise primary.with_traceback(primary.__traceback__)


def _mountinfo() -> bytes:
    return Path("/proc/self/mountinfo").read_bytes()


def _mount_observation(path: str) -> tuple[int, bool]:
    encoded_path = os.fsencode(path).replace(b" ", b"\\040")
    for line in _mountinfo().splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[4] == encoded_path:
            options = fields[5].split(b",")
            if (b"ro" in options) == (b"rw" in options):
                raise OSError("mount access mode is ambiguous")
            return int(fields[0]), b"ro" in options
    raise OSError("exact mount entry not found")


def _mount_id(path: str) -> int:
    return _mount_observation(path)[0]


def _libc_call(name: str, *args: Any) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, name)
    if function(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _enter_private_mount_namespace() -> None:
    _libc_call("unshare", ctypes.c_int(_CLONE_NEWNS))
    _libc_call(
        "mount",
        ctypes.c_char_p(None),
        ctypes.c_char_p(b"/"),
        ctypes.c_char_p(None),
        ctypes.c_ulong(_MS_REC | _MS_PRIVATE),
        ctypes.c_char_p(None),
    )


def _remount_readonly(target: str) -> None:
    _mount_id_value, _readonly = _mount_observation(target)
    encoded_path = os.fsencode(target).replace(b" ", b"\\040")
    options: set[bytes] = set()
    for line in _mountinfo().splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[4] == encoded_path:
            options = set(fields[5].split(b","))
            break
    preserved = 0
    for option, flag in (
        (b"nosuid", _MS_NOSUID),
        (b"nodev", _MS_NODEV),
        (b"noatime", _MS_NOATIME),
        (b"nodiratime", _MS_NODIRATIME),
        (b"relatime", _MS_RELATIME),
    ):
        if option in options:
            preserved |= flag
    # Deliberately do not preserve noexec: the admitted runc bind must execute.
    _libc_call(
        "mount",
        ctypes.c_char_p(None),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_char_p(None),
        ctypes.c_ulong(_MS_BIND | _MS_REMOUNT | _MS_RDONLY | preserved),
        ctypes.c_char_p(None),
    )


def _bind(source_fd: int, target: str, *, readonly: bool) -> None:
    source = f"/proc/self/fd/{source_fd}".encode()
    target_bytes = os.fsencode(target)
    _libc_call(
        "mount",
        ctypes.c_char_p(source),
        ctypes.c_char_p(target_bytes),
        ctypes.c_char_p(None),
        ctypes.c_ulong(_MS_BIND),
        ctypes.c_char_p(None),
    )
    if readonly:
        try:
            _remount_readonly(target)
        except BaseException:
            _libc_call(
                "umount2", ctypes.c_char_p(target_bytes), ctypes.c_int(_MNT_DETACH)
            )
            raise


def _unmount(target: str) -> None:
    _libc_call("umount2", ctypes.c_char_p(os.fsencode(target)), ctypes.c_int(0))


def _tmpfs_size_bytes(option: bytes) -> int:
    if not option.startswith(b"size="):
        raise OSError("private runtime tmpfs size option is absent")
    raw = option[5:]
    factor = 1
    if raw[-1:] in (b"k", b"m", b"g"):
        factor = {b"k": 1024, b"m": 1024**2, b"g": 1024**3}[raw[-1:]]
        raw = raw[:-1]
    if not raw or any(byte < 48 or byte > 57 for byte in raw):
        raise OSError("private runtime tmpfs size option is invalid")
    value = int(raw)
    maximum = _RUNTIME_AUTHORITY_LIMIT + _RUNTIME_TMPFS_OVERHEAD + 4096
    if value <= 0 or value > maximum // factor:
        raise OSError("private runtime tmpfs size option is out of bounds")
    return value * factor


def _runtime_tmpfs_details(
    target: str,
    *,
    expected_size: int | None,
    expected_readonly: bool,
) -> tuple[int, bool, str, str, str]:
    encoded_target = os.fsencode(target).replace(b" ", b"\\040")
    for line in _mountinfo().splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != encoded_target or b"-" not in fields:
            continue
        separator = fields.index(b"-")
        if len(fields) != separator + 4:
            raise OSError("private runtime tmpfs mountinfo is unbounded")
        mount_option_list = fields[5].split(b",")
        mount_options = frozenset(mount_option_list)
        allowed_mount_options = {
            b"ro",
            b"rw",
            b"nosuid",
            b"nodev",
            b"relatime",
            b"noatime",
            b"nodiratime",
            b"strictatime",
            b"lazytime",
        }
        super_option_list = fields[separator + 3].split(b",")
        super_options = frozenset(super_option_list)
        size_options = [
            option for option in super_option_list if option.startswith(b"size=")
        ]
        if type(expected_readonly) is not bool:
            raise TypeError("expected_readonly must be bool")
        expected_access = b"ro" if expected_readonly else b"rw"
        opposite_access = b"rw" if expected_readonly else b"ro"
        expected_super_options = {expected_access, b"nr_inodes=16", b"mode=755"}
        allowed_super_extras = {b"inode64"}
        effective_size = (
            None if len(size_options) != 1 else _tmpfs_size_bytes(size_options[0])
        )
        if (
            fields[separator + 1] != b"tmpfs"
            or fields[separator + 2] != b"tmpfs"
            or not mount_options <= allowed_mount_options
            or len(mount_option_list) != len(mount_options)
            or b"nodev" not in mount_options
            or b"nosuid" not in mount_options
            or b"noexec" in mount_options
            or expected_access not in mount_options
            or opposite_access in mount_options
            or not 1 <= len(super_option_list) <= 8
            or len(super_option_list) != len(super_options)
            or any(not option or len(option) > 128 for option in super_options)
            or len(size_options) != 1
            or (expected_size is not None and effective_size != expected_size)
            or not expected_super_options <= super_options
            or expected_access not in super_options
            or opposite_access in super_options
            or not super_options
            <= (expected_super_options | allowed_super_extras | set(size_options))
        ):
            raise OSError("private runtime tmpfs options are not exact")
        return (
            int(fields[0]),
            b"ro" in mount_options,
            fields[separator + 2].decode("ascii"),
            ",".join(sorted(option.decode("ascii") for option in mount_options)),
            ",".join(sorted(option.decode("ascii") for option in super_options)),
        )
    raise OSError("private runtime tmpfs mount is absent")


def _runtime_tmpfs_observation(
    target: str,
    *,
    expected_size: int | None,
    expected_readonly: bool,
) -> tuple[int, bool]:
    details = _runtime_tmpfs_details(
        target,
        expected_size=expected_size,
        expected_readonly=expected_readonly,
    )
    return details[0], details[1]


def _mount_runtime_tmpfs(target: str, source_size: int) -> int:
    if source_size <= 0 or source_size > _RUNTIME_AUTHORITY_LIMIT:
        raise OSError("runtime authority exceeds private tmpfs bound")
    page_size = os.sysconf("SC_PAGE_SIZE")
    size = (
        (source_size + _RUNTIME_TMPFS_OVERHEAD + page_size - 1) // page_size * page_size
    )
    data = f"mode=0755,size={size},nr_inodes=16".encode("ascii")
    _libc_call(
        "mount",
        ctypes.c_char_p(b"tmpfs"),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_char_p(b"tmpfs"),
        ctypes.c_ulong(_MS_NOSUID | _MS_NODEV),
        ctypes.c_char_p(data),
    )
    mount_id, readonly = _runtime_tmpfs_observation(
        target, expected_size=size, expected_readonly=False
    )
    if mount_id <= 0 or readonly:
        raise OSError("private runtime tmpfs did not mount read-write")
    return size


def _retry_pread(fd: int, size: int, offset: int) -> bytes:
    while True:
        try:
            return os.pread(fd, size, offset)
        except InterruptedError:
            continue


def _retry_write(fd: int, payload: bytes) -> int:
    while True:
        try:
            return os.write(fd, payload)
        except InterruptedError:
            continue


def _sealed_payload_fd(payload: bytes) -> int:
    if not hasattr(os, "memfd_create"):
        raise MountNamespaceBrokerError(
            "runtime_unsupported",
            "sealed Docker payload descriptor is unavailable",
        )
    descriptor = os.memfd_create(
        "breadboard-docker-payload",
        os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )
    try:
        written = 0
        while written < len(payload):
            count = _retry_write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("Docker stdin descriptor write made no progress")
            written += count
        os.lseek(descriptor, 0, os.SEEK_SET)
        required_seals = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required_seals)
        metadata = os.fstat(descriptor)
        if metadata.st_size != len(payload):
            raise OSError("Docker stdin descriptor size changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_sealed_payload_fd(
    descriptor: int, *, expected_size: int, expected_digest: str, limit: int
) -> bytes:
    if (
        type(expected_size) is not int
        or not 0 <= expected_size <= limit
        or type(expected_digest) is not str
        or len(expected_digest) != 71
        or not expected_digest.startswith("sha256:")
    ):
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker payload metadata is invalid"
        )
    metadata = os.fstat(descriptor)
    required_seals = (
        fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size != expected_size
        or fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required_seals != required_seals
        or _digest_fd_exact(descriptor) != expected_digest
    ):
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker payload descriptor changed"
        )
    chunks: list[bytes] = []
    offset = 0
    while offset < expected_size:
        chunk = _retry_pread(descriptor, min(64 * 1024, expected_size - offset), offset)
        if not chunk:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker payload descriptor was truncated"
            )
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _copy_runtime_authority(source_fd: int, target: str, size: int, mode: int) -> None:
    if (
        size <= 0
        or size > _RUNTIME_AUTHORITY_LIMIT
        or mode & 0o111 == 0
        or mode & ~0o777
    ):
        raise OSError("runtime authority copy parameters are invalid")
    target_fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        offset = 0
        while offset < size:
            chunk = _retry_pread(source_fd, min(1024 * 1024, size - offset), offset)
            if not chunk:
                raise OSError("runtime authority truncated during copy")
            written = 0
            while written < len(chunk):
                count = _retry_write(target_fd, chunk[written:])
                if count <= 0:
                    raise OSError("runtime authority copy made no progress")
                written += count
            offset += len(chunk)
        os.fchmod(target_fd, mode)
        os.fsync(target_fd)
        if os.fstat(target_fd).st_size != size:
            raise OSError("runtime authority copy size changed")
    finally:
        os.close(target_fd)


def _seal_runtime_tmpfs(target: str, *, expected_size: int) -> tuple[int, bool]:
    _libc_call(
        "mount",
        ctypes.c_char_p(None),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_char_p(None),
        ctypes.c_ulong(_MS_REMOUNT | _MS_RDONLY | _MS_NOSUID | _MS_NODEV),
        ctypes.c_char_p(None),
    )
    observation = _runtime_tmpfs_observation(
        target, expected_size=expected_size, expected_readonly=True
    )
    if not observation[1]:
        raise OSError("private runtime tmpfs is not read-only")
    return observation


def _validate_runtime_copy(
    runtime_path: str,
    runtime_dir: str,
    *,
    expected_device: int,
    expected_inode: int,
    expected_size: int,
    expected_mode: int,
    expected_digest: str,
    expected_mount_id: int,
    expected_tmpfs_size: int,
) -> None:
    runtime_now = os.stat(runtime_path, follow_symlinks=False)
    mount_id, readonly = _runtime_tmpfs_observation(
        runtime_dir,
        expected_size=expected_tmpfs_size,
        expected_readonly=True,
    )
    copied_fd = os.open(runtime_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        digest = _digest_fd_exact(copied_fd)
    finally:
        os.close(copied_fd)
    if (
        not stat.S_ISREG(runtime_now.st_mode)
        or (runtime_now.st_dev, runtime_now.st_ino) != (expected_device, expected_inode)
        or stat.S_IMODE(runtime_now.st_mode) != expected_mode
        or runtime_now.st_size != expected_size
        or mount_id != expected_mount_id
        or not readonly
        or digest != expected_digest
    ):
        raise OSError("private runtime copy authority changed")


def _remove_runtime_bind(runtime_path: str | None, runtime_dir: str | None) -> None:
    if runtime_dir is not None:
        try:
            _mount_id(runtime_dir)
        except OSError:
            pass
        else:
            try:
                _unmount(runtime_dir)
            except OSError:
                _libc_call(
                    "umount2",
                    ctypes.c_char_p(os.fsencode(runtime_dir)),
                    ctypes.c_int(_MNT_DETACH),
                )
            try:
                _mount_id(runtime_dir)
            except OSError:
                pass
            else:
                raise OSError("private runtime tmpfs remained after unmount")
    if runtime_path is not None and os.path.lexists(runtime_path):
        os.unlink(runtime_path)
    if runtime_dir is not None:
        try:
            os.rmdir(runtime_dir)
        except FileNotFoundError:
            pass
    if (runtime_path is not None and os.path.lexists(runtime_path)) or (
        runtime_dir is not None and os.path.lexists(runtime_dir)
    ):
        raise OSError("private runtime snapshot cleanup is incomplete")


def _remove_stage_target(target: str, *, directory: bool) -> None:
    try:
        _mount_id(target)
    except OSError:
        mounted = False
    else:
        mounted = True
    if mounted:
        _unmount(target)
    try:
        if directory:
            os.rmdir(target)
        else:
            os.unlink(target)
    except FileNotFoundError:
        pass


def _stage_document(stage: _Stage) -> dict[str, Any]:
    receipt = stage.receipt
    return {
        "source_path": receipt.source_path,
        "source_device": receipt.source_device,
        "source_inode": receipt.source_inode,
        "source_mode": receipt.source_mode,
        "descriptor_device": receipt.descriptor_device,
        "descriptor_inode": receipt.descriptor_inode,
        "mount_id": stage.mount_id,
        "readonly": stage.readonly,
    }


def _validate_stage(stage: _Stage, authority_fd: int | None = None) -> None:
    receipt = stage.receipt
    current = os.stat(receipt.source_path, follow_symlinks=False)
    mount_id, readonly = _mount_observation(receipt.source_path)
    if (
        (current.st_dev, current.st_ino)
        != (receipt.source_device, receipt.source_inode)
        or stat.S_IFMT(current.st_mode) != receipt.source_mode
        or mount_id != stage.mount_id
        or readonly != stage.readonly
    ):
        raise OSError("staged mount identity drifted")
    held = os.fstat(stage.fd)
    mounted_authority = os.fstat(stage.mount_fd)
    if (
        mounted_authority.st_dev,
        mounted_authority.st_ino,
    ) != (
        receipt.descriptor_device,
        receipt.descriptor_inode,
    ):
        raise OSError("child-opened descriptor identity drifted")
    if (held.st_dev, held.st_ino) != (
        receipt.descriptor_device,
        receipt.descriptor_inode,
    ):
        raise OSError("held descriptor identity drifted")
    if authority_fd is not None:
        authority = os.fstat(authority_fd)
        if (authority.st_dev, authority.st_ino) != (held.st_dev, held.st_ino):
            raise OSError("authority descriptor identity changed")


def _descriptor_source_path(descriptor: int) -> str:
    source = os.readlink(f"/proc/self/fd/{descriptor}")
    if (
        not source.startswith("/")
        or source.endswith(" (deleted)")
        or os.path.normpath(source) != source
    ):
        raise MountNamespaceBrokerError(
            "workspace_authority_mismatch",
            "descriptor source path is not exact",
        )
    descriptor_metadata = os.fstat(descriptor)
    path_metadata = os.stat(source, follow_symlinks=False)
    if (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
        stat.S_IFMT(descriptor_metadata.st_mode),
    ) != (
        path_metadata.st_dev,
        path_metadata.st_ino,
        stat.S_IFMT(path_metadata.st_mode),
    ):
        raise MountNamespaceBrokerError(
            "workspace_authority_mismatch",
            "descriptor source path identity changed",
        )
    return source


def _authority_fds(authority: Any) -> dict[str, int]:
    admitted = {
        "dockerd": authority.dockerd,
        "docker": authority.docker,
        "containerd": authority.containerd,
        "runc": authority.runc,
        **{
            f"image:{index}": image.archive
            for index, image in enumerate(authority.images)
        },
    }
    opened: dict[str, int] = {}
    try:
        for name, item in admitted.items():
            fd = os.open(
                item.path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(fd)
            digest = _digest_fd_exact(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != item.owner_uid
                or stat.S_IMODE(metadata.st_mode) != item.mode
                or bool(metadata.st_mode & 0o111) != item.executable
                or digest != item.digest
            ):
                os.close(fd)
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker file authority mismatch"
                )
            current = os.stat(item.path, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                os.close(fd)
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker file authority path drifted"
                )
            opened[name] = fd
        return opened
    except BaseException:
        for fd in opened.values():
            os.close(fd)
        raise


def _construct_parent_binding(
    child_payload: Mapping[str, Any],
    *,
    config_fd: int,
    runtime_fd: int,
    parent_pid: int,
) -> PrivateDockerDaemonBinding:
    payload = dict(child_payload)
    payload.update(
        {
            "config_fd": config_fd,
            "config_proc_path": f"/proc/{parent_pid}/fd/{config_fd}",
            "runtime_fd": runtime_fd,
            "runtime_proc_path": f"/proc/{parent_pid}/fd/{runtime_fd}",
        }
    )
    return PrivateDockerDaemonBinding(**payload)


def _stop_process_group(process: subprocess.Popen[bytes], process_group: int) -> None:
    direct_running = process.poll() is None
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if direct_running:
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired as exc:
            raise OSError("broker command could not be reaped") from exc
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    raise OSError("broker command process group survived cleanup")


def _new_output_descriptor() -> int:
    if not hasattr(os, "memfd_create"):
        raise MountNamespaceBrokerError(
            "runtime_unsupported",
            "sealed Docker output descriptor is unavailable",
        )
    return os.memfd_create(
        "breadboard-docker-output",
        os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )


def _seal_output_descriptor(descriptor: int, size: int) -> None:
    required_seals = (
        fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    )
    os.lseek(descriptor, 0, os.SEEK_SET)
    fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required_seals)
    metadata = os.fstat(descriptor)
    if metadata.st_size != size:
        raise OSError("Docker output descriptor size changed")


def _execute_bounded_descriptors(
    argv: Sequence[str],
    *,
    executable_fd: int,
    timeout_ms: int,
    output_limit: int,
    input_fd: int | None = None,
    cancellation_fd: int | None = None,
) -> tuple[
    int,
    tuple[int, int],
    tuple[int, int],
    tuple[str, str],
    bool,
    bool,
]:
    if type(output_limit) is not int or not 1 <= output_limit <= _MAX_OUTPUT:
        raise ValueError("Docker output limit is outside the fixed global bound")
    if cancellation_fd is not None:
        cancellation_metadata = os.fstat(cancellation_fd)
        if not stat.S_ISFIFO(cancellation_metadata.st_mode):
            raise ValueError("Docker cancellation descriptor is not a pipe")
    output_fds = (-1, -1)
    try:
        output_fds = (_new_output_descriptor(), _new_output_descriptor())
        process = subprocess.Popen(
            tuple(argv),
            executable=f"/proc/self/fd/{executable_fd}",
            pass_fds=(executable_fd,),
            env={},
            stdin=subprocess.DEVNULL if input_fd is None else input_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        process_group = os.getpgid(process.pid)
        if process_group != process.pid:
            _stop_process_group(process, process_group)
            raise OSError("broker command process group identity is not exact")
        assert process.stdout is not None and process.stderr is not None
        streams = (process.stdout, process.stderr)
        selector = selectors.DefaultSelector()
        counts = [0, 0]
        hashers = [hashlib.sha256(), hashlib.sha256()]
        for index, stream in enumerate(streams):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        if cancellation_fd is not None:
            selector.register(cancellation_fd, selectors.EVENT_READ, None)
        deadline = time.monotonic() + timeout_ms / 1000
        timed_out = False
        output_limited = False
        cancelled = False
        try:
            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    timed_out = True
                    _stop_process_group(process, process_group)
                    break
                events = selector.select(min(remaining_time, 0.05))
                if not events and process.poll() is not None:
                    events = selector.select(0)
                    if not events:
                        _stop_process_group(process, process_group)
                        break
                for key, _ in events:
                    if key.data is None:
                        try:
                            os.read(key.fileobj, 1)
                        except BlockingIOError:
                            continue
                        cancelled = True
                        _stop_process_group(process, process_group)
                        break
                    try:
                        chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    index = key.data
                    remaining = output_limit - counts[0] - counts[1]
                    if remaining <= 0:
                        output_limited = True
                    else:
                        accepted = chunk[:remaining]
                        offset = 0
                        while offset < len(accepted):
                            count = _retry_write(output_fds[index], accepted[offset:])
                            if count <= 0:
                                raise OSError(
                                    "Docker output descriptor write made no progress"
                                )
                            offset += count
                        hashers[index].update(accepted)
                        counts[index] += len(accepted)
                        if len(chunk) > len(accepted):
                            output_limited = True
                    if output_limited:
                        _stop_process_group(process, process_group)
                        break
                if output_limited or cancelled:
                    break
            if process.poll() is None:
                remaining_time = max(0.0, deadline - time.monotonic())
                try:
                    process.wait(timeout=remaining_time)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _stop_process_group(process, process_group)
        except BaseException:
            _stop_process_group(process, process_group)
            raise
        finally:
            selector.close()
            for stream in streams:
                try:
                    stream.close()
                except OSError:
                    pass
        for descriptor, size in zip(output_fds, counts, strict=True):
            _seal_output_descriptor(descriptor, size)
        return (
            process.returncode if process.returncode is not None else -signal.SIGKILL,
            output_fds,
            (counts[0], counts[1]),
            (
                "sha256:" + hashers[0].hexdigest(),
                "sha256:" + hashers[1].hexdigest(),
            ),
            timed_out,
            output_limited,
        )
    except BaseException:
        for descriptor in output_fds:
            if descriptor >= 0:
                os.close(descriptor)
        raise


def _read_output_descriptor(descriptor: int, size: int) -> tuple[bytes, str]:
    if type(size) is not int or not 0 <= size <= _MAX_OUTPUT:
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker output descriptor size is invalid"
        )
    metadata = os.fstat(descriptor)
    required_seals = (
        fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size != size
        or fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required_seals != required_seals
    ):
        raise MountNamespaceBrokerError(
            "runtime_unsupported", "broker output descriptor changed"
        )
    chunks: list[bytes] = []
    hasher = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = _retry_pread(descriptor, min(64 * 1024, size - offset), offset)
        if not chunk:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker output descriptor was truncated"
            )
        chunks.append(chunk)
        hasher.update(chunk)
        offset += len(chunk)
    return b"".join(chunks), "sha256:" + hasher.hexdigest()


def _execute_bounded(
    argv: Sequence[str],
    *,
    executable_fd: int,
    timeout_ms: int,
    output_limit: int,
    input_fd: int | None = None,
) -> tuple[int, bytes, bytes, bool, bool]:
    returncode, output_fds, counts, _digests, timed_out, output_limited = (
        _execute_bounded_descriptors(
            argv,
            executable_fd=executable_fd,
            timeout_ms=timeout_ms,
            output_limit=output_limit,
            input_fd=input_fd,
        )
    )
    try:
        stdout, _ = _read_output_descriptor(output_fds[0], counts[0])
        stderr, _ = _read_output_descriptor(output_fds[1], counts[1])
        return returncode, stdout, stderr, timed_out, output_limited
    finally:
        for descriptor in output_fds:
            os.close(descriptor)


def _consume_log_fds(
    receipts: Mapping[str, Any],
    fds: Sequence[int],
    *,
    limit: int,
) -> dict[str, dict[str, Any]]:
    roles = ("containerd", "dockerd")
    try:
        if tuple(receipts) != roles or len(fds) != len(roles):
            raise OSError("broker log descriptor order is invalid")
        complete: dict[str, dict[str, Any]] = {}
        for role, fd in zip(roles, fds, strict=True):
            raw_receipt = receipts[role]
            if type(raw_receipt) is not dict:
                raise OSError("broker log receipt is invalid")
            receipt = dict(raw_receipt)
            metadata = os.fstat(fd)
            size = receipt.get("size_bytes")
            mode = receipt.get("mode")
            if (
                receipt.get("role") != role
                or type(size) is not int
                or size < 0
                or size > limit
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != size
                or stat.S_IMODE(metadata.st_mode) != mode
            ):
                raise OSError("broker log descriptor metadata is invalid")
            chunks: list[bytes] = []
            offset = 0
            while offset < size:
                chunk = os.pread(fd, min(1024 * 1024, size - offset), offset)
                if not chunk:
                    raise OSError("broker log descriptor was short-read")
                chunks.append(chunk)
                offset += len(chunk)
            raw = b"".join(chunks)
            if "sha256:" + hashlib.sha256(raw).hexdigest() != receipt.get("sha256"):
                raise OSError("broker log descriptor digest is invalid")
            receipt["bytes_base64"] = base64.b64encode(raw).decode("ascii")
            complete[role] = receipt
        return complete
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _journal_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _journal_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_journal_value(item) for item in value]
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise TypeError("progress journal value is not canonical JSON")


def _error_detail_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _ERROR_DETAIL_MAX_DEPTH:
        return {"truncated": True}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        items = sorted(
            ((str(key), item) for key, item in value.items()),
            key=lambda pair: pair[0],
        )
        for key, item in items[:_ERROR_DETAIL_MAX_ITEMS]:
            if any(redacted in key.lower() for redacted in _ERROR_DETAIL_REDACTED_KEYS):
                result[key] = "[redacted]"
                continue
            result[key] = _error_detail_value(item, depth=depth + 1)
        if len(value) > _ERROR_DETAIL_MAX_ITEMS:
            result["truncated"] = True
        return result
    if isinstance(value, (tuple, list)):
        items = [
            _error_detail_value(item, depth=depth + 1)
            for item in value[:_ERROR_DETAIL_MAX_ITEMS]
        ]
        if len(value) > _ERROR_DETAIL_MAX_ITEMS:
            items.append({"truncated": True})
        return items
    if value is None or type(value) in (bool, int, float):
        return value
    if type(value) is str:
        return value[:_ERROR_DETAIL_STRING_LIMIT]
    return {"unserializable_type": type(value).__name__}


def _exception_leaves(
    error: BaseException,
    *,
    operation: str | None = None,
) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    projected_bytes = 0
    truncated = False

    def append_projection(projection: dict[str, Any]) -> bool:
        nonlocal projected_bytes, truncated
        encoded_size = len(
            json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        )
        if (
            len(leaves) >= _ERROR_PROJECTION_MAX_LEAVES
            or projected_bytes + encoded_size > _ERROR_PROJECTION_MAX_BYTES
        ):
            if not truncated:
                truncated = True
                leaves.append(
                    {
                        "code": "error_projection_truncated",
                        "type": "ErrorProjectionLimit",
                        "message": "nested broker errors exceeded the bounded projection",
                        "operation": operation,
                        "details": {"truncated": True},
                    }
                )
            return False
        leaves.append(projection)
        projected_bytes += encoded_size
        return True

    def visit(
        current: BaseException,
        group_path: tuple[int, ...],
        depth: int,
    ) -> None:
        if truncated:
            return
        children = getattr(current, "exceptions", ())
        if children:
            if depth >= _ERROR_PROJECTION_MAX_DEPTH:
                append_projection(
                    {
                        "code": "error_projection_truncated",
                        "type": type(current).__name__,
                        "message": "nested broker error depth exceeded",
                        "operation": operation,
                        "details": {
                            "group_path": list(group_path),
                            "truncated": True,
                        },
                    }
                )
                return
            for index, child in enumerate(children):
                visit(child, (*group_path, index), depth + 1)
            return
        raw_details = getattr(current, "details", None)
        details = _error_detail_value(raw_details)
        if not isinstance(details, dict):
            details = {"value": details}
        details["group_path"] = list(group_path)
        code = getattr(current, "code", None)
        append_projection(
            {
                "code": (
                    code[:_ERROR_DETAIL_STRING_LIMIT]
                    if type(code) is str
                    else "unclassified"
                ),
                "type": type(current).__name__[:_ERROR_DETAIL_STRING_LIMIT],
                "message": str(current)[:_ERROR_MESSAGE_LIMIT],
                "operation": operation,
                "details": details,
            }
        )

    visit(error, (), 0)
    return leaves


class _ProgressJournal:
    def __init__(self, fd: int, *, writer: str, limit: int = 1024 * 1024) -> None:
        self._fd = fd
        self._writer = writer
        self._limit = limit
        self._size = os.fstat(fd).st_size
        self._sequence = 0

    def __call__(self, event: Mapping[str, Any]) -> None:
        self._sequence += 1
        payload = (
            _canonical(
                {
                    "event": _journal_value(event),
                    "sequence": self._sequence,
                    "writer": self._writer,
                }
            )
            + b"\n"
        )
        current_size = os.fstat(self._fd).st_size
        if current_size + len(payload) > self._limit:
            raise OSError(errno.EFBIG, "broker progress journal limit reached")
        offset = 0
        while offset < len(payload):
            offset += os.write(self._fd, payload[offset:])
        os.fsync(self._fd)
        self._size = current_size + len(payload)


SUPERVISOR_JOURNAL_SCHEMA_VERSION = "bb.rl.mount-namespace-supervisor.v2"
SUPERVISOR_RECEIPT_SCHEMA_VERSION = SUPERVISOR_JOURNAL_SCHEMA_VERSION
_SUPERVISOR_JOURNAL_LIMIT = 1024 * 1024
_JOURNAL_DIGEST_LENGTH = 71
_SUPERVISOR_JOURNAL_TOTAL_ENTRY_LIMIT = 8_192
_SUPERVISOR_JOURNAL_INVENTORY_LIMIT = 4_096
_SUPERVISOR_FINAL_RETENTION = 1
_JOURNAL_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "state",
        "generation",
        "generation_digest",
        "owner_token_digest",
        "lease_id",
        "workspace_id",
        "epoch",
        "role",
        "plan_digest",
        "broker",
        "daemon",
        "containerd",
        "runtime",
        "config",
        "daemon_root",
        "stage_root",
        "stages",
        "container",
        "proof",
    }
)
_JOURNAL_PROCESS_KEYS = frozenset(
    {
        "pid",
        "starttime",
        "pgid",
        "executable_device",
        "executable_inode",
        "executable_ctime_ns",
        "executable_size",
        "executable_digest",
        "namespace_device",
        "namespace_inode",
    }
)
_JOURNAL_PATH_KEYS = frozenset(
    {
        "path",
        "device",
        "inode",
        "mode",
        "digest",
        "parent_path",
        "parent_device",
        "parent_inode",
    }
)


def _journal_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _journal_digest_value(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _JOURNAL_DIGEST_LENGTH
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _journal_name(lease_id: str) -> str:
    if (
        type(lease_id) is not str
        or not 1 <= len(lease_id) <= 256
        or lease_id in {".", ".."}
        or "/" in lease_id
        or "\x00" in lease_id
    ):
        raise ValueError("journal lease id is not a safe basename")
    return lease_id + ".supervisor.json"


def _journal_process_valid(value: object, *, allow_none: bool = False) -> bool:
    if allow_none and value is None:
        return True
    if type(value) is not dict or set(value) != _JOURNAL_PROCESS_KEYS:
        return False
    return (
        type(value["pid"]) is int
        and 0 < value["pid"] <= (1 << 53) - 1
        and type(value["starttime"]) is str
        and value["starttime"].isdigit()
        and type(value["pgid"]) is int
        and 0 < value["pgid"] <= (1 << 53) - 1
        and all(
            type(value[key]) is int and value[key] >= 0
            for key in (
                "executable_device",
                "executable_inode",
                "executable_ctime_ns",
                "executable_size",
                "namespace_device",
                "namespace_inode",
            )
        )
        and value["executable_size"] > 0
        and _journal_digest_value(value["executable_digest"])
    )


def _journal_path_valid(value: object, *, allow_none: bool = False) -> bool:
    if allow_none and value is None:
        return True
    if type(value) is not dict or set(value) != _JOURNAL_PATH_KEYS:
        return False
    return (
        type(value["path"]) is str
        and value["path"].startswith("/")
        and os.path.normpath(value["path"]) == value["path"]
        and type(value["parent_path"]) is str
        and value["parent_path"].startswith("/")
        and os.path.normpath(value["parent_path"]) == value["parent_path"]
        and os.path.dirname(value["path"]) == value["parent_path"]
        and all(
            type(value[key]) is int and value[key] >= 0
            for key in (
                "device",
                "inode",
                "mode",
                "parent_device",
                "parent_inode",
            )
        )
        and _journal_digest_value(value["digest"])
    )


def _validate_journal_payload(
    payload: object,
    *,
    expected_lease_id: str | None = None,
    expected_generation_digest: str | None = None,
    expected_owner_token_digest: str | None = None,
) -> bool:
    if type(payload) is not dict or set(payload) != _JOURNAL_REQUIRED_KEYS:
        return False
    try:
        safe_name = _journal_name(payload["lease_id"])
    except (TypeError, ValueError):
        return False
    if (
        payload["schema_version"] != SUPERVISOR_JOURNAL_SCHEMA_VERSION
        or payload["state"] not in {"ACTIVE", "FINAL", "QUARANTINED"}
        or type(payload["generation"]) is not str
        or not _journal_digest_value(payload["generation_digest"])
        or not _journal_digest_value(payload["owner_token_digest"])
        or type(payload["lease_id"]) is not str
        or safe_name != payload["lease_id"] + ".supervisor.json"
        or type(payload["workspace_id"]) is not str
        or not payload["workspace_id"]
        or type(payload["epoch"]) is not int
        or not 0 < payload["epoch"] <= (1 << 53) - 1
        or payload["role"] not in {"primary", "verifier"}
        or not _journal_digest_value(payload["plan_digest"])
    ):
        return False
    generation = (
        f"{payload['lease_id']}:{payload['workspace_id']}:{payload['epoch']}:"
        f"{payload['role']}:{payload['plan_digest']}"
    )
    if payload["generation"] != generation or payload[
        "generation_digest"
    ] != _journal_digest(generation.encode("utf-8")):
        return False
    if (
        (expected_lease_id is not None and payload["lease_id"] != expected_lease_id)
        or (
            expected_generation_digest is not None
            and payload["generation_digest"] != expected_generation_digest
        )
        or (
            expected_owner_token_digest is not None
            and payload["owner_token_digest"] != expected_owner_token_digest
        )
    ):
        return False
    if not _journal_process_valid(payload["broker"]):
        return False
    if not _journal_process_valid(payload["daemon"], allow_none=True):
        return False
    if not _journal_process_valid(payload["containerd"], allow_none=True):
        return False
    for key in ("runtime", "config", "daemon_root"):
        if not _journal_path_valid(payload[key], allow_none=True):
            return False
    if not _journal_path_valid(payload["stage_root"]):
        return False
    stages = payload["stages"]
    if (
        type(stages) is not list
        or len(stages) > 256
        or any(
            type(stage) is not dict
            or set(stage)
            != {
                "source_path",
                "source_device",
                "source_inode",
                "source_mode",
                "descriptor_device",
                "descriptor_inode",
                "mount_id",
                "readonly",
                "source_parent_path",
                "source_parent_device",
                "source_parent_inode",
            }
            or type(stage["source_path"]) is not str
            or not stage["source_path"].startswith("/")
            or os.path.normpath(stage["source_path"]) != stage["source_path"]
            or type(stage["source_parent_path"]) is not str
            or not stage["source_parent_path"].startswith("/")
            or os.path.normpath(stage["source_parent_path"])
            != stage["source_parent_path"]
            or os.path.dirname(stage["source_path"]) != stage["source_parent_path"]
            or any(
                type(stage[key]) is not int or stage[key] < 0
                for key in (
                    "source_device",
                    "source_inode",
                    "source_mode",
                    "descriptor_device",
                    "descriptor_inode",
                    "mount_id",
                    "source_parent_device",
                    "source_parent_inode",
                )
            )
            or type(stage["readonly"]) is not bool
            for stage in stages
        )
    ):
        return False
    container = payload["container"]
    if (
        type(container) is not dict
        or set(container) != {"id", "name", "labels"}
        or (
            container["id"] is not None
            and (
                type(container["id"]) is not str
                or len(container["id"]) != 64
                or any(char not in "0123456789abcdef" for char in container["id"])
            )
        )
        or type(container["name"]) is not str
        or len(container["name"]) > 256
        or type(container["labels"]) is not dict
        or len(container["labels"]) > 64
        or any(
            type(key) is not str
            or not key
            or len(key) > 256
            or type(value) is not str
            or len(value) > 256
            for key, value in container["labels"].items()
        )
    ):
        return False
    proof = payload["proof"]
    if (
        type(proof) is not dict
        or set(proof)
        != {
            "container_absence",
            "stages_absence",
            "daemon_absence",
            "containerd_absence",
            "runtime_absence",
            "config_absence",
            "root_absence",
        }
        or any(type(value) is not bool for value in proof.values())
    ):
        return False
    return payload["state"] != "FINAL" or all(proof.values())


def validate_supervisor_receipt(
    receipt: Mapping[str, Any],
    *,
    authenticator: Any,
    expected_lease_id: str | None = None,
    expected_generation_digest: str | None = None,
    expected_owner_token_digest: str | None = None,
) -> bool:
    """Validate one bounded, authenticated supervisor receipt."""
    try:
        if (
            type(receipt) is not dict
            or set(receipt)
            != {"payload", "checksum", "key_id", "algorithm", "signature_base64"}
            or receipt["key_id"] != authenticator.key_id
            or receipt["algorithm"] != authenticator.algorithm
        ):
            return False
        payload = receipt["payload"]
        checksum = receipt["checksum"]
        unsigned = {
            "payload": payload,
            "checksum": checksum,
            "key_id": receipt["key_id"],
            "algorithm": receipt["algorithm"],
        }
        signature = base64.b64decode(receipt["signature_base64"], validate=True)
        return (
            type(checksum) is str
            and checksum == _journal_digest(_canonical(payload))
            and authenticator.verify(_canonical(unsigned), signature)
            and _validate_journal_payload(
                payload,
                expected_lease_id=expected_lease_id,
                expected_generation_digest=expected_generation_digest,
                expected_owner_token_digest=expected_owner_token_digest,
            )
        )
    except (AttributeError, TypeError, ValueError, OSError):
        return False


def _atomic_journal_write(
    root_fd: int,
    lease_id: str,
    payload: Mapping[str, Any],
    *,
    authenticator: Any,
) -> None:
    name = _journal_name(lease_id)
    checksum = _journal_digest(_canonical(payload))
    unsigned = {
        "payload": dict(payload),
        "checksum": checksum,
        "key_id": authenticator.key_id,
        "algorithm": authenticator.algorithm,
    }
    envelope = {
        **unsigned,
        "signature_base64": base64.b64encode(
            authenticator.sign(_canonical(unsigned))
        ).decode("ascii"),
    }
    encoded = _canonical(envelope)
    if len(encoded) > _SUPERVISOR_JOURNAL_LIMIT:
        raise OSError(errno.EFBIG, "supervisor journal exceeds fixed bound")
    directory_fd = os.dup(root_fd)
    temporary = "." + name + ".tmp-" + secrets.token_hex(16)
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(encoded):
            count = _retry_write(descriptor, encoded[offset:])
            if count <= 0:
                raise OSError("short supervisor journal write")
            offset += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _read_journal(
    root_fd: int,
    lease_id: str,
    *,
    authenticator: Any,
) -> dict[str, Any]:
    name = _journal_name(lease_id)
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        dir_fd=root_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > _SUPERVISOR_JOURNAL_LIMIT
        ):
            raise OSError("supervisor journal inode is not bounded")
        chunks: list[bytes] = []
        offset = 0
        while offset < metadata.st_size:
            chunk = _retry_pread(
                descriptor, min(64 * 1024, metadata.st_size - offset), offset
            )
            if not chunk:
                raise OSError("supervisor journal short read")
            chunks.append(chunk)
            offset += len(chunk)
        envelope = _decode(b"".join(chunks))
        if not validate_supervisor_receipt(
            envelope,
            authenticator=authenticator,
            expected_lease_id=lease_id,
        ):
            raise OSError("supervisor journal authentication failed")
        return envelope
    finally:
        os.close(descriptor)


def _bounded_journal_names(root_fd: int) -> tuple[str, ...]:
    names: list[str] = []
    total = 0
    with os.scandir(root_fd) as entries:
        for entry in entries:
            total += 1
            if total > _SUPERVISOR_JOURNAL_TOTAL_ENTRY_LIMIT:
                raise MountNamespaceBrokerError(
                    "runtime_cleanup_pending",
                    "supervisor journal directory exceeds its fixed entry bound",
                )
            name = entry.name
            if (
                name.endswith(".supervisor.json")
                and "/" not in name
                and name not in {".", ".."}
            ):
                names.append(name)
                if len(names) > _SUPERVISOR_JOURNAL_INVENTORY_LIMIT:
                    raise MountNamespaceBrokerError(
                        "runtime_cleanup_pending",
                        "supervisor journal inventory exceeds its fixed bound",
                    )
    return tuple(sorted(names))


def _prune_final_journals(
    root_fd: int,
    *,
    authenticator: Any,
    keep: int = _SUPERVISOR_FINAL_RETENTION,
) -> None:
    finals: list[tuple[int, str]] = []
    for name in _bounded_journal_names(root_fd):
        lease_id = name.removesuffix(".supervisor.json")
        try:
            envelope = _read_journal(
                root_fd,
                lease_id,
                authenticator=authenticator,
            )
            metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            continue
        payload = envelope["payload"]
        if payload["state"] == "FINAL" and all(payload["proof"].values()):
            finals.append((metadata.st_mtime_ns, name))
    remove_count = len(finals) - keep
    if remove_count <= 0:
        return
    removed = False
    for _, name in sorted(finals)[:remove_count]:
        try:
            os.unlink(name, dir_fd=root_fd)
        except FileNotFoundError:
            continue
        removed = True
    if removed:
        os.fsync(root_fd)


def _read_lease_journal_authority(
    lease_root_fd: int, payload: Mapping[str, Any]
) -> bool:
    lease_id = payload["lease_id"]
    try:
        descriptor = os.open(
            lease_id + ".json",
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=lease_root_fd,
        )
    except FileNotFoundError:
        proof = payload["proof"]
        return proof["container_absence"] is True and proof["stages_absence"] is True
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > 65_536
        ):
            return False
        encoded = _retry_pread(descriptor, metadata.st_size, 0)
        if len(encoded) != metadata.st_size:
            return False
        envelope = _decode(encoded)
        if (
            type(envelope) is not dict
            or set(envelope) != {"payload", "checksum"}
            or type(envelope["payload"]) is not dict
            or envelope["checksum"] != _journal_digest(_canonical(envelope["payload"]))
        ):
            return False
        lease = envelope["payload"]
        owner_token = lease.get("owner_token")
        return (
            type(owner_token) is str
            and _journal_digest(owner_token.encode("utf-8"))
            == payload["owner_token_digest"]
            and lease.get("lease_id") == lease_id
            and lease.get("workspace_id") == payload["workspace_id"]
            and lease.get("epoch") == payload["epoch"]
            and lease.get("role") == payload["role"]
            and lease.get("effective_plan_digest") == payload["plan_digest"]
        )
    except (OSError, TypeError, ValueError):
        return False
    finally:
        os.close(descriptor)


def _journal_process_absent(process: Mapping[str, Any] | None) -> bool:
    if process is None:
        return True
    pid = process["pid"]
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return True
    except OSError:
        raise
    try:
        fields = raw[raw.rindex(")") + 2 :].split()
    except ValueError as exc:
        raise OSError("process identity observation is malformed") from exc
    if len(fields) < 20:
        raise OSError("process identity observation is incomplete")
    if fields[19] != process["starttime"]:
        return True
    try:
        executable = os.stat(f"/proc/{pid}/exe", follow_symlinks=True)
        namespace = os.stat(f"/proc/{pid}/ns/mnt", follow_symlinks=False)
        pgid = os.getpgid(pid)
    except (FileNotFoundError, ProcessLookupError):
        return True
    if (
        pgid != process["pgid"]
        or executable.st_dev != process["executable_device"]
        or executable.st_ino != process["executable_inode"]
        or executable.st_ctime_ns != process["executable_ctime_ns"]
        or executable.st_size != process["executable_size"]
        or namespace.st_dev != process["namespace_device"]
        or namespace.st_ino != process["namespace_inode"]
    ):
        raise OSError("live process identity changed")
    descriptor = os.open(
        f"/proc/{pid}/exe",
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if _digest_fd_exact(descriptor) != process["executable_digest"]:
            raise OSError("live process executable digest changed")
    finally:
        os.close(descriptor)
    return False


def _journal_path_name_absent(path: str) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


def _journal_path_absent(path: Mapping[str, Any] | None) -> bool:
    if path is None:
        return True
    parent_fd = os.open(
        path["parent_path"],
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        parent = os.fstat(parent_fd)
        if (parent.st_dev, parent.st_ino) != (
            path["parent_device"],
            path["parent_inode"],
        ):
            raise OSError("journal path parent authority changed")
        try:
            target = os.stat(
                os.path.basename(path["path"]),
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return True
        if (
            target.st_dev,
            target.st_ino,
            stat.S_IMODE(target.st_mode),
        ) != (
            path["device"],
            path["inode"],
            path["mode"],
        ):
            raise OSError("journal path authority changed")
        return False
    finally:
        os.close(parent_fd)


def _journal_stage_absent(stage: Mapping[str, Any]) -> bool:
    return _journal_path_absent(
        {
            "path": stage["source_path"],
            "device": stage["source_device"],
            "inode": stage["source_inode"],
            "mode": stage["source_mode"],
            "parent_path": stage["source_parent_path"],
            "parent_device": stage["source_parent_device"],
            "parent_inode": stage["source_parent_inode"],
        }
    )


def recover_supervisor_journals(
    journal_root_fd: int,
    lease_root_fd: int,
    *,
    authenticator: Any,
) -> tuple[Mapping[str, Any], ...]:
    """Authenticate prior lease journals and finalize only observed absence."""
    names = _bounded_journal_names(journal_root_fd)
    recovered: list[Mapping[str, Any]] = []
    residuals: list[str] = []
    for name in names:
        lease_id = name.removesuffix(".supervisor.json")
        envelope = _read_journal(
            journal_root_fd,
            lease_id,
            authenticator=authenticator,
        )
        payload = envelope["payload"]
        if payload["state"] == "FINAL" and not all(payload["proof"].values()):
            residuals.append(lease_id + ":invalid_final")
            continue
        if not _read_lease_journal_authority(lease_root_fd, payload):
            residuals.append(lease_id + ":authority")
            continue
        try:
            process_absence = {
                "broker_absence": _journal_process_absent(payload["broker"]),
                "daemon_absence": _journal_process_absent(payload["daemon"]),
                "containerd_absence": _journal_process_absent(payload["containerd"]),
            }
            root_absence = _journal_path_absent(payload["daemon_root"])
            stage_root_absence = root_absence or _journal_path_absent(
                payload["stage_root"]
            )
            path_absence = {
                "runtime_absence": root_absence
                or _journal_path_absent(payload["runtime"]),
                "config_absence": root_absence
                or _journal_path_absent(payload["config"]),
                "stages_absence": stage_root_absence
                or all(_journal_stage_absent(stage) for stage in payload["stages"]),
            }
        except OSError:
            payload["state"] = "QUARANTINED"
            payload["proof"] = {key: False for key in payload["proof"]}
            _atomic_journal_write(
                journal_root_fd,
                lease_id,
                payload,
                authenticator=authenticator,
            )
            residuals.append(lease_id + ":observation")
            continue
        proof = {
            "container_absence": (
                payload["proof"]["container_absence"]
                or (
                    process_absence["daemon_absence"]
                    and process_absence["containerd_absence"]
                    and root_absence
                )
            ),
            "stages_absence": path_absence["stages_absence"],
            "daemon_absence": process_absence["daemon_absence"],
            "containerd_absence": process_absence["containerd_absence"],
            "runtime_absence": path_absence["runtime_absence"],
            "config_absence": path_absence["config_absence"],
            "root_absence": root_absence,
        }
        final = process_absence["broker_absence"] and all(proof.values())
        payload["state"] = "FINAL" if final else "QUARANTINED"
        payload["proof"] = proof
        _atomic_journal_write(
            journal_root_fd,
            lease_id,
            payload,
            authenticator=authenticator,
        )
        recovered_envelope = _read_journal(
            journal_root_fd,
            lease_id,
            authenticator=authenticator,
        )
        recovered.append(MappingProxyType(recovered_envelope))
        if not final:
            residuals.append(lease_id + ":residual")
    _prune_final_journals(
        journal_root_fd,
        authenticator=authenticator,
    )
    if residuals:
        raise MountNamespaceBrokerError(
            "runtime_cleanup_pending",
            "prior supervisor cleanup is not proven",
            details={"residuals": tuple(residuals)},
        )
    return tuple(recovered)


def _child_loop(
    sock: socket.socket,
    token: str,
    stage_root: str,
    daemon_authority: Any | None,
    authority_fds: Mapping[str, int],
    parent_pid: int,
    progress_fd: int | None,
) -> None:
    stages: dict[str, _Stage] = {}
    failed_stage_targets: set[tuple[str, bool]] = set()
    daemon_owner: Any | None = None
    expected_sequence = 1
    runtime_dir: str | None = None
    runtime_path: str | None = None
    runtime_mount_id: int | None = None
    runtime_fd: int | None = None
    runtime_snapshot_fd: int | None = None
    runtime_tmpfs_size: int | None = None
    startup_step = "enter_namespace"
    progress_sink = (
        _ProgressJournal(progress_fd, writer="broker-child")
        if progress_fd is not None
        else None
    )
    if progress_sink is not None:
        progress_sink({"event": "broker_child_start", "phase": "startup"})
    try:
        if progress_sink is not None:
            progress_sink({"event": "enter_namespace", "phase": "begin"})
        _enter_private_mount_namespace()
        startup_step = "mkdir_runtime"
        if progress_sink is not None:
            progress_sink({"event": "enter_namespace", "phase": "end"})
        os.mkdir(stage_root, 0o700)
        root = os.stat(stage_root, follow_symlinks=False)
        if not stat.S_ISDIR(root.st_mode) or stat.S_IMODE(root.st_mode) != 0o700:
            raise OSError("unsafe stage root")
        if daemon_authority is not None:
            runtime_dir = os.path.join(stage_root, ".runtime-bin")
            runtime_path = os.path.join(runtime_dir, "runc")
            os.mkdir(runtime_dir, 0o700)
            inherited_runtime_fd = authority_fds["runc"]
            runtime_fd = os.dup(inherited_runtime_fd)
            runtime_authority = os.fstat(runtime_fd)
            runtime_mode = stat.S_IMODE(runtime_authority.st_mode)
            if (
                not stat.S_ISREG(runtime_authority.st_mode)
                or runtime_authority.st_uid != daemon_authority.runc.owner_uid
                or runtime_mode != daemon_authority.runc.mode
                or not daemon_authority.runc.executable
                or runtime_authority.st_size <= 0
                or runtime_authority.st_size > _RUNTIME_AUTHORITY_LIMIT
                or _digest_fd_exact(runtime_fd) != daemon_authority.runc.digest
            ):
                raise OSError("admitted runtime descriptor identity is not exact")
            startup_step = "mount_runtime_tmpfs"
            runtime_tmpfs_size = _mount_runtime_tmpfs(
                runtime_dir, runtime_authority.st_size
            )
            startup_step = "copy_runtime"
            _copy_runtime_authority(
                runtime_fd,
                runtime_path,
                runtime_authority.st_size,
                runtime_mode,
            )
            startup_step = "seal_runtime_tmpfs"
            runtime_mount_id, runtime_readonly = _seal_runtime_tmpfs(
                runtime_dir, expected_size=runtime_tmpfs_size
            )
            (
                _,
                _,
                runtime_mount_source,
                runtime_mount_options,
                runtime_super_options,
            ) = _runtime_tmpfs_details(
                runtime_dir,
                expected_size=runtime_tmpfs_size,
                expected_readonly=True,
            )
            startup_step = "observe_runtime"
            mounted_runtime = os.stat(runtime_path, follow_symlinks=False)
            runtime_snapshot_fd = os.open(
                runtime_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            _validate_runtime_copy(
                runtime_path,
                runtime_dir,
                expected_device=mounted_runtime.st_dev,
                expected_inode=mounted_runtime.st_ino,
                expected_size=runtime_authority.st_size,
                expected_mode=runtime_mode,
                expected_digest=daemon_authority.runc.digest,
                expected_mount_id=runtime_mount_id,
                expected_tmpfs_size=runtime_tmpfs_size,
            )
        daemon_payload: dict[str, Any] | None = None
        if daemon_authority is not None:
            from .private_docker_daemon import PrivateDockerDaemonOwner

            startup_step = "owner_init"
            daemon_owner = PrivateDockerDaemonOwner(
                daemon_authority,
                pinned_fds=authority_fds,
                daemon_environment={"PATH": runtime_dir},
                runtime_registration_path=runtime_path,
                runtime_effective_fd=runtime_snapshot_fd,
                progress_sink=progress_sink,
                export_log_fds=True,
            )
            startup_step = "owner_start"
            binding = daemon_owner.start(readiness_timeout=1800.0)
            daemon_payload = {
                "binding": asdict(binding),
                "containerd": asdict(daemon_owner.containerd_observation),
                "config_child_fd": binding.config_fd,
            }
        executable_fd = os.open(
            "/proc/self/exe", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            executable = os.fstat(executable_fd)
            executable_digest = _digest_fd_exact(executable_fd)
        finally:
            os.close(executable_fd)
        observation = {
            "pid": os.getpid(),
            "starttime": _proc_starttime(os.getpid()),
            "mount_namespace": os.readlink("/proc/self/ns/mnt"),
            "mountinfo_digest": "sha256:" + hashlib.sha256(_mountinfo()).hexdigest(),
            "stage_root": stage_root,
            "stage_root_device": root.st_dev,
            "stage_root_inode": root.st_ino,
            "executable_digest": executable_digest,
            "executable_device": executable.st_dev,
            "executable_inode": executable.st_ino,
            "executable_ctime_ns": executable.st_ctime_ns,
            "executable_size": executable.st_size,
            "runtime_path": runtime_path,
            "runtime_device": (
                None if runtime_path is None else os.stat(runtime_path).st_dev
            ),
            "runtime_inode": (
                None if runtime_path is None else os.stat(runtime_path).st_ino
            ),
            "runtime_mount_id": runtime_mount_id,
            "runtime_readonly": None if runtime_path is None else True,
            "runtime_tmpfs_size": runtime_tmpfs_size,
            "runtime_mount_source": (
                None if runtime_path is None else runtime_mount_source
            ),
            "runtime_mount_options": (
                None if runtime_path is None else runtime_mount_options
            ),
            "runtime_super_options": (
                None if runtime_path is None else runtime_super_options
            ),
            "runtime_source_digest": (
                None if runtime_fd is None else daemon_authority.runc.digest
            ),
            "runtime_source_device": (
                None if runtime_fd is None else runtime_authority.st_dev
            ),
            "runtime_source_inode": (
                None if runtime_fd is None else runtime_authority.st_ino
            ),
            "runtime_source_ctime_ns": (
                None if runtime_fd is None else runtime_authority.st_ctime_ns
            ),
            "runtime_source_size": (
                None if runtime_fd is None else runtime_authority.st_size
            ),
            "runtime_source_mode": (None if runtime_fd is None else runtime_mode),
        }
        startup_fds = () if runtime_snapshot_fd is None else (runtime_snapshot_fd,)
        _send(
            sock,
            {
                "ok": True,
                "observation": observation,
                "daemon": daemon_payload,
                "sequence": 0,
                "token": token,
            },
            startup_fds,
        )
        while True:
            request, fds = _receive(sock)
            opened_stage_fd = -1
            stage_target: str | None = None
            response_fds: tuple[int, ...] = ()
            try:
                if (
                    request.get("token") != token
                    or request.get("sequence") != expected_sequence
                ):
                    raise ValueError("broker authorization or sequence mismatch")
                expected_sequence += 1
                operation = request.get("operation")
                if operation == "stage":
                    if len(fds) != 1:
                        raise ValueError("stage requires one authority descriptor")
                    descriptor = fds[0]
                    if progress_sink is not None:
                        progress_sink(
                            {
                                "event": "stage_descriptor_received",
                                "phase": "begin",
                                "fd": descriptor,
                            }
                        )
                    metadata = os.fstat(descriptor)
                    if progress_sink is not None:
                        progress_sink(
                            {
                                "event": "stage_descriptor_fstat",
                                "phase": "end",
                                "device": metadata.st_dev,
                                "inode": metadata.st_ino,
                            }
                        )
                    expected = (
                        request.get("expected_device"),
                        request.get("expected_inode"),
                    )
                    directory = request.get("directory")
                    readonly = request.get("readonly", directory is False)
                    if type(directory) is not bool or type(readonly) is not bool:
                        raise ValueError("invalid stage type")
                    if (metadata.st_dev, metadata.st_ino) != expected:
                        raise OSError("descriptor identity mismatch")
                    wanted_type = stat.S_IFDIR if directory else stat.S_IFREG
                    if stat.S_IFMT(metadata.st_mode) != wanted_type:
                        raise OSError("descriptor type mismatch")
                    authority_path = request.get("authority_path")
                    if (
                        type(authority_path) is not str
                        or not authority_path.startswith("/")
                        or authority_path.endswith(" (deleted)")
                        or os.path.normpath(authority_path) != authority_path
                    ):
                        raise OSError("stage authority path is invalid")
                    # SCM_RIGHTS authenticates the object. Reopen the same absolute
                    # path inside this private namespace so mount(2) receives a
                    # namespace-local vfsmount, then require the full identity to
                    # equal the transferred descriptor before using it.
                    open_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                    if directory:
                        open_flags |= os.O_DIRECTORY
                    opened_stage_fd = os.open(authority_path, open_flags)
                    opened_metadata = os.fstat(opened_stage_fd)
                    descriptor_identity = _stat_identity(metadata)
                    if _stat_identity(opened_metadata) != descriptor_identity:
                        raise OSError("transferred stage descriptor identity changed")
                    stage_id = secrets.token_hex(16)
                    stage_target = target = os.path.join(stage_root, stage_id)
                    if directory:
                        os.mkdir(target, 0o700)
                    else:
                        placeholder = os.open(
                            target,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                        )
                        os.close(placeholder)
                    if progress_sink is not None:
                        progress_sink({"event": "stage_bind", "phase": "begin"})
                    _bind(opened_stage_fd, target, readonly=readonly)
                    if progress_sink is not None:
                        progress_sink({"event": "stage_bind", "phase": "end"})
                    mounted = os.stat(target, follow_symlinks=False)
                    if any(
                        _stat_identity(current) != descriptor_identity
                        for current in (
                            os.fstat(descriptor),
                            os.fstat(opened_stage_fd),
                            mounted,
                            os.stat(authority_path, follow_symlinks=False),
                        )
                    ):
                        raise OSError("mounted stage authority identity changed")
                    receipt = StagedDockerDescriptorMount(
                        source_path=target,
                        source_device=mounted.st_dev,
                        source_inode=mounted.st_ino,
                        source_mode=stat.S_IFMT(mounted.st_mode),
                        descriptor_device=metadata.st_dev,
                        descriptor_inode=metadata.st_ino,
                    )
                    stage = _Stage(
                        receipt,
                        descriptor,
                        opened_stage_fd,
                        _mount_id(target),
                        readonly,
                        directory,
                        descriptor_identity,
                    )
                    if progress_sink is not None:
                        progress_sink({"event": "stage_validate", "phase": "begin"})
                    _validate_stage(stage)
                    if progress_sink is not None:
                        progress_sink({"event": "stage_validate", "phase": "end"})
                    stages[stage_id] = stage
                    fds = ()
                    opened_stage_fd = -1
                    result = _stage_document(stage)
                elif operation == "validate":
                    if len(fds) != 1:
                        raise ValueError("validate requires one authority descriptor")
                    stage_id = os.path.basename(request.get("source_path", ""))
                    stage = stages[stage_id]
                    if _stage_document(stage) != request.get("receipt"):
                        raise OSError("stage receipt changed")
                    _validate_stage(stage, fds[0])
                    result = _stage_document(stage)
                elif operation == "release":
                    if fds:
                        raise ValueError("release accepts no descriptors")
                    stage_id = os.path.basename(request.get("source_path", ""))
                    stage = stages[stage_id]
                    if _stage_document(stage) != request.get("receipt"):
                        raise OSError("stage receipt changed")
                    _validate_stage(stage)
                    _unmount(stage.receipt.source_path)
                    try:
                        remaining_mount_id = _mount_id(stage.receipt.source_path)
                    except OSError:
                        remaining_mount_id = None
                    if remaining_mount_id == stage.mount_id:
                        raise OSError("mount remained after unmount")
                    if stage.directory:
                        os.rmdir(stage.receipt.source_path)
                    else:
                        os.unlink(stage.receipt.source_path)
                    os.close(stage.mount_fd)
                    os.close(stage.fd)
                    del stages[stage_id]
                    result = {"absent": not os.path.lexists(stage.receipt.source_path)}
                elif operation == "execute":
                    input_size = request.get("input_size")
                    input_digest = request.get("input_digest")
                    has_input = input_size is not None or input_digest is not None
                    if (
                        len(fds) != (3 if has_input else 2)
                        or request.get("environment") != []
                        or request.get("cancellation_descriptor") is not True
                    ):
                        raise ValueError("execute authority is invalid")
                    if daemon_owner is not None:
                        _ = daemon_owner.binding
                        _ = daemon_owner.containerd_observation
                    if runtime_path is not None:
                        _validate_runtime_copy(
                            runtime_path,
                            runtime_dir,
                            expected_device=mounted_runtime.st_dev,
                            expected_inode=mounted_runtime.st_ino,
                            expected_size=runtime_authority.st_size,
                            expected_mode=runtime_mode,
                            expected_digest=daemon_authority.runc.digest,
                            expected_mount_id=runtime_mount_id,
                            expected_tmpfs_size=runtime_tmpfs_size,
                        )
                    executable_fd = fds[0]
                    metadata = os.fstat(executable_fd)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise OSError("executable is not regular")
                    pinned_docker = authority_fds.get("docker")
                    if pinned_docker is not None:
                        pinned_metadata = os.fstat(pinned_docker)
                        if (
                            metadata.st_dev,
                            metadata.st_ino,
                            metadata.st_ctime_ns,
                            metadata.st_size,
                        ) != (
                            pinned_metadata.st_dev,
                            pinned_metadata.st_ino,
                            pinned_metadata.st_ctime_ns,
                            pinned_metadata.st_size,
                        ):
                            raise OSError("executable descriptor is not approved")
                    digest = _digest_fd_exact(executable_fd)
                    if digest != request.get("digest"):
                        raise OSError("executable digest mismatch")
                    input_fd: int | None = None
                    if has_input:
                        if (
                            type(input_size) is not int
                            or not 1 <= input_size <= (1 << 53) - 1
                            or type(input_digest) is not str
                            or len(input_digest) != 71
                            or not input_digest.startswith("sha256:")
                        ):
                            raise ValueError("execute input authority is invalid")
                        input_fd = fds[1]
                        input_metadata = os.fstat(input_fd)
                        required_seals = (
                            fcntl.F_SEAL_SEAL
                            | fcntl.F_SEAL_SHRINK
                            | fcntl.F_SEAL_GROW
                            | fcntl.F_SEAL_WRITE
                        )
                        if (
                            not stat.S_ISREG(input_metadata.st_mode)
                            or input_metadata.st_size != input_size
                            or fcntl.fcntl(input_fd, fcntl.F_GET_SEALS) & required_seals
                            != required_seals
                            or _digest_fd_exact(input_fd) != input_digest
                        ):
                            raise OSError("execute input descriptor changed")
                    cancellation_fd = fds[-1]
                    argv = request.get("argv")
                    timeout_ms = request.get("timeout_ms")
                    output_limit = request.get("output_limit")
                    if (
                        type(argv) is not list
                        or not argv
                        or not all(
                            type(value) is str and "\x00" not in value for value in argv
                        )
                        or type(timeout_ms) is not int
                        or not 1 <= timeout_ms <= 600_000
                        or type(output_limit) is not int
                        or not 1 <= output_limit <= _MAX_OUTPUT
                    ):
                        raise ValueError("execute request is out of bounds")
                    if (
                        daemon_authority is not None
                        and argv[0] != daemon_authority.docker.path
                    ):
                        raise ValueError("Docker argv0 is not approved")
                    (
                        returncode,
                        response_fds,
                        output_sizes,
                        output_digests,
                        timed_out,
                        output_limited,
                    ) = _execute_bounded_descriptors(
                        argv,
                        executable_fd=executable_fd,
                        timeout_ms=timeout_ms,
                        output_limit=output_limit,
                        input_fd=input_fd,
                        cancellation_fd=cancellation_fd,
                    )
                    if runtime_path is not None:
                        _validate_runtime_copy(
                            runtime_path,
                            runtime_dir,
                            expected_device=mounted_runtime.st_dev,
                            expected_inode=mounted_runtime.st_ino,
                            expected_size=runtime_authority.st_size,
                            expected_mode=runtime_mode,
                            expected_digest=daemon_authority.runc.digest,
                            expected_mount_id=runtime_mount_id,
                            expected_tmpfs_size=runtime_tmpfs_size,
                        )
                    result = {
                        "returncode": returncode,
                        "stdout_size": output_sizes[0],
                        "stdout_digest": output_digests[0],
                        "stderr_size": output_sizes[1],
                        "stderr_digest": output_digests[1],
                        "timed_out": timed_out,
                        "output_limited": output_limited,
                    }
                elif operation == "shutdown":
                    if progress_sink is not None:
                        progress_sink({"event": "shutdown_request", "phase": "begin"})
                    if fds or stages:
                        raise OSError("broker has live stages")
                    daemon_logs: dict[str, Any] = {}
                    daemon_log_fds: tuple[int, ...] = ()
                    if daemon_owner is not None:
                        try:
                            daemon_owner.close()
                        except BaseException:
                            try:
                                failed_log_fds = daemon_owner.detach_log_fds()
                            except BaseException:
                                failed_log_fds = ()
                            for failed_log_fd in failed_log_fds:
                                os.close(failed_log_fd)
                            raise
                        if progress_sink is not None:
                            progress_sink({"event": "owner_close", "phase": "end"})
                        daemon_log_fds = daemon_owner.detach_log_fds()
                        daemon_logs = {
                            key: {
                                receipt_key: receipt_value
                                for receipt_key, receipt_value in asdict(value).items()
                                if receipt_key != "bytes_base64"
                            }
                            for key, value in daemon_owner.log_receipts.items()
                        }
                        daemon_owner = None
                    if progress_sink is not None:
                        progress_sink({"event": "runtime_unmount", "phase": "begin"})
                    _remove_runtime_bind(runtime_path, runtime_dir)
                    runtime_path = None
                    runtime_dir = None
                    if progress_sink is not None:
                        progress_sink({"event": "runtime_unmount", "phase": "end"})
                    try:
                        _send(
                            sock,
                            {
                                "ok": True,
                                "result": {"absent": True, "daemon_logs": daemon_logs},
                                "sequence": request["sequence"],
                                "token": token,
                            },
                            daemon_log_fds,
                        )
                    finally:
                        for log_fd in daemon_log_fds:
                            os.close(log_fd)
                    if progress_sink is not None:
                        progress_sink({"event": "shutdown_response", "phase": "sent"})
                    os.rmdir(stage_root)
                    if progress_sink is not None:
                        progress_sink({"event": "stage_root_removed", "phase": "end"})
                    if progress_sink is not None:
                        progress_sink({"event": "child_return", "phase": "begin"})
                    return
                else:
                    raise ValueError("unknown broker operation")
                try:
                    _send(
                        sock,
                        {
                            "ok": True,
                            "result": result,
                            "sequence": request["sequence"],
                            "token": token,
                        },
                        response_fds,
                    )
                finally:
                    for response_fd in response_fds:
                        os.close(response_fd)
                    response_fds = ()
            except BaseException as exc:
                cleanup_errors: list[BaseException] = []
                for response_fd in response_fds:
                    try:
                        os.close(response_fd)
                    except OSError as cleanup_exc:
                        cleanup_errors.append(cleanup_exc)
                if opened_stage_fd >= 0:
                    try:
                        os.close(opened_stage_fd)
                    except OSError as cleanup_exc:
                        cleanup_errors.append(cleanup_exc)
                if stage_target is not None and os.path.lexists(stage_target):
                    try:
                        _remove_stage_target(stage_target, directory=directory)
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(cleanup_exc)
                        failed_stage_targets.add((stage_target, directory))
                serialized = _error_detail_value(getattr(exc, "details", None))
                details = serialized if isinstance(serialized, dict) else {}
                if isinstance(exc, OSError):
                    details["errno"] = exc.errno
                operation_name = request.get("operation")
                details["operation"] = operation_name
                details["exception_leaves"] = _exception_leaves(
                    exc, operation=operation_name
                )
                if cleanup_errors:
                    details["cleanup_exception_leaves"] = [
                        leaf
                        for cleanup_exc in cleanup_errors
                        for leaf in _exception_leaves(
                            cleanup_exc, operation=operation_name
                        )
                    ]
                _send(
                    sock,
                    {
                        "error": type(exc).__name__,
                        "message": str(exc)[:_ERROR_MESSAGE_LIMIT],
                        "details": details,
                        "ok": False,
                        "sequence": request.get("sequence", -1),
                        "token": token,
                    },
                )
            finally:
                for fd in fds:
                    os.close(fd)
    except BaseException as exc:
        serialized = _error_detail_value(getattr(exc, "details", None))
        details = serialized if isinstance(serialized, dict) else {}
        details["exception_leaves"] = _exception_leaves(exc, operation="startup")
        details["startup_step"] = startup_step
        if isinstance(exc, OSError) and exc.errno is not None:
            details["errno"] = exc.errno
        details["mountinfo_digest"] = (
            "sha256:" + hashlib.sha256(_mountinfo()).hexdigest()
        )
        if runtime_fd is not None:
            source = os.fstat(runtime_fd)
            details["runtime_source"] = {
                "device": source.st_dev,
                "inode": source.st_ino,
                "mode": stat.S_IFMT(source.st_mode),
                "size": source.st_size,
            }
        if runtime_path is not None and os.path.lexists(runtime_path):
            target = os.lstat(runtime_path)
            details["runtime_target"] = {
                "device": target.st_dev,
                "inode": target.st_ino,
                "mode": stat.S_IFMT(target.st_mode),
                "size": target.st_size,
            }
            encoded_target = os.fsencode(runtime_path).replace(b" ", b"\\040")
            for mount_line in _mountinfo().splitlines():
                fields = mount_line.split()
                if len(fields) >= 10 and fields[4] == encoded_target:
                    details["runtime_mountinfo"] = mount_line.decode(
                        "utf-8", "surrogateescape"
                    )
                    break
        try:
            _send(
                sock,
                {
                    "error": type(exc).__name__,
                    "message": str(exc)[:1024],
                    "details": details,
                    "ok": False,
                    "sequence": 0,
                    "token": token,
                },
            )
        except BaseException:
            pass
    finally:
        cleanup_failed = False
        for stage in stages.values():
            try:
                _unmount(stage.receipt.source_path)
            except BaseException:
                cleanup_failed = True
            try:
                if stage.directory:
                    os.rmdir(stage.receipt.source_path)
                else:
                    os.unlink(stage.receipt.source_path)
            except FileNotFoundError:
                pass
            except BaseException:
                cleanup_failed = True
            try:
                os.close(stage.mount_fd)
            except OSError:
                cleanup_failed = True
            try:
                os.close(stage.fd)
            except OSError:
                cleanup_failed = True
        for failed_target, failed_directory in failed_stage_targets:
            try:
                _remove_stage_target(
                    failed_target,
                    directory=failed_directory,
                )
            except BaseException:
                cleanup_failed = True
        if daemon_owner is not None:
            try:
                daemon_owner.close()
            except BaseException:
                cleanup_failed = True
        try:
            _remove_runtime_bind(runtime_path, runtime_dir)
            runtime_path = None
            runtime_dir = None
        except BaseException:
            cleanup_failed = True
        if runtime_fd is not None:
            try:
                os.close(runtime_fd)
                runtime_fd = None
            except OSError:
                cleanup_failed = True
        if runtime_snapshot_fd is not None:
            try:
                os.close(runtime_snapshot_fd)
                runtime_snapshot_fd = None
            except OSError:
                cleanup_failed = True
        try:
            os.rmdir(stage_root)
        except FileNotFoundError:
            pass
        except BaseException:
            cleanup_failed = True
        if os.path.lexists(stage_root):
            cleanup_failed = True
        sock.close()
        if progress_sink is not None:
            progress_sink(
                {
                    "event": "child_finally_exit",
                    "phase": "end",
                    "cleanup_failed": cleanup_failed,
                }
            )
        if cleanup_failed:
            os._exit(70)


def _waitpid_bounded(pid: int, timeout: float) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reaped, status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return reaped, status
        time.sleep(0.05)
    raise TimeoutError("broker process did not exit before cleanup deadline")


def _wait_reaped(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if reaped == pid:
            return True
        time.sleep(0.05)
    return False


def _terminate_identity_group(
    observation: Mapping[str, Any], *, broker_pid: int, daemon: bool
) -> None:
    pid_key = "daemon_pid" if daemon else "pid"
    starttime_key = "daemon_starttime" if daemon else "starttime"
    device_key = "daemon_executable_device" if daemon else "executable_device"
    inode_key = "daemon_executable_inode" if daemon else "executable_inode"
    pid = observation.get(pid_key)
    starttime = observation.get(starttime_key)
    expected_device = observation.get(device_key)
    expected_inode = observation.get(inode_key)
    if (
        type(pid) is not int
        or pid <= 1
        or pid == broker_pid
        or type(starttime) is not str
        or type(expected_device) is not int
        or type(expected_inode) is not int
    ):
        raise OSError("startup descendant observation is incomplete")
    try:
        if _proc_starttime(pid) != starttime:
            raise OSError("startup descendant identity changed")
    except FileNotFoundError:
        return
    executable = os.stat(f"/proc/{pid}/exe")
    if (executable.st_dev, executable.st_ino) != (
        expected_device,
        expected_inode,
    ):
        raise OSError("startup descendant executable identity changed")
    current = pid
    descendant = False
    for _ in range(64):
        status = Path(f"/proc/{current}/status").read_text(encoding="ascii")
        fields = {
            key: value.strip()
            for key, value in (
                line.split(":", 1) for line in status.splitlines() if ":" in line
            )
        }
        uid_fields = fields.get("Uid", "").split()
        if not uid_fields or int(uid_fields[0]) != os.geteuid():
            raise OSError("startup descendant uid identity changed")
        parent = int(fields.get("PPid", "0"))
        if parent == broker_pid:
            descendant = True
            break
        if parent <= 1 or parent == current:
            break
        current = parent
    if not descendant:
        raise OSError("startup process is not a broker descendant")
    process_group = os.getpgid(pid)
    if process_group != pid or process_group == os.getpgrp():
        raise OSError("startup descendant process group is not isolated")
    for sig, delay in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(process_group, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if not os.path.lexists(f"/proc/{pid}"):
                return
            time.sleep(0.05)
    if os.path.lexists(f"/proc/{pid}"):
        raise OSError("startup descendant process group survived cleanup")


def _cleanup_failed_broker_process(
    sock: socket.socket,
    *,
    pid: int,
    token: str,
    response: Mapping[str, Any],
) -> None:
    graceful_error: BaseException | None = None
    if (
        response.get("ok") is True
        and response.get("token") == token
        and response.get("sequence") == 0
    ):
        try:
            sock.settimeout(30.0)
            _send(
                sock,
                {
                    "operation": "shutdown",
                    "sequence": 1,
                    "token": token,
                },
            )
            shutdown, returned_fds = _receive(sock)
            try:
                if (
                    shutdown.get("ok") is not True
                    or shutdown.get("token") != token
                    or shutdown.get("sequence") != 1
                ):
                    raise OSError("broker graceful startup cleanup was rejected")
            finally:
                for fd in returned_fds:
                    os.close(fd)
        except BaseException as exc:
            graceful_error = exc
    sock.close()
    if _wait_reaped(pid, 30.0):
        return
    daemon = response.get("daemon")
    fallback_errors: list[BaseException] = []
    if type(daemon) is dict:
        binding = daemon.get("binding")
        containerd = daemon.get("containerd")
        for observation in (binding, containerd):
            if type(observation) is not dict:
                continue
            try:
                _terminate_identity_group(
                    observation,
                    broker_pid=pid,
                    daemon=observation is binding,
                )
            except BaseException as exc:
                fallback_errors.append(exc)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if not _wait_reaped(pid, 2.0):
        fallback_errors.append(OSError("failed broker process could not be reaped"))
    if fallback_errors:
        errors: list[BaseException] = []
        if graceful_error is not None:
            errors.append(graceful_error)
        errors.extend(fallback_errors)
        raise BaseExceptionGroup("broker startup process cleanup failed", errors)


class MountNamespaceBroker:
    """Supervised descriptor mount and execution broker in a private mount namespace."""

    def __init__(
        self,
        stage_root: str | Path,
        *,
        daemon_authority: Any | None = None,
        progress_fd: int | None = None,
        journal_root_fd: int | None = None,
        journal_root_path: str | Path | None = None,
        journal_authenticator: Any | None = None,
    ) -> None:
        if os.name != "posix" or not Path("/proc/self/mountinfo").exists():
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "Linux procfs is required"
            )
        if threading.active_count() != 1:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker must start before threads"
            )
        self._stage_root = os.path.abspath(os.fspath(stage_root))
        if os.path.lexists(self._stage_root):
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker stage root already exists"
            )
        parent = os.path.dirname(self._stage_root)
        parent_metadata = os.stat(parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_IMODE(parent_metadata.st_mode) & 0o022
        ):
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker stage parent is not sealed"
            )
        self._daemon_root_fd = -1
        self._journal_root_fd = -1
        self._journal_root_path: str | None = None
        self._journal_authenticator = journal_authenticator
        if (journal_root_fd is None) != (journal_root_path is None):
            raise MountNamespaceBrokerError(
                "runtime_unsupported",
                "journal root requires its pinned descriptor and path",
            )
        if journal_root_fd is not None and journal_root_path is not None:
            journal_path = os.path.abspath(os.fspath(journal_root_path))
            try:
                supplied_journal = os.stat(journal_path, follow_symlinks=False)
                opened_journal = os.fstat(journal_root_fd)
            except OSError as exc:
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "journal root authority is unavailable",
                ) from exc
            if (
                not stat.S_ISDIR(supplied_journal.st_mode)
                or not stat.S_ISDIR(opened_journal.st_mode)
                or (supplied_journal.st_dev, supplied_journal.st_ino)
                != (opened_journal.st_dev, opened_journal.st_ino)
                or supplied_journal.st_uid != os.geteuid()
                or stat.S_IMODE(supplied_journal.st_mode) & 0o077
                or stat.S_IMODE(opened_journal.st_mode)
                != stat.S_IMODE(supplied_journal.st_mode)
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "journal root authority is not sealed"
                )
            if (
                journal_authenticator is None
                or not callable(getattr(journal_authenticator, "sign", None))
                or not callable(getattr(journal_authenticator, "verify", None))
                or type(getattr(journal_authenticator, "key_id", None)) is not str
                or getattr(journal_authenticator, "algorithm", None) != "hmac-sha256-v1"
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "journal authentication authority is unavailable",
                )
            self._journal_root_path = journal_path
        if daemon_authority is not None:
            if (
                self._stage_root != daemon_authority.mount_stage_root
                or parent != daemon_authority.daemon_root
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker daemon root authority is not exact",
                )
            self._daemon_root_fd = os.open(
                parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            held_parent = os.fstat(self._daemon_root_fd)
            if (held_parent.st_dev, held_parent.st_ino) != (
                parent_metadata.st_dev,
                parent_metadata.st_ino,
            ):
                os.close(self._daemon_root_fd)
                self._daemon_root_fd = -1
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker daemon root identity changed",
                )
        if progress_fd is not None:
            progress_metadata = os.fstat(progress_fd)
            if (
                not stat.S_ISREG(progress_metadata.st_mode)
                or stat.S_IMODE(progress_metadata.st_mode) != 0o600
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker progress authority is unsafe"
                )
        self._authority_fds = (
            _authority_fds(daemon_authority) if daemon_authority is not None else {}
        )
        self._daemon_authority = daemon_authority
        parent_sock, child_sock = socket.socketpair(
            socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC
        )
        token = secrets.token_hex(32)
        parent_pid = os.getpid()
        pid = os.fork()
        if pid == 0:
            parent_sock.close()
            _child_loop(
                child_sock,
                token,
                self._stage_root,
                daemon_authority,
                self._authority_fds,
                parent_pid,
                progress_fd,
            )
            os._exit(0)
        child_sock.close()
        self._progress_sink = (
            _ProgressJournal(progress_fd, writer="broker-parent")
            if progress_fd is not None
            else None
        )
        self._socket = parent_sock
        self._pid = pid
        self._token = token
        self._sequence = 1
        self._lock = threading.Lock()
        self._closed = False
        self._resources_closed = False
        self._cleanup_verified = False
        self._reaped = False
        self._wait_status: int | None = None
        self._journal_bindings: dict[str, dict[str, Any]] = {}
        self._global_journal_lease_id: str | None = None
        self._stage_leases: dict[str, str] = {}
        response: Mapping[str, Any] = {}
        try:
            response, fds = _receive(self._socket)
            if (
                not response.get("ok")
                or response.get("token") != token
                or response.get("sequence") != 0
            ):
                for fd in fds:
                    os.close(fd)
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker namespace startup failed",
                    details=response,
                )
            runtime_snapshot_fd = _accept_startup_snapshot_fd(
                fds, required=daemon_authority is not None
            )
            if runtime_snapshot_fd is not None:
                self._authority_fds["runtime-snapshot"] = runtime_snapshot_fd
            value = response["observation"]
            self.observation = BrokerObservation(**value)
            if daemon_authority is not None:
                admitted_runtime = os.fstat(self._authority_fds["runc"])
                if (
                    admitted_runtime.st_dev,
                    admitted_runtime.st_ino,
                    admitted_runtime.st_ctime_ns,
                    admitted_runtime.st_size,
                    stat.S_IMODE(admitted_runtime.st_mode),
                    _digest_fd_exact(self._authority_fds["runc"]),
                ) != (
                    self.observation.runtime_source_device,
                    self.observation.runtime_source_inode,
                    self.observation.runtime_source_ctime_ns,
                    self.observation.runtime_source_size,
                    self.observation.runtime_source_mode,
                    self.observation.runtime_source_digest,
                ):
                    raise MountNamespaceBrokerError(
                        "runtime_unsupported",
                        "broker admitted runtime provenance changed",
                    )
            if (
                self.observation.pid != pid
                or self.observation.mount_namespace == os.readlink("/proc/self/ns/mnt")
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker mount namespace is not isolated"
                )
            executable_fd = os.open(
                f"/proc/{pid}/exe", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                executable = os.fstat(executable_fd)
                executable_digest = _digest_fd_exact(executable_fd)
            finally:
                os.close(executable_fd)
            if (
                executable.st_dev,
                executable.st_ino,
                executable.st_ctime_ns,
                executable.st_size,
                executable_digest,
            ) != (
                self.observation.executable_device,
                self.observation.executable_inode,
                self.observation.executable_ctime_ns,
                self.observation.executable_size,
                self.observation.executable_digest,
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker executable observation changed"
                )
            self.daemon_binding: PrivateDockerDaemonBinding | None = None
            self.containerd_observation: Mapping[str, Any] | None = None
            daemon = response.get("daemon")
            if daemon_authority is not None:
                if type(daemon) is not dict:
                    raise MountNamespaceBrokerError(
                        "runtime_unsupported",
                        "broker daemon startup observation is absent",
                    )
                runtime_snapshot_fd = self._authority_fds["runtime-snapshot"]
                runtime_snapshot = os.fstat(runtime_snapshot_fd)
                binding_payload = daemon["binding"]
                if (
                    type(binding_payload) is not dict
                    or not stat.S_ISREG(runtime_snapshot.st_mode)
                    or (
                        runtime_snapshot.st_dev,
                        runtime_snapshot.st_ino,
                        runtime_snapshot.st_ctime_ns,
                        runtime_snapshot.st_size,
                    )
                    != (
                        binding_payload.get("runtime_device"),
                        binding_payload.get("runtime_inode"),
                        binding_payload.get("runtime_ctime_ns"),
                        binding_payload.get("runtime_size"),
                    )
                    or _digest_fd_exact(runtime_snapshot_fd)
                    != binding_payload.get("runtime_digest")
                    or runtime_snapshot.st_dev != self.observation.runtime_device
                    or runtime_snapshot.st_ino != self.observation.runtime_inode
                ):
                    raise MountNamespaceBrokerError(
                        "runtime_unsupported",
                        "broker runtime snapshot descriptor is not exact",
                    )
                config_fd = os.open(
                    f"/proc/{pid}/fd/{daemon['config_child_fd']}",
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
                )
                self._authority_fds["config"] = config_fd
                self.daemon_binding = _construct_parent_binding(
                    daemon["binding"],
                    config_fd=config_fd,
                    runtime_fd=self._authority_fds["runtime-snapshot"],
                    parent_pid=os.getpid(),
                )
                self.containerd_observation = daemon["containerd"]
            if journal_root_fd is not None:
                self._journal_root_fd = os.dup(journal_root_fd)
        except BaseException as primary:
            cleanup_errors: list[BaseException] = []
            try:
                _cleanup_failed_broker_process(
                    self._socket,
                    pid=pid,
                    token=token,
                    response=response,
                )
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
            for authority_fd in self._authority_fds.values():
                try:
                    os.close(authority_fd)
                except OSError:
                    pass
            self._authority_fds.clear()
            try:
                self._cleanup_dead_placeholders()
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
            try:
                self._remove_daemon_root()
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
            if self._daemon_root_fd >= 0:
                os.close(self._daemon_root_fd)
                self._daemon_root_fd = -1
            if self._journal_root_fd >= 0:
                os.close(self._journal_root_fd)
                self._journal_root_fd = -1
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "broker startup and cleanup failed",
                    [primary, *cleanup_errors],
                ) from None
            raise

    @staticmethod
    def _journal_process(
        pid: int,
        starttime: str,
        *,
        executable_device: int,
        executable_inode: int,
        executable_ctime_ns: int,
        executable_size: int,
        executable_digest: str,
    ) -> dict[str, Any]:
        namespace = os.stat(f"/proc/{pid}/ns/mnt", follow_symlinks=False)
        return {
            "pid": pid,
            "starttime": starttime,
            "pgid": os.getpgid(pid),
            "executable_device": executable_device,
            "executable_inode": executable_inode,
            "executable_ctime_ns": executable_ctime_ns,
            "executable_size": executable_size,
            "executable_digest": executable_digest,
            "namespace_device": namespace.st_dev,
            "namespace_inode": namespace.st_ino,
        }

    @staticmethod
    def _journal_path(
        path: str,
        *,
        device: int,
        inode: int,
        mode: int,
        digest: str,
    ) -> dict[str, Any]:
        parent_path = os.path.dirname(path)
        parent = os.stat(parent_path, follow_symlinks=False)
        return {
            "path": path,
            "device": device,
            "inode": inode,
            "mode": mode,
            "digest": digest,
            "parent_path": parent_path,
            "parent_device": parent.st_dev,
            "parent_inode": parent.st_ino,
        }

    def _journal_base(
        self,
        *,
        lease_id: str,
        workspace_id: str,
        epoch: int,
        role: str,
        plan_digest: str,
        owner_token: str,
    ) -> dict[str, Any]:
        if self._journal_root_fd < 0:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "secure supervisor journal is unavailable"
            )
        observation = self.observation
        broker_exe = self._journal_process(
            observation.pid,
            observation.starttime,
            executable_device=observation.executable_device,
            executable_inode=observation.executable_inode,
            executable_ctime_ns=observation.executable_ctime_ns,
            executable_size=observation.executable_size,
            executable_digest=observation.executable_digest,
        )
        daemon = containerd = runtime = config = daemon_root = None
        if self.daemon_binding is not None:
            binding = self.daemon_binding
            daemon = self._journal_process(
                binding.daemon_pid,
                binding.daemon_starttime,
                executable_device=binding.daemon_executable_device,
                executable_inode=binding.daemon_executable_inode,
                executable_ctime_ns=binding.daemon_executable_ctime_ns,
                executable_size=binding.daemon_executable_size,
                executable_digest=binding.daemon_executable_digest,
            )
            config_metadata = os.fstat(binding.config_fd)
            config_path = self._daemon_authority.config_path
            config_path_metadata = os.stat(config_path, follow_symlinks=False)
            if (
                config_path_metadata.st_dev,
                config_path_metadata.st_ino,
            ) != (
                config_metadata.st_dev,
                config_metadata.st_ino,
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "daemon config path authority changed",
                )
            daemon_root_path = self._daemon_authority.daemon_root
            daemon_root_metadata = os.stat(
                daemon_root_path,
                follow_symlinks=False,
            )
            daemon_root_digest = _journal_digest(
                _canonical(
                    {
                        "device": daemon_root_metadata.st_dev,
                        "inode": daemon_root_metadata.st_ino,
                        "mode": stat.S_IMODE(daemon_root_metadata.st_mode),
                    }
                )
            )
            daemon_root = self._journal_path(
                daemon_root_path,
                device=daemon_root_metadata.st_dev,
                inode=daemon_root_metadata.st_ino,
                mode=stat.S_IMODE(daemon_root_metadata.st_mode),
                digest=daemon_root_digest,
            )
            config = self._journal_path(
                config_path,
                device=config_metadata.st_dev,
                inode=config_metadata.st_ino,
                mode=stat.S_IMODE(config_metadata.st_mode),
                digest=binding.daemon_config_digest,
            )
            runtime_metadata = os.fstat(binding.runtime_fd)
            runtime = self._journal_path(
                binding.runtime_registered_path,
                device=runtime_metadata.st_dev,
                inode=runtime_metadata.st_ino,
                mode=stat.S_IMODE(runtime_metadata.st_mode),
                digest=binding.runtime_digest,
            )
            if self.containerd_observation is not None:
                child = self.containerd_observation
                containerd = self._journal_process(
                    child.pid,
                    child.starttime,
                    executable_device=child.executable_device,
                    executable_inode=child.executable_inode,
                    executable_ctime_ns=child.executable_ctime_ns,
                    executable_size=child.executable_size,
                    executable_digest=child.executable_digest,
                )
        stage_root = os.stat(observation.stage_root, follow_symlinks=False)
        stage_digest = _journal_digest(
            _canonical(
                {
                    "device": stage_root.st_dev,
                    "inode": stage_root.st_ino,
                    "mode": stat.S_IMODE(stage_root.st_mode),
                }
            )
        )
        generation = f"{lease_id}:{workspace_id}:{epoch}:{role}:{plan_digest}"
        generation_digest = _journal_digest(generation.encode("utf-8"))
        return {
            "schema_version": SUPERVISOR_JOURNAL_SCHEMA_VERSION,
            "state": "ACTIVE",
            "generation": generation,
            "generation_digest": generation_digest,
            "owner_token_digest": _journal_digest(owner_token.encode("utf-8")),
            "lease_id": lease_id,
            "workspace_id": workspace_id,
            "epoch": epoch,
            "role": role,
            "plan_digest": plan_digest,
            "broker": broker_exe,
            "daemon": daemon,
            "containerd": containerd,
            "runtime": runtime,
            "config": config,
            "daemon_root": daemon_root,
            "stage_root": self._journal_path(
                observation.stage_root,
                device=stage_root.st_dev,
                inode=stage_root.st_ino,
                mode=stat.S_IMODE(stage_root.st_mode),
                digest=stage_digest,
            ),
            "stages": [],
            "container": {"id": None, "name": "", "labels": {}},
            "proof": {
                "container_absence": False,
                "stages_absence": False,
                "daemon_absence": False,
                "containerd_absence": False,
                "runtime_absence": False,
                "config_absence": False,
                "root_absence": False,
            },
        }

    def _journal_update(self, lease_id: str, **changes: Any) -> None:
        if self._journal_root_fd < 0:
            return
        payload = self._journal_bindings.get(lease_id)
        if payload is None:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "lease journal binding is absent"
            )
        payload.update(changes)
        if not _validate_journal_payload(payload):
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "lease journal update is not exact"
            )
        _atomic_journal_write(
            self._journal_root_fd,
            lease_id,
            payload,
            authenticator=self._journal_authenticator,
        )

    def record_lease_binding(
        self,
        *,
        lease_id: str,
        workspace_id: str,
        epoch: int,
        role: str,
        plan_digest: str,
        owner_token: str,
    ) -> None:
        payload = self._journal_base(
            lease_id=lease_id,
            workspace_id=workspace_id,
            epoch=epoch,
            role=role,
            plan_digest=plan_digest,
            owner_token=owner_token,
        )
        if not _validate_journal_payload(payload):
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "lease journal binding is not exact"
            )
        _atomic_journal_write(
            self._journal_root_fd,
            lease_id,
            payload,
            authenticator=self._journal_authenticator,
        )
        self._journal_bindings[lease_id] = payload

    def record_container_identity(
        self,
        lease_id: str,
        *,
        container_id: str,
        name: str,
        labels: Mapping[str, str],
    ) -> None:
        if (
            type(container_id) is not str
            or len(container_id) != 64
            or any(char not in "0123456789abcdef" for char in container_id)
        ):
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "container identity is not exact"
            )
        self._journal_update(
            lease_id,
            container={
                "id": container_id,
                "name": name,
                "labels": dict(labels),
            },
        )

    def record_cleanup_receipt(
        self,
        lease_id: str,
        *,
        proof: Mapping[str, bool],
        state: str = "FINAL",
    ) -> None:
        if set(proof) != {
            "container_absence",
            "stages_absence",
            "daemon_absence",
            "containerd_absence",
            "runtime_absence",
            "config_absence",
            "root_absence",
        } or any(type(value) is not bool for value in proof.values()):
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "cleanup proof is not exact"
            )
        if state not in {"ACTIVE", "FINAL", "QUARANTINED"}:
            raise ValueError("cleanup receipt state is invalid")
        self._journal_update(lease_id, state=state, proof=dict(proof))
        if state == "ACTIVE" and proof["container_absence"] and proof["stages_absence"]:
            if self._global_journal_lease_id is None:
                self._global_journal_lease_id = lease_id
            elif self._global_journal_lease_id != lease_id:
                self.unlink_supervisor_receipt(lease_id)
                self._journal_bindings.pop(lease_id, None)

    def read_supervisor_receipt(self, lease_id: str) -> Mapping[str, Any]:
        if self._journal_root_fd < 0:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "secure supervisor journal is unavailable"
            )
        return MappingProxyType(
            _read_journal(
                self._journal_root_fd,
                lease_id,
                authenticator=self._journal_authenticator,
            )
        )

    def unlink_supervisor_receipt(self, lease_id: str) -> None:
        if self._journal_root_fd < 0:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "secure supervisor journal is unavailable"
            )
        try:
            os.unlink(_journal_name(lease_id), dir_fd=self._journal_root_fd)
        except FileNotFoundError:
            return
        os.fsync(self._journal_root_fd)

    @property
    def docker_invocation(self) -> ExecutableInvocation:
        if self._daemon_authority is None:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker has no Docker authority"
            )
        fd = self._authority_fds["docker"]
        return ExecutableInvocation(
            argv0=self._daemon_authority.docker.path,
            executable_fd=fd,
            executable_descriptor_path=f"/proc/{os.getpid()}/fd/{fd}",
            digest=self._daemon_authority.docker.digest,
        )

    @property
    def docker_cli_executor(self) -> "BrokerDockerCliExecutor":
        return BrokerDockerCliExecutor(self)

    def execute_docker(
        self,
        argv_tail: Sequence[str],
        *,
        timeout_ms: int = 30_000,
        output_limit: int = _MAX_OUTPUT,
    ) -> DockerCommandResult:
        binding = self.daemon_binding
        if binding is None:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker has no live private Docker daemon"
            )
        if not argv_tail or any(
            type(item) is not str or "\x00" in item for item in argv_tail
        ):
            raise ValueError("Docker argv tail is invalid")
        executable = self.docker_invocation
        host_prefix = ("--host", "unix://" + binding.socket_path)
        argv = (executable.argv0, *host_prefix, *tuple(argv_tail))
        returned_fds: tuple[int, ...] = ()
        cancellation_read_fd = cancellation_write_fd = -1
        try:
            cancellation_read_fd, cancellation_write_fd = os.pipe()
            os.set_inheritable(cancellation_read_fd, False)
            os.set_inheritable(cancellation_write_fd, False)
            os.set_blocking(cancellation_read_fd, False)
            os.set_blocking(cancellation_write_fd, False)
            result, returned_fds = self._call(
                "execute",
                {
                    "argv": list(argv),
                    "digest": executable.digest,
                    "environment": [],
                    "timeout_ms": timeout_ms,
                    "output_limit": output_limit,
                    "cancellation_descriptor": True,
                },
                (executable.executable_fd, cancellation_read_fd),
                expected_return_fds=2,
            )
            stdout_size = result.get("stdout_size")
            stderr_size = result.get("stderr_size")
            if (
                type(stdout_size) is not int
                or type(stderr_size) is not int
                or stdout_size < 0
                or stderr_size < 0
                or stdout_size + stderr_size > output_limit
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker output descriptor bounds are invalid",
                )
            stdout = _read_sealed_payload_fd(
                returned_fds[0],
                expected_size=stdout_size,
                expected_digest=result.get("stdout_digest"),
                limit=output_limit,
            )
            stderr = _read_sealed_payload_fd(
                returned_fds[1],
                expected_size=stderr_size,
                expected_digest=result.get("stderr_digest"),
                limit=output_limit - stdout_size,
            )
        finally:
            if cancellation_read_fd >= 0:
                os.close(cancellation_read_fd)
            if cancellation_write_fd >= 0:
                os.close(cancellation_write_fd)
            for returned_fd in returned_fds:
                os.close(returned_fd)
        return DockerCommandResult(
            argv,
            result["returncode"],
            stdout,
            stderr,
            timed_out=result["timed_out"],
            output_limited=result["output_limited"],
        )

    def _cleanup_dead_placeholders(self) -> None:
        for staged_path, receipt in tuple(getattr(self, "_receipts", {}).items()):
            try:
                if receipt["source_mode"] == stat.S_IFDIR:
                    os.rmdir(staged_path)
                else:
                    os.unlink(staged_path)
            except FileNotFoundError:
                pass
        runtime_dir = os.path.join(self._stage_root, ".runtime-bin")
        runtime_path = os.path.join(runtime_dir, "runc")
        try:
            os.unlink(runtime_path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(runtime_dir)
        except FileNotFoundError:
            pass
        try:
            residual_entries = tuple(os.scandir(self._stage_root))
        except FileNotFoundError:
            residual_entries = ()
        for entry in residual_entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    os.rmdir(entry.path)
                else:
                    os.unlink(entry.path)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(self._stage_root)
        except FileNotFoundError:
            pass
        if os.path.lexists(self._stage_root):
            raise MountNamespaceBrokerError(
                "runtime_unsupported",
                "crashed broker left quarantined staging residue",
            )

    def _remove_daemon_root(self) -> None:
        if self._daemon_authority is None or getattr(self, "_daemon_root_fd", -1) < 0:
            return
        root = self._daemon_authority.daemon_root
        try:
            current = os.stat(root, follow_symlinks=False)
        except FileNotFoundError:
            return
        held = os.fstat(self._daemon_root_fd)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            held.st_dev,
            held.st_ino,
        ):
            raise MountNamespaceBrokerError(
                "runtime_unsupported",
                "broker daemon root cleanup identity drifted",
            )
        os.rmdir(root)

    @property
    def pid(self) -> int:
        return self._pid

    def _call(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        fds: Sequence[int] = (),
        *,
        expected_return_fds: int = 0,
    ) -> Any:
        with self._lock:
            if self._closed:
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker is closed"
                )
            reaped, wait_status = os.waitpid(self._pid, os.WNOHANG)
            if reaped != 0:
                self._wait_status = wait_status
                self._reaped = True
                self._closed = True
                self._cleanup_dead_placeholders()
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker crashed; state is quarantined",
                )
            sequence = self._sequence
            self._sequence += 1
            request = {
                "operation": operation,
                "sequence": sequence,
                "token": self._token,
                **arguments,
            }
            send_details: dict[str, Any] = {
                "operation": operation,
                "socket_fd": self._socket.fileno(),
                "authority_fds": list(fds),
            }
            try:
                socket_metadata = os.fstat(self._socket.fileno())
                send_details["socket_mode"] = stat.S_IFMT(socket_metadata.st_mode)
                send_details["authority"] = [
                    {
                        "fd": fd,
                        "device": os.fstat(fd).st_dev,
                        "inode": os.fstat(fd).st_ino,
                        "mode": stat.S_IFMT(os.fstat(fd).st_mode),
                    }
                    for fd in fds
                ]
                self.record_progress("rpc_send", "begin", send_details)
                _send(self._socket, request, fds)
                self.record_progress("rpc_send", "end", send_details)
            except OSError as exc:
                send_details["errno"] = exc.errno
                self.record_progress("rpc_send", "error", send_details)
                raise
            try:
                response, returned_fds = _receive(self._socket)
            except BaseException as exc:
                self._closed = True
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker RPC failed; state is quarantined",
                ) from exc
            if (
                response.get("token") != self._token
                or response.get("sequence") != sequence
            ):
                for fd in returned_fds:
                    os.close(fd)
                self._closed = True
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker response authentication failed",
                )
            if not response.get("ok"):
                for fd in returned_fds:
                    os.close(fd)
                raise MountNamespaceBrokerError(
                    "workspace_authority_mismatch",
                    "broker rejected the operation",
                    details={
                        "error": response.get("error"),
                        "message": response.get("message"),
                        "details": response.get("details"),
                    },
                )
            if len(returned_fds) != expected_return_fds:
                for fd in returned_fds:
                    os.close(fd)
                self._closed = True
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker response descriptor count is invalid",
                )
            result = response.get("result")
            if type(result) is not dict:
                for fd in returned_fds:
                    os.close(fd)
                raise MountNamespaceBrokerError(
                    "runtime_unsupported", "broker result is malformed"
                )
            if expected_return_fds:
                return result, tuple(returned_fds)
            return result

    async def stage(
        self,
        descriptor: int,
        *,
        expected_device: int,
        expected_inode: int,
        directory: bool,
        lease_id: str,
        destination: str,
    ) -> StagedDockerDescriptorMount:
        if type(lease_id) is not str or not lease_id or len(lease_id) > 256:
            raise MountNamespaceBrokerError(
                "workspace_authority_mismatch", "lease id is invalid"
            )
        authority_path = _descriptor_source_path(descriptor)
        result = self._call(
            "stage",
            {
                "destination": destination,
                "directory": directory,
                "expected_device": expected_device,
                "expected_inode": expected_inode,
                "lease_id": lease_id,
                "readonly": not directory,
                "authority_path": authority_path,
            },
            (descriptor,),
        )
        staged = StagedDockerDescriptorMount(
            **{
                key: result[key]
                for key in (
                    "source_path",
                    "source_device",
                    "source_inode",
                    "source_mode",
                    "descriptor_device",
                    "descriptor_inode",
                )
            }
        )
        self._stage_leases[staged.source_path] = lease_id
        self.record_stage_receipt(staged, result)
        return staged

    @staticmethod
    def _receipt(
        staged: StagedDockerDescriptorMount, mount_id: int, readonly: bool
    ) -> dict[str, Any]:
        return {
            "source_path": staged.source_path,
            "source_device": staged.source_device,
            "source_inode": staged.source_inode,
            "source_mode": staged.source_mode,
            "descriptor_device": staged.descriptor_device,
            "descriptor_inode": staged.descriptor_inode,
            "mount_id": mount_id,
            "readonly": readonly,
        }

    async def validate(
        self, staged: StagedDockerDescriptorMount, descriptor: int
    ) -> None:
        staged.validate_descriptor(descriptor)
        # The broker is authoritative for mount ids; recover the immutable values with a probe.
        result = self._call(
            "validate",
            {"source_path": staged.source_path, "receipt": self._known_receipt(staged)},
            (descriptor,),
        )
        if any(
            result.get(key) != value
            for key, value in self._known_receipt(staged).items()
        ):
            raise MountNamespaceBrokerError(
                "workspace_authority_mismatch", "broker stage observation changed"
            )

    def _known_receipt(self, staged: StagedDockerDescriptorMount) -> dict[str, Any]:
        receipt = getattr(self, "_receipts", {}).get(staged.source_path)
        if receipt is None:
            raise MountNamespaceBrokerError(
                "workspace_authority_mismatch", "stage is not owned by this broker"
            )
        return dict(receipt)

    async def release(self, staged: StagedDockerDescriptorMount) -> None:
        receipt = self._known_receipt(staged)
        result = self._call(
            "release", {"source_path": staged.source_path, "receipt": receipt}
        )
        if result != {"absent": True}:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "broker did not prove stage absence"
            )
        self._receipts.pop(staged.source_path)
        lease_id = self._stage_leases.pop(staged.source_path, None)
        if lease_id is not None and lease_id in self._journal_bindings:
            payload = self._journal_bindings[lease_id]
            self._journal_update(
                lease_id,
                stages=[
                    item
                    for item in payload["stages"]
                    if item.get("source_path") != staged.source_path
                ],
            )

    def record_stage_receipt(
        self, staged: StagedDockerDescriptorMount, result: Mapping[str, Any]
    ) -> None:
        if not hasattr(self, "_receipts"):
            self._receipts: dict[str, dict[str, Any]] = {}
        self._receipts[staged.source_path] = dict(result)
        lease_id = self._stage_leases.get(staged.source_path)
        if lease_id is not None and lease_id in self._journal_bindings:
            payload = self._journal_bindings[lease_id]
            parent_path = os.path.dirname(staged.source_path)
            parent = os.stat(parent_path, follow_symlinks=False)
            journal_result = {
                **dict(result),
                "source_parent_path": parent_path,
                "source_parent_device": parent.st_dev,
                "source_parent_inode": parent.st_ino,
            }
            self._journal_update(
                lease_id,
                stages=[*payload["stages"], journal_result],
            )

    def record_progress(
        self, event: str, phase: str, details: Mapping[str, Any] | None = None
    ) -> None:
        if self._progress_sink is not None:
            self._progress_sink(
                {
                    "event": event,
                    "phase": phase,
                    "details": dict(details or {}),
                }
            )

    def close(self) -> None:
        if getattr(self, "_cleanup_verified", False) or (
            getattr(self, "_resources_closed", False)
            and not getattr(self, "_journal_bindings", None)
        ):
            return
        errors: list[BaseException] = []
        status = self._wait_status
        graceful = False
        receipts = getattr(self, "_receipts", {})
        descendants_terminated = False

        def terminate_daemon_descendants() -> None:
            nonlocal descendants_terminated
            if descendants_terminated or self._daemon_authority is None:
                return
            descendants_terminated = True
            binding = self.daemon_binding
            containerd = self.containerd_observation
            observations: tuple[tuple[Mapping[str, Any] | None, bool], ...] = (
                (None if binding is None else asdict(binding), True),
                (containerd, False),
            )
            for observation, daemon in observations:
                if observation is None:
                    errors.append(
                        OSError("broker daemon descendant observation is absent")
                    )
                    continue
                try:
                    _terminate_identity_group(
                        observation,
                        broker_pid=self._pid,
                        daemon=daemon,
                    )
                except BaseException as exc:
                    errors.append(exc)

        if receipts and not self._closed:
            errors.append(
                MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker cleanup refused with live stages",
                )
            )
        elif not self._closed:
            expected_log_fds = 2 if self._daemon_authority is not None else 0
            try:
                self.record_progress("broker_close_rpc", "begin")
                shutdown_result = self._call(
                    "shutdown",
                    {},
                    expected_return_fds=expected_log_fds,
                )
                self._closed = True
                graceful = True
                if expected_log_fds:
                    shutdown, log_fds = shutdown_result
                    log_receipts = _consume_log_fds(
                        shutdown.get("daemon_logs", {}),
                        log_fds,
                        limit=self._daemon_authority.log_limit_bytes,
                    )
                else:
                    log_receipts = {}
                self.daemon_log_receipts = MappingProxyType(log_receipts)
                self.record_progress("broker_close_rpc", "end")
            except BaseException as exc:
                errors.append(exc)
        else:
            graceful = True

        if not self._reaped:
            try:
                self.record_progress("broker_waitpid", "begin")
                if graceful:
                    reaped, status = _waitpid_bounded(self._pid, 30.0)
                else:
                    reaped, status = os.waitpid(self._pid, os.WNOHANG)
                    if reaped == 0:
                        terminate_daemon_descendants()
                        os.kill(self._pid, signal.SIGKILL)
                        reaped, status = _waitpid_bounded(self._pid, 30.0)
                self.record_progress(
                    "broker_waitpid",
                    "end",
                    {"reaped": reaped, "status": status},
                )
                self._reaped = reaped == self._pid
                self._wait_status = status
            except BaseException as exc:
                errors.append(exc)
                terminate_daemon_descendants()
                try:
                    os.kill(self._pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as cleanup_exc:
                    errors.append(cleanup_exc)
                try:
                    reaped, status = _waitpid_bounded(self._pid, 30.0)
                    self._reaped = reaped == self._pid
                    self._wait_status = status
                except BaseException as cleanup_exc:
                    errors.append(cleanup_exc)
        if self._reaped:
            self._closed = True

        try:
            self._cleanup_dead_placeholders()
        except BaseException as exc:
            errors.append(exc)
        try:
            self._socket.close()
        except BaseException as exc:
            errors.append(exc)
        try:
            self._remove_daemon_root()
        except BaseException as exc:
            errors.append(exc)

        residual_paths = [self._stage_root]
        if self._daemon_authority is not None:
            authority = self._daemon_authority
            residual_paths.extend(
                (
                    authority.socket_path,
                    authority.containerd_socket_path,
                    getattr(
                        authority,
                        "containerd_ttrpc_socket_path",
                        authority.containerd_socket_path + ".ttrpc",
                    ),
                    authority.pid_file,
                    authority.config_path,
                    authority.exec_root,
                    authority.data_root,
                    authority.containerd_root,
                    authority.containerd_state,
                    authority.log_root,
                )
            )
            if getattr(self, "_daemon_root_fd", -1) >= 0:
                residual_paths.append(authority.daemon_root)
        try:
            absent = all(_journal_path_name_absent(path) for path in residual_paths)
        except OSError as exc:
            errors.append(exc)
            absent = False

        failed_authority_fds: dict[str, int] = {}
        for name, fd in self._authority_fds.items():
            try:
                os.close(fd)
            except OSError as exc:
                failed_authority_fds[name] = fd
                errors.append(exc)
        self._authority_fds = failed_authority_fds
        if getattr(self, "_daemon_root_fd", -1) >= 0:
            try:
                root_absent = self._daemon_authority is None or (
                    _journal_path_name_absent(self._daemon_authority.daemon_root)
                )
            except OSError as exc:
                errors.append(exc)
                root_absent = False
            if root_absent:
                try:
                    os.close(self._daemon_root_fd)
                except OSError as exc:
                    errors.append(exc)
                else:
                    self._daemon_root_fd = -1
        self._resources_closed = (
            not self._authority_fds and getattr(self, "_daemon_root_fd", -1) < 0
        )

        status = self._wait_status
        if not self._reaped or status != 0 or not absent:
            errors.append(
                MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker final absence proof failed",
                )
            )
        if not errors and self._resources_closed and self._journal_root_fd >= 0:
            try:
                final_proof = {
                    "container_absence": True,
                    "stages_absence": True,
                    "daemon_absence": True,
                    "containerd_absence": True,
                    "runtime_absence": True,
                    "config_absence": True,
                    "root_absence": True,
                }
                for lease_id in tuple(getattr(self, "_journal_bindings", ())):
                    self.record_cleanup_receipt(
                        lease_id,
                        proof=final_proof,
                        state="FINAL",
                    )
                _prune_final_journals(
                    self._journal_root_fd,
                    authenticator=self._journal_authenticator,
                )
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup("broker cleanup failed", errors)
        if getattr(self, "_journal_root_fd", -1) >= 0:
            os.close(self._journal_root_fd)
            self._journal_root_fd = -1
        self._cleanup_verified = True


class BrokerDockerCliExecutor:
    def __init__(self, broker: MountNamespaceBroker) -> None:
        self._broker = broker

    async def execute(
        self,
        executable: ExecutableInvocation,
        argv_tail: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int,
        environment: tuple[tuple[str, str], ...],
        input_bytes: bytes = b"",
    ) -> DockerCommandResult:
        if environment:
            raise MountNamespaceBrokerError(
                "runtime_unsupported", "ambient Docker environment is forbidden"
            )
        request = {
            "argv": [executable.argv0, *argv_tail],
            "digest": executable.digest,
            "environment": [],
            "timeout_ms": timeout_ms,
            "output_limit": output_limit,
            "cancellation_descriptor": True,
        }
        payload_fd = -1
        returned_fds: tuple[int, ...] = ()
        cancellation_read_fd = cancellation_write_fd = -1
        try:
            cancellation_read_fd, cancellation_write_fd = os.pipe()
            os.set_inheritable(cancellation_read_fd, False)
            os.set_inheritable(cancellation_write_fd, False)
            os.set_blocking(cancellation_read_fd, False)
            os.set_blocking(cancellation_write_fd, False)
            descriptors = [executable.executable_fd]
            if input_bytes:
                payload_fd = _sealed_payload_fd(input_bytes)
                request["input_size"] = len(input_bytes)
                request["input_digest"] = (
                    "sha256:" + hashlib.sha256(input_bytes).hexdigest()
                )
                descriptors.append(payload_fd)
            descriptors.append(cancellation_read_fd)
            duplicated: list[int] = []
            try:
                for descriptor in descriptors:
                    duplicated.append(os.dup(descriptor))
            except BaseException:
                for descriptor in duplicated:
                    os.close(descriptor)
                raise
            worker_descriptors = tuple(duplicated)

            def broker_call() -> Any:
                try:
                    return self._broker._call(
                        "execute",
                        request,
                        worker_descriptors,
                        expected_return_fds=2,
                    )
                finally:
                    for descriptor in worker_descriptors:
                        os.close(descriptor)

            call_task = asyncio.create_task(asyncio.to_thread(broker_call))
            try:
                result, returned_fds = await asyncio.shield(call_task)
            except asyncio.CancelledError:
                def close_abandoned_result(task: asyncio.Task[Any]) -> None:
                    try:
                        _result, abandoned_fds = task.result()
                    except BaseException:
                        return
                    for descriptor in abandoned_fds:
                        os.close(descriptor)

                try:
                    os.write(cancellation_write_fd, b"\x01")
                except OSError:
                    pass
                try:
                    _result, abandoned_fds = await asyncio.shield(call_task)
                except asyncio.CancelledError:
                    call_task.add_done_callback(close_abandoned_result)
                except BaseException:
                    pass
                else:
                    for descriptor in abandoned_fds:
                        os.close(descriptor)
                raise
            stdout_size = result.get("stdout_size")
            stderr_size = result.get("stderr_size")
            if (
                type(stdout_size) is not int
                or type(stderr_size) is not int
                or stdout_size < 0
                or stderr_size < 0
                or stdout_size + stderr_size > output_limit
            ):
                raise MountNamespaceBrokerError(
                    "runtime_unsupported",
                    "broker output descriptor bounds are invalid",
                )
            stdout = _read_sealed_payload_fd(
                returned_fds[0],
                expected_size=stdout_size,
                expected_digest=result.get("stdout_digest"),
                limit=output_limit,
            )
            stderr = _read_sealed_payload_fd(
                returned_fds[1],
                expected_size=stderr_size,
                expected_digest=result.get("stderr_digest"),
                limit=output_limit - stdout_size,
            )
        finally:
            if payload_fd >= 0:
                os.close(payload_fd)
            if cancellation_read_fd >= 0:
                os.close(cancellation_read_fd)
            if cancellation_write_fd >= 0:
                os.close(cancellation_write_fd)
            for returned_fd in returned_fds:
                os.close(returned_fd)
        return DockerCommandResult(
            (executable.argv0, *tuple(argv_tail)),
            result["returncode"],
            stdout,
            stderr,
            timed_out=result["timed_out"],
            output_limited=result["output_limited"],
        )


__all__ = [
    "BrokerDockerCliExecutor",
    "BrokerObservation",
    "MountNamespaceBroker",
    "MountNamespaceBrokerError",
]
