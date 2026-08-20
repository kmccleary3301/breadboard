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

class ReplayToolOutputMismatchError(RuntimeError):
    """Raised when replay-mode tool outputs diverge from the golden trace."""

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


__all__ = ['ReplayToolOutputMismatchError', 'classify_tool_terminal_state', 'build_tool_execution_outcome_record', 'build_tool_model_render_record', 'legacy_message_view']
