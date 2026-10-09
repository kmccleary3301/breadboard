"""UTF-8 bytes/4 accounting for text and Responses-shaped multimodal items."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


def text_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def _image_adjustment(value: Any) -> int:
    if isinstance(value, Mapping):
        url = value.get("image_url")
        if value.get("type") == "input_image" and isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
            return 7373 - len(url.split(";base64,", 1)[1].encode("utf-8"))
        return sum(_image_adjustment(v) for v in value.values())
    if isinstance(value, list):
        return sum(_image_adjustment(v) for v in value)
    return 0


def response_items(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Translate chat envelopes to the pinned Responses item vocabulary."""
    if "type" in message:
        return [message]
    if message.get("role") == "tool":
        return [{"type": "function_call_output", "call_id": message.get("tool_call_id"), "output": message.get("content", "")}]
    parts = []
    content = message.get("content")
    text_type = "output_text" if message.get("role") == "assistant" else "input_text"
    if isinstance(content, str):
        parts.append({"type": text_type, "text": content})
    for part in content if isinstance(content, list) else ():
        if part.get("type") == "text":
            parts.append({"type": text_type, "text": part["text"]})
        elif part.get("type") == "image_url":
            image = {"type": "input_image", "image_url": part["image_url"]["url"]}
            if "detail" in part["image_url"]:
                image["detail"] = part["image_url"]["detail"]
            parts.append(image)
        else:
            parts.append(part)
    items = [{"type": "message", "role": message.get("role"), "content": parts}] if parts else []
    for call in message.get("tool_calls") or ():
        function = call.get("function", call)
        items.append({"type": "function_call", "call_id": call["id"], "name": function["name"], "arguments": function.get("arguments", "")})
    return items


def bytes4(messages: Sequence[Mapping[str, Any]]) -> int:
    tokens = 0
    for message in messages:
        for item in response_items(message):
            encrypted = item.get("encrypted_content")
            if isinstance(encrypted, str) and item.get("type") in {"reasoning", "compaction", "context_compaction"}:
                size = max(0, len(encrypted.encode("utf-8")) * 3 // 4 - 650)
            else:
                raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                size = max(0, len(raw.encode("utf-8")) + _image_adjustment(item))
            tokens += (size + 3) // 4
    return tokens
