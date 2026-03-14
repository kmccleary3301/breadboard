from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines


def test_run_live_baselines_emits_six_lane_summary() -> None:
    write_bootstrap_specs()
    summary = run_live_baselines()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 6
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.scheduling", "lane.research"}
    rows = {row["lane_id"]: row for row in payload["lanes"]}
    for lane_id in ("lane.harness", "lane.repo_swe"):
        refs = rows[lane_id]["shadow_artifact_refs"]
        assert refs["effective_config"].endswith("_effective_config_v0.json")
        assert refs["execution_plan"].endswith("_execution_plan_v0.json")
        assert Path(refs["effective_config"]).exists()
        assert Path(refs["execution_plan"]).exists()
    for lane_id in ("lane.atp", "lane.systems", "lane.scheduling", "lane.research"):
        assert rows[lane_id]["shadow_artifact_refs"] == {}
