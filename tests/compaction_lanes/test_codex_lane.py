"""Byte-strict comparator and real pinned stock/production engine scenarios."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import httpx

from scripts.compaction_lanes.codex_lane import (
    BINARY, EngineMockProvider, PROMPT, SCENARIOS, compare_requests,
    compaction_count, outcomes_valid, run_lane, scenario_script, settings,
)


def record(body: str) -> dict:
    return {"path": "/v1/responses", "raw_body_bytes": body.encode().hex(), "json": json.loads(body)}


def test_comparator_ignores_only_json_object_member_order():
    stock = record('{"model":"gpt-5.5","input":[]}')
    bb = record('{"input":[],"model":"gpt-5.5"}')
    assert compare_requests([stock], [bb]) == []
    assert stock["raw_body_bytes"] != bb["raw_body_bytes"]
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
    assert scenario_script(SCENARIOS[1], limit)[0]["name"] == "shell_command"
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
    assert report["deviations_used"] == []
    assert report["passed"], str(tmp_path / "lane_report.json")
