from __future__ import annotations

import asyncio
import array
import ctypes
import errno
import hashlib
import json
import os
import signal
import socket
import stat
import struct
import re
import select
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

from breadboard_engine.compilation.contracts import canonical_json_bytes

from .mount_namespace_broker import (
    _CLONE_NEWNS,
    _MS_BIND,
    _MS_NODEV,
    _MS_NOEXEC,
    _MS_NOSUID,
    _MNT_DETACH,
    _MS_NOATIME,
    _MS_NODIRATIME,
    _MS_RDONLY,
    _MS_RELATIME,
    _MS_REMOUNT,
    _bind,
    _enter_private_mount_namespace,
    _libc_call,
    _mountinfo,
)


UTC = timezone.utc
_CLONE_NEWUSER = 0x10000000
_CLONE_NEWPID = 0x20000000
_CLONE_NEWNET = 0x40000000
_RECEIPT_SCHEMA = "bb.containment-receipt.v2"
_MAX_FRAME = 256 * 1024
_SYS_OPEN_TREE = 428
_SYS_MOVE_MOUNT = 429
_OPEN_TREE_CLONE = 1
_SYS_MOUNT_SETATTR = 442
_MOUNT_ATTR_RDONLY = 1
_OPEN_TREE_CLOEXEC = 0x80000
_MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
_AT_RECURSIVE = 0x8000
_MAX_FDS = 64


class RuntimeContainment(str, Enum):
    ATTESTED = "attested"
    UNCONFINED_TEST_ONLY = "unconfined_test_only"


class ContainmentReceiptError(ValueError):
    code = "containment_receipt_invalid"



class EnvelopeMountError(OSError):
    """The lease mount view cannot be proven read-only outside its writable roots."""


class _MountAttr(ctypes.Structure):
    _fields_ = [
        ("attr_set", ctypes.c_uint64),
        ("attr_clr", ctypes.c_uint64),
        ("propagation", ctypes.c_uint64),
        ("userns_fd", ctypes.c_uint64),
    ]


def _mount_paths(raw: bytes) -> list[tuple[str, set[bytes]]]:
    mounts = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 7 or not fields[0].isdigit():
            raise EnvelopeMountError(errno.EINVAL, "mountinfo is malformed")
        path = re.sub(
            rb"\\([0-7]{3})",
            lambda match: bytes((int(match.group(1), 8),)),
            fields[4],
        ).decode("utf-8", "surrogateescape")
        mounts.append((path, set(fields[5].split(b","))))
    if not mounts:
        raise EnvelopeMountError(errno.EINVAL, "mountinfo is empty")
    return mounts


def _remount_tree_readonly(target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    attr = _MountAttr(attr_set=_MOUNT_ATTR_RDONLY)
    if syscall(
        ctypes.c_long(_SYS_MOUNT_SETATTR), ctypes.c_int(-100),
        ctypes.c_char_p(os.fsencode(target)), ctypes.c_uint(_AT_RECURSIVE),
        ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)),
    ) == 0:
        return
    error = ctypes.get_errno()
    if error not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
        raise EnvelopeMountError(error, f"recursive readonly remount failed: {target}")
    mounts = _mount_paths(_mountinfo())
    seen: set[str] = set()
    descendants = sorted(
        ((path, options) for path, options in mounts
         if path == target or path.startswith(target.rstrip("/") + "/")),
        key=lambda entry: entry[0].count("/"), reverse=True,
    )
    for path, options in descendants:
        if path in seen:
            raise EnvelopeMountError(errno.EINVAL, f"stacked mount cannot be verified: {path}")
        seen.add(path)
        preserved = 0
        for option, flag in (
            (b"nosuid", _MS_NOSUID), (b"nodev", _MS_NODEV),
            (b"noexec", _MS_NOEXEC), (b"noatime", _MS_NOATIME),
            (b"nodiratime", _MS_NODIRATIME), (b"relatime", _MS_RELATIME),
        ):
            if option in options:
                preserved |= flag
        if b"ro" not in options:
            try:
                _libc_call(
                    "mount", ctypes.c_char_p(None), ctypes.c_char_p(os.fsencode(path)),
                    ctypes.c_char_p(None),
                    ctypes.c_ulong(_MS_REMOUNT | _MS_BIND | _MS_RDONLY | preserved),
                    ctypes.c_char_p(None),
                )
            except OSError as exc:
                raise EnvelopeMountError(
                    exc.errno, f"readonly remount failed: {path}"
                ) from exc
    after = _mount_paths(_mountinfo())
    if len(after) != len(mounts) or any(
        b"ro" not in options for path, options in after
        if path == target or path.startswith(target.rstrip("/") + "/")
    ):
        raise EnvelopeMountError(errno.EROFS, f"readonly mount verification failed: {target}")


class ReceiptAuthenticator(Protocol):
    key_id: str
    algorithm: str

    def sign(self, unsigned_canonical_bytes: bytes) -> bytes: ...

    def verify(self, unsigned_canonical_bytes: bytes, signature: bytes) -> bool: ...


@dataclass(frozen=True, slots=True)
class AdmittedLeaseRecord:
    lease_id: str
    runtime_id: str
    receipt_bytes: bytes
    receipt_signature: bytes


class AdmittedLeaseLedger(Protocol):
    def lookup(self, lease_id: str) -> AdmittedLeaseRecord | None: ...


def _ns_inode(link: str) -> int:
    left, right = link.rsplit("[", 1)
    if not right.endswith("]") or not right[:-1].isdigit():
        raise ContainmentReceiptError("namespace inode is malformed")
    return int(right[:-1])


def _namespace_inodes() -> dict[str, int]:
    return {
        name: _ns_inode(os.readlink(f"/proc/self/ns/{name}"))
        for name in ("pid", "mnt", "user", "net")
    }


def _digest_mountinfo(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _receipt_unsigned(payload: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(payload))


@dataclass(frozen=True, slots=True)
class WritableMount:
    path: str
    fstype: str
    size_bytes: int | None
    source: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "fstype": self.fstype,
            "size_bytes": self.size_bytes,
            "source": self.source,
        }


@contextmanager
def _receipt_guard() -> Iterator[None]:
    try:
        yield
    except ContainmentReceiptError:
        raise
    except Exception as exc:
        raise ContainmentReceiptError("containment receipt is malformed") from exc


def _plain_receipt_json(value: Any, depth: int = 0) -> Any:
    if depth > 32:
        raise ContainmentReceiptError("containment receipt nesting is too deep")
    if type(value) is dict:
        for key in value:
            if type(key) is not str:
                raise ContainmentReceiptError("containment receipt keys must be strings")
        return {key: _plain_receipt_json(item, depth + 1) for key, item in value.items()}
    if type(value) is list:
        return [_plain_receipt_json(item, depth + 1) for item in value]
    if type(value) in (str, int, bool) or value is None:
        return value
    raise ContainmentReceiptError("containment receipt value is not plain JSON")


