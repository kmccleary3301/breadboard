from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.build_darwin_weekly_packet_v1 import write_weekly_packet
from scripts.emit_darwin_evidence_and_claims_v1 import emit_evidence_and_claims
from scripts.check_darwin_lane_readiness_v0 import write_readiness


def test_check_darwin_lane_readiness_passes_after_live_baseline_stack() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_scorecard()
    write_weekly_packet()
    emit_evidence_and_claims()
    summary = write_readiness()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["overall_ok"] is True
