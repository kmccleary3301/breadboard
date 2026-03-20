from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import OUT_DIR, load_json, write_json, write_text  # noqa: E402


PROMOTION = OUT_DIR / "family_promotion_report_v0.json"
OUT_JSON = OUT_DIR / "second_family_decision_v0.json"
OUT_MD = OUT_DIR / "second_family_decision_v0.md"


def build_stage4_second_family_decision() -> dict[str, str]:
    promotion = load_json(PROMOTION)
    promoted = [row for row in promotion.get("rows") or [] if row["promotion_outcome"] == "promoted"]
    decision = "two_promoted_families" if len(promoted) >= 2 else "no_second_family_yet"
    payload = {
        "schema": "breadboard.darwin.stage4.second_family_decision.v0",
        "decision": decision,
        "promoted_family_count": len(promoted),
        "promoted_family_ids": [row["family_id"] for row in promoted],
    }
    lines = [
        "# Stage-4 Second Family Decision",
        "",
        f"- decision: `{decision}`",
        f"- promoted family count: `{len(promoted)}`",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "decision": decision}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 second-family decision.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_second_family_decision()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_second_family_decision={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
