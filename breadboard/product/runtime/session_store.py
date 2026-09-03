from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from breadboard.product.runtime.artifacts import AnchoredStorage, is_portable_basename
from breadboard.product.runtime.events import AnnotationRecord, KernelEvent, ProcessLock, Session


def session_directory(workspace: Path) -> Path:
    return workspace / ".breadboard" / "sessions"


def session_event_path(workspace: Path, session_id: str) -> Path:
    return session_directory(workspace) / session_id / "session_events.jsonl"


def session_metadata_path(workspace: Path, session_id: str) -> Path:
    return session_directory(workspace) / session_id / "session.json"


def legacy_session_event_path(workspace: Path, session_id: str) -> Path:
    return session_directory(workspace) / f"{session_id}.events.jsonl"


def legacy_session_metadata_path(workspace: Path, session_id: str) -> Path:
    return session_directory(workspace) / f"{session_id}.json"


def event_from_record(record: Mapping[str, Any]) -> KernelEvent:
    return KernelEvent(
        session_id=str(record["session_id"]),
        sequence=int(record["sequence"]),
        kind=str(record["kind"]),
        occurred_at=str(record["occurred_at"]),
        payload=record.get("payload", {}),
        schema_version=str(record.get("schema_version", "bb.session_event.v1")),
    )


def _event_bytes(session: Session) -> bytes:
    return b"".join(
        (json.dumps(event.as_dict(), sort_keys=True) + "\n").encode()
        for event in session.events
    )


def _metadata_bytes(session: Session) -> bytes:
    document = {
        "schema_version": "bb.session.v1",
        **session.read_model.as_dict(),
    }
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()


_TRANSACTION_SCHEMA = "bb.session_transaction.v1"
_TRANSACTION_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "event_name",
        "metadata_name",
        "event_stage_name",
        "metadata_stage_name",
        "event_size",
        "metadata_size",
        "event_sha256",
        "metadata_sha256",
    }
)
# Only the bounded intent metadata is parsed; projections remain in staged files.
_MAX_TRANSACTION_INTENT_BYTES = 64 * 1024
_MAX_SESSION_PROJECTION_BYTES = 64 * 1024 * 1024
_PROJECTION_AUTHORITY_SCHEMA = "bb.session_projection_authority.v1"
_MAX_PROJECTION_AUTHORITY_BYTES = 256 * 1024
_MAX_ARTIFACT_MANIFEST_BYTES = 1024 * 1024
_MAX_ARTIFACT_MANIFESTS = 256
_MAX_ARTIFACT_MANIFEST_AGGREGATE_BYTES = 64 * 1024 * 1024
_T = TypeVar("_T")
_LOCAL_SESSION_LOCKS: weakref.WeakValueDictionary[tuple[str, str], threading.RLock] = (
    weakref.WeakValueDictionary()
)
_LOCAL_SESSION_LOCKS_GUARD = threading.Lock()
SessionDirectoryIdentity = tuple[int, int]


def session_directory_identity(
    workspace: str | Path,
    *,
    create: bool = False,
) -> SessionDirectoryIdentity:
    """Return the identity of a symlink-free workspace session directory."""
    root = _workspace_root(workspace)
    if os.name == "nt":
        handles: list[int] = []
        try:
            for path in (root, root / ".breadboard", session_directory(root)):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=create,
                    )
                )
            metadata = os.stat(session_directory(root), follow_symlinks=False)
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
        return int(metadata.st_dev), int(metadata.st_ino)

    descriptors = [
        os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    try:
        for name in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    name,
                    create=create,
                )
            )
        metadata = os.fstat(descriptors[-1])
        return int(metadata.st_dev), int(metadata.st_ino)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _require_session_directory_identity(
    metadata: os.stat_result,
    expected: SessionDirectoryIdentity | None,
) -> None:
    if expected is None:
        return
    observed = int(metadata.st_dev), int(metadata.st_ino)
    if observed != expected:
        raise OSError("durable session directory identity changed")



def validate_session_id(session_id: str) -> None:
    if not is_portable_basename(session_id):
        raise ValueError("session_id must be a portable identifier")


def _session_identity_key(session_id: str) -> str:
    return session_id.casefold()


def _workspace_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve()


def _session_lock_path(workspace: Path, session_id: str) -> Path:
    identity = (
        f"{_workspace_identity(workspace)}\0{_session_identity_key(session_id)}"
    ).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    locks = _authority_root(workspace, create=True) / "locks"
    locks.mkdir(mode=0o700, exist_ok=True)
    metadata = os.lstat(locks)
    if (
        locks.resolve(strict=True) != locks
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise OSError("unsafe session projection authority lock root")
    if os.name != "nt":
        os.chmod(locks, 0o700)
    return locks / f"{digest}.lock"


@contextmanager
def _local_session_guard(
    workspace: str | Path,
    session_id: str,
    *,
    create: bool,
) -> Iterator[Path]:
    root = _workspace_root(workspace)
    validate_session_id(session_id)
    key = (_workspace_identity_key(root), _session_identity_key(session_id))
    with _LOCAL_SESSION_LOCKS_GUARD:
        local_lock = _LOCAL_SESSION_LOCKS.setdefault(key, threading.RLock())
    with local_lock:
        _ensure_session_layout(root, create=create)
        yield root


@contextmanager
def _session_guard(
    workspace: str | Path,
    session_id: str,
    *,
    create: bool,
) -> Iterator[Path]:
    with _local_session_guard(
        workspace,
        session_id,
        create=create,
    ) as root:
        with ProcessLock(_session_lock_path(root, session_id)):
            yield root


def _ensure_session_layout(workspace: Path, *, create: bool) -> None:
    if os.name == "nt":
        handles: list[int] = []
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                session_directory(workspace),
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=create,
                    )
                )
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
        return

    descriptors = [
        os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    try:
        for name in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    name,
                    create=create,
                )
            )
            if create:
                os.fsync(descriptors[-2])
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _intent_name(session_id: str, *, legacy: bool) -> str:
    return f".{session_id}.session.intent.json" if legacy else ".session.intent.json"


