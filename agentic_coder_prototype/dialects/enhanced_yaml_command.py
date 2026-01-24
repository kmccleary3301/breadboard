"""
Enhanced YAML-command dialect implementation.

This exists alongside the legacy `dialects/yaml_command.py` (BaseToolDialect) so that the
enhanced tool-calling subsystem can consume YAML tool call blocks while keeping the main
engine's dialect interface stable.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .enhanced_base_dialect import (
    EnhancedBaseDialect,
    ToolCallFormat,
    TaskType,
    EnhancedToolDefinition,
    ParsedToolCall,
)


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw.startswith(("\"", "'")) and raw.endswith(("\"", "'")) and len(raw) >= 2:
        raw = raw[1:-1]
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        if re.fullmatch(r"-?\d+\.\d+", raw):
            return float(raw)
    except Exception:
        pass
    return raw


def _parse_tools_block(text: str) -> List[ParsedToolCall]:
    lines = [ln.rstrip("\n") for ln in (text or "").splitlines()]
    calls: List[ParsedToolCall] = []

    current_name: str | None = None
    current_args: Dict[str, Any] | None = None
    in_args = False
    args_indent: int | None = None

    def flush() -> None:
        nonlocal current_name, current_args, in_args, args_indent
        if current_name:
            calls.append(ParsedToolCall(function=current_name, arguments=current_args or {}))
        current_name = None
        current_args = None
        in_args = False
        args_indent = None

    for ln in lines:
        if not ln.strip():
            continue

        m_tool = re.match(r"^\s*-\s*name:\s*(\S+)\s*$", ln)
        if m_tool:
            flush()
            current_name = m_tool.group(1).strip()
            current_args = {}
            continue

        if current_name is None:
            continue

        m_args = re.match(r"^(\s*)args:\s*$", ln)
        if m_args:
            in_args = True
            args_indent = len(m_args.group(1) or "")
            continue

        if in_args:
            indent = len(ln) - len(ln.lstrip(" "))
            if args_indent is not None and indent <= args_indent:
                # args block ended
                in_args = False
                args_indent = None
                continue

            m_kv = re.match(r"^\s*([A-Za-z0-9_]+):\s*(.+?)\s*$", ln)
            if m_kv and current_args is not None:
                key = m_kv.group(1)
                value = _parse_scalar(m_kv.group(2))
                current_args[key] = value

    flush()
    return calls


class YAMLCommandDialect(EnhancedBaseDialect):
    """YAML tool-call dialect for shell/config workflows."""

    def __init__(self) -> None:
        super().__init__()
        self.type_id = "yaml_command"
        self.format = ToolCallFormat.YAML_COMMAND
        self.provider_support = ["*"]
        self.performance_baseline = 0.65
        self.use_cases = [TaskType.SHELL_COMMANDS, TaskType.CONFIGURATION, TaskType.GENERAL]

    def get_system_prompt_section(self) -> str:
        return (
            "When you need to use tools, emit a YAML block like:\n\n"
            "```yaml\n"
            "tools:\n"
            "  - name: tool_name\n"
            "    args:\n"
            "      key: value\n"
            "```"
        )

    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        if not content:
            return []

        yaml_blocks = re.findall(
            r"^\s*```yaml\s*\n(.*?)^\s*```",
            content,
            flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
        )
        candidates = yaml_blocks or [content]

        parsed: List[ParsedToolCall] = []
        for block in candidates:
            if "tools:" not in block:
                continue
            calls = _parse_tools_block(block)
            for call in calls:
                call.raw_content = block
                call.format = self.format.value
                call.confidence = 0.85
            parsed.extend(calls)

        return parsed

    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        tool_lines = []
        for tool in tools or []:
            tool_lines.append(f"- {tool.name}: {tool.description}")
        catalog = "\n".join(tool_lines)
        suffix = f"\n\nAvailable tools:\n{catalog}" if catalog else ""
        return f"{self.get_system_prompt_section()}{suffix}"
