"""OpenCode lifecycle and provider-boundary regression coverage."""
from __future__ import annotations

import copy

import pytest

from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.presets import build_recipe, load_preset_document
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtime import provider_registry
from breadboard_engine.state.session_state import SessionState

from .test_controller import FakeRuntime as OmpFakeRuntime, _controller, _session, _target
from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult


class FakeRuntime:
    descriptor = type("Descriptor", (), {"provider_id": "openai", "default_api_variant": "chat", "runtime_id": "openai_chat"})()

    def __init__(self):
        self.summary_requests = []

    def invoke(self, *, client, model, messages, tools, stream, context):
        assert tools is None and stream is True
        assert context.extra["compaction_summary"] is True
        assert context.extra["max_tokens"] == 32000
        self.summary_requests.append(copy.deepcopy(messages))
        return ProviderResult(messages=[ProviderMessage(role="assistant", content="## Goal\nport the parser")],
                              raw_response=None, model=model)

PRESETS = ("opencode@1.2.17", "oh-my-opencode@3.10.0")


def _tool_history(content="result") -> SessionState:
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "Run tool"})
    state.add_message({"role": "assistant", "content": "Running", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    ]})
    state.add_message({"role": "tool_result" if isinstance(content, list) else "tool",
                       "tool_call_id": "c1", "content": content})
    return state


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("provider_model", ["openai/gpt-4o-mini", "anthropic/claude-3-opus"])
@pytest.mark.parametrize("content", [
    "result",
    [{"type": "tool_result", "call_id": "c1", "content": "result"}],
    [{"type": "tool_result", "call_id": "c1", "content": "result", "attachments": [
        {"type": "image", "mime": "image/png", "url": "data:image/png;base64,AA=="}
    ]}],
])
def test_summary_tool_results_pass_real_provider_converters(preset, provider_model, content):
    descriptor, _ = provider_router.get_runtime_descriptor(provider_model)
    converter = provider_registry.create_runtime(descriptor)

    class ConvertingRuntime(FakeRuntime):
        def invoke(self, *, client, model, messages, tools, stream, context):
            if descriptor.provider_id == "anthropic":
                _, converted = converter._convert_messages(messages, context=context)
                blocks = [part for message in converted for part in message["content"]]
                assert any(p.get("type") == "tool_use" and p["id"] == "c1" for p in blocks)
                assert any(p.get("type") == "tool_result" and p["tool_use_id"] == "c1"
                           and p["content"] == "result" for p in blocks)
            else:
                converted = converter._convert_messages_to_chat(messages, context=context)
                assert any(m.get("tool_calls", [{}])[0].get("id") == "c1" for m in converted)
                assert {"role": "tool", "tool_call_id": "c1", "content": "result"} in converted
            assert "AA==" not in str(messages)
            return super().invoke(client=client, model=model, messages=messages, tools=tools,
                                  stream=stream, context=context)

    state = _tool_history(content)
    history = copy.deepcopy(state.provider_messages)
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    outcome = controller.compact_now(state, target=_target(), runtime=ConvertingRuntime(), client=object())
    assert outcome.compacted, outcome.stages
    assert state.provider_messages == history


@pytest.mark.parametrize("usage", [None, {"total_tokens": 1}, {"total_tokens": 999999}])
@pytest.mark.parametrize("limit", [160000, 200000])
@pytest.mark.parametrize("chars", [500000, 600000])
@pytest.mark.parametrize("error_format", ["provider_text", "redacted_bounds"])
def test_plugin_native_overflow_summarizes_before_deferred_hook(usage, limit, chars, error_format):
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError

    state = _tool_history("x" * chars)
    if usage is not None:
        state.set_provider_metadata("usage", usage)
    state.set_provider_metadata("max_input_tokens", 190000)
    details = {"code": "context_length_exceeded"}
    if error_format == "provider_text":
        details["message"] = f"prompt is too long: 210000 tokens > {limit} maximum"
    else:
        details.update(overflow_tokens=210000, overflow_limit=limit)
    error = ProviderRuntimeError("provider operation failed", details=details)
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "oh-my-opencode@3.10.0", "contextWindow": 200000,
    }})
    runtime = FakeRuntime()
    history = copy.deepcopy(state.provider_messages)
    assert controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime, client=object(), model="gpt-test",
    )
    # Native processor.ts:420 returns compact immediately; the deferred
    # session.error hook skips the finished summary (recovery-hook.ts:140).
    assert len(runtime.summary_requests) == 1
    assert [r.method for r in state.compaction_state.records] == ["summary"]
    assert state.provider_messages == history


