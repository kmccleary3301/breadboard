from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import shlex
import shutil
import time
import uuid
import traceback
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict

import ray

from adaptive_iter import decode_adaptive_iterable

from breadboard.sandbox_v2 import DevSandboxV2
from breadboard.opencode_patch import PatchParseError, parse_opencode_patch, to_unified_diff
from breadboard.sandbox_virtualized import SandboxFactory, DeploymentMode
from .core.core import ToolDefinition, ToolParameter
from .execution.composite import CompositeToolCaller
from .dialects.bash_block import BashBlockDialect
from .execution.dialect_manager import DialectManager
from .execution.agent_executor import AgentToolExecutor
from .compilation.system_prompt_compiler import get_compiler
from .provider_ir import IRFinish, IRDeltaEvent
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager
from .provider_runtime import (
    provider_registry,
    ProviderRuntimeContext,
    ProviderResult,
    ProviderMessage,
    ProviderRuntimeError,
)
from .provider_runtime_replay import ReplayRuntime
from .provider_capability_probe import ProviderCapabilityProbeRunner
from .state.session_state import SessionState
from .state.completion_detector import CompletionDetector
from .messaging.message_formatter import MessageFormatter
from .messaging.markdown_logger import MarkdownLogger
from .error_handling.error_handler import ErrorHandler
from .monitoring.telemetry import TelemetryLogger
from .monitoring.reward_metrics import RewardMetricsSQLiteWriter, TodoMetricsSQLiteWriter
from .logging_v2 import LoggerV2Manager
from .logging_v2.api_recorder import APIRequestRecorder
from .logging_v2.prompt_logger import PromptArtifactLogger
from .logging_v2.markdown_transcript import MarkdownTranscriptWriter
from .logging_v2.provider_native_logger import ProviderNativeLogger
from .logging_v2.request_recorder import StructuredRequestRecorder
from .utils.local_ray import LocalActorProxy, identity_get
from .provider_health import RouteHealthManager
from .provider_normalizer import normalize_provider_result
from .provider_metrics import ProviderMetricsCollector
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
    retry_diff_with_aider,
    validate_structural_artifacts,
)
from .conductor_loop import run_main_loop
from .conductor_bootstrap import bootstrap_conductor, prepare_workspace
from .conductor_components import write_env_fingerprint
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

def _tools_schema() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a text file to the workspace (overwrites).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List files in a directory in the workspace. Optional depth parameter for tree structure (1-5, default 1).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "depth": {"type": "integer", "description": "Tree depth (1-5, default 1)", "default": 1}
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "Run a shell command in the workspace and return stdout/stderr/exit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def _dump_tool_defs(tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for t in tool_defs:
        result.append(
            {
                "type_id": t.type_id,
                "name": t.name,
                "description": t.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "description": p.description,
                        "default": p.default,
                    }
                    for p in t.parameters
                ],
            }
        )
    return result


