from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from breadboard.rl.harness.contracts import (
    EffectiveExecutionPlan,
    PolicyCapabilityObservation,
)


_JSON_SCALARS = (str, int, float, bool, type(None))
_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/:+-"
)
_HEX_CHARS = frozenset("0123456789abcdef")
_DEFAULT_JSON_DEPTH = 64
_DEFAULT_JSON_NODES = 100_000


class JsonSnapshotError(ValueError):
    """Fixed closed-JSON snapshot failure safe to expose across runner boundaries."""

    __slots__ = ("code", "encoded_bytes_examined", "string_units_examined")

    def __init__(
        self,
        code: str,
        *,
        encoded_bytes_examined: int | None = None,
        string_units_examined: int | None = None,
    ) -> None:
        messages = {
            "cycle": "JSON value contains a cycle",
            "depth": "JSON value exceeds the nesting depth limit",
            "nodes": "JSON value exceeds the node limit",
            "encoded_bytes": "JSON value exceeds the encoded byte limit",
            "access": "JSON value could not be read safely",
            "key": "JSON value contains a non-string object key",
            "number": "JSON value contains a non-finite number",
            "type": "JSON value contains a non-JSON value",
            "root": "JSON value must be an object",
        }
        super().__init__(messages[code])
        self.code = code
        self.encoded_bytes_examined = encoded_bytes_examined
        self.string_units_examined = string_units_examined


class _FrozenJsonObject(Mapping[str, Any]):
    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return repr(dict(self._values))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and dict(self.items()) == dict(other.items())


FrozenJsonObject: TypeAlias = Mapping[str, Any]


class _JsonByteMeter:
    __slots__ = ("_limit", "size")

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self.size = 0

    def charge(
        self, encoded_bytes: int, *, string_units_examined: int | None = None
    ) -> None:
        if encoded_bytes > self._limit - self.size:
            raise JsonSnapshotError(
                "encoded_bytes",
                encoded_bytes_examined=self._limit + 1,
                string_units_examined=string_units_examined,
            )
        self.size += encoded_bytes


def _freeze_json_snapshot(
    value: Any,
    *,
    max_depth: int,
    max_nodes: int,
    max_encoded_bytes: int | None,
) -> tuple[Any, int | None]:
    active: set[int] = set()
    node_count = [0]
    meter = (
        _JsonByteMeter(max_encoded_bytes)
        if max_encoded_bytes is not None
        else None
    )

    def charge(
        encoded_bytes: int, *, string_units_examined: int | None = None
    ) -> None:
        if meter is not None:
            meter.charge(
                encoded_bytes, string_units_examined=string_units_examined
            )

    def charge_string(item: str) -> None:
        charge(2)
        units_examined = 0
        for character in item:
            units_examined += 1
            codepoint = ord(character)
            if character in {'"', "\\"} or character in {
                "\b",
                "\f",
                "\n",
                "\r",
                "\t",
            }:
                encoded_bytes = 2
            elif codepoint < 0x20 or codepoint > 0x7F:
                encoded_bytes = 6 if codepoint <= 0xFFFF else 12
            else:
                encoded_bytes = 1
            charge(
                encoded_bytes, string_units_examined=units_examined
            )

    def snapshot(item: Any, depth: int) -> Any:
        node_count[0] += 1
        if node_count[0] > max_nodes:
            raise JsonSnapshotError("nodes")
        if depth > max_depth:
            raise JsonSnapshotError("depth")
        if item is None:
            charge(4)
            return None
        if type(item) is bool:
            charge(4 if item else 5)
            return item
        if type(item) is int:
            charge(len(str(item)))
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise JsonSnapshotError("number")
            charge(len(str(item)))
            return item
        if type(item) is str:
            charge_string(item)
            return item
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise JsonSnapshotError("cycle")
            active.add(identity)
            frozen: dict[str, Any] = {}
            charge(1)
            try:
                try:
                    entries = item.items()
                    for index, (key, child) in enumerate(entries):
                        if type(key) is not str:
                            raise JsonSnapshotError("key")
                        if index:
                            charge(1)
                        charge_string(key)
                        charge(1)
                        frozen[key] = snapshot(child, depth + 1)
                except JsonSnapshotError:
                    raise
                except Exception as exc:
                    raise JsonSnapshotError("access") from exc
            finally:
                active.remove(identity)
            charge(1)
            return _FrozenJsonObject(frozen)
        if type(item) in {list, tuple}:
            identity = id(item)
            if identity in active:
                raise JsonSnapshotError("cycle")
            active.add(identity)
            frozen_items: list[Any] = []
            charge(1)
            try:
                for index, child in enumerate(item):
                    if index:
                        charge(1)
                    frozen_items.append(snapshot(child, depth + 1))
            finally:
                active.remove(identity)
            charge(1)
            return tuple(frozen_items)
        raise JsonSnapshotError("type")

    frozen_value = snapshot(value, 1)
    return frozen_value, meter.size if meter is not None else None


