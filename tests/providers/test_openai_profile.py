from __future__ import annotations

import json
import pickle
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from breadboard.product.runtime.artifacts import ArtifactStore
from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.conductor.modes import (
    _bind_episode_provider_profile,
    _finalize_model_surface,
    _provider_wire_evidence,
    _surface_digest,
)
from breadboard_engine.provider import sdk_bindings
from breadboard_engine.provider.runtimes.openai import chat as chat_module
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


class _LoopbackOpenAIServer:
    def __init__(self):
        self.requests = []
        self.request_started = threading.Event()
        self.release_first_request = threading.Event()
        self.block_first_request = False
        self.status_code = 200
        self.reply_messages = []
        self._lock = threading.Lock()
        self._handler_threads = []
        self._server = None

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                with owner._lock:
                    owner._handler_threads.append(threading.current_thread())
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length))
                with owner._lock:
                    owner.requests.append(
                        {
                            "body": body,
                            "path": self.path,
                        }
                    )
                    first_request = len(owner.requests) == 1
                    request_index = len(owner.requests) - 1
                owner.request_started.set()
                if owner.block_first_request and first_request:
                    owner.release_first_request.wait(timeout=5)

                if owner.status_code >= 400:
                    response = {
                        "error": {
                            "message": "loopback failure",
                            "type": "server_error",
                        }
                    }
                else:
                    response = {
                        "id": "chatcmpl-loopback",
                        "object": "chat.completion",
                        "created": 1,
                        "model": body["model"],
                        "choices": [
                            {
                                "index": 0,
                                "message": (
                                    owner.reply_messages[request_index]
                                    if owner.reply_messages
                                    else {"role": "assistant", "content": "done"}
                                ),
                                "finish_reason": (
                                    "tool_calls"
                                    if owner.reply_messages
                                    and owner.reply_messages[request_index].get("tool_calls")
                                    else "stop"
                                ),
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(owner.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="openai-profile-loopback",
            daemon=True,
        )
        self._thread.start()

    def close(self):
        self.release_first_request.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()
        with self._lock:
            handler_threads = tuple(self._handler_threads)
        for handler_thread in handler_threads:
            handler_thread.join(timeout=5)
            assert not handler_thread.is_alive()

@pytest.fixture
def loopback_openai_server():
    server = _LoopbackOpenAIServer()
    server.start()
    try:
        yield server
    finally:
        server.close()

def _serial_profile(server, **overrides):
    values = {
        "base_url": server.base_url,
        "model": "opaque/vendor/model",
        "wire_mode": "serial_nonstreaming",
        "context_window": 4096,
        "max_output_tokens": 512,
    }
    values.update(overrides)
    return _profile(**values)

def _invoke_serial(runtime, profile, client, *, stream=False):
    return runtime.invoke(
        client=client,
        model=profile.model,
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        stream=stream,
        context=ProviderRuntimeContext(
            None,
            {},
            stream=stream,
            provider_profile=profile,
        ),
    )

def test_profile_serial_mode_uses_exact_nonstreaming_wire(loopback_openai_server):
    runtime = _runtime()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a value",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    profile = _serial_profile(
        loopback_openai_server,
        sampling={
            "temperature": 0.2,
            "top_p": 0.8,
            "seed": 7,
            "frequency_penalty": 0.1,
            "presence_penalty": -0.1,
        },
    )
    client = runtime.create_client_from_profile(profile)
    try:
        result = runtime.invoke(
            client=client,
            model=profile.model,
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            stream=False,
            context=ProviderRuntimeContext(
                None,
                {},
                stream=False,
                provider_profile=profile,
            ),
        )
    finally:
        client.close()

    assert result.messages[0].content == "done"
    assert len(loopback_openai_server.requests) == 1
    request = loopback_openai_server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"] == {
        "model": "opaque/vendor/model",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up a value",
                    "parameters": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                        "required": ["key"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "stream": False,
        "n": 1,
        "max_tokens": 512,
        "temperature": 0.2,
        "top_p": 0.8,
        "seed": 7,
        "frequency_penalty": 0.1,
        "presence_penalty": -0.1,
    }



@pytest.mark.parametrize("tool_prompt_mode", ["none", "per_turn_append"])
def test_serial_conductor_tool_turn_preserves_wire_history(
    tmp_path, loopback_openai_server, tool_prompt_mode
):
    loopback_openai_server.reply_messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "read-proof",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": "proof.txt"}),
                },
            }],
        },
        {"role": "assistant", "content": "done"},
    ]
    profile = _serial_profile(loopback_openai_server)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("proof.txt").write_text("actual serial tool observation\n")
    cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = cls(
        workspace=str(workspace),
        config={
            "provider_tools": {"use_native": True},
            "turn_strategy": {"flow": "tool_role", "relay": "tool_role"},
        },
        local_mode=True,
    )
    result = conductor.run_agentic_loop(
        "Complete the requested file operation.",
        "Read proof.txt, then finish.",
        profile.model,
        max_steps=2,
        tool_prompt_mode=tool_prompt_mode,
        completion_config={},
        context={"session_id": "serial-tool", "input_id": "input", "turn_id": "turn"},
        provider_profile=profile,
    )
    assert result["completed"] is True
    first, second = [request["body"] for request in loopback_openai_server.requests]
    assert first["model"] == second["model"] == profile.model
    assert first["stream"] is second["stream"] is False
    previous = first["messages"]
    assert second["messages"][:len(previous)] == previous
    appended = second["messages"][len(previous):]
    assert [message["role"] for message in appended] == ["assistant", "tool"]
    assert appended[1]["tool_call_id"] == "read-proof"
    assert "actual serial tool observation" in appended[1]["content"]


