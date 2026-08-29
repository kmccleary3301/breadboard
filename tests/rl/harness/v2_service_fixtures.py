from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.evidence import (
    EvidenceCorruptError,
    EvidenceValidationError,
    V2EvidenceAuthority,
    canonical_digest,
)
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    SandboxCleanupReceipt,
    VerifierSnapshotReceipt,
)
from breadboard.rl.harness.sandbox import RuntimeClass
from breadboard.rl.harness.runners.conductor import CONDUCTOR_IMPLEMENTATION_DIGEST
from breadboard.rl.harness.runners.base import (
    RunnerCloseResult,
    RunnerResult,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerTurn,
)
from breadboard.rl.state.state_ref import ArtifactRef
from tests.rl.harness.test_config_selection import _resolution_fixture
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan


def cancellation_fingerprint(
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


def ref(label: str) -> ArtifactRef:
    digest = "sha256:" + hashlib.sha256(label.encode()).hexdigest()
    return ArtifactRef(label, digest, 0, "application/json")


def released_receipt(lease_id: str = "lease-deterministic") -> SandboxCleanupReceipt:
    return SandboxCleanupReceipt.from_steps(
        lease_id,
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


def failed_receipt(lease_id: str = "lease-deterministic") -> SandboxCleanupReceipt:
    return SandboxCleanupReceipt.from_steps(
        lease_id,
        (
            CleanupStepReceipt("runtime", CleanupState.RELEASED),
            CleanupStepReceipt(
                "workspace", CleanupState.FAILED, "deterministic failure"
            ),
            CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
            CleanupStepReceipt("lease_record", CleanupState.RELEASED),
        ),
    )


def quarantined_receipt(
    lease_id: str = "lease-deterministic",
) -> SandboxCleanupReceipt:
    return SandboxCleanupReceipt.from_steps(
        lease_id,
        (
            CleanupStepReceipt("runtime", CleanupState.RELEASED),
            CleanupStepReceipt("workspace", CleanupState.RELEASED),
            CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
            CleanupStepReceipt(
                "lease_record",
                CleanupState.QUARANTINED,
                "identity uncertain",
            ),
        ),
    )


def receipt_from_resources(
    *resources: str,
    lease_id: str = "lease-deterministic",
) -> SandboxCleanupReceipt:
    return SandboxCleanupReceipt.from_steps(
        lease_id,
        tuple(
            CleanupStepReceipt(resource, CleanupState.RELEASED)
            for resource in resources
        ),
    )


def cleanup_receipt_projection(
    receipt: SandboxCleanupReceipt,
) -> dict[str, Any]:
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


def deterministic_sandbox_plan() -> Any:
    return SimpleNamespace(
        runtime_class=RuntimeClass.TRUSTED_PROCESS,
        runtime=SimpleNamespace(
            runtime_id="deterministic_fake",
            runtime_class=RuntimeClass.TRUSTED_PROCESS,
            measured_binary_digest=ref("runtime-binary").sha256,
        ),
        image=SimpleNamespace(image_digest=ref("runtime-image").sha256),
        security_policy=SimpleNamespace(policy_digest=ref("security-policy").sha256),
        network_policy=SimpleNamespace(policy_digest=ref("network-policy").sha256),
        verifier=SimpleNamespace(
            grant=SimpleNamespace(
                implementation_digest=ref("verifier-implementation").sha256
            )
        ),
        materialization_plan=SimpleNamespace(
            projection=lambda: {
                "schema_version": "bb.rl.test-materialization-plan.v1",
                "identity": ref("materialization-plan").sha256,
            }
        ),
    )


def exact_wp4_case() -> tuple[Any, Any, Any]:
    fixture = _resolution_fixture(algorithm="direct-v1", candidate_count=1)
    resolved = fixture.runtime.resolve_episode(fixture.request)
    return fixture, fixture.request, resolved


def conductor_compatible_case() -> tuple[Any, Any, Any, Any]:
    fixture, request, _ = exact_wp4_case()
    observation = _observation("v2")
    plan = _plan(
        label="v2",
        observation=observation,
        implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST,
    )
    binding = c.SelectionBinding(
        owner_key="sha256:"
        + hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema_version": "bb.rl.selection-owner.v1",
                    "subject_digest": plan.subject_digest,
                    "episode_id": request.episode_id,
                }
            )
        ).hexdigest(),
        request_digest=ref("selection-request").sha256,
        selection_record_digest=plan.selection_record_digest,
    )
    binding_bytes = binding.canonical_bytes()
    commit = c.SelectionCommitToken(
        binding=binding,
        binding_ref=c.ArtifactRef(
            artifact_id=binding.canonical_digest(),
            sha256=binding.canonical_digest(),
            size_bytes=len(binding_bytes),
            media_type="application/vnd.breadboard.selection-binding+json;version=1",
        ),
        verified_at="2026-07-10T12:00:00Z",
    )
    plan_bytes = plan.canonical_bytes()
    resolved = c.ResolvedEpisodePlan(
        episode_id=request.episode_id,
        subject_digest=plan.subject_digest,
        base_receipt_digest=plan.base_receipt_digest,
        final_receipt_digest=plan.final_receipt_digest,
        policy_capability_observation_digest=plan.policy_capability_observation_digest,
        selection_record_ref=c.ArtifactRef(
            artifact_id=plan.selection_record_digest,
            sha256=plan.selection_record_digest,
            size_bytes=1,
            media_type="application/vnd.breadboard.selection-record+json;version=1",
        ),
        selection_commit=commit,
        effective_plan_ref=c.ArtifactRef(
            artifact_id=plan.canonical_digest(),
            sha256=plan.canonical_digest(),
            size_bytes=len(plan_bytes),
            media_type="application/vnd.breadboard.effective-execution-plan+json;version=1",
        ),
        effective_plan=plan,
        currentness=c.CurrentnessToken(
            receipt_digest=plan.final_receipt_digest,
            subject_digest=plan.subject_digest,
            admission_policy_digest=ref("admission-policy").sha256,
            registry_snapshot_digest=ref("registry").sha256,
            revocation_scope_digest=plan.revocation.scope_digest,
            revocation_epoch=plan.revocation.epoch,
            revocation_state_digest=plan.revocation.state_digest,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
            verified_at="2026-07-10T12:00:00Z",
            expires_at="2026-07-10T13:00:00Z",
        ),
    )
    return fixture, request, resolved, observation


