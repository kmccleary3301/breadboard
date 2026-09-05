from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from breadboard.product.coordination.work_items import (
    CancellationPolicy,
    ResumePolicy,
    RetryPolicy,
    WorkItem,
    WorkItemRepository,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import children as children_module
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime.children import (
    ChildActivation,
    ChildError,
    ChildSpec,
    ChildState,
    DurableChildFactory,
    DurableChildReconciler,
    ExecutionTarget,
    ExpectedRevisionConflict,
    LateResultRejected,
    PreparationRequired,
    ProcessExecutionAdapter,
    RayJobAdapter,
    UnavailableChildAdapter,
)
from breadboard.product.runtime.events import Session
from breadboard.product.runtime.session_store import (
    create_session,
    load_session,
    mutate_session,
)
from breadboard.product.runtime.workflows import (
    WORKFLOW_PROJECTOR_VERSION,
    ReplayableWorkflowController,
    WorkflowDefinition,
    WorkflowStep,
    project_workflow_decision,
)
from breadboard_engine.api.cli_bridge.registry.registry_impl import SessionRegistry

HASH = "sha256:" + "a" * 64


class ProcessAdapter:
    family = "execution-world-process"

    def start(self, activation, spec):
        self.process = process = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
        return ExecutionTarget(
            execution_target_ref=f"pid:{process.pid}",
            pid=process.pid,
            start_token=hashlib_token(process.pid),
            process_group_id=os.getpgid(process.pid),
            volatile_handle=process,
        )

    def observe(self, target):
        if target.get("pid") != self.process.pid:
            return "absent"
        try:
            token = hashlib_token(self.process.pid)
            group = int(
                subprocess.check_output(
                    ["ps", "-p", str(self.process.pid), "-o", "pgid="],
                    text=True,
                ).strip()
            )
        except (subprocess.CalledProcessError, ValueError):
            return "absent"
        return "running" if target.get("start_token") == token and target.get("process_group_id") == group else "absent"

    def cancel(self, target):
        try:
            os.killpg(int(target["process_group_id"]), signal.SIGTERM)
        except ProcessLookupError:
            pass

    def prepare_result(self, target, spec):
        return b"child result"


def hashlib_token(pid: int) -> str:
    try:
        return subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True).strip()
    except subprocess.CalledProcessError:
        return ""


class RetryAdapter:
    family = "retry-adapter"

    def __init__(self) -> None:
        self.starts = 0

    def start(self, activation, spec):
        self.starts += 1
        return ExecutionTarget(activation.execution_target_ref)

    def observe(self, target):
        return "absent" if self.starts == 1 else "running"

    def cancel(self, target):
        return None

    def prepare_result(self, target, spec):
        return None
def _lock() -> EffectiveHarnessLock:
    return EffectiveHarnessLock._from_record({"graph_hash": HASH})


def _running_parent(tmp_path: Path, repository: WorkItemRepository | None = None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = Session.start(_lock(), "parent task", session_id="parent-session")
    create_session(workspace, parent)
    repository = repository or WorkItemRepository(tmp_path / "work-items.jsonl")
    work = WorkItem.create("parent work", work_item_id="parent-work", repository=repository)
    work.acquire_lease("parent-worker", lease_id="parent-lease")
    work.start_attempt("parent-session", lease_id="parent-lease", attempt_id="parent-attempt")
    registry = SessionRegistry(state_root=tmp_path / "registry")
    return workspace, repository, work, registry


def _spec(adapter_family: str, title: str = "child work") -> ChildSpec:
    return ChildSpec(title, "child task", _lock(), "child-worker", adapter_family)


def test_unknown_adapter_is_rejected_before_owner_mutation(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[RetryAdapter()])
    before = parent.events
    with pytest.raises(ChildError, match="not registered"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec("unknown-family"),
        )
    assert parent.events == before
    assert await_records(registry) == []


def test_invalid_adapter_config_leaves_no_task_artifact(
    tmp_path: Path,
) -> None:
    from breadboard.product.runtime.artifacts import list_workspace_artifacts

    class InvalidConfigAdapter(RetryAdapter):
        family = "invalid-config"

        def retained_config(self) -> list[str]:
            return ["not", "durable"]

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = InvalidConfigAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )

    with pytest.raises(ChildError, match="config is not durable"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(adapter.family, "invalid config"),
        )

    assert list_workspace_artifacts(workspace) == []
    assert await_records(registry) == []


def test_non_json_adapter_config_leaves_no_child_record(
    tmp_path: Path,
) -> None:
    from breadboard.product.runtime.artifacts import list_workspace_artifacts

    class InvalidConfigAdapter(RetryAdapter):
        family = "invalid-nested-config"

        def retained_config(self) -> dict[str, object]:
            return {"nested": {"payload": b"not-json"}}

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = InvalidConfigAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )

    with pytest.raises(ChildError, match="config is not durable"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(adapter.family, "invalid nested config"),
        )

    assert list_workspace_artifacts(workspace) == []
    assert await_records(registry) == []
def test_process_adapter_reaps_natural_exit_and_reports_completion(tmp_path: Path) -> None:
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "exit 0"))
    target_ref = "reserved:natural-exit"
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        target_ref,
        adapter.family,
        str(tmp_path),
    )
    target = adapter.start(activation, _spec(adapter.family, "natural exit"))
    deadline = time.monotonic() + 2.0
    while adapter.observe(target.retained()) != "completed" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert adapter.observe(target.retained()) == "completed"
    assert adapter._wait_for_exit(target.retained(), 0.01) is True
    assert target_ref not in adapter._processes
    restarted = ProcessExecutionAdapter(command=("/bin/sh", "-c", "exit 0"))
    restarted.bind_workspace(tmp_path)
    assert restarted.observe(target.retained()) == "completed"


def test_process_wrapper_retains_identity_until_background_descendants_exit(
    tmp_path: Path,
) -> None:
    adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", "sleep 30 & exit 0")
    )
    target_ref = "reserved:background-descendant"
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        target_ref,
        adapter.family,
        str(tmp_path),
    )
    target = adapter.start(
        activation,
        _spec(adapter.family, "background descendant"),
    )
    time.sleep(0.1)

    assert adapter.observe(target.retained()) == "running"
    assert adapter.cancel(target.retained()) is True
    assert adapter.observe(target.retained()) == "completed"

def test_process_adapter_cancels_verified_group_after_leader_loss(
    tmp_path: Path,
) -> None:
    adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", "sleep 30 & exit 0")
    )
    target_ref = "reserved:leader-loss-descendant"
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        target_ref,
        adapter.family,
        str(tmp_path),
    )
    target = adapter.start(
        activation,
        _spec(adapter.family, "leader loss descendant"),
    )
    time.sleep(0.1)
    process = target.volatile_handle
    assert isinstance(process, subprocess.Popen)
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=2)

    restarted = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", "sleep 30 & exit 0")
    )
    restarted.bind_workspace(tmp_path)
    assert restarted.observe(target.retained()) == "running"
    assert restarted.cancel(target.retained()) is True
    assert restarted.observe(target.retained()) == "absent"




def test_process_release_is_committed_before_command_execution(tmp_path: Path) -> None:
    marker = tmp_path / "command-ran"
    phases: list[str] = []

    def publish(target: ExecutionTarget) -> None:
        phase = str(target.metadata.get("launch_phase"))
        phases.append(phase)
        if phase == "release_committed":
            raise RuntimeError("simulated crash before release")

    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", f"touch {marker}"))
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        "reserved:release-order",
        adapter.family,
        str(tmp_path),
        publish_target=publish,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        adapter.start(activation, _spec(adapter.family, "release ordering"))
    assert phases == ["pending", "release_committed"]
    assert marker.exists() is False
    assert adapter._processes == {}

def test_process_release_survives_final_publication_failure(tmp_path: Path) -> None:
    marker = tmp_path / "accepted-command-ran"
    phases: list[str] = []
    targets: list[ExecutionTarget] = []

    def publish(target: ExecutionTarget) -> None:
        phase = str(target.metadata.get("launch_phase"))
        phases.append(phase)
        targets.append(target)
        if phase == "released":
            raise RuntimeError("simulated crash after release")

    adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", f"touch {marker}; sleep 30")
    )
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        "reserved:post-release-publication",
        adapter.family,
        str(tmp_path),
        publish_target=publish,
    )

    with pytest.raises(RuntimeError, match="simulated crash after release"):
        adapter.start(activation, _spec(adapter.family, "post-release publication"))
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert phases == ["pending", "release_committed", "released"]
    assert marker.exists()
    assert activation.execution_target_ref in adapter._processes
    assert adapter.cancel(targets[-1].retained()) is True


def test_process_adapter_releases_durably_committed_wrapper_after_restart(
    tmp_path: Path,
) -> None:
    adapter = ProcessExecutionAdapter(command=("/bin/true",))
    adapter.bind_workspace(tmp_path)
    target_ref = "reserved:release-recovery"
    release_path = adapter._control_path(target_ref, "release")
    marker = tmp_path / "released-after-restart"
    process = subprocess.Popen(
        (
            sys.executable,
            "-c",
            (
                "import pathlib,sys,time\n"
                "release=pathlib.Path(sys.argv[2])\n"
                "while not release.exists(): time.sleep(0.01)\n"
                "pathlib.Path(sys.argv[3]).touch()\n"
            ),
            target_ref,
            str(release_path),
            str(marker),
        ),
        start_new_session=True,
    )
    token, group = adapter._identity(process.pid)
    target = ExecutionTarget(
        target_ref,
        process.pid,
        token,
        group,
        metadata={"launch_phase": "release_committed"},
    )

    try:
        assert release_path.exists() is False
        assert adapter.release_pending(target.retained()) is True
        process.wait(timeout=2)
        assert marker.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_process_adapter_uses_activation_workspace_and_rejects_missing_cwd(tmp_path: Path) -> None:
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "pwd > relative-cwd.txt"))
    workspace = tmp_path / "child-workspace"
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        "reserved:workspace",
        adapter.family,
        str(workspace),
    )
    with pytest.raises(ChildError, match="workspace is unavailable"):
        adapter.start(activation, _spec(adapter.family, "missing cwd"))
    assert adapter._processes == {}
    workspace.mkdir()
    target = adapter.start(activation, _spec(adapter.family, "relative cwd"))
    process = adapter._processes[target.execution_target_ref]
    assert process.wait(timeout=2) == 0
    assert (workspace / "relative-cwd.txt").read_text().strip() == str(workspace)
    assert adapter.observe(target.retained()) == "completed"


def test_process_adapter_delivers_each_delegated_task_on_stdin(
    tmp_path: Path,
) -> None:
    adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", "cat > delegated-task.txt")
    )
    for index, task in enumerate(("first delegated task", "second delegated task")):
        workspace = tmp_path / f"workspace-{index}"
        workspace.mkdir()
        target_ref = f"reserved:delegated-task:{index}"
        activation = ChildActivation(
            "parent-session",
            "parent-session",
            "parent-work",
            f"child-session-{index}",
            f"child-work-{index}",
            f"attempt-{index}",
            f"child://child-session-{index}/attempt/attempt-{index}",
            target_ref,
            adapter.family,
            str(workspace),
        )

        target = adapter.start(
            activation,
            ChildSpec(
                f"child {index}",
                task,
                _lock(),
                "child-worker",
                adapter.family,
            ),
        )
        assert adapter._processes[target_ref].wait(timeout=2) == 0
        assert adapter.observe(target.retained()) == "completed"
        assert (workspace / "delegated-task.txt").read_text() == task

def test_successful_process_child_reconciles_as_completed(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", "cat > reconciled-task.txt")
    )
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family),
    )

    deadline = time.monotonic() + 2.0
    state = factory.reconcile(activation.recovery_ref)
    while not state.terminal_count and time.monotonic() < deadline:
        time.sleep(0.01)
        state = factory.reconcile(activation.recovery_ref)

    assert (state.status, state.terminal_outcome, state.terminal_count) == (
        "completed",
        "completed",
        1,
    )
    assert (workspace / "reconciled-task.txt").read_text() == "child task"
    assert adapter._control_path(activation.execution_target_ref, "task").exists() is False
    assert adapter._control_path(activation.execution_target_ref, "release").exists() is False



def test_process_adapter_hands_off_large_tasks_without_blocking_start(
    tmp_path: Path,
) -> None:
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "exit 0"))
    target_ref = "reserved:large-task"
    activation = ChildActivation(
        "parent-session",
        "parent-session",
        "parent-work",
        "child-session",
        "child-work",
        "attempt",
        "child://child-session/attempt/attempt",
        target_ref,
        adapter.family,
        str(tmp_path),
    )

    target = adapter.start(
        activation,
        ChildSpec(
            "large child task",
            "x" * (2 * 1024 * 1024),
            _lock(),
            "child-worker",
            adapter.family,
        ),
    )
    assert adapter._processes[target_ref].wait(timeout=2) == 0
    assert adapter.observe(target.retained()) == "completed"


def test_process_death_restarts_child_and_rejects_late_result(tmp_path: Path) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    adapter = ProcessAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(parent_session_id="parent-session", root_session_id="parent-session", parent_work_item_id=parent.read_model.work_item_id, spec=_spec(adapter.family))
    assert activation.parent_session_id == "parent-session"


    assert activation.root_session_id == "parent-session"
    assert activation.child_session_id != activation.parent_session_id
    assert activation.parent_work_item_id == "parent-work"
    assert activation.recovery_ref
    retained = await_record(registry, activation.child_session_id).metadata["durable_child"]
    assert "task" not in retained["child_spec"]
    assert retained["child_spec"]["task_artifact_ref"]["digest"].startswith("sha256:")
    os.kill(int(retained["execution_target"]["pid"]), signal.SIGKILL)
    adapter.process.wait(timeout=2)
    assert adapter.observe(retained["execution_target"]) == "absent"

    restarted = DurableChildFactory(workspace, registry=SessionRegistry(state_root=tmp_path / "registry"), repository=WorkItemRepository(tmp_path / "work-items.jsonl"), adapters=[adapter])
    state = restarted.reconcile(activation.recovery_ref)
    assert (state.status, state.terminal_outcome, state.terminal_count) == ("failed", "failed", 1)
    assert (state.child_session_id, state.root_session_id, state.parent_work_item_id, state.recovery_ref) == (activation.child_session_id, activation.root_session_id, "parent-work", activation.recovery_ref)
    child, _ = load_session(workspace, activation.child_session_id)
    assert child.read_model.status == "failed"
    with pytest.raises(LateResultRejected):
        restarted.settle(activation.child_session_id, expected_revision=state.revision, outcome="completed", attempt_id=state.attempt_id)
    assert restarted.reconcile(activation.recovery_ref).terminal_count == 1


def test_start_fences_parent_cancellation_after_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[adapter]
    )
    original_cas = factory._cas

    def cancel_parent_after_delegation(state, **changes):
        next_state = original_cas(state, **changes)
        if changes.get("startup_phase") == "delegated":
            WorkItem.restore(repository, parent.read_model.work_item_id).cancel(
                "operator", "startup race"
            )
            mutate_session(
                workspace,
                "parent-session",
                lambda current: current.cancel("startup race"),
            )
        return next_state

    monkeypatch.setattr(factory, "_cas", cancel_parent_after_delegation)
    with pytest.raises(ChildError, match="parent owner became terminal"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(adapter.family, "startup race"),
        )
    assert adapter.starts == 0
    retained = await_record(
        registry,
        next(
            record.session_id
            for record in asyncio_run(registry.records())
            if record.metadata.get("durable_child")
        ),
    )
    assert ChildState.from_retained(retained.metadata["durable_child"]).terminal_outcome == "canceled"


@pytest.mark.parametrize(
    "field_name",
    (
        "cancellation_requested",
        "launch_claimed",
        "launch_published",
        "result_prepared",
        "joined",
    ),
)
def test_child_state_rejects_non_boolean_retained_flags(field_name: str) -> None:
    retained = ChildState(
        child_session_id="child-session",
        child_work_item_id="child-work",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        attempt_id="attempt",
        recovery_ref="child://child-session/attempt/attempt",
        execution_target_ref="reserved:child-session",
        adapter_family="execution-world-process",
        status="starting",
        revision=0,
    ).retained()
    retained[field_name] = "false"

    with pytest.raises(ValueError, match=field_name):
        ChildState.from_retained(retained)


def test_process_launch_crash_window_is_recovered_or_terminated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])

    def crash_before_identity(_pid: int):
        raise RuntimeError("simulated launch crash")

    monkeypatch.setattr(adapter, "_identity", crash_before_identity)
    with pytest.raises(RuntimeError, match="simulated launch crash"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(adapter.family, "process crash window"),
        )
    retained_record = next(record for record in asyncio_run(registry.records()) if record.metadata.get("durable_child"))
    state = ChildState.from_retained(retained_record.metadata["durable_child"])
    state = factory._cas(state, launch_claim_until=0.0)
    restarted_adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    restarted = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
        adapters=[restarted_adapter],
    )
    recovered = restarted.reconcile(state.recovery_ref)
    assert recovered.launch_published is True
    assert len(restarted_adapter._processes) == 1
    process = restarted_adapter._processes[recovered.execution_target_ref]
    restarted.cancel(recovered.child_session_id, expected_revision=recovered.revision)
    assert process.wait(timeout=2) is not None
    assert restarted_adapter._pending_pid(recovered.execution_target_ref) is None


def test_process_cancel_escalates_sigkill_before_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    ready = tmp_path / "term-ignored"
    adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", f"trap '' TERM; touch {ready}; sleep 30")
    )
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    signals: list[int] = []
    real_killpg = os.killpg

    def record_signal(process_group: int, signum: int) -> None:
        signals.append(signum)
        real_killpg(process_group, signum)

    monkeypatch.setattr(os, "killpg", record_signal)
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "sigterm ignore"),
    )
    deadline = time.monotonic() + 2
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    state = factory._record_state(activation.child_session_id)
    canceled = factory.cancel(activation.child_session_id, expected_revision=state.revision)
    process = adapter._processes[activation.execution_target_ref]
    assert (canceled.status, canceled.terminal_outcome, canceled.terminal_count) == ("canceled", "canceled", 1)
    assert process.poll() is not None
    assert adapter.observe(canceled.execution_target) == "absent"
    assert adapter._pending_pid(activation.execution_target_ref) is None
    assert adapter._control_path(activation.execution_target_ref, "task").exists() is False
    assert adapter._control_path(activation.execution_target_ref, "release").exists() is False
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_process_cancel_leaves_recovery_pending_when_exit_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "unverified cancellation"),
    )
    monkeypatch.setattr(adapter, "_wait_for_exit", lambda target, timeout: False)
    state = factory._record_state(activation.child_session_id)
    requested = factory.cancel(activation.child_session_id, expected_revision=state.revision)
    assert (requested.status, requested.terminal_outcome, requested.terminal_count) == (
        "cancel_requested",
        None,
        0,
    )
    monkeypatch.undo()
    settled = factory.reconcile(activation.recovery_ref)
    assert (settled.status, settled.terminal_outcome, settled.terminal_count) == (
        "canceled",
        "canceled",
        1,
    )


def test_pending_child_cancel_reconciles_after_later_exit_once(tmp_path: Path) -> None:
    class PendingCancelAdapter:
        family = "pending-cancel"

        def __init__(self) -> None:
            self.exited = False
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "absent" if self.exited else "running"

        def cancel(self, target):
            self.cancel_calls += 1
            return self.exited

        def prepare_result(self, target, spec):
            return None

    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    adapter = PendingCancelAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "pending cancellation"),
    )
    state = factory._record_state(activation.child_session_id)
    requested = factory.cancel(activation.child_session_id, expected_revision=state.revision)
    assert requested.cancellation_requested is True
    assert requested.terminal_count == 0
    adapter.exited = True
    settled = factory.reconcile(activation.recovery_ref)
    assert (settled.status, settled.terminal_outcome, settled.terminal_count) == (
        "canceled",
        "canceled",
        1,
    )
    assert factory.reconcile(activation.recovery_ref) == settled
    assert adapter.cancel_calls == 2


def test_cancel_checkpoint_policy_refusal_does_not_signal_adapter(tmp_path: Path) -> None:
    class SignalTrackingAdapter:
        family = "checkpoint-refusal"

        def __init__(self) -> None:
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "running"

        def cancel(self, target):
            self.cancel_calls += 1
            return True

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = SignalTrackingAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    spec = ChildSpec(
        "checkpoint refusal",
        "child task",
        _lock(),
        "child-worker",
        adapter.family,
        cancellation_policy=CancellationPolicy(
            mode="immediate",
            cancellable_by=("operator",),
            propagate_to_children=True,
            cleanup="checkpoint_then_stop",
        ),
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=spec,
    )
    state = factory._record_state(activation.child_session_id)
    with pytest.raises(ValueError, match="checkpoint"):
        factory.cancel(activation.child_session_id, expected_revision=state.revision)
    assert adapter.cancel_calls == 0
    retained = factory._record_state(activation.child_session_id)
    assert retained.cancellation_requested is False
    assert retained.terminal_count == 0

def test_cancel_tree_parent_checkpoint_refusal_precedes_all_mutation(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    class SignalTrackingAdapter(RetryAdapter):
        family = "tree-checkpoint-refusal"

        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls = 0

        def cancel(self, target):
            self.cancel_calls += 1
            return True

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_session = Session.start(
        _lock(), "parent task", session_id="parent-session"
    )
    create_session(workspace, parent_session)
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent_work = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        cancellation_policy=CancellationPolicy(
            mode="immediate",
            cancellable_by=("operator",),
            propagate_to_children=True,
            cleanup="checkpoint_then_stop",
        ),
        repository=repository,
    )
    parent_work.acquire_lease("parent-worker", lease_id="parent-lease")
    parent_work.start_attempt(
        "parent-session",
        lease_id="parent-lease",
        attempt_id="parent-attempt",
    )
    registry = SessionRegistry(state_root=tmp_path / "registry")
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    adapter = SignalTrackingAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        spec=_spec(adapter.family, "checkpoint child"),
    )
    child_before = factory._record_state(child.child_session_id)

    with pytest.raises(ValueError, match="checkpoint"):
        factory.cancel_tree(
            parent_session_id="parent-session",
            parent_work_item_id="parent-work",
        )

    parent_record = await_record(registry, "parent-session")
    child_after = factory._record_state(child.child_session_id)
    assert "durable_parent_cancellation" not in parent_record.metadata
    assert adapter.cancel_calls == 0
    assert child_after == child_before
    assert child_after.cancellation_requested is False
    assert WorkItem.restore(repository, "parent-work").read_model.status == "running"
    restored_parent, _ = load_session(workspace, "parent-session")
    assert restored_parent.read_model.status == "running"


