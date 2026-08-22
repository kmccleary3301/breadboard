from __future__ import annotations

import argparse
import ctypes
import enum
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

from breadboard.rl.phase5.transport_smoke_payload import (
    construct_transport_smoke_payload,
)

_PAYLOAD_NAME = "transport-smoke-payload.zip"
_RECEIPT_NAME = "transport-smoke-payload-build.json"
_STAGING_PREFIX = ".transport-smoke-payload-staging-"
_MANIFEST_MEMBER = "payload_manifest.json"
_BUILD_SCHEMA = "bb.rl.phase5.transport-smoke-payload-build.v1"
_CAPABILITY_BUILD_SCHEMA = (
    "bb.rl.phase5.runtime-preflight-capability-payload-build.v1"
)
_REPORT_SCHEMA = "bb.rl.phase3.transport_smoke.v1"
_REPORT_ID = "transport-smoke-fixed-v1"
_COMPONENT = "transport_smoke"
_NONCE = bytes.fromhex(
    "8f4b4fdc5f2ec82df80b23a5e0a34ebd0ef2734d8e9636f49e2b7df7f6fd5812"
)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PENDING_TARGET_SUFFIX = "-slurm-pending"


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class PublicationState(enum.Enum):
    INITIAL = "initial"
    STAGING_CREATED = "staging_created"
    PAYLOAD_DURABLE = "payload_durable"
    TUPLE_DURABLE = "tuple_durable"
    DIRECTORY_PUBLICATION_UNKNOWN = "directory_publication_unknown"
    DIRECTORY_PUBLISHED_DURABILITY_UNKNOWN = (
        "directory_published_durability_unknown"
    )
    DIRECTORY_NONPUBLICATION_ESTABLISHED = (
        "directory_nonpublication_established"
    )
    COMPLETE = "complete"


class ParentIdentityChanged(RuntimeError):
    def __init__(
        self,
        parent: Path,
        stage: str,
        expected: tuple[int, int],
        observed: tuple[int, int] | None,
    ) -> None:
        super().__init__(
            f"canonical destination parent identity changed at {stage}: "
            f"expected {expected}, observed {observed}"
        )
        self.parent = parent
        self.stage = stage
        self.expected = expected
        self.observed = observed


class DestinationIdentityChanged(RuntimeError):
    def __init__(
        self,
        destination: Path,
        stage: str,
        expected: tuple[int, int],
        observed: tuple[int, int] | None,
    ) -> None:
        super().__init__(
            f"canonical destination identity changed at {stage}: "
            f"expected {expected}, observed {observed}"
        )
        self.destination = destination
        self.stage = stage
        self.expected = expected
        self.observed = observed


class StagingIdentityChanged(RuntimeError):
    def __init__(
        self,
        staging: Path,
        stage: str,
        expected: tuple[int, int],
        observed: tuple[int, int] | None,
    ) -> None:
        super().__init__(
            f"owned staging directory identity changed at {stage}: "
            f"expected {expected}, observed {observed}"
        )
        self.staging = staging
        self.stage = stage
        self.expected = expected
        self.observed = observed


class PublicationRecoveryRequired(RuntimeError):
    def __init__(
        self,
        *,
        destination: Path,
        staging: Path,
        stage: str,
        staging_inode: tuple[int, int],
        parent_inode: tuple[int, int],
        payload_inode: tuple[int, int],
        payload_sha256: str,
        payload_size_bytes: int,
        receipt_inode: tuple[int, int],
        receipt_sha256: str,
        receipt_size_bytes: int,
        committed: bool | None,
        receipt_presence: bool | None,
        primary_error: BaseException,
    ) -> None:
        self.committed = committed
        self.receipt_presence = receipt_presence
        self.destination = destination
        self.staging = staging
        self.canonical_receipt = destination / _RECEIPT_NAME
        self.stage = stage
        self.staging_inode = staging_inode
        self.parent_inode = parent_inode
        self.payload_inode = payload_inode
        self.payload_sha256 = payload_sha256
        self.payload_size_bytes = payload_size_bytes
        self.receipt_inode = receipt_inode
        self.receipt_sha256 = receipt_sha256
        self.receipt_size_bytes = receipt_size_bytes
        self.primary_error = primary_error
        if committed is None:
            self.publication_state = PublicationState.DIRECTORY_PUBLICATION_UNKNOWN
        elif committed:
            self.publication_state = (
                PublicationState.DIRECTORY_PUBLISHED_DURABILITY_UNKNOWN
            )
        else:
            self.publication_state = (
                PublicationState.DIRECTORY_NONPUBLICATION_ESTABLISHED
            )
        if committed is None:
            detail = "atomic directory publication presence is unknown"
        elif committed:
            detail = "atomic directory publication durability or closure is unknown"
        else:
            detail = "atomic directory nonpublication was established"
        super().__init__(
            f"transport smoke tuple at {destination} requires recovery after "
            f"{stage}: {detail}; call recover_publication with this exception"
        )


