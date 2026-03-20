from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage4_live_economics_pilot_v0 import run_stage4_live_economics_pilot
from scripts.build_darwin_stage4_live_readiness_v0 import build_stage4_live_readiness


def test_build_stage4_live_readiness_reports_missing_provider_inputs() -> None:
    write_bootstrap_specs()
    run_stage4_live_economics_pilot()
    summary = build_stage4_live_readiness()
    payload = json.loads(open(summary["out_json"], "r", encoding="utf-8").read())
    assert payload["ready_for_live_claims"] is False
    assert "provider_credentials_missing" in payload["blockers"]
    assert payload["pilot_execution_modes"] == ["scaffold"]
