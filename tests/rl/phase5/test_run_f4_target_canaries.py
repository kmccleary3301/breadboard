from __future__ import annotations

import copy
import hashlib
import hmac
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any

import pytest

from breadboard_engine.compilation.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from breadboard.rl.harness.composition import (
    _ProductionCleanupProbe,
    _process_starttime,
)
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.service import (
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2CloseResult,
    V2CreateResult,
    V2OperationDisposition,
    V2OperationResult,
    V2RunResult,
    V2SandboxPreflightIdentity,
)
from breadboard.rl.phase5.f4_campaign import (
    CampaignInvariantIdentity,
    CompilerVisibleSemanticDelta,
    ImmutableRef,
)
from scripts.rl_phase5.run_f4_target_canaries import (
    F4CleanupObservation,
    F4CampaignConversionAuthority,
    F4ProductionBinding,
    F4ProductionLoaderReceipt,
    F4SecurityPolicyObservation,
    F4TargetCanaryError,
    F4TargetCanaryInput,
    F4TargetCanaryRunResult,
    F4TargetIdentity,
    F4TargetExecutionAuthority,
    F4TargetExecutorReceipt,
    F4VariantExecution,
    VARIANT_IDS,
    build_campaign_target_report,
    run_f4_target_canaries,
    _CAMPAIGN_AUTHORITY_MEDIA_TYPE,
    _run_f4_target_canaries_for_test,
    _component_report_line,
    _wire,
    _require_clean,
    _ProductionRuntime,
)
from tests.rl.harness.test_config_overlays import _admit_overlay_layer, _overlay_runtime
from tests.rl.harness.test_config_selection import ResolutionFixture, _resolution_fixture


def _d(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _json_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _ref(label: str, digest: str | None = None) -> ImmutableRef:
    value = digest or _d(label)
    return ImmutableRef(reference=f"cas://f4-target/{label}@{value}", digest=value)


def _artifact_ref(raw: bytes, media_type: str) -> c.ArtifactRef:
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return c.ArtifactRef(
        artifact_id=digest,
        sha256=digest,
        size_bytes=len(raw),
        media_type=media_type,
    )


@dataclass(frozen=True)
class _Case:
    variant: F4VariantExecution
    create: V2CreateResult
    selection_raw: bytes
    plan_raw: bytes
    semantic_raw: bytes
    selector_raw: bytes


def _case(variant_id: str, index: int) -> _Case:
    seed = _resolution_fixture(
        algorithm="weighted-v1",
        candidate_count=2,
        candidate_names=(variant_id, f"z-paired-arm-{index}", f"zz-unused-{index}"),
    )
    runtime, _compiler = _overlay_runtime(seed)
    base = copy.deepcopy(seed.admission.compiler.effective_semantics)
    after = copy.deepcopy(base)
    after["sampling"]["temperature"] = (index + 1) / 10
    overlay_ref, _overlay_manifest = _admit_overlay_layer(
        seed,
        runtime,
        parent_receipt=seed.base_receipt_ref,
        before=base,
        after=after,
        source_kind="optimizer",
        source_digest=_d(f"overlay-source-{variant_id}"),
        operation=c.OverlayOperation(
            op="replace",
            path="/sampling/temperature",
            value=after["sampling"]["temperature"],
        ),
    )
    receipt_digests = tuple(
        sorted(
            {
                *seed.admitted_set.receipt_digests,
                overlay_ref.result_receipt_digest,
            }
        )
    )
    receipts = tuple(
        c.AdmissionReceipt.model_validate_json(seed.store.records[digest])
        for digest in receipt_digests
    )
    validity = c.ValidityWindow(
        issued_at=max(receipt.validity.issued_at for receipt in receipts),
        not_before=max(receipt.validity.not_before for receipt in receipts),
        expires_at=min(receipt.validity.expires_at for receipt in receipts),
    )
    admitted_payload = seed.admitted_set.model_dump(mode="json")
    admitted_payload["receipt_digests"] = list(receipt_digests)
    admitted_payload["validity"] = validity.to_canonical_obj()
    admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
    admitted_ref = seed.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    assert isinstance(seed.selector, c.ConfigSetManifest)
    selector_payload = seed.selector.model_dump(mode="json")
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    selector_payload["validity"] = validity.to_canonical_obj()
    selector_payload["candidates"][0]["candidate"]["overlays"] = [
        overlay_ref.to_canonical_obj()
    ]
    selector_payload["candidates"][0]["weight"] = 2**53 - 2
    selector_payload["candidates"][1]["weight"] = 1
    selector = c.ConfigSetManifest.model_validate(selector_payload)
    selector_raw = selector.canonical_bytes()
    selector_artifact = seed.store.publish(
        kind=c.ArtifactKind.CONFIG_SET,
        canonical_bytes=selector_raw,
    )
    selector_ref = c.WeightedSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    )
    request_payload = seed.request.model_dump(mode="json")
    request_payload["episode_id"] = f"f4-target-{index}-{variant_id}"
    request_payload["selector"] = selector_ref.to_canonical_obj()
    request = c.ResolveEpisodeRequest.model_validate(request_payload)
    fixture = ResolutionFixture(
        runtime=runtime,
        request=request,
        admission=seed.admission,
        base_receipt_ref=seed.base_receipt_ref,
        admitted_set=admitted_set,
        selector=selector,
        selector_ref=selector_ref,
        policy_observation=seed.policy_observation,
        policy_registry=seed.policy_registry,
        store=seed.store,
        effects=seed.effects,
    )
    resolved = fixture.runtime.resolve_episode(request)
    selection_raw = fixture.store.records[resolved.selection_record_ref.sha256]
    selection = c.SelectionRecord.model_validate_json(selection_raw, strict=True)
    if selection.selected_candidate_id != variant_id:
        raise AssertionError("frozen fixture weights did not select designated arm")
    selection_ref = _artifact_ref(
        selection_raw,
        "application/vnd.breadboard.selection-record+json;version=1",
    )
    plan_payload = resolved.effective_plan.model_dump(mode="json")
    plan_payload["base_compiled"]["manifest_digest"] = _d(f"manifest-{variant_id}")
    plan_payload["base_compiled"]["bundle_digest"] = _d(f"bundle-{variant_id}")
    plan_payload["base_compiled"]["closure_digest"] = _d(f"closure-{variant_id}")
    plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
    plan_raw = plan.canonical_bytes()
    semantic_raw = canonical_json_bytes(base)
    plan_ref = _artifact_ref(
        plan_raw,
        "application/vnd.breadboard.effective-execution-plan+json;version=1",
    )
    binding_payload = resolved.selection_commit.binding.model_dump(mode="json")
    binding_payload["selection_record_digest"] = selection_ref.sha256
    binding = c.SelectionBinding.model_validate(binding_payload)
    binding_raw = binding.canonical_bytes()
    binding_ref = _artifact_ref(
        binding_raw,
        "application/vnd.breadboard.selection-binding+json;version=1",
    )
    commit = c.SelectionCommitToken(
        binding=binding,
        binding_ref=binding_ref,
        verified_at=resolved.selection_commit.verified_at,
    )
    create = V2CreateResult(
        episode_id=request.episode_id,
        create_fingerprint=_d(f"create-{variant_id}"),
        state=EpisodeLifecycleState.READY,
        effective_plan_digest=plan_ref.sha256,
        selection_record_ref=selection_ref,
        effective_plan_ref=plan_ref,
        policy_binding_digest=request.policy_binding.canonical_digest(),
        selection_commit=commit,
        base_receipt_digest=plan.base_receipt_digest,
        final_receipt_digest=plan.final_receipt_digest,
        policy_observation_digest=plan.policy_capability_observation_digest,
        sandbox_preflight=V2SandboxPreflightIdentity(
            runtime=plan.sandbox.runtime_id,
            runtime_class=plan.sandbox.runtime_class,
            runtime_binary_digest=plan.sandbox.runtime_binary_digest,
            image_digest=plan.sandbox.image_digest,
            security_policy_digest=plan.sandbox.security_policy_digest,
            network_policy_digest=plan.sandbox.network_policy_digest,
            verifier_digest=plan.verifier.implementation_digest,
            materialization_plan_digest=_d(f"materialization-{variant_id}"),
        ),
    )
    application = plan.overlay_applications[0]
    variant = F4VariantExecution(
        variant_id=variant_id,
        request=request,
        config_bundle_ref=_ref(
            f"bundle-{variant_id}", plan.base_compiled.bundle_digest
        ),
        dependency_closure_ref=_ref(
            f"closure-{variant_id}", plan.base_compiled.closure_digest
        ),
        compiler_identity_ref=_ref(
            "compiler-identity", plan.base_compiled.compiler.canonical_digest()
        ),
        compiled_config_ref=_ref(
            f"compiled-{variant_id}", plan.base_compiled.manifest_digest
        ),
        compiled_semantics_ref=_artifact_ref(
            semantic_raw,
            "application/vnd.breadboard.compiled-config-semantic+json;version=1",
        ),
        admission_receipt_ref=_ref(
            f"receipt-{variant_id}", plan.base_receipt_digest
        ),
        selection_record_ref=_ref(f"selection-{variant_id}", selection_ref.sha256),
        ordered_overlay_receipt_refs=(
            _ref(
                f"overlay-receipt-{variant_id}",
                application.result_receipt_digest,
            ),
        ),
        semantic_delta=CompilerVisibleSemanticDelta(
            name=f"temperature-{index}",
            compiler_field_pointer="/sampling/temperature",
            before_digest="sha256:"
            + hashlib.sha256(
                canonical_json_bytes(base["sampling"]["temperature"])
            ).hexdigest(),
            after_digest="sha256:"
            + hashlib.sha256(
                canonical_json_bytes(after["sampling"]["temperature"])
            ).hexdigest(),
        ),
        requested_security_policy_digest=plan.sandbox.security_policy_digest,
    )
    return _Case(
        variant=variant,
        create=create,
        selection_raw=selection_raw,
        plan_raw=plan_raw,
        semantic_raw=semantic_raw,
        selector_raw=selector_raw,
    )


