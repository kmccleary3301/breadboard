from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import ROOT as DARWIN_ROOT
from breadboard_ext.darwin.stage3_component_transfer import load_json, write_json


BOUNDED_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference"
COMPONENT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "component_transfer"
BOUNDED_VERIFY = BOUNDED_DIR / "verification_bundle_v0.json"
REGISTRY = COMPONENT_DIR / "component_registry_v0.json"
PROMOTED = COMPONENT_DIR / "promoted_family_artifact_v0.json"
SECOND = COMPONENT_DIR / "second_family_decision_v0.json"
SCORECARD = COMPONENT_DIR / "component_scorecard_v0.json"
MEMO = COMPONENT_DIR / "component_memo_v0.json"
TRANSFER_VERIFY = COMPONENT_DIR / "component_transfer_verification_bundle_v0.json"
OUT_JSON = COMPONENT_DIR / "consolidated_verification_bundle_v0.json"


def write_consolidated_verification_bundle() -> dict[str, str | int]:
    bounded = load_json(BOUNDED_VERIFY)
    registry = load_json(REGISTRY)
    promoted = load_json(PROMOTED)
    second = load_json(SECOND)
    scorecard = load_json(SCORECARD)
    memo = load_json(MEMO)
    transfer = load_json(TRANSFER_VERIFY)
    payload = {
        "schema": "breadboard.darwin.stage3.consolidated_verification_bundle.v0",
        "artifact_refs": {
            "bounded_verify_ref": str(BOUNDED_VERIFY.relative_to(DARWIN_ROOT)),
            "registry_ref": str(REGISTRY.relative_to(DARWIN_ROOT)),
            "promoted_family_ref": str(PROMOTED.relative_to(DARWIN_ROOT)),
            "second_family_decision_ref": str(SECOND.relative_to(DARWIN_ROOT)),
            "component_scorecard_ref": str(SCORECARD.relative_to(DARWIN_ROOT)),
            "component_memo_ref": str(MEMO.relative_to(DARWIN_ROOT)),
            "component_transfer_verify_ref": str(TRANSFER_VERIFY.relative_to(DARWIN_ROOT)),
        },
        "summary": {
            "bounded_comparisons": bounded["comparison_count"],
            "registry_rows": registry["row_count"],
            "promoted_family_id": promoted["component_family_id"],
            "second_family_decision": second["decision"],
            "scorecard_rows": scorecard["row_count"],
            "decision_record_count": transfer["decision_record_count"],
        },
        "closeout_ready": True,
        "memo_claims": memo["what_stage3_demonstrably_does"],
    }
    write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "registry_rows": registry["row_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the consolidated Stage-3 pre-closeout verification bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_consolidated_verification_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"consolidated_verification_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
