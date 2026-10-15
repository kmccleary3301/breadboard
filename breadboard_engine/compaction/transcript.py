"""Chat-format transcript helpers shared by compaction methods.

BreadBoard's model-facing history (``SessionState.provider_messages``) is a
list of OpenAI chat-style dicts: ``system``/``user``/``assistant``/``tool``
roles, assistant ``tool_calls``, and ``tool`` results keyed by
``tool_call_id``. Cut-point rules port OMP ``findCutPoint``: cut only at user
or assistant messages so a tool result never loses its call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..provider.anthropic_replay import (
    is_tool_result_replay,
    native_tool_results,
    replace_tool_result,
    replay_messages,
)
from ..provider.contract_wire import ProviderContractError, canonical_json
from .tokens import estimate_message_tokens, is_image_part

Message = Mapping[str, Any]


def role_of(message: Message) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else ""


def is_tool_result(message: Message) -> bool:
    """A chat-format result whose content compaction may rewrite."""
    return role_of(message) in {"tool", "function"}


def answers_tool_call(message: Message) -> bool:
    """Any result tied to a call, including canonical ``tool_result`` carriers (typed image output)."""
    return is_tool_result(message) or role_of(message) == "tool_result"


def result_call_ids(message: Message) -> Tuple[str, ...]:
    """``tool_call_id``, or the calls a carrier's canonical ``tool_result`` blocks or replay payload answer."""
    call_id = message.get("tool_call_id")
    if isinstance(call_id, str):
        return (call_id,)
    content = message.get("content")
    if not answers_tool_call(message) or not isinstance(content, list):
        return ()
    blocks = [block.get("payload") if block.get("type") == "provider_replay" else block
              for block in content if isinstance(block, Mapping)]
    return tuple(block["call_id"] for block in blocks if isinstance(block, Mapping) and isinstance(block.get("call_id"), str))