def _evidence_fixture(
    case: _Case,
    target: F4TargetIdentity,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    episode_id = case.variant.request.episode_id
    plan = c.EffectiveExecutionPlan.model_validate_json(case.plan_raw, strict=True)
    evidence: dict[str, bytes] = {}

    def store(value: dict[str, Any], media_type: str) -> c.ArtifactRef:
        raw = canonical_json_bytes(value)
        ref = _artifact_ref(raw, media_type)
        evidence[ref.sha256] = raw
        return ref

    create_response_ref = store(
        _wire(case.create),
        "application/vnd.breadboard.phase5-f4-create-response+json;version=1",
    )
    primary_ref = store(
        {
            "schema_version": "bb.rl.test-primary-measurement.v1",
            "episode_id": episode_id,
        },
        "application/vnd.breadboard.test-primary-measurement+json;version=1",
    )
    verifier_measurement_ref = store(
        {
            "schema_version": "bb.rl.test-verifier-measurement.v1",
            "episode_id": episode_id,
        },
        "application/vnd.breadboard.test-verifier-measurement+json;version=1",
    )
    verifier_output_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-verifier-output.v1",
            "episode_id": episode_id,
            "passed": True,
            "reward": 1,
        },
        "application/vnd.breadboard.phase5-f4-verifier-output+json;version=1",
    )
    verifier_result_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-verifier-result-receipt.v1",
            "episode_id": episode_id,
            "effective_plan_digest": plan.canonical_digest(),
            "verifier_implementation_digest": (
                plan.verifier.implementation_digest
            ),
            "verifier_measurement_digest": verifier_measurement_ref.sha256,
            "output_digest": verifier_output_ref.sha256,
            "passed": True,
            "reward": 1,
            "reward_components": {"tests": 1},
        },
        "application/vnd.breadboard.phase5-f4-verifier-result-receipt+json;version=1",
    )
    tool = plan.effective_capabilities.tools[0]
    tool_output_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-tool-output.v1",
            "episode_id": episode_id,
            "exit_code": 0,
        },
        "application/vnd.breadboard.phase5-f4-tool-output+json;version=1",
    )
    call_id = f"call-{episode_id}"
    policy_observation_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-policy-observation.v1",
            "episode_id": episode_id,
            "effective_plan_digest": plan.canonical_digest(),
            "policy_observation_digest": (
                plan.policy_capability_observation_digest
            ),
            "call_id": call_id,
        },
        "application/vnd.breadboard.phase5-f4-policy-observation+json;version=1",
    )
    policy_call_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-policy-call-receipt.v1",
            "episode_id": episode_id,
            "effective_plan_digest": plan.canonical_digest(),
            "policy_observation_digest": (
                plan.policy_capability_observation_digest
            ),
            "call_id": call_id,
            "tool_id": tool.tool_id,
            "implementation_digest": tool.implementation_digest,
            "decision": "allowed",
        },
        "application/vnd.breadboard.phase5-f4-policy-call-receipt+json;version=1",
    )
    tool_call_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-tool-call-receipt.v1",
            "episode_id": episode_id,
            "effective_plan_digest": plan.canonical_digest(),
            "policy_observation_digest": (
                plan.policy_capability_observation_digest
            ),
            "policy_call_digest": policy_call_ref.sha256,
            "call_id": call_id,
            "tool_id": tool.tool_id,
            "implementation_digest": tool.implementation_digest,
            "exit_code": 0,
            "output_digest": tool_output_ref.sha256,
        },
        "application/vnd.breadboard.phase5-f4-tool-call-receipt+json;version=1",
    )
    runner_ledger_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-runner-ledger.v1",
            "episode_id": episode_id,
            "events": (
                {
                    "event_index": 0,
                    "episode_id": episode_id,
                    "effective_plan_digest": plan.canonical_digest(),
                    "call_id": call_id,
                    "policy_call_digest": policy_call_ref.sha256,
                    "policy_call_receipt_ref": policy_call_ref.model_dump(
                        mode="json"
                    ),
                    "policy_observation_digest": (
                        plan.policy_capability_observation_digest
                    ),
                    "policy_observation_ref": policy_observation_ref.model_dump(
                        mode="json"
                    ),
                    "tool_id": tool.tool_id,
                    "implementation_digest": tool.implementation_digest,
                    "exit_code": 0,
                    "output_digest": tool_output_ref.sha256,
                },
            ),
        },
        "application/vnd.breadboard.phase5-f4-runner-ledger+json;version=1",
    )
    object_values = tuple(
        {
            "schema_version": "bb.rl.evidence-object.v2",
            "role": role,
            "producer": "source-backed-test-runtime",
            "artifact_ref": ref.model_dump(mode="json"),
            "authorization_policy_ref": "policy://f4-test",
            "retention_policy_ref": "retention://f4-test",
            "parent_digests": (),
        }
        for role, ref in (
            ("primary-measurement", primary_ref),
            ("verifier-measurement", verifier_measurement_ref),
            ("verifier-result", verifier_result_ref),
            ("tool-call-receipt", tool_call_ref),
            ("verifier-output", verifier_output_ref),
            ("tool-output", tool_output_ref),
        )
    )
    artifact_manifest_ref = store(
        {
            "schema_version": "bb.rl.artifact-manifest.v2",
            "objects": object_values,
            "allowed_roles": (
                "primary-measurement",
                "verifier-measurement",
                "verifier-result",
                "tool-call-receipt",
                "verifier-output",
                "tool-output",
            ),
            "max_each_bytes": 1_000_000,
            "max_total_bytes": 3_000_000,
            "required_roles": (
                "primary-measurement",
                "verifier-measurement",
                "verifier-result",
                "tool-call-receipt",
                "verifier-output",
                "tool-output",
            ),
            "total_byte_count": sum(
                ref.size_bytes
                for ref in (
                    primary_ref,
                    verifier_measurement_ref,
                    verifier_result_ref,
                    tool_call_ref,
                    verifier_output_ref,
                    tool_output_ref,
                )
            ),
        },
        "application/vnd.breadboard.artifact-manifest+json;version=1",
    )
    evidence_manifest_ref = store(
        {
            "schema_version": "bb.rl.execution-evidence-manifest.v2",
            "episode_id": episode_id,
            "resolved_plan_digest": plan.canonical_digest(),
            "selection_digest": case.create.selection_record_ref.sha256,
            "effective_plan_digest": plan.canonical_digest(),
            "policy_binding_digest": case.variant.request.policy_binding.canonical_digest(),
            "runner_ledger_ref": runner_ledger_ref.model_dump(mode="json"),
            "materialization_digest": case.create.sandbox_preflight.materialization_plan_digest,
            "primary_measurement_digest": primary_ref.sha256,
            "verifier_snapshot_digest": _d(f"verifier-snapshot-{episode_id}"),
            "task_input_digest": _json_digest(
                {"prompt": "repair the same repository"}
            ),
            "run_context_digest": _json_digest(
                {"campaign": "f4-target-canary"}
            ),
            "target_identity": target.model_dump(mode="json"),
            "verifier_measurement_digest": verifier_measurement_ref.sha256,
            "verifier_result_digest": verifier_result_ref.sha256,
            "artifact_manifest_ref": artifact_manifest_ref.model_dump(mode="json"),
            "primary_disposition": "succeeded",
            "reward_disposition": "succeeded",
            "reward_components": {"tests": 1},
            "evidence_policy_ref": "policy://f4-test",
            "retention_policy_ref": "retention://f4-test",
            "lineage_nodes": (),
            "lineage_root": _d(f"lineage-{episode_id}"),
            "verifier_cleanup_receipt_ref": None,
            "verifier_cleanup_lease_id": None,
            "retention_policy_record_ref": None,
            "primary_failure_digest": None,
            "authority_access_ledger_ref": None,
            "authority_canary_reads": (),
            "authority_cross_episode_reads": (),
        },
        "application/vnd.breadboard.execution-evidence-manifest+json;version=1",
    )
    run_execution_ref = store(
        {
            "schema_version": "bb.rl.phase5-f4-run-execution-receipt.v1",
            "episode_id": episode_id,
            "create_fingerprint": case.create.create_fingerprint,
            "run_fingerprint": _d(f"run-{episode_id}"),
            "primary_disposition": "succeeded",
            "response": {"submitted": True},
            "termination": "submitted",
            "turn_count": 1,
            "reward": 1,
            "reward_components": {"tests": 1},
            "primary_measurement_digest": primary_ref.sha256,
            "verifier_result_digest": verifier_result_ref.sha256,
            "verifier_measurement_digest": verifier_measurement_ref.sha256,
        },
        "application/vnd.breadboard.phase5-f4-run-execution-receipt+json;version=1",
    )
    completed_ref = store(
        {
            "schema_version": "bb.rl.completed-episode-envelope.v2",
            "episode_id": episode_id,
            "create_fingerprint": case.create.create_fingerprint,
            "run_fingerprint": _d(f"run-{episode_id}"),
            "create_response_ref": create_response_ref.model_dump(mode="json"),
            "run_response_ref": run_execution_ref.model_dump(mode="json"),
            "evidence_manifest_ref": evidence_manifest_ref.model_dump(mode="json"),
            "evidence_root": _d(f"evidence-root-{episode_id}"),
            "primary_outcome": "succeeded",
            "completed_event_ref": primary_ref.model_dump(mode="json"),
            "completed_event_head": _d(f"completed-event-{episode_id}"),
            "subject_digest": case.variant.request.subject.canonical_digest(),
            "cleanup_disposition": "pending",
        },
        "application/vnd.breadboard.completed-episode-envelope+json;version=1",
    )
    closed_ref = store(
        {
            "schema_version": "bb.rl.closed-episode-envelope.v2",
            "episode_id": episode_id,
            "completed_envelope_ref": completed_ref.model_dump(mode="json"),
            "cleanup_receipt_digest": _json_digest({"released": True}),
            "cleanup_receipt": {"released": True},
            "reconciliation_event_ref": primary_ref.model_dump(mode="json"),
            "reconciliation_event_head": _d(f"reconciliation-{episode_id}"),
            "primary_outcome": "succeeded",
            "cleanup_required_resources": ("sandbox",),
            "verifier_cleanup_receipt_digest": None,
            "verifier_cleanup_receipt": None,
            "verifier_cleanup_required_resources": (),
            "export_authorization_refs": (),
            "redaction_decision_refs": (),
            "cleanup_disposition": "released",
        },
        "application/vnd.breadboard.closed-episode-envelope+json;version=1",
    )
    return evidence, {
        "completed_ref": completed_ref,
        "closed_ref": closed_ref,
        "primary_digest": primary_ref.sha256,
        "verifier_measurement_digest": verifier_measurement_ref.sha256,
        "verifier_result_digest": verifier_result_ref.sha256,
    }


