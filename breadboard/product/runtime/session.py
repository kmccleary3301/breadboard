"""Product-owned, event-sourced Session lifecycle facade."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from breadboard.product.harness.lock import EffectiveHarnessLock

from .artifacts import ArtifactRef
from .events import KernelEvent, SessionView, rebuild
from .ports import Clock, EventSink, IdSource, NullEventSink, SystemClock, UUIDSource


class Session:
    """Single lifecycle owner; conductor/provider/tool details stay behind adapters."""

    def __init__(self, events: Iterable[KernelEvent], *, clock: Clock | None = None,
                 sink: EventSink | None = None) -> None:
        self._events = list(events)
        self._clock = clock or SystemClock()
        self._sink = sink or NullEventSink()
        self._view = rebuild(self._events)

    @classmethod
    def start(cls, lock: EffectiveHarnessLock, task: str, *,
              session_id: str | None = None, clock: Clock | None = None,
              ids: IdSource | None = None, sink: EventSink | None = None) -> "Session":
        if not isinstance(lock, EffectiveHarnessLock):
            raise TypeError("Session.start requires an EffectiveHarnessLock")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be non-empty")
        active_clock, active_ids = clock or SystemClock(), ids or UUIDSource()
        graph_hash = lock.as_dict().get("graph_hash")
        if not isinstance(graph_hash, str) or not graph_hash.startswith("sha256:"):
            raise ValueError("EffectiveHarnessLock has no canonical graph_hash")
        event = KernelEvent.create(
            session_id or active_ids.new_id(), 1, "session.started", active_clock.now(),
            {"effective_lock_hash": graph_hash,
             "task_hash": "sha256:" + hashlib.sha256(task.encode()).hexdigest()},
        )
        active_sink = sink or NullEventSink()
        active_sink.append(event)
        return cls((event,), clock=active_clock, sink=active_sink)

    @classmethod
    def restore(cls, events: Iterable[KernelEvent], *, clock: Clock | None = None,
                sink: EventSink | None = None) -> "Session":
        return cls(events, clock=clock, sink=sink)

    @property
    def events(self) -> tuple[KernelEvent, ...]:
        return tuple(self._events)

    @property
    def read_model(self) -> SessionView:
        return self._view

    def input(self, content: str, attachments: Iterable[ArtifactRef] = ()) -> SessionView:
        self._require({"running"}, "accept input")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("input must be non-empty")
        refs = [ref.as_dict() for ref in attachments]
        return self._append(
            "input.accepted",
            {
                "content_hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                "attachments": refs,
            },
        )

    def request_approval(self, request_id: str, operation: str) -> SessionView:
        self._require({"running"}, "request approval")
        if not request_id or not operation:
            raise ValueError("approval request fields must be populated")
        return self._append(
            "approval.requested", {"request_id": request_id, "operation": operation}
        )

    def resolve_approval(self, request_id: str, decision: str) -> SessionView:
        self._require({"awaiting_approval"}, "resolve approval")
        if not request_id or decision not in {"allow", "deny", "once", "always", "reject"}:
            raise ValueError("invalid approval decision")
        return self._append(
            "approval.resolved", {"request_id": request_id, "decision": decision}
        )

    def reconfigure(self, lock: EffectiveHarnessLock, reason: str) -> SessionView:
        self._require({"running", "awaiting_approval", "paused"}, "reconfigure")
        if not isinstance(lock, EffectiveHarnessLock):
            raise TypeError("reconfigure requires an EffectiveHarnessLock")
        graph_hash = lock.as_dict().get("graph_hash")
        if not isinstance(graph_hash, str) or not graph_hash.startswith("sha256:"):
            raise ValueError("EffectiveHarnessLock has no canonical graph_hash")
        return self._append(
            "session.reconfigured", {"effective_lock_hash": graph_hash, "reason": reason}
        )

    def pause(self, reason: str) -> SessionView:
        self._require({"running"}, "pause")
        return self._append("session.paused", {"reason": reason})

    def resume(self) -> SessionView:
        self._require({"paused"}, "resume")
        return self._append("session.resumed", {})

    def cancel(self, reason: str = "operator request") -> SessionView:
        self._require({"running", "awaiting_approval", "paused"}, "cancel")
        return self._append("session.canceled", {"outcome": "canceled", "reason": reason})

    def complete(self, summary: str = "completed") -> SessionView:
        self._require({"running"}, "complete")
        return self._append("session.completed", {"outcome": "completed", "summary": summary})

    def fail(self, error_code: str, detail: str) -> SessionView:
        self._require({"running", "awaiting_approval"}, "fail")
        if not error_code or not detail:
            raise ValueError("terminal error fields must be populated")
        return self._append(
            "session.failed",
            {"outcome": "failed", "error": error_code, "detail": detail},
        )

    def _require(self, allowed: set[str], action: str) -> None:
        if self._view.status not in allowed:
            raise RuntimeError(f"cannot {action} while session is {self._view.status}")

    def _append(self, kind: str, payload: dict[str, Any]) -> SessionView:
        event = KernelEvent.create(
            self._view.session_id, len(self._events) + 1, kind, self._clock.now(), payload
        )
        next_events = [*self._events, event]
        next_view = rebuild(next_events)
        self._sink.append(event)
        self._events = next_events
        self._view = next_view
        return next_view
