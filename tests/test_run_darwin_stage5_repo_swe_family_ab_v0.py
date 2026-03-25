from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage5_repo_swe_family_ab_v0 import run_stage5_repo_swe_family_ab


def test_run_stage5_repo_swe_family_ab_emits_both_family_variants(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage5_repo_swe_family_ab(rounds=1)
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["family_count"] == 2
    assert payload["row_count"] == 2
    assert set(payload["family_totals"]) == {"topology", "tool_scope"}
