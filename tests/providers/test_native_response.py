from __future__ import annotations

import hashlib
import json
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.policy_provider import EpisodeOpenAICompletionsPolicyClient
from breadboard.rl.harness.runners.base import (
    PolicyRuntimeInvokeRequest,
    RunnerPolicyBindingError,
    freeze_json_object,
)

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard_engine.compilation.contracts import bytes_sha256, canonical_sha256
from breadboard_engine.compilation.provider_response import (
    MINI_RESPONSE_CONSUMER_ID,
    NativeResponseBindingError,
    admit_native_response_binding,
    profile_identity_digest,
)
from breadboard_engine.provider.contract_wire import canonical_json as wire_canonical_json
from breadboard_engine.provider.contracts import (
    OpenAICompletionsProviderProfile,
    ProviderContractError,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from breadboard_engine.provider.native_response import NativeRecordingConsumer
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.e4_targets import load_e4_target
from tests.compilation.test_server_compiler import _compile, _options
from tests.rl.harness.test_policy_provider import _observation as _owned_observation
from tests.rl.harness.test_runner_policy_runtime import _plan as _execution_plan


_MODEL = "fixture/opaque-model:01"
_ARGUMENT_PARTS = (
    (" { ", '"command": "echo ok"', " } \n"),
    ('{"command":"printf ', r"\u0061", '"}'),
    ('{"command"', ":"),
)
_ARGUMENTS = tuple("".join(parts) for parts in _ARGUMENT_PARTS)
_TOOLS = [{"type": "function", "function": {
    "name": "bash", "parameters": {"type": "object"},
}}]


def _runtime():
    return OpenAIChatRuntime(ProviderDescriptor(
        provider_id="openai",
        runtime_id="openai_chat",
        default_api_variant="chat",
        supports_native_tools=True,
        supports_streaming=True,
        supports_reasoning_traces=False,
        supports_cache_control=False,
        tool_schema_format="openai",
        base_url=None,
        api_key_env=None,
        default_headers={},
    ))


def _response_body() -> dict:
    return {
        "id": "native-response",
        "object": "chat.completion",
        "created": 1,
        "model": _MODEL,
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": f"call-{index}", "type": "function", "function": {
                        "name": "bash", "arguments": arguments,
                    }}
                    for index, arguments in enumerate(_ARGUMENTS)
                ],
            },
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }


def _stream_body() -> bytes:
    deltas = [{"role": "assistant", "content": ""}]
    for index, parts in enumerate(_ARGUMENT_PARTS):
        for part_index, part in enumerate(parts):
            tool = {"index": index, "function": {"arguments": part}}
            if part_index == 0:
                tool.update(id=f"call-{index}", type="function")
                tool["function"]["name"] = "bash"
            deltas.append({"tool_calls": [tool]})
    chunks = [
        {"id": "native-response", "object": "chat.completion.chunk", "created": 1,
         "model": _MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        for delta in deltas
    ]
    chunks.append({
        "id": "native-response", "object": "chat.completion.chunk", "created": 1,
        "model": _MODEL,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
    })
    return b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"


@contextmanager
def _receiver(*, response_payload: bytes | None = None):
    credential = secrets.token_urlsafe(32)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            authenticated = self.headers.get("Authorization") == f"Bearer {credential}"
            requests.append((self.path, authenticated, body))
            payload = response_payload
            if payload is None:
                payload = _stream_body() if body.get("stream") else json.dumps(_response_body()).encode()
            self.send_response(200 if authenticated else 401)
            self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", credential, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _native_manifest(
    profile,
    *,
    fragment_limit=64,
    max_response_bytes=65_536,
    name="native-response-fixture",
):
    policy = {
        "schema_version": "bb.provider_native_response_policy.v1",
        "consumer_id": "breadboard.provider.recording.v1",
        "provider_profile_digest": profile_identity_digest(profile),
        "max_response_bytes": max_response_bytes,
        "max_stream_fragments": fragment_limit,
    }
    config = {
        "version": 2,
        "profile": {"name": name},
        "workspace": {"root": "workspace"},
        "providers": {
            "default_model": "fixture-authority",
            "models": [{"id": "fixture-authority", "adapter": "openai",
                        "context_length": profile.context_window, "response_policy": policy}],
        },
        "prompts": {"injection": {"system_order": [], "per_turn_order": []}},
        "modes": [{"id": "build", "prompt": ""}],
        "loop": {"sequence": ["build"]},
    }
    manifest, _, _ = _compile({"config.yaml": json.dumps(config).encode()})
    return manifest


