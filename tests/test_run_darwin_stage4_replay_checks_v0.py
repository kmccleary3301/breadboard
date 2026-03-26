from __future__ import annotations

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_stage4_deep_live_reports_v0 import build_stage4_deep_live_reports
from scripts.run_darwin_stage4_deep_live_search_v0 import run_stage4_deep_live_search
from scripts.run_darwin_stage4_replay_checks_v0 import run_stage4_replay_checks


def test_run_stage4_replay_checks_emits_rows(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    run_stage4_deep_live_search()
    build_stage4_deep_live_reports()
    summary = run_stage4_replay_checks()
    assert summary["row_count"] >= 1