def _stage_names(event_name: str, metadata_name: str) -> tuple[str, str]:
    return f".{event_name}.stage", f".{metadata_name}.stage"


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _validate_projection_sizes(event_size: object, metadata_size: object) -> None:
    if (
        type(event_size) is not int
        or event_size < 0
        or type(metadata_size) is not int
        or metadata_size < 0
    ):
        raise ValueError("invalid session transaction intent metadata")
    if event_size + metadata_size > _MAX_SESSION_PROJECTION_BYTES:
        raise ValueError("session transaction projection is oversized")


def _workspace_identity_key(workspace: Path) -> str:
    resolved = workspace.resolve()
    metadata = os.stat(resolved, follow_symlinks=False)
    device = int(getattr(metadata, "st_dev", 0))
    inode = int(getattr(metadata, "st_ino", 0))
    if inode:
        return f"file:{device}:{inode}"
    return "path:" + str(resolved).casefold()


def _workspace_identity(workspace: Path) -> str:
    physical = _workspace_identity_key(workspace).encode("utf-8")
    return "sha256:" + hashlib.sha256(physical).hexdigest()


def _authority_root(workspace: Path, *, create: bool) -> Path:
    configured = os.environ.get("BREADBOARD_SESSION_AUTHORITY_ROOT")
    root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".breadboard" / "session-authority"
    )
    if not root.is_absolute():
        raise ValueError("session projection authority root must be absolute")
    lexical = Path(os.path.normpath(os.fspath(root)))
    if create:
        lexical.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        resolved = lexical.resolve(strict=True)
        metadata = os.lstat(lexical)
    except FileNotFoundError:
        if create:
            raise
        return lexical
    if (
        resolved != lexical
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise OSError("unsafe session projection authority root")
    if (
        resolved == workspace
        or resolved.is_relative_to(workspace)
        or workspace.is_relative_to(resolved)
    ):
        raise OSError("session projection authority overlaps the workspace")
    current_uid = getattr(os, "geteuid", lambda: metadata.st_uid)()
    if getattr(metadata, "st_uid", current_uid) != current_uid:
        raise OSError("session projection authority has the wrong owner")
    if os.name != "nt":
        os.chmod(resolved, 0o700)
        if stat.S_IMODE(os.lstat(resolved).st_mode) != 0o700:
            raise OSError("session projection authority is not owner-only")
    return resolved


def _authority_name(workspace: Path, session_id: str) -> str:
    identity = (
        f"{_workspace_identity(workspace)}\0{_session_identity_key(session_id)}"
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest() + ".json"


def _projection_identity(
    event_payload: bytes,
    metadata_payload: bytes,
) -> dict[str, Any]:
    return {
        "event_size": len(event_payload),
        "metadata_size": len(metadata_payload),
        "event_sha256": _digest(event_payload),
        "metadata_sha256": _digest(metadata_payload),
    }


def _validate_projection_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "event_size",
        "metadata_size",
        "event_sha256",
        "metadata_sha256",
    }:
        raise ValueError("invalid session projection authority")
    _validate_projection_sizes(value["event_size"], value["metadata_size"])
    if not _is_digest(value["event_sha256"]) or not _is_digest(
        value["metadata_sha256"]
    ):
        raise ValueError("invalid session projection authority")
    return value


def _validate_authorized_manifests(
    value: object,
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _MAX_ARTIFACT_MANIFESTS:
        raise ValueError("invalid session artifact authority")
    aggregate = 0
    result: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"name", "size", "sha256"}:
            raise ValueError("invalid session artifact authority")
        name = row["name"]
        size = row["size"]
        digest = row["sha256"]
        if (
            not isinstance(name, str)
            or not name.startswith(f"{session_id}.")
            or not name.endswith(".json")
            or type(size) is not int
            or size < 0
            or size > _MAX_ARTIFACT_MANIFEST_BYTES
            or not _is_digest(digest)
            or name != f"{session_id}.{digest[7:]}.json"
        ):
            raise ValueError("invalid session artifact authority")
        aggregate += size
        if aggregate > _MAX_ARTIFACT_MANIFEST_AGGREGATE_BYTES:
            raise ValueError("session artifact authority is oversized")
        result.append({"name": name, "size": size, "sha256": digest})
    if result != sorted(result, key=lambda row: row["name"]):
        raise ValueError("session artifact authority is not canonical")
    return result


def _validate_authority_record(
    value: object,
    *,
    workspace: Path,
    session_id: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "workspace_id",
        "session_id",
        "generation",
        "state",
        "target",
        "previous",
        "manifests",
    }:
        raise ValueError("invalid session projection authority")
    if (
        value["schema_version"] != _PROJECTION_AUTHORITY_SCHEMA
        or value["workspace_id"] != _workspace_identity(workspace)
        or value["session_id"] != session_id
        or type(value["generation"]) is not int
        or value["generation"] < 1
        or value["state"] not in {"preparing", "committed"}
    ):
        raise ValueError("invalid session projection authority")
    _validate_projection_identity(value["target"])
    previous = value["previous"]
    if previous is not None:
        _validate_projection_identity(previous)
    if value["state"] == "committed" and previous is not None:
        raise ValueError("invalid committed session projection authority")
    _validate_authorized_manifests(value["manifests"], session_id=session_id)
    return value


def _read_projection_authority(
    workspace: Path,
    session_id: str,
) -> dict[str, Any] | None:
    root = _authority_root(workspace, create=False)
    name = _authority_name(workspace, session_id)
    try:
        if os.name == "nt":
            body = _read_windows_file(
                root / name,
                _MAX_PROJECTION_AUTHORITY_BYTES,
            )
        else:
            descriptor = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                body = _read_posix_file(
                    descriptor,
                    name,
                    _MAX_PROJECTION_AUTHORITY_BYTES,
                )
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        return None
    try:
        record = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid session projection authority") from error
    return _validate_authority_record(
        record,
        workspace=workspace,
        session_id=session_id,
    )


def _write_projection_authority(
    workspace: Path,
    session_id: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    canonical = _validate_authority_record(
        dict(record),
        workspace=workspace,
        session_id=session_id,
    )
    body = (json.dumps(canonical, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )
    if len(body) > _MAX_PROJECTION_AUTHORITY_BYTES:
        raise ValueError("session projection authority is oversized")
    root = _authority_root(workspace, create=True)
    name = _authority_name(workspace, session_id)
    if os.name == "nt":
        _write_windows_atomic(root / name, body)
    else:
        descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            AnchoredStorage.write_at(descriptor, name, body)
        finally:
            os.close(descriptor)
    return canonical


def _committed_authority(
    record: Mapping[str, Any],
    *,
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **record,
        "state": "committed",
        "target": dict(target or record["target"]),
        "previous": None,
    }


def _projection_transaction_evidence_exists(
    workspace: Path,
    session_id: str,
) -> bool:
    nested_names = (
        _intent_name(session_id, legacy=False),
        *_stage_names("session_events.jsonl", "session.json"),
        "session_events.jsonl",
        "session.json",
    )
    legacy_event_name = f"{session_id}.events.jsonl"
    legacy_metadata_name = f"{session_id}.json"
    legacy_names = (
        _intent_name(session_id, legacy=True),
        *_stage_names(legacy_event_name, legacy_metadata_name),
        legacy_event_name,
        legacy_metadata_name,
    )

    if os.name == "nt":
        root = session_directory(workspace)
        for path in (
            *(root / name for name in legacy_names),
            *(root / session_id / name for name in nested_names),
        ):
            try:
                descriptor = AnchoredStorage.windows_file_descriptor(
                    path,
                    create=False,
                )
            except FileNotFoundError:
                continue
            else:
                os.close(descriptor)
                return True
        return False

    descriptors = [
        os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    try:
        for component in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    component,
                    create=False,
                )
            )
        parent = descriptors[-1]
        for name in legacy_names:
            try:
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError(f"unsafe session target: {name}")
            return True
        try:
            nested = AnchoredStorage.open_directory(
                parent,
                session_id,
                create=False,
            )
        except FileNotFoundError:
            return False
        descriptors.append(nested)
        for name in nested_names:
            try:
                metadata = os.stat(name, dir_fd=nested, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError(f"unsafe session target: {name}")
            return True
        return False
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _prepare_projection_authority(
    workspace: Path,
    session_id: str,
    target: Mapping[str, Any],
) -> dict[str, Any]:
    previous = _read_projection_authority(workspace, session_id)
    previous_target: dict[str, Any] | None = None
    if previous is not None:
        if previous["state"] == "committed":
            previous_target = dict(previous["target"])
        elif _projection_transaction_evidence_exists(workspace, session_id):
            raise ValueError("session projection authority is not committed")
    record = {
        "schema_version": _PROJECTION_AUTHORITY_SCHEMA,
        "workspace_id": _workspace_identity(workspace),
        "session_id": session_id,
        "generation": 1 if previous is None else previous["generation"] + 1,
        "state": "preparing",
        "target": dict(target),
        "previous": previous_target,
        "manifests": [] if previous is None else list(previous["manifests"]),
    }
    return _write_projection_authority(workspace, session_id, record)


def _require_recovery_authority(
    workspace: Path,
    session_id: str,
    target: Mapping[str, Any],
) -> dict[str, Any]:
    record = _read_projection_authority(workspace, session_id)
    if (
        record is None
        or record["target"] != dict(target)
        or record["state"] not in {"preparing", "committed"}
    ):
        raise ValueError("session projection authority mismatch")
    return record


def _reconcile_projection_authority(
    workspace: Path,
    session_id: str,
    target: Mapping[str, Any],
) -> None:
    record = _read_projection_authority(workspace, session_id)
    if record is None:
        raise ValueError("session projection authority is missing")
    if record["state"] == "committed":
        if record["target"] != dict(target):
            raise ValueError("session projection authority mismatch")
        return
    if record["target"] == dict(target):
        _write_projection_authority(
            workspace,
            session_id,
            _committed_authority(record),
        )
        return
    if record["previous"] == dict(target):
        _write_projection_authority(
            workspace,
            session_id,
            _committed_authority(record, target=target),
        )
        return
    raise ValueError("session projection authority mismatch")


def _intent_bytes(
    session_id: str,
    event_name: str,
    metadata_name: str,
    event_stage_name: str,
    metadata_stage_name: str,
    event_size: int,
    metadata_size: int,
    event_sha256: str,
    metadata_sha256: str,
) -> bytes:
    _validate_projection_sizes(event_size, metadata_size)
    document = {
        "schema_version": _TRANSACTION_SCHEMA,
        "session_id": session_id,
        "event_name": event_name,
        "metadata_name": metadata_name,
        "event_stage_name": event_stage_name,
        "metadata_stage_name": metadata_stage_name,
        "event_size": event_size,
        "metadata_size": metadata_size,
        "event_sha256": event_sha256,
        "metadata_sha256": metadata_sha256,
    }
    body = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )
    if len(body) > _MAX_TRANSACTION_INTENT_BYTES:
        raise ValueError("session transaction intent is oversized")
    return body


def _decode_intent(
    body: bytes,
    *,
    session_id: str,
    event_name: str,
    metadata_name: str,
) -> tuple[str, str, int, int, str, str]:
    if len(body) > _MAX_TRANSACTION_INTENT_BYTES:
        raise ValueError("session transaction intent is oversized")
    try:
        document = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid session transaction intent") from error
    event_stage_name, metadata_stage_name = _stage_names(event_name, metadata_name)
    if (
        not isinstance(document, dict)
        or set(document) != _TRANSACTION_FIELDS
        or document.get("schema_version") != _TRANSACTION_SCHEMA
        or document.get("session_id") != session_id
        or document.get("event_name") != event_name
        or document.get("metadata_name") != metadata_name
        or document.get("event_stage_name") != event_stage_name
        or document.get("metadata_stage_name") != metadata_stage_name
    ):
        raise ValueError("mismatched session transaction intent")
    event_size = document.get("event_size")
    metadata_size = document.get("metadata_size")
    event_sha256 = document.get("event_sha256")
    metadata_sha256 = document.get("metadata_sha256")
    _validate_projection_sizes(event_size, metadata_size)
    if not _is_digest(event_sha256) or not _is_digest(metadata_sha256):
        raise ValueError("invalid session transaction intent metadata")
    return (
        event_stage_name,
        metadata_stage_name,
        event_size,
        metadata_size,
        event_sha256,
        metadata_sha256,
    )


def _session_from_payloads(
    event_payload: bytes,
    metadata_payload: bytes,
    *,
    session_id: str,
) -> Session:
    try:
        events = [
            event_from_record(json.loads(line))
            for line in event_payload.decode().splitlines()
            if line.strip()
        ]
        metadata = json.loads(metadata_payload.decode())
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise ValueError("invalid session transaction projection") from error
    if not events or any(event.session_id != session_id for event in events):
        raise ValueError("session transaction event identity mismatch")
    restored = Session.restore(events)
    if restored.read_model.session_id != session_id:
        raise ValueError("session transaction event identity mismatch")
    expected = {
        "schema_version": "bb.session.v1",
        **restored.read_model.as_dict(),
    }
    if metadata != expected:
        raise ValueError("session transaction projections do not match")
    return restored


def _assert_posix_target(parent: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"unsafe session target: {name}")


def _read_posix_file(parent: int, name: str, max_bytes: int) -> bytes:
    if max_bytes < 0:
        raise ValueError("maximum read size must be nonnegative")
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError(f"unsafe session intent: {name}")
        if metadata.st_size > max_bytes:
            raise ValueError("session transaction intent is oversized")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read(max_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_posix_staged_file(parent: int, name: str, expected_size: int) -> bytes:
    _validate_projection_sizes(expected_size, 0)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError(f"unsafe session staging file: {name}")
        if metadata.st_size != expected_size:
            raise ValueError("session transaction staging size mismatch")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read(expected_size + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_staged_payload(
    payload: bytes,
    expected_size: int,
    expected_digest: str,
) -> None:
    if len(payload) != expected_size or _digest(payload) != expected_digest:
        raise ValueError("session transaction intent digest mismatch")


def _recover_intent_posix(
    parent: int,
    workspace: Path,
    session_id: str,
    *,
    legacy: bool,
) -> None:
    event_name = f"{session_id}.events.jsonl" if legacy else "session_events.jsonl"
    metadata_name = f"{session_id}.json" if legacy else "session.json"
    intent_name = _intent_name(session_id, legacy=legacy)
    try:
        body = _read_posix_file(
            parent,
            intent_name,
            _MAX_TRANSACTION_INTENT_BYTES,
        )
    except FileNotFoundError:
        return
    (
        event_stage_name,
        metadata_stage_name,
        event_size,
        metadata_size,
        event_sha256,
        metadata_sha256,
    ) = _decode_intent(
        body,
        session_id=session_id,
        event_name=event_name,
        metadata_name=metadata_name,
    )
    target = {
        "event_size": event_size,
        "metadata_size": metadata_size,
        "event_sha256": event_sha256,
        "metadata_sha256": metadata_sha256,
    }
    authority = _require_recovery_authority(workspace, session_id, target)
    event_payload = _read_posix_staged_file(parent, event_stage_name, event_size)
    metadata_payload = _read_posix_staged_file(
        parent, metadata_stage_name, metadata_size
    )
    _verify_staged_payload(event_payload, event_size, event_sha256)
    _verify_staged_payload(metadata_payload, metadata_size, metadata_sha256)
    _session_from_payloads(
        event_payload,
        metadata_payload,
        session_id=session_id,
    )
    for name in (
        intent_name,
        event_stage_name,
        metadata_stage_name,
        event_name,
        metadata_name,
    ):
        _assert_posix_target(parent, name)
    AnchoredStorage.write_at(parent, event_name, event_payload)
    AnchoredStorage.write_at(parent, metadata_name, metadata_payload)
    _write_projection_authority(
        workspace,
        session_id,
        _committed_authority(authority),
    )
    for name in (intent_name, event_stage_name, metadata_stage_name):
        os.unlink(name, dir_fd=parent)
    os.fsync(parent)

def _recover_orphaned_stages_posix(
    parent: int,
    workspace: Path,
    session_id: str,
    *,
    legacy: bool,
) -> None:
    authority = _read_projection_authority(workspace, session_id)
    if authority is None or authority["state"] != "preparing":
        return
    event_name = f"{session_id}.events.jsonl" if legacy else "session_events.jsonl"
    metadata_name = f"{session_id}.json" if legacy else "session.json"
    event_stage_name, metadata_stage_name = _stage_names(event_name, metadata_name)
    target = authority["target"]
    try:
        event_payload = _read_posix_staged_file(
            parent, event_stage_name, target["event_size"]
        )
    except FileNotFoundError:
        try:
            _read_posix_staged_file(
                parent, metadata_stage_name, target["metadata_size"]
            )
        except FileNotFoundError:
            return
        os.unlink(metadata_stage_name, dir_fd=parent)
        os.fsync(parent)
        return
    try:
        metadata_payload = _read_posix_staged_file(
            parent, metadata_stage_name, target["metadata_size"]
        )
    except FileNotFoundError:
        os.unlink(event_stage_name, dir_fd=parent)
        os.fsync(parent)
        return
    _verify_staged_payload(
        event_payload, target["event_size"], target["event_sha256"]
    )
    _verify_staged_payload(
        metadata_payload, target["metadata_size"], target["metadata_sha256"]
    )
    _session_from_payloads(
        event_payload,
        metadata_payload,
        session_id=session_id,
    )
    for name in (
        event_stage_name,
        metadata_stage_name,
        event_name,
        metadata_name,
    ):
        _assert_posix_target(parent, name)
    AnchoredStorage.write_at(parent, event_name, event_payload)
    AnchoredStorage.write_at(parent, metadata_name, metadata_payload)
    _write_projection_authority(
        workspace,
        session_id,
        _committed_authority(authority),
    )
    for name in (event_stage_name, metadata_stage_name):
        os.unlink(name, dir_fd=parent)
    os.fsync(parent)


def _recover_orphaned_stages_windows(
    parent: Path,
    workspace: Path,
    session_id: str,
    *,
    legacy: bool,
) -> None:
    authority = _read_projection_authority(workspace, session_id)
    if authority is None or authority["state"] != "preparing":
        return
    event_name = f"{session_id}.events.jsonl" if legacy else "session_events.jsonl"
    metadata_name = f"{session_id}.json" if legacy else "session.json"
    event_stage_name, metadata_stage_name = _stage_names(event_name, metadata_name)
    target = authority["target"]
    event_stage_path = parent / event_stage_name
    metadata_stage_path = parent / metadata_stage_name
    try:
        event_payload = _read_windows_staged_file(
            event_stage_path, target["event_size"]
        )
    except FileNotFoundError:
        try:
            _read_windows_staged_file(metadata_stage_path, target["metadata_size"])
        except FileNotFoundError:
            return
        metadata_stage_path.unlink()
        return
    try:
        metadata_payload = _read_windows_staged_file(
            metadata_stage_path, target["metadata_size"]
        )
    except FileNotFoundError:
        event_stage_path.unlink()
        return
    _verify_staged_payload(
        event_payload, target["event_size"], target["event_sha256"]
    )
    _verify_staged_payload(
        metadata_payload, target["metadata_size"], target["metadata_sha256"]
    )
    _session_from_payloads(
        event_payload,
        metadata_payload,
        session_id=session_id,
    )
    for name in (
        event_stage_name,
        metadata_stage_name,
        event_name,
        metadata_name,
    ):
        _assert_windows_target(parent / name)
    _write_windows_atomic(parent / event_name, event_payload)
    _write_windows_atomic(parent / metadata_name, metadata_payload)
    _write_projection_authority(
        workspace,
        session_id,
        _committed_authority(authority),
    )
    event_stage_path.unlink()
    metadata_stage_path.unlink()


def _read_windows_file(path: Path, max_bytes: int) -> bytes:
    if max_bytes < 0:
        raise ValueError("maximum read size must be nonnegative")
    descriptor = AnchoredStorage.windows_file_descriptor(path, create=False)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError(f"unsafe session intent: {path.name}")
        if metadata.st_size > max_bytes:
            raise ValueError("session transaction intent is oversized")
        return os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)


def _read_windows_staged_file(path: Path, expected_size: int) -> bytes:
    _validate_projection_sizes(expected_size, 0)
    descriptor = AnchoredStorage.windows_file_descriptor(path, create=False)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError(f"unsafe session staging file: {path.name}")
        if metadata.st_size != expected_size:
            raise ValueError("session transaction staging size mismatch")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read(expected_size + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _recover_intent_windows(
    parent: Path,
    workspace: Path,
    session_id: str,
    *,
    legacy: bool,
) -> None:
    event_name = f"{session_id}.events.jsonl" if legacy else "session_events.jsonl"
    metadata_name = f"{session_id}.json" if legacy else "session.json"
    intent_name = _intent_name(session_id, legacy=legacy)
    try:
        body = _read_windows_file(
            parent / intent_name,
            _MAX_TRANSACTION_INTENT_BYTES,
        )
    except FileNotFoundError:
        return
    (
        event_stage_name,
        metadata_stage_name,
        event_size,
        metadata_size,
        event_sha256,
        metadata_sha256,
    ) = _decode_intent(
        body,
        session_id=session_id,
        event_name=event_name,
        metadata_name=metadata_name,
    )
    target = {
        "event_size": event_size,
        "metadata_size": metadata_size,
        "event_sha256": event_sha256,
        "metadata_sha256": metadata_sha256,
    }
    authority = _require_recovery_authority(workspace, session_id, target)
    event_payload = _read_windows_staged_file(parent / event_stage_name, event_size)
    metadata_payload = _read_windows_staged_file(
        parent / metadata_stage_name, metadata_size
    )
    _verify_staged_payload(event_payload, event_size, event_sha256)
    _verify_staged_payload(metadata_payload, metadata_size, metadata_sha256)
    _session_from_payloads(
        event_payload,
        metadata_payload,
        session_id=session_id,
    )
    for name in (
        intent_name,
        event_stage_name,
        metadata_stage_name,
        event_name,
        metadata_name,
    ):
        _assert_windows_target(parent / name)
    _write_windows_atomic(parent / event_name, event_payload)
    _write_windows_atomic(parent / metadata_name, metadata_payload)
    _write_projection_authority(
        workspace,
        session_id,
        _committed_authority(authority),
    )
    for name in (intent_name, event_stage_name, metadata_stage_name):
        (parent / name).unlink()


def _recover_pending_intents(workspace: Path, session_id: str) -> None:
    if os.name == "nt":
        handles: list[int] = []
        nested_handle: int | None = None
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                session_directory(workspace),
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=False,
                    )
                )
            sessions = session_directory(workspace)
            try:
                nested = session_event_path(workspace, session_id).parent
                nested_handle = AnchoredStorage.windows_handle(
                    nested,
                    directory=True,
                    create=False,
                )
            except FileNotFoundError:
                pass
            else:
                _recover_intent_windows(
                    nested,
                    workspace,
                    session_id,
                    legacy=False,
                )
                _recover_orphaned_stages_windows(
                    nested,
                    workspace,
                    session_id,
                    legacy=False,
                )
            _recover_intent_windows(
                sessions,
                workspace,
                session_id,
                legacy=True,
            )
            _recover_orphaned_stages_windows(
                sessions,
                workspace,
                session_id,
                legacy=True,
            )
        finally:
            if nested_handle is not None:
                AnchoredStorage.close_windows_handle(nested_handle)
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
        return

    descriptors = [
        os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    nested_descriptor: int | None = None
    try:
        for name in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    name,
                    create=False,
                )
            )
        sessions = descriptors[-1]
        try:
            nested_descriptor = AnchoredStorage.open_directory(
                sessions,
                session_id,
                create=False,
            )
        except FileNotFoundError:
            pass
        else:
            _recover_intent_posix(
                nested_descriptor,
                workspace,
                session_id,
                legacy=False,
            )
            _recover_orphaned_stages_posix(
                nested_descriptor,
                workspace,
                session_id,
                legacy=False,
            )
        _recover_intent_posix(
            sessions,
            workspace,
            session_id,
            legacy=True,
        )
        _recover_orphaned_stages_posix(
            sessions,
            workspace,
            session_id,
            legacy=True,
        )
    finally:
        if nested_descriptor is not None:
            os.close(nested_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _assert_windows_target(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"unsafe session target: {path.name}")


def _write_windows_file(path: Path, content: bytes) -> None:
    try:
        descriptor = AnchoredStorage.windows_file_descriptor(path, create=False)
    except FileNotFoundError:
        descriptor = AnchoredStorage.windows_file_descriptor(path, create=True)
    with os.fdopen(descriptor, "r+b", buffering=0) as stream:
        stream.seek(0)
        stream.truncate()
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _write_windows_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    try:
        _write_windows_file(temporary, content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_session_locked(
    workspace: Path,
    session: Session,
    event_path: Path | None = None,
    *,
    expected_session_directory_identity: SessionDirectoryIdentity | None = None,
) -> Path:
    session_id = session.read_model.session_id
    validate_session_id(session_id)
    nested_path = session_event_path(workspace, session_id)
    legacy_path = legacy_session_event_path(workspace, session_id)
    if event_path is None:
        event_path = nested_path
    if event_path not in (nested_path, legacy_path):
        raise ValueError("session event path is outside the session store")
    legacy = event_path == legacy_path
    event_name = f"{session_id}.events.jsonl" if legacy else "session_events.jsonl"
    metadata_name = f"{session_id}.json" if legacy else "session.json"
    event_payload = _event_bytes(session)
    metadata_payload = _metadata_bytes(session)
    event_stage_name, metadata_stage_name = _stage_names(event_name, metadata_name)
    intent_payload = _intent_bytes(
        session_id,
        event_name,
        metadata_name,
        event_stage_name,
        metadata_stage_name,
        len(event_payload),
        len(metadata_payload),
        _digest(event_payload),
        _digest(metadata_payload),
    )
    intent_name = _intent_name(session_id, legacy=legacy)
    if expected_session_directory_identity is not None:
        observed_identity = session_directory_identity(workspace)
        if observed_identity != expected_session_directory_identity:
            raise OSError("durable session directory identity changed")
    target = _projection_identity(event_payload, metadata_payload)
    authority = _prepare_projection_authority(workspace, session_id, target)

    if os.name == "nt":
        handles: list[int] = []
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                session_directory(workspace),
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=False,
                    )
                )
            _require_session_directory_identity(
                os.stat(session_directory(workspace), follow_symlinks=False),
                expected_session_directory_identity,
            )
            parent = session_directory(workspace)
            if not legacy:
                parent = nested_path.parent
                handles.append(
                    AnchoredStorage.windows_handle(
                        parent,
                        directory=True,
                        create=True,
                    )
                )
            for name in (
                intent_name,
                event_stage_name,
                metadata_stage_name,
                event_name,
                metadata_name,
            ):
                _assert_windows_target(parent / name)
            _write_windows_atomic(parent / event_stage_name, event_payload)
            _write_windows_atomic(parent / metadata_stage_name, metadata_payload)
            _write_windows_atomic(parent / intent_name, intent_payload)
            _write_windows_atomic(parent / event_name, event_payload)
            _write_windows_atomic(parent / metadata_name, metadata_payload)
            _write_projection_authority(
                workspace,
                session_id,
                _committed_authority(authority),
            )
            for name in (intent_name, event_stage_name, metadata_stage_name):
                (parent / name).unlink()
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
        return event_path

    descriptors = [
        os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    try:
        for name in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    name,
                    create=False,
                )
            )
        _require_session_directory_identity(
            os.fstat(descriptors[-1]),
            expected_session_directory_identity,
        )
        parent = descriptors[-1]
        if not legacy:
            descriptors.append(
                AnchoredStorage.open_directory(
                    parent,
                    session_id,
                    create=True,
                )
            )
            os.fsync(parent)
            parent = descriptors[-1]
        for name in (
            intent_name,
            event_stage_name,
            metadata_stage_name,
            event_name,
            metadata_name,
        ):
            _assert_posix_target(parent, name)
        AnchoredStorage.write_at(parent, event_stage_name, event_payload)
        AnchoredStorage.write_at(parent, metadata_stage_name, metadata_payload)
        AnchoredStorage.write_at(parent, intent_name, intent_payload)
        AnchoredStorage.write_at(parent, event_name, event_payload)
        AnchoredStorage.write_at(parent, metadata_name, metadata_payload)
        _write_projection_authority(
            workspace,
            session_id,
            _committed_authority(authority),
        )
        for name in (intent_name, event_stage_name, metadata_stage_name):
            os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return event_path


