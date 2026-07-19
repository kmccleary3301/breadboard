from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
import pytest

from breadboard.rl.harness import service as service_module
from breadboard.rl.harness.evidence import (
    ClosedEpisodeEnvelopeV2,
    CompletedEpisodeEnvelopeV2,
    EpisodeClosedTombstoneV2,
    EpisodeCompletedTombstoneV2,
    EpisodeLocatorRecordV2,
    EvidenceCorruptError,
    ExecutionEvidenceManifestV2,
    LineageNodeV2,
    RecoveredEpisodeV2,
    EpisodeEvidenceRepository,
    InMemoryEpisodeLocatorStore,
    canonical_digest,
)
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2EpisodeConflict,
    V2EpisodeQuarantined,
    V2LifecycleDependencies,
    V2OperationDisposition,
)
from breadboard.rl.state.cas import InMemoryCAS
from breadboard.rl.state.state_ref import ArtifactRef
from tests.rl.harness.v2_service_fixtures import (
    cancellation_fingerprint,
    canonical_create_response_bytes,
    cleanup_receipt_projection,
    deterministic_clock,
    deterministic_sandbox_plan,
    failed_receipt,
    quarantined_receipt,
    ref,
    released_receipt,
    service_case,
)

pytestmark = pytest.mark.asyncio


def _build_service(monkeypatch: pytest.MonkeyPatch, case):
    monkeypatch.setattr(
        service_module,
        "build_sandbox_execution_plan",
        lambda request, registries, installed_authorities: deterministic_sandbox_plan(),
    )
    return BreadBoardV2EpisodeService(
        V2LifecycleDependencies(
            config_runtime=case.config,
            runner_registry=case.registry,
            sandbox_runtime=case.sandbox,
            policy_client_resolver=case.policy_resolver,
            evidence_repository=case.repository,
            evidence_authority=case.evidence_authority,
            clock=deterministic_clock,
        )
    )


