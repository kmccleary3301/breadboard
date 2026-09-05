"""Replayable workflow decisions over durable child and Work Item owners."""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import ClassVar, Literal

from breadboard.product.coordination.work_items import (
    TERMINAL_STATUSES,
    WorkItemEvent,
    project_work_item_replay,
)
from breadboard.product.projection import (
    Projected,
    ProjectionCursor,
    ProjectionSource,
)

from .children import ChildError, ChildSpec, ChildState, DurableChildFactory
from .events import ProcessLock

WorkflowAction = Literal["start", "wait", "complete", "fail", "cancel"]

WORKFLOW_PROJECTOR_VERSION = "bb.workflow.projector.v1"

@dataclass(frozen=True, slots=True)
class WorkflowStep:
    step_id: str
    child: ChildSpec
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.step_id) is not str or not self.step_id.strip():
            raise ValueError("workflow step_id must be a non-empty string")
        if not isinstance(self.child, ChildSpec):
            raise TypeError("workflow child must be a ChildSpec")
        if any(
            value is not None
            for value in (
                self.child.workflow_id,
                self.child.workflow_step_id,
                self.child.workflow_definition_hash,
            )
        ):
            raise ValueError("workflow definitions require unbound child specifications")
        dependencies = tuple(sorted(self.depends_on))
        if any(
            type(dependency) is not str or not dependency.strip()
            for dependency in dependencies
        ):
            raise ValueError("workflow dependencies must be non-empty strings")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("workflow dependencies must be unique")
        object.__setattr__(self, "depends_on", dependencies)


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    steps: tuple[WorkflowStep, ...]
    _by_id: Mapping[str, WorkflowStep] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        steps = tuple(self.steps)
        if not steps:
            raise ValueError("workflow definition requires at least one step")
        if any(not isinstance(step, WorkflowStep) for step in steps):
            raise TypeError("workflow steps must be WorkflowStep values")
        by_id = {step.step_id: step for step in steps}
        if len(by_id) != len(steps):
            raise ValueError("workflow step_id values must be unique")
        for step in steps:
            unknown = set(step.depends_on) - set(by_id)
            if unknown:
                raise ValueError(
                    "workflow dependency is unknown: " + min(unknown)
                )
            if step.step_id in step.depends_on:
                raise ValueError("workflow step cannot depend on itself")
        resolved: set[str] = set()
        remaining = set(by_id)
        while remaining:
            ready = {
                step_id
                for step_id in remaining
                if set(by_id[step_id].depends_on).issubset(resolved)
            }
            if not ready:
                raise ValueError("workflow dependency graph contains a cycle")
            resolved.update(ready)
            remaining.difference_update(ready)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))

    def step(self, step_id: str) -> WorkflowStep:
        return self._by_id[step_id]

    def identity(self, workflow_id: str) -> str:
        payload = {
            "workflow_id": workflow_id,
            "steps": [
                {
                    "step_id": step.step_id,
                    "depends_on": list(step.depends_on),
                    "child": step.child.retained(),
                }
                for step in sorted(self.steps, key=lambda item: item.step_id)
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkflowDecision:
    workflow_id: str
    definition_hash: str
    action: WorkflowAction
    ready_step_ids: tuple[str, ...]
    active_step_ids: tuple[str, ...]
    completed_step_ids: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    canceled_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
    child_session_ids: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "workflow_id": self.workflow_id,
            "definition_hash": self.definition_hash,
            "action": self.action,
            "ready_step_ids": list(self.ready_step_ids),
            "active_step_ids": list(self.active_step_ids),
            "completed_step_ids": list(self.completed_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
            "canceled_step_ids": list(self.canceled_step_ids),
            "blocked_step_ids": list(self.blocked_step_ids),
            "child_session_ids": {
                step_id: session_id
                for step_id, session_id in self.child_session_ids
            },
        }


def project_workflow_decision(
    definition: WorkflowDefinition,
    *,
    workflow_id: str,
    parent_work_item_events: Iterable[WorkItemEvent],
    children: Iterable[tuple[ChildState, Iterable[WorkItemEvent]]],
) -> Projected[WorkflowDecision]:
    """Purely fold Work Item streams and durable child bindings into a decision."""
    if not isinstance(definition, WorkflowDefinition):
        raise TypeError("definition must be a WorkflowDefinition")
    if type(workflow_id) is not str or not workflow_id.strip():
        raise ValueError("workflow_id must be a non-empty string")
    definition_hash = definition.identity(workflow_id)
    child_rows = tuple(children)
    if any(
        type(row) is not tuple
        or len(row) != 2
        or not isinstance(row[0], ChildState)
        for row in child_rows
    ):
        raise TypeError(
            "workflow children must contain (ChildState, WorkItem events) tuples"
        )
    child_rows = tuple(
        sorted(child_rows, key=lambda row: row[0].child_session_id)
    )
    parent = project_work_item_replay(tuple(parent_work_item_events))
    statuses: dict[str, str] = {}
    child_session_ids: dict[str, str] = {}
    sources = [parent.source]
    cursors = [
        ProjectionCursor(parent.source.stream, parent.source.last_sequence)
    ]
    for state, child_events in child_rows:
        if state.parent_work_item_id != parent.value.work_item_id:
            raise ChildError("retained workflow child has the wrong parent Work Item")
        child_spec = state.child_spec
        if child_spec.get("workflow_id") != workflow_id:
            raise ChildError("retained workflow identity does not match")
        if child_spec.get("workflow_definition_hash") != definition_hash:
            raise ChildError(
                "retained workflow definition does not match current definition"
            )
        step_id = child_spec.get("workflow_step_id")
        if type(step_id) is not str or step_id not in definition._by_id:
            raise ChildError("retained workflow step identity is invalid")
        if step_id in statuses:
            raise ChildError("retained workflow step binding is duplicated")
        expected = replace(
            definition.step(step_id).child,
            workflow_id=workflow_id,
            workflow_step_id=step_id,
            workflow_definition_hash=definition_hash,
        ).retained()
        if any(child_spec.get(key) != value for key, value in expected.items()):
            raise ChildError("retained workflow child specification changed")
        child_state_source = ProjectionSource(
            f"child_state:{state.child_session_id}",
            1,
            state.revision + 1,
        )
        sources.append(child_state_source)
        cursors.append(
            ProjectionCursor(
                child_state_source.stream,
                child_state_source.last_sequence,
            )
        )
        child_event_rows = tuple(child_events)
        if not child_event_rows:
            if state.startup_phase != "recorded":
                raise ChildError(
                    "retained workflow child has no delegated Work Item stream"
                )
            if state.terminal_outcome == "completed":
                raise ChildError(
                    "pre-delegation workflow child cannot be completed"
                )
            statuses[step_id] = state.terminal_outcome or "starting"
            child_session_ids[step_id] = state.child_session_id
            continue
        projected_child = project_work_item_replay(child_event_rows)
        child_snapshot = projected_child.value
        if (
            child_snapshot.work_item_id != state.child_work_item_id
            or child_snapshot.parent_work_item_id != parent.value.work_item_id
        ):
            raise ChildError("retained workflow Work Item lineage does not match")
        if child_snapshot.status in TERMINAL_STATUSES:
            if (
                state.terminal_count != 1
                or state.terminal_outcome != child_snapshot.status
            ):
                raise ChildError(
                    "retained child settlement and Work Item terminal state diverged"
                )
        elif state.terminal_count != 0:
            raise ChildError(
                "retained child settlement and Work Item terminal state diverged"
            )
        statuses[step_id] = child_snapshot.status
        child_session_ids[step_id] = state.child_session_id
        sources.append(projected_child.source)
        cursors.append(
            ProjectionCursor(
                projected_child.source.stream,
                projected_child.source.last_sequence,
            )
        )

    completed = tuple(
        sorted(
            step_id
            for step_id, status in statuses.items()
            if status == "completed"
        )
    )
    failed = tuple(
        sorted(
            step_id for step_id, status in statuses.items() if status == "failed"
        )
    )
    canceled = tuple(
        sorted(
            step_id
            for step_id, status in statuses.items()
            if status == "canceled"
        )
    )
    active = tuple(
        sorted(
            step_id
            for step_id, status in statuses.items()
            if status not in TERMINAL_STATUSES
        )
    )
    completed_set = set(completed)
    unstarted = set(definition._by_id) - set(statuses)
    ready = tuple(
        sorted(
            step_id
            for step_id in unstarted
            if set(definition.step(step_id).depends_on).issubset(completed_set)
        )
    )
    blocked = tuple(sorted(unstarted - set(ready)))
    if parent.value.status == "failed":
        action: WorkflowAction = "fail"
    elif parent.value.status == "canceled":
        action = "cancel"
    elif parent.value.status == "completed":
        action = "complete"
    elif parent.value.status != "running":
        action = "wait"
    elif failed:
        action = "fail"
    elif canceled:
        action = "cancel"
    elif len(completed) == len(definition.steps):
        action = "complete"
    elif active:
        action = "wait"
    elif ready:
        action = "start"
    else:
        action = "wait"
    decision = WorkflowDecision(
        workflow_id=workflow_id,
        definition_hash=definition_hash,
        action=action,
        ready_step_ids=ready,
        active_step_ids=active,
        completed_step_ids=completed,
        failed_step_ids=failed,
        canceled_step_ids=canceled,
        blocked_step_ids=blocked,
        child_session_ids=tuple(sorted(child_session_ids.items())),
    )
    source_sequence = max(source.last_sequence for source in sources)
    return Projected(
        decision,
        WORKFLOW_PROJECTOR_VERSION,
        ProjectionSource(
            f"workflow:{workflow_id}",
            1,
            source_sequence,
            tuple(sources),
        ),
        tuple(cursors),
    )



class ReplayableWorkflowController:
    """Recompute and execute one DAG decision without owning durable state."""
    _locks: ClassVar[dict[str, threading.RLock]] = {}
    _locks_guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        factory: DurableChildFactory,
        *,
        workflow_id: str,
        parent_session_id: str,
        root_session_id: str,
        parent_work_item_id: str,
        definition: WorkflowDefinition,
    ) -> None:
        if not isinstance(factory, DurableChildFactory):
            raise TypeError("factory must be a DurableChildFactory")
        for value, name in (
            (workflow_id, "workflow_id"),
            (parent_session_id, "parent_session_id"),
            (root_session_id, "root_session_id"),
            (parent_work_item_id, "parent_work_item_id"),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(definition, WorkflowDefinition):
            raise TypeError("definition must be a WorkflowDefinition")
        self.factory = factory
        self.workflow_id = workflow_id
        self.parent_session_id = parent_session_id
        self.root_session_id = root_session_id
        self.parent_work_item_id = parent_work_item_id
        self.definition = definition
        self.definition_hash = definition.identity(workflow_id)
        lock_identity = hashlib.sha256(
            f"{parent_work_item_id}\0{workflow_id}".encode()
        ).hexdigest()
        self._lock_key = lock_identity
        self._process_lock_path = (
            factory.workspace / ".breadboard" / f"workflow-{lock_identity}.lock"
        )
        self._process_lock_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _thread_lock(cls, key: str) -> threading.RLock:
        with cls._locks_guard:
            return cls._locks.setdefault(key, threading.RLock())

    def decision(self) -> WorkflowDecision:
        with self._thread_lock(self._lock_key), ProcessLock(self._process_lock_path):
            return self._projection().value

    def advance(self) -> WorkflowDecision:
        with self._thread_lock(self._lock_key), ProcessLock(self._process_lock_path):
            for state in self._children():
                self.factory.reconcile(state.recovery_ref)
            pending = self._projection().value
            if pending.action == "start":
                step_id = pending.ready_step_ids[0]
                step = self.definition.step(step_id)
                tagged = replace(
                    step.child,
                    workflow_id=self.workflow_id,
                    workflow_step_id=step_id,
                    workflow_definition_hash=self.definition_hash,
                )
                self.factory.start(
                    parent_session_id=self.parent_session_id,
                    root_session_id=self.root_session_id,
                    parent_work_item_id=self.parent_work_item_id,
                    spec=tagged,
                )
            return self._projection().value

    def _children(self) -> tuple[ChildState, ...]:
        return tuple(
            state
            for state in self.factory.child_states(
                parent_work_item_id=self.parent_work_item_id
            )
            if state.child_spec.get("workflow_id") == self.workflow_id
        )

    def _projection(self) -> Projected[WorkflowDecision]:
        children = self._children()
        return project_workflow_decision(
            self.definition,
            workflow_id=self.workflow_id,
            parent_work_item_events=self.factory.repository.read(
                self.parent_work_item_id
            ),
            children=(
                (
                    state,
                    self.factory.repository.read(state.child_work_item_id),
                )
                for state in children
            ),
        )


__all__ = [
    "WORKFLOW_PROJECTOR_VERSION",
    "ReplayableWorkflowController",
    "WorkflowAction",
    "WorkflowDecision",
    "WorkflowDefinition",
    "WorkflowStep",
    "project_workflow_decision",
]
