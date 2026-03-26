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
from scripts.build_darwin_transfer_family_view_v0 import write_transfer_family_view
from scripts.build_darwin_lineage_review_v0 import write_lineage_review
from scripts.build_darwin_transfer_lineage_proving_review_v0 import write_transfer_lineage_proving_review


def test_write_transfer_lineage_proving_review_covers_primary_and_audit_lanes() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    write_typed_search_core()
    run_live_baselines()
    run_search_smoke()
    write_invalid_comparison_ledger()
    write_replay_audit()
    write_evolution_ledger()
    write_transfer_family_view()
    write_lineage_review()
    summary = write_transfer_lineage_proving_review()
    payload = json.loads(open(summary["json_path"], "r", encoding="utf-8").read())
    rows = {row["lane_id"]: row for row in payload["rows"]}
    assert rows["lane.repo_swe"]["review_outcome"] == "coherent_invalid_sensitive"
    assert rows["lane.scheduling"]["review_outcome"] == "coherent_multi_cycle"
    assert rows["lane.research"]["result_class"] == "valid_improved"
    assert rows["lane.atp"]["review_outcome"] == "audit_only_confirmed"
