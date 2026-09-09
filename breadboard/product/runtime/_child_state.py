"""Durable child specifications, retained state, and execution contracts."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from breadboard.product.coordination.work_items import CancellationPolicy, ResumePolicy, RetryPolicy
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import ArtifactRef

_TERMINAL = frozenset({"completed", "failed", "canceled"})
_CHILD_SCHEMA = "bb.durable_child.v1"

def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


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

    adapter_config: Mapping[str, Any] = field(default_factory=dict, compare=False)
    def __post_init__(self) -> None:
        if not isinstance(self.lock, EffectiveHarnessLock):
            raise TypeError("child lock must be an EffectiveHarnessLock")
        for value, name in ((self.title, "title"), (self.task, "task"), (self.worker_id, "worker_id"), (self.adapter_family, "adapter_family")):
            if type(value) is not str or not value.strip():
                raise ValueError(f"child {name} must be non-empty")
        if not isinstance(self.adapter_config, Mapping):
            raise TypeError("child adapter config must be a mapping")
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
        generation_id = self.lock.generation_id
        retained = {
            "title": self.title,
            "task_hash": task_hash,
            "task_ref": "child-task://" + task_hash,
            "lock_hash": generation_id,
            "worker_id": self.worker_id,
            "adapter_family": self.adapter_family,
            "retry_policy": self.retry_policy.as_dict(),
            "resume_policy": self.resume_policy.as_dict(),
            "cancellation_policy": self.cancellation_policy.as_dict(),
        }
        if self.adapter_config:
            retained["adapter_config"] = dict(self.adapter_config)
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
        recovery_ref = value["recovery_ref"]
        if recovery_ref != f"child://{child_session_id}/attempt/{attempt_id}":
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
        joined = value.get("joined", False)
        if joined and terminal_count != 1:
            raise ValueError(
                "joined durable child must have one terminal outcome"
            )
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
                {"generation_id": child_spec["lock_hash"]}
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
            joined=joined,
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
