from __future__ import annotations
from builtins import BaseExceptionGroup

import asyncio
from types import SimpleNamespace

import pytest

from breadboard.rl.harness import service as service_module
from breadboard.rl.harness.evidence import (
    EpisodeEvidenceRepository,
    InMemoryEpisodeLocatorStore,
)
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    MaterializationKey,
    SandboxCleanupReceipt,
)
from breadboard.rl.harness.runners.base import (
    RunnerCancellation,
    RunnerCancelled,
    RunnerOpenRequest,
    ToolCallEvent,
)
from breadboard.rl.harness.runners.conductor import (
    ConductorRunRequest,
    PolicyRuntimeBinding,
)
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_TOOL_DEFINITIONS,
    TerminalLoopLimits,
    TerminalRunRequest,
)
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2EpisodeConflict,
    V2EpisodeRejected,
    V2LifecycleDependencies,
    V2OperationDisposition,
)
from breadboard.rl.state.cas import InMemoryCAS
from tests.rl.harness.v2_service_fixtures import (
    DeterministicSession,
    cancellation_fingerprint,
    deterministic_clock,
    deterministic_sandbox_plan,
    failed_receipt,
    receipt_from_resources,
    ref,
    service_case,
)

pytestmark = pytest.mark.asyncio


async def _service(monkeypatch: pytest.MonkeyPatch):
    case = service_case()
    preflights: list[object] = []

    def preflight(request, registries, installed_authorities):
        preflights.append(request)
        return deterministic_sandbox_plan()

    monkeypatch.setattr(service_module, "build_sandbox_execution_plan", preflight)
    service = BreadBoardV2EpisodeService(
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
    return service, case, preflights


async def _service_with_real_repository(monkeypatch: pytest.MonkeyPatch):
    case = service_case()
    case.runner.emit_result_events = True
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(),
        InMemoryEpisodeLocatorStore(),
    )
    case.repository = repository
    monkeypatch.setattr(
        service_module,
        "build_sandbox_execution_plan",
        lambda request, registries, installed_authorities: deterministic_sandbox_plan(),
    )
    service = BreadBoardV2EpisodeService(
        V2LifecycleDependencies(
            config_runtime=case.config,
            runner_registry=case.registry,
            sandbox_runtime=case.sandbox,
            policy_client_resolver=case.policy_resolver,
            evidence_repository=repository,
            evidence_authority=case.evidence_authority,
            clock=deterministic_clock,
        )
    )
    return service, case, repository


async def _created(monkeypatch: pytest.MonkeyPatch):
    service, case, preflights = await _service(monkeypatch)
    created = await service.create(case.request)
    return service, case, preflights, created


async def test_v2_materializes_the_terminal_request_selected_by_the_effective_plan() -> None:
    request = ConductorRunRequest(
        {
            "responses_create_params": {
                "model": "model-a",
                "input": "repair the admitted workspace",
            }
        },
        {"trace_id": "trace-a"},
    )
    plan = SimpleNamespace(
        runner=SimpleNamespace(adapter_id=TERMINAL_ADAPTER_ID),
        effective_capabilities=SimpleNamespace(
            limits=SimpleNamespace(
                max_turns=3,
                action_timeout_ms=60_000,
                observation_bytes=100_000,
            )
        ),
    )

    materialized = service_module._materialize_runner_request(
        request,
        plan,
        episode_id="episode-terminal",
        effective_plan_digest="sha256:" + "1" * 64,
    )

    assert type(materialized) is TerminalRunRequest
    assert dict(materialized.responses_create_params) == {
        "model": "model-a",
        "input": "repair the admitted workspace",
    }
    assert materialized.tools == TERMINAL_TOOL_DEFINITIONS
    assert materialized.limits == TerminalLoopLimits(
        max_turns=3,
        action_timeout_seconds=60,
        max_observation_chars=100_000,
    )


async def test_exact_wp4_plan_binding_registry_workspace_and_full_legal_transition_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, preflights, created = await _created(monkeypatch)

    assert created.disposition is V2OperationDisposition.FRESH
    assert created.response.effective_plan_digest == case.resolved.effective_plan.canonical_digest()
    assert created.response.selection_commit == case.resolved.selection_commit
    assert created.response.base_receipt_digest == case.resolved.base_receipt_digest
    assert created.response.final_receipt_digest == case.resolved.final_receipt_digest
    assert (
        created.response.policy_observation_digest
        == case.resolved.policy_capability_observation_digest
    )
    sandbox_plan = deterministic_sandbox_plan()
    assert created.response.sandbox_preflight.runtime == sandbox_plan.runtime.runtime_id
    assert (
        created.response.sandbox_preflight.runtime_class
        is sandbox_plan.runtime.runtime_class
    )
    assert (
        created.response.sandbox_preflight.runtime_binary_digest
        == sandbox_plan.runtime.measured_binary_digest
    )
    assert (
        created.response.sandbox_preflight.image_digest
        == sandbox_plan.image.image_digest
    )
    assert (
        created.response.sandbox_preflight.security_policy_digest
        == sandbox_plan.security_policy.policy_digest
    )
    assert (
        created.response.sandbox_preflight.network_policy_digest
        == sandbox_plan.network_policy.policy_digest
    )
    assert (
        created.response.sandbox_preflight.verifier_digest
        == sandbox_plan.verifier.grant.implementation_digest
    )
    assert (
        created.response.sandbox_preflight.materialization_plan_digest
        == MaterializationKey.from_plan(sandbox_plan.materialization_plan).digest
    )
    assert case.policy_resolver.arguments == [
        (
            case.request.policy_binding,
            case.request.episode_id,
            case.resolved.effective_plan.canonical_digest(),
        )
    ]
    assert case.registry.arguments == [
        (
            case.resolved.effective_plan.runner.adapter_id,
            case.resolved.effective_plan.runner.runtime_abi,
        )
    ]
    assert len(preflights) == len(case.sandbox.open_arguments) == 1
    assert preflights[0].effective_plan is case.resolved.effective_plan
    assert case.sandbox.open_arguments[0].effective_plan is case.resolved.effective_plan

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"case": "wp4-exact"},
        context={"inventory": 1},
    )

    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.SUCCEEDED
    assert outcome.response.completed_envelope_ref is not None
    assert outcome.response.closed_envelope_ref is not None
    assert case.runner.open_arguments[0][0] == RunnerOpenRequest(
        case.request.episode_id, case.resolved.effective_plan
    )
    assert type(case.runner.open_arguments[0][1]) is PolicyRuntimeBinding
    assert [event.to_state for event in case.repository.events] == [
        "accepted",
        "allocating",
        "ready",
        "running",
        "verifying",
        "completed",
        "closing",
        "closed",
    ]
    assert [(event.sequence, event.from_state, event.to_state) for event in case.repository.events] == [
        (0, None, "accepted"),
        (1, "accepted", "allocating"),
        (2, "allocating", "ready"),
        (3, "ready", "running"),
        (4, "running", "verifying"),
        (5, "verifying", "completed"),
        (6, "completed", "closing"),
        (7, "closing", "closed"),
    ]
    assert case.calls.index("session.close") < case.calls.index("lease.seal")
    assert case.calls.index("lease.seal") < case.calls.index("verifier.execute")
    assert case.calls.index("verifier.execute") < case.calls.index("verifier.close")
    assert case.calls.index("verifier.close") < case.calls.index("repo.publish_completed")
    assert case.calls.index("repo.publish_completed") < case.calls.index("lease.close")
    assert case.calls.index("lease.close") < case.calls.index("repo.publish_closed")
    assert case.repository.closed_inputs[0].final_primary_outcome == "succeeded"
    assert case.calls.count("policy.close") == 1
    assert case.sandbox.verifier.lease_id != case.sandbox.lease.lease_id
    assert (
        case.repository.completed_inputs[0].verifier_lease_id
        == case.sandbox.verifier.lease_id
    )
    assert (
        case.repository.completed_inputs[0].verifier_cleanup_receipt.lease_id
        == case.sandbox.verifier.lease_id
    )
    assert (
        case.repository.closed_inputs[0].verifier_cleanup_lease_id
        == case.sandbox.verifier.lease_id
    )


async def test_real_repository_close_returns_with_absorbing_terminal_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    created = await service.create(case.request)

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"real-repository": "absorbing-close"},
    )
    state = await service.get_state(case.request.episode_id)
    recovered = repository.recover(case.request.episode_id)

    assert outcome.response.closed_envelope_ref is not None
    assert state.state is EpisodeLifecycleState.CLOSED
    assert recovered is not None
    assert recovered.locator.current_state == "closed"
    assert recovered.closed_tombstone is not None
    assert recovered.events[-1].to_state == "closed"
    assert recovered.locator.latest_event_head == recovered.events[-1].digest
    assert (
        await service.close_episode(case.request.episode_id)
    ).disposition is V2OperationDisposition.CACHED


@pytest.mark.parametrize("failure_at", ["resolve", "policy", "registry", "preflight"])
async def test_pre_admission_failures_open_no_lease_and_make_no_cleanup_claim(
    monkeypatch: pytest.MonkeyPatch, failure_at: str
) -> None:
    service, case, _ = await _service(monkeypatch)
    if failure_at == "resolve":
        case.config.error = RuntimeError("deterministic resolution rejection")
    elif failure_at == "policy":
        case.policy_resolver.error = RuntimeError("deterministic policy rejection")
    elif failure_at == "registry":
        case.registry.error = RuntimeError("deterministic registry rejection")
    else:
        def reject_preflight(*args, **kwargs):
            raise RuntimeError("deterministic preflight rejection")
        monkeypatch.setattr(service_module, "build_sandbox_execution_plan", reject_preflight)

    with pytest.raises(V2EpisodeRejected):
        await service.create(case.request)

    assert "sandbox.open" not in case.calls
    assert "lease.close" not in case.calls
    assert not case.repository.events
    assert not case.repository.closed_inputs
    assert not case.repository.quarantine_inputs
    assert case.calls.count("policy.close") == (
        1 if failure_at in {"registry", "preflight"} else 0
    )

