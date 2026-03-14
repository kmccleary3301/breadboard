from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_repair_intensity_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_repair_intensity_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repair_intensity_payload_has_primary_ratios() -> None:
    payload = module.build_payload(module.DEFAULT_INDEX, module.DEFAULT_ARM_AUDIT)
    assert payload["schema"] == "breadboard.atp_hilbert_repair_intensity.v1"
    assert payload["primary_pack_count"] >= 8
    assert payload["primary_focused_pack_count"] >= 1
    assert payload["primary_focused_task_ratio"] > 0
