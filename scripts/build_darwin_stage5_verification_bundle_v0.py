from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "verification_bundle"
OUT_JSON = OUT_DIR / "verification_bundle_v0.json"
OUT_MD = OUT_DIR / "verification_bundle_v0.md"


def build_stage5_verification_bundle() -> dict[str, object]:
    refs = {
        "scaled_compounding_ref": "artifacts/darwin/stage5/scaled_compounding/scaled_compounding_v0.json",
        "compounding_rate_ref": "artifacts/darwin/stage5/compounding_rate/compounding_rate_v0.json",
        "family_reuse_tracking_ref": "artifacts/darwin/stage5/family_reuse_tracking/family_reuse_tracking_v0.json",
        "third_family_decision_ref": "artifacts/darwin/stage5/third_family_decision/third_family_decision_v0.json",
        "family_registry_ref": "artifacts/darwin/stage5/family_registry/family_registry_v0.json",
        "bounded_transfer_ref": "artifacts/darwin/stage5/bounded_transfer/bounded_transfer_outcomes_v0.json",
        "failed_transfer_taxonomy_ref": "artifacts/darwin/stage5/failed_transfer_taxonomy/failed_transfer_taxonomy_v0.json",
        "composition_canary_ref": "artifacts/darwin/stage5/composition_canary/composition_canary_v0.json",
        "replay_posture_ref": "artifacts/darwin/stage5/replay_posture/replay_posture_v0.json",
        "scaled_scorecard_ref": "artifacts/darwin/stage5/scaled_scorecard/scaled_scorecard_v0.json",
        "scaled_memo_ref": "artifacts/darwin/stage5/scaled_memo/scaled_memo_v0.json",
    }
    payload = {
        "schema": "breadboard.darwin.stage5.verification_bundle.v0",
        "ref_count": len(refs),
        "refs": refs,
    }
    lines = ["# Stage-5 Verification Bundle", ""]
    for key, value in refs.items():
        lines.append(f"- `{key}`: `{value}`")
    write_json(OUT_DIR / OUT_JSON.name, payload)
    write_text(OUT_DIR / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_DIR / OUT_JSON.name), "ref_count": len(refs)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 verification bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_verification_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_verification_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
