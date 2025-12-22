from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


def _make_conductor(config: dict) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.workspace = str(Path.cwd())
    inst.image = "python-dev:latest"
    inst.local_mode = True
    inst._multi_agent_orchestrator = None
    inst._multi_agent_event_log_path = None
    return inst  # type: ignore[return-value]


def test_task_tool_requires_multi_agent_enabled() -> None:
    conductor = _make_conductor({})
    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {"description": "x", "prompt": "y", "subagent_type": "reviewer"},
        }
    )
    assert "unknown tool" in str(out.get("error"))
