"""
CLI bridge package exposing the FastAPI facade used by the Node CLI/TUI.

Important: keep imports lazy so lightweight tools (e.g. TUI runtime bundle
captures) can import small helpers without requiring the full FastAPI/Pydantic
stack to be installed.
"""

from __future__ import annotations

import importlib
from typing import Any

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

_EXPORTS: dict[str, tuple[str, str]] = {
    "create_app": (".app", "create_app"),
    "EventType": (".events", "EventType"),
    "SessionEvent": (".events", "SessionEvent"),
    "ErrorResponse": (".models", "ErrorResponse"),
    "ModelCatalogResponse": (".models", "ModelCatalogResponse"),
    "SessionCommandRequest": (".models", "SessionCommandRequest"),
    "SessionCommandResponse": (".models", "SessionCommandResponse"),
    "SessionCreateRequest": (".models", "SessionCreateRequest"),
    "SessionCreateResponse": (".models", "SessionCreateResponse"),
    "SessionInputRequest": (".models", "SessionInputRequest"),
    "SessionInputResponse": (".models", "SessionInputResponse"),
    "SessionStatus": (".models", "SessionStatus"),
    "SessionSummary": (".models", "SessionSummary"),
    "SessionRecord": (".registry", "SessionRecord"),
    "SessionRegistry": (".registry", "SessionRegistry"),
    "SessionService": (".service", "SessionService"),
    "SessionRunner": (".session_runner", "SessionRunner"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr = target
    module = importlib.import_module(module_name, __name__)
    return getattr(module, attr)


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(__all__))

