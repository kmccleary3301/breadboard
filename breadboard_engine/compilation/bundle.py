from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import stat
import struct
import tarfile
import threading
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

from breadboard.rl.state.cas import ArtifactStoreError, CASReader
from breadboard.rl.state.state_ref import ArtifactRef

from agentic_coder_prototype.compilation.contracts import (
    BundleEntry,
    BundleEntrypoint,
    BundleIntegrityError,
    BundleLimitError,
    BundleLimits,
    BundleProvenance,
    BundleSecurityError,
    BundleValidationError,
    ClosureMember,
    ConfigBundleManifest,
    DependencyClosureManifest,
    DependencyEdge,
    LogicalPathError,
    UndeclaredMemberError,
    bytes_sha256,
    canonical_sha256,
    normalize_logical_path,
)

_READ_CHUNK_BYTES = 64 * 1024
_ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".cpio",
    ".gz",
    ".rar",
    ".tar",
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tbz",
    ".tbz2",
    ".tgz",
    ".txz",
    ".xz",
    ".zip",
)
_ARCHIVE_MAGICS = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
)
_MEDIA_TYPES = {
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".jinja": "text/plain; charset=utf-8",
    ".jinja2": "text/plain; charset=utf-8",
    ".j2": "text/plain; charset=utf-8",
    ".toml": "application/toml",
}


