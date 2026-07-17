from __future__ import annotations

import asyncio
import hashlib
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

from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge import service as bridge_service_module
from agentic_coder_prototype.api.cli_bridge.engine_identity_config import (
    ENGINE_BOOTSTRAP_FD_ENV,
    EngineIdentityConfigError,
    EngineProcessIdentity,
    LaunchBootstrapVerifier,
)
from agentic_coder_prototype.api.cli_bridge.models import (
    BeginControlDrainRequest,
    ClientLeaseRequest,
    ClientRegisterRequest,
    DrainControlRequest,
    GracefulControlResultRequest,
    HardSignalDecisionRequest,
    OwnerAcquireRequest,
    OwnerLeaseRequest,
    OwnerLeaseResponse,
    ClientRegistrationResponse,
    DrainControlResponse,
    SessionCreateRequest,
    SessionStatus,
)
from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.registry import (
    LifecycleAuthorityError,
    SessionRecord,
    SessionRegistry,
    TurnRecord,
)
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner


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


def registry_fixture(*, state_root: Path | None = None) -> tuple[SessionRegistry, EngineProcessIdentity, Clock, LaunchBootstrapVerifier]:
    process_identity = identity()
    clock = Clock()
    verifier = inherited_verifier(process_identity)
    registry = SessionRegistry(
        state_root,
        process_identity=process_identity,
        bootstrap_verifier=verifier,
        clock=clock,
    )
    return registry, process_identity, clock, verifier


def owner_acquire(
    process_identity: EngineProcessIdentity,
    *,
    bootstrap: str | None = BOOTSTRAP,
    owner_secret: str = OWNER_SECRET,
    expected_generation: int = 0,
) -> OwnerAcquireRequest:
    request = OwnerAcquireRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        expected_owner_generation=expected_generation,
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
) -> BeginControlDrainRequest:
    request = BeginControlDrainRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        owner_generation=owner_generation,
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
    return await registry.acquire_owner(
        request,
        owner_credential=_secret_buffer(owner),
        bootstrap_credential=_secret_buffer(bootstrap) if bootstrap is not None else None,
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


async def _record_hard_signal_decision(
    registry: SessionRegistry,
    request: HardSignalDecisionRequest,
) -> Any:
    owner = _take_proofs(request).get("owner_credential") or OWNER_SECRET
    return await registry.record_hard_signal_decision(
        request,
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


async def owned_registered_registry() -> tuple[SessionRegistry, EngineProcessIdentity, Clock, Any]:
    registry, process_identity, clock, _ = registry_fixture()
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
    assert verifier.consume(_secret_buffer(BOOTSTRAP), process_identity) is True
    assert verifier.consume(_secret_buffer(BOOTSTRAP), process_identity) is False


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
    assert verifier.consume(_secret_buffer(BOOTSTRAP), process_identity) is True


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
    assert verifier.consume(_secret_buffer(BOOTSTRAP), process_identity) is False

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
    bootstrap_buffer = _secret_buffer(BOOTSTRAP)
    request = OwnerAcquireRequest(
        engine_instance_id=process_identity.engine_instance_id,
        engine_boot_id=process_identity.engine_boot_id,
        launch_id=process_identity.launch_id,
        expected_owner_generation=0,
    )
    await registry.acquire_owner(
        request,
        owner_credential=owner_buffer,
        bootstrap_credential=bootstrap_buffer,
    )
    assert owner_buffer == bytearray(len(owner_buffer))
    assert bootstrap_buffer == bytearray(len(bootstrap_buffer))

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
async def test_expired_owner_reacquisition_transfers_only_recoverable_active_drain() -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(31)
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
@pytest.mark.parametrize("graceful_outcome", ["definitive_rejection", "uncertain"])
async def test_reacquired_owner_transfers_drain_and_only_new_generation_can_rollback(
    graceful_outcome: str,
) -> None:
    registry, process_identity, clock, registration = await owned_registered_registry()
    drained = await _begin_control_drain(
        registry,
        begin_drain(registry, process_identity, registration),
    )
    clock.advance(31)
    await _acquire_owner(
        registry,
        owner_acquire(
            process_identity,
            bootstrap=None,
            expected_generation=1,
        ),
    )
    graceful = await _record_graceful_control(
        registry,
        GracefulControlResultRequest(
            **drain_control(
                process_identity,
                drained.drain_generation,
                owner_generation=2,
            ).model_dump(),
            outcome=graceful_outcome,
        ),
    )
    if graceful_outcome == "uncertain":
        assert graceful.result == "hard_signal_decision_pending"
        assert graceful.signal_permitted is True
        rollback_ready = await _record_hard_signal_decision(
            registry,
            HardSignalDecisionRequest(
                **drain_control(
                    process_identity,
                    drained.drain_generation,
                    owner_generation=2,
                ).model_dump(),
                outcome="abandoned",
            ),
        )
    else:
        rollback_ready = graceful
    assert rollback_ready.result == "rollback_permitted"
    assert rollback_ready.session_admission_open is False
    assert rollback_ready.turn_admission_open is False
    assert rollback_ready.registrations_open is False

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
    assert rolled_back.turn_admission_open is True
    assert rolled_back.registrations_open is True


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
    clock.advance(31)
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
        await _record_hard_signal_decision(registry, HardSignalDecisionRequest(
            **drain_control(process_identity, drained.drain_generation).model_dump(),
            outcome="sent",
        ))
    assert no_signal.value.code == "drain_conflict"

    rolled_back = await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert rolled_back.result == "rolled_back"
    assert rolled_back.session_admission_open is True
    assert rolled_back.turn_admission_open is True
    assert rolled_back.registrations_open is True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "uncertain"])
