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


@pytest.mark.parametrize("harness", PROFILES)
def test_authenticated_source_mode_is_configuration_not_deviation(harness):
    fact = production_settings(harness)["source_api_authentication"]
    assert fact["configuration_fact_not_deviation"] is True
    assert fact["username"] == "opencode"
    assert "both source serve processes" in fact["mode"]
    assert "opencode-http-api.ts:68-72" in fact["source"][-1]


def test_stock_fixture_credentials_do_not_inherit_operator_credentials(monkeypatch, tmp_path):
    from scripts.compaction_lanes.opencode_lane import (
        SOURCE_API_PASSWORD, SOURCE_API_USERNAME, stock_environment,
    )
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "operator-password")
    monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "operator-user")
    env = stock_environment({"lsp": False}, tmp_path)
    assert env["OPENCODE_SERVER_PASSWORD"] == SOURCE_API_PASSWORD != "operator-password"
    assert env["OPENCODE_SERVER_USERNAME"] == SOURCE_API_USERNAME != "operator-user"


def test_stock_api_authenticates_with_its_launched_server_credentials(monkeypatch, tmp_path):
    import base64
    import io
    from types import SimpleNamespace
    from scripts.compaction_lanes import opencode_lane as lane
    launch = {}
    requests = []

    def start_process(command, **kwargs):
        launch.update(kwargs)
        return SimpleNamespace(
            stdout=io.StringIO("opencode server listening on http://127.0.0.1:9123\n"),
            terminate=lambda: None, wait=lambda timeout: 0,
        )

    def open_request(request, *, timeout):
        requests.append(request)
        assert timeout == 180
        return io.BytesIO(b'{"id":"fixture-response"}')

    monkeypatch.setattr(lane.subprocess, "Popen", start_process)
    monkeypatch.setattr(lane, "resolve_stock", lambda: tmp_path / "opencode")
    monkeypatch.setattr(lane.urllib.request, "urlopen", open_request)
    with lane.stock_server(tmp_path, tmp_path / "runtime", {"lsp": False}) as api:
        assert api("GET", "/session") == {"id": "fixture-response"}
    encoded = requests[0].get_header("Authorization").removeprefix("Basic ")
    assert base64.b64decode(encoded).decode() == (
        f'{launch["env"]["OPENCODE_SERVER_USERNAME"]}:'
        f'{launch["env"]["OPENCODE_SERVER_PASSWORD"]}'
    )


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


@pytest.mark.parametrize("harness", PROFILES)
def test_overflow_preserves_descriptors_and_fences_prior_prune(harness):
    settings = production_settings(harness)
    turns = scenario_turns("overflow", settings)
    calls = turns[0]["responses"][0]["tool_calls"]
    assert len(calls) == 12
    assert all("printf '%024000d' 0" in json.loads(call["arguments"])["command"] for call in calls)
    assert turns[-1]["responses"][0]["message"] == "prompt is too long: 74000 tokens > 64000 maximum"
    assert len(turns[-1]["responses"]) == 3 + 2 * settings["deferred_recovery_attempts"]
    if harness == "oh-my-opencode":
        assert len(turns) == 4
        assert turns[2]["native_prune_call_ids"] == [call["call_id"] for call in calls[:6]]
        assert all(turn["responses"][0]["type"] == "text" for turn in turns[1:3])
    else:
        assert len(turns) == 2
        assert all("native_prune_call_ids" not in turn for turn in turns)