class DeterministicConfigRuntime:
    def __init__(self, resolved: Any, calls: list[str]) -> None:
        self.resolved = resolved
        self.calls = calls
        self.error: BaseException | None = None
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    def resolve_episode(self, request: Any) -> Any:
        self.calls.append("resolve")
        if self.error is not None:
            raise self.error
        if self.entered is not None:
            self.entered.set()
        return self.resolved


class DeterministicPolicyClient:
    def __init__(self, observation: Any, calls: list[str]) -> None:
        self.observation = observation
        self.calls = calls
        self.close_error: BaseException | None = None

    def observe(self) -> Any:
        self.calls.append("policy.observe")
        return self.observation

    async def invoke(self, request: Any) -> Any:
        self.calls.append("policy.invoke")
        raise AssertionError("deterministic fake runner never directly invokes policy")

    async def cancel(self, reason: str) -> None:
        self.calls.append(f"policy.cancel:{reason}")

    async def close(self) -> None:
        self.calls.append("policy.close")
        if self.close_error is not None:
            raise self.close_error


class DeterministicPolicyResolver:
    def __init__(self, client: DeterministicPolicyClient, calls: list[str]) -> None:
        self.client = client
        self.calls = calls
        self.error: BaseException | None = None
        self.arguments: list[tuple[Any, str, str]] = []
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def resolve(
        self, policy_binding: Any, *, episode_id: str, effective_plan_digest: str
    ) -> Any:
        self.calls.append("policy.resolve")
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        self.arguments.append((policy_binding, episode_id, effective_plan_digest))
        if self.error is not None:
            raise self.error
        return self.client


