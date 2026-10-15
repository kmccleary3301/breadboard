from __future__ import annotations
import json
import os
import subprocess
import pytest

from tests.rl.harness.openclaw_2026_9_4_test_support import (
    _DEFAULT_DIST,
    _WorkerClient,
    _compact_in_production_phases,
    _make_conversation_history,
    _run_stock_compaction_direct,
    stock_runtime,
    worker,
)

pytestmark = pytest.mark.usefixtures("stock_runtime")

def test_stock_request_budget_caps_summary_with_episode_tools(tmp_path):
    worker = _WorkerClient(_DEFAULT_DIST, tmp_path / "budget", context_window=32_768)
    try:
        messages = [{"role": "system", "content": "S" * 100_000}, *_make_conversation_history(4)]
        settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
        stock = _run_stock_compaction_direct(
            messages, reason="overflow", settings=settings,
            scripted_responses=["Completed prior work", "Current turn"],
            tools=worker.tool_schemas, model_config=worker.model_config,
        )
        prepared = worker.request({
            "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
            "messages": messages, "settings": settings,
        })
        requests = [prepared[key] for key in ("summary_request", "turn_prefix_request") if prepared.get(key)]
        assert requests == stock["captured_requests"]
        assert requests[0]["max_tokens"] < int(8192 * 0.8)
        finalized = worker.request({
            "phase": "finalize_compaction", "summary": "Completed prior work",
            "turn_prefix_summary": "Current turn", "preparation": prepared["preparation"],
        })
        assert finalized["messages"] == stock["messages"]
    finally:
        worker.close()


def test_summary_trace_preserves_proxy_clamp_near_production_window(worker):
    # Preparation-only synthetic branch: fit/retention bounds do not themselves
    # bound summarization input. R2 uses window131072, reserve20000, cap2048.
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "x" * 250400}]},
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": f"r{i}", "name": "read", "arguments": {"path": f"file{i}.txt"}}
            for i in range(20)
        ]},
        *[
            {"role": "toolResult", "toolCallId": f"r{i}", "toolName": "read",
             "content": [{"type": "text", "text": "漢" * 2000}], "isError": False}
            for i in range(20)
        ],
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": "last", "name": "read", "arguments": {"path": "last.txt"}}
        ]},
        {"role": "toolResult", "toolCallId": "last", "toolName": "read",
         "content": [{"type": "text", "text": "last result"}], "isError": False},
    ]
    settings = {"reserveTokens": 20000, "keepRecentTokens": 20000}
    summary = "Context retained"
    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings, scripted_responses=[summary],
        tools=worker.tool_schemas, model_config=worker.model_config,
    )
    prepared = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages, "settings": settings,
    })
    assert prepared["summary_request"] is None
    assert prepared["turn_prefix_request"] == stock["captured_requests"][0]
    assert prepared["turn_prefix_request"]["max_tokens"] == 1985
    from breadboard_engine.compaction_lanes.openclaw_lane import source_input_tokens
    source_request = prepared["turn_prefix_request"]
    assert source_input_tokens(source_request) == 129086
    assert source_input_tokens({**source_request, "tools": worker.tool_schemas}) == 130741
    assert 129086 + source_request["max_tokens"] == 131071
    assert 130741 + worker.model_config["maxTokens"] - worker.model_config["contextWindow"] == 1717
    assert len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) < 1_048_576
    finalized = worker.request({
        "phase": "finalize_compaction", "summary": None, "turn_prefix_summary": summary,
        "preparation": prepared["preparation"],
    })
    assert finalized["messages"] == stock["messages"]


def test_prepare_suspends_before_fitting_a_tiny_summary_budget(tmp_path):
    """Explicit test model contextWindow=32768; stock sizes every request and summary."""
    worker = _WorkerClient(_DEFAULT_DIST, tmp_path / "tiny-budget", context_window=32_768)
    try:
        history = [
            {"role": "user", "content": [{"type": "text", "text": "Prior completed task"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "A" * 40_000}], "stopReason": "stop"},
            {"role": "user", "content": [{"type": "text", "text": "Next task"}]},
        ]
        settings = {"reserveTokens": 16384, "keepRecentTokens": 100}
        usage = {"input": 30000, "output": 20, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 30020}
        low, high = 0, worker.model_config["contextWindow"] * 4
        probes = []
        for _ in range(20):
            size = (low + high) // 2
            messages = [{"role": "system", "content": "S" * size}, *history]
            prepared = worker.request({
                "phase": "prepare_compaction", "reason": "threshold", "checkpoint": "agent_end",
                "messages": messages, "usage": usage, "settings": settings,
            })
            if prepared["kind"] != "compaction_prepared":
                probes.append((size, prepared.get("reason")))
                high = size - 1
                continue
            request_budget = prepared["preparation"]["requestBudget"]
            # Stay before stock's history-only crossover (resource-loader:8889-8892).
            if request_budget["fixedTokens"] + request_budget["pendingTokens"] >= request_budget["contextWindow"] - request_budget["reserveTokens"]:
                high = size - 1
                continue
            budget = prepared["preparation"]["summaryTokenBudget"]
            probes.append((size, budget))
            if 1 <= budget <= 2:
                break
            if budget > 2:
                low = size + 1
            else:
                high = size - 1
        else:
            pytest.fail(f"No stock-sized one/two-token summary boundary: {probes}")
        assert prepared["turn_prefix_request"] is None
        stock = _run_stock_compaction_direct(
            messages, reason="threshold", settings=settings, usage=usage,
            scripted_responses=["x"], tools=worker.tool_schemas, model_config=worker.model_config,
        )
        assert stock["status"] == "completed"
        result, _phases, _exchanges = _compact_in_production_phases(
            worker, messages, reason="threshold", settings=settings, usage=usage, answers=["x"],
        )
        assert result.messages == stock["messages"]
    finally:
        worker.close()


