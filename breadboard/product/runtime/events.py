"""Immutable events and the deterministic Session read model."""
from __future__ import annotations
import base64, hashlib, json, os, re
from collections.abc import Callable, Iterable, Mapping; from dataclasses import dataclass; from datetime import datetime, timezone; from pathlib import Path; from threading import RLock; from types import MappingProxyType; from typing import Any, Protocol; from uuid import uuid4
from breadboard.product.harness.lock import EffectiveHarnessLock
from .artifacts import ArtifactRef
from breadboard.product.projection import Projected, ProjectionSource
def _sync(stream: Any) -> None: stream.flush(); os.fsync(stream.fileno())
class ProcessLock:
    def __init__(self, path: Path) -> None: self.stream = os.fdopen(os.open(path.with_name(f".{path.name}.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600), "a+b", buffering=0)
    def __enter__(self) -> "ProcessLock":
        if os.name == "nt": import msvcrt; self.stream.seek(0, os.SEEK_END); self.stream.write(b"\0") if not self.stream.tell() else None; self.stream.seek(0); msvcrt.locking(self.stream.fileno(), msvcrt.LK_LOCK, 1); self.unlock = lambda: (self.stream.seek(0), msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1))
        else: import fcntl; fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX); self.unlock = lambda: fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        return self
    def __exit__(self, *_: object) -> None: self.unlock(); self.stream.close()
class Clock(Protocol):
    def now(self) -> str: ...
class IdSource(Protocol):
    def new_id(self) -> str: ...
class EventSink(Protocol):
    def append(self, event: object) -> None: ...
class SystemClock:
    def now(self) -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
class UUIDSource:
    def new_id(self) -> str: return str(uuid4())
class _SinkState:
    def __init__(self) -> None: self.lock, self.poisoned = RLock(), set()
_STATES = tuple(_SinkState() for _ in range(256))
class JsonlEventSink:
    def __init__(self, path: str | Path, *, max_bytes: int | None = None) -> None:
        self.path = Path(path).resolve(); self._max_bytes = max_bytes; path, state = self.path, _STATES[hash(self.path) % len(_STATES)]
        with state.lock: self._mkdir_parent(path.parent)
        with state.lock, ProcessLock(path): self._recover(path, state)
    @classmethod
    def _for_existing_path(
        cls,
        path: str | Path,
        *,
        max_bytes: int | None = None,
    ) -> "JsonlEventSink":
        sink = cls.__new__(cls)
        sink.path = Path(path).resolve()
        sink._max_bytes = max_bytes
        return sink
    def _mkdir_parent(self, path: Path) -> None:
        if path.exists(): return
        self._mkdir_parent(path.parent)
        try: path.mkdir()
        except FileExistsError: return
        try: self._sync_parent(path)
        except BaseException: path.rmdir(); raise
    def _transaction_paths(self, path: Path) -> tuple[Path, Path]: wal = path.with_name(f".{path.name}.txn"); return wal, wal.with_name(f"{wal.name}.tmp")
    def _recover(self, path: Path, state: _SinkState) -> None:
        wal, temporary = self._transaction_paths(path); temporary.unlink(missing_ok=True)
        try:
            if not wal.exists(): state.poisoned.discard(path); return
            offset = int(wal.read_text(encoding="ascii"))
            if path.exists():
                with path.open("r+b", buffering=0) as stream:
                    stream.seek(0, os.SEEK_END); size = stream.tell()
                    if offset < 0 or offset > size: raise ValueError("invalid event transaction offset")
                    stream.truncate(offset); _sync(stream)
            elif offset: raise ValueError("event transaction references a missing log")
            wal.unlink(); self._sync_parent(path)
            state.poisoned.discard(path)
        except BaseException: state.poisoned.add(path); raise RuntimeError("event sink recovery failed")
    def _begin(self, path: Path, offset: int) -> None:
        wal, temporary = self._transaction_paths(path); data = str(offset).encode("ascii"); descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if os.write(descriptor, data) != len(data): raise OSError("short event transaction write")
            os.fsync(descriptor)
        finally: os.close(descriptor)
        os.replace(temporary, wal); self._sync_parent(path)
    def _finish(self, path: Path) -> None:
        wal, temporary = self._transaction_paths(path); temporary.unlink(missing_ok=True); wal.unlink(missing_ok=True); self._sync_parent(path)
    def _append_body(self, event: object, path: Path, state: _SinkState) -> None:
        payload = (json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()  # type: ignore[attr-defined]
        if path in state.poisoned: raise RuntimeError("event sink is poisoned after an unconfirmed rollback")
        self._recover(path, state)
        try: stream = path.open("x+b", buffering=0); created = True
        except FileExistsError: stream = path.open("a+b", buffering=0); created = False
        try:
            stream.seek(0, os.SEEK_END); offset = stream.tell()
            if self._max_bytes is not None and offset + len(payload) > self._max_bytes: raise RuntimeError("event journal exceeds byte limit")
            try:
                self._begin(path, offset)
                if stream.write(payload) != len(payload): raise OSError("short event sink write")
                _sync(stream); self._finish(path)
            except BaseException:
                try:
                    stream.seek(offset); stream.truncate(); _sync(stream); self._finish(path)
                    if created: stream.close(); path.unlink(); self._sync_parent(path)
                except BaseException: state.poisoned.add(path)
                raise
        finally:
            try: stream.close()
            except OSError: pass
    def append(self, event: object) -> None:
        path = Path(self.path).resolve(); state = _STATES[hash(path) % len(_STATES)]  # type: ignore[attr-defined]
        with state.lock, ProcessLock(path): self._append_body(event, path, state)
    def _append_with_process_lock(self, event: object) -> None:
        path = Path(self.path).resolve(); state = _STATES[hash(path) % len(_STATES)]  # type: ignore[attr-defined]
        with state.lock: self._append_body(event, path, state)
    def _sync_parent(self, path: Path) -> None:
        if os.name == "nt": return
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
class NullEventSink:
    def append(self, event: object) -> None: return None
_OBSERVATION_EVENT_KINDS = frozenset({"assistant_message", "tool_call", "tool_result"})
_EVENT_KINDS = frozenset({"session.started", "input.accepted", "annotation", "context.compacted", "approval.requested", "approval.resolved", "session.reconfigured", "session.paused", "session.resumed", "session.completed", "session.failed", "session.canceled"}) | _OBSERVATION_EVENT_KINDS
_ALLOWED = MappingProxyType({"input.accepted": ("running",), "assistant_message": ("running",), "tool_call": ("running",), "tool_result": ("running",), "annotation": ("running", "awaiting_approval", "paused", "completed", "failed", "canceled"), "context.compacted": ("running",), "approval.requested": ("running",), "approval.resolved": ("awaiting_approval",), "session.paused": ("running",), "session.reconfigured": ("running", "awaiting_approval", "paused"), "session.resumed": ("paused",), "session.completed": ("running",), "session.failed": ("running", "awaiting_approval", "paused"), "session.canceled": ("running", "awaiting_approval", "paused")})
_STATUSES, _DECISIONS = frozenset({"running", "awaiting_approval", "paused", "completed", "failed", "canceled"}), frozenset({"allow", "deny", "once", "always", "reject"})
_TERMINAL = {"session.completed": ("completed", ("summary",)), "session.failed": ("failed", ("error", "detail")), "session.canceled": ("canceled", ("reason",))}
def _string(value: Any, name: str, populated: bool = True) -> str:
    if type(value) is not str or populated and not value: raise ValueError(f"{name} must be a{' non-empty' if populated else ''} string")
    return value
def _sha256(value: Any, name: str) -> str:
    if len(value := _string(value, name)) != 71 or not value.startswith("sha256:") or any(c not in "0123456789abcdef" for c in value[7:]): raise ValueError(f"{name} must be an exact lowercase sha256 hash")
    return value
def _validate_annotation_payload(payload: Mapping[str, Any]) -> None:
    required = {"annotation_id", "message_id", "trajectory_id", "label", "author", "generation"}
    if set(payload) != required:
        raise ValueError("annotation payload must contain only stable annotation fields")
    for name in required:
        _string(payload.get(name), name)
_RAW_FACT_ID = re.compile(r"ctn_[0-9]{6,}")
def validate_raw_fact_ids(values: Any, name: str = "raw_fact_ids") -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    facts = tuple(values)
    if any(
        type(value) is not str or _RAW_FACT_ID.fullmatch(value) is None
        for value in facts
    ):
        raise ValueError(f"{name} must contain canonical C-Tree identities")
    if len(set(facts)) != len(facts):
        raise ValueError(f"{name} must not contain duplicates")
    return facts

def _validate_effective_context(context: bytes) -> None:
    try:
        messages = json.loads(context.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("effective_context must be UTF-8 JSON") from error
    if not isinstance(messages, list) or any(
        not isinstance(message, dict) for message in messages
    ):
        raise ValueError(
            "effective_context must be a JSON array of message objects"
        )


def _decode_compaction_context(payload: Mapping[str, Any]) -> bytes:
    if payload.get("context_encoding") != "base64":
        raise ValueError("compaction context_encoding must be base64")
    encoded = _string(payload.get("effective_context"), "effective_context", False)
    try:
        context = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("effective_context must be canonical base64") from error
    if base64.b64encode(context).decode("ascii") != encoded:
        raise ValueError("effective_context must be canonical base64")
    if _hash_bytes(context) != payload.get("context_sha256"):
        raise ValueError("compaction context_sha256 does not match effective_context")
    _validate_effective_context(context)
    return context
def _validate_compaction_payload(payload: Mapping[str, Any]) -> None:
    required = {
        "compaction_index",
        "source_sequence_start",
        "source_sequence_end",
        "context_encoding",
        "effective_context",
        "context_sha256",
        "raw_fact_ids",
        "shadowed_raw_fact_ids",
    }
    if set(payload) != required:
        raise ValueError("context.compacted payload must contain only stable compaction fields")
    for name in ("compaction_index", "source_sequence_start", "source_sequence_end"):
        value = payload.get(name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if payload["source_sequence_start"] > payload["source_sequence_end"]:
        raise ValueError("compaction source range must be ordered")
    raw_fact_ids = validate_raw_fact_ids(payload.get("raw_fact_ids"))
    shadowed_raw_fact_ids = validate_raw_fact_ids(
        payload.get("shadowed_raw_fact_ids"),
        "shadowed_raw_fact_ids",
    )
    if not set(shadowed_raw_fact_ids).issubset(raw_fact_ids):
        raise ValueError("shadowed_raw_fact_ids must cite retained raw facts")
    _sha256(payload.get("context_sha256"), "context_sha256")
    _decode_compaction_context(payload)
def _validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    if kind == "session.started": _sha256(payload.get("effective_lock_hash"), "effective_lock_hash"); _sha256(payload.get("task_hash"), "task_hash")
    elif kind == "assistant_message":
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping) or set(metadata) != {"has_content"} or type(metadata.get("has_content")) is not bool:
            raise ValueError("assistant_message payload must contain boolean metadata.has_content")
        identity = set(payload) - {"metadata"}
        if identity not in (set(), {"message_id", "trajectory_id"}):
            raise ValueError("assistant_message identity must contain message_id and trajectory_id")
        if identity:
            _string(payload.get("message_id"), "message_id")
            _string(payload.get("trajectory_id"), "trajectory_id")
    elif kind == "tool_call":
        if set(payload) != {"tool"}: raise ValueError("tool_call payload must contain only tool")
        _string(payload.get("tool"), "tool")
    elif kind == "tool_result":
        if set(payload) != {"tool", "error"} or type(payload.get("error")) is not bool: raise ValueError("tool_result payload must contain tool and one boolean error field")
        _string(payload.get("tool"), "tool")
    elif kind == "annotation":
        _validate_annotation_payload(payload)
    elif kind == "context.compacted":
        _validate_compaction_payload(payload)
    elif kind == "input.accepted":
        _sha256(payload.get("content_hash"), "content_hash"); attachments = payload.get("attachments")
        if not isinstance(attachments, (list, tuple)): raise ValueError("attachments must be an array")
        for ref in attachments:
            if not isinstance(ref, Mapping) or set(ref) != {"digest", "size_bytes", "media_type"}: raise ValueError("attachments must contain artifact references")
            _sha256(ref.get("digest"), "digest"); _string(ref.get("media_type"), "media_type")
            if type(ref.get("size_bytes")) is not int or ref["size_bytes"] < 0: raise ValueError("size_bytes must be a nonnegative integer")
    elif kind == "approval.requested": _string(payload.get("request_id"), "request_id"); _string(payload.get("operation"), "operation")
    elif kind == "approval.resolved":
        _string(payload.get("request_id"), "request_id"); decision = _string(payload.get("decision"), "decision")
        if decision not in _DECISIONS: raise ValueError("invalid approval decision")
    elif kind == "session.reconfigured": _sha256(payload.get("effective_lock_hash"), "effective_lock_hash"); _string(payload.get("reason"), "reason", False)
    elif kind == "session.paused": _string(payload.get("reason"), "reason", False)
    elif kind == "session.resumed" and payload: raise ValueError("session.resumed payload must be empty")
    elif kind in _TERMINAL:
        outcome, fields = _TERMINAL[kind]
        if _string(payload.get("outcome"), "outcome") != outcome: raise ValueError(f"{kind} outcome does not match its kind")
        for field in fields: _string(payload.get(field), field, kind == "session.failed")
def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value): raise TypeError("mapping keys must be strings")
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)): return tuple(_frozen(item) for item in value)
    json.dumps(value, allow_nan=False); return value
