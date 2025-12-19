"""Provider-agnostic intermediate representation (IR) for conversations and tool use."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Literal, Dict


Role = Literal["system", "user", "assistant", "tool"]
PartType = Literal["text", "json", "media"]
DeltaType = Literal["text", "tool_call", "reasoning_meta", "logprob", "finish"]
FinishReason = Literal["stop", "tool_call", "length", "error"]


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
        return IRPart(type="text", text=content)

    @staticmethod
    def json_part(value: Any) -> "IRPart":
        return IRPart(type="json", value=value)

    @staticmethod
    def media_part(kind: str, uri: str, mime: Optional[str] = None) -> "IRPart":
        return IRPart(type="media", kind=kind, uri=uri, mime=mime)


@dataclass
class IRToolCall:
    id: str
    name: str
    args: Any
    group: Optional[str] = None


@dataclass
class IRToolResult:
    tool_call_id: str
    ok: bool
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


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
    name = raw.get("name") or raw.get("function", {}).get("name", "")
    if not name:
        name = "unknown_tool"
    call_id = raw.get("id") or default_id
    args = raw.get("arguments")
    if isinstance(args, str):
        args_payload: Any
        try:
            import json as _json
            args_payload = _json.loads(args)
        except Exception:
            args_payload = {"raw": args}
    else:
        args_payload = args
    group = raw.get("group") or raw.get("metadata", {}).get("group")
    return IRToolCall(id=str(call_id), name=str(name), args=args_payload, group=group)


def _normalize_tool_result(raw: Dict[str, Any], fallback_call_id: str) -> IRToolResult:
    call_id = raw.get("tool_call_id") or fallback_call_id
    ok = bool(raw.get("ok", True))
    result = raw.get("result") or raw.get("out")
    error = raw.get("error") if not ok else raw.get("error_info")
    if isinstance(error, str):
        error = {"message": error}
    return IRToolResult(tool_call_id=str(call_id), ok=ok, result=result, error=error)


def convert_legacy_messages(messages: List[Dict[str, Any]]) -> List[IRMessage]:
    """Convert legacy message dicts into IR messages."""
    ir_messages: List[IRMessage] = []
    for idx, msg in enumerate(messages):
        role = msg.get("role", "assistant")
        role_l: Role = role if role in ("system", "user", "assistant", "tool") else "assistant"
        msg_id = str(msg.get("id", f"msg_{idx}"))

        parts: List[IRPart] = []
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                p_type = part.get("type")
                if p_type == "text":
                    parts.append(IRPart.text_part(str(part.get("text", ""))))
                elif p_type == "json":
                    parts.append(IRPart.json_part(part.get("data")))
                elif p_type in {"image", "audio", "video", "media"}:
                    parts.append(IRPart.media_part(part.get("type", "media"), part.get("uri", ""), part.get("mime")))
                else:
                    parts.append(IRPart.json_part(part))
        elif isinstance(content, str):
            parts.append(IRPart.text_part(content))
        elif content is not None:
            parts.append(IRPart.json_part(content))

        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls: List[IRToolCall] = []
        if isinstance(tool_calls_raw, list):
            for tc_idx, tc in enumerate(tool_calls_raw):
                try:
                    tool_calls.append(_normalize_tool_call(tc, f"tc_{idx}_{tc_idx}"))
                except Exception:
                    continue

        tool_results_raw = msg.get("tool_results") or []
        tool_results: List[IRToolResult] = []
        if isinstance(tool_results_raw, list):
            for tr_idx, tr in enumerate(tool_results_raw):
                try:
                    tool_results.append(_normalize_tool_result(tr, f"tc_{idx}_{tr_idx}"))
                except Exception:
                    continue

        corr_id = msg.get("corr_id") or msg.get("parent_id")
        tags = list(msg.get("tags", [])) if isinstance(msg.get("tags"), list) else []
        time = msg.get("time")

        ir_messages.append(
            IRMessage(
                id=msg_id,
                role=role_l,
                parts=parts,
                tool_calls=tool_calls,
                tool_results=tool_results,
                corr_id=corr_id,
                tags=tags,
                time=time,
            )
        )

    return ir_messages


