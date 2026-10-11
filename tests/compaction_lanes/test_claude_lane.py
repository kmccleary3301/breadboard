"""Strict comparison and real SDK/production-runner tests for the Claude lane."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard_engine.compaction_lanes.claude_lane import (
    ClaudeMockProvider, PRESET, STOCK_PACKAGE, compare_requests, is_summary,
    production_settings, run_scenario, scenario_responses,
)


def capture(body, *, raw=None, path="/v1/messages"):
    raw = raw if raw is not None else json.dumps(body, separators=(",", ":"))
    return {"method": "POST", "path": path, "json": body, "raw_body_bytes": raw.encode().hex()}


def test_comparator_rejects_undeclared_json_representation():
    left = capture({"model": "claude", "messages": [{"role": "user", "content": "é"}]})
    right = capture(left["json"], raw=json.dumps(left["json"], sort_keys=True, indent=2))
    assert left["raw_body_bytes"] != right["raw_body_bytes"]
    differences = compare_requests([left], [right])
    assert differences[0]["stock_raw_sha256"] != differences[0]["bb_raw_sha256"]


@pytest.mark.parametrize("changed", [
    {"model": "other", "messages": ["a", "b"], "temperature": None},
    {"model": "claude", "messages": ["b", "a"], "temperature": None},
    {"model": "claude", "messages": ["a ", "b"], "temperature": None},
    {"model": "claude", "messages": ["a", "b"]},
    {"model": "claude", "messages": ["a", "b"], "temperature": 0.1},
])
def test_comparator_rejects_unadmitted_value_array_whitespace_and_presence_changes(changed):
    original = {"model": "claude", "messages": ["a", "b"], "temperature": None}
    differences = compare_requests([capture(original)], [capture(changed)])
    assert len(differences) == 1
    assert differences[0]["differing_fields"]


def test_comparator_never_discards_extra_requests_or_endpoint_query():
    record = capture({"model": "claude"})
    assert compare_requests([record], [record, record])[0]["missing_side"] == "stock"
    assert compare_requests([record], [capture(record["json"], path="/v1/messages?beta=true")])


def test_dispatch_identifies_the_exact_summary_prompt_not_budgets_or_tool_absence():
    prefix = "Your task is to create a detailed summary of the conversation so far, paying close attention."
    assert is_summary({"messages": [{"role": "user", "content": prefix}]})
    assert is_summary({"messages": [{"role": "user", "content": [{"type": "text", "text": prefix}]}]})
    assert not is_summary({"messages": [{"role": "assistant", "content": prefix}]})
    assert not is_summary({"max_tokens": 20000, "messages": [{"role": "user", "content": "Continue."}]})


def test_scenarios_derive_window_and_budget_from_production_profile(tmp_path):
    settings = production_settings()
    assert settings["preset"] == PRESET
    script = scenario_responses("long_session", tmp_path, settings, 10)
    assert len(script["summary"]) == 2
    assert len(script["main"]) == 3
    start = json.loads(script["main"][0]["chunks"][0].split("data: ", 1)[1])
    assert start["message"]["usage"]["input_tokens"] == 22000
    overflow = scenario_responses("overflow", tmp_path, settings, 10)
    assert f'{settings["context_window"]} maximum' in overflow["main"][0]["message"]
    assert overflow["summary"] == []


@pytest.mark.parametrize("stream", [False, True])
def test_real_anthropic_sdk_preserves_legacy_sampling_members(tmp_path, stream):
    from breadboard_engine.provider.contracts import ProviderRuntimeContext
    from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime
    from breadboard_engine.state.session_state import SessionState
    import anthropic
    runtime = AnthropicMessagesRuntime(SimpleNamespace(provider_id="anthropic", runtime_id="anthropic_messages"))
    script = {"main": [{"type": "text", "id": "msg_test", "text": "Acknowledged.",
                        "usage": {"input_tokens": 100, "output_tokens": 10}}], "summary": []}
    context = ProviderRuntimeContext(session_state=SessionState("ws", "image", {}),
                                     agent_config={"provider_tools": {"anthropic": {"max_output_tokens": 1024, "temperature": 1}}},
                                     stream=stream)
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        with anthropic.Anthropic(api_key="bb-claude-lane", base_url=server.base_url) as client:
            result = runtime.invoke(client=client, model="claude-haiku-4-5-20251001",
                                    messages=[{"role": "user", "content": "Hello."}], tools=None, stream=stream, context=context)
        assert result.messages[0].content == "Acknowledged."
        from breadboard_engine.provider.contract_events import normalize_usage
        assert normalize_usage(result.usage)["inputTokens"] == 100
        assert len(server.engine.recorded_requests) == 1
        body = server.engine.recorded_requests[0]["json"]
        assert body["temperature"] == 1
        assert "extra_body" not in body


def test_summary_options_absent_preserves_original_invocation_and_recorder_body():
    from breadboard_engine.compaction.methods import SummaryRequest
    from breadboard_engine.compaction.summary_model import ConductorSummaryModel
    from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
    from breadboard_engine.state.session_state import SessionState
    invocations, recorded = [], []
    client = object()
    class Runtime:
        descriptor = SimpleNamespace(provider_id="openai", runtime_id="openai_chat")
        def invoke(self, **kwargs):
            invocations.append(kwargs)
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="checkpoint")], raw_response=None)
    class Recorder:
        def record_request(self, *args, **kwargs):
            recorded.append(kwargs)
    summary = ConductorSummaryModel(runtime=Runtime(), client=client, model="model",
                                    session_state=SessionState("ws", "image", {}), agent_config={}, recorder=Recorder())
    request = SummaryRequest("system", ({"role": "user", "content": "task"},), 20, "summary")
    assert summary.complete(request).text == "checkpoint"
    call = invocations[0]
    assert call["client"] is client and call["tools"] is None and call["stream"] is False
    assert call["messages"] == [{"role": "system", "content": "system"}, {"role": "user", "content": "task"}]
    assert call["context"].extra == {"turn_index": 0, "model": "model", "stream": False,
                                     "compaction_summary": True, "summary_purpose": "summary", "max_tokens": 20}
    assert json.dumps(recorded[0]["request_body"], separators=(",", ":")) == '{"messages":[{"role":"system","content":"system"},{"role":"user","content":"task"}],"max_tokens":20}'


def test_summary_rejects_unknown_episode_tool_names():
    from breadboard_engine.compaction.methods import SummaryRequest
    from breadboard_engine.compaction.summary_model import ConductorSummaryModel
    from breadboard_engine.state.session_state import SessionState
    summary = ConductorSummaryModel(runtime=None, client=None, model="model", session_state=SessionState("ws", "image", {}),
                                    agent_config={}, tool_schema_provider=lambda: [{"name": "Read", "input_schema": {"type": "object"}}])
    request = SummaryRequest(None, (), 20, "summary", tool_names=("read",), request_params={})
    with pytest.raises(ValueError, match="Unknown summary tool names.*read"):
        summary.complete(request)


def test_summary_uses_production_route_lease_and_selected_tool_schema():
    from contextlib import contextmanager
    from breadboard_engine.compaction.methods import SummaryRequest
    from breadboard_engine.compaction.summary_model import ConductorSummaryModel
    from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
    from breadboard_engine.state.session_state import SessionState
    calls = []
    client = object()
    schema = {"name": "Read", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}
    @contextmanager
    def lease(route, runtime):
        calls.append(("lease", route))
        yield client
        calls.append(("close", route))
    class Runtime:
        def invoke(self, **kwargs):
            calls.append(("invoke", kwargs))
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="checkpoint")], raw_response=None)
    summary = ConductorSummaryModel(runtime=Runtime(), client=None, model="native-model", route_id="anthropic/native-model",
                                    session_state=SessionState("ws", "image", {}), agent_config={}, client_lease=lease,
                                    tool_schema_provider=lambda: [schema, {"name": "Bash"}])
    request = SummaryRequest(None, (), 20, "summary", stream=True, tool_names=("Read",), request_params={"temperature": 1})
    assert summary.complete(request).text == "checkpoint"
    assert calls[0] == ("lease", "anthropic/native-model")
    assert calls[-1] == ("close", "anthropic/native-model")
    invoke = calls[1][1]
    assert invoke["client"] is client and invoke["stream"] is True and invoke["tools"] == [schema]
    assert invoke["context"].extra["compaction_request_params"] == {"temperature": 1}
    assert schema == invoke["tools"][0]


@pytest.mark.parametrize("result_text", [" \t record é\n", ""])
def test_anthropic_tool_turn_summary_and_next_request_round_trip(tmp_path, result_text):
    from contextlib import contextmanager
    import anthropic
    from breadboard_engine.compaction_lanes.claude_lane import signed_tool_response
    from breadboard_engine.conductor.execution_records import legacy_message_view
    from breadboard_engine.conductor.model_output import _assistant_history_message
    from breadboard_engine.provider.normalizer import normalized_result_messages
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.provider.adapters import provider_adapter_manager
    from breadboard_engine.provider.contracts import ProviderRequest, ProviderRuntimeContext
    from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime
    from breadboard_engine.state.session_state import SessionState

    model = "claude-haiku-4-5-20251001"
    runtime = AnthropicMessagesRuntime(SimpleNamespace(provider_id="anthropic", runtime_id="anthropic_messages", default_api_variant="messages"))
    config = {"compaction": {"enabled": True, "preset": PRESET, "contextWindow": 200000,
                             "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": 10},
              "provider_tools": {"anthropic": {"max_output_tokens": 32000, "thinking": {"type": "enabled", "budget_tokens": 31999}}}}
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "Inspect records.txt."})
    script = {
        "main": [signed_tool_response(
                     {"id": "msg_tool", "name": "Read", "call_id": "toolu_read",
                      "arguments": '{"file_path":"/records.txt"}', "usage": {"input_tokens": 22000, "output_tokens": 10}},
                     model, thinking=" \tInspect é\n", signature="opaque-signature-+/=", redacted_data="opaque-redacted-+/="),
                 {"type": "text", "id": "msg_final", "text": "Inspection complete.",
                  "usage": {"input_tokens": 100, "output_tokens": 10}}],
        "summary": [{"type": "text", "id": "msg_summary", "text": "<summary>Inspected records.txt.</summary>",
                     "usage": {"input_tokens": 100, "output_tokens": 10}}],
    }
    read = SimpleNamespace(name="Read", description="Read a file", parameters=[])
    tools = provider_adapter_manager.translate_tools_to_native_schema([read], "anthropic")
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        with anthropic.Anthropic(api_key="bb-claude-lane", base_url=server.base_url) as client:
            context = ProviderRuntimeContext(session_state=state, agent_config=config, stream=True)
            first = runtime.invoke(client=client, model=model, messages=state.provider_messages, tools=tools, stream=True, context=context)
            call = first.messages[0].tool_calls[0]
            history = _assistant_history_message(legacy_message_view(first.messages[0]), tool_calls=[
                {"type": "function", "id": call.id, "function": {"name": call.name, "arguments": call.arguments_json}},
            ])
            state.add_message(history)
            assert history["reasoning"] == first.reasoning_blocks
            assert len(normalized_result_messages(first)) == 1
            tool_message = provider_adapter_manager.get_adapter("anthropic").create_tool_result_message(
                call.id, call.name, {"__mvi_text_output": result_text},
            )
            # This is the same adapter result appended by model_output.relay_results.
            state.add_message(tool_message)
            canonical = ProviderRequest(stream=True, messages=state.provider_messages, tools=tools).messages
            assert canonical[-1]["role"] == "tool_result"
            assert canonical[-1]["content"][0]["call_id"] == "toolu_read"
            assert canonical[-1]["content"][0]["content"] == result_text
            expected_assistant = [
                {"type": "thinking", "thinking": " \tInspect é\n", "signature": "opaque-signature-+/="},
                {"type": "redacted_thinking", "data": "opaque-redacted-+/="},
                {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "/records.txt"}},
            ]
            # Both persisted history and canonical recorded history round-trip.
            for messages in (state.provider_messages, canonical):
                _, converted = runtime._convert_messages(messages, context=context)
                assert converted[1]["content"] == expected_assistant
            state.set_provider_metadata("usage", first.usage)
            state.set_provider_metadata("max_output_tokens", 32000)
            @contextmanager
            def lease(route, selected_runtime):
                assert route == f"anthropic/{model}" and selected_runtime is runtime
                yield client
            conductor = SimpleNamespace(config=config, current_native_tools=[read], _provider_client_lease=lease,
                                        _current_route_id=f"anthropic/{model}")
            controller = CompactionController(config)
            view = controller.prepare_request(state, conductor=conductor, runtime=runtime, client=None, model=model, turn_index=2)
            assert len(state.compaction_state.records) == 1
            ProviderRequest(stream=True, messages=view, tools=tools)
            second = runtime.invoke(client=client, model=model, messages=view, tools=tools, stream=True, context=context)
            assert second.messages[0].content == "Inspection complete."
        records = server.engine.recorded_requests
        assert len(records) == 3 and is_summary(records[1]["json"])
        results = [block for message in records[1]["json"]["messages"] for block in message["content"] if block["type"] == "tool_result"]
        assert results == [{"type": "tool_result", "tool_use_id": "toolu_read", "content": result_text}]
        calls = [block for message in records[1]["json"]["messages"] for block in message["content"] if block["type"] == "tool_use"]
        assert calls == [{"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "/records.txt"}}]
        assert records[1]["json"]["messages"][1]["content"] == expected_assistant
        assert records[1]["json"]["max_tokens"] == 20000
        assert records[1]["json"]["temperature"] == 1
        assert "thinking" not in records[1]["json"]
        assert [tool["name"] for tool in records[1]["json"]["tools"]] == ["Read"]
        assert len(records[2]["json"]["messages"]) == 1
        assert "Summary:\nInspected records.txt." in records[2]["json"]["messages"][0]["content"][0]["text"]
        assert state.provider_messages[-1] == tool_message


@pytest.mark.parametrize("profile_name", [
    "claude_code_2-1-63_e4_9-10-2026.yaml",
    "claude_code_2-1-63_e4_10-9-2026.yaml",
])
def test_production_tool_continuation_never_injects_empty_user_when_prompts_suppressed(tmp_path, profile_name):
    import subprocess
    import sys
    from breadboard_engine.compaction_lanes.claude_lane import ROOT, signed_tool_response
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    (workspace / "records.txt").write_text("record one\nrecord two\n")
    settings = production_settings()
    script = {
        "main": [signed_tool_response(
            {"id": "msg_read", "name": "Read", "call_id": "toolu_read",
             "arguments": json.dumps({"file_path": str(workspace / "records.txt")}),
             "usage": {"input_tokens": 100, "output_tokens": 10}},
            settings["wire_model"], thinking="Inspect records.", signature="opaque-continuation-signature"),
            {"type": "text", "id": "msg_final", "text": "Inspection complete.", "usage": {"input_tokens": 100, "output_tokens": 10}}],
        "summary": [],
    }
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        code = """
import json, sys
from breadboard_engine.agent import AgenticCoder
from breadboard_engine.provider_broker import get_provider_broker
broker = get_provider_broker()
broker.set_config_api_key("anthropic", "bb-claude-lane", base_url=sys.argv[3])
try:
    agent = AgenticCoder(sys.argv[1], sys.argv[2], {"provider_tools.suppress_prompts": True}, force_local_mode=True)
    print(json.dumps(agent.run_task("Inspect records.txt with Read, then finish.", max_iterations=8, stream=True), default=str))