def _fsync(fd: int, stage: str) -> None:
    os.fsync(fd)


def _write_fsynced_at(
    directory_fd: int, name: str, raw: bytes, mode: int, *, stage: str
) -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeError("exclusive no-follow publication is unsupported")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    fd = os.open(name, flags, mode, dir_fd=directory_fd)
    failure: BaseException | None = None
    identity: tuple[int, int] | None = None
    try:
        remaining = memoryview(raw)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("short write while publishing " + name)
            remaining = remaining[written:]
        os.fchmod(fd, mode)
        _fsync(fd, stage)
        observed = os.fstat(fd)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise RuntimeError(
                "published file is not an isolated regular file: " + name
            )
        identity = (observed.st_dev, observed.st_ino)
    except BaseException as error:
        failure = error
    try:
        os.close(fd)
    except BaseException as error:
        if failure is None:
            failure = error
    if failure is not None:
        raise failure
    if identity is None:
        raise RuntimeError("published file identity was not established: " + name)
    return identity


def _same_inode(observed: os.stat_result, expected: tuple[int, int]) -> bool:
    return (observed.st_dev, observed.st_ino) == expected


def _cleanup_owned_directory(
    *,
    parent_fd: int,
    leaf: str,
    directory_fd: int | None,
    owned_inode: tuple[int, int] | None,
    child_inodes: tuple[tuple[str, tuple[int, int] | None], ...],
) -> None:
    if directory_fd is not None:
        for name, expected_inode in child_inodes:
            if expected_inode is None:
                continue
            try:
                observed = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False
                )
                if stat.S_ISREG(observed.st_mode) and _same_inode(
                    observed, expected_inode
                ):
                    os.unlink(name, dir_fd=directory_fd)
            except BaseException:
                pass
        try:
            os.fsync(directory_fd)
        except BaseException:
            pass
    if owned_inode is None:
        return
    try:
        observed = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode) or not _same_inode(
            observed, owned_inode
        ):
            return
        if directory_fd is None or os.listdir(directory_fd):
            return
        os.rmdir(leaf, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except BaseException:
        pass


def _cleanup_failed_publication(
    *,
    parent_fd: int,
    staging_leaf: str | None,
    staging_fd: int | None,
    staging_inode: tuple[int, int] | None,
    payload_inode: tuple[int, int] | None,
    receipt_inode: tuple[int, int] | None,
) -> None:
    if staging_leaf is None:
        return
    _cleanup_owned_directory(
        parent_fd=parent_fd,
        leaf=staging_leaf,
        directory_fd=staging_fd,
        owned_inode=staging_inode,
        child_inodes=(
            (_RECEIPT_NAME, receipt_inode),
            (_PAYLOAD_NAME, payload_inode),
        ),
    )


def _revalidate_parent(
    parent: Path,
    parent_fd: int,
    expected: tuple[int, int],
    *,
    stage: str,
) -> None:
    observed_identity: tuple[int, int] | None = None
    try:
        opened = os.fstat(parent_fd)
        named = os.stat(parent, follow_symlinks=False)
        observed_identity = (named.st_dev, named.st_ino)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or not _same_inode(opened, expected)
            or not _same_inode(named, expected)
        ):
            raise ParentIdentityChanged(
                parent, stage, expected, observed_identity
            )
    except ParentIdentityChanged:
        raise
    except BaseException as error:
        raise ParentIdentityChanged(
            parent, stage, expected, observed_identity
        ) from error


def _revalidate_destination(
    parent_fd: int,
    leaf: str,
    expected: tuple[int, int],
    *,
    parent: Path,
    stage: str,
    directory_fd: int,
) -> None:
    observed_identity: tuple[int, int] | None = None
    try:
        opened = os.fstat(directory_fd)
        named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        observed_identity = (named.st_dev, named.st_ino)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or not _same_inode(opened, expected)
            or not _same_inode(named, expected)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or stat.S_IMODE(named.st_mode) != 0o700
        ):
            raise DestinationIdentityChanged(
                parent / leaf, stage, expected, observed_identity
            )
    except DestinationIdentityChanged:
        raise
    except BaseException as error:
        raise DestinationIdentityChanged(
            parent / leaf, stage, expected, observed_identity
        ) from error