def _binding(
    profile,
    *,
    fragment_limit=64,
    max_response_bytes=65_536,
    authority_model_id="fixture-authority",
):
    manifest = _native_manifest(
        profile,
        fragment_limit=fragment_limit,
        max_response_bytes=max_response_bytes,
    )
    # Unit-fixture context identities, not a production capability observation.
    observation = canonical_sha256({"fixture": "native-http", "profile": profile.identity_dict()})
    plan = canonical_sha256({"fixture": "native-http", "manifest": manifest.compiled_manifest_digest})
    return admit_native_response_binding(
        manifest.canonical_bytes(),
        expected_compiler_input_digest=manifest.inputs.compiler_input_digest,
        authority_model_id=authority_model_id,
        profile=profile,
        capability_observation_digest=observation,
        episode_id="native-episode",
        effective_plan_digest=plan,
    )


def _profile(base_url, credential, streaming):
    return OpenAICompletionsProviderProfile(
        model=_MODEL, scoped_credential=credential, base_url=base_url,
        context_window=32_768, max_output_tokens=1_024,
        request_policy={
            "mode": "streaming" if streaming else "non_streaming",
            "include_usage": streaming,
            "strict_tools": False,
            "enable_thinking": None,
        },
        capabilities={"supports_non_streaming": True},
    )


def _context(profile, binding):
    return ProviderRuntimeContext(
        None, {}, stream=profile.request_policy.stream, provider_profile=profile,
        session_id=binding.episode_id,
        effective_plan_digest=binding.effective_plan_digest,
        capability_observation_digest=binding.capability_observation_digest,
    )


