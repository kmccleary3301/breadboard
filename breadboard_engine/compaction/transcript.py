"""Chat-format transcript helpers shared by compaction methods.

BreadBoard's model-facing history (``SessionState.provider_messages``) is a
list of OpenAI chat-style dicts: ``system``/``user``/``assistant``/``tool``
roles, assistant ``tool_calls``, and ``tool`` results keyed by
``tool_call_id``. Cut-point rules port OMP ``findCutPoint``: cut only at user
or assistant messages so a tool result never loses its call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Sequence

from .tokens import estimate_message_tokens

Message = Mapping[str, Any]


def role_of(message: Message) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else ""


def is_tool_result(message: Message) -> bool:
    return role_of(message) in {"tool", "function"}


def is_turn_start(message: Message) -> bool:
    return role_of(message) == "user"


def is_valid_cut_point(message: Message) -> bool:
    return role_of(message) in {"user", "assistant"}


def tool_call_ids(message: Message) -> List[str]:
    ids: List[str] = []
    for call in message.get("tool_calls") or ():
        if isinstance(call, Mapping) and isinstance(call.get("id"), str):
            ids.append(call["id"])
    return ids


def leading_system_count(messages: Sequence[Message]) -> int:
    """Number of leading ``system`` messages, which compaction never removes."""
    count = 0
    for message in messages:
        if role_of(message) != "system":
            break
        count += 1
    return count


def find_turn_start_index(messages: Sequence[Message], index: int, start: int) -> int:
    for i in range(index, start - 1, -1):
        if is_turn_start(messages[i]):
            return i
    return -1


@dataclass(frozen=True)
class CutPoint:
    first_kept_index: int
    """Index of the first message kept verbatim."""
    turn_start_index: int
    """User message that starts the split turn, or ``-1``."""
    is_split_turn: bool


def find_cut_point(
    messages: Sequence[Message],
    start: int,
    end: int,
    keep_recent_tokens: int,
    *,
    count_tokens: Callable[[Message], int] = estimate_message_tokens,
) -> CutPoint:
    """Oldest complete recent suffix of ``messages[start:end]`` within budget.

    The newest group is kept even when it alone exceeds the budget. An older
    group that would push a fitting suffix over the budget is never kept.
    """
    cut_points = [i for i in range(start, end) if is_valid_cut_point(messages[i])]
    if not cut_points:
        return CutPoint(start, -1, False)
    accumulated = 0
    cursor = len(cut_points) - 1
    cut_index = cut_points[cursor]
    for i in range(end - 1, start - 1, -1):
        accumulated += count_tokens(messages[i])
        if cursor < 0 or i != cut_points[cursor]:
            continue
        if accumulated > keep_recent_tokens:
            break
        cut_index = i
        cursor -= 1
    turn_start = -1 if is_turn_start(messages[cut_index]) else find_turn_start_index(messages, cut_index, start)
    return CutPoint(cut_index, turn_start, not is_turn_start(messages[cut_index]) and turn_start != -1)


def check_tool_pairing(messages: Sequence[Message]) -> Optional[str]:
    """Return a description of the first orphaned tool result, else ``None``.

    Every ``tool`` message must answer a call issued by an earlier assistant
    message in the same sequence. Projections run this before sending.
    """
    open_calls: set[str] = set()
    for index, message in enumerate(messages):
        if role_of(message) == "assistant":
            open_calls.update(tool_call_ids(message))
        elif is_tool_result(message):
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in open_calls:
                return f"message {index} answers unknown tool call {call_id!r}"
    return None


def content_text(message: Message) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def has_images(message: Message) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(part, Mapping) and part.get("type") in {"image_url", "image", "input_image"}
        for part in content
    )