@dataclass(frozen=True, slots=True)
class ContainmentReceipt:
    schema_version: str
    lease_id: str
    runtime_id: str
    mode: str
    pid_namespace_inode: int
    mount_namespace_inode: int
    user_namespace_inode: int
    network_namespace_inode: int
    mountinfo_sha256: str
    writable_mounts: tuple[WritableMount, ...]
    created_at: str
    key_id: str
    algorithm: str
    signature: bytes
    outcome: Mapping[str, bool] | None = None

    def unsigned_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": self.schema_version,
            "lease_id": self.lease_id,
            "runtime_id": self.runtime_id,
            "mode": self.mode,
            "namespaces": {
                "pid": self.pid_namespace_inode,
                "mnt": self.mount_namespace_inode,
                "user": self.user_namespace_inode,
                "net": self.network_namespace_inode,
            },
            "mountinfo_sha256": self.mountinfo_sha256,
            "writable_mounts": [mount.to_mapping() for mount in self.writable_mounts],
            "created_at": self.created_at,
            "key_id": self.key_id,
            "algorithm": self.algorithm,
        }
        if self.outcome is not None:
            value["outcome"] = dict(self.outcome)
        return value

    def to_mapping(self) -> dict[str, Any]:
        value = self.unsigned_mapping()
        value["signature"] = self.signature.hex()
        return value

    def canonical_bytes(self) -> bytes:
        return _receipt_unsigned(self.unsigned_mapping())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | ContainmentReceipt) -> "ContainmentReceipt":
        with _receipt_guard():
            if type(value) is ContainmentReceipt:
                value = value.to_mapping()
            value = _plain_receipt_json(value)
            if type(value) is not dict:
                raise ContainmentReceiptError("containment receipt is not an object")
            required = {
                "schema", "lease_id", "runtime_id", "mode", "namespaces",
                "mountinfo_sha256", "writable_mounts", "created_at",
                "key_id", "algorithm", "signature",
            }
            if set(value) not in (required, required | {"outcome"}):
                raise ContainmentReceiptError("containment receipt keys are invalid")
            namespaces = value["namespaces"]
            if type(namespaces) is not dict or set(namespaces) != {"pid", "mnt", "user", "net"}:
                raise ContainmentReceiptError("containment receipt namespace keys are invalid")
            raw_mounts = value["writable_mounts"]
            mount_keys = {"path", "fstype", "size_bytes", "source"}
            if type(raw_mounts) is not list or any(
                type(mount) is not dict or set(mount) != mount_keys for mount in raw_mounts
            ):
                raise ContainmentReceiptError("containment receipt writable mount keys are invalid")
            outcome_raw = value["outcome"] if "outcome" in value else None
            if "outcome" in value and (
                type(outcome_raw) is not dict
                or set(outcome_raw) != {"pid1_reaped", "all_dead"}
            ):
                raise ContainmentReceiptError("containment receipt outcome keys are invalid")
            receipt = cls(
                schema_version=value["schema"],
                lease_id=value["lease_id"],
                runtime_id=value["runtime_id"],
                mode=value["mode"],
                pid_namespace_inode=namespaces["pid"],
                mount_namespace_inode=namespaces["mnt"],
                user_namespace_inode=namespaces["user"],
                network_namespace_inode=namespaces["net"],
                mountinfo_sha256=value["mountinfo_sha256"],
                writable_mounts=tuple(WritableMount(**mount) for mount in raw_mounts),
                created_at=value["created_at"],
                key_id=value["key_id"],
                algorithm=value["algorithm"],
                signature=bytes.fromhex(value["signature"]),
                outcome=None if outcome_raw is None else {
                    "pid1_reaped": outcome_raw["pid1_reaped"],
                    "all_dead": outcome_raw["all_dead"],
                },
            )
            _validate_receipt_shape(receipt)
            return receipt


