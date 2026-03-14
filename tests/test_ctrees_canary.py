from __future__ import annotations

from agentic_coder_prototype.ctrees.canary import evaluate_recovery_family_canary


def test_ctree_recovery_family_canary_all_scenarios_pass() -> None:
    payload = evaluate_recovery_family_canary()

    assert payload["schema_version"] == "ctree_recovery_family_canary_v1"
    assert payload["family"] == "tranche1_recovery_family"
    assert payload["scenario_count"] == 6
    assert payload["all_passed"] is True
    assert payload["pass_count"] == payload["scenario_count"]


def test_ctree_recovery_family_canary_shows_positive_delta_over_stripped() -> None:
    payload = evaluate_recovery_family_canary()

    for scenario in payload["scenarios"]:
        assert scenario["delta"] > 0
        assert not scenario["matched_forbidden"]


def test_ctree_recovery_family_canary_keeps_support_bounded() -> None:
    payload = evaluate_recovery_family_canary()

    for scenario in payload["scenarios"]:
        assert scenario["support_share_within_budget"] is True
        assert scenario["support_node_count_within_budget"] is True
