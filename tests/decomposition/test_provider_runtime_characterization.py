from __future__ import annotations

import pickle
import types
from typing import Any

import pytest

import breadboard_engine.provider.runtime as runtime_module
import breadboard_engine.provider_runtime as root_runtime
from breadboard_engine.provider.routing import provider_router

from breadboard_engine.provider.sdk_bindings import provider_sdk_bindings
from breadboard_engine.provider.builtins import register_builtin_runtimes

EXPECTED_EXPORTS = (
    "ProviderRuntime",
    "ProviderRuntimeContext",
    "ProviderRuntimeError",
    "ProviderRuntimeRegistry",
    "ProviderResult",
    "ProviderMessage",
    "ProviderToolCall",
    "provider_registry",
    "OpenAIChatRuntime",
    "OpenAIResponsesRuntime",
    "AnthropicMessagesRuntime",
    "CodexAppServerRuntime",
    "MockRuntime",
    "ReplayRuntime",
)
EXPECTED_DIRECT_RUNTIME_NAMES = EXPECTED_EXPORTS + (
    "OpenAIBaseRuntime",
    "SmokeRuntime",
    "CliMockRuntime",
)

# D-P0 inventory: these are the exact monkeypatch target paths found by grepping
# tests/ before extraction. D-P3 migrates these consumers to ProviderSdkBindings.
CURRENT_MONKEYPATCH_PATHS = (
    "breadboard_engine.provider_runtime.Anthropic",
    "breadboard_engine.provider_runtime.AnthropicOverloadedError",
    "breadboard_engine.provider_runtime.OpenAI",
    "breadboard_engine.provider_runtime.time.sleep",
)
EXPECTED_MOVED_RUNTIME_MODULES = {
    "OpenAIBaseRuntime": "breadboard_engine.provider.runtimes.openai",
    "OpenAIChatRuntime": "breadboard_engine.provider.runtimes.openai",
    "OpenAIResponsesRuntime": "breadboard_engine.provider.runtimes.openai",
    "AnthropicMessagesRuntime": "breadboard_engine.provider.runtimes.anthropic",
}


class _SessionState:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = dict(metadata or {})
        self.writes: list[tuple[str, Any]] = []

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value
        self.writes.append((key, value))


class _RawResponse:
    def __init__(self, parsed: Any, *, status_code: int = 200) -> None:
        self._parsed = parsed
        self.headers = {"content-type": "application/json"}
        self.status_code = status_code
        self.content = b"{}"

    def parse(self) -> Any:
        return self._parsed


