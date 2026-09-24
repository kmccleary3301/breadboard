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
import time
import threading
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from breadboard_engine.compilation.contracts import canonical_json_bytes

from .mount_namespace_broker import (
    _CLONE_NEWNS,
    _MS_BIND,
    _MS_NODEV,
    _MS_NOEXEC,
    _MS_NOSUID,
    _MNT_DETACH,
    _bind,
    _enter_private_mount_namespace,
    _libc_call,
    _mountinfo,
    _remount_readonly,
)


UTC = timezone.utc
_CLONE_NEWUSER = 0x10000000
_CLONE_NEWPID = 0x20000000
_RECEIPT_SCHEMA = "bb.containment-receipt.v1"
_MAX_FRAME = 256 * 1024
_SYS_OPEN_TREE = 428
_SYS_MOVE_MOUNT = 429
_OPEN_TREE_CLONE = 1
_OPEN_TREE_CLOEXEC = 0x80000
_MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
_AT_RECURSIVE = 0x8000
_MAX_FDS = 64


class RuntimeContainment(str, Enum):
    ATTESTED = "attested"
    UNCONFINED_TEST_ONLY = "unconfined_test_only"


class ContainmentReceiptError(ValueError):
    code = "containment_receipt_invalid"


class ReceiptAuthenticator(Protocol):
    key_id: str
    algorithm: str

    def sign(self, unsigned_canonical_bytes: bytes) -> bytes: ...

    def verify(self, unsigned_canonical_bytes: bytes, signature: bytes) -> bool: ...


def _ns_inode(link: str) -> int:
    left, right = link.rsplit("[", 1)
    if not right.endswith("]") or not right[:-1].isdigit():
        raise ContainmentReceiptError("namespace inode is malformed")
    return int(right[:-1])


def _namespace_inodes() -> dict[str, int]:
    return {
        name: _ns_inode(os.readlink(f"/proc/self/ns/{name}"))
        for name in ("pid", "mnt", "user")
    }


