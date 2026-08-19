from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation.primitive_records import get_spec, validate_record
from scripts.e4_parity import run_lane
from scripts.e4_parity.lane_definitions import load_lane_def
from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler
from scripts.e4_parity.adapters.oh_my_pi_p6_6_work_item_projection import PROJECTION_ID as WORK_ITEM_V2_PROJECTION_ID


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
LANE_PATH = ROOT / "config" / "e4_lanes" / f"{LANE_ID}.yaml"
V2_LANE_ID = f"{LANE_ID}_v2"
V2_LANE_PATH = ROOT / "config" / "e4_lanes" / f"{V2_LANE_ID}.yaml"
ACCEPTED_WORK_ITEMS_PATH = ROOT / "docs/conformance/e4_target_support" / LANE_ID / "work_items.json"
ACCEPTED_WORK_ITEMS_SHA256 = "0f93c80969b6c9b1c586470b50bafc14e77d284c249e208e6050d8f54483fc7f"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))




def test_accepted_work_item_v1_records_are_byte_frozen_valid_evidence() -> None:
    accepted_bytes = ACCEPTED_WORK_ITEMS_PATH.read_bytes()
    assert hashlib.sha256(accepted_bytes).hexdigest() == ACCEPTED_WORK_ITEMS_SHA256
    records = json.loads(accepted_bytes)
    spec = get_spec("bb.work_item.v1")

    assert [validate_record(spec, record)["work_item_id"] for record in records] == [
        "wi-job-manager-only",
        "wi-joined-subagent",
        "wi-background-job",
        "wi-background-cancel",
        "wi-detached-subagent",
        "wi-detached-job",
    ]


def test_v2_lane_dispatches_end_to_end_while_frozen_v1_cannot_generate(tmp_path: Path) -> None:
    frozen_lane = load_lane_def(LANE_PATH)
    frozen_descriptor = frozen_lane["normalize"]["config"]["record_builders"][0]
    assert frozen_descriptor["projection"] == "p6_6_task_job_subagent"
    assert frozen_descriptor["projection"] not in compiler.PROJECTIONS
    with pytest.raises(ValueError, match="unknown projection: p6_6_task_job_subagent"):
        compiler._record_builders(frozen_lane)

    v2_lane = load_lane_def(V2_LANE_PATH)
    descriptor = v2_lane["normalize"]["config"]["record_builders"][0]
    constants = v2_lane["normalize"]["config"]["projection_constants"]
    assert descriptor["projection"] == descriptor["id"] == WORK_ITEM_V2_PROJECTION_ID
    assert list(constants) == [WORK_ITEM_V2_PROJECTION_ID]

    lane_dir = tmp_path / "lanes"
    lane_dir.mkdir()
    (lane_dir / V2_LANE_PATH.name).write_bytes(V2_LANE_PATH.read_bytes())
    report = run_lane.run_lane(V2_LANE_ID, stage="capture", out_dir=tmp_path, lane_def_dir=lane_dir)
    records = _read_json(tmp_path / v2_lane["normalize"]["config"]["roles"]["work_item_ref"])["records"]
    validated = [validate_record(get_spec("bb.work_item.v2"), record) for record in records]
    assert report["ok"] is True
    assert {record["work_item_id"]: record["status"] for record in validated} == {
        "wi-job-manager-only": "canceled",
        "wi-joined-subagent": "completed",
        "wi-background-job": "completed",
        "wi-background-cancel": "canceled",
        "wi-detached-subagent": "completed",
        "wi-detached-job": "completed",
    }
