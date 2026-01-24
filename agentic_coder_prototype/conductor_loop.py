from __future__ import annotations

import copy
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
from .surface_snapshot import build_surface_snapshot, record_tool_allowlist_snapshot
from .surface_manifest import build_surface_manifest
from .ctrees.compiler import compile_ctree
from .ctrees.collapse import collapse_ctree
from .ctrees.runner import TreeRunner
from .ctrees.summary import build_ctree_hash_summary
from .ctrees.schema import normalize_ctree_snapshot_summary
from .reward import aggregate_reward_v1, validate_reward_v1
from .policy_pack import PolicyPack


def _call_model_with_hooks(
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
    *,
    turn_index: int,
) -> ProviderResult:
    hook_manager = getattr(self, "hook_manager", None)
    messages_override_key = "__send_messages_override"
    before_payload: Optional[Dict[str, Any]] = None
    if hook_manager is not None:
        try:
            before_payload = {
                "turn": turn_index,
                "model": str(model),
                "tool_prompt_mode": tool_prompt_mode,
                "local_tools_prompt": local_tools_prompt,
                "messages": copy.deepcopy(getattr(session_state, "provider_messages", []) or []),
            }
            before_result = hook_manager.run(
                "before_model",
                before_payload,
                session_state=session_state,
                turn=turn_index,
            )
            if before_result is not None and getattr(before_result, "action", "allow") == "deny":
                reason = getattr(before_result, "reason", None) or "denied_by_hook"
                return ProviderResult(messages=[], raw_response=None, metadata={"denied": True, "reason": reason})
            if (
                before_result is not None
                and getattr(before_result, "action", "") == "transform"
                and isinstance(getattr(before_result, "payload", None), dict)
            ):
                transformed = dict(before_result.payload)
                transformed_model = transformed.get("model")
                if isinstance(transformed_model, str) and transformed_model.strip():
                    model = transformed_model.strip()
                transformed_prompt = transformed.get("local_tools_prompt")
                if isinstance(transformed_prompt, str):
                    local_tools_prompt = transformed_prompt
                transformed_messages = transformed.get("messages")
                if isinstance(transformed_messages, list):
                    session_state.set_provider_metadata(messages_override_key, transformed_messages)
        except Exception:
            pass
    try:
        result = self._get_model_response(
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
    finally:
        try:
            session_state.provider_metadata.pop(messages_override_key, None)
        except Exception:
            pass
    if hook_manager is not None:
        try:
            payload: Dict[str, Any] = {"turn": turn_index, "model": str(model)}
            if result.usage:
                payload["usage"] = result.usage
            hook_manager.run(
                "after_model",
                payload,
                session_state=session_state,
                turn=turn_index,
            )
        except Exception:
            pass
    return result


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
    policy = PolicyPack.from_config(getattr(self, "config", None))

    def poll_control_stop() -> bool:
        """Non-blocking check for a stop/interrupt signal from the CLI bridge."""
        queue = session_state.get_provider_metadata("control_queue")
        if queue is None:
            return False
        get_nowait = getattr(queue, "get_nowait", None)
        if callable(get_nowait):
            try:
                item = get_nowait()
            except Exception:
                return False
        else:
            getter = getattr(queue, "get", None)
            if not callable(getter):
                return False
            try:
                item = getter(timeout=0)
            except Exception:
                return False
        if item is None:
            return True
        if isinstance(item, str):
            return item.strip().lower() in {"stop", "cancel", "interrupt"}
        if isinstance(item, dict):
            if bool(item.get("stop")):
                return True
            kind = str(item.get("kind") or item.get("type") or "").strip().lower()
            return kind in {"stop", "cancel", "interrupt"}
        return False

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
        reward_v1_payload = None
        try:
            reward_cfg = (self.config or {}).get("reward_v1") if isinstance(getattr(self, "config", None), dict) else None
            reward_v1_payload = aggregate_reward_v1(
                reward_metrics_payload,
                completion_summary=summary,
                config=reward_cfg,
                context={
                    "task_type": session_state.get_provider_metadata("task_type") or "general",
                    "latency_budget_ms": session_state.get_provider_metadata("latency_budget_ms"),
                    "initial_tpf": session_state.get_provider_metadata("initial_tpf"),
                    "winrate_vs_baseline": session_state.get_provider_metadata("winrate_vs_baseline"),
                },
            )
            reward_v1_payload["validation"] = validate_reward_v1(reward_v1_payload)
            session_state.set_provider_metadata("reward_v1", reward_v1_payload)
        except Exception:
            reward_v1_payload = None
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
                    if reward_v1_payload:
                        self.logger_v2.write_json("meta/reward_v1.json", reward_v1_payload)
                except Exception:
                    pass
            try:
                try:
                    session_state.record_lifecycle_event(
                        "session_finished",
                        {"exit_kind": exit_kind_value, "steps_taken": steps_taken},
                    )
                except Exception:
                    pass
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
                if reward_v1_payload:
                    run_summary_payload["reward_v1"] = reward_v1_payload
                if guardrail_events:
                    run_summary_payload["guardrail_events"] = guardrail_events[-200:]
                if todo_metrics is not None:
                    run_summary_payload["todo_metrics"] = todo_metrics
                if payload:
                    run_summary_payload["extra_payload"] = payload
                prompt_summary = self._build_prompt_summary()
                if prompt_summary:
                    run_summary_payload["prompts"] = prompt_summary
                try:
                    ctree_store = getattr(session_state, "ctree_store", None)
                    if ctree_store is not None:
                        ctree_cfg = (self.config.get("ctrees") or {}) if isinstance(getattr(self, "config", None), dict) else {}
                        ctrees_snapshot_raw = ctree_store.snapshot()
                        ctrees_snapshot = normalize_ctree_snapshot_summary(ctrees_snapshot_raw) or ctrees_snapshot_raw
                        compiler_cfg = (ctree_cfg.get("compiler") or {}) if isinstance(ctree_cfg, dict) else {}
                        ctrees_compiler = compile_ctree(
                            ctree_store,
                            prompt_summary=prompt_summary,
                            config=compiler_cfg if isinstance(compiler_cfg, dict) else None,
                        )
                        ctrees_collapse = collapse_ctree(ctree_store)
                        session_state.set_provider_metadata("ctrees_snapshot", ctrees_snapshot)
                        session_state.set_provider_metadata("ctrees_compiler", ctrees_compiler)
                        session_state.set_provider_metadata("ctrees_collapse", ctrees_collapse)
                        try:
                            runner_cfg = (ctree_cfg.get("runner") or {}) if isinstance(ctree_cfg, dict) else {}
                            if runner_cfg.get("enabled"):
                                branches = runner_cfg.get("branches") or 2
                                runner = TreeRunner(ctree_store, branches=branches)
                                session_state.set_provider_metadata("ctrees_runner", runner.build_manifest())
                        except Exception:
                            pass
                        try:
                            ctrees_runner = session_state.get_provider_metadata("ctrees_runner")
                            hash_summary = build_ctree_hash_summary(
                                snapshot=ctrees_snapshot if isinstance(ctrees_snapshot, dict) else None,
                                compiler=ctrees_compiler if isinstance(ctrees_compiler, dict) else None,
                                collapse=ctrees_collapse if isinstance(ctrees_collapse, dict) else None,
                                runner=ctrees_runner if isinstance(ctrees_runner, dict) else None,
                            )
                            session_state.set_provider_metadata("ctrees_hash_summary", hash_summary)
                            context_engine = session_state.get_provider_metadata("ctrees_context_engine")
                            session_state.emit_ctree_snapshot(
                                {
                                    "snapshot": ctrees_snapshot,
                                    "compiler": ctrees_compiler,
                                    "collapse": ctrees_collapse,
                                    "runner": ctrees_runner,
                                    "hash_summary": hash_summary,
                                    "context_engine": context_engine if isinstance(context_engine, dict) else None,
                                }
                            )
                        except Exception:
                            pass
                        try:
                            persist_cfg = (ctree_cfg.get("persist") or {}) if isinstance(ctree_cfg, dict) else {}
                            persist_enabled = persist_cfg.get("enabled")
                            if persist_enabled is None:
                                persist_enabled = True
                            if persist_enabled:
                                workspace_root = str(getattr(self, "workspace", "") or "")
                                persist_root = persist_cfg.get("root") or persist_cfg.get("dir")
                                if isinstance(persist_root, str):
                                    persist_root = persist_root.strip()
                                if persist_root and workspace_root and not os.path.isabs(str(persist_root)):
                                    persist_root = os.path.join(workspace_root, str(persist_root))
                                if not persist_root and workspace_root:
                                    persist_root = os.path.join(workspace_root, ".breadboard", "ctrees")
                                if persist_root:
                                    include_raw = bool(persist_cfg.get("include_raw", False))
                                    persist_result = ctree_store.persist(str(persist_root), include_raw=include_raw)
                                    session_state.set_provider_metadata("ctrees_persist", persist_result)
                        except Exception:
                            pass
                except Exception:
                    pass
                surface_snapshot = build_surface_snapshot(self, session_state, prompt_summary=None)
                if surface_snapshot:
                    run_summary_payload["surface_snapshot"] = surface_snapshot
                try:
                    multi_agent_ordering = session_state.get_provider_metadata("multi_agent_ordering")
                    if isinstance(multi_agent_ordering, str) and multi_agent_ordering:
                        run_summary_payload["multi_agent_ordering"] = multi_agent_ordering
                except Exception:
                    pass
                required_surfaces = None
                try:
                    required_surfaces = (self.config or {}).get("surface_manifest_required")
                except Exception:
                    required_surfaces = None
                surface_manifest = build_surface_manifest(
                    prompt_summary=prompt_summary,
                    surface_snapshot=surface_snapshot,
                    required_surfaces=required_surfaces if isinstance(required_surfaces, list) else None,
                )
                if surface_manifest:
                    run_summary_payload["surface_manifest"] = surface_manifest
                if getattr(session_state, "lifecycle_events", None):
                    run_summary_payload["lifecycle_events"] = list(session_state.lifecycle_events)[-500:]
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

        try:
            hook_manager = getattr(self, "hook_manager", None)
            if hook_manager is not None:
                hook_manager.run(
                    "completion",
                    {"summary": dict(summary), "exit_kind": exit_kind_value},
                    session_state=session_state,
                    turn=None,
                )
        except Exception:
            pass

        policy_decisions_summary: Optional[Dict[str, Any]] = None
        try:
            hook_manager = getattr(self, "hook_manager", None)
            decision_log = getattr(hook_manager, "decision_log", None) if hook_manager is not None else None
            if callable(decision_log):
                raw_decisions = decision_log()
                if raw_decisions:
                    from .extensions import merge_policy_decisions

                    merged = merge_policy_decisions(raw_decisions)
                    policy_decisions_summary = {
                        "action": merged.action.value,
                        "counts": {
                            "total": len(merged.ordered),
                            "denies": 1 if merged.deny else 0,
                            "transforms": len(merged.transforms),
                            "warnings": len(merged.warnings),
                            "annotations": len(merged.annotations),
                        },
                        "deny": (
                            {
                                "reason_code": merged.deny.reason_code,
                                "message": merged.deny.message,
                                "plugin_id": merged.deny.plugin_id,
                            }
                            if merged.deny
                            else None
                        ),
                    }
                    session_state.set_provider_metadata("policy_decisions", policy_decisions_summary)
        except Exception:
            policy_decisions_summary = None

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
        if policy_decisions_summary is not None:
            result_payload["policy_decisions"] = policy_decisions_summary
        run_dir = getattr(self.logger_v2, "run_dir", None)
        if run_dir:
            result_payload["run_dir"] = str(run_dir)
            result_payload.setdefault("logging_dir", str(run_dir))
        if payload:
            result_payload.update(payload)
        return result_payload

    base_tool_defs = tool_defs
    for step_index in range(max_steps):
        if getattr(self, "_stop_requested", False) or poll_control_stop():
            session_state.add_transcript_entry({"stop_requested": True})
            session_state.completion_summary = {
                "completed": False,
                "reason": "stopped_by_user",
                "method": "interrupt",
            }
            return finalize_run("stopped", "stopped_by_user", {"stopped": True})
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
                try:
                    if policy.tool_allowlist is not None or policy.tool_denylist:
                        allowed_names = policy.filter_tool_names(allowed_names)
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
            try:
                record_tool_allowlist_snapshot(session_state, turn_allowed_tool_names, turn_index=turn_index)
            except Exception:
                pass
            try:
                hook_manager = getattr(self, "hook_manager", None)
                if hook_manager is not None:
                    hook_payload = {
                        "turn": turn_index,
                        "tool_allowlist": list(turn_allowed_tool_names),
                    }
                    hook_result = hook_manager.run(
                        "pre_turn",
                        hook_payload,
                        session_state=session_state,
                        turn=turn_index,
                    )
                    if getattr(hook_result, "action", "") == "deny":
                        session_state.completion_summary = {
                            "completed": False,
                            "reason": "hook_denied",
                            "method": "hook",
                            "hook_reason": getattr(hook_result, "reason", None),
                        }
                        return finalize_run(
                            "policy_violation",
                            "hook_denied",
                            {"hook_reason": getattr(hook_result, "reason", None)},
                        )
            except Exception:
                pass
            try:
                self._inject_multi_agent_wakeups(session_state, markdown_logger)
            except Exception:
                pass
            # Build request and get response
            try:
                try:
                    session_state.record_lifecycle_event(
                        "model_call_started",
                        {"turn": turn_index, "model": str(model)},
                        turn=turn_index,
                    )
                except Exception:
                    pass
                provider_result = _call_model_with_hooks(
                    self,
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
                    turn_index=turn_index,
                )
                try:
                    session_state.record_lifecycle_event(
                        "model_call_finished",
                        {"turn": turn_index},
                        turn=turn_index,
                    )
                except Exception:
                    pass
            except RuntimeError as exc:
                try:
                    session_state.record_lifecycle_event(
                        "model_call_finished",
                        {"turn": turn_index, "error": str(exc)},
                        turn=turn_index,
                    )
                except Exception:
                    pass
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
                if getattr(self, "_stop_requested", False) or poll_control_stop():
                    session_state.add_transcript_entry({"stop_requested": True})
                    session_state.completion_summary = {
                        "completed": False,
                        "reason": "stopped_by_user",
                        "method": "interrupt",
                    }
                    return finalize_run("stopped", "stopped_by_user", {"stopped": True})
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
            try:
                session_state.record_lifecycle_event(
                    "turn_finished",
                    {"turn": turn_index, "completed": bool(completed)},
                    turn=turn_index,
                )
            except Exception:
                pass
            try:
                hook_manager = getattr(self, "hook_manager", None)
                if hook_manager is not None:
                    hook_manager.run(
                        "post_turn",
                        {"turn": turn_index, "completed": bool(completed)},
                        session_state=session_state,
                        turn=turn_index,
                    )
            except Exception:
                pass
            try:
                session_state.maybe_emit_compaction_summary(reason="turn_end")
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
