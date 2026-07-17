"""Event-sourced Work Item lifecycle and policy owner."""
from __future__ import annotations
import json
from datetime import datetime
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType
from typing import Any
from breadboard.product.runtime.ports import Clock, IdSource, SystemClock, UUIDSource
from .placement import WorkPlacement
STATUSES = frozenset({"blocked", "ready", "leased", "running", "waiting", "paused", "completed", "failed", "canceled"}); TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled"})
_RULES = MappingProxyType({
    "dependency.added": (("blocked", "ready"), {"dependency_ref"}, None), "dependency.satisfied": (("blocked",), {"dependency_ref"}, None),
    "child.delegated": (("running",), {"attempt_id", "child_work_item_id"}, "active"), "lease.acquired": (("ready",), {"lease_id", "worker_id", "expires_at"}, None),
    "lease.released": (("leased",), {"lease_id"}, None), "lease.expired": (("leased",), {"lease_id"}, None), "attempt.started": (("leased",), {"attempt_id", "lease_id", "session_ref"}, None),
    "placement.attached": (("running",), {"placement"}, None), "budget.consumed": (("running",), {"attempt_id", "tokens", "cost_microusd", "wall_time_ms"}, "active"),
    "checkpoint.recorded": (("running",), {"attempt_id", "checkpoint_ref"}, "active"), "work.waiting": (("running",), {"attempt_id", "reason", "wake_refs"}, "active"),
    "work.woken": (("waiting",), {"attempt_id", "wake_ref"}, "latest"), "work.paused": (("running",), {"attempt_id", "reason"}, "active"),
    "work.resumed": (("paused",), {"approved", "attempt_id"}, "latest"), "attempt.failed": (("running",), {"attempt_id", "reason", "retryable", "will_retry"}, "active"), "attempt.expired": (("running",), {"attempt_id", "lease_id", "will_retry"}, "active"),
    "work.completed": (("running",), {"attempt_id", "summary"}, "active"),
    "work.canceled": (("blocked", "ready", "leased", "running", "waiting", "paused"), {"actor_id", "reason", "cleanup", "child_work_item_ids"}, None),
})
def _required(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip(): raise ValueError(f"{name} must be a non-empty string")
    return value
def _optional(value: Any, name: str) -> str | None:
    return None if value is None else _required(value, name)
def _natural(value: Any, name: str) -> int:
    if type(value) is not int or value < 0: raise ValueError(f"{name} must be a non-negative integer")
    return value
def _positive(value: Any, name: str) -> int:
    if type(value) is not int or value < 1: raise ValueError(f"{name} must be a positive integer")
    return value
def _strings(values: Any, name: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)): raise TypeError(f"{name} must be an array")
    rows = tuple(_required(value, name) for value in values)
    if nonempty and not rows: raise ValueError(f"{name} must not be empty")
    if len(rows) != len(set(rows)): raise ValueError(f"{name} must contain unique values")
    return rows
def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value): raise TypeError("event payload keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)): return tuple(_freeze(item) for item in value)
    json.dumps(value, allow_nan=False); return value
def _plain(value: Any) -> Any:
    if isinstance(value, Mapping): return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple): return [_plain(item) for item in value]
    return value
def _keys(payload: Mapping[str, Any], expected: set[str], kind: str) -> None:
    if set(payload) != expected: raise ValueError(f"{kind} payload must contain exactly {sorted(expected)}")
def _reject(condition: bool, message: str, error: type[Exception] = ValueError) -> None:
    if condition: raise error(message)
def _timestamp(value: Any, name: str) -> datetime:
    text = _required(value, name)
    try: parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    _reject(parsed.tzinfo is None or parsed.utcoffset() is None, f"{name} must include a UTC offset")
    return parsed
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1; retry_on_any_failure: bool = False; retryable_reasons: tuple[str, ...] = ()
    def __post_init__(self) -> None:
        _positive(self.max_attempts, "max_attempts")
        if type(self.retry_on_any_failure) is not bool: raise TypeError("retry_on_any_failure must be boolean")
        object.__setattr__(self, "retryable_reasons", _strings(self.retryable_reasons, "retryable_reasons"))
    def allows(self, reason: str) -> bool: return self.retry_on_any_failure or reason in self.retryable_reasons
    def as_dict(self) -> dict[str, Any]: return {"max_attempts": self.max_attempts, "retry_on_any_failure": self.retry_on_any_failure, "retryable_reasons": list(self.retryable_reasons)}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RetryPolicy":
        _keys(value, {"max_attempts", "retry_on_any_failure", "retryable_reasons"}, "retry_policy")
        return cls(value["max_attempts"], value["retry_on_any_failure"], value["retryable_reasons"])
