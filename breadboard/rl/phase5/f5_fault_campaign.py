from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DIGEST_PREFIX = "sha256:"

FaultClass = Literal[
    "timeout",
    "cancel",
    "revocation",
    "egress",
    "resource",
    "verifier",
    "artifact",
    "transport",
]

FAULT_CLASSES: tuple[str, ...] = (
    "timeout",
    "cancel",
    "revocation",
    "egress",
    "resource",
    "verifier",
    "artifact",
    "transport",
)
ENUMERATED_FAULT_NON_CLAIM = (
    "Enumerated timeout, cancel, revocation, egress, resource, verifier, artifact, "
    "and transport fault containment only; no general high-availability claim."
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
Identifier = Annotated[
    str,
    Field(min_length=1, max_length=512, pattern=_IDENTIFIER_PATTERN),
]

# This table is the closed F5 injection contract. In particular, revocation and
# wrapper transport are rejected before sandbox allocation; the other six
# injections are observed only after an accepted allocation.
_FAULT_EXPECTATIONS: dict[str, tuple[str, str]] = {
    "timeout": ("TIMEOUT", "post-allocation"),
    "cancel": ("CANCELLED", "post-allocation"),
    "revocation": ("REVOKED", "pre-allocation"),
    "egress": ("EGRESS_DENIED", "post-allocation"),
    "resource": ("RESOURCE_EXHAUSTED", "post-allocation"),
    "verifier": ("VERIFIER_FAILED", "post-allocation"),
    "artifact": ("ARTIFACT_FAILED", "post-allocation"),
    "transport": ("TRANSPORT_FAILED", "pre-allocation"),
}

_VALIDATION_CHECKS: tuple[str, ...] = (
    "exact-enumerated-fault-closure",
    "one-no-fault-twin-per-fault",
    "exact-twin-non-fault-identities",
    "expected-lifecycle-and-error-classification",
    "reward-publication-quarantine",
    "authoritative-closed-cleanup",
    "pre-and-post-allocation-boundaries",
    "current-non-superseded-target-attempts",
    "exact-target-identity-joins",
    "no-fallback",
    "concurrent-heterogeneous-identity-isolation",
    "unique-canaries-and-no-cross-read",
    "zero-orphans-and-no-unexpected-outcomes",
)


class F5FaultCampaignError(ValueError):
    """Raised when canonical campaign authoring cannot publish safely."""


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
        raise ValueError("F5 identities require a lowercase sha256 digest")
    return value


class F5PinnedIdentity(_ExactModel):
    identity_id: Identifier
    digest: str
    immutable_ref: str = Field(min_length=1, max_length=4096)

    _digest = field_validator("digest")(_digest_field)

    @model_validator(mode="after")
    def content_addressed(self) -> "F5PinnedIdentity":
        if (
            "://" not in self.immutable_ref
            or "?" in self.immutable_ref
            or "#" in self.immutable_ref
            or any(character.isspace() for character in self.immutable_ref)
            or not self.immutable_ref.endswith("@" + self.digest)
        ):
            raise ValueError("identity reference must be an exact content-addressed URI")
        return self


class F5IdentityClosure(_ExactModel):
    authority: F5PinnedIdentity
    effective_plan: F5PinnedIdentity
    config: F5PinnedIdentity
    task: F5PinnedIdentity
    model: F5PinnedIdentity
    image: F5PinnedIdentity
    runtime: F5PinnedIdentity
    verifier: F5PinnedIdentity


class F5TargetAttemptRef(_ExactModel):
    attempt_id: Identifier
    attempt_manifest: F5PinnedIdentity
    status: Literal["succeeded"]
    current: Literal[True]
    superseded_by_attempt_id: None
    job_id: Identifier
    node_id: Identifier


class F5TargetEpisodeRef(_ExactModel):
    episode_id: Identifier
    output: F5PinnedIdentity
    state: Literal["closed"]


class F5TargetIdentityJoin(_ExactModel):
    authority_digest: str
    effective_plan_digest: str
    config_digest: str
    task_digest: str
    model_digest: str
    image_digest: str
    runtime_digest: str
    verifier_digest: str
    job_id: Identifier
    node_id: Identifier
    runtime_id: Identifier

    _digests = field_validator(
        "authority_digest",
        "effective_plan_digest",
        "config_digest",
        "task_digest",
        "model_digest",
        "image_digest",
        "runtime_digest",
        "verifier_digest",
    )(_digest_field)


class F5TargetExecutionRef(_ExactModel):
    attempt: F5TargetAttemptRef
    episode: F5TargetEpisodeRef
    evidence: F5PinnedIdentity
    join: F5TargetIdentityJoin
    fallback_used: Literal[False]


class F5ExpectedOutcome(_ExactModel):
    lifecycle: Literal["succeeded", "failed"]
    error_class: str | None = Field(min_length=1, max_length=128)
    failure_boundary: Literal["pre-allocation", "post-allocation"]
    reward: Literal[1] | None
    reward_quarantined: bool
    lease_opened: bool


class F5ObservedOutcome(_ExactModel):
    lifecycle: Literal["succeeded", "failed"]
    error_class: str | None = Field(min_length=1, max_length=128)
    failure_boundary: Literal["pre-allocation", "post-allocation"]
    reward: Literal[1] | None
    reward_quarantined: bool
    lease_opened: bool
    unexpected_outcomes: tuple[str, ...]

    @model_validator(mode="after")
    def reward_and_outcome_truth(self) -> "F5ObservedOutcome":
        if self.unexpected_outcomes:
            raise ValueError("F5 rows cannot contain unexpected outcomes")
        if self.lifecycle == "succeeded":
            if self.error_class is not None or self.reward != 1 or self.reward_quarantined:
                raise ValueError("successful rows require reward 1 and no error or quarantine")
        elif self.error_class is None or self.reward is not None or not self.reward_quarantined:
            raise ValueError("failed rows require a typed error and quarantined unpublished reward")
        return self


class F5CleanupObservation(_ExactModel):
    authority: Literal["breadboard_episode_service"]
    envelope_state: Literal["closed"]
    cleanup_required: bool
    cleanup_attempts: int = Field(ge=0, le=1024)
    remaining_actors: Literal[0]
    remaining_processes: Literal[0]
    remaining_containers: Literal[0]
    remaining_cgroups: Literal[0]
    remaining_mounts: Literal[0]
    remaining_workspaces: Literal[0]
    remaining_secret_files: Literal[0]
    remaining_orphan_ids: tuple[str, ...]
    cleanup_error_classes: tuple[str, ...]

    @model_validator(mode="after")
    def no_orphans(self) -> "F5CleanupObservation":
        if self.remaining_orphan_ids:
            raise ValueError("authoritative cleanup reports an orphan")
        if self.cleanup_error_classes:
            raise ValueError("authoritative cleanup did not close cleanly")
        if self.cleanup_required != (self.cleanup_attempts > 0):
            raise ValueError("cleanup requirement and attempt count disagree")
        return self


class F5FaultInjection(_ExactModel):
    fault_class: FaultClass
    injection_spec: F5PinnedIdentity


class F5ExecutionRow(_ExactModel):
    row_id: Identifier
    identities: F5IdentityClosure
    workspace_id: Identifier
    container_id: Identifier
    canary: Identifier
    canary_reads: tuple[Identifier, ...]
    target: F5TargetExecutionRef
    expected: F5ExpectedOutcome
    observed: F5ObservedOutcome
    cleanup: F5CleanupObservation
    fault_injection: F5FaultInjection | None

    @model_validator(mode="after")
    def closed_execution_row(self) -> "F5ExecutionRow":
        if self.canary_reads != (self.canary,):
            raise ValueError("row must read its own unique canary exactly once")
        expected_outcome = (
            self.expected.lifecycle,
            self.expected.error_class,
            self.expected.failure_boundary,
            self.expected.reward,
            self.expected.reward_quarantined,
            self.expected.lease_opened,
        )
        observed_outcome = (
            self.observed.lifecycle,
            self.observed.error_class,
            self.observed.failure_boundary,
            self.observed.reward,
            self.observed.reward_quarantined,
            self.observed.lease_opened,
        )
        if observed_outcome != expected_outcome:
            raise ValueError("observed lifecycle/reward/allocation outcome differs from expectation")

        join = self.target.join
        identities = self.identities
        expected_digests = (
            identities.authority.digest,
            identities.effective_plan.digest,
            identities.config.digest,
            identities.task.digest,
            identities.model.digest,
            identities.image.digest,
            identities.runtime.digest,
            identities.verifier.digest,
        )
        joined_digests = (
            join.authority_digest,
            join.effective_plan_digest,
            join.config_digest,
            join.task_digest,
            join.model_digest,
            join.image_digest,
            join.runtime_digest,
            join.verifier_digest,
        )
        if joined_digests != expected_digests:
            raise ValueError("target attempt identity join drifts from the row identity closure")
        if join.job_id != self.target.attempt.job_id or join.node_id != self.target.attempt.node_id:
            raise ValueError("target job/node join drifts from the current target attempt")
        if join.runtime_id != identities.runtime.identity_id:
            raise ValueError("target runtime join drifts from the runtime identity")

        allocated = self.expected.failure_boundary == "post-allocation"
        if self.expected.lease_opened != allocated:
            raise ValueError("lease allocation does not match the declared failure boundary")
        if self.cleanup.cleanup_required != allocated:
            raise ValueError("cleanup behavior does not match the allocation boundary")
        return self


class F5FaultPair(_ExactModel):
    pair_id: Identifier
    fault_class: FaultClass
    fault: F5ExecutionRow
    twin: F5ExecutionRow

    @model_validator(mode="after")
    def exact_pair(self) -> "F5FaultPair":
        if self.fault.row_id == self.twin.row_id:
            raise ValueError("fault and twin row IDs must be distinct")
        invariant_fields = (
            "authority",
            "config",
            "task",
            "model",
            "image",
            "runtime",
            "verifier",
        )
        if any(
            getattr(self.fault.identities, field)
            != getattr(self.twin.identities, field)
            for field in invariant_fields
        ):
            raise ValueError(
                "no-fault twin must preserve every invariant campaign identity"
            )
        if self.fault.fault_injection is None:
            raise ValueError("fault row requires its exact injected fault")
        if self.fault.fault_injection.fault_class != self.fault_class:
            raise ValueError("fault injection class differs from its pair")
        if self.twin.fault_injection is not None:
            raise ValueError("no-fault twin cannot carry a fault injection")

        error_class, boundary = _FAULT_EXPECTATIONS[self.fault_class]
        fault_expectation = F5ExpectedOutcome(
            lifecycle="failed",
            error_class=error_class,
            failure_boundary=boundary,
            reward=None,
            reward_quarantined=True,
            lease_opened=boundary == "post-allocation",
        )
        twin_expectation = F5ExpectedOutcome(
            lifecycle="succeeded",
            error_class=None,
            failure_boundary="post-allocation",
            reward=1,
            reward_quarantined=False,
            lease_opened=True,
        )
        if self.fault.expected != fault_expectation:
            raise ValueError("fault row does not use the closed F5 error/allocation expectation")
        if self.twin.expected != twin_expectation:
            raise ValueError("no-fault twin is not an exact successful control")
        return self


class F5CampaignInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f5-fault-campaign-input.v1"]
    campaign_id: Identifier
    fault_pairs: tuple[F5FaultPair, ...]
    concurrent_rows: tuple[F5ExecutionRow, ...] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def complete_closed_campaign(self) -> "F5CampaignInput":
        if len(self.fault_pairs) != len(FAULT_CLASSES):
            raise ValueError("campaign requires exactly one pair for each enumerated fault")
        classes = tuple(pair.fault_class for pair in self.fault_pairs)
        if classes != FAULT_CLASSES:
            raise ValueError("fault pairs must be complete, unique, and in canonical fault order")

        pair_ids = tuple(pair.pair_id for pair in self.fault_pairs)
        all_rows = tuple(
            row
            for pair in self.fault_pairs
            for row in (pair.fault, pair.twin)
        ) + self.concurrent_rows
        if len(set(pair_ids)) != len(pair_ids):
            raise ValueError("fault pair IDs must be unique")
        row_ids = tuple(row.row_id for row in all_rows)
        if len(set(row_ids)) != len(row_ids):
            raise ValueError("campaign row IDs must be unique")

        for row in self.concurrent_rows:
            if row.fault_injection is not None:
                raise ValueError("concurrency controls cannot carry a fault injection")
            if row.expected.lifecycle != "succeeded":
                raise ValueError("concurrency controls must be successful no-fault rows")

        distinct_fields = {
            "effective plan": tuple(row.identities.effective_plan.digest for row in self.concurrent_rows),
            "workspace": tuple(row.workspace_id for row in self.concurrent_rows),
            "container": tuple(row.container_id for row in self.concurrent_rows),
            "evidence": tuple(row.target.evidence.digest for row in self.concurrent_rows),
            "canary": tuple(row.canary for row in self.concurrent_rows),
            "attempt": tuple(row.target.attempt.attempt_id for row in self.concurrent_rows),
            "episode": tuple(row.target.episode.episode_id for row in self.concurrent_rows),
        }
        for name, values in distinct_fields.items():
            if len(set(values)) != len(values):
                raise ValueError(f"concurrent rows share a {name} identity")
        return self


class F5CampaignManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f5-fault-campaign-manifest.v1"]
    campaign_id: Identifier
    input_digest: str
    enumerated_fault_classes: tuple[FaultClass, ...]
    fault_pairs: tuple[F5FaultPair, ...]
    concurrent_rows: tuple[F5ExecutionRow, ...]
    non_claim: Literal[
        "Enumerated timeout, cancel, revocation, egress, resource, verifier, artifact, and transport fault containment only; no general high-availability claim."
    ]

    _digest = field_validator("input_digest")(_digest_field)

    @model_validator(mode="after")
    def remains_closed(self) -> "F5CampaignManifest":
        F5CampaignInput(
            schema_version="bb.rl.phase5-f5-fault-campaign-input.v1",
            campaign_id=self.campaign_id,
            fault_pairs=self.fault_pairs,
            concurrent_rows=self.concurrent_rows,
        )
        if self.enumerated_fault_classes != FAULT_CLASSES:
            raise ValueError("manifest fault enumeration is not the closed F5 set")
        return self


class F5ValidationReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f5-fault-campaign-validation.v1"]
    campaign_id: Identifier
    manifest_digest: str
    valid: Literal[True]
    fault_pair_count: Literal[8]
    fault_row_count: Literal[8]
    twin_row_count: Literal[8]
    concurrent_row_count: int = Field(ge=2, le=32)
    enumerated_fault_classes: tuple[FaultClass, ...]
    checks: tuple[str, ...]
    unexpected_outcomes: tuple[str, ...]
    non_claim: Literal[
        "Enumerated timeout, cancel, revocation, egress, resource, verifier, artifact, and transport fault containment only; no general high-availability claim."
    ]

    _digest = field_validator("manifest_digest")(_digest_field)

    @model_validator(mode="after")
    def exact_report(self) -> "F5ValidationReport":
        if self.enumerated_fault_classes != FAULT_CLASSES:
            raise ValueError("validation report does not name the exact fault closure")
        if self.checks != _VALIDATION_CHECKS:
            raise ValueError("validation report check closure drifted")
        if self.unexpected_outcomes:
            raise ValueError("a passing validation report cannot contain unexpected outcomes")
        return self


class F5CampaignArtifacts(_ExactModel):
    manifest_path: str
    validation_report_path: str
    manifest_digest: str
    validation_report_digest: str

    _digests = field_validator("manifest_digest", "validation_report_digest")(_digest_field)


def build_f5_campaign_manifest(spec: F5CampaignInput) -> F5CampaignManifest:
    input_payload = canonical_json_bytes(spec.model_dump(mode="json"))
    return F5CampaignManifest(
        schema_version="bb.rl.phase5-f5-fault-campaign-manifest.v1",
        campaign_id=spec.campaign_id,
        input_digest=_digest(input_payload),
        enumerated_fault_classes=FAULT_CLASSES,
        fault_pairs=spec.fault_pairs,
        concurrent_rows=spec.concurrent_rows,
        non_claim=ENUMERATED_FAULT_NON_CLAIM,
    )


def validate_f5_campaign_manifest(
    manifest: F5CampaignManifest,
) -> F5ValidationReport:
    """Revalidate the complete manifest and return its closed validation report."""
    # Round-trip through the public model so callers cannot bypass validators with
    # an unvalidated model construction API.
    validated = F5CampaignManifest.model_validate(
        manifest.model_dump(mode="python"), strict=True
    )
    manifest_payload = canonical_json_bytes(validated.model_dump(mode="json"))
    return F5ValidationReport(
        schema_version="bb.rl.phase5-f5-fault-campaign-validation.v1",
        campaign_id=validated.campaign_id,
        manifest_digest=_digest(manifest_payload),
        valid=True,
        fault_pair_count=8,
        fault_row_count=8,
        twin_row_count=8,
        concurrent_row_count=len(validated.concurrent_rows),
        enumerated_fault_classes=FAULT_CLASSES,
        checks=_VALIDATION_CHECKS,
        unexpected_outcomes=(),
        non_claim=ENUMERATED_FAULT_NON_CLAIM,
    )


def _read_canonical_input(path: str) -> bytes:
    source = Path(path)
    if not source.is_absolute() or os.path.normpath(path) != path:
        raise F5FaultCampaignError("input path must be absolute and normalized")
    try:
        metadata = source.lstat()
    except OSError as error:
        raise F5FaultCampaignError(f"cannot inspect campaign input: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise F5FaultCampaignError("campaign input must be a regular non-symlink file")
    raw = source.read_bytes()
    try:
        parsed: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise F5FaultCampaignError("campaign input is not valid UTF-8 JSON") from error
    if canonical_json_bytes(parsed) != raw:
        raise F5FaultCampaignError("campaign input must use exact canonical JSON bytes")
    return raw


def _write_exclusive(directory: Path, name: str, payload: bytes) -> None:
    path = directory / name
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while publishing F5 campaign")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_f5_fault_campaign(spec: F5CampaignInput, output_dir: str) -> F5CampaignArtifacts:
    """Atomically publish a canonical F5 manifest and validation report."""
    output = Path(output_dir)
    if not output.is_absolute() or os.path.normpath(output_dir) != output_dir:
        raise F5FaultCampaignError("output directory must be absolute and normalized")
    if output.exists():
        raise F5FaultCampaignError("output directory already exists")
    if not output.parent.is_dir():
        raise F5FaultCampaignError("output parent directory does not exist")

    manifest = build_f5_campaign_manifest(spec)
    report = validate_f5_campaign_manifest(manifest)
    manifest_payload = canonical_json_bytes(manifest.model_dump(mode="json"))
    report_payload = canonical_json_bytes(report.model_dump(mode="json"))
    staging = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    try:
        staging.mkdir(mode=0o700)
        _write_exclusive(staging, "f5-campaign-manifest.json", manifest_payload)
        _write_exclusive(staging, "f5-validation-report.json", report_payload)
        directory_descriptor = os.open(staging, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        os.rename(staging, output)
        parent_descriptor = os.open(output.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    manifest_path = output / "f5-campaign-manifest.json"
    report_path = output / "f5-validation-report.json"
    return F5CampaignArtifacts(
        manifest_path=os.fspath(manifest_path),
        validation_report_path=os.fspath(report_path),
        manifest_digest=_digest(manifest_payload),
        validation_report_digest=_digest(report_payload),
    )


def author_f5_fault_campaign(input_path: str, output_dir: str) -> F5CampaignArtifacts:
    raw = _read_canonical_input(input_path)
    spec = F5CampaignInput.model_validate_json(raw, strict=True)
    return publish_f5_fault_campaign(spec, output_dir)


__all__ = [
    "ENUMERATED_FAULT_NON_CLAIM",
    "FAULT_CLASSES",
    "F5CampaignArtifacts",
    "F5CampaignInput",
    "F5CampaignManifest",
    "F5CleanupObservation",
    "F5ExecutionRow",
    "F5ExpectedOutcome",
    "F5FaultCampaignError",
    "F5FaultInjection",
    "F5FaultPair",
    "F5IdentityClosure",
    "F5ObservedOutcome",
    "F5PinnedIdentity",
    "F5TargetAttemptRef",
    "F5TargetEpisodeRef",
    "F5TargetExecutionRef",
    "F5TargetIdentityJoin",
    "F5ValidationReport",
    "author_f5_fault_campaign",
    "build_f5_campaign_manifest",
    "publish_f5_fault_campaign",
    "validate_f5_campaign_manifest",
]
