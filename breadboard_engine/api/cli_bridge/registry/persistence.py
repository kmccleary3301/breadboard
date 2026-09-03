from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    Optional,
)

from breadboard.product.runtime.artifacts import ArtifactRef

from ..events import EventType, SessionEvent
from ..models import (
    SessionStatus,
    SessionSummary,
    TurnAdmission,
)

from .records import (
    _STATE_SCHEMA_VERSION,
    _TERMINAL_EVENT_TYPES,
    _retained_model_id,
    _utcnow,
    CancellationRecord,
    SessionRecord,
    SessionRecordDeletedError,
    TurnRecord,
    cancellation_body_digest,
    identity_digest,
    submission_body_digest,
)

_TURN_COMPLETED_FIELDS = {
    "exchange_ref",
    "finish_reason",
    "output_emitted",
    "raw_provider_finish",
    "usage",
}
_PROVIDER_FINISH_REASONS = {"stop", "length", "toolUse", "error", "aborted"}
_PROVIDER_USAGE_FIELDS = {
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "totalTokens",
    "reasoningTokens",
    "extensions",
}


def _retained_artifact_manifest_ref(value: Any) -> Dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "digest",
        "size_bytes",
        "media_type",
    }:
        raise ValueError("retained artifact manifest reference is invalid")
    return ArtifactRef(
        digest=value["digest"],
        size_bytes=value["size_bytes"],
        media_type=value["media_type"],
    ).as_dict()


