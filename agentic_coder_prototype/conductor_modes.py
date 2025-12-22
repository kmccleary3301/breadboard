from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from .core.core import ToolDefinition
from .dialects.pythonic02 import Pythonic02Dialect
from .dialects.pythonic_inline import PythonicInlineDialect
from .dialects.aider_diff import AiderDiffDialect
from .dialects.unified_diff import UnifiedDiffDialect
from .dialects.opencode_patch import OpenCodePatchDialect
from .dialects.yaml_command import YAMLCommandDialect
from .dialects.bash_block import BashBlockDialect
from .compilation.system_prompt_compiler import get_compiler
from .messaging.markdown_logger import MarkdownLogger
from .provider_ir import IRDeltaEvent
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager, sanitize_openai_tool_name
from .provider_runtime import ProviderRuntimeContext
from .provider_normalizer import normalize_provider_result
from .conductor_components import (
    apply_streaming_policy_for_turn,
    apply_cache_control_to_initial_user_prompt,
    apply_cache_control_to_tool_messages,
    get_prompt_cache_control,
    log_routing_event,
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
        if getattr(descriptor, "runtime_id", None) == "openai_responses" or getattr(descriptor, "default_api_variant", None) == "responses":
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
            native_tools = getattr(conductor, 'current_native_tools', [])
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
                tools_schema = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
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
            native_tools, text_based_tools = provider_adapter_manager.filter_tools_for_provider(source_defs, provider_id)
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
                tools_schema = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
                conductor.current_native_tools = native_tools
                conductor.current_text_based_tools = text_based_tools
        except Exception:
            tools_schema = None
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
                {
                    "model": model,
                    "messages": send_messages,
                    "tools": tools_schema,
                    "stream": effective_stream_responses,
                },
            )
    except Exception:
        pass

    runtime_extra = {
        "turn_index": turn_index,
        "model": model,
        "stream": effective_stream_responses,
        "route_id": conductor._current_route_id,
    }
    if stream_policy is not None:
        runtime_extra["stream_policy"] = stream_policy

    runtime_context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config=conductor.config,
        stream=effective_stream_responses,
        extra=runtime_extra,
    )

    try:
        request_headers = dict(client_config.get("default_headers") or {})
        if getattr(runtime, "descriptor", None) and getattr(runtime.descriptor, "provider_id", None) == "openrouter":
            request_headers.setdefault("Accept", "application/json; charset=utf-8")
            request_headers.setdefault("Accept-Encoding", "identity")
    except Exception:
        request_headers = {}

    try:
        if getattr(conductor.logger_v2, "include_structured_requests", True):
            extra_meta: Dict[str, Any] = {
                "message_count": len(send_messages or []),
                "has_tools": bool(tools_schema),
            }
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
                request_body={
                    "model": model,
                    "messages": send_messages,
                    "tools": tools_schema,
                    "stream": effective_stream_responses,
                },
                stream=effective_stream_responses,
                tool_count=len(tools_schema or []),
                endpoint=client_config.get("base_url"),
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
        try:
            normalized_events = normalize_provider_result(result)
            result.metadata.setdefault("normalized_events", normalized_events)
            session_state.set_provider_metadata("normalized_events", normalized_events)
            if conductor.logger_v2.run_dir:
                conductor.logger_v2.write_json(
                    f"meta/turn_{turn_index}_normalized_events.json",
                    normalized_events,
                )
        except Exception:
            pass

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

    try:
        if conductor.logger_v2.include_raw:
            raw_payload = result.raw_response
            if hasattr(raw_payload, "model_dump"):
                serialized = raw_payload.model_dump()  # type: ignore[attr-defined]
            elif isinstance(raw_payload, dict):
                serialized = raw_payload
            else:
                try:
                    serialized = dict(raw_payload)
                except Exception:
                    serialized = None
            if serialized is not None:
                conductor.api_recorder.save_response(turn_index, serialized)
    except Exception:
        pass

    return result


