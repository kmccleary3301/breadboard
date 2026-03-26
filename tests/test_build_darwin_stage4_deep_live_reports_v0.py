from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_stage4_deep_live_reports_v0 import build_stage4_deep_live_reports
from scripts.run_darwin_stage4_deep_live_search_v0 import run_stage4_deep_live_search


def test_build_stage4_deep_live_reports_from_existing_runs(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    run_stage4_deep_live_search()
    summary = build_stage4_deep_live_reports()
    assert summary["operator_round_row_count"] >= 1
    assert summary["topology_round_row_count"] >= 1
    payload = json.loads(open("artifacts/darwin/stage4/deep_live_search/strongest_families_v0.json", "r", encoding="utf-8").read())
    assert payload["row_count"] >= 1
