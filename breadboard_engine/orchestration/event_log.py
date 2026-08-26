"""Deterministic event log for multi-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..security import WorkspaceFilesystem, WorkspacePathError


@dataclass(frozen=True)
class Event:
    event_id: int
    type: str
    agent_id: str
    parent_agent_id: Optional[str]
    causal_parent_event_id: Optional[int]
    timestamp: Optional[float]
    payload: Dict[str, Any]
    mvi_hash: Optional[str] = None


class EventLog:
    """Append-only event log with deterministic ordering."""

    def __init__(self) -> None:
        self._events: List[Event] = []
        self._next_id = 1
        self._lock = threading.Lock()

    @property
    def events(self) -> List[Event]:
        with self._lock:
            return list(self._events)

    def add(
        self,
        event_type: str,
        *,
        agent_id: str,
        payload: Optional[Dict[str, Any]] = None,
        parent_agent_id: Optional[str] = None,
        causal_parent_event_id: Optional[int] = None,
        timestamp: Optional[float] = None,
        mvi_payload: Optional[Any] = None,
    ) -> Event:
        payload_dict = dict(payload or {})
        ts = time.time() if timestamp is None else float(timestamp)
        mvi_hash = None
        if mvi_payload is not None:
            mvi_hash = _hash_payload(mvi_payload)
        with self._lock:
            event = Event(
                event_id=self._next_id,
                type=str(event_type),
                agent_id=str(agent_id),
                parent_agent_id=parent_agent_id,
                causal_parent_event_id=causal_parent_event_id,
                timestamp=ts,
                payload=payload_dict,
                mvi_hash=mvi_hash,
            )
            self._events.append(event)
            self._next_id += 1
            return event

    def extend(self, events: Iterable[Event]) -> None:
        with self._lock:
            for event in events:
                self._events.append(event)
                self._next_id = max(self._next_id, event.event_id + 1)

    def to_jsonl(self, path: str) -> None:
        target = Path(path).expanduser().absolute()
        filesystem = WorkspaceFilesystem.open_anchored_root(
            target.parent,
            create=True,
        )
        try:
            with self._lock:
                content = "".join(
                    json.dumps(event.__dict__, ensure_ascii=False) + "\n"
                    for event in self._events
                )
            filesystem.write_text(target.name, content, encoding="utf-8")
        finally:
            filesystem.close()

    @staticmethod
    def from_jsonl(path: str) -> "EventLog":
        log = EventLog()
        target = Path(path).expanduser().absolute()
        try:
            filesystem = WorkspaceFilesystem.open_anchored_root(
                target.parent,
                create=False,
            )
        except WorkspacePathError as exc:
            if exc.code == "workspace_root_unavailable":
                return log
            raise
        try:
            try:
                raw = filesystem.read_text(
                    target.name,
                    encoding="utf-8",
                    errors="strict",
                )
            except FileNotFoundError:
                return log
        finally:
            filesystem.close()
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            event = Event(
                event_id=int(payload.get("event_id") or 0),
                type=str(payload.get("type") or ""),
                agent_id=str(payload.get("agent_id") or ""),
                parent_agent_id=payload.get("parent_agent_id"),
                causal_parent_event_id=payload.get("causal_parent_event_id"),
                timestamp=payload.get("timestamp"),
                payload=dict(payload.get("payload") or {}),
                mvi_hash=payload.get("mvi_hash"),
            )
            log.extend([event])
        return log


def _hash_payload(payload: Any) -> str:
    try:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except Exception:
        blob = str(payload)
    return hashlib.sha256(blob.encode("utf-8", errors="ignore")).hexdigest()
