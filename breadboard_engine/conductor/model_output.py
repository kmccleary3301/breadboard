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
    ProviderIdentity,
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    sanitize_provider_result,
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
from .completion_guards import (
    _force_post_receipt_final_answer,
    _force_read_only_observation_final_answer,
    _reject_completion_without_implementation_write,
)
from .execution_records import legacy_message_view
from .implementation_receipts import (
    _async_result_task_id_from_activity,
    _implementation_receipt_missing,
    _implementation_receipts_satisfied,
    _is_allowed_async_result_followup,
    _latest_prompt_requests_tool_stop_after_observation,
    _latest_prompt_requests_read_only_answer_after_observation,
    _required_final_answer_marker,
    _required_final_answer_reminder,
)
from .replay_compare import record_replay_tool_output_mismatches
from .tool_executor import (
    _coordination_task_context,
    _inject_async_result_retrieval,
    _record_validated_signal,
    ToolExecutor,
    build_exec_func,
    execute_agent_calls,
)
from .turn_runtime import AgentRuntime, PreparedProviderExchange, TurnPolicy


def _assistant_history_message(
    msg: Any,
    *,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    history: Dict[str, Any] = {"role": "assistant", "content": msg.content}
    if tool_calls is not None:
        history["tool_calls"] = tool_calls
    annotations = getattr(msg, "annotations", None)
    if isinstance(annotations, dict):
        for field_name in ("reasoning_content", "reasoning", "reasoning_details"):
            field_value = annotations.get(field_name)
            if field_value is not None:
                history[field_name] = field_value
    return history


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
    turn_policy = TurnPolicy.from_config(conductor.config)
    summary = getattr(session_state, "tool_usage_summary", {})
    if (
        turn_policy.allows_zero_tool_completion()
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
            if turn_policy.allows_zero_tool_completion():
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
        assistant_history = _assistant_history_message(msg)
        session_state.add_message(assistant_history, to_provider=False)
        session_state.add_message(dict(assistant_history), to_provider=True)
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
    caller.track_tool_usage(parsed, session_state=session_state)

    assistant_history = _assistant_history_message(msg)
    exchange = PreparedProviderExchange(
        provider_message=msg,
        parsed_calls=list(parsed),
        assistant_message=assistant_history,
        provider_assistant_message=dict(assistant_history),
        model=str(session_state.get_provider_metadata("resolved_model") or ""),
        dialect_selection=tuple(
            session_state.get_provider_metadata("active_dialect_names") or ()
        ),
        input_kind="text",
        transcript_entry={"assistant": msg.content},
    )

    def persist_results(*, executed_results: Any, **_: Any) -> None:
        try:
            if not conductor.logger_v2.run_dir or not executed_results:
                return
            persist_turn = len(session_state.transcript) + 1
            persistable = [
                {
                    "fn": getattr(parsed_call, "function", ""),
                    "provider_fn": getattr(
                        parsed_call,
                        "provider_name",
                        getattr(parsed_call, "function", ""),
                    ),
                    "call_id": getattr(parsed_call, "call_id", f"text_call_{idx}"),
                    "args": getattr(parsed_call, "arguments", {}),
                    "out": call_result,
                }
                for idx, (parsed_call, call_result) in enumerate(executed_results)
            ]
            if persistable:
                conductor.provider_logger.save_tool_results(persist_turn, persistable)
        except Exception:
            pass

    def on_completion(
        *,
        results: Any,
        executed_results: Any,
        failed_at_index: int,
        final_message: Optional[str],
        **_: Any,
    ) -> None:
        del results, final_message
        chunks = conductor.message_formatter.format_execution_results(
            executed_results,
            failed_at_index,
            len(exchange.parsed_calls),
        )
        provider_tool_msg = "\n\n".join(chunks)
        session_state.add_message(
            {"role": "user", "content": provider_tool_msg},
            to_provider=True,
        )
        markdown_logger.log_user_message(provider_tool_msg)

    turn_policy = TurnPolicy.from_config(conductor.config)
    def relay_results(
        *,
        results: Any,
        executed_results: Any,
        failed_at_index: int,
        turn_context: Any,
        **_: Any,
    ) -> None:
        artifact_links: List[str] = []
        try:
            if conductor.logger_v2.run_dir:
                for idx, (parsed_call, call_result) in enumerate(executed_results):
                    rel = conductor.message_formatter.write_tool_result_file(
                        conductor.logger_v2.run_dir,
                        len(session_state.transcript) + 1,
                        idx,
                        parsed_call.function,
                        call_result,
                    )
                    if rel:
                        artifact_links.append(rel)
        except Exception:
            pass
        chunks = conductor.message_formatter.format_execution_results(
            executed_results,
            failed_at_index,
            len(turn_context.parsed_calls),
        )
        for parsed_call, call_result in executed_results:
            result_entry = conductor.message_formatter.create_tool_result_entry(
                parsed_call.function,
                call_result,
                syntax_type="custom-pythonic",
                call_id=getattr(parsed_call, "call_id", None),
            )
            session_state.add_message(result_entry, to_provider=False)
        conductor.turn_relayer.relay_execution_chunks(
            chunks=chunks,
            artifact_links=artifact_links,
            session_state=session_state,
            turn_cfg=turn_policy.turn_strategy,
            markdown_logger=markdown_logger,
        )

    return AgentRuntime(
        conductor=conductor,
        policy=turn_policy,
        tool_executor=ToolExecutor(
            conductor=conductor,
            session_state=session_state,
            exec_func=build_exec_func(conductor, session_state),
            execute_calls=execute_agent_calls,
        ),
        event_sink=session_state.add_transcript_entry,
        log_sink=markdown_logger,
    ).run(
        exchange,
        session_state=session_state,
        error_handler=error_handler,
        stream_responses=stream_responses,
        relay_results=relay_results,
        persist_results=persist_results,
        on_completion=on_completion,
    )

def handle_native_tool_calls(
    conductor: ConductorContext,
    msg,
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    error_handler: Any,
    stream_responses: bool,
    model: str,
) -> bool:
    turn_policy = TurnPolicy.from_config(conductor.config)
    relay_strategy = turn_policy.relay_strategy()

    tool_calls_payload: List[Dict[str, Any]] = []
    for tc in msg.tool_calls:
        fn_name = getattr(getattr(tc, "function", None), "name", None)
        arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
        payload = {
            "id": getattr(tc, "id", None),
            "type": "function",
            "function": {
                "name": fn_name,
                "arguments": arg_str
                if isinstance(arg_str, str)
                else json.dumps(arg_str or {}),
            },
        }
        raw_tool_call = getattr(tc, "raw", None)
        if hasattr(raw_tool_call, "model_dump"):
            try:
                raw_tool_call = raw_tool_call.model_dump(exclude_none=True)
            except Exception:
                raw_tool_call = None
        if isinstance(raw_tool_call, dict):
            for field_name in ("thought_signature", "thoughtSignature", "extra_content"):
                if raw_tool_call.get(field_name) is not None:
                    payload[field_name] = raw_tool_call[field_name]
        tool_calls_payload.append(payload)

    try:
        if conductor.logger_v2.run_dir and tool_calls_payload:
            turn_index = len(session_state.transcript) + 1
            conductor.provider_logger.save_tool_calls(turn_index, tool_calls_payload)
            short = "\n".join(
                f"- {call['function']['name']} (id={call.get('id')})"
                for call in tool_calls_payload
            )
            conductor.logger_v2.append_text(
                "conversation/conversation.md",
                conductor.md_writer.provider_tool_calls(
                    short,
                    f"provider_native/tool_calls/turn_{turn_index}.json",
                ),
            )
    except Exception:
        pass

    enhanced_tool_calls = conductor.message_formatter.create_enhanced_tool_calls(
        tool_calls_payload
    )
    assistant_entry = _assistant_history_message(
        msg,
        tool_calls=enhanced_tool_calls,
    )
    provider_assistant_tool_message = _assistant_history_message(
        msg,
        tool_calls=tool_calls_payload,
    )

    parsed_calls: List[Any] = []
    for tc in msg.tool_calls:
        fn = getattr(getattr(tc, "function", None), "name", None)
        if not fn:
            continue
        arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
        try:
            args = (
                json.loads(arg_str)
                if isinstance(arg_str, str)
                else (arg_str or {})
            )
        except Exception:
            args = {}
        canonical_fn = conductor.agent_executor.canonical_tool_name(fn)
        raw_meta = getattr(tc, "raw", None)
        expected_output = None
        expected_status = None
        expected_metadata = None
        if isinstance(raw_meta, dict):
            expected_output = raw_meta.get("expected_output")
            expected_status = raw_meta.get("expected_status")
            expected_metadata = raw_meta.get("metadata")
        parsed_calls.append(
            SimpleNamespace(
                function=canonical_fn,
                arguments=args,
                provider_name=fn,
                call_id=getattr(tc, "id", None),
                expected_output=expected_output,
                expected_status=expected_status,
                expected_metadata=expected_metadata,
            )
        )

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
        blocked_count = (
            int(
                session_state.get_provider_metadata(
                    "post_required_tool_extra_call_blocks"
                )
                or 0
            )
            + 1
        )
        session_state.set_provider_metadata(
            "post_required_tool_extra_call_blocks",
            blocked_count,
        )
        blocked_tools = [
            str(getattr(call, "function", "") or "") for call in parsed_calls
        ]
        try:
            session_state.add_transcript_entry(
                {
                    "post_required_tool_extra_call_block": {
                        "blocked_tools": blocked_tools,
                        "count": blocked_count,
                    }
                }
            )
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

    current_mode = session_state.get_provider_metadata("current_mode")
    if not parsed_calls and current_mode == "plan":
        manager = session_state.get_todo_manager()
        snapshot = manager.snapshot() if manager else None
        todos = snapshot.get("todos") if isinstance(snapshot, dict) else []
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
            session_state.add_transcript_entry(
                {
                    "todo_guard": {
                        "function": "todo.create",
                        "reason": "no_todos_created_in_plan_mode",
                    }
                }
            )
            return False

    exchange = PreparedProviderExchange(
        provider_message=msg,
        parsed_calls=parsed_calls,
        assistant_message=assistant_entry,
        provider_assistant_message=provider_assistant_tool_message,
        model=model,
        dialect_selection=tuple(
            session_state.get_provider_metadata("active_dialect_names") or ()
        ),
        input_kind="native",
        transcript_entry={
            "assistant_with_tool_calls": {
                "content": msg.content,
                "tool_calls_count": len(msg.tool_calls),
                "tool_calls": [
                    call["function"]["name"] for call in tool_calls_payload
                ],
            }
        },
    )

    def record_execution(
        *,
        executed_results: Any,
        **_: Any,
    ) -> None:
        record_replay_tool_output_mismatches(
            conductor,
            session_state,
            executed_results,
            model=model,
        )

    def persist_results(*, executed_results: Any, **_: Any) -> None:
        try:
            if not conductor.logger_v2.run_dir or not executed_results:
                return
            persist_turn = len(session_state.transcript) + 1
            persistable = [
                {
                    "fn": getattr(parsed_call, "function", ""),
                    "provider_fn": getattr(
                        parsed_call,
                        "provider_name",
                        getattr(parsed_call, "function", ""),
                    ),
                    "call_id": getattr(parsed_call, "call_id", None),
                    "args": getattr(parsed_call, "arguments", {}),
                    "out": call_result,
                }
                for parsed_call, call_result in executed_results
            ]
            conductor.provider_logger.save_tool_results(persist_turn, persistable)
            short = "\n".join(
                f"- {entry['provider_fn']} (id={entry.get('call_id')})"
                for entry in persistable
            )
            conductor.logger_v2.append_text(
                "conversation/conversation.md",
                conductor.md_writer.provider_tool_results(
                    short,
                    f"provider_native/tool_results/turn_{persist_turn}.json",
                ),
            )
        except Exception:
            pass

    def relay_results(
        *,
        results: Any,
        **_: Any,
    ) -> None:
        tool_messages_to_relay: List[Dict[str, Any]] = []
        try:
            flow_strategy = turn_policy.relay_flow()
            if flow_strategy == "assistant_continuation":
                use_responses_api = (
                    str(
                        session_state.get_provider_metadata("api_variant") or ""
                    ).lower()
                    == "responses"
                )
                all_results_text = []
                for entry in results:
                    formatted_output = conductor.message_formatter.format_tool_output(
                        entry["fn"],
                        entry["out"],
                        entry["args"],
                    )
                    tool_result_entry = (
                        conductor.message_formatter.create_tool_result_entry(
                            entry["fn"],
                            entry["out"],
                            syntax_type="openai",
                            call_id=entry.get("call_id"),
                        )
                    )
                    session_state.add_message(
                        tool_result_entry,
                        to_provider=use_responses_api,
                    )
                    all_results_text.append(formatted_output)
                continuation_content = (
                    "\n\nTool execution results:\n"
                    + "\n\n".join(all_results_text)
                )
                session_state.add_message(
                    {"role": "assistant", "content": continuation_content},
                    to_provider=not use_responses_api,
                )
                markdown_logger.log_assistant_message(continuation_content)
                return

            for entry in results:
                formatted_output = conductor.message_formatter.format_tool_output(
                    entry["fn"],
                    entry["out"],
                    entry["args"],
                )
                tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                    entry["fn"],
                    entry["out"],
                    syntax_type="openai",
                    call_id=entry.get("call_id"),
                )
                session_state.add_message(tool_result_entry, to_provider=False)
                call_id = entry.get("call_id")
                if relay_strategy == "tool_role" and call_id:
                    route_hint = getattr(conductor, "_current_route_id", None) or model
                    provider_id = provider_router.parse_model_id(route_hint)[0]
                    adapter = provider_adapter_manager.get_adapter(provider_id)
                    tool_messages_to_relay.append(
                        adapter.create_tool_result_message(
                            call_id,
                            entry.get("provider_fn", entry["fn"]),
                            entry["out"],
                        )
                    )
                else:
                    session_state.add_message(
                        {"role": "user", "content": formatted_output},
                        to_provider=True,
                    )
            if tool_messages_to_relay:
                session_state.provider_messages.extend(tool_messages_to_relay)
        except Exception:
            if tool_messages_to_relay:
                fallback_blob = "\n\n".join(
                    message.get("content", "") for message in tool_messages_to_relay
                )
                session_state.add_message(
                    {"role": "user", "content": fallback_blob},
                    to_provider=True,
                )
    return AgentRuntime(
        conductor=conductor,
        policy=turn_policy,
        tool_executor=ToolExecutor(
            conductor=conductor,
            session_state=session_state,
            exec_func=build_exec_func(conductor, session_state),
            execute_calls=execute_agent_calls,
        ),
        event_sink=session_state.add_transcript_entry,
        log_sink=markdown_logger,
    ).run(
        exchange,
        session_state=session_state,
        error_handler=error_handler,
        stream_responses=stream_responses,
        relay_results=relay_results,
        persist_results=persist_results,
        record_execution=record_execution,
        post_write_closure=True,
    )

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
    client_lease=None,
    route_id: Optional[str] = None,
) -> Optional[ProviderResult]:
    active_provider_router = provider_router_override or provider_router
    active_provider_registry = provider_registry_override or provider_registry
    descriptor = getattr(runtime, "descriptor", None)
    provider_id = getattr(descriptor, "provider_id", None)
    runtime_id = getattr(descriptor, "runtime_id", None)
    def _safe_failure_reason(
        error: Optional[BaseException], default: str = "provider_retry"
    ) -> str:
        if isinstance(error, ProviderRuntimeError):
            return str(error.model_fallback_reason or error.safe_code)
        return default


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
        return sanitize_provider_result(result)

    def _invoke(target_model: str) -> ProviderResult:
        if client_lease is None:
            return sanitize_provider_result(
                runtime.invoke(
                    client=client,
                    model=target_model,
                    messages=messages,
                    tools=tools_schema,
                    stream=stream_responses,
                    context=runtime_context,
                )
            )
        with client_lease(
            route_id or target_model,
            runtime,
        ) as leased_client:
            return sanitize_provider_result(
                runtime.invoke(
                    client=leased_client,
                    model=target_model,
                    messages=messages,
                    tools=tools_schema,
                    stream=stream_responses,
                    context=runtime_context,
                )
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
        conductor.route_health.record_failure(
            model, _safe_failure_reason(last_error)
        )
        conductor._update_health_metadata(session_state)

    retry_same_route = not (
        isinstance(last_error, ProviderRuntimeError)
        and last_error.safe_code == "route_circuit_open"
    )
    if retry_same_route:
        same_route_reason = _safe_failure_reason(last_error)
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
            recorder = getattr(runtime_context, "exchange_recorder", None)
            if retry_error.output_emitted or (
                recorder is not None and recorder.output_emitted
            ):
                raise
            if recorder is not None:
                recorder.reset_unemitted_attempt()
            retry_reason = _safe_failure_reason(retry_error)
            attempted.append((model, stream_responses, retry_reason))
            last_error = retry_error
            conductor.route_health.record_failure(model, retry_reason)
            conductor._update_health_metadata(session_state)

    route_id = None
    if runtime_context and isinstance(runtime_context.extra, dict):
        route_id = runtime_context.extra.get("route_id")
    routing_prefs = conductor._get_model_routing_preferences(route_id)
    explicit_fallbacks = routing_prefs.get("fallback_models") or []
    current_route = str(route_id or model)
    current_error = last_error
    locked_fallbacks = isinstance(
        getattr(conductor, "_model_role_lock", None), dict
    )

    while True:
        fallback_model, fallback_diag = conductor._select_fallback_route(
            current_route,
            provider_id,
            current_route,
            explicit_fallbacks,
            failure_reason=current_error,
        )
        if not fallback_model:
            if current_error:
                raise current_error
            return None

        fallback_reason = (
            current_error.model_fallback_reason
            if isinstance(current_error, ProviderRuntimeError)
            and current_error.model_fallback_reason
            else _safe_failure_reason(current_error, "provider_fallback")
        )
        _record_degraded(current_route, fallback_reason)
        _log_retry(fallback_model, fallback_reason, "fallback")
        conductor.provider_metrics.add_fallback(
            primary=current_route,
            fallback=fallback_model,
            reason=fallback_reason,
        )
        turn_hint = None
        if runtime_context and isinstance(runtime_context.extra, dict):
            turn_hint = runtime_context.extra.get("turn_index")
        conductor._log_routing_event(
            session_state,
            markdown_logger,
            turn_index=turn_hint,
            tag="fallback_route",
            message=(
                f"[routing] Selected fallback route '{fallback_model}' "
                f"after '{fallback_reason}'."
            ),
            payload={
                "from": current_route,
                "reason": fallback_reason,
                "diagnostics": fallback_diag,
            },
        )

        start_ts = time.time()
        try:
            (
                fallback_runtime_descriptor,
                fallback_model_resolved,
            ) = active_provider_router.get_runtime_descriptor(fallback_model)
            fallback_runtime = active_provider_registry.create_runtime(
                fallback_runtime_descriptor
            )
            fallback_identity = ProviderIdentity(
                provider_id=fallback_runtime_descriptor.provider_id,
                runtime_id=fallback_runtime_descriptor.runtime_id,
                route_id=fallback_model,
                model=fallback_model_resolved,
            )
            recorder = getattr(runtime_context, "exchange_recorder", None)
            if recorder is not None:
                recorder.rebind_provider(fallback_identity)
            fallback_client_config = (
                active_provider_router.create_client_config(fallback_model)
            )
            fallback_runtime_context = ProviderRuntimeContext(
                session_state=runtime_context.session_state,
                agent_config=runtime_context.agent_config,
                stream=False,
                extra=dict(
                    runtime_context.extra or {},
                    fallback_of=current_route,
                    route_id=fallback_model,
                ),
                session_id=runtime_context.session_id,
                input_id=runtime_context.input_id,
                turn_id=runtime_context.turn_id,
                exchange_recorder=recorder,
                cancel_requested=runtime_context.cancel_requested,
            )
            try:
                if getattr(
                    conductor.logger_v2,
                    "include_structured_requests",
                    True,
                ):
                    turn_idx = (
                        runtime_context.extra.get("turn_index")
                        if runtime_context.extra
                        else None
                    )
                    try:
                        turn_for_record = (
                            int(turn_idx) if turn_idx is not None else None
                        )
                    except Exception:
                        turn_for_record = None
                    if turn_for_record is not None:
                        headers_snapshot = dict(
                            fallback_client_config.get("default_headers")
                            or {}
                        )
                        if (
                            fallback_runtime_descriptor.provider_id
                            == "openrouter"
                        ):
                            headers_snapshot.setdefault(
                                "Accept",
                                "application/json; charset=utf-8",
                            )
                            headers_snapshot.setdefault(
                                "Accept-Encoding", "identity"
                            )
                        conductor.structured_request_recorder.record_request(
                            turn_for_record,
                            provider_id=(
                                fallback_runtime_descriptor.provider_id
                            ),
                            runtime_id=(
                                fallback_runtime_descriptor.runtime_id
                            ),
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
                            extra={"fallback_of": current_route},
                        )
            except Exception:
                pass
            if client_lease is not None:
                with client_lease(
                    fallback_model, fallback_runtime
                ) as fallback_client:
                    result = sanitize_provider_result(
                        fallback_runtime.invoke(
                            client=fallback_client,
                            model=fallback_model_resolved,
                            messages=messages,
                            tools=tools_schema,
                            stream=False,
                            context=fallback_runtime_context,
                        )
                    )
            else:
                with active_provider_router.execution_client_config(
                    fallback_model,
                    endpoint_id=f"fallback:{fallback_model}",
                ) as fallback_config:
                    fallback_client = fallback_runtime.create_client_from_config(
                        fallback_config
                    )
                    result = sanitize_provider_result(
                        fallback_runtime.invoke(
                            client=fallback_client,
                            model=fallback_model_resolved,
                            messages=messages,
                            tools=tools_schema,
                            stream=False,
                            context=fallback_runtime_context,
                        )
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
            session_state.set_provider_metadata(
                "fallback_route",
                {
                    "from": current_route,
                    "to": fallback_model,
                    "provider": fallback_runtime_descriptor.provider_id,
                    "reason": fallback_reason,
                },
            )
            conductor.route_health.record_success(fallback_model)
            conductor._update_health_metadata(session_state)
            result.metadata = dict(result.metadata or {})
            result.metadata[
                "provider_exchange_identity"
            ] = fallback_identity.as_dict()
            return result
        except ProviderRuntimeError as exc:
            elapsed = time.time() - start_ts
            failure_reason = (
                exc.model_fallback_reason
                or _safe_failure_reason(exc, "provider_fallback_error")
            )
            try:
                conductor.provider_metrics.add_call(
                    fallback_model,
                    stream=False,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason=failure_reason,
                )
            except Exception:
                pass
            attempted.append((fallback_model, False, failure_reason))
            conductor.route_health.record_failure(
                fallback_model, failure_reason
            )
            conductor._update_health_metadata(session_state)
            fallback_recorder = getattr(
                runtime_context, "exchange_recorder", None
            )
            if exc.output_emitted or (
                fallback_recorder is not None
                and fallback_recorder.output_emitted
            ):
                raise
            if fallback_recorder is not None:
                fallback_recorder.reset_unemitted_attempt()
            if not locked_fallbacks:
                raise current_error or exc
            current_route = fallback_model
            current_error = exc
        except Exception:
            elapsed = time.time() - start_ts
            failure_reason = "provider_fallback_error"
            try:
                conductor.provider_metrics.add_call(
                    fallback_model,
                    stream=False,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason=failure_reason,
                )
            except Exception:
                pass
            attempted.append((fallback_model, False, failure_reason))
            conductor.route_health.record_failure(
                fallback_model, failure_reason
            )
            conductor._update_health_metadata(session_state)
            if current_error:
                raise current_error
            raise


__all__ = ['log_provider_message', 'process_model_output', 'handle_text_tool_calls', 'handle_native_tool_calls', 'retry_with_fallback']
