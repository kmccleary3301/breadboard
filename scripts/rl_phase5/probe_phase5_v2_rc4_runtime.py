from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import ctypes.util
import datetime as dt
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping, Protocol, Sequence

PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
REVISION_ID = "v2.0.0-rc4-20260715"
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:b5897c0465bfb0cdf4b3aa79427c55e85b8a1d0b600c40e6d6eb62b579e9cbfd"
)
SUPERSEDED_RC3_MANIFEST_SHA256 = (
    "sha256:57144dd1e87369cc5d0e70065846ec4b2acddcbe9020ca84ed49f84b51117d19"
)
SEALED_V1_ARCHIVE_MANIFEST_SHA256 = (
    "sha256:91519465cfc7a45d8a6375a23908753f48bf61f2d3e90f7734f20affee2ca2d8"
)
V1_ACTIVE_STATUS_SHA256 = (
    "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
)

SUMMARY_QUERY = (
    "SELECT DOLT_HASHOF('HEAD') AS head_commit, "
    "DOLT_HASHOF_DB('HEAD') AS head_root, "
    "DOLT_HASHOF_DB('STAGED') AS staged_root, "
    "DOLT_HASHOF_DB('WORKING') AS working_root, "
    "ACTIVE_BRANCH() AS branch, DOLT_VERSION() AS dolt_version"
)
STATUS_QUERY = (
    "SELECT table_name, staged, status FROM dolt_status "
    "ORDER BY table_name, staged, status"
)
TABLES_QUERY = (
    "SELECT table_name AS `table_name` FROM information_schema.tables "
    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
    "AND table_name NOT LIKE 'dolt\\_%' ESCAPE '\\\\' ORDER BY BINARY table_name"
)
SCHEMA_COLUMNS_QUERY = (
    "SELECT table_name AS `table_name`, ordinal_position AS `ordinal_position`, "
    "column_name AS `column_name`, column_type AS `column_type`, "
    "is_nullable AS `is_nullable`, column_default AS `column_default`, "
    "column_key AS `column_key`, extra AS `extra` FROM information_schema.columns "
    "WHERE table_schema = DATABASE() AND table_name NOT LIKE 'dolt\\_%' "
    "ESCAPE '\\\\' ORDER BY BINARY table_name, ordinal_position"
)
SCHEMA_CONSTRAINTS_QUERY = (
    "SELECT table_name AS `table_name`, constraint_name AS `constraint_name`, "
    "constraint_type AS `constraint_type` FROM information_schema.table_constraints "
    "WHERE table_schema = DATABASE() "
    "AND table_name NOT LIKE 'dolt\\_%' ESCAPE '\\\\' "
    "ORDER BY BINARY table_name, BINARY constraint_name, BINARY constraint_type"
)
SCHEMA_INDEXES_QUERY = (
    "SELECT table_name AS `table_name`, index_name AS `index_name`, "
    "non_unique AS `non_unique`, seq_in_index AS `seq_in_index`, "
    "column_name AS `column_name`, collation AS `collation`, "
    "sub_part AS `sub_part`, nullable AS `nullable`, index_type AS `index_type` "
    "FROM information_schema.statistics "
    "WHERE table_schema = DATABASE() AND table_name NOT LIKE 'dolt\\_%' "
    "ESCAPE '\\\\' ORDER BY BINARY table_name, BINARY index_name, seq_in_index"
)

_DOLT_HASH = re.compile(r"^[0-9a-z]{32,64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_RELEVANT_ENVIRONMENT = (
    "BEADS_DIR",
    "BEADS_DOLT_DATA_DIR",
    "BEADS_DOLT_PASSWORD",
    "BEADS_DOLT_SERVER_DATABASE",
    "BEADS_DOLT_SERVER_HOST",
    "BEADS_DOLT_SERVER_PORT",
    "BEADS_DOLT_SERVER_TLS",
    "BEADS_DOLT_SERVER_USER",
)


