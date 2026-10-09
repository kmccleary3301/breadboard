"""Event-view projection and failure routing not representable by one oracle case."""
from __future__ import annotations

from dataclasses import replace

import pytest

from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, SummaryResponse, load_compaction_config
from breadboard_engine.compaction.pipeline import Pipeline, Stage
from breadboard_engine.compaction.presets import build_recipe
from breadboard_engine.compaction.primitives.event_selection import safe_indices

TARGET = ProjectionTarget("test", "test", "test")


def context(messages, responses, *, reason="manual", state=None, **settings):
    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "openhands_sdk@1.47.0", **settings}))

    class Summaries:
        def __init__(self):
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return SummaryResponse(value)

    ctx = CompactionContext(messages=messages, state=state or CompactionState(), settings=recipe.settings,
                            reason=reason, target=TARGET, context_window=200000, tokens_before=len(messages),
                            summarizer=Summaries())
    return recipe, ctx


def turns(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Turn {i}"} for i in range(n)]


def test_hard_reset_removes_system_and_round_trips_ledger():
    messages = [{"role": "system", "content": "protected system"}, *turns(10)]
    recipe, ctx = context(messages, [RuntimeError("balanced failed"), "reset"])
    outcome = recipe.pipeline.run(ctx, target_tokens=120)
    assert [s.status for s in outcome.stages] == ["failed", "committed"]
    assert outcome.records[-1].reset_context
    restored = CompactionState.from_list(ctx.state.to_list())
    assert restored.project(messages, TARGET) == [{"role": "user", "content": "reset"}]
    assert messages[0]["content"] == "protected system"


def test_keep_zero_can_forget_system_head():
    messages = [{"role": "system", "content": "system"}, *turns(10)]
    recipe, ctx = context(messages, ["summary"], keep_first=0)
    outcome = recipe.pipeline.run(ctx, target_tokens=120)
    assert outcome.records[0].reset_context
    assert not any(m["role"] == "system" for m in ctx.state.project(messages, TARGET))


def test_preserving_projected_prefix_never_revives_forgotten_history():
    messages = turns(12)
    recipe, ctx = context(messages, ["first"], max_size=10)
    first = recipe.pipeline.run(ctx, target_tokens=120).records[0]
    messages.extend(turns(12))
    preserved = ctx.state.project(messages, TARGET, coalesce=False)[:4]
    recipe, second = context(messages, ["second"], keep_first=4, state=ctx.state)
    outcome = recipe.pipeline.run(second, target_tokens=120)
    assert outcome.compacted
    assert outcome.records[0].prefix_end == first.prefix_end
    view = second.state.project(messages, TARGET, coalesce=False)
    assert view[:4] == preserved
    assert outcome.records[0].first_kept_index >= first.first_kept_index


def test_until_boundary_stops_first_condensation_and_does_not_fall_through_error():
    recipe, ctx = context(turns(10), ["first"])
    first = recipe.pipeline.stages[recipe.pipeline.order[0]]
    second = replace(first, id="second")
    pipeline = Pipeline([first, second], "until_boundary", len)
    outcome = pipeline.run(ctx, target_tokens=1)
    assert [s.status for s in outcome.stages] == ["committed", "noop"]
    assert len(ctx.summarizer.requests) == 1
    _, failed = context(turns(10), [RuntimeError("downstream")])
    from breadboard_engine.compaction.primitives.event_selection import NoCondensationAvailableException
    with pytest.raises(NoCondensationAvailableException, match="Summarization LLM call failed: downstream") as error:
        Pipeline([replace(first, failure_next_on=None), second], "until_boundary", len).run(failed, target_tokens=1)
    assert [s.status for s in error.value.compaction_outcome.stages] == ["failed", "noop"]
    assert not failed.state.records
    assert len(failed.summarizer.requests) == 1


def test_token_trigger_reports_missing_input_cap_and_honors_agent_cap():
    recipe, ctx = context([{"role": "user", "content": "X" * 84}], ["unused"], max_tokens=25)
    from breadboard_engine.compaction.primitives.accounting import OccupancyInput
    from breadboard_engine.compaction.primitives.triggers import TriggerInput
    data = TriggerInput(OccupancyInput(ctx.messages, None, True), 200000)
    pressure = recipe.pressure(data, ctx.messages)
    assert not pressure.fires and "missing max_input_tokens" in pressure.source
    pressure = recipe.pressure(replace(data, max_input_tokens=20), ctx.messages)
    assert pressure.to_dict() == {"fires": True, "tokens": 21, "limit": 20, "severity": "hard"}


def test_thinking_loop_extends_past_completed_tool_pair():
    def action(call, thinking=False):
        return {"role": "assistant", "tool_calls": [{"id": call}], "bb_event": {"thinking": thinking}}
    events = [action("a", True), {"role": "tool", "tool_call_id": "a"}, action("b"), {"role": "tool", "tool_call_id": "b"}, {"role": "user"}]
    assert safe_indices(events) == [0, 4, 5]


def test_unknown_native_key_lists_all_accepted_keys():
    with pytest.raises(ValueError, match="accepted keys") as error:
        load_compaction_config({"enabled": True, "preset": "openhands_sdk@1.47.0", "unexpected": 1})
    for key in ("max_size", "keep_first", "max_tokens", "minimum_progress", "hard_context_reset_max_retries", "hard_context_reset_context_scaling"):
        assert key in str(error.value)


def session(messages, **metadata):
    from breadboard_engine.state.session_state import SessionState

    state = SessionState("ws", "image", {})
    for message in messages:
        state.add_message(message)
    for key, value in metadata.items():
        state.set_provider_metadata(key, value)
    return state


def test_production_threshold_uses_declared_estimator_and_configured_route_limits():
    from types import SimpleNamespace
    from breadboard_engine.compaction.controller import CompactionController

    class SummaryRuntime:
        def __init__(self):
            self.calls = 0

        def invoke(self, **kwargs):
            from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
            self.calls += 1
            assert kwargs["context"].extra["compaction_summary"]
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="summary")], raw_response=None, model=kwargs["model"])

    controller = CompactionController({"compaction": {"enabled": True, "preset": "openhands_sdk@1.47.0", "max_tokens": 25, "keep_first": 1}})
    state = session([{"role": "user" if i % 2 == 0 else "assistant", "content": "X" * 20} for i in range(6)])
    runtime = SummaryRuntime()
    conductor = SimpleNamespace(config={"providers": {"models": [{"model_id": "first", "max_input_tokens": 1000, "max_output_tokens": 100}]}})
    view = controller.prepare_request(state, conductor=conductor, runtime=runtime, client=None, model="first", turn_index=1)
    assert runtime.calls == 1 and state.compaction_state.records
    assert all("bb_event" not in message for message in view)
    assert all("bb_event" not in message for record in state.compaction_state.records for message in record.summary_messages)
    assert state.compaction_state.records[-1].details["event_metadata"]
    state.set_provider_metadata("max_input_tokens", 20)
    state.set_provider_metadata("max_output_tokens", 10)
    assert controller.resolve_model_limits(state, conductor, "first") == (20, 10)
    state.set_provider_metadata("max_input_tokens", None)
    state.set_provider_metadata("max_output_tokens", None)
    conductor.config["providers"]["models"].append({"model_id": "second", "max_input_tokens": 40, "max_output_tokens": 5})
    assert controller.resolve_model_limits(state, conductor, "second") == (40, 5)


