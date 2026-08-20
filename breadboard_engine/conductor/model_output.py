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

from .completion_guards import (
    _ensure_tool_completion_final_message, _force_failed_verification_final_answer, _force_failed_write_final_answer,
    _force_post_receipt_final_answer, _force_read_only_observation_final_answer, _maybe_block_read_only_implementation_loop,
    _maybe_force_post_write_auto_verification_closure, _maybe_force_read_only_observation_closure,
    _maybe_force_requested_shell_command_closure, _reject_completion_without_implementation_write,
)
from .execution_records import ReplayToolOutputMismatchError, legacy_message_view
from .implementation_receipts import (
    _async_result_task_id_from_activity, _is_allowed_async_result_followup,
    _implementation_receipt_missing, _implementation_receipts_satisfied, _latest_prompt_requests_verification,
    _latest_prompt_requests_tool_stop_after_observation, _latest_prompt_requests_read_only_answer_after_observation,
    _required_final_answer_marker, _required_final_answer_reminder,
)
from .replay_compare import record_replay_tool_output_mismatches
from .tool_executor import (
    _coordination_task_context, _inject_async_result_retrieval,
    _is_completion_action_result, build_exec_func, execute_agent_calls,
    _record_validated_signal,
)
from .turn_runtime import (
    apply_turn_guards, build_turn_context, finalize_turn_context_snapshot, handle_blocked_calls,
    maybe_transition_plan_mode, summarize_execution_results,
)
def log_provider_message(conductor: ConductorContext, provider_message: ProviderMessage, session_state: SessionState, markdown_logger: MarkdownLogger, stream_responses: bool) -> None:
    legacy_msg = legacy_message_view(provider_message)

    try:
        debug_tc = None
        if getattr(legacy_msg, "tool_calls", None):
            debug_tc = [
                {
                    "id": getattr(tc, "id", None),
                    "name": getattr(getattr(tc, "function", None), "name", None),
                }
                for tc in legacy_msg.tool_calls
            ]
        session_state.add_transcript_entry(
            {
                "choice_debug": {
                    "finish_reason": provider_message.finish_reason,
                    "has_content": bool(getattr(legacy_msg, "content", None)),
                    "tool_calls_len": len(legacy_msg.tool_calls)
                    if getattr(legacy_msg, "tool_calls", None)
                    else 0,
                }
            }
        )
    except Exception:
        pass

    if getattr(legacy_msg, "content", None):
        markdown_logger.log_assistant_message(str(legacy_msg.content))
        try:
            if conductor.logger_v2.run_dir:
                conductor.logger_v2.append_text(
                    "conversation/conversation.md",
                    conductor.md_writer.assistant(str(legacy_msg.content)),
                )
        except Exception:
            pass

    if stream_responses and getattr(legacy_msg, "content", None):
        try:
            print(str(legacy_msg.content))
        except Exception:
            pass

def process_model_output(
    conductor: ConductorContext,
    provider_message: ProviderMessage,
    caller: Any,
    tool_defs: List[ToolDefinition],
    session_state: SessionState,
    completion_detector: Any,
    markdown_logger: MarkdownLogger,
    error_handler: Any,
    stream_responses: bool,
    model: str,
) -> bool:
    msg = legacy_message_view(provider_message)
    completion_cfg = (conductor.config.get("completion", {}) or {})
    summary = getattr(session_state, "tool_usage_summary", {})
    if (
        completion_cfg.get("allow_zero_tool_completion")
        and not getattr(msg, "tool_calls", None)
    ):
        total_calls = int(summary.get("total_calls") or 0)
        if total_calls <= 0 and (msg.content or ""):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if ">>>>>> END RESPONSE" not in content:
                msg.content = f"{content.rstrip()}\n\n>>>>>> END RESPONSE"

    if not getattr(msg, "tool_calls", None) and (msg.content or ""):
        return handle_text_tool_calls(
            conductor,
            msg,
            caller,
            tool_defs,
            session_state,
            completion_detector,
            provider_message.finish_reason,
            markdown_logger,
            error_handler,
            stream_responses,
        )

    if msg.tool_calls:
        return handle_native_tool_calls(
            conductor,
            msg,
            session_state,
            markdown_logger,
            error_handler,
            stream_responses,
            model,
        )

    if msg.content:
        session_state.add_message({"role": "assistant", "content": msg.content})
        session_state.add_transcript_entry({"assistant": msg.content})

        assistant_history = session_state.get_provider_metadata("assistant_text_history", [])
        if not isinstance(assistant_history, list):
            assistant_history = []
        recent_tool_activity = session_state.get_provider_metadata("recent_tool_activity")
        mark_tool_available = session_state.get_provider_metadata("mark_task_complete_available")
        completion_analysis = completion_detector.detect_completion(
            msg_content=msg.content or "",
            choice_finish_reason=provider_message.finish_reason,
            tool_results=[],
            agent_config=conductor.config,
            recent_tool_activity=recent_tool_activity,
            assistant_history=assistant_history,
            mark_tool_available=mark_tool_available,
        )

        session_state.add_transcript_entry({"completion_analysis": completion_analysis})

        normalized_assistant_text = conductor._normalize_assistant_text(msg.content)
        if normalized_assistant_text:
            updated_history = (assistant_history + [normalized_assistant_text])[-5:]
            session_state.set_provider_metadata("assistant_text_history", updated_history)
        session_state.set_provider_metadata("recent_tool_activity", None)

        if completion_analysis["completed"]:
            if _implementation_receipt_missing(conductor, session_state):
                abort = _reject_completion_without_implementation_write(
                    conductor,
                    session_state,
                    markdown_logger,
                    stream_responses,
                )
                return bool(abort)
            signal_task_id, signal_parent_task_id, signal_mission_task_id = _coordination_task_context(session_state)
            rejection_reasons: list[str] = []
            threshold_met = completion_detector.meets_threshold(completion_analysis)
            if not threshold_met:
                rejection_reasons.append(
                    f"confidence_below_threshold:{completion_analysis.get('confidence', 0.0)}<{completion_detector.threshold}"
                )
            guard_ok, guard_reason = conductor._completion_guard_check(session_state)
            if threshold_met and not guard_ok and guard_reason:
                rejection_reasons.append(f"completion_guard_failed:{guard_reason}")
            validated_signal = validate_signal_proposal(
                build_completion_signal_proposal(
                    completion_analysis,
                    task_id=signal_task_id,
                    parent_task_id=signal_parent_task_id,
                    mission_task_id=signal_mission_task_id,
                ),
                mission_owner_role=str(
                    session_state.get_provider_metadata("completion_owner_role") or "assistant"
                ),
                extra_rejection_reasons=rejection_reasons,
            )
            recorded_signal = _record_validated_signal(
                session_state,
                validated_signal,
                turn=session_state.get_provider_metadata("current_turn_index")
                if isinstance(session_state.get_provider_metadata("current_turn_index"), int)
                else None,
            )

            if threshold_met and not guard_ok and guard_reason:
                abort = conductor._emit_completion_guard_feedback(
                    session_state,
                    markdown_logger,
                    guard_reason,
                    stream_responses,
                )
                if abort:
                    session_state.set_provider_metadata("completion_guard_abort", True)
                return False

            session_state.add_transcript_entry({
                "completion_detected": {
                    "method": completion_analysis["method"],
                    "confidence": completion_analysis["confidence"],
                    "reason": completion_analysis["reason"],
                    "content_analyzed": bool(msg.content),
                    "threshold_met": threshold_met,
                    "signal_status": recorded_signal.get("status"),
                }
            })
            if is_accepted_signal(recorded_signal):
                if stream_responses:
                    print(f"[stop] reason={completion_analysis['method']} confidence={completion_analysis['confidence']:.2f} - {completion_analysis['reason']}")
                if not getattr(session_state, "completion_summary", None):
                    session_state.completion_summary = {
                        "completed": True,
                        "method": completion_analysis["method"],
                        "reason": completion_analysis["reason"],
                        "confidence": completion_analysis["confidence"],
                        "source": "assistant_content",
                        "analysis": completion_analysis,
                        "signal": recorded_signal,
                    }
                else:
                    session_state.completion_summary.setdefault("completed", True)
                    session_state.completion_summary.setdefault("method", completion_analysis["method"])
                    session_state.completion_summary.setdefault("reason", completion_analysis["reason"])
                    session_state.completion_summary.setdefault("confidence", completion_analysis["confidence"])
                    session_state.completion_summary.setdefault("signal", recorded_signal)
                return True
        else:
            completion_cfg = (conductor.config.get("completion", {}) or {})
            if completion_cfg.get("allow_zero_tool_completion"):
                summary = getattr(session_state, "tool_usage_summary", {})
                total_calls = int(summary.get("total_calls") or 0)
                if total_calls <= 0:
                    signal_task_id, signal_parent_task_id, signal_mission_task_id = _coordination_task_context(session_state)
                    synthetic_analysis = {
                        "completed": True,
                        "method": "auto_zero_tool",
                        "reason": "Conversation-only turn; zero tool usage allowed",
                        "confidence": 0.65,
                        "signal_code": "complete",
                        "signal_source_kind": "assistant_content",
                        "source": "assistant_content",
                    }
                    recorded_signal = _record_validated_signal(
                        session_state,
                        validate_signal_proposal(
                            build_completion_signal_proposal(
                                synthetic_analysis,
                                task_id=signal_task_id,
                                parent_task_id=signal_parent_task_id,
                                mission_task_id=signal_mission_task_id,
                            ),
                            mission_owner_role=str(
                                session_state.get_provider_metadata("completion_owner_role") or "assistant"
                            ),
                        ),
                        turn=session_state.get_provider_metadata("current_turn_index")
                        if isinstance(session_state.get_provider_metadata("current_turn_index"), int)
                        else None,
                    )
                    session_state.completion_summary = {
                        "completed": True,
                        "method": "auto_zero_tool",
                        "reason": "Conversation-only turn; zero tool usage allowed",
                        "confidence": 0.65,
                        "source": "assistant_content",
                        "analysis": completion_analysis,
                        "signal": recorded_signal,
                    }
                    return True

    return False

