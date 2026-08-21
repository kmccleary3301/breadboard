"""Anthropic Messages provider runtime."""

from __future__ import annotations

import datetime
import json
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ..contracts import ProviderMessage, ProviderResult, ProviderRuntime, ProviderRuntimeContext, ProviderRuntimeError, ProviderToolCall
from ...logging.provider_dump import provider_dump_logger
from ..registry import provider_registry
from ..sdk_bindings import provider_sdk_bindings


class AnthropicMessagesRuntime(ProviderRuntime):
    """Runtime for Anthropic Messages API."""

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        if provider_sdk_bindings.anthropic is None:
            raise ProviderRuntimeError("anthropic package not installed")

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if default_headers:
            kwargs["default_headers"] = default_headers
        return provider_sdk_bindings.anthropic(**kwargs)

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
        if provider_sdk_bindings.anthropic_overloaded_error is not None and isinstance(exc, provider_sdk_bindings.anthropic_overloaded_error):
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
            provider_sdk_bindings.sleep(wait_seconds)

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
            delay += provider_sdk_bindings.uniform(0, jitter)
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
            metadata={
                **response_metadata,
                "phase_label": (context.extra or {}).get("phase16_phase_label"),
                "turn_index": (context.extra or {}).get("turn_index"),
            },
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
                provider_sdk_bindings.sleep(delay_seconds)

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
                is_rate_limit = provider_sdk_bindings.anthropic_rate_limit_error is not None and isinstance(exc, provider_sdk_bindings.anthropic_rate_limit_error)
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
                        provider_sdk_bindings.sleep(wait_seconds)
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
