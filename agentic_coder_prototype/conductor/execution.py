from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import uuid
from pathlib import Path
import random
import time
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


def _coerce_subprocess_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_subprocess_capture_with_group_timeout(
    args: List[str],
    *,
    cwd: str,
    timeout: float,
) -> Dict[str, Any]:
    """Run a local verification command and kill its full process group on timeout."""

    proc: Optional[subprocess.Popen[str]] = None
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "exit": int(proc.returncode or 0),
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = _coerce_subprocess_text(getattr(exc, "stdout", ""))
        stderr = _coerce_subprocess_text(getattr(exc, "stderr", ""))
        if proc is not None and proc.pid is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                out_after, err_after = proc.communicate(timeout=2)
                stdout += _coerce_subprocess_text(out_after)
                stderr += _coerce_subprocess_text(err_after)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    out_after, err_after = proc.communicate(timeout=2)
                    stdout += _coerce_subprocess_text(out_after)
                    stderr += _coerce_subprocess_text(err_after)
                except Exception as kill_exc:
                    stderr += f"\nFailed to collect process output after kill: {kill_exc}"
            except Exception as collect_exc:
                stderr += f"\nFailed to collect process output after termination: {collect_exc}"
        if not stderr.strip():
            stderr = f"Command timed out after {timeout:g} seconds"
        elif "timed out" not in stderr.lower():
            stderr = f"{stderr.rstrip()}\nCommand timed out after {timeout:g} seconds"
        return {"exit": 124, "stdout": stdout or "", "stderr": stderr or "", "timed_out": True}
    except Exception as exc:
        return {"exit": 1, "stdout": "", "stderr": str(exc), "timed_out": False}


def _auto_verify_smoke_command_from_prompt(prompt: str) -> str:
    timeout_match = re.search(
        r"\btimeout\s+([0-9]+(?:\.[0-9]+)?)(s?)\s+(?:bash|sh)\s+smoke_test\.sh\b",
        prompt,
        flags=re.IGNORECASE,
    )
    if timeout_match:
        value = timeout_match.group(1)
        suffix = timeout_match.group(2) or "s"
        return f"timeout {value}{suffix} bash smoke_test.sh"
    return "bash smoke_test.sh"


def _latest_prompt_requests_tool_stop_after_observation(session_state: SessionState) -> bool:
    latest_prompt = latest_real_user_prompt(session_state).lower()
    return (
        "exactly once" in latest_prompt
        or "after that single tool call" in latest_prompt
        or "after a single tool call" in latest_prompt
        or "one tool call" in latest_prompt
    )


def _is_allowed_async_result_followup(parsed_calls: List[Any], prior_tool_activity: Any) -> bool:
    if not parsed_calls:
        return False
    followup_names = {"taskoutput", "background_output"}
    call_names = {
        str(getattr(call, "function", "") or getattr(call, "provider_name", "") or "").strip().lower()
        for call in parsed_calls
    }
    if not call_names or any(name not in followup_names for name in call_names):
        return False
    tools = (prior_tool_activity or {}).get("tools") if isinstance(prior_tool_activity, dict) else None
    if not isinstance(tools, list):
        return False
    prior_names = {
        str((tool or {}).get("name") or "").strip().lower()
        for tool in tools
        if isinstance(tool, dict)
    }
    return bool(prior_names & {"task", "background_task", "call_omo_agent"})


def _async_result_task_id_from_activity(prior_tool_activity: Any) -> str:
    tools = (prior_tool_activity or {}).get("tools") if isinstance(prior_tool_activity, dict) else None
    if not isinstance(tools, list):
        return ""
    for tool in reversed(tools):
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip().lower()
        if name not in {"task", "background_task", "call_omo_agent"}:
            continue
        meta = tool.get("meta") if isinstance(tool.get("meta"), dict) else {}
        for key in ("async_task_id", "task_id", "taskId", "agentId", "agent_id"):
            value = str((meta or {}).get(key) or "").strip()
            if value:
                return value
    return ""


def _async_result_retrieval_tool_for_activity(prior_tool_activity: Any) -> str:
    tools = (prior_tool_activity or {}).get("tools") if isinstance(prior_tool_activity, dict) else None
    if not isinstance(tools, list):
        return "TaskOutput"
    names = {
        str((tool or {}).get("name") or "").strip().lower()
        for tool in tools
        if isinstance(tool, dict)
    }
    if "background_task" in names or "call_omo_agent" in names:
        return "background_output"
    return "TaskOutput"


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


def _latest_prompt_requests_read_only_answer_after_observation(session_state: SessionState) -> bool:
    latest_prompt = _strip_internal_prompt_blocks(latest_real_user_prompt(session_state))
    prompt = latest_prompt.lower()
    if not prompt or _prompt_requires_implementation_write_text(prompt):
        return False
    filename_like_target = bool(re.search(r"\b[\w.-]+\.(?:md|txt|json|yaml|yml|toml|py|ts|tsx|js|jsx|c|h|rs|go)\b", prompt))
    observation_requested = bool(
        re.search(r"\b(use tools?|inspect|read|list|count|summarize|check|run)\b", prompt)
        and (
            filename_like_target
            or re.search(r"\b(file|files|workspace|repo|readme|notes|tasks|marker|git status|wc\b|pwd|ls\b|rg\b|cat\b)\b", prompt)
        )
    )
    answer_requested = bool(
        re.search(r"\bthen\s+(?:reply|answer|summarize|tell me|respond)\b", prompt)
        or re.search(r"\breply with marker\b", prompt)
        or re.search(r"\binclude marker\b", prompt)
        or re.search(r"\bfinal (?:answer|summary)\b", prompt)
    )
    return observation_requested and answer_requested


