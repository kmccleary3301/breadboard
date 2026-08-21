"""OpenAI Chat Completions runtime."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import ProviderMessage, ProviderResult, ProviderRuntimeContext, ProviderRuntimeError
from ...sdk_bindings import provider_sdk_bindings
from .streaming import OpenAIBaseRuntime


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

    def _stream_chat_completion(
        self,
        client: Any,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        context: ProviderRuntimeContext,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Dict[int, Dict[str, Any]]]:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if extra_body:
            kwargs["extra_body"] = extra_body
        try:
            stream_factory = client.chat.completions.stream
            stream_ctx = stream_factory(**kwargs)
        except (AttributeError, TypeError) as exc:
            raise ProviderRuntimeError(
                "OpenAI SDK chat streaming adapter failure",
                kind="adapter",
            ) from exc
        except Exception as exc:  # pragma: no cover - provider SDK boundary
            kind = (
                "transport"
                if exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}
                else "provider"
            )
            raise ProviderRuntimeError(str(exc), kind=kind) from exc

        session_state = getattr(context, "session_state", None)
        turn_index = getattr(session_state, "_active_turn_index", None)
        message_id: Optional[str] = None
        message_started = False
        message_ended = False
        output_emitted = False
        text_parts: List[str] = []
        reasoning_fields: Dict[int, Dict[str, Any]] = {}
        tool_states: Dict[int, Dict[str, Any]] = {}
        started_tool_indices: set[int] = set()

        def ensure_message_started() -> None:
            nonlocal message_started
            if message_started:
                return
            message_started = True
            self._stream_emit_event(
                context,
                "assistant.message.start",
                {"message_id": message_id},
                turn_index=turn_index,
            )

        def end_message() -> None:
            nonlocal message_ended
            if not message_started or message_ended:
                return
            message_ended = True
            payload = {"message_id": message_id}
            text = "".join(text_parts)
            if text:
                payload["text"] = text
            self._stream_emit_event(
                context,
                "assistant.message.end",
                payload,
                turn_index=turn_index,
            )

        def emit_tool_start(index: int, state: Dict[str, Any]) -> None:
            if index in started_tool_indices or not state.get("call_id"):
                return
            ensure_message_started()
            started_tool_indices.add(index)
            self._stream_emit_event(
                context,
                "assistant.tool_call.start",
                {
                    "index": index,
                    "call_id": state["call_id"],
                    "tool": state.get("name"),
                },
                turn_index=turn_index,
            )

        try:
            with stream_ctx as stream:
                for event in stream:
                    event_type = self._get_attr(event, "type")
                    chunk = (
                        self._get_attr(event, "chunk")
                        if event_type == "chunk"
                        else event
                    )
                    if event_type not in {None, "chunk"} or not self._get_attr(
                        chunk, "choices"
                    ):
                        continue
                    chunk_id = self._get_attr(chunk, "id")
                    if chunk_id and message_id is None:
                        message_id = str(chunk_id)
                    for choice in self._get_attr(chunk, "choices", []) or []:
                        choice_index = int(self._get_attr(choice, "index", 0) or 0)
                        delta = self._get_attr(choice, "delta", {}) or {}
                        content_delta = self._get_attr(delta, "content")
                        if isinstance(content_delta, str) and content_delta:
                            ensure_message_started()
                            text_parts.append(content_delta)
                            output_emitted = True
                            self._stream_emit_event(
                                context,
                                "assistant.message.delta",
                                {"message_id": message_id, "text": content_delta},
                                turn_index=turn_index,
                            )

                        delta_reasoning = self._extract_reasoning_fields(delta)
                        for field_name in ("reasoning_content", "reasoning"):
                            reasoning_delta = delta_reasoning.get(field_name)
                            if (
                                not isinstance(reasoning_delta, str)
                                or not reasoning_delta
                            ):
                                continue
                            ensure_message_started()
                            choice_reasoning = reasoning_fields.setdefault(
                                choice_index, {}
                            )
                            choice_reasoning[field_name] = (
                                str(choice_reasoning.get(field_name, ""))
                                + reasoning_delta
                            )
                            output_emitted = True
                            self._stream_emit_event(
                                context,
                                "assistant.reasoning.delta",
                                {
                                    "message_id": message_id,
                                    "text": reasoning_delta,
                                    "provider_field": field_name,
                                },
                                turn_index=turn_index,
                            )
                            break

                        if self._get_attr(delta, "tool_calls") and text_parts:
                            end_message()
                        for tool_delta in self._get_attr(delta, "tool_calls", []) or []:
                            tool_index = int(
                                self._get_attr(tool_delta, "index", 0) or 0
                            )
                            state = tool_states.setdefault(
                                tool_index,
                                {"call_id": None, "name": None, "arguments": ""},
                            )
                            call_id = self._get_attr(tool_delta, "id")
                            if call_id:
                                state["call_id"] = str(call_id)
                            function_delta = (
                                self._get_attr(tool_delta, "function", {}) or {}
                            )
                            name_delta = self._get_attr(function_delta, "name")
                            if name_delta:
                                state["name"] = str(name_delta)
                            arguments_delta = self._get_attr(
                                function_delta, "arguments"
                            )
                            emit_tool_start(tool_index, state)
                            if isinstance(arguments_delta, str) and arguments_delta:
                                state["arguments"] += arguments_delta
                                output_emitted = True
                                if tool_index in started_tool_indices:
                                    self._stream_emit_event(
                                        context,
                                        "assistant.tool_call.delta",
                                        {
                                            "index": tool_index,
                                            "call_id": state["call_id"],
                                            "tool": state.get("name"),
                                            "arguments_delta": arguments_delta,
                                        },
                                        turn_index=turn_index,
                                    )

                finalizer = getattr(stream, "get_final_completion", None)
                if not callable(finalizer):
                    raise ProviderRuntimeError(
                        "OpenAI SDK chat stream has no Chat Completions finalizer",
                        kind="adapter",
                        output_emitted=output_emitted,
                    )
                final_response = finalizer()

            for choice in getattr(final_response, "choices", []) or []:
                final_message = self._get_attr(choice, "message", {}) or {}
                for fallback_index, tool_call in enumerate(
                    self._get_attr(final_message, "tool_calls", []) or []
                ):
                    tool_index = int(
                        self._get_attr(tool_call, "index", fallback_index)
                        or fallback_index
                    )
                    function = self._get_attr(tool_call, "function", {}) or {}
                    state = tool_states.setdefault(
                        tool_index, {"call_id": None, "name": None, "arguments": ""}
                    )
                    state["call_id"] = self._get_attr(tool_call, "id") or state.get(
                        "call_id"
                    )
                    state["name"] = self._get_attr(function, "name") or state.get(
                        "name"
                    )
                    final_arguments = self._get_attr(function, "arguments")
                    if isinstance(final_arguments, str):
                        state["arguments"] = final_arguments
                    emit_tool_start(tool_index, state)
                    if tool_index in started_tool_indices:
                        self._stream_emit_event(
                            context,
                            "assistant.tool_call.end",
                            {
                                "index": tool_index,
                                "call_id": state["call_id"],
                                "tool": state.get("name"),
                                "arguments": state.get("arguments", ""),
                            },
                            turn_index=turn_index,
                        )
            end_message()
            return final_response, reasoning_fields
        except ProviderRuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - provider SDK boundary
            if isinstance(exc, (AttributeError, TypeError)):
                kind = "adapter"
            elif exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}:
                kind = "transport"
            else:
                kind = "provider"
            raise ProviderRuntimeError(
                str(exc),
                kind=kind,
                output_emitted=output_emitted,
            ) from exc

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
        if (
            self.descriptor.provider_id == "openrouter"
            and isinstance(model, str)
            and model.startswith("openai/gpt-5")
        ):
            # Force provider routing away from Azure for GPT-5 OpenAI models on OpenRouter,
            # since some upstreams reject tool outputs.
            extra_body = {"provider": {"order": ["openai"], "allow_fallbacks": False}}

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
            )

        if response is None:
            call_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": request_messages,
                "stream": False,
                "extra_body": extra_body,
            }
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
                raise ProviderRuntimeError(str(exc), kind=kind) from exc

        normalized_messages: List[ProviderMessage] = []
        for idx, choice in enumerate(getattr(response, "choices", []) or []):
            error_obj = self._get_attr(choice, "error")
            if error_obj:
                msg = self._get_attr(error_obj, "message") or str(error_obj)
                raise ProviderRuntimeError(msg)
            message = self._get_attr(choice, "message", {})
            reasoning_fields = self._extract_reasoning_fields(message)
            for field_name, field_value in streamed_reasoning.get(idx, {}).items():
                reasoning_fields.setdefault(field_name, field_value)
            reasoning = reasoning_fields.get(
                "reasoning_content", reasoning_fields.get("reasoning")
            )
            normalized_messages.append(
                ProviderMessage(
                    role=self._get_attr(message, "role", "assistant"),
                    content=self._message_content_to_text(
                        self._get_attr(message, "content")
                    ),
                    tool_calls=self._extract_tool_calls(message),
                    finish_reason=self._get_attr(choice, "finish_reason"),
                    index=idx,
                    raw_message=message,
                    raw_choice=choice,
                    reasoning=reasoning,
                    annotations=reasoning_fields,
                )
            )

        return ProviderResult(
            messages=normalized_messages,
            raw_response=response,
            usage=self._extract_usage(response),
            model=getattr(response, "model", None),
            metadata={},
        )

