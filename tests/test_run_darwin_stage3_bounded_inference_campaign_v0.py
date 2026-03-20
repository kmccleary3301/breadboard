from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage3_bounded_inference_campaign_v0 import run_bounded_inference_campaign


def test_run_bounded_inference_campaign_emits_campaign_artifacts() -> None:
    write_bootstrap_specs()
    summary = run_bounded_inference_campaign()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 3
    assert payload["arm_count"] == 8
    assert payload["run_count"] == 16
    assert payload["comparison_count"] == 10
    assert payload["execution_modes"]
