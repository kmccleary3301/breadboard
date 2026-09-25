from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Mapping

import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard_engine.compilation.contracts import canonical_sha256
from breadboard_engine.compilation.provider_response import (
    HERMES_RESPONSE_CONSUMER_ID,
    NATIVE_RESPONSE_CONSUMER_ID,
    OPENHANDS_RESPONSE_CONSUMER_ID,
    NativeResponseBindingError,
    profile_identity_digest,
)
from breadboard_engine.e4_targets import load_e4_target
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import policy_provider as policy_provider_module
from breadboard.rl.harness.policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyClient,
    EpisodeOpenAICompletionsPolicyResolver,
)
from tests.compilation.test_server_compiler import _options as _compile_options
from tests.rl.harness.e4_compiler_test_helper import compile_pi_target
from tests.rl.harness.test_runner_policy_runtime import (
    _observation as _runtime_observation,
    _plan as _runtime_plan,
    _policy_capabilities,
)
from breadboard.rl.harness.runners.base import (
    RunnerDependencyError,
    PolicyRuntimeInvokeRequest,
    RunnerProtocolError,
    RunnerPolicyBindingError,
    freeze_json_object,
    thaw_json,
)
from breadboard_engine.provider.contracts import (
    OpenAICompletionsProviderProfile,
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
)
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime


MODEL = "Qwen/Qwen3.5-35B-A3B"
MODEL_ID = "qwen3.5-35b-a3b"
DIGEST = "sha256:" + "a" * 64


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _observation() -> c.PolicyCapabilityObservation:
    capabilities = c.PolicyCapabilityVector.model_validate(
        {
            "responses_protocol": "responses-v1",
            "modalities": ["text"],
            "tool_calling": True,
            "parallel_tool_calls": False,
            "token_ids": True,
            "token_logprobs": True,
            "routing_metadata": True,
            "cancellation": True,
            "max_context_tokens": 131_072,
            "max_output_tokens": 32_000,
            "policy_slot_count": 1,
            "request_features": [
                "enable_thinking",
                "max_tokens",
                "n",
                "stream_options",
                "streaming",
                "strict_tools",
            ],
        }
    )
    capability_digest = canonical_sha256(
        {
            "schema_version": "bb.rl.policy-selection-capabilities.v1",
            "protocol_abi": "responses-v1",
            "model_digest": _digest("model"),
            "tokenizer_digest": _digest("tokenizer"),
            "checkpoint_digest": _digest("checkpoint"),
            "capabilities": capabilities.model_dump(mode="json"),
        }
    )
    return c.PolicyCapabilityObservation.model_validate(
        {
            "registry_revision_digest": _digest("registry"),
            "route_id": "policy-route",
            "route_revision_digest": _digest("route"),
            "provider_id": "openai",
            "protocol_abi": "responses-v1",
            "bridge_instance_id": "bridge-one",
            "bridge_build_digest": _digest("bridge"),
            "model_id": MODEL_ID,
            "model_digest": _digest("model"),
            "tokenizer_digest": _digest("tokenizer"),
            "checkpoint_digest": _digest("checkpoint"),
            "credential_handle_id": "credential-one",
            "credential_handle_version_digest": _digest("credential"),
            "subject_scope_digest": _digest("subject"),
            "capabilities": capabilities.model_dump(mode="json"),
            "capability_digest": capability_digest,
            "provenance": {
                "kind": "startup_probe",
                "issuer_id": "operator-control-plane",
                "signer_key_id": "startup-probe-key",
                "environment_digest": _digest("environment"),
                "evidence_digest": _digest("evidence"),
                "validity": {
                    "issued_at": "2026-08-29T11:00:00Z",
                    "not_before": "2026-08-29T11:00:00Z",
                    "expires_at": "2026-08-29T13:00:00Z",
                },
            },
            "revocation": {
                "scope_digest": _digest("subject"),
                "epoch": 1,
                "state_digest": _digest("revocation"),
            },
        }
    )


