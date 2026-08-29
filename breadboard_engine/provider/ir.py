"""Strict provider-agnostic intermediate representation for legacy callers."""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .contracts import ProviderContractError, canonical_json, parse_canonical_json


Role = Literal["system", "user", "developer", "assistant", "tool_result"]
PartType = Literal["text", "json", "media"]
DeltaType = Literal["text", "tool_call", "reasoning_meta", "logprob", "finish"]
FinishReason = Literal["stop", "toolUse", "length", "error", "aborted"]


@dataclass
class IRPart:
    type: PartType
    text: Optional[str] = None
    value: Optional[Any] = None
    kind: Optional[str] = None
    uri: Optional[str] = None
    mime: Optional[str] = None

    @staticmethod
    def text_part(content: str) -> "IRPart":
        if not isinstance(content, str):
            raise ProviderContractError("text part requires a string")
        return IRPart(type="text", text=content)

    @staticmethod
    def json_part(value: Any) -> "IRPart":
        canonical_json(value)
        return IRPart(type="json", value=value)

    @staticmethod
    def media_part(kind: str, uri: str, mime: Optional[str] = None) -> "IRPart":
        if not isinstance(kind, str) or not kind or not isinstance(uri, str) or not uri:
            raise ProviderContractError("media part requires kind and uri")
        return IRPart(type="media", kind=kind, uri=uri, mime=mime)


@dataclass
class IRToolCall:
    id: str
    name: str
    args: Any
    group: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ProviderContractError("tool call id is required")
        if not isinstance(self.name, str) or not self.name:
            raise ProviderContractError("tool name is required")
        canonical_json(self.args)


@dataclass
class IRToolResult:
    tool_call_id: str
    ok: bool
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id:
            raise ProviderContractError("tool result call id is required")
        if not isinstance(self.ok, bool):
            raise ProviderContractError("tool result ok must be boolean")
        if self.error is not None and not isinstance(self.error, dict):
            raise ProviderContractError("tool result error must be an object")
        if self.result is not None:
            canonical_json(self.result)


@dataclass
class IRMessage:
    id: str
    role: Role
    parts: List[IRPart] = field(default_factory=list)
    tool_calls: List[IRToolCall] = field(default_factory=list)
    tool_results: List[IRToolResult] = field(default_factory=list)
    corr_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    time: Optional[str] = None


@dataclass
class IRDeltaEvent:
    cursor: str
    type: DeltaType
    payload: Any


@dataclass
class IRFinish:
    reason: FinishReason
    usage: Dict[str, Any]
    provider_meta: Optional[Any] = None
    agent_summary: Optional[Dict[str, Any]] = None


@dataclass
class IRConversation:
    id: str
    ir_version: str
    messages: List[IRMessage]
    events: List[IRDeltaEvent] = field(default_factory=list)
    finish: Optional[IRFinish] = None


def _normalize_tool_call(raw: Dict[str, Any], default_id: str) -> IRToolCall:
    if not isinstance(raw, dict):
        raise ProviderContractError("tool call must be an object")
    fn = raw.get("function") if isinstance(raw.get("function"), dict) else {}
    name = raw.get("name") or fn.get("name")
    call_id = raw.get("id") or raw.get("call_id") or default_id
    if not isinstance(name, str) or not name:
        raise ProviderContractError("tool call name is required")
    if not isinstance(call_id, str) or not call_id:
        raise ProviderContractError("tool call id is required")
    args = raw.get("arguments", fn.get("arguments"))
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (TypeError, ValueError):
            raise ProviderContractError("malformed tool call arguments") from None
        if args != canonical_json(parsed):
            raise ProviderContractError("tool call arguments must be canonical JSON")
        args_payload = parsed
    elif args is None:
        args_payload = {}
    else:
        args_payload = args
    return IRToolCall(id=call_id, name=name, args=args_payload, group = raw.get("group"))