async def test_pre_admission_failure_removes_only_its_coordinator_for_all_waiters_and_corrected_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    case.policy_resolver.error = _CodedFailure("policy_rejected")
    case.policy_resolver.entered = asyncio.Event()
    case.policy_resolver.release = asyncio.Event()

    first = asyncio.create_task(service.create(case.request))
    await case.policy_resolver.entered.wait()
    waiters = [
        asyncio.create_task(service.create(case.request)),
        asyncio.create_task(service.create(case.request)),
    ]
    await asyncio.sleep(0)
    case.policy_resolver.release.set()
    failures = await asyncio.gather(first, *waiters, return_exceptions=True)

    assert all(isinstance(item, V2EpisodeRejected) for item in failures)
    assert case.calls.count("policy.resolve") == 1
    assert "sandbox.open" not in case.calls
    assert not case.repository.events

    case.policy_resolver.error = None
    payload = case.request.model_dump(mode="python")
    payload["task"] = {
        **payload["task"],
        "task_type": "corrected-after-pre-admission-rejection",
    }
    corrected = type(case.request).model_validate(payload)
    retried = await service.create(corrected)

    assert retried.disposition is V2OperationDisposition.FRESH
    assert retried.response.episode_id == corrected.episode_id
    assert case.calls.count("policy.resolve") == 2
    assert case.calls.count("sandbox.open") == 1



async def test_policy_binding_constructor_failure_closes_raw_client_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)

    def reject_binding(*args, **kwargs):
        raise RuntimeError("deterministic binding construction rejection")

    monkeypatch.setattr(service_module, "PolicyRuntimeBinding", reject_binding)

    with pytest.raises(V2EpisodeRejected):
        await service.create(case.request)

    assert case.calls.count("policy.close") == 1
    assert "registry.resolve" not in case.calls
    assert "sandbox.open" not in case.calls
    assert not case.repository.events


@pytest.mark.parametrize("failure_at", ["constructor", "registry"])
async def test_pre_admission_primary_and_policy_close_failures_are_both_retained(
    monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
) -> None:
    service, case, _ = await _service(monkeypatch)
    case.policy_client.close_error = _CodedFailure("policy_close_failed")
    if failure_at == "constructor":
        def reject_binding(*args, **kwargs):
            raise _CodedFailure("binding_constructor_failed")

        monkeypatch.setattr(service_module, "PolicyRuntimeBinding", reject_binding)
    else:
        case.registry.error = _CodedFailure("registry_rejected")

    error = (await asyncio.gather(
        service.create(case.request),
        return_exceptions=True,
    ))[0]
    assert isinstance(error, BaseException)

    observed_codes: set[str] = set()
    pending: list[BaseException] = [error]
    groups: list[BaseExceptionGroup] = []
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        code = getattr(item, "code", None)
        failure = getattr(item, "failure", None)
        if isinstance(code, str):
            observed_codes.add(code)
        if failure is not None and isinstance(getattr(failure, "code", None), str):
            observed_codes.add(failure.code)
        if isinstance(item, BaseExceptionGroup):
            groups.append(item)
            pending.extend(item.exceptions)
        if item.__cause__ is not None:
            pending.append(item.__cause__)

    assert groups
    assert {
        (
            "binding_constructor_failed"
            if failure_at == "constructor"
            else "registry_rejected"
        ),
        "policy_close_failed",
    } <= observed_codes
    assert case.calls.count("policy.close") == 1


@pytest.mark.parametrize(
    "allocation_error",
    [
        RuntimeError("deterministic allocation failure"),
        asyncio.CancelledError("deterministic allocation cancellation"),
    ],
)
async def test_allocation_failure_or_cancellation_closes_unowned_policy_binding_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    allocation_error: BaseException,
) -> None:
    service, case, _ = await _service(monkeypatch)
    case.sandbox.open_error = allocation_error

    with pytest.raises(service_module.V2EpisodeUnavailable):
        await service.create(case.request)

    assert case.calls.count("policy.close") == 1
    assert "runner.open" not in case.calls
    assert "policy.invoke" not in case.calls


async def test_allocation_and_policy_close_failures_are_both_durable_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    case.sandbox.open_error = _CodedFailure("allocation_failed")
    case.policy_client.close_error = _CodedFailure("policy_close_failed")

    first, retry = await asyncio.gather(
        service.create(case.request),
        service.create(case.request),
        return_exceptions=True,
    )

    assert isinstance(first, service_module.V2EpisodeUnavailable)
    assert isinstance(retry, service_module.V2EpisodeUnavailable)
    facts = {
        (fact.code, fact.side_effect_boundary)
        for event in case.repository.events
        for fact in (event.primary_fact, event.cleanup_fact)
        if fact is not None
    }
    assert ("allocation_failed", "allocation") in facts
    assert ("policy_close_failed", "session_close") in facts
    assert (
        case.repository.failed_completed_inputs[-1].session_close_failure.code
        == "policy_close_failed"
    )
    assert case.calls.count("policy.close") == 1


async def test_cancelled_create_waiter_and_service_close_cancel_responsive_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    case.sandbox.open_release = asyncio.Event()
    case.sandbox.open_error = None
    create_waiter = asyncio.create_task(service.create(case.request))
    await case.sandbox.open_entered.wait()

    create_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await create_waiter
    shutdown = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert shutdown.done() is False
    case.sandbox.open_error = _CodedFailure("allocation_failed")
    case.sandbox.open_release.set()
    await shutdown

    coordinator = service._coordinators[case.request.episode_id]
    assert coordinator.create_task is not None
    assert coordinator.create_task.done() is True
    assert isinstance(
        coordinator.create_task.exception(),
        service_module.V2EpisodeUnavailable,
    )
    assert coordinator.create_task.exception().failure.code == "process_interrupted"
    assert "cancellation_won" in {
        event.event_kind for event in case.repository.events
    }
    assert "allocation_failed" not in {
        event.event_kind for event in case.repository.events
    }
    assert coordinator.state is EpisodeLifecycleState.CLOSED
    assert len(case.repository.failed_completed_inputs) == 1
    assert len(case.repository.closed_inputs) == 1
    assert not case.repository.quarantine_inputs
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1


async def test_ready_close_retains_policy_close_failure_without_double_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, _ = await _created(monkeypatch)
    case.policy_client.close_error = _CodedFailure("policy_close_failed")

    first, retry = await asyncio.gather(
        service.close_episode(case.request.episode_id),
        service.close_episode(case.request.episode_id),
    )

    assert first.response.state is EpisodeLifecycleState.QUARANTINED
    assert retry.response.state is EpisodeLifecycleState.QUARANTINED
    assert case.repository.quarantine_inputs[-1].failure.code == "policy_close_failed"
    assert any(
        event.primary_fact is not None
        and event.primary_fact.code == "policy_close_failed"
        and event.primary_fact.side_effect_boundary == "session_close"
        for event in case.repository.events
    )
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("lease.close") == 1




async def test_real_repository_preallocation_cancel_uses_legal_durable_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    case.policy_resolver.entered = asyncio.Event()
    case.policy_resolver.release = asyncio.Event()
    accepted_durable = asyncio.Event()
    append_transition = repository.append_transition

    def observe_transition(event):
        result = append_transition(event)
        if event.event_kind == "accepted":
            accepted_durable.set()
        return result

    monkeypatch.setattr(repository, "append_transition", observe_transition)
    create_task = asyncio.create_task(service.create(case.request))
    await case.policy_resolver.entered.wait()
    coordinator = service._coordinators[case.request.episode_id]
    await coordinator.lock.acquire()
    cancel_started = asyncio.Event()

    async def request_cancel():
        cancel_started.set()
        return await service.cancel(case.request.episode_id, "pre-allocation stop")

    cancel_task = asyncio.create_task(request_cancel())
    await cancel_started.wait()
    case.policy_resolver.release.set()
    await accepted_durable.wait()
    coordinator.lock.release()
    cancelled, create_error = await asyncio.gather(
        cancel_task,
        create_task,
        return_exceptions=True,
    )

    assert not isinstance(cancelled, BaseException)
    assert cancelled.requested is True
    assert isinstance(create_error, service_module.V2EpisodeUnavailable)
    assert create_error.failure.code == "process_interrupted"
    recovered = repository.recover(case.request.episode_id)
    assert recovered is not None
    assert [
        (event.from_state, event.to_state, event.event_kind)
        for event in recovered.events
    ] == [
        (None, "accepted", "accepted"),
        ("accepted", "cancel_requested", "cancellation_requested"),
        ("cancel_requested", "closing", "cancellation_won"),
        ("closing", "closed", "closed"),
    ]
    receipt = recovered.events[1]
    assert receipt.cancel_reason == "pre-allocation stop"
    assert receipt.cancel_fingerprint == cancellation_fingerprint(
        case.request.episode_id,
        coordinator.create_fingerprint,
        "pre-allocation stop",
    )
    assert recovered.closed_envelope is not None
    assert recovered.closed_envelope.cleanup_receipt is None
    assert case.calls.count("policy.close") == 1
    assert "sandbox.open" not in case.calls
    await service.close()


