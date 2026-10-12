"""Request-pass ordering and failure routing not representable by one oracle case."""
from __future__ import annotations

from dataclasses import replace

import pytest

from breadboard_engine.compaction import ProjectionTarget, load_compaction_config
from breadboard_engine.compaction.pipeline import Pipeline, Stage
from breadboard_engine.compaction.presets import build_recipe

TARGET = ProjectionTarget("test", "test", "test")


def turns(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Turn {i}"} for i in range(n)]


def session(messages, **metadata):
    from breadboard_engine.state.session_state import SessionState

    state = SessionState("ws", "image", {})
    for message in messages:
        state.add_message(message)
    for key, value in metadata.items():
        state.set_provider_metadata(key, value)
    return state


def test_request_builtins_and_stage_ids_run_in_declared_order(monkeypatch):
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.state import ProjectionTarget

    controller = CompactionController({"compaction": {"enabled": True, "preset": "omp@18.4.5", "prune": {"enabled": True}}})
    controller.recipe = replace(controller.recipe, request_view=("soft", "omp_prune", "omp_inline_snapcompact"))
    calls = []
    original_compact = controller.compact

    def inline(view, *args, **kwargs):
        calls.append("inline")
        return view

    def compact(state, **kwargs):
        calls.append((kwargs["reason"], kwargs["order"]))
        return original_compact(state, **kwargs)

    def prune(context):
        calls.append(("prune", context.reason))

    monkeypatch.setattr("breadboard_engine.compaction.controller.apply_inline_snapcompact", inline)
    monkeypatch.setattr("breadboard_engine.compaction.controller.prune_tool_results", prune)
    monkeypatch.setattr(controller, "compact", compact)
    state = session(turns(3))
    assert controller.build_request_view(state, target=ProjectionTarget("unknown", "unknown", "test"), supports_images=True) == state.provider_messages
    assert calls == [("request", ["soft"]), ("prune", "threshold"), "inline"]


@pytest.mark.parametrize("later", ["omp_prune", "soft", "omp_inline_snapcompact"])
def test_inline_request_transform_must_be_last(later):
    from breadboard_engine.compaction.presets import load_preset_document
    from breadboard_engine.compaction.params import PresetError

    config = load_compaction_config({"enabled": True, "preset": "omp@18.4.5"})
    document = load_preset_document(config.preset)
    document["request_view"] = ["omp_inline_snapcompact", later]
    with pytest.raises(PresetError, match="omp_inline_snapcompact must be the final request_view entry"):
        build_recipe(config, document)


def test_real_inline_render_survives_a_request_stage_noop():
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.presets import load_preset_document
    from breadboard_engine.compaction.snapcompact.inline import SYSTEM_STUB, SYSTEM_FRAMES_NOTE
    from breadboard_engine.compaction.state import ProjectionTarget

    controller = CompactionController({"compaction": {"enabled": True, "preset": "omp@18.4.5", "snapcompact": {"system_prompt": "all", "inline_min_tokens": 50}}})
    document = load_preset_document(controller.config.preset)
    document["request_view"] = ["soft", "omp_inline_snapcompact"]
    document["pipeline"]["stages"][4]["when"] = {"reasons": ["threshold"]}
    controller.recipe = build_recipe(controller.config, document)
    messages = [{"role": "system", "content": "You are an autonomous engineering agent with extensive guidelines. " * 250}, {"role": "user", "content": "Please implement feature X."}]
    state = session(messages)
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    view = controller.build_request_view(state, target=ProjectionTarget("google", "google-generative-ai", "test"), supports_images=True)
    assert view[0]["content"] == SYSTEM_STUB
    assert view[1]["content"][0]["text"] == SYSTEM_FRAMES_NOTE
    assert any(part.get("type") == "image_url" for part in view[1]["content"])
    assert state.provider_messages == messages and not state.compaction_state.records
    finished = [payload for event, payload in events if event == "compaction_finished"]
    assert len(finished) == 1 and finished[0]["reason"] == "request"
    assert [stage["status"] for stage in finished[0]["stages"]] == ["noop"]


def test_request_pass_aggregates_interleaved_stage_results_once():
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.presets import load_preset_document
    from breadboard_engine.compaction.state import ProjectionTarget

    controller = CompactionController({"compaction": {"enabled": True, "preset": "omp@18.4.5", "prune": {"enabled": True}}})
    document = load_preset_document(controller.config.preset)
    document["request_view"] = ["soft", "omp_prune", "remote"]
    for index in (0, 4):
        document["pipeline"]["stages"][index]["when"] = {"reasons": ["threshold"]}
    controller.recipe = build_recipe(controller.config, document)
    state = session(turns(3))
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    view = controller.build_request_view(state, target=ProjectionTarget("unknown", "unknown", "test"))
    finished = [payload for event, payload in events if event == "compaction_finished"]
    assert view == state.provider_messages
    assert len(finished) == 1 and finished[0]["reason"] == "request"
    assert [stage["id"] for stage in finished[0]["stages"]] == ["soft", "remote"]
    assert [stage["status"] for stage in finished[0]["stages"]] == ["noop", "noop"]


def test_cancelled_request_pass_reports_all_stage_ids_once():
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.methods import CompactionCancelled
    from breadboard_engine.compaction.pipeline import AlgorithmStep

    class Cancel:
        name = "cancel"

        def run(self, context):
            raise CompactionCancelled("stop")

    controller = CompactionController({"compaction": {"enabled": True, "preset": "omp@18.4.5"}})
    controller.recipe = replace(
        controller.recipe, request_view=("cancel", "omp_prune", "soft"),
        pipeline=Pipeline([Stage("cancel", AlgorithmStep(Cancel())), controller.recipe.pipeline.stages["soft"]],
                          "sequence", controller.recipe.count),
    )
    state = session(turns(3))
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    with pytest.raises(CompactionCancelled, match="stop"):
        controller.build_request_view(state, target=ProjectionTarget("unknown", "unknown", "test"))
    finished = [payload for event, payload in events if event == "compaction_finished"]
    assert len(finished) == 1 and finished[0]["status"] == "cancelled"
    assert [stage["id"] for stage in finished[0]["stages"]] == ["cancel", "soft"]


@pytest.mark.parametrize("disabled_order", [{"strategy": "off"}, {"methodOrder": []}])
def test_explicit_empty_omp_order_disables_request_builtins(disabled_order):
    from breadboard_engine.compaction.controller import CompactionController

    controller = CompactionController({"compaction": {
        "enabled": True, **disabled_order, "prune": {"enabled": True},
        "snapcompact": {"systemPrompt": "all", "inlineMinTokens": 1},
    }})
    messages = [{"role": "system", "content": "long system " * 100}, {"role": "user", "content": "task"}]
    state = session(messages)
    events = []
    state.record_lifecycle_event = lambda *args, **kwargs: events.append(args)
    assert not controller.active
    assert controller.build_request_view(state, target=TARGET, supports_images=True) == messages
    assert not events and not state.compaction_state.records


@pytest.mark.parametrize("profile_output,metadata_output", [(10000, 32000), (10000, "invalid"), (None, "invalid")])
def test_threshold_and_context_share_validated_route_output_cap(profile_output, metadata_output):
    from types import SimpleNamespace
    from breadboard_engine.compaction import MethodUnavailable
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.pipeline import AlgorithmStep

    observed = []

    class Observe:
        name = "observe"

        def run(self, context):
            observed.append(context)
            raise MethodUnavailable("inspection only")

    controller = CompactionController({"compaction": {"enabled": True, "preset": "opencode@1.2.17"}})
    controller.recipe = replace(
        controller.recipe, request_view=(), order=("observe",),
        pipeline=Pipeline([Stage("observe", AlgorithmStep(Observe()))], "sequence", controller.recipe.count),
    )
    state = session(turns(3), context_window=200000, max_output_tokens=metadata_output, usage={"total_tokens": 195000})
    state._episode_provider_profile = SimpleNamespace(context_window=200000, max_output_tokens=profile_output)
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    controller.finish_assistant_step(state, conductor=None, runtime=None, client=None, model="test", turn_index=1)
    assert len(observed) == 1 and observed[0].max_output_tokens == profile_output
    started = next(payload for event, payload in events if event == "compaction_started")
    assert started["target_tokens"] == 200000 - (profile_output or 32000)
