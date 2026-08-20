from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.cli_bridge import service as bridge_service_module
from breadboard_engine.api.cli_bridge.engine_identity_config import (
    ENGINE_BOOTSTRAP_FD_ENV,
    EngineIdentityConfigError,
    EngineProcessIdentity,
    LaunchBootstrapVerifier,
)
from breadboard_engine.api.cli_bridge.models import (
    BeginControlDrainRequest,
    BootstrapChallengeRequest,
    ClientLeaseRequest,
    ClientRegisterRequest,
    DrainControlRequest,
    GracefulControlResultRequest,
    HardSignalCommitRequest,
    HardSignalOutcomeRequest,
    HardSignalPermitResponse,
    HardSignalPrepareRequest,
    HardSignalPreparationResponse,
    OwnerAcquireRequest,
    OwnerLeaseRequest,
    OwnerLeaseResponse,
    ClientRegistrationResponse,
    DrainControlResponse,
    SessionCreateRequest,
    SessionStatus,
)
from breadboard_engine.api.cli_bridge.events import EventType, SessionEvent
from breadboard_engine.api.cli_bridge.registry import (
    LifecycleAuthorityError,
    SessionRecord,
    SessionRegistry,
    TurnRecord,
)
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner


BOOTSTRAP = "bootstrap-proof-material-000000000000000000"
OWNER_SECRET = "owner-proof-material-0000000000000000000000"
CLIENT_A_SECRET = "client-a-proof-material-000000000000000000"
CLIENT_B_SECRET = "client-b-proof-material-000000000000000000"
WORKSPACE_A = "workspace:v1:sha256:" + "a" * 64
WORKSPACE_B = "workspace:v1:sha256:" + "b" * 64
CLIENT_A = "client-instance-a-000000000000000"
CLIENT_B = "client-instance-b-000000000000000"

_REQUEST_PROOFS: dict[int, dict[str, str | None]] = {}


def _remember_proofs(request: Any, **proofs: str | None) -> Any:
    _REQUEST_PROOFS[id(request)] = proofs
    return request


def _take_proofs(request: Any) -> dict[str, str | None]:
    return _REQUEST_PROOFS.pop(id(request), {})


def _secret_buffer(value: str) -> bytearray:
    return bytearray(value, "ascii")

def _bootstrap_proof(
    process_identity: EngineProcessIdentity,
    bootstrap: str,
    owner: str,
    challenge_id: str,
    challenge: str,
) -> str:
    def field(value: bytes) -> bytes:
        return len(value).to_bytes(2, "big") + value

    binding = b"breadboard-p30-launch-bootstrap-v1\0" + b"".join(
        field(value.encode("ascii"))
        for value in (
            process_identity.launch_id,
            process_identity.engine_boot_id,
            process_identity.engine_instance_id,
        )
    ) + field(bootstrap.encode("ascii"))
    digest = hashlib.sha256(binding).digest()
    message = b"breadboard-p30-launch-bootstrap-proof-v1\0" + b"".join(
        field(value.encode("ascii"))
        for value in (
            process_identity.launch_id,
            process_identity.engine_boot_id,
            process_identity.engine_instance_id,
            challenge_id,
            challenge,
            owner,
        )
    )
    return "sha256:" + hmac.new(digest, message, hashlib.sha256).hexdigest()


def _consume_verifier(
    verifier: LaunchBootstrapVerifier,
    process_identity: EngineProcessIdentity,
) -> bool:
    now = time.time()
    issued = verifier.issue_challenge(process_identity, now=now)
    if issued is None:
        return False
    challenge_id, challenge, _ = issued
    return verifier.consume_proof(
        challenge_id,
        _bootstrap_proof(process_identity, BOOTSTRAP, OWNER_SECRET, challenge_id, challenge),
        _secret_buffer(OWNER_SECRET),
        process_identity,
        now=now,
    )

@dataclass
class Clock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def identity() -> EngineProcessIdentity:
    return EngineProcessIdentity(
        pid=12345,
        os_process_start_token="darwin:1000:0",
        engine_instance_id=secrets.token_urlsafe(32),
        engine_boot_id=secrets.token_urlsafe(32),
        launch_id=secrets.token_urlsafe(32),
        launch_source="supervisor",
        started_at=__import__("datetime").datetime.fromtimestamp(
            1_000.0,
            tz=__import__("datetime").timezone.utc,
        ),
        started_at_unix=1_000.0,
        engine_artifact_sha256="sha256:" + "c" * 64,
    )


def inherited_verifier(process_identity: EngineProcessIdentity) -> LaunchBootstrapVerifier:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, BOOTSTRAP.encode("ascii"))
    finally:
        os.close(write_fd)
    return LaunchBootstrapVerifier.from_inherited_fd(read_fd, process_identity)


def registry_fixture(
    *,
    state_root: Path | None = None,
    control_request_capacity: int = 4096,
) -> tuple[SessionRegistry, EngineProcessIdentity, Clock, LaunchBootstrapVerifier]:
    process_identity = identity()
    clock = Clock()
    verifier = inherited_verifier(process_identity)
    registry = SessionRegistry(
        state_root,
        process_identity=process_identity,
        bootstrap_verifier=verifier,
        clock=clock,
        control_request_capacity=control_request_capacity,
    )
    return registry, process_identity, clock, verifier


def owner_acquire(
    process_identity: EngineProcessIdentity,
    *,
    bootstrap: str | None = BOOTSTRAP,
    owner_secret: str = OWNER_SECRET,
    expected_generation: int = 0,
) -> OwnerAcquireRequest:
    bootstrap_fields = {
        "bootstrap_challenge_id": "c" * 43,
        "bootstrap_proof_sha256": "sha256:" + "0" * 64,
    } if expected_generation == 0 else {}
    request = OwnerAcquireRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        expected_owner_generation=expected_generation,
        **bootstrap_fields,
    )
    return _remember_proofs(
        request,
        bootstrap_credential=bootstrap,
        owner_credential=owner_secret,
    )


def owner_lease(
    process_identity: EngineProcessIdentity,
    generation: int = 1,
    *,
    owner_secret: str = OWNER_SECRET,
) -> OwnerLeaseRequest:
    request = OwnerLeaseRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        owner_generation=generation,
    )
    return _remember_proofs(request, owner_credential=owner_secret)


def client_register(
    process_identity: EngineProcessIdentity,
    *,
    client_id: str = CLIENT_A,
    workspace_id: str = WORKSPACE_A,
    credential: str = CLIENT_A_SECRET,
) -> ClientRegisterRequest:
    request = ClientRegisterRequest(
        engine_instance_id=process_identity.engine_instance_id,
        client_instance_id=client_id,
        workspace_id=workspace_id,
        lifecycle_mode="local-owned",
        first_slice_contract_id="p30-e4-session-v1",
    )
    return _remember_proofs(request, registration_credential=credential)


def client_lease(
    response: Any,
    *,
    credential: str = CLIENT_A_SECRET,
    engine_instance_id: str | None = None,
    generation: int | None = None,
    client_id: str | None = None,
) -> ClientLeaseRequest:
    request = ClientLeaseRequest(
        engine_instance_id=engine_instance_id or response.engine_instance_id,
        registration_id=response.registration_id,
        registration_generation=generation or response.registration_generation,
        client_instance_id=client_id or response.client_instance_id,
    )
    return _remember_proofs(request, registration_credential=credential)


def begin_drain(
    registry: SessionRegistry,
    process_identity: EngineProcessIdentity,
    registration: Any,
    *,
    owner_generation: int = 1,
    owner_secret: str = OWNER_SECRET,
    registration_secret: str = CLIENT_A_SECRET,
    epoch: int | None = None,
    control_request_id: str | None = None,
) -> BeginControlDrainRequest:
    request = BeginControlDrainRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        owner_generation=owner_generation,
        control_request_id=control_request_id or secrets.token_urlsafe(32),
        registration_id=registration.registration_id,
        requester_registration_generation=registration.registration_generation,
        requester_client_instance_id=registration.client_instance_id,
        expected_admission_epoch=registry.admission_epoch if epoch is None else epoch,
    )
    return _remember_proofs(
        request,
        owner_credential=owner_secret,
        registration_credential=registration_secret,
    )


def drain_control(
    process_identity: EngineProcessIdentity,
    generation: int,
    *,
    owner_generation: int = 1,
    owner_secret: str = OWNER_SECRET,
) -> DrainControlRequest:
    request = DrainControlRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        owner_generation=owner_generation,
        drain_generation=generation,
    )
    return _remember_proofs(request, owner_credential=owner_secret)

async def _acquire_owner(
    registry: SessionRegistry,
    request: OwnerAcquireRequest,
) -> Any:
    proofs = _take_proofs(request)
    bootstrap = proofs.get("bootstrap_credential")
    owner = proofs.get("owner_credential") or OWNER_SECRET
    if request.expected_owner_generation == 0 and bootstrap is not None:
        process_identity = registry._identity_or_error()
        challenge = await registry.issue_bootstrap_challenge(
            BootstrapChallengeRequest(
                engine_instance_id=request.engine_instance_id,
                engine_boot_id=request.engine_boot_id,
                launch_id=request.launch_id,
            )
        )
        request = request.model_copy(update={
            "bootstrap_challenge_id": challenge.challenge_id,
            "bootstrap_proof_sha256": _bootstrap_proof(
                process_identity,
                bootstrap,
                owner,
                challenge.challenge_id,
                challenge.challenge,
            ),
        })
    return await registry.acquire_owner(
        request,
        owner_credential=_secret_buffer(owner),
    )


async def _renew_owner(registry: SessionRegistry, request: OwnerLeaseRequest) -> Any:
    owner = _take_proofs(request).get("owner_credential") or OWNER_SECRET
    return await registry.renew_owner(
        request,
        owner_credential=_secret_buffer(owner),
    )


async def _release_owner(registry: SessionRegistry, request: OwnerLeaseRequest) -> Any:
    owner = _take_proofs(request).get("owner_credential") or OWNER_SECRET
    return await registry.release_owner(
        request,
        owner_credential=_secret_buffer(owner),
    )


async def _register_client(registry: SessionRegistry, request: ClientRegisterRequest) -> Any:
    proofs = _take_proofs(request)
    registration = proofs.get("registration_credential")
    if registration is None:
        registration = CLIENT_B_SECRET if request.client_instance_id == CLIENT_B else CLIENT_A_SECRET
    return await registry.register_client(
        request,
        registration_credential=_secret_buffer(registration),
    )


async def _renew_client(registry: SessionRegistry, request: ClientLeaseRequest) -> Any:
    proofs = _take_proofs(request)
    registration = proofs.get("registration_credential")
    if registration is None:
        registration = CLIENT_B_SECRET if request.client_instance_id == CLIENT_B else CLIENT_A_SECRET
    return await registry.renew_client(
        request,
        registration_credential=_secret_buffer(registration),
    )


async def _detach_client(registry: SessionRegistry, request: ClientLeaseRequest) -> Any:
    proofs = _take_proofs(request)
    registration = proofs.get("registration_credential")
    if registration is None:
        registration = CLIENT_B_SECRET if request.client_instance_id == CLIENT_B else CLIENT_A_SECRET
    return await registry.detach_client(
        request,
        registration_credential=_secret_buffer(registration),
    )


async def _begin_control_drain(
    registry: SessionRegistry,
    request: BeginControlDrainRequest,
) -> Any:
    proofs = _take_proofs(request)
    owner = proofs.get("owner_credential") or OWNER_SECRET
    registration = proofs.get("registration_credential")
    if registration is None:
        registration = (
            CLIENT_B_SECRET
            if request.requester_client_instance_id == CLIENT_B
            else CLIENT_A_SECRET
        )
    return await registry.begin_control_drain(
        request,
        owner_credential=_secret_buffer(owner),
        registration_credential=_secret_buffer(registration),
    )


async def _record_graceful_control(
    registry: SessionRegistry,
    request: GracefulControlResultRequest,
) -> Any:
    owner = _take_proofs(request).get("owner_credential") or OWNER_SECRET
    return await registry.record_graceful_control(
        request,
        owner_credential=_secret_buffer(owner),
    )


async def _record_hard_signal_outcome(
    registry: SessionRegistry,
    request: HardSignalOutcomeRequest,
) -> Any:
    owner = _take_proofs(request).get("owner_credential") or OWNER_SECRET
    process_identity = registry._identity_or_error()
    authorization = await registry.prepare_hard_signal(
        HardSignalPrepareRequest(
            engine_instance_id=request.engine_instance_id,
            engine_boot_id=request.engine_boot_id,
            launch_id=request.launch_id,
            owner_generation=request.owner_generation,
            drain_generation=request.drain_generation,
            pid=process_identity.pid,
            os_process_start_token=process_identity.os_process_start_token,
        ),
        owner_credential=_secret_buffer(owner),
    )
    if request.outcome in {"sent", "process_exited"}:
        await registry.commit_hard_signal(
            HardSignalCommitRequest(
                engine_instance_id=request.engine_instance_id,
                engine_boot_id=request.engine_boot_id,
                launch_id=request.launch_id,
                owner_generation=request.owner_generation,
                drain_generation=request.drain_generation,
                authorization_id=authorization.authorization_id,
                pid=process_identity.pid,
                os_process_start_token=process_identity.os_process_start_token,
            ),
            owner_credential=_secret_buffer(owner),
        )
    return await registry.record_hard_signal_outcome(
        request.model_copy(update={"authorization_id": authorization.authorization_id}),
        owner_credential=_secret_buffer(owner),
    )


async def _rollback_control_drain(
    registry: SessionRegistry,
    request: DrainControlRequest,
) -> Any:
    owner = _take_proofs(request).get("owner_credential") or OWNER_SECRET
    return await registry.rollback_control_drain(
        request,
        owner_credential=_secret_buffer(owner),
    )


async def owned_registered_registry(
    *,
    control_request_capacity: int = 4096,
) -> tuple[SessionRegistry, EngineProcessIdentity, Clock, Any]:
    registry, process_identity, clock, _ = registry_fixture(
        control_request_capacity=control_request_capacity,
    )
    await _acquire_owner(registry, owner_acquire(process_identity))
    registration = await _register_client(registry, client_register(process_identity))
    return registry, process_identity, clock, registration


