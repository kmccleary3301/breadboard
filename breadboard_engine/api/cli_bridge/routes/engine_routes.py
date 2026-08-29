"""Engine identity, ownership, and control-drain routes."""

from __future__ import annotations

from fastapi import BackgroundTasks, Depends, FastAPI, Header

from ..events import PROTOCOL_VERSION, replay_configuration_digest
from ..engine_identity_config import (
    ENGINE_IDENTITY_SCHEMA_VERSION, P30_SESSION_CONTRACT_ID,
    P30_SESSION_SCHEMA_SHA256,
)
from ..models import (
    BeginControlDrainRequest, BootstrapChallengeRequest, BootstrapChallengeResponse,
    ClientLeaseRequest, ClientRegisterRequest, ClientRegistrationResponse,
    DrainControlRequest, DrainControlResponse, EngineArtifactRevision,
    EngineIdentityReadinessResponse, EngineLaunchIdentity, EngineLiveness,
    EngineProcessStart, EngineProtocolIdentity, EngineSessionContractIdentity,
    EngineSessionReadiness, ErrorResponse, GracefulControlResultRequest,
    HardSignalCommitRequest, HardSignalPermitResponse, HardSignalPreparationResponse,
    HardSignalOutcomeRequest, HardSignalPrepareRequest, OwnerAcquireRequest,
    OwnerLeaseRequest, OwnerLeaseResponse,
)
from ..service import SessionService


