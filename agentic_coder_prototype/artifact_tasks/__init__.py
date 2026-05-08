"""Artifact-gated task primitives for external-evaluation campaigns."""

from .adapters import (
    artifact_task_result_to_candidate_packet,
    artifact_task_result_to_optimize_evaluation_record,
    artifact_task_result_to_rl_episode,
)
from .campaign import CampaignCandidateSpec, CampaignSpec, CampaignSummary, run_campaign
from .contracts import (
    ArtifactCheck,
    ArtifactContract,
    ArtifactRequirement,
    ArtifactValidationResult,
    hash_file,
    safe_relative_path,
    validate_artifact_contract,
)
from .evaluators import EvaluatorResult, EvaluatorSpec, run_evaluator, run_evaluators
from .evidence import EvidenceBundleManifest, write_evidence_bundle
from .materialize import MaterializationResult, MaterializationSpec, materialize_response_artifact
from .runner import ArtifactTaskResult, ArtifactTaskSpec, run_artifact_task
from .workspace import WorkspaceBridgeResult, WorkspaceBridgeSpec, prepare_workspace_bridge

__all__ = [
    "ArtifactCheck",
    "ArtifactContract",
    "ArtifactRequirement",
    "ArtifactTaskResult",
    "ArtifactTaskSpec",
    "ArtifactValidationResult",
    "CampaignCandidateSpec",
    "CampaignSpec",
    "CampaignSummary",
    "EvaluatorResult",
    "EvaluatorSpec",
    "EvidenceBundleManifest",
    "MaterializationResult",
    "MaterializationSpec",
    "WorkspaceBridgeResult",
    "WorkspaceBridgeSpec",
    "artifact_task_result_to_candidate_packet",
    "artifact_task_result_to_optimize_evaluation_record",
    "artifact_task_result_to_rl_episode",
    "hash_file",
    "materialize_response_artifact",
    "prepare_workspace_bridge",
    "run_artifact_task",
    "run_campaign",
    "run_evaluator",
    "run_evaluators",
    "safe_relative_path",
    "validate_artifact_contract",
    "write_evidence_bundle",
]
