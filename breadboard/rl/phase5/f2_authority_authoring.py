from __future__ import annotations

import dataclasses
from enum import Enum
import hashlib
import json
import os
import shutil
import socket
import subprocess
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from agentic_coder_prototype.compilation.bundle import ManifestReader, build_dependency_closure, ingest_member_map
from agentic_coder_prototype.compilation.contracts import CompileOptions, DependencyEdge, canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    InstalledV1,
    DNSPolicyDocumentV1,
    IPPolicyDocumentV1,
    PolicyHttpSchemaAuthorityV1,
    PolicySecretRouteBindingV1,
    PolicyHttpAuthorityGraphV1,
    SecretHandlesV1,
    OuterBridgePlanV1,
    PreboundServiceSocketPlanV1,
    ServerV1,
    CASConfigRuntimeStore,
    HmacSha256ReceiptAuthenticator,
    PinnedRevocationStore,
    PinnedServerCompilerAdapter,
    TlsCallbackRuntimeInputV1,
    EvidenceReceiptSigningAuthorityV1,
    EvidenceReceiptSigningHandoff,
)
from breadboard.rl.harness.config_runtime import ConfigRuntime
from breadboard.rl.harness.policy_http import (
    POLICY_HTTP_PROTOCOL_ABI,
    POLICY_HTTP_REQUEST_SCHEMA,
    POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
    POLICY_HTTP_RESPONSE_SCHEMA,
    POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
)
from breadboard.rl.harness.evidence import EvidenceRoleBindingV2, EvidenceRoleSourceV2
from breadboard.rl.state.cas import FilesystemCAS
from breadboard.rl.phase5.f2_composition import OpenSslAuthorityInput, StorePaths, TlsAuthorityInput, WrapperHostExecutablesInput

_DIGEST_PREFIX = "sha256:"
_COMPONENTS: tuple[tuple[str, type[c._ConfigRuntimeContract], str], ...] = (
    ("runners", c.RunnerRegistryRecord, "runner_registry_digest"),
    ("tools", c.ToolRegistryRecord, "tool_registry_digest"),
    ("setups", c.SetupRegistryRecord, "setup_registry_digest"),
    ("routes", c.RouteRegistryRecord, "route_registry_digest"),
    ("secret_handles", c.SecretHandleRegistryRecord, "secret_handle_registry_digest"),
    ("sandbox_runtimes", c.SandboxRuntimeRegistryRecord, "sandbox_runtime_registry_digest"),
    ("images", c.ImageRegistryRecord, "image_registry_digest"),
    ("repository_bindings", c.RepositoryBindingRegistryRecord, "repository_binding_registry_digest"),
    ("task_datasets", c.TaskDatasetRegistryRecord, "task_dataset_registry_digest"),
    ("models", c.ModelRegistryRecord, "model_registry_digest"),
    ("verifiers", c.VerifierRegistryRecord, "verifier_registry_digest"),
    ("evidence_policies", c.EvidencePolicyRegistryRecord, "evidence_policy_registry_digest"),
    ("retention_policies", c.RetentionPolicyRegistryRecord, "retention_policy_registry_digest"),
    ("policy_capability_attestations", c.PolicyCapabilityAttestationRecord, "policy_capability_registry_digest"),
)
_MEDIA = {
    "compiled_manifest": "application/vnd.breadboard.compiled-manifest+json;version=1",
    "admission_receipt": "application/vnd.breadboard.admission-receipt+json;version=1",
    "admitted_set": "application/vnd.breadboard.admitted-set+json;version=1",
    "direct_selector": "application/vnd.breadboard.direct-selector+json;version=1",
}


class F2AuthorityAuthoringError(ValueError):
    pass


def _parse_evidence_binding_wire(value: Any) -> Any:
    if type(value) is tuple and all(
        type(item) is EvidenceRoleBindingV2 for item in value
    ):
        return value
    if type(value) is not list:
        return value
    expected = {
        "schema_version",
        "role",
        "source",
        "producer_id",
        "producer_implementation_digest",
    }
    bindings: list[EvidenceRoleBindingV2] = []
    for item in value:
        if type(item) is not dict or set(item) != expected:
            raise ValueError("evidence role binding wire keys are not exact")
        if item["schema_version"] != "bb.rl.evidence-role-binding.v2":
            raise ValueError("evidence role binding schema is unsupported")
        if (
            type(item["role"]) is not str
            or not item["role"]
            or type(item["source"]) is not str
            or type(item["producer_id"]) is not str
            or not item["producer_id"]
            or type(item["producer_implementation_digest"]) is not str
        ):
            raise ValueError("evidence role binding wire values are not exact strings")
        bindings.append(
            EvidenceRoleBindingV2(
                role=item["role"],
                source=EvidenceRoleSourceV2(item["source"]),
                producer_id=item["producer_id"],
                producer_implementation_digest=item[
                    "producer_implementation_digest"
                ],
            )
        )
    return tuple(bindings)


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExternalArtifact(_ExactModel):
    path: str
    sha256: str
    media_type: str = "application/json"

    @model_validator(mode="after")
    def validate_identity(self) -> "ExternalArtifact":
        path = Path(self.path)
        if not path.is_absolute() or os.path.normpath(self.path) != self.path:
            raise ValueError("artifact path must be absolute and normalized")
        if not _is_digest(self.sha256):
            raise ValueError("artifact digest must be lowercase sha256")
        return self


