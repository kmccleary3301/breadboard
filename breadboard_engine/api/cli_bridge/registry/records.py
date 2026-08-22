"""Session and event records shared by the CLI bridge registry."""

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
    BeginControlDrainRequest,
    BootstrapChallengeRequest,
    BootstrapChallengeResponse,
    ClientLeaseRequest,
    ClientRegisterRequest,
    ClientRegistrationResponse,
    DrainControlRequest,
    DrainControlResponse,
    GracefulControlResultRequest,
    HardSignalCommitRequest,
    HardSignalPreparationResponse,
    HardSignalPermitResponse,
    HardSignalOutcomeRequest,
    HardSignalPrepareRequest,
    OwnerAcquireRequest,
    OwnerLeaseRequest,
    OwnerLeaseResponse,
    SessionStatus,
    SessionSummary,
    TurnAdmission,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

_STATE_SCHEMA_VERSION = "bb.cli_bridge.session_state.v1"
CONTROL_REQUEST_ID_CAPACITY = 4096
_TERMINAL_EVENT_TYPES = {
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
    EventType.TURN_CANCELLED,
}


def _digest_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _retained_model_id(value: Any) -> str | None:
    if not isinstance(value, str) or not 3 <= len(value) <= 256:
        return None
    if value.startswith("/") or value.endswith("/") or "/" not in value or "://" in value:
        return None
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-:"
    )
    return value if all(character in allowed for character in value) else None


def identity_digest(value: str) -> str:
    return _digest_payload({"identity": str(value)})


def submission_body_digest(content: str, attachments: Tuple[str, ...]) -> str:
    return _digest_payload({"content": content, "attachments": list(attachments)})


def cancellation_body_digest(turn_id: str, reason: str) -> str:
    return _digest_payload({"turn_id": turn_id, "reason": reason})

class SessionRecordDeletedError(RuntimeError):
    """Raised when an operation tries to persist a deleted session record."""



class LifecycleAuthorityError(RuntimeError):
    """Typed secret-safe authority failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class _OwnerLease:
    generation: int
    credential_verifier: bytes = field(repr=False)
    expires_at_unix: float = 0.0
    released: bool = False


@dataclass
class _ClientRegistration:
    registration_id: str
    generation: int
    client_instance_id: str
    workspace_id: str
    lifecycle_mode: str
    first_slice_contract_id: str
    first_slice_schema_sha256: str
    registered_at_unix: float
    expires_at_unix: float
    credential_verifier: bytes = field(repr=False)
    detached: bool = False


@dataclass(frozen=True)
class _GracefulControlReceipt:
    result: str
    admission_epoch: int
    session_admission_open: bool
    turn_admission_open: bool
    registrations_open: bool
    signal_permitted: bool


@dataclass
class _DrainState:
    generation: int
    control_request_id: str
    operation_kind: str
    engine_instance_id: str
    engine_boot_id: str
    launch_id: str
    begin_owner_generation: int
    owner_generation: int
    requester_registration_id: str
    requester_registration_generation: int
    requester_client_instance_id: str
    expected_admission_epoch: int
    phase: str
    graceful_control_outcome: str | None = None
    graceful_control_receipt: _GracefulControlReceipt | None = None
    recovery_forbidden: bool = False
    hard_signal_authorization_id: str | None = None
    hard_signal_authorization_expires_at_unix: float | None = None
    hard_signal_attempt_committed: bool = False
    hard_signal_authorization_owner_generation: int | None = None
    hard_signal_outcome: str | None = None


_T = TypeVar("_T")
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
    terminal_resolution_committed: bool = False
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
    product_session: Any = None
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

    def projected_status(self) -> SessionStatus:
        if self.product_session is None:
            return self.status
        product_status = self.product_session.read_model.status
        projection = {
            "running": SessionStatus.RUNNING,
            "awaiting_approval": SessionStatus.RUNNING,
            "paused": SessionStatus.RUNNING,
            "completed": SessionStatus.COMPLETED,
            "failed": SessionStatus.FAILED,
            "canceled": SessionStatus.STOPPED,
        }
        try:
            return projection[product_status]
        except KeyError as error:
            raise RuntimeError(f"unknown product Session status: {product_status}") from error

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
            status=self.projected_status(),
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


