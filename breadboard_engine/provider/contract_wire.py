"""Provider wire-schema and canonical JSON validation primitives."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Mapping, Optional, Sequence

from jsonschema import Draft202012Validator

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
