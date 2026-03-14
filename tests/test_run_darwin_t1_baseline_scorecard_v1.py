from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.run_darwin_t1_baseline_scorecard_v1 import build_scorecard, write_scorecard


def test_build_scorecard_reports_five_lanes() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    payload = build_scorecard()
    assert payload["lane_count"] == 5
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.scheduling"}
    assert payload["mean_normalized_score"] > 0


def test_write_scorecard_emits_json_and_markdown(tmp_path: Path) -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    summary = write_scorecard(tmp_path)
    payload = json.loads((tmp_path / "t1_baseline_scorecard.latest.json").read_text(encoding="utf-8"))
    assert "t1_baseline_scorecard.latest.md" in summary["out_md"]
    assert payload["schema"] == "breadboard.darwin.t1_baseline_scorecard.v1"