def _native_prune_event_fence(monkeypatch, tmp_path, events, *, already_compacted=False):
    import io
    from types import SimpleNamespace
    from scripts.compaction_lanes import opencode_lane as lane
    part = {"id": "part", "messageID": "assistant", "sessionID": "root",
            "type": "tool", "callID": "call_seed", "tool": "bash",
            "state": {"status": "completed", "output": "Original tool output",
                      "time": {"start": 1, "end": 2}}}
    if already_compacted:
        part["state"]["time"]["compacted"] = 3
    def api(method, path):
        assert (method, path) == ("GET", "/session/root/message")
        return [{"info": {"id": "assistant"}, "parts": [part]}]
    response = io.BytesIO("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode())
    response.status = 200
    def request(method, path, *, headers):
        assert (method, path) == ("GET", "/event")
        assert headers["Authorization"].startswith("Basic ")
        assert headers["x-opencode-directory"] == str(tmp_path.resolve())
    connection = SimpleNamespace(
        sock=None, request=request, getresponse=lambda: response, close=lambda: None,
    )
    monkeypatch.setattr(lane.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    return lane.NativePruneFence(
        api, "http://127.0.0.1:9123", tmp_path, "root", ["call_seed"], "fixture-password",
    )


def _pruned_part_event(**changes):
    part = {"id": "part", "messageID": "assistant", "sessionID": "root",
            "type": "tool", "callID": "call_seed", "tool": "bash",
            "state": {"status": "completed", "output": "Original tool output",
                      "time": {"start": 1, "end": 2, "compacted": 3}}}
    part.update(changes)
    return {"type": "message.part.updated", "properties": {"part": part}}


def test_native_prune_fence_routes_symlink_to_the_source_instance(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "aliased-workspace"
    alias.symlink_to(workspace, target_is_directory=True)
    assert alias != alias.resolve()
    with _native_prune_event_fence(monkeypatch, alias, [_pruned_part_event()]) as fence:
        assert fence.wait()["native_parts"]["call_seed"]["compacted"] == 3


def test_native_prune_fence_ignores_child_collision_and_requires_owned_event(monkeypatch, tmp_path):
    events = [_pruned_part_event(sessionID="child", id="child-part"), _pruned_part_event()]
    with _native_prune_event_fence(monkeypatch, tmp_path, events) as fence:
        proof = fence.wait()
    assert proof["session_id"] == "root"
    assert proof["native_parts"]["call_seed"]["compacted"] == 3
    assert proof["native_parts"]["call_seed"]["id"] == "part"


@pytest.mark.parametrize("event,error", [
    (_pruned_part_event(id="wrong-part"), "changed its native part identity"),
    (_pruned_part_event(type="text"), "changed its native completed tool"),
    (_pruned_part_event(tool="different-tool"), "changed its native completed tool"),
    (_pruned_part_event(state={"status": "running", "output": "Original tool output",
                              "time": {"compacted": 3}}), "changed its native completed tool"),
    (_pruned_part_event(state={"status": "completed", "output": "Changed output",
                              "time": {"compacted": 3}}), "changed output, not native prune"),
    (_pruned_part_event(sessionID="child"), "ended before prune completed"),
    (_pruned_part_event(state={"status": "completed", "output": "Original tool output",
                              "time": {"compacted": True}}), "ended before prune completed"),
])
def test_native_prune_fence_rejects_unproven_prune(monkeypatch, tmp_path, event, error):
    with pytest.raises((ValueError, RuntimeError), match=error):
        with _native_prune_event_fence(monkeypatch, tmp_path, [event]) as fence:
            fence.wait()


def test_native_prune_fence_cannot_claim_already_pruned_state_as_new_event(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="subscribe before native pruning"):
        _native_prune_event_fence(monkeypatch, tmp_path, [], already_compacted=True)


@pytest.mark.skipif(os.environ.get("BB_OPENCODE_LANE_INTEGRATION") != "1", reason="opt-in real pinned stock/production BB lane")
@pytest.mark.parametrize("harness,scenario", [
    (harness, scenario) for harness in PROFILES for scenario in SCENARIOS
] + [("oh-my-opencode", scenario) for scenario in ("delegate_prune", "plugin_error", "delegate_idle")])
def test_pinned_production_lane_is_strict(harness, scenario, tmp_path):
    report = run_scenario(harness, scenario, tmp_path)
    assert {item["name"] for item in report["deviations_used"]} <= {
        "opencode_runtime_generated_ids", "opencode_created_time_tie_order"}
    assert report["diffs"] == []
    assert report["passed"], json.dumps(report, indent=2)
    assert report["settings"]["source_api_authentication"]["configuration_fact_not_deviation"]
    if harness == "oh-my-opencode" and scenario == "overflow":
        assert report["overflow_native_timing"]["real_human_ack_turns_before_overflow"] == 2
        expected_calls = [f"call_overflow_seed_{index}" for index in range(6)]
        for side, proofs in report["native_prune_fences"].items():
            assert len(proofs) == 1
            assert proofs[0]["call_ids"] == expected_calls
            assert set(proofs[0]["native_parts"]) == set(expected_calls)
            assert all(type(part["compacted"]) is int
                       for part in proofs[0]["native_parts"].values())
            result = json.loads((tmp_path / f"{side}_result.json").read_bytes())
            assert proofs[0]["session_id"] == result["session_id"]
        observed = json.loads((tmp_path / "bb_queued_observation.json").read_bytes())
        assert observed["native_prune_fences"] == report["native_prune_fences"]["bb"]
    counts = report["admitted_created_time_tie_groups_per_request"]
    assert [item["request"] for item in counts] == list(range(
        max(count["requests"] for count in report["counts"].values())))
    assert sum(item["tie_groups"] for item in counts) == sum(
        item["tie_groups"] for deviation in report["deviations_used"]
        if deviation["name"] == "opencode_created_time_tie_order"
        for item in deviation["counts_per_request"])
    if scenario == "delegate_idle":
        observed = json.loads((tmp_path / "bb_queued_observation.json").read_bytes())
        result = json.loads((tmp_path / "bb_result.json").read_bytes())
        assert observed["events"] == result["events"]
        assert observed["turns"] == result["turns"]
        assert observed["idle_checkpoints"] == result["idle_checkpoints"]


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
    def execute(command, *, timeout, stream, explicit_zero_timeout=False):
        calls.append((command, timeout, stream, explicit_zero_timeout))
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
    assert calls[0][1:] == (seconds, False, True)
    assert result["stdout"].startswith(str(workdir) + "\n")
    from breadboard_engine.provider.adapters import OpenAIAdapter
    message = OpenAIAdapter().create_tool_result_message("call_bash", "bash", result)
    assert message["content"] == str(workdir) + "\nstderr-output"
    assert "<system-reminder>" not in json.dumps(result)


def test_terminal_answer_compaction_continues_before_zero_tool_watchdog(tmp_path, monkeypatch):
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider.routing import provider_router
    from scripts.compaction_lanes.opencode_lane import ResponsesProvider, is_summary, create_empty_stock_sessions
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
    sessions = create_empty_stock_sessions(tmp_path, tmp_path / "seed_runtime", delegated=False)
    with ResponsesProvider(script=script) as provider:
        route = provider_router.providers["openai"]
        monkeypatch.setattr(route, "base_url", provider.base_url)
        monkeypatch.setattr(route, "auth_owner", "none")
        monkeypatch.setattr(route, "credential_required", False)
        agent = AgenticCoder(settings["profile"], str(tmp_path), overrides={
            "compaction.contextWindow": TEST_WINDOW,
            "providers.models[0].max_input_tokens": TEST_WINDOW,
            "enhanced_tools.lsp_integration.enabled": False,
            "tools.stock_config": {"lsp": False, "provider": {"openai": {"models": {
                settings["model"]: {"limit": {"context": TEST_WINDOW, "input": TEST_WINDOW,
                                             "output": settings["max_output_tokens"]}}}}}},
            "tools.stock_session_imports": sessions,
            "tools.stock_environment": settings["overrides"]["stock_environment"],
        }, force_local_mode=True)
        try:
            result = agent.run_task("\n" + turn["prompt"], max_iterations=10, stream=True,
                                    context={"session_id": sessions[0]["info"]["id"],
                                             "input_id": "watchdog-compaction-input",
                                             "turn_id": "watchdog-compaction-turn"})
        finally:
            agent.close()
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


@pytest.mark.parametrize(("timeout", "expected"), [(None, 30), (0, 30), (0.001, 0.001), (120, 120)])
@pytest.mark.parametrize("explicit_zero", [False, True])
def test_sandbox_run_distinguishes_zero_only_when_selected(timeout, expected, explicit_zero):
    from breadboard.sandbox import DevSandboxV2
    sandbox = object.__new__(DevSandboxV2.__ray_metadata__.modified_class)
    captured = {}
    def run_shell(command, **kwargs):
        captured.update(command=command, **kwargs)
        return {"exit": 0, "stdout": "output"}
    sandbox.run_shell = run_shell
    assert sandbox.run("command", timeout=timeout, stream=False,
                       explicit_zero_timeout=explicit_zero) == {"exit": 0, "stdout": "output"}
    assert captured["timeout"] == (0 if explicit_zero and timeout == 0 else expected)


def test_responses_profile_projection_preserves_unconfigured_bytes_and_provider_batches():
    from types import SimpleNamespace
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    messages = [{"role": "developer", "content": "system"}]
    for batch in (1, 2):
        for n in range(2):
            cid = f"call_{batch}_{n}"
            messages += [
                {"role": "assistant", "content": "", "_provider_tool_batch": batch,
                 "tool_calls": [{"id": cid, "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": cid, "content": f"output {batch} {n}"},
            ]
    base = runtime._convert_messages_to_input(messages, include_tool_calls=True)
    assert [m.get("type") for m in base[1:]] == ["function_call", "function_call_output"] * 4
    configured = runtime._convert_messages_to_input(messages, include_tool_calls=True,
        context=ProviderRuntimeContext(None, {"provider_tools": {
            "responses_developer_content": "string", "responses_group_tool_results": True}}))
    assert configured[0] == {"role": "developer", "content": "system"}
    assert [m["type"] for m in configured[1:]] == ["function_call", "function_call", "function_call_output", "function_call_output"] * 2
    assert [m["call_id"] for m in configured[1:]] == ["call_1_0", "call_1_1", "call_1_0", "call_1_1", "call_2_0", "call_2_1", "call_2_0", "call_2_1"]

@pytest.mark.parametrize("batch", [None, True, -1, "1", 1.5])
def test_provider_tool_batch_rejects_noncanonical_metadata(batch):
    from breadboard_engine.provider.contract_messages import normalize_request_messages
    from breadboard_engine.provider.contract_wire import ProviderContractError
    with pytest.raises(ProviderContractError, match="_provider_tool_batch"):
        normalize_request_messages([{"role": "assistant", "content": "", "_provider_tool_batch": batch}])


def test_provider_tool_batch_is_consumed_but_never_reaches_wire():
    from types import SimpleNamespace
    from breadboard_engine.provider.contract_messages import normalize_request_messages
    from breadboard_engine.provider.contract_wire import ProviderContractError
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.state.session_state import SessionState
    raw = []
    for n in range(2):
        raw += [{"role": "assistant", "content": "", "_provider_tool_batch": 0,
                 "tool_calls": [{"id": f"call_{n}", "type": "function",
                                 "function": {"name": "bash", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"call_{n}", "content": f"output {n}"}]
    messages = normalize_request_messages(raw)
    assert messages[0]["_provider_tool_batch"] == 0
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(SessionState("ws", "image", {}), {"provider_tools": {"openai": {
        "responses_group_tool_results": True, "responses_stateful": False}}})
    body = runtime._request_payload(model="test", messages=messages, tools=None, context=context)
    assert [item["type"] for item in body["input"]] == [
        "function_call", "function_call", "function_call_output", "function_call_output"]
    assert "_provider_tool_batch" not in json.dumps(body)
    assert "_provider_tool_batch" not in json.dumps(normalize_request_messages([{"role": "assistant", "content": "base"}]))
    with pytest.raises(ProviderContractError, match="_provider_tool_batch"):
        normalize_request_messages([{"role": "user", "content": "base", "_provider_tool_batch": 0}])



@pytest.mark.parametrize(("selected", "route", "expected"), [
    (False, "openai/test", None), (True, "openai/test", 32000),
    (True, "openai/other", None),
])
def test_ordinary_output_cap_requires_explicit_flag_and_exact_route(selected, route, expected):
    from types import SimpleNamespace
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.state.session_state import SessionState
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    config = {"providers": {"models": [{"id": "openai/test", "max_output_tokens": 32000}]},
              "provider_tools": {"openai": {"responses_max_output_tokens_from_context": selected}}}
    context = ProviderRuntimeContext(SessionState("ws", "image", {}), config, extra={"route_id": route})
    body = runtime._request_payload(model="test", messages=[{"role": "user", "content": "Task"}],
                                  tools=None, context=context)
    assert body.get("max_output_tokens") == expected
    assert ("max_output_tokens" in body) is (expected is not None)


def test_delegated_scenario_launches_real_background_task_before_same_turn_compactions():
    settings = production_settings("oh-my-opencode")
    turns = scenario_turns("delegate_prune", settings)
    delegated = turns[-1]["responses"]
    arguments = json.loads(delegated[0]["tool_calls"][0]["arguments"])
    assert arguments["run_in_background"] is True
    assert arguments["subagent_type"] == "explore"
    assert "session_id" not in arguments
    assert len(turns) == 4
    assert delegated[1:] == scenario_turns("auto_prune", settings)[-1]["responses"]


def test_source_input_projection_changes_only_current_user(monkeypatch):
    from types import SimpleNamespace
    from breadboard_engine.compilation.context_builders import opencode_stock
    original = {"role": "user", "content": "Original input", "source_id": "bb-current"}
    current = [{"type": "input_text", "text": "[analyze-mode]\nSource transformed input"}]
    native = {"input": [
        {"role": "user", "content": "Never copy this native old history"},
        {"role": "user", "content": current},
    ]}
    monkeypatch.setattr(opencode_stock, "prepare_stock_initial_user", lambda owner, state: native)
    state = SimpleNamespace(_opencode_tool_session=SimpleNamespace(
        latest_user_sequence=lambda: [{"type": "text", "text": current[0]["text"]}]))
    assert opencode_stock.build_context(None, state, None, fresh=True, user_message=original) == []
    assert original == {"role": "user", "content": [{"type": "text", "text": current[0]["text"]}], "source_id": "bb-current"}
    assert original["content"] is not current
    from breadboard_engine.provider.contract_messages import normalize_content
    assert normalize_content(original["content"], role="user") == original["content"]


def test_source_input_projection_keeps_authorized_media_and_order(monkeypatch):
    from types import SimpleNamespace
    from breadboard_engine.compilation.context_builders import opencode_stock
    media = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "authorized"}}
    original = {"role": "user", "content": [
        {"type": "text", "text": "Before"}, media, {"type": "text", "text": "After"}]}
    texts = [{"type": "text", "text": "Source hook prefix"}, {"type": "text", "text": "Source before"},
             {"type": "text", "text": "Source after"}]
    native = {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "never copy"}]}]}
    monkeypatch.setattr(opencode_stock, "prepare_stock_initial_user", lambda owner, state: native)
    state = SimpleNamespace(_opencode_tool_session=SimpleNamespace(
        latest_user_sequence=lambda: [texts[0], texts[1], {"type": "stock_media", "index": 0}, texts[2]]))
    opencode_stock.build_context(None, state, None, fresh=True, user_message=original)
    assert original["content"] == [texts[0], texts[1], media, texts[2]]
    assert original["content"][2] is media
    assert original["content"][0] is not texts[0]


@pytest.mark.parametrize(("reason", "auto"), [("manual", False), ("threshold", True), ("overflow", True)])
def test_source_summary_projection_preserves_bb_history_and_lifecycle(reason, auto):
    from types import SimpleNamespace
    from breadboard_engine.compaction.methods import SummaryRequest, SummaryResponse
    from breadboard_engine.compilation.context_builders.opencode_stock import build_summary_projection
    calls = []
    instruction = [{"type": "input_text", "text": "Native summary contributor\n### Active/Recent Delegated Sessions\n"}]
    def prompt(**kwargs):
        calls.append(kwargs)
        return {"input": [{"role": "user", "content": "Never copy native old history"},
                          {"role": "user", "content": instruction}]}
    observed = []
    port = build_summary_projection(SimpleNamespace(
        _opencode_tool_session=SimpleNamespace(compaction_prompt=prompt,
                                               set_compaction_response=observed.append)), {})
    bb_history = {"role": "assistant", "content": "Canonical BB fact"}
    request = SummaryRequest("summary system", (bb_history, {"role": "user", "content": "BB instruction"}),
                             32000, "summary", context_reason=reason)
    prepared = port.prepare(request)
    assert calls == [{"auto": auto}]
    assert prepared.messages[0] is bb_history
    assert prepared.messages[-1] == {"role": "user", "content": [{"type": "text", "text": instruction[0]["text"]}]}
    assert prepared.system == request.system and prepared.max_tokens == request.max_tokens
    assert request.messages[-1]["content"] == "BB instruction"
    from breadboard_engine.provider.contract_messages import ProviderResult, ProviderMessage
    result = ProviderResult(messages=[ProviderMessage(role="assistant", content="Actual BB summary")],
                            raw_response=None, usage={"input_tokens": 13, "output_tokens": 5}, model="summary")
    assert port.complete(SummaryResponse("Actual BB summary", provider_result=result)) is None
    assert observed == [result] and observed[0] is result


def test_stock_text_projection_rejects_lossy_field_dropping():
    from breadboard_engine.compilation.context_builders.opencode_stock import _last_user_content
    from breadboard_engine.execution.opencode_tools import StockExecutionError
    with pytest.raises(StockExecutionError, match="exact input_text"):
        _last_user_content({"input": [{"role": "user", "content": [
            {"type": "input_text", "text": "input", "unexpected": "must not disappear"}]}]})


def test_native_fixture_imports_are_never_production_profile_data():
    from pathlib import Path
    import yaml
    from scripts.compaction_lanes.opencode_lane import ROOT
    for profile in PROFILES.values():
        config = yaml.safe_load((ROOT / profile).read_text())
        assert "stock_session_imports" not in config["tools"]


def test_source_model_identity_environment_uses_real_router_tuple(tmp_path):
    from breadboard_engine.agent_llm_openai import OpenAIConductor
    conductor = object.__new__(OpenAIConductor.__ray_metadata__.modified_class)
    conductor.workspace = str(tmp_path)
    conductor.config = {"providers": {"default_model": "openai/gpt-5.1-codex-mini"},
                        "prompts": {"environment": {"include_model_identity": True,
                                                    "include_tree": False, "files_tag": "directories"}}}
    block = conductor._build_opencode_environment_block()
    assert block.startswith("You are powered by the model named gpt-5.1-codex-mini. The exact model ID is openai/gpt-5.1-codex-mini\n")
    assert block.endswith("<directories>\n  \n</directories>")


def test_mcp_relay_preserves_real_payload_bytes_and_orders_readiness(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import urllib.request
    from scripts.compaction_lanes.opencode_lane import MCPRelay
    request_bytes = b'{ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }'
    response_bytes = b'{ "jsonrpc":"2.0", "id":1, "result":{"tools":[]} }\n'
    received = []
    released = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["content-length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.send_header("Mcp-Session-Id", "actual-upstream-id")
            self.end_headers()
            self.wfile.write(response_bytes)
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{upstream.server_port}"
    monkeypatch.setattr(MCPRelay, "UPSTREAMS", {"first": endpoint, "second": endpoint})
    try:
        with MCPRelay() as relay, ThreadPoolExecutor(max_workers=2) as pool:
            def request(name):
                req = urllib.request.Request(relay.url + "/mcp/" + name, data=request_bytes,
                                             headers={"content-type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=10) as response:
                    result = (response.read(), response.headers["Mcp-Session-Id"])
                released.append(name)
                return result
            second = pool.submit(request, "second")
            first = pool.submit(request, "first")
            assert first.result() == second.result() == (response_bytes, "actual-upstream-id")
            assert released == ["first", "second"]
            assert received == [request_bytes, request_bytes]
            assert relay.failures == []
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()


def _runtime_id_api_fixture(background, child):
    root = "ses_012345abcdef" + "A" * 14
    output = (
        f"Background task launched.\n\nBackground Task ID: {background}\n"
        "Description: Delegated inspection\nAgent: explore\nStatus: running\n\n"
        f'System notifies on completion. Use `background_output` with task_id="{background}" to check.'
        f"\n\n<task_metadata>\nsession_id: {child}\ntask_id: {child}\n"
        f"background_task_id: {background}\n</task_metadata>"
    )
    return {"session": {"id": root}, "children": [{"session": {"id": child, "parentID": root}, "messages": []}],
            "messages": [{"info": {"id": "native-launch", "role": "assistant", "sessionID": root},
                          "parts": [{"type": "tool", "tool": "task", "callID": "call_delegated",
                                     "state": {"input": {"run_in_background": True, "description": "Delegated inspection"},
                                               "metadata": {"sessionId": child}, "output": output}}]}]}


def _runtime_id_guard_fixture():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    stock = _runtime_id_api_fixture("bg_012345ab", "ses_" + "a" * 12 + "B" * 14)
    bb = _runtime_id_api_fixture("bg_abcdef01", "ses_" + "b" * 12 + "C" * 14)
    return RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]]), stock, bb


def test_runtime_id_deviation_guards_provenance_and_counts_exact_positions():
    from scripts.compaction_lanes.opencode_lane import canonical
    guard, stock, bb = _runtime_id_guard_fixture()
    projected = []
    for side, evidence in (("stock", stock), ("bb", bb)):
        child = evidence["children"][0]["session"]["id"]
        body = {"model": "test", "prompt_cache_key": evidence["session"]["id"], "input": [
            {"role": "developer", "content": "You are a helpful AI assistant tasked with summarizing conversations."},
            {"type": "function_call_output", "call_id": "call_delegated",
             "output": evidence["messages"][0]["parts"][0]["state"]["output"]},
            {"role": "user", "content": [{"type": "input_text", "text":
                "Instruction\n### Active/Recent Delegated Sessions\n"
                f"- **explore**(running): Delegated inspection | session: `{child}`\n"}]}]}
        before = canonical(body)
        projected.append(guard.body(body, side))
        assert canonical(body) == before
        assert guard.body({"prompt_cache_key": child, "input": []}, side)["prompt_cache_key"] == "<runtime-session-0>"
    assert canonical(projected[0]) == canonical(projected[1])
    assert guard.counts["stock"] == guard.counts["bb"] == {
        "background_output.bg_meta": 1, "background_output.session_ref": 1,
        "background_output.session": 1, "background_output.bg_ref": 1,
        "background_output.bg": 1, "summary.active_recent": 1, "delegated.prompt_cache_key": 1}


def test_runtime_id_deviation_rejects_mismatched_native_api_mapping():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    bb["messages"][0]["parts"][0]["state"]["metadata"]["sessionId"] = stock["children"][0]["session"]["id"]
    with pytest.raises(ValueError, match="provenance"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])


def test_runtime_id_deviation_rejects_ids_outside_approved_positions():
    guard, stock, _ = _runtime_id_guard_fixture()
    child = stock["children"][0]["session"]["id"]
    with pytest.raises(ValueError, match="outside an approved position"):
        guard.body({"prompt_cache_key": stock["session"]["id"],
                    "input": [{"role": "user", "content": child}]}, "stock")


def test_runtime_id_deviation_rejects_root_id_difference():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    bb["session"]["id"] = "ses_" + "c" * 12 + "D" * 14
    with pytest.raises(ValueError, match="root session IDs differ"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])


def test_runtime_id_deviation_rejects_regex_only_identifier():
    guard, _, _ = _runtime_id_guard_fixture()
    with pytest.raises(ValueError, match="Unmapped runtime ID"):
        guard.body({"prompt_cache_key": "ses_" + "d" * 12 + "E" * 14, "input": []}, "stock")


@pytest.mark.parametrize("sequence", [
    [{"type": "stock_media", "index": 1}],
    [{"type": "stock_media", "index": 0}, {"type": "stock_media", "index": 0}],
    [{"type": "text", "text": "No authorized media slot"}],
])
def test_source_input_projection_rejects_unauthorized_or_missing_media(monkeypatch, sequence):
    from types import SimpleNamespace
    from breadboard_engine.compilation.context_builders import opencode_stock
    from breadboard_engine.execution.opencode_tools import StockExecutionError
    original = {"role": "user", "content": [{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "authorized"}}]}
    monkeypatch.setattr(opencode_stock, "prepare_stock_initial_user", lambda owner, state: {"input": []})
    state = SimpleNamespace(_opencode_tool_session=SimpleNamespace(latest_user_sequence=lambda: sequence))
    with pytest.raises(StockExecutionError, match="media"):
        opencode_stock.build_context(None, state, None, fresh=True, user_message=original)


