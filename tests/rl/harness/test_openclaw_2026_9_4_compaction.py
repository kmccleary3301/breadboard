from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import pytest

from tests.rl.harness.openclaw_2026_9_4_test_support import (
    _DEFAULT_DIST,
    _WorkerClient,
    _make_conversation_history,
    _run_stock_compaction_direct,
    stock_runtime,
    worker,
)

pytestmark = pytest.mark.usefixtures("stock_runtime")

def test_openclaw_compaction_worker_uninitialized(tmp_path: Path):
    client = _WorkerClient(_DEFAULT_DIST, workspace=tmp_path / "uninit_workspace", initialize=False)
    try:
        with pytest.raises(RuntimeError, match="worker is not initialized with model_config"):
            client.request({
                "phase": "prepare_compaction",
                "reason": "overflow",
                "checkpoint": "overflow",
                "messages": _make_conversation_history(2),
                "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
            })
    finally:
        client.close()


def test_openclaw_worker_prepare_and_finalize_overflow_compaction(worker):
    messages = _make_conversation_history(5)
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })

    assert prepared["kind"] == "compaction_prepared"
    assert prepared["schema_version"] == "bb.openclaw-native.v1"
    summary_req = prepared["summary_request"]
    assert len(summary_req["messages"]) == 2
    assert summary_req["messages"][0]["role"] == "system"
    assert "You are a context summarization assistant." in summary_req["messages"][0]["content"]
    assert summary_req["messages"][1]["role"] == "user"
    user_text = summary_req["messages"][1]["content"][0]["text"]
    assert "<conversation>" in user_text
    assert "## Goal" in user_text

    prep = prepared["preparation"]
    assert "tokensBefore" in prep
    assert prep["firstKeptIndex"] > 0

    turn_prefix_summary = "Prefix work summary" if prep.get("isSplitTurn") else None

    # Finalize compaction
    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": "## Goal\nInspect files\n\n## Decisions\nInspected files 0 to 2",
        "turn_prefix_summary": turn_prefix_summary,
        "preparation": prep,
    })

    assert finalized["kind"] == "compaction_finalized"
    assert finalized["first_kept_index"] == prep["firstKeptIndex"]
    assert finalized["compaction_message"]["role"] == "compactionSummary"
    assert "## Decisions" in finalized["compaction_message"]["summary"]
    assert finalized["compaction_message"]["timestamp"] == int(worker.initialized["bootstrap"]["message_timestamp_ms"]) + 1

    # Verify project_request converts the stock compactionSummary message
    retained = [
        {"role": "system", "content": "You are an agent"},
        finalized["compaction_message"],
        *messages[finalized["first_kept_index"]:],
    ]
    projected = worker.request({"phase": "project_request", "messages": retained})
    wire_user_content = projected["messages"][1]["content"][0]["text"]
    assert "The conversation history before this point was compacted" in wire_user_content
    assert "<summary>" in wire_user_content
    assert "## Decisions" in wire_user_content


def test_openclaw_worker_threshold_not_triggered(worker):
    messages = _make_conversation_history(2)
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "threshold",
        "checkpoint": "agent_end",
        "messages": messages,
        "usage": {
            "input": 5000,
            "output": 200,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 5200,
        },
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 20000},
    })

    assert prepared["kind"] == "compaction_unavailable"
    assert prepared["reason"] == "not_triggered"


def test_openclaw_worker_threshold_compaction_fires(worker):
    messages = _make_conversation_history(6)
    # 131072 window, reserve is floored at 20000 (capped at 32768).
    # Threshold is 131072 - 20000 = 111072 tokens.
    # Usage of 115000 exceeds threshold -> must fire!
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "threshold",
        "checkpoint": "agent_end",
        "messages": messages,
        "usage": {
            "input": 114000,
            "output": 1000,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 115000,
        },
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })

    assert prepared["kind"] == "compaction_prepared"
    assert prepared["preparation"]["tokensBefore"] == 115008


def test_openclaw_worker_split_turn_compaction(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Start task"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Working"}, {"type": "toolCall", "id": "c1", "name": "exec", "arguments": {"command": "ls"}}]},
        {"role": "toolResult", "toolCallId": "c1", "toolName": "exec", "isError": False, "content": [{"type": "text", "text": "log\n" * 500}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Continuing"}, {"type": "toolCall", "id": "c2", "name": "read", "arguments": {"path": "a.txt"}}]},
        {"role": "toolResult", "toolCallId": "c2", "toolName": "read", "isError": False, "content": [{"type": "text", "text": "content\n" * 500}]},
    ]
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })

    assert prepared["kind"] == "compaction_prepared"
    prep = prepared["preparation"]
    if prep.get("isSplitTurn"):
        assert prepared.get("turn_prefix_request") is not None
        prefix_req = prepared["turn_prefix_request"]
        prefix_text = prefix_req["messages"][1]["content"][0]["text"]
        assert "This is the PREFIX of a turn that was too large to keep" in prefix_text

        finalized = worker.request({
            "phase": "finalize_compaction",
            "summary": "Prior turn summary",
            "turn_prefix_summary": "Prefix work done",
            "preparation": prep,
        })
        assert finalized["kind"] == "compaction_finalized"
        assert finalized["compaction_message"]["role"] == "compactionSummary"
        assert "Turn Context (split turn):" in finalized["compaction_message"]["summary"]
        assert "Prefix work done" in finalized["compaction_message"]["summary"]


