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


DEFAULT_REUSE_TRACKING = ROOT / "artifacts" / "darwin" / "stage5" / "family_reuse_tracking" / "family_reuse_tracking_v0.json"
DEFAULT_THIRD_FAMILY = ROOT / "artifacts" / "darwin" / "stage5" / "third_family_decision" / "third_family_decision_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "family_registry"
OUT_JSON = OUT_DIR / "family_registry_v0.json"
OUT_MD = OUT_DIR / "family_registry_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_family_registry(
    *,
    reuse_tracking_path: Path = DEFAULT_REUSE_TRACKING,
    third_family_decision_path: Path = DEFAULT_THIRD_FAMILY,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    reuse_tracking = _load_json(reuse_tracking_path)
    third_family_decision = _load_json(third_family_decision_path)
    rows = []
    for row in list(reuse_tracking.get("rows") or []):
        stage5_state = str(row.get("stage5_family_state") or "")
        registry_row = {
            "family_id": str(row.get("family_id") or ""),
            "lane_id": str(row.get("lane_id") or ""),
            "family_kind": str(row.get("family_kind") or ""),
            "family_key": str(row.get("family_key") or ""),
            "stage4_lifecycle_status": str(row.get("stage4_lifecycle_status") or ""),
            "stage5_family_state": stage5_state,
            "lane_weight": str(row.get("lane_weight") or "unset"),
            "transfer_eligibility": dict(row.get("transfer_eligibility") or {}),
            "transfer_candidate": stage5_state in {"active_proving", "challenge_only"} and bool(dict(row.get("transfer_eligibility") or {}).get("allowed_target_lanes")),
            "composition_eligibility": {
                "allowed": False,
                "reason": (
                    "single_family_lane_center"
                    if stage5_state == "active_proving"
                    else "challenge_lane_only"
                    if stage5_state == "challenge_only"
                    else "held_back_family"
                ),
            },
            "replay_status": str(row.get("replay_status") or ""),
            "activation_readiness": str(row.get("activation_readiness") or ""),
            "third_family_decision": str(third_family_decision.get("decision") or ""),
            "evidence_refs": list(row.get("evidence_refs") or []),
        }
        rows.append(registry_row)
    payload = {
        "schema": "breadboard.darwin.stage5.family_registry.v0",
        "source_refs": {
            "family_reuse_tracking_ref": path_ref(reuse_tracking_path),
            "third_family_decision_ref": path_ref(third_family_decision_path),
        },
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage-5 Family Registry", ""]
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}` / `{row['family_kind']}`: state=`{row['stage5_family_state']}`, "
            f"transfer_candidate=`{row['transfer_candidate']}`, composition_allowed=`{row['composition_eligibility']['allowed']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 family registry.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_family_registry()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_family_registry={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
