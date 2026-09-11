"""Public, typed author-side policy contract.

The values in this module are deliberately boring data records and synchronous
facades.  They describe what an author may do; Session, authority, transport,
and resource ownership remain host responsibilities.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, Literal, NewType, Protocol, TypeAlias, TypeVar

from .transport import decode_bytes, encode_bytes



ModuleId = NewType("ModuleId", str)
GenerationId = NewType("GenerationId", str)
InstanceId = NewType("InstanceId", str)
WorkId = NewType("WorkId", str)
AttemptId = NewType("AttemptId", str)
CheckpointId = NewType("CheckpointId", str)
RequestId = NewType("RequestId", str)
WorkerSessionId = NewType("WorkerSessionId", str)
OutputSequence = NewType("OutputSequence", int)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
StateT = TypeVar("StateT")
SharedT = TypeVar("SharedT")

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]

MAX_FRAME_BYTES = 262_144
MAX_CHECKPOINT_BYTES = 1_048_576


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _nonnegative(value: int, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _bytes(value: bytes, label: str, *, maximum: int | None = None) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{label} must be bytes")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes")
    return value


@dataclass(frozen=True, slots=True)
class InstanceIdentity:
    instance_id: InstanceId
    module_id: ModuleId
    generation_id: GenerationId
    instance_label: str

    def __post_init__(self) -> None:
        _text(self.instance_id, "instance_id")
        _text(self.module_id, "module_id")
        _text(self.generation_id, "generation_id")
        _text(self.instance_label, "instance_label")


@dataclass(frozen=True, slots=True)
class InputEnvelope:
    schema_id: str
    sequence: int
    body: bytes
    final: bool

    def __post_init__(self) -> None:
        _text(self.schema_id, "input schema_id")
        _nonnegative(self.sequence, "input sequence")
        _bytes(self.body, "input body", maximum=MAX_FRAME_BYTES)
        if type(self.final) is not bool:
            raise TypeError("input final must be boolean")

@dataclass(frozen=True, slots=True)
class ModuleInput:
    """Unadmitted public Session input before the owner assigns sequence."""

    schema_id: str
    body: bytes
    final: bool = False

    def __post_init__(self) -> None:
        _text(self.schema_id, "module input schema_id")
        _bytes(self.body, "module input body", maximum=MAX_FRAME_BYTES)
        if type(self.final) is not bool:
            raise TypeError("module input final must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "body": encode_bytes(self.body),
            "final": self.final,
            "schema_id": self.schema_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ModuleInput":
        if not isinstance(value, Mapping) or set(value) != {"body", "final", "schema_id"}:
            raise ValueError("module input requires exactly schema_id, body, and final")
        final = value["final"]
        if type(final) is not bool:
            raise ValueError("module input final must be boolean")
        return cls(
            schema_id=_text(value["schema_id"], "module input schema_id"),
            body=decode_bytes(value["body"]),
            final=final,
        )


@dataclass(frozen=True, slots=True)
class OutputEnvelope:
    schema_id: str
    body: bytes

    def __post_init__(self) -> None:
        _text(self.schema_id, "output schema_id")
        _bytes(self.body, "output body", maximum=MAX_FRAME_BYTES)


@dataclass(frozen=True, slots=True)
class CheckpointEnvelope:
    source_generation_id: GenerationId
    source_module_id: ModuleId
    source_instance_id: InstanceId
    source_work_id: WorkId
    source_attempt_id: AttemptId
    schema_id: str
    body: bytes

    def __post_init__(self) -> None:
        for value, label in (
            (self.source_generation_id, "source_generation_id"),
            (self.source_module_id, "source_module_id"),
            (self.source_instance_id, "source_instance_id"),
            (self.source_work_id, "source_work_id"),
            (self.source_attempt_id, "source_attempt_id"),
            (self.schema_id, "schema_id"),
        ):
            _text(value, label)
        _bytes(self.body, "checkpoint body", maximum=MAX_CHECKPOINT_BYTES)


@dataclass(frozen=True, slots=True)
class CheckpointProposal:
    payload: CheckpointEnvelope
    declared_at_sequence: int

    def __post_init__(self) -> None:
        _nonnegative(self.declared_at_sequence, "declared_at_sequence")


@dataclass(frozen=True, slots=True)
class CheckpointRequest:
    request_id: RequestId
    reason: str
    requested_at_sequence: int

    def __post_init__(self) -> None:
        _text(self.request_id, "checkpoint request_id")
        _text(self.reason, "checkpoint reason")
        _nonnegative(self.requested_at_sequence, "requested_at_sequence")


CheckpointRefusalCode = Literal[
    "boundary_unavailable",
    "pending_effect",
    "pending_resource",
    "pending_child",
    "unknown_effect",
    "cancelled",
    "unsupported",
]


@dataclass(frozen=True, slots=True)
class CheckpointRefusal:
    code: CheckpointRefusalCode
    detail: str
    retryable: bool

    def __post_init__(self) -> None:
        _text(self.code, "checkpoint refusal code")
        _text(self.detail, "checkpoint refusal detail")
        if type(self.retryable) is not bool:
            raise TypeError("checkpoint refusal retryable must be boolean")


@dataclass(frozen=True, slots=True)
class CheckpointCapture(Generic[StateT]):
    request: CheckpointRequest
    observed_sequence: int
    state: StateT | None
    refusal: CheckpointRefusal | None

    def __post_init__(self) -> None:
        _nonnegative(self.observed_sequence, "observed_sequence")
        if (self.state is None) == (self.refusal is None):
            raise ValueError("checkpoint capture must contain state or refusal")


@dataclass(frozen=True, slots=True)
class PolicyFailure:
    code: str
    detail: str
    retryable: bool
    output_emitted: bool

    def __post_init__(self) -> None:
        _text(self.code, "failure code")
        _text(self.detail, "failure detail")
        if type(self.retryable) is not bool or type(self.output_emitted) is not bool:
            raise TypeError("failure flags must be boolean")


@dataclass(frozen=True, slots=True)
class ContinueResult(Generic[StateT]):
    checkpoint: CheckpointProposal | None
    state: StateT | None


@dataclass(frozen=True, slots=True)
class OutputResult(Generic[OutputT, StateT]):
    output: OutputT
    checkpoint: CheckpointProposal | None
    state: StateT | None


@dataclass(frozen=True, slots=True)
class FailureResult(Generic[StateT]):
    failure: PolicyFailure
    checkpoint: CheckpointProposal | None
    state: StateT | None


class PolicyInstance(Protocol[InputT, OutputT, StateT]):
    """State and control flow for one independently owned instance."""

    def step(
        self, value: InputT
    ) -> ContinueResult[StateT] | OutputResult[OutputT, StateT] | FailureResult[StateT]:
        """Consume exactly one decoded input value."""
        ...

    def checkpoint(self, request: CheckpointRequest) -> CheckpointCapture[StateT]:
        """Capture fresh state or refuse at the observed boundary."""
        ...


@dataclass(frozen=True, slots=True)
class DependencyDeclaration:
    name: str
    contract_id: str

    def __post_init__(self) -> None:
        _text(self.name, "dependency name")
        _text(self.contract_id, "dependency contract_id")




class DependencyAccess(Protocol):
    """One compiler-declared dependency contract, not a universal effect bag."""

    @property
    def name(self) -> str:
        ...

    @property
    def contract_id(self) -> str:
        ...

    def exchange(self, value: ModuleInput) -> OutputEnvelope:
        """Submit one typed input to the compiler-selected dependency instance."""
        ...


class ProviderAccess(Protocol):
    """Provider owner facade; provider v2 values remain complete aggregates."""

    def start(self, request: "ProviderCallRequest") -> "ProviderExchangeHandle":
        ...

    def next_event(self, handle: "ProviderExchangeHandle") -> "ProviderStreamItem":
        ...

    def cancel(
        self, handle: "ProviderExchangeHandle", reason: str
    ) -> "ProviderCancelled | ProviderUnknown":
        ...


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    request_id: RequestId
    approval_request_id: str
    tool_id: str
    operation: str
    arguments_schema_id: str
    arguments_json: str
    arguments: JsonValue


@dataclass(frozen=True, slots=True)
class ToolApproval:
    approval_request_id: str
    policy_decision: Literal["allow", "deny"]
    operator_decision: Literal["once", "always", "reject"] | None
    scope: Literal["request", "session", "generation"] | None
    persistent: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ToolSucceeded:
    request_id: RequestId
    output_schema_id: str
    output: bytes


@dataclass(frozen=True, slots=True)
class ToolFailed:
    request_id: RequestId
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ToolCancelled:
    request_id: RequestId
    owner: Literal["caller", "tool", "transport", "engine"]
    reason: str


@dataclass(frozen=True, slots=True)
class ToolUnknown:
    request_id: RequestId
    reason: str
    evidence_refs: tuple[str, ...]


ToolOutcome: TypeAlias = ToolSucceeded | ToolFailed | ToolCancelled | ToolUnknown


class ToolAccess(Protocol):
    def request_approval(self, request: ToolApprovalRequest) -> ToolApproval:
        ...

    def execute(
        self, request: ToolApprovalRequest, approval: ToolApproval
    ) -> ToolOutcome:
        ...


@dataclass(frozen=True, slots=True)
class ChildTarget:
    label: str
    target: str
    contract_id: str
    input_schema_ids: tuple[str, ...] = ()
    output_schema_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChildPlan:
    target: ChildTarget
    initial_input: ModuleInput


@dataclass(frozen=True, slots=True)
class ChildHandle:
    child_work_id: WorkId
    child_generation_id: GenerationId
    child_instance_id: InstanceId
    child_attempt_id: AttemptId
    parent_work_id: WorkId
    child_label: str


@dataclass(frozen=True, slots=True)
class ChildSucceeded:
    child_work_id: WorkId
    child_attempt_id: AttemptId
    output: OutputEnvelope


@dataclass(frozen=True, slots=True)
class ChildFailed:
    child_work_id: WorkId
    child_attempt_id: AttemptId
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ChildUnknown:
    child_work_id: WorkId
    child_attempt_id: AttemptId
    reason: str


@dataclass(frozen=True, slots=True)
class ChildOutput:
    child_work_id: WorkId
    child_attempt_id: AttemptId
    sequence: OutputSequence
    output: OutputEnvelope


ChildOutcome: TypeAlias = ChildSucceeded | ChildFailed | ChildUnknown


class ChildWorkAccess(Protocol):
    @property
    def targets(self) -> tuple[ChildTarget, ...]:
        ...

    def start(self, plan: ChildPlan) -> ChildHandle:
        ...

    def submit_input(self, handle: ChildHandle, chunk: ModuleInput) -> None:
        ...

    def next_output(self, handle: ChildHandle) -> ChildOutput | ChildOutcome:
        ...

    def join(self, handles: tuple[ChildHandle, ...]) -> tuple[ChildOutcome, ...]:
        ...


@dataclass(frozen=True, slots=True)
class ContextSourceProvenance:
    session_id: str
    trajectory_segment_id: str
    source_sequence_start: int
    source_sequence_end: int


@dataclass(frozen=True, slots=True)
class EffectiveContextDocument:
    encoding: Literal["utf-8-json"]
    body: bytes
    context_sha256: str


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    session_id: str
    context_id: str
    session_event_sequence: int
    effective_context: EffectiveContextDocument
    raw_fact_ids: tuple[str, ...]
    shadowed_raw_fact_ids: tuple[str, ...]
    source: ContextSourceProvenance
    compaction_index: int
    turn_index: int | None


@dataclass(frozen=True, slots=True)
class CompactionProposal:
    expected_context_sha256: str
    compaction_index: int
    source_sequence_start: int
    source_sequence_end: int
    effective_context: EffectiveContextDocument
    raw_fact_ids: tuple[str, ...]
    shadowed_raw_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TurnPolicyDecision:
    kind: Literal["continue", "pause", "complete", "compact"]
    reason: str
    expected_context_sha256: str
    expected_context_sequence: int
    turn_index: int | None
    compaction: CompactionProposal | None


@dataclass(frozen=True, slots=True)
class TurnPolicyReceipt:
    proposal_id: str
    accepted: bool
    context_sequence: int
    applied_context_sha256: str
    refusal_code: str | None


class TurnContextAccess(Protocol):
    def snapshot(self) -> ContextSnapshot:
        ...

    def propose(self, decision: TurnPolicyDecision) -> TurnPolicyReceipt:
        ...


class DependencyBindings:
    """Compiler-bound module handles used to construct a package's SharedT."""

    __slots__ = ("_declarations", "_ports")

    def __init__(
        self,
        declarations: tuple[DependencyDeclaration, ...],
        ports: Mapping[str, DependencyAccess],
    ) -> None:
        names = {item.name for item in declarations}
        if len(names) != len(declarations) or set(ports) != names:
            raise ValueError("dependency ports must match distinct declared names")
        self._declarations = {item.name: item for item in declarations}
        self._ports = dict(ports)

    def dependency(self, name: str, contract_id: str) -> DependencyAccess:
        declaration = self._declarations[name]
        if declaration.contract_id != contract_id:
            raise ValueError(f"dependency contract mismatch for {name!r}")
        return self._ports[name]