def _profile(credential: str = "episode-secret") -> OpenAICompletionsProviderProfile:
    return OpenAICompletionsProviderProfile(
        model=MODEL,
        scoped_credential=credential,
        base_url="https://provider.example/v1",
        context_window=131_072,
        max_output_tokens=32_000,
        caller_headers={"X-Episode-ID": "episode-one"},
    )


def _request(
    input_items: list[dict[str, Any]], *, turn: int = 1
) -> PolicyRuntimeInvokeRequest:
    payload = {
        "model": MODEL_ID,
        "instructions": "system prompt",
        "input": input_items,
        "tools": [
            {
                "type": "function",
                "name": "read",
                "description": "Read one file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ],
    }
    return PolicyRuntimeInvokeRequest(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        binding_digest=_digest("binding"),
        policy_slot_id="policy-slot",
        request_digest=canonical_sha256(payload),
        request_payload=freeze_json_object(payload, field_name="test request"),
        turn=turn,
        attempt=1,
    )


class _Transport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_profile_client_projects_multi_turn_tool_history_and_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    captured: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
    results = [
        ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ProviderToolCall(
                            id="call-one",
                            name="read",
                            arguments={"path": "README.md"},
                        )
                    ],
                )
            ],
            raw_response={},
        ),
        ProviderResult(
            messages=[ProviderMessage(role="assistant", content="done")],
            raw_response={},
        ),
    ]

    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: transport,
    )

    def invoke(_self: Any, **kwargs: Any) -> ProviderResult:
        captured.append((kwargs["messages"], kwargs["tools"]))
        return results.pop(0)

    monkeypatch.setattr(OpenAIChatRuntime, "invoke", invoke)
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=_profile(),
    )

    first = await client.invoke(
        _request([{"role": "user", "content": {"task": "inspect"}}])
    )
    assert thaw_json(first.response_payload) == {
        "output": [
            {
                "type": "function_call",
                "name": "read",
                "call_id": "call-one",
                "arguments": '{"path":"README.md"}',
            }
        ]
    }
    assert first.response_digest == canonical_sha256(first.response_payload)

    second_request = _request(
        [
            {"role": "user", "content": {"task": "inspect"}},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "checking"}],
            },
            {
                "type": "function_call",
                "name": "read",
                "call_id": "call-one",
                "arguments": '{"path":"README.md"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-one",
                "output": '{"content":"project"}',
            },
        ],
        turn=2,
    )
    second = await client.invoke(second_request)
    assert thaw_json(second.response_payload) == {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            }
        ]
    }
    assert captured[0][0] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": '{"task":"inspect"}'},
    ]
    assert captured[1][0][2] == {
        "role": "assistant",
        "content": "checking",
    }
    assert captured[1][0][-2:] == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-one",
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-one",
            "content": '{"content":"project"}',
        },
    ]
    assert captured[0][1] == [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read one file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
    ]

    await client.close()
    assert transport.closed
    with pytest.raises(RuntimeError, match="profile is closed"):
        client.profile_identity


@pytest.mark.asyncio
async def test_profile_client_retires_worker_and_retries_transport_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RetryTransport:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("transport still owns resources")

    transport = RetryTransport()
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: transport,
    )
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=_profile(),
    )

    with pytest.raises(RunnerDependencyError) as raised:
        await client.close()
    assert raised.value.code == "provider_cleanup_failed"
    with pytest.raises(RunnerDependencyError) as closed:
        await client.invoke(_request([{"role": "user", "content": "inspect"}]))
    assert closed.value.code == "provider_client_closed"

    await client.close()
    assert transport.close_calls == 2


