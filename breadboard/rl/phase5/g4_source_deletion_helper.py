from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import sys

_AT_REMOVEDIR = 0x80 if sys.platform == "darwin" else 0x200
_ENTRY_NAME = "owned"
_DIGEST_PREFIX = "sha256:"
_REQUEST_SCHEMA = "bb.rl.g4.source-deletion-helper-request.v2"
_RESULT_SCHEMA = "bb.rl.g4.source-deletion-helper-result.v2"
_SUCCESS_SCHEMA = "bb.rl.g4.source-deletion-helper-success.v1"
_SUCCESS_PREFIX = ".success."
_MAX_BYTES = 4096
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}")
_HEX_RE = re.compile(r"[0-9a-f]{64}")

# Resolve the syscall before any deletion operation.  The forked child uses this
# already-loaded function and never performs an executable or module path lookup.
_LIBC = ctypes.CDLL(None, use_errno=True)
_UNLINKAT = _LIBC.unlinkat
_UNLINKAT.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
_UNLINKAT.restype = ctypes.c_int


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_request(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_BYTES:
        raise RuntimeError("helper_request_size_invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("helper_request_invalid") from exc
    if type(value) is not dict or set(value) != {
        "ctime_ns",
        "device",
        "gid",
        "inode",
        "kind",
        "link_count",
        "mtime_ns",
        "mode",
        "schema_version",
        "sha256",
        "size_bytes",
        "success_record",
        "success_record_name",
        "uid",
    }:
        raise RuntimeError("helper_request_schema_invalid")
    if _canonical(value) != raw or value["schema_version"] != _REQUEST_SCHEMA:
        raise RuntimeError("helper_request_not_canonical")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        raise RuntimeError(f"{name}_invalid")
    parsed = int(value)
    if parsed < 0 or str(parsed) != value:
        raise RuntimeError(f"{name}_invalid")
    return parsed


def _unlinkat(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    expected_metadata: tuple[int, int, int, int, int, int, int, int],
) -> None:
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    observed = (
        named.st_dev,
        named.st_ino,
        named.st_ctime_ns,
        named.st_mode,
        named.st_uid,
        named.st_gid,
        named.st_nlink,
        named.st_mtime_ns,
    )
    if observed != expected_metadata:
        raise RuntimeError("source_final_syscall_metadata_changed")
    result = _UNLINKAT(
        parent_fd,
        ctypes.c_char_p(os.fsencode(name)),
        _AT_REMOVEDIR if directory else 0,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)


def _digest(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return _DIGEST_PREFIX + hasher.hexdigest()
        hasher.update(chunk)


def _descriptor_path(descriptor: int) -> bytes | None:
    if sys.platform != "darwin":
        return None
    command = getattr(fcntl, "F_GETPATH", None)
    if command is None:
        raise RuntimeError("descriptor_path_authority_unavailable")
    raw = fcntl.fcntl(descriptor, command, b"\0" * 1024)
    if type(raw) is not bytes:
        raise RuntimeError("descriptor_path_authority_invalid")
    path = raw.split(b"\0", 1)[0]
    if not path or not path.startswith(b"/"):
        raise RuntimeError("descriptor_path_authority_invalid")
    return path


def _write_success_record(capsule_fd: int, name: str, raw: bytes) -> None:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int or no_follow == 0:
        raise RuntimeError("success_record_nofollow_unavailable")
    temporary = name + ".tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
        0o600,
        dir_fd=capsule_fd,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("success_record_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rename(
        temporary,
        name,
        src_dir_fd=capsule_fd,
        dst_dir_fd=capsule_fd,
    )
    os.fsync(capsule_fd)


def _success_record(request: dict[str, object]) -> tuple[str, bytes]:
    record = request["success_record"]
    name = request["success_record_name"]
    if type(record) is not dict or type(name) is not str:
        raise RuntimeError("success_record_invalid")
    if set(record) != {
        "authority_signature",
        "capsule_identity",
        "helper_semantic_digest",
        "intent_digest",
        "operation_id",
        "operation_key",
        "owned_entry_snapshot",
        "postconditions",
        "private_root_identity",
        "request_digest",
        "root_identity",
        "schema_version",
        "source_key",
        "transition_digest",
    }:
        raise RuntimeError("success_record_schema_invalid")
    raw = _canonical(record)
    raw_digest = hashlib.sha256(raw).hexdigest()
    if (
        record["schema_version"] != _SUCCESS_SCHEMA
        or name != f"{_SUCCESS_PREFIX}{raw_digest}.json"
        or type(record["authority_signature"]) is not str
        or _HMAC_RE.fullmatch(record["authority_signature"]) is None
        or type(record["operation_id"]) is not str
        or not record["operation_id"]
        or type(record["operation_key"]) is not str
        or _HEX_RE.fullmatch(record["operation_key"]) is None
        or type(record["source_key"]) is not str
        or not record["source_key"]
    ):
        raise RuntimeError("success_record_invalid")
    for field in (
        "helper_semantic_digest",
        "intent_digest",
        "request_digest",
        "transition_digest",
    ):
        value = record[field]
        if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
            raise RuntimeError("success_record_digest_invalid")
    for field in (
        "capsule_identity",
        "private_root_identity",
        "root_identity",
    ):
        identity = record[field]
        if type(identity) is not dict or set(identity) != {"device", "inode"}:
            raise RuntimeError("success_record_identity_invalid")
        _integer(identity["device"], f"success_record_{field}_device")
        _integer(identity["inode"], f"success_record_{field}_inode")
    snapshot = record["owned_entry_snapshot"]
    if type(snapshot) is not dict or set(snapshot) != {
        "atime_ns",
        "ctime_ns",
        "device",
        "gid",
        "inode",
        "kind",
        "link_count",
        "mode",
        "mtime_ns",
        "sha256",
        "size_bytes",
        "uid",
    }:
        raise RuntimeError("success_record_snapshot_invalid")
    expected_snapshot = {
        field: request[field]
        for field in (
            "ctime_ns",
            "device",
            "gid",
            "inode",
            "kind",
            "link_count",
            "mode",
            "mtime_ns",
            "sha256",
            "size_bytes",
            "uid",
        )
    }
    expected_snapshot["atime_ns"] = snapshot["atime_ns"]
    if snapshot != expected_snapshot:
        raise RuntimeError("success_record_snapshot_invalid")
    _integer(snapshot["atime_ns"], "success_record_atime_ns")
    _integer(snapshot["mtime_ns"], "success_record_mtime_ns")
    if record["postconditions"] != {
        "capsule_entries": [],
        "parent_name_absent": True,
        "retained_inode_terminal": True,
    }:
        raise RuntimeError("success_record_postconditions_invalid")
    return name, raw


def delete_capsule(capsule_fd: int, request_raw: bytes) -> bytes:
    """Delete one verified capsule entry and return bounded canonical proof bytes."""

    if type(capsule_fd) is not int or capsule_fd < 0:
        raise RuntimeError("capsule_fd_invalid")
    request = _parse_request(request_raw)
    success_record_name, success_record_raw = _success_record(request)
    mtime_ns = _integer(request["mtime_ns"], "mtime_ns")
    device = _integer(request["device"], "device")
    inode = _integer(request["inode"], "inode")
    ctime_ns = _integer(request["ctime_ns"], "ctime_ns")
    mode = _integer(request["mode"], "mode")
    uid = _integer(request["uid"], "uid")
    gid = _integer(request["gid"], "gid")
    link_count = _integer(request["link_count"], "link_count")
    size_bytes = _integer(request["size_bytes"], "size_bytes")
    kind = request["kind"]
    sha256 = request["sha256"]
    if kind not in {"file", "directory"}:
        raise RuntimeError("kind_invalid")
    if type(sha256) is not str or _DIGEST_RE.fullmatch(sha256) is None:
        raise RuntimeError("sha256_invalid")
    if (
        (kind == "file" and (not stat.S_ISREG(mode) or link_count != 1))
        or (kind == "directory" and (not stat.S_ISDIR(mode) or link_count < 2))
        or stat.S_ISLNK(mode)
    ):
        raise RuntimeError("source_request_metadata_invalid")

    capsule = os.fstat(capsule_fd)
    capsule_identity = (
        capsule.st_dev,
        capsule.st_ino,
        capsule.st_mode,
        capsule.st_uid,
        capsule.st_gid,
    )
    if (
        not stat.S_ISDIR(capsule.st_mode)
        or stat.S_IMODE(capsule.st_mode) != 0
        or capsule.st_uid != os.geteuid()
        or capsule.st_gid != os.getegid()
        or capsule.st_nlink < 2
    ):
        raise RuntimeError("capsule_authority_invalid")
    success_document = request["success_record"]
    assert isinstance(success_document, dict)
    success_capsule_identity = success_document["capsule_identity"]
    assert isinstance(success_capsule_identity, dict)
    if success_capsule_identity != {
        "device": str(capsule.st_dev),
        "inode": str(capsule.st_ino),
    }:
        raise RuntimeError("success_record_capsule_identity_invalid")

    os.fchmod(capsule_fd, 0o300)
    descriptor = -1
    locked = False
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if kind == "directory":
            flags |= getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(_ENTRY_NAME, flags, dir_fd=capsule_fd)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        opened = os.fstat(descriptor)
        named = os.stat(_ENTRY_NAME, dir_fd=capsule_fd, follow_symlinks=False)
        descriptor_path = _descriptor_path(descriptor)
        expected_metadata = (
            device,
            inode,
            ctime_ns,
            mode,
            uid,
            gid,
            link_count,
            mtime_ns,
        )
        if (
            (
                opened.st_dev,
                opened.st_ino,
                opened.st_ctime_ns,
                opened.st_mode,
                opened.st_uid,
                opened.st_gid,
                opened.st_nlink,
                opened.st_mtime_ns,
            )
            != expected_metadata
            or (
                named.st_dev,
                named.st_ino,
                named.st_ctime_ns,
                named.st_mode,
                named.st_uid,
                named.st_gid,
                named.st_nlink,
                named.st_mtime_ns,
            )
            != expected_metadata
        ):
            raise RuntimeError("source_metadata_mismatch")
        if kind == "file":
            if opened.st_size != size_bytes or _digest(descriptor) != sha256:
                raise RuntimeError("source_content_mismatch")
        elif os.listdir(descriptor):
            raise RuntimeError("source_directory_invalid")

        final_named = os.stat(_ENTRY_NAME, dir_fd=capsule_fd, follow_symlinks=False)
        final_opened = os.fstat(descriptor)
        if (
            (
                final_opened.st_dev,
                final_opened.st_ino,
                final_opened.st_ctime_ns,
                final_opened.st_mode,
                final_opened.st_uid,
                final_opened.st_gid,
                final_opened.st_nlink,
                final_opened.st_mtime_ns,
            )
            != expected_metadata
            or (
                final_named.st_dev,
                final_named.st_ino,
                final_named.st_ctime_ns,
                final_named.st_mode,
                final_named.st_uid,
                final_named.st_gid,
                final_named.st_nlink,
                final_named.st_mtime_ns,
            )
            != expected_metadata
        ):
            raise RuntimeError("source_metadata_changed")
        if kind == "file":
            if final_opened.st_size != size_bytes or _digest(descriptor) != sha256:
                raise RuntimeError("source_content_changed")
        elif os.listdir(descriptor):
            raise RuntimeError("source_directory_changed")

        _unlinkat(
            capsule_fd,
            _ENTRY_NAME,
            directory=kind == "directory",
            expected_metadata=expected_metadata,
        )
        os.fsync(capsule_fd)
        try:
            replacement = os.stat(
                _ENTRY_NAME,
                dir_fd=capsule_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            if exc.errno != errno.ENOENT:
                raise
        else:
            if (replacement.st_dev, replacement.st_ino) == (device, inode):
                raise RuntimeError("source_name_survived_remove")
            raise RuntimeError("source_name_replaced")
        if os.listdir(capsule_fd):
            raise RuntimeError("capsule_inventory_not_empty")
        after_capsule = os.fstat(capsule_fd)
        if (
            after_capsule.st_dev != capsule_identity[0]
            or after_capsule.st_ino != capsule_identity[1]
            or after_capsule.st_uid != capsule_identity[3]
            or after_capsule.st_gid != capsule_identity[4]
            or not stat.S_ISDIR(after_capsule.st_mode)
            or stat.S_IMODE(after_capsule.st_mode) != 0o300
        ):
            raise RuntimeError("capsule_authority_changed")
        after = os.fstat(descriptor)
        if (
            after.st_dev != device
            or after.st_ino != inode
            or after.st_mode != mode
            or after.st_uid != uid
            or after.st_gid != gid
        ):
            raise RuntimeError("source_post_remove_metadata_mismatch")
        if kind == "file" and after.st_nlink != 0:
            raise RuntimeError("source_link_survived_remove")
        if kind == "directory":
            if sys.platform.startswith("linux") and after.st_nlink != 0:
                raise RuntimeError("source_link_survived_remove")
            if sys.platform != "darwin" and not sys.platform.startswith("linux"):
                raise RuntimeError("directory_link_semantics_unsupported")
            if _descriptor_path(descriptor) != descriptor_path:
                raise RuntimeError("source_directory_path_changed")
        namespace_link_count = 0
        if kind == "file":
            if after.st_size != size_bytes or _digest(descriptor) != sha256:
                raise RuntimeError("source_post_remove_content_mismatch")
        elif os.listdir(descriptor):
            raise RuntimeError("source_post_remove_directory_changed")
        _write_success_record(
            capsule_fd,
            success_record_name,
            success_record_raw,
        )
        if os.listdir(capsule_fd) != [success_record_name]:
            raise RuntimeError("success_record_inventory_invalid")
        return _canonical(
            {
                "capsule_entries": [],
                "device": str(after.st_dev),
                "gid": str(after.st_gid),
                "inode": str(after.st_ino),
                "kind": kind,
                "link_count": str(namespace_link_count),
                "observed_inode_link_count": str(after.st_nlink),
                "mode": str(after.st_mode),
                "parent_name_absent": True,
                "prior_ctime_ns": str(ctime_ns),
                "prior_link_count": str(link_count),
                "schema_version": _RESULT_SCHEMA,
                "status": "deleted",
                "uid": str(after.st_uid),
                "success_record_digest": (
                    _DIGEST_PREFIX + hashlib.sha256(success_record_raw).hexdigest()
                ),
                "success_record_name": success_record_name,
            }
        )
    finally:
        if descriptor >= 0:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        os.fchmod(capsule_fd, 0)
        os.fsync(capsule_fd)


__all__ = ["delete_capsule"]
