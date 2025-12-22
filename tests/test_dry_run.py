import os
from pathlib import Path
import uuid

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


def test_dry_run_exec(tmp_path: Path):
    # Prepare isolated sandbox via factory (development mirror to tmp_path)
    cfg = {"runtime": {"image": "python-dev:latest"}, "workspace": str(tmp_path)}
    factory = SandboxFactory()
    ray.init(ignore_reinit_error=True)
    try:
        vsb, session_id = factory.create_sandbox(DeploymentMode.DEVELOPMENT, cfg)

        # Create an agent conductor bound to the mirrored workspace
        agent = OpenAIConductor.options(name=f"dry-{uuid.uuid4()}").remote(
            workspace=str(tmp_path), image="python-dev:latest", config={}
        )

        # Read assistant blocks
        blocks = _read_blocks(Path(__file__).with_name("DRY_RUN.md"))
        assert blocks, "DRY_RUN.md missing or empty"

        # Set up parser and tool defs
        caller = _caller()
        tool_defs = _tool_defs()

        # Execute each block through the agent’s raw tool path
        transcript = []
        for block in blocks:
            parsed = caller.parse_all(block, tool_defs)
            assert parsed, f"Block did not produce any tool calls:\n{block}"
            chunks = []
            for p in parsed:
                out = ray.get(agent._exec_raw.remote({"function": p.function, "arguments": p.arguments}))
                transcript.append({"tool": p.function, "result": out})
                # Format the tool output for logging/user relay. Fallback to simple repr if method not present.
                if hasattr(agent, "_format_tool_output"):
                    fmt_attr = getattr(agent, "_format_tool_output")
                    try:
                        if hasattr(fmt_attr, "__wrapped__"):
                            chunks.append(fmt_attr.__wrapped__(agent, p.function, out, p.arguments))
                        else:
                            chunks.append(ray.get(fmt_attr.remote(p.function, out, p.arguments)))
                    except Exception:
                        chunks.append(f"{p.function}: {out}")
                else:
                    chunks.append(f"{p.function}: {out}")

        # Validate mirror effects in tmp workspace
        assert (tmp_path / "README.md").exists()
        assert (tmp_path / "notes" / "hello.txt").exists()

    finally:
        ray.shutdown()

