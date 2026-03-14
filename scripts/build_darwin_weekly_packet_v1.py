from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import validate_weekly_evidence_packet


DEFAULT_SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
DEFAULT_LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
DEFAULT_CLAIM_LEDGER = ROOT / "artifacts" / "darwin" / "claims" / "claim_ledger_v1.json"
DEFAULT_EVIDENCE_MANIFEST = ROOT / "artifacts" / "darwin" / "evidence" / "evidence_bundle_manifest_v2.json"
DEFAULT_SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
DEFAULT_ARCHIVE_SNAPSHOT = ROOT / "artifacts" / "darwin" / "search" / "archive_snapshot_v1.json"
DEFAULT_INVALID_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"
DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "weekly"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _week_start_utc() -> str:
    now = datetime.now(timezone.utc)
    monday = now.date().toordinal() - now.weekday()
    week_start = datetime.fromordinal(monday).replace(tzinfo=timezone.utc)
    return week_start.strftime("%Y-%m-%dT00:00:00Z")


def build_weekly_packet(scorecard_path: Path = DEFAULT_SCORECARD, *, include_search: bool = False) -> dict:
    scorecard = _load_json(scorecard_path)
    live_summary = _load_json(DEFAULT_LIVE_SUMMARY) if DEFAULT_LIVE_SUMMARY.exists() else {"lanes": []}
    claim_ledger = _load_json(DEFAULT_CLAIM_LEDGER) if DEFAULT_CLAIM_LEDGER.exists() else {"claims": []}
    search_summary = _load_json(DEFAULT_SEARCH_SUMMARY) if include_search and DEFAULT_SEARCH_SUMMARY.exists() else {"lanes": []}
    search_by_lane = {row["lane_id"]: row for row in search_summary.get("lanes") or []}
    lane_summaries = []
    live_by_lane = {row["lane_id"]: row for row in live_summary.get("lanes") or []}
    for lane in scorecard.get("lanes") or []:
        live_row = live_by_lane.get(lane["lane_id"])
        search_row = search_by_lane.get(lane["lane_id"])
        search_note = ""
        if search_row:
            search_note = f" best_mutated={search_row.get('best_mutated_score')} delta={search_row.get('comparative_delta')}"
        lane_summaries.append(
            {
                "lane_id": lane["lane_id"],
                "status": lane["status"],
                "headline": f"{lane['lane_id']} readiness {lane['normalized_score']} live_score {live_row.get('primary_score') if live_row else 'n/a'}{search_note}",
            }
        )
    scorecard_refs = [str(scorecard_path.relative_to(ROOT))]
    for path in [DEFAULT_LIVE_SUMMARY, DEFAULT_EVIDENCE_MANIFEST]:
        if path.exists():
            scorecard_refs.append(str(path.relative_to(ROOT)))
    if include_search:
        for path in [DEFAULT_SEARCH_SUMMARY, DEFAULT_ARCHIVE_SNAPSHOT, DEFAULT_INVALID_LEDGER]:
            if path.exists():
                scorecard_refs.append(str(path.relative_to(ROOT)))
    budget_burn = {
        "class_a_usd": 0.0,
        "class_b_usd": 0.0,
        "note": "local DARWIN micro-baselines and search-smoke runs use zero-cost local execution",
    }
    if include_search and search_summary.get("budget_summary"):
        budget_burn = search_summary["budget_summary"]
    packet = {
        "schema": "breadboard.darwin.weekly_evidence_packet.v0",
        "packet_id": f"packet.{datetime.now(timezone.utc).strftime('%Yw%W')}",
        "week_start_utc": _week_start_utc(),
        "lane_summaries": lane_summaries,
        "scorecard_refs": scorecard_refs,
        "budget_burn": budget_burn,
        "drift_refs": [],
        "incident_refs": [],
        "next_actions": [
            "promote repo_swe baseline from micro evaluator to richer patch-and-test benchmark",
            "expand typed search from harness/repo_swe to one additional lane",
            "launch scheduling lane baseline after scenario-pack is frozen"
        ],
    }
    if claim_ledger.get("claims"):
        packet["incident_refs"].append(str(DEFAULT_CLAIM_LEDGER.relative_to(ROOT)))
    issues = validate_weekly_evidence_packet(packet)
    if issues:
        joined = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"weekly packet validation failed: {joined}")
    return packet


def _to_markdown(packet: dict) -> str:
    lines = [
        "# DARWIN Weekly Evidence Packet v1",
        "",
        f"- packet_id: `{packet['packet_id']}`",
        f"- week_start_utc: `{packet['week_start_utc']}`",
        "",
        "## Lanes",
        "",
    ]
    for lane in packet.get("lane_summaries") or []:
        lines.append(f"- `{lane['lane_id']}` — `{lane['status']}` — {lane['headline']}")
    lines.extend(["", "## Next Actions", ""])
    for item in packet.get("next_actions") or []:
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def write_weekly_packet(out_dir: Path = DEFAULT_OUT_DIR, *, include_search: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    packet = build_weekly_packet(include_search=include_search)
    out_json = out_dir / "weekly_evidence_packet.latest.json"
    out_md = out_dir / "weekly_evidence_packet.latest.md"
    out_json.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(packet), encoding="utf-8")
    return {"out_json": str(out_json), "out_md": str(out_md), "lane_count": len(packet["lane_summaries"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the first DARWIN weekly evidence packet from the T1 scorecard.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-search", action="store_true")
    args = parser.parse_args()

    summary = write_weekly_packet(Path(args.out_dir), include_search=args.include_search)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"weekly_packet_json={summary['out_json']}")
        print(f"weekly_packet_md={summary['out_md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
