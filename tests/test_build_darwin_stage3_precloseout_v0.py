from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage3_bounded_inference_campaign_v0 import run_bounded_inference_campaign
from scripts.build_darwin_stage3_operator_ev_report_v0 import write_operator_ev_report
from scripts.build_darwin_stage3_topology_ev_report_v0 import write_topology_ev_report
from scripts.build_darwin_stage3_invalidity_summary_v0 import write_invalidity_summary
from scripts.build_darwin_stage3_verification_bundle_v0 import write_verification_bundle
from scripts.build_darwin_stage3_component_candidates_v0 import write_component_candidates
from scripts.run_darwin_stage3_component_replay_v0 import run_component_replay
from scripts.build_darwin_stage3_component_promotion_report_v0 import write_component_promotion_report
from scripts.build_darwin_stage3_bounded_transfer_outcomes_v0 import write_bounded_transfer_outcomes
from scripts.build_darwin_stage3_failed_transfer_taxonomy_v0 import write_failed_transfer_taxonomy
from scripts.build_darwin_stage3_component_transfer_verification_bundle_v0 import (
    write_component_transfer_verification_bundle,
)
from scripts.build_darwin_stage3_component_registry_v0 import write_component_registry
from scripts.build_darwin_stage3_promoted_family_artifact_v0 import write_promoted_family_artifact
from scripts.build_darwin_stage3_second_family_decision_v0 import write_second_family_decision
from scripts.build_darwin_stage3_component_scorecard_v0 import write_component_scorecard
from scripts.build_darwin_stage3_component_memo_v0 import write_component_memo
from scripts.build_darwin_stage3_consolidated_verification_bundle_v0 import (
    write_consolidated_verification_bundle,
)


def test_stage3_precloseout_surfaces_build() -> None:
    write_bootstrap_specs()
    run_bounded_inference_campaign()
    write_operator_ev_report()
    write_topology_ev_report()
    write_invalidity_summary()
    write_verification_bundle()
    write_component_candidates()
    run_component_replay()
    write_component_promotion_report()
    write_bounded_transfer_outcomes()
    write_failed_transfer_taxonomy()
    write_component_transfer_verification_bundle()
    registry = write_component_registry()
    promoted = write_promoted_family_artifact()
    second = write_second_family_decision()
    scorecard = write_component_scorecard()
    memo = write_component_memo()
    consolidated = write_consolidated_verification_bundle()

    registry_payload = json.loads(open(registry["out_json"], "r", encoding="utf-8").read())
    promoted_payload = json.loads(open(promoted["out_json"], "r", encoding="utf-8").read())
    second_payload = json.loads(open(second["out_json"], "r", encoding="utf-8").read())
    scorecard_payload = json.loads(open(scorecard["out_json"], "r", encoding="utf-8").read())
    memo_payload = json.loads(open(memo["out_json"], "r", encoding="utf-8").read())
    consolidated_payload = json.loads(open(consolidated["out_json"], "r", encoding="utf-8").read())

    assert registry_payload["row_count"] >= 5
    promoted_row = next(row for row in registry_payload["rows"] if row["lifecycle_status"] == "promoted")
    assert promoted_payload["component_family_id"] == promoted_row["component_family_id"]
    assert second_payload["decision"] == "no_second_family_yet"
    assert scorecard_payload["row_count"] == registry_payload["row_count"]
    assert "promotes one replay-backed reusable topology family from lane.repo_swe" in memo_payload["what_stage3_demonstrably_does"]
    assert consolidated_payload["summary"]["registry_rows"] == registry_payload["row_count"]