async def _closed_recovery_fixture(monkeypatch: pytest.MonkeyPatch):
    case = service_case()
    live_service = _build_service(monkeypatch, case)
    created = await live_service.create(case.request)
    run = await live_service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"replay": "exact"},
        context={"attempt": 1},
    )
    inputs = case.repository.completed_inputs[0]
    create_response_ref = ref("cached-create-response")
    run_response_ref = ref("cached-run-response")
    completed_envelope_ref = ref("cached-completed-envelope")
    closed_envelope_ref = ref("cached-closed-envelope")
    completed_tombstone_ref = ref("cached-completed-tombstone")
    closed_tombstone_ref = ref("cached-closed-tombstone")
    latest_event_ref = ref("cached-closed-event")
    completed_event = next(
        event for event in case.repository.events if event.to_state == "completed"
    )
    closed_event = case.repository.events[-1]
    completed_event_ref = ref("cached-completed-event")
    cleanup_receipt = case.repository.closed_inputs[0].cleanup_receipt
    cleanup_projection = cleanup_receipt_projection(cleanup_receipt)
    completed_tombstone = EpisodeCompletedTombstoneV2(
        case.request.episode_id,
        created.response.create_fingerprint,
        run.response.run_fingerprint,
        completed_event.digest,
        run_response_ref,
        completed_envelope_ref,
        1,
    )
    closed_tombstone = EpisodeClosedTombstoneV2(
        case.request.episode_id,
        created.response.create_fingerprint,
        run.response.run_fingerprint,
        closed_event.digest,
        run_response_ref,
        completed_tombstone_ref,
        closed_envelope_ref,
        1,
    )
    resolved_digest = canonical_digest(case.resolved)
    selection_digest = case.resolved.selection_commit.canonical_digest()
    effective_digest = case.resolved.effective_plan.canonical_digest()
    policy_binding_digest = created.response.policy_binding_digest
    materialization_digest = canonical_digest(
        case.sandbox.lease._materialized.receipt
    )
    primary_measurement_digest = canonical_digest(
        case.sandbox.lease.measurement
    )
    runner_ledger_ref = ref("cached-runner-ledger")
    artifact_manifest_ref = ref("cached-artifact-manifest")
    lineage_nodes = (
        LineageNodeV2(resolved_digest, "resolved_plan", "breadboard"),
        LineageNodeV2(
            selection_digest,
            "selection",
            "breadboard",
            (resolved_digest,),
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
        LineageNodeV2(
            primary_measurement_digest,
            "primary_measurement",
            "breadboard",
            (materialization_digest,),
        ),
        LineageNodeV2(
            runner_ledger_ref.sha256,
            "runner_ledger",
            "breadboard",
            (primary_measurement_digest,),
        ),
        LineageNodeV2(
            artifact_manifest_ref.sha256,
            "artifact_manifest",
            "breadboard",
            (runner_ledger_ref.sha256,),
        ),
    )
    evidence_manifest = ExecutionEvidenceManifestV2(
        episode_id=case.request.episode_id,
        resolved_plan_digest=resolved_digest,
        selection_digest=selection_digest,
        effective_plan_digest=effective_digest,
        policy_binding_digest=policy_binding_digest,
        runner_ledger_ref=runner_ledger_ref,
        materialization_digest=materialization_digest,
        primary_measurement_digest=primary_measurement_digest,
        verifier_snapshot_digest=None,
        verifier_measurement_digest=None,
        verifier_result_digest=None,
        artifact_manifest_ref=artifact_manifest_ref,
        primary_disposition="succeeded",
        reward_disposition="eligible",
        reward_components={},
        evidence_policy_ref=ref("evidence-policy").sha256,
        retention_policy_ref=ref("retention-policy").sha256,
        lineage_nodes=lineage_nodes,
        lineage_root=artifact_manifest_ref.sha256,
    )
    evidence_manifest_bytes = evidence_manifest.canonical_bytes()
    evidence_manifest_ref = ArtifactRef(
        artifact_id=evidence_manifest.digest,
        sha256=evidence_manifest.digest,
        size_bytes=len(evidence_manifest_bytes),
        media_type="application/vnd.breadboard.execution-evidence-manifest+json",
    )
    completed_envelope = CompletedEpisodeEnvelopeV2(
        case.request.episode_id,
        created.response.create_fingerprint,
        run.response.run_fingerprint,
        create_response_ref,
        run_response_ref,
        evidence_manifest_ref,
        evidence_manifest.lineage_root,
        "succeeded",
        completed_event_ref,
        completed_event.digest,
        subject_digest=case.resolved.subject_digest,
    )
    closed_envelope = ClosedEpisodeEnvelopeV2(
        case.request.episode_id,
        completed_envelope_ref,
        canonical_digest(cleanup_projection),
        cleanup_projection,
        latest_event_ref,
        closed_event.digest,
        "succeeded",
    )
    locator = EpisodeLocatorRecordV2(
        case.request.episode_id,
        1,
        "closed",
        closed_event.digest,
        latest_event_ref,
        completed_tombstone_ref,
        closed_tombstone_ref,
    )
    recovered = RecoveredEpisodeV2(
        locator=locator,
        events=tuple(case.repository.events),
        completed_tombstone=completed_tombstone,
        closed_tombstone=closed_tombstone,
        completed_envelope=completed_envelope,
        closed_envelope=closed_envelope,
        primary_lease_id=case.sandbox.lease.lease_id,
        evidence_manifest=evidence_manifest,
    )
    case.repository.responses[create_response_ref.artifact_id] = inputs.create_response_bytes
    case.repository.responses[run_response_ref.artifact_id] = inputs.run_response_bytes
    case.repository.recovered[case.request.episode_id] = recovered
    case.repository.locators = (locator,)
    case.calls.clear()
    return case, created, run, recovered


async def test_closed_restart_returns_byte_identical_cached_result_with_zero_runtime_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, created, original_run, recovered = await _closed_recovery_fixture(monkeypatch)
    service = _build_service(monkeypatch, case)

    cached_create = await service.create(case.request)
    cached_run = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"replay": "exact"},
        context={"attempt": 1},
    )
    cached_close = await service.close_episode(case.request.episode_id)
    state = await service.get_state(case.request.episode_id)

    assert cached_create.disposition is V2OperationDisposition.CACHED
    assert cached_create.response == created.response
    assert cached_create.response.state is EpisodeLifecycleState.READY
    assert canonical_create_response_bytes(cached_create.response) == (
        case.repository.responses[
            recovered.completed_envelope.create_response_ref.artifact_id
        ]
    )
    assert cached_run.disposition is V2OperationDisposition.CACHED
    assert cached_run.response.response == original_run.response.response
    assert cached_run.response.completed_envelope_ref == recovered.completed_tombstone.envelope_ref
    assert cached_run.response.closed_envelope_ref == recovered.closed_tombstone.envelope_ref
    assert cached_close.disposition is V2OperationDisposition.CACHED
    assert state.state is EpisodeLifecycleState.CLOSED
    assert state.completed_envelope_ref == recovered.completed_tombstone.envelope_ref
    assert state.closed_envelope_ref == recovered.closed_tombstone.envelope_ref
    assert not any(
        call in case.calls
        for call in (
            "resolve",
            "policy.resolve",
            "registry.resolve",
            "sandbox.open",
            "runner.open",
            "session.run",
            "verifier.execute",
            "lease.close",
        )
    )


