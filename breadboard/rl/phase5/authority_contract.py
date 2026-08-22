from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TypeAlias


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IDENTITY_FIELDS = (
    "source_head",
    "config_digest",
    "task_digest",
    "run_id",
    "model_digest",
    "threshold_digest",
)
_EVIDENCE_CLASSES = {
    "source_reference",
    "fixture_oracle",
    "compile_artifact",
    "admission_decision",
    "local_contract_test",
    "local_process_integration",
    "local_container_attestation",
    "target_slurm_command",
    "target_episode_lifecycle",
    "target_training_run",
    "digitalocean_provider_run",
    "artifact_integrity",
    "replay_reproduction",
    "negative_control",
    "cleanup_rollback",
    "blocker",
    "review_verdict",
    "authority_decision",
}


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"production transition requires {field}")


def _class_value(value: object) -> object:
    return getattr(value, "value", None)


def _require_identity(identity: object) -> None:
    if type(identity).__name__ != "FrozenEvidenceIdentity":
        raise ValueError("production transition requires a frozen identity")
    for field in _IDENTITY_FIELDS:
        _require_text(getattr(identity, field, None), field)


def _require_class(value: object, expected: str | None = None) -> None:
    class_value = _class_value(value)
    if class_value not in _EVIDENCE_CLASSES:
        raise ValueError("production transition evidence class is closed")
    if expected is not None and class_value != expected:
        raise ValueError(
            f"production transition requires evidence class {expected}"
        )


@dataclass(frozen=True)
class ExternalArtifactClaim:
    evidence_id: str
    evidence_class: object
    identity: object

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_identity(self.identity)
        _require_class(self.evidence_class)


@dataclass(frozen=True)
class IBMTargetExecutionResult:
    evidence_id: str
    evidence_class: object
    identity: object
    provider: str
    execution_plane: str
    scheduler: str
    operation: str
    exit_code: int
    target_run_id: str

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_identity(self.identity)
        _require_class(self.evidence_class, "target_slurm_command")
        if (
            self.provider != "IBM"
            or self.execution_plane != "target"
            or self.scheduler != "slurm"
            or self.operation != "episode"
            or type(self.exit_code) is not int
            or self.exit_code != 0
        ):
            raise ValueError("IBM target result is not a successful target episode")
        _require_text(self.target_run_id, "target_run_id")


@dataclass(frozen=True)
class TargetTrainingExecutionResult:
    evidence_id: str
    evidence_class: object
    identity: object
    provider: str
    execution_plane: str
    scheduler: str
    operation: str
    exit_code: int
    training_run_id: str
    checkpoint_digest: str

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_identity(self.identity)
        _require_class(self.evidence_class, "target_training_run")
        if (
            self.provider != "IBM"
            or self.execution_plane != "target"
            or self.scheduler != "slurm"
            or self.operation != "training"
            or type(self.exit_code) is not int
            or self.exit_code != 0
        ):
            raise ValueError("target training result is not a successful target run")
        _require_text(self.training_run_id, "training_run_id")
        if not isinstance(self.checkpoint_digest, str) or not _SHA256.fullmatch(
            self.checkpoint_digest
        ):
            raise ValueError(
                "target training result requires an exact checkpoint digest"
            )


@dataclass(frozen=True)
class ScopedAuthorityDecisionResult:
    evidence_id: str
    evidence_class: object
    identity: object
    actor_role: str
    authority_record_id: str
    decision: str
    scope: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_identity(self.identity)
        _require_class(self.evidence_class, "authority_decision")
        if self.actor_role != "phase5-supervisor" or self.decision != "approved":
            raise ValueError(
                "scoped authority result is not an approved supervisor decision"
            )
        _require_text(self.authority_record_id, "authority_record_id")
        if (
            type(self.scope) is not tuple
            or not self.scope
            or any(type(item) is not str or not item for item in self.scope)
        ):
            raise ValueError("scoped authority result requires a non-empty exact scope")


@dataclass(frozen=True)
class SupportEvidenceResult:
    evidence_id: str
    evidence_class: object
    identity: object

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_identity(self.identity)
        _require_class(self.evidence_class)


ProductionTransitionResult: TypeAlias = (
    ExternalArtifactClaim
    | IBMTargetExecutionResult
    | TargetTrainingExecutionResult
    | ScopedAuthorityDecisionResult
    | SupportEvidenceResult
)


@dataclass(frozen=True)
class CanonicalEvidenceArtifactDTO:
    object_id: str
    artifact_uri: str
    artifact_bytes: bytes
    authority_id: str
    authority_hmac: str


@dataclass(frozen=True)
class EvidenceInvalidationResultDTO:
    rejection_code: str
    root_node_id: str
    affected_node_ids: tuple[str, ...]
    effective_states: tuple[tuple[str, object], ...]
    award_allowed: bool = False
    promotion_allowed: bool = False


@dataclass(frozen=True)
class ScoreItemDTO:
    item_id: str
    points: int
    workstream: str
    proof_floor: str
    description: str
    pass_predicate: str
    owner_packet: str


@dataclass(frozen=True)
class ScoreDecisionDTO:
    item_id: str
    state: object
    evidence_ids: tuple[str, ...]
    review_ids: tuple[str, ...]
    supervisor_decision_id: str | None


__all__ = [
    "CanonicalEvidenceArtifactDTO",
    "EvidenceInvalidationResultDTO",
    "ExternalArtifactClaim",
    "IBMTargetExecutionResult",
    "ProductionTransitionResult",
    "ScopedAuthorityDecisionResult",
    "ScoreDecisionDTO",
    "ScoreItemDTO",
    "SupportEvidenceResult",
    "TargetTrainingExecutionResult",
]
