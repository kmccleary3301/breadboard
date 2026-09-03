from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from breadboard_engine.compilation.contracts import canonical_json_bytes
import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import service as service_module
from breadboard.rl.harness.evidence import (
    EpisodeEvidenceRepository,
    EvidenceRoleBindingV2,
    EvidenceRoleSourceV2,
    InMemoryEpisodeLocatorStore,
    V2EvidenceAuthority,
    canonical_digest,
)
from breadboard.rl.harness.materialization import MaterializationKey
from breadboard.rl.harness.runners.base import (
    RunnerAdapterRegistry,
    RunnerToolBinding,
    thaw_json,
)
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
    PolicyRuntimeBinding,
)
from breadboard.rl.harness.sandbox import build_sandbox_execution_plan
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    V2LifecycleDependencies,
    V2OperationDisposition,
)
from breadboard.artifacts.cas import InMemoryCAS
from tests.rl.harness.test_config_overlays import (
    _resolution_with_candidate_and_episode_overlay,
)
from tests.rl.harness.test_config_selection import _resolution_fixture
from tests.rl.harness.test_runner_policy_runtime import (
    IMPLEMENTATION_DIGEST,
    RecordingPolicyClient,
    _response,
)
from tests.rl.harness.v2_service_fixtures import (
    DeterministicPolicyResolver,
    DeterministicSandboxRuntime,
    conductor_compatible_case,
    deterministic_clock,
)
from tests.rl.harness.wp7_fixtures import make_runtime_fixture

pytestmark = pytest.mark.asyncio


def _request_for_episode(
    request: c.ResolveEpisodeRequest,
    episode_id: str,
    *,
    nonce_canary: str | None = None,
) -> c.ResolveEpisodeRequest:
    payload = request.model_dump(mode="json")
    payload["episode_id"] = episode_id
    if nonce_canary is not None:
        payload["selection_nonce"] = "sha256:" + hashlib.sha256(
            nonce_canary.encode("utf-8")
        ).hexdigest()
    return c.ResolveEpisodeRequest.model_validate(payload)


def _resolved_for_episode(
    resolved: c.ResolvedEpisodePlan,
    episode_id: str,
) -> c.ResolvedEpisodePlan:
    payload = resolved.model_dump(mode="json")
    payload["episode_id"] = episode_id
    binding_payload = payload["selection_commit"]["binding"]
    binding_payload["owner_key"] = "sha256:" + hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": "bb.rl.selection-owner.v1",
                "subject_digest": resolved.subject_digest,
                "episode_id": episode_id,
            }
        )
    ).hexdigest()
    binding = c.SelectionBinding.model_validate(binding_payload)
    binding_bytes = binding.canonical_bytes()
    payload["selection_commit"]["binding_ref"] = {
        "artifact_id": binding.canonical_digest(),
        "sha256": binding.canonical_digest(),
        "size_bytes": len(binding_bytes),
        "media_type": "application/vnd.breadboard.selection-binding+json;version=1",
    }
    return c.ResolvedEpisodePlan.model_validate(payload)


def _with_evidence_role(
    resolved: c.ResolvedEpisodePlan,
    role: str,
) -> c.ResolvedEpisodePlan:
    plan_payload = resolved.effective_plan.model_dump(mode="json")
    artifact_payload = plan_payload["artifacts"]
    artifact_payload["allowed_roles"] = [role]
    plan_payload["effective_capabilities"]["artifacts"] = artifact_payload
    capabilities = c.CapabilityVector.model_validate(
        plan_payload["effective_capabilities"]
    )
    plan_payload["effective_capabilities"] = capabilities.model_dump(mode="json")
    plan_payload["effective_capability_digest"] = capabilities.canonical_digest()
    plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
    payload = resolved.model_dump(mode="json")
    payload["effective_plan"] = plan.model_dump(mode="json")
    plan_bytes = plan.canonical_bytes()
    payload["effective_plan_ref"] = {
        "artifact_id": plan.canonical_digest(),
        "sha256": plan.canonical_digest(),
        "size_bytes": len(plan_bytes),
        "media_type": "application/vnd.breadboard.effective-execution-plan+json;version=1",
    }
    return c.ResolvedEpisodePlan.model_validate(payload)


