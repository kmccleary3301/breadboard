from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from ..core.core import ToolDefinition
from ..compilation.system_prompt_compiler import get_compiler
from ..messaging.markdown_logger import MarkdownLogger
from ..provider.ir import IRDeltaEvent
from ..provider.routing import provider_router
from ..provider import provider_adapter_manager, sanitize_openai_tool_name
from ..provider.contracts import (
    ProviderContractError,
    ProviderResult,
    ProviderRuntimeContext,
    sanitize_provider_result,
)
from ..provider import normalize_provider_result
from .components import (
    apply_streaming_policy_for_turn,
    apply_cache_control_to_initial_user_prompt,
    apply_cache_control_to_tool_messages,
    get_prompt_cache_control,
    log_routing_event,
)
from ..surface import record_tool_schema_snapshot
from ..security import redaction


def _record_raw_provider_response(
    conductor: Any,
    result: ProviderResult,
    turn_index: int,
) -> None:
    """Persist only the normalized, secret-safe provider transport payload."""
    try:
        if not conductor.logger_v2.include_raw:
            return
        sanitized = sanitize_provider_result(result)
        if isinstance(sanitized.raw_response, dict):
            conductor.api_recorder.save_response(
                turn_index,
                sanitized.raw_response,
            )
    except Exception:
        pass

def _bind_episode_provider_profile(
    episode: Any,
    runtime: Any,
    client: Any,
    model: str,
    stream: bool,
) -> Tuple[Any, bool, Any]:
    profile = getattr(episode, "_episode_provider_profile", None)
    if profile is None:
        return client, stream, None
    if (
        getattr(runtime.descriptor, "provider_id", None) != profile.provider_id
        or getattr(runtime.descriptor, "runtime_id", None) != profile.runtime_id
        or model != profile.model
    ):
        raise ProviderContractError(
            "episode provider profile does not match the selected route"
        )
    profile_client = getattr(episode, "_episode_provider_client", None)
    if profile_client is None:
        profile_client = runtime.create_client_from_profile(profile)
        episode._episode_provider_client = profile_client
    return profile_client, True, profile