def test_inherited_fd_environment_carries_only_descriptor_and_is_consumed() -> None:
    process_identity = identity()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, BOOTSTRAP.encode("ascii"))
    finally:
        os.close(write_fd)
    environ = {ENGINE_BOOTSTRAP_FD_ENV: str(read_fd)}
    verifier = LaunchBootstrapVerifier.from_environ(environ, process_identity)
    assert verifier is not None
    assert ENGINE_BOOTSTRAP_FD_ENV not in environ
    assert BOOTSTRAP not in repr(environ)
    assert _consume_verifier(verifier, process_identity) is True
    assert _consume_verifier(verifier, process_identity) is False


def test_inherited_fd_accepts_fragmented_frame_only_after_verified_eof() -> None:
    process_identity = identity()
    read_fd, write_fd = os.pipe()

    def write_fragments() -> None:
        try:
            os.write(write_fd, BOOTSTRAP[:11].encode("ascii"))
            time.sleep(0.01)
            os.write(write_fd, BOOTSTRAP[11:].encode("ascii"))
        finally:
            os.close(write_fd)

    writer = threading.Thread(target=write_fragments)
    writer.start()
    verifier = LaunchBootstrapVerifier.from_inherited_fd(
        read_fd,
        process_identity,
        startup_deadline_seconds=0.25,
    )
    writer.join()
    assert _consume_verifier(verifier, process_identity) is True


def test_inherited_fd_rejects_truncated_frame_at_eof() -> None:
    process_identity = identity()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, BOOTSTRAP[:-1].encode("ascii"))
    os.close(write_fd)
    with pytest.raises(EngineIdentityConfigError):
        LaunchBootstrapVerifier.from_inherited_fd(
            read_fd,
            process_identity,
            startup_deadline_seconds=0.1,
        )


def test_inherited_fd_rejects_delayed_extra_byte_before_eof() -> None:
    process_identity = identity()
    read_fd, write_fd = os.pipe()

    def write_delayed_extra() -> None:
        try:
            os.write(write_fd, BOOTSTRAP.encode("ascii"))
            time.sleep(0.01)
            os.write(write_fd, b"x")
        finally:
            os.close(write_fd)

    writer = threading.Thread(target=write_delayed_extra)
    writer.start()
    with pytest.raises(EngineIdentityConfigError):
        LaunchBootstrapVerifier.from_inherited_fd(
            read_fd,
            process_identity,
            startup_deadline_seconds=0.25,
        )
    writer.join()


def test_inherited_fd_rejects_valid_prefix_while_writer_remains_open() -> None:
    process_identity = identity()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, BOOTSTRAP.encode("ascii"))
        with pytest.raises(EngineIdentityConfigError):
            LaunchBootstrapVerifier.from_inherited_fd(
                read_fd,
                process_identity,
                startup_deadline_seconds=0.02,
            )
    finally:
        os.close(write_fd)


@pytest.mark.asyncio
async def test_one_use_fd_bootstrap_dual_claimant_untrusted_race_replay_and_wipe() -> None:
    registry, process_identity, _, verifier = registry_fixture()
    with pytest.raises(LifecycleAuthorityError) as no_rotation:
        await _acquire_owner(registry, owner_acquire(process_identity, owner_secret=BOOTSTRAP))
    assert no_rotation.value.code == "bootstrap_rotation_invalid"
    assert verifier.consumed is False


    with pytest.raises(LifecycleAuthorityError) as untrusted:
        await _acquire_owner(registry, owner_acquire(process_identity, bootstrap="x" * 43))
    assert untrusted.value.code == "bootstrap_invalid"
    assert verifier.consumed is False

    racer, first, second = await asyncio.gather(
        _acquire_owner(registry, owner_acquire(process_identity, bootstrap="y" * 43)),
        _acquire_owner(registry, owner_acquire(process_identity)),
        _acquire_owner(registry, owner_acquire(
            process_identity,
            owner_secret="competing-owner-material-000000000000000000",
        )),
        return_exceptions=True,
    )
    assert isinstance(racer, LifecycleAuthorityError)
    assert racer.code == "bootstrap_invalid"
    assert first.result == "acquired"
    assert isinstance(second, LifecycleAuthorityError)
    assert second.code == "owner_conflict"
    assert verifier.consumed is True
    assert verifier.verifier_wiped is True
    assert _consume_verifier(verifier, process_identity) is False

    with pytest.raises(LifecycleAuthorityError) as replay:
        await _acquire_owner(registry, owner_acquire(process_identity))
    assert replay.value.code == "owner_conflict"
    retained = repr(registry.__dict__)
    assert BOOTSTRAP not in retained
    assert OWNER_SECRET not in retained


@pytest.mark.asyncio
async def test_owned_credential_buffers_are_wiped_on_success_and_failure() -> None:
    registry, process_identity, _, _ = registry_fixture()
    owner_buffer = _secret_buffer(OWNER_SECRET)
    challenge = await registry.issue_bootstrap_challenge(
        BootstrapChallengeRequest(
            engine_instance_id=process_identity.engine_instance_id,
            engine_boot_id=process_identity.engine_boot_id,
            launch_id=process_identity.launch_id,
        )
    )
    request = OwnerAcquireRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        expected_owner_generation=0,
        bootstrap_challenge_id=challenge.challenge_id,
        bootstrap_proof_sha256=_bootstrap_proof(
            process_identity,
            BOOTSTRAP,
            OWNER_SECRET,
            challenge.challenge_id,
            challenge.challenge,
        ),
    )
    await registry.acquire_owner(request, owner_credential=owner_buffer)
    assert owner_buffer == bytearray(len(owner_buffer))

    rejected_buffer = _secret_buffer(CLIENT_A_SECRET)
    with pytest.raises(LifecycleAuthorityError):
        await registry.register_client(
            ClientRegisterRequest(
                engine_instance_id=secrets.token_urlsafe(32),
                client_instance_id=CLIENT_A,
                workspace_id=WORKSPACE_A,
                lifecycle_mode="local-owned",
            ),
            registration_credential=rejected_buffer,
        )
    assert rejected_buffer == bytearray(len(rejected_buffer))


@pytest.mark.asyncio
async def test_owner_generation_cas_expiry_reacquisition_and_repeat_release() -> None:
    registry, process_identity, clock, _ = registry_fixture()
    acquired = await _acquire_owner(registry, owner_acquire(process_identity))
    assert acquired.owner_generation == 1
    assert acquired.expires_at_unix == 1_030.0

    with pytest.raises(LifecycleAuthorityError) as stale:
        await _renew_owner(registry, owner_lease(process_identity, generation=2))
    assert stale.value.code == "owner_generation_conflict"

    clock.advance(31)
    with pytest.raises(LifecycleAuthorityError) as expired:
        await _renew_owner(registry, owner_lease(process_identity))
    assert expired.value.code == "owner_expired"

    reacquired = await _acquire_owner(registry, owner_acquire(process_identity, bootstrap=None, expected_generation=1))
    assert reacquired.owner_generation == 2
    released = await _release_owner(registry, owner_lease(process_identity, generation=2))
    repeated = await _release_owner(registry, owner_lease(process_identity, generation=2))
    assert released.result == "released"
    assert repeated.result == "already_released"


@pytest.mark.asyncio
async def test_owner_renewal_during_drain_extends_control_authority() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(20)
    renewed = await _renew_owner(registry, owner_lease(process_identity))
    await _renew_client(registry, client_lease(registration))
    assert renewed.result == "renewed"
    clock.advance(20)
    accepted = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(process_identity, drained.drain_generation).model_dump(),
            outcome="accepted",
        ),
    )
    assert accepted.result == "shutdown_started"
    assert accepted.session_admission_open is False