async def test_cancel_during_resistant_open_fences_ready_and_closes_returned_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    open_entered = asyncio.Event()
    open_release = asyncio.Event()
    first_cancel_observed = asyncio.Event()
    cancellation_count = 0

    async def resistant_open(request):
        nonlocal cancellation_count
        case.calls.append("sandbox.open")
        case.sandbox.open_arguments.append(request)
        open_entered.set()
        while not open_release.is_set():
            try:
                await open_release.wait()
            except asyncio.CancelledError:
                cancellation_count += 1
                asyncio.current_task().uncancel()
                first_cancel_observed.set()
        return case.sandbox.lease

    monkeypatch.setattr(case.sandbox, "open", resistant_open)
    create_task = asyncio.create_task(service.create(case.request))
    await open_entered.wait()
    first = await service.cancel(case.request.episode_id, "cancel blocked open")
    await first_cancel_observed.wait()
    retry = await service.cancel(case.request.episode_id, "ignored retry reason")
    open_release.set()
    create_error = (await asyncio.gather(create_task, return_exceptions=True))[0]
    assert cancellation_count == 1

    assert first.requested is retry.requested is True
    assert first.reason == retry.reason == "cancel blocked open"
    assert isinstance(create_error, service_module.V2EpisodeUnavailable)
    assert create_error.failure.code == "process_interrupted"
    recovered = repository.recover(case.request.episode_id)
    assert recovered is not None
    assert [
        (event.from_state, event.to_state, event.event_kind)
        for event in recovered.events
    ] == [
        (None, "accepted", "accepted"),
        ("accepted", "allocating", "allocation_started"),
        ("allocating", "cancel_requested", "cancellation_requested"),
        ("cancel_requested", "closing", "cancellation_won"),
        ("closing", "closed", "closed"),
    ]
    assert "workspace_ready" not in {
        event.event_kind for event in recovered.events
    }
    assert recovered.primary_lease_id == case.sandbox.lease.lease_id
    assert recovered.closed_envelope is not None
    expected_receipt = case.sandbox.lease.close_receipt
    assert recovered.closed_envelope.cleanup_receipt == {
        "lease_id": expected_receipt.lease_id,
        "steps": [
            {
                "resource": step.resource,
                "state": step.state.value,
                "detail": step.detail,
            }
            for step in expected_receipt.steps
        ],
        "state": expected_receipt.state.value,
    }
    assert case.calls.count("lease.close") == 1
    assert case.calls.count("policy.close") == 1
    await service.close()


async def test_allocation_error_racing_durable_cancel_retains_allocation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    open_release = asyncio.Event()
    binding_close_entered = asyncio.Event()
    binding_close_release = asyncio.Event()

    async def failing_open(request):
        case.calls.append("sandbox.open")
        case.sandbox.open_entered.set()
        await open_release.wait()
        raise _CodedFailure("allocation_race_failed")

    async def blocked_binding_close():
        case.calls.append("policy.close")
        binding_close_entered.set()
        await binding_close_release.wait()

    monkeypatch.setattr(case.sandbox, "open", failing_open)
    monkeypatch.setattr(case.policy_client, "close", blocked_binding_close)
    create_task = asyncio.create_task(service.create(case.request))
    await case.sandbox.open_entered.wait()
    open_release.set()
    await binding_close_entered.wait()
    cancelled = await service.cancel(case.request.episode_id, "cancel after open error")
    binding_close_release.set()
    create_error = (await asyncio.gather(create_task, return_exceptions=True))[0]

    assert cancelled.requested is True
    assert isinstance(create_error, service_module.V2EpisodeUnavailable)
    assert create_error.failure.code == "allocation_race_failed"
    recovered = repository.recover(case.request.episode_id)
    assert recovered is not None
    assert [
        (event.from_state, event.to_state, event.event_kind)
        for event in recovered.events
    ] == [
        (None, "accepted", "accepted"),
        ("accepted", "allocating", "allocation_started"),
        ("allocating", "cancel_requested", "cancellation_requested"),
        ("cancel_requested", "closing", "cancellation_won"),
        ("closing", "closed", "closed"),
    ]
    assert recovered.events[3].primary_fact is not None
    assert recovered.events[3].primary_fact.code == "allocation_race_failed"
    assert recovered.completed_envelope is not None
    assert recovered.completed_envelope.primary_outcome == "failed"
    assert case.calls.count("policy.close") == 1
    await service.close()


@pytest.mark.parametrize("close_fails", [False, True])
async def test_repeated_cancel_and_shutdown_join_policy_binding_close_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    close_fails: bool,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    case.sandbox.open_release = asyncio.Event()
    binding_close_entered = asyncio.Event()
    binding_close_release = asyncio.Event()
    sandbox_shutdown_entered = asyncio.Event()
    loop_failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    sandbox_close = case.sandbox.close

    async def blocked_binding_close():
        case.calls.append("policy.close")
        binding_close_entered.set()
        await binding_close_release.wait()
        if close_fails:
            raise _CodedFailure("policy_close_failed")

    async def observe_sandbox_shutdown():
        sandbox_shutdown_entered.set()
        return await sandbox_close()

    monkeypatch.setattr(case.policy_client, "close", blocked_binding_close)
    monkeypatch.setattr(case.sandbox, "close", observe_sandbox_shutdown)
    loop.set_exception_handler(lambda _loop, context: loop_failures.append(context))
    try:
        create_waiter = asyncio.create_task(service.create(case.request))
        await case.sandbox.open_entered.wait()
        coordinator = service._coordinators[case.request.episode_id]
        create_owner = coordinator.create_task
        assert create_owner is not None

        first = await service.cancel(
            case.request.episode_id,
            "cancel responsive allocation",
        )
        await binding_close_entered.wait()
        binding = coordinator.binding
        assert binding is not None
        physical_close = binding._close_task
        assert physical_close is not None

        repeated = [
            asyncio.create_task(
                service.cancel(case.request.episode_id, f"ignored repeat {index}")
            )
            for index in range(8)
        ]
        shutdown = asyncio.create_task(service.close())
        repeated_receipts = await asyncio.gather(*repeated)
        for _ in range(10):
            await asyncio.sleep(0)

        assert first.requested is True
        assert all(receipt.requested is True for receipt in repeated_receipts)
        assert {
            receipt.reason for receipt in repeated_receipts
        } == {"cancel responsive allocation"}
        assert create_owner.cancelling() == 1
        assert coordinator.binding_released is False
        assert physical_close.done() is False
        assert shutdown.done() is False
        assert sandbox_shutdown_entered.is_set() is False
        assert repository.recover(case.request.episode_id).events[-1].to_state == (
            EpisodeLifecycleState.CANCEL_REQUESTED.value
        )
        assert not repository.recover(case.request.episode_id).completed_envelope
        assert not repository.recover(case.request.episode_id).closed_envelope

        binding_close_release.set()
        create_error, shutdown_result = await asyncio.gather(
            create_waiter,
            shutdown,
            return_exceptions=True,
        )
        await asyncio.sleep(0)
    finally:
        binding_close_release.set()
        loop.set_exception_handler(previous_handler)

    assert isinstance(create_error, service_module.V2EpisodeUnavailable)
    assert create_error.failure.code == "process_interrupted"
    assert shutdown_result is None
    assert coordinator.binding_released is True
    assert physical_close.done() is True
    assert sandbox_shutdown_entered.is_set() is True
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1
    assert not loop_failures
    assert not service._active_tasks
    assert not service._unclaimed_task_failures

    recovered = repository.recover(case.request.episode_id)
    replayed = repository.recover(case.request.episode_id)
    assert recovered is not None and replayed is not None
    assert [event.digest for event in replayed.events] == [
        event.digest for event in recovered.events
    ]
    assert [
        (event.from_state, event.to_state, event.event_kind)
        for event in recovered.events
    ] == [
        (None, "accepted", "accepted"),
        ("accepted", "allocating", "allocation_started"),
        ("allocating", "cancel_requested", "cancellation_requested"),
        ("cancel_requested", "closing", "cancellation_won"),
        ("closing", "closed", "closed"),
    ]
    cancellation_won = recovered.events[3]
    assert cancellation_won.primary_fact is not None
    assert cancellation_won.primary_fact.code == "process_interrupted"
    assert cancellation_won.primary_fact.side_effect_boundary == "allocation"
    if close_fails:
        assert cancellation_won.cleanup_fact is not None
        assert cancellation_won.cleanup_fact.code == "policy_close_failed"
        assert cancellation_won.cleanup_fact.side_effect_boundary == "session_close"
    else:
        assert cancellation_won.cleanup_fact is None
    assert recovered.completed_envelope is not None
    assert recovered.completed_envelope.primary_outcome == "cancelled"
    assert recovered.closed_envelope is not None

    after_close = await service.cancel(
        case.request.episode_id,
        "ignored after close",
    )
    assert after_close.requested is False
    assert after_close.reason == "cancel responsive allocation"
    assert after_close.state is EpisodeLifecycleState.CLOSED



@pytest.mark.parametrize("entry_baseline", [0, 1, 2])
@pytest.mark.parametrize("child_outcome", ["success", "failure", "cancel"])
@pytest.mark.parametrize("new_outer_cancels", [0, 1, 2])
async def test_observe_owned_task_preserves_entry_cancellation_baseline_and_terminal_child(
    entry_baseline: int,
    child_outcome: str,
    new_outer_cancels: int,
) -> None:
    child_entered = asyncio.Event()
    child_release = asyncio.Event()
    loop_failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    class ResultCountingTask(asyncio.Task[str]):
        result_calls = 0

        def result(self) -> str:
            self.result_calls += 1
            return super().result()

    async def child_owner() -> str:
        child_entered.set()
        await child_release.wait()
        if child_outcome == "failure":
            raise _CodedFailure("owned_child_failed")
        return "terminal-result"

    async def observe_with_baseline():
        current = asyncio.current_task()
        assert current is not None
        for request in range(entry_baseline):
            current.cancel(f"entry-baseline-{request}")
        if entry_baseline:
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        assert current.cancelling() == entry_baseline
        child = ResultCountingTask(child_owner())
        outcome = await service_module._observe_owned_task(child)
        return outcome, current.cancelling(), child

    loop.set_exception_handler(lambda _loop, context: loop_failures.append(context))
    observer = asyncio.create_task(observe_with_baseline())
    try:
        await child_entered.wait()
        await asyncio.sleep(0)
        for request in range(new_outer_cancels):
            observer.cancel(f"new-outer-{request}")
        if child_outcome == "cancel":
            child = next(
                task
                for task in asyncio.all_tasks()
                if isinstance(task, ResultCountingTask)
            )
            child.cancel("inner-child-cancel")
        else:
            child_release.set()
        (value, outer_cancellation, child_failure), final_count, child = (
            await observer
        )
        await asyncio.sleep(0)
    finally:
        child_release.set()
        loop.set_exception_handler(previous_handler)

    assert final_count == entry_baseline
    assert (outer_cancellation is not None) is (new_outer_cancels > 0)
    if new_outer_cancels:
        assert isinstance(outer_cancellation, asyncio.CancelledError)
    if child_outcome == "success":
        assert value == "terminal-result"
        assert child_failure is None
    else:
        assert value is None
        expected_failure = (
            _CodedFailure
            if child_outcome == "failure"
            else asyncio.CancelledError
        )
        assert isinstance(child_failure, expected_failure)
    assert child.done() is True
    assert child.result_calls == 1
    assert observer.done() is True
    assert not loop_failures


