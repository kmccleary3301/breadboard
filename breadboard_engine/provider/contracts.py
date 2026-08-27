"""Strict provider runtime and provider-exchange contracts.

The provider runtime may use SDK-native objects while a request is in flight, but
nothing crossing this module's persistence boundary may contain an SDK object,
raw exception text, or an unvalidated JSON fragment.  ``ProviderExchangeV2`` is
the single normalized aggregate owned by :class:`ProviderInvoker`.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from ..security import redaction
from .routing import ProviderDescriptor

ProviderRole = Literal["system", "user", "developer", "assistant", "tool_result"]
_CONTENT_TYPES = {
    "text",
    "media",
    "thinking",
    "redacted_thinking",
    "tool_call",
    "tool_result",
    "provider_replay",
}
_EVENT_KINDS = {
    "response_start",
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_end",
}
_OUTPUT_EVENT_KINDS = {
    "text_delta",
    "thinking_delta",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_end",
}
_EVENT_INPUT_FIELDS = {
    "response_start": frozenset(),
    "text_start": frozenset({"content_index", "index", "message_id", "item_id"}),
    "text_delta": frozenset(
        {"content_index", "index", "message_id", "item_id", "delta", "text"}
    ),
    "text_end": frozenset(
        {"content_index", "index", "message_id", "item_id", "text"}
    ),
    "thinking_start": frozenset(
        {"content_index", "index", "message_id", "item_id"}
    ),
    "thinking_delta": frozenset(
        {
            "content_index",
            "index",
            "message_id",
            "item_id",
            "delta",
            "text",
            "provider_field",
        }
    ),
    "thinking_end": frozenset(
        {"content_index", "index", "message_id", "item_id"}
    ),
    "tool_call_start": frozenset(
        {
            "content_index",
            "index",
            "message_id",
            "item_id",
            "call_id",
            "name",
            "tool",
        }
    ),
    "tool_call_delta": frozenset(
        {
            "content_index",
            "index",
            "message_id",
            "item_id",
            "call_id",
            "name",
            "tool",
            "delta",
            "text",
            "arguments_delta",
        }
    ),
    "tool_call_end": frozenset(
        {
            "content_index",
            "index",
            "message_id",
            "item_id",
            "call_id",
            "name",
            "tool",
            "arguments_json",
            "arguments",
            "parsed_arguments",
            "arguments_parsed",
        }
    ),
}
_FINISH_REASONS = {"stop", "length", "toolUse", "error", "aborted"}
_USAGE_KEYS = {
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "totalTokens",
    "reasoningTokens",
    "extensions",
}
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ProviderContractError(ValueError):
    """Raised when provider data cannot be represented without loss."""


class ProviderProtocolError(ProviderContractError):
    """Raised for a malformed or unknown normative provider event."""


@lru_cache(maxsize=1)
def _provider_exchange_v2_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "kernel"
        / "schemas"
        / "bb.provider_exchange.v2.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_provider_exchange_v2_wire(value: Any) -> None:
    errors = sorted(
        _provider_exchange_v2_validator().iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    pointer = "".join(f"/{part}" for part in error.absolute_path) or "/"
    raise ProviderContractError(
        f"provider exchange violates bb.provider_exchange.v2 at {pointer} "
        f"({error.validator})"
    )


def canonical_json(value: Any) -> str:
    """Encode a JSON value canonically, rejecting SDK objects and NaN values."""
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        raise ProviderContractError("JSON payload must be a parsed JSON value")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ProviderContractError("JSON object keys must be strings")
        candidate = {key: _canonical_value(item) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        candidate = [_canonical_value(item) for item in value]
    elif value is None or isinstance(value, (bool, int, float)):
        candidate = _canonical_value(value)
    else:
        raise ProviderContractError(
            f"unsupported JSON value type: {type(value).__name__}"
        )
    try:
        return json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProviderContractError("JSON payload is not canonical JSON") from exc


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProviderContractError("JSON payload contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ProviderContractError("JSON object keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise ProviderContractError(f"unsupported JSON value type: {type(value).__name__}")


def parse_canonical_json(value: str) -> Any:
    """Parse a canonical JSON string and require its exact canonical spelling."""
    if not isinstance(value, str) or not value:
        raise ProviderContractError("JSON text must be nonempty")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ProviderContractError("malformed JSON payload") from exc
    if canonical_json(parsed) != value:
        raise ProviderContractError("JSON payload is not canonical")
    return parsed

_MAX_INTEROPERABLE_JSON_INTEGER = (1 << 53) - 1


def _validate_interoperable_json_numbers(value: Any, *, field_name: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        if abs(value) > _MAX_INTEROPERABLE_JSON_INTEGER:
            raise ProviderContractError(
                f"{field_name} contains an integer outside the interoperable JSON range"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProviderContractError(f"{field_name} contains a non-finite number")
        if (
            value.is_integer()
            and abs(value) > _MAX_INTEROPERABLE_JSON_INTEGER
        ):
            raise ProviderContractError(
                f"{field_name} contains an integer outside the interoperable JSON range"
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_interoperable_json_numbers(
                item, field_name=f"{field_name}.{key}"
            )
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_interoperable_json_numbers(
                item, field_name=f"{field_name}[{index}]"
            )
        return
    raise ProviderContractError(
        f"{field_name} contains unsupported JSON value type: {type(value).__name__}"
    )


def _validate_canonical_argument_pair(
    arguments_json: Any,
    arguments: Any,
    *,
    field_name: str,
) -> None:
    parsed = parse_canonical_json(arguments_json)
    _validate_interoperable_json_numbers(
        parsed, field_name=f"{field_name}.arguments_json"
    )
    _validate_interoperable_json_numbers(
        arguments, field_name=f"{field_name}.arguments"
    )
    if _canonical_value(arguments) != parsed:
        raise ProviderContractError(
            f"{field_name} arguments disagree with arguments_json"
        )


def _validate_provider_replay_wire(value: Any, *, field_name: str) -> None:
    if not isinstance(value, Mapping):
        return
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        return
    for key in (
        "encrypted_content",
        "signature",
        "redacted_data",
        "item_id",
        "reasoning_id",
    ):
        if key in payload and payload[key] is None:
            raise ProviderContractError(
                f"{field_name}.payload.{key} must be omitted instead of null"
            )


def _validate_content_wire(value: Any, *, field_name: str) -> None:
    if not isinstance(value, list):
        return
    for index, block in enumerate(value):
        if not isinstance(block, Mapping):
            continue
        block_path = f"{field_name}[{index}]"
        if block.get("type") == "tool_call":
            _validate_canonical_argument_pair(
                block.get("arguments_json"),
                block.get("arguments"),
                field_name=block_path,
            )
        elif block.get("type") == "provider_replay":
            _validate_provider_replay_wire(block, field_name=block_path)


def _validate_exchange_wire_semantics(value: Mapping[str, Any]) -> None:
    request = value.get("request")
    if isinstance(request, Mapping):
        messages = request.get("messages")
        if isinstance(messages, list):
            for index, message in enumerate(messages):
                if isinstance(message, Mapping):
                    _validate_content_wire(
                        message.get("content"),
                        field_name=f"request.messages[{index}].content",
                    )
    events = value.get("events")
    if isinstance(events, list):
        for index, event in enumerate(events):
            if (
                isinstance(event, Mapping)
                and event.get("kind") == "tool_call_end"
            ):
                _validate_canonical_argument_pair(
                    event.get("arguments_json"),
                    event.get("arguments"),
                    field_name=f"events[{index}]",
                )
    terminal = value.get("terminal")
    if not isinstance(terminal, Mapping):
        return
    assistant_messages = terminal.get("assistant_messages")
    if isinstance(assistant_messages, list):
        for index, message in enumerate(assistant_messages):
            if isinstance(message, Mapping):
                _validate_content_wire(
                    message.get("content"),
                    field_name=f"terminal.assistant_messages[{index}].content",
                )
    provider_replay = terminal.get("provider_replay")
    if isinstance(provider_replay, list):
        for index, replay in enumerate(provider_replay):
            _validate_provider_replay_wire(
                replay, field_name=f"terminal.provider_replay[{index}]"
            )


def _require_text(
    value: Any, field_name: str, *, max_length: Optional[int] = None
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderContractError(f"{field_name} must be a nonempty string")
    if max_length is not None and len(value) > max_length:
        raise ProviderContractError(f"{field_name} exceeds {max_length} characters")
    return value


def _strict_dict(
    value: Any, *, field_name: str, allowed: Iterable[str]
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderContractError(f"{field_name} must be an object")
    allowed_set = set(allowed)
    unknown = set(value) - allowed_set
    if unknown:
        raise ProviderContractError(
            f"{field_name} contains unknown fields: {sorted(unknown)!r}"
        )
    return dict(value)

def _require_fields(
    value: Mapping[str, Any],
    required: Iterable[str],
    *,
    field_name: str,
) -> None:
    missing = set(required) - set(value)
    if missing:
        raise ProviderContractError(
            f"{field_name} is missing required fields: {sorted(missing)!r}"
        )

def _coalesce_text_fields(
    value: Mapping[str, Any],
    names: Sequence[str],
    *,
    field_name: str,
    max_length: int = 256,
    required: bool = False,
) -> Optional[str]:
    supplied = [
        (name, value[name])
        for name in names
        if name in value
    ]
    if not supplied:
        if required:
            raise ProviderContractError(f"{field_name} is required")
        return None
    normalized = [
        _require_text(item, f"{field_name}.{name}", max_length=max_length)
        for name, item in supplied
    ]
    if any(item != normalized[0] for item in normalized[1:]):
        raise ProviderContractError(f"{field_name} aliases disagree")
    return normalized[0]



_MAX_EXTENSION_KEYS = 32
_MAX_EXTENSION_BYTES = 16_384
_MAX_EXTENSION_DEPTH = 8
_MAX_EXTENSION_ITEMS = 64
_MAX_EXTENSION_STRING = 4_096


def _bounded_json_value(
    value: Any,
    *,
    field_name: str,
    max_bytes: int = _MAX_EXTENSION_BYTES,
    max_depth: int = _MAX_EXTENSION_DEPTH,
    max_items: int = _MAX_EXTENSION_ITEMS,
    max_string: int = _MAX_EXTENSION_STRING,
) -> Any:
    def copy_value(item: Any, depth: int, seen: set[int]) -> Any:
        if depth > max_depth:
            raise ProviderContractError(f"{field_name} exceeds maximum depth")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ProviderContractError(
                    f"{field_name} contains a non-finite number"
                )
            return item
        if isinstance(item, str):
            if len(item) > max_string:
                raise ProviderContractError(
                    f"{field_name} contains an oversized string"
                )
            return item
        if not isinstance(item, (Mapping, list, tuple)):
            raise ProviderContractError(
                f"{field_name} contains unsupported JSON value type: "
                f"{type(item).__name__}"
            )

        identity = id(item)
        if identity in seen:
            raise ProviderContractError(f"{field_name} contains a cycle")
        seen.add(identity)
        try:
            if isinstance(item, Mapping):
                if len(item) > max_items:
                    raise ProviderContractError(
                        f"{field_name} contains an oversized object"
                    )
                result: Dict[str, Any] = {}
                for key, child in item.items():
                    if not isinstance(key, str) or not key or len(key) > 128:
                        raise ProviderContractError(
                            f"{field_name} contains an invalid object key"
                        )
                    result[key] = copy_value(child, depth + 1, seen)
                return result
            if len(item) > max_items:
                raise ProviderContractError(
                    f"{field_name} contains an oversized array"
                )
            return [copy_value(child, depth + 1, seen) for child in item]
        finally:
            seen.remove(identity)

    canonical = copy_value(value, 0, set())
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ProviderContractError(f"{field_name} exceeds maximum encoded size")
    return canonical




@dataclass
class ProviderToolCall:
    """Normalized provider tool call used by runtime adapters.

    ``arguments`` remains the canonical JSON string consumed by existing
    execution paths. ``arguments_json`` is the same canonical spelling, while
    ``parsed_arguments`` owns the lossless JSON value used by exchange v2.
    """

    id: Optional[str]
    name: Optional[str]
    arguments: Any = None
    type: str = "function"
    raw: Any = None
    arguments_json: Optional[str] = None
    parsed_arguments: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.id is not None:
            self.id = _require_text(self.id, "tool call id")
        if self.name is not None:
            self.name = _require_text(self.name, "tool name")

        if self.arguments_json is not None:
            if not isinstance(self.arguments_json, str):
                raise ProviderContractError("arguments_json must be text")
            try:
                parsed = json.loads(self.arguments_json)
            except (TypeError, ValueError):
                raise ProviderContractError("malformed tool call arguments") from None
            if self.arguments is not None:
                try:
                    supplied = (
                        json.loads(self.arguments)
                        if isinstance(self.arguments, str)
                        else _canonical_value(self.arguments)
                    )
                except (TypeError, ValueError, ProviderContractError):
                    raise ProviderContractError(
                        "malformed tool call arguments"
                    ) from None
                if supplied != parsed:
                    raise ProviderContractError(
                        "tool call arguments disagree with arguments_json"
                    )
        elif self.arguments is None:
            parsed = {}
        elif isinstance(self.arguments, str):
            try:
                parsed = json.loads(self.arguments)
            except (TypeError, ValueError):
                raise ProviderContractError("malformed tool call arguments") from None
        else:
            parsed = _canonical_value(self.arguments)

        canonical = canonical_json(parsed)
        self.arguments = canonical
        self.arguments_json = canonical
        self.parsed_arguments = parsed
        self.type = _require_text(self.type, "tool call type")
        if self.type != "function":
            raise ProviderContractError(
                f"unsupported tool call type: {self.type!r}"
            )

    def as_dict(self) -> Dict[str, Any]:
        if self.id is None or self.name is None:
            raise ProviderContractError("completed tool call requires id and name")
        return {
            "call_id": self.id,
            "name": self.name,
            "arguments_json": self.arguments_json,
            "arguments": self.parsed_arguments,
        }


def _normalized_reasoning_blocks(
    reasoning: Any, annotations: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def append_value(label: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            normalized = [{"type": "thinking", "text": value}]
        elif isinstance(value, Mapping):
            normalized = normalize_content([dict(value)], role="assistant")
        elif isinstance(value, list):
            normalized = normalize_content(value, role="assistant")
        else:
            raise ProviderContractError(
                f"{label} must be text or canonical reasoning blocks"
            )
        for block in normalized:
            if block["type"] not in {
                "thinking",
                "redacted_thinking",
                "provider_replay",
            }:
                raise ProviderContractError(
                    f"{label} contains a non-reasoning content block"
                )
            identity = canonical_json(block)
            if identity not in seen:
                seen.add(identity)
                blocks.append(block)

    append_value("reasoning", reasoning)
    append_value("reasoning_content", annotations.get("reasoning_content"))
    append_value("reasoning annotation", annotations.get("reasoning"))
    append_value("reasoning_details", annotations.get("reasoning_details"))
    return blocks



@dataclass
class ProviderMessage:
    """Normalized provider message returned from a runtime."""

    role: str
    content: Any
    tool_calls: List[ProviderToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    index: Optional[int] = None
    raw_message: Any = None
    raw_choice: Any = None
    reasoning: Optional[Any] = None
    annotations: Dict[str, Any] = field(default_factory=dict)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    message_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.role not in {
            "system",
            "user",
            "developer",
            "assistant",
            "tool_result",
            "tool",
        }:
            raise ProviderContractError(f"unknown provider message role: {self.role!r}")
        if self.role == "tool":
            self.role = "tool_result"
        if self.index is not None and (
            not isinstance(self.index, int)
            or isinstance(self.index, bool)
            or self.index < 0
        ):
            raise ProviderContractError("message index must be a nonnegative integer")
        if not isinstance(self.tool_calls, list):
            raise ProviderContractError("tool_calls must be a list")
        for call in self.tool_calls:
            if not isinstance(call, ProviderToolCall):
                raise ProviderContractError(
                    "tool_calls must contain ProviderToolCall values"
                )
        if not isinstance(self.tool_results, list):
            raise ProviderContractError("tool_results must be a list")
        if self.finish_reason is not None:
            self.finish_reason = _require_text(
                self.finish_reason, "finish_reason", max_length=128
            )
        if not isinstance(self.annotations, dict):
            raise ProviderContractError("message annotations must be an object")
        if self.message_id is not None:
            self.message_id = _require_text(
                self.message_id, "message_id", max_length=256
            )

    def as_dict(self) -> Dict[str, Any]:
        content_blocks = (
            []
            if self.content is None
            else normalize_content(self.content, role=self.role)
        )
        blocks = _normalized_reasoning_blocks(
            self.reasoning, self.annotations
        ) + content_blocks
        for call in self.tool_calls:
            blocks.append({"type": "tool_call", **call.as_dict()})
        for item in self.tool_results:
            normalized = normalize_tool_result_dict(item)
            value = normalized.get("result", normalized.get("error"))
            if not isinstance(value, str):
                value = canonical_json(value)
            blocks.append(
                {
                    "type": "tool_result",
                    "call_id": normalized["call_id"],
                    "content": value,
                    "is_error": "error" in normalized,
                }
            )
        result: Dict[str, Any] = {"role": self.role, "content": blocks}
        if self.message_id:
            result["message_id"] = _require_text(
                self.message_id, "message_id", max_length=256
            )
        return result


def normalize_content(content: Any, *, role: str = "assistant") -> List[Dict[str, Any]]:
    """Normalize provider content into the closed block union without dropping empty text."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise ProviderContractError("message content must be a string or block list")
    blocks: List[Dict[str, Any]] = []
    for index, block in enumerate(content):
        if not isinstance(block, Mapping):
            raise ProviderContractError(f"content block {index} must be an object")
        block_type = block.get("type")
        if block_type not in _CONTENT_TYPES:
            raise ProviderContractError(f"unknown content block type: {block_type!r}")
        if block_type == "text":
            payload = _strict_dict(
                block, field_name="text block", allowed={"type", "text"}
            )
            if "text" not in payload or not isinstance(payload["text"], str):
                raise ProviderContractError("text block requires text")
            blocks.append({"type": "text", "text": payload["text"]})
        elif block_type == "media":
            payload = _strict_dict(
                block,
                field_name="media block",
                allowed={"type", "kind", "uri", "mime"},
            )
            if role != "user":
                raise ProviderContractError(
                    "media blocks require the user role"
                )
            if payload.get("kind") != "image":
                raise ProviderContractError(
                    "media block kind must be image"
                )
            uri = _require_text(
                payload.get("uri"), "media.uri", max_length=96
            )
            if not re.fullmatch(
                r"attachment://sha256:[0-9a-f]{64}", uri
            ):
                raise ProviderContractError(
                    "media.uri must be an authorized attachment URI"
                )
            mime = _require_text(
                payload.get("mime"), "media.mime", max_length=128
            )
            if not re.fullmatch(r"image/[a-z0-9][a-z0-9.+-]*", mime):
                raise ProviderContractError(
                    "media.mime must be a canonical image media type"
                )
            blocks.append(
                {
                    "type": "media",
                    "kind": "image",
                    "uri": uri,
                    "mime": mime,
                }
            )
        elif block_type == "thinking":
            payload = _strict_dict(
                block, field_name="thinking block", allowed={"type", "text"}
            )
            if not isinstance(payload.get("text"), str):
                raise ProviderContractError("thinking block requires text")
            blocks.append({"type": "thinking", "text": payload["text"]})
        elif block_type == "redacted_thinking":
            payload = _strict_dict(
                block, field_name="redacted thinking block", allowed={"type", "data"}
            )
            data = _require_text(
                payload.get("data"), "redacted_thinking.data", max_length=4096
            )
            blocks.append({"type": "redacted_thinking", "data": data})
        elif block_type == "tool_call":
            payload = _strict_dict(
                block,
                field_name="tool call block",
                allowed={
                    "type",
                    "call_id",
                    "id",
                    "name",
                    "arguments",
                    "arguments_json",
                    "tool_type",
                    "type_name",
                },
            )
            tool_type = _coalesce_text_fields(
                payload,
                ("tool_type", "type_name"),
                field_name="tool call type",
            ) or "function"
            if "arguments_json" in payload and not isinstance(
                payload["arguments_json"], str
            ):
                raise ProviderContractError(
                    "tool call arguments_json must be text"
                )
            if (
                "arguments" not in payload
                and "arguments_json" not in payload
            ):
                raise ProviderContractError(
                    "tool call requires explicit arguments"
                )
            arguments_json = payload.get("arguments_json")
            if (
                "arguments" in payload
                and payload["arguments"] is None
                and arguments_json is None
            ):
                arguments_json = "null"
            call = ProviderToolCall(
                id=_coalesce_text_fields(
                    payload,
                    ("call_id", "id"),
                    field_name="tool call id",
                    required=True,
                ),
                name=_coalesce_text_fields(
                    payload,
                    ("name",),
                    field_name="tool call name",
                    required=True,
                ),
                arguments=payload.get("arguments"),
                arguments_json=arguments_json,
                type=tool_type,
            )
            blocks.append({"type": "tool_call", **call.as_dict()})
        elif block_type == "tool_result":
            payload = _strict_dict(
                block,
                field_name="tool result block",
                allowed={
                    "type",
                    "call_id",
                    "tool_call_id",
                    "content",
                    "is_error",
                    "result",
                    "error",
                },
            )
            call_id = _coalesce_text_fields(
                payload,
                ("call_id", "tool_call_id"),
                field_name="tool result call id",
                required=True,
            )
            semantic_keys = [
                key for key in ("content", "result", "error") if key in payload
            ]
            if len(semantic_keys) != 1:
                raise ProviderContractError(
                    "tool result requires exactly one of content, result, or error"
                )
            supplied_error = payload.get("is_error")
            if "is_error" in payload and not isinstance(supplied_error, bool):
                raise ProviderContractError("tool result is_error must be boolean")
            semantic_key = semantic_keys[0]
            if semantic_key == "content":
                is_error = bool(supplied_error) if "is_error" in payload else False
            else:
                is_error = semantic_key == "error"
                if "is_error" in payload and supplied_error is not is_error:
                    raise ProviderContractError(
                        "tool result is_error disagrees with its semantic field"
                    )
            content_value = payload[semantic_key]
            if not isinstance(content_value, str):
                content_value = canonical_json(content_value)
            blocks.append(
                {
                    "type": "tool_result",
                    "call_id": call_id,
                    "content": content_value,
                    "is_error": is_error,
                }
            )
        else:
            replay = _sanitize_replay(block)
            blocks.append({"type": "provider_replay", **replay})
    return blocks