@pytest.mark.parametrize("status_code", [503, 429])
def test_profile_serial_mode_does_not_retry_http_errors(
    loopback_openai_server, status_code
):
    # 503 exercises SDK retries; 429 also reaches the runtime's retry loop.
    loopback_openai_server.status_code = status_code
    profile = _serial_profile(loopback_openai_server)
    runtime = _runtime()
    client = runtime.create_client_from_profile(profile)
    try:
        with pytest.raises(ProviderRuntimeError):
            _invoke_serial(runtime, profile, client)
    finally:
        client.close()

    assert len(loopback_openai_server.requests) == 1

def test_profile_client_mismatch_rejects_before_http_request(loopback_openai_server):
    runtime = _runtime()
    first = _serial_profile(loopback_openai_server, scoped_credential="first")
    second = _serial_profile(
        loopback_openai_server,
        scoped_credential="second",
    )
    client = runtime.create_client_from_profile(first)
    try:
        with pytest.raises(ProviderRuntimeError) as raised:
            runtime.invoke(
                client=client,
                model=second.model,
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                stream=False,
                context=ProviderRuntimeContext(
                    None,
                    {},
                    stream=False,
                    provider_profile=second,
                ),
            )
    finally:
        client.close()

    assert raised.value.safe_code == "profile_client_mismatch"
    assert loopback_openai_server.requests == []

def test_profile_serial_client_rejects_concurrent_invocation(loopback_openai_server):
    runtime = _runtime()
    profile = _serial_profile(loopback_openai_server)
    loopback_openai_server.block_first_request = True
    client = runtime.create_client_from_profile(profile)
    first_result = []
    first_errors = []

    def invoke_first():
        try:
            first_result.append(_invoke_serial(runtime, profile, client))
        except Exception as exc:
            first_errors.append(exc)

    first_thread = threading.Thread(target=invoke_first, daemon=True)
    try:
        first_thread.start()
        assert loopback_openai_server.request_started.wait(timeout=5)
        with pytest.raises(ProviderRuntimeError) as raised:
            _invoke_serial(runtime, profile, client)
        assert raised.value.safe_code == "profile_concurrent_invocation"
        assert len(loopback_openai_server.requests) == 1
        loopback_openai_server.release_first_request.set()
        first_thread.join(timeout=5)
        assert not first_thread.is_alive()
    finally:
        loopback_openai_server.release_first_request.set()
        first_thread.join(timeout=5)
        client.close()

    assert first_errors == []
    assert len(first_result) == 1


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

