from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.ledger import build_stage4_transfer_decision_record  # noqa: E402
from breadboard_ext.darwin.stage4_family_program import DEEP_DIR, OUT_DIR, load_json, path_ref, write_json, write_text  # noqa: E402


PROMOTION = OUT_DIR / "family_promotion_report_v0.json"
REGISTRY = OUT_DIR / "family_registry_v0.json"
CANDIDATES = OUT_DIR / "family_candidates_v0.json"
REPLAY = DEEP_DIR / "replay_checks_v0.json"
OUT_JSON = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_MD = OUT_DIR / "bounded_transfer_outcomes_v0.md"
LEDGER_JSON = OUT_DIR / "stage4_decision_ledger_v1.json"


def build_stage4_bounded_transfer_outcomes() -> dict[str, str | int]:
    promotion = load_json(PROMOTION)
    registry = load_json(REGISTRY)
    candidates = load_json(CANDIDATES)
    replay = load_json(REPLAY)
    promoted_lookup = {row["family_id"]: row for row in promotion.get("rows") or [] if row["promotion_outcome"] == "promoted"}
    candidate_lookup = {(row["lane_id"], row["source_operator_id"]): row for row in candidates.get("rows") or []}
    replay_lookup = {(row["lane_id"], row["operator_id"]): row for row in replay.get("rows") or []}

    repo_topology = next(row for row in promotion.get("rows") or [] if row["lane_id"] == "lane.repo_swe" and row["family_kind"] == "topology")
    systems_topology = candidate_lookup[("lane.systems", "mut.topology.single_to_pev_v1")]
    repo_toolscope = next(row for row in promotion.get("rows") or [] if row["lane_id"] == "lane.repo_swe" and row["family_kind"] == "tool_scope")
    systems_policy = next(row for row in promotion.get("rows") or [] if row["lane_id"] == "lane.systems" and row["family_kind"] == "policy")

    rows = [
        {
            "transfer_id": "transfer.stage4.repo_swe.topology_to_systems.v0",
            "source_lane_id": "lane.repo_swe",
            "target_lane_id": "lane.systems",
            "family_id": repo_topology["family_id"],
            "family_kind": repo_topology["family_kind"],
            "transfer_status": "retained",
            "comparison_valid": True,
            "invalid_reason": None,
            "transfer_reason": "target_lane_has_valid_positive_topology_signal",
            "replay_status": "source_replay_supported" if replay_lookup.get(("lane.repo_swe", "mut.topology.single_to_pev_v1"), {}).get("replay_supported") else "source_replay_missing",
            "target_metrics": {
                "valid_comparison_count": systems_topology["valid_comparison_count"],
                "positive_power_signal_count": systems_topology["positive_power_signal_count"],
                "signal_rate": systems_topology["signal_rate"],
            },
            "evidence_refs": [
                path_ref(PROMOTION),
                path_ref(CANDIDATES),
                path_ref(REPLAY),
            ],
        },
        {
            "transfer_id": "transfer.stage4.repo_swe.toolscope_to_scheduling.v0",
            "source_lane_id": "lane.repo_swe",
            "target_lane_id": "lane.scheduling",
            "family_id": repo_toolscope["family_id"],
            "family_kind": repo_toolscope["family_kind"],
            "transfer_status": "failed_transfer",
            "comparison_valid": False,
            "invalid_reason": "unsupported_lane_scope",
            "transfer_reason": "tool_scope_family_is_not_in_first_transfer_scope_for_scheduling",
            "replay_status": "not_applicable_invalid_transfer",
            "target_metrics": {
                "allowed_target_lanes": next(row for row in registry.get("rows") or [] if row["family_id"] == repo_toolscope["family_id"])["transfer_eligibility"]["allowed_target_lanes"],
            },
            "evidence_refs": [
                path_ref(PROMOTION),
                path_ref(REGISTRY),
            ],
        },
        {
            "transfer_id": "transfer.stage4.systems.policy_to_scheduling.v0",
            "source_lane_id": "lane.systems",
            "target_lane_id": "lane.scheduling",
            "family_id": systems_policy["family_id"],
            "family_kind": systems_policy["family_kind"],
            "transfer_status": "invalid_comparison",
            "comparison_valid": False,
            "invalid_reason": "evaluator_scope_mismatch",
            "transfer_reason": "scheduling_not_yet_in_stage4_live_comparison_scope",
            "replay_status": "source_replay_supported" if replay_lookup.get(("lane.systems", "mut.policy.shadow_memory_enable_v1"), {}).get("replay_supported") else "source_replay_missing",
            "target_metrics": {},
            "evidence_refs": [
                path_ref(PROMOTION),
                path_ref(REPLAY),
            ],
        },
    ]

    decision_records = []
    for row in rows:
        decision_records.append(
            build_stage4_transfer_decision_record(
                decision_id=f"decision.stage4.transfer.{row['source_lane_id']}.{row['target_lane_id']}.{row['family_kind']}.v0".replace("lane.", ""),
                source_lane_id=row["source_lane_id"],
                target_lane_id=row["target_lane_id"],
                family_id=row["family_id"],
                evidence_refs=list(row["evidence_refs"]) + [path_ref(OUT_JSON)],
                replay_refs=[ref for ref in row["evidence_refs"] if ref.endswith("replay_checks_v0.json")],
                decision_basis={
                    "transfer_status": row["transfer_status"],
                    "comparison_valid": row["comparison_valid"],
                    "invalid_reason": row["invalid_reason"],
                    "transfer_reason": row["transfer_reason"],
                },
            )
        )

    existing_ledger = load_json(LEDGER_JSON)
    existing_ledger["decision_records"].extend(decision_records)
    payload = {
        "schema": "breadboard.darwin.stage4.bounded_transfer_outcomes.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    lines = [
        "# Stage-4 Bounded Transfer Outcomes",
        "",
        f"- retained: `{rows[0]['family_id']}` from `lane.repo_swe` to `lane.systems`",
        f"- failed: `{rows[1]['family_id']}` to `lane.scheduling` because `{rows[1]['invalid_reason']}`",
        f"- invalid: `{rows[2]['family_id']}` to `lane.scheduling` because `{rows[2]['invalid_reason']}`",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    write_json(LEDGER_JSON, existing_ledger)
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 bounded transfer outcomes.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_bounded_transfer_outcomes()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_bounded_transfer_outcomes={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