@dataclass(frozen=True, slots=True)
class ResumePolicy:
    mode: str = "never"; requires_approval: bool = False
    def __post_init__(self) -> None:
        if self.mode not in {"never", "restart", "checkpoint"}: raise ValueError("resume mode must be never, restart, or checkpoint")
        if type(self.requires_approval) is not bool: raise TypeError("requires_approval must be boolean")
    def as_dict(self) -> dict[str, Any]: return {"mode": self.mode, "requires_approval": self.requires_approval}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResumePolicy":
        _keys(value, {"mode", "requires_approval"}, "resume_policy"); return cls(value["mode"], value["requires_approval"])
@dataclass(frozen=True, slots=True)
class CancellationPolicy:
    mode: str = "cooperative"; cancellable_by: tuple[str, ...] = ("operator",)
    propagate_to_children: bool = True; cleanup: str = "discard_outputs"
    def __post_init__(self) -> None:
        if self.mode not in {"never", "cooperative", "immediate"}: raise ValueError("cancellation mode must be never, cooperative, or immediate")
        object.__setattr__(self, "cancellable_by", _strings(self.cancellable_by, "cancellable_by", nonempty=self.mode != "never"))
        if type(self.propagate_to_children) is not bool: raise TypeError("propagate_to_children must be boolean")
        if self.cleanup not in {"discard_outputs", "keep_partial_outputs", "checkpoint_then_stop"}: raise ValueError("invalid cancellation cleanup")
    def as_dict(self) -> dict[str, Any]: return {"mode": self.mode, "cancellable_by": list(self.cancellable_by), "propagate_to_children": self.propagate_to_children, "cleanup": self.cleanup}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CancellationPolicy":
        _keys(value, {"mode", "cancellable_by", "propagate_to_children", "cleanup"}, "cancellation_policy")
        return cls(value["mode"], value["cancellable_by"], value["propagate_to_children"], value["cleanup"])
@dataclass(frozen=True, slots=True)
class Budget:
    token_limit: int | None = None; cost_limit_microusd: int | None = None; wall_time_limit_ms: int | None = None
    def __post_init__(self) -> None:
        for name in ("token_limit", "cost_limit_microusd", "wall_time_limit_ms"):
            if (value := getattr(self, name)) is not None: _natural(value, name)
    def as_dict(self) -> dict[str, int | None]: return {"token_limit": self.token_limit, "cost_limit_microusd": self.cost_limit_microusd, "wall_time_limit_ms": self.wall_time_limit_ms}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Budget":
        _keys(value, {"token_limit", "cost_limit_microusd", "wall_time_limit_ms"}, "budget")
        return cls(value["token_limit"], value["cost_limit_microusd"], value["wall_time_limit_ms"])
@dataclass(frozen=True, slots=True)
class BudgetUsage:
    tokens: int = 0; cost_microusd: int = 0; wall_time_ms: int = 0
    def __post_init__(self) -> None:
        for name in ("tokens", "cost_microusd", "wall_time_ms"): _natural(getattr(self, name), name)
    def plus(self, tokens: int, cost_microusd: int, wall_time_ms: int) -> "BudgetUsage": return BudgetUsage(self.tokens + tokens, self.cost_microusd + cost_microusd, self.wall_time_ms + wall_time_ms)
@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str; worker_id: str; acquired_at: str; expires_at: str | None = None
    def __post_init__(self) -> None:
        _required(self.lease_id, "lease_id"); _required(self.worker_id, "worker_id")
        acquired = _timestamp(self.acquired_at, "acquired_at")
        _reject(self.expires_at is not None and _timestamp(self.expires_at, "expires_at") <= acquired, "expires_at must be later than acquired_at")
    def _expired_by(self, occurred_at: str) -> bool:
        return self.expires_at is not None and _timestamp(occurred_at, "occurred_at") >= _timestamp(self.expires_at, "expires_at")
@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str; number: int; session_ref: str; worker_id: str; status: str; started_at: str
    ended_at: str | None = None; reason: str | None = None; checkpoint_ref: str | None = None
    def __post_init__(self) -> None:
        _required(self.attempt_id, "attempt_id"); _positive(self.number, "attempt number"); _required(self.session_ref, "session_ref"); _required(self.worker_id, "worker_id"); _required(self.started_at, "started_at")
        if self.status not in {"running", "waiting", "paused", "failed", "completed", "canceled"}: raise ValueError("invalid attempt status")
        _optional(self.ended_at, "ended_at"); _optional(self.reason, "reason"); _optional(self.checkpoint_ref, "checkpoint_ref")
        if self.status == "running" and self.ended_at is not None: raise ValueError("running attempt cannot have ended_at")
        if self.status != "running" and self.ended_at is None: raise ValueError("closed attempt requires ended_at")