def freeze_json(
    value: Any,
    *,
    field_name: str = "JSON value",
    max_depth: int = _DEFAULT_JSON_DEPTH,
    max_nodes: int = _DEFAULT_JSON_NODES,
    max_encoded_bytes: int | None = None,
) -> Any:
    """Take one recursive, closed-JSON snapshot under explicit resource budgets."""
    del field_name
    if type(max_depth) is not int or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    if max_encoded_bytes is not None and (
        type(max_encoded_bytes) is not int or max_encoded_bytes < 1
    ):
        raise ValueError("max_encoded_bytes must be a positive integer")
    frozen_value, _ = _freeze_json_snapshot(
        value,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_encoded_bytes=max_encoded_bytes,
    )
    return frozen_value


def freeze_json_with_size(
    value: Any,
    *,
    field_name: str = "JSON value",
    max_depth: int = _DEFAULT_JSON_DEPTH,
    max_nodes: int = _DEFAULT_JSON_NODES,
    max_encoded_bytes: int,
) -> tuple[Any, int]:
    del field_name
    if type(max_depth) is not int or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    if type(max_encoded_bytes) is not int or max_encoded_bytes < 1:
        raise ValueError("max_encoded_bytes must be a positive integer")
    frozen, size = _freeze_json_snapshot(
        value,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_encoded_bytes=max_encoded_bytes,
    )
    assert size is not None
    return frozen, size


def freeze_json_object(
    value: Mapping[str, Any],
    *,
    field_name: str,
    max_depth: int = _DEFAULT_JSON_DEPTH,
    max_nodes: int = _DEFAULT_JSON_NODES,
    max_encoded_bytes: int | None = None,
) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise JsonSnapshotError("root")
    frozen = freeze_json(
        value,
        field_name=field_name,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_encoded_bytes=max_encoded_bytes,
    )
    assert isinstance(frozen, _FrozenJsonObject)
    return frozen


def freeze_json_object_with_size(
    value: Mapping[str, Any],
    *,
    field_name: str,
    max_depth: int = _DEFAULT_JSON_DEPTH,
    max_nodes: int = _DEFAULT_JSON_NODES,
    max_encoded_bytes: int,
) -> tuple[FrozenJsonObject, int]:
    if not isinstance(value, Mapping):
        raise JsonSnapshotError("root")
    frozen, size = freeze_json_with_size(
        value,
        field_name=field_name,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_encoded_bytes=max_encoded_bytes,
    )
    assert isinstance(frozen, _FrozenJsonObject)
    return frozen, size


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