@pytest.mark.parametrize("child_outcome", ["success", "failure", "cancel"])
async def test_terminal_policy_close_racing_parent_cancel_is_observed_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    child_outcome: str,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    case.sandbox.open_release = asyncio.Event()
    binding_close_entered = asyncio.Event()
    binding_close_release = asyncio.Event()
    publication_after_physical_close: list[bool] = []
    parent_handler_terminality: list[tuple[bool, bool]] = []
    loop_failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    publish_failed_completed = repository.publish_failed_completed
    physical_close: asyncio.Task[None] | None = None
    original_close_unowned_binding = service._close_unowned_binding

    async def terminal_binding_close():
        case.calls.append("policy.close")
        binding_close_entered.set()
        await binding_close_release.wait()
        if child_outcome == "failure":
            raise _CodedFailure("policy_close_failed")

    async def observe_parent_close_handler(coordinator):
        result = await original_close_unowned_binding(coordinator)
        child = coordinator.binding_close_task
        binding = coordinator.binding
        physical = binding._close_task if binding is not None else None
        parent_handler_terminality.append(
            (
                child is not None and child.done(),
                physical is not None and physical.done(),
            )
        )
        return result


    def observe_failed_publication(inputs):
        publication_after_physical_close.append(
            physical_close is not None and physical_close.done()
        )
        return publish_failed_completed(inputs)

    monkeypatch.setattr(
        service,
        "_close_unowned_binding",
        observe_parent_close_handler,
    )
    monkeypatch.setattr(case.policy_client, "close", terminal_binding_close)
    monkeypatch.setattr(
        repository,
        "publish_failed_completed",
        observe_failed_publication,
    )
    loop.set_exception_handler(lambda _loop, context: loop_failures.append(context))
    try:
        create_waiter = asyncio.create_task(service.create(case.request))
        await case.sandbox.open_entered.wait()
        coordinator = service._coordinators[case.request.episode_id]
        create_owner = coordinator.create_task
        assert create_owner is not None

        first = await service.cancel(
            case.request.episode_id,
            "cancel responsive allocation",
        )
        await binding_close_entered.wait()
        binding = coordinator.binding
        child = coordinator.binding_close_task
        assert binding is not None and child is not None
        physical_close = binding._close_task
        assert physical_close is not None
        assert create_owner.cancelling() == 1

        binding_close_release.set()
        if child_outcome == "cancel":
            child.cancel()
        create_owner.cancel()
        create_error = (
            await asyncio.gather(create_waiter, return_exceptions=True)
        )[0]
        await service.close()
    finally:
        binding_close_release.set()
        loop.set_exception_handler(previous_handler)

    assert first.requested is True
    assert isinstance(create_error, service_module.V2EpisodeUnavailable)
    assert create_error.failure.code == "process_interrupted"
    assert create_owner.cancelling() == 1
    assert child.done() is True
    assert child.cancelled() is False
    assert physical_close.done() is True
    assert coordinator.binding_released is True
    assert publication_after_physical_close == [True]
    assert parent_handler_terminality
    assert all(
        child_terminal and physical_terminal
        for child_terminal, physical_terminal in parent_handler_terminality
    )
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1
    assert not loop_failures
    assert not service._active_tasks
    assert not service._unclaimed_task_failures

    recovered = repository.recover(case.request.episode_id)
    replayed = repository.recover(case.request.episode_id)
    assert recovered is not None and replayed is not None
    assert [event.digest for event in replayed.events] == [
        event.digest for event in recovered.events
    ]
    cancellation_won = next(
        event for event in recovered.events if event.event_kind == "cancellation_won"
    )
    assert cancellation_won.primary_fact is not None
    assert cancellation_won.primary_fact.code == "process_interrupted"
    expected_cleanup = {
        "success": None,
        "failure": "policy_close_failed",
        "cancel": "process_interrupted",
    }[child_outcome]
    assert (
        cancellation_won.cleanup_fact.code
        if cancellation_won.cleanup_fact is not None
        else None
    ) == expected_cleanup
    assert recovered.completed_envelope is not None
    assert recovered.completed_envelope.primary_outcome == "cancelled"

@pytest.mark.parametrize("coalesced_outer_cancel", [False, True])
@pytest.mark.parametrize("physical_outcome", ["success", "failure", "cancel"])
async def test_session_close_owner_is_shared_and_terminal_before_shutdown_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    physical_outcome: str,
    coalesced_outer_cancel: bool,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    physical_close_entered = asyncio.Event()
    physical_close_release = asyncio.Event()
    shutdown_cancel_observed = asyncio.Event()
    side_effect_terminality: list[tuple[str, bool, bool, bool]] = []
    shutdown_cancel_counts: list[int] = []
    loop_failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    original_open = case.runner.open
    original_cancel = service.cancel
    original_publish_failed = repository.publish_failed_completed
    original_lease_close = case.sandbox.lease.close
    original_sandbox_close = case.sandbox.close
    session: DeterministicSession | None = None
    physical_close: asyncio.Task[None] | None = None
    coordinator_task: asyncio.Task[object] | None = None

    async def retained_session_open(*args, **kwargs):
        nonlocal session
        session = await original_open(*args, **kwargs)
        original_session_close = session.close
        session._close_task = None

        async def retained_session_close():
            if session._close_task is None:
                session._close_task = asyncio.create_task(
                    original_session_close(),
                    name="test-retained-conductor-session-close",
                )
            await asyncio.shield(session._close_task)
            return session._close_task.result()

        session.close = retained_session_close
        return session

    async def blocked_physical_close():
        case.calls.append("policy.close")
        physical_close_entered.set()
        await physical_close_release.wait()
        if physical_outcome == "failure":
            raise _CodedFailure("policy_close_failed")

    async def observe_shutdown_cancel(episode_id, reason):
        result = await original_cancel(episode_id, reason)
        if not shutdown_cancel_counts:
            coordinator = service._coordinators[episode_id]
            run_owner = coordinator.run_task
            assert run_owner is not None
            run_owner.cancel("shutdown session-close cancellation")
            if coalesced_outer_cancel:
                run_owner.cancel("coalesced session-close cancellation")
            coordinator.owner_cancel_sent = True
            shutdown_cancel_counts.append(run_owner.cancelling())
            shutdown_cancel_observed.set()
        return result

    def terminality() -> tuple[bool, bool, bool]:
        return (
            coordinator_task is not None and coordinator_task.done(),
            session is not None
            and session._close_task is not None
            and session._close_task.done(),
            physical_close is not None and physical_close.done(),
        )

    def observe_failed_publication(inputs):
        side_effect_terminality.append(("publish_failed", *terminality()))
        return original_publish_failed(inputs)

    async def observe_lease_close():
        side_effect_terminality.append(("lease_close", *terminality()))
        return await original_lease_close()

    async def observe_sandbox_close():
        side_effect_terminality.append(("sandbox_close", *terminality()))
        return await original_sandbox_close()

    monkeypatch.setattr(case.runner, "open", retained_session_open)
    monkeypatch.setattr(case.policy_client, "close", blocked_physical_close)
    monkeypatch.setattr(service, "cancel", observe_shutdown_cancel)
    monkeypatch.setattr(
        repository,
        "publish_failed_completed",
        observe_failed_publication,
    )
    monkeypatch.setattr(case.sandbox.lease, "close", observe_lease_close)
    monkeypatch.setattr(case.sandbox, "close", observe_sandbox_close)
    loop.set_exception_handler(lambda _loop, context: loop_failures.append(context))
    try:
        created = await service.create(case.request)
        run_waiter = asyncio.create_task(
            service.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"session-close": physical_outcome},
            )
        )
        await physical_close_entered.wait()
        coordinator = service._coordinators[case.request.episode_id]
        run_owner = coordinator.run_task
        coordinator_task = coordinator.session_close_task
        binding = coordinator.binding
        assert run_owner is not None
        assert coordinator_task is not None
        assert session is not None and session._close_task is not None
        assert binding is not None
        physical_close = binding._close_task
        assert physical_close is not None

        first_joiner = asyncio.create_task(
            service._close_owned_session(coordinator)
        )
        second_joiner = asyncio.create_task(
            service._close_owned_session(coordinator)
        )
        await asyncio.sleep(0)
        assert coordinator.session_close_task is coordinator_task
        assert case.calls.count("session.close") == 1

        shutdown = asyncio.create_task(service.close())
        await shutdown_cancel_observed.wait()
        assert shutdown_cancel_counts == [
            2 if coalesced_outer_cancel else 1
        ]
        assert coordinator.owner_cancel_sent is True
        assert not run_waiter.done()
        assert not shutdown.done()
        assert not first_joiner.done()
        assert not second_joiner.done()
        assert not side_effect_terminality

        if physical_outcome == "cancel":
            physical_close.cancel("inner physical close cancellation")
        else:
            physical_close_release.set()
        run_result, first_result, second_result, shutdown_result = (
            await asyncio.gather(
                run_waiter,
                first_joiner,
                second_joiner,
                shutdown,
                return_exceptions=True,
            )
        )
        await asyncio.sleep(0)
    finally:
        physical_close_release.set()
        loop.set_exception_handler(previous_handler)

    assert not isinstance(run_result, BaseException)
    assert run_result.response.primary_disposition is (
        EpisodePrimaryDisposition.CANCELLED
    )
    assert first_result[0] is None
    assert second_result[0] is None
    expected_close_error_code = {
        "success": None,
        "failure": "policy_close_failed",
        "cancel": "policy_close_failed",
    }[physical_outcome]
    if expected_close_error_code is None:
        assert first_result[1] is None
        assert second_result[1] is None
    else:
        assert getattr(first_result[1], "code", None) == (
            expected_close_error_code
        )
        assert getattr(second_result[1], "code", None) == (
            expected_close_error_code
        )
    assert shutdown_result is None
    assert run_owner.cancelling() == 0
    assert coordinator_task.done() is True
    assert session._close_task.done() is True
    assert physical_close.done() is True
    assert side_effect_terminality
    assert all(
        tuple(flags) == (True, True, True)
        for _, *flags in side_effect_terminality
    )
    assert case.calls.count("session.close") == 1
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("lease.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1
    assert not loop_failures
    assert not service._active_tasks
    assert not service._unclaimed_task_failures
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name().startswith(
            ("bb-v2-", "test-retained-conductor-session-close")
        )
    ]

    recovered = repository.recover(case.request.episode_id)
    replayed = repository.recover(case.request.episode_id)
    assert recovered is not None and replayed is not None
    assert [event.digest for event in replayed.events] == [
        event.digest for event in recovered.events
    ]
    run_failed = next(
        event for event in recovered.events if event.event_kind == "run_failed"
    )
    assert run_failed.primary_fact is not None
    assert run_failed.primary_fact.code == "process_interrupted"
    expected_cleanup = {
        "success": None,
        "failure": "policy_close_failed",
        "cancel": "policy_close_failed",
    }[physical_outcome]
    assert (
        run_failed.cleanup_fact.code
        if run_failed.cleanup_fact is not None
        else None
    ) == expected_cleanup


