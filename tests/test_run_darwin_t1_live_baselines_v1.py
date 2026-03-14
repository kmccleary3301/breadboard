from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines


def test_run_live_baselines_emits_four_lane_summary() -> None:
    write_bootstrap_specs()
    summary = run_live_baselines()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 4
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.atp", "lane.harness", "lane.systems", "lane.repo_swe"}