def _validate_receipt_shape(receipt: ContainmentReceipt) -> None:
    if type(receipt) is not ContainmentReceipt:
        raise ContainmentReceiptError("containment receipt fields are invalid")
    inodes = (
        receipt.pid_namespace_inode,
        receipt.mount_namespace_inode,
        receipt.user_namespace_inode,
        receipt.network_namespace_inode,
    )
    if (
        any(type(value) is not str for value in (
            receipt.schema_version, receipt.lease_id, receipt.runtime_id,
            receipt.mode, receipt.mountinfo_sha256, receipt.created_at,
            receipt.key_id, receipt.algorithm,
        ))
        or any(type(inode) is not int for inode in inodes)
        or type(receipt.writable_mounts) is not tuple
        or any(
            type(mount) is not WritableMount
            or type(mount.path) is not str
            or type(mount.fstype) is not str
            or (mount.size_bytes is not None and type(mount.size_bytes) is not int)
            or type(mount.source) is not str
            for mount in receipt.writable_mounts
        )
        or type(receipt.signature) is not bytes
        or (
            receipt.outcome is not None
            and (
                not isinstance(receipt.outcome, Mapping)
                or any(type(key) is not str or type(item) is not bool
                       for key, item in receipt.outcome.items())
            )
        )
    ):
        raise ContainmentReceiptError("containment receipt fields are invalid")
    if (
        receipt.schema_version != _RECEIPT_SCHEMA
        or not receipt.lease_id
        or not receipt.runtime_id
        or receipt.mode not in {"privileged", "userns"}
        or any(inode <= 0 for inode in inodes)
        or not receipt.mountinfo_sha256.startswith("sha256:")
        or not receipt.writable_mounts
        or any(
            not mount.path.startswith("/")
            or (
                mount.source == "lease_tmpfs"
                and (mount.fstype != "tmpfs" or mount.size_bytes is None or mount.size_bytes <= 0)
            )
            or (
                mount.source == "workspace_bind"
                and (mount.fstype != "bind" or mount.size_bytes is not None)
            )
            or mount.source not in {"lease_tmpfs", "workspace_bind"}
            for mount in receipt.writable_mounts
        )
        or tuple(sorted(receipt.writable_mounts, key=lambda mount: mount.path)) != receipt.writable_mounts
        or len({mount.path for mount in receipt.writable_mounts}) != len(receipt.writable_mounts)
        or not receipt.key_id
        or receipt.algorithm != "hmac-sha256-v1"
        or len(receipt.signature) != 32
        or (receipt.outcome is not None and set(receipt.outcome) != {"pid1_reaped", "all_dead"})
    ):
        raise ContainmentReceiptError("containment receipt fields are invalid")
    try:
        datetime.fromisoformat(receipt.created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContainmentReceiptError("containment receipt timestamp is invalid") from exc


def mint_containment_receipt(
    *,
    lease_id: str,
    runtime_id: str,
    mode: str,
    writable_mounts: Sequence[WritableMount],
    authenticator: ReceiptAuthenticator,
    now: datetime | None = None,
) -> ContainmentReceipt:
    namespaces = _namespace_inodes()
    receipt = ContainmentReceipt(
        schema_version=_RECEIPT_SCHEMA,
        lease_id=lease_id,
        runtime_id=runtime_id,
        mode=mode,
        pid_namespace_inode=namespaces["pid"],
        mount_namespace_inode=namespaces["mnt"],
        user_namespace_inode=namespaces["user"],
        network_namespace_inode=namespaces["net"],
        mountinfo_sha256=_digest_mountinfo(_mountinfo()),
        writable_mounts=tuple(sorted(writable_mounts, key=lambda mount: mount.path)),
        created_at=(now or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        key_id=authenticator.key_id,
        algorithm=authenticator.algorithm,
        signature=b"\0" * 32,
    )
    _validate_receipt_shape(receipt)
    return ContainmentReceipt(
        schema_version=receipt.schema_version,
        lease_id=receipt.lease_id,
        runtime_id=receipt.runtime_id,
        mode=receipt.mode,
        pid_namespace_inode=receipt.pid_namespace_inode,
        mount_namespace_inode=receipt.mount_namespace_inode,
        user_namespace_inode=receipt.user_namespace_inode,
        network_namespace_inode=receipt.network_namespace_inode,
        mountinfo_sha256=receipt.mountinfo_sha256,
        writable_mounts=receipt.writable_mounts,
        created_at=receipt.created_at,
        key_id=receipt.key_id,
        algorithm=receipt.algorithm,
        signature=authenticator.sign(receipt.canonical_bytes()),
        outcome=receipt.outcome,
    )


def add_teardown_outcome(
    receipt: ContainmentReceipt,
    *,
    pid1_reaped: bool,
    all_dead: bool,
    authenticator: ReceiptAuthenticator,
) -> ContainmentReceipt:
    receipt = ContainmentReceipt.from_mapping(receipt)
    if receipt.outcome is not None:
        raise ContainmentReceiptError("teardown outcome already exists")
    unsigned = ContainmentReceipt(
        schema_version=receipt.schema_version,
        lease_id=receipt.lease_id,
        runtime_id=receipt.runtime_id,
        mode=receipt.mode,
        pid_namespace_inode=receipt.pid_namespace_inode,
        mount_namespace_inode=receipt.mount_namespace_inode,
        user_namespace_inode=receipt.user_namespace_inode,
        network_namespace_inode=receipt.network_namespace_inode,
        mountinfo_sha256=receipt.mountinfo_sha256,
        writable_mounts=receipt.writable_mounts,
        created_at=receipt.created_at,
        key_id=receipt.key_id,
        algorithm=receipt.algorithm,
        signature=b"\0" * 32,
        outcome={"pid1_reaped": pid1_reaped, "all_dead": all_dead},
    )
    return ContainmentReceipt(
        schema_version=unsigned.schema_version,
        lease_id=unsigned.lease_id,
        runtime_id=unsigned.runtime_id,
        mode=unsigned.mode,
        pid_namespace_inode=unsigned.pid_namespace_inode,
        mount_namespace_inode=unsigned.mount_namespace_inode,
        user_namespace_inode=unsigned.user_namespace_inode,
        network_namespace_inode=unsigned.network_namespace_inode,
        mountinfo_sha256=unsigned.mountinfo_sha256,
        writable_mounts=unsigned.writable_mounts,
        created_at=unsigned.created_at,
        key_id=unsigned.key_id,
        algorithm=unsigned.algorithm,
        signature=authenticator.sign(unsigned.canonical_bytes()),
        outcome=unsigned.outcome,
    )


def verify_containment_receipt(
    receipt: ContainmentReceipt | Mapping[str, Any],
    *,
    lease_id: str,
    runtime_id: str,
    authenticator: ReceiptAuthenticator,
    require_teardown: bool = False,
) -> ContainmentReceipt:
    with _receipt_guard():
        parsed = ContainmentReceipt.from_mapping(receipt)
        if parsed.lease_id != lease_id:
            raise ContainmentReceiptError("containment receipt lease mismatch")
        if parsed.runtime_id != runtime_id:
            raise ContainmentReceiptError("containment receipt runtime mismatch")
        if parsed.key_id != authenticator.key_id or parsed.algorithm != authenticator.algorithm:
            raise ContainmentReceiptError("containment receipt signer mismatch")
        if not authenticator.verify(parsed.canonical_bytes(), parsed.signature):
            raise ContainmentReceiptError("containment receipt signature mismatch")
        if require_teardown and parsed.outcome is None:
            raise ContainmentReceiptError("containment teardown receipt is missing")
        if parsed.outcome is not None and not all(parsed.outcome.values()):
            raise ContainmentReceiptError("containment teardown outcome is incomplete")
        return parsed


def _send_frame(sock: socket.socket, payload: Mapping[str, Any], fds: Sequence[int] = ()) -> None:
    raw = canonical_json_bytes(dict(payload))
    if len(raw) > _MAX_FRAME:
        raise OSError("envelope frame is oversized")
    anc: list[tuple[int, int, bytes]] = []
    if fds:
        if len(fds) > _MAX_FDS:
            raise OSError("envelope descriptor count is oversized")
        anc.append((socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes()))
    sock.sendmsg([struct.pack("!I", len(raw)) + raw], anc)


def _recv_frame(sock: socket.socket) -> tuple[dict[str, Any], list[int]]:
    raw, ancdata, _flags, _address = sock.recvmsg(_MAX_FRAME + 4, socket.CMSG_SPACE(_MAX_FDS * array.array("i").itemsize))
    if not raw:
        raise EOFError
    if len(raw) < 4:
        raise OSError("envelope frame is truncated")
    size = struct.unpack("!I", raw[:4])[0]
    if size > _MAX_FRAME or len(raw) != size + 4:
        raise OSError("envelope frame size is invalid")
    fds: list[int] = []
    for level, kind, data in ancdata:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(data[: len(data) - (len(data) % values.itemsize)])
            fds.extend(values.tolist())
    value = json.loads(raw[4:].decode("utf-8"))
    if type(value) is not dict:
        raise OSError("envelope message is not an object")
    return value, fds


def _send_credentials(sock: socket.socket, payload: Mapping[str, Any]) -> None:
    _send_frame(sock, payload)
def _execveat_fd(fd: int, argv: Sequence[str], environment: Mapping[str, str]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.execveat
    function.restype = ctypes.c_int
    encoded_argv = [os.fsencode(item) for item in argv]
    argv_array = (ctypes.c_char_p * (len(encoded_argv) + 1))(
        *encoded_argv, None
    )
    encoded_environment = [
        os.fsencode(f"{key}={value}") for key, value in environment.items()
    ]
    environment_array = (ctypes.c_char_p * (len(encoded_environment) + 1))(
        *encoded_environment, None
    )
    result = function(
        ctypes.c_int(fd),
        ctypes.c_char_p(b""),
        argv_array,
        environment_array,
        ctypes.c_int(0x1000),
    )
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))


def _unshare(flags: int) -> None:
    _libc_call("unshare", ctypes.c_int(flags))


def _write_map(path: str, value: str) -> None:
    with open(path, "w", encoding="ascii") as stream:
        stream.write(value)


def _enter_user_namespace() -> None:
    _write_map("/proc/self/setgroups", "deny\n")
    uid = os.geteuid()
    gid = os.getegid()
    _write_map("/proc/self/uid_map", f"0 {uid} 1\n")
    _write_map("/proc/self/gid_map", f"0 {gid} 1\n")


def _mount_tmpfs(target: str, size_bytes: int, *, mode: int = 0o1777) -> None:
    if type(size_bytes) is not int or size_bytes <= 0:
        raise ValueError("tmpfs size is invalid")
    _libc_call(
        "mount",
        ctypes.c_char_p(b"tmpfs"),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_char_p(b"tmpfs"),
        ctypes.c_ulong(_MS_NOSUID | _MS_NODEV),
        ctypes.c_char_p(f"size={size_bytes},mode={mode:o}".encode("ascii")),
    )


def _mount_proc() -> None:
    try:
        _libc_call("umount2", ctypes.c_char_p(b"/proc"), ctypes.c_int(_MNT_DETACH))
    except OSError:
        pass
    _libc_call(
        "mount",
        ctypes.c_char_p(b"proc"),
        ctypes.c_char_p(b"/proc"),
        ctypes.c_char_p(b"proc"),
        ctypes.c_ulong(_MS_NOSUID | _MS_NODEV | _MS_NOEXEC),
        ctypes.c_char_p(None),
    )


def _tmpfs_budgets(size_bytes: int) -> tuple[int, int]:
    if type(size_bytes) is not int or size_bytes < 8192:
        raise EnvelopeMountError(errno.EINVAL, "lease tmpfs budget is too small")
    tmp_size = size_bytes // 2
    return tmp_size, size_bytes - tmp_size


def _verify_mount_view(
    workspace: str, scratch: str, tmpfs_size_bytes: int,
) -> tuple[str, tuple[WritableMount, ...]]:
    workspace = os.path.abspath(workspace)
    scratch = os.path.abspath(scratch)
    tmp_size, scratch_size = _tmpfs_budgets(tmpfs_size_bytes)
    raw = _mountinfo()
    sizes = {"/tmp": tmp_size, scratch: scratch_size}
    roots = {workspace, scratch, "/tmp"}
    seen_roots: set[str] = set()
    for line, (path, options) in zip(raw.splitlines(), _mount_paths(raw), strict=True):
        fields = line.split()
        if path in roots:
            if path in seen_roots or b"rw" not in options:
                raise EnvelopeMountError(errno.EROFS, f"envelope writable mount is invalid: {path}")
            seen_roots.add(path)
            if path in sizes:
                separator = fields.index(b"-") if b"-" in fields else -1
                if separator < 0 or fields[separator + 1] != b"tmpfs":
                    raise EnvelopeMountError(errno.EINVAL, f"lease tmpfs mount is absent: {path}")
                info = os.statvfs(path)
                actual_size = info.f_blocks * info.f_frsize
                if actual_size <= 0 or actual_size > sizes[path] + info.f_frsize:
                    raise EnvelopeMountError(errno.EINVAL, f"lease tmpfs size is invalid: {path}")
                if b"nosuid" not in options or b"nodev" not in options:
                    raise EnvelopeMountError(errno.EINVAL, f"lease tmpfs safety flags are absent: {path}")
        elif b"ro" not in options:
            raise EnvelopeMountError(errno.EROFS, f"envelope inherited mount is writable: {path}")
    if seen_roots != roots:
        raise EnvelopeMountError(errno.ENOENT, f"envelope writable mount is absent: {sorted(roots - seen_roots)}")
    mounts = tuple(sorted((
        WritableMount(path, "tmpfs", size, "lease_tmpfs")
        for path, size in sizes.items()
    ), key=lambda mount: mount.path))
    return _digest_mountinfo(raw), tuple(sorted(
        (*mounts, WritableMount(workspace, "bind", None, "workspace_bind")),
        key=lambda mount: mount.path,
    ))

def _open_tree(path: str) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    tree_fd = syscall(
        ctypes.c_long(_SYS_OPEN_TREE),
        ctypes.c_int(-100),
        ctypes.c_char_p(os.fsencode(path)),
        ctypes.c_uint(_OPEN_TREE_CLONE | _OPEN_TREE_CLOEXEC | _AT_RECURSIVE),
    )
    if tree_fd < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return tree_fd


def _move_mount(tree_fd: int, target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(
        ctypes.c_long(_SYS_MOVE_MOUNT),
        ctypes.c_int(tree_fd),
        ctypes.c_char_p(b""),
        ctypes.c_int(-100),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_uint(_MOVE_MOUNT_F_EMPTY_PATH),
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))




def _verify_bind_identity(source_fd: int, target: str) -> None:
    source_stat = os.fstat(source_fd)
    target_stat = os.stat(target, follow_symlinks=True)
    source_identity = (source_stat.st_dev, source_stat.st_ino)
    target_identity = (target_stat.st_dev, target_stat.st_ino)
    if source_identity != target_identity:
        raise OSError(
            errno.ESTALE,
            f"envelope bind identity mismatch for {target}: "
            f"source={source_identity!r} target={target_identity!r}",
        )

def _prepare_lease_mountpoint(target: str, tmp_root: str = "/tmp") -> None:
    """Recreate a mountpoint hidden by the fresh lease-private /tmp tmpfs.

    Only directories inside the freshly mounted, symlink-free tmpfs are
    created; a target outside it must already exist on the read-only view.
    """
    if os.path.commonpath((target, tmp_root)) != tmp_root or target == tmp_root:
        return
    os.makedirs(target, mode=0o700, exist_ok=True)

def _setup_mount_view(
    workspace: str,
    scratch: str,
    workspace_fd: int,
    scratch_fd: int,
    tmpfs_size_bytes: int,
) -> tuple[str, tuple[WritableMount, ...]]:
    _enter_private_mount_namespace()
    workspace = os.path.abspath(workspace)
    scratch = os.path.abspath(scratch)
    workspace_tree_fd = -1
    try:
        _verify_bind_identity(workspace_fd, workspace)
        _verify_bind_identity(scratch_fd, scratch)
        workspace_tree_fd = _open_tree(workspace)
        _remount_tree_readonly("/")
        tmp_size, scratch_size = _tmpfs_budgets(tmpfs_size_bytes)
        _mount_tmpfs("/tmp", tmp_size)
        _prepare_lease_mountpoint(workspace)
        _prepare_lease_mountpoint(scratch)
        _move_mount(workspace_tree_fd, workspace)
        os.close(workspace_tree_fd)
        workspace_tree_fd = -1
        _mount_tmpfs(scratch, scratch_size, mode=0o700)
        os.mkdir(os.path.join(scratch, "home"), mode=0o700)
        _verify_bind_identity(workspace_fd, workspace)
        _mount_proc()
        _remount_tree_readonly("/proc")
        return _verify_mount_view(workspace, scratch, tmpfs_size_bytes)
    finally:
        if workspace_tree_fd >= 0:
            os.close(workspace_tree_fd)


class _ChildReaper:
    """Reap namespace children from the supervisor's SIGCHLD wakeup loop."""

    def __init__(self) -> None:
        self._signal_read, self._signal_write = os.pipe()
        os.set_blocking(self._signal_read, False)
        os.set_blocking(self._signal_write, False)
        self._previous_wakeup_fd = signal.set_wakeup_fd(
            self._signal_write, warn_on_full_buffer=False
        )
        self._previous_handler = signal.signal(signal.SIGCHLD, lambda *_: None)
        self._leader: int | None = None
        self._leader_status: int | None = None

    def set_leader(self, pid: int) -> None:
        self._leader = pid
        self.reap()

    def wait_for_leader(self, pid: int) -> int:
        while self._leader_status is None:
            self.reap()
            if self._leader_status is not None:
                break
            select.select([self._signal_read], [], [], 0.05)
        return self._leader_status

    def wait_for_control(self, sock: socket.socket) -> bool:
        while True:
            readable, _, _ = select.select(
                [sock, self._signal_read], [], []
            )
            if self._signal_read in readable:
                self.reap()
            if sock in readable:
                return True

    def reap(self) -> None:
        try:
            os.read(self._signal_read, 4096)
        except BlockingIOError:
            pass
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            except InterruptedError:
                continue
            if pid == 0:
                return
            if pid == self._leader:
                self._leader_status = status

    def close(self) -> None:
        signal.set_wakeup_fd(self._previous_wakeup_fd)
        signal.signal(signal.SIGCHLD, self._previous_handler)
        os.close(self._signal_read)
        os.close(self._signal_write)


def _supervisor_main(
    sock: socket.socket,
    *,
    lease_id: str,
    runtime_id: str,
    workspace: str,
    scratch: str,
    workspace_fd: int,
    scratch_fd: int,
    authenticator: ReceiptAuthenticator,
    tmpfs_size_bytes: int,
    mode: str,
) -> None:
    try:
        mountinfo_digest, writable_mounts = _setup_mount_view(
            workspace,
            scratch,
            workspace_fd,
            scratch_fd,
            tmpfs_size_bytes,
        )
        receipt = mint_containment_receipt(
            lease_id=lease_id,
            runtime_id=runtime_id,
            mode=mode,
            writable_mounts=writable_mounts,
            authenticator=authenticator,
        )
        # Replace the mount digest with the verified digest, then resign the receipt.
        receipt = ContainmentReceipt(
            schema_version=receipt.schema_version,
            lease_id=receipt.lease_id,
            runtime_id=receipt.runtime_id,
            mode=receipt.mode,
            pid_namespace_inode=receipt.pid_namespace_inode,
            mount_namespace_inode=receipt.mount_namespace_inode,
            user_namespace_inode=receipt.user_namespace_inode,
            network_namespace_inode=receipt.network_namespace_inode,
            mountinfo_sha256=mountinfo_digest,
            writable_mounts=receipt.writable_mounts,
            created_at=receipt.created_at,
            key_id=receipt.key_id,
            algorithm=receipt.algorithm,
            signature=b"\0" * 32,
        )
        receipt = ContainmentReceipt(
            schema_version=receipt.schema_version,
            lease_id=receipt.lease_id,
            runtime_id=receipt.runtime_id,
            mode=receipt.mode,
            pid_namespace_inode=receipt.pid_namespace_inode,
            mount_namespace_inode=receipt.mount_namespace_inode,
            user_namespace_inode=receipt.user_namespace_inode,
            network_namespace_inode=receipt.network_namespace_inode,
            mountinfo_sha256=receipt.mountinfo_sha256,
            writable_mounts=receipt.writable_mounts,
            created_at=receipt.created_at,
            key_id=receipt.key_id,
            algorithm=receipt.algorithm,
            signature=authenticator.sign(receipt.canonical_bytes()),
        )
        _send_credentials(sock, {"kind": "ready", "receipt": receipt.to_mapping()})
        reaper = _ChildReaper()
        while True:
            reaper.wait_for_control(sock)
            message, fds = _recv_frame(sock)
            if message.get("kind") != "spawn":
                for fd in fds:
                    os.close(fd)
                raise OSError("unknown envelope request")
            worker = os.fork()
            if worker == 0:
                worker_reaper = _ChildReaper()
                try:
                    _spawn_one(sock, message, fds, worker_reaper)
                finally:
                    worker_reaper.close()
                os._exit(0)
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            continue
    except BaseException as exc:
        try:
            _send_credentials(
                sock,
                {"kind": "error", "error": type(exc).__name__, "message": str(exc)},
            )
        except BaseException:
            pass
        os._exit(70)


def _spawn_one(
    _control: socket.socket,
    message: Mapping[str, Any],
    fds: list[int],
    reaper: _ChildReaper,
) -> None:
    count = message.get("fd_count")
    if type(count) is not int or count != len(fds):
        raise OSError("envelope spawn descriptor count is invalid")
    status_index = message["status_index"]
    stdin_index, stdout_index, stderr_index = message["stdio_indices"]
    cwd_index = message["cwd_index"]
    executable_index = message["executable_index"]
    exec_index = message["exec_index"]
    gate_index = message["gate_index"]
    extra_indices = message.get("extra_indices", [])
    command_index = message.get("command_index")
    env = message.get("environment")
    if not isinstance(env, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in env.items()
    ):
        raise OSError("envelope environment is invalid")
    exec_ready_index = message["exec_ready_index"]
    indices = [
        status_index,
        stdin_index,
        stdout_index,
        stderr_index,
        cwd_index,
        executable_index,
        exec_index,
        gate_index,
        exec_ready_index,
        command_index,
        *extra_indices,
    ]
    if any(
        type(index) is not int or not 0 <= index < len(fds)
        for index in indices
        if index is not None
    ):
        raise OSError("envelope spawn descriptor index is invalid")
    exposable_indices = {
        index
        for index in (executable_index, exec_index, command_index, *extra_indices)
        if index is not None
    }
    if exposable_indices & {
        status_index, stdin_index, stdout_index, stderr_index,
        cwd_index, gate_index, exec_ready_index,
    }:
        raise OSError("envelope control channel is not exposable to the child")
    argv_items = message["argv"]
    descriptor_arguments = message.get("descriptor_arguments", [])
    if (
        not isinstance(argv_items, list)
        or any(type(item) is not str for item in argv_items)
        or not isinstance(descriptor_arguments, list)
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(value) is not int for value in pair)
            or not 0 <= pair[0] < len(argv_items)
            or pair[1] not in exposable_indices
            for pair in descriptor_arguments
        )
        or len({pair[0] for pair in descriptor_arguments}) != len(descriptor_arguments)
    ):
        raise OSError("envelope descriptor arguments are invalid")
    status_fd = fds[status_index]
    exec_ready_fd = fds[exec_ready_index]
    child = os.fork()
    if child == 0:
        try:
            os.setsid()
            os.fchdir(fds[cwd_index])
            os.dup2(fds[stdin_index], 0)
            os.dup2(fds[stdout_index], 1)
            os.dup2(fds[stderr_index], 2)
            for fd in fds:
                os.set_inheritable(fd, True)
            status_sock = socket.socket(fileno=status_fd)
            # The child pins its own identity: a pidfd opened by the process
            # on itself cannot name a recycled PID, even if the child is later
            # killed and reaped before the host attests it.
            self_pidfd = os.pidfd_open(os.getpid(), 0)
            status_sock.sendmsg(
                [b"B"],
                [
                    (
                        socket.SOL_SOCKET,
                        socket.SCM_CREDENTIALS,
                        struct.pack("3i", os.getpid(), os.getuid(), os.getgid()),
                    ),
                    (socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", self_pidfd)),
                ],
            )
            os.close(self_pidfd)
            if os.read(fds[gate_index], 1) != b"G":
                raise OSError("envelope exec gate was not admitted")
            os.close(fds[gate_index])
            exec_fd, argv = _prepare_exec_descriptors(
                fds,
                exec_fd=fds[exec_index],
                status_fd=status_fd,
                exec_ready_fd=exec_ready_fd,
                argv=argv_items,
                descriptor_arguments={
                    position: fds[index] for position, index in descriptor_arguments
                },
            )
            argv0_path = message.get("argv0_path")
            if isinstance(argv0_path, str) and argv0_path:
                argv[0] = argv0_path
            os.set_inheritable(exec_fd, False)
            os.set_inheritable(status_fd, False)
            os.set_inheritable(exec_ready_fd, False)
            os.write(exec_ready_fd, b"R")
            try:
                _execveat_fd(exec_fd, argv, env)
            except BaseException as exc:
                os.write(exec_ready_fd, b"E")
                try:
                    os.set_inheritable(status_fd, True)
                    _send_frame(
                        status_sock,
                        {
                            "kind": "exec_error",
                            "errno": getattr(exc, "errno", None),
                            "message": str(exc),
                        },
                    )
                except BaseException:
                    pass
                os._exit(127)
            os._exit(127)
        except BaseException as exc:
            try:
                os.write(exec_ready_fd, b"E")
            except OSError:
                pass
            try:
                os.set_inheritable(status_fd, True)
                _send_frame(
                    status_sock,
                    {
                        "kind": "exec_error",
                        "errno": getattr(exc, "errno", None),
                        "message": str(exc),
                    },
                )
            except BaseException:
                pass
            os._exit(127)
    # The worker must not hold the readiness writer: the client reads EOF on
    # successful exec only once every copy outside the exec'd image is closed.
    os.close(exec_ready_fd)
    reaper.set_leader(child)
    status = reaper.wait_for_leader(child)
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        code = -os.WTERMSIG(status)
    else:
        code = -1
    status_sock = socket.socket(fileno=status_fd)
    try:
        _send_frame(status_sock, {"kind": "done", "returncode": code})
    finally:
        status_sock.detach()


def _close_unlisted_fds(keep: set[int]) -> None:
    try:
        entries = os.listdir("/proc/self/fd")
    except OSError:
        return
    for name in entries:
        if not name.isdecimal():
            continue
        fd = int(name)
        if fd > 2 and fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass


def _launcher_main(
    sock_fd: int,
    *,
    lease_id: str,
    runtime_id: str,
    workspace: str,
    scratch: str,
    workspace_fd: int,
    scratch_fd: int,
    authenticator: ReceiptAuthenticator,
    tmpfs_size_bytes: int,
) -> None:
    sock = socket.socket(fileno=sock_fd)
    _close_unlisted_fds({sock_fd, workspace_fd, scratch_fd})
    try:
        mode = "privileged"
        try:
            _unshare(_CLONE_NEWPID | _CLONE_NEWNS | _CLONE_NEWNET)
        except OSError as exc:
            if exc.errno != errno.EPERM:
                raise
            mode = "userns"
            _unshare(_CLONE_NEWUSER | _CLONE_NEWPID | _CLONE_NEWNS | _CLONE_NEWNET)
            _enter_user_namespace()
        child = os.fork()
        if child == 0:
            _supervisor_main(
                sock,
                lease_id=lease_id,
                runtime_id=runtime_id,
                workspace=workspace,
                scratch=scratch,
                workspace_fd=workspace_fd,
                scratch_fd=scratch_fd,
                authenticator=authenticator,
                tmpfs_size_bytes=tmpfs_size_bytes,
                mode=mode,
            )
            os._exit(0)
        # Pin PID1 before this process can reap it, so the host PID can never
        # be reused under the descriptor the client signals through.
        try:
            pid1_fd = os.pidfd_open(child, 0)
        except BaseException:
            # Not yet reaped, so the raw PID still names PID1 here.
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
            raise
        try:
            _send_frame(sock, {"kind": "pid1", "pid": child}, [pid1_fd])
        finally:
            os.close(pid1_fd)
        _waited, status = os.waitpid(child, 0)
        all_dead = not Path(f"/proc/{child}").exists()
        _send_frame(sock, {"kind": "teardown", "pid1_reaped": True, "all_dead": all_dead})
        os._exit(0 if os.WIFEXITED(status) else 1)
    except BaseException as exc:
        try:
            _send_frame(sock, {"kind": "error", "error": type(exc).__name__, "message": str(exc)})
        except BaseException:
            pass
        os._exit(70)



def _pidfd_pid(pidfd: int) -> int:
    for line in Path(f"/proc/self/fdinfo/{pidfd}").read_text(encoding="ascii").splitlines():
        if line.startswith("Pid:"):
            return int(line.split()[1])
    raise OSError(errno.ENOTSUP, "pidfd fdinfo does not report a Pid")


def _verify_attested_pidfd(
    pidfd: int,
    credentials: tuple[int, int, int],
    pid_namespace_inode: int,
) -> None:
    """Check a self- or launcher-pinned pidfd names the attested process.

    A pidfd keeps its process identity across PID reuse; the Pid field turns
    -1 once that process is reaped, so an equal Pid before and after the
    /proc reads proves those reads observed the pinned process.
    """
    pid = credentials[0]
    if _pidfd_pid(pidfd) != pid:
        raise OSError(errno.ESRCH, "envelope pidfd does not name the attested live process")
    if os.stat(f"/proc/{pid}/ns/pid").st_ino != pid_namespace_inode:
        raise OSError("attested process PID namespace identity changed")
    uid = gid = None
    for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
        if line.startswith("Uid:"):
            uid = int(line.split()[1])
        elif line.startswith("Gid:"):
            gid = int(line.split()[1])
    if uid != credentials[1] or gid != credentials[2]:
        raise OSError("attested process credentials changed")
    if _pidfd_pid(pidfd) != pid:
        raise OSError(errno.ESRCH, "attested process exited during attestation")

def _pidfd_send_signal(pidfd: int, sig: int) -> None:
    sender = getattr(os, "pidfd_send_signal", None)
    if sender is not None:
        sender(pidfd, sig)
        return
    function = ctypes.CDLL(None, use_errno=True).syscall
    function.restype = ctypes.c_long
    result = function(
        ctypes.c_long(424),
        ctypes.c_int(pidfd),
        ctypes.c_int(sig),
        ctypes.c_void_p(),
        ctypes.c_uint(0),
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))

