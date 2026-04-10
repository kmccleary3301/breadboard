from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "precloseout"
OUT_JSON = OUT_DIR / "comparative_bundle_v0.json"


def build_stage6_comparative_bundle() -> dict[str, str | int]:
    payload = {
        "schema": "breadboard.darwin.stage6.comparative_bundle.v0",
        "canonical_artifact_index_ref": path_ref(ROOT / "artifacts" / "darwin" / "stage6" / "precloseout" / "canonical_artifact_index_v0.json"),
        "claim_boundary_ref": "docs/internals/DARWIN_STAGE6_CLAIM_BOUNDARY_2026-04-09.md",
        "family_registry_ref": "artifacts/darwin/stage6/tranche4/family_registry/family_registry_v0.json",
        "scorecard_ref": "artifacts/darwin/stage6/tranche4/scorecard/scorecard_v0.json",
        "broader_compounding_ref": "artifacts/darwin/stage6/tranche3/broader_compounding/broader_compounding_v0.json",
        "compounding_rate_ref": "artifacts/darwin/stage6/tranche3/compounding_rate/compounding_rate_v0.json",
        "economics_attribution_ref": "artifacts/darwin/stage6/tranche3/economics_attribution/economics_attribution_v0.json",
        "transfer_compounding_linkage_ref": "artifacts/darwin/stage6/tranche3/transfer_compounding_linkage/transfer_compounding_linkage_v0.json",
        "replay_posture_ref": "artifacts/darwin/stage6/tranche3/replay_posture/replay_posture_v0.json",
        "composition_canary_ref": "artifacts/darwin/stage6/tranche4/composition_canary/composition_canary_v0.json",
        "late_comparative_memo_ref": "docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_MEMO_2026-04-09.md",
        "verification_bundle_ref": "artifacts/darwin/stage6/tranche3/verification_bundle/verification_bundle_v0.json",
    }
    write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "ref_count": len(payload) - 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 comparative pre-closeout bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_comparative_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_comparative_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