class _RecordingService:
    def __init__(
        self,
        cases: tuple[_Case, ...],
        lifecycle: dict[str, dict[str, Any]],
    ) -> None:
        self.cases = {case.variant.request.episode_id: case for case in cases}
        self.lifecycle = lifecycle
        self.events: list[tuple[str, str]] = []
        self.create_counts: dict[str, int] = {}
        self.reward: int = 1
        self.cleanup_disposition = EpisodeCleanupDisposition.RELEASED
        self.create_disposition = V2OperationDisposition.FRESH

    async def start(self) -> None:
        self.events.append(("start", "service"))

    async def create(
        self, request: c.ResolveEpisodeRequest
    ) -> V2OperationResult[V2CreateResult]:
        self.events.append(("create", request.episode_id))
        self.create_counts[request.episode_id] = (
            self.create_counts.get(request.episode_id, 0) + 1
        )
        return V2OperationResult(
            response=self.cases[request.episode_id].create,
            disposition=self.create_disposition,
        )

    async def run(
        self,
        episode_id: str,
        *,
        create_fingerprint: str,
        task_input: dict[str, Any],
        context: dict[str, Any],
    ) -> V2OperationResult[V2RunResult]:
        self.events.append(("run", episode_id))
        assert create_fingerprint == self.cases[episode_id].create.create_fingerprint
        assert task_input == {"prompt": "repair the same repository"}
        assert context == {"campaign": "f4-target-canary"}
        lifecycle = self.lifecycle[episode_id]
        return V2OperationResult(
            response=V2RunResult(
                episode_id=episode_id,
                create_fingerprint=create_fingerprint,
                run_fingerprint=_d(f"run-{episode_id}"),
                primary_disposition=EpisodePrimaryDisposition.SUCCEEDED,
                response=MappingProxyType({"submitted": True}),
                termination="submitted",
                turn_count=1,
                completed_envelope_ref=lifecycle["completed_ref"],
                closed_envelope_ref=None,
                reward=self.reward,
                reward_components=MappingProxyType({"tests": self.reward}),
                primary_measurement_digest=lifecycle["primary_digest"],
                verifier_result_digest=lifecycle["verifier_result_digest"],
                verifier_measurement_digest=lifecycle["verifier_measurement_digest"],
            ),
            disposition=V2OperationDisposition.FRESH,
        )

    async def close_episode(self, episode_id: str) -> V2OperationResult[V2CloseResult]:
        self.events.append(("close", episode_id))
        lifecycle = self.lifecycle[episode_id]
        return V2OperationResult(
            response=V2CloseResult(
                episode_id=episode_id,
                state=EpisodeLifecycleState.CLOSED,
                cleanup_disposition=self.cleanup_disposition,
                closed_envelope_ref=lifecycle["closed_ref"],
            ),
            disposition=V2OperationDisposition.FRESH,
        )


