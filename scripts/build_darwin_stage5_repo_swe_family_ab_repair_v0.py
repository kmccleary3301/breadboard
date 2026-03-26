from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from scripts.run_darwin_stage5_repo_swe_family_ab_v0 import (  # noqa: E402
    FAMILY_VARIANTS,
    _atomic_write_json,
    _family_selection_status,
)


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_family_ab"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _round_row(
    *,
    family_label: str,
    family_kind: str,
    round_index: int,
    summary_path: Path,
) -> dict[str, Any]:
    if not summary_path.exists():
        return {
            "family_label": family_label,
            "family_probe_override_kind": family_kind,
            "round_index": int(round_index),
            "claim_eligible_comparison_count": 0,
            "comparison_valid_count": 0,
            "reuse_lift_count": 0,
            "flat_count": 0,
            "no_lift_count": 0,
            "round_is_complete": False,
            "summary_ref": path_ref(summary_path),
        }
    payload = _load_json(summary_path)
    round_is_complete = (
        str(payload.get("run_completion_status") or "") == "complete"
        and str(payload.get("live_claim_surface_status") or "") == "claim_eligible_live"
        and int(payload.get("comparison_valid_count") or 0) > 0
        and int(payload.get("claim_eligible_comparison_count") or 0) > 0
    )
    return {
        "family_label": family_label,
        "family_probe_override_kind": family_kind,
        "round_index": int(round_index),
        "claim_eligible_comparison_count": int(payload.get("claim_eligible_comparison_count") or 0),
        "comparison_valid_count": int(payload.get("comparison_valid_count") or 0),
        "reuse_lift_count": int(payload.get("reuse_lift_count") or 0),
        "flat_count": int(payload.get("flat_count") or 0),
        "no_lift_count": int(payload.get("no_lift_count") or 0),
        "round_is_complete": round_is_complete,
        "summary_ref": path_ref(summary_path),
    }


def build_stage5_repo_swe_family_ab_repair(*, rounds: int = 2, out_dir: Path = OUT_DIR) -> dict[str, object]:
    rows: list[dict[str, Any]] = []
    expected_row_count = len(FAMILY_VARIANTS) * int(rounds)
    for family_label, family_kind in FAMILY_VARIANTS:
        for round_index in range(1, int(rounds) + 1):
            summary_path = out_dir / family_label / f"round_r{round_index}" / "compounding_pilot_v0.json"
            rows.append(
                _round_row(
                    family_label=family_label,
                    family_kind=family_kind,
                    round_index=round_index,
                    summary_path=summary_path,
                )
            )

    family_totals: dict[str, dict[str, int]] = {}
    valid_round_row_count = 0
    stale_round_row_count = 0
    for row in rows:
        family_label = str(row["family_label"])
        family_totals.setdefault(
            family_label,
            {
                "claim_eligible_comparison_count": 0,
                "comparison_valid_count": 0,
                "reuse_lift_count": 0,
                "flat_count": 0,
                "no_lift_count": 0,
            },
        )
        family_totals[family_label]["claim_eligible_comparison_count"] += int(row["claim_eligible_comparison_count"])
        family_totals[family_label]["comparison_valid_count"] += int(row["comparison_valid_count"])
        family_totals[family_label]["reuse_lift_count"] += int(row["reuse_lift_count"])
        family_totals[family_label]["flat_count"] += int(row["flat_count"])
        family_totals[family_label]["no_lift_count"] += int(row["no_lift_count"])
        if bool(row["round_is_complete"]):
            valid_round_row_count += 1
        else:
            stale_round_row_count += 1

    bundle_complete = len(rows) == expected_row_count and stale_round_row_count == 0
    family_selection_status, family_selection_reason, preferred_family_kind = _family_selection_status(
        family_totals,
        complete=bundle_complete,
    )
    payload = {
        "schema": "breadboard.darwin.stage5.repo_swe_family_ab.v0",
        "round_count": int(rounds),
        "family_count": len(FAMILY_VARIANTS),
        "expected_row_count": expected_row_count,
        "row_count": len(rows),
        "completion_status": "complete" if bundle_complete else "stale_or_incomplete",
        "bundle_complete": bundle_complete,
        "valid_round_row_count": valid_round_row_count,
        "stale_round_row_count": stale_round_row_count,
        "family_selection_status": family_selection_status,
        "family_selection_reason": family_selection_reason,
        "preferred_family_kind": preferred_family_kind,
        "rows": rows,
        "family_totals": family_totals,
    }
    out_path = out_dir / "repo_swe_family_ab_v0.json"
    _atomic_write_json(out_path, payload)
    return {
        "summary_path": str(out_path),
        "completion_status": payload["completion_status"],
        "family_selection_status": family_selection_status,
        "row_count": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair/rebuild the Stage-5 Repo_SWE family A/B surface from round summaries.")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_repo_swe_family_ab_repair(rounds=args.rounds)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_repo_swe_family_ab_repair={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