def _plain(value: Any) -> Any:
    if isinstance(value, Mapping): return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple): return [_plain(item) for item in value]
    return value
@dataclass(frozen=True, slots=True)
class CompactionSnapshot:
    """Persistence-owner bytes and cumulative raw-fact identities at one boundary."""

    effective_context: bytes
    raw_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.effective_context) is not bytes:
            raise TypeError("effective_context must be bytes")
        _validate_effective_context(self.effective_context)
        facts = validate_raw_fact_ids(self.raw_fact_ids)
        object.__setattr__(self, "raw_fact_ids", facts)
@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """Stable, immutable label metadata for one canonical message target."""

    annotation_id: str
    message_id: str
    trajectory_id: str
    label: str
    author: str
    generation: str

    def __post_init__(self) -> None:
        for name in ("annotation_id", "message_id", "trajectory_id", "label", "author", "generation"):
            _string(getattr(self, name), name)

    def as_dict(self) -> dict[str, str]:
        return {
            "annotation_id": self.annotation_id,
            "message_id": self.message_id,
            "trajectory_id": self.trajectory_id,
            "label": self.label,
            "author": self.author,
            "generation": self.generation,
        }
@dataclass(frozen=True, slots=True)
class KernelEvent:
    session_id: str; sequence: int; kind: str; occurred_at: str; payload: Mapping[str, Any]; schema_version: str = "bb.session_event.v1"
    def __post_init__(self) -> None:
        if self.schema_version != "bb.session_event.v1": raise ValueError("unsupported session event schema_version")
        if any(type(value) is not str or not value for value in (self.session_id, self.kind, self.occurred_at)): raise ValueError("session event identity fields must be non-empty strings")
        if self.kind not in _EVENT_KINDS: raise ValueError("unsupported session event kind")
        if type(self.sequence) is not int or self.sequence < 1: raise ValueError("session event sequence must be a positive integer")
        if not isinstance(self.payload, Mapping): raise TypeError("session event payload must be a mapping")
        payload = _frozen(self.payload); _validate_payload(self.kind, payload); object.__setattr__(self, "payload", payload)
    @classmethod
    def create(cls, session_id: str, sequence: int, kind: str, occurred_at: str, payload: Mapping[str, Any]) -> "KernelEvent": return cls(session_id, sequence, kind, occurred_at, payload)
    def as_dict(self) -> dict[str, Any]: return {"schema_version": self.schema_version, "session_id": self.session_id, "sequence": self.sequence, "kind": self.kind, "occurred_at": self.occurred_at, "payload": _plain(self.payload)}