def test_profile_request_provenance_separates_requested_default_and_adapter_facts():
    profile = _profile()
    messages = [{"role": "system", "content": "sys"}]
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

    requested_messages = [
        {
            "role": "user",
            "content": [{"type": "image", "source": "canonical-media"}],
        }
    ]
    provenance = profile.chat_request_provenance(
        messages,
        tools,
        requested_stream=False,
        requested_messages=requested_messages,
    )

    assert provenance["messages"]["status"] == "requested"
    assert provenance["messages"]["source"] == "session.model_history"
    assert provenance["messages"]["requested_digest"] == _surface_digest(
        requested_messages
    )
    assert provenance["messages"]["effective_digest"] == _surface_digest(
        messages
    )
    assert provenance["tools"]["status"] == "requested"
    assert provenance["tools"]["source"] == "provider.tool_registry"
    assert provenance["n"] == {
        "status": "default",
        "source": "OpenAICompletionsSampling.n",
        "effective": 1,
        "uncertainty": None,
    }
    assert provenance["stream"] == {
        "status": "adapter",
        "source": "openai_chat.profile",
        "requested": False,
        "effective": True,
        "uncertainty": None,
    }
    assert provenance["tools[0].function.strict"]["status"] == "adapter"
    assert provenance["tools[0].function.strict"]["effective"] is False
    assert all(item.get("uncertainty") is None for item in provenance.values())


def test_profile_provenance_distinguishes_explicit_n_from_default():
    profile = _profile(sampling={"n": 1})

    provenance = profile.chat_request_provenance([], None)

    assert provenance["n"] == {
        "status": "effective",
        "source": "lock.provider_profile.sampling.n",
        "effective": 1,
        "uncertainty": None,
    }




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