@dataclass(frozen=True, slots=True)
class WorkItemEvent:
    work_item_id: str; sequence: int; kind: str; occurred_at: str; payload: Mapping[str, Any]
    def __post_init__(self) -> None:
        _required(self.work_item_id, "work_item_id"); _positive(self.sequence, "sequence"); _required(self.occurred_at, "occurred_at")
        if self.kind != "work_item.created" and self.kind not in _RULES: raise ValueError("unsupported Work Item event kind")
        if not isinstance(self.payload, Mapping): raise TypeError("event payload must be a mapping")
        object.__setattr__(self, "payload", _freeze(self.payload))
    def as_dict(self) -> dict[str, Any]: return {"work_item_id": self.work_item_id, "sequence": self.sequence, "kind": self.kind, "occurred_at": self.occurred_at, "payload": _plain(self.payload)}
class WorkItemRepository:
    def __init__(self) -> None: self._lock, self._streams = RLock(), {}
    def read(self, work_item_id: str) -> tuple[WorkItemEvent, ...]:
        with self._lock: return tuple(self._streams.get(_required(work_item_id, "work_item_id"), ()))
    def append(self, expected_revisions: Mapping[str, int], events: Iterable[WorkItemEvent]) -> None:
        grouped: dict[str, list[WorkItemEvent]] = {}
        for event in events: grouped.setdefault(event.work_item_id, []).append(event)
        if not grouped: raise ValueError("append requires at least one event")
        with self._lock:
            for work_item_id, rows in grouped.items():
                current = self._streams.get(work_item_id, [])
                if expected_revisions.get(work_item_id) != len(current): raise RuntimeError(f"stale Work Item revision for {work_item_id}")
                if any(row.sequence != len(current) + offset for offset, row in enumerate(rows, 1)): raise ValueError("appended event sequence is not contiguous")
                rebuild_work_item((*current, *rows))
            for work_item_id, rows in grouped.items(): self._streams.setdefault(work_item_id, []).extend(rows)
@dataclass(frozen=True, slots=True)
class WorkItemSnapshot:
    work_item_id: str; title: str; status: str; parent_work_item_id: str | None
    child_work_item_ids: tuple[str, ...]; dependency_refs: tuple[str, ...]; satisfied_dependency_refs: tuple[str, ...]; wake_refs: tuple[str, ...]
    retry_policy: RetryPolicy; resume_policy: ResumePolicy; cancellation_policy: CancellationPolicy
    budget: Budget; budget_usage: BudgetUsage; active_lease: Lease | None
    used_lease_ids: tuple[str, ...]; attempts: tuple[Attempt, ...]; placements: tuple[WorkPlacement, ...]
    latest_checkpoint_ref: str | None; terminal_reason: str | None; event_count: int
    @property
    def current_attempt(self) -> Attempt | None: return self.attempts[-1] if self.attempts and self.attempts[-1].status == "running" else None
def _budget_allows(limit: Budget, usage: BudgetUsage, *, inclusive: bool = False) -> bool:
    return all(bound is None or (amount <= bound if inclusive else amount < bound) for bound, amount in ((limit.token_limit, usage.tokens), (limit.cost_limit_microusd, usage.cost_microusd), (limit.wall_time_limit_ms, usage.wall_time_ms)))
def _close_attempt(snapshot: WorkItemSnapshot, attempt: Attempt, status: str, event: WorkItemEvent, reason: str) -> tuple[Attempt, ...]:
    return (*snapshot.attempts[:-1], replace(attempt, status=status, ended_at=event.occurred_at, reason=reason))
def _require_event(snapshot: WorkItemSnapshot, event: WorkItemEvent) -> Attempt | None:
    states, fields, attempt_scope = _RULES[event.kind]
    _reject(snapshot.status not in states, f"invalid {event.kind} transition from {snapshot.status}")
    _keys(event.payload, fields, event.kind)
    if attempt_scope is None:
        return None
    attempt = snapshot.attempts[-1] if attempt_scope == "latest" and snapshot.attempts else snapshot.current_attempt
    _reject(attempt is None or _required(event.payload["attempt_id"], "attempt_id") != attempt.attempt_id, f"event does not match the {attempt_scope} attempt"); _reject(attempt_scope == "active" and event.kind != "attempt.expired" and snapshot.active_lease is not None and snapshot.active_lease._expired_by(event.occurred_at), "attempt command occurred at or after lease expiry")
    return attempt