def _sanitize_replay(replay: Mapping[str, Any]) -> Dict[str, Any]:
    payload = _strict_dict(
        replay,
        field_name="provider replay",
        allowed={"type", "provider_id", "schema_version", "replay_scope", "payload"},
    )
    provider_id = _require_text(
        payload.get("provider_id"), "provider replay provider_id", max_length=128
    )
    if not re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", provider_id):
        raise ProviderContractError("provider replay provider_id is not canonical")
    schema_version = _require_text(
        payload.get("schema_version"), "provider replay schema_version", max_length=128
    )
    scope = payload.get("replay_scope")
    if scope not in {"same_provider", "diagnostic"}:
        raise ProviderContractError("invalid provider replay scope")
    replay_payload = payload.get("payload")
    if not isinstance(replay_payload, Mapping):
        raise ProviderContractError("provider replay payload must be an object")
    if set(replay_payload) - {
        "encrypted_content",
        "signature",
        "redacted_data",
        "item_id",
        "reasoning_id",
    }:
        raise ProviderContractError("provider replay payload contains unknown fields")
    null_fields = [key for key, value in replay_payload.items() if value is None]
    if null_fields:
        raise ProviderContractError(
            "provider replay payload fields must be omitted instead of null"
        )
    bounded = dict(replay_payload)
    if not bounded:
        raise ProviderContractError("provider replay payload must be nonempty")
    for key, value in tuple(bounded.items()):
        if key in {"item_id", "reasoning_id"}:
            _require_text(value, f"provider replay {key}", max_length=256)
        else:
            bounded[key] = _bounded_json_value(
                value,
                field_name=f"provider replay {key}",
                max_bytes=4096,
                max_depth=4,
                max_items=32,
            )
    canonical_json(bounded)
    return {
        "provider_id": provider_id,
        "schema_version": schema_version,
        "replay_scope": scope,
        "payload": bounded,
    }