def _retained_turn_completed_payload(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("turn_completed payload must be an object")
    unknown = set(value) - _TURN_COMPLETED_FIELDS
    if unknown:
        raise ValueError("turn_completed payload has unknown fields")
    payload: Dict[str, Any] = {}
    if "exchange_ref" in value:
        exchange_ref = value["exchange_ref"]
        if (
            not isinstance(exchange_ref, dict)
            or set(exchange_ref) != {"exchange_id", "schema_version"}
            or exchange_ref.get("schema_version") != "bb.provider_exchange.v2"
            or not isinstance(exchange_ref.get("exchange_id"), str)
            or not 1 <= len(exchange_ref["exchange_id"]) <= 256
        ):
            raise ValueError("turn_completed payload has an invalid exchange_ref")
        payload["exchange_ref"] = dict(exchange_ref)
    if "finish_reason" in value:
        if value["finish_reason"] not in _PROVIDER_FINISH_REASONS:
            raise ValueError("turn_completed payload has an invalid finish_reason")
        payload["finish_reason"] = value["finish_reason"]
    if "output_emitted" in value:
        if not isinstance(value["output_emitted"], bool):
            raise ValueError("turn_completed payload has an invalid output_emitted")
        payload["output_emitted"] = value["output_emitted"]
    if "raw_provider_finish" in value:
        raw_finish = value["raw_provider_finish"]
        if (
            not isinstance(raw_finish, str)
            or not 1 <= len(raw_finish) <= 128
            or not raw_finish.isascii()
            or not raw_finish[0].isalnum()
            or any(
                not (character.isalnum() or character in "._:/-")
                for character in raw_finish
            )
        ):
            raise ValueError(
                "turn_completed payload has an invalid raw_provider_finish"
            )
        payload["raw_provider_finish"] = raw_finish
    if "usage" in value:
        usage = value["usage"]
        if not isinstance(usage, dict) or set(usage) - _PROVIDER_USAGE_FIELDS:
            raise ValueError("turn_completed payload has invalid usage")
        retained_usage: Dict[str, Any] = {}
        for field_name in _PROVIDER_USAGE_FIELDS - {"extensions"}:
            if field_name not in usage:
                continue
            token_count = usage[field_name]
            if (
                isinstance(token_count, bool)
                or not isinstance(token_count, int)
                or token_count < 0
            ):
                raise ValueError("turn_completed payload has invalid usage")
            retained_usage[field_name] = token_count
        if "extensions" in usage:
            extensions = usage["extensions"]
            if not isinstance(extensions, dict) or any(
                not isinstance(key, str) for key in extensions
            ):
                raise ValueError("turn_completed payload has invalid usage extensions")
            try:
                encoded = json.dumps(
                    extensions,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (TypeError, ValueError):
                raise ValueError(
                    "turn_completed payload has invalid usage extensions"
                ) from None
            if len(encoded.encode("utf-8")) > 65536:
                raise ValueError(
                    "turn_completed payload has oversized usage extensions"
                )
            retained_usage["extensions"] = json.loads(encoded)
        payload["usage"] = retained_usage
    return payload


def _retained_terminal_payload(event_type: EventType, value: Any) -> Dict[str, Any]:
    if event_type is EventType.TURN_COMPLETED:
        return _retained_turn_completed_payload(value)
    if not isinstance(value, dict):
        raise ValueError("terminal event payload must be an object")
    if event_type is EventType.TURN_CANCELLED:
        reason = str(value.get("reason") or "user_requested")
        return {
            "reason": (
                reason
                if reason
                in {"user_requested", "timeout", "superseded", "stop_requested"}
                else "user_requested"
            )
        }
    error = value.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    safe_code = str(code or "turn_execution_failed")
    if not safe_code.replace("_", "").replace("-", "").replace(".", "").isalnum():
        safe_code = "turn_execution_failed"
    return {"error": {"code": safe_code[:128]}}


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

    async def resolve_session_id(self, candidate: str) -> str | None:
        """Return the canonical stored id for a case-insensitive candidate."""

        folded = candidate.casefold()
        async with self._lock:
            return next(
                (
                    session_id
                    for session_id in self._records
                    if session_id.casefold() == folded
                ),
                None,
            )


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
            if (
                record.product_session is not None
                and status is not record.projected_status()
            ):
                raise RuntimeError("bridge status disagrees with product Session")
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
            previous = (
                record.logging_dir,
                record.completion_summary,
                record.reward_summary,
                record.metadata,
                record.last_activity_at,
            )
            if logging_dir:
                record.logging_dir = logging_dir
            if completion_summary is not None:
                record.completion_summary = completion_summary
            if reward_summary is not None:
                record.reward_summary = reward_summary
            if metadata is not None:
                record.metadata = metadata
            record.last_activity_at = _utcnow()
            try:
                self._persist_record_locked(record)
            except Exception:
                (
                    record.logging_dir,
                    record.completion_summary,
                    record.reward_summary,
                    record.metadata,
                    record.last_activity_at,
                ) = previous
                raise

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
        cursor_event: SessionEvent | None = None,
    ) -> None:
        async with self._lock:
            if self._records.get(record.session_id) is not record:
                raise SessionRecordDeletedError(
                    f"session {record.session_id} was deleted before persistence"
                )
            if terminal_event is not None and self._state_root is None:
                raise RuntimeError("durable terminal retention is unavailable")
            head_event = terminal_event or cursor_event
            resolution_turn: TurnRecord | None = None
            resolution_was_committed = False
            if terminal_event is not None and terminal_event.turn_id is not None:
                resolution_turn = record.turns_by_id.get(terminal_event.turn_id)
                if resolution_turn is None or resolution_turn.terminal_outcome is None:
                    raise RuntimeError("terminal event does not resolve an admitted turn")
                resolution_was_committed = resolution_turn.terminal_resolution_committed

            previous_event_seq = record.event_seq
            previous_event_seq_value = head_event.seq if head_event is not None else None
            if head_event is not None:
                if head_event.seq is None:
                    record.event_seq += 1
                    head_event.seq = record.event_seq
                else:
                    record.event_seq = max(record.event_seq, int(head_event.seq))
            candidate = (
                self._retained_terminal_envelope(terminal_event)
                if terminal_event is not None
                else None
            )
            retained: Dict[str, Any] | None = None
            previous_replay_head_sequence = record.replay_head_sequence
            previous_replay_head_event_id = record.replay_head_event_id
            if head_event is not None:
                record.replay_head_sequence = int(head_event.seq)
                record.replay_head_event_id = head_event.event_id
            if candidate is not None and not any(
                item.get("id") == candidate.get("id")
                for item in record.terminal_event_envelopes
            ):
                retained = candidate
                record.terminal_event_envelopes.append(candidate)
            if resolution_turn is not None:
                resolution_turn.terminal_resolution_committed = True
            try:
                self._persist_record_locked(record)
            except Exception:
                record.event_seq = previous_event_seq
                if head_event is not None:
                    head_event.seq = previous_event_seq_value
                record.replay_head_sequence = previous_replay_head_sequence
                record.replay_head_event_id = previous_replay_head_event_id
                if retained is not None:
                    record.terminal_event_envelopes.remove(retained)
                if resolution_turn is not None:
                    resolution_turn.terminal_resolution_committed = (
                        resolution_was_committed
                    )
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
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize_record(record)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(
                payload,
                handle,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _durable_replay_head(record: SessionRecord) -> tuple[int, str | None]:
        if record.replay_head_sequence > 0 and record.replay_head_event_id:
            return record.replay_head_sequence, record.replay_head_event_id
        for envelope in reversed(record.terminal_event_envelopes):
            sequence = envelope.get("seq")
            event_id = envelope.get("id")
            if (
                isinstance(sequence, int)
                and not isinstance(sequence, bool)
                and sequence > 0
                and isinstance(event_id, str)
                and event_id
            ):
                return sequence, event_id
        return 0, None

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
            cancellations.append(
                self._serialize_cancellation(identity_digest(key), cancellation)
            )
        known_cancellation_digests = {item["key_digest"] for item in cancellations}
        for key_digest, cancellation in record.cancellations_by_key_digest.items():
            if key_digest not in known_cancellation_digests:
                cancellations.append(
                    self._serialize_cancellation(key_digest, cancellation)
                )

        turns = []
        for turn in record.turns_by_id.values():
            serialized_turn = {
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
            if turn.logical_event_count_before_admission is not None:
                content_hash = turn.logical_input_content_hash
                if (
                    not isinstance(content_hash, str)
                    or len(content_hash) != 71
                    or not content_hash.startswith("sha256:")
                    or any(character not in "0123456789abcdef" for character in content_hash[7:])
                ):
                    raise ValueError("retained turn content hash is invalid")
                serialized_turn["content_hash"] = content_hash
                serialized_turn["attachments"] = list(turn.attachments)
                serialized_turn["logical_event_count_before_admission"] = (
                    turn.logical_event_count_before_admission
                )
            turns.append(serialized_turn)
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        role_lock = metadata.get("model_role_lock")
        if not isinstance(role_lock, dict):
            role_lock = None
        active_role = metadata.get("active_model_role")
        durable_head_sequence, durable_head_event_id = self._durable_replay_head(record)
        return {
            "schema_version": _STATE_SCHEMA_VERSION,
            "session": {
                "session_id": record.session_id,
                "status": record.status.value,
                "created_at": record.created_at.isoformat(),
                "last_activity_at": record.last_activity_at.isoformat(),
                "event_seq": record.event_seq,
                "replay_head_sequence": durable_head_sequence,
                "event_head_id": durable_head_event_id,
                "model": _retained_model_id(metadata.get("model")),
                "mode": (
                    str(metadata["mode"]).strip()
                    if isinstance(metadata.get("mode"), str)
                    and str(metadata["mode"]).strip()
                    else None
                ),
                "config_path": str(metadata.get("config_path") or ""),
                "workspace": str(metadata.get("workspace") or ""),
                "session_event_root": str(metadata.get("session_event_root") or ""),
                "durable_product_workspace": str(
                    metadata.get("durable_product_workspace") or ""
                ),
                "artifact_manifest_ref": _retained_artifact_manifest_ref(
                    metadata.get("artifact_manifest_ref")
                ),
                "model_role_lock": role_lock,
                "active_model_role": str(active_role) if active_role else None,
                "permission_mode": (
                    str(metadata.get("permission_mode") or "").strip().lower()
                    if str(metadata.get("permission_mode") or "").strip().lower()
                    in {"prompt", "ask", "interactive", "configured"}
                    else None
                ),
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
            "body_digest": turn.body_digest
            or submission_body_digest(turn.content, turn.attachments),
            "input_id": turn.input_id,
            "turn_id": turn.turn_id,
            "original_disposition": turn.original_disposition,
        }

    @staticmethod
    def _serialize_cancellation(
        key_digest: str, cancellation: CancellationRecord
    ) -> Dict[str, Any]:
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
        payload = _retained_terminal_payload(event.type, event.payload)
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
        payload = _retained_terminal_payload(event_type, envelope.get("payload"))
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
        session_id = str(session["session_id"])
        persisted_event_seq = int(session.get("event_seq") or 0)
        persisted_replay_head_sequence = int(
            session.get("replay_head_sequence", persisted_event_seq) or 0
        )
        persisted_head_event_id = session.get("event_head_id")
        if (
            persisted_replay_head_sequence < 0
            or persisted_replay_head_sequence > persisted_event_seq
        ):
            raise ValueError("retained replay head sequence is invalid")
        if persisted_head_event_id is not None and (
            not isinstance(persisted_head_event_id, str) or not persisted_head_event_id
        ):
            raise ValueError("retained replay head identity is invalid")
        if persisted_replay_head_sequence == 0 and persisted_head_event_id is not None:
            raise ValueError("empty retained replay has a head identity")
        if persisted_replay_head_sequence > 0 and persisted_head_event_id is None:
            raise ValueError("retained replay head has no identity")
        model = _retained_model_id(session.get("model"))
        metadata: Dict[str, Any] = {}
        if model is not None:
            metadata["model"] = model
        mode = session.get("mode")
        if mode is not None:
            if not isinstance(mode, str) or not mode.strip():
                raise ValueError("retained session mode is invalid")
            metadata["mode"] = mode.strip()
        if session.get("config_path"):
            metadata["config_path"] = str(session["config_path"])
        if session.get("workspace"):
            metadata["workspace"] = str(session["workspace"])
        event_root = session.get("session_event_root")
        if event_root:
            if not isinstance(event_root, str) or not Path(event_root).is_absolute():
                raise ValueError("retained session event root is invalid")
            metadata["session_event_root"] = event_root
        durable_product_workspace = session.get("durable_product_workspace")
        if durable_product_workspace:
            if (
                not isinstance(durable_product_workspace, str)
                or not Path(durable_product_workspace).is_absolute()
            ):
                raise ValueError("retained durable product workspace is invalid")
            metadata["durable_product_workspace"] = durable_product_workspace
        artifact_manifest_ref = _retained_artifact_manifest_ref(
            session.get("artifact_manifest_ref")
        )
        if artifact_manifest_ref is not None:
            metadata["artifact_manifest_ref"] = artifact_manifest_ref
        permission_mode = str(session.get("permission_mode") or "").strip().lower()
        if permission_mode in {"prompt", "ask", "interactive", "configured"}:
            metadata["permission_mode"] = permission_mode
        role_lock = session.get("model_role_lock")
        if role_lock is not None:
            if not isinstance(role_lock, dict):
                raise ValueError("retained model-role lock is not an object")
            from ....model_roles import (
                select_role_target,
                validate_model_role_lock,
            )

            restored = validate_model_role_lock(role_lock)
            active_role = str(session.get("active_model_role") or "").strip() or str(
                (restored.get("defaults") or {}).get("role") or ""
            )
            if active_role not in restored["roles"]:
                raise ValueError(
                    "retained active model role is not present in its lock"
                )
            active_target = select_role_target(restored, active_role)
            metadata["model_role_lock"] = restored.as_dict()
            metadata["model_role_lock_hash"] = restored.lock_hash
            metadata["active_model_role"] = active_role
            metadata["model"] = str(active_target["route_id"])
        record = SessionRecord(
            session_id=session_id,
            status=SessionStatus(str(session["status"])),
            created_at=datetime.fromisoformat(str(session["created_at"])),
            last_activity_at=datetime.fromisoformat(str(session["last_activity_at"])),
            event_seq=persisted_event_seq,
            replay_history_partial=bool(persisted_event_seq),
            replay_head_event_id=persisted_head_event_id,
            replay_head_sequence=persisted_replay_head_sequence,
            metadata=metadata,
            loaded_from_retained_state=True,
        )
        for item in payload.get("turns") or []:
            marker = item.get("logical_event_count_before_admission")
            if marker is not None and (
                type(marker) is not int or marker < 1
            ):
                raise ValueError("retained turn journal position is invalid")
            if marker is not None and (
                "content_hash" not in item or "attachments" not in item
            ):
                raise ValueError("retained turn admission payload is incomplete")
            content_hash = item.get("content_hash")
            if marker is not None and (
                not isinstance(content_hash, str)
                or len(content_hash) != 71
                or not content_hash.startswith("sha256:")
                or any(
                    character not in "0123456789abcdef"
                    for character in content_hash[7:]
                )
            ):
                raise ValueError("retained turn content hash is invalid")
            content = ""
            attachments = item.get("attachments", [])
            if marker is None:
                content_hash = None
            if not isinstance(attachments, list) or any(
                not isinstance(attachment, str) or not attachment.strip()
                for attachment in attachments
            ):
                raise ValueError("retained turn attachments are invalid")
            turn = TurnRecord(
                input_id=str(item["input_id"]),
                turn_id=str(item["turn_id"]),
                client_message_id="",
                content=content,
                attachments=tuple(attachments),
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
                logical_event_count_before_admission=marker,
                logical_input_content_hash=content_hash,
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
        if record.replay_head_sequence > 0 and record.replay_head_event_id is None:
            if record.event_log:
                legacy_head = record.event_log[-1]
                record.replay_head_sequence = int(legacy_head.seq or 0)
                record.replay_head_event_id = legacy_head.event_id
            else:
                record.event_seq = 0
                record.replay_head_sequence = 0
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
