from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines
from scripts.run_darwin_t2_search_smoke_v1 import run_search_smoke
from scripts.build_darwin_transfer_family_view_v0 import write_transfer_family_view


def test_write_transfer_family_view_emits_classified_transfer_rows() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    run_live_baselines()
    write_typed_search_core()
    run_search_smoke()
    summary = write_transfer_family_view()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["schema"] == "breadboard.darwin.transfer_family_view.v0"
    assert payload["attempt_count"] >= 1
    row = payload["rows"][0]
    assert row["transfer_family"] == "cross_lane_prompt_family_transfer"
    assert row["result_class"] == "valid_improved"
    assert row["component_family"] == "prompt_family"
    assert row["decision_status"] == "promoted"
