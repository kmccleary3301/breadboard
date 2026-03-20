from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import OUT_DIR, load_json, path_ref, write_json  # noqa: E402


PROMOTION = OUT_DIR / "family_promotion_report_v0.json"
REGISTRY = OUT_DIR / "family_registry_v0.json"
TRANSFER = OUT_DIR / "bounded_transfer_outcomes_v0.json"
FAILED = OUT_DIR / "failed_transfer_taxonomy_v0.json"
SCORECARD = OUT_DIR / "family_scorecard_v0.json"
MEMO = OUT_DIR / "family_memo_v0.json"
SECOND = OUT_DIR / "second_family_decision_v0.json"
LEDGER = OUT_DIR / "stage4_decision_ledger_v1.json"
OUT_JSON = OUT_DIR / "family_verification_bundle_v0.json"


def build_stage4_family_verification_bundle() -> dict[str, str | int]:
    promotion = load_json(PROMOTION)
    transfer = load_json(TRANSFER)
    failed = load_json(FAILED)
    second = load_json(SECOND)
    payload = {
        "schema": "breadboard.darwin.stage4.family_verification_bundle.v0",
        "promotion_report_ref": path_ref(PROMOTION),
        "family_registry_ref": path_ref(REGISTRY),
        "bounded_transfer_outcomes_ref": path_ref(TRANSFER),
        "failed_transfer_taxonomy_ref": path_ref(FAILED),
        "family_scorecard_ref": path_ref(SCORECARD),
        "family_memo_ref": path_ref(MEMO),
        "second_family_decision_ref": path_ref(SECOND),
        "decision_ledger_ref": path_ref(LEDGER),
        "promoted_family_count": sum(1 for row in promotion.get("rows") or [] if row["promotion_outcome"] == "promoted"),
        "retained_transfer_count": sum(1 for row in transfer.get("rows") or [] if row["transfer_status"] == "retained"),
        "failed_transfer_count": failed["row_count"],
        "second_family_decision": second["decision"],
    }
    write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "promoted_family_count": payload["promoted_family_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 family verification bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_family_verification_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_verification_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
