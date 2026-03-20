from __future__ import annotations

import json
import pytest

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage4_live_economics_pilot_v0 import run_stage4_live_economics_pilot
from scripts.build_darwin_stage4_live_readiness_v0 import build_stage4_live_readiness


def test_build_stage4_live_readiness_reports_missing_provider_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", raising=False)
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", raising=False)
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", raising=False)
    write_bootstrap_specs()
    run_stage4_live_economics_pilot()
    summary = build_stage4_live_readiness()
    payload = json.loads(open(summary["out_json"], "r", encoding="utf-8").read())
    assert payload["ready_for_live_claims"] is False
    assert "provider_credentials_missing" in payload["blockers"]
    assert payload["pilot_execution_modes"] == ["scaffold"]
