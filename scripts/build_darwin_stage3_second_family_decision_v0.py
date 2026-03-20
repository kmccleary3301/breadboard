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
OUT_JSON = COMPONENT_DIR / "second_family_decision_v0.json"
OUT_MD = COMPONENT_DIR / "second_family_decision_v0.md"


def write_second_family_decision() -> dict[str, str]:
    registry = load_json(REGISTRY)
    rows = registry.get("rows") or []
    candidates = [row for row in rows if row["lifecycle_status"] != "promoted"]
    best = sorted(
        candidates,
        key=lambda row: (
            0 if row["transfer_eligibility"] == "eligible_for_bounded_transfer" else 1,
            0 if row["source_lane_id"] == "lane.repo_swe" else 1,
            -int(row["improvement_count"]),
            -int(row["valid_comparison_count"]),
        ),
    )[0]
    payload = {
        "schema": "breadboard.darwin.stage3.second_family_decision.v0",
        "decision": "no_second_family_yet",
        "selected_candidate_family_id": best["component_family_id"],
        "reason": "single_family_promotion_boundary_kept_for_pre_closeout_stage3",
        "candidate_summary": best,
        "registry_ref": str(REGISTRY.relative_to(DARWIN_ROOT)),
    }
    lines = [
        "# Stage-3 Second-Family Decision",
        "",
        "- decision: `no_second_family_yet`",
        f"- leading non-promoted family: `{best['component_family_id']}`",
        "- reason: keep Stage-3 pre-closeout scope on one canonical promoted family rather than widening promotion claims",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the explicit Stage-3 second-family decision.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_second_family_decision()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"second_family_decision={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
