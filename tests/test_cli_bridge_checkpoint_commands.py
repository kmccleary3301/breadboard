from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner
from agentic_coder_prototype.checkpointing.checkpoint_manager import CheckpointManager


@pytest.mark.asyncio
async def test_checkpoint_list_and_restore_emit_events(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "file.txt"
    target.write_text("one\n", encoding="utf-8")

    manager = CheckpointManager(workspace)
    ckpt1 = manager.create_checkpoint("first")

    target.write_text("two\n", encoding="utf-8")
    ckpt2 = manager.create_checkpoint("second")

    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_ckpt", status=SessionStatus.RUNNING)
    request = SessionCreateRequest(config_path="dummy.yml", task="hi", stream=False, workspace=str(workspace))
    runner = SessionRunner(session=session, registry=registry, request=request)
    runner._workspace_path = workspace
    runner._checkpoint_manager = manager

    await runner.handle_command("list_checkpoints", {})
    evt1 = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    assert evt1 is not None
    assert evt1.type.value == "checkpoint_list"
    assert len(evt1.payload.get("checkpoints") or []) >= 2

    await runner.handle_command("restore_checkpoint", {"checkpoint_id": ckpt1.checkpoint_id, "mode": "code"})
    # restore emits checkpoint_restored then checkpoint_list
    evt2 = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    evt3 = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    assert evt2 is not None and evt3 is not None
    types = [evt2.type.value, evt3.type.value]
    assert "checkpoint_restored" in types
    assert "checkpoint_list" in types

    assert target.read_text(encoding="utf-8") == "one\n"
    checkpoints = manager.list_checkpoints()
    assert checkpoints and checkpoints[-1].checkpoint_id == ckpt1.checkpoint_id

