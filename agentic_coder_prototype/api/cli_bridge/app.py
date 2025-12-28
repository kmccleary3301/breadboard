"""FastAPI application exposing the CLI bridge surface."""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import AsyncIterator, Dict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from .events import SessionEvent
from .models import (
    AttachmentUploadResponse,
    ErrorResponse,
    ModelCatalogResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionFileContent,
    SessionFileInfo,
    SessionInputRequest,
    SessionInputResponse,
    SessionSummary,
)
from .service import SessionService


def _load_chaos_config() -> Dict[str, float] | None:
    latency = max(0, int(os.environ.get("BREADBOARD_CLI_LATENCY_MS", "0")))
    jitter = max(0, int(os.environ.get("BREADBOARD_CLI_JITTER_MS", "0")))
    try:
        drop = float(os.environ.get("BREADBOARD_CLI_DROP_RATE", "0"))
    except ValueError:
        drop = 0.0
    drop = max(0.0, min(1.0, drop))
    if latency == 0 and jitter == 0 and drop == 0:
        return None
    return {
        "latencyMs": latency,
        "jitterMs": jitter,
        "dropRate": drop,
    }


def create_app(service: SessionService | None = None) -> FastAPI:
    app = FastAPI(title="BreadBoard CLI Bridge", version="0.1.0")
    _service = service or SessionService()
    chaos_config = _load_chaos_config()

    def get_service() -> SessionService:
        return _service

    async def event_payloads(events: AsyncIterator[SessionEvent]) -> AsyncIterator[bytes]:
        async for event in events:
            if chaos_config:
                drop_rate = chaos_config.get("dropRate", 0.0)
                if drop_rate and random.random() < drop_rate:
                    continue
                latency_ms = chaos_config.get("latencyMs", 0.0)
                jitter_ms = chaos_config.get("jitterMs", 0.0)
                extra_delay = latency_ms + (random.random() * jitter_ms)
                if extra_delay > 0:
                    await asyncio.sleep(extra_delay / 1000.0)
            payload = json.dumps(event.asdict(), separators=(",", ":"))
            yield f"data: {payload}\n\n".encode("utf-8")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/models",
        response_model=ModelCatalogResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def list_models(
        config_path: str,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.list_models(config_path)

    @app.post(
        "/sessions",
        response_model=SessionCreateResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def create_session(payload: SessionCreateRequest, svc: SessionService = Depends(get_service)):
        return await svc.create_session(payload)

    @app.get(
        "/sessions",
        response_model=list[SessionSummary],
    )
    async def list_sessions(svc: SessionService = Depends(get_service)):
        summaries = await svc.list_sessions()
        return list(summaries)

    @app.get(
        "/sessions/{session_id}",
        response_model=SessionSummary,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session(session_id: str, svc: SessionService = Depends(get_service)):
        record = await svc.ensure_session(session_id)
        return record.to_summary()

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
    async def post_input(session_id: str, payload: SessionInputRequest, svc: SessionService = Depends(get_service)):
        return await svc.send_input(session_id, payload)

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
            return await svc.read_file(
                session_id,
                path or ".",
                mode=mode,
                head_lines=head_lines or 200,
                tail_lines=tail_lines or 80,
                max_bytes=max_bytes or 80_000,
            )
        return await svc.list_files(session_id, root=path or ".")

    @app.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
    )
    async def delete_session(session_id: str, svc: SessionService = Depends(get_service)):
        await svc.stop_session(session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/sessions/{session_id}/events",
        responses={404: {"model": ErrorResponse}},
    )
    async def stream_events(session_id: str, svc: SessionService = Depends(get_service)):
        try:
            generator = svc.event_stream(session_id)
        except HTTPException as exc:
            raise exc

        return StreamingResponse(
            event_payloads(generator),
            media_type="text/event-stream",
        )

    return app


# Default app for uvicorn module-level discovery.
app = create_app()
