"""pi episode responsibility owner."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Tuple
from .pi_target import REPO_ROOT, load_target_document, resolve_target_harness_path

async def run_breadboard_pi_episode(
    mock_base_url: str,
    workdir: Path,
    prompt: str,
    target_id: str = "pi-r3@0.73.1",
    context_window: int = 16000,
    max_output_tokens: int = 2048,
    terminal_overflow: bool = False,
) -> Tuple[Any, List[Dict[str, Any]], Dict[str, Any]]:
    """Build the existing conductor test harness with the selected Pi consumer."""
    from breadboard_engine.compilation.provider_response import (
        PI_RESPONSE_CONSUMER_ID, PI_0_57_1_RESPONSE_CONSUMER_ID,
    )
    from breadboard_engine.e4_targets import load_e4_target
    from tests.rl.harness import test_pi_0_73_1_compaction as h
    from breadboard_engine.provider.contracts import NativeProviderRequestFailure
    from breadboard_engine.compaction.overflow import is_context_overflow
    import yaml
    from tests.rl.harness.test_pi_0_57_1_native_stream_conductor import _Pi057WorkerPort

    version = target_id.split("@", 1)[1]
    legacy = version == "0.57.1"
    consumer = PI_0_57_1_RESPONSE_CONSUMER_ID if legacy else PI_RESPONSE_CONSUMER_ID
    model_id = "model-a"
    profile = h.OpenAICompletionsProviderProfile(
        model=model_id, scoped_credential="episode-secret", base_url=mock_base_url,
        context_window=context_window, max_output_tokens=max_output_tokens,
        caller_headers={},
        request_policy={
            "mode": "streaming", "include_usage": True,
            "strict_tools": False if legacy else None, "enable_thinking": None,
        },
        capabilities={"supports_store": not legacy, "supports_strict_tools": legacy},
    )
    cas = h.FilesystemCAS(workdir / "target-cas")
    try:
        compiled = h.compile_e4_harness(
            load_e4_target(target_id), {},
            {
                "version": 2,
                "profile": {"name": "pi-compaction-lane"},
                "workspace": {"root": "workspace"},
                "provider_tools": {
                    "use_native": True,
                    "api_variant": "chat_completions" if legacy else "responses",
                },
                "providers": {
                    "default_model": model_id,
                    "models": [{
                        "id": model_id, "adapter": "openai",
                        "context_length": context_window,
                        "route_handle_id": "route-a", "credential_handle_id": "credential-a",
                        "params": {},
                        "response_policy": {
                            "schema_version": "bb.provider_native_response_policy.v1",
                            "consumer_id": consumer,
                            "provider_profile_digest": h.profile_identity_digest(profile),
                            "max_response_bytes": 1_048_576, "max_stream_fragments": 10_000,
                        },
                    }],
                },
            },
            cas=cas, options=h._options(),
            request_schema_version="bb.rl.headless-run-request.v2",
        )
        manifest = compiled.manifest
        projection = h.E4TargetPolicyProjection.from_compiled(manifest)
        semantics = manifest.semantic.to_canonical_obj()
    finally:
        cas.close()
    features = ["max_tokens", "n", "stream_options", "streaming"]
    features += ["strict_tools"] if legacy else ["store"]
    observation = h._observation(
        provider_id="openai", model_id=model_id,
        capabilities=h._policy_capabilities(request_features=sorted(features)),
    )
    tool_order = load_target_document(target_id, "native-config.json")["tools"]["ordered"]
    tools = tuple(h._tool_grant(name) for name in sorted(tool_order))
    harness_path = resolve_target_harness_path(target_id)
    controls = yaml.safe_load(harness_path.read_text())["policy"]["execution"]["bounded_controls"]
    plan = h._plan(
        observation=observation, semantics=semantics, tools=tools,
        policy_slot_ids=(f"model:{model_id}",),
        limit_updates={
            "max_turns": controls["max_model_calls"],
            "action_timeout_ms": controls["single_tool_wall_seconds"] * 1000,
            "observation_bytes": controls["raw_evidence_bytes_per_outcome"],
        },
        implementation_digest=h.CONDUCTOR_IMPLEMENTATION_DIGEST,
    )
    base = plan.base_compiled.model_dump(mode="python")
    base.update(
        manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        compiler_input_digest=manifest.inputs.compiler_input_digest,
    )
    payload = plan.model_dump(mode="python")
    payload["base_compiled"] = h.c.CompiledArtifactIdentity.model_validate(base)
    plan = h.c.EffectiveExecutionPlan.model_validate(payload)
    grants = tuple(h.RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools)
    worker = (
        _Pi057WorkerPort(workdir, workdir / ".scratch", grants)
        if legacy else h._NativeWorkerPort(workdir, grants)
    )
    client = h.EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-pi-compaction", effective_plan_digest=plan.canonical_digest(),
        observation=observation, profile=profile, target_projection=projection, timeout_seconds=45,
    )
    request = h.RunnerOpenRequest(episode_id="episode-pi-compaction", effective_plan=plan)
    sink = h._Events()
    session = await h.ConductorAdapter(
        h.CONDUCTOR_RUNTIME_ABI,
        containment_authenticator=h.CONDUCTOR_TEST_AUTHENTICATOR,
        admitted_lease_ledger=h.CONDUCTOR_TEST_LEDGER,
    ).open(
        request, policy=h.PolicyRuntimeBinding(request, client), workspace=worker,
        cancellation=h._Cancellation(), events=sink,
    )
    try:
        result = await session.run(h.ConductorRunRequest(task_input={"prompt": prompt}, context={}))
    except h.RunnerDependencyError as exc:
        cause: BaseException | None = exc
        while cause is not None and not isinstance(cause, NativeProviderRequestFailure):
            cause = cause.__cause__
        if not terminal_overflow or cause is None or not is_context_overflow(cause):
            raise
        result = exc
    finally:
        await session.close()
        await worker.close()
        await client.close()
    limits = plan.effective_capabilities.limits.model_dump(mode="json")
    sources = {
        name: "tests/rl/harness/test_runner_policy_runtime.py:_plan limit_payload"
        for name in limits
    }
    for name, key in [
        ("max_turns", "max_model_calls"),
        ("action_timeout_ms", "single_tool_wall_seconds"),
        ("observation_bytes", "raw_evidence_bytes_per_outcome"),
    ]:
        sources[name] = f"{harness_path.relative_to(REPO_ROOT)}:policy.execution.bounded_controls.{key}"
    # A raised Pi073 dependency failure has no returned replay trace; preserve
    # that absence rather than synthesizing one from captured HTTP requests.
    trace_count = (
        None if isinstance(result, Exception)
        else len(result.response["replay_trace"]["requests"])
    )
    return result, sink.events, {
        "limits": limits, "limit_sources": sources,
        "bb_replay_trace_request_count": trace_count,
    }