@pytest.mark.asyncio
async def test_profile_client_cancellation_closes_active_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()

    class InterruptTransport:
        def close(self) -> None:
            released.set()

    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: InterruptTransport(),
    )

    def invoke(_self: Any, **_kwargs: Any) -> ProviderResult:
        started.set()
        if not released.wait(timeout=2):
            raise AssertionError("transport close did not interrupt provider work")
        raise RuntimeError("transport closed")

    monkeypatch.setattr(OpenAIChatRuntime, "invoke", invoke)
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=_profile(),
    )
    invocation = asyncio.create_task(
        client.invoke(_request([{"role": "user", "content": "inspect"}]))
    )
    assert await asyncio.to_thread(started.wait, 1)

    await client.cancel("episode_cancelled")
    with pytest.raises(asyncio.CancelledError):
        await invocation
    await client.close()


@pytest.mark.asyncio
async def test_profile_client_fails_closed_on_invalid_provider_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: _Transport(),
    )
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "invoke",
        lambda _self, **_kwargs: ProviderResult(
            messages=[
                ProviderMessage(role="assistant", content="one"),
                ProviderMessage(role="assistant", content="two"),
            ],
            raw_response={},
        ),
    )
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=_profile(),
    )

    with pytest.raises(RunnerProtocolError) as raised:
        await client.invoke(_request([{"role": "user", "content": "inspect"}]))
    assert raised.value.code == "policy_response_invalid"
    await client.close()


@pytest.mark.asyncio
async def test_failed_attempt_exhausts_budget_even_when_turn_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    attempts = []
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: transport,
    )

    def invoke(_self: Any, **_kwargs: Any) -> ProviderResult:
        attempts.append("transport attempt")
        raise OSError("injected transport failure")

    monkeypatch.setattr(OpenAIChatRuntime, "invoke", invoke)
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=_profile(),
        max_requests=1,
    )
    request = _request([{"role": "user", "content": "inspect"}])
    try:
        with pytest.raises(RunnerDependencyError) as first:
            await client.invoke(request)
        assert first.value.code == "provider_invocation_failed"
        with pytest.raises(RunnerDependencyError) as second:
            await client.invoke(request)
        assert second.value.code == "provider_request_budget_exhausted"
        assert attempts == ["transport attempt"]
        assert client.request_attempts == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_waits_for_thread_after_its_future_has_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = threading.Event()
    release = threading.Event()
    close_started = threading.Event()
    owned_threads = []

    class PausedCompletion(Future[Any]):
        def set_result(self, result: Any) -> None:
            super().set_result(result)
            completed.set()
            release.wait(timeout=10)

    class ObservedTransport(_Transport):
        def close(self) -> None:
            super().close()
            close_started.set()

    transport = ObservedTransport()
    monkeypatch.setattr(policy_provider_module, "Future", PausedCompletion)
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: transport,
    )

    def invoke(_self: Any, **_kwargs: Any) -> ProviderResult:
        owned_threads.append(threading.current_thread())
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="done")],
            raw_response={},
        )

    monkeypatch.setattr(OpenAIChatRuntime, "invoke", invoke)
    profile = _profile()
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=profile,
    )
    closing = None
    try:
        await client.invoke(_request([{"role": "user", "content": "inspect"}]))
        assert await asyncio.to_thread(completed.wait, 1)
        closing = asyncio.create_task(client.close())
        assert await asyncio.to_thread(close_started.wait, 1)
        assert owned_threads[0].is_alive()
        assert not closing.done()
        assert client.profile_identity == profile.identity_dict()
        release.set()
        await asyncio.wait_for(closing, 1)
        assert not owned_threads[0].is_alive()
    finally:
        release.set()
        if closing is not None:
            await asyncio.wait_for(closing, 2)
        else:
            await client.close()


