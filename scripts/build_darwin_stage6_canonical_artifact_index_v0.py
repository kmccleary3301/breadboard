from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import write_json, write_text  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "precloseout"
OUT_JSON = OUT_DIR / "canonical_artifact_index_v0.json"
OUT_MD = OUT_DIR / "canonical_artifact_index_v0.md"


def _row(path: str, status: str, purpose: str) -> dict[str, str]:
    return {"path": path, "status": status, "purpose": purpose}


def build_stage6_canonical_artifact_index() -> dict[str, str | int]:
    rows = [
        _row("docs/internals/DARWIN_STAGE6_TRANCHE1_STATUS_2026-04-09.md", "canonical", "Tranche 1 status"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE1_REVIEW_2026-04-09.md", "canonical", "Tranche 1 review"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE1_GATE_2026-04-09.md", "canonical", "Tranche 1 gate"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE2_STATUS_2026-04-09.md", "canonical", "Tranche 2 status"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE2_REVIEW_2026-04-09.md", "canonical", "Tranche 2 review"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE2_GATE_2026-04-09.md", "canonical", "Tranche 2 gate"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE3_BASELINE_2026-04-09.md", "canonical", "Tranche 3 carried-forward baseline"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE3_STATUS_2026-04-09.md", "canonical", "Tranche 3 status"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE3_REVIEW_2026-04-09.md", "canonical", "Tranche 3 review"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE3_GATE_2026-04-09.md", "canonical", "Tranche 3 gate"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE4_SLICE_2026-04-09.md", "canonical", "Tranche 4 execution slice"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE4_STATUS_2026-04-09.md", "canonical", "Tranche 4 status"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE4_REVIEW_2026-04-09.md", "canonical", "Tranche 4 review"),
        _row("docs/internals/DARWIN_STAGE6_TRANCHE4_GATE_2026-04-09.md", "canonical", "Tranche 4 gate"),
        _row("docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_EXECUTION_PLAN_2026-04-09.md", "canonical", "late comparative execution plan"),
        _row("docs/internals/DARWIN_STAGE6_CLAIM_BOUNDARY_2026-04-09.md", "canonical", "Stage 6 claim boundary"),
        _row("docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_MEMO_2026-04-09.md", "canonical", "late comparative memo"),
        _row("docs/internals/DARWIN_STAGE6_PRECLOSEOUT_REVIEW_2026-04-09.md", "canonical", "pre-closeout review"),
        _row("docs/internals/DARWIN_STAGE6_PRECLOSEOUT_GATE_2026-04-09.md", "canonical", "pre-closeout gate"),
        _row("docs/internals/DARWIN_STAGE6_REMAINING_TO_CLOSE_2026-04-09.md", "supporting", "remaining-to-close note"),
        _row("artifacts/darwin/stage6/tranche3/broader_compounding/broader_compounding_v0.json", "canonical", "broader-compounding campaign output"),
        _row("artifacts/darwin/stage6/tranche3/compounding_rate/compounding_rate_v0.json", "canonical", "broader-compounding rate report"),
        _row("artifacts/darwin/stage6/tranche3/economics_attribution/economics_attribution_v0.json", "canonical", "economics attribution surface"),
        _row("artifacts/darwin/stage6/tranche3/transfer_compounding_linkage/transfer_compounding_linkage_v0.json", "canonical", "transfer-to-compounding linkage"),
        _row("artifacts/darwin/stage6/tranche3/replay_posture/replay_posture_v0.json", "canonical", "replay posture"),
        _row("artifacts/darwin/stage6/tranche3/verification_bundle/verification_bundle_v0.json", "supporting", "Tranche 3 verification bundle"),
        _row("artifacts/darwin/stage6/tranche4/composition_canary/composition_canary_v0.json", "canonical", "composition decision artifact"),
        _row("artifacts/darwin/stage6/tranche4/family_registry/family_registry_v0.json", "canonical", "tranche-4 family registry"),
        _row("artifacts/darwin/stage6/tranche4/scorecard/scorecard_v0.json", "canonical", "tranche-4 scorecard"),
        _row("artifacts/darwin/stage6/precloseout/comparative_bundle_v0.json", "canonical", "pre-closeout comparative bundle"),
    ]
    payload = {
        "schema": "breadboard.darwin.stage6.canonical_artifact_index.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage 6 Canonical Artifact Index", ""]
    for row in rows:
        lines.append(f"- `{row['path']}`: status=`{row['status']}`, purpose=`{row['purpose']}`")
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 canonical artifact index.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_canonical_artifact_index()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_canonical_artifact_index={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
