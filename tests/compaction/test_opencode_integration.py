"""OpenCode lifecycle and provider-boundary regression coverage."""
from __future__ import annotations

import copy

import pytest

from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.presets import build_recipe, load_preset_document
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtime import provider_registry
from breadboard_engine.state.session_state import SessionState

from .test_controller import FakeRuntime, _controller, _session, _target

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
def test_plugin_overflow_uses_failing_request_bounds(usage, limit, chars, error_format):
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
    sufficient = not (limit == 160000 and chars == 500000)
    assert bool(runtime.summary_requests) is not sufficient
    assert [r.method for r in state.compaction_state.records] == (
        ["recovery"] if sufficient else ["recovery", "summary"]
    )
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
def test_request_prune_runs_only_at_user_turn_start_and_reports_request_reason(preset):
    state = _eligible_tool_history()
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    history = copy.deepcopy(state.provider_messages)
    first = controller.build_request_view(state, target=_target(), turn_index=1)
    assert first == history
    assert not state.compaction_state.records
    event = state.lifecycle_events[-1]
    assert event["type"] == "compaction_finished"
    assert event["payload"]["reason"] == "request"
    assert event["payload"]["stages"][0]["status"] == "noop"
    assert "phase" in event["payload"]["stages"][0]["detail"]
    state.add_message({"role": "user", "content": "Next turn"})
    second = controller.build_request_view(state, target=_target(), turn_index=2)
    assert second[2]["content"] == "[Old tool result content cleared]"
    record = state.compaction_state.records[-1]
    assert record.reason == "request"
    event = state.lifecycle_events[-1]
    assert event["payload"]["reason"] == "request"
    assert event["payload"]["stages"][0]["id"] == "prune"
    assert event["payload"]["stages"][0]["status"] == "edited"
    assert event["payload"]["stages"][0]["detail"] is None
    assert state.provider_messages[:-1] == history


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
