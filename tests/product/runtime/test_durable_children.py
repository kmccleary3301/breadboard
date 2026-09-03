from __future__ import annotations

import hashlib
import asyncio
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from breadboard.product.coordination.work_items import CancellationPolicy, WorkItem, WorkItemRepository
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import Session
from breadboard.product.runtime.children import (
    ChildActivation,
    ChildError,
    ChildSpec,
    ChildState,
    DurableChildReconciler,
    DurableChildFactory,
    ExpectedRevisionConflict,
    ExecutionTarget,
    LateResultRejected,
    PreparationRequired,
    ProcessExecutionAdapter,
    RayJobAdapter,
    UnavailableChildAdapter,
)
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime.session_store import create_session, load_session, mutate_session
from breadboard_engine.api.cli_bridge.registry.registry_impl import SessionRegistry

from breadboard.product.coordination.work_items import RetryPolicy
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


def test_process_adapter_reaps_natural_exit_and_reports_absent(tmp_path: Path) -> None:
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
    while adapter.observe(target.retained()) != "absent" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert adapter.observe(target.retained()) == "absent"
    assert target_ref not in adapter._processes


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
    assert adapter.observe(target.retained()) == "absent"


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
    restarted.cancel(recovered.child_session_id, expected_revision=recovered.revision)
    process = restarted_adapter._processes[recovered.execution_target_ref]
    assert process.wait(timeout=2) is not None
    assert restarted_adapter._pending_pid(recovered.execution_target_ref) is None


def test_process_cancel_escalates_sigkill_before_settlement(tmp_path: Path) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    workspace, repository, parent, registry = _running_parent(tmp_path, repository)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "trap '' TERM; sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    activation = factory.start(
        parent_session_id="parent-session",
        root_session_id="parent-session",
        parent_work_item_id=parent.read_model.work_item_id,
        spec=_spec(adapter.family, "sigterm ignore"),
    )
    state = factory._record_state(activation.child_session_id)
    canceled = factory.cancel(activation.child_session_id, expected_revision=state.revision)
    process = adapter._processes[activation.execution_target_ref]
    assert (canceled.status, canceled.terminal_outcome, canceled.terminal_count) == ("canceled", "canceled", 1)
    assert process.poll() is not None
    assert adapter.observe(canceled.execution_target) == "absent"
    assert adapter._pending_pid(activation.execution_target_ref) is None


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
    orchestrator.mark_job_completed(spawned.job.job_id, result_payload={"result": "inline", "result_bytes": "encoded"})

    rebuilt = MultiAgentOrchestrator(TeamConfig("job-replay-inline"), event_log=orchestrator.event_log)
    restored = rebuilt.job_manager.get(spawned.job.job_id)
    assert restored is not None
    assert restored.result_payload == {"result": "inline", "result_bytes": "encoded"}



def test_absent_target_honors_retry_policy_with_new_attempt_and_recovery_ref(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
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
def test_reconcile_cancellation_adopts_terminal_work_item(
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

    recovered = factory.reconcile(activation.recovery_ref)
    assert (recovered.status, recovered.terminal_outcome, recovered.terminal_count) == (
        outcome,
        outcome,
        1,
    )
    assert load_session(workspace, activation.child_session_id)[0].read_model.status == outcome


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
    target = factory._record_state(activation.child_session_id).execution_target
    artifact_payload = target["metadata"]["job"]["result_payload"]["artifact_ref"]
    artifact = ArtifactRef(
        str(artifact_payload["digest"]),
        int(artifact_payload["size_bytes"]),
        str(artifact_payload["media_type"]),
    )
    assert custom_store.read(artifact) == b"custom ray actor output"



def test_ray_cancel_signal_failure_retains_recovery_state() -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def cancel(self) -> None:
            raise RuntimeError("transient cancellation failure")

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-cancel-recovery"))
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
    )
    actor = Actor()
    adapter = RayJobAdapter(orchestrator)
    adapter._actors[spawned.job.job_id] = actor
    target = {
        "ref": f"job:{spawned.job.job_id}",
        "metadata": {"job": {"job_id": spawned.job.job_id}},
    }
    assert adapter.cancel(target) is False
    assert adapter._actors[spawned.job.job_id] is actor
    assert orchestrator.job_manager.get(spawned.job.job_id).state == "accepted"


def test_ray_observe_state_failure_stays_recovery_pending() -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class Actor:
        def get_state(self) -> str:
            raise RuntimeError("transient state inspection failure")

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-observe-recovery"))
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
    )
    actor = Actor()
    adapter = RayJobAdapter(orchestrator)
    adapter._actors[spawned.job.job_id] = actor
    target = {
        "ref": f"job:{spawned.job.job_id}",
        "metadata": {"job": {"job_id": spawned.job.job_id}},
    }
    assert adapter.observe(target) == "pending"
    assert adapter._actors[spawned.job.job_id] is actor
    assert orchestrator.job_manager.get(spawned.job.job_id).state == "accepted"

