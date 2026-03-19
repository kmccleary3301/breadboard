from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_compute_normalized_view_v2 import write_compute_normalized_view_v2
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t1_baseline_scorecard_v1 import build_scorecard, write_scorecard
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke


def test_build_scorecard_reports_six_lanes() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    payload = build_scorecard()
    assert payload["lane_count"] == 6
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.scheduling", "lane.research"}
    assert payload["mean_normalized_score"] > 0


def test_write_scorecard_emits_json_and_markdown(tmp_path: Path) -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    summary = write_scorecard(tmp_path)
    payload = json.loads((tmp_path / "t1_baseline_scorecard.latest.json").read_text(encoding="utf-8"))
    assert "t1_baseline_scorecard.latest.md" in summary["out_md"]
    assert payload["schema"] == "breadboard.darwin.t1_baseline_scorecard.v1"


def test_build_scorecard_include_search_consumes_compute_view_v2() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    write_invalid_comparison_ledger()
    write_compute_normalized_view_v2()
    payload = build_scorecard(include_search=True)
    assert payload["compute_view_schema"] == "breadboard.darwin.compute_normalized_view.v2"
    repo_swe = next(row for row in payload["lanes"] if row["lane_id"] == "lane.repo_swe")
    assert repo_swe["comparison_status"] == "baseline_retained_with_invalid_trials"
    assert repo_swe["baseline_local_cost_classification"] == "exact_local_zero"
    assert "lane_contains_invalid_trials" in repo_swe["interpretation_flags"]
