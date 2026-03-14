from __future__ import annotations

from agentic_coder_prototype.ctrees.gate_e import evaluate_gate_e


def test_ctree_gate_e_shows_value_over_stripped_bundle() -> None:
    payload = evaluate_gate_e()

    assert payload["schema_version"] == "ctree_gate_e_v1"
    assert payload["gate_e_pass"] is True
    assert payload["value_pass_count"] >= 1

    scenarios = {item["scenario"]: item for item in payload["value_scenarios"]}
    assert scenarios["target_supersession"]["delta"] > 0
    assert scenarios["stale_verification_dominance"]["delta"] > 0
    assert scenarios["false_neighbor_suppression"]["delta"] > 0


def test_ctree_gate_e_keeps_support_share_bounded() -> None:
    payload = evaluate_gate_e()

    assert payload["all_support_shares_within_budget"] is True
    for scenario in payload["value_scenarios"]:
        assert scenario["support_token_share"] <= payload["max_support_share"]


def test_ctree_gate_e_confirms_ready_and_blocker_operational() -> None:
    payload = evaluate_gate_e()
    blocker_probe = payload["blocker_clearance_probe"]

    assert payload["ready_and_blocker_operational"] is True
    assert blocker_probe["blocked_needs_resolution"] is True
    assert blocker_probe["cleared_needs_resolution"] is False