@pytest.mark.asyncio
async def test_transport_cleanup_failure_preserves_owned_profile_until_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailOnceTransport(_Transport):
        attempts = 0

        def close(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("injected cleanup failure")
            super().close()

    transport = FailOnceTransport()
    retired = []

    async def on_close(client: EpisodeOpenAICompletionsPolicyClient) -> None:
        retired.append(client)

    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: transport,
    )
    profile = _profile()
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=profile,
        on_close=on_close,
    )
    try:
        with pytest.raises(RunnerDependencyError) as failure:
            await client.close()
        assert failure.value.code == "provider_cleanup_failed"
        assert not transport.closed
        assert retired == []
        assert client.profile_identity == profile.identity_dict()
        await client.close()
        assert transport.closed
        assert retired == [client]
        with pytest.raises(RuntimeError):
            _ = client.profile_identity
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pi_target_projection_drives_exact_prompt_user_and_tool_wire_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _ = compile_pi_target(tmp_path)
    assert projection.overlay_id == "r3-json-no-session.v1"
    assert "{{" not in projection.system_prompt
    target_tools = [thaw_json(tool) for tool in projection.chat_tools]
    response_tools = [
        {
            "type": "function",
            **tool["function"],
            "strict": True,
        }
        for tool in target_tools
    ]
    payload = {
        "model": MODEL_ID,
        "instructions": projection.system_prompt,
        "input": [
            {"role": "developer", "content": ""},
            {"role": "user", "content": {"prompt": "Inspect the repository."}},
        ],
        "tools": response_tools,
    }
    request = PolicyRuntimeInvokeRequest(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        binding_digest=_digest("binding"),
        policy_slot_id="policy-slot",
        request_digest=canonical_sha256(payload),
        request_payload=freeze_json_object(payload, field_name="target request"),
        turn=1,
        attempt=1,
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: _Transport(),
    )

    def invoke(_self: Any, **kwargs: Any) -> ProviderResult:
        captured.update(kwargs)
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="done")],
            raw_response={},
        )

    monkeypatch.setattr(OpenAIChatRuntime, "invoke", invoke)
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
        observation=_observation(),
        profile=_profile(),
        target_projection=projection,
    )

    await client.invoke(request)

    assert captured["messages"] == [
        {"role": "system", "content": projection.system_prompt},
        {"role": "user", "content": "Inspect the repository."},
    ]
    assert captured["tools"] == target_tools
    assert [tool["function"]["name"] for tool in captured["tools"]] == [
        "read",
        "bash",
        "edit",
        "write",
        "grep",
        "find",
        "ls",
    ]
    assert "additionalProperties" not in captured["tools"][0]["function"]["parameters"]
    assert client.target_identity == projection.identity_dict()
    await client.close()


class _AdmittedClient:
    def __init__(
        self,
        observation: c.PolicyCapabilityObservation | None = None,
    ) -> None:
        self.closed = False
        self._observation = observation or _observation()

    def observe(self) -> c.PolicyCapabilityObservation:
        return self._observation

    async def close(self) -> None:
        self.closed = True


class _AuthorityResolver:
    def __init__(
        self,
        observation: c.PolicyCapabilityObservation | None = None,
    ) -> None:
        self.client = _AdmittedClient(observation)
        self.closed = False
        self.aborted = False

    async def resolve(self, *_args: Any, **_kwargs: Any) -> _AdmittedClient:
        return self.client

    async def close(self) -> None:
        self.closed = True

    def abort_bootstrap(self) -> None:
        self.aborted = True


@pytest.mark.asyncio
async def test_profile_resolver_preserves_authoritative_observation_and_one_shot_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: _Transport(),
    )
    authority = _AuthorityResolver()
    resolver = EpisodeOpenAICompletionsPolicyResolver(
        authority,
        {"episode-one": _profile()},
        {"episode-one": "credential-one"},
        {"episode-one": MODEL_ID},
        {"episode-one": MODEL},
        {"episode-one": _observation().canonical_digest()},
    )
    binding = c.PolicyBindingRef(
        registry_revision_digest=_digest("registry"),
        route_id="policy-route",
        attestation_digest=_digest("attestation"),
    )

    client = await resolver.resolve(
        binding,
        episode_id="episode-one",
        effective_plan_digest=DIGEST,
    )
    assert client.observe() == _observation()
    assert authority.client.closed
    with pytest.raises(RunnerPolicyBindingError, match="no provider profile"):
        await resolver.resolve(
            binding,
            episode_id="episode-one",
            effective_plan_digest=DIGEST,
        )

    await client.close()
    await resolver.close()
    assert authority.closed


