from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.checkpointing.checkpoint_manager import (
    CheckpointManager,
    CheckpointSummary,
    build_checkpoint_metadata_record,
)
from agentic_coder_prototype.longrun.checkpoint import build_longrun_checkpoint_metadata_record


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


def test_checkpoint_metadata_record_helpers() -> None:
    workspace_summary = build_checkpoint_metadata_record(
        summary=CheckpointSummary(
            checkpoint_id="ckpt-0001",
            created_at=1234567890,
            preview="snapshot test",
            tracked_files=2,
            additions=4,
            deletions=1,
            has_untracked_changes=False,
        )
    )
    assert workspace_summary["schema_version"] == "bb.checkpoint_metadata.v1"
    assert workspace_summary["checkpoint_ref"] == "ckpt-0001"
    assert workspace_summary["source_kind"] == "workspace_checkpoint"

    longrun_summary = build_longrun_checkpoint_metadata_record(
        path="meta/checkpoints/longrun_state_ep_2_end.json",
        episode=2,
        phase="end",
        updated_at=123.5,
    )
    assert longrun_summary["schema_version"] == "bb.checkpoint_metadata.v1"
    assert longrun_summary["checkpoint_ref"] == "meta/checkpoints/longrun_state_ep_2_end.json"
    assert longrun_summary["summary"]["episode"] == 2
