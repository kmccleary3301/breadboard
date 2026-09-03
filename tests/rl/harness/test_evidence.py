from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing
import os
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as harness_contracts
from breadboard.rl.harness import evidence as evidence_module
from breadboard.rl.harness.evidence import (
    ArtifactManifestV2,
    ClosedEpisodeEnvelopeV2,
    ClosedPublicationInputsV2,
    CompletedEpisodeEnvelopeV2,
    CompletedPublicationInputsV2,
    EpisodeEvidenceRepository,
    EpisodeClosedTombstoneV2,
    EpisodeCompletedTombstoneV2,
    EpisodeLocatorRecordV2,
    EvidenceCorruptError,
    EvidenceObjectV2,
    EvidenceAuthorityPlanV2,
    EvidenceObjectInputV2,
    EvidenceRoleBindingV2,
    EvidenceRoleSourceV2,
    EvidenceValidationError,
    ExecutionEvidenceManifestV2,
    ExportAuthorizationV2,
    ExportAuthorizationClaimsV2,
    ExportDeniedError,
    FailedCompletedPublicationInputsV2,
    ExportManifestV2,
    FilesystemEpisodeLocatorStore,
    InMemoryEpisodeLocatorStore,
    LifecycleEventV2,
    LineageNodeV2,
    LocatorConflictError,
    QuarantinePublicationInputsV2,
    RedactionDecisionV2,
    RunnerEventLedgerV2,
    SafeFailureFactV2,
    V2EvidenceAuthority,
    canonical_digest,
    validate_lineage,
)
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    SandboxCleanupReceipt,
    VerifierSnapshotReceipt,
)
from breadboard.rl.harness.runners.base import (
    RunnerResult,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerTurn,
)
from breadboard.artifacts.cas import InMemoryCAS
from breadboard.artifacts.references import ArtifactRef

EPISODE = "evidence-episode"
MEDIA_TYPE = "application/vnd.breadboard.evidence+json"
CASE_INVENTORY = (
    "canonical-goldens-and-immutability",
    "event-chain-replay",
    "runner-ledger-exact-identity",
    "artifact-role-each-total-limits",
    "dag-normalization-and-rejections",
    "required-identity-mutation-matrix",
    "completed-is-not-closed",
    "closed-requires-detailed-release",
    "no-forward-or-locator-lineage",
    "locator-memory-filesystem-cas-races",
    "locator-file-directory-fsync-retry",
    "locator-symlink-ancestor-swap",
    "recover-corrupt-checksum-missing-blob-digest-mismatch",
    "publish-recover-export-secret-redaction-policy-retention-role-gates",
    "expired-retention-denies-without-deletion-claim",
    "cleanup-receipt-spoof-matrix",
    "closed-export-authorization-and-redaction-pins",
    "cross-episode-and-linkage-graft-matrix",
    "explicit-zero-artifact-limits",
    "recovery-verifies-ledger-and-every-artifact",
    "artifact-locator-export-redaction",
    "safe-failure-detail-credential-matrix",
    "legal-post-completed-locator-generations",
    "verifier-cleanup-completed-crash-recovery",
    "semantic-lineage-field-node-binding",
    "cleanup-required-resource-contract-not-caller-narrowable",
    "closed-completed-fingerprint-response-linkage",
    "runner-event-journal-append-recover",
    "failed-completed-no-run-verifier-cleanup-failure",
    "binary-export-fails-closed",
    "failed-verifier-evidence-unadmitted-until-released",
    "safe-export-metadata-preserved",
    "failed-no-verifier-primary-release-closes",
    "foreign-completed-aggregate-victim-ref-rejected",
    "embedded-auth-string-export-redaction",
    "quarantine-object-integrity-and-identity",
    "closed-locator-and-transition-absorbing",
    "completed-publication-idempotency-and-conflict",
    "canonical-primary-lease-close-binding",
    "authorization-scheme-complete-redaction",
    "durable-runner-journal-result-conflict",
    "verifier-cleanup-lease-close-binding",
    "malformed-export-pins-fail-closed",
    "primary-disposition-graph-consistency",
    "authoritative-verifier-lease-completion-binding",
    "exact-selection-commit-token-binding",
    "secret-credential-assignment-redaction-boundaries",
    "recovered-verifier-cleanup-proof",
    "public-completed-evidence-projections",
    "typed-evidence-authority-materialization",
    "evidence-object-publication-authority-limits",
    "illegal-lifecycle-state-from-edge-kind-replay-matrix",
    "atomic-close-event-envelope-tombstone-locator-failure-matrix",
    "locator-valid-corrupt-valid-reuse-blocked",
    "per-role-export-pin-roundtrip-mismatch-redaction",
    "cancel-fields-canonical-sticky-tamper-fingerprint",
    "production-retention-window-boundaries-and-pins",
    "stable-claims-unique-pinned-window-export",
    "scheme-independent-url-userinfo-redaction-fail-closed",
)
CASE_INVENTORY_SHA256 = (
    "772d986b21ce5863b91420316f022c6e230065a16a88828dc0ed007f0e0f9d01"
)


def _d(seed: str) -> str:
    return "sha256:" + seed * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", 1),
        ("source", "runner_result"),
        ("producer_id", 1),
        ("producer_implementation_digest", 1),
    ],
)
def test_evidence_role_binding_requires_exact_runtime_types(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "role": "terminal-result",
        "source": EvidenceRoleSourceV2.VERIFIER_RESULT,
        "producer_id": "exact-output",
        "producer_implementation_digest": _d("1"),
    }
    values[field] = value
    with pytest.raises(EvidenceValidationError):
        EvidenceRoleBindingV2(**values)  # type: ignore[arg-type]


def _ref(payload: bytes, artifact_id: str = "fixture") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/json",
        metadata={},
    )


def _event(
    sequence: int = 0,
    *,
    episode_id: str = EPISODE,
    previous: LifecycleEventV2 | None = None,
    previous_ref: ArtifactRef | None = None,
    to_state: str = "accepted",
    event_kind: str | None = None,
    primary_lease_id: str | None = None,
    cancel_reason: str | None = None,
    cancel_fingerprint: str | None = None,
) -> LifecycleEventV2:
    return LifecycleEventV2(
        episode_id=episode_id,
        sequence=sequence,
        previous_event_digest=previous.digest if previous else None,
        from_state=previous.to_state if previous else None,
        to_state=to_state,
        event_kind=event_kind
        or ("accepted" if sequence == 0 else f"transition-{sequence}"),
        observed_at=f"2026-01-01T00:00:0{sequence}Z",
        create_fingerprint=previous.create_fingerprint if previous else _d("a"),
        run_fingerprint=previous.run_fingerprint if previous else _d("b"),
        effective_plan_digest=previous.effective_plan_digest if previous else _d("c"),
        fact_refs=(previous_ref,) if previous_ref else (),
        fact_digests=(_d("d"),),
        primary_lease_id=primary_lease_id
        if primary_lease_id is not None
        else (previous.primary_lease_id if previous is not None else None),
        cancel_reason=cancel_reason,
        cancel_fingerprint=cancel_fingerprint,
    )


def _append_success_lifecycle(
    repository: EpisodeEvidenceRepository,
    *,
    episode_id: str = EPISODE,
    primary_lease_id: str = "lease-7",
    run_fingerprint: str | None = None,
) -> tuple[LifecycleEventV2, ArtifactRef]:
    states = (
        ("accepted", "accepted"),
        ("allocating", "allocation_started"),
        ("ready", "workspace_ready"),
        ("running", "run_started"),
        ("verifying", "runner_terminal"),
        ("completed", "completed"),
    )
    previous: LifecycleEventV2 | None = None
    previous_ref: ArtifactRef | None = None
    for sequence, (state, kind) in enumerate(states):
        event = _event(
            sequence,
            episode_id=episode_id,
            previous=previous,
            previous_ref=previous_ref,
            to_state=state,
            event_kind=kind,
            primary_lease_id=primary_lease_id if sequence >= 2 else None,
        )
        if sequence < 3:
            event = dataclasses.replace(event, run_fingerprint=None)
        elif run_fingerprint is not None:
            event = dataclasses.replace(event, run_fingerprint=run_fingerprint)
        previous_ref = repository.append_transition(event)
        previous = event
    assert previous is not None and previous_ref is not None
    return previous, previous_ref


def _append_failure_lifecycle(
    repository: EpisodeEvidenceRepository,
    *,
    episode_id: str = EPISODE,
    primary_lease_id: str = "lease-7",
    run_fingerprint: str | None = None,
) -> tuple[LifecycleEventV2, ArtifactRef]:
    states = (
        ("accepted", "accepted"),
        ("allocating", "allocation_started"),
        ("ready", "workspace_ready"),
        ("closing", "process_interrupted"),
    )
    previous: LifecycleEventV2 | None = None
    previous_ref: ArtifactRef | None = None
    for sequence, (state, kind) in enumerate(states):
        event = _event(
            sequence,
            episode_id=episode_id,
            previous=previous,
            previous_ref=previous_ref,
            to_state=state,
            event_kind=kind,
            primary_lease_id=primary_lease_id if sequence >= 2 else None,
        )
        if sequence < 3:
            event = dataclasses.replace(event, run_fingerprint=None)
        elif run_fingerprint is not None:
            event = dataclasses.replace(event, run_fingerprint=run_fingerprint)
        previous_ref = repository.append_transition(event)
        previous = event
    assert previous is not None and previous_ref is not None
    return previous, previous_ref


def _runner_result(
    *, episode_id: str = EPISODE, plan_digest: str = _d("c")
) -> RunnerResult:
    event = RunnerTerminationEvent(
        sequence=0,
        episode_id=episode_id,
        effective_plan_digest=plan_digest,
        turns=1,
        reason=RunnerTermination.ASSISTANT_COMPLETE,
    )
    return RunnerResult(
        episode_id=episode_id,
        effective_plan_digest=plan_digest,
        original_request={"task": "deterministic"},
        response={"answer": "done"},
        termination=RunnerTermination.ASSISTANT_COMPLETE,
        turn_count=1,
        turns=(RunnerTurn(1, ({"type": "message", "text": "done"},)),),
        events=(event,),
    )


def _ledger(result: RunnerResult | None = None) -> RunnerEventLedgerV2:
    result = result or _runner_result()
    return RunnerEventLedgerV2(
        episode_id=result.episode_id,
        effective_plan_digest=result.effective_plan_digest,
        events=result.events,
        runner_result_digest=canonical_digest(result),
    )


def _object(
    role: str = "trajectory",
    *,
    payload: bytes = b'{"answer":"done"}',
    metadata: dict[str, Any] | None = None,
    parents: tuple[str, ...] = (),
) -> EvidenceObjectV2:
    ref = ArtifactRef(
        artifact_id=f"objects/{role}",
        sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/json",
        metadata=metadata or {},
    )
    return EvidenceObjectV2(
        role,
        "deterministic-fixture",
        ref,
        _d("c"),
        _d("d"),
        parents,
    )


def _store_schema(
    cas: InMemoryCAS, episode_id: str, suffix: str, value: Any
) -> ArtifactRef:
    payload = value.canonical_bytes()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return cas.put_bytes(
        payload,
        artifact_id=f"v2/{episode_id}/{suffix}/{digest[7:]}",
        media_type=MEDIA_TYPE,
        metadata={"schema": value.schema_version, "episode_id": episode_id},
    )


def _manifest(
    ledger_ref: ArtifactRef, artifact_ref: ArtifactRef, **changes: Any
) -> ExecutionEvidenceManifestV2:
    values: dict[str, Any] = {
        "episode_id": EPISODE,
        "resolved_plan_digest": _d("1"),
        "selection_digest": _d("2"),
        "effective_plan_digest": _d("3"),
        "policy_binding_digest": _d("4"),
        "runner_ledger_ref": ledger_ref,
        "materialization_digest": _d("5"),
        "primary_measurement_digest": _d("6"),
        "verifier_snapshot_digest": _d("7"),
        "verifier_measurement_digest": _d("a"),
        "verifier_result_digest": _d("b"),
        "artifact_manifest_ref": artifact_ref,
        "primary_disposition": "succeeded",
        "reward_disposition": "eligible",
        "reward_components": {"correct": 1},
        "evidence_policy_ref": _d("c"),
        "retention_policy_ref": _d("d"),
        "lineage_nodes": (
            LineageNodeV2(_d("1"), "resolved_plan", "breadboard"),
            LineageNodeV2(_d("2"), "selection", "breadboard", (_d("1"),)),
            LineageNodeV2(_d("3"), "effective_plan", "breadboard", (_d("2"),)),
            LineageNodeV2(_d("4"), "policy_binding", "breadboard", (_d("3"),)),
            LineageNodeV2(_d("5"), "materialization", "breadboard", (_d("4"),)),
            LineageNodeV2(_d("6"), "primary_measurement", "breadboard", (_d("5"),)),
            LineageNodeV2(ledger_ref.sha256, "runner_ledger", "breadboard", (_d("6"),)),
            LineageNodeV2(
                artifact_ref.sha256,
                "artifact_manifest",
                "breadboard",
                (ledger_ref.sha256,),
            ),
            LineageNodeV2(
                _d("7"), "verifier_snapshot", "breadboard", (artifact_ref.sha256,)
            ),
            LineageNodeV2(_d("a"), "verifier_measurement", "breadboard", (_d("7"),)),
            LineageNodeV2(_d("b"), "verifier_result", "breadboard", (_d("a"),)),
        ),
        "lineage_root": _d("b"),
    }
    values.update(changes)
    return ExecutionEvidenceManifestV2(**values)


def _publish_completed(
    cas: InMemoryCAS | None = None,
    locators: InMemoryEpisodeLocatorStore | None = None,
    *,
    episode_id: str = EPISODE,
    artifact_object: EvidenceObjectV2 | None = None,
    artifact_objects: tuple[EvidenceObjectV2, ...] | None = None,
    max_each_bytes: int = 4096,
    max_total_bytes: int = 4096,
    artifact_payload: bytes = b'{"answer":"done"}',
    lifecycle_event: LifecycleEventV2 | None = None,
    runner_result: RunnerResult | None = None,
    durable_runner_events: tuple[RunnerTerminationEvent, ...] = (),
    verifier_lease_id: str = "verifier-lease-7",
    selection_commit: Any | None = None,
    retention_minimum_seconds: int = 17,
    retention_maximum_seconds: int = 86_400,
    clock: Any | None = None,
) -> tuple[EpisodeEvidenceRepository, InMemoryCAS, Any, LifecycleEventV2]:
    cas = cas if cas is not None else InMemoryCAS()
    locators = locators if locators is not None else InMemoryEpisodeLocatorStore()
    repository = EpisodeEvidenceRepository(
        cas,
        locators,
        clock=clock or (lambda: datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)),
    )
    retention_record = harness_contracts.RetentionPolicyRegistryRecord(
        grant=harness_contracts.RetentionPolicyGrant(
            policy=harness_contracts.PolicyRef(
                policy_id="retention-policy",
                revision_digest=_d("d"),
            ),
            minimum_seconds=retention_minimum_seconds,
            maximum_seconds=retention_maximum_seconds,
        )
    )
    retention_policy_ref = canonical_digest(retention_record)
    objects = tuple(
        dataclasses.replace(item, retention_policy_ref=retention_policy_ref)
        for item in (artifact_objects or (artifact_object or _object(),))
    )
    fixture_payload = artifact_payload
    for obj in objects:
        assert obj.artifact_ref.sha256 == _ref(fixture_payload).sha256
        cas.put_bytes(
            fixture_payload,
            artifact_id=obj.artifact_ref.artifact_id,
            media_type=obj.artifact_ref.media_type,
            metadata=dict(obj.artifact_ref.metadata),
        )
    event, event_ref = _append_success_lifecycle(
        repository,
        episode_id=episode_id,
        primary_lease_id=(
            lifecycle_event.primary_lease_id
            if lifecycle_event is not None
            else "lease-7"
        ),
    )
    result = runner_result or _runner_result(episode_id=episode_id, plan_digest=_d("3"))
    for runner_event in durable_runner_events or result.events:
        repository.append_runner_event(episode_id, _d("3"), runner_event)
    resolved_plan = {
        "schema_version": "bb.rl.resolved-episode-plan.v2",
        "selection_digest": _d("2"),
        "effective_plan_digest": _d("3"),
        "effective_plan": {
            "effective_plan_digest": _d("3"),
            "artifacts": {
                "allowed_roles": [obj.role for obj in objects],
                "max_each_bytes": max_each_bytes,
                "max_total_bytes": max_total_bytes,
            },
        },
    }
    if selection_commit is not None:
        resolved_plan.pop("selection_digest")
        resolved_plan["selection_commit"] = selection_commit
    publication = repository.publish_completed(
        CompletedPublicationInputsV2(
            episode_id=episode_id,
            create_fingerprint=_d("a"),
            run_fingerprint=_d("b"),
            create_response_bytes=b'{"created":true}',
            run_response_bytes=b'{"answer":"done"}',
            resolved_plan=resolved_plan,
            policy_binding_digest=_d("4"),
            runner_result=result,
            materialization_receipt={"receipt": _d("5")},
            primary_measurement={"measurement": _d("6")},
            verifier_snapshot={"snapshot": _d("7")},
            verifier_measurement_digest=_d("a"),
            verifier_result={"result": _d("b")},
            evidence_objects=objects,
            evidence_policy={
                "record_digest": _d("c"),
                "required_roles": ["trajectory"],
            },
            retention_policy=retention_record,
            lifecycle_head_ref=event_ref,
            lifecycle_head_digest=event.digest,
            primary_disposition="succeeded",
            reward_disposition="eligible",
            reward_components={"correct": 1},
            verifier_cleanup_receipt=_verifier_released_receipt(),
            verifier_lease_id=verifier_lease_id,
        )
    )
    return repository, cas, publication, event