class ReceiptSignerInput(_ExactModel):
    key_id: str
    secret_handle_id: str
    secret_path: str

    @field_validator("secret_path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if not Path(value).is_absolute() or os.path.normpath(value) != value:
            raise ValueError("signing secret path must be absolute and normalized")
        return value


class C4ModelIdentity(_ExactModel):
    model_digest: str
    tokenizer_digest: str
    checkpoint_digest: str

    @field_validator("model_digest", "tokenizer_digest", "checkpoint_digest")
    @classmethod
    def digests(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("model authority requires lowercase sha256")
        return value


class C4TaskIdentity(_ExactModel):
    task_contract_digest: str
    task_binding_digest: str

    @field_validator("task_contract_digest", "task_binding_digest")
    @classmethod
    def digests(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("task authority requires lowercase sha256")
        return value


class C4CallbackAuthority(_ExactModel):
    target_ip: str
    port: int = Field(ge=1, le=65535)
    protocol_abi: str
    owner_id: str
    secret_handle_id: str
    secret_handle_version_digest: str

    @model_validator(mode="after")
    def exact_protocol(self) -> "C4CallbackAuthority":
        if self.protocol_abi != POLICY_HTTP_PROTOCOL_ABI:
            raise ValueError("callback protocol ABI is not fixed C4")
        if not _is_digest(self.secret_handle_version_digest):
            raise ValueError("callback handle version requires lowercase sha256")
        return self


class C4PolicyAuthority(_ExactModel):
    subject: c.AuthenticatedSubject
    validity: c.ValidityWindow
    revocation: c.RevocationBinding
    receipt_ttl_seconds: int = Field(gt=0)
    evidence_policy_revision_digest: str
    retention_policy_revision_digest: str
    retention_minimum_seconds: int = Field(ge=0)
    retention_maximum_seconds: int = Field(gt=0)

    @field_validator("evidence_policy_revision_digest", "retention_policy_revision_digest")
    @classmethod
    def digests(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("policy revisions require lowercase sha256")
        return value


class F2C4SemanticInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-semantic-input.v1"]
    composition_id: str
    attempt_id: str
    prompt: str = Field(min_length=1, max_length=4096)
    shell_command: Literal["printf 'breadboard-f2-terminal-ok\\n' > /workspace/work/result.txt"]
    completion: Literal["F2 terminal episode complete"]
    tool_implementation_digest: str
    task: C4TaskIdentity
    model: C4ModelIdentity
    callback: C4CallbackAuthority
    policy: C4PolicyAuthority
    primary_runtime_id: str
    verifier_id: str
    resources: c.ResourceLimits
    limits: c.ExecutionLimits
    artifact_policy: c.ArtifactPolicyGrant
    installed: InstalledV1
    stores: dict[str, Any]
    outer_bridge_plan: OuterBridgePlanV1
    prebound_service_socket_plans: tuple[PreboundServiceSocketPlanV1, ...]
    server_request_timeout_seconds: float = Field(gt=0, le=600)
    secret_handles: SecretHandlesV1
    secret_files: dict[str, str]
    receipt_signer: ReceiptSignerInput
    evidence_bindings: tuple[EvidenceRoleBindingV2, ...]
    evidence_receipt_signing_authority: EvidenceReceiptSigningAuthorityV1
    callback_observation_signing_key_handle_id: str
    f1_prerequisite_id: Literal["20260711T203833Z-slurm-263537"]
    f1_prerequisite_report: ExternalArtifact
    f1_prerequisite_canonical_root: Literal["docs_tmp/ZYPHRA/RL_PHASE_5/evidence/target/F1/20260711T203833Z-slurm-263537"]
    host_runtime_root: Literal["/shared/breadboard-f2/host-runtime/07730b5d200c38171ae905345f1d21f9615ecc67fd565065bd88a69c42f14d91/runtime"]
    host_runtime_build_report: ExternalArtifact
    ibm_target_record: ExternalArtifact
    wrapper_image_build_report: ExternalArtifact
    wrapper_image_operator_authorization: ExternalArtifact
    wrapper_host_executables: WrapperHostExecutablesInput
    mount_broker_implementation: ExternalArtifact
    openssl: OpenSslAuthorityInput
    tls: TlsAuthorityInput

    @field_validator("evidence_bindings", mode="before")
    @classmethod
    def parse_evidence_bindings(cls, value: Any) -> Any:
        return _parse_evidence_binding_wire(value)

    @model_validator(mode="after")
    def exact_c4(self) -> "F2C4SemanticInput":
        if self.policy.subject.authority_scope_digest != self.policy.revocation.scope_digest:
            raise ValueError("subject and revocation scope differ")
        if self.callback.secret_handle_id == self.receipt_signer.secret_handle_id:
            raise ValueError("callback and receipt signer handles must be distinct")
        if self.receipt_signer.secret_handle_id not in self.secret_files:
            raise ValueError("receipt signing handle has no supplied secret descriptor")
        if self.secret_files[self.receipt_signer.secret_handle_id] != self.receipt_signer.secret_path:
            raise ValueError("receipt signing descriptor does not match secret_files")
        if self.callback.secret_handle_id not in self.secret_files:
            raise ValueError("callback handle has no supplied secret descriptor")
        if self.evidence_receipt_signing_authority.evidence_policy_digest != self.policy.evidence_policy_revision_digest:
            raise ValueError("evidence receipt signer does not bind semantic evidence policy")
        observation_handles = tuple(item for item in self.secret_handles.records if item.handle_id == self.callback_observation_signing_key_handle_id)
        if len(observation_handles) != 1 or observation_handles[0].purpose != "callback_observation_signing_key":
            raise ValueError("semantic callback observation signer is not exact")
        if self.artifact_policy.max_each_bytes != self.limits.artifact_bytes_each or self.artifact_policy.max_total_bytes != self.limits.artifact_bytes_total:
            raise ValueError("artifact policy and execution limits differ")
        if self.artifact_policy.allowed_roles != ("terminal-result",) or self.limits.max_turns != 1:
            raise ValueError("C4 artifact and turn policy is not exact")
        if self.f1_prerequisite_report.sha256 != "sha256:eaa3a09e8c396946fe82036f3bbf0d778503a647e190627b2ad7f944a2f16f59":
            raise ValueError("F1 prerequisite report is not approved")
        if self.host_runtime_build_report.sha256 != "sha256:e6428360047ed4d3c94cb4910e6ea4cfa6ebbf0e4fcd18eb2e2e679162b56431":
            raise ValueError("host runtime report is not authoritative t0230")
        socket_roles = tuple(item.role for item in self.prebound_service_socket_plans)
        if socket_roles != _SOCKET_ROLES:
            raise ValueError("C4 requires exact callback, policy, and harness socket plans")
        if any(item.gateway != self.outer_bridge_plan.gateway for item in self.prebound_service_socket_plans):
            raise ValueError("prebound service socket plan gateway mismatch")
        callback_socket = next(item for item in self.prebound_service_socket_plans if item.role == "callback_tls")
        if self.callback.target_ip != self.outer_bridge_plan.gateway or self.callback.port != callback_socket.observed_port:
            raise ValueError("callback authority does not bind gateway TLS socket plan")
        return self


class C4StaticPolicyAuthority(_ExactModel):
    receipt_ttl_seconds: int = Field(gt=0)
    evidence_policy_revision_digest: str
    retention_policy_revision_digest: str
    retention_minimum_seconds: int = Field(ge=0)
    retention_maximum_seconds: int = Field(gt=0)

    @field_validator("evidence_policy_revision_digest", "retention_policy_revision_digest")
    @classmethod
    def digests(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("policy revisions require lowercase sha256")
        return value


class F2C4StaticAuthorityInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-static-authority-input.v1"]
    prompt: str = Field(min_length=1, max_length=4096)
    tool_implementation_digest: str
    task: C4TaskIdentity
    model: C4ModelIdentity
    primary_runtime_id: str
    verifier_id: str
    resources: c.ResourceLimits
    limits: c.ExecutionLimits
    artifact_policy: c.ArtifactPolicyGrant
    policy: C4StaticPolicyAuthority
    f1_prerequisite_id: Literal["20260711T203833Z-slurm-263537"]
    f1_prerequisite_report: ExternalArtifact
    f1_prerequisite_canonical_root: Literal["docs_tmp/ZYPHRA/RL_PHASE_5/evidence/target/F1/20260711T203833Z-slurm-263537"]
    host_runtime_root: Literal["/shared/breadboard-f2/host-runtime/07730b5d200c38171ae905345f1d21f9615ecc67fd565065bd88a69c42f14d91/runtime"]
    host_runtime_build_report: ExternalArtifact
    ibm_target_record: ExternalArtifact
    wrapper_image_build_report: ExternalArtifact
    wrapper_image_operator_authorization: ExternalArtifact
    wrapper_host_executables: WrapperHostExecutablesInput
    mount_broker_implementation: ExternalArtifact
    openssl: OpenSslAuthorityInput

    @model_validator(mode="after")
    def exact_static_authority(self) -> "F2C4StaticAuthorityInput":
        if self.f1_prerequisite_report.sha256 != "sha256:eaa3a09e8c396946fe82036f3bbf0d778503a647e190627b2ad7f944a2f16f59":
            raise ValueError("F1 prerequisite report is not approved")
        if self.host_runtime_build_report.sha256 != "sha256:e6428360047ed4d3c94cb4910e6ea4cfa6ebbf0e4fcd18eb2e2e679162b56431":
            raise ValueError("host runtime report is not authoritative t0230")
        if self.artifact_policy.allowed_roles != ("terminal-result",) or self.limits.max_turns != 1:
            raise ValueError("static C4 artifact and turn policy is not exact")
        return self


class F2C4DynamicAuthorityInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-dynamic-authority-input.v1"]
    composition_id: str
    attempt_id: str
    callback: C4CallbackAuthority
    subject: c.AuthenticatedSubject
    validity: c.ValidityWindow
    revocation: c.RevocationBinding
    installed: InstalledV1
    stores: StorePaths
    outer_bridge_plan: OuterBridgePlanV1
    prebound_service_socket_plans: tuple[PreboundServiceSocketPlanV1, ...]
    server_request_timeout_seconds: float = Field(gt=0, le=600)
    secret_handles: SecretHandlesV1
    secret_files: dict[str, str]
    receipt_signer: ReceiptSignerInput
    evidence_bindings: tuple[EvidenceRoleBindingV2, ...]
    tls_private_key_secret_handle_id: str
    tls_leaf_public_key_sha256: str
    broker_implementation_digest: str
    callback_observation_signing_key_handle_id: str
    callback_observation_evidence_policy_revision_digest: str
    callback_observation_route_id: Literal["f2-fixed-policy-callback"]
    evidence_receipt_signing_authority: EvidenceReceiptSigningAuthorityV1
    tls: TlsAuthorityInput


    @field_validator("evidence_bindings", mode="before")
    @classmethod
    def parse_evidence_bindings(cls, value: Any) -> Any:
        return _parse_evidence_binding_wire(value)

    @model_validator(mode="after")
    def exact_dynamic_authority(self) -> "F2C4DynamicAuthorityInput":
        if not _is_digest(self.tls_leaf_public_key_sha256) or not _is_digest(self.broker_implementation_digest):
            raise ValueError("dynamic public digests must be lowercase sha256")
        matching = tuple(item for item in self.secret_handles.records if item.handle_id == self.tls_private_key_secret_handle_id)
        if len(matching) != 1 or matching[0].purpose != "callback_tls_private_key":
            raise ValueError("dynamic TLS private key handle is missing")
        if self.tls_private_key_secret_handle_id in self.secret_files:
            raise ValueError("dynamic TLS private key path must not be persisted")
        observation_handles = tuple(item for item in self.secret_handles.records if item.handle_id == self.callback_observation_signing_key_handle_id)
        if (
            len(observation_handles) != 1
            or observation_handles[0].purpose != "callback_observation_signing_key"
            or observation_handles[0].route_ids != ()
            or self.callback_observation_signing_key_handle_id in self.secret_files
            or len({
                self.callback_observation_signing_key_handle_id,
                self.tls_private_key_secret_handle_id,
                self.receipt_signer.secret_handle_id,
                self.callback.secret_handle_id,
            }) != 4
        ):
            raise ValueError("callback observation signing authority is not exact and distinct")
        if not _is_digest(self.callback_observation_evidence_policy_revision_digest):
            raise ValueError("callback observation evidence policy digest is invalid")
        receipt_authority = self.evidence_receipt_signing_authority
        receipt_handles = tuple(item for item in self.secret_handles.records if item.handle_id == receipt_authority.private_key_secret_handle_id)
        if (
            receipt_authority.attempt_id != self.attempt_id
            or len(receipt_handles) != 1
            or receipt_handles[0].purpose != "evidence_receipt_signing_key"
            or receipt_handles[0].route_ids != ()
            or receipt_authority.private_key_secret_handle_id in self.secret_files
            or len({
                receipt_authority.private_key_secret_handle_id,
                self.callback_observation_signing_key_handle_id,
                self.tls_private_key_secret_handle_id,
                self.receipt_signer.secret_handle_id,
                self.callback.secret_handle_id,
            }) != 5
        ):
            raise ValueError("evidence receipt signing authority is not exact and distinct")
        socket_roles = tuple(item.role for item in self.prebound_service_socket_plans)
        callback_socket = next((item for item in self.prebound_service_socket_plans if item.role == "callback_tls"), None)
        if (
            socket_roles != _SOCKET_ROLES
            or any(item.gateway != self.outer_bridge_plan.gateway for item in self.prebound_service_socket_plans)
            or callback_socket is None
            or self.callback.target_ip != self.outer_bridge_plan.gateway
            or self.callback.port != callback_socket.observed_port
            or self.tls.target_ip != self.outer_bridge_plan.gateway
        ):
            raise ValueError("dynamic callback authority does not bind gateway TLS socket plan")
        return self


class F2C4TargetDynamicPlanInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-target-dynamic-plan.v1"]
    composition_id: str
    attempt_id: str
    callback_owner_id: str
    callback_credential_handle_id: str
    subject: c.AuthenticatedSubject
    outer_bridge_plan: OuterBridgePlanV1
    server_request_timeout_seconds: float = Field(gt=0, le=600)


class F2C4TargetDynamicObservations(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-target-dynamic-observations.v1"]
    attempt_id: str
    callback_observed_port: int = Field(ge=1, le=65535)
    callback_secret_handle_version_digest: str
    validity: c.ValidityWindow
    revocation: c.RevocationBinding
    installed: InstalledV1
    stores: StorePaths
    prebound_service_socket_plans: tuple[PreboundServiceSocketPlanV1, ...]
    secret_handles: SecretHandlesV1
    secret_files: dict[str, str]
    receipt_signer: ReceiptSignerInput
    tls_private_key_secret_handle_id: str
    tls_leaf_public_key_sha256: str
    evidence_bindings: tuple[EvidenceRoleBindingV2, ...]
    tls: TlsAuthorityInput
    broker_implementation_digest: str
    callback_observation_signing_key_handle_id: str
    callback_observation_evidence_policy_revision_digest: str

    callback_observation_route_id: Literal["f2-fixed-policy-callback"]
    evidence_receipt_signing_authority: EvidenceReceiptSigningAuthorityV1
    @field_validator("evidence_bindings", mode="before")
    @classmethod
    def parse_evidence_bindings(cls, value: Any) -> Any:
        return _parse_evidence_binding_wire(value)

    @field_validator(
        "callback_secret_handle_version_digest", "tls_leaf_public_key_sha256",
        "broker_implementation_digest",
        "callback_observation_evidence_policy_revision_digest",
    )
    @classmethod
    def digests(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("dynamic observation digest must be lowercase sha256")
        return value


def author_f2_target_dynamic_authority(
    plan: F2C4TargetDynamicPlanInput,
    observations: F2C4TargetDynamicObservations,
) -> F2C4DynamicAuthorityInput:
    """Cross-bind same-process target observations without serializing live FDs."""
    if not isinstance(plan, F2C4TargetDynamicPlanInput) or not isinstance(observations, F2C4TargetDynamicObservations):
        raise TypeError("target dynamic authoring requires exact typed plan and observations")
    if plan.attempt_id != observations.attempt_id:
        raise F2AuthorityAuthoringError("dynamic observation attempt mismatch")
    if plan.subject.authority_scope_digest != observations.revocation.scope_digest:
        raise F2AuthorityAuthoringError("dynamic subject and revocation scope mismatch")
    if observations.tls.target_ip != plan.outer_bridge_plan.gateway or observations.tls.route_id != _ROUTE_ID:
        raise F2AuthorityAuthoringError("dynamic TLS authority does not bind bridge gateway")
    socket_roles = tuple(item.role for item in observations.prebound_service_socket_plans)
    if socket_roles != _SOCKET_ROLES:
        raise F2AuthorityAuthoringError("dynamic socket plans require exact callback, policy, and harness roles")
    if any(item.gateway != plan.outer_bridge_plan.gateway for item in observations.prebound_service_socket_plans):
        raise F2AuthorityAuthoringError("dynamic socket plan gateway mismatch")
    callback_socket = next(item for item in observations.prebound_service_socket_plans if item.role == "callback_tls")
    if callback_socket.observed_port != observations.callback_observed_port:
        raise F2AuthorityAuthoringError("dynamic callback port does not bind callback TLS socket plan")
    tls_handles = tuple(
        item for item in observations.secret_handles.records
        if item.handle_id == observations.tls_private_key_secret_handle_id
    )
    if len(tls_handles) != 1 or tls_handles[0].purpose != "callback_tls_private_key":
        raise F2AuthorityAuthoringError("dynamic TLS private key handle is missing")
    if observations.tls_private_key_secret_handle_id in observations.secret_files:
        raise F2AuthorityAuthoringError("TLS private key path must not be persisted")
    observation_handles = tuple(
        item for item in observations.secret_handles.records
        if item.handle_id == observations.callback_observation_signing_key_handle_id
    )
    if (
        len(observation_handles) != 1
        or observation_handles[0].purpose != "callback_observation_signing_key"
        or observation_handles[0].route_ids != ()
        or observations.callback_observation_signing_key_handle_id in observations.secret_files
        or len({
            observations.callback_observation_signing_key_handle_id,
            observations.tls_private_key_secret_handle_id,
            observations.receipt_signer.secret_handle_id,
            plan.callback_credential_handle_id,
        }) != 4
    ):
        raise F2AuthorityAuthoringError("callback observation signing handle is not exact and distinct")
    receipt_authority = observations.evidence_receipt_signing_authority
    receipt_handles = tuple(
        item for item in observations.secret_handles.records
        if item.handle_id == receipt_authority.private_key_secret_handle_id
    )
    if (
        receipt_authority.attempt_id != observations.attempt_id
        or len(receipt_handles) != 1
        or receipt_handles[0].purpose != "evidence_receipt_signing_key"
        or receipt_handles[0].route_ids != ()
        or receipt_authority.private_key_secret_handle_id in observations.secret_files
        or len({
            receipt_authority.private_key_secret_handle_id,
            observations.callback_observation_signing_key_handle_id,
            observations.tls_private_key_secret_handle_id,
            observations.receipt_signer.secret_handle_id,
            plan.callback_credential_handle_id,
        }) != 5
    ):
        raise F2AuthorityAuthoringError("evidence receipt signing authority is not exact and distinct")
    callback = C4CallbackAuthority(
        target_ip=plan.outer_bridge_plan.gateway,
        port=observations.callback_observed_port,
        protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
        owner_id=plan.callback_owner_id,
        secret_handle_id=plan.callback_credential_handle_id,
        secret_handle_version_digest=observations.callback_secret_handle_version_digest,
    )
    return F2C4DynamicAuthorityInput(
        schema_version="bb.rl.phase5-f2-c4-dynamic-authority-input.v1",
        composition_id=plan.composition_id, attempt_id=plan.attempt_id,
        callback=callback, subject=plan.subject, validity=observations.validity,
        revocation=observations.revocation, installed=observations.installed,
        stores=observations.stores, outer_bridge_plan=plan.outer_bridge_plan,
        tls_private_key_secret_handle_id=observations.tls_private_key_secret_handle_id,
        tls_leaf_public_key_sha256=observations.tls_leaf_public_key_sha256,
        broker_implementation_digest=observations.broker_implementation_digest,
        evidence_receipt_signing_authority=observations.evidence_receipt_signing_authority,
        callback_observation_signing_key_handle_id=observations.callback_observation_signing_key_handle_id,
        callback_observation_evidence_policy_revision_digest=observations.callback_observation_evidence_policy_revision_digest,
        prebound_service_socket_plans=observations.prebound_service_socket_plans,
        callback_observation_route_id=observations.callback_observation_route_id,
        server_request_timeout_seconds=plan.server_request_timeout_seconds,
        secret_handles=observations.secret_handles, secret_files=observations.secret_files,
        receipt_signer=observations.receipt_signer,
        evidence_bindings=observations.evidence_bindings, tls=observations.tls,
    )
@dataclasses.dataclass(frozen=True, slots=True)
class CallbackObservationSigningKeyHandoffV1:
    handle_id: str
    path: str
    descriptor_fd: int
    device: int
    inode: int
    ctime_ns: int
    size_bytes: int
    mode: int
    owner_uid: int
    key_sha256: str

    def validate_against(self, dynamic: F2C4DynamicAuthorityInput) -> None:
        if self.handle_id != dynamic.callback_observation_signing_key_handle_id or self.descriptor_fd < 0:
            raise F2AuthorityAuthoringError("callback observation signing handoff identity mismatch")
        try:
            info = os.fstat(self.descriptor_fd)
        except OSError as exc:
            raise F2AuthorityAuthoringError("callback observation signing descriptor is no longer live") from exc
        linked = os.stat(self.path, follow_symlinks=False)
        actual = (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size, stat.S_IMODE(info.st_mode), info.st_uid)
        expected = (self.device, self.inode, self.ctime_ns, self.size_bytes, self.mode, self.owner_uid)
        if (
            actual != expected
            or (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino)
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size < 32
        ):
            raise F2AuthorityAuthoringError("callback observation signing descriptor is not exact 0400 secret")
        hasher = hashlib.sha256()
        offset = 0
        while offset < info.st_size:
            chunk = os.pread(self.descriptor_fd, min(4096, info.st_size - offset), offset)
            if not chunk:
                raise F2AuthorityAuthoringError("callback observation signing key was truncated")
            hasher.update(chunk)
            offset += len(chunk)
        if _DIGEST_PREFIX + hasher.hexdigest() != self.key_sha256:
            raise F2AuthorityAuthoringError("callback observation signing key digest mismatch")



@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceReceiptSigningKeyRuntimeHandoffV1:
    path: str
    handoff: EvidenceReceiptSigningHandoff
    openssl: OpenSslAuthorityInput

    def validate_against(
        self,
        dynamic: F2C4DynamicAuthorityInput,
        *,
        composition_digest: str,
        evidence_policy_digest: str,
        openssl_authority_digest: str,
    ) -> None:
        if not os.path.isabs(self.path) or os.path.normpath(self.path) != self.path:
            raise F2AuthorityAuthoringError("evidence receipt private-key path is invalid")
        try:
            descriptor_metadata = os.fstat(self.handoff.private_key_fd)
            path_metadata = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise F2AuthorityAuthoringError("evidence receipt private-key path is not live") from exc
        if (
            not stat.S_ISREG(path_metadata.st_mode)
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise F2AuthorityAuthoringError("evidence receipt private-key path identity mismatch")
        authority = self.handoff.authority
        if (
            authority != dynamic.evidence_receipt_signing_authority
            or authority.attempt_id != dynamic.attempt_id
            or authority.composition_digest != composition_digest
            or authority.evidence_policy_digest != evidence_policy_digest
            or authority.openssl_authority_digest != openssl_authority_digest
        ):
            raise F2AuthorityAuthoringError("evidence receipt signing handoff authority mismatch")
        matching = tuple(
            item for item in dynamic.secret_handles.records
            if item.handle_id == authority.private_key_secret_handle_id
        )
        if (
            len(matching) != 1
            or matching[0].purpose != "evidence_receipt_signing_key"
            or authority.private_key_secret_handle_id in dynamic.secret_files
        ):
            raise F2AuthorityAuthoringError("evidence receipt private-key handle is not live-only")
        try:
            self.handoff.validate_live()
        except (OSError, ValueError) as exc:
            raise F2AuthorityAuthoringError("evidence receipt private-key descriptor is not live") from exc
        _verify_executable_observation(self.openssl)
        _verify_external_artifact(ExternalArtifact(
            path=authority.public_key_ref.path,
            sha256=authority.public_key_sha256,
            media_type=authority.public_key_ref.media_type,
        ))
        if os.stat(authority.public_key_ref.path, follow_symlinks=False).st_size != authority.public_key_ref.size_bytes:
            raise F2AuthorityAuthoringError("evidence receipt public-key size mismatch")
        private_public = subprocess.run(
            [
                self.openssl.path, "pkey", "-in",
                f"/dev/fd/{self.handoff.private_key_fd}",
                "-pubout", "-outform", "DER",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            timeout=10,
            pass_fds=(self.handoff.private_key_fd,),
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        public = subprocess.run(
            [
                self.openssl.path, "pkey", "-pubin", "-in",
                authority.public_key_ref.path,
                "-outform", "DER",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            timeout=10,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        if (
            private_public.returncode != 0
            or public.returncode != 0
            or private_public.stdout != public.stdout
            or len(public.stdout) != 44
            or not public.stdout.startswith(bytes.fromhex("302a300506032b6570032100"))
            or _digest(public.stdout) != authority.public_key_spki_sha256
        ):
            raise F2AuthorityAuthoringError("evidence receipt private/public Ed25519 authority mismatch")


@dataclasses.dataclass(frozen=True, slots=True)
class TlsPrivateKeyRuntimeHandoffV1:
    path: str
    descriptor_fd: int
    device: int
    inode: int
    ctime_ns: int
    size_bytes: int
    mode: int
    owner_uid: int
    private_key_sha256: str
    leaf_certificate_sha256: str
    leaf_public_key_sha256: str

    def validate_live(self) -> None:
        if not os.path.isabs(self.path) or os.path.normpath(self.path) != self.path or self.descriptor_fd < 0:
            raise F2AuthorityAuthoringError("TLS key handoff path or descriptor is invalid")
        try:
            info = os.fstat(self.descriptor_fd)
        except OSError as exc:
            raise F2AuthorityAuthoringError("TLS key handoff descriptor is no longer live") from exc
        linked = os.stat(self.path, follow_symlinks=False)
        actual = (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size, stat.S_IMODE(info.st_mode), info.st_uid)
        expected = (self.device, self.inode, self.ctime_ns, self.size_bytes, self.mode, self.owner_uid)
        if (
            actual != expected
            or (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino)
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o400
        ):
            raise F2AuthorityAuthoringError("TLS key handoff descriptor identity mismatch")
        hasher = hashlib.sha256()
        offset = 0
        while offset < info.st_size:
            chunk = os.pread(self.descriptor_fd, min(1024 * 1024, info.st_size - offset), offset)
            if not chunk:
                raise F2AuthorityAuthoringError("TLS key handoff was truncated")
            hasher.update(chunk)
            offset += len(chunk)
        if _DIGEST_PREFIX + hasher.hexdigest() != self.private_key_sha256:
            raise F2AuthorityAuthoringError("TLS private key digest mismatch")


@dataclasses.dataclass(frozen=True, slots=True)
class TlsCallbackSocketRuntimeHandoffV1:
    descriptor_fd: int
    gateway: str
    observed_port: int
    socket_device: int
    socket_inode: int
    socket_mode: int
    socket_owner_uid: int

    def validate_live(self) -> None:
        if self.descriptor_fd < 0:
            raise F2AuthorityAuthoringError("callback socket descriptor is invalid")
        try:
            info = os.fstat(self.descriptor_fd)
        except OSError as exc:
            raise F2AuthorityAuthoringError("callback socket descriptor is no longer live") from exc
        if (
            not stat.S_ISSOCK(info.st_mode)
            or (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
            != (self.socket_device, self.socket_inode, self.socket_mode, self.socket_owner_uid)
        ):
            raise F2AuthorityAuthoringError("callback socket handoff descriptor identity mismatch")
        duplicate = socket.fromfd(self.descriptor_fd, socket.AF_INET, socket.SOCK_STREAM)
        try:
            if duplicate.getsockname() != (self.gateway, self.observed_port):
                raise F2AuthorityAuthoringError("callback socket handoff endpoint mismatch")
        finally:
            duplicate.close()


@dataclasses.dataclass(frozen=True, slots=True)
class TlsCallbackLiveHandoffV1:
    runtime_input: TlsCallbackRuntimeInputV1
    tls_private_key: TlsPrivateKeyRuntimeHandoffV1
    callback_socket: TlsCallbackSocketRuntimeHandoffV1

    def validate_against(self, dynamic: F2C4DynamicAuthorityInput) -> None:
        self.tls_private_key.validate_live()
        self.callback_socket.validate_live()
        matching_handles = tuple(
            item for item in dynamic.secret_handles.records
            if item.handle_id == self.runtime_input.private_key_secret_handle_id
        )
        callback_plans = tuple(
            item for item in dynamic.prebound_service_socket_plans
            if item.role == "callback_tls"
        )
        runtime = self.runtime_input
        callback_plan = callback_plans[0] if len(callback_plans) == 1 else None
        if (
            runtime.route_id != dynamic.tls.route_id
            or runtime.host != dynamic.outer_bridge_plan.gateway
            or runtime.host != dynamic.callback.target_ip
            or runtime.observed_port != dynamic.callback.port
            or runtime.socket_role != "callback_tls"
            or callback_plan is None
            or runtime.socket_plan_id != callback_plan.socket_plan_id
            or callback_plan.observed_port != runtime.observed_port
            or runtime.ca_certificate_sha256 != dynamic.tls.ca_certificate.sha256
            or runtime.leaf_certificate_sha256 != dynamic.tls.leaf_certificate.sha256
            or runtime.leaf_public_key_sha256 != dynamic.tls_leaf_public_key_sha256
            or self.tls_private_key.leaf_certificate_sha256 != runtime.leaf_certificate_sha256
            or self.tls_private_key.leaf_public_key_sha256 != runtime.leaf_public_key_sha256
            or self.callback_socket.gateway != callback_plan.gateway
            or self.callback_socket.observed_port != callback_plan.observed_port
            or self.callback_socket.socket_device != callback_plan.socket_device
            or self.callback_socket.socket_inode != callback_plan.socket_inode
            or self.callback_socket.socket_mode != callback_plan.socket_mode
            or self.callback_socket.socket_owner_uid != callback_plan.socket_owner_uid
            or len(matching_handles) != 1
            or matching_handles[0].purpose != "callback_tls_private_key"
        ):
            raise F2AuthorityAuthoringError("live callback runtime does not bind dynamic authority")


class F2C4StaticAuthorityFragment(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-static-authority-fragment.v1"]
    authority: F2C4StaticAuthorityInput
    authority_digest: str
    source_inventory: tuple[dict[str, Any], ...]

    @model_validator(mode="after")
    def exact_digest(self) -> "F2C4StaticAuthorityFragment":
        if self.authority_digest != _digest(canonical_json_bytes(self.authority.model_dump(mode="json"))):
            raise ValueError("static authority fragment digest mismatch")
        return self




def _is_digest(value: str) -> bool:
    return len(value) == 71 and value.startswith(_DIGEST_PREFIX) and all(ch in "0123456789abcdef" for ch in value[7:])


def _digest(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _read_canonical_input(path: str) -> bytes:
    source = Path(path)
    before = source.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise F2AuthorityAuthoringError("semantic input must be a regular file")
    raw = source.read_bytes()
    after = source.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise F2AuthorityAuthoringError("semantic input changed while reading")
    value = json.loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F2AuthorityAuthoringError("semantic input must be canonical JSON")
    return raw


def _verify_external_artifact(artifact: ExternalArtifact) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(artifact.path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise F2AuthorityAuthoringError("external authority must be a regular file")
        hasher = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise F2AuthorityAuthoringError("external authority was truncated")
            hasher.update(chunk)
            remaining -= len(chunk)
        linked = os.stat(artifact.path, follow_symlinks=False)
        if (linked.st_dev, linked.st_ino, linked.st_size, linked.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            raise F2AuthorityAuthoringError("external authority changed while reading")
        if _DIGEST_PREFIX + hasher.hexdigest() != artifact.sha256:
            raise F2AuthorityAuthoringError("external authority digest mismatch")
    finally:
        os.close(fd)


def _read_secret_0400(path: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o400:
            raise F2AuthorityAuthoringError("receipt signer must be a regular 0400 secret handle")
        data = os.read(fd, 4097)
        if not data or len(data) > 4096:
            raise F2AuthorityAuthoringError("receipt signer secret length is invalid")
        linked = os.stat(path, follow_symlinks=False)
        if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
            raise F2AuthorityAuthoringError("receipt signer descriptor identity changed")
        return data
    finally:
        os.close(fd)


def _wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _wire(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _write_exclusive(directory_fd: int, name: str, payload: bytes, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, mode, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short authority write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def materialize_f2_c4_semantic_input(
    static_fragment_path: str,
    dynamic: F2C4DynamicAuthorityInput,
    output_path: str,
) -> str:
    """Merge one reviewed static authority with typed same-job observations."""
    if not isinstance(dynamic, F2C4DynamicAuthorityInput):
        raise TypeError("dynamic authority must be F2C4DynamicAuthorityInput")
    fragment_bytes = _read_canonical_input(static_fragment_path)
    fragment = F2C4StaticAuthorityFragment.model_validate_json(fragment_bytes, strict=True)
    _validate_static_authority(fragment.authority)
    fixed = fragment.authority
    if dynamic.broker_implementation_digest != fixed.mount_broker_implementation.sha256:
        raise F2AuthorityAuthoringError("dynamic broker observation does not bind reviewed implementation")
    if dynamic.callback_observation_evidence_policy_revision_digest != fixed.policy.evidence_policy_revision_digest:
        raise F2AuthorityAuthoringError("callback observation signer does not bind reviewed evidence policy")
    if dynamic.evidence_receipt_signing_authority.evidence_policy_digest != fixed.policy.evidence_policy_revision_digest:
        raise F2AuthorityAuthoringError("evidence receipt signer does not bind reviewed evidence policy")
    policy = C4PolicyAuthority(
        subject=dynamic.subject,
        validity=dynamic.validity,
        revocation=dynamic.revocation,
        receipt_ttl_seconds=fixed.policy.receipt_ttl_seconds,
        evidence_policy_revision_digest=fixed.policy.evidence_policy_revision_digest,
        retention_policy_revision_digest=fixed.policy.retention_policy_revision_digest,
        retention_minimum_seconds=fixed.policy.retention_minimum_seconds,
        retention_maximum_seconds=fixed.policy.retention_maximum_seconds,
    )
    model = F2C4SemanticInput(
        schema_version="bb.rl.phase5-f2-c4-semantic-input.v1",
        composition_id=dynamic.composition_id, attempt_id=dynamic.attempt_id,
        prompt=fixed.prompt, shell_command=_TASK_COMMAND,
        completion="F2 terminal episode complete",
        tool_implementation_digest=fixed.tool_implementation_digest,
        task=fixed.task, model=fixed.model, callback=dynamic.callback, policy=policy,
        primary_runtime_id=fixed.primary_runtime_id, verifier_id=fixed.verifier_id,
        resources=fixed.resources, limits=fixed.limits, artifact_policy=fixed.artifact_policy,
        installed=dynamic.installed, stores=dynamic.stores.model_dump(mode="json"),
        outer_bridge_plan=dynamic.outer_bridge_plan,
        prebound_service_socket_plans=dynamic.prebound_service_socket_plans,
        server_request_timeout_seconds=dynamic.server_request_timeout_seconds,
        secret_handles=dynamic.secret_handles, secret_files=dynamic.secret_files,
        receipt_signer=dynamic.receipt_signer, evidence_bindings=dynamic.evidence_bindings,
        evidence_receipt_signing_authority=dynamic.evidence_receipt_signing_authority,
        callback_observation_signing_key_handle_id=dynamic.callback_observation_signing_key_handle_id,
        f1_prerequisite_id=fixed.f1_prerequisite_id,
        f1_prerequisite_report=fixed.f1_prerequisite_report,
        f1_prerequisite_canonical_root=fixed.f1_prerequisite_canonical_root,
        host_runtime_root=fixed.host_runtime_root,
        host_runtime_build_report=fixed.host_runtime_build_report,
        ibm_target_record=fixed.ibm_target_record,
        wrapper_image_build_report=fixed.wrapper_image_build_report,
        wrapper_image_operator_authorization=fixed.wrapper_image_operator_authorization,
        wrapper_host_executables=fixed.wrapper_host_executables,
        mount_broker_implementation=fixed.mount_broker_implementation,
        openssl=fixed.openssl, tls=dynamic.tls,
    )
    path = Path(output_path)
    if not path.is_absolute() or os.path.normpath(output_path) != output_path:
        raise F2AuthorityAuthoringError("semantic output path must be absolute and normalized")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        _write_exclusive(directory_fd, path.name, canonical_json_bytes(model.model_dump(mode="json")))
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return os.fspath(path)


def _source(path: Path, payload: bytes, media_type: str) -> dict[str, Any]:
    return {"path": os.fspath(path.resolve()), "sha256": _digest(payload), "media_type": media_type}


_TASK_COMMAND = "printf 'breadboard-f2-terminal-ok\\n' > /workspace/work/result.txt"
_TASK_OUTPUT = b"breadboard-f2-terminal-ok\n"
_RESULT_DIGEST = "sha256:67bc43eaaf2578ed3bfe753462d197498062ac0ea2aedf5449903c61352bdb21"
_TOOL_ID = "shell"
_ROUTE_ID = "f2-fixed-policy-callback"
_SOCKET_ROLES = ("callback_tls", "fixed_policy", "harness")
_MODEL_ID = "breadboard-fixed-f2"
_POLICY_SLOT_ID = "fixed-real-policy"
_EVIDENCE_POLICY_ID = "phase5-f2-evidence"
_RETENTION_POLICY_ID = "phase5-f2-retention"
_ARGUMENT_SCHEMA = {"type": "object", "properties": {"command": {"const": _TASK_COMMAND, "type": "string"}}, "required": ["command"], "additionalProperties": False}
_RESULT_SCHEMA = {"type": "object", "properties": {"passed": {"type": "boolean"}, "reward": {"enum": [0, 1], "type": "number"}}, "required": ["passed", "reward"], "additionalProperties": False}


def _registry_from_records(records: dict[str, tuple[c._ConfigRuntimeContract, ...]]) -> c.RegistrySnapshotSet:
    digests: dict[str, str] = {}
    for component, _, digest_field in _COMPONENTS:
        digests[digest_field] = c.RegistrySnapshotSet.derive_component_digest(component, records[component])
    digests["snapshot_digest"] = c.RegistrySnapshotSet.derive_snapshot_digest(dict(digests))
    return c.RegistrySnapshotSet(digests=c.RegistryDigestSet(**digests), **records)


def _derive_c4(spec: F2C4SemanticInput) -> tuple[c.CapabilityVector, c.RegistrySnapshotSet, tuple[c.PolicyCapabilityObservation, ...], PolicyHttpAuthorityGraphV1, c.OperatorCeiling]:
    if _digest(_TASK_OUTPUT) != _RESULT_DIGEST or len(_TASK_OUTPUT) != 26:
        raise AssertionError("fixed C4 output contract is corrupt")
    if len(spec.installed.runner_adapters) != 1:
        raise F2AuthorityAuthoringError("C4 requires exactly one installed runner adapter")
    runner_desc = spec.installed.runner_adapters[0]
    runner = c.RunnerGrant(adapter_id=runner_desc.adapter_id, runtime_abi=runner_desc.runtime_abi, implementation_digest=runner_desc.implementation_digest)
    tool = c.ToolGrant(tool_id=_TOOL_ID, implementation_digest=spec.tool_implementation_digest, capability_ids=("terminal.command",))
    installed = spec.installed
    if (
        len(installed.runtimes) != 2
        or len(installed.images) != 2
        or len(installed.security_policies) != 2
        or len(installed.network_policies) != 1
        or len(installed.verifiers) != 1
    ):
        raise F2AuthorityAuthoringError("C4 requires exact primary and verifier sandbox authorities")
    primary = next((item for item in installed.runtimes if item.runtime_id == spec.primary_runtime_id), None)
    verifier_install = next((item for item in installed.verifiers if item.grant.verifier_id == spec.verifier_id), None)
    if primary is None or verifier_install is None:
        raise F2AuthorityAuthoringError("primary runtime or verifier is not installed")
    verifier_runtime = next((item for item in installed.runtimes if item.runtime_id == verifier_install.runtime_id), None)
    if (
        verifier_runtime is None
        or primary.runtime_class != c.RuntimeClass.HARDENED_DOCKER
        or verifier_runtime.runtime_class != c.RuntimeClass.HARDENED_DOCKER
        or verifier_install.runtime_class != c.RuntimeClass.HARDENED_DOCKER
        or primary.runtime_id == verifier_runtime.runtime_id
    ):
        raise F2AuthorityAuthoringError("C4 primary and verifier runtimes must be distinct hardened Docker authorities")
    primary_images = tuple(item for item in installed.images if item.runtime_id == primary.runtime_id)
    verifier_images = tuple(item for item in installed.images if item.runtime_id == verifier_runtime.runtime_id)
    verifier_security = tuple(
        item for item in installed.security_policies
        if item.policy_digest == verifier_install.security_policy_digest
    )
    primary_security = tuple(
        item for item in installed.security_policies
        if item.policy_digest != verifier_install.security_policy_digest
    )
    network_policies = tuple(
        item for item in installed.network_policies
        if item.mode == "none" and item.default_deny and item.egress_route_ids == ()
    )
    if (
        len(primary_images) != 1
        or len(verifier_images) != 1
        or verifier_images[0].image_digest != verifier_install.grant.image_digest
        or len(primary_security) != 1
        or len(verifier_security) != 1
        or len(network_policies) != 1
        or network_policies[0].policy_digest != verifier_install.grant.network_policy_digest
    ):
        raise F2AuthorityAuthoringError("installed sandbox catalogs do not bind exact C4 authorities")
    primary_binding = c.SandboxBinding(
        runtime_id=primary.runtime_id,
        runtime_class=primary.runtime_class,
        driver_implementation_digest=primary.driver_implementation_digest,
        runtime_binary_digest=primary.measured_binary_digest,
        security_policy_digest=primary_security[0].policy_digest,
        image_digest=primary_images[0].image_digest,
        network_policy_digest=network_policies[0].policy_digest,
    )
    verifier_binding = c.SandboxBinding(
        runtime_id=verifier_runtime.runtime_id,
        runtime_class=verifier_runtime.runtime_class,
        driver_implementation_digest=verifier_runtime.driver_implementation_digest,
        runtime_binary_digest=verifier_runtime.measured_binary_digest,
        security_policy_digest=verifier_security[0].policy_digest,
        image_digest=verifier_images[0].image_digest,
        network_policy_digest=network_policies[0].policy_digest,
    )
    secret = c.SecretHandleGrant(handle_id=spec.callback.secret_handle_id, handle_version_digest=spec.callback.secret_handle_version_digest, scope_digest=spec.policy.subject.authority_scope_digest)
    ip_projection = {"schema_version": "bb.rl.policy-ip-authority.v1", "allowed_addresses": [spec.callback.target_ip], "allow_loopback": False, "allow_private": True, "allow_link_local": False, "allow_multicast": False, "allow_unspecified": False}
    dns_projection = {"schema_version": "bb.rl.policy-dns-authority.v1", "hostname": spec.callback.target_ip, "allowed_addresses": [spec.callback.target_ip], "resolution_mode": "pinned", "require_all_answers_admitted": True, "verify_connected_peer": True}
    ip_doc = IPPolicyDocumentV1(ip_policy_digest=c._canonical_digest(ip_projection), allowed_addresses=(spec.callback.target_ip,), allow_loopback=False, allow_private=True, allow_link_local=False, allow_multicast=False, allow_unspecified=False, schema_version="bb.rl.policy-ip-authority.v1")
    dns_doc = DNSPolicyDocumentV1(dns_policy_digest=c._canonical_digest(dns_projection), hostname=spec.callback.target_ip, allowed_addresses=(spec.callback.target_ip,), resolution_mode="pinned", require_all_answers_admitted=True, verify_connected_peer=True, schema_version="bb.rl.policy-dns-authority.v1")
    dummy_route_grant = c.RouteGrant(route_id=_ROUTE_ID, route_revision_digest="sha256:" + "0" * 64, protocol_abi=POLICY_HTTP_PROTOCOL_ABI, credential_handle_id=secret.handle_id)
    route_values = dict(grant=dummy_route_grant, scheme=c.RouteScheme.HTTPS, authority=f"{spec.callback.target_ip}:{spec.callback.port}", paths=("/v1/responses",), methods=(c.RouteMethod.POST,), ip_policy_digest=ip_doc.ip_policy_digest, dns_policy_digest=dns_doc.dns_policy_digest, request_schema_digest=POLICY_HTTP_REQUEST_SCHEMA_DIGEST, response_schema_digest=POLICY_HTTP_RESPONSE_SCHEMA_DIGEST, max_request_bytes=spec.limits.response_bytes, max_response_bytes=spec.limits.observation_bytes, max_requests_per_minute=60, data_classification=c.DataClassification.CONFIDENTIAL, owner=c.RouteOwnerAuthority(owner_id=spec.callback.owner_id, authority_scope_digest=spec.policy.subject.authority_scope_digest))
    unbound_route = c.RouteRegistryRecord.model_construct(**route_values)
    route_grant = c.RouteGrant(route_id=_ROUTE_ID, route_revision_digest=unbound_route.derived_route_revision_digest(), protocol_abi=POLICY_HTTP_PROTOCOL_ABI, credential_handle_id=secret.handle_id)
    route = c.RouteRegistryRecord(**{**route_values, "grant": route_grant})
    model = c.ModelIdentity(model_id=_MODEL_ID, **spec.model.model_dump())
    policy_caps = c.PolicyCapabilityVector(responses_protocol=POLICY_HTTP_PROTOCOL_ABI, modalities=("text",), tool_calling=True, parallel_tool_calls=False, token_ids=False, token_logprobs=False, routing_metadata=False, cancellation=True, max_context_tokens=spec.limits.observation_bytes, max_output_tokens=spec.limits.response_bytes, policy_slot_count=1, request_features=())
    observation_values = dict(registry_revision_digest="sha256:" + "0" * 64, route_id=_ROUTE_ID, route_revision_digest=route_grant.route_revision_digest, provider_id="fixed-c4-policy", protocol_abi=POLICY_HTTP_PROTOCOL_ABI, bridge_instance_id="fixed-c4-bridge", bridge_build_digest=c._canonical_digest({"bridge": "fixed-c4"}), model_id=_MODEL_ID, model_digest=model.model_digest, tokenizer_digest=model.tokenizer_digest, checkpoint_digest=model.checkpoint_digest, credential_handle_id=secret.handle_id, credential_handle_version_digest=secret.handle_version_digest, subject_scope_digest=spec.policy.subject.authority_scope_digest, capabilities=policy_caps, capability_digest="sha256:" + "0" * 64, provenance=c.AttestationProvenance(kind=c.AttestationKind.OPERATOR_ATTESTATION, issuer_id="phase5-f2-authority-author", signer_key_id=spec.receipt_signer.key_id, environment_digest=c._canonical_digest({"attempt_id": spec.attempt_id}), evidence_digest=spec.ibm_target_record.sha256, validity=spec.policy.validity), revocation=spec.policy.revocation)
    observation_unbound = c.PolicyCapabilityObservation.model_construct(**observation_values)
    capability_digest = c._canonical_digest(observation_unbound.selection_capability_obj())
    slot = c.PolicySlotGrant(slot_id=_POLICY_SLOT_ID, protocol_abi=POLICY_HTTP_PROTOCOL_ABI, route_id=_ROUTE_ID, secret_handle_id=secret.handle_id, model_digest=model.model_digest, tokenizer_digest=model.tokenizer_digest, checkpoint_digest=model.checkpoint_digest, required_policy_capabilities_digest=capability_digest)
    evidence_ref = c.PolicyRef(policy_id=_EVIDENCE_POLICY_ID, revision_digest=spec.policy.evidence_policy_revision_digest)
    retention_ref = c.PolicyRef(policy_id=_RETENTION_POLICY_ID, revision_digest=spec.policy.retention_policy_revision_digest)
    retention = c.RetentionPolicyGrant(policy=retention_ref, minimum_seconds=spec.policy.retention_minimum_seconds, maximum_seconds=spec.policy.retention_maximum_seconds)
    task = c.TaskGrant(task_contract_digest=spec.task.task_contract_digest, task_binding_digest=spec.task.task_binding_digest, repository_snapshot_digest=None, dataset_digests=(), input_artifact_digests=())
    sandbox = c.SandboxGrant(**primary_binding.model_dump(), egress_route_ids=(), mounts=())
    capability = c.CapabilityVector(runner=runner, tools=(tool,), setup_plans=(), routes=(route_grant,), secret_handles=(secret,), sandbox=sandbox, resources=spec.resources, limits=spec.limits, task=task, policy_slots=(slot,), verifier=verifier_install.grant, mutable_pointers=(), artifacts=spec.artifact_policy, evidence=evidence_ref, retention=retention_ref)
    attestation_values = dict(route_id=_ROUTE_ID, route_revision_digest=route_grant.route_revision_digest, model_digest=model.model_digest, tokenizer_digest=model.tokenizer_digest, checkpoint_digest=model.checkpoint_digest, capability_digest=capability_digest, validity=spec.policy.validity, revocation=spec.policy.revocation, authorized_signer_key_ids=(spec.receipt_signer.key_id,), signature_verification_policy_digest=c._canonical_digest({"algorithm": "hmac-sha256-v1", "key_id": spec.receipt_signer.key_id}), attestation_digest="sha256:" + "0" * 64)
    attestation_unbound = c.PolicyCapabilityAttestationRecord.model_construct(**attestation_values)
    attestation = c.PolicyCapabilityAttestationRecord(**{**attestation_values, "attestation_digest": attestation_unbound.derived_attestation_digest()})
    records: dict[str, tuple[c._ConfigRuntimeContract, ...]] = {
        "runners": (c.RunnerRegistryRecord(grant=runner),),
        "tools": (c.ToolRegistryRecord(grant=tool, argument_schema_digest=c._canonical_digest(_ARGUMENT_SCHEMA), result_schema_digest=c._canonical_digest(_RESULT_SCHEMA), reserved=False),),
        "setups": (), "routes": (route,), "secret_handles": (c.SecretHandleRegistryRecord(grant=secret, route_ids=(_ROUTE_ID,)),),
        "sandbox_runtimes": tuple(sorted((c.SandboxRuntimeRegistryRecord(binding=primary_binding), c.SandboxRuntimeRegistryRecord(binding=verifier_binding)), key=lambda item: item.binding.runtime_id)),
        "images": tuple(sorted((c.ImageRegistryRecord(image_digest=primary_binding.image_digest, runtime_id=primary_binding.runtime_id, repository_binding_digests=()), c.ImageRegistryRecord(image_digest=verifier_binding.image_digest, runtime_id=verifier_binding.runtime_id, repository_binding_digests=())), key=lambda item: item.image_digest)),
        "repository_bindings": (), "task_datasets": (c.TaskDatasetRegistryRecord(**task.model_dump()),), "models": (c.ModelRegistryRecord(identity=model),),
        "verifiers": (c.VerifierRegistryRecord(grant=verifier_install.grant, runtime_id=verifier_binding.runtime_id, runtime_class=verifier_binding.runtime_class, security_policy_digest=verifier_binding.security_policy_digest),),
        "evidence_policies": (c.EvidencePolicyRegistryRecord(policy=evidence_ref, required_roles=spec.artifact_policy.allowed_roles),),
        "retention_policies": (c.RetentionPolicyRegistryRecord(grant=retention),), "policy_capability_attestations": (attestation,),
    }
    registries = _registry_from_records(records)
    observation = c.PolicyCapabilityObservation(**{**observation_values, "registry_revision_digest": registries.digests.route_registry_digest, "capability_digest": capability_digest})
    schema_authority = PolicyHttpSchemaAuthorityV1(schema_version="bb.rl.policy-http-schema-authority.v1", protocol_abi=POLICY_HTTP_PROTOCOL_ABI, request_schema=POLICY_HTTP_REQUEST_SCHEMA, request_schema_digest=POLICY_HTTP_REQUEST_SCHEMA_DIGEST, response_schema=POLICY_HTTP_RESPONSE_SCHEMA, response_schema_digest=POLICY_HTTP_RESPONSE_SCHEMA_DIGEST)
    secret_binding = PolicySecretRouteBindingV1(schema_version="bb.rl.policy-secret-route-binding.v1", handle_id=secret.handle_id, handle_version_digest=secret.handle_version_digest, scope_digest=secret.scope_digest, route_ids=(_ROUTE_ID,))
    policy_http = PolicyHttpAuthorityGraphV1(registry_revision_digest=registries.digests.route_registry_digest, routes=(route,), observations=(observation,), dns_policies=(dns_doc,), ip_policies=(ip_doc,), schema_authority=schema_authority, secret_bindings=(secret_binding,))
    ceiling = c.OperatorCeiling(runner_bindings=(runner,), tool_grants=(tool,), setup_grants=(), route_grants=(route_grant,), secret_handle_grants=(secret,), sandbox_bindings=tuple(sorted((primary_binding, verifier_binding), key=lambda item: (item.runtime_id, item.image_digest))), repository_snapshot_digests=(), task_contract_digests=(task.task_contract_digest,), task_binding_digests=(task.task_binding_digest,), dataset_digests=(), model_bindings=(model,), verifier_grants=(verifier_install.grant,), policy_slot_grants=(slot,), evidence_policies=(evidence_ref,), retention_policies=(retention,), mutable_pointer_rules=(), resource_maxima=spec.resources, execution_maxima=spec.limits, allowed_egress_route_ids=(), mount_grants=(), artifact_policy_maximum=spec.artifact_policy)
    return capability, registries, (observation,), policy_http, ceiling




def _receipt_validity(policy: C4PolicyAuthority) -> c.ValidityWindow:
    not_before = datetime.strptime(
        policy.validity.not_before, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC)
    expires_at = not_before + timedelta(seconds=policy.receipt_ttl_seconds)
    return c.ValidityWindow(
        issued_at=policy.validity.issued_at,
        not_before=policy.validity.not_before,
        expires_at=expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _compiled_identity(manifest: Any, digest: str) -> c.CompiledArtifactIdentity:
    compiler = manifest.compiler
    identity = c.CompilerIdentity(
        compiler_id=compiler.compiler_id,
        semantic_version=compiler.compiler_version,
        code_digest=compiler.compiler_code_digest,
        source_schema_id=compiler.config_schema_id,
        source_schema_digest=compiler.config_schema_digest,
        manifest_schema_digest=compiler.manifest_schema_digest,
        canonicalizer_id=compiler.canonicalizer_id,
        runtime_abi=compiler.runtime_abi,
    )
    return c.CompiledArtifactIdentity(
        manifest_digest=digest,
        bundle_digest=manifest.inputs.bundle_digest,
        closure_digest=manifest.inputs.closure_digest,
        compiler_input_digest=manifest.inputs.compiler_input_digest,
        semantic_digest=manifest.semantic_digest,
        compiler=identity,
        provenance_digest=c._canonical_digest([item.to_canonical_obj() for item in manifest.provenance]),
        diagnostics_digest=c._canonical_digest(manifest.diagnostics.to_canonical_obj()),
    )


def _verify_authority_objects(objects: dict[str, bytes], graph: dict[str, Any]) -> None:
    from agentic_coder_prototype.compilation.contracts import CompiledConfigManifest, ConfigBundleManifest
    parsers: dict[str, Any] = {
        "compiled-manifest.json": lambda raw: CompiledConfigManifest.from_dict(json.loads(raw)),
        "config-bundle.json": lambda raw: ConfigBundleManifest.from_dict(json.loads(raw)),
        "admission-policy.json": lambda raw: c.AdmissionPolicySnapshot.model_validate_json(raw, strict=True),
        "registry-snapshot.json": lambda raw: c.RegistrySnapshotSet.model_validate_json(raw, strict=True),
        "admission-receipt.json": lambda raw: c.AdmissionReceipt.model_validate_json(raw, strict=True),
        "admitted-set.json": lambda raw: c.AdmittedSetManifest.model_validate_json(raw, strict=True),
        "direct-selector.json": lambda raw: c.DirectSelector.model_validate_json(raw, strict=True),
        "policy-http.json": lambda raw: PolicyHttpAuthorityGraphV1.model_validate_json(raw, strict=True),
    }
    for name, parser in parsers.items():
        value = parser(objects[name])
        canonical = value.canonical_bytes()
        if canonical != objects[name]:
            raise F2AuthorityAuthoringError(f"{name} is not canonical")
    revocations = json.loads(objects["revocations.json"])
    if canonical_json_bytes([c.RevocationBinding.model_validate(item, strict=True).model_dump(mode="json") for item in revocations]) != objects["revocations.json"]:
        raise F2AuthorityAuthoringError("revocations.json is not canonical")
    capabilities = json.loads(objects["policy-capabilities.json"])
    if canonical_json_bytes([c.PolicyCapabilityObservation.model_validate(item, strict=True).model_dump(mode="json") for item in capabilities]) != objects["policy-capabilities.json"]:
        raise F2AuthorityAuthoringError("policy-capabilities.json is not canonical")
    receipt = c.AdmissionReceipt.model_validate_json(objects["admission-receipt.json"], strict=True)
    admitted = c.AdmittedSetManifest.model_validate_json(objects["admitted-set.json"], strict=True)
    selector = c.DirectSelector.model_validate_json(objects["direct-selector.json"], strict=True)
    policy = c.AdmissionPolicySnapshot.model_validate_json(objects["admission-policy.json"], strict=True)
    registries = c.RegistrySnapshotSet.model_validate_json(objects["registry-snapshot.json"], strict=True)
    if (
        receipt.compiled.manifest_digest != graph["joins"]["compiled_to_receipt"]
        or receipt.admission_policy_digest != graph["joins"]["policy_to_receipt"]
        or receipt.registry_snapshot_digest != graph["joins"]["registry_to_receipt"]
        or admitted.receipt_digests != (graph["joins"]["receipt_to_set"],)
        or selector.admitted_set_root != graph["joins"]["set_to_selector"]
        or selector.candidate.candidate_id != graph["joins"]["selector_candidate"]
        or policy.canonical_digest() != receipt.admission_policy_digest
        or registries.digests.snapshot_digest != receipt.registry_snapshot_digest
    ):
        raise F2AuthorityAuthoringError("authority inventory joins do not close")
    referenced = set(graph["objects"])
    if referenced != set(objects):
        raise F2AuthorityAuthoringError("inventory has missing or unused authority objects")
    for name, node in graph["objects"].items():
        if _digest(objects[name]) != node["sha256"]:
            raise F2AuthorityAuthoringError(f"{name} inventory digest mismatch")


def _verify_executable_observation(executable: Any) -> None:
    _verify_external_artifact(ExternalArtifact(path=executable.path, sha256=executable.sha256, media_type="application/octet-stream"))
    info = os.stat(executable.path, follow_symlinks=False)
    actual = (
        info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size,
        stat.S_IMODE(info.st_mode), info.st_uid,
    )
    expected = (
        executable.device, executable.inode, int(executable.ctime_ns),
        executable.size_bytes, executable.mode, executable.owner_uid,
    )
    if actual != expected:
        raise F2AuthorityAuthoringError("wrapper host executable observation mismatch")


def _verify_openssl_authority(openssl: OpenSslAuthorityInput) -> None:
    _verify_external_artifact(openssl.discovery_report)
    _verify_executable_observation(openssl)
    before = os.stat(openssl.path, follow_symlinks=False)
    identity = (
        before.st_dev, before.st_ino, before.st_ctime_ns, before.st_size,
        stat.S_IMODE(before.st_mode), before.st_uid,
    )
    observed = subprocess.run(
        [openssl.path, "version"], executable=openssl.path,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, close_fds=True, env={"LC_ALL": "C", "PATH": "/nonexistent"},
    )
    after = os.stat(openssl.path, follow_symlinks=False)
    if (
        identity != (
            after.st_dev, after.st_ino, after.st_ctime_ns, after.st_size,
            stat.S_IMODE(after.st_mode), after.st_uid,
        )
        or _digest(observed.stdout) != openssl.version_stdout_sha256
        or observed.stdout.decode("ascii").rstrip("\n") != openssl.version
        or observed.stderr
    ):
        raise F2AuthorityAuthoringError("OpenSSL semantic observation mismatch")


def _validate_static_authority(authority: F2C4StaticAuthorityInput) -> None:
    external_sources = (
        authority.f1_prerequisite_report,
        authority.host_runtime_build_report,
        authority.ibm_target_record,
        authority.wrapper_image_build_report,
        authority.wrapper_image_operator_authorization,
        authority.mount_broker_implementation,
        authority.wrapper_host_executables.binary_discovery_report,
    )
    for external in external_sources:
        _verify_external_artifact(external)
    for executable in (
        authority.wrapper_host_executables.cleanup_python,
        authority.wrapper_host_executables.sudo,
        authority.wrapper_host_executables.env,
        authority.wrapper_host_executables.docker,
    ):
        _verify_executable_observation(executable)
    _verify_openssl_authority(authority.openssl)


def build_f2_c4_static_authority(
    value: F2C4StaticAuthorityInput | dict[str, Any],
    output_dir: str,
) -> str:
    """Validate reviewed immutable inputs and publish one static C4 fragment."""
    authority = value if isinstance(value, F2C4StaticAuthorityInput) else F2C4StaticAuthorityInput.model_validate(value, strict=True)
    _validate_static_authority(authority)
    destination = Path(output_dir)
    if not destination.is_absolute() or os.path.normpath(output_dir) != output_dir:
        raise F2AuthorityAuthoringError("static authority output must be absolute and normalized")
    if os.path.lexists(destination):
        raise F2AuthorityAuthoringError("static authority output already exists")
    source_values: tuple[tuple[str, Any], ...] = (
        ("f1_prerequisite_report", authority.f1_prerequisite_report),
        ("host_runtime_build_report", authority.host_runtime_build_report),
        ("ibm_target_record", authority.ibm_target_record),
        ("mount_broker_implementation", authority.mount_broker_implementation),
        ("openssl", authority.openssl),
        ("openssl_discovery_report", authority.openssl.discovery_report),
        ("wrapper_binary_discovery_report", authority.wrapper_host_executables.binary_discovery_report),
        ("wrapper_cleanup_python", authority.wrapper_host_executables.cleanup_python),
        ("wrapper_docker", authority.wrapper_host_executables.docker),
        ("wrapper_env", authority.wrapper_host_executables.env),
        ("wrapper_image_build_report", authority.wrapper_image_build_report),
        ("wrapper_image_operator_authorization", authority.wrapper_image_operator_authorization),
        ("wrapper_sudo", authority.wrapper_host_executables.sudo),
    )
    source_inventory = tuple({
        "logical_name": name,
        "path": item.path,
        "sha256": item.sha256,
        "size_bytes": os.stat(item.path, follow_symlinks=False).st_size,
        "media_type": getattr(item, "media_type", "application/octet-stream"),
    } for name, item in source_values)
    authority_bytes = canonical_json_bytes(authority.model_dump(mode="json"))
    fragment = F2C4StaticAuthorityFragment(
        schema_version="bb.rl.phase5-f2-c4-static-authority-fragment.v1",
        authority=authority,
        authority_digest=_digest(authority_bytes),
        source_inventory=source_inventory,
    )
    staging = destination.parent / f".{destination.name}.static-{uuid.uuid4().hex}"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging.mkdir(mode=0o700)
    try:
        fragment_bytes = canonical_json_bytes(fragment.model_dump(mode="json"))
        inventory_bytes = canonical_json_bytes({
            "schema_version": "bb.rl.phase5-f2-c4-static-authority-inventory.v1",
            "authority_digest": fragment.authority_digest,
            "fragment_sha256": _digest(fragment_bytes),
            "sources": list(source_inventory),
        })
        directory_fd = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            _write_exclusive(directory_fd, "static-authority.json", fragment_bytes)
            _write_exclusive(directory_fd, "inventory.json", inventory_bytes)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        cas = FilesystemCAS(staging / "cas")
        try:
            cas.put_bytes(fragment_bytes, media_type="application/json")
            cas.put_bytes(inventory_bytes, media_type="application/json")
        finally:
            cas.close()
        os.rename(staging, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return os.fspath((destination / "static-authority.json").resolve())
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_operator_source(operator: dict[str, Any]) -> bytes:
    from breadboard.rl.phase5.f2_composition import F2ProductionCompositionInput

    operator_source_bytes = canonical_json_bytes(operator)
    validated = F2ProductionCompositionInput.model_validate_json(
        operator_source_bytes, strict=True
    )
    return canonical_json_bytes(validated.model_dump(mode="json"))


def _compile_c4_config(
    spec: F2C4SemanticInput,
    capability: c.CapabilityVector,
    cas: FilesystemCAS,
) -> tuple[Any, Any, dict[str, bytes]]:
    from agentic_coder_prototype.compilation.server_compiler import compile_config

    compiler_config = {
        "version": 2,
        "extends": ["base-config.json"],
        "profile": {"name": "c4-terminal-direct", "metadata": {"breadboard_rl_authority": {"requested_capabilities": capability.model_dump(mode="json"), "task_binding_digest": capability.task.task_binding_digest}}},
        "workspace": {"root": "workspace"},
        "providers": {"default_model": _MODEL_ID, "models": [{"id": _MODEL_ID, "adapter": "openai_responses", "route_handle_id": _ROUTE_ID, "credential_handle_id": spec.callback.secret_handle_id, "params": {"temperature": 0}}]},
        "prompts": {"injection": {"system_order": [], "per_turn_order": []}},
        "tools": {"registry": {"paths": ["tools"], "include": [_TOOL_ID], "exclude": []}},
        "modes": [{"id": "build", "prompt": spec.prompt, "tools_enabled": [_TOOL_ID]}],
        "loop": {"sequence": ["build"]},
    }
    member_bytes = {
        "base-config.json": canonical_json_bytes({"provider_tools": {"api_variant": "responses", "use_native": True}}),
        "c4-terminal-direct.json": canonical_json_bytes(compiler_config),
        "tools/shell.yaml": canonical_json_bytes({"id": _TOOL_ID, "name": "shell", "description": "Run the one admitted C4 terminal command.", "parameters": [{"name": "command", "description": "The fixed C4 terminal command.", "required": True, "schema": {"const": _TASK_COMMAND, "type": "string"}}], "execution": {"blocking": True, "max_per_turn": 1}}),
    }
    bundle = ingest_member_map(
        member_bytes,
        cas,
        entrypoints={"main": "c4-terminal-direct.json"},
        source_label="phase5-f2-fixed-c4",
        media_types={name: "application/json" for name in member_bytes},
    )
    edges = (
        DependencyEdge("c4-terminal-direct.json", "extends", "base-config.json", "base-config.json", 0),
        DependencyEdge("c4-terminal-direct.json", "tool_registry", "tools", "tools/shell.yaml", 0),
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main", edges=edges)
    reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
    options = CompileOptions.from_dict({
        "schema_id": "bb.compile-options.v1", "source_contract": "v2", "v1_loss_policy": "reject_all",
        "target": {"runner_adapter_id": capability.runner.adapter_id, "runtime_abi": capability.runner.runtime_abi},
        "task_contract": {
            "contract_id": "c4-terminal-direct",
            "parameter_schema": {"type": "object", "properties": {"command": {"const": _TASK_COMMAND}}, "required": ["command"], "additionalProperties": False},
            "artifacts": [{"artifact_id": "terminal-result", "direction": "output", "media_types": ["text/plain"], "required": True, "cardinality": "one", "max_bytes": 26}],
            "verifier": {"binding_id": spec.verifier_id, "input_artifact_ids": ["terminal-result"], "result_schema": _RESULT_SCHEMA, "timeout_ms": spec.limits.verifier_timeout_ms},
            "evidence": {"required_event_types": ["turn.completed"], "required_artifact_ids": ["terminal-result"]},
            "retention": {"retention_class_id": _RETENTION_POLICY_ID, "minimum_retention_seconds": spec.policy.retention_minimum_seconds},
        },
    })
    return compile_config(reader, closure, options), bundle, member_bytes


def author_f2_operator_input(semantic_input_path: str, output_dir: str) -> str:
    """Compile, admit, publish and verify one exact C4 authority graph."""
    destination = Path(output_dir)
    if not destination.is_absolute() or os.path.normpath(output_dir) != output_dir:
        raise F2AuthorityAuthoringError("output directory must be absolute and normalized")
    if os.path.lexists(destination):
        raise F2AuthorityAuthoringError("output directory already exists")
    raw = _read_canonical_input(semantic_input_path)
    spec = F2C4SemanticInput.model_validate_json(raw, strict=True)
    for external in (
        spec.f1_prerequisite_report,
        spec.ibm_target_record,
        spec.host_runtime_build_report,
        spec.wrapper_image_build_report,
        spec.wrapper_image_operator_authorization,
        spec.mount_broker_implementation,
        spec.wrapper_host_executables.binary_discovery_report,
        spec.openssl.discovery_report,
    ):
        _verify_external_artifact(external)
    for executable in (
        spec.wrapper_host_executables.cleanup_python,
        spec.wrapper_host_executables.sudo,
        spec.wrapper_host_executables.env,
        spec.wrapper_host_executables.docker,
    ):
        _verify_executable_observation(executable)
    openssl_before = os.stat(spec.openssl.path, follow_symlinks=False)
    actual_openssl = (
        openssl_before.st_dev, openssl_before.st_ino, openssl_before.st_ctime_ns,
        openssl_before.st_size, stat.S_IMODE(openssl_before.st_mode), openssl_before.st_uid,
    )
    expected_openssl = (
        spec.openssl.device, spec.openssl.inode, int(spec.openssl.ctime_ns),
        spec.openssl.size_bytes, spec.openssl.mode, spec.openssl.owner_uid,
    )
    if actual_openssl != expected_openssl:
        raise F2AuthorityAuthoringError("OpenSSL runtime observation mismatch")
    _verify_external_artifact(ExternalArtifact(path=spec.openssl.path, sha256=spec.openssl.sha256, media_type="application/octet-stream"))
    observed_openssl = subprocess.run(
        [spec.openssl.path, "version"], executable=spec.openssl.path,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, close_fds=True, env={"LC_ALL": "C", "PATH": "/nonexistent"},
    )
    openssl_after = os.stat(spec.openssl.path, follow_symlinks=False)
    if (
        actual_openssl != (
            openssl_after.st_dev, openssl_after.st_ino, openssl_after.st_ctime_ns,
            openssl_after.st_size, stat.S_IMODE(openssl_after.st_mode), openssl_after.st_uid,
        )
        or _digest(observed_openssl.stdout) != spec.openssl.version_stdout_sha256
        or observed_openssl.stdout.decode("ascii").rstrip("\n") != spec.openssl.version
        or observed_openssl.stderr
    ):
        raise F2AuthorityAuthoringError("OpenSSL semantic observation mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = destination.parent / f".{destination.name}.authoring-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        artifacts = staging / "artifacts"
        artifacts.mkdir(mode=0o700)
        cas = FilesystemCAS(staging / "cas")
        try:
            capability, registries, policy_capabilities, policy_http, ceiling = _derive_c4(spec)
            manifest, bundle, member_bytes = _compile_c4_config(spec, capability, cas)
            compiled_bytes = manifest.canonical_bytes()
            compiled_digest = _digest(compiled_bytes)
            compiled = _compiled_identity(manifest, compiled_digest)
            attestation = registries.policy_capability_attestations[0]
            policy = c.AdmissionPolicySnapshot(
                policy_id="phase5-f2-fixed-c4", revision="c4-" + registries.digests.snapshot_digest[7:39],
                subject_scope_digest=spec.policy.subject.authority_scope_digest,
                compiler_constraints=c.CompilerConstraints(allowed_compilers=(compiled.compiler,)),
                registry_digests=registries.digests, ceiling=ceiling,
                required_security=c.RequiredSecurityPolicy(minimum_isolation_class=c.RuntimeClass.HARDENED_DOCKER, required_verifier_isolation_class=c.RuntimeClass.HARDENED_DOCKER, required_evidence_roles=spec.artifact_policy.allowed_roles, prohibited_runtime_classes=(c.RuntimeClass.TRUSTED_PROCESS,), minimum_retention_seconds=spec.policy.retention_minimum_seconds),
                receipt_ttl_seconds=spec.policy.receipt_ttl_seconds, validity=spec.policy.validity, revocation=spec.policy.revocation,
            )
            request = c.AdmissionRequest(
                subject=spec.policy.subject,
                behavior_source=c.CompiledBehaviorSource(manifest_digest=compiled_digest, semantic_digest=manifest.semantic_digest),
                compiled=compiled,
                requested_capabilities=capability,
                requested_capability_digest=capability.canonical_digest(),
                task_binding_digest=capability.task.task_binding_digest,
                policy_binding_ref=c.PolicyBindingRef(route_id=attestation.route_id, registry_revision_digest=registries.digests.route_registry_digest, attestation_digest=attestation.attestation_digest),
                admission_policy_digest=policy.canonical_digest(),
                registry_snapshot_digest=registries.digests.snapshot_digest,
                validity=_receipt_validity(spec.policy),
                parent_receipt_digest=None,
                overlay_chain_digest=None,
            )
            key = _read_secret_0400(spec.receipt_signer.secret_path)
            store = CASConfigRuntimeStore(cas)
            runtime = ConfigRuntime(
                compiler=PinnedServerCompilerAdapter({compiled_digest: compiled_bytes}),
                policy=policy,
                registries=registries,
                revocations=PinnedRevocationStore((policy.revocation,)),
                store=store,
                clock=type("_PinnedClock", (), {"current": lambda self: datetime.strptime(spec.policy.validity.not_before, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)})(),
                authenticator=HmacSha256ReceiptAuthenticator(key_id=spec.receipt_signer.key_id, key=key),
            )
            receipt_ref = runtime.admit(request)
            receipt_bytes = store.load(receipt_ref.digest, kind=c.ArtifactKind.ADMISSION_RECEIPT, max_bytes=4 * 1024 * 1024)
            receipt = c.AdmissionReceipt.model_validate_json(receipt_bytes, strict=True)
            admitted = c.AdmittedSetManifest(
                compiler_abi=receipt.compiled.compiler.semantic_version,
                admission_policy_digest=receipt.admission_policy_digest,
                operator_ceiling_digest=receipt.operator_ceiling_digest,
                registry_snapshot_digest=receipt.registry_snapshot_digest,
                revocation=receipt.revocation,
                receipt_digests=(receipt_ref.digest,),
                validity=receipt.validity,
            )
            admitted_bytes = admitted.canonical_bytes()
            admitted_ref = store.publish(kind=c.ArtifactKind.ADMITTED_SET, canonical_bytes=admitted_bytes)
            selector = c.DirectSelector(
                admitted_set_root=admitted_ref.sha256,
                compiler_abi=admitted.compiler_abi,
                runtime_abi=receipt.compiled.compiler.runtime_abi,
                admission_policy_digest=receipt.admission_policy_digest,
                operator_ceiling_digest=receipt.operator_ceiling_digest,
                candidate=c.ConfigCandidate(candidate_id=spec.attempt_id, receipt_digest=receipt_ref.digest, predicates=(), overlays=()),
                validity=receipt.validity,
            )
            selector_bytes = selector.canonical_bytes()
            store.publish(kind=c.ArtifactKind.DIRECT_SELECTOR, canonical_bytes=selector_bytes)
            objects = {
                "compiled-manifest.json": compiled_bytes,
                "admission-policy.json": policy.canonical_bytes(),
                "registry-snapshot.json": registries.canonical_bytes(),
                "revocations.json": canonical_json_bytes([policy.revocation.model_dump(mode="json")]),
                "policy-capabilities.json": canonical_json_bytes([item.model_dump(mode="json") for item in policy_capabilities]),
                "policy-http.json": policy_http.canonical_bytes(),
                "admission-receipt.json": receipt_bytes,
                "admitted-set.json": admitted_bytes,
                "direct-selector.json": selector_bytes,
                "config-bundle.json": bundle.canonical_bytes(),
            }
            graph = {
                "schema_version": "bb.rl.phase5-f2-authority-inventory.v1",
                "objects": {name: {"sha256": _digest(payload), "size_bytes": len(payload)} for name, payload in sorted(objects.items())},
                "joins": {
                    "compiled_to_receipt": compiled_digest,
                    "policy_to_receipt": policy.canonical_digest(),
                    "registry_to_receipt": registries.digests.snapshot_digest,
                    "receipt_to_set": receipt_ref.digest,
                    "set_to_selector": admitted_ref.sha256,
                    "selector_candidate": spec.attempt_id,
                },
            }
            _verify_authority_objects(objects, graph)
            afd = os.open(artifacts, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
            try:
                for name, payload in objects.items():
                    _write_exclusive(afd, name, payload)
                inventory_bytes = canonical_json_bytes(graph)
                _write_exclusive(afd, "inventory.json", inventory_bytes)
                os.fsync(afd)
            finally:
                os.close(afd)
            sorted_members = tuple(sorted(member_bytes))
            config_sources = tuple({"logical_path": logical_path, "source": _source(artifacts / f"config-{index}.json", member_bytes[logical_path], "application/json")} for index, logical_path in enumerate(sorted_members))
            afd = os.open(artifacts, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
            try:
                for index, logical_path in enumerate(sorted_members):
                    _write_exclusive(afd, f"config-{index}.json", member_bytes[logical_path])
                os.fsync(afd)
            finally:
                os.close(afd)
            authority = {
                "f1_prerequisite_report": spec.f1_prerequisite_report.model_dump(mode="json"),
                "f1_prerequisite_canonical_root": spec.f1_prerequisite_canonical_root,
                "ibm_target_record": spec.ibm_target_record.model_dump(mode="json"),
                "host_runtime_root": spec.host_runtime_root,
                "host_runtime_build_report": spec.host_runtime_build_report.model_dump(mode="json"),
                "wrapper_image_build_report": spec.wrapper_image_build_report.model_dump(mode="json"),
                "wrapper_image_operator_authorization": spec.wrapper_image_operator_authorization.model_dump(mode="json"),
                "admission_policy": _source(artifacts / "admission-policy.json", objects["admission-policy.json"], "application/json"),
                "wrapper_host_executables": spec.wrapper_host_executables.model_dump(mode="json"),
                "registry_snapshot": _source(artifacts / "registry-snapshot.json", objects["registry-snapshot.json"], "application/json"),
                "revocation_snapshot": _source(artifacts / "revocations.json", objects["revocations.json"], "application/json"),
                "policy_capability_snapshot": _source(artifacts / "policy-capabilities.json", objects["policy-capabilities.json"], "application/json"),
                "policy_http": _source(artifacts / "policy-http.json", objects["policy-http.json"], "application/json"),
                "mount_broker_implementation": spec.mount_broker_implementation.model_dump(mode="json"),
                "openssl": spec.openssl.model_dump(mode="json"),
                "config_bundle": _source(artifacts / "config-bundle.json", objects["config-bundle.json"], "application/json"),
                "config_members": config_sources,
                "compiled_manifests": (_source(artifacts / "compiled-manifest.json", compiled_bytes, _MEDIA["compiled_manifest"]),),
                "admission_receipts": (_source(artifacts / "admission-receipt.json", receipt_bytes, _MEDIA["admission_receipt"]),),
                "admitted_set": _source(artifacts / "admitted-set.json", admitted_bytes, _MEDIA["admitted_set"]),
                "direct_selector": _source(artifacts / "direct-selector.json", selector_bytes, _MEDIA["direct_selector"]),
                "tls": spec.tls.model_dump(mode="json"),
            }
            operator = {
                "schema_version": "bb.rl.phase5-f2-production-input.v1",
                "composition_id": spec.composition_id,
                "authority": authority,
                "installed": spec.installed.model_dump(mode="json"),
                "stores": spec.stores,
                "server": ServerV1(mode="internal_bridge", host=spec.outer_bridge_plan.gateway, port=next(item.observed_port for item in spec.prebound_service_socket_plans if item.role == "harness"), allow_unauthenticated_loopback=False, proxy_headers=False, request_timeout_seconds=spec.server_request_timeout_seconds).model_dump(mode="json"),
                "outer_bridge_plan": spec.outer_bridge_plan.model_dump(mode="json"),
                "prebound_service_socket_plans": [item.model_dump(mode="json") for item in spec.prebound_service_socket_plans],
                "secrets": {"handles": spec.secret_handles.model_dump(mode="json"), "files": spec.secret_files},
                "receipt": {"key_id": spec.receipt_signer.key_id, "secret_handle_id": spec.receipt_signer.secret_handle_id},
                "evidence_bindings": [_wire(item) for item in spec.evidence_bindings],
                "evidence_receipt_signing_authority": spec.evidence_receipt_signing_authority.model_dump(mode="json"),
                "request_template": {"task_input": {"command": spec.shell_command, "expected_completion": spec.completion}, "context": {}},
            }
            operator_bytes = _validate_operator_source(operator)
            sfd = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
            try:
                _write_exclusive(sfd, "operator-input.json", operator_bytes)
                os.fsync(sfd)
            finally:
                os.close(sfd)
        finally:
            cas.close()
        os.rename(staging, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return os.fspath((destination / "operator-input.json").resolve())
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_f2_operator_input(operator_input_path: str) -> None:
    """Reload a completed output and reject mutated or unjoined authority bytes."""
    from breadboard.rl.phase5.f2_composition import F2ProductionCompositionInput
    raw = _read_canonical_input(operator_input_path)
    model = F2ProductionCompositionInput.model_validate_json(raw, strict=True)
    inventory_path = Path(operator_input_path).parent / "artifacts" / "inventory.json"
    inventory_raw = _read_canonical_input(os.fspath(inventory_path))
    graph = json.loads(inventory_raw)
    objects: dict[str, bytes] = {}
    for name in graph.get("objects", {}):
        path = inventory_path.parent / name
        payload = path.read_bytes()
        objects[name] = payload
    _verify_authority_objects(objects, graph)
    declared = {Path(item.path).resolve() for item in (
        model.authority.admission_policy, model.authority.registry_snapshot,
        model.authority.revocation_snapshot, model.authority.policy_capability_snapshot,
        model.authority.policy_http, model.authority.config_bundle,
        model.authority.admitted_set, model.authority.direct_selector,
        *model.authority.compiled_manifests, *model.authority.admission_receipts,
    )}
    expected = {inventory_path.parent / name for name in graph["objects"]}
    if not declared <= expected:
        raise F2AuthorityAuthoringError("operator input references authority outside verified inventory")
