"""Responses wire projections for the pinned OpenCode model transport."""
from __future__ import annotations
import copy
import json
import time
import uuid
from typing import Any

LABEL_HEADERS = {"x-bb-stock-session", "x-bb-stock-agent", "x-bb-stock-provider"}

class StockExecutionError(RuntimeError):
    """A stock-side failure must never fall back to BB's executors."""


def upstream_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in
            LABEL_HEADERS | {"host", "content-length", "authorization", "connection", "x-api-key", "accept-encoding"}}


def responses_payload(response: dict, stream: bool) -> bytes:
    """Emit the Responses events stock's AI SDK consumes, without changing output."""
    if not stream:
        return json.dumps(response).encode()
    events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}}]
    for index, completed in enumerate(response["output"]):
        item = copy.deepcopy(completed)
        if item["type"] == "function_call":
            item["arguments"] = ""
        elif item["type"] == "message":
            item["content"] = []
        elif item["type"] == "reasoning":
            item["summary"] = []
        item["status"] = "in_progress"
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if completed["type"] == "function_call":
            events.extend([
                {"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": index, "delta": completed["arguments"]},
                {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": index, "arguments": completed["arguments"]},
            ])
        elif completed["type"] == "message":
            for ci, part in enumerate(completed["content"]):
                if part["type"] not in {"output_text", "refusal"}:
                    raise StockExecutionError(f"Unsupported parent Responses content: {part['type']}")
                base = {"item_id": item["id"], "output_index": index, "content_index": ci}
                key = "text" if part["type"] == "output_text" else "refusal"
                event_type = "output_text" if key == "text" else "refusal"
                events.extend([
                    {"type": "response.content_part.added", **base, "part": {**part, key: ""}},
                    {"type": f"response.{event_type}.delta", **base, "delta": part[key]},
                    {"type": f"response.{event_type}.done", **base, key: part[key]},
                    {"type": "response.content_part.done", **base, "part": part},
                ])
        elif completed["type"] == "reasoning":
            for si, part in enumerate(completed.get("summary", ())):
                if part.get("type") != "summary_text":
                    raise StockExecutionError("Unsupported parent reasoning summary")
                base = {"item_id": item["id"], "output_index": index, "summary_index": si}
                events.extend([
                    {"type": "response.reasoning_summary_part.added", **base, "part": {**part, "text": ""}},
                    {"type": "response.reasoning_summary_text.delta", **base, "delta": part["text"]},
                    {"type": "response.reasoning_summary_text.done", **base, "text": part["text"]},
                    {"type": "response.reasoning_summary_part.done", **base, "part": part},
                ])
        else:
            raise StockExecutionError(f"Unsupported parent Responses item: {completed['type']}")
        events.append({"type": "response.output_item.done", "output_index": index, "item": completed})
    events.append({"type": "response.completed", "response": response})
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def response_from_result(result: Any, model: str) -> dict:
    raw = result.raw_response
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if isinstance(raw, dict) and isinstance(raw.get("output"), list):
        return copy.deepcopy(raw)
    output = []
    for message in result.messages:
        if message.content:
            if not isinstance(message.content, str):
                raise StockExecutionError("Parent response requires raw Responses output for non-text content")
            output.append({"type": "message", "id": "msg_" + uuid.uuid4().hex,
                           "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": message.content, "annotations": []}]})
        for call in message.tool_calls:
            output.append({"type": "function_call", "id": "fc_" + uuid.uuid4().hex,
                           "call_id": call.id, "name": call.name, "arguments": call.arguments,
                           "status": "completed"})
    usage = result.usage or {}
    return {"id": "resp_" + uuid.uuid4().hex, "object": "response", "model": model,
            "created_at": int(time.time()), "status": "completed", "output": output,
            "usage": {"input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                      "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
                      "input_tokens_details": {"cached_tokens": usage.get("cache_read_tokens", 0)},
                      "output_tokens_details": {"reasoning_tokens": 0}}}


def stock_system_messages(request: dict) -> list[dict]:
    messages = []
    for item in request["input"]:
        if item.get("role") not in {"developer", "system"}:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            raise StockExecutionError("Stock developer prompt must be a string")
        messages.append({"role": "system", "content": content})
    if not messages:
        raise StockExecutionError("Stock omitted the generated developer prompt")
    return messages


def stock_tool_schema(request: dict) -> list[dict]:
    tools = request.get("tools", [])
    if not all(t.get("type") == "function" for t in tools):
        raise StockExecutionError("Pinned stock catalog contains a non-function tool")
    return copy.deepcopy(tools)


