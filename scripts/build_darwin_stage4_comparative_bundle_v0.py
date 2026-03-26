from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "precloseout"
OUT_JSON = OUT_DIR / "comparative_bundle_v0.json"


def build_stage4_comparative_bundle() -> dict[str, str | int]:
    payload = {
        "schema": "breadboard.darwin.stage4.comparative_bundle.v0",
        "deep_live_verification_ref": "artifacts/darwin/stage4/deep_live_search/verification_bundle_v0.json",
        "family_verification_ref": "artifacts/darwin/stage4/family_program/family_verification_bundle_v0.json",
        "canonical_artifact_index_ref": path_ref(ROOT / "artifacts" / "darwin" / "stage4" / "precloseout" / "canonical_artifact_index_v0.json"),
        "claim_boundary_ref": "docs/darwin_stage4_claim_boundary_2026-03-20.md",
        "registry_integrity_ref": "docs/darwin_stage4_registry_integrity_note_2026-03-20.md",
        "transfer_eligibility_ref": "docs/darwin_stage4_transfer_eligibility_note_2026-03-20.md",
        "replay_sufficiency_ref": "docs/darwin_stage4_replay_sufficiency_note_2026-03-20.md",
        "transfer_comparability_ref": "docs/darwin_stage4_transfer_comparability_note_2026-03-20.md",
        "route_economics_ref": "docs/darwin_stage4_route_economics_note_2026-03-20.md",
        "strongest_withheld_ref": "docs/darwin_stage4_strongest_withheld_note_2026-03-20.md",
        "family_scorecard_ref": "artifacts/darwin/stage4/family_program/family_scorecard_v0.json",
        "family_memo_ref": "artifacts/darwin/stage4/family_program/family_memo_v0.json",
    }
    write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "ref_count": len(payload) - 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 comparative pre-closeout bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_comparative_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_comparative_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