class _WritableCAS(CASReader, Protocol):
    def put_bytes(
        self,
        data: bytes,
        *,
        artifact_id: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef: ...

    def has(self, artifact_ref: ArtifactRef | str) -> bool: ...


@dataclass(frozen=True)
class _StagedMember:
    logical_path: str
    payload: bytes
    media_type: str
    mode: int
    blob_digest: str
    artifact_id: str

    @property
    def size_bytes(self) -> int:
        return len(self.payload)

    def entry(self) -> BundleEntry:
        return BundleEntry(
            logical_path=self.logical_path,
            artifact_id=self.artifact_id,
            blob_digest=self.blob_digest,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
            mode=self.mode,
        )


@dataclass(frozen=True)
class _RegisteredName:
    path: str
    is_directory: bool


class _NameRegistry:
    def __init__(self, limits: BundleLimits) -> None:
        self._limits = limits
        self._by_path: dict[str, _RegisteredName] = {}
        self._by_folded_path: dict[str, str] = {}

    @property
    def count(self) -> int:
        return len(self._by_path)

    def add(self, raw_name: str, *, is_directory: bool) -> str:
        if not isinstance(raw_name, str):
            raise BundleSecurityError("member names must be Unicode strings")
        candidate = raw_name
        if is_directory and candidate.endswith("/"):
            candidate = candidate[:-1]
        try:
            path = normalize_logical_path(candidate)
        except LogicalPathError as exc:
            raise BundleSecurityError(str(exc)) from exc
        self._limits.validate_path(path)
        folded = path.casefold()
        if path in self._by_path:
            raise BundleSecurityError("duplicate normalized member name")
        if folded in self._by_folded_path:
            raise BundleSecurityError("case- or Unicode-colliding member names")
        self._by_path[path] = _RegisteredName(path=path, is_directory=is_directory)
        self._by_folded_path[folded] = path
        if len(self._by_path) > self._limits.max_members:
            raise BundleLimitError("archive node count limit exceeded")
        return path

    def validate_tree(self) -> None:
        by_folded = {
            path.casefold(): registered for path, registered in self._by_path.items()
        }
        for folded, registered in by_folded.items():
            components = folded.split("/")
            for index in range(1, len(components)):
                parent = by_folded.get("/".join(components[:index]))
                if parent is not None and not parent.is_directory:
                    raise BundleSecurityError("a member shadows an archive directory")
            if registered.is_directory:
                continue
            prefix = folded + "/"
            if any(other.startswith(prefix) for other in by_folded):
                raise BundleSecurityError("a file member shadows an archive directory")


def _media_type(path: str) -> str:
    lower = path.lower()
    for suffix in sorted(_MEDIA_TYPES, key=len, reverse=True):
        if lower.endswith(suffix):
            return _MEDIA_TYPES[suffix]
    return "application/octet-stream"


def _artifact_id(digest: str, size_bytes: int, media_type: str) -> str:
    identity = canonical_sha256(
        {"blob_digest": digest, "media_type": media_type, "size_bytes": size_bytes}
    )
    return "config-blob:" + identity.removeprefix("sha256:")


def _looks_like_archive(path: str, payload: bytes) -> bool:
    lower = path.lower()
    if any(lower.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        return True
    if any(payload.startswith(magic) for magic in _ARCHIVE_MAGICS):
        return True
    return len(payload) >= 262 and payload[257:262] == b"ustar"


def _stage_member(
    path: str,
    payload: bytes,
    *,
    limits: BundleLimits,
    media_type: str | None = None,
    mode: int = 0o444,
) -> _StagedMember:
    if len(payload) > limits.max_member_bytes:
        raise BundleLimitError("bundle member byte limit exceeded")
    if _looks_like_archive(path, payload):
        raise BundleSecurityError("nested archive member rejected")
    digest = bytes_sha256(payload)
    resolved_media_type = media_type or _media_type(path)
    return _StagedMember(
        logical_path=path,
        payload=payload,
        media_type=resolved_media_type,
        mode=mode,
        blob_digest=digest,
        artifact_id=_artifact_id(digest, len(payload), resolved_media_type),
    )


def _validate_staged(staged: Iterable[_StagedMember], limits: BundleLimits) -> tuple[_StagedMember, ...]:
    result = tuple(sorted(staged, key=lambda member: member.logical_path))
    if not result:
        raise BundleValidationError("bundle must contain at least one file member")
    if len(result) > limits.max_members:
        raise BundleLimitError("bundle member count limit exceeded")
    if sum(member.size_bytes for member in result) > limits.max_total_bytes:
        raise BundleLimitError("bundle total byte limit exceeded")
    return result


def _validate_entrypoints(
    entrypoints: Mapping[str, str],
    staged: tuple[_StagedMember, ...],
    limits: BundleLimits,
) -> tuple[BundleEntrypoint, ...]:
    if not isinstance(entrypoints, Mapping) or not entrypoints:
        raise BundleValidationError("entrypoints must be a non-empty mapping")
    paths = {member.logical_path for member in staged}
    result: list[BundleEntrypoint] = []
    for name, raw_path in entrypoints.items():
        if len(result) >= limits.max_members:
            raise BundleLimitError("bundle entrypoint count limit exceeded")
        if not isinstance(name, str) or not isinstance(raw_path, str):
            raise BundleValidationError("entrypoint names and paths must be strings")
        path = normalize_logical_path(raw_path)
        if path not in paths:
            raise BundleValidationError("entrypoint is not a staged bundle member")
        result.append(BundleEntrypoint(name=name, logical_path=path))
    return tuple(result)


def _verify_ref(
    cas: CASReader,
    *,
    artifact_id: str,
    digest: str,
    size_bytes: int,
    media_type: str,
    read_payload: bool,
) -> ArtifactRef:
    try:
        ref = cas.get_ref(artifact_id)
    except (KeyError, FileNotFoundError) as exc:
        raise BundleIntegrityError("declared CAS artifact record is missing") from exc
    except ArtifactStoreError as exc:
        raise BundleIntegrityError("declared CAS artifact record is invalid") from exc
    if (
        ref.artifact_id != artifact_id
        or ref.sha256 != digest
        or ref.size_bytes != size_bytes
        or ref.media_type != media_type
        or ref.metadata
    ):
        raise BundleIntegrityError(
            "CAS artifact record was rebound or does not match the manifest"
        )
    if read_payload:
        try:
            payload = cas.get_bytes(ref, max_bytes=size_bytes)
        except (KeyError, FileNotFoundError) as exc:
            raise BundleIntegrityError("declared CAS blob is missing") from exc
        except ArtifactStoreError as exc:
            raise BundleIntegrityError("declared CAS blob is invalid") from exc
        if (
            not isinstance(payload, bytes)
            or len(payload) != size_bytes
            or bytes_sha256(payload) != digest
        ):
            raise BundleIntegrityError(
                "CAS artifact bytes failed digest or size verification"
            )
    return ref


def _publish(
    staged: tuple[_StagedMember, ...],
    cas: _WritableCAS,
) -> None:
    missing: list[_StagedMember] = []
    for member in staged:
        try:
            present = cas.has(member.artifact_id)
        except (KeyError, FileNotFoundError):
            present = False
        except ArtifactStoreError as exc:
            raise BundleIntegrityError("CAS existence check failed") from exc
        if present:
            _verify_ref(
                cas,
                artifact_id=member.artifact_id,
                digest=member.blob_digest,
                size_bytes=member.size_bytes,
                media_type=member.media_type,
                read_payload=True,
            )
        else:
            missing.append(member)
    for member in missing:
        try:
            published = cas.put_bytes(
                member.payload,
                artifact_id=member.artifact_id,
                media_type=member.media_type,
            )
        except ArtifactStoreError as exc:
            raise BundleIntegrityError("CAS publication failed") from exc
        if (
            published.artifact_id != member.artifact_id
            or published.sha256 != member.blob_digest
            or published.size_bytes != member.size_bytes
            or published.media_type != member.media_type
            or published.metadata
        ):
            raise BundleIntegrityError("CAS publication returned a rebound record")
        _verify_ref(
            cas,
            artifact_id=member.artifact_id,
            digest=member.blob_digest,
            size_bytes=member.size_bytes,
            media_type=member.media_type,
            read_payload=True,
        )


def _manifest_from_staged(
    staged: tuple[_StagedMember, ...],
    *,
    cas: _WritableCAS,
    entrypoints: Mapping[str, str],
    limits: BundleLimits,
    provenance: BundleProvenance,
) -> ConfigBundleManifest:
    declared_entrypoints = _validate_entrypoints(entrypoints, staged, limits)
    manifest = ConfigBundleManifest(
        entries=tuple(member.entry() for member in staged),
        entrypoints=declared_entrypoints,
        provenance=provenance,
        limits=limits,
    )
    _publish(staged, cas)
    return manifest


def ingest_member_map(
    members: Mapping[str, bytes | bytearray | memoryview],
    cas: _WritableCAS,
    *,
    entrypoints: Mapping[str, str],
    limits: BundleLimits | None = None,
    source_label: str = "",
    media_types: Mapping[str, str] | None = None,
    modes: Mapping[str, int] | None = None,
) -> ConfigBundleManifest:
    """Validate a complete logical member map, then publish it atomically-by-validation."""

    resolved_limits = limits or BundleLimits()
    if not isinstance(members, Mapping):
        raise BundleValidationError("members must be a mapping")
    registry = _NameRegistry(resolved_limits)
    staged: list[_StagedMember] = []
    staged_bytes = 0
    for raw_path, raw_payload in members.items():
        if not isinstance(raw_path, str):
            raise BundleValidationError("member map keys must be strings")
        if not isinstance(raw_payload, (bytes, bytearray, memoryview)):
            raise BundleValidationError("member map values must be byte strings")
        path = registry.add(raw_path, is_directory=False)
        payload_size = (
            raw_payload.nbytes if isinstance(raw_payload, memoryview) else len(raw_payload)
        )
        if payload_size > resolved_limits.max_member_bytes:
            raise BundleLimitError("bundle member byte limit exceeded")
        if staged_bytes + payload_size > resolved_limits.max_total_bytes:
            raise BundleLimitError("bundle total byte limit exceeded")
        payload = raw_payload if isinstance(raw_payload, bytes) else bytes(raw_payload)
        media_type = media_types.get(raw_path) if media_types is not None else None
        mode = modes.get(raw_path, 0o444) if modes is not None else 0o444
        staged.append(
            _stage_member(
                path,
                payload,
                limits=resolved_limits,
                media_type=media_type,
                mode=mode,
            )
        )
        staged_bytes += payload_size
    registry.validate_tree()
    resolved = _validate_staged(staged, resolved_limits)
    raw_source_digest = canonical_sha256(
        [
            {"blob_digest": member.blob_digest, "logical_path": member.logical_path}
            for member in resolved
        ]
    )
    return _manifest_from_staged(
        resolved,
        cas=cas,
        entrypoints=entrypoints,
        limits=resolved_limits,
        provenance=BundleProvenance(
            source_kind="member_map",
            raw_source_digest=raw_source_digest,
            source_label=source_label,
        ),
    )


def _read_descriptor(fd: int, limit: int, *, error: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(_READ_CHUNK_BYTES, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise BundleLimitError(error)
    return b"".join(chunks)


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_ctime_ns,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _validate_regular_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise BundleSecurityError("special file source rejected")
    if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise BundleSecurityError("setuid or setgid file rejected")
    if metadata.st_nlink != 1:
        raise BundleSecurityError("hard-linked file rejected")


def _read_opened_file(
    fd: int,
    before: os.stat_result,
    limit: int,
) -> tuple[bytes, os.stat_result]:
    _validate_regular_file(before)
    opened = os.fstat(fd)
    _validate_regular_file(opened)
    if _stat_identity(before) != _stat_identity(opened):
        raise BundleSecurityError("file changed while staging")
    if opened.st_size > limit:
        raise BundleLimitError("source byte limit exceeded")
    payload = _read_descriptor(fd, limit, error="source byte limit exceeded")
    after = os.fstat(fd)
    _validate_regular_file(after)
    if (
        _stat_identity(after) != _stat_identity(opened)
        or len(payload) != opened.st_size
    ):
        raise BundleSecurityError("file changed while staging")
    return payload, opened


def _secure_read_file(
    path: str | os.PathLike[str], limit: int
) -> tuple[bytes, os.stat_result]:
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode):
        raise BundleSecurityError("symlink source rejected")
    _validate_regular_file(before)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BundleSecurityError("source file cannot be opened safely") from exc
    try:
        return _read_opened_file(fd, before, limit)
    finally:
        os.close(fd)


def _secure_read_file_at(
    parent_fd: int,
    name: str,
    before: os.stat_result,
    limit: int,
) -> tuple[bytes, os.stat_result]:
    _validate_regular_file(before)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise BundleSecurityError("bundle member cannot be opened safely") from exc
    try:
        return _read_opened_file(fd, before, limit)
    finally:
        os.close(fd)


def ingest_directory(
    source: str | os.PathLike[str],
    cas: _WritableCAS,
    *,
    entrypoints: Mapping[str, str],
    limits: BundleLimits | None = None,
    source_label: str = "",
) -> ConfigBundleManifest:
    """Stage a descriptor-pinned tree without following links, then publish it."""

    resolved_limits = limits or BundleLimits()
    root = os.fspath(source)
    root_stat = os.lstat(root)
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise BundleSecurityError("bundle directory root must be a real directory")
    if root_stat.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise BundleSecurityError("setuid or setgid directory rejected")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise BundleSecurityError("bundle directory cannot be opened safely") from exc
    registry = _NameRegistry(resolved_limits)
    staged: list[_StagedMember] = []
    staged_bytes = 0

    def scan(directory_fd: int, prefix: str) -> None:
        nonlocal staged_bytes
        directory_before = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_before.st_mode)
            or directory_before.st_mode & (stat.S_ISUID | stat.S_ISGID)
        ):
            raise BundleSecurityError("unsafe bundle directory rejected")
        remaining_nodes = resolved_limits.max_members - registry.count
        names: list[str] = []
        try:
            with os.scandir(directory_fd) as iterator:
                for directory_entry in iterator:
                    if len(names) >= remaining_nodes:
                        raise BundleLimitError("archive node count limit exceeded")
                    names.append(directory_entry.name)
        except BundleLimitError:
            raise
        except OSError as exc:
            raise BundleSecurityError("bundle directory cannot be enumerated") from exc
        names.sort()
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            try:
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise BundleSecurityError("bundle member changed while staging") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise BundleSecurityError("symlink member rejected")
            if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise BundleSecurityError("setuid or setgid member rejected")
            if stat.S_ISDIR(metadata.st_mode):
                path = registry.add(relative, is_directory=True)
                try:
                    child_fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise BundleSecurityError(
                        "bundle directory member cannot be opened safely"
                    ) from exc
                try:
                    opened = os.fstat(child_fd)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or _stat_identity(opened) != _stat_identity(metadata)
                    ):
                        raise BundleSecurityError("directory changed while staging")
                    scan(child_fd, path)
                    if _stat_identity(os.fstat(child_fd)) != _stat_identity(opened):
                        raise BundleSecurityError("directory changed while staging")
                finally:
                    os.close(child_fd)
                continue
            _validate_regular_file(metadata)
            path = registry.add(relative, is_directory=False)
            if metadata.st_size > resolved_limits.max_member_bytes:
                raise BundleLimitError("bundle member byte limit exceeded")
            remaining_bytes = resolved_limits.max_total_bytes - staged_bytes
            if metadata.st_size > remaining_bytes:
                raise BundleLimitError("bundle total byte limit exceeded")
            payload, opened = _secure_read_file_at(
                directory_fd,
                name,
                metadata,
                min(resolved_limits.max_member_bytes, remaining_bytes),
            )
            mode = 0o555 if opened.st_mode & 0o111 else 0o444
            staged.append(
                _stage_member(path, payload, limits=resolved_limits, mode=mode)
            )
            staged_bytes += len(payload)
        if _stat_identity(os.fstat(directory_fd)) != _stat_identity(directory_before):
            raise BundleSecurityError("directory changed while staging")

    try:
        opened_root = os.fstat(root_fd)
        if _stat_identity(opened_root) != _stat_identity(root_stat):
            raise BundleSecurityError("bundle directory root changed while staging")
        scan(root_fd, "")
        if _stat_identity(os.fstat(root_fd)) != _stat_identity(opened_root):
            raise BundleSecurityError("bundle directory root changed while staging")
    finally:
        os.close(root_fd)
    registry.validate_tree()
    resolved = _validate_staged(staged, resolved_limits)
    raw_source_digest = canonical_sha256(
        [
            {
                "blob_digest": member.blob_digest,
                "logical_path": member.logical_path,
                "mode": member.mode,
            }
            for member in resolved
        ]
    )
    return _manifest_from_staged(
        resolved,
        cas=cas,
        entrypoints=entrypoints,
        limits=resolved_limits,
        provenance=BundleProvenance(
            source_kind="directory",
            raw_source_digest=raw_source_digest,
            source_label=source_label,
        ),
    )


