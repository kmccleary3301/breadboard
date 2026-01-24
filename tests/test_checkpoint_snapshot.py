from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.checkpointing.checkpoint_manager import CheckpointManager


def test_checkpoint_snapshot_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "example.txt").write_text("hello", encoding="utf-8")
    manager = CheckpointManager(workspace)
    snapshot = {
        "messages": [{"role": "user", "content": "ping"}],
        "transcript": [{"assistant": "pong"}],
    }
    summary = manager.create_checkpoint("snapshot test", snapshot=snapshot)
    loaded = manager.load_snapshot(summary.checkpoint_id)
    assert isinstance(loaded, dict)
    assert loaded.get("messages") == snapshot["messages"]
    assert loaded.get("transcript") == snapshot["transcript"]