def _bind_wp4_selection_to_conductor(
    resolved: c.ResolvedEpisodePlan,
    conductor_resolved: c.ResolvedEpisodePlan,
) -> c.ResolvedEpisodePlan:
    template = _resolved_for_episode(conductor_resolved, resolved.episode_id)
    plan_payload = template.effective_plan.model_dump(mode="json")
    wp4_plan = resolved.effective_plan
    for field in (
        "selector_digest",
        "config_set_digest",
        "admitted_set_root",
        "selection_record_digest",
        "task_eligibility_digest",
    ):
        plan_payload[field] = wp4_plan.model_dump(mode="json")[field]
    plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
    payload = template.model_dump(mode="json")
    payload["selection_record_ref"] = resolved.selection_record_ref.model_dump(
        mode="json"
    )
    binding_payload = payload["selection_commit"]["binding"]
    binding_payload["request_digest"] = (
        resolved.selection_commit.binding.request_digest
    )
    binding_payload["selection_record_digest"] = (
        resolved.selection_commit.binding.selection_record_digest
    )
    binding = c.SelectionBinding.model_validate(binding_payload)
    binding_bytes = binding.canonical_bytes()
    payload["selection_commit"] = {
        "binding": binding.model_dump(mode="json"),
        "binding_ref": {
            "artifact_id": binding.canonical_digest(),
            "sha256": binding.canonical_digest(),
            "size_bytes": len(binding_bytes),
            "media_type": "application/vnd.breadboard.selection-binding+json;version=1",
        },
        "verified_at": resolved.selection_commit.verified_at,
    }
    payload["effective_plan"] = plan.model_dump(mode="json")
    plan_bytes = plan.canonical_bytes()
    payload["effective_plan_ref"] = {
        "artifact_id": plan.canonical_digest(),
        "sha256": plan.canonical_digest(),
        "size_bytes": len(plan_bytes),
        "media_type": "application/vnd.breadboard.effective-execution-plan+json;version=1",
    }
    return _with_evidence_role(c.ResolvedEpisodePlan.model_validate(payload), "patch")


def _deterministic_evidence_authority(role: str) -> V2EvidenceAuthority:
    return V2EvidenceAuthority(
        (
            EvidenceRoleBindingV2(
                role=role,
                source=EvidenceRoleSourceV2.RUNNER_RESULT,
                producer_id="wp8-deterministic-conductor",
                producer_implementation_digest="sha256:"
                + hashlib.sha256(b"wp8-deterministic-conductor").hexdigest(),
            ),
        )
    )


def _install_exact_policy_records(
    sandbox: DeterministicSandboxRuntime,
    resolved: c.ResolvedEpisodePlan,
    *,
    required_role: str,
) -> None:
    sandbox.registries = SimpleNamespace(
        evidence_policies=(
            c.EvidencePolicyRegistryRecord(
                policy=resolved.effective_plan.evidence,
                required_roles=(required_role,),
            ),
        ),
        retention_policies=(
            c.RetentionPolicyRegistryRecord(
                grant=c.RetentionPolicyGrant(
                    policy=resolved.effective_plan.retention,
                    minimum_seconds=0,
                    maximum_seconds=2_592_000,
                )
            ),
        ),
    )


class _ExactConductorCase:
    def __init__(self, calls: list[str]) -> None:
        fixture, request, base_resolved, observation = conductor_compatible_case()
        resolved = _with_evidence_role(base_resolved, "transcript")
        self.fixture = fixture
        self.request = request
        self.resolved = resolved
        self.calls = calls
        self.policy_client = RecordingPolicyClient(
            observation,
            responses=[_response("v2")],
        )
        self.policy_resolver = DeterministicPolicyResolver(self.policy_client, calls)
        self.registry = RunnerAdapterRegistry(
            (ConductorAdapter(CONDUCTOR_RUNTIME_ABI),)
        )
        self.sandbox = DeterministicSandboxRuntime(resolved, calls)
        self.sandbox.lease.runner_workspace.tool_bindings = tuple(
            RunnerToolBinding(
                grant.tool_id,
                grant.implementation_digest,
                tuple(grant.capability_ids),
            )
            for grant in resolved.effective_plan.effective_capabilities.tools
        )
        _install_exact_policy_records(
            self.sandbox,
            resolved,
            required_role="transcript",
        )
        self.evidence_authority = _deterministic_evidence_authority("transcript")
        wp7 = make_runtime_fixture(
            episode_id=request.episode_id,
            runner_adapter_id=resolved.effective_plan.runner.adapter_id,
            runner_runtime_abi=resolved.effective_plan.runner.runtime_abi,
            runner_implementation_digest=resolved.effective_plan.runner.implementation_digest,
        )
        self.preflight_plan = build_sandbox_execution_plan(
            wp7.request,
            wp7.registries,
            wp7.authorities,
        )