def _released_receipt() -> SandboxCleanupReceipt:
    return SandboxCleanupReceipt.from_steps(
        "lease-7",
        (
            CleanupStepReceipt("child_verifier", CleanupState.RELEASED),
            CleanupStepReceipt("runtime", CleanupState.ALREADY_RELEASED),
            CleanupStepReceipt("workspace", CleanupState.RELEASED),
            CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
            CleanupStepReceipt("lease_record", CleanupState.RELEASED),
        ),
    )


def _verifier_released_receipt() -> SandboxCleanupReceipt:
    return SandboxCleanupReceipt.from_steps(
        "verifier-lease-7",
        tuple(
            CleanupStepReceipt(resource, CleanupState.RELEASED)
            for resource in ("runtime", "workspace", "snapshot", "lease_record")
        ),
    )


def _cleanup_projection(receipt: SandboxCleanupReceipt) -> dict[str, Any]:
    return {
        "lease_id": receipt.lease_id,
        "steps": [
            {
                "resource": step.resource,
                "state": step.state.value,
                "detail": step.detail,
            }
            for step in receipt.steps
        ],
        "state": receipt.state.value,
    }


def _export_authorization(
    repository: EpisodeEvidenceRepository | None = None,
    closed: Any | None = None,
    **changes: Any,
) -> ExportAuthorizationV2:
    if repository is not None and closed is not None:
        authorization = repository._load_export_authorization(
            closed.envelope.export_authorization_refs[0]
        )
        return dataclasses.replace(authorization, **changes)
    redaction_digest = RedactionDecisionV2(
        evidence_policy_ref=_d("c"),
        role="trajectory",
        source_artifact_digest=_object().artifact_ref.sha256,
    ).digest
    values: dict[str, Any] = {
        "subject": _d("e"),
        "scope": "episode_export",
        "evidence_policy_ref": _d("c"),
        "retention_policy_ref": _d("d"),
        "allowed_roles": ("trajectory",),
        "redaction_decision_digest": redaction_digest,
    }
    values.update(changes)
    return ExportAuthorizationV2(**values)


def _export_claims(
    authorization: ExportAuthorizationV2, **changes: Any
) -> ExportAuthorizationClaimsV2:
    values: dict[str, Any] = {
        "subject_digest": authorization.subject,
        "scope": authorization.scope,
        "evidence_policy_ref": authorization.evidence_policy_ref,
        "retention_policy_ref": authorization.retention_policy_ref,
        "allowed_roles": authorization.allowed_roles,
        "redaction_decision_digest": authorization.redaction_decision_digest,
    }
    values.update(changes)
    return ExportAuthorizationClaimsV2(**values)


def _prepare_closed_event(
    repository: EpisodeEvidenceRepository,
    completed: Any,
    event: LifecycleEventV2,
) -> LifecycleEventV2:
    closing_event = _event(
        event.sequence + 1,
        previous=event,
        previous_ref=completed.locator.latest_event_ref,
        to_state="closing",
        event_kind="cleanup_started",
    )
    closing_ref = repository.append_transition(closing_event)
    return _event(
        closing_event.sequence + 1,
        previous=closing_event,
        previous_ref=closing_ref,
        to_state="closed",
        event_kind="closed",
    )


def _publish_closed(
    *,
    artifact_object: EvidenceObjectV2 | None = None,
    artifact_objects: tuple[EvidenceObjectV2, ...] | None = None,
    pin_authorization: bool = True,
    redaction_decision: str = "recursive-safe",
    artifact_payload: bytes = b'{"answer":"done"}',
    cas: InMemoryCAS | None = None,
    locators: Any | None = None,
    retention_minimum_seconds: int = 17,
    retention_maximum_seconds: int = 86_400,
    clock: Any | None = None,
) -> tuple[EpisodeEvidenceRepository, InMemoryCAS, Any]:
    repository, cas, completed, event = _publish_completed(
        cas,
        locators,
        artifact_object=artifact_object,
        artifact_objects=artifact_objects,
        artifact_payload=artifact_payload,
        retention_minimum_seconds=retention_minimum_seconds,
        retention_maximum_seconds=retention_maximum_seconds,
        clock=clock,
    )
    pins = repository.prepare_export_pins(
        EPISODE,
        completed,
        subject_digest=_d("e"),
    )
    closed_event = _prepare_closed_event(repository, completed, event)
    authorization_refs = pins.authorization_refs if pin_authorization else ()
    redaction_refs = pins.redaction_decision_refs
    if redaction_decision != "recursive-safe":
        redaction_refs = (_ref(b"different-redaction", "different-redaction"),)
    closed = repository.publish_closed(
        ClosedPublicationInputsV2(
            episode_id=EPISODE,
            completed=completed,
            cleanup_receipt=_released_receipt(),
            closed_event=closed_event,
            final_primary_outcome="succeeded",
            cleanup_lease_id="lease-7",
            cleanup_required_resources=(
                "child_verifier",
                "runtime",
                "workspace",
                "cache_holder",
                "lease_record",
            ),
            verifier_cleanup_receipt=_verifier_released_receipt(),
            verifier_cleanup_lease_id="verifier-lease-7",
            verifier_cleanup_required_resources=(
                "runtime",
                "workspace",
                "snapshot",
                "lease_record",
            ),
            export_authorization_refs=authorization_refs,
            redaction_decision_refs=redaction_refs,
        )
    )
    return repository, cas, closed


def _locator(
    episode_id: str, generation: int = 1, *, state: str = "accepted"
) -> EpisodeLocatorRecordV2:
    payload = f"{episode_id}-{generation}".encode()
    return EpisodeLocatorRecordV2(
        episode_id,
        generation,
        state,
        _d("e"),
        _ref(payload, f"events/{episode_id}/{generation}"),
    )


def _process_locator_race(root: str, episode_id: str, gate: Any, output: Any) -> None:
    store = FilesystemEpisodeLocatorStore(root)
    gate.wait()
    try:
        store.compare_and_swap(episode_id, None, _locator(episode_id))
    except LocatorConflictError:
        output.put("conflict")
    else:
        output.put("won")


def test_case_inventory_is_exact_and_stable() -> None:
    assert (
        hashlib.sha256(canonical_json_bytes(list(CASE_INVENTORY))).hexdigest()
        == CASE_INVENTORY_SHA256
    )


def test_canonical_golden_bytes_digests_and_immutability_for_every_schema() -> None:
    failure = SafeFailureFactV2(
        "runner", "failed", "never", "after-open", 1, "call-1", "lease-7", "safe"
    )
    event = _event()
    ledger = _ledger()
    obj = _object()
    artifact_manifest = ArtifactManifestV2(
        (obj,), ("trajectory",), 4096, 4096, ("trajectory",)
    )
    manifest = _manifest(
        _ref(ledger.canonical_bytes(), "ledger"),
        _ref(artifact_manifest.canonical_bytes(), "artifacts"),
    )
    completed = CompletedEpisodeEnvelopeV2(
        EPISODE,
        _d("a"),
        _d("b"),
        _ref(b"c", "create"),
        _ref(b"r", "run"),
        _ref(manifest.canonical_bytes(), "evidence"),
        manifest.lineage_root,
        "succeeded",
        _ref(event.canonical_bytes(), "event"),
        event.digest,
    )
    cleanup_receipt = SandboxCleanupReceipt.from_steps(
        "lease-7",
        tuple(
            CleanupStepReceipt(resource, CleanupState.RELEASED)
            for resource in (
                "child_verifier",
                "runtime",
                "workspace",
                "cache_holder",
                "lease_record",
            )
        ),
    )
    cleanup = _cleanup_projection(cleanup_receipt)
    closed = ClosedEpisodeEnvelopeV2(
        episode_id=EPISODE,
        completed_envelope_ref=_ref(completed.canonical_bytes(), "completed"),
        cleanup_receipt_digest=canonical_digest(cleanup),
        cleanup_receipt=cleanup,
        reconciliation_event_ref=_ref(b"closed-event", "event-closed"),
        reconciliation_event_head=_d("f"),
        primary_outcome="succeeded",
    )
    completed_tombstone = EpisodeCompletedTombstoneV2(
        EPISODE,
        _d("a"),
        _d("b"),
        event.digest,
        _ref(b"r", "run"),
        _ref(completed.canonical_bytes(), "completed"),
        2,
    )
    closed_tombstone = EpisodeClosedTombstoneV2(
        EPISODE,
        _d("a"),
        _d("b"),
        _d("f"),
        _ref(b"r", "run"),
        _ref(completed_tombstone.canonical_bytes(), "completed-tombstone"),
        _ref(closed.canonical_bytes(), "closed"),
        3,
    )
    locator = _locator(EPISODE)
    authorization = ExportAuthorizationV2(
        "trainer", "training", _d("c"), _d("d"), ("trajectory",), _d("7")
    )
    export = ExportManifestV2(
        EPISODE,
        _ref(closed.canonical_bytes(), "closed"),
        authorization.digest,
        _d("c"),
        _d("d"),
        ("trajectory",),
        _d("7"),
        (),
        (obj,),
    )
    records = (
        failure,
        event,
        ledger,
        obj,
        artifact_manifest,
        *manifest.lineage_nodes,
        manifest,
        completed,
        closed,
        completed_tombstone,
        closed_tombstone,
        locator,
        authorization,
        export,
    )

    expected_schemas = (
        "bb.rl.safe-failure.v2",
        "bb.rl.lifecycle-event.v2",
        "bb.rl.runner-event-ledger.v2",
        "bb.rl.evidence-object.v2",
        "bb.rl.artifact-manifest.v2",
        *("bb.rl.lineage-node.v2" for _ in manifest.lineage_nodes),
        "bb.rl.execution-evidence-manifest.v2",
        "bb.rl.completed-episode-envelope.v2",
        "bb.rl.closed-episode-envelope.v2",
        "bb.rl.completed-tombstone.v2",
        "bb.rl.closed-tombstone.v2",
        "bb.rl.episode-locator.v2",
        "bb.rl.export-authorization.v2",
        "bb.rl.export-manifest.v2",
    )
    assert tuple(record.schema_version for record in records) == expected_schemas
    expected_digests = (
        "sha256:9e32ca8dab2cbbdb0a205a7608d24abd574d6c4601937c4a63d47429a2f74494",
        "sha256:eb555d22c906b94f8d06038e1d54bd3402274325b1c05a7886c852bcf158eaa8",
        "sha256:bfcfe231031bf76965dcaa625f6fad9cdae3f26894a239fbf52d0b425b902774",
        "sha256:5df66b2ef60e630ba890898fc8c0b046a472f414f7f1f54a6c0826908745c3a2",
        "sha256:a25af9b1233514b3c4eaf4fe69726bfc176312eaa9446e435060897c7be8efef",
        "sha256:7a732f3be9d2a7534b714791c8bd2e9e74e545045fd61f7be28bb6bbb3c7d0fc",
        "sha256:33162b7001202751bb31b818db6e3fe5df088b2ceb0b01157ae0bdd480094ce7",
        "sha256:99e75b9795a8ae0044b46c13a423997902da5ba90c8938f52440db751fd5854f",
        "sha256:c0479d3fc8b3236e3571d37ba3e3f46fde4157d9fe8eef6fa715449d30bf4836",
        "sha256:d9eb4ec6a5f7f9fb43891eee1007ddeeeebb78a86abdcd3fb3262bcc6e4895ef",
        "sha256:fc570434b4c099c39fe90ea7c09e0af3562ce39b22220ead2dde9d77fce9df2f",
        "sha256:4e0fe8aa1d263d63135c8bb44dfd1d463ea782378311ec0ce14102f87645113c",
        "sha256:d22782b4c8bda98fe1ac496c58e856bb47f00688510a9b18ee7c3b8fc13ab5eb",
        "sha256:f20928606864a9f3ff052a81842a06ec85f49ee64fc4acd8b7271d5a63fd4aa9",
        "sha256:146182b284a83d764af0f0b8ea1db95956876101e25663137f0e92a7d68c5cf7",
        "sha256:ed7e5bcc0fed112df055238150baa0318e60d5b328e984bc246b0d46ac3c5172",
        "sha256:0c6a29927961b14781e368e59dca0b0d4a761b42fe69651dcbe48181d23c425a",
        "sha256:1edcb67ef4aabd61de45a0e23d605d898067c61bce9ee234c47cf040e352623b",
        "sha256:7feb2bc36be7c5842420c503a2e9079cdd6d2d2a748df642fdee6abade95c08f",
        "sha256:3809bfbac574ce153e508bce3f0725c23fed52998d8b1c27e713c66b9dada8be",
        "sha256:664c4204340b62f71df5195790d7bf7830c7b0390471e057e5fa8ecce736f91a",
        "sha256:e79cca503dcb2b2155f2495bdbbe090285c185a74884fc2df5d8b8dc5e6f4c93",
        "sha256:83a3b38b3ff9291a02757ab19fd302ed3d48774eab467e6b85eaa42fdfc3f1f9",
        "sha256:422cff21ab0a50148620b69c68aa0a95b10487bbdfe0ae4a899e3f0f11e77c14",
    )
    assert tuple(record.digest for record in records) == expected_digests
    for record in records:
        golden = canonical_json_bytes(record.to_canonical_obj())
        assert record.canonical_bytes() == golden
        assert record.digest == "sha256:" + hashlib.sha256(golden).hexdigest()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            record.schema_version = "mutated"  # type: ignore[misc]
    assert event.canonical_bytes() == canonical_json_bytes(event.to_canonical_obj())
    assert (
        event.digest == "sha256:" + hashlib.sha256(event.canonical_bytes()).hexdigest()
    )


def test_cancel_fields_have_canonical_golden_bytes_and_sticky_restart_continuity() -> (
    None
):
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    repository = EpisodeEvidenceRepository(cas, locators)
    accepted = dataclasses.replace(_event(), run_fingerprint=None)
    accepted_ref = repository.append_transition(accepted)
    reason = "operator requested"
    fingerprint = canonical_digest(
        {
            "schema_version": "bb.rl.episode-cancel-fingerprint.v1",
            "episode_id": EPISODE,
            "create_fingerprint": _d("a"),
            "reason": reason,
        }
    )
    golden_cancellation = _event(
        1,
        previous=accepted,
        previous_ref=None,
        to_state="cancel_requested",
        event_kind="cancellation_requested",
        cancel_reason=reason,
        cancel_fingerprint=fingerprint,
    )
    expected_bytes = (
        b'{"cancel_fingerprint":"sha256:b943f0715c49e033d315af557f1f441d7f8a993ae549d6b6502628ddb3c6dfe8",'
        b'"cancel_reason":"operator requested","cleanup_fact":null,'
        b'"create_fingerprint":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"effective_plan_digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        b'"episode_id":"evidence-episode","event_kind":"cancellation_requested",'
        b'"fact_digests":["sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"],'
        b'"fact_refs":[],"from_state":"accepted","observed_at":"2026-01-01T00:00:01Z",'
        b'"previous_event_digest":"sha256:2e152c60e18992c44ed36e10a63f66a3b08e949a0167c94d376489344d00fb96",'
        b'"primary_fact":null,"primary_lease_id":null,"run_fingerprint":null,'
        b'"schema_version":"bb.rl.lifecycle-event.v2","sequence":1,"to_state":"cancel_requested"}'
    )
    assert golden_cancellation.canonical_bytes() == expected_bytes
    assert (
        golden_cancellation.digest
        == "sha256:2c1569bec9397fe544ffc326d31cc4aeaa8fa9cdcb2908323f500442666ed3df"
    )
    cancellation = dataclasses.replace(
        golden_cancellation,
        fact_refs=(accepted_ref,),
    )
    cancellation_ref = repository.append_transition(cancellation)
    closing = _event(
        2,
        previous=cancellation,
        previous_ref=cancellation_ref,
        to_state="closing",
        event_kind="cancellation_won",
        cancel_reason=reason,
        cancel_fingerprint=fingerprint,
    )
    closing_ref = repository.append_transition(closing)
    closed = _event(
        3,
        previous=closing,
        previous_ref=closing_ref,
        to_state="closed",
        event_kind="closed",
        cancel_reason=reason,
        cancel_fingerprint=fingerprint,
    )
    repository.append_transition(closed)

    recovered = EpisodeEvidenceRepository(cas, locators).recover(EPISODE)
    assert recovered is not None
    assert tuple(
        (event.cancel_reason, event.cancel_fingerprint) for event in recovered.events
    ) == (
        (None, None),
        (reason, fingerprint),
        (reason, fingerprint),
        (reason, fingerprint),
    )