async def test_uncertain_control_remains_closed_until_abandoned_signal_rollback(outcome: str) -> None:
    registry, process_identity, _, registration = await owned_registered_registry()
    drained = await _begin_control_drain(registry, begin_drain(registry, process_identity, registration))
    pending = await _record_graceful_control(registry, GracefulControlResultRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome=outcome,
    ))
    assert pending.result == "hard_signal_decision_pending"
    assert pending.signal_permitted is True
    assert pending.turn_admission_open is False

    with pytest.raises(LifecycleAuthorityError) as premature:
        await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert premature.value.code == "drain_recovery_failed"
    assert registry.authority_snapshot()["turn_admission_open"] is False
    wrong_identity = HardSignalDecisionRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="abandoned",
    ).model_copy(update={"engine_instance_id": secrets.token_urlsafe(32)})
    with pytest.raises(LifecycleAuthorityError) as identity_recheck:
        await _record_hard_signal_decision(registry, wrong_identity)
    assert identity_recheck.value.code == "engine_identity_mismatch"
    assert registry.authority_snapshot()["turn_admission_open"] is False

    abandoned = await _record_hard_signal_decision(registry, HardSignalDecisionRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome="abandoned",
    ))
    assert abandoned.result == "rollback_permitted"
    rolled_back = await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert rolled_back.result == "rolled_back"


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
    decision = await _record_hard_signal_decision(registry, HardSignalDecisionRequest(
        **drain_control(process_identity, drained.drain_generation).model_dump(),
        outcome=outcome,
    ))
    assert decision.result == expected
    with pytest.raises(LifecycleAuthorityError) as failure:
        await _rollback_control_drain(registry, drain_control(process_identity, drained.drain_generation))
    assert failure.value.code == "drain_recovery_failed"
    assert registry.authority_snapshot()["session_admission_open"] is False


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
            result=result,
            drain_generation=1,
            admission_epoch=1,
            session_admission_open=is_open,
            turn_admission_open=is_open,
            registrations_open=is_open,
            signal_permitted=result == "hard_signal_decision_pending",
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
        {
            "result": "hard_signal_decision_pending",
            "session_admission_open": False,
            "turn_admission_open": False,
            "registrations_open": False,
            "signal_permitted": False,
        },
    )
    for contradiction in contradictory_drains:
        with pytest.raises(ValidationError):
            DrainControlResponse(
                **binding,
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
    payload = owner_acquire(process_identity).model_dump(mode="json")
    assert "bootstrap_credential" not in payload
    assert "owner_credential" not in payload
    response = client.post(
        "/v1/engine/owner/acquire",
        json=payload,
        headers={
            "X-Breadboard-Bootstrap-Credential": BOOTSTRAP,
            "X-Breadboard-Owner-Credential": OWNER_SECRET,
        },
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
