from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.build_darwin_weekly_packet_v1 import write_weekly_packet
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.emit_darwin_evidence_and_claims_v1 import emit_evidence_and_claims
from scripts.emit_darwin_search_evidence_v1 import emit_search_evidence
from scripts.build_darwin_compute_normalized_view_v2 import write_compute_normalized_view_v2
from scripts.build_darwin_comparative_dossier_v1 import write_dossier


def test_write_dossier_emits_transfer_and_promotion_summary() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    write_scorecard(include_search=True)
    write_weekly_packet(include_search=True)
    emit_evidence_and_claims()
    write_invalid_comparison_ledger()
    emit_search_evidence()
    write_compute_normalized_view_v2()
    summary = write_dossier()
    payload = json.loads(open(summary["out_json"], "r", encoding="utf-8").read())
    assert payload["live_lane_count"] == 6
    assert payload["search_enabled_lane_count"] == 4
    assert payload["successful_transfer_count"] == 1
    assert payload["compute_view_ref"].endswith("compute_normalized_view_v2.json")