class _Runtime:
    def __init__(self, cases: tuple[_Case, ...], target: F4TargetIdentity) -> None:
        self.evidence: dict[str, bytes] = {}
        lifecycle: dict[str, dict[str, Any]] = {}
        for case in cases:
            case_evidence, case_lifecycle = _evidence_fixture(case, target)
            self.evidence.update(case_evidence)
            lifecycle[case.variant.request.episode_id] = case_lifecycle
        self.service = _RecordingService(cases, lifecycle)
        self.composition_descriptor_digest = _d("composition-descriptor")
        self.composition_manifest_digest = _d("composition-manifest")
        self.authority_bundle_digest = _d("authority-bundle")
        self.target_identity = target
        self.events = self.service.events
        self.artifacts: dict[tuple[str, c.ArtifactKind], bytes] = {}
        for case in cases:
            self.artifacts[
                (
                    case.create.selection_record_ref.sha256,
                    c.ArtifactKind.SELECTION_RECORD,
                )
            ] = case.selection_raw
            self.artifacts[
                (
                    case.create.effective_plan_ref.sha256,
                    c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
                )
            ] = case.plan_raw
            self.artifacts[
                (
                    case.variant.compiled_semantics_ref.sha256,
                    c.ArtifactKind.COMPILED_MANIFEST,
                )
            ] = case.semantic_raw
            self.artifacts[
                (
                    case.variant.request.selector.ref.sha256,
                    c.ArtifactKind.CONFIG_SET,
                )
            ] = case.selector_raw
        cleanup_inventory = {
            "active_lease_ids": (),
            "orphan_resource_ids": (),
            "leaked_artifact_ids": (),
            "cleanup_errors": (),
            "container_ids": (),
            "process_ids": (),
            "cgroup_paths": (),
            "mount_paths": (),
            "workspace_paths": (),
            "artifact_paths": (),
            "secret_lease_ids": (),
            "broker_descriptor_count": 0,
            "broker_close_receipt_ref": _ref("broker-close-receipt").model_dump(
                mode="json"
            ),
        }
        self.cleanup = F4CleanupObservation(
            **cleanup_inventory,
            inventory_digest="sha256:"
            + hashlib.sha256(
                canonical_json_bytes(cleanup_inventory)
            ).hexdigest(),
        )
        self.closed = False

    def load_artifact(self, ref: c.ArtifactRef, kind: c.ArtifactKind) -> bytes:
        self.events.append((f"load-{kind.value}", ref.sha256))
        return self.artifacts[(ref.sha256, kind)]

    def load_evidence(self, ref: c.ArtifactRef) -> bytes:
        self.events.append(("load-evidence", ref.sha256))
        return self.evidence[ref.sha256]

    def security_policy_observation(
        self, episode_id: str
    ) -> F4SecurityPolicyObservation:
        case = self.service.cases[episode_id]
        plan = c.EffectiveExecutionPlan.model_validate_json(case.plan_raw, strict=True)
        return F4SecurityPolicyObservation(
            digest=plan.sandbox.security_policy_digest,
            receipt_ref=_ref(f"security-policy-observation-{episode_id}"),
        )

    def cleanup_observation(self) -> F4CleanupObservation:
        self.events.append(("inspect-cleanup", "runtime"))
        return self.cleanup

    async def close(self) -> None:
        self.closed = True
        self.events.append(("close-runtime", "runtime"))


def _campaign(
    tmp_path: Path,
) -> tuple[F4TargetCanaryInput, _Runtime, tuple[_Case, ...]]:
    cases = tuple(
        _case(variant_id, index) for index, variant_id in enumerate(VARIANT_IDS)
    )
    first_plan = c.EffectiveExecutionPlan.model_validate_json(
        cases[0].plan_raw, strict=True
    )
    invariant = CampaignInvariantIdentity(
        task_id="R-SWE-001",
        task_row_ref=_ref("task-row", first_plan.task.task_binding_digest),
        task_contract_digest=first_plan.task.task_contract_digest,
        repository_snapshot_ref=_ref(
            "repository", first_plan.task.repository_snapshot_digest
        ),
        model_ref=_ref("model", first_plan.policy_slots[0].model_digest),
        checkpoint_ref=_ref("checkpoint", first_plan.policy_slots[0].checkpoint_digest),
        task_image_ref=_ref("task-image", first_plan.sandbox.image_digest),
        verifier_image_ref=_ref("verifier-image", first_plan.verifier.image_digest),
        verifier_ref=_ref("verifier", first_plan.verifier.implementation_digest),
    )
    target = F4TargetIdentity(
        target_run_id="f4-target-run-001",
        target_job_id="slurm-job-001",
        target_node_id="target-node-001",
    )
    spec = F4TargetCanaryInput(
        schema_version="bb.rl.phase5-f4-target-canary-input.v1",
        production=F4ProductionBinding(
            composition_ref_path=str((tmp_path / "composition-ref.json").resolve()),
            composition_descriptor_ref=_ref(
                "composition-descriptor", _d("composition-descriptor")
            ),
            composition_manifest_ref=_ref(
                "composition-manifest", _d("composition-manifest")
            ),
            authority_bundle_ref=_ref("authority-bundle", _d("authority-bundle")),
            secret_files={"api": str((tmp_path / "api.secret").resolve())},
        ),
        target=target,
        execution_authority=F4TargetExecutionAuthority(
            environment_id="l6-env-0",
            environment_ref=_ref("environment"),
            source_runtime_ref=_ref("source-runtime"),
            composition_ref=_ref("composition"),
            runtime_class="docker",
            python_executable="/usr/bin/python3",
            docker_socket_path="/var/run/docker.sock",
            workspace_root=str(tmp_path.resolve()),
            docker_image="f4-target-image@sha256:" + "1" * 64,
            service_factory="production-composition",
        ),
        invariant_identity=invariant,
        variants=tuple(case.variant for case in cases),
        task_input={"prompt": "repair the same repository"},
        run_context={"campaign": "f4-target-canary"},
        output_dir=str((tmp_path / "report").resolve()),
    )
    return spec, _Runtime(cases, target), cases


def _run(spec: F4TargetCanaryInput, runtime: _Runtime) -> Any:
    return _run_f4_target_canaries_for_test(
        spec, input_digest=_d("canonical-input"), runtime=runtime
    ).report
class _TestReceiptAuthenticator:
    algorithm = "hmac-sha256-v1"

    def __init__(
        self,
        *,
        key: bytes = b"composition-owned-secret",
        key_id: str = "composition-receipt-key",
    ) -> None:
        self.__key = key
        self.key_id = key_id

    def sign(self, raw: bytes) -> bytes:
        return hmac.new(self.__key, raw, hashlib.sha256).digest()

    def verify(self, raw: bytes, signature: bytes) -> bool:
        return hmac.compare_digest(self.sign(raw), signature)