@pytest.mark.parametrize("repeated_cancellation", [False, True])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_verifier_close_task_is_owned_observed_and_joined_before_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    repeated_cancellation: bool,
    close_fails: bool,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    created = await service.create(case.request)
    verifier_close_entered = asyncio.Event()
    verifier_close_release = asyncio.Event()
    shutdown_cancel_observed = asyncio.Event()
    loop_failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    original_cancel = service.cancel
    publish_closed = repository.publish_closed

    async def blocked_verifier_close():
        case.calls.append("verifier.close")
        verifier_close_entered.set()
        await verifier_close_release.wait()
        if close_fails:
            raise _CodedFailure("verifier_close_failed")
        return case.sandbox.verifier.close_receipt

    async def observe_shutdown_cancel(episode_id, reason):
        result = await original_cancel(episode_id, reason)
        shutdown_cancel_observed.set()
        return result

    def observe_closed(inputs):
        case.calls.append("repo.publish_closed")
        return publish_closed(inputs)

    monkeypatch.setattr(case.sandbox.verifier, "close", blocked_verifier_close)
    monkeypatch.setattr(service, "cancel", observe_shutdown_cancel)
    monkeypatch.setattr(repository, "publish_closed", observe_closed)
    loop.set_exception_handler(lambda _loop, context: loop_failures.append(context))
    try:
        run_waiter = asyncio.create_task(
            service.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"verifier-close": "owned"},
            )
        )
        await verifier_close_entered.wait()
        coordinator = service._coordinators[case.request.episode_id]
        parent = coordinator.run_task
        child = coordinator.verifier_cleanup_task
        assert parent is not None and child is not None
        shutdown = asyncio.create_task(service.close())
        await shutdown_cancel_observed.wait()
        assert coordinator.owner_cancel_sent is True
        assert parent.cancelling() == 1
        if repeated_cancellation:
            parent.cancel()
        assert child.done() is False
        assert parent in service._active_tasks
        assert shutdown.done() is False
        verifier_close_release.set()
        run_result, shutdown_result = await asyncio.gather(
            run_waiter,
            shutdown,
        )
        await service.close()
    finally:
        loop.set_exception_handler(previous_handler)

    assert run_result.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    assert shutdown_result is None
    assert child.done() is True
    assert parent not in service._active_tasks
    assert not service._active_tasks
    assert not service._unclaimed_task_failures
    assert not loop_failures
    recovered = repository.recover(case.request.episode_id)
    assert recovered is not None
    verification_failed = next(
        event
        for event in recovered.events
        if event.event_kind == "verification_failed"
    )
    assert verification_failed.primary_fact is not None
    assert verification_failed.primary_fact.code == "process_interrupted"
    if close_fails:
        assert verification_failed.cleanup_fact is not None
        assert verification_failed.cleanup_fact.code == "verifier_close_failed"
        assert recovered.verifier_cleanup_receipt is None
        assert recovered.closed_envelope is not None
        assert recovered.locator.current_state == "closed"
    else:
        assert verification_failed.cleanup_fact is None
        assert coordinator.verifier_cleanup_receipt == (
            case.sandbox.verifier.close_receipt
        )
        assert recovered.closed_envelope is None
        assert recovered.quarantined is True
        assert recovered.locator.current_state == "quarantined"
        assert coordinator.cleanup_disposition is EpisodeCleanupDisposition.QUARANTINED
        quarantine_event = recovered.events[-1]
        assert quarantine_event.event_kind == "quarantined"
        assert quarantine_event.primary_fact is not None
        assert quarantine_event.primary_fact.code == "process_interrupted"
        assert quarantine_event.cleanup_fact is not None
        assert quarantine_event.cleanup_fact.code == "closed_publication_failed"
    assert case.calls.count("verifier.close") == 1
    assert case.calls.count("lease.close") == 1
    assert case.calls.index("verifier.close") < case.calls.index("lease.close")
    assert case.calls.index("lease.close") < case.calls.index("repo.publish_closed")
    assert case.calls.index("repo.publish_closed") < case.calls.index(
        "sandbox.manager.close"
    )



@pytest.mark.parametrize("child_outcome", ["success", "failure", "cancel"])
async def test_terminal_verifier_close_racing_parent_cancel_retains_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    child_outcome: str,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    created = await service.create(case.request)
    verifier_close_entered = asyncio.Event()
    verifier_close_release = asyncio.Event()
    loop_failures: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    async def terminal_verifier_close():
        case.calls.append("verifier.close")
        verifier_close_entered.set()
        await verifier_close_release.wait()
        if child_outcome == "failure":
            raise _CodedFailure("verifier_close_failed")
        return case.sandbox.verifier.close_receipt

    monkeypatch.setattr(case.sandbox.verifier, "close", terminal_verifier_close)
    loop.set_exception_handler(lambda _loop, context: loop_failures.append(context))
    try:
        run_waiter = asyncio.create_task(
            service.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"verifier-close": f"terminal-{child_outcome}-race"},
            )
        )
        await verifier_close_entered.wait()
        coordinator = service._coordinators[case.request.episode_id]
        parent = coordinator.run_task
        child = coordinator.verifier_cleanup_task
        assert parent is not None and child is not None

        if child_outcome == "cancel":
            child.cancel()
        else:
            verifier_close_release.set()
        parent.cancel()
        run_result = await run_waiter
        await service.close()
    finally:
        verifier_close_release.set()
        loop.set_exception_handler(previous_handler)

    assert child.done() is True
    assert parent.cancelling() == 0
    assert run_result.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    recovered = repository.recover(case.request.episode_id)
    replayed = repository.recover(case.request.episode_id)
    assert recovered is not None and replayed is not None
    assert [event.digest for event in replayed.events] == [
        event.digest for event in recovered.events
    ]
    verification_failed = next(
        event
        for event in recovered.events
        if event.event_kind == "verification_failed"
    )
    assert verification_failed.primary_fact is not None
    assert verification_failed.primary_fact.code == "process_interrupted"
    expected_cleanup = {
        "success": None,
        "failure": "verifier_close_failed",
        "cancel": "process_interrupted",
    }[child_outcome]
    assert (
        verification_failed.cleanup_fact.code
        if verification_failed.cleanup_fact is not None
        else None
    ) == expected_cleanup
    assert (
        recovered.completed_envelope is None
        or recovered.completed_envelope.primary_outcome != "succeeded"
    )
    assert case.calls.count("verifier.close") == 1
    assert not loop_failures
    assert not service._active_tasks
    assert not service._unclaimed_task_failures

async def test_docker_runtime_unsupported_is_a_rejection_not_local_container_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)

    class RuntimeUnsupported(RuntimeError):
        code = "runtime_unsupported"

    def unsupported(*args, **kwargs):
        raise RuntimeUnsupported("deterministic Docker gate")

    monkeypatch.setattr(service_module, "build_sandbox_execution_plan", unsupported)
    with pytest.raises(V2EpisodeRejected) as caught:
        await service.create(case.request)
    assert caught.value.failure.code == "runtime_unsupported"
    assert "sandbox.open" not in case.calls
    assert "repo.publish_closed" not in case.calls


