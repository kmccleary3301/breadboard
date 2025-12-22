from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.tool_prompt_planner import ToolPromptPlanner


class DummySessionState:
    def __init__(self) -> None:
        self.provider_messages = [{"role": "user", "content": "context"}]


def _append(message: dict, text: str) -> None:
    message.setdefault("content", "")
    message["content"] += text


class DummyLogger:
    def __init__(self) -> None:
        self.tools = []

    def log_tool_availability(self, names):
        self.tools.append(list(names))


def test_per_turn_append_adds_tool_directive() -> None:
    planner = ToolPromptPlanner()
    session = DummySessionState()
    markdown_logger = DummyLogger()
    tool_defs = [SimpleNamespace(name="write"), SimpleNamespace(name="bash")]

    text = planner.plan(
        tool_prompt_mode="per_turn_append",
        send_messages=list(session.provider_messages),
        session_state=session,
        tool_defs=tool_defs,
        active_dialect_names=["pythonic"],
        local_tools_prompt="<tool/>",
        markdown_logger=markdown_logger,
        append_text_block=_append,
        current_native_tools=None,
    )

    assert text is not None
    assert "SYSTEM MESSAGE - AVAILABLE TOOLS" in text
    assert markdown_logger.tools[-1] == ["write", "bash"]


def test_system_compiled_mode_injects_per_turn_block() -> None:
    planner = ToolPromptPlanner()
    session = DummySessionState()
    tool_defs = [SimpleNamespace(name="write")]
    native_tool = SimpleNamespace(name="native_helper")

    text = planner.plan(
        tool_prompt_mode="system_compiled_and_persistent_per_turn",
        send_messages=list(session.provider_messages),
        session_state=session,
        tool_defs=tool_defs,
        active_dialect_names=["pythonic"],
        local_tools_prompt="",
        markdown_logger=None,
        append_text_block=_append,
        current_native_tools=[native_tool],
    )

    assert text is not None
    assert "NATIVE TOOLS AVAILABLE" in text
