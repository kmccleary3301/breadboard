from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import ROOT as DARWIN_ROOT
from breadboard_ext.darwin.stage3_component_transfer import load_json, write_json, write_text


COMPONENT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "component_transfer"
REGISTRY = COMPONENT_DIR / "component_registry_v0.json"
PROMOTION = COMPONENT_DIR / "component_promotion_report_v0.json"
TRANSFER = COMPONENT_DIR / "bounded_transfer_outcomes_v0.json"
REPLAY = COMPONENT_DIR / "component_replay_v0.json"
LEDGER = COMPONENT_DIR / "stage3_decision_ledger_v1.json"
OUT_JSON = COMPONENT_DIR / "promoted_family_artifact_v0.json"
OUT_MD = COMPONENT_DIR / "promoted_family_artifact_v0.md"


def write_promoted_family_artifact() -> dict[str, str]:
    registry = load_json(REGISTRY)
    promotion = load_json(PROMOTION)
    transfer = load_json(TRANSFER)
    replay = load_json(REPLAY)
    promoted = next(row for row in registry.get("rows") or [] if row["lifecycle_status"] == "promoted")
    promotion_row = next(row for row in promotion.get("rows") or [] if row["component_family_id"] == promoted["component_family_id"])
    transfer_rows = [row for row in transfer.get("rows") or [] if row["component_family_id"] == promoted["component_family_id"]]
    replay_row = next(row for row in replay.get("rows") or [] if row["lane_id"] == promoted["source_lane_id"])
    payload = {
        "schema": "breadboard.darwin.stage3.promoted_family_artifact.v0",
        "component_family_id": promoted["component_family_id"],
        "source_lane_id": promoted["source_lane_id"],
        "component_kind": promoted["component_kind"],
        "component_key": promoted["component_key"],
        "promotion_status": promoted["lifecycle_status"],
        "transfer_eligibility": promoted["transfer_eligibility"],
        "replay_status": promoted["replay_status"],
        "promotion_summary": promotion_row,
        "transfer_rows": transfer_rows,
        "replay_summary": replay_row,
        "artifact_refs": {
            "registry_ref": str(REGISTRY.relative_to(DARWIN_ROOT)),
            "promotion_report_ref": str(PROMOTION.relative_to(DARWIN_ROOT)),
            "transfer_outcomes_ref": str(TRANSFER.relative_to(DARWIN_ROOT)),
            "replay_ref": str(REPLAY.relative_to(DARWIN_ROOT)),
            "decision_ledger_ref": str(LEDGER.relative_to(DARWIN_ROOT)),
        },
    }
    lines = [
        "# Stage-3 Promoted Family Artifact",
        "",
        f"- promoted family: `{promoted['component_family_id']}`",
        f"- source lane: `{promoted['source_lane_id']}`",
        f"- replay status: `{promoted['replay_status']}`",
        f"- transfer eligibility: `{promoted['transfer_eligibility']}`",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the canonical artifact for the first promoted Stage-3 family.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_promoted_family_artifact()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"promoted_family_artifact={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
