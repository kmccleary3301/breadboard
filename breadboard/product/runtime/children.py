"""Internal Session-centered durable child factory.

Child coordination is retained on the existing engine ``SessionRecord`` /
``SessionRegistry``.  Product ``Session``, Work Item and ArtifactStore remain
their existing owners; this module only composes their ordering.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar


from breadboard.modules.author import (
    ChildFailed,
    ChildHandle,
    ChildOutput,
    ChildSucceeded,
    ChildTarget,
    ChildUnknown,
    InputEnvelope,
    ModuleInput,
    OutputEnvelope,
)
from breadboard.product.coordination.placement import WorkPlacement
from breadboard.product.coordination.work_items import (
    CancellationPolicy,
    ResumePolicy,
    RetryPolicy,
    WorkItem,
    WorkItemRepository,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import (
    ArtifactRef,
    ArtifactStore,
    artifact_store_ref,
)
from breadboard.product.runtime.events import (
    Clock,
    IdSource,
    ProcessLock,
    Session,
    SessionLineage,
    SystemClock,
    UUIDSource,
)
from breadboard.product.runtime.session_store import (
    _mutate_session_locked,
    _session_transition_guard,
    create_session,
    load_session,
    mutate_session,
)
from breadboard.product.runtime._child_process_adapter import (
    RESEARCH_WORLD_WORKER_COMMAND,
    ProcessExecutionAdapter,
)
from breadboard.product.runtime._child_ray_adapter import RayJobAdapter
from breadboard.product.runtime._child_stream_adapter import (
    AuthorChildBackend,
    AuthorChildStreamAdapter,
)
from breadboard.product.runtime._child_state import (
    ChildActivation,
    ChildError,
    ChildExecutionAdapter,
    ChildSpec,
    ChildState,
    ChildStreamingExecutionAdapter,
    ExecutionTarget,
    ExpectedRevisionConflict,
    LateResultRejected,
    PreparationRequired,
    _TERMINAL,
)




def _parent_cancellation_requests(
    value: object,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping):
        return ()
    raw_requests = value.get("requests")
    candidates: Sequence[object]
    if raw_requests is None:
        candidates = (value,)
    elif isinstance(raw_requests, (list, tuple)):
        candidates = raw_requests
    else:
        raise ValueError("durable parent cancellation requests are invalid")
    requests: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("durable parent cancellation request is invalid")
        work_item_id = candidate.get("work_item_id")
        reason = candidate.get("reason")
        child_refs = candidate.get("child_recovery_refs")
        if (
            not isinstance(child_refs, (list, tuple))
            or any(type(ref) is not str or not ref.strip() for ref in child_refs)
        ):
            raise ValueError(
                "durable parent cancellation child references are invalid"
            )
        if (
            type(work_item_id) is not str
            or not work_item_id.strip()
            or type(reason) is not str
            or not reason.strip()
        ):
            raise ValueError("durable parent cancellation request is invalid")
        requests[work_item_id] = {
            "work_item_id": work_item_id,
            "reason": reason,
            "child_recovery_refs": sorted(set(child_refs)),
        }
    return tuple(requests[key] for key in sorted(requests))



def _reserved_target_ref(adapter_family: str, suffix: str) -> str:
    return f"job:reserved:{suffix}" if adapter_family == "ray-agent-job" else f"reserved:{suffix}"
def _is_reserved_target_ref(adapter_family: str, target_ref: str, child_session_id: str) -> bool:
    base = _reserved_target_ref(adapter_family, child_session_id)
    return target_ref == base or target_ref.startswith(base + ":")



def _recovery_parts(recovery_ref: object) -> tuple[str, str]:
    if type(recovery_ref) is not str:
        raise ExpectedRevisionConflict("malformed child recovery reference")
    prefix = "child://"
    marker = "/attempt/"
    if not recovery_ref.startswith(prefix):
        raise ExpectedRevisionConflict("malformed child recovery reference")
    child_session_id, separator, attempt_id = recovery_ref[len(prefix) :].partition(
        marker
    )
    if not separator or not child_session_id.strip() or not attempt_id.strip():
        raise ExpectedRevisionConflict("malformed child recovery reference")
    return child_session_id, attempt_id










class DurableChildFactory:
    """One provider-neutral boundary over retained SessionRecord and owners."""
    _owner_locks: ClassVar[dict[str, threading.RLock]] = {}
    _owner_locks_guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, workspace: str | Path, *, registry: Any, repository: WorkItemRepository, adapters: Iterable[ChildExecutionAdapter], clock: Clock | None = None, ids: IdSource | None = None, artifact_store: ArtifactStore | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self._registry_loop: asyncio.AbstractEventLoop | None = None
        self.repository = repository
        repository_path = getattr(repository, "_path", None)
        if not isinstance(repository_path, Path):
            raise ChildError("durable child WorkItemRepository must have a durable path")
        self._repository_path = repository_path.resolve()
        self.clock = clock or SystemClock()
        self.ids = ids or UUIDSource()
        self._owner_id = self.ids.new_id()
        self._lifecycle_lock = threading.RLock()
        self._product_transition_state = threading.local()
        self.artifacts = artifact_store or ArtifactStore(self.workspace / ".breadboard" / "artifacts")
        if getattr(self.artifacts, "_descriptor", None) is not None:
            raise ChildError(
                "durable children require an artifact store with a stable path"
            )
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
    def with_async_registry(
        cls,
        workspace: str | Path,
        *,
        registry: Any,
        repository: WorkItemRepository,
        adapters: Iterable[ChildExecutionAdapter],
        artifact_store: ArtifactStore | None = None,
        clock: Clock | None = None,
        ids: IdSource | None = None,
    ) -> "DurableChildFactory":
        loop = asyncio.get_running_loop()
        factory = cls(
            workspace,
            registry=registry,
            repository=repository,
            adapters=adapters,
            artifact_store=artifact_store,
            clock=clock,
            ids=ids,
        )
        factory._registry_loop = loop
        return factory
    @classmethod
    def _owner_lock(cls, key: str) -> threading.RLock:
        with cls._owner_locks_guard:
            return cls._owner_locks.setdefault(key, threading.RLock())
    def child_states(self, *, parent_work_item_id: str) -> tuple[ChildState, ...]:
        if type(parent_work_item_id) is not str or not parent_work_item_id.strip():
            raise ValueError("parent_work_item_id must be a non-empty string")
        states: list[ChildState] = []
        for record in self._registry("records"):
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            retained = metadata.get("durable_child")
            if (
                not isinstance(retained, Mapping)
                or retained.get("parent_work_item_id") != parent_work_item_id
            ):
                continue
            state = self._record_state(record.session_id)
            repository_path = state.child_spec.get("work_item_repository_path")
            if (
                metadata.get("workspace") == str(self.workspace)
                and repository_path == str(self._repository_path)
            ):
                states.append(state)
        return tuple(sorted(states, key=lambda state: state.child_session_id))
    def prepare_cancel_tree(
        self,
        *,
        parent_session_id: str,
        parent_work_item_id: str,
        reason: str = "operator request",
    ) -> tuple[ChildState, ...]:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        root_session_id = self._tree_root_session_id(parent_session_id)
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id), self._tree_process_lock(root_session_id):
            transition_ids = self._root_transition_session_ids(root_session_id)
            with self._product_transition_guard(*transition_ids):
                return self._cancel_tree(
                    parent_session_id=parent_session_id,
                    parent_work_item_id=parent_work_item_id,
                    reason=reason,
                    prepare_only=True,
                )
    def cancel_tree(
        self,
        *,
        parent_session_id: str,
        parent_work_item_id: str,
        reason: str = "operator request",
        admission_preclosed: bool = False,
    ) -> tuple[ChildState, ...]:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        root_session_id = self._tree_root_session_id(parent_session_id)
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id), self._tree_process_lock(root_session_id):
            transition_ids = self._root_transition_session_ids(root_session_id)
            with self._product_transition_guard(*transition_ids):
                return self._cancel_tree(
                    parent_session_id=parent_session_id,
                    parent_work_item_id=parent_work_item_id,
                    reason=reason,
                    admission_preclosed=admission_preclosed,
                )
    def _cancel_tree(
        self,
        *,
        parent_session_id: str,
        parent_work_item_id: str,
        reason: str = "operator request",
        prepare_only: bool = False,
        admission_preclosed: bool = False,
    ) -> tuple[ChildState, ...]:
        """Persist parent intent and every descendant intent before signaling."""
        parent, _ = load_session(self.workspace, parent_session_id)
        parent_work = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        parent_attempt = (
            parent_work.read_model.current_attempt
            or (
                parent_work.read_model.attempts[-1]
                if parent_work.read_model.attempts
                else None
            )
        )
        if (
            parent_attempt is None
            or parent_attempt.session_ref != parent_session_id
        ):
            raise ChildError("parent Work Item does not belong to the parent Session")
        records = self._registry("records")
        expected_root_session_id = self._tree_root_session_id(parent_session_id)
        by_parent: dict[str, list[ChildState]] = {}
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            value = metadata.get("durable_child")
            if not isinstance(value, Mapping):
                continue
            state = self._record_state(record.session_id)
            child_spec = state.child_spec
            repository_path = child_spec.get("work_item_repository_path")
            workspace_path = metadata.get("workspace")
            if (
                state.root_session_id != expected_root_session_id
                or not isinstance(repository_path, str)
                or Path(repository_path).expanduser().resolve()
                != self._repository_path
                or not isinstance(workspace_path, str)
                or Path(workspace_path).expanduser().resolve() != self.workspace
            ):
                continue
            by_parent.setdefault(state.parent_work_item_id, []).append(state)
        retained_tree_refs: list[str] = []
        retained_queue = [parent_work_item_id]
        while retained_queue:
            retained_parent_id = retained_queue.pop(0)
            for retained_state in by_parent.get(retained_parent_id, ()):
                retained_tree_refs.append(retained_state.recovery_ref)
                retained_queue.append(retained_state.child_work_item_id)
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
        if parent_record is None:
            from breadboard_engine.api.cli_bridge.models import SessionStatus
            from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

            retained_status = {
                "completed": SessionStatus.COMPLETED,
                "failed": SessionStatus.FAILED,
                "canceled": SessionStatus.STOPPED,
            }.get(product_status, SessionStatus.RUNNING)
            self._registry(
                "create",
                SessionRecord(
                    session_id=parent_session_id,
                    status=retained_status,
                    metadata={"workspace": str(self.workspace)},
                    product_session=parent,
                ),
            )
            parent_record = self._registry("get", parent_session_id)
        if parent_record is None:
            raise ChildError("parent SessionRecord could not be retained")
        parent_metadata = dict(parent_record.metadata or {})
        try:
            _parent_cancellation_requests(
                parent_metadata.get("durable_parent_cancellation")
            )
        except ValueError as error:
            raise ChildError("durable parent cancellation marker is invalid") from error
        if prepare_only:
            return tuple(descendants)
        if not admission_preclosed:
            child_recovery_refs = [
                state.recovery_ref for state in descendants
            ]
            self._registry(
                "close_admission_for_parent_cancellations",
                parent_session_id,
                requests=(
                    {
                        "work_item_id": parent_work_item_id,
                        "reason": reason,
                        "child_recovery_refs": child_recovery_refs,
                    },
                ),
                expected_child_recovery_refs=retained_tree_refs,
                expected_child_owner=(
                    str(self.workspace),
                    str(self._repository_path),
                    expected_root_session_id,
                ),
                _tree_lock_held=True,
            )
        if product_status in _TERMINAL and work_status not in _TERMINAL:
            try:
                if product_status == "completed":
                    attempt = parent_work.read_model.current_attempt
                    if attempt is None:
                        raise ChildError(
                            "completed Product Session has no active Work Item attempt"
                        )
                    parent_work.complete(
                        "Product Session already completed",
                        attempt_id=attempt.attempt_id,
                    )
                elif product_status == "failed":
                    parent_work.fail(
                        "product_session", "Product Session already failed"
                    )
                else:
                    parent_work.cancel("operator", reason)
            except (RuntimeError, ValueError) as error:
                raise ChildError(
                    "Product Session terminal outcome cannot reconcile Work Item"
                ) from error
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
                raise ChildError(
                    "Work Item terminal outcome cannot reconcile Product Session"
                ) from error
            parent, _ = load_session(self.workspace, parent_session_id)
        adopted: list[ChildState] = []
        remaining_descendants: list[ChildState] = []
        for state in descendants:
            if state.terminal_count:
                self._repair_terminal_owners(state)
                self._status(state)
                continue
            child_events = self.repository.read(state.child_work_item_id)
            if not child_events:
                remaining_descendants.append(state)
                continue
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            if state.settlement is not None:
                payload = state.settlement
                child_product, _ = load_session(
                    self.workspace, state.child_session_id
                )
                if (
                    child.read_model.status in _TERMINAL
                    or child_product.read_model.status in _TERMINAL
                ):
                    adopted.append(
                        self._settle(
                            state,
                            str(payload["outcome"]),
                            tuple(
                                str(ref)
                                for ref in payload.get("result_refs", ())
                            ),
                            allow_unprepared=str(payload["outcome"])
                            != "completed",
                            allow_parent_terminal=True,
                        )
                    )
                    continue
                state = self._cas(state, settlement=None)
            if child.read_model.status in _TERMINAL and not state.cancellation_requested:
                if child.read_model.status == "canceled":
                    state = self._cas(
                        state,
                        status="cancel_requested",
                        cancellation_requested=True,
                        cancellation_reason=reason,
                    )
                    remaining_descendants.append(state)
                else:
                    adopted.append(
                        self._adopt_terminal_work_item(
                            state,
                            child,
                            allow_cancellation_intent=True,
                            allow_parent_terminal=True,
                        )
                    )
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
            if not self._execution_stopped_after_cancel(state):
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
                self._status(state)
                settled.append(state)
        if not unsettled:
            self._registry(
                "remove_durable_parent_cancellation_request",
                parent_session_id,
                work_item_id=parent_work_item_id,
            )
        return tuple(settled)
    @contextmanager
    def _tree_process_lock(self, root_session_id: str):
        lock_path = self.workspace / ".breadboard" / f"child-tree-{hashlib.sha256(root_session_id.encode()).hexdigest()}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with ProcessLock(lock_path):
            yield

    @contextmanager
    def _owner_process_lock(self, key: str):
        lock_path = self.workspace / ".breadboard" / f"child-owner-{hashlib.sha256(key.encode()).hexdigest()}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with ProcessLock(lock_path):
            yield

    def _registry(self, method: str, *args: Any, **kwargs: Any) -> Any:
        result = getattr(self.registry, method)(*args, **kwargs)
        if not hasattr(result, "__await__"):
            return result
        if self._registry_loop is not None:
            return asyncio.run_coroutine_threadsafe(result, self._registry_loop).result()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        raise RuntimeError("synchronous child API cannot run inside an event loop")

    def _tree_root_session_id(self, session_id: str) -> str:
        record = self._registry("get", session_id)
        if record is None:
            return session_id
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        retained = metadata.get("durable_child")
        if not isinstance(retained, Mapping):
            return session_id
        root_session_id = retained.get("root_session_id")
        if not isinstance(root_session_id, str) or not root_session_id.strip():
            raise ChildError("retained child root Session identity is invalid")
        return root_session_id

    def _root_transition_session_ids(self, root_session_id: str) -> tuple[str, ...]:
        session_ids = {root_session_id}
        for record in self._registry("records"):
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            retained = metadata.get("durable_child")
            if (
                isinstance(retained, Mapping)
                and retained.get("root_session_id") == root_session_id
            ):
                session_ids.add(record.session_id)
        return tuple(sorted(session_ids))

    @contextmanager
    def _product_transition_guard(self, *session_ids: str):
        ordered = tuple(sorted(set(session_ids)))
        with ExitStack() as stack:
            for session_id in ordered:
                stack.enter_context(_session_transition_guard(self.workspace, session_id))
            previous = getattr(self._product_transition_state, "session_ids", ())
            self._product_transition_state.session_ids = (*previous, *ordered)
            try:
                yield
            finally:
                self._product_transition_state.session_ids = previous

    def _mutate_child_product(
        self,
        child_session_id: str,
        transition: Callable[[Session], None],
    ) -> None:
        held = getattr(self._product_transition_state, "session_ids", ())
        mutate = _mutate_session_locked if child_session_id in held else mutate_session
        mutate(self.workspace, child_session_id, transition)

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
    def _final_launch_fence(
        self, state: ChildState, parent_attempt_id: str | None = None
    ) -> ChildState:
        current = self._record_state(state.child_session_id)
        parent_active = True
        try:
            self._require_parent_start_active(
                state.parent_session_id, state.root_session_id
            )
            parent_work = WorkItem.restore(
                self.repository,
                state.parent_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            expected_parent_attempt = (
                parent_attempt_id
                or self._parent_attempt_id_for_child(
                    state.parent_work_item_id,
                    state.child_work_item_id,
                )
            )
            parent_attempt = parent_work.read_model.current_attempt
            if (
                expected_parent_attempt is None
                or parent_work.read_model.status != "running"
                or parent_attempt is None
                or parent_attempt.attempt_id != expected_parent_attempt
                or parent_attempt.session_ref != state.parent_session_id
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
            raise ExpectedRevisionConflict(
                "child owner became unavailable before launch"
            ) from error
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

    def _require_parent_work_start_active(
        self,
        parent_work_item_id: str,
        attempt_id: str,
        parent_session_id: str,
    ) -> None:
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
            or attempt.session_ref != parent_session_id
        ):
            raise ChildError("parent Work Item became terminal during child startup")

    def _require_parent_start_active(
        self, parent_session_id: str, root_session_id: str
    ) -> None:
        for session_id, label in (
            (parent_session_id, "parent"),
            (root_session_id, "root"),
        ):
            record = self._registry("get", session_id)
            if record is not None and (
                record.admission_closed
                or (
                    isinstance(record.metadata, Mapping)
                    and isinstance(
                        record.metadata.get("durable_parent_cancellation"), Mapping
                    )
                )
            ):
                raise ChildError(f"{label} Product Session cancellation is pending")
        parent_product, _ = load_session(self.workspace, parent_session_id)
        if parent_product.read_model.status != "running":
            raise ChildError(
                "parent Product Session became terminal during child startup"
            )
        if root_session_id != parent_session_id:
            root_product, _ = load_session(self.workspace, root_session_id)
            if root_product.read_model.status != "running":
                raise ChildError(
                    "root Product Session became terminal during child startup"
                )


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


    @staticmethod
    def _input_envelope(
        value: ModuleInput | InputEnvelope,
        *,
        sequence: int,
    ) -> InputEnvelope:
        if isinstance(value, ModuleInput):
            return InputEnvelope(value.schema_id, sequence, value.body, value.final)
        if isinstance(value, InputEnvelope):
            if value.sequence != sequence:
                raise ExpectedRevisionConflict(
                    "child input sequence is not the next owner sequence"
                )
            return value
        raise TypeError("child input must be ModuleInput or InputEnvelope")

    @staticmethod
    def _child_handle(state: ChildState, spec: ChildSpec) -> ChildHandle:
        return ChildHandle(
            child_work_id=state.child_work_item_id,
            child_generation_id=str(state.child_spec.get("lock_hash", spec.lock.generation_id)),
            child_instance_id=state.child_session_id,
            child_attempt_id=state.attempt_id,
            parent_work_id=state.parent_work_item_id,
            child_label=str(state.child_spec.get("title", spec.title)),
        )

    @staticmethod
    def _stream_adapter(
        adapter: ChildExecutionAdapter,
    ) -> ChildStreamingExecutionAdapter:
        if not isinstance(adapter, ChildStreamingExecutionAdapter):
            raise ChildError(
                f"child adapter family does not support streaming: {adapter.family}"
            )
        return adapter
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
    def start(
        self,
        *,
        parent_session_id: str,
        root_session_id: str,
        parent_work_item_id: str,
        spec: ChildSpec,
        initial_input: ModuleInput | InputEnvelope | None = None,
        target_binding: ChildTarget | None = None,
        scope_fence: Callable[[], None] | None = None,
    ) -> ChildActivation:
        if type(root_session_id) is not str or not root_session_id.strip():
            raise ValueError("root_session_id must be a non-empty string")
        if target_binding is None and isinstance(
            self.adapters.get(spec.adapter_family), AuthorChildStreamAdapter
        ):
            raise ChildError("author child start requires a compiler-resolved target")
        if isinstance(self.adapters.get(spec.adapter_family), AuthorChildStreamAdapter):
            binding_name = spec.adapter_config.get("module_binding")
            if type(binding_name) is not str or not binding_name.strip():
                raise ChildError(
                    "author child adapter config requires module_binding"
                )
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id), self._tree_process_lock(root_session_id):
            canonical_root_session_id = self._tree_root_session_id(parent_session_id)
            if root_session_id != canonical_root_session_id:
                raise ChildError("root Session does not match retained parent lineage")
            return self._start(
                parent_session_id=parent_session_id,
                root_session_id=canonical_root_session_id,
                parent_work_item_id=parent_work_item_id,
                spec=spec,
                initial_input=initial_input,
                target_binding=target_binding,
                scope_fence=scope_fence,
            )
    def _start(
        self,
        *,
        parent_session_id: str,
        root_session_id: str,
        parent_work_item_id: str,
        spec: ChildSpec,
        initial_input: ModuleInput | InputEnvelope | None = None,
        target_binding: ChildTarget | None = None,
        scope_fence: Callable[[], None] | None = None,
    ) -> ChildActivation:
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
        if (
            parent.read_model.status != "running"
            or parent.read_model.current_attempt is None
            or parent.read_model.current_attempt.session_ref != parent_session_id
        ):
            raise ChildError("parent Work Item is not running for the parent Session")
        parent_attempt_id = parent.read_model.current_attempt.attempt_id
        child_session_id = self.ids.new_id()
        child_work_item_id = self.ids.new_id()
        attempt_id = self.ids.new_id()
        recovery_ref = f"child://{child_session_id}/attempt/{attempt_id}"
        reserved = _reserved_target_ref(spec.adapter_family, child_session_id)
        child_spec = spec.retained()
        config_fn = getattr(self.adapters[spec.adapter_family], "retained_config", None)
        config = spec.adapter_config or (config_fn() if callable(config_fn) else {})
        if not isinstance(config, Mapping):
            raise ChildError("child adapter config is not durable")
        try:
            durable_config = json.loads(json.dumps(dict(config)))
        except (TypeError, ValueError) as error:
            raise ChildError("child adapter config is not durable") from error
        child_spec["adapter_config"] = durable_config
        created_artifacts: set[ArtifactRef] = set()
        with self.artifacts.transaction():
            task_artifact = self.artifacts.put(
                spec.task.encode(),
                media_type="text/plain; charset=utf-8",
                created=created_artifacts,
            )
            child_spec["task_artifact_ref"] = task_artifact.as_dict()
            child_spec["task_artifact_store"] = str(self.artifacts._root)
            child_spec["artifact_store_root"] = str(self.artifacts._root)
            child_spec["work_item_repository_path"] = str(self._repository_path)
            execution_target: dict[str, Any] = {"ref": reserved}
            if spec.adapter_family == RayJobAdapter.family:
                execution_target["metadata"] = {
                    "job": {
                        "job_id": reserved.removeprefix("job:"),
                        "agent_id": child_session_id,
                        "owner_agent": parent_session_id,
                        "kind": "agent",
                        "state": "accepted",
                        "seq": 0,
                        "task_descriptor": {
                            "child_session_id": child_session_id,
                            "recovery_ref": recovery_ref,
                            "task_hash": child_spec["task_hash"],
                        },
                        "workspace": str(self.workspace),
                        "artifact_store_root": str(self.artifacts._root),
                    }
                }
            initial = ChildState(
                child_session_id,
                child_work_item_id,
                parent_session_id,
                root_session_id,
                parent_work_item_id,
                attempt_id,
                recovery_ref,
                reserved,
                spec.adapter_family,
                "starting",
                0,
                startup_phase="recorded",
                child_spec=child_spec,
                execution_target=execution_target,
            )
            try:
                self._create_record(initial)
            except BaseException:
                for artifact in created_artifacts:
                    self.artifacts.discard(artifact)
                raise
        product = Session.start(
            spec.lock, spec.task, session_id=child_session_id, clock=self.clock, ids=self.ids,
            lineage=SessionLineage(
                initial.parent_session_id, initial.root_session_id,
                initial.parent_work_item_id, initial.child_work_item_id,
            ),
        )
        create_session(self.workspace, product)
        self._require_start_active(child_session_id)
        parent_product, _ = load_session(self.workspace, parent_session_id)
        parent = WorkItem.restore(self.repository, parent_work_item_id, clock=self.clock, ids=self.ids)
        if (
            parent_product.read_model.status != "running"
            or parent.read_model.status != "running"
            or parent.read_model.current_attempt is None
        ):
            self._cancel_unpublished_start(
                initial,
                "parent owner became terminal during child startup",
                signal=False,
            )
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
        with self._product_transition_guard(
            parent_session_id,
            root_session_id,
            child_session_id,
        ):
            try:
                self._require_start_active(child_session_id)
                self._require_parent_start_active(parent_session_id, root_session_id)
                self._require_parent_work_start_active(
                    parent_work_item_id,
                    parent_attempt_id,
                    parent_session_id,
                )
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
            activation_initial = (
                self._input_envelope(initial_input, sequence=0)
                if initial_input is not None
                else None
            )
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
                target_binding=target_binding,
                initial_input=activation_initial,
                child_handle=self._child_handle(state, spec),
                scope_fence=scope_fence,
            )
            state = self._launch(state, activation, spec)
            return replace(
                activation,
                recovery_ref=state.recovery_ref,
                execution_target_ref=state.execution_target_ref,
            )
    def _stream_state(self, handle: ChildHandle) -> ChildState:
        state = self._record_state(str(handle.child_instance_id))
        expected = self._child_handle(state, self._spec(state))
        if handle != expected:
            raise ChildError("foreign child handle")
        if state.terminal_count:
            return state
        return state

    @staticmethod
    def _stream_identity_matches(
        state: ChildState,
        item: ChildOutput | ChildSucceeded | ChildFailed | ChildUnknown,
    ) -> bool:
        return (
            item.child_work_id == state.child_work_item_id
            and item.child_attempt_id == state.attempt_id
        )

    def _stream_terminal_item(self, state: ChildState) -> ChildFailed | ChildSucceeded:
        outcome = state.stream_outcome
        if isinstance(outcome, Mapping) and outcome.get("kind") == "succeeded":
            artifact = outcome.get("artifact_ref")
            schema_id = outcome.get("schema_id")
            if isinstance(artifact, Mapping) and type(schema_id) is str:
                store = self._artifact_store_for_state(state)
                ref = ArtifactRef(
                    str(artifact["digest"]),
                    int(artifact["size_bytes"]),
                    str(artifact["media_type"]),
                )
                return ChildSucceeded(
                    state.child_work_item_id,
                    state.attempt_id,
                    OutputEnvelope(schema_id, store.read(ref)),
                )
        if isinstance(outcome, Mapping):
            return ChildFailed(
                state.child_work_item_id,
                state.attempt_id,
                str(outcome.get("code") or "child_failed"),
                str(outcome.get("detail") or "child execution failed"),
            )
        return ChildFailed(
            state.child_work_item_id,
            state.attempt_id,
            "child_terminal",
            str(state.terminal_outcome or "child execution terminated"),
        )

    def submit_input(
        self,
        handle: ChildHandle,
        value: ModuleInput | InputEnvelope,
        *,
        expected_revision: int | None = None,
        scope_fence: Callable[[], None] | None = None,
    ) -> None:
        state = self._stream_state(handle)
        if state.terminal_count:
            raise LateResultRejected("child input cannot follow terminal settlement")
        if expected_revision is not None and state.revision != expected_revision:
            raise ExpectedRevisionConflict("stale child revision")
        adapter = self._stream_adapter(self.adapters[state.adapter_family])
        if scope_fence is not None:
            scope_fence()
        envelope = self._input_envelope(value, sequence=state.input_sequence)
        with self._lifecycle_lock, self._owner_lock(state.parent_work_item_id):
            current = self._stream_state(handle)
            if expected_revision is not None and current.revision != expected_revision:
                raise ExpectedRevisionConflict("stale child revision")
            current, envelope = self._admit_stream_input(current, envelope)
        if scope_fence is not None:
            scope_fence()
        adapter.submit_input(
            current.execution_target,
            envelope,
            scope_fence=scope_fence,
        )

    def next_output(
        self,
        handle: ChildHandle,
        *,
        expected_revision: int | None = None,
        scope_fence: Callable[[], None] | None = None,
    ) -> ChildOutput | ChildSucceeded | ChildFailed | ChildUnknown:
        state = self._stream_state(handle)
        if expected_revision is not None and state.revision != expected_revision:
            raise ExpectedRevisionConflict("stale child revision")
        if state.terminal_count:
            return self._stream_terminal_item(state)
        adapter = self._stream_adapter(self.adapters[state.adapter_family])
        if scope_fence is not None:
            scope_fence()
        item = adapter.next_output(
            state.execution_target,
            scope_fence=scope_fence,
        )
        if not self._stream_identity_matches(state, item):
            raise ExpectedRevisionConflict("stream item belongs to a different child attempt")
        with self._lifecycle_lock, self._owner_lock(state.parent_work_item_id):
            state = self._stream_state(handle)
            if state.terminal_count:
                return self._stream_terminal_item(state)
            if isinstance(item, ChildOutput):
                sequence = int(item.sequence)
                store = self._artifact_store_for_state(state)
                ref = store.put(item.output.body)
                record = {
                    "sequence": sequence,
                    "schema_id": item.output.schema_id,
                    "child_work_id": state.child_work_item_id,
                    "child_attempt_id": state.attempt_id,
                    "artifact_ref": ref.as_dict(),
                }
                if sequence != state.output_sequence:
                    prior = next(
                        (row for row in state.output_history if row.get("sequence") == sequence),
                        None,
                    )
                    if prior is None:
                        raise ExpectedRevisionConflict("child output sequence is not the next owner sequence")
                    prior_ref = prior.get("artifact_ref")
                    if not isinstance(prior_ref, Mapping) or prior_ref.get("digest") != ref.digest:
                        raise ExpectedRevisionConflict("conflicting duplicate child output sequence")
                    return item
                self._cas(
                    state,
                    output_sequence=state.output_sequence + 1,
                    output_history=(*state.output_history, record),
                )
                return item
            if isinstance(item, ChildUnknown):
                return item
            if isinstance(item, ChildSucceeded):
                store = self._artifact_store_for_state(state)
                ref = store.put(item.output.body)
                state = self._cas(
                    state,
                    result_prepared=True,
                    result_refs=(ref.digest,),
                    stream_outcome={
                        "kind": "succeeded",
                        "schema_id": item.output.schema_id,
                        "artifact_ref": ref.as_dict(),
                    },
                )
                self.settle(
                    state.child_session_id,
                    expected_revision=state.revision,
                    outcome="completed",
                    result_refs=state.result_refs,
                    attempt_id=state.attempt_id,
                )
                return item
            state = self._cas(
                state,
                stream_outcome={
                    "kind": "failed",
                    "code": item.code,
                    "detail": item.detail,
                },
            )
            self.settle(
                state.child_session_id,
                expected_revision=state.revision,
                outcome="failed",
                result_refs=(),
                attempt_id=state.attempt_id,
            )
            return item

    def join(
        self,
        handles: tuple[ChildHandle, ...],
        *,
        scope_fence: Callable[[], None] | None = None,
    ) -> tuple[ChildSucceeded | ChildFailed | ChildUnknown, ...]:
        outcomes: list[ChildSucceeded | ChildFailed | ChildUnknown] = []
        for handle in handles:
            while True:
                item = self.next_output(handle, scope_fence=scope_fence)
                if isinstance(item, ChildOutput):
                    continue
                outcomes.append(item)
                break
        return tuple(outcomes)


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
        artifact_store = self._artifact_store_for_state(state)
        if isinstance(result, ArtifactRef):
            artifact_store.read(result)
            refs = (result.digest,)
        elif isinstance(result, bytes):
            refs = (artifact_store.put(result).digest,)
        elif result is None:
            refs = ()
        else:
            raise TypeError("prepared child result must be bytes, ArtifactRef, or None")
        return self._cas(state, result_prepared=True, result_refs=refs)

    def _descendants_settled(
        self,
        state: ChildState,
        *,
        require_all: bool,
    ) -> bool:
        child_events = self.repository.read(state.child_work_item_id)
        if not child_events:
            return True
        child = WorkItem.restore(
            self.repository,
            state.child_work_item_id,
            clock=self.clock,
            ids=self.ids,
        )
        descendant_ids = set(child.read_model.child_work_item_ids)
        if (
            not descendant_ids
            or (
                not require_all
                and not child.read_model.cancellation_policy.propagate_to_children
            )
        ):
            return True
        descendants = self.child_states(
            parent_work_item_id=state.child_work_item_id
        )
        descendants_by_id = {
            descendant.child_work_item_id: descendant
            for descendant in descendants
        }
        return (
            set(descendants_by_id) == descendant_ids
            and all(
                descendants_by_id[child_id].terminal_count == 1
                for child_id in descendant_ids
            )
        )

    def _has_propagating_descendants(self, state: ChildState) -> bool:
        child_events = self.repository.read(state.child_work_item_id)
        if not child_events:
            return False
        child = WorkItem.restore(
            self.repository,
            state.child_work_item_id,
            clock=self.clock,
            ids=self.ids,
        )
        return (
            child.read_model.cancellation_policy.propagate_to_children
            and bool(child.read_model.child_work_item_ids)
        )
    def _cancel_propagating_descendants(
        self,
        state: ChildState,
        *,
        reason: str,
    ) -> tuple[ChildState, ...]:
        if not self._has_propagating_descendants(state):
            return ()
        child = WorkItem.restore(
            self.repository,
            state.child_work_item_id,
            clock=self.clock,
            ids=self.ids,
        )
        expected_ids = set(child.read_model.child_work_item_ids)
        descendants = self.child_states(
            parent_work_item_id=state.child_work_item_id
        )
        descendants_by_id = {
            descendant.child_work_item_id: descendant
            for descendant in descendants
        }
        if set(descendants_by_id) != expected_ids:
            raise ChildError("delegated child settlement authority is incomplete")
        settled: list[ChildState] = []
        for child_id in sorted(expected_ids):
            descendant = self._record_state(
                descendants_by_id[child_id].child_session_id
            )
            if descendant.terminal_count:
                settled.append(descendant)
            elif self._has_propagating_descendants(descendant):
                settled.append(
                    self._cancel_nonleaf_locked(
                        descendant,
                        expected_revision=descendant.revision,
                        reason=reason,
                    )
                )
            elif descendant.cancellation_requested:
                settled.append(self._reconcile(descendant.recovery_ref))
            else:
                settled.append(
                    self._cancel(
                        descendant.child_session_id,
                        expected_revision=descendant.revision,
                        reason=reason,
                    )
                )
        return tuple(settled)


    def cancel(
        self,
        child_session_id: str,
        *,
        expected_revision: int,
        reason: str = "operator request",
    ) -> ChildState:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        state = self._record_state(child_session_id)
        with (
            self._lifecycle_lock,
            self._owner_lock(state.parent_work_item_id),
            self._owner_process_lock(state.parent_work_item_id),
            self._tree_process_lock(state.root_session_id),
            self._product_transition_guard(
                state.parent_session_id,
                state.root_session_id,
                state.child_session_id,
            ),
        ):
            state = self._record_state(child_session_id)
            has_propagating_descendants = self._has_propagating_descendants(state)
            if not has_propagating_descendants:
                return self._cancel(
                    child_session_id,
                    expected_revision=expected_revision,
                    reason=reason,
                )
        return self._cancel_nonleaf(
            state,
            expected_revision=expected_revision,
            reason=reason,
        )
    def _cancel_nonleaf(
        self,
        state: ChildState,
        *,
        expected_revision: int,
        reason: str,
    ) -> ChildState:
        with (
            self._lifecycle_lock,
            self._owner_lock(state.parent_work_item_id),
            self._owner_process_lock(state.parent_work_item_id),
            self._tree_process_lock(state.root_session_id),
        ):
            transition_ids = self._root_transition_session_ids(
                state.root_session_id
            )
            with self._product_transition_guard(*transition_ids):
                return self._cancel_nonleaf_locked(
                    state,
                    expected_revision=expected_revision,
                    reason=reason,
                )

    def _cancel_nonleaf_locked(
        self,
        state: ChildState,
        *,
        expected_revision: int,
        reason: str,
    ) -> ChildState:
        state = self._record_state(state.child_session_id)
        if state.revision != expected_revision:
            raise ExpectedRevisionConflict(
                f"stale child revision: expected {expected_revision}, actual {state.revision}"
            )
        if state.terminal_count:
            raise LateResultRejected("cannot cancel a terminal child")
        if state.settlement is not None:
            raise ExpectedRevisionConflict("child settlement is already reserved")
        if not state.cancellation_requested:
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            policy = child.read_model.cancellation_policy
            if policy.mode == "never" or "operator" not in policy.cancellable_by:
                raise ChildError("operator is not authorized to cancel this Work Item")
            current_attempt = child.read_model.current_attempt
            if (
                current_attempt is not None
                and policy.cleanup == "checkpoint_then_stop"
                and current_attempt.checkpoint_ref is None
            ):
                raise ValueError(
                    "checkpoint_then_stop requires a current checkpoint"
                )
            state = self._cas(
                state,
                status="cancel_requested",
                cancellation_requested=True,
                cancellation_reason=reason,
            )
        descendants = self._cancel_tree(
            parent_session_id=state.child_session_id,
            parent_work_item_id=state.child_work_item_id,
            reason=reason,
        )
        if any(descendant.terminal_count != 1 for descendant in descendants):
            return self._record_state(state.child_session_id)
        state = self._record_state(state.child_session_id)
        child = WorkItem.restore(
            self.repository,
            state.child_work_item_id,
            clock=self.clock,
            ids=self.ids,
        )
        if not self._execution_stopped_after_cancel(state):
            return state
        return self._adopt_terminal_work_item(
            state,
            child,
            allow_cancellation_intent=True,
            execution_stopped=True,
        )
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
            return self._cancel_unpublished_start(state, reason, signal=False)
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
        result_refs: Sequence[str] | None = None,
        attempt_id: str | None = None,
        _allow_cancellation_intent: bool = False,
    ) -> ChildState:
        state = self._record_state(child_session_id)
        with (
            self._lifecycle_lock,
            self._owner_lock(state.parent_work_item_id),
            self._owner_process_lock(state.parent_work_item_id),
            self._tree_process_lock(state.root_session_id),
            self._product_transition_guard(
                state.parent_session_id,
                state.root_session_id,
                state.child_session_id,
            ),
        ):
            return self._settle_request(
                child_session_id,
                expected_revision=expected_revision,
                outcome=outcome,
                result_refs=result_refs,
                attempt_id=attempt_id,
                _allow_cancellation_intent=_allow_cancellation_intent,
            )

    def _settle_request(
        self,
        child_session_id: str,
        *,
        expected_revision: int,
        outcome: str,
        result_refs: Sequence[str] | None = None,
        attempt_id: str | None = None,
        _allow_cancellation_intent: bool = False,
        _allow_parent_terminal: bool = False,
    ) -> ChildState:
        if attempt_id is None:
            raise ExpectedRevisionConflict("settlement requires an attempt identity")
        state = self._record_state(child_session_id)
        if attempt_id != state.attempt_id:
            raise ExpectedRevisionConflict("stale child attempt")
        if state.terminal_count:
            if state.terminal_outcome == outcome and (result_refs is None or tuple(result_refs) == state.result_refs):
                self._repair_terminal_owners(state)
                self._status(state)
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
        if not self._descendants_settled(
            state,
            require_all=outcome == "completed",
        ):
            raise ChildError(
                f"child {outcome} settlement requires every delegated child to settle"
            )
        reserved = self._cas(
            state,
            settlement={
                "outcome": outcome,
                "result_refs": list(state.result_refs),
            },
        )
        try:
            return self._settle(
                reserved,
                outcome,
                state.result_refs,
                allow_unprepared=outcome != "completed",
                allow_parent_terminal=_allow_parent_terminal,
            )
        except (LateResultRejected, ChildError, ValueError):
            current = self._record_state(child_session_id)
            if current.settlement == reserved.settlement:
                self._cas(current, settlement=None)
            raise

    def _cancel_late_settlement(self, state: ChildState) -> ChildState:
        current = self._record_state(state.child_session_id)
        if current.terminal_count:
            return current
        if current.settlement is not None:
            current = self._cas(current, settlement=None)
        if not current.cancellation_requested:
            current = self._cas(
                current,
                status="cancel_requested",
                cancellation_requested=True,
                cancellation_reason="parent owner terminated before child settlement",
            )
        if self.adapters[current.adapter_family].cancel(current.execution_target) is False:
            return current
        return self._settle(current, "canceled", (), allow_unprepared=True)

    def _execution_stopped_after_cancel(self, state: ChildState) -> bool:
        adapter = self.adapters[state.adapter_family]
        if adapter.cancel(state.execution_target) is not False:
            return True
        try:
            observed = str(adapter.observe(state.execution_target)).lower()
        except BaseException:
            return False
        return observed in {"completed", "failed"}

    def _adopt_terminal_target_after_cancel(
        self, state: ChildState
    ) -> ChildState:
        adapter = self.adapters[state.adapter_family]
        observed = str(adapter.observe(state.execution_target)).lower()
        if observed not in {"completed", "failed"}:
            return self._record_state(state.child_session_id)
        outcome = "completed" if observed == "completed" else "canceled"
        state = self._cas(state, execution_target=state.execution_target)
        current = self._record_state(state.child_session_id)
        if observed == "completed" and not current.result_prepared:
            current = self.prepare_result(
                current.child_session_id,
                expected_revision=current.revision,
                attempt_id=current.attempt_id,
                _allow_cancellation_intent=True,
            )
        if current.settlement is None:
            current = self._cas(
                current,
                settlement={
                    "outcome": outcome,
                    "result_refs": list(current.result_refs),
                },
            )
        return self._settle(
            current,
            outcome,
            current.result_refs if outcome == "completed" else (),
            allow_unprepared=outcome != "completed",
            allow_parent_terminal=True,
        )

    def _artifact_store_for_state(self, state: ChildState) -> ArtifactStore:
        retained_root = state.child_spec.get("artifact_store_root")
        if retained_root is None:
            return self.artifacts
        if not isinstance(retained_root, str) or not retained_root.strip():
            raise ChildError("durable child artifact store identity is malformed")
        return ArtifactStore(Path(retained_root))

    def _settle(
        self,
        state: ChildState,
        outcome: str,
        result_refs: Sequence[str],
        *,
        allow_unprepared: bool,
        allow_parent_terminal: bool = False,
    ) -> ChildState:
        if not self._descendants_settled(
            state,
            require_all=outcome == "completed",
        ):
            raise ChildError(
                f"child {outcome} settlement requires every delegated child to settle"
            )
        if outcome != "canceled" and not allow_parent_terminal:
            parent_product, _ = load_session(
                self.workspace, state.parent_session_id
            )
            parent_work = WorkItem.restore(
                self.repository,
                state.parent_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            parent_attempt = (
                parent_work.read_model.attempts[-1]
                if parent_work.read_model.attempts
                else None
            )
            expected_parent_attempt_id = self._parent_attempt_id_for_child(
                state.parent_work_item_id,
                state.child_work_item_id,
            )
            if (
                parent_product.read_model.status in _TERMINAL
                or parent_work.read_model.status in _TERMINAL
                or parent_attempt is None
                or expected_parent_attempt_id is None
                or parent_attempt.attempt_id != expected_parent_attempt_id
                or parent_attempt.session_ref != state.parent_session_id
            ):
                raise LateResultRejected(
                    "child settlement cannot follow parent termination"
                )
        if outcome == "completed":
            if allow_unprepared or not state.result_prepared:
                raise PreparationRequired(
                    "result/artifact preparation must precede settlement"
                )
            if tuple(result_refs) != state.result_refs:
                raise ExpectedRevisionConflict(
                    "settlement result refs do not match prepared refs"
                )
            artifact_store = self._artifact_store_for_state(state)
            for digest in result_refs:
                artifact_store.read(
                    artifact_store_ref(artifact_store._root, digest)
                )
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        work_status = child.read_model.status
        latest_attempt = child.read_model.attempts[-1] if child.read_model.attempts else None
        if latest_attempt is None or latest_attempt.attempt_id != state.attempt_id:
            raise ExpectedRevisionConflict("settlement attempt does not match retained child attempt")
        if work_status in _TERMINAL and work_status != outcome:
            raise ChildError("Work Item terminal outcome disagrees with settlement")
        if work_status not in _TERMINAL and child.read_model.current_attempt is None:
            if state.settlement is not None:
                self._cas(state, settlement=None)
            raise ChildError("child Work Item has no active attempt")
        session, _ = load_session(self.workspace, state.child_session_id)
        product_status = session.read_model.status
        if product_status in _TERMINAL and product_status != outcome:
            raise ChildError("Product Session terminal outcome disagrees with settlement")
        if product_status not in _TERMINAL:
            try:
                if outcome == "completed":
                    self._mutate_child_product(
                        state.child_session_id,
                        lambda current: current.complete("child result prepared"),
                    )
                elif outcome == "failed":
                    self._mutate_child_product(
                        state.child_session_id,
                        lambda current: current.fail(
                            "child_failed", "execution target exited"
                        ),
                    )
                else:
                    self._mutate_child_product(
                        state.child_session_id,
                        lambda current: current.cancel(
                            state.cancellation_reason or "operator request"
                        ),
                    )
            except RuntimeError as error:
                refreshed_session, _ = load_session(
                    self.workspace, state.child_session_id
                )
                if refreshed_session.read_model.status != "running":
                    raise ChildError(
                        "Product Session state cannot accept child settlement"
                    ) from error
                raise
        session, _ = load_session(self.workspace, state.child_session_id)
        record = self._registry("get", state.child_session_id)
        if record is not None:
            record.product_session = session
        if work_status not in _TERMINAL:
            attempt = child.read_model.current_attempt
            try:
                if outcome == "completed":
                    child.complete("child result prepared", attempt_id=attempt.attempt_id)  # type: ignore[union-attr]
                elif outcome == "failed":
                    child.fail_attempt("execution target exited", attempt_id=attempt.attempt_id, retryable=False)  # type: ignore[union-attr]
                else:
                    child.cancel("operator", state.cancellation_reason or "operator request")
            except RuntimeError as error:
                refreshed = WorkItem.restore(
                    self.repository,
                    state.child_work_item_id,
                    clock=self.clock,
                    ids=self.ids,
                )
                if refreshed.read_model.status in _TERMINAL:
                    if refreshed.read_model.status != outcome:
                        raise ChildError(
                            "Work Item terminal outcome disagrees with settlement"
                        ) from error
                else:
                    raise
        parent_work = WorkItem.restore(self.repository, state.parent_work_item_id, clock=self.clock, ids=self.ids)
        parent_work.join_child(state.child_work_item_id, state.child_session_id, outcome, result_refs)
        if outcome == "completed":
            self._acknowledge_prepared_result(state)
        state = self._cas(state, status=outcome, terminal_outcome=outcome, terminal_count=1, result_refs=tuple(result_refs), settlement=None, joined=True)
        self._status(state)
        release_terminal = getattr(
            self.adapters[state.adapter_family],
            "release_terminal",
            None,
        )
        if callable(release_terminal) and release_terminal(state.execution_target) is False:
            raise ChildError("terminal execution owner release remains pending")
        return state

    def _adopt_terminal_work_item(
        self,
        state: ChildState,
        child: WorkItem,
        *,
        allow_cancellation_intent: bool = False,
        execution_stopped: bool = False,
        allow_parent_terminal: bool = False,
    ) -> ChildState:
        """Join an already-terminal Work Item after cancellation cleanup."""
        outcome = child.read_model.status
        if outcome not in _TERMINAL:
            raise ValueError("terminal Work Item adoption requires a terminal outcome")
        if (
            outcome == "canceled"
            and state.launch_published
            and not execution_stopped
            and not self._execution_stopped_after_cancel(state)
        ):
            return state
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
        if state.settlement is not None:
            retained_outcome = state.settlement.get("outcome")
            retained_refs = tuple(state.settlement.get("result_refs", ()))
            if retained_outcome != outcome or retained_refs != state.result_refs:
                raise ChildError(
                    "retained settlement disagrees with terminal Work Item"
                )
            return self._settle(
                state,
                outcome,
                retained_refs,
                allow_unprepared=outcome != "completed",
                allow_parent_terminal=allow_parent_terminal,
            )
        if outcome == "completed" and not state.result_prepared:
            state = self.prepare_result(
                state.child_session_id,
                expected_revision=state.revision,
                attempt_id=state.attempt_id,
                _allow_cancellation_intent=allow_cancellation_intent,
            )
        return self._settle_request(
            state.child_session_id,
            expected_revision=state.revision,
            outcome=outcome,
            result_refs=state.result_refs,
            attempt_id=state.attempt_id,
            _allow_cancellation_intent=allow_cancellation_intent,
            _allow_parent_terminal=allow_parent_terminal,
        )
    def _reserved_execution_target(
        self,
        state: ChildState,
        target_ref: str,
        *,
        recovery_ref: str | None = None,
    ) -> dict[str, Any]:
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
                        "recovery_ref": (
                            state.recovery_ref
                            if recovery_ref is None
                            else recovery_ref
                        ),
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

    def _admit_stream_input(
        self,
        state: ChildState,
        envelope: InputEnvelope,
    ) -> tuple[ChildState, InputEnvelope]:
        if state.input_closed:
            raise LateResultRejected("final child input has already been admitted")
        if envelope.sequence != state.input_sequence:
            raise ExpectedRevisionConflict("child input sequence is not the next owner sequence")
        store = self._artifact_store_for_state(state)
        ref = store.put(envelope.body)
        record = {
            "sequence": envelope.sequence,
            "schema_id": envelope.schema_id,
            "final": envelope.final,
            "child_work_id": state.child_work_item_id,
            "child_attempt_id": state.attempt_id,
            "artifact_ref": ref.as_dict(),
        }
        next_state = self._cas(
            state,
            input_sequence=state.input_sequence + 1,
            input_closed=envelope.final,
            input_history=(*state.input_history, record),
        )
        return next_state, envelope

    def _launch(
        self, state: ChildState, activation: ChildActivation, spec: ChildSpec
    ) -> ChildState:
        state = self._final_launch_fence(state)
        if state.terminal_count:
            return state
        adapter = self.adapters[state.adapter_family]
        if activation.initial_input is not None:
            self._stream_adapter(adapter)
            activation = replace(
                activation,
                initial_input=self._input_envelope(
                    activation.initial_input,
                    sequence=state.input_sequence,
                ),
            )
            state, initial_input = self._admit_stream_input(
                state, activation.initial_input
            )
            activation = replace(activation, initial_input=initial_input)
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

    def _retry(
        self,
        state: ChildState,
        child: WorkItem,
        *,
        failed_target: bool = False,
    ) -> ChildState:
        if self._has_propagating_descendants(state):
            descendants = self._cancel_propagating_descendants(
                state,
                reason="parent execution target exited",
            )
            if any(descendant.terminal_count != 1 for descendant in descendants):
                return self._record_state(state.child_session_id)
            state = self._record_state(state.child_session_id)
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
        snapshot = child.read_model
        if snapshot.status in _TERMINAL:
            return self._adopt_terminal_work_item(state, child)
        if snapshot.status in {"waiting", "paused", "blocked"}:
            return state
        if snapshot.status not in {"running", "ready", "leased"}:
            raise ChildError(f"cannot relaunch child Work Item from {snapshot.status}")
        attempt = snapshot.current_attempt
        reason = "execution target exited"
        cleanup_handoff = (
            getattr(self.adapters[state.adapter_family], "cleanup_handoff", None)
            if failed_target
            else None
        )
        existing_attempt = attempt is not None and attempt.attempt_id != state.attempt_id
        if existing_attempt:
            if callable(cleanup_handoff):
                cleanup_handoff(state.execution_target)
            next_attempt = attempt.attempt_id  # type: ignore[union-attr]
            if attempt.session_ref != state.child_session_id:  # type: ignore[union-attr]
                raise ChildError("retained retry attempt session reference disagrees with child session")
            next_session_ref = state.child_session_id
            placement = next((row for row in snapshot.placements if row.attempt_id == next_attempt), None)
            reserved = placement.execution_target_ref if placement is not None else _reserved_target_ref(state.adapter_family, f"{state.child_session_id}:{next_attempt}")
            if placement is None:
                child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, next_attempt, state.child_spec["worker_id"], next_session_ref, reserved, self.clock.now()))
            next_recovery = f"child://{state.child_session_id}/attempt/{next_attempt}"
            state = self._cas(state, attempt_id=next_attempt, recovery_ref=next_recovery, execution_target_ref=reserved, execution_target=self._reserved_execution_target(state, reserved, recovery_ref=next_recovery), status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0, launch_published=False, result_prepared=False, result_refs=(), settlement=None)
        elif attempt is not None:
            if not child.read_model.retry_policy.allows(reason) or len(child.read_model.attempts) >= child.read_model.retry_policy.max_attempts:
                if failed_target and not state.result_prepared:
                    state = self.prepare_result(
                        state.child_session_id,
                        expected_revision=state.revision,
                        attempt_id=state.attempt_id,
                        _allow_cancellation_intent=True,
                    )
                if failed_target:
                    try:
                        return self._settle_request(
                            state.child_session_id,
                            expected_revision=state.revision,
                            outcome="failed",
                            result_refs=state.result_refs,
                            attempt_id=state.attempt_id,
                            _allow_cancellation_intent=True,
                        )
                    except LateResultRejected:
                        return self._cancel_late_settlement(state)
                return self._settle(state, "failed", (), allow_unprepared=True)
            if callable(cleanup_handoff):
                cleanup_handoff(state.execution_target)
            child.fail_attempt(reason, attempt_id=attempt.attempt_id, retryable=True)
            next_attempt = self.ids.new_id()
            lease_id = self.ids.new_id()
            child.acquire_lease(state.child_spec["worker_id"], lease_id=lease_id)
            next_session_ref = state.child_session_id
            child.start_attempt(next_session_ref, lease_id=lease_id, attempt_id=next_attempt, reuse_session_ref=True)
            reserved = _reserved_target_ref(state.adapter_family, f"{state.child_session_id}:{next_attempt}")
            child.attach_placement(WorkPlacement(self.ids.new_id(), state.child_work_item_id, next_attempt, state.child_spec["worker_id"], next_session_ref, reserved, self.clock.now()))
            next_recovery = f"child://{state.child_session_id}/attempt/{next_attempt}"
            state = self._cas(state, attempt_id=next_attempt, recovery_ref=next_recovery, execution_target_ref=reserved, execution_target=self._reserved_execution_target(state, reserved, recovery_ref=next_recovery), status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0, launch_published=False, result_prepared=False, result_refs=(), settlement=None)
        elif snapshot.status in {"ready", "leased"}:
            if callable(cleanup_handoff):
                cleanup_handoff(state.execution_target)
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
            state = self._cas(state, attempt_id=next_attempt, recovery_ref=next_recovery, execution_target_ref=reserved, execution_target=self._reserved_execution_target(state, reserved, recovery_ref=next_recovery), status="running", launch_claimed=True, launch_claim_owner=self._owner_id, launch_claim_until=time.time() + 30.0, launch_published=False, result_prepared=False, result_refs=(), settlement=None)
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
    def _acknowledge_prepared_result(self, state: ChildState) -> None:
        acknowledge = getattr(self.adapters[state.adapter_family], "acknowledge_result", None)
        if callable(acknowledge):
            store = self._artifact_store_for_state(state)
            refs = tuple(artifact_store_ref(store._root, digest) for digest in state.result_refs)
            acknowledge(state.execution_target, result_refs=refs)

    def _repair_terminal_owners(self, state: ChildState) -> None:
        outcome = state.terminal_outcome or state.status
        child_events = self.repository.read(state.child_work_item_id)
        child = (
            WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            if child_events
            else None
        )
        try:
            session, _ = load_session(self.workspace, state.child_session_id)
        except FileNotFoundError:
            spec = self._spec(state)
            product = Session.start(
                spec.lock, spec.task, session_id=state.child_session_id, clock=self.clock, ids=self.ids,
                lineage=SessionLineage(
                    state.parent_session_id, state.root_session_id,
                    state.parent_work_item_id, state.child_work_item_id,
                ),
            )
            create_session(self.workspace, product)
            session, _ = load_session(self.workspace, state.child_session_id)
        if session is not None:
            product_status = session.read_model.status
            if product_status in _TERMINAL and product_status != outcome:
                raise ChildError("Product Session terminal outcome disagrees with retained child")
            if product_status not in _TERMINAL:
                if outcome == "completed":
                    self._mutate_child_product(
                        state.child_session_id,
                        lambda current: current.complete("child result prepared"),
                    )
                elif outcome == "failed":
                    self._mutate_child_product(
                        state.child_session_id,
                        lambda current: current.fail(
                            "child_failed", "execution target exited"
                        ),
                    )
                else:
                    self._mutate_child_product(
                        state.child_session_id,
                        lambda current: current.cancel(
                            state.cancellation_reason or "operator request"
                        ),
                    )
            session, _ = load_session(self.workspace, state.child_session_id)
            record = self._registry("get", state.child_session_id)
            if record is not None:
                record.product_session = session
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
        if outcome == "completed":
            self._acknowledge_prepared_result(state)
        release_terminal = getattr(
            self.adapters[state.adapter_family],
            "release_terminal",
            None,
        )
        if callable(release_terminal) and release_terminal(state.execution_target) is False:
            raise ChildError("terminal execution owner release remains pending")
    def reconcile(self, recovery_ref: str) -> ChildState:
        child_session_id, _ = _recovery_parts(recovery_ref)
        state = self._record_state(child_session_id)
        with (
            self._lifecycle_lock,
            self._owner_lock(state.parent_work_item_id),
            self._owner_process_lock(state.parent_work_item_id),
            self._tree_process_lock(state.root_session_id),
        ):
            transition_ids = self._root_transition_session_ids(
                state.root_session_id
            )
            with self._product_transition_guard(*transition_ids):
                state = self._record_state(child_session_id)
                if (
                    state.cancellation_requested
                    and self._has_propagating_descendants(state)
                ):
                    return self._cancel_nonleaf_locked(
                        state,
                        expected_revision=state.revision,
                        reason=state.cancellation_reason or "operator request",
                    )
                return self._reconcile(recovery_ref)
    def _reconcile(self, recovery_ref: str) -> ChildState:
        child_session_id, _ = _recovery_parts(recovery_ref)
        state = self._record_state(child_session_id)
        if state.recovery_ref != recovery_ref:
            raise ExpectedRevisionConflict("stale child recovery reference")
        if state.terminal_count:
            self._repair_terminal_owners(state)
            if not state.joined:
                state = self._cas(state, joined=True)
            self._status(state)
            return state
        if state.cancellation_requested:
            child_events = self.repository.read(state.child_work_item_id)
            if not child_events:
                return self._cancel_unpublished_start(
                    state,
                    state.cancellation_reason or "operator request",
                    signal=False,
                )
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            if child.read_model.status in _TERMINAL:
                if not self._execution_stopped_after_cancel(state):
                    return state
                if child.read_model.status != "canceled":
                    raise LateResultRejected(
                        "terminal Work Item outcome cannot replace requested cancellation"
                    )
                return self._adopt_terminal_work_item(
                    state,
                    child,
                    allow_cancellation_intent=True,
                    execution_stopped=True,
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
                    return self._cancel_unpublished_start(
                        state,
                        state.cancellation_reason or "operator request",
                        signal=False,
                    )
                return self._abort_startup(state)
            try:
                load_session(self.workspace, child_session_id)
            except FileNotFoundError:
                spec = self._spec(state)
                product = Session.start(
                    spec.lock, spec.task, session_id=child_session_id, clock=self.clock, ids=self.ids,
                    lineage=SessionLineage(
                        state.parent_session_id, state.root_session_id,
                        state.parent_work_item_id, state.child_work_item_id,
                    ),
                )
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
                    state.parent_session_id,
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
            child = WorkItem.restore(
                self.repository,
                state.child_work_item_id,
                clock=self.clock,
                ids=self.ids,
            )
            if child.read_model.status in _TERMINAL:
                return self._adopt_terminal_work_item(state, child)
            try:
                product, _ = load_session(self.workspace, child_session_id)
            except FileNotFoundError:
                product = None
            if product is not None:
                record = self._registry("get", child_session_id)
                if record is not None:
                    record.product_session = product
                product_outcome = product.read_model.status
                if product_outcome in _TERMINAL:
                    if product_outcome == "completed" and not state.result_prepared:
                        raise ChildError(
                            "completed child Product Session has no prepared result"
                        )
                    canceled = self.adapters[state.adapter_family].cancel(
                        state.execution_target
                    )
                    if canceled is False:
                        observed = str(
                            self.adapters[state.adapter_family].observe(
                                state.execution_target
                            )
                        ).lower()
                        if observed != product_outcome:
                            return state
                    return self._settle(
                        state,
                        product_outcome,
                        state.result_refs,
                        allow_unprepared=product_outcome != "completed",
                        allow_parent_terminal=True,
                    )
                self._status(state)
        if state.settlement:
            payload = state.settlement
            try:
                return self._settle(
                    state,
                    str(payload["outcome"]),
                    tuple(str(ref) for ref in payload.get("result_refs", ())),
                    allow_unprepared=str(payload["outcome"]) != "completed",
                )
            except LateResultRejected:
                return self._cancel_late_settlement(state)
        if state.cancellation_requested:
            if self.adapters[state.adapter_family].cancel(state.execution_target) is False:
                return self._adopt_terminal_target_after_cancel(state)
            return self._settle(state, "canceled", (), allow_unprepared=True)
        observed = str(self.adapters[state.adapter_family].observe(state.execution_target)).lower()
        if observed == "pending":
            return state
        if observed in {"running", "started", "live", "accepted"}:
            adapter = self.adapters[state.adapter_family]
            metadata = state.execution_target.get("metadata")
            release_committed = getattr(adapter, "release_committed", None)
            if (
                isinstance(metadata, Mapping)
                and metadata.get("launch_phase") == "pending"
                and callable(release_committed)
            ):
                committed_target = dict(state.execution_target)
                committed_metadata = dict(metadata)
                committed_metadata["launch_phase"] = "release_committed"
                committed_target["metadata"] = committed_metadata
                state = self._cas(state, execution_target=committed_target)
                if release_committed(state.execution_target):
                    released_target = dict(state.execution_target)
                    released_metadata = dict(committed_metadata)
                    released_metadata["launch_phase"] = "released"
                    released_target["metadata"] = released_metadata
                    state = self._cas(state, execution_target=released_target)
                return state
            release_pending = getattr(adapter, "release_pending", None)
            if callable(release_pending) and release_pending(state.execution_target):
                return state
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
            if not state.result_prepared:
                state = self.prepare_result(child_session_id, expected_revision=state.revision, attempt_id=state.attempt_id)
            try:
                return self._settle_request(
                    child_session_id,
                    expected_revision=state.revision,
                    outcome="completed",
                    result_refs=state.result_refs,
                    attempt_id=state.attempt_id,
                )
            except LateResultRejected:
                return self._cancel_late_settlement(state)
        child = WorkItem.restore(self.repository, state.child_work_item_id, clock=self.clock, ids=self.ids)
        adapter = self.adapters[state.adapter_family]
        if observed == "failed":
            state = self._cas(state, execution_target=state.execution_target)
            return self._retry(state, child, failed_target=True)
        if observed == "absent" and getattr(
            adapter, "released_absence_is_terminal", False
        ):
            metadata = state.execution_target.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("launch_phase") in {
                "release_committed",
                "released",
            }:
                return self._retry(state, child)
        if observed == "absent" and getattr(adapter, "absence_is_terminal", False):
            if child.read_model.status in _TERMINAL:
                return self._adopt_terminal_work_item(state, child)
            try:
                return self._settle(state, "failed", (), allow_unprepared=True)
            except LateResultRejected:
                return self._cancel_late_settlement(state)
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
            EffectiveHarnessLock._from_record({"generation_id": str(value["lock_hash"])}),
            str(value["worker_id"]),
            str(value["adapter_family"]),
            RetryPolicy.from_dict(value["retry_policy"]),
            ResumePolicy.from_dict(value["resume_policy"]),
            CancellationPolicy.from_dict(value["cancellation_policy"]),
            workflow_id=value.get("workflow_id"),
            workflow_step_id=value.get("workflow_step_id"),
            workflow_definition_hash=value.get("workflow_definition_hash"),
            adapter_config=(
                dict(value.get("adapter_config"))
                if isinstance(value.get("adapter_config"), Mapping)
                else {}
            ),
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

    def cancel(self, target: Mapping[str, Any]) -> bool:
        return False

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
        child_session_id, _ = _recovery_parts(recovery_ref)
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
        process_factory: type[ProcessExecutionAdapter] | None = None
        for factory in self._adapter_factories:
            if factory is ProcessExecutionAdapter:
                process_factory = factory
                if family != ProcessExecutionAdapter.family:
                    continue
                command = adapter_config.get("command")
                if not isinstance(command, list) or not command or any(type(part) is not str or not part for part in command):
                    raise ChildError("durable process child command is malformed")
                adapters.append(factory(command=tuple(command)))
            else:
                adapters.append(factory())
        adapters.extend(self._adapters)
        records = self.registry.records()
        if hasattr(records, "__await__"):
            records = await records
        retained_families = {family} if family else set()
        for candidate in records:
            candidate_metadata = (
                candidate.metadata
                if isinstance(candidate.metadata, Mapping)
                else {}
            )
            candidate_state = candidate_metadata.get("durable_child")
            candidate_family = (
                candidate_state.get("adapter_family")
                if isinstance(candidate_state, Mapping)
                and candidate_metadata.get("workspace") == workspace
                else None
            )
            if isinstance(candidate_family, str) and candidate_family:
                retained_families.add(candidate_family)
        if (
            process_factory is not None
            and ProcessExecutionAdapter.family in retained_families
            and not any(
                adapter.family == ProcessExecutionAdapter.family
                for adapter in adapters
            )
        ):
            adapters.append(process_factory())
        available_families = {adapter.family for adapter in adapters}
        adapters.extend(
            UnavailableChildAdapter(retained_family)
            for retained_family in sorted(retained_families - available_families)
        )
        artifact_store = ArtifactStore(Path(artifact_store_root)) if isinstance(artifact_store_root, str) else None
        return DurableChildFactory.with_async_registry(
            workspace,
            registry=self.registry,
            repository=repository,
            adapters=adapters,
            artifact_store=artifact_store,
        )
    async def __call__(self, recovery_ref: str) -> ChildState:
        factory = await self._build_factory(recovery_ref)
        return await asyncio.to_thread(factory.reconcile, recovery_ref)

    async def cancel(self, recovery_ref: str, *, reason: str = "operator request") -> ChildState:
        child_session_id, attempt_id = _recovery_parts(recovery_ref)
        factory = await self._build_factory(recovery_ref)
        state = await asyncio.to_thread(factory._record_state, child_session_id)
        if state.recovery_ref != recovery_ref or state.attempt_id != attempt_id:
            raise ExpectedRevisionConflict("stale child recovery reference")
        return await asyncio.to_thread(
            factory.cancel,
            child_session_id,
            expected_revision=state.revision,
            reason=reason,
        )
    async def cancel_tree(
        self,
        parent_session_id: str,
        *,
        reason: str = "operator request",
    ) -> tuple[ChildState, ...]:
        records = self.registry.records()
        if hasattr(records, "__await__"):
            records = await records
        parent_record = self.registry.get(parent_session_id)
        if hasattr(parent_record, "__await__"):
            parent_record = await parent_record
        if parent_record is None:
            direct_owner_scopes: set[tuple[str, str]] = set()
            for candidate in records:
                metadata = (
                    candidate.metadata
                    if isinstance(candidate.metadata, Mapping)
                    else {}
                )
                retained = metadata.get("durable_child")
                workspace = metadata.get("workspace")
                root_session = (
                    retained.get("root_session_id")
                    if isinstance(retained, Mapping)
                    and retained.get("parent_session_id") == parent_session_id
                    else None
                )
                if (
                    isinstance(workspace, str)
                    and workspace.strip()
                    and isinstance(root_session, str)
                    and root_session.strip()
                ):
                    direct_owner_scopes.add((workspace, root_session))
            if not direct_owner_scopes:
                return ()
            if len(direct_owner_scopes) != 1:
                raise ChildError(
                    "durable parent cancellation crosses workspace or root owners"
                )
            expected_workspace, expected_root_session = next(
                iter(direct_owner_scopes)
            )
        else:
            parent_metadata = (
                parent_record.metadata
                if isinstance(parent_record.metadata, Mapping)
                else {}
            )
            expected_workspace = parent_metadata.get("workspace")
            if (
                not isinstance(expected_workspace, str)
                or not expected_workspace.strip()
            ):
                raise ChildError("durable parent retained state has no workspace")
            parent_child_state = parent_metadata.get("durable_child")
            expected_root_session = (
                parent_child_state.get("root_session_id")
                if isinstance(parent_child_state, Mapping)
                else parent_session_id
            )
            if (
                not isinstance(expected_root_session, str)
                or not expected_root_session.strip()
            ):
                raise ChildError("durable parent root identity is malformed")

        parent_work_item_ids: dict[str, str] = {}
        repository_paths: set[str] = set()
        for candidate in records:
            metadata = (
                candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
            )
            retained = metadata.get("durable_child")
            child_spec = retained.get("child_spec") if isinstance(retained, Mapping) else None
            if (
                not isinstance(retained, Mapping)
                or retained.get("parent_session_id") != parent_session_id
                or metadata.get("workspace") != expected_workspace
                or retained.get("root_session_id") != expected_root_session
                or not isinstance(child_spec, Mapping)
            ):
                continue
            repository_path = child_spec.get("work_item_repository_path")
            if not isinstance(repository_path, str) or not repository_path.strip():
                raise ChildError(
                    "durable child WorkItemRepository identity is unavailable"
                )
            repository_paths.add(repository_path)
            parent_work_item_id = str(
                retained.get("parent_work_item_id") or ""
            ).strip()
            recovery_ref = str(retained.get("recovery_ref") or "").strip()
            if parent_work_item_id and recovery_ref:
                parent_work_item_ids.setdefault(parent_work_item_id, recovery_ref)
        if not parent_work_item_ids:
            return ()
        if len(repository_paths) != 1:
            raise ChildError(
                "durable parent cancellation crosses WorkItemRepository owners"
            )
        expected_repository = next(iter(repository_paths))
        expected_owner = (
            expected_workspace,
            expected_repository,
            expected_root_session,
        )

        descendant_session_ids = {parent_session_id}
        observed_child_refs: set[str] = set()
        changed = True
        while changed:
            changed = False
            for candidate in records:
                metadata = (
                    candidate.metadata
                    if isinstance(candidate.metadata, Mapping)
                    else {}
                )
                retained = metadata.get("durable_child")
                child_spec = (
                    retained.get("child_spec")
                    if isinstance(retained, Mapping)
                    else None
                )
                if (
                    not isinstance(retained, Mapping)
                    or retained.get("parent_session_id")
                    not in descendant_session_ids
                    or candidate.session_id in descendant_session_ids
                    or metadata.get("workspace") != expected_workspace
                    or not isinstance(child_spec, Mapping)
                    or child_spec.get("work_item_repository_path")
                    != expected_repository
                    or retained.get("root_session_id") != expected_root_session
                ):
                    continue
                descendant_session_ids.add(candidate.session_id)
                recovery_ref = retained.get("recovery_ref")
                if isinstance(recovery_ref, str) and recovery_ref.strip():
                    observed_child_refs.add(recovery_ref)
                changed = True

        factories: dict[str, DurableChildFactory] = {}
        descendants_by_parent: dict[str, tuple[ChildState, ...]] = {}
        for parent_work_item_id, recovery_ref in parent_work_item_ids.items():
            factory = await self._build_factory(recovery_ref)
            descendants = await asyncio.to_thread(
                factory.prepare_cancel_tree,
                parent_session_id=parent_session_id,
                parent_work_item_id=parent_work_item_id,
                reason=reason,
            )
            factories[parent_work_item_id] = factory
            descendants_by_parent[parent_work_item_id] = descendants
        close_admission = getattr(
            self.registry, "close_admission_for_parent_cancellations", None
        )
        if not callable(close_admission):
            raise ChildError(
                "durable parent cancellation requires atomic admission closure"
            )
        closed = close_admission(
            parent_session_id,
            requests=(
                {
                    "work_item_id": parent_work_item_id,
                    "reason": reason,
                    "child_recovery_refs": tuple(
                        state.recovery_ref
                        for state in descendants_by_parent[parent_work_item_id]
                    ),
                }
                for parent_work_item_id in sorted(parent_work_item_ids)
            ),
            expected_child_recovery_refs=observed_child_refs,
            expected_child_owner=expected_owner,
        )
        if hasattr(closed, "__await__"):
            await closed
        parent_record = self.registry.get(parent_session_id)
        if hasattr(parent_record, "__await__"):
            parent_record = await parent_record
        if parent_record is None or not parent_record.admission_closed:
            raise ChildError("parent cancellation admission closure was not retained")
        settled: list[ChildState] = []
        for parent_work_item_id in parent_work_item_ids:
            result = await asyncio.to_thread(
                factories[parent_work_item_id].cancel_tree,
                parent_session_id=parent_session_id,
                parent_work_item_id=parent_work_item_id,
                reason=reason,
                admission_preclosed=True,
            )
            settled.extend(result)
        return tuple(settled)





__all__ = ["ChildActivation", "ChildError", "ChildExecutionAdapter", "ChildSpec", "ChildState", "DurableChildFactory", "DurableChildReconciler", "ExpectedRevisionConflict", "ExecutionTarget", "LateResultRejected", "PreparationRequired", "ProcessExecutionAdapter", "RayJobAdapter", "RESEARCH_WORLD_WORKER_COMMAND", "UnavailableChildAdapter"]
