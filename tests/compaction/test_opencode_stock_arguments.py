from tests.compaction.test_opencode_stock_tools import omo_dependencies
import copy
import json
from types import SimpleNamespace
import pytest
from breadboard_engine.compilation.context_builders.opencode_stock import (
    StockSummaryProjection, project_inflight_tool_arguments, project_inflight_tool_outputs,
)
from breadboard_engine.compaction.methods import SummaryRequest
from breadboard_engine.execution.opencode.wire import StockExecutionError
from breadboard_engine.provider.contracts import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
from tests.compaction.test_opencode_stock_tools import binary
from tests.compaction.test_opencode_stock_tools import direct_stock
from tests.compaction.test_opencode_stock_tools import plain
from tests.compaction.test_opencode_stock_tools import response
from tests.compaction.test_opencode_stock_tools import hermetic_stock_config
import base64
from breadboard.product.runtime.artifacts import put_workspace_artifact
from breadboard_engine.execution.opencode.driver import stock_user_parts
from breadboard_engine.provider.contracts import ProviderContractError, ProviderRuntimeContext
from breadboard_engine.state.session_state import SessionState
from pathlib import Path
from breadboard_engine.provider.adapters import OpenAIAdapter
from breadboard_engine.compilation.context_builders.opencode_stock import project_inflight_current_user
from breadboard_engine.execution.opencode.session import OpenCodeToolSession
from tests.compaction.test_opencode_stock_tools import MODEL
from tests.compaction.test_opencode_stock_tools import isolated_environment
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN_DIGEST
from tests.compaction.test_opencode_stock_omo_groups import LSP_PIN
from tests.compaction.test_opencode_stock_omo_groups import direct_omo
import httpx
from openai import APIStatusError
from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.execution.opencode import driver

"""Project genuine stock JS serialization only into dated request DTOs."""




