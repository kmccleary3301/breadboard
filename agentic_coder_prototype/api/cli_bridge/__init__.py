"""
CLI bridge package exposing the FastAPI façade used by the Node CLI/TUI.

This lives in the engine so the CLI can talk to a stable HTTP/SSE boundary
without a separate "backend wrapper" package.
"""

from .app import create_app
from .events import EventType, SessionEvent
from .models import (
    ErrorResponse,
    ModelCatalogResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInputRequest,
    SessionInputResponse,
    SessionStatus,
    SessionSummary,
)
from .registry import SessionRecord, SessionRegistry
from .service import SessionService
from .session_runner import SessionRunner

__all__ = [
    "create_app",
    "EventType",
    "SessionEvent",
    "ErrorResponse",
    "ModelCatalogResponse",
    "SessionCommandRequest",
    "SessionCommandResponse",
    "SessionCreateRequest",
    "SessionCreateResponse",
    "SessionInputRequest",
    "SessionInputResponse",
    "SessionStatus",
    "SessionSummary",
    "SessionRecord",
    "SessionRegistry",
    "SessionService",
    "SessionRunner",
]
