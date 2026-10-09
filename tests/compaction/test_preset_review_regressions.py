"""Production pipeline/controller regressions requested by independent review."""

import copy
import json
from dataclasses import replace

import pytest

from breadboard_engine.compaction import (
    CompactionContext, CompactionState, ProjectionTarget, SummaryResponse, load_compaction_config,
)
from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.pipeline import AlgorithmStep, ComposedStep, Pipeline, Stage
from breadboard_engine.compaction.presets import build_recipe, load_preset_document
from breadboard_engine.compaction.primitives.selectors import Selection
from breadboard_engine.state.session_state import SessionState

TARGET = ProjectionTarget("oracle", "oracle", "oracle")
HEADINGS = "\n".join(("## Decisions", "## Open TODOs", "## Constraints/Rules",
                       "## Pending user asks", "## Exact identifiers"))


class Summaries:
    def __init__(self, *texts):
        self.texts = iter(texts)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return SummaryResponse(next(self.texts))


def test_second_compaction_summarizes_the_decayed_protected_prefix():
    summaries = Summaries("first history summary", "carried history summary")
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "hermes_agent@2026.9.11", "contextWindow": 200000,
        "protect_first_n": 1, "protect_last_n": 3, "tail_mode": "legacy", "target_ratio": 0.001,
    }}, summary_model=summaries)
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "PROTECTED_SENTINEL"})
    for i in range(30):
        state.add_message({"role": "user" if i % 2 else "assistant", "content": f"original-{i} " + "x" * 4000})
    first = controller.compact(state, reason="manual", target=TARGET)
    assert first.status == "committed", first.stages
    assert "PROTECTED_SENTINEL" in json.dumps(state.compaction_state.project(state.provider_messages, TARGET))
    assert "PROTECTED_SENTINEL" not in json.dumps(summaries.requests[0].messages)
    already_summarized = state.provider_messages[first.records[-1].prefix_end]["content"]
    for i in range(10):
        state.add_message({"role": "user" if i % 2 else "assistant", "content": f"new-{i} " + "y" * 4000})
    second = controller.compact(state, reason="manual", target=TARGET)
    assert second.status == "committed", second.stages
    prompt = json.dumps(summaries.requests[1].messages)
    assert "PROTECTED_SENTINEL" in prompt
    assert already_summarized not in prompt
    assert "first history summary" in prompt
    assert "PROTECTED_SENTINEL" not in json.dumps(state.compaction_state.project(state.provider_messages, TARGET))


class FixedSelection:
    def __init__(self, selection):
        self.selection = selection

    def select(self, context):
        return self.selection


def run_safeguard(messages, selection, *responses, state=None, policy="strict"):
    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "openclaw@2026.9.4",
                                                  "mode": "safeguard", "identifierPolicy": policy}))
    base = recipe.pipeline.stages["summary"].step
    step = ComposedStep("summary", FixedSelection(selection), base.reducer, base.placement)
    summaries = Summaries(*responses)
    state = state or CompactionState()
    context = CompactionContext(messages, state, recipe.settings, "manual", TARGET, 200000,
                                recipe.count(state.project(messages, TARGET)), summarizer=summaries)
    outcome = Pipeline([Stage("summary", step, accept="any")], "sequence", recipe.count).run(
        context, target_tokens=200000)
    return outcome, state


def test_strict_safeguard_pipeline_rejects_missing_source_identifiers():
    messages = [{"role": "user", "content": "Track tx_987654321 at /project/secret_location"},
                {"role": "assistant", "content": "I will"}, {"role": "user", "content": "continue"}]
    outcome, state = run_safeguard(messages, Selection(first_kept_index=2, summarize=(0, 1)), HEADINGS)
    assert outcome.status == "failed"
    assert state.records == ()
    assert "tx_987654321" in outcome.stages[0].detail
    assert "/project/secret_location" in outcome.stages[0].detail


