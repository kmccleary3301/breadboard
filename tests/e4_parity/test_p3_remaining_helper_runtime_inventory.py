from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler
from scripts.e4_parity.adapters.oh_my_pi_projection_packet import canonical_json_bytes
from scripts.e4_parity.adapters import oh_my_pi_p3_remaining_projections as projections


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
P3_LANE_IDS = [
    "oh_my_pi_p3_2_context_resource_pack_compiler",
    "oh_my_pi_p3_3_capability_registry_compiler",
    "oh_my_pi_p3_4_extension_hook_execution_compiler",
    "oh_my_pi_p3_5_resource_blob_compiler",
    "oh_my_pi_p3_6_protocol_provider_policy_compiler",
    "oh_my_pi_p3_8_projection_broker_adapter",
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_context_resource_pack_projection_is_independent_of_candidate_root() -> None:
    project = projections.PROJECTIONS["p3_2_context_resource_pack"]
    candidate_a = project({"lane_id": "context_pack", "root": "/tmp/candidate-a"})
    candidate_b = project({"lane_id": "context_pack", "root": "/tmp/candidate-b"})

    assert candidate_a == candidate_b
    generated_cwd = candidate_a["records"][0]["value"]["sources"][2]
    assert generated_cwd["source_id"] == "generated_cwd"
    assert generated_cwd["content_hash"] == (
        "sha256:d15580757e216640dbb75339468c374e60e202c7a0339603f02605666dfcc9ab"
    )


def test_p3_7_is_frozen_validation_only() -> None:
    lane_id = "oh_my_pi_p3_7_memory_work_compiler"
    lane_def = _read_json(ROOT / "config" / "e4_lanes" / f"{lane_id}.yaml")
    config = lane_def["normalize"]["config"]
    claim = _read_json(ROOT / "docs/conformance/support_claims/oh_my_pi_p3_7_memory_work_compiler_v1_c4_support_claim.json")

    assert lane_def["status"] == "superseded"
    assert lane_def["capture"]["strategy"] == "replay_dump"
    assert lane_def["capture"]["adapter"] is None
    assert "record_builders" not in config
    assert not any(projection_id.startswith("p3_7_") for projection_id in projections.PROJECTIONS)
    assert lane_id not in compiler.ADR_AV_3_ACCEPTED_COMPILER_LANES
    assert claim["accepted"] is True


@pytest.mark.parametrize("lane_id", P3_LANE_IDS)
def test_remaining_p3_lane_projections_reproduce_accepted_compiled_record_bytes(lane_id: str) -> None:
    lane_def = _read_json(ROOT / "config" / "e4_lanes" / f"{lane_id}.yaml")
    inventory = _read_json(INVENTORY_PATH)
    inventory_lane = next(row for row in inventory["lanes"] if row["lane_id"] == lane_id)

    builders, records, _derived_facts, _projection_inputs = compiler._execute_record_builders(lane_def, inventory_lane)

    expected_record_order = [
        record_key
        for descriptor in lane_def["normalize"]["config"]["record_builders"]
        for record_key in descriptor["records"]
    ]
    assert [builder["id"] for builder in builders] == [
        descriptor["id"] for descriptor in lane_def["normalize"]["config"]["record_builders"]
    ]
    assert list(records) == expected_record_order

    accepted_path = ROOT / "docs" / "conformance" / "e4_target_support" / lane_id / "compiled_records.json"
    accepted = _read_json(accepted_path)
    rebuilt = {
        "config_id": lane_def["config_id"],
        "lane_id": lane_id,
        "records": records,
        "schema_version": "bb.e4.helper_runtime_compiled_records.v1",
    }
    assert rebuilt == accepted
    assert canonical_json_bytes(rebuilt) == accepted_path.read_bytes()
