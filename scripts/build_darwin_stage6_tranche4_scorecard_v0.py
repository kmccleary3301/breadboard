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

DEFAULT_REGISTRY = ROOT / "artifacts" / "darwin" / "stage6" / "tranche4" / "family_registry" / "family_registry_v0.json"
DEFAULT_SCORECARD = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "scorecard" / "scorecard_v0.json"
DEFAULT_COMPOSITION = ROOT / "artifacts" / "darwin" / "stage6" / "tranche4" / "composition_canary" / "composition_canary_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche4" / "scorecard"
OUT_JSON = OUT_DIR / "scorecard_v0.json"
OUT_MD = OUT_DIR / "scorecard_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_tranche4_scorecard(
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    tranche3_scorecard_path: Path = DEFAULT_SCORECARD,
    composition_path: Path = DEFAULT_COMPOSITION,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    registry = _load_json(registry_path)
    tranche3_scorecard = _load_json(tranche3_scorecard_path)
    composition = _load_json(composition_path)
    tranche3_rows = {str(row.get("family_id") or ""): row for row in list(tranche3_scorecard.get("rows") or [])}
    composition_result = str(composition.get("result") or "composition_not_authorized")
    rows = []
    for row in list(registry.get("rows") or []):
        family_id = str(row.get("family_id") or "")
        prior = tranche3_rows.get(family_id, {})
        transfer_status = str(row.get("transfer_status") or "")
        rows.append(
            {
                "family_id": family_id,
                "lane_id": str(row.get("lane_id") or ""),
                "family_state": str(row.get("family_state") or ""),
                "transfer_status": transfer_status,
                "broader_compounding_confidence": str(prior.get("broader_compounding_confidence") or ""),
                "broader_compounding_positive_rate": float(prior.get("broader_compounding_positive_rate") or 0.0),
                "score_lift_vs_family_lockout": float(prior.get("score_lift_vs_family_lockout") or 0.0),
                "score_lift_vs_single_lockout": float(prior.get("score_lift_vs_single_lockout") or 0.0),
                "linkage_result": str(prior.get("linkage_result") or ""),
                "replay_status": str(prior.get("replay_status") or row.get("replay_status") or ""),
                "interpretation_note": str(prior.get("interpretation_note") or ""),
                "composition_decision": composition_result,
                "composition_baseline_role": "retained_single_family_control" if transfer_status == "retained" else "non_baseline_context",
                "challenge_relevance": str(row.get("challenge_relevance") or ""),
            }
        )
    payload = {
        "schema": "breadboard.darwin.stage6.tranche4.scorecard.v0",
        "source_refs": {
            "tranche4_family_registry_ref": path_ref(registry_path),
            "tranche3_scorecard_ref": path_ref(tranche3_scorecard_path),
            "composition_canary_ref": path_ref(composition_path),
        },
        "composition_decision": composition_result,
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage 6 Tranche 4 Scorecard", ""]
    for row in rows:
        lines.append(
            f"- `{row['family_id']}`: transfer=`{row['transfer_status']}`, compounding=`{row['broader_compounding_confidence']}`, composition=`{row['composition_decision']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows), "composition_decision": composition_result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 tranche-4 scorecard.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_tranche4_scorecard()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_tranche4_scorecard={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
