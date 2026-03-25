from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_darwin_stage4_live_economics_pilot_v0 import _write_json  # noqa: E402
from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_family_ab"
FAMILY_VARIANTS = (
    ("topology", "topology"),
    ("tool_scope", "tool_scope"),
)


def run_stage5_repo_swe_family_ab(*, rounds: int = 2, out_dir: Path = OUT_DIR) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for family_label, family_kind in FAMILY_VARIANTS:
        for round_index in range(1, int(rounds) + 1):
            summary = run_stage5_compounding_pilot(
                lane_id="lane.repo_swe",
                out_dir=out_dir / family_label / f"round_r{round_index}",
                round_index=round_index,
                family_probe_override_kind=family_kind,
            )
            payload = json.loads(Path(str(summary["summary_path"])).read_text(encoding="utf-8"))
            rows.append(
                {
                    "family_label": family_label,
                    "family_probe_override_kind": family_kind,
                    "round_index": round_index,
                    "claim_eligible_comparison_count": int(payload.get("claim_eligible_comparison_count") or 0),
                    "comparison_valid_count": int(payload.get("comparison_valid_count") or 0),
                    "reuse_lift_count": int(payload.get("reuse_lift_count") or 0),
                    "flat_count": int(payload.get("flat_count") or 0),
                    "no_lift_count": int(payload.get("no_lift_count") or 0),
                    "summary_ref": str(Path(str(summary["summary_path"])).relative_to(ROOT)),
                }
            )
    family_totals: dict[str, dict[str, int]] = {}
    for row in rows:
        family_totals.setdefault(
            str(row["family_label"]),
            {
                "claim_eligible_comparison_count": 0,
                "comparison_valid_count": 0,
                "reuse_lift_count": 0,
                "flat_count": 0,
                "no_lift_count": 0,
            },
        )
        family_totals[str(row["family_label"])]["claim_eligible_comparison_count"] += int(row["claim_eligible_comparison_count"])
        family_totals[str(row["family_label"])]["comparison_valid_count"] += int(row["comparison_valid_count"])
        family_totals[str(row["family_label"])]["reuse_lift_count"] += int(row["reuse_lift_count"])
        family_totals[str(row["family_label"])]["flat_count"] += int(row["flat_count"])
        family_totals[str(row["family_label"])]["no_lift_count"] += int(row["no_lift_count"])
    payload = {
        "schema": "breadboard.darwin.stage5.repo_swe_family_ab.v0",
        "round_count": int(rounds),
        "family_count": len(FAMILY_VARIANTS),
        "row_count": len(rows),
        "rows": rows,
        "family_totals": family_totals,
    }
    out_path = out_dir / "repo_swe_family_ab_v0.json"
    _write_json(out_path, payload)
    return {"summary_path": str(out_path), "row_count": len(rows), "family_count": len(FAMILY_VARIANTS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 Repo_SWE family A/B surface.")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage5_repo_swe_family_ab(rounds=args.rounds)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_repo_swe_family_ab={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