def apply_turn_strategy_from_loop(conductor: Any) -> None:
    try:
        loop_ts = (conductor.config.get("loop", {}) or {}).get("turn_strategy") or {}
        if loop_ts:
            conductor.config.setdefault("turn_strategy", {})
            conductor.config["turn_strategy"].update(loop_ts)
    except Exception:
        pass


def create_dialect_mapping() -> Dict[str, Any]:
    """Create mapping from config names to dialect instances."""
    return {
        "pythonic02": Pythonic02Dialect(),
        "pythonic_inline": PythonicInlineDialect(),
        "bash_block": BashBlockDialect(),
        "aider_diff": AiderDiffDialect(),
        "unified_diff": UnifiedDiffDialect(),
        "opencode_patch": OpenCodePatchDialect(),
        "yaml_command": YAMLCommandDialect(),
    }


def apply_v2_dialect_selection(conductor: Any, current: List[str], model_id: str, tool_defs: List[ToolDefinition]) -> List[str]:
    try:
        tools_cfg = (conductor.config.get("tools", {}) or {})
        dialects_cfg = (tools_cfg.get("dialects", {}) or {})
        selection_cfg = (dialects_cfg.get("selection", {}) or {})
        base_order = apply_selection_legacy(current, model_id, tool_defs, selection_cfg)

        preference_cfg = (dialects_cfg.get("preference", {}) or {})
        if preference_cfg:
            ordered, native_hint = apply_preference_order(base_order, model_id, tool_defs, preference_cfg)
            conductor._native_preference_hint = native_hint
            return ordered

        conductor._native_preference_hint = None
        return base_order
    except Exception:
        conductor._native_preference_hint = None
        return current


