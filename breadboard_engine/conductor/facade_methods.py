from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..provider.routing import provider_router
from ..provider.runtime import provider_registry
from .components import (
    apply_cache_control_to_initial_user_prompt,
    apply_cache_control_to_tool_messages,
    append_text_block,
    completion_tool_config_enabled,
    ensure_completion_tool,
    get_default_tool_definitions,
    get_prompt_cache_control,
    is_read_only_tool,
    maybe_run_plan_bootstrap,
    normalize_assistant_text,
    prepare_concurrency_policy,
    record_lsp_reward_metrics,
    record_test_reward_metric,
    register_prompt_hash,
    should_require_build_guard,
    tool_defs_from_yaml,
    inject_cache_control_into_message,
)
from .execution import (
    apply_turn_guards,
    build_exec_func,
    build_turn_context,
    execute_agent_calls,
    finalize_turn_context_snapshot,
    handle_blocked_calls,
    handle_native_tool_calls,
    handle_text_tool_calls,
    hydrate_turn_context_signals,
    legacy_message_view,
    log_provider_message,
    maybe_transition_plan_mode,
    process_model_output,
    resolve_replay_todo_placeholders,
    retry_with_fallback,
    summarize_execution_results,
    emit_turn_snapshot,
)
from .loop import run_main_loop
from .modes import (
    add_enhanced_message_fields,
    adjust_tool_prompt_mode,
    apply_preference_order,
    apply_selection_legacy,
    apply_turn_strategy_from_loop,
    apply_v2_dialect_selection,
    create_dialect_mapping,
    get_model_response,
    get_native_preference_hint,
    setup_native_tools,
    setup_tool_prompts,
)
from .patching import (
    apply_patch_operations_direct,
    convert_patch_to_unified,
    count_diff_hunks,
    emit_patch_policy_violation,
    expand_multi_file_patches,
    fetch_workspace_text,
    normalize_patch_block,
    normalize_workspace_path,
    record_diff_metrics,
    split_patch_blocks,
    synthesize_patch_blocks,
    validate_structural_artifacts,
)


