from __future__ import annotations

import json

from scripts.build_darwin_future_lane_placeholders_v0 import write_future_lane_placeholders


def test_write_future_lane_placeholders_emits_scheduling_and_research() -> None:
    summary = write_future_lane_placeholders()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 2
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.scheduling", "lane.research"}
