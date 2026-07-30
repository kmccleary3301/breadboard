"""Session registry responsible for tracking active and historical runs."""

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

from .engine_identity_config import EngineProcessIdentity, LaunchBootstrapVerifier
from .events import EventType, SessionEvent, replay_retention_facts
from .models import (
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

    def __init__(
        self,
        state_root: str | Path | None = None,
        *,
        process_identity: EngineProcessIdentity | None = None,
        bootstrap_verifier: LaunchBootstrapVerifier | None = None,
        clock: Callable[[], float] = time.time,
        control_request_capacity: int = CONTROL_REQUEST_ID_CAPACITY,
    ) -> None:
        self._records: Dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()
        self._authority_lock = asyncio.Lock()
        self._process_identity = process_identity
        self._bootstrap_verifier = bootstrap_verifier
        self._clock = clock
        if (
            isinstance(control_request_capacity, bool)
            or not isinstance(control_request_capacity, int)
            or not 1 <= control_request_capacity <= CONTROL_REQUEST_ID_CAPACITY
        ):
            raise ValueError("control request capacity is outside the supported range")
        self._control_request_capacity = control_request_capacity
        self._owner: _OwnerLease | None = None
        self._registrations: Dict[str, _ClientRegistration] = {}
        self._registration_by_client: Dict[str, str] = {}
        self._client_generation: Dict[str, int] = {}
        self._admission_epoch = 0
        self._session_admission_open = True
        self._turn_admission_open = True
        self._registrations_open = True
        self._drain_generation = 0
        self._drain: _DrainState | None = None
        self._control_request_ids: set[str] = set()
        self._state_root = Path(state_root).resolve() if state_root is not None else None
        if self._state_root is not None:
            self._state_root.mkdir(parents=True, exist_ok=True)
            self._load_retained_records()

    @property
    def admission_epoch(self) -> int:
        return self._admission_epoch

    @staticmethod
    def _credential_verifier(
        kind: str,
        secret: bytearray,
        binding: tuple[str, ...],
    ) -> bytes:
        digest = hashlib.sha256()
        digest.update(b"breadboard-p30-authority-verifier-v1\0")
        digest.update(kind.encode("ascii"))
        for value in binding:
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(2, "big"))
            digest.update(encoded)
        digest.update(len(secret).to_bytes(2, "big"))
        digest.update(secret)
        return digest.digest()

    @staticmethod
    def _wipe_credentials(*credentials: bytearray | None) -> None:
        for credential in credentials:
            if credential is None:
                continue
            for index in range(len(credential)):
                credential[index] = 0

    def _authority_now(self, *, code: str) -> float:
        now = self._clock()
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
        ):
            raise LifecycleAuthorityError(
                code,
                "lifecycle authority clock is invalid",
            )
        return float(now)

    @asynccontextmanager
    async def event_persistence_authority(self) -> Any:
        """Serialize durable terminal resolution with lifecycle drain decisions."""

        async with self._authority_lock:
            yield

    def _identity_or_error(self) -> EngineProcessIdentity:
        if self._process_identity is None:
            raise LifecycleAuthorityError(
                "lifecycle_authority_unavailable",
                "engine lifecycle authority is unavailable",
            )
        return self._process_identity

    def _require_owner_binding(
        self,
        engine_instance_id: str,
        engine_boot_id: str,
        launch_id: str,
    ) -> EngineProcessIdentity:
        identity = self._identity_or_error()
        if engine_instance_id != identity.engine_instance_id:
            raise LifecycleAuthorityError("engine_identity_mismatch", "engine instance does not match")
        if engine_boot_id != identity.engine_boot_id:
            raise LifecycleAuthorityError("engine_identity_mismatch", "engine boot does not match")
        if launch_id != identity.launch_id:
            raise LifecycleAuthorityError("engine_identity_mismatch", "engine launch does not match")
        return identity

    def _owner_response(self, result: str) -> OwnerLeaseResponse:
        identity = self._identity_or_error()
        owner = self._owner
        if owner is None:
            raise LifecycleAuthorityError("owner_expired", "owner lease is unavailable")
        return OwnerLeaseResponse(
            result=result,
            engine_instance_id=identity.engine_instance_id,
            engine_boot_id=identity.engine_boot_id,
            launch_id=identity.launch_id,
            owner_generation=owner.generation,
            expires_at_unix=None if owner.released else owner.expires_at_unix,
        )

    def _owner_binding(self, identity: EngineProcessIdentity) -> tuple[str, ...]:
        return (
            identity.engine_instance_id,
            identity.engine_boot_id,
            identity.launch_id,
        )

    def _owner_credential_matches(
        self,
        supplied: bytearray,
        identity: EngineProcessIdentity,
    ) -> bool:
        owner = self._owner
        if owner is None:
            return False
        candidate = self._credential_verifier(
            "owner",
            supplied,
            self._owner_binding(identity),
        )
        return secrets.compare_digest(candidate, owner.credential_verifier)

    async def issue_bootstrap_challenge(
        self,
        request: BootstrapChallengeRequest,
    ) -> BootstrapChallengeResponse:
        async with self._authority_lock:
            identity = self._require_owner_binding(
                request.engine_instance_id,
                request.engine_boot_id,
                request.launch_id,
            )
            if self._owner is not None:
                raise LifecycleAuthorityError("owner_conflict", "owner generation already exists")
            verifier = self._bootstrap_verifier
            if verifier is None:
                raise LifecycleAuthorityError("bootstrap_unavailable", "launch bootstrap is unavailable")
            challenge = verifier.issue_challenge(identity, now=self._clock())
            if challenge is None:
                code = "bootstrap_consumed" if verifier.consumed else "bootstrap_invalid"
                raise LifecycleAuthorityError(code, "launch bootstrap challenge was rejected")
            return BootstrapChallengeResponse(
                engine_instance_id=identity.engine_instance_id,
                engine_boot_id=identity.engine_boot_id,
                launch_id=identity.launch_id,
                challenge_id=challenge[0],
                challenge=challenge[1],
                expires_at_unix=challenge[2],
            )

    async def acquire_owner(
        self,
        request: OwnerAcquireRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        try:
            async with self._authority_lock:
                identity = self._require_owner_binding(
                    request.engine_instance_id,
                    request.engine_boot_id,
                    request.launch_id,
                )
                now = self._clock()
                if request.expected_owner_generation == 0:
                    if self._owner is not None:
                        raise LifecycleAuthorityError("owner_conflict", "owner generation already exists")
                    bootstrap = self._bootstrap_verifier
                    if bootstrap is None:
                        raise LifecycleAuthorityError(
                            "bootstrap_unavailable",
                            "launch bootstrap is unavailable",
                        )
                    if bootstrap.matches_bootstrap_secret(owner_credential, identity):
                        raise LifecycleAuthorityError(
                            "bootstrap_rotation_invalid",
                            "owner credential must differ from launch bootstrap",
                        )
                    if (
                        request.bootstrap_challenge_id is None
                        or request.bootstrap_proof_sha256 is None
                        or not bootstrap.consume_proof(
                            request.bootstrap_challenge_id,
                            request.bootstrap_proof_sha256,
                            owner_credential,
                            identity,
                            now=now,
                        )
                    ):
                        code = "bootstrap_consumed" if bootstrap.consumed else "bootstrap_invalid"
                        raise LifecycleAuthorityError(code, "launch bootstrap proof was rejected")
                    self._owner = _OwnerLease(
                        generation=1,
                        credential_verifier=self._credential_verifier(
                            "owner",
                            owner_credential,
                            self._owner_binding(identity),
                        ),
                        expires_at_unix=now + 30,
                    )
                    return self._owner_response("acquired")

                if request.bootstrap_challenge_id is not None or request.bootstrap_proof_sha256 is not None:
                    raise LifecycleAuthorityError(
                        "bootstrap_invalid",
                        "launch bootstrap proof is not valid for owner reacquisition",
                    )
                owner = self._owner
                if owner is None:
                    raise LifecycleAuthorityError("owner_expired", "owner lease is unavailable")
                if request.expected_owner_generation != owner.generation:
                    raise LifecycleAuthorityError(
                        "owner_generation_conflict",
                        "owner generation does not match",
                    )
                if not self._owner_credential_matches(owner_credential, identity):
                    raise LifecycleAuthorityError("owner_identity_mismatch", "owner proof was rejected")
                if not owner.released and owner.expires_at_unix > now:
                    raise LifecycleAuthorityError("owner_conflict", "owner lease is still active")
                next_generation = owner.generation + 1
                self._owner = _OwnerLease(
                    generation=next_generation,
                    credential_verifier=owner.credential_verifier,
                    expires_at_unix=now + 30,
                )
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                drain = self._drain
                if drain is not None and drain.phase != "rolled_back":
                    if drain.phase in {
                        "draining",
                        "hard_signal_decision_pending",
                        "rollback_permitted",
                    }:
                        drain.owner_generation = next_generation
                        drain.recovery_forbidden = False
                    else:
                        drain.recovery_forbidden = True
                return self._owner_response("acquired")
        finally:
            self._wipe_credentials(owner_credential)

    def _require_live_owner(
        self,
        request: OwnerLeaseRequest | BeginControlDrainRequest | DrainControlRequest,
        owner_credential: bytearray,
        *,
        now: float | None = None,
    ) -> _OwnerLease:
        identity = self._require_owner_binding(
            request.engine_instance_id,
            request.engine_boot_id,
            request.launch_id,
        )
        owner = self._owner
        if owner is None:
            raise LifecycleAuthorityError("owner_expired", "owner lease is unavailable")
        if request.owner_generation != owner.generation:
            raise LifecycleAuthorityError(
                "owner_generation_conflict",
                "owner generation does not match",
            )
        if not self._owner_credential_matches(owner_credential, identity):
            raise LifecycleAuthorityError("owner_identity_mismatch", "owner proof was rejected")
        if owner.released or owner.expires_at_unix <= (
            self._clock() if now is None else now
        ):
            raise LifecycleAuthorityError("owner_expired", "owner lease has expired")
        return owner

    async def renew_owner(
        self,
        request: OwnerLeaseRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        try:
            async with self._authority_lock:
                owner = self._require_live_owner(request, owner_credential)
                owner.expires_at_unix = self._clock() + 30
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                return self._owner_response("renewed")
        finally:
            self._wipe_credentials(owner_credential)

    async def release_owner(
        self,
        request: OwnerLeaseRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        try:
            async with self._authority_lock:
                identity = self._require_owner_binding(
                    request.engine_instance_id,
                    request.engine_boot_id,
                    request.launch_id,
                )
                owner = self._owner
                if owner is None:
                    raise LifecycleAuthorityError("owner_expired", "owner lease is unavailable")
                if request.owner_generation != owner.generation:
                    raise LifecycleAuthorityError(
                        "owner_generation_conflict",
                        "owner generation does not match",
                    )
                if not self._owner_credential_matches(owner_credential, identity):
                    raise LifecycleAuthorityError(
                        "owner_identity_mismatch",
                        "owner proof was rejected",
                    )
                if owner.released:
                    return self._owner_response("already_released")
                if owner.expires_at_unix <= self._clock():
                    raise LifecycleAuthorityError("owner_expired", "owner lease has expired")
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                if self._drain is not None and self._drain.phase not in {"rolled_back"}:
                    raise LifecycleAuthorityError("drain_in_progress", "control drain is in progress")
                owner.released = True
                owner.expires_at_unix = 0
                return self._owner_response("released")
        finally:
            self._wipe_credentials(owner_credential)

    def _registration_binding(
        self,
        registration_id: str,
        client_instance_id: str,
    ) -> tuple[str, ...]:
        identity = self._identity_or_error()
        return (identity.engine_instance_id, registration_id, client_instance_id)

    def _registration_response(
        self,
        registration: _ClientRegistration,
        result: str,
    ) -> ClientRegistrationResponse:
        identity = self._identity_or_error()
        return ClientRegistrationResponse(
            result=result,
            engine_instance_id=identity.engine_instance_id,
            registration_id=registration.registration_id,
            registration_generation=registration.generation,
            client_instance_id=registration.client_instance_id,
            workspace_id=registration.workspace_id,
            lifecycle_mode=registration.lifecycle_mode,
            first_slice_contract_id=registration.first_slice_contract_id,
            first_slice_schema_sha256=registration.first_slice_schema_sha256,
            registered_at_unix=registration.registered_at_unix,
            expires_at_unix=None if registration.detached else registration.expires_at_unix,
            admission_epoch=self._admission_epoch,
        )

    def _registration_credential_matches(
        self,
        registration: _ClientRegistration,
        supplied: bytearray,
    ) -> bool:
        candidate = self._credential_verifier(
            "registration",
            supplied,
            self._registration_binding(
                registration.registration_id,
                registration.client_instance_id,
            ),
        )
        return secrets.compare_digest(candidate, registration.credential_verifier)

    def _try_rollback_control_drain(
        self,
        *,
        require_orphaned_requester: bool,
        now: float | None = None,
    ) -> bool:
        drain = self._drain
        if (
            drain is None
            or drain.phase not in {"draining", "rollback_permitted"}
            or drain.recovery_forbidden
        ):
            return False
        if drain.phase == "draining" and (
            drain.hard_signal_authorization_id is not None
            or drain.hard_signal_authorization_expires_at_unix is not None
            or drain.hard_signal_authorization_owner_generation is not None
            or drain.hard_signal_outcome is not None
        ):
            return False
        if (
            drain.phase == "rollback_permitted"
            and drain.hard_signal_outcome not in {None, "abandoned"}
        ):
            return False
        if require_orphaned_requester:
            identity = self._process_identity
            if (
                identity is None
                or drain.engine_instance_id != identity.engine_instance_id
                or drain.engine_boot_id != identity.engine_boot_id
                or drain.launch_id != identity.launch_id
            ):
                return False
            requester = self._registrations.get(
                drain.requester_registration_id,
            )
            if requester is not None:
                if (
                    requester.generation
                    != drain.requester_registration_generation
                    or requester.client_instance_id
                    != drain.requester_client_instance_id
                ):
                    return False
                if not requester.detached:
                    requester_now = self._clock() if now is None else now
                    if (
                        not math.isfinite(requester_now)
                        or not math.isfinite(requester.expires_at_unix)
                        or requester.expires_at_unix > requester_now
                    ):
                        return False
        self._session_admission_open = True
        self._turn_admission_open = True
        self._registrations_open = True
        self._admission_epoch += 1
        drain.phase = "rolled_back"
        return True

    async def register_client(
        self,
        request: ClientRegisterRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        try:
            async with self._authority_lock:
                identity = self._identity_or_error()
                if request.engine_instance_id != identity.engine_instance_id:
                    raise LifecycleAuthorityError(
                        "engine_identity_mismatch",
                        "engine instance does not match",
                    )
                if request.lifecycle_mode == "off":
                    raise LifecycleAuthorityError("lifecycle_mode_invalid", "off mode cannot register")
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                if not self._registrations_open:
                    raise LifecycleAuthorityError("drain_in_progress", "new registrations are closed")
                now = self._clock()
                prior_id = self._registration_by_client.get(request.client_instance_id)
                if prior_id is not None:
                    prior = self._registrations[prior_id]
                    if not prior.detached and prior.expires_at_unix > now:
                        raise LifecycleAuthorityError(
                            "registration_conflict",
                            "client registration is already active",
                        )
                generation = self._client_generation.get(request.client_instance_id, 0) + 1
                registration_id = secrets.token_urlsafe(32)
                registration = _ClientRegistration(
                    registration_id=registration_id,
                    generation=generation,
                    client_instance_id=request.client_instance_id,
                    workspace_id=request.workspace_id,
                    lifecycle_mode=request.lifecycle_mode,
                    first_slice_contract_id=request.first_slice_contract_id,
                    first_slice_schema_sha256=request.first_slice_schema_sha256,
                    registered_at_unix=now,
                    expires_at_unix=now + 30,
                    credential_verifier=self._credential_verifier(
                        "registration",
                        registration_credential,
                        self._registration_binding(
                            registration_id,
                            request.client_instance_id,
                        ),
                    ),
                )
                self._registrations[registration_id] = registration
                self._registration_by_client[request.client_instance_id] = registration_id
                self._client_generation[request.client_instance_id] = generation
                return self._registration_response(registration, "registered")
        finally:
            self._wipe_credentials(registration_credential)

    def _require_registration(
        self,
        request: ClientLeaseRequest | BeginControlDrainRequest,
        registration_credential: bytearray,
    ) -> _ClientRegistration:
        identity = self._identity_or_error()
        if request.engine_instance_id != identity.engine_instance_id:
            raise LifecycleAuthorityError("engine_identity_mismatch", "engine instance does not match")
        registration = self._registrations.get(request.registration_id)
        if registration is None:
            raise LifecycleAuthorityError("registration_expired", "registration is unavailable")
        generation = (
            request.requester_registration_generation
            if isinstance(request, BeginControlDrainRequest)
            else request.registration_generation
        )
        client_instance_id = (
            request.requester_client_instance_id
            if isinstance(request, BeginControlDrainRequest)
            else request.client_instance_id
        )
        if generation != registration.generation:
            raise LifecycleAuthorityError(
                "registration_generation_conflict",
                "registration generation does not match",
            )
        if client_instance_id != registration.client_instance_id:
            raise LifecycleAuthorityError(
                "registration_identity_mismatch",
                "registration client does not match",
            )
        if not self._registration_credential_matches(
            registration,
            registration_credential,
        ):
            raise LifecycleAuthorityError(
                "registration_identity_mismatch",
                "registration proof was rejected",
            )
        if registration.detached or registration.expires_at_unix <= self._clock():
            raise LifecycleAuthorityError("registration_expired", "registration has expired")
        return registration

    async def renew_client(
        self,
        request: ClientLeaseRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        try:
            async with self._authority_lock:
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                registration = self._require_registration(
                    request,
                    registration_credential,
                )
                registration.expires_at_unix = self._clock() + 30
                return self._registration_response(registration, "renewed")
        finally:
            self._wipe_credentials(registration_credential)

    async def detach_client(
        self,
        request: ClientLeaseRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        try:
            async with self._authority_lock:
                identity = self._identity_or_error()
                if request.engine_instance_id != identity.engine_instance_id:
                    raise LifecycleAuthorityError(
                        "engine_identity_mismatch",
                        "engine instance does not match",
                    )
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                registration = self._registrations.get(request.registration_id)
                if registration is None:
                    raise LifecycleAuthorityError(
                        "registration_expired",
                        "registration is unavailable",
                    )
                if request.registration_generation != registration.generation:
                    raise LifecycleAuthorityError(
                        "registration_generation_conflict",
                        "registration generation does not match",
                    )
                if (
                    request.client_instance_id != registration.client_instance_id
                    or not self._registration_credential_matches(
                        registration,
                        registration_credential,
                    )
                ):
                    raise LifecycleAuthorityError(
                        "registration_identity_mismatch",
                        "registration proof was rejected",
                    )
                if registration.detached:
                    return self._registration_response(registration, "already_detached")
                registration.detached = True
                registration.expires_at_unix = 0
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                return self._registration_response(registration, "detached")
        finally:
            self._wipe_credentials(registration_credential)

    def _has_unresolved_turn(self) -> bool:
        return any(
            not turn.terminal_resolution_committed
            for record in self._records.values()
            for turn in record.turns_by_id.values()
        )

    def _drain_response(self, result: str, *, signal_permitted: bool = False) -> DrainControlResponse:
        identity = self._identity_or_error()
        drain = self._drain
        if drain is None:
            raise LifecycleAuthorityError("drain_recovery_failed", "control drain state is unavailable")
        return DrainControlResponse(
            result=result,
            engine_instance_id=identity.engine_instance_id,
            engine_boot_id=identity.engine_boot_id,
            launch_id=identity.launch_id,
            drain_generation=drain.generation,
            control_request_id=drain.control_request_id,
            admission_epoch=self._admission_epoch,
            session_admission_open=self._session_admission_open,
            turn_admission_open=self._turn_admission_open,
            registrations_open=self._registrations_open,
            signal_permitted=signal_permitted,
        )

    def _commit_graceful_control_response(
        self,
        drain: _DrainState,
        result: str,
        *,
        signal_permitted: bool = False,
    ) -> DrainControlResponse:
        response = self._drain_response(
            result,
            signal_permitted=signal_permitted,
        )
        drain.graceful_control_receipt = _GracefulControlReceipt(
            result=response.result,
            admission_epoch=response.admission_epoch,
            session_admission_open=response.session_admission_open,
            turn_admission_open=response.turn_admission_open,
            registrations_open=response.registrations_open,
            signal_permitted=response.signal_permitted,
        )
        return response

    def _replay_graceful_control_response(
        self,
        drain: _DrainState,
    ) -> DrainControlResponse:
        receipt = drain.graceful_control_receipt
        identity = self._identity_or_error()
        if receipt is None:
            raise LifecycleAuthorityError(
                "drain_conflict",
                "control drain outcome is already final",
            )
        return DrainControlResponse(
            result=receipt.result,
            engine_instance_id=identity.engine_instance_id,
            engine_boot_id=identity.engine_boot_id,
            launch_id=identity.launch_id,
            drain_generation=drain.generation,
            control_request_id=drain.control_request_id,
            admission_epoch=receipt.admission_epoch,
            session_admission_open=receipt.session_admission_open,
            turn_admission_open=receipt.turn_admission_open,
            registrations_open=receipt.registrations_open,
            signal_permitted=receipt.signal_permitted,
        )

    @staticmethod
    def _control_request_matches(
        drain: _DrainState,
        request: BeginControlDrainRequest,
    ) -> bool:
        return (
            drain.operation_kind == "begin_control_drain"
            and drain.engine_instance_id == request.engine_instance_id
            and drain.engine_boot_id == request.engine_boot_id
            and drain.launch_id == request.launch_id
            and drain.begin_owner_generation == request.owner_generation
            and drain.requester_registration_id == request.registration_id
            and (
                drain.requester_registration_generation
                == request.requester_registration_generation
            )
            and (
                drain.requester_client_instance_id
                == request.requester_client_instance_id
            )
            and (
                drain.expected_admission_epoch
                == request.expected_admission_epoch
            )
        )


    async def begin_control_drain(
        self,
        request: BeginControlDrainRequest,
        *,
        owner_credential: bytearray,
        registration_credential: bytearray,
    ) -> DrainControlResponse:
        try:
            async with self._authority_lock:
                owner = self._require_live_owner(request, owner_credential)
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                requester = self._require_registration(request, registration_credential)
                drain = self._drain
                if (
                    drain is not None
                    and drain.phase != "rolled_back"
                    and request.control_request_id == drain.control_request_id
                ):
                    if not self._control_request_matches(drain, request):
                        raise LifecycleAuthorityError(
                            "control_request_conflict",
                            "control request binding does not match",
                        )
                    return self._drain_response("draining")
                if request.control_request_id in self._control_request_ids:
                    raise LifecycleAuthorityError(
                        "control_request_conflict",
                        "control request identifier is already complete",
                    )
                if drain is not None and drain.phase != "rolled_back":
                    raise LifecycleAuthorityError(
                        "drain_conflict",
                        "control drain is already active",
                    )
                if request.expected_admission_epoch != self._admission_epoch:
                    raise LifecycleAuthorityError(
                        "admission_epoch_conflict",
                        "admission epoch does not match",
                    )
                now = self._clock()
                live = [
                    registration
                    for registration in self._registrations.values()
                    if not registration.detached and registration.expires_at_unix > now
                ]
                if len(live) != 1 or live[0] is not requester:
                    raise LifecycleAuthorityError(
                        "drain_clients_active",
                        "another live client prevents control drain",
                    )
                if self._has_unresolved_turn():
                    raise LifecycleAuthorityError(
                        "drain_turn_active",
                        "an unresolved admitted turn prevents control drain",
                    )
                if (
                    len(self._control_request_ids)
                    >= self._control_request_capacity
                ):
                    raise LifecycleAuthorityError(
                        "control_request_capacity_exceeded",
                        "control request capacity is exhausted",
                    )
                self._drain_generation += 1
                self._session_admission_open = False
                self._turn_admission_open = False
                self._registrations_open = False
                self._admission_epoch += 1
                self._drain = _DrainState(
                    generation=self._drain_generation,
                    control_request_id=request.control_request_id,
                    operation_kind="begin_control_drain",
                    engine_instance_id=request.engine_instance_id,
                    engine_boot_id=request.engine_boot_id,
                    launch_id=request.launch_id,
                    begin_owner_generation=owner.generation,
                    owner_generation=owner.generation,
                    requester_registration_id=requester.registration_id,
                    requester_registration_generation=requester.generation,
                    requester_client_instance_id=requester.client_instance_id,
                    expected_admission_epoch=request.expected_admission_epoch,
                    phase="draining",
                )
                self._control_request_ids.add(request.control_request_id)
                return self._drain_response("draining")
        finally:
            self._wipe_credentials(owner_credential, registration_credential)

    def _require_drain_control(
        self,
        request: DrainControlRequest,
        owner_credential: bytearray,
        *,
        phases: set[str],
        now: float | None = None,
    ) -> _DrainState:
        self._require_live_owner(
            request,
            owner_credential,
            now=now,
        )
        self._try_rollback_control_drain(
            require_orphaned_requester=True,
            now=now,
        )
        drain = self._drain
        if (
            drain is None
            or request.drain_generation != drain.generation
            or request.owner_generation != drain.owner_generation
            or drain.phase not in phases
        ):
            raise LifecycleAuthorityError(
                "drain_conflict",
                "control drain generation or phase does not match",
            )
        return drain

    async def record_graceful_control(
        self,
        request: GracefulControlResultRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        try:
            async with self._authority_lock:
                self._require_live_owner(request, owner_credential)
                drain = self._drain
                if (
                    drain is None
                    or request.drain_generation != drain.generation
                    or request.owner_generation != drain.owner_generation
                ):
                    raise LifecycleAuthorityError(
                        "drain_conflict",
                        "control drain generation or phase does not match",
                    )
                if drain.graceful_control_outcome is not None:
                    if drain.graceful_control_outcome == request.outcome:
                        return self._replay_graceful_control_response(drain)
                    raise LifecycleAuthorityError(
                        "drain_conflict",
                        "control drain outcome is already final",
                    )
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                )
                if drain.phase != "draining":
                    raise LifecycleAuthorityError(
                        "drain_conflict",
                        "control drain generation or phase does not match",
                    )
                drain.graceful_control_outcome = request.outcome
                if request.outcome == "accepted":
                    drain.phase = "shutdown_started"
                    return self._commit_graceful_control_response(
                        drain,
                        "shutdown_started",
                    )
                if request.outcome == "definitive_rejection":
                    drain.phase = "rollback_permitted"
                    self._try_rollback_control_drain(
                        require_orphaned_requester=True,
                    )
                    return self._commit_graceful_control_response(
                        drain,
                        drain.phase,
                    )
                drain.phase = "hard_signal_decision_pending"
                return self._commit_graceful_control_response(
                    drain,
                    "hard_signal_decision_pending",
                )
        finally:
            self._wipe_credentials(owner_credential)

    async def prepare_hard_signal(
        self,
        request: HardSignalPrepareRequest,
        *,
        owner_credential: bytearray,
    ) -> HardSignalPreparationResponse:
        try:
            async with self._authority_lock:
                now = self._authority_now(
                    code="hard_signal_authorization_conflict",
                )
                drain = self._require_drain_control(
                    request,
                    owner_credential,
                    phases={
                        "hard_signal_decision_pending",
                        "signal_attempt_committed",
                        "signal_sent",
                        "process_exited",
                    },
                    now=now,
                )
                identity = self._identity_or_error()
                if request.pid != identity.pid or request.os_process_start_token != identity.os_process_start_token:
                    raise LifecycleAuthorityError("process_identity_mismatch", "process proof does not match")
                authorization_id = drain.hard_signal_authorization_id
                expires_at = drain.hard_signal_authorization_expires_at_unix
                authorization_owner_generation = (
                    drain.hard_signal_authorization_owner_generation
                )
                if (
                    authorization_id is None
                    and expires_at is None
                    and authorization_owner_generation is None
                ):
                    authorization_id = secrets.token_urlsafe(32)
                    expires_at = now + 30
                    authorization_owner_generation = drain.owner_generation
                    drain.hard_signal_authorization_id = authorization_id
                    drain.hard_signal_authorization_expires_at_unix = expires_at
                    drain.hard_signal_authorization_owner_generation = (
                        authorization_owner_generation
                    )
                elif (
                    authorization_id is None
                    or expires_at is None
                    or authorization_owner_generation is None
                ):
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization state is inconsistent",
                    )
                elif (
                    expires_at <= now
                    and not drain.hard_signal_attempt_committed
                ):
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_expired",
                        "hard signal authorization expired",
                    )
                elif authorization_owner_generation != drain.owner_generation:
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization belongs to a prior owner generation",
                    )
                return HardSignalPreparationResponse(
                    engine_instance_id=identity.engine_instance_id,
                    engine_boot_id=identity.engine_boot_id,
                    launch_id=identity.launch_id,
                    owner_generation=authorization_owner_generation,
                    drain_generation=drain.generation,
                    authorization_id=authorization_id,
                    expires_at_unix=expires_at,
                )
        finally:
            self._wipe_credentials(owner_credential)

    async def commit_hard_signal(
        self,
        request: HardSignalCommitRequest,
        *,
        owner_credential: bytearray,
    ) -> HardSignalPermitResponse:
        try:
            async with self._authority_lock:
                now = self._authority_now(
                    code="hard_signal_authorization_conflict",
                )
                drain = self._require_drain_control(
                    request,
                    owner_credential,
                    phases={
                        "hard_signal_decision_pending",
                        "signal_attempt_committed",
                        "signal_sent",
                        "process_exited",
                    },
                    now=now,
                )
                identity = self._identity_or_error()
                if (
                    request.pid != identity.pid
                    or request.os_process_start_token
                    != identity.os_process_start_token
                ):
                    raise LifecycleAuthorityError(
                        "process_identity_mismatch",
                        "process proof does not match",
                    )
                authorization_id = drain.hard_signal_authorization_id
                expires_at = drain.hard_signal_authorization_expires_at_unix
                authorization_owner_generation = (
                    drain.hard_signal_authorization_owner_generation
                )
                if (
                    authorization_id is None
                    or expires_at is None
                    or authorization_owner_generation is None
                ):
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization state is inconsistent",
                    )
                if request.authorization_id != authorization_id:
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization does not match",
                    )
                if (
                    authorization_owner_generation != request.owner_generation
                    or authorization_owner_generation != drain.owner_generation
                ):
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization belongs to a prior owner generation",
                    )
                if not drain.hard_signal_attempt_committed:
                    if expires_at <= now:
                        raise LifecycleAuthorityError(
                            "hard_signal_authorization_expired",
                            "hard signal authorization expired",
                        )
                    if drain.hard_signal_outcome is not None:
                        raise LifecycleAuthorityError(
                            "hard_signal_authorization_conflict",
                            "hard signal outcome is already final",
                        )
                    drain.hard_signal_attempt_committed = True
                    drain.phase = "signal_attempt_committed"
                    drain.recovery_forbidden = True
                return HardSignalPermitResponse(
                    engine_instance_id=identity.engine_instance_id,
                    engine_boot_id=identity.engine_boot_id,
                    launch_id=identity.launch_id,
                    owner_generation=authorization_owner_generation,
                    drain_generation=drain.generation,
                    authorization_id=authorization_id,
                    expires_at_unix=expires_at,
                )
        finally:
            self._wipe_credentials(owner_credential)

    async def record_hard_signal_outcome(
        self,
        request: HardSignalOutcomeRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        try:
            async with self._authority_lock:
                now = self._authority_now(
                    code="hard_signal_authorization_conflict",
                )
                drain = self._require_drain_control(
                    request,
                    owner_credential,
                    phases={
                        "hard_signal_decision_pending",
                        "signal_attempt_committed",
                        "signal_sent",
                        "process_exited",
                        "rolled_back",
                    },
                    now=now,
                )
                if request.authorization_id != drain.hard_signal_authorization_id:
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization does not match",
                    )
                if drain.hard_signal_outcome is not None:
                    if request.outcome != drain.hard_signal_outcome:
                        raise LifecycleAuthorityError(
                            "hard_signal_outcome_conflict",
                            "hard signal outcome is already final",
                        )
                    return self._drain_response(drain.phase)
                expires_at = drain.hard_signal_authorization_expires_at_unix
                authorization_owner_generation = (
                    drain.hard_signal_authorization_owner_generation
                )
                if (
                    expires_at is None
                    or authorization_owner_generation is None
                ):
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization state is inconsistent",
                    )
                if authorization_owner_generation != request.owner_generation:
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal authorization belongs to a prior owner generation",
                    )
                authorization_expired = expires_at <= now
                if request.outcome == "abandoned":
                    if drain.hard_signal_attempt_committed:
                        raise LifecycleAuthorityError(
                            "hard_signal_authorization_conflict",
                            "committed signal attempt cannot be abandoned",
                        )
                    if not authorization_expired:
                        raise LifecycleAuthorityError(
                            "hard_signal_authorization_conflict",
                            "live signal preparation cannot be abandoned",
                        )
                    drain.hard_signal_outcome = "abandoned"
                    drain.phase = "rollback_permitted"
                    if not self._try_rollback_control_drain(
                        require_orphaned_requester=False,
                        now=now,
                    ):
                        drain.hard_signal_outcome = None
                        drain.phase = "hard_signal_decision_pending"
                        raise LifecycleAuthorityError(
                            "drain_recovery_failed",
                            "control drain cannot be safely recovered",
                        )
                    return self._drain_response("rolled_back")
                if not drain.hard_signal_attempt_committed:
                    raise LifecycleAuthorityError(
                        "hard_signal_authorization_conflict",
                        "hard signal attempt has not been committed",
                    )
                drain.hard_signal_outcome = request.outcome
                if request.outcome == "process_exited":
                    drain.phase = "process_exited"
                    return self._drain_response("process_exited")
                drain.phase = "signal_sent"
                return self._drain_response("signal_sent")
        finally:
            self._wipe_credentials(owner_credential)

    async def rollback_control_drain(
        self,
        request: DrainControlRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        try:
            async with self._authority_lock:
                now = self._authority_now(code="drain_recovery_failed")
                identity = self._require_owner_binding(
                    request.engine_instance_id,
                    request.engine_boot_id,
                    request.launch_id,
                )
                owner = self._owner
                if owner is None:
                    raise LifecycleAuthorityError(
                        "drain_recovery_failed",
                        "control drain cannot be safely recovered",
                    )
                if not self._owner_credential_matches(owner_credential, identity):
                    raise LifecycleAuthorityError(
                        "owner_identity_mismatch",
                        "owner proof was rejected",
                    )
                self._try_rollback_control_drain(
                    require_orphaned_requester=True,
                    now=now,
                )
                drain = self._drain
                if (
                    owner.released
                    or owner.expires_at_unix <= now
                    or request.owner_generation != owner.generation
                    or drain is None
                    or request.drain_generation != drain.generation
                    or request.owner_generation != drain.owner_generation
                    or drain.recovery_forbidden
                ):
                    raise LifecycleAuthorityError(
                        "drain_recovery_failed",
                        "control drain cannot be safely recovered",
                    )
                if drain.phase == "rolled_back":
                    return self._drain_response("rolled_back")
                if drain.phase == "hard_signal_decision_pending":
                    authorization_id = drain.hard_signal_authorization_id
                    expires_at = drain.hard_signal_authorization_expires_at_unix
                    authorization_owner_generation = (
                        drain.hard_signal_authorization_owner_generation
                    )
                    if (
                        authorization_id is None
                        or expires_at is None
                        or authorization_owner_generation is None
                        or expires_at > now
                        or drain.hard_signal_outcome is not None
                    ):
                        raise LifecycleAuthorityError(
                            "drain_recovery_failed",
                            "control drain cannot be safely recovered",
                        )
                    drain.hard_signal_outcome = "abandoned"
                    drain.phase = "rollback_permitted"
                    if not self._try_rollback_control_drain(
                        require_orphaned_requester=False,
                        now=now,
                    ):
                        drain.hard_signal_outcome = None
                        drain.phase = "hard_signal_decision_pending"
                        raise LifecycleAuthorityError(
                            "drain_recovery_failed",
                            "control drain cannot be safely recovered",
                        )
                    return self._drain_response("rolled_back")
                if drain.phase != "rollback_permitted":
                    raise LifecycleAuthorityError(
                        "drain_recovery_failed",
                        "control drain cannot be safely recovered",
                    )
                if not self._try_rollback_control_drain(
                    require_orphaned_requester=False,
                    now=now,
                ):
                    raise LifecycleAuthorityError(
                        "drain_recovery_failed",
                        "control drain cannot be safely recovered",
                    )
                return self._drain_response("rolled_back")
        finally:
            self._wipe_credentials(owner_credential)

    async def admit_session(self, record: SessionRecord, runner: Any) -> SessionRecord:
        async with self._authority_lock:
            self._try_rollback_control_drain(
                require_orphaned_requester=True,
            )
            if not self._session_admission_open:
                raise LifecycleAuthorityError("admission_closed", "new session admission is closed")
            await runner.prepare_start(admission_serialized=True)
            try:
                await self.create(record)
            except BaseException:
                await self.delete(record.session_id)
                raise
            self._admission_epoch += 1
            return record

    async def admit_turn(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        async with self._authority_lock:
            self._try_rollback_control_drain(
                require_orphaned_requester=True,
            )
            if not self._turn_admission_open:
                raise LifecycleAuthorityError("admission_closed", "new turn admission is closed")
            result = await operation()
            if getattr(result, "disposition", None) != "deduplicated":
                self._admission_epoch += 1
            return result

    def authority_snapshot(self) -> dict[str, Any]:
        """Return only allowlisted, non-credential authority facts."""

        identity = self._process_identity
        owner = self._owner
        drain = self._drain
        return {
            "engine_instance_id": identity.engine_instance_id if identity is not None else None,
            "owner_generation": owner.generation if owner is not None else None,
            "owner_active": bool(
                owner is not None
                and not owner.released
                and owner.expires_at_unix > self._clock()
            ),
            "registration_generations": sorted(
                registration.generation for registration in self._registrations.values()
            ),
            "admission_epoch": self._admission_epoch,
            "drain_generation": drain.generation if drain is not None else None,
            "drain_phase": drain.phase if drain is not None else None,
            "session_admission_open": self._session_admission_open,
            "turn_admission_open": self._turn_admission_open,
            "registrations_open": self._registrations_open,
        }

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