class PolicyModule(Protocol[InputT, OutputT, StateT, SharedT]):
    """Author entrypoint imported only inside the admitted worker world."""

    def bind_dependencies(self, bindings: DependencyBindings) -> SharedT:
        """Construct the package's typed aggregate without issuing effects."""
        ...

    def decode_input(self, envelope: InputEnvelope) -> InputT:
        ...

    def decode_output(self, envelope: OutputEnvelope) -> OutputT:
        ...

    def decode_checkpoint(self, envelope: CheckpointEnvelope) -> StateT:
        ...

    def encode_output(self, value: OutputT) -> OutputEnvelope:
        ...

    def encode_checkpoint(
        self,
        state: StateT,
        *,
        source_generation_id: GenerationId,
        source_module_id: ModuleId,
        source_instance_id: InstanceId,
        source_work_id: WorkId,
        source_attempt_id: AttemptId,
        declared_at_sequence: int,
    ) -> CheckpointProposal:
        ...

    def assess_checkpoint(self, source: "CheckpointCompatibilityContext") -> "CheckpointCompatibility":
        ...

    def open_instance(
        self,
        *,
        identity: InstanceIdentity,
        initial_input: InputT,
        dependencies: SharedT,
        children: ChildWorkAccess,
        context: TurnContextAccess,
        providers: ProviderAccess | None,
        tools: ToolAccess | None,
        resume: StateT | None,
    ) -> PolicyInstance[InputT, OutputT, StateT]:
        ...


