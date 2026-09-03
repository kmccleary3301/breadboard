from __future__ import annotations

import array
import ctypes
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.artifacts.cas import FilesystemCAS
from breadboard.rl.phase5.f2_authority_authoring import F2C4DynamicAuthorityInput
from breadboard.rl.phase5.f3_authority_authoring import F3AuthorityInput

_REVOCATION_SNAPSHOT_MEDIA_TYPE = (
    "application/vnd.breadboard.revocation-snapshot+json;version=1"
)
_MAX_RECORD_BYTES = 1024 * 1024
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OPERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HISTORY_NAME_RE = re.compile(r"([1-9][0-9]*)\.json\Z")
_POINTER_NAME_RE = re.compile(r"pointer-([1-9][0-9]*)\.json\Z")
_OPERATION_NAME_RE = re.compile(r"[0-9a-f]{64}\.json\Z")
_AUTHORITY_CONFIG_NAME = "authority.json"
_AUTHORITY_LOCK_NAME = "authority.lock"
_IMMUTABLE_TEMP_NAME_RE = re.compile(r"\.immutable\.[0-9a-f]{32}\.tmp\Z")
_ACTIVE_TEMP_NAME_RE = re.compile(
    r"\.active\.[0-9a-f]{32}\.(?:tmp|rollback)\Z"
)
_FS_IOC_GETFLAGS = 0x80086601
_FS_IOC_SETFLAGS = 0x40086602
_FS_IMMUTABLE_FL = 0x00000010
_FS_APPEND_FL = 0x00000020
_UINT64_MAX = 2**64 - 1
_PROC_STATUS_PATH = "/proc/self/status"
_T = TypeVar("_T", bound=BaseModel)


class RevocationPublicationConflictError(ValueError):
    """A publication lost generation CAS or reused an operation identity."""