async def test_start_hydrates_closed_coordinator_for_direct_state_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, _, _, recovered = await _closed_recovery_fixture(monkeypatch)
    service = _build_service(monkeypatch, case)

    await service.start()
    state = await service.get_state(case.request.episode_id)
    closed = await service.close_episode(case.request.episode_id)

    assert state.state is EpisodeLifecycleState.CLOSED
    assert state.closed_envelope_ref == recovered.closed_tombstone.envelope_ref
    assert closed.disposition is V2OperationDisposition.CACHED
    assert closed.response.closed_envelope_ref == recovered.closed_tombstone.envelope_ref
    assert not any(
        call in case.calls
        for call in (
            "resolve",
            "policy.resolve",
            "registry.resolve",
            "sandbox.open",
            "runner.open",
            "session.run",
        )
    )


@pytest.mark.parametrize(
    ("receipt_factory", "expected_state", "expected_cleanup"),
    [
        (
            released_receipt,
            EpisodeLifecycleState.CLOSED,
            EpisodeCleanupDisposition.RELEASED,
        ),
        (
            failed_receipt,
            EpisodeLifecycleState.QUARANTINED,
            EpisodeCleanupDisposition.QUARANTINED,
        ),
    ],
)
async def test_completed_restart_uses_canonical_lease_binding_and_exact_cleanup_receipt(
    monkeypatch: pytest.MonkeyPatch,
    receipt_factory,
    expected_state: EpisodeLifecycleState,
    expected_cleanup: EpisodeCleanupDisposition,
) -> None:
    case, created, _, recovered = await _closed_recovery_fixture(monkeypatch)
    completed_event = next(
        event for event in recovered.events if event.to_state == "completed"
    )
    completed_locator = EpisodeLocatorRecordV2(
        case.request.episode_id,
        1,
        "completed",
        completed_event.digest,
        ref("recovered-completed-event"),
        recovered.locator.completed_tombstone_ref,
    )
    completed_events = tuple(
        event
        for event in recovered.events
        if event.sequence <= completed_event.sequence
    )
    completed_recovery = RecoveredEpisodeV2(
        locator=completed_locator,
        events=completed_events,
        completed_tombstone=recovered.completed_tombstone,
        completed_envelope=recovered.completed_envelope,
        primary_lease_id=recovered.primary_lease_id,
        evidence_manifest=recovered.evidence_manifest,
    )
    receipt = receipt_factory(recovered.primary_lease_id)
    case.repository.locators = (completed_locator,)
    case.repository.recovered[case.request.episode_id] = completed_recovery
    case.sandbox.reconcile_receipts = (receipt,)
    case.repository.closed_inputs.clear()
    case.repository.quarantine_inputs.clear()
    case.calls.clear()
    service = _build_service(monkeypatch, case)

    cached_create = await service.create(case.request)
    recovered_state = await service.get_state(case.request.episode_id)

    assert cached_create.disposition is V2OperationDisposition.CACHED
    assert cached_create.response == created.response
    assert cached_create.response.state is EpisodeLifecycleState.READY
    assert canonical_create_response_bytes(cached_create.response) == (
        case.repository.responses[
            completed_recovery.completed_envelope.create_response_ref.artifact_id
        ]
    )
    assert recovered_state.state is EpisodeLifecycleState.COMPLETED

    await service.start()
    state = await service.get_state(case.request.episode_id)

    assert completed_recovery.completed_envelope is not None
    assert not hasattr(completed_recovery.completed_envelope, "lease_id")
    assert completed_recovery.primary_lease_id == case.sandbox.lease.lease_id
    assert state.state is expected_state
    assert state.primary_disposition is EpisodePrimaryDisposition.SUCCEEDED
    assert state.cleanup_disposition is expected_cleanup
    assert case.calls.count("sandbox.reconcile_stale") == 1
    assert case.calls.count("lease.close") == 0
    assert not any(
        call in case.calls
        for call in (
            "resolve",
            "policy.resolve",
            "registry.resolve",
            "sandbox.open",
            "runner.open",
            "session.run",
        )
    )
    if expected_state is EpisodeLifecycleState.CLOSED:
        assert state.closed_envelope_ref is not None
        closed = case.repository.closed_inputs[-1]
        assert closed.cleanup_receipt is receipt
        assert closed.cleanup_lease_id == recovered.primary_lease_id
        assert closed.final_primary_outcome == "succeeded"
        assert not case.repository.quarantine_inputs
    else:
        assert state.closed_envelope_ref is None
        quarantine = case.repository.quarantine_inputs[-1]
        assert quarantine.failure.code == "cleanup_not_released"
        assert quarantine.event.primary_fact is None
        assert quarantine.event.cleanup_fact == quarantine.failure
        assert not case.repository.closed_inputs


