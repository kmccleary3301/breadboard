"""
Enhanced unified-diff dialect implementation.

This exists alongside the legacy `dialects/unified_diff.py` (BaseToolDialect) so that the
enhanced tool-calling subsystem can use deterministic performance tracking while the main
engine keeps its existing dialect interface unchanged.
"""

from __future__ import annotations

import re
from typing import List

from .enhanced_base_dialect import (
    EnhancedBaseDialect,
    ToolCallFormat,
    TaskType,
    EnhancedToolDefinition,
    ParsedToolCall,
)


class UnifiedDiffDialect(EnhancedBaseDialect):
    """Unified diff dialect for code editing / file modification tasks."""

    def __init__(self) -> None:
        super().__init__()
        self.type_id = "unified_diff"
        self.format = ToolCallFormat.UNIFIED_DIFF
        self.provider_support = ["*"]
        self.performance_baseline = 0.61
        self.use_cases = [TaskType.CODE_EDITING, TaskType.FILE_MODIFICATION, TaskType.GENERAL]

    def get_system_prompt_section(self) -> str:
        return (
            "When you need to edit files, emit a unified diff inside a ```diff code block. "
            "The diff will be applied by the engine."
        )

    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        if not content:
            return []

        diff_blocks = re.findall(
            r"^\s*```diff\s*\n(.*?)^\s*```",
            content,
            flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
        )
        payload = diff_blocks[0].strip() if diff_blocks else content.strip()

        if not payload:
            return []
        if "diff --git" not in payload and "@@" not in payload and "--- " not in payload and "+++ " not in payload:
            return []

        return [
            ParsedToolCall(
                function="apply_diff",
                arguments={"hunks": [{"diff": payload}]},
                raw_content=payload,
                call_id=None,
                format=self.format.value,
                confidence=0.9,
            )
        ]

    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        # Unified diff usage is mostly format-driven (not tool-driven), but we include
        # a small tool list for completeness.
        tool_names = [t.name for t in tools or []]
        suffix = f" Tools available: {', '.join(tool_names)}." if tool_names else ""
        return f"{self.get_system_prompt_section()}{suffix}"
