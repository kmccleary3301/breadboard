"""Pydantic models used by the CLI bridge FastAPI surface."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    model: Optional[str] = None
    mode: Optional[str] = None
    completion_summary: Dict[str, Any] | None = None
    reward_summary: Dict[str, Any] | None = None
    logging_dir: Optional[str] = None
    metadata: Dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    message: str
    detail: Dict[str, Any] | None = None


class AttachmentHandle(BaseModel):
    """Response payload describing a stored attachment."""

    id: str
    filename: str
    mime: Optional[str] = None
    size_bytes: int


class AttachmentUploadResponse(BaseModel):
    attachments: List[AttachmentHandle]


class SessionInputRequest(BaseModel):
    content: str = Field(..., description="User supplied input text.")
    attachments: Optional[List[str]] = Field(default=None, description="Attachment IDs returned by /attachments.")

    @validator("content")
    def _validate_content(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("content must not be empty")
        return value

    @validator("attachments", each_item=True)
    def _validate_attachment_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("attachment IDs must not be empty")
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


class SessionFileInfo(BaseModel):
    path: str
    type: str = Field(..., description="file or directory")
    size: Optional[int] = None
    updated_at: Optional[str] = None


class SessionFileContent(BaseModel):
    path: str
    content: str
    truncated: bool = Field(default=False)
    total_bytes: Optional[int] = None


class ModelCatalogEntry(BaseModel):
    id: str
    adapter: Optional[str] = None
    provider: Optional[str] = None
    name: Optional[str] = None
    context_length: Optional[int] = None
    params: Dict[str, Any] | None = None
    routing: Dict[str, Any] | None = None
    metadata: Dict[str, Any] | None = None


class ModelCatalogResponse(BaseModel):
    models: List[ModelCatalogEntry]
    default_model: Optional[str] = None
    config_path: Optional[str] = None


class SkillCatalogResponse(BaseModel):
    catalog: Dict[str, Any] = Field(default_factory=dict)
    selection: Dict[str, Any] | None = None
    sources: Dict[str, Any] | None = None


class CTreeSnapshotResponse(BaseModel):
    snapshot: Dict[str, Any] | None = None
    compiler: Dict[str, Any] | None = None
    collapse: Dict[str, Any] | None = None
    runner: Dict[str, Any] | None = None
    last_node: Dict[str, Any] | None = None