def handle_text_tool_calls(
    conductor: ConductorContext,
    msg,
    caller,
    tool_defs: List[ToolDefinition],
    session_state: SessionState,
    completion_detector: Any,
    choice_finish_reason: Optional[str],
    markdown_logger: MarkdownLogger,
    error_handler: Any,
    stream_responses: bool,
) -> bool:
    prior_tool_activity = session_state.get_provider_metadata("recent_tool_activity")
    parsed = caller.parse_all(msg.content, tool_defs)
    synthesized_blocks: List[str] = []
    if not parsed:
        synthesized_blocks = conductor._synthesize_patch_blocks(msg.content)
        if synthesized_blocks:
            use_patch_tool = bool(getattr(conductor, "enhanced_executor", None))
            parsed = []
            for idx, block in enumerate(synthesized_blocks):
                normalized = conductor._normalize_patch_block(block)
                if not normalized:
                    continue
                if use_patch_tool:
                    arguments = {"patchText": normalized}
                    function_name = "patch"
                else:
                    unified = conductor._convert_patch_to_unified(normalized)
                    if not unified:
                        continue
                    arguments = {"patch": unified}
                    function_name = "apply_unified_patch"
                parsed.append(
                    SimpleNamespace(
                        function=function_name,
                        arguments=arguments,
                        provider_name="synthetic_patch",
                        call_id=f"synthetic_patch_{uuid.uuid4().hex[:8]}_{idx}",
                    )
                )
    current_mode = session_state.get_provider_metadata("current_mode")
    require_workspace_tool_usage = session_requires_workspace_tool_usage(session_state)
    if not parsed:
        if require_workspace_tool_usage and not prior_tool_activity:
            if assistant_is_progress_update(msg.content or ""):
                session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
                session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
                try:
                    session_state.add_transcript_entry(
                        {
                            "assistant_progress_update": {
                                "source": "tool_required_guard",
                                "text": msg.content,
                            }
                        }
                    )
                except Exception:
                    pass
                return False
            warning = (
                "<VALIDATION_ERROR>\n"
                "This request requires real workspace interaction. Use read/list/diff/bash tools, then answer from the observed result.\n"
                "</VALIDATION_ERROR>"
            )
            session_state.add_message({"role": "user", "content": warning}, to_provider=True)
            try:
                markdown_logger.log_user_message(warning)
            except Exception:
                pass
            if stream_responses:
                try:
                    print("[guard] rejecting assistant-only reply for tool-required request")
                except Exception:
                    pass
            return False
        if prior_tool_activity and assistant_is_progress_update(msg.content or ""):
            if _latest_prompt_requests_tool_stop_after_observation(session_state):
                session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
                reminder = (
                    "<VALIDATION_ERROR>\n"
                    "The required workspace tool has already run for this request. Do not call more tools. "
                    f"Answer now.{_required_final_answer_reminder(session_state)}\n"
                    "</VALIDATION_ERROR>"
                )
                session_state.add_message({"role": "user", "content": reminder}, to_provider=True)
                try:
                    markdown_logger.log_user_message(reminder)
                except Exception:
                    pass
                if stream_responses:
                    try:
                        print("[guard] redirecting progress update after required tool use")
                    except Exception:
                        pass
                return False
            if _latest_prompt_requests_read_only_answer_after_observation(session_state):
                return _force_read_only_observation_final_answer(
                    session_state,
                    reason="post_observation_progress_update_attempt",
                )
        if prior_tool_activity and _latest_prompt_requests_tool_stop_after_observation(session_state):
            async_task_id = _async_result_task_id_from_activity(prior_tool_activity)
            if async_task_id:
                session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
                return _inject_async_result_retrieval(
                    conductor,
                    session_state,
                    markdown_logger,
                    prior_tool_activity,
                    reason="post_required_final_before_async_result",
                    stream_responses=stream_responses,
                )
            required_marker = _required_final_answer_marker(session_state)
            if required_marker and required_marker not in (msg.content or ""):
                blocked_count = int(session_state.get_provider_metadata("post_required_tool_bad_final_blocks") or 0) + 1
                session_state.set_provider_metadata("post_required_tool_bad_final_blocks", blocked_count)
                session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
                try:
                    session_state.add_transcript_entry({
                        "post_required_tool_bad_final_block": {
                            "required_marker": required_marker,
                            "count": blocked_count,
                            "assistant_excerpt": (msg.content or "")[:500],
                        }
                    })
                except Exception:
                    pass
                reminder = (
                    "<VALIDATION_ERROR>\n"
                    "The required workspace tool has already run for this request, but your answer did not satisfy "
                    f"the required final-answer contract. Do not call more tools. Answer now with first line exactly "
                    f"`{required_marker}` and summarize only the observed tool result.\n"
                    "</VALIDATION_ERROR>"
                )
                session_state.add_message({"role": "user", "content": reminder}, to_provider=True)
                try:
                    markdown_logger.log_user_message(reminder)
                except Exception:
                    pass
                if stream_responses:
                    try:
                        print("[guard] rejecting post-tool answer missing required marker")
                    except Exception:
                        pass
                if blocked_count >= 3:
                    session_state.completion_summary = {
                        "completed": False,
                        "reason": "post_required_tool_missing_marker_loop",
                        "method": "turn_contract_guard",
                        "required_marker": required_marker,
                    }
                    session_state.set_provider_metadata("completion_guard_abort", True)
                    return True
                return False
        completion_analysis = None
        try:
            completion_analysis = completion_detector.detect_completion(
                msg_content=msg.content or "",
                choice_finish_reason=choice_finish_reason,
                tool_results=[],
                agent_config=conductor.config,
                recent_tool_activity=prior_tool_activity,
                assistant_history=session_state.get_provider_metadata("assistant_text_history"),
                mark_tool_available=session_state.get_provider_metadata("mark_task_complete_available"),
            )
        except Exception:
            completion_analysis = None
        if isinstance(completion_analysis, dict):
            try:
                session_state.add_transcript_entry({"completion_analysis": completion_analysis})
            except Exception:
                pass
            if completion_analysis.get("completed") and _implementation_receipt_missing(conductor, session_state):
                return _reject_completion_without_implementation_write(
                    conductor,
                    session_state,
                    markdown_logger,
                    stream_responses,
                )
            if completion_analysis.get("method") == "progress_update":
                if _implementation_receipts_satisfied(conductor, session_state):
                    return _force_post_receipt_final_answer(
                        session_state,
                        reason="post_receipt_progress_update_attempt",
                    )
                if prior_tool_activity and _latest_prompt_requests_tool_stop_after_observation(session_state):
                    reminder = (
                        "<VALIDATION_ERROR>\n"
                        "You have already used the required workspace tool for this user request. Do not inspect more files. "
                        f"Answer the current user request now.{_required_final_answer_reminder(session_state)}\n"
                        "</VALIDATION_ERROR>"
                    )
                    session_state.add_message({"role": "user", "content": reminder}, to_provider=True)
                    try:
                        markdown_logger.log_user_message(reminder)
                    except Exception:
                        pass
                    if stream_responses:
                        try:
                            print("[guard] redirecting progress update after required tool use")
                        except Exception:
                            pass
                    return False
        session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
        session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
        if isinstance(completion_analysis, dict):
            if completion_analysis.get("completed"):
                signal_task_id, signal_parent_task_id, signal_mission_task_id = _coordination_task_context(session_state)
                rejection_reasons: list[str] = []
                if not completion_detector.meets_threshold(completion_analysis):
                    rejection_reasons.append(
                        f"confidence_below_threshold:{completion_analysis.get('confidence', 0.0)}<{completion_detector.threshold}"
                    )
                recorded_signal = _record_validated_signal(
                    session_state,
                    validate_signal_proposal(
                        build_completion_signal_proposal(
                            completion_analysis,
                            task_id=signal_task_id,
                            parent_task_id=signal_parent_task_id,
                            mission_task_id=signal_mission_task_id,
                        ),
                        mission_owner_role=str(
                            session_state.get_provider_metadata("completion_owner_role") or "assistant"
                        ),
                        extra_rejection_reasons=rejection_reasons,
                    ),
                    turn=session_state.get_provider_metadata("current_turn_index")
                    if isinstance(session_state.get_provider_metadata("current_turn_index"), int)
                    else None,
                )
                if is_accepted_signal(recorded_signal):
                    if not getattr(session_state, "completion_summary", None):
                        session_state.completion_summary = {
                            "completed": True,
                            "method": completion_analysis.get("method"),
                            "reason": completion_analysis.get("reason"),
                            "confidence": completion_analysis.get("confidence"),
                            "source": "assistant_content",
                            "analysis": completion_analysis,
                            "signal": recorded_signal,
                        }
                    else:
                        session_state.completion_summary.setdefault("completed", True)
                        session_state.completion_summary.setdefault("reason", completion_analysis.get("reason"))
                        session_state.completion_summary.setdefault("method", completion_analysis.get("method"))
                        session_state.completion_summary.setdefault("signal", recorded_signal)
                    session_state.set_provider_metadata("recent_tool_activity", None)
                    return True
        session_state.set_provider_metadata("recent_tool_activity", None)
        if current_mode == "plan":
            manager = session_state.get_todo_manager()
            snapshot = manager.snapshot() if manager else None
            todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
            if not todos:
                try:
                    conductor.guardrail_orchestrator._emit_todo_guard_violation(  # type: ignore[attr-defined]
                        session_state,
                        markdown_logger,
                        "Plan mode requires creating at least one todo via `todo.create` before using edit or bash tools.",
                        blocked_call=None,
                    )
                except Exception:
                    pass
                msg.content = ""
        return False
    parsed = conductor._expand_multi_file_patches(parsed, session_state, markdown_logger)
    if _maybe_block_read_only_implementation_loop(
        conductor,
        session_state,
        markdown_logger,
        parsed,
        stream_responses,
    ):
        msg.content = ""
        completion_summary = getattr(session_state, "completion_summary", None) or {}
        return bool(completion_summary.get("completed"))
    if _maybe_force_read_only_observation_closure(session_state, parsed):
        msg.content = ""
        return True
    session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
    session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
    session_state.add_transcript_entry({"assistant": msg.content})

    plan_bootstrap_traces = session_state.get_provider_metadata("plan_bootstrap_traces") or []
    session_state.set_provider_metadata("plan_bootstrap_traces", plan_bootstrap_traces)

    caller.track_tool_usage(parsed, session_state=session_state)

    turn_ctx = build_turn_context(conductor, session_state, parsed)
    parsed = apply_turn_guards(conductor, turn_ctx, session_state)
    handle_blocked_calls(conductor, turn_ctx, session_state, markdown_logger)
    if not parsed:
        msg.content = ""
        return False

    exec_func = build_exec_func(conductor, session_state)
    executed_results, failed_at_index, execution_error, plan_metadata = execute_agent_calls(
        conductor,
        parsed,
        exec_func,
        session_state,
        transcript_callback=session_state.add_transcript_entry,
        policy_bypass=session_state.get_provider_metadata("replay_mode"),
    )
    turn_ctx.plan_metadata = plan_metadata

    session_state.add_transcript_entry({"tool_execution_plan": plan_metadata})
    turn_index = session_state.get_provider_metadata("current_turn_index")
    turn_index_int = turn_index if isinstance(turn_index, int) else None
    try:
        conductor.provider_metrics.add_concurrency_sample(
            turn=turn_index_int,
            plan=plan_metadata,
        )
    except Exception:
        pass

    recent_tools_summary, test_success = summarize_execution_results(
        conductor,
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
    if _maybe_force_requested_shell_command_closure(
        session_state,
        reason="requested_shell_command_observed_before_continuation",
    ):
        finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
        return True
    if _force_failed_verification_final_answer(
        session_state,
        reason="failed_verification_after_retries",
    ):
        finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
        return True
    if _force_failed_write_final_answer(
        conductor,
        session_state,
        reason="failed_requested_write_after_retries",
    ):
        finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
        return True

    try:
        if conductor.logger_v2.run_dir and executed_results:
            persist_turn = len(session_state.transcript) + 1
            persistable = []
            for idx, (parsed_call, call_result) in enumerate(executed_results):
                persistable.append({
                    "fn": getattr(parsed_call, "function", ""),
                    "provider_fn": getattr(parsed_call, "provider_name", getattr(parsed_call, "function", "")),
                    "call_id": getattr(parsed_call, "call_id", f"text_call_{idx}"),
                    "args": getattr(parsed_call, "arguments", {}),
                    "out": call_result,
                })
            if persistable:
                conductor.provider_logger.save_tool_results(persist_turn, persistable)
    except Exception:
        pass
        if _force_failed_verification_final_answer(
            session_state,
            reason="failed_verification_after_retries",
        ):
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return True

        if execution_error:
            try:
                use_responses_api = (
                    str(session_state.get_provider_metadata("api_variant") or "").lower() == "responses"
                )
            except Exception:
                use_responses_api = False
            if use_responses_api and executed_results:
                for parsed_result_call, parsed_result_out in executed_results:
                    call_id = getattr(parsed_result_call, "call_id", None)
                    tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                        getattr(parsed_result_call, "function", ""),
                        parsed_result_out,
                        syntax_type="openai",
                        call_id=call_id,
                    )
                    session_state.add_message(tool_result_entry, to_provider=True)
            if execution_error.get("validation_failed"):
                session_state.increment_guardrail_counter("validation_errors")
                error_msg = error_handler.handle_validation_error(execution_error)
            elif execution_error.get("constraint_violation"):
                error_msg = error_handler.handle_constraint_violation(execution_error["error"])
            else:
                error_msg = f"<EXECUTION_ERROR>\n{execution_error['error']}\n</EXECUTION_ERROR>"

            session_state.add_message({"role": "user", "content": error_msg}, to_provider=True)
            markdown_logger.log_user_message(error_msg)

            if stream_responses:
                print(f"[error] {execution_error.get('error', 'Unknown error')}")
            try:
                if turn_index_int is not None:
                    if execution_error.get("validation_failed"):
                        session_state.add_reward_metric(turn_index_int, "SVS", 0.0)
                    session_state.add_reward_metric(turn_index_int, "CPS", 0.0)
                    conductor._record_lsp_reward_metrics(session_state, turn_index_int)
                    if test_success is not None:
                        conductor._record_test_reward_metric(session_state, turn_index_int, 0.0)
            except Exception:
                pass
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return False
    try:
        if turn_index_int is not None:
            session_state.add_reward_metric(turn_index_int, "SVS", 1.0)
            if plan_metadata.get("total_calls"):
                executed_calls = plan_metadata.get("executed_calls", 0)
                total_calls = plan_metadata.get("total_calls", 0)
                cps_value = 1.0 if executed_calls == total_calls else 0.0
                session_state.add_reward_metric(turn_index_int, "CPS", cps_value)
            if executed_results:
                acs_value = 1.0 if failed_at_index == -1 else 0.0
                session_state.add_reward_metric(turn_index_int, "ACS", acs_value)
    except Exception:
        pass
    finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)

    for tool_parsed, tool_result in executed_results:
        tool_name = getattr(tool_parsed, "function", "") if tool_parsed else ""
        action = tool_result.get("action") if isinstance(tool_result, dict) else None
        if action == "complete" or tool_name == "mark_task_complete":
            if _implementation_receipt_missing(conductor, session_state):
                abort = _reject_completion_without_implementation_write(
                    conductor,
                    session_state,
                    markdown_logger,
                    stream_responses,
                )
                if abort:
                    return True
                continue
            signal_task_id, signal_parent_task_id, signal_mission_task_id = _coordination_task_context(session_state)
            rejection_reasons: list[str] = []
            guard_ok, guard_reason = conductor._completion_guard_check(session_state)
            if not guard_ok and guard_reason:
                rejection_reasons.append(f"completion_guard_failed:{guard_reason}")
            validated_signal = validate_signal_proposal(
                build_tool_completion_signal_proposal(
                    task_id=signal_task_id,
                    tool_name=tool_name,
                    tool_result=tool_result,
                    parent_task_id=signal_parent_task_id,
                    mission_task_id=signal_mission_task_id,
                ),
                mission_owner_role=str(
                    session_state.get_provider_metadata("completion_owner_role") or "assistant"
                ),
                extra_rejection_reasons=rejection_reasons,
            )
            recorded_signal = _record_validated_signal(
                session_state,
                validated_signal,
                turn=turn_index_int,
            )

            if not guard_ok and guard_reason:
                abort = conductor._emit_completion_guard_feedback(
                    session_state,
                    markdown_logger,
                    guard_reason,
                    stream_responses,
                )
                if abort:
                    session_state.set_provider_metadata("completion_guard_abort", True)
                continue

            if not is_accepted_signal(recorded_signal):
                continue

            final_message = _ensure_tool_completion_final_message(
                conductor,
                session_state,
                reason="mark_task_complete_after_receipts",
            )
            chunks = conductor.message_formatter.format_execution_results(executed_results, failed_at_index, len(parsed))
            provider_tool_msg = "\n\n".join(chunks)
            session_state.add_message({"role": "user", "content": provider_tool_msg}, to_provider=True)
            markdown_logger.log_user_message(provider_tool_msg)

            if not getattr(session_state, "completion_summary", None):
                session_state.completion_summary = {
                    "completed": True,
                    "method": "tool_mark_task_complete",
                    "reason": "mark_task_complete",
                    "confidence": 1.0,
                    "tool": tool_parsed.function,
                    "tool_result": tool_result,
                    "source": "tool_call",
                    "signal": recorded_signal,
                }
                if final_message:
                    session_state.completion_summary["final_message"] = final_message
            else:
                session_state.completion_summary.setdefault("completed", True)
                session_state.completion_summary.setdefault("reason", "mark_task_complete")
                session_state.completion_summary.setdefault("method", "tool_mark_task_complete")
                session_state.completion_summary.setdefault("signal", recorded_signal)
                if final_message:
                    session_state.completion_summary.setdefault("final_message", final_message)

            if stream_responses:
                print(f"[stop] reason=tool_based confidence=1.0 - mark_task_complete() called")
            return True

    artifact_links: list[str] = []
    try:
        if conductor.logger_v2.run_dir:
            for idx, (tool_parsed, tool_result) in enumerate(executed_results):
                rel = conductor.message_formatter.write_tool_result_file(
                    conductor.logger_v2.run_dir,
                    len(session_state.transcript) + 1,
                    idx,
                    tool_parsed.function,
                    tool_result,
                )
                if rel:
                    artifact_links.append(rel)
    except Exception:
        pass

    chunks = conductor.message_formatter.format_execution_results(executed_results, failed_at_index, len(parsed))

    for tool_parsed, tool_result in executed_results:
        call_id = getattr(tool_parsed, "call_id", None)
        tool_result_entry = conductor.message_formatter.create_tool_result_entry(
            tool_parsed.function, tool_result, syntax_type="custom-pythonic", call_id=call_id
        )
        session_state.add_message(tool_result_entry, to_provider=False)

    turn_cfg = conductor.config.get("turn_strategy", {})
    conductor.turn_relayer.relay_execution_chunks(
        chunks=chunks,
        artifact_links=artifact_links,
        session_state=session_state,
        markdown_logger=markdown_logger,
        turn_cfg=turn_cfg,
    )

    conductor._maybe_transition_plan_mode(session_state, markdown_logger)
    return False

