from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Literal

from breadboard_engine.compilation.contracts import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(
    r"^(?:artifact|breadboard|cas|evidence|ibm)://[^\s@?#]+@sha256:[0-9a-f]{64}$"
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_JSON_POINTER_RE = re.compile(r"^/(?:[^~/]|~[01])+(?:/(?:[^~/]|~[01])*)*$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

VARIANT_IDS = (
    "codex-like",
    "claude-like",
    "pi-like",
    "opencode",
    "oh-my-opencode",
    "unknown-name",
)
VariantId = Literal[
    "codex-like",
    "claude-like",
    "pi-like",
    "opencode",
    "oh-my-opencode",
    "unknown-name",
]
_FIXED_DISPLAY_NAMES = {
    "codex-like": "Codex-like",
    "claude-like": "Claude-like",
    "pi-like": "Pi-like",
    "opencode": "OpenCode",
    "oh-my-opencode": "oh-my-opencode",
}

CLAIM_BOUNDARY = (
    "canonical F4 campaign authoring and structural validation for only the exact immutable "
    "inputs, six config variants, L6 environments, attempts, and episodes in this manifest; "
    "it does not establish IBM target success, F3 authority, learned superiority, benchmark "
    "quality, training, scale, production readiness, or external acceptance"
)

_CHECKS = (
    "closed-six-variant-set",
    "unique-config-and-receipt-identities",
    "named-compiler-visible-deltas",
    "exact-l6-environment-cartesian-coverage",
    "immutable-ibm-admission-set-bindings",
    "non-config-identity-invariance",
    "current-successful-episode-evidence",
    "weighted-v1-oracle",
    "aa-determinism",
    "no-cross-arm-evidence-reuse",
    "admitted-overlay-bindings",
    "optimizer-acceptance-receipts",
    "authoritative-cleanup",
    "explicit-no-fallback",
)


class F4CampaignValidationError(ValueError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_field(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("lowercase sha256 digest required")
    return value


def _id_field(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValueError("bounded canonical identifier required")
    return value


def _utc_second(value: str) -> str:
    if _UTC_RE.fullmatch(value) is None:
        raise ValueError("UTC-second timestamp required")
    return value


class ImmutableRef(_ExactModel):
    reference: str = Field(min_length=1, max_length=4096)
    digest: str

    _validate_digest = field_validator("digest")(_digest_field)

    @model_validator(mode="after")
    def content_addressed(self) -> "ImmutableRef":
        if not _REFERENCE_RE.fullmatch(self.reference):
            raise ValueError("immutable content-addressed reference required")
        if not self.reference.endswith("@" + self.digest):
            raise ValueError("reference digest does not match its declared digest")
        return self


class CampaignValidity(_ExactModel):
    not_before: str
    expires_at: str

    @model_validator(mode="after")
    def ordered(self) -> "CampaignValidity":
        if (
            _UTC_RE.fullmatch(self.not_before) is None
            or _UTC_RE.fullmatch(self.expires_at) is None
            or self.not_before >= self.expires_at
        ):
            raise ValueError("campaign validity must be ordered UTC seconds")
        return self


class AuthorityRootArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-authority-root.v1"]
    compiler_identity_ref: ImmutableRef
    admission_policy_ref: ImmutableRef
    operator_ceiling_ref: ImmutableRef
    runtime_abi: str
    validity: CampaignValidity

    _runtime_abi = field_validator("runtime_abi")(_id_field)


class AuthorityRootBinding(_ExactModel):
    ref: ImmutableRef
    artifact: AuthorityRootArtifact

    @model_validator(mode="after")
    def content_bound(self) -> "AuthorityRootBinding":
        raw = canonical_json_bytes(self.artifact.model_dump(mode="json"))
        if _digest(raw) != self.ref.digest:
            raise ValueError("authority root digest does not bind canonical contents")
        return self


class CompilerVisibleSemanticDelta(_ExactModel):
    name: str = Field(min_length=1, max_length=128)
    compiler_field_pointer: str = Field(min_length=1, max_length=512)
    before_digest: str
    after_digest: str

    _digests = field_validator("before_digest", "after_digest")(_digest_field)
    _name = field_validator("name")(_id_field)

    @model_validator(mode="after")
    def visible_and_material(self) -> "CompilerVisibleSemanticDelta":
        if not _JSON_POINTER_RE.fullmatch(self.compiler_field_pointer):
            raise ValueError(
                "semantic delta requires one canonical compiler JSON pointer"
            )
        protected = {
            "runner",
            "runtime",
            "route",
            "secret",
            "sandbox",
            "image",
            "verifier",
            "repository",
            "task",
            "model",
            "checkpoint",
            "evidence_policy",
            "retention_policy",
        }
        pointer_parts = {
            part.replace("~1", "/").replace("~0", "~").casefold()
            for part in self.compiler_field_pointer.split("/")[1:]
        }
        if pointer_parts & protected:
            raise ValueError("semantic delta targets a protected non-config identity")
        if self.before_digest == self.after_digest:
            raise ValueError("semantic delta must change compiler-visible semantics")
        return self


class CampaignInvariantIdentity(_ExactModel):
    task_id: Literal["R-SWE-001"]
    task_row_ref: ImmutableRef
    task_contract_digest: str
    repository_snapshot_ref: ImmutableRef
    model_ref: ImmutableRef
    checkpoint_ref: ImmutableRef
    task_image_ref: ImmutableRef
    verifier_image_ref: ImmutableRef
    verifier_ref: ImmutableRef

    _task_contract = field_validator("task_contract_digest")(_digest_field)

    @model_validator(mode="after")
    def distinct_identity_classes(self) -> "CampaignInvariantIdentity":
        refs = (
            self.task_row_ref,
            self.repository_snapshot_ref,
            self.model_ref,
            self.checkpoint_ref,
            self.task_image_ref,
            self.verifier_image_ref,
            self.verifier_ref,
        )
        if len({ref.digest for ref in refs}) != len(refs):
            raise ValueError(
                "non-config authority identities must be distinct exact objects"
            )
        return self


class ConfigVariant(_ExactModel):
    variant_id: VariantId
    display_name: str = Field(min_length=1, max_length=128)
    generated_name_receipt_ref: ImmutableRef | None
    config_bundle_ref: ImmutableRef
    dependency_closure_ref: ImmutableRef
    compiler_identity_ref: ImmutableRef
    compiled_config_ref: ImmutableRef
    admission_receipt_ref: ImmutableRef
    semantic_delta: CompilerVisibleSemanticDelta
    optimizer_generated: bool

    @model_validator(mode="after")
    def exact_name_class(self) -> "ConfigVariant":
        expected = _FIXED_DISPLAY_NAMES.get(self.variant_id)
        if expected is not None:
            if (
                self.display_name != expected
                or self.generated_name_receipt_ref is not None
            ):
                raise ValueError("fixed variant display identity drift")
        else:
            if self.generated_name_receipt_ref is None:
                raise ValueError(
                    "generated unknown-name variant requires its generation receipt"
                )
            if self.display_name.casefold() in {
                name.casefold() for name in _FIXED_DISPLAY_NAMES.values()
            }:
                raise ValueError(
                    "generated display name substitutes a known config name"
                )
            if not _ID_RE.fullmatch(self.display_name):
                raise ValueError(
                    "generated display name must be a bounded canonical identifier"
                )
        return self


class L6Environment(_ExactModel):
    environment_id: str
    environment_kind: Literal["local-docker", "ibm-one-node"]
    environment_ref: ImmutableRef
    infrastructure_path_ref: ImmutableRef

    _environment_id = field_validator("environment_id")(_id_field)


class IBMAdmissionSetRootArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-ibm-admission-set-root.v1"]
    authority_root_ref: ImmutableRef
    compiler_identity_ref: ImmutableRef
    admission_policy_ref: ImmutableRef
    operator_ceiling_ref: ImmutableRef
    runtime_abi: str
    validity: CampaignValidity
    admission_receipt_refs: tuple[ImmutableRef, ...] = Field(min_length=1)

    _runtime_abi = field_validator("runtime_abi")(_id_field)


class IBMAdmissionSetRootBinding(_ExactModel):
    ref: ImmutableRef
    artifact: IBMAdmissionSetRootArtifact

    @model_validator(mode="after")
    def content_bound(self) -> "IBMAdmissionSetRootBinding":
        raw = canonical_json_bytes(self.artifact.model_dump(mode="json"))
        if _digest(raw) != self.ref.digest:
            raise ValueError(
                "IBM admission-set root digest does not bind canonical contents"
            )
        return self


class L6EnvironmentSetRootArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-l6-environment-set-root.v1"]
    authority_root_ref: ImmutableRef
    environments: tuple[L6Environment, ...] = Field(min_length=1)


class L6EnvironmentSetRootBinding(_ExactModel):
    ref: ImmutableRef
    artifact: L6EnvironmentSetRootArtifact

    @model_validator(mode="after")
    def content_bound(self) -> "L6EnvironmentSetRootBinding":
        raw = canonical_json_bytes(self.artifact.model_dump(mode="json"))
        if _digest(raw) != self.ref.digest:
            raise ValueError(
                "L6 environment-set root digest does not bind canonical contents"
            )
        return self


class CleanupEvidence(_ExactModel):
    cleanup_receipt_ref: ImmutableRef
    authoritative_closed: Literal[True]
    active_lease_ids: tuple[str, ...]
    orphan_resource_ids: tuple[str, ...]
    leaked_artifact_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]

    @model_validator(mode="after")
    def complete(self) -> "CleanupEvidence":
        if (
            self.active_lease_ids
            or self.orphan_resource_ids
            or self.leaked_artifact_ids
            or self.cleanup_errors
        ):
            raise ValueError("episode cleanup is incomplete")
        return self


class TargetEpisodeEvidence(_ExactModel):
    target_attempt_id: str
    episode_id: str
    environment_id: str
    environment_ref: ImmutableRef
    target_attempt_output_ref: ImmutableRef
    episode_output_ref: ImmutableRef
    target_report_output_ref: ImmutableRef
    effective_plan_ref: ImmutableRef
    compiled_config_ref: ImmutableRef
    admission_receipt_ref: ImmutableRef
    invariant_identity: CampaignInvariantIdentity
    attempt_state: Literal["successful"]
    evidence_state: Literal["current"]
    superseded_by_attempt_id: None
    target_report_ref: ImmutableRef
    evidence_manifest_ref: ImmutableRef
    completed_envelope_ref: ImmutableRef
    closed_envelope_ref: ImmutableRef
    tool_call_receipt_refs: tuple[ImmutableRef, ...] = Field(min_length=1)
    server_verifier_result_ref: ImmutableRef
    terminal_outcome: Literal["completed-and-closed"]
    verifier_passed: Literal[True]
    reward: Literal[1]
    fallback_used: Literal[False]
    fallback_variant_id: None
    cleanup: CleanupEvidence

    _attempt_id = field_validator("target_attempt_id", "episode_id", "environment_id")(
        _id_field
    )


class EnvironmentCoverageRow(_ExactModel):
    environment_id: str
    variant_id: VariantId
    admission_receipt_ref: ImmutableRef
    evidence: TargetEpisodeEvidence

    _environment_id = field_validator("environment_id")(_id_field)


class VariantEpisodeRow(_ExactModel):
    variant_id: VariantId
    evidence: TargetEpisodeEvidence


class WeightedCandidate(_ExactModel):
    variant_id: VariantId
    admission_receipt_ref: ImmutableRef
    weight: int = Field(gt=0, le=2**53 - 1)
    ordered_overlay_receipt_refs: tuple[ImmutableRef, ...]


class WeightedSelection(_ExactModel):
    selection_id: str
    config_set_ref: ImmutableRef
    selection_nonce: str
    task_contract_digest: str
    policy_capability_digest: str
    candidates: tuple[WeightedCandidate, WeightedCandidate]
    oracle_draw_digest: str
    oracle_selected_variant_id: VariantId
    selection_record_ref: ImmutableRef
    persisted_before_lease: Literal[True]
    fallback_used: Literal[False]
    evidence: TargetEpisodeEvidence

    _selection_id = field_validator("selection_id")(_id_field)
    _digests = field_validator(
        "selection_nonce",
        "task_contract_digest",
        "policy_capability_digest",
        "oracle_draw_digest",
    )(_digest_field)


class AAArm(_ExactModel):
    evidence: TargetEpisodeEvidence
    deterministic_output_digest: str

    _output = field_validator("deterministic_output_digest")(_digest_field)


class AADeterminismControl(_ExactModel):
    control_id: str
    variant_id: VariantId
    deterministic_input_digest: str
    arm_a: AAArm
    arm_b: AAArm

    _control_id = field_validator("control_id")(_id_field)
    _input = field_validator("deterministic_input_digest")(_digest_field)


class AdmittedOverlayExecution(_ExactModel):
    overlay_execution_id: str
    base_variant_id: VariantId
    ordered_overlay_refs: tuple[ImmutableRef, ...] = Field(min_length=1)
    overlay_admission_receipt_ref: ImmutableRef
    evidence: TargetEpisodeEvidence

    _execution_id = field_validator("overlay_execution_id")(_id_field)


OPTIMIZER_RECEIPT_KINDS = (
    "generation-provenance",
    "source-member-identity",
    "objective",
    "constraints",
    "paired-ab-evaluations",
    "aa-noise-control",
    "held-out-repeat",
    "disposition",
)
OptimizerReceiptKind = Literal[
    "generation-provenance",
    "source-member-identity",
    "objective",
    "constraints",
    "paired-ab-evaluations",
    "aa-noise-control",
    "held-out-repeat",
    "disposition",
]


class F4OptimizerGenerationFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-generation.v1"]
    mutation_axis: Literal[
        "prompt",
        "mode",
        "sampling",
        "max-turns",
        "action-timeout",
        "tool-removal",
        "artifact-policy",
    ]
    generated_variant_id: VariantId
    parent_variant_id: VariantId


class F4OptimizerSourceFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-source.v1"]
    source_member_path: str = Field(min_length=1, max_length=4096)
    source_member_digest: str
    config_bundle_ref: ImmutableRef
    dependency_closure_ref: ImmutableRef
    compiler_identity_ref: ImmutableRef
    compiled_config_ref: ImmutableRef
    admission_receipt_ref: ImmutableRef

    _source_member_digest = field_validator("source_member_digest")(_digest_field)


class F4OptimizerObjectiveFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-objective.v1"]
    primary_objective_frozen: Literal[True]
    secondary_cost_frozen: Literal[True]
    primary_improvement: float = Field(ge=0)
    secondary_cost_reduction: float = Field(ge=0)
    required_secondary_cost_reduction: float = Field(ge=0)


class F4OptimizerConstraintFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-constraints.v1"]
    non_config_inputs_identical: Literal[True]
    correctness_regression: Literal[False]
    security_regression: Literal[False]
    isolation_regression: Literal[False]
    evidence_regression: Literal[False]
    cleanup_regression: Literal[False]


class F4OptimizerPairedABFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-paired-ab.v1"]
    paired_ab_evaluation_count: int = Field(ge=20, le=1_000_000)


class F4OptimizerAANoiseFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-aa-noise.v1"]
    aa_noise_upper_bound: float = Field(ge=0)


class F4OptimizerHeldOutFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-held-out.v1"]
    held_out_repeated: Literal[True]
    repeat_count: int = Field(ge=1, le=1_000_000)


class F4OptimizerDispositionFacts(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-disposition.v1"]
    optimizer_acceptance_id: str
    disposition: Literal["accepted"]
    acceptance_basis: Literal[
        "improved-beyond-aa-noise", "tie-with-lower-secondary-cost"
    ]

    _acceptance_id = field_validator("optimizer_acceptance_id")(_id_field)


F4OptimizerReceiptFacts = (
    F4OptimizerGenerationFacts
    | F4OptimizerSourceFacts
    | F4OptimizerObjectiveFacts
    | F4OptimizerConstraintFacts
    | F4OptimizerPairedABFacts
    | F4OptimizerAANoiseFacts
    | F4OptimizerHeldOutFacts
    | F4OptimizerDispositionFacts
)


class F4OptimizerReceiptBody(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-receipt.v1"]
    receipt_kind: OptimizerReceiptKind
    sequence_index: int = Field(ge=0, le=7)
    optimizer_acceptance_id: str
    variant_id: VariantId
    parent_variant_id: VariantId
    source_member_path: str = Field(min_length=1, max_length=4096)
    source_member_digest: str
    config_bundle_ref: ImmutableRef
    dependency_closure_ref: ImmutableRef
    compiler_identity_ref: ImmutableRef
    compiled_config_ref: ImmutableRef
    admission_receipt_ref: ImmutableRef
    facts: F4OptimizerReceiptFacts

    _acceptance_id = field_validator("optimizer_acceptance_id")(_id_field)
    _source_member_digest = field_validator("source_member_digest")(_digest_field)


class F4OptimizerReceiptBinding(_ExactModel):
    ref: ImmutableRef
    artifact: F4OptimizerReceiptBody

    @model_validator(mode="after")
    def content_bound(self) -> "F4OptimizerReceiptBinding":
        raw = canonical_json_bytes(self.artifact.model_dump(mode="json"))
        if _digest(raw) != self.ref.digest:
            raise ValueError("optimizer receipt ref does not bind canonical body")
        return self


class F4OptimizerWorkPacket(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-optimizer-work-packet.v1"]
    optimizer_acceptance_id: str
    variant_id: VariantId
    parent_variant_id: VariantId
    ordered_receipts: tuple[
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
        F4OptimizerReceiptBinding,
    ]

    _acceptance_id = field_validator("optimizer_acceptance_id")(_id_field)

    @model_validator(mode="after")
    def exact_ordered_inventory(self) -> "F4OptimizerWorkPacket":
        if tuple(
            binding.artifact.receipt_kind for binding in self.ordered_receipts
        ) != OPTIMIZER_RECEIPT_KINDS or tuple(
            binding.artifact.sequence_index for binding in self.ordered_receipts
        ) != tuple(range(len(OPTIMIZER_RECEIPT_KINDS))):
            raise ValueError("optimizer receipt inventory is missing, extra, or reordered")
        expected_fact_types = (
            F4OptimizerGenerationFacts,
            F4OptimizerSourceFacts,
            F4OptimizerObjectiveFacts,
            F4OptimizerConstraintFacts,
            F4OptimizerPairedABFacts,
            F4OptimizerAANoiseFacts,
            F4OptimizerHeldOutFacts,
            F4OptimizerDispositionFacts,
        )
        if tuple(
            type(binding.artifact.facts) for binding in self.ordered_receipts
        ) != expected_fact_types:
            raise ValueError("optimizer receipt facts do not match ordered kinds")
        if len({binding.ref.digest for binding in self.ordered_receipts}) != len(
            OPTIMIZER_RECEIPT_KINDS
        ):
            raise ValueError("optimizer receipt inventory contains a duplicate")
        expected = (
            self.optimizer_acceptance_id,
            self.variant_id,
            self.parent_variant_id,
        )
        if any(
            (
                binding.artifact.optimizer_acceptance_id,
                binding.artifact.variant_id,
                binding.artifact.parent_variant_id,
            )
            != expected
            for binding in self.ordered_receipts
        ):
            raise ValueError("optimizer receipt inventory identity drift")
        closure_identity = (
            self.ordered_receipts[0].artifact.source_member_path,
            self.ordered_receipts[0].artifact.source_member_digest,
            self.ordered_receipts[0].artifact.config_bundle_ref,
            self.ordered_receipts[0].artifact.dependency_closure_ref,
            self.ordered_receipts[0].artifact.compiler_identity_ref,
            self.ordered_receipts[0].artifact.compiled_config_ref,
            self.ordered_receipts[0].artifact.admission_receipt_ref,
        )
        if any(
            (
                binding.artifact.source_member_path,
                binding.artifact.source_member_digest,
                binding.artifact.config_bundle_ref,
                binding.artifact.dependency_closure_ref,
                binding.artifact.compiler_identity_ref,
                binding.artifact.compiled_config_ref,
                binding.artifact.admission_receipt_ref,
            )
            != closure_identity
            for binding in self.ordered_receipts
        ):
            raise ValueError("optimizer receipt source-closure identity drift")
        source_facts = self.ordered_receipts[1].artifact.facts
        assert isinstance(source_facts, F4OptimizerSourceFacts)
        if (
            source_facts.source_member_path,
            source_facts.source_member_digest,
            source_facts.config_bundle_ref,
            source_facts.dependency_closure_ref,
            source_facts.compiler_identity_ref,
            source_facts.compiled_config_ref,
            source_facts.admission_receipt_ref,
        ) != closure_identity:
            raise ValueError("optimizer source facts do not bind rebuilt closure")
        return self


class F4OptimizerWorkPacketBinding(_ExactModel):
    ref: ImmutableRef
    artifact: F4OptimizerWorkPacket

    @model_validator(mode="after")
    def content_bound(self) -> "F4OptimizerWorkPacketBinding":
        raw = canonical_json_bytes(self.artifact.model_dump(mode="json"))
        if _digest(raw) != self.ref.digest:
            raise ValueError("optimizer work-packet ref does not bind canonical body")
        return self


class AcceptedOptimizerVariant(_ExactModel):
    optimizer_acceptance_id: str
    variant_id: VariantId
    parent_variant_id: VariantId
    mutation_axis: Literal[
        "prompt",
        "mode",
        "sampling",
        "max-turns",
        "action-timeout",
        "tool-removal",
        "artifact-policy",
    ]
    optimizer_work_packet: F4OptimizerWorkPacketBinding
    source_member_path: str = Field(min_length=1, max_length=4096)
    source_member_digest: str
    paired_ab_evaluation_count: int = Field(ge=20, le=1_000_000)
    non_config_inputs_identical: Literal[True]
    primary_objective_frozen: Literal[True]
    secondary_cost_frozen: Literal[True]
    correctness_regression: Literal[False]
    security_regression: Literal[False]
    isolation_regression: Literal[False]
    evidence_regression: Literal[False]
    cleanup_regression: Literal[False]
    held_out_repeated: Literal[True]
    acceptance_basis: Literal[
        "improved-beyond-aa-noise", "tie-with-lower-secondary-cost"
    ]
    primary_improvement: float = Field(ge=0)
    aa_noise_upper_bound: float = Field(ge=0)
    secondary_cost_reduction: float = Field(ge=0)
    required_secondary_cost_reduction: float = Field(ge=0)
    evidence: TargetEpisodeEvidence

    _acceptance_id = field_validator("optimizer_acceptance_id")(_id_field)
    _source_member_digest = field_validator("source_member_digest")(_digest_field)

    @model_validator(mode="after")
    def accepted_by_frozen_rule(self) -> "AcceptedOptimizerVariant":
        values = (
            self.primary_improvement,
            self.aa_noise_upper_bound,
            self.secondary_cost_reduction,
            self.required_secondary_cost_reduction,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("optimizer objective values must be finite")
        if self.variant_id == self.parent_variant_id:
            raise ValueError("optimizer parent and accepted variant must differ")
        packet = self.optimizer_work_packet.artifact
        if (
            packet.optimizer_acceptance_id != self.optimizer_acceptance_id
            or packet.variant_id != self.variant_id
            or packet.parent_variant_id != self.parent_variant_id
        ):
            raise ValueError("accepted optimizer work-packet identity drift")
        generation = packet.ordered_receipts[0].artifact.facts
        objective = packet.ordered_receipts[2].artifact.facts
        constraints = packet.ordered_receipts[3].artifact.facts
        paired = packet.ordered_receipts[4].artifact.facts
        noise = packet.ordered_receipts[5].artifact.facts
        held_out = packet.ordered_receipts[6].artifact.facts
        disposition = packet.ordered_receipts[7].artifact.facts
        assert isinstance(generation, F4OptimizerGenerationFacts)
        assert isinstance(objective, F4OptimizerObjectiveFacts)
        assert isinstance(constraints, F4OptimizerConstraintFacts)
        assert isinstance(paired, F4OptimizerPairedABFacts)
        assert isinstance(noise, F4OptimizerAANoiseFacts)
        assert isinstance(held_out, F4OptimizerHeldOutFacts)
        assert isinstance(disposition, F4OptimizerDispositionFacts)
        if (
            generation.generated_variant_id != self.variant_id
            or generation.parent_variant_id != self.parent_variant_id
            or generation.mutation_axis != self.mutation_axis
            or objective.primary_objective_frozen
            != self.primary_objective_frozen
            or objective.secondary_cost_frozen != self.secondary_cost_frozen
            or objective.primary_improvement != self.primary_improvement
            or objective.secondary_cost_reduction != self.secondary_cost_reduction
            or objective.required_secondary_cost_reduction
            != self.required_secondary_cost_reduction
            or constraints.non_config_inputs_identical
            != self.non_config_inputs_identical
            or constraints.correctness_regression != self.correctness_regression
            or constraints.security_regression != self.security_regression
            or constraints.isolation_regression != self.isolation_regression
            or constraints.evidence_regression != self.evidence_regression
            or constraints.cleanup_regression != self.cleanup_regression
            or paired.paired_ab_evaluation_count
            != self.paired_ab_evaluation_count
            or noise.aa_noise_upper_bound != self.aa_noise_upper_bound
            or held_out.held_out_repeated != self.held_out_repeated
            or disposition.optimizer_acceptance_id
            != self.optimizer_acceptance_id
            or disposition.acceptance_basis != self.acceptance_basis
        ):
            raise ValueError("optimizer receipt facts do not bind accepted decision")
        source = packet.ordered_receipts[1].artifact.facts
        assert isinstance(source, F4OptimizerSourceFacts)
        if (
            source.source_member_path != self.source_member_path
            or source.source_member_digest != self.source_member_digest
        ):
            raise ValueError("optimizer source member does not bind accepted decision")
        if self.acceptance_basis == "improved-beyond-aa-noise":
            if self.primary_improvement <= self.aa_noise_upper_bound:
                raise ValueError("accepted objective does not exceed A/A noise")
        elif (
            self.primary_improvement != 0
            or self.required_secondary_cost_reduction <= 0
            or self.secondary_cost_reduction < self.required_secondary_cost_reduction
        ):
            raise ValueError(
                "accepted tie does not meet frozen secondary-cost reduction"
            )
        return self


class F4TargetExecutionReceipt(_ExactModel):
    target_attempt_id: str
    episode_id: str
    variant_id: VariantId
    environment_id: str
    environment_ref: ImmutableRef
    source_runtime_ref: ImmutableRef
    target_run_id: str
    target_job_id: str
    target_node_id: str
    invariant_identity: CampaignInvariantIdentity
    target_attempt_output_ref: ImmutableRef
    episode_output_ref: ImmutableRef
    target_report_output_ref: ImmutableRef
    compiled_config_ref: ImmutableRef
    admission_receipt_ref: ImmutableRef
    effective_plan_ref: ImmutableRef
    evidence_manifest_ref: ImmutableRef
    completed_envelope_ref: ImmutableRef
    closed_envelope_ref: ImmutableRef
    tool_call_receipt_refs: tuple[ImmutableRef, ...] = Field(min_length=1)
    server_verifier_result_ref: ImmutableRef
    verifier_passed: Literal[True]
    reward: Literal[1]
    cleanup_receipt_ref: ImmutableRef
    terminal_outcome: Literal["completed-and-closed"]

    _ids = field_validator(
        "target_attempt_id",
        "episode_id",
        "environment_id",
        "target_run_id",
        "target_job_id",
        "target_node_id",
    )(_id_field)


class F4TargetEvidenceReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-target-evidence-report.v1"]
    report_id: str
    source_runtime_ref: ImmutableRef
    executions: tuple[F4TargetExecutionReceipt, ...] = Field(min_length=1)

    _report_id = field_validator("report_id")(_id_field)

    @model_validator(mode="after")
    def unique_executions(self) -> "F4TargetEvidenceReport":
        episode_ids = [item.episode_id for item in self.executions]
        attempt_ids = [item.target_attempt_id for item in self.executions]
        attempt_outputs = [
            item.target_attempt_output_ref.digest for item in self.executions
        ]
        episode_outputs = [item.episode_output_ref.digest for item in self.executions]
        if any(
            len(values) != len(set(values))
            for values in (
                episode_ids,
                attempt_ids,
                attempt_outputs,
                episode_outputs,
            )
        ):
            raise ValueError(
                "target evidence report reuses episode, attempt, or output identity"
            )
        expected_identity = (
            self.source_runtime_ref,
            self.executions[0].target_run_id,
            self.executions[0].target_job_id,
            self.executions[0].target_node_id,
        )
        if any(
            (
                item.source_runtime_ref,
                item.target_run_id,
                item.target_job_id,
                item.target_node_id,
            )
            != expected_identity
            for item in self.executions
        ):
            raise ValueError("target evidence report execution authority identity drift")
        return self


class F4TargetEvidenceReportBinding(_ExactModel):
    ref: ImmutableRef
    artifact: F4TargetEvidenceReport

    @model_validator(mode="after")
    def content_bound(self) -> "F4TargetEvidenceReportBinding":
        raw = canonical_json_bytes(self.artifact.model_dump(mode="json"))
        if _digest(raw) != self.ref.digest:
            raise ValueError("target report digest does not bind canonical contents")
        return self


class F4CampaignInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-campaign-input.v1"]
    campaign_id: str
    authority_root: AuthorityRootBinding
    compiler_identity_ref: ImmutableRef
    ibm_admission_set_root: IBMAdmissionSetRootBinding
    l6_environment_set_root: L6EnvironmentSetRootBinding
    evaluated_at: str
    target_reports: tuple[F4TargetEvidenceReportBinding, ...] = Field(min_length=1)
    invariant_identity: CampaignInvariantIdentity
    environments: tuple[L6Environment, ...] = Field(min_length=1)
    variants: tuple[ConfigVariant, ...]
    ibm_admission_receipt_refs: tuple[ImmutableRef, ...]
    environment_coverage: tuple[EnvironmentCoverageRow, ...]
    variant_episodes: tuple[VariantEpisodeRow, ...]
    weighted_selections: tuple[WeightedSelection, ...] = Field(min_length=1)
    aa_determinism_controls: tuple[AADeterminismControl, ...] = Field(min_length=1)
    admitted_overlay_executions: tuple[AdmittedOverlayExecution, ...] = Field(
        min_length=1
    )
    optimizer_disposition: Literal["accepted-variants", "no_variant_accepted"]
    accepted_optimizer_variants: tuple[AcceptedOptimizerVariant, ...]
    optimized_config_set_ref: ImmutableRef | None

    _campaign_id = field_validator("campaign_id")(_id_field)
    _evaluated_at = field_validator("evaluated_at")(_utc_second)

    @model_validator(mode="after")
    def validate_campaign(self) -> "F4CampaignInput":
        _validate_campaign(self)
        return self


class F4ValidationCheck(_ExactModel):
    check_id: Literal[
        "closed-six-variant-set",
        "unique-config-and-receipt-identities",
        "named-compiler-visible-deltas",
        "exact-l6-environment-cartesian-coverage",
        "immutable-ibm-admission-set-bindings",
        "non-config-identity-invariance",
        "current-successful-episode-evidence",
        "weighted-v1-oracle",
        "aa-determinism",
        "no-cross-arm-evidence-reuse",
        "admitted-overlay-bindings",
        "optimizer-acceptance-receipts",
        "authoritative-cleanup",
        "explicit-no-fallback",
    ]
    structurally_valid: Literal[True]


class F4ValidationReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-validation-report.v1"]
    campaign_id: str
    campaign_input_digest: str
    disposition: Literal["structurally-valid"]
    checks: tuple[F4ValidationCheck, ...]
    claim_boundary: Literal[
        "canonical F4 campaign authoring and structural validation for only the exact immutable inputs, six config variants, L6 environments, attempts, and episodes in this manifest; it does not establish IBM target success, F3 authority, learned superiority, benchmark quality, training, scale, production readiness, or external acceptance"
    ]

    _campaign_id = field_validator("campaign_id")(_id_field)
    _input_digest = field_validator("campaign_input_digest")(_digest_field)


class F4ArtifactRef(_ExactModel):
    file_name: Literal["validation-report.json"]
    digest: str
    size_bytes: int = Field(gt=0)
    media_type: Literal[
        "application/vnd.breadboard.rl.phase5-f4-validation-report+json;version=1"
    ]

    _artifact_digest = field_validator("digest")(_digest_field)


class F4CampaignManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-campaign-manifest.v1"]
    campaign_id: str
    campaign_input_digest: str
    campaign: F4CampaignInput
    validation_report: F4ArtifactRef
    claim_boundary: Literal[
        "canonical F4 campaign authoring and structural validation for only the exact immutable inputs, six config variants, L6 environments, attempts, and episodes in this manifest; it does not establish IBM target success, F3 authority, learned superiority, benchmark quality, training, scale, production readiness, or external acceptance"
    ]

    _campaign_id = field_validator("campaign_id")(_id_field)
    _input_digest = field_validator("campaign_input_digest")(_digest_field)


def weighted_v1_oracle(
    *,
    config_set_digest: str,
    selection_nonce: str,
    task_contract_digest: str,
    policy_capability_digest: str,
    candidates: tuple[WeightedCandidate, WeightedCandidate],
) -> tuple[str, VariantId]:
    for value in (
        config_set_digest,
        selection_nonce,
        task_contract_digest,
        policy_capability_digest,
    ):
        _digest_field(value)
    if tuple(candidate.variant_id for candidate in candidates) != tuple(
        sorted(candidate.variant_id for candidate in candidates)
    ):
        raise F4CampaignValidationError(
            "weighted candidates must be sorted by canonical ID"
        )
    if candidates[0].variant_id == candidates[1].variant_id:
        raise F4CampaignValidationError("weighted A/B candidates must be distinct")
    digest = _digest(
        b"bb-weighted-v1\0"
        + config_set_digest.encode("utf-8")
        + selection_nonce.encode("utf-8")
        + task_contract_digest.encode("utf-8")
        + policy_capability_digest.encode("utf-8")
    )
    draw = int(digest[7:], 16) % sum(candidate.weight for candidate in candidates)
    cumulative = 0
    for candidate in candidates:
        cumulative += candidate.weight
        if draw < cumulative:
            return digest, candidate.variant_id
    raise AssertionError("positive weighted candidates must select")


def _variant_maps(
    spec: F4CampaignInput,
) -> tuple[dict[VariantId, ConfigVariant], dict[str, L6Environment]]:
    variants = {variant.variant_id: variant for variant in spec.variants}
    environments = {
        environment.environment_id: environment for environment in spec.environments
    }
    return variants, environments


def _episode_evidence(spec: F4CampaignInput) -> tuple[TargetEpisodeEvidence, ...]:
    return (
        *(row.evidence for row in spec.environment_coverage),
        *(row.evidence for row in spec.variant_episodes),
        *(selection.evidence for selection in spec.weighted_selections),
        *(control.arm_a.evidence for control in spec.aa_determinism_controls),
        *(control.arm_b.evidence for control in spec.aa_determinism_controls),
        *(execution.evidence for execution in spec.admitted_overlay_executions),
        *(accepted.evidence for accepted in spec.accepted_optimizer_variants),
    )


def _require_episode_binding(
    *,
    evidence: TargetEpisodeEvidence,
    variant: ConfigVariant,
    identity: CampaignInvariantIdentity,
    environments: dict[str, L6Environment],
) -> None:
    environment = environments.get(evidence.environment_id)
    if environment is None or evidence.environment_ref != environment.environment_ref:
        raise F4CampaignValidationError("episode environment identity drift")
    if evidence.compiled_config_ref != variant.compiled_config_ref:
        raise F4CampaignValidationError("episode compiled-config identity drift")
    if evidence.admission_receipt_ref != variant.admission_receipt_ref:
        raise F4CampaignValidationError("episode admission-receipt identity drift")
    if evidence.invariant_identity != identity:
        raise F4CampaignValidationError("non-config campaign identity drift")


def _validate_campaign(spec: F4CampaignInput) -> None:
    authority = spec.authority_root
    ibm_root = spec.ibm_admission_set_root
    l6_root = spec.l6_environment_set_root
    if authority.artifact.compiler_identity_ref != spec.compiler_identity_ref:
        raise F4CampaignValidationError(
            "compiler identity does not descend from authority-root contents"
        )
    if (
        ibm_root.artifact.authority_root_ref != authority.ref
        or ibm_root.artifact.compiler_identity_ref
        != authority.artifact.compiler_identity_ref
        or ibm_root.artifact.admission_policy_ref
        != authority.artifact.admission_policy_ref
        or ibm_root.artifact.operator_ceiling_ref
        != authority.artifact.operator_ceiling_ref
        or ibm_root.artifact.runtime_abi != authority.artifact.runtime_abi
        or ibm_root.artifact.validity != authority.artifact.validity
    ):
        raise F4CampaignValidationError(
            "IBM admission-set root does not descend from authority-root contents"
        )
    if l6_root.artifact.authority_root_ref != authority.ref:
        raise F4CampaignValidationError(
            "L6 environment-set root does not descend from authority-root contents"
        )
    if not (
        authority.artifact.validity.not_before
        <= spec.evaluated_at
        < authority.artifact.validity.expires_at
    ):
        raise F4CampaignValidationError(
            "campaign evaluation lies outside authority validity"
        )
    if tuple(l6_root.artifact.environments) != tuple(spec.environments):
        raise F4CampaignValidationError(
            "campaign environments do not equal L6 root contents"
        )
    if tuple(ibm_root.artifact.admission_receipt_refs) != tuple(
        spec.ibm_admission_receipt_refs
    ):
        raise F4CampaignValidationError(
            "campaign admission receipts do not equal IBM root contents"
        )
    if tuple(variant.variant_id for variant in spec.variants) != VARIANT_IDS:
        raise F4CampaignValidationError(
            "campaign requires exactly the ordered closed six-variant set"
        )
    variants, environments = _variant_maps(spec)
    if len(environments) != len(spec.environments):
        raise F4CampaignValidationError("L6 environment IDs must be unique")
    if not any(
        environment.environment_kind == "ibm-one-node"
        for environment in spec.environments
    ):
        raise F4CampaignValidationError(
            "L6 campaign requires an exact IBM one-node environment"
        )

    config_digests = [variant.compiled_config_ref.digest for variant in spec.variants]
    bundle_digests = [variant.config_bundle_ref.digest for variant in spec.variants]
    receipt_digests = [
        variant.admission_receipt_ref.digest for variant in spec.variants
    ]
    display_names = [variant.display_name for variant in spec.variants]
    delta_names = [variant.semantic_delta.name for variant in spec.variants]
    for label, values in (
        ("compiled config", config_digests),
        ("config bundle", bundle_digests),
        ("admission receipt", receipt_digests),
        ("variant display name", display_names),
        ("semantic delta name", delta_names),
    ):
        if len(set(values)) != len(values):
            raise F4CampaignValidationError(f"{label} identities must be unique")
    if any(
        variant.compiler_identity_ref != spec.compiler_identity_ref
        for variant in spec.variants
    ):
        raise F4CampaignValidationError("compiler identity drift across variants")

    ibm_receipts = [ref.digest for ref in spec.ibm_admission_receipt_refs]
    if len(ibm_receipts) != len(set(ibm_receipts)) or set(ibm_receipts) != set(
        receipt_digests
    ):
        raise F4CampaignValidationError(
            "IBM admission-set receipt inventory is not the exact six"
        )

    expected_coverage = {
        (environment.environment_id, variant.variant_id)
        for environment in spec.environments
        for variant in spec.variants
    }
    actual_coverage = {
        (row.environment_id, row.variant_id) for row in spec.environment_coverage
    }
    if (
        len(actual_coverage) != len(spec.environment_coverage)
        or actual_coverage != expected_coverage
    ):
        raise F4CampaignValidationError(
            "environment coverage is missing, duplicated, or representative"
        )
    for row in spec.environment_coverage:
        variant = variants[row.variant_id]
        if row.admission_receipt_ref != variant.admission_receipt_ref:
            raise F4CampaignValidationError(
                "coverage row substitutes another admission receipt"
            )
        if row.evidence.environment_id != row.environment_id:
            raise F4CampaignValidationError(
                "coverage row environment does not match its episode"
            )
        _require_episode_binding(
            evidence=row.evidence,
            variant=variant,
            identity=spec.invariant_identity,
            environments=environments,
        )

    if tuple(row.variant_id for row in spec.variant_episodes) != VARIANT_IDS:
        raise F4CampaignValidationError(
            "same-task campaign requires one ordered episode per variant"
        )
    ibm_environment_ids = {
        environment.environment_id
        for environment in spec.environments
        if environment.environment_kind == "ibm-one-node"
    }
    base_plans: dict[VariantId, ImmutableRef] = {}
    for row in spec.variant_episodes:
        if row.evidence.environment_id not in ibm_environment_ids:
            raise F4CampaignValidationError(
                "same-task variant episode lacks IBM target evidence"
            )
        _require_episode_binding(
            evidence=row.evidence,
            variant=variants[row.variant_id],
            identity=spec.invariant_identity,
            environments=environments,
        )
        base_plans[row.variant_id] = row.evidence.effective_plan_ref
    if len({ref.digest for ref in base_plans.values()}) != len(VARIANT_IDS):
        raise F4CampaignValidationError(
            "variant effective-plan identities must be unique"
        )

    selection_ids: set[str] = set()
    for selection in spec.weighted_selections:
        if selection.selection_id in selection_ids:
            raise F4CampaignValidationError("weighted selection IDs must be unique")
        selection_ids.add(selection.selection_id)
        if (
            selection.task_contract_digest
            != spec.invariant_identity.task_contract_digest
        ):
            raise F4CampaignValidationError("weighted selector task identity drift")
        for candidate in selection.candidates:
            variant = variants[candidate.variant_id]
            if candidate.admission_receipt_ref != variant.admission_receipt_ref:
                raise F4CampaignValidationError(
                    "weighted candidate substitutes an admission receipt"
                )
        oracle_digest, oracle_choice = weighted_v1_oracle(
            config_set_digest=selection.config_set_ref.digest,
            selection_nonce=selection.selection_nonce,
            task_contract_digest=selection.task_contract_digest,
            policy_capability_digest=selection.policy_capability_digest,
            candidates=selection.candidates,
        )
        if (
            selection.oracle_draw_digest != oracle_digest
            or selection.oracle_selected_variant_id != oracle_choice
        ):
            raise F4CampaignValidationError(
                "weighted selection does not match the weighted-v1 oracle"
            )
        selected = variants[oracle_choice]
        _require_episode_binding(
            evidence=selection.evidence,
            variant=selected,
            identity=spec.invariant_identity,
            environments=environments,
        )
        if selection.evidence.environment_id not in ibm_environment_ids:
            raise F4CampaignValidationError(
                "weighted selection lacks IBM target episode evidence"
            )

    control_ids: set[str] = set()
    for control in spec.aa_determinism_controls:
        if control.control_id in control_ids:
            raise F4CampaignValidationError("A/A control IDs must be unique")
        control_ids.add(control.control_id)
        variant = variants[control.variant_id]
        for arm in (control.arm_a, control.arm_b):
            _require_episode_binding(
                evidence=arm.evidence,
                variant=variant,
                identity=spec.invariant_identity,
                environments=environments,
            )
            if arm.evidence.environment_id not in ibm_environment_ids:
                raise F4CampaignValidationError(
                    "A/A control lacks IBM target episode evidence"
                )
        if (
            control.arm_a.deterministic_output_digest
            != control.arm_b.deterministic_output_digest
            or control.arm_a.evidence.effective_plan_ref
            != control.arm_b.evidence.effective_plan_ref
        ):
            raise F4CampaignValidationError("A/A deterministic controls disagree")

    overlay_ids: set[str] = set()
    overlay_receipts: set[str] = set()
    for execution in spec.admitted_overlay_executions:
        if execution.overlay_execution_id in overlay_ids:
            raise F4CampaignValidationError("overlay execution IDs must be unique")
        overlay_ids.add(execution.overlay_execution_id)
        if execution.overlay_admission_receipt_ref.digest in overlay_receipts:
            raise F4CampaignValidationError(
                "overlay admission receipts must be immutable and unique"
            )
        overlay_receipts.add(execution.overlay_admission_receipt_ref.digest)
        if len({ref.digest for ref in execution.ordered_overlay_refs}) != len(
            execution.ordered_overlay_refs
        ):
            raise F4CampaignValidationError(
                "ordered overlay references contain a duplicate"
            )
        variant = variants[execution.base_variant_id]
        _require_episode_binding(
            evidence=execution.evidence,
            variant=variant,
            identity=spec.invariant_identity,
            environments=environments,
        )
        if (
            execution.evidence.effective_plan_ref
            == base_plans[execution.base_variant_id]
        ):
            raise F4CampaignValidationError(
                "admitted overlay did not produce a distinct effective plan"
            )
        if execution.evidence.environment_id not in ibm_environment_ids:
            raise F4CampaignValidationError(
                "overlay execution lacks IBM target episode evidence"
            )

    if spec.optimizer_disposition == "no_variant_accepted":
        if (
            spec.accepted_optimizer_variants
            or spec.optimized_config_set_ref is not None
        ):
            raise F4CampaignValidationError(
                "no_variant_accepted must publish neither accepted variants nor optimized set"
            )
    else:
        if (
            not spec.accepted_optimizer_variants
            or spec.optimized_config_set_ref is None
        ):
            raise F4CampaignValidationError(
                "accepted optimizer variants require receipts and a new immutable config set"
            )
    accepted_ids: set[str] = set()
    accepted_variants: set[VariantId] = set()
    for accepted in spec.accepted_optimizer_variants:
        if accepted.optimizer_acceptance_id in accepted_ids:
            raise F4CampaignValidationError("optimizer acceptance IDs must be unique")
        accepted_ids.add(accepted.optimizer_acceptance_id)
        if accepted.variant_id in accepted_variants:
            raise F4CampaignValidationError("optimizer variant accepted more than once")
        accepted_variants.add(accepted.variant_id)
        candidate = variants[accepted.variant_id]
        parent = variants[accepted.parent_variant_id]
        if (
            not candidate.optimizer_generated
            or candidate.compiled_config_ref == parent.compiled_config_ref
        ):
            raise F4CampaignValidationError(
                "accepted optimizer candidate provenance is invalid"
            )
        expected_source_closure = (
            candidate.config_bundle_ref,
            candidate.dependency_closure_ref,
            candidate.compiler_identity_ref,
            candidate.compiled_config_ref,
            candidate.admission_receipt_ref,
        )
        for binding in accepted.optimizer_work_packet.artifact.ordered_receipts:
            receipt = binding.artifact
            if (
                receipt.config_bundle_ref,
                receipt.dependency_closure_ref,
                receipt.compiler_identity_ref,
                receipt.compiled_config_ref,
                receipt.admission_receipt_ref,
            ) != expected_source_closure:
                raise F4CampaignValidationError(
                    "optimizer receipt body does not join rebuilt source closure"
                )
        _require_episode_binding(
            evidence=accepted.evidence,
            variant=candidate,
            identity=spec.invariant_identity,
            environments=environments,
        )
        if accepted.evidence.environment_id not in ibm_environment_ids:
            raise F4CampaignValidationError(
                "accepted optimizer variant lacks IBM target evidence"
            )

    all_evidence = _episode_evidence(spec)
    for field_name in (
        "target_attempt_id",
        "episode_id",
        "target_attempt_output_ref",
        "episode_output_ref",
    ):
        values = [
            getattr(evidence, field_name).digest
            if isinstance(getattr(evidence, field_name), ImmutableRef)
            else getattr(evidence, field_name)
            for evidence in all_evidence
        ]
        if len(values) != len(set(values)):
            raise F4CampaignValidationError(
                f"cross-arm reuse detected for target evidence field {field_name}"
            )
    target_report_digests = [binding.ref.digest for binding in spec.target_reports]
    if len(target_report_digests) != len(set(target_report_digests)):
        raise F4CampaignValidationError("target report references must be unique")
    report_by_digest = {binding.ref.digest: binding for binding in spec.target_reports}
    receipt_by_episode: dict[str, tuple[str, F4TargetExecutionReceipt]] = {}
    for binding in spec.target_reports:
        for receipt in binding.artifact.executions:
            if receipt.episode_id in receipt_by_episode:
                raise F4CampaignValidationError(
                    "target reports duplicate an episode execution"
                )
            receipt_by_episode[receipt.episode_id] = (binding.ref.digest, receipt)
    episodes = _episode_evidence(spec)
    if set(receipt_by_episode) != {evidence.episode_id for evidence in episodes}:
        raise F4CampaignValidationError(
            "target reports do not cover exactly every campaign execution"
        )
    variants_by_compiled = {
        variant.compiled_config_ref.digest: variant for variant in spec.variants
    }
    for evidence in episodes:
        report_digest, receipt = receipt_by_episode[evidence.episode_id]
        variant = variants_by_compiled.get(evidence.compiled_config_ref.digest)
        if (
            evidence.target_report_ref.digest != report_digest
            or report_digest not in report_by_digest
            or variant is None
            or receipt.target_attempt_id != evidence.target_attempt_id
            or receipt.variant_id != variant.variant_id
            or receipt.environment_id != evidence.environment_id
            or receipt.environment_ref != evidence.environment_ref
            or receipt.source_runtime_ref
            != report_by_digest[report_digest].artifact.source_runtime_ref
            or receipt.invariant_identity != evidence.invariant_identity
            or receipt.target_attempt_output_ref != evidence.target_attempt_output_ref
            or receipt.episode_output_ref != evidence.episode_output_ref
            or receipt.target_report_output_ref != evidence.target_report_output_ref
            or receipt.compiled_config_ref != evidence.compiled_config_ref
            or receipt.admission_receipt_ref != evidence.admission_receipt_ref
            or receipt.effective_plan_ref != evidence.effective_plan_ref
            or receipt.evidence_manifest_ref != evidence.evidence_manifest_ref
            or receipt.completed_envelope_ref != evidence.completed_envelope_ref
            or receipt.closed_envelope_ref != evidence.closed_envelope_ref
            or receipt.tool_call_receipt_refs != evidence.tool_call_receipt_refs
            or receipt.server_verifier_result_ref != evidence.server_verifier_result_ref
            or receipt.verifier_passed is not evidence.verifier_passed
            or receipt.reward != evidence.reward
            or receipt.cleanup_receipt_ref != evidence.cleanup.cleanup_receipt_ref
            or receipt.terminal_outcome != evidence.terminal_outcome
        ):
            raise F4CampaignValidationError(
                "campaign execution does not match its source-backed target receipt"
            )


def validate_f4_campaign(spec: F4CampaignInput) -> F4ValidationReport:
    if type(spec) is not F4CampaignInput:
        raise TypeError("spec must be an exact F4CampaignInput")
    _validate_campaign(spec)
    input_bytes = canonical_json_bytes(spec.model_dump(mode="json"))
    return F4ValidationReport(
        schema_version="bb.rl.phase5-f4-validation-report.v1",
        campaign_id=spec.campaign_id,
        campaign_input_digest=_digest(input_bytes),
        disposition="structurally-valid",
        checks=tuple(
            F4ValidationCheck(check_id=check_id, structurally_valid=True)
            for check_id in _CHECKS
        ),
        claim_boundary=CLAIM_BOUNDARY,
    )


def _read_canonical_input(path: str) -> bytes:
    source = Path(path)
    if not source.is_absolute() or os.path.normpath(path) != path:
        raise F4CampaignValidationError("input path must be absolute and normalized")
    try:
        mode = source.lstat().st_mode
    except FileNotFoundError as exc:
        raise F4CampaignValidationError("input file is missing") from exc
    if not stat.S_ISREG(mode) or source.is_symlink():
        raise F4CampaignValidationError("input must be a regular non-symlink file")
    raw = source.read_bytes()
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F4CampaignValidationError("input must be UTF-8 JSON") from exc
    if canonical_json_bytes(parsed) != raw:
        raise F4CampaignValidationError("input must be canonical JSON")
    return raw


def _write_exclusive(directory_fd: int, name: str, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short campaign artifact write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def build_f4_campaign(spec: F4CampaignInput, output_dir: str) -> str:
    if type(spec) is not F4CampaignInput:
        raise TypeError("spec must be an exact F4CampaignInput")
    destination = Path(output_dir)
    if not destination.is_absolute() or os.path.normpath(output_dir) != output_dir:
        raise F4CampaignValidationError(
            "output directory must be absolute and normalized"
        )
    if os.path.lexists(destination):
        raise F4CampaignValidationError("output directory already exists")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = destination.parent / f".{destination.name}.authoring-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        input_bytes = canonical_json_bytes(spec.model_dump(mode="json"))
        report = validate_f4_campaign(spec)
        report_bytes = canonical_json_bytes(report.model_dump(mode="json"))
        manifest = F4CampaignManifest(
            schema_version="bb.rl.phase5-f4-campaign-manifest.v1",
            campaign_id=spec.campaign_id,
            campaign_input_digest=_digest(input_bytes),
            campaign=spec,
            validation_report=F4ArtifactRef(
                file_name="validation-report.json",
                digest=_digest(report_bytes),
                size_bytes=len(report_bytes),
                media_type="application/vnd.breadboard.rl.phase5-f4-validation-report+json;version=1",
            ),
            claim_boundary=CLAIM_BOUNDARY,
        )
        manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
        directory_fd = os.open(
            staging,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            _write_exclusive(directory_fd, "validation-report.json", report_bytes)
            _write_exclusive(directory_fd, "manifest.json", manifest_bytes)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.rename(staging, destination)
        parent_fd = os.open(
            destination.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return os.fspath((destination / "manifest.json").resolve())
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def author_f4_campaign(input_path: str, output_dir: str) -> str:
    raw = _read_canonical_input(input_path)
    try:
        spec = F4CampaignInput.model_validate_json(raw, strict=True)
    except ValueError as exc:
        raise F4CampaignValidationError(str(exc)) from exc
    if canonical_json_bytes(spec.model_dump(mode="json")) != raw:
        raise F4CampaignValidationError(
            "input does not round-trip as the canonical closed model"
        )
    return build_f4_campaign(spec, output_dir)


__all__ = [
    "AADeterminismControl",
    "AAArm",
    "AcceptedOptimizerVariant",
    "AdmittedOverlayExecution",
    "CLAIM_BOUNDARY",
    "CampaignInvariantIdentity",
    "CleanupEvidence",
    "CompilerVisibleSemanticDelta",
    "ConfigVariant",
    "EnvironmentCoverageRow",
    "F4ArtifactRef",
    "F4CampaignInput",
    "F4CampaignManifest",
    "F4CampaignValidationError",
    "F4ValidationCheck",
    "F4ValidationReport",
    "ImmutableRef",
    "L6Environment",
    "TargetEpisodeEvidence",
    "VARIANT_IDS",
    "VariantEpisodeRow",
    "WeightedCandidate",
    "WeightedSelection",
    "author_f4_campaign",
    "build_f4_campaign",
    "validate_f4_campaign",
    "weighted_v1_oracle",
]