async def test_real_repository_durable_quarantine_is_absorbing_on_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    case.runner.emit_result_events = True
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    case.repository = repository
    live_service = _build_service(monkeypatch, case)
    created = await live_service.create(case.request)
    case.sandbox.lease.close_error = RuntimeError(
        "deterministic primary cleanup failure"
    )

    await live_service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"restart": "durable-quarantine"},
    )
    before = repository.recover(case.request.episode_id)

    assert before is not None
    assert before.completed_tombstone is not None
    assert before.quarantined is True
    assert before.closed_tombstone is None
    case.sandbox.lease.close_error = None
    case.sandbox.reconcile_receipts = (
        released_receipt(before.primary_lease_id),
    )
    case.calls.clear()
    restarted = _build_service(monkeypatch, case)

    await restarted.start()
    state = await restarted.get_state(case.request.episode_id)
    after = repository.recover(case.request.episode_id)

    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert after is not None
    assert after.locator == before.locator
    assert after.events == before.events
    assert after.closed_tombstone is None
    assert case.calls.count("lease.close") == 0

async def test_publish_closed_failure_is_durably_quarantined_and_absorbing_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    case.runner.emit_result_events = True
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    case.repository = repository
    live_service = _build_service(monkeypatch, case)
    created = await live_service.create(case.request)
    original_publish_closed = repository.publish_closed
    publish_attempts = 0

    def fail_publish_closed(inputs):
        nonlocal publish_attempts
        publish_attempts += 1
        raise RuntimeError("deterministic closed publication failure")

    monkeypatch.setattr(repository, "publish_closed", fail_publish_closed)
    result = await live_service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"restart": "closed-publication-failure"},
    )
    before = repository.recover(case.request.episode_id)

    assert result.response.closed_envelope_ref is None
    assert before is not None
    assert before.quarantined is True
    assert before.closed_tombstone is None
    assert before.events[-1].to_state == "quarantined"
    assert publish_attempts == 1

    monkeypatch.setattr(repository, "publish_closed", original_publish_closed)
    case.calls.clear()
    restarted = _build_service(monkeypatch, case)
    await restarted.start()
    state = await restarted.get_state(case.request.episode_id)
    after = repository.recover(case.request.episode_id)

    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert after is not None
    assert after.locator == before.locator
    assert after.events == before.events
    assert after.closed_tombstone is None
    assert publish_attempts == 1
    assert case.calls.count("lease.close") == 0



async def test_real_repository_restart_closes_with_recovered_verifier_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    case.runner.emit_result_events = True
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    case.repository = repository
    live_service = _build_service(monkeypatch, case)
    created = await live_service.create(case.request)
    case.sandbox.lease.close_release = asyncio.Event()
    run_task = asyncio.create_task(
        live_service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"restart": "after-completed"},
        )
    )
    await case.sandbox.lease.close_entered.wait()
    crashed = repository.recover(case.request.episode_id)

    assert crashed is not None
    assert crashed.locator.current_state == "closing"
    assert crashed.completed_tombstone is not None
    assert crashed.closed_tombstone is None
    assert crashed.primary_lease_id == case.sandbox.lease.lease_id
    assert crashed.verifier_lease_id == case.sandbox.verifier.lease_id
    assert crashed.verifier_cleanup_receipt is not None
    assert (
        crashed.verifier_cleanup_receipt.lease_id
        == case.sandbox.verifier.lease_id
    )
    case.sandbox.reconcile_receipts = (
        released_receipt(crashed.primary_lease_id),
    )
    case.calls.clear()
    restarted = _build_service(monkeypatch, case)

    try:
        await restarted.start()
        state = await restarted.get_state(case.request.episode_id)
        recovered = repository.recover(case.request.episode_id)

        assert state.state is EpisodeLifecycleState.CLOSED
        assert recovered is not None
        assert recovered.closed_tombstone is not None
        assert recovered.closed_envelope is not None
        assert (
            recovered.closed_envelope.verifier_cleanup_receipt["lease_id"]
            == crashed.verifier_lease_id
        )
        assert case.calls.count("lease.close") == 0
    finally:
        case.sandbox.lease.close_release.set()
        await asyncio.gather(run_task, return_exceptions=True)


async def test_recovered_episode_rejects_changed_create_fingerprint_before_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, _, _, _ = await _closed_recovery_fixture(monkeypatch)
    service = _build_service(monkeypatch, case)
    payload = case.request.model_dump(mode="python")
    payload["task"] = {**payload["task"], "task_type": "changed-replay-task"}
    changed = type(case.request).model_validate(payload)

    with pytest.raises(V2EpisodeConflict):
        await service.create(changed)

    assert case.calls == [f"repo.recover:{case.request.episode_id}"]