def _pending_recovery_setup():
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.state.session_state import SessionState
    from tests.compaction.test_opencode_integration import FakeRuntime
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "Finish the inspection."})
    state.set_provider_metadata("usage", {"total_tokens": 150})
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "oh-my-opencode@3.10.0", "contextWindow": TEST_WINDOW,
    }})
    return controller, state, FakeRuntime()


def _finish_pending_recovery(controller, state, runtime):
    return controller.finish_assistant_step(
        state, conductor=None, runtime=runtime, client=object(),
        model="gpt-test", turn_index=1,
    )


def _arm_native_overflow(controller, state, runtime):
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
    error = ProviderRuntimeError("provider operation failed", details={
        "code": "context_length_exceeded", "overflow_tokens": 74000, "overflow_limit": 64000,
    })
    error.__cause__ = RuntimeError("prompt is too long: 74000 tokens > 64000 maximum")
    assert controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime,
        client=object(), model="gpt-test",
    )


def test_pending_error_trigger_without_pending_state_does_nothing():
    controller, state, runtime = _pending_recovery_setup()
    state.add_message({"role": "assistant", "content": "Inspection complete."})
    assert _finish_pending_recovery(controller, state, runtime) is False
    assert runtime.summary_requests == []


def test_pending_error_trigger_fires_each_checkpoint_until_attempts_run_out():
    controller, state, runtime = _pending_recovery_setup()
    _arm_native_overflow(controller, state, runtime)
    state.add_message({"role": "assistant", "content": "Inspection complete."})
    assert _finish_pending_recovery(controller, state, runtime) is True
    assert len(runtime.summary_requests) == 2
    assert state.compaction_state.records[-1].reason == "threshold"
    state.add_message({"role": "assistant", "content": "Inspection complete after deferred recovery."})
    assert _finish_pending_recovery(controller, state, runtime) is True
    assert len(runtime.summary_requests) == 3
    state.add_message({"role": "assistant", "content": "Inspection finally complete."})
    assert _finish_pending_recovery(controller, state, runtime) is False
    assert len(runtime.summary_requests) == 3


