"""OpenCode lane  behavioral regressions."""
from __future__ import annotations
from breadboard_engine.compaction_lanes import opencode_stock as _lane_opencode_stock
from breadboard_engine.compaction_lanes import opencode_stock as _lane_opencode_stock
import json
import os
import pytest
from breadboard_engine.compaction_lanes.opencode_scenarios import PROFILES, SCENARIOS, TEST_WINDOW, production_settings, responses_sse, scenario_turns
from breadboard_engine.compaction_lanes.opencode_comparison import canonical, compare_records
from breadboard_engine.compaction_lanes.opencode_lane import run_scenario
from .opencode_test_support import _native_prune_event_fence, _pruned_part_event, record, summary_record

def test_comparator_rejects_undeclared_json_member_order():
    stock = record({"model": "m", "input": [{"role": "user", "content": "same"}]})
    bb = record({"input": [{"content": "same", "role": "user"}], "model": "m"})
    assert stock["raw_body_bytes"] != bb["raw_body_bytes"]
    differences = compare_records([stock], [bb])
    assert differences[0]["index"] == 0
    assert differences[0]["stock_raw_sha256"] != differences[0]["bb_raw_sha256"]

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
    from breadboard_engine.compaction_lanes.opencode_scenarios import SOURCE_API_PASSWORD, SOURCE_API_USERNAME
    from breadboard_engine.compaction_lanes.opencode_stock import stock_environment
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "operator-password")
    monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "operator-user")
    env = stock_environment({"lsp": False}, tmp_path)
    assert env["OPENCODE_SERVER_PASSWORD"] == SOURCE_API_PASSWORD != "operator-password"
    assert env["OPENCODE_SERVER_USERNAME"] == SOURCE_API_USERNAME != "operator-user"

def test_stock_api_authenticates_with_its_launched_server_credentials(monkeypatch, tmp_path):
    import base64
    import io
    from types import SimpleNamespace
    from breadboard_engine.compaction_lanes import opencode_lane as lane
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
    monkeypatch.setattr(_lane_opencode_stock, "resolve_stock", lambda: tmp_path / "opencode")
    monkeypatch.setattr(lane.urllib.request, "urlopen", open_request)
    with _lane_opencode_stock.stock_server(tmp_path, tmp_path / "runtime", {"lsp": False}) as api:
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

@pytest.mark.skipif(os.environ.get("BB_OPENCODE_LANE_INTEGRATION") != "1", reason="Pinned native OpenCode lane opt-in")
def test_terminal_answer_compaction_continues_before_zero_tool_watchdog(tmp_path, monkeypatch):
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider.routing import provider_router
    from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
    from breadboard_engine.compaction_lanes.opencode_comparison import is_summary
    from breadboard_engine.compaction_lanes.opencode_stock import create_empty_stock_sessions
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

def test_native_fixture_imports_are_never_production_profile_data():
    from pathlib import Path
    import yaml
    from breadboard_engine.compaction_lanes.opencode_scenarios import ROOT
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
    from breadboard_engine.compaction_lanes.opencode_mcp import MCPRelay
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

def test_background_mock_request_does_not_consume_root_response():
    from breadboard_engine.compaction_lanes.opencode_recording import SessionScriptEngine
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

def test_mock_script_progress_is_read_only_and_tracks_real_provider_responses():
    import urllib.request
    from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
    from breadboard_engine.compaction_lanes.opencode_scenarios import text_response
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
    from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
    from breadboard_engine.compaction_lanes.opencode_scenarios import text_response
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
    from breadboard_engine.compaction_lanes.opencode_recording import SessionScriptEngine
    engine = SessionScriptEngine(script=[{"type": "text", "text": "Root"}])
    engine.set_child_script("root-session", [{"type": "delegated_pending"}])
    body = {"prompt_cache_key": "native-child-session"}
    engine.record_request(method="POST", path="/v1/responses", headers={},
                          raw_body=json.dumps(body).encode(), parsed_json=body)
    assert engine.next_response() == {"type": "delegated_pending"}
    assert engine.child_script == []
    assert engine.script == [{"type": "text", "text": "Root"}]

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
    from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
    from breadboard_engine.compaction_lanes.opencode_lane import auxiliary_script
    from breadboard_engine.compaction_lanes.opencode_scenarios import text_response
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
