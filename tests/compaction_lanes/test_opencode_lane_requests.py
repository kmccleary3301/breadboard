"""OpenCode lane requests behavioral regressions."""
from __future__ import annotations
import json
import pytest
from breadboard_engine.compaction_lanes.opencode_scenarios import PROFILES, TEST_WINDOW, production_settings

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
    from breadboard_engine.provider.contract_requests import normalize_request_messages
    from breadboard_engine.provider.contract_wire import ProviderContractError
    with pytest.raises(ProviderContractError, match="_provider_tool_batch"):
        normalize_request_messages([{"role": "assistant", "content": "", "_provider_tool_batch": batch}])

def test_provider_tool_batch_is_consumed_but_never_reaches_wire():
    from types import SimpleNamespace
    from breadboard_engine.provider.contract_requests import normalize_request_messages
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
    from breadboard_engine.execution.opencode.wire import StockExecutionError
    with pytest.raises(StockExecutionError, match="exact input_text"):
        _last_user_content({"input": [{"role": "user", "content": [
            {"type": "input_text", "text": "input", "unexpected": "must not disappear"}]}]})

@pytest.mark.parametrize("sequence", [
    [{"type": "stock_media", "index": 1}],
    [{"type": "stock_media", "index": 0}, {"type": "stock_media", "index": 0}],
    [{"type": "text", "text": "No authorized media slot"}],
])
def test_source_input_projection_rejects_unauthorized_or_missing_media(monkeypatch, sequence):
    from types import SimpleNamespace
    from breadboard_engine.compilation.context_builders import opencode_stock
    from breadboard_engine.execution.opencode.wire import StockExecutionError
    original = {"role": "user", "content": [{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "authorized"}}]}
    monkeypatch.setattr(opencode_stock, "prepare_stock_initial_user", lambda owner, state: {"input": []})
    state = SimpleNamespace(_opencode_tool_session=SimpleNamespace(latest_user_sequence=lambda: sequence))
    with pytest.raises(StockExecutionError, match="media"):
        opencode_stock.build_context(None, state, None, fresh=True, user_message=original)

@pytest.mark.parametrize("harness", PROFILES)
def test_episode_profile_materialization_preserves_production_settings_and_declared_overrides(harness, tmp_path):
    import hashlib
    from breadboard_engine.compilation.v2_loader import load_agent_config
    from breadboard_engine.compaction_lanes.opencode_episode import materialize_episode_profile
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
    from breadboard_engine.compaction_lanes.opencode_episode import run_bb_queued_session
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
