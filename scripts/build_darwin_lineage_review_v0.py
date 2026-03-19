from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMOTION_HISTORY = ROOT / "artifacts" / "darwin" / "search" / "promotion_history_v1.json"
ARCHIVE = ROOT / "artifacts" / "darwin" / "search" / "archive_snapshot_v1.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "reviews"
POLICY_REF = "docs/contracts/darwin/DARWIN_LINEAGE_POLICY_V0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_lineage_review() -> dict:
    history = _load_json(PROMOTION_HISTORY)
    archive = _load_json(ARCHIVE)
    archive_rows = archive.get("rows") or []
    rows = []
    for lane in history.get("lanes") or []:
        lane_id = lane["lane_id"]
        lane_archive_rows = [row for row in archive_rows if row["lane_id"] == lane_id]
        rollback_candidate_id = None
        if lane.get("cycle_records"):
            rollback_candidate_id = lane["cycle_records"][-1].get("rollback_candidate_id")
        rows.append(
            {
                "lane_id": lane_id,
                "active_candidate_id": lane["active_candidate_id"],
                "promotion_history_depth": lane["promotion_history_depth"],
                "cycle_count": lane["cycle_count"],
                "rollback_candidate_id": rollback_candidate_id,
                "superseded_candidate_ids": [row["candidate_id"] for row in lane_archive_rows if row.get("promotion_state") == "superseded"],
                "rejected_candidate_ids": [row["candidate_id"] for row in lane_archive_rows if row.get("promotion_state") == "rejected"],
                "promoted_candidate_ids": [row["candidate_id"] for row in lane_archive_rows if row.get("promotion_state") == "promoted"],
                "retained_candidate_ids": [row["candidate_id"] for row in lane_archive_rows if row.get("promotion_state") == "retained"],
                "rollback_ready": rollback_candidate_id is not None,
                "active_lineage_state": "promoted"
                if lane["active_candidate_id"] in [row["candidate_id"] for row in lane_archive_rows if row.get("promotion_state") == "promoted"]
                else "retained_baseline",
                "supersession_chain": [row["candidate_id"] for row in lane_archive_rows if row.get("promotion_state") == "superseded"],
            }
        )
    return {
        "schema": "breadboard.darwin.lineage_review.v0",
        "policy_ref": POLICY_REF,
        "lane_count": len(rows),
        "lanes": rows,
    }


def write_lineage_review(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_lineage_review()
    out_path = out_dir / "lineage_review_v0.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "lane_count": payload["lane_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN lineage derived review.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_lineage_review()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"lineage_review={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
