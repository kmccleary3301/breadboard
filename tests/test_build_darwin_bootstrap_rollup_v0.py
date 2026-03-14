from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.build_darwin_weekly_packet_v1 import write_weekly_packet
from scripts.build_darwin_bootstrap_rollup_v0 import write_rollup


def test_build_darwin_bootstrap_rollup_reports_live_inputs() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_scorecard()
    write_weekly_packet()
    summary = write_rollup()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["live_baseline_present"] is True
    assert payload["bootstrap_spec_count"] == 3