def _normalized_identifier(value: str, *, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a nonempty normalized identifier")
    if any(character not in _IDENTIFIER_CHARS for character in value):
        raise ValueError(f"{field_name} contains an invalid character")
    return value


def _implementation_digest(value: str) -> str:
    prefix = "sha256:"
    payload = value[len(prefix) :] if type(value) is str and value.startswith(prefix) else ""
    if len(payload) != 64 or any(character not in _HEX_CHARS for character in payload):
        raise ValueError("implementation_digest must be a full lowercase sha256 digest")
    return value

def _validate_event_identity(
    sequence: int, episode_id: str, effective_plan_digest: str
) -> None:
    if type(sequence) is not int or sequence < 0:
        raise ValueError("event sequence must be a nonnegative integer")
    _normalized_identifier(episode_id, field_name="episode_id")
    _implementation_digest(effective_plan_digest)



def _positive_turn(value: int | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not int or value < 1:
        raise ValueError("turn must be a positive integer")


def _optional_nonempty_text(value: str | None, *, field_name: str) -> None:
    if value is not None and (type(value) is not str or not value):
        raise ValueError(f"{field_name} must be None or a nonempty string")


def _nonempty_text(value: str, *, field_name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a nonempty string")

@dataclass(frozen=True, slots=True)
class RunnerAdapterDescriptor:
    adapter_id: str
    runtime_abi: str
    implementation_digest: str

    def __post_init__(self) -> None:
        try:
            _normalized_identifier(self.adapter_id, field_name="adapter_id")
            _normalized_identifier(self.runtime_abi, field_name="runtime_abi")
            _implementation_digest(self.implementation_digest)
        except ValueError as exc:
            raise RunnerRegistrationError(
                str(exc), code="malformed_descriptor"
            ) from exc


@dataclass(frozen=True, slots=True)
class RunnerToolBinding:
    tool_id: str
    implementation_digest: str
    capability_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _normalized_identifier(self.tool_id, field_name="tool_id")
        _implementation_digest(self.implementation_digest)
        capability_ids = tuple(self.capability_ids)
        if capability_ids != tuple(sorted(set(capability_ids))):
            raise ValueError("capability_ids must be sorted and unique")
        for capability_id in capability_ids:
            _normalized_identifier(capability_id, field_name="capability_id")
        object.__setattr__(self, "capability_ids", capability_ids)


@dataclass(frozen=True, slots=True)
class RunnerOpenRequest:
    episode_id: str
    effective_plan: EffectiveExecutionPlan
    effective_plan_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _normalized_identifier(self.episode_id, field_name="episode_id")
        if type(self.effective_plan) is not EffectiveExecutionPlan:
            raise TypeError("effective_plan must be an exact EffectiveExecutionPlan")
        object.__setattr__(
            self, "effective_plan_digest", self.effective_plan.canonical_digest()
        )


@runtime_checkable
class RunnerRunRequest(Protocol):
    """Marker for an adapter-specific, recursively immutable run request."""


class RunnerTermination(str, Enum):
    MAX_TURNS = "max_turns"
    POLICY_INCOMPLETE = "policy_incomplete"
    INVALID_POLICY_OUTPUT = "invalid_policy_output"
    ASSISTANT_COMPLETE = "assistant_complete"
    SUBMITTED = "submitted"


@dataclass(frozen=True, slots=True)
class RunnerTurn:
    turn: int
    policy_output: tuple[FrozenJsonObject, ...]
    observations: tuple[FrozenJsonObject, ...] = ()

    def __post_init__(self) -> None:
        if type(self.turn) is not int or self.turn < 1:
            raise ValueError("turn must be a positive integer")
        object.__setattr__(
            self,
            "policy_output",
            tuple(
                freeze_json_object(item, field_name="policy output")
                for item in self.policy_output
            ),
        )
        object.__setattr__(
            self,
            "observations",
            tuple(
                freeze_json_object(item, field_name="tool observation")
                for item in self.observations
            ),
        )


@dataclass(frozen=True, slots=True)
class PolicyRuntimeInvokeRequest:
    episode_id: str
    effective_plan_digest: str
    binding_digest: str
    policy_slot_id: str
    request_digest: str
    request_payload: FrozenJsonObject
    turn: int
    attempt: int

    def __post_init__(self) -> None:
        _normalized_identifier(self.episode_id, field_name="episode_id")
        _implementation_digest(self.effective_plan_digest)
        _implementation_digest(self.binding_digest)
        _normalized_identifier(self.policy_slot_id, field_name="policy_slot_id")
        _implementation_digest(self.request_digest)
        _positive_turn(self.turn)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        object.__setattr__(
            self,
            "request_payload",
            freeze_json_object(self.request_payload, field_name="policy runtime request"),
        )


@dataclass(frozen=True, slots=True)
class PolicyRuntimeInvokeResult:
    response_payload: FrozenJsonObject
    response_digest: str

    def __post_init__(self) -> None:
        _implementation_digest(self.response_digest)
        object.__setattr__(
            self,
            "response_payload",
            freeze_json_object(self.response_payload, field_name="policy runtime response"),
        )


@dataclass(frozen=True, slots=True)
class PolicyRuntimeRequestEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turn: int
    attempt: int
    binding_digest: str
    policy_capability_observation_digest: str
    policy_slot_id: str
    request_digest: str
    first_request_digest: str
    trainable_values: FrozenJsonObject

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turn)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        for digest in (
            self.binding_digest,
            self.policy_capability_observation_digest,
            self.request_digest,
            self.first_request_digest,
        ):
            _implementation_digest(digest)
        _normalized_identifier(self.policy_slot_id, field_name="policy_slot_id")
        object.__setattr__(
            self,
            "trainable_values",
            freeze_json_object(self.trainable_values, field_name="trainable values"),
        )


@dataclass(frozen=True, slots=True)
class PolicyRuntimeResponseEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turn: int
    attempt: int
    binding_digest: str
    policy_slot_id: str
    request_digest: str
    response_digest: str

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turn)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        for digest in (self.binding_digest, self.request_digest, self.response_digest):
            _implementation_digest(digest)
        _normalized_identifier(self.policy_slot_id, field_name="policy_slot_id")


@dataclass(frozen=True, slots=True)
class PolicyRequestEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turn: int
    request_payload: FrozenJsonObject

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turn)
        object.__setattr__(self, "request_payload", freeze_json_object(self.request_payload, field_name="policy request"))

