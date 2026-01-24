from __future__ import annotations

from typing import List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class AiderDiffDialect(BaseToolDialect):
    """Parse aider-style search/replace blocks into apply_search_replace calls."""

    type_id: str = "diff"

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""  # minimal prompt

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        if not text:
            return []

        lines = [line.rstrip("\n") for line in text.strip().splitlines()]
        if len(lines) < 4:
            return []

        file_name = lines[0].strip()
        if not file_name:
            return []

        def _find(marker: str, start: int) -> int:
            for idx in range(start, len(lines)):
                if lines[idx].strip() == marker:
                    return idx
            return -1

        start_idx = _find("<<<<<<< SEARCH", 1)
        if start_idx == -1:
            return []
        mid_idx = _find("=======", start_idx + 1)
        if mid_idx == -1:
            return []
        end_idx = _find(">>>>>>> REPLACE", mid_idx + 1)
        if end_idx == -1:
            return []

        search = "\n".join(lines[start_idx + 1 : mid_idx]).rstrip("\n")
        replace = "\n".join(lines[mid_idx + 1 : end_idx]).rstrip("\n")

        return [
            ToolCallParsed(
                function="apply_search_replace",
                arguments={
                    "file_name": file_name,
                    "search": search,
                    "replace": replace,
                },
                dialect="aider_diff",
            )
        ]
