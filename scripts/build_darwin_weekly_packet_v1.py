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
DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "weekly"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _week_start_utc() -> str:
    now = datetime.now(timezone.utc)
    monday = now.date().toordinal() - now.weekday()
    week_start = datetime.fromordinal(monday).replace(tzinfo=timezone.utc)
    return week_start.strftime("%Y-%m-%dT00:00:00Z")


def build_weekly_packet(scorecard_path: Path = DEFAULT_SCORECARD) -> dict:
    scorecard = _load_json(scorecard_path)
    lane_summaries = []
    for lane in scorecard.get("lanes") or []:
        lane_summaries.append(
            {
                "lane_id": lane["lane_id"],
                "status": lane["status"],
                "headline": f"{lane['lane_id']} structural readiness score {lane['normalized_score']}",
            }
        )
    packet = {
        "schema": "breadboard.darwin.weekly_evidence_packet.v0",
        "packet_id": f"packet.{datetime.now(timezone.utc).strftime('%Yw%W')}",
        "week_start_utc": _week_start_utc(),
        "lane_summaries": lane_summaries,
        "scorecard_refs": [str(scorecard_path.relative_to(ROOT))],
        "budget_burn": {
            "class_a_usd": 0.0,
            "class_b_usd": 0.0,
            "note": "bootstrap structural packet only; no live campaign spend attributed yet"
        },
        "drift_refs": [],
        "incident_refs": [],
        "next_actions": [
            "launch first shared topology-family baseline runs",
            "materialize DARWIN evidence bundle writer",
            "start harness and systems lane baseline execution"
        ],
    }
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


def write_weekly_packet(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    packet = build_weekly_packet()
    out_json = out_dir / "weekly_evidence_packet.latest.json"
    out_md = out_dir / "weekly_evidence_packet.latest.md"
    out_json.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(packet), encoding="utf-8")
    return {"out_json": str(out_json), "out_md": str(out_md), "lane_count": len(packet["lane_summaries"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the first DARWIN weekly evidence packet from the T1 scorecard.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = write_weekly_packet(Path(args.out_dir))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"weekly_packet_json={summary['out_json']}")
        print(f"weekly_packet_md={summary['out_md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
