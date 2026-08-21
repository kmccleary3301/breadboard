from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from breadboard_engine.conformance.c4_chain import (
    ACCEPTED_RAW_SOURCE_STATUS,
    RAW_SOURCE_STATUS,
)
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
    "oh_my_pi_p3_7_memory_work_compiler",
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


def test_p3_7_uses_shared_validation_only_adapter_without_reactivating_retired_lane() -> None:
    lane_id = "oh_my_pi_p3_7_memory_work_compiler"
    lane_def = _read_json(ROOT / "config" / "e4_lanes" / f"{lane_id}.yaml")
    config = lane_def["normalize"]["config"]
    claim = _read_json(
        ROOT
        / "docs/conformance/e4_target_support/oh_my_pi_p3_7_memory_work_compiler/frozen_c4_support_claim.json"
    )

    builder_ids = [builder["id"] for builder in config["record_builders"]]
    assert lane_def["status"] == "superseded"
    assert lane_def["capture"]["strategy"] == "adapter"
    assert lane_def["capture"]["adapter"] == "oh_my_pi_compiler_capture"
    assert builder_ids == ["p3_7_memory_compaction_plan", "p3_7_work_item"]
    assert set(builder_ids) <= projections.PROJECTIONS.keys()
    assert {
        projections.HELPER_COMPILER_BY_PROJECTION[builder_id]
        for builder_id in builder_ids
    } == {
        "breadboard_engine.compilation.helper_runtime_primitives.validate_memory_work_evidence"
    }
    assert lane_id not in compiler.ADR_AV_3_ACCEPTED_COMPILER_LANES
    assert claim["accepted"] is True


def test_p3_7_scratch_capture_is_fixed_point_and_preserves_frozen_custody(tmp_path: Path) -> None:
    lane_id = "oh_my_pi_p3_7_memory_work_compiler"
    lane_def = _read_json(ROOT / "config" / "e4_lanes" / f"{lane_id}.yaml")
    inventory = _read_json(INVENTORY_PATH)
    inventory_lane = next(row for row in inventory["lanes"] if row["lane_id"] == lane_id)
    lane_root = ROOT / "docs/conformance/e4_target_support" / lane_id
    frozen_paths = [
        lane_root / "frozen_c4_support_claim.json",
        lane_root / "frozen_c4_evidence_manifest.json",
        lane_root / "frozen_c4_validation_report.json",
        lane_root / "frozen_target_freeze_manifest.yaml",
        ROOT / "agent_configs/misc/oh_my_pi_p3_7_memory_work_compiler_v1.yaml",
    ]
    frozen_before = {path: path.read_bytes() for path in frozen_paths}

    scratch_a = tmp_path / "a"
    scratch_b = tmp_path / "b"
    report_a = compiler.capture(lane_def, inventory_lane, promote_accepted=False, out_dir=scratch_a)
    report_b = compiler.capture(lane_def, inventory_lane, promote_accepted=False, out_dir=scratch_b)

    def emitted_bytes(root: Path) -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes()
            for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
        }

    assert report_a["ok"] is report_b["ok"] is True
    assert report_a["promotion_eligible"] is report_b["promotion_eligible"] is False
    assert (
        report_a["canonical_copy_eligible"]
        is report_b["canonical_copy_eligible"]
        is False
    )
    assert report_a["node_gate"] is report_b["node_gate"] is None
    assert emitted_bytes(scratch_a) == emitted_bytes(scratch_b)
    lane_relative = Path("docs/conformance/e4_target_support") / lane_id
    compiled_relative = lane_relative / "compiled_records.json"
    assert (scratch_a / compiled_relative).read_bytes() == (ROOT / compiled_relative).read_bytes()
    prevalidation = _read_json(scratch_a / lane_relative / "prevalidation_report.json")
    assert prevalidation["ok"] is True
    assert prevalidation["accepted"] is False
    raw_capture = _read_json(scratch_a / lane_relative / "raw_capture_manifest.json")
    assert raw_capture["accepted_as_capture_ref"] is False
    assert raw_capture["raw_source_status"] == "derived_from_unavailable_raw"
    assert raw_capture["raw_source_status"] in RAW_SOURCE_STATUS
    assert raw_capture["raw_source_status"] not in ACCEPTED_RAW_SOURCE_STATUS
    assert {path: path.read_bytes() for path in frozen_paths} == frozen_before
    with pytest.raises(ValueError, match="retired validation-only evidence and cannot be promoted"):
        compiler.capture(lane_def, inventory_lane, promote_accepted=True)


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
