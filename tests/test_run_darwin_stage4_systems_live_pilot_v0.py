from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage4_systems_live_pilot_v0 import run_stage4_systems_live_pilot


def test_run_stage4_systems_live_pilot_emits_selected_systems_arms(monkeypatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage4_systems_live_pilot()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["pilot_lane_id"] == "lane.systems"
    assert payload["lane_count"] == 1
    assert payload["arm_count"] == 3
    assert payload["run_count"] == 6
    assert payload["selected_systems_operator_ids"] == [
        "mut.topology.single_to_pev_v1",
        "mut.policy.shadow_memory_enable_v1",
    ]
    assert payload["execution_modes"] == ["scaffold"]
