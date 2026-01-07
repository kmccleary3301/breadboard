from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp optional dependency not installed")
def test_mcp_manager_stdio_roundtrip(tmp_path: Path) -> None:
    from agentic_coder_prototype.mcp.manager import MCPManager

    server_script = tmp_path / "mcp_server.py"
    server_script.write_text(
        """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")

@mcp.tool()
def echo(text: str) -> dict:
    return {"text": text}

if __name__ == "__main__":
    mcp.run("stdio")
""".lstrip(),
        encoding="utf-8",
    )

    mgr = MCPManager(
        [
            {
                "name": "test",
                "command": sys.executable,
                "args": [str(server_script)],
            }
        ]
    )
    try:
        tools = mgr.list_tools()
        assert any(t.name == "mcp.test.echo" for t in tools)
        result = mgr.call_tool("mcp.test.echo", {"text": "hello"})
        assert isinstance(result, dict)
        assert result.get("text") == "hello" or result.get("result") == "hello"
    finally:
        mgr.close()

