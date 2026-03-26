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


DEFAULT_STAGE5_REGISTRY = ROOT / "artifacts" / "darwin" / "stage5" / "family_registry" / "family_registry_v0.json"
DEFAULT_COMPOUNDING_QUALITY = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_quality" / "compounding_quality_v0.json"
DEFAULT_STAGE4_TRANSFER = ROOT / "artifacts" / "darwin" / "stage4" / "family_program" / "bounded_transfer_outcomes_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "bounded_transfer"
OUT_JSON = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_MD = OUT_DIR / "bounded_transfer_outcomes_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_bounded_transfer_outcomes(
    *,
    stage5_registry_path: Path = DEFAULT_STAGE5_REGISTRY,
    compounding_quality_path: Path = DEFAULT_COMPOUNDING_QUALITY,
    stage4_transfer_path: Path = DEFAULT_STAGE4_TRANSFER,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    stage5_registry = _load_json(stage5_registry_path)
    compounding_quality = _load_json(compounding_quality_path)
    stage4_transfer = _load_json(stage4_transfer_path)

    registry_map = {str(row.get("family_id") or ""): dict(row) for row in list(stage5_registry.get("rows") or [])}
    quality_map = {str(row.get("lane_id") or ""): dict(row) for row in list(compounding_quality.get("rows") or [])}
    stage4_transfer_map = {str(row.get("transfer_id") or ""): dict(row) for row in list(stage4_transfer.get("rows") or [])}

    repo_topology = next(row for row in list(stage5_registry.get("rows") or []) if str(row.get("lane_id")) == "lane.repo_swe" and str(row.get("family_kind")) == "topology")
    systems_policy = next(row for row in list(stage5_registry.get("rows") or []) if str(row.get("lane_id")) == "lane.systems" and str(row.get("family_kind")) == "policy")
    repo_tool_scope = next(row for row in list(stage5_registry.get("rows") or []) if str(row.get("lane_id")) == "lane.repo_swe" and str(row.get("family_kind")) == "tool_scope")
    retained_prior = stage4_transfer_map["transfer.stage4.repo_swe.topology_to_systems.v0"]

    rows = [
        {
            "transfer_id": "transfer.stage5.repo_swe.topology_to_systems.v0",
            "source_lane_id": "lane.repo_swe",
            "target_lane_id": "lane.systems",
            "family_id": repo_topology["family_id"],
            "family_kind": "topology",
            "source_family_state": repo_topology["stage5_family_state"],
            "transfer_status": "retained",
            "comparison_valid": True,
            "invalid_reason": None,
            "transfer_reason": "stage4_retained_transfer_remains_consistent_with_current_systems_primary_surface",
            "target_metrics": {
                "reuse_lift_count": int(quality_map["lane.systems"]["reuse_lift_count"]),
                "no_lift_count": int(quality_map["lane.systems"]["no_lift_count"]),
                "reuse_lift_rate": float(quality_map["lane.systems"]["reuse_lift_rate"]),
            },
            "evidence_refs": [
                path_ref(stage5_registry_path),
                path_ref(compounding_quality_path),
                path_ref(stage4_transfer_path),
            ],
            "prior_transfer_ref": path_ref(stage4_transfer_path) + "#transfer.stage4.repo_swe.topology_to_systems.v0",
            "replay_status": str(retained_prior.get("replay_status") or "unknown"),
        },
        {
            "transfer_id": "transfer.stage5.systems.policy_to_scheduling.v0",
            "source_lane_id": "lane.systems",
            "target_lane_id": "lane.scheduling",
            "family_id": systems_policy["family_id"],
            "family_kind": "policy",
            "source_family_state": systems_policy["stage5_family_state"],
            "transfer_status": "invalid_comparison",
            "comparison_valid": False,
            "invalid_reason": "evaluator_scope_mismatch",
            "transfer_reason": "scheduling_is_not_in_current_stage5_live_comparison_scope",
            "target_metrics": {},
            "evidence_refs": [
                path_ref(stage5_registry_path),
                path_ref(compounding_quality_path),
            ],
            "prior_transfer_ref": path_ref(stage4_transfer_path) + "#transfer.stage4.systems.policy_to_scheduling.v0",
            "replay_status": "replay_observed_but_weak",
        },
        {
            "transfer_id": "transfer.stage5.repo_swe.tool_scope_to_systems.v0",
            "source_lane_id": "lane.repo_swe",
            "target_lane_id": "lane.systems",
            "family_id": repo_tool_scope["family_id"],
            "family_kind": "tool_scope",
            "source_family_state": repo_tool_scope["stage5_family_state"],
            "transfer_status": "failed_transfer",
            "comparison_valid": False,
            "invalid_reason": "family_not_active",
            "transfer_reason": "repo_swe_tool_scope_is_held_back_and_not_in_current_stage5_transfer_scope",
            "target_metrics": {
                "allowed_target_lanes": list(dict(repo_tool_scope.get("transfer_eligibility") or {}).get("allowed_target_lanes") or []),
            },
            "evidence_refs": [
                path_ref(stage5_registry_path),
            ],
            "prior_transfer_ref": None,
            "replay_status": "not_applicable_invalid_transfer",
        },
    ]

    payload = {
        "schema": "breadboard.darwin.stage5.bounded_transfer_outcomes.v0",
        "source_refs": {
            "stage5_registry_ref": path_ref(stage5_registry_path),
            "compounding_quality_ref": path_ref(compounding_quality_path),
            "stage4_transfer_ref": path_ref(stage4_transfer_path),
        },
        "row_count": len(rows),
        "retained_transfer_count": sum(1 for row in rows if str(row.get("transfer_status")) == "retained"),
        "rows": rows,
    }
    lines = ["# Stage-5 Bounded Transfer Outcomes", ""]
    for row in rows:
        lines.append(
            f"- `{row['transfer_id']}`: status=`{row['transfer_status']}`, reason=`{row['transfer_reason']}`, invalid=`{row['invalid_reason']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "retained_transfer_count": payload["retained_transfer_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 bounded transfer outcomes.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_bounded_transfer_outcomes()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_bounded_transfer_outcomes={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