@dataclass(frozen=True, slots=True)
class CompactionEvent:
    """Decoded durable compaction boundary returned by Session.compact."""

    session_id: str
    sequence: int
    compaction_index: int
    source_sequence_start: int
    source_sequence_end: int
    effective_context: bytes
    raw_fact_ids: tuple[str, ...]
    shadowed_raw_fact_ids: tuple[str, ...]

def _compaction_event(event: KernelEvent) -> CompactionEvent:
    if event.kind != "context.compacted":
        raise ValueError("compaction event requires context.compacted")
    return CompactionEvent(
        session_id=event.session_id,
        sequence=event.sequence,
        compaction_index=event.payload["compaction_index"],
        source_sequence_start=event.payload["source_sequence_start"],
        source_sequence_end=event.payload["source_sequence_end"],
        effective_context=_decode_compaction_context(event.payload),
        raw_fact_ids=tuple(event.payload["raw_fact_ids"]),
        shadowed_raw_fact_ids=tuple(event.payload["shadowed_raw_fact_ids"]),
    )
@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str; status: str; effective_lock_hash: str; task_hash: str; event_count: int
    pending_approval: str | None = None; terminal_outcome: Mapping[str, Any] | None = None
    def __post_init__(self) -> None:
        _string(self.session_id, "session_id"); _sha256(self.effective_lock_hash, "effective_lock_hash"); _sha256(self.task_hash, "task_hash")
        if type(self.status) is not str or self.status not in _STATUSES: raise ValueError("invalid session status")
        if type(self.event_count) is not int or self.event_count < 1: raise ValueError("event_count must be a positive integer")
        if self.status != "running" and self.event_count == 1: raise ValueError("non-running sessions require at least two events")
        if self.status == "awaiting_approval": _string(self.pending_approval, "pending_approval")
        elif self.pending_approval is not None: raise ValueError("pending_approval requires awaiting_approval status")
        if self.status in {"completed", "failed", "canceled"}:
            if not isinstance(self.terminal_outcome, Mapping): raise ValueError("terminal_outcome must match terminal status")
            terminal = _frozen(self.terminal_outcome); _validate_payload(f"session.{self.status}", terminal); object.__setattr__(self, "terminal_outcome", terminal)
        elif self.terminal_outcome is not None: raise ValueError("terminal_outcome requires terminal status")
    def as_dict(self) -> dict[str, Any]: return {"schema_version": "bb.session.v1", "session_id": self.session_id, "status": self.status, "effective_lock_hash": self.effective_lock_hash, "task_hash": self.task_hash, "event_count": self.event_count, "pending_approval": self.pending_approval, "terminal_outcome": _plain(self.terminal_outcome)}