class _TestEvidenceCas:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.closed = False

    def put_bytes(
        self,
        raw: bytes,
        *,
        artifact_id: str,
        media_type: str,
        metadata: dict[str, str],
    ) -> c.ArtifactRef:
        if self.closed:
            raise RuntimeError("evidence CAS is closed")
        del artifact_id, metadata
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        self.objects[digest] = raw
        return c.ArtifactRef(
            artifact_id=digest,
            sha256=digest,
            size_bytes=len(raw),
            media_type=media_type,
        )

    def close(self) -> None:
        if self.closed:
            raise RuntimeError("evidence CAS closed more than once")
        self.closed = True


class _TestEvidenceRepository:
    def __init__(self) -> None:
        self._cas = _TestEvidenceCas()

    def _read_ref_exact(self, ref: c.ArtifactRef) -> bytes:
        if self._cas.closed:
            raise RuntimeError("evidence CAS is closed")
        raw = self._cas.objects[ref.sha256]
        if len(raw) != ref.size_bytes or _d_bytes(raw) != ref.sha256:
            raise ValueError("test evidence ref mismatch")
        return raw


def _d_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class _AuthorityComposition:
    def __init__(
        self,
        authenticator: _TestReceiptAuthenticator,
        repository: _TestEvidenceRepository,
        production: F4ProductionBinding,
        *,
        service: Any | None = None,
        runtime_close: Any | None = None,
    ) -> None:
        self.authority_graph = SimpleNamespace(authenticator=authenticator)
        self.manifest = SimpleNamespace(
            input_manifest_digest=production.composition_manifest_ref.digest,
            authority_bundle_digest=production.authority_bundle_ref.digest,
        )
        self._repository = repository
        self._runtime_close = runtime_close
        self.runtime_close_count = 0
        self.authority_close_count = 0
        self._runtime_closed = False
        self._closed = False
        if service is None:
            service = SimpleNamespace(
                _dependencies=SimpleNamespace(evidence_repository=repository)
            )
        else:
            service._dependencies = SimpleNamespace(
                evidence_repository=repository
            )
        self.app = SimpleNamespace(
            state=SimpleNamespace(episode_service=service)
        )

    async def close_runtime(self) -> None:
        if self._runtime_closed:
            return
        self._runtime_closed = True
        self.runtime_close_count += 1
        if self._runtime_close is not None:
            await self._runtime_close()

    async def close(self) -> None:
        if self._closed:
            return
        await self.close_runtime()
        self._closed = True
        self.authority_close_count += 1
        self._repository._cas.close()


def _authenticated_result(
    tmp_path: Path,
) -> tuple[
    F4TargetCanaryRunResult,
    _ProductionRuntime,
    _TestEvidenceRepository,
]:
    spec, runtime, _cases = _campaign(tmp_path)
    executed = _run_f4_target_canaries_for_test(
        spec, input_digest=_d("canonical-input"), runtime=runtime
    )
    loader = F4ProductionLoaderReceipt(
        schema_version="bb.rl.phase5-f4-production-loader-receipt.v1",
        input_digest=_d("canonical-input"),
        production=spec.production,
        target=spec.target,
    )
    executor = F4TargetExecutorReceipt(
        schema_version="bb.rl.phase5-f4-target-executor-receipt.v1",
        loader_receipt_digest=_d_bytes(
            canonical_json_bytes(loader.model_dump(mode="json"))
        ),
        report_digest=_d_bytes(
            canonical_json_bytes(executed.report.model_dump(mode="json"))
        ),
        target=spec.target,
    )
    repository = _TestEvidenceRepository()
    authority_runtime = _ProductionRuntime(
        composition=_AuthorityComposition(
            _TestReceiptAuthenticator(), repository, spec.production
        ),
        production=spec.production,
        target_identity=spec.target,
        lease_root=str(tmp_path),
    )
    authority_ref, authority = authority_runtime.publish_campaign_authority(
        loader, executor, executed.report
    )
    return (
        F4TargetCanaryRunResult(
            report=executed.report,
            report_path=executed.report_path,
            production_loader_receipt=loader,
            target_executor_receipt=executor,
            campaign_authority_ref=authority_ref,
            campaign_authority=authority,
        ),
        authority_runtime,
        repository,
    )




class _PublicProductionRuntime(_ProductionRuntime):
    def __init__(
        self,
        *,
        spec: F4TargetCanaryInput,
        source: _Runtime,
        repository: _TestEvidenceRepository,
    ) -> None:
        composition = _AuthorityComposition(
            _TestReceiptAuthenticator(),
            repository,
            spec.production,
            service=source.service,
            runtime_close=source.close,
        )
        super().__init__(
            composition=composition,
            production=spec.production,
            target_identity=spec.target,
            lease_root=spec.execution_authority.workspace_root,
        )
        self._source = source

    def load_artifact(
        self, ref: c.ArtifactRef, kind: c.ArtifactKind
    ) -> bytes:
        return self._source.load_artifact(ref, kind)

    def load_evidence(self, ref: c.ArtifactRef) -> bytes:
        repository = self.service._dependencies.evidence_repository
        if ref.sha256 in repository._cas.objects:
            return repository._read_ref_exact(ref)
        return self._source.load_evidence(ref)

    def cleanup_observation(self) -> F4CleanupObservation:
        return self._source.cleanup_observation()


def test_public_runner_persists_authority_before_single_cas_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, source, _cases = _campaign(tmp_path)
    repository = _TestEvidenceRepository()
    runtime = _PublicProductionRuntime(
        spec=spec, source=source, repository=repository
    )
    monkeypatch.setattr(
        "scripts.rl_phase5.run_f4_target_canaries._load_production_runtime",
        lambda _spec: runtime,
    )

    result = run_f4_target_canaries(
        spec, input_digest=_d("canonical-input")
    )

    composition = runtime._composition
    assert result.campaign_authority_ref.sha256 in repository._cas.objects
    assert repository._cas.closed is True
    assert composition.runtime_close_count == 1
    assert composition.authority_close_count == 1
    assert source.closed is True


def test_public_runner_closes_authority_once_when_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, source, _cases = _campaign(tmp_path)
    repository = _TestEvidenceRepository()
    runtime = _PublicProductionRuntime(
        spec=spec, source=source, repository=repository
    )
    monkeypatch.setattr(
        "scripts.rl_phase5.run_f4_target_canaries._load_production_runtime",
        lambda _spec: runtime,
    )

    def fail_publication(*_args: Any) -> Any:
        raise RuntimeError("authority publication failed")

    monkeypatch.setattr(
        runtime, "publish_campaign_authority", fail_publication
    )
    with pytest.raises(RuntimeError, match="authority publication failed"):
        run_f4_target_canaries(
            spec, input_digest=_d("canonical-input")
        )

    composition = runtime._composition
    assert repository._cas.closed is True
    assert composition.runtime_close_count == 1
    assert composition.authority_close_count == 1
    assert source.closed is True


