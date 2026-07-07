from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RLResourceCapsModel(BaseModel):
    max_tasks: int
    max_gpus: int
    max_budget_usd: float
    max_duration_seconds: int
    max_artifact_bytes: int


class RLRunSubmitRequest(BaseModel):
    run_id: str
    tenant_id: str
    workspace_id: str
    env_package_ref: str
    target_run_id: str
    requested_tasks: int = 1
    requested_gpus: int = 1
    requested_budget_usd: float = 0.0
    requested_duration_seconds: int = 60
    metadata: dict[str, Any] = Field(default_factory=dict)


class RLRunSubmitResponse(BaseModel):
    run_id: str
    state: str
    target_run_id: str
    accepted: bool
    cancellation_state: str
    reason: str = ""


class RLRunStatusResponse(RLRunSubmitResponse):
    pass


class RLRunCancelRequest(BaseModel):
    tenant_id: str
    workspace_id: str
    reason: str = "cancel requested"


class RlArtifactModel(BaseModel):
    artifact_id: str
    relative_path: str
    sha256: str
    bytes: int
    egress_allowed: bool


class RLRunArtifactListResponse(BaseModel):
    run_id: str
    artifacts: list[RlArtifactModel]


class RLRunReplayResponse(BaseModel):
    available: bool
    artifact_id: str
    replay_path: str | None = None
    sha256: str | None = None
    reason: str | None = None


class RLRunAuditResponse(BaseModel):
    run_id: str
    tenant_id: str
    workspace_id: str
    target_run_id: str
    state: str
    persistent_store: str
    scorecard_update_allowed: bool


class RLStreamEventModel(BaseModel):
    sequence: int
    run_id: str
    event_type: str
    state: str
    message: str
    target_run_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