def test_pending_error_trigger_summary_last_skip_clears_pending_state():
    controller, state, runtime = _pending_recovery_setup()
    _arm_native_overflow(controller, state, runtime)
    assert _finish_pending_recovery(controller, state, runtime) is False
    state.add_message({"role": "assistant", "content": "A later answer."})
    assert _finish_pending_recovery(controller, state, runtime) is False
    assert len(runtime.summary_requests) == 1


@pytest.mark.parametrize("replacement", [
    {"kind": "pending_error", "phase": "assistant_step_end"},
    {"kind": "pending_error", "phase": "user_turn_end",
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error", "max_attempts": 2},
    {"kind": "pending_error", "phase": "assistant_step_end", "error_parser": "os:system", "max_attempts": 2},
    {"kind": "pending_error", "phase": "assistant_step_end", "max_attempts": 2,
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error", "unknown": True},
    {"kind": "pending_error", "phase": "assistant_step_end", "max_attempts": 0,
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error"},
    {"kind": "pending_error", "phase": "assistant_step_end", "max_attempts": None,
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error"},
    {"kind": "pending_error", "phase": "assistant_step_end",
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error"},
])
def test_pending_error_trigger_rejects_invalid_preset_members(replacement):
    from breadboard_engine.compaction.params import PresetError
    from breadboard_engine.compaction.presets import build_recipe, load_preset_document
    controller, _, _ = _pending_recovery_setup()
    document = load_preset_document("oh-my-opencode@3.10.0")
    document["triggers"][1] = replacement
    with pytest.raises(PresetError):
        build_recipe(controller.config, document)


@pytest.mark.parametrize("error,expected", [
    ("prompt is too long: 74000 tokens > 64000 maximum", {"overflow_tokens": 74000, "overflow_limit": 64000}),
    ("max context 64000 but 74000: context length exceeded", {"overflow_tokens": 74000, "overflow_limit": 64000}),
    ("thinking must be the first block: context length exceeded", None),
    ("unrelated provider failure", None),
    ({"data": {"responseBody": '{"error":{"message":"prompt is too long: 74000 tokens > 64000 maximum"}}'}},
     {"overflow_tokens": 74000, "overflow_limit": 64000}),
])
def test_plugin_pending_error_parser_matches_source_cases(error, expected):
    from breadboard_engine.compaction.oh_my_opencode import parse_token_limit_error
    assert parse_token_limit_error(error) == expected


def test_runtime_id_deviation_guards_source_continuation_hint_and_counts():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    for evidence in (stock, bb):
        child = evidence["children"][0]["session"]["id"]
        evidence["messages"][0]["parts"][0]["state"]["output"] += (
            f'\n\nto continue: task(session_id="{child}", prompt="...")')
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    projected = []
    for side, evidence in (("stock", stock), ("bb", bb)):
        projected.append(guard.body({"input": [{"type": "function_call_output", "call_id": "call_delegated",
            "output": evidence["messages"][0]["parts"][0]["state"]["output"]}]}, side))
    assert projected[0] == projected[1]
    assert guard.counts["stock"]["background_output.session_hint"] == 1
    assert guard.counts["stock"] == guard.counts["bb"]


def test_runtime_id_deviation_rejects_wrong_source_continuation_hint():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    wrong = stock["children"][0]["session"]["id"]
    bb["messages"][0]["parts"][0]["state"]["output"] += (
        f'\n\nto continue: task(session_id="{wrong}", prompt="...")')
    with pytest.raises(ValueError, match="continuation hint"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])


def test_background_mock_request_does_not_consume_root_response():
    from scripts.compaction_lanes.opencode_lane import SessionScriptEngine
    engine = SessionScriptEngine(script=[{"type": "text", "text": "Root reply"}])
    engine.delegated_root = "root-session"
    def record(session):
        body = {"prompt_cache_key": session}
        engine.record_request(method="POST", path="/v1/responses", headers={},
                              raw_body=json.dumps(body).encode(), parsed_json=body)
    record("real-child-session")
    assert engine.next_response() == {"type": "delegated_pending"}
    assert len(engine.script) == 1
    record("root-session")
    assert engine.next_response() == {"type": "text", "text": "Root reply"}
    assert len(engine.recorded_requests) == 2
    assert engine.script == []


def _time_tie_fixture():
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    def native(identifier, role, created, text, parent=None):
        return {"info": {"id": identifier, "role": role, "time": {"created": created},
                         "parentID": parent}, "parts": [{"type": "text", "text": text}]}
    stock = {"messages": [native("sa", "assistant", 10, "Done", "su"),
                           native("su", "user", 10, "Finish")]}
    bb = {"messages": [native("bu", "user", 20, "Finish"),
                       native("ba", "assistant", 21, "Done", "bu")]}
    stock["session"] = {"id": "root-session"}
    bb["session"] = {"id": "root-session"}
    header = {"role": "developer", "content": "Unchanged system"}
    stock_body = {"input": [header, *map(CreatedTimeTieOrder.wire, stock["messages"])],
                  "store": False}
    bb_body = {"input": [header, *map(CreatedTimeTieOrder.wire, bb["messages"])],
               "store": False}
    stock_body["prompt_cache_key"] = bb_body["prompt_cache_key"] = "root-session"
    return stock, bb, stock_body, bb_body


def test_created_time_tie_order_uses_native_tie_and_distinct_bb_times():
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected == right
    assert left["input"][1]["role"] == "assistant"
    assert groups == [{"stock_created": 10, "stock_message_ids": ["sa", "su"],
                       "bb_message_ids": ["ba", "bu"], "bb_created": [21, 20],
                       "request_slots": [1, 2]}]


@pytest.mark.parametrize("failure", ["distinct_stock_times", "no_evidence", "content",
                                    "other_field", "tied_bb_times", "wrong_bb_order"])
def test_created_time_tie_order_rejects_unproven_swaps_or_any_other_difference(failure):
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    if failure == "distinct_stock_times":
        stock["messages"][1]["info"]["time"]["created"] = 9
    elif failure == "no_evidence":
        stock["messages"] = []
    elif failure == "content":
        right["input"][1]["content"][0]["text"] += " changed"
    elif failure == "other_field":
        right["store"] = True
    elif failure == "tied_bb_times":
        bb["messages"][1]["info"]["time"]["created"] = 20
    else:
        bb["messages"][0]["info"]["time"]["created"] = 22
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected is left
    assert groups == []
    assert compare_records([record(projected)], [record(right)])


def test_created_time_tie_order_identifies_replay_user_by_genuine_assistant_parent():
    import copy
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    original = copy.deepcopy(bb["messages"][0])
    original["info"].update(id="original-user", time={"created": 1})
    bb["messages"].insert(0, original)
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected == right
    assert groups[0]["bb_message_ids"] == ["ba", "bu"]


def _tool_time_tie_fixture(tool_count=1):
    import copy
    stock, bb, left, right = _time_tie_fixture()
    tools, calls, outputs = [], [], []
    for index in range(tool_count):
        call_id = f"call-{index}"
        arguments = {"command": f"printf {index}"}
        output = str(index)
        tools.append({"type": "tool", "callID": call_id, "tool": "bash",
                      "state": {"status": "completed", "input": arguments, "output": output}})
        calls.append({"type": "function_call", "call_id": call_id, "name": "bash",
                      "arguments": json.dumps(arguments, separators=(",", ":"))})
        outputs.append({"type": "function_call_output", "call_id": call_id, "output": output})
    stock["messages"][0]["parts"] = tools
    bb["messages"][1]["parts"] = copy.deepcopy(tools)
    user = left["input"][2]
    left["input"] = [left["input"][0], *calls, *outputs, user]
    right["input"] = copy.deepcopy([right["input"][0], user, *calls, *outputs])
    return stock, bb, left, right


@pytest.mark.parametrize("tool_count", [1, 2])
def test_created_time_tie_order_moves_genuine_contiguous_tool_slice_as_one_unit(tool_count):
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, left, right = _tool_time_tie_fixture(tool_count)
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected == right
    assert left["input"][1]["type"] == "function_call"
    assert groups == [{"stock_created": 10, "stock_message_ids": ["sa", "su"],
                       "bb_message_ids": ["ba", "bu"], "bb_created": [21, 20],
                       "request_slots": list(range(1, 2 * tool_count + 2))}]


@pytest.mark.parametrize("failure", [
    "split_stock_slice", "split_bb_slice", "call_id", "output", "arguments",
    "malformed_stock_arguments", "malformed_bb_arguments",
    "tool_name", "no_evidence", "non_tied_stock", "non_tied_bb_order", "other_field",
])
def test_created_time_tie_order_rejects_unproven_tool_slices(failure):
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, left, right = _tool_time_tie_fixture()
    if failure == "split_stock_slice":
        left["input"][2], left["input"][3] = left["input"][3], left["input"][2]
    elif failure == "split_bb_slice":
        right["input"][1], right["input"][2] = right["input"][2], right["input"][1]
    elif failure == "call_id":
        right["input"][3]["call_id"] = "unproven-call"
    elif failure == "output":
        right["input"][3]["output"] = "different"
    elif failure == "arguments":
        right["input"][2]["arguments"] = '{"command":"different"}'
    elif failure == "malformed_stock_arguments":
        left["input"][1]["arguments"] = '{"command":'
    elif failure == "malformed_bb_arguments":
        right["input"][2]["arguments"] = '{"command":'
    elif failure == "tool_name":
        right["input"][2]["name"] = "unproven-tool"
    elif failure == "no_evidence":
        stock["messages"] = []
    elif failure == "non_tied_stock":
        stock["messages"][1]["info"]["time"]["created"] = 9
    elif failure == "non_tied_bb_order":
        bb["messages"][0]["info"]["time"]["created"] = 22
    else:
        right["store"] = True
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected is left
    assert groups == []
    assert compare_records([record(projected)], [record(right)])


def test_created_time_tie_order_cannot_cross_a_non_tied_native_message():
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    untied = {"role": "user", "content": [{"type": "input_text", "text": "Not in the tie"}]}
    left["input"].insert(2, untied)
    right["input"].insert(2, untied)
    stock["messages"].append({"info": {"id": "other", "role": "user", "time": {"created": 11}},
                              "parts": [{"type": "text", "text": "Not in the tie"}]})
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected is left
    assert groups == []


def test_created_time_tie_order_validates_raw_native_slices_before_admitted_id_projection():
    import copy
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, raw_left, raw_right = _tool_time_tie_fixture()
    raw_left["input"][2]["output"] = "stock runtime ID"
    stock["messages"][0]["parts"][0]["state"]["output"] = "stock runtime ID"
    raw_right["input"][3]["output"] = "BB runtime ID"
    bb["messages"][1]["parts"][0]["state"]["output"] = "BB runtime ID"
    left, right = copy.deepcopy(raw_left), copy.deepcopy(raw_right)
    left["input"][2]["output"] = right["input"][3]["output"] = "<already-admitted-runtime-ID>"
    projected, groups = CreatedTimeTieOrder(stock, bb).body(
        left, right, raw_stock=raw_left, raw_bb=raw_right)
    assert projected == right
    assert len(groups) == 1


@pytest.mark.parametrize("failure", [
    "colliding_child", "missing_stock_owner", "missing_bb_owner",
    "wrong_stock_owner", "wrong_bb_owner", "missing_stock_session", "missing_bb_session",
])
def test_created_time_tie_order_cannot_borrow_another_sessions_native_tie(failure):
    import copy
    from scripts.compaction_lanes.opencode_lane import CreatedTimeTieOrder
    stock, bb, raw_left, raw_right = _tool_time_tie_fixture()
    if failure == "colliding_child":
        for evidence, body, child_id in (
            (stock, raw_left, "stock-child"), (bb, raw_right, "bb-child"),
        ):
            messages = copy.deepcopy(evidence["messages"])
            for index, message in enumerate(messages):
                message["info"]["id"] = f"{child_id}-{index}"
                message["info"]["sessionID"] = child_id
                message["info"]["time"]["created"] = 100 + index
            child_user = next(message["info"]["id"] for message in messages
                              if message["info"]["role"] == "user")
            for message in messages:
                if message["info"]["role"] == "assistant":
                    message["info"]["parentID"] = child_user
            evidence["children"] = [{"session": {"id": child_id, "parentID": "root-session"},
                                     "messages": messages}]
            body["prompt_cache_key"] = child_id
        left, right = copy.deepcopy(raw_left), copy.deepcopy(raw_right)
        left["prompt_cache_key"] = right["prompt_cache_key"] = "<already-admitted-child-id>"
    else:
        evidence = stock if "stock" in failure else bb
        body = raw_left if "stock" in failure else raw_right
        if "missing" in failure:
            (evidence if "session" in failure else body).pop(
                "session" if "session" in failure else "prompt_cache_key")
        else:
            body["prompt_cache_key"] = "unproven-owner"
        left, right = copy.deepcopy(raw_left), copy.deepcopy(raw_right)
    projected, groups = CreatedTimeTieOrder(stock, bb).body(
        left, right, raw_stock=raw_left, raw_bb=raw_right)
    assert projected is left
    assert groups == []
    assert compare_records([record(projected)], [record(right)])


@pytest.mark.parametrize("call_id", ["call_bash", None])
def test_runtime_id_deviation_rejects_launch_text_from_unproven_wire_call(call_id):
    guard, stock, _ = _runtime_id_guard_fixture()
    body = {"input": [{"type": "function_call_output", "call_id": call_id,
                       "output": stock["messages"][0]["parts"][0]["state"]["output"]}]}
    with pytest.raises(ValueError, match="native task provenance"):
        guard.body(body, "stock")


def test_runtime_id_deviation_rejects_other_proven_tasks_id_pair():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    for evidence, background, child in (
        (stock, "bg_22222222", "ses_" + "2" * 12 + "D" * 14),
        (bb, "bg_33333333", "ses_" + "3" * 12 + "E" * 14),
    ):
        other = _runtime_id_api_fixture(background, child)
        other["messages"][0]["parts"][0]["callID"] = "call_other"
        evidence["messages"] += other["messages"]
        evidence["children"] += other["children"]
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    body = {"input": [{"type": "function_call_output", "call_id": "call_delegated",
                       "output": stock["messages"][1]["parts"][0]["state"]["output"]}]}
    with pytest.raises(ValueError, match="native task provenance"):
        guard.body(body, "stock")


@pytest.mark.parametrize("output", ["malformed launch output", "<runtime-bg-0>"])
def test_runtime_id_deviation_rejects_malformed_task_output_and_raw_sentinels(output):
    guard, _, _ = _runtime_id_guard_fixture()
    body = {"input": [{"type": "function_call_output", "call_id": "call_delegated", "output": output}]}
    with pytest.raises(ValueError, match="malformed wire output|reserved comparison marker"):
        guard.body(body, "stock")


def test_runtime_id_deviation_allows_only_genuinely_pruned_task_placeholder():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    guard, stock, bb = _runtime_id_guard_fixture()
    body = {"input": [{"type": "function_call_output", "call_id": "call_delegated",
                       "output": "[Old tool result content cleared]"}]}
    with pytest.raises(ValueError, match="malformed wire output"):
        guard.body(body, "stock")
    stock["messages"][0]["parts"][0]["state"]["time"] = {"compacted": 10}
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    assert guard.body(body, "stock") == body
    assert guard.counts["stock"] == {}


def test_mock_script_progress_is_read_only_and_tracks_real_provider_responses():
    import urllib.request
    from scripts.compaction_lanes.opencode_lane import ResponsesProvider, text_response
    with ResponsesProvider(script=[responses_sse(text_response("Root reply"), 0)]) as provider:
        url = provider.base_url + "/v1"
        def progress():
            with urllib.request.urlopen(url + "/lane/script-progress") as response:
                return json.load(response)["remaining"]
        assert progress() == 1
        assert provider.engine.recorded_requests == []
        request = urllib.request.Request(url + "/responses", method="POST",
            data=json.dumps({"model": "m", "input": [], "stream": True}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            response.read()
        assert progress() == 0
        assert [row["path"] for row in provider.engine.recorded_requests] == ["/v1/responses"]


def test_auxiliary_fifo_serves_real_multimodal_request_without_consuming_root_script():
    import urllib.request
    from scripts.compaction_lanes.opencode_lane import ResponsesProvider, text_response
    with ResponsesProvider(script=[responses_sse(text_response("Root reply"), 0)]) as provider:
        provider.engine.set_child_script("root-session", [responses_sse(text_response("Image inspected"), 1)])
        def request(session, content):
            body = {"model": "m", "prompt_cache_key": session, "stream": True,
                    "input": [{"role": "user", "content": content}]}
            with urllib.request.urlopen(urllib.request.Request(
                provider.base_url + "/v1/responses", method="POST",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})) as response:
                return response.read()
        content = [{"type": "input_text", "text": "Inspect this image"},
                   {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}]
        assert b"Image inspected" in request("native-child-session", content)
        assert provider.engine.child_script == []
        assert len(provider.engine.script) == 1
        assert b"Root reply" in request("root-session", [{"type": "input_text", "text": "Finish"}])
        assert provider.engine.script == []
        assert provider.engine.recorded_requests[0]["json"]["input"][0]["content"] == content


def test_auxiliary_fifo_supports_explicit_held_descriptor_without_root_dispatch():
    from scripts.compaction_lanes.opencode_lane import SessionScriptEngine
    engine = SessionScriptEngine(script=[{"type": "text", "text": "Root"}])
    engine.set_child_script("root-session", [{"type": "delegated_pending"}])
    body = {"prompt_cache_key": "native-child-session"}
    engine.record_request(method="POST", path="/v1/responses", headers={},
                          raw_body=json.dumps(body).encode(), parsed_json=body)
    assert engine.next_response() == {"type": "delegated_pending"}
    assert engine.child_script == []
    assert engine.script == [{"type": "text", "text": "Root"}]


@pytest.mark.parametrize("preset", ["opencode@1.2.17", "oh-my-opencode@3.10.0"])
def test_opencode_native_classifier_keeps_plugin_only_wording_out_of_native_overflow(preset):
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.overflow import is_context_overflow
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    message = "too many tokens: 74000 tokens > 64000 maximum"
    assert is_context_overflow(message) is True
    assert controller.recipe.error_classifier(message) is False


@pytest.mark.parametrize("message", [
    "prompt is too long: 74000 tokens > 64000 maximum",
    "input is too long for requested model", "exceeds the context window",
    "input token count 74000 exceeds the maximum", "maximum prompt length is 64000",
    "reduce the length of the messages", "maximum context length is 64000 tokens",
    "exceeds the limit of 64000", "exceeds the available context size",
    "greater than the context length", "context window exceeds limit",
    "exceeded model token limit", "context_length_exceeded", "request entity too large",
    "400 (no body)", "413 status code (no body)",
])
def test_opencode_native_classifier_matches_pinned_regular_patterns(message):
    from breadboard_engine.compaction.opencode import is_context_overflow
    assert is_context_overflow(message)


def test_plugin_only_provider_error_recovers_at_pending_checkpoint_not_native_overflow():
    from breadboard_engine.compaction.overflow import provider_overflow_details
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
    controller, state, runtime = _pending_recovery_setup()
    class SDKError(Exception):
        status_code = 400
        body = {"message": "too many tokens: 74000 tokens > 64000 maximum"}
    try:
        raise SDKError("too many tokens: 74000 tokens > 64000 maximum")
    except SDKError as sdk_error:
        error = ProviderRuntimeError("provider operation failed", details=provider_overflow_details(sdk_error))
        error.__context__ = sdk_error
    assert controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime, client=object(), model="gpt-test")
    assert len(runtime.summary_requests) == 1
    assert all(record.reason == "threshold" for record in state.compaction_state.records)
    assert controller.passes_this_turn(1) == 0
    state.add_message({"role": "assistant", "content": "Recovered."})
    assert _finish_pending_recovery(controller, state, runtime)
    state.add_message({"role": "assistant", "content": "Finally recovered."})
    assert not _finish_pending_recovery(controller, state, runtime)
    assert len(runtime.summary_requests) == 2


@pytest.mark.parametrize("value", [None, False, 42, "os:system",
    "breadboard_engine.compaction..opencode:is_context_overflow",
    "breadboard_engine.compaction.opencode:missing",
    "breadboard_engine.compaction.opencode:_PATTERNS",
])
def test_overflow_error_classifier_is_strictly_admitted(value):
    from breadboard_engine.compaction.params import PresetError
    from breadboard_engine.compaction.presets import build_recipe, load_preset_document
    controller, _, _ = _pending_recovery_setup()
    document = load_preset_document("oh-my-opencode@3.10.0")
    document["overflow"]["error_classifier"] = value
    with pytest.raises(PresetError):
        build_recipe(controller.config, document)


def test_absent_overflow_error_classifier_preserves_generic_default():
    from breadboard_engine.compaction.presets import build_recipe, load_preset_document
    controller, _, _ = _pending_recovery_setup()
    document = load_preset_document("oh-my-opencode@3.10.0")
    del document["overflow"]["error_classifier"]
    assert build_recipe(controller.config, document).error_classifier is None


def test_plugin_only_scenario_uses_exact_error_and_production_attempt_budget():
    settings = production_settings("oh-my-opencode")
    turns = scenario_turns("plugin_error", settings)
    responses = turns[0]["responses"]
    assert responses[0] == {"type": "error", "status_code": 400, "error": "invalid_request_error",
                            "message": "too many tokens: 74000 tokens > 64000 maximum"}
    assert len(responses) == 1 + 2 * settings["deferred_recovery_attempts"]
    assert len(turns) == 1


@pytest.mark.parametrize("part,summary,reject", [
    ("text", False, False), ("compaction", False, True), ("text", True, True),
])
def test_stock_oracle_selection_uses_native_control_boundaries_only(part, summary, reject):
    from scripts.compaction_lanes.opencode_lane import stock_control_flow_ties
    evidence = {"session": {"id": "root"}, "messages": [
        {"info": {"id": "user", "role": "user", "time": {"created": 100}}, "parts": [{"type": part}]},
        {"info": {"id": "assistant", "role": "assistant", "summary": summary, "time": {"created": 100}},
         "parts": [{"type": "text", "text": "Native content"}]},
    ]}
    assert bool(stock_control_flow_ties(evidence)) is reject
    evidence["messages"][1]["info"]["time"]["created"] = 101
    assert stock_control_flow_ties(evidence) == []


def test_stock_oracle_all_five_native_ties_fail_without_running_bb(monkeypatch, tmp_path):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0,
                "native_evidence": {"session": {"id": "root"}, "messages": [
                    {"info": {"id": "u", "role": "user", "time": {"created": 100}},
                     "parts": [{"type": "compaction"}]},
                    {"info": {"id": "a", "role": "assistant", "summary": True,
                              "finish": "stop", "time": {"created": 100}}, "parts": []},
                ]}}, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock"] * 5
    assert report["stock_oracle_selection"]["selected_attempt"] is None
    assert len(report["stock_oracle_selection"]["attempts"]) == 5
    assert not report["passed"]


def test_stock_oracle_never_retries_a_tie_free_byte_difference(monkeypatch, tmp_path):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        evidence = {"session": {"id": "root"}, "messages": [
            {"info": {"id": "u", "role": "user", "time": {"created": 100}}, "parts": []},
            {"info": {"id": "a", "role": "assistant", "summary": True, "finish": "stop",
                      "time": {"created": 101}}, "parts": []}]}
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0, "native_evidence": evidence}, [
                    record({"model": "stock" if side == "stock" else "different", "input": []})]
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock", "bb"]
    assert report["stock_oracle_selection"]["selected_attempt"] == 1
    assert len(report["stock_oracle_selection"]["attempts"]) == 1
    assert len(report["diffs"]) == 1
    assert report["admitted_created_time_tie_groups_per_request"] == [
        {"request": 0, "tie_groups": 0}]
    assert not report["passed"]


@pytest.mark.parametrize("outcome", ["match", "bytes", "counts", "error", "ordinary_tie"])
def test_bb_sidecar_selector_rejects_only_native_control_ties(monkeypatch, tmp_path, outcome):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        bb_attempt = called.count("bb")
        control_tied = side == "bb" and bb_attempt == 1
        ordinary_tied = side == "bb" and bb_attempt == 2 and outcome == "ordinary_tie"
        evidence = {"session": {"id": "root"}, "messages": [
            {"info": {"id": "u", "role": "user", "time": {"created": 100}},
             "parts": [{"type": "compaction" if control_tied else "text"}]},
            {"info": {"id": "a", "role": "assistant", "summary": not ordinary_tied,
                      "finish": "stop", "time": {"created": 100 if control_tied or ordinary_tied else 101}},
             "parts": []},
        ]}
        result = {"session_id": "root", "unconsumed_responses": 0,
                  "unconsumed_auxiliary_responses": 0, "native_evidence": evidence}
        if side == "bb" and bb_attempt == 2 and outcome == "error":
            result["error"] = "Original tie-free production failure"
        rows = [record({"model": "different" if side == "bb" and bb_attempt == 2
                        and outcome in {"bytes", "ordinary_tie"} else "same", "input": []})]
        if side == "bb" and bb_attempt == 2 and outcome == "counts":
            rows.append(rows[0])
        return result, rows
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock", "bb", "bb"]
    selection = report["bb_sidecar_oracle_selection"]
    assert selection["selected_attempt"] == 2
    assert selection["attempts"][0]["control_flow_ties"]
    assert selection["attempts"][1]["control_flow_ties"] == []
    assert report["passed"] is (outcome == "match")
    assert report["stock_oracle_selection"]["bb_runs"] == 2
    assert selection["bytes_or_counts_can_reject"] is False


def test_bb_sidecar_all_five_native_ties_fail(monkeypatch, tmp_path):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0,
                "native_evidence": {"session": {"id": "root"}, "messages": [
                    {"info": {"id": "u", "role": "user", "time": {"created": 100}},
                     "parts": [{"type": "compaction"}]},
                    {"info": {"id": "a", "role": "assistant", "summary": True,
                              "finish": "stop", "time": {"created": 100 if side == "bb" else 101}},
                     "parts": []},
                ]}}, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock"] + ["bb"] * 5
    selection = report["bb_sidecar_oracle_selection"]
    assert selection["selected_attempt"] is None
    assert len(selection["attempts"]) == 5
    assert all(attempt["control_flow_ties"] for attempt in selection["attempts"])
    assert not report["passed"]
    assert any("All five BB sidecar" in item.get("error", "") for item in report["unresolved"])


def test_bb_sidecar_missing_evidence_never_retries(monkeypatch, tmp_path):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        result = {"session_id": "root", "unconsumed_responses": 0,
                  "unconsumed_auxiliary_responses": 0}
        if side == "stock":
            result["native_evidence"] = {"session": {"id": "root"}, "messages": [
                {"info": {"id": "a", "role": "assistant", "summary": True,
                          "finish": "stop", "time": {"created": 101}}, "parts": []}]}
        return result, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock", "bb"]
    selection = report["bb_sidecar_oracle_selection"]
    assert selection["selected_attempt"] is None
    assert len(selection["attempts"]) == 1
    assert not report["passed"]
    assert any("BB sidecar oracle omitted" in item.get("error", "") for item in report["unresolved"])


def test_bb_oracle_archives_only_the_closed_owned_session_store(tmp_path):
    from pathlib import Path
    from breadboard.product.runtime.session_store import session_directory
    from scripts.compaction_lanes.opencode_lane import archive_bb_session
    workspace, out = tmp_path / "workspace", tmp_path / "attempt"
    owned = session_directory(workspace) / "root" / "stock-opencode"
    owned.mkdir(parents=True)
    out.mkdir()
    (owned / "opencode.db").write_bytes(b"retained-native-evidence")
    unrelated = session_directory(workspace) / "other"
    unrelated.mkdir()
    (unrelated / "user-file").write_text("Untouched")
    archived = archive_bb_session(workspace, out, "root")
    assert Path(archived, "stock-opencode", "opencode.db").read_bytes() == b"retained-native-evidence"
    assert not owned.parent.exists()
    assert (unrelated / "user-file").read_text() == "Untouched"
    assert archive_bb_session(workspace, out, "absent") is None
    with pytest.raises(ValueError, match="portable"):
        archive_bb_session(workspace, out, "../other")


@pytest.mark.parametrize("observation_fails", [False, True])
def test_foreground_timeout_keeps_original_failure_and_reads_live_stock_evidence(
    observation_fails, monkeypatch, tmp_path,
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from scripts.compaction_lanes import opencode_lane as lane
    live = {"active": False, "closed": False}
    calls = []
    def api(method, path, body=None):
        assert live["active"]
        calls.append((method, path))
        if method == "POST":
            raise TimeoutError("original foreground failure")
        if observation_fails:
            raise OSError("original source observation failure")
        return {
            "/session/root/children": [],
            "/session/root": {"id": "root"},
            "/session/root/message": [],
            "/session/status": {"root": {"type": "retry"}},
        }[path]
    @contextmanager
    def server(*args, **kwargs):
        live["active"] = True
        try:
            yield api
        finally:
            live["active"] = False
            live["closed"] = True
    def imported(command, **kwargs):
        assert command[1] == "import"
        return SimpleNamespace(returncode=0, stdout="Imported session: root", stderr="")
    monkeypatch.setattr(lane, "stock_server", server)
    monkeypatch.setattr(lane, "resolve_stock", lambda: tmp_path / "pinned-stock")
    monkeypatch.setattr(lane.subprocess, "run", imported)
    result = lane.run_stock("opencode", "final_threshold", tmp_path, tmp_path / "runtime",
                            "http://unused", [{"info": {"id": "root"}, "messages": []}])
    assert "TimeoutError: original foreground failure" in result["error"]
    assert result["failure_kind"] == "foreground_post_timeout"
    assert calls[0] == ("POST", "/session/root/message")
    assert live == {"active": False, "closed": True}
    if observation_fails:
        assert result["native_evidence_error"] == "OSError: original source observation failure"
        assert "native_evidence" not in result
    else:
        assert result["native_evidence"] == {"session": {"id": "root"}, "messages": [], "children": []}
        assert result["native_status"] == {"type": "retry"}
        assert all(method == "GET" for method, _ in calls[1:])


@pytest.mark.parametrize("tied", [False, True])
def test_stock_timeout_attempt_retries_only_a_genuine_native_control_boundary_tie(
    tied, monkeypatch, tmp_path,
):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        first = not called
        called.append(side)
        evidence = {"session": {"id": "root"}, "messages": [
            {"info": {"id": "u", "role": "user", "time": {"created": 10}}, "parts": []},
            {"info": {"id": "a", "role": "assistant", "summary": True, "finish": "stop",
                      "time": {"created": 10 if first and tied else 11}}, "parts": []}]}
        result = {"session_id": "root", "native_evidence": evidence,
                  "unconsumed_responses": 0, "unconsumed_auxiliary_responses": 0}
        if first:
            result.update(error="TimeoutError: original foreground failure",
                          failure_kind="foreground_post_timeout")
        return result, [record({"input": [], "prompt_cache_key": "root"})]
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    attempts = report["stock_oracle_selection"]["attempts"]
    assert attempts[0]["error"] == "TimeoutError: original foreground failure"
    if tied:
        assert called == ["stock", "stock", "bb"]
        assert report["stock_oracle_selection"]["selected_attempt"] == 2
        assert len(attempts) == 2
        assert attempts[0]["control_flow_ties"]
        assert not attempts[1]["control_flow_ties"]
    else:
        assert called == ["stock"]
        assert report["stock_oracle_selection"]["selected_attempt"] == 1
        assert len(attempts) == 1
        assert not attempts[0]["control_flow_ties"]
        assert not report["passed"]
        assert any("original foreground failure" in item.get("error", "")
                   for item in report["unresolved"])


def test_original_provider_wording_cannot_be_replaced_by_generic_overflow_code():
    from breadboard_engine.compaction.oh_my_opencode import parse_token_limit_error
    from breadboard_engine.compaction.overflow import provider_overflow_details
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
    controller, state, runtime = _pending_recovery_setup()
    class SDKError(Exception):
        status_code = 400
        body = {"message": "requested tokens exceed n_ctx"}
    sdk_error = SDKError("requested tokens exceed n_ctx")
    details = provider_overflow_details(sdk_error)
    assert details["code"] == "context_length_exceeded"
    error = ProviderRuntimeError("provider operation failed", details=details)
    error.__cause__ = sdk_error
    assert parse_token_limit_error(error) is None
    assert controller.recipe.error_classifier(error) is False
    assert not controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime, client=object(), model="gpt-test")
    assert runtime.summary_requests == []


def test_stock_oracle_missing_native_evidence_fails_without_retry_or_bb(monkeypatch, tmp_path):
    from scripts.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(lane, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0}, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock"]
    selection = report["stock_oracle_selection"]
    assert selection["selected_attempt"] is None
    assert selection["bb_runs"] == 0
    assert len(selection["attempts"]) == 1
    assert selection["attempts"][0]["error"] == "Stock oracle omitted genuine native message evidence"
    assert not report["passed"]


@pytest.mark.parametrize("payload,expected", [
    (b'{"error":{"message":"too many tokens: 74000 tokens > 64000 maximum\xff"}}',
     {"overflow_tokens": 74000, "overflow_limit": 64000}),
    (b'{"error":{"message":"unrelated provider failure\xff"}}', None),
])
def test_plugin_error_response_body_uses_stock_utf8_replacement(payload, expected):
    from types import SimpleNamespace
    from breadboard_engine.compaction.oh_my_opencode import parse_token_limit_error
    assert bytes([255]) in payload
    error = RuntimeError("Public provider operation failed")
    error.response = SimpleNamespace(content=payload)
    assert parse_token_limit_error(error) == expected


def test_delegate_idle_scripts_two_autonomous_compactions_before_real_followup():
    settings = production_settings("oh-my-opencode")
    turns = scenario_turns("delegate_idle", settings)
    assert len(turns) == 2
    assert "autonomous_responses" not in turns[1]
    assert turns[0]["responses"][0]["tool_calls"][0]["name"] == "task"
    assert json.loads(turns[0]["responses"][0]["tool_calls"][0]["arguments"])["run_in_background"] is True
    autonomous = turns[0]["autonomous_responses"]
    assert sum(response.get("usage", {}).get("total_tokens", 0) > 44000
               for response in autonomous) == 2
    assert [response["text"] for response in autonomous if response["type"] == "text"] == [
        "Autonomous summary one: delegated inspection complete.",
        "Autonomous summary two: delegated inspection complete.",
        "Autonomous inspection complete."]
    assert turns[1]["responses"][-1]["text"] == "The delegated inspection is complete."


def test_delegate_idle_releases_real_child_completion_without_consuming_root_fifo():
    from concurrent.futures import ThreadPoolExecutor
    import urllib.request
    from scripts.compaction_lanes.opencode_lane import ResponsesProvider, auxiliary_script, text_response
    with ResponsesProvider(script=[responses_sse(text_response("Root reply"), 0)]) as provider:
        provider.engine.set_child_script("root-session", auxiliary_script("delegate_idle"))
        def child_request():
            request = urllib.request.Request(provider.base_url + "/v1/responses", method="POST",
                data=json.dumps({"model": "m", "prompt_cache_key": "native-child-session",
                                 "input": [], "stream": True}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.read()
        with ThreadPoolExecutor(max_workers=1) as executor:
            child = executor.submit(child_request)
            try:
                assert provider.engine.delegated_arrived.wait(5)
                assert not child.done()
                assert not provider.engine.delegated_finished.is_set()
                with urllib.request.urlopen(provider.base_url + "/v1/lane/release-child") as response:
                    assert json.load(response)["remaining"] == 1
                assert b"Delegated fixture inspection complete." in child.result(timeout=5)
                assert provider.engine.delegated_finished.wait(5)
                assert len(provider.engine.script) == 1
                assert provider.engine.child_script == []
                assert [row["path"] for row in provider.engine.recorded_requests] == ["/v1/responses"]
            finally:
                provider.engine.delegated_release.set()


def _add_completed_notification(evidence, background):
    # Pinned manager.ts:1255-1287 plus internal-initiator-marker.ts:1-10.
    root = evidence["session"]["id"]
    child = next(child for child in evidence["children"]
                 if any(background in part.get("state", {}).get("output", "")
                        and part.get("state", {}).get("metadata", {}).get("sessionId") == child["session"]["id"]
                        for message in evidence["messages"] for part in message["parts"]))
    child["messages"] = [{"info": {"id": "completed-" + background, "role": "assistant",
                                   "sessionID": child["session"]["id"], "finish": "stop",
                                   "time": {"created": 10, "completed": 11}}, "parts": []}]
    text = ("<system-reminder>\n[ALL BACKGROUND TASKS COMPLETE]\n\n**Completed:**\n"
            f"- `{background}`: Delegated inspection\n\n"
            'Use `background_output(task_id="<id>")` to retrieve each result.\n'
            "</system-reminder>\n<!-- OMO_INTERNAL_INITIATOR -->")
    evidence["messages"].append({
        "info": {"id": "notification-" + background, "role": "user", "sessionID": root},
        "parts": [{"type": "text", "text": text, "sessionID": root,
                   "messageID": "notification-" + background}]})
    return {"prompt_cache_key": root,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}


def _completed_notification_guard_fixture():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    stock = _runtime_id_api_fixture("bg_012345ab", "ses_" + "a" * 12 + "B" * 14)
    bb = _runtime_id_api_fixture("bg_abcdef01", "ses_" + "b" * 12 + "C" * 14)
    bodies = {"stock": _add_completed_notification(stock, "bg_012345ab"),
              "bb": _add_completed_notification(bb, "bg_abcdef01")}
    return RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]]), stock, bb, bodies