async def test_identical_create_and_run_retries_coalesce_once_and_changed_fingerprints_conflict_pre_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    duplicate = await service.create(case.request)
    assert duplicate.response == created.response
    assert case.calls.count("resolve") == 1
    assert case.calls.count("sandbox.open") == 1

    first, second = await asyncio.gather(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"same": True},
        ),
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"same": True},
        ),
    )
    assert {first.disposition, second.disposition} == {
        V2OperationDisposition.FRESH,
        V2OperationDisposition.CACHED,
    }
    assert case.calls.count("runner.open") == 1
    assert case.calls.count("session.run") == 1

    side_effect_count = len(case.calls)
    with pytest.raises(V2EpisodeConflict):
        await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"same": False},
        )
    assert len(case.calls) == side_effect_count


async def test_same_process_ready_create_retry_uses_live_cached_response_before_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, preflights, created = await _created(monkeypatch)
    recover_calls = case.calls.count(f"repo.recover:{case.request.episode_id}")
    effects_before_retry = tuple(case.calls)
    case.repository.recover_error = AssertionError(
        "durable recovery must not run for a live coordinator"
    )

    cached = await service.create(case.request)

    assert cached.disposition is V2OperationDisposition.CACHED
    assert cached.response == created.response
    assert cached.response.state is EpisodeLifecycleState.READY
    assert case.calls.count(f"repo.recover:{case.request.episode_id}") == recover_calls
    assert tuple(case.calls) == effects_before_retry

    assert len(preflights) == 1

@pytest.mark.parametrize("failure_at", ["runner", "policy_close", "verifier", "artifact"])
async def test_primary_failures_still_attempt_authoritative_cleanup_and_preserve_no_false_success(
    monkeypatch: pytest.MonkeyPatch, failure_at: str
) -> None:
    service, case, _, created = await _created(monkeypatch)
    if failure_at == "runner":
        case.runner.open_error = RuntimeError("deterministic runner failure")
    elif failure_at == "policy_close":
        # The session owns and closes the exact binding/client.
        case.policy_client.close_error = RuntimeError("deterministic policy close failure")
    elif failure_at == "verifier":
        case.sandbox.verifier.execute_error = RuntimeError("deterministic verifier failure")
    else:
        case.repository.publish_completed_error = RuntimeError("deterministic artifact publication failure")

    if failure_at == "artifact":
        with pytest.raises(RuntimeError, match="artifact publication"):
            await service.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"failure": failure_at},
            )
    else:
        outcome = await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"failure": failure_at},
        )
        assert outcome.response.primary_disposition is EpisodePrimaryDisposition.FAILED
        assert outcome.response.completed_envelope_ref == ref("completed-envelope")
        assert outcome.response.closed_envelope_ref == ref("closed-envelope")
        assert not case.repository.completed_inputs
        assert case.repository.failed_completed_inputs
        assert case.repository.closed_inputs[-1].final_primary_outcome == "failed"
    assert case.calls.count("lease.close") == 1
    if failure_at == "artifact":
        assert "repo.publish_closed" not in case.calls


async def test_failed_or_quarantined_detailed_cleanup_never_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.sandbox.lease.close_receipt = failed_receipt()

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"cleanup": "fails"},
    )
    state = await service.get_state(case.request.episode_id)

    assert outcome.response.closed_envelope_ref is None
    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert state.cleanup_disposition is EpisodeCleanupDisposition.QUARANTINED
    assert case.repository.quarantine_inputs
    assert not case.repository.closed_inputs


@pytest.mark.parametrize(
    ("cleanup_error", "expected_state"),
    [
        (None, EpisodeLifecycleState.CLOSED),
        (RuntimeError("deterministic cleanup failure"), EpisodeLifecycleState.QUARANTINED),
    ],
)
async def test_first_live_cancel_receipt_wins_and_replays_after_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_error: BaseException | None,
    expected_state: EpisodeLifecycleState,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.sandbox.lease.close_error = cleanup_error
    run_task = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"cancel": "during-run"},
        )
    )
    while case.runner.session is None:
        await asyncio.sleep(0)
    case.runner.session.block_run = True
    await case.runner.session.run_entered.wait()

    first = await service.cancel(case.request.episode_id, "operator requested A")
    retry = await service.cancel(case.request.episode_id, "operator requested B")
    case.runner.session.run_release.set()
    outcome = await run_task
    terminal_retry = await service.cancel(
        case.request.episode_id,
        "operator requested B",
    )

    expected_fingerprint = cancellation_fingerprint(
        case.request.episode_id,
        created.response.create_fingerprint,
        "operator requested A",
    )
    cancellation_events = [
        event
        for event in case.repository.events
        if event.event_kind == "cancellation_requested"
    ]
    assert first.requested is True
    assert first.reason == retry.reason == terminal_retry.reason == "operator requested A"
    assert retry.requested is True
    assert terminal_retry.requested is False
    assert first.state is retry.state is EpisodeLifecycleState.CANCEL_REQUESTED
    assert terminal_retry.state is expected_state
    assert len(cancellation_events) == 1
    assert cancellation_events[0].cancel_reason == "operator requested A"
    assert cancellation_events[0].cancel_fingerprint == expected_fingerprint
    assert all(
        event.cancel_reason == "operator requested A"
        and event.cancel_fingerprint == expected_fingerprint
        for event in case.repository.events[cancellation_events[0].sequence :]
    )
    assert case.calls.count("session.cancel:operator requested A") == 1
    assert "session.cancel:operator requested B" not in case.calls
    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.CANCELLED
    assert case.calls.count("lease.close") == 1
    assert case.calls.count("policy.close") == 1
    assert not case.repository.completed_inputs
    assert case.repository.failed_completed_inputs[-1].primary_disposition == "cancelled"
    if expected_state is EpisodeLifecycleState.CLOSED:
        assert outcome.response.closed_envelope_ref == ref("closed-envelope")
        assert case.repository.closed_inputs[-1].final_primary_outcome == "cancelled"
    else:
        assert outcome.response.closed_envelope_ref is None
        assert case.repository.quarantine_inputs


async def test_close_and_shutdown_are_idempotent_and_closed_is_absorbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"close": "ordering"},
    )
    first, second = await asyncio.gather(
        service.close_episode(case.request.episode_id),
        service.close_episode(case.request.episode_id),
    )
    assert first.response.closed_envelope_ref == second.response.closed_envelope_ref
    assert case.calls.count("lease.close") == 1
    assert case.calls.count("policy.close") == 1
    await service.close()
    assert case.calls.count("sandbox.manager.close") == 1
    assert (await service.get_state(case.request.episode_id)).state is EpisodeLifecycleState.CLOSED



async def test_run_after_cancel_rejects_before_fingerprint_task_or_runtime_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    cancelled = await service.cancel(case.request.episode_id, "operator cancelled before run")
    coordinator = service._coordinators[case.request.episode_id]
    calls_before_run = tuple(case.calls)

    with pytest.raises(V2EpisodeConflict) as first:
        await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"admission": "cancelled"},
        )

    assert cancelled.requested is True
    assert first.value.failure.code == "episode_not_ready"
    assert coordinator.run_fingerprint is None
    assert coordinator.run_task is None
    assert tuple(case.calls) == calls_before_run

    with pytest.raises(V2EpisodeConflict) as retry:
        await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"admission": "cancelled"},
        )
    assert retry.value.failure.code == "episode_not_ready"
    assert coordinator.run_fingerprint is None
    assert coordinator.run_task is None
    assert tuple(case.calls) == calls_before_run


async def test_run_after_close_rejects_before_fingerprint_task_or_runtime_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    await service.close_episode(case.request.episode_id)
    assert case.calls.count("policy.close") == 1
    assert "runner.open" not in case.calls
    coordinator = service._coordinators[case.request.episode_id]
    calls_before_run = tuple(case.calls)

    with pytest.raises(V2EpisodeConflict) as first:
        await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"admission": "closed"},
        )

    assert first.value.failure.code == "episode_not_ready"
    assert coordinator.run_fingerprint is None
    assert coordinator.run_task is None
    assert tuple(case.calls) == calls_before_run

    with pytest.raises(V2EpisodeConflict) as retry:
        await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"admission": "closed"},
        )
    assert retry.value.failure.code == "episode_not_ready"
    assert coordinator.run_fingerprint is None
    assert coordinator.run_task is None
    assert tuple(case.calls) == calls_before_run


async def test_run_during_close_admission_rejects_pre_effect_and_exact_retry_is_not_poisoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    coordinator = service._coordinators[case.request.episode_id]
    close_release = asyncio.Event()
    close_task = asyncio.create_task(close_release.wait())
    coordinator.close_task = close_task
    calls_before_run = tuple(case.calls)

    try:
        with pytest.raises(V2EpisodeConflict) as rejected:
            await service.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"admission": "close-in-progress"},
            )
        assert rejected.value.failure.code == "episode_not_ready"
        assert coordinator.run_fingerprint is None
        assert coordinator.run_task is None
        assert tuple(case.calls) == calls_before_run
    finally:
        close_release.set()
        await close_task
        coordinator.close_task = None

    retried = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"admission": "close-in-progress"},
    )

    assert retried.disposition is V2OperationDisposition.FRESH
    assert retried.response.primary_disposition is EpisodePrimaryDisposition.SUCCEEDED
    assert case.calls.count("runner.open") == 1
    assert case.calls.count("session.run") == 1

class _CodedFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ObservedLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.attempted = asyncio.Event()
        self.second_attempted = asyncio.Event()
        self.attempt_count = 0

    async def acquire(self) -> None:
        await self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    async def __aenter__(self):
        self.attempt_count += 1
        self.attempted.set()
        if self.attempt_count == 2:
            self.second_attempted.set()
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._lock.release()


async def test_verifier_close_failure_still_releases_primary_lease_and_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.sandbox.verifier.close_error = _CodedFailure("verifier_close_failed")

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"failure": "verifier-close"},
    )

    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    assert outcome.response.completed_envelope_ref == ref("completed-envelope")
    assert outcome.response.closed_envelope_ref == ref("closed-envelope")
    assert case.calls.count("verifier.close") == 1
    assert case.calls.count("lease.close") == 1
    assert case.repository.failed_completed_inputs[-1].primary_disposition == "failed"
    assert case.repository.closed_inputs[-1].final_primary_outcome == "failed"
    assert any(
        event.primary_fact is not None
        and event.primary_fact.code == "verifier_close_failed"
        and event.primary_fact.side_effect_boundary == "verifier_cleanup"
        for event in case.repository.events
    )


