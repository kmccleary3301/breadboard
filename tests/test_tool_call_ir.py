from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

from agentic_coder_prototype.dialects.json_block import JSONBlockDialect
from agentic_coder_prototype.tool_call_ir import ToolCallIR, as_simplenamespace, to_tool_call_ir


def test_to_tool_call_ir_from_parsed_tool_call() -> None:
    dialect = JSONBlockDialect()
    content = """
```json
{
  "tool_calls": [
    {
      "function": "run_shell",
      "arguments": { "command": "echo hi" }
    }
  ]
}
```"""
    parsed = dialect.parse_tool_calls(content)
    assert parsed, "Expected JSONBlockDialect to parse at least one call"

    ir = to_tool_call_ir(parsed[0])
    assert isinstance(ir, ToolCallIR)
    assert ir.function == "run_shell"
    assert ir.arguments == {"command": "echo hi"}
    # Provider-name/dialect/call_id fields may be None; we only assert that they exist
    assert hasattr(ir, "provider_name")
    assert hasattr(ir, "call_id")
    assert hasattr(ir, "dialect")


def test_to_tool_call_ir_from_simplenamespace() -> None:
    ns = SimpleNamespace(
        function="create_file",
        arguments={"path": "foo.txt"},
        provider_name="create_file",
        call_id="call-123",
        dialect="json_block",
    )

    ir = to_tool_call_ir(ns)
    assert ir.function == "create_file"
    assert ir.arguments == {"path": "foo.txt"}
    assert ir.provider_name == "create_file"
    assert ir.call_id == "call-123"
    assert ir.dialect == "json_block"

    # Round-trip back to SimpleNamespace
    ns2 = as_simplenamespace(ir)
    assert isinstance(ns2, SimpleNamespace)
    assert ns2.function == "create_file"
    assert ns2.arguments == {"path": "foo.txt"}
    assert ns2.provider_name == "create_file"
    assert ns2.call_id == "call-123"
    assert ns2.dialect == "json_block"

