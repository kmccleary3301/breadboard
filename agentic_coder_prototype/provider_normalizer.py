from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List

from .provider_runtime import ProviderResult, ProviderMessage, ProviderToolCall


@dataclass
class NormalizedEvent:
    type: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "payload": self.payload}


def _tool_call_to_payload(tool_call: ProviderToolCall, message_index: int, call_index: int) -> Dict[str, Any]:
    return {
        "message_index": message_index,
        "call_index": call_index,
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": tool_call.arguments,
        "tool_type": tool_call.type,
    }


def normalize_provider_result(result: ProviderResult) -> List[Dict[str, Any]]:
    """Convert ProviderResult into a canonical list of events."""

    if result is None:
        return []

    events: List[NormalizedEvent] = []
    for idx, message in enumerate(result.messages or []):
        if not isinstance(message, ProviderMessage):
            continue
        if message.content:
            events.append(
                NormalizedEvent(
                    type="text",
                    payload={
                        "message_index": idx,
                        "role": message.role,
                        "content": message.content,
                    },
                )
            )
        if message.tool_calls:
            for call_idx, tool_call in enumerate(message.tool_calls):
                events.append(
                    NormalizedEvent(
                        type="tool_call",
                        payload=_tool_call_to_payload(tool_call, idx, call_idx),
                    )
                )
        if message.finish_reason:
            events.append(
                NormalizedEvent(
                    type="finish_reason",
                    payload={
                        "message_index": idx,
                        "finish_reason": message.finish_reason,
                    },
                )
            )

    events.append(
        NormalizedEvent(
            type="finish",
            payload={
                "usage": result.usage,
                "metadata": result.metadata,
                "model": result.model,
            },
        )
    )

    return [event.to_dict() for event in events]
