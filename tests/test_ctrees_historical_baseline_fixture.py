from __future__ import annotations

from agentic_coder_prototype.ctrees.historical_baseline_fixture import load_prompt_centric_historical_fixture


def test_ctree_historical_prompt_centric_fixture_is_frozen_and_present() -> None:
    payload = load_prompt_centric_historical_fixture()

    assert payload["fixture_id"] == "prompt_centric_historical_ctrees_task_tree_toolbudget_v1"
    assert payload["status"] == "frozen_external_historical_not_integrated"
    assert payload["source_commit"] == "477c850"
    assert all(bool(value) for value in payload["exists"].values())


def test_ctree_historical_prompt_centric_fixture_captures_expected_semantics() -> None:
    payload = load_prompt_centric_historical_fixture()
    semantics = payload["observed_semantics"]

    assert semantics["context_engine_mode"] == "replace_messages"
    assert semantics["render_mode"] == "debug_v1"
    assert semantics["replace_stats_present"] is True