def test_profile_stream_keeps_a_length_limited_completion_as_a_truncated_turn():
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "partial answer"}, "finish_reason": None}]},
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]
    body = b"".join(
        b"data: "
        + json.dumps({"id": "chatcmpl-length", "object": "chat.completion.chunk", "created": 1, "model": MODEL, **chunk}).encode()
        + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = _profile(base_url=f"http://127.0.0.1:{server.server_port}/v1")
        runtime = _runtime()
        result = runtime.invoke(
            client=runtime.create_client_from_profile(profile),
            model=MODEL,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            stream=True,
            context=ProviderRuntimeContext(None, {}, stream=True, provider_profile=profile),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert [(message.content, message.finish_reason) for message in result.messages] == [
        ("partial answer", "length")
    ]


def test_profile_client_close_aborts_a_request_blocked_on_its_response():
    # Closing an httpx client does not wake a thread blocked reading a response;
    # the episode's close would wait out the whole provider read timeout.
    received = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.set()
            release.wait(30)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    outcome: list[BaseException] = []
    try:
        profile = _profile(base_url=f"http://127.0.0.1:{server.server_port}/v1")
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=60)

        def invoke():
            try:
                runtime.invoke(
                    client=client,
                    model=MODEL,
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    stream=True,
                    context=ProviderRuntimeContext(None, {}, stream=True, provider_profile=profile),
                )
            except BaseException as exc:
                outcome.append(exc)

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        assert received.wait(10)
        client.close()
        worker.join(5)
        assert not worker.is_alive()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
    assert len(outcome) == 1 and isinstance(outcome[0], ProviderRuntimeError)


def test_profile_identity_is_deterministic_and_secret_free():
    first = _profile(
        base_url="https://episode-secret.provider.example/v1",
        caller_headers={"X-Custom-Trace": "also-secret"},
    )
    second = _profile(
        base_url="https://episode-secret.provider.example/v1",
        caller_headers={"X-Custom-Trace": "also-secret"},
    )
    different_value = _profile(
        base_url="https://episode-secret.provider.example/v1",
        caller_headers={"X-Custom-Trace": "different-secret"},
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
    assert (
        identity["caller_header_names_sha256"]
        == different_value.identity_dict()["caller_header_names_sha256"]
    )
    assert (
        identity["caller_headers_sha256"]
        != different_value.identity_dict()["caller_headers_sha256"]
    )


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
    http_options = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeHttpClient:
        def __init__(self, **kwargs):
            http_options.update(kwargs)
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(sdk_bindings.provider_sdk_bindings, "openai", FakeOpenAI)
    monkeypatch.setattr(chat_module.httpx, "Client", FakeHttpClient)
    runtime = _runtime()
    client = runtime.create_client_from_profile(_profile())
    assert captured["api_key"] == "episode-secret"
    assert captured["base_url"] == "http://127.0.0.1:8111/v1"
    assert captured["max_retries"] == 0
    assert captured["http_client"] is client.http_client
    assert http_options["trust_env"] is False
    assert http_options["follow_redirects"] is False
    assert "Authorization" not in captured["default_headers"]
    client.close()
    assert client.http_client.closed


def test_profile_client_applies_explicit_bounded_transport_timeout(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        sdk_bindings.provider_sdk_bindings,
        "openai",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    runtime = _runtime()

    client = runtime.create_client_from_profile(_profile(), timeout_seconds=30)

    assert captured["timeout"] == 30.0
    with pytest.raises(ProviderRuntimeError) as raised:
        runtime.create_client_from_profile(_profile(), timeout_seconds=0)
    assert raised.value.details["code"] == "invalid_provider_timeout"
    client.close()


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
    runtime = _runtime()
    context = ProviderRuntimeContext(None, {}, provider_profile=profile)

    body, headers, endpoint, identity, exact_body = _provider_wire_evidence(
        profile=profile,
        runtime=runtime,
        provider_id="openai",
        model=MODEL,
        messages=messages,
        tools=tools,
        stream=False,
        client_config={
            "base_url": "https://wrong.example/v1",
            "default_headers": {"X-Wrong": "wrong"},
        },
        context=context,
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
    assert exact_body == body
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

    runtime = _runtime()
    context = ProviderRuntimeContext(None, {}, provider_profile=profile)
    evidence = _provider_wire_evidence(
        profile=profile,
        runtime=runtime,
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
        context=context,
    )

    assert credential not in repr(evidence[0])
    assert header_value not in repr(evidence[0])
    assert profile.base_url not in repr(evidence[:4])
    assert credential in repr(evidence[4])
    assert header_value in repr(evidence[4])
    assert _surface_digest(evidence[4]) != _surface_digest(evidence[0])
    assert evidence[2] == f"sha256:{profile.identity_dict()['base_url_sha256']}"


def test_profile_wire_evidence_matches_media_and_tool_result_projection(tmp_path):
    workspace = tmp_path / "workspace"
    artifact = ArtifactStore(workspace / ".breadboard" / "artifacts").put(
        b"\x89PNG\r\n\x1a\nprofile-evidence",
        media_type="image/png",
    )
    uri = f"attachment://{artifact.digest}"
    metadata = {"attachment_capabilities": {uri: artifact.as_dict()}}
    session_state = types.SimpleNamespace(
        workspace=str(workspace),
        get_provider_metadata=lambda key, default=None: metadata.get(key, default),
    )
    profile = _profile()
    runtime = _runtime()
    context = ProviderRuntimeContext(
        session_state,
        {},
        provider_profile=profile,
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "thinking", "text": "inspect image"},
                {
                    "type": "media",
                    "kind": "image",
                    "uri": uri,
                    "mime": "image/png",
                },
            ],
        },
        {
            "role": "tool_result",
            "content": [
                {
                    "type": "tool_result",
                    "call_id": "call_1",
                    "content": {"status": "ok"},
                }
            ],
        },
    ]

    body, _, _, _, _ = _provider_wire_evidence(
        profile=profile,
        runtime=runtime,
        provider_id="openai",
        model=MODEL,
        messages=messages,
        tools=None,
        stream=True,
        client_config={},
        context=context,
    )

    assert body == runtime.profile_chat_request(
        profile,
        messages,
        None,
        context=context,
    )
    assert body["messages"][0]["content"][0]["type"] == "image_url"
    assert body["messages"][0]["reasoning_content"] == "inspect image"
    assert body["messages"][1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"status":"ok"}',
    }




