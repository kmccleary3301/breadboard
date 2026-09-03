from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import stat
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from breadboard_engine.compilation.bundle import ManifestReader, build_dependency_closure, ingest_member_map
from breadboard_engine.compilation.contracts import CompileOptions, DependencyEdge, canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    CASConfigRuntimeStore,
    DNSPolicyDocumentV1,
    HmacSha256ReceiptAuthenticator,
    IPPolicyDocumentV1,
    PinnedRevocationStore,
    PinnedServerCompilerAdapter,
    PolicyHttpAuthorityGraphV1,
    PolicyHttpSchemaAuthorityV1,
    PolicySecretRouteBindingV1,
)
from breadboard.rl.harness.config_runtime import ConfigRuntime
from breadboard.rl.harness.policy_http import (
    POLICY_HTTP_PROTOCOL_ABI,
    POLICY_HTTP_REQUEST_SCHEMA,
    POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
    POLICY_HTTP_RESPONSE_SCHEMA,
    POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
)
from breadboard.rl.harness.runners import terminal as terminal_runner
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
    TERMINAL_TOOL_DEFINITIONS,
    TerminalResponsesAdapter,
)
from breadboard.artifacts.cas import FilesystemCAS

_DIGEST_PREFIX = "sha256:"
_RESULT_ROLE = "terminal-result"
_REPOSITORY_ARTIFACT_ID = "repository-workspace"
_POLICY_SLOT_ID = "responses-policy"
_EVIDENCE_POLICY_ID = "phase5-f3-terminal-evidence"
_RETENTION_POLICY_ID = "phase5-f3-terminal-retention"
_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "reward": {"type": "number", "enum": [0, 1]},
    },
    "required": ["passed", "reward"],
    "additionalProperties": False,
}
_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("runners", "runner_registry_digest"),
    ("tools", "tool_registry_digest"),
    ("setups", "setup_registry_digest"),
    ("routes", "route_registry_digest"),
    ("secret_handles", "secret_handle_registry_digest"),
    ("sandbox_runtimes", "sandbox_runtime_registry_digest"),
    ("images", "image_registry_digest"),
    ("repository_bindings", "repository_binding_registry_digest"),
    ("task_datasets", "task_dataset_registry_digest"),
    ("models", "model_registry_digest"),
    ("verifiers", "verifier_registry_digest"),
    ("evidence_policies", "evidence_policy_registry_digest"),
    ("retention_policies", "retention_policy_registry_digest"),
    ("policy_capability_attestations", "policy_capability_registry_digest"),
)
_MEDIA_TYPES = {
    "config-bundle.json": "application/vnd.breadboard.config-bundle+json;version=1",
    "config-closure.json": "application/vnd.breadboard.config-closure+json;version=1",
    "compiled-manifest.json": "application/vnd.breadboard.compiled-manifest+json;version=1",
    "admission-policy.json": "application/vnd.breadboard.admission-policy+json;version=1",
    "registry-snapshot.json": "application/vnd.breadboard.registry-snapshot+json;version=1",
    "admission-receipt.json": "application/vnd.breadboard.admission-receipt+json;version=1",
    "admitted-set.json": "application/vnd.breadboard.admitted-set+json;version=1",
    "direct-selector.json": "application/vnd.breadboard.direct-selector+json;version=1",
    "policy-capabilities.json": "application/vnd.breadboard.policy-capabilities+json;version=1",
    "policy-http.json": "application/vnd.breadboard.policy-http-authority+json;version=1",
    "revocations.json": "application/vnd.breadboard.revocation-snapshot+json;version=1",
}


