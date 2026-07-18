"""Immutable events and the deterministic Session read model."""
from __future__ import annotations
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
_EVENT_KINDS = frozenset({
    "session.started", "input.accepted", "approval.requested", "approval.resolved",
    "session.reconfigured", "session.paused", "session.resumed",
    "session.completed", "session.failed", "session.canceled",
})
_ALLOWED = MappingProxyType({
    "input.accepted": ("running",), "approval.requested": ("running",), "approval.resolved": ("awaiting_approval",),
    "session.paused": ("running",), "session.reconfigured": ("running", "awaiting_approval", "paused"),
    "session.resumed": ("paused",), "session.completed": ("running",),
    "session.failed": ("running", "awaiting_approval", "paused"), "session.canceled": ("running", "awaiting_approval", "paused"),
})
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
    json.dumps(value, allow_nan=False)
    return value
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
        payload = _frozen(self.payload); _validate_payload(self.kind, payload)
        object.__setattr__(self, "payload", payload)
    @classmethod
    def create(cls, session_id: str, sequence: int, kind: str, occurred_at: str, payload: Mapping[str, Any]) -> "KernelEvent":
        return cls(session_id, sequence, kind, occurred_at, payload)
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
    start, status, pending, outcome = rows[0], "running", None, None
    lock_hash = start.payload["effective_lock_hash"]
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