@pytest.mark.asyncio
async def test_begin_control_drain_recovers_a_lost_response_exactly_once() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    first = await _begin_control_drain(registry, request)
    epoch = registry.admission_epoch
    generation = registry._drain_generation

    replay = request.model_copy()
    _remember_proofs(
        replay,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    recovered = await _begin_control_drain(registry, replay)

    assert recovered == first
    assert recovered.control_request_id == request.control_request_id
    assert registry.admission_epoch == epoch
    assert registry._drain_generation == generation
    assert registry._drain is not None
    assert registry._drain.generation == first.drain_generation


@pytest.mark.asyncio
async def test_expired_drain_requester_reopens_registration_once_and_keeps_tombstone() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    committed = await _begin_control_drain(registry, request)

    replay = request.model_copy()
    _remember_proofs(
        replay,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    assert await _begin_control_drain(registry, replay) == committed

    clock.advance(20)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(9)
    with pytest.raises(LifecycleAuthorityError) as still_draining:
        await _register_client(
            registry,
            client_register(
                process_identity,
                client_id=CLIENT_B,
                workspace_id=WORKSPACE_B,
                credential=CLIENT_B_SECRET,
            ),
        )
    assert still_draining.value.code == "drain_in_progress"
    assert registry.admission_epoch == committed.admission_epoch

    clock.advance(1)
    replacement = await _register_client(
        registry,
        client_register(
            process_identity,
            client_id=CLIENT_B,
            workspace_id=WORKSPACE_B,
            credential=CLIENT_B_SECRET,
        ),
    )
    rolled_back_epoch = committed.admission_epoch + 1
    assert registry.admission_epoch == rolled_back_epoch
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry.authority_snapshot()["session_admission_open"] is True
    assert registry.authority_snapshot()["turn_admission_open"] is True
    assert registry.authority_snapshot()["registrations_open"] is True
    assert request.control_request_id in registry._control_request_ids
    assert registry._drain is not None
    assert registry._drain.hard_signal_authorization_id is None
    assert registry._drain.hard_signal_outcome is None

    await _renew_client(
        registry,
        client_lease(replacement, credential=CLIENT_B_SECRET),
    )
    assert registry.admission_epoch == rolled_back_epoch

    reused = begin_drain(
        registry,
        process_identity,
        replacement,
        registration_secret=CLIENT_B_SECRET,
        control_request_id=request.control_request_id,
    )
    with pytest.raises(LifecycleAuthorityError) as old_id:
        await _begin_control_drain(registry, reused)
    assert old_id.value.code == "control_request_conflict"
    assert registry.admission_epoch == rolled_back_epoch

    fresh = await _begin_control_drain(
        registry,
        begin_drain(
            registry,
            process_identity,
            replacement,
            registration_secret=CLIENT_B_SECRET,
            control_request_id="r" * 43,
        ),
    )
    assert fresh.result == "draining"
    assert fresh.drain_generation == committed.drain_generation + 1
    assert fresh.admission_epoch == rolled_back_epoch + 1


async def _expired_requester_drain(
    *,
    keep_owner_live: bool = True,
) -> tuple[SessionRegistry, EngineProcessIdentity, Clock, Any, BeginControlDrainRequest, Any]:
    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    committed = await _begin_control_drain(registry, request)
    if keep_owner_live:
        clock.advance(20)
        await _renew_owner(registry, owner_lease(process_identity))
        clock.advance(10)
    else:
        clock.advance(30)
    return registry, process_identity, clock, registration, request, committed


@pytest.mark.asyncio
async def test_expired_requester_concurrent_registrations_serialize_one_rollback() -> None:
    registry, process_identity, _, _, _, committed = await _expired_requester_drain()
    ready_count = 0
    ready_lock = asyncio.Lock()
    both_ready = asyncio.Event()
    release = asyncio.Event()

    async def register(
        client_id: str,
        workspace_id: str,
        credential: str,
    ) -> Any:
        nonlocal ready_count
        async with ready_lock:
            ready_count += 1
            if ready_count == 2:
                both_ready.set()
        await release.wait()
        return await _register_client(
            registry,
            client_register(
                process_identity,
                client_id=client_id,
                workspace_id=workspace_id,
                credential=credential,
            ),
        )

    tasks = [
        asyncio.create_task(
            register(CLIENT_B, WORKSPACE_B, CLIENT_B_SECRET),
        ),
        asyncio.create_task(
            register(
                "client-instance-c-000000000000000",
                "workspace:v1:sha256:" + "c" * 64,
                "client-c-proof-material-000000000000000000",
            ),
        ),
    ]
    await both_ready.wait()
    release.set()
    registrations = await asyncio.gather(*tasks)

    assert {item.client_instance_id for item in registrations} == {
        CLIENT_B,
        "client-instance-c-000000000000000",
    }
    assert registry.admission_epoch == committed.admission_epoch + 1
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry._control_request_ids == {"q" * 43}


@pytest.mark.asyncio
async def test_expired_owner_with_live_requester_does_not_rollback_drain() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(20)
    await _renew_client(registry, client_lease(registration))
    clock.advance(10)

    with pytest.raises(LifecycleAuthorityError) as closed:
        await _register_client(
            registry,
            client_register(
                process_identity,
                client_id=CLIENT_B,
                workspace_id=WORKSPACE_B,
                credential=CLIENT_B_SECRET,
            ),
        )

    assert closed.value.code == "drain_in_progress"
    assert registry.admission_epoch == committed.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == "draining"
    assert registry.authority_snapshot()["registrations_open"] is False


@pytest.mark.asyncio
async def test_live_drain_requester_detach_rolls_back_once() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    committed = await _begin_control_drain(registry, request)

    detached = await _detach_client(
        registry,
        client_lease(registration),
    )
    rolled_back_epoch = committed.admission_epoch + 1
    assert detached.result == "detached"
    assert detached.admission_epoch == rolled_back_epoch
    assert registry.admission_epoch == rolled_back_epoch
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry.authority_snapshot()["session_admission_open"] is True
    assert registry.authority_snapshot()["turn_admission_open"] is True
    assert registry.authority_snapshot()["registrations_open"] is True
    assert registry._control_request_ids == {"q" * 43}

    repeated = await _detach_client(
        registry,
        client_lease(registration),
    )
    assert repeated.result == "already_detached"
    await _renew_owner(registry, owner_lease(process_identity))
    assert registry.admission_epoch == rolled_back_epoch


@pytest.mark.asyncio
async def test_owner_renewal_repairs_expired_requester_once() -> None:
    registry, process_identity, _, _, _, committed = await _expired_requester_drain()

    renewed = await _renew_owner(registry, owner_lease(process_identity))
    rolled_back_epoch = committed.admission_epoch + 1
    assert renewed.result == "renewed"
    assert registry.admission_epoch == rolled_back_epoch
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry.authority_snapshot()["session_admission_open"] is True
    assert registry.authority_snapshot()["turn_admission_open"] is True
    assert registry.authority_snapshot()["registrations_open"] is True
    assert registry._control_request_ids == {"q" * 43}

    await _renew_owner(registry, owner_lease(process_identity))
    assert registry.admission_epoch == rolled_back_epoch


@pytest.mark.asyncio
async def test_expired_requester_rolls_back_definitive_rejection_once() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    committed = await _begin_control_drain(registry, request)
    rollback_ready = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    assert rollback_ready.result == "rollback_permitted"

    clock.advance(20)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(10)
    replacement = await _register_client(
        registry,
        client_register(
            process_identity,
            client_id=CLIENT_B,
            workspace_id=WORKSPACE_B,
            credential=CLIENT_B_SECRET,
        ),
    )

    assert replacement.result == "registered"
    assert registry.admission_epoch == committed.admission_epoch + 1
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry._control_request_ids == {"q" * 43}


@pytest.mark.asyncio
@pytest.mark.parametrize("orphaning", ["expired", "detached"])
async def test_abandoned_outcome_repairs_newly_safe_orphaned_drain_once(
    orphaning: str,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    committed = await _begin_control_drain(registry, request)
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    assert pending.result == "hard_signal_decision_pending"
    assert pending.registrations_open is False
    preparation = await registry.prepare_hard_signal(
        HardSignalPrepareRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            pid=process_identity.pid,
            os_process_start_token=process_identity.os_process_start_token,
        ),
        owner_credential=_secret_buffer(OWNER_SECRET),
    )

    if orphaning == "expired":
        clock.advance(20)
        await _renew_owner(registry, owner_lease(process_identity))
        clock.advance(10)
    else:
        detached = await _detach_client(
            registry,
            client_lease(registration),
        )
        assert detached.result == "detached"
        assert registry.authority_snapshot()["drain_phase"] == (
            "hard_signal_decision_pending"
        )
        assert registry.admission_epoch == committed.admission_epoch
        clock.advance(29)
        await _renew_owner(registry, owner_lease(process_identity))
        clock.advance(1)

    abandoned = await registry.record_hard_signal_outcome(
        HardSignalOutcomeRequest(
            authorization_id=preparation.authorization_id,
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="abandoned",
        ),
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    rolled_back_epoch = committed.admission_epoch + 1
    assert abandoned.result == "rolled_back"
    assert abandoned.admission_epoch == rolled_back_epoch
    assert abandoned.session_admission_open is True
    assert abandoned.turn_admission_open is True
    assert abandoned.registrations_open is True
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry._control_request_ids == {"q" * 43}

    await _renew_owner(registry, owner_lease(process_identity))
    assert registry.admission_epoch == rolled_back_epoch


@pytest.mark.asyncio
async def test_definitive_rejection_automatic_rollback_replays_exact_response() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(20)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(9)
    boundary_times = iter([clock.value, clock.value, clock.value + 1])

    def boundary_clock() -> float:
        return next(boundary_times, clock.value + 1)

    registry._clock = boundary_clock
    request = GracefulControlResultRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        outcome="definitive_rejection",
    )

    first = await _record_graceful_control(registry, request)
    rolled_back_epoch = first.admission_epoch
    assert first.result == "rolled_back"
    replay = await _record_graceful_control(registry, request)
    assert replay == first
    assert registry.admission_epoch == rolled_back_epoch

    with pytest.raises(LifecycleAuthorityError):
        await _record_graceful_control(
            registry,
            request.model_copy(update={"outcome": "accepted"}),
        )
    with pytest.raises(LifecycleAuthorityError):
        await _record_graceful_control(
            registry,
            request.model_copy(
                update={"drain_generation": committed.drain_generation + 1},
            ),
        )
    with pytest.raises(LifecycleAuthorityError):
        await _record_graceful_control(
            registry,
            request.model_copy(update={"owner_generation": 2}),
        )
    with pytest.raises(LifecycleAuthorityError):
        await registry.record_graceful_control(
            request,
            owner_credential=_secret_buffer("foreign-owner-credential-material"),
        )
    assert registry.admission_epoch == rolled_back_epoch


@pytest.mark.asyncio
async def test_abandoned_automatic_rollback_replays_only_exact_authorized_outcome() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    authorization = await registry.prepare_hard_signal(
        HardSignalPrepareRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            pid=process_identity.pid,
            os_process_start_token=process_identity.os_process_start_token,
        ),
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    await _detach_client(registry, client_lease(registration))
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(1)
    request = HardSignalOutcomeRequest(
        authorization_id=authorization.authorization_id,
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        outcome="abandoned",
    )

    first = await registry.record_hard_signal_outcome(
        request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    rolled_back_epoch = first.admission_epoch
    assert first.result == "rolled_back"
    replay = await registry.record_hard_signal_outcome(
        request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert replay == first

    for mismatched in [
        request.model_copy(update={"outcome": "sent"}),
        request.model_copy(update={"authorization_id": "f" * 43}),
        request.model_copy(
            update={"drain_generation": committed.drain_generation + 1},
        ),
        request.model_copy(update={"owner_generation": 2}),
    ]:
        with pytest.raises(LifecycleAuthorityError):
            await registry.record_hard_signal_outcome(
                mismatched,
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
    with pytest.raises(LifecycleAuthorityError):
        await registry.record_hard_signal_outcome(
            request,
            owner_credential=_secret_buffer("foreign-owner-credential-material"),
        )
    assert registry.admission_epoch == rolled_back_epoch


@pytest.mark.asyncio
async def test_explicit_rollback_returns_prehelper_automatic_rollback() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    clock.advance(20)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(10)

    rolled_back = await _rollback_control_drain(
        registry,
        drain_control(process_identity, committed.drain_generation),
    )
    assert rolled_back.result == "rolled_back"
    assert registry.admission_epoch == committed.admission_epoch + 1
    rolled_back_epoch = registry.admission_epoch
    replay = await _rollback_control_drain(
        registry,
        drain_control(process_identity, committed.drain_generation),
    )
    assert replay == rolled_back
    assert registry.admission_epoch == rolled_back_epoch
    with pytest.raises(LifecycleAuthorityError):
        await _rollback_control_drain(
            registry,
            drain_control(
                process_identity,
                committed.drain_generation + 1,
            ),
        )
    with pytest.raises(LifecycleAuthorityError):
        await registry.rollback_control_drain(
            drain_control(process_identity, committed.drain_generation),
            owner_credential=_secret_buffer("foreign-owner-credential-material"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry_point",
    ["register_client", "renew_owner", "detach_client"],
)
@pytest.mark.parametrize(
    ("phase", "authorization_id", "outcome", "recovery_forbidden"),
    [
        ("shutdown_started", None, None, False),
        ("hard_signal_decision_pending", None, None, False),
        ("hard_signal_decision_pending", "a" * 43, None, False),
        ("signal_sent", "a" * 43, "sent", True),
        ("process_exited", "a" * 43, "process_exited", False),
    ],
    ids=[
        "graceful-accepted",
        "graceful-uncertain",
        "hard-signal-prepared",
        "hard-signal-sent",
        "process-exited",
    ],
)
async def test_expired_requester_does_not_change_unsafe_or_terminal_phase(
    entry_point: str,
    phase: str,
    authorization_id: str | None,
    outcome: str | None,
    recovery_forbidden: bool,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    assert registry._drain is not None
    registry._drain.phase = phase
    registry._drain.hard_signal_authorization_id = authorization_id
    registry._drain.hard_signal_authorization_expires_at_unix = (
        clock.value + 30 if authorization_id is not None else None
    )
    registry._drain.hard_signal_outcome = outcome
    registry._drain.recovery_forbidden = recovery_forbidden
    before = (
        registry._drain.hard_signal_authorization_id,
        registry._drain.hard_signal_authorization_expires_at_unix,
        registry._drain.hard_signal_outcome,
        registry._drain.recovery_forbidden,
    )
    clock.advance(20)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(10)

    if entry_point == "register_client":
        with pytest.raises(LifecycleAuthorityError) as closed:
            await _register_client(
                registry,
                client_register(
                    process_identity,
                    client_id=CLIENT_B,
                    workspace_id=WORKSPACE_B,
                    credential=CLIENT_B_SECRET,
                ),
            )
        assert closed.value.code == "drain_in_progress"
    elif entry_point == "renew_owner":
        renewed = await _renew_owner(
            registry,
            owner_lease(process_identity),
        )
        assert renewed.result == "renewed"
    else:
        detached = await _detach_client(
            registry,
            client_lease(registration),
        )
        assert detached.result == "detached"
    assert registry.admission_epoch == committed.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == phase
    assert registry.authority_snapshot()["registrations_open"] is False
    assert registry._drain is not None
    assert (
        registry._drain.hard_signal_authorization_id,
        registry._drain.hard_signal_authorization_expires_at_unix,
        registry._drain.hard_signal_outcome,
        registry._drain.recovery_forbidden,
    ) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguity", ["clock", "registration-binding"])
async def test_ambiguous_requester_expiry_does_not_rollback(
    ambiguity: str,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    await _renew_owner(registry, owner_lease(process_identity))
    if ambiguity == "clock":
        clock.value = float("nan")
    else:
        clock.advance(30)
        registry._registrations[registration.registration_id].generation += 1

    with pytest.raises(LifecycleAuthorityError) as closed:
        await _register_client(
            registry,
            client_register(
                process_identity,
                client_id=CLIENT_B,
                workspace_id=WORKSPACE_B,
                credential=CLIENT_B_SECRET,
            ),
        )

    assert closed.value.code == "drain_in_progress"
    assert registry.admission_epoch == committed.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == "draining"


@pytest.mark.asyncio
async def test_absent_bound_requester_rolls_back_on_next_registration() -> None:
    registry, process_identity, _, registration, _, committed = await _expired_requester_drain()
    del registry._registrations[registration.registration_id]
    del registry._registration_by_client[registration.client_instance_id]

    replacement = await _register_client(
        registry,
        client_register(
            process_identity,
            client_id=CLIENT_B,
            workspace_id=WORKSPACE_B,
            credential=CLIENT_B_SECRET,
        ),
    )

    assert replacement.result == "registered"
    assert registry.admission_epoch == committed.admission_epoch + 1
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry_point",
    [
        "renew_client",
        "detach_client",
        "admit_session",
        "admit_turn",
        "release_owner",
        "acquire_owner",
        "begin_control_drain",
        "record_graceful_control",
        "prepare_hard_signal",
        "record_hard_signal_outcome",
        "rollback_control_drain",
    ],
)
async def test_locked_lifecycle_entry_points_repair_orphaned_drain(
    entry_point: str,
) -> None:
    registry, process_identity, _, registration, request, committed = (
        await _expired_requester_drain(
            keep_owner_live=entry_point != "acquire_owner",
        )
    )
    admission_advance = 0

    if entry_point == "renew_client":
        with pytest.raises(LifecycleAuthorityError) as rejected:
            await _renew_client(registry, client_lease(registration))
        assert rejected.value.code == "registration_expired"
    elif entry_point == "detach_client":
        detached = await _detach_client(registry, client_lease(registration))
        assert detached.result == "detached"
    elif entry_point == "admit_session":
        class Prepared:
            async def prepare_start(
                self,
                *,
                admission_serialized: bool = False,
            ) -> None:
                assert admission_serialized is True

        await registry.admit_session(
            SessionRecord(
                session_id="session-after-expiry",
                status=SessionStatus.STARTING,
            ),
            Prepared(),
        )
        admission_advance = 1
    elif entry_point == "admit_turn":
        class Accepted:
            disposition = "started"

        async def accept() -> Accepted:
            return Accepted()

        assert isinstance(await registry.admit_turn(accept), Accepted)
        admission_advance = 1
    elif entry_point == "release_owner":
        released = await _release_owner(
            registry,
            owner_lease(process_identity),
        )
        assert released.result == "released"
    elif entry_point == "acquire_owner":
        acquired = await _acquire_owner(
            registry,
            owner_acquire(
                process_identity,
                bootstrap=None,
                expected_generation=1,
            ),
        )
        assert acquired.owner_generation == 2
    elif entry_point == "begin_control_drain":
        replay = request.model_copy()
        _remember_proofs(
            replay,
            owner_credential=OWNER_SECRET,
            registration_credential=CLIENT_A_SECRET,
        )
        with pytest.raises(LifecycleAuthorityError) as rejected:
            await _begin_control_drain(registry, replay)
        assert rejected.value.code == "registration_expired"
    elif entry_point == "record_graceful_control":
        with pytest.raises(LifecycleAuthorityError) as rejected:
            await _record_graceful_control(
                registry,
                GracefulControlResultRequest(
                    **drain_control(
                        process_identity,
                        committed.drain_generation,
                    ).model_dump(),
                    outcome="accepted",
                ),
            )
        assert rejected.value.code == "drain_conflict"
    elif entry_point == "prepare_hard_signal":
        control = drain_control(
            process_identity,
            committed.drain_generation,
        )
        _take_proofs(control)
        with pytest.raises(LifecycleAuthorityError) as rejected:
            await registry.prepare_hard_signal(
                HardSignalPrepareRequest(
                    **control.model_dump(),
                    pid=process_identity.pid,
                    os_process_start_token=process_identity.os_process_start_token,
                ),
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
        assert rejected.value.code == "drain_conflict"
    elif entry_point == "record_hard_signal_outcome":
        control = drain_control(
            process_identity,
            committed.drain_generation,
        )
        _take_proofs(control)
        with pytest.raises(LifecycleAuthorityError) as rejected:
            await registry.record_hard_signal_outcome(
                HardSignalOutcomeRequest(
                    **control.model_dump(),
                    authorization_id="a" * 43,
                    outcome="abandoned",
                ),
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
        assert rejected.value.code == "hard_signal_authorization_conflict"
    else:
        rolled_back = await _rollback_control_drain(
            registry,
            drain_control(
                process_identity,
                committed.drain_generation,
            ),
        )
        assert rolled_back.result == "rolled_back"

    assert registry.admission_epoch == (
        committed.admission_epoch + 1 + admission_advance
    )
    assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
    assert registry._control_request_ids == {"q" * 43}


@pytest.mark.asyncio
async def test_drain_route_recovers_a_lost_response_and_rejects_missing_or_malformed_id() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    app = create_app(SessionService(registry=registry))
    client = TestClient(app)
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    _take_proofs(request)
    payload = request.model_dump(mode="json")
    headers = {
        "X-Breadboard-Owner-Credential": OWNER_SECRET,
        "X-Breadboard-Registration-Credential": CLIENT_A_SECRET,
    }

    missing = dict(payload)
    del missing["control_request_id"]
    malformed = {**payload, "control_request_id": "bad"}
    for rejected_payload in (missing, malformed):
        rejected = client.post(
            "/v1/engine/control/drain",
            json=rejected_payload,
            headers=headers,
        )
        assert rejected.status_code == 422

    first = client.post(
        "/v1/engine/control/drain",
        json=payload,
        headers=headers,
    )
    epoch = registry.admission_epoch
    generation = registry._drain_generation
    replay = client.post(
        "/v1/engine/control/drain",
        json=payload,
        headers=headers,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["control_request_id"] == request.control_request_id
    assert registry.admission_epoch == epoch
    assert registry._drain_generation == generation
    assert registry._drain is not None
    assert registry._drain.generation == replay.json()["drain_generation"]


@pytest.mark.asyncio
async def test_begin_control_drain_rejects_operation_id_reuse_and_changed_bindings() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    await _begin_control_drain(registry, request)

    changed_epoch = request.model_copy(
        update={"expected_admission_epoch": request.expected_admission_epoch + 1},
    )
    _remember_proofs(
        changed_epoch,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as mismatched:
        await _begin_control_drain(registry, changed_epoch)
    assert mismatched.value.code == "control_request_conflict"

    different_id = request.model_copy(update={"control_request_id": "r" * 43})
    _remember_proofs(
        different_id,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as competing:
        await _begin_control_drain(registry, different_id)
    assert competing.value.code == "drain_conflict"

    wrong_engine = request.model_copy(update={"engine_boot_id": "b" * 43})
    _remember_proofs(
        wrong_engine,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as engine:
        await _begin_control_drain(registry, wrong_engine)
    assert engine.value.code == "engine_identity_mismatch"

    wrong_requester = request.model_copy(
        update={"requester_client_instance_id": CLIENT_B},
    )
    _remember_proofs(
        wrong_requester,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as requester:
        await _begin_control_drain(registry, wrong_requester)
    assert requester.value.code == "registration_identity_mismatch"

    clock.advance(31)
    stale = request.model_copy()
    _remember_proofs(
        stale,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as expired_owner:
        await _begin_control_drain(registry, stale)
    assert expired_owner.value.code == "owner_expired"


@pytest.mark.asyncio
async def test_begin_control_drain_replay_rejects_a_cross_requester_operation_id() -> None:
    registry, process_identity, _, _ = registry_fixture()
    await _acquire_owner(registry, owner_acquire(process_identity))
    requester = await _register_client(registry, client_register(process_identity))
    other = await _register_client(
        registry,
        client_register(
            process_identity,
            client_id=CLIENT_B,
            workspace_id=WORKSPACE_B,
            credential=CLIENT_B_SECRET,
        ),
    )
    await _detach_client(
        registry,
        client_lease(other, credential=CLIENT_B_SECRET),
    )
    request = begin_drain(
        registry,
        process_identity,
        requester,
        control_request_id="q" * 43,
    )
    await _begin_control_drain(registry, request)

    cross_requester = request.model_copy(
        update={
            "registration_id": other.registration_id,
            "requester_registration_generation": other.registration_generation,
            "requester_client_instance_id": other.client_instance_id,
        },
    )
    _remember_proofs(
        cross_requester,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_B_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as rejected:
        await _begin_control_drain(registry, cross_requester)
    assert rejected.value.code == "registration_expired"


@pytest.mark.asyncio
async def test_begin_control_drain_replay_requires_live_registration_and_original_owner() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    await _begin_control_drain(registry, request)

    clock.advance(20)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(11)
    stale_registration = request.model_copy()
    _remember_proofs(
        stale_registration,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as expired_registration:
        await _begin_control_drain(registry, stale_registration)
    assert expired_registration.value.code == "registration_expired"

    registry, process_identity, clock, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    await _begin_control_drain(registry, request)
    clock.advance(20)
    renewed_registration = await _renew_client(
        registry,
        client_lease(registration),
    )
    clock.advance(11)
    reacquired = await _acquire_owner(
        registry,
        owner_acquire(
            process_identity,
            bootstrap=None,
            expected_generation=1,
        ),
    )
    changed_owner = begin_drain(
        registry,
        process_identity,
        renewed_registration,
        owner_generation=reacquired.owner_generation,
        epoch=request.expected_admission_epoch,
        control_request_id=request.control_request_id,
    )
    with pytest.raises(LifecycleAuthorityError) as owner:
        await _begin_control_drain(registry, changed_owner)
    assert owner.value.code == "control_request_conflict"


@pytest.mark.asyncio
async def test_rolled_back_control_request_id_cannot_be_reused_for_a_new_drain() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    drained = await _begin_control_drain(registry, request)
    rollback_ready = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(process_identity, drained.drain_generation).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    rolled_back = await _rollback_control_drain(
        registry,
        drain_control(process_identity, rollback_ready.drain_generation),
    )

    reused = begin_drain(
        registry,
        process_identity,
        registration,
        epoch=rolled_back.admission_epoch,
        control_request_id=request.control_request_id,
    )
    with pytest.raises(LifecycleAuthorityError) as reuse:
        await _begin_control_drain(registry, reused)
    assert reuse.value.code == "control_request_conflict"

    next_request = begin_drain(
        registry,
        process_identity,
        registration,
        epoch=rolled_back.admission_epoch,
        control_request_id="r" * 43,
    )
    next_drain = await _begin_control_drain(registry, next_request)
    assert next_drain.control_request_id == next_request.control_request_id
    assert next_drain.drain_generation == drained.drain_generation + 1
    assert next_drain.admission_epoch == rolled_back.admission_epoch + 1

    next_rollback_ready = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                next_drain.drain_generation,
            ).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    next_rolled_back = await _rollback_control_drain(
        registry,
        drain_control(
            process_identity,
            next_rollback_ready.drain_generation,
        ),
    )
    historical_reuse = begin_drain(
        registry,
        process_identity,
        registration,
        epoch=next_rolled_back.admission_epoch,
        control_request_id=request.control_request_id,
    )
    with pytest.raises(LifecycleAuthorityError) as historical:
        await _begin_control_drain(registry, historical_reuse)
    assert historical.value.code == "control_request_conflict"


@pytest.mark.asyncio
async def test_control_request_capacity_preserves_replay_and_known_id_rejection() -> None:
    registry, process_identity, _, registration = await owned_registered_registry(
        control_request_capacity=2,
    )
    first_request = begin_drain(
        registry,
        process_identity,
        registration,
        control_request_id="q" * 43,
    )
    first = await _begin_control_drain(registry, first_request)
    first_ready = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(process_identity, first.drain_generation).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    first_rolled_back = await _rollback_control_drain(
        registry,
        drain_control(process_identity, first_ready.drain_generation),
    )
    second_request = begin_drain(
        registry,
        process_identity,
        registration,
        epoch=first_rolled_back.admission_epoch,
        control_request_id="r" * 43,
    )
    second = await _begin_control_drain(registry, second_request)

    replay = second_request.model_copy()
    _remember_proofs(
        replay,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    assert await _begin_control_drain(registry, replay) == second

    known = first_request.model_copy()
    _remember_proofs(
        known,
        owner_credential=OWNER_SECRET,
        registration_credential=CLIENT_A_SECRET,
    )
    with pytest.raises(LifecycleAuthorityError) as reused:
        await _begin_control_drain(registry, known)
    assert reused.value.code == "control_request_conflict"

    second_ready = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(process_identity, second.drain_generation).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    second_rolled_back = await _rollback_control_drain(
        registry,
        drain_control(process_identity, second_ready.drain_generation),
    )
    epoch = registry.admission_epoch
    generation = registry._drain_generation
    new_request = begin_drain(
        registry,
        process_identity,
        registration,
        epoch=second_rolled_back.admission_epoch,
        control_request_id="s" * 43,
    )
    with pytest.raises(LifecycleAuthorityError) as capacity:
        await _begin_control_drain(registry, new_request)
    assert capacity.value.code == "control_request_capacity_exceeded"
    assert registry.admission_epoch == epoch
    assert registry._drain_generation == generation
    assert registry._control_request_ids == {"q" * 43, "r" * 43}
    assert all(
        isinstance(control_request_id, str)
        for control_request_id in registry._control_request_ids
    )

    restarted, restarted_identity, _, restarted_registration = (
        await owned_registered_registry(control_request_capacity=2)
    )
    after_restart = await _begin_control_drain(
        restarted,
        begin_drain(
            restarted,
            restarted_identity,
            restarted_registration,
            control_request_id="q" * 43,
        ),
    )
    assert after_restart.drain_generation == 1


@pytest.mark.asyncio
async def test_expired_owner_reacquisition_transfers_only_recoverable_active_drain() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(20)
    await _renew_client(registry, client_lease(registration))
    clock.advance(11)
    with pytest.raises(LifecycleAuthorityError) as expired:
        await _record_graceful_control(
            registry,
            GracefulControlResultRequest(
                **drain_control(process_identity, drained.drain_generation).model_dump(),
                outcome="accepted",
            ),
        )
    assert expired.value.code == "owner_expired"

    reacquired = await _acquire_owner(
        registry,
        owner_acquire(
            process_identity,
            bootstrap=None,
            expected_generation=1,
        ),
    )
    assert reacquired.owner_generation == 2
    transferred = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                drained.drain_generation,
                owner_generation=2,
            ).model_dump(),
            outcome="accepted",
        ),
    )
    assert transferred.result == "shutdown_started"
    assert transferred.turn_admission_open is False


@pytest.mark.asyncio
async def test_reacquired_owner_transfers_safe_drain_for_rollback() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(20)
    await _renew_client(registry, client_lease(registration))
    clock.advance(11)
    await _acquire_owner(
        registry,
        owner_acquire(
            process_identity,
            bootstrap=None,
            expected_generation=1,
        ),
    )
    rollback_ready = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                drained.drain_generation,
                owner_generation=2,
            ).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    assert rollback_ready.result == "rollback_permitted"
    assert rollback_ready.session_admission_open is False

    with pytest.raises(LifecycleAuthorityError) as stale:
        await _rollback_control_drain(
            registry,
            drain_control(
                process_identity,
                drained.drain_generation,
                owner_generation=1,
            ),
        )
    assert stale.value.code == "drain_recovery_failed"

    rolled_back = await _rollback_control_drain(
        registry,
        drain_control(
            process_identity,
            drained.drain_generation,
            owner_generation=2,
        ),
    )
    assert rolled_back.result == "rolled_back"
    assert rolled_back.session_admission_open is True


@pytest.mark.asyncio
async def test_rollback_after_expiry_requires_exact_owner_reacquisition() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(process_identity, drained.drain_generation).model_dump(),
            outcome="definitive_rejection",
        ),
    )
    clock.advance(20)
    await _renew_client(registry, client_lease(registration))
    clock.advance(11)
    with pytest.raises(LifecycleAuthorityError) as expired:
        await _rollback_control_drain(
            registry,
            drain_control(process_identity, drained.drain_generation),
        )
    assert expired.value.code == "drain_recovery_failed"
    assert registry.authority_snapshot()["turn_admission_open"] is False

    await _acquire_owner(
        registry,
        owner_acquire(
            process_identity,
            bootstrap=None,
            expected_generation=1,
        ),
    )
    with pytest.raises(LifecycleAuthorityError) as stale:
        await _rollback_control_drain(
            registry,
            drain_control(
                process_identity,
                drained.drain_generation,
                owner_generation=1,
            ),
        )
    assert stale.value.code == "drain_recovery_failed"
    assert registry.authority_snapshot()["session_admission_open"] is False

    recovered = await _rollback_control_drain(
        registry,
        drain_control(
            process_identity,
            drained.drain_generation,
            owner_generation=2,
        ),
    )
    assert recovered.result == "rolled_back"
    assert recovered.session_admission_open is True
    assert recovered.turn_admission_open is True
    assert recovered.registrations_open is True


@pytest.mark.asyncio
async def test_registration_generation_workspace_expiry_identity_and_idempotent_detach() -> None:
    registry, process_identity, clock, _ = registry_fixture()
    first = await _register_client(registry, client_register(process_identity))
    second = await _register_client(registry, client_register(
        process_identity,
        client_id=CLIENT_B,
        workspace_id=WORKSPACE_B,
        credential=CLIENT_B_SECRET,
    ))
    assert {first.workspace_id, second.workspace_id} == {WORKSPACE_A, WORKSPACE_B}
    assert first.expires_at_unix == 1_030.0

    wrong_instance = client_lease(first, engine_instance_id=secrets.token_urlsafe(32))
    with pytest.raises(LifecycleAuthorityError) as wrong:
        await _renew_client(registry, wrong_instance)
    assert wrong.value.code == "engine_identity_mismatch"

    with pytest.raises(LifecycleAuthorityError) as stale:
        await _renew_client(registry, client_lease(first, generation=2))
    assert stale.value.code == "registration_generation_conflict"

    with pytest.raises(LifecycleAuthorityError) as cross_client:
        await _detach_client(registry, client_lease(first, credential=CLIENT_B_SECRET, client_id=CLIENT_B))
    assert cross_client.value.code == "registration_identity_mismatch"

    detached = await _detach_client(registry, client_lease(first))
    repeated = await _detach_client(registry, client_lease(first))
    assert detached.result == "detached"
    assert repeated.result == "already_detached"

    clock.advance(31)
    with pytest.raises(LifecycleAuthorityError) as crashed:
        await _renew_client(registry, client_lease(second, credential=CLIENT_B_SECRET))
    assert crashed.value.code == "registration_expired"
    next_generation = await _register_client(registry, client_register(
        process_identity,
        client_id=CLIENT_B,
        workspace_id=WORKSPACE_B,
        credential=CLIENT_B_SECRET,
    ))
    assert next_generation.registration_generation == 2


@pytest.mark.asyncio
async def test_registration_and_drain_have_one_total_order() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    competing_register = client_register(
        process_identity,
        client_id=CLIENT_B,
        workspace_id=WORKSPACE_B,
        credential=CLIENT_B_SECRET,
    )
    register_result, drain_result = await asyncio.gather(
        _register_client(registry, competing_register),
        _begin_control_drain(registry, begin_drain(registry, process_identity, registration)),
        return_exceptions=True,
    )
    assert register_result.client_instance_id == CLIENT_B
    assert isinstance(drain_result, LifecycleAuthorityError)
    assert drain_result.code == "drain_clients_active"

    registry, process_identity, _, registration = await owned_registered_registry()
    drain_result, register_result = await asyncio.gather(
        _begin_control_drain(registry, begin_drain(registry, process_identity, registration)),
        _register_client(registry, competing_register.model_copy(update={"engine_instance_id": process_identity.engine_instance_id})),
        return_exceptions=True,
    )
    assert drain_result.result == "draining"
    assert isinstance(register_result, LifecycleAuthorityError)
    assert register_result.code == "drain_in_progress"


@pytest.mark.asyncio
async def test_detach_and_drain_are_serialized_without_cross_client_window() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    detached, denied = await asyncio.gather(
        _detach_client(registry, client_lease(registration)),
        _begin_control_drain(registry, begin_drain(registry, process_identity, registration)),
        return_exceptions=True,
    )
    assert detached.result == "detached"
    assert isinstance(denied, LifecycleAuthorityError)
    assert denied.code == "registration_expired"

    registry, process_identity, _, registration = await owned_registered_registry()
    drained, detached = await asyncio.gather(
        _begin_control_drain(registry, begin_drain(registry, process_identity, registration)),
        _detach_client(registry, client_lease(registration)),
    )
    assert drained.result == "draining"
    assert detached.result == "detached"


@pytest.mark.asyncio
async def test_paused_terminal_publish_remains_unresolved_for_drain() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    record = SessionRecord(session_id="paused-terminal", status=SessionStatus.RUNNING)
    turn = TurnRecord(
        input_id="input-paused",
        turn_id="turn-paused",
        client_message_id="message-paused",
        content="work",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    await registry.create(record)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="unused"),
    )
    publish_entered = asyncio.Event()
    release_publish = asyncio.Event()

    async def paused_publish(*_args: Any, **_kwargs: Any) -> None:
        publish_entered.set()
        await release_publish.wait()

    runner.publish_event_async = paused_publish  # type: ignore[method-assign]
    finish_task = asyncio.create_task(runner._finish_turn(turn, "completed"))
    await publish_entered.wait()
    assert turn.terminal_outcome == "completed"
    assert turn.terminal_resolution_committed is False
    with pytest.raises(LifecycleAuthorityError) as unresolved:
        await _begin_control_drain(
            registry,
            begin_drain(registry, process_identity, registration),
        )
    assert unresolved.value.code == "drain_turn_active"
    release_publish.set()
    await finish_task


@pytest.mark.asyncio
async def test_no_state_root_terminal_dispatch_stays_unresolved_and_blocks_drain() -> None:
    registry, process_identity, _, registration = registry_fixture()
    await _acquire_owner(registry, owner_acquire(process_identity))
    registration = await _register_client(registry, client_register(process_identity))
    record = SessionRecord(session_id="no-state-terminal", status=SessionStatus.RUNNING)
    turn = TurnRecord(
        input_id="input-no-state-terminal",
        turn_id="turn-no-state-terminal",
        client_message_id="message-no-state-terminal",
        content="work",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    await registry.create(record)
    assert registry._state_path(record.session_id) is None

    service = SessionService(registry=registry)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="unused"),
    )
    await service._ensure_dispatcher(record)
    assert await runner._finish_turn(turn, "completed") is True
    dispatcher = record.dispatcher_task
    assert dispatcher is not None
    await dispatcher

    assert turn.terminal_outcome == "completed"
    assert turn.terminal_resolution_committed is False
    assert record.terminal_event_envelopes == []
    assert not record.event_log
    with pytest.raises(LifecycleAuthorityError) as unresolved:
        await _begin_control_drain(
            registry,
            begin_drain(registry, process_identity, registration),
        )
    assert unresolved.value.code == "drain_turn_active"
    snapshot = registry.authority_snapshot()
    assert snapshot["session_admission_open"] is True
    assert snapshot["turn_admission_open"] is True
    assert snapshot["registrations_open"] is True


@pytest.mark.asyncio
async def test_terminal_persist_failure_rolls_back_resolution_and_keeps_drain_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, process_identity, _, registration = registry_fixture(state_root=tmp_path)
    await _acquire_owner(registry, owner_acquire(process_identity))
    registration = await _register_client(registry, client_register(process_identity))
    record = SessionRecord(session_id="persist-failure", status=SessionStatus.RUNNING)
    turn = TurnRecord(
        input_id="input-persist-failure",
        turn_id="turn-persist-failure",
        client_message_id="message-persist-failure",
        content="work",
        attachments=(),
        original_disposition="started",
        state="completed",
        terminal_outcome="completed",
    )
    record.turns_by_id[turn.turn_id] = turn
    await registry.create(record)
    terminal_event = SessionEvent(
        type=EventType.TURN_COMPLETED,
        session_id=record.session_id,
        payload={},
        input_id=turn.input_id,
        turn_id=turn.turn_id,
    )

    def fail_persist(_record: SessionRecord) -> None:
        raise OSError("injected durable persistence failure")

    monkeypatch.setattr(registry, "_persist_record_locked", fail_persist)
    with pytest.raises(OSError):
        async with registry.event_persistence_authority():
            await registry.persist(record, terminal_event=terminal_event)
    assert turn.terminal_resolution_committed is False
    assert record.terminal_event_envelopes == []
    with pytest.raises(LifecycleAuthorityError) as unresolved:
        await _begin_control_drain(
            registry,
            begin_drain(registry, process_identity, registration),
        )
    assert unresolved.value.code == "drain_turn_active"
    assert registry.authority_snapshot()["session_admission_open"] is True


@pytest.mark.asyncio
async def test_durable_terminal_envelope_commits_resolution_before_drain(
    tmp_path: Path,
) -> None:
    registry, process_identity, _, registration = registry_fixture(state_root=tmp_path)
    await _acquire_owner(registry, owner_acquire(process_identity))
    registration = await _register_client(registry, client_register(process_identity))
    record = SessionRecord(session_id="persist-success", status=SessionStatus.RUNNING)
    turn = TurnRecord(
        input_id="input-persist-success",
        turn_id="turn-persist-success",
        client_message_id="message-persist-success",
        content="work",
        attachments=(),
        original_disposition="started",
        state="completed",
        terminal_outcome="completed",
    )
    record.turns_by_id[turn.turn_id] = turn
    await registry.create(record)
    terminal_event = SessionEvent(
        type=EventType.TURN_COMPLETED,
        session_id=record.session_id,
        payload={},
        input_id=turn.input_id,
        turn_id=turn.turn_id,
    )
    async with registry.event_persistence_authority():
        await registry.persist(record, terminal_event=terminal_event)
    assert turn.terminal_resolution_committed is True
    state_path = registry._state_path(record.session_id)
    assert state_path is not None
    retained = json.loads(state_path.read_text(encoding="utf-8"))
    assert retained["turns"][0]["terminal_resolution_committed"] is True
    assert retained["terminal_event_envelopes"][0]["turn_id"] == turn.turn_id
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    assert drained.result == "draining"


@pytest.mark.asyncio
async def test_initial_task_is_retained_before_session_becomes_runnable(
    tmp_path: Path,
) -> None:
    registry, _, _, _ = registry_fixture(state_root=tmp_path)
    request = SessionCreateRequest(
        config_path="unused",
        task="accepted initial task",
    )
    record = SessionRecord(
        session_id="durable-initial-task",
        status=SessionStatus.STARTING,
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=request,
    )
    record.runner = runner
    await registry.admit_session(record, runner)
    assert runner._task is None
    state_path = registry._state_path(record.session_id)
    assert state_path is not None
    retained = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(retained["turns"]) == 1
    assert len(retained["submissions"]) == 1
    assert retained["turns"][0]["terminal_resolution_committed"] is False
    assert "accepted initial task" not in state_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_closed_session_admission_blocks_evidence_and_prewarm_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    service = SessionService(registry=registry)
    evidence_calls = 0
    prewarm_calls = 0

    def emit_evidence(**_kwargs: Any) -> list[str]:
        nonlocal evidence_calls
        evidence_calls += 1
        return []

    async def prewarm(*_args: Any, **_kwargs: Any) -> None:
        nonlocal prewarm_calls
        prewarm_calls += 1

    monkeypatch.setattr(bridge_service_module, "primitive_emission_enabled", lambda: True)
    monkeypatch.setattr(bridge_service_module, "emit_session_start_records", emit_evidence)
    monkeypatch.setattr(service, "_maybe_prewarm_request_runtime", prewarm)
    with pytest.raises(LifecycleAuthorityError) as closed:
        await service.create_session(
            SessionCreateRequest(
                config_path="unused",
                task="must not be admitted",
            )
        )
    assert closed.value.code == "admission_closed"
    assert evidence_calls == 0
    assert prewarm_calls == 0


@pytest.mark.asyncio
async def test_admission_and_drain_barrier_are_serialized_and_close_before_response() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    record = SessionRecord(session_id="session-race", status=SessionStatus.RUNNING)
    await registry.create(record)
    entered = asyncio.Event()
    release = asyncio.Event()

    class Accepted:
        disposition = "started"

    async def admit_turn() -> Accepted:
        entered.set()
        await release.wait()
        record.turns_by_id["turn-race"] = TurnRecord(
            input_id="input-race",
            turn_id="turn-race",
            client_message_id="message-race",
            content="hello",
            attachments=(),
            original_disposition="started",
            state="active",
        )
        return Accepted()

    admission_task = asyncio.create_task(registry.admit_turn(admit_turn))
    await entered.wait()
    drain_task = asyncio.create_task(
        _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    )
    release.set()
    await admission_task
    with pytest.raises(LifecycleAuthorityError) as stale_epoch:
        await drain_task
    assert stale_epoch.value.code == "admission_epoch_conflict"
    with pytest.raises(LifecycleAuthorityError) as unresolved:
        await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    assert unresolved.value.code == "drain_turn_active"
    assert registry.authority_snapshot()["turn_admission_open"] is True

    record.turns_by_id["turn-race"].terminal_outcome = "completed"
    record.turns_by_id["turn-race"].terminal_resolution_committed = True
    drained = await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    assert drained.result == "draining"
    assert drained.session_admission_open is False
    assert drained.turn_admission_open is False
    assert drained.registrations_open is False
    with pytest.raises(LifecycleAuthorityError) as closed:
        await registry.admit_turn(lambda: asyncio.sleep(0))
    assert closed.value.code == "admission_closed"
    class NeverStarted:
        prepared = False

        async def prepare_start(self, *, admission_serialized: bool = False) -> None:
            self.prepared = True

    candidate = SessionRecord(session_id="session-after-drain", status=SessionStatus.STARTING)
    candidate_runner = NeverStarted()
    with pytest.raises(LifecycleAuthorityError) as session_closed:
        await registry.admit_session(candidate, candidate_runner)
    assert session_closed.value.code == "admission_closed"
    assert candidate_runner.prepared is False
    assert await registry.get(candidate.session_id) is None


@pytest.mark.asyncio
async def test_drain_denies_another_client_unresolved_turn_and_cross_client_requester() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    other = await _register_client(registry, client_register(
        process_identity,
        client_id=CLIENT_B,
        workspace_id=WORKSPACE_B,
        credential=CLIENT_B_SECRET,
    ))
    with pytest.raises(LifecycleAuthorityError) as clients:
        await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    assert clients.value.code == "drain_clients_active"

    await _detach_client(registry, client_lease(other, credential=CLIENT_B_SECRET))
    record = SessionRecord(session_id="session-active", status=SessionStatus.RUNNING)
    record.turns_by_id["turn-active"] = TurnRecord(
        input_id="input-active",
        turn_id="turn-active",
        client_message_id="message-active",
        content="work",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    await registry.create(record)
    with pytest.raises(LifecycleAuthorityError) as turn:
        await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    assert turn.value.code == "drain_turn_active"

    record.turns_by_id["turn-active"].terminal_outcome = "completed"
    record.turns_by_id["turn-active"].terminal_resolution_committed = True
    forged = begin_drain(registry, process_identity, registration).model_copy(
        update={
            "requester_client_instance_id": CLIENT_B,
        }
    )
    with pytest.raises(LifecycleAuthorityError) as cross_client:
        await _begin_control_drain(registry, forged)
    assert cross_client.value.code == "registration_identity_mismatch"


@pytest.mark.asyncio
async def test_unowned_or_wrong_owner_proof_cannot_invoke_control() -> None:
    registry, process_identity, _, _ = registry_fixture()
    registration = await _register_client(registry, client_register(process_identity))
    with pytest.raises(LifecycleAuthorityError) as unowned:
        await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    assert unowned.value.code == "owner_expired"

    await _acquire_owner(registry, owner_acquire(process_identity))
    with pytest.raises(LifecycleAuthorityError) as wrong_owner:
        await _begin_control_drain(registry, begin_drain(
            registry,
            process_identity,
            registration,
            owner_secret="wrong-owner-proof-material-000000000000000",
        ))
    assert wrong_owner.value.code == "owner_identity_mismatch"
    assert registry.authority_snapshot()["session_admission_open"] is True


@pytest.mark.asyncio
async def test_definitive_rejection_rollback_forbids_signal_and_reopens_atomically() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    drained = await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    graceful = GracefulControlResultRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="definitive_rejection",
    )
    rejected = await _record_graceful_control(registry, graceful)
    assert rejected.result == "rollback_permitted"
    assert rejected.signal_permitted is False
    assert rejected.turn_admission_open is False

    with pytest.raises(LifecycleAuthorityError) as no_signal:
        await _record_hard_signal_outcome(registry, HardSignalOutcomeRequest(authorization_id="a" * 43, **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="sent",))
    assert no_signal.value.code == "drain_conflict"

    rolled_back = await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert rolled_back.result == "rolled_back"
    assert rolled_back.session_admission_open is True
    assert rolled_back.turn_admission_open is True
    assert rolled_back.registrations_open is True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "uncertain"])
async def test_uncertain_control_remains_closed_until_expired_preparation_rollback(
    outcome: str,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                drained.drain_generation,
            ).model_dump(),
            outcome=outcome,
        ),
    )
    assert pending.result == "hard_signal_decision_pending"
    assert pending.signal_permitted is False
    assert pending.turn_admission_open is False
    preparation = await registry.prepare_hard_signal(
        HardSignalPrepareRequest(
            **drain_control(
                process_identity,
                drained.drain_generation,
            ).model_dump(),
            pid=process_identity.pid,
            os_process_start_token=process_identity.os_process_start_token,
        ),
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    abandoned_request = HardSignalOutcomeRequest(
        authorization_id=preparation.authorization_id,
        **drain_control(
            process_identity,
            drained.drain_generation,
        ).model_dump(),
        outcome="abandoned",
    )
    with pytest.raises(LifecycleAuthorityError) as premature:
        await registry.record_hard_signal_outcome(
            abandoned_request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert premature.value.code == "hard_signal_authorization_conflict"
    assert registry.authority_snapshot()["turn_admission_open"] is False

    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(1)
    abandoned = await registry.record_hard_signal_outcome(
        abandoned_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert abandoned.result == "rolled_back"
    assert abandoned.session_admission_open is True


@pytest.mark.asyncio
async def test_accepted_graceful_shutdown_is_generation_bound_and_never_reopens() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    drained = await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    accepted = await _record_graceful_control(registry, GracefulControlResultRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="accepted",
    ))
    assert accepted.result == "shutdown_started"
    assert accepted.signal_permitted is False
    with pytest.raises(LifecycleAuthorityError) as recovery:
        await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert recovery.value.code == "drain_recovery_failed"
    assert registry.authority_snapshot()["session_admission_open"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,expected", [("sent", "signal_sent"), ("process_exited", "process_exited")])
async def test_sent_signal_or_process_exit_can_never_rollback(outcome: str, expected: str) -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    drained = await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    await _record_graceful_control(registry, GracefulControlResultRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="timeout",
    ))
    decision = await _record_hard_signal_outcome(registry, HardSignalOutcomeRequest(authorization_id="a" * 43, **drain_control(process_identity, drained.drain_generation).model_dump(),
    outcome=outcome,))
    assert decision.result == expected
    with pytest.raises(LifecycleAuthorityError) as failure:
        await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert failure.value.code == "drain_recovery_failed"
    assert registry.authority_snapshot()["session_admission_open"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_result"),
    [
        ("sent", "signal_sent"),
        ("process_exited", "process_exited"),
    ],
)
async def test_hard_signal_routes_are_typed_secret_safe_and_generation_bound(
    outcome: str,
    expected_result: str,
) -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(process_identity, drained.drain_generation).model_dump(),
            outcome="timeout",
        ),
    )
    app = create_app(SessionService(registry=registry))
    client = TestClient(app)
    prepare_operation = app.openapi()["paths"][
        "/v1/engine/control/hard-signal/prepare"
    ]["post"]
    outcome_operation = app.openapi()["paths"][
        "/v1/engine/control/hard-signal/outcome"
    ]["post"]
    commit_operation = app.openapi()["paths"][
        "/v1/engine/control/hard-signal/commit"
    ]["post"]
    assert prepare_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HardSignalPrepareRequest"
    )
    assert prepare_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HardSignalPreparationResponse"
    )
    assert outcome_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HardSignalOutcomeRequest"
    )
    assert commit_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HardSignalCommitRequest"
    )
    assert commit_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HardSignalPermitResponse"
    )

    preparation = HardSignalPrepareRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    stale = client.post(
        "/v1/engine/control/hard-signal/prepare",
        json=preparation.model_copy(update={"owner_generation": 2}).model_dump(mode="json"),
        headers={"X-Breadboard-Owner-Credential": OWNER_SECRET},
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "owner_generation_conflict"

    authorization_response = client.post(
        "/v1/engine/control/hard-signal/prepare",
        json=preparation.model_dump(mode="json"),
        headers={"X-Breadboard-Owner-Credential": OWNER_SECRET},
    )
    assert authorization_response.status_code == 200
    authorization = authorization_response.json()
    assert authorization["result"] == "prepared"
    assert authorization["signal_permitted"] is False
    commit = HardSignalCommitRequest(
        **preparation.model_dump(),
        authorization_id=authorization["authorization_id"],
    )
    permit_response = client.post(
        "/v1/engine/control/hard-signal/commit",
        json=commit.model_dump(mode="json"),
        headers={"X-Breadboard-Owner-Credential": OWNER_SECRET},
    )
    assert permit_response.status_code == 200
    assert permit_response.json()["result"] == "signal_permitted"
    assert permit_response.json()["signal_permitted"] is True
    request = HardSignalOutcomeRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        authorization_id=authorization["authorization_id"],
        outcome=outcome,
    )
    response = client.post(
        "/v1/engine/control/hard-signal/outcome",
        json=request.model_dump(mode="json"),
        headers={"X-Breadboard-Owner-Credential": OWNER_SECRET},
    )
    assert response.status_code == 200
    assert response.json()["result"] == expected_result
    assert response.json()["signal_permitted"] is False
    assert OWNER_SECRET not in stale.text
    assert OWNER_SECRET not in authorization_response.text
    assert OWNER_SECRET not in permit_response.text
    assert OWNER_SECRET not in response.text


