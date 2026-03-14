from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke


def test_run_search_smoke_emits_promotion_cycles() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    summary = run_search_smoke()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 4
    assert payload["mutation_trial_count"] == 9
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.harness", "lane.repo_swe", "lane.scheduling", "lane.research"}
    scheduling = next(row for row in payload["lanes"] if row["lane_id"] == "lane.scheduling")
    research = next(row for row in payload["lanes"] if row["lane_id"] == "lane.research")
    assert scheduling["promotion_status"] == "promote_mutation"
    assert scheduling["promotion_history_depth"] == 2
    assert research["successful_transfer_count"] == 1
