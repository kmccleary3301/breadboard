from __future__ import annotations

import argparse
import ctypes
from contextlib import ExitStack
import errno
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_DOCUMENT_BYTES = 16 * 1024
_MAX_PATH_BYTES = 4096
_CLIENT_WAIT_SECONDS = 60.0
_MNT_DETACH = 2
_SYS_OPENAT2 = 437
_SYS_OPEN_TREE = 428
_SYS_MOVE_MOUNT = 429
_AT_EMPTY_PATH = 0x1000
_OPEN_TREE_CLONE = 0x01
_OPEN_TREE_CLOEXEC = 0x80000
_MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
_MOVE_MOUNT_T_EMPTY_PATH = 0x00000040
_CAP_SYS_CHROOT = 18
_CAP_SYS_ADMIN = 21
_HELPER_CAPABILITY_MASK = (1 << _CAP_SYS_CHROOT) | (1 << _CAP_SYS_ADMIN)
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NONCE_RE = re.compile(r"[0-9a-f]{64}\Z")
_T = TypeVar("_T", bound=BaseModel)


class G4BindMountAttackError(RuntimeError):
    """The closed G4 bind-replacement protocol failed."""


class G4BindMountCleanupError(G4BindMountAttackError):
    """A named helper or subject container could not be removed."""

    def __init__(self, container_name: str, detail: str) -> None:
        self.container_name = container_name
        self.detail = detail
        super().__init__(f"cleanup failed for {container_name}: {detail}")


class G4BindMountExecutionError(G4BindMountAttackError):
    """A subject or helper container reported an execution failure."""

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        super().__init__(f"container execution failed with exit code {exit_code}")


