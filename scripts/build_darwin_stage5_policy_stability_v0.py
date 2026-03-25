from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402


DEFAULT_BUNDLE = ROOT / "artifacts" / "darwin" / "stage5" / "multilane" / "multilane_compounding_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "policy_stability"
OUT_JSON = OUT_DIR / "policy_stability_v0.json"
OUT_MD = OUT_DIR / "policy_stability_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _lane_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(bundle.get("rows") or [])
    rows.sort(key=lambda row: (str(row.get("lane_id") or ""), int(row.get("round_index") or 0)))
    return rows


def _summary_counts(row: Mapping[str, Any]) -> dict[str, int]:
    summary_ref = Path(str(row["summary_ref"]))
    if summary_ref.exists():
        payload = _load_json(summary_ref)
        return {
            "claim_eligible_comparison_count": int(payload.get("claim_eligible_comparison_count") or 0),
            "comparison_valid_count": int(payload.get("comparison_valid_count") or 0),
            "reuse_lift_count": int(payload.get("reuse_lift_count") or 0),
            "no_lift_count": int(payload.get("no_lift_count") or 0),
            "flat_count": int(payload.get("flat_count") or 0),
        }
    return {
        "claim_eligible_comparison_count": int(row.get("claim_eligible_comparison_count") or 0),
        "comparison_valid_count": int(row.get("comparison_valid_count") or 0),
        "reuse_lift_count": int(row.get("reuse_lift_count") or 0),
        "no_lift_count": int(row.get("no_lift_count") or 0),
        "flat_count": int(row.get("flat_count") or 0),
    }


def _stability_class(
    *,
    positive_round_count: int,
    negative_round_count: int,
    flat_round_count: int,
    round_count: int,
    reuse_lift_count: int,
    no_lift_count: int,
    flat_count: int,
) -> str:
    if round_count <= 0:
        return "unknown"
    if flat_round_count == round_count and flat_count > 0:
        return "stable_flat"
    if positive_round_count == round_count and reuse_lift_count > no_lift_count:
        return "stable_positive"
    if negative_round_count == round_count and no_lift_count > reuse_lift_count:
        return "stable_negative"
    if reuse_lift_count >= no_lift_count:
        return "mixed_positive" if reuse_lift_count > no_lift_count else "mixed_flat"
    return "mixed_negative"


def build_stage5_policy_stability(
    *,
    bundle_path: Path = DEFAULT_BUNDLE,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    bundle = _load_json(bundle_path)
    lane_groups: dict[str, list[dict[str, Any]]] = {}
    for row in _lane_rows(bundle):
        lane_groups.setdefault(str(row["lane_id"]), []).append(row)

    lane_rows: list[dict[str, Any]] = []
    for lane_id, rows in sorted(lane_groups.items()):
        round_indices = [int(row["round_index"]) for row in rows]
        round_counts = [_summary_counts(row) for row in rows]
        claim_eligible_counts = [row["claim_eligible_comparison_count"] for row in round_counts]
        comparison_valid_counts = [row["comparison_valid_count"] for row in round_counts]
        reuse_counts = [row["reuse_lift_count"] for row in round_counts]
        no_lift_counts = [row["no_lift_count"] for row in round_counts]
        flat_counts = [row["flat_count"] for row in round_counts]
        positive_round_count = sum(1 for reuse_count, no_lift_count in zip(reuse_counts, no_lift_counts) if reuse_count > no_lift_count)
        negative_round_count = sum(1 for reuse_count, no_lift_count in zip(reuse_counts, no_lift_counts) if no_lift_count > reuse_count)
        flat_round_count = sum(1 for reuse_count, no_lift_count in zip(reuse_counts, no_lift_counts) if reuse_count == no_lift_count)
        total_reuse = sum(reuse_counts)
        total_no_lift = sum(no_lift_counts)
        total_flat = sum(flat_counts)
        round_count = len(rows)
        lane_rows.append(
            {
                "lane_id": lane_id,
                "round_count": round_count,
                "round_indices": round_indices,
                "round_claim_eligible_counts": claim_eligible_counts,
                "round_comparison_valid_counts": comparison_valid_counts,
                "round_reuse_lift_counts": reuse_counts,
                "round_no_lift_counts": no_lift_counts,
                "round_flat_counts": flat_counts,
                "positive_round_count": positive_round_count,
                "negative_round_count": negative_round_count,
                "flat_round_count": flat_round_count,
                "claim_eligible_comparison_count": sum(claim_eligible_counts),
                "comparison_valid_count": sum(comparison_valid_counts),
                "reuse_lift_count": total_reuse,
                "no_lift_count": total_no_lift,
                "flat_count": total_flat,
                "reuse_lift_rate": round(total_reuse / max(sum(claim_eligible_counts), 1), 4),
                "stability_class": _stability_class(
                    positive_round_count=positive_round_count,
                    negative_round_count=negative_round_count,
                    flat_round_count=flat_round_count,
                    round_count=round_count,
                    reuse_lift_count=total_reuse,
                    no_lift_count=total_no_lift,
                    flat_count=total_flat,
                ),
                "policy_review_conclusion": "continue" if total_reuse + total_flat >= total_no_lift else "tighten",
                "summary_refs": [str(row["summary_ref"]) for row in rows],
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage5.policy_stability.v0",
        "source_bundle_ref": path_ref(bundle_path),
        "lane_count": len(lane_rows),
        "row_count": len(lane_rows),
        "rows": lane_rows,
    }
    lines = ["# Stage-5 Policy Stability", ""]
    lines.append(f"- source bundle: `{path_ref(bundle_path)}`")
    for row in lane_rows:
        lines.append(
            f"- `{row['lane_id']}`: stability=`{row['stability_class']}`, "
            f"reuse_lift=`{row['reuse_lift_count']}`, flat=`{row['flat_count']}`, no_lift=`{row['no_lift_count']}`, "
            f"positive_rounds=`{row['positive_round_count']}/{row['round_count']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "lane_count": len(lane_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 policy stability report.")
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_policy_stability(bundle_path=Path(args.bundle))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_policy_stability={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
