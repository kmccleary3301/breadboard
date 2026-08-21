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
    _DrainState, _GracefulControlReceipt, _OwnerLease, LifecycleAuthorityError,
    SessionRecord, _T,
)


class DrainAuthorityMixin:
    """Control-drain and admission operations for :class:`SessionRegistry`."""


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

    async def ensure_session_admission_open(self) -> None:
        async with self._authority_lock:
            self._try_rollback_control_drain(require_orphaned_requester=True)
            if not self._session_admission_open:
                raise LifecycleAuthorityError("admission_closed", "new session admission is closed")

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

