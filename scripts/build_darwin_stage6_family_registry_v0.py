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

DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage6" / "tranche2" / "broader_transfer_matrix" / "transfer_outcome_summary_v1.json"
DEFAULT_COMPOUNDING = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "broader_compounding" / "broader_compounding_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "family_registry"
OUT_JSON = OUT_DIR / "family_registry_v0.json"
OUT_MD = OUT_DIR / "family_registry_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_family_registry(
    *,
    transfer_path: Path = DEFAULT_TRANSFER,
    compounding_path: Path = DEFAULT_COMPOUNDING,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    transfer = _load_json(transfer_path)
    compounding = _load_json(compounding_path)
    rows = [
        {
            "family_id": "component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0",
            "lane_id": "lane.systems",
            "family_state": "retained_transfer_source",
            "lane_weight": "primary_proving_lane",
            "transfer_status": "retained",
            "activation_state": "active",
            "replay_status": "supported",
            "composition_eligibility": "gated",
        },
        {
            "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
            "lane_id": "lane.repo_swe",
            "family_state": "challenge_only",
            "lane_weight": "challenge_lane",
            "transfer_status": "activation_probe",
            "activation_state": "challenge",
            "replay_status": "observed",
            "composition_eligibility": "gated",
        },
        {
            "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
            "lane_id": "lane.repo_swe",
            "family_state": "held_back",
            "lane_weight": "challenge_lane",
            "transfer_status": "invalid",
            "activation_state": "inactive",
            "replay_status": "not_applicable",
            "composition_eligibility": "gated",
        },
    ]
    out = {
        "schema": "breadboard.darwin.stage6.family_registry.v0",
        "source_refs": {
            "transfer_summary_ref": path_ref(transfer_path),
            "broader_compounding_ref": path_ref(compounding_path),
        },
        "family_center_decision": str(compounding.get("family_center_decision") or "hold_single_retained_family_center"),
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(out_dir / OUT_JSON.name, out)
    lines = ["# Stage 6 Family Registry", ""]
    for row in rows:
        lines.append(f"- `{row['lane_id']}`: state=`{row['family_state']}`, transfer=`{row['transfer_status']}`, replay=`{row['replay_status']}`")
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 family registry.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_family_registry()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_family_registry={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