def _read_archive_source(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    limit: int,
) -> bytes:
    if isinstance(source, bytes):
        if len(source) > limit:
            raise BundleLimitError("archive byte limit exceeded")
        return source
    if isinstance(source, bytearray):
        if len(source) > limit:
            raise BundleLimitError("archive byte limit exceeded")
        return bytes(source)
    if isinstance(source, memoryview):
        if source.nbytes > limit:
            raise BundleLimitError("archive byte limit exceeded")
        return bytes(source)
    payload, _ = _secure_read_file(source, limit)
    return payload


def _read_bounded_stream(stream: BinaryIO, expected_size: int, limit: int) -> bytes:
    if expected_size < 0 or expected_size > limit:
        raise BundleLimitError("archive member byte limit exceeded")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(_READ_CHUNK_BYTES, limit + 1 - total))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise BundleSecurityError("archive returned a non-byte member stream")
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise BundleLimitError("archive member byte limit exceeded")
    if total != expected_size:
        raise BundleIntegrityError("archive member size disagrees with metadata")
    return b"".join(chunks)


def _preflight_zip(archive_bytes: bytes, limits: BundleLimits) -> None:
    minimum_eocd = 22
    search_start = max(0, len(archive_bytes) - (minimum_eocd + 65_535))
    eocd_offset = archive_bytes.rfind(b"PK\x05\x06", search_start)
    if eocd_offset < 0 or eocd_offset + minimum_eocd > len(archive_bytes):
        raise zipfile.BadZipFile("ZIP end-of-central-directory record is missing")
    (
        _,
        disk_number,
        central_disk,
        entries_on_disk,
        entry_count,
        central_size,
        central_offset,
        comment_size,
    ) = struct.unpack_from("<4s4H2LH", archive_bytes, eocd_offset)
    if eocd_offset + minimum_eocd + comment_size != len(archive_bytes):
        raise zipfile.BadZipFile("ZIP end record or comment is truncated")
    if disk_number != 0 or central_disk != 0 or entries_on_disk != entry_count:
        raise zipfile.BadZipFile("multi-disk ZIP archives are unsupported")
    central_end = eocd_offset
    if (
        entry_count == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        locator_offset = eocd_offset - 20
        if locator_offset < 0 or archive_bytes[locator_offset : locator_offset + 4] != b"PK\x06\x07":
            raise zipfile.BadZipFile("ZIP64 locator is missing")
        _, zip64_disk, zip64_offset, disk_count = struct.unpack_from(
            "<4sLQL", archive_bytes, locator_offset
        )
        if zip64_disk != 0 or disk_count != 1:
            raise zipfile.BadZipFile("multi-disk ZIP64 archives are unsupported")
        if zip64_offset + 56 > len(archive_bytes):
            raise zipfile.BadZipFile("ZIP64 end record is truncated")
        (
            signature,
            record_size,
            _,
            _,
            zip64_disk_number,
            zip64_central_disk,
            zip64_entries_on_disk,
            entry_count,
            central_size,
            central_offset,
        ) = struct.unpack_from("<4sQ2H2L4Q", archive_bytes, zip64_offset)
        if (
            signature != b"PK\x06\x06"
            or record_size < 44
            or zip64_disk_number != 0
            or zip64_central_disk != 0
            or zip64_entries_on_disk != entry_count
        ):
            raise zipfile.BadZipFile("invalid ZIP64 end record")
        central_end = zip64_offset
    if entry_count > limits.max_members:
        raise BundleLimitError("ZIP node count limit exceeded")
    if central_size > central_end or central_offset > len(archive_bytes):
        raise zipfile.BadZipFile("ZIP central directory is out of bounds")
    central_start = central_end - central_size
    if central_start < 0:
        raise zipfile.BadZipFile("ZIP central directory is out of bounds")
    position = central_start
    decoded_count = 0
    while position < central_end:
        if position + 4 > central_end:
            raise zipfile.BadZipFile("ZIP central directory is truncated")
        signature = archive_bytes[position : position + 4]
        if signature == b"PK\x01\x02":
            if position + 46 > central_end:
                raise zipfile.BadZipFile("ZIP central directory entry is truncated")
            name_size, extra_size, entry_comment_size = struct.unpack_from(
                "<3H", archive_bytes, position + 28
            )
            decoded_count += 1
            if decoded_count > limits.max_members:
                raise BundleLimitError("ZIP node count limit exceeded")
            if name_size > limits.max_path_bytes + 1:
                raise BundleLimitError("logical path byte limit exceeded")
            position += 46 + name_size + extra_size + entry_comment_size
        elif signature == b"PK\x05\x05":
            if position + 6 > central_end:
                raise zipfile.BadZipFile("ZIP central directory signature is truncated")
            signature_size = struct.unpack_from("<H", archive_bytes, position + 4)[0]
            position += 6 + signature_size
        else:
            raise zipfile.BadZipFile("invalid ZIP central directory entry")
        if position > central_end:
            raise zipfile.BadZipFile("ZIP central directory entry exceeds its bounds")
    if decoded_count != entry_count:
        raise zipfile.BadZipFile("ZIP central directory entry count mismatch")


def _zip_mode(info: zipfile.ZipInfo) -> int:
    raw_mode = (info.external_attr >> 16) & 0xFFFF
    if raw_mode:
        file_type = stat.S_IFMT(raw_mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise BundleSecurityError("ZIP link or special-file member rejected")
        if raw_mode & (stat.S_ISUID | stat.S_ISGID):
            raise BundleSecurityError("ZIP setuid or setgid member rejected")
    return 0o555 if raw_mode & 0o111 else 0o444


def ingest_zip(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    cas: _WritableCAS,
    *,
    entrypoints: Mapping[str, str],
    limits: BundleLimits | None = None,
    source_label: str = "",
) -> ConfigBundleManifest:
    """Stream bounded ZIP members into memory and publish only after full validation."""

    resolved_limits = limits or BundleLimits()
    archive_bytes = _read_archive_source(source, resolved_limits.max_archive_bytes)
    try:
        _preflight_zip(archive_bytes, resolved_limits)
    except BundleValidationError:
        raise
    except zipfile.BadZipFile as exc:
        raise BundleSecurityError("invalid or unsupported ZIP archive") from exc
    registry = _NameRegistry(resolved_limits)
    staged: list[_StagedMember] = []
    offsets: set[int] = set()
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), mode="r") as archive:
            infos = archive.infolist()
            if len(infos) > resolved_limits.max_members:
                raise BundleLimitError("ZIP node count limit exceeded")
            for info in infos:
                is_directory = info.is_dir()
                path = registry.add(info.filename, is_directory=is_directory)
                if info.flag_bits & 0x1:
                    raise BundleSecurityError("encrypted ZIP member rejected")
                if info.header_offset in offsets:
                    raise BundleSecurityError("overlapping ZIP member metadata rejected")
                offsets.add(info.header_offset)
                mode = _zip_mode(info)
                if is_directory:
                    if info.file_size != 0:
                        raise BundleSecurityError("ZIP directory has non-zero content size")
                    continue
                if info.file_size > resolved_limits.max_member_bytes:
                    raise BundleLimitError("ZIP member byte limit exceeded")
                ratio = info.file_size / max(1, info.compress_size)
                if ratio > resolved_limits.max_compression_ratio:
                    raise BundleLimitError("ZIP compression ratio limit exceeded")
                total_uncompressed += info.file_size
                if total_uncompressed > resolved_limits.max_total_bytes:
                    raise BundleLimitError("ZIP total byte limit exceeded")
                with archive.open(info, mode="r") as stream:
                    payload = _read_bounded_stream(
                        stream, info.file_size, resolved_limits.max_member_bytes
                    )
                staged.append(
                    _stage_member(path, payload, limits=resolved_limits, mode=mode)
                )
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise BundleSecurityError("invalid or unsupported ZIP archive") from exc
    registry.validate_tree()
    resolved = _validate_staged(staged, resolved_limits)
    return _manifest_from_staged(
        resolved,
        cas=cas,
        entrypoints=entrypoints,
        limits=resolved_limits,
        provenance=BundleProvenance(
            source_kind="zip",
            raw_source_digest=bytes_sha256(archive_bytes),
            source_label=source_label,
        ),
    )