class _RawCollection:
    def __init__(self, response: _RawResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.with_raw_response = self

    def create(self, **kwargs: Any) -> _RawResponse:
        self.calls.append(kwargs)
        return self.response


class _Context:
    def __init__(self, *, agent_config: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.session_state = _SessionState(metadata)
        self.agent_config = agent_config or {}
        self.stream = False
        self.extra: dict[str, Any] = {}


def _descriptor(runtime_id: str, provider_id: str) -> Any:
    return types.SimpleNamespace(provider_id=provider_id, runtime_id=runtime_id)


def test_complete_export_set_and_direct_runtime_names() -> None:
    assert tuple(runtime_module.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_DIRECT_RUNTIME_NAMES:
        assert hasattr(runtime_module, name), name

def test_d_p4_moved_runtime_pickle_modules_are_intentional() -> None:
    for name, module_name in EXPECTED_MOVED_RUNTIME_MODULES.items():
        assert getattr(runtime_module, name).__module__ == module_name

def test_builtin_registry_keys_and_registration_order() -> None:
    assert list(runtime_module.provider_registry._runtime_classes) == [
        "openai_chat",
        "openrouter_chat",
        "openai_responses",
        "anthropic_messages",
        "mock_chat",
        "smoke_chat",
        "cli_mock_chat",
        "replay",
        "codex_app_server",
    ]


def test_registry_is_singleton_across_canonical_and_root_facades() -> None:
    assert runtime_module.provider_registry is root_runtime.provider_registry
    assert runtime_module.provider_registry is runtime_module.provider_registry
    assert root_runtime is runtime_module


def test_builtin_registration_is_idempotent() -> None:
    before = list(runtime_module.provider_registry._runtime_classes.items())
    register_builtin_runtimes()
    assert list(runtime_module.provider_registry._runtime_classes.items()) == before


def test_optional_sdk_missing_errors_have_current_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    openai_runtime = runtime_module.OpenAIChatRuntime(_descriptor("openai_chat", "openai"))
    anthropic_runtime = runtime_module.AnthropicMessagesRuntime(_descriptor("anthropic_messages", "anthropic"))

    monkeypatch.setattr(provider_sdk_bindings, "openai", None)
    with pytest.raises(runtime_module.ProviderRuntimeError, match=r"^openai package not installed$"):
        openai_runtime.create_client("key")

    monkeypatch.setattr(provider_sdk_bindings, "anthropic", None)
    with pytest.raises(runtime_module.ProviderRuntimeError, match=r"^anthropic package not installed$"):
        anthropic_runtime.create_client("key")


def test_openai_chat_exact_request_payload_and_normalized_result() -> None:
    runtime = runtime_module.OpenAIChatRuntime(_descriptor("openai_chat", "openai"))
    raw = _RawCollection(
        _RawResponse(
            types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message={"role": "assistant", "content": "hello", "tool_calls": []},
                        finish_reason="stop",
                        index=0,
                    )
                ],
                usage={"prompt_tokens": 2, "completion_tokens": 1},
                model="gpt-test",
            )
        )
    )
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=raw))
    tools = [
        {
            "type": "function",
            "function": {"name": "lookup", "description": "Lookup", "parameters": {"type": "object"}},
        }
    ]

    result = runtime.invoke(
        client=client,
        model="gpt-test",
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}],
        tools=tools,
        stream=False,
        context=_Context(),
    )

    assert raw.calls == [
        {
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ],
            "tools": tools,
            "stream": False,
            "extra_body": None,
        }
    ]
    assert isinstance(result, runtime_module.ProviderResult)
    assert result.model == "gpt-test"
    assert result.usage == {"prompt_tokens": 2, "completion_tokens": 1}
    assert len(result.messages) == 1
    assert result.messages[0].role == "assistant"
    assert result.messages[0].content == "hello"
    assert result.messages[0].finish_reason == "stop"


def test_openai_responses_exact_request_payload_and_normalized_result() -> None:
    runtime = runtime_module.OpenAIResponsesRuntime(_descriptor("openai_responses", "openai"))
    raw = _RawCollection(
        _RawResponse(
            types.SimpleNamespace(
                id="resp-1",
                model="gpt-responses-test",
                status="completed",
                output=[
                    types.SimpleNamespace(
                        type="message",
                        role="assistant",
                        content=[{"type": "output_text", "text": "done"}],
                        finish_reason=None,
                    )
                ],
                usage={"input_tokens": 3, "output_tokens": 2},
            )
        )
    )
    client = types.SimpleNamespace(responses=raw)
    context = _Context(agent_config={"provider_tools": {"openai": {"include_reasoning": False}}})
    result = runtime.invoke(
        client=client,
        model="gpt-responses-test",
        messages=[{"role": "system", "content": "instructions"}, {"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    assert raw.calls == [
        {
            "model": "gpt-responses-test",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            ],
            "instructions": "instructions",
        }
    ]
    assert isinstance(result, runtime_module.ProviderResult)
    assert result.metadata == {"previous_response_id": "resp-1"}
    assert result.messages[0].content == "done"
    assert result.messages[0].finish_reason == "stop"


def test_anthropic_exact_request_payload_and_normalized_result() -> None:
    runtime = runtime_module.AnthropicMessagesRuntime(_descriptor("anthropic_messages", "anthropic"))
    raw = _RawCollection(
        _RawResponse(
            types.SimpleNamespace(
                content=[{"type": "text", "text": "bonjour"}],
                stop_reason="end_turn",
                model="claude-test",
                usage={"input_tokens": 4, "output_tokens": 2},
            )
        )
    )
    client = types.SimpleNamespace(messages=raw)
    tools = [{"name": "lookup", "description": "Lookup", "input_schema": {"type": "object"}}]
    context = _Context(
        agent_config={
            "provider_tools": {
                "anthropic": {
                    "max_output_tokens": 256,
                    "temperature": 0.2,
                    "tool_choice": "required",
                    "extra_headers": {"x-test": "yes"},
                }
            }
        }
    )
    result = runtime.invoke(
        client=client,
        model="claude-test",
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}],
        tools=tools,
        stream=False,
        context=context,
    )

    assert raw.calls == [
        {
            "model": "claude-test",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            "max_tokens": 256,
            "system": "system",
            "tools": tools,
            "tool_choice": {"type": "any"},
            "extra_headers": {"x-test": "yes"},
            "temperature": 0.2,
        }
    ]
    assert isinstance(result, runtime_module.ProviderResult)
    assert result.model == "claude-test"
    assert result.messages[0].content == "bonjour"
    assert result.messages[0].finish_reason == "end_turn"
    assert result.usage == {"input_tokens": 4, "output_tokens": 2}


