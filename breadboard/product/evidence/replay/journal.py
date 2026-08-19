from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from threading import RLock
from typing import Any

from breadboard.product.evidence.workspace import BreadBoardWorkspace

from .execution import ReplayExecution, ReplayExecutionEvent


def _plan_digest(plan_id: str) -> str:
    prefix, separator, digest = (
        plan_id.partition(":") if isinstance(plan_id, str) else ("", "", "")
    )
    if (
        prefix != "sha256"
        or not separator
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("replay journal requires a canonical plan_id")
    return digest


def _unique_object(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in rows:
        if name in result:
            raise ValueError("replay journal event contains a duplicate JSON key")
        result[name] = value
    return result


def _canonical_event(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_immutable(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        _sync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class _ReplayEventSink:
    def __init__(
        self, workspace: BreadBoardWorkspace, relative_directory: Path
    ) -> None:
        self.workspace = workspace
        self.relative_directory = relative_directory
        self._lock = RLock()
        self._next_sequence = 1

    def append(self, event: object) -> None:
        if not isinstance(event, ReplayExecutionEvent):
            raise TypeError("replay journal accepts ReplayExecutionEvent values")
        with self._lock:
            if event.sequence != self._next_sequence:
                raise ValueError("replay journal event sequence is not contiguous")
            relative = self.relative_directory / f"{event.sequence:08d}.json"
            destination = self.workspace.path(relative)
            _write_immutable(destination, _canonical_event(event.as_dict()))
            self._next_sequence += 1


class ReplayJournal:
    """Workspace-local immutable replay events with durable restart read-back."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = BreadBoardWorkspace(Path(workspace))

    def run_path(self, plan_id: str) -> Path:
        digest = _plan_digest(plan_id)
        return self.workspace.path(Path(".breadboard") / "replays" / digest)

    def event_path(self, plan_id: str) -> Path:
        return self.workspace.path(
            Path(".breadboard") / "replays" / _plan_digest(plan_id) / "events"
        )

    def start(self, plan_id: str) -> _ReplayEventSink:
        run_path = self.run_path(plan_id)
        replay_root = run_path.parent
        replay_root.mkdir(parents=True, exist_ok=True)
        run_path = self.run_path(plan_id)
        os.mkdir(run_path, 0o700)
        events: Path | None = None
        try:
            events = self.event_path(plan_id)
            os.mkdir(events, 0o700)
            _sync_directory(run_path)
            _sync_directory(replay_root)
        except BaseException:
            cleanup_errors: list[OSError] = []
            for path in (events, run_path):
                if path is None:
                    continue
                try:
                    path.rmdir()
                except OSError as error:
                    cleanup_errors.append(error)
            try:
                _sync_directory(replay_root)
            except OSError as error:
                cleanup_errors.append(error)
            if cleanup_errors:
                raise RuntimeError("replay journal startup cleanup failed") from cleanup_errors[0]
            raise
        relative = Path(".breadboard") / "replays" / _plan_digest(plan_id) / "events"
        return _ReplayEventSink(self.workspace, relative)

    def try_read(self, plan_id: str) -> ReplayExecution | None:
        run_path = self.run_path(plan_id)
        try:
            run_metadata = run_path.lstat()
        except FileNotFoundError:
            return None
        if run_path.is_symlink() or not stat.S_ISDIR(run_metadata.st_mode):
            raise ValueError(
                "replay journal run must be a real workspace-local directory"
            )
        events_path = self.event_path(plan_id)
        metadata = events_path.lstat()
        if events_path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                "replay journal events must be a real workspace-local directory"
            )
        sources = sorted(
            path for path in events_path.iterdir() if path.name.endswith(".json")
        )
        rows: list[ReplayExecutionEvent] = []
        for sequence, source in enumerate(sources, 1):
            if source.name != f"{sequence:08d}.json" or source.is_symlink():
                raise ValueError("replay journal event sequence is not canonical")
            descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("replay journal event must be a regular file")
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    payload = stream.read()
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            try:
                value = json.loads(payload, object_pairs_hook=_unique_object)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError("replay journal event is invalid JSON") from error
            if (
                not isinstance(value, dict)
                or _canonical_event(value) != payload
                or set(value)
                != {
                    "sequence",
                    "state",
                    "kind",
                    "span_id",
                    "parent_span_id",
                    "details",
                }
            ):
                raise ValueError("replay journal event has an invalid envelope")
            if not isinstance(value["details"], dict):
                raise TypeError("replay journal event details must be an object")
            rows.append(
                ReplayExecutionEvent(
                    value["sequence"],
                    value["state"],
                    value["kind"],
                    value["span_id"],
                    value["parent_span_id"],
                    value["details"],
                )
            )
        execution = ReplayExecution.from_events(rows)
        if execution.plan_id != plan_id:
            raise ValueError("replay journal plan identity does not match its path")
        return execution

    def read(self, plan_id: str) -> ReplayExecution:
        execution = self.try_read(plan_id)
        if execution is None:
            raise FileNotFoundError(f"no durable replay execution for {plan_id}")
        return execution
