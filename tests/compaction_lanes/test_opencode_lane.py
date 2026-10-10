"""The OpenCode lane must expose differences, never turn them into deviations."""
from __future__ import annotations

import json
import os

import pytest

from scripts.compaction_lanes.opencode_lane import (
    PROFILES, SCENARIOS, TEST_WINDOW, canonical, compare_records,
    production_settings, responses_sse, run_scenario, scenario_turns,
)


def record(body, path="/v1/responses"):
    raw = json.dumps(body).encode()
    return {"json": body, "path": path, "raw_body": raw.decode(), "raw_body_bytes": raw.hex()}


def test_comparator_ignores_only_json_member_order():
    stock = record({"model": "m", "input": [{"role": "user", "content": "same"}]})
    bb = record({"input": [{"content": "same", "role": "user"}], "model": "m"})
    assert stock["raw_body_bytes"] != bb["raw_body_bytes"]
    assert compare_records([stock], [bb]) == []


@pytest.mark.parametrize("replacement", ["same ", "Same", "same\n"])
def test_string_whitespace_is_not_normalized(replacement):
    stock = record({"input": [{"role": "user", "content": "same"}]})
    bb = record({"input": [{"role": "user", "content": replacement}]})
    assert compare_records([stock], [bb])


def test_undeclared_fields_are_not_removed():
    stock = record({"model": "m", "store": False, "prompt_cache_key": "stock-session"})
    bb = record({"model": "m"})
    diff = compare_records([stock], [bb])[0]
    assert {field["field"] for field in diff["fields"]} == {"store", "prompt_cache_key"}
    assert all(field["classification"] == "pre-existing baseline" for field in diff["fields"])
    assert all(field["baseline_reference"]["matching_entry"] is None for field in diff["fields"])


def test_summary_tools_difference_is_a_defect_not_a_deviation():
    body = {"input": [{"role": "developer", "content": "You are a helpful AI assistant tasked with summarizing conversations."}]}
    diff = compare_records([record(body)], [record({**body, "tools": [{"name": "bash"}]})])[0]
    assert diff["fields"][0]["classification"] == "compaction-path"
    assert diff["fields"][0]["field"] == "tools"


def summary_record(messages):
    return record({"input": [{"role": "developer", "content": "You are a helpful AI assistant tasked with summarizing conversations."}, *messages]})


def test_missing_summary_history_is_classified_as_compaction_not_baseline():
    original = [{"role": "user", "content": "Original task"}, {"role": "assistant", "content": "Original answer"}]
    stock = summary_record(original)
    bb = summary_record([])
    diff = compare_records([stock], [bb])[0]
    assert diff["compaction_input_findings"]
    assert diff["fields"][0]["classification"] == "compaction-path"


@pytest.mark.parametrize("kind", ["function_call", "function_call_output", "developer"])
def test_summary_retained_tool_values_and_system_text_are_guarded(kind):
    from copy import deepcopy
    stock = summary_record([{"type": "function_call", "call_id": "call_1", "name": "bash", "arguments": "{}"},
                            {"type": "function_call_output", "call_id": "call_1", "output": "[Old tool result content cleared]"}])
    bb = deepcopy(stock)
    if kind == "developer":
        bb["json"]["input"][0]["content"] += " extra instruction"
    else:
        next(item for item in bb["json"]["input"] if item.get("type") == kind)["call_id"] = "wrong_call"
    bb = record(bb["json"])
    diff = compare_records([stock], [bb])[0]
    assert diff["compaction_input_findings"]
    assert diff["fields"][0]["classification"] == "compaction-path"


def test_baseline_terminal_rendering_is_classified_but_never_normalized():
    stock = summary_record([{"role": "assistant", "content": "Original answer"}])
    bb = summary_record([{"role": "assistant", "content": "Original answer\n\n>>>>>> END RESPONSE"}])
    diff = compare_records([stock], [bb])[0]
    assert not diff["compaction_input_findings"]
    assert diff["fields"][0]["classification"] == "pre-existing baseline"
    assert canonical(stock["json"]) != canonical(bb["json"])
    assert diff["stock_raw_sha256"] != diff["bb_raw_sha256"]