def rebuild(events: Iterable[KernelEvent]) -> SessionView:
    rows = tuple(events)
    if not rows or rows[0].kind != "session.started":
        raise ValueError("event stream must begin with session.started")
    start, status, pending, outcome = rows[0], "running", None, None
    lock_hash = start.payload["effective_lock_hash"]
    message_targets: dict[str, str] = {}
    annotation_ids: set[str] = set()
    compaction_count = 0
    last_compaction_sequence: int | None = None
    retained_raw_fact_order: tuple[str, ...] = ()
    last_compaction_context_hash: str | None = None
    for expected, event in enumerate(rows, 1):
        if event.session_id != start.session_id or event.sequence != expected:
            raise ValueError("event stream is not contiguous for one session")
        if expected == 1:
            continue
        if status not in _ALLOWED.get(event.kind, ()):
            raise ValueError(f"invalid {event.kind} transition from {status}")
        if event.kind == "assistant_message" and "message_id" in event.payload:
            message_id = event.payload["message_id"]
            trajectory_id = event.payload["trajectory_id"]
            if message_id in message_targets:
                raise ValueError("duplicate canonical message identity")
            message_targets[message_id] = trajectory_id
        elif event.kind == "annotation":
            annotation_id = event.payload["annotation_id"]
            if annotation_id in annotation_ids:
                raise ValueError("duplicate annotation_id in event stream")
            annotation_ids.add(annotation_id)
            if message_targets.get(event.payload["message_id"]) != event.payload["trajectory_id"]:
                raise ValueError("annotation target is not registered for this session")
        elif event.kind == "context.compacted":
            expected_start = last_compaction_sequence or 1
            if event.payload["compaction_index"] != compaction_count + 1:
                raise ValueError("compaction indexes must be contiguous")
            if (
                event.payload["source_sequence_start"] != expected_start
                or event.payload["source_sequence_end"] != event.sequence - 1
            ):
                raise ValueError("compaction source range does not match durable event order")
            current_raw_fact_order = tuple(event.payload["raw_fact_ids"])
            if (
                current_raw_fact_order[: len(retained_raw_fact_order)]
                != retained_raw_fact_order
            ):
                raise ValueError("compaction cannot reorder or discard retained raw facts")
            expected_shadowed = (
                retained_raw_fact_order
                if last_compaction_context_hash is not None
                and event.payload["context_sha256"]
                != last_compaction_context_hash
                else ()
            )
            if tuple(event.payload["shadowed_raw_fact_ids"]) != expected_shadowed:
                raise ValueError(
                    "compaction shadow chain does not cite the replaced surface"
                )
            compaction_count += 1
            last_compaction_sequence = event.sequence
            retained_raw_fact_order = current_raw_fact_order
            last_compaction_context_hash = event.payload["context_sha256"]
        if event.kind == "approval.requested":
            pending, status = event.payload["request_id"], "awaiting_approval"
        elif event.kind == "approval.resolved":
            if pending != event.payload["request_id"]:
                raise ValueError("approval does not match the pending request")
            pending, status = None, "running"
        elif event.kind == "session.reconfigured":
            lock_hash = event.payload["effective_lock_hash"]
        elif event.kind == "session.paused":
            status = "paused"
        elif event.kind == "session.resumed":
            status = "running"
        elif event.kind.startswith("session."):
            pending, status, outcome = None, event.kind.removeprefix("session."), event.payload
    return SessionView(start.session_id, status, lock_hash, start.payload["task_hash"], len(rows), pending, outcome)