def test_six_exact_variants_use_one_real_service_shaped_seam_and_emit_canonical_report(
    tmp_path: Path,
) -> None:
    spec, runtime, _cases = _campaign(tmp_path)

    report = _run(spec, runtime)

    assert tuple(row.variant_id for row in report.variants) == VARIANT_IDS
    assert all(
        row.verifier == row.lifecycle.verifier_result.artifact.model_dump(mode="json")
        and row.lifecycle.verifier_result.artifact.passed is True
        and row.lifecycle.verifier_result.artifact.reward == 1
        and row.lifecycle.verifier_result.artifact.reward_components == {"tests": 1}
        for row in report.variants
    )
    assert report.promotion_authority is False
    assert report.scorecard_authority is False
    assert runtime.closed is True
    assert runtime.service.create_counts == {
        variant.request.episode_id: 1 for variant in spec.variants
    }
    report_raw = (Path(spec.output_dir) / "f4-target-canaries.report.json").read_bytes()
    assert report_raw == canonical_json_bytes(report.model_dump(mode="json"))
    component_line = _component_report_line(
        report, str(Path(spec.output_dir) / "f4-target-canaries.report.json")
    )
    prefix = b"PHASE3_COMPONENT_REPORT_JSON="
    assert component_line.startswith(prefix)
    assert component_line.endswith(b"\n")
    envelope_raw = component_line[len(prefix) : -1]
    envelope = canonical_json_loads(envelope_raw)
    assert canonical_json_bytes(envelope) == envelope_raw
    assert envelope == {
        "component": "rl_phase5_f4_target_canaries",
        "passed": True,
        "permanent_non_authority": True,
        "promotion_authority": False,
        "report_id": "f4-target-canaries",
        "report_path": str(
            (Path(spec.output_dir) / "f4-target-canaries.report.json").resolve()
        ),
        "report_sha256": "sha256:" + hashlib.sha256(report_raw).hexdigest(),
        "schema_version": "bb.rl.phase5-f4-target-component-report.v1",
        "scorecard_authority": False,
        "scorecard_update_allowed": False,
        "summary": {
            "cleanup_complete": True,
            "exact_non_config_invariants": True,
            "fresh_selection_receipts": True,
            "no_orphan_resources": True,
            "unexpected_outcomes": [],
            "variant_count": 6,
            "variant_order": list(VARIANT_IDS),
        },
    }


def test_unknown_name_is_generic_and_selection_is_loaded_once_before_run(
    tmp_path: Path,
) -> None:
    spec, runtime, _cases = _campaign(tmp_path)

    report = _run(spec, runtime)

    per_episode: dict[str, list[str]] = {
        variant.request.episode_id: [] for variant in spec.variants
    }
    ref_to_episode = {
        variant.selection_record_ref.digest: variant.request.episode_id
        for variant in spec.variants
    }
    ref_to_episode.update(
        {row.effective_plan_ref["sha256"]: row.episode_id for row in report.variants}
    )
    for event, identity in runtime.events:
        episode_id = (
            identity if identity in per_episode else ref_to_episode.get(identity)
        )
        if episode_id is not None:
            per_episode[episode_id].append(event)
    expected = [
        "create",
        f"load-{c.ArtifactKind.SELECTION_RECORD.value}",
        f"load-{c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN.value}",
        "run",
        "close",
    ]
    assert all(events == expected for events in per_episode.values())
    unknown_episode = spec.variants[-1].request.episode_id
    assert (
        per_episode[unknown_episode] == per_episode[spec.variants[0].request.episode_id]
    )
    assert report.variants[-1].selection["redrawn"] is False
    assert report.variants[-1].selection["persisted_before_run"] is True


def test_invariant_drift_rejects_before_target_run(tmp_path: Path) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    payload = spec.model_dump(mode="json")
    payload["invariant_identity"]["model_ref"] = _ref("drifted-model").model_dump(
        mode="json"
    )
    drifted = F4TargetCanaryInput.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )

    with pytest.raises(F4TargetCanaryError, match="invariant drift"):
        _run(drifted, runtime)

    assert not any(event == "run" for event, _identity in runtime.events)
    assert runtime.closed is True


def test_mismatched_compiled_ref_and_cached_selection_fail_closed(
    tmp_path: Path,
) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    payload = spec.model_dump(mode="json")
    payload["variants"][0]["compiled_config_ref"] = _ref(
        "substituted-compiled"
    ).model_dump(mode="json")
    mismatched = F4TargetCanaryInput.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )

    with pytest.raises(F4TargetCanaryError, match="compiled config"):
        _run(mismatched, runtime)

    spec, runtime, _cases = _campaign(tmp_path / "cached")
    runtime.service.create_disposition = V2OperationDisposition.CACHED
    with pytest.raises(F4TargetCanaryError, match="fresh committed selection"):
        _run(spec, runtime)


def test_missing_compiled_receipt_rejects_closed(tmp_path: Path) -> None:
    spec, runtime, cases = _campaign(tmp_path)
    missing = cases[0].create.effective_plan_ref
    runtime.artifacts.pop((missing.sha256, c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN))

    with pytest.raises(
        F4TargetCanaryError, match="missing compiled effective-plan receipt"
    ):
        _run(spec, runtime)

    assert ("close", cases[0].variant.request.episode_id) in runtime.events
    assert runtime.closed is True


def test_verifier_failure_rejects_without_writing_report(tmp_path: Path) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    runtime.service.reward = 0

    with pytest.raises(
        F4TargetCanaryError,
        match="run result does not match canonical server verifier receipt",
    ):
        _run(spec, runtime)

    assert not (Path(spec.output_dir) / "f4-target-canaries.report.json").exists()
    assert runtime.closed is True


def test_cleanup_leak_rejects_without_authority_report(tmp_path: Path) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    runtime.cleanup = runtime.cleanup.model_copy(
        update={"active_lease_ids": ("lease-left-behind",)}
    )

    with pytest.raises(F4TargetCanaryError, match="live resource"):
        _run(spec, runtime)

    assert not (Path(spec.output_dir) / "f4-target-canaries.report.json").exists()
    assert runtime.closed is True


def test_public_result_returns_exact_persisted_report_path(tmp_path: Path) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    result = _run_f4_target_canaries_for_test(
        spec, input_digest=_d("canonical-input"), runtime=runtime
    )
    expected = str((Path(spec.output_dir) / "f4-target-canaries.report.json").resolve())
    assert result.report_path == expected
    assert Path(result.report_path).read_bytes() == canonical_json_bytes(
        result.report.model_dump(mode="json")
    )
    with pytest.raises(TypeError, match="exact F4TargetCanaryRunResult"):
        build_campaign_target_report(
            result,  # type: ignore[arg-type]
            trusted_production=spec.production,
            trusted_target=spec.target,
        )


def test_campaign_conversion_reopens_composition_signed_repository_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, authority_runtime, _repository = _authenticated_result(tmp_path)
    monkeypatch.setattr(
        "scripts.rl_phase5.run_f4_target_canaries._load_production_runtime_binding",
        lambda _production, _target: authority_runtime,
    )

    binding = build_campaign_target_report(
        result,
        trusted_production=result.production_loader_receipt.production,
        trusted_target=result.report.target,
    )

    assert binding.artifact.report_id == result.report.target.target_run_id
    assert len(binding.artifact.executions) == len(VARIANT_IDS)


def test_campaign_conversion_rejects_forged_private_rewrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, authority_runtime, repository = _authenticated_result(tmp_path)
    forged_value = result.campaign_authority.model_dump(mode="json")
    forged_value["signature"] = "0" * 64
    forged = F4CampaignConversionAuthority.model_validate(
        forged_value, strict=True
    )
    forged_raw = canonical_json_bytes(forged.model_dump(mode="json"))
    forged_ref = repository._cas.put_bytes(
        forged_raw,
        artifact_id="unused",
        media_type=_CAMPAIGN_AUTHORITY_MEDIA_TYPE,
        metadata={},
    )
    forged_result = F4TargetCanaryRunResult(
        report=result.report,
        report_path=result.report_path,
        production_loader_receipt=result.production_loader_receipt,
        target_executor_receipt=result.target_executor_receipt,
        campaign_authority_ref=forged_ref,
        campaign_authority=forged,
    )
    monkeypatch.setattr(
        "scripts.rl_phase5.run_f4_target_canaries._load_production_runtime_binding",
        lambda _production, _target: authority_runtime,
    )

    with pytest.raises(F4TargetCanaryError, match="signature is invalid"):
        build_campaign_target_report(
            forged_result,
            trusted_production=result.production_loader_receipt.production,
            trusted_target=result.report.target,
        )


