from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
import random
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core.core import ToolDefinition
from .messaging.markdown_logger import MarkdownLogger
from .provider_adapters import provider_adapter_manager
from .provider_routing import provider_router
from .provider_runtime import (
    ProviderMessage,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderResult,
    provider_registry,
)
from .replay import resolve_todo_placeholders
from .state.session_state import SessionState
from .turn_context import TurnContext


class ReplayToolOutputMismatchError(RuntimeError):
    """Raised when replay-mode tool outputs diverge from the golden trace."""


def legacy_message_view(provider_message: ProviderMessage) -> SimpleNamespace:
    tool_calls_ns: List[SimpleNamespace] = []
    for call in provider_message.tool_calls:
        function_ns = SimpleNamespace(
            name=call.name,
            arguments=call.arguments,
        )
        tool_calls_ns.append(
            SimpleNamespace(
                id=call.id,
                type=call.type,
                function=function_ns,
                raw=getattr(call, "raw", None),
            )
        )
    return SimpleNamespace(
        role=provider_message.role,
        content=provider_message.content,
        tool_calls=tool_calls_ns,
        raw_message=provider_message.raw_message,
        finish_reason=provider_message.finish_reason,
        index=provider_message.index,
    )


def _format_codex_wall_time(duration_seconds: Any) -> str:
    try:
        d = float(duration_seconds)
    except Exception:
        d = 0.0
    if d < 0.05:
        return "0"
    rounded = round(d, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.1f}".rstrip("0").rstrip(".") or "0"


def _format_codex_shell_command_output(tool_result: Any) -> Optional[str]:
    if not isinstance(tool_result, dict):
        return None
    exit_code = tool_result.get("exit")
    if exit_code is None:
        exit_code = tool_result.get("exit_code", 0)
    try:
        exit_code_int = int(exit_code)
    except Exception:
        exit_code_int = 0
    stdout = tool_result.get("stdout", "")
    stderr = tool_result.get("stderr", "")
    output = ""
    if isinstance(stdout, str):
        output = stdout
    if isinstance(stderr, str) and stderr:
        if output:
            output = f"{output.rstrip()}\n{stderr.lstrip()}".strip("\n")
        else:
            output = stderr
    wall = _format_codex_wall_time(tool_result.get("duration_seconds"))
    return f"Exit code: {exit_code_int}\nWall time: {wall} seconds\nOutput:\n{output}"


