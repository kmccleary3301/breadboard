from __future__ import annotations

from agentic_coder_prototype.ctrees.helper_summary_coupling_canary import evaluate_helper_summary_coupling_canary


def test_ctree_helper_summary_coupling_canary_improves_over_helper_only_baseline() -> None:
    payload = evaluate_helper_summary_coupling_canary()

    assert payload["schema_version"] == "ctree_helper_summary_coupling_canary_v1"
    assert payload["family"] == "helper_summary_to_rehydration"
    assert payload["scenario_count"] == 1
    assert payload["all_passed"] is True
    assert payload["scenarios"][0]["delta"] > 0
    assert payload["scenarios"][0]["coupled_share_within_budget"] is True
