from __future__ import annotations

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.build_darwin_weekly_packet_v1 import write_weekly_packet
from scripts.emit_darwin_evidence_and_claims_v1 import emit_evidence_and_claims
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.emit_darwin_search_evidence_v1 import emit_search_evidence


def test_emit_search_evidence_writes_bundle_and_claims() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    write_scorecard(include_search=True)
    write_weekly_packet(include_search=True)
    emit_evidence_and_claims()
    write_invalid_comparison_ledger()
    summary = emit_search_evidence()
    assert summary["claim_count"] == 3
