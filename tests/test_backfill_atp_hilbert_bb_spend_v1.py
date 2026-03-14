from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backfill_atp_hilbert_bb_spend_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("backfill_atp_hilbert_bb_spend_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_extract_status_pack_ids_finds_pack_references() -> None:
    text = "Parent tranche pack_d_mixed_induction_numbertheory_minif2f_v1 and child pack_d_induction_core_minif2f_v1"
    assert module._extract_status_pack_ids(text) == [
        "pack_d_induction_core_minif2f_v1",
        "pack_d_mixed_induction_numbertheory_minif2f_v1",
    ]


def test_bb_spend_backfill_payload_contains_entries() -> None:
    payload = module.build_payload(module.DEFAULT_INDEX)
    assert payload["schema"] == "breadboard.atp_hilbert_bb_spend_backfill.v1"
    assert payload["entry_count"] >= 10
    pack_ids = {entry["pack_id"] for entry in payload["entries"]}
    assert "pack_b_core_noimo_minif2f_v1" in pack_ids
    assert any(float(entry["estimated_total_cost_usd"]) > 0 for entry in payload["entries"])
