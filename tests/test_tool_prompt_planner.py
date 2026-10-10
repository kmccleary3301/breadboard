from __future__ import annotations
from contextlib import nullcontext

from types import SimpleNamespace

from breadboard_engine.conductor.prompt_planner import ToolPromptPlanner


class DummySessionState:
    def __init__(self) -> None:
        self.provider_messages = [{"role": "user", "content": "context"}]

    @staticmethod
    def context_mutation():
        return nullcontext()


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


def test_tool_directives_preserve_correlated_result_payload() -> None:
    from copy import deepcopy

    tool_result = {"role": "tool", "tool_call_id": "call_read", "content": "file contents\n"}
    for mode in ("none", "per_turn_append", "system_compiled_and_persistent_per_turn"):
        session = DummySessionState()
        session.provider_messages = [deepcopy(tool_result)]
        send_messages = deepcopy(session.provider_messages)
        text = ToolPromptPlanner().plan(
            tool_prompt_mode=mode,
            send_messages=send_messages,
            session_state=session,
            tool_defs=[SimpleNamespace(name="Read")],
            active_dialect_names=[],
            local_tools_prompt="<tool/>",
            markdown_logger=None,
            append_text_block=_append,
            current_native_tools=[SimpleNamespace(name="Read")],
        )
        assert send_messages[0] == session.provider_messages[0] == tool_result
        if mode == "none":
            assert text is None and send_messages == [tool_result]
        else:
            assert send_messages[-1]["role"] == "user" and send_messages[-1]["content"]
        if mode == "system_compiled_and_persistent_per_turn":
            assert session.provider_messages[-1] == send_messages[-1]


def test_text_tool_directives_keep_base_assistant_role_and_message_count() -> None:
    from copy import deepcopy

    rendering = {"role": "assistant", "content": "\n\nTool execution results:\nresult"}
    for mode in ("per_turn_append", "system_compiled_and_persistent_per_turn"):
        session = DummySessionState()
        session.provider_messages = [deepcopy(rendering)]
        send_messages = deepcopy(session.provider_messages)
        text = ToolPromptPlanner().plan(
            tool_prompt_mode=mode,
            send_messages=send_messages,
            session_state=session,
            tool_defs=[SimpleNamespace(name="Read")],
            active_dialect_names=[],
            local_tools_prompt="<tool/>",
            markdown_logger=None,
            append_text_block=_append,
            current_native_tools=None,
        )
        assert text
        suffix = "\n\n" + text if mode == "system_compiled_and_persistent_per_turn" else text
        assert send_messages == [{"role": "assistant", "content": rendering["content"] + suffix}]
        assert len(session.provider_messages) == 1
        if mode == "system_compiled_and_persistent_per_turn":
            assert session.provider_messages == send_messages
        else:
            assert session.provider_messages == [rendering]


def test_request_stub_preserves_non_responses_whitespace_user(monkeypatch) -> None:
    import pytest

    from breadboard_engine.conductor.modes import get_model_response
    from breadboard_engine.state.session_state import SessionState

    class RequestPrepared(Exception):
        pass

    for runtime_id, variant, expected in (
        ("mock", "chat", " \t\n"),
        ("anthropic_messages", "messages", " \t\n"),
        ("openai_responses", "responses", "Continue."),
    ):
        session = SessionState("ws", "image", {})
        # Only the first user gets prompt caching; the continuation remains text.
        session.add_message({"role": "user", "content": "context"})
        session.add_message({"role": "user", "content": " \t\n"})
        prepared = []

        def capture_request(messages):
            prepared.extend(messages)
            raise RequestPrepared

        monkeypatch.setattr(session, "record_provider_request_surface", capture_request)
        conductor = SimpleNamespace(
            config={},
            tool_prompt_planner=ToolPromptPlanner(),
            current_native_tools=[],
            _append_text_block=_append,
        )
        runtime = SimpleNamespace(
            descriptor=SimpleNamespace(runtime_id=runtime_id, default_api_variant=variant),
        )
        with pytest.raises(RequestPrepared):
            get_model_response(
                conductor, runtime, None, "mock/model", "none", [], [],
                session, None, False, "", {},
            )
        assert len(prepared) == 2
        assert prepared[0] == {
            "role": "user",
            "content": [{"type": "text", "text": "context", "cache_control": {"type": "ephemeral"}}],
        }
        assert prepared[-1] == {"role": "user", "content": expected}
        assert session.provider_messages == [
            {"role": "user", "content": "context"},
            {"role": "user", "content": " \t\n"},
        ]