finally:
    broker.remove_config_api_key("anthropic")
"""
        environment = dict(os.environ, BB_WORKSPACE_ROOT=str(ROOT), PYTHONPATH=str(ROOT),
                           BREADBOARD_CREDENTIAL_STORE_PATH=str(tmp_path.resolve() / "credential-store.json"))
        process = subprocess.run([sys.executable, "-c", code, str(ROOT / "agent_configs" / profile_name), str(workspace), server.base_url],
                                 cwd=ROOT, env=environment, input="", text=True, capture_output=True, timeout=120)
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout.splitlines()[-1])
        assert result["completed"] is True, result
        records = server.engine.recorded_requests
        assert len(records) == 2
        assert all("_provider_tool_batch" not in message for message in result["messages"])
        assert all("_provider_tool_batch" not in message for record in records for message in record["json"]["messages"])
        last = records[1]["json"]["messages"][-1]
        assert last["role"] == "user"
        assert [block["type"] for block in last["content"]] == ["tool_result"]
        assert last["content"][0]["tool_use_id"] == "toolu_read"
        assert isinstance(last["content"][0]["content"], str) and last["content"][0]["content"]
        assert all(block["text"] for message in records[1]["json"]["messages"] if message["role"] == "user"
                   for block in message["content"] if block["type"] == "text")
        assistant = next(message for message in records[1]["json"]["messages"] if message["role"] == "assistant")
        assert assistant["content"][0] == {"type": "thinking", "thinking": "Inspect records.", "signature": "opaque-continuation-signature"}


def test_repeated_thinking_text_keeps_each_distinct_signature_in_order():
    from breadboard_engine.conductor.execution_records import legacy_message_view
    from breadboard_engine.conductor.model_output import _assistant_history_message
    from breadboard_engine.provider.contracts import ProviderRequest
    from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime
    runtime = AnthropicMessagesRuntime(SimpleNamespace(provider_id="anthropic", runtime_id="anthropic_messages"))
    native = [
        {"type": "thinking", "thinking": "same thought\n", "signature": "signature-one"},
        {"type": "thinking", "thinking": "same thought\n", "signature": "signature-two"},
        {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "/records.txt"}},
    ]
    result = runtime._normalize_response(SimpleNamespace(
        id="msg_repeated_thought", content=native, stop_reason="tool_use", usage={"input_tokens": 100, "output_tokens": 10},
    ))
    history = _assistant_history_message(legacy_message_view(result.messages[0]), tool_calls=[
        {"type": "function", "id": "toolu_read", "function": {"name": "Read", "arguments": '{"file_path":"/records.txt"}'}},
    ])
    for messages in ([history], ProviderRequest(stream=False, messages=[history], tools=[]).messages):
        _, converted = runtime._convert_messages(messages)
        assert converted == [{"role": "assistant", "content": native}]


def test_summary_request_count_includes_failed_and_exhausted_wire_attempts(tmp_path):
    import http.client
    from urllib.parse import urlsplit
    from breadboard_engine.compaction_lanes.claude_lane import SUMMARY_PREFIX, summary_request_count
    script = {"main": [], "summary": [{"type": "error", "status_code": 400, "message": "summary rejected"}]}
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        address = urlsplit(server.base_url)
        connection = http.client.HTTPConnection(address.hostname, address.port)
        body = json.dumps({"model": "claude", "messages": [{"role": "user", "content": SUMMARY_PREFIX}]})
        for status in (400, 409):
            connection.request("POST", "/v1/messages", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == status
            response.read()
        connection.close()
        assert server.engine.dispatches[-1]["exhausted"] is True
        assert summary_request_count(server.engine.recorded_requests) == 2


@pytest.mark.parametrize("stock_result,bb_result", [
    ({"result": "Inspection complete.", "is_error": True}, {"completed": True}),
    ({"result": "Not finished", "is_error": False}, {"completed": True}),
    ({"result": "Inspection complete.", "is_error": False}, {"completed": False}),
    ({"result": "Inspection complete.", "is_error": False}, {"completed": True, "completion_summary": {"error": {"type": "provider"}}}),
    ({}, {}),
])
def test_long_session_gate_rejects_failed_or_missing_terminal_completion(stock_result, bb_result):
    from breadboard_engine.compaction_lanes.claude_lane import long_session_terminal_problems
    assert long_session_terminal_problems(stock_result, bb_result)
    assert long_session_terminal_problems({"result": "Inspection complete.", "is_error": False}, {"completed": True}) == []


def test_startup_context_hook_preserves_one_original_user_message(monkeypatch):
    from breadboard_engine.compilation.context_builders import build_initial_context
    from breadboard_engine.compilation.context_builders import claude_code
    from breadboard_engine.state.session_state import SessionState
    context = "<system-reminder>\nAs you answer the user's questions, you can use the following context:\nToday.\n</system-reminder>"
    stock = {"system": [{"type": "text", "text": "Vendor system", "cache_control": {"type": "ephemeral"}}],
             "messages": [{"role": "user", "content": [
                 {"type": "text", "text": "Skills reminder"},
                 {"type": "text", "text": context},
                 {"type": "text", "text": "Inspect records.", "cache_control": {"type": "ephemeral"}},
             ]}]}
    session = SimpleNamespace(capture_context=lambda prompt: deepcopy(stock))
    monkeypatch.setattr(claude_code, "get_claude_tool_session", lambda owner, **kwargs: session)
    owner = SimpleNamespace(config={"prompts": {"initial_context": {
        "module": "breadboard_engine.compilation.context_builders.claude_code", "callable": "build_context"}}})
    state = SessionState("ws", "image", {})
    state.add_message({"role": "system", "content": "Original system"})
    user = {"role": "user", "content": "Inspect records."}
    additional = build_initial_context(owner, state, {}, fresh=True, user_message=user)
    for message in additional:
        state.add_message(message)
    state.add_message(user)
    assert state.provider_messages == [
        {"role": "system", "content": [{"type": "text", "text": "Vendor system"}]},
        {"role": "user", "content": [{"type": "text", "text": "Skills reminder"},
                                     {"type": "text", "text": "Inspect records."}]},
    ]


def test_native_stock_response_transport_round_trips_actual_sdk_message(tmp_path):
    import anthropic
    from breadboard_engine.execution.claude_tools import ClaudeToolSession
    native = {
        "id": "msg_actual_response", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [
            {"type": "thinking", "thinking": "Inspect records.", "signature": "actual-signature"},
            {"type": "redacted_thinking", "data": "opaque-redacted-data"},
            {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "/records.txt"}},
        ],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 22000, "output_tokens": 42,
                  "cache_read_input_tokens": 3, "cache_creation_input_tokens": 5},
    }
    script = {"main": [{"type": "raw", "status_code": 200, "headers": {"Content-Type": "text/event-stream"}, "chunks": [
        ClaudeToolSession.encode_native_response(native, {"stream": True}).decode(),
    ]}], "summary": []}
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        with anthropic.Anthropic(api_key="bb-claude-lane", base_url=server.base_url) as client:
            with client.messages.stream(
                model=native["model"], max_tokens=32000,
                messages=[{"role": "user", "content": "Inspect records."}],
            ) as stream:
                received = stream.get_final_message()
    # Installed SDK _messages.py:517 assigns delta.stop_details even when absent.
    assert received.model_dump(mode="json", exclude_unset=True) == {**native, "stop_details": None}


def test_native_cli_identity_is_stable_and_preserves_explicit_uuid(monkeypatch, tmp_path):
    import uuid
    from breadboard_engine.execution import claude_tools
    from breadboard_engine.state.session_state import SessionState
    created = []
    def create_session(workspace, model, **kwargs):
        created.append(kwargs)
        return SimpleNamespace()
    monkeypatch.setattr(claude_tools, "ClaudeToolSession", create_session)
    owner = SimpleNamespace(
        workspace=str(tmp_path), config={"providers": {"default_model": "anthropic/claude-haiku-4-5-20251001"}},
    )
    for admitted in ("run-first", "run-first", "run-second", "00000000-0000-4000-8000-000000000063"):
        state = SessionState("ws", "image", {})
        state.set_provider_metadata("session_id", admitted)
        session = claude_tools.get_claude_tool_session(owner, session_state=state)
        assert claude_tools.get_claude_tool_session(owner, session_state=state) is session
        assert state.get_provider_metadata("session_id") == admitted
    native_ids = [item["session_id"] for item in created]
    assert native_ids[0] == native_ids[1] != native_ids[2]
    assert uuid.UUID(native_ids[0]).version == 5
    assert native_ids[3] == "00000000-0000-4000-8000-000000000063"


def test_remote_agent_close_waits_for_existing_ray_shutdown(monkeypatch, tmp_path):
    from breadboard_engine import agent as agent_module
    from breadboard_engine.compaction_lanes.claude_lane import ROOT
    # The dated stock-sidecar profile is local-only (tools.native_local_only), so
    # the shared remote close path is exercised with a profile that admits Ray.
    profile = ROOT / "agent_configs/claude_code_2-1-63_e4_9-10-2026.yaml"
    reference = object()
    calls = []
    remote_close = SimpleNamespace(remote=lambda: reference)
    monkeypatch.setattr(agent_module, "_get_ray", lambda: SimpleNamespace(get=calls.append))
    agent = agent_module.AgenticCoder(str(profile), str(tmp_path), force_local_mode=False)
    agent._local_mode = False
    agent.agent = SimpleNamespace(close_stock_tools=remote_close)
    agent.close()
    assert calls == [reference]


@pytest.mark.parametrize("scenario", ["two_runs", "paused_overflow"])
def test_agent_close_releases_all_real_claude_sidecar_resources(tmp_path, scenario):
    import subprocess
    import sys
    from breadboard_engine.compaction_lanes.claude_lane import ROOT, signed_tool_response
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    (workspace / "records.txt").write_text("record one\nrecord two\n")
    settings = production_settings()
    if scenario == "paused_overflow":
        script = scenario_responses("overflow", workspace, settings, 10)
    else:
        main = []
        for index in (1, 2):
            main.extend([
                signed_tool_response(
                    {"id": f"msg_read_{index}", "name": "Read", "call_id": f"toolu_read_{index}",
                     "arguments": json.dumps({"file_path": str(workspace / "records.txt")}),
                     "usage": {"input_tokens": 100, "output_tokens": 10}},
                    settings["wire_model"], thinking="Inspect records.", signature=f"signature-{index}"),
                {"type": "text", "id": f"msg_final_{index}", "text": "Inspection complete.",
                 "usage": {"input_tokens": 100, "output_tokens": 10}},
            ])
        script = {"main": main, "summary": []}
    code = """
