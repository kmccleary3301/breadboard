from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .provider_runtime import (
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
)


@dataclass
class ToolCall:
    tool: str
    input: Dict[str, Any]
    status: Optional[str] = None
    output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class AssistantTurn:
    message_id: str
    text_parts: List[str] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplaySessionData:
    user_prompt: str
    turns: List[AssistantTurn]
    strip_prefix: str


class ReplaySession:
    """Stateful iterator that yields ProviderResult objects from recorded turns."""

    def __init__(self, session: ReplaySessionData, workspace_path: Path, *, preserve_tool_names: bool = False):
        self.workspace_path = workspace_path
        self.turns = session.turns
        self.strip_prefix = session.strip_prefix
        self.pointer = 0
        self.request_log: List[Dict[str, Any]] = []
        self._total_turns = len(self.turns)
        self.preserve_tool_names = bool(preserve_tool_names)

    def is_finished(self) -> bool:
        return self.pointer >= self._total_turns

    @property
    def total_turns(self) -> int:
        return self._total_turns

    def next_result(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ProviderResult:
        if self.pointer >= self._total_turns:
            raise RuntimeError("Replay session exhausted")
        turn = self.turns[self.pointer]
        self.pointer += 1
        msg_snapshot = [dict(m) for m in (messages or [])]
        self.request_log.append({"messages": msg_snapshot, "tools": tools})

        provider_tool_calls: List[ProviderToolCall] = []
        for idx, call in enumerate(turn.tool_calls):
            resolved_args = dict(call.input or {})
            raw_meta: Dict[str, Any] = {}
            if call.status is not None:
                raw_meta["expected_status"] = call.status
            if call.output is not None:
                raw_meta["expected_output"] = call.output
            if call.metadata:
                raw_meta["metadata"] = dict(call.metadata)
            if call.tool in {"shell_command", "ShellCommand"}:
                try:
                    wd = resolved_args.get("workdir") or resolved_args.get("cwd")
                    if isinstance(wd, str) and wd:
                        normalized = normalize_path(wd, self.strip_prefix) or "."
                        resolved_args = dict(resolved_args)
                        resolved_args["workdir"] = normalized
                except Exception:
                    pass
                provider_tool_calls.append(
                    ProviderToolCall(
                        id=f"replay_tool_{self.pointer}_{idx}",
                        name="shell_command",
                        arguments=json.dumps(resolved_args, sort_keys=True),
                        raw=raw_meta or None,
                    )
                )
            else:
                # Default: preserve recorded tool names and argument shape, but normalize
                # absolute paths so replays operate within the current workspace.
                try:
                    if isinstance(resolved_args, dict):
                        file_path = resolved_args.get("filePath") or resolved_args.get("file_path")
                        if isinstance(file_path, str) and file_path:
                            rel = normalize_path(file_path, self.strip_prefix)
                            if rel:
                                resolved_args = dict(resolved_args)
                                resolved_args["filePath"] = str((self.workspace_path / rel).resolve())
                except Exception:
                    pass
                provider_tool_calls.append(
                    ProviderToolCall(
                        id=f"replay_tool_{self.pointer}_{idx}",
                        name=call.tool if self.preserve_tool_names else _legacy_tool_name(call.tool),
                        arguments=json.dumps(resolved_args, sort_keys=True),
                        raw=raw_meta or None,
                    )
                )

        content = "\n\n".join(turn.text_parts) if turn.text_parts else None
        finish_reason = "tool_calls" if provider_tool_calls else "stop"
        provider_message = ProviderMessage(
            role="assistant",
            content=content,
            tool_calls=provider_tool_calls,
            finish_reason=finish_reason,
            raw_message=turn.raw,
        )
        return ProviderResult(
            messages=[provider_message],
            raw_response={"replay": True, "message_id": turn.message_id},
        )


def load_replay_session(session_path: Path) -> ReplaySessionData:
    entries = _load_json(session_path)
    user_prompt = load_user_prompt(entries)
    turns = load_assistant_turns(entries)
    tool_calls = load_tool_calls(entries)
    # Prefer deriving strip_prefix from file-level paths (OpenCode tools like
    # read/write/edit use `filePath`). Directory-valued `path` params (e.g. the
    # `list` tool) would otherwise pull the common prefix *above* the workspace
    # root and mis-map replay file paths into an extra subdirectory.
    file_paths: List[Optional[str]] = [
        call.input.get("filePath") or call.input.get("file_path")
        for call in tool_calls
    ]
    workdirs: List[str] = []
    for call in tool_calls:
        try:
            if not isinstance(call.input, dict):
                continue
            wd = call.input.get("workdir") or call.input.get("cwd")
            if isinstance(wd, str) and wd and os.path.isabs(wd):
                workdirs.append(wd)
        except Exception:
            continue

    # Prefer deriving the strip prefix from file paths when present (they point
    # inside the workspace). If we only have workdir-style paths, derive from
    # the workdirs themselves (not their parents) so the workspace root maps to ".".
    strip_prefix = derive_strip_prefix(file_paths)
    if not strip_prefix and workdirs:
        try:
            strip_prefix = os.path.commonpath(workdirs).rstrip(os.sep)
        except Exception:
            strip_prefix = ""
    if not strip_prefix:
        strip_prefix = derive_strip_prefix_from_tool_outputs(tool_calls)
    return ReplaySessionData(user_prompt=user_prompt, turns=turns, strip_prefix=strip_prefix)


_WORKSPACE_ROOT_RE = re.compile(r"(/[^\s]+?/workspace)(?:/|\b)")


def derive_strip_prefix_from_tool_outputs(calls: Sequence[ToolCall]) -> str:
    """
    Heuristic fallback: derive the workspace root from expected tool outputs.

    Some Claude Code tools (e.g. Glob) do not include filePath-like inputs, so
    we cannot infer the original workspace root from tool arguments. In those
    cases, the golden tool output often includes absolute paths under a
    `.../workspace/` directory; we extract those and compute a common prefix.
    """
    roots: List[str] = []
    for call in calls:
        out = call.output
        if not isinstance(out, str) or not out:
            continue
        for match in _WORKSPACE_ROOT_RE.finditer(out):
            root = match.group(1)
            if root and os.path.isabs(root):
                roots.append(root)
    if not roots:
        return ""
    try:
        return os.path.commonpath(roots).rstrip(os.sep)
    except Exception:
        return roots[0]


def _legacy_tool_name(name: str) -> str:
    """Compatibility mapping for older replay logs that used OpenCode tool IDs."""
    # Only rewrite lowercase / OpenCode-specific tool IDs. Claude Code uses
    # TitleCase tool names (e.g. `Bash`, `Write`) and those should be preserved
    # verbatim so native-tool replays can route through the correct schemas.
    if name in {"bash", "run_shell"}:
        return "run_shell"
    if name in {"write", "opencode_write"}:
        return "create_file_from_block"
    return name


def load_user_prompt(entries: Sequence[Dict[str, Any]]) -> str:
    for entry in entries:
        if entry.get("role") == "user":
            parts = entry.get("parts") or []
            for part in parts:
                if part.get("type") == "text":
                    return str(part.get("text") or "")
    return ""


def load_tool_calls(entries: Sequence[Dict[str, Any]]) -> List[ToolCall]:
    calls: List[ToolCall] = []
    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        for part in entry.get("parts", []):
            if part.get("type") != "tool":
                continue
            state = part.get("meta", {}).get("state") or {}
            status = state.get("status")
            output = state.get("output")
            if (output is None or output == "") and str(status or "").lower() == "error":
                output = state.get("error")
            calls.append(
                ToolCall(
                    tool=part.get("tool", ""),
                    input=state.get("input") or {},
                    status=status,
                    output=output,
                    metadata=state.get("metadata"),
                )
            )
    return calls


def load_assistant_turns(entries: Sequence[Dict[str, Any]]) -> List[AssistantTurn]:
    turns: List[AssistantTurn] = []
    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        turn = AssistantTurn(message_id=str(entry.get("message_id", "")), raw=entry)
        for part in entry.get("parts", []):
            part_type = part.get("type")
            if part_type == "text":
                turn.text_parts.append(str(part.get("text") or ""))
            elif part_type == "tool":
                state = part.get("meta", {}).get("state") or {}
                status = state.get("status")
                output = state.get("output")
                if (output is None or output == "") and str(status or "").lower() == "error":
                    output = state.get("error")
                turn.tool_calls.append(
                    ToolCall(
                        tool=part.get("tool", ""),
                        input=state.get("input") or {},
                        status=status,
                        output=output,
                        metadata=state.get("metadata"),
                    )
                )
        turns.append(turn)
    return turns


def derive_strip_prefix(paths: Sequence[Optional[str]]) -> str:
    prefixes = [
        str(Path(path).parent)
        for path in paths
        if path and os.path.isabs(path)
    ]
    if not prefixes:
        return ""
    prefix = os.path.commonpath(prefixes)
    return prefix.rstrip(os.sep)


def normalize_path(path: str, strip_prefix: str) -> str:
    if not path:
        return ""
    normalized = path.replace("\\", "/")
    prefix = strip_prefix.replace("\\", "/")
    if prefix and normalized.startswith(prefix):
        normalized = normalized[len(prefix):]
    return normalized.lstrip("/").lstrip("\\")


def resolve_todo_placeholders(
    payload: Dict[str, Any],
    workspace_path: Optional[Path] = None,
    todo_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = dict(payload)
    todos = None
    if todo_snapshot:
        todos = todo_snapshot.get("todos")
    elif workspace_path:
        todo_file = workspace_path / ".breadboard" / "todos.json"
        if not todo_file.exists():
            return result
        try:
            todo_data = json.loads(todo_file.read_text(encoding="utf-8"))
        except Exception:
            return result
        todos = todo_data.get("todos") if isinstance(todo_data, dict) else None
    if not isinstance(todos, list):
        return result

    placeholders = {}
    for idx, todo in enumerate(todos):
        todo_id = todo.get("id")
        if todo_id:
            placeholders[f"{{{{todo[{idx}].id}}}}"] = todo_id
    serialized = json.dumps(result)
    for placeholder, value in placeholders.items():
        serialized = serialized.replace(placeholder, value)
    try:
        return json.loads(serialized)
    except Exception:
        return result


def _load_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