def _read_exec_pipe(fd: int) -> bool:
    if os.read(fd, 1) != b"R":
        raise OSError(errno.EIO, "execveat readiness was not reported")
    if os.read(fd, 1) != b"":
        raise OSError(errno.EIO, "execveat failed after readiness")
    return True


class EnvelopeProcess:
    def __init__(
        self,
        *,
        pid: int,
        pidfd: int,
        status: socket.socket,
        gate: int,
        exec_ready: int,
        stdin: Any,
        stdout: Any,
        stderr: Any,
    ) -> None:
        self._exec_ready = exec_ready
        self._gate = gate
        self._pidfd = pidfd
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._status = status
        self.returncode: int | None = None
        self._wait_task: Any = None
        self.exec_error: Mapping[str, Any] | None = None

    async def wait_exec(self, timeout_ms: int) -> None:
        descriptor = self._exec_ready
        if descriptor < 0:
            raise RuntimeError("exec readiness was already consumed")
        self._exec_ready = -1
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_read_exec_pipe, descriptor),
                timeout_ms / 1000,
            )
        finally:
            os.close(descriptor)

    async def wait(self) -> int:
        if self._wait_task is None:
            self._wait_task = asyncio.create_task(
                asyncio.to_thread(_recv_frame, self._status)
            )
        try:
            message, _fds = await asyncio.shield(self._wait_task)
        except (EOFError, OSError):
            self.returncode = -signal.SIGKILL
        else:
            if message.get("kind") == "exec_error":
                self.exec_error = message
                self.returncode = 127
            elif message.get("kind") != "done" or type(message.get("returncode")) is not int:
                self.returncode = -signal.SIGKILL
            else:
                self.returncode = message["returncode"]
        finally:
            if self._status is not None:
                self._status.close()
                self._status = None
            if self._gate >= 0:
                os.close(self._gate)
                self._gate = -1
            if self._pidfd >= 0:
                os.close(self._pidfd)
                self._pidfd = -1
            if self._exec_ready >= 0:
                os.close(self._exec_ready)
                self._exec_ready = -1
        return self.returncode

    def admit(self) -> None:
        if self._gate < 0:
            raise RuntimeError("envelope exec gate was already closed")
        gate = self._gate
        self._gate = -1
        try:
            os.write(gate, b"G")
        finally:
            os.close(gate)

    def kill(self) -> None:
        if self._pidfd < 0:
            return
        try:
            _pidfd_send_signal(self._pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def _pipe_reader(fd: int) -> Any:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, os.fdopen(fd, "rb", buffering=0))
    return reader


