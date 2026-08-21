from __future__ import annotations

from typing import List

from ..core.core import BaseToolDialect, ToolCallParsed, ToolDefinition


class AiderDiffDialect(BaseToolDialect):
    """Parse aider-style search/replace blocks into apply_search_replace calls."""

    type_id: str = "diff"

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        if not text:
            return []

        lines = [line.rstrip("\n") for line in text.strip().splitlines()]
        if len(lines) < 4:
            return []

        file_name = lines[0].strip()
        if not file_name:
            return []

        def find_marker(marker: str, start: int) -> int:
            for index in range(start, len(lines)):
                if lines[index].strip() == marker:
                    return index
            return -1

        search_start = find_marker("<<<<<<< SEARCH", 1)
        if search_start == -1:
            return []
        separator = find_marker("=======", search_start + 1)
        if separator == -1:
            return []
        replace_end = find_marker(">>>>>>> REPLACE", separator + 1)
        if replace_end == -1:
            return []

        return [
            ToolCallParsed(
                function="apply_search_replace",
                arguments={
                    "file_name": file_name,
                    "search": "\n".join(lines[search_start + 1 : separator]).rstrip("\n"),
                    "replace": "\n".join(lines[separator + 1 : replace_end]).rstrip("\n"),
                },
                dialect="aider_diff",
            )
        ]
