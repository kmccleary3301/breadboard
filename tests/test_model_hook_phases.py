from __future__ import annotations

from agentic_coder_prototype.conductor_loop import _call_model_with_hooks
from agentic_coder_prototype.hooks.model import HookResult
from agentic_coder_prototype.messaging.markdown_logger import MarkdownLogger
from agentic_coder_prototype.provider_runtime import ProviderResult
from agentic_coder_prototype.state.session_state import SessionState


class DummyHookManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, phase, payload, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(phase)


class DummyConductor:
    def __init__(self, hook_manager=None) -> None:
        self.hook_manager = hook_manager
        self.called = 0

    def _get_model_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.called += 1
        return ProviderResult(messages=[], raw_response=None)


def test_model_hook_phases_called() -> None:
    hook_manager = DummyHookManager()
    conductor = DummyConductor(hook_manager=hook_manager)
    session_state = SessionState(workspace=".", image="test")
    result = _call_model_with_hooks(
        conductor,
        runtime=None,
        client=None,
        model="test-model",
        tool_prompt_mode="auto",
        tool_defs=[],
        active_dialect_names=[],
        session_state=session_state,
        markdown_logger=MarkdownLogger(),
        stream_responses=False,
        local_tools_prompt="",
        client_config={},
        turn_index=1,
    )
    assert isinstance(result, ProviderResult)
    assert conductor.called == 1
    assert hook_manager.calls == ["before_model", "after_model"]


def test_model_hook_phases_no_manager() -> None:
    conductor = DummyConductor(hook_manager=None)
    session_state = SessionState(workspace=".", image="test")
    result = _call_model_with_hooks(
        conductor,
        runtime=None,
        client=None,
        model="test-model",
        tool_prompt_mode="auto",
        tool_defs=[],
        active_dialect_names=[],
        session_state=session_state,
        markdown_logger=MarkdownLogger(),
        stream_responses=False,
        local_tools_prompt="",
        client_config={},
        turn_index=1,
    )
    assert isinstance(result, ProviderResult)
    assert conductor.called == 1


class TransformHookManager:
    def __init__(self, messages_override) -> None:  # type: ignore[no-untyped-def]
        self.calls: list[str] = []
        self._messages_override = messages_override

    def run(self, phase, payload, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(phase)
        if phase == "before_model":
            return HookResult(action="transform", payload={"messages": self._messages_override})
        return HookResult(action="allow")


class AssertingConductor(DummyConductor):
    def __init__(self, hook_manager=None, *, expect_override=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(hook_manager=hook_manager)
        self.expect_override = expect_override

    def _get_model_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        override = None
        try:
            override = args[6].get_provider_metadata("__send_messages_override")  # session_state
        except Exception:
            override = None
        assert override == self.expect_override
        return super()._get_model_response(*args, **kwargs)


def test_model_hook_before_model_transform_sets_messages_override() -> None:
    override = [{"role": "user", "content": "override"}]
    hook_manager = TransformHookManager(override)
    conductor = AssertingConductor(hook_manager=hook_manager, expect_override=override)
    session_state = SessionState(workspace=".", image="test")
    result = _call_model_with_hooks(
        conductor,
        runtime=None,
        client=None,
        model="test-model",
        tool_prompt_mode="auto",
        tool_defs=[],
        active_dialect_names=[],
        session_state=session_state,
        markdown_logger=MarkdownLogger(),
        stream_responses=False,
        local_tools_prompt="",
        client_config={},
        turn_index=1,
    )
    assert isinstance(result, ProviderResult)
    assert conductor.called == 1
    assert hook_manager.calls == ["before_model", "after_model"]
    assert session_state.get_provider_metadata("__send_messages_override") is None
