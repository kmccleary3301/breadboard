from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Never as _Never, Optional, Tuple, TypedDict

from .generated.session_event_bindings import (
    PublicSessionEventKind,
    PublicSessionEventPayloadSchema,
)


class SessionEventVisibility(TypedDict):
    model_visible: bool
    provider_visible: bool
    host_visible: bool
    redaction_state: Literal["none", "redacted"]


class SessionEventLineage(TypedDict):
    parent_session_id: str
    root_session_id: str
    parent_work_item_id: str
    child_work_item_id: str


class SessionAnnotationPayload(TypedDict):
    annotation_id: str
    message_id: str
    trajectory_id: str
    label: str
    author: str
    generation: str



class ModuleOutputEnvelope(TypedDict):
    schema_id: str
    body: str
    final: bool


class SessionModuleOutputPayload(TypedDict):
    module_output: ModuleOutputEnvelope
    output_sequence: int
    module_id: str
    worker_session_id: str
    request_id: str
    generation_id: str
    instance_id: str
    work_id: str
    attempt_id: str
    authority_epoch: int

class WorldFieldMask(TypedDict):
    schema_version: Literal["bb.world_field_mask.v1"]
    paths: Tuple[Literal["/occurred_at"], Literal["/timestamp"]]




class SessionAdoptionFrontier(TypedDict):
    event_sequence: int
    generation_id: str
    typed_input_sequence: int
    output_sequence: int
    compaction_index: int


class SessionAdoptionMigration(TypedDict):
    binding: str
    disposition: Literal["compatible", "migrate"]
    source_schema_id: str
    target_schema_id: str
    reason: str


class _SessionAdoptionCommittedOptional(TypedDict, total=False):
    request_id: str


class SessionAdoptionCommittedPayload(_SessionAdoptionCommittedOptional):
    adoption_id: str
    checkpoint_id: str
    source_generation_id: str
    source_module_id: str
    source_instance_id: str
    source_work_id: str
    source_attempt_id: str
    source_schema_id: str
    source_body_sha256: str
    source_frontier: SessionAdoptionFrontier
    target_generation_id: str
    effective_lock_hash: str
    reason: str
    migration: List[SessionAdoptionMigration]


class _SessionStartedCommonOptional(TypedDict, total=False):
    lineage: SessionEventLineage


class _SessionStartedTextOptional(_SessionStartedCommonOptional, total=False):
    module_input: _Never
    module_input_sequence: _Never


class SessionStartedTextPayload(_SessionStartedTextOptional):
    effective_lock_hash: str
    task_hash: str


class SessionStartedModulePayload(_SessionStartedCommonOptional):
    effective_lock_hash: str
    task_hash: str
    module_input: ModuleInputRequest
    module_input_sequence: int


SessionStartedPayload = SessionStartedTextPayload | SessionStartedModulePayload


class _SessionInputAcceptedTextOptional(TypedDict, total=False):
    module_input: _Never
    module_input_sequence: _Never


class SessionInputAcceptedTextPayload(_SessionInputAcceptedTextOptional):
    content_hash: str
    attachments: List[Dict[str, Any]]


class _SessionInputAcceptedModuleOptional(TypedDict, total=False):
    content_hash: _Never


class SessionInputAcceptedModulePayload(_SessionInputAcceptedModuleOptional):
    module_input: ModuleInputRequest
    module_input_sequence: int
    attachments: List[Dict[str, Any]]


SessionInputAcceptedPayload = (
    SessionInputAcceptedTextPayload | SessionInputAcceptedModulePayload
)


class _SessionLifecyclePayload(TypedDict, total=False):
    effective_lock_hash: str
    task_hash: str
    content_hash: str
    attachments: List[Dict[str, Any]]
    request_id: str
    operation: str
    decision: Literal["allow", "deny", "once", "always", "reject"]
    reason: str
    outcome: Literal["completed", "failed", "canceled"]
    summary: str
    error: str
    detail: str
    lineage: SessionEventLineage


class _SessionEventEnvelope(TypedDict):
    schema_version: Literal["bb.public_session_event.v1"]
    event_id: str
    seq: int
    timestamp: str
    work_item_id: Optional[str]
    parent_work_item_id: Optional[str]
    attempt_id: Optional[str]
    session_id: str
    span_id: Optional[str]
    visibility: SessionEventVisibility


class _SessionLifecycleEvent(_SessionEventEnvelope):
    kind: Literal[
        "approval.requested",
        "approval.resolved",
        "session.canceled",
        "session.completed",
        "session.failed",
        "session.paused",
        "session.reconfigured",
        "session.resumed",
    ]
    payload: _SessionLifecyclePayload
    payload_schema_version: Literal["bb.payload.product_session.lifecycle.v1"]


class SessionStartedEvent(_SessionEventEnvelope):
    kind: Literal["session.started"]
    payload: SessionStartedPayload
    payload_schema_version: Literal["bb.payload.product_session.lifecycle.v1"]


class SessionInputAcceptedEvent(_SessionEventEnvelope):
    kind: Literal["input.accepted"]
    payload: SessionInputAcceptedPayload
    payload_schema_version: Literal["bb.payload.product_session.lifecycle.v1"]


class SessionAdoptionCommittedEvent(_SessionEventEnvelope):
    kind: Literal["session.adoption_committed"]
    payload: SessionAdoptionCommittedPayload
    payload_schema_version: Literal["bb.payload.product_session.lifecycle.v1"]


class _SessionAnnotationEvent(_SessionEventEnvelope):
    kind: Literal["annotation"]
    payload: SessionAnnotationPayload
    payload_schema_version: Literal["bb.payload.product_session.annotation.v1"]


