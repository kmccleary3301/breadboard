"""Preset behavior at the recipe, ledger and configuration boundaries."""
from copy import deepcopy

import pytest

from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, SummaryResponse, load_compaction_config
from breadboard_engine.compaction.presets import build_recipe


class Summaries:
    def __init__(self, *texts):
        self.texts = iter(texts)

    def complete(self, request):
        return SummaryResponse(next(self.texts))


def codex_context(messages, *texts):
    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "codex@0.139.0"}))
    context = CompactionContext(messages, CompactionState(), recipe.settings, "manual",
                                ProjectionTarget("test", "responses", "model"), 100000, recipe.count(messages),
                                summarizer=Summaries(*texts))
    return recipe, context


def test_user_budget_truncates_one_older_message_in_the_middle():
    recent = "x" * 79996  # 19999 of the fixed 20000 retained-user tokens.
    messages = [{"role": "user", "content": "0123456789abcdef"},
                {"role": "assistant", "content": "work"}, {"role": "user", "content": recent}]
    original = deepcopy(messages)
    recipe, context = codex_context(messages, "checkpoint")
    outcome = recipe.pipeline.run(context, target_tokens=90000)
    assert outcome.compacted
    assert context.projected()[0] == {"role": "user", "content": "01…3 tokens truncated…ef"}
    assert context.projected()[1] == {"role": "user", "content": recent}
    assert context.projected()[-1]["content"].endswith("\ncheckpoint")
    assert messages == original


def test_second_compaction_retains_previous_real_users_but_not_the_old_summary():
    messages = [{"role": "user", "content": "initial"}, {"role": "assistant", "content": "work"}]
    recipe, context = codex_context(messages, "old checkpoint", "new checkpoint")
    recipe.pipeline.run(context, target_tokens=90000)
    messages.append({"role": "user", "content": "follow up"})
    recipe.pipeline.run(context, target_tokens=90000)
    assert context.projected()[:2] == [{"role": "user", "content": "initial"}, {"role": "user", "content": "follow up"}]
    assert len(context.projected()) == 3
    assert context.projected()[-1]["content"].endswith("\nnew checkpoint")
    assert len(context.state.records) == 2


@pytest.mark.parametrize("preset", ["codex@0.139.0", "claude_code@2.1.63"])
def test_preset_loads_and_unknown_key_lists_accepted_settings(preset):
    config = load_compaction_config({"enabled": True, "preset": preset})
    assert build_recipe(config).overflow_policy == "terminal"
    with pytest.raises(ValueError, match="accepted keys are") as error:
        load_compaction_config({"enabled": True, "preset": preset, "typo": True})
    assert ("compact_prompt" if preset.startswith("codex") else "autoCompactEnabled") in str(error.value)
