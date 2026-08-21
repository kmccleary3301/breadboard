from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_0_l6_tui_projection"
LANE_PATH = ROOT / "config" / "e4_lanes" / f"{LANE_ID}.yaml"
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inventory_lane() -> dict[str, Any]:
    inventory = _read_json(INVENTORY_PATH)
    return next(row for row in inventory["lanes"] if row["lane_id"] == LANE_ID)


def test_generic_projection_packet_writes_byte_identical_l6_roles_only_to_scratch(tmp_path: Path) -> None:
    lane_def = _read_json(LANE_PATH)
    inventory_lane = _inventory_lane()
    config = lane_def["normalize"]["config"]
    required_roles = config["packet_constants"]["required_roles"]
    logical_roles = config["roles"]
    accepted_before = {role: (ROOT / logical_roles[role]).read_bytes() for role in required_roles}

    builders, records, derived_facts, projection_inputs = compiler._execute_record_builders(lane_def, inventory_lane)
    result = compiler._capture_projection_packet(
        lane_def,
        inventory_lane,
        promote_accepted=False,
        out_dir=tmp_path,
        builders=builders,
        records=records,
        derived_facts=derived_facts,
        projection_inputs=projection_inputs,
    )

    assert result["ok"] is True
    assert result["node_gate"] == logical_roles["node_gate"]
    for role, accepted_bytes in accepted_before.items():
        assert (tmp_path / logical_roles[role]).read_bytes() == accepted_bytes
        assert (ROOT / logical_roles[role]).read_bytes() == accepted_bytes

    written = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert written == {logical_roles[role] for role in required_roles}
