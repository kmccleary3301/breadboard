"""Token accounting for compaction decisions.

Provider usage is the ground truth for what the last request cost; messages
appended after that request are estimated locally. As in OMP
``compactionContextTokens``, the decision uses the larger of the provider
figure and the local estimate of the stored conversation, so on-wire
reductions cannot hide unbounded history growth.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Optional, Sequence

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
IMAGE_TOKEN_ESTIMATE = 1200


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _part_tokens(part: Any) -> int:
    if isinstance(part, str):
        return estimate_text_tokens(part)
    if not isinstance(part, Mapping):
        return estimate_text_tokens(str(part))
    kind = part.get("type")
    if kind in {"image_url", "image", "input_image"}:
        return IMAGE_TOKEN_ESTIMATE
    for key in ("text", "content", "thinking"):
        value = part.get(key)
        if isinstance(value, str):
            return estimate_text_tokens(value)
    return estimate_text_tokens(json.dumps(part, sort_keys=True, default=str))


def estimate_message_tokens(message: Mapping[str, Any]) -> int:
    """Estimate one chat-format message (role, content, tool_calls)."""
    total = MESSAGE_OVERHEAD_TOKENS
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_text_tokens(content)
    elif isinstance(content, list):
        total += sum(_part_tokens(part) for part in content)
    for call in message.get("tool_calls") or ():
        if not isinstance(call, Mapping):
            continue
        function = call.get("function") if isinstance(call.get("function"), Mapping) else call
        total += estimate_text_tokens(str(function.get("name") or ""))
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            total += estimate_text_tokens(arguments)
        elif arguments is not None:
            total += estimate_text_tokens(json.dumps(arguments, sort_keys=True, default=str))
    native = message.get("bb_native_compaction")
    if isinstance(native, Mapping):
        total += int(native.get("token_estimate") or 0)
    return total


def estimate_messages_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages if isinstance(message, Mapping))


def _number(usage: Mapping[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = usage.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def context_tokens_from_usage(usage: Any) -> Optional[int]:
    """Context occupancy (prompt plus completion) reported by a provider.

    Anthropic reports ``input_tokens`` net of cache reads/writes; OpenAI Chat
    ``prompt_tokens`` and Responses ``input_tokens`` include cached tokens.
    Returns ``None`` when the usage carries no prompt figure.
    """
    if usage is None:
        return None
    if not isinstance(usage, Mapping):
        usage = {
            name: getattr(usage, name)
            for name in (
                "prompt_tokens",
                "input_tokens",
                "completion_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
            if getattr(usage, name, None) is not None
        }
    prompt = _number(usage, "prompt_tokens", "input_tokens")
    if prompt is None:
        return None
    completion = _number(usage, "completion_tokens", "output_tokens") or 0
    if "prompt_tokens" not in usage and (
        "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage
    ):
        prompt += _number(usage, "cache_read_input_tokens") or 0
        prompt += _number(usage, "cache_creation_input_tokens") or 0
    return max(0, prompt + completion)


def compaction_context_tokens(provider_tokens: Optional[int], stored_estimate: int) -> int:
    return max(max(0, provider_tokens or 0), max(0, stored_estimate))
