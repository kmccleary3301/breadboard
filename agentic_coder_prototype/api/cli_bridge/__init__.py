"""
CLI bridge package exposing the FastAPI façade previously hosted in
``tui_backend``. This allows the main engine runtime to serve the
Node CLI without a separate backend process.
"""

from .app import create_app
from .events import EventType, SessionEvent
from .models import (
    ErrorResponse,
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
