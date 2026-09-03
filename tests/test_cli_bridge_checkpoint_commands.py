from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
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
