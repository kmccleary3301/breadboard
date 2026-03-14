from __future__ import annotations

from agentic_coder_prototype.ctrees.blocker_graph_canary import evaluate_blocker_graph_canary


def test_ctree_blocker_graph_canary_improves_over_non_graph_baseline() -> None:
    payload = evaluate_blocker_graph_canary()

    assert payload["schema_version"] == "ctree_blocker_graph_canary_v1"
    assert payload["family"] == "blocker_graph_dependency_lookup"
    assert payload["scenario_count"] == 3
    assert payload["all_passed"] is True
    for scenario in payload["scenarios"]:
        assert scenario["delta"] > 0


def test_ctree_blocker_graph_canary_keeps_support_bounded() -> None:
    payload = evaluate_blocker_graph_canary()

    for scenario in payload["scenarios"]:
        assert scenario["graph_share_within_budget"] is True
