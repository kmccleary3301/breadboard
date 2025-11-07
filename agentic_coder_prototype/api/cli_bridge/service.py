"""High level orchestration of session lifecycle for the CLI bridge."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator, Optional

from fastapi import HTTPException, status

from .events import SessionEvent
from .models import (
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInputRequest,
    SessionInputResponse,
    SessionStatus,
)
from .registry import SessionRecord, SessionRegistry
from .session_runner import SessionRunner

logger = logging.getLogger(__name__)


class SessionService:
    """Facade that coordinates the registry, runners, and FastAPI endpoints."""

    def __init__(self, registry: SessionRegistry | None = None) -> None:
        self.registry = registry or SessionRegistry()

    async def create_session(self, request: SessionCreateRequest) -> SessionCreateResponse:
        session_id = str(uuid.uuid4())
        record = SessionRecord(session_id=session_id, status=SessionStatus.STARTING, metadata=request.metadata or {})
        await self.registry.create(record)
        runner = SessionRunner(session=record, registry=self.registry, request=request)
        record.runner = runner
        await runner.start()
        logger.info("Session %s created", session_id)
        return SessionCreateResponse(
            session_id=session_id,
            status=record.status,
            created_at=record.created_at,
            logging_dir=record.logging_dir,
        )

    async def ensure_session(self, session_id: str) -> SessionRecord:
        record = await self.registry.get(session_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        return record

    async def event_stream(self, session_id: str) -> AsyncIterator[SessionEvent]:
        record = await self.ensure_session(session_id)
        queue = record.event_queue
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event

    async def list_sessions(self):
        return await self.registry.list()

    async def stop_session(self, session_id: str) -> None:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if runner:
            await runner.stop()
        await self.registry.update_status(session_id, SessionStatus.STOPPED)
        await self.registry.delete(session_id)

    async def send_input(self, session_id: str, payload: SessionInputRequest) -> SessionInputResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        try:
            await runner.enqueue_input(payload.content)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return SessionInputResponse()

    async def execute_command(self, session_id: str, payload: SessionCommandRequest) -> SessionCommandResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        try:
            detail = await runner.handle_command(payload.command, payload.payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return SessionCommandResponse(detail=detail)
