from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.build_darwin_compute_normalized_view_v2 import write_compute_normalized_view_v2


def test_write_compute_normalized_view_v2_emits_classified_rows() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    write_invalid_comparison_ledger()
    summary = write_compute_normalized_view_v2()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["schema"] == "breadboard.darwin.compute_normalized_view.v2"
    assert payload["lane_count"] == 6
    assert payload["summary"]["external_billing_classification"] == "unavailable"
    repo_swe = next(row for row in payload["lanes"] if row["lane_id"] == "lane.repo_swe")
    assert repo_swe["baseline_local_cost_classification"] == "exact_local_zero"
    assert repo_swe["comparison_status"] == "baseline_retained_with_invalid_trials"
    assert "lane_contains_invalid_trials" in repo_swe["interpretation_flags"]
