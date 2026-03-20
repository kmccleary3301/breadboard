from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "precloseout"
OUT_JSON = OUT_DIR / "canonical_artifact_index_v0.json"
OUT_MD = OUT_DIR / "canonical_artifact_index_v0.md"


def _row(path: str, status: str, purpose: str) -> dict[str, str]:
    return {"path": path, "status": status, "purpose": purpose}


def build_stage4_canonical_artifact_index() -> dict[str, str | int]:
    rows = [
        _row("artifacts/darwin/stage4/deep_live_search/verification_bundle_v0.json", "canonical", "deep-live-search verification entrypoint"),
        _row("artifacts/darwin/stage4/family_program/family_promotion_report_v0.json", "canonical", "family promotion decisions"),
        _row("artifacts/darwin/stage4/family_program/second_family_decision_v0.json", "canonical", "explicit second-family decision"),
        _row("artifacts/darwin/stage4/family_program/family_registry_v0.json", "canonical", "canonical Stage-4 family registry"),
        _row("artifacts/darwin/stage4/family_program/bounded_transfer_outcomes_v0.json", "canonical", "bounded transfer results"),
        _row("artifacts/darwin/stage4/family_program/failed_transfer_taxonomy_v0.json", "canonical", "failed-transfer taxonomy"),
        _row("artifacts/darwin/stage4/family_program/family_scorecard_v0.json", "canonical", "comparative family scorecard"),
        _row("artifacts/darwin/stage4/family_program/family_memo_v0.json", "canonical", "comparative family memo"),
        _row("artifacts/darwin/stage4/family_program/family_verification_bundle_v0.json", "canonical", "family-program verification bundle"),
        _row("artifacts/darwin/stage4/family_program/stage4_decision_ledger_v1.json", "canonical", "Stage-4 family decision truth"),
        _row("artifacts/darwin/stage4/deep_live_search/replay_checks_v0.json", "supporting", "bounded replay checks"),
        _row("artifacts/darwin/stage4/deep_live_search/route_mix_v0.json", "supporting", "route-provider mix interpretation"),
        _row("artifacts/darwin/stage4/deep_live_search/invalidity_summary_v0.json", "supporting", "invalidity summary"),
        _row("artifacts/darwin/stage4/deep_live_search/strongest_families_v0.json", "historical", "deep-search strongest-family output"),
    ]
    payload = {
        "schema": "breadboard.darwin.stage4.canonical_artifact_index.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage-4 Canonical Artifact Index", ""]
    for row in rows:
        lines.append(f"- `{row['path']}`: status=`{row['status']}`, purpose=`{row['purpose']}`")
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 canonical artifact index.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_canonical_artifact_index()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_canonical_artifact_index={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
