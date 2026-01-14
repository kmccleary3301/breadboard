from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

EVENTS_FILENAME = "ctree_events.jsonl"
SNAPSHOT_FILENAME = "ctree_snapshot.json"


def resolve_ctree_paths(base_dir: str | Path) -> Dict[str, Path]:
    base_path = Path(base_dir)
    meta_dir = base_path / "meta"
    return {
        "events": meta_dir / EVENTS_FILENAME,
        "snapshot": meta_dir / SNAPSHOT_FILENAME,
    }


def append_event(path: str | Path, event: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True, default=str))
        handle.write("\n")


def load_events(path: str | Path) -> List[Dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    events: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def write_snapshot(path: str | Path, snapshot: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=True, indent=2, default=str),
        encoding="utf-8",
    )


def load_snapshot(path: str | Path) -> Optional[Dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return None
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None
