from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone

import pytest

from agentic_coder_prototype.api.cli_bridge.engine_identity_config import (
    EngineProcessIdentity,
    LaunchBootstrapVerifier,
)
from agentic_coder_prototype.api.cli_bridge.models import (
    BeginControlDrainRequest,
    BootstrapChallengeRequest,
    DrainControlRequest,
    ClientRegisterRequest,
    GracefulControlResultRequest,
    HardSignalOutcomeRequest,
    HardSignalPrepareRequest,
    OwnerAcquireRequest,
)
from agentic_coder_prototype.api.cli_bridge.registry import LifecycleAuthorityError, SessionRegistry

BOOTSTRAP = b"bootstrap-proof-material-000000000000000000"
OWNER = b"owner-proof-material-0000000000000000000000"
REGISTRATION = b"registration-proof-material-00000000000000000"
WORKSPACE = "workspace:v1:sha256:" + "a" * 64
CLIENT = "client-instance-0001"


def _identity() -> EngineProcessIdentity:
    return EngineProcessIdentity(
        pid=12345,
        os_process_start_token="darwin:1000:0",
        engine_instance_id="i" * 43,
        engine_boot_id="b" * 43,
        launch_id="l" * 43,
        launch_source="supervisor",
        started_at=datetime.fromtimestamp(1_000.0, tz=timezone.utc),
        started_at_unix=1_000.0,
        engine_artifact_sha256="sha256:" + "c" * 64,
    )


def _verifier(identity: EngineProcessIdentity) -> LaunchBootstrapVerifier:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, BOOTSTRAP)
    finally:
        os.close(write_fd)
    return LaunchBootstrapVerifier.from_inherited_fd(read_fd, identity)


def _field(value: bytes) -> bytes:
    return len(value).to_bytes(2, "big") + value


def _proof(identity: EngineProcessIdentity, challenge_id: str, challenge: str) -> str:
    binding = b"breadboard-p30-launch-bootstrap-v1\0" + b"".join(
        _field(value.encode("ascii"))
        for value in (identity.launch_id, identity.engine_boot_id, identity.engine_instance_id)
    ) + _field(BOOTSTRAP)
    key = hashlib.sha256(binding).digest()
    message = b"breadboard-p30-launch-bootstrap-proof-v1\0" + b"".join(
        _field(value.encode("ascii"))
        for value in (
            identity.launch_id,
            identity.engine_boot_id,
            identity.engine_instance_id,
            challenge_id,
            challenge,
            OWNER.decode("ascii"),
        )
    )
    return "sha256:" + hmac.new(key, message, hashlib.sha256).hexdigest()


def _binding(identity: EngineProcessIdentity) -> dict[str, str]:
    return {
        "engine_instance_id": identity.engine_instance_id,
        "engine_boot_id": identity.engine_boot_id,
        "launch_id": identity.launch_id,
    }


@pytest.mark.asyncio
async def test_bootstrap_proof_is_challenge_bound_one_use_and_secret_buffers_are_wiped() -> None:
    identity = _identity()
    registry = SessionRegistry(process_identity=identity, bootstrap_verifier=_verifier(identity))
    challenge = await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))
    owner = bytearray(OWNER)
    competing = await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))
    assert competing.challenge_id == challenge.challenge_id
    assert competing.challenge == challenge.challenge
    acquired = await registry.acquire_owner(
        OwnerAcquireRequest(
            **_binding(identity),
            expected_owner_generation=0,
            bootstrap_challenge_id=challenge.challenge_id,
            bootstrap_proof_sha256=_proof(identity, challenge.challenge_id, challenge.challenge),
        ),
        owner_credential=owner,
    )
    assert acquired.result == "acquired"
    assert owner == bytearray(len(owner))
    assert registry._bootstrap_verifier is not None
    assert registry._bootstrap_verifier.verifier_wiped is True

    with pytest.raises(LifecycleAuthorityError, match="owner generation"):
        await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))


def test_bootstrap_challenge_expires_at_the_exact_boundary() -> None:
    identity = _identity()
    verifier = _verifier(identity)
    issued = verifier.issue_challenge(identity, now=1_000.0)
    assert issued is not None
    challenge_id, challenge, expires_at = issued
    assert expires_at == 1_010.0
    assert verifier.consume_proof(
        challenge_id,
        _proof(identity, challenge_id, challenge),
        bytearray(OWNER),
        identity,
        now=expires_at,
    ) is False


