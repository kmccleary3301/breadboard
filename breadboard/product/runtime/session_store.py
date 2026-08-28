from __future__ import annotations

import base64
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

from breadboard.product.runtime.artifacts import AnchoredStorage
from breadboard.product.runtime.events import KernelEvent, ProcessLock, Session


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
        "event_payload",
        "metadata_payload",
        "event_sha256",
        "metadata_sha256",
    }
)
_MAX_TRANSACTION_INTENT_BYTES = 8 * 1024 * 1024
_T = TypeVar("_T")
_LOCAL_SESSION_LOCKS: weakref.WeakValueDictionary[tuple[str, str], threading.RLock] = (
    weakref.WeakValueDictionary()
)
_LOCAL_SESSION_LOCKS_GUARD = threading.Lock()


def _validate_session_id(session_id: str) -> None:
    if (
        not session_id
        or session_id in {".", ".."}
        or Path(session_id).name != session_id
    ):
        raise ValueError("session_id must be a portable identifier")


def _workspace_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve()


def _session_lock_path(workspace: Path, session_id: str) -> Path:
    identity = f"{workspace}\0{session_id}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return session_directory(workspace) / f"session-{digest}"


@contextmanager
def _local_session_guard(
    workspace: str | Path,
    session_id: str,
    *,
    create: bool,
) -> Iterator[Path]:
    root = _workspace_root(workspace)
    _validate_session_id(session_id)
    key = (os.path.normcase(str(root)), session_id)
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


def _intent_bytes(
    session_id: str,
    event_name: str,
    metadata_name: str,
    event_payload: bytes,
    metadata_payload: bytes,
) -> bytes:
    document = {
        "schema_version": _TRANSACTION_SCHEMA,
        "session_id": session_id,
        "event_name": event_name,
        "metadata_name": metadata_name,
        "event_payload": base64.b64encode(event_payload).decode("ascii"),
        "metadata_payload": base64.b64encode(metadata_payload).decode("ascii"),
        "event_sha256": _digest(event_payload),
        "metadata_sha256": _digest(metadata_payload),
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
) -> tuple[bytes, bytes]:
    if len(body) > _MAX_TRANSACTION_INTENT_BYTES:
        raise ValueError("session transaction intent is oversized")
    try:
        document = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid session transaction intent") from error
    if (
        not isinstance(document, dict)
        or set(document) != _TRANSACTION_FIELDS
        or document.get("schema_version") != _TRANSACTION_SCHEMA
        or document.get("session_id") != session_id
        or document.get("event_name") != event_name
        or document.get("metadata_name") != metadata_name
    ):
        raise ValueError("mismatched session transaction intent")
    try:
        event_payload = base64.b64decode(
            document["event_payload"],
            validate=True,
        )
        metadata_payload = base64.b64decode(
            document["metadata_payload"],
            validate=True,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid session transaction intent payload") from error
    if (
        len(event_payload) > _MAX_TRANSACTION_INTENT_BYTES
        or len(metadata_payload) > _MAX_TRANSACTION_INTENT_BYTES
        or document.get("event_sha256") != _digest(event_payload)
        or document.get("metadata_sha256") != _digest(metadata_payload)
    ):
        raise ValueError("session transaction intent digest mismatch")
    _session_from_payloads(
        event_payload,
        metadata_payload,
        session_id=session_id,
    )
    return event_payload, metadata_payload


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


def _recover_intent_posix(
    parent: int,
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
    event_payload, metadata_payload = _decode_intent(
        body,
        session_id=session_id,
        event_name=event_name,
        metadata_name=metadata_name,
    )
    for name in (intent_name, event_name, metadata_name):
        _assert_posix_target(parent, name)
    AnchoredStorage.write_at(parent, event_name, event_payload)
    AnchoredStorage.write_at(parent, metadata_name, metadata_payload)
    os.unlink(intent_name, dir_fd=parent)
    os.fsync(parent)


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


def _recover_intent_windows(
    parent: Path,
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
    event_payload, metadata_payload = _decode_intent(
        body,
        session_id=session_id,
        event_name=event_name,
        metadata_name=metadata_name,
    )
    for name in (intent_name, event_name, metadata_name):
        _assert_windows_target(parent / name)
    _write_windows_atomic(parent / event_name, event_payload)
    _write_windows_atomic(parent / metadata_name, metadata_payload)
    (parent / intent_name).unlink()


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
                    session_id,
                    legacy=False,
                )
            _recover_intent_windows(
                sessions,
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
                session_id,
                legacy=False,
            )
        _recover_intent_posix(
            sessions,
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
) -> Path:
    session_id = session.read_model.session_id
    _validate_session_id(session_id)
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
    intent_payload = _intent_bytes(
        session_id,
        event_name,
        metadata_name,
        event_payload,
        metadata_payload,
    )
    intent_name = _intent_name(session_id, legacy=legacy)

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
            for name in (intent_name, event_name, metadata_name):
                _assert_windows_target(parent / name)
            _write_windows_atomic(parent / intent_name, intent_payload)
            _write_windows_atomic(parent / event_name, event_payload)
            _write_windows_atomic(parent / metadata_name, metadata_payload)
            (parent / intent_name).unlink()
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
        for name in (intent_name, event_name, metadata_name):
            _assert_posix_target(parent, name)
        AnchoredStorage.write_at(parent, intent_name, intent_payload)
        AnchoredStorage.write_at(parent, event_name, event_payload)
        AnchoredStorage.write_at(parent, metadata_name, metadata_payload)
        os.unlink(intent_name, dir_fd=parent)
        os.fsync(parent)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return event_path


def _load_anchored(
    workspace: Path,
    session_id: str,
) -> tuple[Session, Path]:
    _validate_session_id(session_id)
    if os.name == "nt":
        handles = []
        descriptor = None
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
                descriptor = AnchoredStorage.windows_file_descriptor(
                    event_path,
                    create=False,
                )
            except FileNotFoundError:
                event_path = legacy_session_event_path(workspace, session_id)
                descriptor = AnchoredStorage.windows_file_descriptor(
                    event_path,
                    create=False,
                )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                body = stream.read()
        finally:
            if descriptor is not None:
                os.close(descriptor)
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
                body = AnchoredStorage.read_at(
                    session_descriptor,
                    "session_events.jsonl",
                )
                event_path = session_event_path(workspace, session_id)
            except FileNotFoundError:
                body = AnchoredStorage.read_at(
                    descriptors[-1],
                    f"{session_id}.events.jsonl",
                )
                event_path = legacy_session_event_path(workspace, session_id)
        finally:
            if session_descriptor is not None:
                os.close(session_descriptor)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    events = [
        event_from_record(json.loads(line))
        for line in body.decode().splitlines()
        if line.strip()
    ]
    session = Session.restore(events)
    if session.read_model.session_id != session_id:
        raise ValueError("session event identity mismatch")
    return session, event_path


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
) -> tuple[Session, Path]:
    """Atomically claim a new session id and publish its first projection."""
    session_id = session.read_model.session_id
    _validate_session_id(session_id)
    with _session_guard(workspace, session_id, create=True) as root:
        _recover_pending_intents(root, session_id)
        try:
            _load_anchored(root, session_id)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"session already exists: {session_id}")
        published_path = _persist_session_locked(root, session, event_path)
        return session, published_path


