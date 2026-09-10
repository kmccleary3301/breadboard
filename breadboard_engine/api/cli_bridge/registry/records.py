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
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
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
from breadboard.modules.author import CheckpointProposal, ModuleInput
from breadboard.modules.authority import AdmissionGrant
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.generations import GenerationAdmission
from breadboard_engine.execution.author_worker import (
    AuthorWorkerCleanupResult,
    AuthorWorkerResourceReceipt,
)



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

_STATE_SCHEMA_VERSION_V1 = "bb.cli_bridge.session_state.v1"
_STATE_SCHEMA_VERSION = "bb.cli_bridge.session_state.v2"
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


def submission_body_digest(
    content: str | None,
    attachments: Tuple[str, ...],
    module_input: ModuleInput | None = None,
) -> str:
    if module_input is None:
        if content is None:
            raise ValueError("text submission content is required")
        return _digest_payload({"content": content, "attachments": list(attachments)})
    if content is not None:
        raise ValueError("typed module input cannot have text content")
    if not isinstance(module_input, ModuleInput):
        raise TypeError("module_input must be a ModuleInput")
    return _digest_payload(
        {
            "module_input": module_input.to_dict(),
            "attachments": list(attachments),
        }
    )

def cancellation_body_digest(turn_id: str, reason: str) -> str:
    return _digest_payload({"turn_id": turn_id, "reason": reason})


_GENERATION_ADMISSION_FIELDS = frozenset(
    {
        "admission_id",
        "session_id",
        "target",
        "publication_revision",
        "generation_id",
        "source_ref",
        "lock_record",
        "controller_epoch",
        "work_id",
        "attempt_id",
        "grant_epoch",
        "status",
        "input_digest",
    }
)
_GENERATION_ADMISSION_STATUSES = frozenset(
    {"reserved", "materialized", "released"}
)


def _plain_generation_admission_value(value: Any) -> Any:
    """Detach JSON-compatible mappings used by the generation projection."""

    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("generation admission contains a non-string key")
        return {
            key: _plain_generation_admission_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plain_generation_admission_value(item) for item in value]
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("generation admission contains a non-JSON value") from error
    return value


def _generation_admission_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        value = as_dict()
    elif is_dataclass(value) and not isinstance(value, type):
        value = {
            item.name: getattr(value, item.name)
            for item in fields(value)
        }
    if not isinstance(value, Mapping):
        raise ValueError("generation admission is not a record")
    detached = _plain_generation_admission_value(value)
    if not isinstance(detached, dict) or set(detached) != _GENERATION_ADMISSION_FIELDS:
        raise ValueError("generation admission has invalid fields")
    for field_name in (
        "admission_id",
        "session_id",
        "generation_id",
        "source_ref",
        "work_id",
        "attempt_id",
        "input_digest",
    ):
        field_value = detached[field_name]
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(f"generation admission {field_name} is invalid")
    target = detached["target"]
    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise ValueError("generation admission target is invalid")
    publication_revision = detached["publication_revision"]
    if publication_revision is not None and (
        type(publication_revision) is not int or publication_revision < 0
    ):
        raise ValueError("generation admission publication revision is invalid")
    for field_name in ("controller_epoch", "grant_epoch"):
        field_value = detached[field_name]
        if type(field_value) is not int or field_value < 0:
            raise ValueError(f"generation admission {field_name} is invalid")
    if detached["status"] not in _GENERATION_ADMISSION_STATUSES:
        raise ValueError("generation admission status is invalid")
    lock_record = detached["lock_record"]
    if not isinstance(lock_record, dict) or not lock_record:
        raise ValueError("generation admission lock record is invalid")
    lock_id = lock_record.get("lock_id")
    if lock_id is not None and lock_id != detached["generation_id"]:
        raise ValueError("generation admission lock identity is contradictory")
    return detached