class RevocationPublicationIntegrityError(ValueError):
    """Persisted revocation publication state failed exact verification."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _load_canonical_json(payload: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise RevocationPublicationIntegrityError(
                    "revocation record has a duplicate JSON member"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                RevocationPublicationIntegrityError(
                    "revocation record has a non-finite JSON value"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevocationPublicationIntegrityError(
            "revocation record is not canonical JSON"
        ) from exc
    if _canonical_bytes(value) != payload:
        raise RevocationPublicationIntegrityError(
            "revocation record is not canonical JSON"
        )
    return value


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_digest(value: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("digest must be lowercase sha256")
    return value


def _validate_uint64(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or not 0 <= value <= _UINT64_MAX):
        raise ValueError("epoch must be an exact uint64")
    return value


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.model_dump(mode="json"))


class RevocationAuthoringIdentity(_ExactModel):
    authority_kind: Literal["f2_c4_dynamic", "f3"]
    schema_version: str = Field(min_length=1)
    config_digest: str

    _digest = field_validator("config_digest")(_validate_digest)


_TrustedAuthoringInput = F2C4DynamicAuthorityInput | F3AuthorityInput


def revocation_authoring_identity(
    value: _TrustedAuthoringInput,
) -> RevocationAuthoringIdentity:
    if type(value) is F2C4DynamicAuthorityInput:
        kind: Literal["f2_c4_dynamic", "f3"] = "f2_c4_dynamic"
    elif type(value) is F3AuthorityInput:
        kind = "f3"
    else:
        raise TypeError("authoring predecessor is not a trusted F2/F3 authority")
    return RevocationAuthoringIdentity(
        authority_kind=kind,
        schema_version=value.schema_version,
        config_digest=_sha256(_canonical_bytes(value.model_dump(mode="json"))),
    )


def _trusted_authority_revocation(
    value: _TrustedAuthoringInput,
) -> c.RevocationBinding:
    if type(value) is F2C4DynamicAuthorityInput:
        return value.revocation
    if type(value) is F3AuthorityInput:
        return value.policy.revocation
    raise TypeError("authoring predecessor is not a trusted F2/F3 authority")


class RevocationSnapshotPublishRequest(_ExactModel):
    operation_id: str = Field(min_length=1, max_length=128)
    scope_digest: str
    expected_generation: int | None = Field(default=None, ge=1)
    expected_epoch: int | None = None
    binding: c.RevocationBinding
    predecessor_authority: F2C4DynamicAuthorityInput | F3AuthorityInput | None = None

    _scope = field_validator("scope_digest")(_validate_digest)
    _epoch = field_validator("expected_epoch")(_validate_uint64)

    @field_validator("operation_id")
    @classmethod
    def canonical_operation_id(cls, value: str) -> str:
        if _OPERATION_RE.fullmatch(value) is None:
            raise ValueError("operation_id is not canonical")
        return value

    @model_validator(mode="after")
    def exact_expectation(self) -> RevocationSnapshotPublishRequest:
        if self.binding.scope_digest != self.scope_digest:
            raise ValueError("revocation publication scope drift")
        if (self.expected_generation is None) != (self.expected_epoch is None):
            raise ValueError("generation and epoch expectations must both be present or absent")
        if self.expected_generation is None:
            if self.predecessor_authority is not None:
                raise ValueError("initial publication cannot carry a predecessor authority")
        elif self.predecessor_authority is None:
            raise ValueError("successor publication requires trusted predecessor authority")
        elif type(self.predecessor_authority) not in (
            F2C4DynamicAuthorityInput,
            F3AuthorityInput,
        ):
            raise TypeError("successor predecessor authority type is not trusted")
        return self

    @property
    def predecessor_identity(self) -> RevocationAuthoringIdentity | None:
        if self.predecessor_authority is None:
            return None
        return revocation_authoring_identity(self.predecessor_authority)

    @property
    def predecessor_model_type(self) -> str | None:
        identity = self.predecessor_identity
        return None if identity is None else identity.authority_kind

    @property
    def predecessor_schema_version(self) -> str | None:
        identity = self.predecessor_identity
        return None if identity is None else identity.schema_version

    @property
    def predecessor_config_digest(self) -> str | None:
        identity = self.predecessor_identity
        return None if identity is None else identity.config_digest

    def canonical_digest(self) -> str:
        return _sha256(self.canonical_bytes())
class MonotonicRevocationAuthorityConfig(_ExactModel):
    schema_version: Literal["bb.rl.monotonic-revocation-authority-config.v1"]
    authority_id: str

    _authority_id = field_validator("authority_id")(_validate_digest)


class MonotonicRevocationAuthorityIdentity(_ExactModel):
    schema_version: Literal["bb.rl.monotonic-revocation-authority-identity.v1"]
    authority_id: str
    root_device: int = Field(ge=0)
    root_inode: int = Field(ge=1)
    root_uid: int = Field(ge=0)
    root_gid: int = Field(ge=0)
    root_flags: int = Field(ge=0)
    config_device: int = Field(ge=0)
    config_inode: int = Field(ge=1)
    config_uid: int = Field(ge=0)
    config_gid: int = Field(ge=0)
    config_flags: int = Field(ge=0)
    lock_device: int = Field(ge=0)
    lock_inode: int = Field(ge=1)
    lock_uid: int = Field(ge=0)
    lock_gid: int = Field(ge=0)
    lock_flags: int = Field(ge=0)
    config_digest: str

    _digests = field_validator("authority_id", "config_digest")(_validate_digest)


class _KernelAuthorityFlags(Protocol):
    append_flag: int
    immutable_flag: int

    def read(self, fd: int) -> int: ...

    def set_immutable(self, fd: int) -> None: ...


class _DarwinKernelAuthorityFlags:
    def __init__(self) -> None:
        if not hasattr(stat, "SF_APPEND") or not hasattr(stat, "SF_IMMUTABLE"):
            raise RuntimeError("Darwin system file flags are unavailable")
        self.append_flag = stat.SF_APPEND
        self.immutable_flag = stat.SF_IMMUTABLE
        libc = ctypes.CDLL(None, use_errno=True)
        fchflags = libc.fchflags
        fchflags.argtypes = (ctypes.c_int, ctypes.c_uint)
        fchflags.restype = ctypes.c_int
        self._fchflags = fchflags

    def read(self, fd: int) -> int:
        return int(os.fstat(fd).st_flags)

    def set_immutable(self, fd: int) -> None:
        flags = self.read(fd) | self.immutable_flag
        if self._fchflags(fd, flags) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))


class _LinuxKernelAuthorityFlags:
    append_flag = _FS_APPEND_FL
    immutable_flag = _FS_IMMUTABLE_FL

    def read(self, fd: int) -> int:
        value = array.array("I", [0])
        fcntl.ioctl(fd, _FS_IOC_GETFLAGS, value, True)
        return int(value[0])

    def set_immutable(self, fd: int) -> None:
        current = self.read(fd)
        value = array.array("I", [current | self.immutable_flag])
        fcntl.ioctl(fd, _FS_IOC_SETFLAGS, value, True)


def _linux_has_immutable_capability() -> bool:
    try:
        with open(_PROC_STATUS_PATH, "rb") as status:
            payload = status.read(128 * 1024 + 1)
    except OSError:
        return False
    if len(payload) > 128 * 1024:
        return False
    matches: list[bytes] = []
    for line in payload.splitlines():
        match = re.fullmatch(rb"CapEff:\t([0-9A-Fa-f]{16})", line)
        if match is not None:
            matches.append(match.group(1))
        elif line.startswith(b"CapEff:"):
            return False
    if len(matches) != 1:
        return False
    return bool(int(matches[0], 16) & (1 << 9))


def _kernel_authority_flags() -> _KernelAuthorityFlags:
    if sys.platform == "darwin":
        return _DarwinKernelAuthorityFlags()
    if sys.platform == "linux":
        if not _linux_has_immutable_capability():
            raise PermissionError(
                "Linux monotonic authority requires effective CAP_LINUX_IMMUTABLE"
            )
        return _LinuxKernelAuthorityFlags()
    raise RuntimeError("no monotonic revocation authority for this platform")




class RevocationSnapshotPublishReceipt(_ExactModel):
    operation_id: str = Field(min_length=1, max_length=128)
    request_digest: str
    generation: int = Field(ge=1)
    previous_snapshot_ref: c.ArtifactRef | None
    snapshot_ref: c.ArtifactRef
    active_pointer_digest: str
    history_digest: str
    predecessor_model_type: str | None
    monotonic_authority: MonotonicRevocationAuthorityIdentity
    predecessor_schema_version: str | None
    predecessor_config_digest: str | None

    _digests = field_validator(
        "request_digest", "active_pointer_digest", "history_digest"
    )(_validate_digest)

    @field_validator("operation_id")
    @classmethod
    def canonical_operation_id(cls, value: str) -> str:
        if _OPERATION_RE.fullmatch(value) is None:
            raise ValueError("operation_id is not canonical")
        return value


class _SignedRecord(_ExactModel):
    signer_key_id: str = Field(min_length=1, max_length=256)
    signature_algorithm: str = Field(min_length=1, max_length=128)
    auth_digest: str
    signature: str = Field(pattern=r"[0-9a-f]+", min_length=2, max_length=4096)
    monotonic_authority: MonotonicRevocationAuthorityIdentity

    _auth = field_validator("auth_digest")(_validate_digest)


class _RevocationHistoryRecord(_SignedRecord):
    schema_version: Literal["bb.rl.revocation-history.v1"]
    operation_id: str
    request_digest: str
    generation: int = Field(ge=1)
    previous_snapshot_ref: c.ArtifactRef | None
    snapshot_ref: c.ArtifactRef
    binding: c.RevocationBinding
    previous_active_pointer_digest: str | None
    predecessor_model_type: str | None
    predecessor_schema_version: str | None
    predecessor_config_digest: str | None

    _request_digest = field_validator("request_digest")(_validate_digest)

    @field_validator("previous_active_pointer_digest")
    @classmethod
    def optional_previous_digest(cls, value: str | None) -> str | None:
        return None if value is None else _validate_digest(value)


class _ActiveRevocationPointer(_SignedRecord):
    schema_version: Literal["bb.rl.active-revocation-pointer.v1"]
    operation_id: str
    request_digest: str
    generation: int = Field(ge=1)
    binding: c.RevocationBinding
    snapshot_ref: c.ArtifactRef
    history_digest: str

    _digests = field_validator("request_digest", "history_digest")(_validate_digest)


class _RevocationHighWaterRecord(_SignedRecord):
    schema_version: Literal["bb.rl.revocation-high-water.v1"]
    generation: int = Field(ge=1)
    request_digest: str
    active_pointer_digest: str
    history_digest: str

    _digests = field_validator(
        "request_digest", "active_pointer_digest", "history_digest"
    )(_validate_digest)
class MonotonicRevocationWitness(_ExactModel):
    monotonic_authority: MonotonicRevocationAuthorityIdentity
    generation: int = Field(ge=1)
    record_digest: str

    _digest = field_validator("record_digest")(_validate_digest)


class PreprovisionedAppendOnlyMonotonicRevocationAuthority:
    """Root-owned kernel-enforced append-only generation authority.

    The root, canonical authority config, and common flock inode must be
    provisioned and deletion-protected before construction. Darwin uses system
    file flags; Linux uses the file-attribute ioctls. No ordinary filesystem
    implementation is accepted by the production publisher.
    """

    def __init__(self, root: str | Path) -> None:
        self._kernel_flags = _kernel_authority_flags()
        requested = Path(root)
        if (
            not requested.is_absolute()
            or not os.path.lexists(requested)
            or os.path.normpath(os.fspath(requested)) != os.fspath(requested)
            or requested.resolve(strict=True) != requested
        ):
            raise ValueError("monotonic authority root must be pre-provisioned")
        observed = os.stat(requested, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != 0:
            raise ValueError(
                "monotonic authority requires a root-owned authority directory"
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        self._root = requested
        self._root_fd = os.open(
            requested,
            flags | getattr(os, "O_DIRECTORY", 0),
        )
        self._config_fd = -1
        self._lock_fd = -1
        try:
            self._config_fd = os.open(
                _AUTHORITY_CONFIG_NAME, flags, dir_fd=self._root_fd
            )
            self._lock_fd = os.open(
                _AUTHORITY_LOCK_NAME, flags, dir_fd=self._root_fd
            )
            root_stat = os.fstat(self._root_fd)
            config_stat = os.fstat(self._config_fd)
            lock_stat = os.fstat(self._lock_fd)
            root_flags = self._kernel_flags.read(self._root_fd)
            config_flags = self._kernel_flags.read(self._config_fd)
            lock_flags = self._kernel_flags.read(self._lock_fd)
            if (
                not stat.S_ISDIR(root_stat.st_mode)
                or root_stat.st_uid != 0
                or not root_flags & self._kernel_flags.append_flag
                or not stat.S_ISREG(config_stat.st_mode)
                or not stat.S_ISREG(lock_stat.st_mode)
                or config_stat.st_uid != 0
                or lock_stat.st_uid != 0
                or config_stat.st_nlink != 1
                or lock_stat.st_nlink != 1
                or not config_flags & self._kernel_flags.immutable_flag
                or not lock_flags & self._kernel_flags.immutable_flag
            ):
                raise RevocationPublicationIntegrityError(
                    "monotonic authority metadata or kernel flags are not exact"
                )
            config_payload = self._read_descriptor(self._config_fd)
            _load_canonical_json(config_payload)
            config = MonotonicRevocationAuthorityConfig.model_validate_json(
                config_payload, strict=True
            )
            self._identity = MonotonicRevocationAuthorityIdentity(
                schema_version="bb.rl.monotonic-revocation-authority-identity.v1",
                authority_id=config.authority_id,
                root_device=root_stat.st_dev,
                root_inode=root_stat.st_ino,
                root_uid=root_stat.st_uid,
                root_gid=root_stat.st_gid,
                root_flags=root_flags,
                config_device=config_stat.st_dev,
                config_inode=config_stat.st_ino,
                config_uid=config_stat.st_uid,
                config_gid=config_stat.st_gid,
                config_flags=config_flags,
                lock_device=lock_stat.st_dev,
                lock_inode=lock_stat.st_ino,
                lock_uid=lock_stat.st_uid,
                lock_gid=lock_stat.st_gid,
                lock_flags=lock_flags,
                config_digest=_sha256(config_payload),
            )
            self._config_payload = config_payload
        except BaseException:
            for fd in (self._lock_fd, self._config_fd, self._root_fd):
                if fd >= 0:
                    os.close(fd)
            raise
        self._thread_lock = threading.RLock()
        self._closed = False
        self._validate()

    @staticmethod
    def _read_descriptor(fd: int) -> bytes:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_RECORD_BYTES:
            raise RevocationPublicationIntegrityError(
                "monotonic authority file is not a bounded regular file"
            )
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise RevocationPublicationIntegrityError(
                    "monotonic authority file was truncated during read"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        closed = os.fstat(fd)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(closed, name) != getattr(opened, name) for name in stable_fields):
            raise RevocationPublicationIntegrityError(
                "monotonic authority file changed during read"
            )
        return b"".join(chunks)

    @property
    def identity(self) -> MonotonicRevocationAuthorityIdentity:
        self._validate()
        return self._identity

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in (self._lock_fd, self._config_fd, self._root_fd):
            os.close(fd)

    def _validate(self) -> None:
        if self._closed:
            raise RevocationPublicationIntegrityError(
                "monotonic authority is closed"
            )
        open_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        linked_fds: list[int] = []
        try:
            root_linked_fd = os.open(
                self._root,
                open_flags | getattr(os, "O_DIRECTORY", 0),
            )
            linked_fds.append(root_linked_fd)
            config_linked_fd = os.open(
                _AUTHORITY_CONFIG_NAME, open_flags, dir_fd=self._root_fd
            )
            linked_fds.append(config_linked_fd)
            lock_linked_fd = os.open(
                _AUTHORITY_LOCK_NAME, open_flags, dir_fd=self._root_fd
            )
            linked_fds.append(lock_linked_fd)
            root_linked = os.fstat(root_linked_fd)
            root_opened = os.fstat(self._root_fd)
            config_linked = os.fstat(config_linked_fd)
            config_opened = os.fstat(self._config_fd)
            lock_linked = os.fstat(lock_linked_fd)
            lock_opened = os.fstat(self._lock_fd)
            root_linked_flags = self._kernel_flags.read(root_linked_fd)
            root_opened_flags = self._kernel_flags.read(self._root_fd)
            config_linked_flags = self._kernel_flags.read(config_linked_fd)
            config_opened_flags = self._kernel_flags.read(self._config_fd)
            lock_linked_flags = self._kernel_flags.read(lock_linked_fd)
            lock_opened_flags = self._kernel_flags.read(self._lock_fd)
        except OSError as exc:
            raise RevocationPublicationIntegrityError(
                "monotonic authority identity is unavailable"
            ) from exc
        finally:
            for descriptor in linked_fds:
                os.close(descriptor)
        root_expected = (
            self._identity.root_device,
            self._identity.root_inode,
            self._identity.root_uid,
            self._identity.root_gid,
            self._identity.root_flags,
        )
        root_observations = (
            (
                root_linked.st_dev,
                root_linked.st_ino,
                root_linked.st_uid,
                root_linked.st_gid,
                root_linked_flags,
            ),
            (
                root_opened.st_dev,
                root_opened.st_ino,
                root_opened.st_uid,
                root_opened.st_gid,
                root_opened_flags,
            ),
        )
        if (
            not stat.S_ISDIR(root_linked.st_mode)
            or not stat.S_ISDIR(root_opened.st_mode)
            or any(observed != root_expected for observed in root_observations)
            or not self._identity.root_flags & self._kernel_flags.append_flag
        ):
            raise RevocationPublicationIntegrityError(
                "monotonic authority root identity or kernel flags changed"
            )
        config_expected = (
            self._identity.config_device,
            self._identity.config_inode,
            self._identity.config_uid,
            self._identity.config_gid,
            self._identity.config_flags,
        )
        config_observations = (
            (
                config_linked.st_dev,
                config_linked.st_ino,
                config_linked.st_uid,
                config_linked.st_gid,
                config_linked_flags,
            ),
            (
                config_opened.st_dev,
                config_opened.st_ino,
                config_opened.st_uid,
                config_opened.st_gid,
                config_opened_flags,
            ),
        )
        lock_expected = (
            self._identity.lock_device,
            self._identity.lock_inode,
            self._identity.lock_uid,
            self._identity.lock_gid,
            self._identity.lock_flags,
        )
        lock_observations = (
            (
                lock_linked.st_dev,
                lock_linked.st_ino,
                lock_linked.st_uid,
                lock_linked.st_gid,
                lock_linked_flags,
            ),
            (
                lock_opened.st_dev,
                lock_opened.st_ino,
                lock_opened.st_uid,
                lock_opened.st_gid,
                lock_opened_flags,
            ),
        )
        if (
            not stat.S_ISREG(config_linked.st_mode)
            or not stat.S_ISREG(config_opened.st_mode)
            or config_linked.st_nlink != 1
            or config_opened.st_nlink != 1
            or any(observed != config_expected for observed in config_observations)
            or not self._identity.config_flags & self._kernel_flags.immutable_flag
            or self._read_descriptor(self._config_fd) != self._config_payload
            or self._identity.config_digest != _sha256(self._config_payload)
        ):
            raise RevocationPublicationIntegrityError(
                "monotonic authority config identity or digest changed"
            )
        if (
            not stat.S_ISREG(lock_linked.st_mode)
            or not stat.S_ISREG(lock_opened.st_mode)
            or lock_linked.st_nlink != 1
            or lock_opened.st_nlink != 1
            or any(observed != lock_expected for observed in lock_observations)
            or not self._identity.lock_flags & self._kernel_flags.immutable_flag
        ):
            raise RevocationPublicationIntegrityError(
                "monotonic authority lock identity or kernel flags changed"
            )

    def open_lock_capability(self) -> int:
        self._validate()
        descriptor = os.open(
            _AUTHORITY_LOCK_NAME,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=self._root_fd,
        )
        try:
            self.validate_lock_capability(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def validate_lock_capability(self, fd: int) -> None:
        self._validate()
        observed = os.fstat(fd)
        observed_flags = self._kernel_flags.read(fd)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_dev != self._identity.lock_device
            or observed.st_ino != self._identity.lock_inode
            or observed.st_uid != self._identity.lock_uid
            or observed.st_gid != self._identity.lock_gid
            or observed.st_nlink != 1
            or observed_flags != self._identity.lock_flags
            or not observed_flags & self._kernel_flags.immutable_flag
        ):
            raise RevocationPublicationIntegrityError(
                "monotonic authority lock capability changed"
            )

    def _read_all_locked(self) -> tuple[MonotonicRevocationWitness, ...]:
        self._validate()
        names: list[tuple[int, str]] = []
        for name in os.listdir(self._root_fd):
            if name in (_AUTHORITY_CONFIG_NAME, _AUTHORITY_LOCK_NAME):
                continue
            match = _HISTORY_NAME_RE.fullmatch(name)
            if match is None:
                raise RevocationPublicationIntegrityError(
                    "monotonic authority contains an unexpected record"
                )
            generation = int(match.group(1))
            if name != f"{generation}.json":
                raise RevocationPublicationIntegrityError(
                    "monotonic authority witness name is not canonical"
                )
            names.append((generation, name))
        names.sort(key=lambda item: item[0])
        if tuple(generation for generation, _name in names) != tuple(
            range(1, len(names) + 1)
        ):
            raise RevocationPublicationIntegrityError(
                "monotonic authority witness sequence is not contiguous"
            )
        values: list[MonotonicRevocationWitness] = []
        for generation, name in names:
            fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self._root_fd,
            )
            try:
                opened = os.fstat(fd)
                linked = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
                opened_flags = self._kernel_flags.read(fd)
                exact_identity = (opened.st_dev, opened.st_ino) == (
                    linked.st_dev,
                    linked.st_ino,
                )
                regular_owned_single_link = (
                    stat.S_ISREG(opened.st_mode)
                    and stat.S_IMODE(opened.st_mode) == 0o400
                    and opened.st_uid == 0
                    and opened.st_nlink == 1
                    and exact_identity
                    and opened.st_mode == linked.st_mode
                    and opened.st_uid == linked.st_uid
                    and opened.st_gid == linked.st_gid
                    and opened.st_nlink == linked.st_nlink
                )
                if (
                    regular_owned_single_link
                    and not opened_flags & self._kernel_flags.immutable_flag
                    and generation == len(names)
                ):
                    continue
                if (
                    not regular_owned_single_link
                    or not opened_flags & self._kernel_flags.immutable_flag
                ):
                    raise RevocationPublicationIntegrityError(
                        "monotonic witness is not root-owned system-immutable"
                    )
                raw = self._read_descriptor(fd)
            finally:
                os.close(fd)
            _load_canonical_json(raw)
            try:
                witness = MonotonicRevocationWitness.model_validate_json(
                    raw, strict=True
                )
            except ValueError as exc:
                raise RevocationPublicationIntegrityError(
                    "monotonic witness is malformed"
                ) from exc
            if (
                witness.generation != generation
                or witness.monotonic_authority != self._identity
            ):
                raise RevocationPublicationIntegrityError(
                    "monotonic witness authority or generation is not exact"
                )
            values.append(witness)
        return tuple(values)

    def latest_locked(self, *, lock_fd: int) -> MonotonicRevocationWitness | None:
        self.validate_lock_capability(lock_fd)
        values = self._read_all_locked()
        return None if not values else values[-1]


    def latest(self) -> MonotonicRevocationWitness | None:
        with self._thread_lock:
            self._validate()
            fcntl.flock(self._lock_fd, fcntl.LOCK_SH)
            try:
                self.validate_lock_capability(self._lock_fd)
                values = self._read_all_locked()
                return None if not values else values[-1]
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def _compare_and_append_locked(
        self,
        expected: MonotonicRevocationWitness | None,
        successor: MonotonicRevocationWitness,
        *,
        lock_fd: int,
    ) -> MonotonicRevocationWitness:
        if type(successor) is not MonotonicRevocationWitness:
            raise TypeError("successor witness must be exact")
        self.validate_lock_capability(lock_fd)
        if successor.monotonic_authority != self._identity:
            raise RevocationPublicationIntegrityError(
                "successor witness authority identity mismatch"
            )
        values = self._read_all_locked()
        latest = None if not values else values[-1]
        if latest != expected or successor.generation != len(values) + 1:
            raise RevocationPublicationConflictError(
                "monotonic authority compare-and-append conflict"
            )
        name = f"{successor.generation}.json"
        try:
            fd = os.open(
                name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o400,
                dir_fd=self._root_fd,
            )
        except FileExistsError:
            committed = self._read_all_locked()
            if len(committed) >= successor.generation:
                raise RevocationPublicationConflictError(
                    "monotonic authority compare-and-append conflict"
                )
            fd = os.open(
                name,
                os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self._root_fd,
            )
            opened = os.fstat(fd)
            linked = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            opened_flags = self._kernel_flags.read(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != 0o400
                or opened.st_uid != 0
                or opened.st_nlink != 1
                or opened_flags & self._kernel_flags.immutable_flag
                or (opened.st_dev, opened.st_ino)
                != (linked.st_dev, linked.st_ino)
            ):
                os.close(fd)
                raise RevocationPublicationIntegrityError(
                    "incomplete monotonic witness cannot be resumed"
                )
            os.ftruncate(fd, 0)
        candidate = os.fstat(fd)
        candidate_flags = self._kernel_flags.read(fd)
        if (
            not stat.S_ISREG(candidate.st_mode)
            or stat.S_IMODE(candidate.st_mode) != 0o400
            or candidate.st_uid != 0
            or candidate.st_nlink != 1
            or candidate_flags & self._kernel_flags.immutable_flag
        ):
            os.close(fd)
            raise RevocationPublicationIntegrityError(
                "monotonic witness staging metadata is not exact"
            )
        try:
            payload = successor.canonical_bytes()
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short monotonic witness write")
                view = view[written:]
            os.fsync(fd)
            self._kernel_flags.set_immutable(fd)
            if not self._kernel_flags.read(fd) & self._kernel_flags.immutable_flag:
                raise RevocationPublicationIntegrityError(
                    "monotonic witness immutable commit did not persist"
                )
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self._root_fd)
        committed = self._read_all_locked()
        if not committed or committed[-1] != successor:
            raise RevocationPublicationIntegrityError(
                "monotonic authority did not commit the exact successor"
            )
        return successor

    def compare_and_append(
        self,
        expected: MonotonicRevocationWitness | None,
        successor: MonotonicRevocationWitness,
    ) -> MonotonicRevocationWitness:
        with self._thread_lock:
            self._validate()
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                return self._compare_and_append_locked(
                    expected, successor, lock_fd=self._lock_fd
                )
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

class _RevocationOperationRecord(_SignedRecord):
    schema_version: Literal["bb.rl.revocation-operation.v1"]
    request: RevocationSnapshotPublishRequest
    receipt: RevocationSnapshotPublishReceipt

    @model_validator(mode="after")
    def exact_request_receipt(self) -> _RevocationOperationRecord:
        if (
            self.request.operation_id != self.receipt.operation_id
            or self.request.canonical_digest() != self.receipt.request_digest
            or self.monotonic_authority != self.receipt.monotonic_authority
        ):
            raise ValueError("revocation operation receipt does not bind its request")
        return self


@runtime_checkable
class RevocationSnapshotPublisher(Protocol):
    def publish(
        self, request: RevocationSnapshotPublishRequest
    ) -> RevocationSnapshotPublishReceipt: ...

    def load(self, scope_digest: str) -> c.RevocationBinding: ...
    def validate_receipt(
        self, receipt: RevocationSnapshotPublishReceipt
    ) -> c.RevocationBinding: ...


    def snapshot_bytes(
        self, receipt: RevocationSnapshotPublishReceipt
    ) -> bytes: ...

    def bind_authoring_input(
        self,
        value: _T,
        receipt: RevocationSnapshotPublishReceipt,
    ) -> _T: ...


class FilesystemRevocationSnapshotPublisher:
    """Signed generation-CAS publication with immutable snapshot/history records.

    Every read verifies the complete contiguous signed chain against a required,
    separately pinned append-only high-water root. Mutable-state deletion or
    restoration therefore cannot lower the last durably published generation.
    """

    def __init__(
        self,
        cas: FilesystemCAS,
        root: str | Path,
        *,
        high_water_root: str | Path,
        monotonic_authority: PreprovisionedAppendOnlyMonotonicRevocationAuthority,
        authenticator: Any,
    ) -> None:
        if type(cas) is not FilesystemCAS:
            raise TypeError("cas must be an exact FilesystemCAS")
        if type(monotonic_authority) is not PreprovisionedAppendOnlyMonotonicRevocationAuthority:
            raise TypeError(
                "production publisher requires the exact preprovisioned append-only authority"
            )
        if (
            not getattr(authenticator, "key_id", None)
            or not getattr(authenticator, "algorithm", None)
            or not callable(getattr(authenticator, "sign", None))
            or not callable(getattr(authenticator, "verify", None))
        ):
            raise TypeError("authenticator does not provide signing authority")
        requested = Path(root)
        high_water = Path(high_water_root)
        for path, label in (
            (requested, "revocation publisher"),
            (high_water, "high-water audit"),
        ):
            if (
                not path.is_absolute()
                or os.path.normpath(os.fspath(path)) != os.fspath(path)
            ):
                raise ValueError(f"{label} root must be absolute and normalized")
            if not os.path.lexists(path):
                raise RevocationPublicationIntegrityError(
                    f"{label} root must be pre-provisioned"
                )
            if (
                not stat.S_ISDIR(os.lstat(path).st_mode)
                or path.resolve(strict=True) != path
            ):
                raise RevocationPublicationIntegrityError(
                    f"{label} root must be a non-aliased directory"
                )
        if high_water == requested:
            raise RevocationPublicationIntegrityError(
                "high-water audit root must be separate"
            )
        self.root = requested
        self.high_water_root = high_water
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        self._root_fd = os.open(self.root, flags)
        self._high_water_fd = os.open(self.high_water_root, flags)
        self._history_fd = -1
        self._operations_fd = -1
        self._lock_fd = monotonic_authority.open_lock_capability()
        try:
            self._history_fd = os.open("history", flags, dir_fd=self._root_fd)
            self._operations_fd = os.open("operations", flags, dir_fd=self._root_fd)
            descriptors = (
                self._root_fd,
                self._history_fd,
                self._operations_fd,
                self._high_water_fd,
            )
            if any(not stat.S_ISDIR(os.fstat(fd).st_mode) for fd in descriptors):
                raise RevocationPublicationIntegrityError(
                    "revocation publisher authority is not a directory"
                )
            monotonic_authority.validate_lock_capability(self._lock_fd)
        except BaseException:
            for fd in (
                self._lock_fd,
                self._operations_fd,
                self._history_fd,
                self._high_water_fd,
                self._root_fd,
            ):
                if fd >= 0:
                    os.close(fd)
            raise
        self._monotonic_authority = monotonic_authority
        self._authority_identity = monotonic_authority.identity
        self._identities = tuple(
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            for fd in (
                self._root_fd,
                self._history_fd,
                self._operations_fd,
            )
        )
        self._high_water_identity = (
            os.fstat(self._high_water_fd).st_dev,
            os.fstat(self._high_water_fd).st_ino,
        )
        self._cas = cas
        self._authenticator = authenticator
        self._thread_lock = threading.RLock()
        self._closed = False

    def close(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
            for fd in (
                self._lock_fd,
                self._operations_fd,
                self._history_fd,
                self._high_water_fd,
                self._root_fd,
            ):
                os.close(fd)

    def _operation(self) -> None:
        if self._closed:
            raise RevocationPublicationIntegrityError(
                "revocation publisher is closed"
            )
        self._validate_directories()

    def _flock_verified(self, mode: int) -> None:
        self._validate_directories()
        fcntl.flock(self._lock_fd, mode)
        try:
            self._validate_directories()
            if mode == fcntl.LOCK_EX:
                self._cleanup_abandoned_temporaries()
        except BaseException:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            raise

    def _cleanup_abandoned_temporaries(self) -> None:
        directories = (
            self._root_fd,
            self._history_fd,
            self._operations_fd,
            self._high_water_fd,
        )
        verified: list[tuple[int, str, int, tuple[int, int]]] = []
        try:
            for directory_fd in directories:
                for name in os.listdir(directory_fd):
                    reserved = name.startswith(".immutable.") or name.startswith(
                        ".active."
                    )
                    canonical = (
                        _IMMUTABLE_TEMP_NAME_RE.fullmatch(name) is not None
                        or (
                            directory_fd == self._root_fd
                            and _ACTIVE_TEMP_NAME_RE.fullmatch(name) is not None
                        )
                    )
                    if not reserved:
                        continue
                    if not canonical:
                        raise RevocationPublicationIntegrityError(
                            "revocation temporary name is not canonical"
                        )
                    descriptor = -1
                    try:
                        descriptor = os.open(
                            name,
                            os.O_RDONLY
                            | getattr(os, "O_NOFOLLOW", 0)
                            | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=directory_fd,
                        )
                        opened = os.fstat(descriptor)
                        linked = os.stat(
                            name, dir_fd=directory_fd, follow_symlinks=False
                        )
                    except OSError as exc:
                        if descriptor >= 0:
                            os.close(descriptor)
                        raise RevocationPublicationIntegrityError(
                            "revocation temporary identity is unavailable"
                        ) from exc
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or stat.S_IMODE(opened.st_mode) != 0o600
                        or opened.st_uid != 0
                        or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino)
                        != (linked.st_dev, linked.st_ino)
                    ):
                        os.close(descriptor)
                        raise RevocationPublicationIntegrityError(
                            "revocation temporary metadata is not exact"
                        )
                    verified.append(
                        (
                            directory_fd,
                            name,
                            descriptor,
                            (opened.st_dev, opened.st_ino),
                        )
                    )

            for directory_fd, name, _descriptor, identity in verified:
                try:
                    linked = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise RevocationPublicationIntegrityError(
                        "revocation temporary identity is unavailable"
                    ) from exc
                if (linked.st_dev, linked.st_ino) != identity:
                    raise RevocationPublicationIntegrityError(
                        "revocation temporary linked identity changed"
                    )

            changed_directories: set[int] = set()
            try:
                for directory_fd in directories:
                    for candidate_fd, name, _descriptor, _identity in verified:
                        if candidate_fd != directory_fd:
                            continue
                        os.unlink(name, dir_fd=directory_fd)
                        changed_directories.add(directory_fd)
            finally:
                for directory_fd in directories:
                    if directory_fd in changed_directories:
                        os.fsync(directory_fd)
        finally:
            for _directory_fd, _name, descriptor, _identity in verified:
                os.close(descriptor)



    def _validate_directories(self) -> None:
        try:
            linked = (
                os.stat(self.root, follow_symlinks=False),
                os.stat("history", dir_fd=self._root_fd, follow_symlinks=False),
                os.stat("operations", dir_fd=self._root_fd, follow_symlinks=False),
            )
        except OSError as exc:
            raise RevocationPublicationIntegrityError(
                "revocation publisher directory identity is unavailable"
            ) from exc
        opened = (
            os.fstat(self._root_fd),
            os.fstat(self._history_fd),
            os.fstat(self._operations_fd),
        )
        for expected, linked_stat, opened_stat in zip(
            self._identities[:3], linked, opened
        ):
            if (
                not stat.S_ISDIR(linked_stat.st_mode)
                or not stat.S_ISDIR(opened_stat.st_mode)
                or (linked_stat.st_dev, linked_stat.st_ino) != expected
                or (opened_stat.st_dev, opened_stat.st_ino) != expected
            ):
                raise RevocationPublicationIntegrityError(
                    "revocation publisher directory identity changed"
                )
        self._monotonic_authority.validate_lock_capability(self._lock_fd)
        if self._monotonic_authority.identity != self._authority_identity:
            raise RevocationPublicationIntegrityError(
                "monotonic authority identity changed"
            )
        high_water_linked = os.stat(self.high_water_root, follow_symlinks=False)
        high_water_opened = os.fstat(self._high_water_fd)
        if (
            not stat.S_ISDIR(high_water_linked.st_mode)
            or not stat.S_ISDIR(high_water_opened.st_mode)
            or (high_water_linked.st_dev, high_water_linked.st_ino)
            != self._high_water_identity
            or (high_water_opened.st_dev, high_water_opened.st_ino)
            != self._high_water_identity
        ):
            raise RevocationPublicationIntegrityError(
                "revocation high-water authority identity changed"
            )

    def _read(self, directory_fd: int, name: str) -> bytes | None:
        try:
            fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return None
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size <= 0
                or metadata.st_size > _MAX_RECORD_BYTES
            ):
                raise RevocationPublicationIntegrityError(
                    "revocation record is not a bounded regular file"
                )
            chunks: list[bytes] = []
            remaining = metadata.st_size + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != metadata.st_size:
                raise RevocationPublicationIntegrityError(
                    "revocation record changed during read"
                )
            return payload
        finally:
            os.close(fd)

    def _write_file(self, directory_fd: int, name: str, payload: bytes) -> None:
        self._validate_directories()
        fd = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short revocation record write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self._validate_directories()

    def _install_immutable(self, directory_fd: int, name: str, payload: bytes) -> None:
        temporary = f".immutable.{uuid.uuid4().hex}.tmp"
        try:
            self._write_file(directory_fd, temporary, payload)
            self._validate_directories()
            existing = self._read(directory_fd, name)
            if existing is None:
                os.rename(
                    temporary,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                self._validate_directories()
            elif existing != payload:
                raise RevocationPublicationConflictError(
                    "immutable revocation record conflict"
                )
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileNotFoundError:
                pass
    def _replace_active(self, payload: bytes) -> None:
        old_payload = self._read(self._root_fd, "active.json")
        temporary = f".active.{uuid.uuid4().hex}.tmp"
        self._write_file(self._root_fd, temporary, payload)
        replaced = False
        try:
            self._validate_directories()
            os.replace(
                temporary,
                "active.json",
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
            replaced = True
            self._validate_directories()
            os.fsync(self._root_fd)
        except BaseException:
            if replaced:
                if old_payload is None:
                    os.unlink("active.json", dir_fd=self._root_fd)
                else:
                    rollback = f".active.{uuid.uuid4().hex}.rollback"
                    self._write_file(self._root_fd, rollback, old_payload)
                    os.replace(
                        rollback,
                        "active.json",
                        src_dir_fd=self._root_fd,
                        dst_dir_fd=self._root_fd,
                    )
                os.fsync(self._root_fd)
            raise
        finally:
            try:
                os.unlink(temporary, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass

    def _sign(self, model_type: type[_T], payload: dict[str, Any]) -> _T:
        authenticated_payload = {
            **payload,
            "monotonic_authority": self._authority_identity.model_dump(mode="json"),
        }
        auth_digest = _sha256(_canonical_bytes(authenticated_payload))
        unsigned = {
            **authenticated_payload,
            "signer_key_id": self._authenticator.key_id,
            "signature_algorithm": self._authenticator.algorithm,
            "auth_digest": auth_digest,
        }
        signed = {
            **unsigned,
            "signature": self._authenticator.sign(_canonical_bytes(unsigned)).hex(),
        }
        return model_type.model_validate_json(_canonical_bytes(signed), strict=True)

    def _verify(self, value: _SignedRecord) -> None:
        document = value.model_dump(mode="json")
        signature_text = document.pop("signature")
        authenticated = dict(document)
        for field in ("signer_key_id", "signature_algorithm", "auth_digest"):
            authenticated.pop(field)
        if value.monotonic_authority != self._authority_identity:
            raise RevocationPublicationIntegrityError(
                "revocation record monotonic authority identity mismatch"
            )
        if (
            value.signer_key_id != self._authenticator.key_id
            or value.signature_algorithm != self._authenticator.algorithm
            or value.auth_digest != _sha256(_canonical_bytes(authenticated))
        ):
            raise RevocationPublicationIntegrityError(
                "revocation record authentication identity mismatch"
            )
        try:
            signature = bytes.fromhex(signature_text)
        except ValueError as exc:
            raise RevocationPublicationIntegrityError(
                "revocation record signature is malformed"
            ) from exc
        if not self._authenticator.verify(_canonical_bytes(document), signature):
            raise RevocationPublicationIntegrityError(
                "revocation record signature verification failed"
            )

    def _parse_signed(self, model_type: type[_T], payload: bytes) -> _T:
        _load_canonical_json(payload)
        try:
            value = model_type.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise RevocationPublicationIntegrityError(
                "revocation record schema verification failed"
            ) from exc
        if not isinstance(value, _SignedRecord):
            raise TypeError("signed revocation record model required")
        self._verify(value)
        return value

    @staticmethod
    def _history_name(generation: int) -> str:
        return f"{generation}.json"

    @staticmethod
    def _operation_name(operation_id: str) -> str:
        return hashlib.sha256(operation_id.encode()).hexdigest() + ".json"

    def _history_generations(self) -> tuple[int, ...]:
        generations: list[int] = []
        for name in os.listdir(self._history_fd):
            match = _HISTORY_NAME_RE.fullmatch(name)
            if match is None:
                raise RevocationPublicationIntegrityError(
                    "revocation history contains an unexpected record"
                )
            generations.append(int(match.group(1)))
        values = tuple(sorted(generations))
        if values and values != tuple(range(1, values[-1] + 1)):
            raise RevocationPublicationIntegrityError(
                "revocation history generations are not contiguous"
            )
        return values

    def _read_history(self, generation: int) -> _RevocationHistoryRecord:
        payload = self._read(self._history_fd, self._history_name(generation))
        if payload is None:
            raise RevocationPublicationIntegrityError(
                "revocation history record is absent"
            )
        return self._parse_signed(_RevocationHistoryRecord, payload)
    def _high_water_generations(self) -> tuple[int, ...]:
        values: list[int] = []
        for name in os.listdir(self._high_water_fd):
            match = _HISTORY_NAME_RE.fullmatch(name)
            if match is None:
                raise RevocationPublicationIntegrityError(
                    "high-water authority contains an unexpected record"
                )
            values.append(int(match.group(1)))
        generations = tuple(sorted(values))
        if generations and generations != tuple(range(1, generations[-1] + 1)):
            raise RevocationPublicationIntegrityError(
                "high-water authority is not contiguous"
            )
        return generations

    def _read_high_water(self, generation: int) -> _RevocationHighWaterRecord:
        payload = self._read(self._high_water_fd, self._history_name(generation))
        if payload is None:
            raise RevocationPublicationIntegrityError(
                "high-water authority record is absent"
            )
        record = self._parse_signed(_RevocationHighWaterRecord, payload)
        if record.generation != generation:
            raise RevocationPublicationIntegrityError(
                "high-water authority generation mismatch"
            )
        return record

    def _snapshot_bytes(
        self, ref: c.ArtifactRef, binding: c.RevocationBinding
    ) -> bytes:
        try:
            stored = self._cas.get_ref(ref.artifact_id)
            if (
                stored.artifact_id != ref.artifact_id
                or stored.sha256 != ref.sha256
                or stored.size_bytes != ref.size_bytes
                or stored.media_type != ref.media_type
            ):
                raise RevocationPublicationIntegrityError(
                    "revocation snapshot reference changed"
                )
            payload = self._cas.get_bytes(
                ref.artifact_id, max_bytes=_MAX_RECORD_BYTES
            )
        except (KeyError, FileNotFoundError) as exc:
            raise RevocationPublicationIntegrityError(
                "revocation snapshot is absent"
            ) from exc
        expected = _canonical_bytes([binding.model_dump(mode="json")])
        if (
            ref.media_type != _REVOCATION_SNAPSHOT_MEDIA_TYPE
            or payload != expected
            or ref.sha256 != _sha256(payload)
            or ref.size_bytes != len(payload)
        ):
            raise RevocationPublicationIntegrityError(
                "revocation snapshot is unsigned, tampered, or not exact"
            )
        return payload

    def _validate_history(
        self,
        history: _RevocationHistoryRecord,
        *,
        expected_generation: int,
    ) -> None:
        if history.generation != expected_generation:
            raise RevocationPublicationIntegrityError(
                "revocation history generation mismatch"
            )
        if history.binding.scope_digest == "":
            raise RevocationPublicationIntegrityError(
                "revocation history binding is absent"
            )
        self._snapshot_bytes(history.snapshot_ref, history.binding)
        if expected_generation == 1:
            if (
                history.previous_snapshot_ref is not None
                or history.previous_active_pointer_digest is not None
            ):
                raise RevocationPublicationIntegrityError(
                    "initial revocation history has a predecessor"
                )
            return
        previous_payload = self._read(self._root_fd, f"pointer-{expected_generation - 1}.json")
        if previous_payload is None:
            raise RevocationPublicationIntegrityError(
                "previous revocation pointer audit record is absent"
            )
        previous = self._parse_signed(_ActiveRevocationPointer, previous_payload)
        if (
            previous.generation != expected_generation - 1
            or history.previous_active_pointer_digest != _sha256(previous_payload)
            or history.previous_snapshot_ref != previous.snapshot_ref
            or previous.binding.scope_digest != history.binding.scope_digest
            or history.binding.epoch != previous.binding.epoch + 1
        ):
            raise RevocationPublicationIntegrityError(
                "revocation history predecessor chain mismatch"
            )

    def _read_active(
        self, *, allow_pending: bool
    ) -> tuple[_ActiveRevocationPointer | None, _RevocationHistoryRecord | None]:
        active_payload = self._read(self._root_fd, "active.json")
        active = (
            None
            if active_payload is None
            else self._parse_signed(_ActiveRevocationPointer, active_payload)
        )
        generations = self._history_generations()
        active_generation = 0 if active is None else active.generation
        if active is not None:
            for generation in range(1, active.generation + 1):
                history = self._read_history(generation)
                self._validate_history(history, expected_generation=generation)
                history_payload = history.canonical_bytes()
                audit_payload = self._read(
                    self._root_fd, f"pointer-{generation}.json"
                )
                if audit_payload is None:
                    raise RevocationPublicationIntegrityError(
                        "revocation pointer audit record is absent"
                    )
                audit = self._parse_signed(_ActiveRevocationPointer, audit_payload)
                if (
                    audit.generation != generation
                    or audit.operation_id != history.operation_id
                    or audit.request_digest != history.request_digest
                    or audit.binding != history.binding
                    or audit.snapshot_ref != history.snapshot_ref
                    or audit.history_digest != _sha256(history_payload)
                ):
                    raise RevocationPublicationIntegrityError(
                        "revocation pointer audit chain mismatch"
                    )
                if generation == active.generation and (
                    audit != active or audit_payload != active_payload
                ):
                    raise RevocationPublicationIntegrityError(
                        "active revocation pointer audit record mismatch"
                    )
        pointer_generations = tuple(
            sorted(
                int(match.group(1))
                for name in os.listdir(self._root_fd)
                if (match := _POINTER_NAME_RE.fullmatch(name)) is not None
            )
        )
        expected_pointer_generations = tuple(range(1, active_generation + 1))
        pending_generation = active_generation + 1
        pointer_pending = (
            allow_pending
            and generations
            and generations[-1] == pending_generation
            and pointer_generations
            == tuple(range(1, pending_generation + 1))
        )
        if pointer_generations != expected_pointer_generations and not pointer_pending:
            raise RevocationPublicationIntegrityError(
                "revocation pointer generations do not match the high-water witness"
            )
        if pointer_pending:
            pending_history = self._read_history(pending_generation)
            pending_payload = self._read(
                self._root_fd, f"pointer-{pending_generation}.json"
            )
            if pending_payload is None:
                raise RevocationPublicationIntegrityError(
                    "pending pointer audit disappeared"
                )
            pending_pointer = self._parse_signed(
                _ActiveRevocationPointer, pending_payload
            )
            if pending_pointer != self._pointer_for(
                pending_history, _sha256(pending_history.canonical_bytes())
            ):
                raise RevocationPublicationIntegrityError(
                    "pending pointer audit is not exact"
                )
        operation_names = set(os.listdir(self._operations_fd))
        if any(_OPERATION_NAME_RE.fullmatch(name) is None for name in operation_names):
            raise RevocationPublicationIntegrityError(
                "revocation operations contain an unexpected record"
            )
        expected_operation_names: set[str] = set()
        for generation in range(1, active_generation + 1):
            history = self._read_history(generation)
            operation_name = self._operation_name(history.operation_id)
            expected_operation_names.add(operation_name)
            if operation_name not in operation_names:
                if allow_pending and generation == active_generation:
                    continue
                raise RevocationPublicationIntegrityError(
                    "revocation operation high-water witness is absent"
                )
            operation = self._read_operation(history.operation_id)
            if operation is None or operation.receipt.generation != generation:
                raise RevocationPublicationIntegrityError(
                    "revocation operation high-water witness mismatches history"
                )
        if not operation_names.issubset(expected_operation_names):
            raise RevocationPublicationIntegrityError(
                "revocation operation high-water witness is unexpected"
            )
        high_water_generations = self._high_water_generations()
        for generation in high_water_generations:
            high_water = self._read_high_water(generation)
            history = self._read_history(generation)
            pointer_payload = self._read(
                self._root_fd, f"pointer-{generation}.json"
            )
            if (
                pointer_payload is None
                or high_water.request_digest != history.request_digest
                or high_water.history_digest != _sha256(history.canonical_bytes())
                or high_water.active_pointer_digest != _sha256(pointer_payload)
            ):
                raise RevocationPublicationIntegrityError(
                    "external high-water authority conflicts with publication state"
                )
        trusted_latest = self._monotonic_authority.latest_locked(
            lock_fd=self._lock_fd
        )
        if trusted_latest is not None and type(trusted_latest) is not MonotonicRevocationWitness:
            raise RevocationPublicationIntegrityError(
                "monotonic authority returned an inexact witness"
            )
        if trusted_latest is None:
            trusted_generation = 0
        else:
            trusted_generation = trusted_latest.generation
            trusted_record = self._read_high_water(trusted_generation)
            if trusted_latest.record_digest != _sha256(
                trusted_record.canonical_bytes()
            ):
                raise RevocationPublicationIntegrityError(
                    "monotonic authority digest conflicts with high-water audit"
                )
        external_maximum = (
            0 if not high_water_generations else high_water_generations[-1]
        )
        if external_maximum != trusted_generation:
            recoverable_monotonic_stage = (
                allow_pending
                and external_maximum == trusted_generation + 1
                and external_maximum == active_generation
            )
            if not recoverable_monotonic_stage:
                raise RevocationPublicationIntegrityError(
                    "high-water audit conflicts with monotonic authority"
                )
        if trusted_generation > active_generation:
            raise RevocationPublicationIntegrityError(
                "mutable publication state rolled back below monotonic authority"
            )
        if not allow_pending and trusted_generation != active_generation:
            raise RevocationPublicationIntegrityError(
                "monotonic authority commit is incomplete"
            )
        if allow_pending and trusted_generation not in {
            active_generation,
            max(0, active_generation - 1),
        }:
            raise RevocationPublicationIntegrityError(
                "monotonic authority commit cannot be recovered"
            )
        maximum = 0 if not generations else generations[-1]
        if maximum == active_generation:
            return active, None
        if allow_pending and maximum == active_generation + 1:
            pending = self._read_history(maximum)
            self._validate_history(pending, expected_generation=maximum)
            return active, pending
        raise RevocationPublicationIntegrityError(
            "active revocation pointer was replaced or publication is incomplete"
        )

    def _read_operation(
        self, operation_id: str
    ) -> _RevocationOperationRecord | None:
        payload = self._read(self._operations_fd, self._operation_name(operation_id))
        if payload is None:
            return None
        operation = self._parse_signed(_RevocationOperationRecord, payload)
        if operation.request.operation_id != operation_id:
            raise RevocationPublicationIntegrityError(
                "revocation operation index identity mismatch"
            )
        history_payload = self._read(
            self._history_fd, self._history_name(operation.receipt.generation)
        )
        if (
            history_payload is None
            or _sha256(history_payload) != operation.receipt.history_digest
        ):
            raise RevocationPublicationIntegrityError(
                "revocation operation history receipt mismatch"
            )
        history = self._parse_signed(_RevocationHistoryRecord, history_payload)
        if (
            history.operation_id != operation_id
            or history.request_digest != operation.receipt.request_digest
            or history.snapshot_ref != operation.receipt.snapshot_ref
            or history.previous_snapshot_ref
            != operation.receipt.previous_snapshot_ref
            or (
                history.predecessor_model_type,
                history.predecessor_schema_version,
                history.predecessor_config_digest,
            )
            != (
                operation.request.predecessor_model_type,
                operation.request.predecessor_schema_version,
                operation.request.predecessor_config_digest,
            )
            or (
                operation.receipt.predecessor_model_type,
                operation.receipt.predecessor_schema_version,
                operation.receipt.predecessor_config_digest,
            )
            != (
                operation.request.predecessor_model_type,
                operation.request.predecessor_schema_version,
                operation.request.predecessor_config_digest,
            )
        ):
            raise RevocationPublicationIntegrityError(
                "revocation operation receipt is not exact"
            )
        self._snapshot_bytes(history.snapshot_ref, history.binding)
        return operation

    def _publish_snapshot(self, binding: c.RevocationBinding) -> c.ArtifactRef:
        payload = _canonical_bytes([binding.model_dump(mode="json")])
        stored = self._cas.put_bytes(
            payload, media_type=_REVOCATION_SNAPSHOT_MEDIA_TYPE
        )
        if self._cas.get_bytes(stored, max_bytes=len(payload)) != payload:
            raise RevocationPublicationIntegrityError(
                "revocation snapshot CAS readback mismatch"
            )
        return c.ArtifactRef(
            artifact_id=stored.sha256,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=stored.media_type,
        )

    def _pointer_for(
        self, history: _RevocationHistoryRecord, history_digest: str
    ) -> _ActiveRevocationPointer:
        return self._sign(
            _ActiveRevocationPointer,
            {
                "schema_version": "bb.rl.active-revocation-pointer.v1",
                "operation_id": history.operation_id,
                "request_digest": history.request_digest,
                "generation": history.generation,
                "binding": history.binding.model_dump(mode="json"),
                "snapshot_ref": history.snapshot_ref.model_dump(mode="json"),
                "history_digest": history_digest,
            },
        )

    def _commit_pointer(
        self, history: _RevocationHistoryRecord
    ) -> tuple[_ActiveRevocationPointer, str]:
        history_digest = _sha256(history.canonical_bytes())
        pointer = self._pointer_for(history, history_digest)
        pointer_payload = pointer.canonical_bytes()
        self._install_immutable(
            self._root_fd,
            f"pointer-{pointer.generation}.json",
            pointer_payload,
        )
        self._replace_active(pointer_payload)
        return pointer, _sha256(pointer_payload)

    def _receipt_for(
        self,
        request: RevocationSnapshotPublishRequest,
        history: _RevocationHistoryRecord,
        active_pointer_digest: str,
    ) -> RevocationSnapshotPublishReceipt:
        return RevocationSnapshotPublishReceipt(
            operation_id=request.operation_id,
            request_digest=request.canonical_digest(),
            generation=history.generation,
            previous_snapshot_ref=history.previous_snapshot_ref,
            snapshot_ref=history.snapshot_ref,
            active_pointer_digest=active_pointer_digest,
            history_digest=_sha256(history.canonical_bytes()),
            monotonic_authority=self._authority_identity,
            predecessor_model_type=request.predecessor_model_type,
            predecessor_schema_version=request.predecessor_schema_version,
            predecessor_config_digest=request.predecessor_config_digest,
        )

    def _record_operation(
        self,
        request: RevocationSnapshotPublishRequest,
        receipt: RevocationSnapshotPublishReceipt,
    ) -> None:
        operation = self._sign(
            _RevocationOperationRecord,
            {
                "schema_version": "bb.rl.revocation-operation.v1",
                "request": request.model_dump(mode="json"),
                "receipt": receipt.model_dump(mode="json"),
            },
        )
        self._install_immutable(
            self._operations_fd,
            self._operation_name(request.operation_id),
            operation.canonical_bytes(),
        )
    def _record_high_water(
        self,
        history: _RevocationHistoryRecord,
        active_pointer_digest: str,
    ) -> None:
        record = self._sign(
            _RevocationHighWaterRecord,
            {
                "schema_version": "bb.rl.revocation-high-water.v1",
                "generation": history.generation,
                "request_digest": history.request_digest,
                "active_pointer_digest": active_pointer_digest,
                "history_digest": _sha256(history.canonical_bytes()),
            },
        )
        payload = record.canonical_bytes()
        self._install_immutable(
            self._high_water_fd,
            self._history_name(history.generation),
            payload,
        )
        successor = MonotonicRevocationWitness(
            monotonic_authority=self._authority_identity,
            generation=history.generation,
            record_digest=_sha256(payload),
        )
        latest = self._monotonic_authority.latest_locked(lock_fd=self._lock_fd)
        if latest == successor:
            return
        expected = None
        if history.generation > 1:
            previous = self._read_high_water(history.generation - 1)
            expected = MonotonicRevocationWitness(
                monotonic_authority=self._authority_identity,
                generation=history.generation - 1,
                record_digest=_sha256(previous.canonical_bytes()),
            )
        if latest != expected:
            raise RevocationPublicationConflictError(
                "monotonic revocation authority CAS conflict"
            )
        committed = self._monotonic_authority._compare_and_append_locked(
            expected, successor, lock_fd=self._lock_fd
        )
        if committed != successor:
            raise RevocationPublicationIntegrityError(
                "monotonic authority returned an inexact commit witness"
            )

    def publish(
        self, request: RevocationSnapshotPublishRequest
    ) -> RevocationSnapshotPublishReceipt:
        if type(request) is not RevocationSnapshotPublishRequest:
            raise TypeError("request must be an exact RevocationSnapshotPublishRequest")
        request_digest = request.canonical_digest()
        with self._thread_lock:
            self._operation()
            self._flock_verified(fcntl.LOCK_EX)
            try:
                existing_operation = self._read_operation(request.operation_id)
                active, pending = self._read_active(allow_pending=True)
                if existing_operation is not None:
                    if existing_operation.request.canonical_digest() != request_digest:
                        raise RevocationPublicationConflictError(
                            "revocation operation_id was reused"
                        )
                    history = self._read_history(existing_operation.receipt.generation)
                    self._record_operation(request, existing_operation.receipt)
                    self._record_high_water(
                        history, existing_operation.receipt.active_pointer_digest
                    )
                    self._read_active(allow_pending=False)
                    return existing_operation.receipt
                if (
                    active is not None
                    and active.operation_id == request.operation_id
                    and active.request_digest == request_digest
                    and active.binding == request.binding
                ):
                    active_payload = self._read(self._root_fd, "active.json")
                    if active_payload is None:
                        raise RevocationPublicationIntegrityError(
                            "active revocation pointer disappeared"
                        )
                    history = self._read_history(active.generation)
                    receipt = self._receipt_for(
                        request, history, _sha256(active_payload)
                    )
                    self._record_operation(request, receipt)
                    self._record_high_water(
                        history, receipt.active_pointer_digest
                    )
                    return receipt
                predecessor = request.predecessor_authority
                if active is None:
                    if predecessor is not None:
                        raise RevocationPublicationConflictError(
                            "initial publication cannot carry predecessor authority"
                        )
                elif (
                    predecessor is None
                    or _trusted_authority_revocation(predecessor) != active.binding
                ):
                    raise RevocationPublicationConflictError(
                        "trusted predecessor revocation is not the active binding"
                    )
                if pending is not None:
                    if (
                        pending.operation_id != request.operation_id
                        or pending.request_digest != request_digest
                        or pending.binding != request.binding
                    ):
                        raise RevocationPublicationConflictError(
                            "another revocation publication is pending"
                        )
                    self._install_immutable(
                        self._history_fd,
                        self._history_name(pending.generation),
                        pending.canonical_bytes(),
                    )
                    pointer, pointer_digest = self._commit_pointer(pending)
                    receipt = self._receipt_for(request, pending, pointer_digest)
                    self._record_operation(request, receipt)
                    self._record_high_water(pending, pointer_digest)
                    if pointer.generation != receipt.generation:
                        raise RevocationPublicationIntegrityError(
                            "revocation publication recovery generation mismatch"
                        )
                    return receipt

                if active is not None:
                    active_operation = self._read_operation(active.operation_id)
                    external_generations = self._high_water_generations()
                    external_maximum = (
                        0 if not external_generations else external_generations[-1]
                    )
                    if (
                        active_operation is None
                        or external_maximum != active.generation
                    ):
                        raise RevocationPublicationConflictError(
                            "only the exact incomplete active operation may recover"
                        )

                if active is None:
                    if (
                        request.expected_generation is not None
                        or request.expected_epoch is not None
                    ):
                        raise RevocationPublicationConflictError(
                            "initial revocation generation expectation is stale"
                        )
                    generation = 1
                    previous_snapshot_ref = None
                    previous_pointer_digest = None
                else:
                    if active.binding.scope_digest != request.scope_digest:
                        raise RevocationPublicationConflictError(
                            "revocation publication scope drift"
                        )
                    if request.expected_generation != active.generation:
                        raise RevocationPublicationConflictError(
                            "active revocation generation compare-and-swap failed"
                        )
                    if request.expected_epoch != active.binding.epoch:
                        raise RevocationPublicationConflictError(
                            "active revocation epoch expectation is stale"
                        )
                    if request.binding.epoch != active.binding.epoch + 1:
                        raise RevocationPublicationConflictError(
                            "revocation epoch must advance exactly once"
                        )
                    generation = active.generation + 1
                    previous_snapshot_ref = active.snapshot_ref
                    active_payload = self._read(self._root_fd, "active.json")
                    if active_payload is None:
                        raise RevocationPublicationIntegrityError(
                            "active revocation pointer disappeared"
                        )
                    previous_pointer_digest = _sha256(active_payload)

                snapshot_ref = self._publish_snapshot(request.binding)
                history = self._sign(
                    _RevocationHistoryRecord,
                    {
                        "schema_version": "bb.rl.revocation-history.v1",
                        "operation_id": request.operation_id,
                        "request_digest": request_digest,
                        "generation": generation,
                        "previous_snapshot_ref": (
                            None
                            if previous_snapshot_ref is None
                            else previous_snapshot_ref.model_dump(mode="json")
                        ),
                        "snapshot_ref": snapshot_ref.model_dump(mode="json"),
                        "binding": request.binding.model_dump(mode="json"),
                        "predecessor_model_type": request.predecessor_model_type,
                        "predecessor_schema_version": request.predecessor_schema_version,
                        "predecessor_config_digest": request.predecessor_config_digest,
                        "previous_active_pointer_digest": previous_pointer_digest,
                    },
                )
                self._install_immutable(
                    self._history_fd,
                    self._history_name(generation),
                    history.canonical_bytes(),
                )
                pointer, pointer_digest = self._commit_pointer(history)
                receipt = self._receipt_for(request, history, pointer_digest)
                self._record_operation(request, receipt)
                self._record_high_water(history, pointer_digest)
                if pointer.binding != request.binding:
                    raise RevocationPublicationIntegrityError(
                        "active revocation pointer binding mismatch"
                    )
                return receipt
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def load(self, scope_digest: str) -> c.RevocationBinding:
        _validate_digest(scope_digest)
        with self._thread_lock:
            self._operation()
            self._flock_verified(fcntl.LOCK_SH)
            try:
                active, pending = self._read_active(allow_pending=False)
                if pending is not None:
                    raise RevocationPublicationIntegrityError(
                        "revocation publication is incomplete"
                    )
                if active is None:
                    raise ValueError("revocation scope is not published")
                if active.binding.scope_digest != scope_digest:
                    raise ValueError("revocation scope is not published")
                return c.RevocationBinding.model_validate(
                    active.binding.model_dump(mode="json"), strict=True
                )
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def _validate_receipt(
        self, receipt: RevocationSnapshotPublishReceipt
    ) -> _RevocationOperationRecord:
        if type(receipt) is not RevocationSnapshotPublishReceipt:
            raise TypeError("receipt must be an exact RevocationSnapshotPublishReceipt")
        if receipt.monotonic_authority != self._authority_identity:
            raise RevocationPublicationIntegrityError(
                "revocation receipt monotonic authority identity mismatch"
            )
        operation = self._read_operation(receipt.operation_id)
        if operation is None or operation.receipt != receipt:
            raise RevocationPublicationIntegrityError(
                "revocation publication receipt is absent or inexact"
            )
        return operation
    def validate_receipt(
        self, receipt: RevocationSnapshotPublishReceipt
    ) -> c.RevocationBinding:
        with self._thread_lock:
            self._operation()
            self._flock_verified(fcntl.LOCK_SH)
            try:
                self._read_active(allow_pending=False)
                operation = self._validate_receipt(receipt)
                binding = operation.request.binding
                self._snapshot_bytes(receipt.snapshot_ref, binding)
                return c.RevocationBinding.model_validate(
                    binding.model_dump(mode="python"), strict=True
                )
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)


    def snapshot_bytes(
        self, receipt: RevocationSnapshotPublishReceipt
    ) -> bytes:
        with self._thread_lock:
            self._operation()
            self._flock_verified(fcntl.LOCK_SH)
            try:
                self._read_active(allow_pending=False)
                operation = self._validate_receipt(receipt)
                return self._snapshot_bytes(
                    receipt.snapshot_ref, operation.request.binding
                )
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def bind_authoring_input(
        self,
        value: _T,
        receipt: RevocationSnapshotPublishReceipt,
    ) -> _T:
        if type(value) not in (F2C4DynamicAuthorityInput, F3AuthorityInput):
            raise TypeError("authoring input is not a trusted F2/F3 authority")
        with self._thread_lock:
            self._operation()
            self._flock_verified(fcntl.LOCK_SH)
            try:
                operation = self._validate_receipt(receipt)
                predecessor = operation.request.predecessor_authority
                if (
                    predecessor is None
                    or type(value) is not type(predecessor)
                    or value != predecessor
                ):
                    raise RevocationPublicationConflictError(
                        "trusted authoring predecessor identity drift"
                    )
                active, _pending = self._read_active(allow_pending=False)
                if active is None:
                    raise RevocationPublicationIntegrityError(
                        "active revocation pointer is absent"
                    )
                active_payload = self._read(self._root_fd, "active.json")
                if (
                    active_payload is None
                    or _sha256(active_payload) != receipt.active_pointer_digest
                    or active.generation != receipt.generation
                    or active.snapshot_ref != receipt.snapshot_ref
                ):
                    raise RevocationPublicationConflictError(
                        "revocation publication receipt is no longer active"
                    )
                binding = operation.request.binding
                self._snapshot_bytes(receipt.snapshot_ref, binding)
                existing = _trusted_authority_revocation(value)
                if type(value) is F3AuthorityInput:
                    replacement_field = "policy"
                    policy = value.policy
                else:
                    replacement_field = "revocation"
                    policy = None
                if receipt.generation <= 1:
                    raise RevocationPublicationConflictError(
                        "authoring rebind requires a published predecessor"
                    )
                previous = self._read_history(receipt.generation - 1).binding
                if existing != previous:
                    raise RevocationPublicationConflictError(
                        "authoring predecessor revocation state drift"
                    )
                if replacement_field == "policy":
                    updated_policy = policy.model_copy(update={"revocation": binding})
                    candidate = value.model_copy(update={"policy": updated_policy})
                else:
                    candidate = value.model_copy(update={"revocation": binding})
                return type(value).model_validate(
                    {
                        name: getattr(candidate, name)
                        for name in type(value).model_fields
                    },
                    strict=True,
                )
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)


__all__ = [
    "FilesystemRevocationSnapshotPublisher",
    "MonotonicRevocationAuthorityConfig",
    "MonotonicRevocationAuthorityIdentity",
    "MonotonicRevocationWitness",
    "PreprovisionedAppendOnlyMonotonicRevocationAuthority",
    "RevocationAuthoringIdentity",
    "RevocationPublicationConflictError",
    "RevocationPublicationIntegrityError",
    "RevocationSnapshotPublishReceipt",
    "RevocationSnapshotPublishRequest",
    "RevocationSnapshotPublisher",
    "revocation_authoring_identity",
]
