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
from typing import Any, Dict, Mapping, Optional, Sequence

from ..provider.anthropic_replay import is_tool_result_replay, replay_messages
from ..provider.contract_wire import ProviderContractError

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
IMAGE_TOKEN_ESTIMATE = 1200


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def is_image_part(part: Any) -> bool:
    """Provider image blocks and the canonical ``media`` image kinds."""
    if not isinstance(part, Mapping):
        return False
    kind = part.get("type")
    return kind in {"image_url", "image", "input_image"} or (kind == "media" and part.get("kind") in {"image", "codex_image_url"})


def _part_tokens(part: Any) -> int:
    if isinstance(part, str):
        return estimate_text_tokens(part)
    if not isinstance(part, Mapping):
        return estimate_text_tokens(str(part))
    if is_image_part(part):
        return IMAGE_TOKEN_ESTIMATE

    kind = part.get("type")
    metadata: Optional[Dict[str, Any]] = None
    if kind == "tool_result":
        metadata = {key: value for key, value in part.items() if key != "content"}
    elif kind == "provider_replay" and part.get("schema_version") == "openai.responses.tool_output.v1":
        payload = part.get("payload")
        if isinstance(payload, Mapping):
            metadata = dict(part)
            metadata["payload"] = {key: value for key, value in payload.items() if key != "output"}
    if metadata is not None:
        from .transcript import result_parts

        parts = result_parts([part])
        metadata_tokens = estimate_text_tokens(json.dumps(metadata, sort_keys=True, default=str))
        return metadata_tokens + sum(_part_tokens(result_part) for result_part in parts)

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
        native_messages = None
        if content and all(isinstance(part, dict) for part in content):
            replay_blocks = [part for part in content if is_tool_result_replay(part)]
            if len(replay_blocks) == 1 and isinstance(replay_blocks[0].get("payload"), Mapping):
                try:
                    native_messages = replay_messages(content)
                except (ProviderContractError, KeyError):
                    pass
        if native_messages is None:
            total += sum(_part_tokens(part) for part in content)
        else:
            replay = next(part for part in content if is_tool_result_replay(part))
            metadata = dict(replay)
            payload = replay["payload"]
            metadata["payload"] = {key: value for key, value in payload.items() if key != "messages"}
            total += estimate_text_tokens(json.dumps(metadata, sort_keys=True, default=str))
            for native_message in native_messages:
                message_metadata = {key: value for key, value in native_message.items() if key != "content"}
                total += estimate_text_tokens(json.dumps(message_metadata, sort_keys=True, default=str))
                total += sum(_part_tokens(block) for block in native_message["content"])
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
