from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_no_repair_slice_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_no_repair_slice_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_no_repair_slice_contains_pack_j() -> None:
    payload = module.build_payload(module.DEFAULT_INDEX, module.DEFAULT_ARM_AUDIT)
    assert payload["schema"] == "breadboard.atp_hilbert_no_repair_slice.v1"
    assert payload["entry_count"] >= 1
    pack_ids = {entry["pack_id"] for entry in payload["entries"]}
    assert "pack_j_residue_gcd_mix_minif2f_v1" in pack_ids
