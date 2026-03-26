from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import write_json, write_text  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "precloseout"
OUT_JSON = OUT_DIR / "canonical_artifact_index_v0.json"
OUT_MD = OUT_DIR / "canonical_artifact_index_v0.md"


def _row(path: str, status: str, purpose: str) -> dict[str, str]:
    return {"path": path, "status": status, "purpose": purpose}


def build_stage5_canonical_artifact_index() -> dict[str, str | int]:
    rows = [
        _row("artifacts/darwin/stage5/family_aware_scorecard/family_aware_scorecard_v0.json", "canonical", "family-aware Tranche-2 proving scorecard"),
        _row("artifacts/darwin/stage5/compounding_quality/compounding_quality_v0.json", "canonical", "per-lane proving quality summary"),
        _row("artifacts/darwin/stage5/compounding_rate/compounding_rate_v0.json", "canonical", "round-series compounding-rate report"),
        _row("artifacts/darwin/stage5/family_registry/family_registry_v0.json", "canonical", "Stage-5 family registry"),
        _row("artifacts/darwin/stage5/third_family_decision/third_family_decision_v0.json", "canonical", "third-family activation decision"),
        _row("artifacts/darwin/stage5/bounded_transfer/bounded_transfer_outcomes_v0.json", "canonical", "bounded transfer outcomes"),
        _row("artifacts/darwin/stage5/failed_transfer_taxonomy/failed_transfer_taxonomy_v0.json", "canonical", "failed-transfer taxonomy"),
        _row("artifacts/darwin/stage5/composition_canary/composition_canary_v0.json", "canonical", "family-composition canary outcome"),
        _row("artifacts/darwin/stage5/replay_posture/replay_posture_v0.json", "canonical", "replay and confirmation posture"),
        _row("artifacts/darwin/stage5/scaled_scorecard/scaled_scorecard_v0.json", "canonical", "scaled compounding comparison surface"),
        _row("artifacts/darwin/stage5/scaled_memo/scaled_memo_v0.json", "canonical", "scaled compounding memo artifact"),
        _row("docs/darwin_stage5_comparative_memo_2026-03-25.md", "canonical", "final comparative memo"),
        _row("artifacts/darwin/stage5/verification_bundle/verification_bundle_v0.json", "canonical", "Tranche-3 verification bundle"),
        _row("artifacts/darwin/stage5/precloseout/comparative_bundle_v0.json", "canonical", "late comparative bundle"),
        _row("docs/darwin_stage5_claim_boundary_2026-03-25.md", "canonical", "final Stage-5 claim boundary"),
        _row("docs/darwin_stage5_late_comparative_review_2026-03-25.md", "canonical", "late comparative review"),
        _row("docs/darwin_stage5_precloseout_gate_2026-03-25.md", "canonical", "pre-closeout gate"),
        _row("docs/darwin_stage5_registry_integrity_note_2026-03-25.md", "supporting", "lane-role and family-state integrity note"),
        _row("docs/darwin_stage5_transfer_comparability_note_2026-03-25.md", "supporting", "transfer comparability and failure interpretation"),
        _row("docs/darwin_stage5_composition_boundary_note_2026-03-25.md", "supporting", "composition canary boundary note"),
        _row("docs/darwin_stage5_replay_sufficiency_note_2026-03-25.md", "supporting", "replay sufficiency note"),
        _row("docs/darwin_stage5_route_economics_note_2026-03-25.md", "supporting", "route-economics interpretation"),
        _row("artifacts/darwin/stage5/scaled_compounding/scaled_compounding_v0.json", "historical", "round-series campaign output"),
        _row("artifacts/darwin/stage5/systems_weighted/systems_weighted_compounding_v0.json", "historical", "systems-weighted runner output"),
    ]
    payload = {
        "schema": "breadboard.darwin.stage5.canonical_artifact_index.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage-5 Canonical Artifact Index", ""]
    for row in rows:
        lines.append(f"- `{row['path']}`: status=`{row['status']}`, purpose=`{row['purpose']}`")
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 canonical artifact index.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_canonical_artifact_index()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_canonical_artifact_index={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
