"""Runtime abstractions and concrete runtimes for model providers."""

from __future__ import annotations

import base64
import datetime
import json
import random
import re
from dataclasses import dataclass, field
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Type
import textwrap

try:  # pragma: no cover - import guard exercised in runtime
    from openai import OpenAI
except ImportError:  # pragma: no cover - covered via error path tests
    OpenAI = None  # type: ignore[assignment]

try:  # pragma: no cover - import guard exercised in runtime
    from anthropic import Anthropic
    from anthropic import RateLimitError as AnthropicRateLimitError
    try:
        from anthropic._exceptions import OverloadedError as AnthropicOverloadedError  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - optional import
        AnthropicOverloadedError = None  # type: ignore[assignment]
except ImportError:  # pragma: no cover - covered via error path tests
    Anthropic = None  # type: ignore[assignment]
    AnthropicRateLimitError = None  # type: ignore[assignment]
    AnthropicOverloadedError = None  # type: ignore[assignment]

from .provider_routing import ProviderDescriptor
from .logging.provider_dump import provider_dump_logger


# ---------------------------------------------------------------------------
# Normalised result objects shared by runtimes
# ---------------------------------------------------------------------------


@dataclass
class ProviderToolCall:
    """Normalized representation of a provider tool call."""

    id: Optional[str]
    name: Optional[str]
    arguments: str
    type: str = "function"
    raw: Any = None