def test_completed_notification_ids_require_native_user_task_and_child_completion():
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    original = canonical(bodies)
    assert canonical(guard.body(bodies["stock"], "stock")) == canonical(guard.body(bodies["bb"], "bb"))
    assert canonical(bodies) == original
    assert guard.counts == {"stock": {"notification.completed_list": 1},
                            "bb": {"notification.completed_list": 1}}
    proof = next(iter(guard.completed_notifications["stock"].values()))
    assert proof["tasks"][0]["call_id"] == "call_delegated"
    assert proof["tasks"][0]["child_completion_ids"] == ["completed-bg_012345ab"]


def test_completed_notification_rejects_unproven_id():
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    body = json.loads(json.dumps(bodies["stock"]))
    body["input"][0]["content"][0]["text"] = body["input"][0]["content"][0]["text"].replace(
        "bg_012345ab", "bg_99999999")
    with pytest.raises(ValueError, match="native user provenance"):
        guard.body(body, "stock")


@pytest.mark.parametrize("owner,error", [
    ("child", "outside an approved position"),
    ("missing", "outside an approved position"),
    ("wrong", "Unmapped runtime ID"),
])
def test_completed_notification_on_non_root_request_is_not_substituted(owner, error):
    # Stock injects the notification only into the parent session (manager.ts:1359-1367).
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    other = {"stock": bb, "bb": stock}
    owned = {}
    for side, evidence in (("stock", stock), ("bb", bb)):
        owned[side] = {key: value for key, value in bodies[side].items() if key != "prompt_cache_key"}
        if owner == "child":
            owned[side]["prompt_cache_key"] = evidence["children"][0]["session"]["id"]
        elif owner == "wrong":
            owned[side]["prompt_cache_key"] = other[side]["children"][0]["session"]["id"]
        with pytest.raises(ValueError, match=error):
            guard.body(owned[side], side)
    assert "notification.completed_list" not in guard.counts["stock"]
    assert "notification.completed_list" not in guard.counts["bb"]
    # The lane falls back to the raw records on a guard error, so the difference stays strict.
    assert compare_records([record(owned["stock"])], [record(owned["bb"])])


