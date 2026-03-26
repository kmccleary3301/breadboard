from __future__ import annotations

from agentic_coder_prototype.ctrees.phase10_judgment_stack import build_phase10_judgment_stack


def test_ctree_phase10_judgment_stack_combines_pilot_and_bridge() -> None:
    payload = build_phase10_judgment_stack()

    assert payload["schema_version"] == "ctree_phase10_judgment_stack_v2"
    assert payload["pilot_summary"]["scenario_count"] == 4
    assert payload["full_holdout_summary"]["scenario_count"] == 48
    assert payload["full_holdout_summary"]["base_scenario_count"] == 16
    assert payload["historical_bridge_summary"]["scenario_count"] == 3
    assert payload["historical_bridge_summary"]["total_repeat_count"] == 6
    assert payload["judgment"]["prompt_centric_baseline_resolved"] is True
    assert payload["judgment"]["prompt_centric_bridge_operational"] is True
    assert payload["judgment"]["prompt_centric_bridge_adverse"] is True
    assert payload["judgment"]["deterministic_reranker_required"] is True
    assert payload["judgment"]["deterministic_baseline_stronger_than_frozen_core"] is True
    assert payload["judgment"]["frozen_core_promotable"] is False
    assert payload["judgment"]["next_gate"] == "phase10_promotion_decision"
