"""OpenAI Responses API runtime."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderToolCall,
)
from ....logging.provider_dump import provider_dump_logger
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
    ) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        tool_output_call_ids: set[str] = set()
        effective_include_tool_calls = include_tool_calls or self.descriptor.provider_id == "openrouter"
        if effective_include_tool_calls:
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
            if effective_include_tool_calls and role_lower == "assistant":
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
            metadata={
                "phase_label": (context.extra or {}).get("phase16_phase_label"),
                "turn_index": (context.extra or {}).get("turn_index"),
            },
        )

        try:
            stream_ctx = client.responses.stream(**payload)
        except (AttributeError, TypeError) as exc:
            raise ProviderRuntimeError(
                "OpenAI SDK Responses streaming adapter failure", kind="adapter"
            ) from exc
        except Exception as exc:  # pragma: no cover - wrapped as runtime error
            kind = (
                "transport"
                if exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}
                else "provider"
            )
            raise ProviderRuntimeError(str(exc), kind=kind) from exc

        session_state = getattr(context, "session_state", None)
        turn_index = getattr(session_state, "_active_turn_index", None)
        started_item_ids: set[str] = set()
        ended_item_ids: set[str] = set()
        output_emitted = False
        tool_states: Dict[str, Dict[str, Any]] = {}
        ended_tool_item_ids: set[str] = set()

        def start_item(item_id: str) -> None:
            if not item_id or item_id in started_item_ids:
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
            if not item_id or not isinstance(delta, str) or not delta:
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
            if (
                not item_id
                or item_id in ended_item_ids
                or item_id not in started_item_ids
            ):
                return
            ended_item_ids.add(item_id)
            self._stream_emit_event(
                context,
                "assistant.message.end",
                {"item_id": item_id},
                turn_index=turn_index,
            )

        def start_tool(item_id: str, item: Any, output_index: int) -> Dict[str, Any]:
            nonlocal output_emitted
            state = tool_states.setdefault(
                item_id,
                {
                    "index": output_index,
                    "call_id": self._get_attr(item, "call_id") or item_id,
                    "tool": self._get_attr(item, "name"),
                    "arguments": "",
                },
            )
            if not state.get("started"):
                state["started"] = True
                output_emitted = True
                self._stream_emit_event(
                    context,
                    "assistant.tool_call.start",
                    {
                        "index": state["index"],
                        "call_id": state["call_id"],
                        "tool": state.get("tool"),
                    },
                    turn_index=turn_index,
                )
            return state

        def end_tool(item_id: str, item: Any, output_index: int) -> None:
            if item_id in ended_tool_item_ids:
                return
            state = start_tool(item_id, item, output_index)
            arguments = self._get_attr(item, "arguments")
            if isinstance(arguments, str):
                state["arguments"] = arguments
            ended_tool_item_ids.add(item_id)
            self._stream_emit_event(
                context,
                "assistant.tool_call.end",
                {
                    "index": state["index"],
                    "call_id": state["call_id"],
                    "tool": state.get("tool"),
                    "arguments": state.get("arguments", ""),
                },
                turn_index=turn_index,
            )

        try:
            with stream_ctx as stream:
                for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "response.output_text.delta":
                        item_id = str(getattr(event, "item_id", "") or "")
                        delta = getattr(event, "delta", None)
                        if isinstance(delta, str) and delta:
                            emit_delta(item_id, delta)
                    elif event_type == "response.output_text.done":
                        item_id = str(getattr(event, "item_id", "") or "")
                        text = getattr(event, "text", None)
                        if (
                            item_id not in started_item_ids
                            and isinstance(text, str)
                            and text
                        ):
                            emit_delta(item_id, text)
                        end_item(item_id)
                    elif event_type in {
                        "response.reasoning_text.delta",
                        "response.reasoning.delta",
                    }:
                        delta = getattr(event, "delta", None)
                        item_id = str(getattr(event, "item_id", "") or "")
                        if isinstance(delta, str) and delta:
                            output_emitted = True
                            self._stream_emit_event(
                                context,
                                "assistant.reasoning.delta",
                                {"item_id": item_id, "delta": delta},
                                turn_index=turn_index,
                            )
                    elif event_type == "response.reasoning_summary_text.delta":
                        delta = getattr(event, "delta", None)
                        item_id = str(getattr(event, "item_id", "") or "")
                        if isinstance(delta, str) and delta:
                            output_emitted = True
                            self._stream_emit_event(
                                context,
                                "assistant.thought_summary.delta",
                                {"item_id": item_id, "delta": delta},
                                turn_index=turn_index,
                            )
                    elif event_type == "response.output_item.added":
                        item = getattr(event, "item", None)
                        if self._get_attr(item, "type") == "function_call":
                            item_id = str(
                                self._get_attr(item, "id")
                                or getattr(event, "item_id", "")
                                or ""
                            )
                            start_tool(
                                item_id,
                                item,
                                int(getattr(event, "output_index", 0) or 0),
                            )
                    elif event_type == "response.function_call_arguments.delta":
                        item_id = str(getattr(event, "item_id", "") or "")
                        delta = getattr(event, "delta", None)
                        if item_id and isinstance(delta, str) and delta:
                            state = start_tool(
                                item_id,
                                None,
                                int(getattr(event, "output_index", 0) or 0),
                            )
                            state["arguments"] += delta
                            output_emitted = True
                            self._stream_emit_event(
                                context,
                                "assistant.tool_call.delta",
                                {
                                    "index": state["index"],
                                    "call_id": state["call_id"],
                                    "tool": state.get("tool"),
                                    "arguments_delta": delta,
                                },
                                turn_index=turn_index,
                            )
                    elif event_type in {
                        "response.function_call_arguments.done",
                        "response.output_item.done",
                    }:
                        item = getattr(event, "item", None)
                        if (
                            event_type == "response.function_call_arguments.done"
                            or self._get_attr(item, "type") == "function_call"
                        ):
                            item_id = str(
                                getattr(event, "item_id", "")
                                or self._get_attr(item, "id")
                                or ""
                            )
                            if (
                                event_type == "response.function_call_arguments.done"
                                and item is None
                            ):
                                item = {
                                    "arguments": getattr(event, "arguments", ""),
                                    "call_id": tool_states.get(item_id, {}).get(
                                        "call_id"
                                    ),
                                    "name": tool_states.get(item_id, {}).get("tool"),
                                }
                            end_tool(
                                item_id,
                                item,
                                int(getattr(event, "output_index", 0) or 0),
                            )
                    elif event_type == "response.completed":
                        for item_id in list(started_item_ids):
                            end_item(item_id)
                        for item_id, state in list(tool_states.items()):
                            end_tool(item_id, state, int(state.get("index", 0) or 0))
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
                str(exc), kind=kind, output_emitted=output_emitted
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

        # Pass provider-tools reasoning config (e.g. {"effort": "high"})
        # through to the Responses API verbatim.
        reasoning_cfg = provider_cfg.get("reasoning")
        if isinstance(reasoning_cfg, dict) and reasoning_cfg:
            payload["reasoning"] = dict(reasoning_cfg)

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