def test_arbitrary_assistant_suffix_is_not_a_baseline_rendering():
    stock = summary_record([{"role": "assistant", "content": "Original answer"}])
    bb = summary_record([{"role": "assistant", "content": "Original answer extra"}])
    assert compare_records([stock], [bb])[0]["fields"][0]["classification"] == "compaction-path"


def test_extra_summary_assistant_is_not_an_inherited_baseline():
    original = {"role": "assistant", "content": "Original answer"}
    stock = summary_record([original])
    bb = summary_record([original, {"role": "assistant", "content": "Extra answer"}])
    assert compare_records([stock], [bb])[0]["fields"][0]["classification"] == "compaction-path"


def test_summary_stream_or_output_cap_difference_is_a_compaction_defect():
    stock = record({**summary_record([])["json"], "stream": True, "max_output_tokens": 32000})
    bb = summary_record([])
    assert {field["classification"] for field in compare_records([stock], [bb])[0]["fields"]} == {"compaction-path"}


def test_request_count_difference_is_a_compaction_path_failure():
    assert compare_records([record({})], []) == [{"kind": "request_count", "classification": "compaction-path", "stock": 1, "bb": 0}]


def test_array_order_and_null_presence_remain_strict():
    assert canonical({"input": [1, 2]}) != canonical({"input": [2, 1]})
    assert canonical({"tools": None}) != canonical({})


@pytest.mark.parametrize("harness", PROFILES)
def test_scenario_limits_come_from_production_profile(harness):
    settings = production_settings(harness)
    assert settings["max_output_tokens"] == 32000
    assert settings["overrides"]["contextWindow"] == {"production": 400000, "lane": TEST_WINDOW}
    assert settings["overrides"]["max_input_tokens"] == {"production": 272000, "lane": TEST_WINDOW}
    turns = scenario_turns("auto_prune", settings)
    assert len(turns) == 4
    responses = turns[-1]["responses"]
    assert sum(response.get("usage", {}).get("total_tokens", 0) > 44000 for response in responses) == 2
    assert len(turns[0]["responses"][0]["tool_calls"]) == 12


def test_complete_sse_includes_sdk_starts_and_deltas():
    settings = production_settings("opencode")
    descriptor = scenario_turns("auto_prune", settings)[0]["responses"][0]
    response = responses_sse(descriptor, 1)
    chunks = "".join(response["chunks"])
    assert chunks.count("event: response.output_item.added\n") == 12
    assert chunks.count("event: response.function_call_arguments.delta\n") == 12
    assert chunks.count("event: response.output_item.done\n") == 12
    assert "event: response.completed\n" in chunks


def test_http_overflow_error_is_not_rewritten():
    descriptor = {"type": "error", "status_code": 400, "error": "context_length_exceeded", "message": "prompt is too long"}
    assert responses_sse(descriptor, 1) is descriptor


