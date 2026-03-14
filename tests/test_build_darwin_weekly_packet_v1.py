from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from scripts.run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from scripts.build_darwin_weekly_packet_v1 import build_weekly_packet, write_weekly_packet


def test_build_weekly_packet_validates_against_contract() -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    write_scorecard()
    packet = build_weekly_packet()
    assert packet["schema"] == "breadboard.darwin.weekly_evidence_packet.v0"
    assert len(packet["lane_summaries"]) == 3


def test_write_weekly_packet_emits_json_and_markdown(tmp_path: Path) -> None:
    write_bootstrap_specs()
    write_topology_runner_manifest()
    write_scorecard()
    summary = write_weekly_packet(tmp_path)
    payload = json.loads((tmp_path / "weekly_evidence_packet.latest.json").read_text(encoding="utf-8"))
    assert summary["lane_count"] == 3
    assert payload["scorecard_refs"]