@pytest.mark.parametrize("message", [
    "prompt is too long: 210000 tokens > 200000 maximum",
    "maximum context length is 200000 tokens, but you requested 210000 tokens",
])
def test_provider_redaction_preserves_only_numeric_overflow_bounds(message):
    from breadboard_engine.compaction.overflow import overflow_http_details, provider_overflow_details

    class SDKError(Exception):
        status_code = 400
        body = {"error": {"message": message}, "credential": "secret"}

    expected = {
        "code": "context_length_exceeded", "classification": "context_overflow", "status_code": 400,
        "overflow_tokens": 210000, "overflow_limit": 200000,
    }
    assert provider_overflow_details(SDKError("redacted")) == expected
    assert overflow_http_details(400, SDKError.body) == expected


def _eligible_tool_history() -> SessionState:
    state = _tool_history("x" * 240004)
    for turn in (2, 3):
        state.add_message({"role": "user", "content": f"Turn {turn}"})
        state.add_message({"role": "assistant", "content": f"Answer {turn}"})
    return state


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("reason", ["threshold", "manual", "overflow"])
def test_summary_does_not_prune_newly_eligible_tool_output(preset, reason):
    state = _eligible_tool_history()
    state.add_message({"role": "user", "content": "Continue"})
    history = copy.deepcopy(state.provider_messages)
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    runtime = FakeRuntime()
    outcome = controller.compact(state, reason=reason, target=_target(), runtime=runtime, client=object())
    assert outcome.compacted, outcome.stages
    assert "prune" not in [s.stage for s in outcome.stages]
    assert any(m.get("role") == "tool" and m["content"] == "x" * 240004
               for m in runtime.summary_requests[0])
    assert state.provider_messages == history
    view = controller.build_request_view(state, target=_target())
    assert all("summary" not in message for message in view)
    assert state.compaction_state.records[-1].details["summary"] is True


@pytest.mark.parametrize("preset", PRESETS)
def test_prune_runs_at_user_turn_end_not_request_start(preset):
    # Pinned OpenCode prompt.ts:716 calls prune after the prompt loop exits.
    state = _eligible_tool_history()
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    history = copy.deepcopy(state.provider_messages)
    assert controller.build_request_view(state, target=_target(), turn_index=1) == history
    assert not state.compaction_state.records
    descriptor, _ = provider_router.get_runtime_descriptor("openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)
    controller.finish_user_turn(
        state, conductor=None, runtime=runtime, client=None,
        model="gpt-4o-mini", turn_index=1,
    )
    assert controller.build_request_view(state, target=_target())[2]["content"] == "[Old tool result content cleared]"
    assert state.compaction_state.records[-1].reason == "request"
    assert state.lifecycle_events[-1]["payload"]["stages"][0]["id"] == "prune"
    assert state.provider_messages == history


@pytest.mark.parametrize("preset", PRESETS)
def test_completed_turn_pruning_survives_product_final_snapshot(preset):
    # Stock compaction.ts:91-94 updates persisted tool parts after prompt.ts:716.
    state = _eligible_tool_history()
    original = copy.deepcopy(state.provider_messages)
    state.record_provider_request_surface(original)
    descriptor, _ = provider_router.get_runtime_descriptor("openai/gpt-4o-mini")
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    controller.finish_user_turn(state, conductor=None, runtime=provider_registry.create_runtime(descriptor),
                                client=None, model="gpt-4o-mini", turn_index=1)
    retained = state.final_provider_context()
    assert retained[2]["content"] == "[Old tool result content cleared]"
    restored = SessionState("ws", "image", {})
    restored.provider_messages = retained
    assert controller.build_request_view(restored, target=_target())[2]["content"] == retained[2]["content"]
    assert state.provider_messages == original


@pytest.mark.parametrize("preset", PRESETS)
def test_final_assistant_pressure_uses_declared_checkpoint(preset):
    state = _tool_history()
    state.add_message({"role": "assistant", "content": "Final answer"})
    state.set_provider_metadata("usage", {"total_tokens": 45000})
    state.set_provider_metadata("max_input_tokens", 64000)
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset, "contextWindow": 64000}})
    runtime = FakeRuntime()
    controller.prepare_request(state, conductor=None, runtime=runtime, client=object(), model="gpt-test", turn_index=1)
    assert not runtime.summary_requests
    assert controller.finish_assistant_step(state, conductor=None, runtime=runtime, client=object(), model="gpt-test", turn_index=1)
    assert len(runtime.summary_requests) == 1
    assert not controller.finish_assistant_step(state, conductor=None, runtime=runtime, client=object(), model="gpt-test", turn_index=1)


