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

from .execution_records import ReplayToolOutputMismatchError

_OPENCODE_ISO_TIMESTAMP_RE = re.compile(r"\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?Z")

_CLAUDE_BUDGET_LINE_RE = re.compile(
    r"USD budget: \$[0-9]+(?:\.[0-9]+)?/\$[0-9]+(?:\.[0-9]+)?; \$[0-9]+(?:\.[0-9]+)? remaining"
)

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

def _normalize_opencode_filetime_timestamps(text: str) -> str:
    raw = str(text)
    if "Last modification:" in raw or "Last read:" in raw:
        return _OPENCODE_ISO_TIMESTAMP_RE.sub("<TIMESTAMP>", raw)
    return raw

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
    provider_id = provider_router.parse_model_id(route_hint)[0]
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


__all__ = ['_OPENCODE_ISO_TIMESTAMP_RE', '_CLAUDE_BUDGET_LINE_RE', '_replay_tool_output_compare_targets', '_extract_tool_result_text', '_normalize_opencode_filetime_timestamps', '_normalize_claude_system_reminders', '_normalize_codex_shell_output', '_normalize_codex_apply_patch_output', '_normalize_replay_paths', 'record_replay_tool_output_mismatches']
