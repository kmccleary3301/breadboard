from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.lease_envelope import RuntimeContainment
from breadboard.rl.harness.policy_provider import EpisodeOpenAICompletionsPolicyClient
from breadboard.rl.harness.runners.base import RunnerOpenRequest, RunnerToolBinding
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_IMPLEMENTATION_DIGEST,
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
    ConductorRunRequest,
    PolicyRuntimeBinding,
)
from breadboard_engine.compilation.provider_response import profile_identity_digest
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from tests.rl.harness.test_omp_native_stream_conductor import _compile_target
from tests.rl.harness.test_runner_conductor import (
    CONDUCTOR_TEST_AUTHENTICATOR,
    CONDUCTOR_TEST_LEDGER,
    _tool_grant,
)
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities
from tests.rl.harness.v2_service_fixtures import signed_containment_receipt

_NATIVE_CONFIG = Path(__file__).parents[3] / "config/e4_targets/oh_my_pi/18.1.17/native-config.json"


class _StopAtInitialize(Exception):
    pass


class _RecordingPort:
    """Installed-runtime shape: the worker gets only what the conductor sends."""

    def __init__(self, workspace: Path, bindings: tuple[RunnerToolBinding, ...]) -> None:
        self.containment = RuntimeContainment.ATTESTED
        self.containment_lease_id = CONDUCTOR_TEST_LEDGER.record.lease_id
        self.containment_receipt = signed_containment_receipt(
            self.containment_lease_id, "sandbox", CONDUCTOR_TEST_AUTHENTICATOR
        )
        self.workspace = workspace
        self.bindings = bindings
        self.initialize_payload: dict[str, Any] | None = None

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        return self.bindings

    def native_runtime_inputs(self, *, input_names: tuple[str, ...], package_subpath: str) -> Mapping[str, str]:
        values = {
            "cwd": str(self.workspace),
            "home": str(self.workspace / "home"),
            "current_date": "2026-09-25",
            "package_dir": str(self.workspace / package_subpath),
        }
        return {name: values[name] for name in input_names}

    async def begin_native_workspace_effects(self) -> None:
        return None

    async def measure_workspace_effects(self) -> Mapping[str, Mapping[str, Any]]:
        return {}

    async def invoke_native_phase(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
        package_subpath: str | None = None,
    ) -> Mapping[str, Any]:
        del timeout_ms, package_subpath
        if operation == "initialize":
            self.initialize_payload = json.loads(json.dumps(payload))
            raise _StopAtInitialize()
        raise AssertionError(f"unexpected phase before initialize: {operation}")

    async def close_native_runtime(self) -> Mapping[str, Any]:
        return {"kind": "closed", "cleanup": {"all_dead": True, "processes": [], "steps": []}}


@pytest.mark.asyncio
async def test_omp_initialize_carries_compiled_route_classifier(tmp_path: Path) -> None:
    model_id = "capture"
    profile = OpenAICompletionsProviderProfile(
        model=model_id,
        scoped_credential="episode-secret",
        base_url="http://127.0.0.1:9/v1",
        context_window=32_768,
        max_output_tokens=2_048,
        caller_headers={},
        request_policy={
            "mode": "streaming",
            "include_usage": True,
            "max_token_field": "max_completion_tokens",
            "strict_tools": None,
            "enable_thinking": None,
        },
        capabilities={"supports_store": True, "supports_max_completion_tokens": True},
    )
    projection, semantics, manifest = _compile_target(tmp_path, model_id, profile_identity_digest(profile))
    observation = _observation(
        provider_id="openai",
        model_id=model_id,
        capabilities=_policy_capabilities(
            request_features=["max_completion_tokens", "n", "store", "stream_options", "streaming"]
        ),
    )
    plan = _plan(
        observation=observation,
        semantics=semantics,
        tools=tuple(_tool_grant(name) for name in ("bash", "edit", "read", "write")),
        policy_slot_ids=(f"model:{model_id}",),
        limit_updates={"max_turns": 8, "action_timeout_ms": 40_000},
        implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST,
    )
    base_payload = plan.base_compiled.model_dump(mode="python")
    base_payload.update(
        manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        compiler_input_digest=manifest.inputs.compiler_input_digest,
    )
    plan_payload = plan.model_dump(mode="python")
    plan_payload["base_compiled"] = c.CompiledArtifactIdentity.model_validate(base_payload)
    plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
    port = _RecordingPort(
        tmp_path / "workspace",
        tuple(
            RunnerToolBinding(tool.tool_id, tool.implementation_digest, tuple(tool.capability_ids))
            for tool in plan.effective_capabilities.tools
        ),
    )
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-omp",
        effective_plan_digest=plan.canonical_digest(),
        observation=observation,
        profile=profile,
        target_projection=projection,
        timeout_seconds=45,
    )
    binding = PolicyRuntimeBinding(RunnerOpenRequest(episode_id="episode-omp", effective_plan=plan), client)
    session = await ConductorAdapter(
        CONDUCTOR_RUNTIME_ABI,
        containment_authenticator=CONDUCTOR_TEST_AUTHENTICATOR,
        admitted_lease_ledger=CONDUCTOR_TEST_LEDGER,
    ).open(
        RunnerOpenRequest(episode_id="episode-omp", effective_plan=plan),
        policy=binding,
        workspace=port,
        cancellation=type("C", (), {"raise_if_cancelled": lambda *args, **kwargs: None})(),
        events=type("E", (), {"emit": lambda self, event: _noop()})(),
    )
    try:
        try:
            await session.run(ConductorRunRequest(task_input={"prompt": "work"}, context={}))
        except _StopAtInitialize:
            pass
    finally:
        await session.close()
        await client.close()
    assert port.initialize_payload is not None
    expected = json.loads(_NATIVE_CONFIG.read_text(encoding="utf-8"))["route_classifier"]
    assert port.initialize_payload["route_classifier"] == expected


async def _noop() -> None:
    return None