def _normalize_tool_result(raw: Dict[str, Any], fallback_call_id: str) -> IRToolResult:
    if not isinstance(raw, dict):
        raise ProviderContractError("tool result must be an object")
    call_id = (
        raw.get("tool_call_id") or raw.get("tool_use_id")
        or raw.get("call_id")
        or fallback_call_id
    )
    ok = raw.get("ok", True)
    if not isinstance(ok, bool):
        raise ProviderContractError("tool result ok must be boolean")
    result = raw["result"] if "result" in raw else raw.get("out")
    error = raw.get("error") if not ok else raw.get("error_info")
    if isinstance(error, str):
        error = {"message": error}
    return IRToolResult(tool_call_id=str(call_id), ok=ok, result=result, error=error)


def convert_legacy_messages(messages: List[Dict[str, Any]]) -> List[IRMessage]:
    """Convert legacy message dicts into IR messages, failing closed on loss."""
    if not isinstance(messages, list):
        raise ProviderContractError("legacy messages must be a list")
    ir_messages: List[IRMessage] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ProviderContractError(f"legacy message {idx} must be an object")
        role = msg.get("role")
        if role == "tool":
            role = "tool_result"
        if role not in {"system", "user", "developer", "assistant", "tool_result"}:
            raise ProviderContractError(f"unknown legacy message role: {role!r}")
        msg_id = str(msg.get("id", f"msg_{idx}"))
        if not msg_id:
            raise ProviderContractError("message id is required")

        parts: List[IRPart] = []
        content = msg.get("content")
        if isinstance(content, list):
            for part_idx, part in enumerate(content):
                if not isinstance(part, dict):
                    raise ProviderContractError(
                        f"content block {part_idx} must be an object"
                    )
                p_type = part.get("type")
                if p_type in {"text", "input_text", "output_text"}:
                    if "text" not in part or not isinstance(part["text"], str):
                        raise ProviderContractError("text content block requires text")
                    parts.append(IRPart.text_part(part["text"]))
                elif p_type == "json":
                    parts.append(IRPart.json_part(part.get("data")))
                elif p_type in {"image", "audio", "video", "media"}:
                    parts.append(IRPart.media_part(part.get("type", "media"), part.get("uri", ""), part.get("mime"),
                        ))
                elif p_type in {"thinking", "redacted_thinking"}:
                    parts.append(IRPart.json_part(dict(part)))
                else:
                    raise ProviderContractError(
                        f"unknown content block type: {p_type!r}"
                    )
        elif isinstance(content, str):
            parts.append(IRPart.text_part(content))
        elif content is not None:
            parts.append(IRPart.json_part(content))

        tool_calls_raw = msg.get("tool_calls", [])
        if not isinstance(tool_calls_raw, list):
            raise ProviderContractError("tool_calls must be a list")
        tool_calls = [
            _normalize_tool_call(tc, f"tc_{idx}_{tc_idx}")
            for tc_idx, tc in enumerate(tool_calls_raw)
        ]

        tool_results_raw = msg.get("tool_results", [])
        if not isinstance(tool_results_raw, list):
            raise ProviderContractError("tool_results must be a list")
        tool_results = [
            _normalize_tool_result(tr, f"tc_{idx}_{tr_idx}")
            for tr_idx, tr in enumerate(tool_results_raw)
        ]
        tags = msg.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ProviderContractError("message tags must be a list of strings")
        corr_id = (msg.get("corr_id")
            if msg.get("corr_id") is not None
            else msg.get("parent_id")
        )
        if corr_id is not None and not isinstance(corr_id, str):
            raise ProviderContractError("message correlation id must be a string")
        time_value = msg.get("time")
        if time_value is not None and not isinstance(time_value, str):
            raise ProviderContractError("message time must be a string")

        ir_messages.append(
            IRMessage(
                id=msg_id,
                role=role,
                parts=parts,
                tool_calls=tool_calls,
                tool_results=tool_results,
                corr_id=corr_id,
                tags=list(tags),
                time=time_value,
            )
        )

    return ir_messages


__all__ = [
    "Role",
    "PartType",
    "DeltaType",
    "FinishReason",
    "IRPart",
    "IRToolCall",
    "IRToolResult",
    "IRMessage",
    "IRDeltaEvent",
    "IRFinish",
    "IRConversation",
    "convert_legacy_messages",
]