class DeterministicSession:
    def __init__(self, request: Any, binding: Any, calls: list[str]) -> None:
        self.request = request
        self.binding = binding
        self.calls = calls
        self.run_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.run_entered = asyncio.Event()
        self.run_release = asyncio.Event()
        self.block_run = False
        self.cancelled: list[str] = []
        self.cancel_entered = asyncio.Event()
        self.events: Any | None = None
        self.emit_before_error: tuple[Any, ...] = ()
        self.emit_result_events = False

    async def run(self, request: Any) -> RunnerResult:
        self.calls.append("session.run")
        self.run_entered.set()
        if self.block_run:
            await self.run_release.wait()
        if self.events is not None:
            for event in self.emit_before_error:
                await self.events.emit(event)
        if self.run_error is not None:
            raise self.run_error
        termination = RunnerTermination.SUBMITTED
        result = RunnerResult(
            episode_id=self.request.episode_id,
            effective_plan_digest=self.request.effective_plan_digest,
            original_request={
                "task_input": dict(request.task_input),
                "context": dict(request.context),
            },
            response={"answer": "deterministic"},
            termination=termination,
            turn_count=1,
            turns=(RunnerTurn(1, ({"type": "submit", "value": "deterministic"},)),),
            events=(
                RunnerTerminationEvent(
                    0,
                    self.request.episode_id,
                    self.request.effective_plan_digest,
                    1,
                    termination,
                ),
            ),
        )
        if self.events is not None and self.emit_result_events:
            for event in result.events:
                await self.events.emit(event)
        return result

    async def cancel(self, reason: str) -> Any:
        self.calls.append(f"session.cancel:{reason}")
        self.cancelled.append(reason)
        self.cancel_entered.set()
        return None

    async def close(self) -> RunnerCloseResult:
        self.calls.append("session.close")
        await self.binding.close()
        if self.close_error is not None:
            raise self.close_error
        return RunnerCloseResult(False)


class DeterministicRunner:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.open_error: BaseException | None = None
        self.open_arguments: list[tuple[Any, Any, Any, Any, Any]] = []
        self.session: DeterministicSession | None = None
        self.opened = asyncio.Event()

        self.block_session_run = False
        self.session_run_error: BaseException | None = None
        self.session_close_error: BaseException | None = None
        self.session_events: tuple[Any, ...] = ()
        self.emit_result_events = False

    async def open(
        self,
        request: Any,
        *,
        policy: Any,
        workspace: Any,
        cancellation: Any,
        events: Any,
    ) -> Any:
        self.calls.append("runner.open")
        self.open_arguments.append((request, policy, workspace, cancellation, events))
        if self.open_error is not None:
            raise self.open_error
        self.session = DeterministicSession(request, policy, self.calls)
        self.session.events = events
        self.session.block_run = self.block_session_run
        self.session.run_error = self.session_run_error
        self.session.close_error = self.session_close_error
        self.session.emit_before_error = self.session_events
        self.session.emit_result_events = self.emit_result_events
        self.opened.set()
        return self.session


class DeterministicRunnerRegistry:
    def __init__(self, runner: DeterministicRunner, calls: list[str]) -> None:
        self.runner = runner
        self.calls = calls
        self.arguments: list[tuple[str, str]] = []
        self.error: BaseException | None = None

    def resolve(self, adapter_id: str, runtime_abi: str) -> Any:
        self.calls.append("registry.resolve")
        self.arguments.append((adapter_id, runtime_abi))
        if self.error is not None:
            raise self.error
        return self.runner


class DeterministicVerifier:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.execute_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.lease_id = "verifier-lease"
        self.close_receipt = receipt_from_resources(
            "runtime",
            "workspace",
            "lease_record",
            lease_id="verifier-lease",
        )
        self.measurement = {"measurement": ref("verifier-measurement").sha256}

    async def execute(self) -> dict[str, Any]:
        self.calls.append("verifier.execute")
        if self.execute_error is not None:
            raise self.execute_error
        return {"reward_components": {"score": 1}, "result": "verified"}

    async def close(self) -> SandboxCleanupReceipt:
        self.calls.append("verifier.close")
        if self.close_error is not None:
            raise self.close_error
        return self.close_receipt


