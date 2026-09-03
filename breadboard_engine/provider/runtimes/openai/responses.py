"""OpenAI Responses API runtime."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    ProviderContractError,
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderToolCall,
    canonical_json,
)
from ...input_media import resolve_input_media
from ...model_role_options import openai_responses_role_options
from ....logging.provider_dump import provider_dump_logger
from ....security import redaction
from .chat import OpenAIChatRuntime


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
        context: ProviderRuntimeContext | None = None,
    ) -> List[Dict[str, Any]]:
        def serialize_output(value: Any) -> str:
            if isinstance(value, str):
                return value
            return canonical_json(value)

        def tool_outputs(message: Dict[str, Any]) -> List[Dict[str, str]]:
            role = message.get("role")
            if role not in {"tool", "tool_result"}:
                return []
            content = message.get("content")
            if isinstance(content, list):
                outputs: List[Dict[str, str]] = []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        raise ProviderContractError(
                            "tool-result messages require tool_result blocks"
                        )
                    call_id = block.get("call_id")
                    if not isinstance(call_id, str) or not call_id:
                        raise ProviderContractError(
                            "tool_result requires a call_id"
                        )
                    if "content" not in block:
                        raise ProviderContractError(
                            "tool_result requires content"
                        )
                    outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": serialize_output(block["content"]),
                        }
                    )
                if not outputs:
                    raise ProviderContractError(
                        "tool-result messages require content"
                    )
                return outputs
            if role != "tool":
                raise ProviderContractError(
                    "canonical tool_result messages require block content"
                )
            call_id = (
                message.get("tool_call_id")
                or message.get("tool_use_id")
                or message.get("call_id")
            )
            if not isinstance(call_id, str) or not call_id:
                raise ProviderContractError("tool message requires a call_id")
            return [
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": serialize_output("" if content is None else content),
                }
            ]

        def assistant_tool_calls(
            message: Dict[str, Any],
        ) -> List[ProviderToolCall]:
            raw_calls: List[Any] = []
            transport_calls = message.get("tool_calls")
            if transport_calls is not None:
                if not isinstance(transport_calls, list):
                    raise ProviderContractError("tool_calls must be a list")
                raw_calls.extend(transport_calls)
            content = message.get("content")
            if isinstance(content, list):
                raw_calls.extend(
                    block
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "tool_call"
                )
            calls: List[ProviderToolCall] = []
            seen: Dict[str, Dict[str, Any]] = {}
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    raise ProviderContractError("tool call must be an object")
                function = raw.get("function")
                function_data = function if isinstance(function, dict) else raw
                call_id = (
                    raw.get("call_id")
                    or raw.get("id")
                    or raw.get("tool_call_id")
                )
                name = function_data.get("name")
                if "arguments_json" in function_data:
                    arguments = function_data.get("arguments_json")
                elif "arguments" in function_data:
                    arguments = function_data.get("arguments")
                else:
                    raise ProviderContractError(
                        "tool call requires arguments"
                    )
                if arguments is None:
                    raise ProviderContractError(
                        "tool call requires arguments"
                    )
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
                call_data = call.as_dict()
                call_id_value = call_data["call_id"]
                prior = seen.get(call_id_value)
                if prior is not None:
                    if prior != call_data:
                        raise ProviderContractError(
                            "conflicting duplicate tool call identifier"
                        )
                    continue
                seen[call_id_value] = call_data
                calls.append(call)
            return calls

        converted: List[Dict[str, Any]] = []
        effective_include_tool_calls = (
            include_tool_calls or self.descriptor.provider_id == "openrouter"
        )
        tool_output_call_ids: set[str] = set()
        for message in messages:
            for output in tool_outputs(message):
                tool_output_call_ids.add(output["call_id"])

        for message in messages:
            role = message.get("role")
            if role not in {
                "system",
                "developer",
                "user",
                "assistant",
                "tool",
                "tool_result",
            }:
                raise ProviderContractError(f"unsupported Responses role: {role!r}")
            outputs = tool_outputs(message)
            if outputs:
                converted.extend(outputs)
                continue

            content = message.get("content")
            emitted_call = False
            if role == "assistant" and effective_include_tool_calls:
                for call in assistant_tool_calls(message):
                    call_data = call.as_dict()
                    converted.append(
                        {
                            "type": "function_call",
                            "call_id": call_data["call_id"],
                            "name": call_data["name"],
                            "arguments": call_data["arguments_json"],
                        }
                    )
                    emitted_call = True

            default_text_type = (
                "output_text" if role == "assistant" else "input_text"
            )
            if isinstance(content, str):
                content_blocks: List[Dict[str, Any]] = [
                    {"type": default_text_type, "text": content}
                ]
            elif isinstance(content, list):
                content_blocks = []
                for block in content:
                    if not isinstance(block, dict):
                        raise ProviderContractError(
                            "Responses content blocks must be objects"
                        )
                    block_type = block.get("type")
                    if block_type == "tool_call":
                        continue
                    if block_type in {"text", "input_text", "output_text"}:
                        text = block.get("text")
                        if not isinstance(text, str):
                            raise ProviderContractError(
                                "Responses text block requires text"
                            )
                        content_blocks.append(
                            {"type": default_text_type, "text": text}
                        )
                    elif block_type == "media":
                        media = resolve_input_media(block, context)
                        content_blocks.append(
                            {
                                "type": "input_image",
                                "image_url": media.data_url,
                            }
                        )
                    elif block_type in {
                        "input_image",
                        "refusal",
                        "input_file",
                        "computer_screenshot",
                        "summary_text",
                    }:
                        content_blocks.append(dict(block))
                    elif block_type in {
                        "thinking",
                        "redacted_thinking",
                        "provider_replay",
                    }:
                        raise ProviderContractError(
                            "Responses reasoning replay requires provider-native input support"
                        )
                    else:
                        raise ProviderContractError(
                            f"unsupported Responses content block: {block_type!r}"
                        )
            elif content is None:
                content_blocks = []
            else:
                raise ProviderContractError(
                    "Responses message content must be text or blocks"
                )

            if emitted_call and (
                not content_blocks
                or all(
                    block.get("type") in {"input_text", "output_text"}
                    and block.get("text") == ""
                    for block in content_blocks
                )
            ):
                continue
            if self.descriptor.provider_id == "openrouter":
                text_parts = [
                    block["text"]
                    for block in content_blocks
                    if block.get("type") in {"input_text", "output_text"}
                    and isinstance(block.get("text"), str)
                ]
                if len(text_parts) != len(content_blocks):
                    raise ProviderContractError(
                        "OpenRouter Responses input requires text content"
                    )
                converted.append({"role": role, "content": "".join(text_parts)})
            else:
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
            metadata={
                "phase_label": (context.extra or {}).get("phase16_phase_label"),
                "turn_index": (context.extra or {}).get("turn_index"),
            },
        )

        try:
            stream_ctx = client.responses.stream(**payload)
        except (AttributeError, TypeError):
            raise ProviderRuntimeError(
                "OpenAI SDK Responses streaming adapter failure", kind="adapter"
            ) from None
        except Exception as exc:  # pragma: no cover - wrapped as runtime error
            kind = (
                "transport"
                if exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}
                else "provider"
            )
            raise ProviderRuntimeError(
                redaction.safe_exception_message(exc), kind=kind
            ) from None

        session_state = getattr(context, "session_state", None)
        turn_index = getattr(session_state, "_active_turn_index", None)
        started_item_ids: set[str] = set()
        ended_item_ids: set[str] = set()
        output_emitted = False
        tool_states: Dict[str, Dict[str, Any]] = {}
        ended_tool_item_ids: set[str] = set()
        started_reasoning_item_ids: set[str] = set()
        ended_reasoning_item_ids: set[str] = set()
        output_item_types: Dict[str, Tuple[str, int]] = {}
        completed_output_item_ids: set[str] = set()
        response_terminal: Optional[str] = None

        def start_item(item_id: str) -> None:
            if not isinstance(item_id, str) or not item_id:
                raise ProviderRuntimeError(
                    "Responses message item is missing item_id",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if item_id in started_item_ids:
                return
            started_item_ids.add(item_id)
            self._stream_emit_event(
                context,
                "assistant.message.start",
                {"item_id": item_id},
                turn_index=turn_index,
            )

        def emit_delta(item_id: str, delta: str) -> None:
            nonlocal output_emitted
            if not isinstance(item_id, str) or not item_id:
                raise ProviderRuntimeError(
                    "Responses text delta is missing item_id",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if not isinstance(delta, str):
                raise ProviderRuntimeError(
                    "Responses text delta is malformed",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if not delta:
                return
            start_item(item_id)
            output_emitted = True
            self._stream_emit_event(
                context,
                "assistant.message.delta",
                {"item_id": item_id, "delta": delta},
                turn_index=turn_index,
            )

        def end_item(item_id: str) -> None:
            if not isinstance(item_id, str) or not item_id:
                raise ProviderRuntimeError(
                    "Responses message end is missing item_id",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if item_id in ended_item_ids:
                return
            if item_id not in started_item_ids:
                raise ProviderRuntimeError(
                    "Responses message ended before it started",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            ended_item_ids.add(item_id)
            self._stream_emit_event(
                context,
                "assistant.message.end",
                {"item_id": item_id},
                turn_index=turn_index,
            )

        def start_reasoning(item_id: str) -> None:
            if not isinstance(item_id, str) or not item_id:
                raise ProviderRuntimeError(
                    "Responses reasoning delta is missing item_id",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if item_id in started_reasoning_item_ids:
                return
            started_reasoning_item_ids.add(item_id)
            self._stream_emit_event(
                context,
                "assistant.reasoning.start",
                {"item_id": item_id},
                turn_index=turn_index,
            )

        def end_reasoning(item_id: Optional[str] = None) -> None:
            pending = started_reasoning_item_ids - ended_reasoning_item_ids
            selected = sorted(pending) if item_id is None else [item_id]
            for reasoning_id in selected:
                if reasoning_id not in pending:
                    continue
                ended_reasoning_item_ids.add(reasoning_id)
                self._stream_emit_event(
                    context,
                    "assistant.reasoning.end",
                    {"item_id": reasoning_id},
                    turn_index=turn_index,
                )

        def start_tool(item_id: str, item: Any, output_index: int) -> Dict[str, Any]:
            nonlocal output_emitted
            if (
                not isinstance(item_id, str)
                or not item_id
                or not isinstance(output_index, int)
                or isinstance(output_index, bool)
                or output_index < 0
            ):
                raise ProviderRuntimeError(
                    "Malformed Responses function-call item",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            state = tool_states.get(item_id)
            if item is not None:
                item_type = self._get_attr(item, "type")
                item_identifier = self._get_attr(item, "id")
                call_id = self._get_attr(item, "call_id")
                name = self._get_attr(item, "name")
                if (
                    item_type != "function_call"
                    or (
                        item_identifier is not None
                        and item_identifier != item_id
                    )
                    or not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not name
                ):
                    raise ProviderRuntimeError(
                        "Malformed Responses function-call item",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "invalid_responses_event"},
                    )
                if state is not None and (
                    state["call_id"] != call_id or state["tool"] != name
                ):
                    raise ProviderRuntimeError(
                        "Responses function-call identity changed",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "invalid_responses_event"},
                    )
                if state is None:
                    state = {
                        "index": output_index,
                        "call_id": call_id,
                        "tool": name,
                        "arguments": "",
                    }
                    tool_states[item_id] = state
            elif state is None:
                raise ProviderRuntimeError(
                    "Responses function-call delta preceded item start",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if state["index"] != output_index:
                raise ProviderRuntimeError(
                    "Responses function-call index changed",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                )
            if not state.get("started"):
                state["started"] = True
                output_emitted = True
                self._stream_emit_event(
                    context,
                    "assistant.tool_call.start",
                    {
                        "item_id": item_id,
                        "index": state["index"],
                        "call_id": state["call_id"],
                        "tool": state["tool"],
                    },
                    turn_index=turn_index,
                )
            return state

        def end_tool(item_id: str, item: Any, output_index: int) -> None:
            if item_id in ended_tool_item_ids:
                return
            state = start_tool(item_id, item, output_index)
            arguments = (
                self._get_attr(item, "arguments") if item is not None else None
            )
            if arguments is not None:
                if not isinstance(arguments, str):
                    raise ProviderRuntimeError(
                        "Malformed Responses function-call arguments",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "invalid_responses_event"},
                    )
                state["arguments"] = arguments
            try:
                call = ProviderToolCall(
                    id=state["call_id"],
                    name=state["tool"],
                    arguments=state["arguments"],
                )
                call.as_dict()
            except ProviderContractError:
                raise ProviderRuntimeError(
                    "Malformed Responses function-call arguments",
                    kind="protocol",
                    output_emitted=output_emitted,
                    details={"code": "invalid_responses_event"},
                ) from None
            ended_tool_item_ids.add(item_id)
            self._stream_emit_event(
                context,
                "assistant.tool_call.end",
                {
                    "item_id": item_id,
                    "index": state["index"],
                    "call_id": state["call_id"],
                    "tool": state["tool"],
                    "arguments": call.arguments_json,
                },
                turn_index=turn_index,
            )

        try:
            with stream_ctx as stream:
                for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "response.output_text.delta":
                        item_id = getattr(event, "item_id", None)
                        delta = getattr(event, "delta", None)
                        emit_delta(item_id, delta)
                    elif event_type == "response.output_text.done":
                        item_id = getattr(event, "item_id", None)
                        text = getattr(event, "text", None)
                        if not isinstance(text, str):
                            raise ProviderRuntimeError(
                                "Malformed Responses text completion",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        if item_id not in started_item_ids:
                            start_item(item_id)
                            if text:
                                emit_delta(item_id, text)
                        end_item(item_id)
                    elif event_type in {
                        "response.reasoning_text.delta",
                        "response.reasoning.delta",
                        "response.reasoning_summary_text.delta",
                    }:
                        delta = getattr(event, "delta", None)
                        item_id = getattr(event, "item_id", None)
                        if not isinstance(delta, str) or not delta:
                            raise ProviderRuntimeError(
                                "Malformed Responses reasoning delta",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        start_reasoning(item_id)
                        output_emitted = True
                        self._stream_emit_event(
                            context,
                            (
                                "assistant.thought_summary.delta"
                                if event_type
                                == "response.reasoning_summary_text.delta"
                                else "assistant.reasoning.delta"
                            ),
                            {"item_id": item_id, "delta": delta},
                            turn_index=turn_index,
                        )
                    elif event_type == "response.output_item.added":
                        item = getattr(event, "item", None)
                        item_type = self._get_attr(item, "type")
                        item_id = self._get_attr(item, "id")
                        event_item_id = getattr(event, "item_id", None)
                        output_index = getattr(event, "output_index", None)
                        if (
                            item_type not in {"message", "reasoning", "function_call"}
                            or not isinstance(item_id, str)
                            or not item_id
                            or (
                                event_item_id is not None
                                and event_item_id != item_id
                            )
                            or not isinstance(output_index, int)
                            or isinstance(output_index, bool)
                            or output_index < 0
                            or item_id in output_item_types
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Responses output item start",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        output_item_types[item_id] = (item_type, output_index)
                        if item_type == "function_call":
                            start_tool(item_id, item, output_index)
                        elif item_type == "message":
                            start_item(item_id)
                        else:
                            start_reasoning(item_id)
                    elif event_type == "response.function_call_arguments.delta":
                        item_id = getattr(event, "item_id", None)
                        delta = getattr(event, "delta", None)
                        output_index = getattr(event, "output_index", None)
                        if (
                            not isinstance(item_id, str)
                            or not item_id
                            or not isinstance(delta, str)
                            or not delta
                            or not isinstance(output_index, int)
                            or isinstance(output_index, bool)
                            or output_index < 0
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Responses function-call argument delta",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        state = start_tool(item_id, None, output_index)
                        state["arguments"] += delta
                        output_emitted = True
                        self._stream_emit_event(
                            context,
                            "assistant.tool_call.delta",
                            {
                                "item_id": item_id,
                                "index": state["index"],
                                "call_id": state["call_id"],
                                "tool": state.get("tool"),
                                "arguments_delta": delta,
                            },
                            turn_index=turn_index,
                        )
                    elif event_type == "response.function_call_arguments.done":
                        item_id = getattr(event, "item_id", None)
                        arguments = getattr(event, "arguments", None)
                        output_index = getattr(event, "output_index", None)
                        state = (
                            tool_states.get(item_id)
                            if isinstance(item_id, str)
                            else None
                        )
                        if (
                            state is None
                            or not isinstance(arguments, str)
                            or not isinstance(output_index, int)
                            or isinstance(output_index, bool)
                            or output_index < 0
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Responses function-call completion",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        state["arguments"] = arguments
                        end_tool(item_id, None, output_index)
                    elif event_type == "response.output_item.done":
                        item = getattr(event, "item", None)
                        item_type = self._get_attr(item, "type")
                        item_id = self._get_attr(item, "id")
                        event_item_id = getattr(event, "item_id", None)
                        output_index = getattr(event, "output_index", None)
                        if (
                            item_type not in {"message", "reasoning", "function_call"}
                            or not isinstance(item_id, str)
                            or not item_id
                            or (
                                event_item_id is not None
                                and event_item_id != item_id
                            )
                            or not isinstance(output_index, int)
                            or isinstance(output_index, bool)
                            or output_index < 0
                            or item_id in completed_output_item_ids
                            or (
                                item_id in output_item_types
                                and output_item_types[item_id]
                                != (item_type, output_index)
                            )
                        ):
                            raise ProviderRuntimeError(
                                "Malformed Responses output item completion",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        output_item_types.setdefault(
                            item_id, (item_type, output_index)
                        )
                        completed_output_item_ids.add(item_id)
                        if item_type == "function_call":
                            end_tool(item_id, item, output_index)
                        elif item_type == "message":
                            start_item(item_id)
                            end_item(item_id)
                        else:
                            start_reasoning(item_id)
                            end_reasoning(item_id)
                    elif event_type in {
                        "response.completed",
                        "response.incomplete",
                    }:
                        if response_terminal is not None:
                            raise ProviderRuntimeError(
                                "Duplicate Responses terminal event",
                                kind="protocol",
                                output_emitted=output_emitted,
                                details={"code": "invalid_responses_event"},
                            )
                        response_terminal = (
                            "completed"
                            if event_type == "response.completed"
                            else "incomplete"
                        )
                        for item_id in started_item_ids - ended_item_ids:
                            end_item(item_id)
                        end_reasoning()
                        for item_id, state in list(tool_states.items()):
                            if item_id not in ended_tool_item_ids:
                                end_tool(
                                    item_id,
                                    None,
                                    int(state.get("index", 0) or 0),
                                )
                    elif event_type == "response.failed":
                        raise ProviderRuntimeError(
                            "Responses stream failed",
                            kind="provider",
                            output_emitted=output_emitted,
                            details={"code": "provider_response_failed"},
                        )
                    elif event_type in {
                        "response.created",
                        "response.in_progress",
                        "response.queued",
                        "response.content_part.added",
                        "response.content_part.done",
                        "response.reasoning_summary_part.added",
                        "response.reasoning_summary_part.done",
                    }:
                        continue
                    else:
                        raise ProviderRuntimeError(
                            "Unknown normative Responses event",
                            kind="protocol",
                            output_emitted=output_emitted,
                            details={"code": "unknown_responses_event"},
                        )
                if response_terminal is None:
                    raise ProviderRuntimeError(
                        "Responses stream omitted terminal event",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "missing_responses_terminal"},
                    )
                if (
                    started_item_ids != ended_item_ids
                    or started_reasoning_item_ids != ended_reasoning_item_ids
                    or set(tool_states) != ended_tool_item_ids
                ):
                    raise ProviderRuntimeError(
                        "Responses stream ended with incomplete items",
                        kind="protocol",
                        output_emitted=output_emitted,
                        details={"code": "invalid_responses_event"},
                    )
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
        except ProviderRuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - wrapped as runtime error
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

    def _normalize_output_message_content(self, content: Any) -> Any:
        if content is None:
            return []
        if isinstance(content, str):
            return content
        if not isinstance(content, (list, tuple)):
            raise ProviderRuntimeError(
                "Malformed Responses message content",
                kind="protocol",
                details={"code": "invalid_responses_content"},
            )
        blocks: List[Dict[str, Any]] = []
        for block in content:
            block_type = self._get_attr(block, "type")
            if block_type in {"output_text", "text", "summary_text", "reasoning_text"}:
                value = self._get_attr(block, "text")
            elif block_type == "refusal":
                value = self._get_attr(block, "refusal", self._get_attr(block, "text"))
            else:
                raise ProviderRuntimeError(
                    "Unknown Responses message content",
                    kind="protocol",
                    details={"code": "unknown_responses_content"},
                )
            if not isinstance(value, str):
                raise ProviderRuntimeError(
                    "Malformed Responses message content",
                    kind="protocol",
                    details={"code": "invalid_responses_content"},
                )
            blocks.append({"type": "text", "text": value})
        return "".join(block["text"] for block in blocks)

    def _request_payload(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        context: ProviderRuntimeContext,
    ) -> Dict[str, Any]:
        instructions, input_messages = self._split_messages_for_responses(
            messages, context
        )
        provider_tools_cfg = context.agent_config.get("provider_tools") or {}
        provider_cfg: Dict[str, Any] = {}
        if isinstance(provider_tools_cfg, dict):
            provider_specific = provider_tools_cfg.get(self.descriptor.provider_id)
            if isinstance(provider_specific, dict):
                provider_cfg = provider_specific
            else:
                openai_specific = provider_tools_cfg.get("openai")
                if isinstance(
                    openai_specific, dict
                ) and self.descriptor.provider_id in ("openai", "openrouter"):
                    provider_cfg = openai_specific
                else:
                    provider_cfg = provider_tools_cfg
        responses_stateful = bool(provider_cfg.get("responses_stateful", True))
        has_state_reference = bool(
            context.session_state.get_provider_metadata("conversation_id")
            or context.session_state.get_provider_metadata("previous_response_id")
        )
        include_tool_calls = (
            self.descriptor.provider_id == "openrouter"
            or not responses_stateful
            or not has_state_reference
        )
        payload: Dict[str, Any] = {
            "model": model,
            "input": self._convert_messages_to_input(
                input_messages,
                include_tool_calls=include_tool_calls,
                context=context,
            ),
        }
        if instructions:
            payload["instructions"] = instructions

        responses_tools = self._convert_tools_to_responses(tools)
        if responses_tools:
            payload["tools"] = responses_tools
        if (
            self.descriptor.provider_id == "openrouter"
            and isinstance(model, str)
            and model.startswith("openai/gpt-5")
        ):
            extra_body = dict(payload.get("extra_body") or {})
            extra_body.setdefault(
                "provider", {"order": ["openai"], "allow_fallbacks": False}
            )
            payload["extra_body"] = extra_body

        include_items: List[str] = list(provider_cfg.get("include", []))
        if (
            provider_cfg.get("include_reasoning", True)
            and "reasoning.encrypted_content" not in include_items
        ):
            include_items.append("reasoning.encrypted_content")
        if include_items:
            payload["include"] = include_items

        if "store" in provider_cfg:
            payload["store"] = bool(provider_cfg.get("store"))

        reasoning_cfg = provider_cfg.get("reasoning")
        if isinstance(reasoning_cfg, dict) and reasoning_cfg:
            payload["reasoning"] = dict(reasoning_cfg)

        tool_choice_cfg = provider_cfg.get("tool_choice")
        if tool_choice_cfg is not None and responses_tools:
            resolved_choice: Any = tool_choice_cfg
            if isinstance(tool_choice_cfg, str):
                lowered = tool_choice_cfg.strip().lower()
                if lowered == "auto":
                    resolved_choice = "auto"
                elif lowered in {"required", "force", "any"}:
                    resolved_choice = "required"
                elif lowered in {"none", "off"}:
                    resolved_choice = "none"
            if resolved_choice is not None:
                payload["tool_choice"] = resolved_choice

        if responses_stateful:
            conversation_id = context.session_state.get_provider_metadata(
                "conversation_id"
            )
            if conversation_id:
                payload["conversation"] = conversation_id

            previous_response_id = context.session_state.get_provider_metadata(
                "previous_response_id"
            )
            if previous_response_id:
                payload["previous_response_id"] = previous_response_id

        extra_payload = context.extra.get("responses_extra") if context.extra else None
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)
        payload.update(openai_responses_role_options(context))
        return payload

    def project_request_body(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> Dict[str, Any]:
        """Project the complete secret-free HTTP request body for evidence."""
        payload = self._request_payload(
            model=model,
            messages=messages,
            tools=tools,
            context=context,
        )
        if stream:
            payload["stream"] = True
        return payload
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
        payload = self._request_payload(
            model=model,
            messages=messages,
            tools=tools,
            context=context,
        )

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
                raise ProviderRuntimeError(
                    redaction.safe_exception_message(exc)
                ) from None

        normalized_messages: List[ProviderMessage] = []
        encrypted_reasoning: List[Any] = []
        reasoning_summaries: List[str] = []
        reasoning_blocks: List[Dict[str, Any]] = []

        response_status = self._get_attr(response, "status", None)
        normalized_status = (
            response_status.lower() if isinstance(response_status, str) else None
        )
        response_output = getattr(response, "output", []) or []
        if normalized_status in {"failed", "error"}:
            raise ProviderRuntimeError(
                "Responses request failed",
                kind="provider",
                output_emitted=bool(response_output),
                details={"code": "provider_response_failed"},
            )
        if normalized_status in {"cancelled", "canceled"}:
            raise ProviderRuntimeError(
                "Responses request cancelled",
                kind="provider",
                output_emitted=bool(response_output),
                details={
                    "code": "provider_response_cancelled",
                    "cancelled": True,
                    "cancel_owner": "provider",
                },
            )
        if normalized_status in {"completed", "succeeded", "complete"}:
            default_finish_reason = "stop"
        elif normalized_status in {"incomplete", "truncated"}:
            default_finish_reason = "length"
        elif normalized_status is None:
            raise ProviderRuntimeError(
                "Responses terminal status is missing",
                kind="protocol",
                output_emitted=bool(response_output),
                details={"code": "invalid_responses_status"},
            )
        else:
            raise ProviderRuntimeError(
                "Unknown Responses terminal status",
                kind="protocol",
                output_emitted=bool(response_output),
                details={"code": "unknown_responses_status"},
            )

        seen_output_item_ids: set[str] = set()
        for idx, item in enumerate(response_output):
            item_type = self._get_attr(item, "type")
            item_id = self._get_attr(item, "id")
            if item_type not in {"message", "function_call", "reasoning"}:
                raise ProviderRuntimeError(
                    "Unknown Responses output item",
                    kind="protocol",
                    output_emitted=bool(normalized_messages),
                    details={"code": "unknown_responses_output"},
                )
            if not isinstance(item_id, str) or not item_id:
                raise ProviderRuntimeError(
                    "Malformed Responses output item",
                    kind="protocol",
                    output_emitted=bool(normalized_messages),
                    details={"code": "invalid_responses_output"},
                )
            if item_id in seen_output_item_ids:
                raise ProviderRuntimeError(
                    "Duplicate Responses output item",
                    kind="protocol",
                    output_emitted=bool(normalized_messages),
                    details={"code": "invalid_responses_output"},
                )
            seen_output_item_ids.add(item_id)


            if item_type == "message":
                role = self._get_attr(item, "role", "assistant")
                if role != "assistant":
                    raise ProviderRuntimeError(
                        "Responses output message has invalid role",
                        kind="protocol",
                        output_emitted=bool(normalized_messages),
                        details={"code": "invalid_responses_output"},
                    )
                content = self._normalize_output_message_content(
                    self._get_attr(item, "content", [])
                )
                finish_reason = (
                    self._get_attr(item, "finish_reason", None) or default_finish_reason
                )
                normalized_messages.append(
                    ProviderMessage(
                        role=role,
                        content=content,
                        finish_reason=finish_reason,
                        index=idx,
                        raw_message=item,
                        annotations={"responses_type": item_type},
                        message_id=item_id,
                    )
                )
            elif item_type == "function_call":
                call_id = self._get_attr(item, "call_id")
                name = self._get_attr(item, "name")
                arguments = self._get_attr(item, "arguments")
                if arguments is None:
                    raise ProviderRuntimeError(
                        "Malformed Responses function-call arguments",
                        kind="protocol",
                        output_emitted=bool(normalized_messages),
                        details={"code": "invalid_responses_output"},
                    )
                try:
                    tool_call = ProviderToolCall(
                        id=call_id,
                        name=name,
                        arguments=arguments,
                        type="function",
                        raw=item,
                    )
                    tool_call.as_dict()
                except ProviderContractError:
                    raise ProviderRuntimeError(
                        "Malformed Responses function-call item",
                        kind="protocol",
                        output_emitted=bool(normalized_messages),
                        details={"code": "invalid_responses_output"},
                    ) from None
                normalized_messages.append(
                    ProviderMessage(
                        role="assistant",
                        content=None,
                        tool_calls=[tool_call],
                        finish_reason=self._get_attr(item, "finish_reason", None)
                        or "toolUse",
                        index=idx,
                        raw_message=item,
                        annotations={"responses_type": item_type},
                        message_id=item_id,
                    )
                )
            elif item_type == "reasoning":
                item_id = item_id
                encrypted = self._get_attr(item, "encrypted_content")
                summary_blocks = self._get_attr(item, "summary", []) or []
                summary_content = self._normalize_output_message_content(summary_blocks)
                normalized_blocks = (
                    list(summary_content)
                    if isinstance(summary_content, list)
                    else [{"type": "text", "text": summary_content}]
                )
                for block in normalized_blocks:
                    thinking_block = {"type": "thinking", "text": block["text"]}
                    reasoning_blocks.append(thinking_block)
                    if block["text"]:
                        reasoning_summaries.append(block["text"])
                if encrypted is not None:
                    replay_payload: Dict[str, Any] = {"encrypted_content": encrypted}
                    if item_id:
                        replay_payload["item_id"] = item_id
                        replay_payload["reasoning_id"] = item_id
                    encrypted_reasoning.append(replay_payload)
                    reasoning_blocks.append(
                        {
                            "type": "provider_replay",
                            "provider_id": self.descriptor.provider_id,
                            "schema_version": "openai.responses.v1",
                            "replay_scope": "same_provider",
                            "payload": replay_payload,
                        }
                    )
            else:
                raise ProviderRuntimeError(
                    "Unknown Responses output item",
                    kind="protocol",
                    output_emitted=bool(normalized_messages),
                    details={"code": "unknown_responses_output"},
                )

        usage_dict = self._extract_usage(response)

        metadata: Dict[str, Any] = {}
        response_id = getattr(response, "id", None)
        if response_id:
            metadata["previous_response_id"] = response_id
        if isinstance(response_status, str) and response_status:
            metadata["raw_finish_reason"] = response_status
        conversation_obj = getattr(response, "conversation", None)
        conversation_id_out = (
            getattr(conversation_obj, "id", None) if conversation_obj else None
        )
        if conversation_id_out:
            metadata["conversation_id"] = conversation_id_out

        return ProviderResult(
            messages=normalized_messages,
            raw_response=response,
            usage=usage_dict,
            encrypted_reasoning=encrypted_reasoning or None,
            reasoning_summaries=reasoning_summaries or None,
            reasoning_blocks=reasoning_blocks or None,
            model=getattr(response, "model", None),
            metadata=metadata,
        )
