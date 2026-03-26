from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_arm_audit_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_arm_audit_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_arm_audit_contains_baseline_and_repaired_modes() -> None:
    payload = module.build_payload()
    assert payload["schema"] == "breadboard.atp_hilbert_arm_audit.v1"
    assert payload["mode_counts"]["focused_repaired"] >= 1
    assert payload["mode_counts"]["baseline_only"] >= 1
    entries = {entry["pack_id"]: entry for entry in payload["entries"]}
    assert entries["pack_j_residue_gcd_mix_minif2f_v1"]["candidate_arm_mode"] == "baseline_only"
    assert entries["pack_f_discrete_arithmetic_mix_minif2f_v1"]["focused_task_count"] == 5