def register_engine_routes(
    app: FastAPI,
    *,
    get_service,
    authority_credential_buffers,
    p30_session_contract_descriptor,
    engine_provenance,
    process_identity,
    request_shutdown,
) -> None:
    _authority_credential_buffers = authority_credential_buffers
    _p30_session_contract_descriptor = p30_session_contract_descriptor
    ENGINE_PROVENANCE = engine_provenance
    get_engine_process_identity = process_identity

    @app.post(
        "/v1/engine/owner/bootstrap-challenge",
        response_model=BootstrapChallengeResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def issue_engine_owner_bootstrap_challenge(
        payload: BootstrapChallengeRequest,
        svc: SessionService = Depends(get_service),
    ) -> BootstrapChallengeResponse:
        return await svc.issue_bootstrap_challenge(payload)

    @app.post(
        "/v1/engine/owner/acquire",
        response_model=OwnerLeaseResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def acquire_engine_owner(
        payload: OwnerAcquireRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> OwnerLeaseResponse:
        (owner_credential,) = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
        )
        assert owner_credential is not None
        return await svc.acquire_owner(
            payload,
            owner_credential=owner_credential,
        )

    @app.post(
        "/v1/engine/owner/renew",
        response_model=OwnerLeaseResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def renew_engine_owner(
        payload: OwnerLeaseRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> OwnerLeaseResponse:
        (owner_credential,) = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
        )
        assert owner_credential is not None
        return await svc.renew_owner(
            payload,
            owner_credential=owner_credential,
        )

    @app.post(
        "/v1/engine/owner/release",
        response_model=OwnerLeaseResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def release_engine_owner(
        payload: OwnerLeaseRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> OwnerLeaseResponse:
        (owner_credential,) = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
        )
        assert owner_credential is not None
        return await svc.release_owner(
            payload,
            owner_credential=owner_credential,
        )

    @app.post(
        "/v1/engine/clients/register",
        response_model=ClientRegistrationResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def register_engine_client(
        payload: ClientRegisterRequest,
        registration_proof: str = Header(
            ...,
            alias="X-Breadboard-Registration-Credential",
        ),
        svc: SessionService = Depends(get_service),
    ) -> ClientRegistrationResponse:
        (registration_credential,) = _authority_credential_buffers(
            (registration_proof, "registration_identity_mismatch"),
        )
        assert registration_credential is not None
        return await svc.register_client(
            payload,
            registration_credential=registration_credential,
        )

    @app.post(
        "/v1/engine/clients/renew",
        response_model=ClientRegistrationResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def renew_engine_client(
        payload: ClientLeaseRequest,
        registration_proof: str = Header(
            ...,
            alias="X-Breadboard-Registration-Credential",
        ),
        svc: SessionService = Depends(get_service),
    ) -> ClientRegistrationResponse:
        (registration_credential,) = _authority_credential_buffers(
            (registration_proof, "registration_identity_mismatch"),
        )
        assert registration_credential is not None
        return await svc.renew_client(
            payload,
            registration_credential=registration_credential,
        )

    @app.post(
        "/v1/engine/clients/detach",
        response_model=ClientRegistrationResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def detach_engine_client(
        payload: ClientLeaseRequest,
        registration_proof: str = Header(
            ...,
            alias="X-Breadboard-Registration-Credential",
        ),
        svc: SessionService = Depends(get_service),
    ) -> ClientRegistrationResponse:
        (registration_credential,) = _authority_credential_buffers(
            (registration_proof, "registration_identity_mismatch"),
        )
        assert registration_credential is not None
        return await svc.detach_client(
            payload,
            registration_credential=registration_credential,
        )

    @app.post(
        "/v1/engine/control/drain",
        response_model=DrainControlResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def begin_engine_control_drain(
        payload: BeginControlDrainRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        registration_proof: str = Header(
            ...,
            alias="X-Breadboard-Registration-Credential",
        ),
        svc: SessionService = Depends(get_service),
    ) -> DrainControlResponse:
        owner_credential, registration_credential = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
            (registration_proof, "registration_identity_mismatch"),
        )
        assert owner_credential is not None
        assert registration_credential is not None
        return await svc.begin_control_drain(
            payload,
            owner_credential=owner_credential,
            registration_credential=registration_credential,
        )

    @app.post(
        "/v1/engine/control/graceful-result",
        response_model=DrainControlResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def record_engine_graceful_control(
        payload: GracefulControlResultRequest,
        background_tasks: BackgroundTasks,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> DrainControlResponse:
        (owner_credential,) = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
        )
        assert owner_credential is not None
        response = await svc.record_graceful_control(
            payload,
            owner_credential=owner_credential,
        )
        if response.result == "shutdown_started" and request_shutdown is not None:
            background_tasks.add_task(request_shutdown)
        return response

    @app.post(
        "/v1/engine/control/hard-signal/prepare",
        response_model=HardSignalPreparationResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def prepare_engine_hard_signal(
        payload: HardSignalPrepareRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> HardSignalPreparationResponse:
        (owner_credential,) = _authority_credential_buffers((owner_proof, "owner_identity_mismatch"))
        assert owner_credential is not None
        return await svc.prepare_hard_signal(payload, owner_credential=owner_credential)

    @app.post(
        "/v1/engine/control/hard-signal/commit",
        response_model=HardSignalPermitResponse,
        responses={
            403: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            410: {"model": ErrorResponse},
        },
    )
    async def commit_engine_hard_signal(
        payload: HardSignalCommitRequest,
        owner_proof: str = Header(
            ...,
            alias="X-Breadboard-Owner-Credential",
        ),
        svc: SessionService = Depends(get_service),
    ) -> HardSignalPermitResponse:
        (owner_credential,) = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
        )
        assert owner_credential is not None
        return await svc.commit_hard_signal(
            payload,
            owner_credential=owner_credential,
        )

    @app.post(
        "/v1/engine/control/hard-signal/outcome",
        response_model=DrainControlResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def record_engine_hard_signal_outcome(
        payload: HardSignalOutcomeRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> DrainControlResponse:
        (owner_credential,) = _authority_credential_buffers((owner_proof, "owner_identity_mismatch"))
        assert owner_credential is not None
        return await svc.record_hard_signal_outcome(payload, owner_credential=owner_credential)

    @app.post(
        "/v1/engine/control/drain-rollback",
        response_model=DrainControlResponse,
        responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
    )
    async def rollback_engine_control_drain(
        payload: DrainControlRequest,
        owner_proof: str = Header(..., alias="X-Breadboard-Owner-Credential"),
        svc: SessionService = Depends(get_service),
    ) -> DrainControlResponse:
        (owner_credential,) = _authority_credential_buffers(
            (owner_proof, "owner_identity_mismatch"),
        )
        assert owner_credential is not None
        return await svc.rollback_control_drain(
            payload,
            owner_credential=owner_credential,
        )

    @app.get(
        "/v1/engine/identity",
        response_model=EngineIdentityReadinessResponse,
    )
    async def engine_identity_readiness(
        svc: SessionService = Depends(get_service),
    ) -> EngineIdentityReadinessResponse:
        session_replay_contract_digest = replay_configuration_digest()
        contract_readiness = svc.p30_session_contract_readiness(
            _p30_session_contract_descriptor(app, svc),
            session_replay_contract_digest=session_replay_contract_digest,
        )
        process_identity = get_engine_process_identity()
        served_backend_commit = ENGINE_PROVENANCE.get("commit")
        if not isinstance(served_backend_commit, str) or len(served_backend_commit) != 40:
            served_backend_commit = None
        served_backend_dirty = ENGINE_PROVENANCE.get("dirty")
        if not isinstance(served_backend_dirty, bool):
            served_backend_dirty = None
        return EngineIdentityReadinessResponse(
            schema_version=ENGINE_IDENTITY_SCHEMA_VERSION,
            liveness=EngineLiveness(),
            process=EngineProcessStart(
                engine_instance_id=process_identity.engine_instance_id,
                engine_boot_id=process_identity.engine_boot_id,
                os_process_start_token=process_identity.os_process_start_token,
                started_at=process_identity.started_at,
                started_at_unix=process_identity.started_at_unix,
                pid=process_identity.pid,
            ),
            launch=EngineLaunchIdentity(
                launch_id=process_identity.launch_id,
                source=process_identity.launch_source,
            ),
            artifact_revision=EngineArtifactRevision(
                engine_artifact_sha256=process_identity.engine_artifact_sha256,
                served_backend_commit=served_backend_commit,
                served_backend_dirty=served_backend_dirty,
            ),
            protocol=EngineProtocolIdentity(protocol_version=PROTOCOL_VERSION),
            session_contract=EngineSessionContractIdentity(
                contract_id=P30_SESSION_CONTRACT_ID,
                schema_sha256=P30_SESSION_SCHEMA_SHA256,
                session_replay_contract_digest=session_replay_contract_digest,
                compatibility=(
                    "compatible"
                    if contract_readiness.ready
                    else "incompatible"
                ),
            ),
            session_readiness=EngineSessionReadiness(
                ready=contract_readiness.ready,
                reason=contract_readiness.reason,
            ),
        )