@pytest.mark.parametrize("raw", [
    ' { "description" : "Check argument order", "command" : "printf parity" } ',
    '{"description":"Check integral float","timeout":1.0,"command":"printf parity"}',
    '{"description":"Check exponent","timeout":1e3,"command":"printf parity"}',
])
def test_native_argument_projection_matches_direct_stock_and_preserves_history(plain, binary, tmp_path, raw):
    call_id = "call_arguments"
    payload = response(plain, [(call_id, "bash", json.loads(raw))])
    payload["output"][0]["arguments"] = raw
    plain.user([{"type": "text", "text": "Execute the approved operation."}])
    plain.submit(payload)
    native = plain.pending["body"]
    replay = {"type": "provider_replay", "provider_id": "openai",
              "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider",
              "payload": {"type": "function_call", "call_id": call_id, "name": "bash", "arguments": raw}}
    messages = [{"role": "assistant", "content": [replay]}]
    canonical = copy.deepcopy(messages)
    projected = project_inflight_tool_arguments(native, messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_preserve_tool_call_items": True}}})
    actual = runtime._convert_messages_to_input(projected, context=context)
    assert messages == canonical
    assert runtime._convert_messages_to_input(messages, context=context)[0]["arguments"] == raw
    assert actual[0]["arguments"] != raw
    assert list(json.loads(actual[0]["arguments"])) == list(json.loads(raw))
    plain.submit(response(plain))
    requests = []
    direct_stock(binary, plain.workspace, tmp_path / "oracle", [(call_id, "bash", json.loads(raw))],
                 captured_requests=requests, raw_arguments={call_id: raw})
    expected = next(item for item in requests[-1]["json"]["input"]
                    if item.get("type") == "function_call" and item.get("call_id") == call_id)
    assert actual == [expected]
    assert actual[0]["arguments"].encode() == expected["arguments"].encode()
    assert messages == canonical
    request = SummaryRequest(system=None, messages=(*messages, {"role": "user", "content": "Summarize."}),
                             max_tokens=None, purpose="summary", context_reason="manual")
    summary = StockSummaryProjection(SimpleNamespace(_opencode_tool_session=plain)).prepare(request)
    summary_calls = runtime._convert_messages_to_input(list(summary.messages), context=context)
    assert summary_calls[0] == expected
    assert request.messages[0] == canonical[0]
    plain.compact("Arguments remained source-serialized.", auto=False)


def test_missing_native_call_fails_without_raw_argument_fallback():
    messages = [{"role": "assistant", "content": [{"type": "provider_replay",
        "schema_version": "openai.responses.tools.v1",
        "payload": {"type": "function_call", "call_id": "call_missing", "arguments": "{}"}}]}]
    with pytest.raises(StockExecutionError, match="omitted arguments.*call_missing"):
        project_inflight_tool_arguments({"input": []}, messages)


@pytest.mark.parametrize("name,raw,repaired", [
    ("BASH", '{"command":"printf repaired","description":"Repair uppercase name"}', "bash"),
    ("bash", "{}", "bash"),
    ("bash", '{"command":', "invalid"),
])
def test_stock_repairs_calls_and_continues_with_exact_native_result(plain, binary, tmp_path, name, raw, repaired):
    call_id = "call_repair"
    call = (call_id, name, {})
    payload = response(plain, [call])
    payload["output"][0]["arguments"] = raw
    plain.user([{"type": "text", "text": "Execute the approved operation."}])
    plain.submit(payload)
    actual_result = plain.result(call_id)["__mvi_text_output"]
    native = plain.pending["body"]
    replay = {"type": "provider_replay", "provider_id": "openai",
              "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider",
              "payload": {"type": "function_call", "call_id": call_id, "name": name, "arguments": raw}}
    messages = [{"role": "assistant", "content": [replay]}]
    canonical = copy.deepcopy(messages)
    projected = project_inflight_tool_arguments(native, messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_preserve_tool_call_items": True}}})
    compiled = runtime._convert_messages_to_input(projected, context=context)
    assert compiled[0]["name"] == repaired
    assert messages == canonical
    plain.submit(response(plain, text="Repaired call complete."))
    requests = []
    expected = direct_stock(binary, plain.workspace, tmp_path / "oracle", [call],
                            captured_requests=requests, raw_arguments={call_id: raw})
    assert actual_result == expected[call_id]
    source_call = next(item for item in requests[-1]["json"]["input"]
                       if item.get("type") == "function_call" and item.get("call_id") == call_id)
    assert compiled == [source_call]
    assert messages == canonical


@pytest.mark.parametrize("shape", ["scalar", "block", "replay"])
def test_native_output_projection_preserves_canonical_history_and_unowned_outputs(shape):
    native = {"input": [
        {"type": "function_call_output", "call_id": "owned", "output": "[Old tool result content cleared]"},
        {"type": "function_call_output", "call_id": "native_only", "output": "Never import native history"},
        {"role": "user", "content": "Summarize the source-owned checkpoint."},
    ]}
    if shape == "scalar":
        owned = {"role": "tool", "tool_call_id": "owned", "content": "Canonical original"}
    elif shape == "block":
        owned = {"role": "tool_result", "content": [
            {"type": "tool_result", "call_id": "owned", "content": "Canonical original"}]}
    else:
        owned = {"role": "tool", "content": [{"type": "provider_replay", "provider_id": "openai",
            "schema_version": "openai.responses.tool_output.v1", "replay_scope": "same_provider",
            "payload": {"type": "function_call_output", "call_id": "owned", "output": "Canonical original"}}]}
    messages = [owned, {"role": "tool", "tool_call_id": "bb_only", "content": "BB-only result"}]
    canonical = copy.deepcopy(messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_tool_output_items": True}}})
    projected = project_inflight_tool_outputs(native, messages)
    assert runtime._convert_messages_to_input(projected, context=context) == [
        native["input"][0], {"type": "function_call_output", "call_id": "bb_only", "output": "BB-only result"}]
    assert messages == canonical
    assert projected[1] is messages[1]
    session = SimpleNamespace(compaction_prompt=lambda **kwargs: native)
    request = SummaryRequest(system=None, messages=(*messages, {"role": "user", "content": "Summarize."}),
                             max_tokens=None, purpose="summary", context_reason="threshold")
    summary = StockSummaryProjection(SimpleNamespace(_opencode_tool_session=session)).prepare(request)
    assert runtime._convert_messages_to_input(list(summary.messages[:-1]), context=context) == (
        runtime._convert_messages_to_input(projected, context=context))
    assert messages == canonical and request.messages[0] == canonical[0]


"""Native user media uses the existing turn capability, never MIME guessing."""




def test_canonical_media_resolves_existing_authorized_artifact(tmp_path):
    content = b"authorized image bytes"
    artifact = put_workspace_artifact(tmp_path, content, media_type="image/png")
    uri = "attachment://" + artifact.digest
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={})
    state.set_provider_metadata("attachment_capabilities", {uri: artifact.as_dict()})
    context = ProviderRuntimeContext(session_state=state, agent_config={})
    block = {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"}
    assert stock_user_parts([block], context=context) == [{
        "type": "file", "mime": "image/png", "filename": "attachment",
        "url": "data:image/png;base64," + base64.b64encode(content).decode(),
    }]
    state.set_provider_metadata("attachment_capabilities", {})
    with pytest.raises(ProviderContractError, match="not authorized"):
        stock_user_parts([block], context=context)


def test_native_image_url_never_invents_mime():
    with pytest.raises(StockExecutionError, match="URL and MIME"):
        stock_user_parts([{"type": "image_url", "image_url": {"url": "https://example.com/image"}}])


def test_image_read_reaches_parent_provider_as_exact_stock_media(plain, binary, tmp_path):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII=")
    (Path(plain.workspace) / "pixel.png").write_bytes(png)
    call = ("call_image_read", "read", {"filePath": "pixel.png"})
    plain.user([{"type": "text", "text": "Execute the approved operation."}])
    plain.submit(response(plain, [call]))
    result = plain.result(call[0])
    assert result["__mvi_native_responses_output"] is True
    canonical = OpenAIAdapter().create_tool_result_message(call[0], call[1], result)
    context = ProviderRuntimeContext(session_state=None, agent_config={
        "provider_tools": {"openai": {"responses_tool_output_items": True}},
    })
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    actual = runtime._convert_messages_to_input([canonical], context=context)
    plain.submit(response(plain))
    expected = direct_stock(binary, plain.workspace, tmp_path / "oracle", [call])
    assert actual == [{"type": "function_call_output", "call_id": call[0], "output": expected[call[0]]}]
    assert any(part["type"] == "input_image" for part in actual[0]["output"])


def test_auto_compaction_media_continuation_projects_exact_native_user_without_history_mutation(plain, tmp_path):
    import copy
    from breadboard_engine.compilation.context_builders.opencode_stock import project_inflight_current_user

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII=")
    artifact = put_workspace_artifact(plain.workspace, png, media_type="image/png")
    uri = "attachment://" + artifact.digest
    config = {"tools": {"native_semantics": "opencode_1_2_17"},
              "provider_tools": {"openai": {"responses_stateful": False}}}
    state = SessionState(workspace=plain.workspace, image="python-dev:latest", config=config)
    state.set_provider_metadata("attachment_capabilities", {uri: artifact.as_dict()})
    state._opencode_tool_session = plain
    context = ProviderRuntimeContext(state, config)
    media = {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"}
    history = [{"role": "user", "content": [{"type": "text", "text": "Inspect this image."}, media]}]
    canonical = copy.deepcopy(history)
    plain.user(stock_user_parts(history[0]["content"], context=context))
    plain.submit(response(plain, text="Image inspected."))
    plain.compact("The image was inspected.", auto=True)
    native = plain.pending["body"]
    assert plain.is_compaction_continuation()
    projected = project_inflight_current_user(state, native, history)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    compiled = runtime.project_request_body(model=plain.model.split("/", 1)[1], messages=projected,
                                            tools=None, stream=False, context=context)
    source_user = next(item for item in reversed(native["input"]) if item.get("role") == "user")
    assert compiled["input"][-1] == source_user
    assert all(part["type"] == "input_text" for part in compiled["input"][-1]["content"])
    assert history == canonical
    plain.submit(response(plain, text="Continuation complete."))





@pytest.mark.usefixtures("omo_dependencies")
def test_real_user_prompt_submit_context_is_ephemeral_and_matches_stock(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = workspace / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": "printf NATIVE_USER_HOOK_CONTEXT"},
    ]}]}}))
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
                                 read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
                                 lsp_root=LSP_PIN, timeout=120, stock_config=hermetic_stock_config({"agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "stock"))
    try:
        # Stock run.ts:340 prefixes piped stdin with one newline.
        request = session.user([{"type": "text", "text": "\nExecute this stock tool group."}])
        persisted = session.latest_user_sequence()
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(persisted)
        messages = [{"role": "user", "content": persisted}]
        state = SimpleNamespace(_opencode_tool_session=session)
        projected = project_inflight_current_user(state, request, messages)
        assert "NATIVE_USER_HOOK_CONTEXT" in json.dumps(projected)
        assert messages == [{"role": "user", "content": persisted}]
        runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
        converted = runtime._convert_messages_to_input(projected, context=ProviderRuntimeContext(None, {}))
        session.submit(response(session))
        direct_requests = []
        direct_omo(binary, str(workspace), tmp_path / "oracle", [], captured_requests=direct_requests)
        direct_user = next(item for item in reversed(direct_requests[0]["json"]["input"]) if item.get("role") == "user")
        assert converted == [direct_user]
        session.user([{"type": "text", "text": "Follow up after the source hook."}])
        followup = [{"role": "user", "content": session.latest_user_sequence()}]
        session.submit(response(session, [("call_hook_followup", "bash", {
            "command": "printf hook-followup", "description": "Continue after the user hook",
        })]))
        continued = project_inflight_current_user(state, session.pending["body"], followup)
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(continued)
        summary_request = session.compaction_prompt(auto=False)
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(summary_request["input"])
        session.compact("The source hook remained ephemeral.", auto=False)
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(session.api("GET", "/session/" + session.session_id + "/message"))
    finally:
        session.close()


"""Native failure forwarding follows the same classifiers as its preset."""




@pytest.mark.parametrize("status", [400, 500])
@pytest.mark.parametrize("preset,semantics,message,forwarded", [
    ("opencode@1.2.17", "opencode_1_2_17", "too many tokens: 74000 tokens > 64000 maximum", False),
    ("oh-my-opencode@3.10.0", "oh_my_opencode_3_10_0", "too many tokens: 74000 tokens > 64000 maximum", True),
    ("oh-my-opencode@3.10.0", "oh_my_opencode_3_10_0", "prompt is too long for this model", True),
    ("oh-my-opencode@3.10.0", "oh_my_opencode_3_10_0", "request would exceed model context budget", False),
    ("opencode@1.2.17", "opencode_1_2_17", "context_length_exceeded", True),
])
def test_original_api_error_uses_recipe_native_or_plugin_parser(monkeypatch, tmp_path, preset, semantics, message, forwarded, status):
    body = {"error": {"message": message, "type": "invalid_request_error"}}
    response = httpx.Response(status, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"},
                              request=httpx.Request("POST", "http://localhost/v1/responses"))
    original = APIStatusError(message, response=response, body=body)
    wrapped = RuntimeError("sanitized context_length_exceeded")
    wrapped.__cause__ = original
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset, "contextWindow": 64000}})
    conductor = SimpleNamespace(config={"tools": {"native_semantics": semantics}}, compaction_controller=controller)
    observed = []
    def submit_overflow(status, headers, payload):
        observed.append((status, headers, payload))
        return "summary"
    session = SimpleNamespace(submit_overflow=submit_overflow)
    monkeypatch.setattr(driver, "get_session", lambda owner, state: session)
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config=conductor.config)
    driver.submit_stock_overflow(conductor, state, wrapped)
    assert state.get_provider_metadata("opencode_stock_recovery_outcome") == ("summary" if forwarded else None)
    assert bool(observed) is forwarded
    if forwarded:
        assert observed == [(status, dict(response.headers), response.content)]
    assert all(getattr(trigger, "pending", None) is None for trigger in controller.recipe.triggers)


