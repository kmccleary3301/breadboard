from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_compute_normalized_view_v1 import write_compute_normalized_view


def test_write_compute_normalized_view_emits_six_lane_view() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    summary = write_compute_normalized_view()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 6
    research = next(row for row in payload["lanes"] if row["lane_id"] == "lane.research")
    assert research["active_score"] >= research["baseline_score"]
