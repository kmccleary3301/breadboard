from __future__ import annotations
import json
from pathlib import Path
import pytest

from tests.rl.harness.openclaw_2026_9_4_test_support import (
    _compact_in_production_phases,
    _make_conversation_history,
    _run_stock_compaction_direct,
    stock_runtime,
    worker,
)

pytestmark = pytest.mark.usefixtures("stock_runtime")

def test_invalid_summary_retries_once_with_real_stock_work(worker):
    messages = _make_conversation_history(3)
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=["", "Recovered summary", "Turn prefix"],
        tools=worker.tool_schemas,
    )
    result, phases, exchanges = _compact_in_production_phases(
        worker, messages, reason="overflow", settings=settings,
        answers=["", "Turn prefix", "Recovered summary"],
    )
    finalized_payloads = [payload for operation, payload in phases if operation == "finalize_compaction"]
    assert finalized_payloads[0]["summary"] == ""
    assert finalized_payloads[0]["turn_prefix_summary"] == "Turn prefix"
    assert finalized_payloads[1]["followup_summaries"] == ["Recovered summary"]
    assert [request["messages"] for request in exchanges] == [
        stock["captured_requests"][0]["messages"],
        stock["captured_requests"][2]["messages"],  # prefetched prefix, not the history retry
        stock["captured_requests"][1]["messages"],
    ]
    assert result.messages == stock["messages"]


