from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, RootModel


class E4Error(BaseModel):
    error: str
    detail: str | dict[str, Any] | None = None
    path: str | None = None


class E4ApiError(HTTPException):
    def __init__(self, *, status_code: int, error: str, detail: str | dict[str, Any] | None = None, path: str | None = None) -> None:
        self.error = error
        self.detail_text = detail
        self.path = path
        super().__init__(status_code=status_code, detail={"error": error, "detail": detail, "path": path})


class E4Health(BaseModel):
    ok: bool
    repo_root: str
    inventory_revision: int
    catalog_revision: int


class E4SchemaInfo(BaseModel):
    schema_id: str
    schema_version: str | None = None
    pack: str
    path: str
    sha256: str


class E4SchemaList(BaseModel):
    schemas: list[E4SchemaInfo]
    total: int


class E4Lane(BaseModel):
    model_config = ConfigDict(extra="allow")

    lane_id: str
    phase: str | None = None
    kind: str | None = None
    status: str | None = None
    points: int | None = None
    target_family: str | None = None
    target_version: str | None = None
    run_id: str | None = None
    builder_script: str | None = None
    artifact_roles: dict[str, str] = Field(default_factory=dict)


class E4LaneList(BaseModel):
    lanes: list[E4Lane]
    total: int


class E4ResolvedArtifact(BaseModel):
    role: str
    role_id: str
    path: str
    sha256: str | None = None
    bytes: int | None = None
    exists: bool


class E4LaneDetail(BaseModel):
    lane: E4Lane
    resolved_artifacts: list[E4ResolvedArtifact]


class E4SupportClaim(BaseModel):
    model_config = ConfigDict(extra="allow")

    claim_id: str
    config_id: str | None = None
    lane_id: str | None = None
    kind: str | None = None
    accepted: bool
    schema_version: str | None = None
    summary: str | None = None
    scope: dict[str, Any] | None = None
    evidence_manifest_ref: str | None = None


class E4ClaimList(BaseModel):
    claims: list[E4SupportClaim]
    total: int


class E4ClaimDetail(BaseModel):
    claim: E4SupportClaim
    evidence_manifest: dict[str, Any]


class E4CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    role_id: str
    lane_id: str | None = None
    path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    exists: bool | None = None
    artifact_kind: str | None = None


class E4CatalogPage(BaseModel):
    revision: int
    total: int
    entries: list[E4CatalogEntry]



class E4CatalogBindingSegment(BaseModel):
    segment_id: str
    stable_entries_hash: str


class E4CatalogBinding(BaseModel):
    catalog_path: str
    schema_version: str
    segments: list[E4CatalogBindingSegment]
    segments_hash: str
    stable_entries_hash: str
    generated_at_utc: str | None = None


class RegistryInfo(BaseModel):
    registry_id: str
    schema_version: str | None = None
    path: str
    entries: int


class RegistryList(BaseModel):
    registries: list[RegistryInfo]
    total: int

class E4LedgerRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    feature_id: str
    lane_id: str | None = None
    points: int | None = None
    status: str | None = None


class E4LedgerRows(BaseModel):
    rows: list[E4LedgerRow]
    total: int


class E4RecordEnvelope(BaseModel):
    schema_version: str
    source: Literal["evidence", "runtime"]
    path: str
    record: dict[str, Any]


class E4RecordList(BaseModel):
    records: list[E4RecordEnvelope]
    total: int


class E4ReverifyRequest(BaseModel):
    rerun_comparators: bool = Field(default=False)


class E4ReverifyResult(BaseModel):
    ok: bool
    errors: list[str]
    claim_id: str
    mode: Literal["check_only"] = "check_only"
    comparator_rerun: bool
    checked_at: str


class E4CoverageMatrix(RootModel[dict[str, Any]]):
    pass
