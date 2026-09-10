from __future__ import annotations
import asyncio

from io import BytesIO
import threading
from types import SimpleNamespace

import pytest

from breadboard.modules import (
    AdmissionGrant,
    AuthorityDeclaration,
    CheckpointCompatibility,
    CheckpointEnvelope,
    CheckpointProposal,
    InstanceIdentity,
)
from breadboard.modules.transport import (
    PROTOCOL_VERSION,
    RequestKey,
    WireHeader,
    WireMessage,
    encode_bytes,
    read_message,
)
from breadboard.modules.worker import _Worker
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.coordination.work_items import WorkItem, WorkItemRepository
from breadboard.product.runtime.events import Session
from breadboard_engine.api.cli_bridge.author_domains import AuthorDomainQuiescence
from breadboard_engine.api.cli_bridge.author_runtime import (
    ModuleDisposal,
    ModuleExecutionError,
    ModuleRuntime,
)
from breadboard_engine.api.cli_bridge.models import SessionStatus
from breadboard_engine.api.cli_bridge.registry.records import (
    ModuleExecutionRecord,
    SessionRecord,
)
from breadboard_engine.api.cli_bridge.service import (
    SessionService,
    _decode_graph_checkpoint,
    _graph_checkpoint_proposal,
)
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner


class MigratingModule:
    def assess_checkpoint(self, context):
        assert context.source_schema_id == "state.v1"
        assert context.source_dependencies == ("source.contract",)
        assert context.target_dependencies == ("target.contract",)
        return CheckpointCompatibility("migrate", "state.v2", "schema upgrade")

    def decode_checkpoint(self, envelope):
        assert envelope.body == b"count=1"
        return {"count": 1, "migrated": True}

    def encode_checkpoint(self, state, **owner):
        assert state == {"count": 1, "migrated": True}
        declared = owner.pop("declared_at_sequence")
        return CheckpointProposal(
            CheckpointEnvelope(schema_id="state.v2", body=b"count=1;migrated=true", **owner),
            declared,
        )


def test_checkpoint_migration_runs_inside_unopened_target_worker() -> None:
    output = BytesIO()
    worker = _Worker(BytesIO(), output)
    worker.module = MigratingModule()
    worker.identity = InstanceIdentity(
        instance_id="target-instance",
        module_id="target.module",
        generation_id="sha256:" + "b" * 64,
        instance_label="root",
    )
    key = RequestKey(
        worker_session_id="target-session",
        request_id="adopt-request",
        generation_id=worker.identity.generation_id,
        instance_id=worker.identity.instance_id,
        work_id="target-work",
        attempt_id="target-attempt",
        authority_epoch=2,
    )
    source = {
        "source_generation_id": "sha256:" + "a" * 64,
        "source_module_id": "source.module",
        "source_instance_id": "source-instance",
        "source_work_id": "source-work",
        "source_attempt_id": "source-attempt",
        "schema_id": "state.v1",
        "body": encode_bytes(b"count=1"),
    }

    worker._prepare_checkpoint(
        WireMessage(
            WireHeader(PROTOCOL_VERSION, "checkpoint_prepare", key, 0),
            {
                "source": source,
                "source_dependencies": ["source.contract"],
                "target_dependencies": ["target.contract"],
                "declared_at_sequence": 0,
            },
        )
    )

    output.seek(0)
    checkpoint = read_message(output)
    compatibility = read_message(output)
    assert checkpoint is not None and checkpoint.header.kind == "checkpoint"
    assert checkpoint.body["schema_id"] == "state.v2"
    assert checkpoint.body["source_generation_id"] == worker.identity.generation_id
    assert compatibility is not None
    assert compatibility.header.kind == "checkpoint_compatibility"
    assert compatibility.body == {
        "disposition": "migrate",
        "target_schema_id": "state.v2",
        "reason": "schema upgrade",
    }
    assert read_message(output) is None


def test_checkpoint_refuses_unsettled_owned_operation() -> None:
    checkpoint_called = False

    def checkpoint(*_args, **_kwargs):
        nonlocal checkpoint_called
        checkpoint_called = True
        raise AssertionError("checkpoint must not run before owner quiescence")

    worker = SimpleNamespace(
        binding="root",
        domains=SimpleNamespace(
            quiescence=lambda: AuthorDomainQuiescence(
                approvals=("approval:pending",)
            )
        ),
        children=None,
        checkpoint=checkpoint,
        prepare=lambda: None,
    )
    runtime = ModuleRuntime.__new__(ModuleRuntime)
    runtime.require_live = lambda: None
    runtime.root_binding = "root"
    runtime._mutation_lock = threading.RLock()
    runtime._workers = {"root": worker}

    with pytest.raises(ModuleExecutionError) as error:
        runtime.capture_checkpoints(request_id="checkpoint-1", reason="adopt")

    assert error.value.code == "boundary_unavailable"
    assert "root:approval:pending" in error.value.detail
    assert not checkpoint_called


