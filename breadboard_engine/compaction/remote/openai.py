"""OpenAI Responses remote compaction port (V1 compact and V2 streaming).

Ports OMP ``packages/agent/src/compaction/openai.ts`` and
``compaction-v2-streaming.ts`` for OpenAI Responses routes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.request

from ..methods import (
    CompactionContext,
    CompactionError,
    CompactionRecord,
    MethodUnavailable,
    RemoteCompactionPort,
)
from ..settings import V2_RETAINED_MESSAGE_TOKEN_BUDGET
from ..state import NATIVE_MARKER_KEY, NativeCompaction
from ..transcript import is_tool_result, leading_system_count, role_of, tool_call_ids

OPENAI_REMOTE_COMPACTION_PRESERVE_KEY = "openaiRemoteCompaction"
CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE = "Output: exceeded available model context → truncated."
COMPACTION_TRIGGER_ITEM: Dict[str, Any] = {"type": "compaction_trigger"}
IMAGE_TOKEN_ESTIMATE = 765
REMOTE_COMPACTION_IMAGE_TOKEN_ESTIMATE = 12_000
REMOTE_COMPACTION_REQUEST_OVERHEAD_TOKENS = 256

CONTEXTUAL_USER_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<additional_context>",
    "<skills",
    "<token_budget>",
    "<model_switch>",
)


def format_remote_compaction_summary(input_tokens: int) -> str:
    """OMP formatRemoteCompactionSummary."""
    msg = "Remote compaction preserved provider-native history for this session."
    if input_tokens > 0:
        msg += f" Compaction processed {input_tokens} input tokens."
    return msg


def _approx_token_count(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _message_content_tokens(item: Mapping[str, Any]) -> int:
    content = item.get("content")
    if not isinstance(content, list):
        return 0
    tokens = 0
    for part in content:
        if not isinstance(part, Mapping):
            continue
        ptype = part.get("type")
        if ptype == "input_image":
            tokens += IMAGE_TOKEN_ESTIMATE
        elif ptype in ("input_text", "output_text"):
            text = part.get("text")
            if isinstance(text, str):
                tokens += _approx_token_count(text)
    return tokens


def _truncate_text_to_budget(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    omitted = max(1, _approx_token_count(text) - max_tokens)
    marker = f"…{omitted} tokens truncated…"
    if max_chars <= len(marker) + 2:
        return text[:max_chars]
    side = max(1, (max_chars - len(marker)) // 2)
    return f"{text[:side]}{marker}{text[-side:]}"


def _truncate_retained_message(
    item: Mapping[str, Any], max_tokens: int
) -> Optional[Dict[str, Any]]:
    content = item.get("content")
    if not isinstance(content, list):
        return None
    remaining = max_tokens
    new_content: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        ptype = part.get("type")
        if ptype == "input_image":
            if remaining < IMAGE_TOKEN_ESTIMATE:
                continue
            new_content.append(dict(part))
            remaining = max(0, remaining - IMAGE_TOKEN_ESTIMATE)
            continue
        if ptype not in ("input_text", "output_text") or remaining == 0:
            continue
        text = str(part.get("text") or "")
        t_tokens = _approx_token_count(text)
        if t_tokens <= remaining:
            new_content.append(dict(part))
            remaining = max(0, remaining - t_tokens)
            continue
        trunc_text = _truncate_text_to_budget(text, remaining)
        remaining = 0
        if trunc_text:
            cloned = dict(part)
            cloned["text"] = trunc_text
            new_content.append(cloned)
    if not new_content:
        return None
    res = dict(item)
    res["content"] = new_content
    return res


def is_contextual_user_message(item: Mapping[str, Any]) -> bool:
    content = item.get("content")
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, Mapping) and part.get("type") == "input_text":
            text = str(part.get("text") or "").lstrip().lower()
            if any(text.startswith(prefix) for prefix in CONTEXTUAL_USER_PREFIXES):
                return True
    return False


def build_compaction_v2_replacement_history(
    input_items: Sequence[Mapping[str, Any]],
    compaction_item: Mapping[str, Any],
    retained_budget: int = V2_RETAINED_MESSAGE_TOKEN_BUDGET,
) -> Tuple[List[Dict[str, Any]], int]:
    """Port of OMP buildCompactionV2ReplacementHistory."""
    budget = min(V2_RETAINED_MESSAGE_TOKEN_BUDGET, max(1, int(retained_budget)))
    retained: list[dict[str, Any]] = []
    for item in input_items:
        if (
            isinstance(item, Mapping)
            and item.get("type") == "message"
            and item.get("role") == "user"
            and not is_contextual_user_message(item)
        ):
            retained.append(dict(item))

    # Truncate retained messages to token budget, walking backwards
    remaining = budget
    kept_reversed: list[dict[str, Any]] = []
    for item in reversed(retained):
        if remaining == 0:
            continue
        token_count = max(_message_content_tokens(item), 1)
        if token_count <= remaining:
            kept_reversed.append(item)
            remaining = max(0, remaining - token_count)
            continue
        truncated = _truncate_retained_message(item, remaining)
        if truncated:
            kept_reversed.append(truncated)
            remaining = 0

    kept = list(reversed(kept_reversed))
    image_count = sum(
        1
        for m in kept
        for part in (m.get("content") or [])
        if isinstance(part, Mapping) and part.get("type") == "input_image"
    )
    kept.append(dict(compaction_item))
    return kept, image_count


def build_openai_native_history(
    messages: Sequence[Mapping[str, Any]],
    model: str,
    previous_replacement_history: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Port of OMP buildOpenAiNativeHistory for Responses chat format."""
    input_items: List[Dict[str, Any]] = []
    if previous_replacement_history:
        input_items.extend(dict(item) for item in previous_replacement_history)

    known_call_ids: set[str] = set()
    custom_call_ids: set[str] = set()
    for item in input_items:
        call_id = item.get("call_id")
        if isinstance(call_id, str):
            known_call_ids.add(call_id)
            if item.get("type") == "custom_tool_call":
                custom_call_ids.add(call_id)

    msg_index = 0
    for message in messages:
        if NATIVE_MARKER_KEY in message:
            marker = message[NATIVE_MARKER_KEY]
            if isinstance(marker, Mapping):
                items = marker.get("items") or ()
                for it in items:
                    if isinstance(it, Mapping):
                        input_items.append(dict(it))
                        cid = it.get("call_id")
                        if isinstance(cid, str):
                            known_call_ids.add(cid)
            continue

        role = message.get("role")
        if role in ("user", "developer"):
            content = message.get("content")
            content_blocks: list[dict[str, Any]] = []
            if isinstance(content, str):
                if content.strip():
                    content_blocks.append({"type": "input_text", "text": content})
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, Mapping):
                        btype = block.get("type")
                        if btype in ("text", "input_text"):
                            text = block.get("text")
                            if text:
                                content_blocks.append({"type": "input_text", "text": str(text)})
                        elif btype in ("image", "input_image"):
                            url = block.get("image_url") or block.get("url")
                            content_blocks.append({"type": "input_image", "image_url": str(url)})
            if content_blocks:
                input_items.append({"type": "message", "role": role, "content": content_blocks})
            msg_index += 1
            continue

        if role == "assistant":
            # Assistant thinking / reasoning
            thinking = message.get("thinking")
            if isinstance(thinking, Mapping):
                input_items.append(dict(thinking))

            # Assistant text
            text = message.get("content")
            if isinstance(text, list):
                parts = []
                for p in text:
                    if isinstance(p, Mapping) and p.get("type") in ("text", "output_text"):
                        parts.append(str(p.get("text") or ""))
                text = "".join(parts)
            if isinstance(text, str) and text.strip():
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                    "status": "completed",
                    "id": f"msg_{msg_index}",
                })

            # Assistant tool calls
            tool_calls = message.get("tool_calls") or ()
            for call in tool_calls:
                if not isinstance(call, Mapping):
                    continue
                call_id = call.get("id") or call.get("call_id")
                if not isinstance(call_id, str):
                    continue
                func = call.get("function") or {}
                name = func.get("name") if isinstance(func, Mapping) else call.get("name")
                args = func.get("arguments") if isinstance(func, Mapping) else call.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args if args is not None else {})
                known_call_ids.add(call_id)
                input_items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": str(name or ""),
                    "arguments": args,
                })

            msg_index += 1
            continue

        if role in ("tool", "tool_result"):
            call_id = message.get("tool_call_id") or message.get("call_id")
            if not isinstance(call_id, str) or call_id not in known_call_ids:
                msg_index += 1
                continue
            output = message.get("content")
            if not isinstance(output, str):
                output = json.dumps(output if output is not None else "")
            input_items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            })
            msg_index += 1
            continue

        msg_index += 1

    return input_items


