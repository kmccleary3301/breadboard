"""Lossless normalization from runtime results to provider boundary events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .contracts import (
    ProviderContractError,
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
    normalize_provider_replay,
    normalize_content,
    normalize_usage,
    parse_canonical_json,
)


@dataclass(frozen=True)
class NormalizedEvent:
    """Legacy-friendly event wrapper with a strict provider event payload."""

    type: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "payload": self.payload}


def _tool_call_to_payload(
    tool_call: ProviderToolCall, message_index: int, call_index: int
) -> Dict[str, Any]:
    if not isinstance(tool_call, ProviderToolCall):
        raise ProviderContractError("tool call is not a ProviderToolCall")
    if tool_call.id is None or tool_call.name is None:
        raise ProviderContractError("completed tool call requires stable id and name")
    if tool_call.arguments_json is None:
        raise ProviderContractError("completed tool call requires arguments_json")
    parsed = parse_canonical_json(tool_call.arguments_json)
    if tool_call.parsed_arguments != parsed:
        raise ProviderContractError("completed tool call arguments are not canonical")
    return {
        "message_index": message_index,
        "call_index": call_index,
        "id": tool_call.id,
        "call_id": tool_call.id,
        "name": tool_call.name,
        "arguments_json": tool_call.arguments_json,
        "arguments": parsed,
        "tool_type": tool_call.type,
    }


def _message_content_events(
    message: ProviderMessage, message_index: int
) -> List[NormalizedEvent]:
    encoded_message = message.as_dict()
    message_id = encoded_message.get("message_id") or f"message_{message_index}"
    events: List[NormalizedEvent] = []
    for content_index, block in enumerate(encoded_message["content"]):
        block_type = block["type"]
        if block_type == "text":
            events.append(
                NormalizedEvent(
                    "text",
                    {
                        "message_index": message_index,
                        "role": message.role,
                        "content": block["text"],
                    },
                )
            )
            events.append(
                NormalizedEvent(
                    "text_start",
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        "message_id": message_id,
                    },
                )
            )
            if block["text"]:
                events.append(
                    NormalizedEvent(
                        "text_delta",
                        {
                            "message_index": message_index,
                            "content_index": content_index,
                            "message_id": message_id,
                            "delta": block["text"],
                        },
                    )
                )
            events.append(
                NormalizedEvent(
                    "text_end",
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        "message_id": message_id,
                    },
                )
            )
        elif block_type == "thinking":
            events.append(
                NormalizedEvent(
                    "thinking_start",
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        "message_id": message_id,
                    },
                )
            )
            if block["text"]:
                events.append(
                    NormalizedEvent(
                        "thinking_delta",
                        {
                            "message_index": message_index,
                            "content_index": content_index,
                            "message_id": message_id,
                            "delta": block["text"],
                        },
                    )
                )
            events.append(
                NormalizedEvent(
                    "thinking_end",
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        "message_id": message_id,
                    },
                )
            )
        elif block_type == "tool_call":
            call = ProviderToolCall(
                id=block["call_id"],
                name=block["name"],
                arguments=block["arguments"],
                arguments_json=block["arguments_json"],
            )
            payload = _tool_call_to_payload(call, message_index, content_index)
            events.append(NormalizedEvent("tool_call", payload))
            events.extend(
                [
                    NormalizedEvent(
                        "tool_call_start",
                        {
                            "message_index": message_index,
                            "content_index": content_index,
                            "message_id": message_id,
                            "call_id": call.id,
                            "name": call.name,
                        },
                    ),
                    NormalizedEvent(
                        "tool_call_delta",
                        {
                            "message_index": message_index,
                            "content_index": content_index,
                            "message_id": message_id,
                            "call_id": call.id,
                            "delta": call.arguments_json,
                        },
                    ),
                    NormalizedEvent(
                        "tool_call_end",
                        {
                            "message_index": message_index,
                            "content_index": content_index,
                            "message_id": message_id,
                            **payload,
                        },
                    ),
                ]
            )
        elif block_type in {
            "tool_result",
            "redacted_thinking",
            "provider_replay",
        }:
            events.append(
                NormalizedEvent(
                    block_type,
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        **block,
                    },
                )
            )
        else:
            raise ProviderContractError(
                f"unknown content block type: {block_type!r}"
            )
    return events


def normalize_provider_result(result: ProviderResult) -> List[Dict[str, Any]]:
    """Return a strict ordered normalized event list without silently skipping values.

    The historical ``type`` names remain as the outer compatibility view, while
    each provider content event uses the v2 event vocabulary.  The invoker uses
    the same values to build its owning aggregate.
    """

    if not isinstance(result, ProviderResult):
        raise ProviderContractError("result must be a ProviderResult")
    if not isinstance(result.messages, list):
        raise ProviderContractError("result.messages must be a list")
    if not isinstance(result.metadata, dict):
        raise ProviderContractError("result.metadata must be an object")

    events: List[NormalizedEvent] = [NormalizedEvent("response_start", {})]
    for index, message in enumerate(result.messages):
        if not isinstance(message, ProviderMessage):
            raise ProviderContractError("result.messages contains an invalid message")
        events.extend(_message_content_events(message, index))
        if message.finish_reason is not None:
            events.append(
                NormalizedEvent(
                    "finish_reason",
                    {
                        "message_index": index,
                        "finish_reason": message.finish_reason,
                    },
                )
            )

    usage = normalize_usage(result.usage) if result.usage is not None else None
    events.append(
        NormalizedEvent(
            "finish",
            {
                "usage": usage,
                "metadata": {
                    key: value
                    for key, value in (result.metadata or {}).items()
                    if key != "normalized_events"
                },
                "model": result.model,
            },
        )
    )

    return [event.to_dict() for event in events]


def normalized_result_messages(result: ProviderResult) -> List[Dict[str, Any]]:
    """Encode terminal assistant messages while preserving present empty content."""
    if not isinstance(result, ProviderResult):
        raise ProviderContractError("result must be a ProviderResult")
    messages: List[Dict[str, Any]] = []
    for message in result.messages:
        if not isinstance(message, ProviderMessage):
            raise ProviderContractError("result.messages contains an invalid message")
        if message.role != "assistant":
            raise ProviderContractError(
                "terminal assistant_messages must use assistant role"
            )
        messages.append(message.as_dict())
    if result.reasoning_blocks:
        blocks = normalize_content(result.reasoning_blocks, role="assistant")
        if blocks:
            messages.append({"role": "assistant", "content": blocks})
    return messages


def normalized_result_replay(
    result: ProviderResult, *, provider_id: str
) -> List[Dict[str, Any]]:
    """Bound and validate provider-native replay envelopes before persistence."""
    replay_items: List[Any] = []
    if result.provider_replay:
        replay_items.extend(result.provider_replay)
    if result.encrypted_reasoning:
        for item in result.encrypted_reasoning:
            if not isinstance(item, dict):
                raise ProviderContractError(
                    "encrypted reasoning replay must be an object"
                )
            unknown = set(item) - {
                "encrypted_content",
                "signature",
                "redacted_data",
                "item_id",
                "reasoning_id",
            }
            if unknown:
                raise ProviderContractError(
                    f"encrypted reasoning contains unknown fields: {sorted(unknown)!r}"
                )
            replay_items.append(
                {
                    "provider_id": provider_id,
                    "schema_version": "bb.provider_replay.v1",
                    "replay_scope": "same_provider",
                    "payload": {
                        key: item[key]
                        for key in (
                            "encrypted_content",
                            "signature",
                            "redacted_data",
                            "item_id",
                            "reasoning_id",
                        )
                        if key in item
                    },
                }
            )
    normalized: List[Dict[str, Any]] = []
    for item in replay_items:
        if not isinstance(item, dict):
            raise ProviderContractError("provider replay must be an object")
        replay = normalize_provider_replay(item)
        if replay["provider_id"] != provider_id:
            raise ProviderContractError(
                "provider replay provider_id does not match runtime provider"
            )
        normalized.append(replay)
    return normalized


__all__ = [
    "NormalizedEvent",
    "normalize_provider_result",
    "normalized_result_messages",
    "normalized_result_replay",
]