def test_retry_timing_uses_current_sleep_and_uniform_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_module.OpenAIResponsesRuntime(_descriptor("openai_responses", "openai"))
    sleeps: list[float] = []
    monkeypatch.setattr(provider_sdk_bindings, "sleep", sleeps.append)

    class RateLimitError(Exception):
        def __init__(self) -> None:
            super().__init__("rate limited")
            self.response = types.SimpleNamespace(status_code=429, headers={"retry-after": "0.25"}, text="busy")

    class RateLimitedCollection:
        def __init__(self) -> None:
            self.with_raw_response = self
            self.calls = 0

        def create(self, **_: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise RateLimitError()
            return _RawResponse(types.SimpleNamespace(output=[], model="gpt-test", usage={}))

    collection = RateLimitedCollection()
    result = runtime._call_with_raw_response(
        collection,
        error_context="responses.create",
        context=_Context(),
        model="gpt-test",
        input=[],
    )
    assert result.model == "gpt-test"
    assert sleeps == [0.25]
    assert collection.calls == 2

    monkeypatch.setattr(provider_sdk_bindings, "uniform", lambda lower, upper: upper)
    assert runtime_module.AnthropicMessagesRuntime(_descriptor("anthropic_messages", "anthropic"))._compute_rate_limit_retry_delay(
        {"retry_base_seconds": 1.5, "retry_jitter_seconds": 0.25}, 1, None
    ) == 3.25


def test_current_monkeypatch_paths_are_recorded() -> None:
    assert tuple(sorted(CURRENT_MONKEYPATCH_PATHS)) == (
        "breadboard_engine.provider_runtime.Anthropic",
        "breadboard_engine.provider_runtime.AnthropicOverloadedError",
        "breadboard_engine.provider_runtime.OpenAI",
        "breadboard_engine.provider_runtime.time.sleep",
    )


def test_representative_classes_pickle_round_trip() -> None:
    descriptor = _descriptor("mock_chat", "mock")
    representatives = [
        runtime_module.ProviderMessage(role="assistant", content="hello"),
        runtime_module.ProviderResult(messages=[], raw_response={"ok": True}),
        runtime_module.MockRuntime(descriptor),
        runtime_module.OpenAIChatRuntime(descriptor),
    ]
    for value in representatives:
        round_tripped = pickle.loads(pickle.dumps(value))
        assert type(round_tripped) is type(value)
        if isinstance(value, (runtime_module.ProviderMessage, runtime_module.ProviderResult)):
            assert round_tripped == value


def test_registry_creates_normalized_mock_result_shape() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor("mock/test")
    runtime = runtime_module.provider_registry.create_runtime(descriptor)
    result = runtime.invoke(
        client=None,
        model="mock-model",
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
        context=_Context(),
    )
    assert isinstance(result, runtime_module.ProviderResult)
    assert result.messages and isinstance(result.messages[0], runtime_module.ProviderMessage)
    assert result.messages[0].role == "assistant"
    assert result.messages[0].content is None or isinstance(result.messages[0].content, str)
