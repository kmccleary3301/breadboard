from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from breadboard.product.runtime.artifacts import AnchoredStorage
from breadboard.product.runtime.events import KernelEvent, Session


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


def persist_session(
    workspace: Path,
    session: Session,
    event_path: Path | None = None,
) -> None:
    session_id = session.read_model.session_id
    legacy = event_path == legacy_session_event_path(workspace, session_id)
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
            parent = session_directory(workspace)
            if not legacy:
                parent = session_event_path(workspace, session_id).parent
                handles.append(
                    AnchoredStorage.windows_handle(
                        parent,
                        directory=True,
                        create=False,
                    )
                )
            _write_windows_file(
                parent
                / (f"{session_id}.events.jsonl" if legacy else "session_events.jsonl"),
                _event_bytes(session),
            )
            _write_windows_file(
                parent / (f"{session_id}.json" if legacy else "session.json"),
                _metadata_bytes(session),
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
                    create=False,
                )
            )
        parent = descriptors[-1]
        if not legacy:
            descriptors.append(
                AnchoredStorage.open_directory(
                    parent,
                    session_id,
                    create=False,
                )
            )
            parent = descriptors[-1]
        AnchoredStorage.write_at(
            parent,
            f"{session_id}.events.jsonl" if legacy else "session_events.jsonl",
            _event_bytes(session),
        )
        AnchoredStorage.write_at(
            parent,
            f"{session_id}.json" if legacy else "session.json",
            _metadata_bytes(session),
        )
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _load_anchored(
    workspace: Path,
    session_id: str,
) -> tuple[Session, Path]:
    if (
        not session_id
        or session_id in {".", ".."}
        or Path(session_id).name != session_id
    ):
        raise ValueError("session_id must be a portable identifier")
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
            except OSError:
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
    return Session.restore(events), event_path


def load_session(
    workspace: str | Path,
    session_id: str,
) -> tuple[Session, Path]:
    root = Path(workspace).expanduser().resolve()
    try:
        return _load_anchored(root, session_id)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise FileNotFoundError(f"session not found: {session_id}") from error


def session_names(workspace: Path) -> list[str]:
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
            return os.listdir(session_directory(workspace))
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
        return os.listdir(descriptors[-1])
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