@dataclass
class ProviderMessage:
    """Normalized assistant message returned from a provider."""

    role: str
    content: Optional[str]
    tool_calls: List[ProviderToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    index: Optional[int] = None
    raw_message: Any = None
    raw_choice: Any = None
    reasoning: Optional[Any] = None
    annotations: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    """Result object returned from a provider runtime invocation."""

    messages: List[ProviderMessage]
    raw_response: Any
    usage: Optional[Dict[str, Any]] = None
    encrypted_reasoning: Optional[List[Any]] = None
    reasoning_summaries: Optional[List[str]] = None
    model: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderRuntimeContext:
    """Context object passed to provider runtimes."""

    session_state: Any
    agent_config: Dict[str, Any]
    stream: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class ProviderRuntimeError(RuntimeError):
    """Raised when a provider runtime encounters a fatal error."""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details: Dict[str, Any] = details or {}


# ---------------------------------------------------------------------------
# Base runtime + registry
# ---------------------------------------------------------------------------


class ProviderRuntime:
    """Interface for provider runtimes."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        raise NotImplementedError

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        raise NotImplementedError


class ProviderRuntimeRegistry:
    """Registry that maps runtime identifiers to implementation classes."""

    def __init__(self) -> None:
        self._runtime_classes: Dict[str, Type[ProviderRuntime]] = {}

    def register_runtime(self, runtime_id: str, runtime_cls: Type[ProviderRuntime]) -> None:
        if not issubclass(runtime_cls, ProviderRuntime):  # defensive guard
            raise TypeError(f"Runtime {runtime_cls!r} must inherit ProviderRuntime")
        self._runtime_classes[runtime_id] = runtime_cls

    def get_runtime_class(self, runtime_id: str) -> Optional[Type[ProviderRuntime]]:
        return self._runtime_classes.get(runtime_id)

    def create_runtime(self, descriptor: ProviderDescriptor) -> ProviderRuntime:
        runtime_cls = self.get_runtime_class(descriptor.runtime_id)
        if runtime_cls is None:
            raise ProviderRuntimeError(
                f"Unknown provider runtime '{descriptor.runtime_id}' for provider '{descriptor.provider_id}'"
            )
        return runtime_cls(descriptor)


provider_registry = ProviderRuntimeRegistry()


# ---------------------------------------------------------------------------
# Shared helper mixins
# ---------------------------------------------------------------------------


class OpenAIBaseRuntime(ProviderRuntime):
    """Utility helpers for OpenAI-compatible runtimes."""

    def _require_openai(self) -> None:
        if OpenAI is None:
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
                                time.sleep(wait_time)
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
                            time.sleep(wait_time)
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
                        time.sleep(wait_time)
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

    # --- data conversion helpers -----------------------------------------
    def _convert_messages_to_chat(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if isinstance(content, list):
                converted.append({"role": role, "content": content})
            else:
                converted.append({"role": role, "content": content})
        return converted

    def _convert_tools_to_openai(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None
        # Tools already follow OpenAI schema in upstream config; clone defensively
        return [dict(tool) for tool in tools]

    # --- response parsing helpers ----------------------------------------
    def _get_attr(self, obj: Any, name: str, default: Any = None) -> Any:
        if hasattr(obj, name):
            return getattr(obj, name)
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default

    def _message_content_to_text(self, content: Any) -> Optional[str]:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        parts: List[str] = []
        try:
            for block in content:
                block_type = self._get_attr(block, "type")
                if block_type in {"input_text", "output_text", "text"}:
                    text_val = self._get_attr(block, "text", "")
                    if text_val:
                        parts.append(str(text_val))
        except Exception:
            return None
        return "".join(parts) if parts else None

    def _extract_tool_calls(self, message: Any) -> List[ProviderToolCall]:
        results: List[ProviderToolCall] = []
        raw_tool_calls = self._get_attr(message, "tool_calls") or []
        for raw in raw_tool_calls:
            fn = self._get_attr(raw, "function", {}) or {}
            arguments = self._get_attr(fn, "arguments", "{}")
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(arguments)
                except Exception:
                    arguments = "{}"
            results.append(
                ProviderToolCall(
                    id=self._get_attr(raw, "id"),
                    name=self._get_attr(fn, "name"),
                    arguments=arguments,
                    type=self._get_attr(raw, "type", "function"),
                    raw=raw,
                )
            )
        return results

    def _extract_usage(self, response: Any) -> Optional[Dict[str, Any]]:
        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return None
        try:
            return dict(usage_obj)
        except Exception:
            try:
                return usage_obj.model_dump()  # type: ignore[attr-defined]
            except Exception:
                return None


# ---------------------------------------------------------------------------
# OpenAI chat runtime (Chat Completions)
# ---------------------------------------------------------------------------


class OpenAIChatRuntime(OpenAIBaseRuntime):
    """Runtime for OpenAI Chat Completions API."""

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        self._require_openai()
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if default_headers:
            kwargs["default_headers"] = default_headers
        return OpenAI(**kwargs)

    def _stream_chat_completion(
        self,
        client: Any,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "tools": tools,
            }
            if extra_body:
                kwargs["extra_body"] = extra_body
            stream_ctx = client.chat.completions.stream(
                **kwargs,
            )
        except AttributeError as exc:
            raise ProviderRuntimeError("OpenAI SDK does not expose chat streaming helpers") from exc
        except Exception as exc:  # pragma: no cover - guarded via ProviderRuntimeError tests
            raise ProviderRuntimeError(str(exc)) from exc

        try:
            with stream_ctx as stream:
                for _ in stream:
                    pass
                return stream.get_final_response()
        except Exception as exc:  # pragma: no cover - guarded in tests via ProviderRuntimeError
            raise ProviderRuntimeError(str(exc)) from exc

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        request_messages = self._convert_messages_to_chat(messages)
        request_tools = self._convert_tools_to_openai(tools)
        extra_body: Optional[Dict[str, Any]] = None
        if self.descriptor.provider_id == "openrouter" and isinstance(model, str) and model.startswith("openai/gpt-5"):
            # Force provider routing away from Azure for GPT-5 OpenAI models on OpenRouter,
            # since some upstreams reject tool outputs.
            extra_body = {"provider": {"order": ["openai"], "allow_fallbacks": False}}

        response: Any = None
        if stream:
            response = self._stream_chat_completion(
                client,
                model=model,
                messages=request_messages,
                tools=request_tools,
                extra_body=extra_body,
            )

        if response is None:
            try:
                response = self._call_with_raw_response(
                    client.chat.completions,
                    error_context="chat.completions.create",
                    context=context,
                    model=model,
                    messages=request_messages,
                    tools=request_tools,
                    stream=False,
                    extra_body=extra_body,
                )
            except ProviderRuntimeError:
                raise
            except Exception as exc:  # pragma: no cover - exercised in integration
                raise ProviderRuntimeError(str(exc)) from exc

        normalized_messages: List[ProviderMessage] = []
        for idx, choice in enumerate(getattr(response, "choices", []) or []):
            error_obj = self._get_attr(choice, "error")
            if error_obj:
                msg = self._get_attr(error_obj, "message") or str(error_obj)
                raise ProviderRuntimeError(msg)
            message = self._get_attr(choice, "message", {})
            normalized_messages.append(
                ProviderMessage(
                    role=self._get_attr(message, "role", "assistant"),
                    content=self._message_content_to_text(self._get_attr(message, "content")),
                    tool_calls=self._extract_tool_calls(message),
                    finish_reason=self._get_attr(choice, "finish_reason"),
                    index=idx,
                    raw_message=message,
                    raw_choice=choice,
                )
            )

        return ProviderResult(
            messages=normalized_messages,
            raw_response=response,
            usage=self._extract_usage(response),
            model=getattr(response, "model", None),
            metadata={},
        )


provider_registry.register_runtime("openai_chat", OpenAIChatRuntime)
provider_registry.register_runtime("openrouter_chat", OpenAIChatRuntime)


# ---------------------------------------------------------------------------
# OpenAI Responses runtime
# ---------------------------------------------------------------------------


class OpenAIResponsesRuntime(OpenAIChatRuntime):
    """Runtime for OpenAI Responses API."""

    def _split_messages_for_responses(
        self,
        messages: List[Dict[str, Any]],
        context: ProviderRuntimeContext,
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """
        Split chat-style messages into (instructions, input_messages) for the Responses API.

        - System messages are merged into a single instructions string.
        - For stateful conversations (conversation_id or previous_response_id present),
          input is trimmed to only the new "inputs" since the last assistant turn (typically
          tool outputs + the per-turn user stub), avoiding re-sending full history.
        """
        system_messages: List[Dict[str, Any]] = []
        non_system: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                system_messages.append(msg)
            else:
                non_system.append(msg)

        provider_tools_cfg = context.agent_config.get("provider_tools") or {}
        provider_cfg: Dict[str, Any] = {}
        if isinstance(provider_tools_cfg, dict):
            provider_specific = provider_tools_cfg.get(self.descriptor.provider_id)
            if isinstance(provider_specific, dict):
                provider_cfg = provider_specific
            else:
                openai_specific = provider_tools_cfg.get("openai")
                if isinstance(openai_specific, dict) and self.descriptor.provider_id in ("openai", "openrouter"):
                    provider_cfg = openai_specific
                else:
                    provider_cfg = provider_tools_cfg
        use_developer = bool(provider_cfg.get("responses_use_developer_role"))
        responses_stateful = True
        if isinstance(provider_cfg, dict) and "responses_stateful" in provider_cfg:
            responses_stateful = bool(provider_cfg.get("responses_stateful"))

        # Build instructions from system messages (if any)
        instructions_parts: List[str] = []
        for msg in system_messages:
            content = msg.get("content")
            text_val = self._message_content_to_text(content)
            if text_val:
                instructions_parts.append(text_val)
        instructions = "\n\n".join(instructions_parts) if instructions_parts else None
        developer_messages: List[Dict[str, Any]] = []
        if use_developer and system_messages:
            # Preserve system content as developer role inside input to mirror OpenCode.
            for msg in system_messages:
                cloned = dict(msg)
                cloned["role"] = "developer"
                developer_messages.append(cloned)
            instructions = None

        has_conversation = False
        if responses_stateful:
            has_conversation = bool(
                context.session_state.get_provider_metadata("conversation_id")
                or context.session_state.get_provider_metadata("previous_response_id")
            )

        if not non_system:
            return instructions, developer_messages

        if not has_conversation:
            # First call: send full non-system history
            return instructions, developer_messages + non_system

        # Subsequent calls: keep only messages after the last assistant message.
        # This preserves tool outputs (role=tool) that must accompany function calls
        # referenced by `previous_response_id`.
        last_assistant_index: Optional[int] = None
        for idx in range(len(non_system) - 1, -1, -1):
            if non_system[idx].get("role") == "assistant":
                last_assistant_index = idx
                break

        if last_assistant_index is None:
            slice_start = max(len(non_system) - 6, 0)
            trimmed = non_system[slice_start:]
        else:
            include_last_assistant = False
            if self.descriptor.provider_id == "openrouter":
                last_assistant = non_system[last_assistant_index]
                if isinstance(last_assistant.get("tool_calls"), list) and last_assistant.get("tool_calls"):
                    include_last_assistant = True
            trimmed = non_system[last_assistant_index:] if include_last_assistant else non_system[last_assistant_index + 1:]

        return instructions, developer_messages + trimmed

    def _convert_messages_to_input(
        self,
        messages: List[Dict[str, Any]],
        *,
        include_tool_calls: bool = False,
    ) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        tool_output_call_ids: set[str] = set()
        if include_tool_calls:
            for message in messages:
                role = message.get("role", "user")
                if str(role or "").lower() != "tool":
                    continue
                call_id = message.get("tool_call_id") or message.get("tool_use_id") or message.get("call_id")
                if call_id:
                    tool_output_call_ids.add(str(call_id))
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            role_lower = str(role or "").lower()

            # OpenRouter's Responses proxy may require tool calls to be echoed in `input`
            # so function_call_output items can be associated without stateful linking.
            if include_tool_calls and role_lower == "assistant":
                tool_calls = message.get("tool_calls")
                emitted_call = False
                if isinstance(tool_calls, list) and tool_calls:
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
                        if not call_id:
                            continue
                        if tool_output_call_ids and str(call_id) not in tool_output_call_ids:
                            continue
                        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                        name = (fn or {}).get("name") or call.get("name")
                        arguments = (fn or {}).get("arguments") or call.get("arguments") or "{}"
                        if call_id and name:
                            if not isinstance(arguments, str):
                                try:
                                    arguments = json.dumps(arguments)
                                except Exception:
                                    arguments = "{}"
                            converted.append(
                                {
                                    "type": "function_call",
                                    "call_id": str(call_id),
                                    "name": str(name),
                                    "arguments": arguments,
                                }
                            )
                            emitted_call = True
                    if emitted_call:
                        continue

            # Chat-style tool result → Responses API function_call_output item.
            if role_lower == "tool":
                call_id = message.get("tool_call_id") or message.get("tool_use_id") or message.get("call_id")
                if call_id:
                    if isinstance(content, (dict, list)):
                        try:
                            output = json.dumps(content)
                        except Exception:
                            output = str(content)
                    else:
                        output = str(content) if content is not None else ""
                    converted.append(
                        {
                            "type": "function_call_output",
                            "call_id": str(call_id),
                            "output": output,
                        }
                    )
                    continue

            if self.descriptor.provider_id == "openrouter":
                # OpenRouter's Responses proxy currently expects simple chat-style message inputs
                # with `content` as a string (not Responses API content blocks).
                text_val = self._message_content_to_text(content)
                if text_val is None:
                    if isinstance(content, (dict, list)):
                        try:
                            text_val = json.dumps(content, ensure_ascii=False)
                        except Exception:
                            text_val = str(content)
                    else:
                        text_val = str(content) if content is not None else ""
                converted.append({"role": role, "content": text_val})
                continue

            # Convert content to Responses API format:
            # https://platform.openai.com/docs/api-reference/responses
            default_text_type = "output_text" if role_lower == "assistant" else "input_text"
            if isinstance(content, str):
                # Simple string → role-appropriate text block
                content_blocks: List[Dict[str, Any]] = [{"type": default_text_type, "text": content}]
            elif isinstance(content, list):
                # Already a list - normalize each element
                content_blocks = []
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type")
                        if block_type in [
                            "input_text",
                            "input_image",
                            "output_text",
                            "refusal",
                            "input_file",
                            "computer_screenshot",
                            "summary_text",
                        ]:
                            # Already in Responses API format; normalise text block type per role
                            if block_type in ("input_text", "output_text") and block_type != default_text_type:
                                new_block = dict(block)
                                new_block["type"] = default_text_type
                                content_blocks.append(new_block)
                            else:
                                content_blocks.append(block)
                        elif block_type == "text" or "text" in block or "content" in block:
                            # Chat-style text block → input_text
                            text_val = block.get("text") or block.get("content", "")
                            content_blocks.append({"type": default_text_type, "text": str(text_val)})
                        else:
                            # Unknown format - preserve as best-effort
                            content_blocks.append(block)
                    else:
                        # Plain string or other scalar in list → wrap as input_text
                        content_blocks.append({"type": default_text_type, "text": str(block)})
            else:
                # Fallback: stringify any other content into a single input_text block
                text_val = ""
                if content is not None:
                    try:
                        text_val = str(content)
                    except Exception:
                        text_val = ""
                content_blocks = [{"type": default_text_type, "text": text_val}]

            converted.append({"role": role, "content": content_blocks})
        return converted

    def _convert_tools_to_responses(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None
        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                fn = tool.get("function", {}) or {}
                strict_flag = fn.get("strict")
                if strict_flag is None:
                    strict_flag = tool.get("strict")
                converted.append(
                    {
                        "type": "function",
                        "name": fn.get("name"),
                        "description": fn.get("description"),
                        "parameters": fn.get("parameters"),
                    }
                )
                if strict_flag is not None:
                    converted[-1]["strict"] = strict_flag
            else:
                converted.append(tool)
        return converted

    def _stream_responses(
        self,
        client: Any,
        payload: Dict[str, Any],
        context: ProviderRuntimeContext,
    ) -> Any:
        request_id = provider_dump_logger.log_request(
            provider=self.descriptor.provider_id,
            model=payload.get("model"),
            payload=payload,
            context=context,
        )

        try:
            stream_ctx = client.responses.stream(**payload)
        except Exception as exc:  # pragma: no cover - wrapped as runtime error
            raise ProviderRuntimeError(str(exc)) from exc

        try:
            with stream_ctx as stream:
                for _ in stream:
                    pass
                final_response = stream.get_final_response()
                if request_id:
                    try:
                        serialized = json.dumps(final_response, default=str)
                    except Exception:
                        serialized = str(final_response)
                    provider_dump_logger.log_response(
                        provider=self.descriptor.provider_id,
                        model=payload.get("model"),
                        request_id=request_id,
                        status_code=None,
                        headers=None,
                        content_type=None,
                        body_text=serialized,
                        body_base64=None,
                        context=context,
                        metadata={"stream": True},
                    )
                return final_response
        except Exception as exc:  # pragma: no cover - wrapped as runtime error
            raise ProviderRuntimeError(str(exc)) from exc

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        instructions, input_messages = self._split_messages_for_responses(messages, context)
        provider_tools_cfg = context.agent_config.get("provider_tools") or {}
        provider_cfg: Dict[str, Any] = {}
        if isinstance(provider_tools_cfg, dict):
            provider_specific = provider_tools_cfg.get(self.descriptor.provider_id)
            if isinstance(provider_specific, dict):
                provider_cfg = provider_specific
            else:
                openai_specific = provider_tools_cfg.get("openai")
                if isinstance(openai_specific, dict) and self.descriptor.provider_id in ("openai", "openrouter"):
                    provider_cfg = openai_specific
                else:
                    provider_cfg = provider_tools_cfg
        responses_stateful = bool(provider_cfg.get("responses_stateful", True))
        include_tool_calls = (self.descriptor.provider_id == "openrouter") or (not responses_stateful)
        payload: Dict[str, Any] = {
            "model": model,
            "input": self._convert_messages_to_input(input_messages, include_tool_calls=include_tool_calls),
        }
        if instructions:
            payload["instructions"] = instructions

        responses_tools = self._convert_tools_to_responses(tools)
        if responses_tools:
            payload["tools"] = responses_tools
        if self.descriptor.provider_id == "openrouter" and isinstance(model, str) and model.startswith("openai/gpt-5"):
            extra_body = dict(payload.get("extra_body") or {})
            extra_body.setdefault("provider", {"order": ["openai"], "allow_fallbacks": False})
            payload["extra_body"] = extra_body

        # provider_cfg already resolved above

        include_items: List[str] = list(provider_cfg.get("include", []))
        if provider_cfg.get("include_reasoning", True) and "reasoning.encrypted_content" not in include_items:
            include_items.append("reasoning.encrypted_content")
        if include_items:
            payload["include"] = include_items

        if "store" in provider_cfg:
            payload["store"] = bool(provider_cfg.get("store"))

        tool_choice_cfg = provider_cfg.get("tool_choice")
        if tool_choice_cfg is not None and responses_tools:
            resolved_choice: Any = tool_choice_cfg
            if isinstance(tool_choice_cfg, str):
                lowered = tool_choice_cfg.strip().lower()
                if lowered in {"auto"}:
                    resolved_choice = "auto"
                elif lowered in {"required", "force", "any"}:
                    resolved_choice = "required"
                elif lowered in {"none", "off"}:
                    resolved_choice = "none"
            if resolved_choice is not None:
                payload["tool_choice"] = resolved_choice

        responses_stateful = bool(provider_cfg.get("responses_stateful", True))
        if responses_stateful:
            conversation_id = context.session_state.get_provider_metadata("conversation_id")
            if conversation_id:
                payload["conversation"] = conversation_id

            previous_response_id = context.session_state.get_provider_metadata("previous_response_id")
            if previous_response_id:
                payload["previous_response_id"] = previous_response_id

        extra_payload = context.extra.get("responses_extra") if context.extra else None
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)

        response: Any = None
        if stream:
            response = self._stream_responses(client, payload, context)

        if response is None:
            try:
                response = self._call_with_raw_response(
                    client.responses,
                    error_context="responses.create",
                    context=context,
                    **payload,
                )
            except ProviderRuntimeError:
                raise
            except Exception as exc:  # pragma: no cover - exercised in integration
                raise ProviderRuntimeError(str(exc)) from exc

        normalized_messages: List[ProviderMessage] = []
        encrypted_reasoning: List[Any] = []
        reasoning_summaries: List[str] = []

        response_status = self._get_attr(response, "status", None)
        default_finish_reason = None
        if isinstance(response_status, str) and response_status.lower() in {"completed", "succeeded", "complete"}:
            default_finish_reason = "stop"

        for idx, item in enumerate(getattr(response, "output", []) or []):
            item_type = self._get_attr(item, "type")

            if item_type == "message":
                role = self._get_attr(item, "role", "assistant")
                content = self._message_content_to_text(self._get_attr(item, "content", []))
                finish_reason = self._get_attr(item, "finish_reason", None)
                if finish_reason is None and content and default_finish_reason:
                    finish_reason = default_finish_reason
                normalized_messages.append(
                    ProviderMessage(
                        role=role,
                        content=content,
                        finish_reason=finish_reason,
                        index=idx,
                        raw_message=item,
                        annotations={"responses_type": item_type},
                    )
                )
            elif item_type == "function_call":
                call_id = self._get_attr(item, "call_id")
                name = self._get_attr(item, "name")
                arguments = self._get_attr(item, "arguments", "{}")
                if not isinstance(arguments, str):
                    try:
                        arguments = json.dumps(arguments)
                    except Exception:
                        arguments = "{}"
                tool_call = ProviderToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                    type="function",
                    raw=item,
                )
                normalized_messages.append(
                    ProviderMessage(
                        role="assistant",
                        content=None,
                        tool_calls=[tool_call],
                        finish_reason=self._get_attr(item, "finish_reason", None),
                        index=idx,
                        raw_message=item,
                        annotations={"responses_type": item_type},
                    )
                )
            elif item_type == "reasoning":
                encrypted = self._get_attr(item, "encrypted_content")
                if encrypted is not None:
                    encrypted_reasoning.append(
                        {
                            "encrypted_content": encrypted,
                            "metadata": {
                                "response_id": getattr(response, "id", None),
                                "type": item_type,
                            },
                        }
                    )
                summary_blocks = self._get_attr(item, "summary", []) or []
                summary_text = self._message_content_to_text(summary_blocks)
                if summary_text:
                    reasoning_summaries.append(summary_text)

        usage_dict = self._extract_usage(response)

        metadata: Dict[str, Any] = {}
        response_id = getattr(response, "id", None)
        if response_id:
            metadata["previous_response_id"] = response_id
        conversation_obj = getattr(response, "conversation", None)
        conversation_id_out = getattr(conversation_obj, "id", None) if conversation_obj else None
        if conversation_id_out:
            metadata["conversation_id"] = conversation_id_out

        return ProviderResult(
            messages=normalized_messages,
            raw_response=response,
            usage=usage_dict,
            encrypted_reasoning=encrypted_reasoning or None,
            reasoning_summaries=reasoning_summaries or None,
            model=getattr(response, "model", None),
            metadata=metadata,
        )


provider_registry.register_runtime("openai_responses", OpenAIResponsesRuntime)


# ---------------------------------------------------------------------------
# Anthropic Messages runtime
# ---------------------------------------------------------------------------


class AnthropicMessagesRuntime(ProviderRuntime):
    """Runtime for Anthropic Messages API."""

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        if Anthropic is None:
            raise ProviderRuntimeError("anthropic package not installed")

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if default_headers:
            kwargs["default_headers"] = default_headers
        return Anthropic(**kwargs)

    def _message_content_to_text(self, content: Any) -> Optional[str]:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        parts: List[str] = []
        try:
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type in {"input_text", "output_text", "text"}:
                    text_val = block.get("text", "")
                    if text_val:
                        parts.append(str(text_val))
        except Exception:
            return None
        return "".join(parts) if parts else None

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        system_prompt: Optional[str] = None
        converted: List[Dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content")

            if role == "system" and system_prompt is None:
                system_prompt = content if isinstance(content, str) else json.dumps(content)
                continue

            # Translate OpenAI-style tool calls into Anthropic `tool_use` blocks so
            # the model receives its own tool invocation history.
            tool_calls = message.get("tool_calls")
            if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
                blocks: List[Dict[str, Any]] = []
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text" and not block.get("text"):
                            continue
                        if block_type:
                            blocks.append(block)
                else:
                    text_value = content if isinstance(content, str) else ""
                    if text_value:
                        blocks.append({"type": "text", "text": text_value})

                for idx, tc in enumerate(tool_calls):
                    if not isinstance(tc, dict):
                        continue
                    call_id = tc.get("id") or tc.get("tool_use_id") or tc.get("tool_call_id") or f"toolu_{idx}"
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = fn.get("name") or tc.get("name")
                    args_raw = fn.get("arguments") or tc.get("arguments") or "{}"
                    try:
                        input_payload = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    except Exception:
                        input_payload = {}
                    if not name:
                        continue
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call_id),
                            "name": str(name),
                            "input": input_payload if isinstance(input_payload, dict) else {},
                        }
                    )

                converted.append({"role": "assistant", "content": blocks})
                continue

            # Tool results must be provided as `tool_result` blocks in a user message
            # immediately after the assistant's `tool_use` block.
            if role == "tool":
                tool_use_id = (
                    message.get("tool_use_id")
                    or message.get("tool_call_id")
                    or message.get("call_id")
                    or message.get("id")
                )
                text_value = self._message_content_to_text(content)
                if not tool_use_id:
                    # Best-effort fallback: preserve output as user text if we can't associate it.
                    if text_value:
                        converted.append({"role": "user", "content": [{"type": "text", "text": text_value}]})
                    continue
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": str(tool_use_id),
                                "content": text_value or "",
                            }
                        ],
                    }
                )
                continue

            if isinstance(content, list):
                blocks: List[Dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text" and not block.get("text"):
                        continue
                    if block_type:
                        blocks.append(block)
            else:
                text_value = content if isinstance(content, str) else ""
                blocks = [{"type": "text", "text": text_value}] if text_value else []

            if not blocks:
                continue

            converted.append({
                "role": role,
                "content": blocks,
            })

        return system_prompt, converted

    def _extract_usage(self, response: Any) -> Optional[Dict[str, Any]]:
        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return None
        try:
            return dict(usage_obj)
        except Exception:
            try:
                return usage_obj.model_dump()  # type: ignore[attr-defined]
            except Exception:
                return None

    def _get_attr(self, obj: Any, name: str, default: Any = None) -> Any:
        if hasattr(obj, name):
            return getattr(obj, name)
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default

    def _normalize_response(
        self,
        response: Any,
        *,
        usage_override: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        text_parts: List[str] = []
        tool_calls: List[ProviderToolCall] = []
        reasoning_summaries: List[str] = []

        for block in getattr(response, "content", []) or []:
            block_type = self._get_attr(block, "type")
            if block_type == "text":
                text_val = self._get_attr(block, "text", "")
                if text_val:
                    text_parts.append(str(text_val))
            elif block_type == "tool_use":
                call_id = self._get_attr(block, "id")
                name = self._get_attr(block, "name")
                input_payload = self._get_attr(block, "input", {})
                try:
                    arguments = json.dumps(input_payload)
                except Exception:
                    arguments = "{}"
                tool_calls.append(
                    ProviderToolCall(
                        id=call_id,
                        name=name,
                        arguments=arguments,
                        type="function",
                        raw=block,
                    )
                )
            elif block_type == "thinking":
                thinking_text = self._get_attr(block, "text", "")
                if thinking_text:
                    reasoning_summaries.append(str(thinking_text))

        content_text = "".join(text_parts) if text_parts else None
        provider_message = ProviderMessage(
            role="assistant",
            content=content_text,
            tool_calls=tool_calls,
            finish_reason=getattr(response, "stop_reason", None),
            index=0,
            raw_message=response,
            annotations={"anthropic_stop_reason": getattr(response, "stop_reason", None)},
        )

        usage_dict = usage_override if usage_override is not None else self._extract_usage(response)
        metadata: Dict[str, Any] = {}
        if usage_dict:
            for key in [
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "input_tokens",
                "output_tokens",
            ]:
                if key in usage_dict:
                    metadata.setdefault("usage", {})[key] = usage_dict[key]

        return ProviderResult(
            messages=[provider_message],
            raw_response=response,
            usage=usage_dict,
            reasoning_summaries=reasoning_summaries or None,
            model=getattr(response, "model", None),
            metadata=metadata,
        )

    _RATE_LIMIT_HEADER_MAP = {
        "anthropic-ratelimit-tokens-limit": "tokens_limit",
        "anthropic-ratelimit-tokens-remaining": "tokens_remaining",
        "anthropic-ratelimit-tokens-reset": "tokens_reset",
        "anthropic-ratelimit-requests-limit": "requests_limit",
        "anthropic-ratelimit-requests-remaining": "requests_remaining",
        "anthropic-ratelimit-requests-reset": "requests_reset",
    }

    def _normalize_headers(self, headers: Any) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        if not headers:
            return normalized
        try:
            items = headers.items()
        except AttributeError:
            items = getattr(headers, "raw", [])  # type: ignore[assignment]
        for key, value in items:
            try:
                normalized[str(key).lower()] = str(value)
            except Exception:
                continue
        return normalized

    def _safe_http_text(self, response_obj: Any) -> Optional[str]:
        if response_obj is None:
            return None
        try:
            return response_obj.text
        except Exception:
            try:
                content = getattr(response_obj, "content", None)
                if content is None:
                    return None
                if isinstance(content, (bytes, bytearray)):
                    return bytes(content).decode("utf-8", "ignore")
                return str(content)
            except Exception:
                return None

    def _parse_reset_header(self, value: str) -> Optional[float]:
        if not value:
            return None
        try:
            return float(value)
        except Exception:
            pass
        try:
            clean = value.rstrip("Z")
            dt = datetime.datetime.fromisoformat(clean + ("+00:00" if "T" in clean and "+" not in clean else ""))
            return dt.timestamp()
        except Exception:
            return None

    def _capture_rate_limit_headers(self, context: ProviderRuntimeContext, headers: Dict[str, str]) -> None:
        if not headers:
            return
        session_state = getattr(context, "session_state", None)
        if not session_state:
            return
        snapshot: Dict[str, Any] = {}
        for header, key in self._RATE_LIMIT_HEADER_MAP.items():
            value = headers.get(header)
            if value is None:
                continue
            if header.endswith("reset"):
                epoch = self._parse_reset_header(value)
                if epoch is not None:
                    snapshot[f"{key}_epoch"] = epoch
                snapshot[key] = value
            else:
                try:
                    snapshot[key] = float(value) if "." in value else int(value)
                except Exception:
                    snapshot[key] = value
        if not snapshot:
            return
        snapshot["captured_at"] = time.time()
        session_state.set_provider_metadata("anthropic_rate_limits", snapshot)

        # Also emit a normalized limits_update event for the CLI bridge stream (best-effort).
        try:
            from .limits.parse_headers import parse_rate_limit_headers

            provider_id = getattr(getattr(self, "descriptor", None), "provider_id", None) or "anthropic"
            parsed = parse_rate_limit_headers(headers, provider=str(provider_id))
            if parsed:
                emit = getattr(session_state, "_emit_event", None)
                if callable(emit):
                    emit("limits_update", parsed, turn=getattr(session_state, "_active_turn_index", None))
        except Exception:
            pass

    def _is_overloaded_error(self, exc: Exception) -> bool:
        if AnthropicOverloadedError is not None and isinstance(exc, AnthropicOverloadedError):
            return True
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            try:
                if int(status_code) == 529:
                    return True
            except Exception:
                pass
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_obj = body.get("error") or {}
            error_type = error_obj.get("type") or body.get("type")
            if isinstance(error_type, str) and "overload" in error_type.lower():
                return True
        message = getattr(exc, "message", None) or str(exc)
        return isinstance(message, str) and "overload" in message.lower()

    def _maybe_delay_for_rate_limits(self, context: ProviderRuntimeContext, anthropic_cfg: Dict[str, Any]) -> None:
        limiter_cfg = (anthropic_cfg.get("rate_limit") or {}) if isinstance(anthropic_cfg, dict) else {}
        if not limiter_cfg.get("enabled"):
            return
        session_state = getattr(context, "session_state", None)
        if not session_state:
            return
        snapshot = session_state.get_provider_metadata("anthropic_rate_limits")
        if not isinstance(snapshot, dict) or not snapshot:
            return
        tokens_remaining = snapshot.get("tokens_remaining")
        if tokens_remaining is None:
            return
        try:
            tokens_remaining = float(tokens_remaining)
        except Exception:
            return
        buffer_tokens = limiter_cfg.get("token_buffer")
        try:
            buffer_tokens = float(buffer_tokens)
        except Exception:
            buffer_tokens = None
        if buffer_tokens is None or tokens_remaining > buffer_tokens:
            return
        wait_seconds = 0.0
        reset_epoch = snapshot.get("tokens_reset_epoch")
        if isinstance(reset_epoch, (int, float)):
            wait_seconds = max(0.0, float(reset_epoch) - time.time())
        fallback = limiter_cfg.get("fallback_cooldown_seconds")
        if wait_seconds <= 0.0 and fallback:
            try:
                wait_seconds = max(wait_seconds, float(fallback))
            except Exception:
                pass
        min_wait = limiter_cfg.get("min_wait_seconds")
        try:
            min_wait = float(min_wait)
        except Exception:
            min_wait = 0.0
        wait_seconds = max(wait_seconds, min_wait or 0.0)
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _compute_rate_limit_retry_delay(
        self,
        limiter_cfg: Dict[str, Any],
        attempt: int,
        retry_after_value: Optional[str],
    ) -> float:
        if retry_after_value:
            try:
                return max(0.0, float(retry_after_value))
            except Exception:
                reset_epoch = self._parse_reset_header(retry_after_value)
                if reset_epoch is not None:
                    return max(0.0, reset_epoch - time.time())
        base = 1.5
        if limiter_cfg.get("retry_base_seconds"):
            try:
                base = float(limiter_cfg["retry_base_seconds"])
            except Exception:
                pass
        max_delay = limiter_cfg.get("retry_max_seconds")
        try:
            max_delay = float(max_delay)
        except Exception:
            max_delay = None
        delay = base * (2 ** attempt)
        jitter = limiter_cfg.get("retry_jitter_seconds")
        try:
            jitter = float(jitter)
        except Exception:
            jitter = 0.0
        if jitter and jitter > 0:
            delay += random.uniform(0, jitter)
        if max_delay is not None:
            delay = min(delay, max_delay)
        return max(delay, 0.0)

    def _resolve_tool_choice(self, configured: Any, tools: Optional[List[Dict[str, Any]]]) -> Optional[Any]:
        if not tools or not configured:
            return None
        if isinstance(configured, str):
            lowered = configured.lower()
            if lowered in {"auto", "any"}:
                return {"type": lowered}
            if lowered in {"required", "force"}:
                return {"type": "any"}
            if lowered in {"none", "off"}:
                return None
        return configured

    def _filter_anthropic_tools(
        self,
        tools: Optional[List[Dict[str, Any]]],
        context: ProviderRuntimeContext,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Anthropic rejects tool names with dots or other invalid characters.
        Drop dotted todo* tools when todos are disabled, and strip any tool whose
        name fails the provider regex ^[a-zA-Z0-9_-]{1,128}$.
        """
        if not tools:
            return tools

        agent_cfg = context.agent_config or {}
        features_cfg = agent_cfg.get("features") or {}
        todos_cfg = features_cfg.get("todos") if isinstance(features_cfg, dict) else {}
        allow_todos = True
        try:
            if isinstance(todos_cfg, dict):
                allow_todos = bool(todos_cfg.get("enabled", True))
            else:
                allow_todos = bool(todos_cfg)
        except Exception:
            allow_todos = True

        filtered: List[Dict[str, Any]] = []
        dropped: List[str] = []
        for tool in tools:
            name = None
            try:
                name = tool.get("name")
            except Exception:
                name = None
            if not name or not isinstance(name, str):
                continue

            lowered = name.lower()
            if not allow_todos and lowered.startswith("todo"):
                dropped.append(name)
                continue

            if not re.match(r"^[A-Za-z0-9_-]{1,128}$", name):
                dropped.append(name)
                continue

            filtered.append(tool)

        session_state = getattr(context, "session_state", None)
        if session_state is not None and dropped:
            try:
                session_state.set_provider_metadata("anthropic_tools_dropped", dropped)
            except Exception:
                pass

        return filtered or None

    def _build_system_prompt(self, system_prompt: str, prompt_cache_cfg: Dict[str, Any]) -> Any:
        apply_cache = bool(prompt_cache_cfg.get("apply_to_system", True))
        cache_control = prompt_cache_cfg.get("cache_control")
        if system_prompt and apply_cache and isinstance(cache_control, dict):
            block = {"type": "text", "text": system_prompt, "cache_control": cache_control}
            return [block]
        return system_prompt

    def _call_streaming(
        self,
        client: Any,
        request: Dict[str, Any],
    ) -> Tuple[Any, Optional[Dict[str, Any]], Dict[str, str], Optional[int], Optional[str]]:
        stream_ctx = client.messages.stream(**request)
        usage_override: Optional[Dict[str, Any]] = None
        response_obj: Any = None
        with stream_ctx as stream_obj:
            for _ in stream_obj:
                pass
            response = stream_obj.get_final_message()
            response_obj = getattr(stream_obj, "response", None)
            final_usage = getattr(stream_obj, "get_final_usage", None)
            if callable(final_usage):
                try:
                    usage_override = self._extract_usage(SimpleNamespace(usage=final_usage()))
                except Exception:
                    usage_override = None
        headers = self._normalize_headers(getattr(response_obj, "headers", {}) or {})
        status_code = getattr(response_obj, "status_code", None)
        return response, usage_override, headers, status_code, None

    def _call_non_streaming(
        self,
        client: Any,
        request: Dict[str, Any],
    ) -> Tuple[Any, Optional[Dict[str, Any]], Dict[str, str], Optional[int], Optional[str]]:
        raw_response = client.messages.with_raw_response.create(**request)
        parsed = raw_response.parse()
        http_response = getattr(raw_response, "http_response", None)
        headers = self._normalize_headers(getattr(http_response, "headers", {}) or {})
        status_code = getattr(http_response, "status_code", None)
        body_text = self._safe_http_text(http_response)
        return parsed, None, headers, status_code, body_text

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        tools = self._filter_anthropic_tools(tools, context)
        system_prompt, converted_messages = self._convert_messages(messages)

        anthropic_cfg = (context.agent_config.get("provider_tools") or {}).get("anthropic", {})
        max_tokens = anthropic_cfg.get("max_output_tokens", 1024)
        temperature = anthropic_cfg.get("temperature")
        prompt_cache_cfg = (anthropic_cfg.get("prompt_cache") or {}) if isinstance(anthropic_cfg, dict) else {}
        extra_headers: Dict[str, str] = {}
        try:
            extra_headers.update(anthropic_cfg.get("extra_headers") or {})
        except Exception:
            extra_headers = {}
        beta_header = prompt_cache_cfg.get("beta_header")
        if beta_header:
            extra_headers.setdefault("anthropic-beta", beta_header)

        delay_seconds = 0.0
        try:
            turn_delay = anthropic_cfg.get("turn_delay_seconds")
            if turn_delay:
                delay_seconds = max(0.0, float(turn_delay))
        except Exception:
            delay_seconds = 0.0

        request: Dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": int(max_tokens) if max_tokens else 1024,
        }

        if system_prompt:
            request["system"] = self._build_system_prompt(system_prompt, prompt_cache_cfg)

        if tools:
            request["tools"] = tools

        resolved_tool_choice = self._resolve_tool_choice(anthropic_cfg.get("tool_choice"), tools)
        if resolved_tool_choice is not None:
            request["tool_choice"] = resolved_tool_choice

        if extra_headers:
            request["extra_headers"] = extra_headers

        if temperature is not None:
            request["temperature"] = float(temperature)

        response_metadata: Dict[str, Any] = {"stream": bool(stream)}
        if resolved_tool_choice:
            response_metadata["tool_choice"] = resolved_tool_choice

        request_id = provider_dump_logger.log_request(
            provider=self.descriptor.provider_id,
            model=model,
            payload=request,
            context=context,
            metadata=response_metadata,
        )

        session_state = getattr(context, "session_state", None)
        if session_state and tools:
            try:
                session_state.set_provider_metadata(
                    "anthropic_active_tools",
                    [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")],
                )
            except Exception:
                pass
            if resolved_tool_choice is not None:
                session_state.set_provider_metadata("anthropic_tool_choice", resolved_tool_choice)

        rate_limit_cfg = (anthropic_cfg.get("rate_limit") or {}) if isinstance(anthropic_cfg, dict) else {}
        max_retries = 0
        try:
            max_retries = int(rate_limit_cfg.get("max_retries") or 0)
        except Exception:
            max_retries = 0

        def _respect_delay() -> None:
            if delay_seconds > 0.0:
                time.sleep(delay_seconds)

        attempt = 0
        while True:
            self._maybe_delay_for_rate_limits(context, anthropic_cfg)
            _respect_delay()
            try:
                if stream:
                    response, usage_override, headers, status_code, body_text = self._call_streaming(client, request)
                else:
                    response, usage_override, headers, status_code, body_text = self._call_non_streaming(client, request)

                metadata = {**response_metadata, "attempts": attempt + 1}
                self._capture_rate_limit_headers(context, headers)
                provider_dump_logger.log_response(
                    provider=self.descriptor.provider_id,
                    model=model,
                    request_id=request_id,
                    status_code=status_code,
                    headers=headers or None,
                    content_type=(headers or {}).get("content-type"),
                    body_text=body_text,
                    body_base64=None,
                    context=context,
                    metadata=metadata,
                )
                return self._normalize_response(response, usage_override=usage_override)
            except Exception as exc:
                is_rate_limit = AnthropicRateLimitError is not None and isinstance(exc, AnthropicRateLimitError)
                is_overloaded = False if is_rate_limit else self._is_overloaded_error(exc)
                headers: Dict[str, str] = {}
                status_code = None
                body_text = None
                if is_rate_limit or is_overloaded:
                    response_obj = getattr(exc, "response", None)
                    headers = self._normalize_headers(getattr(response_obj, "headers", {}) or {})
                    status_code = getattr(exc, "status_code", None)
                    body_text = self._safe_http_text(response_obj) or str(exc)
                    if is_rate_limit:
                        self._capture_rate_limit_headers(context, headers)
                    metadata = {
                        **response_metadata,
                        "attempts": attempt + 1,
                        "error": True,
                    }
                    if is_rate_limit:
                        metadata["rate_limited"] = True
                    if is_overloaded:
                        metadata["overloaded"] = True
                    if attempt >= max_retries:
                        provider_dump_logger.log_response(
                            provider=self.descriptor.provider_id,
                            model=model,
                            request_id=request_id,
                            status_code=status_code,
                            headers=headers or None,
                            content_type=(headers or {}).get("content-type"),
                            body_text=body_text or str(exc),
                            body_base64=None,
                            context=context,
                            metadata=metadata,
                        )
                        raise ProviderRuntimeError(str(exc)) from exc
                    retry_after_value = headers.get("retry-after")
                    wait_seconds = self._compute_rate_limit_retry_delay(rate_limit_cfg, attempt, retry_after_value)
                    fallback_cooldown = rate_limit_cfg.get("fallback_cooldown_seconds")
                    if wait_seconds <= 0 and fallback_cooldown:
                        try:
                            wait_seconds = max(wait_seconds, float(fallback_cooldown))
                        except Exception:
                            pass
                    try:
                        min_wait = float(rate_limit_cfg.get("min_wait_seconds") or 0.0)
                    except Exception:
                        min_wait = 0.0
                    wait_seconds = max(wait_seconds, min_wait)
                    if session_state:
                        try:
                            session_state.set_provider_metadata(
                                "anthropic_last_rate_limit" if is_rate_limit else "anthropic_last_overload",
                                {
                                    "attempt": attempt + 1,
                                    "retry_after": retry_after_value,
                                    "wait_seconds": wait_seconds,
                                    "status_code": status_code,
                                },
                            )
                        except Exception:
                            pass
                    attempt += 1
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
                    continue
                metadata = {**response_metadata, "attempts": attempt + 1, "error": True}
                provider_dump_logger.log_response(
                    provider=self.descriptor.provider_id,
                    model=model,
                    request_id=request_id,
                    status_code=status_code,
                    headers=None,
                    content_type=None,
                    body_text=str(exc),
                    body_base64=None,
                    context=context,
                    metadata=metadata,
                )
                raise ProviderRuntimeError(str(exc)) from exc


provider_registry.register_runtime("anthropic_messages", AnthropicMessagesRuntime)


# ---------------------------------------------------------------------------
# Mock runtime (offline validation)
# ---------------------------------------------------------------------------


class MockRuntime(ProviderRuntime):
    """A simple mock provider runtime for offline validation.

    Heuristics:
      - If no prior tool calls, request list_dir(path=".", depth=1)
      - Else if only one prior tool call, request apply_unified_patch with a minimal project skeleton
      - Else, return a short assistant message
    """

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"mock": True}

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        model_hint = (model or "").strip().lower()
        if model_hint in {"no_tools", "no_tool", "zero_tool", "no_tool_activity"}:
            out_messages = [
                ProviderMessage(
                    role="assistant",
                    content="...",
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            ]
            return ProviderResult(
                messages=out_messages,
                raw_response={"mock": True, "mode": model_hint},
                usage=None,
                encrypted_reasoning=None,
                reasoning_summaries=None,
                model="mock",
            )
        # Count prior tool calls in assistant messages
        prior_calls = 0
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                prior_calls += len(tool_calls)

        def _seen_tool(name: str) -> bool:
            for msg in messages:
                if msg.get("role") != "assistant":
                    continue
                for call in msg.get("tool_calls") or []:
                    fn = getattr(getattr(call, "function", None), "name", "") or call.get("function")
                    if fn == name:
                        return True
            return False

        has_todo = _seen_tool("todo.write_board")
        has_write = _seen_tool("write")
        has_shell = _seen_tool("run_shell")

        def _mk_tool_call(name: str, args: Dict[str, Any]) -> ProviderToolCall:
            try:
                arg_str = json.dumps(args)
            except Exception:
                arg_str = "{}"
            ptc = ProviderToolCall(id=None, name=name, arguments=arg_str, type="function")
            try:
                from types import SimpleNamespace as _SNS
                setattr(ptc, "function", _SNS(name=name, arguments=arg_str))
            except Exception:
                pass
            return ptc

        out_messages: List[ProviderMessage] = []

        if prior_calls == 0:
            # Explore workspace
            tc = _mk_tool_call("list_dir", {"path": ".", "depth": 1})
            out_messages.append(ProviderMessage(role="assistant", content=None, tool_calls=[tc], finish_reason="stop", index=0))
        elif prior_calls == 1:
            # Emit a minimal unified diff patch adding Makefile and skeleton files
            unified = textwrap.dedent(
                """
                diff --git a/Makefile b/Makefile
                new file mode 100644
                index 0000000..c3f9c3b
                --- /dev/null
                +++ b/Makefile
                @@ -0,0 +1,7 @@
                +CC=gcc
                +CFLAGS=-Wall -Wextra -Werror
                +all: test
                +test: protofilesystem.o test_filesystem.o
                +\t$(CC) $(CFLAGS) -o test_fs protofilesystem.o test_filesystem.o
                +clean:
                +\trm -f *.o test_fs

                diff --git a/protofilesystem.h b/protofilesystem.h
                new file mode 100644
                index 0000000..1f1264a
                --- /dev/null
                +++ b/protofilesystem.h
                @@ -0,0 +1,6 @@
                +#ifndef PROTOFILESYSTEM_H
                +#define PROTOFILESYSTEM_H
                +
                +int fs_init(void);
                +
                +#endif

                diff --git a/protofilesystem.c b/protofilesystem.c
                new file mode 100644
                index 0000000..4d6c0be
                --- /dev/null
                +++ b/protofilesystem.c
                @@ -0,0 +1,5 @@
                +#include \"protofilesystem.h\"
                +
                +int fs_init(void) {
                +    return 0;
                +}

                diff --git a/test_filesystem.c b/test_filesystem.c
                new file mode 100644
                index 0000000..a3bb0bc
                --- /dev/null
                +++ b/test_filesystem.c
                @@ -0,0 +1,11 @@
                +#include <stdio.h>
                +#include \"protofilesystem.h\"
                +
                +int main(void) {
                +    if (fs_init() != 0) {
                +        fprintf(stderr, \"fs_init failed\\n\");
                +        return 1;
                +    }
                +    printf(\"OK\\n\");
                +    return 0;
                +}
                """
            ).lstrip("\n")
            if not unified.endswith("\n"):
                unified += "\n"
            tc = _mk_tool_call("apply_unified_patch", {"patch": unified})
            out_messages.append(ProviderMessage(role="assistant", content=None, tool_calls=[tc], finish_reason="stop", index=0))
        else:
            out_messages.append(ProviderMessage(role="assistant", content="Proceed to build and test.", tool_calls=[], finish_reason="stop", index=0))

        return ProviderResult(messages=out_messages, raw_response={"mock": True}, usage=None, encrypted_reasoning=None, reasoning_summaries=None, model="mock")


provider_registry.register_runtime("mock_chat", MockRuntime)


class SmokeRuntime(ProviderRuntime):
    """Deterministic single-turn runtime for CI/smoke checks.

    Emits a completion sentinel immediately (no tool calls) so headless runs
    can validate the full request/stream/shutdown path without spending tokens.
    """

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"smoke": True}

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        out_messages = [
            ProviderMessage(
                role="assistant",
                content="Hi! All systems nominal.\n\nTASK COMPLETE\n\n>>>>>> END RESPONSE",
                tool_calls=[],
                finish_reason="stop",
                index=0,
            )
        ]
        return ProviderResult(
            messages=out_messages,
            raw_response={"smoke": True},
            usage=None,
            encrypted_reasoning=None,
            reasoning_summaries=None,
            model="smoke",
        )


