"""FastAPI application exposing the BreadBoard CLI backend surface."""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse

from .events import SessionEvent
from .models import (
    ErrorResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInputRequest,
    SessionInputResponse,
    SessionSummary,
)
from .service import SessionService


def create_app(service: SessionService | None = None) -> FastAPI:
    app = FastAPI(title="BreadBoard CLI Backend", version="0.1.0")
    _service = service or SessionService()

    def get_service() -> SessionService:
        return _service

    async def event_payloads(events: AsyncIterator[SessionEvent]) -> AsyncIterator[bytes]:
        async for event in events:
            payload = json.dumps(event.asdict(), separators=(",", ":"))
            yield f"data: {payload}\n\n".encode("utf-8")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
        # list_sessions returns an iterable; cast to list for serialization
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

    @app.get(
        "/sessions/{session_id}/events",
        responses={404: {"model": ErrorResponse}},
    )
    async def stream_events(session_id: str, svc: SessionService = Depends(get_service)):
        try:
            generator = svc.event_stream(session_id)
        except HTTPException as exc:  # propagate not found
            raise exc

        return StreamingResponse(
            event_payloads(generator),
            media_type="text/event-stream",
        )

    return app


# Default app for tooling that expects module-level `app`
app = create_app()
