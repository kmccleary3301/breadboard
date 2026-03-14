from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
COMPUTE_VIEW = ROOT / "artifacts" / "darwin" / "scorecards" / "compute_normalized_view_v1.json"
TRANSFER_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "transfer_ledger_v1.json"
PROMOTION_HISTORY = ROOT / "artifacts" / "darwin" / "search" / "promotion_history_v1.json"
WEEKLY = ROOT / "artifacts" / "darwin" / "weekly" / "weekly_evidence_packet.latest.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "dossiers"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_dossier() -> dict:
    scorecard = _load_json(SCORECARD)
    compute_view = _load_json(COMPUTE_VIEW)
    transfer = _load_json(TRANSFER_LEDGER)
    history = _load_json(PROMOTION_HISTORY)
    weekly = _load_json(WEEKLY)
    transfer_successes = [row for row in transfer.get("attempts") or [] if row.get("result") == "improved" and row.get("comparison_valid")]
    deepest_history = max((row["promotion_history_depth"] for row in history.get("lanes") or []), default=0)
    return {
        "schema": "breadboard.darwin.comparative_dossier.v1",
        "live_lane_count": scorecard["lane_count"],
        "search_enabled_lane_count": sum(1 for row in scorecard.get("lanes") or [] if row.get("search_enabled")),
        "transfer_attempt_count": len(transfer.get("attempts") or []),
        "successful_transfer_count": len(transfer_successes),
        "max_promotion_history_depth": deepest_history,
        "weekly_packet_ref": str(WEEKLY.relative_to(ROOT)),
        "scorecard_ref": str(SCORECARD.relative_to(ROOT)),
        "compute_view_ref": str(COMPUTE_VIEW.relative_to(ROOT)),
        "lane_rows": [
            {
                "lane_id": row["lane_id"],
                "search_maturity": row.get("search_maturity"),
                "promotion_cycle_count": row.get("promotion_cycle_count", 0),
                "transfer_attempt_count": row.get("transfer_attempt_count", 0),
                "active_candidate_id": row.get("active_promoted_candidate_id") or row.get("best_candidate_id"),
            }
            for row in scorecard.get("lanes") or []
        ],
        "compute_rows": compute_view.get("lanes") or [],
        "weekly_headlines": weekly.get("lane_summaries") or [],
    }


def _to_markdown(payload: dict) -> str:
    lines = [
        "# DARWIN Comparative Dossier v1",
        "",
        f"- live_lane_count: `{payload['live_lane_count']}`",
        f"- search_enabled_lane_count: `{payload['search_enabled_lane_count']}`",
        f"- transfer_attempt_count: `{payload['transfer_attempt_count']}`",
        f"- successful_transfer_count: `{payload['successful_transfer_count']}`",
        f"- max_promotion_history_depth: `{payload['max_promotion_history_depth']}`",
        "",
        "## Lane Summary",
        "",
    ]
    for row in payload.get("lane_rows") or []:
        lines.append(
            f"- `{row['lane_id']}` — maturity `{row['search_maturity']}` — cycles `{row['promotion_cycle_count']}` — transfers `{row['transfer_attempt_count']}` — active `{row['active_candidate_id']}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_dossier(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_dossier()
    out_json = out_dir / "comparative_dossier_v1.json"
    out_md = out_dir / "comparative_dossier_v1.md"
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    return {"out_json": str(out_json), "out_md": str(out_md), "live_lane_count": payload["live_lane_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN Phase-1 comparative dossier draft.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_dossier()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"comparative_dossier_json={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
