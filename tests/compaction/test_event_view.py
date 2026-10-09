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
    outcome = Pipeline([replace(first, failure_next_on=None), second], "until_boundary", len).run(failed, target_tokens=1)
    assert [s.status for s in outcome.stages] == ["failed", "noop"]
    assert not failed.state.records
    assert len(failed.summarizer.requests) == 1


def test_token_trigger_requires_real_counter_and_honors_agent_cap():
    recipe, ctx = context(turns(10), ["unused"], max_tokens=25)
    from breadboard_engine.compaction.primitives.accounting import OccupancyInput
    from breadboard_engine.compaction.primitives.triggers import TriggerInput
    data = TriggerInput(OccupancyInput(ctx.messages, None, True), 200000)
    assert not recipe.pressure(data, ctx.messages).fires
    pressure = recipe.pressure(replace(data, token_counter=lambda messages: 21, effective_input_tokens=20), ctx.messages)
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