@pytest.mark.parametrize("overflow_mode", ["http_error", "length"])
def test_real_conductor_compacts_production_history_and_preserves_main_wire(tmp_path, overflow_mode):
    """Use unchanged openclaw/2026.9.4-r2 catalog bytes and native 131072 sizing.

    The shared fixture's providers.models[].context_length and provider profile's
    context_window are set to 131072 (rather than its unrelated 32768 default).
    No projection semantics, reserve or keepRecentTokens are overridden. The
    inherited chat_tools array thaw preserves JSON and asserts every other
    projection field unchanged.
    The length case uses stock's zero-output, 99%-window overflow branch
    (@openclaw/ai/dist/internal/runtime.mjs:195-197), without an HTTP failure.
    """
    import asyncio
    from tests.rl.harness.openclaw_native_stream_test_support import (
        RunnerTermination, _NativeWorkerPort, _run_episode,
    )
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    from breadboard.rl.harness.runners.openclaw_semantics import OPENCLAW_CONSUMER_ID
    summary_prompt = NATIVE_STREAM_PROFILES[OPENCLAW_CONSUMER_ID].compaction_summary_system_prompt
    import yaml
    catalog_path = Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4-r2/harness.yaml"
    catalog = yaml.safe_load(catalog_path.read_text())
    # Both evidence envelopes use the unchanged catalog's per-outcome byte cap,
    # rather than the shared fixture's smaller unrelated observation default.
    evidence_bytes = catalog["policy"]["execution"]["bounded_controls"]["raw_evidence_bytes_per_outcome"]

    ports = []

    class RecordingWorker(_NativeWorkerPort):
        def __init__(self, workspace, grants):
            super().__init__(workspace, grants)
            self.compactions = []
            self.compaction_outcomes = []
            ports.append(self)

        def native_runtime_inputs(self, *, input_names, package_subpath):
            runtime = dict(super().native_runtime_inputs(
                input_names=input_names, package_subpath=package_subpath,
            ))
            runtime.update(current_date="2026-10-09", message_timestamp_ms="1728432000000")
            return runtime

        async def invoke_native_phase(self, operation, payload, *, timeout_ms, package_subpath=None):
            result = await super().invoke_native_phase(
                operation, payload, timeout_ms=timeout_ms, package_subpath=package_subpath,
            )
            if operation in {"prepare_compaction", "finalize_compaction"}:
                self.compaction_outcomes.append((operation, result.get("kind"), result.get("reason")))
            if operation == "initialize":
                self.model_config = json.loads(json.dumps(payload["model_config"]))
                self.tool_schemas = result["tool_schemas"]
            elif operation == "prepare_compaction" and result["kind"] == "compaction_prepared":
                self.compactions.append({"input": json.loads(json.dumps(payload)), "prepared": result})
            elif operation == "finalize_compaction" and result["kind"] == "compaction_finalized":
                self.compactions[-1].update(
                    answers=json.loads(json.dumps(payload)), finalized=result,
                    next_wire=await super().invoke_native_phase(
                        "project_request", {"messages": result["messages"]}, timeout_ms=timeout_ms,
                    ),
                )
            return result

    main_requests = []
    summary_responses = []
    for number in range(1, 7):
        (tmp_path / f"observed{number}.txt").write_text(f"Native tool observation {number}\n" * 800)

    def responses(_ordinal, requests):
        request = requests[-1]
        if request["messages"][0]["content"] == summary_prompt:
            summary = "" if not summary_responses else f"Native summary {len(summary_responses)}"
            summary_responses.append(summary)
            return {"content": summary}
        main_requests.append(request)
        if len(main_requests) <= 5:
            number = len(main_requests)
            return {"calls": [(f"callread{number}", "read", {"path": f"observed{number}.txt"})],
                    "usage": {"prompt_tokens": number * 5_000, "completion_tokens": 10,
                              "total_tokens": number * 5_000 + 10}}
        if len(main_requests) == 6:
            if overflow_mode == "length":
                return {"content": "", "finish_reason": "length", "usage": {
                    "prompt_tokens": 131_072, "completion_tokens": 0, "total_tokens": 131_072,
                }}
            return {"http_error": 400, "body": {"error": {
                "code": "context_length_exceeded", "message": "context length exceeded",
            }}}
        if len(main_requests) == 7:
            return {"calls": [("callretry", "read", {"path": "observed6.txt"})],
                    "usage": {"prompt_tokens": 30_000, "completion_tokens": 10, "total_tokens": 30_010}}
        assert len(main_requests) == 8
        return {"content": "Done inspecting everything", "usage": {
            "prompt_tokens": 115_000, "completion_tokens": 20, "total_tokens": 115_020,
        }}

    result, _requests, _events, system_prompt, _operations = asyncio.run(_run_episode(
        tmp_path, responses, worker_factory=RecordingWorker,
        target_id="openclaw-r2@2026.9.4", context_window=131_072,
        evidence_limits={"observation_bytes": evidence_bytes, "transcript_bytes": evidence_bytes},
    ))
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    port = ports[0]
    assert len(main_requests) == 8, (len(main_requests), port.compaction_outcomes)
    assert [record["input"]["checkpoint"] for record in port.compactions] == [
        "agent_end" if overflow_mode == "length" else "overflow", "agent_end",
    ], port.compaction_outcomes
    assert [record["finalized"]["reason"] for record in port.compactions] == ["overflow", "threshold"]
    assert [record["finalized"]["retry"] for record in port.compactions] == [True, False]
    if overflow_mode == "length":
        cutoff = port.compactions[0]["input"]["messages"][-1]
        assert cutoff["stopReason"] == "length"
        assert (cutoff["api"], cutoff["provider"], cutoff["model"]) == (
            port.model_config["api"], port.model_config["provider"], port.model_config["id"],
        )
        assert cutoff not in port.compactions[0]["finalized"]["messages"]
    assert any(
        message["role"] == "toolResult" and message["content"]
        for message in port.compactions[0]["input"]["messages"]
    )
    terminal = port.compactions[1]["input"]["messages"][-1]
    assert terminal["content"] == [{"type": "text", "text": "Done inspecting everything"}]
    retained = [
        message for message in port.compactions[0]["finalized"]["messages"]
        if message["role"] == "assistant" and message.get("providerUsage")
    ]
    assert retained
    assert all(message["providerUsage"]["prompt_tokens"] > 0 for message in retained)
    assert all(message["usage"]["totalTokens"] == 0 for message in retained)
    source_entries = None
    source_messages = None
    for record in port.compactions:
        prepared, answers = record["prepared"], record["answers"]
        scripted = []
        if prepared["summary_request"] is not None:
            scripted.append(answers["summary"])
        if prepared["turn_prefix_request"] is not None:
            scripted.append(answers["turn_prefix_summary"])
        scripted.extend(answers.get("followup_summaries", []))
        stock = _run_stock_compaction_direct(
            record["input"]["messages"], reason=record["input"]["reason"],
            settings=record["input"].get("settings"), usage=record["input"].get("usage"),
            scripted_responses=scripted, tools=port.tool_schemas, model_config=port.model_config,
            # The initial wire's source-rendered runtime fragment is fixed episode context.
            runtime_wire_message=main_requests[0]["messages"][-1],
            message_timestamp_ms=1728432000000,
            source_entries=source_entries,
            source_messages=source_messages,
        )
        source_entries = stock["source_entries"]
        source_messages = stock["messages"]
        assert stock["settings"] == {"enabled": True, "reserveTokens": 20000, "keepRecentTokens": 20000}
        assert record["finalized"]["messages"] == stock["messages"]
        assert record["finalized"]["messages"][0] == {"role": "system", "content": system_prompt}
        assert record["next_wire"]["tools"] == stock["wire"]["tools"]
        if record["finalized"]["reason"] == "overflow":
            # AgentSession owns durable history, not the embedded caller's
            # transient continuation (embedded-agent-CE9KzQvy.mjs:5898,
            # 5961-5967,6063-6068). The real agent-exec lane pins its exact wire.
            next_messages = record["next_wire"]["messages"]
            assert next_messages[:-2] == stock["wire"]["messages"][:-1]
            assert next_messages[-1] == stock["wire"]["messages"][-1]
            assert next_messages[-2]["role"] == "user"
            assert main_requests[6]["messages"] == next_messages
        else:
            assert record["next_wire"]["messages"] == stock["wire"]["messages"]
            assert stock["retry"] is False
            assert "continuation" not in record["finalized"]


