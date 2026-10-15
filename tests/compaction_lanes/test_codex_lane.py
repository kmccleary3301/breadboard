"""Byte-strict comparator and real pinned stock/production engine scenarios."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import httpx

from breadboard_engine.compaction_lanes.codex_lane import (
    BINARY, EngineMockProvider, METADATA_ORDER_DEVIATION, PROMPT, SCENARIOS, compare_requests,
    compaction_count, outcomes_valid, run_lane, scenario_script, settings,
)


def record(body: str) -> dict:
    return {"path": "/v1/responses", "raw_body_bytes": body.encode().hex(), "json": json.loads(body)}


def _metadata_wire(reverse: bool, *, generation: str = "0") -> str:
    metadata = {
        "x-codex-installation-id": "b637ab9d-566a-45d7-a71e-8726b668d5ca",
        "x-codex-window-id": f"01a12d82-4543-7961-9faa-26eaef0ba627:{generation}",
    }
    if reverse:
        metadata = dict(reversed(list(metadata.items())))
    return json.dumps({
        "model": "gpt-5.5",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "π"}]}],
        "client_metadata": metadata,
    }, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("generation", ["0", "1", "2", str((1 << 64) - 1)])
@pytest.mark.parametrize("reverse_stock", [False, True])
def test_declared_metadata_hashmap_order_records_exact_raw_evidence(reverse_stock, generation):
    stock = record(_metadata_wire(reverse_stock, generation=generation))
    bb = record(_metadata_wire(not reverse_stock, generation=generation))
    counts = []
    assert stock["raw_body_bytes"] != bb["raw_body_bytes"]
    assert compare_requests([stock, stock], [deepcopy(stock), bb], counts) == []
    assert counts[0]["raw_difference"] is None
    evidence = counts[1]
    assert evidence["accepted"] is True
    assert evidence["names"] == [METADATA_ORDER_DEVIATION]
    assert evidence["raw_difference"]["index"] == 1
    assert evidence["stock"][METADATA_ORDER_DEVIATION] == int(reverse_stock)
    assert evidence["bb"][METADATA_ORDER_DEVIATION] == int(not reverse_stock)
    permutation = evidence["metadata_order"]
    assert permutation["request"] == 1
    assert permutation["path"] == "$/client_metadata"
    assert permutation["source_commit"] == "a7dff904308535e965aee87680c1fc5ef1d19eec"
    assert permutation["projected_member_order"] == [
        "x-codex-installation-id", "x-codex-window-id",
    ]
    for side, item in (("stock", stock), ("bb", bb)):
        raw = bytes.fromhex(item["raw_body_bytes"])
        start, end = permutation[side]["byte_span"]
        assert json.loads(raw[start:end]) == item["json"]["client_metadata"]
        assert permutation[side]["original_sha256"] == hashlib.sha256(raw[start:end]).hexdigest()
        assert permutation[side]["raw_sha256"] == hashlib.sha256(raw).hexdigest()
        assert permutation[side]["original_member_order"] == list(item["json"]["client_metadata"])
    assert permutation["stock"]["projected_sha256"] == permutation["bb"]["projected_sha256"]


@pytest.mark.parametrize("side", ["stock", "bb"])
@pytest.mark.parametrize("variant", [
    "metadata_whitespace", "escaped_key", "escaped_value", "extra_metadata",
    "duplicate_metadata", "changed_uuid", "non_uuid", "root_whitespace",
    "root_member_order", "nested_member_order", "changed_model",
])
def test_metadata_order_guard_does_not_admit_other_wire_changes(side, variant):
    stock, bb = record(_metadata_wire(False)), record(_metadata_wire(True))
    malformed = stock if side == "stock" else bb
    raw = bytes.fromhex(malformed["raw_body_bytes"]).decode()
    if variant == "metadata_whitespace":
        raw = raw.replace('"client_metadata":{', '"client_metadata":{ ')
    elif variant == "escaped_key":
        raw = raw.replace("x-codex-window-id", "x-codex-window\\u002did")
    elif variant == "escaped_value":
        raw = raw.replace("01a12d82", "\\u00301a12d82")
    elif variant == "extra_metadata":
        raw = raw[:-2] + ',"extra":"value"}}'
    elif variant == "duplicate_metadata":
        raw = raw[:-2] + ',"x-codex-window-id":"01a12d82-4543-7961-9faa-26eaef0ba627:0"}}'
    elif variant == "changed_uuid":
        raw = raw.replace("9faa-26eaef0ba627", "9faa-26eaef0ba628")
    elif variant == "non_uuid":
        raw = raw.replace("b637ab9d-566a-45d7-a71e-8726b668d5ca", "not-a-uuid")
    elif variant == "root_whitespace":
        raw = " " + raw
    elif variant == "root_member_order":
        raw = json.dumps(dict(reversed(list(json.loads(raw).items()))),
                         ensure_ascii=False, separators=(",", ":"))
    elif variant == "nested_member_order":
        body = json.loads(raw)
        body["input"][0] = dict(reversed(list(body["input"][0].items())))
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    elif variant == "changed_model":
        raw = raw.replace("gpt-5.5", "gpt-5.4")
    malformed.update(record(raw))
    counts = []
    differences = compare_requests([stock], [bb], counts)
    assert differences
    assert bytes.fromhex(stock["raw_body_bytes"]) != bytes.fromhex(bb["raw_body_bytes"])
    assert not counts or counts[0]["accepted"] is False


@pytest.mark.parametrize("window", [
    "01a12d82-4543-7961-9faa-26eaef0ba627",
    "01a12d82-4543-7961-9faa-26eaef0ba627:",
    "01a12d82-4543-7961-9faa-26eaef0ba627:00",
    "01a12d82-4543-7961-9faa-26eaef0ba627:-1",
    "01a12d82-4543-7961-9faa-26eaef0ba627:1.0",
    "01a12d82-4543-7961-9faa-26eaef0ba627:18446744073709551616",
    "01a12d82-4543-7961-9faa-26eaef0ba627:0:1",
    "01a12d82-4543-4961-9faa-26eaef0ba627:0",
    "01A12D82-4543-7961-9FAA-26EAEF0BA627:0",
])
def test_metadata_order_guard_rejects_equal_invalid_source_window_ids(window):
    original = "01a12d82-4543-7961-9faa-26eaef0ba627:0"
    stock = record(_metadata_wire(False).replace(original, window))
    bb = record(_metadata_wire(True).replace(original, window))
    assert stock["json"] == bb["json"]
    counts = []
    assert compare_requests([stock], [bb], counts)
    assert counts[0]["names"] == []
    assert counts[0]["accepted"] is False


def test_comparator_rejects_undeclared_json_member_order():
    stock = record('{"model":"gpt-5.5","input":[]}')
    bb = record('{"input":[],"model":"gpt-5.5"}')
    differences = compare_requests([stock], [bb])
    assert differences[0]["stock_raw_sha256"] != differences[0]["bb_raw_sha256"]
    assert stock["raw_body_bytes"] != bb["raw_body_bytes"]
    assert compare_requests([stock], [deepcopy(stock)]) == []


@pytest.mark.parametrize("stock_body,legacy_sdk_body", [
    (
        '{"model":"gpt-5.5","instructions":"native","input":[],"stream":true}',
        '{"model":"gpt-5.5","stream":true,"input":[],"instructions":"native"}',
    ),
    (
        '{"input":[{"type":"message","role":"user","content":[]}]}',
        '{"input":[{"role":"user","content":[],"type":"message"}]}',
    ),
    (
        '{"input":[{"type":"function_call","name":"read","arguments":"{}","call_id":"c"}]}',
        '{"input":[{"type":"function_call","call_id":"c","name":"read","arguments":"{}"}]}',
    ),
])
def test_source_and_legacy_sdk_ordering_remains_a_raw_failure(stock_body, legacy_sdk_body):
    stock = record(stock_body)
    legacy_sdk = record(legacy_sdk_body)
    assert stock["json"] == legacy_sdk["json"]
    differences = compare_requests([stock], [legacy_sdk])
    assert len(differences) == 1
    difference = differences[0]
    assert difference["kind"] == "request_bytes"
    assert difference["request"] == 0
    assert difference["fields"] == []
    assert difference["stock_raw_sha256"] != difference["bb_raw_sha256"]
    assert compare_requests([stock], [deepcopy(stock)]) == []


@pytest.mark.parametrize("a,b", [('{"input":["a","b"]}', '{"input":["b","a"]}'),
                               ('{"input":"a b"}', '{"input":"a  b"}')])
def test_comparator_keeps_array_order_and_string_whitespace(a, b):
    assert compare_requests([record(a)], [record(b)])


@pytest.mark.parametrize("field,value", [("max_output_tokens", 32000), ("tools", []), ("stream", False)])
def test_no_unrecorded_deviation_is_removed(field, value):
    stock = record('{"model":"gpt-5.5","input":[]}')
    body = {**stock["json"], field: value}
    bb = record(json.dumps(body, separators=(",", ":")))
    assert compare_requests([stock], [bb])[0]["fields"][0]["path"] == "$." + field


def test_missing_requests_fail():
    request = record('{"input":[]}')
    assert compare_requests([request], [])[0]["kind"] == "missing_request"
    assert compare_requests([], [request])[0]["bb_present"]


@pytest.mark.parametrize("side", ["stock", "bb"])
def test_exec_chunk_guard_rejects_a_malformed_side(side):
    body = {"input": [
        {"type": "function_call", "name": "exec_command", "call_id": "c", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c",
         "output": "Chunk ID: 05a38f\nWall time: 0.1234 seconds\nProcess exited with code 0\nOutput:\nready\n"},
    ]}
    stock, bb = record(json.dumps(body)), record(json.dumps(body))
    malformed = stock if side == "stock" else bb
    malformed["json"]["input"][1]["output"] = malformed["json"]["input"][1]["output"].replace("05a38f", "05A38F")
    malformed.update(record(json.dumps(malformed["json"])))
    failures = compare_requests([stock], [bb])
    assert failures[0]["kind"] == "deviation_guard"
    assert side in failures[0]["message"]
    assert "codex_exec_chunk_id" in failures[0]["message"]


@pytest.mark.parametrize("side", ["stock", "bb"])
@pytest.mark.parametrize("wall", ["0.1", "-0.1234", "NaN", "01.0000"])
def test_exec_wall_guard_rejects_a_malformed_side(side, wall):
    body = {"input": [
        {"type": "function_call", "name": "write_stdin", "call_id": "c", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c",
         "output": "Chunk ID: 05a38f\nWall time: 0.1234 seconds\nProcess exited with code 0\nOutput:\nready\n"},
    ]}
    stock, bb = record(json.dumps(body)), record(json.dumps(body))
    malformed = stock if side == "stock" else bb
    malformed["json"]["input"][1]["output"] = malformed["json"]["input"][1]["output"].replace("0.1234", wall)
    malformed.update(record(json.dumps(malformed["json"])))
    failures = compare_requests([stock], [bb])
    assert failures[0]["kind"] == "deviation_guard"
    assert side in failures[0]["message"]
    assert "codex_exec_wall_time" in failures[0]["message"]


def test_exec_metadata_guards_count_per_request_and_leave_body_strict():
    body = {"input": [
        {"type": "function_call", "name": "exec_command", "call_id": "c", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c",
         "output": "Chunk ID: 05a38f\nWall time: 0.1234 seconds\nProcess exited with code 0\nOutput:\nWall time: user text\n"},
    ]}
    stock = record(json.dumps(body))
    bb = deepcopy(stock)
    bb["json"]["input"][1]["output"] = bb["json"]["input"][1]["output"].replace("05a38f", "abcdef").replace("0.1234", "12.0000")
    bb.update(record(json.dumps(bb["json"])))
    counts = []
    assert compare_requests([stock], [bb], counts) == []
    assert counts[0]["stock"] == counts[0]["bb"] == {
        "codex_exec_chunk_id": 1, "codex_exec_wall_time": 1}
    assert counts[0]["raw_difference"]["stock_sha256"] != counts[0]["raw_difference"]["bb_sha256"]
    assert {change["path"] for change in counts[0]["changes"]["bb"]} == {"$/input/1/output"}
    bb["json"]["input"][1]["output"] += " "
    bb.update(record(json.dumps(bb["json"])))
    assert compare_requests([stock], [bb])
    body["input"][0]["name"] = "update_plan"
    stock = record(json.dumps(body))
    bb = deepcopy(stock)
    bb["json"]["input"][1]["output"] = bb["json"]["input"][1]["output"].replace("05a38f", "abcdef")
    bb.update(record(json.dumps(bb["json"])))
    assert compare_requests([stock], [bb])


def test_injected_process_ids_are_finite_and_allocation_mismatch_fails():
    from breadboard_engine.compaction_lanes.codex_lane import StockProcessIds, validate_process_allocations
    source = StockProcessIds([1000, 99999])
    assert source() == 1000
    assert source() == 99999
    with pytest.raises(ValueError, match="exhausted"):
        source()
    validate_process_allocations([1000, 99999], {"allocation_count": 2, "draw_count": 2})
    for state in ({"allocation_count": 1, "draw_count": 1}, {"allocation_count": 2, "draw_count": 3}):
        with pytest.raises(ValueError, match="allocation mismatch"):
            validate_process_allocations([1000, 99999], state)


def test_settings_derive_limit_from_production_preset():
    config = settings(16000)
    assert config["production_window"] == 272000
    assert config["limit"] == 14400
    assert config["preset"] == "codex@0.139.0"
    assert config["model"] == "gpt-5.5"


def test_script_covers_requested_lifecycles():
    limit = settings(16000)["limit"]
    script = scenario_script(SCENARIOS[0], limit)
    assert len(script) == 5
    assert script[0]["usage"]["total_tokens"] > limit
    assert script[2]["usage"]["total_tokens"] > limit
    mid = scenario_script(SCENARIOS[1], limit)
    assert [item.get("name") for item in mid[:2]] == ["exec_command", "write_stdin"]
    assert json.loads(mid[0]["arguments"])["tty"] is True
    assert mid[1]["session_id_from_output"] is True
    assert len(mid) == 4
    assert scenario_script(SCENARIOS[2], limit)[0]["error"] == "context_length_exceeded"


def test_compaction_count_inspects_prompt_not_empty_tools():
    summary = record(json.dumps({"input": [{"role": "user", "content": PROMPT}]}))
    assert compaction_count([summary, record('{"tools":[]}')]) == 1


@pytest.mark.parametrize("stream", [True, False])
def test_mock_serves_both_production_response_modes(tmp_path, stream):
    script = scenario_script(SCENARIOS[0], 14400)[:1]
    with EngineMockProvider(script=script, record_path=tmp_path / "requests.jsonl") as provider:
        response = httpx.post(provider.base_url + "/v1/responses", json={"model": "gpt-5.5", "stream": stream})
        assert response.status_code == 200
        if stream:
            assert "response.completed" in response.text
        else:
            body = response.json()
            assert body["output"][0]["content"][0]["text"] == "Turn 1 answer."
            assert body["usage"]["total_tokens"] == 14500
        assert len(provider.engine.recorded_requests) == 1
    assert provider._thread is None


def test_overflow_requires_both_terminal_outcomes():
    sides = {"stock": {"returncode": 0, "remaining_script": 0, "result": {"runs": [{"returncode": 1}]}},
             "bb": {"returncode": 0, "remaining_script": 0, "result": {"runs": [{"completion_summary": {"completed": False, "error": {"message": "provider_error"}}}]}}}
    assert outcomes_valid("overflow_terminal", sides)
    sides["stock"]["result"]["runs"][0]["returncode"] = 0
    assert not outcomes_valid("overflow_terminal", sides)
    sides["stock"]["result"]["runs"][0]["returncode"] = 1
    sides["bb"]["result"]["runs"][0]["completion_summary"]["completed"] = True
    assert not outcomes_valid("overflow_terminal", sides)


@pytest.mark.parametrize("summary,tools", [(False, None), (False, []), (True, None), (True, [])])
def test_responses_summary_projection_preserves_main_bytes(summary, tools):
    from types import SimpleNamespace
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(
        SimpleNamespace(get_provider_metadata=lambda key: None), {},
        extra={"compaction_summary": summary, "compaction_request_params": {"parallel_tool_calls": False, "tool_choice": "auto"}},
    )
    body = runtime.project_request_body(model="gpt-5.5", messages=[{"role": "user", "content": "task"}],
                                        tools=tools, stream=True, context=context)
    expected = {"model": "gpt-5.5", "input": [{"role": "user", "content": [{"type": "input_text", "text": "task"}]}],
                "include": ["reasoning.encrypted_content"], "stream": True}
    if summary:
        expected.update(parallel_tool_calls=False, tool_choice="auto")
        if tools is not None:
            expected["tools"] = []
    assert body == expected
    assert json.dumps(body, sort_keys=True, separators=(",", ":")) == json.dumps(expected, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("generation", [0, 1, 2])
@pytest.mark.parametrize("summary", [False, True])
def test_profile_envelope_derives_identity_and_keeps_null_fields(generation, summary):
    from types import SimpleNamespace
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.compaction.state import CompactionRecord, CompactionState
    from breadboard_engine.state.session_state import SessionState
    root = "019a0000-0000-7000-8000-000000000001"
    state = SessionState(".", "lane", {})
    state.set_provider_metadata("installation_id", "installation")
    state.compaction_state = CompactionState([
        CompactionRecord(f"record-{index}", index, "summary", "threshold", "2026-10-10", 100, 0,
                         first_kept_index=0)
        for index in range(generation)
    ])
    configured = {"provider_tools": {"openai": {
        "responses_stateful": False, "include_reasoning": False,
        "request_params": {"reasoning": None, "text": None, "include": [], "store": False,
                           "parallel_tool_calls": False, "tool_choice": "auto"},
        "session_params": {"prompt_cache_key": "{session_id}"},
        "client_metadata_session": {"x-codex-installation-id": "{installation_id}",
                                    "x-codex-window-id": "{session_id}:{compaction_boundary_count}"},
    }}}
    context = ProviderRuntimeContext(state, configured, session_id=root,
        extra={"compaction_summary": summary, "compaction_request_params": {"tool_choice": "none"}})
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    body = runtime.project_request_body(model="model", messages=[{"role": "user", "content": "task"}],
                                        tools=[] if summary else None, stream=True, context=context)
    assert body["prompt_cache_key"] == root
    assert body["client_metadata"] == {"x-codex-installation-id": "installation",
                                       "x-codex-window-id": f"{root}:{generation}"}
    assert body["reasoning"] is None and body["text"] is None
    assert body["include"] == [] and body["store"] is False
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == ("none" if summary else "auto")


def test_window_generation_rehydrates_resumed_compactions_and_starts_new_at_zero():
    from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, load_compaction_config
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.state.session_state import SessionState
    state = SessionState(".", "lane", {})
    state.compaction_state = CompactionState()
    root = "019a0000-0000-7000-8000-000000000001"
    context = ProviderRuntimeContext(state, {}, session_id=root, extra={})
    def window():
        return OpenAIResponsesRuntime._session_param("{session_id}:{compaction_boundary_count}", context)
    messages = [{"role": "user", "content": "task"}]
    settings = load_compaction_config({"enabled": True}).settings
    for count in range(2):
        assert window() == f"{root}:{count}"
        compaction = CompactionContext(messages, state.compaction_state, settings, "threshold",
                                      ProjectionTarget("openai", "responses", "gpt-5.5"), 16000, 100,
                                      clock=lambda: "2026-10-10")
        state.compaction_state.append(
            compaction.new_record(method="summary", summary="checkpoint", first_kept_index=0,
                                  summary_messages=[{"role": "user", "content": "checkpoint"}]), messages)
    assert window() == f"{root}:2"
    resumed = SessionState(".", "resumed", {})
    resumed.compaction_state = CompactionState.from_list(state.compaction_state.to_list())
    context.session_state = resumed
    assert window() == f"{root}:2"
    context.session_state = SessionState(".", "new", {})
    assert window() == f"{root}:0"


def test_configured_metadata_reaches_mock_through_real_sdk(tmp_path):
    from types import SimpleNamespace
    from openai import OpenAI
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(
        SimpleNamespace(get_provider_metadata=lambda key: None),
        {"provider_tools": {"openai": {"include_reasoning": False,
                                     "client_metadata": {"installation": "shared"}}}},
        extra={},
    )
    with EngineMockProvider(script=scenario_script(SCENARIOS[0], 14400)[:1],
                            record_path=tmp_path / "requests.jsonl") as provider:
        with OpenAI(api_key="lane", base_url=provider.base_url + "/v1") as client:
            runtime.invoke(client=client, model="gpt-5.5",
                           messages=[{"role": "user", "content": "task"}],
                           tools=None, stream=True, context=context)
        assert provider.engine.recorded_requests[0]["json"]["client_metadata"] == {"installation": "shared"}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("message,details,overflow", [
    ("provider error", {"code": "server_error"}, False),
    ("provider error", {"code": "context_length_exceeded", "status_code": 400}, True),
    ("token limit exceeded", {"code": "rate_limited", "status_code": 429}, False),
    ("token limit exceeded", {"classification": "rate_limited"}, False),
    ("provider error", {"code": "context_length_exceeded"}, True),
])
def test_provider_overflow_bypasses_transport_fallback(monkeypatch, stream, message, details, overflow):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from breadboard_engine.provider.invoker import ProviderInvoker
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext, ProviderRuntimeError
    from breadboard_engine.state.session_state import SessionState
    state = SessionState(workspace=".", image="lane", config={})
    for key, value in {"session_id": "lane", "input_id": "input", "turn_id": "turn"}.items():
        state.set_provider_metadata(key, value)
    context = ProviderRuntimeContext(state, {}, stream=stream)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    error = ProviderRuntimeError(message, kind="provider", details=details)
    # A previous overflow must not override this response's explicit rate limit.
    if details.get("status_code") == 429 or details.get("classification") == "rate_limited":
        error.__context__ = ProviderRuntimeError("context_length_exceeded", kind="provider")
    invoke = Mock(side_effect=error)
    monkeypatch.setattr(runtime, "invoke", invoke)
    fallback = Mock(side_effect=error)
    invoker = ProviderInvoker(provider_metrics=Mock(), route_health=Mock(is_circuit_open=Mock(return_value=False)),
                              logger_v2=SimpleNamespace(run_dir=None), md_writer=SimpleNamespace(system=lambda message: message),
                              retry_with_fallback=fallback, update_health_metadata=Mock(), set_last_latency=Mock(), set_html_detected=Mock())
    with pytest.raises(ProviderRuntimeError) as raised:
        invoker.invoke(runtime=runtime, client=object(), model="gpt-5.5", send_messages=[{"role": "user", "content": "task"}],
                       tools_schema=[], stream_responses=stream, runtime_context=context, session_state=state,
                       markdown_logger=Mock(), turn_index=1, route_id="openai/gpt-5.5")
    assert raised.value is error
    assert invoke.call_count == 1
    assert fallback.call_count == (0 if overflow else 1)
    assert context.extra["provider_exchange"]["terminal"]["retryable"] is (not overflow)


@pytest.mark.parametrize("status_source", ["status_code", "response"])
def test_sdk_rate_limit_precedes_phrase_and_cause(status_source):
    from types import SimpleNamespace
    from breadboard_engine.compaction.overflow import is_context_overflow
    error = RuntimeError("token limit exceeded")
    if status_source == "status_code":
        error.status_code = 429
    else:
        error.response = SimpleNamespace(status_code=429)
    error.__context__ = RuntimeError("context_length_exceeded")
    assert not is_context_overflow(error)


def test_other_http_status_still_allows_explicit_overflow():
    from breadboard_engine.compaction.overflow import is_context_overflow
    assert is_context_overflow({"code": "context_length_exceeded", "status_code": 500})


@pytest.mark.skipif(not BINARY.is_file(), reason="pinned Codex binary is unavailable")
def test_live_stock_and_production_engine_bytes(tmp_path: Path):
    report = run_lane(tmp_path)
    names = {item["name"] for item in report["deviations_used"]}
    assert {"codex_exec_chunk_id", "codex_exec_wall_time"} <= names
    assert names <= {"codex_exec_chunk_id", "codex_exec_wall_time", METADATA_ORDER_DEVIATION}
    assert report["passed"], str(tmp_path / "lane_report.json")
    # A fresh production entrypoint resumes the ledger, so the second summary
    # reads the first boundary rather than the original assistant history.
    recordings = tmp_path / "pre_turn_and_second_compaction" / "bb" / "requests.jsonl"
    bodies = [json.loads(line)["json"] for line in recordings.read_text().splitlines()]
    second_summary = json.dumps(bodies[3]["input"], ensure_ascii=False)
    assert "Summary of turn 1." in second_summary
    assert "Turn 1 answer." not in second_summary
    assert "Turn 3 prompt" not in second_summary


@pytest.mark.parametrize("consumer", ["select", "reduce", None])
def test_pending_exclusion_consumes_explicit_request_context(monkeypatch, consumer):
    from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget
    from breadboard_engine.compaction.controller import CompactionController, CompactionRequestContext
    from types import SimpleNamespace
    from breadboard_engine.compaction.methods import SummaryResponse
    from breadboard_engine.compaction import presets
    document = presets.load_preset_document("codex@0.139.0")
    stage = document["pipeline"]["stages"][0]
    stage["place"]["restore_context_placements"] = {}
    stage["select"]["exclude_pending_user_on"] = []
    stage["reduce"]["exclude_pending_user_on"] = []
    if consumer is not None:
        stage[consumer]["exclude_pending_user_on"] = ["pre_turn"]
    monkeypatch.setattr(presets, "load_preset_document", lambda _: document)
    controller = CompactionController({"compaction": {"enabled": True, "preset": "codex@0.139.0", "contextWindow": 16000}})
    messages = [{"role": "user", "content": "previous task"},
                {"role": "assistant", "content": "previous answer"},
                {"role": "user", "content": "pending prompt"}]
    calls = []
    class Summary:
        def complete(self, request):
            calls.append(request)
            return SummaryResponse("checkpoint")
    controller.summary_model = Summary()
    context = controller._context_for_pass(
        SimpleNamespace(provider_messages=messages, compaction_state=CompactionState()),
        reason="threshold", target=ProjectionTarget("openai", "responses", "gpt-5.5"),
        context_window=16000, request_context=CompactionRequestContext("pre_turn", len(messages) - 1))
    step = controller.recipe.pipeline.stages["summary"].step
    selection = step.selector.select(context)
    if consumer == "select":
        assert len(messages) - 1 not in selection.summarize
        assert messages[-1] not in selection.details["retained_messages"]
    step.reducer.reduce(context, selection)
    assert (messages[-1] not in calls[0].messages) is (consumer == "reduce")