class _BoundedTarReader:
    def __init__(self, source: BinaryIO, limit: int) -> None:
        self._source = source
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._limit - self.bytes_read
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        payload = self._source.read(requested)
        if not isinstance(payload, bytes):
            raise BundleSecurityError("TAR decompressor returned non-byte data")
        self.bytes_read += len(payload)
        if self.bytes_read > self._limit:
            raise BundleLimitError(
                "TAR decompressed byte or compression ratio limit exceeded"
            )
        return payload


def _read_exact(stream: _BoundedTarReader, size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < size:
        chunk = stream.read(size - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _tar_source_stream(archive_bytes: bytes) -> BinaryIO:
    compressed = io.BytesIO(archive_bytes)
    if archive_bytes.startswith(b"\x1f\x8b"):
        return gzip.GzipFile(fileobj=compressed, mode="rb")
    if archive_bytes.startswith(b"BZh"):
        return bz2.BZ2File(compressed, mode="rb")
    if archive_bytes.startswith(b"\xfd7zXZ\x00"):
        return lzma.LZMAFile(compressed, mode="rb")
    return compressed


def _preflight_tar(archive_bytes: bytes, limits: BundleLimits) -> bytes:
    ratio_limit = len(archive_bytes) * limits.max_compression_ratio
    structural_limit = (
        limits.max_total_bytes + limits.max_members * 1023 + tarfile.RECORDSIZE
    )
    stream = _BoundedTarReader(
        _tar_source_stream(archive_bytes), min(ratio_limit, structural_limit)
    )
    parts: list[bytes] = []
    payload_bytes = 0
    node_count = 0
    saw_end = False
    while True:
        header = _read_exact(stream, tarfile.BLOCKSIZE)
        if not header:
            break
        if len(header) != tarfile.BLOCKSIZE:
            raise BundleSecurityError("TAR header is truncated")
        parts.append(header)
        if header == tarfile.NUL * tarfile.BLOCKSIZE:
            saw_end = True
            while True:
                trailing = stream.read(_READ_CHUNK_BYTES)
                if not trailing:
                    break
                parts.append(trailing)
            break
        try:
            info = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        except tarfile.TarError as exc:
            raise BundleSecurityError("invalid TAR header") from exc
        node_count += 1
        if node_count > limits.max_members:
            raise BundleLimitError("TAR node count limit exceeded")
        if info.size < 0 or info.size > limits.max_member_bytes:
            raise BundleLimitError("TAR member byte limit exceeded")
        if payload_bytes + info.size > limits.max_total_bytes:
            raise BundleLimitError("TAR total byte limit exceeded")
        payload_bytes += info.size
        if info.isdir() and info.size != 0:
            raise BundleSecurityError("TAR directory has non-zero content size")
        if info.issym():
            raise BundleSecurityError("TAR symlink member rejected")
        if info.islnk():
            raise BundleSecurityError("TAR hardlink member rejected")
        metadata_types = {
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
        }
        if not info.isreg() and not info.isdir() and info.type not in metadata_types:
            raise BundleSecurityError("TAR special-file member rejected")
        padded_size = (info.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
        padded_size *= tarfile.BLOCKSIZE
        remaining = padded_size
        while remaining:
            chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise BundleSecurityError("TAR member payload is truncated")
            parts.append(chunk)
            remaining -= len(chunk)
    if not saw_end:
        raise BundleSecurityError("TAR end marker is missing")
    return b"".join(parts)


def _tar_mode(info: tarfile.TarInfo) -> int:
    if info.mode & (stat.S_ISUID | stat.S_ISGID):
        raise BundleSecurityError("TAR setuid or setgid member rejected")
    return 0o555 if info.mode & 0o111 else 0o444


def ingest_tar(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    cas: _WritableCAS,
    *,
    entrypoints: Mapping[str, str],
    limits: BundleLimits | None = None,
    source_label: str = "",
) -> ConfigBundleManifest:
    """Stream bounded TAR members into memory and publish after validation."""

    resolved_limits = limits or BundleLimits()
    archive_bytes = _read_archive_source(source, resolved_limits.max_archive_bytes)
    try:
        tar_bytes = _preflight_tar(archive_bytes, resolved_limits)
    except BundleValidationError:
        raise
    except (OSError, EOFError, lzma.LZMAError) as exc:
        raise BundleSecurityError("invalid or unsupported TAR archive") from exc
    registry = _NameRegistry(resolved_limits)
    staged: list[_StagedMember] = []
    total_uncompressed = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|") as archive:
            for info in archive:
                is_directory = info.isdir()
                path = registry.add(info.name, is_directory=is_directory)
                if info.issym():
                    raise BundleSecurityError("TAR symlink member rejected")
                if info.islnk():
                    raise BundleSecurityError("TAR hardlink member rejected")
                if is_directory:
                    if info.size != 0:
                        raise BundleSecurityError(
                            "TAR directory has non-zero content size"
                        )
                    _tar_mode(info)
                    continue
                if not info.isreg():
                    raise BundleSecurityError("TAR special-file member rejected")
                if getattr(info, "sparse", None):
                    raise BundleSecurityError("TAR sparse-file member rejected")
                if info.size > resolved_limits.max_member_bytes:
                    raise BundleLimitError("TAR member byte limit exceeded")
                total_uncompressed += info.size
                if total_uncompressed > resolved_limits.max_total_bytes:
                    raise BundleLimitError("TAR total byte limit exceeded")
                stream = archive.extractfile(info)
                if stream is None:
                    raise BundleIntegrityError("TAR regular member has no payload")
                with stream:
                    payload = _read_bounded_stream(
                        stream, info.size, resolved_limits.max_member_bytes
                    )
                staged.append(
                    _stage_member(
                        path,
                        payload,
                        limits=resolved_limits,
                        mode=_tar_mode(info),
                    )
                )
    except (tarfile.TarError, EOFError) as exc:
        raise BundleSecurityError("invalid or unsupported TAR archive") from exc
    registry.validate_tree()
    resolved = _validate_staged(staged, resolved_limits)
    return _manifest_from_staged(
        resolved,
        cas=cas,
        entrypoints=entrypoints,
        limits=resolved_limits,
        provenance=BundleProvenance(
            source_kind="tar",
            raw_source_digest=bytes_sha256(archive_bytes),
            source_label=source_label,
        ),
    )


def ingest_bundle(
    source: Mapping[str, bytes | bytearray | memoryview]
    | str
    | os.PathLike[str]
    | bytes
    | bytearray
    | memoryview,
    cas: _WritableCAS,
    *,
    entrypoints: Mapping[str, str],
    archive_format: str | None = None,
    limits: BundleLimits | None = None,
    source_label: str = "",
) -> ConfigBundleManifest:
    """Ingest a member map, directory, ZIP, or TAR through one bounded entrypoint."""

    if isinstance(source, Mapping):
        return ingest_member_map(
            source,
            cas,
            entrypoints=entrypoints,
            limits=limits,
            source_label=source_label,
        )
    if not isinstance(source, (bytes, bytearray, memoryview)) and os.path.isdir(source):
        if archive_format is not None:
            raise BundleValidationError("archive_format cannot be used with a directory")
        return ingest_directory(
            source,
            cas,
            entrypoints=entrypoints,
            limits=limits,
            source_label=source_label,
        )
    resolved_format = archive_format
    if resolved_format is None and not isinstance(source, (bytes, bytearray, memoryview)):
        lower = os.fspath(source).lower()
        resolved_format = "zip" if lower.endswith(".zip") else "tar"
    if resolved_format == "zip":
        return ingest_zip(
            source,
            cas,
            entrypoints=entrypoints,
            limits=limits,
            source_label=source_label,
        )
    if resolved_format == "tar":
        return ingest_tar(
            source,
            cas,
            entrypoints=entrypoints,
            limits=limits,
            source_label=source_label,
        )
    raise BundleValidationError("archive_format must be explicitly zip or tar for raw bytes")


def _bounded_values(
    values: Iterable[Any],
    limit: int,
    error: str,
) -> tuple[Any, ...]:
    result: list[Any] = []
    for value in values:
        if len(result) >= limit:
            raise BundleLimitError(error)
        result.append(value)
    return tuple(result)


def build_dependency_closure(
    bundle: ConfigBundleManifest,
    *,
    root_entrypoint: str,
    member_paths: Iterable[str] | None = None,
    edges: Iterable[DependencyEdge] = (),
    external_members: Iterable[ClosureMember] = (),
    provenance: Iterable[str] = (),
) -> DependencyClosureManifest:
    """Build and validate an exact reachable closure for a declared entrypoint."""

    if not isinstance(root_entrypoint, str):
        raise BundleValidationError("root_entrypoint must be an entrypoint name")
    entrypoints = {
        entrypoint.name: entrypoint.logical_path for entrypoint in bundle.entrypoints
    }
    root_path = entrypoints.get(root_entrypoint)
    if root_path is None:
        raise BundleValidationError(
            "root_entrypoint must name a declared bundle entrypoint"
        )
    edge_values = _bounded_values(
        edges,
        bundle.limits.max_dependency_edges,
        "dependency edge count limit exceeded",
    )
    external_values = _bounded_values(
        external_members,
        bundle.limits.max_members,
        "closure member count limit exceeded",
    )
    provenance_values = _bounded_values(
        provenance,
        bundle.limits.max_dependency_edges,
        "closure provenance count limit exceeded",
    )
    if any(not isinstance(edge, DependencyEdge) for edge in edge_values):
        raise BundleValidationError("edges must contain DependencyEdge values")
    if any(not isinstance(member, ClosureMember) for member in external_values):
        raise BundleValidationError(
            "external_members must contain ClosureMember values"
        )
    by_path = {entry.logical_path: entry for entry in bundle.entries}
    if member_paths is None:
        selected_paths = {root_path}
        selected_paths.update(edge.from_path for edge in edge_values)
        selected_paths.update(edge.logical_path for edge in edge_values)
    else:
        path_values = _bounded_values(
            member_paths,
            bundle.limits.max_members,
            "closure member count limit exceeded",
        )
        selected_paths = {normalize_logical_path(path) for path in path_values}
        selected_paths.add(root_path)
    if len(selected_paths) + len(external_values) > bundle.limits.max_members:
        raise BundleLimitError("closure member count limit exceeded")
    unknown = selected_paths - set(by_path)
    external_paths = {member.logical_path for member in external_values}
    unknown -= external_paths
    if unknown:
        raise BundleValidationError(
            "closure names members absent from the bundle: "
            + ", ".join(sorted(unknown))
        )
    members = [
        ClosureMember.from_bundle_entry(by_path[path])
        for path in selected_paths
        if path in by_path
    ]
    for member in external_values:
        if member.source != "external":
            raise BundleValidationError(
                "external_members must declare source='external'"
            )
        if member.logical_path in by_path:
            raise BundleValidationError("external member shadows a bundle member")
        members.append(member)
    return DependencyClosureManifest(
        bundle_digest=bundle.bundle_digest,
        root_entrypoint=root_path,
        members=tuple(members),
        edges=edge_values,
        limits=bundle.limits,
        provenance=provenance_values,
    )


class ManifestReader:
    """Read-only, exact-membership view over a manifest/closure/CAS tuple."""

    __slots__ = ("_bytes_read", "_cas", "_max_total_bytes", "_members", "_read_lock", "_sorted_paths")

    def __init__(
        self,
        *,
        cas: CASReader,
        bundle: ConfigBundleManifest,
        closure: DependencyClosureManifest,
    ) -> None:
        if closure.bundle_digest != bundle.bundle_digest:
            raise BundleIntegrityError("closure is bound to a different bundle manifest")
        if closure.limits != bundle.limits:
            raise BundleIntegrityError("closure limits differ from bundle limits")
        bundle_entries = {entry.logical_path: entry for entry in bundle.entries}
        members: dict[str, ClosureMember] = {}
        for member in closure.members:
            if member.source == "bundle":
                entry = bundle_entries.get(member.logical_path)
                if entry is None:
                    raise BundleIntegrityError("closure names an undeclared bundle member")
                if (
                    member.artifact_id != entry.artifact_id
                    or member.blob_digest != entry.blob_digest
                    or member.size_bytes != entry.size_bytes
                    or member.media_type != entry.media_type
                ):
                    raise BundleIntegrityError("closure member differs from its bundle entry")
            elif member.logical_path in bundle_entries:
                raise BundleIntegrityError("external closure member shadows a bundle member")
            _verify_ref(
                cas,
                artifact_id=member.artifact_id,
                digest=member.blob_digest,
                size_bytes=member.size_bytes,
                media_type=member.media_type,
                read_payload=False,
            )
            members[member.logical_path] = member
        self._cas = cas
        self._members = members
        self._sorted_paths = tuple(sorted(members))
        self._bytes_read = 0
        self._max_total_bytes = closure.limits.max_total_bytes
        self._read_lock = threading.Lock()

    def read_bytes(self, logical_path: str) -> bytes:
        try:
            path = normalize_logical_path(logical_path)
        except LogicalPathError as exc:
            raise UndeclaredMemberError(str(exc)) from exc
        member = self._members.get(path)
        if member is None:
            raise UndeclaredMemberError(f"member is not declared by the closure: {path}")
        with self._read_lock:
            if self._bytes_read + member.size_bytes > self._max_total_bytes:
                raise BundleLimitError("ManifestReader aggregate byte limit exceeded")
            ref = _verify_ref(
                self._cas,
                artifact_id=member.artifact_id,
                digest=member.blob_digest,
                size_bytes=member.size_bytes,
                media_type=member.media_type,
                read_payload=False,
            )
            try:
                payload = self._cas.get_bytes(ref, max_bytes=member.size_bytes)
            except (KeyError, FileNotFoundError) as exc:
                raise BundleIntegrityError("declared CAS blob is missing") from exc
            except ArtifactStoreError as exc:
                raise BundleIntegrityError("declared CAS blob is invalid") from exc
            if (
                not isinstance(payload, bytes)
                or len(payload) != member.size_bytes
                or bytes_sha256(payload) != member.blob_digest
            ):
                raise BundleIntegrityError(
                    "CAS artifact bytes failed digest or size verification"
                )
            self._bytes_read += len(payload)
            return payload

    def members(
        self,
        logical_dir: str = "",
        *,
        suffixes: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        if not isinstance(logical_dir, str):
            raise UndeclaredMemberError("logical directory must be a string")
        if logical_dir:
            try:
                directory = normalize_logical_path(logical_dir)
            except LogicalPathError as exc:
                raise UndeclaredMemberError(str(exc)) from exc
            prefix = directory + "/"
        else:
            prefix = ""
        if not isinstance(suffixes, tuple) or any(
            not isinstance(suffix, str) or not suffix for suffix in suffixes
        ):
            raise BundleValidationError("suffixes must be a tuple of non-empty strings")
        return tuple(
            path
            for path in self._sorted_paths
            if path.startswith(prefix) and (not suffixes or path.endswith(suffixes))
        )


__all__ = [
    "ManifestReader",
    "build_dependency_closure",
    "ingest_bundle",
    "ingest_directory",
    "ingest_member_map",
    "ingest_tar",
    "ingest_zip",
]