async def _pipe_writer(fd: int) -> Any:
    loop = asyncio.get_running_loop()
    protocol = asyncio.streams.FlowControlMixin()
    transport, _ = await loop.connect_write_pipe(
        lambda: protocol, os.fdopen(fd, "wb", buffering=0)
    )
    return asyncio.StreamWriter(transport, protocol, None, loop)


def _recv_ready_status(status: socket.socket) -> tuple[tuple[int, int, int], int]:
    """Receive the admission byte, the child's credentials and its self-pidfd."""
    payload, ancdata, flags, _address = status.recvmsg(
        1,
        socket.CMSG_SPACE(3 * struct.calcsize("i"))
        + socket.CMSG_SPACE(struct.calcsize("i")),
    )
    credentials = None
    received: list[int] = []
    for level, kind, data in ancdata:
        if level != socket.SOL_SOCKET:
            continue
        if kind == socket.SCM_CREDENTIALS and len(data) >= 12:
            credentials = struct.unpack("3i", data[:12])
        elif kind == socket.SCM_RIGHTS:
            usable = len(data) - len(data) % struct.calcsize("i")
            received.extend(struct.unpack(f"{usable // 4}i", data[:usable]))
    try:
        if flags & socket.MSG_CTRUNC:
            raise OSError("envelope admission control data was truncated")
        if payload != b"B":
            raise OSError("envelope child did not stop at admission")
        if credentials is None or credentials[0] <= 0:
            raise OSError("envelope child credentials are missing")
        if len(received) != 1:
            raise OSError("envelope child did not pin its identity descriptor")
    except BaseException:
        for fd in received:
            os.close(fd)
        raise
    return credentials, received[0]