def test_cancel_empty_reason_rejects_before_adapter_signal(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    signals: list[object] = []
    adapter.cancel = lambda target: signals.append(target)
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "empty cancellation reason"),
    )
    state = factory._record_state(activation.child_session_id)
    with pytest.raises(ValueError, match="non-empty"):
        factory.cancel(activation.child_session_id, expected_revision=state.revision, reason="")
    assert signals == []
    retained = factory._record_state(activation.child_session_id)
    assert retained.cancellation_requested is False
    assert retained.revision == state.revision

def test_process_cancel_permission_denied_stays_recovery_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProcessExecutionAdapter()
    target = {
        "ref": "pid:permission-denied",
        "pid": 12345,
        "start_token": "sha256:" + "a" * 64,
        "process_group_id": 12345,
    }
    monkeypatch.setattr(adapter, "observe", lambda _target: "running")
    monkeypatch.setattr(
        adapter,
        "_identity",
        lambda _pid: (target["start_token"], target["process_group_id"]),
    )

    def deny_kill(_group: int, _signum: int) -> None:
        raise PermissionError("permission denied")

    monkeypatch.setattr(os, "killpg", deny_kill)
    assert adapter.cancel(target) is False


def test_process_observe_inspection_failure_stays_recovery_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProcessExecutionAdapter()
    target = {
        "ref": "pid:inspection-failure",
        "pid": 12345,
        "start_token": "sha256:" + "a" * 64,
        "process_group_id": 12345,
    }

    def fail_identity(_pid: int) -> tuple[str, int]:
        raise PermissionError("identity inspection denied")

    def fail_group(_group: int) -> bool | None:
        raise OSError("group inspection unavailable")

    monkeypatch.setattr(adapter, "_identity", fail_identity)
    monkeypatch.setattr(adapter, "_group_alive", fail_group)
    assert adapter.observe(target) == "pending"


def test_process_recover_inspection_failure_stays_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProcessExecutionAdapter()
    target = {
        "ref": "pid:recover-inspection-failure",
        "pid": 12345,
        "start_token": "sha256:" + "a" * 64,
        "process_group_id": 12345,
    }
    def fail_identity(_pid: int) -> tuple[str, int]:
        raise RuntimeError("malformed ps output")

    monkeypatch.setattr(adapter, "_identity", fail_identity)
    assert adapter.recover(target) is None


def test_process_cancel_revalidates_identity_immediately_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProcessExecutionAdapter()
    target = {
        "ref": "pid:reused",
        "pid": 12345,
        "start_token": "kernel:111",
        "process_group_id": 12345,
    }
    monkeypatch.setattr(adapter, "observe", lambda _target: "running")
    monkeypatch.setattr(adapter, "_identity", lambda _pid: ("kernel:222", 12345))
    monkeypatch.setattr(
        os,
        "killpg",
        lambda *_args: pytest.fail("reused process group must not be signaled"),
    )

    assert adapter.cancel(target) is False

def test_process_observe_identity_mismatch_waits_for_retained_group_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProcessExecutionAdapter()
    target = {
        "ref": "pid:identity-mismatch",
        "pid": 12345,
        "start_token": "sha256:" + "a" * 64,
        "process_group_id": 12345,
    }
    monkeypatch.setattr(adapter, "_identity", lambda _pid: ("sha256:" + "b" * 64, 54321))
    monkeypatch.setattr(adapter, "_group_alive", lambda _group: True)
    assert adapter.observe(target) == "pending"
    monkeypatch.setattr(adapter, "_group_alive", lambda _group: False)
    assert adapter.observe(target) == "absent"


def test_process_leader_exit_with_live_group_stays_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExitedProcess:
        def poll(self) -> int:
            return 0

    adapter = ProcessExecutionAdapter()
    target = {
        "ref": "pid:leader-exited",
        "pid": 12345,
        "start_token": "sha256:" + "a" * 64,
        "process_group_id": 12345,
    }
    adapter._processes[target["ref"]] = ExitedProcess()
    monkeypatch.setattr(adapter, "_group_alive", lambda _group: True)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda *_args: pytest.fail("unverified group must not be signaled"),
    )
    assert adapter.observe(target) == "pending"
    assert adapter.cancel(target) is False
def test_process_start_publishes_pending_then_released_target(tmp_path: Path) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "process release phase"),
    )
    state = ChildState.from_retained(await_record(registry, activation.child_session_id).metadata["durable_child"])
    assert state.launch_published is True
    assert state.execution_target["metadata"]["launch_phase"] == "released"
    canceled = factory.cancel(state.child_session_id, expected_revision=state.revision)
    assert (canceled.status, canceled.terminal_outcome, canceled.terminal_count) == ("canceled", "canceled", 1)


def test_unavailable_adapter_absence_settles_without_retry(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    source = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[source])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(source.family, "unavailable child"),
    )
    restarted = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
        adapters=[UnavailableChildAdapter(source.family)],
    )
    recovered = restarted.reconcile(activation.recovery_ref)
    assert (recovered.status, recovered.terminal_outcome, recovered.terminal_count) == (
        "failed",
        "failed",
        1,
    )
    assert restarted.reconcile(activation.recovery_ref) == recovered


def test_unavailable_adapter_cancellation_remains_pending(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    source = RetryAdapter()
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[source]
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(source.family, "unavailable cancellation"),
    )
    restarted = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
        adapters=[UnavailableChildAdapter(source.family)],
    )
    state = restarted._record_state(activation.child_session_id)

    canceled = restarted.cancel(
        activation.child_session_id,
        expected_revision=state.revision,
    )

    assert canceled.cancellation_requested
    assert canceled.terminal_count == 0


def test_terminal_owner_repair_rejects_corrupt_work_journal_before_mutation(
    tmp_path: Path,
) -> None:
    from breadboard.product.coordination.work_items import (
        WorkItemJournalCorruptionError,
    )

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "corrupt owner"),
    )
    state = factory._record_state(activation.child_session_id)
    factory._cas(
        state,
        status="failed",
        terminal_outcome="failed",
        terminal_count=1,
        settlement=None,
    )
    assert repository._path is not None
    with repository._path.open("ab") as stream:
        stream.write(b'{"corrupt":true}\n')

    with pytest.raises(WorkItemJournalCorruptionError):
        factory.reconcile(activation.recovery_ref)
    product, _ = load_session(workspace, activation.child_session_id)
    assert product.read_model.status == "running"
def await_record(registry, session_id):
    return asyncio_run(registry.get(session_id))


def asyncio_run(awaitable):
    import asyncio
    return asyncio.run(awaitable)


def test_two_existing_adapter_families_share_session_settlement_boundary(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    workspace, repository, parent, registry = _running_parent(tmp_path)
    ray_adapter = RayJobAdapter(MultiAgentOrchestrator(TeamConfig("team")))
    process_adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[ray_adapter, process_adapter])
    activations = []
    for index, adapter in enumerate((ray_adapter, process_adapter), 1):
        activation = factory.start(parent_session_id="parent-session", root_session_id="parent-session", parent_work_item_id=parent.read_model.work_item_id, spec=_spec(adapter.family, f"child {index}"))
        activations.append(activation)
        current = factory._record_state(activation.child_session_id)
        placement = WorkItem.restore(repository, activation.child_work_item_id).read_model.placements[-1]
        assert all(attempt.session_ref == activation.child_session_id for attempt in WorkItem.restore(repository, activation.child_work_item_id).read_model.attempts)
        assert placement.execution_target_ref == current.execution_target_ref
        assert current.execution_target["ref"] == current.execution_target_ref
        with pytest.raises(PreparationRequired):
            factory.settle(activation.child_session_id, expected_revision=current.revision, outcome="completed", attempt_id=current.attempt_id)
        prepared = factory.prepare_result(activation.child_session_id, expected_revision=current.revision, result=f"result-{index}".encode(), attempt_id=current.attempt_id)
        with pytest.raises(ExpectedRevisionConflict, match="result refs"):
            factory.settle(activation.child_session_id, expected_revision=prepared.revision, outcome="completed", result_refs=(), attempt_id=current.attempt_id)
        settled = factory.settle(activation.child_session_id, expected_revision=prepared.revision, outcome="completed", result_refs=prepared.result_refs, attempt_id=current.attempt_id)
        assert (settled.status, settled.terminal_outcome, settled.terminal_count, settled.joined, settled.result_prepared) == ("completed", "completed", 1, True, True)
        joined = [event for event in WorkItem.restore(repository, parent.read_model.work_item_id).events if event.kind == "child.joined" and event.payload["child_work_item_id"] == activation.child_work_item_id]
        assert tuple(joined[-1].payload["result_refs"]) == settled.result_refs
        assert factory.settle(activation.child_session_id, expected_revision=0, outcome="completed", result_refs=prepared.result_refs, attempt_id=current.attempt_id) == settled
        with pytest.raises(LateResultRejected):
            factory.prepare_result(activation.child_session_id, expected_revision=prepared.revision, result=b"late", attempt_id=current.attempt_id)
    process_target = factory._record_state(activations[1].child_session_id).execution_target
    process_adapter.cancel(process_target)



def test_parent_cancellation_marker_blocks_late_child_settlement(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "parent marker fence"),
    )
    state = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(
        activation.child_session_id,
        expected_revision=state.revision,
        result=b"late result",
        attempt_id=state.attempt_id,
    )
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    parent_record = await_record(registry, "parent-session")
    asyncio_run(
        registry.update_metadata(
            "parent-session",
            metadata={
                **parent_record.metadata,
                "durable_parent_cancellation": {
                    "work_item_id": parent.read_model.work_item_id,
                    "reason": "parent canceled",
                    "child_recovery_refs": [activation.recovery_ref],
                },
            },
        )
    )
    with pytest.raises(LateResultRejected, match="parent cancellation"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            result_refs=prepared.result_refs,
            attempt_id=prepared.attempt_id,
        )
    retained = factory._record_state(activation.child_session_id)
    assert retained.terminal_count == 0
    assert retained.settlement is None
def test_result_callbacks_require_exact_attempt_identity(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "attempt identity"),
    )
    state = factory._record_state(activation.child_session_id)
    with pytest.raises(ExpectedRevisionConflict, match="attempt identity"):
        factory.prepare_result(activation.child_session_id, expected_revision=state.revision, result=b"missing")
    with pytest.raises(ExpectedRevisionConflict, match="attempt identity"):
        factory.settle(activation.child_session_id, expected_revision=state.revision, outcome="failed")
def test_cancel_tree_honors_nonpropagating_root_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = Session.start(_lock(), "parent task", session_id="parent-session")
    create_session(workspace, parent)
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent_work = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        cancellation_policy=CancellationPolicy(propagate_to_children=False),
        repository=repository,
    )
    parent_work.acquire_lease("parent-worker", lease_id="parent-lease")
    parent_work.start_attempt("parent-session", lease_id="parent-lease", attempt_id="parent-attempt")
    registry = SessionRegistry(state_root=tmp_path / "registry")
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[RetryAdapter()])
    child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        spec=_spec("retry-adapter", "root nonpropagation"),
    )
    factory.cancel_tree(parent_session_id="parent-session", parent_work_item_id="parent-work")
    retained = await_record(registry, child.child_session_id).metadata["durable_child"]
    assert retained["status"] == "running"
    factory.cancel(child.child_session_id, expected_revision=int(retained["revision"]))


def test_cancel_tree_honors_nonpropagating_child_policy(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[RetryAdapter()])
    first = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=ChildSpec(
            "nonpropagating child",
            "child task",
            _lock(),
            "child-worker",
            "retry-adapter",
            cancellation_policy=CancellationPolicy(propagate_to_children=False),
        ),
    )
    second = factory.start(
        parent_session_id=first.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=first.child_work_item_id,
        spec=_spec("retry-adapter", "grandchild"),
    )
    factory.cancel_tree(parent_session_id="parent-session", parent_work_item_id="parent-work")
    retained = await_record(registry, second.child_session_id).metadata["durable_child"]
    assert retained["status"] == "running"
    canceled = factory.cancel(second.child_session_id, expected_revision=int(retained["revision"]))
    assert canceled.terminal_count == 1


def test_direct_nonleaf_cancel_signals_descendants_before_settlement(
    tmp_path: Path,
) -> None:
    class TrackingAdapter(RetryAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.canceled: list[str] = []

        def cancel(self, target):
            self.canceled.append(str(target["ref"]))
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = TrackingAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "nonleaf child"),
    )
    grandchild = factory.start(
        parent_session_id=child.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=child.child_work_item_id,
        spec=_spec(adapter.family, "grandchild"),
    )

    canceled = factory.cancel(
        child.child_session_id,
        expected_revision=factory._record_state(child.child_session_id).revision,
    )

    assert canceled.terminal_outcome == "canceled"
    assert factory._record_state(grandchild.child_session_id).terminal_outcome == "canceled"
    assert adapter.canceled == [
        grandchild.execution_target_ref,
        child.execution_target_ref,
    ]


def test_direct_nonleaf_cancel_waits_for_descendant_settlement(
    tmp_path: Path,
) -> None:
    class PendingDescendantAdapter(RetryAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.descendant_ready = False
            self.blocked_ref: str | None = None
            self.canceled: list[str] = []

        def cancel(self, target):
            target_ref = str(target["ref"])
            self.canceled.append(target_ref)
            if target_ref == self.blocked_ref and not self.descendant_ready:
                return False
            return True

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = PendingDescendantAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "nonleaf pending child"),
    )
    grandchild = factory.start(
        parent_session_id=child.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=child.child_work_item_id,
        spec=_spec(adapter.family, "pending grandchild"),
    )
    adapter.blocked_ref = grandchild.execution_target_ref

    pending = factory.cancel(
        child.child_session_id,
        expected_revision=factory._record_state(child.child_session_id).revision,
    )
    assert pending.terminal_count == 0
    assert pending.cancellation_requested is True
    assert factory._record_state(grandchild.child_session_id).terminal_count == 0
    assert adapter.canceled == [grandchild.execution_target_ref]

    adapter.descendant_ready = True
    settled = factory.reconcile(child.recovery_ref)
    assert settled.terminal_outcome == "canceled"
    assert factory._record_state(grandchild.child_session_id).terminal_outcome == "canceled"
    assert adapter.canceled == [
        grandchild.execution_target_ref,
        grandchild.execution_target_ref,
        child.execution_target_ref,
    ]



def test_child_completion_waits_for_every_delegated_child_settlement(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "nonleaf child"),
    )
    grandchild = factory.start(
        parent_session_id=child.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=child.child_work_item_id,
        spec=_spec(adapter.family, "grandchild"),
    )
    prepared_child = factory.prepare_result(
        child.child_session_id,
        expected_revision=factory._record_state(child.child_session_id).revision,
        attempt_id=child.attempt_id,
    )

    with pytest.raises(ChildError, match="every delegated child"):
        factory.settle(
            child.child_session_id,
            expected_revision=prepared_child.revision,
            outcome="completed",
            attempt_id=child.attempt_id,
        )
    for outcome in ("failed", "canceled"):
        with pytest.raises(ChildError, match="every delegated child"):
            factory.settle(
                child.child_session_id,
                expected_revision=prepared_child.revision,
                outcome=outcome,
                attempt_id=child.attempt_id,
            )

    prepared_grandchild = factory.prepare_result(
        grandchild.child_session_id,
        expected_revision=factory._record_state(
            grandchild.child_session_id
        ).revision,
        attempt_id=grandchild.attempt_id,
    )
    factory.settle(
        grandchild.child_session_id,
        expected_revision=prepared_grandchild.revision,
        outcome="completed",
        attempt_id=grandchild.attempt_id,
    )
    completed = factory.settle(
        child.child_session_id,
        expected_revision=prepared_child.revision,
        outcome="completed",
        attempt_id=child.attempt_id,
    )
    assert completed.terminal_outcome == "completed"
def test_rejected_parent_cancel_leaves_no_replayed_intent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = Session.start(_lock(), "parent task", session_id="parent-session")
    create_session(workspace, parent)
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent_work = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        cancellation_policy=CancellationPolicy(mode="never", cancellable_by=()),
        repository=repository,
    )
    parent_work.acquire_lease("parent-worker", lease_id="parent-lease")
    parent_work.start_attempt("parent-session", lease_id="parent-lease", attempt_id="parent-attempt")
    registry = SessionRegistry(state_root=tmp_path / "registry")
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    asyncio_run(registry.create(SessionRecord("parent-session", status=SessionStatus.RUNNING, metadata={"workspace": str(workspace)})))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[RetryAdapter()])
    with pytest.raises(ChildError, match="not authorized"):
        factory.cancel_tree(parent_session_id="parent-session", parent_work_item_id="parent-work")
    record = await_record(registry, "parent-session")
    assert "durable_parent_cancellation" not in (record.metadata or {})
    restarted = SessionRegistry(state_root=tmp_path / "registry")
    assert "durable_parent_cancellation" not in (await_record(restarted, "parent-session").metadata or {})
    assert parent.read_model.status == "running"
    assert WorkItem.restore(repository, "parent-work").read_model.status == "running"


def test_cancel_tree_reconciles_bridge_status_from_terminal_product(tmp_path: Path) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    workspace, repository, parent, registry = _running_parent(tmp_path)
    mutate_session(workspace, "parent-session", lambda current: current.complete("already done"))
    parent_work = WorkItem.restore(repository, "parent-work")
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[RetryAdapter()]
    )
    factory.cancel_tree(
        parent_session_id="parent-session", parent_work_item_id=parent_work.read_model.work_item_id
    )
    record = await_record(registry, "parent-session")
    assert record.status is SessionStatus.COMPLETED
    assert WorkItem.restore(repository, "parent-work").read_model.status == "completed"

def test_cancel_tree_signals_child_after_parent_work_propagates_cancellation(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    class SignalingAdapter(RetryAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls = 0

        def cancel(self, target):
            self.cancel_calls += 1
            return True

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    adapter = SignalingAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "running child"),
    )
    mutate_session(
        workspace,
        "parent-session",
        lambda current: current.cancel("operator request"),
    )

    settled = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
    )

    assert adapter.cancel_calls == 1
    assert settled[0].child_session_id == activation.child_session_id
    assert settled[0].terminal_outcome == "canceled"
    assert await_record(registry, "parent-session").admission_closed
    restarted = SessionRegistry(state_root=tmp_path / "registry")
    assert await_record(restarted, "parent-session").admission_closed


@pytest.mark.parametrize("outcome", ["completed", "failed", "canceled"])
def test_cancel_tree_adopts_terminal_parent_work_item_into_product(
    tmp_path: Path, outcome: str
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    if outcome == "completed":
        attempt = parent.read_model.current_attempt
        assert attempt is not None
        parent.complete("already complete", attempt_id=attempt.attempt_id)
    elif outcome == "failed":
        parent.fail("work_item", "already failed")
    else:
        parent.cancel("operator", "already canceled")
    DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    ).cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )
    product, _ = load_session(workspace, "parent-session")
    assert product.read_model.status == outcome


@pytest.mark.parametrize("outcome", ["completed", "failed", "canceled"])
def test_cancel_tree_adopts_terminal_child_work_item(
    tmp_path: Path, outcome: str
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[RetryAdapter()]
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", f"terminal {outcome}"),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    if outcome == "completed":
        assert attempt is not None
        child.complete("already complete", attempt_id=attempt.attempt_id)
    elif outcome == "failed":
        child.fail("child_failed", "already failed")
    else:
        child.cancel("operator", "already canceled")

    settled = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )
    assert len(settled) == 1
    adopted = settled[0]
    assert (adopted.status, adopted.terminal_outcome, adopted.terminal_count) == (
        outcome,
        outcome,
        1,
    )
    assert load_session(workspace, activation.child_session_id)[0].read_model.status == outcome
    assert WorkItem.restore(repository, activation.child_work_item_id).read_model.status == outcome


def test_cancel_tree_adopts_each_child_from_its_retained_artifact_store(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    first_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
        artifact_store=ArtifactStore(tmp_path / "first-artifacts"),
    )
    second_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
        artifact_store=ArtifactStore(tmp_path / "second-artifacts"),
    )
    first_factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "first sibling"),
    )
    second = second_factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "second sibling"),
    )
    second_state = second_factory._record_state(second.child_session_id)
    second_state = second_factory.prepare_result(
        second.child_session_id,
        expected_revision=second_state.revision,
        result=b"second-store-result",
        attempt_id=second_state.attempt_id,
    )
    child = WorkItem.restore(repository, second.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    child.complete("completed before child-state CAS", attempt_id=attempt.attempt_id)

    settled = first_factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )

    adopted = next(
        state for state in settled if state.child_session_id == second.child_session_id
    )
    assert adopted.terminal_outcome == "completed"
    assert adopted.result_refs == second_state.result_refs



def test_cancel_tree_prepares_adopted_sibling_result_in_retained_store(
    tmp_path: Path,
) -> None:
    class PreparedRetryAdapter(RetryAdapter):
        def prepare_result(self, target, spec):
            return b"retained-sibling-result"

    workspace, repository, parent, registry = _running_parent(tmp_path)
    first_store = ArtifactStore(tmp_path / "first-artifacts")
    second_store = ArtifactStore(tmp_path / "second-artifacts")
    first_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[PreparedRetryAdapter()],
        artifact_store=first_store,
    )
    second_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[PreparedRetryAdapter()],
        artifact_store=second_store,
    )
    first_factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "first sibling"),
    )
    second = second_factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "second sibling"),
    )
    child = WorkItem.restore(repository, second.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    child.complete("completed before result preparation", attempt_id=attempt.attempt_id)

    settled = first_factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )

    adopted = next(
        state for state in settled if state.child_session_id == second.child_session_id
    )
    assert adopted.terminal_outcome == "completed"
    assert adopted.result_prepared is True
    assert len(adopted.result_refs) == 1
    digest = adopted.result_refs[0].removeprefix("sha256:")
    relative = Path("sha256") / digest[:2] / digest
    assert (second_store._root / relative).read_bytes() == b"retained-sibling-result"
    assert not (first_store._root / relative).exists()


def test_cancel_tree_adopts_completed_child_after_parent_completion(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "completed before parent"),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    child.complete("already complete", attempt_id=attempt.attempt_id)
    mutate_session(
        workspace,
        "parent-session",
        lambda current: current.complete("parent already complete"),
    )

    settled = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )

    assert len(settled) == 1
    assert settled[0].terminal_outcome == "completed"
    assert (
        load_session(workspace, activation.child_session_id)[0].read_model.status
        == "completed"
    )