def _load_session_locked(
    workspace: Path,
    session_id: str,
) -> tuple[Session, Path]:
    _recover_pending_intents(workspace, session_id)
    return _load_anchored(workspace, session_id)


def _pending_intent_exists(workspace: Path, session_id: str) -> bool:
    candidates = (
        session_event_path(workspace, session_id).parent
        / _intent_name(session_id, legacy=False),
        session_directory(workspace) / _intent_name(session_id, legacy=True),
    )
    for path in candidates:
        try:
            os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        return True
    return False


def load_session(
    workspace: str | Path,
    session_id: str,
) -> tuple[Session, Path]:
    root = _workspace_root(workspace)
    try:
        with _local_session_guard(root, session_id, create=False):
            if _pending_intent_exists(root, session_id):
                with ProcessLock(_session_lock_path(root, session_id)):
                    return _load_session_locked(root, session_id)
            return _load_anchored(root, session_id)
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
            _validate_session_id(candidate)
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
        for name in (".breadboard", "sessions"):
            descriptors.append(
                AnchoredStorage.open_directory(
                    descriptors[-1],
                    name,
                    create=False,
                )
            )
        for name in os.listdir(descriptors[-1]):
            metadata = os.stat(
                name,
                dir_fd=descriptors[-1],
                follow_symlinks=False,
            )
            if stat.S_ISDIR(metadata.st_mode):
                add(name)
            elif stat.S_ISREG(metadata.st_mode) and name.endswith(suffix):
                add(name[: -len(suffix)])
        return names
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def session_artifact_rows(
    workspace: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    prefix = f"{session_id}."
    handles = []
    descriptors = []
    try:
        if os.name == "nt":
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
            except FileNotFoundError:
                return []
            root = workspace / ".breadboard" / "artifacts" / "manifests"
            names = os.listdir(root)

            def read_manifest(name: str) -> bytes:
                descriptor = AnchoredStorage.windows_file_descriptor(
                    root / name,
                    create=False,
                )
                with os.fdopen(descriptor, "rb") as stream:
                    return stream.read()

        else:
            descriptors = [
                os.open(
                    workspace,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            ]
            try:
                for name in (".breadboard", "artifacts", "manifests"):
                    descriptors.append(
                        AnchoredStorage.open_directory(
                            descriptors[-1],
                            name,
                            create=False,
                        )
                    )
            except FileNotFoundError:
                return []
            names = os.listdir(descriptors[-1])

            def read_manifest(name: str) -> bytes:
                return AnchoredStorage.read_at(descriptors[-1], name)

        for name in sorted(names):
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            digest = name[len(prefix) : -5]
            body = read_manifest(name)
            if (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or hashlib.sha256(body).hexdigest() != digest
            ):
                raise ValueError("artifact manifest digest mismatch")
            document = json.loads(body)
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
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        for handle in reversed(handles):
            AnchoredStorage.close_windows_handle(handle)
