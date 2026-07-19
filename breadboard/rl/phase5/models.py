from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

AUTHORITY_SYNTHESIS_ERROR = (
    "authority cannot be synthesized from score, review, issue, job, or evidence state"
)
AUTHORITY_INELIGIBLE_ERROR = "authority decision is not current, scoped, revocable, and non-revoked"


class EvidenceClass(str, Enum):
    SOURCE_REFERENCE = "source_reference"
    FIXTURE_ORACLE = "fixture_oracle"
    COMPILE_ARTIFACT = "compile_artifact"
    ADMISSION_DECISION = "admission_decision"
    LOCAL_CONTRACT_TEST = "local_contract_test"
    LOCAL_PROCESS_INTEGRATION = "local_process_integration"
    LOCAL_CONTAINER_ATTESTATION = "local_container_attestation"
    TARGET_SLURM_COMMAND = "target_slurm_command"
    TARGET_EPISODE_LIFECYCLE = "target_episode_lifecycle"
    TARGET_TRAINING_RUN = "target_training_run"
    DIGITALOCEAN_PROVIDER_RUN = "digitalocean_provider_run"
    ARTIFACT_INTEGRITY = "artifact_integrity"
    REPLAY_REPRODUCTION = "replay_reproduction"
    NEGATIVE_CONTROL = "negative_control"
    CLEANUP_ROLLBACK = "cleanup_rollback"
    BLOCKER = "blocker"
    REVIEW_VERDICT = "review_verdict"
    AUTHORITY_DECISION = "authority_decision"


class SupportLevel(str, Enum):
    OBSERVED = "observed"
    DERIVED_DETERMINISTICALLY = "derived_deterministically"
    INFERRED = "inferred"
    WORKER_CLAIM = "worker_claim"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


