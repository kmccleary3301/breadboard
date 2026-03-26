from __future__ import annotations

import json

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_tranche1_secondary_audits_v0 import write_secondary_audits


def test_write_secondary_audits_emits_scheduling_and_atp() -> None:
    write_bootstrap_specs()
    summary = write_secondary_audits()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 2
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.scheduling", "lane.atp"}
    for row in payload["lanes"]:
        assert row["topology_supported"] is True
        assert row["invalid_rule_count"] >= 3