def test_missing_provider_usage_does_not_trigger_native_threshold(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Large context " * 40_000}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done"}], "stopReason": "stop"},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    stock = _run_stock_compaction_direct(
        messages, reason="threshold", settings=settings, scripted_responses=[],
        tools=worker.tool_schemas,
    )
    assert stock["status"] == "not_triggered"
    assert stock["captured_requests"] == []
    original = json.loads(json.dumps(messages))
    result, phases, exchanges = _compact_in_production_phases(
        worker, messages, reason="threshold", settings=settings, answers=[],
    )
    assert result is None
    assert [operation for operation, _payload in phases] == ["prepare_compaction"]
    assert exchanges == []
    assert messages == original


def test_failed_native_threshold_settles_with_original_history(worker):
    messages = _make_conversation_history(3)
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    usage = {"input": 115000, "output": 20, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 115020}
    stock = _run_stock_compaction_direct(
        messages, reason="threshold", settings=settings, usage=usage,
        scripted_responses=["", ""], tools=worker.tool_schemas,
    )
    assert stock["status"] == "failed"
    assert stock["retry"] is False
    original = json.loads(json.dumps(messages))
    result, phases, exchanges = _compact_in_production_phases(
        worker, messages, reason="threshold", settings=settings, usage=usage,
        answers=["", "Unused prefix", ""],
    )
    assert result is None
    assert messages == original
    finalize_payloads = [payload for operation, payload in phases if operation == "finalize_compaction"]
    assert finalize_payloads[-1]["followup_summaries"] == [""]
    assert [request["messages"] for request in exchanges if request["messages"] == stock["captured_requests"][0]["messages"]] == [
        request["messages"] for request in stock["captured_requests"]
    ]

