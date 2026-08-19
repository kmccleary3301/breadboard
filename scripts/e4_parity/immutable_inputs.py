"""Verification and safe provisioning for the immutable E4 input bundle."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, Mapping

try:
    from scripts.e4_parity.validators.hash_utils import copy_stream_sha256_hex, sha256_stream_hex
except (ImportError, ModuleNotFoundError):  # pragma: no cover - direct script execution
    from validators.hash_utils import copy_stream_sha256_hex, sha256_stream_hex

_FORMAT_VERSION = 1
_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ARCHIVE_MODE = stat.S_IFREG | 0o644
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CHUNK_SIZE = 1024 * 1024


class ImmutableInputError(ValueError):
    """The immutable input bundle or a provisioning destination is unsafe."""


@dataclass(frozen=True)
class ImmutableInputMember:
    path: str
    namespace: str
    destination: str
    bytes: int
    sha256: str
    classification: str


@dataclass(frozen=True)
class VerifiedImmutableInputs:
    archive_sha256: str
    archive_bytes: int
    members: tuple[ImmutableInputMember, ...]


@dataclass(frozen=True)
class ProvisionResult:
    archive_sha256: str
    member_count: int
    bytes: int
    written: tuple[str, ...]
    existing: tuple[str, ...]


def _sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    return sha256_stream_hex(stream)


def _relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ImmutableInputError(f"{label} must be a non-empty string")
    if "\x00" in value:
        raise ImmutableInputError(f"{label} contains NUL")
    if "\\" in value:
        raise ImmutableInputError(f"{label} contains a backslash")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise ImmutableInputError(f"{label} must be relative")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ImmutableInputError(f"{label} contains an empty, dot, or traversal segment")
    if path.as_posix() != value:
        raise ImmutableInputError(f"{label} is not canonical POSIX syntax")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ImmutableInputError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImmutableInputError(f"{label} must be a non-negative integer")
    return value


def _load_manifest(path: Path) -> tuple[str, str, int, tuple[ImmutableInputMember, ...]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImmutableInputError(f"cannot read immutable-input manifest: {path}") from exc
    if not isinstance(document, dict) or document.get("format_version") != _FORMAT_VERSION:
        raise ImmutableInputError(f"manifest format_version must be {_FORMAT_VERSION}")
    archive = document.get("archive")
    if not isinstance(archive, dict):
        raise ImmutableInputError("manifest archive must be an object")
    archive_file = archive.get("file")
    if not isinstance(archive_file, str) or Path(archive_file).name != archive_file or not archive_file:
        raise ImmutableInputError("manifest archive.file must be a bare file name")
    archive_sha256 = _sha256(archive.get("sha256"), "manifest archive.sha256")
    archive_bytes = _nonnegative_int(archive.get("bytes"), "manifest archive.bytes")

    rows = document.get("members")
    if not isinstance(rows, list) or not rows:
        raise ImmutableInputError("manifest members must be a non-empty array")
    members: list[ImmutableInputMember] = []
    paths: set[str] = set()
    destinations: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        label = f"manifest members[{index}]"
        if not isinstance(row, dict):
            raise ImmutableInputError(f"{label} must be an object")
        namespace = row.get("namespace")
        if namespace not in {"repo", "workspace"}:
            raise ImmutableInputError(f"{label}.namespace must be repo or workspace")
        destination = _relative_path(row.get("destination"), f"{label}.destination")
        path_value = _relative_path(row.get("path"), f"{label}.path")
        expected_path = f"{namespace}/{destination}"
        if path_value != expected_path:
            raise ImmutableInputError(f"{label}.path must equal {expected_path!r}")
        destination_key = (namespace, destination)
        if destination_key in destinations:
            raise ImmutableInputError(f"duplicate destination in manifest: {namespace}/{destination}")
        if path_value in paths:
            raise ImmutableInputError(f"duplicate member path in manifest: {path_value}")
        classification = row.get("classification")
        if not isinstance(classification, str) or not classification:
            raise ImmutableInputError(f"{label}.classification must be a non-empty string")
        members.append(
            ImmutableInputMember(
                path=path_value,
                namespace=namespace,
                destination=destination,
                bytes=_nonnegative_int(row.get("bytes"), f"{label}.bytes"),
                sha256=_sha256(row.get("sha256"), f"{label}.sha256"),
                classification=classification,
            )
        )
        destinations.add(destination_key)
        paths.add(path_value)
    ordered_paths = sorted(paths, key=lambda value: value.encode("utf-8"))
    if [member.path for member in members] != ordered_paths:
        raise ImmutableInputError("manifest members are not sorted by member path")
    return archive_file, archive_sha256, archive_bytes, tuple(members)


def _verify_zip(
    archive: zipfile.ZipFile,
    members: tuple[ImmutableInputMember, ...],
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ImmutableInputError("ZIP contains duplicate member paths")
    try:
        ordered_names = sorted(names, key=lambda value: value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise ImmutableInputError("ZIP member path is not strict UTF-8") from exc
    if names != ordered_names:
        raise ImmutableInputError("ZIP members are not sorted by member path")
    expected = {member.path: member for member in members}
    if set(names) != set(expected):
        missing = sorted(set(expected) - set(names))
        unexpected = sorted(set(names) - set(expected))
        raise ImmutableInputError(f"ZIP member set mismatch: missing={missing!r}, unexpected={unexpected!r}")

    by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        _relative_path(info.filename, f"ZIP member {info.filename!r}")
        if info.is_dir() or info.filename.endswith("/"):
            raise ImmutableInputError(f"ZIP directory entries are forbidden: {info.filename}")
        if not (info.filename.startswith("repo/") or info.filename.startswith("workspace/")):
            raise ImmutableInputError(f"ZIP member has an invalid namespace: {info.filename}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if info.create_system != 3 or mode != _ARCHIVE_MODE:
            raise ImmutableInputError(f"ZIP member is not a 0644 regular file: {info.filename}")
        if info.date_time != _ARCHIVE_TIMESTAMP:
            raise ImmutableInputError(f"ZIP member timestamp is not fixed: {info.filename}")
        member = expected[info.filename]
        if info.file_size != member.bytes:
            raise ImmutableInputError(f"ZIP member size mismatch: {info.filename}")
        try:
            with archive.open(info, "r") as source:
                digest, size = _sha256_stream(source)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise ImmutableInputError(f"cannot read ZIP member: {info.filename}") from exc
        if size != member.bytes:
            raise ImmutableInputError(f"ZIP member byte-count mismatch: {info.filename}")
        if digest != member.sha256:
            raise ImmutableInputError(f"ZIP member SHA-256 mismatch: {info.filename}")
        by_name[info.filename] = info
    return by_name


@contextmanager
def _verified_archive(
    archive_path: Path,
    manifest_path: Path,
) -> Iterator[tuple[VerifiedImmutableInputs, zipfile.ZipFile, dict[str, zipfile.ZipInfo]]]:
    archive_file, expected_sha256, expected_bytes, members = _load_manifest(manifest_path)
    if archive_path.name != archive_file:
        raise ImmutableInputError(
            f"archive file name mismatch: expected {archive_file!r}, got {archive_path.name!r}"
        )
    try:
        stream = archive_path.open("rb")
    except OSError as exc:
        raise ImmutableInputError(f"cannot open immutable-input archive: {archive_path}") from exc
    with stream:
        actual_sha256, actual_bytes = _sha256_stream(stream)
        if actual_bytes != expected_bytes:
            raise ImmutableInputError("archive byte-count mismatch")
        if actual_sha256 != expected_sha256:
            raise ImmutableInputError("archive SHA-256 mismatch")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream, "r") as archive:
                by_name = _verify_zip(archive, members)
                verified = VerifiedImmutableInputs(actual_sha256, actual_bytes, members)
                yield verified, archive, by_name
        except zipfile.BadZipFile as exc:
            raise ImmutableInputError("immutable-input archive is not a valid ZIP") from exc


def verify_immutable_inputs(archive_path: Path, manifest_path: Path) -> VerifiedImmutableInputs:
    """Verify the archive and every manifest-pinned member without writing files."""
    with _verified_archive(Path(archive_path), Path(manifest_path)) as (verified, _archive, _infos):
        return verified


def _resolved_root(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ImmutableInputError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ImmutableInputError(f"{label} must be a real directory: {path}")
    return resolved


def _destination(root: Path, root_resolved: Path, relative: str) -> Path:
    destination = root / Path(*PurePosixPath(relative).parts)
    try:
        resolved = destination.resolve(strict=False)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ImmutableInputError(f"destination escapes namespace root: {destination}") from exc
    return destination


def _existing_bytes(path: Path, expected_sha256: str, expected_bytes: int) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ImmutableInputError(f"cannot inspect provisioning destination: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ImmutableInputError(f"existing destination is not a regular file: {path}")
    try:
        with path.open("rb") as stream:
            digest, size = _sha256_stream(stream)
    except OSError as exc:
        raise ImmutableInputError(f"cannot read provisioning destination: {path}") from exc
    if size != expected_bytes or digest != expected_sha256:
        raise ImmutableInputError(f"conflicting existing bytes at destination: {path}")
    return True


def _atomic_extract(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    member: ImmutableInputMember,
    destination: Path,
    root_resolved: Path,
) -> bool:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.resolve(strict=True).relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ImmutableInputError(f"destination parent escapes namespace root: {destination.parent}") from exc
    if _existing_bytes(destination, member.sha256, member.bytes):
        return False

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            with archive.open(info, "r") as source:
                digest, total = copy_stream_sha256_hex(source, temporary)
            if total != member.bytes or digest != member.sha256:
                raise ImmutableInputError(f"ZIP member changed during extraction: {member.path}")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o644)
        if _existing_bytes(destination, member.sha256, member.bytes):
            os.unlink(temporary_name)
            temporary_name = None
            return False
        os.replace(temporary_name, destination)
        temporary_name = None
        return True
    except ImmutableInputError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ImmutableInputError(f"cannot atomically provision destination: {destination}") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def provision_immutable_inputs(
    archive_path: Path,
    manifest_path: Path,
    *,
    repo_root: Path,
    workspace_root: Path,
) -> ProvisionResult:
    """Verify then provision immutable inputs into two explicit namespace roots."""
    repo_root = Path(repo_root)
    workspace_root = Path(workspace_root)
    roots = {"repo": repo_root, "workspace": workspace_root}
    resolved_roots = {
        "repo": _resolved_root(repo_root, "repo root"),
        "workspace": _resolved_root(workspace_root, "workspace root"),
    }
    with _verified_archive(Path(archive_path), Path(manifest_path)) as (verified, archive, infos):
        planned: list[tuple[ImmutableInputMember, Path]] = []
        resolved_destinations: set[Path] = set()
        existing: list[str] = []
        for member in verified.members:
            destination = _destination(
                roots[member.namespace], resolved_roots[member.namespace], member.destination
            )
            resolved_destination = destination.resolve(strict=False)
            if resolved_destination in resolved_destinations:
                raise ImmutableInputError(f"duplicate resolved destination: {destination}")
            resolved_destinations.add(resolved_destination)
            if _existing_bytes(destination, member.sha256, member.bytes):
                existing.append(member.path)
            planned.append((member, destination))

        written: list[str] = []
        for member, destination in planned:
            if member.path in existing:
                continue
            if _atomic_extract(
                archive,
                infos[member.path],
                member,
                destination,
                resolved_roots[member.namespace],
            ):
                written.append(member.path)
            else:
                existing.append(member.path)
        return ProvisionResult(
            archive_sha256=verified.archive_sha256,
            member_count=len(verified.members),
            bytes=sum(member.bytes for member in verified.members),
            written=tuple(written),
            existing=tuple(existing),
        )


__all__ = [
    "ImmutableInputError",
    "ImmutableInputMember",
    "ProvisionResult",
    "VerifiedImmutableInputs",
    "provision_immutable_inputs",
    "verify_immutable_inputs",
]