@pytest.mark.parametrize("source_order", [
    ("type", "call_id", "name", "arguments"),
    ("call_id", "arguments", "type", "name"),
])
@pytest.mark.parametrize("summary", [False, True])
def test_inflight_call_wire_order_recovers_source_order_after_canonical_sort(source_order, summary):
    values = {
        "type": "function_call", "call_id": "persisted-call",
        "name": "bash", "arguments": '{"command":"printf preserved"}',
    }
    native_call = {key: values[key] for key in source_order}
    native = {
        "stream": False, "input": [native_call, {"role": "user", "content": "Summarize."}],
        "model": "gpt-fixture",
    }
    persisted_call = json.loads(json.dumps(values, sort_keys=True))
    messages = [{"role": "assistant", "content": [{
        "type": "provider_replay", "provider_id": "openai",
        "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider",
        "payload": persisted_call,
    }]}]
    canonical_bytes = json.dumps(messages)
    if summary:
        session = SimpleNamespace(compaction_prompt=lambda **kwargs: native)
        request = SummaryRequest(
            system=None, messages=(*messages, {"role": "user", "content": "Summarize."}),
            max_tokens=None, purpose="summary", context_reason="threshold",
        )
        prepared = StockSummaryProjection(
            SimpleNamespace(_opencode_tool_session=session),
        ).prepare(request)
        assert prepared.native_request_field_order == tuple(native)
        assert request.native_request_field_order is None
        projected = list(prepared.messages)
    else:
        projected = project_inflight_tool_arguments(native, messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(
        provider_id="openai", runtime_id="openai_responses",
    ))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {
        "responses_preserve_tool_call_items": True,
    }}})
    actual_call = runtime._convert_messages_to_input(projected, context=context)[0]
    assert json.dumps(actual_call, separators=(",", ":")).encode() == (
        json.dumps(native_call, separators=(",", ":")).encode()
    )
    assert json.dumps(messages) == canonical_bytes