class RuntimeProbeError(ValueError):
    """The installed runtime cannot be proved safe and unambiguous."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    cwd: Path
    binary_path: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    execution_mode: Literal[
        "native_descriptor_bound",
        "injected_non_native_test_seam",
    ] = "injected_non_native_test_seam"
    used_binary_identity: dict[str, Any] | None = None
    used_cwd_identity: dict[str, Any] | None = None


class CommandRunner(Protocol):
    def __call__(self, argv: tuple[str, ...], cwd: Path) -> CommandResult: ...


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")

def _absolute_path(path: Path, *, kind: str) -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise RuntimeProbeError(f"{kind} path contains parent traversal: {path}")
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return Path(os.path.normpath(absolute))


def _open_directory_fd(
    path: Path,
    *,
    kind: str,
) -> tuple[Path, int, os.stat_result]:
    absolute = _absolute_path(path, kind=kind)
    flags = (
        os.O_RDONLY
        | os.O_CLOEXEC
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(absolute.anchor, flags)
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeProbeError(f"cannot open stable nofollow {kind}: {absolute}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise RuntimeProbeError(f"{kind} is not a directory: {absolute}")
    return absolute, descriptor, metadata


def _open_regular_fd(
    path: Path,
    *,
    kind: str,
) -> tuple[Path, int, os.stat_result]:
    absolute = _absolute_path(path, kind=kind)
    if absolute.name in ("", ".", ".."):
        raise RuntimeProbeError(f"{kind} has no regular-file basename: {absolute}")
    _, parent_descriptor, _ = _open_directory_fd(absolute.parent, kind=f"{kind} parent")
    flags = (
        os.O_RDONLY
        | os.O_CLOEXEC
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise RuntimeProbeError(f"cannot open stable nofollow {kind}: {absolute}") from exc
    finally:
        os.close(parent_descriptor)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise RuntimeProbeError(f"{kind} is not a regular file: {absolute}")
    return absolute, descriptor, metadata


def _nofollow_directory(path: Path, *, kind: str) -> Path:
    absolute, descriptor, _ = _open_directory_fd(path, kind=kind)
    os.close(descriptor)
    return absolute


def _nofollow_regular_file(path: Path, *, kind: str) -> Path:
    absolute, descriptor, _ = _open_regular_fd(path, kind=kind)
    os.close(descriptor)
    return absolute


def _stable_stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_descriptor(
    descriptor: int,
    before: os.stat_result,
    *,
    path: Path,
) -> tuple[bytes, os.stat_result]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        chunks.append(chunk)
        size += len(chunk)
    after = os.fstat(descriptor)
    if _stable_stat_identity(before) != _stable_stat_identity(after) or size != after.st_size:
        raise RuntimeProbeError(f"file drifted while it was read: {path}")
    payload = b"".join(chunks)
    if _sha256(payload) != "sha256:" + digest.hexdigest():
        raise AssertionError("streamed digest mismatch")
    return payload, after


def _read_regular(path: Path) -> tuple[bytes, os.stat_result]:
    absolute, descriptor, before = _open_regular_fd(path, kind="observed file")
    try:
        return _read_descriptor(descriptor, before, path=absolute)
    finally:
        os.close(descriptor)


def _parse_object(raw: bytes, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                RuntimeProbeError(f"non-finite JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeProbeError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeProbeError(f"expected JSON object: {path}")
    return value


def _load_object(path: Path) -> tuple[dict[str, Any], bytes, os.stat_result]:
    raw, metadata = _read_regular(path)
    return _parse_object(raw, path), raw, metadata


def _read_single_link_regular(
    path: Path,
    *,
    kind: str,
) -> tuple[Path, bytes, os.stat_result]:
    absolute, descriptor, before = _open_regular_fd(path, kind=kind)
    try:
        if before.st_nlink != 1:
            raise RuntimeProbeError(f"{kind} must have exactly one link: {absolute}")
        raw, after = _read_descriptor(descriptor, before, path=absolute)
        if after.st_nlink != 1:
            raise RuntimeProbeError(f"{kind} link count changed while read: {absolute}")
        return absolute, raw, after
    finally:
        os.close(descriptor)


def _single_link_identity(
    path: Path,
    payload: bytes,
    metadata: os.stat_result,
) -> dict[str, Any]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "link_count": metadata.st_nlink,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "mtime_ns": metadata.st_mtime_ns,
        "path": str(path),
        "sha256": _sha256(payload),
        "size": len(payload),
    }


def _load_single_link_object(
    path: Path,
    *,
    kind: str,
) -> tuple[dict[str, Any], bytes, os.stat_result, dict[str, Any]]:
    absolute, raw, metadata = _read_single_link_regular(path, kind=kind)
    return (
        _parse_object(raw, absolute),
        raw,
        metadata,
        _single_link_identity(absolute, raw, metadata),
    )


def _single_link_file_snapshot(path: Path, *, kind: str) -> dict[str, Any]:
    absolute, payload, metadata = _read_single_link_regular(path, kind=kind)
    return _single_link_identity(absolute, payload, metadata)


def _path_identity(kind: str, path: Path) -> dict[str, Any]:
    absolute, descriptor, metadata = _open_directory_fd(path, kind=kind)
    try:
        return {
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "kind": kind,
            "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
            "path": str(absolute),
        }
    finally:
        os.close(descriptor)


def _file_snapshot(path: Path) -> dict[str, Any]:
    payload, metadata = _read_regular(path)
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "mtime_ns": metadata.st_mtime_ns,
        "path": str(path),
        "sha256": _sha256(payload),
        "size": len(payload),
    }


def _tree_snapshot(root: Path, *, content_only: bool = False) -> dict[str, Any]:
    root, root_descriptor, root_before = _open_directory_fd(root, kind="tree root")
    entries: list[dict[str, Any]] = []
    observed_files: set[tuple[int, int]] = set()

    def entry_projection(
        relative: Path,
        metadata: os.stat_result,
        *,
        kind: Literal["directory", "file"],
        payload: bytes | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "kind": kind,
            "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
            "path": relative.as_posix() if relative.parts else ".",
        }
        if payload is not None:
            entry.update({"sha256": _sha256(payload), "size": len(payload)})
        if not content_only:
            entry.update(
                {
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                    "mtime_ns": metadata.st_mtime_ns,
                }
            )
        return entry

    def visit(directory_descriptor: int, relative: Path) -> None:
        try:
            with os.scandir(directory_descriptor) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except OSError as exc:
            raise RuntimeProbeError(f"cannot enumerate stable tree: {root / relative}") from exc
        for child in children:
            child_relative = relative / child.name
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeProbeError(f"cannot stat stable tree entry: {child_relative}") from exc
            if stat.S_ISDIR(metadata.st_mode):
                flags = (
                    os.O_RDONLY
                    | os.O_CLOEXEC
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_NONBLOCK", 0)
                )
                try:
                    child_descriptor = os.open(
                        child.name,
                        flags,
                        dir_fd=directory_descriptor,
                    )
                except OSError as exc:
                    raise RuntimeProbeError(
                        f"cannot open stable tree directory: {child_relative}"
                    ) from exc
                try:
                    opened = os.fstat(child_descriptor)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or _stable_stat_identity(opened) != _stable_stat_identity(metadata)
                    ):
                        raise RuntimeProbeError(
                            f"tree directory identity drifted: {child_relative}"
                        )
                    entries.append(
                        entry_projection(child_relative, opened, kind="directory")
                    )
                    visit(child_descriptor, child_relative)
                    after = os.fstat(child_descriptor)
                    if _stable_stat_identity(after) != _stable_stat_identity(opened):
                        raise RuntimeProbeError(
                            f"tree directory drifted while observed: {child_relative}"
                        )
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise RuntimeProbeError(
                        f"hardlink alias in confined observed tree: {root / child_relative}"
                    )
                identity = (metadata.st_dev, metadata.st_ino)
                if identity in observed_files:
                    raise RuntimeProbeError(
                        f"duplicate inode alias in confined observed tree: {root / child_relative}"
                    )
                flags = (
                    os.O_RDONLY
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW
                    | getattr(os, "O_NONBLOCK", 0)
                )
                try:
                    child_descriptor = os.open(
                        child.name,
                        flags,
                        dir_fd=directory_descriptor,
                    )
                except OSError as exc:
                    raise RuntimeProbeError(
                        f"cannot open stable tree file: {child_relative}"
                    ) from exc
                try:
                    opened = os.fstat(child_descriptor)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or _stable_stat_identity(opened) != _stable_stat_identity(metadata)
                    ):
                        raise RuntimeProbeError(f"tree file identity drifted: {child_relative}")
                    payload, after = _read_descriptor(
                        child_descriptor,
                        opened,
                        path=root / child_relative,
                    )
                finally:
                    os.close(child_descriptor)
                observed_files.add(identity)
                entries.append(
                    entry_projection(
                        child_relative,
                        after,
                        kind="file",
                        payload=payload,
                    )
                )
            else:
                raise RuntimeProbeError(
                    f"symlink or special file in confined observed tree: {root / child_relative}"
                )

    try:
        entries.append(entry_projection(Path(), root_before, kind="directory"))
        visit(root_descriptor, Path())
        root_after = os.fstat(root_descriptor)
        if _stable_stat_identity(root_after) != _stable_stat_identity(root_before):
            raise RuntimeProbeError(f"tree root drifted while observed: {root}")
    finally:
        os.close(root_descriptor)
    projection = {"entries": entries}
    snapshot: dict[str, Any] = {
        "entries": entries,
        "entry_count": len(entries),
        "sha256": _sha256(_canonical_bytes(projection)),
    }
    if not content_only:
        snapshot.update(
            {
                "device": root_before.st_dev,
                "inode": root_before.st_ino,
                "path": str(root),
            }
        )
    return snapshot


@dataclass(frozen=True, slots=True)
class _RetainedDirectory:
    path: Path
    parent_descriptor: int
    leaf_descriptor: int
    parent_identity: tuple[int, int, int, int]
    created_identity: tuple[int, ...]
    binding_identity: tuple[int, int, int, int]


def _directory_binding_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
    )


def _revalidate_retained_directory_entry(retained: _RetainedDirectory) -> None:
    parent = os.fstat(retained.parent_descriptor)
    if _directory_binding_identity(parent) != retained.parent_identity:
        raise RuntimeProbeError("isolated clone temporary parent identity drifted")
    try:
        dirent = os.stat(
            retained.path.name,
            dir_fd=retained.parent_descriptor,
            follow_symlinks=False,
        )
        opened = os.fstat(retained.leaf_descriptor)
    except OSError as exc:
        raise RuntimeProbeError(
            "isolated clone temporary root lexical leaf is no longer retained"
        ) from exc
    if (
        not stat.S_ISDIR(dirent.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or _directory_binding_identity(dirent) != retained.binding_identity
        or _directory_binding_identity(opened) != retained.binding_identity
    ):
        raise RuntimeProbeError(
            "isolated clone temporary root descriptor or dirent identity drifted"
        )


@contextlib.contextmanager
def _retained_temporary_directory(
    *,
    prefix: str,
) -> Iterator[_RetainedDirectory]:
    parent_descriptor: int | None = None
    leaf_descriptor: int | None = None
    retained: _RetainedDirectory | None = None
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary_name:
            temporary_lexical_root = Path(temporary_name)
            try:
                created = temporary_lexical_root.lstat()
            except OSError as exc:
                raise RuntimeProbeError(
                    "cannot inspect isolated clone temporary root lexical leaf"
                ) from exc
            if (
                not stat.S_ISDIR(created.st_mode)
                or created.st_uid != os.geteuid()
            ):
                raise RuntimeProbeError(
                    "isolated clone temporary root lexical leaf must be an "
                    "effective-UID-owned directory"
                )
            try:
                canonical_parent = temporary_lexical_root.parent.resolve(strict=True)
            except OSError as exc:
                raise RuntimeProbeError(
                    "cannot canonicalize isolated clone temporary root parent"
                ) from exc
            canonical_parent, parent_descriptor, parent = _open_directory_fd(
                canonical_parent,
                kind="isolated clone temporary root parent",
            )
            canonical_root = canonical_parent / temporary_lexical_root.name
            flags = (
                os.O_RDONLY
                | os.O_CLOEXEC
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | getattr(os, "O_NONBLOCK", 0)
            )
            try:
                leaf_descriptor = os.open(
                    temporary_lexical_root.name,
                    flags,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise RuntimeProbeError(
                    "cannot retain isolated clone temporary root lexical leaf"
                ) from exc
            opened = os.fstat(leaf_descriptor)
            if (
                _stable_stat_identity(opened) != _stable_stat_identity(created)
                or not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.geteuid()
            ):
                raise RuntimeProbeError(
                    "isolated clone temporary root identity changed while retained"
                )
            retained = _RetainedDirectory(
                path=canonical_root,
                parent_descriptor=parent_descriptor,
                leaf_descriptor=leaf_descriptor,
                parent_identity=_directory_binding_identity(parent),
                created_identity=_stable_stat_identity(created),
                binding_identity=_directory_binding_identity(opened),
            )
            try:
                yield retained
            finally:
                _revalidate_retained_directory_entry(retained)
                os.close(leaf_descriptor)
                leaf_descriptor = None
        assert retained is not None
        try:
            os.stat(
                retained.path.name,
                dir_fd=retained.parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeProbeError(
                "cannot verify isolated clone temporary root disposal"
            ) from exc
        else:
            raise RuntimeProbeError(
                "isolated clone temporary root was not disposed"
            )
    finally:
        if leaf_descriptor is not None:
            os.close(leaf_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _clone_tree(
    source: Path,
    destination: Path,
    *,
    destination_parent_descriptor: int | None = None,
) -> Path:
    source, source_descriptor, source_before = _open_directory_fd(
        source,
        kind="clone source",
    )
    destination_parent = _absolute_path(
        destination.parent,
        kind="clone destination parent",
    )
    if destination_parent_descriptor is None:
        destination_parent, parent_descriptor, _ = _open_directory_fd(
            destination_parent,
            kind="clone destination parent",
        )
    else:
        try:
            parent_descriptor = os.dup(destination_parent_descriptor)
            parent = os.fstat(parent_descriptor)
        except OSError as exc:
            os.close(source_descriptor)
            raise RuntimeProbeError(
                "cannot retain clone destination parent descriptor"
            ) from exc
        if not stat.S_ISDIR(parent.st_mode):
            os.close(source_descriptor)
            os.close(parent_descriptor)
            raise RuntimeProbeError("clone destination parent descriptor is not a directory")
    if destination.name in ("", ".", ".."):
        os.close(source_descriptor)
        os.close(parent_descriptor)
        raise RuntimeProbeError("clone destination must be one new basename")

    unsafe_mode_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX

    def safe_mode(metadata: os.stat_result, relative: Path) -> int:
        if metadata.st_mode & unsafe_mode_bits:
            raise RuntimeProbeError(f"clone source has unsafe mode bits: {source / relative}")
        return stat.S_IMODE(metadata.st_mode)

    try:
        os.mkdir(destination.name, 0o700, dir_fd=parent_descriptor)
        destination_flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_NONBLOCK", 0)
        )
        destination_descriptor = os.open(
            destination.name,
            destination_flags,
            dir_fd=parent_descriptor,
        )
    except BaseException:
        os.close(source_descriptor)
        os.close(parent_descriptor)
        raise

    observed_files: set[tuple[int, int]] = set()

    def copy_directory(
        source_fd: int,
        destination_fd: int,
        relative: Path,
    ) -> None:
        try:
            with os.scandir(source_fd) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except OSError as exc:
            raise RuntimeProbeError(
                f"cannot enumerate clone source: {source / relative}"
            ) from exc
        for child in children:
            child_relative = relative / child.name
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeProbeError(
                    f"cannot stat clone source entry: {source / child_relative}"
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                source_flags = (
                    os.O_RDONLY
                    | os.O_CLOEXEC
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_NONBLOCK", 0)
                )
                try:
                    child_source_fd = os.open(child.name, source_flags, dir_fd=source_fd)
                except OSError as exc:
                    raise RuntimeProbeError(
                        f"cannot open clone source directory: {source / child_relative}"
                    ) from exc
                try:
                    opened = os.fstat(child_source_fd)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or _stable_stat_identity(opened) != _stable_stat_identity(metadata)
                    ):
                        raise RuntimeProbeError(
                            f"clone source directory drifted: {child_relative}"
                        )
                    mode = safe_mode(opened, child_relative)
                    os.mkdir(child.name, 0o700, dir_fd=destination_fd)
                    child_destination_fd = os.open(
                        child.name,
                        source_flags,
                        dir_fd=destination_fd,
                    )
                    try:
                        copy_directory(
                            child_source_fd,
                            child_destination_fd,
                            child_relative,
                        )
                        after = os.fstat(child_source_fd)
                        if _stable_stat_identity(after) != _stable_stat_identity(opened):
                            raise RuntimeProbeError(
                                f"clone source directory changed: {child_relative}"
                            )
                        os.fchmod(child_destination_fd, mode)
                        os.fsync(child_destination_fd)
                    finally:
                        os.close(child_destination_fd)
                finally:
                    os.close(child_source_fd)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise RuntimeProbeError(
                        f"clone source contains unsafe hardlink alias: {source / child_relative}"
                    )
                identity = (metadata.st_dev, metadata.st_ino)
                if identity in observed_files:
                    raise RuntimeProbeError(
                        f"clone source contains duplicate inode alias: {source / child_relative}"
                    )
                source_flags = (
                    os.O_RDONLY
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW
                    | getattr(os, "O_NONBLOCK", 0)
                )
                try:
                    child_source_fd = os.open(child.name, source_flags, dir_fd=source_fd)
                except OSError as exc:
                    raise RuntimeProbeError(
                        f"cannot open clone source file: {source / child_relative}"
                    ) from exc
                try:
                    before = os.fstat(child_source_fd)
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_nlink != 1
                        or _stable_stat_identity(before) != _stable_stat_identity(metadata)
                    ):
                        raise RuntimeProbeError(
                            f"clone source file drifted: {child_relative}"
                        )
                    mode = safe_mode(before, child_relative)
                    destination_flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_CLOEXEC
                        | os.O_NOFOLLOW
                        | getattr(os, "O_NONBLOCK", 0)
                    )
                    child_destination_fd = os.open(
                        child.name,
                        destination_flags,
                        0o600,
                        dir_fd=destination_fd,
                    )
                    try:
                        copied = 0
                        while True:
                            chunk = os.read(child_source_fd, 1024 * 1024)
                            if not chunk:
                                break
                            view = memoryview(chunk)
                            while view:
                                written = os.write(child_destination_fd, view)
                                if written <= 0:
                                    raise RuntimeProbeError(
                                        f"short clone write: {child_relative}"
                                    )
                                copied += written
                                view = view[written:]
                        after = os.fstat(child_source_fd)
                        if (
                            _stable_stat_identity(after) != _stable_stat_identity(before)
                            or copied != after.st_size
                        ):
                            raise RuntimeProbeError(
                                f"clone source file changed: {child_relative}"
                            )
                        os.fchmod(child_destination_fd, mode)
                        os.fsync(child_destination_fd)
                    finally:
                        os.close(child_destination_fd)
                finally:
                    os.close(child_source_fd)
                observed_files.add(identity)
            else:
                raise RuntimeProbeError(
                    f"clone source contains symlink or special file: {source / child_relative}"
                )
        os.fsync(destination_fd)

    try:
        safe_root_mode = safe_mode(source_before, Path())
        copy_directory(source_descriptor, destination_descriptor, Path())
        source_after = os.fstat(source_descriptor)
        if _stable_stat_identity(source_after) != _stable_stat_identity(source_before):
            raise RuntimeProbeError("clone source root changed while it was copied")
        os.fchmod(destination_descriptor, safe_root_mode)
        os.fsync(destination_descriptor)
        os.fsync(parent_descriptor)
    finally:
        os.close(destination_descriptor)
        os.close(parent_descriptor)
        os.close(source_descriptor)
    return destination_parent / destination.name


def _candidate_inventory(root: Path) -> tuple[set[str], set[str]]:
    root, root_descriptor, _ = _open_directory_fd(root, kind="candidate inventory root")
    files: set[str] = set()
    directories: set[str] = set()

    def visit(directory_descriptor: int, relative: Path) -> None:
        children = sorted(os.scandir(directory_descriptor), key=lambda child: child.name)
        for child in children:
            child_relative = relative / child.name
            metadata = child.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                child_descriptor = os.open(child.name, flags, dir_fd=directory_descriptor)
                try:
                    opened = os.fstat(child_descriptor)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise RuntimeProbeError(
                            f"candidate directory identity drifted: {child_relative}"
                        )
                    directories.add(child_relative.as_posix())
                    visit(child_descriptor, child_relative)
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(child_relative.as_posix())
            else:
                raise RuntimeProbeError(
                    f"candidate contains a symlink or special file: {child_relative}"
                )

    try:
        visit(root_descriptor, Path())
    finally:
        os.close(root_descriptor)
    return files, directories


def _verify_candidate(execution_root: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    candidate = _nofollow_directory(
        execution_root / "versions" / "v2-two-track" / REVISION_ID,
        kind="rc4 candidate root",
    )
    manifest, raw, _, manifest_identity = _load_single_link_object(
        candidate / "ARTIFACT_MANIFEST.json",
        kind="rc4 artifact manifest",
    )
    manifest_path = Path(manifest_identity["path"])
    if _sha256(raw) != ARTIFACT_MANIFEST_SHA256:
        raise RuntimeProbeError("rc4 ARTIFACT_MANIFEST.json digest is not exact")
    exact_manifest_fields = {
        "archive_manifest_sha256": SEALED_V1_ARCHIVE_MANIFEST_SHA256,
        "immutable": True,
        "program_id": PROGRAM_ID,
        "revision_id": REVISION_ID,
        "superseded_artifact_manifest_sha256": SUPERSEDED_RC3_MANIFEST_SHA256,
        "superseded_revision_id": "v2.0.0-rc3-20260715",
        "v1_active_status_sha256": V1_ACTIVE_STATUS_SHA256,
    }
    for key, expected in exact_manifest_fields.items():
        if manifest.get(key) != expected:
            raise RuntimeProbeError(f"rc4 manifest binding changed: {key}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeProbeError("rc4 manifest file inventory is missing")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise RuntimeProbeError("rc4 manifest contains a non-object file entry")
        logical = entry.get("path")
        if not isinstance(logical, str) or not logical or logical in seen:
            raise RuntimeProbeError("rc4 manifest contains an invalid or duplicate path")
        relative = Path(logical)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeProbeError("rc4 manifest path escapes the candidate")
        seen.add(logical)
        artifact = candidate / relative
        payload, metadata = _read_regular(artifact)
        if len(payload) != entry.get("size") or _sha256(payload) != entry.get("sha256"):
            raise RuntimeProbeError(f"rc4 artifact does not match manifest: {logical}")
        if f"{stat.S_IMODE(metadata.st_mode):04o}" != entry.get("mode"):
            raise RuntimeProbeError(f"rc4 artifact mode does not match manifest: {logical}")
    actual_files, actual_directories = _candidate_inventory(candidate)
    expected_files = seen | {"ARTIFACT_MANIFEST.json"}
    expected_directories = {
        parent.as_posix()
        for logical in expected_files
        for parent in Path(logical).parents
        if parent != Path(".")
    }
    if actual_files != expected_files or actual_directories != expected_directories:
        raise RuntimeProbeError("rc4 candidate inventory has missing or unmanifested entries")
    return candidate, manifest, {
        "file_count": len(files),
        "manifest_path": str(manifest_path),
        "manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "manifest_identity": manifest_identity,
    }


def _verify_frozen_lineage(execution_root: Path) -> dict[str, Any]:
    expected_paths = (
        (
            "superseded_rc3_manifest",
            execution_root
            / "versions"
            / "v2-two-track"
            / "v2.0.0-rc3-20260715"
            / "ARTIFACT_MANIFEST.json",
            SUPERSEDED_RC3_MANIFEST_SHA256,
        ),
        (
            "sealed_v1_archive_manifest",
            execution_root
            / "versions"
            / "v1-bootstrap-20260709-sealed-rc3"
            / "ARCHIVE_MANIFEST.json",
            SEALED_V1_ARCHIVE_MANIFEST_SHA256,
        ),
        (
            "root_active_selector",
            execution_root / "ACTIVE_STATUS.json",
            V1_ACTIVE_STATUS_SHA256,
        ),
    )
    snapshots: dict[str, Any] = {}
    for name, original_path, digest in expected_paths:
        snapshot = _single_link_file_snapshot(original_path, kind=name)
        if snapshot["sha256"] != digest:
            raise RuntimeProbeError(f"frozen lineage digest changed: {name}")
        snapshots[name] = snapshot
    return snapshots


def _binary_identity(name: str, *, required: bool) -> dict[str, Any] | None:
    requested = shutil.which(name)
    if requested is None:
        if required:
            raise RuntimeProbeError(f"required binary is not installed: {name}")
        return None
    resolved = Path(requested).resolve(strict=True)
    snapshot = _file_snapshot(resolved)
    return {
        "name": name,
        "requested_path": requested,
        "resolved_path": str(resolved),
        "device": snapshot["device"],
        "inode": snapshot["inode"],
        "mode": snapshot["mode"],
        "mtime_ns": snapshot["mtime_ns"],
        "sha256": snapshot["sha256"],
        "size": snapshot["size"],
    }


def _private_dolt_environment_paths(cwd: Path) -> dict[str, str]:
    verified_cwd = _nofollow_directory(cwd, kind="Dolt command cwd")
    beads_ancestors = tuple(
        ancestor for ancestor in verified_cwd.parents if ancestor.name == ".beads"
    )
    if len(beads_ancestors) != 1:
        raise RuntimeProbeError(
            "Dolt command cwd must have exactly one .beads ancestor"
        )

    private_root, root_descriptor, root_metadata = _open_directory_fd(
        beads_ancestors[0].parent,
        kind="isolated clone temporary root",
    )
    if (
        root_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        os.close(root_descriptor)
        raise RuntimeProbeError(
            "isolated clone temporary root is not owned with exclusive permissions"
        )

    directory_flags = (
        os.O_RDONLY
        | os.O_CLOEXEC
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
    )
    paths: dict[str, str] = {}
    try:
        for variable, basename in (
            ("HOME", ".dolt-home"),
            ("XDG_CONFIG_HOME", ".dolt-xdg-config"),
            ("TMPDIR", ".dolt-tmp"),
        ):
            created = False
            try:
                os.mkdir(basename, 0o700, dir_fd=root_descriptor)
                created = True
            except FileExistsError:
                pass
            except OSError as exc:
                raise RuntimeProbeError(
                    f"cannot create private Dolt {variable} directory"
                ) from exc
            try:
                descriptor = os.open(
                    basename,
                    directory_flags,
                    dir_fd=root_descriptor,
                )
            except OSError as exc:
                raise RuntimeProbeError(
                    f"cannot open private Dolt {variable} directory without following links"
                ) from exc
            try:
                if created:
                    os.fchmod(descriptor, 0o700)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                ):
                    raise RuntimeProbeError(
                        f"private Dolt {variable} directory is not exclusively owned"
                    )
            finally:
                os.close(descriptor)
            paths[variable] = str(private_root / basename)
    finally:
        os.close(root_descriptor)
    return paths


def _is_installed_dolt_observation(argv: tuple[str, ...]) -> bool:
    if len(argv) < 2 or argv[1] not in ("sql", "version"):
        return False
    executable = Path(argv[0])
    if not executable.is_absolute():
        raise RuntimeProbeError("Dolt observation executable is not absolute")
    requested = shutil.which("dolt")
    if requested is None:
        raise RuntimeProbeError("required binary is not installed: dolt")
    try:
        installed = Path(requested).resolve(strict=True)
    except OSError as exc:
        raise RuntimeProbeError("cannot resolve installed Dolt executable") from exc
    if executable != installed:
        raise RuntimeProbeError(
            "sql/version observation executable is not the installed Dolt binary"
        )
    return True


def _command_binary_identity_from_descriptor(
    descriptor: int,
    before: os.stat_result,
    *,
    path: Path,
) -> tuple[dict[str, Any], os.stat_result]:
    payload, after = _read_descriptor(descriptor, before, path=path)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise RuntimeProbeError(
            f"cannot rewind retained command executable: {path}"
        ) from exc
    return (
        {
            "device": after.st_dev,
            "inode": after.st_ino,
            "mode": f"{stat.S_IMODE(after.st_mode):04o}",
            "mtime_ns": after.st_mtime_ns,
            "path": str(path),
            "sha256": _sha256(payload),
            "size": len(payload),
        },
        after,
    )


def _cwd_identity_from_metadata(path: Path, metadata: os.stat_result) -> dict[str, Any]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "path": str(path),
    }


def _child_binary_descriptor_identity(metadata: os.stat_result) -> dict[str, Any]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "mtime_ns": metadata.st_mtime_ns,
        "size": metadata.st_size,
    }


def _child_cwd_descriptor_identity(metadata: os.stat_result) -> dict[str, Any]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def _descriptor_executable_path(descriptor: int) -> str:
    if platform.system() == "Linux":
        return f"/proc/self/fd/{descriptor}"
    raise RuntimeProbeError(
        "descriptor-bound executable launch is unavailable"
    )


def _read_child_descriptor_evidence(descriptor: int) -> dict[str, Any]:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeProbeError(
            "native child did not return descriptor-bound execution evidence"
        ) from exc
    if not isinstance(evidence, dict):
        raise RuntimeProbeError("native child descriptor evidence is not an object")
    return evidence


def _default_runner(argv: tuple[str, ...], cwd: Path) -> CommandResult:
    is_dolt_observation = _is_installed_dolt_observation(argv)
    if is_dolt_observation:
        environment = {
            **_private_dolt_environment_paths(cwd),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "PAGER": "cat",
            "PATH": os.defpath,
        }
    else:
        environment = dict(os.environ)
        environment.update({"LC_ALL": "C", "NO_COLOR": "1", "PAGER": "cat"})

    binary_path, binary_descriptor, binary_before = _open_regular_fd(
        Path(argv[0]),
        kind="command executable",
    )
    cwd_path, cwd_descriptor, cwd_before = _open_directory_fd(
        cwd,
        kind="command cwd",
    )
    evidence_read_descriptor, evidence_write_descriptor = os.pipe()
    try:
        used_binary_identity, binary_before = _command_binary_identity_from_descriptor(
            binary_descriptor,
            binary_before,
            path=binary_path,
        )
        used_cwd_identity = _cwd_identity_from_metadata(cwd_path, cwd_before)
        expected_child_evidence = {
            "binary_identity": _child_binary_descriptor_identity(binary_before),
            "cwd_identity": _child_cwd_descriptor_identity(cwd_before),
        }

        def bind_retained_descriptors() -> None:
            child_binary = os.fstat(binary_descriptor)
            child_cwd = os.fstat(cwd_descriptor)
            os.fchdir(cwd_descriptor)
            bound_cwd = os.stat(".", follow_symlinks=False)
            if _child_cwd_descriptor_identity(
                bound_cwd
            ) != _child_cwd_descriptor_identity(child_cwd):
                raise RuntimeProbeError(
                    "native child cwd does not match the retained cwd descriptor"
                )
            child_evidence = _canonical_bytes(
                {
                    "binary_identity": _child_binary_descriptor_identity(
                        child_binary
                    ),
                    "cwd_identity": _child_cwd_descriptor_identity(child_cwd),
                }
            )
            view = memoryview(child_evidence)
            while view:
                written = os.write(evidence_write_descriptor, view)
                if written <= 0:
                    raise RuntimeProbeError(
                        "native child descriptor evidence write was short"
                    )
                view = view[written:]
            os.close(evidence_write_descriptor)
            try:
                os.close(0)
            except OSError:
                pass

        try:
            completed = subprocess.run(
                argv,
                executable=_descriptor_executable_path(binary_descriptor),
                cwd=None,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
                pass_fds=(
                    binary_descriptor,
                    cwd_descriptor,
                    evidence_write_descriptor,
                ),
                preexec_fn=bind_retained_descriptors,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeProbeError(
                f"read-only observation command failed to execute: {argv!r}"
            ) from exc
        finally:
            os.close(evidence_write_descriptor)
        child_evidence = _read_child_descriptor_evidence(evidence_read_descriptor)
        if child_evidence != expected_child_evidence:
            raise RuntimeProbeError(
                "native child used identities differ from retained descriptor identities"
            )
        if not isinstance(completed.stdout, bytes) or not isinstance(
            completed.stderr,
            bytes,
        ):
            raise RuntimeProbeError("native command outputs must be exact bytes")
        return CommandResult(
            argv=argv,
            cwd=cwd,
            binary_path=argv[0],
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            execution_mode="native_descriptor_bound",
            used_binary_identity=used_binary_identity,
            used_cwd_identity=used_cwd_identity,
        )
    finally:
        try:
            os.close(evidence_write_descriptor)
        except OSError:
            pass
        os.close(evidence_read_descriptor)
        os.close(cwd_descriptor)
        os.close(binary_descriptor)


def _bind_binary_inventory(
    inventory: Mapping[str, Any],
    command_record: Mapping[str, Any],
) -> None:
    command_identity = command_record["binary_identity"]
    inventory_to_command_fields = {
        "device": "device",
        "inode": "inode",
        "mode": "mode",
        "mtime_ns": "mtime_ns",
        "resolved_path": "path",
        "sha256": "sha256",
        "size": "size",
    }
    compared = 0
    for inventory_field, command_field in inventory_to_command_fields.items():
        if inventory_field not in inventory:
            continue
        compared += 1
        if inventory[inventory_field] != command_identity.get(command_field):
            raise RuntimeProbeError(
                "binary inventory does not match the executable used by the command"
            )
    if compared < 2 or "resolved_path" not in inventory or "sha256" not in inventory:
        raise RuntimeProbeError("binary inventory is too weak to bind the executed command")


def _command_record(
    result: CommandResult,
    *,
    retain_stdout: bool,
    binary_identity: dict[str, Any],
    cwd_identity: dict[str, Any],
    cwd_label: str | None,
) -> dict[str, Any]:
    evidence_cwd = cwd_label or str(result.cwd)
    evidence_cwd_identity = {**cwd_identity, "path": evidence_cwd}
    evidence_used_cwd_identity = (
        None
        if result.used_cwd_identity is None
        else {**result.used_cwd_identity, "path": evidence_cwd}
    )
    projection = {
        "argv": list(result.argv),
        "binary_identity": binary_identity,
        "binary_path": result.binary_path,
        "cwd": evidence_cwd,
        "cwd_identity": evidence_cwd_identity,
        "execution_mode": result.execution_mode,
        "exit_code": result.exit_code,
        "stderr_sha256": _sha256(result.stderr),
        "stderr_size": len(result.stderr),
        "stdout_sha256": _sha256(result.stdout),
        "stdout_size": len(result.stdout),
        "used_binary_identity": result.used_binary_identity,
        "used_cwd_identity": evidence_used_cwd_identity,
    }
    record: dict[str, Any] = {
        **projection,
        "_runtime_cwd": str(result.cwd),
        "_runtime_cwd_identity": cwd_identity,
        "command_sha256": _sha256(
            _canonical_bytes({"argv": list(result.argv), "cwd": evidence_cwd})
        ),
        "result_sha256": _sha256(_canonical_bytes(projection)),
    }
    if retain_stdout:
        record["stdout_base64"] = base64.b64encode(result.stdout).decode("ascii")
        record["stderr_base64"] = base64.b64encode(result.stderr).decode("ascii")
    return record


def _command_binary_identity(path: Path) -> dict[str, Any]:
    snapshot = _file_snapshot(path)
    return {
        "device": snapshot["device"],
        "inode": snapshot["inode"],
        "mode": snapshot["mode"],
        "mtime_ns": snapshot["mtime_ns"],
        "path": snapshot["path"],
        "sha256": snapshot["sha256"],
        "size": snapshot["size"],
    }


def _cwd_identity(path: Path) -> tuple[int, dict[str, Any]]:
    absolute, descriptor, metadata = _open_directory_fd(path, kind="command cwd")
    return descriptor, _cwd_identity_from_metadata(absolute, metadata)


def _revalidate_cwd(path: Path, expected: dict[str, Any]) -> None:
    descriptor, actual = _cwd_identity(path)
    os.close(descriptor)
    if actual != expected:
        raise RuntimeProbeError("command cwd identity drifted around execution")


def _run_checked(
    runner: CommandRunner,
    argv: Sequence[str],
    cwd: Path,
    records: list[dict[str, Any]],
    *,
    retain_stdout: bool = False,
    cwd_label: str | None = None,
) -> CommandResult:
    exact_argv = tuple(str(part) for part in argv)
    binary_before = _command_binary_identity(Path(exact_argv[0]))
    cwd_descriptor, cwd_before = _cwd_identity(cwd)
    try:
        _revalidate_cwd(cwd, cwd_before)
        result = runner(exact_argv, cwd)
        binary_after = _command_binary_identity(Path(exact_argv[0]))
        _revalidate_cwd(cwd, cwd_before)
    finally:
        os.close(cwd_descriptor)
    if binary_after != binary_before:
        raise RuntimeProbeError("command binary identity drifted around execution")
    if result.argv != exact_argv or result.cwd != cwd or result.binary_path != exact_argv[0]:
        raise RuntimeProbeError("command runner returned evidence for a different command")
    if not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes):
        raise RuntimeProbeError("command runner outputs must be exact bytes")
    native_runner = runner is _default_runner
    if native_runner:
        if (
            result.execution_mode != "native_descriptor_bound"
            or result.used_binary_identity != binary_before
            or result.used_cwd_identity != cwd_before
        ):
            raise RuntimeProbeError(
                "native runner did not prove the child-used descriptor identities"
            )
    elif (
        result.execution_mode != "injected_non_native_test_seam"
        or result.used_binary_identity is not None
        or result.used_cwd_identity is not None
    ):
        raise RuntimeProbeError(
            "injected command runner cannot claim native descriptor evidence"
        )
    records.append(
        _command_record(
            result,
            retain_stdout=retain_stdout,
            binary_identity=binary_before,
            cwd_identity=cwd_before,
            cwd_label=cwd_label,
        )
    )
    if result.exit_code != 0:
        raise RuntimeProbeError(f"read-only observation command exited nonzero: {exact_argv!r}")
    return result


def _revalidate_command_bindings(
    records: list[dict[str, Any]],
    *,
    cwd_bindings: bool = True,
) -> None:
    for record in records:
        binary = _command_binary_identity(Path(record["binary_path"]))
        if binary != record["binary_identity"]:
            raise RuntimeProbeError("command binary identity drifted before publication")
        if cwd_bindings:
            _revalidate_cwd(
                Path(record["_runtime_cwd"]),
                record["_runtime_cwd_identity"],
            )


def _public_command_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in record.items() if not key.startswith("_runtime_")}
        for record in records
    ]


def _decode_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeProbeError(f"{label} did not return valid JSON") from exc


def _rows(raw: bytes, label: str) -> list[dict[str, Any]]:
    value = _decode_json(raw, label)
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict) and isinstance(value.get("rows"), list):
        rows = value["rows"]
    elif value == {}:
        rows = []
    else:
        raise RuntimeProbeError(f"{label} did not return a JSON row set")
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeProbeError(f"{label} returned a non-object row")
    return rows


def _lower_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def select_runtime_adapter(
    context: Mapping[str, Any],
) -> Literal["embedded_dolt_cli", "sql_server"]:
    backend = context.get("backend")
    if not isinstance(backend, str) or backend != "dolt":
        raise RuntimeProbeError("bd context did not report the exact Dolt backend")
    database = context.get("database")
    if not isinstance(database, str) or not _IDENTIFIER.fullmatch(database):
        raise RuntimeProbeError("bd context database is missing or unsafe")
    if context.get("is_redirected") is not False:
        raise RuntimeProbeError("bd context must explicitly report is_redirected false")
    for path_field in ("repo_root", "beads_dir"):
        if not isinstance(context.get(path_field), str) or not context[path_field]:
            raise RuntimeProbeError(f"bd context {path_field} is missing")
    reported_modes = [
        context[field]
        for field in ("dolt_mode", "mode")
        if field in context
    ]
    if not reported_modes or any(not isinstance(value, str) for value in reported_modes):
        raise RuntimeProbeError("bd context did not report one string Dolt mode")
    normalized_modes = {value.lower() for value in reported_modes}
    if len(normalized_modes) != 1:
        raise RuntimeProbeError("bd context contains conflicting Dolt modes")
    mode = normalized_modes.pop()
    host = context.get("server_host")
    port = context.get("server_port")
    host_present = isinstance(host, str) and bool(host.strip())
    port_present = isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535
    any_endpoint_evidence = host not in (None, "") or port not in (None, 0, "")
    if mode in {"direct", "embedded", "embeddeddolt", "embedded_dolt_cli"}:
        if any_endpoint_evidence:
            raise RuntimeProbeError("direct mode conflicts with server endpoint evidence")
        return "embedded_dolt_cli"
    if mode in {"server", "sql_server"}:
        if not host_present or not port_present:
            raise RuntimeProbeError("server mode lacks one exact host/port endpoint")
        return "sql_server"
    raise RuntimeProbeError(f"unsupported or ambiguous Dolt mode: {reported_modes[0]!r}")


def _resolve_context_path(value: Any, *, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeProbeError(f"bd context {label} is missing")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return _nofollow_directory(path, kind=f"bd context {label}")


def resolve_embedded_repository(repo_root: Path, context: Mapping[str, Any]) -> Path:
    beads_dir = _resolve_context_path(context.get("beads_dir"), base=repo_root, label="beads_dir")
    database = context.get("database")
    if not isinstance(database, str) or not _IDENTIFIER.fullmatch(database):
        raise RuntimeProbeError("embedded database name is missing or unsafe")
    configured_data_dir = context.get("data_dir")
    expected_data_root_path = beads_dir / "embeddeddolt"
    expected_data_root = _nofollow_directory(
        expected_data_root_path,
        kind="rc4 embedded Dolt data root",
    )
    if configured_data_dir not in (None, ""):
        data_root_path = Path(str(configured_data_dir))
        if not data_root_path.is_absolute():
            data_root_path = beads_dir / data_root_path
        data_root = _nofollow_directory(
            data_root_path,
            kind="configured embedded Dolt data root",
        )
        if data_root != expected_data_root:
            raise RuntimeProbeError("embedded Dolt data directory is not the rc4-required actual root")
    else:
        data_root = expected_data_root
    repository = _nofollow_directory(
        data_root / database,
        kind="embedded Dolt repository",
    )
    if repository.parent != data_root:
        raise RuntimeProbeError("embedded Dolt repository resolution is ambiguous")
    dolt_metadata = _nofollow_directory(
        repository / ".dolt",
        kind="embedded Dolt metadata",
    )
    alternative_candidates = (
        beads_dir / "dolt",
        beads_dir / "dolt" / database,
        data_root,
    )
    conflicts = [
        path
        for path in alternative_candidates
        if path != repository and path.is_dir() and (path / ".dolt").is_dir()
    ]
    if conflicts:
        raise RuntimeProbeError("multiple embedded Dolt repository candidates exist")
    if dolt_metadata.parent != repository:
        raise RuntimeProbeError("embedded Dolt metadata identity is ambiguous")
    return repository


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _environment_evidence() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in _RELEVANT_ENVIRONMENT:
        if name in os.environ:
            raw = os.environ[name].encode("utf-8", "surrogateescape")
            values[name] = {"present": True, "sha256": _sha256(raw), "size": len(raw)}
        else:
            values[name] = {"present": False, "sha256": None, "size": 0}
    return values


def _platform_capabilities() -> dict[str, Any]:
    system = platform.system().lower()
    ps = shutil.which("ps")
    lsof = shutil.which("lsof")
    if system == "darwin":
        libproc_name = ctypes.util.find_library("proc")
        proc_pidinfo_present = False
        if libproc_name is not None:
            try:
                proc_pidinfo_present = hasattr(ctypes.CDLL(libproc_name), "proc_pidinfo")
            except OSError:
                proc_pidinfo_present = False
        process_birth = {
            "backend": "libproc_proc_pidinfo_plus_ps_lstart",
            "present": bool(ps and proc_pidinfo_present),
            "proc_pidinfo_present": proc_pidinfo_present,
            "ps_path": ps,
        }
        fd_scan = {
            "backend": "lsof",
            "lsof_path": lsof,
            "present": lsof is not None,
        }
    elif system == "linux":
        process_birth = {
            "backend": "proc_pid_stat_starttime_plus_ps_lstart",
            "present": Path("/proc/self/stat").is_file() and ps is not None,
            "proc_self_stat_present": Path("/proc/self/stat").is_file(),
            "ps_path": ps,
        }
        fd_scan = {
            "backend": "proc_pid_fd",
            "present": Path("/proc/self/fd").is_dir(),
            "proc_self_fd_present": Path("/proc/self/fd").is_dir(),
        }
    else:
        raise RuntimeProbeError(f"unsupported platform adapter: {system!r}")
    lock_present = all(
        (
            hasattr(fcntl, "flock"),
            hasattr(os, "O_NOFOLLOW"),
            hasattr(os, "O_CLOEXEC"),
        )
    )
    return {
        "architecture": platform.machine(),
        "fd_scan": {**fd_scan, "exercised": False},
        "lock": {
            "backend": "fcntl.flock",
            "continuous_stable_inode_capable": lock_present,
            "exercised": False,
            "present": lock_present,
        },
        "os": system,
        "os_release": platform.release(),
        "process_birth_identity": {**process_birth, "exercised": False},
        "python_version": platform.python_version(),
        "result": "capability_presence_only",
    }


def _parse_context(raw: bytes, repo_root: Path) -> dict[str, Any]:
    value = _decode_json(raw, "bd context --json")
    if not isinstance(value, dict) or "error" in value:
        raise RuntimeProbeError("bd context did not return one usable context object")
    if value.get("is_redirected") is not False:
        raise RuntimeProbeError("bd context must explicitly report is_redirected false")
    context_repo = _resolve_context_path(value.get("repo_root"), base=repo_root, label="repo_root")
    if context_repo != repo_root:
        raise RuntimeProbeError("bd context resolved a different repository root")
    return value


def _query_rows(
    *,
    adapter: str,
    query: str,
    bd_path: str,
    dolt_path: str | None,
    cwd: Path,
    runner: CommandRunner,
    records: list[dict[str, Any]],
    cwd_label: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized = query.lstrip().upper()
    if not normalized.startswith("SELECT"):
        raise RuntimeProbeError("runtime probe attempted a non-SELECT SQL statement")
    if adapter == "embedded_dolt_cli":
        if dolt_path is None:
            raise RuntimeProbeError("embedded adapter has no Dolt CLI")
        argv = (dolt_path, "sql", "--result-format", "json", "--query", query)
    elif adapter == "sql_server":
        raise RuntimeProbeError(
            "sql_server preflight fails closed: this probe cannot independently bind "
            "the configured endpoint, connected database, and socket identity"
        )
    else:
        raise RuntimeProbeError("unknown runtime adapter")
    result = _run_checked(
        runner,
        argv,
        cwd,
        records,
        cwd_label=cwd_label,
    )
    rows = _rows(result.stdout, query)
    return rows, records[-1]


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise RuntimeProbeError(f"unsafe logical table identifier: {identifier!r}")
    return "`" + identifier + "`"


def _canonical_row_hash(
    *,
    tables: list[str],
    adapter: str,
    bd_path: str,
    dolt_path: str | None,
    cwd: Path,
    runner: CommandRunner,
    records: list[dict[str, Any]],
    cwd_label: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    table_projections: list[dict[str, Any]] = []
    table_receipts: list[dict[str, Any]] = []
    for table in tables:
        query = f"SELECT * FROM {_quote_identifier(table)}"
        rows, command = _query_rows(
            adapter=adapter,
            query=query,
            bd_path=bd_path,
            dolt_path=dolt_path,
            cwd=cwd,
            runner=runner,
            records=records,
            cwd_label=cwd_label,
        )
        canonical_rows = sorted((_canonical_bytes(row) for row in rows))
        projection = {
            "row_count": len(rows),
            "rows_sha256": _sha256(b"".join(canonical_rows)),
            "table": table,
        }
        table_projections.append(projection)
        table_receipts.append(
            {
                **projection,
                "command_result_sha256": command["result_sha256"],
            }
        )
    return _sha256(_canonical_bytes(table_projections)), table_receipts


def _paths_disjoint(left: Path, right: Path) -> bool:
    return not _is_within(left, right) and not _is_within(right, left)


def _publication_output(
    *,
    publication_root: Path,
    output_report: Path,
    repo_root: Path,
    execution_root: Path,
) -> tuple[Path, str, dict[str, Any]]:
    root, descriptor, metadata = _open_directory_fd(
        publication_root,
        kind="runtime preflight publication root",
    )
    try:
        expected_root = _absolute_path(
            execution_root.parent / "runtime_preflight_observations",
            kind="expected runtime preflight publication root",
        )
        if root != expected_root:
            raise RuntimeProbeError(
                "publication root must be the dedicated runtime_preflight_observations "
                "sibling of the execution root"
            )
        for forbidden in (repo_root, execution_root):
            if not _paths_disjoint(root, forbidden):
                raise RuntimeProbeError(
                    "publication root overlaps a repo, execution, authority, or store root"
                )
        if output_report.parent == Path("."):
            output_name = output_report.name
        else:
            requested = _absolute_path(output_report, kind="output report")
            if requested.parent != root:
                raise RuntimeProbeError(
                    "output report must be one basename directly below publication root"
                )
            output_name = requested.name
        if output_name in ("", ".", "..") or Path(output_name).name != output_name:
            raise RuntimeProbeError("output report must be one safe new basename")
        try:
            os.stat(output_name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeProbeError("cannot prove output report absence") from exc
        else:
            raise RuntimeProbeError("output report must not already exist")
        identity = {
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
            "path": str(root),
        }
        return root, output_name, identity
    finally:
        os.close(descriptor)


def _write_immutable_report(
    publication_root: Path,
    output_name: str,
    report: dict[str, Any],
    expected_root_identity: dict[str, Any],
) -> None:
    root, parent_descriptor, parent_metadata = _open_directory_fd(
        publication_root,
        kind="runtime preflight publication root",
    )
    actual_root_identity = {
        "device": parent_metadata.st_dev,
        "inode": parent_metadata.st_ino,
        "mode": f"{stat.S_IMODE(parent_metadata.st_mode):04o}",
        "path": str(root),
    }
    if actual_root_identity != expected_root_identity:
        os.close(parent_descriptor)
        raise RuntimeProbeError("publication root identity drifted before creation")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    payload = _canonical_bytes(report)
    try:
        descriptor = os.open(
            output_name,
            flags,
            0o400,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        os.close(parent_descriptor)
        raise RuntimeProbeError(
            f"cannot create immutable output report in {publication_root}"
        ) from exc
    completed = False
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeProbeError("short write while publishing runtime report")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        os.fsync(parent_descriptor)
        completed = True
    finally:
        os.close(descriptor)
        if not completed:
            try:
                os.unlink(output_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)


def probe_runtime(
    repo_root: Path,
    execution_root: Path,
    publication_root: Path,
    output_report: Path,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    repo = _nofollow_directory(repo_root, kind="repo root")
    execution = _nofollow_directory(execution_root, kind="execution root")
    publication, output_name, publication_identity = _publication_output(
        publication_root=publication_root,
        output_report=output_report,
        repo_root=repo,
        execution_root=execution,
    )

    candidate, _, candidate_binding = _verify_candidate(execution)
    default_beads_dir = _nofollow_directory(repo / ".beads", kind="default Beads store")
    for forbidden in (candidate, default_beads_dir):
        if not _paths_disjoint(publication, forbidden):
            raise RuntimeProbeError("publication root overlaps immutable or store content")

    lineage_before = _verify_frozen_lineage(execution)
    candidate_before = _tree_snapshot(candidate)
    live_store_pre_context = _tree_snapshot(default_beads_dir)
    platform_evidence = _platform_capabilities()
    environment_evidence = _environment_evidence()
    bd_binary = _binary_identity("bd", required=True)
    assert bd_binary is not None
    command_records: list[dict[str, Any]] = []
    command_runner = runner or _default_runner
    bd_path = bd_binary["resolved_path"]

    bd_version_result = _run_checked(
        command_runner,
        (bd_path, "--version"),
        repo,
        command_records,
        retain_stdout=True,
    )
    _bind_binary_inventory(bd_binary, command_records[-1])
    context_result = _run_checked(
        command_runner,
        (bd_path, "context", "--json"),
        repo,
        command_records,
        retain_stdout=True,
    )
    context = _parse_context(context_result.stdout, repo)
    context_bd_version = context.get("bd_version")
    try:
        version_text = bd_version_result.stdout.decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeProbeError("bd --version output is not UTF-8") from exc
    if not isinstance(context_bd_version, str) or not context_bd_version:
        raise RuntimeProbeError("bd context did not bind the installed bd version")
    version_match = re.fullmatch(
        r"bd version ([0-9A-Za-z][0-9A-Za-z.+_-]*)(?:\s+.*)?",
        version_text,
    )
    if version_match is None or version_match.group(1) != context_bd_version:
        raise RuntimeProbeError("bd version and context version evidence conflict")

    adapter = select_runtime_adapter(context)
    beads_dir = _resolve_context_path(context.get("beads_dir"), base=repo, label="beads_dir")
    if beads_dir != default_beads_dir:
        raise RuntimeProbeError("bd context Beads directory differs from the default store")
    if _is_within(publication / output_name, beads_dir):
        raise RuntimeProbeError("output report cannot be placed in the actual Beads store")
    if adapter == "sql_server":
        raise RuntimeProbeError(
            "sql_server is non-conformant for this preflight because endpoint, "
            "connected database, socket/DSN, and descriptor identity are not independently proven"
        )
    server_environment = (
        "BEADS_DOLT_PASSWORD",
        "BEADS_DOLT_SERVER_DATABASE",
        "BEADS_DOLT_SERVER_HOST",
        "BEADS_DOLT_SERVER_PORT",
        "BEADS_DOLT_SERVER_TLS",
        "BEADS_DOLT_SERVER_USER",
    )
    if any(name in os.environ for name in server_environment):
        raise RuntimeProbeError("direct mode conflicts with endpoint-affecting server environment")
    if "BEADS_DIR" in os.environ:
        environment_beads_dir = _nofollow_directory(
            Path(os.environ["BEADS_DIR"]),
            kind="BEADS_DIR",
        )
        if environment_beads_dir != beads_dir:
            raise RuntimeProbeError("BEADS_DIR disagrees with bd context")
    if "BEADS_DOLT_DATA_DIR" in os.environ:
        environment_data_dir = Path(os.environ["BEADS_DOLT_DATA_DIR"])
        if not environment_data_dir.is_absolute():
            environment_data_dir = beads_dir / environment_data_dir
        environment_data_dir = _nofollow_directory(
            environment_data_dir,
            kind="BEADS_DOLT_DATA_DIR",
        )
        context_data_dir = context.get("data_dir")
        if context_data_dir in (None, ""):
            raise RuntimeProbeError("BEADS_DOLT_DATA_DIR is absent from bd context")
        configured_data_dir = Path(str(context_data_dir))
        if not configured_data_dir.is_absolute():
            configured_data_dir = beads_dir / configured_data_dir
        configured_data_dir = _nofollow_directory(
            configured_data_dir,
            kind="bd context data_dir",
        )
        if configured_data_dir != environment_data_dir:
            raise RuntimeProbeError("BEADS_DOLT_DATA_DIR disagrees with bd context")

    repository = resolve_embedded_repository(repo, context)
    try:
        repository_relative = repository.relative_to(beads_dir)
    except ValueError as exc:
        raise RuntimeProbeError("embedded Dolt repository is outside the live Beads store") from exc
    if not repository_relative.parts:
        raise RuntimeProbeError("embedded Dolt repository cannot equal the Beads store root")
    dolt_binary = _binary_identity("dolt", required=True)
    assert dolt_binary is not None
    dolt_path = dolt_binary["resolved_path"]
    server_endpoint: str | None = None

    live_store_before = _tree_snapshot(beads_dir)
    if live_store_before != live_store_pre_context:
        raise RuntimeProbeError(
            "live Beads/Dolt store drifted during bd version/context discovery"
        )
    live_store_content_at_clone: dict[str, Any]
    clone_before_content: dict[str, Any]
    clone_after_content: dict[str, Any]
    isolated_query_cwd = (
        "isolated-store://.beads/" + repository_relative.as_posix()
    )
    with _retained_temporary_directory(
        prefix="bb-rc4-runtime-clone-",
    ) as retained_temporary_root:
        temporary_root = retained_temporary_root.path
        for forbidden in (repo, execution, publication, candidate, beads_dir):
            if not _paths_disjoint(temporary_root, forbidden):
                raise RuntimeProbeError(
                    "isolated clone temporary root overlaps a live, execution, or publication root"
                )
        clone_beads_dir = _clone_tree(
            beads_dir,
            temporary_root / ".beads",
            destination_parent_descriptor=retained_temporary_root.leaf_descriptor,
        )
        _revalidate_retained_directory_entry(retained_temporary_root)
        live_store_clone_boundary = _tree_snapshot(beads_dir)
        if live_store_clone_boundary != live_store_before:
            raise RuntimeProbeError(
                "live Beads/Dolt store drifted while the isolated clone was created"
            )
        live_store_content_at_clone = _tree_snapshot(beads_dir, content_only=True)
        clone_before_content = _tree_snapshot(clone_beads_dir, content_only=True)
        if clone_before_content != live_store_content_at_clone:
            raise RuntimeProbeError("isolated clone does not exactly match source")
        clone_repository = _nofollow_directory(
            clone_beads_dir / repository_relative,
            kind="isolated embedded Dolt repository",
        )

        _run_checked(
            command_runner,
            (dolt_path, "version"),
            clone_repository,
            command_records,
            retain_stdout=True,
            cwd_label=isolated_query_cwd,
        )
        _bind_binary_inventory(dolt_binary, command_records[-1])
        summary_rows, summary_command = _query_rows(
            adapter=adapter,
            query=SUMMARY_QUERY,
            bd_path=bd_path,
            dolt_path=dolt_path,
            cwd=clone_repository,
            runner=command_runner,
            records=command_records,
            cwd_label=isolated_query_cwd,
        )
        if len(summary_rows) != 1:
            raise RuntimeProbeError("Dolt summary query did not return exactly one row")
        summary = _lower_row(summary_rows[0])
        required_summary = (
            "head_commit",
            "head_root",
            "staged_root",
            "working_root",
            "branch",
            "dolt_version",
        )
        if any(key not in summary or summary[key] in (None, "") for key in required_summary):
            raise RuntimeProbeError("Dolt summary query omitted a required full value")
        for key in ("head_commit", "head_root", "staged_root", "working_root"):
            value = summary[key]
            if not isinstance(value, str) or not _DOLT_HASH.fullmatch(value.lower()):
                raise RuntimeProbeError(f"Dolt {key} is missing, truncated, or malformed")
        branch = summary["branch"]
        if not isinstance(branch, str) or not _IDENTIFIER.fullmatch(branch):
            raise RuntimeProbeError("Dolt active branch is missing or unsafe")

        status_rows, status_command = _query_rows(
            adapter=adapter,
            query=STATUS_QUERY,
            bd_path=bd_path,
            dolt_path=dolt_path,
            cwd=clone_repository,
            runner=command_runner,
            records=command_records,
            cwd_label=isolated_query_cwd,
        )
        head_staged_clean = summary["head_root"] == summary["staged_root"]
        working_root_diverged = summary["working_root"] != summary["head_root"]
        if status_rows or not head_staged_clean:
            raise RuntimeProbeError(
                "Dolt repository is dirty: dolt_status is nonempty or HEAD/STAGED roots disagree"
            )

        tables_rows, tables_command = _query_rows(
            adapter=adapter,
            query=TABLES_QUERY,
            bd_path=bd_path,
            dolt_path=dolt_path,
            cwd=clone_repository,
            runner=command_runner,
            records=command_records,
            cwd_label=isolated_query_cwd,
        )
        tables: list[str] = []
        for row in tables_rows:
            table_name = _lower_row(row).get("table_name")
            if not isinstance(table_name, str) or not _IDENTIFIER.fullmatch(table_name):
                raise RuntimeProbeError("logical table discovery returned an unsafe name")
            tables.append(table_name)
        binary_sorted_tables = sorted(tables, key=lambda name: name.encode("utf-8"))
        if (
            not tables
            or len(tables) != len(set(tables))
            or tables != binary_sorted_tables
        ):
            raise RuntimeProbeError("logical table inventory is empty, duplicate, or noncanonical")

        schema_parts: dict[str, list[dict[str, Any]]] = {}
        schema_receipts: dict[str, str] = {}
        for name, query in (
            ("columns", SCHEMA_COLUMNS_QUERY),
            ("constraints", SCHEMA_CONSTRAINTS_QUERY),
            ("indexes", SCHEMA_INDEXES_QUERY),
        ):
            rows, command = _query_rows(
                adapter=adapter,
                query=query,
                bd_path=bd_path,
                dolt_path=dolt_path,
                cwd=clone_repository,
                runner=command_runner,
                records=command_records,
                cwd_label=isolated_query_cwd,
            )
            schema_parts[name] = rows
            schema_receipts[name] = command["result_sha256"]
        if not schema_parts["columns"]:
            raise RuntimeProbeError("logical schema column inventory is empty")
        schema_sha256 = _sha256(_canonical_bytes(schema_parts))
        canonical_rows_sha256, table_receipts = _canonical_row_hash(
            tables=tables,
            adapter=adapter,
            bd_path=bd_path,
            dolt_path=dolt_path,
            cwd=clone_repository,
            runner=command_runner,
            records=command_records,
            cwd_label=isolated_query_cwd,
        )

        clone_after_content = _tree_snapshot(clone_beads_dir, content_only=True)
        if clone_after_content != clone_before_content:
            raise RuntimeProbeError("isolated clone content drifted during Dolt observation")
        live_store_during_clone = _tree_snapshot(beads_dir)
        if live_store_during_clone != live_store_before:
            raise RuntimeProbeError(
                "live Beads/Dolt store drifted during isolated Dolt observation"
            )
        _revalidate_command_bindings(command_records)
        dolt_command_records = [
            record
            for record in command_records
            if record["binary_path"] == dolt_path
        ]
        if not dolt_command_records or any(
            record["cwd"] != isolated_query_cwd for record in dolt_command_records
        ):
            raise RuntimeProbeError("a Dolt command was not bound to the isolated repository")
        _revalidate_retained_directory_entry(retained_temporary_root)

    clone_disposed_before_publication = True
    live_store_after = _tree_snapshot(beads_dir)
    candidate_after = _tree_snapshot(candidate)
    lineage_after = _verify_frozen_lineage(execution)
    if live_store_after != live_store_before:
        raise RuntimeProbeError("live Beads/Dolt store drifted before publication")
    if candidate_after != candidate_before:
        raise RuntimeProbeError("immutable rc4 candidate drifted during discovery")
    if lineage_after != lineage_before:
        raise RuntimeProbeError("selector, rc3, or sealed-v1 lineage drifted during discovery")
    _revalidate_command_bindings(command_records, cwd_bindings=False)
    public_command_records = _public_command_records(command_records)

    discovery_projection = {
        "adapter_kind": adapter,
        "bd_context_result_sha256": command_records[1]["result_sha256"],
        "bd_binary_sha256": bd_binary["sha256"],
        "environment": environment_evidence,
        "isolated_query_cwd": isolated_query_cwd,
        "server_endpoint": server_endpoint,
    }
    filesystem_roots = [
        _path_identity("repo_root", repo),
        _path_identity("execution_root", execution),
        _path_identity("candidate_root", candidate),
        _path_identity("beads_store_root", beads_dir),
        _path_identity("dolt_repository", repository),
        {
            **publication_identity,
            "kind": "runtime_preflight_publication_root",
        },
    ]

    report: dict[str, Any] = {
        "adapter": {
            "adapter_kind": adapter,
            "bd_version": context_bd_version,
            "branch": branch,
            "database": context["database"],
            "discovery_evidence_sha256": _sha256(_canonical_bytes(discovery_projection)),
            "dolt_version": summary["dolt_version"],
            "isolated_query_repository": isolated_query_cwd,
            "mode": context.get("dolt_mode", context.get("mode")),
            "repository_path": str(repository),
            "selection_rationale": (
                "bd context reported one direct/embedded mode with no server or "
                "environment endpoint evidence; the live repository was resolved "
                "without a Dolt command and every Dolt command observed an isolated exact clone"
            ),
            "server_socket_or_dsn": server_endpoint,
            "store_root": str(beads_dir),
        },
        "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "authority": {
            "checkpoint_authority": False,
            "completion_authority": False,
            "cutover_authority": False,
            "migration_authority": False,
            "prior_rc3_authority_reused": False,
            "score_authority": False,
            "selector_authority": False,
            "spec_freeze_authority": False,
            "target_authority": False,
            "zero_authority": True,
        },
        "consumption_policy": {
            "consumable": False,
            "prohibited_downstream_receipt_roles": [
                "quiescence_acquisition_receipt",
                "migration_preparation_receipt",
                "migration_commit_receipt",
                "migration_replay_receipt",
                "release_intent_receipt",
                "post_release_receipt",
                "fresh_worker_handoff_receipt",
            ],
            "reason": "no spawn freeze, lease, journal, process inventory, descriptor scan, or quiescence",
        },
        "binary_inventory": {
            "bd": bd_binary,
            "dolt": dolt_binary,
        },
        "candidate": candidate_binding,
        "captured_at": _utc_now(),
        "commands": public_command_records,
        "environment": environment_evidence,
        "publication": {
            "output_basename": output_name,
            "output_path": str(publication / output_name),
            "publication_root": publication_identity,
            "root_disjoint_from_repo_and_execution": True,
        },
        "filesystem_roots": filesystem_roots,
        "immutable_observation": {
            "beads_store_before_sha256": live_store_before["sha256"],
            "beads_store_after_sha256": live_store_after["sha256"],
            "candidate_before_sha256": candidate_before["sha256"],
            "candidate_after_sha256": candidate_after["sha256"],
            "clone_after_content_sha256": clone_after_content["sha256"],
            "clone_before_content_sha256": clone_before_content["sha256"],
            "clone_content_drift": False,
            "clone_disposed_before_publication": clone_disposed_before_publication,
            "isolated_store_exact_snapshot": True,
            "lineage_before": lineage_before,
            "lineage_after": lineage_after,
            "live_store_after_sha256": live_store_after["sha256"],
            "live_store_before_sha256": live_store_before["sha256"],
            "live_store_pre_context_sha256": live_store_pre_context["sha256"],
            "live_store_content_at_clone_sha256": live_store_content_at_clone["sha256"],
            "live_store_drift": False,
            "no_live_dolt_command": True,
            "store_drift": False,
            "candidate_drift": False,
            "lineage_drift": False,
        },
        "isolated_store": {
            "clone_after_content_sha256": clone_after_content["sha256"],
            "clone_before_content_sha256": clone_before_content["sha256"],
            "disposed_before_publication": clone_disposed_before_publication,
            "exact_snapshot": True,
            "live_adapter_write_free_behavior_proved": False,
            "live_dolt_command_executed": False,
            "query_repository": isolated_query_cwd,
            "source_content_sha256": live_store_content_at_clone["sha256"],
        },
        "limitations": [
            "This receipt proves installed adapter discovery and isolated-clone preflight only; it is not a quiescence, lease, prepare, migration, cutover, rollback, release, or handoff receipt.",
            "Every Dolt version and SELECT command ran only in an isolated exact content clone; no Dolt command ran against the live Beads/Dolt store.",
            "The isolated result does not prove that the live direct Dolt adapter is write-free; native direct SELECT may write lock or journal metadata when run against a live store.",
            "The isolated result does not prove live-store quiescence, process absence, descriptor absence, or migration safety.",
            "No process was stopped, signalled, or quiesced; no process-birth or file-descriptor scan was executed.",
            "No advisory lock was acquired and no migration journal was opened or written by this probe.",
            "Read-only observation children were spawned and reaped by the probe, but this receipt does not claim a spawn freeze or prove that unrelated processes were absent.",
            "The live Beads/Dolt tree, immutable candidate, root selector, rc3 manifest, and sealed-v1 archive manifest were content- and full-identity-stable across discovery; session, target, score, and checkpoint stores outside those paths were not opened.",
            "Live-store equality includes every relative path, kind, device, inode, mode, mtime, and regular-file size/content SHA-256; no live mtime normalization is performed.",
            "Clone equality intentionally projects relative path, kind, mode, and regular-file size/content SHA-256 so clone-only inode and mtime churn is non-authoritative.",
            "SQL server mode fails closed because this preflight cannot independently bind its configured endpoint, connected database, socket/DSN, and descriptor identity without contacting a live server.",
            "Platform lock, process-birth, and FD-scan entries record primitive presence only and were not exercised.",
            "DOLT_HASHOF_DB('WORKING') may diverge from clean HEAD/STAGED roots on installed Dolt; the exact value and equality result are recorded, but WORKING-root equality is not used or claimed as a cleanliness or authority invariant.",
        ],
        "platform": platform_evidence,
        "program_id": PROGRAM_ID,
        "quiescence": {
            "descriptor_scan_executed": False,
            "flock_held": False,
            "journal_opened": False,
            "process_inventory_executed": False,
            "quiesced": False,
        },
        "result": "preflight_observation_only",
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.runtime_preflight_observation.v1",
        "scope_result": "non_consumable_for_quiescence_or_migration",
        "sealed_v1_archive_manifest_sha256": SEALED_V1_ARCHIVE_MANIFEST_SHA256,
        "snapshot": {
            "adapter_kind": adapter,
            "branch": branch,
            "canonical_rows_sha256": canonical_rows_sha256,
            "clean": True,
            "clean_invariant": "empty_dolt_status_and_head_root_equals_staged_root",
            "database": context["database"],
            "head_commit": summary["head_commit"],
            "head_root": summary["head_root"],
            "head_root_equals_staged_root": head_staged_clean,
            "observation_repository": isolated_query_cwd,
            "repository_path": str(repository),
            "schema_command_result_sha256s": schema_receipts,
            "schema_sha256": schema_sha256,
            "status_command_result_sha256": status_command["result_sha256"],
            "store_root": str(beads_dir),
            "staged_root": summary["staged_root"],
            "summary_command_result_sha256": summary_command["result_sha256"],
            "table_inventory_command_result_sha256": tables_command["result_sha256"],
            "table_receipts": table_receipts,
            "working_root": summary["working_root"],
            "working_root_diverged_from_head": working_root_diverged,
            "working_root_equality_claimed": False,
        },
        "superseded_rc3_manifest_sha256": SUPERSEDED_RC3_MANIFEST_SHA256,
        "target_execution_allowed": False,
    }
    report["receipt_sha256"] = _sha256(_canonical_bytes(report))
    _write_immutable_report(
        publication,
        output_name,
        report,
        publication_identity,
    )
    print(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a zero-authority rc4 installed-runtime discovery receipt."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--publication-root", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    probe_runtime(
        args.repo_root,
        args.execution_root,
        args.publication_root,
        args.output_report,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