async def _interrupted_recovery_fixture(
    monkeypatch: pytest.MonkeyPatch,
    state: EpisodeLifecycleState,
):
    case = service_case()
    live_service = _build_service(monkeypatch, case)
    created = await live_service.create(case.request)
    if state is EpisodeLifecycleState.CANCEL_REQUESTED:
        await live_service.cancel(
            case.request.episode_id,
            "interrupted before restart",
        )
    elif state in {
        EpisodeLifecycleState.RUNNING,
        EpisodeLifecycleState.VERIFYING,
        EpisodeLifecycleState.CLOSING,
    }:
        await live_service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"interrupted": state.value},
        )
    head = next(event for event in case.repository.events if event.to_state == state.value)
    events = tuple(
        event for event in case.repository.events if event.sequence <= head.sequence
    )
    primary_lease_id = (
        None
        if state in {
            EpisodeLifecycleState.ACCEPTED,
            EpisodeLifecycleState.ALLOCATING,
        }
        else case.sandbox.lease.lease_id
    )
    locator = EpisodeLocatorRecordV2(
        case.request.episode_id,
        1,
        state.value,
        head.digest,
        ref(f"interrupted-{state.value}-event"),
    )
    recovered = RecoveredEpisodeV2(
        locator=locator,
        events=events,
        primary_lease_id=primary_lease_id,
    )
    case.repository.locators = (locator,)
    case.repository.recovered[case.request.episode_id] = recovered
    case.repository.events.clear()
    case.repository.completed_inputs.clear()
    case.repository.failed_completed_inputs.clear()
    case.repository.closed_inputs.clear()
    case.repository.quarantine_inputs.clear()
    case.calls.clear()
    return case, recovered


@pytest.mark.parametrize(
    "durable_state",
    [
        EpisodeLifecycleState.CANCEL_REQUESTED,
        EpisodeLifecycleState.CLOSED,
        EpisodeLifecycleState.QUARANTINED,
    ],
)
async def test_restart_replays_first_durable_cancellation_receipt_in_every_state(
    monkeypatch: pytest.MonkeyPatch,
    durable_state: EpisodeLifecycleState,
) -> None:
    case = service_case()
    case.runner.emit_result_events = True
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    case.repository = repository
    live = _build_service(monkeypatch, case)
    created = await live.create(case.request)

    if durable_state is EpisodeLifecycleState.CANCEL_REQUESTED:
        first = await live.cancel(case.request.episode_id, "operator requested A")
    else:
        if durable_state is EpisodeLifecycleState.QUARANTINED:
            case.sandbox.lease.close_error = RuntimeError(
                "deterministic cleanup failure"
            )
        run_task = asyncio.create_task(
            live.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"cancel": "durable-restart"},
            )
        )
        while case.runner.session is None:
            await asyncio.sleep(0)
        case.runner.session.block_run = True
        await case.runner.session.run_entered.wait()
        first = await live.cancel(case.request.episode_id, "operator requested A")
        case.runner.session.run_release.set()
        await run_task

    before = repository.recover(case.request.episode_id)
    assert before is not None
    assert before.locator.current_state == durable_state.value
    expected_fingerprint = cancellation_fingerprint(
        case.request.episode_id,
        created.response.create_fingerprint,
        "operator requested A",
    )
    assert first.reason == "operator requested A"
    assert any(
        event.cancel_reason == "operator requested A"
        and event.cancel_fingerprint == expected_fingerprint
        for event in before.events
    )

    case.calls.clear()
    restarted = _build_service(monkeypatch, case)
    await restarted.start()
    after_start = repository.recover(case.request.episode_id)
    assert after_start is not None
    replay = await restarted.cancel(
        case.request.episode_id,
        "operator requested B",
    )
    coordinator = restarted._coordinators[case.request.episode_id]

    assert replay.reason == "operator requested A"
    assert replay.state is (
        EpisodeLifecycleState.QUARANTINED
        if durable_state is EpisodeLifecycleState.CANCEL_REQUESTED
        else durable_state
    )
    assert coordinator.cancel_reason == "operator requested A"
    assert coordinator.cancel_fingerprint == expected_fingerprint
    assert repository.recover(case.request.episode_id) == after_start
    assert not any(call.startswith("session.cancel:") for call in case.calls)


