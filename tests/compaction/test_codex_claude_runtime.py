"""Exercise preset decisions through production controller and native ports."""
from copy import deepcopy
from types import SimpleNamespace
from dataclasses import replace

import pytest

from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, load_compaction_config
from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.presets import build_recipe, load_preset_document
from breadboard_engine.compaction.remote.openai import OpenAIResponsesCompactionPort
from breadboard_engine.compaction.state import NATIVE_MARKER_KEY
from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
from breadboard_engine.state.session_state import SessionState


class Runtime:
    descriptor = SimpleNamespace(provider_id="openai", default_api_variant="chat", runtime_id="openai_chat")

    def __init__(self):
        self.models = []

    def invoke(self, *, client, model, messages, tools, stream, context):
        self.models.append(model)
        assert tools is None and not stream and context.extra["compaction_summary"]
        return ProviderResult(messages=[ProviderMessage(role="assistant", content="checkpoint")],
                              raw_response=None, model=model)


def session(messages, usage):
    state = SessionState("ws", "image", {})
    for message in messages:
        state.add_message(message)
    state.set_provider_metadata("usage", {"total_tokens": usage, "input_tokens": usage})
    return state


@pytest.mark.parametrize("preset", ["codex@0.139.0", "claude_code@2.1.63"])
def test_controller_honors_summary_model_and_does_not_reuse_stale_usage(preset):
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": preset, "contextWindow": 200000, "summary_model": "summary-model",
    }})
    runtime = Runtime()
    state = session([{"role": "user", "content": "task"}, {"role": "assistant", "content": "work"}], 190000)
    first = controller.prepare_request(state, conductor=None, runtime=runtime, client=None, model="conversation-model", turn_index=1)
    second = controller.prepare_request(state, conductor=None, runtime=runtime, client=None, model="conversation-model", turn_index=1)
    assert runtime.models == ["summary-model"]
    assert len(state.compaction_state.records) == 1
    assert first == second
    assert all(set(message) == {"role", "content"} for message in second)


def test_claude_request_masking_follows_threshold_without_summary():
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "claude_code@2.1.63", "contextWindow": 200000,
    }})
    runtime = Runtime()
    calls = [{"id": f"call_{i}", "type": "function", "function": {"name": "Read", "arguments": "{}"}}
             for i in range(4)]
    state = session([
        {"role": "user", "content": "task"}, {"role": "assistant", "content": "", "tool_calls": calls},
        *({"role": "tool", "tool_call_id": call["id"], "content": "x" * 120000} for call in calls),
        {"role": "assistant", "content": "ready"},
    ], 150000)
    first = controller.prepare_request(state, conductor=None, runtime=runtime, client=None, model="conversation-model", turn_index=1)
    second = controller.prepare_request(state, conductor=None, runtime=runtime, client=None, model="conversation-model", turn_index=1)
    assert first[2]["content"] == "[Old tool result content cleared]"
    assert first[3]["content"] == "x" * 120000
    assert state.provider_messages[2]["content"] == "x" * 120000
    assert first == second
    assert not runtime.models
    assert len(state.compaction_state.records) == 1
    assert state.compaction_state.records[0].reason == "request"


@pytest.mark.parametrize("preset", [None, "codex@0.139.0", "claude_code@2.1.63"])
def test_request_view_runs_after_threshold_check(monkeypatch, preset):
    config = {"enabled": True}
    if preset is not None:
        config["preset"] = preset
    controller = CompactionController({"compaction": config})
    order = []
    original = controller.build_request_view

    def threshold(*args, **kwargs):
        order.append("threshold")
        return False

    def request(*args, **kwargs):
        order.append("request")
        return original(*args, **kwargs)

    monkeypatch.setattr(controller, "should_trigger_threshold", threshold)
    monkeypatch.setattr(controller, "build_request_view", request)
    controller.prepare_request(session([{"role": "user", "content": "task"}], 0),
                               conductor=None, runtime=Runtime(), client=None, model="model", turn_index=1)
    assert order == ["threshold", "request"]


