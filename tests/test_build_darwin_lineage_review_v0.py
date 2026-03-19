from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_lineage_review_v0 import write_lineage_review


def test_write_lineage_review_emits_scheduling_supersession_chain() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    summary = write_lineage_review()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["schema"] == "breadboard.darwin.lineage_review.v0"
    scheduling = next(row for row in payload["lanes"] if row["lane_id"] == "lane.scheduling")
    assert scheduling["promotion_history_depth"] == 2
    assert "cand.lane.scheduling.baseline.v1" in scheduling["superseded_candidate_ids"]
    assert scheduling["rollback_ready"] is True
    assert scheduling["active_lineage_state"] == "promoted"
