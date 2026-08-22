from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import functools
import hashlib
import json
import os
import re
import stat
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness.contracts import (
    EffectiveExecutionPlan,
    EvidencePolicyRegistryRecord,
    RetentionPolicyRegistryRecord,
    SelectionCommitToken,
)
from breadboard.rl.state.cas import ArtifactIntegrityError, CASReader
from breadboard.rl.state.state_ref import ArtifactRef
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    SandboxCleanupReceipt,
    VerifierSnapshotReceipt,
)
from breadboard.rl.harness.runners.base import RunnerResult

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EPISODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_ARTIFACT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}\Z")
MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_TRAVERSAL_OBJECTS = 10000
_PRIMARY_CLEANUP_RESOURCES = (
    "child_verifier",
    "runtime",
    "workspace",
    "cache_holder",
    "lease_record",
)
_VERIFIER_CLEANUP_RESOURCES = ("runtime", "workspace", "lease_record")
_SCHEMA_MEDIA = "application/vnd.breadboard.evidence+json"


class EvidenceError(RuntimeError):
    pass


class EvidenceValidationError(EvidenceError, ValueError):
    pass


class EvidenceCorruptError(EvidenceError):
    pass


class LocatorConflictError(EvidenceError):
    pass


class ExportDeniedError(EvidenceError):
    pass


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _check_digest(value: str, name: str = "digest") -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise EvidenceValidationError(f"{name} must be a full lowercase sha256 digest")
    return value


def _ref_obj(ref: ArtifactRef) -> dict[str, Any]:
    _check_digest(ref.sha256, "artifact sha256")
    return ref.to_dict()


def _ref_from(value: Mapping[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=str(value["artifact_id"]),
        sha256=str(value["sha256"]),
        size_bytes=int(value["size_bytes"]),
        media_type=str(value.get("media_type", "application/octet-stream")),
        metadata=dict(value.get("metadata", {})),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ArtifactRef):
        return _ref_obj(value)
    if dataclasses.is_dataclass(value):
        return {
            f.name: _json_value(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if not f.name.startswith("_")
        }
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise EvidenceValidationError("canonical objects require string keys")
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    canonical_json_bytes(value)
    return value


def canonical_digest(value: Any) -> str:
    return _digest(canonical_json_bytes(_json_value(value)))


def _selection_commit_digest(
    resolved_plan: Mapping[str, Any],
    *,
    resolved_plan_digest: str,
) -> str:
    selection_commit = resolved_plan.get("selection_commit")
    if selection_commit is not None:
        try:
            normalized = SelectionCommitToken.model_validate(selection_commit)
        except (TypeError, ValueError) as exc:
            raise EvidenceValidationError(
                "selection commit must be an exact canonical token"
            ) from exc
        return _check_digest(
            str(normalized.canonical_digest()),
            "selection commit digest",
        )
    selection_digest = resolved_plan.get("selection_digest")
    if selection_digest is not None:
        return _check_digest(selection_digest, "selection digest")
    selection_record_ref = resolved_plan.get("selection_record_ref")
    if isinstance(selection_record_ref, Mapping):
        return _check_digest(
            selection_record_ref.get("sha256"),
            "selection record digest",
        )
    return canonical_digest(
        {"kind": "selection", "resolved_plan_digest": resolved_plan_digest}
    )


class CanonicalRecord:
    def to_canonical_obj(self) -> dict[str, Any]:
        return _json_value(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_obj())

    @property
    def digest(self) -> str:
        return _digest(self.canonical_bytes())


class EvidenceRoleSourceV2(str, Enum):
    RUNNER_RESULT = "runner_result"
    VERIFIER_SNAPSHOT_RECEIPT = "verifier_snapshot_receipt"
    VERIFIER_RESULT = "verifier_result"


@dataclass(frozen=True, slots=True)
class EvidenceRoleBindingV2(CanonicalRecord):
    role: str
    source: EvidenceRoleSourceV2
    producer_id: str
    producer_implementation_digest: str
    schema_version: str = field(default="bb.rl.evidence-role-binding.v2", init=False)

    def __post_init__(self) -> None:
        if type(self.role) is not str or not self.role:
            raise EvidenceValidationError(
                "evidence role binding role must be a non-empty string"
            )
        if type(self.source) is not EvidenceRoleSourceV2:
            raise EvidenceValidationError("evidence role binding source must be exact")
        if type(self.producer_id) is not str or not self.producer_id:
            raise EvidenceValidationError(
                "evidence role binding producer ID must be a non-empty string"
            )
        if type(self.producer_implementation_digest) is not str:
            raise EvidenceValidationError(
                "producer implementation digest must be a string"
            )
        _check_digest(
            self.producer_implementation_digest,
            "producer implementation digest",
        )


@dataclass(frozen=True, slots=True)
class EvidenceObjectInputV2:
    role: str
    source: EvidenceRoleSourceV2
    producer_id: str
    producer_implementation_digest: str
    payload: bytes
    media_type: str
    parent_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.role or not self.producer_id or not self.media_type:
            raise EvidenceValidationError("evidence object input identity is required")
        _check_digest(
            self.producer_implementation_digest,
            "producer implementation digest",
        )
        if not isinstance(self.payload, bytes):
            raise EvidenceValidationError(
                "evidence object input must own immutable bytes"
            )
        object.__setattr__(self, "payload", bytes(self.payload))
        parents = tuple(sorted(set(self.parent_digests)))
        for parent in parents:
            _check_digest(parent, "evidence parent digest")
        object.__setattr__(self, "parent_digests", parents)


@dataclass(frozen=True, slots=True)
class EvidenceAuthorityPlanV2(CanonicalRecord):
    effective_plan_digest: str
    allowed_roles: tuple[str, ...]
    required_roles: tuple[str, ...]
    max_each_bytes: int
    max_total_bytes: int
    evidence_policy_ref: str
    retention_policy_ref: str
    bindings: tuple[EvidenceRoleBindingV2, ...]
    schema_version: str = field(default="bb.rl.evidence-authority-plan.v2", init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.effective_plan_digest, "effective plan digest"),
            (self.evidence_policy_ref, "evidence policy ref"),
            (self.retention_policy_ref, "retention policy ref"),
        ):
            _check_digest(value, name)
        allowed = tuple(sorted(set(self.allowed_roles)))
        required = tuple(sorted(set(self.required_roles)))
        bindings = tuple(sorted(self.bindings, key=lambda item: item.role))
        binding_roles = tuple(item.role for item in bindings)
        if len(binding_roles) != len(set(binding_roles)):
            raise EvidenceValidationError("evidence role bindings must be unique")
        if not set(required) <= set(binding_roles) or not set(binding_roles) <= set(
            allowed
        ):
            raise EvidenceValidationError(
                "evidence bindings do not satisfy role authority"
            )
        if not set(required) <= set(allowed):
            raise EvidenceValidationError("required evidence roles must be allowed")
        if self.max_each_bytes <= 0 or self.max_total_bytes <= 0:
            raise EvidenceValidationError("evidence publication limits are invalid")
        if self.max_each_bytes > self.max_total_bytes:
            raise EvidenceValidationError("per-object limit exceeds total limit")
        object.__setattr__(self, "allowed_roles", allowed)
        object.__setattr__(self, "required_roles", required)
        object.__setattr__(self, "bindings", bindings)


class V2EvidenceAuthority:
    def __init__(self, role_bindings: Iterable[EvidenceRoleBindingV2]) -> None:
        bindings = tuple(sorted(role_bindings, key=lambda item: item.role))
        if len({item.role for item in bindings}) != len(bindings):
            raise EvidenceValidationError(
                "evidence authority role bindings are duplicated"
            )
        self._bindings = {item.role: item for item in bindings}

    def validate_plan(
        self,
        effective_plan: EffectiveExecutionPlan,
        evidence_policy: EvidencePolicyRegistryRecord,
        retention_policy: RetentionPolicyRegistryRecord,
    ) -> EvidenceAuthorityPlanV2:
        if type(effective_plan) is not EffectiveExecutionPlan:
            raise EvidenceValidationError(
                "evidence authority requires an exact effective plan"
            )
        if type(evidence_policy) is not EvidencePolicyRegistryRecord:
            raise EvidenceValidationError("evidence policy record must be exact")
        if type(retention_policy) is not RetentionPolicyRegistryRecord:
            raise EvidenceValidationError("retention policy record must be exact")
        if evidence_policy.policy != effective_plan.evidence:
            raise EvidenceValidationError(
                "evidence policy revision does not match the plan"
            )
        if retention_policy.grant.policy != effective_plan.retention:
            raise EvidenceValidationError(
                "retention policy revision does not match the plan"
            )
        allowed = tuple(effective_plan.artifacts.allowed_roles)
        bindings = tuple(
            self._bindings[role] for role in allowed if role in self._bindings
        )
        if not set(evidence_policy.required_roles) <= {item.role for item in bindings}:
            raise EvidenceValidationError(
                "required evidence role has no installed authority"
            )
        return EvidenceAuthorityPlanV2(
            effective_plan_digest=canonical_digest(effective_plan),
            allowed_roles=allowed,
            required_roles=tuple(evidence_policy.required_roles),
            max_each_bytes=effective_plan.artifacts.max_each_bytes,
            max_total_bytes=effective_plan.artifacts.max_total_bytes,
            evidence_policy_ref=canonical_digest(evidence_policy),
            retention_policy_ref=canonical_digest(retention_policy),
            bindings=bindings,
        )

    def materialize(
        self,
        plan: EvidenceAuthorityPlanV2,
        *,
        runner_result: RunnerResult,
        verifier_snapshot: VerifierSnapshotReceipt,
        verifier_result: Mapping[str, Any],
    ) -> tuple[EvidenceObjectInputV2, ...]:
        if type(plan) is not EvidenceAuthorityPlanV2:
            raise EvidenceValidationError("evidence authority plan must be exact")
        if type(runner_result) is not RunnerResult:
            raise EvidenceValidationError("runner evidence source must be exact")
        if type(verifier_snapshot) is not VerifierSnapshotReceipt:
            raise EvidenceValidationError("verifier snapshot source must be exact")
        if not isinstance(verifier_result, Mapping):
            raise EvidenceValidationError("verifier result must be a mapping")
        inputs: list[EvidenceObjectInputV2] = []
        for binding in plan.bindings:
            if binding.source is EvidenceRoleSourceV2.RUNNER_RESULT:
                source_value = runner_result
            elif binding.source is EvidenceRoleSourceV2.VERIFIER_SNAPSHOT_RECEIPT:
                source_value = verifier_snapshot
            else:
                if binding.role not in verifier_result:
                    if binding.role in plan.required_roles:
                        raise EvidenceValidationError(
                            "required verifier evidence role is absent"
                        )
                    continue
                source_value = verifier_result[binding.role]
            payload = canonical_json_bytes(_json_value(source_value))
            inputs.append(
                EvidenceObjectInputV2(
                    role=binding.role,
                    source=binding.source,
                    producer_id=binding.producer_id,
                    producer_implementation_digest=binding.producer_implementation_digest,
                    payload=payload,
                    media_type="application/json",
                    parent_digests=(),
                )
            )
        if not set(plan.required_roles) <= {item.role for item in inputs}:
            raise EvidenceValidationError(
                "required evidence roles were not materialized"
            )
        return tuple(inputs)


_LIFECYCLE_STATES = frozenset(
    {
        "accepted",
        "allocating",
        "ready",
        "running",
        "verifying",
        "completed",
        "cancel_requested",
        "closing",
        "closed",
        "quarantined",
    }
)
_LEGAL_TRANSITIONS = frozenset(
    {
        ("accepted", "allocating", "allocation_started"),
        ("accepted", "cancel_requested", "cancellation_requested"),
        ("allocating", "cancel_requested", "cancellation_requested"),
        ("allocating", "ready", "workspace_ready"),
        ("allocating", "closing", "allocation_failed"),
        ("allocating", "closing", "workspace_ready_failed"),
        ("ready", "running", "run_started"),
        ("ready", "cancel_requested", "cancellation_requested"),
        ("ready", "closing", "process_interrupted"),
        ("running", "verifying", "runner_terminal"),
        ("running", "cancel_requested", "cancellation_requested"),
        ("running", "closing", "run_failed"),
        ("running", "closing", "cancellation_won"),
        ("running", "closing", "process_interrupted"),
        ("verifying", "completed", "completed"),
        ("verifying", "closing", "verification_failed"),
        ("verifying", "closing", "completed_publication_failed"),
        ("verifying", "closing", "process_interrupted"),
        ("completed", "closing", "cleanup_started"),
        ("completed", "closing", "restart_cleanup_reconciled"),
        ("cancel_requested", "closing", "cancellation_won"),
        ("cancel_requested", "closing", "run_failed"),
        ("cancel_requested", "closing", "process_interrupted"),
        ("closing", "closing", "cleanup_released"),
        ("closing", "closed", "closed"),
        ("quarantined", "closing", "restart_cleanup_reconciled"),
    }
)


@dataclass(frozen=True, slots=True)
class SafeFailureFactV2(CanonicalRecord):
    category: str
    code: str
    retry_disposition: str
    side_effect_boundary: str
    turn: int | None = None
    call_id: str | None = None
    lease_id: str | None = None
    detail: str | None = None
    schema_version: str = field(default="bb.rl.safe-failure.v2", init=False)

    def __post_init__(self) -> None:
        for name in ("category", "code", "retry_disposition", "side_effect_boundary"):
            value = getattr(self, name)
            if not value or len(value) > 128:
                raise EvidenceValidationError(f"{name} is invalid")
        if self.detail is not None and (
            len(self.detail) > 512 or _contains_unsafe_secret(self.detail)
        ):
            raise EvidenceValidationError("failure detail is unsafe")


def _cancellation_fingerprint(
    episode_id: str,
    create_fingerprint: str,
    reason: str,
) -> str:
    return canonical_digest(
        {
            "schema_version": "bb.rl.episode-cancel-fingerprint.v1",
            "episode_id": episode_id,
            "create_fingerprint": create_fingerprint,
            "reason": reason,
        }
    )


@dataclass(frozen=True, slots=True)
class LifecycleEventV2(CanonicalRecord):
    episode_id: str
    sequence: int
    previous_event_digest: str | None
    from_state: str | None
    to_state: str
    event_kind: str
    observed_at: str
    create_fingerprint: str | None = None
    run_fingerprint: str | None = None
    effective_plan_digest: str | None = None
    fact_refs: tuple[ArtifactRef, ...] = ()
    fact_digests: tuple[str, ...] = ()
    primary_fact: SafeFailureFactV2 | None = None
    cleanup_fact: SafeFailureFactV2 | None = None
    primary_lease_id: str | None = None
    cancel_reason: str | None = None
    cancel_fingerprint: str | None = None
    schema_version: str = field(default="bb.rl.lifecycle-event.v2", init=False)

    def __post_init__(self) -> None:
        if (
            self.sequence < 0
            or not self.episode_id
            or not self.to_state
            or not self.event_kind
        ):
            raise EvidenceValidationError("invalid lifecycle event identity")
        if self.to_state not in _LIFECYCLE_STATES or (
            self.from_state is not None and self.from_state not in _LIFECYCLE_STATES
        ):
            raise EvidenceValidationError("unknown lifecycle state")
        if self.sequence == 0 and self.previous_event_digest is not None:
            raise EvidenceValidationError("first event cannot have a previous digest")
        if self.sequence > 0 and self.previous_event_digest is None:
            raise EvidenceValidationError("non-first event requires a previous digest")
        for value in (
            self.previous_event_digest,
            self.create_fingerprint,
            self.run_fingerprint,
            self.effective_plan_digest,
            self.cancel_fingerprint,
            *self.fact_digests,
        ):
            if value is not None:
                _check_digest(value)
        if self.primary_lease_id is not None and (
            not isinstance(self.primary_lease_id, str)
            or not self.primary_lease_id
            or len(self.primary_lease_id) > 256
            or _contains_unsafe_secret(self.primary_lease_id)
        ):
            raise EvidenceValidationError("primary lease identity is invalid")
        if self.event_kind == "workspace_ready" and self.primary_lease_id is None:
            raise EvidenceValidationError(
                "workspace-ready event requires a primary lease identity"
            )
        if (self.cancel_reason is None) != (self.cancel_fingerprint is None):
            raise EvidenceValidationError(
                "cancellation reason and fingerprint must be paired"
            )
        if self.cancel_reason is not None:
            normalized_reason = " ".join(self.cancel_reason.split())
            if (
                not normalized_reason
                or normalized_reason != self.cancel_reason
                or len(normalized_reason) > 256
            ):
                raise EvidenceValidationError(
                    "cancellation reason must be 1..256 normalized characters"
                )
            if self.create_fingerprint is None:
                raise EvidenceValidationError(
                    "cancellation receipt requires the create fingerprint"
                )
            expected_cancel_fingerprint = _cancellation_fingerprint(
                self.episode_id,
                self.create_fingerprint,
                normalized_reason,
            )
            if self.cancel_fingerprint != expected_cancel_fingerprint:
                raise EvidenceValidationError(
                    "cancellation fingerprint does not bind episode, create, and reason"
                )


def _validate_transition(
    previous: LifecycleEventV2 | None,
    event: LifecycleEventV2,
) -> None:
    if previous is None:
        if (
            event.sequence != 0
            or event.previous_event_digest is not None
            or event.from_state is not None
            or event.to_state != "accepted"
            or event.event_kind != "accepted"
        ):
            raise EvidenceValidationError("initial lifecycle event must be accepted")
        if event.cancel_reason is not None or event.cancel_fingerprint is not None:
            raise EvidenceValidationError(
                "initial lifecycle event cannot contain a cancellation receipt"
            )
        return
    if previous.to_state == "closed":
        raise LocatorConflictError("closed lifecycle is absorbing")
    if (
        event.sequence != previous.sequence + 1
        or event.previous_event_digest != previous.digest
        or event.episode_id != previous.episode_id
        or event.from_state != previous.to_state
    ):
        raise EvidenceValidationError("lifecycle event continuity is invalid")
    edge = (event.from_state, event.to_state, event.event_kind)
    if event.to_state == "quarantined":
        if event.from_state == "closed" or event.event_kind != "quarantined":
            raise EvidenceValidationError("illegal quarantine lifecycle transition")
    elif edge not in _LEGAL_TRANSITIONS:
        raise EvidenceValidationError("illegal lifecycle transition")
    prior_cancel = previous.cancel_reason is not None
    current_cancel = event.cancel_reason is not None
    if not prior_cancel:
        if current_cancel != (event.event_kind == "cancellation_requested"):
            raise EvidenceValidationError(
                "cancellation receipt must first appear on cancellation-requested"
            )
        if current_cancel and (
            previous.create_fingerprint is None
            or event.create_fingerprint != previous.create_fingerprint
        ):
            raise EvidenceValidationError(
                "cancellation receipt is not bound to the accepted create identity"
            )
    elif (
        not current_cancel
        or event.cancel_reason != previous.cancel_reason
        or event.cancel_fingerprint != previous.cancel_fingerprint
    ):
        raise EvidenceValidationError(
            "cancellation receipt changed or disappeared from lifecycle"
        )
    for name in (
        "create_fingerprint",
        "run_fingerprint",
        "effective_plan_digest",
        "primary_lease_id",
    ):
        prior = getattr(previous, name)
        current = getattr(event, name)
        if prior is not None and current != prior:
            raise EvidenceValidationError(f"lifecycle {name} identity changed")
    if previous.primary_lease_id is None and event.primary_lease_id is not None:
        cancellation_cleanup_owns_lease = (
            edge == ("cancel_requested", "closing", "cancellation_won")
            and previous.from_state == "allocating"
            and previous.event_kind == "cancellation_requested"
            and event.primary_fact is not None
            and event.primary_fact.category == "cancellation"
            and event.primary_fact.lease_id == event.primary_lease_id
        )
        if event.event_kind != "workspace_ready" and not cancellation_cleanup_owns_lease:
            raise EvidenceValidationError(
                "primary lease identity has no authoritative lifecycle owner"
            )


@dataclass(frozen=True, slots=True)
class RunnerEventLedgerV2(CanonicalRecord):
    episode_id: str
    effective_plan_digest: str
    events: tuple[Any, ...]
    runner_result_digest: str
    event_count: int = field(init=False)
    first_sequence: int | None = field(init=False)
    last_sequence: int | None = field(init=False)
    schema_version: str = field(default="bb.rl.runner-event-ledger.v2", init=False)

    def __post_init__(self) -> None:
        _check_digest(self.effective_plan_digest)
        _check_digest(self.runner_result_digest)
        events = tuple(self.events)
        event_objects = tuple(_json_value(event) for event in events)
        seqs = [int(event["sequence"]) for event in event_objects]
        if seqs != list(range(len(seqs))):
            raise EvidenceValidationError(
                "runner event sequence is not contiguous from zero"
            )
        for event in event_objects:
            if event.get("episode_id") != self.episode_id:
                raise EvidenceValidationError("runner event episode identity mismatch")
            if event.get("effective_plan_digest") != self.effective_plan_digest:
                raise EvidenceValidationError(
                    "runner event effective-plan identity mismatch"
                )
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "event_count", len(events))
        object.__setattr__(self, "first_sequence", seqs[0] if seqs else None)
        object.__setattr__(self, "last_sequence", seqs[-1] if seqs else None)

    @property
    def ledger_digest(self) -> str:
        return self.digest


