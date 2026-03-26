from __future__ import annotations

from agentic_coder_prototype.ctrees.helper_rehydration_canary import evaluate_helper_rehydration_canary


def test_ctree_helper_rehydration_canary_improves_over_non_helper_baseline() -> None:
    payload = evaluate_helper_rehydration_canary()

    assert payload["schema_version"] == "ctree_helper_rehydration_canary_v1"
    assert payload["family"] == "helper_rehydration_selection"
    assert payload["scenario_count"] == 3
    assert payload["all_passed"] is True
    for scenario in payload["scenarios"]:
        assert scenario["delta"] > 0


def test_ctree_helper_rehydration_canary_keeps_support_bounded() -> None:
    payload = evaluate_helper_rehydration_canary()

    for scenario in payload["scenarios"]:
        assert scenario["helper_share_within_budget"] is True
