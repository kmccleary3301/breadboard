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
DEFAULT_RATE = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "compounding_rate" / "compounding_rate_v0.json"
DEFAULT_ECON = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "economics_attribution" / "economics_attribution_v0.json"
DEFAULT_LINKAGE = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "transfer_compounding_linkage" / "transfer_compounding_linkage_v0.json"
DEFAULT_REPLAY = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "replay_posture" / "replay_posture_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "scorecard"
OUT_JSON = OUT_DIR / "scorecard_v0.json"
OUT_MD = OUT_DIR / "scorecard_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_scorecard(
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    rate_path: Path = DEFAULT_RATE,
    economics_path: Path = DEFAULT_ECON,
    linkage_path: Path = DEFAULT_LINKAGE,
    replay_path: Path = DEFAULT_REPLAY,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    registry = _load_json(registry_path)
    rate = _load_json(rate_path)
    economics = _load_json(economics_path)
    linkage = _load_json(linkage_path)
    replay = _load_json(replay_path)

    scheduling_summary = next((row for row in list(rate.get("lane_summaries") or []) if str(row.get("lane_id") or "") == "lane.scheduling"), {})
    economics_row = next(iter(list(economics.get("rows") or [])), {})
    linkage_rows = list(linkage.get("rows") or [])
    rows = []
    for row in list(registry.get("rows") or []):
        family_id = str(row.get("family_id") or "")
        transfer_status = str(row.get("transfer_status") or "")
        linkage_row = next((item for item in linkage_rows if str(item.get("family_id") or "") == family_id), {})
        replay_row = next((r for r in list(replay.get("rows") or []) if family_id in str(r.get("subject_id") or "")), {})
        linkage_result = str(linkage_row.get("linkage_result") or "")
        if not linkage_result:
            if transfer_status in {"activation_probe", "inconclusive", "descriptive_only", "degraded_but_valid"} and str(row.get("family_state") or "") != "held_back":
                linkage_result = "challenge_context_only"
            elif transfer_status == "invalid":
                linkage_result = "held_back_boundary"
        rows.append(
            {
                "family_id": family_id,
                "lane_id": str(row.get("lane_id") or ""),
                "family_state": str(row.get("family_state") or ""),
                "transfer_status": transfer_status,
                "broader_compounding_confidence": str(scheduling_summary.get("confidence_class") or ""),
                "broader_compounding_positive_rate": float(scheduling_summary.get("positive_rate") or 0.0),
                "score_lift_vs_family_lockout": float(economics_row.get("score_lift_vs_family_lockout") or 0.0),
                "score_lift_vs_single_lockout": float(economics_row.get("score_lift_vs_single_lockout") or 0.0),
                "linkage_result": linkage_result,
                "replay_status": str(replay_row.get("replay_status") or ""),
                "interpretation_note": (
                    "retained_transfer_backed_positive_compounding"
                    if transfer_status == "retained"
                    else "challenge_context_only"
                    if transfer_status in {"activation_probe", "inconclusive", "descriptive_only", "degraded_but_valid"}
                    else "challenge_context_only"
                    if str(row.get("family_state") or "") != "held_back"
                    else "held_back_boundary"
                ),
            }
        )
    out = {
        "schema": "breadboard.darwin.stage6.scorecard.v0",
        "source_refs": {
            "family_registry_ref": path_ref(registry_path),
            "compounding_rate_ref": path_ref(rate_path),
            "economics_attribution_ref": path_ref(economics_path),
            "transfer_compounding_linkage_ref": path_ref(linkage_path),
            "replay_posture_ref": path_ref(replay_path),
        },
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(out_dir / OUT_JSON.name, out)
    lines = ["# Stage 6 Scorecard", ""]
    for row in rows:
        lines.append(f"- `{row['family_id']}`: transfer=`{row['transfer_status']}`, compounding=`{row['broader_compounding_confidence']}`")
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 scorecard.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_scorecard()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_scorecard={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
