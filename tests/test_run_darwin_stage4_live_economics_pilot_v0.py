from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage4_live_economics_pilot_v0 import run_stage4_live_economics_pilot


def test_run_stage4_live_economics_pilot_emits_policy_and_telemetry() -> None:
    write_bootstrap_specs()
    summary = run_stage4_live_economics_pilot()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 2
    assert payload["arm_count"] == 5
    assert payload["run_count"] == 10
    assert payload["selected_repo_swe_operator_ids"] == [
        "mut.topology.single_to_pev_v1",
        "mut.tool_scope.add_git_diff_v1",
        "mut.budget.class_a_to_class_b_v1",
    ]
    assert payload["claim_eligible_comparison_count"] == 0
    assert payload["execution_modes"] == ["scaffold"]