def test_cancel_fields_reject_tamper_disappearance_and_mismatched_fingerprint() -> None:
    accepted = dataclasses.replace(_event(), run_fingerprint=None)
    reason = "operator requested"
    fingerprint = canonical_digest(
        {
            "schema_version": "bb.rl.episode-cancel-fingerprint.v1",
            "episode_id": EPISODE,
            "create_fingerprint": _d("a"),
            "reason": reason,
        }
    )
    with pytest.raises(EvidenceValidationError, match="fingerprint.*bind"):
        _event(
            1,
            previous=accepted,
            previous_ref=_ref(accepted.canonical_bytes(), "accepted"),
            to_state="cancel_requested",
            event_kind="cancellation_requested",
            cancel_reason=reason,
            cancel_fingerprint=_d("0"),
        )
    cancellation = _event(
        1,
        previous=accepted,
        previous_ref=_ref(accepted.canonical_bytes(), "accepted"),
        to_state="cancel_requested",
        event_kind="cancellation_requested",
        cancel_reason=reason,
        cancel_fingerprint=fingerprint,
    )
    repository = EpisodeEvidenceRepository(InMemoryCAS(), InMemoryEpisodeLocatorStore())
    repository.append_transition(accepted)
    cancellation_ref = repository.append_transition(cancellation)

    with pytest.raises(EvidenceValidationError, match="changed|disappeared"):
        repository.append_transition(
            _event(
                2,
                previous=cancellation,
                previous_ref=cancellation_ref,
                to_state="closing",
                event_kind="cancellation_won",
            )
        )
    changed_reason = "different retry reason"
    changed_fingerprint = canonical_digest(
        {
            "schema_version": "bb.rl.episode-cancel-fingerprint.v1",
            "episode_id": EPISODE,
            "create_fingerprint": _d("a"),
            "reason": changed_reason,
        }
    )
    with pytest.raises(EvidenceValidationError, match="changed|disappeared"):
        repository.append_transition(
            _event(
                2,
                previous=cancellation,
                previous_ref=cancellation_ref,
                to_state="closing",
                event_kind="cancellation_won",
                cancel_reason=changed_reason,
                cancel_fingerprint=changed_fingerprint,
            )
        )


def test_event_sequence_hash_link_and_replay_are_exact() -> None:
    repository = EpisodeEvidenceRepository(InMemoryCAS(), InMemoryEpisodeLocatorStore())
    completed, _ = _append_success_lifecycle(repository)

    recovered = repository.recover(EPISODE)
    assert recovered is not None
    assert len(recovered.events) == 6
    assert recovered.events[-1] == completed
    assert recovered.locator.latest_event_head == completed.digest
    with pytest.raises(EvidenceValidationError, match="continuity"):
        repository.append_transition(
            dataclasses.replace(
                completed,
                sequence=completed.sequence + 2,
                event_kind="gap",
            )
        )


def test_runner_ledger_requires_exact_result_identity_and_sequence() -> None:
    result = _runner_result()
    ledger = _ledger(result)
    assert ledger.event_count == 1
    assert (ledger.first_sequence, ledger.last_sequence) == (0, 0)
    assert ledger.runner_result_digest == canonical_digest(result)

    foreign_result = _runner_result(episode_id="foreign")
    with pytest.raises(EvidenceValidationError, match="identity"):
        RunnerEventLedgerV2(
            EPISODE,
            result.effective_plan_digest,
            foreign_result.events,
            canonical_digest(result),
        )


def test_artifact_manifest_enforces_role_each_and_total_limits() -> None:
    a = _object("trajectory", payload=b"1234")
    b = _object("verifier", payload=b"5678")
    manifest = ArtifactManifestV2(
        (b, a), ("verifier", "trajectory"), 4, 8, ("trajectory",)
    )
    assert tuple(x.role for x in manifest.objects) == ("trajectory", "verifier")
    assert manifest.total_byte_count == 8

    cases = (
        ((a,), ("verifier",), 4, 4, (), "role policy"),
        ((a,), ("trajectory",), 3, 4, (), "per-role"),
        ((a, b), ("trajectory", "verifier"), 4, 7, (), "total"),
        ((a,), ("trajectory", "required"), 4, 4, ("required",), "role policy"),
    )
    for objects, allowed, each, total, required, message in cases:
        with pytest.raises(EvidenceValidationError, match=message):
            ArtifactManifestV2(objects, allowed, each, total, required)


def test_dag_normalizes_sorted_parents_and_rejects_duplicate_unknown_self_cycle_and_multiple_roots() -> (
    None
):
    a, b, c = _d("1"), _d("2"), _d("3")
    node = LineageNodeV2(c, "root", "wp8", (b, a))
    assert node.parent_digests == (a, b)
    ordered = validate_lineage(
        (node, LineageNodeV2(b, "mid", "wp8", (a,)), LineageNodeV2(a, "source", "wp4")),
        c,
    )
    assert tuple(x.node_digest for x in ordered) == (a, b, c)

    with pytest.raises(EvidenceValidationError, match="duplicate"):
        validate_lineage((LineageNodeV2(a, "a", "p"), LineageNodeV2(a, "a", "p")), a)
    with pytest.raises(EvidenceValidationError, match="unknown"):
        validate_lineage((LineageNodeV2(a, "a", "p", (b,)),), a)
    with pytest.raises(EvidenceValidationError, match="itself"):
        LineageNodeV2(a, "a", "p", (a,))
    with pytest.raises(EvidenceValidationError, match="cycle|dependency root"):
        validate_lineage(
            (LineageNodeV2(a, "a", "p", (b,)), LineageNodeV2(b, "b", "p", (a,))), b
        )
    with pytest.raises(EvidenceValidationError, match="exactly one"):
        validate_lineage((LineageNodeV2(a, "a", "p"), LineageNodeV2(b, "b", "p")), b)


def test_every_required_identity_mutation_invalidates_manifest_digest_and_dependent_root() -> (
    None
):
    ledger = _ledger()
    artifact_manifest = ArtifactManifestV2(
        (_object(),), ("trajectory",), 4096, 4096, ("trajectory",)
    )
    base = _manifest(
        _ref(ledger.canonical_bytes(), "ledger"),
        _ref(artifact_manifest.canonical_bytes(), "artifacts"),
    )
    mutations: dict[str, Any] = {
        "resolved_plan_digest": _d("0"),
        "selection_digest": _d("0"),
        "effective_plan_digest": _d("0"),
        "policy_binding_digest": _d("0"),
        "materialization_digest": _d("0"),
        "primary_measurement_digest": _d("0"),
        "verifier_snapshot_digest": _d("0"),
        "verifier_measurement_digest": _d("0"),
        "verifier_result_digest": _d("0"),
        "evidence_policy_ref": _d("0"),
        "retention_policy_ref": _d("0"),
        "reward_components": {"correct": 0},
        "runner_ledger_ref": _ref(b"mutated-ledger", "ledger-2"),
        "artifact_manifest_ref": _ref(b"mutated-artifacts", "artifacts-2"),
    }
    lineage_bound = {
        "resolved_plan_digest",
        "selection_digest",
        "effective_plan_digest",
        "policy_binding_digest",
        "materialization_digest",
        "primary_measurement_digest",
        "verifier_snapshot_digest",
        "verifier_measurement_digest",
        "verifier_result_digest",
        "runner_ledger_ref",
        "artifact_manifest_ref",
    }
    for field, value in mutations.items():
        if field in lineage_bound:
            with pytest.raises(EvidenceValidationError, match="lineage semantic"):
                dataclasses.replace(base, **{field: value})
        else:
            changed = dataclasses.replace(base, **{field: value})
            assert changed.digest != base.digest, field
            assert changed.lineage_root != base.lineage_root, field


def test_completed_cannot_claim_cleanup_and_closed_requires_exact_detailed_release() -> (
    None
):
    cleanup_field = next(
        field
        for field in dataclasses.fields(CompletedEpisodeEnvelopeV2)
        if field.name == "cleanup_disposition"
    )
    assert cleanup_field.init is False and cleanup_field.default == "pending"
    with pytest.raises(TypeError):
        CompletedEpisodeEnvelopeV2(cleanup_disposition="released")  # type: ignore[call-arg]

    completed = _ref(b"completed", "completed")
    event = _ref(b"event", "event")
    failures: tuple[Any, ...] = (
        {},
        {"state": "failed", "steps": [{"resource": "lease", "state": "failed"}]},
        {
            "state": "quarantined",
            "steps": [{"resource": "lease", "state": "quarantined"}],
        },
        {"lease_id": "lease-7", "state": "released", "steps": []},
        {
            "lease_id": "lease-7",
            "state": "released",
            "steps": [{"resource": "lease", "state": "released", "detail": ""}],
        },
    )
    for receipt in failures:
        with pytest.raises(EvidenceValidationError):
            ClosedEpisodeEnvelopeV2(
                EPISODE,
                completed,
                canonical_digest(receipt),
                receipt,
                event,
                _d("1"),
                "failed",
            )

    for state in (CleanupState.RELEASED, CleanupState.ALREADY_RELEASED):
        receipt = SandboxCleanupReceipt.from_steps(
            "lease-7",
            tuple(
                CleanupStepReceipt(resource, state)
                for resource in (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                )
            ),
        )
        projection = _cleanup_projection(receipt)
        envelope = ClosedEpisodeEnvelopeV2(
            EPISODE,
            completed,
            canonical_digest(projection),
            projection,
            event,
            _d("1"),
            "failed",
        )
        assert envelope.cleanup_disposition == "released"


def test_closed_envelope_rejects_legacy_base_four_cleanup_and_non_exact_verifier_resources() -> (
    None
):
    completed = _ref(b"completed", "completed")
    event = _ref(b"event", "event")
    legacy_receipt = SandboxCleanupReceipt.from_steps(
        "lease-7",
        tuple(
            CleanupStepReceipt(resource, CleanupState.RELEASED)
            for resource in ("runtime", "workspace", "cache_holder", "lease_record")
        ),
    )
    legacy_projection = _cleanup_projection(legacy_receipt)
    with pytest.raises(EvidenceValidationError, match="incomplete|ambiguous"):
        ClosedEpisodeEnvelopeV2(
            EPISODE,
            completed,
            canonical_digest(legacy_projection),
            legacy_projection,
            event,
            _d("1"),
            "failed",
        )

    primary_projection = _cleanup_projection(_released_receipt())
    verifier_projection = _cleanup_projection(_verifier_released_receipt())
    with pytest.raises(
        EvidenceValidationError, match="verifier cleanup resource contract"
    ):
        ClosedEpisodeEnvelopeV2(
            EPISODE,
            completed,
            canonical_digest(primary_projection),
            primary_projection,
            event,
            _d("1"),
            "failed",
            verifier_cleanup_receipt_digest=canonical_digest(verifier_projection),
            verifier_cleanup_receipt=verifier_projection,
            verifier_cleanup_required_resources=("runtime", "workspace"),
        )


def test_closed_envelope_load_fallback_rejects_legacy_base_four_receipt() -> None:
    repository, cas, closed = _publish_closed()
    legacy = closed.envelope.to_canonical_obj()
    legacy.pop("cleanup_required_resources")
    cleanup = dict(legacy["cleanup_receipt"])
    cleanup["steps"] = [
        step for step in cleanup["steps"] if step["resource"] != "child_verifier"
    ]
    legacy["cleanup_receipt"] = cleanup
    legacy["cleanup_receipt_digest"] = canonical_digest(cleanup)
    payload = canonical_json_bytes(legacy)
    legacy_ref = cas.put_bytes(
        payload,
        artifact_id="v2/legacy-base-four-closed-envelope",
        media_type=MEDIA_TYPE,
        metadata={"episode_id": EPISODE},
    )

    with pytest.raises(EvidenceValidationError, match="incomplete|ambiguous"):
        repository._load_closed_envelope(legacy_ref)


def test_locator_and_forward_self_references_never_enter_evidence_lineage() -> None:
    repository, cas, publication, _ = _publish_completed()
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.completed_envelope is not None
    evidence = json.loads(
        cas.get_bytes(recovered.completed_envelope.evidence_manifest_ref)
    )
    lineage_digests = {node["node_digest"] for node in evidence["lineage_nodes"]}
    lineage_parents = {
        parent
        for node in evidence["lineage_nodes"]
        for parent in node["parent_digests"]
    }
    forbidden = {
        publication.envelope_ref.sha256,
        publication.tombstone_ref.sha256,
        recovered.locator.digest,
        recovered.locator.checksum,
    }
    assert forbidden.isdisjoint(lineage_digests | lineage_parents)
    assert (
        publication.envelope_ref.sha256
        not in publication.envelope.canonical_bytes().decode()
    )
    assert (
        publication.tombstone_ref.sha256
        not in publication.envelope.canonical_bytes().decode()
    )


def test_in_memory_locator_generation_cas_is_thread_safe() -> None:
    store = InMemoryEpisodeLocatorStore()
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def contender() -> None:
        barrier.wait()
        try:
            store.compare_and_swap(EPISODE, None, _locator(EPISODE))
        except LocatorConflictError:
            result = "conflict"
        else:
            result = "won"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=contender) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert outcomes.count("won") == 1
    assert outcomes.count("conflict") == 7


def test_filesystem_locator_generation_cas_is_process_safe(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_process_locator_race, args=(str(tmp_path), EPISODE, gate, output)
        )
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(timeout=5)
        assert not process.is_alive() and process.exitcode == 0
    outcomes = [output.get(timeout=1) for _ in processes]
    assert outcomes.count("won") == 1
    assert outcomes.count("conflict") == 3


