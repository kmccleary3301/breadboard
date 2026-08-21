from __future__ import annotations

from typing import List

from ..core.core import BaseToolDialect, ToolCallParsed, ToolDefinition


class AiderDiffDialect(BaseToolDialect):
    """Parse aider-style search/replace blocks into apply_search_replace calls."""

    type_id: str = "diff"

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        if not text or not any(tool.name == "apply_search_replace" for tool in tools):
            return []

        lines = text.strip().splitlines()
        parsed: List[ToolCallParsed] = []
        cursor = 0
        current_file = ""

        def find_marker(marker: str, start: int) -> int:
            for index in range(start, len(lines)):
                if lines[index].strip() == marker:
                    return index
            return -1

        while cursor < len(lines):
            search_start = find_marker("<<<<<<< SEARCH", cursor)
            if search_start == -1:
                break

            for index in range(search_start - 1, cursor - 1, -1):
                candidate = lines[index].strip()
                if not candidate or candidate.startswith("```"):
                    continue
                if candidate == ">>>>>>> REPLACE":
                    break
                current_file = candidate
                break

            separator = find_marker("=======", search_start + 1)
            next_search = find_marker("<<<<<<< SEARCH", search_start + 1)
            if separator == -1 or (next_search != -1 and next_search < separator):
                cursor = search_start + 1
                continue
            replace_end = find_marker(">>>>>>> REPLACE", separator + 1)
            if replace_end == -1:
                break
            if not current_file:
                cursor = replace_end + 1
                continue

            parsed.append(
                ToolCallParsed(
                    function="apply_search_replace",
                    arguments={
                        "file_name": current_file,
                        "search": "\n".join(lines[search_start + 1 : separator]),
                        "replace": "\n".join(lines[separator + 1 : replace_end]),
                    },
                    dialect="aider_diff",
                )
            )
            cursor = replace_end + 1

        return parsed
