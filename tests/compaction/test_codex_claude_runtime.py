"""Exercise preset decisions through production controller and native ports."""
from types import SimpleNamespace

import pytest

from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, load_compaction_config
from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.presets import build_recipe
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


def test_claude_request_masking_runs_before_threshold_without_summary():
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "claude_code@2.1.63", "contextWindow": 200000,
    }})
    runtime = Runtime()
    calls = [{"id": f"call_{i}", "type": "function", "function": {"name": "Read", "arguments": "{}"}}
             for i in range(4)]
    state = session([
        {"role": "user", "content": "task"}, {"role": "assistant", "content": "", "tool_calls": calls},
        *({"role": "tool", "tool_call_id": call["id"], "content": "x" * 120000} for call in calls),
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
