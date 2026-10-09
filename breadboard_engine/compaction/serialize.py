"""Message serialization for LLM summarization inputs.

Ports OMP ``utils.ts`` (lines 205-350): converts chat-format transcript messages
to plain text for summary prompts, truncates oversized tool outputs, and escapes
harness boundary tags.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional, Sequence

TOOL_RESULT_MAX_CHARS = 2000

_SUMMARY_BOUNDARY_TAG_RE = re.compile(
    r"<\s*/?\s*(?:conversation|previous-summary)\s*>",
    re.IGNORECASE,
)


def truncate_tool_result_for_summary(text: str) -> str:
    """Truncate tool results to the representation used in summarization prompts."""
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    truncated_chars = len(text) - TOOL_RESULT_MAX_CHARS
    return f"{text[:TOOL_RESULT_MAX_CHARS]}\n\n[... {truncated_chars} more characters truncated]"


truncateToolResultForSummary = truncate_tool_result_for_summary


def escape_summary_boundary_tags(text: str) -> str:
    """Keep untrusted summary input from closing or impersonating harness boundary tags."""
    return _SUMMARY_BOUNDARY_TAG_RE.sub(lambda m: f"&lt;{m.group(0)[1:]}", text)


escapeSummaryBoundaryTags = escape_summary_boundary_tags


def _render_tool_calls(calls: Sequence[Mapping[str, Any]]) -> str:
    rendered: list[str] = []
    for call in calls:
        function = call.get("function") if isinstance(call.get("function"), Mapping) else call
        name = str(function.get("name") or "")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                pass
        if isinstance(arguments, Mapping):
            args_parts: list[str] = []
            for k, v in arguments.items():
                if v is None:
                    args_parts.append(f"{k}=null")
                elif isinstance(v, (str, int, float, bool)):
                    args_parts.append(f"{k}={json.dumps(v, ensure_ascii=False)}")
                else:
                    args_parts.append(f"{k}={json.dumps(v, separators=(',', ':'), ensure_ascii=False)}")
            args_str = ", ".join(args_parts)
        elif arguments is not None:
            args_str = str(arguments)
        else:
            args_str = ""
        rendered.append(f"{name}({args_str})")
    return "; ".join(rendered)


def serialize_conversation(messages: Sequence[Mapping[str, Any]]) -> str:
    """Serialize chat-format messages to transcript text."""
    useless_call_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role in ("tool", "toolResult", "function"):
            is_error = bool(msg.get("is_error") or msg.get("isError"))
            if msg.get("useless") is True and not is_error:
                call_id = msg.get("tool_call_id") or msg.get("toolCallId")
                if isinstance(call_id, str):
                    useless_call_ids.add(call_id)

    parts: list[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text_parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, Mapping) and p.get("type") == "text" and isinstance(p.get("text"), str)
                ]
                text = "".join(text_parts)
            else:
                text = ""
            if text:
                parts.append(f"[User]: {text}")

        elif role == "assistant":
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            valid_tool_calls: list[Mapping[str, Any]] = []

            # Check reasoning fields
            if isinstance(msg.get("reasoning"), str) and msg["reasoning"]:
                thinking_parts.append(msg["reasoning"])
            if isinstance(msg.get("reasoning_content"), str) and msg["reasoning_content"]:
                thinking_parts.append(msg["reasoning_content"])
            annotations = msg.get("annotations")
            if isinstance(annotations, Mapping):
                if isinstance(annotations.get("reasoning"), str):
                    thinking_parts.append(annotations["reasoning"])
                if isinstance(annotations.get("reasoning_content"), str):
                    thinking_parts.append(annotations["reasoning_content"])

            content = msg.get("content")
            if isinstance(content, str):
                if content:
                    text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, Mapping):
                        continue
                    btype = block.get("type")
                    if btype == "text" and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
                    elif btype in ("thinking", "thought"):
                        th = block.get("thinking") or block.get("thought") or block.get("text")
                        if isinstance(th, str) and th:
                            thinking_parts.append(th)
                    elif btype in ("toolCall", "tool_call"):
                        cid = block.get("id") or block.get("tool_call_id")
                        if isinstance(cid, str) and cid in useless_call_ids:
                            continue
                        valid_tool_calls.append(block)

            for call in msg.get("tool_calls") or ():
                if isinstance(call, Mapping):
                    cid = call.get("id")
                    if isinstance(cid, str) and cid in useless_call_ids:
                        continue
                    valid_tool_calls.append(call)

            if thinking_parts:
                parts.append(f"[Think]: {'\n'.join(thinking_parts)}")
            if text_parts:
                parts.append(f"[Assistant]: {'\n'.join(text_parts)}")
            if valid_tool_calls:
                parts.append(f"[Tool Call]: {_render_tool_calls(valid_tool_calls)}")

        elif role in ("tool", "toolResult", "function"):
            call_id = msg.get("tool_call_id") or msg.get("toolCallId")
            if isinstance(call_id, str) and call_id in useless_call_ids:
                continue
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text_parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, Mapping) and p.get("type") == "text" and isinstance(p.get("text"), str)
                ]
                text = "".join(text_parts)
            else:
                text = ""
            if text:
                parts.append(f"[Tool Result]: {truncate_tool_result_for_summary(text)}")

    return "\n\n".join(parts)


serializeConversation = serialize_conversation


def serialize_conversation_for_summary(messages: Sequence[Mapping[str, Any]]) -> str:
    """Serialize chat-format messages for summary input with boundary tags escaped."""
    conversation = serialize_conversation(messages)
    return escape_summary_boundary_tags(conversation)


serializeConversationForSummary = serialize_conversation_for_summary
