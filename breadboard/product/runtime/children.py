"""Internal Session-centered durable child factory.

Child coordination is retained on the existing engine ``SessionRecord`` /
``SessionRegistry``.  Product ``Session``, Work Item and ArtifactStore remain
their existing owners; this module only composes their ordering.
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
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


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_TERMINAL = frozenset({"completed", "failed", "canceled"})
_CHILD_SCHEMA = "bb.durable_child.v1"

def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
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


def _parent_cancellation_marker(
    requests: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = _parent_cancellation_requests(
        {"requests": [dict(request) for request in requests]}
    )
    return {"requests": [dict(request) for request in normalized]}

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
    workflow_id: str | None = None
    workflow_step_id: str | None = None
    workflow_definition_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.lock, EffectiveHarnessLock):
            raise TypeError("child lock must be an EffectiveHarnessLock")
        for value, name in ((self.title, "title"), (self.task, "task"), (self.worker_id, "worker_id"), (self.adapter_family, "adapter_family")):
            if type(value) is not str or not value.strip():
                raise ValueError(f"child {name} must be non-empty")
        workflow_fields = (
            self.workflow_id,
            self.workflow_step_id,
            self.workflow_definition_hash,
        )
        if any(value is not None for value in workflow_fields):
            if any(
                type(value) is not str or not value.strip()
                for value in workflow_fields
            ):
                raise ValueError(
                    "child workflow identity fields must be non-empty strings"
                )
            if not _is_sha256(self.workflow_definition_hash):
                raise ValueError("child workflow definition hash is invalid")

    def retained(self) -> dict[str, Any]:
        task_hash = "sha256:" + hashlib.sha256(self.task.encode()).hexdigest()
        lock_hash = self.lock.as_dict().get("graph_hash")
        if type(lock_hash) is not str:
            raise ValueError("child lock has no graph hash")
        retained = {
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
        if self.workflow_id is not None:
            retained.update(
                {
                    "workflow_id": self.workflow_id,
                    "workflow_step_id": self.workflow_step_id,
                    "workflow_definition_hash": self.workflow_definition_hash,
                }
            )
        return retained




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
        identity_fields = (
            "child_session_id",
            "child_work_item_id",
            "parent_session_id",
            "root_session_id",
            "parent_work_item_id",
            "attempt_id",
            "recovery_ref",
            "adapter_family",
        )
        if any(
            type(value.get(field_name)) is not str
            or not value[field_name].strip()
            for field_name in identity_fields
        ):
            raise ValueError("durable child identity is invalid")
        child_session_id = value["child_session_id"]
        attempt_id = value["attempt_id"]
        recovery_prefix = f"child://{child_session_id}/attempt/"
        recovery_ref = value["recovery_ref"]
        if (
            not recovery_ref.startswith(recovery_prefix)
            or not recovery_ref.removeprefix(recovery_prefix)
        ):
            raise ValueError("durable child recovery identity is invalid")
        revision = value.get("revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("durable child revision is invalid")
        launch_claim_owner = value.get("launch_claim_owner")
        if launch_claim_owner is not None and (
            type(launch_claim_owner) is not str or not launch_claim_owner.strip()
        ):
            raise ValueError("durable child launch claim owner is invalid")
        launch_claim_until = value.get("launch_claim_until")
        if launch_claim_until is not None and (
            type(launch_claim_until) not in {int, float}
            or not math.isfinite(launch_claim_until)
            or launch_claim_until < 0
        ):
            raise ValueError("durable child launch claim expiry is invalid")
        cancellation_reason = value.get("cancellation_reason")
        if cancellation_reason is not None and (
            type(cancellation_reason) is not str or not cancellation_reason.strip()
        ):
            raise ValueError("durable child cancellation reason is invalid")
        if value.get("startup_phase", "unknown") not in {
            "unknown",
            "recorded",
            "delegated",
            "product_published",
        }:
            raise ValueError("durable child startup phase is invalid")
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
        terminal_count = value.get("terminal_count", 0)
        status = value.get("status")
        terminal_outcome = value.get("terminal_outcome")
        allowed_statuses = {"starting", "running", "cancel_requested", *_TERMINAL}
        if status not in allowed_statuses:
            raise ValueError("durable child status is invalid")
        if type(terminal_count) is not int or terminal_count not in {0, 1}:
            raise ValueError("durable child terminal_count must be exactly 0 or 1")
        if terminal_count == 0 and (
            terminal_outcome is not None or status in _TERMINAL
        ):
            raise ValueError("nonterminal durable child cannot have a terminal outcome")
        if terminal_count == 1 and (
            terminal_outcome not in _TERMINAL or status != terminal_outcome
        ):
            raise ValueError("terminal durable child status and outcome must agree")
        execution_target_ref = value.get("execution_target_ref")
        execution_target = value.get("execution_target")
        if (
            type(execution_target_ref) is not str
            or not execution_target_ref.strip()
            or not isinstance(execution_target, Mapping)
            or execution_target.get("ref") != execution_target_ref
        ):
            raise ValueError("durable child execution target identity is invalid")
        result_refs_value = value.get("result_refs", ())
        if (
            not isinstance(result_refs_value, (list, tuple))
            or any(not _is_sha256(ref) for ref in result_refs_value)
            or len(set(result_refs_value)) != len(result_refs_value)
        ):
            raise ValueError("durable child result refs are invalid")
        result_refs = tuple(result_refs_value)
        settlement = value.get("settlement")
        if settlement is not None:
            if not isinstance(settlement, Mapping):
                raise ValueError("durable child settlement is invalid")
            settlement_outcome = settlement.get("outcome")
            settlement_refs = settlement.get("result_refs")
            if (
                settlement_outcome not in _TERMINAL
                or not isinstance(settlement_refs, (list, tuple))
                or any(type(ref) is not str for ref in settlement_refs)
                or tuple(settlement_refs) != result_refs
                or (
                    settlement_outcome == "completed"
                    and value.get("result_prepared", False) is not True
                )
            ):
                raise ValueError("durable child settlement is invalid")
        child_spec_value = value.get("child_spec")
        if not isinstance(child_spec_value, Mapping):
            raise ValueError("durable child specification is invalid")
        child_spec = dict(child_spec_value)
        required_spec_strings = (
            "title",
            "task_hash",
            "task_ref",
            "lock_hash",
            "worker_id",
            "adapter_family",
        )
        if any(
            type(child_spec.get(field_name)) is not str
            or not child_spec[field_name].strip()
            for field_name in required_spec_strings
        ):
            raise ValueError("durable child specification is invalid")
        workflow_fields = (
            child_spec.get("workflow_id"),
            child_spec.get("workflow_step_id"),
            child_spec.get("workflow_definition_hash"),
        )
        if any(field is not None for field in workflow_fields):
            if any(
                type(field) is not str or not field.strip()
                for field in workflow_fields
            ):
                raise ValueError("durable child workflow identity is invalid")
            if not _is_sha256(workflow_fields[2]):
                raise ValueError("durable child workflow identity is invalid")
        if (
            child_spec["adapter_family"] != value.get("adapter_family")
            or child_spec["task_ref"] != "child-task://" + child_spec["task_hash"]
        ):
            raise ValueError("durable child specification identity is invalid")
        policy_fields = ("retry_policy", "resume_policy", "cancellation_policy")
        if any(
            not isinstance(child_spec.get(field_name), Mapping)
            for field_name in policy_fields
        ):
            raise ValueError("durable child specification policies are invalid")
        adapter_config = child_spec.get("adapter_config", {})
        if not isinstance(adapter_config, Mapping):
            raise ValueError("durable child adapter config is invalid")
        try:
            RetryPolicy.from_dict(child_spec["retry_policy"])
            ResumePolicy.from_dict(child_spec["resume_policy"])
            CancellationPolicy.from_dict(child_spec["cancellation_policy"])
            EffectiveHarnessLock._from_record(
                {"graph_hash": child_spec["lock_hash"]}
            )
            task_artifact = child_spec.get("task_artifact_ref")
            if task_artifact is not None:
                if not isinstance(task_artifact, Mapping):
                    raise ValueError("task artifact must be a mapping")
                ArtifactRef(
                    str(task_artifact["digest"]),
                    int(task_artifact["size_bytes"]),
                    str(task_artifact["media_type"]),
                )
            json.dumps(child_spec)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("durable child specification is invalid") from error
        for field_name in (
            "task_artifact_store",
            "artifact_store_root",
            "work_item_repository_path",
        ):
            path_value = child_spec.get(field_name)
            if path_value is not None and (
                type(path_value) is not str
                or not path_value
                or not Path(path_value).is_absolute()
            ):
                raise ValueError("durable child specification path is invalid")
        return cls(
            child_session_id=child_session_id,
            child_work_item_id=value["child_work_item_id"],
            parent_session_id=value["parent_session_id"],
            root_session_id=value["root_session_id"],
            parent_work_item_id=value["parent_work_item_id"],
            attempt_id=attempt_id,
            recovery_ref=value["recovery_ref"],
            execution_target_ref=execution_target_ref,
            adapter_family=value["adapter_family"],
            status=status,
            revision=revision,
            cancellation_requested=value.get("cancellation_requested", False),
            launch_claimed=value.get("launch_claimed", False),
            launch_claim_owner=launch_claim_owner,
            launch_claim_until=(
                float(launch_claim_until)
                if launch_claim_until is not None
                else None
            ),
            launch_published=value.get("launch_published", False),
            startup_phase=str(value.get("startup_phase") or "unknown"),
            cancellation_reason=value.get("cancellation_reason"),
            result_prepared=value.get("result_prepared", False),
            result_refs=result_refs,
            terminal_outcome=terminal_outcome,
            terminal_count=terminal_count,
            joined=value.get("joined", False),
            settlement=settlement,
            child_spec=child_spec,
            execution_target=execution_target,
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
                isinstance(retained, Mapping)
                and retained.get("parent_work_item_id") == parent_work_item_id
            ):
                states.append(self._record_state(record.session_id))
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
        transition_ids = self._root_transition_session_ids(root_session_id)
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id), self._product_transition_guard(*transition_ids):
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
        transition_ids = self._root_transition_session_ids(root_session_id)
        with self._lifecycle_lock, self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id), self._product_transition_guard(*transition_ids):
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
            self._registry(
                "close_admission_for_parent_cancellation",
                parent_session_id,
                work_item_id=parent_work_item_id,
                reason=reason,
                child_recovery_refs=[
                    state.recovery_ref for state in descendants
                ],
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
        return _sync(getattr(self.registry, method)(*args, **kwargs))

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
        if type(root_session_id) is not str or not root_session_id.strip():
            raise ValueError("root_session_id must be a non-empty string")
        with self._lifecycle_lock, self._tree_process_lock(root_session_id), self._owner_lock(parent_work_item_id), self._owner_process_lock(parent_work_item_id):
            canonical_root_session_id = self._tree_root_session_id(parent_session_id)
            if root_session_id != canonical_root_session_id:
                raise ChildError("root Session does not match retained parent lineage")
            return self._start(parent_session_id=parent_session_id, root_session_id=canonical_root_session_id, parent_work_item_id=parent_work_item_id, spec=spec)
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
        config = config_fn() if callable(config_fn) else {}
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

    def cancel(self, child_session_id: str, *, expected_revision: int, reason: str = "operator request") -> ChildState:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        state = self._record_state(child_session_id)
        with self._lifecycle_lock, self._owner_lock(state.parent_work_item_id), self._owner_process_lock(state.parent_work_item_id), self._product_transition_guard(state.parent_session_id, state.root_session_id, state.child_session_id):
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
        result_refs: Sequence[str] = (),
        attempt_id: str | None = None,
        _allow_cancellation_intent: bool = False,
    ) -> ChildState:
        state = self._record_state(child_session_id)
        with (
            self._lifecycle_lock,
            self._owner_lock(state.parent_work_item_id),
            self._owner_process_lock(state.parent_work_item_id),
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
        cleanup_handoff = getattr(adapter, "cleanup_handoff", None)
        if callable(cleanup_handoff):
            cleanup_handoff(state.execution_target)
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
                    "outcome": observed,
                    "result_refs": list(current.result_refs),
                },
            )
        return self._settle(
            current,
            observed,
            current.result_refs,
            allow_unprepared=observed != "completed",
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
            if (
                parent_product.read_model.status in _TERMINAL
                or parent_work.read_model.status in _TERMINAL
                or parent_attempt is None
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
                    raise ChildError(
                        "Work Item terminal outcome disagrees with settlement"
                    ) from error
                raise
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
            product = Session.start(spec.lock, spec.task, session_id=state.child_session_id, clock=self.clock, ids=self.ids)
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
    def reconcile(self, recovery_ref: str) -> ChildState:
        child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
        state = self._record_state(child_session_id)
        with (
            self._lifecycle_lock,
            self._owner_lock(state.parent_work_item_id),
            self._owner_process_lock(state.parent_work_item_id),
            self._product_transition_guard(
                state.parent_session_id,
                state.root_session_id,
                state.child_session_id,
            ),
        ):
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
            acknowledge = getattr(self.adapters[state.adapter_family], "acknowledge_result", None)
            if callable(acknowledge):
                acknowledge(state.execution_target)
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
        if observed == "absent":
            cleanup_handoff = getattr(adapter, "cleanup_handoff", None)
            if callable(cleanup_handoff):
                cleanup_handoff(state.execution_target)
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
        available_families = {adapter.family for adapter in adapters}
        adapters.extend(
            UnavailableChildAdapter(retained_family)
            for retained_family in sorted(retained_families - available_families)
        )
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
        for candidate in records:
            metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
            retained = metadata.get("durable_child")
            if not isinstance(retained, Mapping) or retained.get("parent_session_id") != parent_session_id:
                continue
            parent_work_item_id = str(retained.get("parent_work_item_id") or "").strip()
            recovery_ref = str(retained.get("recovery_ref") or "").strip()
            if parent_work_item_id and recovery_ref:
                parent_work_item_ids.setdefault(parent_work_item_id, recovery_ref)
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
                if (
                    not isinstance(retained, Mapping)
                    or retained.get("parent_session_id")
                    not in descendant_session_ids
                    or candidate.session_id in descendant_session_ids
                ):
                    continue
                descendant_session_ids.add(candidate.session_id)
                recovery_ref = retained.get("recovery_ref")
                if isinstance(recovery_ref, str) and recovery_ref.strip():
                    observed_child_refs.add(recovery_ref)
                changed = True
        if not parent_work_item_ids:
            return ()
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


class RayJobAdapter:
    family = "ray-agent-job"
    absence_is_terminal = True

    def __init__(self, orchestrator: Any, *, actor_launcher: Any | None = None) -> None:
        self.orchestrator = orchestrator
        self._default_actor_launcher = actor_launcher is None
        self._actor_launcher = self._launch_actor if self._default_actor_launcher else actor_launcher
        self._actors: dict[str, Any] = {}
        self._actor_lookup_unavailable: set[str] = set()
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
            deadline = time.monotonic() + 30.0
            while True:
                state = RayJobAdapter._invocation_state(actor, invocation_id)
                if state not in {None, "missing"}:
                    break
                if time.monotonic() >= deadline:
                    raise ChildError(
                        "Ray child invocation was not durably accepted"
                    )
                time.sleep(0.01)
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
        except (ImportError, RuntimeError):
            self._actor_lookup_unavailable.add(job_id)
            return None
        except ValueError:
            self._actor_lookup_unavailable.discard(job_id)
            return None
        self._actor_lookup_unavailable.discard(job_id)
        self._actors[job_id] = actor
        return actor

    def _refresh_actor_after_rpc_failure(
        self, job_id: str, failed_actor: Any
    ) -> Any | None:
        if self._actors.get(job_id) is failed_actor:
            self._actors.pop(job_id, None)
        try:
            refreshed = self._lookup_actor(job_id)
        except BaseException:
            return None
        return refreshed
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
                state=(
                    "accepted"
                    if str(job_data.get("state") or "accepted") == "completed"
                    else str(job_data.get("state") or "accepted")
                ),
                seq=int(job_data.get("seq") or 0),
                task_descriptor=dict(job_data.get("task_descriptor") or {}),
                result_payload=dict(job_data["result_payload"])
                if isinstance(job_data.get("result_payload"), Mapping)
                else None,
            )
        except (KeyError, TypeError, ValueError):
            return
        self.orchestrator.job_manager.restore_job(job)

    def _mark_job_failed(self, target: Mapping[str, Any], job_id: str) -> None:
        marked = self.orchestrator.job_manager.update_state(job_id, "failed")
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if isinstance(job_data, dict):
            job_data["state"] = "failed"
            if marked is not None:
                job_data["seq"] = marked.seq


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
        elif not isinstance(job.task_descriptor, dict) or job.task_descriptor.get("invocation_id") != invocation_id:
            job.task_descriptor = dict(job.task_descriptor or {})
            job.task_descriptor["invocation_id"] = invocation_id
        if job.state in {"completed", "failed", "killed"}:
            raise ChildError(
                f"Ray job {job_id} is already terminal ({job.state})"
            )
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
            try:
                durable_payload = self._durably_prepare_result(
                    target, job_data["result_payload"]
                )
            except FileNotFoundError:
                self._mark_job_failed(target, job_id)
                return "failed"
            except OSError:
                return "accepted"
            except (ChildError, KeyError, RuntimeError, TypeError, ValueError):
                self._mark_job_failed(target, job_id)
                return "failed"
            if job is not None and job.state == "completed":
                marked = job
            else:
                marked = self.orchestrator.job_manager.update_state(
                    job_id,
                    "completed",
                    result_payload=dict(durable_payload),
                )
            if marked is None:
                self._mark_job_failed(target, job_id)
                return "failed"
            if isinstance(job_data, dict):
                job_data["state"] = "completed"
                job_data["seq"] = marked.seq
                job_data["result_payload"] = dict(durable_payload)
            return "completed"
        actor = self._lookup_actor(job_id)
        if actor is None:
            if job_id in self._actor_lookup_unavailable:
                return "pending"
            if job is not None and job.state == "completed":
                return "completed"
            if isinstance(job_data, Mapping) and job_data.get("seq") == 0:
                return "absent"
            if job is not None and job.state not in {"failed", "killed"}:
                self.orchestrator.job_manager.update_state(job_id, "failed")
            return "absent"
        if self._invocation_state(actor, self._invocation_id(job_id)) == "missing":
            if isinstance(job_data, Mapping) and job_data.get("seq") == 0:
                return "absent"
            return "absent" if self.cancel(target) else "pending"
        try:
            state = str(self._ray_get(getattr(actor, "get_state", None))).lower()
        except BaseException:
            if getattr(actor, "get_invocation_state", None) is None:
                return "pending"
            actor = self._refresh_actor_after_rpc_failure(job_id, actor)
            if actor is None:
                if job_id in self._actor_lookup_unavailable:
                    return "pending"
                self._mark_job_failed(target, job_id)
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
            try:
                result_payload = self._ray_get(result)
            except BaseException:
                return "pending"
            if not isinstance(result_payload, Mapping):
                if job is not None and job.state not in {"failed", "killed"}:
                    self.orchestrator.job_manager.update_state(job_id, "failed")
                return "failed"
            try:
                durable_payload = self._durably_prepare_result(target, result_payload)
            except ChildError:
                self._mark_job_failed(target, job_id)
                return "failed"
            except (KeyError, TypeError, ValueError):
                self._mark_job_failed(target, job_id)
                return "failed"
            except FileNotFoundError:
                self._mark_job_failed(target, job_id)
                return "failed"
            except OSError:
                return "accepted"
            except RuntimeError:
                self._mark_job_failed(target, job_id)
                return "failed"
            metadata = target.get("metadata")
            job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
            if isinstance(job_data, dict):
                job_data["state"] = "completed"
                job_data["result_payload"] = dict(durable_payload)
        if state == "failed":
            self._mark_job_failed(target, job_id)
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
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        has_recovery_metadata = isinstance(job_data, Mapping) and all(
            key in job_data for key in ("agent_id", "owner_agent", "kind")
        )
        self._restore_job(target, job_id)
        job = self.orchestrator.job_manager.get(job_id)
        if job is not None and job.state in {"completed", "failed"}:
            return False
        if job is not None and job.state == "killed":
            return True
        actor = self._lookup_actor(job_id)
        if actor is None:
            if has_recovery_metadata:
                return False
            return (
                self.orchestrator.job_manager.update_state(job_id, "killed") is not None
            )
        try:
            cancel = getattr(actor, "cancel", None)
            if cancel is None:
                import ray

                ray.kill(actor, no_restart=True)
                cancellation_state = "killed"
            else:
                cancellation_state = self._ray_get(cancel)
                if cancellation_state is False:
                    return False
                cancellation_state = (
                    "killed"
                    if cancellation_state is True
                    else str(cancellation_state).lower()
                )
                if cancellation_state in {"completed", "failed"}:
                    observed = self.observe(target)
                    if observed == "completed":
                        self.acknowledge_result(target)
                    return False
                if cancellation_state != "killed":
                    return False
        except BaseException:
            return False
        marked = self.orchestrator.job_manager.update_state(job_id, "killed")
        if job is not None and marked is None:
            return False
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
        self._status_paths: dict[str, Path] = {}
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
        while self.observe(target) not in {"absent", "completed"}:
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

    def _control_path(
        self,
        target_ref: str,
        suffix: str,
        workspace: Path | None = None,
    ) -> Path:
        root = workspace if workspace is not None else self._workspace
        if root is None:
            raise ChildError("process child adapter is not bound to a workspace")
        status_root = root / ".breadboard" / "process-children"
        status_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if status_root.is_symlink() or not status_root.is_dir():
            raise ChildError("process child status root is not a directory")
        status_root.chmod(0o700)
        identity = hashlib.sha256(target_ref.encode("utf-8")).hexdigest()
        return status_root / f"{identity}.{suffix}"

    def _status_path(
        self,
        target_ref: str,
        workspace: Path | None = None,
    ) -> Path:
        return self._control_path(target_ref, "status", workspace)

    def _known_control_path(self, target_ref: str, suffix: str) -> Path:
        status_path = self._status_paths.get(target_ref)
        if status_path is not None:
            return status_path.with_suffix(f".{suffix}")
        return self._control_path(target_ref, suffix)

    @staticmethod
    def _write_control(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            stream = os.fdopen(descriptor, "wb")
            descriptor = -1
            with stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _release(self, target_ref: str) -> None:
        self._write_control(self._known_control_path(target_ref, "release"), b"1")

    def _clear_handoff(self, target_ref: str) -> None:
        for suffix in ("task", "release"):
            try:
                self._known_control_path(target_ref, suffix).unlink(missing_ok=True)
            except ChildError:
                return

    def _completed_status(self, target_ref: str) -> bool | None:
        path = self._status_paths.get(target_ref)
        if path is None:
            try:
                path = self._status_path(target_ref)
            except ChildError:
                return None
        try:
            value = path.read_text(encoding="ascii")
        except OSError:
            return None
        if value == "0":
            return True
        try:
            int(value)
        except ValueError:
            return None
        return False

    @staticmethod
    def _process_start_token(pid: int) -> str | int | None:
        if sys.platform.startswith("linux"):
            try:
                data = Path(f"/proc/{pid}/stat").read_bytes()
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                    encoding="ascii"
                ).strip()
            except OSError:
                return None
            fields = data[data.rfind(b")") + 1 :].split()
            if len(fields) <= 19:
                return None
            try:
                start_time = int(fields[19])
            except ValueError:
                return None
            return f"{boot_id}:{start_time}"
        if sys.platform == "darwin":
            try:
                libproc = ctypes.CDLL("libproc.dylib", use_errno=True)
            except OSError:
                return None
            proc_pidinfo = libproc.proc_pidinfo
            proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            proc_pidinfo.restype = ctypes.c_int
            info = _DarwinProcBsdInfo()
            size = ctypes.sizeof(info)
            if proc_pidinfo(pid, 3, 0, ctypes.byref(info), size) != size:
                return None
            if info.pbi_start_tvsec == 0 or info.pbi_start_tvusec >= 1_000_000:
                return None
            return (
                int(info.pbi_start_tvsec) * 1_000_000
                + int(info.pbi_start_tvusec)
            )
        return None

    @classmethod
    def _identity(cls, pid: int) -> tuple[str, int]:
        start_token = cls._process_start_token(pid)
        if start_token is None:
            raise ProcessLookupError(pid)
        group = os.getpgid(pid)
        return f"kernel:{start_token}", group
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
        status_path = self._status_path(target_ref, workspace)
        task_path = self._control_path(target_ref, "task", workspace)
        release_path = self._control_path(target_ref, "release", workspace)
        self._status_paths[target_ref] = status_path
        for path in (status_path, task_path, release_path):
            path.unlink(missing_ok=True)
        self._write_control(task_path, spec.task.encode("utf-8"))
        wrapper = (
            "import os,signal,subprocess,sys,time\n"
            "release=sys.argv[2]\n"
            "while not os.path.exists(release): time.sleep(0.01)\n"
            "task=open(sys.argv[3],'rb').read()\n"
            "status=sys.argv[4]\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            "reset_term=lambda: signal.signal(signal.SIGTERM,signal.SIG_DFL)\n"
            "result=subprocess.run(sys.argv[5:],input=task,preexec_fn=reset_term).returncode\n"
            "group=os.getpgrp()\n"
            "def descendants_alive():\n"
            " try: rows=subprocess.check_output(['ps','-axo','pid=,pgid=,stat='],text=True,start_new_session=True).splitlines()\n"
            " except (OSError,subprocess.CalledProcessError): return True\n"
            " for row in rows:\n"
            "  fields=row.strip().split()\n"
            "  if len(fields)>=3 and int(fields[0])!=os.getpid() and int(fields[1])==group and not fields[2].startswith('Z'): return True\n"
            " return False\n"
            "while descendants_alive(): time.sleep(0.01)\n"
            "temporary=f'{status}.{os.getpid()}.tmp'\n"
            "fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
            "os.write(fd,str(result).encode('ascii'));os.fsync(fd);os.close(fd)\n"
            "os.replace(temporary,status)\n"
            "directory=os.open(os.path.dirname(status),os.O_RDONLY)\n"
            "os.fsync(directory);os.close(directory)\n"
            "os._exit(result)\n"
        )
        process = subprocess.Popen(
            (
                sys.executable,
                "-c",
                wrapper,
                target_ref,
                str(release_path),
                str(task_path),
                str(status_path),
                *self.command,
            ),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(workspace),
        )
        self._processes[target_ref] = process
        token, group = self._identity(process.pid)
        metadata: dict[str, Any] = {"launch_phase": "pending"}
        target = ExecutionTarget(target_ref, process.pid, token, group, process, metadata)
        try:
            self._publish(target)
            publisher = activation.publish_target
            if publisher is not None:
                publisher(target)
            metadata["launch_phase"] = "release_committed"
            if publisher is not None:
                publisher(target)
            self._release(target_ref)
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
            completed = self._completed_status(target_ref)
            return "completed" if completed is True else "absent"
        if type(pid) is not int or type(token) is not str:
            return "pending"
        try:
            observed_token, observed_group = self._identity(pid)
        except ProcessLookupError:
            alive = group_state()
            if alive is False:
                completed = self._completed_status(target_ref)
                return "completed" if completed is True else "absent"
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

    def _verified_pending_process(self, target: Mapping[str, Any]) -> bool:
        pid = target.get("pid")
        token = target.get("start_token")
        group = target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            return False
        try:
            if self._identity(pid) != (token, group):
                return False
            command = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="],
                text=True,
            )
        except (OSError, ProcessLookupError, subprocess.CalledProcessError):
            return False
        return str(target.get("ref", "")) in command

    def release_committed(self, target: Mapping[str, Any]) -> bool:
        if not self._verified_pending_process(target):
            return False
        self._release(str(target["ref"]))
        return True

    def release_pending(self, target: Mapping[str, Any]) -> bool:
        metadata = target.get("metadata")
        if not isinstance(metadata, Mapping):
            return False
        phase = metadata.get("launch_phase")
        if phase == "pending":
            return self._verified_pending_process(target)
        if phase == "release_committed":
            return self.release_committed(target)
        return False

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
        if self.observe(
            {
                "ref": str(target.get("ref", "")),
                "pid": pid,
                "start_token": token,
                "process_group_id": group,
            }
        ) != "running":
            return None
        recovered_metadata = dict(target.get("metadata") or {})
        recovered_metadata.setdefault("launch_phase", "pending")
        return ExecutionTarget(
            str(target["ref"]),
            pid,
            token,
            group,
            metadata=recovered_metadata,
        )
    def _signal_verified(self, target: Mapping[str, Any], signum: int) -> bool:
        pid = target.get("pid")
        token = target.get("start_token")
        group = target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            return False
        try:
            observed_token, observed_group = self._identity(pid)
        except (OSError, ProcessLookupError, RuntimeError):
            return False
        if observed_token != token or observed_group != group:
            return False
        try:
            os.killpg(group, signum)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return True

    def cancel(self, target: Mapping[str, Any]) -> bool:
        observed = self.observe(target)
        target_ref = str(target.get("ref", ""))
        if observed == "absent":
            self._clear_handoff(target_ref)
            return True
        if observed != "running":
            return False
        if not self._signal_verified(target, 15):
            return False
        if self._wait_for_exit(target, self._TERM_TIMEOUT_SECONDS):
            self._clear_handoff(target_ref)
            return True
        if not self._signal_verified(target, 9):
            return False
        exited = self._wait_for_exit(target, self._KILL_TIMEOUT_SECONDS)
        if exited:
            self._clear_handoff(target_ref)
        return exited

    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | None:
        return None


    def cleanup_handoff(self, target: Mapping[str, Any]) -> None:
        self._clear_handoff(str(target.get("ref", "")))

    def acknowledge_result(self, target: Mapping[str, Any]) -> None:
        self._clear_handoff(str(target.get("ref", "")))



__all__ = ["ChildActivation", "ChildError", "ChildExecutionAdapter", "ChildSpec", "ChildState", "DurableChildFactory", "DurableChildReconciler", "ExpectedRevisionConflict", "ExecutionTarget", "LateResultRejected", "PreparationRequired", "ProcessExecutionAdapter", "RayJobAdapter", "UnavailableChildAdapter"]