@ray.remote
class OpenAIConductor:
    def __init__(self, workspace: str, image: str = "python-dev:latest", config: Optional[Dict[str, Any]] = None, *, local_mode: bool = False) -> None:
        """Initialize conductor with workspace, image, and configuration."""
        bootstrap_conductor(
            self,
            workspace=workspace,
            image=image,
            config=config,
            local_mode=local_mode,
            zero_tool_warn_message=ZERO_TOOL_WARN_MESSAGE,
            zero_tool_abort_message=ZERO_TOOL_ABORT_MESSAGE,
            completion_guard_abort_threshold=COMPLETION_GUARD_ABORT_THRESHOLD,
        )

    def _prepare_replay_session(
        self,
        session_state: SessionState,
        user_prompt: str,
        max_steps: int,
    ) -> Tuple[str, int]:
        """
        Initialize an active ReplaySession for this run if replay data is configured.

        This helper is a straight extraction of the existing inline logic in
        run_agentic_loop so we can treat replay setup as a single, well-defined
        step without changing behavior.
        """
        if self._replay_session_data:
            user_prompt = self._replay_session_data.user_prompt
            self._active_replay_session = ReplaySession(self._replay_session_data, Path(self.workspace))
            session_state.set_provider_metadata("replay_mode", True)
            session_state.set_provider_metadata("active_replay_session", self._active_replay_session)
            session_state.set_provider_metadata("replay_total_turns", self._active_replay_session.total_turns)
            max_steps = min(max_steps, self._active_replay_session.total_turns + 2)
        else:
            self._active_replay_session = None
        return user_prompt, max_steps

    def seed_workspace_file(self, filename: str, contents: str) -> None:
        """Copy helper files (e.g., task specs) into the active workspace before execution."""
        if not filename:
            return
        try:
            target = Path(self.workspace) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        except Exception:
            pass


    def _prepare_workspace(self, workspace: str) -> Path:
        """Ensure the workspace directory exists and is empty before use."""
        return prepare_workspace(workspace)

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
        route_id = getattr(self, "_current_route_id", None)
        return apply_streaming_policy_for_turn(
            self,
            runtime,
            model,
            tools_schema,
            stream_requested,
            session_state,
            markdown_logger,
            turn_index,
            route_id,
        )

    def _get_model_routing_preferences(self, route_id: Optional[str]) -> Dict[str, Any]:
        """Return routing preferences for a given configured route."""
        return get_model_routing_preferences(self.config, route_id)

    def _get_capability_probe_result(
        self,
        session_state: SessionState,
        route_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Fetch previously recorded capability probe result for a route."""
        return get_capability_probe_result(session_state, route_id)

    def _log_routing_event(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        *,
        turn_index: Optional[Any],
        tag: str,
        message: str,
        payload: Dict[str, Any],
    ) -> None:
        """Emit routing-related diagnostic across transcript, markdown, and IR."""
        log_routing_event(
            self,
            session_state,
            markdown_logger,
            turn_index=turn_index,
            tag=tag,
            message=message,
            payload=payload,
        )

    def _apply_capability_tool_overrides(
        self,
        provider_tools_cfg: Dict[str, Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> Dict[str, Any]:
        """Adjust native tool usage based on capability probe outputs."""
        route_id = getattr(self, "_current_route_id", None)
        return apply_capability_tool_overrides(
            self,
            provider_tools_cfg,
            session_state,
            markdown_logger,
            route_id,
        )

    def _select_fallback_route(
        self,
        primary_route: Optional[str],
        provider_id: Optional[str],
        primary_model: str,
        explicit_candidates: List[str],
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """Choose a fallback route honoring routing prefs and route health."""
        ordered: List[str] = []
        skipped: List[Dict[str, Any]] = []
        seen = set()

        def add_candidate(candidate: Optional[str]) -> None:
            if not candidate:
                return
            if candidate in seen:
                return
            seen.add(candidate)
            ordered.append(candidate)

        for item in explicit_candidates or []:
            add_candidate(str(item))

        if provider_id == "openrouter":
            try:
                _, base_model, _ = provider_router.get_provider_config(primary_route or primary_model)
            except Exception:
                base_model = None
            if base_model and base_model.startswith("openai/"):
                add_candidate(base_model)

        for candidate in ordered:
            try:
                descriptor, resolved = provider_router.get_runtime_descriptor(candidate)
            except Exception:
                skipped.append({"route": candidate, "reason": "descriptor_lookup_failed"})
                continue
            if self.route_health.is_circuit_open(candidate) or self.route_health.is_circuit_open(resolved):
                skipped.append({"route": candidate, "reason": "circuit_open"})
                continue
            return candidate, {"candidates": ordered, "skipped": skipped, "selected": candidate}

        return None, {"candidates": ordered, "skipped": skipped, "selected": None}
    
    # ===== Agent Schema v2 helpers =====
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
        try:
            modes = self.config.get("modes", []) or []
            if modes and isinstance(modes, list):
                name = modes[0].get("name")
                if name:
                    return str(name)
        except Exception:
            pass
        return None

    def _get_mode_config(self, mode_name: Optional[str]) -> Dict[str, Any]:
        if not mode_name:
            return {}
        try:
            for m in self.config.get("modes", []) or []:
                if m.get("name") == mode_name:
                    return m
        except Exception:
            pass
        return {}

    def _filter_tools_by_mode(self, tool_defs: List[ToolDefinition], mode_cfg: Dict[str, Any]) -> List[ToolDefinition]:
        try:
            enabled = mode_cfg.get("tools_enabled")
            disabled = mode_cfg.get("tools_disabled") or []
            if not enabled and not disabled:
                return tool_defs
            enabled_set = set(enabled or [])
            disabled_set = set(disabled)
            out: List[ToolDefinition] = []
            for t in tool_defs:
                name = t.name
                if name in disabled_set:
                    continue
                if enabled and "*" not in enabled_set and name not in enabled_set:
                    continue
                out.append(t)
            return out or tool_defs
        except Exception:
            return tool_defs

    def _apply_turn_strategy_from_loop(self) -> None:
        apply_turn_strategy_from_loop(self)

    def _get_prompt_cache_control(self) -> Optional[Dict[str, Any]]:
        provider_cfg = getattr(self, "_provider_tools_effective", None) or (self.config.get("provider_tools") or {})
        return get_prompt_cache_control({"provider_tools": provider_cfg})

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

    def _prepare_concurrency_policy(self) -> Dict[str, Any]:
        return prepare_concurrency_policy(self.config)

    def _tool_defs_from_yaml(self) -> Optional[List[ToolDefinition]]:
        return tool_defs_from_yaml(self)

    def _completion_tool_config_enabled(self) -> bool:
        return completion_tool_config_enabled(self.config)

    def _ensure_completion_tool(self, tool_defs: List[ToolDefinition]) -> List[ToolDefinition]:
        """Ensure mark_task_complete tool is present in the tool definitions."""
        return ensure_completion_tool(tool_defs)

    @staticmethod
    def _is_read_only_tool(tool_name: str) -> bool:
        """Classify tools that only inspect state (used for completion heuristics)."""
        return is_read_only_tool(tool_name)

    @staticmethod
    def _normalize_assistant_text(text: Optional[str]) -> str:
        return normalize_assistant_text(text)

    def _dump_tool_defs(self, tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
        return _dump_tool_defs(tool_defs)

    def _record_lsp_reward_metrics(
        self,
        session_state: SessionState,
        turn_index: int,
    ) -> None:
        record_lsp_reward_metrics(self, session_state, turn_index)

    def _record_test_reward_metric(
        self,
        session_state: SessionState,
        turn_index: int,
        success_value: Optional[float],
    ) -> None:
        record_test_reward_metric(session_state, turn_index, success_value)

    def _register_prompt_hash(self, prompt_type: str, content: Optional[str], turn_index: Optional[int] = None) -> None:
        register_prompt_hash(self, prompt_type, content, turn_index)

    def _build_prompt_summary(self) -> Optional[Dict[str, Any]]:
        return build_prompt_summary(self)

    def _write_workspace_manifest(self) -> None:
        write_workspace_manifest(self)

    def _write_env_fingerprint(self) -> None:
        write_env_fingerprint(self)

    def _capture_turn_diagnostics(
        self,
        session_state: SessionState,
        provider_result: Optional[ProviderResult],
        allowed_tools: Optional[List[str]],
    ) -> Dict[str, Any]:
        diag = capture_turn_diagnostics(
            self,
            session_state,
            provider_result,
            allowed_tools,
            todo_seed_names=list(TODO_SEED_TOOL_NAMES),
        )
        if diag.get("has_todo_seed"):
            session_state.set_provider_metadata("todo_seed_completed", True)
        self._turn_diagnostics.append(diag)
        if len(self._turn_diagnostics) > 400:
            self._turn_diagnostics = self._turn_diagnostics[-400:]
        # Delegate plan bootstrap warning to guardrail orchestrator
        try:
            self.guardrail_orchestrator.maybe_emit_plan_bootstrap_warning(session_state, diag)
        except Exception:
            pass
        return diag

    def _maybe_run_plan_bootstrap(self, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
        maybe_run_plan_bootstrap(self, session_state, markdown_logger)

    def _should_require_build_guard(self, user_prompt: str) -> bool:
        return should_require_build_guard(self, user_prompt)

    def _todo_config(self) -> Dict[str, Any]:
        return self.guardrail_coordinator.todo_config()

    def _build_turn_context(self, session_state: SessionState, parsed_calls: List[Any]) -> TurnContext:
        return build_turn_context(self, session_state, parsed_calls)

    def _summarize_execution_results(
        self,
        turn_ctx: TurnContext,
        executed_results: List[Any],
        session_state: SessionState,
        turn_index_int: Optional[int],
    ) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        return summarize_execution_results(self, turn_ctx, executed_results, session_state, turn_index_int)

    def _emit_turn_snapshot(self, session_state: SessionState, turn_ctx: TurnContext) -> None:
        emit_turn_snapshot(self, session_state, turn_ctx)

    def _hydrate_turn_context_signals(
        self,
        session_state: SessionState,
        turn_ctx: TurnContext,
    ) -> None:
        hydrate_turn_context_signals(session_state, turn_ctx)

    def _finalize_turn_context_snapshot(
        self,
        session_state: SessionState,
        turn_ctx: TurnContext,
        turn_index: Optional[int],
    ) -> None:
        finalize_turn_context_snapshot(self, session_state, turn_ctx, turn_index)

    def _apply_turn_guards(self, turn_ctx: TurnContext, session_state: SessionState) -> List[Any]:
        return apply_turn_guards(self, turn_ctx, session_state)

    def _handle_blocked_calls(
        self,
        turn_ctx: TurnContext,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> None:
        handle_blocked_calls(self, turn_ctx, session_state, markdown_logger)

    def _resolve_replay_todo_placeholders(self, session_state: SessionState, parsed_call: Any) -> None:
        resolve_replay_todo_placeholders(self, session_state, parsed_call)

    def _maybe_transition_plan_mode(self, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
        maybe_transition_plan_mode(self, session_state, markdown_logger)

    def _prompts_config_with_todos(self) -> Dict[str, Any]:
        todos_cfg = self._todo_config()
        if not todos_cfg["enabled"]:
            return self.config
        cfg = copy.deepcopy(self.config)
        prompts_cfg = cfg.setdefault("prompts", {})
        packs = prompts_cfg.setdefault("packs", {})
        base_pack = packs.setdefault("base", {})
        base_pack.setdefault("todo_plan", "implementations/prompts/todos/plan.md")
        base_pack.setdefault("todo_build", "implementations/prompts/todos/build.md")
        injection = prompts_cfg.setdefault("injection", {})
        system_order = injection.setdefault("system_order", [])
        if "@pack(base).todo_plan" not in system_order:
            system_order.append("@pack(base).todo_plan")
        if "@pack(base).todo_build" not in system_order:
            system_order.append("@pack(base).todo_build")
        return cfg

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

    @staticmethod
    def _is_test_command(command: str) -> bool:
        if not command:
            return False
        normalized = command.lower()
        test_keywords = (
            "pytest",
            "npm test",
            "yarn test",
            "pnpm test",
            "go test",
            "cargo test",
            "mvn test",
            "gradle test",
            "bundle exec rspec",
            "tox",
        )
        return any(keyword in normalized for keyword in test_keywords)

    def _record_usage_reward_metrics(
        self,
        session_state: SessionState,
        turn_index: int,
        usage_raw: Optional[Dict[str, Any]],
    ) -> None:
        if not isinstance(usage_raw, dict):
            return
        prompt_tokens = usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens")
        completion_tokens = usage_raw.get("completion_tokens") or usage_raw.get("output_tokens")
        try:
            prompt_value = float(prompt_tokens) if prompt_tokens is not None else 0.0
        except (TypeError, ValueError):
            prompt_value = 0.0
        try:
            completion_value = float(completion_tokens) if completion_tokens is not None else 0.0
        except (TypeError, ValueError):
            completion_value = 0.0
        total_tokens = prompt_value + completion_value
        try:
            session_state.add_reward_metric(turn_index, "TE", total_tokens)
            if total_tokens > 0:
                toe_value = completion_value / total_tokens
                session_state.add_reward_metric(turn_index, "TOE", toe_value)
        except Exception:
            pass
        latency = getattr(self, "_last_runtime_latency", None)
        if latency is not None:
            try:
                session_state.add_reward_metric(turn_index, "LE", float(latency))
            except Exception:
                pass
        try:
            spa_value = 0.0 if getattr(self, "_last_html_detected", False) else 1.0
            session_state.add_reward_metric(turn_index, "SPA", spa_value)
        except Exception:
            pass

    @staticmethod
    def _count_diff_hunks(text: Optional[str]) -> int:
        return count_diff_hunks(text)

    @staticmethod
    def _split_patch_blocks(patch_text: str) -> List[str]:
        return split_patch_blocks(patch_text)

    def _normalize_patch_block(self, block_text: str) -> str:
        """Wrap loose OpenCode patch snippets with sentinel markers."""
        return normalize_patch_block(block_text)

    def _fetch_workspace_text(self, path: str) -> str:
        """Best-effort read helper for converting OpenCode patches."""
        return fetch_workspace_text(self, path)

    def _convert_patch_to_unified(self, patch_text: str) -> Optional[str]:
        """Convert OpenCode patch text to a unified diff for legacy executors."""
        return convert_patch_to_unified(self, patch_text)

    def _apply_patch_operations_direct(self, patch_text: str) -> Optional[Dict[str, Any]]:
        """Fallback handler that directly applies simple add-file operations."""
        return apply_patch_operations_direct(self, patch_text)

    def _expand_multi_file_patches(
        self,
        parsed_calls: List[Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> List[Any]:
        return expand_multi_file_patches(self, parsed_calls, session_state, markdown_logger)

    def _emit_patch_policy_violation(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        *,
        chunk_count: int,
        policy_mode: str,
        validation_message: Optional[str],
    ) -> None:
        """Emit a validation error when multi-file patch policy is violated."""
        emit_patch_policy_violation(
            self,
            session_state,
            markdown_logger,
            chunk_count=chunk_count,
            policy_mode=policy_mode,
            validation_message=validation_message,
        )

    def _validate_structural_artifacts(self, session_state: SessionState) -> List[str]:
        """Perform lightweight structural checks on key workspace artifacts."""
        return validate_structural_artifacts(self, session_state)

    def _record_diff_metrics(
        self,
        tool_call: Any,
        result: Dict[str, Any],
        *,
        session_state: Optional[SessionState] = None,
        turn_index: Optional[int] = None,
    ) -> None:
        record_diff_metrics(
            self,
            tool_call,
            result,
            session_state=session_state,
            turn_index=turn_index,
        )

    def create_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.write_text.remote(path, ""))

    def read_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.read_text.remote(path))

    def list_dir(self, path: str, depth: int = 1) -> Dict[str, Any]:
        # Always pass virtual paths when using VirtualizedSandbox; else pass normalized absolute
        if getattr(self, 'using_virtualized', False):
            return self._ray_get(self.sandbox.ls.remote(path, depth))
        target = self._normalize_workspace_path(str(path))
        return self._ray_get(self.sandbox.ls.remote(target, depth))

    def run_shell(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        stream = self._ray_get(self.sandbox.run.remote(command, timeout=timeout or 30, stream=True))
        if isinstance(stream, dict):
            return {
                "stdout": stream.get("stdout", "") or "",
                "stderr": stream.get("stderr", "") or "",
                "exit": stream.get("exit"),
            }
        # Decode adaptive-encoded list materialized; last element is {"exit": code}
        exit_obj = stream[-1] if isinstance(stream, list) and stream else {"exit": None}
        # Collect only string lines, drop adaptive markers (>>>>> ...)
        lines: list[str] = []
        if isinstance(stream, list):
            for x in stream[:-1]:
                if not isinstance(x, str):
                    continue
                if x.startswith(">>>>>"):
                    continue
                lines.append(x)
        stdout = "\n".join(lines)
        return {"stdout": stdout, "exit": exit_obj.get("exit")}

    def run_bash_opencode(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        *,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        OpenCode-compatible `bash` tool execution.

        OpenCode surfaces the raw combined stdout+stderr string as the tool result
        content, preserves trailing newlines, and appends truncation/timeout markers.
        """
        if timeout_ms is not None:
            try:
                timeout_ms = int(timeout_ms)
            except Exception:
                timeout_ms = None
        if timeout_ms is not None and timeout_ms < 0:
            msg = f"Invalid timeout value: {timeout_ms}. Timeout must be a positive number."
            return {"error": msg, "__mvi_text_output": msg}

        effective_timeout_ms = min(timeout_ms or 60_000, 10 * 60_000)
        timeout_s = max(0.0, float(effective_timeout_ms) / 1000.0)

        raw = self._ray_get(self.sandbox.run.remote(command, timeout=timeout_s, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        stdout = ""
        exit_code: Any = None
        if isinstance(decoded, dict):
            stdout = str(decoded.get("stdout") or "")
            exit_code = decoded.get("exit")
        elif isinstance(decoded, str):
            stdout = decoded
        else:
            try:
                stdout = json.dumps(decoded, ensure_ascii=False)
            except Exception:
                stdout = str(decoded)

        # Normalize workspace paths during replays so absolute-path outputs match
        # the recorded (strip-prefix) environment.
        try:
            replay_data = getattr(self, "_replay_session_data", None)
            strip_prefix = getattr(replay_data, "strip_prefix", "") if replay_data else ""
            if strip_prefix and isinstance(strip_prefix, str):
                ws = str(self.workspace or "")
                if ws and ws in stdout:
                    stdout = stdout.replace(ws, strip_prefix)
        except Exception:
            pass

        max_len = 30_000
        if len(stdout) > max_len:
            stdout = stdout[:max_len] + "\n\n(Output was truncated due to length limit)"

        if exit_code == -9:
            stdout += f"\n\n(Command timed out after {effective_timeout_ms} ms)"

        if description:
            # Preserve metadata fields for downstream logging even though the model
            # only receives `__mvi_text_output`.
            return {
                "stdout": stdout,
                "exit": exit_code,
                "description": description,
                "__mvi_text_output": stdout,
            }
        return {"stdout": stdout, "exit": exit_code, "__mvi_text_output": stdout}

    @staticmethod
    def _claude_slugify_path(path: str) -> str:
        raw = str(path or "")
        return re.sub(r"[^A-Za-z0-9]", "-", raw)

    def _claude_display_workspace_root(self) -> str:
        try:
            replay_data = getattr(self, "_replay_session_data", None)
            strip_prefix = getattr(replay_data, "strip_prefix", "") if replay_data else ""
            if isinstance(strip_prefix, str) and strip_prefix.strip():
                return strip_prefix
        except Exception:
            pass
        return str(self.workspace or "")

    def _claude_tasks_root(self) -> str:
        display_ws = self._claude_display_workspace_root()
        slug = self._claude_slugify_path(display_ws)
        return f"/tmp/claude/{slug}/tasks"

    def _claude_bash_rel_cwd(self) -> str:
        rel = getattr(self, "_claude_bash_rel_cwd_state", None)
        if isinstance(rel, str) and rel:
            return rel
        setattr(self, "_claude_bash_rel_cwd_state", ".")
        return "."

    def _claude_bash_set_rel_cwd_from_abs(self, abs_path: str) -> None:
        try:
            ws = str(self.workspace or "")
            if not ws:
                return
            abs_norm = os.path.normpath(str(abs_path))
            ws_norm = os.path.normpath(ws)
            if abs_norm == ws_norm:
                setattr(self, "_claude_bash_rel_cwd_state", ".")
                return
            if abs_norm.startswith(ws_norm + os.sep):
                rel = os.path.relpath(abs_norm, ws_norm)
                setattr(self, "_claude_bash_rel_cwd_state", rel if rel else ".")
        except Exception:
            return

    def run_bash_claude(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        *,
        description: Optional[str] = None,
        run_in_background: bool = False,
        expected_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Claude Code-compatible `Bash` tool execution (best-effort).

        Key observed semantics:
        - Maintains CWD across Bash tool calls (but does not persist env exports).
        - Timeout is "soft": if not complete by timeout, return a background-task
          handle instead of killing the process.
        """
        command = str(command or "")
        argv: List[str] = []
        try:
            argv = shlex.split(command, posix=True) if command.strip() else []
        except Exception:
            argv = command.split() if command.strip() else []

        # Claude Code permission rules can explicitly deny certain bash patterns.
        if self._claude_tool_rule_action("Bash", argv=argv, command=command) == "deny":
            cmd_name = argv[0] if argv else (command.strip() or "<empty>")
            msg = f"Permission to use Bash with command {cmd_name} has been denied."
            return {"error": msg, "__mvi_text_output": msg}
        if timeout_ms is not None:
            try:
                timeout_ms = int(timeout_ms)
            except Exception:
                timeout_ms = None

        # Claude Code defaults to 120_000ms and caps at 600_000ms (10 minutes).
        effective_timeout_ms = 120_000 if timeout_ms is None else timeout_ms
        effective_timeout_ms = max(0, min(int(effective_timeout_ms), 600_000))

        # Deterministic task ids during replay: reuse golden id if present.
        task_id: str = uuid.uuid4().hex[:7]
        if isinstance(expected_output, str):
            match = re.search(r"Command running in background with ID:\\s*([a-z0-9]+)", expected_output)
            if match:
                task_id = match.group(1)

        tasks_root = self._claude_tasks_root()
        out_file = f"{tasks_root}/{task_id}.output"
        pid_file = f"{tasks_root}/{task_id}.pid"
        cmd_file = f"{tasks_root}/{task_id}.cmd"
        killed_file = f"{tasks_root}/{task_id}.killed"
        cwd_file = f"{tasks_root}/{task_id}.cwd"
        exit_file = f"{tasks_root}/{task_id}.exit"

        rel_cwd = self._claude_bash_rel_cwd()
        try:
            cwd_abs = str((Path(str(self.workspace)) / rel_cwd).resolve())
        except Exception:
            cwd_abs = str(self.workspace or "")

        run_bg_flag = "1" if bool(run_in_background) else "0"
        script = r"""
set -o pipefail
CWD="$1"
CMD="$2"
TASK_ROOT="$3"
TASK_ID="$4"
TIMEOUT_MS="$5"
RUN_BG="$6"

mkdir -p "$TASK_ROOT" 2>/dev/null || true

OUT="$TASK_ROOT/$TASK_ID.output"
PIDFILE="$TASK_ROOT/$TASK_ID.pid"
CMDFILE="$TASK_ROOT/$TASK_ID.cmd"
KILLEDFILE="$TASK_ROOT/$TASK_ID.killed"
CWDFILE="$TASK_ROOT/$TASK_ID.cwd"
EXITFILE="$TASK_ROOT/$TASK_ID.exit"

rm -f "$KILLEDFILE" 2>/dev/null || true
printf "%s" "$CMD" > "$CMDFILE" 2>/dev/null || true

( bash -lc 'cd "$1" && eval "$2"; status=$?; pwd > "$3" 2>/dev/null || true; printf "%s" "$status" > "$4" 2>/dev/null || true; exit $status' \
  -- "$CWD" "$CMD" "$CWDFILE" "$EXITFILE" > "$OUT" 2>&1 ) &
PID=$!
printf "%s" "$PID" > "$PIDFILE" 2>/dev/null || true

if [ "$RUN_BG" = "1" ]; then
  printf "Command running in background with ID: %s. Output is being written to: %s\n" "$TASK_ID" "$OUT"
  exit 0
fi

STEP_MS=100
MAX_STEPS=$(( TIMEOUT_MS / STEP_MS ))
if [ $(( TIMEOUT_MS % STEP_MS )) -ne 0 ]; then MAX_STEPS=$(( MAX_STEPS + 1 )); fi

i=0
while kill -0 "$PID" 2>/dev/null; do
  if [ "$i" -ge "$MAX_STEPS" ]; then break; fi
  sleep 0.1
  i=$(( i + 1 ))
done

if kill -0 "$PID" 2>/dev/null; then
  printf "Command running in background with ID: %s. Output is being written to: %s\n" "$TASK_ID" "$OUT"
  exit 0
fi

wait "$PID" >/dev/null 2>&1 || true
cat "$OUT" 2>/dev/null || true
FINAL_CWD="$(cat "$CWDFILE" 2>/dev/null || true)"
printf "\n__KC_CLAUDE_CWD__%s\n" "$FINAL_CWD"
""".strip()

        outer = (
            "bash -lc "
            + shlex.quote(script)
            + " -- "
            + " ".join(
                [
                    shlex.quote(cwd_abs),
                    shlex.quote(command),
                    shlex.quote(tasks_root),
                    shlex.quote(task_id),
                    shlex.quote(str(effective_timeout_ms)),
                    shlex.quote(run_bg_flag),
                ]
            )
        )

        # Ensure the wrapper shell itself doesn't get killed before it can decide
        # whether to return output vs background handle.
        wrapper_timeout_s = int(effective_timeout_ms / 1000) + 30
        wrapper_timeout_s = max(wrapper_timeout_s, 30)

        raw = self._ray_get(self.sandbox.run.remote(outer, timeout=wrapper_timeout_s, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        stdout = ""
        exit_code: Any = None
        if isinstance(decoded, dict):
            stdout = str(decoded.get("stdout") or "")
            exit_code = decoded.get("exit")
        elif isinstance(decoded, str):
            stdout = decoded
        else:
            stdout = str(decoded)

        # Extract the final CWD marker (used to persist cwd between calls).
        marker = "\n__KC_CLAUDE_CWD__"
        if marker in stdout:
            before, after = stdout.rsplit(marker, 1)
            final_cwd = after.strip("\n")
            stdout = before
            if final_cwd:
                self._claude_bash_set_rel_cwd_from_abs(final_cwd)

        # Normalize workspace paths during replays so absolute-path outputs match
        # the recorded (strip-prefix) environment.
        try:
            replay_data = getattr(self, "_replay_session_data", None)
            strip_prefix = getattr(replay_data, "strip_prefix", "") if replay_data else ""
            if strip_prefix and isinstance(strip_prefix, str):
                ws = str(self.workspace or "")
                if ws and ws in stdout:
                    stdout = stdout.replace(ws, strip_prefix)
        except Exception:
            pass

        if description:
            return {"stdout": stdout, "exit": exit_code, "description": description, "__mvi_text_output": stdout}
        return {"stdout": stdout, "exit": exit_code, "__mvi_text_output": stdout}

    def _claude_permission_mode(self) -> str:
        try:
            cfg = self.config.get("claude") if isinstance(self.config, dict) else None
            if isinstance(cfg, dict):
                mode = cfg.get("permission_mode") or cfg.get("permissionMode") or ""
            else:
                mode = ""
        except Exception:
            mode = ""
        mode = str(mode or "").strip().lower()
        return mode or "bypasspermissions"

    def _claude_tool_rule_action(
        self,
        tool_name: str,
        *,
        argv: Optional[List[str]] = None,
        command: str = "",
        file_path: str = "",
    ) -> Optional[str]:
        """
        Minimal Claude Code allow/ask/deny rule engine for parity suites.

        Rule precedence: deny > ask > allow.
        Patterns are Claude-style strings like `Bash(pwd:*)`.
        """
        cfg = None
        try:
            cfg = self.config.get("claude") if isinstance(self.config, dict) else None
        except Exception:
            cfg = None
        if not isinstance(cfg, dict):
            return None
        rules = cfg.get("tool_rules") or cfg.get("toolRules") or {}
        if not isinstance(rules, dict):
            return None

        deny = rules.get("deny") or []
        ask = rules.get("ask") or []
        allow = rules.get("allow") or []

        def _coerce_list(value: Any) -> List[str]:
            if not value:
                return []
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [str(item) for item in value if str(item).strip()]
            return []

        deny_list = _coerce_list(deny)
        ask_list = _coerce_list(ask)
        allow_list = _coerce_list(allow)

        for pat in deny_list:
            if self._claude_tool_rule_matches(tool_name, pat, argv=argv, command=command, file_path=file_path):
                return "deny"
        for pat in ask_list:
            if self._claude_tool_rule_matches(tool_name, pat, argv=argv, command=command, file_path=file_path):
                return "ask"
        for pat in allow_list:
            if self._claude_tool_rule_matches(tool_name, pat, argv=argv, command=command, file_path=file_path):
                return "allow"
        return None

    @staticmethod
    def _kc_wildcard_match(value: str, pattern: str) -> bool:
        pat = str(pattern or "")
        text = str(value or "")
        try:
            escaped = re.escape(pat)
            escaped = escaped.replace(r"\*", ".*").replace(r"\?", ".")
            return re.match("^" + escaped + "$", text, flags=re.S) is not None
        except Exception:
            return False

    def _claude_tool_rule_matches(
        self,
        tool_name: str,
        raw_pattern: str,
        *,
        argv: Optional[List[str]] = None,
        command: str = "",
        file_path: str = "",
    ) -> bool:
        pattern = str(raw_pattern or "").strip()
        if not pattern:
            return False
        match = re.match(r"^([A-Za-z0-9_]+)(?:\((.*)\))?$", pattern)
        if not match:
            return False
        pat_tool = match.group(1) or ""
        spec = match.group(2)
        if pat_tool.lower() != str(tool_name or "").strip().lower():
            return False

        if tool_name.lower() == "bash":
            if spec is None or not str(spec).strip():
                return True
            argv_list = argv or []
            if not argv_list and command.strip():
                try:
                    argv_list = shlex.split(command, posix=True)
                except Exception:
                    argv_list = command.split()
            if not argv_list:
                return False

            spec_text = str(spec or "")
            if ":" not in spec_text:
                return self._kc_wildcard_match(" ".join(argv_list), spec_text.strip())
            left, right = spec_text.split(":", 1)
            left = left.strip()
            right = right.strip()
            try:
                left_tokens = shlex.split(left, posix=True) if left else []
            except Exception:
                left_tokens = left.split() if left else []
            if left_tokens:
                if len(argv_list) < len(left_tokens):
                    return False
                for idx, token_pat in enumerate(left_tokens):
                    if not self._kc_wildcard_match(argv_list[idx], token_pat):
                        return False
                rest = " ".join(argv_list[len(left_tokens) :])
            else:
                rest = " ".join(argv_list)
            if not right:
                return True
            return self._kc_wildcard_match(rest, right)

        # TODO: extend rule matching for file tools (Write/Edit/WebFetch) as parity expands.
        _ = file_path
        return True

    def _claude_task_output(
        self,
        task_id: str,
        *,
        block: bool,
        timeout_ms: int,
        expected_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        tasks_root = self._claude_tasks_root()
        out_file = f"{tasks_root}/{task_id}.output"
        pid_file = f"{tasks_root}/{task_id}.pid"
        killed_file = f"{tasks_root}/{task_id}.killed"
        exit_file = f"{tasks_root}/{task_id}.exit"

        effective_timeout_ms = int(max(0, min(int(timeout_ms), 600_000)))
        # Replay helper: if the golden says the task is complete, allow TaskOutput
        # to wait long enough even if the requested timeout is small.
        if isinstance(expected_output, str) and "<status>completed</status>" in expected_output:
            effective_timeout_ms = max(effective_timeout_ms, 600_000)
        if isinstance(expected_output, str) and "<status>killed</status>" in expected_output:
            effective_timeout_ms = max(effective_timeout_ms, 600_000)

        script = r"""
set -o pipefail
TASK_ROOT="$1"
TASK_ID="$2"
BLOCK="$3"
TIMEOUT_MS="$4"

OUT="$TASK_ROOT/$TASK_ID.output"
PIDFILE="$TASK_ROOT/$TASK_ID.pid"
KILLEDFILE="$TASK_ROOT/$TASK_ID.killed"
EXITFILE="$TASK_ROOT/$TASK_ID.exit"

PID="$(cat "$PIDFILE" 2>/dev/null || true)"
if [ -z "$PID" ]; then
  echo "__KC_TASK_STATE__missing"
  echo "__KC_EXIT_CODE__"
  echo "__KC_OUTPUT_BEGIN__"
  echo "__KC_OUTPUT_END__"
  exit 0
fi

if [ "$BLOCK" = "1" ]; then
  STEP_MS=100
  MAX_STEPS=$(( TIMEOUT_MS / STEP_MS ))
  if [ $(( TIMEOUT_MS % STEP_MS )) -ne 0 ]; then MAX_STEPS=$(( MAX_STEPS + 1 )); fi
  i=0
  while kill -0 "$PID" 2>/dev/null; do
    if [ "$i" -ge "$MAX_STEPS" ]; then break; fi
    sleep 0.1
    i=$(( i + 1 ))
  done
fi

STATE="completed"
if kill -0 "$PID" 2>/dev/null; then
  STATE="running"
elif [ -f "$KILLEDFILE" ]; then
  STATE="killed"
fi

EXIT_CODE=""
if [ "$STATE" = "completed" ]; then
  EXIT_CODE="$(cat "$EXITFILE" 2>/dev/null || true)"
fi

echo "__KC_TASK_STATE__${STATE}"
echo "__KC_EXIT_CODE__${EXIT_CODE}"
echo "__KC_OUTPUT_BEGIN__"
cat "$OUT" 2>/dev/null || true
echo
echo "__KC_OUTPUT_END__"
""".strip()

        outer = (
            "bash -lc "
            + shlex.quote(script)
            + " -- "
            + " ".join(
                [
                    shlex.quote(tasks_root),
                    shlex.quote(task_id),
                    shlex.quote("1" if block else "0"),
                    shlex.quote(str(effective_timeout_ms)),
                ]
            )
        )

        # TaskOutput itself should return quickly unless explicitly blocking.
        wrapper_timeout_s = int(effective_timeout_ms / 1000) + 10 if block else 30
        wrapper_timeout_s = max(wrapper_timeout_s, 30)

        raw = self._ray_get(self.sandbox.run.remote(outer, timeout=wrapper_timeout_s, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        blob = ""
        if isinstance(decoded, dict):
            blob = str(decoded.get("stdout") or "")
        else:
            blob = str(decoded or "")

        state = "missing"
        exit_code: Optional[str] = None
        output_text = ""

        try:
            state_match = re.search(r"^__KC_TASK_STATE__(.*)$", blob, flags=re.MULTILINE)
            if state_match:
                state = state_match.group(1).strip() or state
            exit_match = re.search(r"^__KC_EXIT_CODE__(.*)$", blob, flags=re.MULTILINE)
            if exit_match:
                exit_code = exit_match.group(1).strip() or None
            start_idx = blob.find("__KC_OUTPUT_BEGIN__")
            end_idx = blob.find("__KC_OUTPUT_END__")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                start_idx = blob.find("\n", start_idx)
                if start_idx != -1:
                    output_text = blob[start_idx + 1 : end_idx].lstrip("\n")
        except Exception:
            state = "missing"

        retrieval_status = "success" if state in {"completed", "killed"} else "not_ready"
        parts: List[str] = [
            f"<retrieval_status>{retrieval_status}</retrieval_status>",
            f"<task_id>{task_id}</task_id>",
            "<task_type>local_bash</task_type>",
            f"<status>{state}</status>",
        ]
        if state == "completed":
            parts.append(f"<exit_code>{exit_code or 0}</exit_code>")

        output_payload = str(output_text or "")
        if output_payload:
            if not output_payload.endswith("\n"):
                output_payload += "\n"
            parts.append(f"<output>\n{output_payload}</output>")

        rendered = "\n\n".join(parts)
        return {"__mvi_text_output": rendered}

    def _claude_kill_shell(self, shell_id: str) -> Dict[str, Any]:
        tasks_root = self._claude_tasks_root()
        pid_file = f"{tasks_root}/{shell_id}.pid"
        cmd_file = f"{tasks_root}/{shell_id}.cmd"
        killed_file = f"{tasks_root}/{shell_id}.killed"

        script = r"""
set -o pipefail
TASK_ROOT="$1"
SHELL_ID="$2"
PIDFILE="$TASK_ROOT/$SHELL_ID.pid"
CMDFILE="$TASK_ROOT/$SHELL_ID.cmd"
KILLEDFILE="$TASK_ROOT/$SHELL_ID.killed"

PID="$(cat "$PIDFILE" 2>/dev/null || true)"
touch "$KILLEDFILE" 2>/dev/null || true

if [ -n "$PID" ]; then
  kill "$PID" 2>/dev/null || true
  sleep 0.05
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" 2>/dev/null || true
  fi
fi

echo "__KC_CMD_BEGIN__"
cat "$CMDFILE" 2>/dev/null || true
echo
echo "__KC_CMD_END__"
""".strip()

        outer = "bash -lc " + shlex.quote(script) + " -- " + " ".join([shlex.quote(tasks_root), shlex.quote(shell_id)])
        raw = self._ray_get(self.sandbox.run.remote(outer, timeout=30, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        stdout = ""
        if isinstance(decoded, dict):
            stdout = str(decoded.get("stdout") or "")
        else:
            stdout = str(decoded or "")

        cmd = ""
        try:
            start = stdout.find("__KC_CMD_BEGIN__")
            end = stdout.find("__KC_CMD_END__")
            if start != -1 and end != -1 and end > start:
                start = stdout.find("\n", start)
                if start != -1:
                    cmd = stdout[start + 1 : end].strip("\n")
        except Exception:
            cmd = ""

        message = f"Successfully killed shell: {shell_id}"
        if cmd:
            message += f"\nCommand: {cmd}"
        return {"__mvi_text_output": message}

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.vcs.remote(request))

    def _normalize_workspace_path(self, path_in: str) -> str:
        """Normalize a tool-supplied path so it stays within the workspace root."""
        return normalize_workspace_path(self, path_in)
    
    def _exec_raw(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Raw tool execution without enhanced features (for compatibility)"""
        name = tool_call["function"]
        args = tool_call["arguments"]
        original_name = name

        def _coerce_str(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            try:
                return str(value)
            except Exception:
                return ""

        def _first_arg(*keys: str) -> str:
            if not isinstance(args, dict):
                return ""
            for key in keys:
                if key in args:
                    raw = args.get(key)
                    if raw is None:
                        continue
                    text = _coerce_str(raw)
                    if text:
                        return text
            return ""

        # Normalize common aliases used by provider tool names
        normalized = name.lower()
        if normalized == "bash":
            normalized = "run_shell"
        elif normalized == "shell_command":
            normalized = "shell_command"
        elif normalized == "taskoutput":
            normalized = "taskoutput"
        elif normalized == "killshell":
            normalized = "killshell"
        elif normalized == "list":
            normalized = "list_dir"
        elif normalized == "read":
            normalized = "read_file"
        elif normalized == "edit":
            normalized = "apply_search_replace"
        elif normalized == "write":
            normalized = "create_file_from_block"

        if normalized == "create_file":
            target = self._normalize_workspace_path(str(args.get("path", "")))
            return self.create_file(target)
        if normalized == "create_file_from_block":
            raw_target = _first_arg("file_name", "filePath", "file_path", "path", "filename", "file")
            if not raw_target:
                return {"error": "write missing required file_name"}
            content = _coerce_str(args.get("content", "") if isinstance(args, dict) else "")

            # Claude Code `Write` is a distinct surface from OpenCode `write`:
            # - allow absolute paths and ../ traversal (mirrors Claude CLI behavior)
            # - return "File created successfully at: <original_path>"
            if original_name == "Write":
                requested_path = ""
                side_effect_path = ""
                if isinstance(args, dict):
                    requested_path = _coerce_str(args.get("file_path") or args.get("filePath") or raw_target)
                    # ReplaySession may provide both `file_path` (original) and `filePath`
                    # (workspace-mapped) values. Use `filePath` for hermetic side-effects
                    # but preserve the original `file_path` string in tool output.
                    side_effect_path = _coerce_str(args.get("filePath") or requested_path)
                else:
                    requested_path = raw_target
                    side_effect_path = raw_target

                # Claude Code `acceptEdits` auto-accepts edits inside the working
                # directory tree, but still requires explicit permission for writes
                # outside that tree (e.g. /tmp).
                if self._claude_permission_mode() == "acceptedits":
                    ws_root = Path(str(self.workspace)).resolve()
                    allowed = False
                    try:
                        requested = Path(requested_path)
                        resolved = requested.resolve(strict=False) if requested.is_absolute() else (ws_root / requested_path).resolve(strict=False)
                        resolved.relative_to(ws_root)
                        allowed = True
                    except Exception:
                        allowed = False
                    if not allowed:
                        msg = (
                            f"Claude requested permissions to write to {requested_path}, but you haven't granted it yet."
                        )
                        return {"error": msg, "__mvi_text_output": msg}

                target_path: Optional[Path] = None
                try:
                    candidate = Path(side_effect_path)
                    if candidate.is_absolute():
                        target_path = candidate
                    else:
                        target_path = Path(str(self.workspace)) / side_effect_path
                except Exception:
                    target_path = None

                # Replay runs should avoid mutating captured golden artifacts.
                replay_data = getattr(self, "_replay_session_data", None)
                if replay_data and target_path is not None:
                    try:
                        if str(target_path).startswith(str(self.workspace)) and target_path.exists():
                            pass
                    except Exception:
                        pass

                if target_path is None:
                    msg = f"Error: failed to resolve path {requested_path}"
                    return {"error": msg, "__mvi_text_output": msg}

                try:
                    self._ray_get(self.sandbox.write_text.remote(str(target_path), content))
                except Exception as exc:
                    msg = f"Error: failed to write file {requested_path}: {exc}"
                    return {"error": msg, "__mvi_text_output": msg}

                output = f"File created successfully at: {requested_path}"
                return {"ok": True, "path": requested_path, "__mvi_text_output": output}

            target = self._normalize_workspace_path(raw_target)
            result = self._ray_get(self.sandbox.write_text.remote(target, content))
            if original_name.lower() == "write" and isinstance(result, dict):
                result = dict(result)
                result["__mvi_text_output"] = ""
            return result
        if normalized == "read_file":
            raw_target = _first_arg("path", "file_path", "file_name", "filePath", "filename", "file")
            target = self._normalize_workspace_path(raw_target)
            return self.read_file(target)
        if normalized == "list_dir":
            raw_target = _first_arg("path", "dir", "directory", "file_path", "file_name", "filePath")
            target = self._normalize_workspace_path(raw_target)
            depth = int(args.get("depth", 1))
            return self.list_dir(target, depth)
        if normalized == "run_shell":
            command = _first_arg("command", "cmd", "input")
            if not command:
                return {"error": "run_shell missing required command"}
            timeout_val = args.get("timeout") if isinstance(args, dict) else None
            if original_name == "Bash":
                desc = _coerce_str(args.get("description") if isinstance(args, dict) else "")
                run_bg = bool(args.get("run_in_background")) if isinstance(args, dict) else False
                expected = tool_call.get("expected_output")
                expected_str = expected if isinstance(expected, str) else None
                return self.run_bash_claude(
                    command,
                    timeout_val,
                    description=desc or None,
                    run_in_background=run_bg,
                    expected_output=expected_str,
                )
            if original_name.lower() == "bash":
                desc = _coerce_str(args.get("description") if isinstance(args, dict) else "")
                return self.run_bash_opencode(command, timeout_val, description=desc or None)
            return self.run_shell(command, timeout_val)
        if normalized == "taskoutput":
            if not isinstance(args, dict):
                return {"error": "TaskOutput missing required arguments", "__mvi_text_output": "{\"error\":\"TaskOutput missing required arguments\"}"}
            task_id = _coerce_str(args.get("task_id", "")).strip()
            if not task_id:
                msg = "TaskOutput missing required task_id"
                return {"error": msg, "__mvi_text_output": json.dumps({"error": msg}, ensure_ascii=False, separators=(",", ":"))}
            block = True
            if "block" in args:
                block = bool(args.get("block"))
            timeout_val = args.get("timeout")
            try:
                timeout_ms = 30_000 if timeout_val is None else int(timeout_val)
            except Exception:
                timeout_ms = 30_000
            timeout_ms = max(0, min(timeout_ms, 600_000))

            expected = tool_call.get("expected_output")
            expected_str = expected if isinstance(expected, str) else None
            return self._claude_task_output(task_id, block=block, timeout_ms=timeout_ms, expected_output=expected_str)
        if normalized == "killshell":
            if not isinstance(args, dict):
                msg = "KillShell missing required arguments"
                return {"error": msg, "__mvi_text_output": json.dumps({"error": msg}, ensure_ascii=False, separators=(",", ":"))}
            shell_id = _coerce_str(args.get("shell_id", "")).strip()
            if not shell_id:
                msg = "KillShell missing required shell_id"
                return {"error": msg, "__mvi_text_output": json.dumps({"error": msg}, ensure_ascii=False, separators=(",", ":"))}
            return self._claude_kill_shell(shell_id)
        if normalized == "task":
            if not isinstance(args, dict):
                msg = "task missing required arguments"
                return {"error": msg, "__mvi_text_output": msg}
            description = _coerce_str(args.get("description") or "").strip()
            prompt = _coerce_str(args.get("prompt") or "").strip()
            subagent_type = _coerce_str(args.get("subagent_type") or "").strip()
            if not description or not prompt or not subagent_type:
                missing = []
                if not description:
                    missing.append("description")
                if not prompt:
                    missing.append("prompt")
                if not subagent_type:
                    missing.append("subagent_type")
                msg = f"missing required field: {', '.join(missing)}"
                return {"error": msg, "__mvi_text_output": msg}

            digest = hashlib.sha256(f"{description}|{subagent_type}|{prompt}".encode("utf-8")).hexdigest()[:12]
            session_id = f"task_{digest}"
            from .runtime_context import get_current_session_state

            parent_state = get_current_session_state()
            parent_emit = getattr(parent_state, "emit_runtime_event", None) if parent_state is not None else None
            permission_queue = None
            try:
                if parent_state is not None and hasattr(parent_state, "get_provider_metadata"):
                    permission_queue = parent_state.get_provider_metadata("permission_queue")
            except Exception:
                permission_queue = None

            # Replay mode: use the recorded Task tool output instead of executing a live
            # nested subagent (which would require non-deterministic model calls).
            try:
                expected = tool_call.get("expected_output")
                expected_str = expected if isinstance(expected, str) else None
            except Exception:
                expected_str = None
            try:
                replay_mode = bool(parent_state.get_provider_metadata("replay_mode")) if parent_state is not None else False
            except Exception:
                replay_mode = False
            if replay_mode and expected_str:
                return {"output": expected_str, "__mvi_text_output": expected_str}

            if callable(parent_emit):
                try:
                    parent_emit(
                        "task_event",
                        {
                            "kind": "started",
                            "sessionId": session_id,
                            "description": description,
                            "subagent_type": subagent_type,
                        },
                    )
                except Exception:
                    pass

            task_cfg = (self.config.get("task_tool") or {}) if isinstance(getattr(self, "config", None), dict) else {}
            streaming_cfg = (task_cfg.get("streaming") or {}) if isinstance(task_cfg, dict) else {}
            forward_assistant_messages = bool(
                (streaming_cfg.get("forward_assistant_messages") if isinstance(streaming_cfg, dict) else False)
            )
            subagents_cfg = (task_cfg.get("subagents") or {}) if isinstance(task_cfg, dict) else {}
            known_subagents = {"general", "build", "plan"}
            if isinstance(subagents_cfg, dict):
                known_subagents |= {str(k) for k in subagents_cfg.keys() if k}

            # Claude Code also supports custom agents defined in `.claude/agents/*.md`.
            try:
                agents_dir = Path(str(self.workspace)) / ".claude" / "agents"
                if agents_dir.is_dir():
                    for agent_path in agents_dir.glob("*.md"):
                        stem = agent_path.stem.strip()
                        if stem:
                            known_subagents.add(stem)
            except Exception:
                pass

            if subagent_type not in known_subagents:
                msg = f"Unknown agent type: {subagent_type} is not a valid agent type"
                return {"error": msg, "__mvi_text_output": msg}

            sub_cfg: Optional[Dict[str, Any]] = None
            if isinstance(subagents_cfg, dict):
                raw_sub_cfg = subagents_cfg.get(subagent_type)
                if isinstance(raw_sub_cfg, dict):
                    sub_cfg = raw_sub_cfg

            replay_path: Optional[str] = None
            try:
                if isinstance(sub_cfg, dict):
                    replay_path = sub_cfg.get("replay_session") or sub_cfg.get("session")
                if not replay_path and isinstance(task_cfg, dict):
                    replay_map = task_cfg.get("replay_sessions") or {}
                    if isinstance(replay_map, dict):
                        replay_path = replay_map.get(subagent_type)
            except Exception:
                replay_path = None

            output_text = ""
            summary_parts: List[Dict[str, Any]] = []
            if replay_path:
                try:
                    child_entries = json.loads(Path(str(replay_path)).read_text(encoding="utf-8"))
                except Exception as exc:
                    msg = f"Failed to load task replay session: {exc}"
                    return {"error": msg, "__mvi_text_output": msg}
                if isinstance(child_entries, list):
                    for entry in child_entries:
                        if not isinstance(entry, dict) or entry.get("role") != "assistant":
                            continue
                        for part in entry.get("parts", []) or []:
                            if not isinstance(part, dict):
                                continue
                            if part.get("type") == "tool":
                                summary_parts.append(part)
                            elif part.get("type") == "text":
                                output_text = str(part.get("text") or "")
                summary_parts.sort(key=lambda p: str(p.get("id") or ""))
            else:
                # Live nested subagent run (best-effort parity with OpenCode TaskTool).
                child_event_emitter = None
                if callable(parent_emit):
                    def _child_emitter(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
                        try:
                            et = str(event_type or "")
                            if et in {"tool_call", "tool_result"} or et.startswith("permission_"):
                                parent_emit(
                                    "task_event",
                                    {
                                        "kind": et,
                                        "sessionId": session_id,
                                        "subagent_type": subagent_type,
                                        "turn": turn,
                                        "payload": dict(payload or {}),
                                    },
                                )
                        except Exception:
                            pass

                    child_event_emitter = _child_emitter
                try:
                    model_route = ""
                    if isinstance(sub_cfg, dict) and sub_cfg.get("model"):
                        model_route = str(sub_cfg.get("model") or "").strip()
                    if not model_route and isinstance(task_cfg, dict) and task_cfg.get("default_model"):
                        model_route = str(task_cfg.get("default_model") or "").strip()
                    if not model_route:
                        model_route = str(getattr(self, "_current_route_id", "") or "").strip()
                    if not model_route:
                        providers_cfg = (self.config.get("providers") or {}) if isinstance(getattr(self, "config", None), dict) else {}
                        model_route = str((providers_cfg.get("default_model") or "")).strip()
                    if not model_route:
                        model_route = "openai"

                    max_steps = 24
                    try:
                        if isinstance(sub_cfg, dict) and sub_cfg.get("max_steps") is not None:
                            max_steps = int(sub_cfg.get("max_steps") or max_steps)
                        elif isinstance(task_cfg, dict) and task_cfg.get("default_max_steps") is not None:
                            max_steps = int(task_cfg.get("default_max_steps") or max_steps)
                    except Exception:
                        max_steps = 24
                    if max_steps < 1:
                        max_steps = 1

                    allowed_tools = self._task_tool_allowed_tools(sub_cfg)
                    base_child_config: Dict[str, Any] = copy.deepcopy(self.config) if isinstance(getattr(self, "config", None), dict) else {}
                    overrides: Dict[str, Any] = {}
                    if isinstance(task_cfg, dict) and isinstance(task_cfg.get("child_config_overrides"), dict):
                        overrides = self._deep_merge_dict(overrides, task_cfg.get("child_config_overrides"))
                    if isinstance(sub_cfg, dict) and isinstance(sub_cfg.get("config_overrides"), dict):
                        overrides = self._deep_merge_dict(overrides, sub_cfg.get("config_overrides"))
                    child_config = self._deep_merge_dict(base_child_config, overrides) if overrides else base_child_config

                    # Child runs should be stateless and avoid plan/todo scaffolding.
                    feats = child_config.get("features") if isinstance(child_config, dict) else None
                    feats = feats if isinstance(feats, dict) else {}
                    feats["plan"] = False
                    todos_cfg = feats.get("todos")
                    todos_cfg = todos_cfg if isinstance(todos_cfg, dict) else {}
                    todos_cfg["enabled"] = False
                    feats["todos"] = todos_cfg
                    child_config["features"] = feats

                    guardrails_cfg = child_config.get("guardrails")
                    guardrails_cfg = guardrails_cfg if isinstance(guardrails_cfg, dict) else {}
                    pb_cfg = guardrails_cfg.get("plan_bootstrap")
                    pb_cfg = pb_cfg if isinstance(pb_cfg, dict) else {}
                    pb_cfg["strategy"] = "never"
                    guardrails_cfg["plan_bootstrap"] = pb_cfg
                    child_config["guardrails"] = guardrails_cfg

                    completion_cfg = child_config.get("completion")
                    completion_cfg = completion_cfg if isinstance(completion_cfg, dict) else {}
                    completion_cfg["allow_content_only_completion"] = True
                    child_config["completion"] = completion_cfg

                    # Tool visibility: subagent inherits config but gets a narrowed tool set.
                    tools_cfg = child_config.get("tools")
                    tools_cfg = tools_cfg if isinstance(tools_cfg, dict) else {}
                    registry_cfg = tools_cfg.get("registry")
                    registry_cfg = registry_cfg if isinstance(registry_cfg, dict) else {}
                    if allowed_tools:
                        registry_cfg["include"] = list(allowed_tools)
                    tools_cfg["registry"] = registry_cfg
                    child_config["tools"] = tools_cfg

                    # Optional: run the child using replay mode (deterministic tests / fixtures).
                    child_replay_session = None
                    if isinstance(sub_cfg, dict):
                        child_replay_session = sub_cfg.get("child_replay_session") or sub_cfg.get("child_replay_session_path")
                    if child_replay_session:
                        replay_cfg = child_config.get("replay")
                        replay_cfg = replay_cfg if isinstance(replay_cfg, dict) else {}
                        replay_cfg["session_path"] = str(child_replay_session)
                        replay_cfg.setdefault("preserve_tool_names", True)
                        child_config["replay"] = replay_cfg
                        model_route = "replay"

                    run_payload = self._task_tool_run_subagent(
                        prompt=prompt,
                        model_route=model_route,
                        max_steps=max_steps,
                        child_config=child_config,
                        event_emitter=child_event_emitter,
                        permission_queue=permission_queue,
                    )
                    output_text = self._task_tool_extract_last_assistant_text(run_payload.get("messages"))
                    run_dir = run_payload.get("run_dir") or run_payload.get("logging_dir")
                    tool_results = self._task_tool_collect_tool_results(run_dir)
                    summary_parts = self._task_tool_build_summary_parts(tool_results)
                except Exception as exc:
                    msg = f"Failed to execute task subagent: {exc}"
                    return {"error": msg, "__mvi_text_output": msg}

            if callable(parent_emit):
                try:
                    parent_emit(
                        "task_event",
                        {
                            "kind": "completed",
                            "sessionId": session_id,
                            "description": description,
                            "subagent_type": subagent_type,
                            "metadata": {"summary": summary_parts},
                        },
                    )
                except Exception:
                    pass
            return {
                "title": description,
                "metadata": {"sessionId": session_id, "summary": summary_parts},
                "output": output_text,
                "__mvi_text_output": output_text,
            }
        if normalized == "apply_search_replace":
            raw_target = _first_arg("file_name", "file_path", "path", "filePath", "filename", "file")
            target = self._normalize_workspace_path(raw_target)
            search_text = str(args.get("search", ""))
            replace_text = str(args.get("replace", ""))
            try:
                exists = self._ray_get(self.sandbox.exists.remote(target))
            except Exception:
                exists = False
            if not exists or search_text.strip() == "":
                return self._ray_get(self.sandbox.write_text.remote(target, replace_text))
            return self._ray_get(self.sandbox.edit_replace.remote(target, search_text, replace_text, 1))
        if normalized == "apply_unified_patch":
            patch_source_text = str(args.get("patch", ""))
            patch_text = patch_source_text
            if (
                "*** Add File:" in patch_text
                or "*** Update File:" in patch_text
                or "*** Delete File:" in patch_text
                or "*** Begin Patch" in patch_text
            ):
                converted = self._convert_patch_to_unified(patch_text)
                if converted:
                    patch_text = converted
                    try:
                        tool_call["arguments"]["patch"] = patch_text
                    except Exception:
                        pass
            result = self.vcs({
                "action": "apply_patch",
                "params": {
                    "patch": patch_text,
                    "three_way": True,
                    "index": True,
                    "whitespace": "fix",
                    "keep_rejects": True,
                },
            })
            if not result.get("ok"):
                manual_result = self._apply_patch_operations_direct(patch_source_text or patch_text)
                if manual_result:
                    result = manual_result
            if not result.get("ok"):
                rejects = (result.get("data") or {}).get("rejects") or {}
                has_rejects = any(bool(v) for v in rejects.values())
                if has_rejects:
                    retries = self._retry_diff_with_aider(patch_text)
                    if retries is not None:
                        return retries
            return result
        if name == "TodoWrite":
            # Claude Code's TodoWrite is a separate surface from KyleCode's internal
            # todo.* tools. For Claude parity configs we keep todo.* disabled but
            # still need TodoWrite to work and return the exact success message.
            payload = args if isinstance(args, dict) else {}
            todos = payload.get("todos")
            if not isinstance(todos, list):
                msg = "Error: TodoWrite missing required todos"
                return {"error": msg, "__mvi_text_output": msg}
            try:
                # Session-scoped, in-memory todo board (Claude Code does not persist into workspace).
                setattr(self, "_claude_todowrite_state", list(todos))
            except Exception:
                pass
            msg = (
                "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. "
                "Please proceed with the current tasks if applicable"
            )
            return {"ok": True, "__mvi_text_output": msg}
        if name in {"create_file_from_block", "Write"}:
            path = self._normalize_workspace_path(str(args.get("file_name", "")))
            content = str(args.get("content", ""))
            return self._ray_get(self.sandbox.write_text.remote(path, content))
        if name == "mark_task_complete":
            return {"action": "complete"}
        if name.startswith("todo."):
            try:
                return self._execute_todo_tool(name, args)
            except ValueError as exc:
                return {"error": str(exc)}
        if name in {"run_shell", "Bash"}:
            command = args.get("command") or args.get("input")
            timeout = args.get("timeout")
            return self._ray_get(self.sandbox.run_shell.remote(command, timeout=timeout))
        return {"error": f"unknown tool {name}"}

    def _execute_todo_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        manager = getattr(self, "todo_manager", None)
        if manager is None:
            raise ValueError("Todo tools are disabled for this configuration.")
        payload = args if isinstance(args, dict) else {}
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
            todos_payload = payload.get("todos")
            if isinstance(todos_payload, str):
                try:
                    payload["todos"] = json.loads(todos_payload)
                except Exception:
                    pass
            if isinstance(payload.get("todos"), list):
                normalized_list = []
                status_map = {
                    "todo": "pending",
                    "pending": "pending",
                    "in_progress": "in_progress",
                    "blocked": "blocked",
                    "complete": "completed",
                    "completed": "completed",
                    "done": "completed",
                    "canceled": "canceled",
                    "cancelled": "canceled",
                }
                for entry in payload["todos"]:
                    if not isinstance(entry, dict):
                        normalized_list.append(entry)
                        continue
                    normalized = dict(entry)
                    if "content" not in normalized:
                        for alias in ("description", "task", "item"):
                            value = normalized.get(alias)
                            if value:
                                normalized["content"] = value
                                if alias != "content":
                                    normalized.pop(alias, None)
                                break
                    status = normalized.get("status")
                    if isinstance(status, str):
                        normalized["status"] = status_map.get(status.lower(), status)
                    normalized_list.append(normalized)
                payload["todos"] = normalized_list
            result = manager.handle_write_board(payload)
            if isinstance(result, dict):
                result.setdefault(
                    "__mvi_text_output",
                    "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. "
                    "Please proceed with the current tasks if applicable",
                )
            return result
        raise ValueError(f"Unsupported todo tool: {name}")

    @staticmethod
    def _deep_merge_dict(base: Any, override: Any) -> Any:
        """Recursively merge dicts (override wins). Non-dicts replace wholesale."""
        if not isinstance(base, dict) or not isinstance(override, dict):
            return copy.deepcopy(override)
        merged: Dict[str, Any] = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = OpenAIConductor._deep_merge_dict(merged.get(key), value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    @staticmethod
    def _task_tool_default_subagent_description() -> str:
        return "This subagent should only be called manually by the user."

    def _task_tool_allowed_tools(self, sub_cfg: Optional[Dict[str, Any]]) -> List[str]:
        allowed: List[str] = []
        if isinstance(sub_cfg, dict):
            raw = sub_cfg.get("tools")
            if isinstance(raw, list):
                allowed = [str(item) for item in raw if item]
            elif isinstance(raw, dict):
                for key, value in raw.items():
                    if value:
                        allowed.append(str(key))
        if not allowed:
            try:
                tools_cfg = (self.config.get("tools", {}) or {}) if isinstance(getattr(self, "config", None), dict) else {}
                reg = (tools_cfg.get("registry") or {}) if isinstance(tools_cfg, dict) else {}
                include = reg.get("include") or []
                if isinstance(include, list):
                    allowed = [str(item) for item in include if item]
            except Exception:
                allowed = []
        # OpenCode TaskTool always disables these in subagent runs.
        disabled = {"todowrite", "todoread", "task", "mark_task_complete"}
        normalized: List[str] = []
        for name in allowed:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            if cleaned in disabled or cleaned.startswith("todo."):
                continue
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    def _task_tool_run_subagent(
        self,
        *,
        prompt: str,
        model_route: str,
        max_steps: int,
        child_config: Dict[str, Any],
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
        permission_queue: Optional[Any] = None,
    ) -> Dict[str, Any]:
        previous_preserve = os.environ.get("PRESERVE_SEEDED_WORKSPACE")
        os.environ["PRESERVE_SEEDED_WORKSPACE"] = "1"
        try:
            cls = OpenAIConductor.__ray_metadata__.modified_class
            child = cls(
                workspace=str(getattr(self, "workspace", "")),
                image=str(getattr(self, "image", "python-dev:latest")),
                config=child_config,
                local_mode=True,
            )
            return child.run_agentic_loop(
                "",
                prompt,
                model_route,
                max_steps=max_steps,
                output_json_path=None,
                stream_responses=False,
                output_md_path=None,
                tool_prompt_mode="system_once",
                completion_sentinel=">>>>>> END RESPONSE",
                completion_config=child_config.get("completion") if isinstance(child_config, dict) else None,
                event_emitter=event_emitter,
                permission_queue=permission_queue,
            )
        finally:
            if previous_preserve is None:
                os.environ.pop("PRESERVE_SEEDED_WORKSPACE", None)
            else:
                os.environ["PRESERVE_SEEDED_WORKSPACE"] = previous_preserve

    @staticmethod
    def _task_tool_extract_last_assistant_text(messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
        return ""

    @staticmethod
    def _task_tool_collect_tool_results(run_dir: Optional[str]) -> List[Dict[str, Any]]:
        if not run_dir:
            return []
        root = Path(str(run_dir)) / "provider_native" / "tool_results"
        if not root.exists():
            return []
        results: List[Dict[str, Any]] = []
        try:
            paths = sorted(root.glob("turn_*.json"), key=lambda p: p.name)
        except Exception:
            paths = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, list):
                for entry in payload:
                    if isinstance(entry, dict):
                        results.append(entry)
        return results

    @staticmethod
    def _task_tool_build_summary_parts(tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert LoggerV2 tool result payloads into OpenCode-style tool parts."""
        parts: List[Dict[str, Any]] = []
        per_tool_counter: Dict[str, int] = {}
        for entry in tool_results or []:
            if not isinstance(entry, dict):
                continue
            tool_name = str(entry.get("provider_fn") or entry.get("fn") or "").strip()
            if not tool_name:
                continue
            per_tool_counter[tool_name] = int(per_tool_counter.get(tool_name, 0) or 0) + 1
            ordinal = per_tool_counter[tool_name]

            call_id = f"call_{tool_name}_{ordinal}"
            part_id = f"prt_tool_{tool_name}_{ordinal}"

            args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
            out = entry.get("out")

            state: Dict[str, Any] = {
                "status": "completed",
                "input": args,
                "output": "",
                "title": tool_name,
                "metadata": {},
                "time": {"start": 0, "end": 0},
            }

            if isinstance(out, dict):
                title = out.get("title")
                if isinstance(title, str) and title:
                    state["title"] = title
                meta = out.get("metadata")
                if isinstance(meta, dict):
                    state["metadata"] = meta
                err = out.get("error")
                if isinstance(err, str) and err:
                    state["status"] = "error"
                    state["error"] = err
                    state["output"] = ""
                else:
                    output_text: Optional[str] = None
                    if isinstance(out.get("__mvi_text_output"), str):
                        output_text = str(out.get("__mvi_text_output") or "")
                    elif isinstance(out.get("output"), str):
                        output_text = str(out.get("output") or "")
                    if output_text is None:
                        output_text = ""
                    state["output"] = output_text
            elif out is None:
                state["output"] = ""
            else:
                state["output"] = str(out)

            parts.append(
                {
                    "id": part_id,
                    "type": "tool",
                    "tool": tool_name,
                    "meta": {"callID": call_id, "state": state},
                    "input": None,
                    "output": None,
                    "delta": None,
                }
            )
        return parts

    def run_agentic_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_steps: int = 12,
        output_json_path: Optional[str] = None,
        stream_responses: bool = False,
        output_md_path: Optional[str] = None,
        tool_prompt_mode: str = "system_once",  # system_once | per_turn_append | system_and_per_turn | none
        completion_sentinel: Optional[str] = ">>>>>> END RESPONSE",
        completion_config: Optional[Dict[str, Any]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
        event_queue: Optional[Any] = None,
        permission_queue: Optional[Any] = None,
    ) -> Dict[str, Any]:
        # Initialize components
        emitter = event_emitter
        if emitter is None and event_queue is not None:
            def queue_emitter(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
                try:
                    event_queue.put((event_type, payload, turn))
                except Exception:
                    pass
            emitter = queue_emitter
        self.todo_manager = None
        session_state = SessionState(self.workspace, self.image, self.config, event_emitter=emitter)
        if permission_queue is not None:
            session_state.set_provider_metadata("permission_queue", permission_queue)
        self._prompt_hashes = {"system": None, "per_turn": {}}
        self._turn_diagnostics = []
        session_state.set_provider_metadata("initial_user_prompt", user_prompt or "")
        session_state.set_provider_metadata(
            "requires_build_guard",
            self._should_require_build_guard(user_prompt or ""),
        )
        session_state.set_provider_metadata("workspace_context_initialized", False)
        session_state.set_provider_metadata("workspace_context_required", False)
        session_state.set_provider_metadata("workspace_pending_todo_update_allowed", False)
        todos_cfg = self._todo_config()
        if todos_cfg["enabled"]:
            todo_store = TodoStore(self.workspace)
            todo_manager = TodoManager(todo_store, session_state.emit_todo_event)
            session_state.set_todo_manager(todo_manager)
            session_state.set_provider_metadata("todos_config", todos_cfg)
            session_state.set_provider_metadata("todo_snapshot", todo_manager.snapshot())
            self.todo_manager = todo_manager
        completion_detector = CompletionDetector(
            config=completion_config or self.config.get("completion", {}),
        )
        markdown_logger = MarkdownLogger(output_md_path)
        # Also seed conversation.md under logging v2 if enabled
        try:
            if self.logger_v2.run_dir:
                self.logger_v2.write_text("conversation/conversation.md", "# Conversation Transcript\n")
        except Exception:
            pass
        telemetry_path = os.environ.get("RAYCODE_TELEMETRY_PATH")
        if not telemetry_path and getattr(self.logger_v2, "run_dir", None):
            telemetry_path = str(Path(self.logger_v2.run_dir) / "meta" / "telemetry.jsonl")
        telemetry = TelemetryLogger(telemetry_path)
        self._active_telemetry_logger = telemetry
        error_handler = ErrorHandler()

        telemetry_cfg = self.config.get("telemetry", {}) or {}
        db_path = telemetry_cfg.get("database_path") or os.environ.get("KC_TELEMETRY_DB")
        self._reward_metrics_sqlite = None
        self._todo_metrics_sqlite = None
        if db_path:
            try:
                self._reward_metrics_sqlite = RewardMetricsSQLiteWriter(str(db_path))
                self._todo_metrics_sqlite = TodoMetricsSQLiteWriter(str(db_path))
            except Exception:
                self._reward_metrics_sqlite = None
                self._todo_metrics_sqlite = None

        self._ensure_capability_probes(session_state, markdown_logger)
        self.provider_metrics.reset()
        user_prompt, max_steps = self._prepare_replay_session(session_state, user_prompt, max_steps)
        
        # Clear existing log files
        for del_path in [output_md_path, output_json_path]:
            if del_path and os.path.exists(del_path):
                os.remove(del_path)
        
        # Initialize provider and client
        requested_route_id = model
        if self._active_replay_session:
            requested_route_id = "replay"

        runtime = None
        client = None
        provider_tools_cfg = dict((self.config.get("provider_tools") or {}))
        
        # Initialize runtime (ReplayRuntime if replay, else standard)
        provider_config, resolved_model, supports_native_tools_for_model = provider_router.get_provider_config(requested_route_id)
        runtime_descriptor, runtime_model = provider_router.get_runtime_descriptor(requested_route_id)
        client_config = provider_router.create_client_config(requested_route_id)

        # Fix for client_config usage in replay mode
        if runtime_descriptor.provider_id == "replay":
            client_config.setdefault("api_key", "mock")

        api_variant_override = provider_tools_cfg.get("api_variant")
        if api_variant_override and runtime_descriptor.provider_id == "openai":
            variant = str(api_variant_override).lower()
            if variant == "responses":
                runtime_descriptor.runtime_id = "openai_responses"
                runtime_descriptor.default_api_variant = "responses"
            elif variant == "chat":
                runtime_descriptor.runtime_id = "openai_chat"
                runtime_descriptor.default_api_variant = "chat"

        if not client_config["api_key"] and runtime_descriptor.provider_id != "replay":
            raise RuntimeError(f"{provider_config.api_key_env} missing in environment")

        runtime = provider_registry.create_runtime(runtime_descriptor)

        # Create client with provider-specific configuration
        client = runtime.create_client(
            client_config["api_key"],
            base_url=client_config.get("base_url"),
            default_headers=client_config.get("default_headers"),
        )

        self._current_route_id = requested_route_id
        try:
            session_state.set_provider_metadata("route_id", requested_route_id)
        except Exception:
            pass
        model = runtime_model  # Update model to resolved version for API calls
        session_state.set_provider_metadata("provider_id", runtime_descriptor.provider_id)
        session_state.set_provider_metadata("runtime_id", runtime_descriptor.runtime_id)
        session_state.set_provider_metadata("api_variant", runtime_descriptor.default_api_variant)
        session_state.set_provider_metadata("resolved_model", runtime_model)
        try:
            capabilities = provider_router.get_capabilities(model)
            session_state.set_provider_metadata("capabilities", asdict(capabilities))
        except Exception:
            pass
        
        # Defer initial message creation until after tool/dialect resolution so we can compile prompts correctly
        per_turn_prompt = ""
        
        # Setup tool definitions and dialects
        tool_defs_yaml = self._tool_defs_from_yaml()
        tool_defs = tool_defs_yaml or self._get_default_tool_definitions()

        completion_tool_enabled = self._completion_tool_config_enabled()
        if completion_tool_enabled:
            tool_defs = self._ensure_completion_tool(tool_defs)
        mark_tool_present = any(getattr(t, "name", None) == "mark_task_complete" for t in tool_defs)
        session_state.set_provider_metadata("mark_task_complete_available", bool(completion_tool_enabled and mark_tool_present))
        if not completion_tool_enabled or not mark_tool_present:
            session_state.set_provider_metadata("completion_sentinel_required", True)
        
        # Create dialect mapping and filter based on model configuration
        dialect_mapping = self._create_dialect_mapping()
        active_dialect_names = self.dialect_manager.get_dialects_for_model(
            model, list(dialect_mapping.keys())
        )
        
        # Initialize caller for all modes (needed for parsing)
        # Apply v2 selection ordering (by_model/by_tool_kind) if v2 config detected
        try:
            from .compilation.v2_loader import is_v2_config
            if is_v2_config(self.config):
                active_dialect_names = self._apply_v2_dialect_selection(active_dialect_names, model, tool_defs)
        except Exception:
            pass

        filtered_dialects = [dialect_mapping[name] for name in active_dialect_names if name in dialect_mapping]
        caller = CompositeToolCaller(filtered_dialects)

        # Initialize session state with initial messages (now that we have tools and dialects)
        try:
            if int(self.config.get("version", 0)) == 2 and self.config.get("prompts"):
                mode_name = self._resolve_active_mode()
                comp = get_compiler()
                prompts_cfg = self._prompts_config_with_todos()
                v2 = comp.compile_v2_prompts(prompts_cfg, mode_name, tool_defs, active_dialect_names)
                session_state.set_provider_metadata("current_mode", mode_name)
                system_prompt = v2.get("system") or system_prompt
                per_turn_prompt = v2.get("per_turn") or ""
                # Persist compiled system prompt via logging v2
                try:
                    if system_prompt and self.logger_v2.run_dir:
                        self.prompt_logger.save_compiled_system(system_prompt)
                        self._register_prompt_hash("system", system_prompt)
                except Exception:
                    pass
                # Persist TPSL catalogs if available
                try:
                    if self.logger_v2.run_dir and isinstance(v2.get("tpsl"), dict):
                        tpsl_meta = v2["tpsl"]
                        # Choose a stable catalog id from template ids (hash part)
                        def _catalog_id(meta: dict) -> str:
                            tid = str(meta.get("template_id", "fallback"))
                            return tid.split("::")[-1].replace("/", "_")
                        files = []
                        # System catalog
                        if isinstance(tpsl_meta.get("system"), dict):
                            cid = _catalog_id(tpsl_meta["system"]) or "tpsl"
                            path = f"prompts/catalogs/{cid}/system_full.md"
                            self.logger_v2.write_text(path, str(tpsl_meta["system"].get("text", "")))
                            files.append({
                                "path": path,
                                "dialect": tpsl_meta["system"].get("dialect"),
                                "detail": tpsl_meta["system"].get("detail"),
                                "template_id": tpsl_meta["system"].get("template_id"),
                            })
                        # Per-turn catalog
                        if isinstance(tpsl_meta.get("per_turn"), dict):
                            cid = _catalog_id(tpsl_meta["per_turn"]) or "tpsl"
                            path = f"prompts/catalogs/{cid}/per_turn_short.md"
                            self.logger_v2.write_text(path, str(tpsl_meta["per_turn"].get("text", "")))
                            files.append({
                                "path": path,
                                "dialect": tpsl_meta["per_turn"].get("dialect"),
                                "detail": tpsl_meta["per_turn"].get("detail"),
                                "template_id": tpsl_meta["per_turn"].get("template_id"),
                            })
                        if files:
                            # Use first catalog id as manifest name
                            name = (files[0]["template_id"] or "tpsl").split("::")[-1]
                            self.prompt_logger.save_catalog(name, files)
                except Exception:
                    pass
        except Exception:
            per_turn_prompt = per_turn_prompt or ""

        enhanced_system_msg = {"role": "system", "content": system_prompt}
        initial_user_content = user_prompt if not per_turn_prompt else (user_prompt + "\n\n" + per_turn_prompt)
        enhanced_user_msg = {"role": "user", "content": initial_user_content}
        session_state.add_message(enhanced_system_msg)
        session_state.add_message(enhanced_user_msg)
        
        # Configure native tools and tool prompt mode
        native_pref_hint = getattr(self, "_native_preference_hint", None)
        provider_tools_cfg = dict(provider_tools_cfg)
        if provider_tools_cfg.get("use_native") is None and native_pref_hint is not None:
            provider_tools_cfg["use_native"] = native_pref_hint
        provider_tools_cfg = self._apply_capability_tool_overrides(
            provider_tools_cfg,
            session_state,
            markdown_logger,
        )
        effective_config = dict(self.config)
        effective_config["provider_tools"] = provider_tools_cfg
        self._provider_tools_effective = provider_tools_cfg
        use_native_tools = provider_router.should_use_native_tools(model, effective_config)
        will_use_native_tools = self._setup_native_tools(model, use_native_tools)
        tool_prompt_mode = self._adjust_tool_prompt_mode(tool_prompt_mode, will_use_native_tools)
        session_state.last_tool_prompt_mode = tool_prompt_mode
        
        # Setup tool prompts and system messages
        local_tools_prompt = self._setup_tool_prompts(
            tool_prompt_mode, tool_defs, active_dialect_names, 
            session_state, markdown_logger, caller
        )
        
        # Add enhanced descriptive fields to initial messages after tool setup
        self._add_enhanced_message_fields(
            tool_prompt_mode, tool_defs, active_dialect_names, 
            session_state, will_use_native_tools, local_tools_prompt, user_prompt
        )
        
        # Initialize markdown log and snapshot
        markdown_logger.log_system_message(system_prompt)
        try:
            if self.logger_v2.run_dir:
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.system(system_prompt))
        except Exception:
            pass
        if session_state.get_provider_metadata("current_mode") is None:
            session_state.set_provider_metadata("current_mode", self._resolve_active_mode())
        # Use enhanced user content for markdown log
        initial_user_content = session_state.messages[1].get("content", user_prompt)
        markdown_logger.log_user_message(initial_user_content)
        try:
            if self.logger_v2.run_dir:
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.user(initial_user_content))
        except Exception:
            pass
        session_state.write_snapshot(output_json_path, model)
        
        # Main agentic loop - significantly simplified
        run_result = None
        run_loop_error: Optional[Dict[str, Any]] = None
        try:
            run_result = self._run_main_loop(
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
        except Exception as exc:
            run_loop_error = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            session_state.add_transcript_entry({"run_loop_exception": run_loop_error})
            try:
                session_state.set_provider_metadata("run_loop_exception", run_loop_error)
            except Exception:
                pass
            try:
                if self.logger_v2.run_dir:
                    self.logger_v2.write_json("errors/run_loop_exception.json", run_loop_error)
            except Exception:
                pass
        finally:
            self._persist_final_workspace()
            try:
                active_logger = getattr(self, "_active_telemetry_logger", None)
                if active_logger:
                    active_logger.close()
            except Exception:
                pass
            self._active_telemetry_logger = None
        # Defensive: always return a dict result
        if not isinstance(run_result, dict):
            completion_summary = session_state.completion_summary or {}
            completion_summary.setdefault("completed", False)
            if run_loop_error is not None:
                completion_summary["reason"] = "run_loop_exception"
                completion_summary["error"] = run_loop_error
            else:
                completion_summary.setdefault("reason", "no_result")
            session_state.completion_summary = completion_summary
            run_result = {
                "messages": session_state.messages,
                "transcript": session_state.transcript,
                "completion_summary": completion_summary,
                "completion_reason": completion_summary.get("reason", "no_result"),
                "completed": bool(completion_summary.get("completed", False)),
            }

        # Populate finish metadata for IR and persist conversation snapshot
        usage_payload = session_state.get_provider_metadata("usage")
        if not isinstance(usage_payload, dict):
            usage_payload = {}
        finish_reason = "stop" if run_result.get("completed") else "error"
        finish_meta = session_state.get_provider_metadata("raw_finish_meta")
        agent_summary = copy.deepcopy(session_state.completion_summary or {})
        session_state.set_ir_finish(
            IRFinish(
                reason=finish_reason,
                usage=usage_payload,
                provider_meta=finish_meta,
                agent_summary=agent_summary,
            )
        )

        try:
            if self.logger_v2.run_dir:
                conv_id = os.path.basename(self.logger_v2.run_dir)
                conversation_ir = session_state.build_conversation_ir(conversation_id=conv_id)
                self.logger_v2.write_json("meta/conversation_ir.json", asdict(conversation_ir))
        except Exception:
            pass

        return run_result
    
    def _persist_final_workspace(self) -> None:
        """Copy the final workspace into the run's log directory if available."""
        run_dir = getattr(self.logger_v2, "run_dir", None)
        if not run_dir:
            return
        try:
            workspace_path = Path(self.workspace)
        except Exception:
            return
        if not workspace_path.exists() or not workspace_path.is_dir():
            return
        dest = Path(run_dir) / "final_container_dir"
        try:
            workspace_resolved = workspace_path.resolve()
            dest_resolved = dest.resolve(strict=False)
            if str(dest_resolved).startswith(str(workspace_resolved)):
                return
        except Exception:
            pass
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(workspace_path, dest)
        except Exception:
            pass

    def _get_default_tool_definitions(self) -> List[ToolDefinition]:
        return get_default_tool_definitions()
    
    def _create_dialect_mapping(self) -> Dict[str, Any]:
        return create_dialect_mapping()

    def _apply_v2_dialect_selection(self, current: List[str], model_id: str, tool_defs: List[ToolDefinition]) -> List[str]:
        return apply_v2_dialect_selection(self, current, model_id, tool_defs)

    def _apply_selection_legacy(
        self,
        current: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        selection_cfg: Dict[str, Any],
    ) -> List[str]:
        return apply_selection_legacy(current, model_id, tool_defs, selection_cfg)

    def _apply_preference_order(
        self,
        base_order: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
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
        tool_defs: List[ToolDefinition], 
        active_dialect_names: List[str],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        caller
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
        tool_defs: List[ToolDefinition],
        active_dialect_names: List[str],
        session_state: SessionState,
        will_use_native_tools: bool,
        local_tools_prompt: str,
        user_prompt: str
    ):
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
        runtime,
        client,
        model: str,
        max_steps: int,
        output_json_path: str,
        tool_prompt_mode: str,
        tool_defs: List[ToolDefinition],
        active_dialect_names: List[str],
        caller,
        session_state: SessionState,
        completion_detector: CompletionDetector,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool,
        local_tools_prompt: str,
        client_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Main loop delegated to conductor_loop.run_main_loop for readability."""
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

    def _persist_error_artifacts(self, turn_index: int, payload: Dict[str, Any]) -> None:
        """Persist error payload and associated diagnostics artifacts."""

        run_dir = getattr(self.logger_v2, "run_dir", None)
        if not run_dir:
            return

        try:
            self.logger_v2.write_json(f"errors/turn_{turn_index}.json", payload)
            details = payload.get("details")
            if not isinstance(details, dict):
                return
            headers = details.get("response_headers")
            if headers:
                self.logger_v2.write_json(
                    f"raw/responses/turn_{turn_index}.headers.json",
                    headers,
                )
            raw_b64 = details.get("raw_body_b64")
            if raw_b64:
                self.logger_v2.write_text(
                    f"raw/responses/turn_{turn_index}.body.b64",
                    raw_b64,
                )
            raw_excerpt = details.get("raw_excerpt")
            if raw_excerpt:
                self.logger_v2.write_text(
                    f"raw/responses/turn_{turn_index}.raw_excerpt.txt",
                    raw_excerpt,
                )
            html_excerpt = details.get("html_excerpt")
            if html_excerpt:
                self.logger_v2.write_text(
                    f"raw/responses/turn_{turn_index}.html_excerpt.txt",
                    html_excerpt,
                )
        except Exception:
            pass

    def _retry_with_fallback(
        self,
        runtime,
        client,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        runtime_context: ProviderRuntimeContext,
        *,
        stream_responses: bool,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        attempted: List[Tuple[str, bool, Optional[str]]],
        last_error: Optional[ProviderRuntimeError],
    ) -> Optional[ProviderResult]:
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
        )

    def _ensure_capability_probes(self, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
        if self._capability_probes_ran:
            return
        try:
            self.capability_probe_runner.markdown_logger = markdown_logger
            self.capability_probe_runner.run(self.config, session_state)
        except Exception:
            pass
        finally:
            self._capability_probes_ran = True

    def _update_health_metadata(self, session_state: SessionState) -> None:
        try:
            session_state.set_provider_metadata("route_health", self.route_health.snapshot())
        except Exception:
            pass

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
    ) -> Tuple[ProviderResult, bool]:
        """Invoke provider runtime, falling back to non-streaming on failure."""

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
            route_id=getattr(self, "_current_route_id", None),
        )

    def _build_exec_func(self, session_state: SessionState) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        return build_exec_func(self, session_state)

    def _execute_agent_calls(
        self,
        parsed_calls: List[Any],
        exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        session_state: SessionState,
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
    ) -> ProviderResult:
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
    
    def _legacy_message_view(self, provider_message: ProviderMessage) -> SimpleNamespace:
        return legacy_message_view(provider_message)

    def _log_provider_message(
        self,
        provider_message: ProviderMessage,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
    ) -> None:
        log_provider_message(self, provider_message, session_state, markdown_logger, stream_responses)
    
    def _process_model_output(
        self,
        provider_message: ProviderMessage,
        caller,
        tool_defs: List[ToolDefinition],
        session_state: SessionState,
        completion_detector: CompletionDetector,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
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
        msg,
        caller,
        tool_defs: List[ToolDefinition],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool
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
        """Extract raw patch/diff blocks from assistant text."""
        return synthesize_patch_blocks(message_text)
    
    def _handle_native_tool_calls(
        self,
        msg,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool,
        model: str
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
    def _retry_diff_with_aider(self, patch_text: str) -> Optional[Dict[str, Any]]:
        payload = retry_diff_with_aider(patch_text)
        if not payload:
            return None
        result = self._exec_raw(payload)
        result = {"action": "apply_search_replace", **result}
        self._record_diff_metrics(
            SimpleNamespace(function="apply_search_replace", arguments=payload["arguments"], dialect="aider_retry"),
            result,
        )
        return result
