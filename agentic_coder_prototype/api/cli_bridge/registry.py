"""Session registry responsible for tracking active and historical runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Optional, Tuple

from .events import EventType, SessionEvent, replay_retention_facts
from .models import SessionStatus, SessionSummary, TurnAdmission


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

_STATE_SCHEMA_VERSION = "bb.cli_bridge.session_state.v1"
_TERMINAL_EVENT_TYPES = {
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
    EventType.TURN_CANCELLED,
}


def _digest_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def identity_digest(value: str) -> str:
    return _digest_payload({"identity": str(value)})


def submission_body_digest(content: str, attachments: Tuple[str, ...]) -> str:
    return _digest_payload({"content": content, "attachments": list(attachments)})


def cancellation_body_digest(turn_id: str, reason: str) -> str:
    return _digest_payload({"turn_id": turn_id, "reason": reason})

class SessionRecordDeletedError(RuntimeError):
    """Raised when an operation tries to persist a deleted session record."""

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
    body_digest: Optional[str] = None


@dataclass(frozen=True)
class CancellationRecord:
    """Stable acknowledgement facts for one targeted cancellation request."""

    cancellation_request_id: str
    cancellation_request_key: str
    turn_id: str
    input_id: str
    reason: str
    original_disposition: str
    body_digest: Optional[str] = None


@dataclass(eq=False)
class SubscriberState:
    """Delivery state for one bounded stream subscription."""

    queue: "asyncio.Queue[Optional[SessionEvent]]"
    last_delivered_sequence: Optional[int] = None
    last_delivered_event_id: Optional[str] = None
    gapped: bool = False


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
    event_log: Deque[SessionEvent] = field(default_factory=deque)
    event_seq: int = 0
    replay_history_partial: bool = False
    terminal_event_envelopes: list[Dict[str, Any]] = field(default_factory=list, repr=False)
    subscribers: Dict["asyncio.Queue[Optional[SessionEvent]]", SubscriberState] = field(
        default_factory=dict,
        repr=False,
    )
    dispatch_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)
    dispatcher_task: Optional[asyncio.Task] = None
    runner: Any = None  # Populated with SessionRunner once started
    turn_admission: TurnAdmission = TurnAdmission.IDLE
    active_turn_id: Optional[str] = None
    queued_turn_ids: Deque[str] = field(default_factory=deque, repr=False)
    turns_by_id: Dict[str, TurnRecord] = field(default_factory=dict, repr=False)
    submissions_by_key: Dict[str, TurnRecord] = field(default_factory=dict, repr=False)
    submissions_by_key_digest: Dict[str, TurnRecord] = field(default_factory=dict, repr=False)
    cancellations_by_key: Dict[str, CancellationRecord] = field(default_factory=dict, repr=False)
    cancellations_by_key_digest: Dict[str, CancellationRecord] = field(default_factory=dict, repr=False)
    lifecycle_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)
    deleting: bool = field(default=False, repr=False)
    admission_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)

    def to_summary(self) -> SessionSummary:
        model = None
        mode = None
        if isinstance(self.metadata, dict):
            model = self.metadata.get("model")
            mode = self.metadata.get("mode")
        replay = replay_retention_facts(
            self.event_log,
            head_sequence=self.event_seq,
            retained_history_partial=self.replay_history_partial,
        )
        terminal_turns = [
            {
                "input_id": turn.input_id,
                "turn_id": turn.turn_id,
                "outcome": turn.terminal_outcome,
                "original_disposition": turn.original_disposition,
            }
            for turn in self.turns_by_id.values()
            if turn.terminal_outcome is not None
        ]
        terminal_turns.sort(key=lambda item: item["turn_id"])
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
            replay_retention=replay["replayRetention"],
            earliest_retained_sequence=replay["earliestRetainedSequence"],
            earliest_retained_event_id=replay["earliestRetainedEventId"],
            head_sequence=replay["headSequence"],
            head_event_id=replay["headEventId"],
            retained_history=replay["retainedHistory"],
            session_replay_contract_digest=replay["sessionReplayContractDigest"],
            terminal_turns=terminal_turns,
            terminal_event_envelopes=list(self.terminal_event_envelopes),
        )


class SessionRegistry:
    """Registry with optional atomic, secret-safe retained session state."""

    def __init__(self, state_root: str | Path | None = None) -> None:
        self._records: Dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()
        self._state_root = Path(state_root).resolve() if state_root is not None else None
        if self._state_root is not None:
            self._state_root.mkdir(parents=True, exist_ok=True)
            self._load_retained_records()

    async def create(self, record: SessionRecord) -> SessionRecord:
        async with self._lock:
            self._records[record.session_id] = record
            self._persist_record_locked(record)
        return record

    async def get(self, session_id: str) -> Optional[SessionRecord]:
        async with self._lock:
            return self._records.get(session_id)
    async def records(self) -> list[SessionRecord]:
        async with self._lock:
            return list(self._records.values())

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
            self._persist_record_locked(record)

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
            self._persist_record_locked(record)

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._records.pop(session_id, None)
            path = self._state_path(session_id)
            if path is not None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    async def persist(
        self,
        record: SessionRecord,
        *,
        terminal_event: SessionEvent | None = None,
    ) -> None:
        async with self._lock:
            if self._records.get(record.session_id) is not record:
                raise SessionRecordDeletedError(
                    f"session {record.session_id} was deleted before persistence"
                )
            retained: Dict[str, Any] | None = None
            if terminal_event is not None:
                candidate = self._retained_terminal_envelope(terminal_event)
                if candidate is not None and not any(
                    item.get("id") == candidate.get("id")
                    for item in record.terminal_event_envelopes
                ):
                    retained = candidate
                    record.terminal_event_envelopes.append(candidate)
            try:
                self._persist_record_locked(record)
            except Exception:
                if retained is not None:
                    record.terminal_event_envelopes.remove(retained)
                raise

    def _state_path(self, session_id: str) -> Path | None:
        if self._state_root is None:
            return None
        filename = hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json"
        return self._state_root / filename

    def _persist_record_locked(self, record: SessionRecord) -> None:
        path = self._state_path(record.session_id)
        if path is None:
            return
        payload = self._serialize_record(record)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _serialize_record(self, record: SessionRecord) -> Dict[str, Any]:
        submissions = []
        for key, turn in record.submissions_by_key.items():
            submissions.append(self._serialize_submission(identity_digest(key), turn))
        known_submission_digests = {item["key_digest"] for item in submissions}
        for key_digest, turn in record.submissions_by_key_digest.items():
            if key_digest not in known_submission_digests:
                submissions.append(self._serialize_submission(key_digest, turn))

        cancellations = []
        for key, cancellation in record.cancellations_by_key.items():
            cancellations.append(self._serialize_cancellation(identity_digest(key), cancellation))
        known_cancellation_digests = {item["key_digest"] for item in cancellations}
        for key_digest, cancellation in record.cancellations_by_key_digest.items():
            if key_digest not in known_cancellation_digests:
                cancellations.append(self._serialize_cancellation(key_digest, cancellation))

        turns = [
            {
                "input_id": turn.input_id,
                "turn_id": turn.turn_id,
                "original_disposition": turn.original_disposition,
                "state": turn.state,
                "cancellation_requested": turn.cancellation_requested,
                "cancellation_reason": turn.cancellation_reason,
                "execution_committed": turn.execution_committed,
                "terminal_outcome": turn.terminal_outcome,
                "body_digest": turn.body_digest
                or submission_body_digest(turn.content, turn.attachments),
            }
            for turn in record.turns_by_id.values()
        ]
        return {
            "schema_version": _STATE_SCHEMA_VERSION,
            "session": {
                "session_id": record.session_id,
                "status": record.status.value,
                "created_at": record.created_at.isoformat(),
                "last_activity_at": record.last_activity_at.isoformat(),
                "event_seq": record.event_seq,
            },
            "turns": turns,
            "submissions": submissions,
            "cancellations": cancellations,
            "terminal_event_envelopes": list(record.terminal_event_envelopes),
        }

    @staticmethod
    def _serialize_submission(key_digest: str, turn: TurnRecord) -> Dict[str, Any]:
        return {
            "key_digest": key_digest,
            "body_digest": turn.body_digest or submission_body_digest(turn.content, turn.attachments),
            "input_id": turn.input_id,
            "turn_id": turn.turn_id,
            "original_disposition": turn.original_disposition,
        }

    @staticmethod
    def _serialize_cancellation(key_digest: str, cancellation: CancellationRecord) -> Dict[str, Any]:
        return {
            "key_digest": key_digest,
            "body_digest": cancellation.body_digest
            or cancellation_body_digest(cancellation.turn_id, cancellation.reason),
            "cancellation_request_id": cancellation.cancellation_request_id,
            "turn_id": cancellation.turn_id,
            "input_id": cancellation.input_id,
            "reason": cancellation.reason,
            "original_disposition": cancellation.original_disposition,
        }

    @staticmethod
    def _retained_terminal_envelope(event: SessionEvent) -> Dict[str, Any] | None:
        if event.type not in _TERMINAL_EVENT_TYPES:
            return None
        payload: Dict[str, Any] = {}
        if event.type is EventType.TURN_CANCELLED:
            reason = str(event.payload.get("reason") or "user_requested")
            payload["reason"] = reason if reason in {"user_requested", "timeout", "superseded"} else "user_requested"
        elif event.type is EventType.TURN_FAILED:
            error = event.payload.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            safe_code = str(code or "turn_execution_failed")
            if not safe_code.replace("_", "").replace("-", "").replace(".", "").isalnum():
                safe_code = "turn_execution_failed"
            payload["error"] = {"code": safe_code[:128]}
        return {
            "id": event.event_id,
            "seq": event.seq,
            "stable_cursor": True,
            "type": event.type.value,
            "session_id": event.session_id,
            "timestamp_ms": int(event.created_at),
            "protocol_version": event.asdict()["protocol_version"],
            "input_id": event.input_id,
            "turn_id": event.turn_id,
            "payload": payload,
        }

    def _load_retained_records(self) -> None:
        assert self._state_root is not None
        for path in sorted(self._state_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = self._deserialize_record(payload)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            self._records[record.session_id] = record

    def _deserialize_record(self, payload: Dict[str, Any]) -> SessionRecord:
        if payload.get("schema_version") != _STATE_SCHEMA_VERSION:
            raise ValueError("unsupported session-state schema")
        session = payload["session"]
        record = SessionRecord(
            session_id=str(session["session_id"]),
            status=SessionStatus(str(session["status"])),
            created_at=datetime.fromisoformat(str(session["created_at"])),
            last_activity_at=datetime.fromisoformat(str(session["last_activity_at"])),
            event_seq=int(session.get("event_seq") or 0),
            replay_history_partial=bool(session.get("event_seq")),
        )
        for item in payload.get("turns") or []:
            turn = TurnRecord(
                input_id=str(item["input_id"]),
                turn_id=str(item["turn_id"]),
                client_message_id="",
                content="",
                attachments=(),
                original_disposition=str(item["original_disposition"]),
                state=str(item["state"]),
                cancellation_requested=bool(item.get("cancellation_requested")),
                cancellation_reason=item.get("cancellation_reason"),
                execution_committed=bool(item.get("execution_committed")),
                terminal_outcome=item.get("terminal_outcome"),
                body_digest=str(item["body_digest"]),
            )
            record.turns_by_id[turn.turn_id] = turn
        for item in payload.get("submissions") or []:
            turn = record.turns_by_id[str(item["turn_id"])]
            record.submissions_by_key_digest[str(item["key_digest"])] = turn
        for item in payload.get("cancellations") or []:
            cancellation = CancellationRecord(
                cancellation_request_id=str(item["cancellation_request_id"]),
                cancellation_request_key="",
                turn_id=str(item["turn_id"]),
                input_id=str(item["input_id"]),
                reason=str(item["reason"]),
                original_disposition=str(item["original_disposition"]),
                body_digest=str(item["body_digest"]),
            )
            record.cancellations_by_key_digest[str(item["key_digest"])] = cancellation
        terminal_events = payload.get("terminal_event_envelopes")
        if isinstance(terminal_events, list):
            record.terminal_event_envelopes = [
                dict(item) for item in terminal_events if isinstance(item, dict)
            ]
        record.turn_admission = TurnAdmission.IDLE
        record.active_turn_id = None
        return record
