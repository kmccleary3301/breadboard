from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_compute_normalized_memo_v0 import write_memo
from scripts.build_darwin_compute_normalized_review_v0 import write_review
from scripts.build_darwin_compute_normalized_view_v2 import write_compute_normalized_view_v2
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke


def test_write_memo_emits_proving_read_and_keeps_transfer_locked() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    write_invalid_comparison_ledger()
    write_compute_normalized_view_v2()
    write_scorecard(include_search=True)
    write_review()
    summary = write_memo()
    payload = json.loads(open(summary["out_json"], "r", encoding="utf-8").read())
    assert payload["schema"] == "breadboard.darwin.compute_normalized_memo.v0"
    assert payload["proving_clear_count"] == 2
    assert payload["unlock_transfer_lineage"] is False
    repo_swe = next(row for row in payload["proving_rows"] if row["lane_id"] == "lane.repo_swe")
    assert "invalid trials" in repo_swe["lane_story"]
