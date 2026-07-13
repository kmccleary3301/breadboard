from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler
from scripts.e4_parity.adapters.oh_my_pi_projection_packet import build_projection_packet


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_0_l6_tui_projection"
LANE_PATH = ROOT / "config" / "e4_lanes" / f"{LANE_ID}.yaml"
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inventory_lane() -> dict[str, Any]:
    inventory = _read_json(INVENTORY_PATH)
    return next(row for row in inventory["lanes"] if row["lane_id"] == LANE_ID)


def test_l6_projection_reproduces_ordered_host_only_accepted_event_bytes() -> None:
    lane_def = _read_json(LANE_PATH)
    inventory_lane = _inventory_lane()

    builders, records, derived_facts, _projection_inputs = compiler._execute_record_builders(lane_def, inventory_lane)
    packet = build_projection_packet(lane_def, inventory_lane, records, derived_facts)

    assert [builder["id"] for builder in builders] == ["p6_l6_tui_projection"]
    assert list(records) == [
        "read_success_projection_event",
        "bash_error_projection_event",
        "report_tool_issue_success_projection_event",
    ]
    expected_path = ROOT / lane_def["normalize"]["config"]["roles"]["projection_events"]
    assert packet["projection_events"] == expected_path.read_bytes()

    assert [record["projection_event_id"].rsplit("_", 2)[-2:] for record in records.values()] == [
        ["read", "success"],
        ["bash", "error"],
        ["issue", "success"],
    ]
    for record in records.values():
        assert record["kernel_truth"] is False
        assert record["visibility"] == {
            "model_visible": False,
            "host_visible": True,
            "provider_visible": False,
        }
        assert record["status_frames"][0]["visible_to_model"] is False
        assert record["status_frames"][0]["visible_to_host"] is True
