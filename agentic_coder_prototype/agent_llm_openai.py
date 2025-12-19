from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import ray

from kylecode.sandbox_v2 import DevSandboxV2
from kylecode.sandbox_virtualized import SandboxFactory, DeploymentMode

from .core.core import ToolDefinition, ToolParameter
from .execution.composite import CompositeToolCaller
from .execution.dialect_manager import DialectManager
from .execution.agent_executor import AgentToolExecutor
from .compilation.system_prompt_compiler import get_compiler
from .provider_routing import provider_router
from .provider_runtime import (
    provider_registry,
    ProviderRuntimeContext,
    ProviderResult,
    ProviderMessage,
    ProviderRuntimeError,
)
from .state.session_state import SessionState
from .state.completion_detector import CompletionDetector
from .messaging.message_formatter import MessageFormatter
from .messaging.markdown_logger import MarkdownLogger
from .error_handling.error_handler import ErrorHandler
from .logging_v2 import LoggerV2Manager
from .logging_v2.api_recorder import APIRequestRecorder
from .logging_v2.prompt_logger import PromptArtifactLogger
from .logging_v2.markdown_transcript import MarkdownTranscriptWriter
from .logging_v2.provider_native_logger import ProviderNativeLogger
from .logging_v2.request_recorder import StructuredRequestRecorder
from .utils.local_ray import LocalActorProxy, identity_get
from .provider_health import RouteHealthManager
from .provider_metrics import ProviderMetricsCollector
from .provider_capability_probe import ProviderCapabilityProbeRunner
from .provider_normalizer import normalize_provider_result
from .guardrail_coordinator import GuardrailCoordinator
from .guardrail_orchestrator import GuardrailOrchestrator
from .conductor_components import (
    apply_cache_control_to_initial_user_prompt,
    apply_cache_control_to_tool_messages,
    apply_capability_tool_overrides,
    apply_streaming_policy_for_turn,
    append_text_block,
    build_prompt_summary,
    capture_turn_diagnostics,
    completion_tool_config_enabled,
    ensure_completion_tool,
    get_capability_probe_result,
    maybe_run_plan_bootstrap,
    get_model_routing_preferences,
    get_prompt_cache_control,
    should_require_build_guard,
    is_read_only_tool,
    get_default_tool_definitions,
    initialize_config_validator,
    initialize_enhanced_executor,
    initialize_guardrail_config,
    initialize_yaml_tools,
    inject_cache_control_into_message,
    log_routing_event,
    normalize_assistant_text,
    prepare_concurrency_policy,
    record_lsp_reward_metrics,
    record_test_reward_metric,
    register_prompt_hash,
    tool_defs_from_yaml,
    write_env_fingerprint,
    write_workspace_manifest,
)
from .conductor_patching import (
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
from .conductor_loop import run_main_loop
from .conductor_bootstrap import bootstrap_conductor, prepare_workspace
from .conductor_modes import (
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
from .conductor_execution import (
    build_exec_func,
    execute_agent_calls,
    build_turn_context,
    summarize_execution_results,
    emit_turn_snapshot,
    hydrate_turn_context_signals,
    finalize_turn_context_snapshot,
    apply_turn_guards,
    handle_blocked_calls,
    resolve_replay_todo_placeholders,
    maybe_transition_plan_mode,
    handle_native_tool_calls,
    handle_text_tool_calls,
    legacy_message_view,
    log_provider_message,
    process_model_output,
    retry_with_fallback,
)
from .todo import TodoManager, TodoStore
from .todo.store import TODO_OPEN_STATUSES
from .replay import ReplaySession, load_replay_session
from .plan_bootstrapper import PlanBootstrapper
from .permission_broker import PermissionBroker, PermissionDeniedError
from .loop_detection import LoopDetectionService
from .context_window_guard import ContextWindowGuard
from .streaming_policy import StreamingPolicy
from .provider_invoker import ProviderInvoker
from .tool_prompt_planner import ToolPromptPlanner
from .turn_relayer import TurnRelayer
from .turn_context import TurnContext
from .runtime_context import get_current_session_state

ZERO_TOOL_WARN_TURNS = 2
ZERO_TOOL_ABORT_TURNS = 4
COMPLETION_GUARD_ABORT_THRESHOLD = 2
TODO_SEED_TOOL_NAMES = {"todo.create", "todo.write_board", "todowrite"}
ZERO_TOOL_WARN_MESSAGE = (
    "<VALIDATION_ERROR>\n"
    "No tool usage detected. Use read/list/diff/bash tools to make progress before responding again.\n"
    "</VALIDATION_ERROR>"
)
ZERO_TOOL_ABORT_MESSAGE = (
    "<VALIDATION_ERROR>\n"
    "No tool usage detected across multiple turns. The run is being stopped to avoid idle looping.\n"
    "</VALIDATION_ERROR>"
)
COMPLETION_GUARD_WARNING_TEMPLATE = (
    "<VALIDATION_ERROR>\n"
    "{reason}\n"
    "Completion guard engaged. Provide concrete file edits and successful tests. Warnings remaining before abort: {remaining}.\n"
    "</VALIDATION_ERROR>"
)
COMPLETION_GUARD_ABORT_TEMPLATE = (
    "<VALIDATION_ERROR>\n"
    "{reason}\n"
    "Completion guard engaged repeatedly. The run will now terminate to avoid wasting budget.\n"
    "</VALIDATION_ERROR>"
)


def compute_tool_prompt_mode(tool_prompt_mode: str, will_use_native_tools: bool, config: Dict[str, Any]) -> str:
    """Pure helper for testing prompt mode adjustment (module-level)."""
    if will_use_native_tools:
        suppress_prompts = bool((config or {}).get("provider_tools", {}).get("suppress_prompts", False))
        return "none" if suppress_prompts else "per_turn_append"
    return tool_prompt_mode


def _dump_tool_defs(tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for t in tool_defs or []:
        params = []
        for p in getattr(t, "parameters", []) or []:
            params.append({"name": p.name, "type": p.type, "description": p.description, "default": p.default})
        output.append({
            "name": getattr(t, "name", ""),
            "description": getattr(t, "description", ""),
            "parameters": params,
            "type_id": getattr(t, "type_id", "python"),
            "blocking": bool(getattr(t, "blocking", False)),
        })
    return output


@ray.remote
class OpenAIConductor:
    def __init__(
        self,
        workspace: str,
        image: str = "python-dev:latest",
        config: Optional[Dict[str, Any]] = None,
        *,
        local_mode: bool = False,
    ) -> None:
        bootstrap_conductor(
            self,
            workspace=workspace,
            image=image,
            config=config or {},
            local_mode=local_mode,
            zero_tool_warn_message=ZERO_TOOL_WARN_MESSAGE,
            zero_tool_abort_message=ZERO_TOOL_ABORT_MESSAGE,
            completion_guard_abort_threshold=COMPLETION_GUARD_ABORT_THRESHOLD,
        )
        self._active_telemetry_logger = None
        self._provider_tools_effective: Dict[str, Any] = {}
        self._active_tool_names: List[str] = []
        self._native_preference_hint: Optional[bool] = None
        self._capability_probes_ran = False
        self.todo_manager = None

    # ------------------------------------------------------------------
    # Config helpers
    def _resolve_active_mode(self) -> Optional[str]:
        try:
            seq = (self.config.get("loop", {}) or {}).get("sequence") or []
            features = self.config.get("features", {}) or {}
            for step in seq:
                if not isinstance(step, dict):
                    continue
                if "if" in step and "then" in step:
                    cond = str(step.get("if"))
                    then = step.get("then") or {}
                    ok = False
                    if cond.startswith("features."):
                        key = cond.split("features.", 1)[1]
                        ok = bool(features.get(key))
                    if ok and isinstance(then, dict) and then.get("mode"):
                        return str(then.get("mode"))
                if step.get("mode"):
                    return str(step.get("mode"))
        except Exception:
            pass
        return None

    def _get_mode_config(self, mode_name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not mode_name:
            return None
        modes = self.config.get("modes") or []
        for entry in modes:
            if isinstance(entry, dict) and entry.get("name") == mode_name:
                return entry
        return None

    def _filter_tools_by_mode(self, tools: List[ToolDefinition], mode_cfg: Dict[str, Any]) -> List[ToolDefinition]:
        enabled = mode_cfg.get("tools_enabled") or []
        disabled = set(str(name) for name in (mode_cfg.get("tools_disabled") or []) if name)
        if enabled and "*" not in enabled:
            allowed = set(str(name) for name in enabled)
            return [t for t in tools if getattr(t, "name", None) in allowed and getattr(t, "name", None) not in disabled]
        if disabled:
            return [t for t in tools if getattr(t, "name", None) not in disabled]
        return list(tools)

    # ------------------------------------------------------------------
    # Helper wrappers (conductor_* modules)
    def _dump_tool_defs(self, tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
        return _dump_tool_defs(tool_defs)

    def _tool_defs_from_yaml(self) -> Optional[List[ToolDefinition]]:
        return tool_defs_from_yaml(self)

    def _get_default_tool_definitions(self) -> List[ToolDefinition]:
        return get_default_tool_definitions()

    def _apply_streaming_policy_for_turn(
        self,
        runtime: Any,
        model: str,
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_requested: bool,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        turn_index: int,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return apply_streaming_policy_for_turn(
            self,
            runtime,
            model,
            tools_schema,
            stream_requested,
            session_state,
            markdown_logger,
            turn_index,
            getattr(self, "_current_route_id", None),
        )

    def _record_stream_policy_metadata(self, session_state: SessionState, policy: Dict[str, Any]) -> None:
        try:
            history = session_state.get_provider_metadata("stream_policy_history", [])
        except Exception:
            history = []
        if not isinstance(history, list):
            history = []
        history.append(policy)
        if len(history) > 20:
            history = history[-20:]
        try:
            session_state.set_provider_metadata("stream_policy_history", history)
            session_state.set_provider_metadata("last_stream_policy", policy)
            session_state.add_transcript_entry({"stream_policy": policy})
        except Exception:
            pass

    def _apply_capability_tool_overrides(
        self,
        provider_tools_cfg: Dict[str, Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        route_id: Optional[str],
    ) -> Dict[str, Any]:
        return apply_capability_tool_overrides(self, provider_tools_cfg, session_state, markdown_logger, route_id)

    def _get_model_routing_preferences(self, route_id: Optional[str]) -> Dict[str, Any]:
        return get_model_routing_preferences(self.config, route_id)

    def _select_fallback_route(
        self,
        route_id: Optional[str],
        provider_id: Optional[str],
        model: str,
        explicit_fallbacks: List[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        candidates = [m for m in (explicit_fallbacks or []) if m and m != model]
        if candidates:
            return candidates[0], "explicit_fallback"
        return None, None

    def _log_routing_event(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        *,
        turn_index: Optional[int],
        tag: str,
        message: str,
        payload: Dict[str, Any],
    ) -> None:
        return log_routing_event(
            self,
            session_state,
            markdown_logger,
            turn_index=turn_index,
            tag=tag,
            message=message,
            payload=payload,
        )

    def _update_health_metadata(self, session_state: SessionState) -> None:
        try:
            session_state.set_provider_metadata("route_health", self.route_health.snapshot())
        except Exception:
            pass

    def _normalize_assistant_text(self, text: Optional[str]) -> str:
        return normalize_assistant_text(text)

    def _append_text_block(self, message: Dict[str, Any], text: str) -> None:
        append_text_block(message, text)

    def _completion_guard_check(self, session_state: SessionState) -> Tuple[bool, Optional[str]]:
        return self.guardrail_orchestrator.completion_guard_check(session_state)

    def _emit_completion_guard_feedback(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        reason: str,
        stream_responses: bool,
    ) -> bool:
        return self.guardrail_orchestrator.emit_completion_guard_feedback(
            session_state,
            markdown_logger,
            reason,
            stream_responses,
        )

    def _record_diff_metrics(
        self,
        tool_parsed: Any,
        tool_result: Dict[str, Any],
        *,
        session_state: SessionState,
        turn_index: Optional[int],
    ) -> None:
        record_diff_metrics(self, tool_parsed, tool_result, session_state=session_state, turn_index=turn_index)

    def _record_lsp_reward_metrics(self, session_state: SessionState, turn_index: Optional[int]) -> None:
        if turn_index is None:
            return
        record_lsp_reward_metrics(self, session_state, turn_index)

    def _record_test_reward_metric(self, session_state: SessionState, turn_index: Optional[int], success_value: Optional[float]) -> None:
        if turn_index is None:
            return
        record_test_reward_metric(session_state, turn_index, success_value)

    def _record_usage_reward_metrics(self, session_state: SessionState, turn_index: int, usage: Dict[str, Any]) -> None:
        if turn_index is None:
            return
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        try:
            total_tokens = float(prompt_tokens) + float(completion_tokens)
        except Exception:
            total_tokens = 0.0
        if total_tokens:
            session_state.add_reward_metric(turn_index, "TE", total_tokens)
            session_state.add_reward_metric(turn_index, "TOE", float(completion_tokens) / total_tokens)
        latency = getattr(self, "_last_runtime_latency", None)
        if latency is not None:
            try:
                session_state.add_reward_metric(turn_index, "LE", float(latency))
            except Exception:
                pass
        spa = 1.0 if not getattr(self, "_last_html_detected", False) else 0.0
        session_state.add_reward_metric(turn_index, "SPA", spa)

    def _validate_structural_artifacts(self, session_state: SessionState) -> List[str]:
        return validate_structural_artifacts(self, session_state)

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.vcs.remote(request))

    def _persist_error_artifacts(self, turn_index: int, payload: Dict[str, Any]) -> None:
        if not getattr(self.logger_v2, "run_dir", None):
            return
        try:
            self.logger_v2.write_json(f"errors/turn_{turn_index}.json", payload)
        except Exception:
            pass
        details = payload.get("details") if isinstance(payload, dict) else None
        if not isinstance(details, dict):
            return
        headers = details.get("response_headers")
        if headers is not None:
            try:
                self.logger_v2.write_json(f"raw/responses/turn_{turn_index}.headers.json", headers)
            except Exception:
                pass
        raw_b64 = details.get("raw_body_b64")
        if raw_b64 is not None:
            try:
                self.logger_v2.write_text(f"raw/responses/turn_{turn_index}.body.b64", str(raw_b64))
            except Exception:
                pass
        raw_excerpt = details.get("raw_excerpt")
        if raw_excerpt is not None:
            try:
                self.logger_v2.write_text(f"raw/responses/turn_{turn_index}.raw_excerpt.txt", str(raw_excerpt))
            except Exception:
                pass
        html_excerpt = details.get("html_excerpt")
        if html_excerpt is not None:
            try:
                self.logger_v2.write_text(f"raw/responses/turn_{turn_index}.html_excerpt.txt", str(html_excerpt))
            except Exception:
                pass

    def _synthesize_patch_blocks(self, message_text: Optional[str]) -> List[str]:
        return synthesize_patch_blocks(message_text)

    def _normalize_patch_block(self, block: str) -> Optional[str]:
        normalized = normalize_patch_block(block)
        return normalized or None

    def _convert_patch_to_unified(self, patch_text: str) -> Optional[str]:
        return convert_patch_to_unified(self, patch_text)

    def _expand_multi_file_patches(self, parsed_calls: List[Any], session_state: Any, markdown_logger: Any) -> List[Any]:
        return expand_multi_file_patches(self, parsed_calls, session_state, markdown_logger)

    def _build_prompt_summary(self) -> Optional[Dict[str, Any]]:
        return build_prompt_summary(self)

    def _register_prompt_hash(self, prompt_type: str, content: Optional[str], turn_index: Optional[int] = None) -> None:
        register_prompt_hash(self, prompt_type, content, turn_index)

    def _write_workspace_manifest(self) -> None:
        write_workspace_manifest(self)

    def _capture_turn_diagnostics(self, session_state: Any, provider_result: Optional[Any], allowed_tools: Optional[List[str]]) -> None:
        diag = capture_turn_diagnostics(self, session_state, provider_result, allowed_tools, todo_seed_names=list(TODO_SEED_TOOL_NAMES))
        try:
            self._turn_diagnostics.append(diag)
        except Exception:
            pass

    def _maybe_run_plan_bootstrap(self, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
        maybe_run_plan_bootstrap(self, session_state, markdown_logger)

    def _build_exec_func(self, session_state: SessionState):
        return build_exec_func(self, session_state)

    def _execute_agent_calls(self, parsed_calls, exec_func, session_state, transcript_callback=None, policy_bypass: bool = False):
        return execute_agent_calls(self, parsed_calls, exec_func, session_state, transcript_callback=transcript_callback, policy_bypass=policy_bypass)

    def _build_turn_context(self, session_state: SessionState, parsed_calls: List[Any]) -> TurnContext:
        return build_turn_context(self, session_state, parsed_calls)

    def _summarize_execution_results(self, turn_ctx: TurnContext, executed_results, session_state, turn_index_int: Optional[int]):
        return summarize_execution_results(self, turn_ctx, executed_results, session_state, turn_index_int)

    def _emit_turn_snapshot(self, session_state: SessionState, turn_ctx: TurnContext) -> None:
        return emit_turn_snapshot(self, session_state, turn_ctx)

    def _hydrate_turn_context_signals(self, session_state: SessionState, turn_ctx: TurnContext) -> None:
        return hydrate_turn_context_signals(session_state, turn_ctx)

    def _finalize_turn_context_snapshot(self, session_state: SessionState, turn_ctx: TurnContext, turn_index: Optional[int]) -> None:
        return finalize_turn_context_snapshot(self, session_state, turn_ctx, turn_index)

    def _apply_turn_guards(self, turn_ctx: TurnContext, session_state: SessionState):
        return apply_turn_guards(self, turn_ctx, session_state)

    def _handle_blocked_calls(self, turn_ctx: TurnContext, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
        return handle_blocked_calls(self, turn_ctx, session_state, markdown_logger)

    def _resolve_replay_todo_placeholders(self, session_state: SessionState, call: Any) -> None:
        return resolve_replay_todo_placeholders(self, session_state, call)

    def _maybe_transition_plan_mode(self, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
        return maybe_transition_plan_mode(self, session_state, markdown_logger)

    def _handle_native_tool_calls(self, msg, session_state, markdown_logger, error_handler, stream_responses: bool, model: str) -> bool:
        return handle_native_tool_calls(self, msg, session_state, markdown_logger, error_handler, stream_responses, model)

    def _handle_text_tool_calls(self, msg, caller, tool_defs, session_state, markdown_logger, error_handler, stream_responses: bool) -> bool:
        return handle_text_tool_calls(self, msg, caller, tool_defs, session_state, markdown_logger, error_handler, stream_responses)

    def _legacy_message_view(self, provider_message: ProviderMessage):
        return legacy_message_view(provider_message)

    def _log_provider_message(self, provider_message, session_state, markdown_logger, stream_responses: bool) -> None:
        return log_provider_message(self, provider_message, session_state, markdown_logger, stream_responses)

    def _process_model_output(self, provider_message, caller, tool_defs, session_state, completion_detector, markdown_logger, error_handler, stream_responses: bool, model: str) -> bool:
        return process_model_output(self, provider_message, caller, tool_defs, session_state, completion_detector, markdown_logger, error_handler, stream_responses, model)

    def _retry_with_fallback(self, *args, **kwargs):
        return retry_with_fallback(self, *args, **kwargs)

    def _get_model_response(
        self,
        runtime,
        client,
        model: str,
        tool_prompt_mode: str,
        tool_defs: List[ToolDefinition],
        active_dialect_names: List[str],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
        local_tools_prompt: str,
        client_config: Dict[str, Any],
    ):
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

    def _invoke_runtime_with_streaming(
        self,
        runtime,
        client,
        model: str,
        send_messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_responses: bool,
        runtime_context: ProviderRuntimeContext,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        turn_index: int,
    ):
        route_id = getattr(self, "_current_route_id", None)
        return self.provider_invoker.invoke(
            runtime=runtime,
            client=client,
            model=model,
            send_messages=send_messages,
            tools_schema=tools_schema,
            stream_responses=stream_responses,
            runtime_context=runtime_context,
            session_state=session_state,
            markdown_logger=markdown_logger,
            turn_index=turn_index,
            route_id=route_id,
        )

    # ------------------------------------------------------------------
    # Execution helpers
    def _is_test_command(self, command: str) -> bool:
        if not command:
            return False
        lower = command.lower()
        return any(tok in lower for tok in ["pytest", "npm test", "yarn test", "make test", "ctest", "go test", "cargo test"])

    def _is_read_only_tool(self, tool_name: str) -> bool:
        return is_read_only_tool(self, tool_name)

    # ------------------------------------------------------------------
    # Tool execution
    def list_dir(self, path: str, depth: int = 1) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.ls.remote(path, depth))

    def read_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.read_text.remote(path))

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.write_text.remote(path, content))

    def run_shell(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.run_shell.remote(command, timeout))

    def _apply_unified_patch(self, patch_text: str) -> Dict[str, Any]:
        if not patch_text:
            return {"ok": False, "error": "empty patch"}

        # Minimal parser: handle new-file patches from mock runtime
        applied: List[str] = []
        lines = patch_text.splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if line.startswith("diff --git "):
                # parse file path
                parts = line.split()
                if len(parts) >= 4:
                    b_path = parts[3]
                    if b_path.startswith("b/"):
                        b_path = b_path[2:]
                    # scan ahead for new file mode
                    is_new = False
                    content_lines: List[str] = []
                    idx += 1
                    while idx < len(lines) and not lines[idx].startswith("diff --git "):
                        cur = lines[idx]
                        if cur.startswith("new file mode"):
                            is_new = True
                        if cur.startswith("+++ ") or cur.startswith("--- "):
                            idx += 1
                            continue
                        if cur.startswith("@@"):
                            idx += 1
                            continue
                        if cur.startswith("+") and not cur.startswith("+++"):
                            content_lines.append(cur[1:])
                        elif cur.startswith(" "):
                            content_lines.append(cur[1:])
                        idx += 1
                    if is_new:
                        content = "\n".join(content_lines)
                        if content_lines:
                            content += "\n"
                        self.write_file(str(Path(self.workspace) / b_path), content)
                        applied.append(b_path)
                    continue
            idx += 1
        if applied:
            return {"ok": True, "paths": applied, "diff": patch_text}
        # fallback: report success without applying
        return {"ok": True, "diff": patch_text, "paths": []}

    def _apply_opencode_patch(self, patch_text: str) -> Dict[str, Any]:
        result = apply_patch_operations_direct(self, patch_text)
        if result is None:
            return {"ok": False, "error": "failed to apply patch"}
        return {"ok": True, **result}

    def _handle_todo(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.todo_manager:
            self.todo_manager = TodoManager(TodoStore(self.workspace), lambda evt: None)
        manager = self.todo_manager
        try:
            if name == "todo.create":
                return manager.handle_create(payload)
            if name == "todo.update":
                return manager.handle_update(payload)
            if name == "todo.complete":
                return manager.handle_complete(payload)
            if name == "todo.cancel":
                return manager.handle_cancel(payload)
            if name == "todo.reorder":
                return manager.handle_reorder(payload)
            if name == "todo.attach":
                return manager.handle_attach(payload)
            if name == "todo.note":
                return manager.handle_note(payload)
            if name == "todo.list":
                return manager.handle_list(payload)
            if name == "todo.write_board":
                return manager.handle_write_board(payload)
        except Exception as exc:
            return {"error": str(exc)}
        return {"error": f"Unknown todo op: {name}"}

    def _parse_task_replay(self, entries: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
        summary: List[Dict[str, Any]] = []
        output_parts: List[str] = []
        for entry in entries:
            if entry.get("role") != "assistant":
                continue
            parts = entry.get("parts") or []
            for part in parts:
                if part.get("type") == "tool":
                    meta = part.get("meta") or {}
                    state = meta.get("state") or {}
                    summary.append({
                        "id": part.get("id"),
                        "tool": part.get("tool"),
                        "input": state.get("input"),
                        "status": state.get("status"),
                        "output": state.get("output"),
                    })
                if part.get("type") == "text":
                    text = part.get("text")
                    if text:
                        output_parts.append(text)
        output = "\n".join(output_parts).strip()
        return summary, output

    def _emit_task_event(self, session_state: Optional[SessionState], payload: Dict[str, Any]) -> None:
        if not session_state:
            return
        try:
            session_state._emit_event("task_event", payload, turn=session_state.get_provider_metadata("current_turn_index"))
        except Exception:
            pass

    def _exec_task_tool(self, args: Dict[str, Any]) -> Dict[str, Any]:
        description = str(args.get("description") or "Task")
        subagent_type = str(args.get("subagent_type") or args.get("subagentType") or "general")
        task_cfg = (self.config.get("task_tool", {}) or {}).get("subagents", {})
        sub_cfg = task_cfg.get(subagent_type)
        if not sub_cfg:
            return {"error": f"Unknown agent type: {subagent_type}", "__mvi_text_output": f"Unknown agent type: {subagent_type}"}

        replay_path = sub_cfg.get("replay_session") or sub_cfg.get("child_replay_session")
        if not replay_path:
            return {"error": "Failed to load task replay session", "__mvi_text_output": "Failed to load task replay session"}

        session_state = get_current_session_state()
        self._emit_task_event(session_state, {"kind": "started", "title": description, "subagent": subagent_type})

        try:
            entries = json.loads(Path(replay_path).read_text(encoding="utf-8"))
        except Exception:
            return {"error": "Failed to load task replay session", "__mvi_text_output": "Failed to load task replay session"}

        summary, output = self._parse_task_replay(entries)

        # Emit tool call events
        for entry in summary:
            self._emit_task_event(session_state, {"kind": "tool_call", "call": entry})

        forward_assistant = bool(((self.config.get("task_tool", {}) or {}).get("streaming", {}) or {}).get("forward_assistant_messages", False))
        if forward_assistant and output:
            self._emit_task_event(session_state, {"kind": "assistant_message", "content": output})

        self._emit_task_event(session_state, {"kind": "completed", "title": description})

        session_id = str(uuid.uuid4())
        return {
            "title": description,
            "output": output,
            "__mvi_text_output": output,
            "metadata": {"sessionId": session_id, "summary": summary},
        }

    def _exec_raw(self, call: Dict[str, Any]) -> Dict[str, Any]:
        fn = call.get("function") or call.get("name")
        args = call.get("arguments") or {}
        if isinstance(fn, dict):
            fn = fn.get("name")
        if not isinstance(args, dict):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        name = str(fn or "")
        normalized = name.lower()

        if normalized == "task":
            return self._exec_task_tool(args)
        if normalized in {"list_dir", "list"}:
            path = args.get("path") or args.get("root") or "."
            depth = args.get("depth") or 1
            return self.list_dir(str(path), int(depth))
        if normalized in {"read_file", "read"}:
            path = args.get("path") or args.get("filePath") or args.get("file_path")
            if not path:
                return {"error": "read_file missing path"}
            return self.read_file(str(path))
        if normalized in {"create_file_from_block", "write", "write_file", "create_file"}:
            path = args.get("path") or args.get("filePath") or args.get("file_path") or args.get("file_name")
            content = args.get("content") or ""
            if not path:
                return {"error": "write_file missing path"}
            return self.write_file(str(path), str(content))
        if normalized in {"apply_unified_patch", "unified_diff"}:
            patch_text = args.get("patch") or args.get("patchText") or ""
            return self._apply_unified_patch(str(patch_text))
        if normalized in {"patch"}:
            patch_text = args.get("patchText") or args.get("patch") or ""
            return self._apply_opencode_patch(str(patch_text))
        if normalized in {"apply_search_replace", "edit"}:
            path = args.get("path") or args.get("filePath") or args.get("file_path")
            old = args.get("oldString") or args.get("old_string") or ""
            new = args.get("newString") or args.get("new_string") or ""
            count = int(args.get("count") or 0)
            if not path:
                return {"error": "apply_search_replace missing path"}
            return self._ray_get(self.sandbox.edit_replace.remote(str(path), str(old), str(new), count))
        if normalized in {"run_shell", "bash", "shell_command"}:
            command = args.get("command") or args.get("cmd") or ""
            timeout = int(args.get("timeout") or 30)
            if not command:
                return {"error": "run_shell missing command"}
            return self.run_shell(str(command), timeout)
        if normalized in {"todowrite", "todo.write_board", "todo_write_board"}:
            return self._handle_todo("todo.write_board", args)
        if normalized in {"todoread"}:
            return self._handle_todo("todo.list", args)
        if normalized.startswith("todo."):
            return self._handle_todo(normalized, args)
        if normalized == "mark_task_complete":
            return {"ok": True, "action": "complete"}

        return {"error": f"Unknown tool: {name}"}

    # ------------------------------------------------------------------
    # Main loop
    def run_agentic_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_steps: int = 12,
        *,
        output_json_path: Optional[str] = None,
        stream_responses: bool = False,
        output_md_path: Optional[str] = None,
        tool_prompt_mode: str = "system_once",
        event_emitter: Optional[Any] = None,
        event_queue: Optional[Any] = None,
        permission_queue: Optional[Any] = None,
    ) -> Dict[str, Any]:
        session_state = SessionState(workspace=self.workspace, image=self.image, config=self.config, event_emitter=event_emitter)
        if event_emitter:
            session_state.set_event_emitter(event_emitter)

        # Replay mode setup (if a replay session was loaded during bootstrap)
        if getattr(self, "_replay_session_data", None):
            try:
                replay_cfg = (self.config.get("replay") or {}) if isinstance(self.config, dict) else {}
                preserve_tools = bool(replay_cfg.get("preserve_tool_names", False))
                replay_session = ReplaySession(
                    self._replay_session_data,
                    Path(self.workspace),
                    preserve_tool_names=preserve_tools,
                )
                self._active_replay_session = replay_session
                session_state.set_provider_metadata("active_replay_session", replay_session)
                session_state.set_provider_metadata("replay_mode", True)
                # Override initial user prompt with recorded prompt when available.
                if getattr(self._replay_session_data, "user_prompt", None):
                    user_prompt = self._replay_session_data.user_prompt
                model = "replay"
            except Exception:
                pass

        # Seed todo manager if configured
        try:
            todos_cfg = (self.config.get("features", {}) or {}).get("todos", {})
            if todos_cfg.get("enabled", False):
                store = TodoStore(self.workspace)
                manager = TodoManager(store, session_state.emit_todo_event)
                session_state.set_todo_manager(manager)
                self.todo_manager = manager
        except Exception:
            pass

        completion_detector = CompletionDetector(self.config)
        markdown_logger = MarkdownLogger()
        error_handler = ErrorHandler()

        # capability probes
        if not self._capability_probes_ran:
            try:
                self.capability_probe_runner.run(self.config, session_state)
            except Exception:
                pass
            self._capability_probes_ran = True

        route_id = model
        provider_config, resolved_model, supports_native = provider_router.get_provider_config(model)
        runtime_descriptor, runtime_model = provider_router.get_runtime_descriptor(model)
        client_config = provider_router.create_client_config(model)

        if not client_config.get("api_key"):
            raise RuntimeError(f"{provider_config.api_key_env} missing in environment")

        runtime = provider_registry.create_runtime(runtime_descriptor)
        client = runtime.create_client(
            client_config.get("api_key"),
            base_url=client_config.get("base_url"),
            default_headers=client_config.get("default_headers"),
        )

        model = runtime_model
        self._current_route_id = route_id
        session_state.set_provider_metadata("provider_id", runtime_descriptor.provider_id)
        session_state.set_provider_metadata("runtime_id", runtime_descriptor.runtime_id)
        session_state.set_provider_metadata("api_variant", runtime_descriptor.default_api_variant)
        session_state.set_provider_metadata("resolved_model", runtime_model)

        tool_defs = self._tool_defs_from_yaml() or self._get_default_tool_definitions()
        dialect_mapping = create_dialect_mapping()
        active_dialect_names = self.dialect_manager.get_dialects_for_model(model, list(dialect_mapping.keys()))
        try:
            from .compilation.v2_loader import is_v2_config
            if is_v2_config(self.config):
                active_dialect_names = apply_v2_dialect_selection(self, active_dialect_names, model, tool_defs)
        except Exception:
            pass
        filtered_dialects = [dialect_mapping[name] for name in active_dialect_names if name in dialect_mapping]
        caller = CompositeToolCaller(filtered_dialects)

        per_turn_prompt = ""
        try:
            if int(self.config.get("version", 0)) == 2 and self.config.get("prompts"):
                mode_name = self._resolve_active_mode()
                comp = get_compiler()
                v2 = comp.compile_v2_prompts(self.config, mode_name, tool_defs, active_dialect_names)
                system_prompt = v2.get("system") or system_prompt
                per_turn_prompt = v2.get("per_turn") or ""
                try:
                    if system_prompt and self.logger_v2.run_dir:
                        self.prompt_logger.save_compiled_system(system_prompt)
                except Exception:
                    pass
        except Exception:
            per_turn_prompt = ""

        enhanced_user_content = user_prompt if not per_turn_prompt else (user_prompt + "\n\n" + per_turn_prompt)
        session_state.add_message({"role": "system", "content": system_prompt})
        session_state.add_message({"role": "user", "content": enhanced_user_content})

        # provider tools config
        provider_tools_cfg = dict((self.config.get("provider_tools") or {}))
        provider_tools_cfg = self._apply_capability_tool_overrides(
            provider_tools_cfg,
            session_state,
            markdown_logger,
            getattr(self, "_current_route_id", None),
        )
        self._provider_tools_effective = provider_tools_cfg
        use_native_tools = provider_router.should_use_native_tools(model, {"provider_tools": provider_tools_cfg})
        will_use_native_tools = setup_native_tools(self, model, use_native_tools)
        tool_prompt_mode = adjust_tool_prompt_mode(self, tool_prompt_mode, will_use_native_tools)
        session_state.last_tool_prompt_mode = tool_prompt_mode

        local_tools_prompt = setup_tool_prompts(
            self,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            markdown_logger,
            caller,
        )
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

        markdown_logger.log_system_message(system_prompt)
        markdown_logger.log_user_message(enhanced_user_content)

        try:
            if self.logger_v2.run_dir:
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.system(system_prompt))
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.user(enhanced_user_content))
        except Exception:
            pass

        session_state.write_snapshot(output_json_path, model)

        result = run_main_loop(
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
        return result
