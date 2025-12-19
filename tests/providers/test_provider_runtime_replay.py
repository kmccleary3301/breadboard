from __future__ import annotations

import types

import pytest

from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import (
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    provider_registry,
)


class _DummySessionState:
    def __init__(self) -> None:
        self._metadata = {}

    def get_provider_metadata(self, key: str, default=None):
        return self._metadata.get(key, default)

    def set_provider_metadata(self, key: str, value) -> None:
        self._metadata[key] = value


def _make_replay_runtime():
    descriptor, model = provider_router.get_runtime_descriptor("replay")
    runtime = provider_registry.create_runtime(descriptor)
    return runtime, model


def test_replay_runtime_requires_session_state():
    runtime, model = _make_replay_runtime()
    context = ProviderRuntimeContext(session_state=None, agent_config={})  # type: ignore[arg-type]

    with pytest.raises(ProviderRuntimeError) as excinfo:
        runtime.invoke(
            client={"replay": True},
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            stream=False,
            context=context,
        )

    assert "requires a valid session_state" in str(excinfo.value)


def test_replay_runtime_requires_active_session():
    runtime, model = _make_replay_runtime()
    session_state = _DummySessionState()
    # No active_replay_session set
    context = ProviderRuntimeContext(session_state=session_state, agent_config={})

    with pytest.raises(ProviderRuntimeError) as excinfo:
        runtime.invoke(
            client={"replay": True},
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            stream=False,
            context=context,
        )

    assert "No active ReplaySession found" in str(excinfo.value)


def test_replay_runtime_happy_path_delegates_to_session():
    runtime, model = _make_replay_runtime()

    # Build a minimal ProviderResult that a ReplaySession might return
    provider_message = types.SimpleNamespace(
        role="assistant",
        content="ok",
        tool_calls=[],
        finish_reason="stop",
        index=0,
        raw_message=None,
        raw_choice=None,
        reasoning=None,
        annotations={},
    )
    base_result = ProviderResult(
        messages=[provider_message],
        raw_response={"id": "replay_1"},
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        model="upstream-model",
        metadata={},
    )

    class FakeReplaySession:
        def __init__(self) -> None:
            self.calls = []

        def next_result(self, *, messages, tools):
            # Record that we saw the input and return the base ProviderResult
            self.calls.append({"messages": messages, "tools": tools})
            return base_result

    session_state = _DummySessionState()
    session_state.set_provider_metadata("active_replay_session", FakeReplaySession())
    context = ProviderRuntimeContext(session_state=session_state, agent_config={})

    result = runtime.invoke(
        client={"replay": True},
        model=model,
        messages=[{"role": "user", "content": "Hi"}],
        tools=[{"name": "dummy_tool"}],
        stream=False,
        context=context,
    )

    # The result should be the same object with replay metadata applied
    assert result is base_result
    assert result.metadata.get("replay") is True
    assert result.model == "replay-playback"
    assert result.messages[0].content == "ok"