@pytest.mark.parametrize("fail_on", ["file", "directory"])
def test_filesystem_locator_fsync_failure_is_not_committed_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_on: str
) -> None:
    store = FilesystemEpisodeLocatorStore(tmp_path)
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if (fail_on == "file" and calls == 1) or (
            fail_on == "directory" and calls == 2
        ):
            raise OSError(f"deterministic {fail_on} fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="deterministic"):
        store.compare_and_swap(EPISODE, None, _locator(EPISODE))
    monkeypatch.setattr(os, "fsync", real_fsync)
    if fail_on == "directory":
        assert store.get(EPISODE) is None
    store.compare_and_swap(EPISODE, None, _locator(EPISODE))
    assert store.get(EPISODE) == _locator(EPISODE)


def test_filesystem_locator_rejects_leaf_symlink_and_ancestor_swap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "locators"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    store = FilesystemEpisodeLocatorStore(root)
    (root / f"{EPISODE}.json").symlink_to(outside / "captured.json")
    with pytest.raises((EvidenceCorruptError, LocatorConflictError, OSError)):
        store.compare_and_swap(EPISODE, None, _locator(EPISODE))
    assert not (outside / "captured.json").exists()

    (root / f"{EPISODE}.json").unlink()
    root.rename(tmp_path / "old-root")
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises((EvidenceCorruptError, LocatorConflictError, OSError)):
        store.compare_and_swap(EPISODE, None, _locator(EPISODE))
    assert not (outside / f"{EPISODE}.json").exists()


def test_filesystem_locator_corrupt_checksum_is_quarantined_on_read(
    tmp_path: Path,
) -> None:
    store = FilesystemEpisodeLocatorStore(tmp_path)
    store.compare_and_swap(EPISODE, None, _locator(EPISODE))
    path = tmp_path / f"{EPISODE}.json"
    value = json.loads(path.read_bytes())
    value["current_state"] = "closed"
    path.write_bytes(canonical_json_bytes(value))
    with pytest.raises(EvidenceCorruptError, match="checksum"):
        store.get(EPISODE)


def test_recovery_rejects_missing_blob_and_digest_mismatch() -> None:
    repository, cas, publication, _ = _publish_completed()
    del cas._bytes_by_id[publication.envelope.evidence_manifest_ref.artifact_id]
    with pytest.raises(EvidenceCorruptError):
        repository.recover(EPISODE)

    repository2, cas2, publication2, _ = _publish_completed()
    ref = publication2.envelope.evidence_manifest_ref
    cas2._bytes_by_id[ref.artifact_id] = b"{}"
    with pytest.raises(EvidenceCorruptError):
        repository2.recover(EPISODE)


def test_publish_recover_and_export_enforce_recursive_secret_redaction_exact_policy_and_role_gates() -> (
    None
):
    secrets = {
        "authorization": "Bearer recursive-secret",
        "nested": [
            {"password": "hunter2", "url": "https://user:pass@example.invalid/path"}
        ],
        "argv": ["--token=raw-secret"],
        "environment": {"API_TOKEN": "raw-secret"},
    }
    obj = _object(metadata={"request": secrets})
    repository, _, closed = _publish_closed(artifact_object=obj)
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.closed_envelope == closed.envelope

    authorization = _export_authorization(repository, closed)
    exported = repository.export_closed(EPISODE, authorization, ("trajectory",))
    rendered = exported.canonical_bytes().decode().lower()
    for secret in ("recursive-secret", "hunter2", "user:pass", "raw-secret"):
        assert secret not in rendered
    assert exported.exported_objects[0].role == "trajectory"

    with pytest.raises(ExportDeniedError, match="authorized"):
        repository.export_closed(EPISODE, authorization, ("verifier",))
    with pytest.raises(ExportDeniedError, match="authorization|pinned"):
        repository.export_closed(
            EPISODE,
            dataclasses.replace(authorization, evidence_policy_ref=_d("0")),
            ("trajectory",),
        )
    with pytest.raises(ExportDeniedError, match="authorization|pinned"):
        repository.export_closed(
            EPISODE,
            dataclasses.replace(authorization, retention_policy_ref=_d("0")),
            ("trajectory",),
        )


def test_production_retention_window_boundaries_are_exact_pinned_and_timezone_deterministic() -> (
    None
):
    anchor = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    now = [anchor - timedelta(microseconds=1)]
    repository, cas, closed = _publish_closed(
        retention_minimum_seconds=17,
        retention_maximum_seconds=3_600,
        clock=lambda: now[0],
    )
    authorization = _export_authorization(repository, closed)
    assert authorization.not_before == "2026-01-01T00:00:05Z"
    assert authorization.not_after == "2026-01-01T01:00:05Z"
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.evidence_manifest is not None
    retention_ref = recovered.evidence_manifest.retention_policy_record_ref
    assert retention_ref is not None
    retention_record = repository._load_retention_policy(EPISODE, retention_ref)
    assert retention_record.grant.minimum_seconds == 17
    assert retention_record.grant.maximum_seconds == 3_600
    assert retention_ref.sha256 == authorization.retention_policy_ref

    with pytest.raises(ExportDeniedError, match="active|retention"):
        repository.export_closed(EPISODE, authorization, ("trajectory",))
    now[0] = anchor
    at_anchor = repository.export_closed(EPISODE, authorization, ("trajectory",))
    now[0] = datetime(
        2026,
        1,
        1,
        2,
        0,
        4,
        999_999,
        tzinfo=timezone(timedelta(hours=1)),
    )
    before_maximum = repository.export_closed(
        EPISODE,
        authorization,
        ("trajectory",),
    )
    assert before_maximum.authorization_digest == at_anchor.authorization_digest

    refs_before_expiry = set(cas._refs_by_id)
    now[0] = anchor + timedelta(seconds=3_600)
    with pytest.raises(ExportDeniedError, match="expired|retention"):
        repository.export_closed(EPISODE, authorization, ("trajectory",))
    now[0] = anchor + timedelta(days=30)
    with pytest.raises(ExportDeniedError, match="expired|retention"):
        repository.export_closed(EPISODE, authorization, ("trajectory",))
    assert set(cas._refs_by_id) == refs_before_expiry
    assert cas.has(retention_ref)
    assert cas.has(closed.envelope_ref)

    now[0] = anchor
    with pytest.raises(ExportDeniedError, match="authorization|pinned"):
        repository.export_closed(
            EPISODE,
            dataclasses.replace(
                authorization,
                not_after="2026-01-01T01:00:06Z",
            ),
            ("trajectory",),
        )


def test_stable_claims_select_one_pinned_window_and_export_only_while_active() -> None:
    anchor = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    now = [anchor - timedelta(microseconds=1)]
    payload = canonical_json_bytes(
        {
            "authorization": "Bearer repository-selected-secret",
            "safe": "retained",
        }
    )
    repository, cas, closed = _publish_closed(
        artifact_object=_object(payload=payload),
        artifact_payload=payload,
        retention_maximum_seconds=3_600,
        clock=lambda: now[0],
    )
    authorization = _export_authorization(repository, closed)
    claims = _export_claims(authorization)

    assert authorization.not_before == "2026-01-01T00:00:05Z"
    assert authorization.not_after == "2026-01-01T01:00:05Z"
    assert not hasattr(claims, "not_before")
    assert not hasattr(claims, "not_after")
    with pytest.raises(ExportDeniedError, match="active|retention"):
        repository.export_closed_claims(EPISODE, claims)

    now[0] = anchor
    exported = repository.export_closed_claims(EPISODE, claims)
    assert exported.authorization_digest == authorization.digest
    assert exported.allowed_roles == ("trajectory",)
    rendered = cas.get_bytes(exported.exported_objects[0].artifact_ref).decode()
    assert "repository-selected-secret" not in rendered
    assert '"safe":"retained"' in rendered

    for mismatch in (
        {"subject_digest": _d("0")},
        {"scope": "other_export"},
        {"evidence_policy_ref": _d("1")},
        {"retention_policy_ref": _d("2")},
        {"allowed_roles": ("verifier",)},
        {"redaction_decision_digest": _d("3")},
    ):
        with pytest.raises(ExportDeniedError, match="authorization|claim|pin"):
            repository.export_closed_claims(
                EPISODE,
                dataclasses.replace(claims, **mismatch),
            )

    now[0] = anchor + timedelta(seconds=3_600)
    with pytest.raises(ExportDeniedError, match="expired|retention"):
        repository.export_closed_claims(EPISODE, claims)


def test_stable_claims_deny_an_ambiguous_duplicate_pin_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = canonical_json_bytes({"safe": "retained"})
    repository, _, closed = _publish_closed(
        artifact_objects=(
            _object("trajectory", payload=payload),
            _object("verifier", payload=payload),
        ),
        artifact_payload=payload,
    )
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.closed_envelope is not None
    authorization = repository._load_export_authorization(
        closed.envelope.export_authorization_refs[0]
    )
    claims = _export_claims(authorization)
    monkeypatch.setattr(
        repository,
        "_recover_closed_for_export",
        lambda episode_id: recovered,
    )
    monkeypatch.setattr(
        repository,
        "_load_export_authorization",
        lambda ref: authorization,
    )

    with pytest.raises(ExportDeniedError, match="uniquely"):
        repository.export_closed_claims(EPISODE, claims)


def test_production_retention_window_rejects_naive_repository_clock() -> None:
    repository, _, closed = _publish_closed(
        clock=lambda: datetime(2026, 1, 1, 0, 0, 5),
    )
    with pytest.raises(ExportDeniedError, match="timezone-aware"):
        repository.export_closed(
            EPISODE,
            _export_authorization(repository, closed),
            ("trajectory",),
        )


@pytest.mark.parametrize("damage", ("missing", "tampered"))
def test_exact_pinned_retention_record_missing_or_tampered_fails_closed(
    damage: str,
) -> None:
    repository, cas, closed = _publish_closed()
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.evidence_manifest is not None
    retention_ref = recovered.evidence_manifest.retention_policy_record_ref
    assert retention_ref is not None
    if damage == "missing":
        del cas._bytes_by_id[retention_ref.artifact_id]
        del cas._refs_by_id[retention_ref.artifact_id]
    else:
        cas._bytes_by_id[retention_ref.artifact_id] = canonical_json_bytes(
            {"schema_version": "bb.rl.retention-policy-registry-record.v2"}
        )

    with pytest.raises(
        EvidenceCorruptError, match="retention|integrity|artifact|recovery"
    ):
        repository.export_closed(
            EPISODE,
            _export_authorization(repository, closed),
            ("trajectory",),
        )


def test_cleanup_receipt_spoofs_are_rejected_without_publication_side_effects() -> None:
    locators = InMemoryEpisodeLocatorStore()
    repository, cas, completed, event = _publish_completed(locators=locators)
    closed_event = _prepare_closed_event(repository, completed, event)
    resources = (
        "child_verifier",
        "runtime",
        "workspace",
        "cache_holder",
        "lease_record",
    )

    def released(lease: str, names: tuple[str, ...]) -> SandboxCleanupReceipt:
        return SandboxCleanupReceipt.from_steps(
            lease,
            tuple(CleanupStepReceipt(name, CleanupState.RELEASED) for name in names),
        )

    def unreleased(resource: str) -> SandboxCleanupReceipt:
        return SandboxCleanupReceipt.from_steps(
            "lease-7",
            tuple(
                CleanupStepReceipt(
                    name,
                    CleanupState.FAILED if name == resource else CleanupState.RELEASED,
                )
                for name in resources
            ),
        )

    spoofs: tuple[Any, ...] = (
        _cleanup_projection(released("lease-7", resources)),
        released("lease-7", resources[1:]),
        released("lease-7", resources[:-1]),
        released("lease-7", (*resources, "child_verifier")),
        released("lease-7", (*resources, "workspace")),
        released("lease-7", (*resources, "unexpected")),
        unreleased("child_verifier"),
        unreleased("runtime"),
        released("foreign-lease", resources),
    )
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)
    for receipt in spoofs:
        with pytest.raises(EvidenceValidationError):
            repository.publish_closed(
                ClosedPublicationInputsV2(
                    episode_id=EPISODE,
                    completed=completed,
                    cleanup_receipt=receipt,
                    closed_event=closed_event,
                    final_primary_outcome="succeeded",
                    cleanup_lease_id="lease-7",
                    cleanup_required_resources=resources,
                )
            )
        assert locators.get(EPISODE) == locator_before
        assert set(cas._refs_by_id) == refs_before


def test_closed_export_requires_exact_authorization_and_redaction_pins() -> None:
    repository, cas, closed = _publish_closed()
    authorization = _export_authorization(repository, closed)
    refs_before = set(cas._refs_by_id)
    with pytest.raises(ExportDeniedError, match="authorization.*pinned"):
        repository.export_closed(
            EPISODE,
            dataclasses.replace(authorization, subject="unrecorded-subject"),
            ("trajectory",),
        )
    assert set(cas._refs_by_id) == refs_before
    assert cas.has(closed.envelope_ref)

    with pytest.raises(EvidenceValidationError, match="authorization|redaction|pin"):
        _publish_closed(pin_authorization=False)
    with pytest.raises(EvidenceValidationError, match="authorization|redaction|pin"):
        _publish_closed(redaction_decision="different")


@pytest.mark.parametrize(
    "graft", ("event", "tombstone", "envelope", "head", "generation")
)
def test_recovery_rejects_cross_episode_and_locator_linkage_grafts(graft: str) -> None:
    locators = InMemoryEpisodeLocatorStore()
    repository, cas, foreign, _ = _publish_completed(locators=locators)
    victim = "victim-episode"
    victim_event = dataclasses.replace(_event(), episode_id=victim)
    victim_event_ref = repository.append_transition(victim_event)

    if graft == "event":
        forged = EpisodeLocatorRecordV2(
            victim,
            2,
            "accepted",
            foreign.locator.latest_event_head,
            foreign.locator.latest_event_ref,
        )
    else:
        tombstone = EpisodeCompletedTombstoneV2(
            episode_id=victim if graft != "tombstone" else EPISODE,
            create_fingerprint=_d("a"),
            run_fingerprint=_d("b"),
            event_head=_d("0") if graft == "head" else victim_event.digest,
            response_ref=foreign.tombstone.response_ref,
            envelope_ref=foreign.envelope_ref,
            locator_generation=99 if graft == "generation" else 2,
        )
        tombstone_ref = _store_schema(cas, victim, f"graft-{graft}", tombstone)
        forged = EpisodeLocatorRecordV2(
            victim,
            2,
            "completed",
            victim_event.digest,
            victim_event_ref,
            tombstone_ref,
        )
    locators.compare_and_swap(victim, 1, forged)
    with pytest.raises(
        EvidenceCorruptError, match="identity|head|generation|mismatch|non-contiguous"
    ):
        repository.recover(victim)


@pytest.mark.parametrize("max_each,max_total", ((0, 4096), (4096, 0), (0, 0)))
def test_explicit_zero_artifact_limits_are_not_replaced_by_fallbacks(
    max_each: int, max_total: int
) -> None:
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    with pytest.raises(EvidenceValidationError, match="per-role|total"):
        _publish_completed(
            cas, locators, max_each_bytes=max_each, max_total_bytes=max_total
        )
    recovered = EpisodeEvidenceRepository(cas, locators).recover(EPISODE)
    assert recovered is not None
    assert recovered.locator.current_state == "completed"
    assert recovered.locator.generation == len(recovered.events) + 1
    assert recovered.locator.completed_tombstone_ref is None
    assert recovered.completed_tombstone is None


@pytest.mark.parametrize("damage", ("missing", "corrupt"))
@pytest.mark.parametrize("target_index", (0, 1, 2))
def test_recovery_verifies_runner_ledger_and_every_artifact_ref(
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
    target_index: int,
) -> None:
    objects = (_object("trajectory"), _object("verifier"))
    repository, cas, publication, _ = _publish_completed(artifact_objects=objects)
    evidence = json.loads(cas.get_bytes(publication.envelope.evidence_manifest_ref))
    artifact_manifest_ref = ArtifactRef(**evidence["artifact_manifest_ref"])
    artifact_manifest = json.loads(cas.get_bytes(artifact_manifest_ref))
    refs = (
        ArtifactRef(**evidence["runner_ledger_ref"]),
        *(ArtifactRef(**item["artifact_ref"]) for item in artifact_manifest["objects"]),
    )
    assert tuple(ref.artifact_id for ref in refs[1:]) == tuple(
        obj.artifact_ref.artifact_id for obj in objects
    )
    target = refs[target_index]
    if damage == "missing":
        del cas._bytes_by_id[target.artifact_id]
        del cas._refs_by_id[target.artifact_id]
    else:
        cas._bytes_by_id[target.artifact_id] = b"corrupt"

    calls: list[str] = []
    real_get_bytes = cas.get_bytes

    def tracking_get_bytes(
        ref: ArtifactRef | str, *, max_bytes: int | None = None
    ) -> bytes:
        calls.append(ref.artifact_id if isinstance(ref, ArtifactRef) else ref)
        return real_get_bytes(ref, max_bytes=max_bytes)

    monkeypatch.setattr(cas, "get_bytes", tracking_get_bytes)
    with pytest.raises(EvidenceCorruptError):
        repository.recover(EPISODE)
    assert calls.count(target.artifact_id) == 1
    assert len(calls) <= len(set(calls)) + 1


@pytest.mark.parametrize(
    "artifact_id,secret",
    (
        ("https://user:credential@example.invalid/output", "user:credential"),
        ("outputs/--token=raw-secret", "raw-secret"),
        ("outputs/api_key=hunter2", "hunter2"),
    ),
)
def test_export_never_serializes_sensitive_artifact_locators(
    artifact_id: str, secret: str
) -> None:
    with pytest.raises(EvidenceValidationError, match="locator is unsafe"):
        dataclasses.replace(
            _object(),
            artifact_ref=dataclasses.replace(
                _object().artifact_ref, artifact_id=artifact_id
            ),
        )


@pytest.mark.parametrize(
    "detail",
    (
        "api_key=hunter2",
        "api-key: hunter2",
        "secret: hunter2",
        "credential=hunter2",
        "https://user:hunter2@example.invalid/output",
        "--credential hunter2",
        "--api-key=hunter2",
    ),
)
def test_safe_failure_detail_rejects_every_credential_url_and_argv_form(
    detail: str,
) -> None:
    with pytest.raises(EvidenceValidationError, match="unsafe"):
        SafeFailureFactV2("runner", "failed", "never", "after-open", detail=detail)


def test_recovery_accepts_legal_locator_generations_after_completed_publication() -> (
    None
):
    repository, _, completed, event = _publish_completed()
    closing_event = _event(
        event.sequence + 1,
        previous=event,
        previous_ref=completed.locator.latest_event_ref,
        to_state="closing",
        event_kind="cleanup_started",
    )
    repository.append_transition(closing_event)
    recovered = repository.recover(EPISODE)
    assert recovered is not None
    assert recovered.locator.generation == completed.locator.generation + 1
    assert recovered.locator.latest_event_head == closing_event.digest
    assert recovered.completed_tombstone == completed.tombstone
    assert recovered.completed_envelope == completed.envelope


