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

from .implementation_receipts import (
    _implementation_task_anchor, _successful_test_commands, _parsed_call_is_read_only_inspection,
    _implementation_receipts_satisfied, _implementation_verification_receipt_missing,
    _implementation_write_guard_config, _implementation_write_receipt_missing, _missing_requested_write_targets,
    _requested_write_targets, _latest_implementation_prompt, _latest_prompt_requires_implementation_write,
    _latest_prompt_requests_verification, _requested_verification_commands_satisfied, _maybe_auto_verify_make_after_write_receipts,
    _implementation_receipt_missing, _prompt_requires_implementation_write_text,
    _required_final_answer_marker, _latest_prompt_requests_read_only_answer_after_observation,
    _requested_final_answer_terms, _latest_prompt_requests_tool_stop_after_observation,
    _observed_tool_calls_since_read_only_prompt, _strip_internal_prompt_blocks,
)
def _post_receipt_final_reminder(session_state: SessionState) -> str:
    return (
        "<VALIDATION_ERROR>\n"
        "All requested implementation and verification receipts are already present. "
        "Do not inspect more files or run more commands. Give the final answer now with files changed and exact verification commands/results."
        f"{_implementation_task_anchor(session_state)}\n"
        "</VALIDATION_ERROR>"
    )

def _force_post_receipt_final_answer(
    session_state: SessionState,
    *,
    reason: str,
) -> bool:
    final_message = _build_post_receipt_final_message(session_state)
    session_state.add_message({"role": "assistant", "content": final_message}, to_provider=False)
    try:
        summary = dict(getattr(session_state, "tool_usage_summary", {}) or {})
        event_payload = {
            "reason": reason,
            "tool_usage": summary,
            "targets": _post_receipt_final_targets(session_state),
        }
        session_state.record_guardrail_event("implementation_post_receipt_forced_closure", event_payload)
        session_state.add_transcript_entry({"implementation_post_receipt_forced_closure": event_payload})
    except Exception:
        pass
    session_state.completion_summary = {
        "completed": True,
        "method": "post_receipt_forced_closure",
        "reason": reason,
        "confidence": 0.8,
        "source": "workloop_guard",
        "final_message": final_message,
    }
    return True

def _post_receipt_final_targets(session_state: SessionState) -> List[str]:
    summary = dict(getattr(session_state, "tool_usage_summary", {}) or {})
    targets = [
        str(target)
        for target in summary.get("successful_requested_write_targets", []) or []
        if str(target).strip()
    ]
    user_facing_targets = [
        str(target)
        for target in summary.get("successful_user_facing_write_targets", []) or []
        if str(target).strip()
    ]
    if user_facing_targets and (not targets or len(user_facing_targets) > len(targets)):
        targets = user_facing_targets
    if not targets:
        targets = [
            str(target)
            for target in summary.get("requested_write_targets", []) or []
            if str(target).strip()
        ]
    return targets

def _build_post_receipt_final_message(session_state: SessionState) -> str:
    summary = dict(getattr(session_state, "tool_usage_summary", {}) or {})
    targets = _post_receipt_final_targets(session_state)
    file_line = ", ".join(f"`{target}`" for target in targets) if targets else "requested project files"
    successful_commands = _successful_test_commands(session_state)
    if successful_commands:
        verification_line = "; ".join(successful_commands)
    elif int(summary.get("successful_tests") or 0) > 0:
        verification_line = f"{int(summary.get('successful_tests') or 0)} successful verification command(s)"
    else:
        verification_line = "verification receipt present"
    marker = _required_final_answer_marker(session_state)
    marker_prefix = f"{marker}\n" if marker else ""
    final_message = (
        f"{marker_prefix}"
        "Implementation receipts and verification receipts are present, so I am closing the task without running more tools.\n\n"
        f"Files changed: {file_line}\n"
        f"Verification: {verification_line}\n\n"
        "The model attempted to continue with progress or read-only work after the receipts were already satisfied; "
        "BreadBoard forced closure to avoid an unproductive loop."
    )
    return final_message

