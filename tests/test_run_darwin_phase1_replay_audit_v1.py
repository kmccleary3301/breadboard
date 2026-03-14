from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.run_darwin_phase1_replay_audit_v1 import write_replay_audit


def test_write_replay_audit_emits_stable_rows() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    summary = write_replay_audit()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["audit_count"] == 3
    assert payload["all_stable"] is True
