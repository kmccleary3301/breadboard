"""Strict differential gates, including declared-deviation negative guards."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.compaction_lanes import openclaw_lane as lane


def record(body):
    raw = lane.canonical(body)
    return {"path": "/v1/chat/completions", "json": body, "raw_body_bytes": raw.hex()}


def summary_pair():
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    from breadboard.rl.harness.runners.openclaw_semantics import OPENCLAW_CONSUMER_ID
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    opening = record({"messages": [{"role": "system", "content": "episode"}], "tools": tools})
    stock = {"model": "model-a", "messages": [
        {"role": "system", "content": NATIVE_STREAM_PROFILES[OPENCLAW_CONSUMER_ID].compaction_summary_system_prompt},
        {"role": "user", "content": "Summarize the preceding conversation."}], "max_completion_tokens": 2048}
    bb = deepcopy(stock)
    bb["tools"] = tools
    return opening, stock, bb


@pytest.mark.parametrize("field,side,value", [
    ("tools", "stock", []), ("tools", "bb", []),
    ("max_completion_tokens", "stock", 99), ("max_completion_tokens", "bb", 99),
])
def test_deviation_value_guard(field, side, value):
    opening, stock, bb = summary_pair()
    (stock if side == "stock" else bb)[field] = value
    with pytest.raises(ValueError, match="recorded deviation"):
        lane.compare([opening, record(stock)], [opening, record(bb)])


def test_only_admitted_summary_fields_are_removed():
    opening, stock, bb = summary_pair()
    result = lane.compare([opening, record(stock)], [opening, record(bb)])
    assert result["diffs"] == []
    bb["tool_choice"] = "auto"
    assert len(lane.compare([opening, record(stock)], [opening, record(bb)])["diffs"]) == 1


def clamped_summary_pair():
    opening, stock, bb = summary_pair()
    # ceil(44678 * 1.25 / 4) = 13962; 16000 - 13962 - 1 = 2037.
    text = "x" * (44678 - len(stock["messages"][0]["content"]))
    stock["messages"][1]["content"] = bb["messages"][1]["content"] = text
    stock["max_completion_tokens"] = 2037
    return opening, stock, bb


def test_proxy_clamped_2037_stock_cap_is_admitted():
    opening, stock, bb = clamped_summary_pair()
    assert lane.compare([opening, record(stock)], [opening, record(bb)])["diffs"] == []


@pytest.mark.parametrize("side,value", [("stock", 2038), ("bb", 2037)])
def test_proxy_clamped_cap_guard_rejects_wrong_values(side, value):
    opening, stock, bb = clamped_summary_pair()
    (stock if side == "stock" else bb)["max_completion_tokens"] = value
    with pytest.raises(ValueError, match="recorded deviation"):
        lane.compare([opening, record(stock)], [opening, record(bb)])


@pytest.mark.skipif(not os.environ.get("OPENCLAW_DIST"), reason="pinned stock transport required")
@pytest.mark.parametrize("suffix", ["", "漢𠀀😀"])
def test_proxy_cap_formula_matches_stock_transport(suffix):
    _, body, _ = clamped_summary_pair()
    body["messages"][1]["content"] += suffix
    code = """
import { pathToFileURL } from "node:url";
const { t: build } = await import(pathToFileURL(process.env.OPENCLAW_DIST + "/openai-transport-stream-D950WgL3.mjs"));
const body = JSON.parse(process.argv[1]);
const wire = build({id:"model-a",name:"fixture",api:"openai-completions",provider:"openai",
  baseUrl:"http://127.0.0.1:1/v1",input:["text"],contextWindow:16000,maxTokens:2048},
  {systemPrompt:body.messages[0].content,messages:body.messages.slice(1)}, {maxTokens:3200});
console.log(JSON.stringify(wire.max_completion_tokens));
"""
    proc = subprocess.run(["node", "--input-type=module", "-e", code, json.dumps(body)],
                          check=True, capture_output=True, text=True)
    assert lane.stock_summary_cap(body) == json.loads(proc.stdout)


def test_string_whitespace_and_array_order_remain_strict():
    assert lane.canonical({"a": "a b"}) != lane.canonical({"a": "a  b"})
    assert lane.canonical({"a": [1, 2]}) != lane.canonical({"a": [2, 1]})
    assert lane.canonical({"a": 1, "b": 2}) == lane.canonical({"b": 2, "a": 1})


@pytest.mark.skipif(not os.environ.get("OPENCLAW_DIST"), reason="pinned stock formatter required")
def test_one_millisecond_timestamp_difference_is_not_masked():
    # Stock renders minutes, so probe adjacent milliseconds spanning its boundary.
    assert lane.EPISODE_TIMESTAMP_MS % 60000 == 59999
    code = """
import { normalizeMessagesForLlmBoundary } from "openclaw:pinned-attempt-prompt";
const timestamps = JSON.parse(process.argv[1]);
console.log(JSON.stringify(timestamps.map(timestamp =>
  normalizeMessagesForLlmBoundary([{role:"user",content:"clock probe",timestamp}],
    {timezone:"UTC",includeTimestamp:true})[0].content)));
