from __future__ import annotations

import json
import os
import random
import re
import shlex
import signal
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.core import ToolDefinition
from .context import ConductorContext
from ..messaging.markdown_logger import MarkdownLogger
from ..provider import provider_adapter_manager
from ..provider.routing import provider_router
from ..provider.contracts import (
    ProviderMessage,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderResult,
)
from ..provider.registry import provider_registry
from ..replay import resolve_todo_placeholders
from ..orchestration.coordination import (
    build_completion_signal_proposal,
    build_tool_completion_signal_proposal,
    is_accepted_signal,
    validate_signal_proposal,
)
from ..state.session_state import SessionState
from ..turns import TurnContext
from ..utils.assistant_progress import assistant_is_progress_update
from ..checkpointing.checkpoint_manager import CheckpointManager
from ..hooks.model import HookResult
from .components import latest_real_user_prompt, session_requires_workspace_tool_usage

from .execution_records import build_tool_model_render_record
from .implementation_receipts import (
    _implementation_task_anchor, _path_is_user_facing_write_target, _requested_write_matches,
    _requested_write_targets, _shell_command_write_targets, _successful_patch_result_paths,
    _tool_call_write_targets, _write_payload_looks_placeholder,
)
from .tool_executor import resolve_replay_todo_placeholders
def build_turn_context(conductor: ConductorContext, session_state: SessionState, parsed_calls: List[Any]) -> TurnContext:
    current_mode = session_state.get_provider_metadata("current_mode")
    ctx = TurnContext.from_session(session_state, current_mode, list(parsed_calls))
    if ctx.replay_mode:
        for call in ctx.parsed_calls:
            resolve_replay_todo_placeholders(conductor, session_state, call)
    return ctx