def _observed_tool_calls_since_read_only_prompt(session_state: SessionState) -> int:
    prompt = _strip_internal_prompt_blocks(latest_real_user_prompt(session_state))
    summary = getattr(session_state, "tool_usage_summary", {}) or {}
    current_total = max(int(summary.get("run_shell_calls") or 0), int(summary.get("total_calls") or 0))
    try:
        stored_prompt = session_state.get_provider_metadata("read_only_observation_prompt")
        if stored_prompt != prompt:
            session_state.set_provider_metadata("read_only_observation_prompt", prompt)
            session_state.set_provider_metadata("read_only_observation_base_tool_count", current_total)
            return 0
        base_total = int(session_state.get_provider_metadata("read_only_observation_base_tool_count") or 0)
    except Exception:
        base_total = 0
    return max(0, current_total - base_total)


def _requested_final_answer_terms(session_state: SessionState) -> List[str]:
    latest_prompt = latest_real_user_prompt(session_state)
    marker = _required_final_answer_marker(session_state)
    explicit_marker_match = re.search(r"\bmarker\s+`?([A-Z0-9_:-]{3,})`?", latest_prompt)
    if explicit_marker_match:
        marker = explicit_marker_match.group(1)
    terms = [
        term
        for term in re.findall(r"\b[A-Z][A-Z0-9_:-]{2,}\b", latest_prompt)
        if term and term != marker
    ]
    if marker:
        return [marker, *[term for term in terms if term != marker]]
    return list(dict.fromkeys(terms))


def _is_internal_validation_prompt(content: str) -> bool:
    text = str(content or "")
    return bool(
        re.search(
            r"<(?:VALIDATION_ERROR|WORKSPACE_TOOL_REQUIRED|WORKSPACE_RECEIPT_REQUIRED|BREADBOARD_INTERNAL)[\s>]",
            text,
            re.IGNORECASE,
        )
    )


def _required_final_answer_marker(session_state: SessionState) -> str:
    latest_prompt = latest_real_user_prompt(session_state)
    marker_patterns = [
        r"first line\s+(?:must\s+be\s+)?exactly\s+`?([A-Za-z0-9_:-]+)`?",
        r"(?:exact|required)\s+marker\s+`?([A-Za-z0-9_:-]+)`?",
        r"(?:answer|reply|respond)\s+with\s+(?:the\s+)?(?:exact\s+)?marker\s+`?([A-Za-z0-9_:-]+)`?",
    ]
    for pattern in marker_patterns:
        marker_match = re.search(pattern, latest_prompt, re.IGNORECASE)
        if marker_match:
            return marker_match.group(1)
    return ""


def _required_final_answer_reminder(session_state: SessionState) -> str:
    latest_prompt = latest_real_user_prompt(session_state)
    marker_text = _required_final_answer_marker(session_state)
    marker_clause = f" Your first line must be exactly `{marker_text}`." if marker_text else ""
    requested_terms = [
        term
        for term in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", latest_prompt)
        if term != marker_text
    ]
    terms_clause = (
        " Include these requested terms: " + ", ".join(dict.fromkeys(requested_terms[:8])) + "."
        if requested_terms
        else ""
    )
    return marker_clause + terms_clause


