"""OpenAI Chat Completions runtime."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    ProviderContractError,
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from ...model_role_options import openai_chat_role_options
from ...sdk_bindings import provider_sdk_bindings
from ....security import redaction
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
        request_options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Dict[int, Dict[str, Any]]]:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if request_options:
            kwargs.update(request_options)
        if tools:
            kwargs["tools"] = tools
        if extra_body:
            kwargs["extra_body"] = extra_body
        try:
            stream_factory = client.chat.completions.stream
            stream_ctx = stream_factory(**kwargs)
        except (AttributeError, TypeError):
            raise ProviderRuntimeError(
                "OpenAI SDK chat streaming adapter failure",
                kind="adapter",
            ) from None
        except Exception as exc:  # pragma: no cover - provider SDK boundary
            kind = (
                "transport"
                if exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}
                else "provider"
            )
            raise ProviderRuntimeError(redaction.safe_exception_message(exc), kind=kind) from None

        session_state = getattr(context, "session_state", None)
        turn_index = getattr(session_state, "_active_turn_index", None)
        message_id: Optional[str] = None
        message_started = False
        message_ended = False
        text_started = False
        text_ended = False
        output_emitted = False
        text_parts: List[str] = []
        reasoning_fields: Dict[int, Dict[str, Any]] = {}
        tool_states: Dict[int, Dict[str, Any]] = {}
        stream_finish_reasons: Dict[int, str] = {}
        started_tool_indices: set[int] = set()
        started_reasoning_indices: set[int] = set()
        content_indices: Dict[Tuple[str, int], int] = {}
        next_content_index = 0

        def content_index(family: str, source_index: int = 0) -> int:
            nonlocal next_content_index
            key = (family, source_index)
            if key not in content_indices:
                content_indices[key] = next_content_index
                next_content_index += 1
            return content_indices[key]

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
                record=False,
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
                record=False,
            )

        def ensure_text_started() -> None:
            nonlocal text_started
            if text_ended:
                raise ProviderRuntimeError(
                    "Chat Completions text resumed after end",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_text_lifecycle"},
                )
            if text_started:
                return
            ensure_message_started()
            text_started = True
            context.record_provider_event(
                "text_start",
                {
                    "message_id": message_id,
                    "content_index": content_index("text"),
                },
            )

        def end_text() -> None:
            nonlocal text_ended
            if not text_started or text_ended:
                return
            text_ended = True
            context.record_provider_event(
                "text_end",
                {
                    "message_id": message_id,
                    "content_index": content_index("text"),
                },
            )

        def ensure_reasoning_started(index: int) -> None:
            if index in started_reasoning_indices:
                return
            ensure_message_started()
            started_reasoning_indices.add(index)
            self._stream_emit_event(
                context,
                "assistant.reasoning.start",
                {
                    "message_id": message_id,
                    "index": content_index("reasoning", index),
                },
                turn_index=turn_index,
            )

        def end_reasoning() -> None:
            for index in sorted(started_reasoning_indices):
                self._stream_emit_event(
                    context,
                    "assistant.reasoning.end",
                    {
                        "message_id": message_id,
                        "index": content_index("reasoning", index),
                    },
                    turn_index=turn_index,
                )

        def emit_tool_start(index: int, state: Dict[str, Any]) -> None:
            nonlocal output_emitted
            call_id = state.get("call_id")
            name = state.get("name")
            if not isinstance(call_id, str) or not call_id:
                return
            if not isinstance(name, str) or not name:
                return
            if index not in started_tool_indices:
                ensure_message_started()
                started_tool_indices.add(index)
                output_emitted = True
                self._stream_emit_event(
                    context,
                    "assistant.tool_call.start",
                    {
                        "message_id": message_id,
                        "index": content_index("tool", index),
                        "call_id": call_id,
                        "tool": name,
                    },
                    turn_index=turn_index,
                )
            pending_deltas = state.get("pending_deltas")
            if not isinstance(pending_deltas, list):
                raise ProviderRuntimeError(
                    "Malformed Chat Completions tool state",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_tool_state"},
                )
            for arguments_delta in pending_deltas:
                self._stream_emit_event(
                    context,
                    "assistant.tool_call.delta",
                    {
                        "message_id": message_id,
                        "index": content_index("tool", index),
                        "call_id": call_id,
                        "tool": name,
                        "arguments_delta": arguments_delta,
                    },
                    turn_index=turn_index,
                )
            pending_deltas.clear()

        try:
            with stream_ctx as stream:
                context.record_provider_event("response_start")
                for event in stream:
                    context.raise_if_cancelled()
                    event_type = self._get_attr(event, "type")
                    chunk = (
                        self._get_attr(event, "chunk")
                        if event_type == "chunk"
                        else event
                    )
                    if event_type not in {None, "chunk"} or chunk is None:
                        raise ProviderRuntimeError(
                            "Unknown Chat Completions stream event",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unknown_chat_stream_event"},
                        )
                    if self._non_null_unknown_fields(
                        chunk,
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
                            "Unknown Chat Completions chunk semantic",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unknown_chat_chunk"},
                        )
                    choices = self._get_attr(chunk, "choices")
                    if not isinstance(choices, (list, tuple)):
                        raise ProviderRuntimeError(
                            "Malformed Chat Completions choices",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_choices"},
                        )
                    if not choices:
                        continue
                    if len(choices) != 1:
                        raise ProviderRuntimeError(
                            "Chat Completions stream returned multiple choices",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unsupported_chat_choices"},
                        )
                    chunk_id = self._get_attr(chunk, "id")
                    if not isinstance(chunk_id, str) or not chunk_id:
                        raise ProviderRuntimeError(
                            "Chat Completions stream is missing its response id",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_response_id"},
                        )
                    if message_id is None:
                        message_id = chunk_id
                    elif message_id != chunk_id:
                        raise ProviderRuntimeError(
                            "Chat Completions response id changed during streaming",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "chat_response_id_mismatch"},
                        )

                    choice = choices[0]
                    if self._non_null_unknown_fields(
                        choice,
                        {
                            "index",
                            "delta",
                            "finish_reason",
                            "logprobs",
                            "error",
                        },
                    ):
                        raise ProviderRuntimeError(
                            "Unknown Chat Completions choice semantic",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unknown_chat_choice"},
                        )
                    if self._get_attr(choice, "logprobs") is not None:
                        raise ProviderRuntimeError(
                            "Unsupported Chat Completions log probabilities",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unsupported_chat_logprobs"},
                        )
                    if self._get_attr(choice, "error") is not None:
                        raise ProviderRuntimeError(
                            "Chat Completions provider returned a choice error",
                            kind="provider",
                            output_emitted=output_emitted,
                            details={"code": "chat_choice_error"},
                        )
                    choice_index = self._get_attr(choice, "index")
                    if (
                        not isinstance(choice_index, int)
                        or isinstance(choice_index, bool)
                        or choice_index != 0
                    ):
                        raise ProviderRuntimeError(
                            "Chat Completions choice index is invalid",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_choice_index"},
                        )
                    finish_reason = self._get_attr(choice, "finish_reason")
                    if finish_reason is not None:
                        if finish_reason not in {
                            "stop",
                            "end_turn",
                            "completed",
                            "complete",
                            "stop_sequence",
                            "pause_turn",
                            "length",
                            "max_tokens",
                            "max_output_tokens",
                            "truncated",
                            "model_context_window_exceeded",
                            "tool_call",
                            "tool_calls",
                            "tool_use",
                            "function_call",
                            "error",
                            "content_filter",
                            "refusal",
                            "safety",
                            "blocked",
                            "aborted",
                        }:
                            raise ProviderRuntimeError(
                                "Unknown Chat Completions finish reason",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "unknown_chat_finish_reason"},
                            )
                        prior_finish = stream_finish_reasons.get(choice_index)
                        if prior_finish is not None and prior_finish != finish_reason:
                            raise ProviderRuntimeError(
                                "Chat Completions finish reason changed",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "chat_finish_reason_mismatch"},
                            )
                        stream_finish_reasons[choice_index] = finish_reason

                    delta = self._get_attr(choice, "delta")
                    if delta is None:
                        delta = {}
                    if not isinstance(delta, dict) and not hasattr(
                        delta, "__dict__"
                    ):
                        raise ProviderRuntimeError(
                            "Malformed Chat Completions delta",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_delta"},
                        )
                    if self._non_null_unknown_fields(
                        delta,
                        {
                            "role",
                            "content",
                            "tool_calls",
                            "refusal",
                            "function_call",
                            "audio",
                            "reasoning",
                            "reasoning_content",
                            "reasoning_details",
                        },
                    ):
                        raise ProviderRuntimeError(
                            "Unknown Chat Completions delta semantic",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unknown_chat_delta"},
                        )
                    role = self._get_attr(delta, "role")
                    if role is not None and role != "assistant":
                        raise ProviderRuntimeError(
                            "Chat Completions delta has an invalid role",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_role"},
                        )
                    for unsupported_field in ("refusal", "function_call", "audio"):
                        if self._get_attr(delta, unsupported_field) is not None:
                            raise ProviderRuntimeError(
                                "Unsupported Chat Completions delta semantic",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "unsupported_chat_delta"},
                            )

                    content_delta = self._get_attr(delta, "content")
                    if content_delta is not None and not isinstance(
                        content_delta, str
                    ):
                        raise ProviderRuntimeError(
                            "Malformed Chat Completions text delta",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_text_delta"},
                        )
                    if content_delta:
                        ensure_text_started()
                        text_parts.append(content_delta)
                        output_emitted = True
                        self._stream_emit_event(
                            context,
                            "assistant.message.delta",
                            {
                                "message_id": message_id,
                                "index": content_index("text"),
                                "text": content_delta,
                            },
                            turn_index=turn_index,
                        )

                    delta_reasoning = self._extract_reasoning_fields(delta)
                    if delta_reasoning.get("reasoning_details") is not None:
                        raise ProviderRuntimeError(
                            "Unsupported structured Chat Completions reasoning delta",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unsupported_chat_reasoning_delta"},
                        )
                    reasoning_values = [
                        (field_name, delta_reasoning[field_name])
                        for field_name in ("reasoning_content", "reasoning")
                        if field_name in delta_reasoning
                    ]
                    if any(
                        not isinstance(reasoning_delta, str)
                        for _, reasoning_delta in reasoning_values
                    ):
                        raise ProviderRuntimeError(
                            "Malformed Chat Completions reasoning delta",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_reasoning_delta"},
                        )
                    nonempty_reasoning = [
                        item for item in reasoning_values if item[1]
                    ]
                    if (
                        len(nonempty_reasoning) > 1
                        and nonempty_reasoning[0][1] != nonempty_reasoning[1][1]
                    ):
                        raise ProviderRuntimeError(
                            "Chat Completions reasoning aliases disagree",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "chat_reasoning_alias_mismatch"},
                        )
                    if nonempty_reasoning:
                        field_name, reasoning_delta = nonempty_reasoning[0]
                        ensure_reasoning_started(choice_index)
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
                                "index": content_index(
                                    "reasoning", choice_index
                                ),
                                "text": reasoning_delta,
                                "provider_field": field_name,
                            },
                            turn_index=turn_index,
                        )

                    raw_tool_deltas = self._get_attr(delta, "tool_calls")
                    if raw_tool_deltas is None:
                        raw_tool_deltas = []
                    if not isinstance(raw_tool_deltas, (list, tuple)):
                        raise ProviderRuntimeError(
                            "Malformed Chat Completions tool-call deltas",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "invalid_chat_tool_deltas"},
                        )
                    if raw_tool_deltas and text_started and not text_ended:
                        end_text()
                    for tool_delta in raw_tool_deltas:
                        if self._non_null_unknown_fields(
                            tool_delta, {"index", "id", "type", "function"}
                        ):
                            raise ProviderRuntimeError(
                                "Unknown Chat Completions tool delta semantic",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "unknown_chat_tool_delta"},
                            )
                        tool_index = self._get_attr(tool_delta, "index")
                        if (
                            not isinstance(tool_index, int)
                            or isinstance(tool_index, bool)
                            or tool_index < 0
                        ):
                            raise ProviderRuntimeError(
                                "Chat Completions tool index is invalid",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_chat_tool_index"},
                            )
                        tool_type = self._get_attr(tool_delta, "type")
                        if tool_type is not None and tool_type != "function":
                            raise ProviderRuntimeError(
                                "Unsupported Chat Completions tool type",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "unsupported_chat_tool_type"},
                            )
                        state = tool_states.setdefault(
                            tool_index,
                            {
                                "call_id": None,
                                "name": None,
                                "arguments": "",
                                "pending_deltas": [],
                            },
                        )
                        call_id = self._get_attr(tool_delta, "id")
                        if call_id is not None:
                            if not isinstance(call_id, str) or not call_id:
                                raise ProviderRuntimeError(
                                    "Chat Completions tool id is invalid",
                                    kind="protocol",
                                    output_emitted=output_emitted,
                                    details={"code": "invalid_chat_tool_id"},
                                )
                            if state["call_id"] not in {None, call_id}:
                                raise ProviderRuntimeError(
                                    "Chat Completions tool id changed",
                                    kind="protocol",
                                    output_emitted=output_emitted,
                                    details={"code": "chat_tool_id_mismatch"},
                                )
                            state["call_id"] = call_id
                        function_delta = self._get_attr(tool_delta, "function")
                        if function_delta is None:
                            function_delta = {}
                        if not isinstance(function_delta, dict) and not hasattr(
                            function_delta, "__dict__"
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Chat Completions tool function",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_chat_tool_function"},
                            )
                        if self._non_null_unknown_fields(
                            function_delta, {"name", "arguments"}
                        ):
                            raise ProviderRuntimeError(
                                "Unknown Chat Completions tool function semantic",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "unknown_chat_tool_function"},
                            )
                        name_delta = self._get_attr(function_delta, "name")
                        if name_delta is not None:
                            if not isinstance(name_delta, str) or not name_delta:
                                raise ProviderRuntimeError(
                                    "Chat Completions tool name is invalid",
                                    kind="protocol",
                                    output_emitted=output_emitted,
                                    details={"code": "invalid_chat_tool_name"},
                                )
                            if state["name"] not in {None, name_delta}:
                                raise ProviderRuntimeError(
                                    "Chat Completions tool name changed",
                                    kind="protocol",
                                    output_emitted=output_emitted,
                                    details={"code": "chat_tool_name_mismatch"},
                                )
                            state["name"] = name_delta
                        arguments_delta = self._get_attr(
                            function_delta, "arguments"
                        )
                        if arguments_delta is not None and not isinstance(
                            arguments_delta, str
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Chat Completions tool arguments delta",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_chat_tool_arguments"},
                            )
                        if arguments_delta:
                            state["arguments"] += arguments_delta
                            state["pending_deltas"].append(arguments_delta)
                            output_emitted = True
                        if (
                            call_id is None
                            and name_delta is None
                            and arguments_delta is None
                        ):
                            raise ProviderRuntimeError(
                                "Empty Chat Completions tool delta",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_chat_tool_delta"},
                            )
                        emit_tool_start(tool_index, state)

                finalizer = getattr(stream, "get_final_completion", None)
                if not callable(finalizer):
                    raise ProviderRuntimeError(
                        "OpenAI SDK chat stream has no Chat Completions finalizer",
                        kind="adapter",
                        output_emitted=output_emitted,
                    )
                final_response = finalizer()

            if self._non_null_unknown_fields(
                final_response,
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
                    "Unknown final Chat Completions response semantic",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "unknown_chat_response"},
                )
            final_response_id = self._get_attr(final_response, "id")
            if not isinstance(final_response_id, str) or not final_response_id:
                raise ProviderRuntimeError(
                    "Final Chat Completions response is missing its id",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_response_id"},
                )
            if message_id is None:
                message_id = final_response_id
            elif message_id != final_response_id:
                raise ProviderRuntimeError(
                    "Final Chat Completions response id does not match the stream",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "chat_response_id_mismatch"},
                )
            final_choices = self._get_attr(final_response, "choices")
            if not isinstance(final_choices, (list, tuple)) or len(final_choices) != 1:
                raise ProviderRuntimeError(
                    "Final Chat Completions response requires exactly one choice",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_choices"},
                )
            final_choice = final_choices[0]
            if self._non_null_unknown_fields(
                final_choice,
                {
                    "index",
                    "message",
                    "finish_reason",
                    "logprobs",
                    "error",
                },
            ):
                raise ProviderRuntimeError(
                    "Unknown final Chat Completions choice semantic",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "unknown_chat_choice"},
                )
            if (
                self._get_attr(final_choice, "logprobs") is not None
                or self._get_attr(final_choice, "error") is not None
            ):
                raise ProviderRuntimeError(
                    "Unsupported final Chat Completions choice semantic",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "unsupported_chat_choice"},
                )
            final_choice_index = self._get_attr(final_choice, "index")
            if (
                not isinstance(final_choice_index, int)
                or isinstance(final_choice_index, bool)
                or final_choice_index != 0
            ):
                raise ProviderRuntimeError(
                    "Final Chat Completions choice index is invalid",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_choice_index"},
                )
            final_finish_reason = self._get_attr(final_choice, "finish_reason")
            if not isinstance(final_finish_reason, str) or not final_finish_reason:
                raise ProviderRuntimeError(
                    "Final Chat Completions choice is missing its finish reason",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_finish_reason"},
                )
            streamed_finish_reason = stream_finish_reasons.get(final_choice_index)
            if (
                streamed_finish_reason is not None
                and streamed_finish_reason != final_finish_reason
            ):
                raise ProviderRuntimeError(
                    "Final Chat Completions finish reason does not match the stream",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "chat_finish_reason_mismatch"},
                )

            final_message = self._get_attr(final_choice, "message")
            if final_message is None:
                raise ProviderRuntimeError(
                    "Final Chat Completions choice is missing its message",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_message"},
                )
            if self._non_null_unknown_fields(
                final_message,
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
                    "Unknown final Chat Completions message semantic",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "unknown_chat_message"},
                )
            for unsupported_field in (
                "refusal",
                "function_call",
                "audio",
                "annotations",
            ):
                if self._get_attr(final_message, unsupported_field) is not None:
                    raise ProviderRuntimeError(
                        "Unsupported final Chat Completions message semantic",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "unsupported_chat_message"},
                    )
            final_role = self._get_attr(final_message, "role")
            if final_role != "assistant":
                raise ProviderRuntimeError(
                    "Final Chat Completions message has an invalid role",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_role"},
                )
            final_content = self._message_content_to_text(
                self._get_attr(final_message, "content")
            )
            streamed_content = "".join(text_parts)
            if (
                final_content is not None
                and final_content != streamed_content
            ) or (streamed_content and final_content is None):
                raise ProviderRuntimeError(
                    "Final Chat Completions text does not match the stream",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "chat_text_mismatch"},
                )

            final_tool_calls = self._get_attr(final_message, "tool_calls")
            if final_tool_calls is None:
                final_tool_calls = []
            if not isinstance(final_tool_calls, (list, tuple)):
                raise ProviderRuntimeError(
                    "Final Chat Completions tool calls are malformed",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_chat_tool_calls"},
                )
            final_tool_indices: set[int] = set()
            final_call_ids: set[str] = set()
            for fallback_index, tool_call in enumerate(final_tool_calls):
                if self._non_null_unknown_fields(
                    tool_call, {"index", "id", "type", "function"}
                ):
                    raise ProviderRuntimeError(
                        "Unknown final Chat Completions tool semantic",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "unknown_chat_tool_call"},
                    )
                tool_index_value = self._get_attr(tool_call, "index")
                tool_index = (
                    fallback_index
                    if tool_index_value is None
                    else tool_index_value
                )
                if (
                    not isinstance(tool_index, int)
                    or isinstance(tool_index, bool)
                    or tool_index < 0
                    or tool_index in final_tool_indices
                ):
                    raise ProviderRuntimeError(
                        "Final Chat Completions tool index is invalid",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "invalid_chat_tool_index"},
                    )
                final_tool_indices.add(tool_index)
                if self._get_attr(tool_call, "type") != "function":
                    raise ProviderRuntimeError(
                        "Final Chat Completions tool type is unsupported",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "unsupported_chat_tool_type"},
                    )
                function = self._get_attr(tool_call, "function")
                if function is not None and self._non_null_unknown_fields(
                    function, {"name", "arguments"}
                ):
                    raise ProviderRuntimeError(
                        "Unknown final Chat Completions tool function semantic",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "unknown_chat_tool_function"},
                    )
                call_id = self._get_attr(tool_call, "id")
                name = self._get_attr(function, "name")
                final_arguments = self._get_attr(function, "arguments")
                if (
                    function is None
                    or not isinstance(call_id, str)
                    or not call_id
                    or call_id in final_call_ids
                    or not isinstance(name, str)
                    or not name
                    or not isinstance(final_arguments, str)
                    or not final_arguments
                ):
                    raise ProviderRuntimeError(
                        "Final Chat Completions tool call is malformed",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "invalid_chat_tool_call"},
                    )
                final_call_ids.add(call_id)
                state = tool_states.setdefault(
                    tool_index,
                    {
                        "call_id": None,
                        "name": None,
                        "arguments": "",
                        "pending_deltas": [],
                    },
                )
                if state["call_id"] not in {None, call_id}:
                    raise ProviderRuntimeError(
                        "Final Chat Completions tool id does not match the stream",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "chat_tool_id_mismatch"},
                    )
                if state["name"] not in {None, name}:
                    raise ProviderRuntimeError(
                        "Final Chat Completions tool name does not match the stream",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "chat_tool_name_mismatch"},
                    )
                if (
                    state["arguments"]
                    and state["arguments"] != final_arguments
                ):
                    raise ProviderRuntimeError(
                        "Final Chat Completions tool arguments do not match the stream",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "chat_tool_arguments_mismatch"},
                    )
                state["call_id"] = call_id
                state["name"] = name
                state["arguments"] = final_arguments
                emit_tool_start(tool_index, state)
                self._stream_emit_event(
                    context,
                    "assistant.tool_call.end",
                    {
                        "message_id": message_id,
                        "index": content_index("tool", tool_index),
                        "call_id": call_id,
                        "tool": name,
                        "arguments": final_arguments,
                    },
                    turn_index=turn_index,
                )
            if set(tool_states) != final_tool_indices:
                raise ProviderRuntimeError(
                    "Final Chat Completions tool calls do not match the stream",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "chat_tool_set_mismatch"},
                )
            end_reasoning()
            end_text()
            end_message()
            return final_response, reasoning_fields
        except ProviderRuntimeError:
            raise
        except ProviderContractError:
            raise ProviderRuntimeError(
                "Chat Completions provider contract violation",
                kind="protocol",
                output_emitted=output_emitted,
                details={"code": "invalid_chat_provider_contract"},
            ) from None
        except Exception as exc:  # pragma: no cover - provider SDK boundary
            if isinstance(exc, (AttributeError, TypeError)):
                kind = "adapter"
            elif exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}:
                kind = "transport"
            else:
                kind = "provider"
            raise ProviderRuntimeError(
                redaction.safe_exception_message(exc),
                kind=kind,
                output_emitted=output_emitted,
            ) from None

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
        request_messages = self._convert_messages_to_chat(
            messages, context=context
        )
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

