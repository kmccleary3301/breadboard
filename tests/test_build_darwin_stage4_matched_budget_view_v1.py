from __future__ import annotations

import json

import pytest

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_stage4_matched_budget_view_v1 import build_stage4_matched_budget_view
from scripts.run_darwin_stage4_live_economics_pilot_v0 import run_stage4_live_economics_pilot


def test_build_stage4_matched_budget_view_normalizes_topology_and_toolscope_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", raising=False)
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", raising=False)
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", raising=False)
    write_bootstrap_specs()
    run_stage4_live_economics_pilot()
    summary = build_stage4_matched_budget_view()
    assert summary["comparison_count"] == 6
    payload = json.loads(open("artifacts/darwin/stage4/live_economics/matched_budget_comparisons_v0.json", "r", encoding="utf-8").read())
    invalid_reasons = {row["invalid_reason"] for row in payload["rows"] if row["invalid_reason"]}
    assert "support_envelope_digest_mismatch" not in invalid_reasons
    assert invalid_reasons == {"budget_class_mismatch"}
