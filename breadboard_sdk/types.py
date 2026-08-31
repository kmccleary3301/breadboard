from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional, TypedDict


class SessionEventVisibility(TypedDict):
    model_visible: bool
    provider_visible: bool
    host_visible: bool
    redaction_state: str


class SessionEvent(TypedDict):
    schema_version: Literal["bb.kernel_event.v2"]
    event_id: str
    seq: int
    timestamp: str
    work_item_id: Optional[str]
    parent_work_item_id: Optional[str]
    attempt_id: Optional[str]
    session_id: str
    span_id: Optional[str]
    visibility: SessionEventVisibility
    kind: str
    payload: Dict[str, Any]
    payload_schema_version: str


class ArtifactRefPreview(TypedDict, total=False):
    lines: List[str]
    omitted_lines: Optional[int]
    note: Optional[str]


class _ArtifactRefV1Optional(TypedDict, total=False):
    preview: Optional[ArtifactRefPreview]


class ArtifactRefV1(_ArtifactRefV1Optional):
    schema_version: Literal["artifact_ref_v1"]
    id: str
    kind: Literal["tool_output", "tool_diff", "tool_result"]
    mime: str
    size_bytes: int
    sha256: str
    storage: Literal["workspace_file"]
    path: str


class HealthResponse(TypedDict, total=False):
    status: str
    protocol_version: Optional[str]
    version: Optional[str]
    engine_version: Optional[str]


class SessionCreateRequest(TypedDict, total=False):
    config_path: Optional[str]
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
    canonical_provider: Optional[str]
    support_tier: Literal["core", "deferred", "evidence", "unsupported"]
    available: bool
    availability_reason: Optional[
        Literal[
            "provider_managed",
            "missing_auth",
            "unsupported_provider",
            "deferred_provider",
        ]
    ]
    discovery: Literal["configured_only"]
    source: Literal["configured"]
    name: Optional[str]
    context_length: Optional[int]
    params: Optional[Dict[str, Any]]
    routing: Optional[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]


class ModelCatalogIssue(TypedDict, total=False):
    code: Literal[
        "invalid_model",
        "duplicate_model",
        "unsupported_provider",
        "deferred_provider",
        "stale_dynamic_catalog",
    ]
    model_id: Optional[str]
    provider_id: Optional[str]
    source: Literal["configured", "dynamic"]
    index: Optional[int]


class ModelCatalogResponse(TypedDict, total=False):
    models: List[ModelCatalogEntry]
    default_model: Optional[str]
    config_path: Optional[str]
    discovery_policy: Literal["configured_only"]
    issues: List[ModelCatalogIssue]


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


class _ProblemDefaults(TypedDict, total=False):
    schema_version: Literal["bb.problem.v1"]
    record_refs: List[str]
    failed_stage: Optional[str]
    hint: Optional[str]
    next_actions: List[str]


class Problem(_ProblemDefaults):
    error_code: str
    message: str


class _StageOutcomeDefaults(TypedDict, total=False):
    report_ref: Optional[str]
    next_action: Optional[str]


class StageOutcome(_StageOutcomeDefaults):
    stage: str
    status: str


class PublicResult(TypedDict):
    schema_version: Literal["bb.cli.result.v1"]
    ok: bool
    status: Literal["ok", "error"]
    command: List[str]
    record_refs: List[str]
    hashes: Dict[str, str]
    stage_outcomes: List[StageOutcome]
    warnings: List[str]
    next_actions: List[str]
    error: Optional[Problem]
    exit_code: int
    data: Dict[str, Any]


class _PublicHarnessCreateRequestDefaults(TypedDict, total=False):
    directory: str


class PublicHarnessCreateRequest(_PublicHarnessCreateRequestDefaults):
    pass


class PublicHarnessUpdateRequest(TypedDict):
    definition: Dict[str, Any]


class _PublicSessionStartRequestDefaults(TypedDict, total=False):
    session_id: Optional[str]


class PublicSessionStartRequest(_PublicSessionStartRequestDefaults):
    lock_id: str
    task: str


class PublicSessionInputRequest(TypedDict):
    content: str


PublicSessionDecision = Literal["allow", "deny", "once", "always", "reject"]


class PublicSessionApprovalRequest(TypedDict):
    request_id: str
    decision: PublicSessionDecision


class _PublicSessionCancelRequestDefaults(TypedDict, total=False):
    reason: str


class PublicSessionCancelRequest(_PublicSessionCancelRequestDefaults):
    pass


AttachmentFileTuple = tuple[str, bytes, str | None]
AttachmentFileIterable = Iterable[AttachmentFileTuple]
