"""Episode profile authority exercised through the actual SDK HTTP transport."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from breadboard_engine.conductor.modes import _bind_episode_provider_profile
from breadboard_engine.provider.contracts import (
    OpenAICompletionsProviderProfile,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.provider.runtimes.openai import OpenAIChatRuntime

MODEL = "Qwen/Qwen3.5-35B-A3B"


def _runtime():
    return OpenAIChatRuntime(ProviderDescriptor(
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
    ))


def _profile(base_url, *, credential="episode-secret", cap=128, temperature=0.2):
    return OpenAICompletionsProviderProfile(
        model=MODEL,
        base_url=base_url + "/v1",
        scoped_credential=credential,
        context_window=8192,
        max_output_tokens=cap,
        sampling={"temperature": temperature},
        caller_headers={"X-Request-ID": credential + "-request"},
    )


def _completion():
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}},
    ]
    body = b"".join(
        b"data: " + json.dumps({
            "id": "chatcmpl-profile", "object": "chat.completion.chunk",
            "created": 1, "model": MODEL, **chunk,
        }).encode() + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"
    return 200, "text/event-stream", body


def _invoke(runtime, client, profile):
    return runtime.invoke(
        client=client,
        model=MODEL,
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        stream=True,
        context=ProviderRuntimeContext(None, {}, stream=True, provider_profile=profile),
    )


def test_episode_profile_credentials_and_compiled_policy_reach_http(sdk_wire_server):
    requests = []

    def reply(request):
        requests.append(request)
        return _completion()

    base_url = sdk_wire_server(reply)
    runtime = _runtime()
    profiles = [
        _profile(base_url, credential="first-secret", cap=128, temperature=0.2),
        _profile(base_url, credential="second-secret", cap=256, temperature=0.7),
    ]
    clients = []
    try:
        for profile in profiles:
            episode = SimpleNamespace(_episode_provider_profile=profile)
            for _ in range(2):
                client, stream, bound = _bind_episode_provider_profile(
                    episode, runtime, None, MODEL, False,
                )
                if client not in clients:
                    clients.append(client)
                assert stream is True
                result = _invoke(runtime, client, bound)
                assert result.messages[0].content == "done"
    finally:
        for client in clients:
            client.close()

    assert len(requests) == 4
    for request, profile in zip(requests, [profiles[0], profiles[0], profiles[1], profiles[1]], strict=True):
        assert request.path == "/v1/chat/completions"
        headers = {name.lower(): value for name, value in request.headers.items()}
        assert headers["authorization"] == "Bearer " + profile.scoped_credential
        assert headers["x-request-id"] == profile.caller_headers["X-Request-ID"]
        assert json.loads(request.content) == {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": profile.max_output_tokens,
            "n": 1,
            "temperature": profile.sampling.temperature,
            "enable_thinking": False,
        }


def test_profile_client_rejects_cross_episode_authority_before_http(sdk_wire_server):
    requests = []

    def reply(request):
        requests.append(request)
        return _completion()

    base_url = sdk_wire_server(reply)
    runtime = _runtime()
    first = _profile(base_url, credential="first-secret")
    second = _profile(base_url, credential="second-secret")
    client = runtime.create_client_from_profile(first)
    try:
        with pytest.raises(ProviderRuntimeError) as raised:
            _invoke(runtime, client, second)
        assert raised.value.safe_code == "profile_client_mismatch"
        assert requests == []
    finally:
        client.close()


def test_episode_profile_does_not_retry_failed_http(sdk_wire_server):
    requests = []

    def reply(request):
        requests.append(request)
        return 503, "application/json", b'{"error":{"message":"unavailable","type":"server_error"}}'

    runtime = _runtime()
    profile = _profile(sdk_wire_server(reply))
    client = runtime.create_client_from_profile(profile, timeout_seconds=30)
    try:
        with pytest.raises(ProviderRuntimeError):
            _invoke(runtime, client, profile)
        assert len(requests) == 1
    finally:
        client.close()


@pytest.mark.parametrize("timeout", [0, -1, True, "30", 3601])
def test_profile_rejects_invalid_bounded_timeout(timeout):
    runtime = _runtime()
    profile = _profile("http://127.0.0.1:8111")
    with pytest.raises(ProviderRuntimeError) as raised:
        runtime.create_client_from_profile(profile, timeout_seconds=timeout)
    assert raised.value.details["code"] == "invalid_provider_timeout"