@pytest.mark.parametrize("position", ["assistant", "developer", "instructions", "tool_output"])
def test_completed_notification_rejects_wrong_wire_position(position):
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    body = json.loads(json.dumps(bodies["stock"]))
    if position in {"assistant", "developer"}:
        body["input"][0]["role"] = position
    elif position == "instructions":
        body = {"instructions": body["input"][0]["content"][0]["text"], "input": []}
    else:
        body = {"input": [{"type": "function_call_output", "call_id": "other-call",
                           "output": body["input"][0]["content"][0]["text"]}]}
    with pytest.raises(ValueError, match="outside an approved position"):
        guard.body(body, "stock")


def test_completed_notification_requires_actual_child_completion():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    bb["children"][0]["messages"][0]["info"].pop("finish")
    with pytest.raises(ValueError, match="child completion evidence"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])


def test_completed_notification_mapping_mismatch_remains_a_request_difference():
    from scripts.compaction_lanes.opencode_lane import RuntimeGeneratedIDs
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    for evidence, background, child_id in (
        (stock, "bg_22222222", "ses_" + "c" * 12 + "D" * 14),
        (bb, "bg_33333333", "ses_" + "d" * 12 + "E" * 14),
    ):
        second = _runtime_id_api_fixture(background, child_id)
        part = second["messages"][0]["parts"][0]
        part["callID"] = "call_second"
        evidence["messages"][0]["parts"].append(part)
        evidence["children"].extend(second["children"])
        second_body = _add_completed_notification(evidence, background)
        if evidence is bb:
            bodies["bb"] = second_body
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    assert canonical(guard.body(bodies["stock"], "stock")) != canonical(guard.body(bodies["bb"], "bb"))


