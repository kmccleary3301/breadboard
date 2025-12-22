import uuid
from pathlib import Path

import pytest
import ray

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from breadboard.sandbox_virtualized import SandboxFactory, DeploymentMode
from tool_calling.core import ToolDefinition, ToolParameter
from tool_calling.pythonic02 import Pythonic02Dialect
from tool_calling.pythonic_inline import PythonicInlineDialect
from tool_calling.bash_block import BashBlockDialect
from tool_calling.aider_diff import AiderDiffDialect
from tool_calling.unified_diff import UnifiedDiffDialect
from tool_calling.opencode_patch import OpenCodePatchDialect
from tool_calling.composite import CompositeToolCaller


def _read_blocks(md_path: Path):
    text = md_path.read_text(encoding="utf-8")
    blocks = []
    start = "<!-- ASSISTANT -->"
    end = "<!-- END -->"
    i = 0
    while True:
        si = text.find(start, i)
        if si == -1:
            break
        ei = text.find(end, si)
        if ei == -1:
            break
        content = text[si + len(start):ei].strip()
        blocks.append(content)
        i = ei + len(end)
    return blocks


def _tool_defs():
    return [
        ToolDefinition(
            type_id="python",
            name="run_shell",
            description="Run a shell command in the workspace and return stdout/exit.",
            parameters=[
                ToolParameter(name="command", type="string", description="The shell command to execute"),
                ToolParameter(name="timeout", type="integer", description="Timeout seconds", default=30),
            ],
            blocking=True,
        ),
        ToolDefinition(
            type_id="python",
            name="create_file",
            description="Create an empty file; contents should be provided via diffs.",
            parameters=[ToolParameter(name="path", type="string")],
        ),
        ToolDefinition(
            type_id="python",
            name="mark_task_complete",
            description="Signal completion.",
            parameters=[],
            blocking=True,
        ),
        ToolDefinition(
            type_id="python",
            name="read_file",
            description="Read a text file from the workspace.",
            parameters=[ToolParameter(name="path", type="string")],
        ),
        ToolDefinition(
            type_id="python",
            name="list_dir",
            description="List files in a directory with optional depth.",
            parameters=[
                ToolParameter(name="path", type="string"),
                ToolParameter(name="depth", type="integer", description="Tree depth (1-5, default 1)", default=1),
            ],
        ),
        ToolDefinition(
            type_id="diff",
            name="patch",
            description="Apply an OpenCode patch block.",
            parameters=[ToolParameter(name="patchText", type="string")],
            blocking=True,
        ),
    ]


def _caller():
    return CompositeToolCaller([
        Pythonic02Dialect(),
        PythonicInlineDialect(),
        BashBlockDialect(),
        AiderDiffDialect(),
        UnifiedDiffDialect(),
        OpenCodePatchDialect(),
    ])


@pytest.mark.parametrize("md_name", ["DRY_RUN_BAD_CODE.md"])
def test_dry_run_bad_code(tmp_path: Path, md_name: str):
    cfg = {"runtime": {"image": "python-dev:latest"}, "workspace": str(tmp_path)}
    factory = SandboxFactory()
    ray.init(ignore_reinit_error=True)
    try:
        vsb, session_id = factory.create_sandbox(DeploymentMode.DEVELOPMENT, cfg)

        agent = OpenAIConductor.options(name=f"drybad-{uuid.uuid4()}").remote(
            workspace=str(tmp_path), image="python-dev:latest", config={
                "enhanced_tools": {
                    "lsp_integration": {"enabled": True, "format_feedback": True}
                }
            }
        )

        blocks = _read_blocks(Path(__file__).with_name(md_name))
        assert blocks, f"{md_name} missing or empty"

        caller = _caller()
        tool_defs = _tool_defs()

        lsp_feedback_seen = False
        for block in blocks:
            parsed = caller.parse_all(block, tool_defs)
            assert parsed, f"Block did not produce any tool calls:\n{block}"
            for p in parsed:
                out = ray.get(agent._exec_raw.remote({"function": p.function, "arguments": p.arguments}))
                if isinstance(out, dict) and ("lsp_feedback" in out or "lsp_diagnostics" in out):
                    lsp_feedback_seen = True

        assert lsp_feedback_seen, "Expected LSP feedback/diagnostics in bad code dry run"
    finally:
        ray.shutdown()