import json,sys
from breadboard_engine.agent import AgenticCoder
from breadboard_engine.provider_broker import get_provider_broker
broker=get_provider_broker()
broker.set_config_api_key("anthropic","bb-claude-lane",base_url=sys.argv[3])
agent=None
try:
    agent=AgenticCoder(sys.argv[1],sys.argv[2],force_local_mode=True)
    count=1 if sys.argv[4]=="paused_overflow" else 2
    results=[agent.run_task("Inspect records.txt with Read, then finish.",max_iterations=8,stream=True) for _ in range(count)]
    sessions=tuple(agent.agent._stock_tool_sessions)
    assert len(sessions)==count
    assert all(session.process.poll() is None for session in sessions)
    if sys.argv[4]=="paused_overflow":
        assert results[0]["completed"] is False
        assert results[0]["completion_summary"]["error"]["type"]=="provider"
        assert sessions[0].awaiting_main_request is not None
    else:
        assert all(result["completed"] is True for result in results)
    agent.close()
    assert agent.agent._stock_tool_sessions==[]
    for session in sessions:
        assert session.closed and session.process.poll() is not None
        assert session.server.socket.fileno()==-1
        assert not session.server_thread.is_alive()
        assert not session.output_thread.is_alive()
        assert not session.error_thread.is_alive()
        assert all(not thread.is_alive() for thread in session.server._threads)
        assert all(pipe.closed for pipe in (session.process.stdin,session.process.stdout,session.process.stderr))
    agent.close()
    print(json.dumps({"scenario":sys.argv[4],"runs":count,"closed":len(sessions)}))
