"""Shared native Chat stream-consumer seam.

Transport and compiler admission retain immutable ``NativeProviderResponse``
values.  Profile consumers own the only lossy boundary: reconstruction,
validation, history mutations, and terminal arbitration. Effects remain in the lease.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from breadboard_engine.provider.native_response import (
    NativeProviderResponse,
    NativeStreamFragment,
    NativeToolCall,
)


@dataclass(frozen=True, slots=True)
class NativeToolBatchCall:
    """One ordered, validated call returned by a native response consumer."""

    call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.call_id) is not str or not self.call_id:
            raise ValueError("native tool batch call id must be non-empty text")
        if type(self.name) is not str or not self.name:
            raise ValueError("native tool batch call name must be non-empty text")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("native tool batch call arguments must be an object")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True, slots=True)
class NativeTerminalMetadata:
    """Native and public terminal facts kept distinct by the consumer."""

    native_stop_reason: str | None
    public_stop: str | None
    terminal: bool


@dataclass(frozen=True, slots=True)
class NativeResponseProjection:
    """Consumer-owned projection of one immutable native response."""

    tool_batch: tuple[NativeToolBatchCall, ...]
    history_mutations: tuple[Mapping[str, Any], ...]
    terminal: NativeTerminalMetadata
    response_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(call, NativeToolBatchCall) for call in self.tool_batch):
            raise TypeError("tool_batch must contain NativeToolBatchCall values")
        if any(not isinstance(item, Mapping) for item in self.history_mutations):
            raise TypeError("history_mutations must contain mappings")
        object.__setattr__(
            self,
            "history_mutations",
            tuple(MappingProxyType(dict(item)) for item in self.history_mutations),
        )


class NativeStreamConsumer(Protocol):
    """Minimal profile consumer protocol used by the conductor."""

    consumer_id: str

    def consume(self, response: NativeProviderResponse) -> NativeResponseProjection:
        ...


def native_response_from_dict(value: Mapping[str, Any]) -> NativeProviderResponse:
    """Reconstruct a validated immutable response at the conductor boundary."""

    if not isinstance(value, Mapping):
        raise TypeError("native response must be an object")
    raw_calls = value.get("tool_calls", ())
    raw_fragments = value.get("stream_fragments", ())
    if not isinstance(raw_calls, (list, tuple)) or not isinstance(raw_fragments, (list, tuple)):
        raise ValueError("native response ordered fields are malformed")
    calls = tuple(
        item
        if isinstance(item, NativeToolCall)
        else NativeToolCall(item["id"], item["name"], item["arguments"])
        for item in raw_calls
    )
    fragments = tuple(
        item
        if isinstance(item, NativeStreamFragment)
        else NativeStreamFragment(
            item["kind"],
            item["index"],
            item["text"],
            item.get("call_id"),
            item.get("name"),
            item.get("tool_index"),
        )
        for item in raw_fragments
    )
    return NativeProviderResponse(
        binding_digest=value["binding_digest"],
        request_digest=value["request_digest"],
        response_id=value["response_id"],
        model=value.get("model"),
        content=value.get("content"),
        finish_reason=value["finish_reason"],
        tool_calls=calls,
        usage=value.get("usage"),
        raw_response=value.get("raw_response"),
        stream_fragments=fragments,
        request_body=value.get("request_body"),
    )


def consume_pi_response(state: Any, response: NativeProviderResponse) -> NativeResponseProjection:
    """Consume one response through the Pi profile state and expose the seam facts."""

    from breadboard.rl.harness.runners.pi_semantics import PiSemanticsState

    if not isinstance(state, PiSemanticsState):
        raise TypeError("Pi consumer requires PiSemanticsState")
    before = len(state.messages)
    result = state.prepare_response(response)
    mutations = tuple(state.messages[before:])
    calls = tuple(
        NativeToolBatchCall(call.id, call.name, call.arguments)
        for call in result.calls
    )
    return NativeResponseProjection(
        tool_batch=calls,
        history_mutations=mutations,
        terminal=NativeTerminalMetadata(
            native_stop_reason=state.native_stop_reason,
            public_stop=state.exit_status,
            terminal=state.is_exited,
        ),
        response_id=response.response_id,
    )


__all__ = [
    "NativeResponseProjection",
    "NativeStreamConsumer",
    "NativeTerminalMetadata",
    "NativeToolBatchCall",
    "consume_pi_response",
    "native_response_from_dict",
]