@pytest.mark.asyncio
async def test_profile_resolver_rejects_wire_model_outside_launcher_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: _Transport(),
    )
    resolver = EpisodeOpenAICompletionsPolicyResolver(
        _AuthorityResolver(),
        {"episode-one": _profile()},
        {"episode-one": "credential-one"},
        {"episode-one": MODEL_ID},
        {"episode-one": "other/wire-model"},
        {"episode-one": _observation().canonical_digest()},
    )
    binding = c.PolicyBindingRef(
        registry_revision_digest=_digest("registry"),
        route_id="policy-route",
        attestation_digest=_digest("attestation"),
    )

    with pytest.raises(RunnerPolicyBindingError) as raised:
        await resolver.resolve(
            binding,
            episode_id="episode-one",
            effective_plan_digest=DIGEST,
        )

    assert raised.value.code == "provider_profile_mismatch"
    await resolver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("provider_id", "other-provider"),
        ("model_id", "other-model"),
        ("credential_handle_id", "other-credential"),
    ),
)
async def test_profile_resolver_rejects_observation_outside_owned_provider_profile(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    field_value: str,
) -> None:
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: _Transport(),
    )
    observation_payload = _observation().model_dump(mode="json")
    observation_payload[field_name] = field_value
    observation = c.PolicyCapabilityObservation.model_validate(observation_payload)
    resolver = EpisodeOpenAICompletionsPolicyResolver(
        _AuthorityResolver(observation),
        {"episode-one": _profile()},
        {"episode-one": "credential-one"},
        {"episode-one": MODEL_ID},
        {"episode-one": MODEL},
        {"episode-one": _observation().canonical_digest()},
    )
    binding = c.PolicyBindingRef(
        registry_revision_digest=_digest("registry"),
        route_id="policy-route",
        attestation_digest=_digest("attestation"),
    )

    with pytest.raises(
        RunnerPolicyBindingError,
        match="does not match launcher authority",
    ) as error:
        await resolver.resolve(
            binding,
            episode_id="episode-one",
            effective_plan_digest=DIGEST,
        )

    assert error.value.code == "provider_route_authority_mismatch"
    await resolver.close()


def test_mini_wire_messages_keep_source_client_fields() -> None:
    from breadboard.rl.harness.policy_provider import _provider_descriptor
    from breadboard_engine.compilation.provider_response import MINI_RESPONSE_CONSUMER_ID
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext

    # Mini replays its committed assistant message; LiteLLM keeps provider fields.
    assistant = {
        "role": "assistant",
        "content": "Grouped batch A.",
        "provider_specific_fields": {"refusal": None},
        "tool_calls": [
            {"id": "call-0", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
        ],
    }
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, assistant]
    runtime = OpenAIChatRuntime(_provider_descriptor())
    mini = runtime.profile_chat_request(
        _profile(),
        messages,
        None,
        context=ProviderRuntimeContext(None, {}, extra={"response_consumer_id": MINI_RESPONSE_CONSUMER_ID}),
    )
    generic = runtime.profile_chat_request(
        _profile(), messages, None, context=ProviderRuntimeContext(None, {})
    )

    assert mini["messages"] == messages
    assert "provider_specific_fields" not in generic["messages"][2]


_OPENHANDS_SUPPLIER_TRACE = (
    Path(__file__).parents[2]
    / "fixtures/openhands_rerun2/captures/OH-01-normal-file-effect/trace.json"
)
_OPENHANDS_BASE_URL = "https://provider.example/v1"
_OPENHANDS_EPISODE = "episode-openhands"
_FOREIGN_CONVERSATION_KEY = "123e4567-e89b-42d3-a456-426614174000"


def _openhands_supplier_bodies() -> list[dict[str, Any]]:
    trace = json.loads(_OPENHANDS_SUPPLIER_TRACE.read_text(encoding="utf-8"))
    return [request["body"] for request in trace["requests"]]


