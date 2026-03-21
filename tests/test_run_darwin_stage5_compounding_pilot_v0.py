from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot
from scripts.run_darwin_stage5_systems_compounding_pilot_v0 import main as systems_main


def test_run_stage5_compounding_pilot_emits_compounding_cases_in_scaffold_mode(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage5_compounding_pilot(lane_id="lane.repo_swe")
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["pilot_lane_id"] == "lane.repo_swe"
    assert payload["arm_count"] >= 3
    assert payload["comparison_count"] >= 2
    assert payload["compounding_case_count"] >= 1
    assert payload["claim_eligible_comparison_count"] >= 0
    assert "provider_origin_counts" in payload


def test_run_stage5_systems_compounding_pilot_emits_compounding_cases_in_scaffold_mode(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    assert systems_main() == 0
    payload = json.loads(
        open(
            "artifacts/darwin/stage5/tranche1/lane_systems/compounding_pilot_v0.json",
            "r",
            encoding="utf-8",
        ).read()
    )
    assert payload["pilot_lane_id"] == "lane.systems"
    assert payload["comparison_count"] >= 2
    assert payload["compounding_case_count"] >= 1
