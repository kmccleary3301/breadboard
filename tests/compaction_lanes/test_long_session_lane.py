from __future__ import annotations

import json
from pathlib import Path
import os
import pytest

from breadboard.rl.harness.native_stream_profiles import PI_SUMMARIZATION_SYSTEM_PROMPT
from scripts.compaction_lanes.long_session_lane import (
    RpcSettleState,
    get_target_overlay_argv,
    build_long_session_scenario,
    build_repeated_failure_scenario,
    compare_recorded_requests,
    get_target_deviations,
    is_summary_request,
    normalize_request_body,
    resolve_pi_cli,
    run_pi_0_73_1_lane,
)


def test_scenario_script_structure():
    """Verify scripted scenario contains all required compaction test phases."""
    scenario = build_long_session_scenario(context_window=16000)
    assert len(scenario) >= 8

    # 1. Contains tool call
    has_tool_call = any(item.get("type") in ("tool_call", "response") for item in scenario)
    assert has_tool_call

    # 2. Contains context overflow error
    has_overflow = any(
        item.get("type") == "error" and item.get("error") == "context_length_exceeded"
        for item in scenario
    )
    assert has_overflow

    # 3. Repeated failure scenario
    repeated = build_repeated_failure_scenario(context_window=16000)
    assert len(repeated) == 3
    assert repeated[0]["type"] == "error"
    assert repeated[2]["type"] == "error"


