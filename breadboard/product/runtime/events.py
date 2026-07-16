"""Immutable events and the deterministic Session read model."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _frozen(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    json.dumps(value, allow_nan=False)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class KernelEvent:
    session_id: str
    sequence: int
    kind: str
    occurred_at: str
    payload: Mapping[str, Any]
    schema_version: str = "bb.session_event.v1"

    @classmethod
    def create(
        cls,
        session_id: str,
        sequence: int,
        kind: str,
        occurred_at: str,
        payload: Mapping[str, Any],
    ) -> "KernelEvent":
        if not session_id or sequence < 1 or not kind or not occurred_at:
            raise ValueError("session event identity fields must be populated")
        return cls(session_id, sequence, kind, occurred_at, _frozen(payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "payload": _plain(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str
    status: str
    effective_lock_hash: str
    task_hash: str
    event_count: int
    pending_approval: str | None = None
    terminal_outcome: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        record = {
            "schema_version": "bb.session.v1",
            "session_id": self.session_id,
            "status": self.status,
            "effective_lock_hash": self.effective_lock_hash,
            "task_hash": self.task_hash,
            "event_count": self.event_count,
            "pending_approval": self.pending_approval,
            "terminal_outcome": _plain(self.terminal_outcome),
        }
        return record


def rebuild(events: Iterable[KernelEvent]) -> SessionView:
    rows = tuple(events)
    if not rows or rows[0].kind != "session.started":
        raise ValueError("event stream must begin with session.started")
    session_id = rows[0].session_id
    status, pending, outcome = "running", None, None
    lock_hash = str(rows[0].payload["effective_lock_hash"])
    for expected, event in enumerate(rows, 1):
        if event.session_id != session_id or event.sequence != expected:
            raise ValueError("event stream is not contiguous for one session")
        if expected == 1:
            continue
        allowed = {
            "input.accepted": {"running"},
            "approval.requested": {"running"},
            "approval.resolved": {"awaiting_approval"},
            "session.paused": {"running"},
            "session.reconfigured": {"running", "awaiting_approval", "paused"},
            "session.resumed": {"paused"},
            "session.completed": {"running"},
            "session.failed": {"running", "awaiting_approval"},
            "session.canceled": {"running", "awaiting_approval", "paused"},
        }
        if event.kind not in allowed or status not in allowed[event.kind]:
            raise ValueError(f"invalid {event.kind} transition from {status}")
        if event.kind == "approval.requested":
            pending, status = str(event.payload["request_id"]), "awaiting_approval"
        elif event.kind == "approval.resolved":
            if pending != str(event.payload["request_id"]):
                raise ValueError("approval does not match the pending request")
            pending, status = None, "running"
        elif event.kind == "session.reconfigured":
            lock_hash = str(event.payload["effective_lock_hash"])
        elif event.kind == "session.paused":
            status = "paused"
        elif event.kind == "session.resumed":
            status = "running"
        elif event.kind in {"session.completed", "session.failed", "session.canceled"}:
            status, outcome = event.kind.removeprefix("session."), event.payload
    start = rows[0].payload
    return SessionView(
        session_id,
        status,
        lock_hash,
        str(start["task_hash"]),
        len(rows),
        pending,
        _frozen(outcome) if outcome else None,
    )
