from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional, TypedDict, NotRequired


EventType = Literal[
    "turn_start",
    "stream.gap",
    "conversation.compaction.start",
    "conversation.compaction.end",
    "assistant.message.start",
    "assistant.message.delta",
    "assistant.message.end",
    "assistant.reasoning.delta",
    "assistant.thought_summary.delta",
    "assistant_delta",
    "assistant_message",
    "user_message",
    "tool_call",
    "tool.result",
    "tool_result",
    "permission_request",
    "permission_response",
    "checkpoint_list",
    "checkpoint_restored",
    "skills_catalog",
    "skills_selection",
    "ctree_node",
    "ctree_snapshot",
    "task_event",
    "reward_update",
    "completion",
    "log_link",
    "error",
    "run_finished",
]


class SessionEvent(TypedDict):
    id: str
    type: EventType
    session_id: str
    turn: Optional[int]
    timestamp: int
    payload: Dict[str, Any]
    timestamp_ms: NotRequired[int]
    seq: NotRequired[int]
    run_id: NotRequired[Optional[str]]
    thread_id: NotRequired[Optional[str]]
    turn_id: NotRequired[Optional[str | int]]


class ArtifactRefPreview(TypedDict, total=False):
    lines: List[str]
    omitted_lines: Optional[int]
    note: Optional[str]


class ArtifactRefV1(TypedDict):
    schema_version: Literal["artifact_ref_v1"]
    id: str
    kind: Literal["tool_output", "tool_diff", "tool_result"]
    mime: str
    size_bytes: int
    sha256: str
    storage: Literal["workspace_file"]
    path: str
    preview: NotRequired[Optional[ArtifactRefPreview]]


class HealthResponse(TypedDict, total=False):
    status: str
    protocol_version: Optional[str]
    version: Optional[str]
    engine_version: Optional[str]


class SessionCreateRequest(TypedDict, total=False):
    config_path: str
    task: str
    overrides: Dict[str, Any]
    metadata: Dict[str, Any]
    workspace: str
    max_steps: int
    permission_mode: str
    stream: bool


class SessionCreateResponse(TypedDict, total=False):
    session_id: str
    status: str
    created_at: str
    logging_dir: Optional[str]


class SessionSummary(TypedDict, total=False):
    session_id: str
    status: str
    created_at: str
    last_activity_at: str
    model: Optional[str]
    mode: Optional[str]
    completion_summary: Optional[Dict[str, Any]]
    reward_summary: Optional[Dict[str, Any]]
    logging_dir: Optional[str]
    metadata: Optional[Dict[str, Any]]


class ModelCatalogEntry(TypedDict, total=False):
    id: str
    adapter: Optional[str]
    provider: Optional[str]
    name: Optional[str]
    context_length: Optional[int]
    params: Optional[Dict[str, Any]]
    routing: Optional[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]


class ModelCatalogResponse(TypedDict, total=False):
    models: List[ModelCatalogEntry]
    default_model: Optional[str]
    config_path: Optional[str]


class SessionFileInfo(TypedDict, total=False):
    path: str
    type: Literal["file", "directory"]
    size: Optional[int]
    updated_at: Optional[str]


class SessionFileContent(TypedDict, total=False):
    path: str
    content: str
    truncated: bool
    total_bytes: Optional[int]


class AttachmentUploadResponse(TypedDict, total=False):
    attachments: List[Dict[str, Any]]


class SkillCatalogResponse(TypedDict, total=False):
    catalog: Dict[str, Any]
    selection: Optional[Dict[str, Any]]
    sources: Optional[Dict[str, Any]]


class CTreeSnapshotResponse(TypedDict, total=False):
    snapshot: Optional[Dict[str, Any]]
    compiler: Optional[Dict[str, Any]]
    collapse: Optional[Dict[str, Any]]
    runner: Optional[Dict[str, Any]]
    last_node: Optional[Dict[str, Any]]


class ErrorResponse(TypedDict, total=False):
    message: str
    detail: Dict[str, Any]


AttachmentFileTuple = tuple[str, bytes, str | None]
AttachmentFileIterable = Iterable[AttachmentFileTuple]