@dataclass(frozen=True, slots=True)
class CheckpointCompatibilityContext:
    source_generation_id: GenerationId
    source_schema_id: str
    source_state_digest: str
    source_dependencies: tuple[str, ...]
    target_dependencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckpointCompatibility:
    disposition: Literal["compatible", "migrate", "incompatible"]
    target_schema_id: str
    reason: str


# Imported by name only in annotations above.  Keeping these in a separate
# provider module prevents standard-library-only callers from importing provider
# runtime clients.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import (
        ProviderCancelled,
        ProviderExchangeHandle,
        ProviderExchangeRequest,
        ProviderStreamItem,
        ProviderUnknown,
    )


__all__ = [
    "AttemptId", "CheckpointCapture", "CheckpointCompatibility",
    "CheckpointCompatibilityContext", "CheckpointEnvelope", "CheckpointProposal",
    "CheckpointRefusal", "CheckpointRequest", "ChildFailed", "ChildHandle",
    "ChildOutput", "ChildOutcome", "ChildPlan", "ChildSucceeded", "ChildTarget",
    "ChildUnknown", "ChildWorkAccess", "CompactionProposal", "ContinueResult",
    "ContextSnapshot", "ContextSourceProvenance", "DependencyAccess",
    "DependencyBindings", "DependencyDeclaration",
    "EffectiveContextDocument", "FailureResult", "GenerationId", "InputEnvelope",
    "InstanceId", "InstanceIdentity", "JsonValue", "MAX_CHECKPOINT_BYTES",
    "MAX_FRAME_BYTES", "ModuleId", "ModuleInput", "OutputEnvelope", "OutputResult",
    "OutputSequence",
    "PolicyFailure", "PolicyInstance", "PolicyModule", "ProviderAccess",
    "RequestId", "ToolAccess", "ToolApproval", "ToolApprovalRequest", "ToolCancelled",
    "ToolFailed", "ToolOutcome", "ToolSucceeded", "ToolUnknown", "TurnContextAccess",
    "TurnPolicyDecision", "TurnPolicyReceipt", "WorkId", "WorkerSessionId",
]