@pytest.mark.asyncio
async def test_lifecycle_control_never_mutates_retained_session_evidence(tmp_path: Path) -> None:
    registry, process_identity, _, _ = registry_fixture(state_root=tmp_path)
    record = SessionRecord(session_id="retained-evidence", status=SessionStatus.COMPLETED)
    await registry.create(record)
    state_path = registry._state_path(record.session_id)
    assert state_path is not None
    before = hashlib.sha256(state_path.read_bytes()).hexdigest()

    await _acquire_owner(registry, owner_acquire(process_identity))
    registration = await _register_client(registry, client_register(process_identity))
    drained = await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    await _record_graceful_control(registry, GracefulControlResultRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="definitive_rejection",
    ))
    await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    after = hashlib.sha256(state_path.read_bytes()).hexdigest()
    assert after == before

def test_lifecycle_response_models_accept_each_consistent_result_variant() -> None:
    process_identity = identity()
    binding = {
        "engine_instance_id": process_identity.engine_instance_id,
        "engine_boot_id": process_identity.engine_boot_id,
        "launch_id": process_identity.launch_id,
    }
    for result in ("acquired", "renewed", "released", "already_released"):
        response = OwnerLeaseResponse(
            **binding,
            result=result,
            owner_generation=1,
            expires_at_unix=1_030.0 if result in {"acquired", "renewed"} else None,
        )
        assert response.result == result

    client_common = {
        "engine_instance_id": process_identity.engine_instance_id,
        "registration_id": secrets.token_urlsafe(32),
        "registration_generation": 1,
        "client_instance_id": CLIENT_A,
        "workspace_id": WORKSPACE_A,
        "lifecycle_mode": "local-owned",
        "registered_at_unix": 1_000.0,
        "admission_epoch": 0,
    }
    for result in ("registered", "renewed", "detached", "already_detached"):
        response = ClientRegistrationResponse(
            **client_common,
            result=result,
            expires_at_unix=1_030.0 if result in {"registered", "renewed"} else None,
        )
        assert response.result == result

    drain_results = (
        "draining",
        "shutdown_started",
        "rollback_permitted",
        "hard_signal_decision_pending",
        "signal_sent",
        "process_exited",
        "rolled_back",
    )
    for result in drain_results:
        is_open = result == "rolled_back"
        response = DrainControlResponse(
            **binding,
            control_request_id="q" * 43,
            result=result,
            drain_generation=1,
            admission_epoch=1,
            session_admission_open=is_open,
            turn_admission_open=is_open,
            registrations_open=is_open,
            signal_permitted=False,
        )
        assert response.result == result