@pytest.mark.asyncio
async def test_hard_signal_requires_live_process_authorization_before_recorded_outcome() -> None:
    identity = _identity()
    registry = SessionRegistry(process_identity=identity, bootstrap_verifier=_verifier(identity))
    challenge = await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))
    acquired = await registry.acquire_owner(
        OwnerAcquireRequest(
            **_binding(identity),
            expected_owner_generation=0,
            bootstrap_challenge_id=challenge.challenge_id,
            bootstrap_proof_sha256=_proof(identity, challenge.challenge_id, challenge.challenge),
        ),
        owner_credential=bytearray(OWNER),
    )
    registration = await registry.register_client(
        ClientRegisterRequest(
            engine_instance_id=identity.engine_instance_id,
            client_instance_id=CLIENT,
            workspace_id=WORKSPACE,
            lifecycle_mode="local-owned",
        ),
        registration_credential=bytearray(REGISTRATION),
    )
    drained = await registry.begin_control_drain(
        BeginControlDrainRequest(
            **_binding(identity),
            owner_generation=acquired.owner_generation,
            control_request_id="q" * 43,
            registration_id=registration.registration_id,
            requester_registration_generation=registration.registration_generation,
            requester_client_instance_id=registration.client_instance_id,
            expected_admission_epoch=registration.admission_epoch,
        ),
        owner_credential=bytearray(OWNER),
        registration_credential=bytearray(REGISTRATION),
    )
    pending = await registry.record_graceful_control(
        GracefulControlResultRequest(
            **_binding(identity),
            owner_generation=acquired.owner_generation,
            drain_generation=drained.drain_generation,
            outcome="timeout",
        ),
        owner_credential=bytearray(OWNER),
    )
    assert pending.result == "hard_signal_decision_pending"

    wrong = HardSignalPrepareRequest(
        **_binding(identity),
        owner_generation=acquired.owner_generation,
        drain_generation=drained.drain_generation,
        pid=identity.pid,
        os_process_start_token="darwin:wrong",
    )
    with pytest.raises(LifecycleAuthorityError, match="process proof"):
        await registry.prepare_hard_signal(wrong, owner_credential=bytearray(OWNER))

    authorization = await registry.prepare_hard_signal(
        wrong.model_copy(update={"os_process_start_token": identity.os_process_start_token}),
        owner_credential=bytearray(OWNER),
    )
    assert registry._owner is not None
    registry._owner.released = True
    reacquired = await registry.acquire_owner(
        OwnerAcquireRequest(**_binding(identity), expected_owner_generation=acquired.owner_generation),
        owner_credential=bytearray(OWNER),
    )
    stale_outcome = HardSignalOutcomeRequest(
        **_binding(identity),
        owner_generation=reacquired.owner_generation,
        drain_generation=drained.drain_generation,
        authorization_id=authorization.authorization_id,
        outcome="sent",
    )
    with pytest.raises(LifecycleAuthorityError) as stale:
        await registry.record_hard_signal_outcome(
            stale_outcome,
            owner_credential=bytearray(OWNER),
        )
    assert stale.value.code == "hard_signal_authorization_conflict"

    current_prepare = wrong.model_copy(update={
        "owner_generation": reacquired.owner_generation,
        "os_process_start_token": identity.os_process_start_token,
    })
    with pytest.raises(LifecycleAuthorityError) as replacement:
        await registry.prepare_hard_signal(
            current_prepare,
            owner_credential=bytearray(OWNER),
        )
    assert replacement.value.code == "hard_signal_authorization_conflict"

    with pytest.raises(LifecycleAuthorityError) as exited:
        await registry.record_hard_signal_outcome(
            stale_outcome.model_copy(update={"outcome": "process_exited"}),
            owner_credential=bytearray(OWNER),
        )
    assert exited.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as prior_generation:
        await registry.record_hard_signal_outcome(
            stale_outcome.model_copy(update={
                "owner_generation": acquired.owner_generation,
                "outcome": "abandoned",
            }),
            owner_credential=bytearray(OWNER),
        )
    assert prior_generation.value.code == "owner_generation_conflict"

    abandoned = stale_outcome.model_copy(update={"outcome": "abandoned"})
    with pytest.raises(LifecycleAuthorityError) as preexpiry_abandon:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=bytearray(OWNER),
        )
    assert preexpiry_abandon.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as preexpiry_rollback:
        await registry.rollback_control_drain(
            DrainControlRequest(
                **_binding(identity),
                owner_generation=reacquired.owner_generation,
                drain_generation=drained.drain_generation,
            ),
            owner_credential=bytearray(OWNER),
        )
    assert preexpiry_rollback.value.code == "drain_recovery_failed"
    with pytest.raises(LifecycleAuthorityError) as foreign:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=bytearray(b"foreign-owner-credential-material"),
        )
    assert foreign.value.code == "owner_identity_mismatch"
    assert registry.authority_snapshot()["drain_phase"] == (
        "hard_signal_decision_pending"
    )
    assert registry.admission_epoch == pending.admission_epoch
