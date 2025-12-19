import types

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.state.session_state import SessionState


class _DummyMarkdownLogger:
    def __init__(self):
        self.messages = []

    def log_system_message(self, message: str) -> None:
        self.messages.append(message)


def _make_conductor_instance():
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.logger_v2 = types.SimpleNamespace(run_dir=None, append_text=lambda *args, **kwargs: None)
    inst.md_writer = types.SimpleNamespace(system=lambda text: text)
    inst.config = {}
    return inst


def test_stream_policy_forces_stream_off_on_openrouter_tool_turn():
    conductor = _make_conductor_instance()
    session_state = SessionState(workspace=".", image="img")
    markdown_logger = _DummyMarkdownLogger()

    runtime = types.SimpleNamespace(
        descriptor=types.SimpleNamespace(provider_id="openrouter", runtime_id="openrouter_chat")
    )

    effective, policy = conductor._apply_streaming_policy_for_turn(
        runtime,
        "openrouter/openai/gpt-4o",
        tools_schema=[{"function": {"name": "do_something"}}],
        stream_requested=True,
        session_state=session_state,
        markdown_logger=markdown_logger,
        turn_index=1,
    )

    assert effective is False
    assert policy is not None
    assert policy["reason"] == "openrouter_tool_turn_policy"
    assert session_state.transcript[-1]["stream_policy"] == policy
    assert markdown_logger.messages
    history = session_state.get_provider_metadata("stream_policy_history")
    assert isinstance(history, list) and policy in history
    assert session_state.get_provider_metadata("last_stream_policy") == policy


def test_stream_policy_no_change_when_not_tool_turn():
    conductor = _make_conductor_instance()
    session_state = SessionState(workspace=".", image="img")
    markdown_logger = _DummyMarkdownLogger()

    runtime = types.SimpleNamespace(
        descriptor=types.SimpleNamespace(provider_id="openrouter", runtime_id="openrouter_chat")
    )

    effective, policy = conductor._apply_streaming_policy_for_turn(
        runtime,
        "openrouter/openai/gpt-4o",
        tools_schema=None,
        stream_requested=True,
        session_state=session_state,
        markdown_logger=markdown_logger,
        turn_index=1,
    )

    assert effective is True
    assert policy is None
    assert session_state.transcript == []
    assert markdown_logger.messages == []