def summarize_execution_results(
    conductor: ConductorContext,
    turn_ctx: TurnContext,
    executed_results: List[Any],
    session_state: SessionState,
    turn_index_int: Optional[int],
) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    recent_tools_summary: List[Dict[str, Any]] = []
    test_success: Optional[float] = None
    for tool_parsed, tool_result in executed_results:
        original_tool_name = getattr(tool_parsed, "function", None)
        tool_name = (original_tool_name or "") or ""
        tool_name_lower = tool_name.lower()
        if tool_name_lower == "bash":
            tool_name = "run_shell"
        elif tool_name_lower == "shell_command":
            tool_name = "run_shell"
        elif tool_name_lower == "list":
            tool_name = "list_dir"
        elif tool_name_lower == "read":
            tool_name = "read_file"
        elif tool_name_lower == "write":
            tool_name = "create_file_from_block"
        elif tool_name_lower == "apply_patch":
            tool_name = "apply_unified_patch"
        elif tool_name_lower == "todo":
            tool_name = "TodoWrite"
        tool_result_dict = tool_result if isinstance(tool_result, dict) else {}
        if (
            isinstance(tool_result_dict.get("out"), dict)
            and not any(key in tool_result_dict for key in ("ok", "error", "exit", "stdout", "stderr", "data", "action"))
        ):
            tool_result_dict = tool_result_dict["out"]
        conductor._record_diff_metrics(
            tool_parsed,
            tool_result_dict,
            session_state=session_state,
            turn_index=turn_index_int,
        )
        metadata: Dict[str, Any] = {}
        command: str = ""
        guardrail_code = tool_result_dict.get("guardrail")
        if guardrail_code:
            try:
                session_state.increment_guardrail_counter(str(guardrail_code))
            except Exception:
                pass
        if tool_name == "run_shell":
            try:
                command = str((getattr(tool_parsed, "arguments", {}) or {}).get("command", ""))
            except Exception:
                command = ""
            if command and conductor._is_test_command(command):
                exit_code = tool_result_dict.get("exit")
                if isinstance(exit_code, int):
                    success = 1.0 if exit_code == 0 else 0.0
                    test_success = success if test_success is None else min(test_success, success)
        if guardrail_code:
            event_payload = {
                "guardrail": str(guardrail_code),
                "tool": tool_name,
            }
            if tool_name == "run_shell" and command:
                event_payload["command"] = command
            session_state.record_guardrail_event(str(guardrail_code), event_payload)
        success_flag = not conductor.agent_executor.is_tool_failure(tool_name, tool_result_dict)
        if tool_name in ("apply_unified_patch", "patch") and not success_flag:
            diag_payload = {
                "stderr": (tool_result_dict.get("stderr") or "").strip(),
                "stdout": (tool_result_dict.get("stdout") or "").strip(),
                "exit": tool_result_dict.get("exit"),
                "rejects": sorted((tool_result_dict.get("data") or {}).get("rejects", {}).keys()),
                "patch_preview": str(
                    (getattr(tool_parsed, "arguments", {}) or {}).get("patch")
                    or (getattr(tool_parsed, "arguments", {}) or {}).get("patchText")
                    or ""
                )[:400],
            }
            try:
                session_state.add_transcript_entry({
                    "patch_failure": {
                        "diagnostic": diag_payload,
                        "call_id": getattr(tool_parsed, "call_id", None),
                    }
                })
            except Exception:
                pass
            try:
                session_state.record_guardrail_event("patch_failure", diag_payload)
            except Exception:
                pass
        if tool_name in ("apply_unified_patch", "patch", "apply_search_replace", "create_file_from_block", "write", "write_file"):
            metadata["is_write"] = True
            try:
                tool_args = getattr(tool_parsed, "arguments", {}) or {}
                write_targets = _tool_call_write_targets(tool_name, tool_args)
                result_write_targets = _successful_patch_result_paths(tool_result_dict)
                if result_write_targets:
                    write_targets = list(dict.fromkeys([*write_targets, *result_write_targets]))
                requested_targets = _requested_write_targets(session_state)
                placeholder_write = _write_payload_looks_placeholder(tool_args)
                metadata["write_targets"] = write_targets
                metadata["requested_write_targets"] = requested_targets
                metadata["requested_write_matches"] = (
                    [] if placeholder_write else _requested_write_matches(write_targets, requested_targets)
                )
                metadata["is_placeholder_write"] = placeholder_write
                metadata["is_user_facing_write"] = any(
                    _path_is_user_facing_write_target(target) for target in write_targets
                )
                metadata["is_requested_file_write"] = bool(metadata["requested_write_matches"])
                if placeholder_write:
                    try:
                        blocked_count = int(session_state.get_provider_metadata("implementation_placeholder_write_blocks") or 0) + 1
                        session_state.set_provider_metadata("implementation_placeholder_write_blocks", blocked_count)
                        session_state.record_guardrail_event(
                            "placeholder_requested_write",
                            {
                                "tool": tool_name,
                                "write_targets": write_targets,
                                "requested_write_targets": requested_targets,
                                "count": blocked_count,
                            },
                        )
                        if blocked_count <= 3:
                            session_state.add_message(
                                {
                                    "role": "user",
                                    "content": (
                                        "<VALIDATION_ERROR>\n"
                                        "The last write looks like a placeholder/stub and does not satisfy the requested implementation. "
                                        "Replace it with real working code and real verification artifacts."
                                        f"{_implementation_task_anchor(session_state)}\n"
                                        "</VALIDATION_ERROR>"
                                    ),
                                },
                                to_provider=True,
                            )
                    except Exception:
                        pass
            except Exception:
                metadata["is_user_facing_write"] = False
                metadata["is_requested_file_write"] = False
        call_id_value = getattr(tool_parsed, "call_id", None)
        if isinstance(call_id_value, str) and call_id_value.strip():
            metadata.setdefault("call_id", call_id_value.strip())
        if tool_name in {"task", "background_task", "call_omo_agent"} and isinstance(tool_result_dict, dict):
            result_metadata = tool_result_dict.get("metadata") if isinstance(tool_result_dict.get("metadata"), dict) else {}
            for key in ("agentId", "agent_id", "task_id", "taskId"):
                task_id_value = str((result_metadata or {}).get(key) or tool_result_dict.get(key) or "").strip()
                if task_id_value:
                    metadata.setdefault("async_task_id", task_id_value)
                    break
        if tool_name == "run_shell":
            metadata["is_run_shell"] = True
            metadata["command"] = command
            exit_code_val = tool_result_dict.get("exit")
            if isinstance(exit_code_val, int):
                metadata["exit_code"] = exit_code_val
            if command and conductor._is_test_command(command):
                metadata["is_test_command"] = True
            shell_write_targets = _shell_command_write_targets(command)
            if shell_write_targets:
                requested_targets = _requested_write_targets(session_state)
                requested_matches = _requested_write_matches(shell_write_targets, requested_targets)
                metadata["is_write"] = True
                metadata["write_targets"] = shell_write_targets
                metadata["requested_write_targets"] = requested_targets
                metadata["requested_write_matches"] = requested_matches
                metadata["is_user_facing_write"] = True
                metadata["is_requested_file_write"] = bool(requested_matches)
        if isinstance(turn_index_int, int):
            try:
                session_state.record_tool_event(
                    turn_index_int,
                    tool_name or "",
                    success=success_flag,
                    metadata=metadata,
                    result=tool_result_dict,
                )
            except Exception:
                pass
        model_render = build_tool_model_render_record(tool_name, tool_result_dict)
        recent_tool_entry = {
            "name": tool_name,
            "read_only": conductor._is_read_only_tool(tool_name),
            "completion_action": isinstance(tool_result, dict) and tool_result.get("action") == "complete",
            "model_render": model_render,
        }
        if metadata:
            recent_tool_entry["meta"] = dict(metadata)
        recent_tools_summary.append(recent_tool_entry)
    if turn_index_int is not None:
        conductor._record_lsp_reward_metrics(session_state, turn_index_int)
        conductor._record_test_reward_metric(session_state, turn_index_int, test_success)
    return recent_tools_summary, test_success