def _load_anchored(
    workspace: Path,
    session_id: str,
    *,
    bootstrap_authority: bool = False,
) -> tuple[Session, Path]:
    validate_session_id(session_id)
    if os.name == "nt":
        handles = []
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                session_directory(workspace),
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=False,
                    )
                )
            event_path = session_event_path(workspace, session_id)
            metadata_path = session_metadata_path(workspace, session_id)
            try:
                handles.append(
                    AnchoredStorage.windows_handle(
                        event_path.parent,
                        directory=True,
                        create=False,
                    )
                )
                event_payload = _read_windows_file(
                    event_path,
                    _MAX_SESSION_PROJECTION_BYTES,
                )
                metadata_payload = _read_windows_file(
                    metadata_path,
                    _MAX_SESSION_PROJECTION_BYTES - len(event_payload),
                )
            except FileNotFoundError:
                event_path = legacy_session_event_path(workspace, session_id)
                metadata_path = legacy_session_metadata_path(workspace, session_id)
                event_payload = _read_windows_file(
                    event_path,
                    _MAX_SESSION_PROJECTION_BYTES,
                )
                metadata_payload = _read_windows_file(
                    metadata_path,
                    _MAX_SESSION_PROJECTION_BYTES - len(event_payload),
                )
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
    else:
        descriptors = [
            os.open(
                workspace,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        ]
        session_descriptor = None
        try:
            for name in (".breadboard", "sessions"):
                descriptors.append(
                    AnchoredStorage.open_directory(
                        descriptors[-1],
                        name,
                        create=False,
                    )
                )
            try:
                session_descriptor = AnchoredStorage.open_directory(
                    descriptors[-1],
                    session_id,
                    create=False,
                )
                event_payload = _read_posix_file(
                    session_descriptor,
                    "session_events.jsonl",
                    _MAX_SESSION_PROJECTION_BYTES,
                )
                metadata_payload = _read_posix_file(
                    session_descriptor,
                    "session.json",
                    _MAX_SESSION_PROJECTION_BYTES - len(event_payload),
                )
                event_path = session_event_path(workspace, session_id)
            except FileNotFoundError:
                event_payload = _read_posix_file(
                    descriptors[-1],
                    f"{session_id}.events.jsonl",
                    _MAX_SESSION_PROJECTION_BYTES,
                )
                metadata_payload = _read_posix_file(
                    descriptors[-1],
                    f"{session_id}.json",
                    _MAX_SESSION_PROJECTION_BYTES - len(event_payload),
                )
                event_path = legacy_session_event_path(workspace, session_id)
        finally:
            if session_descriptor is not None:
                os.close(session_descriptor)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    target = _projection_identity(event_payload, metadata_payload)
    if bootstrap_authority:
        session = _session_from_payloads(
            event_payload,
            metadata_payload,
            session_id=session_id,
        )
        authority = _read_projection_authority(workspace, session_id)
        if authority is None:
            authority = _prepare_projection_authority(workspace, session_id, target)
            _write_projection_authority(
                workspace,
                session_id,
                _committed_authority(authority),
            )
        else:
            _reconcile_projection_authority(workspace, session_id, target)
    else:
        _reconcile_projection_authority(workspace, session_id, target)
        session = _session_from_payloads(
            event_payload,
            metadata_payload,
            session_id=session_id,
        )
    return session, event_path


def bootstrap_local_session_authority(
    workspace: str | Path,
    session_id: str,
) -> tuple[Session, Path]:
    """Explicitly trust one validated pre-authority projection from local storage."""
    with _session_guard(workspace, session_id, create=False) as root:
        _recover_pending_intents(root, session_id)
        return _load_anchored(root, session_id, bootstrap_authority=True)


def mutate_session(
    workspace: str | Path,
    session_id: str,
    mutation: Callable[[Session], _T],
) -> tuple[_T, Path]:
    """Run one domain mutation while holding the durable session guard."""
    with _session_guard(workspace, session_id, create=False) as root:
        _recover_pending_intents(root, session_id)
        session, event_path = _load_anchored(root, session_id)
        result = mutation(session)
        _persist_session_locked(root, session, event_path)
        return result, event_path


def create_session(
    workspace: str | Path,
    session: Session,
    event_path: Path | None = None,
    *,
    expected_session_directory_identity: SessionDirectoryIdentity | None = None,
) -> tuple[Session, Path]:
    """Atomically claim a new session id and publish its first projection."""
    session_id = session.read_model.session_id
    validate_session_id(session_id)
    with _session_guard(workspace, session_id, create=True) as root:
        _recover_pending_intents(root, session_id)
        collision = next(
            (
                existing
                for existing in session_names(root)
                if _session_identity_key(existing) == _session_identity_key(session_id)
                and existing != session_id
            ),
            None,
        )
        if collision is not None:
            raise ValueError(f"session id collides with existing session: {collision}")
        try:
            _load_anchored(root, session_id)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"session already exists: {session_id}")
        published_path = _persist_session_locked(
            root,
            session,
            event_path,
            expected_session_directory_identity=expected_session_directory_identity,
        )

        def commit_annotation(
            record: AnnotationRecord,
        ) -> tuple[KernelEvent, ...]:
            def annotate(persisted: Session) -> tuple[KernelEvent, ...]:
                persisted.annotate(record)
                return persisted.events

            events, _ = mutate_session(root, session_id, annotate)
            return events

        session._bind_terminal_annotation_commit(commit_annotation)
        return session, published_path


