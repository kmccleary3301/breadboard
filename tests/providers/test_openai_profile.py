from __future__ import annotations

import types

import pytest

from breadboard_engine.provider import sdk_bindings
from breadboard_engine.provider.contracts import (
    OpenAICompletionsCapabilities,
    OpenAICompletionsProviderProfile,
    ProviderContractError,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.provider.runtimes.openai import OpenAIChatRuntime

MODEL = "Qwen/Qwen3.5-35B-A3B"


def _profile(**overrides):
    values = {
        "base_url": "http://127.0.0.1:8111/v1",
        "model": MODEL,
        "scoped_credential": "episode-secret",
        "context_window": 131072,
        "max_output_tokens": 32000,
        "caller_headers": {"X-Episode": "one", "Authorization": "Bearer secret"},
    }
    values.update(overrides)
    return OpenAICompletionsProviderProfile(**values)


def test_profile_builds_exact_qwen_stream_request_without_fallback():
    profile = _profile(sampling={"temperature": 0.2})
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object"},
            },
        }
    ]
    request = profile.chat_request(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        tools,
    )
    assert request == {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object"},
                    "strict": False,
                },
            }
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 32000,
        "n": 1,
        "temperature": 0.2,
        "enable_thinking": False,
    }
    assert "store" not in request
    assert "provider" not in request


def test_profile_projects_exact_sdk_stream_request(monkeypatch):
    profile = _profile(sampling={"temperature": 0.2})
    runtime = OpenAIChatRuntime(
        ProviderDescriptor(
            provider_id="openai",
            runtime_id="openai_chat",
            default_api_variant="chat",
            supports_native_tools=True,
            supports_streaming=True,
            supports_reasoning_traces=True,
            supports_cache_control=False,
            tool_schema_format="openai",
            base_url=None,
            api_key_env=None,
            default_headers={},
        )
    )
    captured = {}

    def fake_stream(_client, **kwargs):
        captured.update(kwargs)
        response = types.SimpleNamespace(
            id="chatcmpl-profile",
            choices=[
                types.SimpleNamespace(
                    index=0,
                    message={"role": "assistant", "content": "done", "tool_calls": []},
                    finish_reason="stop",
                    logprobs=None,
                    error=None,
                )
            ],
            model=MODEL,
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        )
        return response, {}

    monkeypatch.setattr(runtime, "_stream_chat_completion", fake_stream)
    context = ProviderRuntimeContext(None, {}, stream=True, provider_profile=profile)
    result = runtime.invoke(
        client=object(),
        model=MODEL,
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        stream=True,
        context=context,
    )

    assert captured["request_options"] == {
        "stream_options": {"include_usage": True},
        "max_tokens": 32000,
        "n": 1,
        "temperature": 0.2,
    }
    assert captured["extra_body"] == {"enable_thinking": False}
    assert "stream" not in captured["request_options"]
    assert "enable_thinking" not in captured["request_options"]
    assert result.messages[0].content == "done"


def test_profile_identity_is_deterministic_and_secret_free():
    first = _profile(
        caller_headers={
            "Authorization": "Bearer episode-secret",
            "X-Custom": "also-secret",
        }
    )
    second = _profile(
        caller_headers={
            "Authorization": "Bearer episode-secret",
            "X-Custom": "also-secret",
        }
    )
    identity = first.identity_dict()
    assert first.identity_json() == second.identity_json()
    assert "episode-secret" not in first.identity_json()
    assert "also-secret" not in first.identity_json()
    assert "scoped_credential" not in identity
    assert set(identity["caller_headers"].values()) == {"***REDACTED***"}


def test_profile_rejects_nonzero_retry_and_unsupported_tools():
    with pytest.raises(ProviderContractError):
        _profile(compatibility={"sdk_max_retries": 1})
    with pytest.raises(ProviderContractError):
        _profile(scoped_credential="")
    with pytest.raises(ProviderContractError):
        _profile(base_url="https://provider.example/v1?api_key=secret")
    profile = _profile(
        capabilities=OpenAICompletionsCapabilities(
            supports_tools=False,
            supports_strict_tools=False,
            supports_stream_options=True,
            supports_thinking_control=True,
            supports_store=False,
            supports_n=True,
            supports_max_tokens=True,
        )
    )
    with pytest.raises(ProviderContractError):
        profile.chat_request([], [{"type": "function"}])


def test_profile_client_sets_sdk_retries_to_zero(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(sdk_bindings.provider_sdk_bindings, "openai", FakeOpenAI)
    runtime = OpenAIChatRuntime(
        ProviderDescriptor(
            provider_id="openai",
            runtime_id="openai_chat",
            default_api_variant="chat",
            supports_native_tools=True,
            supports_streaming=True,
            supports_reasoning_traces=True,
            supports_cache_control=False,
            tool_schema_format="openai",
            base_url=None,
            api_key_env=None,
            default_headers={},
        )
    )
    runtime.create_client_from_profile(_profile())
    assert captured["api_key"] == "episode-secret"
    assert captured["base_url"] == "http://127.0.0.1:8111/v1"
    assert captured["max_retries"] == 0


def test_context_profile_is_episode_scoped():
    first = _profile(base_url="http://127.0.0.1:8111/v1", scoped_credential="one")
    second = _profile(base_url="http://127.0.0.1:8222/v1", scoped_credential="two")
    first_context = ProviderRuntimeContext(None, {}, provider_profile=first)
    second_context = ProviderRuntimeContext(None, {}, provider_profile=second)
    assert first_context.provider_profile is first
    assert second_context.provider_profile is second
    assert (
        first_context.provider_profile.base_url
        != second_context.provider_profile.base_url
    )
    assert (
        first_context.provider_profile.scoped_credential
        != second_context.provider_profile.scoped_credential
    )
