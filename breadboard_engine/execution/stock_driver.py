"""Profile dispatch and explicit stock lifecycle ports.

Only this setup boundary interprets native tool identities. Conductors consume
capabilities; native implementations own their sessions and execution policy.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .stock_tools import close_stock_sessions, native_concurrent_prompts


@dataclass(frozen=True)
class StockDriver:
    execute: Callable[..., Any]
    prepare_input: Optional[Callable[..., None]] = None
    prepare_request: Optional[Callable[..., tuple[list, Any]]] = None
    request_tools: Optional[Callable[..., Any]] = None
    submit_response: Optional[Callable[..., None]] = None
    submit_overflow: Optional[Callable[..., None]] = None
    execute_batch: Optional[Callable[..., Any]] = None
    pending_compaction: Optional[Callable[..., Any]] = None
    synchronize_record: Optional[Callable[..., None]] = None
    recovery_outcome: Optional[Callable[..., Any]] = None
    begin_turn: Optional[Callable[..., None]] = None
    complete_turn: Optional[Callable[..., None]] = None
    available_tools: Optional[Callable[..., list]] = None
    source_model: Optional[Callable[[Any, dict], Optional[str]]] = None
    wait_for_source_input: Optional[Callable[[Any, str], None]] = None
    source_should_continue: Optional[Callable[[Any, str], bool]] = None
    compaction_error: type[Exception] = RuntimeError
    lexical_arguments: bool = False
    owns_tool_batch: bool = False

    def close(self, owner: Any) -> None:
        close_stock_sessions(owner)


def _prepare_opencode_input(owner, state, context, message):
    concurrent = native_concurrent_prompts(owner.config)
    owner._opencode_stock_input_handler = context.get("opencode_stock_input_handler") if concurrent else None
    owner._opencode_stock_headless = concurrent and context.get("opencode_stock_headless") is True
    origin = context.get("opencode_stock_origin") if concurrent else None
    state.set_provider_metadata("opencode_stock_origin", origin)
    state.set_provider_metadata("opencode_stock_turn_origin", origin)
    state.set_provider_metadata("opencode_stock_raw_user", copy.deepcopy(message))


def _prepare_opencode_request(owner, state, messages):
    from .opencode.driver import prepare_stock_turn
    from .opencode.wire import stock_system_messages
    from ..compilation.context_builders.opencode_stock import (
        project_inflight_current_user, project_inflight_tool_arguments, project_inflight_tool_outputs,
    )
    request = prepare_stock_turn(owner, state)
    state.set_provider_metadata("opencode_stock_request", request)
    messages = stock_system_messages(request) + [m for m in messages if m.get("role") not in {"system", "developer"}]
    messages = project_inflight_current_user(state, request, messages)
    messages = project_inflight_tool_arguments(request, messages)
    return project_inflight_tool_outputs(request, messages), request


def _pending_opencode_compaction(state):
    session = getattr(state, "_opencode_tool_session", None)
    return session.queued_compaction() if session is not None else None


def _opencode_recovery_outcome(state):
    if getattr(state, "_opencode_tool_session", None) is None:
        return None
    outcome = state.get_provider_metadata("opencode_stock_recovery_outcome")
    if outcome in {"handled", "error"}:
        state.set_provider_metadata("opencode_stock_recovery_outcome", None)
    return outcome


def _prepare_codex_input(owner, state, context, message):
    from .codex_agents import prepare_codex_input_message
    prepare_codex_input_message(owner, message)


def _codex_available_tools(owner, tools):
    if getattr(owner, "_codex_agent_depth", 0) >= (owner.config.get("agents") or {}).get("max_depth", 1):
        return [tool for tool in tools if tool.name not in {"multi_agent_v1", "tool_search"}]
    return tools


def _source_model(owner, context):
    from .opencode.wire import StockExecutionError
    source = context.get("opencode_stock_origin")
    if source is None:
        return None
    native = getattr(owner, "_opencode_tool_sessions", {}).get(context["session_id"])
    if native is None or not any(
        pending is not None and pending.get("stock_origin") == source
        for pending in (native.pending, native.compaction_pending)
    ):
        raise StockExecutionError("Stock-origin turn does not own the native parent request")
    source_model = source["model"]
    model = source_model["providerID"] + "/" + source_model["modelID"]
    if model not in {entry["id"] for entry in owner.config["providers"]["models"]}:
        raise StockExecutionError(f"Unroutable stock parent model {model}")
    return model


def _wait_for_source_input(owner, session_id):
    session = getattr(owner, "_opencode_tool_sessions", {}).get(session_id)
    if session is not None:
        session.wait_for_idle_source_input()


def _source_should_continue(owner, session_id):
    session = getattr(owner, "_opencode_tool_sessions", {}).get(session_id)
    return session is not None and session.source_should_continue()


def _execute_claude_batch(owner, state, parsed_calls, transcript_callback):
    from .claude_tools import execute_claude_tool_batch, _texts
    calls = [{
        "id": getattr(call, "call_id", None),
        "name": getattr(call, "provider_name", None) or call.function,
        "input": call.arguments,
    } for call in parsed_calls]
    batch = execute_claude_tool_batch(owner, calls, session_state=state)
    by_id = {getattr(call, "call_id", None): call for call in parsed_calls}
    executed = []
    for block in batch["__claude_tool_result_blocks"]:
        if block.get("type") != "tool_result":
            continue
        call = by_id[block["tool_use_id"]]
        result = {"__claude_tool_result_blocks": [block], "__mvi_text_output": _texts(block["content"])}
        if not executed:
            result["__claude_tool_result_messages"] = batch["__claude_tool_result_messages"]
        executed.append((call, result))
        if transcript_callback:
            transcript_callback({"tool": getattr(call, "provider_name", None) or call.function, "result": result})
    return executed, -1, None, {"total_calls": len(calls), "executed_calls": len(executed)}


def resolve_stock_driver(owner: Any) -> Optional[StockDriver]:
    semantics = (owner.config.get("tools") or {}).get("native_semantics")
    if semantics in {"opencode_1_2_17", "oh_my_opencode_3_10_0"}:
        from .opencode.driver import execute_opencode_tool, submit_stock_response, submit_stock_overflow, synchronize_record
        from .opencode.wire import StockExecutionError, stock_tool_schema
        return StockDriver(
            execute=execute_opencode_tool, prepare_input=_prepare_opencode_input,
            prepare_request=_prepare_opencode_request, request_tools=stock_tool_schema,
            submit_response=submit_stock_response, submit_overflow=submit_stock_overflow,
            pending_compaction=_pending_opencode_compaction, synchronize_record=synchronize_record,
            recovery_outcome=_opencode_recovery_outcome, lexical_arguments=True, owns_tool_batch=True,
            compaction_error=StockExecutionError,
            source_model=_source_model if native_concurrent_prompts(owner.config) else None,
            wait_for_source_input=_wait_for_source_input,
            source_should_continue=_source_should_continue,
        )
    if semantics == "codex_0_139":
        from .codex_exec import execute_codex_tool
        from .codex_agents import begin_codex_goal, sync_codex_goal
        def execute(owner, name, args, *, tool_call):
            return execute_codex_tool(owner, name, args)
        return StockDriver(execute=execute, prepare_input=_prepare_codex_input,
                           begin_turn=begin_codex_goal, complete_turn=sync_codex_goal,
                           available_tools=_codex_available_tools)
    if semantics == "claude_code_2_1_63":
        from .claude_tools import execute_claude_tool
        return StockDriver(
            execute=execute_claude_tool,
            execute_batch=_execute_claude_batch if (owner.config.get("tools") or {}).get("group_native_calls_by_turn") else None,
        )
    return None


def summary_tool_schema(owner: Any, provider: str):
    """Expose the active provider catalog to compaction without object traversal."""
    from ..provider import provider_adapter_manager
    tools = getattr(owner, "current_native_tools", None)
    if tools is None:
        tools, _ = provider_adapter_manager.filter_tools_for_provider(owner.yaml_tools or [], provider)
    active = getattr(owner, "_active_tool_names", None)
    if active:
        tools = [tool for tool in tools if tool.name in active]
    return provider_adapter_manager.translate_tools_to_native_schema(tools, provider)
