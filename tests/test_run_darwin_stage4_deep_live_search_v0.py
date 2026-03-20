from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage4_deep_live_search_v0 import run_stage4_deep_live_search


def test_run_stage4_deep_live_search_emits_rounds_in_scaffold_mode(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage4_deep_live_search()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["primary_lane_count"] == 2
    assert payload["round_count"] == 4
    assert payload["run_count"] > 0
