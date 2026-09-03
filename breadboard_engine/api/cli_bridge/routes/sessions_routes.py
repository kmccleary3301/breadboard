"""Session lifecycle, files, and event-stream routes."""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..models import (
    AttachmentUploadResponse, CTreeSnapshotResponse, ErrorResponse,
    SessionCommandRequest, SessionCommandResponse, SessionCreateRequest,
    SessionCreateResponse, SessionFileContent, SessionFileInfo, SessionInputRequest,
    SessionInputResponse, SessionSummary, SessionTurnCancelRequest,
    SessionTurnCancelResponse, SkillCatalogResponse,
)
from ..service import SessionService
from breadboard.product.harness.default_profile import (
    DefaultProfileInvalidError,
    DefaultProfileUnavailableError,
)


def register_session_routes(
    app: FastAPI,
    *,
    get_service,
    event_payloads,
    route_prefix: str = "/v1/internal/sessions",
) -> None:
    def raw_path(suffix: str = "") -> str:
        return f"{route_prefix}{suffix}"

    @app.post(
        raw_path(),
        include_in_schema=False,
        response_model=SessionCreateResponse,
        responses={
            400: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    @app.post(
        "/sessions",
        response_model=SessionCreateResponse,
        responses={
            400: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def create_session(payload: SessionCreateRequest, svc: SessionService = Depends(get_service)):
        try:
            return await svc.create_session(payload)
        except DefaultProfileUnavailableError as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=ErrorResponse(
                    error=exc.error_code,
                    detail=exc.hint,
                    path=None,
                ).model_dump(),
            )
        except DefaultProfileInvalidError as exc:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=ErrorResponse(
                    error=exc.error_code,
                    detail=exc.hint,
                    path=None,
                ).model_dump(),
            )

    @app.get(
        raw_path(),
        include_in_schema=False,
        response_model=list[SessionSummary],
    )
    @app.get(
        "/sessions",
        response_model=list[SessionSummary],
    )
    async def list_sessions(svc: SessionService = Depends(get_service)):
        summaries = await svc.list_sessions()
        return list(summaries)

    @app.get(
        raw_path("/{session_id}"),
        include_in_schema=False,
        response_model=SessionSummary,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}",
        response_model=SessionSummary,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session(session_id: str, svc: SessionService = Depends(get_service)):
        record = await svc.ensure_session(session_id)
        return record.to_summary()

    @app.get(
        raw_path("/{session_id}/records"),
        include_in_schema=False,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session_records(
        session_id: str,
        schema_version: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        svc: SessionService = Depends(get_service),
    ):
        return await svc.list_session_records(
            session_id,
            schema_version=schema_version,
            offset=offset,
            limit=limit,
        )

    @app.post(
        raw_path("/{session_id}/input"),
        include_in_schema=False,
        response_model=SessionInputResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
        },
    )
    @app.post(
        "/sessions/{session_id}/input",
        response_model=SessionInputResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
        },
    )
    async def post_input(
        session_id: str,
        payload: SessionInputRequest,
        background_tasks: BackgroundTasks,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.send_input(
            session_id,
            payload,
            defer_execution=lambda operation: background_tasks.add_task(operation),
        )

    @app.post(
        raw_path("/{session_id}/turns/{turn_id}/cancel"),
        include_in_schema=False,
        response_model=SessionTurnCancelResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    )
    async def cancel_turn(
        session_id: str, turn_id: str, payload: SessionTurnCancelRequest,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.cancel_turn(session_id, turn_id, payload)

    @app.post(
        raw_path("/{session_id}/command"),
        include_in_schema=False,
        response_model=SessionCommandResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
            501: {"model": ErrorResponse},
        },
    )
    @app.post(
        "/sessions/{session_id}/command",
        response_model=SessionCommandResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
            501: {"model": ErrorResponse},
        },
    )
    async def post_command(session_id: str, payload: SessionCommandRequest, svc: SessionService = Depends(get_service)):
        return await svc.execute_command(session_id, payload)

    @app.post(
        raw_path("/{session_id}/attachments"),
        include_in_schema=False,
        response_model=AttachmentUploadResponse,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    @app.post(
        "/sessions/{session_id}/attachments",
        response_model=AttachmentUploadResponse,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def upload_attachments(
        session_id: str,
        metadata: str | None = Form(default=None),
        files: list[UploadFile] = File(...),
        svc: SessionService = Depends(get_service),
    ):
        metadata_payload = None
        if metadata:
            try:
                metadata_payload = json.loads(metadata)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"metadata must be valid JSON: {exc}",
                ) from exc
        return await svc.upload_attachments(session_id, files, metadata_payload)

    @app.get(
        raw_path("/{session_id}/files"),
        include_in_schema=False,
        response_model=list[SessionFileInfo],
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def list_session_files(
        session_id: str,
        path: str | None = None,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.list_files(session_id, root=path or ".")

    @app.get(
        "/sessions/{session_id}/files",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def session_files(
        session_id: str,
        path: str | None = None,
        mode: str | None = None,
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
        svc: SessionService = Depends(get_service),
    ):
        if mode:
            # Preserve explicit "0" values (e.g. head_lines=0 means "no head"),
            # while still applying sane defaults for snippet mode.
            if mode == "snippet":
                resolved_head_lines = 200 if head_lines is None else head_lines
                resolved_tail_lines = 80 if tail_lines is None else tail_lines
                resolved_max_bytes = 80_000 if max_bytes is None else max_bytes
            else:
                resolved_head_lines = head_lines
                resolved_tail_lines = tail_lines
                resolved_max_bytes = max_bytes
            return await svc.read_file(
                session_id,
                path or ".",
                mode=mode,
                head_lines=resolved_head_lines,
                tail_lines=resolved_tail_lines,
                max_bytes=resolved_max_bytes,
            )
        return await svc.list_files(session_id, root=path or ".")

    @app.get(
        raw_path("/{session_id}/files/content"),
        include_in_schema=False,
        response_model=SessionFileContent,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def read_session_file(
        session_id: str,
        path: str,
        mode: str = "cat",
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
        svc: SessionService = Depends(get_service),
    ):
        if mode == "snippet":
            head_lines = 200 if head_lines is None else head_lines
            tail_lines = 80 if tail_lines is None else tail_lines
            max_bytes = 80_000 if max_bytes is None else max_bytes
        return await svc.read_file(
            session_id,
            path,
            mode=mode,
            head_lines=head_lines,
            tail_lines=tail_lines,
            max_bytes=max_bytes,
        )

    @app.get(
        raw_path("/{session_id}/skills"),
        include_in_schema=False,
        response_model=SkillCatalogResponse,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}/skills",
        response_model=SkillCatalogResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_skills(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.list_skills(session_id)

    @app.get(
        raw_path("/{session_id}/ctrees"),
        include_in_schema=False,
        response_model=CTreeSnapshotResponse,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}/ctrees",
        response_model=CTreeSnapshotResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_ctrees(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.get_ctree_snapshot(session_id)

    @app.delete(
        raw_path("/{session_id}"),
        include_in_schema=False,
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
    )
    @app.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
    )
    async def delete_session(session_id: str, svc: SessionService = Depends(get_service)):
        await svc.delete_session(session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        raw_path("/{session_id}/events"),
        include_in_schema=False,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}/events",
        responses={404: {"model": ErrorResponse}},
    )
    async def stream_events(
        session_id: str,
        request: Request,
        replay: bool = False,
        limit: int | None = None,
        from_id: str | None = None,
        svc: SessionService = Depends(get_service),
    ):
        try:
            if not from_id:
                from_id = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
            if from_id:
                await svc.validate_event_stream(session_id, from_id=from_id, replay=replay)
            prepared = await svc.prepare_event_stream(
                session_id,
                replay=replay,
                limit=limit,
                from_id=from_id,
            )
            generator = svc.prepared_event_stream(prepared)
        except HTTPException as exc:
            raise exc

        return StreamingResponse(
            event_payloads(generator),
            media_type="text/event-stream",
        )

    @app.get(
        raw_path("/{session_id}/download"),
        include_in_schema=False,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    @app.get(
        "/sessions/{session_id}/download",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    async def download_artifact(session_id: str, artifact: str, svc: SessionService = Depends(get_service)):
        path = await svc.resolve_artifact_path(session_id, artifact)
        return FileResponse(path)