def test_cancel_tree_skips_authorization_for_terminal_never_child(
    tmp_path: Path,
) -> None:
    class NoSignalAdapter(RetryAdapter):
        family = "terminal-never-child"

        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls = 0

        def cancel(self, target):
            self.cancel_calls += 1
            return True

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = NoSignalAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    spec = ChildSpec(
        "terminal never child",
        "child task",
        _lock(),
        "child-worker",
        adapter.family,
        cancellation_policy=CancellationPolicy(
            mode="never",
            cancellable_by=(),
            propagate_to_children=False,
        ),
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=spec,
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    child.complete("already complete", attempt_id=attempt.attempt_id)
    settled = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )
    assert len(settled) == 1
    assert settled[0].terminal_outcome == "completed"
    assert adapter.cancel_calls == 0


def test_reconcile_signals_canceled_work_item_before_terminal_adoption(
    tmp_path: Path,
) -> None:
    class PendingCancelAdapter:
        family = "reconcile-canceled-work"

        def __init__(self) -> None:
            self.exited = False
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "absent" if self.exited else "running"

        def cancel(self, target):
            self.cancel_calls += 1
            return self.exited

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = PendingCancelAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "pending signal"),
    )
    state = factory._record_state(activation.child_session_id)
    pending = factory.cancel(
        activation.child_session_id,
        expected_revision=state.revision,
    )
    assert pending.terminal_count == 0
    adapter.cancel_calls = 0
    parent.cancel("operator", "recursive parent cancellation")

    still_pending = factory.reconcile(activation.recovery_ref)

    assert still_pending.terminal_count == 0
    assert adapter.cancel_calls == 1
    adapter.exited = True
    settled = factory.reconcile(activation.recovery_ref)
    assert settled.terminal_outcome == "canceled"
    assert settled.terminal_count == 1
    assert adapter.cancel_calls == 2

@pytest.mark.parametrize("operation", ["cancel", "reconcile"])
def test_external_work_item_cancellation_stops_execution_before_adoption(
    tmp_path: Path,
    operation: str,
) -> None:
    class RunningAdapter:
        family = f"external-cancel-{operation}"

        def __init__(self) -> None:
            self.running = True
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "running" if self.running else "absent"

        def cancel(self, target):
            self.cancel_calls += 1
            self.running = False
            return True

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RunningAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "external cancellation"),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    child.cancel("operator", "external cancellation")
    state = factory._record_state(activation.child_session_id)

    settled = (
        factory.cancel(
            activation.child_session_id,
            expected_revision=state.revision,
        )
        if operation == "cancel"
        else factory.reconcile(activation.recovery_ref)
    )

    assert settled.terminal_outcome == "canceled"
    assert adapter.cancel_calls == 1
    assert adapter.running is False


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_reconcile_rejects_terminal_outcome_after_cancellation_intent(
    tmp_path: Path,
    outcome: str,
) -> None:
    class SignalAdapter:
        family = "reconcile-late-terminal"

        def __init__(self) -> None:
            self.exited = False
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "absent" if self.exited else "running"

        def cancel(self, target):
            self.cancel_calls += 1
            return self.exited

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = SignalAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "late terminal"),
    )
    state = factory._record_state(activation.child_session_id)
    pending = factory.cancel(
        activation.child_session_id,
        expected_revision=state.revision,
    )
    assert pending.terminal_count == 0
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    if outcome == "completed":
        child.complete("late completion", attempt_id=attempt.attempt_id)
    else:
        child.fail_attempt(
            "late failure",
            attempt_id=attempt.attempt_id,
            retryable=False,
        )
    adapter.cancel_calls = 0
    adapter.exited = True

    with pytest.raises(
        LateResultRejected,
        match="cannot replace requested cancellation",
    ):
        factory.reconcile(activation.recovery_ref)

    assert adapter.cancel_calls == 1
    assert factory._record_state(activation.child_session_id).terminal_count == 0


def test_cancel_tree_retries_prior_pending_child_signal(tmp_path: Path) -> None:
    class PendingCancelAdapter:
        family = "tree-pending-cancel"

        def __init__(self) -> None:
            self.exited = False
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "absent" if self.exited else "running"

        def cancel(self, target):
            self.cancel_calls += 1
            return self.exited

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = PendingCancelAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "tree pending child"),
    )
    state = factory._record_state(activation.child_session_id)
    requested = factory.cancel(activation.child_session_id, expected_revision=state.revision)
    assert requested.terminal_count == 0
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    first = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )
    assert first[0].terminal_count == 0
    assert await_record(registry, "parent-session").metadata["durable_parent_cancellation"] is not None
    adapter.exited = True
    second = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )
    assert (second[0].status, second[0].terminal_outcome, second[0].terminal_count) == (
        "canceled",
        "canceled",
        1,
    )
    assert await_record(registry, "parent-session").metadata.get("durable_parent_cancellation") is None
    assert adapter.cancel_calls == 3


def test_cancel_tree_settles_canceled_child_when_execution_completed(
    tmp_path: Path,
) -> None:
    class CompletedDuringCancelAdapter:
        family = "tree-completed-during-cancel"

        def __init__(self) -> None:
            self.observed = "running"

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return self.observed

        def cancel(self, target):
            return False

        def prepare_result(self, target, spec):
            raise AssertionError("late result must not replace cancellation")

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = CompletedDuringCancelAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "completed during cancellation"),
    )
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    first = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )
    assert first[0].terminal_count == 0
    assert (
        WorkItem.restore(repository, activation.child_work_item_id).read_model.status
        == "canceled"
    )
    adapter.observed = "completed"

    second = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )

    assert (second[0].status, second[0].terminal_outcome, second[0].terminal_count) == (
        "canceled",
        "canceled",
        1,
    )
    assert (
        await_record(registry, "parent-session").metadata.get(
            "durable_parent_cancellation"
        )
        is None
    )


def test_cancel_adopts_terminal_child_work_item_without_signaling(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[RetryAdapter()]
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "direct terminal"),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    child.fail("child_failed", "already failed")
    state = factory._record_state(activation.child_session_id)
    adopted = factory.cancel(
        activation.child_session_id,
        expected_revision=state.revision,
    )
    assert (adopted.status, adopted.terminal_outcome, adopted.terminal_count) == (
        "failed",
        "failed",
        1,
    )
    assert load_session(workspace, activation.child_session_id)[0].read_model.status == "failed"


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_parent_cancellation_replay_preserves_terminal_product_status(
    tmp_path: Path, outcome: str
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    if outcome == "completed":
        mutate_session(workspace, "parent-session", lambda current: current.complete("done"))
    else:
        mutate_session(
            workspace,
            "parent-session",
            lambda current: current.fail("child", "already failed"),
        )
    first_record = SessionRecord(
        "parent-session",
        status=SessionStatus.RUNNING,
        metadata={
            "workspace": str(workspace),
            "durable_parent_cancellation": {
                "work_item_id": parent.read_model.work_item_id,
                "reason": "restart cancellation",
                "child_recovery_refs": [],
            },
        },
    )
    asyncio_run(registry.create(first_record))

    class Reconciler:
        def __call__(self, recovery_ref: str):
            raise AssertionError(f"unexpected child replay: {recovery_ref}")

        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            raise AssertionError(f"unexpected child cancellation: {recovery_ref}")

        def cancel_tree(self, parent_session_id: str, *, reason: str = "operator request"):
            raise AssertionError(f"unexpected cancellation tree: {parent_session_id}")

    service = SessionService(
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        state_root=tmp_path / "registry",
        durable_child_reconciler=Reconciler(),
        durable_child_repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
    )
    record = asyncio_run(service.ensure_session("parent-session"))
    assert record.status.value == outcome
    assert record.product_session is not None
    assert record.product_session.read_model.status == outcome
    assert "durable_parent_cancellation" not in (record.metadata or {})
    assert WorkItem.restore(repository, parent.read_model.work_item_id).read_model.status == outcome


@pytest.mark.parametrize("outcome", ["completed", "failed", "canceled"])
def test_parent_replay_adopts_terminal_work_item_into_product(
    tmp_path: Path, outcome: str
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    if outcome == "completed":
        attempt = parent.read_model.current_attempt
        assert attempt is not None
        parent.complete("already complete", attempt_id=attempt.attempt_id)
    elif outcome == "failed":
        parent.fail("parent_failed", "already failed")
    else:
        parent.cancel("operator", "already canceled")
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={
                    "workspace": str(workspace),
                    "durable_parent_cancellation": {
                        "work_item_id": parent.read_model.work_item_id,
                        "reason": "restart cancellation",
                        "child_recovery_refs": [],
                    },
                },
            )
        )
    )

    class Reconciler:
        def __call__(self, recovery_ref: str):
            raise AssertionError(f"unexpected child replay: {recovery_ref}")

        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            raise AssertionError(f"unexpected child cancellation: {recovery_ref}")

        def cancel_tree(self, parent_session_id: str, *, reason: str = "operator request"):
            raise AssertionError(f"unexpected cancellation tree: {parent_session_id}")

    service = SessionService(
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        state_root=tmp_path / "registry",
        durable_child_reconciler=Reconciler(),
        durable_child_repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
    )
    record = asyncio_run(service.ensure_session("parent-session"))
    assert record.status.value == ("stopped" if outcome == "canceled" else outcome)
    assert record.product_session is not None
    assert record.product_session.read_model.status == outcome
    assert "durable_parent_cancellation" not in (record.metadata or {})


def test_parent_cancellation_replay_terminalizes_retained_turn_admission(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry import (
        SessionRecord,
        TurnRecord,
        submission_body_digest,
    )
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    record = SessionRecord(
        "parent-session",
        status=SessionStatus.RUNNING,
        metadata={
            "workspace": str(workspace),
            "durable_parent_cancellation": {
                "work_item_id": parent.read_model.work_item_id,
                "reason": "restart cancellation",
                "child_recovery_refs": [],
            },
        },
    )
    for index, state in enumerate(("active", "queued"), start=1):
        content = f"retained-{state}"
        turn = TurnRecord(
            input_id=f"input-{index}",
            turn_id=f"turn-{index}",
            client_message_id=f"client-{index}",
            content=content,
            attachments=(),
            original_disposition="started" if state == "active" else "queued",
            state=state,
            body_digest=submission_body_digest(content, ()),
        )
        record.turns_by_id[turn.turn_id] = turn
        if state == "active":
            record.active_turn_id = turn.turn_id
        else:
            record.queued_turn_ids.append(turn.turn_id)
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    asyncio_run(registry.create(record))

    class Reconciler:
        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            raise AssertionError(f"unexpected child cancellation: {recovery_ref}")
        def __call__(self, recovery_ref: str):
            raise AssertionError(f"unexpected child replay: {recovery_ref}")

        def cancel_tree(
            self,
            parent_session_id: str,
            *,
            reason: str = "operator request",
        ):
            raise AssertionError(
                f"unexpected cancellation tree: {parent_session_id}"
            )

    service = SessionService(
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        state_root=tmp_path / "registry",
        durable_child_reconciler=Reconciler(),
        durable_child_repository=repository,
    )

    recovered = asyncio_run(service.ensure_session("parent-session"))
    persisted = await_record(
        SessionRegistry(state_root=tmp_path / "registry"), "parent-session"
    )

    assert recovered.status is SessionStatus.STOPPED
    assert recovered.admission_closed is True
    assert recovered.active_turn_id is None
    assert not recovered.queued_turn_ids
    assert recovered.turn_admission is recovered.turn_admission.__class__.IDLE
    assert all(
        turn.terminal_outcome == "cancelled"
        and turn.terminal_resolution_committed is True
        for turn in recovered.turns_by_id.values()
    )
    assert persisted.admission_closed is True
    assert persisted.active_turn_id is None
    assert not persisted.queued_turn_ids
    assert all(
        turn.terminal_outcome == "cancelled"
        and turn.terminal_resolution_committed is True
        for turn in persisted.turns_by_id.values()
    )


def test_cancellation_intent_precedes_signal_and_late_completion_loses(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ProcessAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(parent_session_id="parent-session", root_session_id="parent-session", parent_work_item_id=parent.read_model.work_item_id, spec=_spec(adapter.family, "cancel child"))
    current = factory._record_state(activation.child_session_id)
    canceled = factory.cancel(activation.child_session_id, expected_revision=current.revision, reason="operator stop")
    adapter.process.wait(timeout=2)
    retained = await_record(registry, activation.child_session_id).metadata["durable_child"]
    assert (canceled.status, canceled.terminal_outcome, canceled.terminal_count, canceled.joined) == ("canceled", "canceled", 1, True)
    assert retained["cancellation_requested"] is True
    child, _ = load_session(workspace, activation.child_session_id)
    assert child.read_model.status == "canceled"
    with pytest.raises(LateResultRejected):
        factory.settle(activation.child_session_id, expected_revision=canceled.revision, outcome="completed", attempt_id=canceled.attempt_id)



def test_cancel_cannot_overwrite_inflight_settlement_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "settlement race"),
    )
    current = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(activation.child_session_id, expected_revision=current.revision, result=b"done", attempt_id=current.attempt_id)
    original_settle = factory._settle

    def pause_settlement(*args, **kwargs):
        raise RuntimeError("settlement owner paused")

    monkeypatch.setattr(factory, "_settle", pause_settlement)
    with pytest.raises(RuntimeError, match="settlement owner"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            result_refs=prepared.result_refs,
            attempt_id=current.attempt_id,
        )
    monkeypatch.setattr(factory, "_settle", original_settle)
    reserved = factory._record_state(activation.child_session_id)
    assert reserved.settlement is not None
    with pytest.raises(ExpectedRevisionConflict, match="settlement is already reserved"):
        factory.cancel(activation.child_session_id, expected_revision=reserved.revision)
    repaired = factory.reconcile(activation.recovery_ref)
    assert (repaired.status, repaired.terminal_outcome, repaired.terminal_count) == ("completed", "completed", 1)

def test_reconcile_resumes_reserved_settlement_after_work_item_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "resume reserved settlement"),
    )
    current = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(
        activation.child_session_id,
        expected_revision=current.revision,
        result=b"done",
        attempt_id=current.attempt_id,
    )
    original_join_child = WorkItem.join_child

    def fail_join(*_args, **_kwargs):
        raise RuntimeError("simulated settlement crash")

    monkeypatch.setattr(WorkItem, "join_child", fail_join)
    with pytest.raises(RuntimeError, match="simulated settlement crash"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            result_refs=prepared.result_refs,
            attempt_id=current.attempt_id,
        )
    monkeypatch.setattr(WorkItem, "join_child", original_join_child)

    reserved = factory._record_state(activation.child_session_id)
    assert reserved.settlement is not None
    assert WorkItem.restore(
        repository,
        activation.child_work_item_id,
    ).read_model.status == "completed"
    repaired = factory.reconcile(activation.recovery_ref)
    assert (
        repaired.status,
        repaired.terminal_outcome,
        repaired.terminal_count,
        repaired.settlement,
        repaired.joined,
    ) == ("completed", "completed", 1, None, True)




def test_work_item_journal_truncates_torn_last_frame(tmp_path: Path) -> None:
    path = tmp_path / "work-items.jsonl"
    repository = WorkItemRepository(path)
    item = WorkItem.create("durable item", work_item_id="durable-item", repository=repository)
    prefix = path.read_bytes()
    path.write_bytes(prefix + b'{"checksum":"sha256:broken","payload":')
    recovered = WorkItemRepository(path)
    assert len(recovered.read(item.read_model.work_item_id)) == 1
    assert path.read_bytes() == prefix
def test_work_item_delegate_is_one_recoverable_transaction(tmp_path: Path) -> None:
    path = tmp_path / "work-items.jsonl"
    repository = WorkItemRepository(path)
    parent = WorkItem.create("parent", work_item_id="parent", repository=repository)
    parent.acquire_lease("worker", lease_id="lease")
    parent.start_attempt("parent-session", lease_id="lease", attempt_id="attempt")

    parent.delegate("child", attempt_id="attempt", child_work_item_id="child")
    frames = path.read_text(encoding="utf-8").splitlines()
    assert len(frames) == 4
    transaction = json.loads(frames[-1])
    assert transaction["payload"]["schema_version"] == "bb.work_item.transaction.v1"
    assert [event["work_item_id"] for event in transaction["payload"]["events"]] == ["child", "parent"]
    restored = WorkItemRepository(path)
    assert len(restored.read("child")) == 1
    assert len(restored.read("parent")) == 4

def test_work_item_delegate_torn_transaction_discards_both_events(tmp_path: Path) -> None:
    path = tmp_path / "work-items.jsonl"
    repository = WorkItemRepository(path)
    parent = WorkItem.create("parent", work_item_id="parent", repository=repository)
    parent.acquire_lease("worker", lease_id="lease")
    parent.start_attempt("parent-session", lease_id="lease", attempt_id="attempt")
    prefix = path.read_bytes()
    parent.delegate("child", attempt_id="attempt", child_work_item_id="child")
    transaction = path.read_bytes()[len(prefix):]
    path.write_bytes(prefix + transaction[: len(transaction) // 2])

    restored = WorkItemRepository(path)
    assert len(restored.read("parent")) == 3
    assert restored.read("child") == ()
    assert path.read_bytes() == prefix



def test_job_completion_replay_preserves_inline_result_payload() -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("job-replay-inline"))
    spawned = orchestrator.spawn_subagent(owner_agent="parent-session", agent_id="child-session", async_mode=True)
    result_payload = {
        "error": None,
        "output": "inline",
        "subagent_type": "worker",
        "verdict_code": "approved",
        "agent_id": "result-agent",
        "job_id": "result-job",
        "seq": 99,
        "state": "result-state",
    }
    orchestrator.mark_job_completed(
        spawned.job.job_id,
        result_payload=result_payload,
    )

    rebuilt = MultiAgentOrchestrator(TeamConfig("job-replay-inline"), event_log=orchestrator.event_log)
    restored = rebuilt.job_manager.get(spawned.job.job_id)
    assert restored is not None
    assert restored.result_payload == result_payload



def test_released_absent_target_honors_retry_policy_with_new_attempt_and_recovery_ref(
    tmp_path: Path,
) -> None:
    class ReleasedRetryAdapter(RetryAdapter):
        released_absence_is_terminal = True

        def start(self, activation, spec):
            target = super().start(activation, spec)
            return ExecutionTarget(
                target.execution_target_ref,
                metadata={"launch_phase": "released"},
            )

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ReleasedRetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    spec = ChildSpec("retry child", "retry task", _lock(), "child-worker", adapter.family, retry_policy=RetryPolicy(2, True))
    activation = factory.start(parent_session_id="parent-session", root_session_id="parent-session", parent_work_item_id=parent.read_model.work_item_id, spec=spec)
    state = factory.reconcile(activation.recovery_ref)
    assert state.status == "running"
    assert state.attempt_id != activation.attempt_id
    assert state.recovery_ref == activation.recovery_ref
    with pytest.raises(ExpectedRevisionConflict, match="stale child attempt"):
        factory.prepare_result(activation.child_session_id, expected_revision=state.revision, result=b"stale", attempt_id=activation.attempt_id)
    child_work = WorkItem.restore(repository, activation.child_work_item_id)
    assert [(attempt.number, attempt.status) for attempt in child_work.read_model.attempts] == [(1, "failed"), (2, "running")]
    assert factory.reconcile(state.recovery_ref) == state

def test_parent_retry_cancels_propagating_descendants_without_closing_admission(
    tmp_path: Path,
) -> None:
    class ParentFailureAdapter(RetryAdapter):
        family = "parent-failure-retry"

        def __init__(self) -> None:
            super().__init__()
            self.failed_ref: str | None = None
            self.canceled: list[str] = []

        def observe(self, target):
            return (
                "absent"
                if target.get("ref") == self.failed_ref
                else "running"
            )

        def cancel(self, target):
            self.canceled.append(str(target["ref"]))

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ParentFailureAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=ChildSpec(
            "retrying parent child",
            "retry parent",
            _lock(),
            "child-worker",
            adapter.family,
            retry_policy=RetryPolicy(2, True),
        ),
    )
    grandchild = factory.start(
        parent_session_id=child.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=child.child_work_item_id,
        spec=_spec(adapter.family, "prior-attempt grandchild"),
    )
    adapter.failed_ref = child.execution_target_ref

    retried = factory.reconcile(child.recovery_ref)

    assert retried.status == "running"
    assert retried.attempt_id != child.attempt_id
    assert factory._record_state(grandchild.child_session_id).terminal_outcome == (
        "canceled"
    )
    assert adapter.canceled == [grandchild.execution_target_ref]
    assert not await_record(registry, child.child_session_id).admission_closed
    attempts = WorkItem.restore(
        repository,
        child.child_work_item_id,
    ).read_model.attempts
    assert [(attempt.number, attempt.status) for attempt in attempts] == [
        (1, "failed"),
        (2, "running"),
    ]



