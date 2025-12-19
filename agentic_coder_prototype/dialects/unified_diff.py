from __future__ import annotations

from typing import List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class UnifiedDiffDialect(BaseToolDialect):
    """Parse unified diff blocks into apply_unified_patch calls."""

    type_id: str = "diff"

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""  # minimal

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        if not text:
            return []
        if "diff --git" not in text and "@@" not in text:
            return []
        # Use full text as patch payload
        return [ToolCallParsed(function="apply_unified_patch", arguments={"patch": text})]