def _resolve_host_pid(supervisor_pid: int, namespace_pid: int) -> int:
    """Resolve a child PID from the supervisor's PID namespace to the host.

    ``SCM_CREDENTIALS`` may report a PID in the receiver's namespace or in
    the initial namespace, depending on which side of the namespace boundary
    owns the socket.  Prefer the direct host-visible child when present, and
    otherwise map a namespace PID through ``/proc``.
    """
    expected_parent = str(supervisor_pid)

    def read_status(pid: int) -> tuple[str | None, str | None]:
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
        except (OSError, UnicodeError):
            return None, None
        nspid = parent = None
        for line in status.splitlines():
            if line.startswith("NSpid:"):
                values = line.split()[1:]
                if values:
                    nspid = values[-1]
            elif line.startswith("PPid:"):
                fields = line.split()
                if len(fields) == 2:
                    parent = fields[1]
        return nspid, parent

    _nspid, parent = read_status(namespace_pid)
    if parent == expected_parent:
        return namespace_pid

    suffix = str(namespace_pid)
    for entry in os.scandir("/proc"):
        if not entry.name.isdecimal():
            continue
        nspid, parent = read_status(int(entry.name))
        if nspid == suffix and parent == expected_parent:
            return int(entry.name)
    for entry in os.scandir("/proc"):
        if not entry.name.isdecimal():
            continue
        nspid, _parent = read_status(int(entry.name))
        if nspid == suffix:
            return int(entry.name)
    raise OSError(
        errno.ESRCH,
        f"unable to resolve namespace PID {namespace_pid} "
        f"under supervisor {supervisor_pid}",
    )




