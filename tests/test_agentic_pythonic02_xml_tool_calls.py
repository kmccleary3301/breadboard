from __future__ import annotations

from agentic_coder_prototype.core.core import ToolDefinition, ToolParameter
from agentic_coder_prototype.dialects.pythonic02 import Pythonic02Dialect


def test_pythonic02_parses_xml_tool_call_blocks() -> None:
    dialect = Pythonic02Dialect()
    tools = [
        ToolDefinition(
            type_id="python",
            name="todo.create",
            description="Create todos.",
            parameters=[ToolParameter(name="items", type="array")],
        ),
        ToolDefinition(
            type_id="python",
            name="todo.update",
            description="Update todo.",
            parameters=[
                ToolParameter(name="id", type="string"),
                ToolParameter(name="status", type="string"),
            ],
        ),
        ToolDefinition(
            type_id="python",
            name="run_shell",
            description="Run shell.",
            parameters=[ToolParameter(name="command", type="string")],
        ),
    ]

    payload = """
<tool_calls>
  <tool_call name="todo.create">
    <parameter name="items">[{"title": "A"}]</parameter>
  </tool_call>
</tool_calls>
<tool_calls>
  <tool_call name="todo.update">
    <parameter name="id">todo_123</parameter>
    <parameter name="status">in_progress</parameter>
  </tool_call>
</tool_calls>
<tool_calls>
  <tool_call name="run_shell">
    <parameter name="command">echo a &amp;&amp; echo b</parameter>
  </tool_call>
</tool_calls>
""".strip()

    calls = dialect.parse_calls(payload, tools)
    assert [c.function for c in calls] == ["todo.create", "todo.update", "run_shell"]
    assert calls[0].arguments["items"] == [{"title": "A"}]
    assert calls[1].arguments == {"id": "todo_123", "status": "in_progress"}
    assert calls[2].arguments["command"] == "echo a && echo b"

