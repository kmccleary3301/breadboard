from __future__ import annotations

import re
from typing import List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class BashBlockDialect(BaseToolDialect):
    """
    Parse <BASH>...</BASH> blocks and map them to run_shell(command=...).

    Example:
      <BASH>
      ls -la
      make -j2
      </BASH>
    """

    # Shares the "python" tool type group so it can see run_shell
    type_id: str = "python"

    _BLOCK_RE = re.compile(r"<BASH>\s*(?P<cmd>[\s\S]*?)\s*</BASH>", re.M)

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        has_shell = any(t.name == "run_shell" for t in tools)
        if not has_shell:
            return ""
        return (
            "### Running bash commands\n"
            "Wrap commands in a <BASH>...</BASH> block. Example:\n"
            "<BASH>\nmake -j2\n</BASH>\n"
            "Notes:\n"
            "- Commands run in the workspace root.\n"
            "- Do NOT use bash to pipe large file contents (heredocs/echo >>).\n"
            "  Instead: call create_file() to create an empty file then apply a diff block for contents.\n"
        )

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        tool_name = None
        if any(t.name == "run_shell" for t in tools):
            tool_name = "run_shell"
        elif any(t.name == "bash" for t in tools):
            tool_name = "bash"
        if not tool_name:
            return []
        calls: list[ToolCallParsed] = []
        for m in self._BLOCK_RE.finditer(text):
            cmd = m.group("cmd").strip()
            if cmd:
                calls.append(ToolCallParsed(function=tool_name, arguments={"command": cmd}))
        return calls