@pytest.mark.parametrize("preset", PRESETS)
def test_summary_boundary_invalidates_superseded_responses_reference(preset):
    state = _tool_history()
    state.set_provider_metadata("previous_response_id", "resp_original")
    state.set_provider_metadata("conversation_id", "conv_original")
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    from breadboard_engine.compaction.state import ProjectionTarget
    controller.compact_now(state, target=ProjectionTarget("openai", "responses", "gpt-test"),
                           runtime=FakeRuntime(), client=object())
    assert state.get_provider_metadata("previous_response_id") is None
    assert state.get_provider_metadata("conversation_id") is None
    assert controller.build_request_view(state, target=_target())[0]["role"] == "user"


@pytest.mark.parametrize("request_declared", [False, True])
def test_legacy_summary_boundary_preserves_responses_state_references(request_declared):
    from breadboard_engine.compaction.state import ProjectionTarget
    from breadboard_engine.compaction.params import BuildEnv, Params
    from breadboard_engine.compaction.primitives.chat_reducers import ChatSummary

    state = _session()
    state.set_provider_metadata("previous_response_id", "resp_original")
    state.set_provider_metadata("conversation_id", "conv_original")
    controller = _controller()
    target = ProjectionTarget("openai", "responses", "gpt-test")
    # Exercise the actual legacy recipe, not a manually fabricated boundary.
    outcome = controller.compact_now(state, target=target, runtime=OmpFakeRuntime(), client=object())
    assert outcome.compacted
    assert state.get_provider_metadata("previous_response_id") == "resp_original"
    assert state.get_provider_metadata("conversation_id") == "conv_original"
    reducer = ChatSummary(Params({"system": "Summary", "prompt": "Summarize",
                                  **({"request": {"stream": False}} if request_declared else {})}, "test", BuildEnv(None)))
    assert reducer.stateless is False


def test_other_presets_do_not_run_assistant_end_checkpoint():
    controller = _controller()
    runtime = FakeRuntime()
    state = _session()
    state.set_provider_metadata("usage", {"total_tokens": 999999})
    assert not controller.finish_assistant_step(state, conductor=None, runtime=runtime, client=object(), model="gpt-test", turn_index=1)
    assert not runtime.summary_requests


@pytest.mark.parametrize("preset", PRESETS)
def test_request_pass_aggregates_stage_ids_once_in_recipe_order(preset):
    state = _eligible_tool_history()
    state.add_message({"role": "user", "content": "Next turn"})
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    document = load_preset_document(preset)
    prune = next(s for s in document["pipeline"]["stages"] if s["id"] == "prune")
    document["pipeline"]["stages"].append({**copy.deepcopy(prune), "id": "prune_again"})
    document["request_view"] = ["prune", "omp_prune", "prune_again", "omp_inline_snapcompact"]
    controller.recipe = build_recipe(controller.config, document)
    controller.build_request_view(state, target=_target())
    finished = [event for event in state.lifecycle_events if event["type"] == "compaction_finished"]
    assert len(finished) == 1
    payload = finished[0]["payload"]
    assert payload["reason"] == "request"
    assert [s["id"] for s in payload["stages"]] == ["prune", "prune_again"]
    assert payload["stages"][0]["status"] == "edited"
    assert payload["stages"][0]["detail"] is None
    assert payload["stages"][1]["status"] == "unavailable"


def test_omp_request_builtins_keep_base_record_reason_and_events():
    state = _session(turns=2)
    state.provider_messages[4]["tool_calls"][0]["function"]["arguments"] = '{"path":"f0.py"}'
    controller = _controller(prune={
        "enabled": True, "supersede_reads": True, "minimum_savings": 50, "protect_tokens": 0,
    })
    controller.build_request_view(state, target=_target())
    assert state.compaction_state.records
    assert all(record.reason == "threshold" for record in state.compaction_state.records)
    assert [event["type"] for event in state.lifecycle_events] == ["compaction_record_appended"]