@pytest.mark.parametrize("transition", ["wait", "pause"])
def test_absent_target_does_not_launch_nonlaunchable_work_item(
    tmp_path: Path, transition: str
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[adapter]
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=ChildSpec(
            "waiting child",
            "waiting task",
            _lock(),
            "child-worker",
            adapter.family,
            retry_policy=RetryPolicy(2, True),
        ),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    if transition == "wait":
        child.wait(("resume-token",), "waiting for resume", attempt_id=attempt.attempt_id)
    else:
        child.pause("paused by owner", attempt_id=attempt.attempt_id)
    recovered = factory.reconcile(activation.recovery_ref)
    assert recovered.status == "running"
    assert adapter.starts == 1
    assert WorkItem.restore(repository, activation.child_work_item_id).read_model.status == (
        "waiting" if transition == "wait" else "paused"
    )

def test_retry_reconciles_after_work_item_failure_commit(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=ChildSpec("retry crash", "retry crash task", _lock(), "child-worker", adapter.family, retry_policy=RetryPolicy(2, True)),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    child.fail_attempt("execution target exited", attempt_id=activation.attempt_id, retryable=True)

    recovered = factory.reconcile(activation.recovery_ref)
    assert recovered.status == "running"
    assert recovered.attempt_id != activation.attempt_id


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_reconcile_cancellation_rejects_late_terminal_work_item(
    tmp_path: Path, outcome: str
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[adapter]
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, f"persisted cancellation {outcome}"),
    )
    state = factory._record_state(activation.child_session_id)
    state = factory._cas(
        state,
        status="cancel_requested",
        cancellation_requested=True,
        cancellation_reason="persisted stop",
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    if outcome == "completed":
        child.complete("completed before replayed cancel", attempt_id=attempt.attempt_id)
    else:
        child.fail_attempt(
            "failed before replayed cancel",
            attempt_id=attempt.attempt_id,
            retryable=False,
        )

    with pytest.raises(
        LateResultRejected,
        match="cannot replace requested cancellation",
    ):
        factory.reconcile(activation.recovery_ref)
    retained = factory._record_state(activation.child_session_id)
    assert retained.status == "cancel_requested"
    assert retained.terminal_count == 0


def test_interrupted_startup_aborts_retained_record_without_work_item(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    spec = _spec(adapter.family, "interrupted child")
    initial = ChildState(
        "child-startup",
        "work-startup",
        "parent-session",
        "parent-session",
        parent.read_model.work_item_id,
        "attempt-startup",
        "child://child-startup/attempt/attempt-startup",
        "reserved:child-startup",
        adapter.family,
        "starting",
        0,
        startup_phase="recorded",
        child_spec={**spec.retained(), "task_artifact_ref": factory.artifacts.put(spec.task.encode(), media_type="text/plain; charset=utf-8").as_dict(), "task_artifact_store": str(factory.artifacts._root)},
        execution_target={"ref": "reserved:child-startup"},
    )
    factory._create_record(initial)
    recovered = factory.reconcile(initial.recovery_ref)
    assert (recovered.status, recovered.terminal_outcome, recovered.terminal_count) == ("failed", "failed", 1)
    assert await_record(registry, initial.child_session_id).status.value == "failed"
    child, _ = load_session(workspace, initial.child_session_id)
    assert child.read_model.status == "failed"
    assert child.task is None


@pytest.mark.parametrize("outcome", ["failed", "canceled"])
def test_reconcile_starting_child_adopts_terminal_parent_owner(
    tmp_path: Path, outcome: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_product = Session.start(_lock(), "parent task", session_id="parent-session")
    create_session(workspace, parent_product)
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        cancellation_policy=CancellationPolicy(propagate_to_children=False),
        repository=repository,
    )
    parent.acquire_lease("parent-worker", lease_id="parent-lease")
    parent.start_attempt(
        "parent-session",
        lease_id="parent-lease",
        attempt_id="parent-attempt",
    )
    registry = SessionRegistry(state_root=tmp_path / "registry")
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[adapter]
    )
    spec = _spec(adapter.family, f"terminal parent {outcome}")
    child_session_id = "child-terminal-parent"
    child_work_item_id = "work-terminal-parent"
    attempt_id = "attempt-terminal-parent"
    initial = ChildState(
        child_session_id,
        child_work_item_id,
        "parent-session",
        "parent-session",
        parent.read_model.work_item_id,
        attempt_id,
        f"child://{child_session_id}/attempt/{attempt_id}",
        f"reserved:{child_session_id}",
        adapter.family,
        "starting",
        0,
        startup_phase="delegated",
        child_spec={
            **spec.retained(),
            "task_artifact_ref": factory.artifacts.put(
                spec.task.encode(), media_type="text/plain; charset=utf-8"
            ).as_dict(),
            "task_artifact_store": str(factory.artifacts._root),
        },
        execution_target={"ref": f"reserved:{child_session_id}"},
    )
    factory._create_record(initial)
    child_product = Session.start(
        spec.lock,
        spec.task,
        session_id=child_session_id,
    )
    create_session(workspace, child_product)
    parent.delegate(
        spec.title,
        attempt_id="parent-attempt",
        child_work_item_id=child_work_item_id,
        cancellation_policy=spec.cancellation_policy,
    )
    if outcome == "failed":
        parent.fail("parent_failed", "terminal before child recovery")
        mutate_session(
            workspace,
            "parent-session",
            lambda current: current.fail("parent_failed", "terminal before child recovery"),
        )
    else:
        parent.cancel("operator", "terminal before child recovery")
        mutate_session(
            workspace,
            "parent-session",
            lambda current: current.cancel("terminal before child recovery"),
        )
    recovered = factory.reconcile(initial.recovery_ref)
    assert recovered.terminal_outcome == "canceled"
    assert adapter.starts == 0
    assert WorkItem.restore(repository, child_work_item_id).read_model.status == "canceled"
def test_starting_child_without_work_item_can_persist_cancellation(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    spec = _spec(adapter.family, "predelegation cancel")
    initial = ChildState(
        "child-cancel-start",
        "work-cancel-start",
        "parent-session",
        "parent-session",
        parent.read_model.work_item_id,
        "attempt-cancel-start",
        "child://child-cancel-start/attempt/attempt-cancel-start",
        "reserved:child-cancel-start",
        adapter.family,
        "starting",
        0,
        child_spec={**spec.retained(), "task_artifact_ref": factory.artifacts.put(spec.task.encode(), media_type="text/plain; charset=utf-8").as_dict(), "task_artifact_store": str(factory.artifacts._root)},
        execution_target={"ref": "reserved:child-cancel-start"},
    )
    factory._create_record(initial)
    canceled = factory.cancel(initial.child_session_id, expected_revision=0, reason="caller requested")
    assert (canceled.status, canceled.terminal_outcome, canceled.cancellation_reason) == ("canceled", "canceled", "caller requested")
    assert await_record(registry, initial.child_session_id).status.value == "stopped"
def test_process_starting_child_without_work_item_cancels_without_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ProcessExecutionAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    spec = _spec(adapter.family, "process predelegation cancel")
    initial = ChildState(
        "child-process-cancel-start",
        "work-process-cancel-start",
        "parent-session",
        "parent-session",
        parent.read_model.work_item_id,
        "attempt-process-cancel-start",
        "child://child-process-cancel-start/attempt/attempt-process-cancel-start",
        "reserved:child-process-cancel-start",
        adapter.family,
        "starting",
        0,
        child_spec={
            **spec.retained(),
            "task_artifact_ref": factory.artifacts.put(
                spec.task.encode(), media_type="text/plain; charset=utf-8"
            ).as_dict(),
            "task_artifact_store": str(factory.artifacts._root),
        },
        execution_target={"ref": "reserved:child-process-cancel-start"},
    )
    factory._create_record(initial)
    monkeypatch.setattr(
        adapter,
        "cancel",
        lambda _target: pytest.fail("reserved process target must not be signaled"),
    )

    canceled = factory.cancel(
        initial.child_session_id,
        expected_revision=0,
        reason="caller requested",
    )

    assert (canceled.status, canceled.terminal_outcome) == ("canceled", "canceled")


def test_reconcile_process_cancellation_without_work_item_cancels_without_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ProcessExecutionAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    spec = _spec(adapter.family, "retained process predelegation cancel")
    initial = ChildState(
        "child-process-reconcile-cancel",
        "work-process-reconcile-cancel",
        "parent-session",
        "parent-session",
        parent.read_model.work_item_id,
        "attempt-process-reconcile-cancel",
        "child://child-process-reconcile-cancel/attempt/attempt-process-reconcile-cancel",
        "reserved:child-process-reconcile-cancel",
        adapter.family,
        "cancel_requested",
        0,
        cancellation_requested=True,
        cancellation_reason="retained request",
        child_spec={
            **spec.retained(),
            "task_artifact_ref": factory.artifacts.put(
                spec.task.encode(), media_type="text/plain; charset=utf-8"
            ).as_dict(),
            "task_artifact_store": str(factory.artifacts._root),
        },
        execution_target={"ref": "reserved:child-process-reconcile-cancel"},
    )
    factory._create_record(initial)
    monkeypatch.setattr(
        adapter,
        "cancel",
        lambda _target: pytest.fail("reserved process target must not be signaled"),
    )

    canceled = factory.reconcile(initial.recovery_ref)

    assert (canceled.status, canceled.terminal_outcome) == ("canceled", "canceled")

def test_cancel_tree_terminalizes_bridge_for_child_without_work_item(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    spec = _spec(adapter.family, "predelegation tree cancel")
    initial = ChildState(
        "child-tree-cancel-start",
        "work-tree-cancel-start",
        "parent-session",
        "parent-session",
        parent.read_model.work_item_id,
        "attempt-tree-cancel-start",
        "child://child-tree-cancel-start/attempt/attempt-tree-cancel-start",
        "reserved:child-tree-cancel-start",
        adapter.family,
        "starting",
        0,
        child_spec={
            **spec.retained(),
            "task_artifact_ref": factory.artifacts.put(
                spec.task.encode(),
                media_type="text/plain; charset=utf-8",
            ).as_dict(),
            "task_artifact_store": str(factory.artifacts._root),
            "work_item_repository_path": str(factory._repository_path),
        },
        execution_target={"ref": "reserved:child-tree-cancel-start"},
    )
    factory._create_record(initial)

    settled = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
    )

    assert settled[0].terminal_outcome == "canceled"
    assert await_record(registry, initial.child_session_id).status.value == "stopped"


def test_launch_claimed_before_adapter_start_relaunches_same_attempt(tmp_path: Path) -> None:
    class CrashOnceAdapter:
        family = "crash-once"

        def __init__(self) -> None:
            self.starts = 0

        def start(self, activation, spec):
            self.starts += 1
            if self.starts == 1:
                raise RuntimeError("adapter start interrupted")
            return ExecutionTarget("live-after-restart")


        def observe(self, target):
            return "running" if self.starts >= 2 else "absent"

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = CrashOnceAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    with pytest.raises(RuntimeError, match="adapter start interrupted"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(adapter.family, "same attempt"),
        )
    retained = await_record(registry, next(record.session_id for record in asyncio_run(registry.records()) if record.metadata.get("durable_child")))
    state = ChildState.from_retained(retained.metadata["durable_child"])
    recovered = factory.reconcile(state.recovery_ref)
    assert (adapter.starts, recovered.status, recovered.attempt_id) == (2, "running", state.attempt_id)


def test_reserved_launch_claim_does_not_relaunch_on_pending_observation(tmp_path: Path) -> None:
    class PendingRecoveryAdapter:
        family = "pending-recovery"

        def __init__(self) -> None:
            self.starts = 0

        def start(self, activation, spec):
            self.starts += 1
            return ExecutionTarget(activation.execution_target_ref)

        def recover(self, target):
            return None

        def observe(self, target):
            return "pending"

        def cancel(self, target):
            return None

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = PendingRecoveryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "pending launch recovery"),
    )
    state = factory._record_state(activation.child_session_id)
    state = factory._cas(
        state,
        launch_claimed=True,
        launch_claim_owner=None,
        launch_claim_until=0.0,
        launch_published=False,
    )
    recovered = factory.reconcile(activation.recovery_ref)
    assert recovered.terminal_count == 0
    assert recovered.launch_published is False
    assert adapter.starts == 1


def test_pending_release_observation_never_cancels_and_relaunches(tmp_path: Path) -> None:
    class ReleaseRaceAdapter:
        family = "release-race"

        def __init__(self) -> None:
            self.starts = 0
            self.cancels = 0

        def start(self, activation, spec):
            self.starts += 1
            return ExecutionTarget(activation.execution_target_ref, metadata={"launch_phase": "released"})

        def observe(self, target):
            return "running"

        def release_pending(self, target):
            return target.get("metadata", {}).get("launch_phase") == "pending"

        def recover(self, target):
            raise AssertionError("pending wrapper must remain under observation")

        def cancel(self, target):
            self.cancels += 1
            return True

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ReleaseRaceAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "release race"),
    )
    state = factory._record_state(activation.child_session_id)
    target = dict(state.execution_target)
    target["metadata"] = {"launch_phase": "pending"}
    state = factory._cas(state, execution_target=target)
    recovered = factory.reconcile(activation.recovery_ref)
    assert recovered == state
    assert adapter.starts == 1
    assert adapter.cancels == 0


def test_reconciler_commits_pending_process_release_before_execution(
    tmp_path: Path,
) -> None:
    class DurableReleaseAdapter:
        family = "durable-release"

        def __init__(self) -> None:
            self.release_phases: list[str] = []

        def start(self, activation, spec):
            return ExecutionTarget(
                activation.execution_target_ref,
                metadata={"launch_phase": "released"},
            )

        def observe(self, target):
            return "running"

        def release_committed(self, target):
            self.release_phases.append(target["metadata"]["launch_phase"])
            return True

        def recover(self, target):
            raise AssertionError("committed wrapper must be released before recovery")

        def cancel(self, target):
            return True

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = DurableReleaseAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "durable release"),
    )
    state = factory._record_state(activation.child_session_id)
    target = dict(state.execution_target)
    target["metadata"] = {"launch_phase": "pending"}
    state = factory._cas(state, execution_target=target)

    recovered = factory.reconcile(activation.recovery_ref)

    assert recovered.execution_target["metadata"]["launch_phase"] == "released"
    assert recovered.revision == state.revision + 2
    assert adapter.release_phases == ["release_committed"]


def test_reserved_empty_target_published_once(tmp_path: Path) -> None:
    class ReservedAdapter:
        family = "reserved-empty"

        def __init__(self) -> None:
            self.starts = 0

        def start(self, activation, spec):
            self.starts += 1
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "running"

        def cancel(self, target):
            return None

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ReservedAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "reserved empty target"),
    )
    assert factory._record_state(activation.child_session_id).launch_published is True
    factory.reconcile(activation.recovery_ref)
    factory.reconcile(activation.recovery_ref)
    assert adapter.starts == 1



def test_terminal_metadata_repairs_bridge_status_after_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "terminal repair"),
    )
    current = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(activation.child_session_id, expected_revision=current.revision, result=b"done", attempt_id=current.attempt_id)
    original_update_status = registry.update_status

    async def crash_before_status(session_id, status):
        raise RuntimeError("simulated crash before bridge status update")

    monkeypatch.setattr(registry, "update_status", crash_before_status)
    with pytest.raises(RuntimeError, match="bridge status"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            result_refs=prepared.result_refs,
            attempt_id=current.attempt_id,
        )
    monkeypatch.setattr(registry, "update_status", original_update_status)
    restarted = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=repository,
        adapters=[adapter],
    )
    repaired = restarted.reconcile(activation.recovery_ref)
    assert repaired.terminal_count == 1
    assert await_record(restarted.registry, activation.child_session_id).status.value == "completed"


def test_ray_dead_cached_actor_is_evicted_and_terminalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ray
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class DeadActor:
        def get_invocation_state(self, _invocation_id: str) -> str:
            return "running"

        def get_state(self) -> str:
            raise RuntimeError("actor is dead")

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-dead-cache"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-dead-cache-job"
    adapter.bind_workspace(tmp_path)
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    actor_key = adapter._actor_key(provider_job_id, tmp_path)
    adapter._actors[actor_key] = DeadActor()
    monkeypatch.setattr(
        ray,
        "get_actor",
        lambda _name, **_kwargs: (_ for _ in ()).throw(ValueError("actor not found")),
    )
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }

    assert adapter.observe(target) == "absent"
    assert actor_key not in adapter._actors
    assert (
        orchestrator.job_manager.get(
            adapter._manager_job_id(provider_job_id, tmp_path)
        ).state
        == "failed"
    )
def test_process_identity_is_retained_before_restart_without_private_journal(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "launch recovery"),
    )
    process = next(iter(adapter._processes.values()))
    try:
        state = factory._record_state(activation.child_session_id)
        assert state.launch_published is True
        assert isinstance(state.execution_target.get("pid"), int)
        restarted_adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
        restarted = DurableChildFactory(
            workspace,
            registry=SessionRegistry(state_root=tmp_path / "registry"),
            repository=repository,
            adapters=[restarted_adapter],
        )
        recovered = restarted.reconcile(activation.recovery_ref)
        assert recovered.status == "running"
        assert recovered.execution_target["pid"] == process.pid
        restarted_adapter.cancel(recovered.execution_target)
    finally:
        process.wait(timeout=2)



def await_records(registry):
    return asyncio_run(registry.records())

def test_service_restart_routes_retained_child_to_reconciler_not_session_runner(tmp_path: Path) -> None:
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "service restart"),
    )
    seen: list[str] = []

    async def callback_only(recovery_ref: str) -> None:
        seen.append(recovery_ref)

    restarted_registry = SessionRegistry(state_root=tmp_path / "registry")
    with pytest.raises(TypeError, match="cancel_tree"):
        SessionService(
            registry=restarted_registry,
            state_root=tmp_path / "registry",
            durable_child_reconciler=callback_only,
        )

    class Reconciler:
        async def __call__(self, recovery_ref: str) -> None:
            seen.append(recovery_ref)

        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            raise AssertionError("cancel is not part of startup callback test")

        def cancel_tree(self, parent_session_id: str, *, reason: str = "operator request"):
            raise AssertionError("cancel_tree is not part of startup callback test")

    service = SessionService(
        registry=restarted_registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=Reconciler(),
    )
    record = asyncio_run(service.ensure_session(activation.child_session_id))
    assert seen == [activation.recovery_ref]
    assert record.product_session is not None
    assert record.runner is None
    again = asyncio_run(service.ensure_session(activation.child_session_id))
    assert seen == [activation.recovery_ref, activation.recovery_ref]
    assert again.loaded_from_retained_state is True


def test_service_retries_retained_pending_cancel_until_verified_exit(tmp_path: Path) -> None:
    from breadboard_engine.api.cli_bridge.service import SessionService

    class PendingCancelAdapter:
        family = "service-pending-cancel"

        def __init__(self) -> None:
            self.exited = False
            self.cancel_calls = 0

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "absent" if self.exited else "running"

        def cancel(self, target):
            self.cancel_calls += 1
            return self.exited

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = PendingCancelAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "service pending cancellation"),
    )
    state = factory._record_state(activation.child_session_id)
    requested = factory.cancel(activation.child_session_id, expected_revision=state.revision)
    assert requested.cancellation_requested is True
    restarted_registry = SessionRegistry(state_root=tmp_path / "registry")
    service = SessionService(
        registry=restarted_registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=DurableChildReconciler(
            registry=restarted_registry,
            repository=repository,
            adapters=[adapter],
        ),
    )
    pending = asyncio_run(service.ensure_session(activation.child_session_id))
    assert pending.loaded_from_retained_state is True
    assert ChildState.from_retained(
        await_record(restarted_registry, activation.child_session_id).metadata["durable_child"]
    ).terminal_count == 0
    adapter.exited = True
    settled = asyncio_run(service.ensure_session(activation.child_session_id))
    assert settled.loaded_from_retained_state is False
    assert settled.status.value == "stopped"
    retained = ChildState.from_retained(
        await_record(restarted_registry, activation.child_session_id).metadata["durable_child"]
    )
    assert (retained.terminal_outcome, retained.terminal_count) == ("canceled", 1)
    again = asyncio_run(service.ensure_session(activation.child_session_id))
    assert again.loaded_from_retained_state is False
    assert again.status.value == "stopped"
    assert adapter.cancel_calls == 3


def test_nested_parent_cancellation_replays_after_child_reconcile(tmp_path: Path) -> None:
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[RetryAdapter()]
    )
    first = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "nested parent"),
    )
    second_parent = WorkItem.restore(repository, first.child_work_item_id)
    second = factory.start(
        parent_session_id=first.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=second_parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "nested child"),
    )
    first_record = await_record(registry, first.child_session_id)
    marker = {
        "work_item_id": first.child_work_item_id,
        "reason": "nested restart cancellation",
        "child_recovery_refs": [second.recovery_ref],
    }
    await_record_update = registry.update_metadata(
        first.child_session_id,
        metadata={**first_record.metadata, "durable_parent_cancellation": marker},
    )
    asyncio_run(await_record_update)

    restarted_registry = SessionRegistry(state_root=tmp_path / "registry")
    restarted_repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    restarted_factory = DurableChildFactory(
        workspace,
        registry=restarted_registry,
        repository=restarted_repository,
        adapters=[RetryAdapter()],
    )

    class Reconciler:
        async def __call__(self, recovery_ref: str):
            return await asyncio.to_thread(restarted_factory.reconcile, recovery_ref)

        async def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
            state = await asyncio.to_thread(restarted_factory._record_state, child_session_id)
            return await asyncio.to_thread(
                restarted_factory.cancel,
                child_session_id,
                expected_revision=state.revision,
                reason=reason,
            )

        async def cancel_tree(self, parent_session_id: str, *, reason: str = "operator request"):
            return await asyncio.to_thread(
                restarted_factory.cancel_tree,
                parent_session_id=parent_session_id,
                parent_work_item_id=first.child_work_item_id,
                reason=reason,
            )

    service = SessionService(
        registry=restarted_registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=Reconciler(),
        durable_child_repository=restarted_repository,
    )
    record = asyncio_run(service.ensure_session(first.child_session_id))
    assert record.status.value == "stopped"
    assert "durable_parent_cancellation" not in (record.metadata or {})
    assert load_session(workspace, first.child_session_id)[0].read_model.status == "canceled"
    assert load_session(workspace, second.child_session_id)[0].read_model.status == "canceled"


def test_nested_parent_cancellation_keeps_resume_pending_on_unverified_child(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.service import SessionService

    class PendingCancelAdapter:
        family = "nested-pending-cancel"

        def __init__(self) -> None:
            self.cancel_ready = False

        def start(self, activation, spec):
            return ExecutionTarget(activation.execution_target_ref)

        def observe(self, target):
            return "running"

        def cancel(self, target):
            return self.cancel_ready

        def prepare_result(self, target, spec):
            return None

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = PendingCancelAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    first = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "nested pending parent"),
    )
    second_parent = WorkItem.restore(repository, first.child_work_item_id)
    second = factory.start(
        parent_session_id=first.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=second_parent.read_model.work_item_id,
        spec=_spec(adapter.family, "nested pending child"),
    )
    first_record = await_record(registry, first.child_session_id)
    asyncio_run(
        registry.update_metadata(
            first.child_session_id,
            metadata={
                **first_record.metadata,
                "durable_parent_cancellation": {
                    "work_item_id": first.child_work_item_id,
                    "reason": "nested pending cancellation",
                    "child_recovery_refs": [second.recovery_ref],
                },
            },
        )
    )
    restarted_registry = SessionRegistry(state_root=tmp_path / "registry")
    restarted_factory = DurableChildFactory(
        workspace,
        registry=restarted_registry,
        repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
        adapters=[adapter],
    )

    class Reconciler:
        async def __call__(self, recovery_ref: str):
            return await asyncio.to_thread(restarted_factory.reconcile, recovery_ref)

        async def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            child_session_id = recovery_ref.split("/attempt/", 1)[0].removeprefix("child://")
            state = await asyncio.to_thread(restarted_factory._record_state, child_session_id)
            return await asyncio.to_thread(
                restarted_factory.cancel,
                child_session_id,
                expected_revision=state.revision,
                reason=reason,
            )

        async def cancel_tree(self, parent_session_id: str, *, reason: str = "operator request"):
            raise AssertionError("parent cancellation replay should not call cancel_tree")

    service = SessionService(
        registry=restarted_registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=Reconciler(),
        durable_child_repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
    )
    with pytest.raises(RuntimeError, match="did not settle child"):
        asyncio_run(service.ensure_session(first.child_session_id))
    assert (
        await_record(restarted_registry, first.child_session_id).loaded_from_retained_state
        is True
    )
    adapter.cancel_ready = True
    record = asyncio_run(service.ensure_session(first.child_session_id))
    assert record.loaded_from_retained_state is False
    assert record.status.value == "stopped"
    assert "durable_parent_cancellation" not in (record.metadata or {})


