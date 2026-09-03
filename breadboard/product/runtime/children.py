"""Internal Session-centered durable child factory.

Child coordination is retained on the existing engine ``SessionRecord`` /
``SessionRegistry``.  Product ``Session``, Work Item and ArtifactStore remain
their existing owners; this module only composes their ordering.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from breadboard.product.coordination.placement import WorkPlacement
from breadboard.product.coordination.work_items import (
    CancellationPolicy,
    ResumePolicy,
    RetryPolicy,
    WorkItem,
    WorkItemRepository,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime.events import Clock, IdSource, Session, SystemClock, UUIDSource
from breadboard.product.runtime.session_store import create_session, load_session, mutate_session

_TERMINAL = frozenset({"completed", "failed", "canceled"})
_CHILD_SCHEMA = "bb.durable_child.v1"


class ChildError(RuntimeError):
    pass


class ExpectedRevisionConflict(ChildError):
    pass


class PreparationRequired(ChildError):
    pass


class LateResultRejected(ChildError):
    pass


@dataclass(frozen=True, slots=True)
class ChildSpec:
    title: str
    task: str
    lock: EffectiveHarnessLock
    worker_id: str
    adapter_family: str
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    resume_policy: ResumePolicy = field(default_factory=lambda: ResumePolicy("restart"))
    cancellation_policy: CancellationPolicy = field(default_factory=CancellationPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.lock, EffectiveHarnessLock):
            raise TypeError("child lock must be an EffectiveHarnessLock")
        for value, name in ((self.title, "title"), (self.task, "task"), (self.worker_id, "worker_id"), (self.adapter_family, "adapter_family")):
            if type(value) is not str or not value.strip():
                raise ValueError(f"child {name} must be non-empty")

    def retained(self) -> dict[str, Any]:
        task_hash = "sha256:" + hashlib.sha256(self.task.encode()).hexdigest()
        lock_hash = self.lock.as_dict().get("graph_hash")
        if type(lock_hash) is not str:
            raise ValueError("child lock has no graph hash")
        return {
            "title": self.title,
            "task_hash": task_hash,
            "lock_hash": lock_hash,
            "worker_id": self.worker_id,
            "adapter_family": self.adapter_family,
            "retry_policy": self.retry_policy.as_dict(),
            "resume_policy": self.resume_policy.as_dict(),
            "cancellation_policy": self.cancellation_policy.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    execution_target_ref: str
    pid: int | None = None
    start_token: str | None = None
    process_group_id: int | None = None
    volatile_handle: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.execution_target_ref) is not str or not self.execution_target_ref.strip():
            raise ValueError("execution target reference must be non-empty")
        if self.pid is not None and (type(self.pid) is not int or self.pid < 1):
            raise ValueError("execution target pid must be positive")
        if self.process_group_id is not None and (type(self.process_group_id) is not int or self.process_group_id < 1):
            raise ValueError("execution target process group must be positive")

    def retained(self) -> dict[str, Any]:
        return {"ref": self.execution_target_ref, "pid": self.pid, "start_token": self.start_token, "process_group_id": self.process_group_id}


@dataclass(frozen=True, slots=True)
class ChildActivation:
    parent_session_id: str
    root_session_id: str
    parent_work_item_id: str
    child_session_id: str
    child_work_item_id: str
    attempt_id: str
    recovery_ref: str
    execution_target_ref: str
    adapter_family: str


@dataclass(frozen=True, slots=True)
class ChildState:
    child_session_id: str
    child_work_item_id: str
    parent_session_id: str
    root_session_id: str
    parent_work_item_id: str
    attempt_id: str
    recovery_ref: str
    execution_target_ref: str
    adapter_family: str
    status: str
    revision: int
    cancellation_requested: bool = False
    cancellation_reason: str | None = None
    result_prepared: bool = False
    result_refs: tuple[str, ...] = ()
    terminal_outcome: str | None = None
    terminal_count: int = 0
    joined: bool = False
    settlement: Mapping[str, Any] | None = None
    child_spec: Mapping[str, Any] = field(default_factory=dict)
    execution_target: Mapping[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str | None:
        return self.terminal_outcome

    def retained(self) -> dict[str, Any]:
        return {
            "schema_version": _CHILD_SCHEMA,
            "child_session_id": self.child_session_id,
            "child_work_item_id": self.child_work_item_id,
            "parent_session_id": self.parent_session_id,
            "root_session_id": self.root_session_id,
            "parent_work_item_id": self.parent_work_item_id,
            "attempt_id": self.attempt_id,
            "recovery_ref": self.recovery_ref,
            "execution_target_ref": self.execution_target_ref,
            "adapter_family": self.adapter_family,
            "status": self.status,
            "revision": self.revision,
            "cancellation_requested": self.cancellation_requested,
            "cancellation_reason": self.cancellation_reason,
            "result_prepared": self.result_prepared,
            "result_refs": list(self.result_refs),
            "terminal_outcome": self.terminal_outcome,
            "terminal_count": self.terminal_count,
            "joined": self.joined,
            "settlement": dict(self.settlement) if self.settlement is not None else None,
            "child_spec": dict(self.child_spec),
            "execution_target": dict(self.execution_target),
        }

    @classmethod
    def from_retained(cls, value: Mapping[str, Any]) -> "ChildState":
        if value.get("schema_version") != _CHILD_SCHEMA:
            raise ValueError("unsupported durable child state")
        return cls(
            child_session_id=str(value["child_session_id"]),
            child_work_item_id=str(value["child_work_item_id"]),
            parent_session_id=str(value["parent_session_id"]),
            root_session_id=str(value["root_session_id"]),
            parent_work_item_id=str(value["parent_work_item_id"]),
            attempt_id=str(value["attempt_id"]),
            recovery_ref=str(value["recovery_ref"]),
            execution_target_ref=str(value["execution_target_ref"]),
            adapter_family=str(value["adapter_family"]),
            status=str(value["status"]),
            revision=int(value["revision"]),
            cancellation_requested=bool(value.get("cancellation_requested")),
            cancellation_reason=value.get("cancellation_reason"),
            result_prepared=bool(value.get("result_prepared")),
            result_refs=tuple(str(ref) for ref in value.get("result_refs", ())),
            terminal_outcome=value.get("terminal_outcome"),
            terminal_count=int(value.get("terminal_count", 0)),
            joined=bool(value.get("joined")),
            settlement=value.get("settlement"),
            child_spec=value.get("child_spec") or {},
            execution_target=value.get("execution_target") or {},
        )


class ChildExecutionAdapter(Protocol):
    family: str
    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget: ...
    def observe(self, target: Mapping[str, Any]) -> str: ...
    def cancel(self, target: Mapping[str, Any]) -> None: ...
    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | ArtifactRef | None: ...


def _sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("synchronous child API cannot run inside an event loop")



class DurableChildFactory:
    """One provider-neutral boundary over retained SessionRecord and owners."""

    def __init__(self, workspace: str | Path, *, registry: Any, repository: WorkItemRepository, adapters: Iterable[ChildExecutionAdapter], clock: Clock | None = None, ids: IdSource | None = None, artifact_store: ArtifactStore | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.repository = repository
        self.clock = clock or SystemClock()
        self.ids = ids or UUIDSource()
        self.artifacts = artifact_store or ArtifactStore(self.workspace / ".breadboard" / "artifacts")
        self.adapters = {adapter.family: adapter for adapter in adapters}
        for adapter in self.adapters.values():
            binder = getattr(adapter, "bind_workspace", None)
            if callable(binder):
                binder(self.workspace)
        if not self.adapters:
            raise ValueError("at least one child execution adapter is required")
    def cancel_tree(self, *, parent_session_id: str, parent_work_item_id: str, reason: str = "operator request") -> tuple[ChildState, ...]:
        """Persist parent intent and every descendant intent before signaling."""
        parent, _ = load_session(self.workspace, parent_session_id)
        if parent.read_model.status not in _TERMINAL:
            mutate_session(self.workspace, parent_session_id, lambda current: current.cancel(reason))
            from breadboard_engine.api.cli_bridge.models import SessionStatus
            self._registry("update_status", parent_session_id, status=SessionStatus.STOPPED)
        records = self._registry("records")
        by_parent: dict[str, list[ChildState]] = {}
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            value = metadata.get("durable_child")
            if isinstance(value, Mapping):
                by_parent.setdefault(str(value.get("parent_work_item_id")), []).append(self._record_state(record.session_id))
        descendants: list[ChildState] = []
        queue = [parent_work_item_id]
        while queue:
            parent_id = queue.pop(0)
            for state in by_parent.get(parent_id, ()):
                descendants.append(state)
                queue.append(state.child_work_item_id)
        if any(state.settlement is not None for state in descendants if not state.terminal_count):
            raise ExpectedRevisionConflict("child settlement is already reserved")
        pending = []
        for state in descendants:
            if state.terminal_count or state.cancellation_requested:
                continue
            pending.append(self._cas(state, status="cancel_requested", cancellation_requested=True, cancellation_reason=reason))
        parent_work = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        if parent_work.read_model.status not in _TERMINAL:
            parent_work.cancel("operator", reason)
        settled = []
        for state in pending:
            self.adapters[state.adapter_family].cancel(state.execution_target)
            settled.append(self._settle(state, "canceled", (), allow_unprepared=True))
        return tuple(settled)

    def _registry(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return _sync(getattr(self.registry, method)(*args, **kwargs))

    def _record_state(self, child_session_id: str) -> ChildState:
        record = self._registry("get", child_session_id)
        if record is None:
            raise ChildError(f"retained child SessionRecord is missing: {child_session_id}")
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        value = metadata.get("durable_child")
        if not isinstance(value, Mapping):
            raise ChildError("SessionRecord has no durable child state")
        return ChildState.from_retained(value)

    def _cas(self, state: ChildState, **changes: Any) -> ChildState:
        next_state = replace(state, revision=state.revision + 1, **changes)
        try:
            self._registry("update_durable_child", state.child_session_id, expected_revision=state.revision, child_state=next_state.retained())
        except RuntimeError as error:
            raise ExpectedRevisionConflict(str(error)) from error
        return next_state

    def _create_record(self, state: ChildState) -> None:
        from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
        from breadboard_engine.api.cli_bridge.models import SessionStatus
        self._registry("create", SessionRecord(session_id=state.child_session_id, status=SessionStatus.STARTING, metadata={"durable_child": state.retained(), "workspace": str(self.workspace)}))
    def _status(self, state: ChildState) -> None:
        from breadboard_engine.api.cli_bridge.models import SessionStatus
        value = (
            SessionStatus.RUNNING
            if state.status in {"starting", "running", "cancel_requested"}
            else SessionStatus.COMPLETED
            if state.status == "completed"
            else SessionStatus.FAILED
            if state.status == "failed"
            else SessionStatus.STOPPED
        )
        self._registry("update_status", state.child_session_id, status=value)

    def _abort_startup(self, state: ChildState) -> ChildState:
        failed = self._cas(state, status="failed", terminal_outcome="failed", terminal_count=1, settlement=None, joined=True)
        self._status(failed)
        return failed

    def start(self, *, parent_session_id: str, root_session_id: str, parent_work_item_id: str, spec: ChildSpec) -> ChildActivation:
        load_session(self.workspace, parent_session_id)
        if root_session_id != parent_session_id:
            load_session(self.workspace, root_session_id)
        child_session_id = self.ids.new_id()
        child_work_item_id = self.ids.new_id()
        attempt_id = self.ids.new_id()
        recovery_ref = f"child://{child_session_id}/attempt/{attempt_id}"
        initial = ChildState(child_session_id, child_work_item_id, parent_session_id, root_session_id, parent_work_item_id, attempt_id, recovery_ref, f"reserved:{child_session_id}", spec.adapter_family, "starting", 0, child_spec=spec.retained(), execution_target={"ref": f"reserved:{child_session_id}"})
        # Retain identity before WorkItem delegation. A crash here leaves a
        # recoverable STARTING SessionRecord, not an unowned launched process.
        self._create_record(initial)
        parent = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        child = parent.delegate(spec.title, attempt_id=parent.read_model.current_attempt.attempt_id, child_work_item_id=child_work_item_id, retry_policy=spec.retry_policy, resume_policy=spec.resume_policy, cancellation_policy=spec.cancellation_policy)  # type: ignore[union-attr]
        product = Session.start(spec.lock, spec.task, session_id=child_session_id, clock=self.clock, ids=self.ids)
        create_session(self.workspace, product)
        child.acquire_lease(spec.worker_id, lease_id=self.ids.new_id())
        child.start_attempt(child_session_id, lease_id=child.read_model.active_lease.lease_id, attempt_id=attempt_id)  # type: ignore[union-attr]
        placement = WorkPlacement(self.ids.new_id(), child_work_item_id, attempt_id, spec.worker_id, child_session_id, initial.execution_target_ref, self.clock.now())
        child.attach_placement(placement)
        state = self._cas(initial, status="running")
        self._status(state)
        adapter = self.adapters[spec.adapter_family]
        target = adapter.start(replace(ChildActivation(parent_session_id, root_session_id, parent_work_item_id, child_session_id, child_work_item_id, attempt_id, recovery_ref, initial.execution_target_ref, spec.adapter_family), execution_target_ref=initial.execution_target_ref), spec)
        state = self._cas(state, execution_target_ref=target.execution_target_ref, execution_target=target.retained())
        return ChildActivation(parent_session_id, root_session_id, parent_work_item_id, child_session_id, child_work_item_id, attempt_id, state.recovery_ref, state.execution_target_ref, spec.adapter_family)

    def prepare_result(self, child_session_id: str, *, expected_revision: int, result: bytes | ArtifactRef | None = None) -> ChildState:
        state = self._record_state(child_session_id)
        if state.terminal_count:
            raise LateResultRejected("late result preparation cannot follow settlement")
        if result is None:
            spec = self._spec(state)
            result = self.adapters[state.adapter_family].prepare_result(state.execution_target, spec)
        if isinstance(result, ArtifactRef):
            self.artifacts.read(result)
            refs = (result.digest,)
        elif isinstance(result, bytes):
            refs = (self.artifacts.put(result).digest,)
        elif result is None:
            refs = ()
        else:
            raise TypeError("prepared child result must be bytes, ArtifactRef, or None")
        if state.revision != expected_revision:
            raise ExpectedRevisionConflict(f"stale child revision: expected {expected_revision}, actual {state.revision}")
        return self._cas(state, result_prepared=True, result_refs=refs)

    def cancel(self, child_session_id: str, *, expected_revision: int, reason: str = "operator request") -> ChildState:
        state = self._record_state(child_session_id)
        if state.terminal_count:
            if state.terminal_outcome == "canceled":
                return state
            raise LateResultRejected("cannot cancel a terminal child")
        if state.revision != expected_revision:
            raise ExpectedRevisionConflict(f"stale child revision: expected {expected_revision}, actual {state.revision}")
        if state.settlement is not None:
            raise ExpectedRevisionConflict("child settlement is already reserved")
        if state.cancellation_requested:
            raise ExpectedRevisionConflict("child cancellation is already requested")
        state = self._cas(state, status="cancel_requested", cancellation_requested=True, cancellation_reason=reason)
        self.adapters[state.adapter_family].cancel(state.execution_target)
        return self._settle(state, "canceled", (), allow_unprepared=True)

    def settle(self, child_session_id: str, *, expected_revision: int, outcome: str, result_refs: Sequence[str] = ()) -> ChildState:
        state = self._record_state(child_session_id)
        if state.terminal_count:
            if state.terminal_outcome == outcome and tuple(result_refs) == state.result_refs:
                return state
            raise LateResultRejected("late child result cannot replace terminal outcome")
        if state.settlement is not None:
            raise ExpectedRevisionConflict("child settlement is already reserved")
        if state.cancellation_requested and outcome != "canceled":
            raise LateResultRejected("late child result arrived after cancellation intent")
        if outcome not in _TERMINAL:
            raise ValueError("child outcome must be completed, failed, or canceled")
        if outcome == "completed" and not state.result_prepared:
            raise PreparationRequired("result/artifact preparation must precede settlement")
        if state.revision != expected_revision:
            raise ExpectedRevisionConflict(f"stale child revision: expected {expected_revision}, actual {state.revision}")
        if result_refs and tuple(result_refs) != state.result_refs:
            raise ExpectedRevisionConflict("settlement result refs do not match prepared refs")
        # Reserve the settlement in the retained SessionRecord before touching
        # either terminal owner. Fresh reconcile can finish this reservation.
        reserved = self._cas(state, settlement={"outcome": outcome, "result_refs": list(state.result_refs)})
        return self._settle(reserved, outcome, state.result_refs, allow_unprepared=outcome != "completed")

    def _settle(self, state: ChildState, outcome: str, result_refs: Sequence[str], *, allow_unprepared: bool) -> ChildState:
        session, _ = load_session(self.workspace, state.child_session_id)
        product_status = session.read_model.status
        if product_status in _TERMINAL and product_status != outcome:
            raise ChildError("Product Session terminal outcome disagrees with settlement")
        if product_status not in _TERMINAL:
            if outcome == "completed":
                mutate_session(self.workspace, state.child_session_id, lambda current: current.complete("child result prepared"))
            elif outcome == "failed":
                mutate_session(self.workspace, state.child_session_id, lambda current: current.fail("child_failed", "execution target exited"))
            else:
                mutate_session(self.workspace, state.child_session_id, lambda current: current.cancel("operator request"))
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        work_status = child.read_model.status
        if work_status in _TERMINAL and work_status != outcome:
            raise ChildError("Work Item terminal outcome disagrees with settlement")
        if work_status not in _TERMINAL:
            attempt = child.read_model.current_attempt
            if attempt is None:
                raise ChildError("child Work Item has no active attempt")
            if outcome == "completed":
                child.complete("child result prepared", attempt_id=attempt.attempt_id)
            elif outcome == "failed":
                child.fail_attempt("execution target exited", attempt_id=attempt.attempt_id, retryable=False)
            else:
                child.cancel("operator", "operator request")
        state = self._cas(state, status=outcome, terminal_outcome=outcome, terminal_count=1, result_refs=tuple(result_refs), settlement=None, joined=True)
        self._status(state)
        return state

    def _retry(self, state: ChildState, child: WorkItem) -> ChildState:
        attempt = child.read_model.current_attempt
        if attempt is None:
            raise ChildError("retry requires an active child attempt")
        reason = "execution target exited"
        if not child.read_model.retry_policy.allows(reason) or len(child.read_model.attempts) >= child.read_model.retry_policy.max_attempts:
            return self._settle(state, "failed", (), allow_unprepared=True)
        child.fail_attempt(reason, attempt_id=attempt.attempt_id, retryable=True)
        next_attempt = self.ids.new_id()
        lease_id = self.ids.new_id()
        child.acquire_lease(state.child_spec["worker_id"], lease_id=lease_id)
        child.start_attempt(f"{state.child_session_id}:{next_attempt}", lease_id=lease_id, attempt_id=next_attempt)
        reserved = f"reserved:{state.child_session_id}:{next_attempt}"
        placement = WorkPlacement(self.ids.new_id(), state.child_work_item_id, next_attempt, state.child_spec["worker_id"], f"{state.child_session_id}:{next_attempt}", reserved, self.clock.now())
        child.attach_placement(placement)
        next_recovery = f"child://{state.child_session_id}/attempt/{next_attempt}"
        state = self._cas(state, attempt_id=next_attempt, recovery_ref=next_recovery, execution_target_ref=reserved, execution_target={"ref": reserved}, status="running")
        self._status(state)
        activation = ChildActivation(state.parent_session_id, state.root_session_id, state.parent_work_item_id, state.child_session_id, state.child_work_item_id, next_attempt, next_recovery, reserved, state.adapter_family)
        target = self.adapters[state.adapter_family].start(activation, self._spec(state))
        return self._cas(state, execution_target_ref=target.execution_target_ref, execution_target=target.retained())

    def _repair_terminal_owners(self, state: ChildState) -> None:
        outcome = state.terminal_outcome or state.status
        try:
            session, _ = load_session(self.workspace, state.child_session_id)
        except FileNotFoundError:
            session = None
        if session is not None:
            product_status = session.read_model.status
            if product_status in _TERMINAL and product_status != outcome:
                raise ChildError("Product Session terminal outcome disagrees with retained child")
            if product_status not in _TERMINAL:
                if outcome == "completed":
                    mutate_session(self.workspace, state.child_session_id, lambda current: current.complete("child result prepared"))
                elif outcome == "failed":
                    mutate_session(self.workspace, state.child_session_id, lambda current: current.fail("child_failed", "execution target exited"))
                else:
                    mutate_session(self.workspace, state.child_session_id, lambda current: current.cancel("operator request"))
        try:
            child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        except ChildError:
            child = None
        if child is not None:
            work_status = child.read_model.status
            if work_status in _TERMINAL and work_status != outcome:
                raise ChildError("Work Item terminal outcome disagrees with retained child")
            if work_status not in _TERMINAL:
                attempt = child.read_model.current_attempt
                if attempt is not None:
                    if outcome == "completed":
                        child.complete("child result prepared", attempt_id=attempt.attempt_id)
                    elif outcome == "failed":
                        child.fail_attempt("execution target exited", attempt_id=attempt.attempt_id, retryable=False)
                    else:
                        child.cancel("operator", "operator request")
        self._status(state)
    def reconcile(self, recovery_ref: str) -> ChildState:
        child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
        state = self._record_state(child_session_id)
        if not state.recovery_ref.startswith(recovery_ref.split("/attempt/", 1)[0]):
            raise ChildError("recovery reference does not match child")
        if state.terminal_count:
            self._repair_terminal_owners(state)
            return state
        if state.status == "starting":
            if not self.repository.read(state.child_work_item_id):
                return self._abort_startup(state)
            try:
                load_session(self.workspace, child_session_id)
            except (FileNotFoundError, ValueError):
                return self._abort_startup(state)
            child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
            if child.read_model.current_attempt is None:
                child.acquire_lease(state.child_spec["worker_id"], lease_id=self.ids.new_id())
                child.start_attempt(child_session_id, lease_id=child.read_model.active_lease.lease_id, attempt_id=state.attempt_id)  # type: ignore[union-attr]
            if not any(placement.attempt_id == state.attempt_id for placement in child.read_model.placements):
                child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, state.attempt_id, state.child_spec["worker_id"], child_session_id, state.execution_target_ref, self.clock.now()))
            state = self._cas(state, status="running")
            self._status(state)
            activation = ChildActivation(state.parent_session_id, state.root_session_id, state.parent_work_item_id, state.child_session_id, state.child_work_item_id, state.attempt_id, state.recovery_ref, state.execution_target_ref, state.adapter_family)
            target = self.adapters[state.adapter_family].start(activation, self._spec(state))
            state = self._cas(state, execution_target_ref=target.execution_target_ref, execution_target=target.retained())
        if state.settlement:
            payload = state.settlement
            return self._settle(state, str(payload["outcome"]), tuple(str(ref) for ref in payload.get("result_refs", ())), allow_unprepared=True)
        if state.cancellation_requested:
            self.adapters[state.adapter_family].cancel(state.execution_target)
            return self._settle(state, "canceled", (), allow_unprepared=True)
        observed = str(self.adapters[state.adapter_family].observe(state.execution_target)).lower()
        if observed in {"running", "started", "live", "accepted"}:
            recover = getattr(self.adapters[state.adapter_family], "recover", None)
            if callable(recover):
                target = recover(state.execution_target)
                if target is not None and target.retained() != state.execution_target:
                    state = self._cas(state, execution_target=target.retained())
            return state
        if observed == "completed":
            if not state.result_prepared:
                state = self.prepare_result(child_session_id, expected_revision=state.revision)
            return self.settle(child_session_id, expected_revision=state.revision, outcome="completed", result_refs=state.result_refs)
        if observed == "absent" and getattr(self.adapters[state.adapter_family], "absence_is_terminal", False):
            return self._settle(state, "failed", (), allow_unprepared=True)
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        return self._retry(state, child)
    def _spec(self, state: ChildState) -> ChildSpec:
        value = state.child_spec
        return ChildSpec(
            str(value["title"]),
            str(value.get("task_hash") or "retained-task"),
            EffectiveHarnessLock._from_record({"graph_hash": str(value["lock_hash"])}),
            str(value["worker_id"]),
            str(value["adapter_family"]),
            RetryPolicy.from_dict(value["retry_policy"]),
            ResumePolicy.from_dict(value["resume_policy"]),
            CancellationPolicy.from_dict(value["cancellation_policy"]),
        )

class RayJobAdapter:
    family = "ray-agent-job"
    absence_is_terminal = True
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        result = self.orchestrator.spawn_subagent(owner_agent=activation.parent_session_id, agent_id=activation.child_session_id, async_mode=True, task_descriptor={"child_session_id": activation.child_session_id, "recovery_ref": activation.recovery_ref})
        return ExecutionTarget(f"job:{result.job.job_id}", volatile_handle=result.job)
    def observe(self, target: Mapping[str, Any]) -> str:
        job = self.orchestrator.job_manager.get(str(target.get("ref", "")).removeprefix("job:"))
        return "absent" if job is None else {"accepted": "accepted", "running": "running", "completed": "completed", "failed": "failed", "killed": "killed"}.get(str(job.state), "absent")
    def cancel(self, target: Mapping[str, Any]) -> None:
        self.orchestrator.job_manager.update_state(
            str(target.get("ref", "")).removeprefix("job:"), "killed"
        )

    def prepare_result(
        self, target: Mapping[str, Any], spec: ChildSpec
    ) -> bytes | ArtifactRef | None:
        job = self.orchestrator.job_manager.get(
            str(target.get("ref", "")).removeprefix("job:")
        )
        payload = getattr(job, "result_payload", None) if job is not None else None
        if not isinstance(payload, Mapping):
            return None
        value = payload.get("result_bytes")
        if isinstance(value, bytes):
            return value
        ref = payload.get("artifact_ref")
        if isinstance(ref, Mapping):
            try:
                return ArtifactRef(str(ref["digest"]), int(ref["size_bytes"]), str(ref["media_type"]))
            except (KeyError, TypeError, ValueError):
                return None
        result = payload.get("result")
        return result.encode() if isinstance(result, str) else None


class ProcessExecutionAdapter:
    family = "execution-world-process"

    def __init__(self, command: Sequence[str] = ("/bin/sh", "-c", "sleep 30")) -> None:
        self.command = tuple(command)
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._workspace: Path | None = None

    def bind_workspace(self, workspace: Path) -> None:
        self._workspace = workspace

    def _journal_path(self, target_ref: str) -> Path:
        if self._workspace is None:
            raise RuntimeError("process adapter is not bound to a workspace")
        name = hashlib.sha256(target_ref.encode()).hexdigest()
        path = self._workspace / ".breadboard" / "child-launches" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _reserve(self, target_ref: str) -> Path:
        path = self._journal_path(target_ref)
        with path.open("w", encoding="utf-8") as stream:
            json.dump({"ref": target_ref, "launch_reserved": True}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def _publish(self, target: ExecutionTarget) -> None:
        path = self._journal_path(target.execution_target_ref)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(target.retained(), stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _recover(self, target: Mapping[str, Any]) -> dict[str, Any]:
        if type(target.get("pid")) is int:
            return dict(target)
        try:
            path = self._journal_path(str(target.get("ref", "")))
            retained = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return dict(target)
        recovered = dict(retained)
        recovered.update(target)
        return recovered

    @staticmethod
    def _identity(pid: int) -> tuple[str, int]:
        try:
            raw = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart=,pgid="],
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ProcessLookupError(pid) from error
        fields = raw.rsplit(None, 1)
        if len(fields) != 2:
            raise ProcessLookupError(pid)
        return hashlib.sha256(fields[0].encode()).hexdigest(), int(fields[1])
    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        target_ref = activation.execution_target_ref
        path = self._reserve(target_ref)
        wrapper = (
            "import hashlib,json,os,subprocess,sys\n"
            f"path={str(path)!r}\n"
            "pid=os.getpid()\n"
            "group=os.getpgid(pid)\n"
            "raw=subprocess.check_output(['ps','-p',str(pid),'-o','lstart='],text=True).strip()\n"
            "payload={'ref':sys.argv[1],'pid':pid,'start_token':'sha256:'+hashlib.sha256(raw.encode()).hexdigest(),'process_group_id':group}\n"
            "tmp=path+'.tmp.'+str(pid)\n"
            "with open(tmp,'w',encoding='utf-8') as stream:\n"
            " json.dump(payload,stream,sort_keys=True); stream.flush(); os.fsync(stream.fileno())\n"
            "os.replace(tmp,path)\n"
            "os.execvpe(sys.argv[2],sys.argv[2:],os.environ.copy())\n"
        )
        process = subprocess.Popen((sys.executable, "-c", wrapper, target_ref, *self.command), start_new_session=True)
        self._processes[target_ref] = process
        for _ in range(100):
            recovered = self._recover({"ref": target_ref})
            if type(recovered.get("pid")) is int:
                break
            if process.poll() is not None:
                raise RuntimeError("child launch wrapper exited before publishing identity")
            time.sleep(0.01)
        token, group = self._identity(process.pid)
        target = ExecutionTarget(target_ref, process.pid, token, group, process)
        self._publish(target)
        return target

    def observe(self, target: Mapping[str, Any]) -> str:
        target = self._recover(target)
        pid, token, group = target.get("pid"), target.get("start_token"), target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            return "absent"
        try:
            observed_token, observed_group = self._identity(pid)
        except ProcessLookupError:
            return "absent"
        return "running" if observed_token == token and observed_group == group else "absent"

    def recover(self, target: Mapping[str, Any]) -> ExecutionTarget | None:
        recovered = self._recover(target)
        pid, token, group = recovered.get("pid"), recovered.get("start_token"), recovered.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            return None
        if self.observe(recovered) != "running":
            return None
        return ExecutionTarget(str(recovered["ref"]), pid, token, group)

    def cancel(self, target: Mapping[str, Any]) -> None:
        target = self._recover(target)
        if self.observe(target) != "running":
            return
        os.killpg(int(target["process_group_id"]), 15)

    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | None:
        return None


__all__ = ["ChildActivation", "ChildError", "ChildExecutionAdapter", "ChildSpec", "ChildState", "DurableChildFactory", "ExpectedRevisionConflict", "ExecutionTarget", "LateResultRejected", "PreparationRequired", "ProcessExecutionAdapter", "RayJobAdapter"]