async def test_restart_quarantines_tampered_cancellation_fingerprint_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    cas = InMemoryCAS()
    locators = InMemoryEpisodeLocatorStore()
    repository = EpisodeEvidenceRepository(cas, locators)
    case.repository = repository
    live = _build_service(monkeypatch, case)
    created = await live.create(case.request)
    await live.cancel(case.request.episode_id, "operator requested A")

    current = locators.get(case.request.episode_id)
    assert current is not None
    payload = json.loads(cas.get_bytes(current.latest_event_ref))
    payload["cancel_fingerprint"] = cancellation_fingerprint(
        case.request.episode_id,
        created.response.create_fingerprint,
        "operator requested B",
    )
    forged_bytes = canonical_json_bytes(payload)
    forged_ref = cas.put_bytes(
        forged_bytes,
        artifact_id="v2/forged-cancellation-receipt",
        media_type="application/json",
    )
    forged_locator = replace(
        current,
        generation=current.generation + 1,
        latest_event_head=forged_ref.sha256,
        latest_event_ref=forged_ref,
        checksum="",
    )
    locators.compare_and_swap(
        case.request.episode_id,
        current.generation,
        forged_locator,
    )
    case.calls.clear()
    restarted = _build_service(monkeypatch, case)

    await restarted.start()

    with pytest.raises(EvidenceCorruptError, match="quarantined"):
        repository.recover(case.request.episode_id)
    assert case.request.episode_id not in restarted._coordinators
    assert not any(call.startswith("session.cancel:") for call in case.calls)


@pytest.mark.parametrize(
    "interrupted_state",
    [
        EpisodeLifecycleState.ACCEPTED,
        EpisodeLifecycleState.ALLOCATING,
    ],
)
async def test_interrupted_pre_lease_state_has_no_cleanup_authority_and_never_false_closes(
    monkeypatch: pytest.MonkeyPatch,
    interrupted_state: EpisodeLifecycleState,
) -> None:
    case, recovered = await _interrupted_recovery_fixture(
        monkeypatch,
        interrupted_state,
    )
    unmatched = released_receipt("manager-owned-unmatched-lease")
    case.sandbox.reconcile_receipts = (unmatched,)
    service = _build_service(monkeypatch, case)

    await service.start()
    state = await service.get_state(case.request.episode_id)

    assert recovered.primary_lease_id is None
    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert state.primary_disposition is EpisodePrimaryDisposition.INTERRUPTED
    assert state.cleanup_disposition is EpisodeCleanupDisposition.QUARANTINED
    assert not case.repository.closed_inputs
    assert not case.repository.failed_completed_inputs
    quarantine = case.repository.quarantine_inputs[-1]
    assert quarantine.failure.code == "process_interrupted"
    assert quarantine.event.primary_fact == quarantine.failure
    assert quarantine.event.cleanup_fact is None
    assert quarantine.failure.lease_id is None


async def test_restart_reconciles_three_partial_allocations_without_false_tombstones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    states = (
        ("partial-allocating", EpisodeLifecycleState.ALLOCATING, None),
        ("partial-ready", EpisodeLifecycleState.READY, "partial-ready-lease"),
        ("partial-quarantined", EpisodeLifecycleState.QUARANTINED, None),
    )
    locators: list[EpisodeLocatorRecordV2] = []
    for index, (episode_id, state, lease_id) in enumerate(states):
        accepted = service_module.LifecycleEventV2(
            episode_id,
            0,
            None,
            None,
            "accepted",
            "accepted",
            "2026-07-10T12:00:00Z",
            "sha256:" + str(index + 1) * 64,
            None,
            case.resolved.effective_plan.canonical_digest(),
        )
        events = [accepted]
        if state in {
            EpisodeLifecycleState.ALLOCATING,
            EpisodeLifecycleState.READY,
            EpisodeLifecycleState.QUARANTINED,
        }:
            events.append(
                service_module.LifecycleEventV2(
                    episode_id,
                    1,
                    accepted.digest,
                    "accepted",
                    "allocating",
                    "allocation_started",
                    "2026-07-10T12:00:01Z",
                    accepted.create_fingerprint,
                    None,
                    accepted.effective_plan_digest,
                )
            )
        if state is EpisodeLifecycleState.READY:
            allocating = events[-1]
            events.append(
                service_module.LifecycleEventV2(
                    episode_id,
                    2,
                    allocating.digest,
                    "allocating",
                    "ready",
                    "workspace_ready",
                    "2026-07-10T12:00:02Z",
                    accepted.create_fingerprint,
                    None,
                    accepted.effective_plan_digest,
                    primary_lease_id=lease_id,
                )
            )
        elif state is EpisodeLifecycleState.QUARANTINED:
            allocating = events[-1]
            events.append(
                service_module.LifecycleEventV2(
                    episode_id,
                    2,
                    allocating.digest,
                    "allocating",
                    "quarantined",
                    "quarantined",
                    "2026-07-10T12:00:02Z",
                    accepted.create_fingerprint,
                    None,
                    accepted.effective_plan_digest,
                )
            )
        head = events[-1]
        locator = EpisodeLocatorRecordV2(
            episode_id,
            1,
            state.value,
            head.digest,
            ref(f"{episode_id}-head"),
        )
        assert locator.completed_tombstone_ref is None
        assert locator.closed_tombstone_ref is None
        locators.append(locator)
        case.repository.recovered[episode_id] = RecoveredEpisodeV2(
            locator=locator,
            events=tuple(events),
            quarantined=state is EpisodeLifecycleState.QUARANTINED,
            primary_lease_id=lease_id,
        )
    case.repository.locators = tuple(locators)
    case.sandbox.reconcile_receipts = (released_receipt("partial-ready-lease"),)
    service = _build_service(monkeypatch, case)

    await service.start()

    observed = {
        episode_id: (await service.get_state(episode_id)).state
        for episode_id, _state, _lease_id in states
    }
    assert set(observed.values()) == {EpisodeLifecycleState.QUARANTINED}
    assert not case.repository.closed_inputs
    assert {
        item.episode_id for item in case.repository.failed_completed_inputs
    } == {"partial-ready"}
    assert {
        item.episode_id for item in case.repository.quarantine_inputs
    } == {"partial-allocating", "partial-ready"}
    assert all(
        item.primary_failure.code == "process_interrupted"
        for item in case.repository.failed_completed_inputs
    )
    assert all(
        item.failure.code == "process_interrupted"
        for item in case.repository.quarantine_inputs
    )
    assert {
        (event.from_state, event.to_state)
        for event in case.repository.events
        if event.episode_id.startswith("partial-")
    } == {
        ("allocating", "quarantined"),
        ("ready", "closing"),
        ("closing", "quarantined"),
    }