def _revalidate_staging(
    parent_fd: int,
    leaf: str,
    expected: tuple[int, int],
    *,
    parent: Path,
    stage: str,
    directory_fd: int,
) -> None:
    observed_identity: tuple[int, int] | None = None
    try:
        opened = os.fstat(directory_fd)
        named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        observed_identity = (named.st_dev, named.st_ino)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or not _same_inode(opened, expected)
            or not _same_inode(named, expected)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or stat.S_IMODE(named.st_mode) != 0o700
        ):
            raise StagingIdentityChanged(
                parent / leaf, stage, expected, observed_identity
            )
    except StagingIdentityChanged:
        raise
    except BaseException as error:
        raise StagingIdentityChanged(
            parent / leaf, stage, expected, observed_identity
        ) from error


def _read_regular_at(
    directory_fd: int,
    name: str,
    *,
    expected_inode: tuple[int, int],
    expected_size: int,
    expected_sha256: str,
    expected_mode: int,
) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeError("exclusive no-follow validation is unsupported")
    named_before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(named_before.st_mode)
        or named_before.st_nlink != 1
        or not _same_inode(named_before, expected_inode)
        or named_before.st_size != expected_size
        or stat.S_IMODE(named_before.st_mode) != expected_mode
    ):
        raise RuntimeError("published file metadata changed: " + name)
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    failure: BaseException | None = None
    raw: bytes | None = None
    try:
        opened_before = os.fstat(fd)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or opened_before.st_nlink != 1
            or not _same_inode(opened_before, expected_inode)
            or opened_before.st_size != expected_size
            or stat.S_IMODE(opened_before.st_mode) != expected_mode
        ):
            raise RuntimeError("published file metadata changed: " + name)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        opened_after = os.fstat(fd)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_after.st_mode)
            or opened_after.st_nlink != 1
            or not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or not _same_inode(opened_after, expected_inode)
            or not _same_inode(named, expected_inode)
            or opened_after.st_size != expected_size
            or named.st_size != expected_size
            or stat.S_IMODE(opened_after.st_mode) != expected_mode
            or stat.S_IMODE(named.st_mode) != expected_mode
            or len(raw) != expected_size
            or _sha256(raw) != expected_sha256
        ):
            raise RuntimeError("published file closure changed: " + name)
    except BaseException as error:
        failure = error
    try:
        os.close(fd)
    except BaseException as error:
        if failure is None:
            failure = error
    if failure is not None:
        raise failure
    if raw is None:
        raise RuntimeError("published file was not read: " + name)
    return raw


def _assert_exact_tuple_at(directory_fd: int) -> None:
    if set(os.listdir(directory_fd)) != {_PAYLOAD_NAME, _RECEIPT_NAME}:
        raise RuntimeError("staging directory does not contain the exact tuple")


def _assert_empty_directory(directory_fd: int) -> None:
    if os.listdir(directory_fd):
        raise RuntimeError("owned staging directory is not empty")


def _publication_checkpoint(stage: str) -> None:
    del stage


def _rename_directory_noreplace_at(
    parent_fd: int, staging_leaf: str, destination_leaf: str
) -> None:
    source = os.fsencode(staging_leaf)
    destination = os.fsencode(destination_leaf)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        try:
            rename = libc.renameatx_np
        except AttributeError as error:
            raise RuntimeError(
                "Darwin atomic no-replace directory rename is unavailable"
            ) from error
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        arguments = (parent_fd, source, parent_fd, destination, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError as error:
            raise RuntimeError(
                "Linux atomic no-replace directory rename is unavailable"
            ) from error
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        arguments = (parent_fd, source, parent_fd, destination, 0x00000001)
    else:
        raise RuntimeError(
            "atomic no-replace directory rename is unsupported on "
            + sys.platform
        )
    ctypes.set_errno(0)
    if rename(*arguments) != 0:
        error_number = ctypes.get_errno()
        if error_number == 0:
            raise RuntimeError(
                "atomic no-replace directory rename failed without errno"
            )
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination_leaf,
        )