_HERMES_SUPPLIER_TRACE = (
    Path(__file__).parents[2]
    / "e4_parity/fixtures/hermes_agent/H-01-normal-memory-skill-write/trace.json"
)


def _unbound_openhands_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conversation_key_field: str | None,
    consumer_id: str,
) -> tuple[EpisodeOpenAICompletionsPolicyClient, c.EffectiveExecutionPlan, list[bytes]]:
    """Build a real OpenHands client for the compiled target and a recording transport."""
    request_policy: dict[str, Any] = {
        "mode": "non_streaming",
        "include_usage": False,
        "max_token_field": "max_completion_tokens",
        "strict_tools": None,
        "enable_thinking": None,
    }
    if conversation_key_field is not None:
        request_policy["conversation_key_field"] = conversation_key_field
    return _unbound_native_chat_client(
        tmp_path,
        monkeypatch,
        target_id="openhands-sdk@1.47.0",
        consumer_id=consumer_id,
        model=_openhands_supplier_bodies()[0]["model"],
        request_policy=request_policy,
        sampling={"temperature": 0},
        max_token_feature="max_completion_tokens",
        request_features=["max_completion_tokens", "non_streaming", "temperature"],
    )


def _unbound_native_chat_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_id: str,
    consumer_id: str,
    model: str,
    request_policy: Mapping[str, Any],
    sampling: Mapping[str, Any],
    max_token_feature: str,
    request_features: list[str],
) -> tuple[EpisodeOpenAICompletionsPolicyClient, c.EffectiveExecutionPlan, list[bytes]]:
    """Build a real native Chat client for a compiled E4 target and a recording transport."""
    profile = OpenAICompletionsProviderProfile(
        model=model,
        scoped_credential="episode-secret",
        base_url=_OPENHANDS_BASE_URL,
        context_window=131_072,
        max_output_tokens=2_048,
        sampling=dict(sampling),
        capabilities={
            f"supports_{max_token_feature}": True,
            "supports_non_streaming": True,
            "supports_streaming": False,
            "supports_tools": True,
        },
        request_policy=dict(request_policy),
    )
    cas = FilesystemCAS(tmp_path / "native-chat-target-cas")
    try:
        manifest = compile_e4_harness(
            load_e4_target(target_id),
            {},
            {
                "version": 2,
                "profile": {"name": "native-chat-http-test"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True, "api_variant": "chat"},
                "providers": {
                    "default_model": "model-a",
                    "models": [{
                        "id": "model-a",
                        "adapter": "openai",
                        "context_length": profile.context_window,
                        "route_handle_id": "route-a",
                        "credential_handle_id": "credential-a",
                        "params": {},
                        "response_policy": {
                            "schema_version": "bb.provider_native_response_policy.v1",
                            "consumer_id": consumer_id,
                            "provider_profile_digest": profile_identity_digest(profile),
                            "max_response_bytes": 4_194_304,
                            "max_stream_fragments": 1,
                        },
                    }],
                },
            },
            cas=cas,
            options=_compile_options(),
            request_schema_version="bb.rl.headless-run-request.v3",
        ).manifest
    finally:
        cas.close()
    observation = _runtime_observation(
        provider_id="openai",
        model_id="model-a",
        capabilities=_policy_capabilities(
            max_context_tokens=profile.context_window,
            max_output_tokens=profile.max_output_tokens,
            request_features=request_features,
        ),
    )
    plan = _runtime_plan(
        observation=observation,
        semantics=manifest.semantic.to_canonical_obj(),
        policy_slot_ids=("model:model-a",),
    )
    plan_payload = plan.model_dump(mode="python")
    plan_payload["base_compiled"] = c.CompiledArtifactIdentity.model_validate({
        **plan.base_compiled.model_dump(mode="python"),
        "manifest_digest": "sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        "compiler_input_digest": manifest.inputs.compiler_input_digest,
    })
    plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
    sent: list[bytes] = []
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: _Transport(),
    )

    def send_native_http_request(_self: Any, **kwargs: Any) -> dict[str, Any]:
        sent.append(kwargs["body"])
        return {"status_code": 200, "headers": [], "body": b"{}"}

    monkeypatch.setattr(OpenAIChatRuntime, "send_native_http_request", send_native_http_request)
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id=_OPENHANDS_EPISODE,
        effective_plan_digest=plan.canonical_digest(),
        observation=observation,
        profile=profile,
        target_projection=E4TargetPolicyProjection.from_compiled(manifest),
        timeout_seconds=45,
    )
    return client, plan, sent


