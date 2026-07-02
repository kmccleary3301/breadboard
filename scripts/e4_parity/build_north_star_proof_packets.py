#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Any

GENERATED_AT = "2026-07-07T03:00:00Z"

try:
    from scripts.e4_parity import run_lane
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import run_lane  # type: ignore
    from lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs  # type: ignore


def build(*, lane_id: str | None = None) -> dict[str, Any]:
    """Compatibility CLI wrapper; packet bytes are produced through run_lane."""
    lane_defs = load_lane_defs(DEFAULT_LANE_DEF_DIR)
    lane_ids = list(run_lane.NORTH_STAR_LANE_IDS) if lane_id is None else [lane_id]
    missing = [selected_lane for selected_lane in lane_ids if selected_lane not in lane_defs]
    if missing:
        raise KeyError(f"unknown lane_id(s): {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for selected_lane in lane_ids:
        result = run_lane.run_lane(selected_lane, stage="capture", out_dir=None, promote_accepted=True)
        if result.get("stages"):
            packet_report = result["stages"][0].get("packet_report")
            if isinstance(packet_report, dict):
                rows.append(packet_report)
                continue
        rows.append({"lane_id": selected_lane, "config_id": lane_defs[selected_lane]["config_id"], "ok": False, "errors": ["run_lane did not return a packet_report"]})
    return {
        "schema_version": "bb.ns.north_star_proof_packets.v1",
        "generated_at_utc": GENERATED_AT,
        "ok": all(bool(row.get("ok")) for row in rows),
        "rows": rows,
        "artifact_writer": "run_lane",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compatibility wrapper for accepted north-star lane promotion.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--lane-id", choices=list(run_lane.NORTH_STAR_LANE_IDS))
    args = parser.parse_args(argv)
    report = build(lane_id=args.lane_id)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