def test_durable_child_factory_rejects_descriptor_backed_artifact_store(
    tmp_path: Path,
) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    root = tmp_path / "descriptor-artifacts"
    root.mkdir()
    descriptor = os.open(root, os.O_RDONLY)
    try:
        store = ArtifactStore(root, descriptor=descriptor)
        with pytest.raises(ChildError, match="stable path"):
            DurableChildFactory(
                workspace,
                registry=registry,
                repository=repository,
                adapters=[RetryAdapter()],
                artifact_store=store,
            )
    finally:
        os.close(descriptor)


def test_ray_completed_result_uses_custom_artifact_store(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    workspace, repository, parent, registry = _running_parent(tmp_path)
    custom_store = ArtifactStore(tmp_path / "custom-artifacts")
    actor_artifact = custom_store.put(
        b"custom ray actor output", media_type="application/octet-stream"
    )

    class FakeActor:
        def get_state(self) -> str:
            return "completed"

        def get_result(self) -> dict[str, object]:
            return {"artifact_ref": actor_artifact.as_dict()}

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-custom-artifacts"))
    adapter = RayJobAdapter(orchestrator, actor_launcher=lambda *_args: FakeActor())
    factory = DurableChildFactory(
        workspace,


        registry=registry,
        repository=repository,
        adapters=[adapter],
        artifact_store=custom_store,
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "custom artifact child"),
    )
    target = factory._record_state(activation.child_session_id).execution_target
    assert target["metadata"]["job"]["artifact_store_root"] == str(custom_store._root)
    recovered = factory.reconcile(activation.recovery_ref)
    assert recovered.status == "completed"
    assert recovered.result_refs
    completion_events = [
        event
        for event in orchestrator.event_log.events
        if event.type == "agent.job_completed"
    ]
    assert len(completion_events) == 1
    target = factory._record_state(activation.child_session_id).execution_target
    artifact_payload = target["metadata"]["job"]["result_payload"]["artifact_ref"]
    artifact = ArtifactRef(
        str(artifact_payload["digest"]),
        int(artifact_payload["size_bytes"]),
        str(artifact_payload["media_type"]),
    )
    assert custom_store.read(artifact) == b"custom ray actor output"



def test_ray_cancel_signal_failure_retains_recovery_state(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def cancel(self) -> None:
            raise RuntimeError("transient cancellation failure")

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-cancel-recovery"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-cancel-recovery-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    actor = Actor()
    actor_key = adapter._actor_key(provider_job_id, tmp_path)
    adapter._actors[actor_key] = actor
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }
    assert adapter.cancel(target) is False
    assert adapter._actors[actor_key] is actor
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "accepted"


def test_ray_pending_cancellation_does_not_terminalize_job(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def cancel(self) -> str:
            return "pending"

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-cancel-in-flight"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-cancel-in-flight-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    adapter._actors[adapter._actor_key(provider_job_id, tmp_path)] = Actor()
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }

    assert adapter.cancel(target) is False
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "accepted"


def test_ray_actor_cancellation_waits_for_execution_group_to_quiesce() -> None:
    from breadboard_engine.orchestration.agent_session import OpenCodeAgent

    actor_class = OpenCodeAgent.__ray_metadata__.modified_class
    actor = object.__new__(actor_class)
    actor.state = "running"
    actor._state_lock = threading.Lock()
    actor._execution_idle = threading.Event()

    assert actor_class.cancel(actor) == "pending"
    assert actor.state == "killed"
    actor._execution_idle.set()
    assert actor_class.cancel(actor) == "killed"


def test_ray_observe_state_failure_stays_recovery_pending(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self) -> str:
            raise RuntimeError("transient state inspection failure")

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-observe-recovery"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-observe-recovery-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    actor = Actor()
    actor_key = adapter._actor_key(provider_job_id, tmp_path)
    adapter._actors[actor_key] = actor
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }
    assert adapter.observe(target) == "pending"
    assert adapter._actors[actor_key] is actor
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "accepted"

def test_ray_result_rpc_failure_stays_recovery_pending(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self) -> str:
            return "completed"

        def get_result(self) -> Dict[str, Any]:
            raise RuntimeError("transient result inspection failure")

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-result-recovery"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-result-recovery-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    adapter._actors[adapter._actor_key(provider_job_id, tmp_path)] = Actor()
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }

    assert adapter.observe(target) == "pending"
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "accepted"
def test_ray_cancel_preserves_completed_actor_result(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def cancel(self) -> str:
            return "completed"

        def get_state(self) -> str:
            return "completed"

        def get_result(self) -> dict[str, bytes]:
            return {"result_bytes": b"completed before cancellation"}

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-completed-cancel"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-completed-cancel-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    adapter._actors[adapter._actor_key(provider_job_id, tmp_path)] = Actor()
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {
            "job": {
                "job_id": provider_job_id,
                "workspace": str(tmp_path),
                "artifact_store_root": str(tmp_path / "artifacts"),
            }
        },
    }

    assert adapter.cancel(target) is False
    job = orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    )
    assert job is not None and job.state == "completed"
    assert target["metadata"]["job"]["state"] == "completed"
def test_ray_cancel_after_restart_without_actor_remains_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    original = MultiAgentOrchestrator(TeamConfig("ray-cancel-original"))
    spawned = original.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"task": "survive restart"},
    )
    target = {
        "ref": f"job:{spawned.job.job_id}",
        "metadata": {
            "job": {
                "job_id": spawned.job.job_id,
                "agent_id": spawned.job.agent_id,
                "owner_agent": spawned.job.owner_agent,
                "kind": spawned.job.kind,
                "state": spawned.job.state,
                "seq": spawned.job.seq,
                "task_descriptor": spawned.job.task_descriptor,
            }
        },
    }
    restarted = MultiAgentOrchestrator(TeamConfig("ray-cancel-restarted"))
    adapter = RayJobAdapter(restarted)
    monkeypatch.setattr(adapter, "_lookup_actor", lambda _job_id, _workspace: None)

    assert adapter.cancel(target) is False
    restored = restarted.job_manager.get(spawned.job.job_id)
    assert restored is not None and restored.state == "accepted"
    assert target["metadata"]["job"]["state"] == "accepted"
def test_ray_reserved_target_recovers_named_actor_without_duplicate_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self):
            return "running"

    launches: list[str] = []
    actor = Actor()

    def launch(job_id: str, launch_workspace: Path, task: str) -> Actor:
        launches.append(task)
        return actor

    workspace, repository, parent, registry = _running_parent(tmp_path)
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-reserved-recovery"))
    adapter = RayJobAdapter(orchestrator, actor_launcher=launch)
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "ray reserved recovery"),
    )
    retained = next(record for record in asyncio_run(registry.records()) if record.metadata.get("durable_child"))
    state = ChildState.from_retained(retained.metadata["durable_child"])
    recovered = factory.reconcile(state.recovery_ref)
    assert recovered.status == "running"
    assert launches == ["child task"]


def test_ray_actor_submission_crash_is_resumed_once_after_claim_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def __init__(self) -> None:
            self.invocations: dict[str, str] = {}

        def submit_message_once(self, invocation_id: str, parts: list[dict[str, str]]) -> dict[str, str]:
            state = self.invocations.setdefault(invocation_id, "accepted")
            if state == "accepted":
                self.invocations[invocation_id] = "running"
            return {"state": self.invocations[invocation_id]}

        def get_invocation_state(self, invocation_id: str) -> str:
            return self.invocations.get(invocation_id, "missing")

        def get_state(self) -> str:
            return "running" if self.invocations else "accepted"

    actors: dict[str, Actor] = {}
    actor = Actor()

    def create_then_crash(job_id: str, launch_workspace: Path, task: str) -> Actor:
        actors[job_id] = actor
        raise RuntimeError("crash after actor creation")

    workspace, repository, parent, registry = _running_parent(tmp_path)
    first_orchestrator = MultiAgentOrchestrator(TeamConfig("ray-submit-crash"))
    first_adapter = RayJobAdapter(first_orchestrator, actor_launcher=create_then_crash)
    monkeypatch.setattr(first_adapter, "_lookup_actor", lambda job_id, _workspace: actors.get(job_id))
    first_factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[first_adapter])
    with pytest.raises(RuntimeError, match="crash after actor creation"):
        first_factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(first_adapter.family, "ray submit crash"),
        )
    retained = next(record for record in asyncio_run(registry.records()) if record.metadata.get("durable_child"))
    state = ChildState.from_retained(retained.metadata["durable_child"])
    assert state.launch_claim_until is not None and state.launch_claim_until > time.time()
    assert actor.invocations == {}

    second_orchestrator = MultiAgentOrchestrator(TeamConfig("ray-submit-recovery"))
    second_adapter = RayJobAdapter(
        second_orchestrator,
        actor_launcher=lambda *_args: pytest.fail("recovery must reuse named actor"),
    )
    monkeypatch.setattr(second_adapter, "_lookup_actor", lambda job_id, _workspace: actors.get(job_id))
    second_factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[second_adapter])
    assert second_factory.reconcile(state.recovery_ref) == state
    state = first_factory._cas(state, launch_claim_until=0.0)
    recovered = second_factory.reconcile(state.recovery_ref)
    invocation_id = second_adapter._invocation_id(state.execution_target_ref.removeprefix("job:"))
    assert actor.invocations == {invocation_id: "running"}
    assert recovered.status == "running"
    assert second_factory.reconcile(state.recovery_ref) == recovered
    assert actor.invocations == {invocation_id: "running"}


def test_ray_reserved_target_relaunches_after_clean_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    workspace, repository, parent, registry = _running_parent(tmp_path)
    first_adapter = RayJobAdapter(
        MultiAgentOrchestrator(TeamConfig("ray-prepublish-crash")),
        actor_launcher=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("crash before target publication")
        ),
    )
    monkeypatch.setattr(first_adapter, "_lookup_actor", lambda _job_id, _workspace: None)
    first_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[first_adapter],
    )
    with pytest.raises(RuntimeError, match="crash before target publication"):
        first_factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(first_adapter.family, "ray clean restart"),
        )
    retained = next(
        record
        for record in asyncio_run(registry.records())
        if record.metadata.get("durable_child")
    )
    state = ChildState.from_retained(retained.metadata["durable_child"])
    state = first_factory._cas(state, launch_claim_until=0.0)

    launches: list[str] = []
    actor = object()

    def relaunch(_job_id: str, _workspace: Path, task: str) -> object:
        launches.append(task)
        return actor

    second_orchestrator = MultiAgentOrchestrator(TeamConfig("ray-clean-restart"))
    second_adapter = RayJobAdapter(second_orchestrator, actor_launcher=relaunch)
    monkeypatch.setattr(second_adapter, "_lookup_actor", lambda _job_id, _workspace: None)
    second_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[second_adapter],
    )
    recovered = second_factory.reconcile(state.recovery_ref)

    assert recovered.launch_published is True
    assert launches == ["child task"]
    job = second_orchestrator.job_manager.get(
        second_adapter._manager_job_id(
            state.execution_target_ref.removeprefix("job:"), workspace
        )
    )
    assert job is not None and job.state == "accepted"


def test_shared_ray_adapter_keeps_artifact_roots_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        pass

    barrier = threading.Barrier(2)
    seen_workspaces: list[Path] = []

    def launch(job_id: str, launch_workspace: Path, task: str) -> Actor:
        seen_workspaces.append(launch_workspace)
        barrier.wait(timeout=2)
        return Actor()

    adapter = RayJobAdapter(
        MultiAgentOrchestrator(TeamConfig("ray-shared-root")),
        actor_launcher=launch,
    )
    monkeypatch.setattr(adapter, "_lookup_actor", lambda job_id, _workspace: None)
    workspaces = (tmp_path / "workspace-a", tmp_path / "workspace-b")
    roots = (workspaces[0] / "artifacts-a", workspaces[1] / "artifacts-b")
    activations = tuple(
        ChildActivation(
            "parent-session",
            "parent-session",
            "parent-work",
            f"child-session-{index}",
            f"child-work-{index}",
            f"attempt-{index}",
            f"child://child-session-{index}/attempt/attempt-{index}",
            f"job:child-{index}",
            adapter.family,
            str(workspace),
            artifact_store_root=str(root),
        )
        for index, (workspace, root) in enumerate(zip(workspaces, roots), 1)
    )
    for workspace in workspaces:
        workspace.mkdir()
    results: list[ExecutionTarget | None] = [None, None]
    errors: list[BaseException] = []

    def run(index: int) -> None:
        try:
            results[index] = adapter.start(activations[index], _spec(adapter.family, f"shared root {index}"))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert errors == []
    assert all(result is not None for result in results)
    assert {result.metadata["job"]["artifact_store_root"] for result in results if result is not None} == {
        str(root) for root in roots
    }
    assert set(seen_workspaces) == set(workspaces)

def test_shared_job_manager_scopes_ray_ids_by_workspace(
    tmp_path: Path,
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig
    from breadboard_engine.orchestration.job_manager import JobManager

    class Actor:
        def __init__(self, result: bytes) -> None:
            self.result = result

        def get_state(self) -> str:
            return "completed"

        def get_result(self) -> dict[str, bytes]:
            return {"result_bytes": self.result}

        def cancel(self) -> bool:
            return True

    shared_manager = JobManager()
    orchestrator = MultiAgentOrchestrator(
        TeamConfig("ray-shared-job-manager"),
        job_manager=shared_manager,
    )
    actors: dict[Path, Actor] = {}

    def launch(_job_id: str, workspace: Path, _task: str) -> Actor:
        actor = Actor(workspace.name.encode())
        actors[workspace] = actor
        return actor

    adapter = RayJobAdapter(orchestrator, actor_launcher=launch)
    provider_job_id = "same-provider-job"
    workspaces = (tmp_path / "workspace-a", tmp_path / "workspace-b")
    for workspace in workspaces:
        workspace.mkdir()

    def activation(workspace: Path, child_id: str) -> ChildActivation:
        return ChildActivation(
            "parent-session",
            "parent-session",
            "parent-work",
            child_id,
            f"{child_id}-work",
            f"{child_id}-attempt",
            f"child://{child_id}/attempt/{child_id}-attempt",
            f"job:{provider_job_id}",
            adapter.family,
            str(workspace),
        )

    targets = tuple(
        adapter.start(
            activation(workspace, f"child-{index}"),
            _spec(adapter.family, f"shared job {index}"),
        )
        for index, workspace in enumerate(workspaces, 1)
    )
    assert [
        target.metadata["job"]["job_id"]
        for target in targets
    ] == [provider_job_id, provider_job_id]
    manager_jobs = tuple(
        shared_manager.get(adapter._manager_job_id(provider_job_id, workspace))
        for workspace in workspaces
    )
    assert all(job is not None for job in manager_jobs)
    assert manager_jobs[0] is not manager_jobs[1]
    assert adapter._actors[
        adapter._actor_key(provider_job_id, workspaces[0])
    ] is actors[workspaces[0]]
    assert adapter._actors[
        adapter._actor_key(provider_job_id, workspaces[1])
    ] is actors[workspaces[1]]

    assert adapter.observe(targets[0].retained()) == "completed"
    adapter.acknowledge_result(targets[0].retained())
    assert (
        shared_manager.get(
            adapter._manager_job_id(provider_job_id, workspaces[0])
        ).state
        == "completed"
    )
    assert (
        shared_manager.get(
            adapter._manager_job_id(provider_job_id, workspaces[1])
        ).state
        == "accepted"
    )
    assert adapter.cancel(targets[1].retained()) is True
    assert (
        shared_manager.get(
            adapter._manager_job_id(provider_job_id, workspaces[0])
        ).state
        == "completed"
    )
    assert (
        shared_manager.get(
            adapter._manager_job_id(provider_job_id, workspaces[1])
        ).state
        == "killed"
    )
    assert {
        adapter._actor_key(provider_job_id, workspace)
        for workspace in workspaces
    } == adapter._released_actor_ids


def test_ray_adapter_launches_named_runtime_with_locked_task(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class FakeActor:
        def get_state(self) -> str:
            return "completed"

        def get_result(self) -> dict[str, str]:
            return {"result": "ray output"}

    calls: list[tuple[str, str, str]] = []
    actor = FakeActor()

    def launch(job_id: str, workspace: Path, task: str) -> FakeActor:
        calls.append((job_id, str(workspace), task))
        return actor

    workspace, repository, parent, registry = _running_parent(tmp_path)
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-runtime"))
    adapter = RayJobAdapter(orchestrator, actor_launcher=launch)
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=ChildSpec("ray child", "locked ray task", _lock(), "child-worker", adapter.family),
    )

    assert calls == [(activation.execution_target_ref.removeprefix("job:"), str(workspace), "locked ray task")]
    recovered = factory.reconcile(activation.recovery_ref)
    assert recovered.status == "completed"
    assert recovered.result_prepared
    assert recovered.result_refs
def test_ray_completed_result_survives_actor_disappearance_from_job_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self):
            return "completed"

        def get_result(self):
            return {"result": "persisted ray result"}

    workspace, repository, parent, registry = _running_parent(tmp_path)
    actor = Actor()
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-disappearance"))
    adapter = RayJobAdapter(orchestrator, actor_launcher=lambda job_id, launch_workspace, task: actor)
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "ray disappearance"),
    )
    target = factory._record_state(activation.child_session_id).execution_target
    assert adapter.observe(target) == "completed"
    monkeypatch.setattr(adapter, "_lookup_actor", lambda job_id, _workspace: None)
    assert adapter.observe(target) == "completed"
    result_ref = adapter.prepare_result(target, _spec(adapter.family, "ray disappearance"))
    assert isinstance(result_ref, ArtifactRef)
    assert ArtifactStore(workspace / ".breadboard" / "artifacts").read(result_ref) == b"persisted ray result"
def test_ray_result_persistence_failure_stays_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self):
            return "completed"

        def get_result(self):
            return {"result": "must remain pending"}

    workspace, repository, parent, registry = _running_parent(tmp_path)
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-persistence-failure"))
    adapter = RayJobAdapter(orchestrator, actor_launcher=lambda job_id, launch_workspace, task: Actor())
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "persistence failure"),
    )
    target = factory._record_state(activation.child_session_id).execution_target

    def reject_persistence(_target, _payload):
        raise OSError("artifact store unavailable")

    monkeypatch.setattr(adapter, "_durably_prepare_result", reject_persistence)
    assert adapter.observe(target) == "accepted"
    job = orchestrator.job_manager.get(
        adapter._manager_job_id(target["ref"].removeprefix("job:"), workspace)
    )


@pytest.mark.parametrize("failure_type", (RuntimeError, FileNotFoundError))
def test_ray_result_integrity_failure_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[Exception],
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self):
            return "completed"

        def get_result(self):
            return {"result": "corrupt result"}

    workspace, repository, parent, registry = _running_parent(tmp_path)
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-integrity-failure"))
    adapter = RayJobAdapter(
        orchestrator,
        actor_launcher=lambda job_id, launch_workspace, task: Actor(),
    )
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "integrity failure"),
    )
    target = factory._record_state(activation.child_session_id).execution_target
    monkeypatch.setattr(
        adapter,
        "_durably_prepare_result",
        lambda _target, _payload: (_ for _ in ()).throw(
            failure_type("artifact digest unavailable")
        ),
    )

    assert adapter.observe(target) == "failed"
    job = orchestrator.job_manager.get(
        adapter._manager_job_id(target["ref"].removeprefix("job:"), tmp_path)
    )
def test_ray_malformed_completed_payload_fails_once(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self):
            return "completed"

        def get_result(self):
            return "malformed"

    workspace, repository, parent, registry = _running_parent(tmp_path)
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-malformed-result"))
    adapter = RayJobAdapter(orchestrator, actor_launcher=lambda job_id, launch_workspace, task: Actor())
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "malformed result"),
    )
    target = factory._record_state(activation.child_session_id).execution_target
    assert adapter.observe(target) == "failed"
    assert adapter.observe(target) == "failed"
    job = orchestrator.job_manager.get(
        adapter._manager_job_id(target["ref"].removeprefix("job:"), workspace)
    )

def test_ray_malformed_artifact_reference_fails_once(tmp_path: Path) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self):
            return "completed"

        def get_result(self):
            return {"artifact_ref": {"digest": "not-a-digest"}}

    workspace, repository, parent, registry = _running_parent(tmp_path)
    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-malformed-artifact"))
    adapter = RayJobAdapter(
        orchestrator,
        actor_launcher=lambda job_id, launch_workspace, task: Actor(),
    )
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "malformed artifact"),
    )
    target = factory._record_state(activation.child_session_id).execution_target

    assert adapter.observe(target) == "failed"
    assert adapter.observe(target) == "failed"
    job = orchestrator.job_manager.get(
        adapter._manager_job_id(target["ref"].removeprefix("job:"), workspace)
    )



def test_ray_retained_completed_payload_is_validated_before_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-retained-malformed"))
    adapter = RayJobAdapter(orchestrator)
    monkeypatch.setattr(adapter, "_lookup_actor", lambda _job_id, _workspace: None)
    job_id = "retained-malformed-job"
    target = {
        "ref": f"job:{job_id}",
        "metadata": {
            "job": {
                "job_id": job_id,
                "agent_id": "child-session",
                "owner_agent": "parent-session",
                "kind": "subagent",
                "state": "completed",
                "seq": 1,
                "task_descriptor": {},
                "workspace": str(tmp_path),
                "artifact_store_root": str(tmp_path / "artifacts"),
                "result_payload": {"artifact_ref": {"digest": "not-a-digest"}},
            }
        },
    }

    assert adapter.observe(target) == "failed"
    job = orchestrator.job_manager.get(
        adapter._manager_job_id(job_id, tmp_path)
    )


