from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
ARCHIVE_SNAPSHOT = ROOT / "artifacts" / "darwin" / "search" / "archive_snapshot_v1.json"
OUT_PATH = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_invalid_comparison_ledger() -> dict:
    payload = _load_json(SEARCH_SUMMARY)
    archive = _load_json(ARCHIVE_SNAPSHOT)
    invalid_rows = []
    archive_rows = archive.get("rows") or []
    baseline_by_lane = {
        row["lane_id"]: row
        for row in archive_rows
        if row.get("promotion_state") in {"baseline", "retained", "superseded"}
    }
    for lane in payload.get("lanes") or []:
        if lane.get("baseline_primary_score") is None or lane.get("best_mutated_score") is None:
            invalid_rows.append(
                {
                    "lane_id": lane["lane_id"],
                    "reason": "missing_baseline_or_mutated_score",
                }
            )
    for row in archive_rows:
        if not row.get("parent_ids"):
            continue
        baseline = baseline_by_lane.get(row["lane_id"])
        if not baseline:
            invalid_rows.append({"lane_id": row["lane_id"], "candidate_id": row["candidate_id"], "reason": "stale_parent_reference"})
            continue
        if row.get("budget_class") != baseline.get("budget_class"):
            invalid_rows.append({"lane_id": row["lane_id"], "candidate_id": row["candidate_id"], "reason": "budget_class_mismatch"})
    return {
        "schema": "breadboard.darwin.invalid_comparison_ledger.v1",
        "invalid_count": len(invalid_rows),
        "rows": invalid_rows,
    }


def write_invalid_comparison_ledger() -> dict:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = build_invalid_comparison_ledger()
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(OUT_PATH), "invalid_count": payload["invalid_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit DARWIN invalid-comparison ledger for current search-smoke artifacts.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_invalid_comparison_ledger()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"invalid_comparison_ledger={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