def test_lifecycle_response_models_reject_contradictory_states() -> None:
    process_identity = identity()
    binding = {
        "engine_instance_id": process_identity.engine_instance_id,
        "engine_boot_id": process_identity.engine_boot_id,
        "launch_id": process_identity.launch_id,
    }
    with pytest.raises(ValidationError):
        OwnerAcquireRequest(
            **binding,
            expected_owner_generation=0,
            owner_credential=OWNER_SECRET,
        )
    with pytest.raises(ValidationError):
        OwnerLeaseResponse(
            **binding,
            result="acquired",
            owner_generation=1,
            expires_at_unix=None,
        )
    with pytest.raises(ValidationError):
        OwnerLeaseResponse(
            **binding,
            result="released",
            owner_generation=1,
            expires_at_unix=1_030.0,
        )

    client_common = {
        "engine_instance_id": process_identity.engine_instance_id,
        "registration_id": secrets.token_urlsafe(32),
        "registration_generation": 1,
        "client_instance_id": CLIENT_A,
        "workspace_id": WORKSPACE_A,
        "lifecycle_mode": "local-owned",
        "registered_at_unix": 1_000.0,
        "admission_epoch": 0,
    }
    with pytest.raises(ValidationError):
        ClientRegistrationResponse(
            **client_common,
            result="registered",
            expires_at_unix=None,
        )
    with pytest.raises(ValidationError):
        ClientRegistrationResponse(
            **client_common,
            result="detached",
            expires_at_unix=1_030.0,
        )
    with pytest.raises(ValidationError):
        ClientRegistrationResponse(
            **{**client_common, "lifecycle_mode": "off"},
            result="registered",
            expires_at_unix=1_030.0,
        )

    contradictory_drains = (
        {
            "result": "rolled_back",
            "session_admission_open": False,
            "turn_admission_open": False,
            "registrations_open": False,
            "signal_permitted": False,
        },
        {
            "result": "draining",
            "session_admission_open": True,
            "turn_admission_open": True,
            "registrations_open": True,
            "signal_permitted": False,
        },
        {
            "result": "signal_sent",
            "session_admission_open": False,
            "turn_admission_open": False,
            "registrations_open": False,
            "signal_permitted": True,
        },
    )
    for contradiction in contradictory_drains:
        with pytest.raises(ValidationError):
            DrainControlResponse(
                **binding,
                control_request_id="q" * 43,
                drain_generation=1,
                admission_epoch=1,
                **contradiction,
            )