def test_ray_cancellation_adopts_retained_completion_without_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class RunningActor:
        def get_state(self) -> str:
            return "running"

    workspace, repository, parent, registry = _running_parent(tmp_path)
    initial_orchestrator = MultiAgentOrchestrator(TeamConfig("ray-retained-winner"))
    initial_adapter = RayJobAdapter(
        initial_orchestrator,
        actor_launcher=lambda *_args: RunningActor(),
    )
    initial_factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[initial_adapter],
    )
    activation = initial_factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(initial_adapter.family, "retained completion wins"),
    )
    state = initial_factory._record_state(activation.child_session_id)
    artifact = initial_factory.artifacts.put(b"retained result")
    target = dict(state.execution_target)
    metadata = dict(target["metadata"])
    job = dict(metadata["job"])
    job["state"] = "completed"
    job["result_payload"] = {"artifact_ref": artifact.as_dict()}
    metadata["job"] = job
    target["metadata"] = metadata
    state = initial_factory._cas(
        state,
        status="cancel_requested",
        cancellation_requested=True,
        cancellation_reason="operator request",
        execution_target=target,
    )

    recovered_orchestrator = MultiAgentOrchestrator(
        TeamConfig("ray-retained-winner")
    )
    recovered_adapter = RayJobAdapter(recovered_orchestrator)
    monkeypatch.setattr(recovered_adapter, "_lookup_actor", lambda _job_id, _workspace: None)
    recovered_factory = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
        adapters=[recovered_adapter],
    )

    recovered = recovered_factory.reconcile(state.recovery_ref)

    assert recovered.terminal_outcome == "completed"
    assert recovered.result_refs == (artifact.digest,)
    assert recovered_factory.artifacts.read(artifact) == b"retained result"
    completion_events = [
        event
        for event in recovered_orchestrator.event_log.events
        if event.type == "agent.job_completed"
    ]
    assert len(completion_events) == 1
def test_job_completion_replay_preserves_artifact_reference() -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("job-replay"))
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"recovery_ref": "child://child-session/attempt/a"},
    )
    artifact = {"digest": "sha256:" + "b" * 64, "size_bytes": 3, "media_type": "text/plain"}
    orchestrator.mark_job_completed(spawned.job.job_id, result_payload={"artifact_ref": artifact})

    rebuilt = MultiAgentOrchestrator(TeamConfig("job-replay"), event_log=orchestrator.event_log)
    restored = rebuilt.job_manager.get(spawned.job.job_id)
    assert restored is not None
    assert restored.result_payload == {"artifact_ref": artifact}
    failed = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="failed-child-session",
        async_mode=True,
    ).job
    killed = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="killed-child-session",
        async_mode=True,
    ).job
    assert orchestrator.mark_job_failed(failed.job_id) is not None
    assert orchestrator.mark_job_killed(killed.job_id) is not None

    rebuilt = MultiAgentOrchestrator(
        TeamConfig("job-terminal-replay"),
        event_log=orchestrator.event_log,
    )
    assert rebuilt.job_manager.get(failed.job_id).state == "failed"
    assert rebuilt.job_manager.get(killed.job_id).state == "killed"



def test_default_service_stops_and_deletes_ordinary_sessions_without_workspace(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    registry = SessionRegistry(state_root=tmp_path / "registry")
    for session_id in ("stop-session", "delete-session"):
        asyncio_run(
            registry.create(
                SessionRecord(
                    session_id,
                    status=SessionStatus.RUNNING,
                    metadata={},
                )
            )
        )
    service = SessionService(registry=registry, state_root=tmp_path / "registry")

    asyncio_run(service.stop_session("stop-session"))
    stopped = asyncio_run(registry.get("stop-session"))
    assert stopped is not None

    asyncio_run(service.delete_session("delete-session"))
    assert asyncio_run(registry.get("delete-session")) is None


def test_default_service_refuses_parent_stop_with_unreconciled_child(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace, registry=registry, repository=repository, adapters=[RetryAdapter()]
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "default stop guard"),
    )
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    service = SessionService(
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        state_root=tmp_path / "registry",
    )
    with pytest.raises(RuntimeError, match="retained children"):
        asyncio_run(service.stop_session("parent-session"))
    child_state = factory._record_state(activation.child_session_id)
    assert child_state.cancellation_requested is False


def test_parent_stop_rejects_noninteger_retained_terminal_count(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    registry = SessionRegistry(state_root=tmp_path / "registry")
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(tmp_path)},
            )
        )
    )
    asyncio_run(
        registry.create(
            SessionRecord(
                "unrelated-parent",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(tmp_path)},
            )
        )
    )
    asyncio_run(
        registry.create(
            SessionRecord(
                "child-session",
                status=SessionStatus.RUNNING,
                metadata={
                    "workspace": str(tmp_path),
                    "durable_child": {
                        "parent_session_id": "parent-session",
                        "terminal_count": True,
                    },
                },
            )
        )
    )
    service = SessionService(registry=registry, state_root=tmp_path / "registry")
    asyncio_run(service.stop_session("unrelated-parent"))

    with pytest.raises(RuntimeError, match="terminal_count must be exactly 0 or 1"):
        asyncio_run(service.stop_session("parent-session"))

def test_default_service_reconciles_process_child_after_restart(tmp_path: Path) -> None:
    from breadboard_engine.api.cli_bridge.service import SessionService

    root = tmp_path / "registry"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = WorkItemRepository(root / "authoritative-owner.jsonl")
    parent = Session.start(_lock(), "parent task", session_id="parent-session")
    create_session(workspace, parent)
    parent_work = WorkItem.create("parent work", work_item_id="parent-work", repository=repository)
    parent_work.acquire_lease("parent-worker", lease_id="parent-lease")
    parent_work.start_attempt("parent-session", lease_id="parent-lease", attempt_id="parent-attempt")
    registry = SessionRegistry(state_root=root)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "default service"),
    )
    assert not (workspace / ".breadboard" / "child-specs").exists()
    process = adapter._processes[activation.execution_target_ref]
    try:
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
        restarted = SessionService(
            registry=SessionRegistry(state_root=root),
            state_root=root,
            durable_child_repository=WorkItemRepository(root / "authoritative-owner.jsonl"),
        )
        record = asyncio_run(restarted.ensure_session(activation.child_session_id))
        assert record.runner is None
        assert record.product_session is not None
        assert record.product_session.read_model.status == "failed"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_ray_kill_is_immutable_against_late_completion() -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-race"))
    adapter = RayJobAdapter(orchestrator)
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"recovery_ref": "child://child-session/attempt/a"},
    )
    target = ExecutionTarget(f"job:{spawned.job.job_id}")
    adapter.cancel(target.retained())
    assert orchestrator.mark_job_completed(spawned.job.job_id, result_payload={"result": "late"}) is None
    assert adapter.observe(target.retained()) == "killed"
def test_ray_submission_waits_for_actor_invocation_acceptance() -> None:
    class SubmitMethod:
        def __init__(self, actor: "Actor") -> None:
            self.actor = actor

        def remote(self, _invocation_id: str, _parts: object) -> None:
            self.actor.submitted = True

    class Actor:
        def __init__(self) -> None:
            self.submitted = False
            self.state_reads = 0
            self.submit_message_once = SubmitMethod(self)

        def get_invocation_state(self, _invocation_id: str) -> str:
            assert self.submitted
            self.state_reads += 1
            return "accepted" if self.state_reads >= 3 else "missing"

    actor = Actor()
    assert RayJobAdapter._submit_invocation(
        actor, "child-invocation:job", "task"
    )
    assert actor.state_reads == 3


def test_ray_actor_failure_updates_job_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class FailedActor:
        def get_state(self) -> str:
            return "failed"

        def get_invocation_state(self, _invocation_id: str) -> str:
            return "failed"

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-actor-failure"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-actor-failure-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"recovery_ref": "child://child-session/attempt/a"},
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    monkeypatch.setattr(adapter, "_lookup_actor", lambda _job_id, _workspace: FailedActor())
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }

    assert adapter.observe(target) == "failed"
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "failed"


def test_ray_absent_nonterminal_job_is_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-absent"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-absent-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"recovery_ref": "child://child-session/attempt/a"},
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    monkeypatch.setattr(adapter, "_lookup_actor", lambda job_id, _workspace: None)
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }
    assert adapter.observe(target) == "absent"
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "failed"


def test_ray_missing_invocation_cancels_detached_actor_and_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def __init__(self) -> None:
            self.cancelled = False

        def get_invocation_state(self, _invocation_id: str) -> str:
            return "missing"

        def cancel(self) -> bool:
            self.cancelled = True
            return True

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-missing-invocation"))
    adapter = RayJobAdapter(orchestrator)
    provider_job_id = "ray-missing-invocation-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"recovery_ref": "child://child-session/attempt/a"},
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    actor = Actor()
    actor_key = adapter._actor_key(provider_job_id, tmp_path)
    adapter._actors[actor_key] = actor
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id, "workspace": str(tmp_path)}},
    }
    killed: list[object] = []
    import ray

    monkeypatch.setattr(
        ray,
        "kill",
        lambda killed_actor, *, no_restart: killed.append(
            (killed_actor, no_restart)
        ),
    )

    assert adapter.observe(target) == "absent"
    assert actor.cancelled
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "killed"
    assert killed == [(actor, True)]
    assert actor_key not in adapter._actors
def test_registry_record_lock_uses_portable_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.api.cli_bridge.registry import persistence

    lock_paths: list[Path] = []

    class FakeProcessLock:
        def __init__(self, path: Path) -> None:
            lock_paths.append(path)

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(persistence, "ProcessLock", FakeProcessLock)
    monkeypatch.setattr(persistence.os, "name", "nt")
    mixin = object.__new__(persistence.PersistenceMixin)
    mixin._state_root = tmp_path
    async def acquire() -> None:
        async with mixin._record_file_lock("windows-session"):
            pass

    asyncio_run(acquire())
    identity = hashlib.sha256(
        f"{tmp_path.resolve()}\0windows-session".encode("utf-8")
    ).hexdigest()
    assert lock_paths == [tmp_path.parent / ".breadboard-session-locks" / identity]

def test_process_lock_releases_after_cancelled_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from breadboard_engine.api.cli_bridge.registry import persistence

    acquisition_started = threading.Event()
    allow_acquisition = threading.Event()
    released = threading.Event()

    class BlockingProcessLock:
        def __init__(self, _path: Path) -> None:
            pass

        def __enter__(self):
            acquisition_started.set()
            assert allow_acquisition.wait(timeout=2)
            return self

        def __exit__(self, *args: object) -> None:
            released.set()

    monkeypatch.setattr(persistence, "ProcessLock", BlockingProcessLock)

    async def scenario() -> None:
        async def acquire() -> None:
            async with persistence._process_lock(tmp_path / "cancel.lock"):
                pytest.fail("cancelled acquisition must not enter the body")

        task = asyncio.create_task(acquire())
        assert await asyncio.to_thread(acquisition_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        allow_acquisition.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio_run(scenario())
    assert released.is_set()



def test_work_item_cancel_torn_transaction_does_not_orphan_descendant(tmp_path: Path) -> None:
    path = tmp_path / "work-items.jsonl"
    repository = WorkItemRepository(path)
    parent = WorkItem.create("parent", work_item_id="parent", repository=repository)
    parent.acquire_lease("worker", lease_id="lease")
    parent.start_attempt("parent-session", lease_id="lease", attempt_id="attempt")
    child = parent.delegate("child", attempt_id="attempt", child_work_item_id="child")
    child.acquire_lease("worker", lease_id="child-lease")
    child.start_attempt("child-session", lease_id="child-lease", attempt_id="child-attempt")
    parent.cancel("operator", "caller reason")
    data = path.read_bytes()
    path.write_bytes(data.rsplit(b"\n", 2)[0] + b"\n")
    recovered = WorkItemRepository(path)
    assert WorkItem.restore(recovered, "parent").read_model.status == "running"
    assert WorkItem.restore(recovered, "child").read_model.status == "running"
@pytest.mark.parametrize(
    "changes",
    (
        {"terminal_count": -1},
        {"terminal_count": 2},
        {"terminal_count": 0, "terminal_outcome": "failed"},
        {
            "terminal_count": 1,
            "terminal_outcome": "failed",
            "status": "running",
        },
        {"status": "unknown"},
        {"status": "completed", "terminal_count": 0, "terminal_outcome": None},
    ),
)
def test_child_state_rejects_inconsistent_terminal_cardinality(
    changes: Dict[str, Any],
) -> None:
    retained = ChildState(
        child_session_id="child-session",
        child_work_item_id="child-work",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        attempt_id="attempt",
        recovery_ref="child://child-session/attempt/attempt",
        execution_target_ref="reserved:child-session",
        adapter_family="execution-world-process",
        status="running",
        execution_target={"ref": "reserved:child-session"},
        revision=0,
    ).retained()
    retained.update(changes)

    with pytest.raises(ValueError, match="durable child"):
        ChildState.from_retained(retained)


@pytest.mark.parametrize(
    "changes",
    (
        {"child_session_id": 123},
        {"child_work_item_id": ""},
        {"recovery_ref": "child://other/attempt/attempt"},
        {"revision": True},
        {"revision": -1},
        {"launch_claim_owner": ""},
        {"launch_claim_until": "later"},
        {"launch_claim_until": float("inf")},
        {"launch_claim_until": float("nan")},
        {"cancellation_reason": ""},
        {"startup_phase": "invented"},
        {"execution_target_ref": " ", "execution_target": {"ref": " "}},
        {"result_refs": ["not-a-digest"]},
        {"result_refs": [HASH, HASH]},
    ),
)
def test_child_state_rejects_malformed_core_identity(
    changes: Dict[str, Any],
) -> None:
    retained = ChildState(
        child_session_id="child-session",
        child_work_item_id="child-work",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        attempt_id="attempt",
        recovery_ref="child://child-session/attempt/attempt",
        execution_target_ref="reserved:child-session",
        adapter_family="execution-world-process",
        status="running",
        revision=0,
        child_spec=_spec("execution-world-process").retained(),
        execution_target={"ref": "reserved:child-session"},
    ).retained()
    retained.update(changes)

    with pytest.raises(ValueError, match="durable child"):
        ChildState.from_retained(retained)


def test_child_state_rejects_mismatched_execution_target_identity() -> None:
    retained = ChildState(
        child_session_id="child-session",
        child_work_item_id="child-work",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        attempt_id="attempt",
        recovery_ref="child://child-session/attempt/attempt",
        execution_target_ref="reserved:child-session",
        adapter_family="execution-world-process",
        status="running",
        revision=0,
        execution_target={"ref": "reserved:other-child"},
    ).retained()

    with pytest.raises(ValueError, match="execution target identity"):
        ChildState.from_retained(retained)


def test_child_state_rejects_malformed_retained_specification() -> None:
    retained = ChildState(
        child_session_id="child-session",
        child_work_item_id="child-work",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        attempt_id="attempt",
        recovery_ref="child://child-session/attempt/attempt",
        execution_target_ref="reserved:child-session",
        adapter_family="execution-world-process",
        status="running",
        revision=0,
        child_spec=_spec("execution-world-process").retained(),
        execution_target={"ref": "reserved:child-session"},
    ).retained()
    malformed_values = (
        "not-a-mapping",
        {
            **retained["child_spec"],
            "cancellation_policy": [],
        },
    )

    for malformed in malformed_values:
        candidate = {**retained, "child_spec": malformed}
        with pytest.raises(ValueError, match="durable child specification"):
            ChildState.from_retained(candidate)


def test_child_start_rejects_mismatched_parent_session_and_work_item(
    tmp_path: Path,
) -> None:
    from breadboard.product.runtime.artifacts import list_workspace_artifacts

    workspace, repository, _parent, registry = _running_parent(tmp_path)
    other = WorkItem.create(
        "other parent",
        work_item_id="other-parent-work",
        repository=repository,
    )
    other.acquire_lease("other-worker", lease_id="other-lease")
    other.start_attempt(
        "other-parent-session",
        lease_id="other-lease",
        attempt_id="other-attempt",
    )
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )

    with pytest.raises(ChildError, match="parent Session"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=other.read_model.work_item_id,
            spec=_spec(adapter.family, "mismatched owner"),
        )

    assert list_workspace_artifacts(workspace) == []
    assert await_records(registry) == []


def test_reconciler_without_children_leaves_parent_admission_open(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    workspace, repository, parent, registry = _running_parent(tmp_path)
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    reconciler = DurableChildReconciler(
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )

    assert asyncio_run(reconciler.cancel_tree("parent-session")) == ()
    assert not await_record(registry, "parent-session").admission_closed
    restarted = SessionRegistry(state_root=tmp_path / "registry")
    assert not await_record(restarted, "parent-session").admission_closed

    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(RetryAdapter.family, "new child"),
    )
    assert activation.child_session_id


def test_ray_actor_lookup_uses_stable_workspace_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ray
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    actor = object()
    other_actor = object()
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    lookups: list[tuple[str, str | None]] = []

    def get_actor(name: str, *, namespace: str | None = None):
        lookups.append((name, namespace))
        return (
            other_actor
            if namespace == RayJobAdapter._actor_namespace(other_workspace)
            else actor
        )

    monkeypatch.setattr(ray, "get_actor", get_actor)
    adapter = RayJobAdapter(
        MultiAgentOrchestrator(TeamConfig("ray-stable-namespace"))
    )

    assert adapter._lookup_actor("job-stable-namespace", tmp_path) is actor
    assert (
        adapter._lookup_actor("job-stable-namespace", other_workspace)
        is other_actor
    )
    restarted = RayJobAdapter(
        MultiAgentOrchestrator(TeamConfig("ray-stable-namespace-restart"))
    )
    recovered = restarted.recover(
        {
            "ref": "job:job-stable-namespace",
            "metadata": {
                "job": {
                    "job_id": "job-stable-namespace",
                    "workspace": str(tmp_path),
                }
            },
        }
    )

    assert recovered is not None
    assert recovered.volatile_handle is actor
    assert lookups == [
        (
            "bb-child-job-stable-namespace",
            adapter._actor_namespace(tmp_path),
        ),
        (
            "bb-child-job-stable-namespace",
            adapter._actor_namespace(other_workspace),
        ),
        (
            "bb-child-job-stable-namespace",
            adapter._actor_namespace(tmp_path),
        ),
    ]


def test_ray_actor_lookup_runtime_failure_stays_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ray
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-lookup-unavailable"))
    adapter = RayJobAdapter(orchestrator)
    adapter.bind_workspace(tmp_path)
    provider_job_id = "ray-lookup-unavailable-job"
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        job_id=adapter._manager_job_id(provider_job_id, tmp_path),
    )
    monkeypatch.setattr(
        ray,
        "get_actor",
        lambda _name, **_kwargs: (_ for _ in ()).throw(RuntimeError("Ray unavailable")),
    )
    target = {
        "ref": f"job:{provider_job_id}",
        "metadata": {"job": {"job_id": provider_job_id}},
    }

    assert adapter.observe(target) == "pending"
    assert orchestrator.job_manager.get(
        adapter._manager_job_id(provider_job_id, tmp_path)
    ).state == "accepted"

def test_reconcile_adopts_failed_child_owner_before_target_observation(
    tmp_path: Path,
) -> None:
    class NoObservationAdapter(RetryAdapter):
        family = "no-observation-after-owner-terminal"

        def observe(self, target):
            raise AssertionError(
                "terminal Work Item must win before target observation"
            )

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = NoObservationAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "externally failed child"),
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    attempt = child.read_model.current_attempt
    assert attempt is not None
    child.fail_attempt(
        "external failure",
        attempt_id=attempt.attempt_id,
        retryable=False,
    )

    recovered = factory.reconcile(activation.recovery_ref)

    assert recovered.terminal_outcome == "failed"
    assert recovered.terminal_count == 1


def test_parent_cancellation_replay_rejects_missing_child_reference_list(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    workspace, repository, parent, registry = _running_parent(tmp_path)
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={
                    "workspace": str(workspace),
                    "durable_parent_cancellation": {
                        "work_item_id": parent.read_model.work_item_id,
                        "reason": "restart cancellation",
                    },
                },
            )
        )
    )
    service = SessionService(
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        state_root=tmp_path / "registry",
        durable_child_reconciler=DurableChildReconciler(
            registry=registry,
            repository=repository,
            adapters=[RetryAdapter()],
        ),
        durable_child_repository=repository,
    )

    with pytest.raises(RuntimeError, match="child references are invalid"):
        asyncio_run(service.ensure_session("parent-session"))


def test_reconcile_repairs_terminal_product_owner_before_target_observation(
    tmp_path: Path,
) -> None:
    class StopOnlyAdapter(RetryAdapter):
        family = "terminal-product-repair"

        def observe(self, target):
            raise AssertionError("terminal Product owner must win before observation")

        def cancel(self, target):
            return True

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = StopOnlyAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "terminal Product repair"),
    )
    mutate_session(
        workspace,
        activation.child_session_id,
        lambda current: current.fail("child_failed", "simulated settlement crash"),
    )

    recovered = factory.reconcile(activation.recovery_ref)

    assert recovered.terminal_outcome == "failed"
    assert recovered.terminal_count == 1


def test_parent_race_after_child_product_creation_is_durably_canceled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    create = children_module.create_session

    def create_then_cancel_parent(workspace_path, session):
        create(workspace_path, session)
        parent_work.cancel("operator", "startup race")
        mutate_session(
            workspace,
            "parent-session",
            lambda current: current.cancel("startup race"),
        )

    monkeypatch.setattr(children_module, "create_session", create_then_cancel_parent)

    with pytest.raises(ChildError, match="parent owner became terminal"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent_work.read_model.work_item_id,
            spec=_spec(adapter.family, "startup race"),
        )

    child_records = [
        record
        for record in await_records(registry)
        if isinstance(record.metadata.get("durable_child"), dict)
    ]
    assert len(child_records) == 1
    state = ChildState.from_retained(child_records[0].metadata["durable_child"])
    assert state.status == "canceled"
    assert state.terminal_count == 1
    child_product, _ = load_session(workspace, state.child_session_id)
    assert child_product.read_model.status == "canceled"
    assert repository.read(state.child_work_item_id) == ()


def test_cancel_tree_rejects_mismatched_parent_owner_pair(tmp_path: Path) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    other = WorkItem.create(
        "other parent",
        work_item_id="other-parent-work",
        repository=repository,
    )
    other.acquire_lease("worker", lease_id="other-lease")
    other.start_attempt(
        "other-session",
        lease_id="other-lease",
        attempt_id="other-attempt",
    )
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )

    with pytest.raises(ChildError, match="does not belong"):
        factory.cancel_tree(
            parent_session_id="parent-session",
            parent_work_item_id=other.read_model.work_item_id,
        )

    assert parent_work.read_model.status == "running"


def test_completed_settlement_replay_requires_prepared_result() -> None:
    retained = ChildState(
        child_session_id="child-session",
        child_work_item_id="child-work",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        attempt_id="attempt",
        recovery_ref="child://child-session/attempt/attempt",
        execution_target_ref="reserved:child-session",
        adapter_family="execution-world-process",
        status="running",
        revision=1,
        execution_target={"ref": "reserved:child-session"},
    ).retained()
    retained["settlement"] = {"outcome": "completed", "result_refs": []}

    with pytest.raises(ValueError, match="settlement"):
        ChildState.from_retained(retained)


