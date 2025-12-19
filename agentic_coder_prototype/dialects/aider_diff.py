from __future__ import annotations

from typing import List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class AiderDiffDialect(BaseToolDialect):
    """Minimal aider-style search/replace dialect stub."""

    type_id: str = "diff"

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""  # minimal prompt

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        return []
