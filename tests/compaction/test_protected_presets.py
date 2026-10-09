"""Preset controls not covered by the captured helper cases."""

from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, load_compaction_config
from breadboard_engine.compaction.presets import build_recipe
from breadboard_engine.compaction.primitives.accounting import OccupancyInput, normalize_usage
from breadboard_engine.compaction.primitives.triggers import TriggerInput


def test_available_provider_context_usage_takes_precedence():
    recipe = build_recipe(load_compaction_config({"enabled": True, "preset": "openclaw@2026.9.4"}))
    usage = normalize_usage({"input_tokens": 100, "total_tokens": 100,
                             "contextUsage": {"state": "available", "totalTokens": 190000}})
    pressure = recipe.pressure(TriggerInput(OccupancyInput([], usage, True), 200000), [])
    assert pressure.tokens == 190000
    assert pressure.fires


def test_disabled_preset_preserves_observation_bytes():
    from breadboard_engine.compaction.controller import CompactionController
    from types import SimpleNamespace
    messages = [{"role": "tool", "tool_call_id": "call", "content": "x" * 15000}]
    state = SimpleNamespace(provider_messages=messages, compaction_state=CompactionState())
    controller = CompactionController({"compaction": {"enabled": False, "preset": "mini_swe_agent@2.4.6"}})
    assert controller.build_request_view(state, target=ProjectionTarget("oracle", "oracle", "oracle")) == messages