def _format_codex_apply_patch_output(tool_result: Any) -> Optional[str]:
    if not isinstance(tool_result, dict):
        return None
    data = tool_result.get("data") or {}
    status = data.get("status") if isinstance(data, dict) else None
    if not isinstance(status, dict):
        status = {}
    added = status.get("added") if isinstance(status.get("added"), list) else []
    modified = status.get("modified") if isinstance(status.get("modified"), list) else []
    deleted = status.get("deleted") if isinstance(status.get("deleted"), list) else []
    if not (added or modified or deleted):
        paths = data.get("paths") if isinstance(data.get("paths"), list) else []
        added = list(paths)
    lines: List[str] = []
    for path in added:
        if isinstance(path, str) and path:
            lines.append(f"A {path}")
    for path in modified:
        if isinstance(path, str) and path:
            lines.append(f"M {path}")
    for path in deleted:
        if isinstance(path, str) and path:
            lines.append(f"D {path}")
    output_text = "Success. Updated the following files:\n" + ("\n".join(lines) + "\n" if lines else "\n")
    try:
        exit_code = int(tool_result.get("exit") or 0)
    except Exception:
        exit_code = 0
    try:
        duration = float(tool_result.get("duration_seconds") or 0.0)
    except Exception:
        duration = 0.0
    payload = {
        "output": output_text,
        "metadata": {"exit_code": exit_code, "duration_seconds": duration},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_OPENCODE_ISO_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")


def _normalize_opencode_filetime_timestamps(text: str) -> str:
    raw = str(text)
    # OpenCode file-time guard errors include ISO timestamps which are not stable
    # across replay environments; normalize them so equivalence checks focus on
    # behavior/formatting rather than wall-clock values.
    if "Last modification:" in raw or "Last read:" in raw:
        return _OPENCODE_ISO_TIMESTAMP_RE.sub("<TIMESTAMP>", raw)
    return raw


_CLAUDE_BUDGET_LINE_RE = re.compile(
    r"USD budget: \$[0-9]+(?:\.[0-9]+)?/\$[0-9]+(?:\.[0-9]+)?; \$[0-9]+(?:\.[0-9]+)? remaining"
)


def _normalize_claude_system_reminders(text: str) -> str:
    """
    Claude Code tool results may contain <system-reminder> blocks with dynamic
    budget numbers that depend on model token usage/caching. Normalize just the
    numeric portions so replay parity focuses on controllable formatting/presence.
    """
    raw = str(text)
    if "<system-reminder>" not in raw:
        return raw
    return _CLAUDE_BUDGET_LINE_RE.sub("USD budget: $<SPENT>/$<BUDGET>; $<REMAINING> remaining", raw)


_CLAUDE_MALWARE_REMINDER = (
    "Whenever you read a file, you should consider whether it would be considered malware. "
    "You CAN and SHOULD provide analysis of malware, what it is doing. But you MUST refuse "
    "to improve or augment the code. You can still analyze existing code, write reports, "
    "or answer questions about the code behavior."
)

_CLAUDE_TODOWRITE_NUDGE = (
    "The TodoWrite tool hasn't been used recently. If you're working on tasks that would benefit "
    "from tracking progress, consider using the TodoWrite tool to track progress. Also consider "
    "cleaning up the todo list if has become stale and no longer matches what you are working on. "
    "Only use it if it's relevant to the current work. This is just a gentle reminder - ignore if "
    "not applicable. Make sure that you NEVER mention this reminder to the user\n"
)


def _claude_system_reminder_block(inner: str) -> str:
    return f"<system-reminder>\n{inner}\n</system-reminder>"


def apply_claude_system_reminders_to_results(
    conductor: ConductorContext,
    session_state: SessionState,
    executed_results: List[Tuple[Any, Any]],
) -> None:
    """
    Claude Code appends <system-reminder> blocks inside tool_result content. Apply a
    minimal, config-gated version of those reminders to the tool outputs we send to
    the provider so replay parity can validate controllable formatting surfaces.
    """
    try:
        sr_cfg = (((conductor.config.get("provider_tools") or {}).get("anthropic") or {}).get("system_reminders") or {})
    except Exception:
        sr_cfg = {}
    if not bool(sr_cfg.get("enabled")):
        return

    try:
        budget_limit = float(sr_cfg.get("usd_budget_limit") or 0.25)
    except Exception:
        budget_limit = 0.25
    try:
        nudge_after = int(sr_cfg.get("todowrite_nudge_after_tool_results") or 4)
    except Exception:
        nudge_after = 4
    nudge_after = max(nudge_after, 1)

    state = session_state.get_provider_metadata("claude_system_reminders_state") or {}
    if not isinstance(state, dict):
        state = {}
    tool_results_seen = int(state.get("tool_results_seen") or 0)
    last_todowrite_seen = state.get("todowrite_last_seen")
    if not isinstance(last_todowrite_seen, int):
        last_todowrite_seen = None
    nudge_emitted = bool(state.get("todowrite_nudge_emitted") or False)

    for parsed, out in executed_results:
        provider_name = getattr(parsed, "provider_name", None) or getattr(parsed, "function", None) or ""
        if not provider_name:
            continue
        if not isinstance(out, dict):
            continue
        base_text = out.get("__mvi_text_output")
        if not isinstance(base_text, str):
            continue
        if "<system-reminder>" in base_text:
            continue

        tool_results_seen += 1

        if provider_name == "TodoWrite":
            last_todowrite_seen = tool_results_seen
            # If TodoWrite is used, reset the nudge so it can appear again later if needed.
            nudge_emitted = False

        # Claude Code does not append system reminders to TaskOutput responses
        # while the task is still running (retrieval_status=not_ready).
        if provider_name == "TaskOutput" and "<retrieval_status>not_ready</retrieval_status>" in base_text:
            continue
        # Claude Code does not append system reminders to Task tool results.
        if provider_name == "Task":
            continue

        blocks: List[str] = []
        if provider_name == "Read":
            blocks.append(_claude_system_reminder_block(_CLAUDE_MALWARE_REMINDER))

        if not nudge_emitted and provider_name != "TodoWrite":
            since = tool_results_seen if last_todowrite_seen is None else (tool_results_seen - last_todowrite_seen)
            if since >= nudge_after:
                blocks.append(_claude_system_reminder_block(_CLAUDE_TODOWRITE_NUDGE))
                nudge_emitted = True

        # Budget reminder is appended last.
        try:
            spent = float(session_state.get_provider_metadata("usd_spent") or 0.0)
        except Exception:
            spent = 0.0
        remaining = max(budget_limit - spent, 0.0)
        budget_line = f"USD budget: ${spent}/${budget_limit}; ${remaining} remaining"
        blocks.append(_claude_system_reminder_block(budget_line))

        prefix = base_text.rstrip("\n")
        if prefix:
            out["__mvi_text_output"] = prefix + "\n\n" + "\n\n".join(blocks)
        else:
            out["__mvi_text_output"] = "\n\n".join(blocks)

        # For Claude parity, error tool results should include the same system-reminder
        # blocks in the surfaced error text.
        if out.get("error"):
            out["error"] = out.get("__mvi_text_output")

    state["tool_results_seen"] = tool_results_seen
    state["todowrite_last_seen"] = last_todowrite_seen
    state["todowrite_nudge_emitted"] = nudge_emitted
    session_state.set_provider_metadata("claude_system_reminders_state", state)


def _replay_tool_output_compare_targets(config: Dict[str, Any]) -> tuple[bool, set[str]]:
    """
    Decide which tool outputs to compare in replay mode.

    Default: compare only Codex CLI MVI tools (shell_command/apply_patch/update_plan) so
    we don't accidentally gate unrelated replay suites.
    """
    replay_cfg = (config.get("replay", {}) or {}) if isinstance(config, dict) else {}
    cfg = replay_cfg.get("compare_tool_outputs")

    if cfg is True:
        return True, set()
    if isinstance(cfg, str):
        if cfg.lower() == "all":
            return True, set()
        return False, {cfg}
    if isinstance(cfg, list):
        return False, {str(item) for item in cfg if item}

    return False, {"shell_command", "apply_patch", "update_plan"}


def _extract_tool_result_text(message: Dict[str, Any]) -> Optional[str]:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    # Anthropic-style tool_result blocks (or other list-based content payloads).
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    return inner
            if block.get("type") == "text":
                inner = block.get("text")
                if isinstance(inner, str):
                    return inner
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return None
    return None


def record_replay_tool_output_mismatches(
    conductor: ConductorContext,
    session_state: SessionState,
    executed_results: List[Tuple[Any, Any]],
    *,
    model: str,
) -> None:
    if not session_state.get_provider_metadata("replay_mode"):
        return

    compare_all, compare_tools = _replay_tool_output_compare_targets(conductor.config)
    if not compare_all and not compare_tools:
        return

    route_hint = getattr(conductor, "_current_route_id", None) or model
    try:
        provider_id = provider_router.parse_model_id(route_hint)[0]
    except Exception:
        provider_id = "openai"
    adapter = provider_adapter_manager.get_adapter(provider_id)

    mismatches: List[Dict[str, Any]] = []
    for parsed, out in executed_results:
        expected = getattr(parsed, "expected_output", None)
        if expected is None:
            continue
        provider_name = getattr(parsed, "provider_name", getattr(parsed, "function", ""))
        if not provider_name:
            continue
        if not compare_all and provider_name not in compare_tools:
            continue
        if str(provider_name).lower() in {"todowrite", "write", "bash"}:
            # Claude Code tool outputs are structurally different from our internal
            # tool payloads; skip strict replay output comparison for these tools.
            continue
        call_id = getattr(parsed, "call_id", None) or "replay_call"
        try:
            msg = adapter.create_tool_result_message(call_id, provider_name, out)
        except Exception:
            continue
        actual_formatted = _extract_tool_result_text(msg)
        if actual_formatted is None:
            continue
        expected_text = str(expected)
        actual_text = str(actual_formatted)
        if provider_name == "shell_command":
            expected_text = _normalize_codex_shell_output(expected_text)
            actual_text = _normalize_codex_shell_output(actual_text)
        elif provider_name == "apply_patch":
            expected_text = _normalize_codex_apply_patch_output(expected_text)
            actual_text = _normalize_codex_apply_patch_output(actual_text)
        expected_text = _normalize_opencode_filetime_timestamps(expected_text)
        actual_text = _normalize_opencode_filetime_timestamps(actual_text)
        if "<system-reminder>" in expected_text or "<system-reminder>" in actual_text:
            expected_text = _normalize_claude_system_reminders(expected_text)
            actual_text = _normalize_claude_system_reminders(actual_text)
        if expected_text != actual_text:
            mismatches.append(
                {
                    "tool": provider_name,
                    "call_id": getattr(parsed, "call_id", None),
                    "expected": expected_text,
                    "actual": actual_text,
                }
            )

    if mismatches:
        session_state.record_guardrail_event(
            "mvi_tool_output_mismatch",
            {
                "count": len(mismatches),
                "first": {
                    "tool": mismatches[0].get("tool"),
                    "call_id": mismatches[0].get("call_id"),
                    "expected_excerpt": (mismatches[0].get("expected") or "")[:240],
                    "actual_excerpt": (mismatches[0].get("actual") or "")[:240],
                },
            },
        )
        replay_cfg = (conductor.config.get("replay", {}) or {}) if isinstance(conductor.config, dict) else {}
        if bool(replay_cfg.get("fail_on_tool_output_mismatch")):
            first = mismatches[0]
            expected_excerpt = (first.get("expected") or "")[:400]
            actual_excerpt = (first.get("actual") or "")[:400]
            raise ReplayToolOutputMismatchError(
                "Replay tool output mismatch "
                f"(tool={first.get('tool')} call_id={first.get('call_id')})\n"
                f"EXPECTED:\n{expected_excerpt}\n"
                f"ACTUAL:\n{actual_excerpt}"
            )


def _format_codex_update_plan_output(tool_result: Any) -> Optional[str]:
    if isinstance(tool_result, dict) and isinstance(tool_result.get("__mvi_text_output"), str):
        return str(tool_result.get("__mvi_text_output"))
    if isinstance(tool_result, dict) and tool_result.get("ok") is False:
        err = tool_result.get("error")
        if isinstance(err, str) and err:
            return err
    return "Plan updated"


def _normalize_codex_shell_output(text: str) -> str:
    lines = str(text).splitlines()
    normalized: List[str] = []
    in_output = False
    for line in lines:
        if line.startswith("Wall time:"):
            normalized.append("Wall time: <redacted> seconds")
            continue
        if line.strip() == "Output:":
            normalized.append("Output:")
            in_output = True
            continue
        if in_output:
            stripped = line.strip()
            if stripped.startswith("total "):
                normalized.append("total <redacted>")
                continue
            if re.match(r"^[bcdlps-][rwxstST-]{9}\s+", line):
                parts = line.split()
                perm = parts[0] if parts else ""
                if "->" in parts:
                    idx = parts.index("->")
                    if idx > 0 and idx + 1 < len(parts):
                        name = parts[idx - 1]
                        target = parts[idx + 1]
                        if name in {".", ".."}:
                            normalized.append(name)
                        elif perm:
                            normalized.append(f"{perm} {name} -> {target}")
                        else:
                            normalized.append(f"{name} -> {target}")
                        continue
                if parts:
                    name = parts[-1]
                    if name in {".", ".."}:
                        normalized.append(name)
                    elif perm:
                        normalized.append(f"{perm} {name}")
                    else:
                        normalized.append(name)
                    continue
        normalized.append(line)
    return "\n".join(normalized)


def _normalize_codex_apply_patch_output(text: str) -> str:
    raw = str(text)
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(payload, dict):
        return raw
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        meta = dict(meta)
        meta["duration_seconds"] = 0.0
        payload = dict(payload)
        payload["metadata"] = meta
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_exec_func(conductor: Any, session_state: SessionState) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Create execution function with replay-aware TODO resolution."""
    if not session_state.get_provider_metadata("replay_mode"):
        return conductor._exec_raw

    try:
        workspace_path = Path(conductor.workspace)
    except Exception:
        workspace_path = Path(str(conductor.workspace))

    def _exec_with_replay(call: Dict[str, Any]) -> Dict[str, Any]:
        args = call.get("arguments")
        if isinstance(args, dict):
            try:
                resolved = resolve_todo_placeholders(dict(args), workspace_path)
                call = dict(call)
                call["arguments"] = resolved
            except Exception:
                pass
        result = conductor._exec_raw(call)
        expected_output = call.get("expected_output")
        provider_name = call.get("provider_name") or call.get("function") or ""
        if expected_output is not None and provider_name in {"TodoWrite", "Write", "Bash"}:
            expected_text = str(expected_output)
            if isinstance(result, dict):
                result = dict(result)
                result["__mvi_text_output"] = expected_text
                return result
            return {"__mvi_text_output": expected_text}
        return result

    return _exec_with_replay


def build_turn_context(conductor: Any, session_state: SessionState, parsed_calls: List[Any]) -> TurnContext:
    current_mode = session_state.get_provider_metadata("current_mode")
    ctx = TurnContext.from_session(session_state, current_mode, list(parsed_calls))
    if ctx.replay_mode:
        for call in ctx.parsed_calls:
            resolve_replay_todo_placeholders(conductor, session_state, call)
    return ctx


def summarize_execution_results(
    conductor: Any,
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
        elif tool_name_lower == "list":
            tool_name = "list_dir"
        elif tool_name_lower == "read":
            tool_name = "read_file"
        elif tool_name_lower == "write":
            tool_name = "create_file_from_block"
        elif tool_name_lower == "todo":
            tool_name = "TodoWrite"
        tool_result_dict = tool_result if isinstance(tool_result, dict) else {}
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
        if tool_name.startswith("todo.") or tool_name in {"TodoWrite", "todo"}:
            metadata["is_todo"] = True
        if tool_name == "run_shell":
            metadata["is_run_shell"] = True
            metadata["command"] = command
            exit_code_val = tool_result_dict.get("exit")
            if isinstance(exit_code_val, int):
                metadata["exit_code"] = exit_code_val
            if command and conductor._is_test_command(command):
                metadata["is_test_command"] = True
        if isinstance(turn_index_int, int):
            try:
                session_state.record_tool_event(
                    turn_index_int,
                    tool_name or "",
                    success=success_flag,
                    metadata=metadata,
                )
            except Exception:
                pass
        recent_tools_summary.append({
            "name": tool_name,
            "read_only": conductor._is_read_only_tool(tool_name),
            "completion_action": isinstance(tool_result, dict) and tool_result.get("action") == "complete",
        })
    if turn_index_int is not None:
        conductor._record_lsp_reward_metrics(session_state, turn_index_int)
        conductor._record_test_reward_metric(session_state, turn_index_int, test_success)
    return recent_tools_summary, test_success


def emit_turn_snapshot(conductor: Any, session_state: SessionState, turn_ctx: TurnContext) -> None:
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


def finalize_turn_context_snapshot(conductor: Any, session_state: SessionState, turn_ctx: TurnContext, turn_index: Optional[int]) -> None:
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


def apply_turn_guards(conductor: Any, turn_ctx: TurnContext, session_state: SessionState) -> List[Any]:
    return conductor.guardrail_orchestrator.apply_turn_guards(
        turn_ctx,
        session_state,
        workspace_guard_handler=getattr(conductor, "workspace_guard_handler", None),
        todo_rate_guard_handler=getattr(conductor, "todo_rate_guard_handler", None),
    )


def handle_blocked_calls(conductor: Any, turn_ctx: TurnContext, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
    conductor.guardrail_orchestrator.handle_blocked_calls(turn_ctx, session_state, markdown_logger)


def resolve_replay_todo_placeholders(conductor: Any, session_state: SessionState, parsed_call: Any) -> None:
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


def maybe_transition_plan_mode(conductor: Any, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
    conductor.guardrail_orchestrator.maybe_transition_plan_mode(
        session_state,
        markdown_logger,
        workspace_guard_handler=getattr(conductor, "workspace_guard_handler", None),
        todo_rate_guard_handler=getattr(conductor, "todo_rate_guard_handler", None),
    )


def execute_agent_calls(
    conductor: Any,
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
    try:
        try:
            conductor.permission_broker.ensure_allowed(session_state, parsed_calls)
        except Exception as exc:
            if exc.__class__.__name__ == "PermissionDeniedError":
                return [], 0, {"error": str(exc), "validation_failed": True, "permission_denied": True}, {}
            raise
        return conductor.agent_executor.execute_parsed_calls(
            parsed_calls,
            exec_func,
            transcript_callback=transcript_callback,
            policy_bypass=policy_bypass,
        )
    finally:
        conductor.agent_executor.allow_multiple_bash = previous_value


def log_provider_message(conductor: Any, provider_message: ProviderMessage, session_state: SessionState, markdown_logger: MarkdownLogger, stream_responses: bool) -> None:
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
    conductor: Any,
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
    allow_content_only_completion = bool(completion_cfg.get("allow_content_only_completion", False))
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
        try:
            parsed_preview = caller.parse_all(msg.content, tool_defs)
        except Exception:
            parsed_preview = []
        synthesized_blocks: List[str] = []
        if not parsed_preview:
            try:
                synthesized_blocks = conductor._synthesize_patch_blocks(msg.content)
            except Exception:
                synthesized_blocks = []
        # Only treat assistant text as tool calls if we can actually parse a call
        # or extract an explicit patch/diff block. Otherwise allow completion
        # detection to operate on content-only replies (Codex CLI-style).
        if parsed_preview or synthesized_blocks or not allow_content_only_completion:
            return handle_text_tool_calls(
                conductor,
                msg,
                caller,
                tool_defs,
                session_state,
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

        if completion_analysis["completed"] and completion_detector.meets_threshold(completion_analysis):
            guard_ok, guard_reason = conductor._completion_guard_check(session_state)
            if not guard_ok and guard_reason:
                abort = conductor._emit_completion_guard_feedback(
                    session_state,
                    markdown_logger,
                    guard_reason,
                    stream_responses,
                )
                if abort:
                    session_state.set_provider_metadata("completion_guard_abort", True)
                return False

            if stream_responses:
                print(f"[stop] reason={completion_analysis['method']} confidence={completion_analysis['confidence']:.2f} - {completion_analysis['reason']}")
            session_state.add_transcript_entry({
                "completion_detected": {
                    "method": completion_analysis["method"],
                    "confidence": completion_analysis["confidence"],
                    "reason": completion_analysis["reason"],
                    "content_analyzed": bool(msg.content),
                    "threshold_met": completion_detector.meets_threshold(completion_analysis)
                }
            })
            if not getattr(session_state, "completion_summary", None):
                session_state.completion_summary = {
                    "completed": True,
                    "method": completion_analysis["method"],
                    "reason": completion_analysis["reason"],
                    "confidence": completion_analysis["confidence"],
                    "source": "assistant_content",
                    "analysis": completion_analysis,
                }
            else:
                session_state.completion_summary.setdefault("completed", True)
                session_state.completion_summary.setdefault("method", completion_analysis["method"])
                session_state.completion_summary.setdefault("reason", completion_analysis["reason"])
                session_state.completion_summary.setdefault("confidence", completion_analysis["confidence"])
            return True
        else:
            completion_cfg = (conductor.config.get("completion", {}) or {})
            if completion_cfg.get("allow_zero_tool_completion"):
                summary = getattr(session_state, "tool_usage_summary", {})
                total_calls = int(summary.get("total_calls") or 0)
                if total_calls <= 0:
                    session_state.completion_summary = {
                        "completed": True,
                        "method": "auto_zero_tool",
                        "reason": "Conversation-only turn; zero tool usage allowed",
                        "confidence": 0.65,
                        "source": "assistant_content",
                        "analysis": completion_analysis,
                    }
                    return True

    return False


def handle_text_tool_calls(
    conductor: Any,
    msg,
    caller,
    tool_defs: List[ToolDefinition],
    session_state: SessionState,
    markdown_logger: MarkdownLogger,
    error_handler: Any,
    stream_responses: bool,
) -> bool:
    session_state.set_provider_metadata("recent_tool_activity", None)
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
    if not parsed:
        session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
        session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
        if current_mode == "plan":
            try:
                todos_cfg = conductor.guardrail_coordinator.todo_config()  # type: ignore[attr-defined]
            except Exception:
                todos_cfg = {}
            if bool(todos_cfg.get("enabled")) and bool(todos_cfg.get("strict")) and bool(todos_cfg.get("plan_mode_requires_todo", True)):
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
    session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
    session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
    session_state.add_transcript_entry({"assistant": msg.content})

    plan_bootstrap_traces = session_state.get_provider_metadata("plan_bootstrap_traces") or []
    session_state.set_provider_metadata("plan_bootstrap_traces", plan_bootstrap_traces)

    caller.track_tool_usage(parsed, session_state=session_state)

    turn_ctx = conductor._build_turn_context(session_state, parsed)
    parsed = conductor._apply_turn_guards(turn_ctx, session_state)
    conductor._handle_blocked_calls(turn_ctx, session_state, markdown_logger)
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
    apply_claude_system_reminders_to_results(conductor, session_state, executed_results)
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

    recent_tools_summary, test_success = conductor._summarize_execution_results(
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

    if execution_error:
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
            guard_ok, guard_reason = conductor._completion_guard_check(session_state)
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

            artifact_issues: List[str] = []
            try:
                if not session_state.get_provider_metadata("replay_mode"):
                    artifact_issues = conductor._validate_structural_artifacts(session_state)  # type: ignore[attr-defined]
            except Exception:
                artifact_issues = []
            if artifact_issues:
                message = (
                    "<VALIDATION_ERROR>\n"
                    "Cannot mark task complete yet. Fix the following issues:\n"
                    + "\n".join([f"- {issue}" for issue in artifact_issues])
                    + "\n</VALIDATION_ERROR>"
                )
                try:
                    session_state.add_message({"role": "user", "content": message}, to_provider=True)
                except Exception:
                    pass
                try:
                    markdown_logger.log_user_message(message)
                except Exception:
                    pass
                try:
                    session_state.increment_guardrail_counter("artifact_validation_blocks")
                    session_state.record_guardrail_event("artifact_validation_block", {"issues": artifact_issues})
                except Exception:
                    pass
                continue

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
                }
            else:
                session_state.completion_summary.setdefault("completed", True)
                session_state.completion_summary.setdefault("reason", "mark_task_complete")
                session_state.completion_summary.setdefault("method", "tool_mark_task_complete")

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
        tool_result_entry = conductor.message_formatter.create_tool_result_entry(
            tool_parsed.function, tool_result, syntax_type="custom-pythonic"
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
    conductor: Any,
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
        # OpenCode-style tool-call repair:
        # - If toolName casing differs and lowercase exists, rewrite toolName -> lowercase
        # - Otherwise rewrite tool call -> `invalid` with `{tool, error}`
        # This mirrors OpenCode's `experimental_repairToolCall` flow, and keeps
        # the provider-visible tool-call list consistent with the tool outputs we send.
        available_tools: Dict[str, Any] = {}
        for tool in (getattr(conductor, "yaml_tools", None) or []):
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name:
                available_tools[name] = tool
        available_names = set(available_tools)
        available_names.add("invalid")

        def _missing_required(tool_def: Any, args: Any) -> Optional[str]:
            if not isinstance(args, dict):
                return "input"
            params = getattr(tool_def, "parameters", None) or []
            for param in params:
                try:
                    name = getattr(param, "name", None)
                    required = bool(getattr(param, "required", False))
                except Exception:
                    continue
                if required and name and name not in args:
                    return str(name)
            return None

        def _invalid_payload(raw_tool: str, message: str) -> tuple[Dict[str, Any], str]:
            payload = {"tool": raw_tool, "error": message}
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            return payload, text

        repaired_calls: List[Dict[str, Any]] = []
        tool_calls_payload: List[Dict[str, Any]] = []
        for tc in msg.tool_calls:
            raw_name = getattr(getattr(tc, "function", None), "name", None)
            call_id = getattr(tc, "id", None)
            raw_args = getattr(getattr(tc, "function", None), "arguments", "{}")
            raw_args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args or {}, ensure_ascii=False, separators=(",", ":"))

            expected_output = None
            try:
                raw = getattr(tc, "raw", None)
                if isinstance(raw, dict):
                    expected_output = raw.get("expected_output")
            except Exception:
                expected_output = None

            if not isinstance(raw_name, str) or not raw_name:
                continue

            provider_name = raw_name
            args_dict: Any = {}
            args_str = raw_args_str

            parse_error = None
            try:
                args_dict = json.loads(raw_args_str) if raw_args_str.strip() else {}
            except Exception as exc:
                parse_error = str(exc)

            if parse_error is not None:
                payload, payload_str = _invalid_payload(raw_name, parse_error)
                provider_name = "invalid"
                args_dict = payload
                args_str = payload_str
            else:
                lower = raw_name.lower()
                if lower != raw_name and lower in available_names:
                    provider_name = lower
                if provider_name not in available_names:
                    payload, payload_str = _invalid_payload(raw_name, f"unknown tool: {raw_name}")
                    provider_name = "invalid"
                    args_dict = payload
                    args_str = payload_str
                else:
                    tool_def = available_tools.get(provider_name)
                    if tool_def is not None:
                        missing = _missing_required(tool_def, args_dict)
                        if missing:
                            payload, payload_str = _invalid_payload(raw_name, f"missing required field: {missing}")
                            provider_name = "invalid"
                            args_dict = payload
                            args_str = payload_str

            repaired_calls.append(
                {
                    "call_id": call_id,
                    "provider_name": provider_name,
                    "args": args_dict,
                    "args_str": args_str,
                    "expected_output": expected_output,
                }
            )
            tool_calls_payload.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": provider_name, "arguments": args_str},
                }
            )
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
        session_state.add_message(assistant_entry, to_provider=False)
        route_hint = getattr(conductor, "_current_route_id", None) or model
        provider_id_for_messages: Optional[str]
        try:
            provider_id_for_messages = provider_router.parse_model_id(route_hint)[0]
        except Exception:
            provider_id_for_messages = None
        if provider_id_for_messages == "anthropic":
            # Anthropic Messages API represents tool calls as `tool_use` content blocks.
            content_blocks: List[Dict[str, Any]] = []
            if msg.content:
                content_blocks.append({"type": "text", "text": str(msg.content)})
            for call in repaired_calls:
                fn_name = call.get("provider_name")
                call_id = call.get("call_id")
                args = call.get("args")
                if not fn_name or not call_id:
                    continue
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": fn_name,
                        "input": args if isinstance(args, dict) else {},
                    }
                )
            session_state.add_message({"role": "assistant", "content": content_blocks}, to_provider=True)
        else:
            session_state.add_message({"role": "assistant", "content": msg.content, "tool_calls": tool_calls_payload}, to_provider=True)

        session_state.add_transcript_entry({
            "assistant_with_tool_calls": {
                "content": msg.content,
                "tool_calls_count": len(repaired_calls),
                "tool_calls": [tc["function"]["name"] for tc in tool_calls_payload]
            }
        })

        parsed_calls: List[Any] = []
        for call in repaired_calls:
            fn = call.get("provider_name")
            call_id = call.get("call_id")
            args = call.get("args")
            expected_output = call.get("expected_output")
            if not fn:
                continue
            canonical_fn = conductor.agent_executor.canonical_tool_name(str(fn))
            call_obj = SimpleNamespace(
                function=canonical_fn,
                arguments=args,
                provider_name=str(fn),
                call_id=call_id,
                expected_output=expected_output,
            )
            parsed_calls.append(call_obj)

        if session_state.get_provider_metadata("replay_mode"):
            if any(getattr(call, "function", "") == "mark_task_complete" for call in parsed_calls):
                if not session_state.get_provider_metadata("completion_guard_emitted"):
                    guard_ok, guard_reason = conductor._completion_guard_check(session_state)
                    if not guard_ok and guard_reason:
                        conductor._emit_completion_guard_feedback(
                            session_state,
                            markdown_logger,
                            guard_reason,
                            stream_responses,
                        )

        current_mode = session_state.get_provider_metadata("current_mode")
        turn_ctx = build_turn_context(conductor, session_state, parsed_calls)
        parsed_calls = apply_turn_guards(conductor, turn_ctx, session_state)
        handle_blocked_calls(conductor, turn_ctx, session_state, markdown_logger)
        parsed_calls = conductor._expand_multi_file_patches(parsed_calls, session_state, markdown_logger)
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
            apply_claude_system_reminders_to_results(conductor, session_state, executed_results)
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
                try:
                    todos_cfg = conductor.guardrail_coordinator.todo_config()  # type: ignore[attr-defined]
                except Exception:
                    todos_cfg = {}
                if bool(todos_cfg.get("enabled")) and bool(todos_cfg.get("strict")) and bool(todos_cfg.get("plan_mode_requires_todo", True)):
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

        if execution_error:
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
            if (
                current.get("fn") == "mark_task_complete"
                and isinstance(current.get("out"), dict)
                and current["out"].get("action") == "complete"
            ):
                guard_ok, guard_reason = conductor._completion_guard_check(session_state)
                if guard_ok:
                    continue
                guard_blocked = True
                session_state.increment_guardrail_counter("completion_guard_blocks")
                abort = conductor._emit_completion_guard_feedback(
                    session_state,
                    markdown_logger,
                    guard_reason or "Completion guard blocked mark_task_complete",
                    stream_responses,
                )
                # Even when blocked, the provider must receive a tool result for the tool call id.
                # Otherwise OpenAI/Anthropic will reject the next request due to a missing tool output.
                current["out"] = {
                    "action": "blocked",
                    "blocked": True,
                    "reason": guard_reason or "Completion guard blocked mark_task_complete",
                }
                current["failed"] = True
                summary = session_state.tool_usage_summary
                summary["total_calls"] = max(0, int(summary.get("total_calls", 0)) - 1)
                if isinstance(turn_index_hint, int):
                    turn_usage = session_state.turn_tool_usage.get(turn_index_hint)
                    if turn_usage and isinstance(turn_usage.get("tools"), list):
                        tools_list = turn_usage["tools"]
                        for tool_entry_idx in range(len(tools_list) - 1, -1, -1):
                            if tools_list[tool_entry_idx].get("name") == "mark_task_complete":
                                tools_list.pop(tool_entry_idx)
                                break
                recent_tools_summary = [
                    entry for entry in recent_tools_summary
                    if entry.get("name") != "mark_task_complete"
                ]
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
        conductor._finalize_turn_context_snapshot(session_state, turn_ctx, turn_index_int)
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

        if flow_strategy == "assistant_continuation":
            all_results_text = []
            for r in results:
                formatted_output = conductor.message_formatter.format_tool_output(r["fn"], r["out"], r["args"])
                call_id = r.get("call_id")

                tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                    r["fn"], r["out"], call_id or f"native_call_{r['fn']}", "openai"
                )
                session_state.add_message(tool_result_entry, to_provider=False)
                all_results_text.append(formatted_output)

            continuation_content = f"\n\nTool execution results:\n" + "\n\n".join(all_results_text)
            assistant_continuation = {
                "role": "assistant",
                "content": continuation_content
            }
            session_state.add_message(assistant_continuation)
            markdown_logger.log_assistant_message(continuation_content)
        else:
            for r in results:
                formatted_output = conductor.message_formatter.format_tool_output(r["fn"], r["out"], r["args"])
                call_id = r.get("call_id")

                tool_result_entry = conductor.message_formatter.create_tool_result_entry(
                    r["fn"], r["out"], call_id or f"native_call_{r['fn']}", "openai"
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

        conductor._maybe_transition_plan_mode(session_state, markdown_logger)
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
    conductor: Any,
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
) -> Optional[ProviderResult]:
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
        fallback_runtime_descriptor, fallback_model_resolved = provider_router.get_runtime_descriptor(fallback_model)
        fallback_runtime = provider_registry.create_runtime(fallback_runtime_descriptor)
        fallback_client_config = provider_router.create_client_config(fallback_model)
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
