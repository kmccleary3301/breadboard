from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_scoreboard_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_scoreboard_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_scoreboard_payload_uses_primary_roles_for_headline() -> None:
    payload = module.build_payload(module.DEFAULT_INDEX)
    totals = payload["headline_totals"]
    assert payload["schema"] == "breadboard.atp_hilbert_scoreboard.v1"
    assert totals["pack_count"] >= 8
    assert totals["candidate_solved_total"] >= totals["baseline_solved_total"]
    assert totals["candidate_ahead_pack_count"] >= 1


def test_scoreboard_entries_include_spend_for_primary_packs() -> None:
    payload = module.build_payload(module.DEFAULT_INDEX, module.DEFAULT_BB_SPEND, module.DEFAULT_ARM_AUDIT)
    primary_entries = [entry for entry in payload["entries"] if entry["is_primary"]]
    assert primary_entries
    assert any(entry["hilbert_exact_spend_usd"] is not None for entry in primary_entries)
    assert payload["headline_totals"]["hilbert_exact_spend_usd_total"] is not None
    assert payload["headline_totals"]["breadboard_estimated_spend_usd_total"] is not None
    assert any(entry.get("candidate_arm_mode") == "baseline_only" for entry in payload["entries"])