@pytest.mark.parametrize("reason,reported_usage", [("threshold", False), ("threshold", True), ("overflow", True)])
def test_native_threshold_gates_or_frames_planner_no_fit(tmp_path, reason, reported_usage):
    """Explicit contextWindow=32768; stock admits or declines the oversized prompt."""
    worker = _WorkerClient(_DEFAULT_DIST, tmp_path / "no-fit", context_window=32_768)
    try:
        sizing_script = """
import { pathToFileURL } from "node:url";
const dist = process.env.OPENCLAW_DIST;
const { f: budget, rt: SettingsManager } = await import(pathToFileURL(dist + "/resource-loader-Bu_pVD2t.mjs"));
const { r: applySettings } = await import(pathToFileURL(dist + "/agent-settings-DcI_VuTd.mjs"));
const data = JSON.parse(process.argv[1]);
const settings = SettingsManager.inMemory({ compaction: data.settings });
applySettings({ settingsManager: settings, contextTokenBudget: data.window });
let low = 0, high = data.window * 4, size = 0;
while (low <= high) {
  const mid = Math.floor((low + high) / 2);
  const request = budget({
    contextWindow: data.window, reserveTokens: settings.getCompactionSettings().reserveTokens,
    systemPrompt: "S".repeat(mid), tools: data.tools.map((tool) => tool.function),
  });
  if (request.fixedTokens + request.pendingTokens < request.contextWindow - request.reserveTokens) {
    size = mid; low = mid + 1;
  } else high = mid - 1;
}
console.log(JSON.stringify(size));
"""
        sizing = subprocess.run(
            ["node", "--input-type=module", "-e", sizing_script, json.dumps({
                "settings": {"reserveTokens": 16384, "keepRecentTokens": 5},
                "window": worker.model_config["contextWindow"], "tools": worker.tool_schemas,
            })], capture_output=True, text=True,
            env={**os.environ, "OPENCLAW_DIST": str(_DEFAULT_DIST)}, check=True,
        )
        # Actual stock createCompactionRequestBudget picks the last positive
        # foreground history budget, avoiding its history-only fallback (8889-8892).
        system_size = json.loads(sizing.stdout)
        messages = [
            {"role": "system", "content": "S" * system_size},
            {"role": "user", "content": [{"type": "text", "text": "Completed task"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Done " * 200}], "stopReason": "stop"},
        ]
        settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
        usage = {"input": 30000, "output": 20, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 30020} if reported_usage else None
        stock = _run_stock_compaction_direct(
            messages, reason=reason, settings=settings, usage=usage, scripted_responses=[],
            tools=worker.tool_schemas, model_config=worker.model_config,
        )
        assert stock["status"] == ("failed" if reported_usage else "not_triggered")
        assert stock["captured_requests"] == []
        assert stock["retry"] is False
        if reported_usage:
            assert "No complete recent message fits" in stock["outcome"]["reason"]
        original = json.loads(json.dumps(messages))
        result, phases, exchanges = _compact_in_production_phases(
            worker, messages, reason=reason, settings=settings, usage=usage, answers=[],
        )
        assert result is None
        assert [operation for operation, _payload in phases] == ["prepare_compaction"]
        assert exchanges == []
        assert messages == original
        if reason == "overflow":
            # Repeat the admitted overflow through the same worker. The source
            # counts failed recovery passes too and declines the fourth before
            # making any provider request (resource-loader:10094-10109).
            for attempts in (1, 2, 3):
                stock = _run_stock_compaction_direct(
                    messages, reason=reason, settings=settings, usage=usage, scripted_responses=[],
                    tools=worker.tool_schemas, model_config=worker.model_config,
                    overflow_recovery_attempts=attempts,
                )
                actual = worker.request({"phase": "prepare_compaction", **phases[0][1]})
                assert actual["kind"] == "compaction_unavailable"
                assert actual["reason"] == stock["outcome"]["reason"]
                assert stock["retry"] is False
                assert stock["captured_requests"] == []
            assert "after 3 compact-and-retry attempts" in actual["reason"]
    finally:
        worker.close()

