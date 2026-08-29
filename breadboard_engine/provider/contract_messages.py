"""Provider message, tool, request, and DTO normalization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract_wire import (
    ProviderContractError,
    _CONTENT_TYPES,
    _bounded_json_value,
    _canonical_value,
    _coalesce_text_fields,
    _require_text,
    _strict_dict,
    canonical_json,
)


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
