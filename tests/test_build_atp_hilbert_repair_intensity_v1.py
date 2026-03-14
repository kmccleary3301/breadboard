from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from atp_hilbert_fixture_utils import write_json

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_repair_intensity_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_repair_intensity_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repair_intensity_payload_has_primary_ratios(tmp_path: Path) -> None:
    index_path = tmp_path / "canonical_baseline_index_v1.json"
    arm_audit_path = tmp_path / "arm_audit_v1.json"
    write_json(
        index_path,
        {
            "entries": [
                {"pack_id": "pack_f", "role": "canonical_primary", "task_count": 6},
                {"pack_id": "pack_j", "role": "canonical_primary", "task_count": 6},
            ]
        },
    )
    write_json(
        arm_audit_path,
        {
            "entries": [
                {"pack_id": "pack_f", "candidate_arm_mode": "focused_repaired", "focused_task_count": 3},
                {"pack_id": "pack_j", "candidate_arm_mode": "baseline_only", "focused_task_count": 0},
            ]
        },
    )
    payload = module.build_payload(index_path, arm_audit_path)
    assert payload["schema"] == "breadboard.atp_hilbert_repair_intensity.v1"
    assert payload["primary_pack_count"] == 2
    assert payload["primary_focused_pack_count"] >= 1
    assert payload["primary_focused_task_ratio"] > 0
