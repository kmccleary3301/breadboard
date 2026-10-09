"""Event metadata and wire conversion for the pinned SDK display format.

``bb_event`` preserves information absent from chat messages: display strings,
response batches and thinking boundaries. It is never sent to the provider.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def display(message: Mapping[str, Any]) -> str:
    metadata = message.get("bb_event") or {}
    if isinstance(metadata.get("display"), str):
        return metadata["display"]
    role = message.get("role", "user")
    content = message.get("content")
    if isinstance(content, str):
        text = content
    else:
        parts = []
        for part in content or ():
            if not isinstance(part, Mapping):
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif part.get("type") in {"image", "image_url", "input_image"}:
                parts.append("[Image: 1 URLs]")
        text = " ".join(parts)
    if len(text) > 500:
        text = text[:497] + "..."
    source = "agent" if role == "assistant" else "user"
    return f"MessageEvent ({source})\n  {role}: {text or '[no text content]'}"


def summary_message(summary: str) -> dict[str, Any]:
    preview = summary[:497] + "..." if len(summary) > 500 else summary
    return {"role": "user", "content": summary, "bb_event": {
        "type": "CondensationSummaryEvent",
        "display": f"CondensationSummaryEvent (environment)\n  user: {preview or '[no text content]'}",
    }}


def wire_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    previous_batch = None
    for event in messages:
        metadata = event.get("bb_event") or {}
        batch = metadata.get("batch")
        message = {k: v for k, v in event.items() if k != "bb_event"}
        if batch is not None and batch == previous_batch and out:
            out[-1]["tool_calls"] = [*out[-1].get("tool_calls", ()), *message.get("tool_calls", ())]
            continue
        previous_batch = batch
        plain = message.get("role") == "user" and not any(message.get(k) for k in ("tool_calls", "tool_call_id", "name", "bb_native_compaction"))
        prior_plain = bool(out) and out[-1].get("role") == "user" and not any(out[-1].get(k) for k in ("tool_calls", "tool_call_id", "name", "bb_native_compaction"))
        if plain and prior_plain:
            left, right = out[-1].get("content"), message.get("content")
            if isinstance(left, str) and isinstance(right, str):
                out[-1]["content"] = left + "\n" + right
                continue
            if isinstance(left, list) and isinstance(right, list):
                out[-1]["content"] = left + right
                continue
        out.append(message)
    return out
