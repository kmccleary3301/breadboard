from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .schema import CTREE_SCHEMA_VERSION, CTreeEvent
from .persistence import append_event, load_events, resolve_ctree_paths, write_snapshot

_ID_RE = re.compile(r"^ctn_(\\d+)$")
_VOLATILE_KEYS = {"seq", "timestamp", "timestamp_ms", "created_at", "event_id"}


@dataclass
class CTreeNode:
    id: str
    kind: str
    turn: Optional[int]
    payload: Any


class CTreeStore:
    """Deterministic, append-only C-Tree event store."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self._counter = 0
        self._persisted_events = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"ctn_{self._counter:06d}"

    def _bump_counter_from_id(self, node_id: str) -> None:
        match = _ID_RE.match(str(node_id))
        if not match:
            return
        try:
            value = int(match.group(1))
        except Exception:
            return
        if value > self._counter:
            self._counter = value

    def _normalize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": event.get("kind"),
            "payload": event.get("payload"),
            "turn": event.get("turn"),
            "node_id": event.get("node_id"),
        }

    def _strip_volatile(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_volatile(val)
                for key, val in value.items()
                if key not in _VOLATILE_KEYS
            }
        if isinstance(value, list):
            return [self._strip_volatile(item) for item in value]
        return value

    def _canonical_dumps(self, value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )

    def apply_event(self, event: Dict[str, Any] | CTreeEvent) -> str:
        if isinstance(event, CTreeEvent):
            event_dict = event.to_dict()
        else:
            event_dict = dict(event or {})
        node_id = event_dict.get("node_id")
        if not node_id:
            node_id = self._next_id()
            event_dict["node_id"] = node_id
        else:
            self._bump_counter_from_id(str(node_id))
        normalized = self._normalize_event(event_dict)
        self.events.append(normalized)
        node = {
            "id": node_id,
            "kind": normalized.get("kind"),
            "turn": normalized.get("turn"),
            "payload": normalized.get("payload"),
        }
        self.nodes.append(node)
        return str(node_id)

    def apply_events(self, events: Iterable[Dict[str, Any] | CTreeEvent]) -> None:
        for event in events:
            self.apply_event(event)

    def record(self, kind: str, payload: Any, *, turn: Optional[int] = None) -> str:
        event = CTreeEvent(kind=str(kind), payload=payload, turn=turn)
        return self.apply_event(event)

    def snapshot(self) -> Dict[str, Any]:
        last_id = self.nodes[-1]["id"] if self.nodes else None
        return {
            "schema_version": CTREE_SCHEMA_VERSION,
            "node_count": len(self.nodes),
            "event_count": len(self.events),
            "last_id": last_id,
        }

    def hashes(self) -> Dict[str, str]:
        normalized_events = [self._strip_volatile(evt) for evt in self.events]
        normalized_nodes = [self._strip_volatile(node) for node in self.nodes]
        events_blob = self._canonical_dumps(normalized_events)
        nodes_blob = self._canonical_dumps(normalized_nodes)
        return {
            "events": hashlib.sha1(events_blob.encode("utf-8")).hexdigest(),
            "nodes": hashlib.sha1(nodes_blob.encode("utf-8")).hexdigest(),
        }

    def persist(self, base_dir: str, *, append: bool = True) -> Dict[str, Any]:
        paths = resolve_ctree_paths(base_dir)
        if append:
            new_events = self.events[self._persisted_events :]
            for event in new_events:
                append_event(paths["events"], dict(event))
            self._persisted_events = len(self.events)
        else:
            target = paths["events"]
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = "\n".join(self._canonical_dumps(event) for event in self.events) + ("\n" if self.events else "")
            target.write_text(payload, encoding="utf-8")
            self._persisted_events = len(self.events)
        snapshot = self.snapshot()
        write_snapshot(paths["snapshot"], snapshot)
        return {"paths": {key: str(path) for key, path in paths.items()}, "snapshot": snapshot}

    @classmethod
    def from_events(cls, events: Iterable[Dict[str, Any] | CTreeEvent]) -> "CTreeStore":
        store = cls()
        store.apply_events(events)
        return store

    @classmethod
    def from_dir(cls, base_dir: str) -> "CTreeStore":
        paths = resolve_ctree_paths(base_dir)
        events = load_events(paths["events"])
        store = cls()
        store.apply_events(events)
        return store
