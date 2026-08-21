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
    _ClientRegistration, _DrainState, _GracefulControlReceipt, _OwnerLease,
    LifecycleAuthorityError, SessionRecord,
)


class OwnerAuthorityMixin:
    """Owner and client lease operations for :class:`SessionRegistry`."""

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