@pytest.mark.parametrize("max_output,summary_count", [(4096, 0), (32000, 1)])
def test_claude_threshold_uses_actual_output_cap(max_output, summary_count):
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "claude_code@2.1.63", "contextWindow": 200000,
    }})
    runtime = Runtime()
    state = session([{"role": "user", "content": "task"}, {"role": "assistant", "content": "work"}], 170000)
    state.set_provider_metadata("max_output_tokens", max_output)
    controller.prepare_request(state, conductor=None, runtime=runtime, client=None, model="model", turn_index=1)
    assert len(runtime.models) == summary_count


def test_request_pass_emits_one_finished_event_for_all_stage_results(monkeypatch):
    controller = CompactionController({"compaction": {"enabled": True, "preset": "claude_code@2.1.63"}})
    controller.recipe = replace(controller.recipe, request_view=("microcompact", "microcompact"))
    state = session([{"role": "user", "content": "task"}], 0)
    events = []
    monkeypatch.setattr(state, "record_lifecycle_event", lambda event, payload, **kwargs: events.append((event, payload)))
    controller.prepare_request(state, conductor=None, runtime=Runtime(), client=None, model="model", turn_index=1)
    finished = [payload for event, payload in events if event == "compaction_finished"]
    assert len(finished) == 1
    assert finished[0]["reason"] == "request"
    assert [stage["id"] for stage in finished[0]["stages"]] == ["microcompact", "microcompact"]


def test_native_port_owns_retention_and_counts_each_text_part_without_charging_images():
    recipe = build_recipe(load_compaction_config({
        "enabled": True, "preset": "codex@0.139.0", "features": ["RemoteCompactionV2"],
    }))
    posts = []

    def post(url, payload, headers):
        posts.append(payload)
        return [{"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "enc"}},
                {"type": "response.completed", "response": {"usage": {}}}]

    port = OpenAIResponsesCompactionPort(model="model", http_poster=post)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"},
                                       {"type": "input_image", "image_url": "https://example.com/image.png"}]},
        {"role": "developer", "content": "not retained"},
        {"role": "system", "content": "not retained"},
        {"role": "user", "content": "x" * 255996},
    ]
    target = ProjectionTarget("openai", "responses", "model")
    context = CompactionContext(messages, CompactionState(), recipe.settings, "manual", target,
                                1000000, recipe.count(messages), remote_ports=(port,),
                                native_retention=recipe.native_retention)
    outcome = recipe.pipeline.run(context, target_tokens=900000)
    assert outcome.compacted
    items = context.projected()[0][NATIVE_MARKER_KEY]["items"]
    assert len(items) == 3
    assert items[0] == {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "a"},
        {"type": "input_image", "image_url": "https://example.com/image.png"},
    ]}
    assert items[1]["content"] == [{"type": "input_text", "text": "x" * 255996}]
    assert items[2] == {"type": "compaction", "encrypted_content": "enc"}
    messages.append({"role": "user", "content": "follow up"})
    recipe.pipeline.run(context, target_tokens=900000)
    second = context.projected()[0][NATIVE_MARKER_KEY]["items"]
    assert sum(item.get("type") == "compaction" for item in second) == 1
    assert sum(item.get("role") == "user" for item in second) <= 3
    assert len(posts) == 2
    assert sum(item.get("role") == "user" for item in posts[1]["input"]) == 3


def test_view_only_inline_renderer_must_be_last():
    config = load_compaction_config({"enabled": True, "preset": "claude_code@2.1.63"})
    doc = deepcopy(load_preset_document(config.preset))
    doc["request_view"] = ["omp_inline_snapcompact", "microcompact"]
    with pytest.raises(ValueError, match="must be the final"):
        build_recipe(config, doc)
    doc["request_view"] = ["microcompact", "omp_inline_snapcompact"]
    assert build_recipe(config, doc).request_view == ("microcompact", "omp_inline_snapcompact")