@pytest.mark.parametrize("damage", ("missing", "corrupt"))
def test_verifier_cleanup_receipt_survives_completed_crash_recovery_and_is_verified(
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    repository, cas, completed, _ = _publish_completed()
    evidence = json.loads(cas.get_bytes(completed.envelope.evidence_manifest_ref))
    cleanup_ref = ArtifactRef(**evidence["verifier_cleanup_receipt_ref"])
    assert json.loads(cas.get_bytes(cleanup_ref)) == _cleanup_projection(
        _verifier_released_receipt()
    )
    if damage == "missing":
        del cas._bytes_by_id[cleanup_ref.artifact_id]
        del cas._refs_by_id[cleanup_ref.artifact_id]
    else:
        cas._bytes_by_id[cleanup_ref.artifact_id] = b"corrupt"
    calls = 0
    real_get_bytes = cas.get_bytes

    def tracking_get_bytes(
        ref: ArtifactRef | str, *, max_bytes: int | None = None
    ) -> bytes:
        nonlocal calls
        if (
            ref.artifact_id if isinstance(ref, ArtifactRef) else ref
        ) == cleanup_ref.artifact_id:
            calls += 1
        return real_get_bytes(ref, max_bytes=max_bytes)

    monkeypatch.setattr(cas, "get_bytes", tracking_get_bytes)
    with pytest.raises(EvidenceCorruptError):
        repository.recover(EPISODE)
    assert calls == 1


@pytest.mark.parametrize("mismatch", ("kind", "digest", "parents"))
def test_execution_manifest_rejects_semantic_lineage_field_node_mismatches(
    mismatch: str,
) -> None:
    ledger = _ledger()
    artifacts = ArtifactManifestV2(
        (_object(),), ("trajectory",), 4096, 4096, ("trajectory",)
    )
    manifest = _manifest(
        _ref(ledger.canonical_bytes(), "ledger"),
        _ref(artifacts.canonical_bytes(), "artifacts"),
    )
    if mismatch == "digest":
        changes = {"resolved_plan_digest": _d("0")}
    else:
        nodes = list(manifest.lineage_nodes)
        index = next(i for i, node in enumerate(nodes) if node.kind == "resolved_plan")
        nodes[index] = dataclasses.replace(
            nodes[index],
            kind="selection" if mismatch == "kind" else nodes[index].kind,
            parent_digests=(_d("0"),)
            if mismatch == "parents"
            else nodes[index].parent_digests,
        )
        changes = {"lineage_nodes": tuple(nodes)}
    with pytest.raises(
        EvidenceValidationError, match="lineage|semantic|binding|parent"
    ):
        dataclasses.replace(manifest, **changes)


def test_caller_cannot_narrow_authoritative_cleanup_required_resources() -> None:
    locators = InMemoryEpisodeLocatorStore()
    repository, cas, completed, event = _publish_completed(locators=locators)
    closed_event = _prepare_closed_event(repository, completed, event)
    narrowed = ("runtime", "workspace", "lease_record")
    receipt = SandboxCleanupReceipt.from_steps(
        "lease-7",
        tuple(
            CleanupStepReceipt(resource, CleanupState.RELEASED) for resource in narrowed
        ),
    )
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)
    with pytest.raises(EvidenceValidationError, match="resource contract"):
        repository.publish_closed(
            ClosedPublicationInputsV2(
                episode_id=EPISODE,
                completed=completed,
                cleanup_receipt=receipt,
                closed_event=closed_event,
                final_primary_outcome="succeeded",
                cleanup_lease_id="lease-7",
                cleanup_required_resources=narrowed,
                verifier_cleanup_receipt=_verifier_released_receipt(),
                verifier_cleanup_lease_id="verifier-lease-7",
                verifier_cleanup_required_resources=(
                    "runtime",
                    "workspace",
                    "snapshot",
                    "lease_record",
                ),
            )
        )
    assert locators.get(EPISODE) == locator_before
    assert set(cas._refs_by_id) == refs_before


@pytest.mark.parametrize(
    "mismatch", ("create_fingerprint", "run_fingerprint", "response_ref")
)
def test_recovery_rejects_closed_tombstone_completed_identity_linkage_mismatch(
    mismatch: str,
) -> None:
    repository, cas, closed = _publish_closed()
    changes: dict[str, Any] = (
        {mismatch: _ref(b"foreign-response", "foreign-response")}
        if mismatch == "response_ref"
        else {mismatch: _d("0")}
    )
    forged_tombstone = dataclasses.replace(closed.tombstone, **changes)
    forged_ref = _store_schema(
        cas, EPISODE, f"forged-closed-{mismatch}", forged_tombstone
    )
    forged_locator = dataclasses.replace(
        closed.locator, closed_tombstone_ref=forged_ref, checksum=""
    )
    repository._locators._records[EPISODE] = forged_locator
    with pytest.raises(
        EvidenceCorruptError, match="linkage|fingerprint|response|identity"
    ):
        repository.recover(EPISODE)


def test_runner_event_journal_append_and_recovery_are_exact_and_head_bound() -> None:
    repository = EpisodeEvidenceRepository(InMemoryCAS(), InMemoryEpisodeLocatorStore())
    repository.append_transition(_event())
    first = _runner_result(plan_digest=_d("3")).events[0]
    second = dataclasses.replace(first, sequence=1)
    publications = (
        repository.append_runner_event(EPISODE, _d("3"), first),
        repository.append_runner_event(EPISODE, _d("3"), second),
    )
    recovered = repository.recover_runner_events(EPISODE)
    assert tuple(event["sequence"] for event in recovered) == (0, 1)
    assert tuple(publication.event_digest for publication in publications) == tuple(
        canonical_digest(event) for event in recovered
    )
    locator = repository._locators.get(EPISODE)
    assert locator is not None
    repository._locators._records[EPISODE] = dataclasses.replace(
        locator, runner_event_head=_d("0"), checksum=""
    )
    with pytest.raises(EvidenceCorruptError, match="head"):
        repository.recover_runner_events(EPISODE)


def test_failed_completed_without_run_persists_verifier_cleanup_failure() -> None:
    repository = EpisodeEvidenceRepository(InMemoryCAS(), InMemoryEpisodeLocatorStore())
    no_run_fingerprint = canonical_digest({"episode_id": EPISODE, "run": "not-started"})
    event, event_ref = _append_failure_lifecycle(
        repository, run_fingerprint=no_run_fingerprint
    )
    failure = SafeFailureFactV2(
        "runner", "failed", "never", "before-run", detail="safe"
    )
    verifier_failure = SafeFailureFactV2(
        "cleanup", "failed", "manual", "after-verifier", detail="safe"
    )
    publication = repository.publish_failed_completed(
        FailedCompletedPublicationInputsV2(
            episode_id=EPISODE,
            create_fingerprint=_d("a"),
            run_fingerprint=no_run_fingerprint,
            create_response_bytes=b'{"created":true}',
            run_response_bytes=canonical_json_bytes(
                {"run": "not-started", "run_fingerprint": no_run_fingerprint}
            ),
            lifecycle_head_ref=event_ref,
            lifecycle_head_digest=event.digest,
            primary_disposition="failed",
            primary_failure=failure,
            session_close_failure=None,
            verifier_cleanup_failure=verifier_failure,
            runner_event_refs=(),
            resolved_plan=None,
            policy_binding_digest=None,
            materialization_receipt=None,
            primary_measurement=None,
            verifier_snapshot=None,
            verifier_measurement_digest=None,
            verifier_result=None,
            verifier_cleanup_receipt=None,
            verifier_lease_id=None,
        )
    )
    recovered = repository.recover(EPISODE)
    assert (
        recovered is not None and recovered.completed_envelope == publication.envelope
    )
    assert recovered.completed_envelope.primary_outcome == "failed"
    manifest = recovered.evidence_manifest
    assert manifest is not None
    assert manifest.primary_measurement_digest is None
    assert manifest.primary_failure_digest is not None
    assert {node.kind for node in manifest.lineage_nodes} >= {
        "primary_failure",
        "runner_ledger",
    }
    assert "primary_measurement" not in {node.kind for node in manifest.lineage_nodes}


def test_binary_artifact_export_fails_closed_without_creating_projection() -> None:
    payload = b"\x00\xffraw-secret"
    obj = _object(payload=payload)
    repository, cas, closed = _publish_closed(
        artifact_object=obj, artifact_payload=payload
    )
    before = set(cas._refs_by_id)
    with pytest.raises(
        ExportDeniedError, match="opaque|non-JSON|safe export projection"
    ):
        repository.export_closed(
            EPISODE, _export_authorization(repository, closed), ("trajectory",)
        )
    assert set(cas._refs_by_id) == before


def test_failed_verifier_evidence_is_unadmitted_until_released_cleanup_proof() -> None:
    repository = EpisodeEvidenceRepository(InMemoryCAS(), InMemoryEpisodeLocatorStore())
    no_run = canonical_digest({"episode_id": EPISODE, "run": "not-started"})
    event, event_ref = _append_failure_lifecycle(repository, run_fingerprint=no_run)
    failed_cleanup = SandboxCleanupReceipt(
        "verifier-lease-7",
        (
            CleanupStepReceipt("runtime", CleanupState.FAILED),
            CleanupStepReceipt("workspace", CleanupState.ALREADY_RELEASED),
            CleanupStepReceipt("lease_record", CleanupState.ALREADY_RELEASED),
        ),
        CleanupState.FAILED,
    )
    completed = repository.publish_failed_completed(
        FailedCompletedPublicationInputsV2(
            EPISODE,
            _d("a"),
            no_run,
            b'{"created":true}',
            canonical_json_bytes({"run": "not-started", "run_fingerprint": no_run}),
            event_ref,
            event.digest,
            "failed",
            SafeFailureFactV2("runner", "failed", "never", "before-run", detail="safe"),
            None,
            None,
            (),
            None,
            None,
            None,
            None,
            {"snapshot": "unadmitted"},
            _d("a"),
            {"result": "unadmitted"},
            failed_cleanup,
            "verifier-lease-7",
        )
    )
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.completed_envelope is not None
    evidence = repository._load_evidence_manifest(
        recovered.completed_envelope.evidence_manifest_ref
    )
    assert evidence.reward_disposition == "ineligible"
    assert evidence.verifier_snapshot_digest is None
    assert evidence.verifier_measurement_digest is None
    assert evidence.verifier_result_digest is None
    assert evidence.verifier_cleanup_receipt_ref is None

    closed_event = _event(
        event.sequence + 1,
        previous=event,
        previous_ref=completed.locator.latest_event_ref,
        to_state="closed",
        event_kind="closed",
    )
    with pytest.raises(EvidenceValidationError, match="released|cleanup"):
        repository.publish_closed(
            ClosedPublicationInputsV2(
                EPISODE,
                completed,
                _released_receipt(),
                closed_event,
                "failed",
                "lease-7",
                (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                ),
                failed_cleanup,
                "verifier-lease-7",
                ("runtime", "workspace", "snapshot", "lease_record"),
            )
        )


def test_safe_export_preserves_artifact_metadata_and_immutable_reference() -> None:
    metadata = {"dataset": "train", "shard": 7, "nested": {"format": "json"}}
    obj = _object(metadata=metadata)
    repository, _, closed = _publish_closed(artifact_object=obj)
    exported = repository.export_closed(
        EPISODE, _export_authorization(repository, closed), ("trajectory",)
    )
    exported_ref = exported.exported_objects[0].artifact_ref
    assert exported_ref == obj.artifact_ref
    assert exported_ref.metadata == metadata


def test_failed_completed_without_verifier_closes_from_released_primary_cleanup() -> (
    None
):
    repository = EpisodeEvidenceRepository(InMemoryCAS(), InMemoryEpisodeLocatorStore())
    no_run = canonical_digest({"episode_id": EPISODE, "run": "not-started"})
    failed_event, failed_event_ref = _append_failure_lifecycle(
        repository, run_fingerprint=no_run
    )
    completed = repository.publish_failed_completed(
        FailedCompletedPublicationInputsV2(
            episode_id=EPISODE,
            create_fingerprint=_d("a"),
            run_fingerprint=no_run,
            create_response_bytes=b'{"created":true}',
            run_response_bytes=canonical_json_bytes(
                {"run": "not-started", "run_fingerprint": no_run}
            ),
            lifecycle_head_ref=failed_event_ref,
            lifecycle_head_digest=failed_event.digest,
            primary_disposition="failed",
            primary_failure=SafeFailureFactV2(
                "runner", "failed", "never", "before-run", detail="safe"
            ),
            session_close_failure=None,
            verifier_cleanup_failure=None,
            runner_event_refs=(),
            resolved_plan=None,
            policy_binding_digest=None,
            materialization_receipt=None,
            primary_measurement=None,
            verifier_snapshot=None,
            verifier_measurement_digest=None,
            verifier_result=None,
            verifier_cleanup_receipt=None,
            verifier_lease_id=None,
        )
    )
    closed_event = _event(
        failed_event.sequence + 1,
        previous=failed_event,
        previous_ref=completed.locator.latest_event_ref,
        to_state="closed",
        event_kind="closed",
    )

    closed = repository.publish_closed(
        ClosedPublicationInputsV2(
            EPISODE,
            completed,
            _released_receipt(),
            closed_event,
            "failed",
            "lease-7",
            ("child_verifier", "runtime", "workspace", "cache_holder", "lease_record"),
        )
    )

    recovered = repository.recover(EPISODE)
    assert recovered is not None
    assert recovered.closed_envelope == closed.envelope
    assert recovered.closed_envelope.verifier_cleanup_receipt is None


def test_foreign_completed_aggregate_with_victim_tombstone_ref_is_rejected() -> None:
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    repository, _, victim, victim_event = _publish_completed(cas, locators)
    _, _, foreign, _ = _publish_completed(
        cas,
        locators,
        episode_id="foreign-evidence-episode",
    )
    closed_event = _prepare_closed_event(repository, victim, victim_event)
    forged = dataclasses.replace(
        foreign,
        tombstone_ref=victim.tombstone_ref,
        locator=victim.locator,
    )
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)

    with pytest.raises(
        (EvidenceValidationError, LocatorConflictError),
        match="completed|current|episode",
    ):
        repository.publish_closed(
            ClosedPublicationInputsV2(
                EPISODE,
                forged,
                _released_receipt(),
                closed_event,
                "succeeded",
                "lease-7",
                (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                ),
                _verifier_released_receipt(),
                "verifier-lease-7",
                ("runtime", "workspace", "snapshot", "lease_record"),
            )
        )
    assert locators.get(EPISODE) == locator_before
    assert set(cas._refs_by_id) == refs_before


def test_export_redacts_embedded_bearer_auth_and_password_strings() -> None:
    payload = canonical_json_bytes(
        {
            "message": "Authorization: Bearer live-bearer-secret",
            "note": "auth=live-auth-secret",
            "detail": "password=live-password-secret",
        }
    )
    obj = _object(payload=payload)
    repository, cas, closed = _publish_closed(
        artifact_object=obj, artifact_payload=payload
    )

    exported = repository.export_closed(
        EPISODE, _export_authorization(repository, closed), ("trajectory",)
    )
    exported_ref = exported.exported_objects[0].artifact_ref
    rendered = cas.get_bytes(exported_ref).decode().lower()

    assert exported_ref != obj.artifact_ref
    for secret in ("live-bearer-secret", "live-auth-secret", "live-password-secret"):
        assert secret not in rendered


@pytest.mark.parametrize("damage", ("missing", "corrupt", "foreign"))
def test_recovery_rejects_missing_corrupt_or_foreign_quarantine_object(
    damage: str,
) -> None:
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    repository = EpisodeEvidenceRepository(cas, locators)
    accepted = _event()
    accepted_ref = repository.append_transition(accepted)
    victim_event = _event(
        1,
        previous=accepted,
        previous_ref=accepted_ref,
        to_state="quarantined",
        event_kind="quarantined",
    )
    repository.append_transition(victim_event)
    victim = repository.quarantine(
        QuarantinePublicationInputsV2(
            EPISODE,
            victim_event,
            SafeFailureFactV2("runner", "failed", "never", "before-run", detail="safe"),
        )
    )

    if damage == "missing":
        del cas._bytes_by_id[victim.quarantine_ref.artifact_id]
    elif damage == "corrupt":
        cas._bytes_by_id[victim.quarantine_ref.artifact_id] = b"{}"
    else:
        foreign_id = "foreign-quarantine-episode"
        foreign_accepted = _event(episode_id=foreign_id)
        foreign_accepted_ref = repository.append_transition(foreign_accepted)
        foreign_event = _event(
            1,
            episode_id=foreign_id,
            previous=foreign_accepted,
            previous_ref=foreign_accepted_ref,
            to_state="quarantined",
            event_kind="quarantined",
        )
        repository.append_transition(foreign_event)
        foreign = repository.quarantine(
            QuarantinePublicationInputsV2(
                foreign_id,
                foreign_event,
                SafeFailureFactV2(
                    "runner", "failed", "never", "before-run", detail="foreign"
                ),
            )
        )
        locators._records[EPISODE] = dataclasses.replace(
            victim.locator,
            quarantine_ref=foreign.quarantine_ref,
            checksum="",
        )

    with pytest.raises(
        EvidenceCorruptError, match="quarantine|recovery|digest|identity|integrity"
    ):
        repository.recover(EPISODE)


