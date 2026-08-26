"""Anthropic Messages provider runtime."""

from __future__ import annotations

import datetime
import re
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ..contracts import (
    ProviderContractError,
    ProviderMessage,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderToolCall,
    canonical_json,
)
from ..input_media import resolve_input_media
from ..model_role_options import anthropic_role_options
from ...logging.provider_dump import provider_dump_logger
from ...security import redaction
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

    def _convert_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        context: ProviderRuntimeContext | None = None,
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        system_parts: List[str] = []
        converted: List[Dict[str, Any]] = []

        def text_blocks(content: Any) -> List[Dict[str, Any]]:
            if isinstance(content, str):
                return [{"type": "text", "text": content}]
            if content is None:
                return []
            if not isinstance(content, list):
                raise ProviderContractError(
                    "Anthropic message content must be text or blocks"
                )
            blocks: List[Dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    raise ProviderContractError(
                        "Anthropic content blocks must be objects"
                    )
                block_type = block.get("type")
                if block_type in {"text", "input_text", "output_text"}:
                    text = block.get("text")
                    if not isinstance(text, str):
                        raise ProviderContractError(
                            "Anthropic text block requires text"
                        )
                    blocks.append({"type": "text", "text": text})
                elif block_type == "media":
                    media = resolve_input_media(block, context)
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media.mime,
                                "data": media.base64_data,
                            },
                        }
                    )
                elif block_type in {
                    "image",
                    "document",
                    "thinking",
                    "redacted_thinking",
                }:
                    blocks.append(dict(block))
                elif block_type in {"tool_call", "tool_result"}:
                    blocks.append(dict(block))
                elif block_type == "provider_replay":
                    raise ProviderContractError(
                        "Anthropic provider replay requires native replay conversion"
                    )
                else:
                    raise ProviderContractError(
                        f"unsupported Anthropic content block: {block_type!r}"
                    )
            return blocks

        def parse_tool_call(raw: Any) -> ProviderToolCall:
            if not isinstance(raw, dict):
                raise ProviderContractError("tool call must be an object")
            function = raw.get("function")
            function_data = function if isinstance(function, dict) else raw
            call_id = (
                raw.get("call_id")
                or raw.get("id")
                or raw.get("tool_use_id")
                or raw.get("tool_call_id")
            )
            name = function_data.get("name")
            if "arguments_json" in function_data:
                arguments = function_data.get("arguments_json")
            elif "arguments" in function_data:
                arguments = function_data.get("arguments")
            else:
                raise ProviderContractError("tool call requires arguments")
            if arguments is None:
                raise ProviderContractError("tool call requires arguments")
            call = ProviderToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                type=(
                    "function"
                    if raw.get("type") == "tool_call"
                    else raw.get("type", "function")
                ),
                raw=raw,
            )
            call.as_dict()
            if not isinstance(call.parsed_arguments, dict):
                raise ProviderContractError(
                    "Anthropic tool arguments must be an object"
                )
            return call

        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role in {"system", "developer"}:
                blocks = text_blocks(content)
                if any(block.get("type") != "text" for block in blocks):
                    raise ProviderContractError(
                        "Anthropic system content must be text"
                    )
                system_parts.extend(block["text"] for block in blocks)
                continue

            if role in {"tool", "tool_result"}:
                result_blocks: List[Dict[str, Any]] = []
                if isinstance(content, list):
                    for block in text_blocks(content):
                        if block.get("type") != "tool_result":
                            raise ProviderContractError(
                                "tool-result messages require tool_result blocks"
                            )
                        call_id = block.get("call_id")
                        result_content = block.get("content")
                        if not isinstance(call_id, str) or not call_id:
                            raise ProviderContractError(
                                "tool_result requires call_id"
                            )
                        if result_content is None:
                            raise ProviderContractError(
                                "tool_result requires content"
                            )
                        if "is_error" in block and not isinstance(
                            block["is_error"], bool
                        ):
                            raise ProviderContractError(
                                "tool_result is_error must be boolean"
                            )
                        result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": (
                                    result_content
                                    if isinstance(result_content, str)
                                    else canonical_json(result_content)
                                ),
                                "is_error": (
                                    block["is_error"]
                                    if "is_error" in block
                                    else False
                                ),
                            }
                        )
                elif role == "tool":
                    call_id = (
                        message.get("tool_use_id")
                        or message.get("tool_call_id")
                        or message.get("call_id")
                        or message.get("id")
                    )
                    if not isinstance(call_id, str) or not call_id:
                        raise ProviderContractError(
                            "tool message requires call_id"
                        )
                    result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": (
                                content
                                if isinstance(content, str)
                                else canonical_json(content)
                            ),
                        }
                    )
                else:
                    raise ProviderContractError(
                        "canonical tool_result messages require block content"
                    )
                if not result_blocks:
                    raise ProviderContractError(
                        "tool-result messages require content"
                    )
                converted.append({"role": "user", "content": result_blocks})
                continue

            if role not in {"user", "assistant"}:
                raise ProviderContractError(
                    f"unsupported Anthropic role: {role!r}"
                )
            blocks = text_blocks(content)
            if role == "assistant":
                raw_calls = message.get("tool_calls")
                if raw_calls is not None:
                    if not isinstance(raw_calls, list):
                        raise ProviderContractError("tool_calls must be a list")
                    blocks.extend(raw_calls)
                converted_blocks: List[Dict[str, Any]] = []
                seen_calls: Dict[str, Dict[str, Any]] = {}
                for block in blocks:
                    is_tool_call = (
                        block.get("type") in {"tool_call", "function"}
                        or isinstance(block.get("function"), dict)
                    )
                    if not is_tool_call:
                        if block.get("type") == "tool_result":
                            raise ProviderContractError(
                                "assistant content cannot contain tool_result"
                            )
                        converted_blocks.append(block)
                        continue
                    call = parse_tool_call(block)
                    call_data = call.as_dict()
                    call_id = call_data["call_id"]
                    prior = seen_calls.get(call_id)
                    if prior is not None:
                        if prior != call_data:
                            raise ProviderContractError(
                                "conflicting duplicate tool call identifier"
                            )
                        continue
                    seen_calls[call_id] = call_data
                    converted_blocks.append(
                        {
                            "type": "tool_use",
                            "id": call_data["call_id"],
                            "name": call_data["name"],
                            "input": call.parsed_arguments,
                        }
                    )
                blocks = converted_blocks
            elif any(
                block.get("type") in {"tool_call", "tool_result"}
                for block in blocks
            ):
                raise ProviderContractError(
                    "user content cannot contain tool call/result blocks"
                )
            if blocks:
                converted.append({"role": role, "content": blocks})

        return (
            "\n\n".join(system_parts) if system_parts else None,
            converted,
        )

    def _extract_usage(self, response: Any) -> Optional[Dict[str, Any]]:
        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return None
        if isinstance(usage_obj, dict):
            return dict(usage_obj)
        model_dump = getattr(usage_obj, "model_dump", None)
        if callable(model_dump):
            value = model_dump()
            if isinstance(value, dict):
                return value
        raise ProviderContractError("Anthropic usage must be an object")

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
        reasoning_blocks: List[Dict[str, Any]] = []
        tool_calls: List[ProviderToolCall] = []
        reasoning_summaries: List[str] = []
        provider_replay: List[Dict[str, Any]] = []
        seen_tool_call_ids: set[str] = set()

        content = getattr(response, "content", None)
        if not isinstance(content, (list, tuple)):
            raise ProviderRuntimeError(
                "Malformed Anthropic response content",
                kind="protocol",
                details={"code": "invalid_anthropic_content"},
            )
        response_id = getattr(response, "id", None)
        if not isinstance(response_id, str) or not response_id:
            raise ProviderRuntimeError(
                "Malformed Anthropic response identifier",
                kind="protocol",
                details={"code": "invalid_anthropic_content"},
            )
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason not in {
            "end_turn",
            "max_tokens",
            "stop_sequence",
            "tool_use",
            "pause_turn",
            "refusal",
            "model_context_window_exceeded",
        }:
            raise ProviderRuntimeError(
                "Unknown Anthropic stop reason",
                kind="protocol",
                details={"code": "unknown_anthropic_finish"},
            )

        for block in content:
            block_type = self._get_attr(block, "type")
            if block_type == "text":
                text_value = self._get_attr(block, "text", "")
                if not isinstance(text_value, str):
                    raise ProviderRuntimeError(
                        "Malformed Anthropic text block",
                        kind="protocol",
                        details={"code": "invalid_anthropic_content"},
                    )
                text_parts.append(text_value)
            elif block_type == "tool_use":
                call_id = self._get_attr(block, "id")
                name = self._get_attr(block, "name")
                input_payload = self._get_attr(block, "input", None)
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or call_id in seen_tool_call_ids
                    or not isinstance(name, str)
                    or not name
                    or not isinstance(input_payload, dict)
                ):
                    raise ProviderRuntimeError(
                        "Malformed Anthropic tool-use block",
                        kind="protocol",
                        details={"code": "invalid_anthropic_content"},
                    )
                seen_tool_call_ids.add(call_id)
                tool_calls.append(
                    ProviderToolCall(
                        id=call_id,
                        name=name,
                        arguments=input_payload,
                        type="function",
                        raw=block,
                    )
                )
            elif block_type == "thinking":
                thinking_text = self._get_attr(
                    block, "thinking", self._get_attr(block, "text", "")
                )
                if not isinstance(thinking_text, str):
                    raise ProviderRuntimeError(
                        "Malformed Anthropic thinking block",
                        kind="protocol",
                        details={"code": "invalid_anthropic_content"},
                    )
                reasoning_blocks.append({"type": "thinking", "text": thinking_text})
                if thinking_text:
                    reasoning_summaries.append(thinking_text)
                signature = self._get_attr(block, "signature")
                if signature is not None:
                    if not isinstance(signature, str) or not signature:
                        raise ProviderRuntimeError(
                            "Malformed Anthropic thinking signature",
                            kind="protocol",
                            details={"code": "invalid_anthropic_content"},
                        )
                    replay = {
                        "provider_id": "anthropic",
                        "schema_version": "anthropic.messages.v1",
                        "replay_scope": "same_provider",
                        "payload": {"signature": signature},
                    }
                    reasoning_blocks.append({"type": "provider_replay", **replay})
                    provider_replay.append(replay)
            elif block_type == "redacted_thinking":
                redacted_data = self._get_attr(block, "data")
                if not isinstance(redacted_data, str) or not redacted_data:
                    raise ProviderRuntimeError(
                        "Malformed Anthropic redacted thinking block",
                        kind="protocol",
                        details={"code": "invalid_anthropic_content"},
                    )
                reasoning_blocks.append(
                    {"type": "redacted_thinking", "data": redacted_data}
                )
                replay = {
                    "provider_id": "anthropic",
                    "schema_version": "anthropic.messages.v1",
                    "replay_scope": "same_provider",
                    "payload": {"redacted_data": redacted_data},
                }
                reasoning_blocks.append({"type": "provider_replay", **replay})
                provider_replay.append(replay)
            else:
                raise ProviderRuntimeError(
                    "Unknown Anthropic response content",
                    kind="protocol",
                    details={"code": "unknown_anthropic_content"},
                )

        provider_message = ProviderMessage(
            role="assistant",
            content="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            finish_reason=stop_reason,
            index=0,
            raw_message=response,
            annotations={
                "anthropic_stop_reason": getattr(response, "stop_reason", None)
            },
            message_id=response_id,
        )

        usage_dict = (
            usage_override
            if usage_override is not None
            else self._extract_usage(response)
        )
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
            reasoning_blocks=reasoning_blocks or None,
            model=getattr(response, "model", None),
            metadata=metadata,
            provider_replay=provider_replay or None,
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
        context: ProviderRuntimeContext,
    ) -> Tuple[
        Any, Optional[Dict[str, Any]], Dict[str, str], Optional[int], Optional[str]
    ]:
        stream_ctx = client.messages.stream(**request)
        usage_override: Optional[Dict[str, Any]] = None
        response_obj: Any = None
        block_types: Dict[int, str] = {}
        call_ids: Dict[int, str] = {}
        tool_names: Dict[int, str] = {}
        tool_args: Dict[int, str] = {}
        tool_arg_delta_started: set[int] = set()
        ended_blocks: set[int] = set()
        streamed_stop_reason: Optional[str] = None
        message_id: Optional[str] = None
        message_stopped = False
        def event_index(event: Any) -> int:
            value = self._get_attr(event, "index")
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ProviderRuntimeError(
                    "Malformed Anthropic content index",
                    kind="protocol",
                    details={"code": "invalid_anthropic_event"},
                )
            return value

        with stream_ctx as stream_obj:
            for event in stream_obj:
                context.raise_if_cancelled()
                event_type = self._get_attr(event, "type")
                if event_type == "message_start":
                    if message_id is not None or message_stopped:
                        raise ProviderRuntimeError(
                            "Duplicate Anthropic message start",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    message = self._get_attr(event, "message")
                    message_id_value = self._get_attr(message, "id")
                    if (
                        not isinstance(message_id_value, str)
                        or not message_id_value
                    ):
                        raise ProviderRuntimeError(
                            "Anthropic message start is missing its identifier",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    message_id = message_id_value
                    context.record_provider_event("response_start", {})
                elif event_type == "content_block_start":
                    if message_id is None or message_stopped:
                        raise ProviderRuntimeError(
                            "Anthropic content preceded message start",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    index = event_index(event)
                    if index != len(block_types) or index in block_types:
                        raise ProviderRuntimeError(
                            "Anthropic content indices are not contiguous",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    block = self._get_attr(event, "content_block")
                    block_type = self._get_attr(block, "type")
                    if not isinstance(block_type, str) or not block_type:
                        raise ProviderRuntimeError(
                            "Malformed Anthropic content block",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    block_types[index] = block_type
                    payload = {"content_index": index, "message_id": message_id}
                    if block_type == "text":
                        context.record_provider_event("text_start", payload)
                    elif block_type in {"thinking", "redacted_thinking"}:
                        context.record_provider_event("thinking_start", payload)
                    elif block_type == "tool_use":
                        call_id = self._get_attr(block, "id")
                        name = self._get_attr(block, "name")
                        input_payload = self._get_attr(block, "input")
                        if (
                            not isinstance(call_id, str)
                            or not call_id
                            or not isinstance(name, str)
                            or not name
                            or not isinstance(input_payload, dict)
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Anthropic tool-use start",
                                kind="protocol",
                                details={"code": "invalid_anthropic_event"},
                            )
                        call_ids[index] = call_id
                        tool_names[index] = name
                        tool_args[index] = canonical_json(input_payload)
                        context.record_provider_event(
                            "tool_call_start",
                            {**payload, "call_id": call_id, "name": name},
                        )
                    else:
                        raise ProviderRuntimeError(
                            "Unknown Anthropic content block",
                            kind="protocol",
                            details={"code": "unknown_anthropic_content"},
                        )
                elif event_type == "content_block_delta":
                    index = event_index(event)
                    if (
                        message_id is None
                        or index not in block_types
                        or index in ended_blocks
                        or message_stopped
                    ):
                        raise ProviderRuntimeError(
                            "Anthropic content delta preceded block start",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    delta = self._get_attr(event, "delta")
                    delta_type = self._get_attr(delta, "type")
                    block_type = block_types[index]
                    if delta_type == "text_delta" and block_type == "text":
                        value = self._get_attr(delta, "text")
                        event_kind = "text_delta"
                    elif (
                        delta_type == "thinking_delta"
                        and block_type in {"thinking", "redacted_thinking"}
                    ):
                        value = self._get_attr(delta, "thinking")
                        event_kind = "thinking_delta"
                    elif (
                        delta_type == "input_json_delta"
                        and block_type == "tool_use"
                    ):
                        value = self._get_attr(delta, "partial_json")
                        event_kind = "tool_call_delta"
                    elif (
                        delta_type == "signature_delta"
                        and block_type in {"thinking", "redacted_thinking"}
                    ):
                        signature = self._get_attr(delta, "signature")
                        if not isinstance(signature, str) or not signature:
                            raise ProviderRuntimeError(
                                "Malformed Anthropic signature delta",
                                kind="protocol",
                                details={"code": "invalid_anthropic_event"},
                            )
                        continue
                    else:
                        raise ProviderRuntimeError(
                            "Unknown Anthropic content delta",
                            kind="protocol",
                            details={"code": "unknown_anthropic_delta"},
                        )
                    if not isinstance(value, str) or not value:
                        raise ProviderRuntimeError(
                            "Malformed Anthropic content delta",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    payload = {
                        "content_index": index,
                        "message_id": message_id,
                        "delta": value,
                    }
                    if event_kind == "tool_call_delta":
                        if index not in tool_arg_delta_started:
                            if tool_args[index] != "{}":
                                raise ProviderRuntimeError(
                                    "Anthropic tool input changed during streaming",
                                    kind="protocol",
                                    details={"code": "invalid_anthropic_event"},
                                )
                            tool_args[index] = ""
                            tool_arg_delta_started.add(index)
                        tool_args[index] += value
                        context.record_provider_event(
                            event_kind,
                            {**payload, "call_id": call_ids[index]},
                        )
                    else:
                        context.record_provider_event(event_kind, payload)
                elif event_type == "content_block_stop":
                    index = event_index(event)
                    if (
                        message_id is None
                        or message_stopped
                        or index not in block_types
                        or index in ended_blocks
                    ):
                        raise ProviderRuntimeError(
                            "Malformed Anthropic content stop",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    ended_blocks.add(index)
                    payload = {"content_index": index, "message_id": message_id}
                    block_type = block_types[index]
                    if block_type == "text":
                        context.record_provider_event("text_end", payload)
                    elif block_type in {"thinking", "redacted_thinking"}:
                        context.record_provider_event("thinking_end", payload)
                    elif block_type == "tool_use":
                        try:
                            call = ProviderToolCall(
                                id=call_ids[index],
                                name=tool_names[index],
                                arguments=tool_args[index],
                            )
                            parsed_arguments = call.parsed_arguments
                        except (KeyError, ProviderContractError):
                            raise ProviderRuntimeError(
                                "Malformed Anthropic tool-use completion",
                                kind="protocol",
                                details={"code": "invalid_anthropic_event"},
                            ) from None
                        context.record_provider_event(
                            "tool_call_end",
                            {
                                **payload,
                                "call_id": call_ids[index],
                                "arguments_json": call.arguments_json,
                                "arguments": parsed_arguments,
                            },
                        )
                    else:
                        raise ProviderRuntimeError(
                            "Unknown Anthropic content block",
                            kind="protocol",
                            details={"code": "unknown_anthropic_content"},
                        )
                elif event_type == "message_delta":
                    if message_id is None or message_stopped:
                        raise ProviderRuntimeError(
                            "Malformed Anthropic message delta",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    delta = self._get_attr(event, "delta")
                    if delta is None or (
                        isinstance(delta, dict) and "stop_reason" not in delta
                    ):
                        raise ProviderRuntimeError(
                            "Malformed Anthropic message delta",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    stop_reason = self._get_attr(delta, "stop_reason")
                    if stop_reason is not None and stop_reason not in {
                        "end_turn",
                        "max_tokens",
                        "stop_sequence",
                        "tool_use",
                        "pause_turn",
                        "refusal",
                        "model_context_window_exceeded",
                    }:
                        raise ProviderRuntimeError(
                            "Unknown Anthropic message stop reason",
                            kind="protocol",
                            details={"code": "unknown_anthropic_finish"},
                        )
                    if stop_reason is not None:
                        streamed_stop_reason = stop_reason
                    event_usage = self._get_attr(event, "usage")
                    if event_usage is not None:
                        usage_override = self._extract_usage(
                            SimpleNamespace(usage=event_usage)
                        )
                elif event_type == "message_stop":
                    if (
                        message_id is None
                        or message_stopped
                        or set(block_types) != ended_blocks
                    ):
                        raise ProviderRuntimeError(
                            "Anthropic message stopped with open content blocks",
                            kind="protocol",
                            details={"code": "invalid_anthropic_event"},
                        )
                    message_stopped = True
                elif event_type == "ping":
                    continue
                else:
                    raise ProviderRuntimeError(
                        "Unknown Anthropic stream event",
                        kind="protocol",
                        details={"code": "unknown_anthropic_event"},
                    )
            if message_id is None or not message_stopped:
                raise ProviderRuntimeError(
                    "Anthropic stream omitted message terminal",
                    kind="protocol",
                    details={"code": "missing_anthropic_terminal"},
                )
            response = stream_obj.get_final_message()
            if getattr(response, "id", None) != message_id:
                raise ProviderRuntimeError(
                    "Anthropic final message identifier mismatch",
                    kind="protocol",
                    details={"code": "invalid_anthropic_event"},
                )
            if (
                streamed_stop_reason is not None
                and getattr(response, "stop_reason", None)
                != streamed_stop_reason
            ):
                raise ProviderRuntimeError(
                    "Anthropic final stop reason mismatch",
                    kind="protocol",
                    details={"code": "invalid_anthropic_event"},
                )
            response_obj = getattr(stream_obj, "response", None)
            final_usage = getattr(stream_obj, "get_final_usage", None)
            if callable(final_usage):
                usage_override = self._extract_usage(
                    SimpleNamespace(usage=final_usage())
                )
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
        context.raise_if_cancelled()
        tools = self._filter_anthropic_tools(tools, context)
        system_prompt, converted_messages = self._convert_messages(
            messages, context=context
        )

        anthropic_cfg = (context.agent_config.get("provider_tools") or {}).get(
            "anthropic", {}
        )
        max_tokens = anthropic_cfg.get("max_output_tokens", 1024)
        temperature = anthropic_cfg.get("temperature")
        prompt_cache_cfg = (
            (anthropic_cfg.get("prompt_cache") or {})
            if isinstance(anthropic_cfg, dict)
            else {}
        )
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
            request["system"] = self._build_system_prompt(
                system_prompt, prompt_cache_cfg
            )

        if tools:
            request["tools"] = tools

        resolved_tool_choice = self._resolve_tool_choice(
            anthropic_cfg.get("tool_choice"), tools
        )
        if resolved_tool_choice is not None:
            request["tool_choice"] = resolved_tool_choice

        if extra_headers:
            request["extra_headers"] = extra_headers

        if temperature is not None:
            request["temperature"] = float(temperature)
        request.update(anthropic_role_options(context))

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
                    [
                        t.get("name")
                        for t in tools
                        if isinstance(t, dict) and t.get("name")
                    ],
                )
            except Exception:
                pass
            if resolved_tool_choice is not None:
                session_state.set_provider_metadata(
                    "anthropic_tool_choice", resolved_tool_choice
                )

        rate_limit_cfg = (
            (anthropic_cfg.get("rate_limit") or {})
            if isinstance(anthropic_cfg, dict)
            else {}
        )
        max_retries = 0
        try:
            max_retries = int(rate_limit_cfg.get("max_retries") or 0)
        except Exception:
            max_retries = 0

        def _respect_delay() -> None:
            if delay_seconds > 0.0:
                provider_sdk_bindings.sleep(delay_seconds)

        attempt = 0
        exchange_recorder = getattr(context, "exchange_recorder", None)
        while True:
            self._maybe_delay_for_rate_limits(context, anthropic_cfg)
            _respect_delay()
            try:
                if stream:
                    response, usage_override, headers, status_code, body_text = (
                        self._call_streaming(client, request, context)
                    )
                else:
                    response, usage_override, headers, status_code, body_text = (
                        self._call_non_streaming(client, request)
                    )

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
            except ProviderRuntimeError:
                raise
            except ProviderContractError:
                raise ProviderRuntimeError(
                    "Anthropic provider contract violation",
                    kind="protocol",
                    output_emitted=bool(
                        exchange_recorder and exchange_recorder.output_emitted
                    ),
                    details={"code": "invalid_anthropic_content"},
                ) from None
            except Exception as exc:
                is_rate_limit = (
                    provider_sdk_bindings.anthropic_rate_limit_error is not None
                    and isinstance(
                        exc, provider_sdk_bindings.anthropic_rate_limit_error
                    )
                )
                is_overloaded = (
                    False if is_rate_limit else self._is_overloaded_error(exc)
                )
                headers: Dict[str, str] = {}
                status_code = None
                body_text = None
                if is_rate_limit or is_overloaded:
                    response_obj = getattr(exc, "response", None)
                    headers = self._normalize_headers(
                        getattr(response_obj, "headers", {}) or {}
                    )
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
                        details: Dict[str, Any] | None = None
                        if is_rate_limit:
                            details = {
                                "classification": "rate_limited",
                                "status_code": 429,
                            }
                            retry_after = headers.get("retry-after")
                            if retry_after is not None:
                                details["retry_after"] = retry_after
                        raise ProviderRuntimeError(
                            redaction.safe_exception_message(exc),
                            details=details,
                            output_emitted=bool(
                                exchange_recorder
                                and exchange_recorder.output_emitted
                            ),
                        ) from None
                    retry_after_value = headers.get("retry-after")
                    wait_seconds = self._compute_rate_limit_retry_delay(
                        rate_limit_cfg, attempt, retry_after_value
                    )
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
                                (
                                    "anthropic_last_rate_limit"
                                    if is_rate_limit
                                    else "anthropic_last_overload"
                                ),
                                {
                                    "attempt": attempt + 1,
                                    "retry_after": retry_after_value,
                                    "wait_seconds": wait_seconds,
                                    "status_code": status_code,
                                },
                            )
                        except Exception:
                            pass
                    if (
                        exchange_recorder is not None
                        and exchange_recorder.output_emitted
                    ):
                        raise ProviderRuntimeError(
                            "Anthropic retry refused after output",
                            kind="provider",
                            output_emitted=True,
                            details={
                                "classification": (
                                    "rate_limited"
                                    if is_rate_limit
                                    else "overloaded"
                                ),
                                "status_code": status_code,
                            },
                        ) from None
                    if exchange_recorder is not None:
                        exchange_recorder.reset_unemitted_attempt()
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
                    body_text=None,
                    body_base64=None,
                    context=context,
                    metadata=metadata,
                )
                raise ProviderRuntimeError(
                    redaction.safe_exception_message(exc),
                    output_emitted=bool(
                        exchange_recorder and exchange_recorder.output_emitted
                    ),
                ) from None


provider_registry.register_runtime("anthropic_messages", AnthropicMessagesRuntime)
