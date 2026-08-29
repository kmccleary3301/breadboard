"""Chat Completions streaming, SSE aggregation, and diagnostics."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import ProviderRuntime, ProviderRuntimeContext, ProviderRuntimeError
from ....logging.provider_dump import provider_dump_logger
from ....security import redaction
from ...sdk_bindings import provider_sdk_bindings
from .conversion import OpenAIConversionMixin


class OpenAIBaseRuntime(OpenAIConversionMixin, ProviderRuntime):
    """Utility helpers for OpenAI-compatible runtimes."""

    def _require_openai(self) -> None:
        if provider_sdk_bindings.openai is None:
            raise ProviderRuntimeError("openai package not installed")

    def _decode_snippet(self, content: Any) -> str:
        if content is None:
            return ""
        try:
            if isinstance(content, (bytes, bytearray)):
                return bytes(content).decode("utf-8", "ignore")[:400].strip()
            if hasattr(content, "decode"):
                return content.decode("utf-8", "ignore")[:400].strip()
            text = str(content)
            return text[:400].strip()
        except Exception:
            return ""

    def _decode_body_text(self, raw: Any) -> Optional[str]:
        """Best-effort decode of raw HTTP body for diagnostics."""

        try:
            payload = getattr(raw, "content", None)
        except Exception:
            payload = None
        if payload is None:
            return None

        try:
            if isinstance(payload, str):
                return payload
            if isinstance(payload, (bytes, bytearray)):
                data = bytes(payload)
                headers = getattr(raw, "headers", {}) or {}
                encoding = None
                if isinstance(headers, dict):
                    encoding = headers.get("Content-Encoding") or headers.get("content-encoding")
                if encoding and "gzip" in str(encoding).lower():
                    try:
                        import gzip

                        return gzip.decompress(data).decode("utf-8", "ignore")
                    except Exception:
                        return data.decode("utf-8", "ignore")
                return data.decode("utf-8", "ignore")
            return str(payload)
        except Exception:
            return None


    def _split_sse_events(self, body_text: str) -> List[str]:
        """Split an SSE body into individual `data:` payload strings."""

        events: List[str] = []
        buffer: List[str] = []
        for line in body_text.splitlines():
            if not line.strip():
                if buffer:
                    events.append("\n".join(buffer))
                    buffer = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                buffer.append(line[5:].lstrip())
            else:
                buffer.append(line.strip())
        if buffer:
            events.append("\n".join(buffer))
        return events

    def _aggregate_sse_events(
        self,
        payloads: List[str],
        context: Optional[ProviderRuntimeContext] = None,
    ) -> Optional[Dict[str, Any]]:
        """Strictly aggregate an unexpected SSE Chat Completions response."""

        if not payloads:
            return None

        choices_state: Dict[int, Dict[str, Any]] = {}
        operations: List[Tuple[str, int, Optional[int], Optional[str]]] = []
        response_id: Optional[str] = None
        model_name: Optional[str] = None
        usage_block: Optional[Dict[str, Any]] = None
        usage_supplied = False
        saw_done = False

        def consume_message(
            value: Any,
            *,
            choice_index: int,
            state: Dict[str, Any],
            is_delta: bool,
        ) -> bool:
            if not isinstance(value, dict):
                return False
            allowed = {
                "role",
                "content",
                "tool_calls",
                "refusal",
                "function_call",
                "audio",
                "reasoning",
                "reasoning_content",
                "reasoning_details",
            }
            if any(
                item is not None
                for key, item in value.items()
                if key not in allowed
            ):
                return False
            if any(
                value.get(field) is not None
                for field in (
                    "refusal",
                    "function_call",
                    "audio",
                    "reasoning",
                    "reasoning_content",
                    "reasoning_details",
                )
            ):
                return False

            role = value.get("role")
            if role is not None:
                if role != "assistant" or state["role"] not in {None, role}:
                    return False
                state["role"] = role

            if "content" in value:
                content = value["content"]
                if content is None:
                    pass
                elif isinstance(content, str):
                    state["content_seen"] = True
                    state["content"].append(content)
                    if content:
                        operations.append(
                            ("text", choice_index, None, content)
                        )
                elif isinstance(content, list):
                    for block in content:
                        if (
                            not isinstance(block, dict)
                            or set(block) - {"type", "text"}
                            or block.get("type") not in {"text", "output_text"}
                            or not isinstance(block.get("text"), str)
                        ):
                            return False
                        text = block["text"]
                        state["content_seen"] = True
                        state["content"].append(text)
                        if text:
                            operations.append(
                                ("text", choice_index, None, text)
                            )
                else:
                    return False

            raw_tool_calls = value.get("tool_calls")
            if raw_tool_calls is None:
                raw_tool_calls = []
            if not isinstance(raw_tool_calls, list):
                return False
            for position, raw_tool_call in enumerate(raw_tool_calls):
                if (
                    not isinstance(raw_tool_call, dict)
                    or any(
                        item is not None
                        for key, item in raw_tool_call.items()
                        if key not in {"index", "id", "type", "function"}
                    )
                ):
                    return False
                tool_index = (
                    raw_tool_call.get("index")
                    if is_delta
                    else raw_tool_call.get("index", position)
                )
                if (
                    not isinstance(tool_index, int)
                    or isinstance(tool_index, bool)
                    or tool_index < 0
                ):
                    return False
                tool_type = raw_tool_call.get("type")
                if tool_type is not None and tool_type != "function":
                    return False
                function = raw_tool_call.get("function")
                if function is None:
                    function = {}
                if (
                    not isinstance(function, dict)
                    or any(
                        item is not None
                        for key, item in function.items()
                        if key not in {"name", "arguments"}
                    )
                ):
                    return False
                call_id = raw_tool_call.get("id")
                name = function.get("name")
                arguments = function.get("arguments")
                if call_id is not None and (
                    not isinstance(call_id, str) or not call_id
                ):
                    return False
                if name is not None and (
                    not isinstance(name, str) or not name
                ):
                    return False
                if arguments is not None and not isinstance(arguments, str):
                    return False
                if (
                    call_id is None
                    and name is None
                    and arguments is None
                ):
                    return False

                tool_state = state["tool_calls"].setdefault(
                    tool_index,
                    {
                        "id": None,
                        "name": None,
                        "arguments": "",
                        "arguments_seen": False,
                    },
                )
                if call_id is not None:
                    if tool_state["id"] not in {None, call_id}:
                        return False
                    tool_state["id"] = call_id
                if name is not None:
                    if tool_state["name"] not in {None, name}:
                        return False
                    tool_state["name"] = name
                operations.append(
                    ("tool_touch", choice_index, tool_index, None)
                )
                if arguments is not None:
                    if is_delta:
                        tool_state["arguments"] += arguments
                    elif (
                        tool_state["arguments_seen"]
                        and tool_state["arguments"] != arguments
                    ):
                        return False
                    else:
                        tool_state["arguments"] = arguments
                    tool_state["arguments_seen"] = True
                    if arguments:
                        operations.append(
                            (
                                "tool_arguments",
                                choice_index,
                                tool_index,
                                arguments,
                            )
                        )
            return True

        for position, payload in enumerate(payloads):
            if not payload:
                return None
            if payload == "[DONE]":
                if saw_done or position != len(payloads) - 1:
                    return None
                saw_done = True
                continue
            if saw_done:
                return None
            try:
                event_obj = json.loads(payload)
            except json.JSONDecodeError:
                return None
            if not isinstance(event_obj, dict):
                return None
            allowed_event_fields = {
                "id",
                "object",
                "created",
                "model",
                "choices",
                "usage",
                "system_fingerprint",
                "service_tier",
            }
            if any(
                value is not None
                for key, value in event_obj.items()
                if key not in allowed_event_fields
            ):
                return None

            event_response_id = event_obj.get("id")
            if event_response_id is not None:
                if (
                    not isinstance(event_response_id, str)
                    or not event_response_id
                    or response_id not in {None, event_response_id}
                ):
                    return None
                response_id = event_response_id
            event_model = event_obj.get("model")
            if event_model is not None:
                if (
                    not isinstance(event_model, str)
                    or not event_model
                    or model_name not in {None, event_model}
                ):
                    return None
                model_name = event_model
            if "usage" in event_obj and event_obj["usage"] is not None:
                candidate_usage = event_obj["usage"]
                if not isinstance(candidate_usage, dict):
                    return None
                if usage_supplied and usage_block != candidate_usage:
                    return None
                usage_block = candidate_usage
                usage_supplied = True

            choices = event_obj.get("choices")
            if not isinstance(choices, list):
                return None
            seen_event_choices: set[int] = set()
            for choice in choices:
                if (
                    not isinstance(choice, dict)
                    or any(
                        value is not None
                        for key, value in choice.items()
                        if key
                        not in {
                            "index",
                            "delta",
                            "message",
                            "finish_reason",
                            "logprobs",
                        }
                    )
                    or choice.get("logprobs") is not None
                ):
                    return None
                choice_index = choice.get("index")
                if (
                    not isinstance(choice_index, int)
                    or isinstance(choice_index, bool)
                    or choice_index < 0
                    or choice_index in seen_event_choices
                ):
                    return None
                seen_event_choices.add(choice_index)
                state = choices_state.setdefault(
                    choice_index,
                    {
                        "role": None,
                        "content": [],
                        "content_seen": False,
                        "tool_calls": {},
                        "finish_reason": None,
                    },
                )
                message_present = (
                    "message" in choice and choice["message"] is not None
                )
                delta_present = "delta" in choice and choice["delta"] is not None
                if message_present and delta_present:
                    return None
                if message_present and not consume_message(
                    choice["message"],
                    choice_index=choice_index,
                    state=state,
                    is_delta=False,
                ):
                    return None
                if delta_present and not consume_message(
                    choice["delta"],
                    choice_index=choice_index,
                    state=state,
                    is_delta=True,
                ):
                    return None

                finish_reason = choice.get("finish_reason")
                if finish_reason is not None:
                    if (
                        not isinstance(finish_reason, str)
                        or not re.fullmatch(
                            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}",
                            finish_reason,
                        )
                        or state["finish_reason"]
                        not in {None, finish_reason}
                    ):
                        return None
                    state["finish_reason"] = finish_reason
                    operations.append(
                        ("finish", choice_index, None, None)
                    )

        if not saw_done or not response_id or not choices_state:
            return None

        assembled_choices: List[Dict[str, Any]] = []
        parsed_tool_arguments: Dict[Tuple[int, int], Any] = {}
        for choice_index in sorted(choices_state):
            state = choices_state[choice_index]
            if state["role"] != "assistant" or state["finish_reason"] is None:
                return None
            tool_calls: List[Dict[str, Any]] = []
            for tool_index in sorted(state["tool_calls"]):
                tool_state = state["tool_calls"][tool_index]
                arguments = tool_state["arguments"]
                if (
                    not isinstance(tool_state["id"], str)
                    or not tool_state["id"]
                    or not isinstance(tool_state["name"], str)
                    or not tool_state["name"]
                    or not tool_state["arguments_seen"]
                    or not isinstance(arguments, str)
                    or not arguments
                ):
                    return None
                try:
                    parsed_tool_arguments[(choice_index, tool_index)] = (
                        json.loads(arguments)
                    )
                except json.JSONDecodeError:
                    return None
                tool_calls.append(
                    {
                        "id": tool_state["id"],
                        "type": "function",
                        "function": {
                            "name": tool_state["name"],
                            "arguments": arguments,
                        },
                    }
                )
            message: Dict[str, Any] = {"role": "assistant"}
            if state["content_seen"]:
                message["content"] = "".join(state["content"])
            elif tool_calls:
                message["content"] = None
            if tool_calls:
                message["tool_calls"] = tool_calls
            assembled_choices.append(
                {
                    "index": choice_index,
                    "message": message,
                    "finish_reason": state["finish_reason"],
                }
            )

        if context is not None and context.exchange_recorder is not None:
            content_indices: Dict[Tuple[str, int, int], int] = {}
            next_content_index = 0
            open_text: set[int] = set()
            open_tools: set[Tuple[int, int]] = set()

            def event_index(
                family: str, choice_index: int, source_index: int = 0
            ) -> int:
                nonlocal next_content_index
                key = (family, choice_index, source_index)
                if key not in content_indices:
                    content_indices[key] = next_content_index
                    next_content_index += 1
                return content_indices[key]

            def start_tool(choice_index: int, tool_index: int) -> None:
                key = (choice_index, tool_index)
                if key in open_tools:
                    return
                tool_state = choices_state[choice_index]["tool_calls"][
                    tool_index
                ]
                context.record_provider_event(
                    "tool_call_start",
                    {
                        "content_index": event_index(
                            "tool", choice_index, tool_index
                        ),
                        "message_id": response_id,
                        "call_id": tool_state["id"],
                        "name": tool_state["name"],
                    },
                )
                open_tools.add(key)

            for operation, choice_index, tool_index, value in operations:
                if operation == "text":
                    if choice_index not in open_text:
                        context.record_provider_event(
                            "text_start",
                            {
                                "content_index": event_index(
                                    "text", choice_index
                                ),
                                "message_id": response_id,
                            },
                        )
                        open_text.add(choice_index)
                    context.record_provider_event(
                        "text_delta",
                        {
                            "content_index": event_index(
                                "text", choice_index
                            ),
                            "message_id": response_id,
                            "delta": value,
                        },
                    )
                elif operation == "tool_touch" and tool_index is not None:
                    start_tool(choice_index, tool_index)
                elif operation == "tool_arguments" and tool_index is not None:
                    start_tool(choice_index, tool_index)
                    tool_state = choices_state[choice_index]["tool_calls"][
                        tool_index
                    ]
                    context.record_provider_event(
                        "tool_call_delta",
                        {
                            "content_index": event_index(
                                "tool", choice_index, tool_index
                            ),
                            "message_id": response_id,
                            "call_id": tool_state["id"],
                            "delta": value,
                        },
                    )
                elif operation == "finish":
                    if choice_index in open_text:
                        context.record_provider_event(
                            "text_end",
                            {
                                "content_index": event_index(
                                    "text", choice_index
                                ),
                                "message_id": response_id,
                            },
                        )
                        open_text.remove(choice_index)
                    for key in sorted(tuple(open_tools)):
                        if key[0] != choice_index:
                            continue
                        _, active_tool_index = key
                        tool_state = choices_state[choice_index][
                            "tool_calls"
                        ][active_tool_index]
                        context.record_provider_event(
                            "tool_call_end",
                            {
                                "content_index": event_index(
                                    "tool",
                                    choice_index,
                                    active_tool_index,
                                ),
                                "message_id": response_id,
                                "call_id": tool_state["id"],
                                "arguments": parsed_tool_arguments[key],
                            },
                        )
                        open_tools.remove(key)
            if open_text or open_tools:
                return None

        response_payload: Dict[str, Any] = {
            "id": response_id,
            "choices": assembled_choices,
        }
        if model_name is not None:
            response_payload["model"] = model_name
        if usage_supplied:
            response_payload["usage"] = usage_block
        return response_payload

    def _parse_sse_chat_completion(
        self,
        raw: Any,
        model: Optional[str],
        context: Optional[ProviderRuntimeContext] = None,
    ) -> Optional[Any]:
        """Parse a text/event-stream payload into a chat completion result."""

        body_text = self._decode_body_text(raw)
        if not body_text:
            return None
        events = self._split_sse_events(body_text)
        response_payload = self._aggregate_sse_events(events, context)
        if response_payload is None:
            return None
        if model and "model" not in response_payload:
            response_payload["model"] = model
        return SimpleNamespace(**response_payload)

    def _normalize_headers(self, headers: Any) -> Dict[str, str]:
        """Return a case-insensitive copy of response headers for diagnostics."""

        normalized: Dict[str, str] = {}
        if headers is None:
            return normalized
        try:
            items = headers.items() if hasattr(headers, "items") else headers
            for key, value in items:
                if key is None or value is None:
                    continue
                normalized[str(key).lower()] = str(value)
        except Exception:
            pass
        return normalized

    def _normalize_content_type(self, content_type: Optional[str]) -> Optional[str]:
        """Return the base MIME type without parameters."""

        if not content_type:
            return None
        base = content_type.split(";", 1)[0].strip().lower()
        return base or None

    def _is_json_content_type(self, content_type: Optional[str]) -> bool:
        """Identify content types that should be parsed as JSON."""

        normalized = self._normalize_content_type(content_type)
        if normalized is None:
            return False
        return normalized in {"application/json", "application/problem+json"}

    def _extract_request_id(self, headers: Dict[str, str]) -> Optional[str]:
        """Extract a provider request identifier from response headers if present."""

        for key in ("openrouter-request-id", "x-request-id", "request-id"):
            if key in headers:
                return headers[key]
        return None

    def _classify_html_response(self, snippet: str) -> Optional[Dict[str, str]]:
        """Identify common HTML payloads so callers can surface better hints."""

        lowered = (snippet or "").lower()
        if not lowered:
            return None

        if "rate limit" in lowered or "too many requests" in lowered:
            return {
                "classification": "rate_limited",
                "hint": "Provider rate-limited the request; pause briefly or slow retries.",
            }
        if "cloudflare" in lowered or "cf-ray" in lowered:
            return {
                "classification": "gateway_protection",
                "hint": "Provider gateway (Cloudflare) blocked the call; check upstream status.",
            }
        if "maintenance" in lowered:
            return {
                "classification": "maintenance",
                "hint": "Provider reported maintenance; retry later.",
            }
        return None

    def _call_with_raw_response(
        self,
        collection: Any,
        *,
        error_context: str,
        context: ProviderRuntimeContext,
        **kwargs,
    ):
        """Call provider with raw response handling and short HTML retry.

        Some providers intermittently return HTML error pages. Detect these by
        attempting to parse and, on JSON decode failure with an HTML body
        snippet, retry a small number of times with brief backoff.
        """
        raw_callable = getattr(collection, "with_raw_response", None)
        request_id: Optional[str] = None
        if raw_callable is None:
            request_id = provider_dump_logger.log_request(
                provider=self.descriptor.provider_id,
                model=kwargs.get("model"),
                payload=kwargs,
                context=context,
                metadata={"errorContext": error_context},
            )
            response = collection.create(**kwargs)
            body_text = None
            try:
                body_text = json.dumps(response, default=str)
            except Exception:
                body_text = str(response)
            provider_dump_logger.log_response(
                provider=self.descriptor.provider_id,
                model=kwargs.get("model"),
                request_id=request_id,
                status_code=None,
                headers=None,
                content_type=None,
                body_text=body_text,
                body_base64=None,
                context=context,
                metadata={"rawCallable": False},
            )
            return response

        if self.descriptor.provider_id == "openrouter":
            forced_headers = {
                "Accept": "application/json; charset=utf-8",
                "Accept-Encoding": "identity",
            }
            extra_headers = dict(kwargs.get("extra_headers") or {})
            existing_lower = {key.lower(): key for key in extra_headers}
            for header, value in forced_headers.items():
                if header.lower() not in existing_lower:
                    extra_headers[header] = value
            if extra_headers:
                kwargs["extra_headers"] = extra_headers

        # Small, bounded retry plan per V11 next steps
        max_retries = 2
        backoffs = [0.4, 0.9]
        retry_schedule: List[float] = []

        last_exc: Optional[Exception] = None
        last_details: Dict[str, Any] = {}
        captured_html: Optional[str] = None
        request_id = provider_dump_logger.log_request(
            provider=self.descriptor.provider_id,
            model=kwargs.get("model"),
            payload=kwargs,
            context=context,
            metadata={"errorContext": error_context},
        )
        for attempt in range(max_retries + 1):
            try:
                raw = raw_callable.create(**kwargs)
            except Exception as exc:
                last_exc = exc
                response_obj = getattr(exc, "response", None)
                response_headers = self._normalize_headers(getattr(response_obj, "headers", {}) or {})
                safe_response_headers = redaction.scrub_headers(response_headers)
                status_code = getattr(response_obj, "status_code", None) or getattr(exc, "status_code", None)
                content_type_header = response_headers.get("content-type")

                def _parse_rate_limit_wait_seconds(message: str, headers: Dict[str, str], fallback: float) -> float:
                    retry_after_value = headers.get("retry-after")
                    if retry_after_value:
                        try:
                            return max(0.0, float(retry_after_value))
                        except Exception:
                            pass
                    lowered = (message or "").lower()
                    match = re.search(r"try again in\\s*([0-9.]+)\\s*ms", lowered)
                    if match:
                        try:
                            return max(0.0, float(match.group(1)) / 1000.0)
                        except Exception:
                            pass
                    match = re.search(r"try again in\\s*([0-9.]+)\\s*s", lowered)
                    if match:
                        try:
                            return max(0.0, float(match.group(1)))
                        except Exception:
                            pass
                    return max(0.0, fallback)

                provider_dump_logger.log_response(
                    provider=self.descriptor.provider_id,
                    model=kwargs.get("model"),
                    request_id=request_id,
                    status_code=status_code,
                    headers=response_headers or None,
                    content_type=content_type_header,
                    body_text=None,
                    body_base64=None,
                    context=context,
                    metadata={"statusText": None, "attempt": attempt, "exception": type(exc).__name__,
                    },
                )

                is_rate_limited = status_code == 429
                if is_rate_limited:
                    if attempt < max_retries:
                        fallback_wait = (
                            backoffs[attempt] if attempt < len(backoffs) else 0.8
                        )
                        wait_time = _parse_rate_limit_wait_seconds(str(exc), response_headers, fallback_wait)
                        retry_schedule.append(wait_time)
                        if wait_time > 0:
                            try:
                                provider_sdk_bindings.sleep(wait_time)
                            except Exception:
                                pass
                        continue

                    details: Dict[str, Any] = {
                        "status_code": status_code,
                        "context": error_context,
                        "attempt": attempt,
                        "attempts": attempt + 1,
                        "classification": "rate_limited",
                        "response_headers": safe_response_headers or None,
                    }
                    if retry_schedule:
                        details["retry_schedule"] = retry_schedule
                    raise ProviderRuntimeError(redaction.safe_exception_message(exc), details=details) from None

                raise ProviderRuntimeError(redaction.safe_exception_message(exc)) from None
            response_headers = self._normalize_headers(getattr(raw, "headers", {}) or {})
            safe_response_headers = redaction.scrub_headers(response_headers)
            content_type_header = response_headers.get("content-type")
            normalized_content_type = self._normalize_content_type(content_type_header)
            status_code = getattr(raw, "status_code", None)

            if (
                self.descriptor.provider_id == "openrouter"
                and normalized_content_type
                and not self._is_json_content_type(content_type_header)
            ):
                snippet = redaction.scrub_text(self._decode_snippet(getattr(raw, "content", None)))
                full_body_text = redaction.scrub_text(self._decode_body_text(raw))
                details: Dict[str, Any] = {
                    "body_snippet": snippet,
                    "status_code": status_code,
                    "context": error_context,
                    "attempt": attempt,
                    "content_type": content_type_header,
                    "response_headers": safe_response_headers or None,
                }
                request_id = self._extract_request_id(response_headers)
                if request_id:
                    details["request_id"] = request_id
                if full_body_text:
                    details["raw_excerpt"] = full_body_text[:2000]

                if normalized_content_type == "text/html":
                    details["html_detected"] = True
                    classification = self._classify_html_response(snippet)
                    if classification:
                        details.update(classification)
                    if captured_html is None:
                        captured_html = snippet or (full_body_text[:4000] if full_body_text else None)
                    if attempt < max_retries:
                        try:
                            wait_time = (
                                backoffs[attempt] if attempt < len(backoffs) else 0.8
                            )
                            retry_schedule.append(wait_time)
                            provider_sdk_bindings.sleep(wait_time)
                        except Exception:
                            pass
                        continue
                    details["attempts"] = attempt + 1
                    if retry_schedule:
                        details["retry_schedule"] = retry_schedule
                    if captured_html:
                        details.setdefault("html_excerpt", captured_html[:2000])
                    raise ProviderRuntimeError(
                        "Failed to decode provider response (non-JSON payload). This often indicates an HTML error page from the provider.",
                        details=details,
                    )

                if normalized_content_type == "text/event-stream":
                    details["classification"] = "event_stream"
                    parsed = self._parse_sse_chat_completion(
                        raw, kwargs.get("model"), context
                    )
                    if parsed is not None:
                        provider_dump_logger.log_response(
                            provider=self.descriptor.provider_id,
                            model=kwargs.get("model"),
                            request_id=request_id,
                            status_code=status_code,
                            headers=response_headers,
                            content_type=content_type_header,
                            body_text=redaction.scrub_text(self._decode_body_text(raw)),
                            body_base64=None,
                            context=context,
                            metadata={"statusText": getattr(raw, "reason_phrase", None), "attempt": attempt,
                            },
                        )
                        return parsed
                    details["classification"] = "event_stream_parse_failed"
                    details["sse_parse_failed"] = True
                    raise ProviderRuntimeError(
                        "Unable to parse text/event-stream payload from provider.",
                        details=details,
                    )

                details["classification"] = "unexpected_content_type"
                raise ProviderRuntimeError("Unexpected Content-Type received from provider.", details=details)

            try:
                parsed_payload = raw.parse()
                provider_dump_logger.log_response(
                    provider=self.descriptor.provider_id,
                    model=kwargs.get("model"),
                    request_id=request_id,
                    status_code=status_code,
                    headers=response_headers,
                    content_type=content_type_header,
                    body_text=redaction.scrub_text(self._decode_body_text(raw)),
                    body_base64=None,
                    context=context,
                    metadata={"statusText": getattr(raw, "reason_phrase", None), "attempt": attempt,
                    },
                )
                return parsed_payload
            except json.JSONDecodeError as exc:
                last_exc = exc
                snippet = redaction.scrub_text(self._decode_snippet(getattr(raw, "content", None)))
                # Detect likely HTML payloads
                is_html = (
                    "<html" in (snippet or "").lower() or "<!doctype html" in (snippet or "").lower()
                )
                full_body_text = redaction.scrub_text(self._decode_body_text(raw))
                last_details = {
                    "body_snippet": snippet,
                    "status_code": status_code,
                    "context": error_context,
                    "attempt": attempt,
                    "html_detected": bool(is_html),
                    "content_type": content_type_header,
                    "response_headers": safe_response_headers or None,
                }
                request_id = self._extract_request_id(response_headers)
                if request_id:
                    last_details["request_id"] = request_id
                if full_body_text:
                    last_details.setdefault("raw_excerpt", full_body_text[:2000])
                if is_html:
                    classification = self._classify_html_response(snippet)
                    if classification:
                        last_details.update(classification)
                    if captured_html is None:
                        captured_html = snippet
                        if not captured_html:
                            if full_body_text:
                                captured_html = full_body_text[:4000]
                if is_html and attempt < max_retries:
                    # Short backoff then retry
                    try:
                        wait_time = (
                            backoffs[attempt] if attempt < len(backoffs) else 0.8
                        )
                        retry_schedule.append(wait_time)
                        provider_sdk_bindings.sleep(wait_time)
                    except Exception:
                        pass
                    continue

                details = dict(last_details)
                details["attempts"] = attempt + 1
                if retry_schedule:
                    details["retry_schedule"] = retry_schedule
                details["retry_outcome"] = (
                    "retry_exhausted_html" if details.get("html_detected") else "retry_exhausted_non_json"
                )
                if captured_html:
                    details.setdefault("body_snippet", captured_html)
                    details.setdefault("html_excerpt", captured_html[:2000])
                elif full_body_text:
                    details.setdefault("body_snippet", full_body_text[:400])
                    details.setdefault("raw_excerpt", full_body_text[:2000])
                raise ProviderRuntimeError(
                    "Failed to decode provider response (non-JSON payload). This often indicates an HTML error page from the provider.",
                    details=details,
                ) from None
            except Exception as exc:
                # Non-JSON errors: do not retry unless they look like transient HTML (covered above)
                last_exc = exc
                break

        # Safety: if we fall out of loop, raise a normalized runtime error
        error_msg = (
            redaction.safe_exception_message(last_exc)
            if last_exc
            else "Unknown provider error"
        )
        if last_details:
            if retry_schedule:
                last_details.setdefault("retry_schedule", retry_schedule)
            last_details.setdefault(
                "retry_outcome",
                (
                    "retry_exhausted_html" if last_details.get("html_detected") else "retry_exhausted_non_json"
                ),
            )
            if captured_html and not last_details.get("body_snippet"):
                last_details["body_snippet"] = captured_html
                last_details.setdefault("html_excerpt", captured_html[:2000])
            if "raw_excerpt" not in last_details:
                full_body_text = redaction.scrub_text(self._decode_body_text(raw))
                if full_body_text:
                    last_details["raw_excerpt"] = full_body_text[:2000]
            raise ProviderRuntimeError(error_msg, details=last_details)
        raise ProviderRuntimeError(error_msg)

    def _stream_emit_event(
        self,
        context: ProviderRuntimeContext,
        event_type: str,
        payload: Dict[str, Any],
        *,
        turn_index: Optional[int],
        record: bool = True,
    ) -> None:
        """Capture provider events before projecting them to SessionState."""
        payload, _redaction_problems = redaction.scrub_structure(
            payload,
            path="$.provider_event",
        )
        if not isinstance(payload, dict):
            raise ProviderRuntimeError(
                "Provider event payload is invalid",
                kind="protocol",
                output_emitted=True,
                details={"code": "invalid_provider_event"},
            )
        recorder = getattr(context, "exchange_recorder", None)
        if recorder is not None and record:
            mapping = {
                "assistant.message.start": "text_start",
                "assistant.message.delta": "text_delta",
                "assistant.message.end": "text_end",
                "assistant.reasoning.start": "thinking_start",
                "assistant.reasoning.delta": "thinking_delta",
                "assistant.reasoning.end": "thinking_end",
                "assistant.thought_summary.delta": "thinking_delta",
                "assistant.tool_call.start": "tool_call_start",
                "assistant.tool_call.delta": "tool_call_delta",
                "assistant.tool_call.end": "tool_call_end",
            }
            kind = mapping.get(event_type)
            if kind is None:
                raise ProviderRuntimeError(
                    "Unknown normative provider event",
                    kind="protocol",
                    output_emitted=True,
                    details={"code": "unknown_provider_event"},
                )
            recorder.record(kind, payload)
        session_state = getattr(context, "session_state", None)
        emit = getattr(session_state, "_emit_event", None)
        if callable(emit):
            emit(event_type, payload, turn=turn_index)

