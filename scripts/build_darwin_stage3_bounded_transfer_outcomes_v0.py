from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.ledger import build_stage3_transfer_decision_record
from breadboard_ext.darwin.stage3_component_transfer import (
    OUT_DIR,
    load_json,
    policy_registry_lookup,
    write_json,
    write_text,
)


PROMOTION_REPORT = OUT_DIR / "component_promotion_report_v0.json"
REPLAY = OUT_DIR / "component_replay_v0.json"
OUT_JSON = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_MD = OUT_DIR / "bounded_transfer_outcomes_v0.md"
LEDGER_JSON = OUT_DIR / "stage3_decision_ledger_v1.json"


def write_bounded_transfer_outcomes() -> dict[str, str | int]:
    promotion_payload = load_json(PROMOTION_REPORT)
    replay_payload = load_json(REPLAY)
    policy_lookup = policy_registry_lookup()
    replay_lookup = {row["lane_id"]: row for row in replay_payload.get("rows") or []}

    promoted = next(row for row in promotion_payload.get("rows") or [] if row["promotion_outcome"] == "promoted")
    systems_row = next(row for row in promotion_payload.get("rows") or [] if row["lane_id"] == "lane.systems")
    policy_bundle = policy_lookup[promoted["component_key"]]

    retained_transfer = {
        "transfer_id": "transfer.stage3.repo_swe.topology_pev_to_systems.v0",
        "source_lane_id": "lane.repo_swe",
        "target_lane_id": "lane.systems",
        "component_family_id": promoted["component_family_id"],
        "component_kind": promoted["component_kind"],
        "component_key": promoted["component_key"],
        "transfer_status": "retained",
        "comparison_valid": True,
        "invalid_reason": None,
        "transfer_reason": "matched_budget_valid_and_target_lane_score_retained",
        "replay_status": "source_replay_supported" if replay_lookup["lane.repo_swe"]["replay_supported"] else "source_replay_unsupported",
        "evidence_refs": [
            str(PROMOTION_REPORT.relative_to(ROOT)),
            str(REPLAY.relative_to(ROOT)),
        ],
        "target_metrics": {
            "valid_comparison_count": systems_row["valid_comparison_count"],
            "improvement_count": systems_row["improvement_count"],
            "average_runtime_delta_ms": systems_row["average_runtime_delta_ms"],
        },
    }

    failed_transfer = {
        "transfer_id": "transfer.stage3.repo_swe.topology_pev_to_scheduling.v0",
        "source_lane_id": "lane.repo_swe",
        "target_lane_id": "lane.scheduling",
        "component_family_id": promoted["component_family_id"],
        "component_kind": promoted["component_kind"],
        "component_key": promoted["component_key"],
        "transfer_status": "failed_transfer",
        "comparison_valid": False,
        "invalid_reason": "unsupported_lane_scope",
        "transfer_reason": "target_lane_not_in_component_scope",
        "replay_status": "not_applicable_invalid_transfer",
        "evidence_refs": [
            str(PROMOTION_REPORT.relative_to(ROOT)),
            str(ROOT.joinpath("docs/contracts/darwin/registries/policy_registry_v0.json").relative_to(ROOT)),
        ],
        "target_metrics": {
            "allowed_target_lanes": policy_bundle["applies_to_lanes"],
        },
    }

    rows = [retained_transfer, failed_transfer]
    lines = [
        "# Stage-3 Bounded Transfer Outcomes",
        "",
        f"- retained: `{retained_transfer['component_family_id']}` from `lane.repo_swe` to `lane.systems`",
        f"- failed: `{failed_transfer['component_family_id']}` from `lane.repo_swe` to `lane.scheduling` because `{failed_transfer['invalid_reason']}`",
    ]

    existing_ledger = load_json(LEDGER_JSON)
    transfer_decisions = [
        build_stage3_transfer_decision_record(
            decision_id="decision.stage3.transfer.repo_swe.topology_pev_to_systems.v0",
            source_lane_id="lane.repo_swe",
            target_lane_id="lane.systems",
            component_family_id=promoted["component_family_id"],
            evidence_refs=retained_transfer["evidence_refs"],
            replay_refs=[str(REPLAY.relative_to(ROOT))],
            decision_basis={
                "transfer_status": retained_transfer["transfer_status"],
                "comparison_valid": retained_transfer["comparison_valid"],
                "transfer_reason": retained_transfer["transfer_reason"],
            },
        ),
        build_stage3_transfer_decision_record(
            decision_id="decision.stage3.transfer.repo_swe.topology_pev_to_scheduling.v0",
            source_lane_id="lane.repo_swe",
            target_lane_id="lane.scheduling",
            component_family_id=promoted["component_family_id"],
            evidence_refs=failed_transfer["evidence_refs"],
            replay_refs=[],
            decision_basis={
                "transfer_status": failed_transfer["transfer_status"],
                "comparison_valid": failed_transfer["comparison_valid"],
                "invalid_reason": failed_transfer["invalid_reason"],
                "transfer_reason": failed_transfer["transfer_reason"],
            },
        ),
    ]
    decision_records = [
        row for row in (existing_ledger.get("decision_records") or []) if row.get("decision_type") != "transfer"
    ]
    decision_records.extend(transfer_decisions)

    payload = {
        "schema": "breadboard.darwin.stage3.bounded_transfer_outcomes.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    existing_ledger["decision_records"] = decision_records
    write_json(LEDGER_JSON, existing_ledger)
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 bounded transfer outcomes.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_bounded_transfer_outcomes()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"bounded_transfer_outcomes={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
