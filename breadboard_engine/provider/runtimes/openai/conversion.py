"""Shared OpenAI-compatible request and response conversion helpers."""

from __future__ import annotations

import json

from typing import Any, Dict, List, Optional

from ...contracts import ProviderMessage, ProviderToolCall


class OpenAIConversionMixin:
    def _convert_messages_to_chat(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        passthrough_fields = (
            "name",
            "tool_call_id",
            "tool_calls",
            "reasoning",
            "reasoning_content",
            "reasoning_details",
        )
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if content is None and role in {"assistant", "tool", "user", "system"}:
                # Some OpenAI-compatible routes reject null `content` values.
                # Normalize to empty string while preserving tool and reasoning fields.
                content = ""
            converted_message: Dict[str, Any] = {"role": role, "content": content}
            for field_name in passthrough_fields:
                field_value = message.get(field_name)
                if field_value is not None:
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

    def _message_content_to_text(self, content: Any) -> Optional[str]:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        parts: List[str] = []
        try:
            for block in content:
                block_type = self._get_attr(block, "type")
                if block_type in {"input_text", "output_text", "text"}:
                    text_val = self._get_attr(block, "text", "")
                    if text_val:
                        parts.append(str(text_val))
        except Exception:
            return None
        return "".join(parts) if parts else None

    def _extract_tool_calls(self, message: Any) -> List[ProviderToolCall]:
        results: List[ProviderToolCall] = []
        raw_tool_calls = self._get_attr(message, "tool_calls") or []
        for raw in raw_tool_calls:
            fn = self._get_attr(raw, "function", {}) or {}
            arguments = self._get_attr(fn, "arguments", "{}")
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(arguments)
                except Exception:
                    arguments = "{}"
            results.append(
                ProviderToolCall(
                    id=self._get_attr(raw, "id"),
                    name=self._get_attr(fn, "name"),
                    arguments=arguments,
                    type=self._get_attr(raw, "type", "function"),
                    raw=raw,
                )
            )
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
        try:
            return dict(usage_obj)
        except Exception:
            try:
                return usage_obj.model_dump()  # type: ignore[attr-defined]
            except Exception:
                return None