def _strip_internal_prompt_blocks(text: str) -> str:
    return re.sub(
        r"<(?:VALIDATION_ERROR|WORKSPACE_TOOL_REQUIRED|WORKSPACE_RECEIPT_REQUIRED|BREADBOARD_INTERNAL)>.*?</(?:VALIDATION_ERROR|WORKSPACE_TOOL_REQUIRED|WORKSPACE_RECEIPT_REQUIRED|BREADBOARD_INTERNAL)>",
        "",
        str(text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()


def _implementation_write_guard_config(conductor: ConductorContext) -> Dict[str, Any]:
    cfg = conductor.config if isinstance(getattr(conductor, "config", None), dict) else {}
    guard_cfg = (cfg.get("workloop_guards", {}) or {}) if isinstance(cfg, dict) else {}
    impl_cfg = (guard_cfg.get("implementation_write_receipts", {}) or {}) if isinstance(guard_cfg, dict) else {}
    if not isinstance(impl_cfg, dict):
        impl_cfg = {}
    return {
        "enabled": impl_cfg.get("enabled", True),
        "max_read_only_calls_before_write": impl_cfg.get("max_read_only_calls_before_write", 4),
        "auto_verify_make_after_write_receipts": impl_cfg.get("auto_verify_make_after_write_receipts", False),
    }


def _prompt_requires_implementation_write_text(prompt_text: str) -> bool:
    prompt = str(prompt_text or "").lower()
    if not prompt:
        return False
    read_only_prompt = (
        re.search(r"\bread[- ]only\b", prompt)
        or re.search(r"\binspect[- ]only\b(?!\s+(?:this\s+)?workspace\b)", prompt)
        or re.search(r"\b(?:do not|don't) (?:edit|modify|write|change)\b(?:\s+(?:any(?:thing)?|files?|code|the workspace))?\b", prompt)
        or re.search(r"\bwithout (?:editing|modifying|writing|changing)\b(?:\s+(?:any(?:thing)?|files?|code|the workspace))?\b", prompt)
    )
    if (
        "inspection/reporting task" in prompt
        or "not a bug-fix task" in prompt
        or read_only_prompt
        or "do not edit files yet" in prompt
    ):
        return False
    if re.search(r"^\s*(?:verify|check|test)\b", prompt) and not re.search(
        r"\b(create|implement|write|modify|fix|add|update|generate|perform one small edit)\b",
        prompt,
    ):
        return False
    action_hit = re.search(r"\b(build|create|implement|write|modify|edit|fix|add|update|generate)\b", prompt)
    artifact_hit = re.search(
        r"\b(file|files|code|server|script|makefile|readme|test|smoke|compile|c11|smtp|workspace|repo)\b|"
        r"\.[a-z0-9_+-]+\b",
        prompt,
    )
    return bool(action_hit and artifact_hit)


def _latest_implementation_prompt(session_state: SessionState) -> str:
    prompts: List[str] = []
    try:
        for message in reversed(getattr(session_state, "messages", []) or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = _strip_internal_prompt_blocks(str(message.get("content") or ""))
            if text and not text.startswith("Tool execution results:"):
                prompts.append(text)
    except Exception:
        pass
    try:
        for message in reversed(getattr(session_state, "provider_messages", []) or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = _strip_internal_prompt_blocks(str(message.get("content") or ""))
            if text and not text.startswith("Tool execution results:"):
                prompts.append(text)
    except Exception:
        pass
    try:
        initial_prompt = _strip_internal_prompt_blocks(str(session_state.get_provider_metadata("initial_user_prompt") or ""))
        if initial_prompt:
            prompts.append(initial_prompt)
    except Exception:
        pass
    latest_prompt = _strip_internal_prompt_blocks(latest_real_user_prompt(session_state))
    if latest_prompt and not latest_prompt.startswith("Tool execution results:"):
        prompts.append(latest_prompt)
    for prompt in prompts:
        if _prompt_requires_implementation_write_text(prompt):
            return prompt
    return latest_prompt


def _implementation_prompt_candidates(session_state: SessionState) -> List[str]:
    prompts: List[str] = []
    try:
        message_lists = [
            getattr(session_state, "messages", []) or [],
            getattr(session_state, "provider_messages", []) or [],
        ]
        for message_list in message_lists:
            for message in reversed(message_list):
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                text = _strip_internal_prompt_blocks(str(message.get("content") or ""))
                if not text or text.startswith("Tool execution results:"):
                    continue
                if _prompt_requires_implementation_write_text(text) and text not in prompts:
                    prompts.append(text)
    except Exception:
        pass
    try:
        initial_prompt = _strip_internal_prompt_blocks(str(session_state.get_provider_metadata("initial_user_prompt") or ""))
        if _prompt_requires_implementation_write_text(initial_prompt) and initial_prompt not in prompts:
            prompts.append(initial_prompt)
    except Exception:
        pass
    latest_prompt = _latest_implementation_prompt(session_state)
    if latest_prompt and latest_prompt not in prompts:
        prompts.append(latest_prompt)
    return prompts


def _latest_prompt_requires_implementation_write(session_state: SessionState) -> bool:
    return _prompt_requires_implementation_write_text(_latest_implementation_prompt(session_state))


def _shell_command_is_read_only(command: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(command or "").strip())
    if not normalized:
        return True
    if re.search(r"(^|[;&|]\s*)(make|npm|pnpm|yarn|pytest|bash\s+smoke|sh\s+smoke|gcc|cc)\b", normalized):
        return False
    if re.search(r"(^|[;&|]\s*)(cat\s*>|tee\b|touch\b|mkdir\b|rm\b|mv\b|cp\b|chmod\b|python\b.*open\(|perl\b.*-pi|sed\b.*-i)\b", normalized):
        return False
    if re.search(r"(^|[^<>])>{1,2}[^>&]", normalized):
        return False
    segments = [segment.strip() for segment in re.split(r"[;&|]+", normalized) if segment.strip()]
    if not segments:
        return True
    read_only_prefixes = (
        "pwd",
        "ls",
        "find",
        "rg",
        "grep",
        "cat",
        "sed -n",
        "head",
        "tail",
        "wc",
        "printf",
        "git status",
        "git diff",
        "git log",
        "git show",
    )
    return all(segment.startswith(read_only_prefixes) or segment.startswith("[ -f ") for segment in segments)


def _command_tunnels_apply_patch(command: str) -> bool:
    return bool(re.search(r"(^|[;&|]\s*|\n\s*)apply_patch\s*<<", str(command or "")))


def _path_is_user_facing_write_target(candidate: str) -> bool:
    raw = str(candidate or "").strip().replace("\\", "/")
    if raw.startswith("/"):
        return False
    normalized = raw
    if not normalized or normalized == "/dev/null":
        return False
    normalized = re.sub(r"^[ab]/", "", normalized)
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return False
    if parts[0] in {".git", ".breadboard"}:
        return False
    return not any(part.startswith(".") for part in parts)


def _normalize_write_target(candidate: str) -> str:
    normalized = str(candidate or "").strip().strip("`'\":;()[]").rstrip(",").replace("\\", "/")
    normalized = re.sub(r"^[ab]/", "", normalized)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/+", "/", normalized)
    return normalized


def _tool_call_write_targets(tool_name: str, args: Dict[str, Any]) -> List[str]:
    name = str(tool_name or "")
    payload = args if isinstance(args, dict) else {}
    if name in {"create_file_from_block", "write", "write_file", "apply_search_replace"}:
        target = _normalize_write_target(str(payload.get("file_name") or payload.get("path") or ""))
        return [target] if target else []
    if name not in {"apply_unified_patch", "patch", "apply_patch"}:
        return []
    patch_text = str(payload.get("patch") or payload.get("input") or payload.get("patchText") or "")
    targets = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch_text, flags=re.MULTILINE)
    targets.extend(
        target
        for target in re.findall(r"^(?:---|\+\+\+) (?:[ab]/)?(.+)$", patch_text, flags=re.MULTILINE)
        if target != "/dev/null"
    )
    return list(dict.fromkeys(_normalize_write_target(target) for target in targets if _normalize_write_target(target)))


def _tool_call_delete_targets(tool_name: str, args: Dict[str, Any]) -> List[str]:
    name = str(tool_name or "")
    payload = args if isinstance(args, dict) else {}
    if name not in {"apply_unified_patch", "patch", "apply_patch"}:
        return []
    patch_text = str(payload.get("patch") or payload.get("input") or payload.get("patchText") or "")
    targets = re.findall(r"^\*\*\* Delete File: (.+)$", patch_text, flags=re.MULTILINE)
    targets.extend(
        target
        for target in re.findall(r"^--- (?:[ab]/)?(.+)\n\+\+\+ /dev/null$", patch_text, flags=re.MULTILINE)
        if target != "/dev/null"
    )
    return list(dict.fromkeys(_normalize_write_target(target) for target in targets if _normalize_write_target(target)))


def _shell_command_delete_targets(command: str) -> List[str]:
    text = str(command or "")
    targets: List[str] = []
    for match in re.findall(r"(?:^|[;&|]\s*)rm\s+(?:-[A-Za-z]+\s+)*([A-Za-z0-9_./-]+)", text):
        target = _normalize_write_target(match)
        if target and _path_is_user_facing_write_target(target):
            targets.append(target)
    return list(dict.fromkeys(targets))


def _latest_prompt_forbidden_direct_commands(session_state: SessionState) -> List[str]:
    prompt = _latest_implementation_prompt(session_state)
    commands: List[str] = []
    for match in re.findall(r"\bdo not run\s+(`?\.\/[A-Za-z0-9_.-]+`?)", prompt, flags=re.IGNORECASE):
        command = match.strip("`")
        if command and command not in commands:
            commands.append(command)
    return commands


def _latest_prompt_requests_file_deletion(session_state: SessionState) -> bool:
    prompt = _latest_implementation_prompt(session_state).lower()
    return bool(re.search(r"\b(delete|remove|rm|clean up|drop)\b", prompt))


def _shell_command_write_targets(command: str) -> List[str]:
    text = str(command or "")
    targets: List[str] = []
    for match in re.findall(r"(?:^|[^2])>>?\s*([A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+|[A-Za-z0-9_./-]+)", text):
        target = _normalize_write_target(match)
        if target and _path_is_user_facing_write_target(target):
            targets.append(target)
    for match in re.findall(r"(?:^|[;&|]\s*)tee\s+(?:-[a-zA-Z]+\s+)*([A-Za-z0-9_./-]+)", text):
        target = _normalize_write_target(match)
        if target and _path_is_user_facing_write_target(target):
            targets.append(target)
    return list(dict.fromkeys(targets))


def _tool_call_has_user_facing_write_target(tool_name: str, args: Dict[str, Any]) -> bool:
    targets = _tool_call_write_targets(tool_name, args)
    return any(_path_is_user_facing_write_target(target) for target in targets)


def _write_payload_looks_placeholder(args: Dict[str, Any]) -> bool:
    payload = args if isinstance(args, dict) else {}
    text = "\n".join(str(value or "") for value in payload.values() if isinstance(value, str))
    if not text:
        return False
    return bool(re.search(r"\b(placeholder|stub|todo|not implemented|fake implementation)\b", text, flags=re.IGNORECASE))


def _requested_write_targets(session_state: SessionState) -> List[str]:
    prompts: List[str] = _implementation_prompt_candidates(session_state)
    if not prompts:
        return []
    targets: List[str] = []
    file_pattern = (
        r"(?<![\w./-])"
        r"(?:[\w.-]+/)*[\w.-]+\."
        r"(?:c|h|sh|md|txt|json|yaml|yml|toml|py|js|ts|tsx|jsx|rs|go|java|rb|php|sql)"
        r"(?![\w-])"
    )
    def negated(span_start: int) -> bool:
        prefix = prompt[max(0, span_start - 120):span_start].lower()
        boundary = max(prefix.rfind("."), prefix.rfind("\n"), prefix.rfind(";"))
        clause = prefix[boundary + 1 :]
        return bool(re.search(r"\bdo not\b|\bdon't\b|\bnever\b|\bwithout\b", clause))

    def verification_only_mention(span_start: int) -> bool:
        prefix = prompt[max(0, span_start - 160):span_start].lower()
        boundaries = [
            prefix.rfind("."),
            prefix.rfind("\n"),
            prefix.rfind(";"),
            prefix.rfind(" then "),
        ]
        boundary = max(boundaries)
        clause = prefix[boundary + 1 :]
        if re.search(r"\b(create|implement|write|modify|edit|fix|add|update|generate|repair)\b", clause):
            return False
        return bool(re.search(r"\b(run|bash|sh|node\s+--check|verify|verification|test|smoke|compile|build|make|check)\b", clause))

    for prompt in prompts:
        prompt_targets: List[str] = []
        for match in re.finditer(file_pattern, prompt, flags=re.IGNORECASE):
            if negated(match.start()) or verification_only_mention(match.start()):
                continue
            target = _normalize_write_target(match.group(0))
            if target and _path_is_user_facing_write_target(target):
                prompt_targets.append(target)
        for special in ("Makefile", "Dockerfile"):
            special_match = re.search(rf"(?<![\w.-]){re.escape(special)}(?![\w.-])", prompt)
            if special_match and not negated(special_match.start()):
                prompt_targets.append(special)
        if (
            not prompt_targets
            and re.search(r"\breadme\b", prompt, flags=re.IGNORECASE)
            and re.search(r"\b(add|improve|update|edit|write|document|usage|instructions?)\b", prompt, flags=re.IGNORECASE)
        ):
            prompt_targets.append("README.md")
        if prompt_targets:
            targets.extend(prompt_targets)
            break
    return list(dict.fromkeys(targets))


def _successful_patch_result_paths(result: Dict[str, Any]) -> List[str]:
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return []
    paths = data.get("paths")
    if not isinstance(paths, list):
        return []
    out: List[str] = []
    for path_value in paths:
        target = _normalize_write_target(str(path_value or ""))
        if target:
            out.append(target)
    return list(dict.fromkeys(out))


def _write_target_matches_requested(target: str, requested_target: str) -> bool:
    if str(target or "").strip().startswith("/"):
        return False
    actual = _normalize_write_target(target)
    requested = _normalize_write_target(requested_target)
    if not actual or not requested:
        return False
    return (
        actual == requested
        or actual.endswith("/" + requested)
        or Path(actual).name == Path(requested).name
    )


def _requested_write_matches(write_targets: List[str], requested_targets: List[str]) -> List[str]:
    matches: List[str] = []
    for requested in requested_targets:
        if any(_write_target_matches_requested(target, requested) for target in write_targets):
            matches.append(_normalize_write_target(requested))
    return list(dict.fromkeys(matches))


def _implementation_task_anchor(session_state: SessionState, *, max_chars: int = 700) -> str:
    prompt = _latest_implementation_prompt(session_state).strip()
    if not prompt:
        return ""
    prompt = re.sub(r"<WORKSPACE_TOOL_REQUIRED>.*?</WORKSPACE_TOOL_REQUIRED>", "", prompt, flags=re.DOTALL).strip()
    compact = re.sub(r"\s+", " ", prompt)
    if len(compact) > max_chars:
        compact = compact[: max_chars - 1].rstrip() + "..."
    return f"\nOriginal request: {compact}"


def _missing_requested_write_targets(session_state: SessionState) -> List[str]:
    requested_targets = _requested_write_targets(session_state)
    if not requested_targets:
        return []
    summary = getattr(session_state, "tool_usage_summary", {}) or {}
    successful = {
        _normalize_write_target(target)
        for target in (summary.get("successful_requested_write_targets") or [])
        if _normalize_write_target(str(target))
    }
    return [target for target in requested_targets if _normalize_write_target(target) not in successful]


def _parsed_call_is_read_only_inspection(call: Any) -> bool:
    fn = str(getattr(call, "function", "") or "").lower()
    args = getattr(call, "arguments", {}) or {}
    if fn in {"read_file", "list_dir", "glob", "grep", "update_plan"}:
        return True
    if fn in {"run_shell", "shell_command", "bash"}:
        return _shell_command_is_read_only(str(args.get("command") or args.get("input") or ""))
    return False


def _implementation_write_receipt_missing(conductor: ConductorContext, session_state: SessionState) -> bool:
    guard_cfg = _implementation_write_guard_config(conductor)
    if not bool(guard_cfg.get("enabled", False)):
        return False
    if not _latest_prompt_requires_implementation_write(session_state):
        return False
    summary = getattr(session_state, "tool_usage_summary", {}) or {}
    requested_targets = _requested_write_targets(session_state)
    if requested_targets:
        missing = _missing_requested_write_targets(session_state)
        if not missing:
            return False
        try:
            workspace = Path(str(getattr(conductor, "workspace", "") or "")).resolve(strict=False)
            if workspace:
                still_missing = []
                for target in missing:
                    candidate = (workspace / _normalize_write_target(target)).resolve(strict=False)
                    try:
                        candidate.relative_to(workspace)
                    except Exception:
                        still_missing.append(target)
                        continue
                    if not candidate.exists():
                        still_missing.append(target)
                if not still_missing:
                    return False
        except Exception:
            pass
        return True
    return int(summary.get("successful_user_facing_writes") or 0) <= 0


def _latest_prompt_requests_verification(session_state: SessionState) -> bool:
    prompt = _latest_implementation_prompt(session_state).lower()
    return bool(re.search(r"\b(verify|verification|test|smoke|compile|build|make|check)\b", prompt))


def _successful_test_commands(session_state: SessionState) -> List[str]:
    commands: List[str] = []
    try:
        for turn_payload in (getattr(session_state, "turn_tool_usage", {}) or {}).values():
            for tool in (turn_payload or {}).get("tools", []) or []:
                meta = (tool or {}).get("meta") or {}
                if not meta.get("is_test_command"):
                    continue
                if meta.get("exit_code") != 0 or not bool((tool or {}).get("success")):
                    continue
                command = str(meta.get("command") or "").strip()
                if command and command not in commands:
                    commands.append(command)
    except Exception:
        return []
    return commands


def _requested_verification_commands_satisfied(session_state: SessionState) -> bool:
    prompt = _latest_implementation_prompt(session_state).lower()
    commands = [command.lower() for command in _successful_test_commands(session_state)]
    make_any_ok = any(re.search(r"(^|[;&|]\s*)(?:timeout\s+\d+s?\s+)?make(\s|$)", command) for command in commands)
    make_clean_all_ok = any(
        re.search(r"(^|[;&|]\s*)(?:timeout\s+\d+s?\s+)?make\s+clean\s+all(\s|[;&|]|$)", command)
        for command in commands
    )
    smoke_ok = any("smoke_test.sh" in command for command in commands)
    node_check_match = re.search(r"node\s+--check\s+([^\s;&|`'\"]+)", prompt)
    node_check_ok = True
    if node_check_match:
        requested_target = node_check_match.group(1).strip().rstrip(".,:;").lower()
        node_check_ok = any(
            re.search(rf"(^|[;&|]\s*)node\s+--check\s+{re.escape(requested_target)}(\s|[;&|]|$)", command)
            for command in commands
        )
    if "smoke_test.sh" in prompt:
        if not smoke_ok:
            return False
        if re.search(r"\bmake\s+clean\s+all\b", prompt) and not make_clean_all_ok:
            return False
        if re.search(r"\bmake\b", prompt) and not make_any_ok:
            return False
        if not node_check_ok:
            return False
        return True
    if node_check_match:
        return node_check_ok
    if re.search(r"\bmake\s+clean\s+all\b", prompt):
        return make_clean_all_ok
    if re.search(r"\bsmoke\b", prompt):
        return any("smoke" in command for command in commands)
    if re.search(r"\bmake\b", prompt):
        return make_any_ok
    return bool(commands)


def _implementation_verification_receipt_missing(conductor: ConductorContext, session_state: SessionState) -> bool:
    guard_cfg = _implementation_write_guard_config(conductor)
    if not bool(guard_cfg.get("enabled", False)):
        return False
    if not _latest_prompt_requires_implementation_write(session_state):
        return False
    if not _latest_prompt_requests_verification(session_state):
        return False
    if _implementation_write_receipt_missing(conductor, session_state):
        return False
    summary = getattr(session_state, "tool_usage_summary", {}) or {}
    if int(summary.get("successful_tests") or 0) <= 0:
        return True
    return not _requested_verification_commands_satisfied(session_state)


def _implementation_receipt_missing(conductor: ConductorContext, session_state: SessionState) -> bool:
    return (
        _implementation_write_receipt_missing(conductor, session_state)
        or _implementation_verification_receipt_missing(conductor, session_state)
    )


def _implementation_receipts_satisfied(conductor: ConductorContext, session_state: SessionState) -> bool:
    if not _latest_prompt_requires_implementation_write(session_state):
        return False
    return (
        not _implementation_write_receipt_missing(conductor, session_state)
        and not _implementation_verification_receipt_missing(conductor, session_state)
    )


def _maybe_auto_verify_make_after_write_receipts(
    conductor: ConductorContext,
    session_state: SessionState,
) -> bool:
    guard_cfg = _implementation_write_guard_config(conductor)
    if not bool(guard_cfg.get("auto_verify_make_after_write_receipts", False)):
        return False
    if not _latest_prompt_requests_verification(session_state):
        return False
    if _implementation_write_receipt_missing(conductor, session_state):
        return False
    if not _implementation_verification_receipt_missing(conductor, session_state):
        return False
    summary = getattr(session_state, "tool_usage_summary", {}) or {}
    successful_writes = int(summary.get("successful_writes") or 0)
    attempted_at = int(session_state.get_provider_metadata("auto_verify_make_after_write_receipts_successful_writes") or 0)
    if attempted_at >= successful_writes:
        return False
    workspace = Path(str(getattr(conductor, "workspace", "") or "")).resolve()
    makefile = workspace / "Makefile"
    prompt = _latest_implementation_prompt(session_state).lower()
    smoke_script = workspace / "smoke_test.sh"
    smoke_command = _auto_verify_smoke_command_from_prompt(prompt)
    if makefile.is_file() and "smoke_test.sh" in prompt and smoke_script.is_file():
        verify_command = f"make clean all && {smoke_command}"
        subprocess_args = ["bash", "-lc", verify_command]
    elif "smoke_test.sh" in prompt and smoke_script.is_file():
        node_check_match = re.search(r"node\s+--check\s+([^\s;&|]+)", prompt)
        if node_check_match:
            check_target = node_check_match.group(1).strip().strip("`'\"").rstrip(".,:;")
            candidate = (workspace / check_target).resolve(strict=False)
            try:
                candidate.relative_to(workspace)
            except Exception:
                candidate = workspace / "__invalid_check_target__"
            if candidate.is_file():
                verify_command = f"node --check {shlex.quote(check_target)} && {smoke_command}"
                subprocess_args = ["bash", "-lc", verify_command]
            else:
                verify_command = smoke_command
                subprocess_args = ["bash", "-lc", verify_command]
        else:
            verify_command = smoke_command
            subprocess_args = ["bash", "-lc", verify_command]
    elif makefile.is_file():
        verify_command = "make"
        subprocess_args = ["make"]
    else:
        return False
    session_state.set_provider_metadata("auto_verify_make_after_write_receipts_successful_writes", successful_writes)
    started = time.monotonic()
    completed = _run_subprocess_capture_with_group_timeout(
        subprocess_args,
        cwd=str(workspace),
        timeout=120,
    )
    exit_code = int(completed.get("exit") or 0)
    stdout = str(completed.get("stdout") or "")
    stderr = str(completed.get("stderr") or "")
    success = exit_code == 0
    elapsed_ms = int((time.monotonic() - started) * 1000)
    result_payload = {
        "tool": "shell_command",
        "command": verify_command,
        "exit": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_ms": elapsed_ms,
        "source": "auto_verify_make_after_write_receipts",
    }
    try:
        session_state.add_transcript_entry({"tool_result": result_payload})
        session_state.record_guardrail_event(
            "auto_verify_make_after_write_receipts",
            {
                "exit": exit_code,
                "elapsed_ms": elapsed_ms,
                "stdout_excerpt": stdout[:500],
                "stderr_excerpt": stderr[:500],
            },
        )
        turn_index = session_state.get_provider_metadata("current_turn_index")
        session_state.record_tool_event(
            turn_index if turn_index is not None else 0,
            "run_shell",
            success=success,
            metadata={
                "is_run_shell": True,
                "command": verify_command,
                "exit_code": exit_code,
                "is_test_command": True,
                "source": "auto_verify_make_after_write_receipts",
            },
            result=result_payload,
        )
    except Exception:
        pass
    if not success:
        return False
    # Auto verification is an opportunistic helper. It must not turn into a
    # closure receipt unless the requested verification contract is satisfied.
    return not _implementation_verification_receipt_missing(conductor, session_state)


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


class ReplayToolOutputMismatchError(RuntimeError):
    """Raised when replay-mode tool outputs diverge from the golden trace."""


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


def classify_tool_terminal_state(tool_result: Dict[str, Any]) -> str:
    payload = dict(tool_result or {})
    if payload.get("cancelled"):
        return "cancelled"
    if payload.get("denied") or payload.get("permission_denied") or payload.get("guardrail"):
        return "denied"
    if payload.get("error"):
        return "failed"
    return "completed"


def build_tool_execution_outcome_record(tool_name: str, tool_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize the engine-side execution outcome for a tool call.

    This boundary is intentionally distinct from any model-visible transcript or
    provider-facing render of the same tool result.
    """

    payload = dict(tool_result or {})
    error = payload.get("error")
    return {
        "tool": str(tool_name or ""),
        "terminal_state": classify_tool_terminal_state(payload),
        "ok": not bool(error),
        "error": error,
        "raw": payload,
    }


def build_tool_model_render_record(
    tool_name: str,
    tool_result: Dict[str, Any],
    *,
    max_preview_chars: int = 120,
) -> Dict[str, Any]:
    """
    Build the minimal model-visible render summary for a tool outcome.

    The intent is to keep render semantics explicit instead of letting every
    transcript or projection caller infer its own summary ad hoc.
    """

    outcome = build_tool_execution_outcome_record(tool_name, tool_result)
    raw = outcome["raw"]
    preview_source = raw.get("stdout")
    if preview_source is None:
        preview_source = raw.get("content")
    if preview_source is None and outcome["error"] is not None:
        preview_source = outcome["error"]
    preview = str(preview_source or "")
    truncated = len(preview) > max_preview_chars
    if truncated:
        preview = preview[:max_preview_chars]
    return {
        "tool": outcome["tool"],
        "terminal_state": outcome["terminal_state"],
        "status": "ok" if outcome["ok"] else "error",
        "error": outcome["error"],
        "preview": preview,
        "truncated": truncated,
    }


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
            if result is not None:
                error = result.get("error") if isinstance(result, dict) else None
                payload["success"] = bool(not error)
                if error:
                    payload["error"] = str(error)
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


def maybe_transition_plan_mode(conductor: ConductorContext, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
    conductor.guardrail_orchestrator.maybe_transition_plan_mode(
        session_state,
        markdown_logger,
        workspace_guard_handler=getattr(conductor, "workspace_guard_handler", None),
        todo_rate_guard_handler=getattr(conductor, "todo_rate_guard_handler", None),
    )


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


def _replay_tool_output_compare_targets(config: Dict[str, Any]) -> tuple[bool, set[str]]:
    """
    Decide which tool outputs to compare in replay mode.

    Default: compare only a conservative Codex-style subset so unrelated replay
    fixtures do not start failing just because tool output comparison exists.

    E4 profiles must opt-in via `replay.compare_tool_outputs`.
    """
    replay_cfg = (config.get("replay", {}) or {}) if isinstance(config, dict) else {}
    cfg = replay_cfg.get("compare_tool_outputs")

    if cfg is True:
        return True, set()
    if isinstance(cfg, str):
        if cfg.lower() == "all":
            return True, set()
        return False, {cfg.lower()}
    if isinstance(cfg, list):
        return False, {str(item).lower() for item in cfg if item}

    return False, {"shell_command", "apply_patch", "update_plan"}


def _extract_tool_result_text(message: Dict[str, Any]) -> Optional[str]:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
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


_OPENCODE_ISO_TIMESTAMP_RE = re.compile(r"\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?Z")


def _normalize_opencode_filetime_timestamps(text: str) -> str:
    raw = str(text)
    if "Last modification:" in raw or "Last read:" in raw:
        return _OPENCODE_ISO_TIMESTAMP_RE.sub("<TIMESTAMP>", raw)
    return raw


_CLAUDE_BUDGET_LINE_RE = re.compile(
    r"USD budget: \$[0-9]+(?:\.[0-9]+)?/\$[0-9]+(?:\.[0-9]+)?; \$[0-9]+(?:\.[0-9]+)? remaining"
)


def _normalize_claude_system_reminders(text: str) -> str:
    raw = str(text)
    if "<system-reminder>" not in raw:
        return raw
    return _CLAUDE_BUDGET_LINE_RE.sub("USD budget: $<SPENT>/$<BUDGET>; $<REMAINING> remaining", raw)


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
            if re.match(r"^[bcdlps-][rwxstST-]{9}\\s+", line):
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
    return "\\n".join(normalized)


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


def _normalize_replay_paths(text: str, prefixes: List[str]) -> str:
    raw = str(text)
    for prefix in prefixes:
        if not prefix:
            continue
        variants = {prefix, prefix.replace("\\\\", "/")}
        if not prefix.endswith("/"):
            variants.add(prefix + "/")
            variants.add(prefix.replace("\\\\", "/") + "/")
        for variant in variants:
            raw = raw.replace(variant, "<PATH>")
    return raw


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

    prefixes: List[str] = []
    try:
        replay_session = session_state.get_provider_metadata("active_replay_session")
        strip_prefix = getattr(replay_session, "strip_prefix", None)
        if isinstance(strip_prefix, str) and strip_prefix:
            prefixes.append(strip_prefix)
    except Exception:
        pass
    try:
        prefixes.append(str(Path(conductor.workspace).resolve()))
    except Exception:
        pass

    mismatches: List[Dict[str, Any]] = []
    for parsed, out in executed_results:
        expected = getattr(parsed, "expected_output", None)
        if expected is None:
            continue
        provider_name = getattr(parsed, "provider_name", getattr(parsed, "function", "")) or ""
        if not provider_name:
            continue

        provider_name_l = str(provider_name).lower()
        if not compare_all and provider_name_l not in compare_tools:
            continue

        call_id = getattr(parsed, "call_id", None) or "replay_call"
        try:
            msg = adapter.create_tool_result_message(call_id, provider_name, out)
        except Exception:
            continue
        actual_formatted = _extract_tool_result_text(msg)
        if actual_formatted is None:
            continue

        expected_text = _normalize_replay_paths(str(expected), prefixes)
        actual_text = _normalize_replay_paths(str(actual_formatted), prefixes)

        if provider_name_l == "shell_command":
            expected_text = _normalize_codex_shell_output(expected_text)
            actual_text = _normalize_codex_shell_output(actual_text)
        elif provider_name_l == "apply_patch":
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

    if not mismatches:
        return

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
    compare_cfg = replay_cfg.get("compare_tool_outputs") if isinstance(replay_cfg, dict) else None
    fail_flag = replay_cfg.get("fail_on_tool_output_mismatch") if isinstance(replay_cfg, dict) else None
    if bool(fail_flag) or compare_cfg is not None:
        first = mismatches[0]
        expected_excerpt = (first.get("expected") or "")[:400]
        actual_excerpt = (first.get("actual") or "")[:400]
        raise ReplayToolOutputMismatchError(
            "Replay tool output mismatch "
            f"(tool={first.get('tool')} call_id={first.get('call_id')})\\n"
            f"EXPECTED:\\n{expected_excerpt}\\n"
            f"ACTUAL:\\n{actual_excerpt}"
        )


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