def _created(event: WorkItemEvent) -> WorkItemSnapshot:
    payload = event.payload
    _keys(payload, {"title", "parent_work_item_id", "dependency_refs", "retry_policy", "resume_policy", "cancellation_policy", "budget"}, event.kind)
    dependencies = _strings(payload["dependency_refs"], "dependency_refs")
    parent = _optional(payload["parent_work_item_id"], "parent_work_item_id")
    if parent == event.work_item_id: raise ValueError("Work Item cannot parent itself")
    _reject(not all(isinstance(payload[name], Mapping) for name in ("retry_policy", "resume_policy", "cancellation_policy", "budget")), "Work Item policies must be mappings", TypeError)
    return WorkItemSnapshot(event.work_item_id, _required(payload["title"], "title"), "blocked" if dependencies else "ready", parent, (), dependencies, (), (), RetryPolicy.from_dict(payload["retry_policy"]), ResumePolicy.from_dict(payload["resume_policy"]), CancellationPolicy.from_dict(payload["cancellation_policy"]), Budget.from_dict(payload["budget"]), BudgetUsage(), None, (), (), (), None, None, 1)
def _apply(snapshot: WorkItemSnapshot, event: WorkItemEvent) -> WorkItemSnapshot:
    payload, kind, attempt = event.payload, event.kind, _require_event(snapshot, event)
    if kind == "dependency.added":
        ref = _required(payload["dependency_ref"], "dependency_ref")
        if ref in snapshot.dependency_refs: raise ValueError("dependency is already attached")
        return replace(snapshot, status="blocked", dependency_refs=(*snapshot.dependency_refs, ref))
    if kind == "dependency.satisfied":
        ref = _required(payload["dependency_ref"], "dependency_ref")
        if ref not in snapshot.dependency_refs or ref in snapshot.satisfied_dependency_refs: raise ValueError("dependency is not pending")
        satisfied = (*snapshot.satisfied_dependency_refs, ref)
        return replace(snapshot, status="ready" if set(satisfied) == set(snapshot.dependency_refs) else "blocked", satisfied_dependency_refs=satisfied)
    if kind == "child.delegated":
        child = _required(payload["child_work_item_id"], "child_work_item_id")
        if child == snapshot.work_item_id or child in snapshot.child_work_item_ids: raise ValueError("invalid child Work Item")
        return replace(snapshot, child_work_item_ids=(*snapshot.child_work_item_ids, child))
    if kind == "lease.acquired":
        lease_id = _required(payload["lease_id"], "lease_id")
        if snapshot.active_lease is not None or lease_id in snapshot.used_lease_ids or not _budget_allows(snapshot.budget, snapshot.budget_usage): raise ValueError("Work Item cannot acquire another or previously used lease")
        lease = Lease(lease_id, _required(payload["worker_id"], "worker_id"), event.occurred_at, _optional(payload["expires_at"], "expires_at"))
        return replace(snapshot, status="leased", active_lease=lease, used_lease_ids=(*snapshot.used_lease_ids, lease_id))
    if kind in {"lease.released", "lease.expired"}:
        lease_id = _required(payload["lease_id"], "lease_id")
        if snapshot.active_lease is None or lease_id != snapshot.active_lease.lease_id: raise ValueError("lease outcome does not match the active lease")
        _reject(kind == "lease.expired" and not snapshot.active_lease._expired_by(event.occurred_at), "lease expiry event precedes the active lease expiry")
        return replace(snapshot, status="ready", active_lease=None)
    if kind == "attempt.started":
        _reject(snapshot.active_lease is None or _required(payload["lease_id"], "lease_id") != snapshot.active_lease.lease_id, "attempt does not match the active lease")
        _reject(snapshot.active_lease is not None and snapshot.active_lease._expired_by(event.occurred_at), "attempt cannot start at or after lease expiry")
        attempt_id, session_ref = _required(payload["attempt_id"], "attempt_id"), _required(payload["session_ref"], "session_ref")
        if any(row.attempt_id == attempt_id or row.session_ref == session_ref for row in snapshot.attempts): raise ValueError("attempt and session references must be unique")
        started = Attempt(attempt_id, len(snapshot.attempts) + 1, session_ref, snapshot.active_lease.worker_id, "running", event.occurred_at)
        return replace(snapshot, status="running", attempts=(*snapshot.attempts, started))
    if kind == "placement.attached":
        if not isinstance(payload["placement"], Mapping): raise TypeError("placement must be a mapping")
        _reject(snapshot.active_lease is not None and snapshot.active_lease._expired_by(event.occurred_at), "placement occurred at or after lease expiry")
        placement = WorkPlacement(**dict(payload["placement"])); current = snapshot.current_attempt
        if current is None or (placement.work_item_id, placement.attempt_id, placement.worker_id, placement.session_ref) != (snapshot.work_item_id, current.attempt_id, current.worker_id, current.session_ref): raise ValueError("placement does not match the active attempt")
        if any(row.placement_id == placement.placement_id or row.attempt_id == placement.attempt_id for row in snapshot.placements): raise ValueError("attempt already has a placement")
        return replace(snapshot, placements=(*snapshot.placements, placement))
    if kind == "budget.consumed":
        usage = snapshot.budget_usage.plus(_natural(payload["tokens"], "tokens"), _natural(payload["cost_microusd"], "cost_microusd"), _natural(payload["wall_time_ms"], "wall_time_ms"))
        if not _budget_allows(snapshot.budget, usage, inclusive=True): raise ValueError("budget consumption exceeds Work Item limits")
        return replace(snapshot, budget_usage=usage)
    if kind == "checkpoint.recorded":
        checkpoint = _required(payload["checkpoint_ref"], "checkpoint_ref")
        return replace(snapshot, attempts=(*snapshot.attempts[:-1], replace(attempt, checkpoint_ref=checkpoint)), latest_checkpoint_ref=checkpoint)
    if kind in {"work.waiting", "work.paused"}:
        reason = _required(payload["reason"], "reason")
        if snapshot.resume_policy.mode == "never" or snapshot.resume_policy.mode == "checkpoint" and attempt.checkpoint_ref is None: raise ValueError("Work Item resume policy does not permit this transition")
        wake_refs = _strings(payload["wake_refs"], "wake_refs", nonempty=True) if kind == "work.waiting" else ()
        status = "waiting" if kind == "work.waiting" else "paused"
        return replace(snapshot, status=status, active_lease=None, attempts=_close_attempt(snapshot, attempt, status, event, reason), wake_refs=wake_refs)
    if kind == "work.woken":
        wake_ref = _required(payload["wake_ref"], "wake_ref")
        if wake_ref not in snapshot.wake_refs: raise ValueError("wake reference is not pending")
        return replace(snapshot, status="ready", wake_refs=())
    if kind == "work.resumed":
        if snapshot.resume_policy.mode == "never" or type(payload["approved"]) is not bool or snapshot.resume_policy.requires_approval and not payload["approved"]: raise ValueError("Work Item resume policy denied resume")
        if snapshot.resume_policy.mode == "checkpoint" and attempt.checkpoint_ref is None: raise ValueError("checkpoint resume requires a checkpoint")
        return replace(snapshot, status="ready")
    if kind == "attempt.expired":
        lease = snapshot.active_lease; expected_retry = snapshot.retry_policy.allows("lease expired") and len(snapshot.attempts) < snapshot.retry_policy.max_attempts and _budget_allows(snapshot.budget, snapshot.budget_usage); _reject(lease is None or payload["lease_id"] != lease.lease_id or not lease._expired_by(event.occurred_at), "attempt expiry does not match an expired active lease"); _reject(type(payload["will_retry"]) is not bool or payload["will_retry"] != expected_retry, "will_retry does not match Work Item retry policy")
        return replace(snapshot, status="ready" if expected_retry else "failed", active_lease=None, attempts=_close_attempt(snapshot, attempt, "failed", event, "lease expired"), terminal_reason=None if expected_retry else "lease expired")
    if kind == "attempt.failed":
        reason = _required(payload["reason"], "reason")
        if type(payload["retryable"]) is not bool or type(payload["will_retry"]) is not bool: raise TypeError("retry fields must be boolean")
        expected_retry = payload["retryable"] and snapshot.retry_policy.allows(reason) and len(snapshot.attempts) < snapshot.retry_policy.max_attempts and _budget_allows(snapshot.budget, snapshot.budget_usage)
        if payload["will_retry"] != expected_retry: raise ValueError("will_retry does not match Work Item retry policy")
        return replace(snapshot, status="ready" if expected_retry else "failed", active_lease=None, attempts=_close_attempt(snapshot, attempt, "failed", event, reason), terminal_reason=None if expected_retry else reason)
    if kind == "work.completed":
        summary = _required(payload["summary"], "summary")
        return replace(snapshot, status="completed", active_lease=None, attempts=_close_attempt(snapshot, attempt, "completed", event, summary), terminal_reason=summary)
    if kind == "work.canceled":
        actor, reason = _required(payload["actor_id"], "actor_id"), _required(payload["reason"], "reason")
        children = _strings(payload["child_work_item_ids"], "child_work_item_ids")
        expected_children = snapshot.child_work_item_ids if snapshot.cancellation_policy.propagate_to_children else ()
        if snapshot.cancellation_policy.mode == "never" or actor not in snapshot.cancellation_policy.cancellable_by: raise ValueError("actor cannot cancel this Work Item")
        if payload["cleanup"] != snapshot.cancellation_policy.cleanup or children != expected_children: raise ValueError("cancel cleanup does not match Work Item policy")
        if snapshot.current_attempt is not None and snapshot.cancellation_policy.cleanup == "checkpoint_then_stop" and snapshot.current_attempt.checkpoint_ref is None: raise ValueError("checkpoint_then_stop requires a current checkpoint")
        attempts = _close_attempt(snapshot, snapshot.current_attempt, "canceled", event, reason) if snapshot.current_attempt is not None else snapshot.attempts
        return replace(snapshot, status="canceled", active_lease=None, attempts=attempts, wake_refs=(), terminal_reason=reason)
    raise AssertionError(f"unhandled event kind {kind}")
