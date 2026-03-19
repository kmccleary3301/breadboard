from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from scripts.run_darwin_phase1_replay_audit_v1 import write_replay_audit
from scripts.build_darwin_evolution_ledger_v0 import write_evolution_ledger


def test_write_evolution_ledger_reconstructs_target_cases() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    write_typed_search_core()
    run_live_baselines()
    run_search_smoke()
    write_invalid_comparison_ledger()
    write_replay_audit()
    summary = write_evolution_ledger()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["archive_snapshot_is_derived"] is True
    assert payload["decision_truth_scope"]["runtime_truth_owned_by"] == "breadboard_runtime"
    assert "transfer" in payload["decision_truth_scope"]["canonical_decision_types"]
    assert len(payload["component_refs"]) >= 10
    assert {row["decision_type"] for row in payload["decision_records"]} >= {
        "promotion",
        "transfer",
        "deprecation",
        "retained_baseline",
    }
    assert {row["case_id"] for row in payload["reconstructed_cases"]} == {
        "case.promotion.lane.scheduling.v1",
        "case.transfer.harness_to_research.v1",
        "case.search_review.lane.repo_swe.v1",
    }
