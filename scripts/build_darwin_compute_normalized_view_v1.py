from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
ARCHIVE = ROOT / "artifacts" / "darwin" / "search" / "archive_snapshot_v1.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "scorecards"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_per_second(score: float, wall_clock_ms: int) -> float:
    seconds = max(wall_clock_ms / 1000.0, 0.001)
    return round(score / seconds, 6)


def build_compute_normalized_view() -> dict:
    live = _load_json(LIVE_SUMMARY)
    search = _load_json(SEARCH_SUMMARY) if SEARCH_SUMMARY.exists() else {"lanes": []}
    archive = _load_json(ARCHIVE)
    archive_by_candidate = {row["candidate_id"]: row for row in archive.get("rows") or []}
    search_by_lane = {row["lane_id"]: row for row in search.get("lanes") or []}
    rows = []
    for lane in live.get("lanes") or []:
        lane_id = lane["lane_id"]
        baseline_eval = _load_json(ROOT / lane["evaluation_ref"])
        baseline_score = float(lane["primary_score"])
        baseline_rate = _score_per_second(baseline_score, int(baseline_eval["wall_clock_ms"]))
        search_row = search_by_lane.get(lane_id)
        promoted_candidate_id = search_row.get("active_promoted_candidate_id") if search_row else None
        promoted_score = baseline_score
        promoted_rate = baseline_rate
        promoted_eval_ref = lane["evaluation_ref"]
        if promoted_candidate_id:
            promoted_row = archive_by_candidate[promoted_candidate_id]
            promoted_eval = _load_json(ROOT / promoted_row["evaluation_ref"])
            promoted_score = float(promoted_row["primary_score"])
            promoted_rate = _score_per_second(promoted_score, int(promoted_eval["wall_clock_ms"]))
            promoted_eval_ref = promoted_row["evaluation_ref"]
        rows.append(
            {
                "lane_id": lane_id,
                "baseline_candidate_id": lane["candidate_id"],
                "baseline_score": baseline_score,
                "baseline_eval_ref": lane["evaluation_ref"],
                "baseline_score_per_second": baseline_rate,
                "active_candidate_id": promoted_candidate_id or lane["candidate_id"],
                "active_score": promoted_score,
                "active_eval_ref": promoted_eval_ref,
                "active_score_per_second": promoted_rate,
                "score_delta": round(promoted_score - baseline_score, 6),
                "rate_delta": round(promoted_rate - baseline_rate, 6),
            }
        )
    return {
        "schema": "breadboard.darwin.compute_normalized_view.v1",
        "lane_count": len(rows),
        "lanes": rows,
    }


def write_compute_normalized_view(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_compute_normalized_view()
    out_path = out_dir / "compute_normalized_view_v1.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "lane_count": payload["lane_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compute-normalized DARWIN scorecard companion view.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_compute_normalized_view()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"compute_normalized_view={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