@dataclass(frozen=True, slots=True)
class EvidenceObjectV2(CanonicalRecord):
    role: str
    producer: str
    artifact_ref: ArtifactRef
    authorization_policy_ref: str
    retention_policy_ref: str
    parent_digests: tuple[str, ...] = ()
    schema_version: str = field(default="bb.rl.evidence-object.v2", init=False)

    def __post_init__(self) -> None:
        artifact_id = self.artifact_ref.artifact_id
        if (
            _SAFE_ARTIFACT_ID_RE.fullmatch(artifact_id) is None
            or ".." in artifact_id.split("/")
            or _contains_unsafe_secret(artifact_id)
        ):
            raise EvidenceValidationError("artifact locator is unsafe")
        if not self.role or not self.producer:
            raise EvidenceValidationError("evidence role and producer are required")
        parents = tuple(sorted(set(self.parent_digests)))
        for p in parents:
            _check_digest(p)
        if self.artifact_ref.sha256 in parents:
            raise EvidenceValidationError("evidence object cannot parent itself")
        object.__setattr__(self, "parent_digests", parents)


@dataclass(frozen=True, slots=True)
class ArtifactManifestV2(CanonicalRecord):
    objects: tuple[EvidenceObjectV2, ...]
    allowed_roles: tuple[str, ...]
    max_each_bytes: int
    max_total_bytes: int
    required_roles: tuple[str, ...] = ()
    total_byte_count: int = field(init=False)
    schema_version: str = field(default="bb.rl.artifact-manifest.v2", init=False)

    def __post_init__(self) -> None:
        objects = tuple(
            sorted(self.objects, key=lambda x: (x.role, x.artifact_ref.sha256))
        )
        roles = [x.role for x in objects]
        if len(roles) != len(set(roles)):
            raise EvidenceValidationError("artifact roles must be unique")
        allowed = tuple(sorted(set(self.allowed_roles)))
        required = tuple(sorted(set(self.required_roles)))
        if not set(roles) <= set(allowed) or not set(required) <= set(roles):
            raise EvidenceValidationError("artifact role policy rejected manifest")
        if self.max_each_bytes < 0 or self.max_total_bytes < 0:
            raise EvidenceValidationError("artifact byte limits must be non-negative")
        if any(x.artifact_ref.size_bytes > self.max_each_bytes for x in objects):
            raise EvidenceValidationError("artifact exceeds per-role byte limit")
        total = sum(x.artifact_ref.size_bytes for x in objects)
        if total > self.max_total_bytes:
            raise EvidenceValidationError("artifacts exceed total byte limit")
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "allowed_roles", allowed)
        object.__setattr__(self, "required_roles", required)
        object.__setattr__(self, "total_byte_count", total)

    @property
    def manifest_digest(self) -> str:
        return self.digest


@dataclass(frozen=True, slots=True)
class LineageNodeV2(CanonicalRecord):
    node_digest: str
    kind: str
    producer: str
    parent_digests: tuple[str, ...] = ()
    schema_version: str = field(default="bb.rl.lineage-node.v2", init=False)

    def __post_init__(self) -> None:
        _check_digest(self.node_digest, "node digest")
        parents = tuple(sorted(set(self.parent_digests)))
        if self.node_digest in parents:
            raise EvidenceValidationError("lineage node cannot parent itself")
        for p in parents:
            _check_digest(p)
        object.__setattr__(self, "parent_digests", parents)


def validate_lineage(
    nodes: Sequence[LineageNodeV2], root_digest: str | None = None
) -> tuple[LineageNodeV2, ...]:
    by_id = {n.node_digest: n for n in nodes}
    if len(by_id) != len(nodes) or not by_id:
        raise EvidenceValidationError("lineage contains duplicate nodes or is empty")
    children: dict[str, list[str]] = {k: [] for k in by_id}
    indegree = {k: 0 for k in by_id}
    for node in nodes:
        for parent in node.parent_digests:
            if parent not in by_id:
                raise EvidenceValidationError("lineage references an unknown parent")
            children[parent].append(node.node_digest)
            indegree[node.node_digest] += 1
    roots = [k for k, degree in indegree.items() if degree == 0]
    terminals = [k for k, descendants in children.items() if not descendants]
    if len(roots) != 1 or len(terminals) != 1:
        raise EvidenceValidationError(
            "lineage must have exactly one dependency root and terminal"
        )
    ready = sorted(roots)
    ordered: list[LineageNodeV2] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(ordered) != len(nodes):
        raise EvidenceValidationError("lineage contains a cycle")
    if root_digest is not None:
        _check_digest(root_digest, "lineage root")
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class AuthorityAccessEventV2(CanonicalRecord):
    sequence: int
    actor_episode_id: str
    authority_episode_id: str
    authority_ref: str
    canary: str
    source_ref: str
    schema_version: str = field(default="bb.rl.authority-access-event.v2", init=False)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise EvidenceValidationError(
                "authority access sequence must be a positive integer"
            )
        for name, value in (
            ("actor episode", self.actor_episode_id),
            ("authority episode", self.authority_episode_id),
            ("authority canary", self.canary),
        ):
            if type(value) is not str or not value:
                raise EvidenceValidationError(f"{name} must be a non-empty string")
        for name, value in (
            ("authority ref", self.authority_ref),
            ("authority source ref", self.source_ref),
        ):
            if (
                type(value) is not str
                or "://" not in value
                or "?" in value
                or "#" in value
                or any(character.isspace() for character in value)
                or "@sha256:" not in value
            ):
                raise EvidenceValidationError(
                    f"{name} must be an immutable content-addressed reference"
                )
            _check_digest(value.rsplit("@", 1)[-1], name)


@dataclass(frozen=True, slots=True)
class AuthorityAccessLedgerV2(CanonicalRecord):
    episode_id: str
    events: tuple[AuthorityAccessEventV2, ...]
    schema_version: str = field(default="bb.rl.authority-access-ledger.v2", init=False)

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if (
            type(self.episode_id) is not str
            or not self.episode_id
            or not events
            or any(type(event) is not AuthorityAccessEventV2 for event in events)
            or any(event.actor_episode_id != self.episode_id for event in events)
            or tuple(event.sequence for event in events)
            != tuple(range(1, len(events) + 1))
            or len({event.source_ref for event in events}) != 1
        ):
            raise EvidenceValidationError(
                "authority access ledger is not exact, ordered, and episode-scoped"
            )
        object.__setattr__(self, "events", events)

    @property
    def canary_reads(self) -> tuple[str, ...]:
        return tuple(
            event.canary
            for event in self.events
            if event.authority_episode_id == self.episode_id
        )

    @property
    def cross_episode_reads(self) -> tuple[str, ...]:
        return tuple(
            event.authority_episode_id
            for event in self.events
            if event.authority_episode_id != self.episode_id
        )


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceManifestV2(CanonicalRecord):
    episode_id: str
    resolved_plan_digest: str
    selection_digest: str
    effective_plan_digest: str
    policy_binding_digest: str
    runner_ledger_ref: ArtifactRef
    materialization_digest: str
    primary_measurement_digest: str | None
    verifier_snapshot_digest: str | None
    verifier_measurement_digest: str | None
    verifier_result_digest: str | None
    artifact_manifest_ref: ArtifactRef
    primary_disposition: str
    reward_disposition: str
    reward_components: Mapping[str, Any]
    evidence_policy_ref: str
    retention_policy_ref: str
    lineage_nodes: tuple[LineageNodeV2, ...]
    lineage_root: str
    verifier_cleanup_receipt_ref: ArtifactRef | None = None
    verifier_cleanup_lease_id: str | None = None
    retention_policy_record_ref: ArtifactRef | None = None
    primary_failure_digest: str | None = None
    authority_access_ledger_ref: ArtifactRef | None = None
    authority_canary_reads: tuple[str, ...] = ()
    authority_cross_episode_reads: tuple[str, ...] = ()
    schema_version: str = field(
        default="bb.rl.execution-evidence-manifest.v2", init=False
    )

    def __post_init__(self) -> None:
        for value in (
            self.resolved_plan_digest,
            self.selection_digest,
            self.effective_plan_digest,
            self.policy_binding_digest,
            self.materialization_digest,
            self.primary_measurement_digest,
            self.primary_failure_digest,
            self.verifier_snapshot_digest,
            self.verifier_measurement_digest,
            self.verifier_result_digest,
        ):
            if value is not None:
                _check_digest(value)
        _check_digest(self.evidence_policy_ref, "evidence policy ref")
        _check_digest(self.retention_policy_ref, "retention policy ref")
        if (
            self.retention_policy_record_ref is not None
            and self.retention_policy_record_ref.sha256 != self.retention_policy_ref
        ):
            raise EvidenceValidationError(
                "retention policy record reference does not bind policy revision"
            )
        if (self.verifier_cleanup_receipt_ref is None) != (
            self.verifier_cleanup_lease_id is None
        ):
            raise EvidenceValidationError(
                "verifier cleanup receipt and authoritative lease must be paired"
            )
        if self.verifier_cleanup_lease_id is not None and (
            not isinstance(self.verifier_cleanup_lease_id, str)
            or not self.verifier_cleanup_lease_id
            or len(self.verifier_cleanup_lease_id) > 256
            or _contains_unsafe_secret(self.verifier_cleanup_lease_id)
        ):
            raise EvidenceValidationError("verifier cleanup lease identity is invalid")
        for values in (
            self.authority_canary_reads,
            self.authority_cross_episode_reads,
        ):
            if type(values) is not tuple or any(
                type(value) is not str or not value for value in values
            ):
                raise EvidenceValidationError(
                    "authority access audit is not an exact string tuple"
                )
        if (self.authority_access_ledger_ref is None) != (
            not self.authority_canary_reads and not self.authority_cross_episode_reads
        ):
            raise EvidenceValidationError(
                "authority access ledger must bind every projected authority read"
            )
        nodes = validate_lineage(self.lineage_nodes, self.lineage_root)
        expected: list[tuple[str, str, tuple[str, ...]]] = [
            ("resolved_plan", self.resolved_plan_digest, ()),
            ("selection", self.selection_digest, (self.resolved_plan_digest,)),
            ("effective_plan", self.effective_plan_digest, (self.selection_digest,)),
            (
                "policy_binding",
                self.policy_binding_digest,
                (self.effective_plan_digest,),
            ),
            (
                "materialization",
                self.materialization_digest,
                (self.policy_binding_digest,),
            ),
        ]
        previous = self.materialization_digest
        if self.primary_measurement_digest is not None:
            expected.append(
                (
                    "primary_measurement",
                    self.primary_measurement_digest,
                    (previous,),
                )
            )
            previous = self.primary_measurement_digest
        if self.primary_failure_digest is not None:
            expected.append(
                ("primary_failure", self.primary_failure_digest, (previous,))
            )
            previous = self.primary_failure_digest
        expected.extend(
            (
                ("runner_ledger", self.runner_ledger_ref.sha256, (previous,)),
                (
                    "artifact_manifest",
                    self.artifact_manifest_ref.sha256,
                    (self.runner_ledger_ref.sha256,),
                ),
            )
        )
        previous = self.artifact_manifest_ref.sha256
        for kind, digest in (
            ("verifier_snapshot", self.verifier_snapshot_digest),
            ("verifier_measurement", self.verifier_measurement_digest),
            ("verifier_result", self.verifier_result_digest),
        ):
            if digest is not None:
                expected.append((kind, digest, (previous,)))
                previous = digest
        if self.verifier_cleanup_receipt_ref is not None:
            expected.append(
                (
                    "verifier_cleanup",
                    self.verifier_cleanup_receipt_ref.sha256,
                    (previous,),
                )
            )
            previous = self.verifier_cleanup_receipt_ref.sha256
        if self.authority_access_ledger_ref is not None:
            expected.append(
                (
                    "authority_access_ledger",
                    self.authority_access_ledger_ref.sha256,
                    (previous,),
                )
            )
        actual = sorted(
            (node.kind, node.node_digest, node.parent_digests) for node in nodes
        )
        if actual != sorted(expected):
            raise EvidenceValidationError("lineage semantic field binding mismatch")
        object.__setattr__(self, "lineage_nodes", nodes)
        reward = _json_value(self.reward_components)
        canonical_json_bytes(reward)
        commitment = {
            "schema_version": "bb.rl.lineage-commitment.v2",
            "episode_id": self.episode_id,
            "resolved_plan_digest": self.resolved_plan_digest,
            "selection_digest": self.selection_digest,
            "effective_plan_digest": self.effective_plan_digest,
            "policy_binding_digest": self.policy_binding_digest,
            "runner_ledger_ref": _ref_obj(self.runner_ledger_ref),
            "materialization_digest": self.materialization_digest,
            "primary_measurement_digest": self.primary_measurement_digest,
            "primary_failure_digest": self.primary_failure_digest,
            "verifier_snapshot_digest": self.verifier_snapshot_digest,
            "verifier_measurement_digest": self.verifier_measurement_digest,
            "verifier_result_digest": self.verifier_result_digest,
            "artifact_manifest_ref": _ref_obj(self.artifact_manifest_ref),
            "primary_disposition": self.primary_disposition,
            "reward_disposition": self.reward_disposition,
            "reward_components": reward,
            "evidence_policy_ref": self.evidence_policy_ref,
            "retention_policy_ref": self.retention_policy_ref,
            "retention_policy_record_ref": _json_value(
                self.retention_policy_record_ref
            ),
            "lineage_nodes": [_json_value(node) for node in nodes],
            "verifier_cleanup_lease_id": self.verifier_cleanup_lease_id,
            "authority_access_ledger_ref": _json_value(
                self.authority_access_ledger_ref
            ),
            "authority_canary_reads": list(self.authority_canary_reads),
            "authority_cross_episode_reads": list(self.authority_cross_episode_reads),
        }
        object.__setattr__(self, "lineage_root", canonical_digest(commitment))


@dataclass(frozen=True, slots=True)
class CompletedEpisodeEnvelopeV2(CanonicalRecord):
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    create_response_ref: ArtifactRef
    run_response_ref: ArtifactRef
    evidence_manifest_ref: ArtifactRef
    evidence_root: str
    primary_outcome: str
    completed_event_ref: ArtifactRef
    completed_event_head: str
    subject_digest: str | None = None
    cleanup_disposition: str = field(default="pending", init=False)
    schema_version: str = field(
        default="bb.rl.completed-episode-envelope.v2", init=False
    )

    def __post_init__(self) -> None:
        for x in (
            self.create_fingerprint,
            self.run_fingerprint,
            self.evidence_root,
            self.completed_event_head,
        ):
            _check_digest(x)
        if self.subject_digest is not None:
            _check_digest(self.subject_digest, "subject digest")


@dataclass(frozen=True, slots=True)
class ClosedEpisodeEnvelopeV2(CanonicalRecord):
    episode_id: str
    completed_envelope_ref: ArtifactRef
    cleanup_receipt_digest: str | None
    cleanup_receipt: Mapping[str, Any] | None
    reconciliation_event_ref: ArtifactRef
    reconciliation_event_head: str
    primary_outcome: str
    cleanup_required_resources: tuple[str, ...] = _PRIMARY_CLEANUP_RESOURCES
    verifier_cleanup_receipt_digest: str | None = None
    verifier_cleanup_receipt: Mapping[str, Any] | None = None
    verifier_cleanup_required_resources: tuple[str, ...] = ()
    export_authorization_refs: tuple[ArtifactRef, ...] = ()
    redaction_decision_refs: tuple[ArtifactRef, ...] = ()
    cleanup_disposition: str = field(default="released", init=False)
    schema_version: str = field(default="bb.rl.closed-episode-envelope.v2", init=False)

    def __post_init__(self) -> None:
        _check_digest(self.reconciliation_event_head)
        if self.cleanup_receipt is None:
            if (
                self.cleanup_receipt_digest is not None
                or self.cleanup_required_resources
            ):
                raise EvidenceValidationError(
                    "no-allocation close cannot claim primary cleanup"
                )
        else:
            if self.cleanup_receipt_digest is None:
                raise EvidenceValidationError(
                    "primary cleanup receipt and digest must be paired"
                )
            _check_digest(self.cleanup_receipt_digest)
            if tuple(self.cleanup_required_resources) != _PRIMARY_CLEANUP_RESOURCES:
                raise EvidenceValidationError(
                    "primary cleanup resource contract mismatch"
                )
            _validate_cleanup_projection(
                self.cleanup_receipt,
                required_resources=self.cleanup_required_resources,
            )
            if canonical_digest(self.cleanup_receipt) != self.cleanup_receipt_digest:
                raise EvidenceValidationError("cleanup receipt digest mismatch")
        if (self.verifier_cleanup_receipt is None) != (
            self.verifier_cleanup_receipt_digest is None
        ):
            raise EvidenceValidationError(
                "verifier cleanup receipt and digest must be paired"
            )
        if self.verifier_cleanup_receipt is None:
            if self.verifier_cleanup_required_resources:
                raise EvidenceValidationError(
                    "verifier cleanup resources require a verifier cleanup receipt"
                )
        else:
            if (
                tuple(self.verifier_cleanup_required_resources)
                != _VERIFIER_CLEANUP_RESOURCES
            ):
                raise EvidenceValidationError(
                    "verifier cleanup resource contract mismatch"
                )
            _check_digest(self.verifier_cleanup_receipt_digest or "")
            _validate_cleanup_projection(
                self.verifier_cleanup_receipt,
                required_resources=_VERIFIER_CLEANUP_RESOURCES,
            )
            if (
                canonical_digest(self.verifier_cleanup_receipt)
                != self.verifier_cleanup_receipt_digest
            ):
                raise EvidenceValidationError(
                    "verifier cleanup receipt digest mismatch"
                )


@dataclass(frozen=True, slots=True)
class EpisodeCompletedTombstoneV2(CanonicalRecord):
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    event_head: str
    response_ref: ArtifactRef
    envelope_ref: ArtifactRef
    locator_generation: int
    schema_version: str = field(default="bb.rl.completed-tombstone.v2", init=False)


@dataclass(frozen=True, slots=True)
class EpisodeClosedTombstoneV2(CanonicalRecord):
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    event_head: str
    response_ref: ArtifactRef
    completed_tombstone_ref: ArtifactRef
    envelope_ref: ArtifactRef
    locator_generation: int
    schema_version: str = field(default="bb.rl.closed-tombstone.v2", init=False)


@dataclass(frozen=True, slots=True)
class EpisodeLocatorRecordV2(CanonicalRecord):
    episode_id: str
    generation: int
    current_state: str
    latest_event_head: str
    latest_event_ref: ArtifactRef
    completed_tombstone_ref: ArtifactRef | None = None
    closed_tombstone_ref: ArtifactRef | None = None
    quarantine_ref: ArtifactRef | None = None
    runner_event_refs: tuple[ArtifactRef, ...] = ()
    runner_event_head: str | None = None
    runner_effective_plan_digest: str | None = None
    checksum: str = ""
    evidentiary: bool = field(default=False, init=False)
    schema_version: str = field(default="bb.rl.episode-locator.v2", init=False)

    def __post_init__(self) -> None:
        if _EPISODE_RE.fullmatch(self.episode_id) is None or self.generation < 1:
            raise EvidenceValidationError("invalid locator identity")
        _check_digest(self.latest_event_head)
        if self.runner_event_head is not None:
            _check_digest(self.runner_event_head, "runner event head")
        if self.runner_effective_plan_digest is not None:
            _check_digest(
                self.runner_effective_plan_digest, "runner effective plan digest"
            )
        if bool(self.runner_event_refs) != (self.runner_event_head is not None):
            raise EvidenceValidationError(
                "runner event references and head must be paired"
            )
        if bool(self.runner_event_refs) != (
            self.runner_effective_plan_digest is not None
        ):
            raise EvidenceValidationError("runner journal plan binding is incomplete")
        expected = canonical_digest(self.payload())
        if self.checksum and self.checksum != expected:
            raise EvidenceCorruptError("locator checksum mismatch")
        object.__setattr__(self, "checksum", expected)

    def payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "generation": self.generation,
            "current_state": self.current_state,
            "latest_event_head": self.latest_event_head,
            "latest_event_ref": _ref_obj(self.latest_event_ref),
            "completed_tombstone_ref": _json_value(self.completed_tombstone_ref),
            "closed_tombstone_ref": _json_value(self.closed_tombstone_ref),
            "quarantine_ref": _json_value(self.quarantine_ref),
            "evidentiary": False,
        }
        if self.runner_event_refs:
            payload["runner_event_refs"] = _json_value(self.runner_event_refs)
            payload["runner_event_head"] = self.runner_event_head
            payload["runner_effective_plan_digest"] = self.runner_effective_plan_digest
        return payload

    def to_canonical_obj(self) -> dict[str, Any]:
        return {**self.payload(), "checksum": self.checksum}