provider_registry.register_runtime("smoke_chat", SmokeRuntime)


class CliMockRuntime(ProviderRuntime):
    """Deterministic runtime for CLI guardrail fixtures."""

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"cli_mock": True}

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        prior_calls = 0
        has_todo = False
        has_write = False
        has_shell = False
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            prior_calls += len(tool_calls)
            for call in tool_calls:
                name = None
                if isinstance(call, dict):
                    fn_block = call.get("function")
                    if isinstance(fn_block, dict):
                        name = fn_block.get("name")
                    name = name or call.get("name")
                if not name:
                    continue
                if name.startswith("todo."):
                    has_todo = True
                elif name.startswith("write") or name == "create_file_from_block":
                    has_write = True
                elif name.startswith("run_shell") or name == "bash.run":
                    has_shell = True

        def _mk_tool_call(name: str, args: Dict[str, Any]) -> ProviderToolCall:
            payload = json.dumps(args)
            call = ProviderToolCall(id=None, name=name, arguments=payload, type="function")
            try:
                from types import SimpleNamespace as _SNS
                setattr(call, "function", _SNS(name=name, arguments=payload))
            except Exception:
                pass
            return call

        out_messages: List[ProviderMessage] = []

        if not has_todo:
            board = {
                "todos": [
                    {"content": "Plan work", "status": "completed"},
                    {"content": "Implement feature", "status": "in_progress"},
                    {"content": "Validate output", "status": "pending"},
                ]
            }
            call = _mk_tool_call("todo.write_board", board)
            out_messages.append(
                ProviderMessage(role="assistant", content=None, tool_calls=[call], finish_reason="stop", index=0)
            )
        elif not has_write:
            call = _mk_tool_call(
                "write",
                {
                    "path": "bubble_sort.py",
                    "content": "def bubble_sort(nums):\n    n = len(nums)\n    for i in range(n):\n        for j in range(0, n - i - 1):\n            if nums[j] > nums[j + 1]:\n                nums[j], nums[j + 1] = nums[j + 1], nums[j]\n    return nums\n\nif __name__ == '__main__':\n    data = [5, 3, 1, 4, 2]\n    print(bubble_sort(data))\n",
                },
            )
            out_messages.append(
                ProviderMessage(role="assistant", content=None, tool_calls=[call], finish_reason="stop", index=0)
            )
        elif not has_shell:
            call = _mk_tool_call("run_shell", {"command": "python bubble_sort.py", "timeout": 30})
            out_messages.append(
                ProviderMessage(role="assistant", content=None, tool_calls=[call], finish_reason="stop", index=0)
            )
        else:
            out_messages.append(
                ProviderMessage(
                    role="assistant",
                    content="TASK COMPLETE",
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            )

        raw_response = {
            "mock": True,
            "prior_tool_calls": prior_calls,
        }
        return ProviderResult(messages=out_messages, raw_response=raw_response, metadata={"status_code": 200})


provider_registry.register_runtime("cli_mock_chat", CliMockRuntime)
try:  # pragma: no cover - optional replay runtime
    from .provider_runtime_replay import ReplayRuntime

    provider_registry.register_runtime("replay", ReplayRuntime)
except Exception:
    pass
__all__ = [
    "ProviderRuntime",
    "ProviderRuntimeContext",
    "ProviderRuntimeError",
    "ProviderRuntimeRegistry",
    "ProviderResult",
    "ProviderMessage",
    "ProviderToolCall",
    "provider_registry",
    "OpenAIChatRuntime",
    "OpenAIResponsesRuntime",
    "AnthropicMessagesRuntime",
    "MockRuntime",
    "ReplayRuntime",
]
