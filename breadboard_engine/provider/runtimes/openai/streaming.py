"""Chat Completions streaming, SSE aggregation, and diagnostics."""

from __future__ import annotations

import base64
import json
import os
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import ProviderRuntime, ProviderRuntimeContext, ProviderRuntimeError
from ....logging.provider_dump import provider_dump_logger
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

    def _encode_body_base64(self, raw: Any, limit: int = 65536) -> Optional[str]:
        """Return base64-encoded body content (up to `limit`) for diagnostics."""

        try:
            payload = getattr(raw, "content", None)
        except Exception:
            payload = None
        if payload is None:
            return None

        data: Optional[bytes]
        try:
            if isinstance(payload, (bytes, bytearray)):
                data = bytes(payload)
            elif isinstance(payload, str):
                data = payload.encode("utf-8", "ignore")
            else:
                data = None
        except Exception:
            data = None

        if not data:
            return None

        if limit and limit > 0:
            data = data[:limit]

        try:
            return base64.b64encode(data).decode("ascii")
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

    def _aggregate_sse_events(self, payloads: List[str]) -> Optional[Dict[str, Any]]:
        """Aggregate SSE chat completion payloads into a final response dictionary."""

        if not payloads:
            return None

        choices_state: Dict[int, Dict[str, Any]] = {}
        response_id: Optional[str] = None
        model_name: Optional[str] = None
        usage_block: Optional[Dict[str, Any]] = None

        for payload in payloads:
            if not payload or payload == "[DONE]":
                continue
            try:
                event_obj = json.loads(payload)
            except json.JSONDecodeError:
                continue

            response_id = event_obj.get("id") or response_id
            model_name = event_obj.get("model") or model_name
            candidate_usage = event_obj.get("usage")
            if candidate_usage and not usage_block:
                usage_block = candidate_usage

            for choice in event_obj.get("choices", []) or []:
                idx = choice.get("index", 0)
                state = choices_state.setdefault(
                    idx,
                    {
                        "role": None,
                        "content": [],
                        "tool_calls": {},
                        "finish_reason": None,
                    },
                )

                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    state["finish_reason"] = finish_reason

                message_obj = choice.get("message") or {}
                if message_obj:
                    role_val = message_obj.get("role")
                    if role_val:
                        state["role"] = role_val
                    content_val = message_obj.get("content")
                    if isinstance(content_val, str):
                        state["content"].append(content_val)
                    elif isinstance(content_val, list):
                        for block in content_val:
                            text_val = self._get_attr(block, "text")
                            if text_val:
                                state["content"].append(str(text_val))
                    tool_calls_list = message_obj.get("tool_calls") or []
                    if tool_calls_list:
                        tool_map: Dict[int, Dict[str, Any]] = {}
                        for tc_idx, tc in enumerate(tool_calls_list):
                            fn_payload = dict(self._get_attr(tc, "function", {}) or {})
                            if "arguments" not in fn_payload:
                                fn_payload["arguments"] = fn_payload.get("arguments", "")
                            tool_map[tc_idx] = {
                                "id": self._get_attr(tc, "id"),
                                "type": self._get_attr(tc, "type", "function"),
                                "function": fn_payload,
                            }
                        state["tool_calls"] = tool_map

                delta_obj = choice.get("delta") or {}
                delta_role = delta_obj.get("role")
                if delta_role:
                    state["role"] = delta_role
                delta_content = delta_obj.get("content")
                if isinstance(delta_content, str):
                    state["content"].append(delta_content)
                elif isinstance(delta_content, list):
                    for block in delta_content:
                        text_val = self._get_attr(block, "text")
                        if text_val:
                            state["content"].append(str(text_val))
                for tc in delta_obj.get("tool_calls", []) or []:
                    tc_index = tc.get("index")
                    if tc_index is None:
                        tc_index = len(state["tool_calls"])
                    call_state = state["tool_calls"].setdefault(
                        tc_index,
                        {
                            "id": None,
                            "type": "function",
                            "function": {"name": None, "arguments": ""},
                        },
                    )
                    if tc.get("id"):
                        call_state["id"] = tc["id"]
                    if tc.get("type"):
                        call_state["type"] = tc["type"]
                    fn_delta = tc.get("function") or {}
                    if fn_delta.get("name"):
                        call_state["function"]["name"] = fn_delta["name"]
                    if fn_delta.get("arguments"):
                        existing = call_state["function"].get("arguments") or ""
                        call_state["function"]["arguments"] = existing + fn_delta["arguments"]

        if not choices_state:
            return None

        assembled_choices: List[Dict[str, Any]] = []
        for idx in sorted(choices_state.keys()):
            state = choices_state[idx]
            content_str = "".join(state["content"]).strip() if state["content"] else None
            tool_calls_map = state["tool_calls"]
            tool_calls_list: List[Dict[str, Any]] = []
            if isinstance(tool_calls_map, dict) and tool_calls_map:
                for tc_idx in sorted(tool_calls_map.keys()):
                    entry = tool_calls_map[tc_idx]
                    fn_payload = dict(entry.get("function") or {})
                    if "arguments" not in fn_payload:
                        fn_payload["arguments"] = ""
                    tool_calls_list.append(
                        {
                            "id": entry.get("id"),
                            "type": entry.get("type", "function"),
                            "function": fn_payload,
                        }
                    )

            message_payload: Dict[str, Any] = {}
            role_val = state.get("role")
            if role_val or content_str or tool_calls_list:
                message_payload["role"] = role_val or "assistant"
            if content_str:
                message_payload["content"] = content_str
            elif tool_calls_list:
                message_payload["content"] = None
            if tool_calls_list:
                message_payload["tool_calls"] = tool_calls_list

            assembled_choices.append(
                {
                    "index": idx,
                    "message": message_payload,
                    "finish_reason": state.get("finish_reason"),
                }
            )

        response_payload: Dict[str, Any] = {
            "choices": assembled_choices,
        }
        if response_id:
            response_payload["id"] = response_id
        if model_name:
            response_payload["model"] = model_name
        if usage_block:
            response_payload["usage"] = usage_block
        return response_payload

    def _parse_sse_chat_completion(self, raw: Any, model: Optional[str]) -> Optional[Dict[str, Any]]:
        """Parse a text/event-stream payload into a chat completion style result."""

        body_text = self._decode_body_text(raw)
        if not body_text:
            return None
        events = self._split_sse_events(body_text)
        response_payload = self._aggregate_sse_events(events)
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

                body_text: Optional[str] = None
                if response_obj is not None:
                    try:
                        body_text = response_obj.text
                    except Exception:
                        try:
                            content = getattr(response_obj, "content", None)
                            if isinstance(content, (bytes, bytearray)):
                                body_text = bytes(content).decode("utf-8", "ignore")
                            elif content is not None:
                                body_text = str(content)
                        except Exception:
                            body_text = None
                body_text = body_text or str(exc)
                provider_dump_logger.log_response(
                    provider=self.descriptor.provider_id,
                    model=kwargs.get("model"),
                    request_id=request_id,
                    status_code=status_code,
                    headers=response_headers or None,
                    content_type=content_type_header,
                    body_text=body_text,
                    body_base64=None,
                    context=context,
                    metadata={"statusText": None, "attempt": attempt, "exception": type(exc).__name__},
                )

                is_rate_limited = status_code == 429
                if is_rate_limited:
                    if attempt < max_retries:
                        fallback_wait = backoffs[attempt] if attempt < len(backoffs) else 0.8
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
                        "response_headers": response_headers or None,
                    }
                    if retry_schedule:
                        details["retry_schedule"] = retry_schedule
                    raise ProviderRuntimeError(str(exc), details=details) from exc

                raise ProviderRuntimeError(str(exc)) from exc
            response_headers = self._normalize_headers(getattr(raw, "headers", {}) or {})
            content_type_header = response_headers.get("content-type")
            normalized_content_type = self._normalize_content_type(content_type_header)
            status_code = getattr(raw, "status_code", None)

            if (
                self.descriptor.provider_id == "openrouter"
                and normalized_content_type
                and not self._is_json_content_type(content_type_header)
            ):
                snippet = self._decode_snippet(getattr(raw, "content", None))
                full_body_text = self._decode_body_text(raw)
                details: Dict[str, Any] = {
                    "body_snippet": snippet,
                    "status_code": status_code,
                    "context": error_context,
                    "attempt": attempt,
                    "content_type": content_type_header,
                    "response_headers": response_headers or None,
                }
                request_id = self._extract_request_id(response_headers)
                if request_id:
                    details["request_id"] = request_id
                if full_body_text:
                    details["raw_excerpt"] = full_body_text[:2000]
                body_b64 = self._encode_body_base64(raw)
                if body_b64:
                    details["raw_body_b64"] = body_b64

                if normalized_content_type == "text/html":
                    details["html_detected"] = True
                    classification = self._classify_html_response(snippet)
                    if classification:
                        details.update(classification)
                    if captured_html is None:
                        captured_html = snippet or (full_body_text[:4000] if full_body_text else None)
                    if attempt < max_retries:
                        try:
                            wait_time = backoffs[attempt] if attempt < len(backoffs) else 0.8
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
                    parsed = self._parse_sse_chat_completion(raw, kwargs.get("model"))
                    if parsed is not None:
                        provider_dump_logger.log_response(
                            provider=self.descriptor.provider_id,
                            model=kwargs.get("model"),
                            request_id=request_id,
                            status_code=status_code,
                            headers=response_headers,
                            content_type=content_type_header,
                            body_text=self._decode_body_text(raw),
                            body_base64=self._encode_body_base64(raw),
                            context=context,
                            metadata={"statusText": getattr(raw, "reason_phrase", None), "attempt": attempt},
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
                    body_text=self._decode_body_text(raw),
                    body_base64=self._encode_body_base64(raw),
                    context=context,
                    metadata={"statusText": getattr(raw, "reason_phrase", None), "attempt": attempt},
                )
                return parsed_payload
            except json.JSONDecodeError as exc:
                last_exc = exc
                snippet = self._decode_snippet(getattr(raw, "content", None))
                # Detect likely HTML payloads
                is_html = "<html" in (snippet or "").lower() or "<!doctype html" in (snippet or "").lower()
                full_body_text = self._decode_body_text(raw)
                last_details = {
                    "body_snippet": snippet,
                    "status_code": status_code,
                    "context": error_context,
                    "attempt": attempt,
                    "html_detected": bool(is_html),
                    "content_type": content_type_header,
                    "response_headers": response_headers or None,
                }
                request_id = self._extract_request_id(response_headers)
                if request_id:
                    last_details["request_id"] = request_id
                if full_body_text:
                    last_details.setdefault("raw_excerpt", full_body_text[:2000])
                body_b64 = self._encode_body_base64(raw)
                if body_b64:
                    last_details.setdefault("raw_body_b64", body_b64)
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
                        wait_time = backoffs[attempt] if attempt < len(backoffs) else 0.8
                        retry_schedule.append(wait_time)
                        provider_sdk_bindings.sleep(wait_time)
                    except Exception:
                        pass
                    continue

                details = dict(last_details)
                details["attempts"] = attempt + 1
                if retry_schedule:
                    details["retry_schedule"] = retry_schedule
                details["retry_outcome"] = "retry_exhausted_html" if details.get("html_detected") else "retry_exhausted_non_json"
                if captured_html:
                    details.setdefault("body_snippet", captured_html)
                    details.setdefault("html_excerpt", captured_html[:2000])
                elif full_body_text:
                    details.setdefault("body_snippet", full_body_text[:400])
                    details.setdefault("raw_excerpt", full_body_text[:2000])
                raise ProviderRuntimeError(
                    "Failed to decode provider response (non-JSON payload). This often indicates an HTML error page from the provider.",
                    details=details,
                ) from exc
            except Exception as exc:
                # Non-JSON errors: do not retry unless they look like transient HTML (covered above)
                last_exc = exc
                break

        # Safety: if we fall out of loop, raise a normalized runtime error
        error_msg = str(last_exc) if last_exc else "Unknown provider error"
        if last_details:
            if retry_schedule:
                last_details.setdefault("retry_schedule", retry_schedule)
            last_details.setdefault(
                "retry_outcome",
                "retry_exhausted_html" if last_details.get("html_detected") else "retry_exhausted_non_json",
            )
            if captured_html and not last_details.get("body_snippet"):
                last_details["body_snippet"] = captured_html
                last_details.setdefault("html_excerpt", captured_html[:2000])
            if "raw_excerpt" not in last_details:
                full_body_text = self._decode_body_text(raw)
                if full_body_text:
                    last_details["raw_excerpt"] = full_body_text[:2000]
            if "raw_body_b64" not in last_details:
                body_b64 = self._encode_body_base64(raw)
                if body_b64:
                    last_details["raw_body_b64"] = body_b64
            raise ProviderRuntimeError(error_msg, details=last_details)
        raise ProviderRuntimeError(error_msg)

    def _stream_emit_event(
        self,
        context: ProviderRuntimeContext,
        event_type: str,
        payload: Dict[str, Any],
        *,
        turn_index: Optional[int],
    ) -> None:
        session_state = getattr(context, "session_state", None)
        emit = getattr(session_state, "_emit_event", None)
        if callable(emit):
            emit(event_type, payload, turn=turn_index)