def test_ray_cancel_kills_actor_without_waiting_for_actor_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ray
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    class NeverReturningCancel:
        def __init__(self) -> None:
            self.calls = 0

        def remote(self):
            self.calls += 1
            raise AssertionError("actor cancellation RPC must not be awaited")

    class Actor:
        def __init__(self) -> None:
            self.cancel = NeverReturningCancel()

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-kill-cancel"))
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
    )
    actor = Actor()
    adapter = RayJobAdapter(orchestrator)
    adapter._actors[spawned.job.job_id] = actor
    kills: list[tuple[object, bool]] = []
    monkeypatch.setattr(ray, "kill", lambda handle, no_restart: kills.append((handle, no_restart)))
    target = {
        "ref": f"job:{spawned.job.job_id}",
        "metadata": {"job": {"job_id": spawned.job.job_id}},
    }
    adapter.cancel(target)
    assert kills == [(actor, True)]
    assert actor.cancel.calls == 0
    assert orchestrator.job_manager.get(spawned.job.job_id).state == "killed"
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
    monkeypatch.setattr(first_adapter, "_lookup_actor", lambda job_id: actors.get(job_id))
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
    monkeypatch.setattr(second_adapter, "_lookup_actor", lambda job_id: actors.get(job_id))
    second_factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[second_adapter])
    assert second_factory.reconcile(state.recovery_ref) == state
    state = first_factory._cas(state, launch_claim_until=0.0)
    recovered = second_factory.reconcile(state.recovery_ref)
    invocation_id = second_adapter._invocation_id(state.execution_target_ref.removeprefix("job:"))
    assert actor.invocations == {invocation_id: "running"}
    assert recovered.status == "running"
    assert second_factory.reconcile(state.recovery_ref) == recovered
    assert actor.invocations == {invocation_id: "running"}


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
    monkeypatch.setattr(adapter, "_lookup_actor", lambda job_id: None)
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
    monkeypatch.setattr(adapter, "_lookup_actor", lambda job_id: None)
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
    job = orchestrator.job_manager.get(target["ref"].removeprefix("job:"))
    assert job is not None and job.state == "accepted" and job.result_payload is None


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
    job = orchestrator.job_manager.get(target["ref"].removeprefix("job:"))
    assert job is not None and job.state == "failed"



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
def test_ray_absent_nonterminal_job_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from breadboard_engine.orchestration import MultiAgentOrchestrator, TeamConfig

    orchestrator = MultiAgentOrchestrator(TeamConfig("ray-absent"))
    adapter = RayJobAdapter(orchestrator)
    spawned = orchestrator.spawn_subagent(
        owner_agent="parent-session",
        agent_id="child-session",
        async_mode=True,
        task_descriptor={"recovery_ref": "child://child-session/attempt/a"},
    )
    monkeypatch.setattr(adapter, "_lookup_actor", lambda job_id: None)
    assert adapter.observe(ExecutionTarget(f"job:{spawned.job.job_id}").retained()) == "absent"
    assert orchestrator.job_manager.get(spawned.job.job_id).state == "failed"


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
    with mixin._record_file_lock("windows-session"):
        pass
    assert lock_paths == [tmp_path / (hashlib.sha256(b"windows-session").hexdigest() + ".lock")]



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
