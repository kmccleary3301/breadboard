from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_repo_swe_family_ab_repair_v0 import build_stage5_repo_swe_family_ab_repair


def test_build_stage5_repo_swe_family_ab_repair_marks_stale_when_round_missing(tmp_path: Path) -> None:
    base_dir = tmp_path / "repo_swe_family_ab"
    summary_path = base_dir / "topology" / "round_r1" / "compounding_pilot_v0.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "run_completion_status": "complete",
                "live_claim_surface_status": "claim_eligible_live",
                "claim_eligible_comparison_count": 6,
                "comparison_valid_count": 6,
                "reuse_lift_count": 2,
                "flat_count": 1,
                "no_lift_count": 0,
            }
        ),
        encoding="utf-8",
    )

    summary = build_stage5_repo_swe_family_ab_repair(rounds=1, out_dir=base_dir)
    payload = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
    assert payload["completion_status"] == "stale_or_incomplete"
    assert payload["bundle_complete"] is False
    assert payload["stale_round_row_count"] == 1
    assert payload["family_selection_status"] == "stale_or_incomplete"


def test_build_stage5_repo_swe_family_ab_repair_rebuilds_complete_surface(tmp_path: Path) -> None:
    base_dir = tmp_path / "repo_swe_family_ab"
    for family_label, reuse, flat, no_lift in (
        ("topology", 1, 1, 2),
        ("tool_scope", 2, 1, 1),
    ):
        summary_path = base_dir / family_label / "round_r1" / "compounding_pilot_v0.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "run_completion_status": "complete",
                    "live_claim_surface_status": "claim_eligible_live",
                    "claim_eligible_comparison_count": 6,
                    "comparison_valid_count": 6,
                    "reuse_lift_count": reuse,
                    "flat_count": flat,
                    "no_lift_count": no_lift,
                }
            ),
            encoding="utf-8",
        )

    summary = build_stage5_repo_swe_family_ab_repair(rounds=1, out_dir=base_dir)
    payload = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
    assert payload["completion_status"] == "complete"
    assert payload["bundle_complete"] is True
    assert payload["family_selection_status"] == "settled_tool_scope"
    assert payload["preferred_family_kind"] == "tool_scope"
