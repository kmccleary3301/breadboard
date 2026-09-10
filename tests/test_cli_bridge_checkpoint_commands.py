from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import threading
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard.modules import (
    AdmissionGrant,
    AuthorityDeclaration,
    CheckpointEnvelope,
    CheckpointProposal,
    InputEnvelope,
    ModuleInput,
    OutputEnvelope,
)
from breadboard.modules.transport import (
    PROTOCOL_VERSION,
    encode_bytes,
    RequestKey,
    WireHeader,
    WireMessage,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import Session
from breadboard.product.runtime.events import GenerationAdoptionError
from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.author_runtime import ModuleRuntime, _ModuleWorker
from breadboard_engine.api.cli_bridge.registry import (
    ModuleExecutionRecord,
    SessionRecord,
    SessionRegistry,
)
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.api.cli_bridge.service import (
    _decode_graph_checkpoint,
    _graph_checkpoint_proposal,
)
from breadboard_engine.checkpointing.checkpoint_manager import CheckpointManager


@pytest.mark.asyncio
async def test_checkpoint_command_before_workspace_fails_stably() -> None:
    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_unready", status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path="dummy.yml", task="", stream=False)
    runner = SessionRunner(session=session, registry=registry, request=request)

    assert runner._checkpoint_manager is None
    with pytest.raises(RuntimeError, match="workspace not ready"):
        await runner.handle_command("list_checkpoints", {})


@pytest.mark.asyncio
async def test_checkpoint_list_and_restore_emit_events(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "file.txt"
    target.write_text("one\n", encoding="utf-8")

    manager = CheckpointManager(workspace)
    ckpt1 = manager.create_checkpoint("first", snapshot={"messages": [{"role": "user", "content": "one"}]})

    target.write_text("two\n", encoding="utf-8")
    manager.create_checkpoint("second")

    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_ckpt", status=SessionStatus.RUNNING)
    request = SessionCreateRequest(
        config_path="dummy.yml", task="hi", stream=False, workspace=str(workspace)
    )
    runner = SessionRunner(session=session, registry=registry, request=request)
    runner._workspace_path = workspace
    runner._checkpoint_manager = manager

    await runner.handle_command("list_checkpoints", {})
    evt1 = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    assert evt1 is not None
    assert evt1.type.value == "checkpoint_list"
    assert len(evt1.payload.get("checkpoints") or []) >= 2

    await runner.handle_command(
        "restore_checkpoint", {"checkpoint_id": ckpt1.checkpoint_id, "mode": "both"}
    )
    # restore emits checkpoint_restored then checkpoint_list
    evt2 = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    evt3 = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    assert evt2 is not None and evt3 is not None
    types = [evt2.type.value, evt3.type.value]
    assert "checkpoint_restored" in types
    assert "checkpoint_list" in types

    assert target.read_text(encoding="utf-8") == "one\n"
    snapshot_ref = session.metadata["conversation_snapshot"]
    assert snapshot_ref["checkpoint_id"] == ckpt1.checkpoint_id
    assert Path(snapshot_ref["path"]).read_text(encoding="utf-8").startswith("{")
    checkpoints = manager.list_checkpoints()
    assert checkpoints and checkpoints[-1].checkpoint_id == ckpt1.checkpoint_id


def test_graph_checkpoint_retains_each_binding_frontier() -> None:
    generation = "sha256:" + "a" * 64
    product_session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": generation}),
        module_input=ModuleInput("demo.input.v1", b"{}", False),
        module_input_sequence=0,
        session_id="session-graph",
    )
    root = CheckpointEnvelope(
        generation,
        "root.module",
        "root-instance",
        "work-a",
        "attempt-a",
        "state.v1",
        b"root-state",
    )
    dependency = CheckpointEnvelope(
        generation,
        "dependency.module",
        "dependency-instance",
        "work-a",
        "attempt-a",
        "state.v1",
        b"dependency-state",
    )
    aggregate = _graph_checkpoint_proposal(
        session_id="session-graph",
        request_id="checkpoint-1",
        reason="adopt",
        root_binding="root",
        proposals={
            "root": CheckpointProposal(root, 1),
            "dependency": CheckpointProposal(dependency, 3),
        },
        source_dependencies={"root": (), "dependency": ()},
    )

    checkpoint = product_session.stamp_checkpoint(
        aggregate,
        checkpoint_id="checkpoint-1",
    )
    payload, proposals = _decode_graph_checkpoint(checkpoint)

    assert aggregate.declared_at_sequence == 1
    resume_sequences = {
        row["binding"]: row["declared_at_sequence"] for row in payload["bindings"]
    }
    assert resume_sequences == {"dependency": 3, "root": 1}
    assert proposals == {
        "dependency": CheckpointProposal(dependency, 3),
        "root": CheckpointProposal(root, 1),
    }

    corrupt_payload = json.loads(checkpoint.body)
    next(
        row for row in corrupt_payload["bindings"] if row["binding"] == "root"
    )["declared_at_sequence"] = 2
    corrupt_body = json.dumps(
        corrupt_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    corrupt_checkpoint = replace(
        checkpoint,
        body=corrupt_body,
        body_sha256="sha256:" + hashlib.sha256(corrupt_body).hexdigest(),
    )

    with pytest.raises(GenerationAdoptionError) as error:
        _decode_graph_checkpoint(corrupt_checkpoint)
    assert error.value.code == "checkpoint_corrupt"


@pytest.mark.asyncio
async def test_resumed_dependency_continues_its_persisted_binding_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_generation = "sha256:" + "a" * 64
    target_generation = "sha256:" + "b" * 64
    source_session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": source_generation}),
        module_input=ModuleInput("root.input.v1", b"{}", False),
        module_input_sequence=0,
        session_id="session-resume-frontier",
    )
    root_checkpoint = CheckpointEnvelope(
        source_generation,
        "root.module",
        "root-source-instance",
        "source-work",
        "source-attempt",
        "state.v1",
        b'{"root_calls":1}',
    )
    dependency_checkpoint = CheckpointEnvelope(
        source_generation,
        "dependency.module",
        "dependency-source-instance",
        "source-work",
        "source-attempt",
        "state.v1",
        b'{"dependency_calls":3}',
    )
    aggregate = _graph_checkpoint_proposal(
        session_id=source_session.read_model.session_id,
        request_id="checkpoint-resume-frontier",
        reason="adopt",
        root_binding="root",
        proposals={
            "root": CheckpointProposal(root_checkpoint, 1),
            "dependency": CheckpointProposal(dependency_checkpoint, 3),
        },
        source_dependencies={"root": ("ranking.v1",), "dependency": ()},
    )
    checkpoint = source_session.stamp_checkpoint(
        aggregate,
        checkpoint_id="checkpoint-resume-frontier",
    )
    _, source_proposals = _decode_graph_checkpoint(checkpoint)
    adoption_key = RequestKey(
        worker_session_id="dependency-target-worker",
        request_id="checkpoint-adoption:dependency",
        generation_id=target_generation,
        instance_id="dependency-target-instance",
        work_id="target-work",
        attempt_id="target-attempt",
        authority_epoch=2,
    )
    dependency_adoption_worker = _ModuleWorker.__new__(_ModuleWorker)
    dependency_adoption_worker.binding = "dependency"
    dependency_adoption_worker.key = adoption_key
    dependency_adoption_worker.limits = SimpleNamespace(
        max_checkpoint_bytes=1024 * 1024
    )
    dependency_adoption_worker._step_lock = threading.Lock()
    dependency_adoption_worker.prepare = lambda: None
    dependency_adoption_worker._send = lambda *_args: None
    adoption_responses = iter(
        (
            WireMessage(
                WireHeader(PROTOCOL_VERSION, "checkpoint", adoption_key, 0),
                {
                    "phase": "proposal",
                    "chunk_index": 0,
                    "chunk_count": 1,
                    "total_bytes": len(dependency_checkpoint.body),
                    "schema_id": dependency_checkpoint.schema_id,
                    "source_generation_id": dependency_checkpoint.source_generation_id,
                    "source_module_id": dependency_checkpoint.source_module_id,
                    "source_instance_id": dependency_checkpoint.source_instance_id,
                    "source_work_id": dependency_checkpoint.source_work_id,
                    "source_attempt_id": dependency_checkpoint.source_attempt_id,
                    "declared_at_sequence": 3,
                    "body": encode_bytes(dependency_checkpoint.body),
                },
            ),
            WireMessage(
                WireHeader(
                    PROTOCOL_VERSION,
                    "checkpoint_compatibility",
                    adoption_key,
                    1,
                ),
                {
                    "disposition": "compatible",
                    "target_schema_id": dependency_checkpoint.schema_id,
                    "reason": "",
                },
            ),
        )
    )
    dependency_adoption_worker._receive = (
        lambda _timeout=None: next(adoption_responses)
    )
    disposition, _, _, prepared_dependency = (
        dependency_adoption_worker.prepare_checkpoint(
            source_proposals["dependency"],
            (),
            (),
        )
    )
    assert disposition == "compatible"
    assert prepared_dependency is not None
    source_proposals["dependency"] = prepared_dependency

    state_root = tmp_path / "registry"
    registry = SessionRegistry(state_root=state_root)
    await registry.create(
        SessionRecord(
            session_id="session-resume-frontier",
            status=SessionStatus.RUNNING,
            module_resume_checkpoints=source_proposals,
        )
    )
    restarted = await SessionRegistry(state_root=state_root).get(
        "session-resume-frontier"
    )
    assert restarted is not None

    bindings = {
        "root": {
            "dependencies": {"ranking": "dependency"},
            "children": {},
        },
        "dependency": {
            "dependencies": {},
            "children": {},
        },
    }

    class TargetLock:
        generation_id = target_generation

        def __getitem__(self, key: str) -> object:
            assert key == "modules"
            return {"bindings": bindings}

    contract = SimpleNamespace(
        contract_id="ranking.v1",
        input_schema_ids=("dependency.input.v1",),
        output_schema_ids=("dependency.output.v1",),
    )
    runtime = SimpleNamespace(kind="oci")
    root_package = SimpleNamespace(
        manifest=SimpleNamespace(
            logical_package="root.module",
            resource_budget={},
            dependency_contracts={"ranking": "ranking.v1"},
            contracts=(),
            execution_tier="enforced_isolated",
            runtime=runtime,
        )
    )
    dependency_package = SimpleNamespace(
        manifest=SimpleNamespace(
            logical_package="dependency.module",
            resource_budget={},
            dependency_contracts={},
            contracts=(contract,),
            execution_tier="enforced_isolated",
            requested_authority=AuthorityDeclaration(),
            runtime=runtime,
        )
    )
    captured = SimpleNamespace(
        config={},
        materialization=SimpleNamespace(
            lock=TargetLock(),
            packages={
                "root": root_package,
                "dependency": dependency_package,
            },
        )
    )
    target_session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": target_generation}),
        "resumed dependency",
        session_id="session-resume-frontier",
    )
    target_record = SessionRecord(
        session_id="session-resume-frontier",
        status=SessionStatus.RUNNING,
        product_session=target_session,
        module_execution=ModuleExecutionRecord(
            target_generation,
            "root",
            "target-work",
            "target-attempt",
        ),
        module_grant=AdmissionGrant(
            "target-grant",
            2,
            AuthorityDeclaration(),
        ),
        module_resume_checkpoints=restarted.module_resume_checkpoints,
    )
    module_runtime = ModuleRuntime(
        record=target_record,
        registry=SessionRegistry(),
        captured=captured,
        workspace=tmp_path / "workspace",
        storage_root=tmp_path / "modules",
        repository=SimpleNamespace(),
        loop=asyncio.get_running_loop(),
        session_lock=threading.RLock(),
        persist_session=lambda: None,
        emit_output=lambda *_args: None,
        resume_checkpoints=restarted.module_resume_checkpoints,
    )
    monkeypatch.setattr(_ModuleWorker, "prepare", lambda _worker: None)
    observed_sequences: list[int] = []

    def dependency_step(
        worker: _ModuleWorker,
        envelope: InputEnvelope,
        _request_id: str,
    ) -> OutputEnvelope:
        assert worker.binding == "dependency"
        observed_sequences.append(envelope.sequence)
        return OutputEnvelope("dependency.output.v1", b'{"ranked":true}')

    monkeypatch.setattr(_ModuleWorker, "step", dependency_step)
    key = RequestKey(
        worker_session_id="root-target-worker",
        request_id="dependency-call-after-resume",
        generation_id=target_generation,
        instance_id="root-target-instance",
        work_id="target-work",
        attempt_id="target-attempt",
        authority_epoch=2,
    )
    response = module_runtime.dispatch(
        SimpleNamespace(
            binding="root",
            key=key,
            package=root_package,
        ),
        WireMessage(
            WireHeader(PROTOCOL_VERSION, "dependency_request", key, 0),
            {
                "request_id": key.request_id,
                "dependency": "ranking",
                "contract_id": "ranking.v1",
                "input": ModuleInput(
                    "dependency.input.v1",
                    b'{"query":"next"}',
                ).to_dict(),
            },
        ),
    )

    assert response["schema_id"] == "dependency.output.v1"
    assert observed_sequences == [3]