@dataclass(frozen=True, slots=True)
class ExportAuthorizationClaimsV2(CanonicalRecord):
    subject_digest: str
    scope: str
    evidence_policy_ref: str
    retention_policy_ref: str
    allowed_roles: tuple[str, ...]
    redaction_decision_digest: str
    schema_version: str = field(
        default="bb.rl.export-authorization-claims.v2", init=False
    )

    def __post_init__(self) -> None:
        _check_digest(self.subject_digest, "export subject digest")
        if (
            type(self.scope) is not str
            or not self.scope
            or self.scope != self.scope.strip()
        ):
            raise EvidenceValidationError("export scope is required")
        if (
            type(self.evidence_policy_ref) is not str
            or not self.evidence_policy_ref
            or self.evidence_policy_ref != self.evidence_policy_ref.strip()
            or type(self.retention_policy_ref) is not str
            or not self.retention_policy_ref
            or self.retention_policy_ref != self.retention_policy_ref.strip()
        ):
            raise EvidenceValidationError("export policy references are required")
        _check_digest(self.redaction_decision_digest, "redaction decision digest")
        if (
            type(self.allowed_roles) is not tuple
            or not self.allowed_roles
            or any(
                type(role) is not str or not role or role != role.strip()
                for role in self.allowed_roles
            )
            or self.allowed_roles != tuple(sorted(set(self.allowed_roles)))
        ):
            raise EvidenceValidationError(
                "allowed roles must be a non-empty sorted unique tuple of strings"
            )


@dataclass(frozen=True, slots=True)
class ExportAuthorizationV2(CanonicalRecord):
    subject: str
    scope: str
    evidence_policy_ref: str
    retention_policy_ref: str
    allowed_roles: tuple[str, ...]
    redaction_decision_digest: str
    not_before: str | None = None
    not_after: str | None = None
    schema_version: str = field(default="bb.rl.export-authorization.v2", init=False)

    def __post_init__(self) -> None:
        if not self.subject or not self.scope:
            raise EvidenceValidationError("export subject and scope are required")
        _check_digest(self.redaction_decision_digest)
        object.__setattr__(
            self, "allowed_roles", tuple(sorted(set(self.allowed_roles)))
        )


@dataclass(frozen=True, slots=True)
class ExportManifestV2(CanonicalRecord):
    episode_id: str
    closed_envelope_ref: ArtifactRef
    authorization_digest: str
    evidence_policy_ref: str
    retention_policy_ref: str
    allowed_roles: tuple[str, ...]
    redaction_decision_digest: str
    omitted: tuple[tuple[str, str], ...]
    exported_objects: tuple[EvidenceObjectV2, ...]
    schema_version: str = field(default="bb.rl.export-manifest.v2", init=False)


@dataclass(frozen=True, slots=True)
class LocatorScanEntryV2:
    locator_key: str
    episode_id_hint: str | None
    record: EpisodeLocatorRecordV2 | None
    failure: SafeFailureFactV2 | None

    def __post_init__(self) -> None:
        if not self.locator_key or _contains_unsafe_secret(self.locator_key):
            raise EvidenceValidationError("locator scan key is invalid")
        if (self.record is None) == (self.failure is None):
            raise EvidenceValidationError("locator scan entry must be valid or corrupt")
        if self.episode_id_hint is not None and (
            _EPISODE_RE.fullmatch(self.episode_id_hint) is None
            or self.record is not None
            and self.record.episode_id != self.episode_id_hint
        ):
            raise EvidenceValidationError("locator episode hint is invalid")


@dataclass(frozen=True, slots=True)
class ExportPinsV2:
    authorization_refs: tuple[ArtifactRef, ...]
    redaction_decision_refs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if len({ref.sha256 for ref in self.authorization_refs}) != len(
            self.authorization_refs
        ):
            raise EvidenceValidationError("export authorization pins must be unique")
        if len({ref.sha256 for ref in self.redaction_decision_refs}) != len(
            self.redaction_decision_refs
        ):
            raise EvidenceValidationError("redaction decision pins must be unique")


@runtime_checkable
class EpisodeLocatorStore(Protocol):
    def get(self, episode_id: str) -> EpisodeLocatorRecordV2 | None: ...
    def compare_and_swap(
        self,
        episode_id: str,
        expected_generation: int | None,
        record: EpisodeLocatorRecordV2,
    ) -> None: ...
    def enumerate(self) -> tuple[EpisodeLocatorRecordV2, ...]: ...
    def scan(self) -> tuple[LocatorScanEntryV2, ...]: ...
    def quarantine_corrupt(
        self, entry: LocatorScanEntryV2, failure: SafeFailureFactV2
    ) -> None: ...


class InMemoryEpisodeLocatorStore:
    def __init__(self) -> None:
        self._records: dict[str, EpisodeLocatorRecordV2] = {}
        self._blocked: set[str] = set()
        self._lock = threading.RLock()

    def get(self, episode_id: str) -> EpisodeLocatorRecordV2 | None:
        with self._lock:
            if episode_id in self._blocked:
                raise EvidenceCorruptError("episode locator is quarantined")
            return self._records.get(episode_id)

    def compare_and_swap(
        self,
        episode_id: str,
        expected_generation: int | None,
        record: EpisodeLocatorRecordV2,
    ) -> None:
        with self._lock:
            current = self._records.get(episode_id)
            actual = current.generation if current else None
            if (
                actual != expected_generation
                or record.episode_id != episode_id
                or record.generation != (1 if actual is None else actual + 1)
            ):
                raise LocatorConflictError("locator generation compare-and-swap failed")
            if current and current.closed_tombstone_ref is not None:
                raise LocatorConflictError("closed locator is absorbing")
            self._records[episode_id] = record

    def enumerate(self) -> tuple[EpisodeLocatorRecordV2, ...]:
        with self._lock:
            return tuple(self._records[k] for k in sorted(self._records))

    def scan(self) -> tuple[LocatorScanEntryV2, ...]:
        with self._lock:
            return tuple(
                LocatorScanEntryV2(key, key, record, None)
                for key, record in sorted(self._records.items())
            )

    def quarantine_corrupt(
        self,
        entry: LocatorScanEntryV2,
        failure: SafeFailureFactV2,
    ) -> None:
        if type(failure) is not SafeFailureFactV2:
            raise EvidenceValidationError("locator quarantine failure must be exact")
        with self._lock:
            if entry.episode_id_hint is not None:
                self._records.pop(entry.episode_id_hint, None)
                self._blocked.add(entry.episode_id_hint)


def _locator_operation(method: Any) -> Any:
    @functools.wraps(method)
    def guarded(
        self: "FilesystemEpisodeLocatorStore", *args: Any, **kwargs: Any
    ) -> Any:
        with self._lock:
            if self._closed:
                raise EvidenceCorruptError("episode locator store is closed")
            return method(self, *args, **kwargs)

    return guarded


