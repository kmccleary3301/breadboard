from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "bb.harness.episode.v1"


class HarnessTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    sandbox_image_digest: str | None = None
    repository_snapshot_digest: str | None = None
    verifier_ref: str | None = None

    @field_validator("task_id")
    @classmethod
    def _strip_task_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("task_id must be non-empty")
        return stripped

    @field_validator(
        "sandbox_image_digest", "repository_snapshot_digest", "verifier_ref"
    )
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PolicyRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        return value.strip()


class EpisodeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    episode_id: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    task: HarnessTask

    @field_validator("schema_version")
    @classmethod
    def _schema_is_current(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value!r}")
        return value

    @field_validator("episode_id", "profile")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("episode_id and profile must be non-empty")
        return stripped


class EpisodeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responses_create_params: dict[str, Any]
    policy: PolicyRoute


class AtomicEpisodeRunRequest(EpisodeCreateRequest, EpisodeRunRequest):
    pass


class EpisodeCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    episode_id: str
    state: str
    sandbox_attestation: dict[str, Any]


class EpisodeRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    episode_id: str
    responses_create_params: dict[str, Any]
    response: dict[str, Any]
    reward: float
    reward_components: dict[str, float]
    termination_reason: str
    turns: int
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    sandbox_attestation: dict[str, Any]
    verifier_attestation: dict[str, Any] | None = None


class EpisodeStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    episode_id: str
    state: str
    reason: str = ""


__all__ = [
    "SCHEMA_VERSION",
    "AtomicEpisodeRunRequest",
    "EpisodeCreateRequest",
    "EpisodeCreateResponse",
    "EpisodeRunRequest",
    "EpisodeRunResponse",
    "EpisodeStateResponse",
    "HarnessTask",
    "PolicyRoute",
]
