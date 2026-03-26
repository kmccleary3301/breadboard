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
SCORECARD = COMPONENT_DIR / "component_scorecard_v0.json"
PROMOTED = COMPONENT_DIR / "promoted_family_artifact_v0.json"
SECOND = COMPONENT_DIR / "second_family_decision_v0.json"
FAILED = COMPONENT_DIR / "failed_transfer_taxonomy_v0.json"
OUT_JSON = COMPONENT_DIR / "component_memo_v0.json"
OUT_MD = COMPONENT_DIR / "component_memo_v0.md"


def write_component_memo() -> dict[str, str]:
    registry = load_json(REGISTRY)
    scorecard = load_json(SCORECARD)
    promoted = load_json(PROMOTED)
    second = load_json(SECOND)
    failed = load_json(FAILED)
    payload = {
        "schema": "breadboard.darwin.stage3.component_memo.v0",
        "what_stage3_demonstrably_does": [
            "promotes one replay-backed reusable topology family from lane.repo_swe",
            "records one retained bounded transfer to lane.systems",
            "records one informative failed transfer on lane.scheduling",
        ],
        "what_remains_bounded": [
            "single canonical promoted family",
            "no broad transfer-learning claim",
            "route-aware inference remains scaffold-only in this workspace unless OpenAI/OpenRouter credentials are present",
        ],
        "what_is_not_claimable": [
            "broad cross-lane transfer superiority",
            "publication-grade transfer generalization",
            "broad second-family promotion confidence",
        ],
        "artifact_refs": {
            "registry_ref": str(REGISTRY.relative_to(DARWIN_ROOT)),
            "scorecard_ref": str(SCORECARD.relative_to(DARWIN_ROOT)),
            "promoted_family_ref": str(PROMOTED.relative_to(DARWIN_ROOT)),
            "second_family_decision_ref": str(SECOND.relative_to(DARWIN_ROOT)),
            "failed_transfer_taxonomy_ref": str(FAILED.relative_to(DARWIN_ROOT)),
        },
        "summary_counts": {
            "registry_rows": registry["row_count"],
            "scorecard_rows": scorecard["row_count"],
            "failed_transfer_rows": failed["row_count"],
        },
    }
    lines = [
        "# Stage-3 Component Memo",
        "",
        "## Demonstrated",
        "- one replay-backed promoted topology family from `lane.repo_swe`",
        "- one retained bounded transfer to `lane.systems`",
        "- one informative failed transfer on `lane.scheduling`",
        "",
        "## Bounded",
        "- no second family promoted yet",
        "- no broad transfer-learning claim",
        "- route-aware inference remains scaffold-only in this workspace unless OpenAI/OpenRouter credentials are present",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 component memo/dossier.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_component_memo()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_memo={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
