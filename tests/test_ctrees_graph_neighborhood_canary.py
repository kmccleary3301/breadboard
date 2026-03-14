from __future__ import annotations

from agentic_coder_prototype.ctrees.graph_neighborhood_canary import evaluate_graph_neighborhood_canary


def test_ctree_graph_neighborhood_canary_improves_over_direct_graph_baseline() -> None:
    payload = evaluate_graph_neighborhood_canary()

    assert payload["schema_version"] == "ctree_graph_neighborhood_canary_v1"
    assert payload["family"] == "dependency_lookup_graph_neighborhood"
    assert payload["scenario_count"] == 3
    assert payload["all_passed"] is True
    for scenario in payload["scenarios"]:
        assert scenario["delta"] > 0


def test_ctree_graph_neighborhood_canary_keeps_support_bounded() -> None:
    payload = evaluate_graph_neighborhood_canary()

    for scenario in payload["scenarios"]:
        assert scenario["graph_share_within_budget"] is True
