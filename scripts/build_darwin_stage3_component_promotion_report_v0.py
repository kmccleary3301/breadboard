from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.ledger import build_stage3_component_decision_record
from breadboard_ext.darwin.stage3_component_transfer import (
    OUT_DIR,
    candidate_rank,
    load_json,
    write_json,
    write_text,
)


CANDIDATES = OUT_DIR / "component_candidates_v0.json"
REPLAY = OUT_DIR / "component_replay_v0.json"
OUT_JSON = OUT_DIR / "component_promotion_report_v0.json"
OUT_MD = OUT_DIR / "component_promotion_report_v0.md"
LEDGER_JSON = OUT_DIR / "stage3_decision_ledger_v1.json"


def write_component_promotion_report() -> dict[str, str | int]:
    candidates_payload = load_json(CANDIDATES)
    replay_payload = load_json(REPLAY)
    replay_lookup = {row["lane_id"]: row for row in replay_payload.get("rows") or []}
    rows = sorted(list(candidates_payload.get("rows") or []), key=candidate_rank)

    repo_candidates = [row for row in rows if row["lane_id"] == "lane.repo_swe"]
    systems_candidates = [row for row in rows if row["lane_id"] == "lane.systems"]
    selected_repo = next((row for row in repo_candidates if row["component_kind"] == "topology"), repo_candidates[0])
    repo_replay = replay_lookup.get("lane.repo_swe", {})
    selected_repo["promotion_outcome"] = "promoted" if selected_repo["promotion_class"] == "promotion_ready" and repo_replay else "withheld"
    selected_repo["replay_status"] = "supported" if repo_replay.get("replay_supported") else ("observed_nonconfirming" if repo_replay else "missing")

    selected_systems = next((row for row in systems_candidates if row["component_kind"] == "topology"), systems_candidates[0])
    selected_systems["promotion_outcome"] = "not_promoted"
    selected_systems["replay_status"] = "observed" if replay_lookup.get("lane.systems", {}).get("replay_supported") else "not_observed"
    selected_systems["decision_reason"] = "secondary_confirmation_only_in_this_tranche"

    decisions = [
        build_stage3_component_decision_record(
            decision_id="decision.stage3.promote.repo_swe.topology_pev.v0",
            lane_id="lane.repo_swe",
            decision_type="promotion",
            component_family_id=selected_repo["component_family_id"],
            candidate_ids=["cand.lane.repo_swe.topology_r1.v1", "cand.lane.repo_swe.topology_r2.v1"],
            evidence_refs=[str(CANDIDATES.relative_to(ROOT)), str(OUT_JSON.relative_to(ROOT))],
            replay_refs=[str(REPLAY.relative_to(ROOT))],
            decision_basis={
                "promotion_class": selected_repo["promotion_class"],
                "promotion_outcome": selected_repo["promotion_outcome"],
                "replay_status": selected_repo["replay_status"],
                "valid_comparison_count": selected_repo["valid_comparison_count"],
                "improvement_count": selected_repo["improvement_count"],
                "matched_budget_retention_rate": selected_repo["matched_budget_retention_rate"],
            },
        ),
        build_stage3_component_decision_record(
            decision_id="decision.stage3.reject.systems.topology_pev.v0",
            lane_id="lane.systems",
            decision_type="deprecation",
            component_family_id=selected_systems["component_family_id"],
            candidate_ids=["cand.lane.systems.topology_r1.v1", "cand.lane.systems.topology_r2.v1"],
            evidence_refs=[str(CANDIDATES.relative_to(ROOT)), str(OUT_JSON.relative_to(ROOT))],
            replay_refs=[str(REPLAY.relative_to(ROOT))],
            decision_basis={
                "promotion_class": selected_systems["promotion_class"],
                "promotion_outcome": selected_systems["promotion_outcome"],
                "decision_reason": selected_systems["decision_reason"],
                "valid_comparison_count": selected_systems["valid_comparison_count"],
                "improvement_count": selected_systems["improvement_count"],
            },
        ),
    ]

    report_rows = [
        {
            "lane_id": selected_repo["lane_id"],
            "component_family_id": selected_repo["component_family_id"],
            "component_kind": selected_repo["component_kind"],
            "component_key": selected_repo["component_key"],
            "promotion_class": selected_repo["promotion_class"],
            "promotion_outcome": selected_repo["promotion_outcome"],
            "replay_status": selected_repo["replay_status"],
            "valid_comparison_count": selected_repo["valid_comparison_count"],
            "improvement_count": selected_repo["improvement_count"],
            "average_runtime_delta_ms": selected_repo["average_runtime_delta_ms"],
        },
        {
            "lane_id": selected_systems["lane_id"],
            "component_family_id": selected_systems["component_family_id"],
            "component_kind": selected_systems["component_kind"],
            "component_key": selected_systems["component_key"],
            "promotion_class": selected_systems["promotion_class"],
            "promotion_outcome": selected_systems["promotion_outcome"],
            "replay_status": selected_systems["replay_status"],
            "decision_reason": selected_systems["decision_reason"],
            "valid_comparison_count": selected_systems["valid_comparison_count"],
            "improvement_count": selected_systems["improvement_count"],
            "average_runtime_delta_ms": selected_systems["average_runtime_delta_ms"],
        },
    ]
    payload = {
        "schema": "breadboard.darwin.stage3.component_promotion_report.v0",
        "row_count": len(report_rows),
        "rows": report_rows,
    }
    lines = [
        "# Stage-3 Component Promotion Report",
        "",
        f"- `lane.repo_swe` promoted `{selected_repo['component_family_id']}` with replay_status=`{selected_repo['replay_status']}`",
        f"- `lane.systems` withheld `{selected_systems['component_family_id']}` because `{selected_systems['decision_reason']}`",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    write_json(
        LEDGER_JSON,
        {
            "schema": "breadboard.darwin.stage3.decision_ledger.v1",
            "archive_is_derived": True,
            "decision_truth_scope": {
                "canonical_decision_types": ["promotion", "transfer", "deprecation"],
                "runtime_truth_owned_by": "breadboard_runtime",
                "archive_is_derived": True,
            },
            "decision_records": decisions,
        },
    )
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "ledger_json": str(LEDGER_JSON), "row_count": len(report_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 component-promotion report and decision ledger.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_component_promotion_report()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_promotion_report={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