def _observe_directory_publication(
    parent_fd: int,
    staging_leaf: str,
    destination_leaf: str,
    staging_inode: tuple[int, int],
) -> bool | None:
    destination_inode: tuple[int, int] | None = None
    staging_named_inode: tuple[int, int] | None = None
    try:
        try:
            destination = os.stat(
                destination_leaf, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            destination = None
        if destination is not None:
            if not stat.S_ISDIR(destination.st_mode):
                return None
            destination_inode = (destination.st_dev, destination.st_ino)
            if destination_inode == staging_inode:
                return True
        try:
            staging = os.stat(
                staging_leaf, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            staging = None
        if staging is not None:
            if not stat.S_ISDIR(staging.st_mode):
                return None
            staging_named_inode = (staging.st_dev, staging.st_ino)
            if staging_named_inode == staging_inode:
                return False
    except BaseException:
        return None
    return None
def _observe_receipt_presence(parent_fd: int, destination_leaf: str) -> bool | None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        return None
    destination_fd: int | None = None
    observed: bool | None = None
    try:
        destination_fd = os.open(
            destination_leaf,
            os.O_RDONLY | directory_flag | nofollow,
            dir_fd=parent_fd,
        )
        try:
            os.stat(_RECEIPT_NAME, dir_fd=destination_fd, follow_symlinks=False)
        except FileNotFoundError:
            observed = False
        else:
            observed = True
    except FileNotFoundError:
        observed = False
    except BaseException:
        observed = None
    if destination_fd is not None:
        try:
            os.close(destination_fd)
        except BaseException:
            observed = None
    return observed








def _validate_recovery_closure(
    receipt_raw: bytes,
    payload_raw: bytes,
    *,
    expected_receipt_sha256: str,
) -> dict[str, Any]:
    if _sha256(receipt_raw) != expected_receipt_sha256:
        raise RuntimeError("canonical recovery receipt bytes changed")
    receipt = json.loads(receipt_raw)
    if not isinstance(receipt, dict):
        raise RuntimeError("canonical recovery receipt is not an object")
    if (
        receipt.get("schema_version") not in {_BUILD_SCHEMA, _CAPABILITY_BUILD_SCHEMA}
        or receipt.get("publication_state") != "complete"
        or receipt.get("passed") is not True
        or receipt.get("payload_path") != _PAYLOAD_NAME
        or receipt.get("payload_manifest_member") != _MANIFEST_MEMBER
    ):
        raise RuntimeError("canonical recovery receipt contract is invalid")
    payload_size = receipt.get("payload_size_bytes")
    manifest_size = receipt.get("payload_manifest_size_bytes")
    if (
        type(payload_size) is not int
        or payload_size != len(payload_raw)
        or receipt.get("payload_sha256") != _sha256(payload_raw)
        or type(manifest_size) is not int
    ):
        raise RuntimeError("canonical recovery payload closure is invalid")
    with zipfile.ZipFile(io.BytesIO(payload_raw), "r") as archive:
        manifest_raw = archive.read(_MANIFEST_MEMBER)
    if (
        manifest_size != len(manifest_raw)
        or receipt.get("payload_manifest_sha256") != _sha256(manifest_raw)
    ):
        raise RuntimeError("canonical recovery manifest closure is invalid")
    return receipt


def _validate_published_closure(
    *,
    parent: Path,
    parent_fd: int,
    leaf: str,
    directory_inode: tuple[int, int],
    payload_inode: tuple[int, int],
    payload_sha256: str,
    payload_size_bytes: int,
    receipt_inode: tuple[int, int],
    receipt_sha256: str,
    receipt_size_bytes: int,
    stage: str,
) -> dict[str, Any]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise RuntimeError("exclusive no-follow closure validation is unsupported")
    directory_fd = os.open(
        leaf,
        os.O_RDONLY | directory_flag | nofollow,
        dir_fd=parent_fd,
    )
    failure: BaseException | None = None
    receipt: dict[str, Any] | None = None
    try:
        _revalidate_destination(
            parent_fd,
            leaf,
            directory_inode,
            parent=parent,
            stage=stage,
            directory_fd=directory_fd,
        )
        _assert_exact_tuple_at(directory_fd)
        receipt_raw = _read_regular_at(
            directory_fd,
            _RECEIPT_NAME,
            expected_inode=receipt_inode,
            expected_size=receipt_size_bytes,
            expected_sha256=receipt_sha256,
            expected_mode=0o444,
        )
        payload_raw = _read_regular_at(
            directory_fd,
            _PAYLOAD_NAME,
            expected_inode=payload_inode,
            expected_size=payload_size_bytes,
            expected_sha256=payload_sha256,
            expected_mode=0o444,
        )
        _assert_exact_tuple_at(directory_fd)
        _revalidate_destination(
            parent_fd,
            leaf,
            directory_inode,
            parent=parent,
            stage=stage,
            directory_fd=directory_fd,
        )
        receipt = _validate_recovery_closure(
            receipt_raw,
            payload_raw,
            expected_receipt_sha256=receipt_sha256,
        )
    except BaseException as error:
        failure = error
    try:
        os.close(directory_fd)
    except BaseException as error:
        if failure is None:
            failure = error
    if failure is not None:
        raise failure
    if receipt is None:
        raise RuntimeError("published closure was not established")
    return receipt


def recover_publication(
    recovery: PublicationRecoveryRequired,
) -> dict[str, Any]:
    if not isinstance(recovery, PublicationRecoveryRequired):
        raise TypeError("recovery must be PublicationRecoveryRequired")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise RuntimeError("exclusive no-follow recovery is unsupported")
    parent = recovery.destination.parent
    leaf = recovery.destination.name
    staging_leaf = recovery.staging.name
    parent_fd: int | None = None
    staging_fd: int | None = None
    receipt: dict[str, Any] | None = None
    failure: BaseException | None = None
    committed = recovery.committed
    receipt_presence: bool | None = recovery.receipt_presence
    nonpublication_established = False
    stage = "recovery_parent_open"
    try:
        parent_fd = os.open(parent, os.O_RDONLY | directory_flag | nofollow)
        _revalidate_parent(
            parent,
            parent_fd,
            recovery.parent_inode,
            stage="recovery_parent_validation",
        )
        stage = "recovery_destination_identity"
        observed = _observe_directory_publication(
            parent_fd,
            staging_leaf,
            leaf,
            recovery.staging_inode,
        )
        if observed is None or (recovery.committed is True and not observed):
            raise RuntimeError("atomic directory publication presence is unknown")

        if observed:
            committed = True
            stage = "recovery_committed_parent"
            _fsync(parent_fd, stage)
            _revalidate_parent(
                parent,
                parent_fd,
                recovery.parent_inode,
                stage=stage,
            )
            stage = "recovery_committed_closure"
            receipt = _validate_published_closure(
                parent=parent,
                parent_fd=parent_fd,
                leaf=leaf,
                directory_inode=recovery.staging_inode,
                payload_inode=recovery.payload_inode,
                payload_sha256=recovery.payload_sha256,
                payload_size_bytes=recovery.payload_size_bytes,
                receipt_inode=recovery.receipt_inode,
                receipt_sha256=recovery.receipt_sha256,
                receipt_size_bytes=recovery.receipt_size_bytes,
                stage=stage,
            )
        else:
            committed = False
            stage = "recovery_nonpublication"
            staging_fd = os.open(
                staging_leaf,
                os.O_RDONLY | directory_flag | nofollow,
                dir_fd=parent_fd,
            )
            _revalidate_staging(
                parent_fd,
                staging_leaf,
                recovery.staging_inode,
                parent=parent,
                stage=stage,
                directory_fd=staging_fd,
            )
            _assert_exact_tuple_at(staging_fd)
            _read_regular_at(
                staging_fd,
                _RECEIPT_NAME,
                expected_inode=recovery.receipt_inode,
                expected_size=recovery.receipt_size_bytes,
                expected_sha256=recovery.receipt_sha256,
                expected_mode=0o444,
            )
            _read_regular_at(
                staging_fd,
                _PAYLOAD_NAME,
                expected_inode=recovery.payload_inode,
                expected_size=recovery.payload_size_bytes,
                expected_sha256=recovery.payload_sha256,
                expected_mode=0o444,
            )
            nonpublication_established = True
            _cleanup_failed_publication(
                parent_fd=parent_fd,
                staging_leaf=staging_leaf,
                staging_fd=staging_fd,
                staging_inode=recovery.staging_inode,
                payload_inode=recovery.payload_inode,
                receipt_inode=recovery.receipt_inode,
            )
            raise recovery.primary_error
    except BaseException as error:
        failure = error
        if parent_fd is not None:
            receipt_presence = _observe_receipt_presence(parent_fd, leaf)
    for fd in (staging_fd, parent_fd):
        if fd is None:
            continue
        try:
            os.close(fd)
        except BaseException as error:
            if failure is None:
                failure = error
                stage = "recovery_close"
    if failure is not None:
        if nonpublication_established:
            raise failure
        followup = PublicationRecoveryRequired(
            destination=recovery.destination,
            staging=recovery.staging,
            stage=stage,
            staging_inode=recovery.staging_inode,
            parent_inode=recovery.parent_inode,
            payload_inode=recovery.payload_inode,
            payload_sha256=recovery.payload_sha256,
            payload_size_bytes=recovery.payload_size_bytes,
            receipt_inode=recovery.receipt_inode,
            receipt_sha256=recovery.receipt_sha256,
            receipt_size_bytes=recovery.receipt_size_bytes,
            committed=committed,
            receipt_presence=receipt_presence,
            primary_error=failure,
        )
        raise followup from failure
    if receipt is None:
        raise RuntimeError("publication recovery did not establish atomic commit")
    return receipt


def _publish_exclusive(
    *, parent: Path, leaf: str, payload_raw: bytes, receipt_raw: bytes
) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise RuntimeError("exclusive no-follow directory publication is unsupported")
    parent_fd = os.open(parent, os.O_RDONLY | directory_flag | nofollow)
    staging_fd: int | None = None
    staging_inode: tuple[int, int] | None = None
    parent_inode: tuple[int, int] | None = None
    payload_inode: tuple[int, int] | None = None
    receipt_inode: tuple[int, int] | None = None
    staging_leaf: str | None = None
    payload_sha256 = _sha256(payload_raw)
    receipt_sha256 = _sha256(receipt_raw)
    publication_state = PublicationState.INITIAL
    rename_entered = False
    committed: bool | None = False
    receipt_presence: bool | None = None
    stage = "initial_parent_validation"
    failure: BaseException | None = None
    try:
        opened_parent = os.fstat(parent_fd)
        parent_inode = (opened_parent.st_dev, opened_parent.st_ino)
        _revalidate_parent(parent, parent_fd, parent_inode, stage=stage)

        stage = "staging_directory"
        for _ in range(128):
            candidate = _STAGING_PREFIX + os.urandom(16).hex()
            try:
                os.mkdir(candidate, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            staging_leaf = candidate
            break
        if staging_leaf is None:
            raise RuntimeError("could not allocate an unpredictable staging directory")
        staged = os.stat(
            staging_leaf, dir_fd=parent_fd, follow_symlinks=False
        )
        if not stat.S_ISDIR(staged.st_mode):
            raise RuntimeError("owned staging path is not a directory")
        staging_inode = (staged.st_dev, staged.st_ino)
        staging_fd = os.open(
            staging_leaf,
            os.O_RDONLY | directory_flag | nofollow,
            dir_fd=parent_fd,
        )
        os.fchmod(staging_fd, 0o700)
        _revalidate_staging(
            parent_fd,
            staging_leaf,
            staging_inode,
            parent=parent,
            stage=stage,
            directory_fd=staging_fd,
        )
        _assert_empty_directory(staging_fd)
        _fsync(staging_fd, stage)
        publication_state = PublicationState.STAGING_CREATED
        _fsync(parent_fd, "staging_parent")

        stage = "staging_payload_file"
        payload_inode = _write_fsynced_at(
            staging_fd,
            _PAYLOAD_NAME,
            payload_raw,
            0o444,
            stage=stage,
        )
        publication_state = PublicationState.PAYLOAD_DURABLE

        stage = "staging_receipt_file"
        receipt_inode = _write_fsynced_at(
            staging_fd,
            _RECEIPT_NAME,
            receipt_raw,
            0o444,
            stage=stage,
        )

        stage = "staging_tuple_durable"
        _assert_exact_tuple_at(staging_fd)
        _read_regular_at(
            staging_fd,
            _RECEIPT_NAME,
            expected_inode=receipt_inode,
            expected_size=len(receipt_raw),
            expected_sha256=receipt_sha256,
            expected_mode=0o444,
        )
        _read_regular_at(
            staging_fd,
            _PAYLOAD_NAME,
            expected_inode=payload_inode,
            expected_size=len(payload_raw),
            expected_sha256=payload_sha256,
            expected_mode=0o444,
        )
        _revalidate_staging(
            parent_fd,
            staging_leaf,
            staging_inode,
            parent=parent,
            stage=stage,
            directory_fd=staging_fd,
        )
        _fsync(staging_fd, stage)
        publication_state = PublicationState.TUPLE_DURABLE

        stage = "precommit_staging_receipt"
        _read_regular_at(
            staging_fd,
            _RECEIPT_NAME,
            expected_inode=receipt_inode,
            expected_size=len(receipt_raw),
            expected_sha256=receipt_sha256,
            expected_mode=0o444,
        )
        _publication_checkpoint(stage)
        _read_regular_at(
            staging_fd,
            _RECEIPT_NAME,
            expected_inode=receipt_inode,
            expected_size=len(receipt_raw),
            expected_sha256=receipt_sha256,
            expected_mode=0o444,
        )

        stage = "precommit_staging_payload"
        _read_regular_at(
            staging_fd,
            _PAYLOAD_NAME,
            expected_inode=payload_inode,
            expected_size=len(payload_raw),
            expected_sha256=payload_sha256,
            expected_mode=0o444,
        )
        _publication_checkpoint(stage)
        _read_regular_at(
            staging_fd,
            _PAYLOAD_NAME,
            expected_inode=payload_inode,
            expected_size=len(payload_raw),
            expected_sha256=payload_sha256,
            expected_mode=0o444,
        )

        stage = "precommit_staging_directory"
        _assert_exact_tuple_at(staging_fd)
        _revalidate_staging(
            parent_fd,
            staging_leaf,
            staging_inode,
            parent=parent,
            stage=stage,
            directory_fd=staging_fd,
        )
        _publication_checkpoint(stage)
        _assert_exact_tuple_at(staging_fd)
        _revalidate_staging(
            parent_fd,
            staging_leaf,
            staging_inode,
            parent=parent,
            stage=stage,
            directory_fd=staging_fd,
        )

        stage = "precommit_parent"
        _revalidate_parent(parent, parent_fd, parent_inode, stage=stage)
        _publication_checkpoint(stage)
        _revalidate_parent(parent, parent_fd, parent_inode, stage=stage)

        stage = "atomic_directory_precondition"
        _assert_exact_tuple_at(staging_fd)
        _read_regular_at(
            staging_fd,
            _RECEIPT_NAME,
            expected_inode=receipt_inode,
            expected_size=len(receipt_raw),
            expected_sha256=receipt_sha256,
            expected_mode=0o444,
        )
        _read_regular_at(
            staging_fd,
            _PAYLOAD_NAME,
            expected_inode=payload_inode,
            expected_size=len(payload_raw),
            expected_sha256=payload_sha256,
            expected_mode=0o444,
        )
        _revalidate_staging(
            parent_fd,
            staging_leaf,
            staging_inode,
            parent=parent,
            stage=stage,
            directory_fd=staging_fd,
        )
        _revalidate_parent(parent, parent_fd, parent_inode, stage=stage)

        stage = "atomic_directory_rename"
        rename_entered = True
        committed = None
        publication_state = PublicationState.DIRECTORY_PUBLICATION_UNKNOWN
        try:
            _rename_directory_noreplace_at(parent_fd, staging_leaf, leaf)
        except FileExistsError:
            rename_entered = False
            committed = False
            publication_state = (
                PublicationState.DIRECTORY_NONPUBLICATION_ESTABLISHED
            )
            raise
        committed = True
        publication_state = (
            PublicationState.DIRECTORY_PUBLISHED_DURABILITY_UNKNOWN
        )
        _publication_checkpoint("directory_committed")

        stage = "committed_parent"
        _fsync(parent_fd, stage)
        _revalidate_parent(parent, parent_fd, parent_inode, stage=stage)

        stage = "postcommit_closure"
        _validate_published_closure(
            parent=parent,
            parent_fd=parent_fd,
            leaf=leaf,
            directory_inode=staging_inode,
            payload_inode=payload_inode,
            payload_sha256=payload_sha256,
            payload_size_bytes=len(payload_raw),
            receipt_inode=receipt_inode,
            receipt_sha256=receipt_sha256,
            receipt_size_bytes=len(receipt_raw),
            stage=stage,
        )
        publication_state = PublicationState.COMPLETE
    except BaseException as error:
        failure = error
        if rename_entered and parent_fd is not None:
            receipt_presence = _observe_receipt_presence(parent_fd, leaf)
        if not rename_entered:
            _cleanup_failed_publication(
                parent_fd=parent_fd,
                staging_leaf=staging_leaf,
                staging_fd=staging_fd,
                staging_inode=staging_inode,
                payload_inode=payload_inode,
                receipt_inode=receipt_inode,
            )
    for fd in (staging_fd, parent_fd):
        if fd is None:
            continue
        try:
            os.close(fd)
        except BaseException as error:
            if failure is None:
                failure = error
                stage = "close"
    if failure is not None:
        if rename_entered:
            if (
                staging_leaf is None
                or staging_inode is None
                or parent_inode is None
                or payload_inode is None
                or receipt_inode is None
            ):
                raise RuntimeError(
                    "atomic tuple publication identities were not established"
                ) from failure
            recovery = PublicationRecoveryRequired(
                destination=parent / leaf,
                staging=parent / staging_leaf,
                stage=stage,
                staging_inode=staging_inode,
                parent_inode=parent_inode,
                payload_inode=payload_inode,
                payload_sha256=payload_sha256,
                payload_size_bytes=len(payload_raw),
                receipt_inode=receipt_inode,
                receipt_sha256=receipt_sha256,
                receipt_size_bytes=len(receipt_raw),
                committed=committed,
                receipt_presence=receipt_presence,
                primary_error=failure,
            )
            raise recovery from failure
        raise failure
    if publication_state is not PublicationState.COMPLETE:
        raise RuntimeError("transport smoke publication did not complete")


def build(
    *,
    destination: Path,
    command_id: str,
    requested_target_run_id: str,
    runner_source_sha256: str,
    runner_test_sha256: str,
) -> dict[str, Any]:
    component_input = {
        "command_id": command_id,
        "fixed_nonce_sha256": _sha256(_NONCE),
        "requested_target_run_id": requested_target_run_id,
        "runner_source_sha256": runner_source_sha256,
        "runner_test_sha256": runner_test_sha256,
    }
    component_input_sha256 = _sha256(_canonical(component_input))
    first, manifest_raw = construct_transport_smoke_payload(component_input)
    second, second_manifest_raw = construct_transport_smoke_payload(component_input)
    if first != second or manifest_raw != second_manifest_raw:
        raise RuntimeError("transport smoke payload build is nondeterministic")

    receipt = {
        "admission_binding": (
            "authority_admission_sha256_equals_canonical_receipt_sha256"
        ),
        "admission_revalidation_required": True,
        "campaign_admission": False,
        "command_id": command_id,
        "component_identity": {
            "component": _COMPONENT,
            "report_id": _REPORT_ID,
            "schema_version": _REPORT_SCHEMA,
        },
        "component_input": component_input,
        "component_input_sha256": component_input_sha256,
        "deterministic_double_build": True,
        "fixed_nonce_sha256": component_input["fixed_nonce_sha256"],
        "incomplete_without_receipt": True,
        "passed": True,
        "payload_manifest_member": _MANIFEST_MEMBER,
        "payload_manifest_sha256": _sha256(manifest_raw),
        "payload_manifest_size_bytes": len(manifest_raw),
        "payload_path": _PAYLOAD_NAME,
        "payload_sha256": _sha256(first),
        "payload_size_bytes": len(first),
        "publication_guarantee": "atomic_visibility_only",
        "publication_state": "complete",
        "requested_target_run_id": requested_target_run_id,
        "runner_source_sha256": runner_source_sha256,
        "runner_test_sha256": runner_test_sha256,
        "same_uid_mutation_exclusion": False,
        "schema_version": _BUILD_SCHEMA,
        "target_execution": False,
        "transport_authority": False,
        "claim_boundary": (
            "local_deterministic_build_and_cooperative_atomic_visibility_only"
        ),
    }
    receipt_raw = _canonical(receipt)

    destination = Path(destination)
    leaf = destination.name
    if leaf in {"", ".", ".."}:
        raise ValueError("destination must name a child of its parent")
    parent = destination.parent.resolve(strict=True)
    _publish_exclusive(
        parent=parent,
        leaf=leaf,
        payload_raw=first,
        receipt_raw=receipt_raw,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--requested-target-run-id", required=True)
    parser.add_argument("--runner-source-sha256", required=True)
    parser.add_argument("--runner-test-sha256", required=True)
    args = parser.parse_args()
    receipt = build(
        destination=args.destination,
        command_id=args.command_id,
        requested_target_run_id=args.requested_target_run_id,
        runner_source_sha256=args.runner_source_sha256,
        runner_test_sha256=args.runner_test_sha256,
    )
    print(_canonical(receipt).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