def _digest_mountinfo(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _receipt_unsigned(payload: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(payload))


@dataclass(frozen=True, slots=True)
class ContainmentReceipt:
    schema_version: str
    lease_id: str
    runtime_id: str
    mode: str
    pid_namespace_inode: int
    mount_namespace_inode: int
    user_namespace_inode: int
    mountinfo_sha256: str
    writable_roots: tuple[str, ...]
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
            },
            "mountinfo_sha256": self.mountinfo_sha256,
            "writable_roots": list(self.writable_roots),
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
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContainmentReceipt":
        if type(value) is not dict and not isinstance(value, Mapping):
            raise ContainmentReceiptError("containment receipt is not an object")
        try:
            namespaces = value["namespaces"]
            signature = bytes.fromhex(value["signature"])
            roots = tuple(value["writable_roots"])
            outcome_raw = value.get("outcome")
            outcome = None if outcome_raw is None else {
                "pid1_reaped": outcome_raw["pid1_reaped"],
                "all_dead": outcome_raw["all_dead"],
            }
            receipt = cls(
                schema_version=value["schema"],
                lease_id=value["lease_id"],
                runtime_id=value["runtime_id"],
                mode=value["mode"],
                pid_namespace_inode=namespaces["pid"],
                mount_namespace_inode=namespaces["mnt"],
                user_namespace_inode=namespaces["user"],
                mountinfo_sha256=value["mountinfo_sha256"],
                writable_roots=roots,
                created_at=value["created_at"],
                key_id=value["key_id"],
                algorithm=value["algorithm"],
                signature=signature,
                outcome=outcome,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ContainmentReceiptError("containment receipt is malformed") from exc
        _validate_receipt_shape(receipt)
        return receipt


def _validate_receipt_shape(receipt: ContainmentReceipt) -> None:
    if (
        receipt.schema_version != _RECEIPT_SCHEMA
        or not isinstance(receipt.lease_id, str)
        or not receipt.lease_id
        or not isinstance(receipt.runtime_id, str)
        or not receipt.runtime_id
        or receipt.mode not in {"privileged", "userns"}
        or any(
            type(value) is not int or value <= 0
            for value in (
                receipt.pid_namespace_inode,
                receipt.mount_namespace_inode,
                receipt.user_namespace_inode,
            )
        )
        or not isinstance(receipt.mountinfo_sha256, str)
        or not receipt.mountinfo_sha256.startswith("sha256:")
        or not receipt.writable_roots
        or tuple(sorted(set(receipt.writable_roots))) != receipt.writable_roots
        or any(type(root) is not str or not root.startswith("/") for root in receipt.writable_roots)
        or not isinstance(receipt.created_at, str)
        or not isinstance(receipt.key_id, str)
        or not receipt.key_id
        or receipt.algorithm != "hmac-sha256-v1"
        or type(receipt.signature) is not bytes
        or len(receipt.signature) != 32
        or (receipt.outcome is not None and (
            set(receipt.outcome) != {"pid1_reaped", "all_dead"}
            or any(type(item) is not bool for item in receipt.outcome.values())
        ))
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
    writable_roots: Sequence[str],
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
        mountinfo_sha256=_digest_mountinfo(_mountinfo()),
        writable_roots=tuple(sorted(set(writable_roots))),
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
        mountinfo_sha256=receipt.mountinfo_sha256,
        writable_roots=receipt.writable_roots,
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
    _validate_receipt_shape(receipt)
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
        mountinfo_sha256=receipt.mountinfo_sha256,
        writable_roots=receipt.writable_roots,
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
        mountinfo_sha256=unsigned.mountinfo_sha256,
        writable_roots=unsigned.writable_roots,
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
    parsed = receipt if isinstance(receipt, ContainmentReceipt) else ContainmentReceipt.from_mapping(receipt)
    _validate_receipt_shape(parsed)
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


def _mount_tmpfs(target: str, size_bytes: int) -> None:
    if type(size_bytes) is not int or size_bytes <= 0:
        raise ValueError("tmpfs size is invalid")
    _libc_call(
        "mount",
        ctypes.c_char_p(b"tmpfs"),
        ctypes.c_char_p(os.fsencode(target)),
        ctypes.c_char_p(b"tmpfs"),
        ctypes.c_ulong(_MS_NOSUID | _MS_NODEV),
        ctypes.c_char_p(f"size={size_bytes},mode=1777".encode("ascii")),
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


def _verify_mount_view(workspace: str, scratch: str) -> tuple[str, tuple[str, ...]]:
    workspace = os.path.abspath(workspace)
    scratch = os.path.abspath(scratch)
    raw = _mountinfo()
    roots = tuple(sorted({workspace, scratch, "/tmp"}))
    entries: dict[str, list[bytes]] = {}
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) >= 6:
            entries[fields[4].replace(b"\\040", b" ").decode("utf-8", "surrogateescape")] = fields
    root = entries.get("/")
    if root is None or b"ro" not in root[5].split(b","):
        raise OSError("envelope root mount is writable")
    for path in roots:
        fields = entries.get(path)
        if fields is None or b"rw" not in fields[5].split(b","):
            raise OSError(f"envelope writable mount is absent: {path}")
    return _digest_mountinfo(raw), roots

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

def _setup_mount_view(
    workspace: str,
    scratch: str,
    workspace_fd: int,
    scratch_fd: int,
    tmpfs_size_bytes: int,
) -> tuple[str, tuple[str, ...]]:
    _enter_private_mount_namespace()
    workspace = os.path.abspath(workspace)
    scratch = os.path.abspath(scratch)
    workspace_tree_fd = -1
    scratch_tree_fd = -1
    try:
        _verify_bind_identity(workspace_fd, workspace)
        _verify_bind_identity(scratch_fd, scratch)
        workspace_tree_fd = _open_tree(workspace)
        scratch_tree_fd = _open_tree(scratch)
        _mount_tmpfs("/tmp", tmpfs_size_bytes)
        for path in (workspace, scratch):
            os.makedirs(path, mode=0o700, exist_ok=True)
        _move_mount(workspace_tree_fd, workspace)
        os.close(workspace_tree_fd)
        workspace_tree_fd = -1
        _move_mount(scratch_tree_fd, scratch)
        os.close(scratch_tree_fd)
        scratch_tree_fd = -1
        _verify_bind_identity(workspace_fd, workspace)
        _verify_bind_identity(scratch_fd, scratch)
        _remount_readonly("/")
        _mount_proc()
        return _verify_mount_view(workspace, scratch)
    finally:
        for tree_fd in (workspace_tree_fd, scratch_tree_fd):
            if tree_fd >= 0:
                os.close(tree_fd)


class _ChildReaper:
    """Continuously reap namespace children while preserving leader statuses."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._forking = False
        self._leaders: set[int] = set()
        self._statuses: dict[int, int] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def prepare_fork(self) -> None:
        self._condition.acquire()
        self._forking = True

    def register_forked_child(self, pid: int) -> None:
        self._leaders.add(pid)
        self._forking = False
        self._condition.notify_all()
        self._condition.release()

    def abort_fork(self) -> None:
        self._forking = False
        self._condition.notify_all()
        self._condition.release()
    def wait_for_leader(self, pid: int) -> int:
        with self._condition:
            while pid not in self._statuses:
                self._condition.wait()
            status = self._statuses.pop(pid)
            self._leaders.discard(pid)
            return status

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._forking:
                    self._condition.wait(timeout=0.01)
                    continue
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                time.sleep(0.01)
                continue
            except InterruptedError:
                continue
            if pid == 0:
                time.sleep(0.005)
                continue
            with self._condition:
                if pid in self._leaders:
                    self._statuses[pid] = status
                    self._condition.notify_all()


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
        mountinfo_digest, writable_roots = _setup_mount_view(
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
            writable_roots=writable_roots,
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
            mountinfo_sha256=mountinfo_digest,
            writable_roots=receipt.writable_roots,
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
            mountinfo_sha256=receipt.mountinfo_sha256,
            writable_roots=receipt.writable_roots,
            created_at=receipt.created_at,
            key_id=receipt.key_id,
            algorithm=receipt.algorithm,
            signature=authenticator.sign(receipt.canonical_bytes()),
        )
        _send_credentials(sock, {"kind": "ready", "receipt": receipt.to_mapping()})
        reaper = _ChildReaper()
        reaper.start()
        while True:
            message, fds = _recv_frame(sock)
            if message.get("kind") == "reap":
                _send_frame(sock, {"kind": "reaped"})
                continue
            if message.get("kind") != "spawn":
                for fd in fds:
                    os.close(fd)
                raise OSError("unknown envelope request")
            try:
                _spawn_one(sock, message, fds, reaper)
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
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
    env = message.get("environment")
    if not isinstance(env, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in env.items()
    ):
        raise OSError("envelope environment is invalid")
    indices = [
        status_index,
        stdin_index,
        stdout_index,
        stderr_index,
        cwd_index,
        executable_index,
        exec_index,
        gate_index,
        *extra_indices,
    ]
    if any(
        type(index) is not int or not 0 <= index < len(fds)
        for index in indices
        if index is not None
    ):
        raise OSError("envelope spawn descriptor index is invalid")
    status_fd = fds[status_index]
    reaper.prepare_fork()
    try:
        child = os.fork()
    except BaseException:
        reaper.abort_fork()
        raise
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
            status_sock.sendmsg(
                [b"B"],
                [
                    (
                        socket.SOL_SOCKET,
                        socket.SCM_CREDENTIALS,
                        struct.pack("3i", os.getpid(), os.getuid(), os.getgid()),
                    )
                ],
            )
            if os.read(fds[gate_index], 1) != b"G":
                raise OSError("envelope exec gate was not admitted")
            os.close(fds[gate_index])
            argv = list(_rewrite_received_fd_paths(tuple(message["argv"]), fds))
            argv0_path = message.get("argv0_path")
            if isinstance(argv0_path, str) and argv0_path:
                argv[0] = argv0_path
            exec_fd, argv = _prepare_exec_descriptors(
                fds,
                exec_fd=fds[exec_index],
                status_fd=status_fd,
                argv=argv,
            )
            os.set_inheritable(exec_fd, False)
            os.set_inheritable(status_fd, False)
            try:
                _execveat_fd(exec_fd, argv, env)
            except BaseException as exc:
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
    try:
        mode = "privileged"
        try:
            _unshare(_CLONE_NEWPID | _CLONE_NEWNS)
        except OSError as exc:
            if exc.errno != errno.EPERM:
                raise
            mode = "userns"
            _unshare(_CLONE_NEWUSER | _CLONE_NEWPID | _CLONE_NEWNS)
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


class EnvelopeProcess:
    def __init__(
        self,
        *,
        pid: int,
        status: socket.socket,
        gate: int,
        stdin: Any,
        stdout: Any,
        stderr: Any,
    ) -> None:
        self._gate = gate
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._status = status
        self.returncode: int | None = None
        self._wait_task: Any = None
        self.exec_error: Mapping[str, Any] | None = None

    async def wait(self) -> int:
        if self._wait_task is None:
            self._wait_task = asyncio.create_task(
                asyncio.to_thread(_recv_frame, self._status)
            )
        try:
            message, _fds = await self._wait_task
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
        try:
            os.kill(self.pid, signal.SIGKILL)
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


def _recv_ready_status(status: socket.socket) -> int:
    payload, ancdata, _flags, _address = status.recvmsg(
        1, socket.CMSG_SPACE(3 * struct.calcsize("i"))
    )
    if payload != b"B":
        raise OSError("envelope child did not stop at admission")
    process_pid = None
    for level, kind, data in ancdata:
        if (
            level == socket.SOL_SOCKET
            and kind == socket.SCM_CREDENTIALS
            and len(data) >= 12
        ):
            process_pid = struct.unpack("3i", data[:12])[0]
            break
    if process_pid is None or process_pid <= 0:
        raise OSError("envelope child credentials are missing")
    return process_pid
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
    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    gate_r, gate_w = os.pipe()
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
        and argv[0] == f"/proc/self/fd/{command_fd}"
        else 4
    )
    gate_index = len(sent_fds)
    sent_fds.append(gate_r)
    cwd_index = len(sent_fds)
    sent_fds.append(os.dup(cwd_fd))
    mapping = {fd: index for index, fd in enumerate(sent_fds)}
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
        "cwd_index": cwd_index,
        "argv0_path": argv0_path,
        "argv": _rewrite_fd_paths(argv, mapping),
        "environment": dict(environment),
    }
    try:
        await asyncio.to_thread(_send_frame, envelope.control, message, sent_fds)
    finally:
        for fd in (stdin_r, stdout_w, stderr_w, gate_r, sent_fds[-1]):
            try:
                os.close(fd)
            except OSError:
                pass
    try:
        namespace_pid = await asyncio.wait_for(
            asyncio.to_thread(_recv_ready_status, status_host),
            max(0.001, timeout_ms / 1000),
        )
        pid = _resolve_host_pid(envelope.pid1, namespace_pid)
        stdout = await _pipe_reader(stdout_r)
        stderr = await _pipe_reader(stderr_r)
        stdin = await _pipe_writer(stdin_w)
        process = EnvelopeProcess(
            pid=pid,
            status=status_host,
            gate=gate_w,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        return process
    except BaseException:
        status_host.close()
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
    receipt: ContainmentReceipt
    authenticator: ReceiptAuthenticator
    workspace: str
    scratch: str

    async def terminate(self) -> ContainmentReceipt:
        try:
            os.kill(self.pid1, signal.SIGKILL)
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
        all_dead = bool(message and message.get("all_dead") is True and not Path(f"/proc/{self.pid1}").exists())
        receipt = add_teardown_outcome(
            self.receipt,
            pid1_reaped=pid1_reaped,
            all_dead=all_dead,
            authenticator=self.authenticator,
        )
        self.control.close()
        return receipt


def _rewrite_fd_paths(argv: Sequence[str], mapping: Mapping[int, int]) -> tuple[str, ...]:
    result = []
    for item in argv:
        value = item
        for source, target in mapping.items():
            value = value.replace(f"/proc/self/fd/{source}", f"/proc/self/fd/{target}")
        result.append(value)
    return tuple(result)


def _rewrite_received_fd_paths(argv: Sequence[str], fds: Sequence[int]) -> tuple[str, ...]:
    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(fds):
            return match.group(0)
        return f"/proc/self/fd/{fds[index]}"

    return tuple(
        re.sub(r"/proc/self/fd/([0-9]+)", replace, str(item))
        for item in argv
    )
def _prepare_exec_descriptors(
    fds: Sequence[int],
    *,
    exec_fd: int,
    status_fd: int,
    argv: Sequence[str],
) -> tuple[int, tuple[str, ...]]:
    referenced = {exec_fd}
    for item in argv:
        for match in re.finditer(r"/proc/self/fd/([0-9]+)", item):
            candidate = int(match.group(1))
            if candidate in fds:
                referenced.add(candidate)
    mapping: dict[int, int] = {}
    next_fd = 3
    for source in sorted(referenced):
        while next_fd == status_fd or next_fd in mapping.values():
            next_fd += 1
        os.dup2(source, next_fd, inheritable=True)
        mapping[source] = next_fd
        next_fd += 1
    rewritten = _rewrite_fd_paths(argv, mapping)
    preserved = set(mapping) | {status_fd}
    for fd in set(fds):
        if fd > 2 and fd not in preserved:
            try:
                os.close(fd)
            except OSError:
                pass
    for source, target in mapping.items():
        if source != target:
            try:
                os.close(source)
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
    try:
        message, ancdata, _flags, _address = control_parent.recvmsg(
            _MAX_FRAME + 4,
            socket.CMSG_SPACE(_MAX_FDS * array.array("i").itemsize),
        )
        # SOCK_SEQPACKET preserves each response as one message.
        if len(message) < 4:
            raise OSError("envelope readiness is truncated")
        size = struct.unpack("!I", message[:4])[0]
        if size > _MAX_FRAME or len(message) != size + 4:
            raise OSError("envelope readiness size is invalid")
        ready = json.loads(message[4:].decode("utf-8"))
        if ready.get("kind") == "error":
            raise OSError(ready.get("message", "envelope launch failed"))
        if ready.get("kind") != "ready":
            raise OSError("envelope readiness is invalid")
        receipt = ContainmentReceipt.from_mapping(ready["receipt"])
        credentials_pid = None
        for level, kind, data in ancdata:
            if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(data) >= 12:
                credentials_pid = struct.unpack("3i", data[:12])[0]
                break
        if credentials_pid is None or credentials_pid <= 0:
            raise OSError("envelope supervisor credentials are missing")
        return EnvelopeLaunch(
            control_parent,
            pid,
            credentials_pid,
            receipt,
            authenticator,
            str(workspace),
            str(scratch),
        )
    except BaseException:
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