def _openhands_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conversation_key_field: str | None,
    consumer_id: str = OPENHANDS_RESPONSE_CONSUMER_ID,
) -> tuple[EpisodeOpenAICompletionsPolicyClient, str, list[bytes]]:
    """Bind a real OpenHands client to the compiled plan and the supplier tools."""
    client, plan, sent = _unbound_openhands_client(
        tmp_path,
        monkeypatch,
        conversation_key_field=conversation_key_field,
        consumer_id=consumer_id,
    )
    client.bind_compiled_plan(plan)
    client.bind_native_tools(tuple(_openhands_supplier_bodies()[0]["tools"]))
    return client, plan.canonical_digest(), sent


def _native_http_request(body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": _OPENHANDS_BASE_URL + "/chat/completions",
        "headers": [["Content-Type", "application/json"]],
        "body_b64": base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii"),
    }


async def _exchange_native_http(
    client: EpisodeOpenAICompletionsPolicyClient,
    plan_digest: str,
    body: Mapping[str, Any],
    *,
    turn: int,
) -> None:
    public_request = client.stage_native_http_request(_native_http_request(body))
    result = await client.invoke(PolicyRuntimeInvokeRequest(
        episode_id=_OPENHANDS_EPISODE,
        effective_plan_digest=plan_digest,
        binding_digest=_digest("binding"),
        policy_slot_id="model:model-a",
        request_digest=canonical_sha256(public_request),
        request_payload=freeze_json_object(public_request, field_name="native request"),
        turn=turn,
        attempt=1,
    ))
    client.take_native_http_response(result.response_digest)


@pytest.mark.asyncio
async def test_openhands_native_http_admits_declared_supplier_conversation_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = _openhands_supplier_bodies()
    client, plan_digest, sent = _openhands_client(
        tmp_path, monkeypatch, conversation_key_field="prompt_cache_key"
    )
    try:
        for turn, body in enumerate(bodies, start=1):
            await _exchange_native_http(client, plan_digest, body, turn=turn)
    finally:
        await client.close()

    assert sent == [json.dumps(body).encode("utf-8") for body in bodies]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conversation_keys",
    [
        pytest.param(("supplier", None), id="missing-after-present"),
        pytest.param((None, "supplier"), id="present-after-missing"),
        pytest.param(("supplier", _FOREIGN_CONVERSATION_KEY), id="changed"),
        pytest.param(("conversation-1",), id="not-uuid"),
        pytest.param((_FOREIGN_CONVERSATION_KEY.upper(),), id="uppercase-uuid"),
        pytest.param((1234,), id="not-string"),
    ],
)
async def test_openhands_native_http_pins_one_uuid_conversation_key_per_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conversation_keys: tuple[Any, ...],
) -> None:
    bodies = []
    for body, key in zip(_openhands_supplier_bodies(), conversation_keys):
        if key is None:
            body.pop("prompt_cache_key")
        elif key != "supplier":
            body["prompt_cache_key"] = key
        bodies.append(body)
    client, plan_digest, sent = _openhands_client(
        tmp_path, monkeypatch, conversation_key_field="prompt_cache_key"
    )
    try:
        for turn, body in enumerate(bodies[:-1], start=1):
            await _exchange_native_http(client, plan_digest, body, turn=turn)
        with pytest.raises(RunnerPolicyBindingError) as error:
            client.stage_native_http_request(_native_http_request(bodies[-1]))
    finally:
        await client.close()

    assert error.value.code == "native_http_capability_mismatch"
    assert len(sent) == len(bodies) - 1