def test_unbound_openai_surface_digest_matches_runtime_wire_projection(tmp_path):
    workspace = tmp_path / "workspace"
    artifact = ArtifactStore(workspace / ".breadboard" / "artifacts").put(
        b"\x89PNG\r\n\x1a\nunbound-surface",
        media_type="image/png",
    )
    uri = f"attachment://{artifact.digest}"
    metadata = {"attachment_capabilities": {uri: artifact.as_dict()}}
    session_state = types.SimpleNamespace(
        workspace=str(workspace),
        get_provider_metadata=lambda key, default=None: metadata.get(key, default),
    )
    context = ProviderRuntimeContext(session_state, {}, provider_profile=None)
    runtime = _runtime()
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "media",
                    "kind": "image",
                    "uri": uri,
                    "mime": "image/png",
                }
            ],
        },
        {
            "role": "tool_result",
            "content": [
                {
                    "type": "tool_result",
                    "call_id": "call_1",
                    "content": {"status": "ok"},
                }
            ],
        },
    ]
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

    body, _, _, _, _ = _provider_wire_evidence(
        profile=None,
        runtime=runtime,
        provider_id="openai",
        model=MODEL,
        messages=messages,
        tools=tools,
        stream=True,
        client_config={},
        context=context,
    )
    surface = _finalize_model_surface(
        {"prompt_sections": {}, "tools": []},
        body["messages"],
        body["tools"],
        "",
        body,
    )

    assert body["messages"] == runtime._convert_messages_to_chat(
        messages, context=context
    )
    assert body["tools"] == runtime._convert_tools_to_openai(tools)
    assert surface is not None
    assert surface["provider_request"] == {
        "messages_sha256": _surface_digest(body["messages"]),
        "tools_sha256": _surface_digest(body["tools"]),
        "request_sha256": _surface_digest(body),
    }
def test_unbound_openai_wire_evidence_includes_role_and_provider_options():
    runtime = OpenAIChatRuntime(
        ProviderDescriptor(
            provider_id="openrouter",
            runtime_id="openai_chat",
            default_api_variant="chat",
            supports_native_tools=True,
            supports_streaming=True,
            supports_reasoning_traces=True,
            supports_cache_control=False,
            tool_schema_format="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key_env=None,
            default_headers={},
        )
    )
    context = ProviderRuntimeContext(
        types.SimpleNamespace(set_provider_metadata=lambda *_args: None),
        {
            "active_model_role": "worker",
            "model_role_lock": {
                "roles": {
                    "worker": {
                        "generation": {
                            "temperature": 0.25,
                            "max_output_tokens": 321,
                        },
                        "reasoning": {"mode": "disabled"},
                    }
                }
            },
        },
        stream=True,
    )

    body, _, _, _, _ = _provider_wire_evidence(
        profile=None,
        runtime=runtime,
        provider_id="openrouter",
        model="openai/gpt-5-mini",
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=True,
        client_config={},
        context=context,
    )

    assert body["temperature"] == 0.25
    assert body["max_completion_tokens"] == 321
    assert body["reasoning"] == {"effort": "none"}
    assert body["provider"] == {
        "order": ["openai"],
        "allow_fallbacks": False,
    }


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