@pytest.mark.parametrize("preset", PRESETS)
def test_packaged_prompt_assets_have_provenance_and_no_obsolete_duplicates(preset):
    import json
    from pathlib import Path
    from breadboard_engine.compaction import presets

    directory = Path(presets.__file__).parent / "prompts" / preset
    provenance = json.loads((directory / "SOURCE.json").read_text())
    assert {p.name for p in directory.iterdir()} == {"SOURCE.json", *provenance["files"]}


@pytest.mark.parametrize("preset", PRESETS)
def test_inline_request_projection_must_be_last(preset):
    from breadboard_engine.compaction.params import PresetError

    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    document = load_preset_document(preset)
    document["request_view"] = ["omp_inline_snapcompact", "prune"]
    with pytest.raises(PresetError, match="final request_view entry"):
        build_recipe(controller.config, document)


def test_request_summary_receives_configured_custom_instructions():
    state = _session()
    # Include completed earlier turns so the history-summary path runs;
    # native OMP does not apply custom focus to a split-turn prefix alone.
    for turn in (2, 3):
        state.add_message({"role": "user", "content": f"Turn {turn} " + "data " * 20})
        state.add_message({"role": "assistant", "content": f"Answer {turn} " + "data " * 20})
    controller = _controller(customInstructions="PRESERVE_SENTINEL", keepRecentTokens=30)
    document = load_preset_document(controller.config.preset)
    document["request_view"] = ["soft"]
    controller.recipe = build_recipe(controller.config, document)
    runtime = OmpFakeRuntime()
    controller.build_request_view(state, target=_target(), runtime=runtime, client=object())
    assert runtime.summary_requests
    assert any("Additional focus: PRESERVE_SENTINEL" in str(message["content"])
               for message in runtime.summary_requests[0])
    finished = [e for e in state.lifecycle_events if e["type"] == "compaction_finished"]
    assert len(finished) == 1
    assert finished[0]["payload"]["reason"] == "request"
    assert finished[0]["payload"]["stages"][0]["status"] == "committed"


