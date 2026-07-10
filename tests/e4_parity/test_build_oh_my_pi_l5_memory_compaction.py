from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler
from scripts.e4_parity.adapters.oh_my_pi_projection_packet import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_0_l5_memory_compaction"
LANE_PATH = ROOT / "config" / "e4_lanes" / f"{LANE_ID}.yaml"
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inventory_lane() -> dict[str, Any]:
    inventory = _read_json(INVENTORY_PATH)
    return next(row for row in inventory["lanes"] if row["lane_id"] == LANE_ID)


def test_l5_projection_reproduces_accepted_plan_and_continuation_patch_bytes() -> None:
    lane_def = _read_json(LANE_PATH)

    builders, records, derived_facts, _projection_inputs = compiler._execute_record_builders(lane_def, _inventory_lane())

    assert [builder["id"] for builder in builders] == [
        "p6_l5_memory_compaction_plan",
        "p6_l5_transcript_continuation_patch",
    ]
    assert list(records) == ["memory_compaction_plan", "transcript_continuation_patch"]

    roles = lane_def["normalize"]["config"]["roles"]
    for role in records:
        assert canonical_json_bytes(records[role]) == (ROOT / roles[role]).read_bytes()

    plan = records["memory_compaction_plan"]
    patch = records["transcript_continuation_patch"]
    assert plan["generated_refs"][0]["hash"] == derived_facts["p6_l5_memory_compaction_plan"]["patch_hash"]
    assert plan["hashes"]["compacted_hash"] == derived_facts["p6_l5_memory_compaction_plan"]["patch_hash"]
    assert patch["appended_messages"][0]["firstKeptEntryId"] == plan["preserved_refs"][0]["ref"].split("=", 1)[1]
    assert plan["trigger"]["observed_tokens"] == patch["appended_messages"][0]["tokensBefore"]
