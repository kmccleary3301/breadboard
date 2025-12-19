from __future__ import annotations

from agentic_coder_prototype.core.core import ToolDefinition
from agentic_coder_prototype.dialects.bash_block import BashBlockDialect


def test_bash_block_dialect_prefers_run_shell_when_available() -> None:
    dialect = BashBlockDialect()
    tools = [ToolDefinition(name="run_shell", description="", type_id="python")]
    calls = dialect.parse_calls("<BASH>\necho hi\n</BASH>", tools)
    assert calls and calls[0].function == "run_shell"


def test_bash_block_dialect_falls_back_to_bash_tool() -> None:
    dialect = BashBlockDialect()
    tools = [ToolDefinition(name="bash", description="", type_id="python")]
    calls = dialect.parse_calls("<BASH>\necho hi\n</BASH>", tools)
    assert calls and calls[0].function == "bash"


def test_bash_block_dialect_noops_without_shell_tool() -> None:
    dialect = BashBlockDialect()
    assert dialect.prompt_for_tools([]) == ""
    assert dialect.parse_calls("<BASH>\necho hi\n</BASH>", []) == []

