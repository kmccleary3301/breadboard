from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import OUT_DIR, load_json, write_json


CANDIDATES = OUT_DIR / "component_candidates_v0.json"
PROMOTION = OUT_DIR / "component_promotion_report_v0.json"
TRANSFER = OUT_DIR / "bounded_transfer_outcomes_v0.json"
FAILED = OUT_DIR / "failed_transfer_taxonomy_v0.json"
REPLAY = OUT_DIR / "component_replay_v0.json"
LEDGER = OUT_DIR / "stage3_decision_ledger_v1.json"
OUT_JSON = OUT_DIR / "component_transfer_verification_bundle_v0.json"


def write_component_transfer_verification_bundle() -> dict[str, str | int]:
    candidates = load_json(CANDIDATES)
    promotion = load_json(PROMOTION)
    transfer = load_json(TRANSFER)
    failed = load_json(FAILED)
    replay = load_json(REPLAY)
    ledger = load_json(LEDGER)
    payload = {
        "schema": "breadboard.darwin.stage3.component_transfer_verification_bundle.v0",
        "candidate_row_count": candidates["row_count"],
        "promotion_row_count": promotion["row_count"],
        "transfer_row_count": transfer["row_count"],
        "failed_transfer_row_count": failed["row_count"],
        "replay_row_count": replay["row_count"],
        "decision_record_count": len(ledger.get("decision_records") or []),
        "artifact_refs": {
            "component_candidates_ref": str(CANDIDATES.relative_to(ROOT)),
            "promotion_report_ref": str(PROMOTION.relative_to(ROOT)),
            "transfer_outcomes_ref": str(TRANSFER.relative_to(ROOT)),
            "failed_transfer_taxonomy_ref": str(FAILED.relative_to(ROOT)),
            "component_replay_ref": str(REPLAY.relative_to(ROOT)),
            "decision_ledger_ref": str(LEDGER.relative_to(ROOT)),
        },
    }
    write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "decision_record_count": payload["decision_record_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 component-promotion / transfer verification bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_component_transfer_verification_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_transfer_verification_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
