from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def test_mcp_stdio_parameters_use_restricted_process_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    import breadboard_engine.mcp.manager as manager_module

    manager = manager_module.MCPManager.__new__(manager_module.MCPManager)
    manager._workspace = tmp_path
    manager._protected_paths = (str(tmp_path / "protected"),)
    captured = {}

    class Policy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def command_and_environment(self, command):
            captured["command"] = command
            return SimpleNamespace(
                argv=("/isolated-launch", *command),
                environment_dict=lambda: {"PATH": "/usr/bin"},
            )

    monkeypatch.setattr(manager_module, "ChildProcessPolicy", Policy)
    monkeypatch.setattr(
        manager_module,
        "StdioServerParameters",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    configured_cwd = tmp_path / "server"
    configured_cwd.mkdir()
    params = manager._stdio_parameters(
        "server-bin",
        ("--stdio",),
        {"FOO": "bar"},
        str(configured_cwd),
    )

    assert captured["command"] == ("server-bin", "--stdio")
    assert captured["workspace"] == tmp_path
    assert captured["working_directory"] == str(configured_cwd)
    assert captured["protected_paths"] == manager._protected_paths
    assert captured["allowed_override_keys"] == ("FOO",)
    assert params.command == "/isolated-launch"
    assert params.args == ["server-bin", "--stdio"]
    assert params.env == {"PATH": "/usr/bin"}
    assert params.cwd == str(configured_cwd)


def test_mcp_replay_append_rejects_linked_target(tmp_path: Path) -> None:
    from breadboard_engine.mcp.replay import append_mcp_replay_entry
    from breadboard_engine.security import WorkspacePathError

    secret = tmp_path / "credential"
    secret.write_text("mcp-tape-link-canary", encoding="utf-8")
    tape = tmp_path / "mcp_tape.jsonl"
    tape.symlink_to(secret)

    with pytest.raises(WorkspacePathError):
        append_mcp_replay_entry(tape, {"result": "replacement"})

    assert secret.read_text(encoding="utf-8") == "mcp-tape-link-canary"


@pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="mcp optional dependency not installed",
)
def test_mcp_manager_stdio_roundtrip(tmp_path: Path) -> None:
    from breadboard_engine.mcp.manager import MCPManager

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
        ],
        workspace=str(tmp_path),
    )
    try:
        tools = mgr.list_tools()
        assert any(t.name == "mcp.test.echo" for t in tools)
        result = mgr.call_tool("mcp.test.echo", {"text": "hello"})
        assert isinstance(result, dict)
        assert result.get("text") == "hello" or result.get("result") == "hello"
    finally:
        mgr.close()
