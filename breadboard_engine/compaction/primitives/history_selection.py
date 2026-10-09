"""Whole-history replay and size-driven tool-output selection."""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..methods import CompactionContext
from ..params import Params, PresetError
from .accounting import _utf16_len
from .selectors import Selection


def edited_history(context: CompactionContext) -> list[Mapping[str, Any]]:
    messages = list(context.messages)
    for record in context.state.records:
        for edit in record.edits:
            messages[edit.index] = edit.message
    return messages


def tool_name(messages: list[Mapping[str, Any]], index: int) -> str:
    message = messages[index]
    if message.get("name") or message.get("tool"):
        return str(message.get("name") or message.get("tool"))
    call_id = message.get("tool_call_id")
    for previous in reversed(messages[:index]):
        for call in previous.get("tool_calls") or ():
            if call.get("id") == call_id:
                return str((call.get("function") or call).get("name") or "")
    return ""


def output_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or () if p.get("type") == "text")


class ProtectedToolOutputs:
    """Reverse scan, protecting user turns and a token budget before masking."""

    kind = "protected_tool_outputs"

    def __init__(self, params: Params) -> None:
        self.enabled = params.bool("enabled", True)
        self.protect_tokens = params.int("protect_tokens", minimum=0)
        self.protect_turns = params.int("protect_user_turns", minimum=0)
        self.min_savings = params.int("min_savings", minimum=0)
        self.exempt = params.list("exempt_tools", [])
        self.stop_at_summary = params.bool("stop_at_summary", True)
        self.placeholder = params.str("placeholder")
        params.done()

    def select(self, context: CompactionContext) -> Selection:
        if not self.enabled:
            return Selection(details={"noop": "tool-output pruning disabled"})
        messages = edited_history(context)
        owner_summary = False
        summary_tools = set()
        for index, message in enumerate(messages):
            if message.get("role") == "assistant":
                owner_summary = bool(message.get("summary"))
            elif message.get("role") == "tool" and owner_summary:
                summary_tools.add(index)
        turns = total = savings = 0
        targets = []
        for i in range(len(messages) - 1, context.state.kept_start(messages) - 1, -1):
            message = messages[i]
            if message.get("role") == "user":
                turns += 1
            if turns < self.protect_turns:
                continue
            if self.stop_at_summary and message.get("role") == "assistant" and message.get("summary"):
                break
            if self.stop_at_summary and i in summary_tools:
                break
            if message.get("role") != "tool" or message.get("status", "completed") != "completed":
                continue
            if tool_name(messages, i) in self.exempt:
                continue
            text = output_text(message)
            if message.get("compacted") or text == self.placeholder:
                break
            estimate = math.floor(_utf16_len(text) / 4 + 0.5)
            total += estimate
            if total > self.protect_tokens:
                savings += estimate
                targets.append(i)
        return Selection(targets=tuple(targets) if savings > self.min_savings else (),
                         details={"noop": "tool-output savings below minimum"})


class LargestFirstMasking:
    """Select largest outputs until their full lengths meet a provider reduction target."""

    kind = "largest_first_masking"

    def __init__(self, params: Params) -> None:
        self.target_ratio = params.number("target_ratio", 0.5)
        self.chars_per_token = params.int("chars_per_token", 4, minimum=1)
        self.placeholder = params.str("placeholder")
        if not 0 < self.target_ratio <= 1:
            raise PresetError(f"{params.where}.target_ratio must be in (0, 1]")
        params.done()

    def select(self, context: CompactionContext) -> Selection:
        current, limit = context.overflow_tokens, context.overflow_limit
        if current is None or limit is None or current <= limit or limit <= 0:
            return Selection(details={"noop": "provider overflow bounds unavailable"})
        target = math.floor(limit * self.target_ratio)
        required = (current - target) * self.chars_per_token
        messages = edited_history(context)
        candidates = []
        for i in range(context.state.kept_start(messages), len(messages)):
            message = messages[i]
            text = output_text(message)
            if message.get("role") == "tool" and text and not message.get("compacted") and text != self.placeholder:
                candidates.append((i, _utf16_len(text)))
        candidates.sort(key=lambda pair: -pair[1])
        removed = 0
        targets = []
        for index, size in candidates:
            targets.append(index)
            removed += size
            if removed >= required:
                break
        return Selection(targets=tuple(targets), details={"recovered": removed >= required})
