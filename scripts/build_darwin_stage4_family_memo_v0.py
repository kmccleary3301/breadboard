from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import OUT_DIR, load_json, path_ref, write_json, write_text  # noqa: E402


PROMOTION = OUT_DIR / "family_promotion_report_v0.json"
REGISTRY = OUT_DIR / "family_registry_v0.json"
TRANSFER = OUT_DIR / "bounded_transfer_outcomes_v0.json"
FAILED = OUT_DIR / "failed_transfer_taxonomy_v0.json"
SCORECARD = OUT_DIR / "family_scorecard_v0.json"
SECOND = OUT_DIR / "second_family_decision_v0.json"
OUT_JSON = OUT_DIR / "family_memo_v0.json"
OUT_MD = OUT_DIR / "family_memo_v0.md"


def build_stage4_family_memo() -> dict[str, str]:
    promotion = load_json(PROMOTION)
    registry = load_json(REGISTRY)
    transfer = load_json(TRANSFER)
    failed = load_json(FAILED)
    scorecard = load_json(SCORECARD)
    second = load_json(SECOND)
    promoted = [row for row in promotion.get("rows") or [] if row["promotion_outcome"] == "promoted"]
    withheld = [row for row in promotion.get("rows") or [] if row["promotion_outcome"] == "withheld"]
    retained = [row for row in transfer.get("rows") or [] if row["transfer_status"] == "retained"]
    payload = {
        "schema": "breadboard.darwin.stage4.family_memo.v0",
        "promoted_family_count": len(promoted),
        "withheld_family_count": len(withheld),
        "retained_transfer_count": len(retained),
        "second_family_decision": second["decision"],
        "promoted_family_ids": [row["family_id"] for row in promoted],
        "withheld_family_ids": [row["family_id"] for row in withheld],
        "refs": {
            "promotion_report_ref": path_ref(PROMOTION),
            "family_registry_ref": path_ref(REGISTRY),
            "transfer_outcomes_ref": path_ref(TRANSFER),
            "failed_transfer_taxonomy_ref": path_ref(FAILED),
            "family_scorecard_ref": path_ref(SCORECARD),
        },
        "interpretation": {
            "what_stage4_now_does": [
                "promotes more than one reusable family from live provider-backed search",
                "records bounded retained and failed transfer outcomes",
                "keeps replay posture and transfer eligibility explicit at family level",
            ],
            "what_remains_bounded": [
                "the current signals remain retained-score efficiency signals",
                "route economics remain mostly OpenAI-fallback in this workspace",
                "broad compounding claims remain out of scope in this tranche",
            ],
        },
    }
    lines = [
        "# Stage-4 Family Memo",
        "",
        f"- promoted family count: `{len(promoted)}`",
        f"- withheld family count: `{len(withheld)}`",
        f"- retained transfer count: `{len(retained)}`",
        f"- second-family decision: `{second['decision']}`",
        f"- failed transfer rows: `{failed['row_count']}`",
        f"- scorecard rows: `{scorecard['row_count']}`",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 family memo.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_family_memo()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_memo={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