def _estimate_input_tokens(input_items: Sequence[Mapping[str, Any]]) -> int:
    tokens = REMOTE_COMPACTION_REQUEST_OVERHEAD_TOKENS
    for item in input_items:
        itype = item.get("type")
        if itype == "message":
            tokens += _message_content_tokens(item)
        elif itype in ("function_call_output", "custom_tool_call_output"):
            out = item.get("output")
            if isinstance(out, str):
                tokens += _approx_token_count(out)
        elif itype == "function_call":
            args = item.get("arguments")
            if isinstance(args, str):
                tokens += _approx_token_count(args)
        else:
            tokens += 20
    return tokens


def trim_remote_compaction_input_to_context_window(
    input_items: Sequence[Mapping[str, Any]],
    context_window: Optional[int],
) -> Dict[str, Any]:
    """Port of OMP trimRemoteCompactionInputToContextWindow."""
    items = [dict(item) for item in input_items]
    tokens_before = _estimate_input_tokens(items)
    if not context_window or context_window <= 0 or tokens_before <= context_window:
        return {
            "input": items,
            "rewritten_outputs": 0,
            "estimated_tokens_before": tokens_before,
            "estimated_tokens_after": tokens_before,
            "fits": True,
        }

    rewritten_outputs = 0
    current_tokens = tokens_before
    for idx in range(len(items) - 1, -1, -1):
        if current_tokens <= context_window:
            break
        item = items[idx]
        if item.get("type") in ("function_call_output", "custom_tool_call_output"):
            old_output = str(item.get("output") or "")
            items[idx] = {**item, "output": CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE}
            rewritten_outputs += 1
            current_tokens = _estimate_input_tokens(items)

    fits = current_tokens <= context_window
    return {
        "input": items,
        "rewritten_outputs": rewritten_outputs,
        "estimated_tokens_before": tokens_before,
        "estimated_tokens_after": current_tokens,
        "fits": fits,
    }