@dataclass(frozen=True, slots=True)
class PolicyResponseEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turn: int
    response_payload: FrozenJsonObject
    normalized_output: tuple[FrozenJsonObject, ...]

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turn)
        object.__setattr__(self, "response_payload", freeze_json_object(self.response_payload, field_name="policy response"))
        object.__setattr__(self, "normalized_output", tuple(freeze_json_object(item, field_name="policy output") for item in self.normalized_output))

@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turn: int
    ordinal: int
    call_id: str
    tool_name: str
    arguments_json: str

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turn)
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("tool call ordinal must be a nonnegative integer")
        _nonempty_text(self.call_id, field_name="call_id")
        if type(self.tool_name) is not str or type(self.arguments_json) is not str:
            raise TypeError("tool_name and arguments_json must be strings")

@dataclass(frozen=True, slots=True)
class ToolObservationEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turn: int
    ordinal: int
    call_id: str
    tool_name: str
    observation: FrozenJsonObject
    submitted: bool
    error_type: str | None = None

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turn)
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("tool observation ordinal must be a nonnegative integer")
        _nonempty_text(self.call_id, field_name="call_id")
        if type(self.tool_name) is not str:
            raise TypeError("tool_name must be a string")
        if type(self.submitted) is not bool:
            raise TypeError("submitted must be a bool")
        _optional_nonempty_text(self.error_type, field_name="error_type")
        object.__setattr__(self, "observation", freeze_json_object(self.observation, field_name="tool observation event"))

@dataclass(frozen=True, slots=True)
class RunnerTerminationEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    turns: int
    reason: RunnerTermination

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _positive_turn(self.turns)
        if type(self.reason) is not RunnerTermination:
            raise TypeError("termination reason must be RunnerTermination")

@dataclass(frozen=True, slots=True)
class RunnerCancellationRequestedEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    reason: str

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _nonempty_text(self.reason, field_name="cancellation reason")

@dataclass(frozen=True, slots=True)
class RunnerCancellationObservedEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    reason: str
    checkpoint: str
    turn: int | None = None
    call_id: str | None = None

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _nonempty_text(self.reason, field_name="cancellation reason")
        _nonempty_text(self.checkpoint, field_name="cancellation checkpoint")
        _positive_turn(self.turn, optional=True)
        _optional_nonempty_text(self.call_id, field_name="call_id")