class FilesystemEpisodeLocatorStore:
    """Mutable locator index pinned to the exact directory opened at construction."""

    def __init__(self, root: str | Path, *, root_fd: int | None = None) -> None:
        requested = Path(root)
        if root_fd is None:
            requested.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.root = requested.resolve(strict=True)
        else:
            self.root = requested
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        self._root_fd = (
            os.dup(root_fd) if root_fd is not None else os.open(self.root, flags)
        )
        self._root_stat = os.fstat(self._root_fd)
        self._quarantine_fd = -1
        try:
            try:
                os.mkdir(".quarantine", mode=0o700, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            except FileExistsError:
                pass
            self._quarantine_fd = os.open(
                ".quarantine",
                flags,
                dir_fd=self._root_fd,
            )
            if not stat.S_ISDIR(os.fstat(self._quarantine_fd).st_mode):
                raise EvidenceCorruptError("locator quarantine is not a directory")
        except BaseException:
            if self._quarantine_fd >= 0:
                os.close(self._quarantine_fd)
            os.close(self._root_fd)
            raise
        self._lock = threading.RLock()
        self._scan_names: dict[str, str] = {}
        self._closed = False

    def close(self) -> None:
        """Stop admission and close pinned descriptors after active operations."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for descriptor in (self._quarantine_fd, self._root_fd):
                with contextlib.suppress(OSError):
                    os.close(descriptor)

    def _name(self, episode_id: str) -> str:
        if _EPISODE_RE.fullmatch(episode_id) is None:
            raise EvidenceValidationError("invalid episode id")
        return f"{episode_id}.json"

    def _validate_root(self) -> None:
        current = os.stat(self.root, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (
            current.st_dev,
            current.st_ino,
        ) != (self._root_stat.st_dev, self._root_stat.st_ino):
            raise EvidenceCorruptError("locator root identity changed")

    def _read_name(self, name: str) -> bytes | None:
        self._validate_root()
        try:
            fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
                raise EvidenceCorruptError("locator is not a bounded regular file")
            chunks: list[bytes] = []
            remaining = st.st_size + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    @_locator_operation
    def get(self, episode_id: str) -> EpisodeLocatorRecordV2 | None:
        name = self._name(episode_id)
        try:
            marker_fd = os.open(
                f"{episode_id}.blocked",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._quarantine_fd,
            )
        except FileNotFoundError:
            marker_fd = None
        if marker_fd is not None:
            os.close(marker_fd)
            raise EvidenceCorruptError("episode locator is quarantined")
        data = self._read_name(name)
        return None if data is None else _locator_from_json(data)

    def _write_temp(self, name: str, payload: bytes) -> None:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=self._root_fd,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    @_locator_operation
    def compare_and_swap(
        self,
        episode_id: str,
        expected_generation: int | None,
        record: EpisodeLocatorRecordV2,
    ) -> None:
        with self._lock:
            self._validate_root()
            try:
                blocked_fd = os.open(
                    f"{episode_id}.blocked",
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._quarantine_fd,
                )
            except FileNotFoundError:
                blocked_fd = None
            if blocked_fd is not None:
                os.close(blocked_fd)
                raise EvidenceCorruptError("episode locator is quarantined")
            target = self._name(episode_id)
            lock_name = f".{episode_id}.lock"
            try:
                lock_fd = os.open(
                    lock_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=self._root_fd,
                )
            except FileExistsError:
                lock_fd = os.open(
                    lock_name,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._root_fd,
                )
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                os.close(lock_fd)
                raise EvidenceCorruptError("locator lock is not a regular file")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                old_payload = self._read_name(target)
                current = (
                    None if old_payload is None else _locator_from_json(old_payload)
                )
                actual = current.generation if current else None
                if (
                    actual != expected_generation
                    or record.episode_id != episode_id
                    or record.generation != (1 if actual is None else actual + 1)
                ):
                    raise LocatorConflictError(
                        "locator generation compare-and-swap failed"
                    )
                if current and current.closed_tombstone_ref is not None:
                    raise LocatorConflictError("closed locator is absorbing")
                temp = f".{episode_id}.{uuid.uuid4().hex}.tmp"
                self._write_temp(temp, record.canonical_bytes())
                replaced = False
                try:
                    os.replace(
                        temp, target, src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd
                    )
                    replaced = True
                    os.fsync(self._root_fd)
                except BaseException:
                    if replaced:
                        if old_payload is None:
                            os.unlink(target, dir_fd=self._root_fd)
                        else:
                            rollback = f".{episode_id}.{uuid.uuid4().hex}.rollback"
                            self._write_temp(rollback, old_payload)
                            os.replace(
                                rollback,
                                target,
                                src_dir_fd=self._root_fd,
                                dst_dir_fd=self._root_fd,
                            )
                        os.fsync(self._root_fd)
                    raise
                finally:
                    try:
                        os.unlink(temp, dir_fd=self._root_fd)
                    except FileNotFoundError:
                        pass
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    @_locator_operation
    def scan(self) -> tuple[LocatorScanEntryV2, ...]:
        self._validate_root()
        names = sorted(
            name
            for name in os.listdir(self._root_fd)
            if name.endswith(".json") and not name.startswith(".")
        )
        entries: list[LocatorScanEntryV2] = []
        self._scan_names = {}
        for name in names:
            hint = name[:-5] if _EPISODE_RE.fullmatch(name[:-5]) else None
            key = _digest(name.encode("utf-8"))
            self._scan_names[key] = name
            try:
                data = self._read_name(name)
                if data is None:
                    continue
                record = _locator_from_json(data)
                if hint is None or record.episode_id != hint:
                    raise EvidenceCorruptError("locator filename identity mismatch")
            except (
                EvidenceError,
                ValueError,
                TypeError,
                KeyError,
                json.JSONDecodeError,
            ):
                failure = SafeFailureFactV2(
                    "evidence",
                    "locator_corrupt",
                    "blocked",
                    "locator_scan",
                )
                entries.append(LocatorScanEntryV2(key, hint, None, failure))
            else:
                entries.append(LocatorScanEntryV2(key, hint, record, None))
        return tuple(entries)

    @_locator_operation
    def enumerate(self) -> tuple[EpisodeLocatorRecordV2, ...]:
        return tuple(entry.record for entry in self.scan() if entry.record is not None)

    @_locator_operation
    def quarantine_corrupt(
        self,
        entry: LocatorScanEntryV2,
        failure: SafeFailureFactV2,
    ) -> None:
        if type(failure) is not SafeFailureFactV2:
            raise EvidenceValidationError("locator quarantine failure must be exact")
        with self._lock:
            self._validate_root()
            source = self._scan_names.get(entry.locator_key)
            if source is None:
                raise LocatorConflictError("locator scan entry is stale or foreign")
            destination = f"{entry.locator_key[7:]}.locator"
            try:
                os.rename(
                    source,
                    destination,
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._quarantine_fd,
                )
            except FileNotFoundError:
                pass
            if entry.episode_id_hint is not None:
                marker = f".{entry.episode_id_hint}.{uuid.uuid4().hex}.tmp"
                marker_payload = canonical_json_bytes(
                    {
                        "schema_version": "bb.rl.locator-quarantine-marker.v2",
                        "episode_id": entry.episode_id_hint,
                        "failure": _json_value(failure),
                    }
                )
                marker_fd = os.open(
                    marker,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=self._quarantine_fd,
                )
                try:
                    os.write(marker_fd, marker_payload)
                    os.fsync(marker_fd)
                finally:
                    os.close(marker_fd)
                os.replace(
                    marker,
                    f"{entry.episode_id_hint}.blocked",
                    src_dir_fd=self._quarantine_fd,
                    dst_dir_fd=self._quarantine_fd,
                )
            self._scan_names.pop(entry.locator_key, None)
            os.fsync(self._quarantine_fd)
            os.fsync(self._root_fd)


@dataclass(frozen=True, slots=True)
class RecoveredEpisodeV2:
    locator: EpisodeLocatorRecordV2
    events: tuple[LifecycleEventV2, ...]
    completed_tombstone: EpisodeCompletedTombstoneV2 | None = None
    closed_tombstone: EpisodeClosedTombstoneV2 | None = None
    completed_envelope: CompletedEpisodeEnvelopeV2 | None = None
    closed_envelope: ClosedEpisodeEnvelopeV2 | None = None
    quarantined: bool = False
    runner_events: tuple[Any, ...] = ()
    primary_lease_id: str | None = None
    verifier_cleanup_receipt: SandboxCleanupReceipt | None = None
    verifier_lease_id: str | None = None
    evidence_manifest: ExecutionEvidenceManifestV2 | None = None

    @property
    def result_ref(self) -> ArtifactRef | None:
        return (
            self.completed_envelope.run_response_ref
            if self.completed_envelope is not None
            else None
        )

    @property
    def evidence_manifest_ref(self) -> ArtifactRef | None:
        return (
            self.completed_envelope.evidence_manifest_ref
            if self.completed_envelope is not None
            else None
        )

    @property
    def evidence_root(self) -> str | None:
        return (
            self.completed_envelope.evidence_root
            if self.completed_envelope is not None
            else None
        )

    @property
    def artifact_manifest_ref(self) -> ArtifactRef | None:
        return (
            self.evidence_manifest.artifact_manifest_ref
            if self.evidence_manifest is not None
            else None
        )

    @property
    def primary_measurement_digest(self) -> str | None:
        return (
            self.evidence_manifest.primary_measurement_digest
            if self.evidence_manifest is not None
            else None
        )

    @property
    def verifier_measurement_digest(self) -> str | None:
        return (
            self.evidence_manifest.verifier_measurement_digest
            if self.evidence_manifest is not None
            else None
        )

    @property
    def verifier_result_digest(self) -> str | None:
        return (
            self.evidence_manifest.verifier_result_digest
            if self.evidence_manifest is not None
            else None
        )


@dataclass(frozen=True, slots=True)
class FailedCompletedPublicationInputsV2:
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    create_response_bytes: bytes
    run_response_bytes: bytes
    lifecycle_head_ref: ArtifactRef
    lifecycle_head_digest: str
    primary_disposition: str
    primary_failure: SafeFailureFactV2
    session_close_failure: SafeFailureFactV2 | None
    verifier_cleanup_failure: SafeFailureFactV2 | None
    runner_event_refs: tuple[ArtifactRef, ...]
    resolved_plan: Any | None
    policy_binding_digest: str | None
    materialization_receipt: Any | None
    primary_measurement: Any | None
    verifier_snapshot: Any | None
    verifier_measurement_digest: str | None
    verifier_result: Mapping[str, Any] | None
    verifier_cleanup_receipt: SandboxCleanupReceipt | None
    verifier_lease_id: str | None
    subject_digest: str | None = None
    authority_access_events: tuple[AuthorityAccessEventV2, ...] = ()


@dataclass(frozen=True, slots=True)
class RunnerEventPublicationV2:
    event_ref: ArtifactRef
    sequence: int
    event_digest: str


@dataclass(frozen=True, slots=True)
class CompletedPublicationInputsV2:
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    create_response_bytes: bytes
    run_response_bytes: bytes
    resolved_plan: Any
    policy_binding_digest: str
    runner_result: Any
    materialization_receipt: Any
    primary_measurement: Any
    verifier_snapshot: Any
    verifier_measurement_digest: str | None
    verifier_result: Mapping[str, Any]
    evidence_objects: tuple[EvidenceObjectV2, ...]
    evidence_policy: Any
    retention_policy: Any
    lifecycle_head_ref: ArtifactRef
    lifecycle_head_digest: str
    primary_disposition: str
    reward_disposition: str
    reward_components: Mapping[str, Any]
    verifier_lease_id: str
    verifier_cleanup_receipt: SandboxCleanupReceipt | None = None
    subject_digest: str | None = None
    authority_access_events: tuple[AuthorityAccessEventV2, ...] = ()


@dataclass(frozen=True, slots=True)
class CompletedPublicationV2:
    envelope: CompletedEpisodeEnvelopeV2
    envelope_ref: ArtifactRef
    tombstone: EpisodeCompletedTombstoneV2
    tombstone_ref: ArtifactRef
    locator: EpisodeLocatorRecordV2
    evidence_manifest: ExecutionEvidenceManifestV2

    def __post_init__(self) -> None:
        if (
            self.envelope.run_response_ref != self.tombstone.response_ref
            or self.envelope.evidence_manifest_ref.sha256
            != self.evidence_manifest.digest
            or self.envelope.evidence_root != self.evidence_manifest.lineage_root
        ):
            raise EvidenceValidationError(
                "completed publication evidence projection mismatch"
            )

    @property
    def result_ref(self) -> ArtifactRef:
        return self.envelope.run_response_ref

    @property
    def evidence_manifest_ref(self) -> ArtifactRef:
        return self.envelope.evidence_manifest_ref

    @property
    def evidence_root(self) -> str:
        return self.envelope.evidence_root

    @property
    def artifact_manifest_ref(self) -> ArtifactRef:
        return self.evidence_manifest.artifact_manifest_ref

    @property
    def primary_measurement_digest(self) -> str:
        return self.evidence_manifest.primary_measurement_digest

    @property
    def verifier_measurement_digest(self) -> str | None:
        return self.evidence_manifest.verifier_measurement_digest

    @property
    def verifier_result_digest(self) -> str | None:
        return self.evidence_manifest.verifier_result_digest


@dataclass(frozen=True, slots=True)
class ClosedPublicationInputsV2:
    episode_id: str
    completed: CompletedPublicationV2
    cleanup_receipt: SandboxCleanupReceipt | None
    closed_event: LifecycleEventV2
    final_primary_outcome: str
    cleanup_lease_id: str | None
    cleanup_required_resources: tuple[str, ...]
    verifier_cleanup_receipt: SandboxCleanupReceipt | None = None
    verifier_cleanup_lease_id: str | None = None
    verifier_cleanup_required_resources: tuple[str, ...] = ()
    export_authorization_refs: tuple[ArtifactRef, ...] = ()
    redaction_decision_refs: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ClosedPublicationV2:
    envelope: ClosedEpisodeEnvelopeV2
    envelope_ref: ArtifactRef
    tombstone: EpisodeClosedTombstoneV2
    tombstone_ref: ArtifactRef
    locator: EpisodeLocatorRecordV2


@dataclass(frozen=True, slots=True)
class QuarantinePublicationInputsV2:
    episode_id: str
    event: LifecycleEventV2
    failure: SafeFailureFactV2


@dataclass(frozen=True, slots=True)
class QuarantinePublicationV2:
    quarantine_ref: ArtifactRef
    locator: EpisodeLocatorRecordV2


class EpisodeEvidenceRepository:
    def __init__(
        self,
        cas: CASReader,
        locator_store: EpisodeLocatorStore,
        *,
        max_object_bytes: int = MAX_OBJECT_BYTES,
        max_traversal_objects: int = MAX_TRAVERSAL_OBJECTS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not hasattr(cas, "put_bytes"):
            raise TypeError("evidence CAS must support immutable put_bytes")
        self._cas = cas
        self._locators = locator_store
        self._max_object_bytes = max_object_bytes
        self._max_traversal_objects = max_traversal_objects
        self._clock = clock or (lambda: datetime.now(UTC))
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, episode_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(episode_id, threading.RLock())

    def scan_locators(self) -> tuple[LocatorScanEntryV2, ...]:
        return self._locators.scan()

    def quarantine_corrupt_locator(
        self,
        entry: LocatorScanEntryV2,
        failure: SafeFailureFactV2,
    ) -> None:
        self._locators.quarantine_corrupt(entry, failure)

    def _put(self, kind: str, episode_id: str, value: Any, suffix: str) -> ArtifactRef:
        payload = (
            value
            if isinstance(value, bytes)
            else canonical_json_bytes(_json_value(value))
        )
        if len(payload) > self._max_object_bytes:
            raise EvidenceValidationError("evidence object exceeds repository bound")
        artifact_id = f"v2/{episode_id}/{suffix}/{_digest(payload)[7:]}"
        return self._cas.put_bytes(
            payload,
            artifact_id=artifact_id,
            media_type=_SCHEMA_MEDIA
            if not isinstance(value, bytes)
            else "application/json",
            metadata={"schema": kind, "episode_id": episode_id},
        )  # type: ignore[attr-defined]

    def publish_evidence_objects(
        self,
        episode_id: str,
        authority_plan: EvidenceAuthorityPlanV2,
        inputs: tuple[EvidenceObjectInputV2, ...],
    ) -> tuple[EvidenceObjectV2, ...]:
        if _EPISODE_RE.fullmatch(episode_id) is None:
            raise EvidenceValidationError("invalid evidence episode identity")
        if type(authority_plan) is not EvidenceAuthorityPlanV2:
            raise EvidenceValidationError("evidence authority plan must be exact")
        if type(inputs) is not tuple or any(
            type(item) is not EvidenceObjectInputV2 for item in inputs
        ):
            raise EvidenceValidationError(
                "evidence object inputs must be an exact tuple"
            )
        ordered = tuple(sorted(inputs, key=lambda item: item.role))
        roles = tuple(item.role for item in ordered)
        if len(roles) != len(set(roles)):
            raise EvidenceValidationError("evidence object roles must be unique")
        if not set(authority_plan.required_roles) <= set(roles):
            raise EvidenceValidationError("required evidence role is absent")
        if not set(roles) <= set(authority_plan.allowed_roles):
            raise EvidenceValidationError("unknown evidence role")
        bindings = {binding.role: binding for binding in authority_plan.bindings}
        total = 0
        for item in ordered:
            binding = bindings.get(item.role)
            if binding is None or (
                item.source is not binding.source
                or item.producer_id != binding.producer_id
                or item.producer_implementation_digest
                != binding.producer_implementation_digest
            ):
                raise EvidenceValidationError(
                    "evidence source or producer is unauthorized"
                )
            size = len(item.payload)
            if size > authority_plan.max_each_bytes:
                raise EvidenceValidationError(
                    "evidence object exceeds per-role byte limit"
                )
            total += size
            if total > authority_plan.max_total_bytes:
                raise EvidenceValidationError(
                    "evidence objects exceed total byte limit"
                )
            if size > self._max_object_bytes:
                raise EvidenceValidationError(
                    "evidence object exceeds repository bound"
                )
        objects: list[EvidenceObjectV2] = []
        for item in ordered:
            digest = _digest(item.payload)
            artifact_id = f"v2/{episode_id}/evidence-object-{item.role}/{digest[7:]}"
            ref = self._cas.put_bytes(  # type: ignore[attr-defined]
                item.payload,
                artifact_id=artifact_id,
                media_type=item.media_type,
                metadata={
                    "schema": "bb.rl.evidence-object-payload.v2",
                    "episode_id": episode_id,
                    "role": item.role,
                },
            )
            if (
                ref.sha256 != digest
                or ref.size_bytes != len(item.payload)
                or ref.media_type != item.media_type
            ):
                raise EvidenceCorruptError(
                    "CAS returned a mismatched evidence reference"
                )
            producer = canonical_digest(
                {
                    "schema_version": "bb.rl.evidence-producer-binding.v1",
                    "producer_id": item.producer_id,
                    "implementation_digest": item.producer_implementation_digest,
                }
            )
            objects.append(
                EvidenceObjectV2(
                    role=item.role,
                    producer=producer,
                    artifact_ref=ref,
                    authorization_policy_ref=authority_plan.evidence_policy_ref,
                    retention_policy_ref=authority_plan.retention_policy_ref,
                    parent_digests=item.parent_digests,
                )
            )
        return tuple(objects)

    def append_transition(self, event: LifecycleEventV2) -> ArtifactRef:
        with self._lock_for(event.episode_id):
            current = self._locators.get(event.episode_id)
            previous: LifecycleEventV2 | None = None
            if current is not None:
                if current.closed_tombstone_ref is not None:
                    raise LocatorConflictError("closed locator is absorbing")
                previous = self._load_event(current.latest_event_ref)
                if (
                    current.latest_event_head != previous.digest
                    or current.current_state != previous.to_state
                ):
                    raise EvidenceCorruptError("locator lifecycle head is incoherent")
            _validate_transition(previous, event)
            generation = 1 if current is None else current.generation + 1
            ref = self._put(
                event.schema_version, event.episode_id, event, f"event-{event.sequence}"
            )
            record = EpisodeLocatorRecordV2(
                event.episode_id,
                generation,
                event.to_state,
                event.digest,
                ref,
                current.completed_tombstone_ref if current else None,
                current.closed_tombstone_ref if current else None,
                current.quarantine_ref if current else None,
                runner_event_refs=current.runner_event_refs if current else (),
                runner_event_head=current.runner_event_head if current else None,
                runner_effective_plan_digest=current.runner_effective_plan_digest
                if current
                else None,
            )
            self._locators.compare_and_swap(
                event.episode_id, current.generation if current else None, record
            )
            return ref

    def append_runner_event(
        self,
        episode_id: str,
        effective_plan_digest: str,
        event: Any,
    ) -> RunnerEventPublicationV2:
        if _EPISODE_RE.fullmatch(episode_id) is None:
            raise EvidenceValidationError("invalid runner event episode identity")
        _check_digest(effective_plan_digest, "runner effective plan digest")
        event_value = _json_value(event)
        if not isinstance(event_value, Mapping):
            raise EvidenceValidationError("runner event must be a canonical object")
        if event_value.get("episode_id") != episode_id:
            raise EvidenceValidationError("runner event episode identity mismatch")
        if event_value.get("effective_plan_digest") != effective_plan_digest:
            raise EvidenceValidationError(
                "runner event effective-plan identity mismatch"
            )
        sequence = event_value.get("sequence")
        if type(sequence) is not int or sequence < 0:
            raise EvidenceValidationError("runner event sequence is invalid")
        event_digest = canonical_digest(event_value)
        with self._lock_for(episode_id):
            current = self._locators.get(episode_id)
            if current is None:
                raise LocatorConflictError("runner event requires an existing episode")
            if (
                current.completed_tombstone_ref
                or current.closed_tombstone_ref
                or current.quarantine_ref
            ):
                raise LocatorConflictError(
                    "runner journal is immutable after terminal publication"
                )
            if current.runner_effective_plan_digest not in (
                None,
                effective_plan_digest,
            ):
                raise EvidenceValidationError(
                    "runner journal effective-plan identity mismatch"
                )
            if sequence != len(current.runner_event_refs):
                raise EvidenceValidationError("runner event sequence is not contiguous")
            if current.runner_event_refs:
                previous = self._load_json(current.runner_event_refs[-1])
                if canonical_digest(previous) != current.runner_event_head:
                    raise EvidenceCorruptError("runner journal prior head mismatch")
                if int(previous.get("sequence", -1)) != sequence - 1:
                    raise EvidenceCorruptError("runner journal prior sequence mismatch")
            ref = self._put(
                "bb.rl.runner-event.v2",
                episode_id,
                event_value,
                f"runner-event-{sequence}",
            )
            if ref.sha256 != event_digest:
                raise EvidenceCorruptError("runner event CAS reference mismatch")
            locator = EpisodeLocatorRecordV2(
                episode_id,
                current.generation + 1,
                current.current_state,
                current.latest_event_head,
                current.latest_event_ref,
                current.completed_tombstone_ref,
                current.closed_tombstone_ref,
                current.quarantine_ref,
                runner_event_refs=(*current.runner_event_refs, ref),
                runner_event_head=event_digest,
                runner_effective_plan_digest=effective_plan_digest,
            )
            self._locators.compare_and_swap(episode_id, current.generation, locator)
            return RunnerEventPublicationV2(ref, sequence, event_digest)

    def recover_runner_events(self, episode_id: str) -> tuple[Any, ...]:
        locator = self._locators.get(episode_id)
        if locator is None:
            return ()
        if locator.episode_id != episode_id:
            raise EvidenceCorruptError("runner journal locator episode mismatch")
        events: list[Mapping[str, Any]] = []
        if len(locator.runner_event_refs) > self._max_traversal_objects:
            raise EvidenceCorruptError("runner journal traversal bound exceeded")
        for sequence, ref in enumerate(locator.runner_event_refs):
            value = self._load_json(ref)
            if canonical_digest(value) != ref.sha256:
                raise EvidenceCorruptError("runner event reference does not bind event")
            if (
                value.get("episode_id") != episode_id
                or value.get("effective_plan_digest")
                != locator.runner_effective_plan_digest
                or value.get("sequence") != sequence
            ):
                raise EvidenceCorruptError(
                    "runner journal identity or sequence mismatch"
                )
            events.append(value)
        expected_head = canonical_digest(events[-1]) if events else None
        if expected_head != locator.runner_event_head:
            raise EvidenceCorruptError("runner journal head mismatch")
        return tuple(events)

    def _publish_authority_access_ledger(
        self,
        episode_id: str,
        events: tuple[AuthorityAccessEventV2, ...],
    ) -> tuple[ArtifactRef | None, tuple[str, ...], tuple[str, ...]]:
        exact_events = tuple(events)
        if not exact_events:
            return None, (), ()
        if any(type(event) is not AuthorityAccessEventV2 for event in exact_events):
            raise EvidenceValidationError(
                "authority access events must be exact durable event values"
            )
        ledger = AuthorityAccessLedgerV2(episode_id, exact_events)
        if ledger.cross_episode_reads:
            raise EvidenceValidationError(
                "cross-episode authority access prevents evidence publication"
            )
        ledger_ref = self._put(
            ledger.schema_version,
            episode_id,
            ledger,
            "authority-access-ledger",
        )
        return ledger_ref, ledger.canary_reads, ledger.cross_episode_reads

    def publish_failed_completed(
        self,
        inputs: FailedCompletedPublicationInputsV2,
    ) -> CompletedPublicationV2:
        if inputs.primary_disposition not in {"failed", "cancelled", "interrupted"}:
            raise EvidenceValidationError(
                "failed completion requires a non-success primary disposition"
            )
        if type(inputs.primary_failure) is not SafeFailureFactV2:
            raise EvidenceValidationError(
                "failed completion requires an exact safe primary failure"
            )
        if (
            inputs.session_close_failure is not None
            and type(inputs.session_close_failure) is not SafeFailureFactV2
        ):
            raise EvidenceValidationError("session close failure must be exact")
        if (
            inputs.verifier_cleanup_failure is not None
            and type(inputs.verifier_cleanup_failure) is not SafeFailureFactV2
        ):
            raise EvidenceValidationError("verifier cleanup failure must be exact")
        _check_digest(inputs.create_fingerprint, "create fingerprint")
        run_fingerprint = inputs.run_fingerprint or canonical_digest(
            {"episode_id": inputs.episode_id, "run": "not-started"}
        )
        _check_digest(run_fingerprint, "run fingerprint")
        _check_digest(inputs.lifecycle_head_digest, "lifecycle head digest")
        decoded_responses: list[Any] = []
        for payload in (inputs.create_response_bytes, inputs.run_response_bytes):
            try:
                decoded = json.loads(payload)
            except (TypeError, json.JSONDecodeError) as exc:
                raise EvidenceValidationError(
                    "publication response must be canonical JSON"
                ) from exc
            if canonical_json_bytes(decoded) != payload:
                raise EvidenceValidationError(
                    "publication response must be canonical JSON"
                )
            decoded_responses.append(decoded)
        run_response = decoded_responses[1]
        if not isinstance(run_response, Mapping):
            raise EvidenceValidationError("run response must be a canonical object")
        response_run_fingerprint = run_response.get("run_fingerprint")
        if response_run_fingerprint not in (None, "", run_fingerprint):
            raise EvidenceValidationError(
                "run response fingerprint does not match publication identity"
            )
        normalized_run_response_bytes = canonical_json_bytes(
            {**run_response, "run_fingerprint": run_fingerprint}
        )
        verifier_facts_exist = any(
            value is not None
            for value in (
                inputs.verifier_snapshot,
                inputs.verifier_measurement_digest,
                inputs.verifier_result,
            )
        )
        presented_verifier_cleanup: Mapping[str, Any] | None = None
        verifier_cleanup_released = False
        if inputs.verifier_cleanup_receipt is not None:
            if type(inputs.verifier_cleanup_receipt) is not SandboxCleanupReceipt:
                raise EvidenceValidationError("verifier cleanup receipt must be exact")
            presented_verifier_cleanup = _json_value(inputs.verifier_cleanup_receipt)
            state = str(presented_verifier_cleanup.get("state", "")).lower()
            verifier_cleanup_released = state in {
                CleanupState.RELEASED.value,
                CleanupState.ALREADY_RELEASED.value,
            }
            if verifier_cleanup_released:
                if not inputs.verifier_lease_id:
                    raise EvidenceValidationError(
                        "verifier cleanup requires an authoritative lease identity"
                    )
                _validate_cleanup_projection(
                    presented_verifier_cleanup,
                    expected_lease_id=inputs.verifier_lease_id,
                    required_resources=_VERIFIER_CLEANUP_RESOURCES,
                )
        verifier_evidence_admitted = (
            verifier_facts_exist
            and inputs.verifier_cleanup_failure is None
            and verifier_cleanup_released
        )
        if (
            verifier_facts_exist
            and inputs.verifier_cleanup_failure is None
            and presented_verifier_cleanup is None
        ):
            raise EvidenceValidationError(
                "verifier facts require a durable cleanup receipt or cleanup failure"
            )
        verifier_cleanup_projection = (
            presented_verifier_cleanup if verifier_evidence_admitted else None
        )
        with self._lock_for(inputs.episode_id):
            current = self._locators.get(inputs.episode_id)
            if (
                current is None
                or current.latest_event_head != inputs.lifecycle_head_digest
                or current.latest_event_ref.sha256 != inputs.lifecycle_head_ref.sha256
            ):
                raise LocatorConflictError("publication event is not current")
            if current.completed_tombstone_ref is not None:
                return self._return_existing_completed(
                    current,
                    create_fingerprint=inputs.create_fingerprint,
                    run_fingerprint=run_fingerprint,
                    lifecycle_head_ref=inputs.lifecycle_head_ref,
                    lifecycle_head_digest=inputs.lifecycle_head_digest,
                    create_response_bytes=inputs.create_response_bytes,
                    run_response_bytes=normalized_run_response_bytes,
                    primary_outcome=inputs.primary_disposition,
                    subject_digest=inputs.subject_digest,
                )
            if current.closed_tombstone_ref or current.quarantine_ref:
                raise LocatorConflictError("terminal locator cannot be downgraded")
            lifecycle_events = self._recover_events(current)
            anchored = tuple(
                event
                for event in lifecycle_events
                if event.digest == inputs.lifecycle_head_digest
            )
            if (
                len(anchored) != 1
                or inputs.lifecycle_head_ref.sha256 != anchored[0].digest
                or anchored[0].episode_id != inputs.episode_id
            ):
                raise EvidenceValidationError(
                    "completed head is not a verified lifecycle ancestor"
                )
            if anchored[0].to_state != "closing":
                raise EvidenceValidationError(
                    "failed completion requires a closing lifecycle head"
                )
            if tuple(inputs.runner_event_refs) != current.runner_event_refs:
                raise EvidenceValidationError(
                    "runner event references do not match durable journal"
                )
            runner_events = self.recover_runner_events(inputs.episode_id)
            resolved_obj = (
                _json_value(inputs.resolved_plan)
                if inputs.resolved_plan is not None
                else {"status": "unresolved", "primary_failure": inputs.primary_failure}
            )
            resolved_digest = canonical_digest(resolved_obj)
            effective_obj = (
                resolved_obj.get(
                    "effective_plan", resolved_obj.get("plan", resolved_obj)
                )
                if isinstance(resolved_obj, Mapping)
                else resolved_obj
            )
            effective_digest = (
                current.runner_effective_plan_digest
                or str(
                    (
                        resolved_obj.get("effective_plan_digest")
                        if isinstance(resolved_obj, Mapping)
                        else ""
                    )
                    or ""
                )
                or canonical_digest({"kind": "effective-plan", "value": effective_obj})
            )
            selection_digest = (
                _selection_commit_digest(
                    resolved_obj,
                    resolved_plan_digest=resolved_digest,
                )
                if isinstance(resolved_obj, Mapping)
                else canonical_digest(
                    {"kind": "selection", "resolved_plan_digest": resolved_digest}
                )
            )
            policy_binding_digest = inputs.policy_binding_digest or canonical_digest(
                {"status": "unbound", "effective_plan_digest": effective_digest}
            )
            _check_digest(policy_binding_digest, "policy binding digest")
            primary_failure_digest = canonical_digest(
                {
                    "primary_failure": inputs.primary_failure,
                    "session_close_failure": inputs.session_close_failure,
                    "verifier_cleanup_failure": inputs.verifier_cleanup_failure,
                    "verifier_cleanup_receipt": (
                        presented_verifier_cleanup
                        if not verifier_evidence_admitted
                        else None
                    ),
                    "unadmitted_verifier_snapshot": (
                        inputs.verifier_snapshot
                        if not verifier_evidence_admitted
                        else None
                    ),
                    "unadmitted_verifier_measurement_digest": (
                        inputs.verifier_measurement_digest
                        if not verifier_evidence_admitted
                        else None
                    ),
                    "unadmitted_verifier_result": (
                        inputs.verifier_result
                        if not verifier_evidence_admitted
                        else None
                    ),
                }
            )
            materialization_digest = canonical_digest(
                inputs.materialization_receipt
                if inputs.materialization_receipt is not None
                else {"status": "not-materialized", "failure": inputs.primary_failure}
            )
            primary_measurement_digest = (
                canonical_digest(inputs.primary_measurement)
                if inputs.primary_measurement is not None
                else None
            )
            ledger = RunnerEventLedgerV2(
                inputs.episode_id,
                effective_digest,
                runner_events,
                primary_failure_digest,
            )
            ledger_ref = self._put(
                ledger.schema_version, inputs.episode_id, ledger, "runner-ledger"
            )
            evidence_policy_ref = canonical_digest(
                {"eligibility": "ineligible", "reason": "primary-failure"}
            )
            retention_policy_ref = canonical_digest({"retention": "failure-evidence"})
            artifact_manifest = ArtifactManifestV2((), (), 0, 0, ())
            artifact_ref = self._put(
                artifact_manifest.schema_version,
                inputs.episode_id,
                artifact_manifest,
                "artifact-manifest",
            )
            verifier_cleanup_ref = (
                self._put(
                    "bb.rl.verifier-cleanup-receipt.v2",
                    inputs.episode_id,
                    verifier_cleanup_projection,
                    "verifier-cleanup",
                )
                if verifier_cleanup_projection is not None
                else None
            )
            snapshot_digest = (
                canonical_digest(inputs.verifier_snapshot)
                if verifier_evidence_admitted and inputs.verifier_snapshot is not None
                else None
            )
            verifier_measurement_digest = (
                inputs.verifier_measurement_digest
                if verifier_evidence_admitted
                else None
            )
            verifier_result_digest = (
                canonical_digest(inputs.verifier_result)
                if verifier_evidence_admitted and inputs.verifier_result is not None
                else None
            )
            (
                authority_access_ledger_ref,
                authority_canary_reads,
                authority_cross_episode_reads,
            ) = self._publish_authority_access_ledger(
                inputs.episode_id,
                inputs.authority_access_events,
            )
            nodes = [
                LineageNodeV2(resolved_digest, "resolved_plan", "breadboard", ()),
                LineageNodeV2(
                    selection_digest, "selection", "breadboard", (resolved_digest,)
                ),
                LineageNodeV2(
                    effective_digest,
                    "effective_plan",
                    "breadboard",
                    (selection_digest,),
                ),
                LineageNodeV2(
                    policy_binding_digest,
                    "policy_binding",
                    "breadboard",
                    (effective_digest,),
                ),
                LineageNodeV2(
                    materialization_digest,
                    "materialization",
                    "breadboard",
                    (policy_binding_digest,),
                ),
            ]
            failure_parent = materialization_digest
            if primary_measurement_digest is not None:
                nodes.append(
                    LineageNodeV2(
                        primary_measurement_digest,
                        "primary_measurement",
                        "breadboard",
                        (materialization_digest,),
                    )
                )
                failure_parent = primary_measurement_digest
            nodes.extend(
                (
                    LineageNodeV2(
                        primary_failure_digest,
                        "primary_failure",
                        "breadboard",
                        (failure_parent,),
                    ),
                    LineageNodeV2(
                        ledger_ref.sha256,
                        "runner_ledger",
                        "breadboard",
                        (primary_failure_digest,),
                    ),
                    LineageNodeV2(
                        artifact_ref.sha256,
                        "artifact_manifest",
                        "breadboard",
                        (ledger_ref.sha256,),
                    ),
                )
            )
            last = artifact_ref.sha256
            for digest, kind in (
                (snapshot_digest, "verifier_snapshot"),
                (verifier_measurement_digest, "verifier_measurement"),
                (verifier_result_digest, "verifier_result"),
            ):
                if digest is not None:
                    _check_digest(digest)
                    nodes.append(LineageNodeV2(digest, kind, "breadboard", (last,)))
                    last = digest
            if verifier_cleanup_ref is not None:
                nodes.append(
                    LineageNodeV2(
                        verifier_cleanup_ref.sha256,
                        "verifier_cleanup",
                        "breadboard",
                        (last,),
                    )
                )
                last = verifier_cleanup_ref.sha256
            if authority_access_ledger_ref is not None:
                nodes.append(
                    LineageNodeV2(
                        authority_access_ledger_ref.sha256,
                        "authority_access_ledger",
                        "breadboard",
                        (last,),
                    )
                )
                last = authority_access_ledger_ref.sha256
            evidence_manifest = ExecutionEvidenceManifestV2(
                inputs.episode_id,
                resolved_digest,
                selection_digest,
                effective_digest,
                policy_binding_digest,
                ledger_ref,
                materialization_digest,
                primary_measurement_digest,
                snapshot_digest,
                verifier_measurement_digest,
                verifier_result_digest,
                artifact_ref,
                inputs.primary_disposition,
                "ineligible",
                {},
                evidence_policy_ref,
                retention_policy_ref,
                tuple(nodes),
                last,
                verifier_cleanup_ref,
                inputs.verifier_lease_id if verifier_cleanup_ref is not None else None,
                primary_failure_digest=primary_failure_digest,
                authority_access_ledger_ref=authority_access_ledger_ref,
                authority_canary_reads=authority_canary_reads,
                authority_cross_episode_reads=authority_cross_episode_reads,
            )
            evidence_ref = self._put(
                evidence_manifest.schema_version,
                inputs.episode_id,
                evidence_manifest,
                "evidence-manifest",
            )
            create_ref = self._put(
                "bb.rl.create-response.v2",
                inputs.episode_id,
                inputs.create_response_bytes,
                "create-response",
            )
            run_ref = self._put(
                "bb.rl.run-response.v2",
                inputs.episode_id,
                normalized_run_response_bytes,
                "run-response",
            )
            envelope = CompletedEpisodeEnvelopeV2(
                inputs.episode_id,
                inputs.create_fingerprint,
                run_fingerprint,
                create_ref,
                run_ref,
                evidence_ref,
                evidence_manifest.lineage_root,
                inputs.primary_disposition,
                inputs.lifecycle_head_ref,
                inputs.lifecycle_head_digest,
                inputs.subject_digest,
            )
            envelope_ref = self._put(
                envelope.schema_version,
                inputs.episode_id,
                envelope,
                "completed-envelope",
            )
            tombstone = EpisodeCompletedTombstoneV2(
                inputs.episode_id,
                inputs.create_fingerprint,
                run_fingerprint,
                inputs.lifecycle_head_digest,
                run_ref,
                envelope_ref,
                current.generation + 1,
            )
            tombstone_ref = self._put(
                tombstone.schema_version,
                inputs.episode_id,
                tombstone,
                "completed-tombstone",
            )
            locator = EpisodeLocatorRecordV2(
                inputs.episode_id,
                current.generation + 1,
                "closing",
                inputs.lifecycle_head_digest,
                inputs.lifecycle_head_ref,
                tombstone_ref,
                runner_event_refs=current.runner_event_refs,
                runner_event_head=current.runner_event_head,
                runner_effective_plan_digest=current.runner_effective_plan_digest,
            )
            self._locators.compare_and_swap(
                inputs.episode_id, current.generation, locator
            )
            return CompletedPublicationV2(
                envelope,
                envelope_ref,
                tombstone,
                tombstone_ref,
                locator,
                evidence_manifest,
            )

    def publish_completed(
        self, inputs: CompletedPublicationInputsV2
    ) -> CompletedPublicationV2:
        with self._lock_for(inputs.episode_id):
            current = self._locators.get(inputs.episode_id)
            if (
                current is None
                or current.latest_event_head != inputs.lifecycle_head_digest
            ):
                raise LocatorConflictError("publication event is not current")
            if current.latest_event_ref.sha256 != inputs.lifecycle_head_ref.sha256:
                raise LocatorConflictError("publication event reference is not current")
            if current.completed_tombstone_ref is not None:
                return self._return_existing_completed(
                    current,
                    create_fingerprint=inputs.create_fingerprint,
                    run_fingerprint=inputs.run_fingerprint,
                    lifecycle_head_ref=inputs.lifecycle_head_ref,
                    lifecycle_head_digest=inputs.lifecycle_head_digest,
                    create_response_bytes=inputs.create_response_bytes,
                    run_response_bytes=inputs.run_response_bytes,
                    primary_outcome=inputs.primary_disposition,
                    subject_digest=inputs.subject_digest,
                )
            if current.closed_tombstone_ref or current.quarantine_ref:
                raise LocatorConflictError("terminal locator cannot be downgraded")
            if inputs.verifier_cleanup_receipt is None:
                raise EvidenceValidationError(
                    "verifier evidence requires a durable cleanup receipt"
                )
            if type(inputs.verifier_cleanup_receipt) is not SandboxCleanupReceipt:
                raise EvidenceValidationError("verifier cleanup receipt must be exact")
            if not inputs.verifier_lease_id:
                raise EvidenceValidationError(
                    "verifier cleanup requires an authoritative lease identity"
                )
            verifier_cleanup_projection = _json_value(inputs.verifier_cleanup_receipt)
            _validate_cleanup_projection(
                verifier_cleanup_projection,
                expected_lease_id=inputs.verifier_lease_id,
                required_resources=_VERIFIER_CLEANUP_RESOURCES,
            )
            resolved_digest = canonical_digest(inputs.resolved_plan)
            plan_obj = _json_value(inputs.resolved_plan)
            effective_obj = plan_obj.get(
                "effective_plan", plan_obj.get("plan", plan_obj)
            )
            effective_digest = str(
                plan_obj.get("effective_plan_digest")
                or effective_obj.get("effective_plan_digest")
                or canonical_digest(effective_obj)
            )
            _check_digest(effective_digest, "effective plan digest")
            selection_digest = _selection_commit_digest(
                plan_obj,
                resolved_plan_digest=resolved_digest,
            )
            _check_digest(selection_digest, "selection digest")
            runner_result_digest = canonical_digest(inputs.runner_result)
            runner_events = self.recover_runner_events(inputs.episode_id)
            presented_runner_events = tuple(
                _json_value(event)
                for event in getattr(inputs.runner_result, "events", ())
            )
            if presented_runner_events != runner_events:
                raise EvidenceValidationError(
                    "runner result events do not match durable journal"
                )
            if type(inputs.retention_policy) is not RetentionPolicyRegistryRecord:
                raise EvidenceValidationError(
                    "retention policy registry record must be exact"
                )
            ledger = RunnerEventLedgerV2(
                inputs.episode_id,
                effective_digest,
                runner_events,
                runner_result_digest,
            )
            ledger_ref = self._put(
                ledger.schema_version, inputs.episode_id, ledger, "runner-ledger"
            )
            artifact_policy = effective_obj.get("artifacts", {})
            allowed_roles = tuple(
                artifact_policy.get("allowed_roles")
                or artifact_policy.get("roles")
                or (item.role for item in inputs.evidence_objects)
            )
            if "max_each_bytes" in artifact_policy:
                max_each = int(artifact_policy["max_each_bytes"])
            elif "max_artifact_bytes" in artifact_policy:
                max_each = int(artifact_policy["max_artifact_bytes"])
            else:
                max_each = max(
                    (x.artifact_ref.size_bytes for x in inputs.evidence_objects),
                    default=0,
                )
            if "max_total_bytes" in artifact_policy:
                max_total = int(artifact_policy["max_total_bytes"])
            else:
                max_total = max(
                    sum(x.artifact_ref.size_bytes for x in inputs.evidence_objects),
                    max_each,
                )
            evidence_policy_obj = _json_value(inputs.evidence_policy)
            required_roles = tuple(evidence_policy_obj.get("required_roles", ()))
            artifact_manifest = ArtifactManifestV2(
                inputs.evidence_objects,
                allowed_roles,
                max_each,
                max_total,
                required_roles,
            )
            artifact_ref = self._put(
                artifact_manifest.schema_version,
                inputs.episode_id,
                artifact_manifest,
                "artifact-manifest",
            )
            materialization_digest = canonical_digest(inputs.materialization_receipt)
            primary_measurement_digest = canonical_digest(inputs.primary_measurement)
            snapshot_digest = (
                canonical_digest(inputs.verifier_snapshot)
                if inputs.verifier_snapshot is not None
                else None
            )
            verifier_result_digest = canonical_digest(inputs.verifier_result)
            (
                authority_access_ledger_ref,
                authority_canary_reads,
                authority_cross_episode_reads,
            ) = self._publish_authority_access_ledger(
                inputs.episode_id,
                inputs.authority_access_events,
            )
            facts = (
                (resolved_digest, "resolved_plan", ()),
                (selection_digest, "selection", (resolved_digest,)),
                (effective_digest, "effective_plan", (selection_digest,)),
                (inputs.policy_binding_digest, "policy_binding", (effective_digest,)),
                (
                    materialization_digest,
                    "materialization",
                    (inputs.policy_binding_digest,),
                ),
                (
                    primary_measurement_digest,
                    "primary_measurement",
                    (materialization_digest,),
                ),
                (ledger_ref.sha256, "runner_ledger", (primary_measurement_digest,)),
                (artifact_ref.sha256, "artifact_manifest", (ledger_ref.sha256,)),
            )
            verifier_cleanup_ref = None
            if inputs.verifier_cleanup_receipt is not None:
                verifier_cleanup_ref = self._put(
                    "bb.rl.verifier-cleanup-receipt.v2",
                    inputs.episode_id,
                    verifier_cleanup_projection,
                    "verifier-cleanup",
                )
            nodes = [
                LineageNodeV2(digest, kind, "breadboard", parents)
                for digest, kind, parents in facts
            ]
            last = artifact_ref.sha256
            for digest, kind in (
                (snapshot_digest, "verifier_snapshot"),
                (inputs.verifier_measurement_digest, "verifier_measurement"),
                (verifier_result_digest, "verifier_result"),
            ):
                if digest is not None:
                    _check_digest(digest)
                    nodes.append(LineageNodeV2(digest, kind, "breadboard", (last,)))
                    last = digest
            if verifier_cleanup_ref is not None:
                nodes.append(
                    LineageNodeV2(
                        verifier_cleanup_ref.sha256,
                        "verifier_cleanup",
                        "breadboard",
                        (last,),
                    )
                )
                last = verifier_cleanup_ref.sha256
            if authority_access_ledger_ref is not None:
                nodes.append(
                    LineageNodeV2(
                        authority_access_ledger_ref.sha256,
                        "authority_access_ledger",
                        "breadboard",
                        (last,),
                    )
                )
                last = authority_access_ledger_ref.sha256
            evidence_policy_ref = str(
                evidence_policy_obj.get("record_digest")
                or evidence_policy_obj.get("digest")
                or canonical_digest(evidence_policy_obj)
            )
            retention_policy_record_ref = self._put(
                "bb.rl.retention-policy-registry-record.v2",
                inputs.episode_id,
                inputs.retention_policy,
                "retention-policy",
            )
            retention_policy_ref = retention_policy_record_ref.sha256
            evidence_manifest = ExecutionEvidenceManifestV2(
                inputs.episode_id,
                resolved_digest,
                selection_digest,
                effective_digest,
                inputs.policy_binding_digest,
                ledger_ref,
                materialization_digest,
                primary_measurement_digest,
                snapshot_digest,
                inputs.verifier_measurement_digest,
                verifier_result_digest,
                artifact_ref,
                inputs.primary_disposition,
                inputs.reward_disposition,
                inputs.reward_components,
                evidence_policy_ref,
                retention_policy_ref,
                tuple(nodes),
                last,
                verifier_cleanup_ref,
                inputs.verifier_lease_id,
                retention_policy_record_ref,
                authority_access_ledger_ref=authority_access_ledger_ref,
                authority_canary_reads=authority_canary_reads,
                authority_cross_episode_reads=authority_cross_episode_reads,
            )
            for evidence_object in artifact_manifest.objects:
                if (
                    evidence_object.authorization_policy_ref != evidence_policy_ref
                    or evidence_object.retention_policy_ref != retention_policy_ref
                ):
                    raise EvidenceValidationError(
                        "artifact object policy binding mismatch"
                    )
                self._read_ref_exact(evidence_object.artifact_ref)
            evidence_ref = self._put(
                evidence_manifest.schema_version,
                inputs.episode_id,
                evidence_manifest,
                "evidence-manifest",
            )
            create_ref = self._put(
                "bb.rl.create-response.v2",
                inputs.episode_id,
                inputs.create_response_bytes,
                "create-response",
            )
            run_ref = self._put(
                "bb.rl.run-response.v2",
                inputs.episode_id,
                inputs.run_response_bytes,
                "run-response",
            )
            envelope = CompletedEpisodeEnvelopeV2(
                inputs.episode_id,
                inputs.create_fingerprint,
                inputs.run_fingerprint,
                create_ref,
                run_ref,
                evidence_ref,
                evidence_manifest.lineage_root,
                inputs.primary_disposition,
                inputs.lifecycle_head_ref,
                inputs.lifecycle_head_digest,
                inputs.subject_digest,
            )
            envelope_ref = self._put(
                envelope.schema_version,
                inputs.episode_id,
                envelope,
                "completed-envelope",
            )
            tombstone = EpisodeCompletedTombstoneV2(
                inputs.episode_id,
                inputs.create_fingerprint,
                inputs.run_fingerprint,
                inputs.lifecycle_head_digest,
                run_ref,
                envelope_ref,
                current.generation + 1,
            )
            tombstone_ref = self._put(
                tombstone.schema_version,
                inputs.episode_id,
                tombstone,
                "completed-tombstone",
            )
            locator = EpisodeLocatorRecordV2(
                inputs.episode_id,
                current.generation + 1,
                "completed",
                inputs.lifecycle_head_digest,
                inputs.lifecycle_head_ref,
                tombstone_ref,
                runner_event_refs=current.runner_event_refs,
                runner_event_head=current.runner_event_head,
                runner_effective_plan_digest=current.runner_effective_plan_digest,
            )
            self._locators.compare_and_swap(
                inputs.episode_id, current.generation, locator
            )
            return CompletedPublicationV2(
                envelope,
                envelope_ref,
                tombstone,
                tombstone_ref,
                locator,
                evidence_manifest,
            )

    def _retention_export_window(
        self,
        envelope: CompletedEpisodeEnvelopeV2,
        evidence: ExecutionEvidenceManifestV2,
    ) -> tuple[RetentionPolicyRegistryRecord, str, str]:
        retention_ref = evidence.retention_policy_record_ref
        if retention_ref is None:
            raise EvidenceCorruptError(
                "completed evidence lacks a pinned retention policy record"
            )
        record = self._load_retention_policy(envelope.episode_id, retention_ref)
        if retention_ref.sha256 != evidence.retention_policy_ref:
            raise EvidenceCorruptError(
                "retention policy record does not match manifest policy revision"
            )
        completed_event = self._load_event(envelope.completed_event_ref)
        if (
            completed_event.digest != envelope.completed_event_head
            or completed_event.episode_id != envelope.episode_id
            or completed_event.event_kind != "completed"
            or completed_event.to_state != "completed"
            or completed_event.create_fingerprint != envelope.create_fingerprint
        ):
            raise EvidenceCorruptError(
                "retention anchor is not the verified completed lifecycle event"
            )
        try:
            anchor = datetime.fromisoformat(
                completed_event.observed_at.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as exc:
            raise EvidenceCorruptError(
                "completed lifecycle retention timestamp is invalid"
            ) from exc
        if anchor.tzinfo is None:
            raise EvidenceCorruptError(
                "completed lifecycle retention timestamp must be timezone-aware"
            )
        anchor = anchor.astimezone(UTC)
        try:
            expires = anchor + timedelta(seconds=int(record.grant.maximum_seconds))
        except (OverflowError, ValueError) as exc:
            raise EvidenceCorruptError("retention window is not representable") from exc
        canonical_anchor = anchor.isoformat().replace("+00:00", "Z")
        canonical_expiry = expires.isoformat().replace("+00:00", "Z")
        return record, canonical_anchor, canonical_expiry

    def prepare_export_pins(
        self,
        episode_id: str,
        completed: CompletedPublicationV2,
        *,
        subject_digest: str,
        scope: str = "episode_export",
    ) -> ExportPinsV2:
        _check_digest(subject_digest, "export subject digest")
        if scope != "episode_export":
            raise EvidenceValidationError(
                "export authorization scope must be episode_export"
            )
        with self._lock_for(episode_id):
            current = self._locators.get(episode_id)
            if (
                current is None
                or current.completed_tombstone_ref != completed.tombstone_ref
                or current.closed_tombstone_ref is not None
                or current.quarantine_ref is not None
            ):
                raise LocatorConflictError(
                    "export pins require the current completed graph"
                )
            persisted = self._load_completed(current.completed_tombstone_ref)
            envelope = self._load_completed_envelope(persisted.envelope_ref)
            if (
                persisted != completed.tombstone
                or envelope != completed.envelope
                or envelope.episode_id != episode_id
            ):
                raise EvidenceValidationError("completed publication is grafted")
            self._verify_completed_graph(envelope)
            evidence = self._load_evidence_manifest(envelope.evidence_manifest_ref)
            manifest = self._load_artifact_manifest(evidence.artifact_manifest_ref)
            not_before: str | None = None
            not_after: str | None = None
            if manifest.objects:
                _, not_before, not_after = self._retention_export_window(
                    envelope,
                    evidence,
                )
            authorization_refs: list[ArtifactRef] = []
            decision_refs: list[ArtifactRef] = []
            for evidence_object in manifest.objects:
                decision = RedactionDecisionV2(
                    evidence_policy_ref=evidence.evidence_policy_ref,
                    role=evidence_object.role,
                    source_artifact_digest=evidence_object.artifact_ref.sha256,
                )
                decision_ref = self._put(
                    decision.schema_version,
                    episode_id,
                    decision,
                    f"redaction-{evidence_object.role}",
                )
                authorization = ExportAuthorizationV2(
                    subject=subject_digest,
                    scope=scope,
                    evidence_policy_ref=evidence.evidence_policy_ref,
                    retention_policy_ref=evidence.retention_policy_ref,
                    allowed_roles=(evidence_object.role,),
                    redaction_decision_digest=decision.digest,
                    not_before=not_before,
                    not_after=not_after,
                )
                authorization_ref = self._put(
                    authorization.schema_version,
                    episode_id,
                    authorization,
                    f"export-authorization-{evidence_object.role}",
                )
                decision_refs.append(decision_ref)
                authorization_refs.append(authorization_ref)
            return ExportPinsV2(tuple(authorization_refs), tuple(decision_refs))

    def publish_closed(self, inputs: ClosedPublicationInputsV2) -> ClosedPublicationV2:
        if inputs.cleanup_receipt is None:
            if inputs.cleanup_lease_id is not None or inputs.cleanup_required_resources:
                raise EvidenceValidationError(
                    "no-allocation close cannot claim primary cleanup"
                )
        elif (
            type(inputs.cleanup_receipt) is not SandboxCleanupReceipt
            or not inputs.cleanup_lease_id
            or tuple(inputs.cleanup_required_resources) != _PRIMARY_CLEANUP_RESOURCES
        ):
            raise EvidenceValidationError("primary cleanup resource contract mismatch")
        if inputs.verifier_cleanup_receipt is None:
            if inputs.verifier_cleanup_required_resources:
                raise EvidenceValidationError(
                    "verifier cleanup resources require a verifier cleanup receipt"
                )
        elif (
            tuple(inputs.verifier_cleanup_required_resources)
            != _VERIFIER_CLEANUP_RESOURCES
        ):
            raise EvidenceValidationError("verifier cleanup resource contract mismatch")
        projection = (
            _json_value(inputs.cleanup_receipt)
            if inputs.cleanup_receipt is not None
            else None
        )
        verifier_projection: Mapping[str, Any] | None = None
        with self._lock_for(inputs.episode_id):
            current = self._locators.get(inputs.episode_id)
            if current is None:
                raise LocatorConflictError("closed publication requires an episode")
            if current.closed_tombstone_ref is not None:
                if current.latest_event_head != inputs.closed_event.digest:
                    raise LocatorConflictError("closed locator is absorbing")
                recovered = self.recover(inputs.episode_id)
                if (
                    recovered is None
                    or recovered.closed_tombstone is None
                    or recovered.closed_envelope is None
                ):
                    raise EvidenceCorruptError("closed publication is unreadable")
                return ClosedPublicationV2(
                    recovered.closed_envelope,
                    recovered.closed_tombstone.envelope_ref,
                    recovered.closed_tombstone,
                    current.closed_tombstone_ref,
                    current,
                )
            if current.current_state != "closing":
                raise LocatorConflictError("closed publication requires CLOSING")
            previous_event = self._load_event(current.latest_event_ref)
            if (
                current.latest_event_head != previous_event.digest
                or previous_event.to_state != "closing"
            ):
                raise EvidenceCorruptError("closing locator head is incoherent")
            _validate_transition(previous_event, inputs.closed_event)
            if (
                inputs.closed_event.episode_id != inputs.episode_id
                or inputs.closed_event.event_kind != "closed"
                or inputs.closed_event.to_state != "closed"
            ):
                raise EvidenceValidationError("closed lifecycle event is invalid")
            lifecycle_events = self._recover_events(current)
            primary_lease_id = self._primary_lease_id(lifecycle_events)
            if primary_lease_id is None:
                if (
                    inputs.cleanup_receipt is not None
                    or inputs.cleanup_lease_id is not None
                    or inputs.cleanup_required_resources
                    or inputs.closed_event.primary_lease_id is not None
                ):
                    raise EvidenceValidationError(
                        "no-allocation close contains a primary cleanup graft"
                    )
            else:
                if inputs.cleanup_lease_id != primary_lease_id:
                    raise EvidenceValidationError(
                        "presented primary cleanup lease does not match lifecycle evidence"
                    )
                if projection is None:
                    raise EvidenceValidationError(
                        "allocated close requires primary cleanup proof"
                    )
                _validate_cleanup_projection(
                    projection,
                    expected_lease_id=primary_lease_id,
                    required_resources=_PRIMARY_CLEANUP_RESOURCES,
                )
                if inputs.closed_event.primary_lease_id != primary_lease_id:
                    raise EvidenceValidationError(
                        "closed event lease identity mismatch"
                    )
            if (
                current.completed_tombstone_ref is None
                or current.completed_tombstone_ref != inputs.completed.tombstone_ref
            ):
                raise EvidenceValidationError(
                    "closed publication must point to the current completed tombstone"
                )
            persisted_tombstone = self._load_completed(current.completed_tombstone_ref)
            persisted_envelope = self._load_completed_envelope(
                persisted_tombstone.envelope_ref
            )
            if (
                persisted_tombstone.digest != current.completed_tombstone_ref.sha256
                or persisted_envelope.digest != persisted_tombstone.envelope_ref.sha256
                or persisted_tombstone.episode_id != inputs.episode_id
                or persisted_envelope.episode_id != inputs.episode_id
                or persisted_tombstone.envelope_ref != inputs.completed.envelope_ref
                or persisted_tombstone != inputs.completed.tombstone
                or persisted_envelope != inputs.completed.envelope
            ):
                raise EvidenceValidationError(
                    "completed publication aggregate is grafted or corrupt"
                )
            if (
                persisted_tombstone.create_fingerprint
                != persisted_envelope.create_fingerprint
                or persisted_tombstone.run_fingerprint
                != persisted_envelope.run_fingerprint
                or persisted_tombstone.response_ref
                != persisted_envelope.run_response_ref
                or persisted_tombstone.event_head
                != persisted_envelope.completed_event_head
                or inputs.final_primary_outcome != persisted_envelope.primary_outcome
            ):
                raise EvidenceValidationError(
                    "completed publication identity does not match close inputs"
                )
            self._verify_completed_graph(persisted_envelope)
            persisted_evidence = self._load_evidence_manifest(
                persisted_envelope.evidence_manifest_ref
            )
            verifier_facts_exist = any(
                value is not None
                for value in (
                    persisted_evidence.verifier_snapshot_digest,
                    persisted_evidence.verifier_measurement_digest,
                    persisted_evidence.verifier_result_digest,
                )
            )
            verifier_required_resources: tuple[str, ...] = ()
            if persisted_evidence.verifier_cleanup_receipt_ref is not None:
                verifier_projection = self._load_json(
                    persisted_evidence.verifier_cleanup_receipt_ref
                )
                if not persisted_evidence.verifier_cleanup_lease_id:
                    raise EvidenceCorruptError(
                        "completed verifier cleanup lacks authoritative lease identity"
                    )
                if (
                    inputs.verifier_cleanup_lease_id
                    != persisted_evidence.verifier_cleanup_lease_id
                ):
                    raise EvidenceValidationError(
                        "verifier cleanup lease differs from completed evidence"
                    )
                _validate_cleanup_projection(
                    verifier_projection,
                    expected_lease_id=persisted_evidence.verifier_cleanup_lease_id,
                    required_resources=_VERIFIER_CLEANUP_RESOURCES,
                )
                verifier_required_resources = _VERIFIER_CLEANUP_RESOURCES
                if (
                    inputs.verifier_cleanup_receipt is not None
                    and canonical_digest(_json_value(inputs.verifier_cleanup_receipt))
                    != persisted_evidence.verifier_cleanup_receipt_ref.sha256
                ):
                    raise EvidenceValidationError(
                        "presented verifier cleanup differs from completed evidence"
                    )
            elif verifier_facts_exist:
                raise EvidenceValidationError(
                    "completed verifier evidence lacks pinned cleanup"
                )
            elif inputs.verifier_cleanup_receipt is not None:
                raise EvidenceValidationError(
                    "pre-verifier completion cannot accept a verifier cleanup graft"
                )
            self._validate_export_pins(
                inputs.episode_id,
                persisted_evidence,
                persisted_envelope,
                inputs.export_authorization_refs,
                inputs.redaction_decision_refs,
            )
            cleanup_digest = (
                canonical_digest(projection) if projection is not None else None
            )
            verifier_cleanup_digest = (
                canonical_digest(verifier_projection)
                if verifier_projection is not None
                else None
            )
            closed_event_ref = self._put(
                inputs.closed_event.schema_version,
                inputs.episode_id,
                inputs.closed_event,
                f"event-{inputs.closed_event.sequence}",
            )
            envelope = ClosedEpisodeEnvelopeV2(
                inputs.episode_id,
                persisted_tombstone.envelope_ref,
                cleanup_digest,
                projection,
                closed_event_ref,
                inputs.closed_event.digest,
                persisted_envelope.primary_outcome,
                (_PRIMARY_CLEANUP_RESOURCES if projection is not None else ()),
                verifier_cleanup_digest,
                verifier_projection,
                verifier_required_resources,
                inputs.export_authorization_refs,
                inputs.redaction_decision_refs,
            )
            envelope_ref = self._put(
                envelope.schema_version, inputs.episode_id, envelope, "closed-envelope"
            )
            tombstone = EpisodeClosedTombstoneV2(
                inputs.episode_id,
                persisted_tombstone.create_fingerprint,
                persisted_tombstone.run_fingerprint,
                inputs.closed_event.digest,
                persisted_tombstone.response_ref,
                current.completed_tombstone_ref,
                envelope_ref,
                current.generation + 1,
            )
            tombstone_ref = self._put(
                tombstone.schema_version,
                inputs.episode_id,
                tombstone,
                "closed-tombstone",
            )
            locator = EpisodeLocatorRecordV2(
                inputs.episode_id,
                current.generation + 1,
                "closed",
                inputs.closed_event.digest,
                closed_event_ref,
                current.completed_tombstone_ref,
                tombstone_ref,
                runner_event_refs=current.runner_event_refs,
                runner_event_head=current.runner_event_head,
                runner_effective_plan_digest=current.runner_effective_plan_digest,
            )
            self._locators.compare_and_swap(
                inputs.episode_id, current.generation, locator
            )
            return ClosedPublicationV2(
                envelope, envelope_ref, tombstone, tombstone_ref, locator
            )

    def quarantine(
        self, inputs: QuarantinePublicationInputsV2
    ) -> QuarantinePublicationV2:
        with self._lock_for(inputs.episode_id):
            current = self._require_event_head(inputs.episode_id, inputs.event)
            if current.closed_tombstone_ref:
                raise LocatorConflictError("closed locator is absorbing")
            ref = self._put(
                "bb.rl.quarantine.v2",
                inputs.episode_id,
                {
                    "schema_version": "bb.rl.quarantine.v2",
                    "episode_id": inputs.episode_id,
                    "event_head": inputs.event.digest,
                    "failure": inputs.failure,
                },
                "quarantine",
            )
            locator = EpisodeLocatorRecordV2(
                inputs.episode_id,
                current.generation + 1,
                "quarantined",
                inputs.event.digest,
                current.latest_event_ref,
                current.completed_tombstone_ref,
                None,
                ref,
                runner_event_refs=current.runner_event_refs,
                runner_event_head=current.runner_event_head,
                runner_effective_plan_digest=current.runner_effective_plan_digest,
            )
            self._locators.compare_and_swap(
                inputs.episode_id, current.generation, locator
            )
            return QuarantinePublicationV2(ref, locator)

    def recover(self, episode_id: str) -> RecoveredEpisodeV2 | None:
        locator = self._locators.get(episode_id)
        if locator is None:
            return None
        try:
            if locator.episode_id != episode_id:
                raise EvidenceCorruptError("locator episode identity mismatch")
            events = self._recover_events(locator)
            completed_ts = (
                self._load_completed(locator.completed_tombstone_ref)
                if locator.completed_tombstone_ref
                else None
            )
            quarantine_value: Mapping[str, Any] | None = None
            if locator.quarantine_ref is not None:
                quarantine_value = self._load_json(locator.quarantine_ref)
                expected_quarantine_keys = {
                    "schema_version",
                    "episode_id",
                    "event_head",
                    "failure",
                }
                if (
                    set(quarantine_value) != expected_quarantine_keys
                    or quarantine_value.get("schema_version") != "bb.rl.quarantine.v2"
                    or quarantine_value.get("episode_id") != episode_id
                    or quarantine_value.get("event_head") != locator.latest_event_head
                ):
                    raise EvidenceCorruptError(
                        "quarantine evidence identity or event head mismatch"
                    )
                quarantine_failure = _failure_from(quarantine_value.get("failure"))
                if (
                    quarantine_failure is None
                    or canonical_digest(quarantine_value["failure"])
                    != quarantine_failure.digest
                ):
                    raise EvidenceCorruptError(
                        "quarantine failure projection is invalid"
                    )
            closed_ts = (
                self._load_closed(locator.closed_tombstone_ref)
                if locator.closed_tombstone_ref
                else None
            )
            completed_env = (
                self._load_completed_envelope(completed_ts.envelope_ref)
                if completed_ts
                else None
            )
            closed_env = (
                self._load_closed_envelope(closed_ts.envelope_ref)
                if closed_ts
                else None
            )
            identities = (
                events,
                (completed_ts,),
                (closed_ts,),
                (completed_env,),
                (closed_env,),
            )
            if any(
                item is not None and item.episode_id != episode_id
                for group in identities
                for item in group
            ):
                raise EvidenceCorruptError("recovery graph episode identity mismatch")
            if locator.closed_tombstone_ref is not None and (
                locator.current_state != "closed" or locator.quarantine_ref is not None
            ):
                raise EvidenceCorruptError(
                    "closed locator terminal state is incoherent"
                )
            if locator.quarantine_ref is not None and (
                locator.current_state != "quarantined"
                or locator.closed_tombstone_ref is not None
            ):
                raise EvidenceCorruptError(
                    "quarantine locator terminal state is incoherent"
                )
            if completed_ts:
                if completed_ts.locator_generation > locator.generation:
                    raise EvidenceCorruptError(
                        "completed tombstone locator generation mismatch"
                    )
                if (
                    locator.current_state == "completed"
                    and completed_ts.locator_generation != locator.generation
                ):
                    raise EvidenceCorruptError("completed locator generation mismatch")
                if (
                    completed_env is None
                    or completed_ts.event_head != completed_env.completed_event_head
                    or completed_ts.create_fingerprint
                    != completed_env.create_fingerprint
                    or completed_ts.run_fingerprint != completed_env.run_fingerprint
                    or completed_ts.response_ref.sha256
                    != completed_env.run_response_ref.sha256
                ):
                    raise EvidenceCorruptError(
                        "completed tombstone envelope linkage mismatch"
                    )
                completed_events = tuple(
                    event for event in events if event.digest == completed_ts.event_head
                )
                if (
                    len(completed_events) != 1
                    or completed_env.completed_event_ref.sha256
                    != completed_events[0].digest
                ):
                    raise EvidenceCorruptError(
                        "completed event is not uniquely anchored in lifecycle history"
                    )
            if closed_ts:
                if (
                    completed_ts is None
                    or locator.completed_tombstone_ref is None
                    or closed_ts.completed_tombstone_ref.sha256
                    != locator.completed_tombstone_ref.sha256
                ):
                    raise EvidenceCorruptError(
                        "closed tombstone does not point backward to completed"
                    )
                if (
                    closed_ts.create_fingerprint != completed_ts.create_fingerprint
                    or closed_ts.run_fingerprint != completed_ts.run_fingerprint
                    or closed_ts.response_ref.sha256 != completed_ts.response_ref.sha256
                ):
                    raise EvidenceCorruptError(
                        "closed tombstone completed identity mismatch"
                    )
                if (
                    closed_ts.locator_generation != locator.generation
                    or closed_ts.event_head != locator.latest_event_head
                ):
                    raise EvidenceCorruptError(
                        "closed tombstone locator linkage mismatch"
                    )
            primary_lease_id = self._primary_lease_id(events)
            evidence: ExecutionEvidenceManifestV2 | None = None
            if completed_env:
                self._verify_completed_graph(completed_env)
                evidence = self._load_evidence_manifest(
                    completed_env.evidence_manifest_ref
                )
            if closed_env:
                if (
                    completed_ts is None
                    or closed_env.completed_envelope_ref.sha256
                    != completed_ts.envelope_ref.sha256
                ):
                    raise EvidenceCorruptError(
                        "closed envelope completed reference mismatch"
                    )
                if (
                    completed_env is None
                    or closed_env.primary_outcome != completed_env.primary_outcome
                ):
                    raise EvidenceCorruptError(
                        "closed and completed primary dispositions mismatch"
                    )
                if (
                    closed_env.reconciliation_event_head != locator.latest_event_head
                    or closed_env.reconciliation_event_ref.sha256
                    != locator.latest_event_ref.sha256
                ):
                    raise EvidenceCorruptError(
                        "closed envelope reconciliation linkage mismatch"
                    )
                if primary_lease_id is None:
                    if (
                        closed_env.cleanup_receipt is not None
                        or closed_env.cleanup_receipt_digest is not None
                        or closed_env.cleanup_required_resources
                    ):
                        raise EvidenceCorruptError(
                            "no-allocation closed evidence contains a cleanup graft"
                        )
                else:
                    if closed_env.cleanup_receipt is None:
                        raise EvidenceCorruptError(
                            "allocated closed evidence lacks cleanup proof"
                        )
                    _validate_cleanup_projection(
                        closed_env.cleanup_receipt,
                        expected_lease_id=primary_lease_id,
                        required_resources=_PRIMARY_CLEANUP_RESOURCES,
                    )
                if evidence is None:
                    raise EvidenceCorruptError(
                        "closed evidence lacks completed evidence"
                    )
                self._validate_export_pins(
                    episode_id,
                    evidence,
                    completed_env,
                    closed_env.export_authorization_refs,
                    closed_env.redaction_decision_refs,
                )
            verifier_cleanup_receipt: SandboxCleanupReceipt | None = None
            verifier_lease_id: str | None = None
            if (
                evidence is not None
                and evidence.verifier_cleanup_receipt_ref is not None
            ):
                if evidence.verifier_cleanup_lease_id is None:
                    raise EvidenceCorruptError(
                        "verifier cleanup lacks authoritative lease identity"
                    )
                verifier_cleanup_projection = self._load_json(
                    evidence.verifier_cleanup_receipt_ref
                )
                _validate_cleanup_projection(
                    verifier_cleanup_projection,
                    expected_lease_id=evidence.verifier_cleanup_lease_id,
                    required_resources=_VERIFIER_CLEANUP_RESOURCES,
                )
                verifier_cleanup_receipt = SandboxCleanupReceipt(
                    lease_id=evidence.verifier_cleanup_lease_id,
                    steps=tuple(
                        CleanupStepReceipt(
                            resource=str(step["resource"]),
                            state=CleanupState(str(step["state"]).lower()),
                            detail=str(step.get("detail", "")),
                        )
                        for step in verifier_cleanup_projection["steps"]
                    ),
                    state=CleanupState(
                        str(verifier_cleanup_projection["state"]).lower()
                    ),
                )
                verifier_lease_id = evidence.verifier_cleanup_lease_id
            runner_events = self.recover_runner_events(episode_id)
            if evidence is not None:
                ledger_value = self._load_json(evidence.runner_ledger_ref)
                if tuple(ledger_value.get("events", ())) != runner_events:
                    raise EvidenceCorruptError(
                        "runner ledger does not match durable journal"
                    )
            return RecoveredEpisodeV2(
                locator,
                events,
                completed_ts,
                closed_ts,
                completed_env,
                closed_env,
                locator.quarantine_ref is not None,
                runner_events,
                primary_lease_id,
                verifier_cleanup_receipt,
                verifier_lease_id,
                evidence,
            )
        except (
            KeyError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            EvidenceError,
        ) as exc:
            if isinstance(exc, EvidenceCorruptError):
                raise
            raise EvidenceCorruptError("episode evidence recovery failed") from exc

    def export_closed(
        self,
        episode_id: str,
        authorization: ExportAuthorizationV2,
        roles: Iterable[str],
    ) -> ExportManifestV2:
        if type(authorization) is not ExportAuthorizationV2:
            raise TypeError("authorization must be an exact ExportAuthorizationV2")
        recovered = self._recover_closed_for_export(episode_id)
        pinned = tuple(
            self._load_export_authorization(ref)
            for ref in recovered.closed_envelope.export_authorization_refs
        )
        matches = tuple(item for item in pinned if item.digest == authorization.digest)
        if len(matches) != 1:
            raise ExportDeniedError(
                "authorization is not uniquely pinned by the closed envelope"
            )
        return self._export_with_pinned_authorization(
            episode_id,
            recovered,
            matches[0],
            roles,
        )

    def export_closed_claims(
        self,
        episode_id: str,
        claims: ExportAuthorizationClaimsV2,
    ) -> ExportManifestV2:
        if type(claims) is not ExportAuthorizationClaimsV2:
            raise TypeError("claims must be an exact ExportAuthorizationClaimsV2")
        recovered = self._recover_closed_for_export(episode_id)
        pinned = tuple(
            self._load_export_authorization(ref)
            for ref in recovered.closed_envelope.export_authorization_refs
        )
        matches = tuple(
            authorization
            for authorization in pinned
            if authorization.subject == claims.subject_digest
            and authorization.scope == claims.scope
            and authorization.evidence_policy_ref == claims.evidence_policy_ref
            and authorization.retention_policy_ref == claims.retention_policy_ref
            and authorization.allowed_roles == claims.allowed_roles
            and authorization.redaction_decision_digest
            == claims.redaction_decision_digest
        )
        if len(matches) != 1:
            raise ExportDeniedError(
                "export claims do not uniquely select a pinned authorization"
            )
        return self._export_with_pinned_authorization(
            episode_id,
            recovered,
            matches[0],
            claims.allowed_roles,
        )

    def _recover_closed_for_export(self, episode_id: str) -> RecoveredEpisodeV2:
        recovered = self.recover(episode_id)
        if (
            recovered is None
            or recovered.closed_envelope is None
            or recovered.completed_envelope is None
            or recovered.closed_tombstone is None
        ):
            raise ExportDeniedError("only verified closed evidence is exportable")
        return recovered

    def _export_with_pinned_authorization(
        self,
        episode_id: str,
        recovered: RecoveredEpisodeV2,
        authorization: ExportAuthorizationV2,
        roles: Iterable[str],
    ) -> ExportManifestV2:
        now = self._clock()
        if now.tzinfo is None:
            raise ExportDeniedError("repository clock must be timezone-aware")
        for boundary, is_lower in (
            (authorization.not_before, True),
            (authorization.not_after, False),
        ):
            if boundary is None:
                continue
            try:
                instant = datetime.fromisoformat(boundary.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ExportDeniedError(
                    "invalid retention authorization window"
                ) from exc
            if instant.tzinfo is None:
                raise ExportDeniedError(
                    "retention authorization window must be timezone-aware"
                )
            if (is_lower and now < instant) or (not is_lower and now >= instant):
                raise ExportDeniedError(
                    "retention authorization is not active or has expired"
                )
        evidence = self._load_evidence_manifest(
            recovered.completed_envelope.evidence_manifest_ref
        )
        manifest = self._load_artifact_manifest(evidence.artifact_manifest_ref)
        redaction_refs = recovered.closed_envelope.redaction_decision_refs
        redaction_matches = tuple(
            ref
            for ref in redaction_refs
            if ref.sha256 == authorization.redaction_decision_digest
        )
        if len(redaction_matches) != 1:
            raise ExportDeniedError(
                "redaction decision is not uniquely pinned by the closed envelope"
            )
        self._load_json(redaction_matches[0])
        requested = tuple(sorted(set(roles)))
        if not set(requested) <= set(authorization.allowed_roles) or not set(
            requested
        ) <= set(manifest.allowed_roles):
            raise ExportDeniedError("requested role is not authorized")
        if (
            authorization.evidence_policy_ref != evidence.evidence_policy_ref
            or authorization.retention_policy_ref != evidence.retention_policy_ref
        ):
            raise ExportDeniedError("pinned policy revision mismatch")
        selected = tuple(
            self._export_evidence_object(value, episode_id)
            for value in manifest.objects
            if value.role in requested
        )
        omitted = tuple(
            (value.role, value.artifact_ref.sha256)
            for value in manifest.objects
            if value.role not in requested
        )
        return ExportManifestV2(
            episode_id,
            recovered.closed_tombstone.envelope_ref,
            authorization.digest,
            authorization.evidence_policy_ref,
            authorization.retention_policy_ref,
            requested,
            authorization.redaction_decision_digest,
            omitted,
            selected,
        )

    def _export_evidence_object(
        self, value: EvidenceObjectV2, episode_id: str
    ) -> EvidenceObjectV2:
        payload = self._read_ref_exact(value.artifact_ref)
        redacted_metadata = _redact_export_value(
            _json_value(value.artifact_ref.metadata)
        )
        sanitized_payload = payload
        payload_changed = False
        try:
            decoded = json.loads(payload)
        except UnicodeDecodeError as exc:
            raise ExportDeniedError(
                "opaque artifact cannot be safely inspected for secrets"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ExportDeniedError(
                "non-JSON artifact lacks an independently safe export projection"
            ) from exc
        else:
            redacted_value = _redact_export_value(decoded)
            if _contains_export_hazard(redacted_value):
                raise ExportDeniedError(
                    "redacted evidence retains a classified export hazard"
                )
            sanitized_payload = canonical_json_bytes(redacted_value)
            payload_changed = sanitized_payload != payload
        if _contains_export_hazard(redacted_metadata):
            raise ExportDeniedError(
                "redacted evidence metadata retains a classified export hazard"
            )
        metadata_changed = redacted_metadata != _json_value(value.artifact_ref.metadata)
        if not payload_changed and not metadata_changed:
            return value
        sanitized_ref = self._cas.put_bytes(  # type: ignore[attr-defined]
            sanitized_payload,
            artifact_id=f"v2/{episode_id}/export-sanitized-{value.role}/{_digest(sanitized_payload)[7:]}",
            media_type=value.artifact_ref.media_type,
            metadata={
                **redacted_metadata,
                "schema": "bb.rl.export-sanitized-artifact.v2",
                "episode_id": episode_id,
            },
        )
        self._read_ref_exact(sanitized_ref)
        return EvidenceObjectV2(
            value.role,
            value.producer,
            sanitized_ref,
            value.authorization_policy_ref,
            value.retention_policy_ref,
            value.parent_digests,
        )

    def enumerate_locators(self) -> tuple[EpisodeLocatorRecordV2, ...]:
        return self._locators.enumerate()

    def get_response_bytes(self, ref: ArtifactRef) -> bytes:
        """Load an exact persisted response through the bounded verified CAS seam."""
        if ref.media_type != "application/json":
            raise EvidenceCorruptError("response reference has an invalid media type")
        payload = self._cas.get_bytes(ref, max_bytes=self._max_object_bytes)
        if len(payload) != ref.size_bytes or _digest(payload) != ref.sha256:
            raise EvidenceCorruptError(
                "response bytes do not match their immutable reference"
            )
        return payload

    def _return_existing_completed(
        self,
        current: EpisodeLocatorRecordV2,
        *,
        create_fingerprint: str,
        run_fingerprint: str,
        lifecycle_head_ref: ArtifactRef,
        lifecycle_head_digest: str,
        create_response_bytes: bytes,
        run_response_bytes: bytes,
        primary_outcome: str,
        subject_digest: str | None,
    ) -> CompletedPublicationV2:
        recovered = self.recover(current.episode_id)
        if (
            recovered is None
            or recovered.completed_tombstone is None
            or recovered.completed_envelope is None
            or current.completed_tombstone_ref is None
        ):
            raise EvidenceCorruptError("completed publication is unreadable")
        tombstone = recovered.completed_tombstone
        envelope = recovered.completed_envelope
        if recovered.evidence_manifest is None:
            raise EvidenceCorruptError("completed evidence manifest is unreadable")
        if (
            tombstone.create_fingerprint != create_fingerprint
            or tombstone.run_fingerprint != run_fingerprint
            or tombstone.event_head != lifecycle_head_digest
            or envelope.completed_event_ref != lifecycle_head_ref
            or envelope.primary_outcome != primary_outcome
            or envelope.subject_digest != subject_digest
            or envelope.create_response_ref.sha256 != _digest(create_response_bytes)
            or envelope.run_response_ref.sha256 != _digest(run_response_bytes)
        ):
            raise LocatorConflictError(
                "completed publication identity conflicts with existing publication"
            )
        return CompletedPublicationV2(
            envelope,
            tombstone.envelope_ref,
            tombstone,
            current.completed_tombstone_ref,
            current,
            recovered.evidence_manifest,
        )

    def _require_event_head(
        self, episode_id: str, event: LifecycleEventV2
    ) -> EpisodeLocatorRecordV2:
        current = self._locators.get(episode_id)
        if (
            current is None
            or current.latest_event_head != event.digest
            or current.latest_event_ref.sha256 != event.digest
        ):
            raise LocatorConflictError("publication event is not current")
        return current

    def _load_json(self, ref: ArtifactRef) -> Mapping[str, Any]:
        try:
            payload = self._cas.get_bytes(ref, max_bytes=self._max_object_bytes)
        except ArtifactIntegrityError as exc:
            raise EvidenceCorruptError("CAS artifact integrity failure") from exc
        if len(payload) != ref.size_bytes or _digest(payload) != ref.sha256:
            raise EvidenceCorruptError(
                "artifact bytes do not match immutable reference"
            )
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvidenceCorruptError("artifact is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise EvidenceCorruptError("evidence artifact must be a canonical object")
        if canonical_json_bytes(value) != payload:
            raise EvidenceCorruptError("artifact is not canonically encoded")
        return value

    def _load_event(self, ref: ArtifactRef) -> LifecycleEventV2:
        v = self._load_json(ref)
        return LifecycleEventV2(
            str(v["episode_id"]),
            int(v["sequence"]),
            v.get("previous_event_digest"),
            v.get("from_state"),
            str(v["to_state"]),
            str(v["event_kind"]),
            str(v["observed_at"]),
            v.get("create_fingerprint"),
            v.get("run_fingerprint"),
            v.get("effective_plan_digest"),
            tuple(_ref_from(x) for x in v.get("fact_refs", [])),
            tuple(v.get("fact_digests", [])),
            _failure_from(v.get("primary_fact")),
            _failure_from(v.get("cleanup_fact")),
            v.get("primary_lease_id"),
            v.get("cancel_reason"),
            v.get("cancel_fingerprint"),
        )

    def _primary_lease_id(self, events: Sequence[LifecycleEventV2]) -> str | None:
        primary_lease_id: str | None = None
        for index, event in enumerate(events):
            if primary_lease_id is None:
                if event.primary_lease_id is None:
                    continue
                previous = events[index - 1] if index else None
                cancellation_cleanup_owns_lease = (
                    previous is not None
                    and previous.from_state == "allocating"
                    and previous.to_state == "cancel_requested"
                    and previous.event_kind == "cancellation_requested"
                    and event.from_state == "cancel_requested"
                    and event.to_state == "closing"
                    and event.event_kind == "cancellation_won"
                    and event.primary_fact is not None
                    and event.primary_fact.category == "cancellation"
                    and event.primary_fact.lease_id == event.primary_lease_id
                )
                if event.event_kind != "workspace_ready" and not cancellation_cleanup_owns_lease:
                    raise EvidenceCorruptError(
                        "primary lease identity has no authoritative lifecycle owner"
                    )
                primary_lease_id = event.primary_lease_id
            elif event.primary_lease_id != primary_lease_id:
                raise EvidenceCorruptError(
                    "primary lease identity is not retained across lifecycle events"
                )
        return primary_lease_id

    def _recover_events(
        self, locator: EpisodeLocatorRecordV2
    ) -> tuple[LifecycleEventV2, ...]:
        events: list[LifecycleEventV2] = []
        ref = locator.latest_event_ref
        for _ in range(self._max_traversal_objects):
            event = self._load_event(ref)
            if event.digest != ref.sha256:
                raise EvidenceCorruptError("event ref does not bind canonical event")
            events.append(event)
            if event.sequence == 0:
                break
            if not event.fact_refs:
                raise EvidenceCorruptError("event chain lacks previous event reference")
            candidates = [
                x for x in event.fact_refs if x.sha256 == event.previous_event_digest
            ]
            if len(candidates) != 1:
                raise EvidenceCorruptError(
                    "event previous reference is missing or ambiguous"
                )
            ref = candidates[0]
        else:
            raise EvidenceCorruptError("event traversal bound exceeded")
        events.reverse()
        if (
            [x.sequence for x in events] != list(range(len(events)))
            or events[-1].digest != locator.latest_event_head
            or events[-1].to_state != locator.current_state
            or events[-1].digest != locator.latest_event_ref.sha256
            or any(event.episode_id != locator.episode_id for event in events)
        ):
            raise EvidenceCorruptError("event chain is non-contiguous")
        previous: LifecycleEventV2 | None = None
        try:
            for event in events:
                _validate_transition(previous, event)
                previous = event
        except EvidenceError as exc:
            raise EvidenceCorruptError(
                "event chain contains an illegal transition"
            ) from exc
        return tuple(events)

    def _load_completed(self, ref: ArtifactRef) -> EpisodeCompletedTombstoneV2:
        v = self._load_json(ref)
        return EpisodeCompletedTombstoneV2(
            str(v["episode_id"]),
            str(v["create_fingerprint"]),
            str(v["run_fingerprint"]),
            str(v["event_head"]),
            _ref_from(v["response_ref"]),
            _ref_from(v["envelope_ref"]),
            int(v["locator_generation"]),
        )

    def _load_closed(self, ref: ArtifactRef) -> EpisodeClosedTombstoneV2:
        v = self._load_json(ref)
        return EpisodeClosedTombstoneV2(
            str(v["episode_id"]),
            str(v["create_fingerprint"]),
            str(v["run_fingerprint"]),
            str(v["event_head"]),
            _ref_from(v["response_ref"]),
            _ref_from(v["completed_tombstone_ref"]),
            _ref_from(v["envelope_ref"]),
            int(v["locator_generation"]),
        )

    def _load_completed_envelope(self, ref: ArtifactRef) -> CompletedEpisodeEnvelopeV2:
        v = self._load_json(ref)
        return CompletedEpisodeEnvelopeV2(
            str(v["episode_id"]),
            str(v["create_fingerprint"]),
            str(v["run_fingerprint"]),
            _ref_from(v["create_response_ref"]),
            _ref_from(v["run_response_ref"]),
            _ref_from(v["evidence_manifest_ref"]),
            str(v["evidence_root"]),
            str(v["primary_outcome"]),
            _ref_from(v["completed_event_ref"]),
            str(v["completed_event_head"]),
            v.get("subject_digest"),
        )

    def _load_closed_envelope(self, ref: ArtifactRef) -> ClosedEpisodeEnvelopeV2:
        v = self._load_json(ref)
        return ClosedEpisodeEnvelopeV2(
            str(v["episode_id"]),
            _ref_from(v["completed_envelope_ref"]),
            v.get("cleanup_receipt_digest"),
            v.get("cleanup_receipt"),
            _ref_from(v["reconciliation_event_ref"]),
            str(v["reconciliation_event_head"]),
            str(v["primary_outcome"]),
            tuple(
                v.get(
                    "cleanup_required_resources",
                    _PRIMARY_CLEANUP_RESOURCES
                    if v.get("cleanup_receipt") is not None
                    else (),
                )
            ),
            v.get("verifier_cleanup_receipt_digest"),
            v.get("verifier_cleanup_receipt"),
            tuple(v.get("verifier_cleanup_required_resources", ())),
            tuple(_ref_from(x) for x in v.get("export_authorization_refs", [])),
            tuple(_ref_from(x) for x in v.get("redaction_decision_refs", [])),
        )

    def _load_export_authorization(self, ref: ArtifactRef) -> ExportAuthorizationV2:
        try:
            v = self._load_json(ref)
            if v.get("schema_version") != "bb.rl.export-authorization.v2":
                raise EvidenceCorruptError("authorization schema is invalid")
            authorization = ExportAuthorizationV2(
                str(v["subject"]),
                str(v["scope"]),
                str(v["evidence_policy_ref"]),
                str(v["retention_policy_ref"]),
                tuple(v["allowed_roles"]),
                str(v["redaction_decision_digest"]),
                v.get("not_before"),
                v.get("not_after"),
            )
        except EvidenceCorruptError:
            raise
        except (KeyError, TypeError, ValueError, EvidenceValidationError) as exc:
            raise EvidenceCorruptError("authorization projection is malformed") from exc
        if authorization.digest != ref.sha256:
            raise EvidenceCorruptError(
                "authorization reference does not bind canonical authorization"
            )
        return authorization

    def _validate_export_pins(
        self,
        episode_id: str,
        evidence: ExecutionEvidenceManifestV2,
        completed_envelope: CompletedEpisodeEnvelopeV2,
        authorization_refs: Sequence[ArtifactRef],
        redaction_refs: Sequence[ArtifactRef],
    ) -> None:
        if len({ref.sha256 for ref in authorization_refs}) != len(authorization_refs):
            raise EvidenceValidationError("export authorization pins must be unique")
        if len({ref.sha256 for ref in redaction_refs}) != len(redaction_refs):
            raise EvidenceValidationError("redaction decision pins must be unique")
        manifest = self._load_artifact_manifest(evidence.artifact_manifest_ref)
        objects_by_role = {item.role: item for item in manifest.objects}
        expected_not_before: str | None = None
        expected_not_after: str | None = None
        if objects_by_role:
            _, expected_not_before, expected_not_after = self._retention_export_window(
                completed_envelope, evidence
            )
        decisions: dict[str, RedactionDecisionV2] = {}
        decision_roles: set[str] = set()
        for ref in redaction_refs:
            if (
                ref.metadata.get("schema") != "bb.rl.redaction-decision.v2"
                or ref.metadata.get("episode_id") != episode_id
            ):
                raise EvidenceValidationError(
                    "redaction decision pin schema or episode mismatch"
                )
            value = self._load_json(ref)
            try:
                decision = RedactionDecisionV2(
                    evidence_policy_ref=str(value["evidence_policy_ref"]),
                    role=str(value["role"]),
                    source_artifact_digest=str(value["source_artifact_digest"]),
                    transform_id=str(value["transform_id"]),
                    transform_implementation_digest=str(
                        value["transform_implementation_digest"]
                    ),
                )
            except (KeyError, TypeError, ValueError, EvidenceError) as exc:
                raise EvidenceValidationError(
                    "redaction decision is malformed"
                ) from exc
            evidence_object = objects_by_role.get(decision.role)
            if (
                decision.digest != ref.sha256
                or evidence_object is None
                or decision.evidence_policy_ref != evidence.evidence_policy_ref
                or decision.source_artifact_digest
                != evidence_object.artifact_ref.sha256
                or decision.role in decision_roles
            ):
                raise EvidenceValidationError("redaction decision binding mismatch")
            decisions[ref.sha256] = decision
            decision_roles.add(decision.role)
        authorization_roles: set[str] = set()
        referenced_decisions: set[str] = set()
        for ref in authorization_refs:
            if (
                ref.metadata.get("schema") != "bb.rl.export-authorization.v2"
                or ref.metadata.get("episode_id") != episode_id
            ):
                raise EvidenceValidationError(
                    "export authorization pin schema or episode mismatch"
                )
            try:
                authorization = self._load_export_authorization(ref)
            except EvidenceCorruptError as exc:
                raise EvidenceValidationError(
                    "export authorization pin is malformed"
                ) from exc
            if (
                authorization.evidence_policy_ref != evidence.evidence_policy_ref
                or authorization.retention_policy_ref != evidence.retention_policy_ref
            ):
                raise EvidenceValidationError(
                    "export authorization policy binding mismatch"
                )
            if (
                authorization.not_before != expected_not_before
                or authorization.not_after != expected_not_after
            ):
                raise EvidenceValidationError(
                    "export authorization retention window mismatch"
                )
            if authorization.redaction_decision_digest not in decisions:
                raise EvidenceValidationError(
                    "export authorization redaction decision is not pinned"
                )
            if (
                authorization.scope != "episode_export"
                or _DIGEST_RE.fullmatch(authorization.subject) is None
                or len(authorization.allowed_roles) != 1
                or authorization.allowed_roles[0] not in objects_by_role
                or authorization.allowed_roles[0] in authorization_roles
                or decisions[authorization.redaction_decision_digest].role
                != authorization.allowed_roles[0]
            ):
                raise EvidenceValidationError(
                    "export authorization role binding mismatch"
                )
            authorization_roles.add(authorization.allowed_roles[0])
            referenced_decisions.add(authorization.redaction_decision_digest)
        if (
            referenced_decisions != set(decisions)
            or decision_roles != set(objects_by_role)
            or authorization_roles != set(objects_by_role)
        ):
            raise EvidenceValidationError(
                "export pins are not exact per-role singletons"
            )

    def _load_evidence_manifest(self, ref: ArtifactRef) -> ExecutionEvidenceManifestV2:
        v = self._load_json(ref)
        nodes = tuple(
            LineageNodeV2(
                str(x["node_digest"]),
                str(x["kind"]),
                str(x["producer"]),
                tuple(x.get("parent_digests", [])),
            )
            for x in v["lineage_nodes"]
        )
        authority_ledger_ref = (
            _ref_from(v["authority_access_ledger_ref"])
            if v.get("authority_access_ledger_ref")
            else None
        )
        manifest = ExecutionEvidenceManifestV2(
            str(v["episode_id"]),
            str(v["resolved_plan_digest"]),
            str(v["selection_digest"]),
            str(v["effective_plan_digest"]),
            str(v["policy_binding_digest"]),
            _ref_from(v["runner_ledger_ref"]),
            str(v["materialization_digest"]),
            (
                str(v["primary_measurement_digest"])
                if v.get("primary_measurement_digest") is not None
                else None
            ),
            v.get("verifier_snapshot_digest"),
            v.get("verifier_measurement_digest"),
            v.get("verifier_result_digest"),
            _ref_from(v["artifact_manifest_ref"]),
            str(v["primary_disposition"]),
            str(v["reward_disposition"]),
            v["reward_components"],
            str(v["evidence_policy_ref"]),
            str(v["retention_policy_ref"]),
            nodes,
            str(v["lineage_root"]),
            (
                _ref_from(v["verifier_cleanup_receipt_ref"])
                if v.get("verifier_cleanup_receipt_ref")
                else None
            ),
            v.get("verifier_cleanup_lease_id"),
            (
                _ref_from(v["retention_policy_record_ref"])
                if v.get("retention_policy_record_ref")
                else None
            ),
            primary_failure_digest=v.get("primary_failure_digest"),
            authority_access_ledger_ref=authority_ledger_ref,
            authority_canary_reads=tuple(v.get("authority_canary_reads", ())),
            authority_cross_episode_reads=tuple(
                v.get("authority_cross_episode_reads", ())
            ),
        )
        if authority_ledger_ref is not None:
            ledger_value = self._load_json(authority_ledger_ref)
            events = tuple(
                AuthorityAccessEventV2(
                    sequence=int(event["sequence"]),
                    actor_episode_id=str(event["actor_episode_id"]),
                    authority_episode_id=str(event["authority_episode_id"]),
                    authority_ref=str(event["authority_ref"]),
                    canary=str(event["canary"]),
                    source_ref=str(event["source_ref"]),
                )
                for event in ledger_value["events"]
            )
            ledger = AuthorityAccessLedgerV2(
                episode_id=str(ledger_value["episode_id"]),
                events=events,
            )
            if (
                ledger.digest != authority_ledger_ref.sha256
                or ledger.episode_id != manifest.episode_id
                or ledger.canary_reads != manifest.authority_canary_reads
                or ledger.cross_episode_reads != manifest.authority_cross_episode_reads
            ):
                raise EvidenceCorruptError(
                    "authority access ledger does not bind manifest projection"
                )
        return manifest

    def _load_retention_policy(
        self,
        episode_id: str,
        ref: ArtifactRef,
    ) -> RetentionPolicyRegistryRecord:
        if (
            ref.metadata.get("schema") != "bb.rl.retention-policy-registry-record.v2"
            or ref.metadata.get("episode_id") != episode_id
        ):
            raise EvidenceCorruptError(
                "retention policy record schema or episode binding mismatch"
            )
        value = self._load_json(ref)
        try:
            record = RetentionPolicyRegistryRecord.model_validate(value)
        except (TypeError, ValueError) as exc:
            raise EvidenceCorruptError(
                "retention policy registry record is malformed"
            ) from exc
        if canonical_digest(record) != ref.sha256:
            raise EvidenceCorruptError(
                "retention policy registry record digest mismatch"
            )
        return record

    def _load_artifact_manifest(self, ref: ArtifactRef) -> ArtifactManifestV2:
        v = self._load_json(ref)
        objects = tuple(
            EvidenceObjectV2(
                str(x["role"]),
                str(x["producer"]),
                _ref_from(x["artifact_ref"]),
                str(x["authorization_policy_ref"]),
                str(x["retention_policy_ref"]),
                tuple(x.get("parent_digests", [])),
            )
            for x in v["objects"]
        )
        return ArtifactManifestV2(
            objects,
            tuple(v["allowed_roles"]),
            int(v["max_each_bytes"]),
            int(v["max_total_bytes"]),
            tuple(v.get("required_roles", [])),
        )

    def _verify_completed_graph(self, envelope: CompletedEpisodeEnvelopeV2) -> None:
        evidence = self._load_evidence_manifest(envelope.evidence_manifest_ref)
        if (
            evidence.episode_id != envelope.episode_id
            or evidence.lineage_root != envelope.evidence_root
            or evidence.primary_disposition != envelope.primary_outcome
        ):
            raise EvidenceCorruptError(
                "completed evidence identity, root, or primary disposition mismatch"
            )
        expected_nodes: list[tuple[str, str, tuple[str, ...]]] = [
            ("resolved_plan", evidence.resolved_plan_digest, ()),
            ("selection", evidence.selection_digest, (evidence.resolved_plan_digest,)),
            (
                "effective_plan",
                evidence.effective_plan_digest,
                (evidence.selection_digest,),
            ),
            (
                "policy_binding",
                evidence.policy_binding_digest,
                (evidence.effective_plan_digest,),
            ),
            (
                "materialization",
                evidence.materialization_digest,
                (evidence.policy_binding_digest,),
            ),
        ]
        previous = evidence.materialization_digest
        if evidence.primary_measurement_digest is not None:
            expected_nodes.append(
                (
                    "primary_measurement",
                    evidence.primary_measurement_digest,
                    (previous,),
                )
            )
            previous = evidence.primary_measurement_digest
        if evidence.primary_failure_digest is not None:
            expected_nodes.append(
                ("primary_failure", evidence.primary_failure_digest, (previous,))
            )
            previous = evidence.primary_failure_digest
        expected_nodes.extend(
            (
                ("runner_ledger", evidence.runner_ledger_ref.sha256, (previous,)),
                (
                    "artifact_manifest",
                    evidence.artifact_manifest_ref.sha256,
                    (evidence.runner_ledger_ref.sha256,),
                ),
            )
        )
        previous = evidence.artifact_manifest_ref.sha256
        for kind, digest in (
            ("verifier_snapshot", evidence.verifier_snapshot_digest),
            ("verifier_measurement", evidence.verifier_measurement_digest),
            ("verifier_result", evidence.verifier_result_digest),
        ):
            if digest is not None:
                expected_nodes.append((kind, digest, (previous,)))
                previous = digest
        if evidence.verifier_cleanup_receipt_ref is not None:
            expected_nodes.append(
                (
                    "verifier_cleanup",
                    evidence.verifier_cleanup_receipt_ref.sha256,
                    (previous,),
                )
            )
            previous = evidence.verifier_cleanup_receipt_ref.sha256
        if evidence.authority_access_ledger_ref is not None:
            expected_nodes.append(
                (
                    "authority_access_ledger",
                    evidence.authority_access_ledger_ref.sha256,
                    (previous,),
                )
            )
        actual_nodes = sorted(
            (node.kind, node.node_digest, node.parent_digests)
            for node in evidence.lineage_nodes
        )
        if actual_nodes != sorted(expected_nodes):
            raise EvidenceCorruptError(
                "evidence lineage fields do not match lineage nodes"
            )
        if (
            evidence.verifier_result_digest is not None
            and evidence.verifier_cleanup_receipt_ref is None
        ):
            raise EvidenceCorruptError(
                "verifier evidence lacks durable cleanup receipt"
            )
        if evidence.verifier_cleanup_receipt_ref is not None:
            if evidence.verifier_cleanup_lease_id is None:
                raise EvidenceCorruptError(
                    "verifier cleanup lacks authoritative lease identity"
                )
            verifier_cleanup = self._load_json(evidence.verifier_cleanup_receipt_ref)
            _validate_cleanup_projection(
                verifier_cleanup,
                expected_lease_id=evidence.verifier_cleanup_lease_id,
                required_resources=_VERIFIER_CLEANUP_RESOURCES,
            )
        manifest = self._load_artifact_manifest(evidence.artifact_manifest_ref)
        if any(
            obj.authorization_policy_ref != evidence.evidence_policy_ref
            or obj.retention_policy_ref != evidence.retention_policy_ref
            for obj in manifest.objects
        ):
            raise EvidenceCorruptError("artifact manifest policy binding mismatch")
        if manifest.objects and evidence.retention_policy_record_ref is None:
            raise EvidenceCorruptError(
                "exportable evidence lacks a retention policy registry record"
            )
        if evidence.retention_policy_record_ref is not None:
            self._load_retention_policy(
                envelope.episode_id,
                evidence.retention_policy_record_ref,
            )
        ledger_value = self._load_json(evidence.runner_ledger_ref)
        ledger = RunnerEventLedgerV2(
            str(ledger_value["episode_id"]),
            str(ledger_value["effective_plan_digest"]),
            tuple(ledger_value["events"]),
            str(ledger_value["runner_result_digest"]),
        )
        if (
            ledger.digest != evidence.runner_ledger_ref.sha256
            or ledger.episode_id != envelope.episode_id
            or ledger.effective_plan_digest != evidence.effective_plan_digest
        ):
            raise EvidenceCorruptError("runner ledger binding mismatch")
        refs = (
            envelope.create_response_ref,
            envelope.run_response_ref,
            envelope.completed_event_ref,
            *(obj.artifact_ref for obj in manifest.objects),
        )
        if len(refs) + 3 > self._max_traversal_objects:
            raise EvidenceCorruptError("completed graph traversal bound exceeded")
        for ref in refs:
            self._read_ref_exact(ref)

    def _read_ref_exact(self, ref: ArtifactRef) -> bytes:
        if ref.size_bytes < 0 or ref.size_bytes > self._max_object_bytes:
            raise EvidenceCorruptError("artifact size exceeds repository bound")
        try:
            payload = self._cas.get_bytes(
                ref, max_bytes=min(self._max_object_bytes, ref.size_bytes)
            )
        except ArtifactIntegrityError as exc:
            raise EvidenceCorruptError("CAS artifact integrity failure") from exc
        if len(payload) != ref.size_bytes or _digest(payload) != ref.sha256:
            raise EvidenceCorruptError(
                "artifact bytes do not match immutable reference"
            )
        return payload


_UNSAFE_SECRET_RE = re.compile(
    r"""(?ix)
    (?:https?://\S+)
    |
    (?:bearer\s+\S+)
    |
    (?:
      (?:--?)?(?:authorization|password|passwd|secret|token|credential|api[_-]?key)
      \s*(?:=|:|\s)\s*\S+
    )
    """
)


def _contains_unsafe_secret(value: str) -> bool:
    return _UNSAFE_SECRET_RE.search(value) is not None


_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|password|passwd|secret|token|credential|api[_-]?key|environment|env|argv|command)",
    re.IGNORECASE,
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?ix)"
    r"((?<![\w-])(?:"
    r"--?(?:password|passwd|secret|token|credential|api[_-]?key)"
    r"(?:\s*(?:=|:)\s*|\s+)"
    r"|(?:password|passwd|secret|token|credential|api[_-]?key)"
    r"\s*(?:=|:)\s*))"
    r"[^\s,;]+"
)
_AUTHORIZATION_VALUE_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+")
_VALUE_SECRET_RE = re.compile(
    r"(?i)((?:authorization\s*[:=]\s*)?bearer\s+|"
    r"(?:auth|password|passwd|secret|token|credential|api[_-]?key)\s*[:=]\s*)"
    r"[^\s,;]+"
)
_URL_USERINFO_RE = re.compile(
    r"(?i)(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/?#@\s]+)@"
)
_SAFE_NON_FILE_URL_RE = re.compile(
    r"""(?ix)
    \b(?!file://)[a-z][a-z0-9+.-]*://
    [^\s,;!?'"<>{}\[\]()]*
    """
)
_FILESYSTEM_ROOT_RE = re.compile(r"(?i)^(?:/|~[/\\]|[a-z]:[/\\])$")
_FILESYSTEM_PATH_RE = re.compile(
    r"""(?ix)
    (?:
        (?<![a-z0-9+.-])
        file:///
        [^\s,;!?'"<>{}\[\]()]*
        [a-z0-9_~$@%+=/\\-]
        |
        (?<![a-z0-9/\\])
        (?:
            ~[/\\]
            |[a-z]:[/\\]
            |\\\\[^\\\s,;!?'"<>{}\[\]()]+\\
            |/(?!/)
        )
        [^\s,;!?'"<>{}\[\]()]*
        [a-z0-9_~$@%+=/\\-]
    )
    """
)
_FILESYSTEM_PATH_REPLACEMENT = "[REDACTED_PATH]"


def _redact_secret_text(value: str) -> str:
    value = _URL_USERINFO_RE.sub(r"\g<scheme>", value)
    value = _ASSIGNMENT_SECRET_RE.sub(r"\1[REDACTED]", value)
    value = _AUTHORIZATION_VALUE_RE.sub(r"\1[REDACTED]", value)
    return _VALUE_SECRET_RE.sub(r"\1[REDACTED]", value)


def _redact_filesystem_paths(value: str) -> str:
    if _FILESYSTEM_ROOT_RE.fullmatch(value):
        return _FILESYSTEM_PATH_REPLACEMENT
    parts: list[str] = []
    cursor = 0
    for match in _SAFE_NON_FILE_URL_RE.finditer(value):
        parts.append(
            _FILESYSTEM_PATH_RE.sub(
                _FILESYSTEM_PATH_REPLACEMENT, value[cursor : match.start()]
            )
        )
        parts.append(match.group())
        cursor = match.end()
    parts.append(_FILESYSTEM_PATH_RE.sub(_FILESYSTEM_PATH_REPLACEMENT, value[cursor:]))
    return "".join(parts)


def _contains_filesystem_path(value: str) -> bool:
    if _FILESYSTEM_ROOT_RE.fullmatch(value):
        return True
    cursor = 0
    for match in _SAFE_NON_FILE_URL_RE.finditer(value):
        if _FILESYSTEM_PATH_RE.search(value, cursor, match.start()):
            return True
        cursor = match.end()
    return _FILESYSTEM_PATH_RE.search(value, cursor) is not None


def _contains_export_hazard(value: Any, *, sensitive: bool = False) -> bool:
    if sensitive:
        if isinstance(value, Mapping):
            return any(
                _contains_export_hazard(str(key)) or item != "[REDACTED]"
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(item != "[REDACTED]" for item in value)
        return value != "[REDACTED]"
    if isinstance(value, str):
        return (
            _URL_USERINFO_RE.search(value) is not None
            or _contains_filesystem_path(value)
            or _redact_secret_text(value) != value
        )
    if isinstance(value, Mapping):
        return any(
            _contains_export_hazard(str(key))
            or _contains_export_hazard(
                item, sensitive=bool(_SENSITIVE_KEY_RE.search(str(key)))
            )
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_export_hazard(item) for item in value)
    return False


REDACTION_TRANSFORM_ID = "breadboard.closed-json-redaction.v3"
_REDACTION_TRANSFORM_SPEC = {
    "schema_version": "bb.rl.redaction-transform.v3",
    "transform_id": REDACTION_TRANSFORM_ID,
    "sensitive_key_pattern": _SENSITIVE_KEY_RE.pattern,
    "assignment_pattern": _ASSIGNMENT_SECRET_RE.pattern,
    "authorization_pattern": _AUTHORIZATION_VALUE_RE.pattern,
    "value_pattern": _VALUE_SECRET_RE.pattern,
    "url_userinfo_pattern": _URL_USERINFO_RE.pattern,
    "url_userinfo": "replace-userinfo",
    "filesystem_path_pattern": _FILESYSTEM_PATH_RE.pattern,
    "filesystem_root_pattern": _FILESYSTEM_ROOT_RE.pattern,
    "safe_non_file_url_pattern": _SAFE_NON_FILE_URL_RE.pattern,
    "safe_non_file_url_mode": "exclude-from-filesystem-path-classification",
    "filesystem_path_mode": "replace-posix-home-drive-unc-and-file-url-including-roots",
    "filesystem_path_replacement": _FILESYSTEM_PATH_REPLACEMENT,
    "replacement": "[REDACTED]",
    "containers": "preserve-shape",
    "postcondition": "recursive-classified-hazard-scan",
    "non_json": "deny",
}
REDACTION_TRANSFORM_IMPLEMENTATION_DIGEST = canonical_digest(_REDACTION_TRANSFORM_SPEC)


@dataclass(frozen=True, slots=True)
class RedactionDecisionV2(CanonicalRecord):
    evidence_policy_ref: str
    role: str
    source_artifact_digest: str
    transform_id: str = REDACTION_TRANSFORM_ID
    transform_implementation_digest: str = REDACTION_TRANSFORM_IMPLEMENTATION_DIGEST
    schema_version: str = field(default="bb.rl.redaction-decision.v2", init=False)

    def __post_init__(self) -> None:
        _check_digest(self.evidence_policy_ref, "evidence policy ref")
        _check_digest(self.source_artifact_digest, "source artifact digest")
        if not self.role:
            raise EvidenceValidationError("redaction decision role is required")
        if (
            self.transform_id != REDACTION_TRANSFORM_ID
            or self.transform_implementation_digest
            != REDACTION_TRANSFORM_IMPLEMENTATION_DIGEST
        ):
            raise EvidenceValidationError("redaction transform implementation mismatch")


def _redact_export_value(value: Any, *, sensitive: bool = False) -> Any:
    if sensitive:
        if isinstance(value, Mapping):
            return {
                _redact_filesystem_paths(_redact_secret_text(str(key))): "[REDACTED]"
                for key in value
            }
        if isinstance(value, (list, tuple)):
            return ["[REDACTED]" for _ in value]
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            _redact_filesystem_paths(_redact_secret_text(str(key))): (
                _redact_export_value(
                    item, sensitive=bool(_SENSITIVE_KEY_RE.search(str(key)))
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_export_value(item) for item in value]
    if isinstance(value, str):
        return _redact_filesystem_paths(_redact_secret_text(value))
    return value


def _redacted_evidence_object(value: EvidenceObjectV2) -> EvidenceObjectV2:
    ref = value.artifact_ref
    redacted_ref = ArtifactRef(
        artifact_id=ref.artifact_id,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
        metadata=_redact_export_value(_json_value(ref.metadata)),
    )
    return EvidenceObjectV2(
        value.role,
        value.producer,
        redacted_ref,
        value.authorization_policy_ref,
        value.retention_policy_ref,
        value.parent_digests,
    )


def _failure_from(value: Any) -> SafeFailureFactV2 | None:
    if value is None:
        return None
    return SafeFailureFactV2(
        str(value["category"]),
        str(value["code"]),
        str(value["retry_disposition"]),
        str(value["side_effect_boundary"]),
        value.get("turn"),
        value.get("call_id"),
        value.get("lease_id"),
        value.get("detail"),
    )


def _locator_from_json(data: bytes) -> EpisodeLocatorRecordV2:
    try:
        value = json.loads(data)
        if canonical_json_bytes(value) != data:
            raise EvidenceCorruptError("locator is not canonically encoded")
        return EpisodeLocatorRecordV2(
            str(value["episode_id"]),
            int(value["generation"]),
            str(value["current_state"]),
            str(value["latest_event_head"]),
            _ref_from(value["latest_event_ref"]),
            _ref_from(value["completed_tombstone_ref"])
            if value.get("completed_tombstone_ref")
            else None,
            _ref_from(value["closed_tombstone_ref"])
            if value.get("closed_tombstone_ref")
            else None,
            _ref_from(value["quarantine_ref"]) if value.get("quarantine_ref") else None,
            tuple(_ref_from(ref) for ref in value.get("runner_event_refs", ())),
            value.get("runner_event_head"),
            value.get("runner_effective_plan_digest"),
            str(value["checksum"]),
        )
    except EvidenceError:
        raise
    except Exception as exc:
        raise EvidenceCorruptError("invalid locator record") from exc


def _validate_cleanup_projection(
    receipt: Mapping[str, Any],
    *,
    expected_lease_id: str | None = None,
    required_resources: Sequence[str] = _PRIMARY_CLEANUP_RESOURCES,
) -> None:
    if set(receipt) != {"lease_id", "steps", "state"}:
        raise EvidenceValidationError(
            "cleanup receipt projection has unexpected fields"
        )
    lease_id = receipt.get("lease_id")
    if (
        not isinstance(lease_id, str)
        or not lease_id
        or (expected_lease_id is not None and lease_id != expected_lease_id)
    ):
        raise EvidenceValidationError("cleanup receipt lease binding mismatch")
    state = str(receipt.get("state", "")).lower()
    if state not in {CleanupState.RELEASED.value, CleanupState.ALREADY_RELEASED.value}:
        raise EvidenceValidationError("cleanup receipt is not authoritatively released")
    steps = receipt.get("steps")
    if not isinstance(steps, list) or not steps:
        raise EvidenceValidationError("cleanup receipt requires detailed steps")
    resources: list[str] = []
    for step in steps:
        if not isinstance(step, Mapping) or set(step) != {
            "resource",
            "state",
            "detail",
        }:
            raise EvidenceValidationError("cleanup step projection is invalid")
        resource = step.get("resource")
        step_state = str(step.get("state", "")).lower()
        if (
            not isinstance(resource, str)
            or not resource
            or step_state
            not in {CleanupState.RELEASED.value, CleanupState.ALREADY_RELEASED.value}
        ):
            raise EvidenceValidationError(
                "cleanup receipt contains an invalid or unreleased step"
            )
        resources.append(resource)
    expected = tuple(required_resources)
    if (
        len(expected) != len(set(expected))
        or len(resources) != len(set(resources))
        or set(resources) != set(expected)
    ):
        raise EvidenceValidationError(
            "cleanup receipt resource set is incomplete or ambiguous"
        )


__all__ = [
    "ArtifactManifestV2",
    "ClosedEpisodeEnvelopeV2",
    "ClosedPublicationInputsV2",
    "ClosedPublicationV2",
    "CompletedEpisodeEnvelopeV2",
    "CompletedPublicationInputsV2",
    "CompletedPublicationV2",
    "EpisodeClosedTombstoneV2",
    "EpisodeCompletedTombstoneV2",
    "EpisodeEvidenceRepository",
    "EpisodeLocatorRecordV2",
    "EpisodeLocatorStore",
    "EvidenceCorruptError",
    "EvidenceError",
    "EvidenceObjectV2",
    "EvidenceValidationError",
    "ExecutionEvidenceManifestV2",
    "ExportAuthorizationV2",
    "ExportDeniedError",
    "ExportManifestV2",
    "FailedCompletedPublicationInputsV2",
    "FilesystemEpisodeLocatorStore",
    "InMemoryEpisodeLocatorStore",
    "LifecycleEventV2",
    "LineageNodeV2",
    "LocatorConflictError",
    "QuarantinePublicationInputsV2",
    "QuarantinePublicationV2",
    "RecoveredEpisodeV2",
    "RunnerEventLedgerV2",
    "RunnerEventPublicationV2",
    "SafeFailureFactV2",
    "canonical_digest",
    "validate_lineage",
]