def test_owner_acquire_and_client_detach_declare_and_return_410() -> None:
    registry, process_identity, _, _ = registry_fixture()
    app = create_app(SessionService(registry=registry))
    client = TestClient(app)
    openapi = app.openapi()
    assert "410" in openapi["paths"]["/v1/engine/owner/acquire"]["post"]["responses"]
    assert "410" in openapi["paths"]["/v1/engine/clients/detach"]["post"]["responses"]

    owner_response = client.post(
        "/v1/engine/owner/acquire",
        json=OwnerAcquireRequest(
            engine_instance_id=process_identity.engine_instance_id,
            engine_boot_id=process_identity.engine_boot_id,
            launch_id=process_identity.launch_id,
            expected_owner_generation=1,
        ).model_dump(mode="json"),
        headers={"X-Breadboard-Owner-Credential": OWNER_SECRET},
    )
    assert owner_response.status_code == 410
    assert owner_response.json()["error"] == "owner_expired"

    detach_response = client.post(
        "/v1/engine/clients/detach",
        json=ClientLeaseRequest(
            engine_instance_id=process_identity.engine_instance_id,
            registration_id=secrets.token_urlsafe(32),
            registration_generation=1,
            client_instance_id=CLIENT_A,
        ).model_dump(mode="json"),
        headers={"X-Breadboard-Registration-Credential": CLIENT_A_SECRET},
    )
    assert detach_response.status_code == 410
    assert detach_response.json()["error"] == "registration_expired"

