from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from atp_hilbert_fixture_utils import write_json

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_no_repair_slice_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_no_repair_slice_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_no_repair_slice_contains_pack_j(tmp_path: Path) -> None:
    index_path = tmp_path / "canonical_baseline_index_v1.json"
    arm_audit_path = tmp_path / "arm_audit_v1.json"
    write_json(
        index_path,
        {
            "entries": [
                {
                    "pack_id": "pack_j_residue_gcd_mix_minif2f_v1",
                    "role": "canonical_primary",
                    "task_count": 6,
                    "candidate_solved": 6,
                    "baseline_solved": 4,
                    "report": "r.json",
                    "validation": "v.json",
                }
            ]
        },
    )
    write_json(
        arm_audit_path,
        {"entries": [{"pack_id": "pack_j_residue_gcd_mix_minif2f_v1", "candidate_arm_mode": "baseline_only"}]},
    )
    payload = module.build_payload(index_path, arm_audit_path)
    assert payload["schema"] == "breadboard.atp_hilbert_no_repair_slice.v1"
    assert payload["entry_count"] >= 1
    pack_ids = {entry["pack_id"] for entry in payload["entries"]}
    assert "pack_j_residue_gcd_mix_minif2f_v1" in pack_ids