SESSION_PROJECTOR_VERSION = "bb.session.projector.v1"
class SessionProjectionError(ValueError):
    """A Session projection request cannot be satisfied."""
class SessionProjectionAsOfError(SessionProjectionError):
    """A requested Session source sequence is outside the stream."""
    def __init__(self, as_of: int, available: int) -> None:
        super().__init__(f"Session as_of {as_of!r} is outside source range 1..{available}")
        self.as_of, self.available = as_of, available
class SessionProjectionVersionError(SessionProjectionError):
    """A caller requested a projector version this owner does not provide."""
    def __init__(self, expected: str) -> None:
        super().__init__(f"unsupported Session projector version {expected!r}")
        self.expected = expected
def _session_projection_limit(rows: tuple[KernelEvent, ...], as_of: int | None) -> int:
    if not rows:
        raise ValueError("event stream must begin with session.started")
    limit = len(rows) if as_of is None else as_of
    if type(limit) is not int or limit < 1 or limit > len(rows):
        raise SessionProjectionAsOfError(limit, len(rows))
    return limit
def _check_session_projection_version(expected: str | None) -> None:
    if expected is not None and expected != SESSION_PROJECTOR_VERSION:
        raise SessionProjectionVersionError(expected)
def project_session_replay(events: Iterable[KernelEvent], *, as_of: int | None = None, expected_projector_version: str | None = None) -> Projected[SessionView]:
    _check_session_projection_version(expected_projector_version)
    rows = tuple(events); limit = _session_projection_limit(rows, as_of); value = rebuild(rows[:limit])
    return Projected(value, SESSION_PROJECTOR_VERSION, ProjectionSource(f"session:{value.session_id}", 1, limit), limit)
def project_session(events: Iterable[KernelEvent], *, as_of: int | None = None, expected_projector_version: str | None = None) -> Projected[SessionView]:
    return project_session_replay(events, as_of=as_of, expected_projector_version=expected_projector_version)
def project_session_snapshot(view: SessionView, *, as_of: int | None = None, expected_projector_version: str | None = None) -> Projected[SessionView]:
    _check_session_projection_version(expected_projector_version)
    if not isinstance(view, SessionView):
        raise TypeError("Session snapshot projection requires a SessionView")
    if as_of is not None and (type(as_of) is not int or as_of != view.event_count):
        raise SessionProjectionAsOfError(as_of, view.event_count)
    return Projected(view, SESSION_PROJECTOR_VERSION, ProjectionSource(f"session:{view.session_id}", 1, view.event_count), view.event_count)
_SESSION_ACTIONS = MappingProxyType({"accept input": ("running",), "observe assistant": ("running",), "observe tool call": ("running",), "observe tool result": ("running",), "annotate": ("running", "awaiting_approval", "paused", "completed", "failed", "canceled"), "compact": ("running",), "request approval": ("running",), "resolve approval": ("awaiting_approval",), "reconfigure": ("running", "awaiting_approval", "paused"), "pause": ("running",), "resume": ("paused",), "cancel": ("running", "awaiting_approval", "paused"), "complete": ("running",), "fail": ("running", "awaiting_approval", "paused")})
def _graph_hash(lock: EffectiveHarnessLock) -> str:
    return _sha256(lock.as_dict().get("graph_hash"), "graph_hash")