class EvidenceState(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    STALE = "stale"
    SUPERSEDED = "superseded"
    INVALID = "invalid"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class EvidenceNodeKind(str, Enum):
    EVIDENCE = "evidence"
    CLAIM = "claim"
    REVIEW = "review"
    POINT = "point"
    PROMOTION = "promotion"
    STATUS = "status"


class CampaignDisposition(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    INFEASIBLE_WITH_REQUIRED_NONCLAIM = "INFEASIBLE_WITH_REQUIRED_NONCLAIM"
    DISABLED_WITH_REQUIRED_NONCLAIM = "DISABLED_WITH_REQUIRED_NONCLAIM"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"


class ClaimState(str, Enum):
    UNCLAIMED = "unclaimed"
    PENDING = "pending"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ScoreItemState(str, Enum):
    PENDING = "pending"
    AWARDED = "awarded"
    BLOCKED = "blocked"
    FAILED = "failed"
    DEFERRED = "deferred"
    STALE = "stale"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class AuthorityKind(str, Enum):
    APPROVAL = "approval"
    AUTHORITY_DECISION = "authority_decision"
    DEFERRAL = "deferral"
    RISK_ACCEPTANCE = "risk_acceptance"
    REVOCATION = "revocation"
    ROLLBACK = "rollback"
    CANONICAL_PROMOTION = "canonical_promotion"
    INCIDENT_REOPENING = "incident_reopening"


class BlockerKind(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class BlockerFailureClass(str, Enum):
    DETERMINISTIC_FAILURE = "deterministic_failure"
    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"
    FLAKY_OR_UNKNOWN = "flaky_or_unknown"
    MISSING_IBM_INPUT = "missing_ibm_input"
    MISSING_TOKEN = "missing_token"
    MISSING_IMAGE = "missing_image"
    MISSING_CHECKPOINT = "missing_checkpoint"
    MISSING_DATA_LICENSE = "missing_data_license"
    MISSING_AUTHORITY = "missing_authority"
    GVISOR_UNAVAILABLE = "gvisor_unavailable"
    TRAINING_PREEMPTION = "training_preemption"
    REVIEW_FINDING = "review_finding"


class BlockerState(str, Enum):
    OPEN = "open"
    WOKEN = "woken"
    RESOLVED = "resolved"
    REJECTED = "rejected"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


class AuthorityRecord(BaseModel):
    """An explicit authority artifact; this record is never derived from campaign state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: NonEmpty
    kind: AuthorityKind
    actor_identity: NonEmpty
    actor_role: NonEmpty
    scope: tuple[NonEmpty, ...] = Field(min_length=1)
    artifact_hashes: tuple[Sha256, ...] = Field(min_length=1)
    authority_artifact_uri: NonEmpty
    issued_at: datetime
    expires_at: datetime
    revocable: bool = True

    @model_validator(mode="after")
    def _valid_lifetime(self) -> AuthorityRecord:
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        if not self.revocable:
            raise ValueError("authority decisions must be revocable")
        return self

    @classmethod
    def from_campaign_signal(cls, *_args: object, **_kwargs: object) -> AuthorityRecord:
        raise ValueError(AUTHORITY_SYNTHESIS_ERROR)


class AuthorityRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revocation_id: NonEmpty
    target_record_id: NonEmpty
    target_artifact_hash: Sha256
    actor_identity: NonEmpty
    actor_role: NonEmpty
    reason: NonEmpty
    revocation_artifact_uri: NonEmpty
    revocation_artifact_sha256: Sha256
    revoked_at: datetime

    @model_validator(mode="after")
    def _timestamp_is_aware(self) -> AuthorityRevocation:
        _require_aware(self.revoked_at, "revoked_at")
        return self


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: NonEmpty
    subject: NonEmpty
    claim_state: ClaimState
    claim: NonEmpty
    non_claims: tuple[NonEmpty, ...] = Field(min_length=1)
    proof_floor: NonEmpty
    evidence_ids: tuple[NonEmpty, ...] = ()
    review_ids: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def _support_names_evidence(self) -> ClaimRecord:
        if self.claim_state is ClaimState.SUPPORTED and (
            not self.evidence_ids or not self.review_ids
        ):
            raise ValueError("supported claims require evidence and current-hash review")
        return self


class BlockerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    blocker_id: NonEmpty
    blocker_kind: BlockerKind
    failure_class: BlockerFailureClass
    affected_packet_ids: tuple[NonEmpty, ...] = ()
    affected_score_item_ids: tuple[NonEmpty, ...] = ()
    owner_identity: NonEmpty
    wake_condition: NonEmpty
    next_action: NonEmpty
    state: BlockerState
    opened_at: datetime
    evidence_ids: tuple[NonEmpty, ...] = Field(min_length=1)
    evidence_hashes: tuple[Sha256, ...] = Field(min_length=1)
    wake_artifact_uri: NonEmpty | None = None
    wake_artifact_sha256: Sha256 | None = None
    wake_artifact_size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _scope_and_wake_are_complete(self) -> BlockerRecord:
        _require_aware(self.opened_at, "opened_at")
        if not self.affected_packet_ids and not self.affected_score_item_ids:
            raise ValueError("blockers require an affected packet or score item")
        wake_fields = (
            self.wake_artifact_uri,
            self.wake_artifact_sha256,
            self.wake_artifact_size,
        )
        if self.state in {BlockerState.WOKEN, BlockerState.RESOLVED}:
            if any(value is None for value in wake_fields):
                raise ValueError("woken blockers require a hash-bound wake artifact")
        elif any(value is not None for value in wake_fields):
            raise ValueError("wake artifacts are allowed only for woken or resolved blockers")
        return self


class EvidenceCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: NonEmpty
    evidence_class: EvidenceClass
    support_level: SupportLevel
    state: EvidenceState
    proof_floor: NonEmpty
    artifact_uri: NonEmpty
    artifact_sha256: Sha256
    artifact_size: int = Field(ge=0)
    observed_at: datetime
    claims: tuple[NonEmpty, ...] = ()
    non_claims: tuple[NonEmpty, ...] = ()
    independent_verification_ids: tuple[NonEmpty, ...] = ()
    reviewed_artifact_hashes: tuple[Sha256, ...] = ()
    derivation_code_hash: Sha256 | None = None
    derivation_version: NonEmpty | None = None

    @model_validator(mode="after")
    def _class_specific_metadata(self) -> EvidenceCard:
        _require_aware(self.observed_at, "observed_at")
        if self.support_level is SupportLevel.DERIVED_DETERMINISTICALLY:
            if self.derivation_code_hash is None or self.derivation_version is None:
                raise ValueError(
                    f"derived evidence requires pinned derivation code and version: {self.evidence_id}"
                )
        elif self.derivation_code_hash is not None or self.derivation_version is not None:
            raise ValueError(
                "derivation metadata is allowed only for derived_deterministically evidence"
            )
        if self.evidence_class is EvidenceClass.REVIEW_VERDICT:
            if not self.reviewed_artifact_hashes:
                raise ValueError("review verdicts require reviewed_artifact_hashes")
        elif self.reviewed_artifact_hashes:
            raise ValueError("reviewed_artifact_hashes are allowed only for review verdicts")
        return self


class EvidenceNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: NonEmpty
    evidence_id: NonEmpty | None = None
    node_kind: EvidenceNodeKind
    dependencies: tuple[NonEmpty, ...] = ()
    state: EvidenceState = EvidenceState.CURRENT

    @model_validator(mode="after")
    def _evidence_binding_is_exact(self) -> EvidenceNode:
        card_kinds = {EvidenceNodeKind.EVIDENCE, EvidenceNodeKind.REVIEW}
        if self.node_kind in card_kinds and self.evidence_id is None:
            raise ValueError("evidence and review nodes require evidence_id")
        if self.node_kind not in card_kinds and self.evidence_id is not None:
            raise ValueError("only evidence and review nodes may name evidence_id")
        if self.node_id in self.dependencies:
            raise ValueError("evidence nodes cannot depend on themselves")
        return self


class ActiveStatusPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pointer_id: NonEmpty
    target_node_id: NonEmpty
    activated_at: datetime

    @model_validator(mode="after")
    def _timestamp_is_aware(self) -> ActiveStatusPointer:
        _require_aware(self.activated_at, "activated_at")
        return self


def require_explicit_authority(
    value: object,
    *,
    at: datetime | None = None,
    revocations: tuple[AuthorityRevocation, ...] = (),
    required_scope: tuple[str, ...] = (),
    required_artifact_hashes: tuple[str, ...] = (),
) -> AuthorityRecord:
    if not isinstance(value, AuthorityRecord):
        raise ValueError(AUTHORITY_SYNTHESIS_ERROR)
    instant = at or datetime.now(timezone.utc)
    _require_aware(instant, "at")
    matching_revocation = any(
        revocation.target_record_id == value.record_id
        and revocation.target_artifact_hash in value.artifact_hashes
        and revocation.revoked_at <= instant
        for revocation in revocations
    )
    if (
        not value.revocable
        or instant < value.issued_at
        or instant >= value.expires_at
        or matching_revocation
        or not set(required_scope).issubset(value.scope)
        or not set(required_artifact_hashes).issubset(value.artifact_hashes)
    ):
        raise ValueError(f"{AUTHORITY_INELIGIBLE_ERROR}: {value.record_id}")
    return value


__all__ = [
    "ACTIVE_POINTER_ERROR",
    "AUTHORITY_INELIGIBLE_ERROR",
    "AUTHORITY_SYNTHESIS_ERROR",
    "ActiveStatusPointer",
    "AuthorityKind",
    "AuthorityRecord",
    "AuthorityRevocation",
    "BlockerFailureClass",
    "BlockerKind",
    "BlockerRecord",
    "BlockerState",
    "CampaignDisposition",
    "ClaimRecord",
    "ClaimState",
    "EvidenceCard",
    "EvidenceClass",
    "EvidenceNode",
    "EvidenceNodeKind",
    "EvidenceState",
    "ScoreItemState",
    "SupportLevel",
    "require_explicit_authority",
]

ACTIVE_POINTER_ERROR = "exactly one active-status pointer is required"