async def _exact_conductor_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cas: InMemoryCAS | None = None,
    locators: InMemoryEpisodeLocatorStore | None = None,
) -> tuple[BreadBoardV2EpisodeService, _ExactConductorCase, InMemoryCAS, InMemoryEpisodeLocatorStore]:
    calls: list[str] = []
    case = _ExactConductorCase(calls)
    resolved_cas = cas or InMemoryCAS()
    resolved_locators = locators or InMemoryEpisodeLocatorStore()
    repository = EpisodeEvidenceRepository(resolved_cas, resolved_locators)

    class _ConfigRuntime:
        def resolve_episode(self, request: c.ResolveEpisodeRequest) -> c.ResolvedEpisodePlan:
            calls.append("config.resolve")
            assert request is case.request
            return case.resolved

    monkeypatch.setattr(
        service_module,
        "build_sandbox_execution_plan",
        lambda request, registries, installed_authorities: case.preflight_plan,
    )
    service = BreadBoardV2EpisodeService(
        V2LifecycleDependencies(
            config_runtime=_ConfigRuntime(),
            runner_registry=case.registry,
            sandbox_runtime=case.sandbox,
            policy_client_resolver=case.policy_resolver,
            evidence_repository=repository,
            evidence_authority=case.evidence_authority,
            clock=deterministic_clock,
        )
    )
    return service, case, resolved_cas, resolved_locators


