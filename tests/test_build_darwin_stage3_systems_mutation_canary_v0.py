from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_stage3_systems_mutation_canary_v0 import run_systems_mutation_canary


def test_run_systems_mutation_canary_emits_stage3_artifacts() -> None:
    write_bootstrap_specs()
    summary = run_systems_mutation_canary()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_id"] == "lane.systems"
    assert payload["mutation_trial_count"] == 2
    assert {row["stage3_substrate"]["selected_locus_id"] for row in payload["rows"]} == {
        "topology.params",
        "policy.bundle",
    }
    for row in payload["rows"]:
        refs = row["stage3_substrate"]["artifact_refs"]
        assert refs["optimization_target"].endswith("_optimization_target_v1.json")
        assert refs["candidate_bundle"].endswith("_candidate_bundle_v1.json")
        assert refs["materialized_candidate"].endswith("_materialized_candidate_v1.json")
        assert refs["evaluator_pack"].endswith("_evaluator_pack_v0.json")