def test_campaign_conversion_rejects_missing_repository_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, authority_runtime, repository = _authenticated_result(tmp_path)
    del repository._cas.objects[result.campaign_authority_ref.sha256]
    monkeypatch.setattr(
        "scripts.rl_phase5.run_f4_target_canaries._load_production_runtime_binding",
        lambda _production, _target: authority_runtime,
    )

    with pytest.raises(F4TargetCanaryError, match="cannot be reopened"):
        build_campaign_target_report(
            result,
            trusted_production=result.production_loader_receipt.production,
            trusted_target=result.report.target,
        )


def test_campaign_conversion_rejects_stale_target_authority(
    tmp_path: Path,
) -> None:
    result, authority_runtime, repository = _authenticated_result(tmp_path)
    stale_value = result.campaign_authority.model_dump(mode="json")
    stale_value["target"] = {
        **stale_value["target"],
        "target_job_id": "stale-job",
    }
    unsigned = F4CampaignConversionAuthority.unsigned_canonical_bytes_from_wire(
        stale_value
    )
    authenticator = authority_runtime._composition.authority_graph.authenticator
    stale_value["signed_payload_digest"] = _d_bytes(unsigned)
    stale_value["signature"] = authenticator.sign(unsigned).hex()
    stale = F4CampaignConversionAuthority.model_validate(
        stale_value, strict=True
    )
    stale_raw = canonical_json_bytes(stale.model_dump(mode="json"))
    stale_ref = repository._cas.put_bytes(
        stale_raw,
        artifact_id="unused",
        media_type=_CAMPAIGN_AUTHORITY_MEDIA_TYPE,
        metadata={},
    )

    with pytest.raises(
        ValueError, match="campaign conversion authority receipt mismatch"
    ):
        F4TargetCanaryRunResult(
            report=result.report,
            report_path=result.report_path,
            production_loader_receipt=result.production_loader_receipt,
            target_executor_receipt=result.target_executor_receipt,
            campaign_authority_ref=stale_ref,
            campaign_authority=stale,
        )


def test_public_result_rejects_loader_input_digest_mismatch(
    tmp_path: Path,
) -> None:
    result, authority_runtime, _repository = _authenticated_result(tmp_path)
    loader = F4ProductionLoaderReceipt(
        schema_version="bb.rl.phase5-f4-production-loader-receipt.v1",
        input_digest=_d("different-input"),
        production=result.production_loader_receipt.production,
        target=result.report.target,
    )
    executor = F4TargetExecutorReceipt(
        schema_version="bb.rl.phase5-f4-target-executor-receipt.v1",
        loader_receipt_digest=_d_bytes(
            canonical_json_bytes(loader.model_dump(mode="json"))
        ),
        report_digest=_d_bytes(
            canonical_json_bytes(result.report.model_dump(mode="json"))
        ),
        target=result.report.target,
    )
    authority_ref, authority = authority_runtime.publish_campaign_authority(
        loader, executor, result.report
    )

    with pytest.raises(
        ValueError, match="production loader/target executor receipt mismatch"
    ):
        F4TargetCanaryRunResult(
            report=result.report,
            report_path=result.report_path,
            production_loader_receipt=loader,
            target_executor_receipt=executor,
            campaign_authority_ref=authority_ref,
            campaign_authority=authority,
        )


def test_public_result_rejects_loader_production_ref_mismatch(
    tmp_path: Path,
) -> None:
    result, authority_runtime, _repository = _authenticated_result(tmp_path)
    original = result.production_loader_receipt.production
    mismatched_production = F4ProductionBinding(
        composition_ref_path=original.composition_ref_path,
        composition_descriptor_ref=_ref("different-composition"),
        composition_manifest_ref=original.composition_manifest_ref,
        authority_bundle_ref=original.authority_bundle_ref,
        secret_files=original.secret_files,
    )
    loader = F4ProductionLoaderReceipt(
        schema_version="bb.rl.phase5-f4-production-loader-receipt.v1",
        input_digest=result.report.input_digest,
        production=mismatched_production,
        target=result.report.target,
    )
    executor = F4TargetExecutorReceipt(
        schema_version="bb.rl.phase5-f4-target-executor-receipt.v1",
        loader_receipt_digest=_d_bytes(
            canonical_json_bytes(loader.model_dump(mode="json"))
        ),
        report_digest=_d_bytes(
            canonical_json_bytes(result.report.model_dump(mode="json"))
        ),
        target=result.report.target,
    )
    authority_ref, authority = authority_runtime.publish_campaign_authority(
        loader, executor, result.report
    )

    with pytest.raises(
        ValueError, match="production loader/target executor receipt mismatch"
    ):
        F4TargetCanaryRunResult(
            report=result.report,
            report_path=result.report_path,
            production_loader_receipt=loader,
            target_executor_receipt=executor,
            campaign_authority_ref=authority_ref,
            campaign_authority=authority,
        )


def test_campaign_conversion_rejects_untrusted_production_anchor(
    tmp_path: Path,
) -> None:
    result, _authority_runtime, _repository = _authenticated_result(tmp_path)
    original = result.production_loader_receipt.production
    untrusted = F4ProductionBinding(
        composition_ref_path=original.composition_ref_path,
        composition_descriptor_ref=_ref("untrusted-composition"),
        composition_manifest_ref=original.composition_manifest_ref,
        authority_bundle_ref=original.authority_bundle_ref,
        secret_files=original.secret_files,
    )

    with pytest.raises(
        F4TargetCanaryError, match="independently trusted authority"
    ):
        build_campaign_target_report(
            result,
            trusted_production=untrusted,
            trusted_target=result.report.target,
        )


def test_campaign_conversion_rejects_valid_alternate_composition_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, trusted_runtime, trusted_repository = _authenticated_result(
        tmp_path
    )
    alternate_repository = _TestEvidenceRepository()
    production = result.production_loader_receipt.production
    alternate_runtime = _ProductionRuntime(
        composition=_AuthorityComposition(
            _TestReceiptAuthenticator(
                key=b"alternate-composition-secret",
                key_id="alternate-composition-key",
            ),
            alternate_repository,
            production,
        ),
        production=production,
        target_identity=result.report.target,
        lease_root=str(tmp_path),
    )
    alternate_ref, alternate_authority = (
        alternate_runtime.publish_campaign_authority(
            result.production_loader_receipt,
            result.target_executor_receipt,
            result.report,
        )
    )
    assert (
        alternate_runtime.verify_campaign_authority(
            alternate_ref, alternate_authority
        )
        == alternate_authority
    )
    alternate_raw = alternate_repository._cas.objects[alternate_ref.sha256]
    trusted_repository._cas.objects[alternate_ref.sha256] = alternate_raw
    alternate_result = F4TargetCanaryRunResult(
        report=result.report,
        report_path=result.report_path,
        production_loader_receipt=result.production_loader_receipt,
        target_executor_receipt=result.target_executor_receipt,
        campaign_authority_ref=alternate_ref,
        campaign_authority=alternate_authority,
    )
    monkeypatch.setattr(
        "scripts.rl_phase5.run_f4_target_canaries._load_production_runtime_binding",
        lambda _production, _target: trusted_runtime,
    )

    with pytest.raises(F4TargetCanaryError, match="signature is invalid"):
        build_campaign_target_report(
            alternate_result,
            trusted_production=production,
            trusted_target=result.report.target,
        )