def rebuild_work_item(events: Iterable[WorkItemEvent]) -> WorkItemSnapshot:
    rows = tuple(events)
    if not rows or rows[0].kind != "work_item.created": raise ValueError("event stream must begin with work_item.created")
    if rows[0].sequence != 1: raise ValueError("Work Item event stream must start at sequence 1")
    snapshot = _created(rows[0])
    for expected, event in enumerate(rows[1:], 2):
        if event.work_item_id != rows[0].work_item_id or event.sequence != expected: raise ValueError("event stream is not contiguous for one Work Item")
        snapshot = replace(_apply(snapshot, event), event_count=expected)
    return snapshot
class WorkItem:
    def __init__(self, events: Iterable[WorkItemEvent], *, repository: WorkItemRepository, clock: Clock | None = None, ids: IdSource | None = None) -> None:
        self._lock, self._appending, self._repository = RLock(), False, repository
        self._events = list(events); self._clock = clock if clock is not None else SystemClock(); self._ids = ids if ids is not None else UUIDSource()
        self._snapshot = rebuild_work_item(self._events); self._work_item_id = self._snapshot.work_item_id; authority = repository.read(self._work_item_id)
        if tuple(self._events) != authority[:len(self._events)]: raise ValueError("Work Item events do not match repository authority")
        self._events, self._snapshot = list(authority), rebuild_work_item(authority)
        self._set_fences(self._snapshot)
    @classmethod
    def create(cls, title: str, *, work_item_id: str | None = None, parent_work_item_id: str | None = None, dependency_refs: Iterable[str] = (), retry_policy: RetryPolicy | None = None, resume_policy: ResumePolicy | None = None, cancellation_policy: CancellationPolicy | None = None, budget: Budget | None = None, clock: Clock | None = None, ids: IdSource | None = None, repository: WorkItemRepository | None = None) -> "WorkItem":
        active_clock, active_ids, active_repository = clock if clock is not None else SystemClock(), ids if ids is not None else UUIDSource(), repository or WorkItemRepository()
        item_id = active_ids.new_id() if work_item_id is None else work_item_id
        event = WorkItemEvent(item_id, 1, "work_item.created", active_clock.now(), {"title": title, "parent_work_item_id": parent_work_item_id, "dependency_refs": list(dependency_refs), "retry_policy": (retry_policy or RetryPolicy()).as_dict(), "resume_policy": (resume_policy or ResumePolicy()).as_dict(), "cancellation_policy": (cancellation_policy or CancellationPolicy()).as_dict(), "budget": (budget or Budget()).as_dict()})
        active_repository.append({item_id: 0}, (event,))
        return cls((event,), repository=active_repository, clock=active_clock, ids=active_ids)
    @classmethod
    def restore(cls, repository: WorkItemRepository, work_item_id: str, **kwargs: Any) -> "WorkItem": return cls(repository.read(work_item_id), repository=repository, **kwargs)
    @staticmethod
    def _attempt_identity(snapshot: WorkItemSnapshot) -> str | None:
        return snapshot.attempts[-1].attempt_id if snapshot.status in {"running", "waiting", "paused"} and snapshot.attempts else None
    def _set_fences(self, snapshot: WorkItemSnapshot) -> None:
        self._lease_fence, self._attempt_fence = (snapshot.active_lease.lease_id if snapshot.active_lease is not None else None), self._attempt_identity(snapshot)
    def _require_fence(self, fence: str) -> None:
        actual = self._snapshot.active_lease.lease_id if fence == "lease" and self._snapshot.active_lease is not None else self._attempt_identity(self._snapshot) if fence == "attempt" else None
        _reject((self._lease_fence if fence == "lease" else self._attempt_fence) != actual, f"stale Work Item {fence} authority", RuntimeError)
    def _fence_payload(self, values: Mapping[str, Any], *, lease: bool = False, latest: bool = False) -> dict[str, Any]:
        fenced = self._snapshot.active_lease if lease else self._snapshot.attempts[-1] if latest and self._snapshot.attempts else self._snapshot.current_attempt
        _reject(fenced is None and not lease, "command requires an attempt" if latest else "command requires an active attempt", RuntimeError)
        return {("lease_id" if lease else "attempt_id"): "" if fenced is None else fenced.lease_id if lease else fenced.attempt_id, **values}
    def _refresh(self) -> None:
        events = self._repository.read(self._work_item_id)
        if len(events) != len(self._events): self._events, self._snapshot = list(events), rebuild_work_item(events)
    @property
    def events(self) -> tuple[WorkItemEvent, ...]:
        with self._lock: self._refresh(); return tuple(self._events)
    @property
    def read_model(self) -> WorkItemSnapshot:
        with self._lock: self._refresh(); return self._snapshot
    def add_dependency(self, dependency_ref: str) -> WorkItemSnapshot: return self._append("dependency.added", lambda: {"dependency_ref": dependency_ref})
    def satisfy_dependency(self, dependency_ref: str) -> WorkItemSnapshot: return self._append("dependency.satisfied", lambda: {"dependency_ref": dependency_ref})
    def acquire_lease(self, worker_id: str, *, lease_id: str | None = None, expires_at: str | None = None) -> WorkItemSnapshot: return self._append("lease.acquired", lambda: {"lease_id": self._ids.new_id() if lease_id is None else lease_id, "worker_id": worker_id, "expires_at": expires_at})
    def release_lease(self, lease_id: str) -> WorkItemSnapshot: return self._append("lease.released", lambda: {"lease_id": lease_id}, fence="lease")
    def start_attempt(self, session_ref: str, *, attempt_id: str | None = None) -> WorkItemSnapshot:
        return self._append("attempt.started", lambda: self._fence_payload({"attempt_id": self._ids.new_id() if attempt_id is None else attempt_id, "session_ref": session_ref}, lease=True), fence="lease")
    def attach_placement(self, placement: WorkPlacement) -> WorkItemSnapshot: return self._append("placement.attached", lambda: {"placement": placement.as_dict()}, fence="attempt")
    def consume_budget(self, *, tokens: int = 0, cost_microusd: int = 0, wall_time_ms: int = 0) -> WorkItemSnapshot: return self._append("budget.consumed", lambda: self._fence_payload({"tokens": tokens, "cost_microusd": cost_microusd, "wall_time_ms": wall_time_ms}), fence="attempt")
    def checkpoint(self, checkpoint_ref: str) -> WorkItemSnapshot: return self._append("checkpoint.recorded", lambda: self._fence_payload({"checkpoint_ref": checkpoint_ref}), fence="attempt")
    def wait(self, wake_refs: Iterable[str], reason: str) -> WorkItemSnapshot: return self._append("work.waiting", lambda: self._fence_payload({"wake_refs": list(wake_refs), "reason": reason}), fence="attempt")
    def wake(self, wake_ref: str) -> WorkItemSnapshot: return self._append("work.woken", lambda: self._fence_payload({"wake_ref": wake_ref}, latest=True), fence="attempt")
    def pause(self, reason: str) -> WorkItemSnapshot: return self._append("work.paused", lambda: self._fence_payload({"reason": reason}), fence="attempt")
    def resume(self, *, approved: bool = False) -> WorkItemSnapshot: return self._append("work.resumed", lambda: self._fence_payload({"approved": approved}, latest=True), fence="attempt")
    def fail_attempt(self, reason: str, *, retryable: bool) -> WorkItemSnapshot:
        def payload() -> dict[str, Any]:
            return self._fence_payload({"reason": reason, "retryable": retryable, "will_retry": retryable and self._snapshot.retry_policy.allows(reason) and len(self._snapshot.attempts) < self._snapshot.retry_policy.max_attempts and _budget_allows(self._snapshot.budget, self._snapshot.budget_usage)})
        return self._append("attempt.failed", payload, fence="attempt")
    def complete(self, summary: str = "completed") -> WorkItemSnapshot: return self._append("work.completed", lambda: self._fence_payload({"summary": summary}), fence="attempt")
    def cancel(self, actor_id: str, reason: str = "operator request") -> WorkItemSnapshot:
        return self._append("work.canceled", lambda: {"actor_id": actor_id, "reason": reason, "cleanup": self._snapshot.cancellation_policy.cleanup, "child_work_item_ids": list(self._snapshot.child_work_item_ids if self._snapshot.cancellation_policy.propagate_to_children else ())})
    def delegate(self, title: str, *, child_work_item_id: str | None = None, dependency_refs: Iterable[str] = (), retry_policy: RetryPolicy | None = None, resume_policy: ResumePolicy | None = None, cancellation_policy: CancellationPolicy | None = None, budget: Budget | None = None) -> "WorkItem":
        with self._lock:
            if self._appending: raise RuntimeError("cannot mutate Work Item while an append is in progress")
            self._refresh()
            self._require_fence("attempt")
            if self._snapshot.status != "running": raise RuntimeError(f"cannot delegate while Work Item is {self._snapshot.status}")
            occurred_at = self._clock.now()
            if (expiry := self._expiry_event(occurred_at)) is not None: self._record(expiry); raise RuntimeError("cannot delegate after lease expiry")
            child_id = self._ids.new_id() if child_work_item_id is None else child_work_item_id
            child_event = WorkItemEvent(child_id, 1, "work_item.created", occurred_at, {"title": title, "parent_work_item_id": self._work_item_id, "dependency_refs": list(dependency_refs), "retry_policy": (retry_policy or RetryPolicy()).as_dict(), "resume_policy": (resume_policy or ResumePolicy()).as_dict(), "cancellation_policy": (cancellation_policy or CancellationPolicy()).as_dict(), "budget": (budget or Budget()).as_dict()})
            parent_event = WorkItemEvent(self._work_item_id, len(self._events) + 1, "child.delegated", occurred_at, self._fence_payload({"child_work_item_id": child_id}))
            parent_snapshot = rebuild_work_item((*self._events, parent_event))
            self._repository.append({self._work_item_id: len(self._events), child_id: 0}, (child_event, parent_event))
            self._events.append(parent_event); self._snapshot = parent_snapshot; self._set_fences(parent_snapshot)
            return WorkItem((child_event,), repository=self._repository, clock=self._clock, ids=self._ids)
    def _expiry_event(self, occurred_at: str) -> WorkItemEvent | None:
        lease = self._snapshot.active_lease
        if lease is None or not lease._expired_by(occurred_at): return None
        if self._snapshot.current_attempt is None: return WorkItemEvent(self._work_item_id, len(self._events) + 1, "lease.expired", occurred_at, self._fence_payload({}, lease=True))
        will_retry = self._snapshot.retry_policy.allows("lease expired") and len(self._snapshot.attempts) < self._snapshot.retry_policy.max_attempts and _budget_allows(self._snapshot.budget, self._snapshot.budget_usage); return WorkItemEvent(self._work_item_id, len(self._events) + 1, "attempt.expired", occurred_at, self._fence_payload({"lease_id": lease.lease_id, "will_retry": will_retry}))
    def _record(self, event: WorkItemEvent) -> WorkItemSnapshot:
        next_events = [*self._events, event]; next_snapshot = rebuild_work_item(next_events); self._repository.append({self._work_item_id: len(self._events)}, (event,)); self._events, self._snapshot = next_events, next_snapshot; self._set_fences(next_snapshot); return next_snapshot
    def _append(self, kind: str, payload: Callable[[], Mapping[str, Any]], *, fence: str | None = None) -> WorkItemSnapshot:
        with self._lock:
            if self._appending: raise RuntimeError("cannot mutate Work Item while an append is in progress")
            self._refresh()
            if fence is not None: self._require_fence(fence)
            if self._snapshot.status not in _RULES[kind][0]: raise RuntimeError(f"cannot apply {kind} while Work Item is {self._snapshot.status}")
            self._appending = True
            try:
                occurred_at = self._clock.now()
                expiry = self._expiry_event(occurred_at) if kind == "attempt.started" or fence == "attempt" else None
                event = expiry or WorkItemEvent(self._work_item_id, len(self._events) + 1, kind, occurred_at, payload())
                next_snapshot = self._record(event)
                _reject(expiry is not None, f"cannot apply {kind} after lease expiry", RuntimeError)
                return next_snapshot
            finally:
                self._appending = False