@pytest.mark.parametrize("locator_kind", ("memory", "filesystem"))
def test_closed_locator_rejects_mutation_and_transition_append(
    locator_kind: str,
    tmp_path: Path,
) -> None:
    locators = (
        InMemoryEpisodeLocatorStore()
        if locator_kind == "memory"
        else FilesystemEpisodeLocatorStore(tmp_path / "locators")
    )
    repository, cas, closed = _publish_closed(locators=locators)
    mutated = dataclasses.replace(
        closed.locator,
        generation=closed.locator.generation + 1,
        current_state="cleanup-retried",
        checksum="",
    )

    with pytest.raises(LocatorConflictError, match="closed|absorbing|immutable"):
        locators.compare_and_swap(EPISODE, closed.locator.generation, mutated)

    recovered = repository.recover(EPISODE)
    assert recovered is not None
    last_event = recovered.events[-1]
    refs_before = set(cas._refs_by_id)
    with pytest.raises(LocatorConflictError, match="closed|absorbing|terminal"):
        repository.append_transition(
            _event(
                last_event.sequence + 1,
                previous=last_event,
                previous_ref=closed.locator.latest_event_ref,
                to_state="accepted",
                event_kind="accepted",
            )
        )
    assert set(cas._refs_by_id) == refs_before
    assert repository.recover(EPISODE) == recovered


def test_repeated_completed_publication_is_exactly_idempotent_or_conflicts() -> None:
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    repository, _, first, event = _publish_completed(cas, locators)
    obj = _object()
    inputs = CompletedPublicationInputsV2(
        episode_id=EPISODE,
        create_fingerprint=_d("a"),
        run_fingerprint=_d("b"),
        create_response_bytes=b'{"created":true}',
        run_response_bytes=b'{"answer":"done"}',
        resolved_plan={
            "schema_version": "bb.rl.resolved-episode-plan.v2",
            "selection_digest": _d("2"),
            "effective_plan_digest": _d("3"),
            "effective_plan": {
                "effective_plan_digest": _d("3"),
                "artifacts": {
                    "allowed_roles": ["trajectory"],
                    "max_each_bytes": 4096,
                    "max_total_bytes": 4096,
                },
            },
        },
        policy_binding_digest=_d("4"),
        runner_result=_runner_result(plan_digest=_d("3")),
        materialization_receipt={"receipt": _d("5")},
        primary_measurement={"measurement": _d("6")},
        verifier_snapshot={"snapshot": _d("7")},
        verifier_measurement_digest=_d("a"),
        verifier_result={"result": _d("b")},
        evidence_objects=(obj,),
        evidence_policy={"record_digest": _d("c"), "required_roles": ["trajectory"]},
        retention_policy={"record_digest": _d("d")},
        lifecycle_head_ref=first.locator.latest_event_ref,
        lifecycle_head_digest=event.digest,
        primary_disposition="succeeded",
        reward_disposition="eligible",
        reward_components={"correct": 1},
        verifier_cleanup_receipt=_verifier_released_receipt(),
        verifier_lease_id="verifier-lease-7",
    )
    refs_before = set(cas._refs_by_id)

    assert repository.publish_completed(inputs) == first
    assert locators.get(EPISODE) == first.locator
    assert set(cas._refs_by_id) == refs_before

    with pytest.raises(LocatorConflictError, match="completed|conflict|immutable"):
        repository.publish_completed(
            dataclasses.replace(inputs, run_response_bytes=b'{"answer":"different"}')
        )
    assert locators.get(EPISODE) == first.locator
    assert set(cas._refs_by_id) == refs_before


@pytest.mark.parametrize(
    ("recorded_lease_id", "presented_lease_id"),
    (("primary-lease-a", "primary-lease-b"), (None, "primary-lease-b")),
)
def test_close_requires_the_canonical_lifecycle_primary_lease(
    recorded_lease_id: str | None,
    presented_lease_id: str,
) -> None:
    lifecycle_event = _event(
        event_kind="workspace_ready",
        primary_lease_id=recorded_lease_id or "primary-lease-a",
    )
    locators = InMemoryEpisodeLocatorStore()
    repository, cas, completed, event = _publish_completed(
        locators=locators,
        lifecycle_event=lifecycle_event,
    )
    closed_event = _prepare_closed_event(repository, completed, event)
    if recorded_lease_id is None:
        closed_event = dataclasses.replace(closed_event, primary_lease_id=None)
    receipt = SandboxCleanupReceipt.from_steps(
        presented_lease_id,
        tuple(
            CleanupStepReceipt(resource, CleanupState.RELEASED)
            for resource in (
                "child_verifier",
                "runtime",
                "workspace",
                "cache_holder",
                "lease_record",
            )
        ),
    )
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)

    with pytest.raises(
        EvidenceValidationError, match="primary.*lease|lease.*lifecycle"
    ):
        repository.publish_closed(
            ClosedPublicationInputsV2(
                EPISODE,
                completed,
                receipt,
                closed_event,
                "succeeded",
                presented_lease_id,
                (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                ),
                _verifier_released_receipt(),
                "verifier-lease-7",
                ("runtime", "workspace", "snapshot", "lease_record"),
            )
        )
    assert locators.get(EPISODE) == locator_before
    assert set(cas._refs_by_id) == refs_before


@pytest.mark.parametrize(
    ("authorization_value", "secrets"),
    (
        ("Basic dXNlcjpwYXNz", ("dXNlcjpwYXNz",)),
        (
            'Digest username="live-user", realm="private", response="live-response"',
            ("live-user", "private", "live-response"),
        ),
        ("Breadboard-Custom live-opaque-credential", ("live-opaque-credential",)),
    ),
)
def test_export_fully_redacts_every_authorization_scheme(
    authorization_value: str,
    secrets: tuple[str, ...],
) -> None:
    original = f"Authorization: {authorization_value}"
    payload = canonical_json_bytes({"message": original, "safe": "retained"})
    obj = _object(payload=payload)
    repository, cas, closed = _publish_closed(
        artifact_object=obj, artifact_payload=payload
    )

    exported = repository.export_closed(
        EPISODE, _export_authorization(repository, closed), ("trajectory",)
    )
    rendered = cas.get_bytes(exported.exported_objects[0].artifact_ref).decode()

    assert original not in rendered
    assert authorization_value not in rendered
    for secret in secrets:
        assert secret not in rendered
    assert "retained" in rendered


def test_publish_completed_rejects_runner_result_conflicting_with_durable_journal() -> (
    None
):
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    repository = EpisodeEvidenceRepository(cas, locators)
    obj = _object()
    cas.put_bytes(
        b'{"answer":"done"}',
        artifact_id=obj.artifact_ref.artifact_id,
        media_type=obj.artifact_ref.media_type,
        metadata=dict(obj.artifact_ref.metadata),
    )
    lifecycle_event, lifecycle_ref = _append_success_lifecycle(repository)
    journal_result = _runner_result(plan_digest=_d("3"))
    repository.append_runner_event(EPISODE, _d("3"), journal_result.events[0])
    conflicting_result = dataclasses.replace(
        journal_result,
        termination=RunnerTermination.MAX_TURNS,
        events=(
            dataclasses.replace(
                journal_result.events[0],
                reason=RunnerTermination.MAX_TURNS,
            ),
        ),
    )
    inputs = CompletedPublicationInputsV2(
        episode_id=EPISODE,
        create_fingerprint=_d("a"),
        run_fingerprint=_d("b"),
        create_response_bytes=b'{"created":true}',
        run_response_bytes=b'{"answer":"done"}',
        resolved_plan={
            "schema_version": "bb.rl.resolved-episode-plan.v2",
            "selection_digest": _d("2"),
            "effective_plan_digest": _d("3"),
            "effective_plan": {
                "effective_plan_digest": _d("3"),
                "artifacts": {
                    "allowed_roles": ["trajectory"],
                    "max_each_bytes": 4096,
                    "max_total_bytes": 4096,
                },
            },
        },
        policy_binding_digest=_d("4"),
        runner_result=conflicting_result,
        materialization_receipt={"receipt": _d("5")},
        primary_measurement={"measurement": _d("6")},
        verifier_snapshot={"snapshot": _d("7")},
        verifier_measurement_digest=_d("a"),
        verifier_result={"result": _d("b")},
        evidence_objects=(obj,),
        evidence_policy={"record_digest": _d("c"), "required_roles": ["trajectory"]},
        retention_policy={"record_digest": _d("d")},
        lifecycle_head_ref=lifecycle_ref,
        lifecycle_head_digest=lifecycle_event.digest,
        primary_disposition="succeeded",
        reward_disposition="eligible",
        reward_components={"correct": 1},
        verifier_cleanup_receipt=_verifier_released_receipt(),
        verifier_lease_id="verifier-lease-7",
    )
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)

    with pytest.raises(
        EvidenceValidationError, match="runner.*journal|journal.*result"
    ):
        repository.publish_completed(inputs)
    assert locators.get(EPISODE) == locator_before
    assert set(cas._refs_by_id) == refs_before


def test_close_rejects_verifier_cleanup_lease_claim_that_differs_from_persisted_receipt() -> (
    None
):
    locators = InMemoryEpisodeLocatorStore()
    repository, cas, completed, event = _publish_completed(locators=locators)
    closed_event = _prepare_closed_event(repository, completed, event)
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)

    with pytest.raises(EvidenceValidationError, match="cleanup.*lease|lease.*binding"):
        repository.publish_closed(
            ClosedPublicationInputsV2(
                EPISODE,
                completed,
                _released_receipt(),
                closed_event,
                "succeeded",
                "lease-7",
                (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                ),
                _verifier_released_receipt(),
                "wrong-verifier-lease",
                ("runtime", "workspace", "snapshot", "lease_record"),
            )
        )
    assert locators.get(EPISODE) == locator_before
    assert set(cas._refs_by_id) == refs_before


@pytest.mark.parametrize(
    ("pin_field", "payload"),
    (
        ("export_authorization_refs", b"[]"),
        (
            "export_authorization_refs",
            b'{"schema_version":"bb.rl.foreign-authorization.v2"}',
        ),
        ("redaction_decision_refs", b"[]"),
        (
            "redaction_decision_refs",
            b'{"schema_version":"bb.rl.foreign-redaction.v2"}',
        ),
    ),
)
def test_malformed_or_foreign_export_pins_fail_closed_with_typed_errors(
    pin_field: str,
    payload: bytes,
) -> None:
    locators = InMemoryEpisodeLocatorStore()
    repository, cas, completed, event = _publish_completed(locators=locators)
    closed_event = _prepare_closed_event(repository, completed, event)
    bad_ref = cas.put_bytes(
        payload,
        artifact_id=f"v2/{EPISODE}/bad-pin/{hashlib.sha256(payload).hexdigest()}",
        media_type=MEDIA_TYPE,
        metadata={"episode_id": EPISODE},
    )
    pins = {
        "export_authorization_refs": (),
        "redaction_decision_refs": (),
        pin_field: (bad_ref,),
    }
    locator_before = locators.get(EPISODE)
    refs_before = set(cas._refs_by_id)

    with pytest.raises(
        EvidenceValidationError, match="authorization|redaction|pin|schema"
    ):
        repository.publish_closed(
            ClosedPublicationInputsV2(
                EPISODE,
                completed,
                _released_receipt(),
                closed_event,
                "succeeded",
                "lease-7",
                (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                ),
                _verifier_released_receipt(),
                "verifier-lease-7",
                ("runtime", "workspace", "snapshot", "lease_record"),
                **pins,
            )
        )
    assert locators.get(EPISODE) == locator_before
    assert set(cas._refs_by_id) == refs_before

    legacy_repository, legacy_cas, closed = _publish_closed()
    legacy_bad_ref = legacy_cas.put_bytes(
        payload,
        artifact_id=f"v2/{EPISODE}/legacy-bad-pin/{pin_field}/{hashlib.sha256(payload).hexdigest()}",
        media_type=MEDIA_TYPE,
        metadata={"episode_id": EPISODE},
    )
    forged_envelope = dataclasses.replace(
        closed.envelope, **{pin_field: (legacy_bad_ref,)}
    )
    forged_envelope_ref = _store_schema(
        legacy_cas,
        EPISODE,
        f"forged-closed-envelope-{pin_field}",
        forged_envelope,
    )
    forged_tombstone = dataclasses.replace(
        closed.tombstone, envelope_ref=forged_envelope_ref
    )
    forged_tombstone_ref = _store_schema(
        legacy_cas,
        EPISODE,
        f"forged-closed-tombstone-{pin_field}",
        forged_tombstone,
    )
    legacy_repository._locators._records[EPISODE] = dataclasses.replace(
        closed.locator,
        closed_tombstone_ref=forged_tombstone_ref,
        checksum="",
    )

    with pytest.raises(
        (EvidenceCorruptError, ExportDeniedError),
        match="authorization|redaction|pin|schema|export|recovery|corrupt",
    ):
        legacy_repository.export_closed(
            EPISODE, _export_authorization(), ("trajectory",)
        )


@pytest.mark.parametrize("mismatch", ("manifest-completed", "completed-closed"))
def test_recovery_rejects_primary_disposition_mismatch_across_committed_graph(
    mismatch: str,
) -> None:
    if mismatch == "manifest-completed":
        repository, cas, completed, _ = _publish_completed()
        evidence = repository._load_evidence_manifest(
            completed.envelope.evidence_manifest_ref
        )
        forged_evidence = dataclasses.replace(evidence, primary_disposition="failed")
        forged_evidence_ref = _store_schema(
            cas,
            EPISODE,
            "forged-disposition-evidence",
            forged_evidence,
        )
        forged_envelope = dataclasses.replace(
            completed.envelope,
            evidence_manifest_ref=forged_evidence_ref,
            evidence_root=forged_evidence.lineage_root,
        )
        forged_envelope_ref = _store_schema(
            cas,
            EPISODE,
            "forged-disposition-completed-envelope",
            forged_envelope,
        )
        forged_tombstone = dataclasses.replace(
            completed.tombstone,
            envelope_ref=forged_envelope_ref,
        )
        forged_tombstone_ref = _store_schema(
            cas,
            EPISODE,
            "forged-disposition-completed-tombstone",
            forged_tombstone,
        )
        repository._locators._records[EPISODE] = dataclasses.replace(
            completed.locator,
            completed_tombstone_ref=forged_tombstone_ref,
            checksum="",
        )
    else:
        repository, cas, closed = _publish_closed()
        forged_envelope = dataclasses.replace(closed.envelope, primary_outcome="failed")
        forged_envelope_ref = _store_schema(
            cas,
            EPISODE,
            "forged-disposition-closed-envelope",
            forged_envelope,
        )
        forged_tombstone = dataclasses.replace(
            closed.tombstone,
            envelope_ref=forged_envelope_ref,
        )
        forged_tombstone_ref = _store_schema(
            cas,
            EPISODE,
            "forged-disposition-closed-tombstone",
            forged_tombstone,
        )
        repository._locators._records[EPISODE] = dataclasses.replace(
            closed.locator,
            closed_tombstone_ref=forged_tombstone_ref,
            checksum="",
        )

    with pytest.raises(
        EvidenceCorruptError, match="primary|disposition|outcome|recovery|graph"
    ):
        repository.recover(EPISODE)


def test_publish_completed_rejects_receipt_for_non_authoritative_verifier_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    observations: list[
        tuple[
            EpisodeLocatorRecordV2 | None,
            set[str],
            EpisodeLocatorRecordV2 | None,
            set[str],
        ]
    ] = []
    real_publish_completed = EpisodeEvidenceRepository.publish_completed

    def track_publication(
        repository: EpisodeEvidenceRepository,
        inputs: CompletedPublicationInputsV2,
    ) -> Any:
        locator_before = locators.get(EPISODE)
        refs_before = set(cas._refs_by_id)
        try:
            return real_publish_completed(repository, inputs)
        finally:
            observations.append(
                (
                    locator_before,
                    refs_before,
                    locators.get(EPISODE),
                    set(cas._refs_by_id),
                )
            )

    monkeypatch.setattr(
        EpisodeEvidenceRepository,
        "publish_completed",
        track_publication,
    )

    with pytest.raises(EvidenceValidationError, match="cleanup.*lease|lease.*binding"):
        _publish_completed(
            cas,
            locators,
            verifier_lease_id="authoritative-verifier-lease",
        )

    assert len(observations) == 1
    locator_before, refs_before, locator_after, refs_after = observations[0]
    assert locator_after == locator_before
    assert refs_after == refs_before
    recovered = EpisodeEvidenceRepository(cas, locators).recover(EPISODE)
    assert recovered is not None
    assert recovered.completed_envelope is None
    assert recovered.closed_envelope is None
    with pytest.raises(ExportDeniedError, match="closed|export"):
        EpisodeEvidenceRepository(cas, locators).export_closed(
            EPISODE,
            _export_authorization(),
            ("trajectory",),
        )


def test_publish_completed_binds_exact_selection_commit_token_and_rejects_string() -> (
    None
):
    binding = harness_contracts.SelectionBinding(
        owner_key=_d("8"),
        request_digest=_d("9"),
        selection_record_digest=_d("2"),
    )
    binding_bytes = binding.canonical_bytes()
    token = harness_contracts.SelectionCommitToken(
        binding=binding,
        binding_ref=harness_contracts.ArtifactRef(
            artifact_id=binding.canonical_digest(),
            sha256=binding.canonical_digest(),
            size_bytes=len(binding_bytes),
            media_type="application/vnd.breadboard.selection-binding+json;version=1",
        ),
        verified_at="2026-07-10T12:00:00Z",
    )

    repository, _, completed, _ = _publish_completed(selection_commit=token)
    manifest = repository._load_evidence_manifest(
        completed.envelope.evidence_manifest_ref
    )
    assert manifest.selection_digest == token.canonical_digest()

    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    with pytest.raises(
        EvidenceValidationError, match="selection.*commit|commit.*token"
    ):
        _publish_completed(cas, locators, selection_commit=_d("2"))
    recovered = EpisodeEvidenceRepository(cas, locators).recover(EPISODE)
    assert recovered is not None
    assert recovered.completed_envelope is None


