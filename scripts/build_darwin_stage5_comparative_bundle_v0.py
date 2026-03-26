from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "precloseout"
OUT_JSON = OUT_DIR / "comparative_bundle_v0.json"


def build_stage5_comparative_bundle() -> dict[str, str | int]:
    payload = {
        "schema": "breadboard.darwin.stage5.comparative_bundle.v0",
        "canonical_artifact_index_ref": path_ref(ROOT / "artifacts" / "darwin" / "stage5" / "precloseout" / "canonical_artifact_index_v0.json"),
        "claim_boundary_ref": "docs/darwin_stage5_claim_boundary_2026-03-25.md",
        "registry_integrity_ref": "docs/darwin_stage5_registry_integrity_note_2026-03-25.md",
        "transfer_comparability_ref": "docs/darwin_stage5_transfer_comparability_note_2026-03-25.md",
        "composition_boundary_ref": "docs/darwin_stage5_composition_boundary_note_2026-03-25.md",
        "replay_sufficiency_ref": "docs/darwin_stage5_replay_sufficiency_note_2026-03-25.md",
        "route_economics_ref": "docs/darwin_stage5_route_economics_note_2026-03-25.md",
        "family_aware_scorecard_ref": "artifacts/darwin/stage5/family_aware_scorecard/family_aware_scorecard_v0.json",
        "compounding_quality_ref": "artifacts/darwin/stage5/compounding_quality/compounding_quality_v0.json",
        "compounding_rate_ref": "artifacts/darwin/stage5/compounding_rate/compounding_rate_v0.json",
        "family_registry_ref": "artifacts/darwin/stage5/family_registry/family_registry_v0.json",
        "bounded_transfer_ref": "artifacts/darwin/stage5/bounded_transfer/bounded_transfer_outcomes_v0.json",
        "failed_transfer_taxonomy_ref": "artifacts/darwin/stage5/failed_transfer_taxonomy/failed_transfer_taxonomy_v0.json",
        "composition_canary_ref": "artifacts/darwin/stage5/composition_canary/composition_canary_v0.json",
        "replay_posture_ref": "artifacts/darwin/stage5/replay_posture/replay_posture_v0.json",
        "scaled_scorecard_ref": "artifacts/darwin/stage5/scaled_scorecard/scaled_scorecard_v0.json",
        "scaled_memo_ref": "artifacts/darwin/stage5/scaled_memo/scaled_memo_v0.json",
        "comparative_memo_ref": "docs/darwin_stage5_comparative_memo_2026-03-25.md",
        "verification_bundle_ref": "artifacts/darwin/stage5/verification_bundle/verification_bundle_v0.json",
    }
    write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "ref_count": len(payload) - 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 comparative pre-closeout bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_comparative_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_comparative_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
