from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import math
import os
import secrets
import time
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, Optional, Tuple, TypeVar

from ..engine_identity_config import EngineProcessIdentity, LaunchBootstrapVerifier
from ..events import EventType, SessionEvent, replay_retention_facts
from ..models import (
    BeginControlDrainRequest, BootstrapChallengeRequest, BootstrapChallengeResponse,
    ClientLeaseRequest, ClientRegisterRequest, ClientRegistrationResponse,
    DrainControlRequest, DrainControlResponse, GracefulControlResultRequest,
    HardSignalCommitRequest, HardSignalPreparationResponse, HardSignalPermitResponse,
    HardSignalOutcomeRequest, HardSignalPrepareRequest, OwnerAcquireRequest,
    OwnerLeaseRequest, OwnerLeaseResponse, SessionStatus, SessionSummary,
    TurnAdmission,
)

from .records import (
    _STATE_SCHEMA_VERSION, _TERMINAL_EVENT_TYPES, _retained_model_id, _utcnow,
    CancellationRecord, EventType, SessionRecord, SessionRecordDeletedError,
    SessionEvent, SessionStatus, TurnAdmission, TurnRecord, cancellation_body_digest,
    identity_digest, submission_body_digest,
)


class PersistenceMixin:
    """Retained session persistence and basic record operations."""

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
            if terminal_event is not None and self._state_root is None:
                raise RuntimeError("durable terminal retention is unavailable")
            retained: Dict[str, Any] | None = None
            resolution_turn: TurnRecord | None = None
            resolution_was_committed = False
            if terminal_event is not None:
                candidate = self._retained_terminal_envelope(terminal_event)
                if candidate is not None and not any(
                    item.get("id") == candidate.get("id")
                    for item in record.terminal_event_envelopes
                ):
                    retained = candidate
                    record.terminal_event_envelopes.append(candidate)
                if terminal_event.turn_id is not None:
                    resolution_turn = record.turns_by_id.get(terminal_event.turn_id)
                    if resolution_turn is None or resolution_turn.terminal_outcome is None:
                        raise RuntimeError("terminal event does not resolve an admitted turn")
                    resolution_was_committed = (
                        resolution_turn.terminal_resolution_committed
                    )
                    resolution_turn.terminal_resolution_committed = True
            try:
                self._persist_record_locked(record)
            except Exception:
                if retained is not None:
                    record.terminal_event_envelopes.remove(retained)
                if resolution_turn is not None:
                    resolution_turn.terminal_resolution_committed = resolution_was_committed
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
                "terminal_resolution_committed": turn.terminal_resolution_committed,
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
                "model": _retained_model_id(record.metadata.get("model")),
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

    @staticmethod
    def _rehydrate_terminal_event(
        envelope: Dict[str, Any],
        *,
        session_id: str,
        head_sequence: int,
    ) -> SessionEvent:
        event_type = EventType(str(envelope["type"]))
        if event_type not in _TERMINAL_EVENT_TYPES:
            raise ValueError("retained event is not terminal")
        if envelope.get("stable_cursor") is not True:
            raise ValueError("retained terminal event is not cursor-stable")
        if str(envelope.get("session_id") or "") != session_id:
            raise ValueError("retained terminal event has the wrong session")
        event_id = str(envelope.get("id") or "")
        if not event_id:
            raise ValueError("retained terminal event has no identity")
        sequence = envelope.get("seq")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or sequence > head_sequence
        ):
            raise ValueError("retained terminal event has an invalid sequence")
        timestamp_ms = envelope.get("timestamp_ms")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
            raise ValueError("retained terminal event has an invalid timestamp")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("retained terminal event has an invalid payload")
        input_id = envelope.get("input_id")
        turn_id = envelope.get("turn_id")
        if input_id is not None and not isinstance(input_id, str):
            raise ValueError("retained terminal event has an invalid input identity")
        if turn_id is not None and not isinstance(turn_id, str):
            raise ValueError("retained terminal event has an invalid turn identity")
        return SessionEvent(
            event_type,
            session_id,
            dict(payload),
            created_at=timestamp_ms,
            event_id=event_id,
            seq=sequence,
            stable_cursor=True,
            input_id=input_id,
            turn_id=turn_id,
        )


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
        model = _retained_model_id(session.get("model"))
        record = SessionRecord(
            session_id=str(session["session_id"]),
            status=SessionStatus(str(session["status"])),
            created_at=datetime.fromisoformat(str(session["created_at"])),
            last_activity_at=datetime.fromisoformat(str(session["last_activity_at"])),
            event_seq=int(session.get("event_seq") or 0),
            replay_history_partial=bool(session.get("event_seq")),
            metadata={"model": model} if model is not None else {},
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
                terminal_resolution_committed=bool(
                    item.get("terminal_resolution_committed")
                ),
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
            retained_ids: set[str] = set()
            retained_sequences: set[int] = set()
            rehydrated_events: list[SessionEvent] = []
            for item in terminal_events:
                if not isinstance(item, dict):
                    continue
                envelope = dict(item)
                event = self._rehydrate_terminal_event(
                    envelope,
                    session_id=record.session_id,
                    head_sequence=record.event_seq,
                )
                if event.event_id in retained_ids or event.seq in retained_sequences:
                    raise ValueError("retained terminal event identity is duplicated")
                retained_ids.add(event.event_id)
                retained_sequences.add(event.seq)
                record.terminal_event_envelopes.append(envelope)
                rehydrated_events.append(event)
            record.event_log.extend(
                sorted(rehydrated_events, key=lambda event: int(event.seq or 0))
            )
        committed_turn_ids = {
            str(item.get("turn_id"))
            for item in record.terminal_event_envelopes
            if item.get("turn_id") is not None
        }
        for turn_id, turn in record.turns_by_id.items():
            turn.terminal_resolution_committed = bool(
                turn.terminal_outcome is not None and turn_id in committed_turn_ids
            )
        record.turn_admission = TurnAdmission.IDLE
        record.active_turn_id = None
        return record