def test_hard_threshold_exhaustion_propagates_original_failure_before_request():
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.primitives.event_selection import NoCondensationAvailableException

    class FailingSummary:
        def __init__(self):
            self.calls = 0

        def complete(self, request):
            self.calls += 1
            raise RuntimeError("balanced original" if self.calls == 1 else "reset failure")

    summaries = FailingSummary()
    controller = CompactionController({"compaction": {"enabled": True, "preset": "openhands_sdk@1.47.0", "max_tokens": 25, "keep_first": 1}}, summary_model=summaries)
    state = session([{"role": "user" if i % 2 == 0 else "assistant", "content": "X" * 20} for i in range(6)], max_input_tokens=1000)
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    with pytest.raises(NoCondensationAvailableException, match="Summarization LLM call failed: balanced original") as error:
        controller.prepare_request(state, conductor=None, runtime=None, client=None, model="test", turn_index=1)
    assert summaries.calls == 6
    assert not state.compaction_state.records
    assert [stage.status for stage in error.value.compaction_outcome.stages] == ["failed", "failed"]
    assert events[-1][0] == "compaction_finished" and events[-1][1]["status"] == "failed"
    assert len(events[-1][1]["stages"]) == 2


@pytest.mark.parametrize("preset", ["openhands_sdk@1.47.0", "omp@18.4.5", "pi@0.73.1"])
def test_event_pressure_does_not_require_model_window(preset):
    from breadboard_engine.compaction.controller import CompactionController

    options = {"max_size": 10} if preset.startswith("openhands") else {}
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset, **options}})
    assert controller.should_trigger_threshold(session(turns(11)), context_window=None) == preset.startswith("openhands")