@dataclass(frozen=True, slots=True)
class RunnerErrorEvent:
    sequence: int
    episode_id: str
    effective_plan_digest: str
    category: str
    code: str
    message: str
    turn: int | None = None
    call_id: str | None = None

    def __post_init__(self) -> None:
        _validate_event_identity(self.sequence, self.episode_id, self.effective_plan_digest)
        _nonempty_text(self.category, field_name="error category")
        _nonempty_text(self.code, field_name="error code")
        _nonempty_text(self.message, field_name="error message")
        _positive_turn(self.turn, optional=True)
        _optional_nonempty_text(self.call_id, field_name="call_id")

RunnerEvent: TypeAlias = (
    PolicyRequestEvent
    | PolicyResponseEvent
    | PolicyRuntimeRequestEvent
    | PolicyRuntimeResponseEvent
    | ToolCallEvent
    | ToolObservationEvent
    | RunnerTerminationEvent
    | RunnerCancellationRequestedEvent
    | RunnerCancellationObservedEvent
    | RunnerErrorEvent
)


@dataclass(frozen=True, slots=True)
class RunnerResult:
    episode_id: str
    effective_plan_digest: str
    original_request: FrozenJsonObject
    response: FrozenJsonObject
    termination: RunnerTermination
    turn_count: int
    turns: tuple[RunnerTurn, ...]
    events: tuple[RunnerEvent, ...]

    def __post_init__(self) -> None:
        _normalized_identifier(self.episode_id, field_name="episode_id")
        _implementation_digest(self.effective_plan_digest)
        object.__setattr__(self, "original_request", freeze_json_object(self.original_request, field_name="original request"))
        object.__setattr__(self, "response", freeze_json_object(self.response, field_name="runner response"))
        turns = tuple(self.turns)
        events = tuple(self.events)
        if any(type(turn) is not RunnerTurn for turn in turns):
            raise TypeError("result turns must contain only RunnerTurn values")
        event_types = (
            PolicyRequestEvent,
            PolicyResponseEvent,
            PolicyRuntimeRequestEvent,
            PolicyRuntimeResponseEvent,
            ToolCallEvent,
            ToolObservationEvent,
            RunnerTerminationEvent,
            RunnerCancellationRequestedEvent,
            RunnerCancellationObservedEvent,
            RunnerErrorEvent,
        )
        if any(type(event) not in event_types for event in events):
            raise TypeError("result events must contain only closed RunnerEvent values")
        object.__setattr__(self, "turns", turns)
        object.__setattr__(self, "events", events)
        if type(self.turn_count) is not int or self.turn_count < 1 or self.turn_count != len(turns):
            raise ValueError("turn_count must equal the number of turns")
        if type(self.termination) is not RunnerTermination:
            raise TypeError("termination must be RunnerTermination")
        if tuple(turn.turn for turn in turns) != tuple(range(1, self.turn_count + 1)):
            raise ValueError("result turns must be contiguous and ordered")
        termination_events = 0
        for sequence, event in enumerate(events):
            if event.sequence != sequence or event.episode_id != self.episode_id or event.effective_plan_digest != self.effective_plan_digest:
                raise ValueError("result events must have contiguous sequence and matching identity")
            event_turn = (
                event.turn
                if type(event) in {
                    PolicyRequestEvent,
                    PolicyResponseEvent,
                    PolicyRuntimeRequestEvent,
                    PolicyRuntimeResponseEvent,
                    ToolCallEvent,
                    ToolObservationEvent,
                    RunnerCancellationObservedEvent,
                    RunnerErrorEvent,
                }
                else None
            )
            if event_turn is not None and (
                type(event_turn) is not int or not 1 <= event_turn <= self.turn_count
            ):
                raise ValueError("result event turn is outside the result turn range")
            if type(event) is RunnerTerminationEvent:
                termination_events += 1
                if event.turns != self.turn_count or event.reason is not self.termination:
                    raise ValueError("result termination event does not match the result")
        if termination_events != 1:
            raise ValueError("successful result must contain exactly one termination event")


