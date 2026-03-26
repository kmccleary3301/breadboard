from __future__ import annotations

import re
from typing import List

from ..core.core import ToolCallParsed, ToolDefinition
from .enhanced_base_dialect import (
    EnhancedBaseDialect,
    EnhancedToolDefinition,
    ParsedToolCall,
    TaskType,
    ToolCallFormat,
)


class UnifiedDiffDialect(EnhancedBaseDialect):
    """Enhanced unified-diff dialect with legacy parse compatibility."""

    def __init__(self):
        super().__init__()
        self.type_id = "diff"
        self.format = ToolCallFormat.UNIFIED_DIFF
        self.provider_support = ["*"]
        self.performance_baseline = 0.78
        self.use_cases = [
            TaskType.CODE_EDITING,
            TaskType.FILE_MODIFICATION,
            TaskType.GENERAL,
        ]

    def get_system_prompt_section(self) -> str:
        return (
            "When you need to modify files, emit a unified diff block wrapped in "
            "```diff fences so it can be applied directly."
        )

    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        if not content:
            return []

        diff_blocks = re.findall(r"```diff\s*\n(.*?)\n```", content, re.DOTALL | re.IGNORECASE)
        candidate_patches = diff_blocks or [content]

        parsed: List[ParsedToolCall] = []
        for patch in candidate_patches:
            normalized = patch.strip()
            if "@@" not in normalized and "diff --git" not in normalized and not normalized.startswith("--- "):
                continue

            hunks = re.findall(r"@@.*?(?=\n@@|\Z)", normalized, re.DOTALL)
            parsed.append(
                ParsedToolCall(
                    function="apply_diff",
                    arguments={
                        "patch": normalized,
                        "hunks": hunks or [normalized],
                    },
                    raw_content=normalized,
                    format=self.format.value,
                    confidence=0.9,
                )
            )

        return parsed

    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        return (
            "Use unified diffs for file changes. Emit only valid patch hunks for the "
            "target files."
        )

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        """Legacy BaseToolDialect compatibility."""
        return self.get_system_prompt_section()

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        """Legacy BaseToolDialect compatibility."""
        return [
            ToolCallParsed(function=call.function, arguments=call.arguments)
            for call in self.parse_tool_calls(text)
        ]