class F3AuthorityAuthoringError(ValueError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _is_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith(_DIGEST_PREFIX)
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _digest(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _digest_field(value: str) -> str:
    if not _is_digest(value):
        raise ValueError("authority requires a lowercase sha256 digest")
    return value


class ImmutableAuthorityRef(_ExactModel):
    immutable_reference: str = Field(min_length=1, max_length=4096)
    digest: str

    _digest = field_validator("digest")(_digest_field)

    @model_validator(mode="after")
    def exact_reference(self) -> "ImmutableAuthorityRef":
        if not self.immutable_reference.endswith("@" + self.digest):
            raise ValueError("external authority reference must end in its immutable digest")
        return self


class F3TaskAuthority(_ExactModel):
    task_id: Literal["R-SWE-001"]
    eligibility: c.TaskEligibilityInput
    task_contract_digest: str
    task_binding_digest: str
    prompt: str = Field(min_length=1, max_length=32768)

    _digests = field_validator("task_contract_digest", "task_binding_digest")(_digest_field)


class F3RepositoryAuthority(_ExactModel):
    repository_snapshot_digest: str
    binding_digest: str
    source: ImmutableAuthorityRef

    _digests = field_validator("repository_snapshot_digest", "binding_digest")(_digest_field)

    @model_validator(mode="after")
    def exact_snapshot(self) -> "F3RepositoryAuthority":
        if self.source.digest != self.repository_snapshot_digest:
            raise ValueError("repository reference does not bind the repository snapshot")
        return self


class F3RuntimeAuthority(_ExactModel):
    runtime_id: str = Field(min_length=1, max_length=128)
    runtime_class: Literal[c.RuntimeClass.HARDENED_DOCKER]
    driver_implementation_digest: str
    runtime_binary: ImmutableAuthorityRef
    oci_runtime_binary: ImmutableAuthorityRef

    _digest = field_validator("driver_implementation_digest")(_digest_field)


class F3ImageAuthority(_ExactModel):
    runtime_id: str = Field(min_length=1, max_length=128)
    image_digest: str
    immutable_reference: str = Field(min_length=1, max_length=4096)

    _digest = field_validator("image_digest")(_digest_field)

    @model_validator(mode="after")
    def immutable_image(self) -> "F3ImageAuthority":
        if not self.immutable_reference.endswith("@" + self.image_digest):
            raise ValueError("image reference must end in its immutable digest")
        return self


class F3SecurityAuthority(_ExactModel):
    policy_digest: str
    seccomp_digest: str
    uid: int = Field(ge=0, le=2**31 - 1)
    gid: int = Field(ge=0, le=2**31 - 1)
    read_only_root: Literal[True]
    drop_all_capabilities: Literal[True]
    no_new_privileges: Literal[True]
    privileged: Literal[False]
    docker_socket_forbidden: Literal[True]
    lsm_profile: str = Field(min_length=1, max_length=256)

    _digests = field_validator("policy_digest", "seccomp_digest")(_digest_field)


class F3NetworkAuthority(_ExactModel):
    policy_digest: str
    mode: Literal["none"]
    docker_network: Literal["none"]
    egress_route_ids: tuple[str, ...] = ()
    default_deny: Literal[True]

    _digest = field_validator("policy_digest")(_digest_field)

    @model_validator(mode="after")
    def no_egress(self) -> "F3NetworkAuthority":
        if self.egress_route_ids:
            raise ValueError("R-SWE terminal authority forbids network egress")
        return self


class F3VerifierAuthority(_ExactModel):
    grant: c.VerifierGrant
    runtime_id: str
    security_policy_digest: str

    _digest = field_validator("security_policy_digest")(_digest_field)


class F3PolicyAuthority(_ExactModel):
    subject: c.AuthenticatedSubject
    validity: c.ValidityWindow
    revocation: c.RevocationBinding
    receipt_ttl_seconds: int = Field(gt=0, le=86400)
    route_id: str = Field(min_length=1, max_length=128)
    target_ip: str
    port: int = Field(ge=1, le=65535)
    owner_id: str = Field(min_length=1, max_length=128)
    secret_handle_id: str = Field(min_length=1, max_length=128)
    secret_handle_version_digest: str
    model: c.ModelIdentity
    provider_id: str = Field(min_length=1, max_length=128)
    bridge_instance_id: str = Field(min_length=1, max_length=128)
    bridge_build_digest: str
    evidence_policy_revision_digest: str
    retention_policy_revision_digest: str
    retention_minimum_seconds: int = Field(ge=0, le=31536000)
    retention_maximum_seconds: int = Field(gt=0, le=31536000)

    _digests = field_validator(
        "secret_handle_version_digest",
        "bridge_build_digest",
        "evidence_policy_revision_digest",
        "retention_policy_revision_digest",
    )(_digest_field)

    @model_validator(mode="after")
    def exact_policy(self) -> "F3PolicyAuthority":
        address = ipaddress.ip_address(self.target_ip)
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise ValueError("policy target must be one exact usable IPv4 address")
        if self.subject.authority_scope_digest != self.revocation.scope_digest:
            raise ValueError("policy subject and revocation scopes differ")
        if self.retention_minimum_seconds > self.retention_maximum_seconds:
            raise ValueError("retention bounds are inverted")
        return self


class F3ReceiptSignerInput(_ExactModel):
    key_id: str = Field(min_length=1, max_length=128)
    secret_path: str

    @field_validator("secret_path")
    @classmethod
    def absolute_secret(cls, value: str) -> str:
        if not Path(value).is_absolute() or os.path.normpath(value) != value:
            raise ValueError("receipt signing secret path must be absolute and normalized")
        return value


class F3AuthorityInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f3-authority-input.v1"]
    composition_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    task: F3TaskAuthority
    repository: F3RepositoryAuthority
    primary_runtime: F3RuntimeAuthority
    primary_image: F3ImageAuthority
    primary_security: F3SecurityAuthority
    verifier_runtime: F3RuntimeAuthority
    verifier_image: F3ImageAuthority
    verifier_security: F3SecurityAuthority
    network: F3NetworkAuthority
    verifier: F3VerifierAuthority
    policy: F3PolicyAuthority
    resources: c.ResourceLimits
    limits: c.ExecutionLimits
    receipt_signer: F3ReceiptSignerInput

    @model_validator(mode="after")
    def exact_terminal_profile(self) -> "F3AuthorityInput":
        if self.primary_runtime.runtime_id == self.verifier_runtime.runtime_id:
            raise ValueError("primary and verifier runtimes must be distinct")
        if self.primary_image.runtime_id != self.primary_runtime.runtime_id:
            raise ValueError("primary image and runtime authorities differ")
        if self.verifier_image.runtime_id != self.verifier_runtime.runtime_id:
            raise ValueError("verifier image and runtime authorities differ")
        if (
            self.verifier.runtime_id != self.verifier_runtime.runtime_id
            or self.verifier.security_policy_digest != self.verifier_security.policy_digest
            or self.verifier.grant.image_digest != self.verifier_image.image_digest
            or self.verifier.grant.network_policy_digest != self.network.policy_digest
        ):
            raise ValueError("verifier authority does not bind its sandbox")
        repository_artifacts = self.task.eligibility.artifacts
        if (
            self.task.task_contract_digest != self.task.eligibility.canonical_digest()
            or len(repository_artifacts) != 1
            or repository_artifacts[0].role != _REPOSITORY_ARTIFACT_ID
            or repository_artifacts[0].digest
            != self.repository.repository_snapshot_digest
        ):
            raise ValueError(
                "task contract must bind only the admitted repository workspace"
            )
        if self.repository.binding_digest != c._canonical_digest(
            {
                "repository_snapshot_digest": self.repository.repository_snapshot_digest,
                "image_digest": self.primary_image.image_digest,
            }
        ):
            raise ValueError("repository binding digest mismatch")
        if self.limits.artifact_bytes_each > self.limits.artifact_bytes_total:
            raise ValueError("artifact bounds are inverted")
        if (
            self.limits.max_turns > 128
            or self.limits.action_timeout_ms > 600_000
            or self.limits.observation_bytes > 16 * 1024 * 1024
            or self.limits.response_bytes > 16 * 1024 * 1024
            or self.limits.artifact_bytes_total > 1024 * 1024 * 1024
            or self.limits.transcript_bytes > 1024 * 1024 * 1024
            or self.limits.setup_timeout_ms > 600_000
            or self.limits.verifier_timeout_ms > 600_000
        ):
            raise ValueError("execution limits exceed the bounded F3 profile")
        if (
            self.resources.cpu_millis > 128_000
            or self.resources.memory_bytes > 1024**4
            or self.resources.storage_bytes > 1024**4
            or self.resources.pids > 32768
            or self.resources.open_files > 1_000_000
            or self.resources.wall_time_ms > 86_400_000
        ):
            raise ValueError("resource limits exceed the bounded F3 profile")
        return self


class F3ArtifactRef(_ExactModel):
    path: str
    sha256: str
    size_bytes: int = Field(ge=1)
    media_type: str

    _digest = field_validator("sha256")(_digest_field)


class F3AuthorityBundleManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f3-authority-bundle.v1"]
    composition_id: str
    attempt_id: str
    task_contract_digest: str
    task_binding_digest: str
    repository_snapshot_digest: str
    runner: c.RunnerGrant
    terminal_tool_ids: tuple[str, ...]
    artifacts: dict[str, F3ArtifactRef]
    cas_root: str

    _digests = field_validator(
        "task_contract_digest", "task_binding_digest", "repository_snapshot_digest"
    )(_digest_field)


def _read_canonical_input(path: str) -> bytes:
    source = Path(path)
    if not source.is_absolute() or os.path.normpath(path) != path:
        raise F3AuthorityAuthoringError("input path must be absolute and normalized")
    raw = source.read_bytes()
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F3AuthorityAuthoringError("input must be UTF-8 JSON") from exc
    if canonical_json_bytes(parsed) != raw:
        raise F3AuthorityAuthoringError("input must be canonical JSON")
    return raw


def _read_signing_key(path: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o400:
            raise F3AuthorityAuthoringError("receipt signing key must be a regular 0400 file")
        payload = os.read(fd, 4097)
        if payload.endswith(b"\n"):
            payload = payload[:-1]
        if (
            len(payload) < 32
            or len(payload) > 4096
            or b"\x00" in payload
            or b"\r" in payload
            or b"\n" in payload
        ):
            raise F3AuthorityAuthoringError("receipt signing key size or content is invalid")
        return payload
    finally:
        os.close(fd)


def _wire(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _registry_from_records(records: dict[str, tuple[c._ConfigRuntimeContract, ...]]) -> c.RegistrySnapshotSet:
    digests: dict[str, str] = {}
    for component, digest_field in _COMPONENTS:
        digests[digest_field] = c.RegistrySnapshotSet.derive_component_digest(
            component, records[component]
        )
    digests["snapshot_digest"] = c.RegistrySnapshotSet.derive_snapshot_digest(digests)
    return c.RegistrySnapshotSet(digests=c.RegistryDigestSet(**digests), **records)


def _terminal_authority() -> tuple[c.RunnerGrant, tuple[c.ToolGrant, ...]]:
    descriptor = TerminalResponsesAdapter(TERMINAL_RUNTIME_ABI).descriptor
    if (
        descriptor.adapter_id != TERMINAL_ADAPTER_ID
        or descriptor.runtime_abi != TERMINAL_RUNTIME_ABI
        or descriptor.implementation_digest != TERMINAL_IMPLEMENTATION_DIGEST
        or TERMINAL_TOOL_DEFINITIONS != terminal_runner.TERMINAL_TOOL_DEFINITIONS
    ):
        raise F3AuthorityAuthoringError("terminal runner authority drift detected")
    runner = c.RunnerGrant(
        adapter_id=descriptor.adapter_id,
        runtime_abi=descriptor.runtime_abi,
        implementation_digest=descriptor.implementation_digest,
    )
    tools = tuple(
        sorted(
            (
                c.ToolGrant(
                    tool_id=definition.tool_id,
                    implementation_digest=TERMINAL_IMPLEMENTATION_DIGEST,
                    capability_ids=(f"terminal.{definition.tool_id}",),
                )
                for definition in TERMINAL_TOOL_DEFINITIONS
            ),
            key=lambda item: item.tool_id,
        )
    )
    if tuple(item.tool_id for item in tools) != tuple(
        sorted(definition.tool_id for definition in TERMINAL_TOOL_DEFINITIONS)
    ):
        raise F3AuthorityAuthoringError("terminal tool authority drift detected")
    return runner, tools


def _sandbox_binding(
    runtime: F3RuntimeAuthority,
    image: F3ImageAuthority,
    security: F3SecurityAuthority,
    network: F3NetworkAuthority,
) -> c.SandboxBinding:
    return c.SandboxBinding(
        runtime_id=runtime.runtime_id,
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=runtime.driver_implementation_digest,
        runtime_binary_digest=runtime.runtime_binary.digest,
        security_policy_digest=security.policy_digest,
        image_digest=image.image_digest,
        network_policy_digest=network.policy_digest,
    )


def _derive_authority(spec: F3AuthorityInput) -> tuple[
    c.CapabilityVector,
    c.RegistrySnapshotSet,
    tuple[c.PolicyCapabilityObservation, ...],
    PolicyHttpAuthorityGraphV1,
    c.OperatorCeiling,
]:
    runner, tools = _terminal_authority()
    primary = _sandbox_binding(
        spec.primary_runtime, spec.primary_image, spec.primary_security, spec.network
    )
    verifier_sandbox = _sandbox_binding(
        spec.verifier_runtime, spec.verifier_image, spec.verifier_security, spec.network
    )
    secret = c.SecretHandleGrant(
        handle_id=spec.policy.secret_handle_id,
        handle_version_digest=spec.policy.secret_handle_version_digest,
        scope_digest=spec.policy.subject.authority_scope_digest,
    )
    ip_projection = {
        "schema_version": "bb.rl.policy-ip-authority.v1",
        "allowed_addresses": [spec.policy.target_ip],
        "allow_loopback": True,
        "allow_private": True,
        "allow_link_local": False,
        "allow_multicast": False,
        "allow_unspecified": False,
    }
    dns_projection = {
        "schema_version": "bb.rl.policy-dns-authority.v1",
        "hostname": spec.policy.target_ip,
        "allowed_addresses": [spec.policy.target_ip],
        "resolution_mode": "pinned",
        "require_all_answers_admitted": True,
        "verify_connected_peer": True,
    }
    ip_doc = IPPolicyDocumentV1(
        schema_version="bb.rl.policy-ip-authority.v1",
        ip_policy_digest=c._canonical_digest(ip_projection),
        allowed_addresses=(spec.policy.target_ip,),
        allow_loopback=True,
        allow_private=True,
        allow_link_local=False,
        allow_multicast=False,
        allow_unspecified=False,
    )
    dns_doc = DNSPolicyDocumentV1(
        schema_version="bb.rl.policy-dns-authority.v1",
        dns_policy_digest=c._canonical_digest(dns_projection),
        hostname=spec.policy.target_ip,
        allowed_addresses=(spec.policy.target_ip,),
        resolution_mode="pinned",
        require_all_answers_admitted=True,
        verify_connected_peer=True,
    )
    dummy_grant = c.RouteGrant(
        route_id=spec.policy.route_id,
        route_revision_digest=_DIGEST_PREFIX + "0" * 64,
        protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
        credential_handle_id=secret.handle_id,
    )
    route_values = {
        "grant": dummy_grant,
        "scheme": c.RouteScheme.HTTPS,
        "authority": f"{spec.policy.target_ip}:{spec.policy.port}",
        "paths": ("/v1/responses",),
        "methods": (c.RouteMethod.POST,),
        "ip_policy_digest": ip_doc.ip_policy_digest,
        "dns_policy_digest": dns_doc.dns_policy_digest,
        "request_schema_digest": POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
        "response_schema_digest": POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
        "max_request_bytes": spec.limits.response_bytes,
        "max_response_bytes": spec.limits.observation_bytes,
        "max_requests_per_minute": 600,
        "data_classification": c.DataClassification.CONFIDENTIAL,
        "owner": c.RouteOwnerAuthority(
            owner_id=spec.policy.owner_id,
            authority_scope_digest=spec.policy.subject.authority_scope_digest,
        ),
    }
    route_unbound = c.RouteRegistryRecord.model_construct(**route_values)
    route_grant = c.RouteGrant(
        route_id=dummy_grant.route_id,
        route_revision_digest=route_unbound.derived_route_revision_digest(),
        protocol_abi=dummy_grant.protocol_abi,
        credential_handle_id=dummy_grant.credential_handle_id,
    )
    route = c.RouteRegistryRecord(**{**route_values, "grant": route_grant})
    policy_caps = c.PolicyCapabilityVector(
        responses_protocol=POLICY_HTTP_PROTOCOL_ABI,
        modalities=("text",),
        tool_calling=True,
        parallel_tool_calls=False,
        token_ids=False,
        token_logprobs=False,
        routing_metadata=False,
        cancellation=True,
        max_context_tokens=spec.limits.observation_bytes,
        max_output_tokens=spec.limits.response_bytes,
        policy_slot_count=1,
        request_features=(),
    )
    observation_values = {
        "registry_revision_digest": _DIGEST_PREFIX + "0" * 64,
        "route_id": route_grant.route_id,
        "route_revision_digest": route_grant.route_revision_digest,
        "provider_id": spec.policy.provider_id,
        "protocol_abi": POLICY_HTTP_PROTOCOL_ABI,
        "bridge_instance_id": spec.policy.bridge_instance_id,
        "bridge_build_digest": spec.policy.bridge_build_digest,
        "model_id": spec.policy.model.model_id,
        "model_digest": spec.policy.model.model_digest,
        "tokenizer_digest": spec.policy.model.tokenizer_digest,
        "checkpoint_digest": spec.policy.model.checkpoint_digest,
        "credential_handle_id": secret.handle_id,
        "credential_handle_version_digest": secret.handle_version_digest,
        "subject_scope_digest": secret.scope_digest,
        "capabilities": policy_caps,
        "capability_digest": _DIGEST_PREFIX + "0" * 64,
        "provenance": c.AttestationProvenance(
            kind=c.AttestationKind.OPERATOR_ATTESTATION,
            issuer_id=spec.policy.owner_id,
            signer_key_id=spec.receipt_signer.key_id,
            environment_digest=c._canonical_digest(
                {"bridge": spec.policy.bridge_build_digest}
            ),
            evidence_digest=c._canonical_digest({"task": spec.task.task_binding_digest}),
            validity=spec.policy.validity,
        ),
        "revocation": spec.policy.revocation,
    }
    observation_unbound = c.PolicyCapabilityObservation.model_construct(**observation_values)
    capability_digest = c._canonical_digest(observation_unbound.selection_capability_obj())
    slot = c.PolicySlotGrant(
        slot_id=_POLICY_SLOT_ID,
        protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
        route_id=route_grant.route_id,
        secret_handle_id=secret.handle_id,
        model_digest=spec.policy.model.model_digest,
        tokenizer_digest=spec.policy.model.tokenizer_digest,
        checkpoint_digest=spec.policy.model.checkpoint_digest,
        required_policy_capabilities_digest=capability_digest,
    )
    evidence_ref = c.PolicyRef(
        policy_id=_EVIDENCE_POLICY_ID,
        revision_digest=spec.policy.evidence_policy_revision_digest,
    )
    retention_ref = c.PolicyRef(
        policy_id=_RETENTION_POLICY_ID,
        revision_digest=spec.policy.retention_policy_revision_digest,
    )
    retention = c.RetentionPolicyGrant(
        policy=retention_ref,
        minimum_seconds=spec.policy.retention_minimum_seconds,
        maximum_seconds=spec.policy.retention_maximum_seconds,
    )
    task = c.TaskGrant(
        task_contract_digest=spec.task.task_contract_digest,
        task_binding_digest=spec.task.task_binding_digest,
        repository_snapshot_digest=spec.repository.repository_snapshot_digest,
        dataset_digests=(),
        input_artifact_digests=(spec.repository.repository_snapshot_digest,),
    )
    artifact_policy = c.ArtifactPolicyGrant(
        allowed_roles=(_RESULT_ROLE,),
        max_each_bytes=spec.limits.artifact_bytes_each,
        max_total_bytes=spec.limits.artifact_bytes_total,
    )
    sandbox = c.SandboxGrant(
        **primary.model_dump(),
        egress_route_ids=(),
        mounts=(
            c.MountGrant(
                source_artifact_digest=spec.repository.repository_snapshot_digest,
                target_logical_path="work",
                access=c.MountAccess.READ_WRITE,
                max_bytes=spec.resources.storage_bytes,
            ),
        ),
    )
    capability = c.CapabilityVector(
        runner=runner,
        tools=tools,
        setup_plans=(),
        routes=(route_grant,),
        secret_handles=(secret,),
        sandbox=sandbox,
        resources=spec.resources,
        limits=spec.limits,
        task=task,
        policy_slots=(slot,),
        verifier=spec.verifier.grant,
        mutable_pointers=(),
        artifacts=artifact_policy,
        evidence=evidence_ref,
        retention=retention_ref,
    )
    attestation_values = {
        "route_id": route_grant.route_id,
        "route_revision_digest": route_grant.route_revision_digest,
        "model_digest": spec.policy.model.model_digest,
        "tokenizer_digest": spec.policy.model.tokenizer_digest,
        "checkpoint_digest": spec.policy.model.checkpoint_digest,
        "capability_digest": capability_digest,
        "validity": spec.policy.validity,
        "revocation": spec.policy.revocation,
        "authorized_signer_key_ids": (spec.receipt_signer.key_id,),
        "signature_verification_policy_digest": c._canonical_digest(
            {"algorithm": "hmac-sha256-v1", "key_id": spec.receipt_signer.key_id}
        ),
        "attestation_digest": _DIGEST_PREFIX + "0" * 64,
    }
    attestation_unbound = c.PolicyCapabilityAttestationRecord.model_construct(**attestation_values)
    attestation = c.PolicyCapabilityAttestationRecord(
        **{
            **attestation_values,
            "attestation_digest": attestation_unbound.derived_attestation_digest(),
        }
    )
    repository_record = c.RepositoryBindingRegistryRecord(
        binding_digest=spec.repository.binding_digest,
        repository_snapshot_digest=spec.repository.repository_snapshot_digest,
        image_digest=spec.primary_image.image_digest,
    )
    tool_by_id = {definition.tool_id: definition for definition in TERMINAL_TOOL_DEFINITIONS}
    tool_records = tuple(
        c.ToolRegistryRecord(
            grant=grant,
            argument_schema_digest=c._canonical_digest(
                _wire(tool_by_id[grant.tool_id].responses_schema)["parameters"]
            ),
            result_schema_digest=c._canonical_digest(
                {"type": "object", "additionalProperties": True}
            ),
            reserved=False,
        )
        for grant in tools
    )
    records: dict[str, tuple[c._ConfigRuntimeContract, ...]] = {
        "runners": (c.RunnerRegistryRecord(grant=runner),),
        "tools": tool_records,
        "setups": (),
        "routes": (route,),
        "secret_handles": (
            c.SecretHandleRegistryRecord(grant=secret, route_ids=(route_grant.route_id,)),
        ),
        "sandbox_runtimes": tuple(
            sorted(
                (
                    c.SandboxRuntimeRegistryRecord(binding=primary),
                    c.SandboxRuntimeRegistryRecord(binding=verifier_sandbox),
                ),
                key=lambda item: item.binding.runtime_id,
            )
        ),
        "images": tuple(
            sorted(
                (
                    c.ImageRegistryRecord(
                        image_digest=primary.image_digest,
                        runtime_id=primary.runtime_id,
                        repository_binding_digests=(repository_record.binding_digest,),
                    ),
                    c.ImageRegistryRecord(
                        image_digest=verifier_sandbox.image_digest,
                        runtime_id=verifier_sandbox.runtime_id,
                        repository_binding_digests=(),
                    ),
                ),
                key=lambda item: item.image_digest,
            )
        ),
        "repository_bindings": (repository_record,),
        "task_datasets": (c.TaskDatasetRegistryRecord(**task.model_dump()),),
        "models": (c.ModelRegistryRecord(identity=spec.policy.model),),
        "verifiers": (
            c.VerifierRegistryRecord(
                grant=spec.verifier.grant,
                runtime_id=verifier_sandbox.runtime_id,
                runtime_class=verifier_sandbox.runtime_class,
                security_policy_digest=verifier_sandbox.security_policy_digest,
            ),
        ),
        "evidence_policies": (
            c.EvidencePolicyRegistryRecord(policy=evidence_ref, required_roles=(_RESULT_ROLE,)),
        ),
        "retention_policies": (c.RetentionPolicyRegistryRecord(grant=retention),),
        "policy_capability_attestations": (attestation,),
    }
    registries = _registry_from_records(records)
    observation = c.PolicyCapabilityObservation(
        **{
            **observation_values,
            "registry_revision_digest": registries.digests.route_registry_digest,
            "capability_digest": capability_digest,
        }
    )
    policy_http = PolicyHttpAuthorityGraphV1(
        registry_revision_digest=registries.digests.route_registry_digest,
        routes=(route,),
        observations=(observation,),
        dns_policies=(dns_doc,),
        ip_policies=(ip_doc,),
        schema_authority=PolicyHttpSchemaAuthorityV1(
            schema_version="bb.rl.policy-http-schema-authority.v1",
            protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
            request_schema=POLICY_HTTP_REQUEST_SCHEMA,
            request_schema_digest=POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
            response_schema=POLICY_HTTP_RESPONSE_SCHEMA,
            response_schema_digest=POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
        ),
        secret_bindings=(
            PolicySecretRouteBindingV1(
                schema_version="bb.rl.policy-secret-route-binding.v1",
                handle_id=secret.handle_id,
                handle_version_digest=secret.handle_version_digest,
                scope_digest=secret.scope_digest,
                route_ids=(route_grant.route_id,),
            ),
        ),
    )
    ceiling = c.OperatorCeiling(
        runner_bindings=(runner,),
        tool_grants=tools,
        setup_grants=(),
        route_grants=(route_grant,),
        secret_handle_grants=(secret,),
        sandbox_bindings=tuple(
            sorted((primary, verifier_sandbox), key=lambda item: (item.runtime_id, item.image_digest))
        ),
        repository_snapshot_digests=(spec.repository.repository_snapshot_digest,),
        task_contract_digests=(task.task_contract_digest,),
        task_binding_digests=(task.task_binding_digest,),
        dataset_digests=(),
        model_bindings=(spec.policy.model,),
        verifier_grants=(spec.verifier.grant,),
        policy_slot_grants=(slot,),
        evidence_policies=(evidence_ref,),
        retention_policies=(retention,),
        mutable_pointer_rules=(),
        resource_maxima=spec.resources,
        execution_maxima=spec.limits,
        allowed_egress_route_ids=(),
        mount_grants=sandbox.mounts,
        artifact_policy_maximum=artifact_policy,
    )
    return capability, registries, (observation,), policy_http, ceiling


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
        provenance_digest=c._canonical_digest(
            [item.to_canonical_obj() for item in manifest.provenance]
        ),
        diagnostics_digest=c._canonical_digest(manifest.diagnostics.to_canonical_obj()),
    )


def _tool_member(definition: Any) -> dict[str, Any]:
    schema = _wire(definition.responses_schema)
    properties = schema["parameters"]["properties"]
    required = set(schema["parameters"].get("required", ()))
    return {
        "id": definition.tool_id,
        "name": schema["name"],
        "description": schema["description"],
        "parameters": [
            {
                "name": name,
                "description": f"Canonical {definition.tool_id} argument {name}.",
                "required": name in required,
                "schema": value,
            }
            for name, value in properties.items()
        ],
        "execution": {"blocking": True, "max_per_turn": 1},
    }


def _write_exclusive(directory_fd: int, name: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short artifact write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def build_f3_authority(spec: F3AuthorityInput, output_dir: str) -> str:
    """Compile, admit, and publish one exact R-SWE-001 terminal authority bundle."""
    if type(spec) is not F3AuthorityInput:
        raise TypeError("spec must be an exact F3AuthorityInput")
    destination = Path(output_dir)
    if not destination.is_absolute() or os.path.normpath(output_dir) != output_dir:
        raise F3AuthorityAuthoringError("output directory must be absolute and normalized")
    if os.path.lexists(destination):
        raise F3AuthorityAuthoringError("output directory already exists")
    key = _read_signing_key(spec.receipt_signer.secret_path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = destination.parent / f".{destination.name}.authoring-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        artifacts_dir = staging / "artifacts"
        artifacts_dir.mkdir(mode=0o700)
        cas = FilesystemCAS(staging / "cas")
        (staging / "cas").chmod(0o700)
        try:
            capability, registries, observations, policy_http, ceiling = _derive_authority(spec)
            tool_ids = tuple(item.tool_id for item in capability.tools)
            config = {
                "version": 2,
                "extends": ["base-config.json"],
                "profile": {
                    "name": "r-swe-001-terminal",
                    "metadata": {
                        "breadboard_rl_authority": {
                            "requested_capabilities": capability.model_dump(mode="json"),
                            "task_binding_digest": capability.task.task_binding_digest,
                        }
                    },
                },
                "workspace": {"root": "workspace"},
                "providers": {
                    "default_model": spec.policy.model.model_id,
                    "models": [
                        {
                            "id": spec.policy.model.model_id,
                            "adapter": "openai_responses",
                            "route_handle_id": spec.policy.route_id,
                            "credential_handle_id": spec.policy.secret_handle_id,
                            "params": {"temperature": 0},
                        }
                    ],
                },
                "prompts": {"injection": {"system_order": [], "per_turn_order": []}},
                "tools": {"registry": {"paths": ["tools"], "include": list(tool_ids), "exclude": []}},
                "modes": [{"id": "build", "prompt": spec.task.prompt, "tools_enabled": list(tool_ids)}],
                "loop": {"sequence": ["build"]},
            }
            member_bytes = {
                "base-config.json": canonical_json_bytes(
                    {"provider_tools": {"api_variant": "responses", "use_native": True}}
                ),
                "r-swe-001-terminal.json": canonical_json_bytes(config),
                **{
                    f"tools/{definition.tool_id}.yaml": canonical_json_bytes(
                        _tool_member(definition)
                    )
                    for definition in TERMINAL_TOOL_DEFINITIONS
                },
            }
            bundle = ingest_member_map(
                member_bytes,
                cas,
                entrypoints={"main": "r-swe-001-terminal.json"},
                source_label="phase5-f3-r-swe-001-terminal",
                media_types={name: "application/json" for name in member_bytes},
            )
            edges = (
                DependencyEdge(
                    "r-swe-001-terminal.json", "extends", "base-config.json", "base-config.json", 0
                ),
                *tuple(
                    DependencyEdge(
                        "r-swe-001-terminal.json",
                        "tool_registry",
                        "tools",
                        f"tools/{tool_id}.yaml",
                        index,
                    )
                    for index, tool_id in enumerate(tool_ids)
                ),
            )
            closure = build_dependency_closure(bundle, root_entrypoint="main", edges=edges)
            reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
            options = CompileOptions.from_dict(
                {
                    "schema_id": "bb.compile-options.v1",
                    "source_contract": "v2",
                    "v1_loss_policy": "reject_all",
                    "target": {
                        "runner_adapter_id": TERMINAL_ADAPTER_ID,
                        "runtime_abi": TERMINAL_RUNTIME_ABI,
                    },
                    "task_contract": {
                        "contract_id": spec.task.task_id.lower(),
                        "parameter_schema": {
                            "type": "object",
                            "properties": {
                                "task_id": {"const": spec.task.task_id},
                                "repository_snapshot_digest": {
                                    "const": spec.repository.repository_snapshot_digest
                                },
                            },
                            "required": ["task_id", "repository_snapshot_digest"],
                            "additionalProperties": False,
                        },
                        "artifacts": [
                            {
                                "artifact_id": _REPOSITORY_ARTIFACT_ID,
                                "direction": "input",
                                "media_types": [
                                    "application/vnd.breadboard.repository-workspace+tar"
                                ],
                                "required": True,
                                "cardinality": "one",
                                "max_bytes": spec.resources.storage_bytes,
                            },
                            {
                                "artifact_id": _RESULT_ROLE,
                                "direction": "output",
                                "media_types": ["application/json"],
                                "required": True,
                                "cardinality": "one",
                                "max_bytes": spec.limits.artifact_bytes_each,
                            },
                        ],
                        "verifier": {
                            "binding_id": spec.verifier.grant.verifier_id,
                            "input_artifact_ids": [_RESULT_ROLE],
                            "result_schema": _RESULT_SCHEMA,
                            "timeout_ms": spec.limits.verifier_timeout_ms,
                        },
                        "evidence": {
                            "required_event_types": ["turn.completed"],
                            "required_artifact_ids": [_RESULT_ROLE],
                        },
                        "retention": {
                            "retention_class_id": _RETENTION_POLICY_ID,
                            "minimum_retention_seconds": spec.policy.retention_minimum_seconds,
                        },
                    },
                }
            )
            from breadboard_engine.compilation.server_compiler import compile_config

            compiled_manifest = compile_config(reader, closure, options)
            compiled_bytes = compiled_manifest.canonical_bytes()
            compiled_digest = _digest(compiled_bytes)
            compiled = _compiled_identity(compiled_manifest, compiled_digest)
            policy = c.AdmissionPolicySnapshot(
                policy_id="phase5-f3-r-swe-001-terminal",
                revision="f3-" + registries.digests.snapshot_digest[7:39],
                subject_scope_digest=spec.policy.subject.authority_scope_digest,
                compiler_constraints=c.CompilerConstraints(allowed_compilers=(compiled.compiler,)),
                registry_digests=registries.digests,
                ceiling=ceiling,
                required_security=c.RequiredSecurityPolicy(
                    minimum_isolation_class=c.RuntimeClass.HARDENED_DOCKER,
                    required_verifier_isolation_class=c.RuntimeClass.HARDENED_DOCKER,
                    required_evidence_roles=(_RESULT_ROLE,),
                    prohibited_runtime_classes=(c.RuntimeClass.TRUSTED_PROCESS,),
                    minimum_retention_seconds=spec.policy.retention_minimum_seconds,
                ),
                receipt_ttl_seconds=spec.policy.receipt_ttl_seconds,
                validity=spec.policy.validity,
                revocation=spec.policy.revocation,
            )
            attestation = registries.policy_capability_attestations[0]
            admission_request = c.AdmissionRequest(
                subject=spec.policy.subject,
                behavior_source=c.CompiledBehaviorSource(
                    manifest_digest=compiled_digest,
                    semantic_digest=compiled_manifest.semantic_digest,
                ),
                compiled=compiled,
                requested_capabilities=capability,
                requested_capability_digest=capability.canonical_digest(),
                task_binding_digest=capability.task.task_binding_digest,
                policy_binding_ref=c.PolicyBindingRef(
                    route_id=attestation.route_id,
                    registry_revision_digest=registries.digests.route_registry_digest,
                    attestation_digest=attestation.attestation_digest,
                ),
                admission_policy_digest=policy.canonical_digest(),
                registry_snapshot_digest=registries.digests.snapshot_digest,
                validity=spec.policy.validity,
                parent_receipt_digest=None,
                overlay_chain_digest=None,
            )
            store = CASConfigRuntimeStore(cas)
            compiled_ref = store.publish(
                kind=c.ArtifactKind.COMPILED_MANIFEST,
                canonical_bytes=compiled_bytes,
            )
            if compiled_ref.sha256 != compiled_digest:
                raise F3AuthorityAuthoringError(
                    "compiled manifest CAS identity mismatch"
                )
            clock = type(
                "_PinnedClock",
                (),
                {
                    "current": lambda self: datetime.strptime(
                        spec.policy.validity.not_before, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=UTC)
                },
            )()
            runtime = ConfigRuntime(
                compiler=PinnedServerCompilerAdapter({compiled_digest: compiled_bytes}),
                policy=policy,
                registries=registries,
                revocations=PinnedRevocationStore((policy.revocation,)),
                store=store,
                clock=clock,
                authenticator=HmacSha256ReceiptAuthenticator(
                    key_id=spec.receipt_signer.key_id, key=key
                ),
            )
            receipt_ref = runtime.admit(admission_request)
            receipt_bytes = store.load(
                receipt_ref.digest,
                kind=c.ArtifactKind.ADMISSION_RECEIPT,
                max_bytes=4 * 1024 * 1024,
            )
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
            admitted_ref = store.publish(
                kind=c.ArtifactKind.ADMITTED_SET, canonical_bytes=admitted_bytes
            )
            selector = c.DirectSelector(
                admitted_set_root=admitted_ref.sha256,
                compiler_abi=admitted.compiler_abi,
                runtime_abi=receipt.compiled.compiler.runtime_abi,
                admission_policy_digest=receipt.admission_policy_digest,
                operator_ceiling_digest=receipt.operator_ceiling_digest,
                candidate=c.ConfigCandidate(
                    candidate_id=spec.attempt_id,
                    receipt_digest=receipt_ref.digest,
                    predicates=(),
                    overlays=(),
                ),
                validity=receipt.validity,
            )
            selector_bytes = selector.canonical_bytes()
            store.publish(kind=c.ArtifactKind.DIRECT_SELECTOR, canonical_bytes=selector_bytes)
            objects = {
                "config-bundle.json": bundle.canonical_bytes(),
                "config-closure.json": closure.canonical_bytes(),
                "compiled-manifest.json": compiled_bytes,
                "admission-policy.json": policy.canonical_bytes(),
                "registry-snapshot.json": registries.canonical_bytes(),
                "admission-receipt.json": receipt_bytes,
                "admitted-set.json": admitted_bytes,
                "direct-selector.json": selector_bytes,
                "policy-capabilities.json": canonical_json_bytes(
                    [item.model_dump(mode="json") for item in observations]
                ),
                "policy-http.json": policy_http.canonical_bytes(),
                "revocations.json": canonical_json_bytes(
                    [policy.revocation.model_dump(mode="json")]
                ),
            }
            directory_fd = os.open(
                artifacts_dir,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                for name, payload in objects.items():
                    _write_exclusive(directory_fd, name, payload)
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            artifact_refs = {
                name: F3ArtifactRef(
                    path=os.fspath((destination / "artifacts" / name).resolve()),
                    sha256=_digest(payload),
                    size_bytes=len(payload),
                    media_type=_MEDIA_TYPES[name],
                )
                for name, payload in sorted(objects.items())
            }
            manifest = F3AuthorityBundleManifest(
                schema_version="bb.rl.phase5-f3-authority-bundle.v1",
                composition_id=spec.composition_id,
                attempt_id=spec.attempt_id,
                task_contract_digest=spec.task.task_contract_digest,
                task_binding_digest=spec.task.task_binding_digest,
                repository_snapshot_digest=spec.repository.repository_snapshot_digest,
                runner=capability.runner,
                terminal_tool_ids=tool_ids,
                artifacts=artifact_refs,
                cas_root=os.fspath((destination / "cas").resolve()),
            )
            staging_fd = os.open(
                staging,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                _write_exclusive(
                    staging_fd,
                    "manifest.json",
                    canonical_json_bytes(manifest.model_dump(mode="json")),
                )
                os.fsync(staging_fd)
            finally:
                os.close(staging_fd)
        finally:
            cas.close()
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


def author_f3_authority(input_path: str, output_dir: str) -> str:
    """Read one canonical exact input and publish its F3 authority bundle."""
    raw = _read_canonical_input(input_path)
    spec = F3AuthorityInput.model_validate_json(raw, strict=True)
    return build_f3_authority(spec, output_dir)


__all__ = [
    "F3ArtifactRef",
    "F3AuthorityAuthoringError",
    "F3AuthorityBundleManifest",
    "F3AuthorityInput",
    "F3ImageAuthority",
    "F3NetworkAuthority",
    "F3PolicyAuthority",
    "F3ReceiptSignerInput",
    "F3RepositoryAuthority",
    "F3RuntimeAuthority",
    "F3SecurityAuthority",
    "F3TaskAuthority",
    "F3VerifierAuthority",
    "ImmutableAuthorityRef",
    "author_f3_authority",
    "build_f3_authority",
]
