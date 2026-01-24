from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import sanitize_ctree_payload
from .store import CTreeStore
from .persistence import load_snapshot, write_snapshot


def backfill_ctrees_from_eventlog(
    *,
    eventlog_path: str | Path,
    out_dir: str | Path,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Backfill a `.breadboard/ctrees/` artifact set from a session `events.jsonl` file.

    The eventlog is expected to contain JSON objects with at least:
    - `type`: string event type
    - `payload`: object payload

    We extract `ctree_node` events and persist them as:
    - `meta/ctree_events.jsonl`
    - `meta/ctree_snapshot.json`
    """

    src = Path(str(eventlog_path)).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"eventlog not found: {src}")

    dest = Path(str(out_dir)).expanduser().resolve()
    if dest.exists() and not dest.is_dir():
        raise ValueError(f"out_dir must be a directory: {dest}")

    events_path = dest / "meta" / "ctree_events.jsonl"
    snapshot_path = dest / "meta" / "ctree_snapshot.json"
    if not overwrite and (events_path.exists() or snapshot_path.exists()):
        raise FileExistsError("ctrees artifacts already exist; pass overwrite=True to replace")

    extracted: List[Dict[str, Any]] = []
    with src.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("type") or "") != "ctree_node":
                continue
            payload = parsed.get("payload")
            if not isinstance(payload, dict):
                continue
            node = payload.get("node")
            if not isinstance(node, dict):
                continue
            kind = node.get("kind")
            if not kind:
                continue
            event = {
                "kind": kind,
                "payload": node.get("payload"),
                "turn": node.get("turn"),
                "node_id": node.get("id"),
            }
            sanitized = sanitize_ctree_payload(event)
            if isinstance(sanitized, dict):
                extracted.append(dict(sanitized))

    store = CTreeStore.from_events(extracted)

    # Persist as a normal C-Trees artifact set.
    result = store.persist(str(dest), include_raw=False)
    snapshot_path = Path(result["paths"]["snapshot"])
    snapshot = load_snapshot(snapshot_path) or {}
    snapshot["backfilled_from_eventlog"] = True
    snapshot["backfill_eventlog_path"] = str(src)
    write_snapshot(snapshot_path, snapshot)
    result["extracted_events"] = len(extracted)
    result["eventlog_path"] = str(src)
    result["out_dir"] = str(dest)
    return result


def try_backfill_ctrees_from_eventlog(
    *,
    eventlog_path: str | Path,
    out_dir: str | Path,
    overwrite: bool = False,
) -> Optional[Dict[str, Any]]:
    """Best-effort wrapper for backfill (returns None on failure)."""

    try:
        return backfill_ctrees_from_eventlog(eventlog_path=eventlog_path, out_dir=out_dir, overwrite=overwrite)
    except Exception:
        return None