@pytest.mark.parametrize(
    "assignment",
    (
        "secret=TOPSECRET",
        "secret: TOPSECRET",
        "credential=TOPSECRET",
        "credential : TOPSECRET",
        "SECRET = TOPSECRET",
        "Credential:TOPSECRET",
        "--secret=TOPSECRET",
        "--secret TOPSECRET",
        "-credential: TOPSECRET",
        "-credential TOPSECRET",
    ),
)
def test_export_redacts_secret_and_credential_assignments_without_overmatching_prose(
    assignment: str,
) -> None:
    payload = canonical_json_bytes(
        {
            "line": f"before {assignment} after",
            "neighboring_prose": (
                "secret garden; credential review; secretary; credentialing"
            ),
        }
    )
    obj = _object(payload=payload)
    repository, cas, closed = _publish_closed(
        artifact_object=obj,
        artifact_payload=payload,
    )

    exported = repository.export_closed(
        EPISODE,
        _export_authorization(repository, closed),
        ("trajectory",),
    )
    rendered = cas.get_bytes(exported.exported_objects[0].artifact_ref).decode()

    assert "TOPSECRET" not in rendered
    assert f"before {assignment.replace('TOPSECRET', '[REDACTED]')} after" in rendered
    for safe_text in (
        "secret garden",
        "credential review",
        "secretary",
        "credentialing",
    ):
        assert safe_text in rendered


def test_recovery_returns_verified_verifier_cleanup_proof_or_explicit_absence() -> None:
    completed_repository, _, _, _ = _publish_completed()
    recovered_completed = completed_repository.recover(EPISODE)
    assert recovered_completed is not None
    assert recovered_completed.verifier_cleanup_receipt == _verifier_released_receipt()
    assert recovered_completed.verifier_lease_id == "verifier-lease-7"

    closed_repository, _, _ = _publish_closed()
    recovered_closed = closed_repository.recover(EPISODE)
    assert recovered_closed is not None
    assert recovered_closed.verifier_cleanup_receipt == _verifier_released_receipt()
    assert recovered_closed.verifier_lease_id == "verifier-lease-7"

    no_verifier_repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    no_run = canonical_digest({"episode_id": EPISODE, "run": "not-started"})
    failed_event, failed_event_ref = _append_failure_lifecycle(
        no_verifier_repository, run_fingerprint=no_run
    )
    no_verifier_repository.publish_failed_completed(
        FailedCompletedPublicationInputsV2(
            episode_id=EPISODE,
            create_fingerprint=_d("a"),
            run_fingerprint=no_run,
            create_response_bytes=b'{"created":true}',
            run_response_bytes=canonical_json_bytes(
                {"run": "not-started", "run_fingerprint": no_run}
            ),
            lifecycle_head_ref=failed_event_ref,
            lifecycle_head_digest=failed_event.digest,
            primary_disposition="failed",
            primary_failure=SafeFailureFactV2(
                "runner",
                "failed",
                "never",
                "before-run",
                detail="safe",
            ),
            session_close_failure=None,
            verifier_cleanup_failure=None,
            runner_event_refs=(),
            resolved_plan=None,
            policy_binding_digest=None,
            materialization_receipt=None,
            primary_measurement=None,
            verifier_snapshot=None,
            verifier_measurement_digest=None,
            verifier_result=None,
            verifier_cleanup_receipt=None,
            verifier_lease_id=None,
        )
    )
    recovered_without_verifier = no_verifier_repository.recover(EPISODE)
    assert recovered_without_verifier is not None
    assert recovered_without_verifier.verifier_cleanup_receipt is None
    assert recovered_without_verifier.verifier_lease_id is None


def test_public_completed_evidence_projections_are_exact_and_absent_before_completion() -> (
    None
):
    repository, _, publication, _ = _publish_completed()
    manifest = repository._load_evidence_manifest(
        publication.envelope.evidence_manifest_ref
    )

    assert publication.evidence_manifest == manifest
    assert publication.result_ref == publication.envelope.run_response_ref
    assert (
        publication.evidence_manifest_ref == publication.envelope.evidence_manifest_ref
    )
    assert publication.evidence_root == publication.envelope.evidence_root
    assert publication.artifact_manifest_ref == manifest.artifact_manifest_ref
    assert publication.primary_measurement_digest == manifest.primary_measurement_digest
    assert (
        publication.verifier_measurement_digest == manifest.verifier_measurement_digest
    )
    assert publication.verifier_result_digest == manifest.verifier_result_digest

    recovered_completed = repository.recover(EPISODE)
    assert recovered_completed is not None
    assert recovered_completed.evidence_manifest == manifest
    assert recovered_completed.result_ref == publication.envelope.run_response_ref
    assert (
        recovered_completed.evidence_manifest_ref
        == publication.envelope.evidence_manifest_ref
    )
    assert recovered_completed.evidence_root == publication.envelope.evidence_root
    assert recovered_completed.artifact_manifest_ref == manifest.artifact_manifest_ref
    assert (
        recovered_completed.primary_measurement_digest
        == manifest.primary_measurement_digest
    )
    assert (
        recovered_completed.verifier_measurement_digest
        == manifest.verifier_measurement_digest
    )
    assert recovered_completed.verifier_result_digest == manifest.verifier_result_digest

    closed_repository, _, closed = _publish_closed()
    recovered_closed = closed_repository.recover(EPISODE)
    assert recovered_closed is not None
    closed_manifest = closed_repository._load_evidence_manifest(
        recovered_closed.completed_envelope.evidence_manifest_ref
    )
    assert recovered_closed.evidence_manifest == closed_manifest
    assert (
        recovered_closed.result_ref
        == recovered_closed.completed_envelope.run_response_ref
    )
    assert (
        recovered_closed.evidence_manifest_ref
        == recovered_closed.completed_envelope.evidence_manifest_ref
    )
    assert (
        recovered_closed.evidence_root
        == recovered_closed.completed_envelope.evidence_root
    )
    assert (
        recovered_closed.artifact_manifest_ref == closed_manifest.artifact_manifest_ref
    )
    assert (
        recovered_closed.primary_measurement_digest
        == closed_manifest.primary_measurement_digest
    )
    assert (
        recovered_closed.verifier_measurement_digest
        == closed_manifest.verifier_measurement_digest
    )
    assert (
        recovered_closed.verifier_result_digest
        == closed_manifest.verifier_result_digest
    )
    assert (
        closed.envelope.completed_envelope_ref
        == recovered_closed.closed_envelope.completed_envelope_ref
    )

    pending_repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    pending_repository.append_transition(_event(to_state="accepted"))
    recovered_pending = pending_repository.recover(EPISODE)
    assert recovered_pending is not None
    for field_name in (
        "result_ref",
        "evidence_manifest_ref",
        "evidence_root",
        "artifact_manifest_ref",
        "primary_measurement_digest",
        "verifier_measurement_digest",
        "verifier_result_digest",
    ):
        assert getattr(recovered_pending, field_name) is None
    assert recovered_pending.evidence_manifest is None


def _authority_case(
    *,
    allowed_roles: tuple[str, ...],
    required_roles: tuple[str, ...],
    max_each_bytes: int = 4096,
    max_total_bytes: int = 8192,
) -> tuple[
    V2EvidenceAuthority,
    EvidenceAuthorityPlanV2,
    Any,
]:
    from tests.rl.harness.v2_service_fixtures import conductor_compatible_case

    _, _, resolved, _ = conductor_compatible_case()
    effective_plan = resolved.effective_plan
    artifacts = harness_contracts.ArtifactPolicyGrant(
        allowed_roles=allowed_roles,
        max_each_bytes=max_each_bytes,
        max_total_bytes=max_total_bytes,
    )
    capabilities_value = effective_plan.effective_capabilities.model_dump(mode="json")
    capabilities_value["artifacts"] = artifacts.model_dump(mode="json")
    capabilities = harness_contracts.CapabilityVector.model_validate(capabilities_value)
    effective_plan_value = effective_plan.model_dump(mode="json")
    effective_plan_value.update(
        {
            "artifacts": artifacts.model_dump(mode="json"),
            "effective_capabilities": capabilities.model_dump(mode="json"),
            "effective_capability_digest": capabilities.canonical_digest(),
        }
    )
    effective_plan = harness_contracts.EffectiveExecutionPlan.model_validate(
        effective_plan_value
    )
    sources = {
        "runner_transcript": EvidenceRoleSourceV2.RUNNER_RESULT,
        "snapshot_receipt": EvidenceRoleSourceV2.VERIFIER_SNAPSHOT_RECEIPT,
        "verifier_report": EvidenceRoleSourceV2.VERIFIER_RESULT,
    }
    authority = V2EvidenceAuthority(
        EvidenceRoleBindingV2(
            role=role,
            source=sources[role],
            producer_id=f"producer-{role}",
            producer_implementation_digest=_d(
                {
                    "runner_transcript": "4",
                    "snapshot_receipt": "5",
                    "verifier_report": "6",
                }[role]
            ),
        )
        for role in allowed_roles
    )
    authority_plan = authority.validate_plan(
        effective_plan,
        harness_contracts.EvidencePolicyRegistryRecord(
            policy=effective_plan.evidence,
            required_roles=required_roles,
        ),
        harness_contracts.RetentionPolicyRegistryRecord(
            grant=harness_contracts.RetentionPolicyGrant(
                policy=effective_plan.retention,
                minimum_seconds=0,
                maximum_seconds=86_400,
            )
        ),
    )
    return authority, authority_plan, effective_plan


def _snapshot_receipt(plan_digest: str) -> VerifierSnapshotReceipt:
    return VerifierSnapshotReceipt(
        snapshot_id="snapshot-authoritative",
        source_workspace_id="workspace-sealed",
        source_lease_id="lease-authoritative",
        effective_plan_digest=plan_digest,
        task_digest=_d("7"),
        verifier_digest=_d("8"),
        manifest_digest=_d("9"),
        root_digest=_d("a"),
        file_count=2,
        inode_count=3,
        byte_count=41,
        immutable_storage_object_id="cas/snapshot-authoritative",
    )


def test_typed_evidence_authority_materializes_required_and_optional_roles_from_exact_sources() -> (
    None
):
    roles = ("runner_transcript", "snapshot_receipt", "verifier_report")
    authority, plan, effective_plan = _authority_case(
        allowed_roles=roles,
        required_roles=("runner_transcript", "snapshot_receipt"),
    )
    runner_result = _runner_result(plan_digest=effective_plan.canonical_digest())
    snapshot = _snapshot_receipt(effective_plan.canonical_digest())

    without_optional = authority.materialize(
        plan,
        runner_result=runner_result,
        verifier_snapshot=snapshot,
        verifier_result={"score": 1},
    )
    with_optional = authority.materialize(
        plan,
        runner_result=runner_result,
        verifier_snapshot=snapshot,
        verifier_result={"verifier_report": {"score": 1}},
    )

    assert tuple(item.role for item in without_optional) == (
        "runner_transcript",
        "snapshot_receipt",
    )
    assert tuple(item.role for item in with_optional) == roles
    by_role = {item.role: item for item in with_optional}
    runner_payload = json.loads(by_role["runner_transcript"].payload)
    assert by_role["runner_transcript"].payload == canonical_json_bytes(runner_payload)
    assert runner_payload["episode_id"] == runner_result.episode_id
    assert (
        runner_payload["effective_plan_digest"] == runner_result.effective_plan_digest
    )
    assert runner_payload["response"] == {"answer": "done"}
    assert runner_payload["termination"] == runner_result.termination.value
    assert by_role["snapshot_receipt"].payload == canonical_json_bytes(
        dataclasses.asdict(snapshot)
    )
    assert by_role["verifier_report"].payload == b'{"score":1}'
    assert {
        item.role: (
            item.source,
            item.producer_id,
            item.producer_implementation_digest,
        )
        for item in with_optional
    } == {
        binding.role: (
            binding.source,
            binding.producer_id,
            binding.producer_implementation_digest,
        )
        for binding in plan.bindings
    }
    assert set(EvidenceObjectInputV2.__dataclass_fields__) == {
        "role",
        "source",
        "producer_id",
        "producer_implementation_digest",
        "payload",
        "media_type",
        "parent_digests",
    }
    assert not any(
        field in EvidenceObjectInputV2.__dataclass_fields__
        for field in ("artifact_ref", "workspace_path", "source_path", "file_path")
    )


def test_required_verifier_role_cannot_be_silently_omitted() -> None:
    authority, plan, effective_plan = _authority_case(
        allowed_roles=("verifier_report",),
        required_roles=("verifier_report",),
    )
    with pytest.raises(EvidenceValidationError, match="required.*absent"):
        authority.materialize(
            plan,
            runner_result=_runner_result(plan_digest=effective_plan.canonical_digest()),
            verifier_snapshot=_snapshot_receipt(effective_plan.canonical_digest()),
            verifier_result={},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("source", "source|producer"),
        ("producer_id", "source|producer"),
        ("producer_implementation_digest", "source|producer"),
        ("role", "unknown|role"),
        ("each_limit", "per-role"),
        ("total_limit", "total"),
    ),
)
def test_repository_materializes_owned_bytes_only_under_exact_role_source_producer_and_limits(
    mutation: str,
    message: str,
) -> None:
    authority, plan, effective_plan = _authority_case(
        allowed_roles=("runner_transcript", "snapshot_receipt"),
        required_roles=("runner_transcript",),
    )
    inputs = authority.materialize(
        plan,
        runner_result=_runner_result(plan_digest=effective_plan.canonical_digest()),
        verifier_snapshot=_snapshot_receipt(effective_plan.canonical_digest()),
        verifier_result={},
    )
    if mutation == "source":
        inputs = (
            dataclasses.replace(
                inputs[0],
                source=EvidenceRoleSourceV2.VERIFIER_RESULT,
            ),
            *inputs[1:],
        )
    elif mutation == "producer_id":
        inputs = (
            dataclasses.replace(inputs[0], producer_id="foreign-producer"),
            *inputs[1:],
        )
    elif mutation == "producer_implementation_digest":
        inputs = (
            dataclasses.replace(
                inputs[0],
                producer_implementation_digest=_d("0"),
            ),
            *inputs[1:],
        )
    elif mutation == "role":
        inputs = (
            dataclasses.replace(inputs[0], role="workspace_file"),
            *inputs[1:],
        )
    elif mutation == "each_limit":
        plan = dataclasses.replace(
            plan,
            max_each_bytes=len(inputs[0].payload) - 1,
        )
    else:
        plan = dataclasses.replace(
            plan,
            max_each_bytes=max(len(item.payload) for item in inputs),
            max_total_bytes=sum(len(item.payload) for item in inputs) - 1,
        )

    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    with pytest.raises(EvidenceValidationError, match=message):
        repository.publish_evidence_objects(EPISODE, plan, tuple(inputs))


