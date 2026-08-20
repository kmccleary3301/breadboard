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
from ..provider.runtime import (
    ProviderMessage,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderResult,
    provider_registry,
)
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

from .execution_records import (build_tool_execution_outcome_record, build_tool_model_render_record, classify_tool_terminal_state, legacy_message_view)
from .implementation_receipts import (
    _async_result_retrieval_tool_for_activity, _async_result_task_id_from_activity,
    _command_tunnels_apply_patch, _latest_implementation_prompt, _latest_prompt_forbidden_direct_commands,
    _latest_prompt_requests_file_deletion, _path_is_user_facing_write_target, _requested_write_matches,
    _requested_write_targets, _required_final_answer_marker, _required_final_answer_reminder,
    _shell_command_delete_targets, _shell_command_write_targets, _tool_call_delete_targets,
    _tool_call_write_targets,
)
def _inject_async_result_retrieval(
    conductor: ConductorContext,
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    prior_tool_activity: Any,
    *,
    reason: str,
    stream_responses: bool,
) -> bool:
    task_id = _async_result_task_id_from_activity(prior_tool_activity)
    if not task_id:
        return False
    retrieval_tool = _async_result_retrieval_tool_for_activity(prior_tool_activity)
    args = {"task_id": task_id, "block": True, "timeout": 30000}
    try:
        exec_func = build_exec_func(conductor, session_state)
        result = exec_func({"function": retrieval_tool, "arguments": args})
    except Exception as exc:
        result = {"error": str(exc), "__mvi_text_output": str(exc)}
    result_dict = result if isinstance(result, dict) else {"output": str(result), "__mvi_text_output": str(result)}
    success = not conductor.agent_executor.is_tool_failure(retrieval_tool, result_dict)
    turn_index = session_state.get_provider_metadata("current_turn_index")
    turn_index_int = turn_index if isinstance(turn_index, int) else None
    metadata = {
        "async_task_id": task_id,
        "source": "auto_async_result_retrieval",
        "reason": reason,
    }
    try:
        session_state.record_tool_event(
            turn_index_int,
            retrieval_tool,
            success=success,
            metadata=metadata,
            result=result_dict,
        )
    except Exception:
        pass
    try:
        session_state.add_transcript_entry({
            "auto_async_result_retrieval": {
                "task_id": task_id,
                "retrieval_tool": retrieval_tool,
                "success": success,
                "reason": reason,
                "result": result_dict,
            }
        })
    except Exception:
        pass
    output_text = str(
        result_dict.get("__mvi_text_output")
        or result_dict.get("output")
        or result_dict.get("error")
        or result_dict
    )
    marker = _required_final_answer_marker(session_state)
    marker_instruction = f" Start with the exact marker `{marker}`." if marker else ""
    followup = (
        "<ASYNC_TASK_RESULT>\n"
        f"tool: {retrieval_tool}\n"
        f"task_id: {task_id}\n"
        f"status: {'ok' if success else 'error'}\n\n"
        f"{output_text.rstrip()}\n"
        "</ASYNC_TASK_RESULT>\n\n"
        "Use this retrieved async task result to give the final answer now. "
        f"{marker_instruction} Do not call more tools unless the retrieved result is an explicit error."
    )
    session_state.add_message({"role": "user", "content": followup}, to_provider=True)
    try:
        markdown_logger.log_user_message(followup)
    except Exception:
        pass
    session_state.set_provider_metadata(
        "recent_tool_activity",
        {
            "tools": [
                {
                    "name": retrieval_tool,
                    "read_only": True,
                    "completion_action": False,
                    "meta": metadata,
                }
            ],
            "turn": turn_index,
        },
    )
    if stream_responses:
        try:
            print("[guard] injected async task result retrieval")
        except Exception:
            pass
    return False

def _coordination_task_context(session_state: SessionState) -> tuple[str, Optional[str], Optional[str]]:
    task_id = (
        session_state.get_provider_metadata("coordination_task_id")
        or session_state.get_provider_metadata("task_id")
        or "main"
    )
    parent_task_id = (
        session_state.get_provider_metadata("coordination_parent_task_id")
        or session_state.get_provider_metadata("parent_task_id")
    )
    mission_task_id = (
        session_state.get_provider_metadata("coordination_mission_task_id")
        or session_state.get_provider_metadata("mission_task_id")
    )
    return str(task_id), str(parent_task_id) if parent_task_id else None, str(mission_task_id) if mission_task_id else None

