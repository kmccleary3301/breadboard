"""Internal Session-centered durable child factory.

Child coordination is retained on the existing engine ``SessionRecord`` /
``SessionRegistry``.  Product ``Session``, Work Item and ArtifactStore remain
their existing owners; this module only composes their ordering.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, Protocol

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
from breadboard.product.runtime.events import Clock, IdSource, ProcessLock, Session, SystemClock, UUIDSource
from breadboard.product.runtime.session_store import _mutate_session_locked, _session_transition_guard, create_session, load_session, mutate_session

_TERMINAL = frozenset({"completed", "failed", "canceled"})
_CHILD_SCHEMA = "bb.durable_child.v1"

def _reserved_target_ref(adapter_family: str, suffix: str) -> str:
    return f"job:reserved:{suffix}" if adapter_family == "ray-agent-job" else f"reserved:{suffix}"
def _is_reserved_target_ref(adapter_family: str, target_ref: str, child_session_id: str) -> bool:
    base = _reserved_target_ref(adapter_family, child_session_id)
    return target_ref == base or target_ref.startswith(base + ":")


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
            "task_ref": "child-task://" + task_hash,
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
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if type(self.execution_target_ref) is not str or not self.execution_target_ref.strip():
            raise ValueError("execution target reference must be non-empty")
        if self.pid is not None and (type(self.pid) is not int or self.pid < 1):
            raise ValueError("execution target pid must be positive")
        if self.process_group_id is not None and (type(self.process_group_id) is not int or self.process_group_id < 1):
            raise ValueError("execution target process group must be positive")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("execution target metadata must be a mapping")

    def retained(self) -> dict[str, Any]:
        value = {"ref": self.execution_target_ref, "pid": self.pid, "start_token": self.start_token, "process_group_id": self.process_group_id}
        if self.metadata:
            value["metadata"] = dict(self.metadata)
        return value


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
    workspace: str | None = None
    publish_target: Callable[[ExecutionTarget], None] | None = field(default=None, compare=False, repr=False)
    artifact_store_root: str | None = None
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
    launch_claimed: bool = False
    launch_claim_owner: str | None = None
    launch_claim_until: float | None = None
    launch_published: bool = False
    startup_phase: str = "unknown"
    startup_lease_until: float | None = None
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
            "launch_claimed": self.launch_claimed,
            "launch_claim_owner": self.launch_claim_owner,
            "launch_claim_until": self.launch_claim_until,
            "launch_published": self.launch_published,
            "startup_phase": self.startup_phase,
            "startup_lease_until": self.startup_lease_until,
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
        boolean_fields = (
            "cancellation_requested",
            "launch_claimed",
            "launch_published",
            "result_prepared",
            "joined",
        )
        for field_name in boolean_fields:
            if type(value.get(field_name, False)) is not bool:
                raise ValueError(f"durable child state field {field_name!r} must be boolean")
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
            cancellation_requested=value.get("cancellation_requested", False),
            launch_claimed=value.get("launch_claimed", False),
            launch_claim_owner=str(value["launch_claim_owner"]) if value.get("launch_claim_owner") is not None else None,
            launch_claim_until=float(value["launch_claim_until"]) if value.get("launch_claim_until") is not None else None,
            launch_published=value.get("launch_published", False),
            startup_phase=str(value.get("startup_phase") or "unknown"),
            startup_lease_until=float(value["startup_lease_until"]) if value.get("startup_lease_until") is not None else None,
            cancellation_reason=value.get("cancellation_reason"),
            result_prepared=value.get("result_prepared", False),
            result_refs=tuple(str(ref) for ref in value.get("result_refs", ())),
            terminal_outcome=value.get("terminal_outcome"),
            terminal_count=int(value.get("terminal_count", 0)),
            joined=value.get("joined", False),
            settlement=value.get("settlement"),
            child_spec=value.get("child_spec") or {},
            execution_target=value.get("execution_target") or {},
        )


class ChildExecutionAdapter(Protocol):
    family: str
    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget: ...
    def observe(self, target: Mapping[str, Any]) -> str: ...
    def cancel(self, target: Mapping[str, Any]) -> bool | None: ...
    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | ArtifactRef | None: ...


def _sync(awaitable: Any) -> Any:
    if not hasattr(awaitable, "__await__"):
        return awaitable
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("synchronous child API cannot run inside an event loop")


class _RegistryThreadBridge:
    def __init__(self, registry: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._registry = registry
        self._loop = loop

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._registry, name)
        def invoke(*args: Any, **kwargs: Any) -> Any:
            result = method(*args, **kwargs)
            if not hasattr(result, "__await__"):
                return result
            return asyncio.run_coroutine_threadsafe(result, self._loop).result()
        return invoke



class DurableChildFactory:
    """One provider-neutral boundary over retained SessionRecord and owners."""
    _owner_locks: ClassVar[dict[str, threading.RLock]] = {}
    _owner_locks_guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, workspace: str | Path, *, registry: Any, repository: WorkItemRepository, adapters: Iterable[ChildExecutionAdapter], clock: Clock | None = None, ids: IdSource | None = None, artifact_store: ArtifactStore | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.repository = repository
        repository_path = getattr(repository, "_path", None)
        if not isinstance(repository_path, Path):
            raise ChildError("durable child WorkItemRepository must have a durable path")
        self._repository_path = repository_path.resolve()
        self.clock = clock or SystemClock()
        self.ids = ids or UUIDSource()
        self._owner_id = self.ids.new_id()
        self._lifecycle_lock = threading.RLock()
        self.artifacts = artifact_store or ArtifactStore(self.workspace / ".breadboard" / "artifacts")
        self.adapters = {adapter.family: adapter for adapter in adapters}
        for adapter in self.adapters.values():
            binder = getattr(adapter, "bind_workspace", None)
            if callable(binder) and adapter.family != "ray-agent-job":
                binder(self.workspace)
            artifact_binder = getattr(adapter, "bind_artifact_store", None)
            if callable(artifact_binder) and adapter.family != "ray-agent-job":
                artifact_binder(self.artifacts)
        if not self.adapters:
            raise ValueError("at least one child execution adapter is required")
    @classmethod
    def _owner_lock(cls, key: str) -> threading.RLock:
        with cls._owner_locks_guard:
            return cls._owner_locks.setdefault(key, threading.RLock())
    def cancel_tree(self, *, parent_session_id: str, parent_work_item_id: str, reason: str = "operator request") -> tuple[ChildState, ...]:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id), self._product_transition_guard(parent_session_id, parent_session_id):
            return self._cancel_tree(parent_session_id=parent_session_id, parent_work_item_id=parent_work_item_id, reason=reason)
    def _cancel_tree(self, *, parent_session_id: str, parent_work_item_id: str, reason: str = "operator request") -> tuple[ChildState, ...]:
        """Persist parent intent and every descendant intent before signaling."""
        parent, _ = load_session(self.workspace, parent_session_id)
        parent_work = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        records = self._registry("records")
        by_parent: dict[str, list[ChildState]] = {}
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            value = metadata.get("durable_child")
            if isinstance(value, Mapping):
                by_parent.setdefault(str(value.get("parent_work_item_id")), []).append(self._record_state(record.session_id))
        descendants: list[ChildState] = []
        queue = [parent_work_item_id] if parent_work.read_model.cancellation_policy.propagate_to_children else []
        while queue:
            parent_id = queue.pop(0)
            for state in by_parent.get(parent_id, ()):
                descendants.append(state)
                try:
                    child_work = WorkItem.restore(
                        self.repository,
                        state.child_work_item_id,
                        clock=self.clock,
                        ids=self.ids,
                    )
                    propagates = child_work.read_model.cancellation_policy.propagate_to_children
                except (FileNotFoundError, ValueError):
                    policy = CancellationPolicy.from_dict(state.child_spec["cancellation_policy"])
                    propagates = policy.propagate_to_children
                if propagates:
                    queue.append(state.child_work_item_id)
        product_status = parent.read_model.status
        work_status = parent_work.read_model.status
        if (
            product_status in _TERMINAL
            and work_status in _TERMINAL
            and product_status != work_status
        ):
            raise ChildError("parent Product Session and Work Item terminal outcomes disagree")
        if work_status not in _TERMINAL:
            policy = parent_work.read_model.cancellation_policy
            if policy.mode == "never" or "operator" not in policy.cancellable_by:
                raise ChildError("operator is not authorized to cancel the parent Work Item")
            current_attempt = parent_work.read_model.current_attempt
            if (
                current_attempt is not None
                and policy.cleanup == "checkpoint_then_stop"
                and current_attempt.checkpoint_ref is None
            ):
                raise ValueError("checkpoint_then_stop requires a current checkpoint")
        if any(state.settlement is not None for state in descendants if not state.terminal_count):
            raise ExpectedRevisionConflict("child settlement is already reserved")
        for state in descendants:
            if state.terminal_count or state.cancellation_requested:
                continue
            try:
                child = WorkItem.restore(
                    self.repository,
                    state.child_work_item_id,
                    clock=self.clock,
                    ids=self.ids,
                )
            except (FileNotFoundError, ValueError):
                child = None
            if child is not None and child.read_model.status in _TERMINAL:
                continue
            policy = (
                child.read_model.cancellation_policy
                if child is not None
                else CancellationPolicy.from_dict(state.child_spec["cancellation_policy"])
            )
            if policy.mode == "never" or "operator" not in policy.cancellable_by:
                raise ChildError("operator is not authorized to cancel a child Work Item")
            if child is not None:
                current_attempt = child.read_model.current_attempt
                if (
                    current_attempt is not None
                    and policy.cleanup == "checkpoint_then_stop"
                    and current_attempt.checkpoint_ref is None
                ):
                    raise ValueError(
                        "checkpoint_then_stop requires a current checkpoint"
                    )
        parent_record = self._registry("get", parent_session_id)
        parent_metadata = dict(parent_record.metadata or {}) if parent_record is not None else {}
        parent_metadata["durable_parent_cancellation"] = {
            "work_item_id": parent_work_item_id,
            "reason": reason,
            "child_recovery_refs": [state.recovery_ref for state in descendants],
        }
        if parent_record is not None:
            self._registry("update_metadata", parent_session_id, metadata=parent_metadata)
        if product_status in _TERMINAL and work_status not in _TERMINAL:
            try:
                if product_status == "completed":
                    attempt = parent_work.read_model.current_attempt
                    if attempt is None:
                        raise ChildError("completed Product Session has no active Work Item attempt")
                    parent_work.complete("Product Session already completed", attempt_id=attempt.attempt_id)
                elif product_status == "failed":
                    parent_work.fail("product_session", "Product Session already failed")
                else:
                    parent_work.cancel("operator", reason)
            except (RuntimeError, ValueError) as error:
                raise ChildError("Product Session terminal outcome cannot reconcile Work Item") from error
            work_status = parent_work.read_model.status
        elif work_status in _TERMINAL and product_status not in _TERMINAL:
            try:
                if work_status == "completed":
                    _mutate_session_locked(
                        self.workspace,
                        parent_session_id,
                        lambda current: current.complete("Work Item already completed"),
                    )
                elif work_status == "failed":
                    _mutate_session_locked(
                        self.workspace,
                        parent_session_id,
                        lambda current: current.fail(
                            "work_item",
                            "Work Item already failed",
                        ),
                    )
                else:
                    _mutate_session_locked(
                        self.workspace,
                        parent_session_id,
                        lambda current: current.cancel(reason),
                    )
            except (RuntimeError, ValueError) as error:
                raise ChildError("Work Item terminal outcome cannot reconcile Product Session") from error
            parent, _ = load_session(self.workspace, parent_session_id)
        elif (
            product_status in _TERMINAL
            and work_status in _TERMINAL
            and product_status != work_status
        ):
            raise ChildError("parent Product Session and Work Item terminal outcomes disagree")
        adopted: list[ChildState] = []
        remaining_descendants: list[ChildState] = []
        for state in descendants:
            if state.terminal_count:
                continue
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            if child.read_model.status in _TERMINAL and not state.cancellation_requested:
                adopted.append(self._adopt_terminal_work_item(state, child, allow_cancellation_intent=True))
            else:
                remaining_descendants.append(state)
        descendants = remaining_descendants
        pending = []
        for state in descendants:
            if state.terminal_count:
                continue
            if not state.cancellation_requested:
                state = self._cas(
                    state,
                    status="cancel_requested",
                    cancellation_requested=True,
                    cancellation_reason=reason,
                )
            pending.append(state)
        if parent_work.read_model.status not in _TERMINAL:
            parent_work.cancel("operator", reason)
        if parent.read_model.status not in _TERMINAL:
            _mutate_session_locked(self.workspace, parent_session_id, lambda current: current.cancel(reason))
            parent, _ = load_session(self.workspace, parent_session_id)
        settled = list(adopted)
        record = self._registry("get", parent_session_id)
        if record is not None:
            record.product_session = parent
            from breadboard_engine.api.cli_bridge.models import SessionStatus
            bridge_status = {
                "completed": SessionStatus.COMPLETED,
                "failed": SessionStatus.FAILED,
                "canceled": SessionStatus.STOPPED,
            }.get(parent.read_model.status)
            if bridge_status is not None:
                self._registry("update_status", parent_session_id, status=bridge_status)
        unsettled = False
        for state in pending:
            if self.adapters[state.adapter_family].cancel(state.execution_target) is False:
                unsettled = True
                settled.append(self._record_state(state.child_session_id))
                continue
            child_events = self.repository.read(state.child_work_item_id)
            if child_events:
                child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
                if child.read_model.current_attempt is None:
                    settled.append(self._cancel_unpublished_start(state, reason, signal=False))
                else:
                    settled.append(self._settle(state, "canceled", (), allow_unprepared=True))
            else:
                state = self._cas(state, status="canceled", terminal_outcome="canceled", terminal_count=1, settlement=None, joined=True)
                self._repair_terminal_owners(state)
                settled.append(state)
        if not unsettled:
            current_parent_record = self._registry("get", parent_session_id)
            if current_parent_record is not None:
                cleared_metadata = dict(current_parent_record.metadata or {})
                cleared_metadata["durable_parent_cancellation"] = None
                self._registry("update_metadata", parent_session_id, metadata=cleared_metadata)
        return tuple(settled)
    @contextmanager
    def _owner_process_lock(self, key: str):
        lock_path = self.workspace / ".breadboard" / f"child-owner-{hashlib.sha256(key.encode()).hexdigest()}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with ProcessLock(lock_path):
            yield

    def _registry(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return _sync(getattr(self.registry, method)(*args, **kwargs))

    @contextmanager
    def _product_transition_guard(self, parent_session_id: str, root_session_id: str):
        with ExitStack() as stack:
            for session_id in sorted({parent_session_id, root_session_id}):
                stack.enter_context(_session_transition_guard(self.workspace, session_id))
            yield

    def _record_state(self, child_session_id: str) -> ChildState:
        record = self._registry("get", child_session_id)
        if record is None:
            raise ChildError(f"retained child SessionRecord is missing: {child_session_id}")
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        value = metadata.get("durable_child")
        if not isinstance(value, Mapping):
            raise ChildError("SessionRecord has no durable child state")
        return ChildState.from_retained(value)
    def _require_start_active(self, child_session_id: str) -> ChildState:
        state = self._record_state(child_session_id)
        if state.terminal_count or state.cancellation_requested:
            raise ExpectedRevisionConflict("child startup was canceled before owner publication")
        return state
    def _parent_attempt_id_for_child(self, parent_work_item_id: str, child_work_item_id: str) -> str | None:
        for event in reversed(self.repository.read(parent_work_item_id)):
            if event.kind != "child.delegated":
                continue
            if event.payload.get("child_work_item_id") != child_work_item_id:
                continue
            attempt_id = event.payload.get("attempt_id")
            return attempt_id if isinstance(attempt_id, str) else None
        return None
    def _final_launch_fence(self, state: ChildState, parent_attempt_id: str | None = None) -> ChildState:
        current = self._record_state(state.child_session_id)
        parent_active = True
        try:
            self._require_parent_start_active(state.parent_session_id, state.root_session_id)
            parent_work = WorkItem.restore(
                self.repository,
                state.parent_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            expected_parent_attempt = parent_attempt_id or self._parent_attempt_id_for_child(
                state.parent_work_item_id,
                state.child_work_item_id,
            )
            parent_attempt = parent_work.read_model.current_attempt
            if (
                expected_parent_attempt is None
                or parent_work.read_model.status != "running"
                or parent_attempt is None
                or parent_attempt.attempt_id != expected_parent_attempt
            ):
                raise ChildError("parent Work Item attempt is no longer active")
        except (ChildError, FileNotFoundError, ValueError):
            parent_active = False
        try:
            product, _ = load_session(self.workspace, state.child_session_id)
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
        except (FileNotFoundError, ValueError) as error:
            raise ExpectedRevisionConflict("child owner became unavailable before launch") from error
        if (
            parent_active
            and not current.cancellation_requested
            and not current.terminal_count
            and product.read_model.status == "running"
            and child.read_model.status == "running"
            and child.read_model.current_attempt is not None
            and child.read_model.current_attempt.attempt_id == state.attempt_id
        ):
            return current
        if current.terminal_count:
            return current
        if child.read_model.status in _TERMINAL:
            return self._adopt_terminal_work_item(current, child)
        return self._cancel_unpublished_start(
            current,
            "child owner became terminal before launch",
            signal=False,
        )
    def _require_parent_work_start_active(self, parent_work_item_id: str, attempt_id: str) -> None:
        parent = WorkItem.restore(
            self.repository,
            parent_work_item_id,
            clock=self.clock,
            ids=self.ids,
        )
        attempt = parent.read_model.current_attempt
        if (
            parent.read_model.status != "running"
            or attempt is None
            or attempt.attempt_id != attempt_id
        ):
            raise ChildError("parent Work Item became terminal during child startup")
    def _require_parent_start_active(self, parent_session_id: str, root_session_id: str) -> None:
        for session_id, label in ((parent_session_id, "parent"), (root_session_id, "root")):
            record = self._registry("get", session_id)
            if record is not None and isinstance(record.metadata, Mapping) and isinstance(record.metadata.get("durable_parent_cancellation"), Mapping):
                raise ChildError(f"{label} Product Session cancellation is pending")
        parent_product, _ = load_session(self.workspace, parent_session_id)
        if parent_product.read_model.status != "running":
            raise ChildError("parent Product Session became terminal during child startup")
        if root_session_id != parent_session_id:
            root_product, _ = load_session(self.workspace, root_session_id)
            if root_product.read_model.status != "running":
                raise ChildError("root Product Session became terminal during child startup")


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
        self._repair_terminal_owners(failed)
        self._status(failed)
        return failed
    def _cancel_unpublished_start(self, state: ChildState, reason: str, *, signal: bool = True) -> ChildState:
        if not state.cancellation_requested:
            state = self._cas(state, status="cancel_requested", cancellation_requested=True, cancellation_reason=reason)
        if signal and self.adapters[state.adapter_family].cancel(state.execution_target) is False:
            return state
        state = self._cas(state, status="canceled", terminal_outcome="canceled", terminal_count=1, settlement=None, joined=True)
        self._repair_terminal_owners(state)
        self._status(state)
        return state
    def start(self, *, parent_session_id: str, root_session_id: str, parent_work_item_id: str, spec: ChildSpec) -> ChildActivation:
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id):
            return self._start(parent_session_id=parent_session_id, root_session_id=root_session_id, parent_work_item_id=parent_work_item_id, spec=spec)
    def _start(self, *, parent_session_id: str, root_session_id: str, parent_work_item_id: str, spec: ChildSpec) -> ChildActivation:
        if spec.adapter_family not in self.adapters:
            raise ChildError(f"child adapter family is not registered: {spec.adapter_family}")
        parent_product, _ = load_session(self.workspace, parent_session_id)
        if parent_product.read_model.status != "running":
            raise ChildError("parent Product Session is not running")
        if root_session_id != parent_session_id:
            root_product, _ = load_session(self.workspace, root_session_id)
            if root_product.read_model.status != "running":
                raise ChildError("root Product Session is not running")
        self._require_parent_start_active(parent_session_id, root_session_id)
        parent = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        if parent.read_model.status != "running" or parent.read_model.current_attempt is None:
            raise ChildError("parent Work Item is not running")
        parent_attempt_id = parent.read_model.current_attempt.attempt_id
        child_session_id = self.ids.new_id()
        child_work_item_id = self.ids.new_id()
        attempt_id = self.ids.new_id()
        recovery_ref = f"child://{child_session_id}/attempt/{attempt_id}"
        reserved = _reserved_target_ref(spec.adapter_family, child_session_id)
        child_spec = spec.retained()
        task_artifact = self.artifacts.put(spec.task.encode(), media_type="text/plain; charset=utf-8")
        child_spec["task_artifact_ref"] = task_artifact.as_dict()
        child_spec["task_artifact_store"] = str(self.artifacts._root)
        child_spec["artifact_store_root"] = str(self.artifacts._root)
        child_spec["work_item_repository_path"] = str(self._repository_path)
        config_fn = getattr(self.adapters[spec.adapter_family], "retained_config", None)
        config = config_fn() if callable(config_fn) else {}
        if not isinstance(config, Mapping):
            raise ChildError("child adapter config is not durable")
        child_spec["adapter_config"] = dict(config)
        execution_target: dict[str, Any] = {"ref": reserved}
        if spec.adapter_family == RayJobAdapter.family:
            execution_target["metadata"] = {"job": {"job_id": reserved.removeprefix("job:"), "agent_id": child_session_id, "owner_agent": parent_session_id, "kind": "agent", "state": "accepted", "seq": 0, "task_descriptor": {"child_session_id": child_session_id, "recovery_ref": recovery_ref, "task_hash": child_spec["task_hash"]}, "workspace": str(self.workspace), "artifact_store_root": str(self.artifacts._root)}}
        initial = ChildState(child_session_id, child_work_item_id, parent_session_id, root_session_id, parent_work_item_id, attempt_id, recovery_ref, reserved, spec.adapter_family, "starting", 0, startup_phase="recorded", startup_lease_until=time.time() + 30.0, child_spec=child_spec, execution_target=execution_target)
        self._create_record(initial)
        product = Session.start(spec.lock, spec.task, session_id=child_session_id, clock=self.clock, ids=self.ids)
        create_session(self.workspace, product)
        self._require_start_active(child_session_id)
        parent_product, _ = load_session(self.workspace, parent_session_id)
        parent = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        if (
            parent_product.read_model.status != "running"
            or parent.read_model.status != "running"
            or parent.read_model.current_attempt is None
        ):
            raise ChildError("parent owner became terminal during child startup")
        child = parent.delegate(
            spec.title,
            attempt_id=parent_attempt_id,
            child_work_item_id=child_work_item_id,
            retry_policy=spec.retry_policy,
            resume_policy=spec.resume_policy,
            cancellation_policy=spec.cancellation_policy,
        )
        state = self._cas(initial, startup_phase="delegated")
        parent_product, _ = load_session(self.workspace, parent_session_id)
        parent = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        if (
            parent_product.read_model.status != "running"
            or parent.read_model.status != "running"
            or parent.read_model.current_attempt.attempt_id != parent_attempt_id
        ):
            self._cancel_unpublished_start(state, "parent owner became terminal during child startup")
            raise ChildError("parent owner became terminal during child startup")
        state = self._cas(state, startup_phase="product_published")
        self._require_start_active(child_session_id)
        record = self._registry("get", child_session_id)
        if record is None:
            raise ChildError("retained child SessionRecord disappeared during start")
        record.product_session = product
        child.acquire_lease(spec.worker_id, lease_id=self.ids.new_id())
        self._require_start_active(child_session_id)
        child.start_attempt(
            child_session_id,
            lease_id=child.read_model.active_lease.lease_id,
            attempt_id=attempt_id,
        )  # type: ignore[union-attr]
        self._require_start_active(child_session_id)
        child.attach_placement(
            WorkPlacement(
                self.ids.new_id(),
                child_work_item_id,
                attempt_id,
                spec.worker_id,
                child_session_id,
                initial.execution_target_ref,
                self.clock.now(),
            )
        )
        state = self._cas(
            state,
            status="running",
            launch_claimed=True,
            launch_claim_owner=self._owner_id,
            launch_claim_until=time.time() + 30.0,
        )
        self._status(state)
        with self._product_transition_guard(parent_session_id, root_session_id):
            try:
                self._require_start_active(child_session_id)
                self._require_parent_start_active(parent_session_id, root_session_id)
                self._require_parent_work_start_active(parent_work_item_id, parent_attempt_id)
                state = self._final_launch_fence(state, parent_attempt_id)
                if state.terminal_count:
                    raise ExpectedRevisionConflict("child owner became terminal before launch")
            except (ChildError, ExpectedRevisionConflict):
                current = self._record_state(child_session_id)
                if not current.terminal_count:
                    self._cancel_unpublished_start(
                        current,
                        "parent owner became terminal during child startup",
                        signal=False,
                    )
                raise
            activation = ChildActivation(
                parent_session_id,
                root_session_id,
                parent_work_item_id,
                child_session_id,
                child_work_item_id,
                attempt_id,
                recovery_ref,
                initial.execution_target_ref,
                spec.adapter_family,
                str(self.workspace),
                artifact_store_root=str(self.artifacts._root),
            )
            state = self._launch(state, activation, spec)
            return ChildActivation(
                parent_session_id,
                root_session_id,
                parent_work_item_id,
                child_session_id,
                child_work_item_id,
                attempt_id,
                state.recovery_ref,
                state.execution_target_ref,
                spec.adapter_family,
                str(self.workspace),
                artifact_store_root=str(self.artifacts._root),
            )

    def prepare_result(
        self,
        child_session_id: str,
        *,
        expected_revision: int,
        result: bytes | ArtifactRef | None = None,
        attempt_id: str | None = None,
        _allow_cancellation_intent: bool = False,
    ) -> ChildState:
        if attempt_id is None:
            raise ExpectedRevisionConflict("result preparation requires an attempt identity")
        state = self._record_state(child_session_id)
        if attempt_id != state.attempt_id:
            raise ExpectedRevisionConflict("stale child attempt")
        if state.terminal_count:
            raise LateResultRejected("late result preparation cannot follow settlement")
        if state.cancellation_requested and not _allow_cancellation_intent:
            raise LateResultRejected("result preparation cannot follow cancellation intent")
        if state.settlement is not None:
            raise ExpectedRevisionConflict("child settlement is already reserved")
        if state.revision != expected_revision:
            raise ExpectedRevisionConflict(f"stale child revision: expected {expected_revision}, actual {state.revision}")
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
        return self._cas(state, result_prepared=True, result_refs=refs)

    def cancel(self, child_session_id: str, *, expected_revision: int, reason: str = "operator request") -> ChildState:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        parent_work_item_id = self._record_state(child_session_id).parent_work_item_id
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id):
            return self._cancel(child_session_id, expected_revision=expected_revision, reason=reason)
    def _cancel(self, child_session_id: str, *, expected_revision: int, reason: str = "operator request") -> ChildState:
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
        if state.status == "starting" and not self.repository.read(state.child_work_item_id):
            policy = CancellationPolicy.from_dict(state.child_spec["cancellation_policy"])
            if policy.mode == "never" or "operator" not in policy.cancellable_by:
                raise ChildError("operator is not authorized to cancel this Work Item")
            return self._cancel_unpublished_start(state, reason)
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        if child.read_model.status in _TERMINAL:
            return self._adopt_terminal_work_item(state, child)
        policy = child.read_model.cancellation_policy
        if policy.mode == "never" or "operator" not in policy.cancellable_by:
            raise ChildError("operator is not authorized to cancel this Work Item")
        current_attempt = child.read_model.current_attempt
        if (
            current_attempt is not None
            and policy.cleanup == "checkpoint_then_stop"
            and current_attempt.checkpoint_ref is None
        ):
            raise ValueError("checkpoint_then_stop requires a current checkpoint")
        if child.read_model.current_attempt is None:
            state = self._cas(
                state,
                status="cancel_requested",
                cancellation_requested=True,
                cancellation_reason=reason,
            )
            if self.adapters[state.adapter_family].cancel(state.execution_target) is False:
                return state
            return self._cancel_unpublished_start(state, reason, signal=False)
        state = self._cas(
            state,
            status="cancel_requested",
            cancellation_requested=True,
            cancellation_reason=reason,
        )
        if self.adapters[state.adapter_family].cancel(state.execution_target) is False:
            return state
        return self._settle(state, "canceled", (), allow_unprepared=True)
    def settle(
        self,
        child_session_id: str,
        *,
        expected_revision: int,
        outcome: str,
        result_refs: Sequence[str] = (),
        attempt_id: str | None = None,
        _allow_cancellation_intent: bool = False,
    ) -> ChildState:
        if attempt_id is None:
            raise ExpectedRevisionConflict("settlement requires an attempt identity")
        state = self._record_state(child_session_id)
        if attempt_id != state.attempt_id:
            raise ExpectedRevisionConflict("stale child attempt")
        if state.terminal_count:
            if state.terminal_outcome == outcome and tuple(result_refs) == state.result_refs:
                return state
            raise LateResultRejected("late child result cannot replace terminal outcome")
        if state.settlement is not None:
            raise ExpectedRevisionConflict("child settlement is already reserved")
        if state.cancellation_requested and outcome != "canceled" and not _allow_cancellation_intent:
            raise LateResultRejected("late child result arrived after cancellation intent")
        if outcome != "canceled" and not _allow_cancellation_intent:
            parent_record = self._registry("get", state.parent_session_id)
            parent_metadata = (
                parent_record.metadata
                if parent_record is not None and isinstance(parent_record.metadata, Mapping)
                else {}
            )
            if isinstance(parent_metadata.get("durable_parent_cancellation"), Mapping):
                raise LateResultRejected("child settlement cannot follow parent cancellation")
        if outcome not in _TERMINAL:
            raise ValueError("child outcome must be completed, failed, or canceled")
        if outcome == "completed" and not state.result_prepared:
            raise PreparationRequired("result/artifact preparation must precede settlement")
        if state.revision != expected_revision:
            raise ExpectedRevisionConflict(f"stale child revision: expected {expected_revision}, actual {state.revision}")
        if result_refs is not None and tuple(result_refs) != state.result_refs:
            raise ExpectedRevisionConflict("settlement result refs do not match prepared refs")
        reserved = self._cas(state, settlement={"outcome": outcome, "result_refs": list(state.result_refs)})
        return self._settle(reserved, outcome, state.result_refs, allow_unprepared=outcome != "completed")

    def _settle(self, state: ChildState, outcome: str, result_refs: Sequence[str], *, allow_unprepared: bool) -> ChildState:
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        work_status = child.read_model.status
        latest_attempt = child.read_model.attempts[-1] if child.read_model.attempts else None
        if latest_attempt is None or latest_attempt.attempt_id != state.attempt_id:
            raise ExpectedRevisionConflict("settlement attempt does not match retained child attempt")
        if work_status in _TERMINAL and work_status != outcome:
            raise ChildError("Work Item terminal outcome disagrees with settlement")
        if work_status not in _TERMINAL and child.read_model.current_attempt is None:
            raise ChildError("child Work Item has no active attempt")
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
                mutate_session(self.workspace, state.child_session_id, lambda current: current.cancel(state.cancellation_reason or "operator request"))
        session, _ = load_session(self.workspace, state.child_session_id)
        record = self._registry("get", state.child_session_id)
        if record is not None:
            record.product_session = session
        if work_status not in _TERMINAL:
            attempt = child.read_model.current_attempt
            if outcome == "completed":
                child.complete("child result prepared", attempt_id=attempt.attempt_id)  # type: ignore[union-attr]
            elif outcome == "failed":
                child.fail_attempt("execution target exited", attempt_id=attempt.attempt_id, retryable=False)  # type: ignore[union-attr]
            else:
                child.cancel("operator", state.cancellation_reason or "operator request")
        parent_work = WorkItem.restore(self.repository, state.parent_work_item_id, clock=self.clock, ids=self.ids)
        parent_work.join_child(state.child_work_item_id, state.child_session_id, outcome, result_refs)
        state = self._cas(state, status=outcome, terminal_outcome=outcome, terminal_count=1, result_refs=tuple(result_refs), settlement=None, joined=True)
        self._status(state)
        return state

    def _adopt_terminal_work_item(
        self,
        state: ChildState,
        child: WorkItem,
        *,
        allow_cancellation_intent: bool = False,
    ) -> ChildState:
        """Join an already-terminal Work Item without issuing a late cancel."""
        outcome = child.read_model.status
        if outcome not in _TERMINAL:
            raise ValueError("terminal Work Item adoption requires a terminal outcome")
        session, _ = load_session(self.workspace, state.child_session_id)
        if session.read_model.status in _TERMINAL and session.read_model.status != outcome:
            raise ChildError("Product Session terminal outcome disagrees with Work Item")
        latest_attempt = child.read_model.attempts[-1] if child.read_model.attempts else None
        if latest_attempt is None:
            adopted = self._cas(
                state,
                status=outcome,
                terminal_outcome=outcome,
                terminal_count=1,
                result_refs=state.result_refs,
                settlement=None,
                joined=True,
            )
            self._repair_terminal_owners(adopted)
            self._status(adopted)
            return adopted
        if latest_attempt.attempt_id != state.attempt_id:
            raise ExpectedRevisionConflict("terminal Work Item attempt does not match retained child")
        if outcome == "completed" and not state.result_prepared:
            state = self.prepare_result(
                state.child_session_id,
                expected_revision=state.revision,
                attempt_id=state.attempt_id,
                _allow_cancellation_intent=allow_cancellation_intent,
            )
        return self.settle(
            state.child_session_id,
            expected_revision=state.revision,
            outcome=outcome,
            result_refs=state.result_refs,
            attempt_id=state.attempt_id,
            _allow_cancellation_intent=allow_cancellation_intent,
        )
    def _reserved_execution_target(self, state: ChildState, target_ref: str) -> dict[str, Any]:
        target: dict[str, Any] = {"ref": target_ref}
        if state.adapter_family == RayJobAdapter.family:
            target["metadata"] = {
                "job": {
                    "job_id": target_ref.removeprefix("job:"),
                    "agent_id": state.child_session_id,
                    "owner_agent": state.parent_session_id,
                    "kind": "agent",
                    "state": "accepted",
                    "seq": 0,
                    "task_descriptor": {
                        "child_session_id": state.child_session_id,
                        "recovery_ref": state.recovery_ref,
                        "task_hash": state.child_spec["task_hash"],
                    },
                    "workspace": str(self.workspace),
                    "artifact_store_root": str(self.artifacts._root),
                }
            }
        return target
    def _publish_target(self, state: ChildState, target: ExecutionTarget) -> ChildState:
        retained_target = target.retained()
        retained_target["ref"] = state.execution_target_ref
        next_state = replace(state, revision=state.revision + 1, launch_published=True, launch_claim_owner=None, launch_claim_until=None, execution_target=retained_target)
        try:
            self._registry("update_durable_child", state.child_session_id, expected_revision=state.revision, child_state=next_state.retained())
        except RuntimeError as error:
            raise ExpectedRevisionConflict(str(error)) from error
        return next_state

    def _launch(self, state: ChildState, activation: ChildActivation, spec: ChildSpec) -> ChildState:
        state = self._final_launch_fence(state)
        if state.terminal_count:
            return state
        adapter = self.adapters[state.adapter_family]
        published: list[ChildState] = []
        published_state = state

        def publish(target: ExecutionTarget) -> None:
            nonlocal published_state
            published_state = self._publish_target(published_state, target)
            published.append(published_state)
        target = adapter.start(replace(activation, publish_target=publish), spec)
        if published:
            return published[-1]
        try:
            return self._publish_target(state, target)
        except ExpectedRevisionConflict:
            adapter.cancel(target.retained())
            raise


    def _retry(self, state: ChildState, child: WorkItem) -> ChildState:
        snapshot = child.read_model
        if snapshot.status in _TERMINAL:
            return self._adopt_terminal_work_item(state, child)
        if snapshot.status in {"waiting", "paused", "blocked"}:
            return state
        if snapshot.status not in {"running", "ready", "leased"}:
            raise ChildError(f"cannot relaunch child Work Item from {snapshot.status}")
        attempt = snapshot.current_attempt
        reason = "execution target exited"
        existing_attempt = attempt is not None and attempt.attempt_id != state.attempt_id
        if existing_attempt:
            next_attempt = attempt.attempt_id  # type: ignore[union-attr]
            if attempt.session_ref != state.child_session_id:  # type: ignore[union-attr]
                raise ChildError("retained retry attempt session reference disagrees with child session")
            next_session_ref = state.child_session_id
            placement = next((row for row in snapshot.placements if row.attempt_id == next_attempt), None)
            reserved = placement.execution_target_ref if placement is not None else _reserved_target_ref(state.adapter_family, f"{state.child_session_id}:{next_attempt}")
            if placement is None:
                child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, next_attempt, state.child_spec["worker_id"], next_session_ref, reserved, self.clock.now()))
            next_recovery = f"child://{state.child_session_id}/attempt/{next_attempt}"
            state = self._cas(state, attempt_id=next_attempt, recovery_ref=state.recovery_ref, execution_target_ref=reserved, execution_target=self._reserved_execution_target(state, reserved), status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0, launch_published=False, result_prepared=False, result_refs=(), settlement=None)
        elif attempt is not None:
            if not child.read_model.retry_policy.allows(reason) or len(child.read_model.attempts) >= child.read_model.retry_policy.max_attempts:
                return self._settle(state, "failed", (), allow_unprepared=True)
            child.fail_attempt(reason, attempt_id=attempt.attempt_id, retryable=True)
            next_attempt = self.ids.new_id()
            lease_id = self.ids.new_id()
            child.acquire_lease(state.child_spec["worker_id"], lease_id=lease_id)
            next_session_ref = state.child_session_id
            child.start_attempt(next_session_ref, lease_id=lease_id, attempt_id=next_attempt, reuse_session_ref=True)
            reserved = _reserved_target_ref(state.adapter_family, f"{state.child_session_id}:{next_attempt}")
            child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, next_attempt, state.child_spec["worker_id"], next_session_ref, reserved, self.clock.now()))
            next_recovery = f"child://{state.child_session_id}/attempt/{next_attempt}"
            state = self._cas(state, attempt_id=next_attempt, recovery_ref=state.recovery_ref, execution_target_ref=reserved, execution_target=self._reserved_execution_target(state, reserved), status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0, launch_published=False, result_prepared=False, result_refs=(), settlement=None)
        elif snapshot.status in {"ready", "leased"}:
            next_attempt = self.ids.new_id()
            if snapshot.status == "ready":
                lease_id = self.ids.new_id()
                child.acquire_lease(state.child_spec["worker_id"], lease_id=lease_id)
            else:
                lease_id = snapshot.active_lease.lease_id  # type: ignore[union-attr]
            next_session_ref = state.child_session_id
            child.start_attempt(next_session_ref, lease_id=lease_id, attempt_id=next_attempt, reuse_session_ref=True)
            reserved = _reserved_target_ref(state.adapter_family, f"{state.child_session_id}:{next_attempt}")
            child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, next_attempt, state.child_spec["worker_id"], next_session_ref, reserved, self.clock.now()))
            next_recovery = f"child://{state.child_session_id}/attempt/{next_attempt}"
            state = self._cas(state, attempt_id=next_attempt, recovery_ref=state.recovery_ref, execution_target_ref=reserved, execution_target=self._reserved_execution_target(state, reserved), status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0, launch_published=False, result_prepared=False, result_refs=(), settlement=None)
        self._status(state)
        activation = ChildActivation(
            state.parent_session_id,
            state.root_session_id,
            state.parent_work_item_id,
            state.child_session_id,
            state.child_work_item_id,
            state.attempt_id,
            state.recovery_ref,
            state.execution_target_ref,
            state.adapter_family,
            str(self.workspace),
            artifact_store_root=str(self.artifacts._root),
        )
        adapter = self.adapters[state.adapter_family]
        recover = getattr(adapter, "recover", None)
        if callable(recover):
            target = recover(state.execution_target)
            if target is not None:
                return self._publish_target(state, target)
        return self._launch(state, activation, self._spec(state))
    def _repair_terminal_owners(self, state: ChildState) -> None:
        outcome = state.terminal_outcome or state.status
        try:
            session, _ = load_session(self.workspace, state.child_session_id)
        except FileNotFoundError:
            spec = self._spec(state)
            product = Session.start(spec.lock, spec.task, session_id=state.child_session_id, clock=self.clock, ids=self.ids)
            create_session(self.workspace, product)
            session, _ = load_session(self.workspace, state.child_session_id)
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
                    mutate_session(self.workspace, state.child_session_id, lambda current: current.cancel(state.cancellation_reason or "operator request"))
            session, _ = load_session(self.workspace, state.child_session_id)
            record = self._registry("get", state.child_session_id)
            if record is not None:
                record.product_session = session
        try:
            child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        except (ChildError, ValueError):
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
                        child.cancel("operator", state.cancellation_reason or "operator request")
                elif outcome == "failed":
                    child.fail("child_startup", state.cancellation_reason or "startup interrupted")
                else:
                    child.cancel("operator", state.cancellation_reason or "operator request")
        parent_work = WorkItem.restore(self.repository, state.parent_work_item_id, clock=self.clock, ids=self.ids)
        if state.child_work_item_id in parent_work.read_model.child_work_item_ids:
            parent_work.join_child(state.child_work_item_id, state.child_session_id, outcome, state.result_refs)
    def reconcile(self, recovery_ref: str) -> ChildState:
        child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
        parent_work_item_id = self._record_state(child_session_id).parent_work_item_id
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id):
            return self._reconcile(recovery_ref)
    def _reconcile(self, recovery_ref: str) -> ChildState:
        child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
        state = self._record_state(child_session_id)
        if state.recovery_ref != recovery_ref:
            raise ExpectedRevisionConflict("stale child recovery reference")
        if state.terminal_count:
            self._repair_terminal_owners(state)
            self._status(state)
            return state
        if state.cancellation_requested:
            child_events = self.repository.read(state.child_work_item_id)
            if not child_events:
                return self._cancel_unpublished_start(
                    state,
                    state.cancellation_reason or "operator request",
                )
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            if child.read_model.status in _TERMINAL:
                return self._adopt_terminal_work_item(
                    state,
                    child,
                    allow_cancellation_intent=True,
                )
            if child.read_model.current_attempt is None:
                if self.adapters[state.adapter_family].cancel(state.execution_target) is False:
                    return state
                return self._cancel_unpublished_start(
                    state,
                    state.cancellation_reason or "operator request",
                    signal=False,
                )
        if state.status == "starting":
            if not self.repository.read(state.child_work_item_id):
                if state.cancellation_requested:
                    return self._cancel_unpublished_start(state, state.cancellation_reason or "operator request")
                if state.startup_phase == "recorded" and state.startup_lease_until is not None and state.startup_lease_until > time.time():
                    return state
                return self._abort_startup(state)
            try:
                load_session(self.workspace, child_session_id)
            except FileNotFoundError:
                spec = self._spec(state)
                product = Session.start(spec.lock, spec.task, session_id=child_session_id, clock=self.clock, ids=self.ids)
                create_session(self.workspace, product)
                record = self._registry("get", child_session_id)
                if record is not None:
                    record.product_session = product
            except ValueError:
                return self._abort_startup(state)
            child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
            if child.read_model.status in _TERMINAL:
                return self._adopt_terminal_work_item(state, child)
            try:
                parent_attempt_id = self._parent_attempt_id_for_child(
                    state.parent_work_item_id,
                    state.child_work_item_id,
                )
                if parent_attempt_id is None:
                    raise ChildError("retained child has no parent Work Item attempt")
                self._require_parent_start_active(
                    state.parent_session_id,
                    state.root_session_id,
                )
                self._require_parent_work_start_active(
                    state.parent_work_item_id,
                    parent_attempt_id,
                )
            except (ChildError, FileNotFoundError, ValueError):
                return self._cancel_unpublished_start(
                    state,
                    "parent owner became terminal during child recovery",
                    signal=False,
                )
            if child.read_model.current_attempt is None:
                child.acquire_lease(state.child_spec["worker_id"], lease_id=self.ids.new_id())
                child.start_attempt(child_session_id, lease_id=child.read_model.active_lease.lease_id, attempt_id=state.attempt_id)  # type: ignore[union-attr]
            if not any(placement.attempt_id == state.attempt_id for placement in child.read_model.placements):
                child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, state.attempt_id, state.child_spec["worker_id"], child_session_id, state.execution_target_ref, self.clock.now()))
            state = self._cas(state, status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0)
            self._status(state)
            if not state.cancellation_requested:
                state = self._final_launch_fence(state)
                if state.terminal_count:
                    return state
                activation = ChildActivation(
                    state.parent_session_id,
                    state.root_session_id,
                    state.parent_work_item_id,
                    state.child_session_id,
                    state.child_work_item_id,
                    state.attempt_id,
                    state.recovery_ref,
                    state.execution_target_ref,
                    state.adapter_family,
                    str(self.workspace),
                    artifact_store_root=str(self.artifacts._root),
                )
                adapter = self.adapters[state.adapter_family]
                if isinstance(adapter, UnavailableChildAdapter):
                    return self._settle(state, "failed", (), allow_unprepared=True)
                state = self._launch(state, activation, self._spec(state))
        if (
            state.status == "running"
            and state.launch_claimed
            and not state.cancellation_requested
            and not state.launch_published
            and _is_reserved_target_ref(
                state.adapter_family,
                state.execution_target_ref,
                state.child_session_id,
            )
        ):
            if state.launch_claim_owner not in {None, self._owner_id} and (
                state.launch_claim_until or 0.0
            ) > time.time():
                return state
            adapter = self.adapters[state.adapter_family]
            if isinstance(adapter, UnavailableChildAdapter):
                return self._settle(state, "failed", (), allow_unprepared=True)
            if state.launch_claim_owner != self._owner_id:
                state = self._cas(
                    state,
                    launch_claim_owner=self._owner_id,
                    launch_claim_until=time.time() + 30.0,
                )
            state = self._final_launch_fence(state)
            if state.terminal_count:
                return state
            activation = ChildActivation(
                state.parent_session_id,
                state.root_session_id,
                state.parent_work_item_id,
                state.child_session_id,
                state.child_work_item_id,
                state.attempt_id,
                state.recovery_ref,
                state.execution_target_ref,
                state.adapter_family,
                str(self.workspace),
                artifact_store_root=str(self.artifacts._root),
            )
            recover = getattr(adapter, "recover", None)
            target = recover(state.execution_target) if callable(recover) else None
            if target is not None:
                state = self._publish_target(state, target)
            else:
                try:
                    observed = str(adapter.observe(state.execution_target)).lower()
                except BaseException:
                    return state
                if observed != "absent":
                    return state
                state = self._launch(state, activation, self._spec(state))
        if state.status == "running":
            try:
                product, _ = load_session(self.workspace, child_session_id)
            except FileNotFoundError:
                product = None
            if product is not None:
                record = self._registry("get", child_session_id)
                if record is not None:
                    record.product_session = product
                self._status(state)
        if state.settlement:
            payload = state.settlement
            return self._settle(state, str(payload["outcome"]), tuple(str(ref) for ref in payload.get("result_refs", ())), allow_unprepared=True)
        if state.cancellation_requested:
            if self.adapters[state.adapter_family].cancel(state.execution_target) is False:
                return state
            return self._settle(state, "canceled", (), allow_unprepared=True)
        observed = str(self.adapters[state.adapter_family].observe(state.execution_target)).lower()
        if observed == "pending":
            return state
        if observed in {"running", "started", "live", "accepted"}:
            adapter = self.adapters[state.adapter_family]
            release_pending = getattr(adapter, "release_pending", None)
            if callable(release_pending) and release_pending(state.execution_target):
                if adapter.cancel(state.execution_target) is False:
                    return state
                state = self._cas(
                    state,
                    execution_target=self._reserved_execution_target(state, state.execution_target_ref),
                    launch_claimed=True,
                    launch_claim_owner=self._owner_id,
                    launch_claim_until=time.time() + 30.0,
                    launch_published=False,
                )
                activation = ChildActivation(state.parent_session_id, state.root_session_id, state.parent_work_item_id, state.child_session_id, state.child_work_item_id, state.attempt_id, state.recovery_ref, state.execution_target_ref, state.adapter_family, str(self.workspace), artifact_store_root=str(self.artifacts._root))
                return self._launch(state, activation, self._spec(state))
            recover = getattr(adapter, "recover", None)
            if callable(recover):
                target = recover(state.execution_target)
                if target is not None and target.retained() != state.execution_target:
                    recovered_target = target.retained()
                    if "metadata" not in recovered_target and "metadata" in state.execution_target:
                        recovered_target["metadata"] = dict(state.execution_target["metadata"])
                    state = self._cas(state, execution_target=recovered_target)
            metadata = state.execution_target.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("launch_phase") == "pending":
                released_target = dict(state.execution_target)
                released_metadata = dict(metadata)
                released_metadata["launch_phase"] = "released"
                released_target["metadata"] = released_metadata
                state = self._cas(state, execution_target=released_target)
            return state
        if observed == "completed":
            state = self._cas(state, execution_target=state.execution_target)
            acknowledge = getattr(self.adapters[state.adapter_family], "acknowledge_result", None)
            if callable(acknowledge):
                acknowledge(state.execution_target)
            if not state.result_prepared:
                state = self.prepare_result(child_session_id, expected_revision=state.revision, attempt_id=state.attempt_id)
            return self.settle(child_session_id, expected_revision=state.revision, outcome="completed", result_refs=state.result_refs, attempt_id=state.attempt_id)
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        adapter = self.adapters[state.adapter_family]
        if observed == "absent" and getattr(adapter, "absence_is_terminal", False):
            if child.read_model.status in _TERMINAL:
                return self._adopt_terminal_work_item(state, child)
            return self._settle(state, "failed", (), allow_unprepared=True)
        return self._retry(state, child)
    def _spec(self, state: ChildState) -> ChildSpec:
        value = state.child_spec
        task: str | None = None
        task_artifact = value.get("task_artifact_ref")
        if isinstance(task_artifact, Mapping):
            try:
                artifact = ArtifactRef(
                    str(task_artifact["digest"]),
                    int(task_artifact["size_bytes"]),
                    str(task_artifact["media_type"]),
                )
                store_root = value.get("task_artifact_store")
                store = ArtifactStore(Path(str(store_root))) if isinstance(store_root, str) and store_root.strip() else self.artifacts
                task = store.read(artifact).decode("utf-8")
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, OSError) as error:
                raise ChildError("durable child task artifact is unavailable") from error
        if task is None:
            try:
                product, _ = load_session(self.workspace, state.child_session_id)
            except (FileNotFoundError, ValueError) as error:
                raise ChildError("durable child Product Session task is unavailable") from error
            task = product.task
        if not isinstance(task, str) or not task.strip():
            raise ChildError("durable child Product Session has no retained task")
        task_hash = "sha256:" + hashlib.sha256(task.encode()).hexdigest()
        if task_hash != value.get("task_hash") or value.get("adapter_family") not in self.adapters:
            raise ChildError("durable child Product Session task does not match retained state")
        return ChildSpec(
            str(value["title"]),
            task,
            EffectiveHarnessLock._from_record({"graph_hash": str(value["lock_hash"])}),
            str(value["worker_id"]),
            str(value["adapter_family"]),
            RetryPolicy.from_dict(value["retry_policy"]),
            ResumePolicy.from_dict(value["resume_policy"]),
            CancellationPolicy.from_dict(value["cancellation_policy"]),
        )

class UnavailableChildAdapter:
    """Explicit terminal adapter for a retained family unavailable after restart."""

    absence_is_terminal = True

    def __init__(self, family: str) -> None:
        self.family = family

    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        raise ChildError(f"child execution adapter is unavailable: {self.family}")

    def observe(self, target: Mapping[str, Any]) -> str:
        return "absent"

    def cancel(self, target: Mapping[str, Any]) -> None:
        return None

    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | ArtifactRef | None:
        return None


class DurableChildReconciler:
    """Production restart boundary for retained child SessionRecords.

    The factory is synchronous because its existing owners expose synchronous
    CAS APIs.  Service startup explicitly offloads that bounded reconciliation
    to a worker thread rather than skipping the retained child.
    """

    def __init__(
        self,
        *,
        registry: Any,
        repository: WorkItemRepository,
        adapters: Iterable[ChildExecutionAdapter] = (),
        adapter_factories: Iterable[Any] = (),
    ) -> None:
        self.registry = registry
        self.repository = repository
        self._adapters = tuple(adapters)
        self._adapter_factories = tuple(adapter_factories)

    async def _build_factory(self, recovery_ref: str) -> DurableChildFactory:
        child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
        record = self.registry.get(child_session_id)
        if hasattr(record, "__await__"):
            record = await record
        if record is None:
            raise ChildError(f"retained child SessionRecord is missing: {child_session_id}")
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        workspace = metadata.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ChildError("durable child retained state has no workspace")
        retained = metadata.get("durable_child")
        family = str(retained.get("adapter_family") or "") if isinstance(retained, Mapping) else ""
        child_spec = retained.get("child_spec") if isinstance(retained, Mapping) else None
        adapter_config = child_spec.get("adapter_config") if isinstance(child_spec, Mapping) else {}
        artifact_store_root = child_spec.get("artifact_store_root") if isinstance(child_spec, Mapping) else None
        if artifact_store_root is not None and (
            not isinstance(artifact_store_root, str) or not artifact_store_root.strip()
        ):
            raise ChildError("durable child artifact store identity is malformed")
        if not isinstance(adapter_config, Mapping):
            raise ChildError("durable child adapter config is unavailable")
        repository_path = child_spec.get("work_item_repository_path") if isinstance(child_spec, Mapping) else None
        if not isinstance(repository_path, str) or not repository_path.strip():
            raise ChildError("durable child WorkItemRepository identity is unavailable")
        repository_path = Path(repository_path).expanduser().resolve()
        if not repository_path.is_file():
            raise ChildError("durable child WorkItemRepository is unavailable")
        configured_repository_path = getattr(self.repository, "_path", None)
        repository = (
            self.repository
            if isinstance(configured_repository_path, Path)
            and configured_repository_path.resolve() == repository_path
            else WorkItemRepository(repository_path)
        )
        adapters = []
        for factory in self._adapter_factories:
            if family == ProcessExecutionAdapter.family and factory is ProcessExecutionAdapter:
                command = adapter_config.get("command")
                if not isinstance(command, list) or not command or any(type(part) is not str or not part for part in command):
                    raise ChildError("durable process child command is malformed")
                adapters.append(factory(command=tuple(command)))
            else:
                adapters.append(factory())
        adapters.extend(self._adapters)
        if family and not any(adapter.family == family for adapter in adapters):
            adapters.append(UnavailableChildAdapter(family))
        loop = asyncio.get_running_loop()
        artifact_store = ArtifactStore(Path(artifact_store_root)) if isinstance(artifact_store_root, str) else None
        return DurableChildFactory(workspace, registry=_RegistryThreadBridge(self.registry, loop), repository=repository, adapters=adapters, artifact_store=artifact_store)
    async def __call__(self, recovery_ref: str) -> ChildState:
        factory = await self._build_factory(recovery_ref)
        return await asyncio.to_thread(factory.reconcile, recovery_ref)

    async def cancel(self, recovery_ref: str, *, reason: str = "operator request") -> ChildState:
        factory = await self._build_factory(recovery_ref)
        child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
        state = await asyncio.to_thread(factory._record_state, child_session_id)
        return await asyncio.to_thread(factory.cancel, child_session_id, expected_revision=state.revision, reason=reason)
    async def cancel_tree(self, parent_session_id: str, *, reason: str = "operator request") -> tuple[ChildState, ...]:
        records = self.registry.records()
        if hasattr(records, "__await__"):
            records = await records
        parent_work_item_ids: dict[str, str] = {}
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
            retained = metadata.get("durable_child")
            if not isinstance(retained, Mapping) or retained.get("parent_session_id") != parent_session_id:
                continue
            parent_work_item_id = str(retained.get("parent_work_item_id") or "").strip()
            recovery_ref = str(retained.get("recovery_ref") or "").strip()
            if parent_work_item_id and recovery_ref:
                parent_work_item_ids.setdefault(parent_work_item_id, recovery_ref)
        settled: list[ChildState] = []
        for parent_work_item_id, recovery_ref in parent_work_item_ids.items():
            factory = await self._build_factory(recovery_ref)
            result = await asyncio.to_thread(
                factory.cancel_tree,
                parent_session_id=parent_session_id,
                parent_work_item_id=parent_work_item_id,
                reason=reason,
            )
            settled.extend(result)
        return tuple(settled)


class RayJobAdapter:
    family = "ray-agent-job"
    absence_is_terminal = True

    def __init__(self, orchestrator: Any, *, actor_launcher: Any | None = None) -> None:
        self.orchestrator = orchestrator
        self._default_actor_launcher = actor_launcher is None
        self._actor_launcher = self._launch_actor if self._default_actor_launcher else actor_launcher
        self._actors: dict[str, Any] = {}
        self._workspace: Path | None = None

    def bind_workspace(self, workspace: Path) -> None:
        self._workspace = workspace

    @staticmethod
    def _actor_name(job_id: str) -> str:
        return "bb-child-" + job_id.replace(":", "_")

    @staticmethod
    def _invocation_id(job_id: str) -> str:
        return "child-invocation:" + job_id

    @staticmethod
    def _submit_invocation(actor: Any, invocation_id: str, task: str) -> bool:
        submit = getattr(actor, "submit_message_once", None)
        if submit is None:
            return False
        parts = [{"type": "text", "text": task}]
        remote = getattr(submit, "remote", None)
        if callable(remote):
            remote(invocation_id, parts)
        else:
            submit(invocation_id, parts)
        return True

    @staticmethod
    def _invocation_state(actor: Any, invocation_id: str) -> str | None:
        getter = getattr(actor, "get_invocation_state", None)
        if getter is None:
            return None
        remote = getattr(getter, "remote", None)
        try:
            value = remote(invocation_id) if callable(remote) else getter(invocation_id)
            if callable(remote):
                import ray
                value = ray.get(value)
        except BaseException:
            return None
        return str(value) if isinstance(value, str) else None

    def _launch_actor(
        self, job_id: str, workspace: Path, task: str, artifact_store_root: str | None = None
    ) -> Any:
        import ray
        from breadboard_engine.orchestration.agent_session import OpenCodeAgent

        name = self._actor_name(job_id)
        created = False
        try:
            actor = ray.get_actor(name)
        except ValueError:
            root = artifact_store_root or str(workspace / ".breadboard" / "artifacts")
            actor = OpenCodeAgent.options(name=name, lifetime="detached").remote(
                str(workspace), artifact_store_root=root
            )
            created = True
        if created:
            actor.run_message.remote([{"type": "text", "text": task}])
        return actor

    def _lookup_actor(self, job_id: str) -> Any | None:
        actor = self._actors.get(job_id)
        if actor is not None:
            return actor
        try:
            import ray
            actor = ray.get_actor(self._actor_name(job_id))
        except (ImportError, ValueError):
            return None
        self._actors[job_id] = actor
        return actor
    def recover(self, target: Mapping[str, Any]) -> ExecutionTarget | None:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        actor = self._lookup_actor(job_id)
        job = self.orchestrator.job_manager.get(job_id)
        metadata = target.get("metadata")
        if not isinstance(metadata, Mapping):
            return None
        job_data = metadata.get("job")
        if not isinstance(job_data, Mapping) or job_data.get("job_id") != job_id:
            return None
        if actor is None and (job is None or job.state not in {"completed", "failed", "killed"}):
            return None
        return ExecutionTarget(str(target.get("ref") or ""), volatile_handle=actor, metadata=dict(metadata))


    def _restore_job(self, target: Mapping[str, Any], job_id: str) -> None:
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if not isinstance(job_data, Mapping) or job_data.get("job_id") != job_id:
            return
        if self.orchestrator.job_manager.get(job_id) is not None:
            return
        from breadboard_engine.orchestration.job_manager import JobRef
        try:
            job = JobRef(
                job_id=job_id,
                agent_id=str(job_data["agent_id"]),
                owner_agent=str(job_data["owner_agent"]),
                kind=str(job_data["kind"]),
                state=str(job_data.get("state") or "accepted"),
                seq=int(job_data.get("seq") or 0),
                task_descriptor=dict(job_data.get("task_descriptor") or {}),
                result_payload=dict(job_data["result_payload"]) if isinstance(job_data.get("result_payload"), Mapping) else None,
            )
        except (KeyError, TypeError, ValueError):
            return
        self.orchestrator.job_manager.restore_job(job)


    @staticmethod
    def _ray_get(value: Any) -> Any:
        if hasattr(value, "remote"):
            import ray
            return ray.get(value.remote())
        return value() if callable(value) else value
    def _artifact_store(self, target: Mapping[str, Any]) -> ArtifactStore:
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        root = job_data.get("artifact_store_root") if isinstance(job_data, Mapping) else None
        if not isinstance(root, str) or not root.strip():
            workspace = job_data.get("workspace") if isinstance(job_data, Mapping) else None
            root = str(Path(workspace) / ".breadboard" / "artifacts") if isinstance(workspace, str) and workspace.strip() else ""
        if not root.strip():
            raise ChildError("Ray child target has no durable artifact store")
        return ArtifactStore(Path(root))

    def _durably_prepare_result(self, target: Mapping[str, Any], payload: Mapping[str, Any]) -> Mapping[str, Any]:
        store = self._artifact_store(target)
        ref = payload.get("artifact_ref")
        if isinstance(ref, Mapping):
            artifact = ArtifactRef(str(ref["digest"]), int(ref["size_bytes"]), str(ref["media_type"]))
            store.read(artifact)
            return {"artifact_ref": artifact.as_dict()}
        value = payload.get("result_bytes")
        if not isinstance(value, bytes):
            result = payload.get("result")
            if isinstance(result, str):
                value = result.encode()
            elif isinstance(result, Mapping):
                value = json.dumps(dict(result), sort_keys=True).encode()
            else:
                raise ChildError("completed Ray child result has no durable payload")
        artifact = store.put(value, media_type="application/octet-stream")
        return {"artifact_ref": artifact.as_dict()}


    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        target_ref = activation.execution_target_ref
        job_id = target_ref.removeprefix("job:")
        workspace = Path(activation.workspace) if activation.workspace is not None else self._workspace
        if workspace is None:
            raise ChildError("Ray child adapter is not bound to a workspace")
        artifact_store_root = activation.artifact_store_root or str(workspace / ".breadboard" / "artifacts")
        task_descriptor = {
            "child_session_id": activation.child_session_id,
            "recovery_ref": activation.recovery_ref,
            "task_hash": spec.retained()["task_hash"],
        }
        job = self.orchestrator.job_manager.get(job_id)
        if job is None:
            job = self.orchestrator.spawn_subagent(
                owner_agent=activation.parent_session_id,
                agent_id=activation.child_session_id,
                async_mode=True,
                task_descriptor=task_descriptor,
                job_id=job_id,
            ).job
        actor = self._lookup_actor(job_id)
        try:
            if actor is None:
                if self._default_actor_launcher:
                    actor = self._actor_launcher(job_id, workspace, spec.task, artifact_store_root)
                else:
                    actor = self._actor_launcher(job_id, workspace, spec.task)
            else:
                self._submit_invocation(actor, invocation_id, spec.task)
        except BaseException:
            self.orchestrator.job_manager.update_state(job_id, "failed")
            raise
        self._actors[job_id] = actor
        metadata = {
            "job": {
                "job_id": job.job_id,
                "agent_id": job.agent_id,
                "owner_agent": job.owner_agent,
                "kind": job.kind,
                "state": job.state,
                "seq": job.seq,
                "task_descriptor": job.task_descriptor,
                "workspace": str(workspace),
                "artifact_store_root": artifact_store_root,
            }
        }
        return ExecutionTarget(target_ref, volatile_handle=actor, metadata=metadata)
    def observe(self, target: Mapping[str, Any]) -> str:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        self._restore_job(target, job_id)
        job = self.orchestrator.job_manager.get(job_id)
        if job is not None and job.state in {"failed", "killed"}:
            return str(job.state)
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if (
            isinstance(job_data, Mapping)
            and job_data.get("state") == "completed"
            and isinstance(job_data.get("result_payload"), Mapping)
        ):
            return "completed"
        actor = self._lookup_actor(job_id)
        if actor is None:
            if job is not None and job.state == "completed":
                return "completed"
            if job is not None and job.state not in {"failed", "killed"}:
                self.orchestrator.job_manager.update_state(job_id, "failed")
            return "absent"
        if self._invocation_state(actor, self._invocation_id(job_id)) == "missing":
            return "absent"
        try:
            state = str(self._ray_get(getattr(actor, "get_state", None))).lower()
        except BaseException:
            return "pending"
        if state == "completed":
            result = getattr(actor, "get_result", None)
            if result is None:
                if job is not None and job.state not in {"failed", "killed"}:
                    self.orchestrator.job_manager.update_state(job_id, "failed")
                return "failed"
            result_payload = self._ray_get(result)
            if not isinstance(result_payload, Mapping):
                if job is not None and job.state not in {"failed", "killed"}:
                    self.orchestrator.job_manager.update_state(job_id, "failed")
                return "failed"
            try:
                durable_payload = self._durably_prepare_result(target, result_payload)
            except ChildError:
                if job is not None and job.state not in {"failed", "killed"}:
                    self.orchestrator.job_manager.update_state(job_id, "failed")
                return "failed"
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                return "accepted"
            metadata = target.get("metadata")
            job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
            if isinstance(job_data, dict):
                job_data["state"] = "completed"
                job_data["result_payload"] = dict(durable_payload)
        return state

    def acknowledge_result(self, target: Mapping[str, Any]) -> None:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        payload = job_data.get("result_payload") if isinstance(job_data, Mapping) else None
        job = self.orchestrator.job_manager.get(job_id)
        if (not isinstance(payload, Mapping) or not payload) and job is not None:
            payload = getattr(job, "result_payload", None)
            if isinstance(job_data, dict) and isinstance(payload, Mapping):
                job_data["result_payload"] = dict(payload)
        if not isinstance(payload, Mapping):
            raise ChildError("completed Ray child has no durable result payload")
        if job is not None and job.state == "completed":
            marked = job
        else:
            marked = self.orchestrator.mark_job_completed(job_id, result_payload=dict(payload))
        if marked is None:
            raise ChildError("completed Ray child could not be durably marked")
        if isinstance(job_data, dict):
            job_data["state"] = "completed"
            job_data["seq"] = marked.seq
            job_data["result_payload"] = dict(payload)
    def cancel(self, target: Mapping[str, Any]) -> bool:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        job = self.orchestrator.job_manager.get(job_id)
        if job is not None and job.state in {"completed", "failed", "killed"}:
            return True
        actor = self._lookup_actor(job_id)
        if actor is not None:
            try:
                cancel = getattr(actor, "cancel", None)
                if cancel is not None and not hasattr(cancel, "remote"):
                    if cancel() is False:
                        return False
                else:
                    import ray
                    ray.kill(actor, no_restart=True)
            except BaseException:
                return False
        marked = self.orchestrator.job_manager.update_state(job_id, "killed")
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if isinstance(job_data, dict) and marked is not None:
            job_data["state"] = "killed"
            job_data["seq"] = marked.seq
        return True

    def prepare_result(
        self, target: Mapping[str, Any], spec: ChildSpec
    ) -> bytes | ArtifactRef | None:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        job = self.orchestrator.job_manager.get(job_id)
        payload = getattr(job, "result_payload", None) if job is not None else None
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if not isinstance(payload, Mapping) or not payload:
            payload = job_data.get("result_payload") if isinstance(job_data, Mapping) else None
        if not isinstance(payload, Mapping) or not payload:
            actor = self._lookup_actor(job_id)
            if actor is not None:
                result = getattr(actor, "get_result", None)
                payload = self._ray_get(result) if result is not None else None
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
        if isinstance(result, str):
            return result.encode()
        if isinstance(result, Mapping):
            return json.dumps(result, sort_keys=True).encode()
        return None
class ProcessExecutionAdapter:
    family = "execution-world-process"

    def __init__(self, command: Sequence[str] = ("/bin/sh", "-c", "sleep 30")) -> None:
        self.command = tuple(command)
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._workspace: Path | None = None
    def retained_config(self) -> dict[str, Any]:
        return {"command": list(self.command)}

    def bind_workspace(self, workspace: Path) -> None:
        self._workspace = workspace

    _TERM_TIMEOUT_SECONDS = 0.5
    _KILL_TIMEOUT_SECONDS = 0.5

    @staticmethod
    def _group_alive(group: int) -> bool | None:
        try:
            output = subprocess.check_output(["ps", "-axo", "pgid=,stat="], text=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        for line in output.splitlines():
            fields = line.strip().split()
            if len(fields) < 2:
                continue
            try:
                process_group = int(fields[0])
            except ValueError:
                continue
            if process_group == group and not fields[1].startswith("Z"):
                return True
        return False

    def _wait_for_exit(self, target: Mapping[str, Any], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.observe(target) != "absent":
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def _workspace_path(self, activation: ChildActivation) -> Path:
        raw = activation.workspace if activation.workspace is not None else self._workspace
        if raw is None or not str(raw).strip():
            raise ChildError("process child adapter is not bound to a workspace")
        workspace = Path(raw).expanduser().resolve()
        if not workspace.is_dir():
            raise ChildError(f"process child workspace is unavailable: {workspace}")
        return workspace


    @staticmethod
    def _identity(pid: int) -> tuple[str, int]:
        try:
            raw = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart=,pgid="],
                text=True,
            ).strip()
        except subprocess.CalledProcessError as error:
            raise ProcessLookupError(pid) from error
        fields = raw.rsplit(None, 1)
        if len(fields) != 2:
            raise RuntimeError(f"process identity inspection returned malformed data for pid {pid}")
        try:
            group = int(fields[1])
        except ValueError as error:
            raise RuntimeError(f"process identity inspection returned malformed group for pid {pid}") from error
        return "sha256:" + hashlib.sha256(fields[0].encode()).hexdigest(), group
    def _publish(self, target: ExecutionTarget) -> None:
        return None
    @staticmethod
    def _pending_pid(target_ref: str) -> int | None:
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        for line in output.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and target_ref in fields[1]:
                try:
                    return int(fields[0])
                except ValueError:
                    continue
        return None

    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        workspace = self._workspace_path(activation)
        target_ref = activation.execution_target_ref
        wrapper = "import os,sys\nif os.read(0,1) != b'1': os._exit(78)\nos.execvpe(sys.argv[2],sys.argv[2:],os.environ.copy())\n"
        process = subprocess.Popen((sys.executable, "-c", wrapper, target_ref, *self.command), stdin=subprocess.PIPE, start_new_session=True, cwd=str(workspace))
        self._processes[target_ref] = process
        token, group = self._identity(process.pid)
        metadata: dict[str, Any] = {"launch_phase": "pending"}
        target = ExecutionTarget(target_ref, process.pid, token, group, process, metadata)
        try:
            self._publish(target)
            publisher = activation.publish_target
            if publisher is not None:
                publisher(target)
            if process.stdin is not None:
                process.stdin.write(b"1")
                process.stdin.close()
            metadata["launch_phase"] = "released"
            if publisher is not None:
                publisher(target)
        except BaseException:
            try:
                os.killpg(group, 15)
            except ProcessLookupError:
                pass
            raise
        return target

    def observe(self, target: Mapping[str, Any]) -> str:
        target_ref = str(target.get("ref", ""))
        process = self._processes.get(target_ref)
        pid, token, group = target.get("pid"), target.get("start_token"), target.get("process_group_id")
        if type(group) is not int:
            if process is not None:
                return "pending"
            return "pending" if self._pending_pid(target_ref) is not None else "absent"

        def group_state() -> bool | None:
            try:
                return self._group_alive(group)
            except (OSError, subprocess.CalledProcessError, RuntimeError):
                return None

        if process is not None and process.poll() is not None:
            alive = group_state()
            if alive is True:
                return "pending"
            if alive is None:
                return "pending"
            self._processes.pop(target_ref, None)
            return "absent"
        if type(pid) is not int or type(token) is not str:
            return "pending"
        try:
            observed_token, observed_group = self._identity(pid)
        except ProcessLookupError:
            alive = group_state()
            if alive is False:
                return "absent"
            return "running" if alive is True else "pending"
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            alive = group_state()
            if alive is False:
                return "absent"
            return "running" if alive is True else "pending"
        if observed_token != token or observed_group != group:
            return "absent" if group_state() is False else "pending"
        try:
            state = subprocess.check_output(["ps", "-p", str(pid), "-o", "stat="], text=True).strip()
        except ProcessLookupError:
            alive = group_state()
            if alive is False:
                return "absent"
            return "running" if alive is True else "pending"
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            alive = group_state()
            if alive is False:
                return "absent"
            return "running" if alive is True else "pending"
        if not state or state.startswith("Z"):
            alive = group_state()
            if alive is False:
                return "absent"
            return "running" if alive is True else "pending"
        return "running"
    def release_pending(self, target: Mapping[str, Any]) -> bool:
        metadata = target.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("launch_phase") != "pending":
            return False
        pid = target.get("pid")
        if type(pid) is not int:
            return False
        try:
            command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        return str(target.get("ref", "")) in command

    def recover(self, target: Mapping[str, Any]) -> ExecutionTarget | None:
        pid, token, group = target.get("pid"), target.get("start_token"), target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            process = self._processes.get(str(target.get("ref", "")))
            if process is not None and process.poll() is None:
                pid = process.pid
            else:
                pending_pid = self._pending_pid(str(target.get("ref", "")))
                if pending_pid is None:
                    return None
                try:
                    pending_token, pending_group = self._identity(pending_pid)
                except (OSError, ProcessLookupError, RuntimeError):
                    return None
                pending_target = {
                    "ref": str(target.get("ref", "")),
                    "pid": pending_pid,
                    "start_token": pending_token,
                    "process_group_id": pending_group,
                }
                if self.cancel(pending_target) is False:
                    return ExecutionTarget(
                        str(target["ref"]),
                        pending_pid,
                        pending_token,
                        pending_group,
                        metadata=dict(target.get("metadata") or {}),
                    )
                return None
            try:
                token, group = self._identity(pid)
            except (OSError, ProcessLookupError, RuntimeError):
                return None
        if self.observe({"pid": pid, "start_token": token, "process_group_id": group}) != "running":
            return None
        return ExecutionTarget(str(target["ref"]), pid, token, group, metadata=dict(target.get("metadata") or {}))
    def cancel(self, target: Mapping[str, Any]) -> bool:
        observed = self.observe(target)
        if observed == "absent":
            return True
        if observed != "running":
            return False
        group = target.get("process_group_id")
        if type(group) is not int:
            return False
        try:
            os.killpg(group, 15)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if self._wait_for_exit(target, self._TERM_TIMEOUT_SECONDS):
            return True
        try:
            os.killpg(group, 9)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return self._wait_for_exit(target, self._KILL_TIMEOUT_SECONDS)

    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | None:
        return None


__all__ = ["ChildActivation", "ChildError", "ChildExecutionAdapter", "ChildSpec", "ChildState", "DurableChildFactory", "DurableChildReconciler", "ExpectedRevisionConflict", "ExecutionTarget", "LateResultRejected", "PreparationRequired", "ProcessExecutionAdapter", "RayJobAdapter", "UnavailableChildAdapter"]