def _build_read_only_observation_final_message(session_state: SessionState) -> str:
    terms = _requested_final_answer_terms(session_state)
    marker_prefix = f"{terms[0]}\n" if terms else ""
    latest_prompt = re.sub(r"\s+", " ", latest_real_user_prompt(session_state)).strip()
    prompt_clause = f"\n\nRequest: {latest_prompt[:500]}" if latest_prompt else ""
    return (
        f"{marker_prefix}"
        "The requested workspace observation has already been performed, so I am closing this read-only turn without running more tools.\n\n"
        "Result: the workspace/tool output was observed successfully; no file changes were requested or made."
        f"{prompt_clause}\n\n"
        "BreadBoard forced closure to avoid a repetitive read-only inspection loop."
    )

def _force_read_only_observation_final_answer(
    session_state: SessionState,
    *,
    reason: str,
) -> bool:
    final_message = _build_read_only_observation_final_message(session_state)
    session_state.add_message({"role": "assistant", "content": final_message}, to_provider=False)
    try:
        summary = dict(getattr(session_state, "tool_usage_summary", {}) or {})
        event_payload = {
            "reason": reason,
            "tool_usage": summary,
            "terms": _requested_final_answer_terms(session_state),
        }
        session_state.record_guardrail_event("read_only_observation_forced_closure", event_payload)
        session_state.add_transcript_entry({"read_only_observation_forced_closure": event_payload})
    except Exception:
        pass
    session_state.completion_summary = {
        "completed": True,
        "method": "read_only_observation_forced_closure",
        "reason": reason,
        "confidence": 0.7,
        "source": "workloop_guard",
        "final_message": final_message,
    }
    return True

def _maybe_force_read_only_observation_closure(
    session_state: SessionState,
    parsed_calls: List[Any],
) -> bool:
    if not parsed_calls:
        return False
    if not _latest_prompt_requests_read_only_answer_after_observation(session_state):
        return False
    if not all(_parsed_call_is_read_only_inspection(call) for call in parsed_calls):
        return False
    observed_calls = _observed_tool_calls_since_read_only_prompt(session_state)
    if observed_calls < 2:
        return False
    return _force_read_only_observation_final_answer(
        session_state,
        reason="post_observation_repeated_read_only_tool_attempt",
    )

def _ensure_tool_completion_final_message(
    conductor: ConductorContext,
    session_state: SessionState,
    *,
    reason: str,
) -> Optional[str]:
    if not _implementation_receipts_satisfied(conductor, session_state):
        return None
    for entry in reversed(getattr(session_state, "messages", []) or []):
        if not isinstance(entry, dict) or entry.get("role") != "assistant":
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        if "Verification:" in content or "Files changed:" in content:
            return content
        break
    final_message = _build_post_receipt_final_message(session_state)
    session_state.add_message({"role": "assistant", "content": final_message}, to_provider=False)
    try:
        session_state.record_guardrail_event(
            "implementation_mark_task_complete_final_message",
            {
                "reason": reason,
                "tool_usage": dict(getattr(session_state, "tool_usage_summary", {}) or {}),
                "targets": _post_receipt_final_targets(session_state),
            },
        )
    except Exception:
        pass
    return final_message

def _maybe_force_post_write_auto_verification_closure(
    conductor: ConductorContext,
    session_state: SessionState,
    *,
    reason: str,
) -> bool:
    """Close immediately when a write receipt enables deterministic verification.

    This is intentionally separate from completion rejection: if the model has
    already performed the requested write, and BreadBoard can run the exact
    requested smoke/build receipt, continuing the model loop only creates churn.
    """
    if _implementation_receipts_satisfied(conductor, session_state):
        return _force_post_receipt_final_answer(session_state, reason=reason)
    if not _maybe_auto_verify_make_after_write_receipts(conductor, session_state):
        return False
    return _force_post_receipt_final_answer(session_state, reason=reason)

