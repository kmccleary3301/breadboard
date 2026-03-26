from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_rollup_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_rollup_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_rollup_builds_all_expected_files(tmp_path: Path) -> None:
    module.build_baseline_payload = lambda: {
        "schema": "breadboard.atp_hilbert_canonical_baselines.v1",
        "candidate_system": "bb_hilbert_like",
        "baseline_system": "hilbert_roselab",
        "entry_count": 1,
        "entries": [
            {
                "pack_id": "pack_fixture",
                "role": "canonical_primary",
                "task_count": 3,
                "candidate_solved": 2,
                "baseline_solved": 1,
                "candidate_only": 1,
                "baseline_only": 0,
                "both_solved": 1,
                "both_unsolved": 1,
                "status_doc": "docs/fixture.md",
                "report": "artifacts/fixture_report.json",
                "validation": "artifacts/fixture_validation.json",
                "note": "fixture",
            }
        ],
    }
    module.build_arm_audit_payload = lambda: {
        "schema": "breadboard.atp_hilbert_arm_audit.v1",
        "entry_count": 1,
        "entries": [{"pack_id": "pack_fixture", "candidate_arm_mode": "baseline_only", "focused_task_count": 0, "note": "fixture"}],
        "mode_counts": {"baseline_only": 1},
    }
    module.build_bb_spend_payload = lambda baseline_json: {
        "schema": "breadboard.atp_hilbert_bb_spend_backfill.v1",
        "entries": [{"pack_id": "pack_fixture", "estimated_total_cost_usd": 0.25}],
    }
    module.build_scoreboard_payload = lambda baseline_json, bb_spend_json, arm_audit_json: {
        "schema": "breadboard.atp_hilbert_scoreboard.v1",
        "candidate_system": "bb_hilbert_like",
        "baseline_system": "hilbert_roselab",
        "headline_totals": {
            "pack_count": 1,
            "task_count": 3,
            "candidate_solved_total": 2,
            "baseline_solved_total": 1,
            "candidate_only_total": 1,
            "baseline_only_total": 0,
            "candidate_ahead_pack_count": 1,
            "tied_pack_count": 0,
            "baseline_ahead_pack_count": 0,
            "hilbert_exact_spend_usd_total": 0.125,
            "breadboard_estimated_spend_usd_total": 0.25,
        },
        "arm_mode_breakdown": {"baseline_only": 1},
        "entries": [],
    }
    payload = module.build_rollup(tmp_path)
    assert payload["schema"] == "breadboard.atp_hilbert_rollup_manifest.v1"
    baseline_json = tmp_path / "canonical_baseline_index_v1.json"
    arm_audit_json = tmp_path / "arm_audit_v1.json"
    bb_spend_json = tmp_path / "bb_spend_backfill_v1.json"
    scoreboard_json = tmp_path / "scoreboard_v1.json"
    rollup_json = tmp_path / "rollup_manifest_v1.json"
    assert baseline_json.exists()
    assert arm_audit_json.exists()
    assert bb_spend_json.exists()
    assert scoreboard_json.exists()
    assert rollup_json.exists()
    scoreboard = json.loads(scoreboard_json.read_text())
    assert scoreboard["headline_totals"]["pack_count"] == 1