@pytest.mark.skipif(os.environ.get("BB_OPENCODE_LANE_INTEGRATION") != "1", reason="opt-in real pinned stock/production BB lane")
@pytest.mark.parametrize("harness", PROFILES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_pinned_production_lane_is_strict(harness, scenario, tmp_path):
    report = run_scenario(harness, scenario, tmp_path)
    assert report["deviations_used"] == []
    assert report["passed"], json.dumps(report, indent=2)


def test_user_prefix_is_baseline_only_when_recorded_before_compaction():
    original = {"role": "user", "content": "Original task"}
    prefixed = {"role": "user", "content": "Plugin prefix\nOriginal task"}
    stock = [record({"input": [prefixed]}), summary_record([prefixed])]
    bb = [record({"input": [original]}), summary_record([original])]
    diffs = compare_records(stock, bb)
    assert len(diffs) == 2
    assert not diffs[1]["compaction_input_findings"]
    assert diffs[1]["fields"][0]["classification"] == "pre-existing baseline"
    assert canonical(stock[1]["json"]) != canonical(bb[1]["json"])
    # A new summary-only change must not be attributed to the earlier baseline.
    bb[1] = summary_record([{"role": "user", "content": "Different task"}])
    assert compare_records(stock, bb)[1]["fields"][0]["classification"] == "compaction-path"


def test_tool_reminder_baseline_requires_exact_recorded_pre_summary_pair():
    original = {"type": "function_call_output", "call_id": "call_1", "output": "stdout"}
    reminder = {**original, "output": "stdout\n[Category+Skill Reminder]"}
    stock = [record({"input": [reminder]}), summary_record([reminder])]
    bb = [record({"input": [original]}), summary_record([original])]
    diff = compare_records(stock, bb)[1]
    assert not diff["compaction_input_findings"]
    assert diff["fields"][0]["classification"] == "pre-existing baseline"
    assert canonical(stock[1]["json"]) != canonical(bb[1]["json"])
    bb[1] = summary_record([{**original, "output": "changed only in summary"}])
    assert compare_records(stock, bb)[1]["fields"][0]["classification"] == "compaction-path"


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("executed_tool", [False, True])
@pytest.mark.parametrize("input_kind", ["native", "text"])
def test_continuation_trace_uses_previous_active_provider_turn_not_transcript_length(native, executed_tool, input_kind):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from breadboard_engine.conductor.modes import get_model_response
    from breadboard_engine.conductor.components import append_text_block
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime
    from breadboard_engine.state.session_state import SessionState

    class CapturedPlan(Exception):
        pass

    captured = []
    class Planner:
        def plan(self, **kwargs):
            captured.extend(kwargs["send_messages"])
            raise CapturedPlan

    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "Run tool"})
    state.add_message({"role": "assistant", "content": "Legacy tool rendering"})
    # Extra transcript entries must not change the identity of the provider
    # turn whose actual tool execution produced this continuation.
    for index in range(8):
        state.add_transcript_entry({"extra_context": index})
    state.begin_turn(3)
    if executed_tool:
        state.record_tool_event(3, "bash", success=True)
        state.set_provider_metadata("last_tool_execution_input_kind", input_kind)
        state.set_provider_metadata("last_tool_execution_turn_index", 3)
    conductor = SimpleNamespace(config={}, loop_detector=Mock(), tool_prompt_planner=Planner(),
                                current_native_tools=[SimpleNamespace(name="bash")] if native else [],
                                _append_text_block=append_text_block)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    with pytest.raises(CapturedPlan):
        get_model_response(conductor, client=None, runtime=runtime, model="gpt-test",
                           tool_prompt_mode="per_turn_append", tool_defs=[], active_dialect_names=[],
                           session_state=state, markdown_logger=Mock(), stream_responses=False,
                           local_tools_prompt="", client_config={})
    suppressed = native and executed_tool and input_kind == "native"
    assert len(captured) == (2 if suppressed else 3)
    if not suppressed:
        assert captured[-1] == {"role": "user", "content": "Continue."}


@pytest.mark.parametrize("content", [" \n\t", "\n", "\t  "])
def test_non_responses_whitespace_user_keeps_exact_base_stub_bytes(content):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from breadboard_engine.conductor.modes import get_model_response
    from breadboard_engine.conductor.components import append_text_block
    from breadboard_engine.state.session_state import SessionState

    class CapturedPlan(Exception):
        pass

    captured = []
    class Planner:
        def plan(self, **kwargs):
            captured.extend(kwargs["send_messages"])
            raise CapturedPlan

    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "Earlier context"})
    state.add_message({"role": "user", "content": content})
    conductor = SimpleNamespace(config={}, loop_detector=Mock(), tool_prompt_planner=Planner(),
                                current_native_tools=[], _append_text_block=append_text_block)
    runtime = SimpleNamespace(descriptor=SimpleNamespace(runtime_id="anthropic_messages", default_api_variant="messages"))
    with pytest.raises(CapturedPlan):
        get_model_response(conductor, client=None, runtime=runtime, model="test", tool_prompt_mode="per_turn_append",
                           tool_defs=[], active_dialect_names=[], session_state=state, markdown_logger=Mock(),
                           stream_responses=False, local_tools_prompt="", client_config={})
    assert captured[-1] == {"role": "user", "content": content}
    assert canonical(captured[-1]) == canonical(state.provider_messages[-1])


