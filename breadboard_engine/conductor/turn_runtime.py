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
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple
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
from ..permissions.policy_pack import PolicyPack
from ..state.session_state import SessionState
from ..turns import TurnContext
from ..utils.assistant_progress import assistant_is_progress_update
from ..checkpointing.checkpoint_manager import CheckpointManager
from ..hooks.model import HookResult
from .components import latest_real_user_prompt, session_requires_workspace_tool_usage

from .completion_guards import (
    _ensure_tool_completion_final_message,
    _force_failed_verification_final_answer,
    _force_failed_write_final_answer,
    _maybe_block_read_only_implementation_loop,
    _maybe_force_post_write_auto_verification_closure,
    _maybe_force_read_only_observation_closure,
    _maybe_force_requested_shell_command_closure,
    _reject_completion_without_implementation_write,
)
from .execution_records import build_tool_model_render_record
from .implementation_receipts import (
    _implementation_task_anchor, _path_is_user_facing_write_target, _requested_write_matches,
    _requested_write_targets, _shell_command_write_targets, _successful_patch_result_paths,
    _tool_call_write_targets, _write_payload_looks_placeholder,
    _implementation_receipt_missing,
)
from .tool_executor import (
    _coordination_task_context,
    _record_validated_signal,
    ToolExecutor,
    resolve_replay_todo_placeholders,
)

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


@dataclass(frozen=True, slots=True)
class TurnPolicy:
    """Immutable completion, tool-usage, and relay decisions for one turn."""

    completion: Dict[str, Any]
    turn_strategy: Dict[str, Any]
    tool_policy: PolicyPack

    @classmethod
    def from_config(cls, config: Dict[str, Any] | None) -> "TurnPolicy":
        source = dict(config or {})
        completion = source.get("completion")
        turn_strategy = source.get("turn_strategy")
        return cls(
            completion=dict(completion) if isinstance(completion, dict) else {},
            turn_strategy=(
                dict(turn_strategy) if isinstance(turn_strategy, dict) else {}
            ),
            tool_policy=PolicyPack.from_config(source),
        )

    def allows_zero_tool_completion(self) -> bool:
        return bool(self.completion.get("allow_zero_tool_completion"))

    def relay_flow(self) -> str:
        return str(self.turn_strategy.get("flow") or "assistant_continuation").lower()

    def relay_strategy(self) -> str:
        return str(self.turn_strategy.get("relay") or "tool_role").lower()

    def is_completion_action(self, tool_name: str, result: Any) -> bool:
        if tool_name == "mark_task_complete":
            return True
        return isinstance(result, dict) and result.get("action") == "complete"

    def completion_method(self, input_kind: str) -> str:
        return "tool_mark_task_complete" if input_kind == "text" else "tool_completion_action"

    def completion_reason(self, input_kind: str, tool_name: str) -> str:
        return "mark_task_complete" if input_kind == "text" else tool_name

    def tool_allowed(self, tool_name: str) -> bool:
        return self.tool_policy.is_tool_allowed(tool_name)


@dataclass(slots=True)
class PreparedProviderExchange:
    """Provider-neutral turn input after transport-specific preparation.

    Provider adapters decide how a response becomes ``parsed_calls`` and how
    its assistant message is represented.  The runtime owns everything after
    that point.  ``dialect_selection`` is carried explicitly so a turn cannot
    accidentally consult mutable conductor state while it is executing.
    """

    provider_message: ProviderMessage
    parsed_calls: List[Any]
    assistant_message: Dict[str, Any]
    provider_assistant_message: Optional[Dict[str, Any]]
    model: str
    dialect_selection: Tuple[str, ...]
    input_kind: Literal["text", "native"]
    transcript_entry: Dict[str, Any]