def emit_turn_snapshot(conductor: ConductorContext, session_state: SessionState, turn_ctx: TurnContext) -> None:
    try:
        snapshot = turn_ctx.snapshot()
        session_state.set_provider_metadata("turn_context_snapshot", snapshot)
        session_state.add_transcript_entry({"turn_context": snapshot})
    except Exception:
        pass

def hydrate_turn_context_signals(session_state: SessionState, turn_ctx: TurnContext) -> None:
    try:
        loop_payload = session_state.get_provider_metadata("loop_detection_payload")
        if loop_payload:
            turn_ctx.loop_payload = loop_payload
            session_state.set_provider_metadata("loop_detection_payload", None)
    except Exception:
        pass
    try:
        context_payload = session_state.get_provider_metadata("context_window_warning")
        if context_payload:
            turn_ctx.context_warning = context_payload
            session_state.set_provider_metadata("context_window_warning", None)
    except Exception:
        pass

def finalize_turn_context_snapshot(conductor: ConductorContext, session_state: SessionState, turn_ctx: TurnContext, turn_index: Optional[int]) -> None:
    hydrate_turn_context_signals(session_state, turn_ctx)
    rewards: Dict[str, float] = {}
    if isinstance(turn_index, int):
        recorder = getattr(session_state, "reward_metrics", None)
        if recorder and hasattr(recorder, "get_record"):
            try:
                record = recorder.get_record(turn_index)
            except Exception:
                record = None
            if record and getattr(record, "metrics", None):
                rewards = {
                    str(name): value
                    for name, value in record.metrics.items()
                    if value is not None
                }
    turn_ctx.reward_metrics = rewards
    emit_turn_snapshot(conductor, session_state, turn_ctx)

def apply_turn_guards(conductor: ConductorContext, turn_ctx: TurnContext, session_state: SessionState) -> List[Any]:
    return conductor.guardrail_orchestrator.apply_turn_guards(
        turn_ctx,
        session_state,
        workspace_guard_handler=getattr(conductor, "workspace_guard_handler", None),
        todo_rate_guard_handler=getattr(conductor, "todo_rate_guard_handler", None),
    )

def handle_blocked_calls(conductor: ConductorContext, turn_ctx: TurnContext, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
    conductor.guardrail_orchestrator.handle_blocked_calls(turn_ctx, session_state, markdown_logger)


def maybe_transition_plan_mode(conductor: ConductorContext, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
    conductor.guardrail_orchestrator.maybe_transition_plan_mode(
        session_state,
        markdown_logger,
        workspace_guard_handler=getattr(conductor, "workspace_guard_handler", None),
        todo_rate_guard_handler=getattr(conductor, "todo_rate_guard_handler", None),
    )


__all__ = ['build_turn_context', 'summarize_execution_results', 'emit_turn_snapshot', 'hydrate_turn_context_signals', 'finalize_turn_context_snapshot', 'apply_turn_guards', 'handle_blocked_calls', 'maybe_transition_plan_mode']
