from __future__ import annotations

import asyncio
import hashlib
import threading
from typing import Any

import pytest

from agentic_coder_prototype.compilation.contracts import canonical_sha256
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyClient,
    EpisodeOpenAICompletionsPolicyResolver,
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
            "request_features": [],
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
    with pytest.raises(RunnerDependencyError) as raised:
        await invocation
    assert raised.value.code == "provider_invocation_failed"
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
async def test_pi_target_projection_drives_exact_prompt_user_and_tool_wire_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = E4TargetPolicyProjection.load(
        "pi@0.57.1",
        {
            "readme_path": "/opt/pi/README.md",
            "docs_path": "/opt/pi/docs",
            "examples_path": "/opt/pi/examples",
            "current_date_time": "2026-08-29T12:00:00Z",
            "cwd": "/workspace",
        },
    )
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
    )
    binding = c.PolicyBindingRef(
        registry_revision_digest=_digest("registry"),
        route_id="policy-route",
        attestation_digest=_digest("attestation"),
    )

    with pytest.raises(
        RunnerPolicyBindingError,
        match="does not match the admitted observation",
    ) as error:
        await resolver.resolve(
            binding,
            episode_id="episode-one",
            effective_plan_digest=DIGEST,
        )

    assert error.value.code == "provider_profile_mismatch"
    await resolver.close()
