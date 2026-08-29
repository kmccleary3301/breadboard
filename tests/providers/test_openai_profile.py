from __future__ import annotations

import pickle
import types

import pytest

from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.conductor.modes import (
    _bind_episode_provider_profile,
    _provider_wire_evidence,
)
from breadboard_engine.provider import sdk_bindings
from breadboard_engine.provider.contracts import (
    OpenAICompletionsCapabilities,
    OpenAICompletionsProviderProfile,
    ProviderContractError,
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
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
        "caller_headers": {"X-Request-ID": "episode-one"},
    }
    values.update(overrides)
    return OpenAICompletionsProviderProfile(**values)


def _runtime():
    return OpenAIChatRuntime(
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
    runtime = _runtime()
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
    monkeypatch.setattr(
        sdk_bindings.provider_sdk_bindings,
        "openai",
        lambda **_kwargs: object(),
    )
    client = runtime.create_client_from_profile(profile)
    context = ProviderRuntimeContext(None, {}, stream=True, provider_profile=profile)
    result = runtime.invoke(
        client=client,
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
        base_url="https://episode-secret.provider.example/v1",
        caller_headers={"X-Custom-Trace": "also-secret"},
    )
    second = _profile(
        base_url="https://episode-secret.provider.example/v1",
        caller_headers={"X-Custom-Trace": "also-secret"},
    )
    identity = first.identity_dict()
    assert first.identity_json() == second.identity_json()
    assert "episode-secret" not in first.identity_json()
    assert "also-secret" not in first.identity_json()
    assert "scoped_credential" not in identity
    assert "base_url" not in identity
    assert "caller_headers" not in identity
    assert identity["base_url_sha256"]
    assert identity["caller_header_names_sha256"]


def test_profile_rejects_nonzero_retry_and_unsupported_tools():
    with pytest.raises(ProviderContractError):
        _profile(compatibility={"sdk_max_retries": 1})
    with pytest.raises(ProviderContractError):
        _profile(scoped_credential="")
    with pytest.raises(ProviderContractError):
        _profile(base_url="https://provider.example/v1?api_key=secret")
    with pytest.raises(ProviderContractError):
        _profile(
            capabilities=OpenAICompletionsCapabilities(
                supports_tools=False,
                supports_strict_tools=True,
                supports_stream_options=True,
                supports_thinking_control=True,
                supports_store=False,
                supports_n=True,
                supports_max_tokens=True,
            )
        )
    with pytest.raises(ProviderContractError):
        _profile(caller_headers={"Authorization": "Bearer override"})
    with pytest.raises(ProviderContractError):
        _profile(caller_headers={"Host": "attacker.example"})
    with pytest.raises(ProviderContractError):
        _profile(context_window=1)
    profile = _profile()
    with pytest.raises(ProviderContractError):
        profile.chat_request([("user", "bad")], None)
    with pytest.raises(ProviderContractError):
        profile.chat_request([], [{"type": "function"}])


def test_profile_client_sets_sdk_retries_to_zero(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(sdk_bindings.provider_sdk_bindings, "openai", FakeOpenAI)
    runtime = _runtime()
    runtime.create_client_from_profile(_profile())
    assert captured["api_key"] == "episode-secret"
    assert captured["base_url"] == "http://127.0.0.1:8111/v1"
    assert captured["max_retries"] == 0
    assert "Authorization" not in captured["default_headers"]


def test_profile_binds_production_runtime_client(monkeypatch):
    created = []

    class FakeTransport:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    transport = FakeTransport()

    def create_transport(**_kwargs):
        created.append(transport)
        return transport

    monkeypatch.setattr(
        sdk_bindings.provider_sdk_bindings,
        "openai",
        create_transport,
    )
    profile = _profile()
    episode = types.SimpleNamespace(_episode_provider_profile=profile)
    runtime = _runtime()
    client, stream, bound_profile = _bind_episode_provider_profile(
        episode,
        runtime,
        object(),
        MODEL,
        False,
    )
    rebound_client, _, _ = _bind_episode_provider_profile(
        episode,
        runtime,
        object(),
        MODEL,
        False,
    )

    assert stream is True
    assert bound_profile is profile
    assert client is rebound_client
    assert client.transport is transport
    assert client.profile is profile
    assert created == [transport]
    client.close()
    assert transport.close_calls == 1


def test_profile_binding_is_scoped_to_each_episode(monkeypatch):
    monkeypatch.setattr(
        sdk_bindings.provider_sdk_bindings,
        "openai",
        lambda **_kwargs: object(),
    )
    runtime = _runtime()
    first = _profile(scoped_credential="first-secret")
    second = _profile(scoped_credential="second-secret")

    first_client, _, first_bound = _bind_episode_provider_profile(
        types.SimpleNamespace(_episode_provider_profile=first),
        runtime,
        object(),
        MODEL,
        False,
    )
    second_client, _, second_bound = _bind_episode_provider_profile(
        types.SimpleNamespace(_episode_provider_profile=second),
        runtime,
        object(),
        MODEL,
        False,
    )

    assert first_bound is first
    assert first_client.profile is first
    assert second_bound is second
    assert second_client.profile is second


def test_profile_client_is_bound_to_one_episode(monkeypatch):
    monkeypatch.setattr(
        sdk_bindings.provider_sdk_bindings,
        "openai",
        lambda **_kwargs: object(),
    )
    runtime = _runtime()
    first = _profile(base_url="http://127.0.0.1:8111/v1", scoped_credential="one")
    second = _profile(base_url="http://127.0.0.1:8222/v1", scoped_credential="two")
    first_client = runtime.create_client_from_profile(first)

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=first_client,
            model=MODEL,
            messages=[],
            tools=None,
            stream=True,
            context=ProviderRuntimeContext(
                None,
                {},
                stream=True,
                provider_profile=second,
            ),
        )

    assert exc_info.value.safe_code == "profile_client_mismatch"


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


def test_profile_is_pickle_safe_for_ray_actor_admission():
    profile = _profile()

    restored = pickle.loads(pickle.dumps(profile))

    assert restored == profile
    assert dict(restored.caller_headers) == {"X-Request-ID": "episode-one"}
    assert restored.scoped_credential == "episode-secret"
    assert "episode-secret" not in repr(restored)
    assert "episode-one" not in repr(restored)


def test_profile_response_is_sanitized_inside_secret_scope(monkeypatch):
    profile = _profile(caller_headers={"X-Request-ID": "caller-secret"})
    runtime = _runtime()
    emitted = []
    recorded = []
    session_state = types.SimpleNamespace(
        _emit_event=lambda event_type, payload, turn: emitted.append(
            (event_type, payload, turn)
        )
    )
    exchange_recorder = types.SimpleNamespace(
        record=lambda kind, payload: recorded.append((kind, payload))
    )

    def leaking_invoke(**kwargs):
        runtime._stream_emit_event(
            kwargs["context"],
            "assistant.message.delta",
            {"text": "episode-secret caller-secret"},
            turn_index=0,
        )
        return ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content="episode-secret caller-secret",
                )
            ],
            raw_response={"echo": "episode-secret caller-secret"},
        )

    monkeypatch.setattr(runtime, "_invoke", leaking_invoke)

    result = runtime.invoke(
        client=object(),
        model=MODEL,
        messages=[],
        tools=None,
        stream=True,
        context=ProviderRuntimeContext(
            session_state,
            {},
            stream=True,
            exchange_recorder=exchange_recorder,
            provider_profile=profile,
        ),
    )

    rendered = repr(result)
    assert "episode-secret" not in rendered
    assert "caller-secret" not in rendered
    assert "episode-secret" not in repr(emitted)
    assert "caller-secret" not in repr(emitted)
    assert "episode-secret" not in repr(recorded)
    assert "caller-secret" not in repr(recorded)


