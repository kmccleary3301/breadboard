from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from builtins import BaseExceptionGroup
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

from breadboard_engine.compilation.contracts import canonical_json_bytes

from breadboard.rl.harness.config_runtime import ConfigRuntime
from breadboard.rl.harness.contracts import ArtifactRef as ContractArtifactRef
from breadboard.rl.harness.contracts import (
    EffectiveExecutionPlan,
    PolicyBindingRef,
    ResolvedEpisodePlan,
    ResolveEpisodeRequest,
    RuntimeClass,
    SelectionCommitToken,
)
from breadboard.rl.harness.evidence import (
    AuthorityAccessEventV2,
    ClosedEpisodeEnvelopeV2,
    ClosedPublicationInputsV2,
    ClosedPublicationV2,
    CompletedEpisodeEnvelopeV2,
    CompletedPublicationInputsV2,
    CompletedPublicationV2,
    EpisodeEvidenceRepository,
    EvidenceAuthorityPlanV2,
    EvidenceCorruptError,
    ExportAuthorizationClaimsV2,
    ExportDeniedError,
    ExportManifestV2,
    FailedCompletedPublicationInputsV2,
    LifecycleEventV2,
    QuarantinePublicationInputsV2,
    RecoveredEpisodeV2,
    SafeFailureFactV2,
    V2EvidenceAuthority,
    canonical_digest,
)
from breadboard.rl.harness.materialization import (
    CleanupState,
    MaterializationKey,
    SandboxCleanupReceipt,
    VerifierSnapshotReceipt,
    WorkspaceOpenRequest,
)
from breadboard.rl.harness.runners.base import (
    PolicyRuntimeClientPort,
    RunnerAdapter,
    RunnerAdapterRegistry,
    RunnerCancellationProbe,
    RunnerCancelled,
    RunnerEvent,
    RunnerEventSink,
    RunnerOpenRequest,
    RunnerPlanError,
    RunnerRequestError,
    RunnerResult,
    RunnerRunRequest,
    RunnerSession,
)
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_ADAPTER_ID,
    ConductorRunRequest,
    PolicyRuntimeBinding,
)
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_TOOL_DEFINITIONS,
    TerminalLoopLimits,
    TerminalRunRequest,
)
from breadboard.rl.harness.sandbox import (
    SandboxExecutionPlan,
    SandboxFault,
    SandboxRuntimeManager,
    SandboxWorkspaceLease,
    VerifierWorkspaceLease,
    build_sandbox_execution_plan,
)
from breadboard.artifacts.references import ArtifactRef


class EpisodeLifecycleState(str, Enum):
    ACCEPTED = "accepted"
    ALLOCATING = "allocating"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    CANCEL_REQUESTED = "cancel_requested"
    CLOSING = "closing"
    CLOSED = "closed"
    QUARANTINED = "quarantined"