@pytest.mark.parametrize(
    ("delta", "safe_code"),
    [
        ({"role": "user"}, "invalid_chat_role"),
        ({"refusal": "unsupported"}, "unsupported_chat_delta"),
    ],
)
def test_stream_rejects_unsupported_delta_in_normalized_and_native_paths(delta, safe_code):
    chunks = [
        {
            "id": "semantic-rejection", "object": "chat.completion.chunk",
            "created": 1, "model": _MODEL,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "hello", **delta},
                "finish_reason": None,
            }],
        },
        {
            "id": "semantic-rejection", "object": "chat.completion.chunk",
            "created": 1, "model": _MODEL,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    payload = b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
    payload += b"data: [DONE]\n\n"
    with _receiver(response_payload=payload) as (base_url, credential, requests):
        profile = _profile(base_url, credential, True)
        binding = _binding(profile)
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        try:
            for native in (False, True):
                kwargs = {
                    "client": client, "model": _MODEL,
                    "messages": [{"role": "user", "content": "inspect response"}],
                    "tools": _TOOLS, "stream": True, "context": _context(profile, binding),
                }
                if native:
                    kwargs["binding"] = binding
                invoke = runtime.invoke_native if native else runtime.invoke
                with pytest.raises(ProviderRuntimeError) as raised:
                    invoke(**kwargs)
                assert raised.value.kind == "protocol"
                assert raised.value.safe_code == safe_code
        finally:
            client.close()
        assert len(requests) == 2


def test_nonstream_native_rejects_indices_that_would_reorder_calls():
    body = _response_body()
    calls = body["choices"][0]["message"]["tool_calls"][:2]
    calls[0]["index"], calls[1]["index"] = 1, 0
    body["choices"][0]["message"]["tool_calls"] = calls
    with _receiver(response_payload=json.dumps(body).encode()) as (base_url, credential, requests):
        profile = _profile(base_url, credential, False)
        binding = _binding(profile)
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        try:
            with pytest.raises(ProviderRuntimeError) as raised:
                runtime.invoke_native(
                    client=client, model=_MODEL,
                    messages=[{"role": "user", "content": "preserve call order"}],
                    tools=_TOOLS, stream=False,
                    context=_context(profile, binding), binding=binding,
                )
            assert raised.value.safe_code == "invalid_chat_tool_index"
        finally:
            client.close()
        assert len(requests) == 1


def test_native_admission_rejects_an_uncompiled_authority():
    profile = _profile("http://127.0.0.1:1/v1", "unit-fixture", False)
    with pytest.raises(NativeResponseBindingError):
        _binding(profile, authority_model_id="uncompiled-authority")


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [None, "foreign_manifest", "effective_model"])
async def test_owned_native_recording_joins_the_actual_plan(corruption):
    with _receiver() as (base_url, credential, requests):
        profile = _profile(base_url, credential, True)
        manifest = _native_manifest(profile)
        observed = _owned_observation().model_dump(mode="json")
        observed["model_id"] = "fixture-authority"
        observation = c.PolicyCapabilityObservation.model_validate(observed)
        semantics = manifest.semantic.to_dict()
        if corruption == "effective_model":
            semantics["providers"]["models"][0]["context_length"] += 1
        # Self-consistent unit context, not an installed admission receipt.
        plan_payload = _execution_plan(
            observation=observation, semantics=semantics
        ).model_dump(mode="json")
        plan_payload["base_compiled"]["manifest_digest"] = bytes_sha256(manifest.canonical_bytes())
        plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
        native_manifest = (
            _native_manifest(profile, name="foreign-manifest")
            if corruption == "foreign_manifest" else manifest
        )
        binding = admit_native_response_binding(
            native_manifest.canonical_bytes(),
            expected_compiler_input_digest=native_manifest.inputs.compiler_input_digest,
            authority_model_id=observation.model_id,
            profile=profile,
            capability_observation_digest=observation.canonical_digest(),
            episode_id="native-episode",
            effective_plan_digest=plan.canonical_digest(),
        )
        payload = {
            "model": observation.model_id,
            "instructions": "Record the native response without dispatching tools.",
            "input": [{"role": "user", "content": "sample native calls"}],
            "tools": [],
        }
        request = PolicyRuntimeInvokeRequest(
            episode_id=binding.episode_id,
            effective_plan_digest=plan.canonical_digest(),
            binding_digest=canonical_sha256({"unit": "policy-binding"}),
            policy_slot_id=plan.policy_slots[0].slot_id,
            request_digest=canonical_sha256(payload),
            request_payload=freeze_json_object(payload, field_name="native test request"),
            turn=1,
            attempt=1,
        )
        client = EpisodeOpenAICompletionsPolicyClient(
            episode_id=binding.episode_id,
            effective_plan_digest=plan.canonical_digest(),
            observation=observation,
            profile=profile,
            timeout_seconds=3,
            max_requests=1,
        )
        try:
            if corruption is not None:
                with pytest.raises(RunnerPolicyBindingError) as failure:
                    await client.invoke_native(request, binding=binding, effective_plan=plan)
                assert failure.value.code == "native_response_binding_invalid"
                assert client.request_attempts == 0
                assert requests == []
            else:
                response = await client.invoke_native(
                    request, binding=binding, effective_plan=plan
                )
                assert tuple(call.arguments for call in response.tool_calls) == _ARGUMENTS
                assert client.request_attempts == 1
                assert len(requests) == 1
                assert requests[0][2]["model"] == _MODEL
        finally:
            await client.close()


@pytest.mark.parametrize("streaming", [False, True])
def test_compiled_native_response_precedes_argument_normalization(streaming):
    with _receiver() as (base_url, credential, requests):
        profile = _profile(base_url, credential, streaming)
        binding = _binding(profile)
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        context = _context(profile, binding)
        messages = [{"role": "user", "content": "sample native calls"}]
        try:
            response = runtime.invoke_native(
                client=client, model=_MODEL, messages=messages, tools=_TOOLS,
                stream=streaming, context=context, binding=binding,
            )
            consumer = NativeRecordingConsumer()
            recorded = consumer.record(response)
            with pytest.raises(ProviderContractError):
                consumer.record(response)
            assert consumer.responses == (recorded,)
            assert tuple(call.arguments for call in recorded.tool_calls) == _ARGUMENTS
            assert tuple(call.id for call in recorded.tool_calls) == ("call-0", "call-1", "call-2")
            if streaming:
                assert tuple(fragment.text for fragment in recorded.stream_fragments) == (
                    "", *(part for parts in _ARGUMENT_PARTS for part in parts),
                )
            else:
                assert recorded.usage["prompt_tokens"] == 3
                with pytest.raises(TypeError):
                    recorded.usage["prompt_tokens"] = 99
                detached = recorded.as_dict()
                detached["usage"]["prompt_tokens"] = 99
                assert recorded.usage["prompt_tokens"] == 3
            with pytest.raises((ProviderContractError, ProviderRuntimeError)):
                runtime.invoke(
                    client=client, model=_MODEL, messages=messages, tools=_TOOLS,
                    stream=streaming, context=context,
                )
        finally:
            client.close()
        assert len(requests) == 2
        assert recorded.request_digest == canonical_sha256(requests[0][2]).removeprefix("sha256:")
        for path, authenticated, body in requests:
            assert path == "/v1/chat/completions"
            assert authenticated
            assert body["model"] == _MODEL
            assert body["stream"] is streaming
            assert ("stream_options" in body) is streaming


