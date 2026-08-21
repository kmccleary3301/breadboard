from __future__ import annotations

import re
from typing import List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class OpenCodePatchDialect(BaseToolDialect):
    """Parse OpenCode patch blocks into patch tool calls."""

    type_id: str = "diff"

    _PATCH_RE = re.compile(r"\*\*\* Begin Patch[\s\S]*?\*\*\* End Patch", re.MULTILINE)

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        return ""  # minimal

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        if not text:
            return []
        calls: List[ToolCallParsed] = []
        for match in self._PATCH_RE.finditer(text):
            block = match.group(0)
            if block:
                calls.append(ToolCallParsed(function="patch", arguments={"patchText": block}))
        return calls
