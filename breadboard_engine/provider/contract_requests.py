"""Provider request identity and message transport normalization."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence
from .contract_wire import (ProviderContractError, _canonical_value, _coalesce_text_fields, _require_text, _strict_dict, canonical_json)
from .profiles import validate_wire_model
from . import contract_messages, contract_tools

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
        validate_wire_model(self.model)

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
    _tool_api_variant: Optional[str] = field(default=None, repr=False, compare=False)

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
            contract_tools._normalize_tool_schema(tool, index, api_variant=self._tool_api_variant)
            for index, tool in enumerate(self.tools)
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
                "phase",
                "_provider_tool_batch",
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
                contract_messages.normalize_content(semantic_content, role=role)
                if isinstance(semantic_content, str) or semantic_content
                else []
            )
        if is_tool_transport and not isinstance(content_raw, str):
            if any(block.get("type") == "provider_replay" for block in blocks):
                if not all(block.get("schema_version") == "openai.responses.tool_output.v1"
                           and block.get("provider_id") in {"openai", "openrouter"} for block in blocks):
                    from .anthropic_replay import replay_messages

                    replay_messages(blocks)
            elif not blocks or any(block.get("type") != "tool_result" for block in blocks):
                raise ProviderContractError(
                    "tool-result messages require correlated tool_result content"
                )
        if transport_call_id is not None and not (
            is_tool_transport and isinstance(content_raw, str)
        ):
            raise ProviderContractError(
                "transport call id is supported only on string tool-result messages"
            )
        reasoning_blocks = contract_messages._normalized_reasoning_blocks(
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
            call = contract_tools.normalize_tool_call_dict(raw_call, call_index)
            blocks.append({"type": "tool_call", **call.as_dict()})
        raw_results = message.get("tool_results")
        if raw_results is None:
            raw_results = []
        if not isinstance(raw_results, list):
            raise ProviderContractError("message.tool_results must be a list")
        for result_index, raw_result in enumerate(raw_results):
            tool_result = contract_tools.normalize_tool_result_dict(raw_result, result_index)
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
        phase = message.get("phase")
        if phase is not None:
            if role != "assistant" or phase not in ("commentary", "final_answer"):
                raise ProviderContractError("message.phase requires an assistant commentary or final_answer")
            normalized["phase"] = phase
        if "_provider_tool_batch" in message:
            batch = message["_provider_tool_batch"]
            if role != "assistant" or type(batch) is not int or batch < 0:
                raise ProviderContractError("message._provider_tool_batch requires an assistant nonnegative integer")
            normalized["_provider_tool_batch"] = batch
        message_id = _coalesce_text_fields(
            message,
            ("message_id", "id"),
            field_name=f"request.messages[{index}].message_id",
        )
        if message_id is not None:
            normalized["message_id"] = message_id
        result.append(normalized)
    return result