async def spawn_envelope_process(
    envelope: EnvelopeLaunch,
    *,
    argv: Sequence[str],
    argv0_path: str | None = None,
    environment: Mapping[str, str],
    executable_fd: int,
    command_fd: int | None,
    extra_fds: Sequence[int],
    cwd_fd: int,
    timeout_ms: int,
) -> EnvelopeProcess:
    handed = {executable_fd, *extra_fds} | ({command_fd} if command_fd is not None else set())
    if any(type(item) is DescriptorPath and item.fd not in handed for item in argv):
        raise ValueError("argv names a descriptor that is not handed to the child")
    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    gate_r, gate_w = os.pipe()
    exec_ready_r, exec_ready_w = os.pipe2(os.O_CLOEXEC)
    status_host, status_supervisor = socket.socketpair(
        socket.AF_UNIX, socket.SOCK_SEQPACKET
    )
    status_host.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    sent_fds = [stdin_r, stdout_w, stderr_w, status_supervisor.fileno(), executable_fd]
    command_index = None
    if command_fd is not None:
        command_index = len(sent_fds)
        sent_fds.append(command_fd)
    extra_indices = []
    for fd in extra_fds:
        extra_indices.append(len(sent_fds))
        sent_fds.append(fd)
    exec_index = (
        command_index
        if command_index is not None
        and argv
        and type(argv[0]) is DescriptorPath
        and argv[0].fd == command_fd
        else 4
    )
    gate_index = len(sent_fds)
    sent_fds.append(gate_r)
    cwd_index = len(sent_fds)
    sent_fds.append(os.dup(cwd_fd))
    exec_ready_index = len(sent_fds)
    sent_fds.append(exec_ready_w)
    # Only launcher-generated DescriptorPath entries name descriptors, and only
    # the ones handed to the child; status, gate, readiness, stdio and cwd
    # channels never are. Plain strings, including model text, pass verbatim.
    exposable = {
        sent_fds[index]: index
        for index in (4, command_index, *extra_indices)
        if index is not None
    }
    descriptor_arguments = [
        [position, exposable[item.fd]]
        for position, item in enumerate(argv)
        if type(item) is DescriptorPath
    ]
    message = {
        "kind": "spawn",
        "fd_count": len(sent_fds),
        "status_index": 3,
        "stdio_indices": [0, 1, 2],
        "executable_index": 4,
        "exec_index": exec_index,
        "command_index": command_index,
        "gate_index": gate_index,
        "extra_indices": extra_indices,
        "exec_ready_index": exec_ready_index,
        "cwd_index": cwd_index,
        "argv0_path": argv0_path,
        "argv": [str(item) for item in argv],
        "descriptor_arguments": descriptor_arguments,
        "environment": dict(environment),
    }
    try:
        await asyncio.to_thread(_send_frame, envelope.control, message, sent_fds)
    finally:
        for fd in (stdin_r, stdout_w, stderr_w, gate_r, exec_ready_w, sent_fds[cwd_index]):
            try:
                os.close(fd)
            except OSError:
                pass
    pidfd = -1
    try:
        credentials, pidfd = await asyncio.wait_for(
            asyncio.to_thread(_recv_ready_status, status_host),
            max(0.001, timeout_ms / 1000),
        )
        pid, _uid, _gid = credentials
        _verify_attested_pidfd(pidfd, credentials, envelope.receipt.pid_namespace_inode)
        stdout = await _pipe_reader(stdout_r)
        stderr = await _pipe_reader(stderr_r)
        stdin = await _pipe_writer(stdin_w)
        process = EnvelopeProcess(
            pid=pid,
            pidfd=pidfd,
            status=status_host,
            gate=gate_w,
            exec_ready=exec_ready_r,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        pidfd = -1
        return process
    except BaseException:
        if pidfd >= 0:
            os.close(pidfd)
        os.close(exec_ready_r)
        try:
            os.close(gate_w)
        except OSError:
            pass
        for fd in (stdout_r, stderr_r, stdin_w):
            try:
                os.close(fd)
            except OSError:
                pass
        raise

@dataclass(slots=True)
class EnvelopeLaunch:
    control: socket.socket
    launcher_pid: int
    pid1: int
    pid1_fd: int
    receipt: ContainmentReceipt
    authenticator: ReceiptAuthenticator
    workspace: str
    scratch: str

    async def terminate(self) -> ContainmentReceipt:
        # Signal through the attested pidfd: a raw PID may be reused on the host
        # once the launcher reaps PID1.
        if self.pid1_fd < 0:
            raise RuntimeError("envelope was already terminated")
        try:
            _pidfd_send_signal(self.pid1_fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        message: dict[str, Any] | None = None
        while message is None:
            try:
                candidate, _fds = await __import__("asyncio").to_thread(_recv_frame, self.control)
            except (EOFError, OSError):
                break
            if candidate.get("kind") == "teardown":
                message = candidate
            elif candidate.get("kind") == "error":
                break
        try:
            await __import__("asyncio").to_thread(os.waitpid, self.launcher_pid, 0)
        except ChildProcessError:
            pass
        pid1_reaped = bool(message and message.get("pid1_reaped") is True)
        pid1_exited = bool(select.select([self.pid1_fd], [], [], 0)[0])
        os.close(self.pid1_fd)
        self.pid1_fd = -1
        all_dead = bool(message and message.get("all_dead") is True and pid1_exited)
        receipt = add_teardown_outcome(
            self.receipt,
            pid1_reaped=pid1_reaped,
            all_dead=all_dead,
            authenticator=self.authenticator,
        )
        self.control.close()
        return receipt


class DescriptorPath(str):
    """A launcher-generated argv entry naming one descriptor handed to the child.

    Only entries of exactly this type are renumbered for the child. Equal
    plain strings, including any model-supplied text, pass through verbatim.
    """

    fd: int

    def __new__(cls, fd: int) -> "DescriptorPath":
        path = super().__new__(cls, f"/proc/self/fd/{fd}")
        path.fd = fd
        return path


def _prepare_exec_descriptors(
    fds: Sequence[int],
    *,
    exec_fd: int,
    status_fd: int,
    exec_ready_fd: int,
    argv: Sequence[str],
    descriptor_arguments: Mapping[int, int],
) -> tuple[int, list[str]]:
    """Pack the exec and argv-named descriptors low; close every other one.

    ``descriptor_arguments`` maps argv positions to received descriptors; no
    other argv entry is interpreted as a descriptor reference.
    """
    referenced = {exec_fd, *descriptor_arguments.values()}
    mapping: dict[int, int] = {}
    next_fd = 3
    for source in sorted(referenced):
        while next_fd in (status_fd, exec_ready_fd) or next_fd in mapping.values():
            next_fd += 1
        os.dup2(source, next_fd, inheritable=True)
        mapping[source] = next_fd
        next_fd += 1
    rewritten = list(argv)
    for position, source in descriptor_arguments.items():
        rewritten[position] = f"/proc/self/fd/{mapping[source]}"
    preserved = set(mapping.values()) | {status_fd, exec_ready_fd}
    for fd in set(fds) | set(mapping):
        if fd > 2 and fd not in preserved:
            try:
                os.close(fd)
            except OSError:
                pass
    return mapping[exec_fd], rewritten


def launch_envelope(
    *,
    lease_id: str,
    runtime_id: str,
    workspace: Path,
    scratch: Path,
    workspace_fd: int,
    authenticator: ReceiptAuthenticator,
    tmpfs_size_bytes: int,
) -> EnvelopeLaunch:
    if os.name != "posix" or not Path("/proc/self/ns").is_dir():
        raise OSError(errno.ENOTSUP, "Linux namespaces are required for containment")
    workspace_path_fd = os.open(
        f"/proc/self/fd/{workspace_fd}",
        os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC,
    )
    scratch_fd = os.open(
        scratch, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC
    )
    control_parent, control_child = socket.socketpair(
        socket.AF_UNIX, socket.SOCK_SEQPACKET
    )
    control_parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    pid = os.fork()
    if pid == 0:
        control_parent.close()
        _launcher_main(
            control_child.detach(),
            lease_id=lease_id,
            runtime_id=runtime_id,
            workspace_fd=os.dup(workspace_path_fd),
            workspace=str(workspace),
            scratch=str(scratch),
            scratch_fd=scratch_fd,
            authenticator=authenticator,
            tmpfs_size_bytes=tmpfs_size_bytes,
        )
        os._exit(70)
    control_child.close()
    os.close(workspace_path_fd)
    os.close(scratch_fd)
    pid1_fd = -1
    try:
        ready: dict[str, Any] | None = None
        credentials: tuple[int, int, int] | None = None
        pid1_pid: int | None = None
        # SOCK_SEQPACKET preserves each frame. The launcher sends the PID1
        # pidfd it opened before it could reap PID1; the supervisor sends
        # readiness with kernel-verified credentials. Either may come first.
        while ready is None or pid1_fd < 0:
            message, ancdata, _flags, _address = control_parent.recvmsg(
                _MAX_FRAME + 4,
                socket.CMSG_SPACE(_MAX_FDS * array.array("i").itemsize)
                + socket.CMSG_SPACE(12),
            )
            if len(message) < 4:
                raise OSError("envelope readiness is truncated")
            size = struct.unpack("!I", message[:4])[0]
            if size > _MAX_FRAME or len(message) != size + 4:
                raise OSError("envelope readiness size is invalid")
            frame = json.loads(message[4:].decode("utf-8"))
            received: list[int] = []
            frame_credentials: tuple[int, int, int] | None = None
            for level, kind, data in ancdata:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    values = array.array("i")
                    values.frombytes(data[: len(data) - (len(data) % values.itemsize)])
                    received.extend(values.tolist())
                elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(data) >= 12:
                    frame_credentials = struct.unpack("3i", data[:12])
            kind_name = frame.get("kind") if type(frame) is dict else None
            if kind_name == "pid1" and pid1_fd < 0 and len(received) == 1:
                pid1_fd = received[0]
                pid1_pid = frame.get("pid")
                continue
            for fd in received:
                os.close(fd)
            if kind_name == "error":
                raise OSError(frame.get("message", "envelope launch failed"))
            if kind_name != "ready" or ready is not None:
                raise OSError("envelope readiness is invalid")
            ready = frame
            credentials = frame_credentials
        receipt = ContainmentReceipt.from_mapping(ready["receipt"])
        if credentials is None or credentials[0] <= 0:
            raise OSError("envelope supervisor credentials are missing")
        if type(pid1_pid) is not int or pid1_pid != credentials[0]:
            raise OSError("envelope PID1 descriptor does not match the supervisor")
        _verify_attested_pidfd(pid1_fd, credentials, receipt.pid_namespace_inode)
        launch = EnvelopeLaunch(
            control_parent,
            pid,
            credentials[0],
            pid1_fd,
            receipt,
            authenticator,
            str(workspace),
            str(scratch),
        )
        pid1_fd = -1
        return launch
    except BaseException:
        if pid1_fd >= 0:
            os.close(pid1_fd)
        control_parent.close()
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        raise


__all__ = [
    "AdmittedLeaseLedger",
    "AdmittedLeaseRecord",
    "ContainmentReceipt",
    "ContainmentReceiptError",
    "EnvelopeLaunch",
    "EnvelopeProcess",
    "RuntimeContainment",
    "add_teardown_outcome",
    "launch_envelope",
    "mint_containment_receipt",
    "spawn_envelope_process",
    "verify_containment_receipt",
]