class _ServiceLifecycleState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class EpisodePrimaryDisposition(str, Enum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EpisodeCleanupDisposition(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    RELEASED = "released"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class V2OperationDisposition(str, Enum):
    FRESH = "fresh"
    CACHED = "cached"


class V2FaultClass(str, Enum):
    TIMEOUT = "timeout"
    CANCEL = "cancel"
    REVOCATION = "revocation"
    EGRESS = "egress"
    RESOURCE = "resource"
    VERIFIER = "verifier"
    ARTIFACT = "artifact"
    TRANSPORT = "transport"


class V2FaultBoundary(str, Enum):
    PRE_ALLOCATION = "pre-allocation"
    POST_ALLOCATION = "post-allocation"


_V2_FAULT_CONTRACT: Mapping[
    V2FaultClass,
    tuple[str, V2FaultBoundary, EpisodePrimaryDisposition],
] = MappingProxyType(
    {
        V2FaultClass.TIMEOUT: (
            "TIMEOUT",
            V2FaultBoundary.POST_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
        V2FaultClass.CANCEL: (
            "CANCELLED",
            V2FaultBoundary.POST_ALLOCATION,
            EpisodePrimaryDisposition.CANCELLED,
        ),
        V2FaultClass.REVOCATION: (
            "REVOKED",
            V2FaultBoundary.PRE_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
        V2FaultClass.EGRESS: (
            "EGRESS_DENIED",
            V2FaultBoundary.POST_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
        V2FaultClass.RESOURCE: (
            "RESOURCE_EXHAUSTED",
            V2FaultBoundary.POST_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
        V2FaultClass.VERIFIER: (
            "VERIFIER_FAILED",
            V2FaultBoundary.POST_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
        V2FaultClass.ARTIFACT: (
            "ARTIFACT_FAILED",
            V2FaultBoundary.POST_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
        V2FaultClass.TRANSPORT: (
            "TRANSPORT_FAILED",
            V2FaultBoundary.PRE_ALLOCATION,
            EpisodePrimaryDisposition.FAILED,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class V2FaultInjectionSpec:
    episode_id: str
    immutable_ref: str
    fault_class: V2FaultClass

    def __post_init__(self) -> None:
        if (
            type(self.episode_id) is not str
            or not self.episode_id
            or type(self.immutable_ref) is not str
            or "://" not in self.immutable_ref
            or "?" in self.immutable_ref
            or "#" in self.immutable_ref
            or any(character.isspace() for character in self.immutable_ref)
            or "@sha256:" not in self.immutable_ref
            or len(self.immutable_ref.rsplit("@", 1)[-1]) != 71
            or any(
                character not in "0123456789abcdef"
                for character in self.immutable_ref.rsplit("@", 1)[-1][7:]
            )
            or type(self.fault_class) is not V2FaultClass
        ):
            raise ValueError("fault injection spec is not exact and content-addressed")

    @property
    def error_code(self) -> str:
        return _V2_FAULT_CONTRACT[self.fault_class][0]

    @property
    def boundary(self) -> V2FaultBoundary:
        return _V2_FAULT_CONTRACT[self.fault_class][1]

    @property
    def primary_disposition(self) -> EpisodePrimaryDisposition:
        return _V2_FAULT_CONTRACT[self.fault_class][2]


@dataclass(frozen=True, slots=True)
class V2FaultInjectionAdmission:
    spec: V2FaultInjectionSpec
    _service_authority: object


@dataclass(frozen=True, slots=True)
class V2EpisodeAuditSpec:
    episode_id: str
    authority_ref: str
    canary: str

    def __post_init__(self) -> None:
        digest = self.authority_ref.rsplit("@", 1)[-1]
        if (
            type(self.episode_id) is not str
            or not self.episode_id
            or type(self.authority_ref) is not str
            or "://" not in self.authority_ref
            or "?" in self.authority_ref
            or "#" in self.authority_ref
            or any(character.isspace() for character in self.authority_ref)
            or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
            or type(self.canary) is not str
            or not self.canary
        ):
            raise ValueError("episode audit spec is not exact and content-addressed")


@dataclass(frozen=True, slots=True)
class V2EpisodeAuditAdmission:
    spec: V2EpisodeAuditSpec
    _service_authority: object


class V2FaultInjectionAuthority:
    """Composition-owned authority for exact F5 injection and access tuples."""

    def __init__(
        self,
        *,
        source_ref: str,
        fault_specs: tuple[V2FaultInjectionSpec, ...],
        audit_specs: tuple[V2EpisodeAuditSpec, ...],
    ) -> None:
        source_digest = source_ref.rsplit("@", 1)[-1]
        if (
            type(source_ref) is not str
            or "://" not in source_ref
            or "?" in source_ref
            or "#" in source_ref
            or any(character.isspace() for character in source_ref)
            or len(source_digest) != 71
            or not source_digest.startswith("sha256:")
            or any(
                character not in "0123456789abcdef" for character in source_digest[7:]
            )
        ):
            raise ValueError(
                "fault injection authority source must be content-addressed"
            )
        faults = tuple(fault_specs)
        audits = tuple(audit_specs)
        if (
            any(type(spec) is not V2FaultInjectionSpec for spec in faults)
            or any(type(spec) is not V2EpisodeAuditSpec for spec in audits)
            or len({spec.episode_id for spec in faults}) != len(faults)
            or len({spec.episode_id for spec in audits}) != len(audits)
        ):
            raise ValueError(
                "fault injection authority tuples must be exact and episode-unique"
            )
        self.source_ref = source_ref
        self._fault_specs = frozenset(faults)
        self._audit_specs = MappingProxyType({spec.episode_id: spec for spec in audits})
        self._access_events: dict[str, list[AuthorityAccessEventV2]] = {}
        self._lock = threading.Lock()

    def authorizes_fault(self, spec: V2FaultInjectionSpec) -> bool:
        return type(spec) is V2FaultInjectionSpec and spec in self._fault_specs

    def authorizes_audit(self, spec: V2EpisodeAuditSpec) -> bool:
        return (
            type(spec) is V2EpisodeAuditSpec
            and self._audit_specs.get(spec.episode_id) == spec
        )

    def matches(
        self,
        *,
        source_ref: str,
        fault_specs: tuple[V2FaultInjectionSpec, ...],
        audit_specs: tuple[V2EpisodeAuditSpec, ...],
    ) -> bool:
        return (
            self.source_ref == source_ref
            and self._fault_specs == frozenset(fault_specs)
            and dict(self._audit_specs)
            == {spec.episode_id: spec for spec in audit_specs}
        )

    def read_episode_canary(
        self,
        *,
        actor_episode_id: str,
        authority_episode_id: str,
    ) -> AuthorityAccessEventV2:
        audit = self._audit_specs.get(authority_episode_id)
        if audit is None:
            raise KeyError("unknown episode audit authority")
        with self._lock:
            events = self._access_events.setdefault(actor_episode_id, [])
            event = AuthorityAccessEventV2(
                sequence=len(events) + 1,
                actor_episode_id=actor_episode_id,
                authority_episode_id=authority_episode_id,
                authority_ref=audit.authority_ref,
                canary=audit.canary,
                source_ref=self.source_ref,
            )
            events.append(event)
            return event

    def access_events(self, episode_id: str) -> tuple[AuthorityAccessEventV2, ...]:
        with self._lock:
            return tuple(self._access_events.get(episode_id, ()))


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class V2OperationResult(Generic[T]):
    response: T
    disposition: V2OperationDisposition


class PolicyRuntimeClientResolver(Protocol):
    async def resolve(
        self,
        policy_binding: PolicyBindingRef,
        *,
        episode_id: str,
        effective_plan_digest: str,
    ) -> PolicyRuntimeClientPort: ...


@dataclass(frozen=True, slots=True)
class V2LifecycleDependencies:
    config_runtime: ConfigRuntime
    runner_registry: RunnerAdapterRegistry
    sandbox_runtime: SandboxRuntimeManager
    policy_client_resolver: PolicyRuntimeClientResolver
    evidence_repository: EpisodeEvidenceRepository
    evidence_authority: V2EvidenceAuthority
    clock: Callable[[], datetime]
    fault_injection_authority: V2FaultInjectionAuthority | None = None


@dataclass(frozen=True, slots=True)
class V2SandboxPreflightIdentity:
    runtime: str
    runtime_class: RuntimeClass
    runtime_binary_digest: str
    image_digest: str
    security_policy_digest: str
    network_policy_digest: str
    verifier_digest: str
    materialization_plan_digest: str


@dataclass(frozen=True, slots=True)
class V2CreateResult:
    episode_id: str
    create_fingerprint: str
    state: EpisodeLifecycleState
    effective_plan_digest: str
    selection_record_ref: ContractArtifactRef
    effective_plan_ref: ContractArtifactRef
    policy_binding_digest: str
    selection_commit: SelectionCommitToken
    base_receipt_digest: str
    final_receipt_digest: str
    policy_observation_digest: str
    sandbox_preflight: V2SandboxPreflightIdentity


@dataclass(frozen=True, slots=True)
class V2RunResult:
    episode_id: str
    create_fingerprint: str
    run_fingerprint: str
    primary_disposition: EpisodePrimaryDisposition
    response: Mapping[str, Any] | None
    termination: str | None
    turn_count: int
    completed_envelope_ref: ArtifactRef | None
    closed_envelope_ref: ArtifactRef | None
    result_ref: ArtifactRef | None = None
    evidence_manifest_ref: ArtifactRef | None = None
    evidence_root: str | None = None
    reward: float | int | None = None
    reward_components: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    artifact_manifest_ref: ArtifactRef | None = None
    primary_measurement_digest: str | None = None
    verifier_measurement_digest: str | None = None
    verifier_result_digest: str | None = None
    workspace_diff: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class V2CancellationResult:
    episode_id: str
    requested: bool
    reason: str
    state: EpisodeLifecycleState


@dataclass(frozen=True, slots=True)
class V2CloseResult:
    episode_id: str
    state: EpisodeLifecycleState
    cleanup_disposition: EpisodeCleanupDisposition
    closed_envelope_ref: ArtifactRef | None


@dataclass(frozen=True, slots=True)
class V2EpisodeState:
    episode_id: str
    state: EpisodeLifecycleState
    transition_sequence: int
    transition_head_digest: str
    create_fingerprint: str | None
    run_fingerprint: str | None
    primary_disposition: EpisodePrimaryDisposition | None
    cleanup_disposition: EpisodeCleanupDisposition
    completed_envelope_ref: ArtifactRef | None
    closed_envelope_ref: ArtifactRef | None


class V2EpisodeError(RuntimeError):
    def __init__(self, failure: SafeFailureFactV2) -> None:
        super().__init__(failure.code)
        self.failure = failure


class V2EpisodeNotFound(V2EpisodeError):
    pass


class V2EpisodeConflict(V2EpisodeError):
    pass


class V2EpisodeRejected(V2EpisodeError):
    pass


class V2EpisodeUnavailable(V2EpisodeError):
    pass


class V2EpisodeQuarantined(V2EpisodeError):
    pass


class V2FaultInjectionError(V2EpisodeUnavailable):
    def __init__(self, admission: V2FaultInjectionAdmission) -> None:
        self.admission = admission
        super().__init__(
            _v2_failure(
                "fault_injection",
                admission.spec.error_code,
                "none",
                admission.spec.boundary.value,
            )
        )


@dataclass(frozen=True, slots=True)
class _V2RecoveredRequest:
    episode_id: str


@dataclass(frozen=True, slots=True)
class _V2BindingCloseOutcome:
    cancellations: tuple[asyncio.CancelledError, ...]
    failure: BaseException | None


@dataclass(slots=True)
class _V2EpisodeCoordinator:
    request: ResolveEpisodeRequest | _V2RecoveredRequest
    create_fingerprint: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    state: EpisodeLifecycleState = EpisodeLifecycleState.ACCEPTED
    resolved_plan: ResolvedEpisodePlan | None = None
    resolved_subject_digest: str | None = None
    runner: RunnerAdapter[Any] | None = None
    binding: PolicyRuntimeBinding | None = None
    evidence_authority_plan: EvidenceAuthorityPlanV2 | None = None
    owner_cancel_sent: bool = False
    binding_close_task: asyncio.Task[_V2BindingCloseOutcome] | None = None
    binding_close_error: BaseException | None = None
    binding_released: bool = False
    lease: SandboxWorkspaceLease | None = None
    primary_lease_id: str | None = None
    session: RunnerSession[ConductorRunRequest] | None = None
    session_close_task: asyncio.Task[Any] | None = None
    create_result: V2CreateResult | None = None
    run_result: V2RunResult | None = None
    run_fingerprint: str | None = None
    completed: CompletedPublicationV2 | None = None
    closed: ClosedPublicationV2 | None = None
    primary_disposition: EpisodePrimaryDisposition | None = None
    cleanup_disposition: EpisodeCleanupDisposition = EpisodeCleanupDisposition.PENDING
    cancel_reason: str = ""
    cancel_fingerprint: str | None = None
    terminal_committed: bool = False
    create_task: asyncio.Task[V2CreateResult] | None = None
    create_observation: asyncio.Task[BaseException | None] | None = None
    run_task: asyncio.Task[V2RunResult] | None = None
    run_observation: asyncio.Task[BaseException | None] | None = None
    close_task: asyncio.Task[V2CloseResult] | None = None
    cleanup_task: asyncio.Task[V2CloseResult] | None = None
    cleanup_receipt: SandboxCleanupReceipt | None = None
    primary_failure: SafeFailureFactV2 | None = None
    session_close_failure: SafeFailureFactV2 | None = None
    verifier_cleanup_receipt: SandboxCleanupReceipt | None = None
    verifier_cleanup_task: asyncio.Task[SandboxCleanupReceipt] | None = None
    verifier_cleanup_failure: SafeFailureFactV2 | None = None
    verifier_lease_id: str | None = None
    verifier_snapshot: VerifierSnapshotReceipt | None = None
    verifier_result: Mapping[str, Any] | None = None
    workspace_diff: Mapping[str, Any] | None = None
    runner_event_refs: list[ArtifactRef] = field(default_factory=list)
    fault_injection: V2FaultInjectionAdmission | None = None
    fault_injection_consumed: bool = False
    episode_audit: V2EpisodeAuditAdmission | None = None
    episode_audit_observed: bool = False
    last_event: LifecycleEventV2 | None = None
    last_event_ref: ArtifactRef | None = None


class _V2CancellationProbe(RunnerCancellationProbe):
    __slots__ = ("_coordinator",)

    def __init__(self, coordinator: _V2EpisodeCoordinator) -> None:
        self._coordinator = coordinator

    def raise_if_cancelled(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        if self._coordinator.cancel_event.is_set():
            error = asyncio.CancelledError(
                self._coordinator.cancel_reason or "cancelled"
            )
            setattr(error, "checkpoint", checkpoint)
            setattr(error, "turn", turn)
            setattr(error, "call_id", call_id)
            raise error


class _V2DurableRunnerEventSink(RunnerEventSink):
    __slots__ = (
        "_coordinator",
        "_effective_plan_digest",
        "_events",
        "_lock",
        "_repository",
    )

    def __init__(
        self,
        repository: EpisodeEvidenceRepository,
        coordinator: _V2EpisodeCoordinator,
        effective_plan_digest: str,
    ) -> None:
        self._repository = repository
        self._coordinator = coordinator
        self._effective_plan_digest = effective_plan_digest
        self._events: list[RunnerEvent] = []
        self._lock = asyncio.Lock()

    async def emit(self, event: RunnerEvent) -> None:
        async with self._lock:
            if event.sequence != len(self._events):
                raise ValueError("runner events must be contiguous")
            publication = self._repository.append_runner_event(
                self._coordinator.request.episode_id,
                self._effective_plan_digest,
                event,
            )
            self._coordinator.runner_event_refs.append(publication.event_ref)
            self._events.append(event)

    @property
    def events(self) -> tuple[RunnerEvent, ...]:
        return tuple(self._events)


async def _observe_owned_task(
    task: asyncio.Task[T],
) -> tuple[T | None, asyncio.CancelledError | None, BaseException | None]:
    current = asyncio.current_task()
    entry_cancelling = current.cancelling() if current is not None else 0
    cancellation: asyncio.CancelledError | None = None
    completion = asyncio.get_running_loop().create_future()

    def mark_terminal(_task: asyncio.Task[T]) -> None:
        if not completion.done():
            completion.set_result(None)

    task.add_done_callback(mark_terminal)
    while not task.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError as exc:
            new_cancellations = (
                max(current.cancelling() - entry_cancelling, 0)
                if current is not None
                else 0
            )
            if new_cancellations:
                if cancellation is None:
                    cancellation = exc
                for _ in range(new_cancellations):
                    current.uncancel()
    try:
        return task.result(), cancellation, None
    except BaseException as failure:
        return None, cancellation, failure


async def _await_owned_close(task: asyncio.Task[None]) -> None:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
    failure: BaseException | None = None
    try:
        task.result()
    except BaseException as exc:
        failure = exc
    if cancellation is not None:
        if failure is not None:
            raise BaseExceptionGroup(
                "service shutdown cancelled and failed",
                [cancellation, failure],
            )
        raise cancellation
    if failure is not None:
        raise failure


def _materialize_runner_request(
    request: ConductorRunRequest,
    effective_plan: EffectiveExecutionPlan,
    *,
    episode_id: str,
    effective_plan_digest: str,
) -> RunnerRunRequest:
    adapter_id = effective_plan.runner.adapter_id
    if adapter_id == CONDUCTOR_ADAPTER_ID:
        return request
    if adapter_id != TERMINAL_ADAPTER_ID:
        raise RunnerPlanError(
            "selected runner has no V2 request materializer",
            code="request_materializer_unavailable",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
        )
    responses_create_params = request.task_input.get("responses_create_params")
    if not isinstance(responses_create_params, Mapping):
        raise RunnerRequestError(
            "terminal task input requires responses_create_params",
            code="request_type_invalid",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
        )
    limits = effective_plan.effective_capabilities.limits
    if limits.action_timeout_ms % 1000:
        raise RunnerPlanError(
            "terminal action timeout must be an exact number of seconds",
            code="limit_projection_mismatch",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
        )
    return TerminalRunRequest(
        responses_create_params=responses_create_params,
        tools=TERMINAL_TOOL_DEFINITIONS,
        limits=TerminalLoopLimits(
            max_turns=limits.max_turns,
            action_timeout_seconds=limits.action_timeout_ms // 1000,
            max_observation_chars=limits.observation_bytes,
        ),
    )


class BreadBoardV2EpisodeService:
    def __init__(self, dependencies: V2LifecycleDependencies) -> None:
        if type(dependencies) is not V2LifecycleDependencies:
            raise TypeError("dependencies must be exact V2LifecycleDependencies")
        self._dependencies = dependencies
        self._coordinators: dict[str, _V2EpisodeCoordinator] = {}
        self._dictionary_lock = asyncio.Lock()
        self._lifecycle_state = _ServiceLifecycleState.OPEN
        self._started = False
        self._start_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._active_tasks: dict[
            asyncio.Task[Any], asyncio.Task[BaseException | None]
        ] = {}
        self._unclaimed_task_failures: dict[asyncio.Task[Any], BaseException] = {}
        self._fault_injection_authority = object()
        self._fault_injection_admissions: dict[str, V2FaultInjectionAdmission] = {}
        self._episode_audit_authority = object()
        self._episode_audit_admissions: dict[str, V2EpisodeAuditAdmission] = {}

    def _register_operation_task(
        self,
        coordinator: _V2EpisodeCoordinator,
        kind: str,
        coroutine: Any,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        observation = asyncio.create_task(self._observe_operation_task(task))
        self._active_tasks[task] = observation
        if kind == "create":
            coordinator.create_observation = observation
        elif kind == "run":
            coordinator.run_observation = observation
        else:
            raise ValueError("operation task kind must be create or run")
        return task

    async def _observe_operation_task(
        self, task: asyncio.Task[Any]
    ) -> BaseException | None:
        failure: BaseException | None = None
        try:
            await asyncio.shield(task)
        except BaseException as exc:
            failure = exc
        finally:
            async with self._dictionary_lock:
                unexpected = self._unexpected_operation_failure(failure)
                if unexpected is not None:
                    self._unclaimed_task_failures[task] = unexpected
                self._active_tasks.pop(task, None)
        return failure

    @staticmethod
    def _unexpected_operation_failure(
        failure: BaseException | None,
    ) -> BaseException | None:
        if failure is None or isinstance(
            failure,
            (V2EpisodeError, asyncio.CancelledError),
        ):
            return None
        return failure

    async def _coordinator_operation_failures(
        self, coordinator: _V2EpisodeCoordinator
    ) -> list[BaseException]:
        async with coordinator.lock:
            pairs = tuple(
                (task, observation)
                for task, observation in (
                    (coordinator.create_task, coordinator.create_observation),
                    (coordinator.run_task, coordinator.run_observation),
                )
                if task is not None and observation is not None
            )
            coordinator.create_observation = None
            coordinator.run_observation = None
        if not pairs:
            return []
        outcomes = await asyncio.gather(
            *(asyncio.shield(observation) for _, observation in pairs)
        )
        async with self._dictionary_lock:
            for task, _ in pairs:
                self._unclaimed_task_failures.pop(task, None)
        return [
            unexpected
            for outcome in outcomes
            if (unexpected := self._unexpected_operation_failure(outcome)) is not None
        ]

    async def _drain_operation_tasks(self) -> list[BaseException]:
        while True:
            async with self._dictionary_lock:
                observations = tuple(self._active_tasks.values())
                if not observations:
                    failures = list(self._unclaimed_task_failures.values())
                    self._unclaimed_task_failures.clear()
                    return failures
            await asyncio.gather(
                *(asyncio.shield(observation) for observation in observations)
            )

    async def start(self) -> None:
        async with self._dictionary_lock:
            if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "availability",
                        "service_closing",
                        "retry_new_service",
                        "none",
                    )
                )
            if self._started:
                return
            task = self._start_task
            if task is None:
                task = asyncio.create_task(self._start_owner())
                self._start_task = task
        await asyncio.shield(task)

    async def _start_owner(self) -> None:
        task = asyncio.current_task()
        try:
            recovered: list[tuple[Any, RecoveredEpisodeV2]] = []
            for entry in self._dependencies.evidence_repository.scan_locators():
                if entry.failure is not None:
                    self._dependencies.evidence_repository.quarantine_corrupt_locator(
                        entry, entry.failure
                    )
                    continue
                if entry.record is None:
                    continue
                try:
                    item = self._dependencies.evidence_repository.recover(
                        entry.record.episode_id
                    )
                except EvidenceCorruptError:
                    failure = _v2_failure(
                        "evidence",
                        "evidence_corrupt",
                        "reconcile",
                        "durable",
                    )
                    self._dependencies.evidence_repository.quarantine_corrupt_locator(
                        entry, failure
                    )
                    continue
                if item is not None:
                    recovered.append((entry, item))
            receipts = await self._dependencies.sandbox_runtime.reconcile_stale()
            receipts_by_lease = {receipt.lease_id: receipt for receipt in receipts}
            for entry, item in recovered:
                try:
                    await self._reconstruct(item, receipts_by_lease)
                except (EvidenceCorruptError, V2EpisodeQuarantined):
                    failure = _v2_failure(
                        "evidence",
                        "evidence_corrupt",
                        "reconcile",
                        "durable",
                    )
                    self._dependencies.evidence_repository.quarantine_corrupt_locator(
                        entry, failure
                    )
        except BaseException:
            async with self._dictionary_lock:
                if self._start_task is task:
                    self._start_task = None
            raise
        async with self._dictionary_lock:
            if self._start_task is task:
                self._started = True

    async def admit_episode_audit(
        self, spec: V2EpisodeAuditSpec
    ) -> V2EpisodeAuditAdmission:
        if type(spec) is not V2EpisodeAuditSpec:
            raise TypeError("episode audit spec must be exact")
        async with self._dictionary_lock:
            if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "availability",
                        "service_closing",
                        "retry_new_service",
                        "none",
                    )
                )
            authority = self._dependencies.fault_injection_authority
            if authority is None or not authority.authorizes_audit(spec):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "authority_audit",
                        "episode_audit_unknown_authority",
                        "none",
                        "none",
                    )
                )
            if (
                spec.episode_id in self._coordinators
                or spec.episode_id in self._episode_audit_admissions
            ):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "authority_audit",
                        "episode_audit_stale",
                        "new_episode_id",
                        "none",
                    )
                )
            admission = V2EpisodeAuditAdmission(spec, self._episode_audit_authority)
            self._episode_audit_admissions[spec.episode_id] = admission
            return admission

    def _validate_episode_audit(
        self,
        episode_id: str,
        admission: V2EpisodeAuditAdmission | None,
        coordinator: _V2EpisodeCoordinator | None,
    ) -> V2EpisodeAuditAdmission | None:
        pending = self._episode_audit_admissions.get(episode_id)
        if admission is None:
            if pending is not None or (
                coordinator is not None and coordinator.episode_audit is not None
            ):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "authority_audit",
                        "episode_audit_missing",
                        "none",
                        "none",
                    )
                )
            return None
        if (
            type(admission) is not V2EpisodeAuditAdmission
            or admission._service_authority is not self._episode_audit_authority
            or admission.spec.episode_id != episode_id
            or pending is not admission
            or (coordinator is not None and coordinator.episode_audit is not admission)
        ):
            raise V2EpisodeConflict(
                _v2_failure(
                    "authority_audit",
                    "episode_audit_mismatch",
                    "new_episode_id",
                    "none",
                )
            )
        return admission

    async def admit_fault_injection(
        self, spec: V2FaultInjectionSpec
    ) -> V2FaultInjectionAdmission:
        if type(spec) is not V2FaultInjectionSpec:
            raise TypeError("fault injection spec must be exact")
        async with self._dictionary_lock:
            if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "availability",
                        "service_closing",
                        "retry_new_service",
                        "none",
                    )
                )
            authority = self._dependencies.fault_injection_authority
            if authority is None or not authority.authorizes_fault(spec):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "fault_injection",
                        "fault_injection_unknown_authority",
                        "none",
                        "none",
                    )
                )
            if (
                spec.episode_id in self._coordinators
                or spec.episode_id in self._fault_injection_admissions
            ):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "fault_injection",
                        "fault_injection_stale",
                        "new_episode_id",
                        "none",
                    )
                )
            admission = V2FaultInjectionAdmission(spec, self._fault_injection_authority)
            self._fault_injection_admissions[spec.episode_id] = admission
            return admission

    def _validate_fault_injection(
        self,
        episode_id: str,
        admission: V2FaultInjectionAdmission | None,
        coordinator: _V2EpisodeCoordinator | None,
    ) -> V2FaultInjectionAdmission | None:
        pending = self._fault_injection_admissions.get(episode_id)
        if admission is None:
            if pending is not None or (
                coordinator is not None and coordinator.fault_injection is not None
            ):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "fault_injection",
                        "fault_injection_missing",
                        "none",
                        "none",
                    )
                )
            return None
        if (
            type(admission) is not V2FaultInjectionAdmission
            or admission._service_authority is not self._fault_injection_authority
            or admission.spec.episode_id != episode_id
            or pending is not admission
            or (
                coordinator is not None and coordinator.fault_injection is not admission
            )
        ):
            raise V2EpisodeConflict(
                _v2_failure(
                    "fault_injection",
                    "fault_injection_mismatch",
                    "new_episode_id",
                    "none",
                )
            )
        return admission

    def _observe_episode_authority(self, coordinator: _V2EpisodeCoordinator) -> None:
        admission = coordinator.episode_audit
        if admission is None or coordinator.episode_audit_observed:
            return
        authority = self._dependencies.fault_injection_authority
        if authority is None:
            raise V2EpisodeConflict(
                _v2_failure(
                    "authority_audit",
                    "episode_audit_monitor_missing",
                    "none",
                    "none",
                )
            )
        event = authority.read_episode_canary(
            actor_episode_id=coordinator.request.episode_id,
            authority_episode_id=admission.spec.episode_id,
        )
        if (
            event.authority_ref != admission.spec.authority_ref
            or event.canary != admission.spec.canary
        ):
            raise V2EpisodeConflict(
                _v2_failure(
                    "authority_audit",
                    "episode_audit_monitor_mismatch",
                    "none",
                    "none",
                )
            )
        coordinator.episode_audit_observed = True

    def _authority_access_events(
        self, coordinator: _V2EpisodeCoordinator
    ) -> tuple[AuthorityAccessEventV2, ...]:
        if coordinator.episode_audit is None:
            return ()
        authority = self._dependencies.fault_injection_authority
        if authority is None or not coordinator.episode_audit_observed:
            raise V2EpisodeConflict(
                _v2_failure(
                    "authority_audit",
                    "episode_audit_monitor_missing",
                    "none",
                    "none",
                )
            )
        events = authority.access_events(coordinator.request.episode_id)
        if not events:
            raise V2EpisodeConflict(
                _v2_failure(
                    "authority_audit",
                    "episode_audit_observation_missing",
                    "none",
                    "none",
                )
            )
        return events

    @staticmethod
    def _raise_fault_injection(
        coordinator: _V2EpisodeCoordinator,
        boundary: V2FaultBoundary,
    ) -> None:
        admission = coordinator.fault_injection
        if (
            admission is not None
            and not coordinator.fault_injection_consumed
            and admission.spec.boundary is boundary
        ):
            coordinator.fault_injection_consumed = True
            raise V2FaultInjectionError(admission)

    async def create(
        self,
        request: ResolveEpisodeRequest,
        *,
        fault_injection: V2FaultInjectionAdmission | None = None,
        episode_audit: V2EpisodeAuditAdmission | None = None,
    ) -> V2OperationResult[V2CreateResult]:
        if type(request) is not ResolveEpisodeRequest:
            raise TypeError("request must be an exact ResolveEpisodeRequest")
        # Admission is fenced again while holding the coordinator dictionary
        # lock below.  The unlocked check is only a fast rejection path.
        if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
            raise V2EpisodeUnavailable(
                _v2_failure(
                    "availability", "service_closing", "retry_new_service", "none"
                )
            )
        fingerprint = _v2_fingerprint(
            "bb.rl.episode-create-fingerprint.v1",
            {"request": request.model_dump(mode="json")},
        )
        async with self._dictionary_lock:
            if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "availability",
                        "service_closing",
                        "retry_new_service",
                        "none",
                    )
                )
            coordinator = self._coordinators.get(request.episode_id)
            if (
                coordinator is not None
                and coordinator.create_fingerprint != fingerprint
            ):
                raise V2EpisodeConflict(
                    _v2_failure(
                        "conflict",
                        "create_fingerprint_mismatch",
                        "new_episode_id",
                        "none",
                    )
                )
            task = coordinator.create_task if coordinator is not None else None
            self._validate_fault_injection(
                request.episode_id, fault_injection, coordinator
            )
            self._validate_episode_audit(request.episode_id, episode_audit, coordinator)
        if coordinator is not None:
            if task is None:
                if coordinator.create_result is None:
                    raise V2EpisodeUnavailable(
                        _v2_failure(
                            "lifecycle",
                            "create_result_missing",
                            "reconcile",
                            "accepted",
                        )
                    )
                return V2OperationResult(
                    coordinator.create_result, V2OperationDisposition.CACHED
                )
            result = await asyncio.shield(task)
            return V2OperationResult(result, V2OperationDisposition.CACHED)
        try:
            recovered = self._dependencies.evidence_repository.recover(
                request.episode_id
            )
        except EvidenceCorruptError as exc:
            raise V2EpisodeQuarantined(
                _v2_failure(
                    "evidence",
                    "evidence_corrupt",
                    "reconcile",
                    "durable",
                )
            ) from exc
        if recovered is not None:
            if fault_injection is not None or episode_audit is not None:
                async with self._dictionary_lock:
                    retired_fault = self._fault_injection_admissions.pop(
                        request.episode_id, None
                    )
                    retired_audit = self._episode_audit_admissions.pop(
                        request.episode_id, None
                    )
                if fault_injection is not None:
                    code = (
                        "fault_injection_stale"
                        if retired_fault is fault_injection
                        else "fault_injection_mismatch"
                    )
                    raise V2EpisodeConflict(
                        _v2_failure(
                            "fault_injection",
                            code,
                            "new_episode_id",
                            "durable",
                        )
                    )
                code = (
                    "episode_audit_stale"
                    if retired_audit is episode_audit
                    else "episode_audit_mismatch"
                )
                raise V2EpisodeConflict(
                    _v2_failure(
                        "authority_audit",
                        code,
                        "new_episode_id",
                        "durable",
                    )
                )
            return await self._recover_create(request, fingerprint, recovered)
        owns_create = False
        async with self._dictionary_lock:
            if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "availability",
                        "service_closing",
                        "retry_new_service",
                        "none",
                    )
                )
            coordinator = self._coordinators.get(request.episode_id)
            admitted_fault = self._validate_fault_injection(
                request.episode_id, fault_injection, coordinator
            )
            admitted_audit = self._validate_episode_audit(
                request.episode_id, episode_audit, coordinator
            )
            if coordinator is None:
                coordinator = _V2EpisodeCoordinator(
                    request=request,
                    create_fingerprint=fingerprint,
                    fault_injection=admitted_fault,
                    episode_audit=admitted_audit,
                )
                self._coordinators[request.episode_id] = coordinator
                if admitted_fault is not None:
                    if (
                        self._fault_injection_admissions.pop(request.episode_id, None)
                        is not admitted_fault
                    ):
                        raise V2EpisodeConflict(
                            _v2_failure(
                                "fault_injection",
                                "fault_injection_stale",
                                "new_episode_id",
                                "none",
                            )
                        )
                if admitted_audit is not None:
                    if (
                        self._episode_audit_admissions.pop(request.episode_id, None)
                        is not admitted_audit
                    ):
                        raise V2EpisodeConflict(
                            _v2_failure(
                                "authority_audit",
                                "episode_audit_stale",
                                "new_episode_id",
                                "none",
                            )
                        )
                coordinator.create_task = self._register_operation_task(
                    coordinator,
                    "create",
                    self._create_fresh(coordinator),
                )
                owns_create = True
            elif coordinator.create_fingerprint != fingerprint:
                raise V2EpisodeConflict(
                    _v2_failure(
                        "conflict",
                        "create_fingerprint_mismatch",
                        "new_episode_id",
                        "none",
                    )
                )
            task = coordinator.create_task
        if task is None:
            if coordinator.create_result is None:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "lifecycle", "create_result_missing", "reconcile", "accepted"
                    )
                )
            return V2OperationResult(
                coordinator.create_result, V2OperationDisposition.CACHED
            )
        result = await asyncio.shield(task)
        return V2OperationResult(
            result,
            V2OperationDisposition.FRESH
            if owns_create
            else V2OperationDisposition.CACHED,
        )

    async def _create_fresh(self, coordinator: _V2EpisodeCoordinator) -> V2CreateResult:
        request = coordinator.request
        binding: PolicyRuntimeBinding | None = None
        try:
            resolved = self._dependencies.config_runtime.resolve_episode(request)
            if (
                type(resolved) is not ResolvedEpisodePlan
                or resolved.episode_id != request.episode_id
            ):
                raise ValueError("resolver returned a non-canonical episode plan")
            evidence_policy = next(
                record
                for record in self._dependencies.sandbox_runtime.registries.evidence_policies
                if record.policy == resolved.effective_plan.evidence
            )
            retention_policy = next(
                record
                for record in self._dependencies.sandbox_runtime.registries.retention_policies
                if record.grant.policy == resolved.effective_plan.retention
            )
            authority_plan = self._dependencies.evidence_authority.validate_plan(
                resolved.effective_plan,
                evidence_policy,
                retention_policy,
            )
            open_request = RunnerOpenRequest(
                request.episode_id, resolved.effective_plan
            )
            client = await self._dependencies.policy_client_resolver.resolve(
                request.policy_binding,
                episode_id=request.episode_id,
                effective_plan_digest=open_request.effective_plan_digest,
            )
            try:
                binding = PolicyRuntimeBinding(open_request, client)
            except BaseException as exc:
                try:
                    await client.close()
                except BaseException as close_error:
                    raise BaseExceptionGroup(
                        "policy binding construction and client close failed",
                        [exc, close_error],
                    ) from exc
                raise
            runner = self._dependencies.runner_registry.resolve(
                resolved.effective_plan.runner.adapter_id,
                resolved.effective_plan.runner.runtime_abi,
            )
            workspace_request = WorkspaceOpenRequest(
                request.episode_id, resolved.effective_plan
            )
            sandbox_plan = build_sandbox_execution_plan(
                workspace_request,
                self._dependencies.sandbox_runtime.registries,
                self._dependencies.sandbox_runtime.installed_authorities,
            )
            coordinator.resolved_plan = resolved
            coordinator.resolved_subject_digest = resolved.subject_digest
            coordinator.binding = binding
            coordinator.evidence_authority_plan = authority_plan
            coordinator.runner = runner
            await self._transition(
                coordinator,
                EpisodeLifecycleState.ACCEPTED,
                "accepted",
                from_state=None,
            )
        except BaseException as exc:
            close_error: BaseException | None = None
            if binding is not None and coordinator.last_event is None:
                coordinator.binding = binding
                close_error = await self._close_unowned_binding(coordinator)
            await self._discard_unaccepted(coordinator)
            if close_error is not None:
                raise BaseExceptionGroup(
                    "pre-admission rejection and policy binding close failed",
                    [exc, close_error],
                ) from exc
            if isinstance(exc, BaseExceptionGroup):
                raise exc
            raise V2EpisodeRejected(
                _failure_from_exception(exc, "pre_admission")
            ) from exc
        coordinator.create_result = V2CreateResult(
            request.episode_id,
            coordinator.create_fingerprint,
            EpisodeLifecycleState.ACCEPTED,
            open_request.effective_plan_digest,
            resolved.selection_record_ref,
            resolved.effective_plan_ref,
            binding.binding_digest,
            resolved.selection_commit,
            resolved.base_receipt_digest,
            resolved.final_receipt_digest,
            resolved.policy_capability_observation_digest,
            _sandbox_preflight_identity(sandbox_plan),
        )
        try:
            async with coordinator.lock:
                if (
                    coordinator.cancel_event.is_set()
                    and coordinator.state is not EpisodeLifecycleState.CANCEL_REQUESTED
                ):
                    self._transition_now(
                        coordinator,
                        EpisodeLifecycleState.CANCEL_REQUESTED,
                        "cancellation_requested",
                    )
                _V2CancellationProbe(coordinator).raise_if_cancelled(
                    "before_allocation"
                )
                self._transition_now(
                    coordinator,
                    EpisodeLifecycleState.ALLOCATING,
                    "allocation_started",
                )
                self._observe_episode_authority(coordinator)
                self._raise_fault_injection(coordinator, V2FaultBoundary.PRE_ALLOCATION)
            lease = await self._dependencies.sandbox_runtime.open(workspace_request)
        except BaseException as exc:
            failure = _failure_from_exception(exc, "allocation")
            coordinator.primary_disposition = (
                exc.admission.spec.primary_disposition
                if isinstance(exc, V2FaultInjectionError)
                else (
                    EpisodePrimaryDisposition.CANCELLED
                    if isinstance(exc, asyncio.CancelledError)
                    else EpisodePrimaryDisposition.FAILED
                )
            )
            await self._close_unowned_binding(coordinator)
            binding_close_failure = coordinator.session_close_failure
            async with coordinator.lock:
                transition_kind = (
                    "cancellation_won"
                    if coordinator.state is EpisodeLifecycleState.CANCEL_REQUESTED
                    else "allocation_failed"
                )
                self._transition_now(
                    coordinator,
                    EpisodeLifecycleState.CLOSING,
                    transition_kind,
                    primary=failure,
                    cleanup=binding_close_failure,
                )
            coordinator.primary_failure = failure
            self._publish_failed_completed(coordinator, failure)
            if isinstance(exc, V2FaultInjectionError):
                await self._close_owner(coordinator, failure)
            elif isinstance(exc, SandboxFault):
                await self._finish_cleanup(coordinator, exc.cleanup_receipt, failure)
            else:
                await self._close_owner(coordinator, failure)
            raise V2EpisodeUnavailable(failure) from exc
        post_open_cancelled = False
        try:
            async with coordinator.lock:
                coordinator.lease = lease
                coordinator.primary_lease_id = lease.lease_id
                if (
                    coordinator.cancel_event.is_set()
                    or coordinator.state is not EpisodeLifecycleState.ALLOCATING
                ):
                    post_open_cancelled = True
                else:
                    self._transition_now(
                        coordinator,
                        EpisodeLifecycleState.READY,
                        "workspace_ready",
                    )
        except BaseException as exc:
            if (
                coordinator.last_event is None
                or coordinator.last_event.primary_lease_id
                != coordinator.primary_lease_id
            ):
                coordinator.primary_lease_id = (
                    coordinator.last_event.primary_lease_id
                    if coordinator.last_event is not None
                    else None
                )
            failure = _failure_from_exception(exc, "lifecycle_transition")
            coordinator.primary_disposition = EpisodePrimaryDisposition.FAILED
            await self._transition_to_closing_and_cleanup(
                coordinator, "workspace_ready_failed", failure
            )
            raise
        if post_open_cancelled:
            failure = _v2_failure(
                "cancellation",
                "process_interrupted",
                "reconcile",
                "allocation",
                lease_id=coordinator.primary_lease_id,
            )
            coordinator.primary_disposition = EpisodePrimaryDisposition.CANCELLED
            await self._close_unowned_binding(coordinator)
            await self._transition_to_closing_and_cleanup(
                coordinator,
                "cancellation_won",
                failure,
                cleanup=coordinator.session_close_failure,
            )
            raise V2EpisodeUnavailable(failure)
        result = V2CreateResult(
            request.episode_id,
            coordinator.create_fingerprint,
            EpisodeLifecycleState.READY,
            open_request.effective_plan_digest,
            resolved.selection_record_ref,
            resolved.effective_plan_ref,
            binding.binding_digest,
            resolved.selection_commit,
            resolved.base_receipt_digest,
            resolved.final_receipt_digest,
            resolved.policy_capability_observation_digest,
            _sandbox_preflight_identity(sandbox_plan),
        )
        coordinator.create_result = result
        return result

    async def run(
        self,
        episode_id: str,
        *,
        create_fingerprint: str,
        task_input: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> V2OperationResult[V2RunResult]:
        run_request = ConductorRunRequest(task_input, context)
        fingerprint = _v2_fingerprint(
            "bb.rl.episode-run-fingerprint.v1",
            {
                "episode_id": episode_id,
                "create_fingerprint": create_fingerprint,
                "request": {
                    "task_input": dict(run_request.task_input),
                    "context": dict(run_request.context),
                },
            },
        )
        async with self._dictionary_lock:
            if self._lifecycle_state is not _ServiceLifecycleState.OPEN:
                raise V2EpisodeUnavailable(
                    _v2_failure(
                        "availability",
                        "service_closing",
                        "retry_new_service",
                        "none",
                    )
                )
            coordinator = self._coordinators.get(episode_id)
            if coordinator is None:
                raise V2EpisodeNotFound(
                    _v2_failure("lookup", "episode_not_found", "none", "none")
                )
            if create_fingerprint != coordinator.create_fingerprint:
                raise V2EpisodeConflict(
                    _v2_failure(
                        "conflict",
                        "create_fingerprint_mismatch",
                        "new_episode_id",
                        "none",
                    )
                )
            async with coordinator.lock:
                if coordinator.run_fingerprint not in {None, fingerprint}:
                    raise V2EpisodeConflict(
                        _v2_failure(
                            "conflict",
                            "run_fingerprint_mismatch",
                            "new_episode_id",
                            "none",
                        )
                    )
                if coordinator.run_result is not None:
                    return V2OperationResult(
                        coordinator.run_result,
                        V2OperationDisposition.CACHED,
                    )
                if coordinator.run_task is None:
                    close_active = (
                        coordinator.close_task is not None
                        and not coordinator.close_task.done()
                    )
                    if (
                        coordinator.state is not EpisodeLifecycleState.READY
                        or coordinator.cancel_event.is_set()
                        or close_active
                        or coordinator.closed is not None
                        or coordinator.terminal_committed
                    ):
                        raise V2EpisodeConflict(
                            _v2_failure(
                                "lifecycle",
                                "episode_not_ready",
                                "none",
                                "accepted",
                            )
                        )
                    coordinator.run_fingerprint = fingerprint
                    coordinator.run_task = self._register_operation_task(
                        coordinator,
                        "run",
                        self._run_fresh(coordinator, run_request),
                    )
                    fresh = True
                else:
                    fresh = False
                task = coordinator.run_task
        result = await asyncio.shield(task)
        return V2OperationResult(
            result,
            V2OperationDisposition.FRESH if fresh else V2OperationDisposition.CACHED,
        )

    async def _run_fresh(
        self,
        coordinator: _V2EpisodeCoordinator,
        request: ConductorRunRequest,
    ) -> V2RunResult:
        if coordinator.state is not EpisodeLifecycleState.READY:
            raise V2EpisodeConflict(
                _v2_failure("lifecycle", "episode_not_ready", "none", "accepted")
            )
        if (
            coordinator.runner is None
            or coordinator.binding is None
            or coordinator.lease is None
        ):
            raise V2EpisodeUnavailable(
                _v2_failure(
                    "lifecycle", "episode_dependencies_missing", "reconcile", "accepted"
                )
            )
        sink = _V2DurableRunnerEventSink(
            self._dependencies.evidence_repository,
            coordinator,
            coordinator.resolved_plan.effective_plan.canonical_digest(),
        )
        probe = _V2CancellationProbe(coordinator)
        try:
            await self._transition(
                coordinator, EpisodeLifecycleState.RUNNING, "run_started"
            )
        except BaseException as exc:
            failure = _failure_from_exception(exc, "lifecycle_transition")
            coordinator.primary_disposition = EpisodePrimaryDisposition.FAILED
            coordinator.primary_failure = failure
            await self._cleanup_after_transition_failure(coordinator, failure)
            raise
        runner_result: RunnerResult | None = None
        primary_error: BaseException | None = None
        try:
            self._raise_fault_injection(coordinator, V2FaultBoundary.POST_ALLOCATION)
            probe.raise_if_cancelled("before_runner_open")
            runner_request = _materialize_runner_request(
                request,
                coordinator.resolved_plan.effective_plan,
                episode_id=coordinator.request.episode_id,
                effective_plan_digest=(
                    coordinator.resolved_plan.effective_plan.canonical_digest()
                ),
            )
            session = await coordinator.runner.open(
                RunnerOpenRequest(
                    coordinator.request.episode_id,
                    coordinator.resolved_plan.effective_plan,
                ),
                policy=coordinator.binding,
                workspace=coordinator.lease.runner_workspace,
                cancellation=probe,
                events=sink,
            )
            coordinator.session = session
            runner_result = await session.run(runner_request)
        except (asyncio.CancelledError, RunnerCancelled) as exc:
            primary_error = exc
            coordinator.primary_disposition = EpisodePrimaryDisposition.CANCELLED
        except BaseException as exc:
            primary_error = exc
            coordinator.primary_disposition = (
                exc.admission.spec.primary_disposition
                if isinstance(exc, V2FaultInjectionError)
                else EpisodePrimaryDisposition.FAILED
            )
        finally:
            if coordinator.session is not None:
                close_cancellation, close_error = await self._close_owned_session(
                    coordinator
                )
                if close_error is not None:
                    coordinator.session_close_failure = _failure_from_exception(
                        close_error, "session_close"
                    )
                if primary_error is None:
                    if close_cancellation is not None:
                        primary_error = close_cancellation
                        coordinator.primary_disposition = (
                            EpisodePrimaryDisposition.CANCELLED
                        )
                    elif close_error is not None:
                        primary_error = close_error
                        coordinator.primary_disposition = (
                            EpisodePrimaryDisposition.FAILED
                        )
            else:
                binding_close_error = await self._close_unowned_binding(coordinator)
                if binding_close_error is not None and primary_error is None:
                    primary_error = binding_close_error
                    coordinator.primary_disposition = EpisodePrimaryDisposition.FAILED
        if primary_error is not None or runner_result is None:
            failure = _failure_from_exception(
                primary_error or RuntimeError("runner result missing"), "runner"
            )
            await self._transition_to_closing_and_cleanup(
                coordinator,
                "run_failed",
                failure,
                cleanup=coordinator.session_close_failure,
            )
            result = V2RunResult(
                coordinator.request.episode_id,
                coordinator.create_fingerprint,
                coordinator.run_fingerprint or "",
                coordinator.primary_disposition or EpisodePrimaryDisposition.FAILED,
                None,
                None,
                0,
                coordinator.completed.envelope_ref if coordinator.completed else None,
                coordinator.closed.envelope_ref if coordinator.closed else None,
                result_ref=(
                    coordinator.completed.result_ref
                    if coordinator.completed is not None
                    else None
                ),
                evidence_manifest_ref=(
                    coordinator.completed.evidence_manifest_ref
                    if coordinator.completed is not None
                    else None
                ),
                evidence_root=(
                    coordinator.completed.evidence_root
                    if coordinator.completed is not None
                    else None
                ),
                artifact_manifest_ref=(
                    coordinator.completed.artifact_manifest_ref
                    if coordinator.completed is not None
                    else None
                ),
                primary_measurement_digest=(
                    coordinator.completed.primary_measurement_digest
                    if coordinator.completed is not None
                    else None
                ),
                verifier_measurement_digest=(
                    coordinator.completed.verifier_measurement_digest
                    if coordinator.completed is not None
                    else None
                ),
                verifier_result_digest=(
                    coordinator.completed.verifier_result_digest
                    if coordinator.completed is not None
                    else None
                ),
            )
            coordinator.run_result = result
            return result
        cancelled_at_fence = False
        async with coordinator.lock:
            if coordinator.cancel_event.is_set():
                coordinator.primary_disposition = EpisodePrimaryDisposition.CANCELLED
                cancelled_at_fence = True
            else:
                coordinator.terminal_committed = True
        if cancelled_at_fence:
            failure = _v2_failure(
                "cancellation", "cancelled_before_terminal_commit", "none", "runner"
            )
            await self._transition_to_closing_and_cleanup(
                coordinator, "cancellation_won", failure
            )
            result = V2RunResult(
                coordinator.request.episode_id,
                coordinator.create_fingerprint,
                coordinator.run_fingerprint or "",
                EpisodePrimaryDisposition.CANCELLED,
                None,
                None,
                runner_result.turn_count,
                coordinator.completed.envelope_ref if coordinator.completed else None,
                coordinator.closed.envelope_ref if coordinator.closed else None,
                result_ref=(
                    coordinator.completed.result_ref
                    if coordinator.completed is not None
                    else None
                ),
                evidence_manifest_ref=(
                    coordinator.completed.evidence_manifest_ref
                    if coordinator.completed is not None
                    else None
                ),
                evidence_root=(
                    coordinator.completed.evidence_root
                    if coordinator.completed is not None
                    else None
                ),
                artifact_manifest_ref=(
                    coordinator.completed.artifact_manifest_ref
                    if coordinator.completed is not None
                    else None
                ),
                primary_measurement_digest=(
                    coordinator.completed.primary_measurement_digest
                    if coordinator.completed is not None
                    else None
                ),
                verifier_measurement_digest=(
                    coordinator.completed.verifier_measurement_digest
                    if coordinator.completed is not None
                    else None
                ),
                verifier_result_digest=(
                    coordinator.completed.verifier_result_digest
                    if coordinator.completed is not None
                    else None
                ),
            )
            coordinator.run_result = result
            return result
        try:
            await self._transition(
                coordinator, EpisodeLifecycleState.VERIFYING, "runner_terminal"
            )
        except BaseException as exc:
            failure = _failure_from_exception(exc, "lifecycle_transition")
            coordinator.primary_disposition = EpisodePrimaryDisposition.FAILED
            coordinator.primary_failure = failure
            await self._cleanup_after_transition_failure(coordinator, failure)
            raise
        verifier: VerifierWorkspaceLease | None = None
        verification_error: BaseException | None = None
        try:
            snapshot = await coordinator.lease.seal_for_verifier()
            coordinator.verifier_snapshot = snapshot
            raw_workspace_diff = coordinator.lease.sealed_workspace_diff()
            if raw_workspace_diff is not None:
                expected_keys = {
                    "returncode", "stdout", "stderr", "base_commit",
                    "git_executable_digest", "patch_digest",
                    "snapshot_root_digest",
                }
                patch_bytes = raw_workspace_diff.get("stdout", "").encode("utf-8")
                if (
                    set(raw_workspace_diff) != expected_keys
                    or type(raw_workspace_diff["returncode"]) is not int
                    or type(raw_workspace_diff["stdout"]) is not str
                    or type(raw_workspace_diff["stderr"]) is not str
                    or type(raw_workspace_diff["base_commit"]) is not str
                    or type(raw_workspace_diff["git_executable_digest"]) is not str
                    or type(raw_workspace_diff["patch_digest"]) is not str
                    or type(raw_workspace_diff["snapshot_root_digest"]) is not str
                    or raw_workspace_diff["returncode"] != 0
                    or raw_workspace_diff["stderr"] != ""
                    or raw_workspace_diff["snapshot_root_digest"] != snapshot.root_digest
                    or raw_workspace_diff["patch_digest"]
                    != "sha256:" + hashlib.sha256(patch_bytes).hexdigest()
                ):
                    raise RuntimeError("canonical sealed workspace diff failed")
                coordinator.workspace_diff = MappingProxyType(dict(raw_workspace_diff))
            probe.raise_if_cancelled("before_verifier_open")
            verifier = await self._dependencies.sandbox_runtime.open_verifier(
                coordinator.lease, snapshot
            )
            coordinator.verifier_lease_id = getattr(verifier, "lease_id", None)
            verifier_result = await verifier.execute()
            coordinator.verifier_result = verifier_result
        except BaseException as exc:
            verification_error = exc
        finally:
            if verifier is not None:
                if coordinator.verifier_cleanup_task is None:
                    coordinator.verifier_cleanup_task = asyncio.create_task(
                        verifier.close(),
                        name=(f"bb-v2-verifier-close:{coordinator.request.episode_id}"),
                    )
                (
                    receipt,
                    cleanup_cancellation,
                    close_error,
                ) = await _observe_owned_task(coordinator.verifier_cleanup_task)
                if receipt is not None:
                    coordinator.verifier_cleanup_receipt = receipt
                if close_error is not None:
                    coordinator.verifier_cleanup_failure = _failure_from_exception(
                        close_error, "verifier_cleanup"
                    )
                if cleanup_cancellation is not None and verification_error is None:
                    verification_error = cleanup_cancellation
        verifier_receipt = coordinator.verifier_cleanup_receipt
        verifier_cleanup_lease_mismatch = (
            verifier_receipt is not None
            and verifier_receipt.lease_id != coordinator.verifier_lease_id
        )
        verifier_cleanup_bad = verifier is not None and (
            verifier_receipt is None
            or verifier_cleanup_lease_mismatch
            or not _cleanup_released(
                verifier_receipt,
                required={"runtime", "workspace", "snapshot", "lease_record"},
            )
        )
        if verifier_cleanup_bad and coordinator.verifier_cleanup_failure is None:
            coordinator.verifier_cleanup_failure = _v2_failure(
                "cleanup",
                (
                    "verifier_cleanup_lease_mismatch"
                    if verifier_cleanup_lease_mismatch
                    else "verifier_cleanup_not_released"
                ),
                "reconcile",
                "verifier_cleanup",
                lease_id=coordinator.verifier_lease_id,
            )
        if verification_error is not None or verifier_cleanup_bad:
            failure = (
                _failure_from_exception(verification_error, "verifier")
                if verification_error is not None
                else coordinator.verifier_cleanup_failure
                or _v2_failure(
                    "cleanup",
                    "verifier_cleanup_not_released",
                    "reconcile",
                    "verifier_cleanup",
                    lease_id=coordinator.verifier_lease_id,
                )
            )
            coordinator.primary_disposition = EpisodePrimaryDisposition.FAILED
            coordinator.primary_failure = failure
            await self._transition_to_closing_and_cleanup(
                coordinator,
                "verification_failed",
                failure,
                cleanup=coordinator.verifier_cleanup_failure,
            )
            result = V2RunResult(
                coordinator.request.episode_id,
                coordinator.create_fingerprint,
                coordinator.run_fingerprint or "",
                EpisodePrimaryDisposition.FAILED,
                None,
                None,
                runner_result.turn_count,
                coordinator.completed.envelope_ref if coordinator.completed else None,
                coordinator.closed.envelope_ref if coordinator.closed else None,
                result_ref=(
                    coordinator.completed.result_ref
                    if coordinator.completed is not None
                    else None
                ),
                evidence_manifest_ref=(
                    coordinator.completed.evidence_manifest_ref
                    if coordinator.completed is not None
                    else None
                ),
                evidence_root=(
                    coordinator.completed.evidence_root
                    if coordinator.completed is not None
                    else None
                ),
                artifact_manifest_ref=(
                    coordinator.completed.artifact_manifest_ref
                    if coordinator.completed is not None
                    else None
                ),
                primary_measurement_digest=(
                    coordinator.completed.primary_measurement_digest
                    if coordinator.completed is not None
                    else None
                ),
                verifier_measurement_digest=(
                    coordinator.completed.verifier_measurement_digest
                    if coordinator.completed is not None
                    else None
                ),
                verifier_result_digest=(
                    coordinator.completed.verifier_result_digest
                    if coordinator.completed is not None
                    else None
                ),
            )
            coordinator.run_result = result
            return result
        try:
            completed_event = await self._transition(
                coordinator, EpisodeLifecycleState.COMPLETED, "completed"
            )
            completed = self._publish_completed(
                coordinator,
                runner_result,
                snapshot,
                verifier,
                verifier_result,
                completed_event,
            )
        except BaseException as exc:
            failure = _failure_from_exception(exc, "evidence_publication")
            coordinator.primary_disposition = EpisodePrimaryDisposition.FAILED
            await self._transition_to_closing_and_cleanup(
                coordinator, "cleanup_started", failure
            )
            raise
        coordinator.primary_disposition = EpisodePrimaryDisposition.SUCCEEDED
        coordinator.completed = completed
        try:
            await self._transition(
                coordinator, EpisodeLifecycleState.CLOSING, "cleanup_started"
            )
        finally:
            await self._close_owner(coordinator, None)
        result = V2RunResult(
            coordinator.request.episode_id,
            coordinator.create_fingerprint,
            coordinator.run_fingerprint or "",
            EpisodePrimaryDisposition.SUCCEEDED,
            MappingProxyType(dict(runner_result.response)),
            runner_result.termination.value,
            runner_result.turn_count,
            completed.envelope_ref,
            coordinator.closed.envelope_ref if coordinator.closed else None,
            result_ref=completed.result_ref,
            evidence_manifest_ref=completed.evidence_manifest_ref,
            evidence_root=completed.evidence_root,
            reward=verifier_result.get("reward"),
            reward_components=MappingProxyType(
                dict(verifier_result.get("reward_components", {}))
            ),
            artifact_manifest_ref=completed.artifact_manifest_ref,
            primary_measurement_digest=completed.primary_measurement_digest,
            verifier_measurement_digest=completed.verifier_measurement_digest,
            verifier_result_digest=completed.verifier_result_digest,
            workspace_diff=coordinator.workspace_diff,
        )
        coordinator.run_result = result
        return result

    async def cancel(self, episode_id: str, reason: str) -> V2CancellationResult:
        normalized = " ".join(reason.split())
        if not normalized or len(normalized) > 256:
            raise ValueError("cancellation reason must be 1..256 normalized characters")
        coordinator = await self._coordinator(episode_id)
        first_transition_won = False
        cancel_owner: asyncio.Task[Any] | None = None
        async with coordinator.lock:
            stored_reason = coordinator.cancel_reason or normalized
            if coordinator.terminal_committed or coordinator.state in {
                EpisodeLifecycleState.CLOSING,
                EpisodeLifecycleState.COMPLETED,
                EpisodeLifecycleState.CLOSED,
                EpisodeLifecycleState.QUARANTINED,
            }:
                return V2CancellationResult(
                    episode_id, False, stored_reason, coordinator.state
                )
            if not coordinator.cancel_event.is_set():
                coordinator.cancel_reason = normalized
                coordinator.cancel_fingerprint = _v2_cancel_fingerprint(
                    episode_id,
                    coordinator.create_fingerprint,
                    normalized,
                )
                try:
                    if coordinator.last_event is not None:
                        self._transition_now(
                            coordinator,
                            EpisodeLifecycleState.CANCEL_REQUESTED,
                            "cancellation_requested",
                        )
                        first_transition_won = True
                except BaseException:
                    coordinator.cancel_reason = ""
                    coordinator.cancel_fingerprint = None
                    raise
                coordinator.cancel_event.set()
            if first_transition_won and not coordinator.owner_cancel_sent:
                if (
                    coordinator.lease is None
                    and coordinator.state is EpisodeLifecycleState.CANCEL_REQUESTED
                    and coordinator.last_event is not None
                    and coordinator.last_event.from_state
                    == EpisodeLifecycleState.ALLOCATING.value
                    and coordinator.create_task is not None
                    and not coordinator.create_task.done()
                ):
                    cancel_owner = coordinator.create_task
                if cancel_owner is not None:
                    coordinator.owner_cancel_sent = True
            session = coordinator.session if first_transition_won else None
            stored_reason = coordinator.cancel_reason
        if session is not None:
            await session.cancel(stored_reason)
        if cancel_owner is not None:
            cancel_owner.cancel()
        return V2CancellationResult(episode_id, True, stored_reason, coordinator.state)

    async def close_episode(self, episode_id: str) -> V2OperationResult[V2CloseResult]:
        coordinator = await self._coordinator(episode_id)
        await self.cancel(episode_id, "episode close requested")
        cached: V2CloseResult | None = None
        async with coordinator.lock:
            if coordinator.close_task is not None:
                task = coordinator.close_task
                fresh = False
            elif coordinator.closed is not None:
                cached = V2CloseResult(
                    episode_id,
                    EpisodeLifecycleState.CLOSED,
                    EpisodeCleanupDisposition.RELEASED,
                    coordinator.closed.envelope_ref,
                )
                task = None
                fresh = False
            else:
                coordinator.close_task = asyncio.create_task(
                    self._close_after_active_owner(coordinator)
                )
                task = coordinator.close_task
                fresh = True
        if cached is not None:
            operation_failures = await self._coordinator_operation_failures(coordinator)
            if operation_failures:
                raise BaseExceptionGroup(
                    "episode operation and close failed",
                    operation_failures,
                )
            return V2OperationResult(cached, V2OperationDisposition.CACHED)
        if task is None:
            raise AssertionError("episode close owner is missing")
        result = await asyncio.shield(task)
        return V2OperationResult(
            result,
            V2OperationDisposition.FRESH if fresh else V2OperationDisposition.CACHED,
        )

    async def _close_after_active_owner(
        self, coordinator: _V2EpisodeCoordinator
    ) -> V2CloseResult:
        operation_failures = await self._coordinator_operation_failures(coordinator)
        close_failure: BaseException | None = None
        result: V2CloseResult | None = None
        try:
            async with coordinator.lock:
                if coordinator.state is EpisodeLifecycleState.COMPLETED:
                    self._transition_now(
                        coordinator,
                        EpisodeLifecycleState.CLOSING,
                        "cleanup_started",
                    )
            result = await self._close_owner(
                coordinator,
                coordinator.primary_failure,
            )
        except BaseException as exc:
            close_failure = exc
        failures = [
            *operation_failures,
            *(() if close_failure is None else (close_failure,)),
        ]
        if failures:
            raise BaseExceptionGroup(
                "episode operation and close failed",
                failures,
            )
        if result is None:
            raise AssertionError("episode close owner returned no result")
        return result

    async def get_state(self, episode_id: str) -> V2EpisodeState:
        coordinator = await self._coordinator(episode_id)
        event = coordinator.last_event
        if event is None:
            raise V2EpisodeUnavailable(
                _v2_failure("evidence", "event_head_missing", "reconcile", "accepted")
            )
        return V2EpisodeState(
            episode_id,
            coordinator.state,
            event.sequence,
            event.digest,
            coordinator.create_fingerprint,
            coordinator.run_fingerprint,
            coordinator.primary_disposition,
            coordinator.cleanup_disposition,
            coordinator.completed.envelope_ref if coordinator.completed else None,
            coordinator.closed.envelope_ref if coordinator.closed else None,
        )

    async def get_completed_envelope(
        self, episode_id: str
    ) -> CompletedEpisodeEnvelopeV2:
        recovered = self._recover_for_read(episode_id)
        if recovered.completed_envelope is None:
            raise V2EpisodeUnavailable(
                _v2_failure(
                    "lifecycle",
                    "completed_envelope_unavailable",
                    "retry_after_completion",
                    "none",
                )
            )
        return recovered.completed_envelope

    async def get_closed_envelope(self, episode_id: str) -> ClosedEpisodeEnvelopeV2:
        recovered = self._recover_for_read(episode_id)
        if recovered.closed_envelope is None:
            raise V2EpisodeUnavailable(
                _v2_failure(
                    "lifecycle",
                    "closed_envelope_unavailable",
                    "reconcile",
                    "none",
                )
            )
        return recovered.closed_envelope

    async def export_closed(
        self,
        episode_id: str,
        claims: ExportAuthorizationClaimsV2,
    ) -> ExportManifestV2:
        if type(claims) is not ExportAuthorizationClaimsV2:
            raise TypeError("claims must be an exact ExportAuthorizationClaimsV2")
        try:
            return self._dependencies.evidence_repository.export_closed_claims(
                episode_id, claims
            )
        except ExportDeniedError as exc:
            raise V2EpisodeRejected(
                _v2_failure(
                    "authorization",
                    "export_denied",
                    "new_authorization",
                    "none",
                )
            ) from exc
        except EvidenceCorruptError as exc:
            raise V2EpisodeQuarantined(
                _v2_failure("evidence", "evidence_corrupt", "reconcile", "durable")
            ) from exc

    def _recover_for_read(self, episode_id: str) -> RecoveredEpisodeV2:
        try:
            recovered = self._dependencies.evidence_repository.recover(episode_id)
        except EvidenceCorruptError as exc:
            raise V2EpisodeQuarantined(
                _v2_failure("evidence", "evidence_corrupt", "reconcile", "durable")
            ) from exc
        if recovered is None:
            raise V2EpisodeNotFound(
                _v2_failure("lookup", "episode_not_found", "none", "none")
            )
        if recovered.quarantined:
            raise V2EpisodeQuarantined(
                _v2_failure("evidence", "episode_quarantined", "reconcile", "durable")
            )
        return recovered

    async def close(self) -> None:
        async with self._dictionary_lock:
            task = self._close_task
            if task is None:
                self._lifecycle_state = _ServiceLifecycleState.CLOSING
                task = asyncio.create_task(self._shutdown_owner())
                self._close_task = task
        try:
            await _await_owned_close(task)
        finally:
            if (
                task.done()
                and not task.cancelled()
                and task.exception() is not None
                and _retryable_shutdown_failure(task.exception())
            ):
                async with self._dictionary_lock:
                    if self._close_task is task:
                        self._close_task = None

    async def _shutdown_owner(self) -> None:
        errors: list[BaseException] = []
        async with self._dictionary_lock:
            start_task = self._start_task
        if start_task is not None:
            try:
                await asyncio.shield(start_task)
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                errors.append(exc)
        async with self._dictionary_lock:
            coordinators = tuple(self._coordinators.values())
        for coordinator in coordinators:
            if coordinator.state not in {
                EpisodeLifecycleState.CLOSED,
                EpisodeLifecycleState.QUARANTINED,
            }:
                try:
                    await self.cancel(
                        coordinator.request.episode_id,
                        "service shutdown",
                    )
                except BaseException as exc:
                    errors.append(exc)
                cancel_owner: asyncio.Task[Any] | None = None
                async with coordinator.lock:
                    if (
                        coordinator.run_task is not None
                        and not coordinator.run_task.done()
                        and not coordinator.owner_cancel_sent
                    ):
                        cancel_owner = coordinator.run_task
                        coordinator.owner_cancel_sent = True
                if cancel_owner is not None:
                    cancel_owner.cancel()
                    await asyncio.sleep(0)
                try:
                    await self.close_episode(coordinator.request.episode_id)
                except BaseException as exc:
                    errors.append(exc)
            else:
                errors.extend(await self._coordinator_operation_failures(coordinator))
        errors.extend(await self._drain_operation_tasks())
        try:
            sandbox_receipts = await self._dependencies.sandbox_runtime.close()
            errors.extend(
                SandboxFault(
                    RuntimeError("sandbox runtime cleanup pending"),
                    receipt,
                    (),
                )
                for receipt in sandbox_receipts
                if receipt.state
                not in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
            )
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise BaseExceptionGroup("V2 service shutdown failed", errors)
        async with self._dictionary_lock:
            self._lifecycle_state = _ServiceLifecycleState.CLOSED

    async def _coordinator(self, episode_id: str) -> _V2EpisodeCoordinator:
        async with self._dictionary_lock:
            coordinator = self._coordinators.get(episode_id)
        if coordinator is None:
            raise V2EpisodeNotFound(
                _v2_failure("lookup", "episode_not_found", "none", "none")
            )
        return coordinator

    async def _transition(
        self,
        coordinator: _V2EpisodeCoordinator,
        state: EpisodeLifecycleState,
        kind: str,
        *,
        from_state: EpisodeLifecycleState | None = None,
        primary: SafeFailureFactV2 | None = None,
        cleanup: SafeFailureFactV2 | None = None,
    ) -> LifecycleEventV2:
        return self._transition_now(
            coordinator,
            state,
            kind,
            from_state=from_state,
            primary=primary,
            cleanup=cleanup,
        )

    def _transition_now(
        self,
        coordinator: _V2EpisodeCoordinator,
        state: EpisodeLifecycleState,
        kind: str,
        *,
        from_state: EpisodeLifecycleState | None = None,
        primary: SafeFailureFactV2 | None = None,
        cleanup: SafeFailureFactV2 | None = None,
    ) -> LifecycleEventV2:
        previous = coordinator.last_event
        previous_ref = coordinator.last_event_ref
        cancellation_is_durable = (
            kind == "cancellation_requested"
            or previous is not None
            and previous.cancel_reason is not None
        )
        event = LifecycleEventV2(
            episode_id=coordinator.request.episode_id,
            sequence=0 if previous is None else previous.sequence + 1,
            previous_event_digest=None if previous is None else previous.digest,
            from_state=(
                from_state.value
                if from_state is not None
                else (None if previous is None else coordinator.state.value)
            ),
            to_state=state.value,
            event_kind=kind,
            observed_at=_observed_at(self._dependencies.clock()),
            create_fingerprint=coordinator.create_fingerprint,
            run_fingerprint=coordinator.run_fingerprint,
            effective_plan_digest=(
                coordinator.resolved_plan.effective_plan.canonical_digest()
                if coordinator.resolved_plan is not None
                else (previous.effective_plan_digest if previous is not None else None)
            ),
            fact_refs=() if previous_ref is None else (previous_ref,),
            fact_digests=(),
            primary_fact=primary,
            cleanup_fact=cleanup,
            primary_lease_id=coordinator.primary_lease_id,
            cancel_reason=(
                coordinator.cancel_reason if cancellation_is_durable else None
            ),
            cancel_fingerprint=(
                coordinator.cancel_fingerprint if cancellation_is_durable else None
            ),
        )
        ref = self._dependencies.evidence_repository.append_transition(event)
        coordinator.last_event = event
        coordinator.last_event_ref = ref
        coordinator.state = state
        return event

    async def _cleanup_after_transition_failure(
        self,
        coordinator: _V2EpisodeCoordinator,
        failure: SafeFailureFactV2,
    ) -> None:
        coordinator.primary_failure = failure
        await self._close_owner(coordinator, failure)
        if coordinator.last_event is not None:
            self._dependencies.evidence_repository.quarantine(
                QuarantinePublicationInputsV2(
                    episode_id=coordinator.request.episode_id,
                    event=coordinator.last_event,
                    failure=failure,
                )
            )
        coordinator.state = EpisodeLifecycleState.QUARANTINED
        coordinator.cleanup_disposition = EpisodeCleanupDisposition.QUARANTINED

    async def _transition_to_closing_and_cleanup(
        self,
        coordinator: _V2EpisodeCoordinator,
        kind: str,
        failure: SafeFailureFactV2,
        *,
        cleanup: SafeFailureFactV2 | None = None,
    ) -> V2CloseResult:
        coordinator.primary_failure = failure
        transition_error: BaseException | None = None
        try:
            await self._transition(
                coordinator,
                EpisodeLifecycleState.CLOSING,
                kind,
                primary=failure,
                cleanup=cleanup,
            )
        except BaseException as exc:
            transition_error = exc
        publication_error: BaseException | None = None
        if transition_error is None:
            try:
                self._publish_failed_completed(coordinator, failure)
            except BaseException as exc:
                publication_error = exc
        result = await self._close_owner(coordinator, failure)
        if transition_error is not None:
            raise transition_error
        if publication_error is not None:
            raise publication_error
        return result

    def _publish_failed_completed(
        self,
        coordinator: _V2EpisodeCoordinator,
        failure: SafeFailureFactV2,
    ) -> CompletedPublicationV2:
        if coordinator.completed is not None:
            return coordinator.completed
        if coordinator.last_event_ref is None or coordinator.last_event is None:
            raise V2EpisodeUnavailable(
                _v2_failure(
                    "evidence",
                    "failure_publication_head_missing",
                    "reconcile",
                    "durable",
                )
            )
        disposition = (
            coordinator.primary_disposition or EpisodePrimaryDisposition.FAILED
        )
        if coordinator.run_fingerprint is None:
            coordinator.run_fingerprint = _v2_fingerprint(
                "bb.rl.no-run-fingerprint.v1",
                {
                    "episode_id": coordinator.request.episode_id,
                    "create_fingerprint": coordinator.create_fingerprint,
                    "failure_phase": failure.side_effect_boundary,
                },
            )
        run_payload = {
            "episode_id": coordinator.request.episode_id,
            "create_fingerprint": coordinator.create_fingerprint,
            "run_fingerprint": coordinator.run_fingerprint,
            "primary_disposition": disposition.value,
            "response": None,
            "termination": None,
            "turn_count": 0,
            "reward": None,
            "reward_components": {},
        }
        plan = coordinator.resolved_plan
        lease = coordinator.lease
        completed = self._dependencies.evidence_repository.publish_failed_completed(
            FailedCompletedPublicationInputsV2(
                episode_id=coordinator.request.episode_id,
                create_fingerprint=coordinator.create_fingerprint,
                run_fingerprint=coordinator.run_fingerprint,
                create_response_bytes=canonical_json_bytes(
                    _create_result_payload(coordinator.create_result)
                ),
                run_response_bytes=canonical_json_bytes(run_payload),
                lifecycle_head_ref=coordinator.last_event_ref,
                lifecycle_head_digest=coordinator.last_event.digest,
                primary_disposition=disposition.value,
                primary_failure=failure,
                session_close_failure=coordinator.session_close_failure,
                verifier_cleanup_failure=coordinator.verifier_cleanup_failure,
                runner_event_refs=tuple(coordinator.runner_event_refs),
                resolved_plan=plan,
                policy_binding_digest=(
                    coordinator.binding.binding_digest
                    if coordinator.binding is not None
                    else None
                ),
                materialization_receipt=(
                    lease._materialized.receipt if lease is not None else None
                ),
                primary_measurement=(lease.measurement if lease is not None else None),
                verifier_snapshot=None,
                verifier_measurement_digest=None,
                verifier_result=None,
                subject_digest=coordinator.resolved_subject_digest,
                verifier_cleanup_receipt=(
                    None
                    if coordinator.verifier_cleanup_failure is not None
                    and coordinator.verifier_cleanup_receipt is not None
                    and coordinator.verifier_cleanup_receipt.lease_id
                    != coordinator.verifier_lease_id
                    else coordinator.verifier_cleanup_receipt
                ),
                verifier_lease_id=coordinator.verifier_lease_id,
                authority_access_events=self._authority_access_events(coordinator),
            )
        )
        coordinator.completed = completed
        return completed

    async def _discard_unaccepted(self, coordinator: _V2EpisodeCoordinator) -> None:
        if coordinator.last_event is not None:
            return
        async with self._dictionary_lock:
            episode_id = coordinator.request.episode_id
            if (
                coordinator.last_event is None
                and self._coordinators.get(episode_id) is coordinator
            ):
                del self._coordinators[episode_id]

    async def _close_owned_session(
        self, coordinator: _V2EpisodeCoordinator
    ) -> tuple[asyncio.CancelledError | None, BaseException | None]:
        async with coordinator.lock:
            session = coordinator.session
            if session is None:
                return None, None
            task = coordinator.session_close_task
            if task is None:
                task = asyncio.create_task(
                    session.close(),
                    name=(
                        f"bb-v2-runner-session-close:{coordinator.request.episode_id}"
                    ),
                )
                coordinator.session_close_task = task
        _, cancellation, close_error = await _observe_owned_task(task)
        if not task.done():
            raise AssertionError("runner session close observation is not terminal")
        if close_error is not None:
            async with coordinator.lock:
                if coordinator.session_close_failure is None:
                    coordinator.session_close_failure = _failure_from_exception(
                        close_error, "session_close"
                    )
        return cancellation, close_error

    async def _close_unowned_binding(
        self, coordinator: _V2EpisodeCoordinator
    ) -> BaseException | None:
        async with coordinator.lock:
            if coordinator.binding is None or coordinator.session is not None:
                return None
            if coordinator.binding_released:
                return coordinator.binding_close_error
            task = coordinator.binding_close_task
            if task is None:
                binding = coordinator.binding
                episode_id = coordinator.request.episode_id

                async def observe_physical_close() -> _V2BindingCloseOutcome:
                    cancellations: list[asyncio.CancelledError] = []
                    while True:
                        close_waiter = asyncio.create_task(
                            binding.close(),
                            name=f"bb-v2-policy-binding-close-waiter:{episode_id}",
                        )
                        (
                            _,
                            waiter_cancellation,
                            physical_failure,
                        ) = await _observe_owned_task(close_waiter)
                        if waiter_cancellation is not None:
                            cancellations.append(waiter_cancellation)
                        if isinstance(physical_failure, asyncio.CancelledError):
                            cancellations.append(physical_failure)
                            continue
                        return _V2BindingCloseOutcome(
                            tuple(cancellations),
                            physical_failure,
                        )

                task = asyncio.create_task(
                    observe_physical_close(),
                    name=f"bb-v2-policy-binding-close:{episode_id}",
                )
                coordinator.binding_close_task = task
        outcome, cancellation, observer_failure = await _observe_owned_task(task)
        if outcome is None:
            outcome = _V2BindingCloseOutcome((), observer_failure)
        elif observer_failure is not None:
            outcome = _V2BindingCloseOutcome(
                outcome.cancellations,
                observer_failure,
            )
        cleanup_error = outcome.failure or next(
            iter(outcome.cancellations),
            None,
        )
        observed_errors: list[BaseException] = [
            *(() if cancellation is None else (cancellation,)),
            *outcome.cancellations,
            *(() if outcome.failure is None else (outcome.failure,)),
        ]
        close_error = (
            None
            if not observed_errors
            else (
                observed_errors[0]
                if len(observed_errors) == 1
                else BaseExceptionGroup(
                    "policy binding close cancelled and failed",
                    observed_errors,
                )
            )
        )
        async with coordinator.lock:
            if not task.done():
                raise AssertionError("policy binding close observation is not terminal")
            if coordinator.binding_released:
                close_error = coordinator.binding_close_error
            else:
                coordinator.binding_close_error = close_error
                if cleanup_error is not None:
                    coordinator.session_close_failure = _failure_from_exception(
                        cleanup_error, "session_close"
                    )
                coordinator.binding_released = True
        return close_error

    async def _close_owner(
        self,
        coordinator: _V2EpisodeCoordinator,
        primary_failure: SafeFailureFactV2 | None,
    ) -> V2CloseResult:
        await self._close_owned_session(coordinator)
        await self._close_unowned_binding(coordinator)
        if primary_failure is None:
            primary_failure = coordinator.session_close_failure
        async with coordinator.lock:
            if primary_failure is not None and coordinator.primary_failure is None:
                coordinator.primary_failure = primary_failure
            if coordinator.cleanup_task is None or (
                coordinator.cleanup_task.done()
                and coordinator.closed is None
                and coordinator.cleanup_receipt is not None
                and coordinator.state is EpisodeLifecycleState.CLOSING
            ):
                coordinator.cleanup_task = asyncio.create_task(
                    self._finish_cleanup(
                        coordinator,
                        coordinator.cleanup_receipt,
                        coordinator.primary_failure,
                    )
                    if coordinator.cleanup_receipt is not None
                    else self._cleanup_once(coordinator)
                )
            task = coordinator.cleanup_task
        return await asyncio.shield(task)

    async def _cleanup_once(self, coordinator: _V2EpisodeCoordinator) -> V2CloseResult:
        primary_failure = coordinator.primary_failure
        if coordinator.lease is None:
            if (
                coordinator.completed is not None
                and coordinator.primary_lease_id is None
            ):
                return await self._finish_cleanup(coordinator, None, primary_failure)
            failure = primary_failure or _v2_failure(
                "cleanup", "cleanup_receipt_missing", "reconcile", "accepted"
            )
            await self._quarantine(coordinator, failure)
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        try:
            receipt = await asyncio.shield(coordinator.lease.close())
        except BaseException as exc:
            failure = _failure_from_exception(exc, "cleanup")
            await self._quarantine(coordinator, failure)
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        coordinator.cleanup_receipt = receipt
        return await self._finish_cleanup(coordinator, receipt, primary_failure)

    async def _finish_cleanup(
        self,
        coordinator: _V2EpisodeCoordinator,
        receipt: SandboxCleanupReceipt | None,
        primary_failure: SafeFailureFactV2 | None,
    ) -> V2CloseResult:
        if receipt is not None and not _cleanup_released(receipt):
            failure = _v2_failure(
                "cleanup",
                "cleanup_not_released",
                "reconcile",
                "cleanup",
                lease_id=receipt.lease_id,
            )
            await self._quarantine(
                coordinator,
                failure,
                primary=primary_failure,
                independent_cleanup=True,
            )
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        if receipt is None and (
            coordinator.lease is not None or coordinator.primary_lease_id is not None
        ):
            failure = _v2_failure(
                "cleanup",
                "cleanup_receipt_missing",
                "reconcile",
                "cleanup",
                lease_id=coordinator.primary_lease_id,
            )
            await self._quarantine(
                coordinator,
                failure,
                primary=primary_failure,
                independent_cleanup=True,
            )
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        if coordinator.completed is None:
            # A failed pre-completion episode has cleanup proof but no completed
            # evidence root; it remains quarantined rather than manufacturing one.
            failure = primary_failure or _v2_failure(
                "primary", "completed_evidence_missing", "none", "cleanup"
            )
            await self._quarantine(coordinator, failure)
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        if coordinator.verifier_cleanup_receipt is not None and (
            coordinator.verifier_cleanup_receipt.lease_id
            != coordinator.verifier_lease_id
            or not _cleanup_released(
                coordinator.verifier_cleanup_receipt,
                required={"runtime", "workspace", "snapshot", "lease_record"},
            )
        ):
            failure = coordinator.verifier_cleanup_failure or _v2_failure(
                "cleanup",
                "verifier_cleanup_not_released",
                "reconcile",
                "verifier_cleanup",
                lease_id=coordinator.verifier_lease_id,
            )
            await self._quarantine(coordinator, failure)
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        event = coordinator.last_event
        if (
            event is None
            or coordinator.last_event_ref is None
            or coordinator.state is not EpisodeLifecycleState.CLOSING
        ):
            failure = _v2_failure(
                "lifecycle",
                "closing_head_missing",
                "reconcile",
                "cleanup",
            )
            await self._quarantine(coordinator, failure)
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        subject_digest = coordinator.resolved_subject_digest
        if subject_digest is None:
            failure = _v2_failure(
                "evidence",
                "resolved_subject_missing",
                "reconcile",
                "completed",
            )
            await self._quarantine(
                coordinator,
                failure,
                primary=primary_failure,
                independent_cleanup=True,
            )
            return V2CloseResult(
                coordinator.request.episode_id,
                coordinator.state,
                coordinator.cleanup_disposition,
                None,
            )
        cleanup_digest = canonical_digest(receipt) if receipt is not None else None
        closed_event = LifecycleEventV2(
            episode_id=coordinator.request.episode_id,
            sequence=event.sequence + 1,
            previous_event_digest=event.digest,
            from_state=EpisodeLifecycleState.CLOSING.value,
            to_state=EpisodeLifecycleState.CLOSED.value,
            event_kind="closed",
            observed_at=_observed_at(self._dependencies.clock()),
            create_fingerprint=coordinator.create_fingerprint,
            run_fingerprint=coordinator.run_fingerprint,
            effective_plan_digest=event.effective_plan_digest,
            fact_refs=(coordinator.last_event_ref,),
            fact_digests=() if cleanup_digest is None else (cleanup_digest,),
            primary_lease_id=coordinator.primary_lease_id,
            cancel_reason=event.cancel_reason,
            cancel_fingerprint=event.cancel_fingerprint,
        )
        try:
            pins = self._dependencies.evidence_repository.prepare_export_pins(
                coordinator.request.episode_id,
                coordinator.completed,
                subject_digest=subject_digest,
                scope="episode_export",
            )
            closed = self._dependencies.evidence_repository.publish_closed(
                ClosedPublicationInputsV2(
                    episode_id=coordinator.request.episode_id,
                    completed=coordinator.completed,
                    cleanup_receipt=receipt,
                    closed_event=closed_event,
                    final_primary_outcome=(
                        coordinator.primary_disposition
                        or EpisodePrimaryDisposition.FAILED
                    ).value,
                    cleanup_lease_id=(
                        receipt.lease_id if receipt is not None else None
                    ),
                    cleanup_required_resources=(
                        (
                            "child_verifier",
                            "runtime",
                            "workspace",
                            "cache_holder",
                            "lease_record",
                        )
                        if receipt is not None
                        else ()
                    ),
                    verifier_cleanup_receipt=(
                        coordinator.verifier_cleanup_receipt
                        if coordinator.verifier_cleanup_receipt is not None
                        and _cleanup_released(
                            coordinator.verifier_cleanup_receipt,
                            required={
                                "runtime",
                                "workspace",
                                "snapshot",
                                "lease_record",
                            },
                        )
                        else None
                    ),
                    verifier_cleanup_lease_id=(
                        coordinator.verifier_lease_id
                        if coordinator.verifier_cleanup_receipt is not None
                        and _cleanup_released(
                            coordinator.verifier_cleanup_receipt,
                            required={
                                "runtime",
                                "workspace",
                                "snapshot",
                                "lease_record",
                            },
                        )
                        else None
                    ),
                    verifier_cleanup_required_resources=(
                        ("runtime", "workspace", "snapshot", "lease_record")
                        if coordinator.verifier_cleanup_receipt is not None
                        and _cleanup_released(
                            coordinator.verifier_cleanup_receipt,
                            required={
                                "runtime",
                                "workspace",
                                "snapshot",
                                "lease_record",
                            },
                        )
                        else ()
                    ),
                    export_authorization_refs=pins.authorization_refs,
                    redaction_decision_refs=pins.redaction_decision_refs,
                )
            )
            if closed.locator.current_state != EpisodeLifecycleState.CLOSED.value:
                raise EvidenceCorruptError(
                    "closed publication did not return a closed locator"
                )
        except BaseException as publication_error:
            try:
                winner = self._dependencies.evidence_repository.recover(
                    coordinator.request.episode_id
                )
            except BaseException:
                winner = None
            if (
                winner is not None
                and winner.closed_tombstone is not None
                and winner.closed_envelope is not None
                and winner.locator.closed_tombstone_ref is not None
                and winner.events
                and winner.events[-1].to_state == EpisodeLifecycleState.CLOSED.value
            ):
                closed = ClosedPublicationV2(
                    winner.closed_envelope,
                    winner.closed_tombstone.envelope_ref,
                    winner.closed_tombstone,
                    winner.locator.closed_tombstone_ref,
                    winner.locator,
                )
                closed_event = winner.events[-1]
            else:
                failure = _v2_failure(
                    "evidence",
                    "closed_publication_failed",
                    "reconcile",
                    "cleanup",
                    lease_id=receipt.lease_id if receipt is not None else None,
                )
                try:
                    await self._quarantine(
                        coordinator,
                        failure,
                        primary=primary_failure,
                        independent_cleanup=True,
                    )
                except BaseException as quarantine_error:
                    raise BaseExceptionGroup(
                        "closed publication and durable quarantine failed",
                        [publication_error, quarantine_error],
                    ) from publication_error
                return V2CloseResult(
                    coordinator.request.episode_id,
                    coordinator.state,
                    coordinator.cleanup_disposition,
                    None,
                )
        coordinator.closed = closed
        coordinator.last_event = closed_event
        coordinator.last_event_ref = closed.locator.latest_event_ref
        coordinator.cleanup_disposition = EpisodeCleanupDisposition.RELEASED
        coordinator.state = EpisodeLifecycleState(closed.locator.current_state)
        return V2CloseResult(
            coordinator.request.episode_id,
            coordinator.state,
            coordinator.cleanup_disposition,
            closed.envelope_ref,
        )

    async def _quarantine(
        self,
        coordinator: _V2EpisodeCoordinator,
        failure: SafeFailureFactV2,
        *,
        primary: SafeFailureFactV2 | None = None,
        independent_cleanup: bool = False,
    ) -> None:
        if coordinator.state is not EpisodeLifecycleState.QUARANTINED:
            event = await self._transition(
                coordinator,
                EpisodeLifecycleState.QUARANTINED,
                "quarantined",
                primary=(primary if independent_cleanup else failure),
                cleanup=failure if independent_cleanup else None,
            )
            self._dependencies.evidence_repository.quarantine(
                QuarantinePublicationInputsV2(
                    episode_id=coordinator.request.episode_id,
                    event=event,
                    failure=primary
                    if independent_cleanup and primary is not None
                    else failure,
                )
            )
        coordinator.cleanup_disposition = EpisodeCleanupDisposition.QUARANTINED

    def _publish_completed(
        self,
        coordinator: _V2EpisodeCoordinator,
        result: RunnerResult,
        snapshot: VerifierSnapshotReceipt,
        verifier: VerifierWorkspaceLease,
        verifier_result: Mapping[str, Any],
        event: LifecycleEventV2,
    ) -> CompletedPublicationV2:
        plan = coordinator.resolved_plan
        lease = coordinator.lease
        authority_plan = coordinator.evidence_authority_plan
        if (
            plan is None
            or lease is None
            or coordinator.last_event_ref is None
            or authority_plan is None
        ):
            raise V2EpisodeUnavailable(
                _v2_failure(
                    "lifecycle", "publication_inputs_missing", "reconcile", "completed"
                )
            )
        object_inputs = self._dependencies.evidence_authority.materialize(
            authority_plan,
            runner_result=result,
            verifier_snapshot=snapshot,
            verifier_result=verifier_result,
        )
        evidence_objects = (
            self._dependencies.evidence_repository.publish_evidence_objects(
                coordinator.request.episode_id,
                authority_plan,
                object_inputs,
            )
        )
        evidence_policy = next(
            record
            for record in self._dependencies.sandbox_runtime.registries.evidence_policies
            if record.policy == plan.effective_plan.evidence
        )
        retention_policy = next(
            record
            for record in self._dependencies.sandbox_runtime.registries.retention_policies
            if record.grant.policy == plan.effective_plan.retention
        )
        run_payload = {
            "episode_id": coordinator.request.episode_id,
            "create_fingerprint": coordinator.create_fingerprint,
            "run_fingerprint": coordinator.run_fingerprint,
            "primary_disposition": EpisodePrimaryDisposition.SUCCEEDED.value,
            "response": dict(result.response),
            "termination": result.termination.value,
            "turn_count": result.turn_count,
            "workspace_diff": dict(coordinator.workspace_diff or {}),
            "reward": verifier_result.get("reward"),
            "reward_components": dict(verifier_result.get("reward_components", {})),
        }
        return self._dependencies.evidence_repository.publish_completed(
            CompletedPublicationInputsV2(
                episode_id=coordinator.request.episode_id,
                create_fingerprint=coordinator.create_fingerprint,
                run_fingerprint=coordinator.run_fingerprint or "",
                create_response_bytes=canonical_json_bytes(
                    _create_result_payload(coordinator.create_result)
                ),
                run_response_bytes=canonical_json_bytes(run_payload),
                resolved_plan=plan,
                policy_binding_digest=coordinator.binding.binding_digest,
                runner_result=result,
                materialization_receipt=lease._materialized.receipt,
                primary_measurement=lease.measurement,
                verifier_snapshot=snapshot,
                verifier_measurement_digest=_measurement_digest(verifier.measurement),
                verifier_result=verifier_result,
                verifier_cleanup_receipt=coordinator.verifier_cleanup_receipt,
                verifier_lease_id=coordinator.verifier_lease_id,
                evidence_objects=evidence_objects,
                evidence_policy=evidence_policy,
                retention_policy=retention_policy,
                lifecycle_head_ref=coordinator.last_event_ref,
                lifecycle_head_digest=event.digest,
                primary_disposition=EpisodePrimaryDisposition.SUCCEEDED.value,
                reward_disposition="eligible",
                reward_components=verifier_result.get("reward_components", {}),
                subject_digest=coordinator.resolved_subject_digest,
                authority_access_events=self._authority_access_events(coordinator),
            )
        )

    async def _reconstruct(
        self,
        recovered: RecoveredEpisodeV2,
        receipts_by_lease: Mapping[str, SandboxCleanupReceipt],
    ) -> None:
        if not recovered.events:
            raise V2EpisodeQuarantined(
                _v2_failure(
                    "evidence",
                    "event_head_missing",
                    "reconcile",
                    "durable",
                )
            )
        previous = recovered.events[-1]
        request = _V2RecoveredRequest(recovered.locator.episode_id)
        tombstone = recovered.closed_tombstone or recovered.completed_tombstone
        if tombstone is not None:
            await self._recover_create(
                request,
                tombstone.create_fingerprint,
                recovered,
            )
            coordinator = await self._coordinator(recovered.locator.episode_id)
            coordinator.verifier_cleanup_receipt = recovered.verifier_cleanup_receipt
            coordinator.verifier_lease_id = recovered.verifier_lease_id
            if recovered.quarantined:
                coordinator.state = EpisodeLifecycleState.QUARANTINED
                coordinator.cleanup_disposition = EpisodeCleanupDisposition.QUARANTINED
                return
            if recovered.closed_tombstone is not None:
                return
            lease_id = recovered.primary_lease_id
            receipt = receipts_by_lease.get(lease_id) if lease_id is not None else None
            if receipt is None:
                failure = _v2_failure(
                    "cleanup",
                    "cleanup_receipt_missing",
                    "reconcile",
                    "restart_reconciliation",
                    lease_id=lease_id,
                )
                await self._quarantine(
                    coordinator,
                    failure,
                    independent_cleanup=True,
                )
                return
            if coordinator.state is not EpisodeLifecycleState.CLOSING:
                await self._transition(
                    coordinator,
                    EpisodeLifecycleState.CLOSING,
                    "restart_cleanup_reconciled",
                )
            coordinator.cleanup_receipt = receipt
            await self._finish_cleanup(
                coordinator,
                receipt,
                coordinator.primary_failure,
            )
            return

        state = EpisodeLifecycleState(recovered.locator.current_state)
        coordinator = _V2EpisodeCoordinator(
            request=request,
            create_fingerprint=previous.create_fingerprint,
            state=state,
            run_fingerprint=previous.run_fingerprint,
            primary_lease_id=recovered.primary_lease_id,
            primary_disposition=(
                EpisodePrimaryDisposition.INTERRUPTED
                if state
                in {
                    EpisodeLifecycleState.ACCEPTED,
                    EpisodeLifecycleState.CANCEL_REQUESTED,
                    EpisodeLifecycleState.ALLOCATING,
                    EpisodeLifecycleState.READY,
                    EpisodeLifecycleState.RUNNING,
                    EpisodeLifecycleState.VERIFYING,
                    EpisodeLifecycleState.CLOSING,
                }
                else None
            ),
            cleanup_disposition=(
                EpisodeCleanupDisposition.QUARANTINED
                if state is EpisodeLifecycleState.QUARANTINED
                else EpisodeCleanupDisposition.PENDING
            ),
            last_event=previous,
            last_event_ref=recovered.locator.latest_event_ref,
        )
        _hydrate_v2_cancellation_receipt(coordinator, recovered.events)
        async with self._dictionary_lock:
            coordinator = self._coordinators.setdefault(
                recovered.locator.episode_id,
                coordinator,
            )
        if state is EpisodeLifecycleState.QUARANTINED:
            return
        if state in {
            EpisodeLifecycleState.ACCEPTED,
            EpisodeLifecycleState.CANCEL_REQUESTED,
            EpisodeLifecycleState.ALLOCATING,
            EpisodeLifecycleState.READY,
            EpisodeLifecycleState.RUNNING,
            EpisodeLifecycleState.VERIFYING,
            EpisodeLifecycleState.CLOSING,
        }:
            failure = _v2_failure(
                "interruption",
                "process_interrupted",
                "new_episode_id",
                "restart_reconciliation",
                lease_id=recovered.primary_lease_id,
            )
            coordinator.primary_failure = failure
            lease_id = recovered.primary_lease_id
            if lease_id is None:
                await self._quarantine(coordinator, failure)
                return
            receipt = receipts_by_lease.get(lease_id) if lease_id is not None else None
            if receipt is None:
                cleanup_failure = _v2_failure(
                    "cleanup",
                    "cleanup_receipt_missing",
                    "reconcile",
                    "restart_reconciliation",
                    lease_id=lease_id,
                )
                await self._quarantine(
                    coordinator,
                    cleanup_failure,
                    primary=failure,
                    independent_cleanup=True,
                )
                return
            cleanup_failure = (
                None
                if _cleanup_released(receipt)
                else _v2_failure(
                    "cleanup",
                    "cleanup_not_released",
                    "reconcile",
                    "restart_reconciliation",
                    lease_id=lease_id,
                )
            )
            if state is not EpisodeLifecycleState.CLOSING:
                await self._transition(
                    coordinator,
                    EpisodeLifecycleState.CLOSING,
                    "process_interrupted",
                    primary=failure,
                    cleanup=cleanup_failure,
                )
            self._publish_failed_completed(coordinator, failure)
            coordinator.cleanup_receipt = receipt
            await self._finish_cleanup(coordinator, receipt, failure)

    async def _recover_create(
        self,
        request: ResolveEpisodeRequest,
        fingerprint: str,
        recovered: RecoveredEpisodeV2,
    ) -> V2OperationResult[V2CreateResult]:
        tombstone = recovered.closed_tombstone or recovered.completed_tombstone
        if tombstone is None:
            if recovered.quarantined:
                raise V2EpisodeQuarantined(
                    _v2_failure(
                        "evidence", "episode_quarantined", "reconcile", "durable"
                    )
                )
            raise V2EpisodeUnavailable(
                _v2_failure("recovery", "episode_not_live", "reconcile", "durable")
            )
        if tombstone.create_fingerprint != fingerprint:
            raise V2EpisodeConflict(
                _v2_failure(
                    "conflict", "create_fingerprint_mismatch", "new_episode_id", "none"
                )
            )
        envelope = recovered.completed_envelope
        if envelope is None:
            raise V2EpisodeQuarantined(
                _v2_failure(
                    "evidence", "completed_envelope_missing", "reconcile", "durable"
                )
            )
        completed_tombstone = recovered.completed_tombstone
        if (
            completed_tombstone is None
            or recovered.locator.completed_tombstone_ref is None
        ):
            raise V2EpisodeQuarantined(
                _v2_failure(
                    "evidence",
                    "completed_tombstone_missing",
                    "reconcile",
                    "durable",
                )
            )
        try:
            create_payload = json.loads(
                self._dependencies.evidence_repository.get_response_bytes(
                    envelope.create_response_ref
                )
            )
            run_payload = json.loads(
                self._dependencies.evidence_repository.get_response_bytes(
                    tombstone.response_ref
                )
            )
            subject_digest = envelope.subject_digest
            if subject_digest is None:
                legacy_subject_digest = create_payload.get("subject_digest")
                if legacy_subject_digest is not None:
                    subject_digest = str(legacy_subject_digest)
                elif isinstance(request, ResolveEpisodeRequest):
                    subject_digest = canonical_digest(request.subject)
                else:
                    raise KeyError("subject_digest")
            create_result = V2CreateResult(
                episode_id=str(create_payload["episode_id"]),
                create_fingerprint=str(create_payload["create_fingerprint"]),
                state=EpisodeLifecycleState(create_payload["state"]),
                effective_plan_digest=str(create_payload["effective_plan_digest"]),
                selection_record_ref=_v2_artifact_ref(
                    create_payload["selection_record_ref"]
                ),
                effective_plan_ref=_v2_artifact_ref(
                    create_payload["effective_plan_ref"]
                ),
                policy_binding_digest=str(create_payload["policy_binding_digest"]),
                selection_commit=SelectionCommitToken.model_validate(
                    create_payload["selection_commit"]
                ),
                base_receipt_digest=str(create_payload["base_receipt_digest"]),
                final_receipt_digest=str(create_payload["final_receipt_digest"]),
                policy_observation_digest=str(
                    create_payload["policy_observation_digest"]
                ),
                sandbox_preflight=_v2_sandbox_preflight(
                    create_payload["sandbox_preflight"]
                ),
            )
            run_result = V2RunResult(
                episode_id=str(run_payload["episode_id"]),
                create_fingerprint=str(run_payload["create_fingerprint"]),
                run_fingerprint=str(run_payload["run_fingerprint"]),
                primary_disposition=EpisodePrimaryDisposition(
                    run_payload["primary_disposition"]
                ),
                response=MappingProxyType(dict(run_payload["response"]))
                if run_payload.get("response") is not None
                else None,
                termination=run_payload.get("termination"),
                turn_count=int(run_payload.get("turn_count", 0)),
                completed_envelope_ref=completed_tombstone.envelope_ref,
                closed_envelope_ref=recovered.closed_tombstone.envelope_ref
                if recovered.closed_tombstone is not None
                else None,
                result_ref=recovered.result_ref,
                evidence_manifest_ref=recovered.evidence_manifest_ref,
                evidence_root=recovered.evidence_root,
                reward=run_payload.get("reward"),
                reward_components=MappingProxyType(
                    dict(run_payload.get("reward_components", {}))
                ),
                artifact_manifest_ref=recovered.artifact_manifest_ref,
                primary_measurement_digest=recovered.primary_measurement_digest,
                verifier_measurement_digest=recovered.verifier_measurement_digest,
                verifier_result_digest=recovered.verifier_result_digest,
                workspace_diff=(
                    MappingProxyType(dict(run_payload["workspace_diff"]))
                    if run_payload.get("workspace_diff")
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise V2EpisodeQuarantined(
                _v2_failure(
                    "evidence", "cached_response_corrupt", "reconcile", "durable"
                )
            ) from exc
        completed_publication = CompletedPublicationV2(
            envelope,
            completed_tombstone.envelope_ref,
            completed_tombstone,
            recovered.locator.completed_tombstone_ref,
            recovered.locator,
            recovered.evidence_manifest,
        )
        closed_publication = None
        if recovered.closed_tombstone is not None:
            if (
                recovered.closed_envelope is None
                or recovered.locator.closed_tombstone_ref is None
            ):
                raise V2EpisodeQuarantined(
                    _v2_failure(
                        "evidence",
                        "closed_publication_missing",
                        "reconcile",
                        "durable",
                    )
                )
            closed_publication = ClosedPublicationV2(
                recovered.closed_envelope,
                recovered.closed_tombstone.envelope_ref,
                recovered.closed_tombstone,
                recovered.locator.closed_tombstone_ref,
                recovered.locator,
            )
        coordinator = _V2EpisodeCoordinator(
            request=request,
            create_fingerprint=fingerprint,
            state=EpisodeLifecycleState(recovered.locator.current_state),
            create_result=create_result,
            run_result=run_result,
            completed=completed_publication,
            closed=closed_publication,
            resolved_subject_digest=subject_digest,
            run_fingerprint=tombstone.run_fingerprint,
            primary_lease_id=recovered.primary_lease_id,
            primary_disposition=run_result.primary_disposition,
            workspace_diff=run_result.workspace_diff,
            cleanup_disposition=EpisodeCleanupDisposition.RELEASED
            if recovered.closed_tombstone is not None
            else EpisodeCleanupDisposition.PENDING,
            verifier_cleanup_receipt=recovered.verifier_cleanup_receipt,
            verifier_lease_id=recovered.verifier_lease_id,
            terminal_committed=True,
            last_event=recovered.events[-1],
            last_event_ref=recovered.locator.latest_event_ref,
        )
        _hydrate_v2_cancellation_receipt(coordinator, recovered.events)
        async with self._dictionary_lock:
            winner = self._coordinators.setdefault(request.episode_id, coordinator)
        return V2OperationResult(
            winner.create_result or create_result, V2OperationDisposition.CACHED
        )


def _hydrate_v2_cancellation_receipt(
    coordinator: _V2EpisodeCoordinator,
    events: tuple[LifecycleEventV2, ...],
) -> None:
    reason = events[-1].cancel_reason
    fingerprint = events[-1].cancel_fingerprint
    if reason is None and fingerprint is None:
        return
    expected = _v2_cancel_fingerprint(
        coordinator.request.episode_id,
        coordinator.create_fingerprint,
        reason or "",
    )
    if reason is None or fingerprint != expected:
        raise V2EpisodeQuarantined(
            _v2_failure(
                "evidence",
                "cancellation_receipt_corrupt",
                "reconcile",
                "durable",
            )
        )
    coordinator.cancel_reason = reason
    coordinator.cancel_fingerprint = fingerprint
    coordinator.cancel_event.set()


def _v2_cancel_fingerprint(
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


def _v2_artifact_ref(value: Mapping[str, Any]) -> ContractArtifactRef:
    return ContractArtifactRef.model_validate(value)


def _v2_fingerprint(schema_version: str, payload: Mapping[str, Any]) -> str:
    body = {"schema_version": schema_version, **payload}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _measurement_digest(measurement: Any) -> str:
    method = getattr(measurement, "canonical_digest", None)
    return method() if callable(method) else canonical_digest(measurement)


def _v2_failure(
    category: str,
    code: str,
    retry: str,
    boundary: str,
    *,
    lease_id: str | None = None,
) -> SafeFailureFactV2:
    return SafeFailureFactV2(category, code, retry, boundary, lease_id=lease_id)


def _retryable_shutdown_failure(exc: BaseException) -> bool:
    if isinstance(exc, SandboxFault):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_retryable_shutdown_failure(item) for item in exc.exceptions)
    return False


def _failure_from_exception(
    exc: BaseException,
    boundary: str,
) -> SafeFailureFactV2:
    if isinstance(exc, V2EpisodeError):
        return exc.failure
    cancelled = isinstance(exc, (asyncio.CancelledError, RunnerCancelled))
    code = "process_interrupted" if cancelled else getattr(exc, "code", None)
    if not isinstance(code, str) or not code:
        code = type(exc).__name__.lower()
    lease_id = getattr(exc, "lease_id", None)
    return _v2_failure(
        "cancellation" if cancelled else "runtime",
        code,
        "reconcile",
        boundary,
        lease_id=lease_id if isinstance(lease_id, str) else None,
    )


def _cleanup_released(
    receipt: SandboxCleanupReceipt,
    *,
    required: set[str] | None = None,
) -> bool:
    released = {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
    base_resources = {"runtime", "workspace", "cache_holder", "lease_record"}
    required_resources = base_resources if required is None else required
    allowed_resources = (
        base_resources | {"child_verifier"} if required is None else required_resources
    )
    resources = tuple(step.resource for step in receipt.steps)
    resource_set = set(resources)
    return (
        receipt.state in released
        and len(resources) == len(resource_set)
        and required_resources <= resource_set <= allowed_resources
        and all(step.state in released for step in receipt.steps)
    )


def _observed_at(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("V2 clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sandbox_preflight_identity(
    plan: SandboxExecutionPlan,
) -> V2SandboxPreflightIdentity:
    return V2SandboxPreflightIdentity(
        runtime=plan.runtime.runtime_id,
        runtime_class=plan.runtime.runtime_class,
        runtime_binary_digest=plan.runtime.measured_binary_digest,
        image_digest=plan.image.image_digest,
        security_policy_digest=plan.security_policy.policy_digest,
        network_policy_digest=plan.network_policy.policy_digest,
        verifier_digest=plan.verifier.grant.implementation_digest,
        materialization_plan_digest=MaterializationKey.from_plan(
            plan.materialization_plan
        ).digest,
    )


def _v2_sandbox_preflight(
    value: Mapping[str, Any],
) -> V2SandboxPreflightIdentity:
    return V2SandboxPreflightIdentity(
        runtime=str(value["runtime"]),
        runtime_class=RuntimeClass(value["runtime_class"]),
        runtime_binary_digest=str(value["runtime_binary_digest"]),
        image_digest=str(value["image_digest"]),
        security_policy_digest=str(value["security_policy_digest"]),
        network_policy_digest=str(value["network_policy_digest"]),
        verifier_digest=str(value["verifier_digest"]),
        materialization_plan_digest=str(value["materialization_plan_digest"]),
    )


def _create_result_payload(
    result: V2CreateResult | None,
) -> Mapping[str, Any]:
    if result is None:
        return {}
    return {
        "episode_id": result.episode_id,
        "create_fingerprint": result.create_fingerprint,
        "state": result.state.value,
        "effective_plan_digest": result.effective_plan_digest,
        "selection_record_ref": result.selection_record_ref.model_dump(mode="json"),
        "effective_plan_ref": result.effective_plan_ref.model_dump(mode="json"),
        "policy_binding_digest": result.policy_binding_digest,
        "selection_commit": result.selection_commit.model_dump(mode="json"),
        "base_receipt_digest": result.base_receipt_digest,
        "final_receipt_digest": result.final_receipt_digest,
        "policy_observation_digest": result.policy_observation_digest,
        "sandbox_preflight": {
            "runtime": result.sandbox_preflight.runtime,
            "runtime_class": result.sandbox_preflight.runtime_class.value,
            "runtime_binary_digest": result.sandbox_preflight.runtime_binary_digest,
            "image_digest": result.sandbox_preflight.image_digest,
            "security_policy_digest": result.sandbox_preflight.security_policy_digest,
            "network_policy_digest": result.sandbox_preflight.network_policy_digest,
            "verifier_digest": result.sandbox_preflight.verifier_digest,
            "materialization_plan_digest": (
                result.sandbox_preflight.materialization_plan_digest
            ),
        },
    }


__all__ = [
    "BreadBoardV2EpisodeService",
    "EpisodeCleanupDisposition",
    "EpisodeLifecycleState",
    "EpisodePrimaryDisposition",
    "PolicyRuntimeClientResolver",
    "V2CancellationResult",
    "V2CloseResult",
    "V2CreateResult",
    "V2SandboxPreflightIdentity",
    "V2EpisodeConflict",
    "V2EpisodeError",
    "V2EpisodeNotFound",
    "V2EpisodeQuarantined",
    "V2EpisodeRejected",
    "V2EpisodeState",
    "V2EpisodeUnavailable",
    "V2LifecycleDependencies",
    "V2OperationDisposition",
    "V2OperationResult",
    "V2RunResult",
]