async def test_real_wp4_cases_cross_deterministic_v2_lifecycle_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = _resolution_fixture(algorithm="direct-v1", candidate_count=1)
    weighted = _resolution_fixture(algorithm="weighted-v1", candidate_count=3)
    overlay, _, overlay_manifests = _resolution_with_candidate_and_episode_overlay()
    fixtures = (direct, weighted, overlay)
    requests = (
        _request_for_episode(direct.request, "wp8-direct-canary"),
        _request_for_episode(
            weighted.request,
            "wp8-weighted-canary",
            nonce_canary="wp8-weighted-selection-canary",
        ),
        _request_for_episode(overlay.request, "wp8-overlay-canary"),
    )
    resolved = await asyncio.gather(
        *(
            asyncio.to_thread(fixture.runtime.resolve_episode, request)
            for fixture, request in zip(fixtures, requests, strict=True)
        )
    )
    _, _, conductor_resolved, conductor_observation = conductor_compatible_case()
    lifecycle_plans = tuple(
        _bind_wp4_selection_to_conductor(plan, conductor_resolved)
        for plan in resolved
    )

    class _WP4ConductorRuntime:
        def __init__(self, fixture: Any) -> None:
            self._fixture = fixture

        def resolve_episode(
            self,
            request: c.ResolveEpisodeRequest,
        ) -> c.ResolvedEpisodePlan:
            raw = self._fixture.runtime.resolve_episode(request)
            return _bind_wp4_selection_to_conductor(raw, conductor_resolved)

    selection_records = tuple(
        c.SelectionRecord.model_validate_json(
            fixture.store.records[plan.selection_record_ref.sha256]
        )
        for fixture, plan in zip(fixtures, resolved, strict=True)
    )
    store_snapshots = tuple(
        (frozenset(fixture.store.records), frozenset(fixture.store.bindings))
        for fixture in fixtures
    )

    cases: list[Any] = []
    preflight_by_episode: dict[str, Any] = {}
    for index, (fixture, request, plan) in enumerate(
        zip(fixtures, requests, lifecycle_plans, strict=True)
    ):
        calls: list[str] = []
        sandbox = DeterministicSandboxRuntime(plan, calls)
        sandbox.lease.runner_workspace.tool_bindings = tuple(
            RunnerToolBinding(
                grant.tool_id,
                grant.implementation_digest,
                tuple(grant.capability_ids),
            )
            for grant in plan.effective_plan.effective_capabilities.tools
        )
        _install_exact_policy_records(sandbox, plan, required_role="patch")
        policy_client = RecordingPolicyClient(
            conductor_observation,
            responses=[_response(f"wp8-case-{index}")],
        )
        policy_resolver = DeterministicPolicyResolver(policy_client, calls)
        registry = RunnerAdapterRegistry(
            (
                ConductorAdapter(plan.effective_plan.runner.runtime_abi),
            )
        )
        wp7 = make_runtime_fixture(
            episode_id=request.episode_id,
            runner_adapter_id=plan.effective_plan.runner.adapter_id,
            runner_runtime_abi=plan.effective_plan.runner.runtime_abi,
            runner_implementation_digest=plan.effective_plan.runner.implementation_digest,
        )
        preflight = build_sandbox_execution_plan(
            wp7.request,
            wp7.registries,
            wp7.authorities,
        )
        preflight_by_episode[request.episode_id] = preflight
        cas = InMemoryCAS()
        repository = EpisodeEvidenceRepository(
            cas,
            InMemoryEpisodeLocatorStore(),
        )
        service = BreadBoardV2EpisodeService(
            V2LifecycleDependencies(
                config_runtime=_WP4ConductorRuntime(fixture),
                runner_registry=registry,
                sandbox_runtime=sandbox,
                policy_client_resolver=policy_resolver,
                evidence_repository=repository,
                evidence_authority=_deterministic_evidence_authority("patch"),
                clock=deterministic_clock,
            )
        )
        cases.append(
            SimpleNamespace(
                fixture=fixture,
                request=request,
                resolved=plan,
                calls=calls,
                sandbox=sandbox,
                policy_client=policy_client,
                policy_resolver=policy_resolver,
                cas=cas,
                repository=repository,
                service=service,
            )
        )

    monkeypatch.setattr(
        service_module,
        "build_sandbox_execution_plan",
        lambda request, registries, installed_authorities: preflight_by_episode[
            request.episode_id
        ],
    )
    created = await asyncio.gather(
        *(case.service.create(case.request) for case in cases)
    )
    runs = await asyncio.gather(
        *(
            case.service.run(
                case.request.episode_id,
                create_fingerprint=create.response.create_fingerprint,
                task_input={"query": f"{case.request.episode_id}-query"},
                context={"scope": "deterministic-only"},
            )
            for case, create in zip(cases, created, strict=True)
        )
    )
    closes = await asyncio.gather(
        *(case.service.close_episode(case.request.episode_id) for case in cases)
    )

    assert [record.selected_candidate_id for record in selection_records] == [
        "a",
        "b",
        "a",
    ]
    assert selection_records[1].draw is not None
    assert (
        selection_records[1].draw.draw_digest
        == "sha256:f885bb14355a5c11b3cca2fb01232754cffc7b1658a43b1aabd3efa77a6d72e2"
    )
    assert isinstance(direct.selector_ref, c.DirectSelectorRef)
    assert isinstance(weighted.selector_ref, c.WeightedSelectorRef)
    assert [
        application.overlay_digest
        for application in resolved[2].effective_plan.overlay_applications
    ] == [manifest.canonical_digest() for manifest in overlay_manifests]
    assert [
        application.parent_receipt_digest
        for application in resolved[2].effective_plan.overlay_applications
    ] == [
        resolved[2].base_receipt_digest,
        resolved[2].effective_plan.overlay_applications[0].result_receipt_digest,
    ]
    assert resolved[2].effective_plan.effective_semantics["sampling"][
        "temperature"
    ] == 0.25

    assert all(
        result.disposition is V2OperationDisposition.FRESH for result in created
    )
    assert all(result.disposition is V2OperationDisposition.FRESH for result in runs)
    assert all(
        result.disposition is V2OperationDisposition.CACHED for result in closes
    )
    assert all(
        thaw_json(result.response.response)
        == _response(f"wp8-case-{index}")
        for index, result in enumerate(runs)
    )
    for case, create, run, close, record in zip(
        cases,
        created,
        runs,
        closes,
        selection_records,
        strict=True,
    ):
        plan = case.resolved
        assert case.policy_resolver.arguments == [
            (
                case.request.policy_binding,
                case.request.episode_id,
                plan.effective_plan.canonical_digest(),
            )
        ]
        assert len(case.policy_client.requests) == 1
        conductor_request = case.policy_client.requests[0]
        assert conductor_request.episode_id == case.request.episode_id
        assert (
            conductor_request.effective_plan_digest
            == plan.effective_plan.canonical_digest()
        )
        assert (
            conductor_request.binding_digest
            == create.response.policy_binding_digest
        )
        assert (
            plan.selection_commit.binding.selection_record_digest
            == record.canonical_digest()
            == plan.selection_record_ref.sha256
        )
        assert create.response.effective_plan_digest == plan.effective_plan.canonical_digest()
        assert run.response.completed_envelope_ref is not None
        assert run.response.closed_envelope_ref is not None
        assert close.response.closed_envelope_ref == run.response.closed_envelope_ref

        recovered = case.repository.recover(case.request.episode_id)
        assert recovered is not None
        assert recovered.completed_envelope is not None
        assert recovered.closed_envelope is not None
        assert recovered.evidence_manifest is not None
        completed = recovered.completed_envelope
        closed = recovered.closed_envelope
        manifest = recovered.evidence_manifest
        assert completed.evidence_root == manifest.lineage_root == run.response.evidence_root
        assert closed.completed_envelope_ref == run.response.completed_envelope_ref
        assert close.response.closed_envelope_ref.sha256 == closed.digest
        assert completed.completed_event_head == recovered.events[-3].digest
        assert closed.reconciliation_event_head == recovered.events[-1].digest
        assert recovered.locator.latest_event_head == recovered.events[-1].digest
        assert recovered.locator.current_state == EpisodeLifecycleState.CLOSED.value
        assert [event.to_state for event in recovered.events] == [
            "accepted",
            "allocating",
            "ready",
            "running",
            "verifying",
            "completed",
            "closing",
            "closed",
        ]
        assert manifest.resolved_plan_digest == plan.canonical_digest()
        assert manifest.selection_digest == plan.selection_commit.canonical_digest()
        assert manifest.effective_plan_digest == plan.effective_plan.canonical_digest()
        assert manifest.policy_binding_digest == create.response.policy_binding_digest
        assert manifest.materialization_digest == canonical_digest(
            case.sandbox.lease._materialized.receipt
        )
        assert manifest.primary_measurement_digest == canonical_digest(
            case.sandbox.lease.measurement
        )
        assert manifest.verifier_snapshot_digest == canonical_digest(
            case.sandbox.verifier_arguments[0][1]
        )
        assert manifest.verifier_measurement_digest == canonical_digest(
            case.sandbox.verifier.measurement
        )
        assert manifest.verifier_result_digest == canonical_digest(
            {"reward_components": {"score": 1}, "result": "verified"}
        )
        artifact_payload = json.loads(
            case.cas.get_bytes(manifest.artifact_manifest_ref)
        )
        assert [item["role"] for item in artifact_payload["objects"]] == ["patch"]
        artifact_ref = artifact_payload["objects"][0]["artifact_ref"]
        artifact_bytes = case.cas.get_bytes(artifact_ref["artifact_id"])
        assert artifact_ref["sha256"] == (
            "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        )
        assert closed.cleanup_receipt_digest == canonical_digest(
            closed.cleanup_receipt
        )
        assert closed.verifier_cleanup_receipt_digest == canonical_digest(
            closed.verifier_cleanup_receipt
        )
        assert case.calls.index("verifier.close") < case.calls.index("lease.close")

    assert len({id(fixture.store) for fixture in fixtures}) == 3
    assert len({id(fixture.policy_registry) for fixture in fixtures}) == 3
    assert len({id(case.service._dependencies.runner_registry) for case in cases}) == 3
    assert all(len(fixture.store.bindings) == 1 for fixture in fixtures)
    assert tuple(
        (frozenset(fixture.store.records), frozenset(fixture.store.bindings))
        for fixture in fixtures
    ) == store_snapshots


async def test_wp8_exact_conductor_and_fake_wp7_preserve_every_digest_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, cas, _ = await _exact_conductor_service(monkeypatch)

    created = await service.create(case.request)
    run = await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"query": "wp8-protocol-canary"},
        context={"batch": "deterministic-v2"},
    )
    closed = await service.close_episode(case.request.episode_id)

    assert created.disposition is V2OperationDisposition.FRESH
    assert run.disposition is V2OperationDisposition.FRESH
    assert thaw_json(run.response.response) == _response("v2")
    assert run.response.closed_envelope_ref is not None
    assert closed.disposition is V2OperationDisposition.CACHED
    assert closed.response.closed_envelope_ref == run.response.closed_envelope_ref
    preflight = case.preflight_plan
    assert created.response.sandbox_preflight.runtime == preflight.runtime.runtime_id
    assert created.response.sandbox_preflight.runtime_class is preflight.runtime.runtime_class
    assert (
        created.response.sandbox_preflight.runtime_binary_digest
        == preflight.runtime.measured_binary_digest
    )
    assert created.response.sandbox_preflight.image_digest == preflight.image.image_digest
    assert (
        created.response.sandbox_preflight.security_policy_digest
        == preflight.security_policy.policy_digest
    )
    assert (
        created.response.sandbox_preflight.network_policy_digest
        == preflight.network_policy.policy_digest
    )
    assert (
        created.response.sandbox_preflight.verifier_digest
        == preflight.verifier.grant.implementation_digest
    )
    assert (
        created.response.sandbox_preflight.materialization_plan_digest
        == MaterializationKey.from_plan(preflight.materialization_plan).digest
    )
    assert case.policy_resolver.arguments == [
        (
            case.request.policy_binding,
            case.request.episode_id,
            case.resolved.effective_plan.canonical_digest(),
        )
    ]
    assert len(case.policy_client.requests) == 1
    policy_request = case.policy_client.requests[0]
    assert policy_request.episode_id == case.request.episode_id
    assert policy_request.effective_plan_digest == case.resolved.effective_plan.canonical_digest()
    assert policy_request.binding_digest == created.response.policy_binding_digest

    recovered = service._dependencies.evidence_repository.recover(case.request.episode_id)
    assert recovered is not None
    assert recovered.completed_envelope is not None
    assert recovered.closed_envelope is not None
    envelope = recovered.completed_envelope
    manifest_payload = json.loads(cas.get_bytes(envelope.evidence_manifest_ref))
    assert manifest_payload["resolved_plan_digest"] == case.resolved.canonical_digest()
    assert (
        manifest_payload["selection_digest"]
        == case.resolved.selection_commit.canonical_digest()
    )
    assert manifest_payload["effective_plan_digest"] == created.response.effective_plan_digest
    assert manifest_payload["policy_binding_digest"] == created.response.policy_binding_digest
    assert manifest_payload["materialization_digest"] == canonical_digest(
        case.sandbox.lease._materialized.receipt
    )
    assert manifest_payload["primary_measurement_digest"] == canonical_digest(
        case.sandbox.lease.measurement
    )
    assert manifest_payload["verifier_snapshot_digest"] == canonical_digest(
        case.sandbox.verifier_arguments[0][1]
    )
    assert manifest_payload["verifier_measurement_digest"] == canonical_digest(
        case.sandbox.verifier.measurement
    )
    assert manifest_payload["verifier_result_digest"] == canonical_digest(
        {"reward_components": {"score": 1}, "result": "verified"}
    )
    ledger_payload = json.loads(cas.get_bytes(manifest_payload["runner_ledger_ref"]["artifact_id"]))
    assert ledger_payload["event_count"] == 5
    assert [event["sequence"] for event in ledger_payload["events"]] == list(range(5))
    assert "request_payload" in recovered.runner_events[0]
    assert "trainable_values" in recovered.runner_events[1]
    assert "policy_capability_observation_digest" in recovered.runner_events[1]
    assert "response_digest" in recovered.runner_events[2]
    assert "normalized_output" in recovered.runner_events[3]
    assert recovered.runner_events[4]["reason"] == "assistant_complete"
    assert ledger_payload["events"][1]["binding_digest"] == created.response.policy_binding_digest
    assert ledger_payload["events"][2]["binding_digest"] == created.response.policy_binding_digest
    assert recovered.closed_envelope.cleanup_receipt_digest == canonical_digest(
        recovered.closed_envelope.cleanup_receipt
    )
    assert recovered.closed_envelope.verifier_cleanup_receipt_digest == canonical_digest(
        recovered.closed_envelope.verifier_cleanup_receipt
    )
    assert [event.to_state for event in recovered.events] == [
        "accepted",
        "allocating",
        "ready",
        "running",
        "verifying",
        "completed",
        "closing",
        "closed",
    ]
    assert recovered.locator.current_state == EpisodeLifecycleState.CLOSED.value
    assert case.policy_client.close_calls == 1
    assert case.calls.index("verifier.close") < case.calls.index("lease.close")


