"""Production pipeline/controller regressions requested by independent review."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from breadboard_engine.compaction import (
    CompactionContext, CompactionState, ProjectionTarget, SummaryResponse, load_compaction_config,
    settings_from_config,
)
from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.pipeline import AlgorithmStep, ComposedStep, Pipeline, Stage
from breadboard_engine.compaction.presets import build_recipe, load_preset_document
from breadboard_engine.compaction.primitives.selectors import Selection
from breadboard_engine.state.session_state import SessionState
from breadboard_engine.compaction.params import BuildEnv, Params
from breadboard_engine.compaction.primitives.budget_selectors import LatestToolOutputs
from breadboard_engine.compaction.primitives.history_selection import LargestFirstMasking, ProtectedToolOutputs
from breadboard_engine.compaction.primitives.message_reduction import MaskOutputs
from breadboard_engine.compaction.transcript import check_tool_pairing, result_call_ids
from breadboard_engine.provider.adapters import AnthropicAdapter
from breadboard_engine.provider.anthropic_replay import replay_messages
from breadboard_engine.provider.contracts import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime

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
    config = load_compaction_config({"enabled": True, "preset": "omp@18.4.5"})
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

    controller = CompactionController({"compaction": {"enabled": True, "preset": "omp@18.4.5"}})
    controller.recipe = replace(controller.recipe, order=("observation",), request_view=("observation",), pipeline=Pipeline(
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


@pytest.mark.parametrize("max_output_tokens", [None, 32000])
def test_codex_threshold_is_window_fraction_not_preempted_by_output_reserve(max_output_tokens):
    """Codex compacts at 90% of the window whatever the output cap.

    At the RL window W=131072 with a 32000 output cap, a reserve-style rule
    (prompt + output > W) would act at 99072, before Codex's own trigger at
    floor(0.9 * W) = 117964 (openai_models.rs:430-441, compare ``>=``). The
    preset must keep Codex's accounting so overflow, terminal for Codex, is
    reached only where stock Codex reaches it.
    """
    controller = CompactionController({"compaction": {"enabled": True, "preset": "codex@0.139.0"}})
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "task"})
    state.add_message({"role": "assistant", "content": "working"})

    def fires(total):
        usage = {"input_tokens": total, "output_tokens": 0, "total_tokens": total}
        return controller.should_trigger_threshold(
            state, context_window=131072, last_usage=usage, max_output_tokens=max_output_tokens,
        )

    assert not fires(131072 - 32000)
    assert not fires(117963)
    assert fires(117964)
    assert controller.recipe.overflow_policy == "terminal"


def _hermes_checkpoint_request(messages):
    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "hermes_agent@2026.9.11"}))
    summaries = Summaries("checkpoint")
    context = CompactionContext(messages, CompactionState(), recipe.settings, "manual", TARGET, 200000, 1,
                                summarizer=summaries)
    selection = Selection(first_kept_index=len(messages) - 1, summarize=tuple(range(len(messages) - 1)))
    recipe.pipeline.stages["summary"].step.reducer.reduce(context, selection)
    return summaries.requests[0].messages[0]["content"]

def test_hermes_checkpoint_summary_reads_typed_tool_results():
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "hermes_agent@2026.9.11"}))
    messages = [
        {"role": "user", "content": "look at the screen"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}]},
        responses_image_tool_result_message("c1", [{"type": "input_text", "text": "SCREEN_TEXT"},
                                                   {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]),
        {"role": "user", "content": "next"},
    ]
    summaries = Summaries("checkpoint")
    context = CompactionContext(messages, CompactionState(), recipe.settings, "manual", TARGET, 200000, 1,
                                summarizer=summaries)
    recipe.pipeline.stages["summary"].step.reducer.reduce(context, Selection(first_kept_index=3, summarize=(0, 1, 2)))
    assert "[TOOL RESULT c1]: SCREEN_TEXT\\n[image]" in json.dumps(summaries.requests[0].messages)


def test_hermes_checkpoint_summary_reads_anthropic_replay_result_blocks():
    native_messages = [{"role": "user", "content": [
        {
            "type": "tool_result",
            "tool_use_id": "c1",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
                {"type": "text", "text": "SCREEN_TEXT"},
            ],
        },
        {"type": "tool_result", "tool_use_id": "c2", "content": "SECOND_RESULT"},
    ]}]
    result = AnthropicAdapter().create_tool_result_batch_message(native_messages)
    assert replay_messages(result["content"]) == native_messages
    assert result_call_ids(result) == ("c1", "c2")

    messages = [
        {"role": "user", "content": "look at the screen"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "read_screen", "arguments": "{}"}},
        ]},
        result,
        {"role": "user", "content": "next"},
    ]
    request = _hermes_checkpoint_request(messages)
    assert "[TOOL RESULT c1,c2]: [image]\nSCREEN_TEXT\nSECOND_RESULT" in request
    assert "AA==" not in request


def test_hermes_checkpoint_summary_preserves_json_shaped_plain_tool_text():
    content = (
        '[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"AA=="}},'
        '{"type":"text","text":"SCREEN_TEXT"}]'
    )
    result = AnthropicAdapter().create_tool_result_message(
        "c1", "view_image", {"__mvi_text_output": content},
    )
    messages = [
        {"role": "user", "content": "look at the screen"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}]},
        result,
        {"role": "user", "content": "next"},
    ]
    request = _hermes_checkpoint_request(messages)
    assert f"[TOOL RESULT c1]: {content}" in request
    assert "[TOOL RESULT c1]: [image]" not in request

@pytest.mark.parametrize(("batch", "outer_id", "expected_native"), [
    (False, "other", False),
    (False, "c1", True),
    (True, "c1", False),
])
def test_hermes_checkpoint_summary_checks_outer_call_id_before_native_replay(batch, outer_id, expected_native):
    first = {"type": "tool_result", "tool_use_id": "c1", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
        {"type": "text", "text": "SCREEN_TEXT"},
    ]}
    native_messages = [{"role": "user", "content": [
        first,
        *([{"type": "tool_result", "tool_use_id": "c2", "content": "SECOND_RESULT"}] if batch else []),
    ]}]
    adapter = AnthropicAdapter()
    result = (
        adapter.create_tool_result_batch_message(native_messages)
        if batch
        else adapter.create_tool_result_message("c1", "view_image", {"__claude_tool_result_messages": native_messages})
    )
    native_call_ids = result_call_ids(result)
    canonical_results = [block["content"] for block in result["content"] if block.get("type") == "tool_result"]
    result["tool_call_id"] = outer_id
    messages = [
        {"role": "user", "content": "look at the screen"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "view_image", "arguments": "{}"}}
            for call_id in native_call_ids
        ]},
        result,
        {"role": "user", "content": "next"},
    ]
    request = _hermes_checkpoint_request(messages)
    assert f"[TOOL RESULT {outer_id}]" in request
    if expected_native:
        assert "[image]\nSCREEN_TEXT" in request
        assert "AA==" not in request
    else:
        assert all(content in request for content in canonical_results)
        assert "AA==" in request
        assert "[image]\nSCREEN_TEXT" not in request


@pytest.mark.parametrize("invalid_payload", ["wrong_result_id", "missing", "none", "list", "string"])
def test_hermes_checkpoint_summary_falls_back_for_invalid_anthropic_replay(invalid_payload):
    native_messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
            {"type": "text", "text": "SCREEN_TEXT"},
        ],
    }]}]
    result = AnthropicAdapter().create_tool_result_message(
        "c1", "view_image", {"__claude_tool_result_messages": native_messages},
    )
    replay = result["content"][1]
    if invalid_payload == "wrong_result_id":
        replay["payload"]["messages"][0]["content"][0]["tool_use_id"] = "other"
    elif invalid_payload == "missing":
        del replay["payload"]
    elif invalid_payload == "none":
        replay["payload"] = None
    elif invalid_payload == "list":
        replay["payload"] = []
    else:
        replay["payload"] = "not-an-object"
    canonical_result = result["content"][0]["content"]
    messages = [
        {"role": "user", "content": "look at the screen"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}]},
        result,
        {"role": "user", "content": "next"},
    ]
    request = _hermes_checkpoint_request(messages)
    assert f"[TOOL RESULT c1]: {canonical_result}" in request
    assert "AA==" in request
    assert "[image]\nSCREEN_TEXT" not in request


def test_hermes_checkpoint_summary_falls_back_for_empty_native_result_parts():
    native_messages = [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "c1", "content": [],
    }]}]
    result = AnthropicAdapter().create_tool_result_message(
        "c1", "view_image", {"__claude_tool_result_messages": native_messages},
    )
    messages = [
        {"role": "user", "content": "look at the screen"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}]},
        result,
        {"role": "user", "content": "next"},
    ]
    request = _hermes_checkpoint_request(messages)
    assert "[TOOL RESULT c1]: []" in request

CARRIER_PLACEHOLDER = "[Old tool result content cleared]"
CARRIER_ANTHROPIC = ProjectionTarget("anthropic", "messages", "claude-sonnet")
CARRIER_OPENAI = ProjectionTarget("openai", "responses", "gpt-x")


def _carrier_context(messages, *, target=CARRIER_ANTHROPIC, overflow_tokens=None, overflow_limit=None):
    return CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True}),
        reason="request",
        target=target,
        context_window=10_000,
        tokens_before=0,
        last_usage={"input_tokens": 20_000},
        max_output_tokens=100,
        overflow_tokens=overflow_tokens,
        overflow_limit=overflow_limit,
    )


def _carrier_latest_selector(keep_latest_calls):
    return LatestToolOutputs(Params({
        "keep_latest_calls": keep_latest_calls,
        "residual_target": 0,
        "min_savings": 0,
        "warning_delta": 0,
        "eligible_tools": ["Read"],
        "limit": {"kind": "window_minus_reserve", "reserve": 0},
        "disabled_auto_limit": {"kind": "window_minus_reserve", "reserve": 0},
    }, "test.select", BuildEnv(None)))


def _carrier_masker():
    return MaskOutputs(Params({"placeholder": CARRIER_PLACEHOLDER}, "test.reduce", BuildEnv(None)))


def _carrier_call(call_id):
    return {"id": call_id, "type": "function", "function": {"name": "Read", "arguments": "{}"}}


def test_latest_tool_outputs_masks_only_selected_result_in_native_batch():
    calls = [_carrier_call("older"), _carrier_call("latest")]
    assistant = {"role": "assistant", "content": "", "tool_calls": calls}
    native = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "older", "content": "older result " * 100},
        {"type": "tool_result", "tool_use_id": "latest", "content": "latest result"},
    ]}]
    carrier = AnthropicAdapter().create_tool_result_batch_message(native)
    messages = [{"role": "user", "content": "Read both files."}, assistant, carrier]
    context = _carrier_context(messages)

    selection = _carrier_latest_selector(keep_latest_calls=1).select(context)
    assert selection.targets == (2,)
    reduction = _carrier_masker().reduce(context, selection)
    masked = reduction.edits[0].message

    assert check_tool_pairing([messages[0], assistant, masked]) is None
    assert result_call_ids(masked) == ("older", "latest")
    canonical = {block["call_id"]: block["content"] for block in masked["content"]
                 if block.get("type") == "tool_result"}
    assert canonical == {"older": CARRIER_PLACEHOLDER, "latest": "latest result"}
    replayed = replay_messages(masked["content"])
    assert replayed[0]["content"] == [
        {"type": "tool_result", "tool_use_id": "older", "content": CARRIER_PLACEHOLDER},
        {"type": "tool_result", "tool_use_id": "latest", "content": "latest result"},
    ]


def test_latest_tool_outputs_keeps_chat_result_wire_bytes():
    assistant = {"role": "assistant", "content": "", "tool_calls": [_carrier_call("chat-call")]}
    result = {"role": "tool", "tool_call_id": "chat-call", "content": "chat result " * 100}
    context = _carrier_context([assistant, result])

    selection = _carrier_latest_selector(keep_latest_calls=0).select(context)
    masked = _carrier_masker().reduce(context, selection).edits[0].message

    assert check_tool_pairing([assistant, masked]) is None
    assert json.dumps(masked, ensure_ascii=False, separators=(",", ":")) == json.dumps(
        {"role": "tool", "tool_call_id": "chat-call", "content": CARRIER_PLACEHOLDER},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _carrier_history_selector(kind):
    if kind == "protected_tool_outputs":
        return ProtectedToolOutputs(Params({
            "enabled": True,
            "protect_tokens": 0,
            "protect_user_turns": 0,
            "min_savings": 0,
            "exempt_tools": [],
            "stop_at_summary": True,
            "placeholder": CARRIER_PLACEHOLDER,
        }, "test.select", BuildEnv(None)))
    return LargestFirstMasking(Params({
        "target_ratio": 0.5,
        "chars_per_token": 4,
        "placeholder": CARRIER_PLACEHOLDER,
    }, "test.select", BuildEnv(None)))


@pytest.mark.parametrize("kind", ["protected_tool_outputs", "largest_first_masking"])
def test_history_selectors_mask_typed_responses_output_and_preserve_consumable_pair(kind):
    call_id = "typed-output"
    assistant = {"role": "assistant", "content": "", "tool_calls": [_carrier_call(call_id)]}
    output = "typed tool output " * 2_000
    result = {"role": "tool_result", "content": [{
        "type": "provider_replay",
        "provider_id": "openai",
        "schema_version": "openai.responses.tool_output.v1",
        "replay_scope": "same_provider",
        "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
    }]}
    context = _carrier_context([assistant, result], target=CARRIER_OPENAI,
                               overflow_tokens=5_000, overflow_limit=1_000)

    selection = _carrier_history_selector(kind).select(context)
    assert selection.targets == (1,)
    masked = _carrier_masker().reduce(context, selection).edits[0].message

    assert check_tool_pairing([assistant, masked]) is None
    assert result_call_ids(masked) == (call_id,)
    replay = masked["content"][0]
    assert replay["payload"] == {
        "type": "function_call_output", "call_id": call_id, "output": CARRIER_PLACEHOLDER,
    }
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    runtime_context = ProviderRuntimeContext(None, {
        "provider_tools": {"openai": {"responses_tool_output_items": True}},
    })
    input_items = runtime._convert_messages_to_input(
        [assistant, masked], include_tool_calls=True, context=runtime_context,
    )
    assert input_items[-1] == {
        "type": "function_call_output", "call_id": call_id, "output": CARRIER_PLACEHOLDER,
    }
