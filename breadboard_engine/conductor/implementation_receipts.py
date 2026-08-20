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


__all__ = ['_coerce_subprocess_text', '_run_subprocess_capture_with_group_timeout', '_auto_verify_smoke_command_from_prompt', '_latest_prompt_requests_tool_stop_after_observation', '_is_allowed_async_result_followup', '_async_result_task_id_from_activity', '_async_result_retrieval_tool_for_activity', '_latest_prompt_requests_read_only_answer_after_observation', '_observed_tool_calls_since_read_only_prompt', '_requested_final_answer_terms', '_is_internal_validation_prompt', '_required_final_answer_marker', '_required_final_answer_reminder', '_strip_internal_prompt_blocks', '_implementation_write_guard_config', '_prompt_requires_implementation_write_text', '_latest_implementation_prompt', '_implementation_prompt_candidates', '_latest_prompt_requires_implementation_write', '_shell_command_is_read_only', '_command_tunnels_apply_patch', '_path_is_user_facing_write_target', '_normalize_write_target', '_tool_call_write_targets', '_tool_call_delete_targets', '_shell_command_delete_targets', '_latest_prompt_forbidden_direct_commands', '_latest_prompt_requests_file_deletion', '_shell_command_write_targets', '_tool_call_has_user_facing_write_target', '_write_payload_looks_placeholder', '_requested_write_targets', '_successful_patch_result_paths', '_write_target_matches_requested', '_requested_write_matches', '_implementation_task_anchor', '_missing_requested_write_targets', '_parsed_call_is_read_only_inspection', '_implementation_write_receipt_missing', '_latest_prompt_requests_verification', '_successful_test_commands', '_requested_verification_commands_satisfied', '_implementation_verification_receipt_missing', '_implementation_receipt_missing', '_implementation_receipts_satisfied', '_maybe_auto_verify_make_after_write_receipts']