class OpenAIConductorFacadeMethods:
    """Pass-through OpenAIConductor facade methods delegated to conductor modules."""

    def _apply_turn_strategy_from_loop(self) -> None:
        apply_turn_strategy_from_loop(self)

    def _apply_cache_control_to_initial_user_prompt(
        self,
        messages: List[Dict[str, Any]],
        cache_control: Dict[str, Any],
    ) -> None:
        apply_cache_control_to_initial_user_prompt(messages, cache_control)

    def _inject_cache_control_into_message(
        self,
        message: Dict[str, Any],
        cache_control: Dict[str, Any],
    ) -> None:
        inject_cache_control_into_message(message, cache_control)

    def _append_text_block(self, message: Dict[str, Any], text: str) -> None:
        append_text_block(message, text)

    def _apply_cache_control_to_tool_messages(
        self,
        messages: List[Dict[str, Any]],
        cache_control: Dict[str, Any],
    ) -> None:
        apply_cache_control_to_tool_messages(messages, cache_control)

    def _get_prompt_cache_control(self) -> Optional[Dict[str, Any]]:
        provider_cfg = getattr(self, "_provider_tools_effective", None) or (self.config.get("provider_tools") or {})
        return get_prompt_cache_control({"provider_tools": provider_cfg})

    def _prepare_concurrency_policy(self) -> Dict[str, Any]:
        return prepare_concurrency_policy(self.config)

    def _tool_defs_from_yaml(self) -> Optional[List[Any]]:
        return tool_defs_from_yaml(self)

    def _completion_tool_config_enabled(self) -> bool:
        return completion_tool_config_enabled(self.config)

    def _ensure_completion_tool(self, tool_defs: List[Any]) -> List[Any]:
        return ensure_completion_tool(tool_defs)

    @staticmethod
    def _is_read_only_tool(tool_name: str) -> bool:
        return is_read_only_tool(tool_name)

    @staticmethod
    def _normalize_assistant_text(text: Optional[str]) -> str:
        return normalize_assistant_text(text)

    def _record_lsp_reward_metrics(self, session_state: Any, turn_index: int) -> None:
        record_lsp_reward_metrics(self, session_state, turn_index)

    def _record_test_reward_metric(
        self,
        session_state: Any,
        turn_index: int,
        success_value: Optional[float],
    ) -> None:
        record_test_reward_metric(session_state, turn_index, success_value)

    def _register_prompt_hash(self, prompt_type: str, content: Optional[str], turn_index: Optional[int] = None) -> None:
        register_prompt_hash(self, prompt_type, content, turn_index)

    def _maybe_run_plan_bootstrap(self, session_state: Any, markdown_logger: Optional[Any] = None) -> None:
        maybe_run_plan_bootstrap(self, session_state, markdown_logger)

    def _should_require_build_guard(self, user_prompt: str) -> bool:
        return should_require_build_guard(self, user_prompt)

    def _build_turn_context(self, session_state: Any, parsed_calls: List[Any]) -> Any:
        return build_turn_context(self, session_state, parsed_calls)

    def _summarize_execution_results(
        self,
        turn_ctx: Any,
        executed_results: List[Any],
        session_state: Any,
        turn_index_int: Optional[int],
    ) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        return summarize_execution_results(self, turn_ctx, executed_results, session_state, turn_index_int)

    def _emit_turn_snapshot(self, session_state: Any, turn_ctx: Any) -> None:
        emit_turn_snapshot(self, session_state, turn_ctx)

    def _hydrate_turn_context_signals(self, session_state: Any, turn_ctx: Any) -> None:
        hydrate_turn_context_signals(session_state, turn_ctx)

    def _finalize_turn_context_snapshot(self, session_state: Any, turn_ctx: Any, turn_index: Optional[int]) -> None:
        finalize_turn_context_snapshot(self, session_state, turn_ctx, turn_index)

    def _apply_turn_guards(self, turn_ctx: Any, session_state: Any) -> List[Any]:
        return apply_turn_guards(self, turn_ctx, session_state)

    def _handle_blocked_calls(self, turn_ctx: Any, session_state: Any, markdown_logger: Any) -> None:
        handle_blocked_calls(self, turn_ctx, session_state, markdown_logger)

    def _resolve_replay_todo_placeholders(self, session_state: Any, parsed_call: Any) -> None:
        resolve_replay_todo_placeholders(self, session_state, parsed_call)

    def _maybe_transition_plan_mode(self, session_state: Any, markdown_logger: Optional[Any] = None) -> None:
        maybe_transition_plan_mode(self, session_state, markdown_logger)

    @staticmethod
    def _count_diff_hunks(text: Optional[str]) -> int:
        return count_diff_hunks(text)

    @staticmethod
    def _split_patch_blocks(patch_text: str) -> List[str]:
        return split_patch_blocks(patch_text)

    def _normalize_patch_block(self, block_text: str) -> str:
        return normalize_patch_block(block_text)

    def _fetch_workspace_text(self, path: str) -> str:
        return fetch_workspace_text(self, path)

    def _convert_patch_to_unified(self, patch_text: str) -> Optional[str]:
        return convert_patch_to_unified(self, patch_text)

    def _apply_patch_operations_direct(self, patch_text: str) -> Optional[Dict[str, Any]]:
        return apply_patch_operations_direct(self, patch_text)

    def _expand_multi_file_patches(self, parsed_calls: List[Any], session_state: Any, markdown_logger: Any) -> List[Any]:
        return expand_multi_file_patches(self, parsed_calls, session_state, markdown_logger)

    def _emit_patch_policy_violation(
        self,
        session_state: Any,
        markdown_logger: Any,
        *,
        chunk_count: int,
        policy_mode: str,
        validation_message: Optional[str],
    ) -> None:
        emit_patch_policy_violation(
            self,
            session_state,
            markdown_logger,
            chunk_count=chunk_count,
            policy_mode=policy_mode,
            validation_message=validation_message,
        )

    def _validate_structural_artifacts(self, session_state: Any) -> List[str]:
        return validate_structural_artifacts(self, session_state)

    def _record_diff_metrics(
        self,
        tool_call: Any,
        result: Dict[str, Any],
        *,
        session_state: Optional[Any] = None,
        turn_index: Optional[int] = None,
    ) -> None:
        record_diff_metrics(
            self,
            tool_call,
            result,
            session_state=session_state,
            turn_index=turn_index,
        )

    def _normalize_workspace_path(self, path_in: str) -> str:
        return normalize_workspace_path(self, path_in)

    def _get_default_tool_definitions(self) -> List[Any]:
        return get_default_tool_definitions()

    def _create_dialect_mapping(self) -> Dict[str, Any]:
        return create_dialect_mapping()

    def _apply_v2_dialect_selection(self, current: List[str], model_id: str, tool_defs: List[Any]) -> List[str]:
        return apply_v2_dialect_selection(self, current, model_id, tool_defs)

    def _apply_selection_legacy(
        self,
        current: List[str],
        model_id: str,
        tool_defs: List[Any],
        selection_cfg: Dict[str, Any],
    ) -> List[str]:
        return apply_selection_legacy(current, model_id, tool_defs, selection_cfg)

    def _apply_preference_order(
        self,
        base_order: List[str],
        model_id: str,
        tool_defs: List[Any],
        preference_cfg: Dict[str, Any],
    ) -> Tuple[List[str], Optional[bool]]:
        return apply_preference_order(base_order, model_id, tool_defs, preference_cfg)

    def _get_native_preference_hint(self) -> Optional[bool]:
        return get_native_preference_hint(self)

    def _setup_native_tools(self, model: str, use_native_tools: bool) -> bool:
        return setup_native_tools(self, model, use_native_tools)

    def _adjust_tool_prompt_mode(self, tool_prompt_mode: str, will_use_native_tools: bool) -> str:
        return adjust_tool_prompt_mode(self, tool_prompt_mode, will_use_native_tools)

    def _setup_tool_prompts(
        self,
        tool_prompt_mode: str,
        tool_defs: List[Any],
        active_dialect_names: List[str],
        session_state: Any,
        markdown_logger: Any,
        caller: Any,
    ) -> str:
        return setup_tool_prompts(
            self,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            markdown_logger,
            caller,
        )

    def _add_enhanced_message_fields(
        self,
        tool_prompt_mode: str,
        tool_defs: List[Any],
        active_dialect_names: List[str],
        session_state: Any,
        will_use_native_tools: bool,
        local_tools_prompt: str,
        user_prompt: str,
    ) -> None:
        add_enhanced_message_fields(
            self,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            will_use_native_tools,
            local_tools_prompt,
            user_prompt,
        )

    def _run_main_loop(
        self,
        runtime: Any,
        client: Any,
        model: str,
        max_steps: int,
        output_json_path: str,
        tool_prompt_mode: str,
        tool_defs: List[Any],
        active_dialect_names: List[str],
        caller: Any,
        session_state: Any,
        completion_detector: Any,
        markdown_logger: Any,
        error_handler: Any,
        stream_responses: bool,
        local_tools_prompt: str,
        client_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        return run_main_loop(
            self,
            runtime,
            client,
            model,
            max_steps,
            output_json_path,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            caller,
            session_state,
            completion_detector,
            markdown_logger,
            error_handler,
            stream_responses,
            local_tools_prompt,
            client_config,
        )

    def _retry_with_fallback(
        self,
        runtime: Any,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        runtime_context: Any,
        *,
        stream_responses: bool,
        session_state: Any,
        markdown_logger: Any,
        attempted: List[Tuple[str, bool, Optional[str]]],
        last_error: Optional[Any],
    ) -> Optional[Any]:
        openai_module = sys.modules.get("agentic_coder_prototype.agent_llm_openai")
        active_provider_router = getattr(openai_module, "provider_router", provider_router)
        active_provider_registry = getattr(openai_module, "provider_registry", provider_registry)
        return retry_with_fallback(
            self,
            runtime,
            client,
            model,
            messages,
            tools_schema,
            runtime_context,
            stream_responses=stream_responses,
            session_state=session_state,
            markdown_logger=markdown_logger,
            attempted=attempted,
            last_error=last_error,
            provider_router_override=active_provider_router,
            provider_registry_override=active_provider_registry,
        )

    def _build_exec_func(self, session_state: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        return build_exec_func(self, session_state)

    def _execute_agent_calls(
        self,
        parsed_calls: List[Any],
        exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        session_state: Any,
        *,
        transcript_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        policy_bypass: bool = False,
    ) -> Tuple[List[Any], int, Optional[Dict[str, Any]], Dict[str, Any]]:
        return execute_agent_calls(
            self,
            parsed_calls,
            exec_func,
            session_state,
            transcript_callback=transcript_callback,
            policy_bypass=policy_bypass,
        )

    def _get_model_response(
        self,
        runtime: Any,
        client: Any,
        model: str,
        tool_prompt_mode: str,
        tool_defs: List[Any],
        active_dialect_names: List[str],
        session_state: Any,
        markdown_logger: Any,
        stream_responses: bool,
        local_tools_prompt: str,
        client_config: Dict[str, Any],
    ) -> Any:
        return get_model_response(
            self,
            runtime,
            client,
            model,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            markdown_logger,
            stream_responses,
            local_tools_prompt,
            client_config,
        )

    def _legacy_message_view(self, provider_message: Any) -> SimpleNamespace:
        return legacy_message_view(provider_message)

    def _log_provider_message(
        self,
        provider_message: Any,
        session_state: Any,
        markdown_logger: Any,
        stream_responses: bool,
    ) -> None:
        log_provider_message(self, provider_message, session_state, markdown_logger, stream_responses)

    def _process_model_output(
        self,
        provider_message: Any,
        caller: Any,
        tool_defs: List[Any],
        session_state: Any,
        completion_detector: Any,
        markdown_logger: Any,
        error_handler: Any,
        stream_responses: bool,
        model: str,
    ) -> bool:
        return process_model_output(
            self,
            provider_message,
            caller,
            tool_defs,
            session_state,
            completion_detector,
            markdown_logger,
            error_handler,
            stream_responses,
            model,
        )

    def _handle_text_tool_calls(
        self,
        msg: Any,
        caller: Any,
        tool_defs: List[Any],
        session_state: Any,
        markdown_logger: Any,
        error_handler: Any,
        stream_responses: bool,
    ) -> bool:
        return handle_text_tool_calls(
            self,
            msg,
            caller,
            tool_defs,
            session_state,
            markdown_logger,
            error_handler,
            stream_responses,
        )

    def _synthesize_patch_blocks(self, message_text: Optional[str]) -> List[str]:
        return synthesize_patch_blocks(message_text)

    def _handle_native_tool_calls(
        self,
        msg: Any,
        session_state: Any,
        markdown_logger: Any,
        error_handler: Any,
        stream_responses: bool,
        model: str,
    ) -> bool:
        return handle_native_tool_calls(
            self,
            msg,
            session_state,
            markdown_logger,
            error_handler,
            stream_responses,
            model,
        )
