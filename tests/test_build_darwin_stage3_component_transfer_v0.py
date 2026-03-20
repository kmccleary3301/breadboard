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


def test_stage3_component_transfer_bundle_builds() -> None:
    write_bootstrap_specs()
    run_bounded_inference_campaign()
    write_operator_ev_report()
    write_topology_ev_report()
    write_invalidity_summary()
    write_verification_bundle()
    write_component_candidates()
    run_component_replay()
    promotion = write_component_promotion_report()
    transfer = write_bounded_transfer_outcomes()
    failed = write_failed_transfer_taxonomy()
    verification = write_component_transfer_verification_bundle()

    promotion_payload = json.loads(open(promotion["out_json"], "r", encoding="utf-8").read())
    transfer_payload = json.loads(open(transfer["out_json"], "r", encoding="utf-8").read())
    failed_payload = json.loads(open(failed["out_json"], "r", encoding="utf-8").read())
    verification_payload = json.loads(open(verification["out_json"], "r", encoding="utf-8").read())

    promoted = next(row for row in promotion_payload["rows"] if row["lane_id"] == "lane.repo_swe")
    systems = next(row for row in promotion_payload["rows"] if row["lane_id"] == "lane.systems")
    retained = next(row for row in transfer_payload["rows"] if row["target_lane_id"] == "lane.systems")
    failed_transfer = next(row for row in transfer_payload["rows"] if row["target_lane_id"] == "lane.scheduling")

    assert promoted["promotion_outcome"] == "promoted"
    assert promoted["replay_status"] in {"supported", "observed_nonconfirming"}
    assert systems["promotion_outcome"] == "not_promoted"
    assert retained["transfer_status"] == "retained"
    assert retained["comparison_valid"] is True
    assert failed_transfer["transfer_status"] == "failed_transfer"
    assert failed_transfer["invalid_reason"] == "unsupported_lane_scope"
    assert failed_payload["reason_counts"]["unsupported_lane_scope"] >= 1
    assert verification_payload["decision_record_count"] >= 4