@pytest.mark.asyncio
async def test_adoption_preparation_carries_the_owning_product_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    source_generation = "sha256:" + "a" * 64
    target_generation = "sha256:" + "b" * 64
    product_session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": source_generation}),
        "checkpoint adoption",
        session_id="session-1",
    )
    record = SessionRecord(
        session_id="session-1",
        status=SessionStatus.RUNNING,
        module_execution=ModuleExecutionRecord(
            target_generation,
            "root",
            "work-b",
            "attempt-b",
        ),
        module_grant=AdmissionGrant("grant-b", 2, AuthorityDeclaration()),
        product_session=product_session,
    )

    class TargetLock:
        generation_id = target_generation

        def __getitem__(self, key):
            assert key == "modules"
            return {"bindings": {"root": "target.module"}}

    captured = SimpleNamespace(
        materialization=SimpleNamespace(lock=TargetLock(), packages={})
    )
    runner = SessionRunner.__new__(SessionRunner)
    runner.session = record
    runner.registry = SimpleNamespace()
    runner._module_storage_root = tmp_path / "modules"
    runner._durable_child_repository = SimpleNamespace()
    runner._workspace_path = tmp_path
    runner._loop = asyncio.get_running_loop()
    runner._product_session_lock = threading.RLock()
    source_checkpoint = CheckpointEnvelope(
        source_generation,
        "source.module",
        "instance-a",
        "work-a",
        "attempt-a",
        "state.v1",
        b"state",
    )

    def prepare(candidate, checkpoints, dependencies):
        assert candidate.record.product_session is product_session
        assert checkpoints == {"root": source_checkpoint}
        assert dependencies == {"root": ("source.contract",)}
        return {"root": source_checkpoint}, (
            {
                "binding": "root",
                "disposition": "compatible",
                "source_schema_id": "state.v1",
                "target_schema_id": "state.v1",
                "reason": "",
            },
        )

    monkeypatch.setattr(ModuleRuntime, "prepare_checkpoint_adoption", prepare)
    monkeypatch.setattr(
        ModuleRuntime,
        "close",
        lambda *_args, **_kwargs: ModuleDisposal("confirmed_absent", (), ()),
    )

    resumed, decisions = await runner.prepare_adopted_module_checkpoints(
        captured_runtime=captured,
        module_execution=record.module_execution,
        module_grant=record.module_grant,
        source_checkpoints={"root": source_checkpoint},
        source_dependencies={"root": ("source.contract",)},
    )

    assert resumed == {"root": source_checkpoint}
    assert decisions[0]["disposition"] == "compatible"


@pytest.mark.asyncio
async def test_source_runtime_is_confirmed_absent_before_adoption_commit() -> None:
    generation = "sha256:" + "a" * 64
    product_session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": generation}),
        "checkpoint adoption",
        session_id="session-1",
    )
    record = SessionRecord(
        session_id="session-1",
        status=SessionStatus.RUNNING,
        module_execution=ModuleExecutionRecord(
            generation,
            "root",
            "work-a",
            "attempt-a",
        ),
        module_grant=AdmissionGrant("grant-a", 1, AuthorityDeclaration()),
        product_session=product_session,
    )
    persisted = []

    class Registry:
        async def persist(self, session):
            persisted.append(session.module_execution)

    runtime = SimpleNamespace(
        generation_id=generation,
        close=lambda reason: ModuleDisposal(
            "confirmed_absent",
            (),
            () if reason == "generation_adoption_fence" else ("wrong_reason",),
        ),
    )
    runner = SessionRunner.__new__(SessionRunner)
    runner.session = record
    runner.registry = Registry()
    runner._product_session_lock = threading.RLock()
    runner._module_runtime = runtime
    runner._module_disposal = None
    runner._resume_checkpoints = {}
    checkpoint = CheckpointEnvelope(
        generation,
        "source.module",
        "instance-a",
        "work-a",
        "attempt-a",
        "state.v1",
        b"state",
    )

    disposal = await runner.dispose_source_runtime_for_adoption(
        {"root": checkpoint}
    )

    assert disposal.status == "confirmed_absent"
    assert runner._module_runtime is None
    assert record.module_execution is not None
    assert record.module_execution.workers == ()
    assert record.module_resume_checkpoints == {"root": checkpoint}
    assert persisted


def test_graph_checkpoint_retains_dependency_and_owner_frontier() -> None:
    generation = "sha256:" + "a" * 64
    product_session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": generation}),
        "checkpoint graph",
        session_id="session-graph",
    )
    root = CheckpointEnvelope(
        generation,
        "source.module",
        "instance-a",
        "work-a",
        "attempt-a",
        "state.v1",
        b"state",
    )
    aggregate = _graph_checkpoint_proposal(
        session_id="session-graph",
        request_id="checkpoint-1",
        reason="adopt",
        root_binding="root",
        proposals={"root": CheckpointProposal(root, 0)},
        source_dependencies={
            "root": ("ranking.contract.v1",),
            "unused": ("unused.contract.v1",),
        },
    )
    checkpoint = product_session.stamp_checkpoint(
        aggregate,
        checkpoint_id="checkpoint-1",
    )

    payload, envelopes = _decode_graph_checkpoint(checkpoint)

    assert envelopes == {"root": root}
    assert payload["bindings"][0]["dependencies"] == ["ranking.contract.v1"]
    assert payload["operation_frontier"] == {
        "children": [],
        "effects": [],
        "approvals": [],
    }


def test_source_work_settlement_is_exact_and_idempotent(tmp_path) -> None:
    repository = WorkItemRepository(tmp_path / "work-items.jsonl")
    work = WorkItem.create(
        "source generation",
        work_item_id="work-a",
        repository=repository,
    )
    lease = work.acquire_lease("session-1")
    work.start_attempt(
        "session-1",
        lease_id=lease.active_lease.lease_id,
        attempt_id="attempt-a",
    )
    service = SessionService.__new__(SessionService)
    service._durable_child_repository = repository
    payload = {
        "source_work_id": "work-a",
        "source_attempt_id": "attempt-a",
    }

    service._settle_adopted_source_work(payload)
    service._settle_adopted_source_work(payload)

    assert WorkItem.restore(repository, "work-a").read_model.status == "completed"
