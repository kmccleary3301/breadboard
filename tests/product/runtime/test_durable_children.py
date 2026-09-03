from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from breadboard.product.coordination.work_items import WorkItem, WorkItemRepository
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import Session
from breadboard.product.runtime.children import (
    ChildSpec,
    ChildState,
    DurableChildFactory,
    ExpectedRevisionConflict,
    ExecutionTarget,
    LateResultRejected,
    PreparationRequired,
    ProcessExecutionAdapter,
    RayJobAdapter,
)
from breadboard.product.runtime.session_store import create_session, load_session
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
        return ExecutionTarget(f"retry-target-{self.starts}")

    def observe(self, target):
        return "absent" if target["ref"] == "retry-target-1" else "running"

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
    repository = repository or WorkItemRepository()
    work = WorkItem.create("parent work", work_item_id="parent-work", repository=repository)
    work.acquire_lease("parent-worker", lease_id="parent-lease")
    work.start_attempt("parent-session", lease_id="parent-lease", attempt_id="parent-attempt")
    registry = SessionRegistry(state_root=tmp_path / "registry")
    return workspace, repository, work, registry


def _spec(adapter_family: str, title: str = "child work") -> ChildSpec:
    return ChildSpec(title, "child task", _lock(), "child-worker", adapter_family)


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
    os.kill(int(activation.execution_target_ref.removeprefix("pid:")), signal.SIGKILL)
    adapter.process.wait(timeout=2)
    assert adapter.observe((await_record(registry, activation.child_session_id)).metadata["durable_child"]["execution_target"]) == "absent"

    restarted = DurableChildFactory(workspace, registry=SessionRegistry(state_root=tmp_path / "registry"), repository=WorkItemRepository(tmp_path / "work-items.jsonl"), adapters=[adapter])
    state = restarted.reconcile(activation.recovery_ref)
    assert (state.status, state.terminal_outcome, state.terminal_count) == ("failed", "failed", 1)
    assert (state.child_session_id, state.root_session_id, state.parent_work_item_id, state.recovery_ref) == (activation.child_session_id, activation.root_session_id, "parent-work", activation.recovery_ref)
    child, _ = load_session(workspace, activation.child_session_id)
    assert child.read_model.status == "failed"
    with pytest.raises(LateResultRejected):
        restarted.settle(activation.child_session_id, expected_revision=state.revision, outcome="completed")
    assert restarted.reconcile(activation.recovery_ref).terminal_count == 1


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
        with pytest.raises(PreparationRequired):
            factory.settle(activation.child_session_id, expected_revision=current.revision, outcome="completed")
        prepared = factory.prepare_result(activation.child_session_id, expected_revision=current.revision, result=f"result-{index}".encode())
        settled = factory.settle(activation.child_session_id, expected_revision=prepared.revision, outcome="completed", result_refs=prepared.result_refs)
        assert (settled.status, settled.terminal_outcome, settled.terminal_count, settled.joined, settled.result_prepared) == ("completed", "completed", 1, True, True)
        assert factory.settle(activation.child_session_id, expected_revision=0, outcome="completed", result_refs=prepared.result_refs) == settled
        with pytest.raises(LateResultRejected):
            factory.prepare_result(activation.child_session_id, expected_revision=prepared.revision, result=b"late")
    process_target = factory._record_state(activations[1].child_session_id).execution_target
    process_adapter.cancel(process_target)


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
        factory.settle(activation.child_session_id, expected_revision=canceled.revision, outcome="completed")



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
    prepared = factory.prepare_result(activation.child_session_id, expected_revision=current.revision, result=b"done")
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

def test_absent_target_honors_retry_policy_with_new_attempt_and_recovery_ref(tmp_path: Path) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = RetryAdapter()
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    spec = ChildSpec("retry child", "retry task", _lock(), "child-worker", adapter.family, retry_policy=RetryPolicy(2, True))
    activation = factory.start(parent_session_id="parent-session", root_session_id="parent-session", parent_work_item_id=parent.read_model.work_item_id, spec=spec)
    state = factory.reconcile(activation.recovery_ref)
    assert state.status == "running"
    assert state.attempt_id != activation.attempt_id
    assert state.recovery_ref != activation.recovery_ref
    child_work = WorkItem.restore(repository, activation.child_work_item_id)
    assert [(attempt.number, attempt.status) for attempt in child_work.read_model.attempts] == [(1, "failed"), (2, "running")]
    assert factory.reconcile(state.recovery_ref) == state


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
        child_spec=spec.retained(),
        execution_target={"ref": "reserved:child-startup"},
    )
    factory._create_record(initial)
    recovered = factory.reconcile(initial.recovery_ref)
    assert (recovered.status, recovered.terminal_outcome, recovered.terminal_count) == ("failed", "failed", 1)
    assert await_record(registry, initial.child_session_id).status.value == "failed"


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
    prepared = factory.prepare_result(activation.child_session_id, expected_revision=current.revision, result=b"done")
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


def test_launch_identity_is_recovered_before_retry_after_target_cas_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, repository, parent, registry = _running_parent(tmp_path)
    adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    original_cas = factory._cas

    def crash_target_publication(state, **changes):
        if "execution_target" in changes and state.status == "running":
            raise RuntimeError("simulated crash before target CAS")
        return original_cas(state, **changes)

    monkeypatch.setattr(factory, "_cas", crash_target_publication)
    with pytest.raises(RuntimeError, match="target CAS"):
        factory.start(
            parent_session_id="parent-session",
            root_session_id="parent-session",
            parent_work_item_id=parent.read_model.work_item_id,
            spec=_spec(adapter.family, "launch recovery"),
        )
    process = next(iter(adapter._processes.values()))
    try:
        restarted_adapter = ProcessExecutionAdapter(command=("/bin/sh", "-c", "sleep 30"))
        restarted = DurableChildFactory(
            workspace,
            registry=SessionRegistry(state_root=tmp_path / "registry"),
            repository=repository,
            adapters=[restarted_adapter],
        )
        state = next(
            value
            for value in await_records(restarted.registry)
            if value.metadata.get("durable_child")
        )
        recovered = restarted.reconcile(state.metadata["durable_child"]["recovery_ref"])
        assert recovered.status == "running"
        assert isinstance(recovered.execution_target.get("pid"), int)
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

    async def reconcile(recovery_ref: str) -> None:
        seen.append(recovery_ref)

    restarted_registry = SessionRegistry(state_root=tmp_path / "registry")
    service = SessionService(
        registry=restarted_registry,
        state_root=tmp_path / "registry",
        durable_child_reconciler=reconcile,
    )
    record = asyncio_run(service.ensure_session(activation.child_session_id))
    assert seen == [activation.recovery_ref]
    assert record.product_session is not None
    assert record.runner is None


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
