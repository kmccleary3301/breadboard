"""Session registry responsible for tracking active and historical runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

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
    event_queue: "asyncio.Queue[SessionEvent]" = field(default_factory=asyncio.Queue)
    runner: Any = None  # Populated with SessionRunner once started

    def to_summary(self) -> SessionSummary:
        return SessionSummary(
            session_id=self.session_id,
            status=self.status,
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            completion_summary=self.completion_summary,
            reward_summary=self.reward_summary,
            logging_dir=self.logging_dir,
            metadata=self.metadata or None,
        )


class SessionRegistry:
    """In-memory registry; replaceable with persistent implementation later."""

    def __init__(self) -> None:
        self._records: Dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: SessionRecord) -> SessionRecord:
        async with self._lock:
            self._records[record.session_id] = record
        return record

    async def get(self, session_id: str) -> Optional[SessionRecord]:
        async with self._lock:
            return self._records.get(session_id)

    async def list(self) -> Iterable[SessionSummary]:
        async with self._lock:
            return [record.to_summary() for record in self._records.values()]

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if not record:
                return
            record.status = status
            record.last_activity_at = _utcnow()

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

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._records.pop(session_id, None)