def test_safeguard_split_turn_audits_the_structural_history_only():
    messages = [{"role": "user", "content": "Review the design"},
                {"role": "assistant", "content": "Reviewed"},
                {"role": "user", "content": "Now implement the design"},
                {"role": "assistant", "content": "working"}]
    outcome, state = run_safeguard(messages, Selection(first_kept_index=3, summarize=(0, 1), turn_prefix=(2,)),
                                   HEADINGS, "Ordinary turn-prefix context without headings")
    assert outcome.status == "committed", outcome.stages
    assert HEADINGS in state.records[-1].summary
    assert "Ordinary turn-prefix context without headings" in state.records[-1].summary


class FailedEdit:
    name = "observation"

    def run(self, context):
        raise RuntimeError("render failed")


@pytest.mark.parametrize("kind", ["edited", "unavailable", "failed"])
def test_request_view_emits_every_stage_outcome(kind):
    controller = CompactionController({"compaction": {"enabled": True, "preset": "mini_swe_agent@2.4.6"}})
    if kind == "failed":
        controller.recipe = replace(controller.recipe, pipeline=Pipeline(
            [Stage("observation", AlgorithmStep(FailedEdit()))], "sequence", controller.recipe.count))
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "read"})
    state.add_message({"role": "assistant", "tool_calls": [{"id": "call", "type": "function",
                       "function": {"name": "read", "arguments": "{}"}}]})
    state.add_message({"role": "tool", "tool_call_id": "call", "content": json.dumps(
        {"returncode": 0, "output": "x" * (12000 if kind == "edited" else 100), "exception_info": None})})
    history = copy.deepcopy(state.provider_messages)
    view = controller.build_request_view(state, target=TARGET, context_window=200000)
    assert state.provider_messages == history
    finished = [event for event in state.lifecycle_events if event["type"] == "compaction_finished"]
    assert finished, state.lifecycle_events
    event = finished[-1]["payload"]
    assert event["reason"] == "request"
    assert event["status"] == ("noop" if kind == "unavailable" else kind)
    assert event["stages"][0]["id"] == "observation"
    assert event["stages"][0]["status"] == kind
    assert event["target_tokens"] == 200000
    if kind == "edited":
        assert view != history
        assert state.compaction_state.records[-1].reason == "request"
    else:
        assert view == history
        assert event["stages"][0]["detail"]
        assert state.compaction_state.records == ()


def test_merged_summary_carrier_is_ledger_only_and_survives_replay():
    summaries = Summaries("compact checkpoint")
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "hermes_agent@2026.9.11", "protect_first_n": 0,
        "protect_last_n": 3, "tail_mode": "legacy", "target_ratio": 0.001,
    }}, summary_model=summaries)
    state = SessionState("ws", "image", {})
    for i in range(20):
        state.add_message({"role": "user", "content": "turn " + "x" * 4000})
    outcome = controller.compact(state, reason="manual", target=TARGET, context_window=200000)
    assert outcome.status == "committed", outcome.stages
    record = outcome.records[-1]
    assert record.readable and record.edits and not record.summary_messages
    projected = state.compaction_state.project(state.provider_messages, TARGET)
    assert all(not any(key.startswith("_compressed_summary") for key in m) for m in projected)
    assert record.details["summary_carrier_index"] >= record.first_kept_index
    restored = CompactionState.from_list(state.compaction_state.to_list())
    assert restored.latest_readable_boundary().summary == "compact checkpoint"
    assert restored.project(state.provider_messages, TARGET) == projected


def test_request_view_validates_and_runs_pipeline_stage_ids_in_order():
    config = load_compaction_config({"enabled": True, "preset": "mini_swe_agent@2.4.6"})
    document = load_preset_document(config.preset)
    document["request_view"] = ["second", "first"]
    original = document["pipeline"]["stages"][0]
    document["pipeline"]["stages"] = [{**original, "id": name, "when": {"reasons": ["request"]}}
                                        for name in ("first", "second")]
    recipe = build_recipe(config, document)
    assert recipe.request_view == ("second", "first")
    controller = CompactionController({"compaction": {"enabled": True, "preset": config.preset}})
    controller.recipe = recipe
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "no observations"})
    controller.build_request_view(state, target=TARGET)
    event = [e for e in state.lifecycle_events if e["type"] == "compaction_finished"][-1]["payload"]
    assert [stage["id"] for stage in event["stages"]] == ["second", "first"]
    document["request_view"] = ["missing"]
    with pytest.raises(ValueError, match="unknown steps"):
        build_recipe(config, document)


