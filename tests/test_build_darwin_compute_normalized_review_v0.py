from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_compute_normalized_view_v2 import write_compute_normalized_view_v2
from scripts.build_darwin_compute_normalized_review_v0 import write_review
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke


def test_write_review_emits_proving_and_audit_rows() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    write_invalid_comparison_ledger()
    write_compute_normalized_view_v2()
    write_scorecard(include_search=True)
    summary = write_review()
    payload = json.loads(open(summary["out_json"], "r", encoding="utf-8").read())
    assert payload["schema"] == "breadboard.darwin.compute_normalized_review.v0"
    assert payload["coherent_proving_count"] == 2
    repo_swe = next(row for row in payload["proving_rows"] if row["lane_id"] == "lane.repo_swe")
    assert repo_swe["comparison_status"] == "baseline_retained_with_invalid_trials"
    assert repo_swe["review_outcome"] == "coherent"