@pytest.mark.asyncio
async def test_openhands_binding_requires_declared_conversation_key_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, plan, sent = _unbound_openhands_client(
        tmp_path,
        monkeypatch,
        conversation_key_field=None,
        consumer_id=OPENHANDS_RESPONSE_CONSUMER_ID,
    )
    try:
        with pytest.raises(
            NativeResponseBindingError,
            match="native Chat response requires its compiled source profile",
        ):
            client.bind_compiled_plan(plan)
        with pytest.raises(RunnerPolicyBindingError) as error:
            client.stage_native_http_request(
                _native_http_request(_openhands_supplier_bodies()[0])
            )
    finally:
        await client.close()

    assert error.value.code == "native_http_binding_invalid"
    assert sent == []


def _unbound_hermes_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conversation_key_field: str | None,
) -> tuple[EpisodeOpenAICompletionsPolicyClient, c.EffectiveExecutionPlan, list[bytes]]:
    request_policy: dict[str, Any] = {
        "mode": "non_streaming",
        "include_usage": False,
        "max_token_field": "max_tokens",
        "strict_tools": None,
        "enable_thinking": None,
    }
    if conversation_key_field is not None:
        request_policy["conversation_key_field"] = conversation_key_field
    return _unbound_native_chat_client(
        tmp_path,
        monkeypatch,
        target_id="hermes-agent@2026.9.11",
        consumer_id=HERMES_RESPONSE_CONSUMER_ID,
        model=_hermes_supplier_bodies()[0]["model"],
        request_policy=request_policy,
        sampling={},
        max_token_feature="max_tokens",
        request_features=["max_tokens", "non_streaming"],
    )


def _hermes_supplier_bodies() -> list[dict[str, Any]]:
    trace = json.loads(_HERMES_SUPPLIER_TRACE.read_text(encoding="utf-8"))
    return [request["body"] for request in trace["requests"] if request["kind"] == "request"]


@pytest.mark.asyncio
async def test_hermes_binding_admits_source_profile_without_conversation_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _hermes_supplier_bodies()[0]["model"]
    client, plan, sent = _unbound_hermes_client(tmp_path, monkeypatch, conversation_key_field=None)
    try:
        public_config = client.bind_compiled_plan(plan)
    finally:
        await client.close()

    assert public_config["model_name"] == model
    assert public_config["base_url"] == _OPENHANDS_BASE_URL
    assert sent == []


@pytest.mark.asyncio
async def test_hermes_binding_rejects_undeclared_source_conversation_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, plan, sent = _unbound_hermes_client(
        tmp_path, monkeypatch, conversation_key_field="prompt_cache_key"
    )
    try:
        with pytest.raises(
            NativeResponseBindingError,
            match="native Chat response requires its compiled source profile",
        ):
            client.bind_compiled_plan(plan)
    finally:
        await client.close()

    assert sent == []



@pytest.mark.asyncio
async def test_native_http_rejects_conversation_key_without_policy_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplier_body = _openhands_supplier_bodies()[0]
    keyless_body = dict(supplier_body)
    keyless_body.pop("prompt_cache_key")
    # The recording consumer does not gate the source profile, so staging must.
    client, plan_digest, sent = _openhands_client(
        tmp_path,
        monkeypatch,
        conversation_key_field=None,
        consumer_id=NATIVE_RESPONSE_CONSUMER_ID,
    )
    try:
        with pytest.raises(RunnerPolicyBindingError) as error:
            client.stage_native_http_request(_native_http_request(supplier_body))
        await _exchange_native_http(client, plan_digest, keyless_body, turn=1)
    finally:
        await client.close()

    assert error.value.code == "native_http_capability_mismatch"
    assert sent == [json.dumps(keyless_body).encode("utf-8")]
