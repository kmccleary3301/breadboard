from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from atp_hilbert_fixture_utils import write_json

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_scoreboard_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_scoreboard_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_scoreboard_payload_uses_primary_roles_for_headline(tmp_path: Path) -> None:
    index_path = tmp_path / "canonical_baseline_index_v1.json"
    spend_path = tmp_path / "bb_spend_backfill_v1.json"
    arm_audit_path = tmp_path / "arm_audit_v1.json"
    status_doc = tmp_path / "docs" / "pack_a.md"
    status_doc.parent.mkdir(parents=True, exist_ok=True)
    status_doc.write_text("maintained Hilbert spend: ~$0.125000\n", encoding="utf-8")
    write_json(tmp_path / "artifacts" / "pack_a_report.json", {})
    write_json(tmp_path / "artifacts" / "pack_a_validation.json", {"ok": True})
    write_json(
        index_path,
        {
            "candidate_system": "bb_hilbert_like",
            "baseline_system": "hilbert_roselab",
            "entries": [
                {
                    "pack_id": "pack_a",
                    "role": "canonical_primary",
                    "status_doc": "docs/pack_a.md",
                    "report": "artifacts/pack_a_report.json",
                    "validation": "artifacts/pack_a_validation.json",
                    "task_count": 5,
                    "candidate_solved": 4,
                    "baseline_solved": 2,
                    "candidate_only": 2,
                    "baseline_only": 0,
                    "both_solved": 2,
                    "both_unsolved": 1,
                }
            ],
        },
    )
    write_json(spend_path, {"entries": [{"pack_id": "pack_a", "estimated_total_cost_usd": 0.03125}]})
    write_json(arm_audit_path, {"entries": [{"pack_id": "pack_a", "candidate_arm_mode": "baseline_only", "focused_task_count": 0}]})
    module.REPO_ROOT = tmp_path
    payload = module.build_payload(index_path, spend_path, arm_audit_path)
    totals = payload["headline_totals"]
    assert payload["schema"] == "breadboard.atp_hilbert_scoreboard.v1"
    assert totals["pack_count"] == 1
    assert totals["candidate_solved_total"] >= totals["baseline_solved_total"]
    assert totals["candidate_ahead_pack_count"] >= 1


def test_scoreboard_entries_include_spend_for_primary_packs(tmp_path: Path) -> None:
    index_path = tmp_path / "canonical_baseline_index_v1.json"
    spend_path = tmp_path / "bb_spend_backfill_v1.json"
    arm_audit_path = tmp_path / "arm_audit_v1.json"
    status_doc = tmp_path / "docs" / "pack_b.md"
    status_doc.parent.mkdir(parents=True, exist_ok=True)
    status_doc.write_text("maintained Hilbert spend: ~$0.250000\n", encoding="utf-8")
    write_json(
        index_path,
        {
            "candidate_system": "bb_hilbert_like",
            "baseline_system": "hilbert_roselab",
            "entries": [
                {
                    "pack_id": "pack_b",
                    "role": "canonical_primary",
                    "status_doc": "docs/pack_b.md",
                    "report": "artifacts/pack_b_report.json",
                    "validation": "artifacts/pack_b_validation.json",
                    "task_count": 4,
                    "candidate_solved": 3,
                    "baseline_solved": 2,
                    "candidate_only": 1,
                    "baseline_only": 0,
                    "both_solved": 2,
                    "both_unsolved": 1,
                }
            ],
        },
    )
    write_json(spend_path, {"entries": [{"pack_id": "pack_b", "estimated_total_cost_usd": 0.125}]})
    write_json(arm_audit_path, {"entries": [{"pack_id": "pack_b", "candidate_arm_mode": "baseline_only", "focused_task_count": 0}]})
    module.REPO_ROOT = tmp_path
    payload = module.build_payload(index_path, spend_path, arm_audit_path)
    primary_entries = [entry for entry in payload["entries"] if entry["is_primary"]]
    assert primary_entries
    assert any(entry["hilbert_exact_spend_usd"] is not None for entry in primary_entries)
    assert payload["headline_totals"]["hilbert_exact_spend_usd_total"] is not None
    assert payload["headline_totals"]["breadboard_estimated_spend_usd_total"] is not None
    assert any(entry.get("candidate_arm_mode") == "baseline_only" for entry in payload["entries"])