"""
    proc = subprocess.run(["node", "--import", str(lane.ROOT / "breadboard/rl/harness/openclaw_classifier_loader.mjs"),
                           "--input-type=module", "-e", code,
                           json.dumps([lane.EPISODE_TIMESTAMP_MS, lane.EPISODE_TIMESTAMP_MS + 1])],
                          check=True, capture_output=True, text=True)
    left, right = json.loads(proc.stdout)
    opening, _, _ = summary_pair()
    result = lane.compare([opening, record({"messages": [{"role": "user", "content": left}]})],
                          [opening, record({"messages": [{"role": "user", "content": right}]})])
    assert len(result["diffs"]) == 1


@pytest.fixture(scope="module", params=lane.SCENARIOS)
def report(tmp_path_factory, request):
    if not os.environ.get("OPENCLAW_DIST"):
        pytest.skip("OPENCLAW_DIST is required for pinned stock execution")
    out = Path(os.environ.get("BB_OPENCLAW_LANE_REPORT_DIR", str(tmp_path_factory.mktemp("openclaw-lane"))))
    return lane.run_lane(out / request.param, (request.param,))


def test_real_stock_and_production_request_parity(report):
    scenario, result = next(iter(report["lane_results"].items()))
    assert "execution_error" not in result["stock_result"]
    assert result["stock_requests"] > 0
    assert result["bb_requests"] > 0
    assert result["diffs"] == []


def test_compaction_lifecycle_and_recovery_bound(report):
    scenario, result = next(iter(report["lane_results"].items()))
    assert result["stock_compactions"] == result["bb_compactions"]
    assert result["bb_overflow_attempts"] == len(result["stock_overflow_attempts"])
    if scenario == "threshold":
        assert result["stock_compactions"] == 1
        assert result["stock_requests"] - result["stock_summary_requests"] == 4
    elif scenario in {"split_turn", "length_stop"}:
        assert result["stock_compactions"] >= 2
        assert result["stock_turn_prefix_requests"] > 0
        assert result["bb_turn_prefix_requests"] > 0
    else:
        assert result["stock_compactions"] == result["bb_compactions"] == 3
        assert result["stock_overflow_attempts"] == [1, 2, 3]
        assert result["stock_overflow_exhausted"] is True
        assert result["bb_overflow_attempts"] == 3
        assert result["bb_overflow_bound_reached"] is True


def test_main_and_summary_tool_choice_are_source_owned(report):
    for scenario in report["lane_results"]:
        folder = Path(report["artifact_root"]) / scenario
        for side in ("stock", "bb"):
            requests = [json.loads(line) for line in (folder / f"{side}_requests.jsonl").read_text().splitlines()]
            for request in requests:
                body = request["json"]
                if request["path"] != "/v1/chat/completions":
                    continue
                if lane.is_summary(body):
                    assert "tool_choice" not in body
                else:
                    assert body["tool_choice"] == "auto"


def test_replay_trace_records_stock_transport_cap(report):
    scenario, result = next(iter(report["lane_results"].items()))
    folder = Path(report["artifact_root"]) / scenario
    stock = [json.loads(line)["json"] for line in (folder / "stock_requests.jsonl").read_text().splitlines()]
    wire_caps = [body["max_completion_tokens"] for body in stock if lane.is_summary(body)]
    trace_caps = [
        request["max_tokens"]
        for phase in result["bb_result"]["phases"]
        if phase["result"]["kind"] == "compaction_prepared"
        for request in (phase["result"]["summary_request"], phase["result"]["turn_prefix_request"])
        if request is not None
    ]
    assert trace_caps == wire_caps


def test_primary_and_summary_payload_parity(report):
    # This isolates compaction defects; the full-request gate above still includes
    # embeddings and fails on any unmatched auxiliary request.
    scenario = next(iter(report["lane_results"]))
    folder = Path(report["artifact_root"]) / scenario
    requests = {
        side: [json.loads(line) for line in (folder / f"{side}_requests.jsonl").read_text().splitlines()]
        for side in ("stock", "bb")
    }
    chat = {side: [request for request in values if request["path"] == "/v1/chat/completions"]
            for side, values in requests.items()}
    assert lane.compare(chat["stock"], chat["bb"])["diffs"] == []


def test_caller_continuation_is_source_exact_and_not_persisted(report):
    scenario, result = next(iter(report["lane_results"].items()))
    assert result["clock_timestamps"]["stock"] == result["clock_timestamps"]["bb"]
    assert result["clock_timestamps"]["stock"][0] == lane.EPISODE_TIMESTAMP_MS
    folder = Path(report["artifact_root"]) / scenario
    marker = "Continue the current task from the existing transcript"
    guards = {}
    for side in ("stock", "bb"):
        guards[side] = []
        for line in (folder / f"{side}_requests.jsonl").read_text().splitlines():
            body = json.loads(line)["json"]
            if lane.is_summary(body):
                assert marker not in json.dumps(body)
            else:
                guards[side].extend(message["content"].encode("utf-8") for message in body.get("messages", [])
                                    if message["role"] == "user" and isinstance(message["content"], str)
                                    and marker in message["content"])
    assert guards["stock"] == guards["bb"]
    if scenario == "threshold":
        assert guards["stock"] == []
    else:
        assert guards["stock"]
    for phase in result["bb_result"]["phases"]:
        if phase["result"]["kind"] == "compaction_finalized":
            assert marker not in json.dumps(phase["result"]["messages"])
