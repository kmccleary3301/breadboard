from __future__ import annotations
from pathlib import Path
import json
import pytest
from breadboard.rl.harness.omp_native_tools import NativeToolWorker, NativeWorkerPhaseError
from typing import Any

from tests.rl.harness.omp_18_1_17_test_support import (
    OMP_AVAILABLE,
    _authority_payload,
    _description_policy,
    _lease_model,
    _model_registry,
    _pinned_source,
    _run_stock_compaction_oracle,
    _runtime_spec,
    _sample_conversation_messages,
    _test_denials,
)

@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
@pytest.mark.parametrize("terminal_text", [False, True])
def test_agent_end_stock_continuation_gate(tmp_path: Path, terminal_text: bool) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.phase("initialize", {
            "task": "continue after compaction",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [*_sample_conversation_messages(), {
            "role": "assistant", "stopReason": "stop",
            "content": [{"type": "text", "text": "done"}] if terminal_text else [],
        }]
        prepared = worker.phase("prepare_compaction", {
            "messages": messages, "reason": "threshold", "checkpoint": "agent_end",
            "context_window": 131072, "usage": {"prompt_tokens": 120000, "completion_tokens": 1000},
            "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
        })
        assert prepared["kind"] == "compaction_prepared"
        payload = {"preparation": prepared["preparation"], "summary": "Summary.", "turn_prefix_summary": "Prefix."}
        finalized = worker.phase("finalize_compaction", payload)
        if finalized["kind"] == "compaction_followup_request":
            finalized = worker.phase("finalize_compaction", {**payload, "followup_summaries": ["Short summary."]})
        assert finalized["kind"] == "compaction_finalized"
        if terminal_text:
            assert "continuation" not in finalized
        else:
            prompt = _pinned_source("packages/coding-agent/src/prompts/system/auto-continue.md")
            assert finalized["continuation"] == [{
                "role": "developer", "content": [{"type": "text", "text": prompt}],
                "attribution": "agent", "synthetic": True, "timestamp": 1000 + len(messages),
            }]
        # Stock invalidates billed usage from an assistant before the new compaction
        # (session-maintenance.ts:2465-2477), retaining the stored-history floor.
        next_preparation = worker.phase("prepare_compaction", {
            "messages": [*finalized["messages"], *finalized.get("continuation", [])],
            "reason": "threshold", "checkpoint": "before_request", "context_window": 131072,
            "usage": {"prompt_tokens": 120000, "completion_tokens": 1000},
            "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
        })
        assert next_preparation["kind"] == "compaction_unavailable"
        assert next_preparation["reason"] == "not_triggered"
    finally:
        worker.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_source_body", [False, True])
async def test_handoff_tool_choice_reaches_transport_without_changing_main_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture_source_body: bool,
) -> None:
    import httpx
    from breadboard.rl.harness.runners.base import PolicyRuntimeInvokeRequest, freeze_json_object, thaw_json
    from breadboard_engine.compilation.contracts import canonical_sha256
    from breadboard_engine.compilation.provider_response import OMP_RESPONSE_CONSUMER_ID
    from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
    from tests.rl.harness.test_policy_provider import _unbound_native_chat_client, _OPENHANDS_EPISODE, _digest
    from tests.rl.harness import test_policy_provider as fixtures

    profile_type = fixtures.OpenAICompletionsProviderProfile

    def streaming_profile(**kwargs: Any) -> Any:
        return profile_type(**{**kwargs, "capabilities": {
            **kwargs["capabilities"], "supports_streaming": True, "supports_store": True,
        }})

    monkeypatch.setattr(fixtures, "OpenAICompletionsProviderProfile", streaming_profile)

    factory = OpenAIChatRuntime.create_client_from_profile
    client, plan, _ = _unbound_native_chat_client(
        tmp_path, monkeypatch, target_id="oh-my-pi-r2@18.1.17",
        consumer_id=OMP_RESPONSE_CONSUMER_ID, model="model-a",
        request_policy={"mode": "streaming", "include_usage": True, "max_token_field": "max_completion_tokens",
                        "strict_tools": None, "enable_thinking": None},
        sampling={"n": 1}, max_token_feature="max_completion_tokens",
        request_features=["max_completion_tokens", "n", "store", "stream_options", "streaming"],
    )
    client._transport.close()
    client._transport = factory(client._runtime, client._profile)
    bodies: list[bytes] = []
    source_bodies: list[bytes] = []

    def send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
        bodies.append(request.content)
        chunk = {
            "id": f"response-{len(bodies)}", "object": "chat.completion.chunk", "created": 1, "model": "model-a",
            "choices": [{"index": 0, "finish_reason": "stop", "delta": {"role": "assistant", "content": "done"}}],
        }
        return httpx.Response(200, request=request, headers={"content-type": "text/event-stream"},
                              content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())

    monkeypatch.setattr(client._transport.http_client, "send", send)
    client.bind_compiled_plan(plan)
    tools = tuple(thaw_json(tool) for tool in client._target_projection.chat_tools)
    client.bind_native_stream("stock base system", tools, accept_truncated_stream=True)
    main = {
        "model": "model-a", "messages": [{"role": "system", "content": "stock base system"}, {"role": "user", "content": "task"}],
        "tools": list(tools),
    }
    summary = {**main, "tool_choice": "none"}
    try:
        for turn, body in enumerate((main, summary, main), 1):
            native_request_body_json = None
            if capture_source_body:
                source_body = client._runtime.profile_chat_request(
                    client._profile, body["messages"], body["tools"],
                    context=ProviderRuntimeContext(None, {}, extra={
                        "response_consumer_id": OMP_RESPONSE_CONSUMER_ID,
                        **({"tool_choice": "none"} if turn == 2 else {}),
                    }),
                )
                # Any source ordering is authoritative, not an SDK field table.
                source_body = dict(reversed(tuple(source_body.items())))
                native_request_body_json = json.dumps(
                    source_body, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                )
                source_bodies.append(native_request_body_json.encode("utf-8"))
            await client.invoke(PolicyRuntimeInvokeRequest(
                episode_id=_OPENHANDS_EPISODE, effective_plan_digest=plan.canonical_digest(),
                binding_digest=_digest("binding"), policy_slot_id="model:model-a",
                request_digest=canonical_sha256(body), request_payload=freeze_json_object(body, field_name="request"),
                turn=turn, attempt=1, compaction_summary=turn == 2,
                native_request_body_json=native_request_body_json,
            ))
    finally:
        await client.close()
    assert json.loads(bodies[1])["tool_choice"] == "none"
    assert "tool_choice" not in json.loads(bodies[0])
    assert bodies[0] == bodies[2]
    if capture_source_body:
        assert bodies == source_bodies


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
@pytest.mark.asyncio
async def test_conductor_large_tool_batch_compacts_from_stored_floor_and_matches_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    from dataclasses import replace
    from breadboard.rl.harness import native_stream_profiles
    from breadboard.rl.harness.runners import conductor as conductor_module, omp_semantics
    from breadboard.rl.harness.runners.base import thaw_json
    from breadboard.rl.harness.runners.conductor import ConductorRunRequest
    from breadboard_engine.provider.native_response import NativeProviderResponse
    from tests.rl.harness.test_runner_conductor import _native_close_test_case, _open, _digest

    lease = {**_lease_model(), "id": "model-a", "name": "Model A"}
    settings = {"methodOrder": ["handoff", "soft"], "keepRecentTokens": 10}
    plan, client, tools = _native_close_test_case(
        monkeypatch, agent_end_compaction=True, action_timeout_ms=40000,
        runtime_profile_override={"context_window": lease["contextWindow"], "compaction_settings": settings},
        limit_updates_override={"max_turns": 3, "observation_bytes": 2_000_000, "transcript_bytes": 8_000_000, "response_bytes": 4_000_000},
    )
    authority = _authority_payload(tmp_path)
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    states = []
    histories = []
    prepared_results = []
    original_profile = next(iter(conductor_module.NATIVE_STREAM_PROFILES.values()))

    def state_factory(task: str, system_prompt: str, bootstrap: dict[str, Any]) -> Any:
        state = omp_semantics.OMPSemanticsState(
            task="previous context " * 30000, system_prompt=system_prompt, request_cap=4,
        )
        state.begin_query()
        state.prepare_response(NativeProviderResponse(
            binding_digest="binding", request_digest="request", response_id="prior-turn", model=lease["id"],
            content="Prior task completed.", finish_reason="stop",
        ), usage=worker.phase("parse_usage", {"usage": None})["usage"])
        state.resume_after_compaction([{"role": "user", "content": [{"type": "text", "text": task}]}])
        states.append(state)
        return state

    profile = replace(
        original_profile, phase_schema_version=omp_semantics.PHASE_SCHEMA_VERSION,
        state_factory=state_factory, state_module=omp_semantics,
        max_turns=plan.effective_capabilities.limits.max_turns,
        action_timeout_ms=plan.effective_capabilities.limits.action_timeout_ms,
        episode_timeout_seconds=120, compaction_checkpoints=frozenset({"before_request"}),
        assistant_usage_phase="parse_usage",
        runtime_input_names=native_stream_profiles.NATIVE_STREAM_PROFILES[omp_semantics.CONSUMER_ID].runtime_input_names,
    )
    monkeypatch.setattr(native_stream_profiles, "NATIVE_STREAM_PROFILES", {profile.consumer_id: profile})
    monkeypatch.setattr(conductor_module, "NATIVE_STREAM_PROFILES", {profile.consumer_id: profile})
    monkeypatch.setattr(type(tools), "declared_workspace", property(lambda self: str(tmp_path)))
    monkeypatch.setattr(tools, "native_runtime_inputs", lambda **kwargs: dict(authority["runtime_inputs"]))
    original_phase = tools.invoke_native_phase

    async def phase(operation: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if operation == "initialize":
            return worker.phase(operation, {
                "task": "large batch", "model_config": lease, **authority,
                "compaction": True,
                "advertisement": {
                    "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                    "capability_denials": _test_denials(),
                },
            })
        if operation == "execute_batch":
            result = dict(await original_phase(operation, payload, **kwargs))
            result["schema_version"] = omp_semantics.PHASE_SCHEMA_VERSION
            # A scripted native tool batch, committed through production semantics.
            result["results"][0]["id"] = f"call-{main_count}"
            result["results"][0]["content"] = [{"type": "text", "text": "large payload " * 30000 if main_count == 1 else "Small result."}]
            return result
        if operation == "close":
            worker.stop()
            result = dict(await original_phase(operation, payload, **kwargs))
            result["schema_version"] = omp_semantics.PHASE_SCHEMA_VERSION
            return result
        if operation == "prepare_compaction":
            histories.append(copy.deepcopy(payload["messages"]))
            result = worker.phase(operation, payload)
            prepared_results.append((result.get("kind"), result.get("reason")))
            return result
        return worker.phase(operation, payload)

    monkeypatch.setattr(tools, "invoke_native_phase", phase)
    original_invoke = client.invoke
    main_count = 0
    client.responses.clear()

    async def invoke(request: Any) -> Any:
        nonlocal main_count
        # Script the provider receipt from the actual pinned SDK capture, not
        # the separate semantic replay snapshot.
        body = json.loads(request.native_request_body_json)
        summary = request.compaction_summary
        if not summary:
            main_count += 1
        response = {
            "binding_digest": _digest("native-binding"), "request_digest": conductor_module.canonical_sha256(body).removeprefix("sha256:"),
            "request_body": body, "response_id": f"native-{len(client.requests)}",
            "finish_reason": "stop" if summary or main_count == 3 else "toolUse",
            "content": "Batch summary." if summary else ("done" if main_count == 3 else "Inspecting."),
            "tool_calls": [] if summary or main_count == 3 else [{"id": f"call-{main_count}", "name": "read", "arguments": '{"path":"journal.py"}'}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050},
            "stream_fragments": [],
        }
        client.responses.append({"native_response": response})
        return await original_invoke(request)

    monkeypatch.setattr(client, "invoke", invoke)
    session, *_ = await _open(observation=client.observation, plan=plan, client=client, tools=tools)
    try:
        await session.run(ConductorRunRequest({"prompt": "large batch"}))
        main_requests = [thaw_json(request.request_payload) for request in client.requests if not request.compaction_summary]
        assert len(main_requests) == 3
        assert len([request for request in client.requests if request.compaction_summary]) == 1, prepared_results
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": lease, "messages": histories[-1],
            "settings": settings, "summaries": ["Batch summary."], "action": "handoff",
        })
        assert main_requests[-1]["messages"] == stock["mainMessages"]
        assert states[0].messages[-1]["usage"]["input"] == 1000
    finally:
        await session.close()
        worker.stop()


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
def test_overflow_projection_uses_replay_timestamp(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.phase("initialize", {
            "task": "replay overflow", "model_config": _lease_model(), "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        payload = {
            "http_status": 400, "messages": [],
            "provider_request_duration_ms": 12.5,
            "response_body_text": json.dumps({"error": {
                "message": "Your input exceeds the context window of this model.",
                "type": "invalid_request_error", "code": "context_length_exceeded",
            }}),
        }
        first = worker.phase("project_provider_failure", payload)
        second = worker.phase("project_provider_failure", payload)
        # The pinned manager/session initialization consumes two replay IDs.
        assert first["message"]["timestamp"] == 1002
        assert first == second
        assert first["message"]["duration"] == 12.5
        for invalid in (None, True, -1):
            with pytest.raises(NativeWorkerPhaseError, match="recorded provider request duration"):
                worker.phase("project_provider_failure", {
                    **payload, "provider_request_duration_ms": invalid,
                })
        with pytest.raises(NativeWorkerPhaseError, match="recorded provider request duration"):
            worker.phase("project_provider_failure", {
                key: value for key, value in payload.items() if key != "provider_request_duration_ms"
            })
    finally:
        worker.stop()


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
@pytest.mark.parametrize("method_order", [["handoff"], ["soft"], ["shake", "soft"]])
@pytest.mark.parametrize("tool_text", ["record payload value\n" * 1800, "x"], ids=["positive_savings", "zero_savings"])
@pytest.mark.asyncio
async def test_failed_preparation_preserves_native_rescue_history(
    tmp_path: Path, method_order: list[str], tool_text: str,
) -> None:
    from breadboard.rl.harness.runners.native_compaction import compact_history

    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    lease = {**_lease_model(), "contextWindow": 4000}
    messages = [
        {"role": "user", "content": "Inspect the records."},
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": "read-1", "name": "read", "arguments": {"path": "records.txt"}},
        ], "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": "read-1", "toolName": "read", "isError": False,
         "content": [{"type": "text", "text": tool_text}]},
    ]
    prepared_results = []
    try:
        worker.phase("initialize", {
            "task": "retain rescue edits", "model_config": lease, "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })

        async def phase(operation, payload):
            result = worker.phase(operation, dict(payload))
            prepared_results.append(result)
            return result

        async def no_summary(request, native_request_body_json=None):
            pytest.fail("Failed preparation must not issue a summary request")

        settings = {"methodOrder": method_order}
        if "shake" in method_order:
            settings["shake"] = {"protectTokens": 0, "minSavings": 0}
        usage = {"prompt_tokens": 50000, "completion_tokens": 0, "total_tokens": 50000}
        compacted = await compact_history(
            messages, reason="threshold", checkpoint="before_request", usage=usage,
            context_window=4000, settings=settings, model_id=lease["id"], tools=[],
            phase=phase, exchange=no_summary, trace_requests=[],
        )
        prepared = prepared_results[0]
        assert prepared["kind"] == "compaction_unavailable"
        assert prepared["reason"] == "nothing_to_compact"
        assert prepared["history_rewritten"] is True
        assert compacted is not None
        assert compacted.event["kind"] == "history_rewrite"
        assert compacted.retry is False
        assert compacted.continuation is None
        assert compacted.messages == prepared["messages"]
        assert compacted.messages != messages
        projected = worker.phase("project_request", {"messages": compacted.messages})
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": lease, "messages": messages,
            "settings": settings, "action": "shake" if "shake" in method_order else "rescue_only",
        })
        assert stock["wireRequests"] == []
        assert stock["finalMessages"] == compacted.messages
        assert stock["mainMessages"] == projected["messages"]
        if tool_text == "x":
            assert stock["shakeResult"]["tokensFreed"] == 0
            assert stock["shakeResult"]["toolResultsDropped"] + stock["shakeResult"]["blocksDropped"] > 0
        # Re-check the committed prefix: no old tool body may be restored.
        repeated = await compact_history(
            compacted.messages, reason="threshold", checkpoint="before_request",
            usage=usage, context_window=4000, settings=settings,
            model_id=lease["id"], tools=[], phase=phase, exchange=no_summary, trace_requests=[],
        )
        assert repeated is None
        assert worker.phase("project_request", {"messages": compacted.messages}) == projected
    finally:
        worker.stop()