def _load_session_locked(
    workspace: Path,
    session_id: str,
) -> tuple[Session, Path]:
    _recover_pending_intents(workspace, session_id)
    return _load_anchored(workspace, session_id)


def _load_untrusted_running_anchored(
    workspace: Path,
    session_id: str,
) -> tuple[Session, Path]:
    if os.name == "nt":
        handles = []
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                session_directory(workspace),
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=False,
                    )
                )
            event_path = session_event_path(workspace, session_id)
            try:
                handles.append(
                    AnchoredStorage.windows_handle(
                        event_path.parent,
                        directory=True,
                        create=False,
                    )
                )
                event_payload = _read_windows_file(
                    event_path,
                    _MAX_SESSION_PROJECTION_BYTES,
                )
            except FileNotFoundError:
                event_path = legacy_session_event_path(workspace, session_id)
                event_payload = _read_windows_file(
                    event_path,
                    _MAX_SESSION_PROJECTION_BYTES,
                )
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
    else:
        descriptors = [
            os.open(
                workspace,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        ]
        session_descriptor = None
        try:
            for name in (".breadboard", "sessions"):
                descriptors.append(
                    AnchoredStorage.open_directory(
                        descriptors[-1],
                        name,
                        create=False,
                    )
                )
            try:
                session_descriptor = AnchoredStorage.open_directory(
                    descriptors[-1],
                    session_id,
                    create=False,
                )
                event_payload = _read_posix_file(
                    session_descriptor,
                    "session_events.jsonl",
                    _MAX_SESSION_PROJECTION_BYTES,
                )
                event_path = session_event_path(workspace, session_id)
            except FileNotFoundError:
                event_payload = _read_posix_file(
                    descriptors[-1],
                    f"{session_id}.events.jsonl",
                    _MAX_SESSION_PROJECTION_BYTES,
                )
                event_path = legacy_session_event_path(workspace, session_id)
        finally:
            if session_descriptor is not None:
                os.close(session_descriptor)
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    try:
        events = [
            event_from_record(json.loads(line))
            for line in event_payload.decode().splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid session event projection") from error
    session = Session.restore(events)
    if session.read_model.session_id != session_id or session.read_model.status in {
        "completed",
        "failed",
        "canceled",
    }:
        raise ValueError("terminal session projection lacks private authority")
    return session, event_path


def load_session(
    workspace: str | Path,
    session_id: str,
    *,
    allow_untrusted_running: bool = False,
) -> tuple[Session, Path]:
    root = _workspace_root(workspace)
    try:
        with _session_guard(root, session_id, create=False):
            try:
                return _load_session_locked(root, session_id)
            except (FileNotFoundError, ValueError):
                if (
                    not allow_untrusted_running
                    or _read_projection_authority(root, session_id) is not None
                ):
                    raise
                return _load_untrusted_running_anchored(root, session_id)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise FileNotFoundError(f"session not found: {session_id}") from error


def session_names(workspace: Path) -> list[str]:
    """Return normalized durable session ids, excluding transaction auxiliaries."""
    suffix = ".events.jsonl"
    names: list[str] = []

    def add(candidate: str) -> None:
        try:
            validate_session_id(candidate)
        except ValueError:
            return
        names.append(candidate)

    if os.name == "nt":
        handles = []
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                session_directory(workspace),
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=False,
                    )
                )
            directory = session_directory(workspace)
            for name in os.listdir(directory):
                try:
                    handle = AnchoredStorage.windows_handle(
                        directory / name,
                        directory=True,
                        create=False,
                    )
                except OSError:
                    if not name.endswith(suffix):
                        continue
                    try:
                        descriptor = AnchoredStorage.windows_file_descriptor(
                            directory / name,
                            create=False,
                        )
                    except OSError:
                        continue
                    os.close(descriptor)
                    add(name[: -len(suffix)])
                else:
                    AnchoredStorage.close_windows_handle(handle)
                    add(name)
            return names
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)

    descriptors = [
        os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    try:
        for component in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    component,
                    create=False,
                )
            )
        for name in os.listdir(descriptors[-1]):
            try:
                metadata = os.stat(
                    name,
                    dir_fd=descriptors[-1],
                    follow_symlinks=False,
                )
            except (FileNotFoundError, NotADirectoryError):
                continue
            if stat.S_ISDIR(metadata.st_mode):
                add(name)
            elif stat.S_ISREG(metadata.st_mode) and name.endswith(suffix):
                add(name[: -len(suffix)])
        return names
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _manifest_digest_from_name(session_id: str, manifest_name: str) -> str:
    prefix = f"{session_id}."
    if (
        not manifest_name.startswith(prefix)
        or not manifest_name.endswith(".json")
        or "/" in manifest_name
        or "\\" in manifest_name
    ):
        raise ValueError("invalid artifact manifest name")
    digest = manifest_name[len(prefix) : -5]
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("invalid artifact manifest name")
    return "sha256:" + digest