def _latest_requested_exact_shell_command(session_state: SessionState) -> str:
    prompts: List[str] = []
    try:
        latest_prompt = _strip_internal_prompt_blocks(latest_real_user_prompt(session_state))
        if latest_prompt:
            prompts.append(latest_prompt)
    except Exception:
        pass
    try:
        for message in reversed(getattr(session_state, "messages", []) or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = _strip_internal_prompt_blocks(str(message.get("content") or ""))
            if text and not text.startswith("Tool execution results:"):
                prompts.append(text)
                break
    except Exception:
        pass
    for prompt in prompts:
        if _prompt_requires_implementation_write_text(prompt):
            continue
        match = re.search(r"\b(?:shell tool to run|run)\s+`([^`]+)`", prompt, flags=re.IGNORECASE)
        if not match:
            continue
        command = " ".join(str(match.group(1) or "").split())
        if command:
            return command
    return ""

def _successful_exact_shell_command_observation(session_state: SessionState, command: str) -> Dict[str, Any]:
    if not command:
        return {}
    normalized_command = " ".join(command.split())
    try:
        for turn_payload in (getattr(session_state, "turn_tool_usage", {}) or {}).values():
            for tool in (turn_payload or {}).get("tools", []) or []:
                meta = (tool or {}).get("meta") or {}
                observed = " ".join(str(meta.get("command") or "").split())
                if observed != normalized_command:
                    continue
                if not bool((tool or {}).get("success")) and int(meta.get("exit_code") or 1) != 0:
                    continue
                result = (tool or {}).get("result") or {}
                return {
                    "command": command,
                    "stdout": str(result.get("stdout") or "")[:1000],
                    "stderr": str(result.get("stderr") or "")[:1000],
                    "exit": result.get("exit", meta.get("exit_code")),
                }
    except Exception:
        return {}
    return {}

def _maybe_force_requested_shell_command_closure(
    session_state: SessionState,
    *,
    reason: str,
) -> bool:
    command = _latest_requested_exact_shell_command(session_state)
    observation = _successful_exact_shell_command_observation(session_state, command)
    if not observation:
        return False
    stdout = str(observation.get("stdout") or "").strip()
    stderr = str(observation.get("stderr") or "").strip()
    output_line = stdout or stderr or "(no output)"
    final_message = (
        "Requested shell command completed, so I am closing the task without running more tools.\n\n"
        f"Command: `{command}`\n"
        f"Exit: {observation.get('exit')}\n"
        f"Output: {output_line}"
    )
    session_state.add_message({"role": "assistant", "content": final_message}, to_provider=False)
    try:
        event_payload = {
            "reason": reason,
            "command": command,
            "observation": observation,
        }
        session_state.record_guardrail_event("requested_shell_command_forced_closure", event_payload)
        session_state.add_transcript_entry({"requested_shell_command_forced_closure": event_payload})
    except Exception:
        pass
    session_state.completion_summary = {
        "completed": True,
        "method": "requested_shell_command_forced_closure",
        "reason": reason,
        "confidence": 0.8,
        "source": "workloop_guard",
        "final_message": final_message,
    }
    return True

def _force_failed_verification_final_answer(
    session_state: SessionState,
    *,
    reason: str,
    min_failed_tests: int = 3,
) -> bool:
    if not _latest_prompt_requires_implementation_write(session_state):
        return False
    if not _latest_prompt_requests_verification(session_state):
        return False
    summary = dict(getattr(session_state, "tool_usage_summary", {}) or {})
    if int(summary.get("successful_user_facing_writes") or 0) <= 0:
        return False
    test_commands = int(summary.get("test_commands") or 0)
    successful_tests = int(summary.get("successful_tests") or 0)
    if test_commands < min_failed_tests:
        return False
    if successful_tests > 0 and _requested_verification_commands_satisfied(session_state):
        return False

    targets = [
        str(target)
        for target in summary.get("successful_user_facing_write_targets", []) or []
        if str(target).strip()
    ]
    if not targets:
        targets = [
            str(target)
            for target in summary.get("successful_requested_write_targets", []) or []
            if str(target).strip()
        ]
    failed_commands: List[str] = []
    try:
        for turn_payload in (getattr(session_state, "turn_tool_usage", {}) or {}).values():
            for tool in (turn_payload or {}).get("tools", []) or []:
                meta = (tool or {}).get("meta") or {}
                if not meta.get("is_test_command"):
                    continue
                if meta.get("exit_code") == 0 and bool((tool or {}).get("success")):
                    continue
                command = str(meta.get("command") or "").strip()
                if command and command not in failed_commands:
                    failed_commands.append(command)
    except Exception:
        failed_commands = []
    commands_line = ", ".join(f"`{command}`" for command in failed_commands[:4]) if failed_commands else "requested build/smoke verification"
    file_line = ", ".join(f"`{target}`" for target in targets) if targets else "requested project files"
    final_message = (
        "I created or modified the requested project files, but verification is still failing after repeated attempts.\n\n"
        f"Files changed: {file_line}\n"
        f"Failed verification attempts: {test_commands}\n"
        f"Commands attempted: {commands_line}\n\n"
        "BreadBoard is stopping this turn with an explicit failed-verification summary instead of exhausting more steps."
    )
    session_state.add_message({"role": "assistant", "content": final_message}, to_provider=False)
    try:
        event_payload = {
            "reason": reason,
            "tool_usage": summary,
            "targets": targets,
            "failed_commands": failed_commands,
        }
        session_state.record_guardrail_event("implementation_failed_verification_forced_closure", event_payload)
        session_state.add_transcript_entry({"implementation_failed_verification_forced_closure": event_payload})
    except Exception:
        pass
    session_state.completion_summary = {
        "completed": False,
        "method": "failed_verification_forced_closure",
        "reason": reason,
        "confidence": 0.75,
        "source": "workloop_guard",
        "final_message": final_message,
        "test_commands": test_commands,
        "successful_tests": successful_tests,
    }
    return True

def _failed_requested_write_attempts(session_state: SessionState) -> List[Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
    try:
        for turn_payload in (getattr(session_state, "turn_tool_usage", {}) or {}).values():
            for tool in (turn_payload or {}).get("tools", []) or []:
                meta = (tool or {}).get("meta") or {}
                if not meta.get("is_write") or not meta.get("is_requested_file_write"):
                    continue
                if bool((tool or {}).get("success")):
                    continue
                attempts.append({
                    "name": str((tool or {}).get("name") or ""),
                    "write_targets": list(meta.get("write_targets") or []),
                    "requested_write_matches": list(meta.get("requested_write_matches") or []),
                    "call_id": meta.get("call_id"),
                })
    except Exception:
        return attempts
    return attempts

def _force_failed_write_final_answer(
    conductor: ConductorContext,
    session_state: SessionState,
    *,
    reason: str,
    min_failed_writes: int = 3,
) -> bool:
    if not _latest_prompt_requires_implementation_write(session_state):
        return False
    attempts = _failed_requested_write_attempts(session_state)
    if len(attempts) < min_failed_writes:
        return False
    summary = dict(getattr(session_state, "tool_usage_summary", {}) or {})
    missing_write_receipt = _implementation_write_receipt_missing(conductor, session_state)
    successful_requested_writes = int(summary.get("successful_requested_file_writes") or 0)
    existing_target_edit_request = bool(
        re.search(r"\b(fix|edit|modify|update|repair)\b", _latest_implementation_prompt(session_state), flags=re.IGNORECASE)
    )
    if not missing_write_receipt and successful_requested_writes > 0:
        return False
    if not missing_write_receipt and not existing_target_edit_request:
        return False
    missing_targets = _missing_requested_write_targets(session_state)
    requested_targets = _requested_write_targets(session_state)
    target_line = ", ".join(missing_targets or requested_targets) if (missing_targets or requested_targets) else "requested files"
    attempted_targets = sorted(
        {
            str(target)
            for attempt in attempts
            for target in (attempt.get("write_targets") or [])
            if str(target).strip()
        }
    )
    attempted_line = ", ".join(attempted_targets) if attempted_targets else target_line
    final_message = (
        "I could not complete the requested implementation because repeated write attempts failed.\n\n"
        f"Missing requested files: {target_line}\n"
        f"Failed write attempts: {len(attempts)}\n"
        f"Attempted write targets: {attempted_line}\n\n"
        "BreadBoard is stopping this turn with an explicit failed-write summary instead of exhausting more steps."
    )
    session_state.add_message({"role": "assistant", "content": final_message}, to_provider=False)
    try:
        event_payload = {
            "reason": reason,
            "tool_usage": summary,
            "missing_requested_write_targets": missing_targets,
            "failed_write_attempts": attempts[-8:],
        }
        session_state.record_guardrail_event("implementation_failed_write_forced_closure", event_payload)
        session_state.add_transcript_entry({"implementation_failed_write_forced_closure": event_payload})
    except Exception:
        pass
    session_state.completion_summary = {
        "completed": False,
        "method": "failed_write_forced_closure",
        "reason": reason,
        "confidence": 0.75,
        "source": "workloop_guard",
        "final_message": final_message,
        "failed_write_attempts": len(attempts),
    }
    return True

def _reject_completion_without_implementation_write(
    conductor: ConductorContext,
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    stream_responses: bool,
) -> bool:
    missing_write_receipt = _implementation_write_receipt_missing(conductor, session_state)
    missing_verification_receipt = _implementation_verification_receipt_missing(conductor, session_state)
    if missing_verification_receipt and not missing_write_receipt:
        if _maybe_auto_verify_make_after_write_receipts(conductor, session_state):
            return _force_post_receipt_final_answer(
                session_state,
                reason="auto_verified_make_after_write_receipts",
            )
        missing_verification_receipt = _implementation_verification_receipt_missing(conductor, session_state)
    if not missing_write_receipt and not missing_verification_receipt:
        return False
    blocked_count = int(session_state.get_provider_metadata("implementation_missing_write_blocks") or 0) + 1
    session_state.set_provider_metadata("implementation_missing_write_blocks", blocked_count)
    missing_targets = _missing_requested_write_targets(session_state)
    target_clause = (
        " Missing requested files: " + ", ".join(missing_targets) + "."
        if missing_targets
        else ""
    )
    verification_clause = (
        " Run the requested build/smoke verification now."
        if missing_verification_receipt
        else ""
    )
    reminder = (
        "<VALIDATION_ERROR>\n"
        "This implementation task is not complete: required implementation receipts are missing. "
        "Create or modify the requested project files, then run the requested build/smoke verification before giving a final answer."
        f"{target_clause}{verification_clause}{_implementation_task_anchor(session_state)}\n"
        "</VALIDATION_ERROR>"
    )
    session_state.add_message({"role": "user", "content": reminder}, to_provider=True)
    try:
        session_state.record_guardrail_event(
            "implementation_completion_without_write",
            {
                "count": blocked_count,
                "tool_usage": dict(getattr(session_state, "tool_usage_summary", {}) or {}),
                "missing_requested_write_targets": missing_targets,
                "missing_verification_receipt": missing_verification_receipt,
            },
        )
        session_state.add_transcript_entry(
            {
                "implementation_completion_without_write_block": {
                    "count": blocked_count,
                    "tool_usage": dict(getattr(session_state, "tool_usage_summary", {}) or {}),
                    "missing_requested_write_targets": missing_targets,
                    "missing_verification_receipt": missing_verification_receipt,
                }
            }
        )
    except Exception:
        pass
    try:
        markdown_logger.log_user_message(reminder)
    except Exception:
        pass
    if stream_responses:
        try:
            print("[guard] rejecting implementation completion without write receipt")
        except Exception:
            pass
    if blocked_count >= 3:
        session_state.completion_summary = {
            "completed": False,
            "reason": "implementation_missing_write_receipt_loop",
            "method": "workloop_guard",
        }
        session_state.set_provider_metadata("completion_guard_abort", True)
        return True
    return False

def _maybe_block_read_only_implementation_loop(
    conductor: ConductorContext,
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    parsed_calls: List[Any],
    stream_responses: bool,
) -> bool:
    guard_cfg = _implementation_write_guard_config(conductor)
    if not bool(guard_cfg.get("enabled", False)):
        return False
    if not _latest_prompt_requires_implementation_write(session_state):
        return False
    summary = getattr(session_state, "tool_usage_summary", {}) or {}
    receipts_satisfied = _implementation_receipts_satisfied(conductor, session_state)
    if receipts_satisfied:
        if not parsed_calls:
            return False
        return _force_post_receipt_final_answer(
            session_state,
            reason="post_receipt_extra_tool_attempt",
        )
    missing_write_receipt = _implementation_write_receipt_missing(conductor, session_state)
    missing_verification_receipt = _implementation_verification_receipt_missing(conductor, session_state)
    if not missing_write_receipt and not missing_verification_receipt:
        return False
    if not parsed_calls or not all(_parsed_call_is_read_only_inspection(call) for call in parsed_calls):
        return False
    read_only_limit = int(guard_cfg.get("max_read_only_calls_before_write") or 4)
    run_shell_calls = int(summary.get("run_shell_calls") or 0)
    total_calls = int(summary.get("total_calls") or 0)
    if max(run_shell_calls, total_calls) < read_only_limit:
        return False
    read_only_loop_blocks = int(session_state.get_provider_metadata("implementation_read_only_loop_blocks") or 0) + 1
    session_state.set_provider_metadata("implementation_read_only_loop_blocks", read_only_loop_blocks)
    failed_write_attempts = _failed_requested_write_attempts(session_state)
    if read_only_loop_blocks >= 3:
        if failed_write_attempts:
            return _force_failed_write_final_answer(
                conductor,
                session_state,
                reason="read_only_loop_after_failed_requested_write",
                min_failed_writes=1,
            )
        return _force_read_only_observation_final_answer(
            session_state,
            reason="repeated_read_only_loop_safety_net",
        )
    blocked_tools = [str(getattr(call, "function", "") or "") for call in parsed_calls]
    missing_targets = _missing_requested_write_targets(session_state)
    target_clause = (
        " Your next write must target: " + ", ".join(missing_targets) + "."
        if missing_targets
        else ""
    )
    verification_clause = (
        " Your next tool call must run the requested build/smoke verification, such as `make` and `./smoke_test.sh`."
        if missing_verification_receipt
        else ""
    )
    failed_write_clause = (
        " A previous requested write attempt failed; use the patch/tool error and already-observed file contents to issue a corrected write, not more read-only inspection."
        if failed_write_attempts
        else ""
    )
    reminder = (
        "<VALIDATION_ERROR>\n"
        "This is an implementation task. You have already inspected the workspace enough and required implementation receipts are still missing. "
        "Your next tool call must create or modify the requested project files with apply_patch/write tooling, unless there is a concrete blocker. "
        f"Do not run another read-only inspection command.{target_clause}{verification_clause}{failed_write_clause}{_implementation_task_anchor(session_state)}\n"
        "</VALIDATION_ERROR>"
    )
    session_state.add_message({"role": "user", "content": reminder}, to_provider=True)
    try:
        session_state.record_guardrail_event(
            "implementation_read_only_loop",
            {
                "blocked_tools": blocked_tools,
                "total_calls": total_calls,
                "run_shell_calls": run_shell_calls,
                "successful_writes": int(summary.get("successful_writes") or 0),
                "missing_requested_write_targets": missing_targets,
                "missing_verification_receipt": missing_verification_receipt,
            },
        )
        session_state.add_transcript_entry(
            {
                "implementation_read_only_loop_block": {
                    "blocked_tools": blocked_tools,
                    "total_calls": total_calls,
                    "run_shell_calls": run_shell_calls,
                    "limit": read_only_limit,
                    "missing_requested_write_targets": missing_targets,
                    "missing_verification_receipt": missing_verification_receipt,
                }
            }
        )
    except Exception:
        pass
    try:
        markdown_logger.log_user_message(reminder)
    except Exception:
        pass
    if stream_responses:
        try:
            print("[guard] blocking read-only inspection loop for implementation task")
        except Exception:
            pass
    return True


__all__ = ['_post_receipt_final_reminder', '_force_post_receipt_final_answer', '_post_receipt_final_targets', '_build_post_receipt_final_message', '_build_read_only_observation_final_message', '_force_read_only_observation_final_answer', '_maybe_force_read_only_observation_closure', '_ensure_tool_completion_final_message', '_maybe_force_post_write_auto_verification_closure', '_latest_requested_exact_shell_command', '_successful_exact_shell_command_observation', '_maybe_force_requested_shell_command_closure', '_force_failed_verification_final_answer', '_failed_requested_write_attempts', '_force_failed_write_final_answer', '_reject_completion_without_implementation_write', '_maybe_block_read_only_implementation_loop']