def test_request_remote_stage_receives_native_port_and_model_dependencies():
    from types import SimpleNamespace
    from .test_remote_dispatch import DummyPort

    class Port(DummyPort):
        def compact(self, context):
            assert context.reason == "request"
            assert context.custom_instructions == "PRESERVE_SENTINEL"
            assert context.max_input_tokens == 120000
            assert context.max_output_tokens == 10000
            assert context.supports_images is True
            return super().compact(context)

    port = Port("openai", "chat", ["gpt-test"])
    client = object()

    class PortRuntime(FakeRuntime):
        def compaction_port(self, *, client, model):
            assert client is expected_client and model == "gpt-test"
            return port

    expected_client = client
    state = _session()
    controller = _controller(methodOrder=["remote"], remoteEnabled=True, customInstructions="PRESERVE_SENTINEL")
    document = load_preset_document(controller.config.preset)
    document["request_view"] = ["remote"]
    controller.recipe = build_recipe(controller.config, document)
    conductor = SimpleNamespace(config={"providers": {"models": [{
        "id": "gpt-test", "max_input_tokens": 120000, "max_output_tokens": 10000,
    }]}})
    view = controller.build_request_view(
        state, target=_target(), runtime=PortRuntime(), client=client, conductor=conductor, supports_images=True,
    )
    assert port.called
    assert state.compaction_state.records[-1].reason == "request"
    assert any("bb_native_compaction" in message for message in view)
    finished = [e for e in state.lifecycle_events if e["type"] == "compaction_finished"]
    assert len(finished) == 1
    assert finished[0]["payload"]["stages"][0]["status"] == "committed"


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("enabled", [True, False])
def test_request_only_recipe_runs_prune_without_enabling_disabled_recipes(preset, enabled):
    state = _eligible_tool_history()
    state.add_message({"role": "user", "content": "Next turn"})
    history = copy.deepcopy(state.provider_messages)
    controller = CompactionController({"compaction": {"enabled": enabled, "preset": preset}})
    document = load_preset_document(preset)
    document["pipeline"]["stages"] = [s for s in document["pipeline"]["stages"] if s["id"] == "prune"]
    controller.recipe = build_recipe(controller.config, document)
    assert controller.recipe.order == ()
    assert controller.active is enabled
    runtime = FakeRuntime()
    view = controller.prepare_request(
        state, conductor=None, runtime=runtime, client=object(), model="gpt-test", turn_index=1,
    )
    descriptor, _ = provider_router.get_runtime_descriptor("openai/gpt-4o-mini")
    controller.finish_user_turn(
        state, conductor=None, runtime=provider_registry.create_runtime(descriptor),
        client=None, model="gpt-4o-mini", turn_index=1,
    )
    view = controller.build_request_view(state, target=_target())
    assert not runtime.summary_requests
    finished = [e for e in state.lifecycle_events if e["type"] == "compaction_finished"]
    if enabled:
        assert view[2]["content"] == "[Old tool result content cleared]"
        assert len(finished) == 1
        assert finished[0]["payload"]["reason"] == "request"
        assert [s["id"] for s in finished[0]["payload"]["stages"]] == ["prune"]
        assert finished[0]["payload"]["stages"][0]["status"] == "edited"
    else:
        assert view == history
        assert not state.compaction_state.records
        assert not state.lifecycle_events
    assert state.provider_messages == history


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize(("original_text", "wrap"), [("\nFinish the inspection.", True), ("\ufeff", False), ("\x1c", True), (" \n\t", False)])
def test_overflow_queued_user_reminder_is_ephemeral_and_not_summary_history(preset, original_text, wrap):
    from types import SimpleNamespace
    from breadboard_engine.compaction.state import ProjectionTarget
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext

    state = _tool_history()
    state.add_message({"role": "user", "content": original_text})
    history = copy.deepcopy(state.provider_messages)
    target = ProjectionTarget("openai", "responses", "gpt-test")
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    controller.compact(state, reason="overflow", target=target, runtime=FakeRuntime(), client=object())
    view = controller.build_request_view(state, target=target)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    config = {"provider_tools": {"openai": {"responses_stateful": False}}}
    context = ProviderRuntimeContext(state, config)
    payload = runtime._request_payload(model="gpt-test", messages=view, tools=None, context=context)
    expected = "\n".join(("<system-reminder>", "The user sent the following message:", original_text, "",
                          "Please address this message and continue with your tasks.", "</system-reminder>")) if wrap else original_text
    assert payload["input"][-1]["content"][0]["text"] == expected
    assert view[-1]["content"][0]["text"] == original_text
    assert state.provider_messages == history
    summary = ProviderRuntimeContext(state, config, extra={"compaction_summary": True})
    assert runtime._request_payload(model="gpt-test", messages=view, tools=None, context=summary)["input"][-1]["content"][0]["text"] == original_text
    restored = SessionState("ws", "image", {})
    restored.provider_messages = copy.deepcopy(view)
    next_turn = ProviderRuntimeContext(restored, config)
    assert runtime._request_payload(model="gpt-test", messages=view, tools=None, context=next_turn)["input"][-1]["content"][0]["text"] == original_text


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("followup", ["native_assistant", "identical_user"])
def test_queued_replay_reminder_expires_when_canonical_history_advances(preset, followup):
    from types import SimpleNamespace
    from breadboard_engine.compaction.state import ProjectionTarget
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext

    state = _tool_history()
    original = "Finish the inspection."
    state.add_message({"role": "user", "content": original})
    target = ProjectionTarget("openai", "responses", "gpt-test")
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    controller.compact(state, reason="overflow", target=target, runtime=FakeRuntime(), client=object())
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(state, {"provider_tools": {"openai": {"responses_stateful": False}}})
    view = controller.build_request_view(state, target=target)
    first = runtime._request_payload(model="gpt-test", messages=view, tools=None, context=context)
    assert "<system-reminder>" in first["input"][-1]["content"][0]["text"]
    if followup == "native_assistant":
        state.add_message({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
        ]})
        state.add_message({"role": "tool", "tool_call_id": "c2", "content": "done"})
    else:
        state.add_message({"role": "user", "content": original})
    view = controller.build_request_view(state, target=target)
    payload = runtime._request_payload(model="gpt-test", messages=view, tools=None, context=context)
    texts = [part["text"] for item in payload["input"] for part in item.get("content", []) if isinstance(part, dict) and "text" in part]
    assert original in texts
    assert all("<system-reminder>" not in text for text in texts)