def _read_workspace_manifest(
    workspace: Path,
    manifest_name: str,
) -> bytes:
    if os.name == "nt":
        handles = []
        try:
            for path in (
                workspace,
                workspace / ".breadboard",
                workspace / ".breadboard" / "artifacts",
                workspace / ".breadboard" / "artifacts" / "manifests",
            ):
                handles.append(
                    AnchoredStorage.windows_handle(
                        path,
                        directory=True,
                        create=False,
                    )
                )
            return _read_windows_file(
                workspace / ".breadboard" / "artifacts" / "manifests" / manifest_name,
                _MAX_ARTIFACT_MANIFEST_BYTES,
            )
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)

    descriptors = [
        os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    try:
        for component in (".breadboard", "artifacts", "manifests"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    component,
                    create=False,
                )
            )
        return _read_posix_file(
            descriptors[-1],
            manifest_name,
            _MAX_ARTIFACT_MANIFEST_BYTES,
        )
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def authorize_session_artifact_manifest(
    workspace: str | Path,
    session_id: str,
    manifest_name: str,
    *,
    expected_session_directory_identity: SessionDirectoryIdentity | None = None,
) -> None:
    root = _workspace_root(workspace)
    validate_session_id(session_id)
    expected_digest = _manifest_digest_from_name(session_id, manifest_name)
    if (
        expected_session_directory_identity is not None
        and session_directory_identity(root)
        != expected_session_directory_identity
    ):
        raise OSError("durable session directory identity changed")
    with _session_guard(root, session_id, create=False):
        _recover_pending_intents(root, session_id)
        _load_anchored(root, session_id)
        body = _read_workspace_manifest(root, manifest_name)
        if _digest(body) != expected_digest:
            raise ValueError("artifact manifest digest mismatch")
        record = _read_projection_authority(root, session_id)
        if record is None or record["state"] != "committed":
            raise ValueError("missing committed session projection authority")
        manifest = {
            "name": manifest_name,
            "size": len(body),
            "sha256": expected_digest,
        }
        if manifest in record["manifests"]:
            return
        manifests = sorted(
            [*record["manifests"], manifest],
            key=lambda row: row["name"],
        )
        updated = dict(record)
        updated["generation"] += 1
        updated["manifests"] = manifests
        if (
            expected_session_directory_identity is not None
            and session_directory_identity(root)
            != expected_session_directory_identity
        ):
            raise OSError("durable session directory identity changed")
        _write_projection_authority(root, session_id, updated)


def session_artifact_rows(
    workspace: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    root = _workspace_root(workspace)
    validate_session_id(session_id)
    rows: dict[str, dict[str, Any]] = {}
    with _session_guard(root, session_id, create=False):
        _recover_pending_intents(root, session_id)
        _load_anchored(root, session_id)
        record = _read_projection_authority(root, session_id)
        if record is None or record["state"] != "committed":
            raise ValueError("missing committed session projection authority")
        for manifest in record["manifests"]:
            body = _read_workspace_manifest(root, manifest["name"])
            if len(body) != manifest["size"] or _digest(body) != manifest["sha256"]:
                raise ValueError("artifact manifest digest mismatch")
            try:
                document = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("invalid artifact manifest") from error
            if (
                document.get("schema_version") != "bb.artifact_manifest.v1"
                or document.get("session_id") != session_id
                or not isinstance(document.get("artifacts"), list)
            ):
                raise ValueError("invalid artifact manifest")
            for row in document["artifacts"]:
                if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                    raise ValueError("invalid artifact manifest row")
                prior = rows.setdefault(row["name"], row)
                if prior != row:
                    raise ValueError("conflicting artifact manifest rows")
    return [rows[name] for name in sorted(rows)]