class DeterministicLease:
    def __init__(self, calls: list[str], effective_plan_digest: str) -> None:
        self.calls = calls
        self.effective_plan_digest = effective_plan_digest
        self.lease_id = "lease-deterministic"
        self.runner_workspace = SimpleNamespace(tool_bindings=())
        self.measurement = {"measurement": ref("primary-measurement").sha256}
        self._materialized = SimpleNamespace(
            receipt={"receipt": ref("materialized").sha256}
        )
        self.close_receipt = released_receipt(self.lease_id)
        self.close_error: BaseException | None = None
        self.close_entered = asyncio.Event()
        self.close_release: asyncio.Event | None = None
        self._sealed_workspace_diff: dict[str, Any] | None = None


    async def seal_for_verifier(self) -> VerifierSnapshotReceipt:
        self.calls.append("lease.seal")
        receipt = VerifierSnapshotReceipt(
            snapshot_id="snapshot-deterministic",
            source_workspace_id="workspace-deterministic",
            source_lease_id=self.lease_id,
            effective_plan_digest=self.effective_plan_digest,
            task_digest=ref("snapshot-task").sha256,
            verifier_digest=ref("snapshot-verifier").sha256,
            manifest_digest=ref("snapshot-manifest").sha256,
            root_digest=ref("snapshot-root").sha256,
            file_count=0,
            inode_count=0,
            byte_count=0,
            immutable_storage_object_id="cas/snapshot-deterministic",
        )
        patch = "diff --git a/a.py b/a.py\n"
        self._sealed_workspace_diff = {
            "returncode": 0,
            "stdout": patch,
            "stderr": "",
            "base_commit": "0" * 40,
            "git_executable_digest": ref("workspace-diff-git").sha256,
            "patch_digest": "sha256:" + hashlib.sha256(patch.encode()).hexdigest(),
            "snapshot_root_digest": receipt.root_digest,
        }
        return receipt

    def sealed_workspace_diff(self) -> dict[str, Any] | None:
        self.calls.append("workspace.diff")
        return self._sealed_workspace_diff

    async def close(self) -> SandboxCleanupReceipt:
        self.calls.append("lease.close")
        self.close_entered.set()
        if self.close_release is not None:
            await self.close_release.wait()
        if self.close_error is not None:
            raise self.close_error
        return self.close_receipt


class DeterministicEvidenceAuthority(V2EvidenceAuthority):
    def __init__(self, calls: list[str]) -> None:
        super().__init__(())
        self.calls = calls

    def validate_plan(
        self, effective_plan: Any, evidence_policy: Any, retention_policy: Any
    ) -> Any:
        self.calls.append("evidence.validate_plan")
        return super().validate_plan(effective_plan, evidence_policy, retention_policy)

    def materialize(
        self,
        plan: Any,
        *,
        runner_result: Any,
        verifier_snapshot: Any,
        verifier_result: Any,
    ) -> Any:
        self.calls.append("evidence.materialize")
        return super().materialize(
            plan,
            runner_result=runner_result,
            verifier_snapshot=verifier_snapshot,
            verifier_result=verifier_result,
        )