def handle_native_tool_calls(
    conductor: ConductorContext,
    msg,
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    error_handler: Any,
    stream_responses: bool,
    model: str,
) -> bool:
    turn_cfg = conductor.config.get("turn_strategy", {})
    relay_strategy = (turn_cfg.get("relay") or "tool_role").lower()

    tool_messages_to_relay: List[Dict[str, Any]] = []

    try:
        tool_calls_payload = []
        for tc in msg.tool_calls:
            fn_name = getattr(getattr(tc, "function", None), "name", None)
            arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
            tool_calls_payload.append({
                "id": getattr(tc, "id", None),
                "type": "function",
                "function": {"name": fn_name, "arguments": arg_str if isinstance(arg_str, str) else json.dumps(arg_str or {})},
            })
        try:
            if conductor.logger_v2.run_dir and tool_calls_payload:
                turn_index = len(session_state.transcript) + 1
                conductor.provider_logger.save_tool_calls(turn_index, tool_calls_payload)
                short = "\n".join([f"- {c['function']['name']} (id={c.get('id')})" for c in tool_calls_payload])
                conductor.logger_v2.append_text(
                    "conversation/conversation.md",
                    conductor.md_writer.provider_tool_calls(
                        short,
                        f"provider_native/tool_calls/turn_{turn_index}.json",
                    ),
                )
        except Exception:
            pass

        enhanced_tool_calls = conductor.message_formatter.create_enhanced_tool_calls(tool_calls_payload)

        assistant_entry = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": enhanced_tool_calls,
        }
        provider_assistant_tool_message = {"role": "assistant", "content": msg.content, "tool_calls": tool_calls_payload}

        parsed_calls: List[Any] = []
        for tc in msg.tool_calls:
            fn = getattr(getattr(tc, "function", None), "name", None)
            call_id = getattr(tc, "id", None)
            arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
            try:
                args = json.loads(arg_str) if isinstance(arg_str, str) else (arg_str or {})
            except Exception:
                args = {}
            if not fn:
                continue
            canonical_fn = conductor.agent_executor.canonical_tool_name(fn)
            raw_meta = getattr(tc, "raw", None)
            expected_output = None
            expected_status = None
            expected_metadata = None
            if isinstance(raw_meta, dict):
                expected_output = raw_meta.get("expected_output")
                expected_status = raw_meta.get("expected_status")
                expected_metadata = raw_meta.get("metadata")
            call_obj = SimpleNamespace(
                function=canonical_fn,
                arguments=args,
                provider_name=fn,
                call_id=call_id,
                expected_output=expected_output,
                expected_status=expected_status,
                expected_metadata=expected_metadata,
            )
            parsed_calls.append(call_obj)

        current_mode = session_state.get_provider_metadata("current_mode")
        turn_ctx = build_turn_context(conductor, session_state, parsed_calls)
        parsed_calls = apply_turn_guards(conductor, turn_ctx, session_state)
        handle_blocked_calls(conductor, turn_ctx, session_state, markdown_logger)
        parsed_calls = conductor._expand_multi_file_patches(parsed_calls, session_state, markdown_logger)
        if _maybe_block_read_only_implementation_loop(
            conductor,
            session_state,
            markdown_logger,
            parsed_calls,
            stream_responses,
        ):
            completion_summary = getattr(session_state, "completion_summary", None) or {}
            return bool(completion_summary.get("completed"))
        prior_tool_activity = session_state.get_provider_metadata("recent_tool_activity")
        if (
            parsed_calls
            and prior_tool_activity
            and _latest_prompt_requests_tool_stop_after_observation(session_state)
            and not _is_allowed_async_result_followup(parsed_calls, prior_tool_activity)
        ):
            async_task_id = _async_result_task_id_from_activity(prior_tool_activity)
            if async_task_id:
                return _inject_async_result_retrieval(
                    conductor,
                    session_state,
                    markdown_logger,
                    prior_tool_activity,
                    reason="post_required_extra_call_before_async_result",
                    stream_responses=stream_responses,
                )
            blocked_count = int(session_state.get_provider_metadata("post_required_tool_extra_call_blocks") or 0) + 1
            session_state.set_provider_metadata("post_required_tool_extra_call_blocks", blocked_count)
            blocked_tools = [str(getattr(call, "function", "") or "") for call in parsed_calls]
            try:
                session_state.add_transcript_entry({
                    "post_required_tool_extra_call_block": {
                        "blocked_tools": blocked_tools,
                        "count": blocked_count,
                    }
                })
            except Exception:
                pass
            reminder = (
                "<VALIDATION_ERROR>\n"
                "The required workspace tool has already run for this request. Do not call additional tools. "
                f"Answer from the observed tool result now.{_required_final_answer_reminder(session_state)}\n"
                "</VALIDATION_ERROR>"
            )
            session_state.add_message({"role": "user", "content": reminder}, to_provider=True)
            try:
                markdown_logger.log_user_message(reminder)
            except Exception:
                pass
            if stream_responses:
                try:
                    print("[guard] blocking extra tool call after required tool use")
                except Exception:
                    pass
            if blocked_count >= 3:
                session_state.completion_summary = {
                    "completed": False,
                    "reason": "post_required_tool_extra_call_loop",
                    "method": "turn_contract_guard",
                    "blocked_tools": blocked_tools,
                }
                session_state.set_provider_metadata("completion_guard_abort", True)
                return True
            return False
        if parsed_calls and _maybe_force_read_only_observation_closure(session_state, parsed_calls):
            return True
        session_state.add_message(assistant_entry, to_provider=False)
        session_state.add_transcript_entry({
            "assistant_with_tool_calls": {
                "content": msg.content,
                "tool_calls_count": len(msg.tool_calls),
                "tool_calls": [tc["function"]["name"] for tc in tool_calls_payload]
            }
        })
        session_state.add_message(provider_assistant_tool_message, to_provider=True)
        executed_results: List[tuple] = []
        failed_at_index = -1
        execution_error: Optional[Dict[str, Any]] = None

        exec_func = build_exec_func(conductor, session_state)
        if parsed_calls:
            executed_results, failed_at_index, execution_error, plan_metadata = execute_agent_calls(
                conductor,
                parsed_calls,
                exec_func,
                session_state,
                transcript_callback=session_state.add_transcript_entry,
                policy_bypass=session_state.get_provider_metadata("replay_mode"),
            )
        else:
            plan_metadata = {
                "strategy": "no_calls",
                "can_run_concurrent": False,
                "max_workers": 0,
                "group_counts": {},
                "group_limits": {},
                "total_calls": 0,
                "executed_calls": 0,
            }
            if current_mode == "plan":
                manager = session_state.get_todo_manager()
                snapshot = manager.snapshot() if manager else None
                todos = []
                if isinstance(snapshot, dict):
                    todos = snapshot.get("todos") or []
                if not todos:
                    warning = (
                        "<VALIDATION_ERROR>\n"
                        "Plan mode requires creating at least one todo via `todo.create` before continuing.\n"
                        "</VALIDATION_ERROR>"
                    )
                    session_state.increment_guardrail_counter("todo_plan_violation")
                    session_state.add_message({"role": "user", "content": warning}, to_provider=True)
                    try:
                        markdown_logger.log_user_message(warning)
                    except Exception:
                        pass
                    try:
                        session_state.add_transcript_entry({
                            "todo_guard": {
                                "function": "todo.create",
                                "reason": "no_todos_created_in_plan_mode",
                            }
                        })
                    except Exception:
                        pass
                    return False

        turn_ctx.plan_metadata = plan_metadata
        turn_index = session_state.get_provider_metadata("current_turn_index")
        turn_index_int = turn_index if isinstance(turn_index, int) else None
        session_state.add_transcript_entry({"tool_execution_plan": plan_metadata})
        try:
            conductor.provider_metrics.add_concurrency_sample(
                turn=turn_index_int,
                plan=plan_metadata,
            )
        except Exception:
            pass

        record_replay_tool_output_mismatches(
            conductor,
            session_state,
            executed_results,
            model=model,
        )

        recent_tools_summary, test_success = summarize_execution_results(
            conductor,
            turn_ctx,
            executed_results,
            session_state,
            turn_index_int,
        )
        turn_ctx.recent_tools_summary = recent_tools_summary
        turn_ctx.test_success = test_success
        try:
            session_state.set_provider_metadata(
                "recent_tool_activity",
                {
                    "tools": recent_tools_summary,
                    "turn": session_state.get_provider_metadata("current_turn_index"),
                },
            )
        except Exception:
            pass

        if _maybe_force_post_write_auto_verification_closure(
            conductor,
            session_state,
            reason="post_write_auto_verified_before_continuation",
        ):
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return True

        if _maybe_force_requested_shell_command_closure(
            session_state,
            reason="requested_shell_command_observed_before_continuation",
        ):
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return True

        if _force_failed_verification_final_answer(
            session_state,
            reason="failed_verification_after_retries",
        ):
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return True

        if _force_failed_write_final_answer(
            conductor,
            session_state,
            reason="failed_requested_write_after_retries",
        ):
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return True

        if execution_error:
            try:
                use_responses_api = (
                    str(session_state.get_provider_metadata("api_variant") or "").lower() == "responses"
                )
            except Exception:
                use_responses_api = False
            if use_responses_api and executed_results:
                for parsed_result_call, parsed_result_out in executed_results:
                    call_id = getattr(parsed_result_call, "call_id", None)
                    tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                        getattr(parsed_result_call, "function", ""),
                        parsed_result_out,
                        syntax_type="openai",
                        call_id=call_id,
                    )
                    session_state.add_message(tool_result_entry, to_provider=True)
            if execution_error.get("validation_failed"):
                session_state.increment_guardrail_counter("validation_errors")
                error_msg = error_handler.handle_validation_error(execution_error)
            elif execution_error.get("constraint_violation"):
                error_msg = error_handler.handle_constraint_violation(execution_error["error"])
            else:
                error_msg = f"<EXECUTION_ERROR>\n{execution_error['error']}\n</EXECUTION_ERROR>"

            session_state.add_message({"role": "user", "content": error_msg}, to_provider=True)
            markdown_logger.log_user_message(error_msg)
            if stream_responses:
                print(f"[error] {execution_error.get('error', 'Unknown error')}")
            try:
                if turn_index_int is not None:
                    if execution_error.get("validation_failed"):
                        session_state.add_reward_metric(turn_index_int, "SVS", 0.0)
                    session_state.add_reward_metric(turn_index_int, "CPS", 0.0)
                    conductor._record_lsp_reward_metrics(session_state, turn_index_int)
            except Exception:
                pass
            finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
            return False

        results: List[Dict[str, Any]] = []
        turn_index_hint = turn_index_int
        for parsed, tool_result in executed_results:
            tool_result_dict = tool_result if isinstance(tool_result, dict) else {}
            results.append({
                "fn": getattr(parsed, "function", ""),
                "provider_fn": getattr(parsed, "provider_name", getattr(parsed, "function", "")),
                "out": tool_result,
                "args": getattr(parsed, "arguments", {}),
                "call_id": getattr(parsed, "call_id", None),
                "failed": conductor.agent_executor.is_tool_failure(getattr(parsed, "function", ""), tool_result_dict),
            })

        guard_blocked = False
        for idx in range(len(results) - 1, -1, -1):
            current = results[idx]
            current_fn = str(current.get("fn") or "")
            current_out = current.get("out") if isinstance(current.get("out"), dict) else {}
            if _is_completion_action_result(current_fn, current_out):
                guard_ok, guard_reason = conductor._completion_guard_check(session_state)
                if guard_ok:
                    continue
                guard_blocked = True
                session_state.increment_guardrail_counter("completion_guard_blocks")
                abort = conductor._emit_completion_guard_feedback(
                    session_state,
                    markdown_logger,
                    guard_reason or f"Completion guard blocked {current_fn or 'tool completion'}",
                    stream_responses,
                )
                summary = session_state.tool_usage_summary
                summary["total_calls"] = max(0, int(summary.get("total_calls", 0)) - 1)
                if isinstance(turn_index_hint, int):
                    turn_usage = session_state.turn_tool_usage.get(turn_index_hint)
                    if turn_usage and isinstance(turn_usage.get("tools"), list):
                        tools_list = turn_usage["tools"]
                        for tool_entry_idx in range(len(tools_list) - 1, -1, -1):
                            if tools_list[tool_entry_idx].get("name") == current_fn:
                                tools_list.pop(tool_entry_idx)
                                break
                recent_tools_summary = [
                    entry for entry in recent_tools_summary
                    if entry.get("name") != current_fn
                ]
                results.pop(idx)
                if abort:
                    finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
                    return True
                break

        try:
            if turn_index_int is not None:
                session_state.add_reward_metric(turn_index_int, "SVS", 1.0)
                if plan_metadata.get("total_calls"):
                    executed_calls = plan_metadata.get("executed_calls", 0)
                    total_calls = plan_metadata.get("total_calls", 0)
                    cps_value = 1.0 if executed_calls == total_calls else 0.0
                    session_state.add_reward_metric(turn_index_int, "CPS", cps_value)
                if executed_results:
                    acs_value = 1.0 if failed_at_index == -1 else 0.0
                    session_state.add_reward_metric(turn_index_int, "ACS", acs_value)
        except Exception:
            pass
        if turn_index_int is not None:
            conductor._record_lsp_reward_metrics(session_state, turn_index_int)
            conductor._record_test_reward_metric(session_state, turn_index_int, test_success)
        finalize_turn_context_snapshot(conductor, session_state, turn_ctx, turn_index_int)
        if turn_ctx.blocked_calls:
            for blocked in turn_ctx.blocked_calls:
                if blocked.get("source") != "todo":
                    continue
                entry = conductor._emit_todo_guard_violation(
                    session_state,
                    markdown_logger,
                    blocked.get("reason") or "Plan guard violation",
                    blocked_call=blocked.get("call"),
                )
                if entry:
                    results.append(entry)

        flow_strategy = turn_cfg.get("flow", "assistant_continuation").lower()

        try:
            if conductor.logger_v2.run_dir and results:
                persist_turn = len(session_state.transcript) + 1
                persistable = []
                for r in results:
                    persistable.append({
                        "fn": r["fn"],
                        "provider_fn": r.get("provider_fn", r["fn"]),
                        "call_id": r.get("call_id"),
                        "args": r.get("args"),
                        "out": r.get("out"),
                    })
                conductor.provider_logger.save_tool_results(persist_turn, persistable)
                short = "\n".join([f"- {r.get('provider_fn', r['fn'])} (id={r.get('call_id')})" for r in results])
                conductor.logger_v2.append_text("conversation/conversation.md", conductor.md_writer.provider_tool_results(short, f"provider_native/tool_results/turn_{persist_turn}.json"))
        except Exception:
            pass

        for result_entry in results:
            tool_name = str(result_entry.get("fn") or "")
            tool_out = result_entry.get("out") if isinstance(result_entry.get("out"), dict) else {}
            if not _is_completion_action_result(tool_name, tool_out):
                continue

            if not getattr(session_state, "completion_summary", None):
                session_state.completion_summary = {
                    "completed": True,
                    "method": "tool_completion_action",
                    "reason": tool_name or "tool_completion_action",
                    "confidence": 1.0,
                    "tool": tool_name,
                    "tool_result": tool_out,
                    "source": "tool_call",
                }
            else:
                session_state.completion_summary.setdefault("completed", True)
                session_state.completion_summary.setdefault("reason", tool_name or "tool_completion_action")
                session_state.completion_summary.setdefault("method", "tool_completion_action")

            if stream_responses:
                print(f"[stop] reason=tool_based confidence=1.0 - {tool_name}() completed")
            return True

        if flow_strategy == "assistant_continuation":
            try:
                use_responses_api = (
                    str(session_state.get_provider_metadata("api_variant") or "").lower() == "responses"
                )
            except Exception:
                use_responses_api = False
            all_results_text = []
            for r in results:
                formatted_output = conductor.message_formatter.format_tool_output(r["fn"], r["out"], r["args"])
                call_id = r.get("call_id")

                tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                    r["fn"], r["out"], syntax_type="openai", call_id=call_id
                )
                session_state.add_message(tool_result_entry, to_provider=use_responses_api)
                all_results_text.append(formatted_output)

            continuation_content = f"\n\nTool execution results:\n" + "\n\n".join(all_results_text)
            assistant_continuation = {
                "role": "assistant",
                "content": continuation_content
            }
            session_state.add_message(assistant_continuation, to_provider=not use_responses_api)
            markdown_logger.log_assistant_message(continuation_content)
        else:
            for r in results:
                formatted_output = conductor.message_formatter.format_tool_output(r["fn"], r["out"], r["args"])
                call_id = r.get("call_id")

                tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                    r["fn"], r["out"], syntax_type="openai", call_id=call_id
                )
                session_state.add_message(tool_result_entry, to_provider=False)

                if relay_strategy == "tool_role" and call_id:
                    provider_id = provider_router.parse_model_id(model)[0]
                    adapter = provider_adapter_manager.get_adapter(provider_id)
                    tool_result_msg = adapter.create_tool_result_message(call_id, r.get("provider_fn", r["fn"]), r["out"])
                    tool_messages_to_relay.append(tool_result_msg)
                else:
                    session_state.add_message({"role": "user", "content": formatted_output}, to_provider=True)

            if tool_messages_to_relay:
                session_state.provider_messages.extend(tool_messages_to_relay)

        maybe_transition_plan_mode(conductor, session_state, markdown_logger)
        return False
    except ReplayToolOutputMismatchError:
        raise
    except Exception:
        try:
            if tool_messages_to_relay:
                fallback_blob = "\n\n".join([m.get("content", "") for m in tool_messages_to_relay])
                session_state.add_message({"role": "user", "content": fallback_blob}, to_provider=True)
        except Exception:
            pass
        return False