def normalize_provider_replay(replay: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and bound one provider-native replay envelope."""
    return _sanitize_replay(replay)


@dataclass
class ProviderResult:
    """Result object returned from a provider runtime invocation."""

    messages: List[ProviderMessage]
    raw_response: Any
    usage: Optional[Dict[str, Any]] = None
    encrypted_reasoning: Optional[List[Any]] = None
    reasoning_summaries: Optional[List[str]] = None
    reasoning_blocks: Optional[List[Dict[str, Any]]] = None
    model: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    provider_replay: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class ProviderCorrelation:
    session_id: str
    input_id: str
    turn_id: str

    def __post_init__(self) -> None:
        _require_text(self.session_id, "correlation.session_id", max_length=256)
        _require_text(self.input_id, "correlation.input_id", max_length=256)
        _require_text(self.turn_id, "correlation.turn_id", max_length=256)

    def as_dict(self) -> Dict[str, str]:
        return {
            "session_id": self.session_id,
            "input_id": self.input_id,
            "turn_id": self.turn_id,
        }


@dataclass(frozen=True)
class ProviderIdentity:
    provider_id: str
    runtime_id: str
    route_id: Optional[str]
    model: str

    def __post_init__(self) -> None:
        _require_text(self.provider_id, "provider.provider_id", max_length=128)
        _require_text(self.runtime_id, "provider.runtime_id", max_length=128)
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", self.provider_id):
            raise ProviderContractError("provider.provider_id is not canonical")
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", self.runtime_id):
            raise ProviderContractError("provider.runtime_id is not canonical")
        if self.route_id is not None:
            route_id = _require_text(
                self.route_id, "provider.route_id", max_length=256
            )
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", route_id
            ):
                raise ProviderContractError(
                    "provider.route_id is not canonical"
                )
        model = _require_text(self.model, "provider.model", max_length=256)
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", model
        ):
            raise ProviderContractError("provider.model is not canonical")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "runtime_id": self.runtime_id,
            "route_id": self.route_id,
            "model": self.model,
        }


@dataclass
class ProviderRequest:
    stream: bool
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    _wire_strict: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.stream, bool):
            raise ProviderContractError("request.stream must be boolean")
        if not isinstance(self.messages, list) or not isinstance(self.tools, list):
            raise ProviderContractError(
                "request.messages and request.tools must be arrays"
            )
        if self._wire_strict:
            self.messages = _canonical_value(self.messages)
            self.tools = _canonical_value(self.tools)
            return
        self.messages = normalize_request_messages(self.messages)
        self.tools = [
            _normalize_tool_schema(tool, index) for index, tool in enumerate(self.tools)
        ]

    def as_dict(self) -> Dict[str, Any]:
        if self._wire_strict:
            return {
                "stream": self.stream,
                "messages": _canonical_value(self.messages),
                "tools": _canonical_value(self.tools),
            }
        return {"stream": self.stream, "messages": self.messages, "tools": self.tools}


def _strip_request_transport_metadata(
    content: str | List[Any],
) -> str | List[Any]:
    if isinstance(content, str):
        return content
    normalized: List[Any] = []
    for index, block in enumerate(content):
        if not isinstance(block, Mapping) or "cache_control" not in block:
            normalized.append(block)
            continue
        cache_control = _strict_dict(
            block["cache_control"],
            field_name=f"request content block {index} cache_control",
            allowed={"type", "ttl"},
        )
        if cache_control.get("type") != "ephemeral":
            raise ProviderContractError(
                f"request content block {index} has unsupported cache_control type"
            )
        if "ttl" in cache_control and cache_control["ttl"] not in {"5m", "1h"}:
            raise ProviderContractError(
                f"request content block {index} has unsupported cache_control ttl"
            )
        semantic_block = dict(block)
        semantic_block.pop("cache_control")
        normalized.append(semantic_block)
    return normalized


def normalize_request_messages(
    messages: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for index, message in enumerate(messages):
        message = _strict_dict(
            message,
            field_name=f"request.messages[{index}]",
            allowed={
                "role",
                "content",
                "tool_calls",
                "tool_results",
                "message_id",
                "id",
                "tool_call_id",
                "call_id",
                "name",
                "reasoning_content",
                "reasoning",
                "reasoning_details",
            },
        )
        original_role = message.get("role")
        role = "tool_result" if original_role == "tool" else original_role
        if role not in {"system", "user", "developer", "assistant", "tool_result"}:
            raise ProviderContractError(f"unknown request message role: {role!r}")
        is_tool_transport = role == "tool_result"
        content_raw = message.get("content", [])
        if content_raw is None:
            content_raw = []
        if not isinstance(content_raw, (str, list)):
            raise ProviderContractError(
                "message.content must be a string or block list"
            )
        transport_call_id = _coalesce_text_fields(
            message,
            ("tool_call_id", "call_id"),
            field_name=f"request.messages[{index}].tool_call_id",
        )
        if is_tool_transport and isinstance(content_raw, str):
            if transport_call_id is None:
                raise ProviderContractError(
                    "tool result transport message requires a call id"
                )
            blocks = [
                {
                    "type": "tool_result",
                    "call_id": transport_call_id,
                    "content": content_raw,
                    "is_error": False,
                }
            ]
        else:
            semantic_content = _strip_request_transport_metadata(content_raw)
            blocks = (
                normalize_content(semantic_content, role=role)
                if isinstance(semantic_content, str) or semantic_content
                else []
            )
        if is_tool_transport and not isinstance(content_raw, str):
            if not blocks or any(
                block.get("type") != "tool_result" for block in blocks
            ):
                raise ProviderContractError(
                    "tool-result messages require correlated tool_result content"
                )
        if transport_call_id is not None and not (
            is_tool_transport and isinstance(content_raw, str)
        ):
            raise ProviderContractError(
                "transport call id is supported only on string tool-result messages"
            )
        reasoning_blocks = _normalized_reasoning_blocks(
            message.get("reasoning"),
            {
                key: message[key]
                for key in ("reasoning_content", "reasoning_details")
                if key in message
            },
        )
        if reasoning_blocks:
            if role != "assistant":
                raise ProviderContractError(
                    "reasoning blocks require the assistant role"
                )
            blocks = reasoning_blocks + blocks
        transport_name = message.get("name")
        if transport_name is not None:
            if not is_tool_transport:
                raise ProviderContractError(
                    "message.name is supported only on tool-result transport messages"
                )
            _require_text(
                transport_name,
                f"request.messages[{index}].name",
                max_length=256,
            )
        raw_calls = message.get("tool_calls")
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise ProviderContractError("message.tool_calls must be a list")
        for call_index, raw_call in enumerate(raw_calls):
            call = normalize_tool_call_dict(raw_call, call_index)
            blocks.append({"type": "tool_call", **call.as_dict()})
        raw_results = message.get("tool_results")
        if raw_results is None:
            raw_results = []
        if not isinstance(raw_results, list):
            raise ProviderContractError("message.tool_results must be a list")
        for result_index, raw_result in enumerate(raw_results):
            tool_result = normalize_tool_result_dict(raw_result, result_index)
            text = tool_result.get("result", tool_result.get("error"))
            if not isinstance(text, str):
                text = canonical_json(text)
            blocks.append(
                {
                    "type": "tool_result",
                    "call_id": tool_result["call_id"],
                    "content": text,
                    "is_error": "error" in tool_result,
                }
            )
        normalized: Dict[str, Any] = {"role": role, "content": blocks}
        message_id = _coalesce_text_fields(
            message,
            ("message_id", "id"),
            field_name=f"request.messages[{index}].message_id",
        )
        if message_id is not None:
            normalized["message_id"] = message_id
        result.append(normalized)
    return result


def _normalize_tool_schema(tool: Any, index: int) -> Dict[str, Any]:
    if not isinstance(tool, Mapping):
        raise ProviderContractError(f"request.tools[{index}] must be an object")
    if "function" in tool:
        wrapped = _strict_dict(
            tool,
            field_name=f"request.tools[{index}]",
            allowed={"type", "function"},
        )
        if wrapped.get("type") not in {None, "function"}:
            raise ProviderContractError(
                f"request.tools[{index}] has unsupported type"
            )
        fn = _strict_dict(
            wrapped.get("function"),
            field_name=f"request.tools[{index}].function",
            allowed={"name", "description", "parameters", "strict"},
        )
    else:
        fn = _strict_dict(
            tool,
            field_name=f"request.tools[{index}]",
            allowed={"name", "description", "parameters", "input_schema", "strict"},
        )
        if "parameters" in fn and "input_schema" in fn:
            raise ProviderContractError(
                f"request.tools[{index}] cannot define both parameters and input_schema"
            )
    name = fn.get("name")
    parameters = fn.get("parameters", fn.get("input_schema", {}))
    if not isinstance(name, str) or not name or not isinstance(parameters, Mapping):
        raise ProviderContractError(
            f"request.tools[{index}] requires name and parameters"
        )
    _require_text(name, f"request.tools[{index}].name", max_length=256)
    result: Dict[str, Any] = {
        "name": name,
        "parameters": json.loads(canonical_json(dict(parameters))),
    }
    strict = fn.get("strict")
    if strict is not None:
        if not isinstance(strict, bool):
            raise ProviderContractError(
                f"request.tools[{index}].strict must be boolean"
            )
        result["strict"] = strict
    description = fn.get("description")
    if description is not None:
        if not isinstance(description, str) or not description:
            raise ProviderContractError(
                f"request.tools[{index}].description must be nonempty"
            )
        result["description"] = _require_text(
            description, f"request.tools[{index}].description", max_length=16384
        )
    return result


def normalize_tool_call_dict(raw: Any, index: int = 0) -> ProviderToolCall:
    raw = _strict_dict(
        raw,
        field_name=f"tool_calls[{index}]",
        allowed={
            "call_id",
            "id",
            "tool_call_id",
            "name",
            "arguments",
            "arguments_json",
            "type",
            "tool_type",
            "function",
        },
    )
    raw_function = raw.get("function")
    if raw_function is not None:
        fn = _strict_dict(
            raw_function,
            field_name=f"tool_calls[{index}].function",
            allowed={"name", "arguments"},
        )
    else:
        fn = {}
    top_name = _coalesce_text_fields(
        raw, ("name",), field_name=f"tool_calls[{index}].name"
    )
    function_name = _coalesce_text_fields(
        fn, ("name",), field_name=f"tool_calls[{index}].function.name"
    )
    if top_name is not None and function_name is not None and top_name != function_name:
        raise ProviderContractError(f"tool_calls[{index}] names disagree")
    top_arguments_json = raw.get("arguments_json")
    function_arguments_json = fn.get("arguments")
    if "arguments_json" in raw and not isinstance(
        top_arguments_json, str
    ):
        raise ProviderContractError(
            f"tool_calls[{index}].arguments_json must be text"
        )
    if "arguments" in fn and not isinstance(
        function_arguments_json, str
    ):
        raise ProviderContractError(
            f"tool_calls[{index}].function.arguments must be text"
        )
    if (
        top_arguments_json is not None
        and function_arguments_json is not None
        and top_arguments_json != function_arguments_json
    ):
        raise ProviderContractError(
            f"tool_calls[{index}] arguments_json aliases disagree"
        )
    if (
        "arguments" not in raw
        and top_arguments_json is None
        and function_arguments_json is None
    ):
        raise ProviderContractError(
            f"tool_calls[{index}] requires explicit arguments"
        )
    if (
        "arguments" in raw
        and raw["arguments"] is None
        and top_arguments_json is None
        and function_arguments_json is None
    ):
        top_arguments_json = "null"
    call = ProviderToolCall(
        id=_coalesce_text_fields(
            raw,
            ("call_id", "id", "tool_call_id"),
            field_name=f"tool_calls[{index}].call_id",
            required=True,
        ),
        name=top_name or function_name,
        arguments=raw.get("arguments"),
        arguments_json=(
            top_arguments_json
            if top_arguments_json is not None
            else function_arguments_json
        ),
        type=_coalesce_text_fields(
            raw,
            ("type", "tool_type"),
            field_name=f"tool_calls[{index}].type",
        )
        or "function",
    )
    if call.name is None:
        raise ProviderContractError(f"tool_calls[{index}].name is required")
    return call


def normalize_tool_result_dict(raw: Any, index: int = 0) -> Dict[str, Any]:
    raw = _strict_dict(
        raw,
        field_name=f"tool_results[{index}]",
        allowed={
            "call_id",
            "tool_call_id",
            "tool_use_id",
            "result",
            "out",
            "error",
        },
    )
    call_id = _coalesce_text_fields(
        raw,
        ("call_id", "tool_call_id", "tool_use_id"),
        field_name=f"tool_results[{index}].call_id",
        required=True,
    )
    semantic_keys = [
        key for key in ("result", "out", "error") if key in raw
    ]
    if len(semantic_keys) != 1:
        raise ProviderContractError(
            "tool result requires exactly one of result, out, or error"
        )
    semantic_key = semantic_keys[0]
    result: Dict[str, Any] = {"call_id": call_id}
    if semantic_key == "error":
        result["error"] = raw[semantic_key]
    else:
        result["result"] = raw[semantic_key]
    canonical_json({key: value for key, value in result.items() if key != "call_id"})
    return result


@dataclass
class ProviderEvent:
    sequence: int
    kind: str
    content_index: Optional[int] = None
    message_id: Optional[str] = None
    call_id: Optional[str] = None
    name: Optional[str] = None
    delta: Optional[str] = None
    arguments_json: Optional[str] = None
    arguments: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ProviderContractError("event.sequence must be a nonnegative integer")
        if self.kind not in _EVENT_KINDS:
            raise ProviderProtocolError(f"unknown provider event kind: {self.kind!r}")
        if self.metadata:
            raise ProviderContractError("event metadata is not part of the v2 contract")
        fields = {
            "content_index": self.content_index,
            "message_id": self.message_id,
            "call_id": self.call_id,
            "name": self.name,
            "delta": self.delta,
            "arguments_json": self.arguments_json,
            "arguments": self.arguments,
        }
        allowed = {
            "response_start": set(),
            "text_start": {"content_index", "message_id"},
            "text_delta": {"content_index", "message_id", "delta"},
            "text_end": {"content_index", "message_id"},
            "thinking_start": {"content_index", "message_id"},
            "thinking_delta": {"content_index", "message_id", "delta"},
            "thinking_end": {"content_index", "message_id"},
            "tool_call_start": {"content_index", "message_id", "call_id", "name"},
            "tool_call_delta": {"content_index", "message_id", "call_id", "delta"},
            "tool_call_end": {
                "content_index",
                "message_id",
                "call_id",
                "arguments_json",
                "arguments",
            },
        }[self.kind]
        unexpected = {
            key
            for key, value in fields.items()
            if value is not None and key not in allowed
        }
        if unexpected:
            raise ProviderContractError(
                f"{self.kind} contains invalid fields: {sorted(unexpected)!r}"
            )
        if self.kind == "response_start":
            return
        if (
            not isinstance(self.content_index, int)
            or isinstance(self.content_index, bool)
            or self.content_index < 0
        ):
            raise ProviderContractError("indexed provider event requires content_index")
        _require_text(self.message_id, "provider event message_id", max_length=256)
        if self.kind.startswith("tool_call_"):
            _require_text(self.call_id, "tool event call_id", max_length=256)
        if self.kind == "tool_call_start":
            _require_text(self.name, "tool event name", max_length=256)
        if self.kind.endswith("_delta"):
            _require_text(self.delta, "provider event delta", max_length=65536)
        if self.kind == "tool_call_end":
            if (
                not isinstance(self.arguments_json, str)
                or len(self.arguments_json) > 65536
            ):
                raise ProviderContractError(
                    "tool_call_end requires bounded arguments_json"
                )
            _validate_canonical_argument_pair(
                self.arguments_json,
                self.arguments,
                field_name="tool_call_end",
            )

    def as_dict(self) -> Dict[str, Any]:
        self.validate()
        result: Dict[str, Any] = {"sequence": self.sequence, "kind": self.kind}
        for key in (
            "content_index",
            "message_id",
            "call_id",
            "name",
            "delta",
            "arguments_json",
            "arguments",
        ):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass
class ProviderDone:
    output_emitted: bool
    finish_reason: str = "stop"
    raw_provider_finish: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    assistant_messages: List[Dict[str, Any]] = field(default_factory=list)
    provider_replay: List[Dict[str, Any]] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    _wire_strict: bool = field(default=False, repr=False, compare=False)
    _wire_provider_replay_present: bool = field(
        default=True, repr=False, compare=False
    )

    def as_dict(self) -> Dict[str, Any]:
        if self.finish_reason not in _FINISH_REASONS:
            raise ProviderContractError("invalid finish_reason")
        if not isinstance(self.output_emitted, bool):
            raise ProviderContractError("terminal.output_emitted must be boolean")
        if not isinstance(self.assistant_messages, list):
            raise ProviderContractError(
                "terminal.assistant_messages must be an array"
            )
        if not isinstance(self.provider_replay, list):
            raise ProviderContractError(
                "terminal.provider_replay must be an array"
            )
        if not isinstance(self.evidence_refs, list):
            raise ProviderContractError(
                "terminal.evidence_refs must be an array"
            )
        result: Dict[str, Any] = {
            "kind": "done",
            "output_emitted": self.output_emitted,
            "finish_reason": self.finish_reason,
        }
        if self.raw_provider_finish is not None:
            raw_provider_finish = _require_text(
                self.raw_provider_finish, "raw_provider_finish", max_length=128
            )
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", raw_provider_finish
            ):
                raise ProviderContractError(
                    "raw_provider_finish must be a protocol token"
                )
            result["raw_provider_finish"] = raw_provider_finish
        if self._wire_strict:
            if self.usage is not None:
                result["usage"] = _canonical_value(self.usage)
            result["assistant_messages"] = _canonical_value(
                self.assistant_messages
            )
            if self._wire_provider_replay_present or self.provider_replay:
                result["provider_replay"] = _canonical_value(
                    self.provider_replay
                )
            result["evidence_refs"] = _canonical_value(self.evidence_refs)
            return result
        if self.usage is not None:
            result["usage"] = normalize_usage(self.usage)
        result["assistant_messages"] = [
            normalize_terminal_message(message) for message in self.assistant_messages
        ]
        result["provider_replay"] = [
            _sanitize_replay(item) for item in self.provider_replay
        ]
        result["evidence_refs"] = _normalize_evidence_refs(self.evidence_refs)
        return result


@dataclass
class ProviderErrorTerminal:
    output_emitted: bool
    code: str
    category: Literal["adapter", "provider", "transport", "protocol", "configuration"]
    retryable: bool
    http_status: Optional[int] = None
    evidence_refs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        code = _safe_error_code(self.code)
        if self.category not in {
            "adapter",
            "provider",
            "transport",
            "protocol",
            "configuration",
        }:
            raise ProviderContractError("invalid error category")
        if not isinstance(self.evidence_refs, list):
            raise ProviderContractError(
                "terminal.evidence_refs must be an array"
            )
        if not isinstance(self.output_emitted, bool) or not isinstance(
            self.retryable, bool
        ):
            raise ProviderContractError("invalid error terminal booleans")
        result: Dict[str, Any] = {
            "kind": "error",
            "output_emitted": self.output_emitted,
            "code": code,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            if (
                not isinstance(self.http_status, int)
                or isinstance(self.http_status, bool)
                or not 100 <= self.http_status <= 599
            ):
                raise ProviderContractError("http_status must be a valid HTTP status")
            result["http_status"] = self.http_status
        result["evidence_refs"] = _normalize_evidence_refs(self.evidence_refs)
        return result


@dataclass
class ProviderCancelled:
    output_emitted: bool
    owner: Literal["caller", "provider", "transport", "engine"]
    reason_code: str
    evidence_refs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        if self.owner not in {"caller", "provider", "transport", "engine"}:
            raise ProviderContractError("invalid cancellation owner")
        if not isinstance(self.output_emitted, bool):
            raise ProviderContractError("invalid cancellation output_emitted")
        if not isinstance(self.evidence_refs, list):
            raise ProviderContractError(
                "terminal.evidence_refs must be an array"
            )
        return {
            "kind": "cancelled",
            "output_emitted": self.output_emitted,
            "owner": self.owner,
            "reason_code": _safe_error_code(self.reason_code),
            "evidence_refs": _normalize_evidence_refs(self.evidence_refs),
        }


def _safe_error_code(value: Any) -> str:
    text = str(value or "provider_error").strip().lower().replace(" ", "_")
    if not _SAFE_CODE.fullmatch(text):
        text = "provider_error"
    return text


def _normalize_evidence_refs(values: Iterable[Any]) -> List[str]:
    if not isinstance(values, (list, tuple)):
        raise ProviderContractError("evidence_refs must be an array")
    refs = [_require_text(item, "evidence ref", max_length=1024) for item in values]
    if len(refs) != len(set(refs)):
        raise ProviderContractError("evidence refs must be unique")
    return refs


def normalize_usage(usage: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(usage, Mapping):
        raise ProviderContractError("usage must be an object")
    aliases = {
        "input_tokens": "inputTokens",
        "prompt_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "completion_tokens": "outputTokens",
        "cache_read_tokens": "cacheReadTokens",
        "cache_read_input_tokens": "cacheReadTokens",
        "cache_write_tokens": "cacheWriteTokens",
        "cache_creation_input_tokens": "cacheWriteTokens",
        "total_tokens": "totalTokens",
        "reasoning_tokens": "reasoningTokens",
    }
    result: Dict[str, Any] = {}
    extensions: Dict[str, Any] = {}
    explicit_extensions = usage.get("extensions")
    extensions_supplied = "extensions" in usage
    if extensions_supplied:
        if not isinstance(explicit_extensions, Mapping):
            raise ProviderContractError("usage.extensions must be an object")
        for key, value in explicit_extensions.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ProviderContractError("usage extension key is invalid")
            extensions[key] = _bounded_json_value(
                value, field_name=f"usage.extensions.{key}"
            )
    for key, value in usage.items():
        if key == "extensions":
            continue
        if not isinstance(key, str):
            raise ProviderContractError("usage keys must be strings")
        normalized_key = aliases.get(key, key)
        if normalized_key not in _USAGE_KEYS:
            if not key or len(key) > 128:
                raise ProviderContractError("usage extension key is invalid")
            canonical_value = _bounded_json_value(
                value, field_name=f"usage.extensions.{key}"
            )
            if key in extensions and extensions[key] != canonical_value:
                raise ProviderContractError(
                    f"conflicting usage extension value for {key}"
                )
            extensions[key] = canonical_value
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ProviderContractError(
                f"usage.{normalized_key} must be a nonnegative integer"
            )
        if normalized_key in result and result[normalized_key] != value:
            raise ProviderContractError(
                f"conflicting usage aliases for {normalized_key}"
            )
        result[normalized_key] = value
    if len(extensions) > _MAX_EXTENSION_KEYS:
        raise ProviderContractError("usage.extensions contains too many keys")
    if extensions or extensions_supplied:
        result["extensions"] = _bounded_json_value(
            extensions, field_name="usage.extensions"
        )
    return result


def normalize_terminal_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, ProviderMessage):
        if message.role != "assistant":
            raise ProviderContractError(
                "terminal assistant_messages must use assistant role"
            )
        return message.as_dict()
    if not isinstance(message, Mapping):
        raise ProviderContractError("terminal message must be an object")
    role = message.get("role")
    if role != "assistant":
        raise ProviderContractError(
            "terminal assistant_messages must use assistant role"
        )
    return normalize_request_messages([dict(message)])[0]


def _validate_event_lifecycle(
    events: Sequence[ProviderEvent], *, require_closed: bool
) -> None:
    if not events:
        if require_closed:
            raise ProviderContractError(
                "done provider exchange requires response_start"
            )
        return
    if events[0].kind != "response_start":
        raise ProviderContractError(
            "provider events must begin with response_start"
        )
    open_blocks: Dict[
        Tuple[int, str], Tuple[str, Optional[str]]
    ] = {}
    closed_blocks: set[Tuple[int, str]] = set()
    for event in events[1:]:
        if event.kind == "response_start":
            raise ProviderContractError(
                "provider events contain duplicate response_start"
            )
        if event.content_index is None or event.message_id is None:
            raise ProviderContractError(
                "provider content lifecycle is missing identity"
            )
        key = (event.content_index, event.message_id)
        family, phase = event.kind.rsplit("_", 1)
        identity = (family, event.call_id if family == "tool_call" else None)
        if phase == "start":
            if key in open_blocks or key in closed_blocks:
                raise ProviderContractError(
                    "provider content lifecycle contains a duplicate start"
                )
            open_blocks[key] = identity
            continue
        active = open_blocks.get(key)
        if active is None or active != identity:
            raise ProviderContractError(
                "provider content lifecycle is incomplete or mismatched"
            )
        if phase == "end":
            del open_blocks[key]
            closed_blocks.add(key)
    if require_closed and open_blocks:
        raise ProviderContractError(
            "done provider exchange contains unclosed content"
        )


@dataclass
class ProviderExchangeV2:
    schema_version: str
    exchange_id: str
    correlation: ProviderCorrelation
    provider: ProviderIdentity
    request: ProviderRequest
    events: List[ProviderEvent]
    terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled

    def validate(self) -> "ProviderExchangeV2":
        if self.schema_version != "bb.provider_exchange.v2":
            raise ProviderContractError(
                "provider exchange must use bb.provider_exchange.v2"
            )
        _require_text(self.exchange_id, "exchange_id", max_length=256)
        if not isinstance(self.correlation, ProviderCorrelation):
            raise ProviderContractError("correlation must be ProviderCorrelation")
        if not isinstance(self.provider, ProviderIdentity):
            raise ProviderContractError("provider must be ProviderIdentity")
        if not isinstance(self.request, ProviderRequest):
            raise ProviderContractError("request must be ProviderRequest")
        for index, message in enumerate(self.request.messages):
            if isinstance(message, Mapping):
                _validate_content_wire(
                    message.get("content"),
                    field_name=f"request.messages[{index}].content",
                )
        if not isinstance(self.events, list):
            raise ProviderContractError("events must be an array")
        for index, event in enumerate(self.events):
            if not isinstance(event, ProviderEvent):
                raise ProviderContractError("events must contain ProviderEvent values")
            if event.sequence != index:
                raise ProviderContractError(
                    "provider event sequences must be contiguous zero-based integers"
                )
            event.validate()
        if not isinstance(
            self.terminal, (ProviderDone, ProviderErrorTerminal, ProviderCancelled)
        ):
            raise ProviderContractError("exchange must contain exactly one terminal")
        self.terminal.as_dict()
        if isinstance(self.terminal, ProviderDone):
            for index, message in enumerate(self.terminal.assistant_messages):
                if isinstance(message, Mapping):
                    _validate_content_wire(
                        message.get("content"),
                        field_name=f"terminal.assistant_messages[{index}].content",
                    )
            for index, replay in enumerate(self.terminal.provider_replay):
                _validate_provider_replay_wire(
                    replay, field_name=f"terminal.provider_replay[{index}]"
                )
        observed_output = any(
            event.kind in _OUTPUT_EVENT_KINDS for event in self.events
        )
        if isinstance(self.terminal, ProviderDone):
            observed_output = observed_output or bool(
                self.terminal.assistant_messages
            )
            if self.terminal.output_emitted is not observed_output:
                raise ProviderContractError(
                    "done terminal output_emitted disagrees with its output"
                )
        elif observed_output and not self.terminal.output_emitted:
            raise ProviderContractError(
                "terminal cannot deny recorded provider output"
            )
        _validate_event_lifecycle(
            self.events,
            require_closed=isinstance(self.terminal, ProviderDone),
        )
        return self

    def as_dict(self) -> Dict[str, Any]:
        self.validate()
        terminal = self.terminal.as_dict()
        result = {
            "schema_version": self.schema_version,
            "exchange_id": self.exchange_id,
            "correlation": self.correlation.as_dict(),
            "provider": self.provider.as_dict(),
            "request": self.request.as_dict(),
            "events": [event.as_dict() for event in self.events],
            "terminal": terminal,
        }
        _validate_provider_exchange_v2_wire(result)
        _validate_exchange_wire_semantics(result)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderExchangeV2":
        _validate_provider_exchange_v2_wire(value)
        top = _strict_dict(
            value,
            field_name="provider exchange",
            allowed={
                "schema_version",
                "exchange_id",
                "correlation",
                "provider",
                "request",
                "events",
                "terminal",
            },
        )
        _require_fields(
            top,
            {
                "schema_version",
                "exchange_id",
                "correlation",
                "provider",
                "request",
                "events",
                "terminal",
            },
            field_name="provider exchange",
        )
        _validate_exchange_wire_semantics(top)
        correlation = _strict_dict(
            top.get("correlation"),
            field_name="correlation",
            allowed={"session_id", "input_id", "turn_id"},
        )
        _require_fields(
            correlation,
            {"session_id", "input_id", "turn_id"},
            field_name="correlation",
        )
        provider = _strict_dict(
            top.get("provider"),
            field_name="provider",
            allowed={"provider_id", "runtime_id", "route_id", "model"},
        )
        _require_fields(
            provider,
            {"provider_id", "runtime_id", "route_id", "model"},
            field_name="provider",
        )
        request = _strict_dict(
            top.get("request"),
            field_name="request",
            allowed={"stream", "messages", "tools"},
        )
        _require_fields(
            request,
            {"stream", "messages", "tools"},
            field_name="request",
        )
        events_raw = top.get("events")
        if not isinstance(events_raw, list):
            raise ProviderContractError("events must be an array")
        events: List[ProviderEvent] = []
        for raw in events_raw:
            if not isinstance(raw, Mapping):
                raise ProviderContractError("event must be an object")
            allowed_event_fields = {
                "sequence",
                "kind",
                "content_index",
                "message_id",
                "call_id",
                "name",
                "delta",
                "arguments_json",
                "arguments",
            }
            raw = _strict_dict(raw, field_name="event", allowed=allowed_event_fields)
            _require_fields(
                raw, {"sequence", "kind"}, field_name="event"
            )
            event = ProviderEvent(
                sequence=raw.get("sequence"),
                kind=raw.get("kind"),
                content_index=raw.get("content_index"),
                message_id=raw.get("message_id"),
                call_id=raw.get("call_id"),
                name=raw.get("name"),
                delta=raw.get("delta"),
                arguments_json=raw.get("arguments_json"),
                arguments=raw.get("arguments"),
            )
            events.append(event)
        terminal_raw = top.get("terminal")
        if not isinstance(terminal_raw, Mapping):
            raise ProviderContractError("terminal must be an object")
        terminal_kind = terminal_raw.get("kind")
        if terminal_kind == "done":
            terminal_raw = _strict_dict(
                terminal_raw,
                field_name="done terminal",
                allowed={
                    "kind",
                    "output_emitted",
                    "finish_reason",
                    "raw_provider_finish",
                    "usage",
                    "assistant_messages",
                    "provider_replay",
                    "evidence_refs",
                },
            )
            _require_fields(
                terminal_raw,
                {
                    "kind",
                    "output_emitted",
                    "finish_reason",
                    "assistant_messages",
                    "evidence_refs",
                },
                field_name="done terminal",
            )
            terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled = (
                ProviderDone(
                    output_emitted=terminal_raw["output_emitted"],
                    finish_reason=terminal_raw["finish_reason"],
                    raw_provider_finish=terminal_raw.get("raw_provider_finish"),
                    usage=terminal_raw.get("usage"),
                    assistant_messages=terminal_raw["assistant_messages"],
                    provider_replay=terminal_raw.get("provider_replay", []),
                    evidence_refs=terminal_raw["evidence_refs"],
                    _wire_strict=True,
                    _wire_provider_replay_present="provider_replay" in terminal_raw,
                )
            )
        elif terminal_kind == "error":
            terminal_raw = _strict_dict(
                terminal_raw,
                field_name="error terminal",
                allowed={
                    "kind",
                    "output_emitted",
                    "code",
                    "category",
                    "retryable",
                    "http_status",
                    "evidence_refs",
                },
            )
            _require_fields(
                terminal_raw,
                {
                    "kind",
                    "output_emitted",
                    "code",
                    "category",
                    "retryable",
                    "evidence_refs",
                },
                field_name="error terminal",
            )
            terminal = ProviderErrorTerminal(
                output_emitted=terminal_raw["output_emitted"],
                code=terminal_raw["code"],
                category=terminal_raw["category"],
                retryable=terminal_raw["retryable"],
                http_status=terminal_raw.get("http_status"),
                evidence_refs=terminal_raw["evidence_refs"],
            )
        elif terminal_kind == "cancelled":
            terminal_raw = _strict_dict(
                terminal_raw,
                field_name="cancelled terminal",
                allowed={
                    "kind",
                    "output_emitted",
                    "owner",
                    "reason_code",
                    "evidence_refs",
                },
            )
            _require_fields(
                terminal_raw,
                {
                    "kind",
                    "output_emitted",
                    "owner",
                    "reason_code",
                    "evidence_refs",
                },
                field_name="cancelled terminal",
            )
            terminal = ProviderCancelled(
                output_emitted=terminal_raw["output_emitted"],
                owner=terminal_raw["owner"],
                reason_code=terminal_raw["reason_code"],
                evidence_refs=terminal_raw["evidence_refs"],
            )
        else:
            raise ProviderContractError(
                "terminal kind must be done, error, or cancelled"
            )
        return cls(
            schema_version=top.get("schema_version"),
            exchange_id=top.get("exchange_id"),
            correlation=ProviderCorrelation(**correlation),
            provider=ProviderIdentity(**provider),
            request=ProviderRequest(**request, _wire_strict=True),
            events=events,
            terminal=terminal,
        ).validate()


def encode_provider_exchange(
    exchange: ProviderExchangeV2 | Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the strict v2 JSON object used for persistence and transport."""
    if isinstance(exchange, ProviderExchangeV2):
        return exchange.as_dict()
    return ProviderExchangeV2.from_dict(exchange).as_dict()

_PUBLIC_COMPLETION_SENTINELS = frozenset(
    {
        ">>>>>> END RESPONSE",
        "TASK COMPLETE",
    }
)


def _strip_public_completion_sentinel_lines(value: str) -> str:
    lines = value.splitlines(keepends=True)
    removed = any(line.strip() in _PUBLIC_COMPLETION_SENTINELS for line in lines)
    filtered = "".join(
        line for line in lines if line.strip() not in _PUBLIC_COMPLETION_SENTINELS
    )
    return filtered.rstrip("\r\n") if removed else filtered


def _strip_public_completion_sentinel_tree(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_public_completion_sentinel_lines(value)
    if isinstance(value, list):
        return [_strip_public_completion_sentinel_tree(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_public_completion_sentinel_tree(item)
            for key, item in value.items()
        }
    return value


def strip_provider_exchange_completion_sentinels(
    exchange: ProviderExchangeV2 | Mapping[str, Any],
) -> Dict[str, Any]:
    """Remove control-only completion lines without changing request/tool inputs."""

    document = encode_provider_exchange(exchange)
    delta_groups: Dict[tuple[str, Any, Any], List[Dict[str, Any]]] = {}
    for event in document["events"]:
        if event["kind"] not in {"text_delta", "thinking_delta"} or not isinstance(
            event.get("delta"), str
        ):
            continue
        key = (
            event["kind"],
            event.get("content_index"),
            event.get("message_id"),
        )
        delta_groups.setdefault(key, []).append(event)
    removed_event_ids: set[int] = set()
    for events in delta_groups.values():
        combined = "".join(event["delta"] for event in events)
        sanitized = _strip_public_completion_sentinel_lines(combined)
        if sanitized == combined:
            continue
        if sanitized:
            events[0]["delta"] = sanitized
            removed_event_ids.update(id(event) for event in events[1:])
        else:
            removed_event_ids.update(id(event) for event in events)
    if removed_event_ids:
        document["events"] = [
            event
            for event in document["events"]
            if id(event) not in removed_event_ids
        ]
        for sequence, event in enumerate(document["events"]):
            event["sequence"] = sequence

    terminal = document["terminal"]
    if terminal["kind"] == "done":
        sanitized_messages: List[Dict[str, Any]] = []
        for message in terminal["assistant_messages"]:
            sanitized_blocks: List[Dict[str, Any]] = []
            for block in message["content"]:
                block_type = block.get("type")
                if block_type in {"text", "thinking"} and isinstance(
                    block.get("text"), str
                ):
                    text = _strip_public_completion_sentinel_lines(
                        block["text"]
                    )
                    if text:
                        sanitized_blocks.append({**block, "text": text})
                elif block_type == "provider_replay":
                    sanitized_blocks.append(
                        _strip_public_completion_sentinel_tree(block)
                    )
                else:
                    sanitized_blocks.append(block)
            if sanitized_blocks:
                sanitized_messages.append(
                    {**message, "content": sanitized_blocks}
                )
        terminal["assistant_messages"] = sanitized_messages
        terminal["provider_replay"] = _strip_public_completion_sentinel_tree(
            terminal.get("provider_replay", [])
        )
    observed_output = any(
        event["kind"] in _OUTPUT_EVENT_KINDS
        for event in document["events"]
    )
    if terminal["kind"] == "done":
        terminal["output_emitted"] = bool(
            observed_output or terminal["assistant_messages"]
        )
    else:
        terminal["output_emitted"] = bool(
            terminal.get("output_emitted") or observed_output
        )
    return encode_provider_exchange(document)




# Existing runtime normalization helpers -------------------------------------------------
def _portable_provider_payload(value: Any, *, seen: set[int] | None = None) -> Any:
    """Convert a provider SDK value to bounded redacted data for diagnostics only."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redaction.scrub_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return redaction.scrub_text(bytes(value).decode("utf-8", errors="replace"))[
            :8192
        ]
    active = seen if seen is not None else set()
    marker = id(value)
    if marker in active:
        return "<recursive>"
    active.add(marker)
    try:
        if isinstance(value, Mapping):
            return {
                redaction.scrub_text(str(key)): _portable_provider_payload(
                    item, seen=active
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_portable_provider_payload(item, seen=active) for item in value]
        if is_dataclass(value) and not isinstance(value, type):
            return {
                item.name: _portable_provider_payload(
                    getattr(value, item.name), seen=active
                )
                for item in fields(value)
            }
        for method_name in ("model_dump", "to_dict", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    converted = method()
                except Exception:
                    continue
                if converted is not value:
                    return _portable_provider_payload(converted, seen=active)
        return f"<{type(value).__name__}>"
    finally:
        active.discard(marker)


def sanitize_provider_result(result: ProviderResult) -> ProviderResult:
    """Remove operation secrets and SDK transport objects before lease release."""
    if not isinstance(result, ProviderResult):
        raise ProviderContractError("runtime returned an invalid ProviderResult")
    for message in result.messages:
        if not isinstance(message, ProviderMessage):
            raise ProviderContractError("runtime result contains an invalid message")
        message.content = _portable_provider_payload(message.content)
        message.finish_reason = (
            redaction.scrub_text(message.finish_reason)
            if message.finish_reason is not None
            else None
        )
        message.message_id = (
            redaction.scrub_text(message.message_id)
            if message.message_id is not None
            else None
        )
        message.raw_message = _portable_provider_payload(message.raw_message)
        message.raw_choice = _portable_provider_payload(message.raw_choice)
        message.reasoning = _portable_provider_payload(message.reasoning)
        message.annotations = (
            _portable_provider_payload(message.annotations)
            if isinstance(message.annotations, dict)
            else {}
        )
        message.tool_results = _portable_provider_payload(message.tool_results)
        for call in message.tool_calls:
            call.id = redaction.scrub_text(call.id) if call.id is not None else None
            call.name = (
                redaction.scrub_text(call.name) if call.name is not None else None
            )
            call.parsed_arguments = _portable_provider_payload(call.parsed_arguments)
            canonical_arguments = canonical_json(call.parsed_arguments)
            call.arguments = canonical_arguments
            call.arguments_json = canonical_arguments
            call.raw = _portable_provider_payload(call.raw)
    result.raw_response = _portable_provider_payload(result.raw_response)
    result.usage = (
        _portable_provider_payload(result.usage)
        if isinstance(result.usage, dict)
        else None
    )
    result.encrypted_reasoning = (
        _portable_provider_payload(result.encrypted_reasoning)
        if isinstance(result.encrypted_reasoning, list)
        else None
    )
    result.reasoning_summaries = (
        [redaction.scrub_text(str(item))[:8192] for item in result.reasoning_summaries]
        if result.reasoning_summaries is not None
        else None
    )
    result.reasoning_blocks = (
        _portable_provider_payload(result.reasoning_blocks)
        if isinstance(result.reasoning_blocks, list)
        else None
    )
    result.provider_replay = (
        _portable_provider_payload(result.provider_replay)
        if isinstance(result.provider_replay, list)
        else None
    )
    result.model = (
        redaction.scrub_text(result.model) if result.model is not None else None
    )
    result.metadata = (
        _portable_provider_payload(result.metadata)
        if isinstance(result.metadata, dict)
        else {}
    )
    return result


@dataclass
class ProviderExchangeRecorder:
    """Capture required provider stream events before SessionState projection."""

    correlation: ProviderCorrelation
    provider: ProviderIdentity
    request: ProviderRequest
    exchange_id: str = field(default_factory=lambda: f"px_{uuid.uuid4().hex}")
    events: List[ProviderEvent] = field(default_factory=list)

    @property
    def output_emitted(self) -> bool:
        return any(event.kind in _OUTPUT_EVENT_KINDS for event in self.events)

    def reset_unemitted_attempt(self) -> None:
        """Discard lifecycle-only events before a replay-safe retry."""
        if self.output_emitted:
            raise ProviderProtocolError(
                "cannot reset a provider attempt after normative output"
            )
        self.events.clear()

    def rebind_request_stream(self, stream: bool) -> None:
        """Record the effective stream posture of a replay-safe retry."""
        if not isinstance(stream, bool):
            raise ProviderContractError("request.stream must be boolean")
        if self.request.stream == stream:
            return
        self.reset_unemitted_attempt()
        self.request = ProviderRequest(
            stream=stream,
            messages=self.request.messages,
            tools=self.request.tools,
        )

    def rebind_provider(self, provider: ProviderIdentity) -> None:
        """Begin a replay-safe fallback attempt with its actual provider identity."""
        if not isinstance(provider, ProviderIdentity):
            raise ProviderContractError(
                "fallback provider identity must be a ProviderIdentity"
            )
        if provider == self.provider:
            return
        provider.as_dict()
        self.reset_unemitted_attempt()
        self.provider = provider

    def record(self, kind: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        if kind not in _EVENT_KINDS:
            raise ProviderProtocolError(f"unknown provider event kind: {kind!r}")
        if payload is not None and not isinstance(payload, Mapping):
            raise ProviderProtocolError("provider event payload must be an object")
        payload_dict = dict(payload or {})
        unknown = set(payload_dict) - _EVENT_INPUT_FIELDS[kind]
        if unknown:
            raise ProviderProtocolError(
                f"{kind} contains unknown input fields: {sorted(unknown)!r}"
            )
        if kind != "response_start" and not self.events:
            self.record("response_start")
        if kind == "response_start" and self.events:
            raise ProviderProtocolError(
                "response_start must be the first provider event"
            )

        is_response_start = kind == "response_start"
        is_tool = kind.startswith("tool_call_")
        is_delta = kind.endswith("_delta")
        try:
            indexed_values = [
                payload_dict[key]
                for key in ("content_index", "index")
                if key in payload_dict
            ]
            if len(indexed_values) > 1 and any(
                value != indexed_values[0] for value in indexed_values[1:]
            ):
                raise ProviderContractError(
                    "provider event content-index aliases disagree"
                )
            content_index = indexed_values[0] if indexed_values else 0
            message_id = _coalesce_text_fields(
                payload_dict,
                ("message_id", "item_id"),
                field_name="provider event message_id",
                required=not is_response_start,
            )
            call_id = (
                _coalesce_text_fields(
                    payload_dict,
                    ("call_id",),
                    field_name="provider event call_id",
                    required=True,
                )
                if is_tool
                else None
            )
            tool_name = (
                _coalesce_text_fields(
                    payload_dict,
                    ("name", "tool"),
                    field_name="provider event tool name",
                    required=kind == "tool_call_start",
                )
                if is_tool
                else None
            )
            if "provider_field" in payload_dict and payload_dict[
                "provider_field"
            ] not in {"reasoning", "reasoning_content"}:
                raise ProviderContractError(
                    "thinking delta contains an unknown provider field"
                )
            if kind == "text_end" and "text" in payload_dict and not isinstance(
                payload_dict["text"], str
            ):
                raise ProviderContractError("text_end text must be a string")

            arguments_json: Optional[str] = None
            parsed_arguments: Any = None
            if kind == "tool_call_end":
                argument_values = [
                    payload_dict[key]
                    for key in (
                        "arguments_json",
                        "arguments",
                        "parsed_arguments",
                        "arguments_parsed",
                    )
                    if key in payload_dict
                ]
                if not argument_values:
                    raise ProviderContractError(
                        "tool_call_end requires explicit arguments"
                    )
                normalized_arguments: List[Tuple[str, Any]] = []
                for raw_arguments in argument_values:
                    parsed = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                    normalized_arguments.append((canonical_json(parsed), parsed))
                if any(
                    encoded != normalized_arguments[0][0]
                    for encoded, _ in normalized_arguments[1:]
                ):
                    raise ProviderContractError(
                        "completed tool argument aliases disagree"
                    )
                arguments_json, parsed_arguments = normalized_arguments[0]

            delta = (
                _coalesce_text_fields(
                    payload_dict,
                    ("delta", "text", "arguments_delta"),
                    field_name="provider event delta",
                    max_length=65536,
                    required=True,
                )
                if is_delta
                else None
            )
            event = ProviderEvent(
                sequence=len(self.events),
                kind=kind,
                content_index=None if is_response_start else content_index,
                message_id=None if is_response_start else message_id,
                call_id=call_id,
                name=tool_name if kind == "tool_call_start" else None,
                delta=delta,
                arguments_json=arguments_json,
                arguments=parsed_arguments,
            )
            _validate_event_lifecycle(
                [*self.events, event], require_closed=False
            )
            event.validate()
        except (TypeError, ValueError, ProviderContractError):
            raise ProviderProtocolError(
                f"malformed {kind} provider event"
            ) from None
        self.events.append(event)

    def build(
        self, terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled
    ) -> ProviderExchangeV2:
        if isinstance(terminal, ProviderDone) and not self.events:
            self.record("response_start")
        return ProviderExchangeV2(
            schema_version="bb.provider_exchange.v2",
            exchange_id=self.exchange_id,
            correlation=self.correlation,
            provider=self.provider,
            request=self.request,
            events=list(self.events),
            terminal=terminal,
        ).validate()


@dataclass
class ProviderRuntimeContext:
    """Context object passed to provider runtimes."""

    session_state: Any
    agent_config: Dict[str, Any]
    stream: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    input_id: Optional[str] = None
    turn_id: Optional[str] = None
    exchange_recorder: Optional[ProviderExchangeRecorder] = None
    cancel_requested: Optional[Callable[[], bool]] = None

    def record_provider_event(
        self, kind: str, payload: Optional[Mapping[str, Any]] = None
    ) -> None:
        if self.exchange_recorder is not None:
            self.exchange_recorder.record(kind, payload)

    def raise_if_cancelled(self) -> None:
        """Raise the typed caller-cancellation boundary before more provider work."""

        if self.cancel_requested is None or not self.cancel_requested():
            return
        raise ProviderRuntimeError(
            "provider invocation cancelled",
            kind="transport",
            details={
                "code": "caller_cancelled",
                "classification": "cancelled",
                "cancelled": True,
                "cancel_owner": "caller",
                "reason_code": "caller_cancelled",
            },
            output_emitted=bool(
                self.exchange_recorder and self.exchange_recorder.output_emitted
            ),
        )


ProviderErrorKind = Literal[
    "adapter", "provider", "transport", "protocol", "configuration"
]
_MODEL_FALLBACK_REASONS = frozenset(
    {
        "provider_unavailable",
        "rate_limited",
        "model_unavailable",
        "capability_drift",
        "timeout_before_output",
    }
)


def _derive_model_fallback_reason(
    details: Mapping[str, Any],
    *,
    kind: ProviderErrorKind,
    output_emitted: bool,
) -> str | None:
    if output_emitted or kind in {"adapter", "configuration", "protocol"}:
        return None
    code = _safe_error_code(details.get("code"))
    classification = str(
        details.get("classification") or ""
    ).strip().lower()
    status = details.get("status_code", details.get("status"))
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    if (
        code
        in {
            "auth_failure",
            "authentication_failed",
            "unauthorized",
            "forbidden",
            "policy_rejection",
            "policy_denied",
        }
        or classification in {"auth", "authentication", "policy"}
        or status_code in {401, 403}
    ):
        return None
    if (
        code in {"rate_limited", "rate_limit", "too_many_requests"}
        or classification in {"rate_limit", "rate_limited"}
        or status_code == 429
    ):
        return "rate_limited"
    if (
        code
        in {
            "timeout",
            "request_timeout",
            "provider_timeout",
            "connection_timeout",
        }
        or classification in {"timeout", "timed_out"}
    ):
        return "timeout_before_output"
    if code in {"model_unavailable", "model_not_found", "invalid_model"}:
        return "model_unavailable"
    if code in {"capability_drift", "unsupported_capability"}:
        return "capability_drift"
    if status_code is not None and status_code >= 500:
        return "provider_unavailable"
    if kind in {"provider", "transport"} and code in {
        "provider_error",
        "transport_error",
        "service_unavailable",
        "connection_error",
        "route_circuit_open",
    }:
        return "provider_unavailable"
    return None


class ProviderRuntimeError(RuntimeError):
    """Raised when a provider runtime encounters a classified fatal error."""

    def __init__(
        self,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        kind: ProviderErrorKind = "provider",
        output_emitted: bool = False,
        model_fallback_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.details: Dict[str, Any] = dict(details or {})
        self.kind: ProviderErrorKind = kind
        self.output_emitted = output_emitted
        derived_reason = _derive_model_fallback_reason(
            self.details, kind=kind, output_emitted=output_emitted
        )
        explicit_reason = (
            model_fallback_reason
            if model_fallback_reason in _MODEL_FALLBACK_REASONS
            else None
        )
        self.model_fallback_reason = derived_reason or (
            explicit_reason
            if not output_emitted
            and kind in {"provider", "transport"}
            and _safe_error_code(self.details.get("code"))
            not in {
                "auth_failure",
                "authentication_failed",
                "unauthorized",
                "forbidden",
                "policy_rejection",
                "policy_denied",
            }
            and str(
                self.details.get("classification") or ""
            ).strip().lower()
            not in {"auth", "authentication", "policy"}
            and self.details.get("status_code", self.details.get("status"))
            not in {401, 403, "401", "403"}
            else None
        )

    @property
    def replay_safe(self) -> bool:
        return not self.output_emitted

    @property
    def safe_code(self) -> str:
        value = self.details.get("code") if isinstance(self.details, dict) else None
        return _safe_error_code(value or f"{self.kind}_error")


class ProviderRuntime:
    """Interface for provider runtimes."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        raise NotImplementedError

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        raise NotImplementedError


__all__ = [
    "ProviderContractError",
    "ProviderProtocolError",
    "ProviderCorrelation",
    "ProviderIdentity",
    "ProviderRequest",
    "ProviderEvent",
    "ProviderDone",
    "ProviderErrorTerminal",
    "ProviderCancelled",
    "ProviderExchangeV2",
    "ProviderExchangeRecorder",
    "encode_provider_exchange",
    "strip_provider_exchange_completion_sentinels",
    "canonical_json",
    "parse_canonical_json",
    "normalize_usage",
    "ProviderToolCall",
    "ProviderMessage",
    "ProviderResult",
    "ProviderRuntimeContext",
    "ProviderRuntimeError",
    "ProviderRuntime",
    "sanitize_provider_result",
    "normalize_request_messages",
    "normalize_content",
]