def _hash(value: str) -> str: return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
def _hash_bytes(value: bytes) -> str: return "sha256:" + hashlib.sha256(value).hexdigest()
class ReplayError(ValueError):
    """A durable event stream cannot be rebuilt into a valid Session."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _check(condition: bool, error: type[Exception], message: str) -> None:
    if not condition: raise error(message)
class GenerationAdoptionError(RuntimeError):
    """Typed refusal for an invalid or non-quiescent generation adoption."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _assistant_payload(
    content: str,
    message_id: str | None,
    trajectory_id: str | None,
) -> dict[str, Any]:
    _check(type(content) is str, TypeError, "assistant content must be a string")
    if (message_id is None) != (trajectory_id is None):
        raise ValueError("assistant message identity requires message_id and trajectory_id")
    payload: dict[str, Any] = {"metadata": {"has_content": bool(content)}}
    if message_id is not None and trajectory_id is not None:
        _string(message_id, "message_id")
        _string(trajectory_id, "trajectory_id")
        payload.update(message_id=message_id, trajectory_id=trajectory_id)
    return payload
class Session:
    """Lifecycle owner; adapters may add only validated, minimal runtime observations."""
    def __init__(self, events: Iterable[KernelEvent], *, clock: Clock | None = None, sink: EventSink | None = None, task: str | None = None) -> None:
        if task is not None and (not isinstance(task, str) or not task.strip()): raise ValueError("task must be non-empty when retained")
        self._task = task
        self._transition_lock = RLock()
        self._appending = False
        self._events = list(events)
        self._clock = clock if clock is not None else SystemClock()
        self._sink = sink if sink is not None else NullEventSink()
        self._terminal_annotation_commit: Callable[[AnnotationRecord], tuple[KernelEvent, ...]] | None = None
        self._view = rebuild(self._events)
        compaction = next(
            (row for row in reversed(self._events) if row.kind == "context.compacted"),
            None,
        )
        self._effective_context = (
            None if compaction is None else _decode_compaction_context(compaction.payload)
        )
        self._raw_fact_ids = (
            () if compaction is None else tuple(compaction.payload["raw_fact_ids"])
        )
    @classmethod
    def start(cls, lock: EffectiveHarnessLock, task: str, *, session_id: str | None = None, clock: Clock | None = None, ids: IdSource | None = None, sink: EventSink | None = None) -> "Session":
        if not isinstance(lock, EffectiveHarnessLock): raise TypeError("Session.start requires an EffectiveHarnessLock")
        if not isinstance(task, str) or not task.strip(): raise ValueError("task must be non-empty")
        active_clock, active_ids = clock if clock is not None else SystemClock(), ids if ids is not None else UUIDSource(); graph_hash, active_session_id = _graph_hash(lock), session_id if session_id is not None else active_ids.new_id()
        event = KernelEvent.create(active_session_id, 1, "session.started", active_clock.now(), {"effective_lock_hash": graph_hash, "task_hash": _hash(task)}); active_sink = sink if sink is not None else NullEventSink(); active_sink.append(event)
        return cls((event,), clock=active_clock, sink=active_sink, task=task)
    @classmethod
    def restore(cls, events: Iterable[KernelEvent], *, clock: Clock | None = None, sink: EventSink | None = None, task: str | None = None) -> "Session":
        try:
            return cls(events, clock=clock, sink=sink, task=task)
        except (AttributeError, TypeError, ValueError) as error:
            raise ReplayError("invalid_event_stream", str(error)) from error
    @property
    def events(self) -> tuple[KernelEvent, ...]:
        with self._transition_lock: return tuple(self._events)
    @property
    def task(self) -> str | None:
        return self._task
    @property
    def read_model(self) -> SessionView:
        with self._transition_lock: return self._view
    @property
    def pinned_generation_id(self) -> str:
        """The immutable Lock identity pinned by this Session."""
        with self._transition_lock:
            return self._view.effective_lock_hash
    @property
    def generation_sequence(self) -> tuple[str, ...]:
        """Ordered Lock identities that have governed this Session."""
        with self._transition_lock:
            return tuple(
                event.payload["effective_lock_hash"]
                for event in self._events
                if event.kind in {"session.started", "session.reconfigured"}
            )
    @property
    def trajectory_segments(self) -> tuple[Mapping[str, Any], ...]:
        with self._transition_lock:
            session_id = self._view.session_id
            boundaries = tuple(
                event
                for event in self._events
                if event.kind in {"session.started", "session.reconfigured"}
            )
            return tuple(
                MappingProxyType(
                    {
                        "segment_id": f"{session_id}:segment:{index}:{boundary.payload['effective_lock_hash'].removeprefix('sha256:')}",
                        "segment_index": index,
                        "generation_id": boundary.payload["effective_lock_hash"],
                        "start_sequence": boundary.sequence,
                    }
                )
                for index, boundary in enumerate(boundaries)
            )
    @property
    def adoption_history(self) -> tuple[Mapping[str, Any], ...]:
        with self._transition_lock:
            session_id = self._view.session_id
            prior = None
            history = []
            for event in self._events:
                if event.kind not in {"session.started", "session.reconfigured"}:
                    continue
                generation = event.payload["effective_lock_hash"]
                if event.kind == "session.reconfigured":
                    history.append(
                        MappingProxyType(
                            {
                                "old_generation_id": prior,
                                "new_generation_id": generation,
                                "reason": event.payload["reason"],
                                "effective_sequence": event.sequence,
                                "trajectory_segment_id": f"{session_id}:segment:{len(history) + 1}:{generation.removeprefix('sha256:')}",
                            }
                        )
                    )
                prior = generation
            return tuple(history)
    @property
    def effective_context(self) -> bytes | None:
        with self._transition_lock:
            return self._effective_context
    @property
    def raw_fact_ids(self) -> tuple[str, ...]:
        with self._transition_lock:
            return self._raw_fact_ids
    def projected_read_model(self, *, as_of: int | None = None, expected_projector_version: str | None = None) -> Projected[SessionView]:
        return project_session_live(self, as_of=as_of, expected_projector_version=expected_projector_version)
    def compact(self, snapshot: CompactionSnapshot) -> CompactionEvent:
        if not isinstance(snapshot, CompactionSnapshot):
            raise TypeError("compact requires a CompactionSnapshot")
        with self._transition_lock:
            event, _ = self._append_event(
                "compact",
                "context.compacted",
                lambda: self._compaction_payload(snapshot),
            )
            self._effective_context = snapshot.effective_context
            self._raw_fact_ids = snapshot.raw_fact_ids
            return _compaction_event(event)
    def _compaction_payload(self, snapshot: CompactionSnapshot) -> dict[str, Any]:
        previous = next(
            (row for row in reversed(self._events) if row.kind == "context.compacted"),
            None,
        )
        if (
            snapshot.raw_fact_ids[: len(self.raw_fact_ids)]
            != self.raw_fact_ids
        ):
            raise ValueError("compaction cannot reorder or discard retained raw facts")
        context_sha256 = _hash_bytes(snapshot.effective_context)
        shadowed_raw_fact_ids = (
            list(previous.payload["raw_fact_ids"])
            if previous is not None
            and previous.payload["context_sha256"] != context_sha256
            else []
        )
        return {
            "compaction_index": 1 if previous is None else previous.payload["compaction_index"] + 1,
            "source_sequence_start": 1 if previous is None else previous.sequence,
            "source_sequence_end": len(self._events),
            "context_encoding": "base64",
            "effective_context": base64.b64encode(snapshot.effective_context).decode("ascii"),
            "context_sha256": context_sha256,
            "raw_fact_ids": list(snapshot.raw_fact_ids),
            "shadowed_raw_fact_ids": shadowed_raw_fact_ids,
        }
    def input(self, content: str, attachments: Iterable[ArtifactRef] = ()) -> SessionView: return self._append("accept input", "input.accepted", lambda: (_check(isinstance(content, str) and bool(content.strip()), ValueError, "input must be non-empty"), {"content_hash": _hash(content), "attachments": [ref.as_dict() for ref in attachments]})[1])
    def input_digest(
        self, content_hash: str, attachments: Iterable[ArtifactRef] = ()
    ) -> SessionView:
        """Append an accepted input when only its retained content hash is available."""
        return self._append(
            "accept input",
            "input.accepted",
            lambda: (
                _sha256(content_hash, "content_hash"),
                {
                    "content_hash": content_hash,
                    "attachments": [ref.as_dict() for ref in attachments],
                },
            )[1],
        )
    def assistant_message(self, content: str, *, message_id: str | None = None, trajectory_id: str | None = None) -> SessionView: return self._append("observe assistant", "assistant_message", lambda: self._assistant_event_payload(content, message_id, trajectory_id))
    def _assistant_event_payload(self, content: str, message_id: str | None, trajectory_id: str | None) -> dict[str, Any]:
        payload = _assistant_payload(content, message_id, trajectory_id)
        if message_id is not None and any(
            event.kind == "assistant_message"
            and event.payload.get("message_id") == message_id
            for event in self._events
        ):
            raise ValueError("duplicate canonical message identity")
        return payload
    def tool_called(self, tool: str) -> SessionView: return self._append("observe tool call", "tool_call", lambda: (_check(type(tool) is str and bool(tool), ValueError, "tool name must be a non-empty string"), {"tool": tool})[1])
    def annotate(self, record: AnnotationRecord) -> SessionView:
        with self._transition_lock:
            if self._view.status not in {"completed", "failed", "canceled"} or self._terminal_annotation_commit is None:
                return self._append("annotate", "annotation", lambda: self._annotation_payload(record))
            self._require("annotate")
            self._annotation_payload(record)
            self._appending = True
            try:
                events = tuple(self._terminal_annotation_commit(record))
                if (
                    len(events) <= len(self._events)
                    or events[: len(self._events)] != tuple(self._events)
                    or events[-1].kind != "annotation"
                    or events[-1].payload != record.as_dict()
                ):
                    raise RuntimeError("durable annotation commit returned inconsistent events")
                view = rebuild(events)
                self._events, self._view = list(events), view
                return view
            finally:
                self._appending = False
    def _bind_terminal_annotation_commit(self, commit: Callable[[AnnotationRecord], tuple[KernelEvent, ...]]) -> None:
        if not callable(commit):
            raise TypeError("terminal annotation commit must be callable")
        with self._transition_lock:
            self._terminal_annotation_commit = commit
    def tool_completed(self, tool: str, failed: bool) -> SessionView: return self._append("observe tool result", "tool_result", lambda: (_check(type(tool) is str and bool(tool), ValueError, "tool name must be a non-empty string"), _check(type(failed) is bool, TypeError, "tool completion error flag must be boolean"), {"tool": tool, "error": failed})[2])
    def request_approval(self, request_id: str, operation: str) -> SessionView: return self._append("request approval", "approval.requested", lambda: (_check(bool(request_id and operation), ValueError, "approval request fields must be populated"), {"request_id": request_id, "operation": operation})[1])
    def resolve_approval(self, request_id: str, decision: str) -> SessionView: return self._append("resolve approval", "approval.resolved", lambda: (_check(bool(request_id and decision in _DECISIONS), ValueError, "invalid approval decision"), {"request_id": request_id, "decision": decision})[1])
    def adopt_generation(self, lock: EffectiveHarnessLock, reason: str) -> SessionView:
        if not isinstance(lock, EffectiveHarnessLock):
            raise GenerationAdoptionError("incompatible", "generation must be an EffectiveHarnessLock")
        if not isinstance(reason, str):
            raise GenerationAdoptionError("incompatible", "adoption reason must be a string")
        try:
            generation_id = _graph_hash(lock)
        except (TypeError, ValueError) as error:
            raise GenerationAdoptionError(
                "incompatible", "generation Lock has no canonical identity"
            ) from error
        return self._append(
            "reconfigure",
            "session.reconfigured",
            lambda: {"effective_lock_hash": generation_id, "reason": reason},
        )
    def reconfigure(self, lock: EffectiveHarnessLock, reason: str) -> SessionView: return self.adopt_generation(lock, reason)
    def pause(self, reason: str) -> SessionView: return self._append("pause", "session.paused", lambda: {"reason": reason})
    def resume(self) -> SessionView: return self._append("resume", "session.resumed", lambda: {})
    def cancel(self, reason: str = "operator request") -> SessionView: return self._append("cancel", "session.canceled", lambda: {"outcome": "canceled", "reason": reason})
    def complete(self, summary: str = "completed") -> SessionView: return self._append("complete", "session.completed", lambda: {"outcome": "completed", "summary": summary})
    def fail(self, error_code: str, detail: str) -> SessionView: return self._append("fail", "session.failed", lambda: (_check(bool(error_code and detail), ValueError, "terminal error fields must be populated"), {"outcome": "failed", "error": error_code, "detail": detail})[1])
    def _annotation_payload(self, record: AnnotationRecord) -> dict[str, str]:
        if not isinstance(record, AnnotationRecord):
            raise TypeError("annotation requires an AnnotationRecord")
        message_targets = {
            event.payload["message_id"]: event.payload["trajectory_id"]
            for event in self._events
            if event.kind == "assistant_message" and "message_id" in event.payload
        }
        annotation_ids = {
            event.payload["annotation_id"]
            for event in self._events
            if event.kind == "annotation"
        }
        if record.annotation_id in annotation_ids:
            raise ValueError("duplicate annotation_id")
        if message_targets.get(record.message_id) != record.trajectory_id:
            raise ValueError("annotation target is not registered for this session")
        return record.as_dict()
    def _require(self, action: str) -> None:
        if self._appending: raise RuntimeError("cannot mutate session while an append is in progress")
        if self._view.status not in _SESSION_ACTIONS[action]: raise RuntimeError(f"cannot {action} while session is {self._view.status}")
    def _append_event(self, action: str, kind: str, payload: Callable[[], dict[str, Any]]) -> tuple[KernelEvent, SessionView]:
        with self._transition_lock:
            self._require(action); self._appending = True
            try:
                body = payload(); event = KernelEvent.create(self._view.session_id, len(self._events) + 1, kind, self._clock.now(), body); next_events = [*self._events, event]; next_view = rebuild(next_events); self._sink.append(event)
                self._events, self._view = next_events, next_view; return event, next_view
            finally: self._appending = False
    def _append(self, action: str, kind: str, payload: Callable[[], dict[str, Any]]) -> SessionView:
        return self._append_event(action, kind, payload)[1]
def replay_differential(session: Session) -> dict[str, Any]:
    """Compare live compaction reconstruction with a fresh durable replay."""
    if not isinstance(session, Session):
        raise TypeError("replay_differential requires a Session")
    restored = Session.restore(session.events)
    difference: dict[str, Any] = {}
    if restored.effective_context != session.effective_context:
        difference["effective_context"] = {
            "live": None if session.effective_context is None else _hash_bytes(session.effective_context),
            "replay": None if restored.effective_context is None else _hash_bytes(restored.effective_context),
        }
    live_facts = tuple(session.raw_fact_ids)
    replay_facts = tuple(restored.raw_fact_ids)
    if live_facts != replay_facts:
        difference["raw_fact_ids"] = {
            "live": list(live_facts),
            "replay": list(replay_facts),
        }
    return difference
def project_session_live(session: Session, *, as_of: int | None = None, expected_projector_version: str | None = None) -> Projected[SessionView]:
    if not isinstance(session, Session):
        raise TypeError("live Session projection requires a Session")
    return project_session_replay(session.events, as_of=as_of, expected_projector_version=expected_projector_version)
