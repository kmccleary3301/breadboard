from __future__ import annotations

from agentic_coder_prototype.ctrees.historical_bridge_summary import load_prompt_centric_bridge_summary


def test_ctree_historical_bridge_summary_loads_three_scenario_pack() -> None:
    payload = load_prompt_centric_bridge_summary()

    assert payload["schema_version"] == "ctree_historical_bridge_summary_v1"
    assert payload["scenario_count"] == 3
    assert payload["total_repeat_count"] == 6
    assert payload["bundle_present_count"] >= 3
    assert payload["avg_request_count"] > 0.0
    assert payload["avg_input_tokens"] > 0.0


def test_ctree_historical_bridge_summary_separates_policy_and_task_failures() -> None:
    payload = load_prompt_centric_bridge_summary()

    assert payload["task_failure_count"] >= 1
    assert payload["tool_policy_failure_count"] >= 1
    for scenario in payload["scenario_summaries"]:
        assert scenario["repeat_count"] == 2
        for repeat in scenario["repeat_summaries"]:
            semantics = repeat["observed_semantics"]
            assert semantics["context_engine_mode"] == "replace_messages"
            assert semantics["render_mode"] == "debug_v1"