@pytest.mark.parametrize(
    ("family", "inventory_field"),
    [
        ("lease", "active_lease_ids"),
        ("container", "container_ids"),
        ("process", "process_ids"),
        ("cgroup", "cgroup_paths"),
        ("mount", "mount_paths"),
        ("workspace", "workspace_paths"),
        ("artifact", "artifact_paths"),
        ("secret", "secret_lease_ids"),
        ("descriptor", "broker_descriptor_count"),
    ],
)
def test_production_cleanup_probe_detects_each_surviving_resource_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    inventory_field: str,
) -> None:
    lease_root = tmp_path / "leases"
    workspace_root = tmp_path / "workspaces"
    container_root = tmp_path / "containers"
    for root in (lease_root, workspace_root, container_root):
        root.mkdir()
    sandbox_runtime = SimpleNamespace(_leases={}, _snapshots={})
    probe = object.__new__(_ProductionCleanupProbe)
    probe._materialization = SimpleNamespace(_active_workspaces={})
    probe._sandbox_runtime = sandbox_runtime
    probe._broker = None
    probe._lease_root = lease_root
    probe._workspace_root = workspace_root
    probe._secret_fds = {}
    probe._descriptors = {}
    probe._process_identities = {}
    probe._container_root = container_root
    probe._broker_paths = ()
    probe._episode_lease_ids = set()
    probe._episode_workspace_paths = set()
    probe._episode_artifacts = {}
    probe._episode_container_ids = set()
    probe._episode_cgroup_paths = set()
    probe._episode_mount_roots = set()
    opened: list[int] = []
    if family == "lease":
        (lease_root / "lease-live.json").write_bytes(b"{}")
    elif family == "container":
        (container_root / ("a" * 64)).mkdir()
    elif family == "process":
        starttime = _process_starttime(os.getpid())
        if starttime is None:
            starttime = "portable-live-start"
            monkeypatch.setattr(
                "breadboard.rl.harness.composition._process_starttime",
                lambda pid: starttime if pid == os.getpid() else None,
            )
        probe._process_identities[os.getpid()] = starttime
    elif family == "cgroup":
        survivor = tmp_path / "cgroup-live"
        survivor.mkdir()
        probe._episode_cgroup_paths.add(survivor)
    elif family == "mount":
        survivor = tmp_path / "mount-live"
        survivor.mkdir()
        probe._episode_mount_roots.add(survivor)
        monkeypatch.setattr(
            probe, "_mountinfo_paths", lambda _pid=None: (survivor,)
        )
    elif family == "workspace":
        (workspace_root / "workspace-live").mkdir()
    elif family == "artifact":
        survivor = tmp_path / "snapshot-live.json"
        survivor.write_bytes(b"{}")
        probe._episode_artifacts["snapshot-live"] = survivor
    elif family in {"secret", "descriptor"}:
        survivor = tmp_path / f"{family}.fd"
        survivor.write_bytes(b"secret")
        descriptor = os.open(survivor, os.O_RDONLY)
        opened.append(descriptor)
        identity = probe._fd_identity(descriptor)
        assert identity is not None
        if family == "secret":
            probe._secret_fds["secret-live"] = identity
        else:
            probe._descriptors["broker"] = identity
    else:
        raise AssertionError(f"unknown resource family {family}")
    try:
        inventory = probe.observe()
        observed = getattr(inventory, inventory_field)
        assert observed if inventory_field != "broker_descriptor_count" else observed == 1
        broker_ref = _ref("broker-close")
        observation = F4CleanupObservation(
            **asdict(inventory),
            inventory_digest=inventory.canonical_digest(
                broker_ref.model_dump(mode="json")
            ),
            broker_close_receipt_ref=broker_ref,
        )
        with pytest.raises(F4TargetCanaryError, match="live resource"):
            _require_clean(observation)
    finally:
        for descriptor in opened:
            os.close(descriptor)


def test_campaign_binding_rejects_target_without_tool_call_receipt(
    tmp_path: Path,
) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    result = _run_f4_target_canaries_for_test(
        spec, input_digest=_d("canonical-input"), runtime=runtime
    )
    rows = list(result.report.variants)
    first = rows[0]
    lifecycle = first.lifecycle.model_copy(
        update={"tool_call_receipts": ()}
    )
    rows[0] = first.model_copy(update={"lifecycle": lifecycle})
    bad_report = result.report.model_copy(update={"variants": tuple(rows)})
    bad_result = result.model_copy(update={"report": bad_report})
    Path(result.report_path).chmod(0o600)
    Path(result.report_path).write_bytes(
        canonical_json_bytes(bad_report.model_dump(mode="json"))
    )
    with pytest.raises(TypeError, match="exact F4TargetCanaryRunResult"):
        build_campaign_target_report(
            bad_result,  # type: ignore[arg-type]
            trusted_production=spec.production,
            trusted_target=spec.target,
        )


def test_measured_security_policy_mismatch_rejects_before_run(tmp_path: Path) -> None:
    spec, runtime, cases = _campaign(tmp_path)
    first = cases[0]
    drifted_preflight = replace(
        first.create.sandbox_preflight,
        security_policy_digest=_d("substituted-policy"),
    )
    drifted_create = replace(
        first.create,
        sandbox_preflight=drifted_preflight,
    )
    runtime.service.cases[first.variant.request.episode_id] = replace(
        first,
        create=drifted_create,
    )
    with pytest.raises(F4TargetCanaryError, match="create preflight"):
        _run(spec, runtime)
    assert all(event != "run" for event, _identity in runtime.events)


def test_tampered_raw_lifecycle_evidence_rejects(tmp_path: Path) -> None:
    spec, runtime, cases = _campaign(tmp_path)
    episode_id = cases[0].variant.request.episode_id
    completed_ref = runtime.service.lifecycle[episode_id]["completed_ref"]
    runtime.evidence[completed_ref.sha256] = b"{}"
    with pytest.raises(F4TargetCanaryError, match="evidence bytes"):
        _run(spec, runtime)
    assert not (Path(spec.output_dir) / "f4-target-canaries.report.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("container_ids", ("container-left-behind",)),
        ("process_ids", (43210,)),
        ("cgroup_paths", ("/sys/fs/cgroup/f4-left-behind",)),
        ("broker_descriptor_count", 1),
        ("broker_close_receipt_ref", None),
    ],
)
def test_exhaustive_cleanup_inventory_rejects_each_live_resource(
    tmp_path: Path, field: str, value: object
) -> None:
    spec, runtime, _cases = _campaign(tmp_path)
    runtime.cleanup = runtime.cleanup.model_copy(update={field: value})
    with pytest.raises(F4TargetCanaryError, match="live resource"):
        _run(spec, runtime)


def test_primary_and_cleanup_failures_are_both_preserved(tmp_path: Path) -> None:
    spec, runtime, _cases = _campaign(tmp_path)

    async def primary_failure(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("primary-execution-failure")

    async def cleanup_failure(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("cleanup-close-failure")

    runtime.service.run = primary_failure
    runtime.service.close_episode = cleanup_failure
    with pytest.raises(
        F4TargetCanaryError,
        match="primary-execution-failure.*cleanup-close-failure",
    ):
        _run(spec, runtime)