@pytest.mark.parametrize("catalog", ["defs_oc", "defs_omo"])
@pytest.mark.parametrize(("timeout_ms", "seconds"), [(None, 120.0), (1000, 1.0), (1, 0.001)])
def test_native_bash_translates_timeout_and_workdir_without_budget_reminders(tmp_path, timeout_ms, seconds, catalog):
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    conductor_class = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_class)
    conductor.workspace = str(tmp_path)
    conductor.config = {
        "tools": {"defs_dir": str(Path(__file__).resolve().parents[2] / f"implementations/tools/{catalog}")},
        "provider_tools": {"anthropic": {"system_reminders": {"enabled": True, "usd_budget_limit": 10}}},
    }
    calls = []
    def execute(command, *, timeout, stream):
        calls.append((command, timeout, stream))
        # Assert the timeout at the admission boundary, independently of host
        # process-startup latency for sub-millisecond fixtures.
        result = subprocess.run(command, shell=True, cwd=tmp_path, capture_output=True, text=True, timeout=10)
        return {"stdout": result.stdout, "stderr": result.stderr, "exit": result.returncode}
    conductor.sandbox = SimpleNamespace(run=SimpleNamespace(remote=execute))
    conductor._ray_get = lambda result: result
    workdir = tmp_path / "quoted ' subdirectory"
    workdir.mkdir()
    arguments = {"command": "pwd; printf stderr-output >&2", "description": "Print the selected working directory", "workdir": workdir.name}
    if timeout_ms is not None:
        arguments["timeout"] = timeout_ms
    result = conductor._exec_raw({"function": "bash", "arguments": arguments})
    assert calls[0][1:] == (seconds, False)
    assert result["stdout"].startswith(str(workdir) + "\n")
    from breadboard_engine.provider.adapters import OpenAIAdapter
    message = OpenAIAdapter().create_tool_result_message("call_bash", "bash", result)
    assert message["content"] == str(workdir) + "\nstderr-output"
    assert "<system-reminder>" not in json.dumps(result)


def test_terminal_answer_compaction_continues_before_zero_tool_watchdog(tmp_path, monkeypatch):
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider.routing import provider_router
    from scripts.compaction_lanes.opencode_lane import ResponsesProvider, is_summary
    from breadboard_engine.guardrails.orchestrator import GuardrailOrchestrator

    original_init = GuardrailOrchestrator.__init__
    initialized = []
    def initialize_watchdog(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.zero_tool_warn_turns = 1
        self.zero_tool_abort_turns = 1
        initialized.append(self)
    monkeypatch.setattr(GuardrailOrchestrator, "__init__", initialize_watchdog)

    settings = production_settings("opencode")
    turn = scenario_turns("final_threshold", settings)[0]
    script = [responses_sse(response, index) for index, response in enumerate(turn["responses"])]
    with ResponsesProvider(script=script) as provider:
        route = provider_router.providers["openai"]
        monkeypatch.setattr(route, "base_url", provider.base_url)
        monkeypatch.setattr(route, "auth_owner", "none")
        monkeypatch.setattr(route, "credential_required", False)
        agent = AgenticCoder(settings["profile"], str(tmp_path), overrides={
            "compaction.contextWindow": TEST_WINDOW,
            "providers.models[0].max_input_tokens": TEST_WINDOW,
            "enhanced_tools.lsp_integration.enabled": False,
        }, force_local_mode=True)
        result = agent.run_task("\n" + turn["prompt"], max_iterations=10, stream=True)
        assert initialized and initialized[0].zero_tool_abort_turns == 1
        assert result["completion_summary"]["completed"] is True
        assert len(provider.engine.recorded_requests) == 3
        assert sum(is_summary(row) for row in provider.engine.recorded_requests) == 1
        assert provider.engine.script == []


@pytest.mark.parametrize("name", ["run_shell", "bash"])
def test_seconds_based_catalog_keeps_legacy_shell_argument_path(name):
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    conductor_class = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_class)
    conductor.config = {}
    calls = []
    def legacy_shell(command, timeout):
        calls.append((command, timeout))
        return {"stdout": "legacy", "exit": 0}
    conductor.run_shell = legacy_shell
    result = conductor._exec_raw({"function": name, "arguments": {"command": "pwd", "timeout": 0.5}})
    assert calls == [("pwd", 0.5)]
    assert result == {"stdout": "legacy", "exit": 0}