@pytest.mark.parametrize("harness", PROFILES)
def test_episode_profile_materialization_preserves_production_settings_and_declared_overrides(harness, tmp_path):
    import hashlib
    from breadboard_engine.compilation.v2_loader import load_agent_config
    from scripts.compaction_lanes.opencode_lane import materialize_episode_profile
    settings = production_settings(harness)
    source = load_agent_config(settings["profile"])
    overrides = {"compaction.contextWindow": TEST_WINDOW,
                 "providers.models[0].max_input_tokens": TEST_WINDOW,
                 "enhanced_tools.lsp_integration.enabled": False}
    path = tmp_path / "episode.json"
    artifact = materialize_episode_profile(settings, overrides, path)
    materialized = load_agent_config(str(path))
    assert materialized["compaction"]["contextWindow"] == TEST_WINDOW
    assert materialized["compaction"]["preset"] == source["compaction"]["preset"]
    assert materialized["providers"]["models"][0]["max_input_tokens"] == TEST_WINDOW
    assert materialized["providers"]["models"][0]["max_output_tokens"] == source["providers"]["models"][0]["max_output_tokens"]
    assert materialized["providers"]["models"][0]["params"] == source["providers"]["models"][0]["params"]
    assert materialized["tools"] == source["tools"]
    assert materialized["prompts"] == source["prompts"]
    assert artifact["source_profile_sha256"] == settings["profile_sha256"]
    assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact["override_keys"] == list(overrides)