class SessionModuleOutputEvent(_SessionEventEnvelope):
    kind: Literal["module_output"]
    payload: SessionModuleOutputPayload
    payload_schema_version: Literal["bb.payload.product_session.module_output.v1"]


class _SessionKernelEvent(_SessionEventEnvelope):
    kind: Literal["assistant_message", "tool_call", "tool_result"]
    payload: Dict[str, Any]
    payload_schema_version: Literal[
        "bb.payload.message.assistant.v1",
        "bb.payload.tool.called.v1",
        "bb.payload.tool.completed.v1",
    ]


SessionEvent = (
    _SessionLifecycleEvent
    | SessionStartedEvent
    | SessionInputAcceptedEvent
    | SessionAdoptionCommittedEvent
    | _SessionAnnotationEvent
    | SessionModuleOutputEvent
    | _SessionKernelEvent
)


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


class _SessionSummaryOptional(TypedDict, total=False):
    created_at: str
    last_activity_at: str
    schema_version: str
    effective_lock_hash: str
    task_hash: str
    event_count: int
    pending_approval: Optional[str]
    terminal_outcome: Optional[Dict[str, Any]]
    model: Optional[str]
    mode: Optional[str]
    completion_summary: Optional[Dict[str, Any]]
    reward_summary: Optional[Dict[str, Any]]
    logging_dir: Optional[str]
    metadata: Optional[Dict[str, Any]]


class SessionSummary(_SessionSummaryOptional):
    session_id: str
    status: str
    generation_id: str
    trajectory_segment_id: str
    lineage: Optional[SessionEventLineage]


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


class PublicHarnessPublishRequest(TypedDict):
    lock_id: str
    expected_revision: int
    request_id: str


class ModuleInputRequest(TypedDict):
    schema_id: str
    body: str
    final: bool


class ProjectAuthorityRequest(TypedDict, total=False):
    roots: List[str]
    operations: List[Literal["read", "write"]]


class NetworkAuthorityRequest(TypedDict, total=False):
    destinations: List[str]
    operations: List[Literal["connect", "resolve"]]


class ChildAuthorityRequest(TypedDict, total=False):
    allowed_module_ids: List[str]
    max_depth: int


class CredentialDisclosureRequest(TypedDict):
    secret_name: str
    purpose: str


class AuthorityDeclarationRequest(TypedDict, total=False):
    project: Optional[ProjectAuthorityRequest]
    network: Optional[NetworkAuthorityRequest]
    child: Optional[ChildAuthorityRequest]
    provider_ids: List[str]
    tool_ids: List[str]
    credential_disclosures: List[CredentialDisclosureRequest]


class _SessionStartLockSelectorOptional(TypedDict, total=False):
    publication_target: _Never


class _SessionStartTargetSelectorOptional(TypedDict, total=False):
    lock_id: _Never


class _SessionStartCommonOptional(TypedDict, total=False):
    session_id: Optional[str]


class _SessionStartTextOptional(_SessionStartCommonOptional, total=False):
    module_input: _Never
    module_authority: _Never


class _SessionStartModuleOptional(_SessionStartCommonOptional, total=False):
    task: _Never
    module_authority: Optional[AuthorityDeclarationRequest]


class PublicSessionStartTextLockRequest(
    _SessionStartLockSelectorOptional, _SessionStartTextOptional
):
    lock_id: str
    task: str


class PublicSessionStartTextTargetRequest(
    _SessionStartTargetSelectorOptional, _SessionStartTextOptional
):
    publication_target: str
    task: str


class PublicSessionStartModuleLockRequest(
    _SessionStartLockSelectorOptional, _SessionStartModuleOptional
):
    lock_id: str
    module_input: ModuleInputRequest


class PublicSessionStartModuleTargetRequest(
    _SessionStartTargetSelectorOptional, _SessionStartModuleOptional
):
    publication_target: str
    module_input: ModuleInputRequest


PublicSessionStartRequest = (
    PublicSessionStartTextLockRequest
    | PublicSessionStartTextTargetRequest
    | PublicSessionStartModuleLockRequest
    | PublicSessionStartModuleTargetRequest
)

class _SessionTextInputOptional(TypedDict, total=False):
    module_input: _Never


class PublicSessionTextInputRequest(_SessionTextInputOptional):
    content: str


class _SessionModuleInputOptional(TypedDict, total=False):
    content: _Never


class PublicSessionModuleInputRequest(_SessionModuleInputOptional):
    module_input: ModuleInputRequest


PublicSessionInputRequest = (
    PublicSessionTextInputRequest | PublicSessionModuleInputRequest
)

PublicSessionDecision = Literal["allow", "deny", "once", "always", "reject"]


class PublicSessionApprovalRequest(TypedDict):
    request_id: str
    decision: PublicSessionDecision

class PublicSessionCheckpointRequest(TypedDict):
    reason: str
    request_id: str


class PublicSessionAdoptRequest(TypedDict):
    checkpoint_id: str
    lock_id: str
    request_id: str


class _PublicSessionCancelRequestDefaults(TypedDict, total=False):
    reason: str


class PublicSessionCancelRequest(_PublicSessionCancelRequestDefaults):
    pass


class ResearchCompareRequest(TypedDict):
    definition: str
    world: str
    generation: str
    projection: str
    compare: Tuple[str, str]


AttachmentFileTuple = tuple[str, bytes, str | None]
AttachmentFileIterable = Iterable[AttachmentFileTuple]