async def test_close_waits_for_blocked_run_owner_before_single_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.runner.block_session_run = True
    run_task = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"race": "close-vs-run"},
        )
    )
    await case.runner.opened.wait()
    await case.runner.session.run_entered.wait()

    close_task = asyncio.create_task(service.close_episode(case.request.episode_id))
    cancel_waiter = asyncio.create_task(case.runner.session.cancel_entered.wait())
    done, _ = await asyncio.wait(
        {close_task, cancel_waiter},
        return_when=asyncio.FIRST_COMPLETED,
    )
    cancel_observed = cancel_waiter in done
    lease_closed_before_run_exit = "lease.close" in case.calls
    if not cancel_observed:
        cancel_waiter.cancel()
        await asyncio.gather(cancel_waiter, return_exceptions=True)
    case.runner.session.run_release.set()
    run_outcome, close_outcome = await asyncio.gather(
        run_task,
        close_task,
        return_exceptions=True,
    )

    assert cancel_observed
    assert case.runner.session.cancelled == ["episode close requested"]
    assert lease_closed_before_run_exit is False
    assert not isinstance(run_outcome, BaseException)
    assert not isinstance(close_outcome, BaseException)

    assert run_outcome.response.primary_disposition is EpisodePrimaryDisposition.CANCELLED
    assert close_outcome.response.cleanup_disposition is EpisodeCleanupDisposition.RELEASED
    assert case.calls.count("session.close") == 1
    assert case.calls.count("lease.close") == 1
    assert not any(
        event.from_state == "quarantined" and event.to_state == "verifying"
        for event in case.repository.events
    )


@pytest.mark.parametrize("primary", ["failed", "cancelled"])
async def test_released_primary_failure_publishes_failure_and_cleanup_proof_envelopes(
    monkeypatch: pytest.MonkeyPatch,
    primary: str,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    if primary == "failed":
        case.runner.session_run_error = _CodedFailure("runner_primary_failed")
    else:
        case.runner.block_session_run = True

    run_task = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"primary": primary},
        )
    )
    if primary == "cancelled":
        await case.runner.opened.wait()
        await case.runner.session.run_entered.wait()
        await service.cancel(case.request.episode_id, "deterministic cancellation")
        case.runner.session.run_release.set()
    first = await run_task
    calls_after_first = tuple(case.calls)
    second = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"primary": primary},
    )

    expected = (
        EpisodePrimaryDisposition.FAILED
        if primary == "failed"
        else EpisodePrimaryDisposition.CANCELLED
    )
    assert first.response.primary_disposition is expected
    assert first.response.completed_envelope_ref == ref("completed-envelope")
    assert first.response.closed_envelope_ref == ref("closed-envelope")
    assert second.disposition is V2OperationDisposition.CACHED
    assert second.response == first.response
    assert tuple(case.calls) == calls_after_first
    assert case.repository.failed_completed_inputs[-1].primary_disposition == primary
    assert case.repository.closed_inputs[-1].final_primary_outcome == primary
    assert case.calls.count("lease.close") == 1


async def test_service_shutdown_cancels_blocked_run_before_waiting_for_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.runner.block_session_run = True
    run_task = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"shutdown": "active-run"},
        )
    )
    await case.runner.opened.wait()
    await case.runner.session.run_entered.wait()

    await asyncio.wait_for(service.close(), 1)
    run_outcome = await asyncio.wait_for(run_task, 1)

    assert run_outcome.response.primary_disposition is EpisodePrimaryDisposition.CANCELLED
    assert case.runner.session.cancelled == ["service shutdown"]
    assert case.calls.count("lease.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1
    assert (
        await service.get_state(case.request.episode_id)
    ).cleanup_disposition is EpisodeCleanupDisposition.RELEASED


async def test_shutdown_classifies_typed_runner_cancellation_as_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run = DeterministicSession.run

    async def typed_cancellation(self, request):
        try:
            return await original_run(self, request)
        except asyncio.CancelledError:
            raise RunnerCancelled(
                RunnerCancellation(
                    reason="service shutdown",
                    requested=True,
                    observed_checkpoint="after_action",
                ),
                episode_id=self.request.episode_id,
                effective_plan_digest=self.request.effective_plan_digest,
            )

    monkeypatch.setattr(DeterministicSession, "run", typed_cancellation)
    service, case, _, created = await _created(monkeypatch)
    case.runner.block_session_run = True
    run_task = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"shutdown": "typed-runner-cancellation"},
        )
    )
    await case.runner.opened.wait()
    await case.runner.session.run_entered.wait()

    await service.close()
    outcome = await run_task

    assert (
        outcome.response.primary_disposition
        is EpisodePrimaryDisposition.CANCELLED
    )
    failed = case.repository.failed_completed_inputs[-1]
    assert failed.primary_disposition == "cancelled"
    assert failed.primary_failure.category == "cancellation"
    assert failed.primary_failure.code == "process_interrupted"
    assert case.runner.session.cancelled == ["service shutdown"]
    assert not any(
        event.primary_fact is not None
        and event.primary_fact.code
        in {"tool_invoke_failed", "policy_invoke_failed"}
        for event in case.repository.events
    )


async def test_concurrent_shutdown_waiters_share_owner_and_waiter_cancellation_defers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.runner.block_session_run = True
    case.sandbox.lease.close_release = asyncio.Event()
    run_task = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"shutdown": "shared-owner"},
        )
    )
    await case.runner.opened.wait()
    await case.runner.session.run_entered.wait()

    first = asyncio.create_task(service.close())
    second = asyncio.create_task(service.close())
    await case.sandbox.lease.close_entered.wait()
    first.cancel()
    await asyncio.sleep(0)

    assert first.done() is False
    assert second.done() is False
    assert case.calls.count("sandbox.manager.close") == 0

    case.sandbox.lease.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    await service.close()
    await run_task

    assert case.calls.count("lease.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1


async def test_shutdown_owner_failure_is_replayed_to_every_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    calls = 0

    async def fail_close() -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        raise RuntimeError("sandbox close failed")

    case.sandbox.close = fail_close

    with pytest.raises(BaseExceptionGroup) as first:
        await service.close()
    with pytest.raises(BaseExceptionGroup) as retry:
        await service.close()

    assert retry.value is first.value
    assert calls == 1


async def test_shutdown_fence_rejects_create_already_waiting_for_dictionary_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    observed_lock = _ObservedLock()
    service._dictionary_lock = observed_lock
    await observed_lock.acquire()
    create_task = asyncio.create_task(service.create(case.request))
    await observed_lock.attempted.wait()
    shutdown_task = asyncio.create_task(service.close())
    await observed_lock.second_attempted.wait()
    observed_lock.release()

    with pytest.raises(service_module.V2EpisodeUnavailable) as caught:
        await create_task
    await shutdown_task

    assert caught.value.failure.code == "service_closing"
    assert case.calls.count("resolve") == 0
    assert case.calls.count("sandbox.open") == 0
    assert case.calls.count("sandbox.manager.close") == 1


async def test_cancel_at_policy_resolver_barrier_prevents_allocation_and_backward_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    case.policy_resolver.entered = asyncio.Event()
    case.policy_resolver.release = asyncio.Event()
    create_task = asyncio.create_task(service.create(case.request))
    await case.policy_resolver.entered.wait()

    cancelled = await service.cancel(case.request.episode_id, "cancel before allocation")
    case.policy_resolver.release.set()
    create_result = await asyncio.gather(create_task, return_exceptions=True)

    assert cancelled.requested is True
    assert isinstance(create_result[0], service_module.V2EpisodeUnavailable)
    assert "sandbox.open" not in case.calls
    transitions = [(event.from_state, event.to_state) for event in case.repository.events]
    assert ("cancel_requested", "accepted") not in transitions
    assert [event.to_state for event in case.repository.events][:2] == [
        "accepted",
        "cancel_requested",
    ]
    assert "allocating" not in [event.to_state for event in case.repository.events]
    assert "allocation_started" not in [
        event.event_kind for event in case.repository.events
    ]
    assert case.repository.events[-1].to_state in {
        "closed",
        "quarantined",
    }
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("lease.close") == 0
    state = await service.get_state(case.request.episode_id)
    assert state.primary_disposition is EpisodePrimaryDisposition.CANCELLED
    assert state.state in {EpisodeLifecycleState.CLOSED, EpisodeLifecycleState.QUARANTINED}


async def test_cancel_at_accepted_fence_cannot_interleave_allocation_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service(monkeypatch)
    accepted = asyncio.Event()
    release = asyncio.Event()
    original_transition = service._transition

    async def block_after_accepted(coordinator, state, kind, **kwargs):
        event = await original_transition(
            coordinator,
            state,
            kind,
            **kwargs,
        )
        if state is EpisodeLifecycleState.ACCEPTED:
            accepted.set()
            await release.wait()
        return event

    monkeypatch.setattr(service, "_transition", block_after_accepted)
    create_task = asyncio.create_task(service.create(case.request))
    await accepted.wait()

    cancelled = await service.cancel(
        case.request.episode_id,
        "cancel at allocation fence",
    )
    release.set()
    create_result = await asyncio.gather(create_task, return_exceptions=True)

    assert cancelled.requested is True
    assert isinstance(create_result[0], service_module.V2EpisodeUnavailable)
    assert [event.to_state for event in case.repository.events][:2] == [
        "accepted",
        "cancel_requested",
    ]
    assert "allocating" not in [event.to_state for event in case.repository.events]
    assert "allocation_started" not in [
        event.event_kind for event in case.repository.events
    ]
    assert "sandbox.open" not in case.calls
    assert case.calls.count("policy.close") == 1
    assert case.calls.count("lease.close") == 0


@pytest.mark.parametrize(
    "resources",
    [
        ("runtime", "workspace", "cache_holder", "lease_record"),
        ("child_verifier", "runtime", "workspace", "cache_holder"),
        (
            "child_verifier",
            "runtime",
            "workspace",
            "cache_holder",
            "lease_record",
            "child_verifier",
        ),
        (
            "child_verifier",
            "runtime",
            "workspace",
            "cache_holder",
            "lease_record",
            "lease_record",
        ),
        (
            "child_verifier",
            "runtime",
            "workspace",
            "cache_holder",
            "lease_record",
            "unexpected",
        ),
    ],
)
async def test_incomplete_duplicate_or_unexpected_cleanup_never_claims_closed(
    monkeypatch: pytest.MonkeyPatch,
    resources: tuple[str, ...],
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.sandbox.lease.close_receipt = receipt_from_resources(*resources)

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"cleanup-resources": resources},
    )
    state = await service.get_state(case.request.episode_id)

    assert outcome.response.closed_envelope_ref is None
    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert state.cleanup_disposition is EpisodeCleanupDisposition.QUARANTINED
    assert case.calls.count("lease.close") == 1
    assert not case.repository.closed_inputs