def test_controller_supplies_real_boundary_count_in_both_passes():
    from breadboard_engine.compaction import MessageEdit, MethodUnavailable

    observed = []

    class ObserveContext:
        name = "observation"

        def run(self, context):
            observed.append(context)
            raise MethodUnavailable("inspection only")

    controller = CompactionController({"compaction": {"enabled": True, "preset": "mini_swe_agent@2.4.6"}})
    controller.recipe = replace(controller.recipe, pipeline=Pipeline(
        [Stage("observation", AlgorithmStep(ObserveContext()))], "sequence", controller.recipe.count))
    state = SessionState("ws", "image", {})
    for i in range(5):
        state.add_message({"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"})
    context = CompactionContext(state.provider_messages, state.compaction_state, controller.settings,
                                "manual", TARGET, 200000, 100)
    state.compaction_state.append(context.new_record(
        method="summary", first_kept_index=2, summary="prior", summary_messages=[{"role": "user", "content": "prior"}],
    ), state.provider_messages)
    state.compaction_state.append(context.new_record(
        method="edit", edits=[MessageEdit(3, {**state.provider_messages[3], "content": "edited"})],
    ), state.provider_messages)
    controller.compact(state, reason="manual", target=TARGET, context_window=200000)
    controller.build_request_view(state, target=TARGET, context_window=200000)
    assert [context.reason for context in observed] == ["manual", "request"]
    for context in observed:
        assert context.prior_compactions == 1


@pytest.mark.parametrize("policy, status", [("strict", "failed"), ("off", "committed")])
def test_safeguard_audits_prior_summary_identifiers_according_to_policy(policy, status):
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "new history"},
                {"role": "user", "content": "keep"}]
    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "openclaw@2026.9.4"}))
    state = CompactionState()
    context = CompactionContext(messages, state, recipe.settings, "manual", TARGET, 200000, 100)
    previous = HEADINGS + "\nretain tx_123456789"
    state.append(context.new_record(method="summary", first_kept_index=1, summary=previous,
                                    summary_messages=[{"role": "user", "content": previous}]), messages)
    outcome, _ = run_safeguard(messages, Selection(first_kept_index=2, summarize=(1,)),
                               HEADINGS, state=state, policy=policy)
    assert outcome.status == status, outcome.stages
    if policy == "strict":
        assert "tx_123456789" in outcome.stages[0].detail
        assert state.records[-1].summary == previous


@pytest.mark.parametrize("preset, mode, headed", [
    ("openclaw@2026.9.4", "safeguard", True),
    ("openclaw@2026.9.4", "default", False),
    ("pi@0.73.1", None, False),
])
def test_real_selector_empty_history_split_turn_preserves_mode_fallback(preset, mode, headed):
    config = {"enabled": True, "preset": preset, "keepRecentTokens": 1}
    if mode is not None:
        config.update(mode=mode, identifierPolicy="off")
    recipe = build_recipe(load_compaction_config(config))
    messages = [{"role": "user", "content": "Review the design"},
                {"role": "assistant", "content": "I am working on it"}]
    summaries = Summaries("ordinary turn context")
    state = CompactionState()
    context = CompactionContext(messages, state, recipe.settings, "manual", TARGET, 200000,
                                recipe.count(messages), summarizer=summaries)
    stage = recipe.pipeline.stages[recipe.pipeline.order[0]]
    selection = stage.step.selector.select(context)
    assert selection.first_kept_index == 1
    assert selection.summarize == () and selection.turn_prefix == (0,)
    outcome = recipe.pipeline.run(context, target_tokens=200000)
    assert outcome.status == "committed", outcome.stages
    assert len(summaries.requests) == 1 and summaries.requests[0].purpose == "turn_prefix"
    summary = state.records[-1].summary
    assert "ordinary turn context" in summary
    if headed:
        expected = (
            "## Decisions\nNo prior history.\n\n## Open TODOs\nNone.\n\n"
            "## Constraints/Rules\nNone.\n\n## Pending user asks\nNone.\n\n"
            "## Exact identifiers\nNone captured."
        )
        assert summary.startswith(expected)
    else:
        assert summary.startswith("No prior history.")
        assert "## Decisions" not in summary