def parse_sse_events(raw_body: str) -> List[Dict[str, Any]]:
    """Parse Server-Sent Events text into event objects."""
    events: List[Dict[str, Any]] = []
    lines = raw_body.splitlines()
    event_type: Optional[str] = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_type, data_lines
        if not data_lines:
            event_type = None
            return
        payload_str = "\n".join(data_lines)
        data_lines = []
        if payload_str.strip() == "[DONE]":
            event_type = None
            return
        try:
            parsed = json.loads(payload_str)
            if isinstance(parsed, dict):
                if event_type and "type" not in parsed:
                    parsed["type"] = event_type
                events.append(parsed)
        except Exception:
            pass
        event_type = None

    for line in lines:
        line = line.rstrip("\r\n")
        if not line:
            flush()
        elif line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return events


class OpenAIResponsesCompactionPort:
    """RemoteCompactionPort implementation for OpenAI Responses V1 and V2 streaming."""

    provider: str = "openai"
    api: str = "responses"

    def __init__(
        self,
        *,
        client: Any = None,
        model: Optional[str] = None,
        context: Any = None,
        runtime: Any = None,
        http_poster: Optional[Callable[[str, Mapping[str, Any], Optional[Mapping[str, str]]], Any]] = None,
        endpoint: Optional[str] = None,
        v2_endpoint: Optional[str] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.context = context
        self.runtime = runtime
        self._http_poster = http_poster
        self.endpoint = endpoint
        self.v2_endpoint = v2_endpoint

    def supports(self, model: str) -> bool:
        if self.model and self.model == model:
            return True
        # Responses compaction supports OpenAI responses models
        return True

    def _resolve_endpoints(self, model: str) -> Tuple[str, str]:
        # Determine base URL from client, runtime, or default
        base_url = "https://api.openai.com/v1"
        if hasattr(self.client, "base_url") and self.client.base_url:
            base_url = str(self.client.base_url).rstrip("/")
        elif hasattr(self.runtime, "descriptor") and getattr(self.runtime.descriptor, "base_url", None):
            base_url = str(self.runtime.descriptor.base_url).rstrip("/")

        # V1 compact endpoint
        if self.endpoint:
            v1_url = self.endpoint
        elif "/codex" in base_url or "codex" in model:
            v1_url = f"{base_url}/responses/compact" if base_url.endswith("/codex") else f"{base_url}/codex/responses/compact"
        elif base_url.endswith("/v1"):
            v1_url = f"{base_url}/responses/compact"
        else:
            v1_url = f"{base_url}/v1/responses/compact"

        # V2 streaming endpoint
        if self.v2_endpoint:
            v2_url = self.v2_endpoint
        elif "/codex" in base_url or "codex" in model:
            v2_url = f"{base_url}/responses" if base_url.endswith("/codex") else f"{base_url}/codex/responses"
        elif base_url.endswith("/responses"):
            v2_url = base_url
        elif base_url.endswith("/v1"):
            v2_url = f"{base_url}/responses"
        else:
            v2_url = f"{base_url}/v1/responses"

        return v1_url, v2_url

    def _post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        if self._http_poster is not None:
            return self._http_poster(url, payload, headers)

        # Use client._client (httpx.Client) if available
        httpx_client = getattr(self.client, "_client", None)
        if httpx_client is not None and hasattr(httpx_client, "post"):
            resp = httpx_client.post(url, json=payload, headers=headers or {})
            if resp.status_code >= 400:
                raise CompactionError(f"HTTP {resp.status_code} from {url}: {resp.text}")
            if "text/event-stream" in resp.headers.get("content-type", ""):
                return resp.text
            return resp.json()

        # Fallback to urllib
        req_headers = {"Content-Type": "application/json"}
        api_key = getattr(self.client, "api_key", None)
        if api_key:
            req_headers["Authorization"] = f"Bearer {api_key}"
        if headers:
            req_headers.update(headers)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = resp.read().decode("utf-8")
                ct = resp.headers.get("Content-Type", "")
                if "text/event-stream" in ct:
                    return body
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise CompactionError(f"HTTP {exc.code} from {url}: {err_body}") from exc
        except Exception as exc:
            raise CompactionError(f"Request to {url} failed: {exc}") from exc

    def compact(self, context: CompactionContext) -> CompactionRecord:
        head = leading_system_count(context.messages)
        remote_messages = context.projected()[head:] if context.native_retention is not None else context.messages[head:]
        if not remote_messages:
            raise MethodUnavailable("No messages to compact")

        # Check if previous boundary has usable native items to reuse
        prev_replacement: Optional[Sequence[Mapping[str, Any]]] = None
        latest_boundary = context.state.latest_boundary()
        if context.native_retention is None and latest_boundary and latest_boundary.native and latest_boundary.native.usable_for(context.target):
            prev_replacement = latest_boundary.native.items

        model = context.target.model
        native_history = build_openai_native_history(
            remote_messages, model, previous_replacement_history=prev_replacement
        )
        if not native_history:
            raise MethodUnavailable("Native history construction produced no items")

        # Trim oversized trailing outputs to context window
        trimmed = trim_remote_compaction_input_to_context_window(
            native_history, context.context_window
        )
        if not trimmed["fits"]:
            raise MethodUnavailable(
                f"Remote compaction input exceeds context window: {trimmed['estimated_tokens_after']} > {context.context_window}"
            )
        input_items = trimmed["input"]
        instructions = context.custom_instructions or "You are a helpful coding assistant."

        v1_url, v2_url = self._resolve_endpoints(model)
        used_v2 = False
        replacement_history: list[dict[str, Any]] = []
        used_tokens = 0

        # Try V2 streaming first if enabled (default True in settings)
        if context.settings.remote_streaming_v2_enabled:
            try:
                v2_payload = {
                    "model": model,
                    "input": [*input_items, COMPACTION_TRIGGER_ITEM],
                    "instructions": instructions,
                    "stream": True,
                    "store": False,
                }
                raw_v2_resp = self._post(v2_url, v2_payload, None)
                events: List[Dict[str, Any]] = []
                if isinstance(raw_v2_resp, str):
                    events = parse_sse_events(raw_v2_resp)
                elif isinstance(raw_v2_resp, list):
                    events = raw_v2_resp
                elif isinstance(raw_v2_resp, dict):
                    events = [raw_v2_resp]

                compaction_item: Optional[dict[str, Any]] = None
                saw_completed = False
                usage: dict[str, Any] = {}

                for ev in events:
                    etype = ev.get("type")
                    if etype == "response.output_item.done":
                        item = ev.get("item")
                        if isinstance(item, Mapping) and item.get("type") == "compaction":
                            compaction_item = dict(item)
                    elif etype in ("response.completed", "response.done"):
                        saw_completed = True
                        resp_obj = ev.get("response") or ev
                        usage = resp_obj.get("usage") or {}
                    elif etype in ("response.failed", "response.incomplete", "error"):
                        err_msg = ev.get("message") or ev.get("error") or etype
                        raise CompactionError(f"V2 stream failed: {err_msg}")

                if not compaction_item:
                    raise CompactionError("V2 compaction stream did not return a compaction output item")

                used_tokens = int(usage.get("input_tokens") or 0)
                retained_budget = context.settings.v2_retained_message_budget
                if context.native_retention is not None:
                    replacement_history = [*context.native_retention.retain(input_items), compaction_item]
                else:
                    replacement_history, _ = build_compaction_v2_replacement_history(
                        input_items, compaction_item, retained_budget
                    )
                used_v2 = True
            except MethodUnavailable:
                raise
            except Exception:
                # Fall back to V1 compact
                used_v2 = False

        if not used_v2:
            # V1 /responses/compact
            v1_payload = {
                "model": model,
                "input": input_items,
                "instructions": instructions,
            }
            resp_data = self._post(v1_url, v1_payload, None)
            if not isinstance(resp_data, dict):
                raise CompactionError("Invalid response from /responses/compact endpoint")
            raw_output = resp_data.get("output") or []
            compaction_item = None
            replacement_history = []
            for it in raw_output:
                if not isinstance(it, Mapping):
                    continue
                itype = it.get("type")
                if itype in ("compaction", "compaction_summary"):
                    compaction_item = dict(it)
                    replacement_history.append(dict(it))
                elif itype == "message" and it.get("role") in ("assistant", "user"):
                    replacement_history.append(dict(it))

            if not compaction_item:
                raise CompactionError("Remote compaction response missing compaction item")

        summary_text = format_remote_compaction_summary(used_tokens)
        native = NativeCompaction(
            provider=self.provider,
            api=self.api,
            model=model,
            items=tuple(replacement_history),
            token_estimate=used_tokens,
        )

        return context.new_record(
            method="remote",
            first_kept_index=len(context.messages),
            summary=summary_text,
            short_summary="Remote compaction",
            summary_messages=(),  # OMP produces no readable text for remote compaction
            native=native,
            details={
                "used_tokens": used_tokens,
                "replacement_history_length": len(replacement_history),
                "v2_streaming": used_v2,
            },
        )
