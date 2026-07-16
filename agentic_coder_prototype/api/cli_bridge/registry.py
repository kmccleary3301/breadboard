"""Session registry responsible for tracking active and historical runs."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, Optional, Set, Tuple

from .events import SessionEvent
from .models import SessionStatus, SessionSummary, TurnAdmission


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TurnRecord:
    """Engine-owned identity and admission state for one accepted turn."""

    input_id: str
    turn_id: str
    client_message_id: str
    content: str
    attachments: Tuple[str, ...]
    original_disposition: str
    state: str
    cancellation_requested: bool = False
    cancellation_reason: Optional[str] = None
    execution_committed: bool = False
    terminal_outcome: Optional[str] = None


@dataclass(frozen=True)
class CancellationRecord:
    """Stable acknowledgement facts for one targeted cancellation request."""

    cancellation_request_id: str
    cancellation_request_key: str
    turn_id: str
    input_id: str
    reason: str
    original_disposition: str


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
    subscribers: Set["asyncio.Queue[Optional[SessionEvent]]"] = field(default_factory=set, repr=False)
    dispatch_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)
    dispatcher_task: Optional[asyncio.Task] = None
    runner: Any = None  # Populated with SessionRunner once started
    turn_admission: TurnAdmission = TurnAdmission.IDLE
    active_turn_id: Optional[str] = None
    queued_turn_ids: Deque[str] = field(default_factory=deque, repr=False)
    turns_by_id: Dict[str, TurnRecord] = field(default_factory=dict, repr=False)
    submissions_by_key: Dict[str, TurnRecord] = field(default_factory=dict, repr=False)
    cancellations_by_key: Dict[str, CancellationRecord] = field(default_factory=dict, repr=False)
    admission_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)

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
            turn_admission=self.turn_admission,
            active_turn_id=self.active_turn_id,
            queued_turn_count=len(self.queued_turn_ids),
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
