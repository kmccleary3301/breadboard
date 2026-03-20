from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_stage4_systems_ev_bundle_v0 import build_stage4_systems_ev_bundle
from scripts.run_darwin_stage4_systems_live_pilot_v0 import run_stage4_systems_live_pilot


def test_build_stage4_systems_ev_bundle_from_existing_runs(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    run_stage4_systems_live_pilot()
    summary = build_stage4_systems_ev_bundle()
    assert summary["comparison_count"] == 4
    assert summary["operator_row_count"] == 2
    assert summary["topology_row_count"] >= 1

    payload = json.loads(open("artifacts/darwin/stage4/systems_live/matched_budget_comparisons_v0.json", "r", encoding="utf-8").read())
    assert all(row["lane_id"] == "lane.systems" for row in payload["rows"])