def test_user_blocks_and_summary_blocks_do_not_add_token_separators():
    from breadboard_engine.compaction.primitives.accounting import chars_div4_floor
    from breadboard_engine.compaction.primitives.event_messages import wire_messages, summary_message

    for first in ({"role": "user", "content": "X" * 19}, summary_message("X" * 19)):
        view = wire_messages([first, {"role": "user", "content": "Y" * 20}])
        assert view == [{"role": "user", "content": [{"type": "text", "text": "X" * 19}, {"type": "text", "text": "Y" * 20}]}]
        assert chars_div4_floor(view) == 9


def test_request_stage_ids_run_on_each_request_and_emit_all_results():
    from types import SimpleNamespace
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.presets import load_preset_document

    controller = CompactionController({"compaction": {"enabled": True, "preset": "openhands_sdk@1.47.0"}})
    document = load_preset_document(controller.config.preset)
    document["request_view"] = ["hard_context_reset"]
    document["pipeline"]["stages"][1]["when"] = {"reasons": ["request"]}
    controller.recipe = build_recipe(controller.config, document)
    events = []
    state = session(turns(3))
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))

    class Summary:
        def complete(self, request):
            return SummaryResponse("request summary")

    controller.summary_model = Summary()
    for _ in range(2):
        controller.prepare_request(state, conductor=SimpleNamespace(config={}), runtime=None, client=None, model="test", turn_index=1)
    finished = [payload for event, payload in events if event == "compaction_finished"]
    assert len(finished) == 2
    assert all(item["reason"] == "request" for item in finished)
    assert all([stage["id"] for stage in item["stages"]] == ["hard_context_reset"] for item in finished)
    assert all(item["stages"][0]["status"] == "committed" for item in finished)


def test_event_threshold_prepares_request_without_provider_window():
    from breadboard_engine.compaction.controller import CompactionController

    class Summary:
        def complete(self, request):
            return SummaryResponse("event summary")

    controller = CompactionController({"compaction": {"enabled": True, "preset": "openhands_sdk@1.47.0", "max_size": 10}}, summary_model=Summary())
    state = session(turns(11))
    view = controller.prepare_request(state, conductor=None, runtime=None, client=None, model="test", turn_index=1)
    assert state.compaction_state.records
    assert len(view) < len(state.provider_messages)


def test_soft_failure_keeps_request_view_and_reports_failed_stage():
    from breadboard_engine.compaction.controller import CompactionController

    controller = CompactionController({"compaction": {"enabled": True, "preset": "openhands_sdk@1.47.0", "max_size": 10, "minimum_progress": 0.9}})
    messages = turns(11)
    state = session(messages)
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    view = controller.prepare_request(state, conductor=None, runtime=None, client=None, model="test", turn_index=1)
    assert view == messages and not state.compaction_state.records
    assert [stage["status"] for stage in events[-1][1]["stages"]] == ["failed", "noop"]


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
    from breadboard_engine.compaction.presets import load_preset_document
    from breadboard_engine.compaction.state import ProjectionTarget

    class Cancel:
        def complete(self, request):
            raise CompactionCancelled("stop")

    controller = CompactionController({"compaction": {"enabled": True, "preset": "openhands_sdk@1.47.0"}}, summary_model=Cancel())
    document = load_preset_document(controller.config.preset)
    document["request_view"] = ["hard_context_reset", "omp_prune", "balanced_summary"]
    document["pipeline"]["stages"][1]["when"] = {"reasons": ["request"]}
    controller.recipe = build_recipe(controller.config, document)
    state = session(turns(3))
    events = []
    state.record_lifecycle_event = lambda event, payload, **kwargs: events.append((event, payload))
    with pytest.raises(CompactionCancelled, match="stop"):
        controller.build_request_view(state, target=ProjectionTarget("unknown", "unknown", "test"))
    finished = [payload for event, payload in events if event == "compaction_finished"]
    assert len(finished) == 1 and finished[0]["status"] == "cancelled"
    assert [stage["id"] for stage in finished[0]["stages"]] == ["hard_context_reset", "balanced_summary"]