def test_client_registration_declares_and_returns_403_for_malformed_proof() -> None:
    registry, process_identity, _, _ = registry_fixture()
    app = create_app(SessionService(registry=registry))
    client = TestClient(app)
    openapi = app.openapi()
    assert "403" in openapi["paths"]["/v1/engine/clients/register"]["post"]["responses"]

    response = client.post(
        "/v1/engine/clients/register",
        json=ClientRegisterRequest(
            engine_instance_id=process_identity.engine_instance_id,
            client_instance_id=CLIENT_A,
            workspace_id=WORKSPACE_A,
            lifecycle_mode="local-owned",
        ).model_dump(mode="json"),
        headers={"X-Breadboard-Registration-Credential": "!" * 32},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "registration_identity_mismatch"




def test_http_contract_is_typed_secret_safe_and_accepts_no_pid_authority(caplog: pytest.LogCaptureFixture) -> None:
    registry, process_identity, _, _ = registry_fixture()
    app = create_app(SessionService(registry=registry))
    client = TestClient(app)
    challenge_response = client.post(
        "/v1/engine/owner/bootstrap-challenge",
        json={
            "engine_instance_id": process_identity.engine_instance_id,
            "engine_boot_id": process_identity.engine_boot_id,
            "launch_id": process_identity.launch_id,
        },
    )
    assert challenge_response.status_code == 200
    challenge = challenge_response.json()
    payload = owner_acquire(process_identity).model_dump(mode="json")
    payload["bootstrap_challenge_id"] = challenge["challenge_id"]
    payload["bootstrap_proof_sha256"] = _bootstrap_proof(
        process_identity,
        BOOTSTRAP,
        OWNER_SECRET,
        challenge["challenge_id"],
        challenge["challenge"],
    )
    assert "bootstrap_credential" not in payload
    assert "owner_credential" not in payload
    response = client.post(
        "/v1/engine/owner/acquire",
        json=payload,
        headers={"X-Breadboard-Owner-Credential": OWNER_SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "bb.engine_owner.v1"
    assert body["lease_ttl_seconds"] == 30
    assert body["renewal_interval_seconds"] == 10
    assert "pid" not in json.dumps(body)

    registration_payload = client_register(process_identity).model_dump(mode="json")
    registration_response = client.post(
        "/v1/engine/clients/register",
        json=registration_payload,
        headers={"X-Breadboard-Registration-Credential": CLIENT_A_SECRET},
    )
    assert registration_response.status_code == 200
    registration_body = registration_response.json()
    assert registration_body["first_slice_contract_id"] == "p30-e4-session-v1"
    assert registration_body["first_slice_schema_sha256"] == (
        "sha256:5757652c22d6aa2eb7a1cc8be1a40021d3f6a15df18d69ca22dc1916a400dbd4"
    )
    assert registration_body["workspace_id"] == WORKSPACE_A

    rejected = client.post(
        "/v1/engine/owner/renew",
        json={
            **owner_lease(process_identity).model_dump(mode="json"),
            "engine_instance_id": "bad",
        },
        headers={
            "X-Breadboard-Owner-Credential": "validation-secret-material-000000000000000"
        },
    )
    assert rejected.status_code == 422

    all_outputs = "\n".join(
        [
            response.text,
            registration_response.text,
            rejected.text,
            repr(registry.authority_snapshot()),
            caplog.text,
            " ".join(sys.argv),
            json.dumps(dict(os.environ), sort_keys=True),
        ]
    )
    for secret in (
        BOOTSTRAP,
        OWNER_SECRET,
        CLIENT_A_SECRET,
        "validation-secret-material-000000000000000",
    ):
        assert secret not in all_outputs


@pytest.mark.asyncio
async def test_graceful_control_committed_accepted_response_replays_exactly() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    request = GracefulControlResultRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        outcome="accepted",
    )

    first = await _record_graceful_control(registry, request)
    replay = await _record_graceful_control(registry, request)

    assert first.result == "shutdown_started"
    assert replay == first
    assert registry.admission_epoch == committed.admission_epoch

    for mismatched in [
        request.model_copy(update={"outcome": "timeout"}),
        request.model_copy(
            update={"drain_generation": committed.drain_generation + 1},
        ),
        request.model_copy(update={"owner_generation": 2}),
    ]:
        with pytest.raises(LifecycleAuthorityError):
            await _record_graceful_control(registry, mismatched)
    with pytest.raises(LifecycleAuthorityError):
        await registry.record_graceful_control(
            request,
            owner_credential=_secret_buffer("foreign-owner-credential-material"),
        )
    assert registry.admission_epoch == committed.admission_epoch


@pytest.mark.asyncio
async def test_graceful_control_committed_timeout_response_replays_exactly() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    request = GracefulControlResultRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        outcome="timeout",
    )

    first = await _record_graceful_control(registry, request)
    replay = await _record_graceful_control(registry, request)

    assert first.result == "hard_signal_decision_pending"
    assert first.signal_permitted is False
    assert replay == first
    assert registry.admission_epoch == committed.admission_epoch

    with pytest.raises(LifecycleAuthorityError):
        await _record_graceful_control(
            registry,
            request.model_copy(update={"outcome": "uncertain"}),
        )
    assert registry.admission_epoch == committed.admission_epoch


@pytest.mark.asyncio
async def test_expired_hard_signal_authorization_can_abandon_and_roll_back_once() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    prepare = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    authorization = await registry.prepare_hard_signal(
        prepare,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(1)
    with pytest.raises(LifecycleAuthorityError) as expired:
        await registry.prepare_hard_signal(
            prepare,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert expired.value.code == "hard_signal_authorization_expired"
    request = HardSignalOutcomeRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        authorization_id=authorization.authorization_id,
        outcome="abandoned",
    )

    first, concurrent_replay = await asyncio.gather(
        registry.record_hard_signal_outcome(
            request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
        registry.record_hard_signal_outcome(
            request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
    )
    replay = await registry.record_hard_signal_outcome(
        request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )

    assert first.result == "rolled_back"
    assert first.admission_epoch == pending.admission_epoch + 1
    assert first.session_admission_open is True
    assert first.turn_admission_open is True
    assert first.registrations_open is True
    assert first.signal_permitted is False
    assert replay == first
    assert concurrent_replay == first
    assert registry.admission_epoch == first.admission_epoch


@pytest.mark.asyncio
async def test_expired_hard_signal_authorization_has_no_signal_authority() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    authorization = await registry.prepare_hard_signal(
        HardSignalPrepareRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            pid=process_identity.pid,
            os_process_start_token=process_identity.os_process_start_token,
        ),
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(1)

    for outcome in ("sent", "process_exited"):
        with pytest.raises(LifecycleAuthorityError) as expired:
            await registry.record_hard_signal_outcome(
                HardSignalOutcomeRequest(
                    **drain_control(
                        process_identity,
                        committed.drain_generation,
                    ).model_dump(),
                    authorization_id=authorization.authorization_id,
                    outcome=outcome,
                ),
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
        assert expired.value.code == "hard_signal_authorization_conflict"

    abandoned = HardSignalOutcomeRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        authorization_id=authorization.authorization_id,
        outcome="abandoned",
    )
    with pytest.raises(LifecycleAuthorityError) as foreign_authorization:
        await registry.record_hard_signal_outcome(
            abandoned.model_copy(update={"authorization_id": "f" * 43}),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert foreign_authorization.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as foreign_owner:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=_secret_buffer("foreign-owner-credential-material"),
        )
    assert foreign_owner.value.code == "owner_identity_mismatch"
    for unsafe_id in ("../authorization", "a" * 42, "a" * 44):
        with pytest.raises(ValidationError):
            HardSignalOutcomeRequest(
                **drain_control(
                    process_identity,
                    committed.drain_generation,
                ).model_dump(),
                authorization_id=unsafe_id,
                outcome="abandoned",
            )

    assert registry.authority_snapshot()["drain_phase"] == (
        "hard_signal_decision_pending"
    )
    assert registry.admission_epoch == pending.admission_epoch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_result", "expected_signal_permitted"),
    [
        ("accepted", "shutdown_started", False),
        ("timeout", "hard_signal_decision_pending", False),
    ],
)
async def test_graceful_control_replay_survives_controller_restart(
    outcome: str,
    expected_result: str,
    expected_signal_permitted: bool,
) -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    request = GracefulControlResultRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        outcome=outcome,
    )
    _take_proofs(request)
    payload = request.model_dump(mode="json")
    headers = {"X-Breadboard-Owner-Credential": OWNER_SECRET}

    first_controller = TestClient(create_app(SessionService(registry=registry)))
    first = first_controller.post(
        "/v1/engine/control/graceful-result",
        json=payload,
        headers=headers,
    )
    first_controller.close()
    restarted_controller = TestClient(create_app(SessionService(registry=registry)))
    replay = restarted_controller.post(
        "/v1/engine/control/graceful-result",
        json=payload,
        headers=headers,
    )
    restarted_controller.close()

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["result"] == expected_result
    assert replay.json()["admission_epoch"] == committed.admission_epoch
    assert replay.json()["signal_permitted"] is expected_signal_permitted
    assert registry.admission_epoch == committed.admission_epoch


@pytest.mark.asyncio
async def test_owner_rotation_preserves_expired_authorization_for_safe_abandonment() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    first_prepare = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    authorization = await registry.prepare_hard_signal(
        first_prepare,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(30)
    reacquired = await _acquire_owner(
        registry,
        owner_acquire(process_identity, expected_generation=1),
    )
    assert reacquired.owner_generation == 2
    current_prepare = first_prepare.model_copy(update={"owner_generation": 2})

    with pytest.raises(LifecycleAuthorityError) as prepare_expired:
        await registry.prepare_hard_signal(
            current_prepare,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert prepare_expired.value.code == "hard_signal_authorization_expired"

    abandoned = HardSignalOutcomeRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
            owner_generation=2,
        ).model_dump(),
        authorization_id=authorization.authorization_id,
        outcome="abandoned",
    )
    with pytest.raises(LifecycleAuthorityError) as signal_expired:
        await registry.record_hard_signal_outcome(
            abandoned.model_copy(update={"outcome": "sent"}),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert signal_expired.value.code == "hard_signal_authorization_conflict"

    rollback = DrainControlRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
            owner_generation=2,
        ).model_dump(),
    )
    first, concurrent_replay = await asyncio.gather(
        registry.rollback_control_drain(
            rollback,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
        registry.rollback_control_drain(
            rollback,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
    )
    replay = await registry.rollback_control_drain(
        rollback,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    known_id_replay = await registry.record_hard_signal_outcome(
        abandoned,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert first.result == "rolled_back"
    assert first.admission_epoch == pending.admission_epoch + 1
    assert concurrent_replay == first
    assert replay == first
    assert known_id_replay == first
    assert registry.admission_epoch == first.admission_epoch

    with pytest.raises(LifecycleAuthorityError) as foreign_authorization:
        await registry.record_hard_signal_outcome(
            abandoned.model_copy(update={"authorization_id": "f" * 43}),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert foreign_authorization.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as foreign_owner:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=_secret_buffer("foreign-owner-credential-material"),
        )
    assert foreign_owner.value.code == "owner_identity_mismatch"


@pytest.mark.asyncio
async def test_lost_prepare_response_recovers_only_at_expiry_via_exact_rollback() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    prepare = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    await registry.prepare_hard_signal(
        prepare,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    rollback = DrainControlRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
    )
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))

    with pytest.raises(LifecycleAuthorityError) as still_live:
        await registry.rollback_control_drain(
            rollback,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert still_live.value.code == "drain_recovery_failed"
    assert registry.admission_epoch == pending.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == (
        "hard_signal_decision_pending"
    )

    clock.advance(1)
    with pytest.raises(LifecycleAuthorityError) as expired:
        await registry.prepare_hard_signal(
            prepare,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert expired.value.code == "hard_signal_authorization_expired"
    payload = rollback.model_dump(mode="json")
    headers = {"X-Breadboard-Owner-Credential": OWNER_SECRET}
    first_controller = TestClient(create_app(SessionService(registry=registry)))
    first = first_controller.post(
        "/v1/engine/control/drain-rollback",
        json=payload,
        headers=headers,
    )
    first_controller.close()
    restarted_controller = TestClient(create_app(SessionService(registry=registry)))
    replay = restarted_controller.post(
        "/v1/engine/control/drain-rollback",
        json=payload,
        headers=headers,
    )
    restarted_controller.close()

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["result"] == "rolled_back"
    assert first.json()["admission_epoch"] == pending.admission_epoch + 1
    assert replay.json() == first.json()
    assert registry.admission_epoch == first.json()["admission_epoch"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ambiguous_now",
    [float("nan"), float("inf"), float("-inf"), -1.0],
    ids=["nan", "positive-infinity", "negative-infinity", "negative"],
)
async def test_ambiguous_time_cannot_prepare_abandon_or_rollback(
    ambiguous_now: float,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    prepare = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    authorization = await registry.prepare_hard_signal(
        prepare,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    abandoned = HardSignalOutcomeRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        authorization_id=authorization.authorization_id,
        outcome="abandoned",
    )
    rollback = DrainControlRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
    )
    clock.value = ambiguous_now

    with pytest.raises(LifecycleAuthorityError) as prepare_rejected:
        await registry.prepare_hard_signal(
            prepare,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert prepare_rejected.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as abandon_rejected:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert abandon_rejected.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as rollback_rejected:
        await registry.rollback_control_drain(
            rollback,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert rollback_rejected.value.code == "drain_recovery_failed"
    assert registry.admission_epoch == pending.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == (
        "hard_signal_decision_pending"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "linearized_now"),
    [
        ("prepare", 1_029.0),
        ("known-id-abandon", 1_030.0),
        ("rollback", 1_030.0),
    ],
)
async def test_hard_signal_authority_uses_one_boundary_clock_sample(
    operation: str,
    linearized_now: float,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    prepare = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    authorization = await registry.prepare_hard_signal(
        prepare,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    sampled: list[float] = []
    boundary_values = iter([linearized_now, 1_060.0])

    def boundary_clock() -> float:
        value = next(boundary_values)
        sampled.append(value)
        return value

    registry._clock = boundary_clock
    if operation == "prepare":
        result = await registry.prepare_hard_signal(
            prepare,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
        assert result == authorization
        assert registry.admission_epoch == pending.admission_epoch
    elif operation == "known-id-abandon":
        result = await registry.record_hard_signal_outcome(
            HardSignalOutcomeRequest(
                **drain_control(
                    process_identity,
                    committed.drain_generation,
                ).model_dump(),
                authorization_id=authorization.authorization_id,
                outcome="abandoned",
            ),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
        assert result.result == "rolled_back"
        assert result.admission_epoch == pending.admission_epoch + 1
    else:
        result = await registry.rollback_control_drain(
            DrainControlRequest(
                **drain_control(
                    process_identity,
                    committed.drain_generation,
                ).model_dump(),
            ),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
        assert result.result == "rolled_back"
        assert result.admission_epoch == pending.admission_epoch + 1

    assert sampled == [linearized_now]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "later_phase"),
    [
        ("accepted", "shutdown_started"),
        ("definitive_rejection", "rollback_permitted"),
        ("definitive_rejection", "rolled_back"),
        ("timeout", "hard_signal_decision_pending"),
        ("timeout", "signal_sent"),
        ("timeout", "process_exited"),
        ("timeout", "rolled_back"),
        ("uncertain", "hard_signal_decision_pending"),
        ("uncertain", "signal_sent"),
        ("uncertain", "process_exited"),
        ("uncertain", "rolled_back"),
    ],
)
async def test_graceful_control_receipt_replays_after_every_later_phase(
    outcome: str,
    later_phase: str,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    request = GracefulControlResultRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        outcome=outcome,
    )
    first = await _record_graceful_control(registry, request)

    if outcome in {"timeout", "uncertain"} and later_phase != (
        "hard_signal_decision_pending"
    ):
        prepare = HardSignalPrepareRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            pid=process_identity.pid,
            os_process_start_token=process_identity.os_process_start_token,
        )
        authorization = await registry.prepare_hard_signal(
            prepare,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
        if later_phase == "rolled_back":
            clock.advance(29)
            await _renew_owner(registry, owner_lease(process_identity))
            clock.advance(1)
            hard_outcome = "abandoned"
        else:
            await registry.commit_hard_signal(
                HardSignalCommitRequest(
                    **prepare.model_dump(),
                    authorization_id=authorization.authorization_id,
                ),
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
            hard_outcome = (
                "process_exited"
                if later_phase == "process_exited"
                else "sent"
            )
        await registry.record_hard_signal_outcome(
            HardSignalOutcomeRequest(
                **drain_control(
                    process_identity,
                    committed.drain_generation,
                ).model_dump(),
                authorization_id=authorization.authorization_id,
                outcome=hard_outcome,
            ),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    elif outcome == "definitive_rejection" and later_phase == "rolled_back":
        clock.advance(29)
        await _renew_owner(registry, owner_lease(process_identity))
        clock.advance(1)
        await registry.rollback_control_drain(
            DrainControlRequest(
                **drain_control(
                    process_identity,
                    committed.drain_generation,
                ).model_dump(),
            ),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )

    assert registry.authority_snapshot()["drain_phase"] == later_phase
    epoch_before_replay = registry.admission_epoch
    conflicting_outcome = "timeout" if outcome == "accepted" else "accepted"
    with pytest.raises(LifecycleAuthorityError) as conflicting:
        await registry.record_graceful_control(
            request.model_copy(update={"outcome": conflicting_outcome}),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert conflicting.value.code == "drain_conflict"
    assert registry.admission_epoch == epoch_before_replay
    assert registry.authority_snapshot()["drain_phase"] == later_phase
    recreated_controller = SessionService(registry=registry)
    registry_replay, controller_replay = await asyncio.gather(
        registry.record_graceful_control(
            request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
        recreated_controller.record_graceful_control(
            request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
    )

    assert registry_replay == first
    assert controller_replay == first
    assert registry.admission_epoch == epoch_before_replay
    assert registry.authority_snapshot()["drain_phase"] == later_phase


@pytest.mark.asyncio
async def test_hard_signal_attempt_requires_committed_permit_before_outcome() -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    committed = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                committed.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    prepare_request = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    preparation = await registry.prepare_hard_signal(
        prepare_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert preparation.result == "prepared"
    assert preparation.signal_permitted is False
    outcome = HardSignalOutcomeRequest(
        **drain_control(
            process_identity,
            committed.drain_generation,
        ).model_dump(),
        authorization_id=preparation.authorization_id,
        outcome="sent",
    )
    with pytest.raises(LifecycleAuthorityError) as uncommitted:
        await registry.record_hard_signal_outcome(
            outcome,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert uncommitted.value.code == "hard_signal_authorization_conflict"

    permit = await registry.commit_hard_signal(
        HardSignalCommitRequest(
            **prepare_request.model_dump(),
            authorization_id=preparation.authorization_id,
        ),
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert permit.result == "signal_permitted"
    assert permit.signal_permitted is True
    recorded = await registry.record_hard_signal_outcome(
        outcome,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert recorded.result == "signal_sent"


async def _prepared_hard_signal_fixture() -> tuple[
    SessionRegistry,
    EngineProcessIdentity,
    Clock,
    Any,
    DrainControlResponse,
    HardSignalPrepareRequest,
    HardSignalPreparationResponse,
    HardSignalCommitRequest,
]:
    registry, process_identity, clock, registration = await owned_registered_registry()
    draining = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    pending = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                draining.drain_generation,
            ).model_dump(),
            outcome="timeout",
        ),
    )
    prepare_request = HardSignalPrepareRequest(
        **drain_control(
            process_identity,
            draining.drain_generation,
        ).model_dump(),
        pid=process_identity.pid,
        os_process_start_token=process_identity.os_process_start_token,
    )
    preparation = await registry.prepare_hard_signal(
        prepare_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    commit_request = HardSignalCommitRequest(
        **prepare_request.model_dump(),
        authorization_id=preparation.authorization_id,
    )
    return (
        registry,
        process_identity,
        clock,
        registration,
        pending,
        prepare_request,
        preparation,
        commit_request,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("later_outcome", "expected_phase"),
    [
        (None, "signal_attempt_committed"),
        ("sent", "signal_sent"),
        ("process_exited", "process_exited"),
    ],
)
async def test_hard_signal_commit_response_replays_exactly_after_loss_concurrency_and_later_phase(
    later_outcome: str | None,
    expected_phase: str,
) -> None:
    (
        registry,
        process_identity,
        _,
        _,
        pending,
        _,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    epoch_before_commit = registry.admission_epoch
    first, concurrent_replay = await asyncio.gather(
        registry.commit_hard_signal(
            commit_request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
        SessionService(registry=registry).commit_hard_signal(
            commit_request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        ),
    )
    assert first.result == "signal_permitted"
    assert concurrent_replay == first
    assert first.authorization_id == preparation.authorization_id
    assert first.expires_at_unix == preparation.expires_at_unix
    assert registry.admission_epoch == epoch_before_commit == pending.admission_epoch
    assert registry._drain is not None
    assert registry._drain.hard_signal_attempt_committed is True
    assert registry._drain.hard_signal_authorization_id == preparation.authorization_id

    if later_outcome is not None:
        recorded = await registry.record_hard_signal_outcome(
            HardSignalOutcomeRequest(
                **drain_control(
                    process_identity,
                    pending.drain_generation,
                ).model_dump(),
                authorization_id=preparation.authorization_id,
                outcome=later_outcome,
            ),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
        assert recorded.result == (
            "signal_sent" if later_outcome == "sent" else "process_exited"
        )

    phase_before_replay = registry.authority_snapshot()["drain_phase"]
    lost_response_retry = await registry.commit_hard_signal(
        commit_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert lost_response_retry == first
    assert registry.authority_snapshot()["drain_phase"] == phase_before_replay
    assert phase_before_replay == expected_phase
    assert registry.admission_epoch == epoch_before_commit
    assert registry._drain is not None
    assert registry._drain.hard_signal_authorization_id == preparation.authorization_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seconds_after_prepare", "commit_permitted"),
    [(29.999, True), (30.0, False), (30.001, False)],
)
async def test_hard_signal_commit_expiry_boundary_is_fail_closed_and_zero_mutation(
    seconds_after_prepare: float,
    commit_permitted: bool,
) -> None:
    (
        registry,
        _,
        clock,
        _,
        pending,
        _,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    clock.advance(29)
    await _renew_owner(registry, owner_lease(registry._identity_or_error()))
    clock.advance(seconds_after_prepare - 29)
    epoch_before = registry.admission_epoch
    phase_before = registry.authority_snapshot()["drain_phase"]
    assert registry._drain is not None
    authorization_before = (
        registry._drain.hard_signal_authorization_id,
        registry._drain.hard_signal_authorization_expires_at_unix,
        registry._drain.hard_signal_attempt_committed,
        registry._drain.recovery_forbidden,
    )

    if commit_permitted:
        permit = await registry.commit_hard_signal(
            commit_request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
        assert permit.authorization_id == preparation.authorization_id
        assert permit.expires_at_unix == preparation.expires_at_unix
        assert registry.authority_snapshot()["drain_phase"] == (
            "signal_attempt_committed"
        )
    else:
        with pytest.raises(LifecycleAuthorityError) as expired:
            await registry.commit_hard_signal(
                commit_request,
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
        assert expired.value.code == "hard_signal_authorization_expired"
        assert registry.authority_snapshot()["drain_phase"] == phase_before
        assert registry._drain is not None
        assert (
            registry._drain.hard_signal_authorization_id,
            registry._drain.hard_signal_authorization_expires_at_unix,
            registry._drain.hard_signal_attempt_committed,
            registry._drain.recovery_forbidden,
        ) == authorization_before
    assert registry.admission_epoch == epoch_before == pending.admission_epoch
    assert registry._control_request_ids == {pending.control_request_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_first", [True, False])
@pytest.mark.parametrize(
    ("seconds_after_prepare", "expected_winner"),
    [(29.999, "commit"), (30.0, "rollback")],
)
async def test_hard_signal_commit_and_expiry_rollback_linearize_under_one_lock(
    commit_first: bool,
    seconds_after_prepare: float,
    expected_winner: str,
) -> None:
    (
        registry,
        process_identity,
        clock,
        _,
        pending,
        _,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(seconds_after_prepare - 29)
    rollback_request = DrainControlRequest(
        **drain_control(
            process_identity,
            pending.drain_generation,
        ).model_dump(),
    )
    commit_call = lambda: registry.commit_hard_signal(
        commit_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    rollback_call = lambda: registry.rollback_control_drain(
        rollback_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    calls = (
        [commit_call(), rollback_call()]
        if commit_first
        else [rollback_call(), commit_call()]
    )
    results = await asyncio.gather(*calls, return_exceptions=True)
    permits = [
        result
        for result in results
        if isinstance(result, HardSignalPermitResponse)
    ]
    rollbacks = [
        result
        for result in results
        if isinstance(result, DrainControlResponse)
        and result.result == "rolled_back"
    ]
    failures = [
        result
        for result in results
        if isinstance(result, LifecycleAuthorityError)
    ]
    assert len(failures) == 1
    assert registry._drain is not None
    assert registry._drain.hard_signal_authorization_id == preparation.authorization_id
    if expected_winner == "commit":
        assert len(permits) == 1
        assert rollbacks == []
        assert registry.authority_snapshot()["drain_phase"] == (
            "signal_attempt_committed"
        )
        assert registry.admission_epoch == pending.admission_epoch
        assert registry.authority_snapshot()["session_admission_open"] is False
    else:
        assert permits == []
        assert len(rollbacks) == 1
        assert registry.authority_snapshot()["drain_phase"] == "rolled_back"
        assert registry.admission_epoch == pending.admission_epoch + 1
        assert registry.authority_snapshot()["session_admission_open"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["sent", "process_exited"])
async def test_committed_hard_signal_outcome_remains_reportable_after_authorization_expiry(
    outcome: str,
) -> None:
    (
        registry,
        process_identity,
        clock,
        _,
        pending,
        _,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    permit = await registry.commit_hard_signal(
        commit_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    clock.advance(29)
    await _renew_owner(registry, owner_lease(process_identity))
    clock.advance(2)
    request = HardSignalOutcomeRequest(
        **drain_control(
            process_identity,
            pending.drain_generation,
        ).model_dump(),
        authorization_id=preparation.authorization_id,
        outcome=outcome,
    )
    first = await registry.record_hard_signal_outcome(
        request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    replay = await registry.record_hard_signal_outcome(
        request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    commit_replay = await registry.commit_hard_signal(
        commit_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert replay == first
    assert commit_replay == permit
    assert first.result == ("signal_sent" if outcome == "sent" else "process_exited")
    assert registry.admission_epoch == pending.admission_epoch
    assert registry.authority_snapshot()["session_admission_open"] is False
    with pytest.raises(LifecycleAuthorityError) as no_reopen:
        await registry.rollback_control_drain(
            DrainControlRequest(
                **drain_control(
                    process_identity,
                    pending.drain_generation,
                ).model_dump(),
            ),
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert no_reopen.value.code == "drain_recovery_failed"
    assert registry.admission_epoch == pending.admission_epoch


@pytest.mark.asyncio
async def test_committed_hard_signal_owner_rotation_transfers_no_authority_and_stays_closed() -> None:
    (
        registry,
        process_identity,
        clock,
        _,
        pending,
        _,
        _,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    await registry.commit_hard_signal(
        commit_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    clock.advance(30)
    reacquired = await _acquire_owner(
        registry,
        owner_acquire(process_identity, expected_generation=1),
    )
    assert reacquired.owner_generation == 2
    epoch_before = registry.admission_epoch
    for request in (
        commit_request,
        commit_request.model_copy(update={"owner_generation": 2}),
    ):
        with pytest.raises(LifecycleAuthorityError):
            await registry.commit_hard_signal(
                request,
                owner_credential=_secret_buffer(OWNER_SECRET),
            )
    assert registry.authority_snapshot()["drain_phase"] == (
        "signal_attempt_committed"
    )
    assert registry.authority_snapshot()["session_admission_open"] is False
    assert registry.admission_epoch == epoch_before == pending.admission_epoch


@pytest.mark.asyncio
async def test_prepare_response_loss_recovers_id_then_commit_replays_across_controllers() -> None:
    (
        registry,
        _,
        _,
        _,
        pending,
        prepare_request,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    payload = prepare_request.model_dump(mode="json")
    headers = {"X-Breadboard-Owner-Credential": OWNER_SECRET}
    first_controller = TestClient(create_app(SessionService(registry=registry)))
    recovered = first_controller.post(
        "/v1/engine/control/hard-signal/prepare",
        json=payload,
        headers=headers,
    )
    first_controller.close()
    assert recovered.status_code == 200
    assert recovered.json()["authorization_id"] == preparation.authorization_id
    assert recovered.json()["signal_permitted"] is False

    commit_payload = commit_request.model_dump(mode="json")
    second_controller = TestClient(create_app(SessionService(registry=registry)))
    first_permit = second_controller.post(
        "/v1/engine/control/hard-signal/commit",
        json=commit_payload,
        headers=headers,
    )
    second_controller.close()
    third_controller = TestClient(create_app(SessionService(registry=registry)))
    replay = third_controller.post(
        "/v1/engine/control/hard-signal/commit",
        json=commit_payload,
        headers=headers,
    )
    third_controller.close()
    assert first_permit.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first_permit.json()
    assert first_permit.json()["authorization_id"] == preparation.authorization_id
    assert first_permit.json()["signal_permitted"] is True
    assert registry.admission_epoch == pending.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == (
        "signal_attempt_committed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_now",
    [float("nan"), float("inf"), float("-inf"), -1.0],
    ids=["nan", "positive-infinity", "negative-infinity", "negative"],
)
async def test_hard_signal_commit_rejects_invalid_clock_without_mutation(
    invalid_now: float,
) -> None:
    (
        registry,
        _,
        clock,
        _,
        pending,
        _,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    phase_before = registry.authority_snapshot()["drain_phase"]
    epoch_before = registry.admission_epoch
    assert registry._drain is not None
    state_before = (
        registry._drain.hard_signal_authorization_id,
        registry._drain.hard_signal_authorization_expires_at_unix,
        registry._drain.hard_signal_attempt_committed,
        registry._drain.recovery_forbidden,
    )
    clock.value = invalid_now
    with pytest.raises(LifecycleAuthorityError) as invalid:
        await registry.commit_hard_signal(
            commit_request,
            owner_credential=_secret_buffer(OWNER_SECRET),
        )
    assert invalid.value.code == "hard_signal_authorization_conflict"
    assert registry.authority_snapshot()["drain_phase"] == phase_before
    assert registry.admission_epoch == epoch_before == pending.admission_epoch
    assert registry._drain is not None
    assert (
        registry._drain.hard_signal_authorization_id,
        registry._drain.hard_signal_authorization_expires_at_unix,
        registry._drain.hard_signal_attempt_committed,
        registry._drain.recovery_forbidden,
    ) == state_before
    assert registry._drain.hard_signal_authorization_id == preparation.authorization_id


@pytest.mark.asyncio
async def test_hard_signal_commit_samples_clock_once_at_linearization() -> None:
    (
        registry,
        _,
        _,
        _,
        pending,
        _,
        preparation,
        commit_request,
    ) = await _prepared_hard_signal_fixture()
    samples = iter(
        [
            preparation.expires_at_unix - 0.001,
            preparation.expires_at_unix + 1,
        ]
    )
    calls = 0

    def boundary_clock() -> float:
        nonlocal calls
        calls += 1
        return next(samples)

    registry._clock = boundary_clock
    permit = await registry.commit_hard_signal(
        commit_request,
        owner_credential=_secret_buffer(OWNER_SECRET),
    )
    assert permit.authorization_id == preparation.authorization_id
    assert calls == 1
    assert registry.admission_epoch == pending.admission_epoch
    assert registry.authority_snapshot()["drain_phase"] == (
        "signal_attempt_committed"
    )
