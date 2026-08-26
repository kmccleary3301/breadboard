"""Shared OpenAI-compatible request and response conversion helpers."""

from __future__ import annotations


from typing import Any, Dict, List, Optional

from ...contracts import (
    ProviderContractError,
    ProviderRuntimeContext,
    ProviderToolCall,
    canonical_json,
)
from ...input_media import resolve_input_media


class OpenAIConversionMixin:
    def _convert_messages_to_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        context: ProviderRuntimeContext | None = None,
    ) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role in {"tool", "tool_result"}:
                if isinstance(content, list):
                    if not content:
                        raise ProviderContractError(
                            "tool-result messages require content"
                        )
                    for block in content:
                        if (
                            not isinstance(block, dict)
                            or block.get("type") != "tool_result"
                            or not isinstance(block.get("call_id"), str)
                            or not block.get("call_id")
                            or "content" not in block
                        ):
                            raise ProviderContractError(
                                "malformed tool_result block"
                            )
                        value = block["content"]
                        converted.append(
                            {
                                "role": "tool",
                                "tool_call_id": block["call_id"],
                                "content": (
                                    value
                                    if isinstance(value, str)
                                    else canonical_json(value)
                                ),
                            }
                        )
                elif role == "tool":
                    call_id = (
                        message.get("tool_call_id")
                        or message.get("tool_use_id")
                        or message.get("call_id")
                    )
                    if not isinstance(call_id, str) or not call_id:
                        raise ProviderContractError(
                            "tool message requires call_id"
                        )
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": (
                                ""
                                if content is None
                                else content
                                if isinstance(content, str)
                                else canonical_json(content)
                            ),
                        }
                    )
                else:
                    raise ProviderContractError(
                        "canonical tool_result messages require block content"
                    )
                continue
            if role not in {
                "system",
                "developer",
                "user",
                "assistant",
            }:
                raise ProviderContractError(f"unsupported chat role: {role!r}")

            text_content: Any = content
            canonical_calls: List[ProviderToolCall] = []
            reasoning_parts: List[str] = []
            if isinstance(content, list):
                visible_blocks: List[Dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        raise ProviderContractError(
                            "chat content blocks must be objects"
                        )
                    block_type = block.get("type")
                    if block_type == "tool_call":
                        arguments = (
                            block.get("arguments_json")
                            if "arguments_json" in block
                            else block.get("arguments")
                        )
                        if arguments is None:
                            raise ProviderContractError(
                                "tool call requires arguments"
                            )
                        call = ProviderToolCall(
                            id=block.get("call_id", block.get("id")),
                            name=block.get("name"),
                            arguments=arguments,
                        )
                        call.as_dict()
                        canonical_calls.append(call)
                    elif block_type == "thinking":
                        value = block.get("text")
                        if not isinstance(value, str):
                            raise ProviderContractError(
                                "thinking block requires text"
                            )
                        reasoning_parts.append(value)
                    elif block_type in {
                        "redacted_thinking",
                        "provider_replay",
                        "tool_result",
                    }:
                        raise ProviderContractError(
                            f"chat adapter cannot replay {block_type}"
                        )
                    elif block_type == "text":
                        visible_blocks.append(
                            {
                                "type": "text",
                                "text": str(block.get("text") or ""),
                            }
                        )
                    elif block_type == "media":
                        media = resolve_input_media(block, context)
                        visible_blocks.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": media.data_url},
                            }
                        )
                    else:
                        raise ProviderContractError(
                            f"unsupported OpenAI Chat content block: {block_type!r}"
                        )
                text_content = visible_blocks
            elif content is not None and not isinstance(content, str):
                raise ProviderContractError(
                    "chat content must be text or blocks"
                )

            raw_calls = message.get("tool_calls")
            if raw_calls is not None:
                canonical_calls.extend(self._extract_tool_calls(message))
            converted_message: Dict[str, Any] = {
                "role": role,
                "content": "" if text_content is None else text_content,
            }
            if canonical_calls:
                seen: Dict[str, Dict[str, Any]] = {}
                converted_calls: List[Dict[str, Any]] = []
                for call in canonical_calls:
                    call_data = call.as_dict()
                    call_id = call_data["call_id"]
                    prior = seen.get(call_id)
                    if prior is not None:
                        if prior != call_data:
                            raise ProviderContractError(
                                "conflicting duplicate tool call identifier"
                            )
                        continue
                    seen[call_id] = call_data
                    converted_calls.append(
                        {
                            "id": call_data["call_id"],
                            "type": "function",
                            "function": {
                                "name": call_data["name"],
                                "arguments": call_data["arguments_json"],
                            },
                        }
                    )
                converted_message["tool_calls"] = converted_calls
            if reasoning_parts:
                converted_message["reasoning_content"] = "\n\n".join(
                    reasoning_parts
                )
            for field_name in (
                "name",
                "reasoning",
                "reasoning_content",
                "reasoning_details",
            ):
                field_value = message.get(field_name)
                if field_value is not None and field_name not in converted_message:
                    converted_message[field_name] = field_value
            converted.append(converted_message)
        return converted

    def _convert_tools_to_openai(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None
        # Tools already follow OpenAI schema in upstream config; clone defensively
        return [dict(tool) for tool in tools]

    def _get_attr(self, obj: Any, name: str, default: Any = None) -> Any:
        if hasattr(obj, name):
            return getattr(obj, name)
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default

    def _non_null_unknown_fields(
        self, value: Any, allowed: set[str]
    ) -> set[str]:
        if isinstance(value, dict):
            data = value
        else:
            data = None
            model_dump = getattr(value, "model_dump", None)
            if callable(model_dump):
                try:
                    candidate = model_dump(exclude_none=True)
                except TypeError:
                    candidate = model_dump()
                if isinstance(candidate, dict):
                    data = candidate
            if data is None:
                candidate = getattr(value, "__dict__", None)
                if isinstance(candidate, dict):
                    data = candidate
        if data is None:
            return set()
        return {
            key
            for key, item in data.items()
            if isinstance(key, str)
            and not key.startswith("_")
            and item is not None
            and key not in allowed
        }

    def _message_content_to_text(self, content: Any) -> Optional[str]:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if not isinstance(content, (list, tuple)):
            raise ProviderContractError(
                "provider message content must be text or a block list"
            )
        parts: List[str] = []
        for block in content:
            block_type = self._get_attr(block, "type")
            if block_type not in {"input_text", "output_text", "text"}:
                raise ProviderContractError(
                    f"unsupported provider content block: {block_type!r}"
                )
            text_val = self._get_attr(block, "text")
            if not isinstance(text_val, str):
                raise ProviderContractError(
                    "provider text content block requires text"
                )
            parts.append(text_val)
        return "".join(parts)

    def _extract_tool_calls(self, message: Any) -> List[ProviderToolCall]:
        results: List[ProviderToolCall] = []
        raw_tool_calls = self._get_attr(message, "tool_calls") or []
        if not isinstance(raw_tool_calls, (list, tuple)):
            raise ProviderContractError("tool_calls must be a list")
        for raw in raw_tool_calls:
            if self._non_null_unknown_fields(
                raw, {"index", "id", "type", "function"}
            ):
                raise ProviderContractError(
                    "tool call contains unknown fields"
                )
            fn = self._get_attr(raw, "function")
            if fn is None:
                raise ProviderContractError("tool call requires function data")
            if self._non_null_unknown_fields(fn, {"name", "arguments"}):
                raise ProviderContractError(
                    "tool call function contains unknown fields"
                )
            arguments = self._get_attr(fn, "arguments")
            if arguments is None:
                raise ProviderContractError("tool call requires arguments")
            call_type = self._get_attr(raw, "type")
            if call_type != "function":
                raise ProviderContractError("tool call requires function type")
            call = ProviderToolCall(
                id=self._get_attr(raw, "id"),
                name=self._get_attr(fn, "name"),
                arguments=arguments,
                type=call_type,
                raw=raw,
            )
            call.as_dict()
            results.append(call)
        return results

    def _extract_reasoning_fields(self, message: Any) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        for field_name in ("reasoning_content", "reasoning", "reasoning_details"):
            value = self._get_attr(message, field_name)
            if value is not None:
                fields[field_name] = value
        return fields

    def _extract_usage(self, response: Any) -> Optional[Dict[str, Any]]:
        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return None
        if isinstance(usage_obj, dict):
            return dict(usage_obj)
        model_dump = getattr(usage_obj, "model_dump", None)
        if callable(model_dump):
            value = model_dump()
            if isinstance(value, dict):
                return value
        raise ProviderContractError("provider usage must be an object")