@dataclass(frozen=True, slots=True)
class RunnerCancellation:
    reason: str
    requested: bool
    observed_checkpoint: str | None = None
    turn: int | None = None
    call_id: str | None = None


    def __post_init__(self) -> None:
        _nonempty_text(self.reason, field_name="cancellation reason")
        if type(self.requested) is not bool:
            raise TypeError("requested must be a bool")
        _optional_nonempty_text(
            self.observed_checkpoint, field_name="observed_checkpoint"
        )
        _positive_turn(self.turn, optional=True)
        _optional_nonempty_text(self.call_id, field_name="call_id")
        if self.observed_checkpoint is not None and not self.requested:
            raise ValueError("an observed cancellation must have been requested")

@dataclass(frozen=True, slots=True)
class RunnerCloseResult:
    already_closed: bool
    cancellation: RunnerCancellation | None = None


    def __post_init__(self) -> None:
        if type(self.already_closed) is not bool:
            raise TypeError("already_closed must be a bool")
        if self.cancellation is not None and type(self.cancellation) is not RunnerCancellation:
            raise TypeError("cancellation must be RunnerCancellation or None")

class RunnerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        episode_id: str | None = None,
        effective_plan_digest: str | None = None,
        events_so_far: Iterable[RunnerEvent] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.episode_id = episode_id
        self.effective_plan_digest = effective_plan_digest
        self.events_so_far = tuple(events_so_far)
        self.category = type(self).category


class RunnerRegistrationError(RunnerError):
    category = "registry"


class RunnerResolutionError(RunnerError):
    category = "registry"


class RunnerPlanError(RunnerError):
    category = "plan"


class RunnerPolicyBindingError(RunnerError):
    category = "policy_binding"


class RunnerRequestError(RunnerError):
    category = "request"


class RunnerProtocolError(RunnerError):
    category = "protocol"


class RunnerDependencyError(RunnerError):
    category = "dependency"


class RunnerEventSinkError(RunnerError):
    category = "event_sink"

    def __init__(
        self,
        message: str,
        *,
        failed_event: RunnerEvent,
        cause: Exception,
        episode_id: str | None = None,
        effective_plan_digest: str | None = None,
        events_so_far: Iterable[RunnerEvent] = (),
    ) -> None:
        super().__init__(
            message,
            code="event_sink_failed",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
            events_so_far=events_so_far,
        )
        self.failed_event = failed_event
        self.__cause__ = cause


class RunnerStateError(RunnerError):
    category = "state"


class RunnerCancelled(RunnerError):
    category = "cancellation"

    def __init__(
        self,
        cancellation: RunnerCancellation,
        *,
        episode_id: str | None = None,
        effective_plan_digest: str | None = None,
        events_so_far: Iterable[RunnerEvent] = (),
    ) -> None:
        super().__init__(
            cancellation.reason,
            code="cancelled",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
            events_so_far=events_so_far,
        )
        self.cancellation = cancellation


@runtime_checkable
class PolicyGeneratePort(Protocol):
    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class PolicyRuntimeClientPort(Protocol):
    def observe(self) -> PolicyCapabilityObservation: ...
    async def invoke(
        self, request: PolicyRuntimeInvokeRequest
    ) -> PolicyRuntimeInvokeResult: ...
    async def cancel(self, reason: str) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class PolicyRuntimeBindingPort(Protocol):
    @property
    def episode_id(self) -> str: ...
    @property
    def effective_plan_digest(self) -> str: ...
    @property
    def binding_digest(self) -> str: ...
    @property
    def policy_capability_observation(self) -> PolicyCapabilityObservation: ...
    @property
    def policy_slot_ids(self) -> tuple[str, ...]: ...
    @property
    def first_request_digest(self) -> str | None: ...
    async def invoke(
        self, request: PolicyRuntimeInvokeRequest
    ) -> PolicyRuntimeInvokeResult: ...
    async def cancel(self, reason: str) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class ConductorToolPort(Protocol):
    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]: ...
    async def invoke_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class RunnerWorkspacePort(Protocol):
    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]: ...

    async def run_shell(self, command: str, *, timeout: int) -> Mapping[str, Any]: ...
    async def read_text(self, path: str, *, offset: int = 0, limit: int | None = None) -> Mapping[str, Any]: ...
    async def write_text(self, path: str, content: str) -> Mapping[str, Any]: ...
    async def list_files(self, path: str, *, depth: int) -> Mapping[str, Any]: ...


