"""Decode OpenAI Chat Completions streams."""

from __future__ import annotations
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Protocol, Tuple

from ...contracts import (
    ProviderContractError,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from ...contract_wire import canonical_json
from ...native_response import (
    NativeProviderResponse,
    NativeStreamFragment,
    NativeToolCall,
)
from ....security import redaction

_OPENAI_DERIVED_CHAT_EVENT_TYPES = frozenset(
    {
        "content.delta",
        "content.done",
        "tool_calls.function.arguments.delta",
        "tool_calls.function.arguments.done",
        "logprobs.content.delta",
        "logprobs.content.done",
        "logprobs.refusal.delta",
        "logprobs.refusal.done",
        "refusal.delta",
        "refusal.done",
    }
)



class _ChatStreamHost(Protocol):
    """OpenAIBaseRuntime operations needed by the decoder."""

    def _get_attr(
        self, obj: Any, name: str, default: Any = None
    ) -> Any: ...

    def _non_null_unknown_fields(
        self, value: Any, allowed: set[str]
    ) -> set[str]: ...

    def _extract_reasoning_fields(self, message: Any) -> Dict[str, Any]: ...

    def _message_content_to_text(self, content: Any) -> Optional[str]: ...
    def _extract_usage(self, response: Any) -> Optional[Dict[str, Any]]: ...

    def _stream_emit_event(
        self,
        context: ProviderRuntimeContext,
        event_type: str,
        payload: Dict[str, Any],
        *,
        turn_index: Optional[int],
        record: bool = True,
    ) -> None: ...


@dataclass
class _ChatToolState:
    call_id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ""
    arguments_seen: bool = False
    pending_deltas: List[str] = field(default_factory=list)


@dataclass
class _ChatStreamState:
    turn_index: Optional[int]
    native_mode: bool = False
    message_id: Optional[str] = None
    model: Optional[str] = None
    usage: Any = None
    message_started: bool = False
    message_ended: bool = False
    text_started: bool = False
    text_ended: bool = False
    content_seen: bool = False
    output_emitted: bool = False
    text_parts: List[str] = field(default_factory=list)
    reasoning_fields: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    tool_states: Dict[int, _ChatToolState] = field(default_factory=dict)
    stream_finish_reasons: Dict[int, str] = field(default_factory=dict)
    started_tool_indices: set[int] = field(default_factory=set)
    started_reasoning_indices: set[int] = field(default_factory=set)
    content_indices: Dict[Tuple[str, int], int] = field(default_factory=dict)
    next_content_index: int = 0
    native_fragments: List[Tuple[str, int, str]] = field(default_factory=list)
    native_fragment_bytes: int = 0
    native_max_response_bytes: int = 0
    native_max_stream_fragments: int = 0


class OpenAIChatStreamDecoder:
    """Own Chat Completions stream lifecycle and response reconciliation."""

    def __init__(self, host: _ChatStreamHost) -> None:
        self._host = host

    def stream(
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
        stream_ctx = self._create_stream(
            client,
            model=model,
            messages=messages,
            tools=tools,
            extra_body=extra_body,
            request_options=request_options,
        )
        session_state = getattr(context, "session_state", None)
        turn_index = getattr(session_state, "_active_turn_index", None)
        state = _ChatStreamState(turn_index=turn_index)
        try:
            with stream_ctx as stream:
                context.record_provider_event("response_start")
                for event in stream:
                    context.raise_if_cancelled()
                    self._consume_event(event, context, state)

                finalizer = getattr(stream, "get_final_completion", None)
                if not callable(finalizer):
                    raise ProviderRuntimeError(
                        "OpenAI SDK chat stream has no Chat Completions finalizer",
                        kind="adapter",
                        output_emitted=state.output_emitted,
                    )
                final_response = finalizer()

            self._reconcile_final_response(final_response, context, state)
            self._finalize_lifecycle(context, state)
            return final_response, state.reasoning_fields
        except ProviderRuntimeError:
            raise
        except ProviderContractError:
            raise ProviderRuntimeError(
                "Chat Completions provider contract violation",
                kind="protocol",
                output_emitted=state.output_emitted,
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
                output_emitted=state.output_emitted,
            ) from None
    def native_stream(
        self,
        client: Any,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        context: ProviderRuntimeContext,
        binding_digest: str,
        request_digest: str,
        max_response_bytes: int,
        max_stream_fragments: int,
        extra_body: Optional[Dict[str, Any]] = None,
        request_options: Optional[Dict[str, Any]] = None,
    ) -> NativeProviderResponse:
        """Decode low-level Chat SSE without constructing normalized tool calls."""
        stream_ctx = self._create_stream(
            client,
            model=model,
            messages=messages,
            tools=tools,
            extra_body=extra_body,
            request_options=request_options,
            native=True,
        )
        session_state = getattr(context, "session_state", None)
        turn_index = getattr(session_state, "_active_turn_index", None)
        state = _ChatStreamState(
            turn_index=turn_index,
            native_mode=True,
            native_max_response_bytes=max_response_bytes,
            native_max_stream_fragments=max_stream_fragments,
        )
        try:
            with stream_ctx as stream:
                for event in stream:
                    context.raise_if_cancelled()
                    self._consume_event(event, context, state)
            context.raise_if_cancelled()
            return self._native_response_from_state(
                state,
                binding_digest=binding_digest,
                request_digest=request_digest,
                max_response_bytes=max_response_bytes,
                max_stream_fragments=max_stream_fragments,
            )
        except ProviderRuntimeError:
            raise
        except ProviderContractError:
            raise ProviderRuntimeError(
                "Chat Completions native response contract violation",
                kind="protocol",
                output_emitted=state.output_emitted,
                details={"code": "invalid_native_chat_response"},
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
                output_emitted=state.output_emitted,
            ) from None

    def native_response(
        self,
        response: Any,
        *,
        binding_digest: str,
        request_digest: str,
        max_response_bytes: int,
        max_stream_fragments: int,
    ) -> NativeProviderResponse:
        """Project one non-streamed response without parsing tool arguments."""
        state = _ChatStreamState(turn_index=None, native_mode=True)
        self._validate_native_final_response(response, state)
        return self._native_response_from_state(
            state,
            binding_digest=binding_digest,
            request_digest=request_digest,
            max_response_bytes=max_response_bytes,
            max_stream_fragments=max_stream_fragments,
        )

    def _native_response_from_state(
        self,
        state: _ChatStreamState,
        *,
        binding_digest: str,
        request_digest: str,
        max_response_bytes: int,
        max_stream_fragments: int,
    ) -> NativeProviderResponse:
        if not state.message_id:
            raise self._protocol_error(
                "Native Chat Completions response is missing its id",
                state,
                "invalid_chat_response_id",
            )
        if not state.stream_finish_reasons.get(0):
            raise self._protocol_error(
                "Native Chat Completions response is incomplete",
                state,
                "incomplete_chat_stream",
            )
        calls: list[NativeToolCall] = []
        for index in sorted(state.tool_states):
            tool_state = state.tool_states[index]
            if (
                not tool_state.call_id
                or not tool_state.name
                or not tool_state.arguments_seen
            ):
                raise self._protocol_error(
                    "Native Chat Completions tool call is incomplete",
                    state,
                    "incomplete_chat_tool_call",
                )
            calls.append(
                NativeToolCall(
                    id=tool_state.call_id,
                    name=tool_state.name,
                    arguments=(
                        "".join(tool_state.pending_deltas)
                        if tool_state.pending_deltas
                        else tool_state.arguments
                    ),
                )
            )
        fragments: list[NativeStreamFragment] = []
        for kind, tool_index, text in state.native_fragments:
            if kind == "content":
                fragments.append(
                    NativeStreamFragment(
                        kind="content", index=len(fragments), text=text
                    )
                )
                continue
            tool_state = state.tool_states.get(tool_index)
            if tool_state is None:
                raise self._protocol_error(
                    "Native Chat Completions fragment has no tool",
                    state,
                    "invalid_native_chat_fragment",
                )
            fragments.append(
                NativeStreamFragment(
                    kind="tool_arguments",
                    index=len(fragments),
                    text=text,
                    call_id=tool_state.call_id,
                    name=tool_state.name,
                )
            )
        usage = None
        if state.usage is not None:
            usage = self._host._extract_usage(
                SimpleNamespace(usage=state.usage)
            )
        response = NativeProviderResponse(
            binding_digest=binding_digest,
            request_digest=request_digest,
            response_id=state.message_id,
            model=state.model,
            content="".join(state.text_parts) if state.content_seen else None,
            finish_reason=state.stream_finish_reasons[0],
            tool_calls=tuple(calls),
            usage=usage,
            stream_fragments=tuple(fragments),
        )
        response.validate_bounds(
            max_response_bytes=max_response_bytes,
            max_stream_fragments=max_stream_fragments,
        )
        return response

    def _validate_native_final_response(
        self, response: Any, state: _ChatStreamState
    ) -> None:
        if self._host._non_null_unknown_fields(
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
            raise self._protocol_error(
                "Unknown native Chat Completions response semantic",
                state,
                "unknown_chat_response",
            )
        response_id = self._host._get_attr(response, "id")
        if not isinstance(response_id, str) or not response_id:
            raise self._protocol_error(
                "Native Chat Completions response is missing its id",
                state,
                "invalid_chat_response_id",
            )
        state.message_id = response_id
        response_model = self._host._get_attr(response, "model")
        if response_model is not None and not isinstance(response_model, str):
            raise self._protocol_error(
                "Native Chat Completions model is invalid",
                state,
                "invalid_chat_model",
            )
        state.model = response_model
        state.usage = self._host._get_attr(response, "usage")
        choices = self._host._get_attr(response, "choices")
        if not isinstance(choices, (list, tuple)) or len(choices) != 1:
            raise self._protocol_error(
                "Native Chat Completions response requires one choice",
                state,
                "invalid_chat_choices",
            )
        choice = choices[0]
        self._validate_choice(choice, state, streaming=False)
        finish_reason = self._host._get_attr(choice, "finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise self._protocol_error(
                "Native Chat Completions response is missing finish reason",
                state,
                "invalid_chat_finish_reason",
            )
        state.stream_finish_reasons[0] = finish_reason
        message = self._host._get_attr(choice, "message")
        if message is None:
            raise self._protocol_error(
                "Native Chat Completions response is missing message",
                state,
                "invalid_chat_message",
            )
        self._validate_native_message(message, state)

    def _validate_native_message(self, message: Any, state: _ChatStreamState) -> None:
        if self._host._non_null_unknown_fields(
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
            raise self._protocol_error(
                "Unknown native Chat Completions message semantic",
                state,
                "unknown_chat_message",
            )
        if self._host._get_attr(message, "role") != "assistant":
            raise self._protocol_error(
                "Native Chat Completions message role is invalid",
                state,
                "invalid_chat_role",
            )
        for field_name in (
            "refusal",
            "function_call",
            "audio",
            "annotations",
        ):
            if self._host._get_attr(message, field_name) is not None:
                raise self._protocol_error(
                    "Unsupported native Chat Completions message semantic",
                    state,
                    "unsupported_chat_message",
                )
        content = self._host._get_attr(message, "content")
        if content is not None:
            if isinstance(content, str):
                state.content_seen = True
                state.text_parts.append(content)
            elif isinstance(content, (list, tuple)):
                for block in content:
                    if (
                        not isinstance(block, dict)
                        or set(block) - {"type", "text"}
                        or block.get("type") not in {"text", "output_text"}
                        or not isinstance(block.get("text"), str)
                    ):
                        raise self._protocol_error(
                            "Native Chat Completions content is malformed",
                            state,
                            "invalid_chat_content",
                        )
                    state.content_seen = True
                    state.text_parts.append(block["text"])
            else:
                raise self._protocol_error(
                    "Native Chat Completions content is malformed",
                    state,
                    "invalid_chat_content",
                )
        tool_calls = self._host._get_attr(message, "tool_calls") or []
        if not isinstance(tool_calls, (list, tuple)):
            raise self._protocol_error(
                "Native Chat Completions tool calls are malformed",
                state,
                "invalid_chat_tool_calls",
            )
        for fallback_index, tool_call in enumerate(tool_calls):
            if self._host._non_null_unknown_fields(
                tool_call, {"index", "id", "type", "function"}
            ):
                raise self._protocol_error(
                    "Unknown native Chat Completions tool call",
                    state,
                    "unknown_chat_tool_call",
                )
            index_value = self._host._get_attr(tool_call, "index")
            index = fallback_index if index_value is None else index_value
            if type(index) is not int or index != fallback_index:
                raise self._protocol_error(
                    "Native Chat Completions tool index is invalid",
                    state,
                    "invalid_chat_tool_index",
                )
            if self._host._get_attr(tool_call, "type") != "function":
                raise self._protocol_error(
                    "Native Chat Completions tool type is unsupported",
                    state,
                    "unsupported_chat_tool_type",
                )
            function = self._host._get_attr(tool_call, "function")
            if function is None or self._host._non_null_unknown_fields(
                function, {"name", "arguments"}
            ):
                raise self._protocol_error(
                    "Native Chat Completions tool function is malformed",
                    state,
                    "invalid_chat_tool_function",
                )
            call_id = self._host._get_attr(tool_call, "id")
            name = self._host._get_attr(function, "name")
            arguments = self._host._get_attr(function, "arguments")
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(name, str)
                or not name
                or not isinstance(arguments, str)
            ):
                raise self._protocol_error(
                    "Native Chat Completions tool call is malformed",
                    state,
                    "invalid_chat_tool_call",
                )
            state.tool_states[index] = _ChatToolState(
                call_id=call_id,
                name=name,
                arguments=arguments,
                arguments_seen=True,
            )

    def _create_stream(
        self,
        client: Any,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        extra_body: Optional[Dict[str, Any]],
        request_options: Optional[Dict[str, Any]],
        native: bool = False,
    ) -> Any:
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
            if native:
                kwargs["stream"] = True
                return client.chat.completions.create(**kwargs)
            stream_factory = client.chat.completions.stream
            return stream_factory(**kwargs)
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
            raise ProviderRuntimeError(
                redaction.safe_exception_message(exc), kind=kind
            ) from None

    def _content_index(self, state: _ChatStreamState, family: str, source_index: int = 0) -> int:
        key = (family, source_index)
        if key not in state.content_indices:
            state.content_indices[key] = state.next_content_index
            state.next_content_index += 1
        return state.content_indices[key]

    def _ensure_message_started(
        self, context: ProviderRuntimeContext, state: _ChatStreamState
    ) -> None:
        if state.message_started:
            return
        state.message_started = True
        self._host._stream_emit_event(
            context,
            "assistant.message.start",
            {"message_id": state.message_id},
            turn_index=state.turn_index,
            record=False,
        )

    def _end_message(
        self, context: ProviderRuntimeContext, state: _ChatStreamState
    ) -> None:
        if not state.message_started or state.message_ended:
            return
        state.message_ended = True
        payload: Dict[str, Any] = {"message_id": state.message_id}
        text = "".join(state.text_parts)
        if text:
            payload["text"] = text
        self._host._stream_emit_event(
            context,
            "assistant.message.end",
            payload,
            turn_index=state.turn_index,
            record=False,
        )

    def _ensure_text_started(
        self, context: ProviderRuntimeContext, state: _ChatStreamState
    ) -> None:
        if state.text_ended:
            raise ProviderRuntimeError(
                "Chat Completions text resumed after end",
                kind="protocol",
                output_emitted=state.output_emitted,
                details={"code": "invalid_chat_text_lifecycle"},
            )
        if state.text_started:
            return
        self._ensure_message_started(context, state)
        state.text_started = True
        context.record_provider_event(
            "text_start",
            {
                "message_id": state.message_id,
                "content_index": self._content_index(state, "text"),
            },
        )

    def _end_text(
        self, context: ProviderRuntimeContext, state: _ChatStreamState
    ) -> None:
        if not state.text_started or state.text_ended:
            return
        state.text_ended = True
        if state.native_mode:
            return
        context.record_provider_event(
            "text_end",
            {
                "message_id": state.message_id,
                "content_index": self._content_index(state, "text"),
            },
        )

    def _ensure_reasoning_started(
        self,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
        index: int,
    ) -> None:
        if index in state.started_reasoning_indices:
            return
        self._ensure_message_started(context, state)
        state.started_reasoning_indices.add(index)
        self._host._stream_emit_event(
            context,
            "assistant.reasoning.start",
            {
                "message_id": state.message_id,
                "index": self._content_index(state, "reasoning", index),
            },
            turn_index=state.turn_index,
        )

    def _end_reasoning(
        self, context: ProviderRuntimeContext, state: _ChatStreamState
    ) -> None:
        for index in sorted(state.started_reasoning_indices):
            self._host._stream_emit_event(
                context,
                "assistant.reasoning.end",
                {
                    "message_id": state.message_id,
                    "index": self._content_index(state, "reasoning", index),
                },
                turn_index=state.turn_index,
            )

    def _emit_tool_start(
        self,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
        index: int,
        tool_state: _ChatToolState,
    ) -> None:
        call_id = tool_state.call_id
        name = tool_state.name
        if not isinstance(call_id, str) or not call_id:
            return
        if not isinstance(name, str) or not name:
            return
        if index not in state.started_tool_indices:
            state.started_tool_indices.add(index)
            state.output_emitted = True
            if not state.native_mode:
                self._ensure_message_started(context, state)
                self._host._stream_emit_event(
                    context,
                    "assistant.tool_call.start",
                    {
                        "message_id": state.message_id,
                        "index": self._content_index(state, "tool", index),
                        "call_id": call_id,
                        "tool": name,
                    },
                    turn_index=state.turn_index,
                )
        pending_deltas = tool_state.pending_deltas
        if not isinstance(pending_deltas, list):
            raise ProviderRuntimeError(
                "Malformed Chat Completions tool state",
                kind="protocol",
                output_emitted=state.output_emitted,
                details={"code": "invalid_chat_tool_state"},
            )
        if not state.native_mode:
            for arguments_delta in pending_deltas:
                self._host._stream_emit_event(
                    context,
                    "assistant.tool_call.delta",
                    {
                        "message_id": state.message_id,
                        "index": self._content_index(state, "tool", index),
                        "call_id": call_id,
                        "tool": name,
                        "arguments_delta": arguments_delta,
                    },
                    turn_index=state.turn_index,
                )
            pending_deltas.clear()


    def _protocol_error(
        self, message: str, state: _ChatStreamState, code: str
    ) -> ProviderRuntimeError:
        return ProviderRuntimeError(
            message,
            kind="protocol",
            output_emitted=state.output_emitted,
            details={"code": code},
        )

    def _consume_event(
        self,
        event: Any,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
    ) -> None:
        event_type = self._host._get_attr(event, "type")
        if event_type in _OPENAI_DERIVED_CHAT_EVENT_TYPES:
            return
        chunk = (
            self._host._get_attr(event, "chunk")
            if event_type == "chunk"
            else event
        )
        if event_type not in {None, "chunk"} or chunk is None:
            raise self._protocol_error(
                "Unknown Chat Completions stream event",
                state,
                "unknown_chat_stream_event",
            )
        if self._host._non_null_unknown_fields(
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
            raise self._protocol_error(
                "Unknown Chat Completions chunk semantic",
                state,
                "unknown_chat_chunk",
            )
        chunk_model = self._host._get_attr(chunk, "model")
        if chunk_model is not None:
            if not isinstance(chunk_model, str) or not chunk_model:
                raise self._protocol_error(
                    "Chat Completions stream model is invalid",
                    state,
                    "invalid_chat_model",
                )
            if state.model not in {None, chunk_model}:
                raise self._protocol_error(
                    "Chat Completions stream model changed",
                    state,
                    "chat_model_mismatch",
                )
            state.model = chunk_model
        chunk_usage = self._host._get_attr(chunk, "usage")
        if chunk_usage is not None:
            if state.usage is not None and state.usage != chunk_usage:
                raise self._protocol_error(
                    "Chat Completions stream usage changed",
                    state,
                    "chat_usage_mismatch",
                )
            state.usage = chunk_usage
        choices = self._host._get_attr(chunk, "choices")
        if not isinstance(choices, (list, tuple)):
            raise self._protocol_error(
                "Malformed Chat Completions choices",
                state,
                "invalid_chat_choices",
            )
        if not choices:
            return
        if len(choices) != 1:
            raise self._protocol_error(
                "Chat Completions stream returned multiple choices",
                state,
                "unsupported_chat_choices",
            )
        chunk_id = self._host._get_attr(chunk, "id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise self._protocol_error(
                "Chat Completions stream is missing its response id",
                state,
                "invalid_chat_response_id",
            )
        if state.message_id is None:
            state.message_id = chunk_id
        elif state.message_id != chunk_id:
            raise self._protocol_error(
                "Chat Completions response id changed during streaming",
                state,
                "chat_response_id_mismatch",
            )

        choice = choices[0]
        choice_index = self._validate_choice(choice, state)
        delta = self._validate_delta(choice, state)
        self._project_delta(delta, choice_index, context, state)

    def _validate_choice(
        self, choice: Any, state: _ChatStreamState, *, streaming: bool = True
    ) -> int:
        if self._host._non_null_unknown_fields(
            choice,
            {
                "index",
                "delta" if streaming else "message",
                "finish_reason",
                "logprobs",
                "error",
            },
        ):
            raise self._protocol_error(
                "Unknown Chat Completions choice semantic",
                state,
                "unknown_chat_choice",
            )
        if self._host._get_attr(choice, "logprobs") is not None:
            raise self._protocol_error(
                "Unsupported Chat Completions log probabilities",
                state,
                "unsupported_chat_logprobs",
            )
        if self._host._get_attr(choice, "error") is not None:
            raise ProviderRuntimeError(
                "Chat Completions provider returned a choice error",
                kind="provider",
                output_emitted=state.output_emitted,
                details={"code": "chat_choice_error"},
            )
        choice_index = self._host._get_attr(choice, "index")
        if (
            not isinstance(choice_index, int)
            or isinstance(choice_index, bool)
            or choice_index != 0
        ):
            raise self._protocol_error(
                "Chat Completions choice index is invalid",
                state,
                "invalid_chat_choice_index",
            )
        finish_reason = self._host._get_attr(choice, "finish_reason")
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
                raise self._protocol_error(
                    "Unknown Chat Completions finish reason",
                    state,
                    "unknown_chat_finish_reason",
                )
            prior_finish = state.stream_finish_reasons.get(choice_index)
            if prior_finish is not None and prior_finish != finish_reason:
                raise self._protocol_error(
                    "Chat Completions finish reason changed",
                    state,
                    "chat_finish_reason_mismatch",
                )
            state.stream_finish_reasons[choice_index] = finish_reason
        return choice_index

    def _validate_delta(self, choice: Any, state: _ChatStreamState) -> Any:
        delta = self._host._get_attr(choice, "delta")
        if delta is None:
            delta = {}
        if not isinstance(delta, dict) and not hasattr(delta, "__dict__"):
            raise self._protocol_error(
                "Malformed Chat Completions delta",
                state,
                "invalid_chat_delta",
            )
        if self._host._non_null_unknown_fields(
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
            raise self._protocol_error(
                "Unknown Chat Completions delta semantic",
                state,
                "unknown_chat_delta",
            )
        role = self._host._get_attr(delta, "role")
        if role is not None and role != "assistant":
            raise self._protocol_error(
                "Chat Completions delta has an invalid role",
                state,
                "invalid_chat_role",
            )
        for unsupported_field in ("refusal", "function_call", "audio"):
            if self._host._get_attr(delta, unsupported_field) is not None:
                raise self._protocol_error(
                    "Unsupported Chat Completions delta semantic",
                    state,
                    "unsupported_chat_delta",
                )
        return delta

    def _record_native_fragment(
        self, state: _ChatStreamState, kind: str, index: int, text: str
    ) -> None:
        if len(state.native_fragments) >= state.native_max_stream_fragments:
            raise self._protocol_error(
                "Native Chat Completions fragment limit exceeded",
                state,
                "native_fragment_limit_exceeded",
            )
        fragment = (kind, index, text)
        size = len(canonical_json(fragment).encode("utf-8"))
        if size > state.native_max_response_bytes - state.native_fragment_bytes:
            raise self._protocol_error(
                "Native Chat Completions retained fragment byte limit exceeded",
                state,
                "native_response_limit_exceeded",
            )
        state.native_fragment_bytes += size
        state.native_fragments.append(fragment)

    def _project_delta(
        self,
        delta: Any,
        choice_index: int,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
    ) -> None:
        content_delta = self._host._get_attr(delta, "content")
        if content_delta is not None:
            if not isinstance(content_delta, str):
                raise self._protocol_error(
                    "Malformed Chat Completions text delta",
                    state,
                    "invalid_chat_text_delta",
                )
            state.content_seen = True
            if state.native_mode:
                self._record_native_fragment(state, "content", choice_index, content_delta)
        if content_delta:
            state.text_parts.append(content_delta)
            state.output_emitted = True
            if state.native_mode:
                state.text_started = True
            else:
                self._ensure_text_started(context, state)
                self._host._stream_emit_event(
                    context,
                    "assistant.message.delta",
                    {
                        "message_id": state.message_id,
                        "index": self._content_index(state, "text"),
                        "text": content_delta,
                    },
                    turn_index=state.turn_index,
                )

        delta_reasoning = self._host._extract_reasoning_fields(delta)
        if delta_reasoning.get("reasoning_details") is not None:
            raise self._protocol_error(
                "Unsupported structured Chat Completions reasoning delta",
                state,
                "unsupported_chat_reasoning_delta",
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
            raise self._protocol_error(
                "Malformed Chat Completions reasoning delta",
                state,
                "invalid_chat_reasoning_delta",
            )
        nonempty_reasoning = [item for item in reasoning_values if item[1]]
        if (
            len(nonempty_reasoning) > 1
            and nonempty_reasoning[0][1] != nonempty_reasoning[1][1]
        ):
            raise self._protocol_error(
                "Chat Completions reasoning aliases disagree",
                state,
                "chat_reasoning_alias_mismatch",
            )
        if nonempty_reasoning:
            field_name, reasoning_delta = nonempty_reasoning[0]
            if not state.native_mode:
                self._ensure_reasoning_started(context, state, choice_index)
            choice_reasoning = state.reasoning_fields.setdefault(choice_index, {})
            choice_reasoning[field_name] = (
                str(choice_reasoning.get(field_name, "")) + reasoning_delta
            )
            state.output_emitted = True
            if not state.native_mode:
                self._host._stream_emit_event(
                    context,
                    "assistant.reasoning.delta",
                    {
                        "message_id": state.message_id,
                        "index": self._content_index(
                            state, "reasoning", choice_index
                        ),
                        "text": reasoning_delta,
                        "provider_field": field_name,
                    },
                    turn_index=state.turn_index,
                )

        self._accumulate_tool_deltas(
            delta, context=context, state=state
        )

    def _accumulate_tool_deltas(
        self,
        delta: Any,
        *,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
    ) -> None:
        raw_tool_deltas = self._host._get_attr(delta, "tool_calls")
        if raw_tool_deltas is None:
            raw_tool_deltas = []
        if not isinstance(raw_tool_deltas, (list, tuple)):
            raise self._protocol_error(
                "Malformed Chat Completions tool-call deltas",
                state,
                "invalid_chat_tool_deltas",
            )
        if raw_tool_deltas and state.text_started and not state.text_ended:
            self._end_text(context, state)
        for tool_delta in raw_tool_deltas:
            self._accumulate_tool_delta(
                tool_delta, context=context, state=state
            )

    def _accumulate_tool_delta(
        self,
        tool_delta: Any,
        *,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
    ) -> None:
        if self._host._non_null_unknown_fields(
            tool_delta, {"index", "id", "type", "function"}
        ):
            raise self._protocol_error(
                "Unknown Chat Completions tool delta semantic",
                state,
                "unknown_chat_tool_delta",
            )
        tool_index = self._host._get_attr(tool_delta, "index")
        if (
            not isinstance(tool_index, int)
            or isinstance(tool_index, bool)
            or tool_index < 0
        ):
            raise self._protocol_error(
                "Chat Completions tool index is invalid",
                state,
                "invalid_chat_tool_index",
            )
        tool_type = self._host._get_attr(tool_delta, "type")
        if tool_type is not None and tool_type != "function":
            raise self._protocol_error(
                "Unsupported Chat Completions tool type",
                state,
                "unsupported_chat_tool_type",
            )
        tool_state = state.tool_states.setdefault(
            tool_index, _ChatToolState()
        )
        call_id = self._host._get_attr(tool_delta, "id")
        if call_id is not None:
            if not isinstance(call_id, str) or not call_id:
                raise self._protocol_error(
                    "Chat Completions tool id is invalid",
                    state,
                    "invalid_chat_tool_id",
                )
            if tool_state.call_id not in {None, call_id}:
                raise self._protocol_error(
                    "Chat Completions tool id changed",
                    state,
                    "chat_tool_id_mismatch",
                )
            tool_state.call_id = call_id

        function_delta = self._host._get_attr(tool_delta, "function")
        if function_delta is None:
            function_delta = {}
        if not isinstance(function_delta, dict) and not hasattr(
            function_delta, "__dict__"
        ):
            raise self._protocol_error(
                "Malformed Chat Completions tool function",
                state,
                "invalid_chat_tool_function",
            )
        if self._host._non_null_unknown_fields(
            function_delta, {"name", "arguments"}
        ):
            raise self._protocol_error(
                "Unknown Chat Completions tool function semantic",
                state,
                "unknown_chat_tool_function",
            )
        name_delta = self._host._get_attr(function_delta, "name")
        if name_delta is not None:
            if not isinstance(name_delta, str) or not name_delta:
                raise self._protocol_error(
                    "Chat Completions tool name is invalid",
                    state,
                    "invalid_chat_tool_name",
                )
            if tool_state.name not in {None, name_delta}:
                raise self._protocol_error(
                    "Chat Completions tool name changed",
                    state,
                    "chat_tool_name_mismatch",
                )
            tool_state.name = name_delta
        arguments_delta = self._host._get_attr(function_delta, "arguments")
        if arguments_delta is not None and not isinstance(
            arguments_delta, str
        ):
            raise self._protocol_error(
                "Malformed Chat Completions tool arguments delta",
                state,
                "invalid_chat_tool_arguments",
            )
        if arguments_delta is not None:
            tool_state.arguments_seen = True
            if state.native_mode:
                self._record_native_fragment(
                    state, "tool_arguments", tool_index, arguments_delta
                )
                tool_state.pending_deltas.append(arguments_delta)
            else:
                tool_state.arguments += arguments_delta
                if arguments_delta:
                    tool_state.pending_deltas.append(arguments_delta)
            state.output_emitted = True
        if (
            call_id is None
            and name_delta is None
            and arguments_delta is None
        ):
            raise self._protocol_error(
                "Empty Chat Completions tool delta",
                state,
                "invalid_chat_tool_delta",
            )
        self._emit_tool_start(context, state, tool_index, tool_state)


    def _reconcile_final_response(
        self,
        final_response: Any,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
    ) -> None:
        if self._host._non_null_unknown_fields(
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
            raise self._protocol_error(
                "Unknown final Chat Completions response semantic",
                state,
                "unknown_chat_response",
            )
        final_response_id = self._host._get_attr(final_response, "id")
        if not isinstance(final_response_id, str) or not final_response_id:
            raise self._protocol_error(
                "Final Chat Completions response is missing its id",
                state,
                "invalid_chat_response_id",
            )
        if state.message_id is None:
            state.message_id = final_response_id
        elif state.message_id != final_response_id:
            raise self._protocol_error(
                "Final Chat Completions response id does not match the stream",
                state,
                "chat_response_id_mismatch",
            )
        final_choices = self._host._get_attr(final_response, "choices")
        if not isinstance(final_choices, (list, tuple)) or len(final_choices) != 1:
            raise self._protocol_error(
                "Final Chat Completions response requires exactly one choice",
                state,
                "invalid_chat_choices",
            )
        final_choice = final_choices[0]
        if self._host._non_null_unknown_fields(
            final_choice,
            {
                "index",
                "message",
                "finish_reason",
                "logprobs",
                "error",
            },
        ):
            raise self._protocol_error(
                "Unknown final Chat Completions choice semantic",
                state,
                "unknown_chat_choice",
            )
        if (
            self._host._get_attr(final_choice, "logprobs") is not None
            or self._host._get_attr(final_choice, "error") is not None
        ):
            raise self._protocol_error(
                "Unsupported final Chat Completions choice semantic",
                state,
                "unsupported_chat_choice",
            )
        final_choice_index = self._host._get_attr(final_choice, "index")
        if (
            not isinstance(final_choice_index, int)
            or isinstance(final_choice_index, bool)
            or final_choice_index != 0
        ):
            raise self._protocol_error(
                "Final Chat Completions choice index is invalid",
                state,
                "invalid_chat_choice_index",
            )
        final_finish_reason = self._host._get_attr(
            final_choice, "finish_reason"
        )
        if not isinstance(final_finish_reason, str) or not final_finish_reason:
            raise self._protocol_error(
                "Final Chat Completions choice is missing its finish reason",
                state,
                "invalid_chat_finish_reason",
            )
        streamed_finish_reason = state.stream_finish_reasons.get(
            final_choice_index
        )
        if (
            streamed_finish_reason is not None
            and streamed_finish_reason != final_finish_reason
        ):
            raise self._protocol_error(
                "Final Chat Completions finish reason does not match the stream",
                state,
                "chat_finish_reason_mismatch",
            )

        final_message = self._host._get_attr(final_choice, "message")
        if final_message is None:
            raise self._protocol_error(
                "Final Chat Completions choice is missing its message",
                state,
                "invalid_chat_message",
            )
        self._reconcile_final_message(final_message, state)
        self._reconcile_final_tools(
            self._host._get_attr(final_message, "tool_calls"),
            context,
            state,
        )

    def _reconcile_final_message(
        self, final_message: Any, state: _ChatStreamState
    ) -> None:
        if self._host._non_null_unknown_fields(
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
            raise self._protocol_error(
                "Unknown final Chat Completions message semantic",
                state,
                "unknown_chat_message",
            )
        for unsupported_field in (
            "refusal",
            "function_call",
            "audio",
            "annotations",
        ):
            if self._host._get_attr(final_message, unsupported_field) is not None:
                raise self._protocol_error(
                    "Unsupported final Chat Completions message semantic",
                    state,
                    "unsupported_chat_message",
                )
        final_role = self._host._get_attr(final_message, "role")
        if final_role != "assistant":
            raise self._protocol_error(
                "Final Chat Completions message has an invalid role",
                state,
                "invalid_chat_role",
            )
        final_content = self._host._message_content_to_text(
            self._host._get_attr(final_message, "content")
        )
        streamed_content = "".join(state.text_parts)
        if (
            final_content is not None and final_content != streamed_content
        ) or (streamed_content and final_content is None):
            raise self._protocol_error(
                "Final Chat Completions text does not match the stream",
                state,
                "chat_text_mismatch",
            )

    def _reconcile_final_tools(
        self,
        final_tool_calls: Any,
        context: ProviderRuntimeContext,
        state: _ChatStreamState,
    ) -> None:
        if final_tool_calls is None:
            final_tool_calls = []
        if not isinstance(final_tool_calls, (list, tuple)):
            raise self._protocol_error(
                "Final Chat Completions tool calls are malformed",
                state,
                "invalid_chat_tool_calls",
            )
        final_tool_indices: set[int] = set()
        final_call_ids: set[str] = set()
        for fallback_index, tool_call in enumerate(final_tool_calls):
            self._reconcile_final_tool(
                tool_call,
                context=context,
                fallback_index=fallback_index,
                final_tool_indices=final_tool_indices,
                final_call_ids=final_call_ids,
                state=state,
            )
        if set(state.tool_states) != final_tool_indices:
            raise self._protocol_error(
                "Final Chat Completions tool calls do not match the stream",
                state,
                "chat_tool_set_mismatch",
            )

    def _reconcile_final_tool(
        self,
        tool_call: Any,
        *,
        context: ProviderRuntimeContext,
        fallback_index: int,
        final_tool_indices: set[int],
        final_call_ids: set[str],
        state: _ChatStreamState,
    ) -> None:
        if self._host._non_null_unknown_fields(
            tool_call, {"index", "id", "type", "function"}
        ):
            raise self._protocol_error(
                "Unknown final Chat Completions tool semantic",
                state,
                "unknown_chat_tool_call",
            )
        tool_index_value = self._host._get_attr(tool_call, "index")
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
            raise self._protocol_error(
                "Final Chat Completions tool index is invalid",
                state,
                "invalid_chat_tool_index",
            )
        final_tool_indices.add(tool_index)
        if self._host._get_attr(tool_call, "type") != "function":
            raise self._protocol_error(
                "Final Chat Completions tool type is unsupported",
                state,
                "unsupported_chat_tool_type",
            )
        function = self._host._get_attr(tool_call, "function")
        if function is not None and self._host._non_null_unknown_fields(
            function, {"name", "arguments"}
        ):
            raise self._protocol_error(
                "Unknown final Chat Completions tool function semantic",
                state,
                "unknown_chat_tool_function",
            )
        call_id = self._host._get_attr(tool_call, "id")
        name = self._host._get_attr(function, "name")
        final_arguments = self._host._get_attr(function, "arguments")
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
            raise self._protocol_error(
                "Final Chat Completions tool call is malformed",
                state,
                "invalid_chat_tool_call",
            )
        final_call_ids.add(call_id)
        tool_state = state.tool_states.setdefault(
            tool_index, _ChatToolState()
        )
        if tool_state.call_id not in {None, call_id}:
            raise self._protocol_error(
                "Final Chat Completions tool id does not match the stream",
                state,
                "chat_tool_id_mismatch",
            )
        if tool_state.name not in {None, name}:
            raise self._protocol_error(
                "Final Chat Completions tool name does not match the stream",
                state,
                "chat_tool_name_mismatch",
            )
        if tool_state.arguments and tool_state.arguments != final_arguments:
            raise self._protocol_error(
                "Final Chat Completions tool arguments do not match the stream",
                state,
                "chat_tool_arguments_mismatch",
            )
        tool_state.call_id = call_id
        tool_state.name = name
        tool_state.arguments = final_arguments
        self._emit_tool_start(
            context,
            state,
            tool_index,
            tool_state,
        )
        self._host._stream_emit_event(
            context,
            "assistant.tool_call.end",
            {
                "message_id": state.message_id,
                "index": self._content_index(state, "tool", tool_index),
                "call_id": call_id,
                "tool": name,
                "arguments": final_arguments,
            },
            turn_index=state.turn_index,
        )


    def _finalize_lifecycle(
        self, context: ProviderRuntimeContext, state: _ChatStreamState
    ) -> None:
        self._end_reasoning(context, state)
        self._end_text(context, state)
        self._end_message(context, state)