@pytest.mark.parametrize(
    "interrupted_state",
    [
        EpisodeLifecycleState.READY,
        EpisodeLifecycleState.RUNNING,
        EpisodeLifecycleState.VERIFYING,
        EpisodeLifecycleState.CANCEL_REQUESTED,
        EpisodeLifecycleState.CLOSING,
    ],
)
@pytest.mark.parametrize(
    ("receipt_factory", "expected_cleanup_failure"),
    [
        (released_receipt, "resolved_subject_missing"),
        (failed_receipt, "cleanup_not_released"),
        (quarantined_receipt, "cleanup_not_released"),
    ],
)
async def test_interrupted_restart_separates_primary_and_exact_cleanup_receipt_facts(
    monkeypatch: pytest.MonkeyPatch,
    interrupted_state: EpisodeLifecycleState,
    receipt_factory,
    expected_cleanup_failure: str,
) -> None:
    case, recovered = await _interrupted_recovery_fixture(
        monkeypatch,
        interrupted_state,
    )
    receipt = receipt_factory(recovered.primary_lease_id)
    case.sandbox.reconcile_receipts = (receipt,)
    service = _build_service(monkeypatch, case)

    await service.start()
    state = await service.get_state(case.request.episode_id)

    assert recovered.primary_lease_id == case.sandbox.lease.lease_id
    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert state.primary_disposition is EpisodePrimaryDisposition.INTERRUPTED
    assert state.cleanup_disposition is EpisodeCleanupDisposition.QUARANTINED
    interrupted = case.repository.failed_completed_inputs[-1]
    assert interrupted.primary_disposition == "interrupted"
    assert interrupted.primary_failure.code == "process_interrupted"
    assert interrupted.primary_failure.lease_id == recovered.primary_lease_id
    assert not case.repository.closed_inputs
    quarantine = case.repository.quarantine_inputs[-1]
    assert quarantine.failure.code == "process_interrupted"
    assert quarantine.event.primary_fact == quarantine.failure
    assert quarantine.event.cleanup_fact.code == expected_cleanup_failure
    assert quarantine.event.cleanup_fact.lease_id == (
        recovered.primary_lease_id
        if expected_cleanup_failure == "cleanup_not_released"
        else None
    )
    assert quarantine.event.primary_fact != quarantine.event.cleanup_fact


@pytest.mark.parametrize(
    "interrupted_state",
    [
        EpisodeLifecycleState.CANCEL_REQUESTED,
        EpisodeLifecycleState.RUNNING,
        EpisodeLifecycleState.CLOSING,
    ],
)
@pytest.mark.parametrize(
    "receipts",
    [
        (),
        (released_receipt("wrong-lease"),),
    ],
)
async def test_interrupted_restart_missing_or_wrong_lease_receipt_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    receipts,
    interrupted_state: EpisodeLifecycleState,
) -> None:
    case, recovered = await _interrupted_recovery_fixture(
        monkeypatch,
        interrupted_state,
    )
    case.sandbox.reconcile_receipts = receipts
    service = _build_service(monkeypatch, case)

    await service.start()
    state = await service.get_state(case.request.episode_id)

    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert state.primary_disposition is EpisodePrimaryDisposition.INTERRUPTED
    assert state.cleanup_disposition is EpisodeCleanupDisposition.QUARANTINED
    assert not case.repository.closed_inputs
    assert not case.repository.failed_completed_inputs
    quarantine = case.repository.quarantine_inputs[-1]
    assert quarantine.failure.code == "process_interrupted"
    assert quarantine.failure.lease_id == recovered.primary_lease_id
    assert quarantine.event.primary_fact == quarantine.failure
    assert quarantine.event.cleanup_fact.code == "cleanup_receipt_missing"
    assert quarantine.event.cleanup_fact.lease_id == recovered.primary_lease_id


