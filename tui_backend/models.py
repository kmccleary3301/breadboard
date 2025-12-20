"""Pydantic models used by the CLI backend surface."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, validator


class SessionStatus(str, enum.Enum):
    """Lifecycle marker for a session."""

    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class SessionCreateRequest(BaseModel):
    """Incoming payload for POST /sessions."""

    config_path: str = Field(..., description="Path to agent config YAML/JSON.")
    task: str = Field(..., description="User prompt or path to task file.")
    overrides: Dict[str, Any] | None = Field(default=None, description="Dotted-key override map.")
    metadata: Dict[str, Any] | None = Field(default=None, description="Opaque metadata for UX features.")
    workspace: Optional[str] = Field(default=None, description="Optional explicit workspace root.")
    max_steps: Optional[int] = Field(default=None, description="Override max steps for the loop.")
    permission_mode: Optional[str] = Field(default=None, description="Agent permission preset.")
    stream: bool = Field(default=True, description="Request streaming responses when supported.")

    @validator("config_path")
    def _validate_config(cls, value: str) -> str:
        if not value:
            raise ValueError("config_path must be provided")
        return value


class SessionCreateResponse(BaseModel):
    session_id: str
    status: SessionStatus
    created_at: datetime
    logging_dir: Optional[str] = None


class SessionSummary(BaseModel):
    session_id: str
    status: SessionStatus
    created_at: datetime
    last_activity_at: datetime
    completion_summary: Dict[str, Any] | None = None
    reward_summary: Dict[str, Any] | None = None
    logging_dir: Optional[str] = None
    metadata: Dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    message: str
    detail: Dict[str, Any] | None = None


class SessionInputRequest(BaseModel):
    content: str = Field(..., description="User supplied input text.")

    @validator("content")
    def _validate_content(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("content must not be empty")
        return value


class SessionInputResponse(BaseModel):
    status: str = Field(default="accepted")


class SessionCommandRequest(BaseModel):
    command: str = Field(..., description="Command identifier (e.g. set_model, set_mode).")
    payload: Dict[str, Any] | None = Field(default=None, description="Optional command payload.")

    @validator("command")
    def _validate_command(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("command must not be empty")
        return value


class SessionCommandResponse(BaseModel):
    status: str = Field(default="accepted")
    detail: Dict[str, Any] | None = None