def _serialize_generation_admission(value: Any) -> dict[str, Any] | None:
    """Return a strict detached v2 projection for a lifecycle admission."""

    return _generation_admission_dict(value)


def _deserialize_generation_admission(
    value: Any,
    *,
    session_id: str,
) -> GenerationAdmission | None:
    """Validate and restore the lifecycle owner's immutable admission value."""

    detached = _generation_admission_dict(value)
    if detached is None:
        return None
    if detached["session_id"] != session_id:
        raise ValueError("generation admission session identity is contradictory")
    try:
        lock = EffectiveHarnessLock._from_record(detached["lock_record"])
        detached["lock_record"] = lock
        return GenerationAdmission(**detached)
    except (TypeError, ValueError) as error:
        raise ValueError("retained generation admission is invalid") from error


def _generation_admission_identity(value: Any) -> tuple[Any, ...] | None:
    detached = _generation_admission_dict(value)
    if detached is None:
        return None
    return tuple(
        detached[field_name]
        for field_name in sorted(_GENERATION_ADMISSION_FIELDS - {"status"})
    )


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
@dataclass
class ModuleWorkerOwnership:
    binding: str
    instance_id: str
    worker_session_id: str
    owner_ref: str
    execution_id: str
    execution_token: str = field(repr=False)
    staging_root: str
    staging_owner_ref: str
    next_input_sequence: int = 0
    resource_id: str | None = None
    container_name: str | None = None
    receipt: AuthorWorkerResourceReceipt | None = None
    cleanup: AuthorWorkerCleanupResult | None = None


@dataclass
class ModuleExecutionRecord:
    generation_id: str
    root_binding: str
    work_item_id: str
    attempt_id: str
    workers: tuple[ModuleWorkerOwnership, ...] = ()


_T = TypeVar("_T")
@dataclass
class TurnRecord:
    """Engine-owned identity and admission state for one accepted turn."""

    input_id: str
    turn_id: str
    client_message_id: str
    content: str | None
    attachments: Tuple[str, ...]
    original_disposition: str
    state: str
    cancellation_requested: bool = False
    cancellation_reason: Optional[str] = None
    execution_committed: bool = False
    terminal_outcome: Optional[str] = None
    terminal_resolution_committed: bool = False
    body_digest: Optional[str] = None
    logical_event_count_before_admission: Optional[int] = None
    logical_input_content_hash: Optional[str] = None
    logical_input_session_status_before_admission: Optional[str] = None
    module_input: ModuleInput | None = None
    module_input_sequence: int | None = None


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
    replay_head_event_id: Optional[str] = None
    replay_head_sequence: int = 0
    # A loaded cursor without its event body anchors the newly buffered suffix.
    retained_replay_boundary: Optional[tuple[int, str]] = field(default=None, repr=False)
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
    admission_closed: bool = field(default=False, repr=False)
    admission_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock, repr=False)
    loaded_from_retained_state: bool = field(default=False, repr=False)
    retained_turn_journal_digest: Optional[str] = field(default=None, repr=False)
    runtime_generation_source_ref: Optional[str] = None
    next_module_input_sequence: int = 0
    module_grant: AdmissionGrant | None = None
    module_execution: ModuleExecutionRecord | None = None
    generation_admission: GenerationAdmission | None = None
    module_resume_checkpoints: Dict[str, CheckpointProposal] = field(
        default_factory=dict,
        repr=False,
    )

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
            head_sequence=(
                self.replay_head_sequence or self.event_seq
                if self.replay_history_partial
                else self.event_seq
            ),
            retained_history_partial=self.replay_history_partial,
            persisted_head_event_id=self.replay_head_event_id,
            retained_boundary=self.retained_replay_boundary,
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

def _generation_admission_exact_identity(value: Any) -> tuple[Any, ...] | None:
    detached = _generation_admission_dict(value)
    if detached is None:
        return None
    return tuple(
        detached[field_name] for field_name in sorted(_GENERATION_ADMISSION_FIELDS)
    )

