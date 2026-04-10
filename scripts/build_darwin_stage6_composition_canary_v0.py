from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402

DEFAULT_REGISTRY = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "family_registry" / "family_registry_v0.json"
DEFAULT_SCORECARD = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "scorecard" / "scorecard_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche4" / "composition_canary"
OUT_JSON = OUT_DIR / "composition_canary_v0.json"
OUT_MD = OUT_DIR / "composition_canary_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_composition_canary(
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    scorecard_path: Path = DEFAULT_SCORECARD,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    registry = _load_json(registry_path)
    scorecard = _load_json(scorecard_path)
    retained = next((row for row in list(registry.get("rows") or []) if str(row.get("transfer_status") or "") == "retained"), None)
    challenge = next((row for row in list(registry.get("rows") or []) if str(row.get("transfer_status") or "") == "activation_probe"), None)
    retained_scorecard = next((row for row in list(scorecard.get("rows") or []) if str(row.get("transfer_status") or "") == "retained"), {})
    challenge_scorecard = next((row for row in list(scorecard.get("rows") or []) if str(row.get("transfer_status") or "") == "activation_probe"), {})

    candidate_pair = [
        str((retained or {}).get("family_id") or ""),
        str((challenge or {}).get("family_id") or ""),
    ]
    if retained and challenge and str(challenge_scorecard.get("interpretation_note") or "") == "challenge_context_only":
        result = "composition_not_authorized"
        reason = "challenge_family_remains_context_only_and_composition_would_confound_the_retained_single_family_baseline"
    elif retained:
        result = "composition_not_authorized"
        reason = "only_one_retained_family_center_is_currently_eligible_for_stage6_composition"
    else:
        result = "composition_invalid"
        reason = "no_retained_family_center_exists_for_stage6_composition"

    payload = {
        "schema": "breadboard.darwin.stage6.composition_canary.v0",
        "source_refs": {
            "family_registry_ref": path_ref(registry_path),
            "scorecard_ref": path_ref(scorecard_path),
        },
        "result": result,
        "result_reason": reason,
        "candidate_pair_family_ids": candidate_pair,
        "baseline_family_id": str((retained or {}).get("family_id") or ""),
        "baseline_transfer_status": str((retained or {}).get("transfer_status") or ""),
        "challenge_family_id": str((challenge or {}).get("family_id") or ""),
        "challenge_transfer_status": str((challenge or {}).get("transfer_status") or ""),
        "baseline_broader_compounding_confidence": str(retained_scorecard.get("broader_compounding_confidence") or ""),
        "challenge_interpretation_note": str(challenge_scorecard.get("interpretation_note") or ""),
        "replay_status": "not_required" if result == "composition_not_authorized" else "pending",
    }
    lines = [
        "# Stage 6 Composition Canary",
        "",
        f"- result: `{result}`",
        f"- reason: `{reason}`",
        f"- candidate pair: `{candidate_pair}`",
        f"- baseline family: `{payload['baseline_family_id']}`",
        f"- challenge family: `{payload['challenge_family_id']}`",
    ]
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 composition canary outcome.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_composition_canary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_composition_canary={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
