from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from breadboard.rl.phase2.service import ArtifactRecord, RunSubmission
from breadboard.rl.phase3.api_models import (
    RLRunArtifactListResponse,
    RLRunAuditResponse,
    RLRunCancelRequest,
    RLRunReplayResponse,
    RLRunStatusResponse,
    RLRunSubmitRequest,
    RLRunSubmitResponse,
    RLStreamEventModel,
)
from breadboard.rl.phase3.service_live import LiveRLRunService


def _tenant(headers_tenant: str | None, query_tenant: str | None) -> str:
    tenant = query_tenant or headers_tenant
    if not tenant:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    return tenant


def _workspace(headers_workspace: str | None, query_workspace: str | None) -> str:
    workspace = query_workspace or headers_workspace
    if not workspace:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    return workspace


def _status_response(status) -> RLRunStatusResponse:  # type: ignore[no-untyped-def]
    return RLRunStatusResponse(**status.to_dict())


def create_phase3_rl_router(rl_service: LiveRLRunService | None = None) -> APIRouter:
    service = rl_service or LiveRLRunService()
    router = APIRouter()

    @router.post("/runs", response_model=RLRunSubmitResponse)
    def submit_run(payload: RLRunSubmitRequest) -> RLRunSubmitResponse:
        try:
            status = service.submit(RunSubmission(**payload.dict()))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return RLRunSubmitResponse(**status.to_dict())

    @router.get("/runs/{run_id}", response_model=RLRunStatusResponse)
    def get_run(
        run_id: str,
        tenant_id: str | None = Query(default=None),
        workspace_id: str | None = Query(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_workspace_id: str | None = Header(default=None),
    ) -> RLRunStatusResponse:
        try:
            return _status_response(service.status(run_id, tenant_id=_tenant(x_tenant_id, tenant_id), workspace_id=_workspace(x_workspace_id, workspace_id)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/events", response_class=PlainTextResponse)
    def get_events(
        run_id: str,
        from_sequence: int = Query(default=0),
        tenant_id: str | None = Query(default=None),
        workspace_id: str | None = Query(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_workspace_id: str | None = Header(default=None),
    ) -> str:
        try:
            events = service.stream_since(run_id, from_sequence=from_sequence, tenant_id=_tenant(x_tenant_id, tenant_id), workspace_id=_workspace(x_workspace_id, workspace_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return "".join(json.dumps(RLStreamEventModel(**event.to_dict()).dict(), separators=(",", ":")) + "\n" for event in events)

    @router.post("/runs/{run_id}/cancel", response_model=RLRunStatusResponse)
    def cancel_run(run_id: str, payload: RLRunCancelRequest) -> RLRunStatusResponse:
        try:
            service.status(run_id, tenant_id=payload.tenant_id, workspace_id=payload.workspace_id)
            return _status_response(service.cancel(run_id, reason=payload.reason))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/artifacts", response_model=RLRunArtifactListResponse)
    def list_artifacts(run_id: str, tenant_id: str, workspace_id: str) -> RLRunArtifactListResponse:
        try:
            artifacts = service.collect(run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return RLRunArtifactListResponse(run_id=run_id, artifacts=[artifact.to_dict() for artifact in artifacts])

    @router.get("/runs/{run_id}/replay/{artifact_id}", response_model=RLRunReplayResponse)
    def replay_artifact(run_id: str, artifact_id: str, tenant_id: str, workspace_id: str) -> RLRunReplayResponse:
        try:
            return RLRunReplayResponse(**service.replay(run_id, artifact_id, tenant_id=tenant_id, workspace_id=workspace_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/audit", response_model=RLRunAuditResponse)
    def audit_run(run_id: str, tenant_id: str, workspace_id: str) -> RLRunAuditResponse:
        try:
            return RLRunAuditResponse(**service.audit(run_id, tenant_id=tenant_id, workspace_id=workspace_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    # Test and scheduler code call the service directly for artifact writes; no public upload route here.
    return router