def test_profile_wire_evidence_records_exact_authoritative_request():
    profile = _profile(
        sampling={
            "temperature": 0.6,
            "top_p": 0.95,
            "seed": 7,
        }
    )
    messages = [{"role": "user", "content": "fixture"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object"},
                "strict": True,
            },
        }
    ]

    body, headers, endpoint, identity = _provider_wire_evidence(
        profile=profile,
        provider_id="openai",
        model=MODEL,
        messages=messages,
        tools=tools,
        stream=False,
        client_config={
            "base_url": "https://wrong.example/v1",
            "default_headers": {"X-Wrong": "wrong"},
        },
    )

    assert body == profile.chat_request(messages, tools)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 32_000
    assert body["n"] == 1
    assert body["temperature"] == 0.6
    assert body["top_p"] == 0.95
    assert body["seed"] == 7
    assert body["enable_thinking"] is False
    assert body["tools"][0]["function"]["strict"] is False
    assert endpoint == f"sha256:{identity['base_url_sha256']}"
    assert identity == profile.identity_dict()
    assert headers == {
        "Authorization": "***REDACTED***",
        "X-Request-ID": "***REDACTED***",
    }
    assert "episode-secret" not in repr((body, headers, endpoint, identity))


@pytest.mark.parametrize(
    ("credential", "header_value"),
    [
        ("episode-secret", "caller-secret"),
        ("xy", "z"),
    ],
)
def test_profile_wire_evidence_redacts_echoes_and_raw_endpoint(
    credential,
    header_value,
):
    profile = _profile(
        scoped_credential=credential,
        base_url="http://127.0.0.1:8111/caller-secret/v1",
        caller_headers={"X-Request-ID": header_value},
    )

    evidence = _provider_wire_evidence(
        profile=profile,
        provider_id="openai",
        model=MODEL,
        messages=[{"role": "user", "content": f"{credential} {header_value}"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": f"{credential} {header_value}",
                    "parameters": {"type": "object"},
                },
            }
        ],
        stream=True,
        client_config={},
    )

    assert credential not in repr(evidence[0])
    assert header_value not in repr(evidence[0])
    assert profile.base_url not in repr(evidence)
    assert evidence[2] == f"sha256:{profile.identity_dict()['base_url_sha256']}"


def test_rejected_episode_does_not_retain_provider_profile():
    conductor_class = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_class)
    conductor._active_session_state = None

    with pytest.raises(ProviderContractError, match="run context requires"):
        conductor.run_agentic_loop(
            "",
            "",
            MODEL,
            context=None,
            provider_profile=_profile(),
        )

    assert conductor._active_session_state is None


def test_setup_failure_does_not_retain_provider_profile(tmp_path):
    conductor_class = OpenAIConductor.__ray_metadata__.modified_class
    conductor = conductor_class(
        workspace=str(tmp_path / "workspace"),
        config={},
        local_mode=True,
    )
    conductor._ensure_capability_probes = lambda *_args: (_ for _ in ()).throw(
        AssertionError("profile-bound episodes must not perform capability probes")
    )

    with pytest.raises(AttributeError):
        conductor.run_agentic_loop(
            "",
            "",
            MODEL,
            completion_config="invalid",
            context={
                "session_id": "session",
                "input_id": "input",
                "turn_id": "turn",
            },
            provider_profile=_profile(),
        )

    assert conductor._active_session_state is None
