from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Literal, Mapping

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from breadboard.rl.harness.contracts import (
    Digest,
    ResolveEpisodeRequest,
    RuntimeClass,
    SelectionCommitToken,
)
from breadboard.rl.harness.evidence import (
    EvidenceCorruptError,
    EvidenceError,
    ExportAuthorizationClaimsV2,
    ExportDeniedError,
)
from breadboard.rl.harness.history import (
    HistoricalEpisodeCorrupt,
    HistoricalEpisodeNotFound,
    HistoricalV1EpisodeReader,
)
from breadboard.rl.harness.runners.conductor import ConductorRunRequest
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2EpisodeConflict,
    V2EpisodeError,
    V2EpisodeNotFound,
    V2EpisodeQuarantined,
    V2EpisodeRejected,
    V2EpisodeUnavailable,
    V2OperationDisposition,
)




V2_SCHEMA_VERSION = "bb.rl.episode.v2"


class _V2WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ArtifactRefV2(_V2WireModel):
    artifact_id: str = Field(min_length=1, max_length=1024)
    sha256: Digest
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=256)
    metadata: dict[str, Any]


class EpisodeCreateV2Request(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"]
    resolution: ResolveEpisodeRequest


class EpisodeRunV2Request(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"]
    create_fingerprint: Digest
    task_input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _closed_json(self) -> EpisodeRunV2Request:
        canonical_json_bytes(self.task_input)
        canonical_json_bytes(self.context)
        return self


class EpisodeCancelV2Request(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"]
    reason: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _normalized_reason(self) -> EpisodeCancelV2Request:
        if self.reason != " ".join(self.reason.split()):
            raise ValueError("reason must be normalized")
        return self


class SandboxPreflightIdentityV2(_V2WireModel):
    runtime: str = Field(min_length=1, max_length=256)
    runtime_class: RuntimeClass
    runtime_binary_digest: Digest
    image_digest: Digest
    security_policy_digest: Digest
    network_policy_digest: Digest
    verifier_digest: Digest
    materialization_plan_digest: Digest


class EpisodeCreateV2Response(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"] = V2_SCHEMA_VERSION
    episode_id: str
    create_fingerprint: Digest
    state: EpisodeLifecycleState
    effective_plan_digest: Digest
    selection_record_ref: dict[str, Any]
    effective_plan_ref: dict[str, Any]
    policy_binding_digest: Digest
    selection_commit: SelectionCommitToken
    base_receipt_digest: Digest
    final_receipt_digest: Digest
    policy_observation_digest: Digest
    sandbox_preflight: SandboxPreflightIdentityV2


class EpisodeRunV2Response(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"] = V2_SCHEMA_VERSION
    episode_id: str
    create_fingerprint: Digest
    run_fingerprint: Digest
    primary_disposition: EpisodePrimaryDisposition
    result_ref: ArtifactRefV2 | None
    evidence_manifest_ref: ArtifactRefV2 | None
    evidence_root: Digest | None
    response: dict[str, Any] | None
    reward: int | float | None
    reward_components: dict[str, Any]
    termination: str | None
    turn_count: int = Field(ge=0)
    completed_envelope_ref: ArtifactRefV2 | None
    closed_envelope_ref: ArtifactRefV2 | None
    artifact_manifest_ref: ArtifactRefV2 | None
    primary_measurement_digest: Digest | None
    verifier_measurement_digest: Digest | None
    verifier_result_digest: Digest | None

    @model_validator(mode="after")
    def _closed_response(self) -> EpisodeRunV2Response:
        canonical_json_bytes(self.response)
        return self


class EpisodeCancelV2Response(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"] = V2_SCHEMA_VERSION
    episode_id: str
    requested: bool
    reason: str
    state: EpisodeLifecycleState


class EpisodeStateV2Response(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"] = V2_SCHEMA_VERSION
    episode_id: str
    state: EpisodeLifecycleState
    transition_sequence: int = Field(ge=0)
    transition_head_digest: Digest
    create_fingerprint: Digest | None
    run_fingerprint: Digest | None
    primary_disposition: EpisodePrimaryDisposition | None
    cleanup_disposition: EpisodeCleanupDisposition
    completed_envelope_ref: ArtifactRefV2 | None
    closed_envelope_ref: ArtifactRefV2 | None


class EpisodeCloseV2Response(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"] = V2_SCHEMA_VERSION
    episode_id: str
    state: EpisodeLifecycleState
    cleanup_disposition: EpisodeCleanupDisposition
    closed_envelope_ref: ArtifactRefV2 | None


class V2ErrorResponse(_V2WireModel):
    schema_version: Literal["bb.rl.episode.v2"] = V2_SCHEMA_VERSION
    category: str
    code: str
    retry_disposition: str
    side_effect_boundary: str
    episode_id: str | None = None
    create_fingerprint: Digest | None = None
    run_fingerprint: Digest | None = None
    primary_error: dict[str, Any] | None = None
    cleanup_error: dict[str, Any] | None = None
    completed_envelope_ref: ArtifactRefV2 | None = None
    closed_envelope_ref: ArtifactRefV2 | None = None


class _V2TransportFailure(Exception):
    def __init__(
        self,
        status_code: int,
        category: str,
        code: str,
        retry_disposition: str,
        side_effect_boundary: str = "none",
    ) -> None:
        self.status_code = status_code
        self.category = category
        self.code = code
        self.retry_disposition = retry_disposition
        self.side_effect_boundary = side_effect_boundary


def _wire_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return _wire_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _wire_value(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if isinstance(value, Mapping):
        return {key: _wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_value(item) for item in value]
    return value


def _canonical_model_response(
    model_type: type[_V2WireModel],
    value: Any,
    *,
    disposition: V2OperationDisposition | None = None,
) -> Response:
    payload = _wire_value(value)
    if type(payload) is not dict:
        raise TypeError("V2 response must be a canonical object")
    payload = {
        key: item
        for key, item in payload.items()
        if key in model_type.model_fields
    }
    model = model_type.model_validate_json(
        canonical_json_bytes({"schema_version": V2_SCHEMA_VERSION, **payload})
    )
    headers = (
        {"X-BreadBoard-Result-Source": disposition.value}
        if disposition is not None
        else None
    )
    return Response(
        content=canonical_json_bytes(model.model_dump(mode="json")),
        media_type="application/json",
        headers=headers,
    )


def _canonical_record_response(value: Any) -> Response:
    payload = (
        value.to_canonical_obj()
        if hasattr(value, "to_canonical_obj")
        else _wire_value(value)
    )
    return Response(
        content=canonical_json_bytes(payload),
        media_type="application/json",
    )

def create_app(
    v2_service: BreadBoardV2EpisodeService,
    *,
    history: HistoricalV1EpisodeReader | None = None,
    auth_token: str | None = None,
    allow_unauthenticated_loopback: bool = False,
) -> FastAPI:
    history_reader = history or HistoricalV1EpisodeReader()
    resolved_token = auth_token or ""
    if not resolved_token and not allow_unauthenticated_loopback:
        raise RuntimeError(
            "auth_token is required unless unauthenticated loopback mode is explicitly enabled"
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await v2_service.start()
        try:
            yield
        finally:
            await v2_service.close()

    app = FastAPI(
        title="BreadBoard episode harness", version=V2_SCHEMA_VERSION, lifespan=lifespan
    )
    app.state.episode_service = v2_service

    def authorize_v2(authorization: str | None) -> None:
        if not resolved_token:
            return
        expected = f"Bearer {resolved_token}"
        if not hmac.compare_digest(authorization or "", expected):
            raise _V2TransportFailure(
                401, "authentication", "invalid_bearer_token", "new_credentials"
            )


    def v2_error_response(exc: Exception) -> Response:
        if isinstance(exc, _V2TransportFailure):
            status_code = exc.status_code
            payload = V2ErrorResponse(
                category=exc.category,
                code=exc.code,
                retry_disposition=exc.retry_disposition,
                side_effect_boundary=exc.side_effect_boundary,
            )
        elif isinstance(exc, V2EpisodeError):
            failure = exc.failure
            if isinstance(exc, V2EpisodeNotFound):
                status_code = 404
            elif isinstance(exc, V2EpisodeConflict):
                status_code = 409
            elif isinstance(exc, V2EpisodeQuarantined):
                status_code = 409
            elif isinstance(exc, V2EpisodeRejected):
                status_code = 403 if failure.code == "export_denied" else 422
            elif isinstance(exc, V2EpisodeUnavailable):
                status_code = (
                    409
                    if failure.code.endswith("_envelope_unavailable")
                    else 503
                )
            else:
                status_code = 500
            payload = V2ErrorResponse(
                category=failure.category,
                code=failure.code,
                retry_disposition=failure.retry_disposition,
                side_effect_boundary=failure.side_effect_boundary,
            )
        elif isinstance(exc, (ValueError, TypeError)):
            status_code = 422
            payload = V2ErrorResponse(
                category="validation",
                code="invalid_request",
                retry_disposition="correct_request",
                side_effect_boundary="none",
            )
        elif isinstance(exc, (EvidenceCorruptError, EvidenceError, ExportDeniedError)):
            status_code = 502
            payload = V2ErrorResponse(
                category="evidence",
                code="evidence_unavailable",
                retry_disposition="reconcile",
                side_effect_boundary="none",
            )
        else:
            status_code = 500
            payload = V2ErrorResponse(
                category="internal",
                code="internal_error",
                retry_disposition="operator_review",
                side_effect_boundary="unknown",
            )
        return Response(
            content=canonical_json_bytes(
                payload.model_dump(mode="json", exclude_none=True)
            ),
            status_code=status_code,
            media_type="application/json",
        )

    @app.exception_handler(_V2TransportFailure)
    async def v2_transport_error_handler(
        _: Request, exc: _V2TransportFailure
    ) -> Response:
        return v2_error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> Response:
        if request.url.path.startswith("/v2/"):
            return v2_error_response(
                _V2TransportFailure(
                    422,
                    "validation",
                    "invalid_request",
                    "correct_request",
                )
            )
        return await request_validation_exception_handler(request, exc)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "schema_version": V2_SCHEMA_VERSION}

    @app.get("/v1/episodes/{episode_id}")
    async def historical_episode_status(
        episode_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        authorize_v2(authorization)
        try:
            episode = await history_reader.get(episode_id)
        except HistoricalEpisodeNotFound as exc:
            raise HTTPException(status_code=404, detail="historical episode not found") from exc
        except HistoricalEpisodeCorrupt as exc:
            raise HTTPException(status_code=502, detail="historical episode is corrupt") from exc
        return {
            "schema_version": "bb.harness.episode.v1",
            "episode_id": episode.episode_id,
            "state": episode.state,
            "reason": episode.reason,
        }

    @app.get("/v1/episodes/{episode_id}/artifact")
    async def historical_episode_artifact(
        episode_id: str,
        authorization: str | None = Header(default=None),
    ) -> Response:
        authorize_v2(authorization)
        try:
            ref, payload = await history_reader.artifact(episode_id)
        except HistoricalEpisodeNotFound as exc:
            raise HTTPException(status_code=404, detail="historical episode not found") from exc
        except HistoricalEpisodeCorrupt as exc:
            raise HTTPException(status_code=502, detail="historical episode is corrupt") from exc
        return Response(
            content=payload,
            media_type=ref.media_type,
            headers={
                "ETag": f'"{ref.sha256}"',
                "X-BreadBoard-Artifact-SHA256": ref.sha256,
            },
        )
    if v2_service is not None:

        @app.post("/v2/episodes", response_model=EpisodeCreateV2Response)
        async def create_episode_v2(
            body: EpisodeCreateV2Request,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                operation = await v2_service.create(body.resolution)
                return _canonical_model_response(
                    EpisodeCreateV2Response,
                    operation.response,
                    disposition=operation.disposition,
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.post(
            "/v2/episodes/{episode_id}:run",
            response_model=EpisodeRunV2Response,
        )
        async def run_episode_v2(
            episode_id: str,
            body: EpisodeRunV2Request,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                operation = await v2_service.run(
                    episode_id,
                    create_fingerprint=body.create_fingerprint,
                    task_input=body.task_input,
                    context=body.context,
                )
                return _canonical_model_response(
                    EpisodeRunV2Response,
                    operation.response,
                    disposition=operation.disposition,
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.get(
            "/v2/episodes/{episode_id}",
            response_model=EpisodeStateV2Response,
        )
        async def episode_status_v2(
            episode_id: str,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                return _canonical_model_response(
                    EpisodeStateV2Response,
                    await v2_service.get_state(episode_id),
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.post(
            "/v2/episodes/{episode_id}:cancel",
            response_model=EpisodeCancelV2Response,
        )
        async def cancel_episode_v2(
            episode_id: str,
            body: EpisodeCancelV2Request,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                return _canonical_model_response(
                    EpisodeCancelV2Response,
                    await v2_service.cancel(episode_id, body.reason),
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.delete(
            "/v2/episodes/{episode_id}",
            response_model=EpisodeCloseV2Response,
        )
        async def close_episode_v2(
            episode_id: str,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                operation = await v2_service.close_episode(episode_id)
                return _canonical_model_response(
                    EpisodeCloseV2Response,
                    operation.response,
                    disposition=operation.disposition,
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.get("/v2/episodes/{episode_id}/envelopes/completed")
        async def completed_envelope_v2(
            episode_id: str,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                return _canonical_record_response(
                    await v2_service.get_completed_envelope(episode_id)
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.get("/v2/episodes/{episode_id}/envelopes/closed")
        async def closed_envelope_v2(
            episode_id: str,
            authorization: str | None = Header(default=None),
        ) -> Response:
            try:
                authorize_v2(authorization)
                return _canonical_record_response(
                    await v2_service.get_closed_envelope(episode_id)
                )
            except Exception as exc:
                return v2_error_response(exc)

        @app.get("/v2/episodes/{episode_id}/exports/{role}")
        async def export_episode_v2(
            episode_id: str,
            role: str,
            request: Request,
            export_subject_digest: Digest = Header(
                alias="X-BreadBoard-Export-Subject-Digest"
            ),
            export_scope: str = Header(
                alias="X-BreadBoard-Export-Scope"
            ),
            evidence_policy_ref: str = Header(
                alias="X-BreadBoard-Export-Evidence-Policy-Ref"
            ),
            retention_policy_ref: str = Header(
                alias="X-BreadBoard-Export-Retention-Policy-Ref"
            ),
            redaction_decision_digest: Digest = Header(
                alias="X-BreadBoard-Export-Redaction-Decision-Digest",
            ),
        ) -> Response:
            try:
                authorize_v2(request.headers.get("Authorization"))
                required = (
                    export_subject_digest,
                    export_scope,
                    evidence_policy_ref,
                    retention_policy_ref,
                    redaction_decision_digest,
                )
                if any(value is None or not value.strip() for value in required):
                    raise _V2TransportFailure(
                        422,
                        "authorization",
                        "export_authorization_incomplete",
                        "correct_request",
                    )
                if not role or len(role) > 128 or role != role.strip():
                    raise _V2TransportFailure(
                        422,
                        "authorization",
                        "export_role_invalid",
                        "correct_request",
                    )
                export_claims = ExportAuthorizationClaimsV2(
                    subject_digest=export_subject_digest,
                    scope=export_scope,
                    evidence_policy_ref=evidence_policy_ref,
                    retention_policy_ref=retention_policy_ref,
                    allowed_roles=(role,),
                    redaction_decision_digest=redaction_decision_digest,
                )
                return _canonical_record_response(
                    await v2_service.export_closed(episode_id, export_claims)
                )
            except Exception as exc:
                return v2_error_response(exc)

    return app

__all__ = [
    "EpisodeCancelV2Request",
    "EpisodeCancelV2Response",
    "EpisodeCloseV2Response",
    "EpisodeCreateV2Request",
    "EpisodeCreateV2Response",
    "EpisodeRunV2Request",
    "EpisodeRunV2Response",
    "EpisodeStateV2Response",
    "SandboxPreflightIdentityV2",
    "V2ErrorResponse",
    "V2_SCHEMA_VERSION",
    "create_app",
]