@pytest.mark.parametrize("unreleased_resource", ("child_verifier", "runtime"))
async def test_unreleased_child_or_base_cleanup_never_claims_closed(
    monkeypatch: pytest.MonkeyPatch,
    unreleased_resource: str,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    resources = (
        "child_verifier",
        "runtime",
        "workspace",
        "cache_holder",
        "lease_record",
    )
    case.sandbox.lease.close_receipt = SandboxCleanupReceipt.from_steps(
        "lease-deterministic",
        tuple(
            CleanupStepReceipt(
                resource,
                CleanupState.FAILED
                if resource == unreleased_resource
                else CleanupState.RELEASED,
            )
            for resource in resources
        ),
    )

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"unreleased-resource": unreleased_resource},
    )

    assert outcome.response.closed_envelope_ref is None
    assert not case.repository.closed_inputs
    assert case.repository.quarantine_inputs


async def test_exact_five_resource_primary_cleanup_receipt_is_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    resources = (
        "child_verifier",
        "runtime",
        "workspace",
        "cache_holder",
        "lease_record",
    )
    receipt = SandboxCleanupReceipt.from_steps(
        "lease-deterministic",
        tuple(
            CleanupStepReceipt(
                resource,
                CleanupState.ALREADY_RELEASED
                if resource == "runtime"
                else CleanupState.RELEASED,
            )
            for resource in resources
        ),
    )
    case.sandbox.lease.close_receipt = receipt

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"cleanup-resources": "exact"},
    )

    assert outcome.response.closed_envelope_ref is not None
    assert case.repository.closed_inputs
    assert case.repository.closed_inputs[0].cleanup_receipt is receipt
    assert case.repository.closed_inputs[0].cleanup_required_resources == resources


async def test_incomplete_verifier_cleanup_receipt_prevents_success_and_closed_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.sandbox.verifier.close_receipt = receipt_from_resources(
        "runtime",
        "workspace",
        lease_id="verifier-lease",
    )

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"cleanup-resources": "incomplete-verifier"},
    )

    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    assert outcome.response.closed_envelope_ref is None
    assert case.calls.count("verifier.close") == 1
    assert case.calls.count("lease.close") == 1
    assert not case.repository.closed_inputs
    assert case.repository.quarantine_inputs


async def test_verifier_cleanup_receipt_cannot_replace_authoritative_verifier_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    authoritative_verifier_lease_id = case.sandbox.verifier.lease_id
    mismatched_receipt_lease_id = "verifier-receipt-impostor"
    case.sandbox.verifier.close_receipt = receipt_from_resources(
        "runtime",
        "workspace",
        "lease_record",
        lease_id=mismatched_receipt_lease_id,
    )

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"cleanup-resources": "mismatched-verifier-lease"},
    )

    assert authoritative_verifier_lease_id != mismatched_receipt_lease_id
    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    assert outcome.response.completed_envelope_ref is not None
    assert outcome.response.closed_envelope_ref is None
    assert case.calls.count("verifier.close") == 1
    assert case.calls.count("lease.close") == 1
    assert not case.repository.completed_inputs
    assert not case.repository.closed_inputs
    assert case.repository.failed_completed_inputs
    failed = case.repository.failed_completed_inputs[-1]
    assert failed.verifier_lease_id == authoritative_verifier_lease_id
    assert failed.verifier_cleanup_receipt is None
    assert case.repository.quarantine_inputs
    assert any(
        fact is not None
        and fact.side_effect_boundary == "verifier_cleanup"
        and fact.lease_id == authoritative_verifier_lease_id
        for event in case.repository.events
        for fact in (event.primary_fact, event.cleanup_fact)
    )


@pytest.mark.parametrize(
    ("event_kind", "runner_fails"),
    [("run_started", False), ("runner_terminal", False), ("run_failed", True)],
)
async def test_transition_append_failure_cannot_leak_or_reexecute_lease(
    monkeypatch: pytest.MonkeyPatch,
    event_kind: str,
    runner_fails: bool,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.repository.append_transition_error_kinds = {event_kind}
    if runner_fails:
        case.runner.session_run_error = _CodedFailure("runner_before_closing")

    first = await asyncio.gather(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"append-failure": event_kind},
        ),
        return_exceptions=True,
    )
    calls_after_first = tuple(case.calls)
    second = await asyncio.gather(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"append-failure": event_kind},
        ),
        return_exceptions=True,
    )

    assert case.calls.count("lease.close") == 1
    assert case.calls.count("session.run") <= 1
    assert tuple(case.calls) == calls_after_first
    assert type(second[0]) is type(first[0])
    assert not case.repository.closed_inputs or (
        case.repository.closed_inputs[-1].final_primary_outcome != "succeeded"
    )
    assert case.repository.quarantine_inputs


async def test_runner_and_session_close_failures_preserve_two_durable_failure_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.runner.session_run_error = _CodedFailure("runner_primary_failed")
    case.runner.session_close_error = _CodedFailure("session_close_failed")

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"dual-failure": True},
    )

    facts = {
        (fact.code, fact.side_effect_boundary)
        for event in case.repository.events
        for fact in (event.primary_fact, event.cleanup_fact)
        if fact is not None
    }
    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    assert ("runner_primary_failed", "runner") in facts
    assert ("session_close_failed", "session_close") in facts
    assert case.calls.count("lease.close") == 1
    assert case.repository.failed_completed_inputs[-1].primary_disposition == "failed"
    assert case.repository.closed_inputs[-1].final_primary_outcome == "failed"


async def test_emitted_runner_event_is_durable_before_runner_failure_and_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    event = ToolCallEvent(
        0,
        case.request.episode_id,
        case.resolved.effective_plan.canonical_digest(),
        1,
        0,
        "call-deterministic",
        "deterministic_tool",
        '{"value":1}',
    )
    case.runner.session_events = (event,)
    case.runner.session_run_error = _CodedFailure("runner_failed_after_event")

    outcome = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"event": "before-failure"},
    )
    recovered_events = case.repository.recover_runner_events(
        case.request.episode_id
    )

    assert outcome.response.primary_disposition is EpisodePrimaryDisposition.FAILED
    assert recovered_events == (event,)
    assert [item.sequence for item in recovered_events] == [0]
    assert case.calls.index("runner_event:0") < case.calls.index("session.close")
    assert case.calls.index("runner_event:0") < case.calls.index("event:run_failed")
    assert case.calls.count("lease.close") == 1
    assert case.repository.failed_completed_inputs[-1].runner_event_refs == tuple(
        case.repository.runner_event_refs[case.request.episode_id]
    )


async def test_publish_closed_failure_never_makes_closed_durable_and_retry_can_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    case.repository.publish_closed_error = _CodedFailure("closed_publication_failed")

    first = await asyncio.gather(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"publication": "closed-fails"},
        ),
        return_exceptions=True,
    )
    failed_state = await service.get_state(case.request.episode_id)

    assert not isinstance(first[0], BaseException)
    assert first[0].response.closed_envelope_ref is None
    assert failed_state.state is not EpisodeLifecycleState.CLOSED
    assert failed_state.closed_envelope_ref is None
    assert not any(event.to_state == "closed" for event in case.repository.events)
    assert case.calls.count("lease.close") == 1

    case.repository.publish_closed_error = None
    reconciled = await service.close_episode(case.request.episode_id)
    state = await service.get_state(case.request.episode_id)

    assert reconciled.response.state is EpisodeLifecycleState.QUARANTINED
    assert reconciled.response.closed_envelope_ref is None
    assert state.state is EpisodeLifecycleState.QUARANTINED
    assert case.calls.count("lease.close") == 1


async def test_publish_closed_and_durable_quarantine_dual_failure_surfaces_exception_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _, created = await _created(monkeypatch)
    publication_error = _CodedFailure("closed_publication_failed")
    quarantine_error = _CodedFailure("durable_quarantine_failed")
    case.repository.publish_closed_error = publication_error
    case.repository.quarantine_error = quarantine_error

    captured = await asyncio.gather(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"publication": "closed-and-quarantine-fail"},
        ),
        return_exceptions=True,
    )

    error = captured[0]
    assert isinstance(error, BaseExceptionGroup)
    assert error.message == "closed publication and durable quarantine failed"
    assert error.exceptions == (publication_error, quarantine_error)
    assert case.calls.count("lease.close") == 1
    assert not any(event.to_state == "closed" for event in case.repository.events)
    assert case.repository.quarantine_inputs[-1].failure.code == (
        "closed_publication_failed"
    )
