from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .messaging.markdown_logger import MarkdownLogger
from .plan_bootstrapper import PlanBootstrapper
from .state.session_state import SessionState
from .todo.store import TODO_OPEN_STATUSES
from .turn_context import TurnContext
from .guardrail_coordinator import GuardrailCoordinator


class GuardrailOrchestrator:
    """
    Orchestrates guardrail behavior for a single run.

    This class centralizes guard logic that was previously spread across
    the conductor, without changing behavior:
    - TODO plan guard (per-call preflight + violations)
    - Workspace/TODO progress guards (per-call preflight)
    - Plan mode transitions
    - Completion guard checks + feedback
    - Plan bootstrap warnings and seeding
    """

    def __init__(
        self,
        config: Dict[str, Any],
        guardrail_coordinator: GuardrailCoordinator,
        plan_bootstrapper: PlanBootstrapper,
        *,
        completion_guard_abort_threshold: int,
        completion_guard_handler: Optional[Any],
        zero_tool_warn_turns: int,
        zero_tool_abort_turns: int,
        zero_tool_warn_message: str,
        zero_tool_abort_message: str,
        zero_tool_emit_event: bool,
    ) -> None:
        self.config = config
        self.guardrail_coordinator = guardrail_coordinator
        self.plan_bootstrapper = plan_bootstrapper
        self.completion_guard_abort_threshold = int(completion_guard_abort_threshold)
        self.completion_guard_handler = completion_guard_handler
        self.zero_tool_warn_turns = int(zero_tool_warn_turns)
        self.zero_tool_abort_turns = int(zero_tool_abort_turns)
        self.zero_tool_warn_message = zero_tool_warn_message
        self.zero_tool_abort_message = zero_tool_abort_message
        self.zero_tool_emit_event = bool(zero_tool_emit_event)

    # ----- Per-call guard preflight helpers -----

    def todo_guard_preflight(
        self,
        session_state: SessionState,
        parsed_call: Any,
        current_mode: Optional[str],
    ) -> Optional[str]:
        return self.guardrail_coordinator.todo_guard_preflight(
            session_state,
            parsed_call,
            current_mode,
        )

    def workspace_context_guard_preflight(
        self,
        session_state: SessionState,
        parsed_call: Any,
        workspace_guard_handler: Optional[Any],
        todo_rate_guard_handler: Optional[Any],
    ) -> Optional[str]:
        return self.guardrail_coordinator.workspace_context_guard_preflight(
            session_state,
            parsed_call,
            workspace_guard_handler,
            todo_rate_guard_handler,
        )

    def todo_progress_guard_preflight(
        self,
        session_state: SessionState,
        parsed_call: Any,
        todo_rate_guard_handler: Optional[Any],
    ) -> Optional[str]:
        return self.guardrail_coordinator.todo_progress_guard_preflight(
            session_state,
            parsed_call,
            todo_rate_guard_handler,
        )

    def apply_turn_guards(
        self,
        turn_ctx: TurnContext,
        session_state: SessionState,
        *,
        workspace_guard_handler: Optional[Any],
        todo_rate_guard_handler: Optional[Any],
    ) -> List[Any]:
        """
        Apply TODO/Workspace/TODO-progress guardrails to parsed calls.

        Returns the filtered list of calls that are allowed to execute.
        """
        filtered: List[Any] = []
        for call in turn_ctx.parsed_calls:
            guard_source = "todo"
            block_reason = self.todo_guard_preflight(session_state, call, turn_ctx.mode)
            if not block_reason:
                guard_source = "workspace"
                block_reason = self.workspace_context_guard_preflight(
                    session_state,
                    call,
                    workspace_guard_handler,
                    todo_rate_guard_handler,
                )
            if not block_reason:
                guard_source = "todo_progress"
                block_reason = self.todo_progress_guard_preflight(
                    session_state,
                    call,
                    todo_rate_guard_handler,
                )
            if block_reason:
                if turn_ctx.replay_mode:
                    filtered.append(call)
                else:
                    turn_ctx.record_block(call, block_reason, guard_source)
            else:
                filtered.append(call)
        return filtered

    # ----- Blocked call handling -----

    def _emit_todo_guard_violation(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        reason: str,
        *,
        blocked_call: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Emit standard validation feedback for plan-mode TODO guard violations."""
        warning = (
            "<VALIDATION_ERROR>\n"
            f"{reason}\n"
            "</VALIDATION_ERROR>"
        )
        try:
            session_state.add_message({"role": "user", "content": warning}, to_provider=True)
        except Exception:
            pass
        try:
            markdown_logger.log_user_message(warning)
        except Exception:
            pass
        try:
            session_state.increment_guardrail_counter("todo_plan_violation")
        except Exception:
            pass
        try:
            session_state.add_transcript_entry({
                "todo_guard": {
                    "function": getattr(blocked_call, "function", "") if blocked_call else "",
                    "reason": reason,
                }
            })
        except Exception:
            pass
        try:
            payload = {
                "reason": reason,
            }
            fn_name = getattr(blocked_call, "function", None) if blocked_call else None
            if fn_name:
                payload["function"] = fn_name
            session_state.record_guardrail_event("todo_plan_violation", payload)
        except Exception:
            pass
        if not blocked_call:
            return None
        return {
            "fn": getattr(blocked_call, "function", ""),
            "provider_fn": getattr(blocked_call, "provider_name", getattr(blocked_call, "function", "")),
            "out": {"error": reason, "validation_failed": True},
            "args": getattr(blocked_call, "arguments", {}),
            "call_id": getattr(blocked_call, "call_id", None),
            "failed": True,
        }

    def handle_blocked_calls(
        self,
        turn_ctx: TurnContext,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> None:
        """Emit appropriate feedback for any blocked calls."""
        if not turn_ctx.blocked_calls:
            return
        for blocked in turn_ctx.blocked_calls:
            guard_source = blocked.get("source")
            reason = blocked.get("reason")
            blocked_call = blocked.get("call")
            if guard_source == "todo" and reason:
                self._emit_todo_guard_violation(
                    session_state,
                    markdown_logger,
                    reason,
                    blocked_call=blocked_call,
                )
                continue
            if guard_source in {"workspace", "todo_progress"} and reason:
                message = str(reason)
                if "<VALIDATION_ERROR>" not in message:
                    message = f"<VALIDATION_ERROR>\n{message}\n</VALIDATION_ERROR>"
                try:
                    session_state.add_message({"role": "user", "content": message}, to_provider=True)
                except Exception:
                    pass
                try:
                    markdown_logger.log_user_message(message)
                except Exception:
                    pass
                try:
                    counter_key = "workspace_guard_violation" if guard_source == "workspace" else "todo_progress_violation"
                    session_state.increment_guardrail_counter(counter_key)
                except Exception:
                    pass
                try:
                    session_state.add_transcript_entry({
                        "guardrail_block": {
                            "source": guard_source,
                            "function": getattr(blocked_call, "function", "") if blocked_call else "",
                            "reason": message,
                        }
                    })
                except Exception:
                    pass
                try:
                    payload = {"reason": message}
                    fn_name = getattr(blocked_call, "function", None) if blocked_call else None
                    if fn_name:
                        payload["function"] = fn_name
                    session_state.record_guardrail_event(f"{guard_source}_block", payload)
                except Exception:
                    pass

    # ----- Plan mode transitions -----

    def maybe_transition_plan_mode(
        self,
        session_state: SessionState,
        markdown_logger: Optional[MarkdownLogger],
        *,
        workspace_guard_handler: Optional[Any],
        todo_rate_guard_handler: Optional[Any],
    ) -> None:
        """Advance from plan mode to the next mode once TODO prerequisites are satisfied."""
        current_mode = session_state.get_provider_metadata("current_mode")
        if current_mode != "plan":
            return

        manager = session_state.get_todo_manager()
        snapshot = manager.snapshot() if manager else None
        todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
        if not todos:
            return

        try:
            plan_turns = int(session_state.get_provider_metadata("plan_turns") or 0)
        except Exception:
            plan_turns = 0
        plan_turns += 1
        session_state.set_provider_metadata("plan_turns", plan_turns)

        try:
            plan_limit = int(session_state.get_provider_metadata("plan_turn_limit") or 0)
        except Exception:
            plan_limit = 0

        if plan_limit and plan_turns < plan_limit:
            return

        features_cfg = self.config.setdefault("features", {})
        if features_cfg.get("plan", True):
            features_cfg["plan"] = False
            session_state.set_provider_metadata("plan_mode_disabled", True)
            guard_cfg = self.guardrail_coordinator.workspace_guard_config(
                workspace_guard_handler,
                todo_rate_guard_handler,
            )
            if guard_cfg["require_exploration"] and not session_state.get_provider_metadata("workspace_context_initialized", False):
                session_state.set_provider_metadata("workspace_context_required", True)
            session_state.set_provider_metadata("workspace_pending_todo_update_allowed", True)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            if guard_cfg["require_read"]:
                session_state.set_provider_metadata("workspace_pending_read", True)
            else:
                session_state.set_provider_metadata("workspace_pending_read", False)
            try:
                session_state.add_transcript_entry({
                    "mode_transition": {
                        "from": "plan",
                        "to": "build",
                        "reason": "plan_turn_limit" if plan_limit else "plan_disabled",
                        "plan_turns": plan_turns,
                    }
                })
            except Exception:
                pass
            if markdown_logger:
                try:
                    markdown_logger.log_system_message("[mode] Transitioning from plan mode to build mode.")
                except Exception:
                    pass

        # Resolve next active mode based on config.
        try:
            seq = (self.config.get("loop", {}) or {}).get("sequence") or []
            features = self.config.get("features", {}) or {}
            next_mode: Optional[str] = None
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
                        next_mode = str(then.get("mode"))
                        break
                if step.get("mode"):
                    next_mode = str(step.get("mode"))
                    break
        except Exception:
            next_mode = None
        if not next_mode:
            try:
                modes = self.config.get("modes", []) or []
                if modes and isinstance(modes, list):
                    name = modes[0].get("name")
                    if name:
                        next_mode = str(name)
            except Exception:
                next_mode = None
        session_state.set_provider_metadata("current_mode", next_mode)

    # ----- Plan bootstrap warnings and seeding -----

    def maybe_emit_plan_bootstrap_warning(self, session_state: SessionState, diag: Dict[str, Any]) -> None:
        # Only emit plan/todo bootstrap warnings when the plan bootstrapper is enabled
        # (i.e., explicitly configured via guardrails.plan_bootstrap with a seed spec).
        # Otherwise, these warnings create policy divergences across harnesses/configs.
        if not self.plan_bootstrapper or not self.plan_bootstrapper.is_enabled():
            return
        if session_state.get_provider_metadata("replay_mode"):
            return
        if session_state.get_provider_metadata("plan_mode_disabled"):
            return
        if session_state.get_provider_metadata("todo_seed_completed"):
            return
        # If the workspace already contains a populated TODO board (e.g. preserved workspace),
        # do not nag the model to seed again.
        try:
            manager = session_state.get_todo_manager()
            snapshot = manager.snapshot() if manager else None
            todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
            if todos:
                session_state.set_provider_metadata("todo_seed_completed", True)
                return
        except Exception:
            pass
        if session_state.get_provider_metadata("plan_bootstrap_event_emitted"):
            return
        turn_index = diag.get("turn")
        if not isinstance(turn_index, int):
            return
        warmup_turns = int((self.plan_bootstrapper.config.get("warmup_turns") or 1))
        if turn_index > warmup_turns:
            return
        if diag.get("has_todo_seed"):
            return
        reason = "Plan mode requires creating at least one todo via `todo.create` or `todo.write_board` before continuing."
        session_state.increment_guardrail_counter("plan_todo_bootstrap_missing")
        session_state.record_guardrail_event("plan_todo_bootstrap_missing", {"reason": reason})
        session_state.set_provider_metadata("plan_bootstrap_event_emitted", True)

    def _handle_plan_bootstrap_failure(
        self,
        session_state: SessionState,
        markdown_logger: Optional[MarkdownLogger],
        message: Optional[str],
    ) -> None:
        session_state.increment_guardrail_counter("plan_todo_seed_failed")
        session_state.record_guardrail_event(
            "plan_todo_seed_failed",
            {"reason": message or "unknown"},
        )
        if markdown_logger and message:
            try:
                markdown_logger.log_system_message(f"[plan-bootstrap] Failed to seed TODO board: {message}")
            except Exception:
                pass

    def maybe_run_plan_bootstrap(
        self,
        session_state: SessionState,
        markdown_logger: Optional[MarkdownLogger],
        exec_func,
    ) -> None:
        if not self.plan_bootstrapper or not self.plan_bootstrapper.should_seed(session_state):
            return
        calls = self.plan_bootstrapper.build_calls()
        if not calls:
            return
        try:
            _, _, execution_error, _ = exec_func(
                calls,
                transcript_callback=session_state.add_transcript_entry,
                policy_bypass=session_state.get_provider_metadata("replay_mode"),
            )
        except Exception as exc:  # noqa: BLE001
            self._handle_plan_bootstrap_failure(session_state, markdown_logger, str(exc))
            return
        if execution_error:
            self._handle_plan_bootstrap_failure(session_state, markdown_logger, execution_error.get("error"))
            return
        session_state.set_provider_metadata("todo_seed_completed", True)
        session_state.set_provider_metadata("plan_bootstrap_seeded", True)
        session_state.increment_guardrail_counter("plan_todo_seeded")
        session_state.record_guardrail_event(
            "plan_todo_seeded",
            {"strategy": self.plan_bootstrapper.strategy, "calls": [getattr(call, "function", "") for call in calls]},
        )
        if markdown_logger:
            try:
                markdown_logger.log_system_message("[plan-bootstrap] Seeded TODO board automatically.")
            except Exception:
                pass

    # ----- Completion guard -----

    def completion_guard_check(self, session_state: SessionState) -> Tuple[bool, Optional[str]]:
        """
        Evaluate completion guard conditions based on tool usage and TODO state.
        Mirrors the former _completion_guard_check implementation.
        """
        if session_state.get_provider_metadata("replay_mode"):
            return False, "No tool usage detected yet. Use the available tools (read/diff/bash) to make concrete progress before declaring completion."
        current_mode = session_state.get_provider_metadata("current_mode")
        if current_mode == "plan":
            return False, "No tool usage detected yet. Use the available tools (read/diff/bash) to make concrete progress before declaring completion."

        summary = getattr(session_state, "tool_usage_summary", {})
        total_calls = int(summary.get("total_calls") or 0)
        completion_cfg = (self.config.get("completion", {}) or {})
        allow_zero_tool = bool(completion_cfg.get("allow_zero_tool_completion"))
        if total_calls <= 0 and not allow_zero_tool:
            return False, "No tool usage detected yet. Use the available tools (read/diff/bash) to make concrete progress before declaring completion."

        if session_state.get_provider_metadata("requires_build_guard", False):
            build_guard_cfg = (self.config.get("completion", {}) or {}).get("build_guard", {}) or {}
            require_write = bool(build_guard_cfg.get("require_write", True))
            require_run_shell = bool(build_guard_cfg.get("require_run_shell", True))
            require_successful_tests = bool(build_guard_cfg.get("require_successful_tests", True))
            if int(summary.get("successful_writes") or 0) <= 0:
                if require_write:
                    return False, "No successful code edits recorded. Apply at least one diff or file creation before completing."
            if int(summary.get("run_shell_calls") or 0) <= 0:
                if require_run_shell:
                    return False, "No shell commands executed. Compile or run the test suite before completing."
            test_commands = int(summary.get("test_commands") or 0)
            successful_tests = int(summary.get("successful_tests") or 0)
            if require_successful_tests:
                if test_commands <= 0:
                    return False, "No tests were executed. Run the test suite before completion."
                if successful_tests <= 0:
                    return False, "Tests were attempted but none succeeded. Resolve test failures before completion."

        todos_cfg = self.guardrail_coordinator.todo_config()
        if todos_cfg["enabled"] and todos_cfg["strict"]:
            manager = session_state.get_todo_manager()
            if manager and manager.has_open_items():
                snapshot = manager.snapshot()
                open_titles = [
                    todo.get("title", "unnamed todo")
                    for todo in snapshot.get("todos", [])
                    if todo.get("status") in TODO_OPEN_STATUSES
                ][:3]
                reason = "Outstanding todos must be completed or canceled before finishing."
                if open_titles:
                    reason += " Pending items: " + "; ".join(open_titles)
                return False, reason

        return True, None

    # ----- Zero-tool watchdog & loop detection -----

    def handle_zero_tool_turn(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Handle a turn where no tool activity was observed.

        Returns (abort, extra_payload). When abort is True, the caller should
        finalize the run with exit_kind="policy_violation" and reason
        "no_tool_activity", using the returned payload.
        """
        todos_cfg = self.guardrail_coordinator.todo_config()
        if todos_cfg["enabled"] and todos_cfg["reset_streak_on_todo"] and session_state.turn_had_todo_activity():
            session_state.reset_tool_free_streak()
            return False, None

        streak = session_state.increment_tool_free_streak()
        abort_threshold = self.zero_tool_abort_turns
        warn_threshold = self.zero_tool_warn_turns

        if abort_threshold and streak >= abort_threshold:
            warning = self.zero_tool_abort_message
            session_state.add_message({"role": "user", "content": warning}, to_provider=True)
            try:
                markdown_logger.log_user_message(warning)
            except Exception:
                pass
            if stream_responses:
                print("[guard] aborting due to zero tool usage streak")
            extra_payload = {"zero_tool_streak": streak, "threshold": abort_threshold}
            event_payload = {
                "action": "abort",
                "streak": streak,
                "threshold": abort_threshold,
            }
            if self.zero_tool_emit_event:
                try:
                    session_state.add_transcript_entry({"zero_tool_watchdog": event_payload.copy()})
                except Exception:
                    pass
            try:
                session_state.record_guardrail_event("zero_tool_watchdog", event_payload)
                session_state.increment_guardrail_counter("zero_tool_abort")
            except Exception:
                pass
            return True, extra_payload

        if warn_threshold and streak == warn_threshold:
            warning = self.zero_tool_warn_message
            session_state.add_message({"role": "user", "content": warning}, to_provider=True)
            try:
                markdown_logger.log_user_message(warning)
            except Exception:
                pass
            event_payload = {
                "action": "warn",
                "streak": streak,
                "threshold": warn_threshold,
            }
            if self.zero_tool_emit_event:
                try:
                    session_state.add_transcript_entry({"zero_tool_watchdog": event_payload.copy()})
                except Exception:
                    pass
            try:
                session_state.record_guardrail_event("zero_tool_watchdog", event_payload)
                session_state.increment_guardrail_counter("zero_tool_warnings")
            except Exception:
                pass
            if stream_responses:
                print("[guard] zero-tool warning issued")

        return False, None

    def handle_loop_detection(
        self,
        session_state: SessionState,
        loop_detector: Any,
        turn_index: int,
    ) -> None:
        """
        Check loop detector and emit loop_detection guardrail events when needed.
        """
        # Skip loop detection telemetry when replaying a recorded session to avoid
        # spurious guard events that are not present in the golden trace.
        if session_state.get_provider_metadata("replay_mode"):
            return
        try:
            loop_reason = loop_detector.check()
        except Exception:
            loop_reason = None
        if not loop_reason:
            return
        payload = {"reason": loop_reason, "turn": turn_index}
        try:
            session_state.set_provider_metadata("loop_detection_payload", payload)
            session_state.record_guardrail_event("loop_detection", payload)
            session_state.add_transcript_entry({"loop_detection": payload})
        except Exception:
            pass

    def emit_completion_guard_feedback(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        reason: str,
        stream_responses: bool,
    ) -> bool:
        """
        Emit completion guard feedback and return True if the run should abort.
        Mirrors the former _emit_completion_guard_feedback implementation.
        """
        if session_state.get_provider_metadata("replay_mode"):
            if session_state.get_provider_metadata("completion_guard_emitted"):
                return False
        failure_count = session_state.increment_guard_failures()
        threshold = max(int(self.completion_guard_abort_threshold or 0), 0)
        remaining = max(threshold - failure_count, 0) if threshold else 0
        handler = self.completion_guard_handler
        if handler:
            advisory = handler.render_feedback(reason=reason, remaining=remaining)
        else:
            if remaining > 0:
                advisory = (
                    "<VALIDATION_ERROR>\n"
                    f"{reason}\n"
                    "Completion guard engaged. Provide concrete file edits and successful tests. "
                    f"Warnings remaining before abort: {remaining}.\n"
                    "</VALIDATION_ERROR>"
                )
            else:
                advisory = (
                    "<VALIDATION_ERROR>\n"
                    f"{reason}\n"
                    "Completion guard engaged repeatedly. The run will now terminate to avoid wasting budget.\n"
                    "</VALIDATION_ERROR>"
                )

        session_state.add_message({"role": "user", "content": advisory}, to_provider=True)
        try:
            markdown_logger.log_user_message(advisory)
        except Exception:
            pass
        if stream_responses:
            print(f"[guard] {reason}")

        if threshold <= 0:
            abort = True
        else:
            abort = failure_count >= threshold
        session_state.record_guardrail_event(
            "completion_guard",
            {
                "action": "abort" if abort else "warn",
                "reason": reason,
                "remaining": remaining,
                "warnings_seen": failure_count,
                "threshold": threshold,
            },
        )
        if session_state.get_provider_metadata("replay_mode"):
            session_state.set_provider_metadata("completion_guard_emitted", True)
        if abort:
            session_state.set_provider_metadata("completion_guard_abort", True)
        return abort