def result_parts(content: Any) -> List[Any]:
    """A result's content as chat parts, with canonical ``tool_result`` blocks and typed Responses output inlined.

    Other replay payloads (a Claude continuation's) repeat their ``tool_result`` blocks, so they add nothing.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts: List[Any] = []
    for block in content if isinstance(content, list) else ():
        kind = block.get("type") if isinstance(block, Mapping) else None
        if kind == "tool_result":
            parts.extend(result_parts(block.get("content")))
        elif kind == "provider_replay":
            payload = block.get("payload")
            for item in result_parts(payload.get("output") if isinstance(payload, Mapping) else None):
                if isinstance(item, Mapping) and item.get("type") in {"input_text", "output_text"}:
                    item = {"type": "text", "text": item.get("text")}
                parts.append(item)
        else:
            parts.append(block)
    return parts


def result_text(content: Any) -> str:
    return "".join(part["text"] for part in result_parts(content)
                   if isinstance(part, Mapping) and part.get("type") == "text" and isinstance(part.get("text"), str))


def media_placeholder(part: Any, *, image_label: Optional[str] = "[image]") -> Optional[str]:
    """Render canonical image and document parts without exposing their payloads."""
    if not isinstance(part, Mapping):
        return None
    kind = part.get("type")
    is_image = is_image_part(part)
    if image_label is not None:
        if is_image:
            return image_label
        if kind != "document":
            return None
        source = part.get("source")
        source = source if isinstance(source, Mapping) else {}
        mime = part.get("mediaType") or part.get("mime") or source.get("media_type")
        if not isinstance(mime, str) or not mime:
            mime = "application/pdf"
        return f"[Attached {mime}: {part.get('filename') or 'file'}]"

    mime = part.get("mediaType") or part.get("mime")
    if not isinstance(mime, str) or not mime:
        mime = None
        source = part.get("source")
        if isinstance(source, Mapping):
            source_mime = source.get("media_type") or source.get("mime")
            if isinstance(source_mime, str) and source_mime:
                mime = source_mime
    if not mime and kind == "document":
        mime = "application/pdf"
    elif not mime and is_image:
        image = part.get("image_url") or {}
        url = image.get("url", "") if isinstance(image, Mapping) else (image if isinstance(image, str) else "")
        target = url or part.get("uri") or part.get("url") or ""
        match = re.match(r"data:([^;,]+)", target) if isinstance(target, str) else None
        mime = match.group(1) if match else "image/png"
    if isinstance(mime, str) and (mime.startswith("image/") or mime == "application/pdf"):
        return f"[Attached {mime}: {part.get('filename') or 'file'}]"
    return None


def _anthropic_replay_part(part: Any) -> Dict[str, Any]:
    if isinstance(part, dict):
        kind = part.get("type")
        if kind in {"image", "document"}:
            return dict(part)
        if kind == "text" and isinstance(part.get("text"), str):
            if set(part) == {"type", "text"}:
                return dict(part)
    return {"type": "text", "text": canonical_json(part)}


def anthropic_replay_conversation(message: Message) -> Optional[List[Dict[str, Any]]]:
    """Return a validated tool-result replay as ordered user observations and results."""
    if role_of(message) not in {"tool", "tool_result"}:
        return None
    content = message.get("content")
    if not isinstance(content, (list, tuple)):
        return None

    content = list(content)
    if not any(isinstance(block, dict) and is_tool_result_replay(block) for block in content):
        return None

    try:
        native_messages = replay_messages(content)
    except (ProviderContractError, KeyError, AttributeError):
        return None

    native_results = [
        block
        for native_message in native_messages
        for block in native_message["content"]
        if block["type"] == "tool_result"
    ]
    canonical_results = [
        block for block in content
        if isinstance(block, Mapping) and block.get("type") == "tool_result"
    ]
    call_id = message.get("tool_call_id")
    if isinstance(call_id, str) and (
        len(native_results) != 1 or native_results[0].get("tool_use_id") != call_id
    ):
        return None

    # The validator binds each canonical result to its native peer by ordinal; IDs may repeat.
    if len(native_results) != len(canonical_results):
        return None

    result_contents: List[Any] = []
    has_result_parts = False
    for native_result, canonical_result in zip(native_results, canonical_results):
        native_call_id = native_result.get("tool_use_id")
        canonical_call_id = canonical_result.get("call_id")
        if canonical_call_id != native_call_id:
            return None
        result_content = native_result.get("content")
        if isinstance(result_content, str):
            normalized_content: Any = result_content
            has_result_parts = True
        elif isinstance(result_content, list):
            normalized_content = [_anthropic_replay_part(part) for part in result_content]
            has_result_parts |= bool(normalized_content)
        else:
            normalized_content = canonical_json(result_content)
            has_result_parts = True
        result_contents.append(normalized_content)

    has_observations = any(
        block["type"] != "tool_result"
        for native_message in native_messages for block in native_message["content"]
    )
    # Empty result-only replays retain canonical fallback, but never suppress side observations.
    if not has_result_parts and not has_observations:
        return None

    conversation: List[Dict[str, Any]] = []
    result_index = 0
    for native_message in native_messages:
        observations: List[Dict[str, Any]] = []
        for block in native_message["content"]:
            if block["type"] != "tool_result":
                observations.append(_anthropic_replay_part(block))
                continue
            if observations:
                conversation.append({"role": "user", "content": observations})
                observations = []
            tool_message = dict(message)
            tool_message["content"] = [
                {**canonical_results[result_index], "content": result_contents[result_index]}
            ]
            result_index += 1
            conversation.append(tool_message)
        if observations:
            conversation.append({"role": "user", "content": observations})
    return conversation


def project_replay_messages(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], set[str]]:
    """Replace validated Anthropic replays with their semantic transcript messages.

    Returns the projected messages and the call IDs whose results came from a
    replay carrying native side observations. Plain, invalid, and foreign
    results pass through unchanged.
    """
    projected_messages: list[Mapping[str, Any]] = []
    native_replay_call_ids: set[str] = set()
    for message in messages:
        replay = anthropic_replay_conversation(message)
        if replay is None:
            projected_messages.append(message)
        elif any(item.get("role") == "user" for item in replay):
            projected_messages.extend(replay)
            native_replay_call_ids.update(
                call_id
                for replay_message in replay
                for call_id in result_call_ids(replay_message)
                if isinstance(call_id, str)
            )
        else:
            flattened_message = dict(message)
            flattened_message["content"] = [
                part for replay_message in replay for part in replay_message.get("content", [])
            ]
            projected_messages.append(flattened_message)
    return projected_messages, native_replay_call_ids


def rewrite_result_content(
    message: Dict[str, Any],
    replacement: str,
    selected_call_ids: Optional[Iterable[str]] = None,
    *,
    selected_blocks: Optional[Iterable[int]] = None,
) -> bool:
    """Rewrite selected canonical tool results while preserving replay envelopes.

    ``selected_blocks`` narrows the rewrite to those ``content`` positions, since call IDs may repeat.
    """
    content = message.get("content")
    if role_of(message) not in {"tool", "tool_result"} or not isinstance(content, list):
        return False
    selected = None if selected_call_ids is None else set(selected_call_ids)
    positions = None if selected_blocks is None else set(selected_blocks)

    def is_selected(call_id: Any, position: int) -> bool:
        return (isinstance(call_id, str) and (selected is None or call_id in selected)
                and (positions is None or position in positions))

    def without_attachments(block: Mapping[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in block.items() if key != "attachments"}

    if any(isinstance(block, dict) and is_tool_result_replay(block) for block in content):
        try:
            native_results = native_tool_results(content)
        except (ProviderContractError, KeyError, AttributeError):
            native_results = None
        if native_results is not None:
            result_positions = [position for position, block in enumerate(content) if block.get("type") == "tool_result"]
            chosen = [(ordinal, position)
                      for ordinal, (native, position) in enumerate(zip(native_results, result_positions))
                      if is_selected(native.get("tool_use_id"), position)]
            if not chosen:
                return False
            stripped = {position for _, position in chosen}
            replayed = [without_attachments(block) if position in stripped else block
                        for position, block in enumerate(content)]
            for ordinal, _ in chosen:
                replayed = replace_tool_result(replayed, ordinal, replacement)
            message["content"] = replayed
            return True

    rewritten: List[Any] = []
    changed = False
    for position, block in enumerate(content):
        if not isinstance(block, Mapping):
            rewritten.append(block)
            continue
        kind = block.get("type")
        if kind == "tool_result" and is_selected(block.get("call_id"), position):
            rewritten.append({**without_attachments(block), "content": replacement})
            changed = True
            continue
        payload = block.get("payload") if kind == "provider_replay" else None
        if (isinstance(payload, Mapping) and block.get("schema_version") == "openai.responses.tool_output.v1"
                and is_selected(payload.get("call_id"), position) and "output" in payload):
            output = [{"type": "input_text", "text": replacement}] if isinstance(payload["output"], list) else replacement
            rewritten.append({**block, "payload": {**payload, "output": output}})
            changed = True
            continue
        rewritten.append(block)
    if changed:
        message["content"] = rewritten
    return changed


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


def find_cut_point_crossing(
    messages: Sequence[Message],
    start: int,
    end: int,
    keep_recent_tokens: int,
    *,
    count_tokens: Callable[[Message], int] = estimate_message_tokens,
) -> CutPoint:
    """Pi 0.73.1 ``findCutPoint`` (``core/compaction/compaction.js:304-354``).

    Walks back per message; at the first message where the running total is
    ``>= keep_recent_tokens`` it cuts at the nearest valid cut point at or
    after that message. If the budget is never reached, or no cut point
    follows the crossing message, everything from the first cut point is kept.
    """
    cut_points = [i for i in range(start, end) if is_valid_cut_point(messages[i])]
    if not cut_points:
        return CutPoint(start, -1, False)
    accumulated = 0
    cut_index = cut_points[0]
    for i in range(end - 1, start - 1, -1):
        accumulated += count_tokens(messages[i])
        if accumulated >= keep_recent_tokens:
            for candidate in cut_points:
                if candidate >= i:
                    cut_index = candidate
                    break
            break
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
        elif answers_tool_call(message):
            for call_id in result_call_ids(message) or (None,):
                if call_id not in open_calls:
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
    return isinstance(content, list) and any(is_image_part(part) for part in content)