def test_normalizer_removes_strictly_admitted_deviations():
    """Normalizer removes only tools and max_tokens on summary requests and nothing else."""
    summary_req = {
        "model": "mock-model",
        "messages": [
            {
                "role": "system",
                "content": PI_SUMMARIZATION_SYSTEM_PROMPT,
            },
            {"role": "user", "content": "Summarize."},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
        "max_tokens": 2048,
        "temperature": 0.0,
    }

    assert is_summary_request(summary_req)

    # 1. Without deviations: tools and max_tokens remain
    norm_none = normalize_request_body(summary_req, allowed_deviations=[])
    assert "tools" in norm_none
    assert "max_tokens" in norm_none

    # 2. With both deviations: strictly tools and max_tokens are removed
    norm_both = normalize_request_body(
        summary_req,
        allowed_deviations=["compaction_summary_episode_tools", "compaction_summary_max_tokens"],
        episode_tools=summary_req["tools"], episode_max_tokens=2048,
    )
    assert "tools" not in norm_both
    assert "max_tokens" not in norm_both
    assert norm_both["model"] == "mock-model"
    assert norm_both["temperature"] == 0.0
    assert len(norm_both["messages"]) == 2

    # 3. On non-summary request: tools and max_tokens are NOT removed
    agent_req = {
        "model": "mock-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
        "max_tokens": 2048,
    }
    assert not is_summary_request(agent_req)
    norm_agent = normalize_request_body(
        agent_req,
        allowed_deviations=["compaction_summary_episode_tools", "compaction_summary_max_tokens"],
    )
    assert "tools" in norm_agent
    assert "max_tokens" in norm_agent


def test_comparison_detects_unadmitted_differences():
    """Comparison passes when diff is admitted deviation and fails on real diffs."""
    stock_summary = {
        "json": {
            "model": "mock-model",
            "messages": [
                {"role": "system", "content": PI_SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": "Conversation history."},
            ],
            # Stock Pi has no tools and summary max_tokens
            "max_tokens": 800,
        }
    }
    bb_summary = {
        "json": {
            "model": "mock-model",
            "messages": [
                {"role": "system", "content": PI_SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": "Conversation history."},
            ],
            # Breadboard sends episode tools and episode max_tokens
            "tools": [{"type": "function", "function": {"name": "bash"}}],
            "max_tokens": 2048,
        }
    }

    # Pass with admitted deviations
    passed, diffs = compare_recorded_requests(
        [stock_summary],
        [bb_summary],
        allowed_deviations=["compaction_summary_episode_tools", "compaction_summary_max_tokens"],
        episode_tools=bb_summary["json"]["tools"], episode_max_tokens=2048, reserve_tokens=1000,
    )
    assert passed
    assert len(diffs) == 0

    # Fail if an unadmitted field differs (e.g. prompt text)
    bb_diverged = {
        "json": {
            "model": "mock-model",
            "messages": [
                {"role": "system", "content": PI_SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": "Diverged conversation history."},
            ],
            "tools": [{"type": "function", "function": {"name": "bash"}}],
            "max_tokens": 2048,
        }
    }
    passed_diverged, diffs_diverged = compare_recorded_requests(
        [stock_summary],
        [bb_diverged],
        allowed_deviations=["compaction_summary_episode_tools", "compaction_summary_max_tokens"],
        episode_tools=bb_summary["json"]["tools"], episode_max_tokens=2048, reserve_tokens=1000,
    )
    assert not passed_diverged
    assert len(diffs_diverged) == 1
    assert diffs_diverged[0]["type"] == "body_diff"


@pytest.mark.parametrize("side,field,value", [
    ("bb", "tools", [{"type": "function", "function": {"name": "altered"}}]),
    ("bb", "max_tokens", 1),
    ("stock", "max_tokens", 799),
    ("stock", "tools", []),
])
def test_comparison_rejects_wrong_deviation_values(side, field, value):
    from copy import deepcopy
    tools = [{"type": "function", "function": {"name": "bash"}}]
    messages = [
        {"role": "system", "content": PI_SUMMARIZATION_SYSTEM_PROMPT},
        {"role": "user", "content": "Conversation history."},
    ]
    stock = {"messages": messages, "max_tokens": 800}
    bb = {"messages": messages, "tools": tools, "max_tokens": 2048}
    stock, bb = deepcopy(stock), deepcopy(bb)
    (bb if side == "bb" else stock)[field] = value
    passed, diffs = compare_recorded_requests(
        [{"json": stock}], [{"json": bb}],
        get_target_deviations("pi-r3@0.73.1"),
        episode_tools=tools, episode_max_tokens=2048, reserve_tokens=1000,
    )
    assert not passed
    assert diffs[0]["type"] == "deviation_value_diff"


def test_target_deviations_fails_loudly_without_fallbacks():
    """get_target_deviations raises FileNotFoundError for nonexistent targets without fallback."""
    with pytest.raises(FileNotFoundError):
        get_target_deviations("nonexistent-target@9.9.9")


@pytest.mark.parametrize("target_id", ["pi-r3@0.73.1", "pi-r4@0.57.1"])
@pytest.mark.parametrize(
    "scenario,requests,compactions",
    [
        ("overflow_recovery", 5, 1),
        ("long_session", 9, 2),
        ("repeated_failure", 3, 1),
        ("final_over_threshold", 2, 1),
    ],
)
def test_pi_rpc_lane_e2e(tmp_path: Path, target_id: str, scenario: str, requests: int, compactions: int):
    version = target_id.split("@")[1]
    env_var = "PI057_CODING_AGENT_NODE_MODULES" if version == "0.57.1" else "PI_CODING_AGENT_NODE_MODULES"
    if not os.environ.get(env_var):
        pytest.skip(f"{env_var} is required for real-stock tests")
    report = run_pi_0_73_1_lane(tmp_path, target_id=target_id, scenario=scenario, runner="rpc")
    assert (tmp_path / "lane_report.json").is_file()
    assert report["harness"] == f"pi@{version}"
    assert report["scenario"] == scenario
    assert report["stock_request_count"] == requests
    assert report["bb_request_count"] == requests
    if version == "0.73.1" and scenario in {"long_session", "repeated_failure"}:
        assert report["bb_terminal_error"] is not None
        assert report["bb_replay_trace_request_count"] is None
    else:
        assert report["bb_replay_trace_request_count"] == requests
    assert report["stock_compaction_count"] == compactions
    assert report["bb_compaction_count"] == compactions
    assert report["diffs"] == []
    assert report["passed"] is True
    if scenario == "long_session":
        stock = [json.loads(line) for line in (tmp_path / "stock_requests.jsonl").read_text().splitlines()]
        assert any("PREFIX of a turn" in json.dumps(row["json"]) for row in stock)


@pytest.mark.parametrize("version,env_var", [
    ("0.73.1", "PI_CODING_AGENT_NODE_MODULES"),
    ("0.57.1", "PI057_CODING_AGENT_NODE_MODULES"),
])
def test_pi_package_root_is_required(monkeypatch, version, env_var):
    monkeypatch.delenv(env_var, raising=False)
    with pytest.raises(ValueError, match=env_var):
        resolve_pi_cli(version)


@pytest.mark.parametrize("document", [
    {}, {"overlay": {}}, {"overlay": {"argv": []}},
    {"overlay": {"argv": "not an argv"}}, {"overlay": {"argv": [None]}},
])
def test_invalid_overlay_argv_raises(monkeypatch, tmp_path, document):
    import scripts.compaction_lanes.long_session_lane as lane
    harness = tmp_path / "harness.yaml"
    harness.write_text("")
    harness.with_name("target.json").write_text(json.dumps(document))
    monkeypatch.setattr(lane, "resolve_target_harness_path", lambda _: harness)
    with pytest.raises((KeyError, ValueError)):
        get_target_overlay_argv("pi-r4@0.57.1")


def test_invalid_native_config_raises(monkeypatch, tmp_path):
    import scripts.compaction_lanes.long_session_lane as lane
    harness = tmp_path / "harness.yaml"
    harness.write_text("")
    harness.with_name("native-config.json").write_text("not json")
    monkeypatch.setattr(lane, "resolve_target_harness_path", lambda _: harness)
    with pytest.raises(json.JSONDecodeError):
        normalize_request_body({"messages": []}, [])


@pytest.mark.parametrize("version,start,end", [
    ("0.73.1", "compaction_start", "compaction_end"),
    ("0.57.1", "auto_compaction_start", "auto_compaction_end"),
])
def test_rpc_settle_waits_for_compaction_and_retry(version, start, end):
    state = RpcSettleState(version)
    assert not state.observe({"type": "agent_end"})
    assert not state.observe({"type": start})
    assert not state.observe({
        "type": "response", "id": state.barrier_id, "success": True,
        "data": {"isStreaming": False, "isCompacting": True},
    })
    assert not state.observe({"type": end, "willRetry": True})
    assert not state.observe({
        "type": "response", "id": state.barrier_id, "success": True,
        "data": {"isStreaming": False, "isCompacting": False},
    })
    assert not state.observe({"type": "agent_start"})
    assert not state.observe({"type": "agent_end"})
    assert state.observe({
        "type": "response", "id": state.barrier_id, "success": True,
        "data": {"isStreaming": False, "isCompacting": False},
    })


@pytest.mark.parametrize("version", ["0.73.1", "0.57.1"])
def test_rpc_settle_waits_for_final_threshold_compaction(version):
    state = RpcSettleState(version)
    assert not state.observe({"type": "agent_end"})
    assert not state.observe({"type": state.start})
    for _ in range(20):
        assert not state.observe({"type": "message_update"})
    assert state.observe({"type": state.end, "willRetry": False})


def test_rpc_reader_errors_reach_main_thread(tmp_path):
    from scripts.compaction_lanes.long_session_lane import run_stock_pi_rpc
    cli = tmp_path / "malformed-rpc.js"
    cli.write_text('process.stdin.on("data", () => console.log("not json"));')
    with pytest.raises(json.JSONDecodeError):
        run_stock_pi_rpc(cli, "http://127.0.0.1:1", tmp_path, "Hello", timeout=5)