def test_provider_failure_duration_capability_is_source_specific() -> None:
    from breadboard.rl.harness.runners import omp_semantics
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES

    assert [p.state_module for p in NATIVE_STREAM_PROFILES.values()
            if p.provider_failure_requires_duration] == [omp_semantics]


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
def test_zero_savings_shake_reclaims_overflow_without_falling_back(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    messages = _sample_conversation_messages()[:3]
    messages[2]["content"] = [{"type": "text", "text": "x"}]
    settings = {"methodOrder": ["shake", "soft"], "shake": {"protectTokens": 0, "minSavings": 0}}
    lease = _lease_model()
    try:
        worker.phase("initialize", {
            "task": "retain zero-savings rewrite", "model_config": lease, "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        prepared = worker.phase("prepare_compaction", {
            "messages": messages, "reason": "overflow", "checkpoint": "overflow",
            "context_window": lease["contextWindow"],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 0, "total_tokens": 1000},
            "settings": settings,
        })
        assert prepared["kind"] == "compaction_prepared"
        assert prepared["preparation"]["method"] == "shake"
        assert prepared["summary_request"] is None
        assert prepared["turn_prefix_request"] is None
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": lease, "messages": messages,
            "settings": settings, "action": "shake",
        })
        assert stock["shakeResult"]["tokensFreed"] == 0
        assert stock["shakeResult"]["toolResultsDropped"] == 1
        assert stock["finalMessages"] == prepared["preparation"]["shakenMessages"]
    finally:
        worker.stop()