@runtime_checkable
class RunnerCancellationProbe(Protocol):
    def raise_if_cancelled(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None: ...


@runtime_checkable
class RunnerEventSink(Protocol):
    async def emit(self, event: RunnerEvent) -> None: ...


RunRequestT = TypeVar("RunRequestT", bound=RunnerRunRequest)


@runtime_checkable
class RunnerSession(Protocol, Generic[RunRequestT]):
    async def run(self, request: RunRequestT) -> RunnerResult: ...
    async def cancel(self, reason: str) -> RunnerCancellation: ...
    async def close(self) -> RunnerCloseResult: ...


@runtime_checkable
class RunnerAdapter(Protocol, Generic[RunRequestT]):
    @property
    def descriptor(self) -> RunnerAdapterDescriptor: ...

    async def open(
        self,
        request: RunnerOpenRequest,
        *,
        policy: PolicyGeneratePort | PolicyRuntimeBindingPort,
        workspace: RunnerWorkspacePort | ConductorToolPort,
        cancellation: RunnerCancellationProbe,
        events: RunnerEventSink,
    ) -> RunnerSession[RunRequestT]: ...


class RunnerAdapterRegistry:
    __slots__ = ("_adapters", "_adapter_ids")

    def __init__(self, adapters: Iterable[RunnerAdapter[Any]]) -> None:
        installed: dict[
            tuple[str, str], tuple[RunnerAdapter[Any], RunnerAdapterDescriptor]
        ] = {}
        adapter_ids: set[str] = set()
        for adapter in adapters:
            try:
                descriptor = adapter.descriptor
            except Exception as exc:
                error = RunnerRegistrationError(
                    "runner adapter descriptor is malformed",
                    code="malformed_descriptor",
                )
                error.__cause__ = exc
                raise error
            if type(descriptor) is not RunnerAdapterDescriptor:
                raise RunnerRegistrationError(
                    "runner adapter descriptor is malformed",
                    code="malformed_descriptor",
                )
            snapshot = RunnerAdapterDescriptor(
                adapter_id=descriptor.adapter_id,
                runtime_abi=descriptor.runtime_abi,
                implementation_digest=descriptor.implementation_digest,
            )
            key = (snapshot.adapter_id, snapshot.runtime_abi)
            if key in installed:
                raise RunnerRegistrationError(
                    f"duplicate runner adapter registration for {snapshot.adapter_id!r} and {snapshot.runtime_abi!r}",
                    code="duplicate_adapter",
                )
            installed[key] = (adapter, snapshot)
            adapter_ids.add(snapshot.adapter_id)
        self._adapters = MappingProxyType(installed)
        self._adapter_ids = frozenset(adapter_ids)

    def resolve(self, adapter_id: str, runtime_abi: str) -> RunnerAdapter[Any]:
        registered = self._adapters.get((adapter_id, runtime_abi))
        if registered is not None:
            adapter, snapshot = registered
            try:
                current = adapter.descriptor
            except Exception as exc:
                error = RunnerResolutionError(
                    "runner adapter descriptor changed after registration",
                    code="descriptor_drift",
                )
                error.__cause__ = exc
                raise error
            if type(current) is not RunnerAdapterDescriptor or current != snapshot:
                raise RunnerResolutionError(
                    "runner adapter descriptor changed after registration",
                    code="descriptor_drift",
                )
            return adapter
        if adapter_id in self._adapter_ids:
            raise RunnerResolutionError(
                f"runner adapter {adapter_id!r} does not support runtime ABI {runtime_abi!r}",
                code="runtime_abi_not_supported",
            )
        raise RunnerResolutionError(
            f"runner adapter {adapter_id!r} is not registered",
            code="adapter_not_found",
        )
