"""Runtime abstractions and concrete runtimes for model providers."""

from __future__ import annotations

import base64
import json
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
except ImportError:  # pragma: no cover - covered via error path tests
    Anthropic = None  # type: ignore[assignment]

from .provider_routing import ProviderDescriptor


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

    def _call_with_raw_response(self, collection: Any, *, error_context: str, **kwargs):
        """Call provider with raw response handling and short HTML retry.

        Some providers intermittently return HTML error pages. Detect these by
        attempting to parse and, on JSON decode failure with an HTML body
        snippet, retry a small number of times with brief backoff.
        """
        raw_callable = getattr(collection, "with_raw_response", None)
        if raw_callable is None:
            return collection.create(**kwargs)

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
        for attempt in range(max_retries + 1):
            raw = raw_callable.create(**kwargs)
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
                return raw.parse()
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
    ) -> Any:
        try:
            stream_ctx = client.chat.completions.stream(
                model=model,
                messages=messages,
                tools=tools,
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

        response: Any = None
        if stream:
            response = self._stream_chat_completion(
                client,
                model=model,
                messages=request_messages,
                tools=request_tools,
            )

        if response is None:
            try:
                response = self._call_with_raw_response(
                    client.chat.completions,
                    error_context="chat.completions.create",
                    model=model,
                    messages=request_messages,
                    tools=request_tools,
                    stream=False,
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

    def _convert_messages_to_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if isinstance(content, list):
                converted.append({"role": role, "content": content})
            else:
                converted.append({"role": role, "content": content})
        return converted

    def _convert_tools_to_responses(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None
        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                fn = tool.get("function", {}) or {}
                converted.append(
                    {
                        "type": "function",
                        "name": fn.get("name"),
                        "description": fn.get("description"),
                        "parameters": fn.get("parameters"),
                    }
                )
            else:
                converted.append(tool)
        return converted

    def _stream_responses(
        self,
        client: Any,
        payload: Dict[str, Any],
    ) -> Any:
        try:
            stream_ctx = client.responses.stream(**payload)
        except Exception as exc:  # pragma: no cover - wrapped as runtime error
            raise ProviderRuntimeError(str(exc)) from exc

        try:
            with stream_ctx as stream:
                for _ in stream:
                    pass
                return stream.get_final_response()
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
        payload: Dict[str, Any] = {
            "model": model,
            "input": self._convert_messages_to_input(messages),
        }

        responses_tools = self._convert_tools_to_responses(tools)
        if responses_tools:
            payload["tools"] = responses_tools

        provider_cfg = (context.agent_config.get("provider_tools") or {}).get("openai", {})

        include_items: List[str] = list(provider_cfg.get("include", []))
        if provider_cfg.get("include_reasoning", True) and "reasoning.encrypted_content" not in include_items:
            include_items.append("reasoning.encrypted_content")
        if include_items:
            payload["include"] = include_items

        if "store" in provider_cfg:
            payload["store"] = bool(provider_cfg.get("store"))

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
            response = self._stream_responses(client, payload)

        if response is None:
            try:
                response = self._call_with_raw_response(
                    client.responses,
                    error_context="responses.create",
                    **payload,
                )
            except ProviderRuntimeError:
                raise
            except Exception as exc:  # pragma: no cover - exercised in integration
                raise ProviderRuntimeError(str(exc)) from exc

        normalized_messages: List[ProviderMessage] = []
        encrypted_reasoning: List[Any] = []
        reasoning_summaries: List[str] = []

        for idx, item in enumerate(getattr(response, "output", []) or []):
            item_type = self._get_attr(item, "type")

            if item_type == "message":
                role = self._get_attr(item, "role", "assistant")
                content = self._message_content_to_text(self._get_attr(item, "content", []))
                normalized_messages.append(
                    ProviderMessage(
                        role=role,
                        content=content,
                        finish_reason=self._get_attr(item, "finish_reason", None),
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

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        system_prompt: Optional[str] = None
        converted: List[Dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content")

            if role == "system" and system_prompt is None:
                system_prompt = content if isinstance(content, str) else json.dumps(content)
                continue

            if isinstance(content, list):
                blocks = content
            else:
                blocks = [{"type": "text", "text": content or ""}]

            tool_calls = message.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name") if isinstance(fn, dict) else None
                args_raw = fn.get("arguments") if isinstance(fn, dict) else None
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except Exception:
                    args = {}
                if name:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": name,
                        "input": args,
                    })

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
        system_prompt, converted_messages = self._convert_messages(messages)

        anthropic_cfg = (context.agent_config.get("provider_tools") or {}).get("anthropic", {})
        max_tokens = anthropic_cfg.get("max_output_tokens", 1024)
        temperature = anthropic_cfg.get("temperature")
        request: Dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": int(max_tokens) if max_tokens else 1024,
        }

        if system_prompt:
            request["system"] = system_prompt

        if tools:
            request["tools"] = tools

        if temperature is not None:
            request["temperature"] = float(temperature)

        response: Any = None
        usage_override: Optional[Dict[str, Any]] = None

        if stream:
            try:
                stream_ctx = client.messages.stream(**request)
            except Exception as exc:  # pragma: no cover - wrapped by caller
                raise ProviderRuntimeError(str(exc)) from exc

            try:
                with stream_ctx as stream_obj:
                    for _ in stream_obj:
                        pass
                    response = stream_obj.get_final_message()
                    final_usage = getattr(stream_obj, "get_final_usage", None)
                    if callable(final_usage):
                        try:
                            usage_override = self._extract_usage(
                                SimpleNamespace(usage=final_usage())
                            )
                        except Exception:
                            usage_override = None
            except Exception as exc:  # pragma: no cover - wrapped by caller
                raise ProviderRuntimeError(str(exc)) from exc

        if response is None:
            try:
                response = client.messages.create(**request)
            except json.JSONDecodeError as exc:
                snippet = getattr(exc, "doc", "")
                snippet = (snippet or "")[:400].strip()
                raise ProviderRuntimeError(
                    "Failed to decode provider response (non-JSON payload).",
                    details={"body_snippet": snippet},
                ) from exc
            except Exception as exc:  # pragma: no cover - exercised in integration
                raise ProviderRuntimeError(str(exc)) from exc

        return self._normalize_response(response, usage_override=usage_override)


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
        # Count prior tool calls in assistant messages
        prior_calls = 0
        for msg in messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
                prior_calls += len(msg.get("tool_calls") or [])

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
]