class G4BindMountOrchestrationError(G4BindMountAttackError):
    """Report closed cleanup state without rendering the retained cause."""

    def __init__(
        self,
        primary_error: BaseException | None,
        cleanup_failures: list[G4BindMountCleanupError],
        *,
        primary_reason: Literal[
            "execution_exception",
            "container_exit",
            "cleanup_interrupted",
            "cleanup_failed",
        ],
    ) -> None:
        self.primary_error = primary_error
        self.primary_reason = primary_reason
        self.cleanup_failures = tuple(cleanup_failures)
        exit_detail = ""
        if (
            primary_reason == "container_exit"
            and isinstance(primary_error, G4BindMountExecutionError)
            and 0 <= primary_error.exit_code <= 255
        ):
            exit_detail = f"; exit_code={primary_error.exit_code}"
        super().__init__(
            f"G4 orchestration failed: reason={primary_reason}"
            f"{exit_detail}; cleanup_failures={len(cleanup_failures)}"
        )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_digest(value: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("digest must be lowercase sha256")
    return value


def _validate_nonce(value: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        raise ValueError("nonce must be 32 lowercase hexadecimal bytes")
    return value


def _validate_absolute_path(value: str) -> str:
    if type(value) is not str or not value.startswith("/"):
        raise ValueError("path must be absolute")
    encoded = os.fsencode(value)
    if not encoded or len(encoded) > _MAX_PATH_BYTES or b"\x00" in encoded:
        raise ValueError("path is outside the bounded path contract")
    if os.path.normpath(value) != value or value == "/":
        raise ValueError("path must be normalized and non-root")
    return value


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.model_dump(mode="json"))


class NodeIdentity(_ExactModel):
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    file_type: Literal["directory", "regular"]


class NamespaceIdentity(_ExactModel):
    device: int = Field(ge=0)
    inode: int = Field(gt=0)


class PeerIdentity(_ExactModel):
    pid: int = Field(gt=0)
    uid: int = Field(ge=0)
    gid: int = Field(ge=0)
    starttime: str = Field(pattern=r"[0-9]+")


class BindReplaceManifest(_ExactModel):
    schema_version: Literal["bb.rl.g4-bind-replace-manifest.v1"]
    operation: Literal["bind_replace"]
    subject_pid: int = Field(gt=0)
    subject_starttime: str = Field(pattern=r"[0-9]+")
    subject_mount_namespace: NamespaceIdentity
    source_path: str
    target_path: str
    source_before: NodeIdentity
    target_before: NodeIdentity
    expected_peer: PeerIdentity
    nonce: str
    request_digest: str
    deadline_unix_ns: int = Field(gt=0)
    socket_path: str
    state_path: str
    socket_directory: NamespaceIdentity
    state_directory: NamespaceIdentity

    _paths = field_validator("source_path", "target_path", "socket_path", "state_path")(
        _validate_absolute_path
    )
    _nonce = field_validator("nonce")(_validate_nonce)
    _request_digest = field_validator("request_digest")(_validate_digest)

    @model_validator(mode="after")
    def exact_bind_replacement(self) -> "BindReplaceManifest":
        if self.source_path == self.target_path:
            raise ValueError("source and target must differ")
        if self.source_before.file_type != "directory" or self.target_before.file_type != "directory":
            raise ValueError("bind replacement requires directory nodes")
        if self.source_before.device == self.target_before.device:
            raise ValueError("bind replacement source must be on a distinct device")
        if self.expected_peer.pid != self.subject_pid:
            raise ValueError("peer PID must be the preauthorized subject PID")
        if self.expected_peer.starttime != self.subject_starttime:
            raise ValueError("peer starttime must bind the subject identity")
        if Path(self.socket_path).parent == Path(self.state_path).parent:
            raise ValueError(
                "socket transport and helper-private state must be separate"
            )
        expected = bind_replace_request_digest(self.nonce)
        if self.request_digest != expected:
            raise ValueError("request digest does not bind the fixed operation and nonce")
        return self

    @property
    def digest(self) -> str:
        return _digest(self.canonical_bytes())


class BindReplaceChallenge(_ExactModel):
    schema_version: Literal["bb.rl.g4-bind-replace-challenge.v1"]
    status: Literal["ready"]
    operation: Literal["bind_replace"]
    nonce: str
    request_digest: str
    manifest_digest: str

    _nonce = field_validator("nonce")(_validate_nonce)
    _digests = field_validator("request_digest", "manifest_digest")(_validate_digest)

    @model_validator(mode="after")
    def request_binding_is_exact(self) -> "BindReplaceChallenge":
        if self.request_digest != bind_replace_request_digest(self.nonce):
            raise ValueError("challenge request digest mismatch")
        return self


class BindReplaceRequest(_ExactModel):
    schema_version: Literal["bb.rl.g4-bind-replace-request.v1"]
    operation: Literal["bind_replace"]
    nonce: str
    request_digest: str
    manifest_digest: str

    _nonce = field_validator("nonce")(_validate_nonce)
    _digests = field_validator("request_digest", "manifest_digest")(_validate_digest)


class BindReplaceAck(_ExactModel):
    schema_version: Literal["bb.rl.g4-bind-replace-ack.v1"]
    status: Literal["accepted"]
    operation: Literal["bind_replace"]
    nonce: str
    request_digest: str
    manifest_digest: str
    result_digest: str

    _nonce = field_validator("nonce")(_validate_nonce)
    _digests = field_validator("request_digest", "manifest_digest", "result_digest")(
        _validate_digest
    )


class BindReplaceResult(_ExactModel):
    schema_version: Literal["bb.rl.g4-bind-replace-result.v1"]
    status: Literal["ok"]
    operation: Literal["bind_replace"]
    nonce: str
    request_digest: str
    manifest_digest: str
    peer: PeerIdentity
    mount_namespace: NamespaceIdentity
    source_before: NodeIdentity
    target_before: NodeIdentity
    target_after: NodeIdentity
    result_digest: str

    _nonce = field_validator("nonce")(_validate_nonce)
    _digests = field_validator("request_digest", "manifest_digest", "result_digest")(
        _validate_digest
    )

    @model_validator(mode="after")
    def digest_is_exact(self) -> "BindReplaceResult":
        document = self.model_dump(mode="json", exclude={"result_digest"})
        if self.result_digest != _digest(_canonical_bytes(document)):
            raise ValueError("result digest mismatch")
        return self


_FailureCode = Literal[
    "deadline",
    "helper_internal",
    "mount_failed",
    "namespace_drift",
    "node_drift",
    "peer_mismatch",
    "postcondition_failed",
    "protocol_invalid",
    "replay",
    "result_unacknowledged",
]


class BindReplaceFailure(_ExactModel):
    schema_version: Literal["bb.rl.g4-bind-replace-failure.v1"]
    status: Literal["failed"]
    operation: Literal["bind_replace"]
    nonce: str
    request_digest: str
    manifest_digest: str
    error_code: _FailureCode
    message: str = Field(min_length=1, max_length=256)
    result_digest: str

    _nonce = field_validator("nonce")(_validate_nonce)
    _digests = field_validator("request_digest", "manifest_digest", "result_digest")(
        _validate_digest
    )

    @model_validator(mode="after")
    def digest_is_exact(self) -> "BindReplaceFailure":
        document = self.model_dump(mode="json", exclude={"result_digest"})
        if self.result_digest != _digest(_canonical_bytes(document)):
            raise ValueError("failure digest mismatch")
        return self


class _ProtocolFailure(Exception):
    def __init__(self, code: _FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:256] or code


def bind_replace_request_digest(nonce: str) -> str:
    _validate_nonce(nonce)
    return _digest(
        _canonical_bytes(
            {
                "nonce": nonce,
                "operation": "bind_replace",
                "schema_version": "bb.rl.g4-bind-replace-request-binding.v1",
            }
        )
    )


def _parse_exact(payload: bytes, model: type[_T]) -> _T:
    if not payload or len(payload) > _MAX_DOCUMENT_BYTES:
        raise ValueError("protocol document exceeds its bounded size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("protocol document has a duplicate member")
            result[key] = value
        return result

    value = json.loads(
        payload,
        object_pairs_hook=pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(
            ValueError("protocol document contains a non-finite value")
        ),
    )
    if _canonical_bytes(value) != payload:
        raise ValueError("protocol document is not canonical JSON")
    return model.model_validate(value, strict=True)


def _read_exact(path: Path, *, model: type[_T]) -> _T:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_DOCUMENT_BYTES:
            raise ValueError("protocol file is not a bounded regular file")
        payload = os.read(descriptor, _MAX_DOCUMENT_BYTES + 1)
        if len(payload) != metadata.st_size:
            raise ValueError("protocol file changed while it was read")
    finally:
        os.close(descriptor)
    return _parse_exact(payload, model)


def load_manifest(path: Path, *, expected_digest: str | None = None) -> BindReplaceManifest:
    manifest = _read_exact(path, model=BindReplaceManifest)
    if expected_digest is not None and manifest.digest != _validate_digest(expected_digest):
        raise G4BindMountAttackError("manifest digest mismatch")
    return manifest


def _write_exclusive(path: Path, payload: bytes) -> None:
    if len(payload) > _MAX_DOCUMENT_BYTES:
        raise ValueError("protocol document exceeds its bounded size")
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short protocol file write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _file_type(mode: int) -> Literal["directory", "regular"]:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "regular"
    raise ValueError("only regular files and directories are valid nodes")


def _node_identity(path: Path) -> NodeIdentity:
    metadata = path.stat(follow_symlinks=False)
    return NodeIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        file_type=_file_type(metadata.st_mode),
    )


def _namespace_identity(path: Path) -> NamespaceIdentity:
    metadata = path.stat(follow_symlinks=False)
    return NamespaceIdentity(device=metadata.st_dev, inode=metadata.st_ino)



def _capability_masks(status: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in status.splitlines():
        name, separator, value = line.partition(":")
        if separator and name in {"CapEff", "CapPrm"}:
            if name in values:
                raise ValueError(f"duplicate {name} in process status")
            values[name] = int(value.strip(), 16)
    if values.keys() != {"CapEff", "CapPrm"}:
        raise ValueError("process status is missing CapEff or CapPrm")
    return values["CapEff"], values["CapPrm"]


def _require_exact_helper_capabilities() -> None:
    effective, permitted = _capability_masks(
        Path("/proc/self/status").read_text(encoding="ascii")
    )
    if (
        effective != _HELPER_CAPABILITY_MASK
        or permitted != _HELPER_CAPABILITY_MASK
    ):
        raise G4BindMountAttackError(
            "helper requires exactly CAP_SYS_ADMIN and CAP_SYS_CHROOT"
        )


def _proc_starttime(pid: int) -> str:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    if len(fields) < 22 or not fields[21].isdigit():
        raise ValueError("subject process starttime is unavailable")
    return fields[21]


def _subject_path(subject_pid: int, path: str) -> Path:
    validated = _validate_absolute_path(path)
    return Path(f"/proc/{subject_pid}/root") / validated.removeprefix("/")


def _subject_node_identity(subject_pid: int, path: str) -> NodeIdentity:
    return _node_identity(_subject_path(subject_pid, path))


def _relative_subject_path(path: str) -> str:
    return "./" + _validate_absolute_path(path).removeprefix("/")


def _relative_node_identity(path: str) -> NodeIdentity:
    return _node_identity(Path(_relative_subject_path(path)))


def _peer_identity(connection: socket.socket) -> PeerIdentity:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise _ProtocolFailure("peer_mismatch", "Linux SO_PEERCRED is unavailable")
    payload = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", payload)
    return PeerIdentity(pid=pid, uid=uid, gid=gid, starttime=_proc_starttime(pid))


def _open_protocol_socket() -> socket.socket:
    return socket.socket(
        socket.AF_UNIX,
        socket.SOCK_SEQPACKET | getattr(socket, "SOCK_CLOEXEC", 0),
    )


def _libc_call(name: str, *arguments: Any) -> None:
    function = getattr(ctypes.CDLL(None, use_errno=True), name)
    function.restype = ctypes.c_int
    if function(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))




def _linux_syscall(number: int, *arguments: Any) -> int:
    syscall = ctypes.CDLL(None, use_errno=True).syscall
    syscall.restype = ctypes.c_long
    result = syscall(ctypes.c_long(number), *arguments)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return int(result)


def _require_new_mount_api() -> None:
    probes = (
        (
            _SYS_OPEN_TREE,
            (
                ctypes.c_int(-1),
                ctypes.c_char_p(b""),
                ctypes.c_uint(
                    _AT_EMPTY_PATH | _OPEN_TREE_CLONE | _OPEN_TREE_CLOEXEC
                ),
            ),
        ),
        (
            _SYS_MOVE_MOUNT,
            (
                ctypes.c_int(-1),
                ctypes.c_char_p(b""),
                ctypes.c_int(-1),
                ctypes.c_char_p(b""),
                ctypes.c_uint(
                    _MOVE_MOUNT_F_EMPTY_PATH | _MOVE_MOUNT_T_EMPTY_PATH
                ),
            ),
        ),
    )
    for number, arguments in probes:
        try:
            _linux_syscall(number, *arguments)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                continue
            raise G4BindMountAttackError(
                "open_tree/move_mount unavailable or blocked"
            ) from exc
        raise G4BindMountAttackError(
            "new mount API capability probe unexpectedly succeeded"
        )


class _OpenHow(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


def _openat2_path(root_fd: int, path: str) -> int:
    relative = _validate_absolute_path(path).removeprefix("/")
    how = _OpenHow(
        flags=getattr(os, "O_PATH", 0x200000) | getattr(os, "O_CLOEXEC", 0),
        mode=0,
        resolve=(
            _RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_MAGICLINKS
        ),
    )
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    descriptor = syscall(
        ctypes.c_long(_SYS_OPENAT2),
        ctypes.c_int(root_fd),
        ctypes.c_char_p(os.fsencode(relative)),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return int(descriptor)


def _node_identity_fd(descriptor: int) -> NodeIdentity:
    metadata = os.fstat(descriptor)
    return NodeIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        file_type=_file_type(metadata.st_mode),
    )


def _validate_pinned_path(
    root_fd: int,
    path: str,
    pinned_fd: int,
    expected: NodeIdentity,
) -> None:
    current_fd = _openat2_path(root_fd, path)
    try:
        if (
            _node_identity_fd(pinned_fd) != expected
            or _node_identity_fd(current_fd) != expected
        ):
            raise _ProtocolFailure(
                "node_drift", "pinned node no longer occupies its authorized path"
            )
    finally:
        os.close(current_fd)


def _proc_fd_path(descriptor: int) -> bytes:
    if descriptor < 0:
        raise ValueError("descriptor must be nonnegative")
    return os.fsencode(f"./proc/self/fd/{descriptor}")


def _setns_exact(subject_pid: int, expected: NamespaceIdentity) -> int:
    namespace_path = Path(f"/proc/{subject_pid}/ns/mnt")
    namespace_fd = os.open(
        namespace_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    )
    root_fd = os.open(
        Path(f"/proc/{subject_pid}/root"),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        observed = _namespace_identity(namespace_path)
        if observed != expected:
            raise _ProtocolFailure(
                "namespace_drift", "subject mount namespace drifted before setns"
            )
        _libc_call("setns", ctypes.c_int(namespace_fd), ctypes.c_int(0))
        os.fchdir(root_fd)
        namespace_metadata = os.fstat(namespace_fd)
        if (
            namespace_metadata.st_dev != expected.device
            or namespace_metadata.st_ino != expected.inode
        ):
            raise _ProtocolFailure(
                "namespace_drift", "entered mount namespace identity changed"
            )
        return root_fd
    except BaseException:
        os.close(root_fd)
        raise
    finally:
        os.close(namespace_fd)


def _open_tree_clone(source_fd: int) -> int:
    return _linux_syscall(
        _SYS_OPEN_TREE,
        ctypes.c_int(source_fd),
        ctypes.c_char_p(b""),
        ctypes.c_uint(
            _AT_EMPTY_PATH | _OPEN_TREE_CLONE | _OPEN_TREE_CLOEXEC
        ),
    )


def _attach_mount_tree(tree_fd: int, target_fd: int) -> None:
    _linux_syscall(
        _SYS_MOVE_MOUNT,
        ctypes.c_int(tree_fd),
        ctypes.c_char_p(b""),
        ctypes.c_int(target_fd),
        ctypes.c_char_p(b""),
        ctypes.c_uint(
            _MOVE_MOUNT_F_EMPTY_PATH | _MOVE_MOUNT_T_EMPTY_PATH
        ),
    )


def _unmount_attached_mount_fd(tree_fd: int) -> None:
    _libc_call(
        "umount2",
        ctypes.c_char_p(_proc_fd_path(tree_fd)),
        ctypes.c_int(_MNT_DETACH),
    )


def _fsync_pinned_node(descriptor: int) -> None:
    sync_fd = os.open(
        os.fsdecode(_proc_fd_path(descriptor)),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(sync_fd)
    finally:
        os.close(sync_fd)


def _remaining_seconds(deadline_unix_ns: int) -> float:
    remaining = (deadline_unix_ns - time.time_ns()) / 1_000_000_000
    if remaining <= 0:
        raise _ProtocolFailure("deadline", "bind-replacement deadline expired")
    return remaining


def _send_packet(connection: socket.socket, document: _ExactModel) -> None:
    payload = document.canonical_bytes()
    if len(payload) > _MAX_DOCUMENT_BYTES:
        raise ValueError("protocol result exceeds its bounded size")
    connection.sendall(payload)


def _receive_packet(connection: socket.socket) -> bytes:
    payload, _ancillary, flags, _address = connection.recvmsg(_MAX_DOCUMENT_BYTES + 1)
    if flags & getattr(socket, "MSG_TRUNC", 0) or not payload or len(payload) > _MAX_DOCUMENT_BYTES:
        raise ValueError("protocol packet is empty or truncated")
    return payload


def _failure(manifest: BindReplaceManifest, code: _FailureCode, message: str) -> BindReplaceFailure:
    document: dict[str, Any] = {
        "schema_version": "bb.rl.g4-bind-replace-failure.v1",
        "status": "failed",
        "operation": "bind_replace",
        "nonce": manifest.nonce,
        "request_digest": manifest.request_digest,
        "manifest_digest": manifest.digest,
        "error_code": code,
        "message": message[:256] or code,
    }
    document["result_digest"] = _digest(_canonical_bytes(document))
    return BindReplaceFailure.model_validate(document, strict=True)


def _challenge(manifest: BindReplaceManifest) -> BindReplaceChallenge:
    return BindReplaceChallenge(
        schema_version="bb.rl.g4-bind-replace-challenge.v1",
        status="ready",
        operation="bind_replace",
        nonce=manifest.nonce,
        request_digest=manifest.request_digest,
        manifest_digest=manifest.digest,
    )


def _success(
    manifest: BindReplaceManifest,
    peer: PeerIdentity,
    target_after: NodeIdentity,
) -> BindReplaceResult:
    document: dict[str, Any] = {
        "schema_version": "bb.rl.g4-bind-replace-result.v1",
        "status": "ok",
        "operation": "bind_replace",
        "nonce": manifest.nonce,
        "request_digest": manifest.request_digest,
        "manifest_digest": manifest.digest,
        "peer": peer.model_dump(mode="json"),
        "mount_namespace": manifest.subject_mount_namespace.model_dump(mode="json"),
        "source_before": manifest.source_before.model_dump(mode="json"),
        "target_before": manifest.target_before.model_dump(mode="json"),
        "target_after": target_after.model_dump(mode="json"),
    }
    document["result_digest"] = _digest(_canonical_bytes(document))
    return BindReplaceResult.model_validate(document, strict=True)


def _directory_identity_fd(descriptor: int) -> NamespaceIdentity:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("pinned control descriptor is not a directory")
    return NamespaceIdentity(device=metadata.st_dev, inode=metadata.st_ino)


def _open_pinned_directory(path: Path, expected: NamespaceIdentity) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    if _directory_identity_fd(descriptor) != expected:
        os.close(descriptor)
        raise G4BindMountAttackError("control directory identity drifted")
    return descriptor


def _write_exclusive_at(directory_fd: int, name: str, payload: bytes) -> None:
    if Path(name).name != name or not name or len(payload) > _MAX_DOCUMENT_BYTES:
        raise ValueError("private state name or payload is invalid")
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short private state write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)


def _bind_socket_at(
    listener: socket.socket,
    directory_fd: int,
    name: str,
) -> None:
    if Path(name).name != name or not name:
        raise ValueError("socket basename is invalid")
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise G4BindMountAttackError("broker socket path already exists")
    cwd_fd = os.open(".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fchdir(directory_fd)
        listener.bind("./" + name)
        os.chmod(name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISSOCK(metadata.st_mode):
            raise G4BindMountAttackError("broker socket creation was replaced")
    finally:
        os.fchdir(cwd_fd)
        os.close(cwd_fd)


def _consume_nonce(
    manifest: BindReplaceManifest, state_directory_fd: int
) -> None:
    try:
        _write_exclusive_at(
            state_directory_fd,
            Path(manifest.state_path).name,
            _canonical_bytes(
                {
                    "manifest_digest": manifest.digest,
                    "nonce": manifest.nonce,
                    "operation": manifest.operation,
                    "request_digest": manifest.request_digest,
                    "schema_version": "bb.rl.g4-bind-replace-consumption.v1",
                }
            ),
        )
    except FileExistsError as exc:
        raise _ProtocolFailure(
            "replay", "bind-replacement nonce was already consumed"
        ) from exc
def _validate_request(
    payload: bytes, manifest: BindReplaceManifest
) -> BindReplaceRequest:
    try:
        request = _parse_exact(payload, BindReplaceRequest)
    except (ValueError, TypeError) as exc:
        raise _ProtocolFailure(
            "protocol_invalid", "request is not the closed canonical schema"
        ) from exc
    if (
        request.operation != manifest.operation
        or request.nonce != manifest.nonce
        or request.request_digest != manifest.request_digest
        or request.manifest_digest != manifest.digest
    ):
        raise _ProtocolFailure(
            "protocol_invalid", "request binding does not match the manifest"
        )
    return request


def _validate_nodes(manifest: BindReplaceManifest) -> None:
    if _subject_node_identity(manifest.subject_pid, manifest.source_path) != manifest.source_before:
        raise _ProtocolFailure("node_drift", "bind source identity drifted")
    if _subject_node_identity(manifest.subject_pid, manifest.target_path) != manifest.target_before:
        raise _ProtocolFailure("node_drift", "bind target identity drifted")




def _serve_connection(
    connection: socket.socket,
    manifest: BindReplaceManifest,
    state_directory_fd: int,
) -> BindReplaceResult:
    peer = _peer_identity(connection)
    if peer != manifest.expected_peer:
        raise _ProtocolFailure(
            "peer_mismatch", "request peer does not match the pinned subject"
        )
    if _proc_starttime(manifest.subject_pid) != manifest.subject_starttime:
        raise _ProtocolFailure("peer_mismatch", "subject process identity drifted")
    if (
        _namespace_identity(Path(f"/proc/{manifest.subject_pid}/ns/mnt"))
        != manifest.subject_mount_namespace
    ):
        raise _ProtocolFailure("namespace_drift", "subject mount namespace drifted")
    connection.settimeout(_remaining_seconds(manifest.deadline_unix_ns))
    _send_packet(connection, _challenge(manifest))
    _validate_request(_receive_packet(connection), manifest)
    _remaining_seconds(manifest.deadline_unix_ns)
    _validate_nodes(manifest)
    _consume_nonce(manifest, state_directory_fd)
    root_fd = _setns_exact(
        manifest.subject_pid, manifest.subject_mount_namespace
    )
    source_fd = -1
    target_fd = -1
    tree_fd = -1
    mounted = False
    try:
        _remaining_seconds(manifest.deadline_unix_ns)
        try:
            source_fd = _openat2_path(root_fd, manifest.source_path)
            target_fd = _openat2_path(root_fd, manifest.target_path)
        except OSError as exc:
            raise _ProtocolFailure(
                "node_drift", "authorized path could not be pinned with openat2"
            ) from exc
        if (
            _node_identity_fd(source_fd) != manifest.source_before
            or _node_identity_fd(target_fd) != manifest.target_before
        ):
            raise _ProtocolFailure(
                "node_drift", "openat2-pinned node identity drifted"
            )
        _validate_pinned_path(
            root_fd,
            manifest.source_path,
            source_fd,
            manifest.source_before,
        )
        _validate_pinned_path(
            root_fd,
            manifest.target_path,
            target_fd,
            manifest.target_before,
        )
        try:
            tree_fd = _open_tree_clone(source_fd)
        except OSError as exc:
            raise _ProtocolFailure(
                "mount_failed", "descriptor-bound open_tree clone failed"
            ) from exc
        _validate_pinned_path(
            root_fd,
            manifest.source_path,
            source_fd,
            manifest.source_before,
        )
        _validate_pinned_path(
            root_fd,
            manifest.target_path,
            target_fd,
            manifest.target_before,
        )
        try:
            _attach_mount_tree(tree_fd, target_fd)
        except OSError as exc:
            raise _ProtocolFailure(
                "mount_failed", "descriptor-bound move_mount attach failed"
            ) from exc
        mounted = True
        source_post_fd = -1
        target_post_fd = -1
        try:
            source_post_fd = _openat2_path(root_fd, manifest.source_path)
            target_post_fd = _openat2_path(root_fd, manifest.target_path)
        except OSError as exc:
            if target_post_fd >= 0:
                os.close(target_post_fd)
            if source_post_fd >= 0:
                os.close(source_post_fd)
            raise _ProtocolFailure(
                "postcondition_failed",
                "authorized path changed during descriptor mount attach",
            ) from exc
        try:
            _fsync_pinned_node(source_post_fd)
            _fsync_pinned_node(target_post_fd)
            source_after = _node_identity_fd(source_post_fd)
            target_after = _node_identity_fd(target_post_fd)
        finally:
            os.close(target_post_fd)
            os.close(source_post_fd)
        if (
            source_after != manifest.source_before
            or target_after != manifest.source_before
        ):
            raise _ProtocolFailure(
                "postcondition_failed",
                "mount replacement did not expose the exact distinct-device source identity",
            )
        result = _success(manifest, peer, target_after)
        _send_packet(connection, result)
        connection.settimeout(_remaining_seconds(manifest.deadline_unix_ns))
        try:
            ack = _parse_exact(_receive_packet(connection), BindReplaceAck)
        except (ValueError, TypeError, OSError, TimeoutError) as exc:
            raise _ProtocolFailure(
                "result_unacknowledged",
                "client did not acknowledge the exact result",
            ) from exc
        if (
            ack.operation != manifest.operation
            or ack.nonce != manifest.nonce
            or ack.request_digest != manifest.request_digest
            or ack.manifest_digest != manifest.digest
            or ack.result_digest != result.result_digest
        ):
            raise _ProtocolFailure(
                "result_unacknowledged", "client acknowledgment binding mismatch"
            )
        mounted = False
        return result
    finally:
        try:
            if mounted and tree_fd >= 0:
                _unmount_attached_mount_fd(tree_fd)
        finally:
            if tree_fd >= 0:
                os.close(tree_fd)
            if target_fd >= 0:
                os.close(target_fd)
            if source_fd >= 0:
                os.close(source_fd)
            os.close(root_fd)


def serve_manifest(
    manifest: BindReplaceManifest,
    *,
    creator_bytes: bytes,
    creator_digest: str,
) -> BindReplaceResult | BindReplaceFailure:
    if sys.platform != "linux":
        raise G4BindMountAttackError("the bind-replacement helper requires Linux")
    _require_exact_helper_capabilities()
    _require_new_mount_api()
    if (
        manifest.canonical_bytes() != creator_bytes
        or manifest.digest != _validate_digest(creator_digest)
    ):
        raise G4BindMountAttackError("creator-pinned manifest binding mismatch")
    listener = _open_protocol_socket()
    connection: socket.socket | None = None
    socket_path = Path(manifest.socket_path)
    state_path = Path(manifest.state_path)
    socket_directory_fd = -1
    state_directory_fd = -1
    socket_bound = False
    try:
        socket_directory_fd = _open_pinned_directory(
            socket_path.parent, manifest.socket_directory
        )
        state_directory_fd = _open_pinned_directory(
            state_path.parent, manifest.state_directory
        )
        _bind_socket_at(listener, socket_directory_fd, socket_path.name)
        socket_bound = True
        listener.listen(1)
        listener.settimeout(_remaining_seconds(manifest.deadline_unix_ns))
        try:
            connection, _address = listener.accept()
        except socket.timeout:
            return _failure(
                manifest, "deadline", "no client connected before the deadline"
            )
        try:
            return _serve_connection(connection, manifest, state_directory_fd)
        except _ProtocolFailure as exc:
            failure = _failure(manifest, exc.code, exc.message)
        except (OSError, ValueError, TypeError) as exc:
            failure = _failure(manifest, "helper_internal", type(exc).__name__)
        try:
            _send_packet(connection, failure)
        except OSError:
            pass
        return failure
    finally:
        if connection is not None:
            connection.close()
        listener.close()
        try:
            if socket_bound:
                os.unlink(socket_path.name, dir_fd=socket_directory_fd)
                os.fsync(socket_directory_fd)
        finally:
            if state_directory_fd >= 0:
                os.close(state_directory_fd)
            if socket_directory_fd >= 0:
                os.close(socket_directory_fd)


def serve_once(manifest_path: Path) -> BindReplaceResult | BindReplaceFailure:
    manifest = load_manifest(manifest_path)
    creator_bytes = manifest.canonical_bytes()
    return serve_manifest(
        manifest,
        creator_bytes=creator_bytes,
        creator_digest=_digest(creator_bytes),
    )


def request_preconfigured_bind_replace(
    socket_path: str | Path | None = None,
) -> BindReplaceResult:
    if sys.platform != "linux":
        raise G4BindMountAttackError("the bind-replacement client requires Linux")
    if socket_path is None:
        configured = os.environ.get("BREADBOARD_G4_BIND_MOUNT_ATTACK_SOCKET")
        if configured is None:
            raise G4BindMountAttackError(
                "external bind-replacement socket is required"
            )
        socket_path = configured
    socket_value = _validate_absolute_path(os.fspath(socket_path))
    wait_deadline = time.monotonic() + _CLIENT_WAIT_SECONDS
    connection = _open_protocol_socket()
    try:
        while True:
            try:
                connection.connect(socket_value)
                break
            except OSError as exc:
                if exc.errno not in {errno.ENOENT, errno.ECONNREFUSED}:
                    raise G4BindMountAttackError(
                        "external bind-replacement helper connection failed"
                    ) from exc
                if time.monotonic() >= wait_deadline:
                    raise G4BindMountAttackError(
                        "external bind-replacement helper deadline expired"
                    ) from exc
                time.sleep(0.01)
        connection.settimeout(max(0.001, wait_deadline - time.monotonic()))
        first_payload = _receive_packet(connection)
        try:
            first_value = json.loads(first_payload)
            first_status = (
                first_value.get("status") if type(first_value) is dict else None
            )
            if first_status == "failed":
                failure = _parse_exact(first_payload, BindReplaceFailure)
                raise G4BindMountAttackError(
                    f"{failure.error_code}: {failure.message}"
                )
            challenge = _parse_exact(first_payload, BindReplaceChallenge)
        except G4BindMountAttackError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise G4BindMountAttackError(
                "external helper challenge is invalid or tampered"
            ) from exc
        request = BindReplaceRequest(
            schema_version="bb.rl.g4-bind-replace-request.v1",
            operation="bind_replace",
            nonce=challenge.nonce,
            request_digest=challenge.request_digest,
            manifest_digest=challenge.manifest_digest,
        )
        _send_packet(connection, request)
        payload = _receive_packet(connection)
        try:
            value = json.loads(payload)
            status = value.get("status") if type(value) is dict else None
            if status == "failed":
                failure = _parse_exact(payload, BindReplaceFailure)
                if (
                    failure.manifest_digest != challenge.manifest_digest
                    or failure.request_digest != challenge.request_digest
                    or failure.nonce != challenge.nonce
                ):
                    raise ValueError("failure binding mismatch")
                raise G4BindMountAttackError(
                    f"{failure.error_code}: {failure.message}"
                )
            result = _parse_exact(payload, BindReplaceResult)
        except G4BindMountAttackError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise G4BindMountAttackError(
                "external helper result is invalid or tampered"
            ) from exc
        if (
            result.operation != challenge.operation
            or result.nonce != challenge.nonce
            or result.request_digest != challenge.request_digest
            or result.manifest_digest != challenge.manifest_digest
            or result.source_before.file_type != "directory"
            or result.target_before.file_type != "directory"
            or result.target_after != result.source_before
            or result.source_before.device == result.target_before.device
        ):
            raise G4BindMountAttackError("external helper result binding mismatch")
        ack = BindReplaceAck(
            schema_version="bb.rl.g4-bind-replace-ack.v1",
            status="accepted",
            operation="bind_replace",
            nonce=challenge.nonce,
            request_digest=challenge.request_digest,
            manifest_digest=challenge.manifest_digest,
            result_digest=result.result_digest,
        )
        _send_packet(connection, ack)
        return result
    except (OSError, socket.timeout) as exc:
        raise G4BindMountAttackError(
            "external bind-replacement helper died or timed out"
        ) from exc
    finally:
        connection.close()


def create_manifest(
    *,
    subject_pid: int,
    source_path: str,
    target_path: str,
    peer_uid: int,
    peer_gid: int,
    socket_path: str,
    state_path: str,
    deadline_unix_ns: int,
    nonce: str | None = None,
) -> BindReplaceManifest:
    nonce = secrets.token_hex(32) if nonce is None else nonce
    starttime = _proc_starttime(subject_pid)
    return BindReplaceManifest(
        schema_version="bb.rl.g4-bind-replace-manifest.v1",
        operation="bind_replace",
        subject_pid=subject_pid,
        subject_starttime=starttime,
        subject_mount_namespace=_namespace_identity(Path(f"/proc/{subject_pid}/ns/mnt")),
        source_path=source_path,
        target_path=target_path,
        source_before=_subject_node_identity(subject_pid, source_path),
        target_before=_subject_node_identity(subject_pid, target_path),
        expected_peer=PeerIdentity(
            pid=subject_pid,
            uid=peer_uid,
            gid=peer_gid,
            starttime=starttime,
        ),
        nonce=nonce,
        request_digest=bind_replace_request_digest(nonce),
        deadline_unix_ns=deadline_unix_ns,
        socket_path=socket_path,
        state_path=state_path,
        socket_directory=_namespace_identity(Path(socket_path).parent),
        state_directory=_namespace_identity(Path(state_path).parent),
    )


def prepare_and_serve(
    *,
    manifest_path: Path,
    subject_pid: int,
    source_path: str,
    target_path: str,
    peer_uid: int,
    peer_gid: int,
    socket_path: str,
    state_path: str,
    deadline_seconds: float,
) -> BindReplaceResult | BindReplaceFailure:
    deadline_unix_ns = time.time_ns() + int(deadline_seconds * 1_000_000_000)
    while True:
        try:
            manifest = create_manifest(
                subject_pid=subject_pid,
                source_path=source_path,
                target_path=target_path,
                peer_uid=peer_uid,
                peer_gid=peer_gid,
                socket_path=socket_path,
                state_path=state_path,
                deadline_unix_ns=deadline_unix_ns,
            )
            break
        except (FileNotFoundError, ProcessLookupError):
            if time.time_ns() >= deadline_unix_ns:
                raise G4BindMountAttackError("subject nodes did not appear before the deadline")
            time.sleep(0.01)
    creator_bytes = manifest.canonical_bytes()
    creator_digest = _digest(creator_bytes)
    _write_exclusive(manifest_path, creator_bytes)
    return serve_manifest(
        manifest,
        creator_bytes=creator_bytes,
        creator_digest=creator_digest,
    )


def _docker_run(arguments: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def orchestrate_target(
    *,
    docker: str,
    subject_image: str,
    helper_image: str,
    payload_host: Path,
    authority_host: Path,
    different_device_host: Path,
    timeout_seconds: float,
) -> int:
    """Run only the exact G4 node with SYS_ADMIN isolated to a one-shot helper.

    The subject is detached with Docker's default capability set plus
    CAP_LINUX_IMMUTABLE; it is never privileged and never receives SYS_ADMIN.
    The helper receives exactly SYS_ADMIN and SYS_CHROOT, joins the subject PID
    namespace, enters the manifest-pinned mount namespace with setns, and
    exposes only the closed bind_replace protocol over the shared control socket.
    """
    token = secrets.token_hex(8)
    subject_name = f"bb-g4-bind-subject-{token}"
    helper_name = f"bb-g4-bind-helper-{token}"
    node = "tests/rl/phase5/test_revocation_publication.py::test_privileged_linux_bind_mount_device_replacement_fails_live_and_restart"
    with ExitStack() as stack:
        transport = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(prefix="bb-g4-bind-socket-")
            )
        )
        private = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(prefix="bb-g4-bind-private-")
            )
        )
        os.chmod(transport, 0o755)
        os.chmod(private, 0o700)
        subject_command = [
            docker,
            "run",
            "-d",
            "--name",
            subject_name,
            "--network",
            "none",
            "--cap-add",
            "LINUX_IMMUTABLE",
            "--security-opt",
            "seccomp=unconfined",
            "--mount",
            f"type=bind,src={payload_host.resolve()},dst=/payload,readonly",
            "--mount",
            f"type=bind,src={authority_host.resolve()},dst=/authority",
            "--mount",
            f"type=bind,src={different_device_host.resolve()},dst=/different",
            "--mount",
            f"type=bind,src={transport.resolve()},dst=/g4-socket,readonly",
            "--env",
            "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_DEVICE_ROOT=/authority",
            "--env",
            "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_DEVICE_PARENT=/different",
            "--env",
            "BREADBOARD_G4_BIND_MOUNT_ATTACK_SOCKET=/g4-socket/broker.sock",
            "--workdir",
            "/payload",
            subject_image,
            "python",
            "-m",
            "pytest",
            "-q",
            node,
        ]
        helper_command = [
            docker,
            "run",
            "-d",
            "--name",
            helper_name,
            "--network",
            "none",
            "--pid",
            f"container:{subject_name}",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SYS_ADMIN",
            "--cap-add",
            "SYS_CHROOT",
            "--security-opt",
            "seccomp=unconfined",
            "--mount",
            f"type=bind,src={payload_host.resolve()},dst=/payload,readonly",
            "--mount",
            f"type=bind,src={transport.resolve()},dst=/g4-socket",
            "--mount",
            f"type=bind,src={private.resolve()},dst=/g4-private",
            "--workdir",
            "/payload",
            helper_image,
            "python",
            "scripts/rl_phase5/g4_bind_mount_attack.py",
            "helper",
            "--prepare",
            "--manifest",
            "/g4-private/manifest.json",
            "--socket",
            "/g4-socket/broker.sock",
            "--state",
            "/g4-private/consumed.json",
            "--subject-pid",
            "1",
            "--source",
            "/different/revocation-device-1",
            "--target",
            "/authority",
            "--peer-uid",
            "0",
            "--peer-gid",
            "0",
            "--deadline-seconds",
            str(timeout_seconds),
        ]
        cleanup_timeout = min(10.0, timeout_seconds)
        result_code = 1
        primary_error: BaseException | None = None
        try:
            _docker_run(subject_command, timeout=cleanup_timeout)
            _docker_run(helper_command, timeout=cleanup_timeout)
            subject_wait = _docker_run(
                [docker, "wait", subject_name], timeout=timeout_seconds
            )
            helper_wait = _docker_run(
                [docker, "wait", helper_name], timeout=cleanup_timeout
            )
            subject_code = int(subject_wait.stdout.strip())
            helper_code = int(helper_wait.stdout.strip())
            result_code = subject_code or helper_code
        except BaseException as exc:
            primary_error = exc

        cleanup_failures: list[G4BindMountCleanupError] = []
        cleanup_primary: BaseException | None = None
        for name in (helper_name, subject_name):
            try:
                cleanup = subprocess.run(
                    [docker, "rm", "-f", name],
                    check=False,
                    capture_output=True,
                    timeout=cleanup_timeout,
                )
            except BaseException as exc:
                if cleanup_primary is None:
                    cleanup_primary = exc
                detail = (
                    "timeout"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "cleanup_exception"
                    if isinstance(exc, Exception)
                    else "interrupted"
                )
                cleanup_failures.append(
                    G4BindMountCleanupError(name, detail)
                )
            else:
                if cleanup.returncode != 0:
                    cleanup_failures.append(
                        G4BindMountCleanupError(
                            name, f"docker rm returned {cleanup.returncode}"
                        )
                    )

        if primary_error is not None and not cleanup_failures:
            raise primary_error
        if cleanup_failures:
            aggregate_primary: BaseException | None
            primary_reason: Literal[
                "execution_exception",
                "container_exit",
                "cleanup_interrupted",
                "cleanup_failed",
            ]
            if primary_error is not None:
                aggregate_primary = primary_error
                primary_reason = "execution_exception"
            elif result_code != 0:
                aggregate_primary = G4BindMountExecutionError(result_code)
                primary_reason = "container_exit"
            elif cleanup_primary is not None:
                aggregate_primary = cleanup_primary
                primary_reason = "cleanup_interrupted"
            else:
                aggregate_primary = None
                primary_reason = "cleanup_failed"
            aggregate = G4BindMountOrchestrationError(
                aggregate_primary,
                cleanup_failures,
                primary_reason=primary_reason,
            )
            if aggregate_primary is not None:
                raise aggregate from aggregate_primary
            raise aggregate
        return result_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Closed one-shot G4 bind_replace broker. The target subject keeps "
            "CAP_LINUX_IMMUTABLE without SYS_ADMIN; only a separate helper may "
            "receive SYS_ADMIN and join the already-running subject PID namespace."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    helper = subparsers.add_parser(
        "helper",
        help="serve exactly one manifest-bound descriptor mount request and exit",
    )
    helper.add_argument("--manifest", type=Path, required=True)
    helper.add_argument("--prepare", action="store_true")
    helper.add_argument("--socket")
    helper.add_argument("--state")
    helper.add_argument("--subject-pid", type=int)
    helper.add_argument("--source")
    helper.add_argument("--target")
    helper.add_argument("--peer-uid", type=int)
    helper.add_argument("--peer-gid", type=int)
    helper.add_argument("--deadline-seconds", type=float, default=60.0)
    orchestrate = subparsers.add_parser(
        "orchestrate",
        help=(
            "run the exact node in a detached unprivileged subject and a "
            "helper-only SYS_ADMIN container with bounded cleanup"
        ),
    )
    orchestrate.add_argument("--docker", default="docker")
    orchestrate.add_argument("--subject-image", required=True)
    orchestrate.add_argument("--helper-image", required=True)
    orchestrate.add_argument("--payload-host", type=Path, required=True)
    orchestrate.add_argument("--authority-host", type=Path, required=True)
    orchestrate.add_argument("--different-device-host", type=Path, required=True)
    orchestrate.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "helper":
        if args.prepare:
            required = (
                args.socket,
                args.state,
                args.subject_pid,
                args.source,
                args.target,
                args.peer_uid,
                args.peer_gid,
            )
            if any(value is None for value in required):
                raise SystemExit("--prepare requires socket, state, subject, path, and peer pins")
            result = prepare_and_serve(
                manifest_path=args.manifest,
                subject_pid=args.subject_pid,
                source_path=args.source,
                target_path=args.target,
                peer_uid=args.peer_uid,
                peer_gid=args.peer_gid,
                socket_path=args.socket,
                state_path=args.state,
                deadline_seconds=args.deadline_seconds,
            )
        else:
            result = serve_once(args.manifest)
        sys.stdout.buffer.write(result.canonical_bytes() + b"\n")
        return 0 if isinstance(result, BindReplaceResult) else 1
    return orchestrate_target(
        docker=args.docker,
        subject_image=args.subject_image,
        helper_image=args.helper_image,
        payload_host=args.payload_host,
        authority_host=args.authority_host,
        different_device_host=args.different_device_host,
        timeout_seconds=args.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
