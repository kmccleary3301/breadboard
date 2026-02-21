from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.longrun.checkpoint import (
    LATEST_POINTER_REL,
    load_latest_checkpoint_pointer,
    load_state_from_latest_checkpoint,
    write_checkpoint,
)
from agentic_coder_prototype.longrun.controller import LongRunController


class _DiskLogger:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = str(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, rel_path: str, data: Dict[str, Any]) -> str:
        target = Path(self.run_dir) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return rel_path


def test_checkpoint_pointer_roundtrip(tmp_path: Path) -> None:
    logger = _DiskLogger(tmp_path / "run")
    payload = {"episode_index": 2, "status": "running"}
    rel = write_checkpoint(logger, payload, episode=2, phase="end")
    assert rel == "meta/checkpoints/longrun_state_ep_2_end.json"
    pointer = load_latest_checkpoint_pointer(tmp_path / "run")
    assert pointer is not None
    assert pointer["path"] == rel
    restored = load_state_from_latest_checkpoint(tmp_path / "run")
    assert restored is not None
    assert restored["episode_index"] == 2


def test_controller_checkpoint_start_end_and_pointer(tmp_path: Path) -> None:
    logger = _DiskLogger(tmp_path / "run")
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 3},
            "recovery": {"checkpoint_every_episodes": 1},
        }
    }
    controller = LongRunController(cfg, logger_v2=logger)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    summary = out["macro_summary"]
    assert summary["stop_reason"] == "episode_completed"

    run_dir = Path(logger.run_dir)
    start_ckpt = run_dir / "meta/checkpoints/longrun_state_ep_0_start.json"
    end_ckpt = run_dir / "meta/checkpoints/longrun_state_ep_0_end.json"
    pointer = run_dir / LATEST_POINTER_REL
    assert start_ckpt.exists()
    assert end_ckpt.exists()
    assert pointer.exists()

    payload = json.loads(pointer.read_text(encoding="utf-8"))
    assert payload["path"] == "meta/checkpoints/longrun_state_ep_0_end.json"