def test_repository_publishes_exact_authority_owned_bytes_and_pins_each_role() -> None:
    authority, plan, effective_plan = _authority_case(
        allowed_roles=("runner_transcript", "snapshot_receipt"),
        required_roles=("runner_transcript",),
    )
    inputs = authority.materialize(
        plan,
        runner_result=_runner_result(plan_digest=effective_plan.canonical_digest()),
        verifier_snapshot=_snapshot_receipt(effective_plan.canonical_digest()),
        verifier_result={},
    )
    cas = InMemoryCAS()
    repository = EpisodeEvidenceRepository(cas, InMemoryEpisodeLocatorStore())

    objects = repository.publish_evidence_objects(EPISODE, plan, inputs)

    assert tuple(item.role for item in objects) == (
        "runner_transcript",
        "snapshot_receipt",
    )
    assert tuple(cas.get_bytes(item.artifact_ref) for item in objects) == tuple(
        item.payload for item in inputs
    )
    assert all(
        item.authorization_policy_ref == plan.evidence_policy_ref for item in objects
    )
    assert all(
        item.retention_policy_ref == plan.retention_policy_ref for item in objects
    )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("to_state", "unknown", "unknown lifecycle state"),
        ("from_state", "unknown", "unknown lifecycle state"),
    ),
)
def test_lifecycle_rejects_exact_unknown_state_and_from_state(
    field_name: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(EvidenceValidationError, match=message):
        dataclasses.replace(_event(), **{field_name: value})


@pytest.mark.parametrize(
    ("to_state", "event_kind"),
    (
        ("running", "run_started"),
        ("allocating", "wrong-kind"),
    ),
)
def test_lifecycle_rejects_exact_illegal_edge_and_kind(
    to_state: str,
    event_kind: str,
) -> None:
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    accepted = _event()
    accepted_ref = repository.append_transition(accepted)
    illegal = _event(
        1,
        previous=accepted,
        previous_ref=accepted_ref,
        to_state=to_state,
        event_kind=event_kind,
    )
    with pytest.raises(EvidenceValidationError, match="illegal lifecycle transition"):
        repository.append_transition(illegal)


def test_lifecycle_rejects_exact_replay_without_advancing_locator() -> None:
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    accepted = _event()
    repository.append_transition(accepted)
    locator_before = repository._locators.get(EPISODE)
    with pytest.raises(EvidenceValidationError, match="continuity"):
        repository.append_transition(accepted)
    assert repository._locators.get(EPISODE) == locator_before


@pytest.mark.parametrize("failure_stage", ("event", "envelope", "tombstone", "locator"))
def test_atomic_close_failure_never_claims_closed(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    locators = InMemoryEpisodeLocatorStore()
    repository, _, completed, event = _publish_completed(locators=locators)
    pins = repository.prepare_export_pins(
        EPISODE,
        completed,
        subject_digest=_d("e"),
    )
    closed_event = _prepare_closed_event(repository, completed, event)
    closing = locators.get(EPISODE)
    assert closing is not None and closing.current_state == "closing"
    inputs = ClosedPublicationInputsV2(
        episode_id=EPISODE,
        completed=completed,
        cleanup_receipt=_released_receipt(),
        closed_event=closed_event,
        final_primary_outcome="succeeded",
        cleanup_lease_id="lease-7",
        cleanup_required_resources=(
            "child_verifier",
            "runtime",
            "workspace",
            "cache_holder",
            "lease_record",
        ),
        verifier_cleanup_receipt=_verifier_released_receipt(),
        verifier_cleanup_lease_id="verifier-lease-7",
        verifier_cleanup_required_resources=(
            "runtime",
            "workspace",
            "snapshot",
            "lease_record",
        ),
        export_authorization_refs=pins.authorization_refs,
        redaction_decision_refs=pins.redaction_decision_refs,
    )
    if failure_stage == "locator":
        real_compare_and_swap = locators.compare_and_swap

        def fail_closed_locator(
            episode_id: str,
            expected_generation: int | None,
            record: EpisodeLocatorRecordV2,
        ) -> None:
            if record.current_state == "closed":
                raise OSError("deterministic locator close failure")
            real_compare_and_swap(episode_id, expected_generation, record)

        monkeypatch.setattr(locators, "compare_and_swap", fail_closed_locator)
    else:
        real_put = repository._put
        suffix_by_stage = {
            "event": f"event-{closed_event.sequence}",
            "envelope": "closed-envelope",
            "tombstone": "closed-tombstone",
        }

        def fail_close_object(
            schema: str,
            episode_id: str,
            value: Any,
            suffix: str,
        ) -> ArtifactRef:
            if suffix == suffix_by_stage[failure_stage]:
                raise OSError(f"deterministic {failure_stage} close failure")
            return real_put(schema, episode_id, value, suffix)

        monkeypatch.setattr(repository, "_put", fail_close_object)
    with pytest.raises(OSError, match="deterministic"):
        repository.publish_closed(inputs)
    locator = locators.get(EPISODE)
    assert locator == closing
    assert locator.closed_tombstone_ref is None
    recovered = repository.recover(EPISODE)
    assert recovered is not None
    assert recovered.locator.current_state == "closing"
    assert recovered.closed_envelope is None
    assert recovered.closed_tombstone is None


def test_locator_scan_preserves_valid_neighbors_and_blocks_corrupt_id_reuse(
    tmp_path: Path,
) -> None:
    store = FilesystemEpisodeLocatorStore(tmp_path)
    before_id = "episode-before"
    corrupt_id = "episode-corrupt"
    after_id = "episode-after"
    for episode_id in (before_id, corrupt_id, after_id):
        store.compare_and_swap(episode_id, None, _locator(episode_id))
    corrupt_path = tmp_path / f"{corrupt_id}.json"
    corrupt_value = json.loads(corrupt_path.read_bytes())
    corrupt_value["current_state"] = "closed"
    corrupt_path.write_bytes(canonical_json_bytes(corrupt_value))

    entries = store.scan()
    assert tuple(entry.episode_id_hint for entry in entries) == (
        after_id,
        before_id,
        corrupt_id,
    )
    corrupt_entry = next(
        entry for entry in entries if entry.episode_id_hint == corrupt_id
    )
    assert corrupt_entry.record is None
    assert corrupt_entry.failure is not None
    assert {record.episode_id for record in store.enumerate()} == {before_id, after_id}

    store.quarantine_corrupt(corrupt_entry, corrupt_entry.failure)
    assert store.get(before_id) == _locator(before_id)
    assert store.get(after_id) == _locator(after_id)
    with pytest.raises(EvidenceCorruptError, match="quarantined"):
        store.get(corrupt_id)
    with pytest.raises(EvidenceCorruptError, match="quarantined"):
        store.compare_and_swap(corrupt_id, None, _locator(corrupt_id))


@pytest.mark.parametrize(
    ("unsafe_url", "credentials"),
    (
        ("http://user:pass@example.invalid/path", ("user", "pass")),
        ("HTTPS://Admin:S3cret@example.invalid/path", ("Admin", "S3cret")),
        ("postgres://dbuser:dbpass@example.invalid/database", ("dbuser", "dbpass")),
        ("ssh://deploy:key@example.invalid/repository", ("deploy", "key")),
        ("git://gituser:gittoken@example.invalid/project", ("gituser", "gittoken")),
        ("ftp://ftpuser:ftppass@example.invalid/file", ("ftpuser", "ftppass")),
        (
            "CuStOm+V1://owner:credential@example.invalid/resource",
            ("owner", "credential"),
        ),
        ("https://us%65r:p%40ss@example.invalid/encoded", ("us%65r", "p%40ss")),
    ),
)
def test_export_redacts_url_userinfo_for_every_scheme_without_overmatching(
    unsafe_url: str,
    credentials: tuple[str, str],
) -> None:
    safe_urls = (
        "https://example.invalid/public",
        "POSTGRES://example.invalid/database",
        "custom+v1://example.invalid/resource",
    )
    safe_prose = "user guidance: pass through the public URL without credentials"
    payload = canonical_json_bytes(
        {
            "unsafe_url": unsafe_url,
            "safe_urls": safe_urls,
            "safe_prose": safe_prose,
        }
    )
    obj = _object(payload=payload)
    repository, cas, closed = _publish_closed(
        artifact_object=obj,
        artifact_payload=payload,
    )

    exported = repository.export_closed(
        EPISODE,
        _export_authorization(repository, closed),
        ("trajectory",),
    )
    rendered = cas.get_bytes(exported.exported_objects[0].artifact_ref).decode()

    assert unsafe_url not in rendered
    scheme = unsafe_url.split("://", 1)[0]
    assert f"{scheme}://example.invalid" in rendered
    assert "@example.invalid" not in rendered
    assert f"{credentials[0]}:{credentials[1]}@" not in rendered
    for safe_url in safe_urls:
        assert safe_url in rendered
    assert safe_prose in rendered


def test_export_post_redaction_scan_fails_closed_if_transform_leaves_url_userinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = canonical_json_bytes(
        {"url": "custom://post-scan-user:post-scan-pass@example.invalid/resource"}
    )
    obj = _object(payload=payload)
    repository, _, closed = _publish_closed(
        artifact_object=obj,
        artifact_payload=payload,
    )
    monkeypatch.setattr(
        evidence_module,
        "_redact_export_value",
        lambda value, *, sensitive=False: value,
    )

    with pytest.raises(ExportDeniedError, match="unsafe|secret|redact"):
        repository.export_closed(
            EPISODE,
            _export_authorization(repository, closed),
            ("trajectory",),
        )


def test_per_role_export_pins_roundtrip_mismatch_and_redaction() -> None:
    payload = canonical_json_bytes(
        {
            "authorization": "Bearer per-role-secret",
            "safe": "retained",
        }
    )
    objects = (
        _object("trajectory", payload=payload),
        _object("verifier", payload=payload),
    )
    repository, cas, closed = _publish_closed(
        artifact_objects=objects,
        artifact_payload=payload,
    )
    recovered = repository.recover(EPISODE)
    assert recovered is not None and recovered.closed_envelope is not None
    assert (
        recovered.closed_envelope.export_authorization_refs
        == closed.envelope.export_authorization_refs
    )
    assert (
        recovered.closed_envelope.redaction_decision_refs
        == closed.envelope.redaction_decision_refs
    )
    authorizations = tuple(
        repository._load_export_authorization(ref)
        for ref in closed.envelope.export_authorization_refs
    )
    by_role = {
        authorization.allowed_roles[0]: authorization
        for authorization in authorizations
    }
    assert set(by_role) == {"trajectory", "verifier"}
    for role, authorization in by_role.items():
        exported = repository.export_closed(EPISODE, authorization, (role,))
        assert tuple(item.role for item in exported.exported_objects) == (role,)
        rendered = cas.get_bytes(exported.exported_objects[0].artifact_ref).decode()
        assert "per-role-secret" not in rendered
        assert "retained" in rendered
    with pytest.raises(ExportDeniedError, match="authorized"):
        repository.export_closed(EPISODE, by_role["trajectory"], ("verifier",))


def test_filesystem_locator_close_is_idempotent(tmp_path: Path) -> None:
    store = FilesystemEpisodeLocatorStore(tmp_path)
    descriptors = (store._root_fd, store._quarantine_fd)
    store.close()
    store.close()
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(EvidenceCorruptError, match="closed"):
        store.get(EPISODE)
    with pytest.raises(EvidenceCorruptError, match="closed"):
        store.scan()
    with pytest.raises(EvidenceCorruptError, match="closed"):
        store.enumerate()


def test_filesystem_locator_close_waits_for_active_descriptor_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemEpisodeLocatorStore(tmp_path)
    store.compare_and_swap(EPISODE, None, _locator(EPISODE))
    entered = threading.Event()
    release = threading.Event()
    original = store._validate_root
    result: list[EpisodeLocatorRecordV2 | None] = []

    def paused_validate() -> None:
        entered.set()
        assert release.wait(timeout=5)
        original()

    monkeypatch.setattr(store, "_validate_root", paused_validate)
    reader = threading.Thread(target=lambda: result.append(store.get(EPISODE)))
    reader.start()
    assert entered.wait(timeout=5)
    closing = threading.Thread(target=store.close)
    closing.start()
    time.sleep(0.02)
    assert closing.is_alive()
    release.set()
    reader.join(timeout=5)
    closing.join(timeout=5)
    assert result == [_locator(EPISODE)]
    assert not reader.is_alive()
    assert not closing.is_alive()
    with pytest.raises(EvidenceCorruptError, match="closed"):
        store.get(EPISODE)


def test_e6_export_packet_recursively_redacts_seeded_secrets_and_filesystem_paths() -> (
    None
):
    seeded_secrets = (
        "e6-bearer-seed",
        "e6-password-seed",
        "e6-assignment-seed",
        "e6-user:e6-pass",
    )
    raw_paths = (
        "/Users/e6-seed/.ssh/id_ed25519",
        "~/private/e6-token.json",
        r"C:\Users\E6Seed\AppData\secret.txt",
        r"\\e6-server\private\evidence.key",
        "file:///Users/e6-seed/private/evidence.json",
    )
    raw_roots = (
        "/",
        "~/",
        "C:\\",
    )
    safe_controls = (
        "https://example.invalid/public/evidence",
        "workspace/results/evidence.json",
        "ordinary evidence prose remains visible",
    )
    payload = canonical_json_bytes(
        {
            "nested": [
                {
                    "authorization": f"Bearer {seeded_secrets[0]}",
                    "location": f"read {raw_paths[0]}, then ({raw_paths[1]}).",
                },
                {
                    "password": seeded_secrets[1],
                    "windows": f"inputs: {raw_paths[2]}; backup: {raw_paths[3]}!",
                    "file_url": raw_paths[4],
                    "roots": raw_roots,
                },
            ],
            "assignment": f"token={seeded_secrets[2]}",
            "credential_url": f"https://{seeded_secrets[3]}@example.invalid/private",
            "safe": safe_controls,
        }
    )
    metadata = {
        "nested": {
            "locations": [
                f"source={raw_paths[0]}",
                f"home ({raw_paths[1]}), drive [{raw_paths[2]}], unc {raw_paths[3]}.",
                f"file projection source {raw_paths[4]}",
                *raw_roots,
            ],
            "authorization": f"Bearer {seeded_secrets[0]}",
        },
        "safe": list(safe_controls),
    }
    source_object = _object(payload=payload, metadata=metadata)
    repository, cas, closed = _publish_closed(
        artifact_object=source_object,
        artifact_payload=payload,
    )

    exported = repository.export_closed(
        EPISODE,
        _export_authorization(repository, closed),
        ("trajectory",),
    )
    exported_object = exported.exported_objects[0]
    assert exported_object.artifact_ref.sha256 != source_object.artifact_ref.sha256

    packet_values = [
        json.loads(exported.canonical_bytes()),
        exported_object.artifact_ref.metadata,
        *(
            json.loads(cas.get_bytes(item.artifact_ref))
            for item in exported.exported_objects
        ),
    ]

    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [
                text
                for key, item in value.items()
                for text in (*strings(key), *strings(item))
            ]
        if isinstance(value, list):
            return [text for item in value for text in strings(item)]
        return []

    packet_strings = [text for value in packet_values for text in strings(value)]
    rendered_packet = "\n".join(packet_strings)
    manifest_bytes = exported.canonical_bytes()
    for seed in seeded_secrets:
        assert seed not in rendered_packet
        assert seed.encode() not in manifest_bytes
    for raw_path in raw_paths:
        assert all(raw_path not in text for text in packet_strings)
    for raw_root in raw_roots:
        assert raw_root not in packet_strings
    for safe_control in safe_controls:
        assert safe_control in rendered_packet
    assert "[REDACTED_PATH]" in rendered_packet


@pytest.mark.parametrize(
    ("unsafe", "expected"),
    (
        (
            "embedded POSIX path (/Users/alice/.ssh/id_rsa), preserved punctuation",
            "embedded POSIX path ([REDACTED_PATH]), preserved punctuation",
        ),
        (
            "home path '~/Library/ApplicationSupport/key.json'; next",
            "home path '[REDACTED_PATH]'; next",
        ),
        (
            r"drive path [C:\Users\Alice\secret.txt], next",
            "drive path [[REDACTED_PATH]], next",
        ),
        (
            r"UNC path \\server\share\private.key! next",
            "UNC path [REDACTED_PATH]! next",
        ),
        ("/", "[REDACTED_PATH]"),
        ("~/", "[REDACTED_PATH]"),
        ("C:\\", "[REDACTED_PATH]"),
        (
            "file:///Users/alice/private/evidence.json",
            "[REDACTED_PATH]",
        ),
        (
            "label path:/Users/alice/private/evidence.json; next",
            "label path:[REDACTED_PATH]; next",
        ),
    ),
)
def test_e6_filesystem_path_classifier_redacts_embedded_tokens_and_preserves_punctuation(
    unsafe: str,
    expected: str,
) -> None:
    assert evidence_module._contains_export_hazard(unsafe)
    transformed = evidence_module._redact_export_value(unsafe)
    assert transformed == expected
    assert not evidence_module._contains_export_hazard(transformed)


@pytest.mark.parametrize(
    "safe",
    (
        "https://example.invalid/Users/alice/public",
        "custom+v1://example.invalid/C:/public",
        "workspace/results/evidence.json",
        "ordinary evidence prose with no filesystem location",
        "version 2/3 remains a benign fraction",
        "choose and/or without treating the slash as a path",
        "arithmetic 1 / 2 remains readable",
    ),
)
def test_e6_filesystem_path_classifier_preserves_url_relative_and_prose_controls(
    safe: str,
) -> None:
    assert evidence_module._redact_export_value(safe) == safe
    assert not evidence_module._contains_export_hazard(safe)


@pytest.mark.parametrize(
    "raw_path",
    (
        "/",
        "~/",
        "C:\\",
        "file:///Users/postcondition-seed/.ssh/id_ed25519",
        "path:/Users/postcondition-seed/.ssh/id_ed25519",
    ),
)
def test_e6_filesystem_path_postcondition_fails_closed_before_projection(
    raw_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = canonical_json_bytes({"neutral": raw_path})
    source_object = _object(payload=payload)
    repository, cas, closed = _publish_closed(
        artifact_object=source_object,
        artifact_payload=payload,
    )
    refs_before = set(cas._refs_by_id)
    monkeypatch.setattr(
        evidence_module,
        "_redact_filesystem_paths",
        lambda value: value,
    )

    with pytest.raises(ExportDeniedError, match="redact|hazard"):
        repository.export_closed(
            EPISODE,
            _export_authorization(repository, closed),
            ("trajectory",),
        )
    assert set(cas._refs_by_id) == refs_before
