from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import write_json  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "verification_bundle"
OUT_JSON = OUT_DIR / "verification_bundle_v0.json"


def build_stage6_verification_bundle(*, out_dir: Path = OUT_DIR) -> dict[str, object]:
    payload = {
        "schema": "breadboard.darwin.stage6.verification_bundle.v0",
        "broader_compounding_ref": "artifacts/darwin/stage6/tranche3/broader_compounding/broader_compounding_v0.json",
        "compounding_rate_ref": "artifacts/darwin/stage6/tranche3/compounding_rate/compounding_rate_v0.json",
        "family_registry_ref": "artifacts/darwin/stage6/tranche3/family_registry/family_registry_v0.json",
        "economics_attribution_ref": "artifacts/darwin/stage6/tranche3/economics_attribution/economics_attribution_v0.json",
        "transfer_compounding_linkage_ref": "artifacts/darwin/stage6/tranche3/transfer_compounding_linkage/transfer_compounding_linkage_v0.json",
        "replay_posture_ref": "artifacts/darwin/stage6/tranche3/replay_posture/replay_posture_v0.json",
        "scorecard_ref": "artifacts/darwin/stage6/tranche3/scorecard/scorecard_v0.json",
    }
    write_json(out_dir / OUT_JSON.name, payload)
    return {"out_json": str(out_dir / OUT_JSON.name)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 verification bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_verification_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_verification_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
