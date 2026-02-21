from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping


LATEST_POINTER_REL = "meta/checkpoints/latest_checkpoint.json"


def write_checkpoint(
    logger_v2: Any,
    payload: Mapping[str, Any],
    *,
    episode: int,
    phase: str,
) -> str | None:
    """
    Persist a checkpoint and update latest-pointer metadata.
    """
    if logger_v2 is None:
        return None
    rel = f"meta/checkpoints/longrun_state_ep_{int(episode)}_{str(phase)}.json"
    try:
        logger_v2.write_json(rel, dict(payload))
    except Exception:
        return None
    pointer = {
        "path": rel,
        "episode": int(episode),
        "phase": str(phase),
        "updated_at": time.time(),
    }
    try:
        logger_v2.write_json(LATEST_POINTER_REL, pointer)
    except Exception:
        pass
    return rel


def load_latest_checkpoint_pointer(run_dir: str | Path) -> dict[str, Any] | None:
    base = Path(run_dir)
    pointer_path = base / LATEST_POINTER_REL
    if not pointer_path.exists():
        return None
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load_state_from_latest_checkpoint(run_dir: str | Path) -> dict[str, Any] | None:
    pointer = load_latest_checkpoint_pointer(run_dir)
    if not isinstance(pointer, dict):
        return None
    rel = pointer.get("path")
    if not isinstance(rel, str) or not rel:
        return None
    target = Path(run_dir) / rel
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
