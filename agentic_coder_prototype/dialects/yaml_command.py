from __future__ import annotations

from typing import List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class YAMLCommandDialect(BaseToolDialect):
    """Minimal YAML command dialect stub."""

    type_id: str = "python"

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        return []