@pytest.mark.parametrize("after", ["observation", "omp_prune", "omp_inline_snapcompact"])
def test_loader_rejects_any_entry_after_inline_snapcompact(after):
    config = load_compaction_config({"enabled": True, "preset": "mini_swe_agent@2.4.6"})
    document = load_preset_document(config.preset)
    document["request_view"] = ["omp_inline_snapcompact", after]
    with pytest.raises(ValueError, match="omp_inline_snapcompact.*final"):
        build_recipe(config, document)


def test_controller_uses_the_stage_estimator_when_recipe_estimator_differs():
    config = load_compaction_config({"enabled": True, "preset": "openclaw@2026.9.4", "keepRecentTokens": 2})
    document = load_preset_document(config.preset)
    document["estimator"] = "bb_chars4"
    recipe = build_recipe(config, document)
    summaries = Summaries("old history", "unused prefix")
    controller = CompactionController({"compaction": {"enabled": True, "preset": config.preset}},
                                     summary_model=summaries)
    controller.recipe = recipe
    state = SessionState("ws", "image", {})
    for role, text in [("user", "old"), ("assistant", "prior"), ("user", "middle"), ("assistant", "last")]:
        state.add_message({"role": role, "content": text})
    outcome = controller.compact(state, reason="manual", target=TARGET, context_window=200000)
    assert outcome.status == "committed", outcome.stages
    assert outcome.records[-1].first_kept_index == 2
    assert len(summaries.requests) == 1


@pytest.mark.parametrize("observation_length, status", [(100, "unavailable"), (12000, "edited")])
def test_final_inline_imaging_composes_with_request_stage_outcomes(observation_length, status):
    import base64
    import io
    from PIL import Image
    from breadboard_engine.compaction.settings import SnapcompactSettings
    from breadboard_engine.compaction.snapcompact.inline import SYSTEM_STUB

    config = load_compaction_config({"enabled": True, "preset": "mini_swe_agent@2.4.6"})
    settings = replace(config.settings, snapcompact=SnapcompactSettings(system_prompt="all", inline_min_tokens=50))
    document = load_preset_document(config.preset)
    document["request_view"] = ["observation", "omp_inline_snapcompact"]
    recipe = build_recipe(replace(config, settings=settings), document)
    controller = CompactionController({"compaction": {"enabled": True, "preset": config.preset}})
    controller.settings, controller.recipe = settings, recipe
    state = SessionState("ws", "image", {})
    state.add_message({"role": "system", "content": "You are an autonomous engineering agent with extensive guidelines. " * 250})
    state.add_message({"role": "user", "content": "Inspect the output"})
    state.add_message({"role": "assistant", "tool_calls": [{"id": "call", "type": "function",
                       "function": {"name": "read", "arguments": "{}"}}]})
    state.add_message({"role": "tool", "tool_call_id": "call", "content": json.dumps(
        {"returncode": 0, "output": "x" * observation_length, "exception_info": None})})
    history = copy.deepcopy(state.provider_messages)
    view = controller.build_request_view(
        state, target=ProjectionTarget("google", "google-generative-ai", "gemini-2.5-flash"),
        supports_images=True, context_window=200000)
    assert state.provider_messages == history
    assert view[0]["content"] == SYSTEM_STUB
    image_url = next(block["image_url"]["url"] for block in view[1]["content"] if block["type"] == "image_url")
    assert Image.open(io.BytesIO(base64.b64decode(image_url.split("base64,")[1]))).format == "PNG"
    event = [event["payload"] for event in state.lifecycle_events if event["type"] == "compaction_finished"][-1]
    assert event["reason"] == "request" and event["stages"][0]["status"] == status
    if status == "edited":
        assert view[-1]["content"] == state.compaction_state.records[-1].edits[0].message["content"]
        assert view[-1]["content"] != history[-1]["content"]
    else:
        assert view[-1] == history[-1]
