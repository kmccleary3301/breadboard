"""Lossless, bounded native Chat Completions response values.

This module deliberately does not normalize tool arguments.  The ordinary
provider contract owns parsed ``ProviderToolCall`` values; the native contract
owns the provider's sampled argument text and the order in which fragments
arrived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

from .contract_wire import ProviderContractError, canonical_json

_MAX_ID_LENGTH = 256
_MAX_TEXT_LENGTH = 16 * 1024 * 1024
_MAX_FRAGMENT_TEXT_LENGTH = 16 * 1024 * 1024


def _require_text(value: Any, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ProviderContractError(f"{field_name} must be non-empty text")
    return value


def _optional_text(value: Any, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise ProviderContractError(f"{field_name} must be text or null")
    return value


def _copy_json_value(value: Any, field_name: str, *, freeze: bool = False) -> Any:
    """Detach JSON usage values; retained samples use immutable containers."""
    if isinstance(value, float) and not isfinite(value):
        raise ProviderContractError(f"{field_name} must contain finite numbers")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderContractError(f"{field_name} keys must be text")
            copied[key] = _copy_json_value(item, f"{field_name}.{key}", freeze=freeze)
        return MappingProxyType(copied) if freeze else copied
    if isinstance(value, (list, tuple)):
        copied_items = [
            _copy_json_value(item, f"{field_name}[]", freeze=freeze) for item in value
        ]
        return tuple(copied_items) if freeze else copied_items
    raise ProviderContractError(f"{field_name} must be JSON-shaped")




@dataclass(frozen=True, slots=True)
class NativeToolCall:
    """One ordered native function call, retaining argument text verbatim."""

    id: str
    name: str
    arguments: str

    def __post_init__(self) -> None:
        _require_text(self.id, "native tool call id", max_length=_MAX_ID_LENGTH)
        _require_text(self.name, "native tool call name", max_length=_MAX_ID_LENGTH)
        if not isinstance(self.arguments, str):
            raise ProviderContractError("native tool call arguments must be text")
        if len(self.arguments.encode("utf-8")) > _MAX_TEXT_LENGTH:
            raise ProviderContractError("native tool call arguments exceed the bound")

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True, slots=True)
class NativeStreamFragment:
    """An ordered content or tool-argument fragment from a native stream."""

    kind: str
    index: int
    text: str
    call_id: str | None = None
    name: str | None = None
    # Source stream ``tool_calls[].index``; ``index`` is the global ordinal.
    tool_index: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"content", "tool_arguments"}:
            raise ProviderContractError("native stream fragment kind is unsupported")
        if type(self.index) is not int or self.index < 0:
            raise ProviderContractError("native stream fragment index is invalid")
        if not isinstance(self.text, str):
            raise ProviderContractError("native stream fragment text must be a string")
        if len(self.text.encode("utf-8")) > _MAX_FRAGMENT_TEXT_LENGTH:
            raise ProviderContractError("native stream fragment text exceeds the bound")
        if self.kind == "tool_arguments":
            if self.call_id is not None:
                _require_text(
                    self.call_id, "native stream fragment call_id", max_length=_MAX_ID_LENGTH
                )
            if self.name is not None:
                _require_text(
                    self.name, "native stream fragment name", max_length=_MAX_ID_LENGTH
                )
            if self.tool_index is not None and (
                type(self.tool_index) is not int or self.tool_index < 0
            ):
                raise ProviderContractError("native stream fragment tool_index is invalid")
        elif self.call_id is not None or self.name is not None or self.tool_index is not None:
            raise ProviderContractError("content fragments cannot carry tool identity")

    @property
    def arguments_text(self) -> str | None:
        return self.text if self.kind == "tool_arguments" else None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "index": self.index, "text": self.text}
        if self.call_id is not None:
            result["call_id"] = self.call_id
        if self.name is not None:
            result["name"] = self.name
        if self.tool_index is not None:
            result["tool_index"] = self.tool_index
        return result


@dataclass(frozen=True, slots=True)
class NativeProviderResponse:
    """Bound, lossless native response delivered to a recording consumer."""

    binding_digest: str
    request_digest: str
    response_id: str
    model: str | None
    content: str | None
    finish_reason: str
    tool_calls: tuple[NativeToolCall, ...] = field(default_factory=tuple)
    usage: Mapping[str, Any] | None = None
    stream_fragments: tuple[NativeStreamFragment, ...] = field(default_factory=tuple)
    raw_response: Mapping[str, Any] | None = None
    request_body: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_text(self.binding_digest, "binding_digest", max_length=256)
        _require_text(self.request_digest, "request_digest", max_length=256)
        _require_text(self.response_id, "response_id", max_length=_MAX_ID_LENGTH)
        _optional_text(self.model, "model", max_length=_MAX_ID_LENGTH)
        _optional_text(self.content, "content", max_length=_MAX_TEXT_LENGTH)
        _require_text(self.finish_reason, "finish_reason", max_length=128)
        if not isinstance(self.tool_calls, tuple):
            raise ProviderContractError("tool_calls must be an ordered tuple")
        if any(not isinstance(item, NativeToolCall) for item in self.tool_calls):
            raise ProviderContractError("tool_calls must contain NativeToolCall values")
        if self.usage is not None:
            copied_usage = _copy_json_value(self.usage, "usage", freeze=True)
            if not isinstance(copied_usage, Mapping):
                raise ProviderContractError("usage must be an object")
            object.__setattr__(self, "usage", copied_usage)
        if self.raw_response is not None:
            raw = _copy_json_value(self.raw_response, "raw_response", freeze=True)
            if not isinstance(raw, Mapping):
                raise ProviderContractError("raw_response must be an object")
            object.__setattr__(self, "raw_response", raw)
        if self.request_body is not None:
            body = _copy_json_value(self.request_body, "request_body", freeze=True)
            if not isinstance(body, Mapping):
                raise ProviderContractError("request_body must be an object")
            object.__setattr__(self, "request_body", body)
        if not isinstance(self.stream_fragments, tuple):
            raise ProviderContractError("stream_fragments must be an ordered tuple")
        if any(not isinstance(item, NativeStreamFragment) for item in self.stream_fragments):
            raise ProviderContractError(
                "stream_fragments must contain NativeStreamFragment values"
            )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "binding_digest": self.binding_digest,
            "request_digest": self.request_digest,
            "response_id": self.response_id,
            "model": self.model,
            "content": self.content,
            "finish_reason": self.finish_reason,
            "tool_calls": [item.as_dict() for item in self.tool_calls],
            "usage": _copy_json_value(self.usage, "usage"),
            "stream_fragments": [item.as_dict() for item in self.stream_fragments],
        }
        if self.raw_response is not None:
            result["raw_response"] = _copy_json_value(self.raw_response, "raw_response")
        if self.request_body is not None:
            result["request_body"] = _copy_json_value(self.request_body, "request_body")
        return result

    def validate_bounds(self, *, max_response_bytes: int, max_stream_fragments: int) -> None:
        if type(max_response_bytes) is not int or not 0 < max_response_bytes <= 16 * 1024 * 1024:
            raise ProviderContractError("max_response_bytes is outside the supported bound")
        if type(max_stream_fragments) is not int or not 0 < max_stream_fragments <= 65536:
            raise ProviderContractError("max_stream_fragments is outside the supported bound")
        if len(self.stream_fragments) > max_stream_fragments:
            raise ProviderContractError("native response stream fragment limit exceeded")
        if len(canonical_json(self.as_dict()).encode("utf-8")) > max_response_bytes:
            raise ProviderContractError("native response byte limit exceeded")


@dataclass(frozen=True, slots=True)
class NativeRecordingConsumer:
    """Bounded recording-only consumer; it never dispatches native tool calls."""

    max_responses: int = 1
    _responses: list[NativeProviderResponse] = field(
        default_factory=list, init=False, repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.max_responses) is not int or not 0 < self.max_responses <= 65536:
            raise ProviderContractError("max_responses is outside the supported bound")

    @property
    def responses(self) -> tuple[NativeProviderResponse, ...]:
        return tuple(self._responses)

    def record(self, response: NativeProviderResponse) -> NativeProviderResponse:
        if not isinstance(response, NativeProviderResponse):
            raise ProviderContractError("native recording requires NativeProviderResponse")
        if len(self._responses) >= self.max_responses:
            raise ProviderContractError("native recording response limit exceeded")
        response.validate_bounds(
            max_response_bytes=16 * 1024 * 1024,
            max_stream_fragments=65536,
        )
        self._responses.append(response)
        return response



__all__ = [
    "NativeProviderResponse",
    "NativeRecordingConsumer",
    "NativeStreamFragment",
    "NativeToolCall",
]