def test_mini_nonstream_digest_is_of_the_body_actually_sent(tmp_path):
    with _receiver() as (base_url, credential, requests):
        profile = OpenAICompletionsProviderProfile(
            model=_MODEL, scoped_credential=credential, base_url=base_url,
            context_window=32_768, max_output_tokens=2048,
            sampling={"temperature": 0.0, "n": 1},
            request_policy={
                "mode": "non_streaming", "include_usage": False,
                "strict_tools": None, "enable_thinking": None,
            },
            capabilities={"supports_non_streaming": True},
        )
        cas = FilesystemCAS(tmp_path / "cas")
        try:
            manifest = compile_e4_harness(
                load_e4_target("mini-swe-agent@2.4.6"),
                {},
                {
                    "version": 2,
                    "profile": {"name": "mini-request-digest"},
                    "workspace": {"root": "workspace"},
                    "provider_tools": {"use_native": True},
                    "providers": {
                        "default_model": "fixture-authority",
                        "models": [{
                            "id": "fixture-authority", "adapter": "openai",
                            "context_length": profile.context_window, "params": {},
                            "response_policy": {
                                "schema_version": "bb.provider_native_response_policy.v1",
                                "consumer_id": MINI_RESPONSE_CONSUMER_ID,
                                "provider_profile_digest": profile_identity_digest(profile),
                                "max_response_bytes": 65_536,
                                "max_stream_fragments": 1,
                            },
                        }],
                    },
                },
                cas=cas,
                options=_options(),
                request_schema_version="bb.rl.headless-run-request.v2",
            ).manifest
        finally:
            cas.close()
        binding = admit_native_response_binding(
            manifest.canonical_bytes(),
            expected_compiler_input_digest=manifest.inputs.compiler_input_digest,
            authority_model_id="fixture-authority",
            profile=profile,
            capability_observation_digest=canonical_sha256({"fixture": "mini-digest"}),
            episode_id="native-episode",
            effective_plan_digest=canonical_sha256({"fixture": "mini-digest-plan"}),
        )
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        try:
            response = runtime.invoke_native(
                client=client, model=_MODEL,
                messages=[{"role": "user", "content": "sample native calls"}],
                tools=_TOOLS, stream=False,
                context=_context(profile, binding), binding=binding,
            )
        finally:
            client.close()
        assert len(requests) == 1
        assert "stream" not in requests[0][2]
        # temperature=0.0 needs the wire encoder the runtime hashes with.
        assert response.request_digest == hashlib.sha256(
            wire_canonical_json(requests[0][2]).encode("utf-8")
        ).hexdigest()


def test_native_response_bound_includes_attached_request_body():
    with _receiver() as (base_url, credential, requests):
        profile = _profile(base_url, credential, False)
        runtime = _runtime()
        high_binding = _binding(profile)
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        try:
            response = runtime.invoke_native(
                client=client,
                model=_MODEL,
                messages=[{"role": "user", "content": "request body is attached"}],
                tools=_TOOLS,
                stream=False,
                context=_context(profile, high_binding),
                binding=high_binding,
            )
            response_without_request = response.as_dict()
            response_without_request.pop("request_body", None)
            max_response_bytes = len(
                wire_canonical_json(response_without_request).encode("utf-8")
            ) + 1
            low_binding = _binding(profile, max_response_bytes=max_response_bytes)
            with pytest.raises(ProviderRuntimeError) as failure:
                runtime.invoke_native(
                    client=client,
                    model=_MODEL,
                    messages=[{"role": "user", "content": "request body is attached"}],
                    tools=_TOOLS,
                    stream=False,
                    context=_context(profile, low_binding),
                    binding=low_binding,
                )
            assert failure.value.details["code"] == "invalid_native_chat_response"
        finally:
            client.close()
        assert len(requests) == 2


