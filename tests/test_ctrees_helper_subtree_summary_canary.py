from __future__ import annotations

from agentic_coder_prototype.ctrees.helper_subtree_summary_canary import evaluate_helper_subtree_summary_canary


def test_ctree_helper_subtree_summary_canary_improves_over_baseline() -> None:
    payload = evaluate_helper_subtree_summary_canary()

    assert payload["schema_version"] == "ctree_helper_subtree_summary_canary_v1"
    assert payload["family"] == "helper_subtree_summary_selection"
    assert payload["scenario_count"] == 1
    assert payload["all_passed"] is True
    assert payload["scenarios"][0]["delta"] > 0
