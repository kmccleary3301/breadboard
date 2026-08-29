"""OpenAI Chat Completions runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    OpenAICompletionsProviderProfile,
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    sanitize_provider_result,
)
from ...model_role_options import openai_chat_role_options
from ...sdk_bindings import provider_sdk_bindings
from ....security import redaction
from .streaming import OpenAIBaseRuntime
from .chat_stream_decoder import OpenAIChatStreamDecoder


@dataclass(frozen=True, slots=True)
class _ProfileClient:
    transport: Any
    profile: OpenAICompletionsProviderProfile
    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()


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
        # Long non-streamed reasoning turns can exceed the SDK's default read
        # timeout; a timed-out request currently kills the whole session.
        timeout_env = os.environ.get("BB_OPENAI_TIMEOUT_S")
        if timeout_env:
            try:
                kwargs["timeout"] = float(timeout_env)
            except ValueError:
                pass
        return provider_sdk_bindings.openai(**kwargs)

    def create_client_from_profile(
        self,
        profile: OpenAICompletionsProviderProfile,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Create a zero-retry SDK client from one immutable episode profile."""
        if not isinstance(profile, OpenAICompletionsProviderProfile):
            raise ProviderRuntimeError(
                "OpenAI Chat profile is invalid",
                kind="configuration",
                details={"code": "invalid_provider_profile"},
            )
        if timeout_seconds is not None and (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 3_600
        ):
            raise ProviderRuntimeError(
                "OpenAI Chat profile timeout is invalid",
                kind="configuration",
                details={"code": "invalid_provider_timeout"},
            )
        self._require_openai()
        with redaction.secret_value_scope(
            profile.scoped_credential,
            *profile.caller_headers.values(),
            allow_short=True,
        ):
            try:
                kwargs: Dict[str, Any] = {
                    "api_key": profile.scoped_credential,
                    "base_url": profile.base_url,
                    "default_headers": dict(profile.caller_headers),
                    "max_retries": 0,
                }
                if timeout_seconds is not None:
                    kwargs["timeout"] = float(timeout_seconds)
                transport = provider_sdk_bindings.openai(**kwargs)
            except Exception as exc:
                raise ProviderRuntimeError(
                    redaction.safe_exception_message(exc),
                    kind="configuration",
                    details={"code": "profile_client_creation_failed"},
                ) from None
        return _ProfileClient(transport, profile)

    def _stream_chat_completion(
        self,
        client: Any,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        context: ProviderRuntimeContext,
        extra_body: Optional[Dict[str, Any]] = None,
        request_options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Dict[int, Dict[str, Any]]]:
        return OpenAIChatStreamDecoder(self).stream(
            client,
            model=model,
            messages=messages,
            tools=tools,
            context=context,
            extra_body=extra_body,
            request_options=request_options,
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
        profile = context.provider_profile
        if profile is None:
            return self._invoke(
                client=client,
                model=model,
                messages=messages,
                tools=tools,
                stream=stream,
                context=context,
            )
        with redaction.secret_value_scope(
            profile.scoped_credential,
            *profile.caller_headers.values(),
            allow_short=True,
        ):
            return sanitize_provider_result(
                self._invoke(
                    client=client,
                    model=model,
                    messages=messages,
                    tools=tools,
                    stream=stream,
                    context=context,
                )
            )

    def profile_chat_request(
        self,
        profile: OpenAICompletionsProviderProfile,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        *,
        context: ProviderRuntimeContext,
    ) -> Dict[str, Any]:
        """Project the exact request used by a profile-bound invocation."""
        return profile.chat_request(
            self._convert_messages_to_chat(messages, context=context),
            self._convert_tools_to_openai(tools),
        )

    def _invoke(
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
        profile = context.provider_profile
        if profile is not None:
            profile_request = self.profile_chat_request(
                profile,
                messages,
                tools,
                context=context,
            )
            request_messages = profile_request["messages"]
            request_tools = profile_request.get("tools")
        else:
            request_messages = self._convert_messages_to_chat(
                messages, context=context
            )
            request_tools = self._convert_tools_to_openai(tools)
        if profile is not None:
            if not isinstance(client, _ProfileClient) or client.profile is not profile:
                raise ProviderRuntimeError(
                    "OpenAI Completions profile client does not match the episode",
                    kind="configuration",
                    details={"code": "profile_client_mismatch"},
                )
            client = client.transport
            if not stream:
                raise ProviderRuntimeError(
                    "OpenAI Completions profile requires streaming",
                    kind="configuration",
                    details={"code": "profile_requires_streaming"},
                )
            if model != profile.model:
                raise ProviderRuntimeError(
                    "OpenAI Completions profile model does not match invocation",
                    kind="configuration",
                    details={"code": "profile_model_mismatch"},
                )
            profile_request = profile.chat_request(request_messages, request_tools)
            profile_request.pop("model")
            profile_request.pop("messages")
            profile_request.pop("stream")
            request_tools = profile_request.pop("tools", None)
            thinking_control = profile_request.pop("enable_thinking", None)
            extra_body = (
                {"enable_thinking": thinking_control}
                if thinking_control is not None
                else None
            )
            role_request = profile_request
        elif isinstance(client, _ProfileClient):
            raise ProviderRuntimeError(
                "OpenAI Completions profile client requires its episode profile",
                kind="configuration",
                details={"code": "profile_context_missing"},
            )
        else:
            extra_body: Optional[Dict[str, Any]] = None
            if (
                self.descriptor.provider_id == "openrouter"
                and isinstance(model, str)
                and model.startswith("openai/gpt-5")
            ):
                # Force provider routing away from Azure for GPT-5 OpenAI models on OpenRouter,
                # since some upstreams reject tool outputs.
                extra_body = {
                    "provider": {"order": ["openai"], "allow_fallbacks": False}
                }
            role_request, role_extra_body = openai_chat_role_options(
                context,
                provider_id=self.descriptor.provider_id,
            )
            if role_extra_body:
                extra_body = {**(extra_body or {}), **role_extra_body}

        response: Any = None
        streamed_reasoning: Dict[int, Dict[str, Any]] = {}
        if stream:
            response, streamed_reasoning = self._stream_chat_completion(
                client,
                model=model,
                messages=request_messages,
                tools=request_tools,
                context=context,
                extra_body=extra_body,
                request_options=role_request,
            )

        if response is None:
            call_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": request_messages,
                "stream": False,
                "extra_body": extra_body,
            }
            call_kwargs.update(role_request)
            if request_tools:
                call_kwargs["tools"] = request_tools
            try:
                response = self._call_with_raw_response(
                    client.chat.completions,
                    error_context="chat.completions.create",
                    context=context,
                    **call_kwargs,
                )
            except ProviderRuntimeError:
                raise
            except Exception as exc:  # pragma: no cover - exercised in integration
                kind = (
                    "adapter"
                    if isinstance(exc, (AttributeError, TypeError))
                    else "provider"
                )
                raise ProviderRuntimeError(redaction.safe_exception_message(exc), kind=kind) from None

        if self._non_null_unknown_fields(
            response,
            {
                "id",
                "choices",
                "created",
                "model",
                "object",
                "service_tier",
                "system_fingerprint",
                "usage",
            },
        ):
            raise ProviderRuntimeError(
                "Unknown Chat Completions response semantic",
                kind="protocol",
                details={"code": "unknown_chat_response"},
            )
        response_id = self._get_attr(response, "id")
        if not isinstance(response_id, str) or not response_id:
            raise ProviderRuntimeError(
                "Chat Completions response is missing its id",
                kind="protocol",
                details={"code": "invalid_chat_response_id"},
            )
        choices = self._get_attr(response, "choices")
        if not isinstance(choices, (list, tuple)) or not choices:
            raise ProviderRuntimeError(
                "Chat Completions response has no choices",
                kind="protocol",
                details={"code": "invalid_chat_choices"},
            )
        normalized_messages: List[ProviderMessage] = []
        seen_choice_indices: set[int] = set()
        for choice in choices:
            if self._non_null_unknown_fields(
                choice,
                {
                    "index",
                    "message",
                    "finish_reason",
                    "logprobs",
                    "error",
                },
            ):
                raise ProviderRuntimeError(
                    "Unknown Chat Completions choice semantic",
                    kind="protocol",
                    details={"code": "unknown_chat_choice"},
                )
            if self._get_attr(choice, "logprobs") is not None:
                raise ProviderRuntimeError(
                    "Unsupported Chat Completions log probabilities",
                    kind="protocol",
                    details={"code": "unsupported_chat_logprobs"},
                )
            choice_index = self._get_attr(choice, "index")
            if (
                not isinstance(choice_index, int)
                or isinstance(choice_index, bool)
                or choice_index < 0
                or choice_index in seen_choice_indices
            ):
                raise ProviderRuntimeError(
                    "Chat Completions choice index is invalid",
                    kind="protocol",
                    details={"code": "invalid_chat_choice_index"},
                )
            seen_choice_indices.add(choice_index)
            error_obj = self._get_attr(choice, "error")
            if error_obj is not None:
                raise ProviderRuntimeError(
                    "Chat Completions provider returned a choice error",
                    kind="provider",
                    details={"code": "chat_choice_error"},
                )
            message = self._get_attr(choice, "message")
            if message is None:
                raise ProviderRuntimeError(
                    "Chat Completions choice is missing its message",
                    kind="protocol",
                    details={"code": "invalid_chat_message"},
                )
            if self._non_null_unknown_fields(
                message,
                {
                    "role",
                    "content",
                    "tool_calls",
                    "refusal",
                    "function_call",
                    "audio",
                    "annotations",
                    "reasoning",
                    "reasoning_content",
                    "reasoning_details",
                },
            ):
                raise ProviderRuntimeError(
                    "Unknown Chat Completions message semantic",
                    kind="protocol",
                    details={"code": "unknown_chat_message"},
                )
            role = self._get_attr(message, "role")
            if role != "assistant":
                raise ProviderRuntimeError(
                    "Chat Completions message has an invalid role",
                    kind="protocol",
                    details={"code": "invalid_chat_role"},
                )
            for unsupported_field in (
                "refusal",
                "function_call",
                "audio",
                "annotations",
            ):
                if self._get_attr(message, unsupported_field) is not None:
                    raise ProviderRuntimeError(
                        "Unsupported Chat Completions message semantic",
                        kind="protocol",
                        details={"code": "unsupported_chat_message"},
                    )
            reasoning_fields = self._extract_reasoning_fields(message)
            for field_name, field_value in streamed_reasoning.get(
                choice_index, {}
            ).items():
                existing = reasoning_fields.get(field_name)
                if existing is not None and existing != field_value:
                    raise ProviderRuntimeError(
                        "Final Chat Completions reasoning does not match the stream",
                        kind="protocol",
                        details={"code": "chat_reasoning_mismatch"},
                    )
                reasoning_fields.setdefault(field_name, field_value)
            reasoning = reasoning_fields.get(
                "reasoning_content", reasoning_fields.get("reasoning")
            )
            normalized_messages.append(
                ProviderMessage(
                    role=role,
                    content=self._message_content_to_text(
                        self._get_attr(message, "content")
                    ),
                    tool_calls=self._extract_tool_calls(message),
                    finish_reason=self._get_attr(choice, "finish_reason"),
                    index=choice_index,
                    raw_message=message,
                    raw_choice=choice,
                    reasoning=reasoning,
                    annotations=reasoning_fields,
                    message_id=response_id,
                )
            )

        return ProviderResult(
            messages=normalized_messages,
            raw_response=response,
            usage=self._extract_usage(response),
            model=getattr(response, "model", None),
            metadata={},
        )

