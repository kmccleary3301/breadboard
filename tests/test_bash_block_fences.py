"""BashBlockDialect accepts markdown shell fences alongside <BASH> blocks."""

from agentic_coder_prototype.core.core import ToolDefinition
from agentic_coder_prototype.dialects.bash_block import BashBlockDialect


def _tools():
    return [ToolDefinition(name="run_shell", description="", parameters=[])]


def test_bash_tag_block_still_parses():
    calls = BashBlockDialect().parse_calls("<BASH>\nls -la\n</BASH>", _tools())
    assert [c.arguments["command"] for c in calls] == ["ls -la"]


def test_markdown_bash_fence_parses():
    text = "I'll inspect the workspace first.\n```bash\nls -la\ncat README.md\n```\n"
    calls = BashBlockDialect().parse_calls(text, _tools())
    assert [c.arguments["command"] for c in calls] == ["ls -la\ncat README.md"]


def test_sh_and_shell_fences_parse():
    text = "```sh\necho one\n```\nthen\n```shell\necho two\n```\n"
    calls = BashBlockDialect().parse_calls(text, _tools())
    assert [c.arguments["command"] for c in calls] == ["echo one", "echo two"]


def test_non_shell_fences_ignored():
    text = "```python\nprint('hi')\n```\n```json\n{}\n```"
    assert BashBlockDialect().parse_calls(text, _tools()) == []