def test_paused_child_product_clears_rejected_settlement_reservation(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "paused Product owner"),
    )
    state = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(
        activation.child_session_id,
        expected_revision=state.revision,
        result=b"prepared",
        attempt_id=state.attempt_id,
    )
    mutate_session(
        workspace,
        activation.child_session_id,
        lambda current: current.pause("external pause"),
    )

    with pytest.raises(ChildError, match="cannot accept child settlement"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            attempt_id=prepared.attempt_id,
        )

    assert factory._record_state(activation.child_session_id).settlement is None


def test_conflicting_terminal_owner_clears_settlement_reservation(
    tmp_path: Path,
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec("retry-adapter", "conflicting terminal"),
    )
    state = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(
        activation.child_session_id,
        expected_revision=state.revision,
        result=b"prepared",
        attempt_id=state.attempt_id,
    )
    child = WorkItem.restore(repository, activation.child_work_item_id)
    child.fail("external", "independent terminal owner")

    with pytest.raises(ChildError, match="terminal outcome disagrees"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            attempt_id=prepared.attempt_id,
        )

    assert factory._record_state(activation.child_session_id).settlement is None

def test_stale_registry_persist_cannot_reopen_closed_admission(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    state_root = tmp_path / "registry"
    closer = SessionRegistry(state_root=state_root)
    asyncio_run(
        closer.create(
            SessionRecord("parent-session", status=SessionStatus.RUNNING)
        )
    )
    stale_writer = SessionRegistry(state_root=state_root)
    asyncio_run(closer.close_admission("parent-session"))
    asyncio_run(
        stale_writer.update_status(
            "parent-session",
            status=SessionStatus.COMPLETED,
        )
    )

    recovered = SessionRegistry(state_root=state_root)
    assert await_record(recovered, "parent-session").admission_closed is True


def test_parent_cancellation_marker_merges_across_stale_registries(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    state_root = tmp_path / "registry"
    first = SessionRegistry(state_root=state_root)
    asyncio_run(
        first.create(
            SessionRecord("parent-session", status=SessionStatus.RUNNING)
        )
    )
    second = SessionRegistry(state_root=state_root)
    asyncio_run(
        first.close_admission_for_parent_cancellation(
            "parent-session",
            work_item_id="work-a",
            reason="cancel a",
            child_recovery_refs=["child://a/attempt/1"],
        )
    )
    asyncio_run(
        second.close_admission_for_parent_cancellation(
            "parent-session",
            work_item_id="work-b",
            reason="cancel b",
            child_recovery_refs=["child://b/attempt/1"],
        )
    )

    marker = await_record(
        SessionRegistry(state_root=state_root), "parent-session"
    ).metadata["durable_parent_cancellation"]
    assert [request["work_item_id"] for request in marker["requests"]] == [
        "work-a",
        "work-b",
    ]



    asyncio_run(
        first.remove_durable_parent_cancellation_request(
            "parent-session",
            work_item_id="work-a",
        )
    )
    remaining_marker = await_record(
        SessionRegistry(state_root=state_root), "parent-session"
    ).metadata["durable_parent_cancellation"]
    assert [request["work_item_id"] for request in remaining_marker["requests"]] == [
        "work-b"
    ]
def test_parent_cancellation_waits_for_in_flight_admission(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    async def scenario() -> None:
        registry = SessionRegistry(state_root=tmp_path / "registry")
        await registry.create(
            SessionRecord("parent-session", status=SessionStatus.RUNNING)
        )
        record = await registry.get("parent-session")
        assert record is not None
        await record.admission_lock.acquire()
        cancellation = asyncio.create_task(
            registry.close_admission_for_parent_cancellation(
                "parent-session",
                work_item_id="work-a",
                reason="cancel during admission",
                child_recovery_refs=[],
            )
        )
        try:
            await asyncio.sleep(0)
            assert not cancellation.done()
        finally:
            record.admission_lock.release()
        await cancellation
        assert record.admission_closed is True

    asyncio_run(scenario())


def test_parent_cancellation_waits_for_cross_registry_turn_fence(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    state_root = tmp_path / "registry"
    workspace = tmp_path / "workspace"
    admitting = SessionRegistry(state_root=state_root)
    asyncio_run(
        admitting.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    cancelling = SessionRegistry(state_root=state_root)
    cancellation_errors: list[BaseException] = []

    def cancel_parent() -> None:
        try:
            asyncio_run(
                cancelling.close_admission_for_parent_cancellation(
                    "parent-session",
                    work_item_id="work-a",
                    reason="cancel during cross-registry admission",
                    child_recovery_refs=[],
                )
            )
        except BaseException as error:
            cancellation_errors.append(error)

    cancellation_thread = threading.Thread(target=cancel_parent)

    async def hold_admission_fence() -> None:
        async with admitting.fence_parent_turn_admission("parent-session"):
            cancellation_thread.start()
            await asyncio.sleep(0.05)
            assert cancellation_thread.is_alive()

    asyncio_run(hold_admission_fence())
    cancellation_thread.join(timeout=2)

    assert not cancellation_thread.is_alive()
    assert cancellation_errors == []
    assert await_record(cancelling, "parent-session").admission_closed is True

def test_cross_registry_cancellation_does_not_block_event_loop(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    async def scenario() -> None:
        state_root = tmp_path / "registry"
        workspace = tmp_path / "workspace"
        admitting = SessionRegistry(state_root=state_root)
        await admitting.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
        cancelling = SessionRegistry(state_root=state_root)
        async with admitting.fence_parent_turn_admission("parent-session"):
            cancellation = asyncio.create_task(
                cancelling.close_admission_for_parent_cancellation(
                    "parent-session",
                    work_item_id="work-a",
                    reason="cancel during same-loop admission",
                    child_recovery_refs=[],
                )
            )
            await asyncio.sleep(0.05)
            assert not cancellation.done()
        await asyncio.wait_for(cancellation, timeout=2)

    asyncio_run(scenario())




def test_generic_metadata_update_preserves_newer_parent_cancellation(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    state_root = tmp_path / "registry"
    owner = SessionRegistry(state_root=state_root)
    asyncio_run(
        owner.create(
            SessionRecord("parent-session", status=SessionStatus.RUNNING)
        )
    )
    asyncio_run(
        owner.close_admission_for_parent_cancellation(
            "parent-session",
            work_item_id="work-a",
            reason="cancel a",
            child_recovery_refs=[],
        )
    )
    stale_writer = SessionRegistry(state_root=state_root)
    stale_metadata = dict(
        await_record(stale_writer, "parent-session").metadata
    )
    asyncio_run(
        owner.close_admission_for_parent_cancellation(
            "parent-session",
            work_item_id="work-b",
            reason="cancel b",
            child_recovery_refs=[],
        )
    )

    asyncio_run(
        stale_writer.update_metadata(
            "parent-session",
            metadata={**stale_metadata, "runner_note": "persisted"},
        )
    )

    record = await_record(
        SessionRegistry(state_root=state_root), "parent-session"
    )
    marker = record.metadata["durable_parent_cancellation"]
    assert [request["work_item_id"] for request in marker["requests"]] == [
        "work-a",
        "work-b",
    ]



def test_cancel_tree_persists_marker_before_parent_reconciliation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    product = Session.start(_lock(), "parent task", session_id="parent-session")
    create_session(workspace, product)
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent_work = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        repository=repository,
        resume_policy=ResumePolicy("restart"),
    )
    parent_work.acquire_lease("worker", lease_id="lease")
    parent_work.start_attempt(
        "parent-session",
        lease_id="lease",
        attempt_id="attempt",
    )
    parent_work.pause("awaiting input", attempt_id="attempt")
    mutate_session(
        workspace,
        "parent-session",
        lambda current: current.complete("external completion"),
    )
    registry = SessionRegistry(state_root=tmp_path / "registry")
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )

    with pytest.raises(ChildError, match="cannot reconcile Work Item"):
        factory.cancel_tree(
            parent_session_id="parent-session",
            parent_work_item_id="parent-work",
        )

    record = await_record(registry, "parent-session")
    assert record is not None and record.admission_closed
    assert record.metadata["durable_parent_cancellation"] == {
        "requests": [
            {
                "work_item_id": "parent-work",
                "reason": "operator request",
                "child_recovery_refs": [],
            }
        ]
    }


def test_cancel_tree_clears_uncommitted_child_settlement_after_parent_terminal(
    tmp_path: Path,
) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[RetryAdapter()],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec("retry-adapter", "reserved before parent terminal"),
    )
    state = factory._record_state(activation.child_session_id)
    prepared = factory.prepare_result(
        activation.child_session_id,
        expected_revision=state.revision,
        result=b"prepared",
        attempt_id=state.attempt_id,
    )
    factory._cas(
        prepared,
        settlement={
            "outcome": "completed",
            "result_refs": list(prepared.result_refs),
        },
    )
    mutate_session(
        workspace,
        "parent-session",
        lambda current: current.complete("parent completed"),
    )

    settled = factory.cancel_tree(
        parent_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
    )

    assert parent_work.read_model.status == "completed"
    assert len(settled) == 1
    assert settled[0].status == "canceled"
    assert settled[0].settlement is None



def test_terminal_cancel_adoption_persists_target_before_acknowledgement(
    tmp_path: Path,
) -> None:
    persisted_before_acknowledgement: list[bool] = []

    class CompletedAdapter(RetryAdapter):
        family = "completed-during-cancel-adoption"

        def start(self, activation, spec):
            self.starts += 1
            return ExecutionTarget(
                activation.execution_target_ref,
                metadata={"phase": "accepted"},
            )

        def observe(self, target):
            target["metadata"]["phase"] = "completed"
            return "completed"

        def prepare_result(self, target, spec):
            return b"completed during cancellation"

        def acknowledge_result(self, target):
            retained = factory._record_state(activation.child_session_id)
            persisted_before_acknowledgement.append(
                retained.execution_target["metadata"]["phase"] == "completed"
            )

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    adapter = CompletedAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "completed during cancel adoption"),
    )

    settled = factory._adopt_terminal_target_after_cancel(
        factory._record_state(activation.child_session_id)
    )

    assert persisted_before_acknowledgement == [True]
    assert settled.status == "completed"
    assert settled.terminal_count == 1


def test_failed_observation_after_cancellation_settles_as_canceled(
    tmp_path: Path,
) -> None:
    class FailedDuringCancelAdapter(RetryAdapter):
        family = "failed-during-cancel-adoption"

        def cancel(self, target):
            return False

        def observe(self, target):
            return "failed"

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    adapter = FailedDuringCancelAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "failed after cancellation"),
    )
    state = factory._record_state(activation.child_session_id)
    state = factory._cas(
        state,
        status="cancel_requested",
        cancellation_requested=True,
        cancellation_reason="operator request",
    )

    settled = factory.reconcile(state.recovery_ref)

    assert settled.status == "canceled"
    assert settled.terminal_outcome == "canceled"
    assert settled.settlement is None
    assert settled.terminal_count == 1


def test_reconcile_cancels_late_completed_result_after_parent_terminal(
    tmp_path: Path,
) -> None:
    class CompletedAdapter(RetryAdapter):
        family = "completed-after-parent"

        def observe(self, target):
            return "completed"

        def prepare_result(self, target, spec):
            return b"completed too late"

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    adapter = CompletedAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "late completion"),
    )
    mutate_session(
        workspace,
        "parent-session",
        lambda current: current.complete("parent completed first"),
    )
    parent_work.complete(
        "parent completed first",
        attempt_id=parent_work.read_model.current_attempt.attempt_id,
    )

    recovered = factory.reconcile(activation.recovery_ref)

    assert recovered.status == "canceled"
    assert recovered.terminal_count == 1
    assert recovered.settlement is None


def test_child_completion_joins_nonterminal_waiting_parent(
    tmp_path: Path,
) -> None:
    class CompletedAdapter(RetryAdapter):
        family = "completed-while-parent-waits"

        def observe(self, target):
            return "completed"

        def prepare_result(self, target, spec):
            return b"completed while parent waits"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_product = Session.start(
        _lock(), "parent task", session_id="parent-session"
    )
    create_session(workspace, parent_product)
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent_work = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        repository=repository,
        resume_policy=ResumePolicy("restart"),
    )
    parent_work.acquire_lease("parent-worker", lease_id="parent-lease")
    parent_work.start_attempt(
        "parent-session",
        lease_id="parent-lease",
        attempt_id="parent-attempt",
    )
    registry = SessionRegistry(state_root=tmp_path / "registry")
    adapter = CompletedAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "complete while waiting"),
    )
    parent_work.wait(
        [activation.recovery_ref],
        "waiting for child",
        attempt_id="parent-attempt",
    )
    mutate_session(
        workspace,
        "parent-session",
        lambda current: current.request_approval(
            "approval-1", "continue parent"
        ),
    )

    recovered = factory.reconcile(activation.recovery_ref)

    assert recovered.status == "completed"
    assert parent_work.read_model.status == "waiting"
    parent_product, _ = load_session(workspace, "parent-session")
    assert parent_product.read_model.status == "awaiting_approval"

def test_parent_cancellation_refreshes_cross_process_descendants(
    tmp_path: Path,
) -> None:
    workspace, repository, parent_work, initial_registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    first = DurableChildFactory(
        workspace,
        registry=initial_registry,
        repository=repository,
        adapters=[adapter],
    ).start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "nested parent"),
    )
    stale_registry = SessionRegistry(state_root=tmp_path / "registry")
    assert len(await_records(stale_registry)) == 1

    first_work = WorkItem.restore(repository, first.child_work_item_id)
    writer_registry = SessionRegistry(state_root=tmp_path / "registry")
    second = DurableChildFactory(
        workspace,
        registry=writer_registry,
        repository=repository,
        adapters=[adapter],
    ).start(
        parent_session_id=first.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=first_work.read_model.work_item_id,
        spec=_spec(adapter.family, "late nested child"),
    )

    with pytest.raises(
        RuntimeError,
        match="retained child set changed during parent cancellation",
    ):
        asyncio_run(
            stale_registry.close_admission_for_parent_cancellations(
                first.child_session_id,
                requests=(
                    {
                        "work_item_id": first.child_work_item_id,
                        "reason": "operator request",
                        "child_recovery_refs": (),
                    },
                ),
                expected_child_recovery_refs=(),
            )
        )

    parent_record = await_record(stale_registry, first.child_session_id)
    assert parent_record.admission_closed is False
    assert (
        parent_record.metadata.get("durable_parent_cancellation")
        is None
    )
    assert second.recovery_ref in {
        record.metadata["durable_child"]["recovery_ref"]
        for record in await_records(stale_registry)
        if isinstance(record.metadata.get("durable_child"), dict)
    }

def test_nested_child_start_rejects_a_noncanonical_root_session(
    tmp_path: Path,
) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    first = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "nested parent"),
    )
    first_work = WorkItem.restore(repository, first.child_work_item_id)

    with pytest.raises(
        ChildError,
        match="root Session does not match retained parent lineage",
    ):
        factory.start(
            parent_session_id=first.child_session_id,
            root_session_id=first.child_session_id,
            parent_work_item_id=first_work.read_model.work_item_id,
            spec=_spec(adapter.family, "invalid nested root"),
        )


def test_parent_cancellation_fences_concurrent_child_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    asyncio_run(
        registry.create(
            SessionRecord(
                "parent-session",
                status=SessionStatus.RUNNING,
                metadata={"workspace": str(workspace)},
            )
        )
    )
    entered_refresh = threading.Event()
    release_refresh = threading.Event()
    original_refresh = registry._refresh_records_from_disk_locked

    async def pause_refresh() -> None:
        entered_refresh.set()
        if not await asyncio.to_thread(release_refresh.wait, 2):
            raise RuntimeError("test did not release cancellation refresh")
        await original_refresh()

    monkeypatch.setattr(registry, "_refresh_records_from_disk_locked", pause_refresh)
    cancellation_errors: list[BaseException] = []

    def cancel_parent() -> None:
        try:
            asyncio_run(
                registry.close_admission_for_parent_cancellations(
                    "parent-session",
                    requests=(
                        {
                            "work_item_id": parent_work.read_model.work_item_id,
                            "reason": "operator request",
                            "child_recovery_refs": (),
                        },
                    ),
                    expected_child_recovery_refs=(),
                )
            )
        except BaseException as error:
            cancellation_errors.append(error)

    cancellation_thread = threading.Thread(target=cancel_parent)
    cancellation_thread.start()
    assert entered_refresh.wait(timeout=2)

    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    start_errors: list[BaseException] = []

    def start_child() -> None:
        try:
            factory.start(
                parent_session_id="parent-session",
                root_session_id="parent-session",
                parent_work_item_id=parent_work.read_model.work_item_id,
                spec=_spec(adapter.family, "concurrent child"),
            )
        except BaseException as error:
            start_errors.append(error)

    start_thread = threading.Thread(target=start_child)
    start_thread.start()
    time.sleep(0.05)
    assert start_thread.is_alive()
    release_refresh.set()
    cancellation_thread.join(timeout=2)
    start_thread.join(timeout=2)

    assert not cancellation_thread.is_alive()
    assert not start_thread.is_alive()
    assert cancellation_errors == []
    assert len(start_errors) == 1
    assert isinstance(start_errors[0], ChildError)
    assert "cancellation is pending" in str(start_errors[0])
    assert adapter.starts == 0
    assert factory.child_states(
        parent_work_item_id=parent_work.read_model.work_item_id
    ) == ()



def test_reconciler_persists_complete_nested_intent_before_signaling(
    tmp_path: Path,
) -> None:
    class CrashAfterBatchRegistry(SessionRegistry):
        async def close_admission_for_parent_cancellations(
            self,
            session_id: str,
            *,
            requests,
            expected_child_recovery_refs=None,
            expected_child_owner=None,
        ) -> None:
            await super().close_admission_for_parent_cancellations(
                session_id,
                requests=requests,
                expected_child_recovery_refs=expected_child_recovery_refs,
                expected_child_owner=expected_child_owner,
            )
            raise RuntimeError("simulated crash after durable batch")

    workspace, repository, parent_work, _ = _running_parent(tmp_path)
    registry = CrashAfterBatchRegistry(state_root=tmp_path / "registry")
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    first = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "nested parent"),
    )
    first_work = WorkItem.restore(repository, first.child_work_item_id)
    second = factory.start(
        parent_session_id=first.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=first_work.read_model.work_item_id,
        spec=_spec(adapter.family, "nested child"),
    )
    second_work = WorkItem.restore(repository, second.child_work_item_id)
    third = factory.start(
        parent_session_id=second.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=second_work.read_model.work_item_id,
        spec=_spec(adapter.family, "nested grandchild"),
    )
    nonpropagating = factory.start(
        parent_session_id=first.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=first_work.read_model.work_item_id,
        spec=ChildSpec(
            "nonpropagating nested child",
            "nested child task",
            _lock(),
            "child-worker",
            adapter.family,
            cancellation_policy=CancellationPolicy(
                propagate_to_children=False
            ),
        ),
    )
    nonpropagating_work = WorkItem.restore(
        repository, nonpropagating.child_work_item_id
    )
    excluded = factory.start(
        parent_session_id=nonpropagating.child_session_id,
        root_session_id="parent-session",
        parent_work_item_id=nonpropagating_work.read_model.work_item_id,
        spec=_spec(adapter.family, "excluded nested grandchild"),
    )
    reconciler = DurableChildReconciler(
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        asyncio_run(reconciler.cancel_tree(first.child_session_id))

    retained_parent = await_record(registry, first.child_session_id)
    requests = retained_parent.metadata["durable_parent_cancellation"]["requests"]
    assert requests == [
        {
            "work_item_id": first.child_work_item_id,
            "reason": "operator request",
            "child_recovery_refs": sorted(
                [
                    second.recovery_ref,
                    third.recovery_ref,
                    nonpropagating.recovery_ref,
                ]
            ),
        }
    ]
    assert retained_parent.admission_closed
    assert not factory._record_state(second.child_session_id).cancellation_requested
    assert not factory._record_state(third.child_session_id).cancellation_requested
    assert not factory._record_state(excluded.child_session_id).cancellation_requested


def test_reconciler_cancellation_supports_mixed_unavailable_families(
    tmp_path: Path,
) -> None:
    class FirstAdapter(RetryAdapter):
        family = "unavailable-first"

    class SecondAdapter(RetryAdapter):
        family = "unavailable-second"

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[FirstAdapter(), SecondAdapter()],
    )
    first = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec("unavailable-first", "first"),
    )
    second = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec("unavailable-second", "second"),
    )
    reconciler = DurableChildReconciler(
        registry=registry,
        repository=repository,
    )

    states = asyncio_run(reconciler.cancel_tree("parent-session"))

    assert {state.child_session_id for state in states} == {
        first.child_session_id,
        second.child_session_id,
    }
    assert all(state.cancellation_requested for state in states)


def test_reconciler_cancels_process_child_when_ray_is_representative(
    tmp_path: Path,
) -> None:
    class RayLikeAdapter(RetryAdapter):
        family = RayJobAdapter.family

        def observe(self, target):
            return "running"

        def cancel(self, target):
            return True

    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    ray_adapter = RayLikeAdapter()
    process_adapter = ProcessExecutionAdapter(
        command=("/bin/sh", "-c", "sleep 30")
    )
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[ray_adapter, process_adapter],
    )
    ray_child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(ray_adapter.family, "ray child"),
    )
    process_child = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(process_adapter.family, "process child"),
    )
    process_target = factory._record_state(
        process_child.child_session_id
    ).execution_target
    reconciler = DurableChildReconciler(
        registry=registry,
        repository=repository,
        adapters=[RayLikeAdapter()],
        adapter_factories=[ProcessExecutionAdapter],
    )

    try:
        states = asyncio_run(reconciler.cancel_tree("parent-session"))
        assert {state.child_session_id for state in states} == {
            ray_child.child_session_id,
            process_child.child_session_id,
        }
        assert all(state.terminal_count == 1 for state in states)
    finally:
        process_adapter.cancel(process_target)