class AgentRuntime:
    """Own one prepared turn from admission through relay.

    This is deliberately an internal seam.  The conductor remains the
    composition owner, while text parsing and provider-native calls provide
    only typed input preparation and relay formatting.
    """

    def __init__(
        self,
        *,
        conductor: ConductorContext,
        policy: TurnPolicy,
        tool_executor: ToolExecutor,
        event_sink: Callable[[Dict[str, Any]], None],
        log_sink: MarkdownLogger,
    ) -> None:
        self.conductor = conductor
        self.policy = policy
        self.tool_executor = tool_executor
        self.event_sink = event_sink
        self.log_sink = log_sink

    def _result_entries(
        self,
        executed_results: List[Tuple[Any, Any]],
    ) -> List[Dict[str, Any]]:
        return self.tool_executor.shape_results(executed_results)

    def _record_rewards(
        self,
        session_state: SessionState,
        turn_index: Optional[int],
        plan_metadata: Dict[str, Any],
        executed_results: List[Tuple[Any, Any]],
        failed_at_index: int,
        test_success: Optional[float],
        *,
        failed: bool = False,
    ) -> None:
        if not isinstance(turn_index, int):
            return
        try:
            session_state.add_reward_metric(turn_index, "SVS", 0.0 if failed else 1.0)
            if plan_metadata.get("total_calls"):
                executed_calls = plan_metadata.get("executed_calls", 0)
                total_calls = plan_metadata.get("total_calls", 0)
                session_state.add_reward_metric(
                    turn_index,
                    "CPS",
                    0.0 if failed else (1.0 if executed_calls == total_calls else 0.0),
                )
            if executed_results:
                session_state.add_reward_metric(
                    turn_index,
                    "ACS",
                    0.0 if failed else (1.0 if failed_at_index == -1 else 0.0),
                )
            self.conductor._record_lsp_reward_metrics(session_state, turn_index)
            self.conductor._record_test_reward_metric(session_state, turn_index, test_success)
        except Exception:
            pass

    def _relay_execution_error(
        self,
        exchange: PreparedProviderExchange,
        executed_results: List[Tuple[Any, Any]],
        execution_error: Dict[str, Any],
        session_state: SessionState,
        error_handler: Any,
        stream_responses: bool,
    ) -> None:
        try:
            use_responses_api = (
                str(session_state.get_provider_metadata("api_variant") or "").lower()
                == "responses"
            )
        except Exception:
            use_responses_api = False
        if use_responses_api and executed_results:
            for parsed_call, parsed_out in executed_results:
                entry = self.conductor.message_formatter.create_tool_result_entry(
                    getattr(parsed_call, "function", ""),
                    parsed_out,
                    syntax_type="openai",
                    call_id=getattr(parsed_call, "call_id", None),
                )
                session_state.add_message(entry, to_provider=True)
        if execution_error.get("validation_failed"):
            session_state.increment_guardrail_counter("validation_errors")
            error_msg = error_handler.handle_validation_error(execution_error)
        elif execution_error.get("constraint_violation"):
            error_msg = error_handler.handle_constraint_violation(execution_error["error"])
        else:
            error_msg = f"<EXECUTION_ERROR>\n{execution_error['error']}\n</EXECUTION_ERROR>"
        session_state.add_message({"role": "user", "content": error_msg}, to_provider=True)
        try:
            self.log_sink.log_user_message(error_msg)
        except Exception:
            pass
        if stream_responses:
            try:
                print(f"[error] {execution_error.get('error', 'Unknown error')}")
            except Exception:
                pass

    def _handle_completion_action(
        self,
        exchange: PreparedProviderExchange,
        result_entry: Dict[str, Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
        turn_index: Optional[int],
        *,
        executed_results: List[Tuple[Any, Any]],
        results: List[Dict[str, Any]],
        failed_at_index: int,
        on_completion: Optional[Callable[..., None]],
    ) -> Optional[bool]:
        tool_name = str(result_entry.get("fn") or "")
        tool_result = (
            result_entry.get("out")
            if isinstance(result_entry.get("out"), dict)
            else {}
        )
        if not self.policy.is_completion_action(tool_name, tool_result):
            return None
        if _implementation_receipt_missing(self.conductor, session_state):
            abort = _reject_completion_without_implementation_write(
                self.conductor,
                session_state,
                markdown_logger,
                stream_responses,
            )
            return True if abort else False
        task_id, parent_task_id, mission_task_id = _coordination_task_context(session_state)
        guard_ok, guard_reason = self.conductor._completion_guard_check(session_state)
        rejection_reasons: List[str] = []
        if not guard_ok and guard_reason:
            rejection_reasons.append(f"completion_guard_failed:{guard_reason}")
        signal = validate_signal_proposal(
            build_tool_completion_signal_proposal(
                task_id=task_id,
                tool_name=tool_name,
                tool_result=tool_result,
                parent_task_id=parent_task_id,
                mission_task_id=mission_task_id,
            ),
            mission_owner_role=str(
                session_state.get_provider_metadata("completion_owner_role") or "assistant"
            ),
            extra_rejection_reasons=rejection_reasons,
        )
        recorded_signal = _record_validated_signal(
            session_state,
            signal,
            turn=turn_index,
        )
        if not guard_ok and guard_reason:
            abort = self.conductor._emit_completion_guard_feedback(
                session_state,
                markdown_logger,
                guard_reason,
                stream_responses,
            )
            if abort:
                session_state.set_provider_metadata("completion_guard_abort", True)
            result_entry["_completion_guard_blocked"] = True
            return True if abort else False
        if not is_accepted_signal(recorded_signal):
            return False
        final_message = (
            _ensure_tool_completion_final_message(
                self.conductor,
                session_state,
                reason="mark_task_complete_after_receipts",
            )
            if exchange.input_kind == "text"
            else None
        )
        method = self.policy.completion_method(exchange.input_kind)
        summary = {
            "completed": True,
            "method": method,
            "reason": self.policy.completion_reason(exchange.input_kind, tool_name),
            "confidence": 1.0,
            "tool": tool_name,
            "tool_result": tool_result,
            "source": "tool_call",
            "signal": recorded_signal,
        }
        if final_message:
            summary["final_message"] = final_message
        existing = dict(getattr(session_state, "completion_summary", None) or {})
        existing.update({key: value for key, value in summary.items() if key not in existing})
        session_state.completion_summary = existing
        if on_completion is not None:
            try:
                on_completion(
                    exchange=exchange,
                    result_entry=result_entry,
                    results=results,
                    executed_results=executed_results,
                    failed_at_index=failed_at_index,
                    final_message=final_message,
                    turn_index=turn_index,
                )
            except Exception:
                pass
        if stream_responses:
            try:
                print(
                    f"[stop] reason=tool_based confidence=1.0 - "
                    f"{tool_name}() completed"
                )
            except Exception:
                pass
        return True

    def run(
        self,
        exchange: PreparedProviderExchange,
        *,
        session_state: SessionState,
        error_handler: Any,
        stream_responses: bool,
        relay_results: Callable[..., None],
        persist_results: Optional[Callable[..., None]] = None,
        on_completion: Optional[Callable[..., None]] = None,
        record_execution: Optional[Callable[..., None]] = None,
        post_write_closure: bool = False,
    ) -> bool:
        calls = list(exchange.parsed_calls)
        calls = self.conductor._expand_multi_file_patches(
            calls,
            session_state,
            self.log_sink,
        )
        if _maybe_block_read_only_implementation_loop(
            self.conductor,
            session_state,
            self.log_sink,
            calls,
            stream_responses,
        ):
            summary = getattr(session_state, "completion_summary", None) or {}
            return bool(summary.get("completed"))
        if _maybe_force_read_only_observation_closure(session_state, calls):
            return True

        turn_ctx = build_turn_context(self.conductor, session_state, calls)
        calls = apply_turn_guards(self.conductor, turn_ctx, session_state)
        turn_ctx.parsed_calls = list(calls)
        handle_blocked_calls(self.conductor, turn_ctx, session_state, self.log_sink)
        if not calls and exchange.input_kind == "text":
            return False

        session_state.add_message(exchange.assistant_message, to_provider=False)
        self.event_sink(exchange.transcript_entry)
        if exchange.provider_assistant_message is not None:
            session_state.add_message(exchange.provider_assistant_message, to_provider=True)

        turn_index = session_state.get_provider_metadata("current_turn_index")
        turn_index_int = turn_index if isinstance(turn_index, int) else None
        if calls:
            batch = self.tool_executor.execute(
                calls,
                transcript_callback=self.event_sink,
                policy_bypass=session_state.get_provider_metadata("replay_mode"),
            )
            executed_results = batch.executed_results
            failed_at_index = batch.failed_at_index
            execution_error = batch.execution_error
            plan_metadata = batch.plan_metadata
        else:
            executed_results = []
            failed_at_index = -1
            execution_error = None
            plan_metadata = {
                "strategy": "no_calls",
                "can_run_concurrent": False,
                "max_workers": 0,
                "group_counts": {},
                "group_limits": {},
                "total_calls": 0,
                "executed_calls": 0,
            }
        turn_ctx.plan_metadata = plan_metadata
        self.event_sink({"tool_execution_plan": plan_metadata})
        if record_execution is not None:
            record_execution(
                exchange=exchange,
                executed_results=executed_results,
                plan_metadata=plan_metadata,
                turn_index=turn_index_int,
            )
        if persist_results is not None:
            persist_results(
                exchange=exchange,
                executed_results=executed_results,
                plan_metadata=plan_metadata,
                turn_index=turn_index_int,
            )

        try:
            self.conductor.provider_metrics.add_concurrency_sample(
                turn=turn_index_int,
                plan=plan_metadata,
            )
        except Exception:
            pass
        recent_tools_summary, test_success = summarize_execution_results(
            self.conductor,
            turn_ctx,
            executed_results,
            session_state,
            turn_index_int,
        )
        turn_ctx.recent_tools_summary = recent_tools_summary
        turn_ctx.test_success = test_success
        session_state.set_provider_metadata(
            "recent_tool_activity",
            {
                "tools": recent_tools_summary,
                "turn": session_state.get_provider_metadata("current_turn_index"),
            },
        )
        if post_write_closure and _maybe_force_post_write_auto_verification_closure(
            self.conductor,
            session_state,
            reason="post_write_auto_verified_before_continuation",
        ):
            finalize_turn_context_snapshot(
                self.conductor,
                session_state,
                turn_ctx,
                turn_index_int,
            )
            return True
        if _maybe_force_requested_shell_command_closure(
            session_state,
            reason="requested_shell_command_observed_before_continuation",
        ):
            finalize_turn_context_snapshot(
                self.conductor,
                session_state,
                turn_ctx,
                turn_index_int,
            )
            return True
        if _force_failed_verification_final_answer(
            session_state,
            reason="failed_verification_after_retries",
        ):
            finalize_turn_context_snapshot(
                self.conductor,
                session_state,
                turn_ctx,
                turn_index_int,
            )
            return True
        if _force_failed_write_final_answer(
            self.conductor,
            session_state,
            reason="failed_requested_write_after_retries",
        ):
            finalize_turn_context_snapshot(
                self.conductor,
                session_state,
                turn_ctx,
                turn_index_int,
            )
            return True

        if execution_error:
            self._record_rewards(
                session_state,
                turn_index_int,
                plan_metadata,
                executed_results,
                failed_at_index,
                test_success,
                failed=True,
            )
            self._relay_execution_error(
                exchange,
                executed_results,
                execution_error,
                session_state,
                error_handler,
                stream_responses,
            )
            finalize_turn_context_snapshot(
                self.conductor,
                session_state,
                turn_ctx,
                turn_index_int,
            )
            return False

        results = self._result_entries(executed_results)
        completion_result: Optional[bool] = None
        for result_entry in reversed(results):
            completion_result = self._handle_completion_action(
                exchange,
                result_entry,
                session_state,
                self.log_sink,
                stream_responses,
                turn_index_int,
                executed_results=executed_results,
                results=results,
                failed_at_index=failed_at_index,
                on_completion=on_completion,
            )
            if completion_result is not None:
                if completion_result:
                    finalize_turn_context_snapshot(
                        self.conductor,
                        session_state,
                        turn_ctx,
                        turn_index_int,
                    )
                    return True
                if result_entry.get("_completion_guard_blocked"):
                    try:
                        results.remove(result_entry)
                        summary = session_state.tool_usage_summary
                        summary["total_calls"] = max(
                            0,
                            int(summary.get("total_calls", 0)) - 1,
                        )
                        recent_tools_summary = [
                            entry
                            for entry in recent_tools_summary
                            if entry.get("name") != result_entry.get("fn")
                        ]
                        turn_ctx.recent_tools_summary = recent_tools_summary
                    except Exception:
                        pass
                break
        self._record_rewards(
            session_state,
            turn_index_int,
            plan_metadata,
            executed_results,
            failed_at_index,
            test_success,
        )
        if turn_ctx.blocked_calls:
            for blocked in turn_ctx.blocked_calls:
                if blocked.get("source") != "todo":
                    continue
                entry = self.conductor._emit_todo_guard_violation(
                    session_state,
                    self.log_sink,
                    blocked.get("reason") or "Plan guard violation",
                    blocked_call=blocked.get("call"),
                )
                if entry:
                    results.append(entry)
        finalize_turn_context_snapshot(
            self.conductor,
            session_state,
            turn_ctx,
            turn_index_int,
        )
        relay_results(
            exchange=exchange,
            results=results,
            executed_results=executed_results,
            failed_at_index=failed_at_index,
            turn_context=turn_ctx,
        )
        maybe_transition_plan_mode(self.conductor, session_state, self.log_sink)
        return False


__all__ = ['build_turn_context', 'summarize_execution_results', 'emit_turn_snapshot', 'hydrate_turn_context_signals', 'finalize_turn_context_snapshot', 'apply_turn_guards', 'handle_blocked_calls', 'maybe_transition_plan_mode']
