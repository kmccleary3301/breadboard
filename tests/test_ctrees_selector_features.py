from __future__ import annotations

from agentic_coder_prototype.ctrees.holdout_generalization_pack import build_phase10_base_scenarios
from agentic_coder_prototype.ctrees.policy import build_retrieval_substrate
from agentic_coder_prototype.ctrees.selector_features import build_selector_feature_table


def _scenario_by_id(base_scenario_id: str):
    for item in build_phase10_base_scenarios():
        if item["base_scenario_id"] == base_scenario_id:
            return item
    raise AssertionError(f"missing scenario {base_scenario_id}")


def test_selector_feature_table_marks_stale_and_overlap_signals() -> None:
    scenario = _scenario_by_id("continuation_workspace_scope_v1")
    substrate = build_retrieval_substrate(
        scenario["store"],
        mode=scenario["mode"],
        focus_node_id=scenario["focus_node_id"],
    )
    feature_table = build_selector_feature_table(
        scenario["store"],
        mode=scenario["mode"],
        retrieval_substrate=substrate,
    )

    rows_by_title = {row["title"]: row for row in feature_table["rows"]}
    relevant = rows_by_title["Compiler continuity note"]
    stale = rows_by_title["Old compiler continuity note"]

    assert feature_table["focus_node_id"] == scenario["focus_node_id"]
    assert relevant["features"]["target_overlap_count"] > 0
    assert relevant["features"]["workspace_overlap_count"] > 0
    assert stale["features"]["stale_or_conflict"] is True