finally:
    if agent is not None:
        agent.close()
    broker.remove_config_api_key("anthropic")
"""
    environment = dict(os.environ, BB_WORKSPACE_ROOT=str(ROOT), PYTHONPATH=str(ROOT),
                       BREADBOARD_CREDENTIAL_STORE_PATH=str(tmp_path.resolve() / "credential-store.json"))
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        process = subprocess.run(
            [sys.executable, "-c", code, str(ROOT / "agent_configs/claude_code_2-1-63_e4_10-9-2026.yaml"),
             str(workspace), server.base_url, scenario],
            cwd=ROOT, env=environment, input="", text=True, capture_output=True, timeout=120,
        )
        assert process.returncode == 0, process.stderr + process.stdout
        outcome = json.loads(process.stdout.splitlines()[-1])
        assert outcome["closed"] == (1 if scenario == "paused_overflow" else 2)
        assert len(server.engine.recorded_requests) == (1 if scenario == "paused_overflow" else 4)


@pytest.mark.parametrize("retry_cleanup", [False, True])
def test_close_cancels_real_pending_permission_and_closes_every_resource(tmp_path, monkeypatch, retry_cleanup):
    import queue
    import threading
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider_broker import get_provider_broker
    from breadboard_engine.compaction_lanes.claude_lane import PROFILE, signed_tool_response
    pending = threading.Event()

    class PermissionQueue(queue.Queue):
        def get(self, *args, **kwargs):
            pending.set()
            return super().get(*args, **kwargs)

    responses = PermissionQueue()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "not-approved.txt"
    settings = production_settings()
    script = {"main": [signed_tool_response(
        {"id": "msg_permission", "name": "Write", "call_id": "toolu_permission",
         "arguments": json.dumps({"file_path": str(target), "content": "never approved"}),
         "usage": {"input_tokens": 100, "output_tokens": 10}},
        settings["wire_model"], thinking="Write the requested file.", signature="permission-signature")],
        "summary": []}
    monkeypatch.setenv("BREADBOARD_CREDENTIAL_STORE_PATH", str(tmp_path / "credential-store.json"))
    broker = get_provider_broker()
    results = []
    agent = None
    worker = None
    with ClaudeMockProvider(script, tmp_path / "requests.jsonl") as server:
        broker.set_config_api_key("anthropic", "bb-claude-lane", base_url=server.base_url)
        try:
            agent = AgenticCoder(str(PROFILE), str(workspace), force_local_mode=True)
            worker = threading.Thread(target=lambda: results.append(
                agent.run_task("Write not-approved.txt.", max_iterations=8, stream=True, permission_queue=responses)))
            worker.start()
            assert pending.wait(30), results
            stock, = agent.agent._stock_tool_sessions
            state = agent.agent._active_session_state
            assert stock.output_thread.is_alive()
            if retry_cleanup:
                cancel_permission = stock.cancel_permission

                def interrupted():
                    raise RuntimeError("permission shutdown interrupted")

                stock.cancel_permission = interrupted
                with pytest.raises(RuntimeError, match="Stock tool shutdown failed"):
                    agent.close()
                assert stock.closed is False
                assert agent.agent._stock_tool_sessions == [stock]
                stock.cancel_permission = cancel_permission
            agent.close()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert len(results) == 1 and results[0]["completed"] is False
            assert not target.exists()
            assert agent.agent._stock_tool_sessions == []
            assert stock.closed and stock.process.poll() is not None
            assert stock.server.socket.fileno() == -1
            assert not any(thread.is_alive() for thread in (stock.server_thread, stock.output_thread, stock.error_thread))
            assert not any(thread.is_alive() for thread in stock.server._threads)
            assert all(pipe.closed for pipe in (stock.process.stdin, stock.process.stdout, stock.process.stderr))
            assert state.get_provider_metadata(agent.agent.permission_broker.STATE_KEY)["pending"] == {}
            from breadboard_engine.execution.claude_tools import StockExecutionError
            with pytest.raises(StockExecutionError):
                stock._pause_main({"messages": []})
            import anthropic
            from breadboard_engine.compilation.context_builders.claude_code import accept_response
            late = anthropic.types.Message(
                id="msg_late", type="message", role="assistant", model=settings["wire_model"],
                content=[{"type": "text", "text": "Late final answer."}],
                stop_reason="end_turn", stop_sequence=None,
                usage={"input_tokens": 100, "output_tokens": 10})
            with pytest.raises(StockExecutionError):
                accept_response(late, SimpleNamespace(session_state=state, extra={}))
            with pytest.raises(StockExecutionError):
                stock.release_main_response({"kind": "answer"}, late.model_dump(mode="json"))
            with pytest.raises(StockExecutionError):
                stock.execute("Write", {"file_path": str(target), "content": "late"})
            with pytest.raises(StockExecutionError):
                stock.complete_auto_compaction(late)
            assert stock.completed.is_set()
            with pytest.raises(StockExecutionError):
                stock.await_tool_result()
            agent.close()
        finally:
            if agent is not None:
                agent.close()
            if worker is not None:
                worker.join(timeout=5)
            broker.remove_config_api_key("anthropic")


def test_permission_cancellation_without_session_is_harmless():
    from breadboard_engine.permissions.broker import PermissionBroker
    assert PermissionBroker({}).cancel_pending_requests(None) is None


def test_cancelled_broker_rejects_late_permission_registration_without_waiting(tmp_path):
    import queue
    from breadboard_engine.permissions.broker import PermissionBroker
    from breadboard_engine.permissions.claude_permission import request_stock_tool_permission
    from breadboard_engine.state.session_state import SessionState
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={})
    responses = queue.Queue()
    state.set_provider_metadata("permission_queue", responses)
    broker = PermissionBroker({})
    broker.cancel_pending_requests(state)
    decision = request_stock_tool_permission(broker,
        state, {"tool_name": "Write", "input": {"file_path": str(tmp_path / "late.txt"), "content": "unapproved"}})
    assert decision["behavior"] == "deny"
    assert responses.empty()
    assert state.get_provider_metadata(broker.STATE_KEY)["pending"] == {}


def test_failed_stock_cleanup_retains_session_for_retry():
    from breadboard_engine.execution.stock_tools import register_stock_session, close_stock_sessions

    class Session:
        def __init__(self, fail):
            self.calls = 0
            self.fail = fail

        def close(self):
            self.calls += 1
            if self.fail:
                raise RuntimeError("shutdown interrupted")

    owner = SimpleNamespace()
    success, failure = Session(False), Session(True)
    register_stock_session(owner, success)
    register_stock_session(owner, failure)
    with pytest.raises(RuntimeError, match="Stock tool shutdown failed"):
        close_stock_sessions(owner)
    assert owner._stock_tool_sessions == [failure]
    failure.fail = False
    close_stock_sessions(owner)
    assert owner._stock_tool_sessions == []
    assert success.calls == 1 and failure.calls == 2


def test_exchange_schema_admits_only_exact_native_tool_replay_tags():
    from jsonschema import Draft202012Validator, ValidationError
    from breadboard_engine.compaction_lanes.claude_lane import ROOT
    schema = json.loads((ROOT / "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json").read_text())
    validator = Draft202012Validator({"$ref": "#/$defs/provider_replay_block", "$defs": schema["$defs"]})
    replay = {
        "type": "provider_replay", "provider_id": "anthropic",
        "schema_version": "anthropic.messages.tool_result.v1", "replay_scope": "same_provider",
        "payload": {"messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_read", "content": "record\n" * 1000},
        ]}]},
    }
    validator.validate(replay)
    for field, value in (("provider_id", "openai"), ("schema_version", "anthropic.messages.v1"),
                         ("replay_scope", "diagnostic")):
        invalid = {**replay, field: value}
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    for invalid_message in (
        {"role": "assistant", "content": []},
        {"role": "user", "content": "lossy text conversion"},
        {"role": "user", "content": [], "unknown": True},
        {"role": "user", "content": [{"text": "missing native type"}]},
    ):
        with pytest.raises(ValidationError):
            validator.validate({**replay, "payload": {"messages": [invalid_message]}})


@pytest.mark.skipif(os.environ.get("BB_RUN_CLAUDE_LANE") != "1", reason="Opt-in real pinned stock and production engine differential lane")
@pytest.mark.parametrize("scenario", ["long_session", "overflow"])
def test_real_pinned_stock_against_production_engine(tmp_path, scenario):
    assert (STOCK_PACKAGE / "cli.js").is_file(), "Install the pinned stock package before running this gate"
    report = run_scenario(scenario, tmp_path.resolve() / scenario)
    assert report["passed"], json.dumps({"unresolved": report["unresolved"], "diffs": report["diffs"], "artifacts": report["artifacts"]}, indent=2)