@pytest.mark.parametrize("observation_write_fails", [False, True])
def test_queued_driver_preserves_observations_without_swallowing_admission_failure(
    monkeypatch, tmp_path, observation_write_fails,
):
    import asyncio
    from pathlib import Path
    from breadboard_engine.api.cli_bridge.service import SessionService
    from scripts.compaction_lanes.opencode_lane import run_bb_queued_session
    failure = RuntimeError("Observed admission failure")
    async def fail_admission(self, *args, **kwargs):
        raise failure
    shutdown_calls = []
    original_shutdown = SessionService.shutdown_runtime_owners
    async def shutdown(self):
        shutdown_calls.append(True)
        await original_shutdown(self)
    monkeypatch.setattr(SessionService, "shutdown_runtime_owners", shutdown)
    if observation_write_fails:
        original_write = Path.write_text
        def fail_observation_write(path, *args, **kwargs):
            if path.name == "bb_queued_observation.json":
                raise OSError("Observed output write failure")
            return original_write(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", fail_observation_write)
    monkeypatch.setattr(SessionService, "create_session", fail_admission)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(run_bb_queued_session({}, tmp_path, "http://127.0.0.1:1",
                                          "root", {"path": str(tmp_path / "episode.json")},
                                          "delegate_idle"))
    assert raised.value is failure
    assert shutdown_calls == [True]
    if observation_write_fails:
        assert raised.value.__notes__ == [
            "Queued observation failed: OSError: Observed output write failure",
        ]
    else:
        assert json.loads((tmp_path / "bb_queued_observation.json").read_bytes()) == {
            "session_id": "root", "events": [], "turns": [], "idle_checkpoints": [],
            "native_prune_fences": [],
        }
