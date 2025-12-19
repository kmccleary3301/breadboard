import os
import types

import pytest

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.provider_capability_probe import CapabilityProbeResult
from agentic_coder_prototype.provider_runtime import (
    MockRuntime,
    ProviderRuntimeError,
    ProviderResult,
    ProviderMessage,
)
from agentic_coder_prototype.provider_routing import provider_router


REGRESSION_SCENARIOS = [
    {
        "id": "openrouter_streaming_fallback",
        "config": "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
        "stream": True,
        "probe": CapabilityProbeResult(
            model_id="openrouter/x-ai/grok-4-fast",
            attempted=True,
            stream_success=False,
            tool_stream_success=False,
            json_mode_success=False,
            error="mock capability failure",
            elapsed=0.0,
        ),
        "expect_stream_policy": True,
        "expect_fallback": True,
    },
    {
        "id": "openrouter_text_only",
        "config": "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
        "stream": False,
        "probe": CapabilityProbeResult(
            model_id="openrouter/x-ai/grok-4-fast",
            attempted=True,
            stream_success=True,
            tool_stream_success=True,
            json_mode_success=True,
            error=None,
            elapsed=0.0,
        ),
        "expect_stream_policy": False,
        "expect_fallback": False,
    },
]


@pytest.fixture()
def regression_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    monkeypatch.setenv("KC_DISABLE_PROVIDER_PROBES", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "regression-dummy")

    workspace = tmp_path / "regression_ws"
    workspace.mkdir(parents=True, exist_ok=True)
    yield workspace


def _run_scenario(monkeypatch, workspace, scenario):
    config = load_agent_config(scenario["config"])
    config.setdefault("logging", {})["enabled"] = False

    # Ensure fallback candidates favour mock runtime for deterministic assertions.
    for model_entry in config.get("providers", {}).get("models", []):
        if model_entry.get("id") == "openrouter/x-ai/grok-4-fast":
            routing = model_entry.setdefault("routing", {})
            routing.setdefault("fallback_models", ["mock/dev", "openrouter/openai/gpt-4o-mini"])

    openrouter_cfg = provider_router.providers["openrouter"]
    monkeypatch.setattr(openrouter_cfg, "runtime_id", "mock_chat")
    monkeypatch.setattr(openrouter_cfg, "base_url", None)

    orig_invoke = MockRuntime.invoke
    sentinel_result = ProviderResult(
        messages=[ProviderMessage(role="assistant", content="TASK COMPLETE", tool_calls=[], finish_reason="stop", index=0)],
        raw_response={"mock": True},
        usage=None,
        encrypted_reasoning=None,
        reasoning_summaries=None,
        model="mock",
    )

    non_fallback_calls = {"count": 0}

    def _patched_invoke(self, *, client, model, messages, tools, stream, context):
        extra = getattr(context, "extra", {}) or {}
        if extra.get("fallback_of"):
            return sentinel_result
        if stream:
            raise ProviderRuntimeError("mock streaming failure")
        raise ProviderRuntimeError("mock primary failure")

    if scenario["expect_fallback"]:
        monkeypatch.setattr(MockRuntime, "invoke", _patched_invoke)
    else:
        def _non_fallback_invoke(self, *, client, model, messages, tools, stream, context):
            non_fallback_calls["count"] += 1
            if non_fallback_calls["count"] >= 2:
                return sentinel_result
            return orig_invoke(self, client=client, model=model, messages=messages, tools=tools, stream=stream, context=context)

        monkeypatch.setattr(MockRuntime, "invoke", _non_fallback_invoke)

    events = []
    stream_policies = []

    cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = cls(
        workspace=str(workspace),
        config=config,
        local_mode=True,
    )

    def _capture_event(self, session_state, markdown_logger, *, turn_index, tag, message, payload):
        events.append((tag, payload))

    def _capture_policy(self, session_state, policy):
        stream_policies.append(policy)

    conductor._log_routing_event = types.MethodType(_capture_event, conductor)  # type: ignore[attr-defined]
    conductor._record_stream_policy_metadata = types.MethodType(_capture_policy, conductor)  # type: ignore[attr-defined]

    def _capability_stub(self, cfg, session_state):
        session_state.set_provider_metadata("capability_probes", [scenario["probe"].__dict__])
        return [scenario["probe"]]

    conductor.capability_probe_runner.run = types.MethodType(_capability_stub, conductor.capability_probe_runner)  # type: ignore[attr-defined]

    result = conductor.run_agentic_loop(
        system_prompt="",
        user_prompt="Enumerate workspace",
        model=config["providers"]["default_model"],
        max_steps=2,
        stream_responses=scenario["stream"],
        tool_prompt_mode="system_compiled_and_persistent_per_turn",
    )

    metrics = conductor.provider_metrics.snapshot()

    return events, stream_policies, metrics, result


@pytest.mark.parametrize("scenario", REGRESSION_SCENARIOS, ids=lambda s: s["id"])
def test_provider_regression_matrix(monkeypatch, regression_env, scenario):
    events, stream_policies, metrics, result = _run_scenario(monkeypatch, regression_env, scenario)

    stream_tags = [tag for tag, _ in events if tag == "stream_policy"]
    fallback_tags = [tag for tag, _ in events if tag == "fallback_route"]

    if scenario["expect_stream_policy"]:
        assert stream_tags, f"expected stream policy event for {scenario['id']}"
        assert stream_policies, "expected recorded stream policy payload"
    else:
        assert not stream_tags, f"unexpected stream policy event for {scenario['id']}"
        assert not stream_policies, f"unexpected stream policy metadata for {scenario['id']}"

    if scenario["expect_fallback"]:
        assert fallback_tags, f"expected fallback routing event for {scenario['id']}"
        assert metrics["fallbacks"], "expected fallback metrics recorded"
        assert metrics["summary"]["errors"] > 0, "expected error path to be recorded"
    else:
        assert not fallback_tags, f"unexpected fallback routing event for {scenario['id']}"
        assert not metrics["fallbacks"], "unexpected fallback metrics recorded"
        assert metrics["summary"]["errors"] == 0, "unexpected error metrics recorded"

    stream_override_count = metrics["summary"]["stream_overrides"]
    if scenario["expect_stream_policy"]:
        assert stream_override_count > 0, "stream override should be counted"
    else:
        assert stream_override_count == 0, "stream override should not be counted"

    completion_reason = (result or {}).get("completion_reason")
    assert completion_reason, "completion reason should be populated"

    assert "dialects" in metrics, "dialect metrics key should exist in provider metrics snapshot"
