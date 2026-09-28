"""Unit tests for the OMP 16.2.13 engine slice (provider body, policy binding, public config)."""
from __future__ import annotations

import copy
from typing import Any, Mapping

import pytest

from breadboard.rl.harness.policy_provider import (
    _OMP_16_2_13_MODEL_REGISTRY_FIELDS,
    _omp_16_2_13_public_config,
)
from breadboard_engine.compilation.provider_response import (
    OMP_16_2_13_RESPONSE_CONSUMER_ID,
    NativeResponseBindingError,
    NativeResponsePolicy,
    admit_native_response_binding,
    profile_identity_digest,
)
from breadboard_engine.provider.contracts import (
    OpenAICompletionsProviderProfile,
    ProviderContractError,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.profiles import OpenAICompletionsRequestPolicy
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime

# Pinned literal digest for a default profile computed against base 4e9b668c.
BASE_4E9B668C_DEFAULT_PROFILE_DIGEST = (
    "sha256:f8a9625fa744e9450f58033e3ee68f67881801ee759036999e8320375de23a39"
)

ZERO_COST = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
OMP_16_2_13_MODEL_REGISTRY = {
    "provider_id": "vllm-local",
    "api": "openai-completions",
    "auth": "none",
    "contextWindow": 200_000,
    "maxTokens": 2048,
    "reasoning": False,
    "input": ["text"],
    "compat": {
        "supportsStore": False,
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": False,
        "supportsUsageInStreaming": True,
        "maxTokensField": "max_completion_tokens",
        "thinkingFormat": "qwen",
        "supportsStrictMode": False,
    },
}

OMP_16_2_13_DECLARED_REQUEST_POLICY = {
    "preserve_thinking": True,
    "chat_template_kwargs": {"preserve_thinking": True},
}


def _profile(**overrides: Any) -> OpenAICompletionsProviderProfile:
    policy_overrides = overrides.pop("request_policy", {})
    policy = {
        "mode": "streaming",
        "include_usage": True,
        "max_token_field": "max_completion_tokens",
        "strict_tools": None,
        "enable_thinking": None,
        "preserve_thinking": True,
        "chat_template_kwargs": {"preserve_thinking": True},
    }
    policy.update(policy_overrides)
    capabilities_overrides = overrides.pop("capabilities", {})
    capabilities = {
        "supports_store": False,
        "supports_thinking_control": True,
        "supports_stream_options": True,
        "supports_max_completion_tokens": True,
    }
    capabilities.update(capabilities_overrides)
    values = {
        "model": "Qwen/Qwen3.5-35B-A3B",
        "scoped_credential": "episode-secret",
        "base_url": "http://127.0.0.1:18080/v1",
        "context_window": 200_000,
        "max_output_tokens": 2048,
        "request_policy": policy,
        "capabilities": capabilities,
    }
    values.update(overrides)
    return OpenAICompletionsProviderProfile(**values)


def _target(**overrides: Any) -> dict[str, Any]:
    runtime_profile = {
        "request_policy": copy.deepcopy(OMP_16_2_13_DECLARED_REQUEST_POLICY),
        "model_registry": copy.deepcopy(OMP_16_2_13_MODEL_REGISTRY),
    }
    base = {
        "version": 3,
        "target_id": "oh-my-pi-r2@16.2.13",
        "renderer_id": OMP_16_2_13_RESPONSE_CONSUMER_ID,
        "rendered_prompt_digest": None,
        "runtime_profile": runtime_profile,
    }
    base.update(overrides)
    return base


def test_chat_body_members_and_values_for_16_2_13_match_packet_o0_req0() -> None:
    """The 16.2.13 wire request carries exactly the packet's top-level member set with right values."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read file",
                "parameters": {"type": "object", "properties": {"i": {"type": "string"}, "path": {"type": "string"}}, "required": ["path", "i"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute command",
                "parameters": {"type": "object", "properties": {"i": {"type": "string"}, "command": {"type": "string"}}, "required": ["command", "i"]},
            },
        },
    ]
    messages = [
        {"role": "system", "content": "You are omp's trusted coding assistant."},
        {"role": "user", "content": [{"type": "text", "text": "task"}]},
    ]
    profile = _profile()
    runtime = object.__new__(OpenAIChatRuntime)
    context = ProviderRuntimeContext(
        session_state=None,
        agent_config={},
        extra={"response_consumer_id": OMP_16_2_13_RESPONSE_CONSUMER_ID},
    )
    body = runtime.profile_chat_request(profile, messages, tools, context=context)

    # Top-level members equal packet o0 request 0's top-level keys
    assert set(body.keys()) == {
        "model",
        "messages",
        "stream",
        "stream_options",
        "tools",
        "max_completion_tokens",
        "preserve_thinking",
        "chat_template_kwargs",
    }
    # Values
    assert body["model"] == "Qwen/Qwen3.5-35B-A3B"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_completion_tokens"] == 2048
    assert body["preserve_thinking"] is True
    assert body["chat_template_kwargs"] == {"preserve_thinking": True}
    # Tools and messages pass through verbatim
    assert body["tools"] == tools
    assert body["messages"] == messages
    # OMP 16.2.13 wire never carries store or n
    assert "store" not in body
    assert "n" not in body


def test_existing_profile_identity_and_as_dict_unchanged_when_new_fields_unset() -> None:
    """Existing profile identities/digests are unchanged when preserve_thinking/chat_template_kwargs are unset."""
    p_default = OpenAICompletionsProviderProfile(
        model="test-model",
        scoped_credential="secret",
        base_url="https://api.example.com/v1",
        context_window=100_000,
        max_output_tokens=2048,
    )
    digest = profile_identity_digest(p_default)
    assert digest == BASE_4E9B668C_DEFAULT_PROFILE_DIGEST

    as_dict = p_default.request_policy.as_dict()
    assert "preserve_thinking" not in as_dict
    assert "chat_template_kwargs" not in as_dict

    request = p_default.chat_request([], None)
    assert "preserve_thinking" not in request
    assert "chat_template_kwargs" not in request

    features = p_default.required_request_features(tools=False)
    assert "preserve_thinking" not in features
    assert "chat_template_kwargs" not in features

    prov = p_default.chat_request_provenance([], None)
    assert "preserve_thinking" not in prov
    assert "chat_template_kwargs" not in prov


def test_request_policy_preserve_thinking_and_chat_template_kwargs_included_when_set() -> None:
    policy = OpenAICompletionsRequestPolicy(
        preserve_thinking=True,
        chat_template_kwargs={"preserve_thinking": True},
    )
    as_dict = policy.as_dict()
    assert as_dict["preserve_thinking"] is True
    assert as_dict["chat_template_kwargs"] == {"preserve_thinking": True}

    profile = OpenAICompletionsProviderProfile(
        model="test-model",
        scoped_credential="secret",
        base_url="https://api.example.com/v1",
        context_window=100_000,
        max_output_tokens=2048,
        request_policy=policy,
    )
    request = profile.chat_request([], None)
    assert request["preserve_thinking"] is True
    assert request["chat_template_kwargs"] == {"preserve_thinking": True}

    features = profile.required_request_features(tools=False)
    assert "preserve_thinking" in features
    assert "chat_template_kwargs" in features

    prov = profile.chat_request_provenance([], None)
    assert prov["preserve_thinking"]["effective"] is True
    assert prov["chat_template_kwargs"]["effective"] == {"preserve_thinking": True}


@pytest.mark.parametrize("invalid_pt", [123, "true", {}, []])
def test_request_policy_rejects_non_boolean_preserve_thinking(invalid_pt: Any) -> None:
    with pytest.raises(ProviderContractError, match="preserve_thinking must be boolean or null"):
        OpenAICompletionsRequestPolicy(preserve_thinking=invalid_pt)


@pytest.mark.parametrize("invalid_ctk", [
    "not-a-dict",
    [1, 2],
    123,
    {"": "empty-key"},
    {123: "non-str-key"},
    {"key": {"nested": "not-a-scalar"}},
    {"key": [1, 2, 3]},
])
def test_request_policy_rejects_invalid_chat_template_kwargs(invalid_ctk: Any) -> None:
    with pytest.raises(ProviderContractError):
        OpenAICompletionsRequestPolicy(chat_template_kwargs=invalid_ctk)


def test_preserve_thinking_requires_supports_thinking_control_capability() -> None:
    with pytest.raises(ProviderContractError, match="supports_thinking_control"):
        OpenAICompletionsProviderProfile(
            model="m",
            scoped_credential="c",
            base_url="https://u",
            context_window=1000,
            max_output_tokens=100,
            request_policy={"preserve_thinking": True},
            capabilities={"supports_thinking_control": False},
        )


def _mock_manifest(target_binding: dict[str, Any], profile: OpenAICompletionsProviderProfile) -> Any:
    from types import SimpleNamespace
    policy = NativeResponsePolicy(
        schema_version="bb.provider_native_response_policy.v1",
        consumer_id=OMP_16_2_13_RESPONSE_CONSUMER_ID,
        provider_profile_digest=profile_identity_digest(profile),
        max_response_bytes=1024 * 1024,
        max_stream_fragments=1000,
    )
    return SimpleNamespace(
        inputs=SimpleNamespace(compiler_input_digest="sha256:" + "0" * 64),
        semantic=SimpleNamespace(
            providers={
                "models": [{
                    "model_id": profile.model,
                    "adapter_id": "openai",
                    "context_length": profile.context_window,
                    "routing": {"fallback_model_ids": []},
                    "response_policy": policy.identity_dict(),
                }]
            },
            metadata={"e4_target": target_binding},
        ),
    )


def test_admit_native_response_binding_succeeds_with_declared_policy() -> None:
    from unittest.mock import patch
    profile = _profile()
    target = _target()
    mock_manifest = _mock_manifest(target, profile)
    with patch("breadboard_engine.compilation.server_compiler.verify_cached_manifest", return_value=mock_manifest):
        binding = admit_native_response_binding(
            b"dummy",
            expected_compiler_input_digest="sha256:" + "0" * 64,
            authority_model_id=profile.model,
            profile=profile,
            capability_observation_digest="sha256:" + "1" * 64,
            episode_id="ep1",
            effective_plan_digest="sha256:" + "2" * 64,
        )
    assert binding.policy.consumer_id == OMP_16_2_13_RESPONSE_CONSUMER_ID

@pytest.mark.parametrize("mutator", [
    lambda p, t: (p, {**t, "version": 2}),
    lambda p, t: (p, {**t, "target_id": "other"}),
    lambda p, t: (p, {**t, "renderer_id": "other"}),
    lambda p, t: (p, {**t, "rendered_prompt_digest": "not-null"}),
    lambda p, t: (_profile(request_policy={"max_token_field": "max_tokens"}), t),
    lambda p, t: (_profile(max_output_tokens=4096), t),
    lambda p, t: (_profile(request_policy={"strict_tools": False}), t),
    lambda p, t: (_profile(request_policy={"include_usage": False}), t),
    lambda p, t: (_profile(request_policy={"enable_thinking": True}), t),
    lambda p, t: (_profile(capabilities={"supports_store": True}), t),
    lambda p, t: (_profile(request_policy={"preserve_thinking": False}), t),
    lambda p, t: (_profile(request_policy={"preserve_thinking": None}), t),
    lambda p, t: (_profile(request_policy={"chat_template_kwargs": None}), t),
    lambda p, t: (_profile(request_policy={"chat_template_kwargs": {"other": True}}), t),
    lambda p, t: (p, {**t, "runtime_profile": {"model_registry": OMP_16_2_13_MODEL_REGISTRY}}),
])
def test_admit_native_response_binding_fails_closed_on_invalid_policy(mutator: Any) -> None:
    from unittest.mock import patch
    profile, target = mutator(_profile(), _target())
    mock_manifest = _mock_manifest(target, profile)
    with patch("breadboard_engine.compilation.server_compiler.verify_cached_manifest", return_value=mock_manifest):
        with pytest.raises(NativeResponseBindingError):
            admit_native_response_binding(
                b"dummy",
                expected_compiler_input_digest="sha256:" + "0" * 64,
                authority_model_id=profile.model,
                profile=profile,
                capability_observation_digest="sha256:" + "1" * 64,
                episode_id="ep1",
                effective_plan_digest="sha256:" + "2" * 64,
            )

def test_omp_16_2_13_public_config_comes_from_model_registry() -> None:
    profile = _profile()
    runtime_profile = {"model_registry": copy.deepcopy(OMP_16_2_13_MODEL_REGISTRY)}
    config = _omp_16_2_13_public_config(profile, runtime_profile)
    assert config == {
        "id": "Qwen/Qwen3.5-35B-A3B",
        "name": "Qwen/Qwen3.5-35B-A3B",
        "api": "openai-completions",
        "provider": "vllm-local",
        "baseUrl": "http://127.0.0.1:18080/v1",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 200_000,
        "maxTokens": 2048,
        "compat": OMP_16_2_13_MODEL_REGISTRY["compat"],
    }


@pytest.mark.parametrize("missing", sorted(_OMP_16_2_13_MODEL_REGISTRY_FIELDS))
def test_omp_16_2_13_public_config_fails_closed_on_missing_registry_key(missing: str) -> None:
    profile = _profile()
    registry = {k: v for k, v in OMP_16_2_13_MODEL_REGISTRY.items() if k != missing}
    with pytest.raises(ValueError):
        _omp_16_2_13_public_config(profile, {"model_registry": registry})


@pytest.mark.parametrize("override", [
    {"provider_id": "openai"},
    {"api": "openai-responses"},
    {"reasoning": True},
    {"compat": {**OMP_16_2_13_MODEL_REGISTRY["compat"], "supportsDeveloperRole": True}},
    {"compat": {**OMP_16_2_13_MODEL_REGISTRY["compat"], "supportsStore": True}},
    {"compat": {**OMP_16_2_13_MODEL_REGISTRY["compat"], "maxTokensField": "max_tokens"}},
])
def test_omp_16_2_13_public_config_rejects_forbidden_registry_values(override: Mapping[str, Any]) -> None:
    profile = _profile()
    registry = {**copy.deepcopy(OMP_16_2_13_MODEL_REGISTRY), **override}
    with pytest.raises(ValueError):
        _omp_16_2_13_public_config(profile, {"model_registry": registry})
