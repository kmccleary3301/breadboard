from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler
from scripts.e4_parity.adapters.oh_my_pi_p6_6_work_item_projection import project_work_items
from scripts.e4_parity.adapters.oh_my_pi_projection_packet import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
LANE_PATH = ROOT / "config" / "e4_lanes" / f"{LANE_ID}.yaml"
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inventory_lane() -> dict[str, Any]:
    inventory = _read_json(INVENTORY_PATH)
    return next(row for row in inventory["lanes"] if row["lane_id"] == LANE_ID)


def test_task_projection_reproduces_accepted_lifecycle_records_and_rejects_job_only_evidence() -> None:
    lane_def = _read_json(LANE_PATH)
    descriptor = lane_def["normalize"]["config"]["record_builders"][0]
    inputs = compiler._load_projection_inputs(lane_def)
    context = compiler._projection_context(lane_def, _inventory_lane(), descriptor, inputs)

    projected = project_work_items(context)
    rows = projected["records"]
    records = [row["value"] for row in rows]

    assert [row["record_key"] for row in rows] == [
        "wi-job-manager-only",
        "wi-joined-subagent",
        "wi-background-job",
        "wi-background-cancel",
        "wi-detached-subagent",
        "wi-detached-job",
    ]
    expected_path = ROOT / "docs/conformance/e4_target_support" / LANE_ID / "work_items.json"
    assert canonical_json_bytes(records) == expected_path.read_bytes()

    assert {record["work_item_id"]: record["state"]["status"] for record in records} == {
        "wi-job-manager-only": "cancelled",
        "wi-joined-subagent": "completed",
        "wi-background-job": "completed",
        "wi-background-cancel": "cancelled",
        "wi-detached-subagent": "completed",
        "wi-detached-job": "completed",
    }
    assert projected["derived_facts"] == {
        "work_item_schema_valid": True,
        "joined_subagent_target_capture_observed": True,
        "detached_subagent_target_capture_observed": True,
        "background_job_observed": True,
        "cancel_observed": True,
        "job_manager_only_evidence": False,
        "provider_authenticated_capture": True,
        "provider_dispatch_observed": True,
        "provider_parity_claimed": False,
        "network_observed": True,
        "fetch_event_count": 2,
    }