def apply_selection_legacy(
    current: List[str],
    model_id: str,
    tool_defs: List[ToolDefinition],
    selection_cfg: Dict[str, Any],
) -> List[str]:
    by_model: Dict[str, List[str]] = selection_cfg.get("by_model", {}) or {}
    by_tool_kind: Dict[str, List[str]] = selection_cfg.get("by_tool_kind", {}) or {}

    def ordered_intersection(prefer: List[str], available: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for name in prefer:
            if name in available and name not in seen:
                out.append(name)
                seen.add(name)
        return out

    import fnmatch as _fnmatch

    preferred: List[str] = []
    if model_id:
        for pattern, prefer_list in by_model.items():
            try:
                if _fnmatch.fnmatch(model_id, pattern):
                    preferred.extend(ordered_intersection([str(x) for x in (prefer_list or [])], current))
            except Exception:
                continue

    present_types = {t.type_id for t in (tool_defs or []) if getattr(t, "type_id", None)}
    diff_pref_list = [str(x) for x in (by_tool_kind.get("diff", []) or [])]
    bash_pref_list = [str(x) for x in (by_tool_kind.get("bash", []) or [])]

    known_diff_names = set(diff_pref_list) | {"aider_diff", "unified_diff", "opencode_patch"}
    diff_present = ("diff" in present_types) or any(name in current for name in known_diff_names)
    bash_present = any(name in current for name in (bash_pref_list or ["bash_block"]))

    if diff_present and diff_pref_list:
        preferred.extend(ordered_intersection(diff_pref_list, current))
    if bash_present and bash_pref_list:
        preferred.extend(ordered_intersection(bash_pref_list, current))

    seen = set()
    ordered_pref = [d for d in preferred if (d not in seen and not seen.add(d))]
    remaining = [d for d in current if d not in ordered_pref]
    return ordered_pref + remaining


def apply_preference_order(
    base_order: List[str],
    model_id: str,
    tool_defs: List[ToolDefinition],
    preference_cfg: Dict[str, Any],
) -> Tuple[List[str], Optional[bool]]:
    import fnmatch as _fnmatch

    available = list(base_order)
    available_set = set(available)
    preferred: List[str] = []
    seen = set()
    native_hint: Optional[bool] = None

    def normalize_entry(entry: Any) -> Tuple[List[str], Optional[bool]]:
        native_override: Optional[bool] = None
        order_values: List[Any]

        if isinstance(entry, dict):
            order_values = entry.get("order")  # type: ignore[assignment]
            if order_values is None and "list" in entry:
                order_values = entry.get("list")
            native_val = entry.get("native")
            if native_val is not None:
                native_override = bool(native_val)
        else:
            order_values = entry

        if isinstance(order_values, str):
            raw_items = [order_values]
        elif isinstance(order_values, (list, tuple)):
            raw_items = list(order_values)
        elif order_values is None:
            raw_items = []
        else:
            raw_items = [str(order_values)]

        cleaned: List[str] = []
        for item in raw_items:
            item_str = str(item).strip()
            if not item_str:
                continue
            if item_str == "provider_native":
                if native_override is None:
                    native_override = True
                continue
            cleaned.append(item_str)

        return cleaned, native_override

    def extend(entry: Any) -> None:
        nonlocal native_hint
        order_list, native_override = normalize_entry(entry)
        for name in order_list:
            if name not in available_set:
                continue
            if name in seen:
                try:
                    preferred.remove(name)
                except ValueError:
                    pass
            else:
                seen.add(name)
            preferred.append(name)
        if native_override is not None and native_hint is None:
            native_hint = native_override

    if model_id:
        for pattern, entry in (preference_cfg.get("by_model", {}) or {}).items():
            try:
                if _fnmatch.fnmatch(model_id, pattern):
                    extend(entry)
            except Exception:
                continue

    present_types = {t.type_id for t in (tool_defs or []) if getattr(t, "type_id", None)}
    available_names = set(available)

    for kind, entry in (preference_cfg.get("by_tool_kind", {}) or {}).items():
        if kind == "diff":
            diff_names = normalize_entry(entry)[0]
            diff_present = ("diff" in present_types) or any(name in available_names for name in diff_names)
            if not diff_present:
                continue
        elif kind == "bash":
            bash_names = normalize_entry(entry)[0]
            bash_present = any(name in available_names for name in (bash_names or ["bash_block"]))
            if not bash_present:
                continue
        extend(entry)

    extend(preference_cfg.get("default"))

    remaining = [d for d in available if d not in seen]
    ordered = preferred + remaining
    return ordered, native_hint


def get_native_preference_hint(conductor: Any) -> Optional[bool]:
    return getattr(conductor, "_native_preference_hint", None)


def setup_native_tools(conductor: Any, model: str, use_native_tools: bool) -> bool:
    will_use_native_tools = False

    if use_native_tools and getattr(conductor, "yaml_tools", None):
        try:
            provider_id = provider_router.parse_model_id(model)[0]
            native_tools, text_based_tools = provider_adapter_manager.filter_tools_for_provider(conductor.yaml_tools, provider_id)
            will_use_native_tools = bool(native_tools)
            conductor.current_native_tools = native_tools
            conductor.current_text_based_tools = text_based_tools
            if will_use_native_tools and provider_id in ("openai", "openrouter", "mock", "cli_mock"):
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
        loop_cfg = (conductor.config.get("loop", {}) or {})
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
    for definition in (prompt_tool_defs or []):
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

        local_tools_prompt = "(using cached comprehensive system prompt with research-based preferences)"
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
    if tool_prompt_mode in ("system_once", "system_and_per_turn", "system_compiled_and_persistent_per_turn"):
        session_state.messages[0]["compiled_tools_available"] = [
            {
                "name": t.name,
                "type_id": t.type_id,
                "description": t.description,
                "parameters": [{"name": p.name, "type": p.type, "description": p.description, "default": p.default} for p in t.parameters] if t.parameters else []
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
                provider_id = provider_router.parse_model_id(conductor.config.get("model", "gpt-4"))[0]
                native_tools_spec = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
        except Exception:
            pass

    session_state.messages[1]["tools_available_prompt"] = tools_prompt_content
    if native_tools_spec:
        session_state.messages[1]["tools"] = native_tools_spec

    if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
        enabled_tools = [t.name for t in tool_defs]
        per_turn_availability = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
        initial_user_content = user_prompt + "\n\n" + per_turn_availability
        session_state.messages[1]["content"] = initial_user_content
        session_state.provider_messages[1]["content"] = initial_user_content
