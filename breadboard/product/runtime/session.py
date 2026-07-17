"""Product-owned, event-sourced Session lifecycle facade."""
from __future__ import annotations
import hashlib
from threading import RLock
from collections.abc import Callable, Iterable
from types import MappingProxyType
from typing import Any
from breadboard.product.harness.lock import EffectiveHarnessLock
from .artifacts import ArtifactRef
from .events import KernelEvent, SessionView, rebuild
from .ports import Clock, EventSink, IdSource, NullEventSink, SystemClock, UUIDSource
_ALLOWED = MappingProxyType({
    "accept input": ("running",), "request approval": ("running",), "resolve approval": ("awaiting_approval",),
    "reconfigure": ("running", "awaiting_approval", "paused"), "pause": ("running",), "resume": ("paused",),
    "cancel": ("running", "awaiting_approval", "paused"), "complete": ("running",),
    "fail": ("running", "awaiting_approval", "paused"),
})
_DECISIONS = frozenset({"allow", "deny", "once", "always", "reject"})
def _graph_hash(lock: EffectiveHarnessLock) -> str:
    graph_hash = lock.as_dict().get("graph_hash")
    if not isinstance(graph_hash, str) or not graph_hash.startswith("sha256:"): raise ValueError("EffectiveHarnessLock has no canonical graph_hash")
    return graph_hash
def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
def _check(condition: bool, error: type[Exception], message: str) -> None:
    if not condition: raise error(message)
class Session:
    """Single lifecycle owner; conductor/provider/tool details stay behind adapters."""
    def __init__(self, events: Iterable[KernelEvent], *, clock: Clock | None = None, sink: EventSink | None = None) -> None:
        self._transition_lock = RLock()
        self._appending = False; self._events = list(events)
        self._clock = clock if clock is not None else SystemClock(); self._sink = sink if sink is not None else NullEventSink()
        self._view = rebuild(self._events)
    @classmethod
    def start(cls, lock: EffectiveHarnessLock, task: str, *, session_id: str | None = None, clock: Clock | None = None, ids: IdSource | None = None, sink: EventSink | None = None) -> "Session":
        if not isinstance(lock, EffectiveHarnessLock): raise TypeError("Session.start requires an EffectiveHarnessLock")
        if not isinstance(task, str) or not task.strip(): raise ValueError("task must be non-empty")
        active_clock = clock if clock is not None else SystemClock()
        active_ids = ids if ids is not None else UUIDSource()
        graph_hash = _graph_hash(lock)
        active_session_id = session_id if session_id is not None else active_ids.new_id()
        event = KernelEvent.create(active_session_id, 1, "session.started", active_clock.now(),
                                   {"effective_lock_hash": graph_hash, "task_hash": _hash(task)})
        active_sink = sink if sink is not None else NullEventSink()
        active_sink.append(event)
        return cls((event,), clock=active_clock, sink=active_sink)
    @classmethod
    def restore(cls, events: Iterable[KernelEvent], *, clock: Clock | None = None, sink: EventSink | None = None) -> "Session":
        return cls(events, clock=clock, sink=sink)
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
        if self._view.status not in _ALLOWED[action]: raise RuntimeError(f"cannot {action} while session is {self._view.status}")
    def _append(self, action: str, kind: str, payload: Callable[[], dict[str, Any]]) -> SessionView:
        with self._transition_lock:
            self._require(action); self._appending = True
            try:
                body = payload()
                event = KernelEvent.create(self._view.session_id, len(self._events) + 1, kind, self._clock.now(), body)
                next_events = [*self._events, event]; next_view = rebuild(next_events); self._sink.append(event)
                self._events, self._view = next_events, next_view; return next_view
            finally: self._appending = False
