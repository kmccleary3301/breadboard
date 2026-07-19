from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import hmac
import os
import socket
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_coder_prototype.compilation.contracts import (
    COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
    canonical_json_bytes,
    canonical_json_loads,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    CompositionRefV1,
    CompositionRefV2,
    HarnessCompositionManifestV1,
    HarnessCompositionManifestV2,
    ProductionCleanupInventory,
    load_production_composition,
)
from breadboard.rl.phase5.f4_campaign import (
    CampaignInvariantIdentity,
    CompilerVisibleSemanticDelta,
    F4TargetEvidenceReport,
    F4TargetEvidenceReportBinding,
    F4TargetExecutionReceipt,
    ImmutableRef,
)


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
_REPORT_NAME = "f4-target-canaries.report.json"
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class F4TargetCanaryError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: str) -> str:
    if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError("lowercase sha256 digest required")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError("lowercase sha256 digest required") from exc
    if value != value.lower():
        raise ValueError("lowercase sha256 digest required")
    return value


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise ValueError("bounded canonical identifier required")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("path must be absolute and normalized")
    return value


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _wire(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _wire(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(child) for child in value]
    return value


class F4ProductionBinding(_ExactModel):
    composition_ref_path: str
    composition_descriptor_ref: ImmutableRef
    composition_manifest_ref: ImmutableRef
    authority_bundle_ref: ImmutableRef
    secret_files: dict[str, str]

    _path = field_validator("composition_ref_path")(_absolute)

    @field_validator("secret_files")
    @classmethod
    def exact_secret_paths(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or any(type(key) is not str or not key for key in value):
            raise ValueError("composition secret handle map must be nonempty")
        normalized = {key: _absolute(path) for key, path in value.items()}
        if len(set(normalized.values())) != len(normalized):
            raise ValueError("composition secret file reuse is forbidden")
        return normalized


class F4TargetIdentity(_ExactModel):
    target_run_id: str
    target_job_id: str
    target_node_id: str

    _ids = field_validator("target_run_id", "target_job_id", "target_node_id")(
        _identifier
    )


class F4TargetExecutionAuthority(_ExactModel):
    environment_id: str
    environment_ref: ImmutableRef
    source_runtime_ref: ImmutableRef
    composition_ref: ImmutableRef
    runtime_class: Literal["docker"]
    python_executable: str
    docker_socket_path: str
    workspace_root: str
    docker_image: str
    service_factory: Literal["production-composition"]

    _environment_id = field_validator("environment_id")(_identifier)
    _paths = field_validator(
        "python_executable",
        "docker_socket_path",
        "workspace_root",
    )(_absolute)


class F4VariantExecution(_ExactModel):
    variant_id: VariantId
    request: c.ResolveEpisodeRequest
    config_bundle_ref: ImmutableRef
    dependency_closure_ref: ImmutableRef
    compiler_identity_ref: ImmutableRef
    compiled_config_ref: ImmutableRef
    compiled_semantics_ref: c.ArtifactRef
    admission_receipt_ref: ImmutableRef
    selection_record_ref: ImmutableRef
    ordered_overlay_receipt_refs: tuple[ImmutableRef, ...]
    semantic_delta: CompilerVisibleSemanticDelta

    @model_validator(mode="after")
    def weighted_request(self) -> "F4VariantExecution":
        if not isinstance(self.request.selector, c.WeightedSelectorRef):
            raise ValueError(
                "F4 target canaries require the production weighted selector"
            )
        if self.request.selection_nonce is None:
            raise ValueError("F4 target canary selection seed is missing")
        if not self.ordered_overlay_receipt_refs:
            raise ValueError("F4 designated A/B arm requires an admitted overlay")
        if len({ref.digest for ref in self.ordered_overlay_receipt_refs}) != len(
            self.ordered_overlay_receipt_refs
        ):
            raise ValueError("ordered overlay receipt refs contain a duplicate")
        return self

    requested_security_policy_digest: str

    _security_policy_digest = field_validator("requested_security_policy_digest")(
        _digest
    )


class F4TargetCanaryInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-target-canary-input.v1"]
    production: F4ProductionBinding
    target: F4TargetIdentity
    execution_authority: F4TargetExecutionAuthority
    invariant_identity: CampaignInvariantIdentity
    variants: tuple[
        F4VariantExecution,
        F4VariantExecution,
        F4VariantExecution,
        F4VariantExecution,
        F4VariantExecution,
        F4VariantExecution,
    ]
    task_input: dict[str, Any]
    run_context: dict[str, Any]
    output_dir: str

    _output = field_validator("output_dir")(_absolute)

    @model_validator(mode="after")
    def closed_campaign(self) -> "F4TargetCanaryInput":
        if tuple(variant.variant_id for variant in self.variants) != VARIANT_IDS:
            raise ValueError(
                "F4 target canaries require the frozen ordered six-variant set"
            )
        if len({variant.request.episode_id for variant in self.variants}) != len(
            VARIANT_IDS
        ):
            raise ValueError("F4 target episode IDs must be unique")
        for variant in self.variants:
            if (
                variant.request.task.canonical_digest()
                != self.invariant_identity.task_contract_digest
            ):
                raise ValueError("variant request task contract identity drift")
        for label, values in (
            (
                "config bundle",
                [item.config_bundle_ref.digest for item in self.variants],
            ),
            (
                "dependency closure",
                [item.dependency_closure_ref.digest for item in self.variants],
            ),
            (
                "compiled config",
                [item.compiled_config_ref.digest for item in self.variants],
            ),
            (
                "selection record",
                [item.selection_record_ref.digest for item in self.variants],
            ),
        ):
            if len(set(values)) != len(VARIANT_IDS):
                raise ValueError(
                    f"{label} identities must be unique across the six canaries"
                )
        compiler_refs = {item.compiler_identity_ref.digest for item in self.variants}
        if len(compiler_refs) != 1:
            raise ValueError("compiler identity drift across F4 variants")
        return self


class F4EvidenceObject(_ExactModel):
    schema_version: Literal["bb.rl.evidence-object.v2"]
    role: str
    producer: str
    artifact_ref: c.ArtifactRef
    authorization_policy_ref: str
    retention_policy_ref: str
    parent_digests: tuple[str, ...]


class F4ArtifactManifest(_ExactModel):
    schema_version: Literal["bb.rl.artifact-manifest.v2"]
    objects: tuple[F4EvidenceObject, ...]
    allowed_roles: tuple[str, ...]
    max_each_bytes: int
    max_total_bytes: int
    required_roles: tuple[str, ...]
    total_byte_count: int


class F4ExecutionEvidenceManifest(_ExactModel):
    schema_version: Literal["bb.rl.execution-evidence-manifest.v2"]
    episode_id: str
    resolved_plan_digest: str
    selection_digest: str
    effective_plan_digest: str
    policy_binding_digest: str
    runner_ledger_ref: c.ArtifactRef
    materialization_digest: str
    primary_measurement_digest: str | None
    verifier_snapshot_digest: str | None
    task_input_digest: str
    run_context_digest: str
    target_identity: F4TargetIdentity
    verifier_measurement_digest: str | None
    verifier_result_digest: str | None
    artifact_manifest_ref: c.ArtifactRef
    primary_disposition: str
    reward_disposition: str
    reward_components: dict[str, Any]
    evidence_policy_ref: str
    retention_policy_ref: str
    lineage_nodes: tuple[dict[str, Any], ...]
    lineage_root: str
    verifier_cleanup_receipt_ref: c.ArtifactRef | None
    verifier_cleanup_lease_id: str | None
    retention_policy_record_ref: c.ArtifactRef | None
    primary_failure_digest: str | None
    authority_access_ledger_ref: c.ArtifactRef | None
    authority_canary_reads: tuple[str, ...]
    authority_cross_episode_reads: tuple[str, ...]


class F4CompletedEnvelope(_ExactModel):
    schema_version: Literal["bb.rl.completed-episode-envelope.v2"]
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    create_response_ref: c.ArtifactRef
    run_response_ref: c.ArtifactRef
    evidence_manifest_ref: c.ArtifactRef
    evidence_root: str
    primary_outcome: str
    completed_event_ref: c.ArtifactRef
    completed_event_head: str
    subject_digest: str
    cleanup_disposition: Literal["pending"]
    _subject_digest = field_validator("subject_digest")(_digest)


class F4ClosedEnvelope(_ExactModel):
    schema_version: Literal["bb.rl.closed-episode-envelope.v2"]
    episode_id: str
    completed_envelope_ref: c.ArtifactRef
    cleanup_receipt_digest: str | None
    cleanup_receipt: dict[str, Any] | None
    reconciliation_event_ref: c.ArtifactRef
    reconciliation_event_head: str
    primary_outcome: str
    cleanup_required_resources: tuple[str, ...]
    verifier_cleanup_receipt_digest: str | None
    verifier_cleanup_receipt: dict[str, Any] | None
    verifier_cleanup_required_resources: tuple[str, ...]
    export_authorization_refs: tuple[c.ArtifactRef, ...]
    redaction_decision_refs: tuple[c.ArtifactRef, ...]
    cleanup_disposition: Literal["released"]


class F4PolicyObservationEvidence(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-policy-observation.v1"]
    episode_id: str
    effective_plan_digest: str
    policy_observation_digest: str
    call_id: str

    _ids = field_validator("episode_id", "call_id")(_identifier)
    _digests = field_validator(
        "effective_plan_digest", "policy_observation_digest"
    )(_digest)


class F4PolicyCallReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-policy-call-receipt.v1"]
    episode_id: str
    effective_plan_digest: str
    policy_observation_digest: str
    call_id: str
    tool_id: str
    implementation_digest: str
    decision: Literal["allowed"]

    _ids = field_validator("episode_id", "call_id", "tool_id")(_identifier)
    _digests = field_validator(
        "effective_plan_digest",
        "policy_observation_digest",
        "implementation_digest",
    )(_digest)


class F4RunnerLedgerEvent(_ExactModel):
    event_index: int = Field(ge=0)
    episode_id: str
    effective_plan_digest: str
    call_id: str
    policy_call_digest: str
    policy_call_receipt_ref: c.ArtifactRef
    policy_observation_digest: str
    policy_observation_ref: c.ArtifactRef
    tool_id: str
    implementation_digest: str
    exit_code: Literal[0]
    output_digest: str

    _ids = field_validator("episode_id", "call_id", "tool_id")(_identifier)
    _digests = field_validator(
        "effective_plan_digest",
        "policy_call_digest",
        "policy_observation_digest",
        "implementation_digest",
        "output_digest",
    )(_digest)


class F4RunnerLedger(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-runner-ledger.v1"]
    episode_id: str
    events: tuple[F4RunnerLedgerEvent, ...] = Field(min_length=1)

    _episode_id = field_validator("episode_id")(_identifier)

    @model_validator(mode="after")
    def ordered_events(self) -> "F4RunnerLedger":
        if tuple(event.event_index for event in self.events) != tuple(
            range(len(self.events))
        ):
            raise ValueError("runner ledger event order is not contiguous")
        if any(event.episode_id != self.episode_id for event in self.events):
            raise ValueError("runner ledger event episode drift")
        if len({event.call_id for event in self.events}) != len(self.events):
            raise ValueError("runner ledger call IDs must be unique")
        return self


class F4ToolCallReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-tool-call-receipt.v1"]
    episode_id: str
    effective_plan_digest: str
    policy_call_digest: str
    policy_observation_digest: str
    call_id: str
    tool_id: str
    implementation_digest: str
    exit_code: Literal[0]
    output_digest: str

    _ids = field_validator("episode_id", "call_id", "tool_id")(_identifier)
    _digests = field_validator(
        "effective_plan_digest",
        "policy_observation_digest",
        "policy_call_digest",
        "implementation_digest",
        "output_digest",
    )(_digest)


class F4VerifierResultReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-verifier-result-receipt.v1"]
    episode_id: str
    effective_plan_digest: str
    verifier_implementation_digest: str
    verifier_measurement_digest: str
    output_digest: str
    passed: Literal[True]
    reward: Literal[1]
    reward_components: dict[str, Any]

    _episode_id = field_validator("episode_id")(_identifier)
    _digests = field_validator(
        "effective_plan_digest",
        "verifier_implementation_digest",
        "verifier_measurement_digest",
        "output_digest",
    )(_digest)


class F4RunExecutionReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-run-execution-receipt.v1"]
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    primary_disposition: Literal["succeeded"]
    response: dict[str, Any]
    termination: str
    turn_count: int = Field(ge=0)
    reward: Literal[1]
    reward_components: dict[str, Any]
    primary_measurement_digest: str
    verifier_result_digest: str
    verifier_measurement_digest: str

    _episode_id = field_validator("episode_id")(_identifier)
    _digests = field_validator(
        "create_fingerprint",
        "run_fingerprint",
        "primary_measurement_digest",
        "verifier_result_digest",
        "verifier_measurement_digest",
    )(_digest)


class F4ToolCallReceiptBinding(_ExactModel):
    ref: c.ArtifactRef
    artifact: F4ToolCallReceipt


class F4VerifierResultReceiptBinding(_ExactModel):
    ref: c.ArtifactRef
    artifact: F4VerifierResultReceipt


class F4LifecycleEvidence(_ExactModel):
    completed_envelope_ref: c.ArtifactRef
    closed_envelope_ref: c.ArtifactRef
    evidence_manifest_ref: c.ArtifactRef
    artifact_manifest_ref: c.ArtifactRef
    primary_measurement_digest: str
    verifier_measurement_digest: str
    verifier_result_digest: str
    cleanup_receipt_digest: str
    tool_call_receipts: tuple[F4ToolCallReceiptBinding, ...] = Field(min_length=1)
    verifier_result: F4VerifierResultReceiptBinding


class F4SecurityPolicyObservation(_ExactModel):
    runtime: str
    runtime_class: str
    runtime_binary_digest: str
    image_digest: str
    security_policy_digest: str
    network_policy_digest: str
    verifier_digest: str
    materialization_plan_digest: str
    create_fingerprint: str
    effective_plan_digest: str

    _digests = field_validator(
        "runtime_binary_digest",
        "image_digest",
        "security_policy_digest",
        "network_policy_digest",
        "verifier_digest",
        "materialization_plan_digest",
        "create_fingerprint",
        "effective_plan_digest",
    )(_digest)


class F4CleanupObservation(_ExactModel):
    active_lease_ids: tuple[str, ...]
    orphan_resource_ids: tuple[str, ...]
    leaked_artifact_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]
    container_ids: tuple[str, ...]
    process_ids: tuple[int, ...]
    cgroup_paths: tuple[str, ...]
    mount_paths: tuple[str, ...]
    workspace_paths: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    secret_lease_ids: tuple[str, ...]
    inventory_digest: str
    _inventory_digest = field_validator("inventory_digest")(_digest)

    @model_validator(mode="after")
    def content_addressed_inventory(self) -> "F4CleanupObservation":
        inventory = ProductionCleanupInventory(
            active_lease_ids=self.active_lease_ids,
            orphan_resource_ids=self.orphan_resource_ids,
            leaked_artifact_ids=self.leaked_artifact_ids,
            cleanup_errors=self.cleanup_errors,
            container_ids=self.container_ids,
            process_ids=self.process_ids,
            cgroup_paths=self.cgroup_paths,
            mount_paths=self.mount_paths,
            workspace_paths=self.workspace_paths,
            artifact_paths=self.artifact_paths,
            secret_lease_ids=self.secret_lease_ids,
            broker_descriptor_count=self.broker_descriptor_count,
        )
        broker_ref = (
            None
            if self.broker_close_receipt_ref is None
            else self.broker_close_receipt_ref.model_dump(mode="json")
        )
        if inventory.canonical_digest(broker_ref) != self.inventory_digest:
            raise ValueError("cleanup inventory content digest mismatch")
        return self
    broker_descriptor_count: int = Field(ge=0)
    broker_close_receipt_ref: ImmutableRef | None


def _require_clean(observation: F4CleanupObservation) -> None:
    if (
        observation.active_lease_ids
        or observation.orphan_resource_ids
        or observation.leaked_artifact_ids
        or observation.cleanup_errors
        or observation.container_ids
        or observation.process_ids
        or observation.cgroup_paths
        or observation.mount_paths
        or observation.workspace_paths
        or observation.artifact_paths
        or observation.secret_lease_ids
        or observation.broker_descriptor_count != 0
        or observation.broker_close_receipt_ref is None
    ):
        raise F4TargetCanaryError(
            "target cleanup inventory contains a live resource, descriptor, leak, or error"
        )


class F4VariantTargetReport(_ExactModel):
    variant_id: VariantId
    episode_id: str
    compiled_manifest_digest: str
    config_bundle_digest: str
    dependency_closure_digest: str
    compiler_identity_digest: str
    admission_receipt_digest: str
    selection: dict[str, Any]
    overlay_order: tuple[str, ...]
    effective_plan_ref: dict[str, Any]
    effective_plan_digest: str
    target_identity: F4TargetIdentity
    lifecycle: F4LifecycleEvidence
    verifier: dict[str, Any]
    invariant_identity: CampaignInvariantIdentity
    requested_security_policy_digest: str
    security_policy_observation: F4SecurityPolicyObservation
    non_config_invariants_preserved: Literal[True]
    fallback_used: Literal[False]

    _digests = field_validator(
        "compiled_manifest_digest",
        "config_bundle_digest",
        "dependency_closure_digest",
        "compiler_identity_digest",
        "admission_receipt_digest",
        "effective_plan_digest",
    )(_digest)

    @model_validator(mode="after")
    def successful_closed_target(self) -> "F4VariantTargetReport":
        reward = self.verifier.get("reward")
        if (
            self.verifier.get("passed") is not True
            or isinstance(reward, bool)
            or reward != 1
        ):
            raise ValueError("F4 target verifier did not pass with exact reward 1")
        if (
            self.requested_security_policy_digest
            != self.security_policy_observation.security_policy_digest
            or self.effective_plan_digest
            != self.security_policy_observation.effective_plan_digest
        ):
            raise ValueError("F4 target security-policy measurement mismatch")
        if self.selection.get("persisted_before_run") is not True:
            raise ValueError("F4 target selection was not persisted before execution")
        return self


class F4TargetCanaryReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-target-canary-report.v1"]
    input_digest: str
    production: dict[str, Any]
    target: F4TargetIdentity
    execution_authority: F4TargetExecutionAuthority
    variants: tuple[
        F4VariantTargetReport,
        F4VariantTargetReport,
        F4VariantTargetReport,
        F4VariantTargetReport,
        F4VariantTargetReport,
        F4VariantTargetReport,
    ]
    cleanup: F4CleanupObservation
    cleanup_complete: Literal[True]
    no_orphan_resources: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]

    @model_validator(mode="after")
    def source_closed_outputs(self) -> "F4TargetCanaryReport":
        if any(row.target_identity != self.target for row in self.variants):
            raise ValueError("F4 target identity drift across variant receipts")
        output_digests = [
            binding.artifact.output_digest
            for row in self.variants
            for binding in row.lifecycle.tool_call_receipts
        ] + [
            row.lifecycle.verifier_result.artifact.output_digest
            for row in self.variants
        ]
        if len(output_digests) != len(set(output_digests)):
            raise ValueError("F4 target canary report reuses an output artifact")
        return self

    _input_digest = field_validator("input_digest")(_digest)

class F4ProductionLoaderReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-production-loader-receipt.v1"]
    input_digest: str
    production: F4ProductionBinding
    target: F4TargetIdentity
    _input_digest = field_validator("input_digest")(_digest)


class F4TargetExecutorReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-target-executor-receipt.v1"]
    loader_receipt_digest: str
    report_digest: str
    target: F4TargetIdentity
    _digests = field_validator("loader_receipt_digest", "report_digest")(_digest)
class F4CampaignConversionAuthority(_ExactModel):
    schema_version: Literal[
        "bb.rl.phase5-f4-campaign-conversion-authority.v1"
    ]
    loader_receipt_digest: str
    executor_receipt_digest: str
    report_digest: str
    target: F4TargetIdentity
    signer_key_id: str = Field(min_length=1, max_length=256)
    signature_algorithm: Literal["hmac-sha256-v1"]
    signed_payload_digest: str
    signature: str = Field(pattern=r"[0-9a-f]{64}")

    _digests = field_validator(
        "loader_receipt_digest",
        "executor_receipt_digest",
        "report_digest",
        "signed_payload_digest",
    )(_digest)

    @staticmethod
    def unsigned_canonical_bytes_from_wire(
        value: Mapping[str, Any],
    ) -> bytes:
        unsigned = dict(value)
        unsigned.pop("signed_payload_digest", None)
        unsigned.pop("signature", None)
        return canonical_json_bytes(unsigned)

    def unsigned_canonical_bytes(self) -> bytes:
        return self.unsigned_canonical_bytes_from_wire(
            self.model_dump(mode="json")
        )

    @model_validator(mode="after")
    def exact_signed_payload(self) -> "F4CampaignConversionAuthority":
        if _sha256(self.unsigned_canonical_bytes()) != self.signed_payload_digest:
            raise ValueError(
                "campaign conversion authority signed payload digest mismatch"
            )
        return self


_CAMPAIGN_AUTHORITY_MEDIA_TYPE = (
    "application/vnd.breadboard.phase5-f4-campaign-conversion-authority+json;"
    "version=1"
)




class F4TargetCanaryTestRunResult(_ExactModel):
    report: F4TargetCanaryReport
    report_path: str
    _path = field_validator("report_path")(_absolute)


class F4TargetCanaryRunResult(_ExactModel):
    report: F4TargetCanaryReport
    report_path: str
    production_loader_receipt: F4ProductionLoaderReceipt
    target_executor_receipt: F4TargetExecutorReceipt
    campaign_authority_ref: c.ArtifactRef
    campaign_authority: F4CampaignConversionAuthority


    _path = field_validator("report_path")(_absolute)

    @model_validator(mode="after")
    def authenticated_execution(self) -> "F4TargetCanaryRunResult":
        loader_digest = _sha256(
            canonical_json_bytes(
                self.production_loader_receipt.model_dump(mode="json")
            )
        )
        report_digest = _sha256(
            canonical_json_bytes(self.report.model_dump(mode="json"))
        )
        production = self.production_loader_receipt.production
        expected_production = {
            "composition_descriptor_ref": (
                production.composition_descriptor_ref.model_dump(mode="json")
            ),
            "composition_manifest_ref": (
                production.composition_manifest_ref.model_dump(mode="json")
            ),
            "authority_bundle_ref": production.authority_bundle_ref.model_dump(
                mode="json"
            ),
        }
        if (
            self.target_executor_receipt.loader_receipt_digest != loader_digest
            or self.target_executor_receipt.report_digest != report_digest
            or self.production_loader_receipt.target != self.report.target
            or self.target_executor_receipt.target != self.report.target
            or self.production_loader_receipt.input_digest
            != self.report.input_digest
            or self.report.production != expected_production
        ):
            raise ValueError("production loader/target executor receipt mismatch")
        authority_raw = canonical_json_bytes(
            self.campaign_authority.model_dump(mode="json")
        )
        if (
            self.campaign_authority_ref.sha256 != _sha256(authority_raw)
            or self.campaign_authority_ref.size_bytes != len(authority_raw)
            or self.campaign_authority_ref.media_type
            != _CAMPAIGN_AUTHORITY_MEDIA_TYPE
            or self.campaign_authority.loader_receipt_digest != loader_digest
            or self.campaign_authority.executor_receipt_digest
            != _sha256(
                canonical_json_bytes(
                    self.target_executor_receipt.model_dump(mode="json")
                )
            )
            or self.campaign_authority.report_digest != report_digest
            or self.campaign_authority.target != self.report.target
        ):
            raise ValueError("campaign conversion authority receipt mismatch")
        return self




class F4TargetComponentEnvelope(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-target-component-report.v1"]
    report_id: Literal["f4-target-canaries"]
    component: Literal["rl_phase5_f4_target_canaries"]
    passed: Literal[True]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    report_sha256: str
    report_path: str
    summary: dict[str, Any]

    _report_digest = field_validator("report_sha256")(_digest)
    _report_path = field_validator("report_path")(_absolute)

    @model_validator(mode="after")
    def exact_bounded_summary(self) -> "F4TargetComponentEnvelope":
        expected = {
            "variant_count": 6,
            "variant_order": list(VARIANT_IDS),
            "fresh_selection_receipts": True,
            "exact_non_config_invariants": True,
            "cleanup_complete": True,
            "no_orphan_resources": True,
            "unexpected_outcomes": [],
        }
        if self.summary != expected:
            raise ValueError(
                "passing F4 component envelope summary is not the exact bounded gate"
            )
        return self


class F4TargetRuntime(Protocol):
    composition_descriptor_digest: str
    composition_manifest_digest: str
    authority_bundle_digest: str
    target_identity: F4TargetIdentity
    service: Any

    def load_artifact(self, ref: c.ArtifactRef, kind: c.ArtifactKind) -> bytes: ...

    def load_evidence(self, ref: c.ArtifactRef) -> bytes: ...
    def cleanup_observation(self) -> F4CleanupObservation: ...
    async def close(self) -> None: ...



class _ProductionRuntime:
    def __init__(
        self,
        *,
        composition: Any,
        production: F4ProductionBinding,
        target_identity: F4TargetIdentity,
        lease_root: str,
    ) -> None:
        self._composition = composition
        self._lease_root = lease_root
        self.service = composition.app.state.episode_service
        self.composition_descriptor_digest = (
            production.composition_descriptor_ref.digest
        )
        self.composition_manifest_digest = composition.manifest.input_manifest_digest
        self.authority_bundle_digest = composition.manifest.authority_bundle_digest
        self.target_identity = target_identity

    def load_artifact(self, ref: c.ArtifactRef, kind: c.ArtifactKind) -> bytes:
        return self._composition.authority_graph.store.load(
            ref.sha256, kind=kind, max_bytes=_MAX_ARTIFACT_BYTES
        )

    def publish_campaign_authority(
        self,
        loader: F4ProductionLoaderReceipt,
        executor: F4TargetExecutorReceipt,
        report: F4TargetCanaryReport,
    ) -> tuple[c.ArtifactRef, F4CampaignConversionAuthority]:
        authenticator = self._composition.authority_graph.authenticator
        unsigned = {
            "schema_version": (
                "bb.rl.phase5-f4-campaign-conversion-authority.v1"
            ),
            "loader_receipt_digest": _sha256(
                canonical_json_bytes(loader.model_dump(mode="json"))
            ),
            "executor_receipt_digest": _sha256(
                canonical_json_bytes(executor.model_dump(mode="json"))
            ),
            "report_digest": _sha256(
                canonical_json_bytes(report.model_dump(mode="json"))
            ),
            "target": report.target.model_dump(mode="json"),
            "signer_key_id": authenticator.key_id,
            "signature_algorithm": authenticator.algorithm,
        }
        unsigned_raw = canonical_json_bytes(unsigned)
        receipt = F4CampaignConversionAuthority.model_validate(
            {
                **unsigned,
                "signed_payload_digest": _sha256(unsigned_raw),
                "signature": authenticator.sign(unsigned_raw).hex(),
            },
            strict=True,
        )
        raw = canonical_json_bytes(receipt.model_dump(mode="json"))
        repository = self.service._dependencies.evidence_repository
        ref = repository._cas.put_bytes(
            raw,
            artifact_id=(
                "phase5-f4/campaign-conversion-authority/"
                f"{receipt.report_digest[7:]}"
            ),
            media_type=_CAMPAIGN_AUTHORITY_MEDIA_TYPE,
            metadata={
                "schema": receipt.schema_version,
                "target_run_id": receipt.target.target_run_id,
            },
        )
        return ref, self.verify_campaign_authority(ref, receipt)

    def verify_campaign_authority(
        self,
        ref: c.ArtifactRef,
        expected: F4CampaignConversionAuthority,
    ) -> F4CampaignConversionAuthority:
        try:
            raw = self.load_evidence(ref)
            receipt = F4CampaignConversionAuthority.model_validate_json(
                raw, strict=True
            )
        except BaseException as exc:
            raise F4TargetCanaryError(
                "campaign conversion authority cannot be reopened"
            ) from exc
        if (
            canonical_json_bytes(receipt.model_dump(mode="json")) != raw
            or receipt != expected
        ):
            raise F4TargetCanaryError(
                "campaign conversion authority repository body mismatch"
            )
        authenticator = self._composition.authority_graph.authenticator
        if (
            receipt.signer_key_id != authenticator.key_id
            or receipt.signature_algorithm != authenticator.algorithm
            or not hmac.compare_digest(
                receipt.signed_payload_digest,
                _sha256(receipt.unsigned_canonical_bytes()),
            )
            or not authenticator.verify(
                receipt.unsigned_canonical_bytes(),
                bytes.fromhex(receipt.signature),
            )
        ):
            raise F4TargetCanaryError(
                "campaign conversion authority signature is invalid"
            )
        return receipt

    def load_evidence(self, ref: c.ArtifactRef) -> bytes:
        repository = self.service._dependencies.evidence_repository
        return repository._read_ref_exact(ref)
    async def close(self) -> None:
        await self._composition.close_runtime()

    async def close_authority(self) -> None:
        await self._composition.close()

    def cleanup_observation(self) -> F4CleanupObservation:
        inventory = self._composition.observe_cleanup_inventory()
        lease = self._composition.outer_bridge_lease
        bridge_cleanup = self._composition.outer_bridge_cleanup_receipt
        if lease is None or bridge_cleanup is None:
            raise F4TargetCanaryError(
                "production composition lacks outer-bridge cleanup absence proof"
            )
        cleanup_digest = bridge_cleanup.canonical_digest()
        broker_ref = ImmutableRef(
            reference=(
                "evidence://phase5-f4/outer-bridge-cleanup"
                f"@{cleanup_digest}"
            ),
            digest=cleanup_digest,
        )
        return F4CleanupObservation(
            **dataclasses.asdict(inventory),
            inventory_digest=inventory.canonical_digest(
                broker_ref.model_dump(mode="json")
            ),
            broker_close_receipt_ref=broker_ref,
        )



def _load_production_runtime(spec: F4TargetCanaryInput) -> _ProductionRuntime:
    return _load_production_runtime_binding(spec.production, spec.target)


def _load_production_runtime_binding(
    production: F4ProductionBinding,
    target: F4TargetIdentity,
) -> _ProductionRuntime:
    descriptor_raw = Path(production.composition_ref_path).read_bytes()
    try:
        descriptor_value = canonical_json_loads(descriptor_raw)
    except Exception as exc:
        raise F4TargetCanaryError(
            "production composition descriptor is not JSON"
        ) from exc
    if canonical_json_bytes(descriptor_value) != descriptor_raw:
        raise F4TargetCanaryError(
            "production composition descriptor is not canonical JSON"
        )
    if _sha256(descriptor_raw) != production.composition_descriptor_ref.digest:
        raise F4TargetCanaryError("production composition descriptor digest mismatch")
    if descriptor_value.get("schema_version") == "bb.rl.harness-composition-ref.v1":
        descriptor: CompositionRefV1 | CompositionRefV2 = (
            CompositionRefV1.model_validate_json(descriptor_raw, strict=True)
        )
    elif descriptor_value.get("schema_version") == "bb.rl.harness-composition-ref.v2":
        descriptor = CompositionRefV2.model_validate_json(descriptor_raw, strict=True)
    else:
        raise F4TargetCanaryError(
            "unsupported production composition descriptor schema"
        )
    if descriptor.manifest_sha256 != production.composition_manifest_ref.digest:
        raise F4TargetCanaryError("production composition manifest ref mismatch")
    manifest_raw = Path(descriptor.manifest_path).read_bytes()
    if _sha256(manifest_raw) != descriptor.manifest_sha256:
        raise F4TargetCanaryError("production composition manifest digest mismatch")
    manifest_value = canonical_json_loads(manifest_raw)
    if (
        type(descriptor) is CompositionRefV1
        and manifest_value.get("schema_version") == "bb.rl.harness-composition.v1"
    ):
        manifest: HarnessCompositionManifestV1 | HarnessCompositionManifestV2 = (
            HarnessCompositionManifestV1.model_validate_json(manifest_raw, strict=True)
        )
    elif (
        type(descriptor) is CompositionRefV2
        and manifest_value.get("schema_version") == "bb.rl.harness-composition.v2"
    ):
        manifest = HarnessCompositionManifestV2.model_validate_json(
            manifest_raw, strict=True
        )
    else:
        raise F4TargetCanaryError(
            "production composition descriptor/manifest schema mismatch"
        )
    if manifest.authority_bundle_ref.sha256 != production.authority_bundle_ref.digest:
        raise F4TargetCanaryError("production authority bundle ref mismatch")
    observed = F4TargetIdentity(
        target_run_id=os.environ.get("BREADBOARD_TARGET_RUN_ID", ""),
        target_job_id=os.environ.get("SLURM_JOB_ID", ""),
        target_node_id=os.environ.get(
            "SLURM_JOB_NODELIST", os.environ.get("SLURM_NODELIST", socket.gethostname())
        ),
    )
    if observed != target:
        raise F4TargetCanaryError("observed target run/job/node identity mismatch")
    composition = load_production_composition(
        production.composition_ref_path, production.secret_files
    )
    runtime = _ProductionRuntime(
        composition=composition,
        production=production,
        target_identity=observed,
        lease_root=manifest.stores.lease.path,
    )
    return runtime


def _expect_operation_response(operation: Any, label: str) -> Any:
    if operation is None or not hasattr(operation, "response"):
        raise F4TargetCanaryError(
            f"{label} did not return a real-service-shaped operation"
        )
    return operation.response


def _artifact_bytes(
    runtime: F4TargetRuntime,
    ref: c.ArtifactRef,
    kind: c.ArtifactKind,
    label: str,
) -> bytes:
    try:
        raw = runtime.load_artifact(ref, kind)
    except BaseException as exc:
        raise F4TargetCanaryError(f"missing compiled {label} receipt") from exc
    if type(raw) is not bytes or not raw:
        raise F4TargetCanaryError(f"missing compiled {label} receipt")
    return raw


def _evidence_ref(value: Any, label: str) -> c.ArtifactRef:
    try:
        return c.ArtifactRef.model_validate(value, strict=True)
    except BaseException as exc:
        raise F4TargetCanaryError(f"{label} reference is missing or malformed") from exc


def _read_evidence_bytes(
    runtime: F4TargetRuntime, ref: c.ArtifactRef, label: str
) -> bytes:
    try:
        raw = runtime.load_evidence(ref)
        value = canonical_json_loads(raw)
    except BaseException as exc:
        raise F4TargetCanaryError(f"{label} evidence cannot be opened") from exc
    if (
        type(raw) is not bytes
        or canonical_json_bytes(value) != raw
        or _sha256(raw) != ref.sha256
        or len(raw) != ref.size_bytes
    ):
        raise F4TargetCanaryError(
            f"{label} evidence bytes do not match their reference"
        )
    return raw


def _read_evidence_model(
    runtime: F4TargetRuntime,
    ref: c.ArtifactRef,
    model: type[BaseModel],
    label: str,
) -> BaseModel:
    raw = _read_evidence_bytes(runtime, ref, label)
    try:
        return model.model_validate_json(raw, strict=True)
    except BaseException as exc:
        raise F4TargetCanaryError(f"{label} evidence schema is invalid") from exc


def _verify_lifecycle_evidence(
    runtime: F4TargetRuntime,
    episode_id: str,
    create_wire: dict[str, Any],
    run_wire: dict[str, Any],
    close_wire: dict[str, Any],
    plan: c.EffectiveExecutionPlan,
    *,
    selection_digest: str,
    policy_binding_digest: str,
    task_input_digest: str,
    run_context_digest: str,
    target_identity: F4TargetIdentity,
    subject_digest: str,
) -> F4LifecycleEvidence:
    completed_ref = _evidence_ref(
        run_wire.get("completed_envelope_ref"), "completed envelope"
    )
    closed_ref = _evidence_ref(close_wire.get("closed_envelope_ref"), "closed envelope")
    completed = _read_evidence_model(
        runtime, completed_ref, F4CompletedEnvelope, "completed envelope"
    )
    closed = _read_evidence_model(
        runtime, closed_ref, F4ClosedEnvelope, "closed envelope"
    )
    assert isinstance(completed, F4CompletedEnvelope)
    assert isinstance(closed, F4ClosedEnvelope)
    if (
        completed.episode_id != episode_id
        or closed.episode_id != episode_id
        or closed.completed_envelope_ref != completed_ref
        or completed.cleanup_disposition != "pending"
        or closed.cleanup_disposition != "released"
        or _evidence_ref(
            run_wire.get("completed_envelope_ref"), "final run completed envelope"
        )
        != completed_ref
        or run_wire.get("closed_envelope_ref") is not None
    ):
        raise F4TargetCanaryError(
            "completed/closed lifecycle evidence identity mismatch"
        )
    if (
        completed.primary_outcome != "succeeded"
        or closed.primary_outcome != "succeeded"
        or closed.cleanup_receipt is None
        or closed.cleanup_receipt_digest is None
        or _sha256(canonical_json_bytes(closed.cleanup_receipt))
        != closed.cleanup_receipt_digest
        or closed.cleanup_receipt.get("released") is not True
    ):
        raise F4TargetCanaryError(
            "closed envelope cleanup receipt is missing or not content-bound"
        )
    _read_evidence_bytes(
        runtime, completed.completed_event_ref, "completed lifecycle event"
    )
    _read_evidence_bytes(
        runtime, closed.reconciliation_event_ref, "close reconciliation event"
    )
    run_receipt = _read_evidence_model(
        runtime,
        completed.run_response_ref,
        F4RunExecutionReceipt,
        "run execution receipt",
    )
    assert isinstance(run_receipt, F4RunExecutionReceipt)
    if run_wire.get("reward") != 1:
        raise F4TargetCanaryError(
            "run result does not match canonical server verifier receipt"
        )
    expected_run_receipt = F4RunExecutionReceipt(
        schema_version="bb.rl.phase5-f4-run-execution-receipt.v1",
        episode_id=run_wire["episode_id"],
        create_fingerprint=run_wire["create_fingerprint"],
        run_fingerprint=run_wire["run_fingerprint"],
        primary_disposition=run_wire["primary_disposition"],
        response=run_wire["response"],
        termination=run_wire["termination"],
        turn_count=run_wire["turn_count"],
        reward=run_wire["reward"],
        reward_components=run_wire["reward_components"],
        primary_measurement_digest=run_wire["primary_measurement_digest"],
        verifier_result_digest=run_wire["verifier_result_digest"],
        verifier_measurement_digest=run_wire["verifier_measurement_digest"],
    )
    if (
        _read_evidence_bytes(
            runtime, completed.create_response_ref, "create response"
        )
        != canonical_json_bytes(create_wire)
        or run_receipt != expected_run_receipt
        or completed.create_fingerprint != create_wire.get("create_fingerprint")
        or completed.run_fingerprint != run_wire.get("run_fingerprint")
    ):
        raise F4TargetCanaryError(
            "completed envelope does not bind the exact create/run responses"
        )
    evidence_manifest = _read_evidence_model(
        runtime,
        completed.evidence_manifest_ref,
        F4ExecutionEvidenceManifest,
        "execution evidence manifest",
    )
    assert isinstance(evidence_manifest, F4ExecutionEvidenceManifest)
    artifact_manifest = _read_evidence_model(
        runtime,
        evidence_manifest.artifact_manifest_ref,
        F4ArtifactManifest,
        "artifact manifest",
    )
    assert isinstance(artifact_manifest, F4ArtifactManifest)
    runner_ledger = _read_evidence_model(
        runtime,
        evidence_manifest.runner_ledger_ref,
        F4RunnerLedger,
        "runner ledger",
    )
    assert isinstance(runner_ledger, F4RunnerLedger)
    if runner_ledger.episode_id != episode_id:
        raise F4TargetCanaryError("runner ledger episode identity mismatch")
    ledger_by_call = {event.call_id: event for event in runner_ledger.events}
    preflight = create_wire.get("sandbox_preflight")
    if type(preflight) is not dict:
        raise F4TargetCanaryError("create preflight receipt is missing")
    expected_plan_digest = plan.canonical_digest()
    if (
        evidence_manifest.episode_id != episode_id
        or evidence_manifest.primary_disposition != "succeeded"
        or evidence_manifest.reward_disposition != "succeeded"
        or evidence_manifest.primary_measurement_digest is None
        or evidence_manifest.verifier_measurement_digest is None
        or evidence_manifest.verifier_result_digest is None
        or run_wire.get("primary_measurement_digest")
        != evidence_manifest.primary_measurement_digest
        or run_wire.get("verifier_measurement_digest")
        != evidence_manifest.verifier_measurement_digest
        or run_wire.get("verifier_result_digest")
        != evidence_manifest.verifier_result_digest
        or evidence_manifest.resolved_plan_digest != expected_plan_digest
        or evidence_manifest.selection_digest != selection_digest
        or evidence_manifest.effective_plan_digest != expected_plan_digest
        or evidence_manifest.policy_binding_digest != policy_binding_digest
        or evidence_manifest.materialization_digest
        != preflight.get("materialization_plan_digest")
        or completed.subject_digest != subject_digest
        or evidence_manifest.task_input_digest != task_input_digest
        or evidence_manifest.run_context_digest != run_context_digest
        or evidence_manifest.target_identity != target_identity
    ):
        raise F4TargetCanaryError(
            "run response does not join completed execution evidence"
        )
    object_refs = tuple(item.artifact_ref for item in artifact_manifest.objects)
    object_digests = {ref.sha256 for ref in object_refs}
    required_digests = {
        evidence_manifest.primary_measurement_digest,
        evidence_manifest.verifier_measurement_digest,
        evidence_manifest.verifier_result_digest,
    }
    if not required_digests <= object_digests:
        raise F4TargetCanaryError(
            "measurement or verifier result is absent from artifact manifest"
        )
    output_digests: set[str] = set()
    tool_bindings: list[F4ToolCallReceiptBinding] = []
    verifier_bindings: list[F4VerifierResultReceiptBinding] = []
    expected_tools = {
        (grant.tool_id, grant.implementation_digest)
        for grant in plan.effective_capabilities.tools
    }
    for item in artifact_manifest.objects:
        ref = item.artifact_ref
        if item.role == "tool-call-receipt":
            if (
                ref.media_type
                != "application/vnd.breadboard.phase5-f4-tool-call-receipt+json;version=1"
            ):
                raise F4TargetCanaryError("tool-call receipt media type mismatch")
            artifact = _read_evidence_model(
                runtime, ref, F4ToolCallReceipt, "tool-call receipt"
            )
            assert isinstance(artifact, F4ToolCallReceipt)
            if (
                artifact.episode_id != episode_id
                or artifact.effective_plan_digest != plan.canonical_digest()
                or artifact.policy_observation_digest
                != plan.policy_capability_observation_digest
                or (artifact.tool_id, artifact.implementation_digest)
                not in expected_tools
            ):
                raise F4TargetCanaryError(
                    "tool-call receipt does not join episode/plan/policy/tool authority"
                )
            ledger_event = ledger_by_call.get(artifact.call_id)
            if ledger_event is None:
                raise F4TargetCanaryError(
                    "tool-call receipt is absent from runner ledger"
                )
            policy_call = _read_evidence_model(
                runtime,
                ledger_event.policy_call_receipt_ref,
                F4PolicyCallReceipt,
                "policy-call receipt",
            )
            policy_observation = _read_evidence_model(
                runtime,
                ledger_event.policy_observation_ref,
                F4PolicyObservationEvidence,
                "policy observation",
            )
            assert isinstance(policy_call, F4PolicyCallReceipt)
            assert isinstance(policy_observation, F4PolicyObservationEvidence)
            if (
                ledger_event.policy_call_receipt_ref.sha256
                != artifact.policy_call_digest
                or ledger_event.policy_call_digest != artifact.policy_call_digest
                or ledger_event.episode_id != artifact.episode_id
                or ledger_event.effective_plan_digest
                != artifact.effective_plan_digest
                or ledger_event.policy_observation_digest
                != artifact.policy_observation_digest
                or ledger_event.tool_id != artifact.tool_id
                or ledger_event.implementation_digest
                != artifact.implementation_digest
                or ledger_event.exit_code != artifact.exit_code
                or ledger_event.output_digest != artifact.output_digest
                or policy_call.episode_id != artifact.episode_id
                or policy_call.effective_plan_digest
                != artifact.effective_plan_digest
                or policy_call.policy_observation_digest
                != artifact.policy_observation_digest
                or policy_call.call_id != artifact.call_id
                or policy_call.tool_id != artifact.tool_id
                or policy_call.implementation_digest
                != artifact.implementation_digest
                or policy_observation.episode_id != artifact.episode_id
                or policy_observation.effective_plan_digest
                != artifact.effective_plan_digest
                or policy_observation.policy_observation_digest
                != artifact.policy_observation_digest
                or policy_observation.call_id != artifact.call_id
            ):
                raise F4TargetCanaryError(
                    "tool receipt does not join ledger/policy/observation evidence"
                )
            tool_bindings.append(F4ToolCallReceiptBinding(ref=ref, artifact=artifact))
            output_digests.add(artifact.output_digest)
        elif item.role == "verifier-result":
            if (
                ref.media_type
                != "application/vnd.breadboard.phase5-f4-verifier-result-receipt+json;version=1"
            ):
                raise F4TargetCanaryError("verifier result media type mismatch")
            artifact = _read_evidence_model(
                runtime, ref, F4VerifierResultReceipt, "verifier result receipt"
            )
            assert isinstance(artifact, F4VerifierResultReceipt)
            if (
                artifact.episode_id != episode_id
                or artifact.effective_plan_digest != plan.canonical_digest()
                or artifact.verifier_implementation_digest
                != plan.verifier.implementation_digest
                or artifact.verifier_measurement_digest
                != evidence_manifest.verifier_measurement_digest
                or artifact.reward_components != evidence_manifest.reward_components
                or ref.sha256 != evidence_manifest.verifier_result_digest
            ):
                raise F4TargetCanaryError(
                    "verifier result does not join episode/plan/verifier/reward evidence"
                )
            verifier_bindings.append(
                F4VerifierResultReceiptBinding(ref=ref, artifact=artifact)
            )
            output_digests.add(artifact.output_digest)
        else:
            try:
                raw = runtime.load_evidence(ref)
            except BaseException as exc:
                raise F4TargetCanaryError(
                    "manifest evidence object cannot be opened"
                ) from exc
            if (
                type(raw) is not bytes
                or _sha256(raw) != ref.sha256
                or len(raw) != ref.size_bytes
            ):
                raise F4TargetCanaryError("manifest evidence object bytes mismatch")
    if not output_digests or not output_digests <= object_digests:
        raise F4TargetCanaryError(
            "tool or verifier output digest is not an opened manifest artifact"
        )
    if not tool_bindings or len(verifier_bindings) != 1:
        raise F4TargetCanaryError(
            "lifecycle requires tool-call receipts and one server verifier result"
        )
    if {binding.artifact.call_id for binding in tool_bindings} != set(
        ledger_by_call
    ):
        raise F4TargetCanaryError(
            "runner ledger does not exactly cover opened tool receipts"
        )
    return F4LifecycleEvidence(
        completed_envelope_ref=completed_ref,
        closed_envelope_ref=closed_ref,
        evidence_manifest_ref=completed.evidence_manifest_ref,
        artifact_manifest_ref=evidence_manifest.artifact_manifest_ref,
        primary_measurement_digest=evidence_manifest.primary_measurement_digest,
        verifier_measurement_digest=evidence_manifest.verifier_measurement_digest,
        verifier_result_digest=evidence_manifest.verifier_result_digest,
        cleanup_receipt_digest=closed.cleanup_receipt_digest,
        tool_call_receipts=tuple(tool_bindings),
        verifier_result=verifier_bindings[0],
    )


def _pointer_value(document: Any, pointer: str) -> Any:
    current = document
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            try:
                current = current[token]
            except KeyError as exc:
                raise F4TargetCanaryError(
                    "compiler-visible semantic delta pointer is absent"
                ) from exc
        elif isinstance(current, (list, tuple)):
            try:
                index = int(token)
            except ValueError as exc:
                raise F4TargetCanaryError(
                    "compiler-visible semantic delta index is invalid"
                ) from exc
            if index < 0 or index >= len(current) or str(index) != token:
                raise F4TargetCanaryError(
                    "compiler-visible semantic delta index is invalid"
                )
            current = current[index]
        else:
            raise F4TargetCanaryError(
                "compiler-visible semantic delta pointer is absent"
            )
    return current


def _validate_plan_invariants(
    spec: F4TargetCanaryInput,
    variant: F4VariantExecution,
    plan: c.EffectiveExecutionPlan,
    base_semantics: Mapping[str, Any],
) -> tuple[str, ...]:
    compiled = plan.base_compiled
    if (
        compiled.manifest_digest != variant.compiled_config_ref.digest
        or compiled.bundle_digest != variant.config_bundle_ref.digest
        or compiled.closure_digest != variant.dependency_closure_ref.digest
        or compiled.compiler.canonical_digest() != variant.compiler_identity_ref.digest
        or plan.base_receipt_digest != variant.admission_receipt_ref.digest
    ):
        raise F4TargetCanaryError(
            "compiled config or admission receipt identity mismatch"
        )
    expected_overlay_order = tuple(
        ref.digest for ref in variant.ordered_overlay_receipt_refs
    )
    actual_overlay_order = tuple(
        application.result_receipt_digest for application in plan.overlay_applications
    )
    if actual_overlay_order != expected_overlay_order:
        raise F4TargetCanaryError("persisted overlay receipt order mismatch")
    expected_final_receipt = (
        expected_overlay_order[-1]
        if expected_overlay_order
        else variant.admission_receipt_ref.digest
    )
    if plan.final_receipt_digest != expected_final_receipt:
        raise F4TargetCanaryError(
            "effective plan final receipt is not the admitted overlay chain"
        )
    before_value = _pointer_value(
        base_semantics, variant.semantic_delta.compiler_field_pointer
    )
    if (
        _sha256(canonical_json_bytes(before_value))
        != variant.semantic_delta.before_digest
    ):
        raise F4TargetCanaryError(
            "compiler-visible semantic delta before digest mismatch"
        )
    after_value = _pointer_value(
        plan.effective_semantics, variant.semantic_delta.compiler_field_pointer
    )
    if (
        _sha256(canonical_json_bytes(after_value))
        != variant.semantic_delta.after_digest
    ):
        raise F4TargetCanaryError(
            "compiler-visible semantic delta after digest mismatch"
        )
    invariant = spec.invariant_identity
    slots = plan.policy_slots
    if len(slots) != 1:
        raise F4TargetCanaryError(
            "non-config invariant drift: exact policy slot missing"
        )
    slot = slots[0]
    actual = (
        plan.task.task_binding_digest,
        plan.task.task_contract_digest,
        plan.task.repository_snapshot_digest,
        slot.model_digest,
        slot.checkpoint_digest,
        plan.sandbox.image_digest,
        plan.verifier.image_digest,
        plan.verifier.implementation_digest,
    )
    expected = (
        invariant.task_row_ref.digest,
        invariant.task_contract_digest,
        invariant.repository_snapshot_ref.digest,
        invariant.model_ref.digest,
        invariant.checkpoint_ref.digest,
        invariant.task_image_ref.digest,
        invariant.verifier_image_ref.digest,
        invariant.verifier_ref.digest,
    )
    if actual != expected:
        raise F4TargetCanaryError(
            "non-config task/model/checkpoint/verifier/image invariant drift"
        )
    if plan.sandbox.security_policy_digest != variant.requested_security_policy_digest:
        raise F4TargetCanaryError(
            "resolved security policy differs from the preflight request"
        )
    if variant.request.task.canonical_digest() != plan.task.task_contract_digest:
        raise F4TargetCanaryError(
            "resolved task contract does not join the target request"
        )
    return actual_overlay_order


def _runtime_identity(runtime: F4TargetRuntime, spec: F4TargetCanaryInput) -> None:
    if (
        runtime.composition_descriptor_digest
        != spec.production.composition_descriptor_ref.digest
        or runtime.composition_manifest_digest
        != spec.production.composition_manifest_ref.digest
        or runtime.authority_bundle_digest
        != spec.production.authority_bundle_ref.digest
    ):
        raise F4TargetCanaryError(
            "runtime substituted the bound production composition or authority"
        )
    if runtime.target_identity != spec.target:
        raise F4TargetCanaryError("runtime target run/job/node identity mismatch")


async def _execute_variant(
    spec: F4TargetCanaryInput,
    variant: F4VariantExecution,
    runtime: F4TargetRuntime,
) -> F4VariantTargetReport:
    service = runtime.service
    create_operation: Any | None = None
    close_operation: Any | None = None
    try:
        create_operation = await service.create(variant.request)
        if _wire(getattr(create_operation, "disposition", None)) != "fresh":
            raise F4TargetCanaryError(
                "target create was not a fresh committed selection"
            )
        created = _expect_operation_response(create_operation, "create")
        selector_ref = variant.request.selector.ref
        selector_raw = _artifact_bytes(
            runtime,
            selector_ref,
            c.ArtifactKind.CONFIG_SET,
            "weighted selector",
        )
        selector = c.ConfigSetManifest.model_validate_json(selector_raw, strict=True)
        if (
            selector.canonical_digest() != variant.request.selector.digest
            or len(selector.candidates) != 2
            or len(
                {
                    candidate.candidate.candidate_id
                    for candidate in selector.candidates
                }
            )
            != 2
        ):
            raise F4TargetCanaryError(
                "F4 weighted selector is not the exact frozen two-candidate A/B pair"
            )
        designated = tuple(
            candidate
            for candidate in selector.candidates
            if candidate.candidate.candidate_id == variant.variant_id
        )
        if (
            len(designated) != 1
            or not designated[0].candidate.overlays
            or tuple(
                overlay.result_receipt_digest
                for overlay in designated[0].candidate.overlays
            )
            != tuple(
                ref.digest for ref in variant.ordered_overlay_receipt_refs
            )
        ):
            raise F4TargetCanaryError(
                "F4 designated A/B arm does not bind the admitted overlay chain"
            )
        if created.episode_id != variant.request.episode_id:
            raise F4TargetCanaryError("created episode identity mismatch")
        selection_raw = _artifact_bytes(
            runtime,
            created.selection_record_ref,
            c.ArtifactKind.SELECTION_RECORD,
            "selection",
        )
        selection = c.SelectionRecord.model_validate_json(selection_raw, strict=True)
        selection_digest = selection.canonical_digest()
        expected_owner_key = _sha256(
            canonical_json_bytes(
                {
                    "schema_version": "bb.rl.selection-owner.v1",
                    "subject_digest": variant.request.subject.canonical_digest(),
                    "episode_id": variant.request.episode_id,
                }
            )
        )
        expected_request_digest = _sha256(
            canonical_json_bytes(
                {
                    "schema_version": "bb.rl.selection-request.v1",
                    "episode_id": variant.request.episode_id,
                    "subject_digest": variant.request.subject.canonical_digest(),
                    "selector_digest": selection.selector_digest,
                    "config_set_digest": selection.config_set_digest,
                    "selection_nonce": variant.request.selection_nonce,
                    "task_contract_digest": variant.request.task.canonical_digest(),
                    "policy_capability_observation_digest": (
                        selection.policy_capability_observation_digest
                    ),
                    "policy_capability_digest": selection.policy_capability_digest,
                    "admitted_set_root": selection.admitted_set_root,
                    "revocation_state_digest": selection.revocation_state_digest,
                    "episode_overlays": _wire(variant.request.episode_overlays),
                }
            )
        )
        persisted_overlay_order = tuple(
            overlay.result_receipt_digest for overlay in selection.selected_overlays
        ) + tuple(
            overlay.result_receipt_digest
            for overlay in variant.request.episode_overlays
        )
        expected_overlay_order = tuple(
            ref.digest for ref in variant.ordered_overlay_receipt_refs
        )
        if (
            selection_digest != created.selection_record_ref.sha256
            or selection_digest != variant.selection_record_ref.digest
            or created.selection_commit.binding.selection_record_digest
            != selection_digest
            or created.selection_commit.binding.owner_key != expected_owner_key
            or created.selection_commit.binding.request_digest
            != expected_request_digest
            or created.selection_commit.binding_ref.sha256
            != created.selection_commit.binding.canonical_digest()
            or selection.algorithm != "weighted-v1"
            or selection.episode_id != variant.request.episode_id
            or selection.subject_digest != variant.request.subject.canonical_digest()
            or selection.selector_digest != variant.request.selector.digest
            or selection.config_set_digest != variant.request.selector.digest
            or selection.selection_nonce != variant.request.selection_nonce
            or selection.task_contract_digest != variant.request.task.canonical_digest()
            or selection.selected_candidate_id != variant.variant_id
            or selection.selected_receipt_digest
            != variant.admission_receipt_ref.digest
            or persisted_overlay_order != expected_overlay_order
        ):
            raise F4TargetCanaryError(
                "persisted weighted selection receipt identity mismatch"
            )
        plan_raw = _artifact_bytes(
            runtime,
            created.effective_plan_ref,
            c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
            "effective-plan",
        )
        plan = c.EffectiveExecutionPlan.model_validate_json(plan_raw, strict=True)
        plan_digest = plan.canonical_digest()
        if (
            plan_digest != created.effective_plan_ref.sha256
            or plan_digest != created.effective_plan_digest
            or plan.selection_record_digest != selection_digest
            or created.base_receipt_digest != variant.admission_receipt_ref.digest
            or created.final_receipt_digest != plan.final_receipt_digest
        ):
            raise F4TargetCanaryError("compiled effective-plan receipt mismatch")
        semantic_raw = _artifact_bytes(
            runtime,
            variant.compiled_semantics_ref,
            c.ArtifactKind.COMPILED_MANIFEST,
            "compiled-semantics",
        )
        base_semantics = canonical_json_loads(semantic_raw)
        if (
            type(base_semantics) is not dict
            or _sha256(
                canonical_json_bytes(
                    {
                        "schema": COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
                        "config": base_semantics,
                    }
                )
            )
            != plan.base_compiled.semantic_digest
        ):
            raise F4TargetCanaryError(
                "compiled semantic source does not match target identity"
            )
        overlay_order = _validate_plan_invariants(
            spec,
            variant,
            plan,
            base_semantics,
        )
        preflight = _wire(created.sandbox_preflight)
        security_observation = F4SecurityPolicyObservation.model_validate(
            {
                **preflight,
                "create_fingerprint": created.create_fingerprint,
                "effective_plan_digest": created.effective_plan_digest,
            },
            strict=True,
        )
        if (
            security_observation.security_policy_digest
            != variant.requested_security_policy_digest
            or security_observation.runtime_binary_digest
            != plan.sandbox.runtime_binary_digest
            or security_observation.image_digest != plan.sandbox.image_digest
            or security_observation.verifier_digest
            != plan.verifier.implementation_digest
        ):
            raise F4TargetCanaryError(
                "create preflight differs from effective plan runtime authority"
            )
        run_operation = await service.run(
            variant.request.episode_id,
            create_fingerprint=created.create_fingerprint,
            task_input=canonical_json_loads(canonical_json_bytes(spec.task_input)),
            context=canonical_json_loads(canonical_json_bytes(spec.run_context)),
        )
        run = _expect_operation_response(run_operation, "run")
        close_operation = await service.close_episode(variant.request.episode_id)
        closed = _expect_operation_response(close_operation, "close")
        create_wire = _wire(created)
        run_wire = _wire(run)
        close_wire = _wire(closed)
        lifecycle_evidence = _verify_lifecycle_evidence(
            runtime,
            variant.request.episode_id,
            create_wire,
            run_wire,
            close_wire,
            plan,
            selection_digest=selection_digest,
            subject_digest=variant.request.subject.canonical_digest(),
            policy_binding_digest=variant.request.policy_binding.canonical_digest(),
            task_input_digest=_sha256(canonical_json_bytes(spec.task_input)),
            run_context_digest=_sha256(canonical_json_bytes(spec.run_context)),
            target_identity=spec.target,
        )
        verifier_result = lifecycle_evidence.verifier_result.artifact
        reward = verifier_result.reward
        if (
            run_wire.get("primary_disposition") != "succeeded"
            or run_wire.get("reward") != reward
            or run_wire.get("reward_components") != verifier_result.reward_components
        ):
            raise F4TargetCanaryError(
                "run result does not match canonical server verifier receipt"
            )
        if close_wire.get("cleanup_disposition") != "released":
            raise F4TargetCanaryError("target cleanup did not release the episode")
        return F4VariantTargetReport(
            variant_id=variant.variant_id,
            episode_id=variant.request.episode_id,
            compiled_manifest_digest=plan.base_compiled.manifest_digest,
            config_bundle_digest=plan.base_compiled.bundle_digest,
            dependency_closure_digest=plan.base_compiled.closure_digest,
            compiler_identity_digest=plan.base_compiled.compiler.canonical_digest(),
            admission_receipt_digest=plan.base_receipt_digest,
            selection={
                "algorithm": selection.algorithm,
                "selection_nonce": selection.selection_nonce,
                "selection_record_ref": _wire(created.selection_record_ref),
                "selection_record_digest": selection_digest,
                "selection_commit": _wire(created.selection_commit),
                "selected_candidate_id": selection.selected_candidate_id,
                "selected_receipt_digest": selection.selected_receipt_digest,
                "persisted_before_run": True,
                "redrawn": False,
            },
            overlay_order=overlay_order,
            effective_plan_ref=_wire(created.effective_plan_ref),
            effective_plan_digest=plan_digest,
            target_identity=spec.target,
            lifecycle=lifecycle_evidence,
            verifier=verifier_result.model_dump(mode="json"),
            invariant_identity=spec.invariant_identity,
            requested_security_policy_digest=variant.requested_security_policy_digest,
            security_policy_observation=security_observation,
            non_config_invariants_preserved=True,
            fallback_used=False,
        )
    except BaseException as primary_exc:
        if create_operation is not None and close_operation is None:
            try:
                await service.close_episode(variant.request.episode_id)
            except BaseException as cleanup_exc:
                raise F4TargetCanaryError(
                    f"target primary failed ({primary_exc!r}) and cleanup failed "
                    f"({cleanup_exc!r}) for {variant.request.episode_id}"
                ) from cleanup_exc
        raise


async def _run_f4_target_canaries(
    spec: F4TargetCanaryInput,
    *,
    input_digest: str,
    runtime: F4TargetRuntime,
) -> F4TargetCanaryTestRunResult:
    reports: list[F4VariantTargetReport] = []
    primary_exc: BaseException | None = None
    try:
        _runtime_identity(runtime, spec)
        await runtime.service.start()
        for variant in spec.variants:
            reports.append(await _execute_variant(spec, variant, runtime))
    except BaseException as exc:
        primary_exc = exc
    try:
        await runtime.close()
    except BaseException as cleanup_exc:
        if primary_exc is not None:
            raise F4TargetCanaryError(
                f"target run failed ({primary_exc!r}) and runtime close failed "
                f"({cleanup_exc!r})"
            ) from cleanup_exc
        raise
    if primary_exc is not None:
        raise primary_exc
    final_cleanup = runtime.cleanup_observation()
    if type(final_cleanup) is not F4CleanupObservation:
        raise F4TargetCanaryError(
            "post-close inventory probe did not return the canonical cleanup observation"
        )
    _require_clean(final_cleanup)
    report = F4TargetCanaryReport(
        schema_version="bb.rl.phase5-f4-target-canary-report.v1",
        input_digest=input_digest,
        production={
            "composition_descriptor_ref": spec.production.composition_descriptor_ref.model_dump(
                mode="json"
            ),
            "composition_manifest_ref": spec.production.composition_manifest_ref.model_dump(
                mode="json"
            ),
            "authority_bundle_ref": spec.production.authority_bundle_ref.model_dump(
                mode="json"
            ),
        },
        target=spec.target,
        execution_authority=spec.execution_authority,
        variants=tuple(reports),
        cleanup=final_cleanup,
        cleanup_complete=True,
        no_orphan_resources=True,
        promotion_authority=False,
        scorecard_authority=False,
    )
    report_path = _write_report(report, spec.output_dir)
    return F4TargetCanaryTestRunResult(report=report, report_path=report_path)


def _write_report(report: F4TargetCanaryReport, output_dir: str) -> str:
    root = Path(output_dir)
    root.mkdir(mode=0o750, parents=False, exist_ok=True)
    output = root / _REPORT_NAME
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    fd = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return os.fspath(output)


def _component_envelope(
    report: F4TargetCanaryReport, report_path: str
) -> F4TargetComponentEnvelope:
    if type(report) is not F4TargetCanaryReport:
        raise TypeError("report must be an exact F4TargetCanaryReport")
    normalized_path = _absolute(os.fspath(Path(report_path).resolve()))
    report_raw = canonical_json_bytes(report.model_dump(mode="json"))
    validated = F4TargetCanaryReport.model_validate_json(report_raw, strict=True)
    if validated != report:
        raise F4TargetCanaryError(
            "strict F4 target report validation changed the report"
        )
    if Path(normalized_path).read_bytes() != report_raw:
        raise F4TargetCanaryError(
            "persisted F4 target report differs from the strict report"
        )
    return F4TargetComponentEnvelope(
        schema_version="bb.rl.phase5-f4-target-component-report.v1",
        report_id="f4-target-canaries",
        component="rl_phase5_f4_target_canaries",
        passed=True,
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_authority=False,
        scorecard_update_allowed=False,
        report_sha256=_sha256(report_raw),
        report_path=normalized_path,
        summary={
            "variant_count": len(report.variants),
            "variant_order": [variant.variant_id for variant in report.variants],
            "fresh_selection_receipts": all(
                variant.selection.get("persisted_before_run") is True
                and variant.selection.get("redrawn") is False
                and variant.fallback_used is False
                for variant in report.variants
            ),
            "exact_non_config_invariants": all(
                variant.non_config_invariants_preserved is True
                for variant in report.variants
            ),
            "cleanup_complete": report.cleanup_complete,
            "no_orphan_resources": report.no_orphan_resources,
            "unexpected_outcomes": [],
        },
    )


def _component_report_line(report: F4TargetCanaryReport, report_path: str) -> bytes:
    envelope = _component_envelope(report, report_path)
    return (
        b"PHASE3_COMPONENT_REPORT_JSON="
        + canonical_json_bytes(envelope.model_dump(mode="json"))
        + b"\n"
    )


def _run_f4_target_canaries_for_test(
    spec: F4TargetCanaryInput,
    *,
    input_digest: str,
    runtime: F4TargetRuntime,
) -> F4TargetCanaryTestRunResult:
    if type(spec) is not F4TargetCanaryInput:
        raise TypeError("spec must be an exact F4TargetCanaryInput")
    _digest(input_digest)
    return asyncio.run(
        _run_f4_target_canaries(spec, input_digest=input_digest, runtime=runtime)
    )


def run_f4_target_canaries(
    spec: F4TargetCanaryInput,
    *,
    input_digest: str,
) -> F4TargetCanaryRunResult:
    if type(spec) is not F4TargetCanaryInput:
        raise TypeError("spec must be an exact F4TargetCanaryInput")
    _digest(input_digest)
    runtime = _load_production_runtime(spec)
    result: F4TargetCanaryRunResult | None = None
    primary_error: BaseException | None = None
    try:
        executed = asyncio.run(
            _run_f4_target_canaries(
                spec,
                input_digest=input_digest,
                runtime=runtime,
            )
        )
        loader_receipt = F4ProductionLoaderReceipt(
            schema_version="bb.rl.phase5-f4-production-loader-receipt.v1",
            input_digest=input_digest,
            production=spec.production,
            target=spec.target,
        )
        executor_receipt = F4TargetExecutorReceipt(
            schema_version="bb.rl.phase5-f4-target-executor-receipt.v1",
            loader_receipt_digest=_sha256(
                canonical_json_bytes(loader_receipt.model_dump(mode="json"))
            ),
            report_digest=_sha256(
                canonical_json_bytes(executed.report.model_dump(mode="json"))
            ),
            target=spec.target,
        )
        authority_ref, campaign_authority = (
            runtime.publish_campaign_authority(
                loader_receipt, executor_receipt, executed.report
            )
        )
        result = F4TargetCanaryRunResult(
            report=executed.report,
            report_path=executed.report_path,
            production_loader_receipt=loader_receipt,
            target_executor_receipt=executor_receipt,
            campaign_authority_ref=authority_ref,
            campaign_authority=campaign_authority,
        )
    except BaseException as exc:
        primary_error = exc
    try:
        asyncio.run(runtime.close_authority())
    except BaseException as cleanup_error:
        if primary_error is not None:
            raise F4TargetCanaryError(
                f"target run failed ({primary_error!r}) and production "
                f"authority close failed ({cleanup_error!r})"
            ) from cleanup_error
        raise
    if primary_error is not None:
        raise primary_error
    if result is None:
        raise AssertionError("production F4 target result was not constructed")
    return result


def build_campaign_target_report(
    result: F4TargetCanaryRunResult,
    *,
    trusted_production: F4ProductionBinding,
    trusted_target: F4TargetIdentity,
) -> F4TargetEvidenceReportBinding:
    """Derive every campaign identity from one persisted source-closed target result."""
    if type(result) is not F4TargetCanaryRunResult:
        raise TypeError("result must be an exact F4TargetCanaryRunResult")
    if (
        type(trusted_production) is not F4ProductionBinding
        or type(trusted_target) is not F4TargetIdentity
    ):
        raise TypeError(
            "exact independently trusted production and target authority required"
        )
    if (
        trusted_production != result.production_loader_receipt.production
        or trusted_target != result.report.target
    ):
        raise F4TargetCanaryError(
            "campaign conversion does not match independently trusted authority"
        )
    persisted = Path(result.report_path).resolve(strict=True).read_bytes()
    report_raw = canonical_json_bytes(result.report.model_dump(mode="json"))
    if persisted != report_raw:
        raise F4TargetCanaryError(
            "persisted F4 target report differs from the supplied target result"
        )
    verifier_runtime = _load_production_runtime_binding(
        trusted_production,
        trusted_target,
    )
    verification_error: BaseException | None = None
    try:
        verifier_runtime.verify_campaign_authority(
            result.campaign_authority_ref,
            result.campaign_authority,
        )
    except BaseException as exc:
        verification_error = exc
    try:
        asyncio.run(verifier_runtime.close_authority())
    except BaseException as cleanup_exc:
        if verification_error is not None:
            raise F4TargetCanaryError(
                "campaign authority verification failed "
                f"({verification_error!r}) and verifier cleanup failed "
                f"({cleanup_exc!r})"
            ) from cleanup_exc
        raise
    if verification_error is not None:
        raise verification_error


    def immutable(kind: str, digest: str) -> ImmutableRef:
        return ImmutableRef(
            reference=f"evidence://phase5-f4/{kind}@{digest}",
            digest=digest,
        )

    authority = result.report.execution_authority
    report_output_ref = immutable("target-output", _sha256(persisted))
    executions: list[F4TargetExecutionReceipt] = []
    for row in result.report.variants:
        if not row.lifecycle.tool_call_receipts:
            raise F4TargetCanaryError(
                "F4 target binding requires at least one tool-call receipt"
            )
        tools = tuple(
            immutable(f"tool-call/{row.episode_id}", binding.ref.sha256)
            for binding in row.lifecycle.tool_call_receipts
        )
        verifier = row.lifecycle.verifier_result
        effective_plan = c.ArtifactRef.model_validate(
            row.effective_plan_ref, strict=True
        )
        executions.append(
            F4TargetExecutionReceipt(
                target_attempt_id=row.episode_id,
                episode_id=row.episode_id,
                variant_id=row.variant_id,
                environment_id=authority.environment_id,
                environment_ref=authority.environment_ref,
                source_runtime_ref=authority.source_runtime_ref,
                target_run_id=result.report.target.target_run_id,
                target_job_id=result.report.target.target_job_id,
                target_node_id=result.report.target.target_node_id,
                invariant_identity=row.invariant_identity,
                target_attempt_output_ref=immutable(
                    f"target-attempt-output/{row.episode_id}",
                    row.lifecycle.tool_call_receipts[-1].artifact.output_digest,
                ),
                episode_output_ref=immutable(
                    f"episode-output/{row.episode_id}",
                    verifier.artifact.output_digest,
                ),
                target_report_output_ref=report_output_ref,
                compiled_config_ref=immutable(
                    f"compiled-config/{row.variant_id}",
                    row.compiled_manifest_digest,
                ),
                admission_receipt_ref=immutable(
                    f"admission/{row.variant_id}",
                    row.admission_receipt_digest,
                ),
                effective_plan_ref=immutable(
                    f"effective-plan/{row.episode_id}",
                    effective_plan.sha256,
                ),
                evidence_manifest_ref=immutable(
                    f"evidence-manifest/{row.episode_id}",
                    row.lifecycle.evidence_manifest_ref.sha256,
                ),
                completed_envelope_ref=immutable(
                    f"completed-envelope/{row.episode_id}",
                    row.lifecycle.completed_envelope_ref.sha256,
                ),
                closed_envelope_ref=immutable(
                    f"closed-envelope/{row.episode_id}",
                    row.lifecycle.closed_envelope_ref.sha256,
                ),
                tool_call_receipt_refs=tools,
                server_verifier_result_ref=immutable(
                    f"verifier-result/{row.episode_id}",
                    verifier.ref.sha256,
                ),
                verifier_passed=verifier.artifact.passed,
                reward=verifier.artifact.reward,
                cleanup_receipt_ref=immutable(
                    f"cleanup-receipt/{row.episode_id}",
                    row.lifecycle.cleanup_receipt_digest,
                ),
                terminal_outcome="completed-and-closed",
            )
        )
    report_id = result.report.target.target_run_id
    artifact = F4TargetEvidenceReport(
        schema_version="bb.rl.phase5-f4-target-evidence-report.v1",
        report_id=report_id,
        source_runtime_ref=authority.source_runtime_ref,
        executions=tuple(executions),
    )
    artifact_raw = canonical_json_bytes(artifact.model_dump(mode="json"))
    return F4TargetEvidenceReportBinding(
        ref=immutable(f"target-report/{report_id}", _sha256(artifact_raw)),
        artifact=artifact,
    )


def _read_input(path: str) -> tuple[F4TargetCanaryInput, str]:
    raw = Path(path).resolve(strict=True).read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F4TargetCanaryError("F4 target canary input is not canonical JSON")
    return F4TargetCanaryInput.model_validate_json(raw, strict=True), _sha256(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute the frozen six F4 config canaries through one BreadBoard lifecycle"
    )
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    spec, input_digest = _read_input(args.input)
    result = run_f4_target_canaries(spec, input_digest=input_digest)
    os.write(1, _component_report_line(result.report, result.report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "F4CleanupObservation",
    "F4ProductionBinding",
    "F4TargetCanaryError",
    "F4TargetCanaryInput",
    "F4TargetCanaryReport",
    "F4TargetCanaryRunResult",
    "F4TargetIdentity",
    "F4VariantExecution",
    "F4VariantTargetReport",
    "VARIANT_IDS",
    "build_campaign_target_report",
    "run_f4_target_canaries",
]