def _record_validated_signal(
    session_state: SessionState,
    signal: Dict[str, Any],
    *,
    turn: Optional[int],
) -> Dict[str, Any]:
    recorded = session_state.record_coordination_signal(signal, turn=turn)
    session_state.add_transcript_entry({"coordination_signal": recorded})
    return recorded

def _is_completion_action_result(tool_name: str, tool_result: Dict[str, Any]) -> bool:
    if tool_name == "mark_task_complete":
        return True
    return isinstance(tool_result, dict) and tool_result.get("action") == "complete"

def build_exec_func(conductor: ConductorContext, session_state: SessionState) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Create execution function with replay-aware TODO resolution."""
    hook_manager = getattr(conductor, "hook_manager", None)

    def _hook_executor(hook: Any, payload: Dict[str, Any], *, session_state: Optional[Any] = None, turn: Optional[int] = None) -> HookResult:
        try:
            return conductor._exec_hook_tool(hook, payload, session_state=session_state, turn=turn)
        except Exception as exc:
            return HookResult(action="deny", reason=f"hook_tool_error: {exc}")

    def _emit_tool_lifecycle(call: Dict[str, Any], phase: str, result: Optional[Dict[str, Any]] = None) -> None:
        try:
            tool_name = call.get("function") or call.get("name") or call.get("tool")
            turn_index = session_state.get_provider_metadata("current_turn_index")
            payload: Dict[str, Any] = {"tool": tool_name}
            error = None
            if result is not None:
                error = result.get("error") if isinstance(result, dict) else None
                payload["success"] = bool(not error)
                if error:
                    payload["error"] = str(error)
            primitive = getattr(session_state, "emit_tool_call_primitive", None)
            if callable(primitive):
                primitive(call, "declared")
                if phase == "started":
                    primitive(call, "executing")
                elif phase == "finished":
                    primitive(call, "failed" if error else "completed", result=result if isinstance(result, dict) else None)
                    outcome = getattr(session_state, "emit_tool_outcome_primitives", None)
                    if callable(outcome) and isinstance(result, dict):
                        outcome(call, result)
            session_state.record_lifecycle_event(
                f"tool_call_{phase}",
                payload,
                turn=turn_index if isinstance(turn_index, int) else None,
            )
        except Exception:
            pass

    def _apply_pre_tool_hooks(call: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Any]]:
        if hook_manager is None:
            return call, None
        turn_index = session_state.get_provider_metadata("current_turn_index")
        hook_result = hook_manager.run(
            "pre_tool",
            {"tool_call": dict(call)},
            session_state=session_state,
            turn=turn_index if isinstance(turn_index, int) else None,
            hook_executor=_hook_executor,
        )
        if hook_result.action == "transform":
            payload = hook_result.payload if isinstance(hook_result.payload, dict) else {}
            override = payload.get("tool_call")
            if isinstance(override, dict):
                return override, hook_result
        return call, hook_result

    def _apply_post_tool_hooks(call: Dict[str, Any], result: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Any]]:
        if hook_manager is None:
            return result, None
        turn_index = session_state.get_provider_metadata("current_turn_index")
        hook_result = hook_manager.run(
            "post_tool",
            {"tool_call": dict(call), "tool_result": dict(result)},
            session_state=session_state,
            turn=turn_index if isinstance(turn_index, int) else None,
            hook_executor=_hook_executor,
        )
        if hook_result.action == "deny":
            error_text = hook_result.reason or "blocked_by_hook"
            return {"error": error_text}, hook_result
        if hook_result.action == "transform":
            payload = hook_result.payload if isinstance(hook_result.payload, dict) else {}
            override = payload.get("tool_result")
            if isinstance(override, dict):
                return override, hook_result
        return result, hook_result

    if not session_state.get_provider_metadata("replay_mode"):
        def _exec_logged(call: Dict[str, Any]) -> Dict[str, Any]:
            call_to_use, pre_hook = _apply_pre_tool_hooks(call)
            if pre_hook is not None and getattr(pre_hook, "action", "") == "deny":
                error_text = pre_hook.reason or "blocked_by_hook"
                result = {"error": error_text}
                _emit_tool_lifecycle(call_to_use, "started")
                _emit_tool_lifecycle(call_to_use, "finished", result=result)
                return result
            _emit_tool_lifecycle(call_to_use, "started")
            result: Dict[str, Any] = {}
            try:
                result = conductor._exec_raw(call_to_use)
                result, _ = _apply_post_tool_hooks(call_to_use, result)
                return result
            finally:
                if isinstance(result, dict):
                    _emit_tool_lifecycle(call_to_use, "finished", result=result)
                else:
                    _emit_tool_lifecycle(call_to_use, "finished", result={"error": "non-dict result"})
        return _exec_logged

    try:
        workspace_path = Path(conductor.workspace)
    except Exception:
        workspace_path = Path(str(conductor.workspace))

    def _exec_with_replay(call: Dict[str, Any]) -> Dict[str, Any]:
        call_to_use, pre_hook = _apply_pre_tool_hooks(call)
        if pre_hook is not None and getattr(pre_hook, "action", "") == "deny":
            error_text = pre_hook.reason or "blocked_by_hook"
            result = {"error": error_text}
            _emit_tool_lifecycle(call_to_use, "started")
            _emit_tool_lifecycle(call_to_use, "finished", result=result)
            return result
        _emit_tool_lifecycle(call_to_use, "started")
        args = call_to_use.get("arguments")
        if isinstance(args, dict):
            try:
                resolved = resolve_todo_placeholders(dict(args), workspace_path)
                call_to_use = dict(call_to_use)
                call_to_use["arguments"] = resolved
            except Exception:
                pass
        result: Dict[str, Any] = {}
        try:
            result = conductor._exec_raw(call_to_use)
            result, _ = _apply_post_tool_hooks(call_to_use, result)
            return result
        finally:
            if isinstance(result, dict):
                _emit_tool_lifecycle(call_to_use, "finished", result=result)
            else:
                _emit_tool_lifecycle(call_to_use, "finished", result={"error": "non-dict result"})

    return _exec_with_replay

def resolve_replay_todo_placeholders(conductor: ConductorContext, session_state: SessionState, parsed_call: Any) -> None:
    if not session_state.get_provider_metadata("replay_mode"):
        return
    args = getattr(parsed_call, "arguments", None)
    if not isinstance(args, dict):
        return
    todo_manager = session_state.get_todo_manager()
    if not todo_manager:
        return
    snapshot = todo_manager.snapshot()
    if not isinstance(snapshot, dict):
        return
    resolved = resolve_todo_placeholders(args, todo_snapshot=snapshot)
    if resolved != args:
        parsed_call.arguments = resolved

def _emit_tool_denial_primitives(session_state: SessionState, parsed_calls: List[Any], reason: str) -> None:
    for call in parsed_calls:
        try:
            primitive = getattr(session_state, "emit_tool_call_primitive", None)
            if callable(primitive):
                primitive(call, "declared")
                primitive(call, "denied", result={"error": reason, "denied": True})
            outcome = getattr(session_state, "emit_tool_outcome_primitives", None)
            if callable(outcome):
                outcome(call, {"error": reason, "denied": True})
        except Exception:
            pass

def execute_agent_calls(
    conductor: ConductorContext,
    parsed_calls: List[Any],
    exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
    session_state: SessionState,
    *,
    transcript_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    policy_bypass: bool = False,
) -> Tuple[List[Any], int, Optional[Dict[str, Any]], Dict[str, Any]]:
    """Execute parsed agent calls with permission enforcement."""
    allow_multi_bash = bool(session_state.get_provider_metadata("replay_mode"))
    previous_value = getattr(conductor.agent_executor, "allow_multiple_bash", False)
    conductor.agent_executor.allow_multiple_bash = allow_multi_bash
    def _blocked_call_result(call: Any, payload: Dict[str, Any]) -> Tuple[List[Any], int, Dict[str, Any], Dict[str, Any]]:
        result = dict(payload)
        result.setdefault("validation_failed", True)
        result.setdefault("exit", 1)
        return [(call, result)], 0, result, {"total_calls": len(parsed_calls), "executed_calls": 0}

    def _smoke_test_requested() -> bool:
        if "smoke_test.sh" in _requested_write_targets(session_state):
            return True
        try:
            prompt = _latest_implementation_prompt(session_state).lower()
        except Exception:
            prompt = ""
        if "smoke_test.sh" in prompt:
            return True
        try:
            for message in getattr(session_state, "provider_messages", []) or []:
                if str((message or {}).get("role") or "") != "user":
                    continue
                content = str((message or {}).get("content") or "").lower()
                if "smoke_test.sh" in content:
                    return True
        except Exception:
            pass
        try:
            for message in getattr(session_state, "transcript", []) or []:
                if str((message or {}).get("role") or "") != "user":
                    continue
                content = str((message or {}).get("content") or "").lower()
                if "smoke_test.sh" in content:
                    return True
        except Exception:
            pass
        return False

    def _command_runs_direct_smtp(command_text: str) -> bool:
        return bool(re.search(r"(^|[;&|]\s*|\|\s*)\./smtp_server(?:\s|$)", command_text))

    def _command_runs_unbounded_smoke(command_text: str) -> bool:
        return bool(
            re.search(
                r"(^|[;&|]\s*)(?:(?:bash|sh)\s+)?\.?/??smoke_test\.sh\b",
                command_text,
            )
            and not re.search(r"(^|[;&|]\s*)timeout\s+\d+", command_text)
        )

    try:
        try:
            conductor.permission_broker.ensure_allowed(session_state, parsed_calls)
        except Exception as exc:
            if exc.__class__.__name__ == "PermissionDeniedError":
                _emit_tool_denial_primitives(session_state, parsed_calls, str(exc))
                return [], 0, {"error": str(exc), "validation_failed": True, "permission_denied": True}, {}
            raise

        write_tools = {
            "write",
            "write_file",
            "write_files",
            "create_file",
            "create_file_from_block",
            "apply_unified_patch",
            "apply_unified_diff",
            "apply_patch",
            "apply_search_replace",
            "patch",
        }
        for call in parsed_calls:
            tool_name = str(getattr(call, "function", "") or "").strip()
            if not tool_name:
                continue
            try:
                tool_name = conductor.agent_executor.canonical_tool_name(tool_name) or tool_name
            except Exception:
                pass
            if tool_name.lower() not in {"run_shell", "shell_command", "bash"}:
                continue
            tool_args = getattr(call, "arguments", {}) or {}
            command_text = str(tool_args.get("command") or tool_args.get("input") or "")
            if _command_tunnels_apply_patch(command_text):
                msg = (
                    "shell command rejected: use the native apply_patch tool for patches; "
                    "do not invoke apply_patch through shell_command"
                )
                return _blocked_call_result(call, {"error": msg, "shell_tunneled_apply_patch": True})
            if _command_runs_direct_smtp(command_text) and _smoke_test_requested():
                msg = "shell command rejected: run ./smtp_server only through the requested smoke_test.sh script"
                return _blocked_call_result(call, {"error": msg, "forbidden_direct_command": True})
            if _smoke_test_requested() and _command_runs_unbounded_smoke(command_text):
                msg = (
                    "shell command rejected: run smoke_test.sh through a bounded timeout, e.g. "
                    "`timeout 20s bash smoke_test.sh`"
                )
                return _blocked_call_result(call, {"error": msg, "unbounded_smoke_test": True})
            forbidden_direct_commands = _latest_prompt_forbidden_direct_commands(session_state)
            for forbidden in forbidden_direct_commands:
                if re.search(rf"(^|[;&|]\s*|\|\s*){re.escape(forbidden)}(?:\s|$)", command_text):
                    msg = (
                        "shell command rejected: the current request explicitly says not to run "
                        f"{forbidden} directly"
                    )
                    return _blocked_call_result(call, {"error": msg, "forbidden_direct_command": True})

        requested_write_targets = _requested_write_targets(session_state)
        if requested_write_targets:
            for call in parsed_calls:
                tool_name = str(getattr(call, "function", "") or "").strip()
                if not tool_name:
                    continue
                try:
                    tool_name = conductor.agent_executor.canonical_tool_name(tool_name) or tool_name
                except Exception:
                    pass
                tool_args = getattr(call, "arguments", {}) or {}
                write_targets: List[str] = []
                if tool_name.lower() in write_tools:
                    write_targets = _tool_call_write_targets(tool_name, tool_args)
                    delete_targets = _tool_call_delete_targets(tool_name, tool_args)
                elif tool_name.lower() in {"run_shell", "shell_command", "bash"}:
                    command_text = str(tool_args.get("command") or tool_args.get("input") or "")
                    write_targets = _shell_command_write_targets(command_text)
                    delete_targets = _shell_command_delete_targets(command_text)
                else:
                    delete_targets = []
                requested_delete_matches = _requested_write_matches(delete_targets, requested_write_targets)
                if requested_delete_matches and not _latest_prompt_requests_file_deletion(session_state):
                    msg = (
                        "write rejected: refusing to delete requested implementation target(s) "
                        f"{', '.join(requested_delete_matches)} because the current request did not ask for deletion"
                    )
                    return _blocked_call_result(call, {"error": msg, "destructive_requested_target_delete": True})
                user_facing_targets = [
                    target for target in write_targets
                    if _path_is_user_facing_write_target(target)
                ]
                if not user_facing_targets:
                    continue
                unmatched = [
                    target for target in user_facing_targets
                    if not _requested_write_matches([target], requested_write_targets)
                ]
                if unmatched:
                    msg = (
                        "write rejected: this implementation request names specific write target(s) "
                        f"{', '.join(requested_write_targets)}; attempted non-requested target(s): "
                        f"{', '.join(unmatched)}"
                    )
                    return _blocked_call_result(call, {"error": msg, "unrequested_write_target": True})

        def _checkpoint_snapshot() -> Optional[Dict[str, Any]]:
            try:
                model = session_state.get_provider_metadata("resolved_model") or session_state.get_provider_metadata("model")
            except Exception:
                model = None
            if not model:
                try:
                    model = (session_state.config.get("providers", {}) or {}).get("default_model")
                except Exception:
                    model = None
            try:
                return session_state.create_snapshot(str(model or "unknown"))
            except Exception:
                return None
        checkpoint_manager: Optional[CheckpointManager] = None
        before_tool: Optional[str] = None
        for call in parsed_calls:
            tool_name = str(getattr(call, "function", "") or "").strip()
            if not tool_name:
                continue
            try:
                tool_name = conductor.agent_executor.canonical_tool_name(tool_name) or tool_name
            except Exception:
                pass
            if tool_name.lower() in write_tools:
                before_tool = tool_name
                break
        if before_tool:
            try:
                checkpoint_manager = CheckpointManager(Path(conductor.workspace))
                checkpoint_manager.create_checkpoint(f"Before {before_tool}", snapshot=_checkpoint_snapshot())
            except Exception:
                checkpoint_manager = None

        executed_results, failed_at_index, validation_error, meta = conductor.agent_executor.execute_parsed_calls(
            parsed_calls,
            exec_func,
            transcript_callback=transcript_callback,
            policy_bypass=policy_bypass,
        )
        if validation_error:
            reason = str(validation_error.get("error") if isinstance(validation_error, dict) else validation_error)
            _emit_tool_denial_primitives(session_state, parsed_calls, reason)
        if checkpoint_manager and before_tool and executed_results and not validation_error:
            after_tool: Optional[str] = None
            for parsed_call, tool_out in executed_results:
                tool_name = str(getattr(parsed_call, "function", "") or "").strip()
                if not tool_name:
                    continue
                try:
                    tool_name = conductor.agent_executor.canonical_tool_name(tool_name) or tool_name
                except Exception:
                    pass
                if tool_name.lower() not in write_tools:
                    continue
                if not conductor.agent_executor.is_tool_failure(tool_name, tool_out):
                    after_tool = tool_name
            if after_tool:
                try:
                    checkpoint_manager.create_checkpoint(f"After {after_tool}", snapshot=_checkpoint_snapshot())
                except Exception:
                    pass
        return executed_results, failed_at_index, validation_error, meta
    finally:
        conductor.agent_executor.allow_multiple_bash = previous_value


__all__ = ['_inject_async_result_retrieval', '_coordination_task_context', '_record_validated_signal', '_is_completion_action_result', 'build_exec_func', 'resolve_replay_todo_placeholders', '_emit_tool_denial_primitives', 'execute_agent_calls']
