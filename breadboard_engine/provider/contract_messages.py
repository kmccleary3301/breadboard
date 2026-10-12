"""Provider message content, reasoning, replay, and result normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .contract_wire import (
    ProviderContractError,
    _CONTENT_TYPES,
    _bounded_json_value,
    _coalesce_text_fields,
    _require_text,
    _strict_dict,
    canonical_json,
)
from . import contract_tools


# Annotation keys whose values are model reasoning (merged with ``reasoning``), with
# the label used in contract errors.
_REASONING_ANNOTATION_LABELS = {
    "reasoning_content": "reasoning_content",
    "reasoning": "reasoning annotation",
    "reasoning_details": "reasoning_details",
}
REASONING_ANNOTATION_KEYS = tuple(_REASONING_ANNOTATION_LABELS)


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
        # Deduplicate aliases across fields, not ordered blocks in one field.
        prior_fields = seen.copy()
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
            if identity not in prior_fields:
                seen.add(identity)
                blocks.append(block)

    append_value("reasoning", reasoning)
    for key, label in _REASONING_ANNOTATION_LABELS.items():
        append_value(label, annotations.get(key))
    return blocks



@dataclass
class ProviderMessage:
    """Normalized provider message returned from a runtime."""

    role: str
    content: Any
    tool_calls: List[contract_tools.ProviderToolCall] = field(default_factory=list)
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
            if not isinstance(call, contract_tools.ProviderToolCall):
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
            normalized = contract_tools.normalize_tool_result_dict(item)
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
        phase = self.annotations.get("phase")
        if phase is not None:
            if self.role != "assistant" or phase not in ("commentary", "final_answer"):
                raise ProviderContractError("message.phase requires an assistant commentary or final_answer")
            result["phase"] = phase
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
            if block.get("kind") == "codex_image_url":
                payload = _strict_dict(
                    block, field_name="Codex image URL",
                    allowed={"type", "kind", "uri", "detail"},
                )
                if role != "user":
                    raise ProviderContractError("media blocks require the user role")
                uri = payload.get("uri")
                if not isinstance(uri, str):
                    raise ProviderContractError("Codex image URL must be text")
                detail = payload.get("detail", "high")
                if detail not in {"auto", "low", "high", "original"}:
                    raise ProviderContractError("invalid Codex image detail")
                blocks.append({"type": "media", "kind": "codex_image_url",
                               "uri": uri, "detail": detail})
                continue
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
                    "raw_arguments",
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
                and "raw_arguments" not in payload
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
            call = contract_tools.ProviderToolCall(
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
                raw_arguments=payload.get("raw_arguments"),
                allow_raw_arguments="raw_arguments" in payload,
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
    if provider_id in {"openai", "openrouter"} and schema_version == "openai.responses.hosted.v1":
        # Stock protocol/src/models.rs:869-879,1193-1220.
        if scope != "same_provider":
            raise ProviderContractError("hosted search requires same-provider replay")
        hosted = _strict_dict(replay_payload, field_name="hosted search",
                              allowed={"type", "status", "action"})
        if hosted.get("type") != "web_search_call":
            raise ProviderContractError("unsupported hosted replay type")
        if "status" in hosted and not isinstance(hosted["status"], str):
            raise ProviderContractError("hosted search status must be text")
        if "action" in hosted:
            action = hosted["action"]
            if not isinstance(action, Mapping) or not isinstance(action.get("type"), str):
                raise ProviderContractError("hosted search action requires a type")
            fields = {"search": {"query", "queries"}, "open_page": {"url"},
                      "find_in_page": {"url", "pattern"}, "other": set()}
            if action["type"] not in fields:
                raise ProviderContractError("unsupported hosted search action")
            _strict_dict(action, field_name="hosted search action",
                         allowed={"type", *fields[action["type"]]})
            for field, value in action.items():
                if field == "queries":
                    if not isinstance(value, list) or any(not isinstance(query, str) for query in value):
                        raise ProviderContractError("hosted search queries must be a text array")
                elif not isinstance(value, str):
                    raise ProviderContractError("hosted search action fields must be text")
        canonical_json(hosted)
        return {"provider_id": provider_id, "schema_version": schema_version,
                "replay_scope": scope, "payload": hosted}
    if provider_id in {"openai", "openrouter"} and schema_version == "openai.responses.tool_output.v1":
        if scope != "same_provider":
            raise ProviderContractError("native tool outputs require same-provider replay")
        native = _strict_dict(
            replay_payload, field_name="native tool output", allowed={"type", "call_id", "output"},
        )
        if native.get("type") != "function_call_output":
            raise ProviderContractError("unsupported native tool output type")
        _require_text(native.get("call_id"), "native tool output call_id", max_length=256)
        if not isinstance(native.get("output"), list):
            raise ProviderContractError("native tool output requires a content-item array")
        for item in native["output"]:
            if not isinstance(item, Mapping):
                raise ProviderContractError("native tool content items must be objects")
            kind = item.get("type")
            if kind == "input_image":
                image = _strict_dict(item, field_name="native output image",
                                     allowed={"type", "image_url", "detail"})
                if not isinstance(image.get("image_url"), str):
                    raise ProviderContractError("native output image_url must be text")
                if ("detail" in image and (not isinstance(image["detail"], str)
                                           or image["detail"] not in {"auto", "low", "high", "original"})):
                    raise ProviderContractError("invalid native output image detail")
            elif kind == "input_text":
                text = _strict_dict(item, field_name="native output text", allowed={"type", "text"})
                if not isinstance(text.get("text"), str):
                    raise ProviderContractError("native output text must be text")
            else:
                raise ProviderContractError("unsupported native tool content item")
        canonical_json(native)
        return {"provider_id": provider_id, "schema_version": schema_version,
                "replay_scope": scope, "payload": native}
    if provider_id in {"openai", "openrouter"} and schema_version == "openai.responses.tools.v1":
        # Stock protocol/src/models.rs:807-841. This typed envelope is produced
        # only by the explicitly selected custom/client-search runtime paths.
        if scope != "same_provider":
            raise ProviderContractError("native tool calls require same-provider replay")
        kind = replay_payload.get("type")
        if kind == "custom_tool_call":
            required = {"type", "call_id", "name", "input"}
        elif kind == "tool_search_call":
            required = {"type", "call_id", "execution", "arguments"}
        elif kind == "function_call":
            required = {"type", "call_id", "name", "arguments"}
        else:
            raise ProviderContractError("unsupported native tool replay type")
        native = _strict_dict(
            replay_payload, field_name="native tool replay",
            allowed=required | ({"namespace"} if kind == "function_call" else {"status"}),
        )
        if not required.issubset(native):
            raise ProviderContractError("native tool replay requires its protocol fields")
        _require_text(native["call_id"], "native tool call_id", max_length=256)
        if "status" in native and not isinstance(native["status"], str):
            raise ProviderContractError("native tool status must be text or omitted")
        if kind in {"custom_tool_call", "function_call"}:
            _require_text(native["name"], "native tool name", max_length=256)
            field = "input" if kind == "custom_tool_call" else "arguments"
            if not isinstance(native[field], str):
                raise ProviderContractError(f"native tool {field} must be text")
            if "namespace" in native and not isinstance(native["namespace"], str):
                raise ProviderContractError("native tool namespace must be text or omitted")
        elif not isinstance(native["execution"], str):
            raise ProviderContractError("tool-search execution must be text")
        canonical_json(native)
        return {"provider_id": provider_id, "schema_version": schema_version,
                "replay_scope": scope, "payload": native}
    if provider_id == "anthropic" and schema_version == "anthropic.messages.tool_result.v1":
        if scope != "same_provider" or set(replay_payload) != {"messages"}:
            raise ProviderContractError("invalid native tool continuation envelope")
        canonical_json(replay_payload)
        return {"provider_id": provider_id, "schema_version": schema_version,
                "replay_scope": scope, "payload": dict(replay_payload)}
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


def responses_image_tool_result_message(
    call_id: str, output: List[Dict[str, Any]], *, provider_id: str = "openai",
) -> Dict[str, Any]:
    """Carry stock typed image output through the canonical request boundary."""
    return {"role": "tool_result", "content": [{
        "type": "provider_replay", "provider_id": provider_id,
        "schema_version": "openai.responses.tool_output.v1", "replay_scope": "same_provider",
        "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
    }]}


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