async def test_start_hydrates_existing_quarantine_without_runtime_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    event = service_module.LifecycleEventV2(
        case.request.episode_id,
        0,
        None,
        "running",
        "quarantined",
        "process_interrupted",
        "2026-07-10T12:00:00Z",
        "sha256:" + "3" * 64,
        "sha256:" + "4" * 64,
        case.resolved.effective_plan.canonical_digest(),
    )
    locator = EpisodeLocatorRecordV2(
        case.request.episode_id,
        1,
        "quarantined",
        event.digest,
        ref("existing-quarantine-event"),
    )
    case.repository.locators = (locator,)
    case.repository.recovered[case.request.episode_id] = RecoveredEpisodeV2(
        locator=locator,
        events=(event,),
    )
    service = _build_service(monkeypatch, case)

    await service.start()
    state = await service.get_state(case.request.episode_id)
    closed = await service.close_episode(case.request.episode_id)

    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert closed.response.state is EpisodeLifecycleState.QUARANTINED
    assert not any(
        call in case.calls
        for call in (
            "resolve",
            "policy.resolve",
            "registry.resolve",
            "sandbox.open",
            "runner.open",
            "session.run",
        )
    )

async def test_start_is_single_flight_resets_after_failure_and_reconciles_once_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    service = _build_service(monkeypatch, case)
    scans = 0
    fail_scan = True

    def scan_locators():
        nonlocal scans
        scans += 1
        if fail_scan:
            raise RuntimeError("deterministic startup scan failure")
        return ()

    case.repository.scan_locators = scan_locators
    failures = await asyncio.gather(
        service.start(),
        service.start(),
        service.start(),
        return_exceptions=True,
    )

    assert scans == 1
    assert all(
        isinstance(item, RuntimeError)
        and str(item) == "deterministic startup scan failure"
        for item in failures
    )
    assert "sandbox.reconcile_stale" not in case.calls

    fail_scan = False
    await asyncio.gather(service.start(), service.start(), service.start())
    await service.start()

    assert scans == 2
    assert case.calls.count("sandbox.reconcile_stale") == 1


async def test_start_scans_valid_corrupt_valid_and_blocks_the_hinted_episode_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    corrupt_failure = RuntimeError("deterministic corrupt locator")
    before = SimpleNamespace(
        locator_key="valid-before.json",
        episode_id_hint="valid-before",
        record=SimpleNamespace(episode_id="valid-before"),
        failure=None,
    )
    corrupt = SimpleNamespace(
        locator_key=f"{case.request.episode_id}.json",
        episode_id_hint=case.request.episode_id,
        record=None,
        failure=corrupt_failure,
    )
    after = SimpleNamespace(
        locator_key="valid-after.json",
        episode_id_hint="valid-after",
        record=SimpleNamespace(episode_id="valid-after"),
        failure=None,
    )
    case.repository.scan_entries = (before, corrupt, after)
    service = _build_service(monkeypatch, case)

    await service.start()

    assert case.calls.index("repo.recover:valid-before") < case.calls.index(
        "repo.quarantine_corrupt_locator"
    )
    assert case.calls.index("repo.quarantine_corrupt_locator") < case.calls.index(
        "repo.recover:valid-after"
    )
    assert case.repository.corrupt_locator_inputs == [(corrupt, corrupt_failure)]
    with pytest.raises(V2EpisodeQuarantined) as captured:
        await service.create(case.request)
    assert captured.value.failure.code == "evidence_corrupt"
    assert not any(
        call in case.calls
        for call in ("resolve", "policy.resolve", "registry.resolve", "sandbox.open")
    )



async def test_corrupt_recovery_is_typed_quarantine_and_opens_no_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = service_case()
    service = _build_service(monkeypatch, case)

    def corrupt(_episode_id: str):
        raise EvidenceCorruptError("corrupt durable evidence")

    case.repository.recover = corrupt
    with pytest.raises(V2EpisodeQuarantined) as captured:
        await service.create(case.request)

    assert captured.value.failure.code == "evidence_corrupt"
    assert not any(
        call in case.calls
        for call in ("resolve", "policy.resolve", "registry.resolve", "sandbox.open")
    )