def retry_with_fallback(
    conductor: ConductorContext,
    runtime,
    client,
    model: str,
    messages: List[Dict[str, Any]],
    tools_schema: Optional[List[Dict[str, Any]]],
    runtime_context,
    *,
    stream_responses: bool,
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    attempted: List[Tuple[str, bool, Optional[str]]],
    last_error: Optional[ProviderRuntimeError],
    provider_router_override=None,
    provider_registry_override=None,
) -> Optional[ProviderResult]:
    active_provider_router = provider_router_override or provider_router
    active_provider_registry = provider_registry_override or provider_registry
    descriptor = getattr(runtime, "descriptor", None)
    provider_id = getattr(descriptor, "provider_id", None)
    runtime_id = getattr(descriptor, "runtime_id", None)

    def _record_degraded(route_model: str, reason: str) -> None:
        try:
            degraded = session_state.get_provider_metadata("degraded_routes", {})
            if not isinstance(degraded, dict):
                degraded = {}
            info = degraded.get(route_model, {})
            history = info.get("history", [])
            if not isinstance(history, list):
                history = [history] if history else []
            history.append(
                {
                    "reason": reason,
                    "provider": provider_id,
                    "runtime": runtime_id,
                    "turn": session_state.get_provider_metadata("current_turn_index"),
                }
            )
            info.update({
                "reason": reason,
                "provider": provider_id,
                "runtime": runtime_id,
                "history": history,
            })
            degraded[route_model] = info
            session_state.set_provider_metadata("degraded_routes", degraded)
        except Exception:
            pass

    def _sleep_with_jitter(base: float) -> None:
        jitter = base * 0.25
        wait_time = base + random.uniform(-jitter, jitter)
        if wait_time < 0:
            wait_time = base
        try:
            time.sleep(wait_time)
        except Exception:
            pass

    def _simplify_result(result: ProviderResult) -> ProviderResult:
        return result

    def _invoke(target_model: str) -> ProviderResult:
        return runtime.invoke(
            client=client,
            model=target_model,
            messages=messages,
            tools=tools_schema,
            stream=stream_responses,
            context=runtime_context,
        )

    def _log_retry(route_model: str, reason: str, attempt: str) -> None:
        message = (
            f"[provider-retry] route={route_model} attempt={attempt} reason={reason}"
        )
        try:
            markdown_logger.log_system_message(message)
        except Exception:
            pass
        try:
            if getattr(conductor.logger_v2, "run_dir", None):
                conductor.logger_v2.append_text(
                    "conversation/conversation.md",
                    conductor.md_writer.system(message),
                )
        except Exception:
            pass
        try:
            session_state.add_transcript_entry({
                "provider_retry": {
                    "route": route_model,
                    "attempt": attempt,
                    "reason": reason,
                }
            })
        except Exception:
            pass

    if last_error and not attempted:
        conductor.route_health.record_failure(model, str(last_error))
        conductor._update_health_metadata(session_state)

    same_route_reason = str(last_error) if last_error else "retry"
    backoff_seconds = 0.6
    _log_retry(model, same_route_reason, "retry")
    _sleep_with_jitter(backoff_seconds)

    try:
        result = _invoke(model)
        attempted.append((model, stream_responses, None))
        conductor.route_health.record_success(model)
        conductor._update_health_metadata(session_state)
        return _simplify_result(result)
    except ProviderRuntimeError as retry_error:
        attempted.append((model, stream_responses, str(retry_error) or retry_error.__class__.__name__))
        last_error = retry_error
        conductor.route_health.record_failure(model, str(retry_error) or retry_error.__class__.__name__)
        conductor._update_health_metadata(session_state)

    route_id = None
    if runtime_context and isinstance(runtime_context.extra, dict):
        route_id = runtime_context.extra.get("route_id")
    routing_prefs = conductor._get_model_routing_preferences(route_id)
    explicit_fallbacks = routing_prefs.get("fallback_models") or []
    fallback_model, fallback_diag = conductor._select_fallback_route(
        route_id,
        provider_id,
        model,
        explicit_fallbacks,
    )

    if not fallback_model:
        if last_error:
            raise last_error
        return None

    fallback_reason = str(last_error) if last_error else "fallback"
    _record_degraded(model, fallback_reason)
    _log_retry(fallback_model, fallback_reason, "fallback")
    conductor.provider_metrics.add_fallback(primary=model, fallback=fallback_model, reason=fallback_reason)
    turn_hint = None
    if runtime_context and isinstance(runtime_context.extra, dict):
        turn_hint = runtime_context.extra.get("turn_index")
    conductor._log_routing_event(
        session_state,
        markdown_logger,
        turn_index=turn_hint,
        tag="fallback_route",
        message=(
            f"[routing] Selected fallback route '{fallback_model}' after '{fallback_reason}'."
        ),
        payload={
            "from": route_id or model,
            "reason": fallback_reason,
            "diagnostics": fallback_diag,
        },
    )

    try:
        fallback_runtime_descriptor, fallback_model_resolved = active_provider_router.get_runtime_descriptor(
            fallback_model
        )
        fallback_runtime = active_provider_registry.create_runtime(fallback_runtime_descriptor)
        fallback_client_config = active_provider_router.create_client_config(fallback_model)
        fallback_runtime_context = ProviderRuntimeContext(
            session_state=runtime_context.session_state,
            agent_config=runtime_context.agent_config,
            stream=False,
            extra=dict(runtime_context.extra or {}, fallback_of=model, route_id=fallback_model),
        )
        fallback_client = fallback_runtime.create_client(
            fallback_client_config["api_key"],
            base_url=fallback_client_config.get("base_url"),
            default_headers=fallback_client_config.get("default_headers"),
        )
        try:
            if getattr(conductor.logger_v2, "include_structured_requests", True):
                turn_idx = runtime_context.extra.get("turn_index") if runtime_context.extra else None
                if turn_idx is not None:
                    try:
                        turn_for_record = int(turn_idx)
                    except Exception:
                        turn_for_record = None
                else:
                    turn_for_record = None
                if turn_for_record is not None:
                    headers_snapshot = dict(fallback_client_config.get("default_headers") or {})
                    if getattr(fallback_runtime_descriptor, "provider_id", None) == "openrouter":
                        headers_snapshot.setdefault("Accept", "application/json; charset=utf-8")
                        headers_snapshot.setdefault("Accept-Encoding", "identity")
                    conductor.structured_request_recorder.record_request(
                        turn_for_record,
                        provider_id=fallback_runtime_descriptor.provider_id,
                        runtime_id=fallback_runtime_descriptor.runtime_id,
                        model=fallback_model_resolved,
                        request_headers=headers_snapshot,
                        request_body={
                            "model": fallback_model_resolved,
                            "messages": messages,
                            "tools": tools_schema,
                            "stream": False,
                        },
                        stream=False,
                        tool_count=len(tools_schema or []),
                        endpoint=fallback_client_config.get("base_url"),
                        attempt=len(attempted),
                        extra={"fallback_of": model},
                    )
        except Exception:
            pass
        start_ts = time.time()
        result = fallback_runtime.invoke(
            client=fallback_client,
            model=fallback_model_resolved,
            messages=messages,
            tools=tools_schema,
            stream=False,
            context=fallback_runtime_context,
        )
        elapsed = time.time() - start_ts
        try:
            conductor.provider_metrics.add_call(
                fallback_model_resolved,
                stream=False,
                elapsed=elapsed,
                outcome="success",
            )
        except Exception:
            pass
        session_state.set_provider_metadata("fallback_route", {
            "from": model,
            "to": fallback_model,
            "provider": fallback_runtime_descriptor.provider_id,
        })
        conductor.route_health.record_success(fallback_model)
        conductor._update_health_metadata(session_state)
        return result
    except Exception as exc:
        elapsed = time.time() - start_ts if 'start_ts' in locals() else 0.0
        try:
            conductor.provider_metrics.add_call(
                fallback_model,
                stream=False,
                elapsed=elapsed,
                outcome="error",
                error_reason=str(exc),
            )
        except Exception:
            pass
        attempted.append((fallback_model, False, str(exc) or exc.__class__.__name__))
        conductor.route_health.record_failure(fallback_model, str(exc) or exc.__class__.__name__)
        conductor._update_health_metadata(session_state)
        if last_error:
            raise last_error
        raise


__all__ = ['log_provider_message', 'process_model_output', 'handle_text_tool_calls', 'handle_native_tool_calls', 'retry_with_fallback']