def test_reconciler_relaunches_process_child_from_its_retained_command(
    tmp_path: Path,
) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    command_a = ("/bin/sh", "-c", "printf a >> child-a.txt; exit 1")
    command_b = ("/bin/sh", "-c", "printf b >> child-b.txt; exit 1")
    factory_a = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[ProcessExecutionAdapter(command=command_a)],
    )
    first = factory_a.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=ChildSpec(
            "first process child",
            "first process task",
            _lock(),
            "child-worker",
            ProcessExecutionAdapter.family,
            retry_policy=RetryPolicy(2, True),
        ),
    )
    factory_b = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[ProcessExecutionAdapter(command=command_a)],
    )
    second = factory_b.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=ChildSpec(
            "second process child",
            "second process task",
            _lock(),
            "child-worker",
            ProcessExecutionAdapter.family,
            retry_policy=RetryPolicy(2, True),
            adapter_config={"command": list(command_b)},
        ),
    )

    deadline = time.monotonic() + 2.0
    marker = workspace / "child-b.txt"
    process_adapter = factory_b.adapters[ProcessExecutionAdapter.family]
    target = factory_b._record_state(second.child_session_id).execution_target
    observed = process_adapter.observe(target)
    while (
        (not marker.exists() or marker.read_text() != "b" or observed != "absent")
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
        observed = process_adapter.observe(target)
    assert observed == "absent"

    reconciler = DurableChildReconciler(
        registry=registry,
        repository=repository,
        adapter_factories=[ProcessExecutionAdapter],
    )
    asyncio_run(reconciler(second.recovery_ref))

    deadline = time.monotonic() + 2.0
    while marker.read_text() != "bb" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.read_text() == "bb"


def test_settlement_reservation_clears_when_child_attempt_is_paused(
    tmp_path: Path,
) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        spec=_spec(adapter.family, "paused settlement"),
    )
    prepared = factory.prepare_result(
        activation.child_session_id,
        result=b"prepared",
        expected_revision=factory._record_state(
            activation.child_session_id
        ).revision,
        attempt_id=activation.attempt_id,
    )
    child_work = WorkItem.restore(repository, activation.child_work_item_id)
    child_work.pause("awaiting input", attempt_id=activation.attempt_id)

    with pytest.raises(ChildError, match="no active attempt"):
        factory.settle(
            activation.child_session_id,
            expected_revision=prepared.revision,
            outcome="completed",
            result_refs=prepared.result_refs,
            attempt_id=activation.attempt_id,
        )

    recovered = factory._record_state(activation.child_session_id)
    assert recovered.settlement is None
    assert recovered.terminal_count == 0


class WorkflowAdapter:
    family = "workflow-adapter"

    def __init__(self) -> None:
        self.started: list[str | None] = []

    def start(self, activation, spec):
        self.started.append(spec.workflow_step_id)
        return ExecutionTarget(activation.execution_target_ref)

    def observe(self, target):
        return "running"

    def cancel(self, target):
        return None

    def prepare_result(self, target, spec):
        return b"workflow result"


def _workflow_definition(*, verify_title: str = "verify") -> WorkflowDefinition:
    return WorkflowDefinition(
        (
            WorkflowStep("inspect", _spec("workflow-adapter", "inspect")),
            WorkflowStep(
                "verify",
                _spec("workflow-adapter", verify_title),
                depends_on=("inspect",),
            ),
        )
    )


def _hold_replayed_workflow_decision(
    workspace: str,
    registry_root: str,
    repository_path: str,
    marker_path: str,
) -> None:
    repository = WorkItemRepository(repository_path)
    factory = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=registry_root),
        repository=repository,
        adapters=[UnavailableChildAdapter("workflow-adapter")],
    )
    decision = ReplayableWorkflowController(
        factory,
        workflow_id="research",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    ).decision()
    Path(marker_path).write_text(
        json.dumps(decision.as_dict(), sort_keys=True),
        encoding="utf-8",
    )
    while True:
        time.sleep(1)


@pytest.mark.parametrize(
    ("parent_outcome", "expected_action"),
    (("failed", "fail"), ("canceled", "cancel")),
)
def test_workflow_decision_honors_terminal_parent_outcome(
    tmp_path: Path,
    parent_outcome: str,
    expected_action: str,
) -> None:
    workspace, repository, parent_work, registry = _running_parent(tmp_path)
    if parent_outcome == "failed":
        parent_work.fail("parent_failed", "workflow parent failed")
    else:
        parent_work.cancel("operator", "workflow parent canceled")
    decision = ReplayableWorkflowController(
        DurableChildFactory(
            workspace,
            registry=registry,
            repository=repository,
            adapters=[WorkflowAdapter()],
        ),
        workflow_id="terminal-parent",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent_work.read_model.work_item_id,
        definition=_workflow_definition(),
    ).decision()

    assert decision.action == expected_action


def test_workflow_decision_replays_across_controller_restart(tmp_path: Path) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    adapter = WorkflowAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    definition = _workflow_definition()
    controller = ReplayableWorkflowController(
        factory,
        workflow_id="research",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=definition,
    )

    first = controller.advance()
    assert first.action == "wait"
    inspect = factory.child_states(parent_work_item_id="parent-work")[0]
    projected = project_workflow_decision(
        definition,
        workflow_id="research",
        parent_work_item_events=repository.read("parent-work"),
        children=((inspect, repository.read(inspect.child_work_item_id)),),
    )
    assert projected.projector_version == WORKFLOW_PROJECTOR_VERSION
    assert projected.value.action == "wait"
    assert first == projected.value
    assert tuple(source.stream for source in projected.source.components) == (
        "work_item:parent-work",
        f"child_state:{inspect.child_session_id}",
        f"work_item:{inspect.child_work_item_id}",
    )
    assert tuple(
        (cursor.stream, cursor.sequence) for cursor in projected.as_of
    )[1] == (f"child_state:{inspect.child_session_id}", inspect.revision + 1)
    prepared = factory.prepare_result(
        inspect.child_session_id,
        expected_revision=inspect.revision,
        result=b"inspect result",
        attempt_id=inspect.attempt_id,
    )
    factory.settle(
        inspect.child_session_id,
        expected_revision=prepared.revision,
        outcome="completed",
        attempt_id=prepared.attempt_id,
    )

    expected_after_kill = controller.decision()
    assert expected_after_kill.action == "start"
    assert expected_after_kill.ready_step_ids == ("verify",)
    event_counts = {
        "parent-work": len(repository.read("parent-work")),
        inspect.child_work_item_id: len(repository.read(inspect.child_work_item_id)),
    }
    marker = tmp_path / "controller-decision.json"
    process = multiprocessing.get_context("spawn").Process(
        target=_hold_replayed_workflow_decision,
        args=(
            str(workspace),
            str(tmp_path / "registry"),
            str(tmp_path / "work-items.jsonl"),
            str(marker),
        ),
    )
    process.start()
    deadline = time.monotonic() + 10
    while (
        not marker.exists()
        and process.is_alive()
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    try:
        assert marker.exists(), f"controller process exited with {process.exitcode}"
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    assert process.exitcode not in {None, 0}
    assert json.loads(marker.read_text(encoding="utf-8")) == (
        expected_after_kill.as_dict()
    )
    after_kill_repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    assert {
        work_item_id: len(after_kill_repository.read(work_item_id))
        for work_item_id in event_counts
    } == event_counts

    restarted_factory = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=WorkItemRepository(tmp_path / "work-items.jsonl"),
        adapters=[adapter],
    )
    restarted = ReplayableWorkflowController(
        restarted_factory,
        workflow_id="research",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=definition,
    )
    assert restarted.decision() == expected_after_kill
    second = restarted.advance()
    verify = {
        state.child_spec["workflow_step_id"]: state
        for state in restarted_factory.child_states(parent_work_item_id="parent-work")
    }["verify"]
    prepared_verify = restarted_factory.prepare_result(
        verify.child_session_id,
        expected_revision=verify.revision,
        result=b"verify result",
        attempt_id=verify.attempt_id,
    )
    restarted_factory.settle(
        verify.child_session_id,
        expected_revision=prepared_verify.revision,
        outcome="completed",
        attempt_id=prepared_verify.attempt_id,
    )

    terminal = ReplayableWorkflowController(
        restarted_factory,
        workflow_id="research",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=definition,
    ).decision()
    assert terminal.action == "complete"
    assert terminal.completed_step_ids == ("inspect", "verify")


@pytest.mark.parametrize(
    "changed_child",
    (
        _spec("workflow-adapter", "changed verify"),
        replace(
            _spec("workflow-adapter", "verify"),
            adapter_config={"command": ["/bin/false"]},
        ),
    ),
    ids=("title", "execution-config"),
)
def test_workflow_rejects_definition_drift_after_child_start(
    tmp_path: Path, changed_child: ChildSpec,
) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[WorkflowAdapter()],
    )
    ReplayableWorkflowController(
        factory,
        workflow_id="research",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    ).advance()
    definition = _workflow_definition()

    changed = ReplayableWorkflowController(
        factory,
        workflow_id="research",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=WorkflowDefinition(
            (definition.steps[0], replace(definition.steps[1], child=changed_child))
        ),
    )
    with pytest.raises(ChildError, match="definition does not match"):
        changed.decision()


@pytest.mark.parametrize(
    ("outcome", "expected_action"),
    (("failed", "fail"), ("canceled", "cancel")),
)
def test_workflow_rule_table_terminal_outcome_precedence(
    tmp_path: Path,
    outcome: str,
    expected_action: str,
) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[WorkflowAdapter()],
    )
    controller = ReplayableWorkflowController(
        factory,
        workflow_id="terminal-rule",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    )
    controller.advance()
    state = factory.child_states(parent_work_item_id="parent-work")[0]
    factory.settle(
        state.child_session_id,
        expected_revision=state.revision,
        outcome=outcome,
        attempt_id=state.attempt_id,
    )

    decision = controller.decision()
    assert decision.action == expected_action
    assert decision.ready_step_ids == ()
    assert decision.blocked_step_ids == ("verify",)


def test_workflow_waits_while_parent_is_ready(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    create_session(
        workspace,
        Session.start(_lock(), "parent task", session_id="parent-session"),
    )
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        repository=repository,
    )
    registry = SessionRegistry(state_root=tmp_path / "registry")
    adapter = WorkflowAdapter()
    controller = ReplayableWorkflowController(
        DurableChildFactory(
            workspace,
            registry=registry,
            repository=repository,
            adapters=[adapter],
        ),
        workflow_id="ready-parent",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    )

    decision = controller.advance()

    assert decision.action == "wait"
    assert decision.ready_step_ids == ("inspect",)
    assert adapter.started == []


def test_workflow_concurrent_advance_starts_each_step_once(tmp_path: Path) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    adapter = WorkflowAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    controllers = [
        ReplayableWorkflowController(
            factory,
            workflow_id="concurrent",
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id="parent-work",
            definition=_workflow_definition(),
        )
        for _ in range(2)
    ]
    results = []

    def advance(controller: ReplayableWorkflowController) -> None:
        results.append(controller.advance())

    threads = [
        threading.Thread(target=advance, args=(controller,))
        for controller in controllers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results[0] == results[1]
    assert adapter.started == ["inspect"]
    states = factory.child_states(parent_work_item_id="parent-work")
    assert len(states) == 1
    assert states[0].child_spec["workflow_step_id"] == "inspect"


def test_workflow_definition_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        WorkflowDefinition(
            (
                WorkflowStep(
                    "a",
                    _spec("workflow-adapter", "a"),
                    depends_on=("b",),
                ),
                WorkflowStep(
                    "b",
                    _spec("workflow-adapter", "b"),
                    depends_on=("a",),
                ),
            )
        )


def test_child_state_rejects_malformed_workflow_binding(tmp_path: Path) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[WorkflowAdapter()],
    )
    ReplayableWorkflowController(
        factory,
        workflow_id="retained-validation",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    ).advance()
    retained = factory.child_states(parent_work_item_id="parent-work")[0].retained()
    retained["child_spec"]["workflow_definition_hash"] = "not-a-digest"

    with pytest.raises(ValueError, match="workflow identity"):
        ChildState.from_retained(retained)


def test_workflow_reconciles_retained_predelegation_child_without_stream(
    tmp_path: Path,
) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[WorkflowAdapter()],
    )
    definition = _workflow_definition()
    tagged = replace(
        definition.step("inspect").child,
        workflow_id="startup-replay",
        workflow_step_id="inspect",
        workflow_definition_hash=definition.identity("startup-replay"),
    )
    initial = ChildState(
        "child-startup-replay",
        "work-startup-replay",
        "parent-session",
        "parent-session",
        "parent-work",
        "attempt-startup-replay",
        "child://child-startup-replay/attempt/attempt-startup-replay",
        "reserved:child-startup-replay",
        "workflow-adapter",
        "starting",
        0,
        startup_phase="recorded",
        child_spec={
            **tagged.retained(),
            "task_artifact_ref": factory.artifacts.put(
                tagged.task.encode(),
                media_type="text/plain; charset=utf-8",
            ).as_dict(),
            "task_artifact_store": str(factory.artifacts._root),
            "work_item_repository_path": str(factory._repository_path),
        },
        execution_target={"ref": "reserved:child-startup-replay"},
    )
    factory._create_record(initial)
    controller = ReplayableWorkflowController(
        factory,
        workflow_id="startup-replay",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=definition,
    )
    parent_event_count = len(repository.read("parent-work"))

    before_repair = controller.decision()
    assert before_repair.action == "wait"
    assert before_repair.active_step_ids == ("inspect",)
    before_projected = project_workflow_decision(
        definition,
        workflow_id="startup-replay",
        parent_work_item_events=repository.read("parent-work"),
        children=((initial, ()),),
    )
    before_child_cursor = next(
        cursor
        for cursor in before_projected.as_of
        if cursor.stream == "child_state:child-startup-replay"
    )
    repaired = controller.advance()

    assert repaired.action == "fail"
    assert repaired.failed_step_ids == ("inspect",)
    assert repaired.blocked_step_ids == ("verify",)
    assert repository.read("work-startup-replay") == ()
    assert len(repository.read("parent-work")) == parent_event_count
    repaired_state = factory.child_states(parent_work_item_id="parent-work")[0]
    after_projected = project_workflow_decision(
        definition,
        workflow_id="startup-replay",
        parent_work_item_events=repository.read("parent-work"),
        children=((repaired_state, ()),),
    )
    after_child_cursor = next(
        cursor
        for cursor in after_projected.as_of
        if cursor.stream == "child_state:child-startup-replay"
    )
    assert after_projected.value.action == "fail"
    assert after_child_cursor.sequence > before_child_cursor.sequence


def test_workflow_waits_for_active_step_before_starting_another_root(
    tmp_path: Path,
) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[WorkflowAdapter()],
    )
    definition = WorkflowDefinition(
        (
            WorkflowStep("b", _spec("workflow-adapter", "root b")),
            WorkflowStep("a", _spec("workflow-adapter", "root a")),
        )
    )
    controller = ReplayableWorkflowController(
        factory,
        workflow_id="serialized-roots",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=definition,
    )

    decision = controller.advance()

    assert decision.action == "wait"
    assert decision.active_step_ids == ("a",)
    assert decision.ready_step_ids == ("b",)
    states = factory.child_states(parent_work_item_id="parent-work")
    assert len(states) == 1
    assert states[0].child_spec["workflow_step_id"] == "a"


def test_workflow_lock_path_exists_with_external_artifact_store(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    parent = WorkItem.create(
        "parent work",
        work_item_id="parent-work",
        repository=repository,
    )
    parent.acquire_lease("parent-worker", lease_id="parent-lease")
    parent.start_attempt(
        "parent-session",
        lease_id="parent-lease",
        attempt_id="parent-attempt",
    )
    factory = DurableChildFactory(
        workspace,
        registry=SessionRegistry(state_root=tmp_path / "registry"),
        repository=repository,
        adapters=[WorkflowAdapter()],
        artifact_store=ArtifactStore(tmp_path / "external-artifacts"),
    )
    controller = ReplayableWorkflowController(
        factory,
        workflow_id="external-artifacts",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    )

    assert controller.decision().action == "start"
    assert (workspace / ".breadboard").is_dir()


def test_workflow_advance_repairs_terminal_child_owner_gap(
    tmp_path: Path,
) -> None:
    workspace, repository, _parent, registry = _running_parent(tmp_path)
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[WorkflowAdapter()],
    )
    controller = ReplayableWorkflowController(
        factory,
        workflow_id="terminal-repair",
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id="parent-work",
        definition=_workflow_definition(),
    )
    controller.advance()
    state = factory.child_states(parent_work_item_id="parent-work")[0]
    factory._cas(
        state,
        status="failed",
        terminal_outcome="failed",
        terminal_count=1,
        joined=True,
    )

    with pytest.raises(ChildError, match="diverged"):
        controller.decision()
    repaired = controller.advance()

    assert repaired.action == "fail"
    assert repaired.failed_step_ids == ("inspect",)
    child = WorkItem.restore(repository, state.child_work_item_id)
    assert child.read_model.status == "failed"
    parent = WorkItem.restore(repository, "parent-work")
    joined = [
        event
        for event in parent.events
        if event.kind == "child.joined"
        and event.payload["child_work_item_id"] == state.child_work_item_id
    ]
    assert len(joined) == 1


def test_workflow_definition_identity_canonicalizes_graph_order() -> None:
    first = WorkflowDefinition(
        (
            WorkflowStep("a", _spec("workflow-adapter", "a")),
            WorkflowStep("b", _spec("workflow-adapter", "b")),
            WorkflowStep(
                "c",
                _spec("workflow-adapter", "c"),
                depends_on=("b", "a"),
            ),
        )
    )
    second = WorkflowDefinition(
        (
            WorkflowStep(
                "c",
                _spec("workflow-adapter", "c"),
                depends_on=("a", "b"),
            ),
            WorkflowStep("b", _spec("workflow-adapter", "b")),
            WorkflowStep("a", _spec("workflow-adapter", "a")),
        )
    )

    assert first.identity("canonical") == second.identity("canonical")


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin process ABI")
def test_darwin_process_start_token_uses_process_start_time() -> None:
    token = ProcessExecutionAdapter._process_start_token(os.getpid())

    assert isinstance(token, int)
    started_seconds = token // 1_000_000
    assert 946_684_800 <= started_seconds <= int(time.time())


def test_parent_stop_remains_pending_until_child_cancellation_settles(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    registry = SessionRegistry(state_root=tmp_path / "registry")
    parent = SessionRecord(
        "parent-session",
        status=SessionStatus.RUNNING,
        metadata={"workspace": str(tmp_path)},
    )
    child = SessionRecord(
        "child-session",
        status=SessionStatus.RUNNING,
        metadata={
            "workspace": str(tmp_path),
            "durable_child": {
                "parent_session_id": parent.session_id,
                "terminal_count": 0,
            },
        },
    )
    asyncio_run(registry.create(parent))
    asyncio_run(registry.create(child))

    class PendingReconciler:
        def __call__(self, recovery_ref: str):
            raise AssertionError(recovery_ref)

        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            raise AssertionError(recovery_ref)

        def cancel_tree(
            self,
            parent_session_id: str,
            *,
            reason: str = "operator request",
        ):
            assert parent_session_id == parent.session_id
            return ()

    service = SessionService(
        registry=registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=PendingReconciler(),
        durable_child_repository=WorkItemRepository(
            tmp_path / "work-items.jsonl"
        ),
    )

    with pytest.raises(RuntimeError, match="cancellation remains pending"):
        asyncio_run(service.stop_session(parent.session_id))

    assert await_record(registry, parent.session_id).status is SessionStatus.RUNNING


def test_parent_stop_honors_pending_authoritative_tree_result(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    registry = SessionRegistry(state_root=tmp_path / "registry")
    parent = SessionRecord(
        "parent-session",
        status=SessionStatus.RUNNING,
        metadata={"workspace": str(tmp_path)},
    )
    asyncio_run(registry.create(parent))

    class PendingState:
        terminal_count = 0

    class PendingReconciler:
        def __call__(self, recovery_ref: str):
            raise AssertionError(recovery_ref)

        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            raise AssertionError(recovery_ref)

        def cancel_tree(
            self,
            parent_session_id: str,
            *,
            reason: str = "operator request",
        ):
            assert parent_session_id == parent.session_id
            return (PendingState(),)

    service = SessionService(
        registry=registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=PendingReconciler(),
        durable_child_repository=WorkItemRepository(
            tmp_path / "work-items.jsonl"
        ),
    )

    with pytest.raises(RuntimeError, match="cancellation remains pending"):
        asyncio_run(service.stop_session(parent.session_id))

    assert await_record(registry, parent.session_id).status is SessionStatus.RUNNING



def test_reconciler_skips_process_factory_for_nonprocess_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedProcessAdapter:
        family = "execution-world-process"

        def __init__(self, *args, **kwargs) -> None:
            raise ChildError("process execution is unavailable")

    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(
        workspace,
        registry=registry,
        repository=repository,
        adapters=[adapter],
    )
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "nonprocess child"),
    )
    monkeypatch.setattr(
        children_module,
        "ProcessExecutionAdapter",
        UnsupportedProcessAdapter,
    )
    reconciler = DurableChildReconciler(
        registry=registry,
        repository=repository,
        adapters=[adapter],
        adapter_factories=(UnsupportedProcessAdapter,),
    )

    assert asyncio_run(reconciler(activation.recovery_ref)).child_session_id == (
        activation.child_session_id
    )


def test_direct_child_stop_remains_pending_until_adapter_confirms_exit(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord
    from breadboard_engine.api.cli_bridge.service import SessionService

    registry = SessionRegistry(state_root=tmp_path / "registry")
    record = SessionRecord(
        "child-session",
        status=SessionStatus.RUNNING,
        metadata={
            "workspace": str(tmp_path),
            "durable_child": {
                "recovery_ref": "child://child-session/attempt/attempt-1",
                "terminal_count": 0,
            },
        },
    )
    asyncio_run(registry.create(record))

    class PendingState:
        terminal_count = 0
        status = "cancel_requested"
        cancellation_requested = True

    class PendingReconciler:
        def __call__(self, recovery_ref: str):
            return PendingState()

        def cancel(self, recovery_ref: str, *, reason: str = "operator request"):
            return PendingState()

        def cancel_tree(
            self,
            parent_session_id: str,
            *,
            reason: str = "operator request",
        ):
            return ()

    service = SessionService(
        registry=registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=PendingReconciler(),
        durable_child_repository=WorkItemRepository(
            tmp_path / "work-items.jsonl"
        ),
    )

    with pytest.raises(RuntimeError, match="cancellation remains pending"):
        asyncio_run(service.delete_session(record.session_id))

    assert await_record(registry, record.session_id).status is SessionStatus.RUNNING


def test_registry_records_evicts_session_deleted_by_another_owner(
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionStatus
    from breadboard_engine.api.cli_bridge.registry.records import SessionRecord

    async def scenario() -> None:
        state_root = tmp_path / "registry"
        deleting = SessionRegistry(state_root=state_root)
        stale = SessionRegistry(state_root=state_root)
        await deleting.create(
            SessionRecord("ordinary-session", status=SessionStatus.RUNNING)
        )
        assert [record.session_id for record in await stale.records()] == [
            "ordinary-session"
        ]

        await deleting.delete("ordinary-session")

        assert await stale.records() == []

    asyncio_run(scenario())
