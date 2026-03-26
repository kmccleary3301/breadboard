from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage3_bounded_inference_campaign_v0 import run_bounded_inference_campaign
from scripts.build_darwin_stage3_operator_ev_report_v0 import write_operator_ev_report
from scripts.build_darwin_stage3_topology_ev_report_v0 import write_topology_ev_report
from scripts.build_darwin_stage3_invalidity_summary_v0 import write_invalidity_summary
from scripts.build_darwin_stage3_verification_bundle_v0 import write_verification_bundle


def test_stage3_reports_build_from_campaign_outputs() -> None:
    write_bootstrap_specs()
    run_bounded_inference_campaign()
    operator = write_operator_ev_report()
    topology = write_topology_ev_report()
    invalidity = write_invalidity_summary()
    verification = write_verification_bundle()
    operator_payload = json.loads(open(operator["out_json"], "r", encoding="utf-8").read())
    topology_payload = json.loads(open(topology["out_json"], "r", encoding="utf-8").read())
    verification_payload = json.loads(open(verification["out_json"], "r", encoding="utf-8").read())
    assert operator_payload["row_count"] >= 4
    assert topology_payload["row_count"] >= 2
    assert invalidity["reason_count"] >= 1
    assert verification_payload["comparison_count"] == 10
