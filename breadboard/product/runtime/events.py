"""Immutable events and the deterministic Session read model."""
from __future__ import annotations
import hashlib, json, os
from collections.abc import Callable, Iterable, Mapping; from dataclasses import dataclass; from datetime import datetime, timezone; from pathlib import Path; from threading import RLock; from types import MappingProxyType; from typing import Any, Protocol; from uuid import uuid4
from breadboard.product.harness.lock import EffectiveHarnessLock
from .artifacts import ArtifactRef
def _sync(stream: Any) -> None: stream.flush(); os.fsync(stream.fileno())
class _ProcessLock:
    def __init__(self, path: Path) -> None: self.stream = os.fdopen(os.open(path.with_name(f".{path.name}.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600), "a+b", buffering=0)
    def __enter__(self) -> "_ProcessLock":
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
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve(); path, state = self.path, _STATES[hash(self.path) % len(_STATES)]
        with state.lock: self._mkdir_parent(path.parent)
        with state.lock, _ProcessLock(path): self._recover(path, state)
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
    def append(self, event: object) -> None:
        payload = (json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode(); path = Path(self.path).resolve(); state = _STATES[hash(path) % len(_STATES)]  # type: ignore[attr-defined]
        with state.lock, _ProcessLock(path):
            if path in state.poisoned: raise RuntimeError("event sink is poisoned after an unconfirmed rollback")
            self._recover(path, state)
            try: stream = path.open("x+b", buffering=0); created = True
            except FileExistsError: stream = path.open("a+b", buffering=0); created = False
            try:
                stream.seek(0, os.SEEK_END); offset = stream.tell()
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
    def _sync_parent(self, path: Path) -> None:
        if os.name == "nt": return
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
class NullEventSink:
    def append(self, event: object) -> None: return None
_EVENT_KINDS = frozenset({"session.started", "input.accepted", "approval.requested", "approval.resolved", "session.reconfigured", "session.paused", "session.resumed", "session.completed", "session.failed", "session.canceled"})
_ALLOWED = MappingProxyType({"input.accepted": ("running",), "approval.requested": ("running",), "approval.resolved": ("awaiting_approval",), "session.paused": ("running",), "session.reconfigured": ("running", "awaiting_approval", "paused"), "session.resumed": ("paused",), "session.completed": ("running",), "session.failed": ("running", "awaiting_approval", "paused"), "session.canceled": ("running", "awaiting_approval", "paused")})
_STATUSES, _DECISIONS = frozenset({"running", "awaiting_approval", "paused", "completed", "failed", "canceled"}), frozenset({"allow", "deny", "once", "always", "reject"})
_TERMINAL = {"session.completed": ("completed", ("summary",)), "session.failed": ("failed", ("error", "detail")), "session.canceled": ("canceled", ("reason",))}
def _string(value: Any, name: str, populated: bool = True) -> str:
    if type(value) is not str or populated and not value: raise ValueError(f"{name} must be a{' non-empty' if populated else ''} string")
    return value
def _sha256(value: Any, name: str) -> str:
    if len(value := _string(value, name)) != 71 or not value.startswith("sha256:") or any(c not in "0123456789abcdef" for c in value[7:]): raise ValueError(f"{name} must be an exact lowercase sha256 hash")
    return value
def _validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    if kind == "session.started": _sha256(payload.get("effective_lock_hash"), "effective_lock_hash"); _sha256(payload.get("task_hash"), "task_hash")
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
    if not rows or rows[0].kind != "session.started": raise ValueError("event stream must begin with session.started")
    start, status, pending, outcome = rows[0], "running", None, None; lock_hash = start.payload["effective_lock_hash"]
    for expected, event in enumerate(rows, 1):
        if event.session_id != start.session_id or event.sequence != expected: raise ValueError("event stream is not contiguous for one session")
        if expected == 1: continue
        if status not in _ALLOWED.get(event.kind, ()): raise ValueError(f"invalid {event.kind} transition from {status}")
        if event.kind == "approval.requested": pending, status = event.payload["request_id"], "awaiting_approval"
        elif event.kind == "approval.resolved":
            if pending != event.payload["request_id"]: raise ValueError("approval does not match the pending request")
            pending, status = None, "running"
        elif event.kind == "session.reconfigured": lock_hash = event.payload["effective_lock_hash"]
        elif event.kind == "session.paused": status = "paused"
        elif event.kind == "session.resumed": status = "running"
        elif event.kind.startswith("session."): pending, status, outcome = None, event.kind.removeprefix("session."), event.payload
    return SessionView(start.session_id, status, lock_hash, start.payload["task_hash"], len(rows), pending, outcome)
_SESSION_ACTIONS = MappingProxyType({"accept input": ("running",), "request approval": ("running",), "resolve approval": ("awaiting_approval",), "reconfigure": ("running", "awaiting_approval", "paused"), "pause": ("running",), "resume": ("paused",), "cancel": ("running", "awaiting_approval", "paused"), "complete": ("running",), "fail": ("running", "awaiting_approval", "paused")})
def _graph_hash(lock: EffectiveHarnessLock) -> str:
    graph_hash = lock.as_dict().get("graph_hash")
    if not isinstance(graph_hash, str) or not graph_hash.startswith("sha256:"): raise ValueError("EffectiveHarnessLock has no canonical graph_hash")
    return graph_hash
def _hash(value: str) -> str: return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
def _check(condition: bool, error: type[Exception], message: str) -> None:
    if not condition: raise error(message)
class Session:
    """Single lifecycle owner; conductor/provider/tool details stay behind adapters."""
    def __init__(self, events: Iterable[KernelEvent], *, clock: Clock | None = None, sink: EventSink | None = None) -> None:
        self._transition_lock = RLock(); self._appending = False; self._events = list(events); self._clock = clock if clock is not None else SystemClock(); self._sink = sink if sink is not None else NullEventSink(); self._view = rebuild(self._events)
    @classmethod
    def start(cls, lock: EffectiveHarnessLock, task: str, *, session_id: str | None = None, clock: Clock | None = None, ids: IdSource | None = None, sink: EventSink | None = None) -> "Session":
        if not isinstance(lock, EffectiveHarnessLock): raise TypeError("Session.start requires an EffectiveHarnessLock")
        if not isinstance(task, str) or not task.strip(): raise ValueError("task must be non-empty")
        active_clock, active_ids = clock if clock is not None else SystemClock(), ids if ids is not None else UUIDSource(); graph_hash, active_session_id = _graph_hash(lock), session_id if session_id is not None else active_ids.new_id()
        event = KernelEvent.create(active_session_id, 1, "session.started", active_clock.now(), {"effective_lock_hash": graph_hash, "task_hash": _hash(task)}); active_sink = sink if sink is not None else NullEventSink(); active_sink.append(event)
        return cls((event,), clock=active_clock, sink=active_sink)
    @classmethod
    def restore(cls, events: Iterable[KernelEvent], *, clock: Clock | None = None, sink: EventSink | None = None) -> "Session": return cls(events, clock=clock, sink=sink)
    @property
    def events(self) -> tuple[KernelEvent, ...]:
        with self._transition_lock: return tuple(self._events)
    @property
    def read_model(self) -> SessionView:
        with self._transition_lock: return self._view
    def input(self, content: str, attachments: Iterable[ArtifactRef] = ()) -> SessionView: return self._append("accept input", "input.accepted", lambda: (_check(isinstance(content, str) and bool(content.strip()), ValueError, "input must be non-empty"), {"content_hash": _hash(content), "attachments": [ref.as_dict() for ref in attachments]})[1])
    def request_approval(self, request_id: str, operation: str) -> SessionView: return self._append("request approval", "approval.requested", lambda: (_check(bool(request_id and operation), ValueError, "approval request fields must be populated"), {"request_id": request_id, "operation": operation})[1])
    def resolve_approval(self, request_id: str, decision: str) -> SessionView: return self._append("resolve approval", "approval.resolved", lambda: (_check(bool(request_id and decision in _DECISIONS), ValueError, "invalid approval decision"), {"request_id": request_id, "decision": decision})[1])
    def reconfigure(self, lock: EffectiveHarnessLock, reason: str) -> SessionView: return self._append("reconfigure", "session.reconfigured", lambda: (_check(isinstance(lock, EffectiveHarnessLock), TypeError, "reconfigure requires an EffectiveHarnessLock"), {"effective_lock_hash": _graph_hash(lock), "reason": reason})[1])
    def pause(self, reason: str) -> SessionView: return self._append("pause", "session.paused", lambda: {"reason": reason})
    def resume(self) -> SessionView: return self._append("resume", "session.resumed", lambda: {})
    def cancel(self, reason: str = "operator request") -> SessionView: return self._append("cancel", "session.canceled", lambda: {"outcome": "canceled", "reason": reason})
    def complete(self, summary: str = "completed") -> SessionView: return self._append("complete", "session.completed", lambda: {"outcome": "completed", "summary": summary})
    def fail(self, error_code: str, detail: str) -> SessionView: return self._append("fail", "session.failed", lambda: (_check(bool(error_code and detail), ValueError, "terminal error fields must be populated"), {"outcome": "failed", "error": error_code, "detail": detail})[1])
    def _require(self, action: str) -> None:
        if self._appending: raise RuntimeError("cannot mutate session while an append is in progress")
        if self._view.status not in _SESSION_ACTIONS[action]: raise RuntimeError(f"cannot {action} while session is {self._view.status}")
    def _append(self, action: str, kind: str, payload: Callable[[], dict[str, Any]]) -> SessionView:
        with self._transition_lock:
            self._require(action); self._appending = True
            try:
                body = payload(); event = KernelEvent.create(self._view.session_id, len(self._events) + 1, kind, self._clock.now(), body); next_events = [*self._events, event]; next_view = rebuild(next_events); self._sink.append(event)
                self._events, self._view = next_events, next_view; return next_view
            finally: self._appending = False