def _provider_wire_evidence(
    *,
    profile: Any,
    runtime: Any,
    provider_id: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    stream: bool,
    client_config: Dict[str, Any],
    context: ProviderRuntimeContext,
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
    if profile is not None:
        profile_identity = profile.identity_dict()
        request_headers = {"Authorization": redaction.REDACTED}
        request_headers.update(
            {name: redaction.REDACTED for name in profile.caller_headers}
        )
        secret_values = [
            profile.scoped_credential,
            *profile.caller_headers.values(),
        ]
        with redaction.secret_value_scope(*secret_values, allow_short=True):
            request_body, _redaction_problems = redaction.scrub_structure(
                runtime.profile_chat_request(
                    profile,
                    messages,
                    tools,
                    context=context,
                ),
                path="$.provider_request",
            )
        if not isinstance(request_body, dict):
            raise ProviderContractError(
                "profile request evidence must remain an object after redaction"
            )
        return (
            request_body,
            request_headers,
            f"sha256:{profile_identity['base_url_sha256']}",
            profile_identity,
        )
    try:
        request_headers = dict(client_config.get("default_headers") or {})
        if provider_id == "openrouter":
            request_headers.setdefault("Accept", "application/json; charset=utf-8")
            request_headers.setdefault("Accept-Encoding", "identity")
        endpoint = client_config.get("base_url")
    except Exception:
        request_headers = {}
        endpoint = None
    return (
        {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": stream,
        },
        request_headers,
        endpoint,
        None,
    )



def get_model_response(
    conductor: Any,
    runtime,
    client,
    model: str,
    tool_prompt_mode: str,
    tool_defs: List[ToolDefinition],
    active_dialect_names: List[str],
    session_state: Any,
    markdown_logger: MarkdownLogger,
    stream_responses: bool,
    local_tools_prompt: str,
    client_config: Dict[str, Any],
) -> Any:
    """Get response from the model with proper tool configuration."""
    turn_index = len(session_state.transcript) + 1
    try:
        session_state.begin_turn(turn_index)
    except Exception:
        session_state.set_provider_metadata("current_turn_index", turn_index)
    session_state.set_provider_metadata("loop_detection_payload", None)
    session_state.set_provider_metadata("context_window_warning", None)
    try:
        conductor.loop_detector.turn_started()
    except Exception:
        pass
    try:
        injector = getattr(conductor, "_inject_multi_agent_wakeups", None)
        if callable(injector):
            injector(session_state, markdown_logger)
    except Exception:
        pass

    send_messages = copy.deepcopy(session_state.provider_messages)
    cache_control = get_prompt_cache_control({"provider_tools": getattr(conductor, "_provider_tools_effective", None) or (conductor.config.get("provider_tools") or {})})
    if cache_control:
        apply_cache_control_to_initial_user_prompt(send_messages, cache_control)
        # Anthropic only allows up to 4 cache_control blocks per request.
        # Applying cache_control to every tool_use/tool_result block can exceed this and cause a hard 400.
        route_hint_for_cache = getattr(conductor, "_current_route_id", None) or model
        try:
            provider_id_for_cache = provider_router.parse_model_id(route_hint_for_cache)[0]
        except Exception:
            provider_id_for_cache = None
        if provider_id_for_cache != "anthropic":
            apply_cache_control_to_tool_messages(send_messages, cache_control)

    stub_text = ""
    try:
        descriptor = getattr(runtime, "descriptor", None)
        if (
            getattr(descriptor, "runtime_id", None) == "openai_responses" or getattr(descriptor, "default_api_variant", None) == "responses"
        ):
            stub_text = "Continue."
    except Exception:
        pass

    if not send_messages:
        send_messages.append({"role": "user", "content": stub_text})
    elif send_messages[-1].get("role") != "user":
        send_messages.append({"role": "user", "content": stub_text})
    else:
        last_content = send_messages[-1].get("content")
        if isinstance(last_content, str) and not last_content.strip() and stub_text:
            send_messages[-1]["content"] = stub_text

    per_turn_written_text = conductor.tool_prompt_planner.plan(
        tool_prompt_mode=tool_prompt_mode,
        send_messages=send_messages,
        session_state=session_state,
        tool_defs=tool_defs,
        active_dialect_names=active_dialect_names,
        local_tools_prompt=local_tools_prompt,
        markdown_logger=markdown_logger,
        append_text_block=conductor._append_text_block,
        current_native_tools=getattr(conductor, "current_native_tools", None),
    )

    try:
        if per_turn_written_text and conductor.logger_v2.run_dir:
            rel = conductor.prompt_logger.save_per_turn(turn_index, per_turn_written_text)
            conductor._register_prompt_hash("per_turn", per_turn_written_text, turn_index)
            conductor.logger_v2.append_text(
                "conversation/conversation.md",
                conductor.md_writer.tools_available_temp("Per-turn tools prompt appended.", rel),
            )
    except Exception:
        pass

    session_state.add_transcript_entry({
        "tools_context": {
            "available_tools": conductor._dump_tool_defs(tool_defs),
            "compiled_tools_prompt": local_tools_prompt,
        }
    })

    try:
        context_payload = conductor.context_guard.maybe_warn(session_state, send_messages)
        if context_payload:
            session_state.set_provider_metadata("context_window_warning", context_payload)
    except Exception:
        pass

    provider_tools_cfg = getattr(conductor, "_provider_tools_effective", None) or dict((conductor.config.get("provider_tools") or {}))
    phase16_tool_choice = session_state.get_provider_metadata("phase16_provider_tool_choice")
    if phase16_tool_choice is not None:
        provider_tools_cfg = dict(provider_tools_cfg)
        provider_tools_cfg["tool_choice"] = phase16_tool_choice
    effective_config = dict(conductor.config)
    effective_config["provider_tools"] = provider_tools_cfg
    conductor._provider_tools_effective = provider_tools_cfg
    route_hint = getattr(conductor, "_current_route_id", None) or model
    use_native_tools = provider_router.should_use_native_tools(route_hint, effective_config)
    if use_native_tools and not getattr(conductor, "current_native_tools", None):
        try:
            conductor._setup_native_tools(route_hint, True)
        except Exception:
            pass
    tools_schema = None
    provider_id: Optional[str] = None
    allowed_tool_names = set(getattr(conductor, "_active_tool_names", []) or [])
    if use_native_tools and getattr(conductor, "current_native_tools", None):
        try:
            provider_id = provider_router.parse_model_id(route_hint)[0]
            native_tools = getattr(conductor, "current_native_tools", [])
            if allowed_tool_names:
                native_tools = [
                    tool for tool in native_tools
                    if getattr(tool, "name", None) in allowed_tool_names
                ]
            hide_invalid = True
            try:
                hide_flag = provider_tools_cfg.get("hide_invalid_tool")
                if isinstance(hide_flag, bool):
                    hide_invalid = hide_flag
            except Exception:
                hide_invalid = True
            if hide_invalid:
                native_tools = [tool for tool in native_tools if getattr(tool, "name", None) != "invalid"]
            if native_tools:
                tools_schema = (
                    provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id
                    )
                )
                try:
                    if conductor.logger_v2.run_dir and tools_schema:
                        conductor.provider_logger.save_tools_provided(turn_index, tools_schema)
                        ids = []
                        try:
                            for it in tools_schema:
                                fn = (it.get("function") or {}).get("name")
                                if fn:
                                    ids.append(str(fn))
                        except Exception:
                            pass
                        conductor.logger_v2.append_text(
                            "conversation/conversation.md",
                            conductor.md_writer.provider_tools_provided(
                                ids or ["(see JSON)"],
                                f"provider_native/tools_provided/turn_{turn_index}.json",
                            ),
                        )
                except Exception:
                    pass
        except Exception:
            tools_schema = None
    if tools_schema is None:
        try:
            provider_id = provider_id or provider_router.parse_model_id(route_hint)[0]
        except Exception:
            provider_id = None
    if tools_schema is None and provider_id == "anthropic":
        try:
            source_defs = getattr(conductor, "yaml_tools", None) or []
            native_tools, text_based_tools = (
                provider_adapter_manager.filter_tools_for_provider(source_defs, provider_id
                )
            )
            if allowed_tool_names:
                native_tools = [
                    tool for tool in native_tools
                    if getattr(tool, "name", None) in allowed_tool_names
                ]
            hide_invalid = True
            try:
                hide_flag = provider_tools_cfg.get("hide_invalid_tool")
                if isinstance(hide_flag, bool):
                    hide_invalid = hide_flag
            except Exception:
                hide_invalid = True
            if hide_invalid:
                native_tools = [tool for tool in native_tools if getattr(tool, "name", None) != "invalid"]
            if native_tools:
                tools_schema = (
                    provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id
                    )
                )
                conductor.current_native_tools = native_tools
                conductor.current_text_based_tools = text_based_tools
        except Exception:
            tools_schema = None
    if tools_schema:
        try:
            record_tool_schema_snapshot(session_state, tools_schema, turn_index=turn_index)
        except Exception:
            pass
    if provider_id == "anthropic":
        try:
            anth_cfg = (conductor.config.get("provider_tools") or {}).get("anthropic", {}) or {}
            if isinstance(anth_cfg, dict) and anth_cfg.get("stream") is True:
                stream_responses = True
        except Exception:
            pass

    effective_stream_responses, stream_policy = apply_streaming_policy_for_turn(
        conductor,
        runtime,
        model,
        tools_schema,
        stream_responses,
        session_state,
        markdown_logger,
        turn_index,
        getattr(conductor, "_current_route_id", None),
    )
    client, effective_stream_responses, provider_profile = (
        _bind_episode_provider_profile(
            session_state,
            runtime,
            client,
            model,
            effective_stream_responses,
        )
    )
    runtime_extra = {
        "turn_index": turn_index,
        "model": model,
        "stream": effective_stream_responses,
        "route_id": conductor._current_route_id,
    }
    if stream_policy is not None:
        runtime_extra["stream_policy"] = stream_policy
    phase16_parallel_tool_calls = session_state.get_provider_metadata("phase16_parallel_tool_calls")
    phase16_phase_label = session_state.get_provider_metadata("phase16_phase_label")
    responses_extra: Dict[str, Any] = {}
    if phase16_parallel_tool_calls is not None:
        responses_extra["parallel_tool_calls"] = bool(phase16_parallel_tool_calls)
    if responses_extra:
        runtime_extra["responses_extra"] = responses_extra
    if phase16_phase_label:
        runtime_extra["phase16_phase_label"] = str(phase16_phase_label)

    runtime_context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config=conductor.config,
        stream=effective_stream_responses,
        extra=runtime_extra,
        session_id=session_state.get_provider_metadata("session_id")
        or getattr(session_state, "session_id", None),
        input_id=session_state.get_provider_metadata("input_id"),
        turn_id=session_state.get_provider_metadata("turn_id"),
        provider_profile=provider_profile,
    )
    (
        wire_request_body,
        request_headers,
        request_endpoint,
        profile_identity,
    ) = _provider_wire_evidence(
        profile=provider_profile,
        runtime=runtime,
        provider_id=provider_id,
        model=model,
        messages=send_messages,
        tools=tools_schema,
        stream=effective_stream_responses,
        client_config=client_config,
        context=runtime_context,
    )
    try:
        session_state.set_provider_metadata("current_stream_requested", stream_responses)
        session_state.set_provider_metadata("current_stream_effective", effective_stream_responses)
    except Exception:
        pass

    try:
        provider_messages = getattr(session_state, "provider_messages", []) or []
        if provider_messages:
            last_message = provider_messages[-1]
            if isinstance(last_message, dict) and last_message.get("tool_calls"):
                for _ in last_message.get("tool_calls", []):
                    conductor.loop_detector.observe_tool_call()
    except Exception:
        pass

    try:
        if conductor.logger_v2.include_raw:
            conductor.api_recorder.save_request(
                turn_index,
                wire_request_body,
            )
    except Exception:
        pass



    try:
        if getattr(conductor.logger_v2, "include_structured_requests", True):
            extra_meta: Dict[str, Any] = {
                "message_count": len(send_messages or []),
                "has_tools": bool(tools_schema),
            }
            if profile_identity is not None:
                extra_meta["provider_profile_identity"] = profile_identity
            if stream_policy:
                extra_meta["stream_policy"] = {
                    "reason": stream_policy.get("reason"),
                    "stream_effective": stream_policy.get("stream_effective"),
                }
            conductor.structured_request_recorder.record_request(
                turn_index,
                provider_id=getattr(runtime.descriptor, "provider_id", "unknown"),
                runtime_id=getattr(runtime.descriptor, "runtime_id", "unknown"),
                model=model,
                request_headers=request_headers,
                request_body=wire_request_body,
                stream=effective_stream_responses,
                tool_count=len(tools_schema or []),
                endpoint=request_endpoint,
                attempt=0,
                extra=extra_meta,
            )
    except Exception:
        pass

    result, _ = conductor._invoke_runtime_with_streaming(
        runtime,
        client,
        model,
        send_messages,
        tools_schema,
        effective_stream_responses,
        runtime_context,
        session_state,
        markdown_logger,
        turn_index,
    )

    if (conductor.config.get("features", {}) or {}).get("response_normalizer"):
        normalized_events = normalize_provider_result(result)
        result.metadata["normalized_events"] = normalized_events
        session_state.set_provider_metadata("normalized_events", normalized_events)
        if conductor.logger_v2.run_dir:
            conductor.logger_v2.write_json(
                f"meta/turn_{turn_index}_normalized_events.json",
                normalized_events,
            )

    usage_raw = result.usage or {}
    normalized_usage: Dict[str, Any] = {}
    if isinstance(usage_raw, dict):
        prompt_tokens = usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens")
        completion_tokens = usage_raw.get("completion_tokens") or usage_raw.get("output_tokens")
        if prompt_tokens is not None:
            normalized_usage["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            normalized_usage["completion_tokens"] = completion_tokens
        if usage_raw.get("cache_read_tokens") is not None:
            normalized_usage["cache_read_tokens"] = usage_raw.get("cache_read_tokens")
        if usage_raw.get("cache_write_tokens") is not None:
            normalized_usage["cache_write_tokens"] = usage_raw.get("cache_write_tokens")
        normalized_usage["model"] = model
    try:
        session_state.set_provider_metadata("usage_normalized", normalized_usage)
    except Exception:
        pass
    try:
        conductor._record_usage_reward_metrics(session_state, turn_index, usage_raw)
    except Exception:
        pass

    cursor_prefix = f"turn_{turn_index}"
    try:
        for msg_idx, prov_msg in enumerate(result.messages or []):
            if prov_msg.content:
                session_state.add_ir_event(
                    IRDeltaEvent(
                        cursor=f"{cursor_prefix}:text:{msg_idx}",
                        type="text",
                        payload={"role": prov_msg.role, "content": prov_msg.content},
                    )
                )
            if prov_msg.tool_calls:
                for tc_idx, tc in enumerate(prov_msg.tool_calls):
                    session_state.add_ir_event(
                        IRDeltaEvent(
                            cursor=f"{cursor_prefix}:tool_call:{msg_idx}:{tc_idx}",
                            type="tool_call",
                            payload={
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                                "tool_type": tc.type,
                            },
                        )
                    )
    except Exception:
        pass

    try:
        finish_reason = None
        if result.messages:
            finish_reason = result.messages[-1].finish_reason
        session_state.add_ir_event(
            IRDeltaEvent(
                cursor=f"{cursor_prefix}:finish",
                type="finish",
                payload={
                    "finish_reason": finish_reason,
                    "usage": normalized_usage,
                    "metadata": result.metadata,
                },
            )
        )
    except Exception:
        pass

    _record_raw_provider_response(conductor, result, turn_index)

    return result


def apply_turn_strategy_from_loop(conductor: Any) -> None:
    try:
        loop_ts = (conductor.config.get("loop", {}) or {}).get("turn_strategy") or {}
        if loop_ts:
            conductor.config.setdefault("turn_strategy", {})
            conductor.config["turn_strategy"].update(loop_ts)
    except Exception:
        pass


def setup_native_tools(conductor: Any, model: str, use_native_tools: bool) -> bool:
    will_use_native_tools = False

    if use_native_tools and getattr(conductor, "yaml_tools", None):
        try:
            provider_id = provider_router.parse_model_id(model)[0]
            native_tools, text_based_tools = (
                provider_adapter_manager.filter_tools_for_provider(conductor.yaml_tools, provider_id
                )
            )
            will_use_native_tools = bool(native_tools)
            conductor.current_native_tools = native_tools
            conductor.current_text_based_tools = text_based_tools
            if will_use_native_tools and provider_id in ("openai", "openrouter", "mock", "cli_mock",
            ):
                try:
                    alias_map = getattr(getattr(conductor, "agent_executor", None), "alias_map", None)
                    if isinstance(alias_map, dict):
                        for tool in native_tools:
                            name = getattr(tool, "name", None)
                            if not name:
                                continue
                            sanitized = sanitize_openai_tool_name(str(name))
                            if sanitized != name and sanitized not in alias_map:
                                alias_map[sanitized] = str(name)
                except Exception:
                    pass
        except Exception:
            will_use_native_tools = False

    return will_use_native_tools


def adjust_tool_prompt_mode(conductor: Any, tool_prompt_mode: str, will_use_native_tools: bool) -> str:
    if will_use_native_tools:
        if str(tool_prompt_mode or "").strip().lower() == "none":
            return "none"
        provider_cfg = getattr(conductor, "_provider_tools_effective", None) or (conductor.config.get("provider_tools") or {})
        suppress_prompts = bool(provider_cfg.get("suppress_prompts", False))
        if suppress_prompts:
            return "none"
        return "per_turn_append"
    return tool_prompt_mode


def setup_tool_prompts(
    conductor: Any,
    tool_prompt_mode: str,
    tool_defs: List[ToolDefinition],
    active_dialect_names: List[str],
    session_state,
    markdown_logger: MarkdownLogger,
    caller,
) -> str:
    prompt_tool_defs = getattr(conductor, "current_text_based_tools", None) or tool_defs
    mode_cfg = None
    if int(conductor.config.get("version", 0)) == 2:
        active_mode = conductor._resolve_active_mode()
        loop_cfg = conductor.config.get("loop", {}) or {}
        plan_limit = 0
        try:
            plan_limit = int(loop_cfg.get("plan_turn_limit") or 0)
        except Exception:
            plan_limit = 0
        if plan_limit:
            session_state.set_provider_metadata("plan_turn_limit", plan_limit)
            if session_state.get_provider_metadata("plan_turns") is None:
                session_state.set_provider_metadata("plan_turns", 0)
        mode_cfg = conductor._get_mode_config(active_mode)
        if mode_cfg:
            prompt_tool_defs = conductor._filter_tools_by_mode(prompt_tool_defs, mode_cfg)
        apply_turn_strategy_from_loop(conductor)

    active_tool_names: List[str] = []
    for definition in prompt_tool_defs or []:
        name = getattr(definition, "name", None)
        if name:
            active_tool_names.append(name)
    mode_allowed_names: set[str] = set()
    if mode_cfg:
        try:
            mode_allowed_names = {
                str(name)
                for name in (mode_cfg.get("tools_enabled") or [])
                if name not in (None, "")
            }
        except Exception:
            mode_allowed_names = set()
    if mode_allowed_names:
        for name in mode_allowed_names:
            if name not in active_tool_names:
                active_tool_names.append(name)
    conductor._active_tool_names = active_tool_names

    if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
        compiler = get_compiler()

        primary_prompt = session_state.messages[0].get("content", "")
        comprehensive_prompt, tools_hash = compiler.get_or_create_system_prompt(prompt_tool_defs, active_dialect_names, primary_prompt)

        session_state.messages[0]["content"] = comprehensive_prompt
        session_state.provider_messages[0]["content"] = comprehensive_prompt

        local_tools_prompt = (
            "(using cached comprehensive system prompt with research-based preferences)"
        )
    else:
        local_tools_prompt = caller.build_prompt(prompt_tool_defs)

        if getattr(conductor, "enhanced_executor", None):
            context = conductor.enhanced_executor.get_workspace_context()
            if context.get("files_created_this_session"):
                local_tools_prompt += f"\n\nWORKSPACE CONTEXT:\nFiles created this session: {context['files_created_this_session']}"
                local_tools_prompt += "\nIMPORTANT: Use edit tools for existing files, not create tools.\n"

        tool_directive_text = (
            "\n\nSYSTEM MESSAGE - AVAILABLE TOOLS\n"
            f"<FUNCTIONS>\n{local_tools_prompt}\n</FUNCTIONS>\n"
            "MANDATORY: Respond ONLY with one or more tool calls using <TOOL_CALL> ..., with <BASH>...</BASH> for shell, or with diff blocks (SEARCH/REPLACE for edits; unified diff or OpenCode Add File for new files).\n"
            "You may call multiple tools in one reply; non-blocking tools may run concurrently.\n"
            "Some tools are blocking and must run alone in sequence.\n"
            "Do NOT include any extra prose beyond tool calls or diff blocks.\n"
            "When you deem the task fully complete, call mark_task_complete(). If you cannot call tools, end your reply with a single line `TASK COMPLETE`.\n"
            "NEVER use bash to write large file contents (heredocs, echo >>). For files: call create_file() then apply a diff block for contents.\n"
            "Do NOT include extra prose.\nEND SYSTEM MESSAGE\n"
        )

        if tool_prompt_mode in ("system_once", "system_and_per_turn"):
            session_state.messages[0]["content"] = (session_state.messages[0].get("content") or "") + tool_directive_text
            session_state.provider_messages[0]["content"] = session_state.messages[0]["content"]
            markdown_logger.log_tool_availability([t.name for t in tool_defs])

    return local_tools_prompt


def add_enhanced_message_fields(
    conductor: Any,
    tool_prompt_mode: str,
    tool_defs: List[ToolDefinition],
    active_dialect_names: List[str],
    session_state,
    will_use_native_tools: bool,
    local_tools_prompt: str,
    user_prompt: str,
) -> None:
    if tool_prompt_mode in ("system_once", "system_and_per_turn", "system_compiled_and_persistent_per_turn",
    ):
        session_state.messages[0]["compiled_tools_available"] = [
            {
                "name": t.name,
                "type_id": t.type_id,
                "description": t.description,
                "parameters": (
                    [{"name": p.name, "type": p.type, "description": p.description, "default": p.default,
                        } for p in t.parameters] if t.parameters else []
                ),
            }
            for t in tool_defs
        ]

    tools_prompt_content = None
    native_tools_spec = None

    if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
        enabled_tools = [t.name for t in tool_defs]
        tools_prompt_content = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
    elif tool_prompt_mode in ("per_turn_append", "system_and_per_turn"):
        tools_prompt_content = local_tools_prompt

    if will_use_native_tools:
        try:
            native_tools = getattr(conductor, "current_native_tools", [])
            if native_tools:
                route_hint = getattr(conductor, "_current_route_id", None) or conductor.config.get("model", "gpt-4")
                provider_id = provider_router.parse_model_id(route_hint)[0]
                native_tools_spec = (
                    provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id
                    )
                )
        except Exception:
            pass

    user_message = next(
        (
            message
            for message in reversed(session_state.messages)
            if message.get("role") == "user"
        ),
        None,
    )
    provider_user_message = next(
        (
            message
            for message in reversed(session_state.provider_messages)
            if message.get("role") == "user"
        ),
        None,
    )
    if user_message is None or provider_user_message is None:
        raise RuntimeError("initial user message is missing")
    user_message["tools_available_prompt"] = tools_prompt_content
    if native_tools_spec:
        user_message["tools"] = native_tools_spec

    if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
        enabled_tools = [t.name for t in tool_defs]
        per_turn_availability = get_compiler().format_per_turn_availability(
            enabled_tools, active_dialect_names
        )
        initial_user_content = user_prompt + "\n\n" + per_turn_availability
        for message in (user_message, provider_user_message):
            content = message.get("content")
            if isinstance(content, list):
                replaced = False
                updated = []
                for block in content:
                    if (
                        not replaced
                        and isinstance(block, dict)
                        and block.get("type") == "text"
                    ):
                        updated.append(
                            {"type": "text", "text": initial_user_content}
                        )
                        replaced = True
                    else:
                        updated.append(block)
                if not replaced:
                    updated.insert(
                        0, {"type": "text", "text": initial_user_content}
                    )
                message["content"] = updated
            else:
                message["content"] = initial_user_content
