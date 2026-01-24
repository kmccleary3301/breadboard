"""Session registry responsible for tracking active and historical runs."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, Optional, Set

from .persistence import SessionIndex, list_event_log_sessions, rehydrate_session_from_event_log

from .events import SessionEvent
from .models import SessionStatus, SessionSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionRecord:
    session_id: str
    status: SessionStatus
    created_at: datetime = field(default_factory=_utcnow)
    last_activity_at: datetime = field(default_factory=_utcnow)
    logging_dir: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    completion_summary: Optional[Dict[str, Any]] = None
    reward_summary: Optional[Dict[str, Any]] = None
    event_queue: "asyncio.Queue[Optional[SessionEvent]]" = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )
    event_log: Deque[SessionEvent] = field(default_factory=lambda: deque(maxlen=1000))
    event_seq: int = 0
    event_log_sink: Optional[Any] = None
    session_jsonl_writer: Optional[Any] = None
    subscribers: Set["asyncio.Queue[Optional[SessionEvent]]"] = field(default_factory=set, repr=False)
    dispatch_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)
    dispatcher_task: Optional[asyncio.Task] = None
    runner: Any = None  # Populated with SessionRunner once started

    def to_summary(self) -> SessionSummary:
        model = None
        mode = None
        if isinstance(self.metadata, dict):
            model = self.metadata.get("model")
            mode = self.metadata.get("mode")
        return SessionSummary(
            session_id=self.session_id,
            status=self.status,
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            model=model,
            mode=mode,
            completion_summary=self.completion_summary,
            reward_summary=self.reward_summary,
            logging_dir=self.logging_dir,
            metadata=self.metadata or None,
        )


class SessionRegistry:
    """In-memory registry; replaceable with persistent implementation later."""

    def __init__(self, *, index: Optional[SessionIndex] = None) -> None:
        self._records: Dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()
        self._index = index

    async def create(self, record: SessionRecord) -> SessionRecord:
        async with self._lock:
            self._records[record.session_id] = record
        await self._maybe_index(record)
        return record

    async def get(self, session_id: str) -> Optional[SessionRecord]:
        async with self._lock:
            return self._records.get(session_id)

    async def list(self, *, use_index: bool = False) -> Iterable[SessionSummary]:
        async with self._lock:
            summaries = [record.to_summary() for record in self._records.values()]
        if summaries or not use_index or self._index is None:
            return summaries
        try:
            return await self._index.list_summaries()
        except Exception:
            return summaries

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if not record:
                return
            record.status = status
            record.last_activity_at = _utcnow()
        await self._maybe_index(record)

    async def update_metadata(
        self,
        session_id: str,
        *,
        logging_dir: Optional[str] = None,
        completion_summary: Optional[Dict[str, Any]] = None,
        reward_summary: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if not record:
                return
            if logging_dir:
                record.logging_dir = logging_dir
            if completion_summary is not None:
                record.completion_summary = completion_summary
            if reward_summary is not None:
                record.reward_summary = reward_summary
            if metadata is not None:
                record.metadata = metadata
            record.last_activity_at = _utcnow()
        await self._maybe_index(record)

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._records.pop(session_id, None)
        if self._index is not None:
            try:
                await self._index.delete(session_id)
            except Exception:
                pass

    async def _maybe_index(self, record: SessionRecord) -> None:
        if self._index is None:
            return
        try:
            await self._index.write_summary(record.to_summary())
        except Exception:
            pass

    async def bootstrap_from_event_logs(self, base_dir: str, *, populate_replay: bool = False) -> int:
        session_ids = list_event_log_sessions(base_dir)
        created = 0
        for session_id in session_ids:
            replay = rehydrate_session_from_event_log(base_dir, session_id)
            events = replay.events
            record = SessionRecord(
                session_id=str(session_id),
                status=replay.summary.status,
                created_at=replay.summary.created_at,
                last_activity_at=replay.summary.last_activity_at,
                logging_dir=replay.summary.logging_dir,
                metadata=replay.summary.metadata or {},
                completion_summary=replay.summary.completion_summary,
                reward_summary=replay.summary.reward_summary,
            )
            if events:
                record.event_seq = max((e.seq or 0) for e in events)
                if populate_replay:
                    record.event_log = deque(events[-1000:], maxlen=1000)
            async with self._lock:
                if session_id in self._records:
                    continue
                self._records[session_id] = record
                created += 1
        return created

    async def bootstrap_from_index(
        self,
        *,
        eventlog_dir: Optional[str] = None,
        enrich_from_eventlog: bool = False,
    ) -> int:
        if self._index is None:
            return 0
        summaries = await self._index.list_summaries()
        created = 0
        for summary in summaries:
            session_id = summary.session_id
            async with self._lock:
                if session_id in self._records:
                    continue
            record = SessionRecord(
                session_id=session_id,
                status=summary.status,
                created_at=summary.created_at,
                last_activity_at=summary.last_activity_at,
                logging_dir=summary.logging_dir,
                metadata=summary.metadata or {},
                completion_summary=summary.completion_summary,
                reward_summary=summary.reward_summary,
            )
            if enrich_from_eventlog and eventlog_dir:
                try:
                    replay = rehydrate_session_from_event_log(eventlog_dir, session_id)
                    if replay.events:
                        record.status = replay.summary.status
                        record.last_activity_at = replay.summary.last_activity_at
                        if replay.summary.created_at < record.created_at:
                            record.created_at = replay.summary.created_at
                        record.event_seq = max((e.seq or 0) for e in replay.events)
                except Exception:
                    pass
            async with self._lock:
                if session_id in self._records:
                    continue
                self._records[session_id] = record
                created += 1
        return created