def test_openclaw_worker_parity_against_stock_native_work(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "step 0"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 0"}]},
        {"role": "user", "content": [{"type": "text", "text": "step 1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 3}
    direct = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=["Prior work", "Current turn"], tools=worker.tool_schemas,
    )
    worker_prep = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages, "settings": settings,
    })
    assert worker_prep["kind"] == "compaction_prepared"
    assert worker_prep["preparation"]["firstKeptIndex"] == direct["first_kept_index"]


def test_stock_equality_basic_cut(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Task turn 0: inspect files"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Inspected files."}]},
        {"role": "user", "content": [{"type": "text", "text": "Task turn 1: edit files"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Edited files."}]},
        {"role": "user", "content": [{"type": "text", "text": "Task turn 2: run tests"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    scripted = ["## Goal\nComplete tasks\n\n## Progress\n### Done\n- [x] Inspected and edited"]
    usage = {"input": 115000, "output": 1000, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 116000}

    stock = _run_stock_compaction_direct(
        messages, reason="threshold", settings=settings, usage=usage,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert len(stock["captured_requests"]) == 1

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "threshold",
        "checkpoint": "agent_end",
        "messages": messages,
        "usage": usage,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"
    worker_requests = [prepared["summary_request"]]
    if prepared.get("turn_prefix_request"):
        worker_requests.append(prepared["turn_prefix_request"])

    # Wire request sequence equality
    assert len(worker_requests) == len(stock["captured_requests"])
    for w_req, s_req in zip(worker_requests, stock["captured_requests"]):
        assert w_req["messages"] == s_req["messages"]
        assert w_req["max_tokens"] == s_req["max_tokens"]

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["first_kept_index"] == stock["first_kept_index"]
    assert finalized["summary"] == stock["summary"]
    assert finalized["compaction_message"] == stock["compaction_message"]
    assert "continuation" not in finalized


def test_stock_equality_split_turn(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Initial request"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Initial reply"}]},
        {"role": "user", "content": [{"type": "text", "text": "Split request"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Large turn running"},
            {"type": "toolCall", "id": "tc1", "name": "exec", "arguments": {"command": "cat big.log"}},
        ]},
        {"role": "toolResult", "toolCallId": "tc1", "toolName": "exec", "isError": False, "content": [{"type": "text", "text": "line\n" * 400}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Second large call"},
            {"type": "toolCall", "id": "tc2", "name": "read", "arguments": {"path": "big.txt"}},
        ]},
        {"role": "toolResult", "toolCallId": "tc2", "toolName": "read", "isError": False, "content": [{"type": "text", "text": "data\n" * 400}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 50}
    scripted = [
        "## Goal\nInitial tasks\n\n## Progress\n- [x] Initial",
        "## Original Request\nSplit request\n\n## Early Progress\n- Executed big.log",
    ]

    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert len(stock["captured_requests"]) == 2

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"
    assert prepared["summary_request"] is not None
    assert prepared["turn_prefix_request"] is not None
    worker_requests = [prepared["summary_request"], prepared["turn_prefix_request"]]

    assert len(worker_requests) == len(stock["captured_requests"])
    for w_req, s_req in zip(worker_requests, stock["captured_requests"]):
        assert w_req["messages"] == s_req["messages"]
        assert w_req["max_tokens"] == s_req["max_tokens"]

    # No exception is needed to suspend at stock's next provider await.
    followup = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert followup["kind"] == "compaction_followup_request"
    assert followup["request"] == stock["captured_requests"][1]

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "turn_prefix_summary": scripted[1],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["first_kept_index"] == stock["first_kept_index"]
    assert finalized["summary"] == stock["summary"]
    assert finalized["compaction_message"] == stock["compaction_message"]


def test_stock_equality_update_previous_summary(worker):
    messages = [
        {
            "role": "compactionSummary",
            "summary": "## Goal\nOriginal goal\n\n## Progress\n- [x] Prior work",
            "tokensBefore": 100,
            "timestamp": 0,
        },
        {"role": "user", "content": [{"type": "text", "text": "New turn: add feature X"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Feature X added"}]},
        {"role": "user", "content": [{"type": "text", "text": "New turn: add feature Y"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    scripted = ["## Goal\nOriginal goal plus X\n\n## Progress\n- [x] Prior work\n- [x] Added feature X"]

    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert len(stock["captured_requests"]) == 1
    stock_user_text = stock["captured_requests"][0]["messages"][1]["content"][0]["text"]
    assert "<previous-summary>" in stock_user_text

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"
    worker_user_text = prepared["summary_request"]["messages"][1]["content"][0]["text"]
    assert "<previous-summary>" in worker_user_text
    assert worker_user_text == stock_user_text

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["summary"] == stock["summary"]
    assert finalized["first_kept_index"] == stock["first_kept_index"]
    assert finalized["compaction_message"] == stock["compaction_message"]


def test_prior_compaction_missing_timestamp_is_not_replaced_with_epoch(worker):
    # session-DVbOtm8K.mjs:49-55,147 requires the persisted timestamp.
    with pytest.raises(RuntimeError, match="compaction summary timestamp"):
        worker.request({
            "phase": "prepare_compaction",
            "reason": "overflow",
            "checkpoint": "overflow",
            "messages": [
                {"role": "compactionSummary", "summary": "Prior work", "tokensBefore": 100},
                *_make_conversation_history(3),
            ],
            "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
        })


def test_prior_compaction_missing_tokens_before_is_preserved(worker):
    prepared = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": [
            {"role": "compactionSummary", "summary": "Prior work", "timestamp": 0},
            *_make_conversation_history(3),
        ],
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })
    assert "tokensBefore" not in prepared["preparation"]["entries"][0]


def test_stock_equality_overflow(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "task 0"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "result 0"}]},
        {"role": "user", "content": [{"type": "text", "text": "unresolved request 1"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    scripted = ["## Goal\nTask 0 complete"]

    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert "## Latest unresolved user request" in stock["summary"]

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["summary"] == stock["summary"]
    assert "## Latest unresolved user request" in finalized["summary"]
    assert finalized["compaction_message"] == stock["compaction_message"]


def test_stock_safeguard_mode_check():
    # Confirm RL config effective compaction mode is "default", not "safeguard"
    node_script = """
import { pathToFileURL } from "node:url";
const dist = process.env.OPENCLAW_DIST;
const { a: resolveEffectiveCompactionMode } = await import(pathToFileURL(dist + "/agent-settings-DcI_VuTd.mjs"));

// RL configuration has compaction: false and no provider / mode: safeguard
const modeEmpty = resolveEffectiveCompactionMode({});
const modeFalse = resolveEffectiveCompactionMode({ agents: { defaults: { compaction: false } } });
const modeSafeguard = resolveEffectiveCompactionMode({ agents: { defaults: { compaction: { mode: "safeguard" } } } });
console.log(JSON.stringify({ modeEmpty, modeFalse, modeSafeguard }));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=str(_DEFAULT_DIST),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "OPENCLAW_DIST": str(_DEFAULT_DIST)},
    )
    data = json.loads(proc.stdout.strip())
    assert data["modeEmpty"] == "default"
    assert data["modeFalse"] == "default"
    assert data["modeSafeguard"] == "safeguard"


def test_stock_session_context_and_worker_lifecycle_parity(worker):
    messages = [
        {"role": "system", "content": "You are an agent"},
        {"role": "user", "content": [{"type": "text", "text": "step 0"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 0"}]},
        {"role": "user", "content": [{"type": "text", "text": "step 1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 3}
    summary = "## Goal\nComplete steps\n\n## Progress\n- Step 0 completed"
    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings, scripted_responses=[summary, "Current turn"],
        tools=worker.tool_schemas,
    )
    prepared = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages, "settings": settings,
    })
    assert prepared["summary_request"] == stock["captured_requests"][0]
    finalized = worker.request({
        "phase": "finalize_compaction", "summary": summary, "turn_prefix_summary": "Current turn",
        "preparation": prepared["preparation"],
    })
    assert finalized["messages"] == stock["messages"]
    projected = worker.request({"phase": "project_request", "messages": finalized["messages"]})
    assert projected["messages"][:2] == stock["wire"]["messages"][:2]
    messages2 = [
        *finalized["messages"],
        {"role": "user", "content": [{"type": "text", "text": "step 2"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 2"}]},
    ]
    settings2 = {"reserveTokens": 16384, "keepRecentTokens": 2}
    stock2 = _run_stock_compaction_direct(
        messages2, reason="overflow", settings=settings2, scripted_responses=["Updated work", "Current turn"],
        tools=worker.tool_schemas, source_entries=stock["source_entries"],
        source_messages=stock["messages"],
    )
    prepared2 = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages2, "settings": settings2,
    })
    assert prepared2["summary_request"] == stock2["captured_requests"][0]
    assert "<previous-summary>" in prepared2["summary_request"]["messages"][1]["content"][0]["text"]

