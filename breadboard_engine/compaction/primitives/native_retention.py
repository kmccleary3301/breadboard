"""Text-only retained-message budgets, with media preserved independently."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..params import Params
from .byte_estimator import text_tokens


def truncate_middle(text: str, budget: int) -> str:
    raw = text.encode("utf-8")
    keep = budget * 4
    if len(raw) <= keep:
        return text
    left = keep // 2
    right = keep - left
    prefix = raw[:left].decode("utf-8", errors="ignore")
    suffix = raw[len(raw) - right:].decode("utf-8", errors="ignore") if right else ""
    return f"{prefix}…{(len(raw) - keep + 3) // 4} tokens truncated…{suffix}"


class MessageTextBudget:
    kind = "message_text_budget"

    def __init__(self, params: Params) -> None:
        self.budget = params.int("budget", minimum=0)
        self.roles = tuple(params.list("roles"))
        self.exclude_contextual = params.bool("exclude_contextual", False)
        params.done()

    def retain(self, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        from ..remote.openai import is_contextual_user_message
        remaining = self.budget
        kept = []
        for item in reversed(items):
            if remaining == 0 or item.get("type") != "message" or item.get("role") not in self.roles:
                continue
            if self.exclude_contextual and item.get("role") == "user" and is_contextual_user_message(item):
                continue
            content = item.get("content") or []
            cost = max(1, sum(text_tokens(p.get("text") or "") for p in content if p.get("type") in {"input_text", "output_text"}))
            if cost <= remaining:
                kept.append(dict(item))
                remaining -= cost
                continue
            parts = []
            for part in content:
                kind = part.get("type")
                if kind == "input_image":
                    parts.append(dict(part))
                elif kind in {"input_text", "output_text"} and remaining:
                    text = part.get("text") or ""
                    value = truncate_middle(text, remaining)
                    remaining = max(0, remaining - text_tokens(text))
                    if value:
                        parts.append({**part, "text": value})
            if parts:
                kept.append({**item, "content": parts})
            remaining = 0
        kept.reverse()
        return kept


def build_native_retention(params: Params) -> MessageTextBudget:
    params.kind((MessageTextBudget.kind,))
    return MessageTextBudget(params)
