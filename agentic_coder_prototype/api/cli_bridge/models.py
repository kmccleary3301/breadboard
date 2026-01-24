"""Pydantic models used by the CLI bridge FastAPI surface."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("config_path")
    @classmethod
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

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("content must not be empty")
        return value

    @field_validator("attachments")
    @classmethod
    def _validate_attachment_id(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("attachments must be a list")
        cleaned: List[str] = []
        for item in value:
            if not item or not str(item).strip():
                raise ValueError("attachment IDs must not be empty")
            cleaned.append(str(item).strip())
        return cleaned


class SessionInputResponse(BaseModel):
    status: str = Field(default="accepted")


class SessionCommandRequest(BaseModel):
    command: str = Field(..., description="Command identifier (e.g. set_model, set_mode).")
    payload: Dict[str, Any] | None = Field(default=None, description="Optional command payload.")

    @field_validator("command")
    @classmethod
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
    hash_summary: Dict[str, Any] | None = None
    context_engine: Dict[str, Any] | None = None


class CTreeDiskArtifact(BaseModel):
    path: str
    exists: bool = Field(default=False)
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    sha256_skipped: bool = Field(default=False)


class CTreeDiskArtifactsResponse(BaseModel):
    root: str
    artifacts: Dict[str, CTreeDiskArtifact] = Field(default_factory=dict)


class CTreeEventsResponse(BaseModel):
    source: str
    root: Optional[str] = None
    offset: int = 0
    limit: Optional[int] = None
    has_more: bool = Field(default=False)
    truncated: bool = Field(default=False)
    header: Dict[str, Any] | None = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    artifact: Optional[CTreeDiskArtifact] = None


class CTreeTreeNode(BaseModel):
    id: str
    parent_id: Optional[str] = None
    kind: str
    turn: Optional[int] = None
    label: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class CTreeTreeResponse(BaseModel):
    source: str
    stage: str
    root_id: str
    nodes: List[CTreeTreeNode] = Field(default_factory=list)
    selection: Dict[str, Any] | None = None
    hashes: Dict[str, Any] | None = None
