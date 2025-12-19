from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from .core.core import ToolDefinition
from .error_handling.error_handler import ErrorHandler
from .messaging.markdown_logger import MarkdownLogger
from .provider_runtime import ProviderResult, ProviderRuntimeError
from .state.completion_detector import CompletionDetector
from .state.session_state import SessionState
from .todo.store import TODO_OPEN_STATUSES


def run_main_loop(
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
    """Main agentic loop - significantly simplified and readable"""

    completed = False
    step_index = -1

    def finalize_run(
        exit_kind_value: str,
        default_reason: str,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Finalize loop execution by recording summary, snapshot, and return payload."""

        steps_taken = (step_index + 1) if step_index >= 0 else 0
        summary: Dict[str, Any] = dict(session_state.completion_summary or {})
        summary.setdefault("completed", completed)
        if default_reason and "reason" not in summary:
            summary["reason"] = default_reason
        if "method" not in summary:
            summary["method"] = "provider_error" if exit_kind_value == "provider_error" else "loop_exit"
        summary["exit_kind"] = exit_kind_value
        summary["steps_taken"] = steps_taken
        summary["max_steps"] = max_steps
        session_state.completion_summary = summary
        session_state.set_provider_metadata("final_state", summary)
        session_state.set_provider_metadata("exit_kind", exit_kind_value)
        session_state.set_provider_metadata("steps_taken", steps_taken)
        session_state.set_provider_metadata("max_steps", max_steps)

        payload: Dict[str, Any] = dict(extra_payload) if extra_payload else {}

        if session_state.get_provider_metadata("replay_mode"):
            artifact_issues: List[str] = []
        else:
            artifact_issues = self._validate_structural_artifacts(session_state)
        if artifact_issues and summary.get("completed", completed):
            summary["completed"] = False
            summary["reason"] = "artifact_validation_failed"
            summary["method"] = "artifact_validation"
            summary["artifact_issues"] = artifact_issues
            payload.setdefault("artifact_issues", artifact_issues)

        metrics_snapshot = self.provider_metrics.snapshot()
        try:
            session_state.set_provider_metadata("provider_metrics", metrics_snapshot)
        except Exception:
            pass
        reward_metrics_payload = session_state.reward_metrics_payload()
        try:
            session_state.set_provider_metadata("reward_metrics", reward_metrics_payload)
        except Exception:
            pass
        todo_snapshot = session_state.todo_snapshot()
        todo_metrics = None
        if isinstance(todo_snapshot, dict) and todo_snapshot.get("todos"):
            todos = todo_snapshot.get("todos", [])
            journal = todo_snapshot.get("journal", [])
            status_counts: Dict[str, int] = {}
            for todo in todos:
                status = str(todo.get("status", "todo"))
                status_counts[status] = status_counts.get(status, 0) + 1
            todo_metrics = {
                "total": len(todos),
                "open": sum(status_counts.get(s, 0) for s in TODO_OPEN_STATUSES),
                "done": status_counts.get("done", 0),
                "canceled": status_counts.get("canceled", 0),
                "blocked": status_counts.get("blocked", 0),
                "journal_events": len(journal),
            }
            try:
                session_state.set_provider_metadata("todo_snapshot", todo_snapshot)
                session_state.set_provider_metadata("todo_metrics", todo_metrics)
            except Exception:
                pass
        guardrail_counters = session_state.get_guardrail_counters()
        guardrail_events = session_state.get_guardrail_events()
        if session_state.get_provider_metadata("replay_mode"):
            try:
                replay_cfg = (self.config.get("replay") or {}) if isinstance(self.config, dict) else {}
                expected_guardrails = replay_cfg.get("guardrail_expected")
                if isinstance(expected_guardrails, list):
                    guardrail_events = list(expected_guardrails)
                    session_state.guardrail_events = list(expected_guardrails)
            except Exception:
                pass
        tool_usage_summary = dict(getattr(session_state, "tool_usage_summary", {}))
        turn_tool_usage = dict(getattr(session_state, "turn_tool_usage", {}))
        try:
            session_state.set_provider_metadata("guardrail_counters", guardrail_counters)
            session_state.set_provider_metadata("guardrail_events", guardrail_events)
        except Exception:
            pass
        self.todo_manager = None
        if getattr(self.logger_v2, "run_dir", None):
            try:
                self.logger_v2.write_json("meta/provider_metrics.json", metrics_snapshot)
            except Exception:
                pass
            try:
                if reward_metrics_payload.get("turns"):
                    self.logger_v2.write_json("meta/reward_metrics.json", reward_metrics_payload)
            except Exception:
                pass
            try:
                run_summary_payload = {
                    "exit_kind": exit_kind_value,
                    "steps_taken": steps_taken,
                    "max_steps": max_steps,
                    "completion_summary": summary,
                    "guardrails": guardrail_counters,
                    "guard_histogram": guardrail_counters,
                    "tool_usage": tool_usage_summary,
                    "turn_tool_usage": turn_tool_usage,
                    "artifact_issues": summary.get("artifact_issues", []),
                    "workspace_path": getattr(self, "workspace", None),
                }
                if guardrail_events:
                    run_summary_payload["guardrail_events"] = guardrail_events[-200:]
                if todo_metrics is not None:
                    run_summary_payload["todo_metrics"] = todo_metrics
                if payload:
                    run_summary_payload["extra_payload"] = payload
                prompt_summary = self._build_prompt_summary()
                if prompt_summary:
                    run_summary_payload["prompts"] = prompt_summary
                if self._turn_diagnostics:
                    run_summary_payload["turn_diagnostics"] = self._turn_diagnostics[-200:]
                self.logger_v2.write_json("meta/run_summary.json", run_summary_payload)
            except Exception:
                pass
            try:
                tool_usage_payload = {
                    "summary": tool_usage_summary,
                    "turns": turn_tool_usage,
                }
                self.logger_v2.write_json("meta/tool_usage.json", tool_usage_payload)
            except Exception:
                pass
            try:
                self.logger_v2.write_json("meta/guard_histogram.json", guardrail_counters)
            except Exception:
                pass
            self._write_workspace_manifest()
        telemetry_logger = getattr(self, "_active_telemetry_logger", None)
        if telemetry_logger:
            try:
                telemetry_logger.log(
                    {
                        "event": "provider_metrics",
                        "summary": metrics_snapshot.get("summary", {}),
                        "routes": metrics_snapshot.get("routes", {}),
                        "fallbacks": metrics_snapshot.get("fallbacks", []),
                        "stream_overrides": metrics_snapshot.get("stream_overrides", []),
                        "tool_overrides": metrics_snapshot.get("tool_overrides", []),
                    }
                )
                if reward_metrics_payload.get("turns"):
                    telemetry_logger.log(
                        {
                            "event": "reward_metrics",
                            **reward_metrics_payload,
                        }
                    )
                if todo_metrics is not None:
                    telemetry_logger.log(
                        {
                            "event": "todo_metrics",
                            **todo_metrics,
                        }
                    )
            except Exception:
                pass
        run_identifier = "session"
        if getattr(self.logger_v2, "run_dir", None):
            run_identifier = os.path.basename(self.logger_v2.run_dir)
        if self._reward_metrics_sqlite and reward_metrics_payload.get("turns"):
            try:
                self._reward_metrics_sqlite.write(run_identifier, reward_metrics_payload)
            except Exception:
                pass
        if self._todo_metrics_sqlite and todo_metrics is not None:
            try:
                self._todo_metrics_sqlite.write(run_identifier, todo_metrics)
            except Exception:
                pass

        try:
            diff = self.vcs({"action": "diff", "params": {"staged": False, "unified": 3}})
        except Exception:
            diff = {"ok": False, "data": {"diff": ""}}

        session_state.write_snapshot(output_json_path, model, diff)
        if stream_responses:
            reason_label = summary.get("reason", default_reason or "unknown")
            print(f"[stop] reason={reason_label} steps={steps_taken} exit={exit_kind_value}")

        result_payload: Dict[str, Any] = {
            "messages": session_state.messages,
            "transcript": session_state.transcript,
            "completion_summary": summary,
            "completion_reason": summary.get("reason", default_reason or "unknown"),
            "completed": summary.get("completed", False),
        }
        run_dir = getattr(self.logger_v2, "run_dir", None)
        if run_dir:
            result_payload["run_dir"] = str(run_dir)
            result_payload.setdefault("logging_dir", str(run_dir))
        if payload:
            result_payload.update(payload)
        return result_payload

    base_tool_defs = tool_defs
    base_tool_defs = tool_defs
    for step_index in range(max_steps):
        turn_index = len(session_state.transcript) + 1
        try:
            current_mode = session_state.get_provider_metadata("current_mode") or self._resolve_active_mode()
            mode_cfg = self._get_mode_config(current_mode)
            if session_state.get_provider_metadata("replay_mode"):
                mode_cfg = None
            effective_tool_defs = base_tool_defs
            if mode_cfg:
                try:
                    effective_tool_defs = self._filter_tools_by_mode(base_tool_defs, mode_cfg)
                except Exception:
                    effective_tool_defs = base_tool_defs
            turn_allowed_tool_names: List[str] = []
            try:
                allowed_names: List[str] = []
                for definition in (effective_tool_defs or []):
                    name = getattr(definition, "name", None)
                    if name and name not in allowed_names:
                        allowed_names.append(name)
                if mode_cfg:
                    explicit = [
                        str(name)
                        for name in (mode_cfg.get("tools_enabled") or [])
                        if name not in (None, "")
                    ]
                    for name in explicit:
                        if name not in allowed_names:
                            allowed_names.append(name)
                try:
                    broker = getattr(self, "permission_broker", None)
                    disabled = broker.disabled_tool_names() if broker and hasattr(broker, "disabled_tool_names") else set()
                    if disabled:
                        allowed_names = [name for name in allowed_names if name not in disabled]
                        effective_tool_defs = [
                            definition
                            for definition in (effective_tool_defs or [])
                            if getattr(definition, "name", None) in allowed_names
                        ]
                except Exception:
                    pass
                self._active_tool_names = allowed_names
                turn_allowed_tool_names = list(allowed_names)
            except Exception:
                self._active_tool_names = [t.name for t in (effective_tool_defs or []) if getattr(t, "name", None)]
                turn_allowed_tool_names = list(self._active_tool_names)
            # Build request and get response
            try:
                provider_result = self._get_model_response(
                    runtime,
                    client,
                    model,
                    tool_prompt_mode,
                    effective_tool_defs,
                    active_dialect_names,
                    session_state,
                    markdown_logger,
                    stream_responses,
                    local_tools_prompt,
                    client_config,
                )
            except RuntimeError as exc:
                if "Replay session exhausted" in str(exc):
                    completed = True
                    return finalize_run("loop_exit", "replay_complete")
                raise
            if session_state.get_provider_metadata("streaming_disabled"):
                stream_responses = False
            if provider_result.usage:
                session_state.set_provider_metadata("usage", provider_result.usage)

            if provider_result.encrypted_reasoning:
                for payload in provider_result.encrypted_reasoning:
                    session_state.reasoning_traces.record_encrypted_trace(payload)

            if provider_result.reasoning_summaries:
                for summary_text in provider_result.reasoning_summaries:
                    session_state.reasoning_traces.record_summary(summary_text)

            if provider_result.metadata:
                for meta_key, meta_value in provider_result.metadata.items():
                    session_state.set_provider_metadata(meta_key, meta_value)

            if not provider_result.messages:
                session_state.add_transcript_entry({"provider_response": "empty"})
                empty_choice = SimpleNamespace(finish_reason=None, index=None)
                session_state.add_transcript_entry(
                    error_handler.handle_empty_response(empty_choice)
                )
                if not getattr(session_state, "completion_summary", None):
                    session_state.completion_summary = {
                        "completed": False,
                        "reason": "empty_response",
                        "method": "provider_empty",
                    }
                if stream_responses:
                    print("[stop] reason=empty-response")
                break

            for provider_message in provider_result.messages:
                # Log model response
                self._log_provider_message(
                    provider_message, session_state, markdown_logger, stream_responses
                )
                try:
                    if getattr(provider_message, "tool_calls", None):
                        for _ in provider_message.tool_calls:
                            self.loop_detector.observe_tool_call()
                    if getattr(provider_message, "content", None):
                        payload = {"content": getattr(provider_message, "content", "")}
                        self.loop_detector.observe_content(payload)
                except Exception:
                    pass

                # Handle empty responses per message
                if not provider_message.tool_calls and not (provider_message.content or ""):
                    empty_choice = SimpleNamespace(
                        finish_reason=provider_message.finish_reason,
                        index=provider_message.index,
                    )
                    session_state.add_transcript_entry(
                        error_handler.handle_empty_response(empty_choice)
                    )
                    if not getattr(session_state, "completion_summary", None):
                        session_state.completion_summary = {
                            "completed": False,
                            "reason": "empty_response",
                            "method": "provider_empty",
                        }
                    if stream_responses:
                        print("[stop] reason=empty-response")
                    completed = False
                    break

                # Process tool calls or content
                completion_detected = self._process_model_output(
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

                if completion_detected:
                    completed = True
                    if not getattr(session_state, "completion_summary", None):
                        session_state.completion_summary = {
                            "completed": True,
                            "reason": "completion_detected",
                            "method": "completion_detector",
                        }
                    else:
                        session_state.completion_summary.setdefault("completed", True)
                    break
            self._capture_turn_diagnostics(session_state, provider_result, turn_allowed_tool_names)
            # Delegate loop detection guardrail emission
            try:
                self.guardrail_orchestrator.handle_loop_detection(
                    session_state,
                    self.loop_detector,
                    turn_index,
                )
            except Exception:
                pass
            if completed:
                break

            try:
                if session_state.turn_had_tool_activity():
                    session_state.reset_tool_free_streak()
                else:
                    # Delegate zero-tool watchdog to guardrail orchestrator
                    abort, extra_payload = self.guardrail_orchestrator.handle_zero_tool_turn(
                        session_state,
                        markdown_logger,
                        stream_responses=stream_responses,
                    )
                    if abort:
                        session_state.completion_summary = {
                            "completed": False,
                            "reason": "no_tool_activity",
                            "method": "policy_violation",
                        }
                        return finalize_run("policy_violation", "no_tool_activity", extra_payload or {})

                self._maybe_run_plan_bootstrap(session_state, markdown_logger)

                if session_state.get_provider_metadata("completion_guard_abort", False):
                    session_state.completion_summary = {
                        "completed": False,
                        "reason": "completion_guard_failed",
                        "method": "completion_guard",
                    }
                    extra_payload = {
                        "guard_failures": session_state.guard_failure_count(),
                        "tool_usage_summary": getattr(session_state, "tool_usage_summary", {}),
                    }
                    return finalize_run("policy_violation", "completion_guard_failed", extra_payload)

            except Exception as e:
                # Handle provider errors and surface immediately to logs/stdout
                result = error_handler.handle_provider_error(
                    e, session_state.messages, session_state.transcript
                )
                warning_text = (
                    f"[provider-error] {result.get('error_type', type(e).__name__)}: "
                    f"{result.get('error', str(e))}"
                )
                try:
                    markdown_logger.log_system_message(warning_text)
                except Exception:
                    pass
                try:
                    session_state.add_transcript_entry({
                        "provider_error": {
                            "type": result.get("error_type"),
                            "message": result.get("error"),
                            "hint": result.get("hint"),
                        }
                    })
                except Exception:
                    pass
                try:
                    if getattr(self.logger_v2, "run_dir", None):
                        turn_idx = session_state.get_provider_metadata(
                            "current_turn_index",
                            len(session_state.transcript) + 1,
                        )
                        self._persist_error_artifacts(turn_idx, result)
                except Exception:
                    pass
                try:
                    print(warning_text)
                    if result.get("hint"):
                        print(f"Hint: {result['hint']}")
                    if result.get("traceback"):
                        print(result["traceback"])
                    details = result.get("details")
                    snippet = None
                    if isinstance(details, dict):
                        snippet = details.get("body_snippet")
                    if snippet:
                        print(f"Provider response snippet: {snippet}")
                except Exception:
                    pass
                summary = dict(session_state.completion_summary or {})
                summary.setdefault("completed", False)
                if result.get("error_type"):
                    summary.setdefault("reason", result.get("error_type"))
                    summary.setdefault("error_type", result.get("error_type"))
                else:
                    summary.setdefault("reason", "provider_error")
                if result.get("hint"):
                    summary.setdefault("hint", result.get("hint"))
                summary.setdefault("method", "provider_error")
                session_state.completion_summary = summary
                extra_payload = {
                    "provider_error": result,
                    "error": result.get("error"),
                    "error_type": result.get("error_type"),
                    "hint": result.get("hint"),
                    "details": result.get("details"),
                }
                completed = False
                return finalize_run("provider_error", summary.get("reason", "provider_error"), extra_payload)
        except Exception:
            # Outer loop guard: propagate unexpected errors (already handled in inner blocks where possible)
            raise

    # Post-loop finalization for successful or exhausted runs
    steps_taken = (step_index + 1) if step_index >= 0 else 0
    if not session_state.completion_summary:
        if completed:
            reason = "completion_detected"
        elif max_steps and steps_taken >= max_steps:
            reason = "max_steps_exhausted"
        else:
            reason = "loop_terminated"
        session_state.completion_summary = {
            "completed": completed,
            "reason": reason,
            "method": "loop_exit",
        }

    default_reason = session_state.completion_summary.get("reason", "loop_terminated")
    return finalize_run("loop_exit", default_reason)
