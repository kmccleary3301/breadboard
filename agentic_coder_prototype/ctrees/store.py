from __future__ import annotations

import hashlib
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .schema import CTREE_SCHEMA_VERSION, canonical_ctree_json, normalize_ctree_snapshot_summary


@dataclass
class CTreeNode:
    id: str
    digest: str
    kind: str
    turn: Optional[int]
    payload: Any


class CTreeStore:
    """In-memory C-Tree store (events + derived node digests).

    Design goals:
    - Deterministic hashes across replays (ignore volatile fields like seq/timestamps).
    - Simple persistence for debugging and golden replays.
    - Backwards compatibility: keep a `nodes` list of dicts used by older emitters.
    """

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []

    @staticmethod
    def _semantic_digest(*, kind: str, payload: Any, turn: Optional[int]) -> str:
        canonical = canonical_ctree_json({"kind": kind, "turn": turn, "payload": payload})
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _node_id(*, digest: str, ordinal: int) -> str:
        # IDs must be:
        # - deterministic across replays given identical event order
        # - unique even when identical payloads repeat
        # - compact and stable to surface in SSE/UI
        return f"n{ordinal:08d}_{str(digest)[:8]}"

    def record(
        self,
        kind: str,
        payload: Any,
        *,
        turn: Optional[int] = None,
        node_id: Optional[str] = None,
    ) -> str:
        digest = self._semantic_digest(kind=kind, payload=payload, turn=turn)
        if not node_id:
            node_id = self._node_id(digest=digest, ordinal=len(self.nodes))
        entry: Dict[str, Any] = {
            "id": node_id,
            "digest": digest,
            "kind": kind,
            "turn": turn,
            "payload": payload,
        }
        self.nodes.append(entry)
        self._events.append(
            {
                "kind": kind,
                "payload": payload,
                "turn": turn,
                "node_id": node_id,
            }
        )
        return node_id

    def snapshot(self) -> Dict[str, Any]:
        last_id = self.nodes[-1]["id"] if self.nodes else None
        raw = {
            "schema_version": CTREE_SCHEMA_VERSION,
            "node_count": len(self.nodes),
            "event_count": len(self._events),
            "last_id": last_id,
            "node_hash": self.hashes().get("node_hash"),
        }
        normalized = normalize_ctree_snapshot_summary(raw)
        return normalized if isinstance(normalized, dict) else raw

    @property
    def events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def hashes(self) -> Dict[str, str]:
        digests = [str(node.get("digest") or "") for node in self.nodes if node.get("digest")]
        canonical = json.dumps(digests, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        node_hash = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
        return {
            "node_hash": node_hash,
        }

    def persist(self, directory: str, *, include_raw: bool = False) -> Dict[str, Any]:
        from .persistence import build_eventlog_header, resolve_ctree_paths, write_snapshot

        base = Path(str(directory)).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        paths = resolve_ctree_paths(base)
        events_path = paths["events"]
        snapshot_path = paths["snapshot"]

        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    build_eventlog_header(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    default=str,
                )
            )
            handle.write("\n")
            for event in self._events:
                if include_raw:
                    handle.write(
                        json.dumps(
                            event,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                            default=str,
                        )
                    )
                else:
                    handle.write(canonical_ctree_json(event))
                handle.write("\n")

        snapshot = self.snapshot()
        write_snapshot(snapshot_path, snapshot)

        return {
            "paths": {
                "events": str(events_path),
                "snapshot": str(snapshot_path),
            },
            "counts": {
                "events": len(self._events),
                "nodes": len(self.nodes),
            },
            "hashes": self.hashes(),
        }

    @classmethod
    def from_events(cls, events: Sequence[Dict[str, Any]] | Iterable[Dict[str, Any]]) -> "CTreeStore":
        store = cls()
        for event in list(events):
            if not isinstance(event, dict):
                continue
            kind_raw = event.get("kind")
            if not kind_raw:
                continue
            store.record(
                str(kind_raw),
                event.get("payload"),
                turn=event.get("turn"),
                node_id=str(event.get("node_id")) if event.get("node_id") else None,
            )
        return store

    @classmethod
    def from_dir(cls, directory: str) -> "CTreeStore":
        from .persistence import load_events, resolve_ctree_paths

        base = Path(str(directory)).expanduser().resolve()
        paths = resolve_ctree_paths(base)
        events_path = paths["events"]
        if not events_path.exists():
            # Legacy fallback
            events_path = base / "events.jsonl"
            if not events_path.exists():
                return cls()
        return cls.from_events(load_events(events_path))