def test_native_fragment_limit_stops_retention_without_another_request():
    with _receiver() as (base_url, credential, requests):
        profile = _profile(base_url, credential, True)
        binding = _binding(profile, fragment_limit=2)
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        try:
            with pytest.raises(ProviderRuntimeError) as failure:
                runtime.invoke_native(
                    client=client, model=_MODEL,
                    messages=[{"role": "user", "content": "bounded sample"}],
                    tools=_TOOLS, stream=True, context=_context(profile, binding), binding=binding,
                )
            assert failure.value.details["code"] == "native_fragment_limit_exceeded"
        finally:
            client.close()
        assert len(requests) == 1


# Job-1203 supplier capture `stream_fragments_broken`: four chunks, then EOF with
# neither a finish chunk nor [DONE].
_TRUNCATED_STREAM_EVENTS = [
    {"choices": [{"delta": {"content": "broken stream", "role": "assistant"},
                  "finish_reason": None, "index": 0}],
     "created": 0, "id": "omp-capture-stream-stream_fragments_broken-00",
     "model": _MODEL, "object": "chat.completion.chunk"},
    {"choices": [{"delta": {"content": " mid-argument"}, "finish_reason": None, "index": 0}],
     "created": 0, "id": "omp-capture-stream-stream_fragments_broken-00",
     "model": _MODEL, "object": "chat.completion.chunk"},
    {"choices": [{"delta": {"tool_calls": [{
        "function": {"arguments": "", "name": "bash"},
        "id": "omp-capture-stream_fragments_broken-00-00", "index": 0, "type": "function",
    }]}, "finish_reason": None, "index": 0}],
     "created": 0, "id": "omp-capture-stream-stream_fragments_broken-00",
     "model": _MODEL, "object": "chat.completion.chunk"},
    {"choices": [{"delta": {"tool_calls": [{
        "function": {"arguments": "{\"command\":\"printf 'stream-"}, "index": 0,
    }]}, "finish_reason": None, "index": 0}],
     "created": 0, "id": "omp-capture-stream-stream_fragments_broken-00",
     "model": _MODEL, "object": "chat.completion.chunk"},
]


def _invoke_truncated_stream(**flag):
    payload = b"".join(
        b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in _TRUNCATED_STREAM_EVENTS
    )
    with _receiver(response_payload=payload) as (base_url, credential, requests):
        profile = _profile(base_url, credential, True)
        binding = _binding(profile)
        runtime = _runtime()
        client = runtime.create_client_from_profile(profile, timeout_seconds=3)
        try:
            response = runtime.invoke_native(
                client=client, model=_MODEL,
                messages=[{"role": "user", "content": "cut stream"}],
                tools=_TOOLS, stream=True, context=_context(profile, binding),
                binding=binding, **flag,
            )
        finally:
            client.close()
    assert len(requests) == 1
    return response


def test_accepted_truncated_stream_is_a_typed_termination_with_its_chunks():
    response = _invoke_truncated_stream(accept_truncated_stream=True)
    assert response.finish_reason is None
    assert response.stream_termination.reason == "stream_truncated"
    assert [dict(item) for item in response.as_dict()["stream_termination"]["chunks"]] == (
        _TRUNCATED_STREAM_EVENTS
    )
    assert response.content == "broken stream mid-argument"
    assert [call.as_dict() for call in response.tool_calls] == [{
        "id": "omp-capture-stream_fragments_broken-00-00",
        "name": "bash",
        "arguments": "{\"command\":\"printf 'stream-",
    }]


def test_truncated_stream_without_the_profile_flag_stays_incomplete():
    with pytest.raises(ProviderRuntimeError) as raised:
        _invoke_truncated_stream()
    assert raised.value.safe_code == "incomplete_chat_stream"