class DeterministicSandboxRuntime:
    def __init__(self, resolved: Any, calls: list[str]) -> None:
        self.calls = calls
        self.open_error: BaseException | None = None
        self.verifier_error: BaseException | None = None
        self.open_arguments: list[Any] = []
        self.open_entered = asyncio.Event()
        self.open_release: asyncio.Event | None = None
        self.verifier_arguments: list[tuple[Any, Any]] = []
        self.lease = DeterministicLease(
            calls,
            resolved.effective_plan.canonical_digest(),
        )
        self.verifier = DeterministicVerifier(calls)
        self.reconcile_receipts: tuple[SandboxCleanupReceipt, ...] = ()
        self.registries = SimpleNamespace(
            evidence_policies=(
                c.EvidencePolicyRegistryRecord(
                    policy=resolved.effective_plan.evidence,
                    required_roles=(),
                ),
            ),
            retention_policies=(
                c.RetentionPolicyRegistryRecord(
                    grant=c.RetentionPolicyGrant(
                        policy=resolved.effective_plan.retention,
                        minimum_seconds=0,
                        maximum_seconds=86_400,
                    ),
                ),
            ),
        )
        self.installed_authorities = SimpleNamespace()

    async def open(self, request: Any) -> Any:
        self.calls.append("sandbox.open")
        self.open_arguments.append(request)
        self.open_entered.set()
        if self.open_release is not None:
            await self.open_release.wait()
        if self.open_error is not None:
            raise self.open_error
        return self.lease

    async def open_verifier(self, lease: Any, snapshot: Any) -> Any:
        self.calls.append("sandbox.open_verifier")
        self.verifier_arguments.append((lease, snapshot))
        if self.verifier_error is not None:
            raise self.verifier_error
        return self.verifier

    async def reconcile_stale(self) -> tuple[SandboxCleanupReceipt, ...]:
        self.calls.append("sandbox.reconcile_stale")
        return self.reconcile_receipts

    async def close(self) -> tuple[Any, ...]:
        self.calls.append("sandbox.manager.close")
        return ()


class DeterministicEvidenceRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.events: list[Any] = []
        self._runner_events: dict[str, list[Any]] = {}
        self.runner_event_refs: dict[str, list[ArtifactRef]] = {}
        self.completed_inputs: list[Any] = []
        self.failed_completed_inputs: list[Any] = []
        self.closed_inputs: list[Any] = []
        self.quarantine_inputs: list[Any] = []
        self.recovered: dict[str, Any] = {}
        self.locators: tuple[Any, ...] = ()
        self.scan_entries: tuple[Any, ...] | None = None
        self.blocked_episode_ids: set[str] = set()
        self.publish_completed_error: BaseException | None = None
        self.responses: dict[str, bytes] = {}
        self.publish_closed_error: BaseException | None = None
        self.quarantine_error: BaseException | None = None
        self.append_transition_error_kinds: set[str] = set()
        self.append_transition_fail_once = True
        self.recover_error: BaseException | None = None
        self.evidence_object_inputs: list[Any] = []
        self.corrupt_locator_inputs: list[Any] = []

    def scan_locators(self) -> tuple[Any, ...]:
        self.calls.append("repo.enumerate")
        if self.scan_entries is not None:
            return self.scan_entries
        return tuple(
            SimpleNamespace(
                locator_key=f"{locator.episode_id}.json",
                episode_id_hint=locator.episode_id,
                record=locator,
                failure=None,
            )
            for locator in self.locators
        )

    def quarantine_corrupt_locator(self, entry: Any, failure: Any) -> None:
        self.calls.append("repo.quarantine_corrupt_locator")
        self.corrupt_locator_inputs.append((entry, failure))
        if entry.episode_id_hint is not None:
            self.blocked_episode_ids.add(entry.episode_id_hint)

    def enumerate_locators(self) -> tuple[Any, ...]:
        self.calls.append("repo.enumerate")
        return self.locators

    def recover(self, episode_id: str) -> Any:
        self.calls.append(f"repo.recover:{episode_id}")
        if episode_id in self.blocked_episode_ids:
            raise EvidenceCorruptError("episode locator is quarantined")
        if self.recover_error is not None:
            raise self.recover_error
        return self.recovered.get(episode_id)

    def get_response_bytes(self, artifact_ref: ArtifactRef) -> bytes:
        self.calls.append(f"repo.response:{artifact_ref.artifact_id}")
        return self.responses[artifact_ref.artifact_id]

    def append_transition(self, event: Any) -> ArtifactRef:
        self.calls.append(f"event:{event.event_kind}")
        if event.event_kind in self.append_transition_error_kinds:
            if self.append_transition_fail_once:
                self.append_transition_error_kinds.remove(event.event_kind)
            raise RuntimeError(f"deterministic append failure: {event.event_kind}")
        self.events.append(event)
        return ref(f"event-{event.episode_id}-{event.sequence}-{event.event_kind}")

    def append_runner_event(
        self,
        episode_id: str,
        effective_plan_digest: str,
        event: Any,
    ) -> Any:
        self.calls.append(f"runner_event:{event.sequence}")
        events = self._runner_events.setdefault(episode_id, [])
        if event.sequence != len(events):
            raise ValueError("deterministic runner event sequence is not contiguous")
        events.append(event)
        event_digest = canonical_digest(event)
        event_ref = ref(f"runner-event-{episode_id}-{event.sequence}-{event_digest}")
        self.runner_event_refs.setdefault(episode_id, []).append(event_ref)
        return SimpleNamespace(
            event_ref=event_ref,
            sequence=event.sequence,
            event_digest=event_digest,
        )

    def recover_runner_events(self, episode_id: str) -> tuple[Any, ...]:
        self.calls.append(f"repo.recover_runner_events:{episode_id}")
        return tuple(self._runner_events.get(episode_id, ()))

    def publish_evidence_objects(
        self,
        episode_id: str,
        authority_plan: Any,
        inputs: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        self.calls.append("repo.publish_evidence_objects")
        self.evidence_object_inputs.extend(inputs)
        return ()

    def prepare_export_pins(
        self,
        episode_id: str,
        completed: Any,
        *,
        subject_digest: str,
        scope: str,
    ) -> Any:
        self.calls.append("repo.prepare_export_pins")
        return SimpleNamespace(
            authorization_refs=(),
            redaction_decision_refs=(),
        )

    def publish_completed(self, inputs: Any) -> Any:
        self.calls.append("repo.publish_completed")
        if (
            inputs.verifier_cleanup_receipt is not None
            and inputs.verifier_cleanup_receipt.lease_id != inputs.verifier_lease_id
        ):
            raise EvidenceValidationError("cleanup receipt lease mismatch")
        self.completed_inputs.append(inputs)
        if self.publish_completed_error is not None:
            raise self.publish_completed_error
        return SimpleNamespace(
            envelope_ref=ref("completed-envelope"),
            envelope=SimpleNamespace(),
            result_ref=ref("run-response"),
            evidence_manifest_ref=ref("evidence-manifest"),
            evidence_root=ref("evidence-root").sha256,
            artifact_manifest_ref=ref("artifact-manifest"),
            primary_measurement_digest=ref("primary-measurement").sha256,
            verifier_measurement_digest=ref("verifier-measurement").sha256,
            verifier_result_digest=ref("verifier-result").sha256,
        )

    def publish_failed_completed(self, inputs: Any) -> Any:
        self.calls.append("repo.publish_failed_completed")
        self.failed_completed_inputs.append(inputs)
        if self.publish_completed_error is not None:
            raise self.publish_completed_error
        return SimpleNamespace(
            envelope_ref=ref("completed-envelope"),
            envelope=SimpleNamespace(),
            result_ref=None,
            evidence_manifest_ref=None,
            evidence_root=None,
            artifact_manifest_ref=None,
            primary_measurement_digest=None,
            verifier_measurement_digest=None,
            verifier_result_digest=None,
        )

    def publish_closed(self, inputs: Any) -> Any:
        self.calls.append("repo.publish_closed")
        required_resources = (
            "child_verifier",
            "runtime",
            "workspace",
            "cache_holder",
            "lease_record",
        )
        if inputs.cleanup_receipt is None:
            if inputs.cleanup_required_resources:
                raise EvidenceValidationError(
                    "absent cleanup receipt cannot claim cleanup resources"
                )
        else:
            resources = tuple(step.resource for step in inputs.cleanup_receipt.steps)
            released = {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
            if (
                tuple(inputs.cleanup_required_resources) != required_resources
                or len(resources) != len(set(resources))
                or set(resources) != set(required_resources)
                or inputs.cleanup_receipt.state not in released
                or any(
                    step.state not in released for step in inputs.cleanup_receipt.steps
                )
            ):
                raise EvidenceValidationError(
                    "primary cleanup resource contract mismatch"
                )
        self.closed_inputs.append(inputs)
        if self.publish_closed_error is not None:
            raise self.publish_closed_error
        self.events.append(inputs.closed_event)
        return SimpleNamespace(
            envelope_ref=ref("closed-envelope"),
            envelope=SimpleNamespace(),
            locator=SimpleNamespace(
                current_state="closed",
                latest_event_head=inputs.closed_event.digest,
                latest_event_ref=ref(
                    f"event-{inputs.episode_id}-{inputs.closed_event.sequence}-closed"
                ),
            ),
        )

    def quarantine(self, inputs: Any) -> Any:
        self.calls.append("repo.quarantine")
        self.quarantine_inputs.append(inputs)
        if self.quarantine_error is not None:
            raise self.quarantine_error
        return SimpleNamespace(quarantine_ref=ref("quarantine"))


@dataclass
class DeterministicServiceCase:
    fixture: Any
    request: Any
    resolved: Any
    calls: list[str]
    config: DeterministicConfigRuntime
    policy_client: DeterministicPolicyClient
    policy_resolver: DeterministicPolicyResolver
    runner: DeterministicRunner
    registry: DeterministicRunnerRegistry
    sandbox: DeterministicSandboxRuntime
    repository: DeterministicEvidenceRepository
    evidence_authority: DeterministicEvidenceAuthority


def service_case() -> DeterministicServiceCase:
    fixture, request, resolved, observation = conductor_compatible_case()
    calls: list[str] = []
    config = DeterministicConfigRuntime(resolved, calls)
    client = DeterministicPolicyClient(observation, calls)
    resolver = DeterministicPolicyResolver(client, calls)
    runner = DeterministicRunner(calls)
    registry = DeterministicRunnerRegistry(runner, calls)
    sandbox = DeterministicSandboxRuntime(resolved, calls)
    evidence_authority = DeterministicEvidenceAuthority(calls)
    repository = DeterministicEvidenceRepository(calls)
    return DeterministicServiceCase(
        fixture=fixture,
        request=request,
        resolved=resolved,
        calls=calls,
        config=config,
        policy_client=client,
        policy_resolver=resolver,
        runner=runner,
        registry=registry,
        sandbox=sandbox,
        repository=repository,
        evidence_authority=evidence_authority,
    )


def canonical_create_response_bytes(response: Any) -> bytes:
    return canonical_json_bytes(
        {
            "episode_id": response.episode_id,
            "create_fingerprint": response.create_fingerprint,
            "state": response.state.value,
            "effective_plan_digest": response.effective_plan_digest,
            "selection_record_ref": response.selection_record_ref.model_dump(
                mode="json"
            ),
            "effective_plan_ref": response.effective_plan_ref.model_dump(mode="json"),
            "policy_binding_digest": response.policy_binding_digest,
            "selection_commit": response.selection_commit.model_dump(mode="json"),
            "base_receipt_digest": response.base_receipt_digest,
            "final_receipt_digest": response.final_receipt_digest,
            "policy_observation_digest": response.policy_observation_digest,
            "sandbox_preflight": {
                "runtime": response.sandbox_preflight.runtime,
                "runtime_class": response.sandbox_preflight.runtime_class.value,
                "runtime_binary_digest": (
                    response.sandbox_preflight.runtime_binary_digest
                ),
                "image_digest": response.sandbox_preflight.image_digest,
                "security_policy_digest": (
                    response.sandbox_preflight.security_policy_digest
                ),
                "network_policy_digest": (
                    response.sandbox_preflight.network_policy_digest
                ),
                "verifier_digest": response.sandbox_preflight.verifier_digest,
                "materialization_plan_digest": (
                    response.sandbox_preflight.materialization_plan_digest
                ),
            },
        }
    )


def deterministic_clock() -> datetime:
    return datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