async def test_restart_cached_retry_has_zero_execution_effects_but_new_id_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, case, cas, locators = await _exact_conductor_service(monkeypatch)
    created = await first.create(case.request)
    original = await first.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"query": "restart-canary"},
        context={"batch": "restart"},
    )
    await first.close_episode(case.request.episode_id)
    original_recovered = first._dependencies.evidence_repository.recover(
        case.request.episode_id
    )
    assert original_recovered is not None
    assert original_recovered.completed_envelope is not None
    original_body = cas.get_bytes(
        original_recovered.completed_envelope.run_response_ref
    )

    restarted, restarted_case, _, _ = await _exact_conductor_service(
        monkeypatch,
        cas=cas,
        locators=locators,
    )
    restarted_case.sandbox.reconcile_receipts = ()
    await restarted.start()
    restart_effect_boundary = len(restarted_case.calls)
    cached_create = await restarted.create(case.request)
    cached_run = await restarted.run(
        case.request.episode_id,
        create_fingerprint=cached_create.response.create_fingerprint,
        task_input={"query": "restart-canary"},
        context={"batch": "restart"},
    )

    assert cached_create.disposition is V2OperationDisposition.CACHED
    assert cached_run.disposition is V2OperationDisposition.CACHED
    assert canonical_json_bytes(thaw_json(cached_run.response.response)) == canonical_json_bytes(
        thaw_json(original.response.response)
    )
    cached_recovered = restarted._dependencies.evidence_repository.recover(
        case.request.episode_id
    )
    assert cached_recovered is not None
    assert cached_recovered.completed_envelope is not None
    assert cas.get_bytes(cached_recovered.completed_envelope.run_response_ref) == original_body
    assert restarted_case.calls[restart_effect_boundary:] == []
    assert restarted_case.policy_client.observe_calls == 0
    assert restarted_case.policy_client.requests == []

    fresh_service, fresh_case, _, _ = await _exact_conductor_service(monkeypatch)
    fresh_request = _request_for_episode(fresh_case.request, "wp8-fresh-new-id-canary")
    fresh_case.request = fresh_request
    fresh_case.resolved = _resolved_for_episode(
        fresh_case.resolved,
        fresh_request.episode_id,
    )
    fresh_case.sandbox = DeterministicSandboxRuntime(fresh_case.resolved, fresh_case.calls)
    fresh_case.sandbox.lease.runner_workspace.tool_bindings = tuple(
        RunnerToolBinding(
            grant.tool_id,
            grant.implementation_digest,
            tuple(grant.capability_ids),
        )
        for grant in fresh_case.resolved.effective_plan.effective_capabilities.tools
    )
    _install_exact_policy_records(
        fresh_case.sandbox,
        fresh_case.resolved,
        required_role="transcript",
    )
    fresh_service._dependencies = replace(
        fresh_service._dependencies,
        sandbox_runtime=fresh_case.sandbox,
    )
    fresh = await fresh_service.create(fresh_request)
    fresh_run = await fresh_service.run(
        fresh_request.episode_id,
        create_fingerprint=fresh.response.create_fingerprint,
        task_input={"query": "fresh-side-effect-canary"},
        context={"batch": "fresh"},
    )
    fresh_close = await fresh_service.close_episode(fresh_request.episode_id)

    assert fresh.disposition is V2OperationDisposition.FRESH
    assert fresh_run.disposition is V2OperationDisposition.FRESH
    assert fresh_run.response.closed_envelope_ref is not None
    assert (
        fresh_close.response.closed_envelope_ref
        == fresh_run.response.closed_envelope_ref
    )
    assert fresh_case.calls.count("config.resolve") == 1
    assert fresh_case.calls.count("sandbox.open") == 1
    assert fresh_case.policy_client.observe_calls == 1
    assert len(fresh_case.policy_client.requests) == 1
    assert fresh_case.calls.count("verifier.execute") == 1
    assert fresh_case.calls.count("lease.close") == 1
    fresh_state = await fresh_service.get_state(fresh_request.episode_id)
    assert fresh_state.state is EpisodeLifecycleState.CLOSED
    assert fresh_state.cleanup_disposition is EpisodeCleanupDisposition.RELEASED
