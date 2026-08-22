from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.ledger import build_stage4_family_decision_record  # noqa: E402
from breadboard_ext.darwin.stage4_family_program import OUT_DIR, candidate_rank, load_json, path_ref, write_json, write_text  # noqa: E402


CANDIDATES = OUT_DIR / "family_candidates_v0.json"
OUT_JSON = OUT_DIR / "family_promotion_report_v0.json"
OUT_MD = OUT_DIR / "family_promotion_report_v0.md"
LEDGER_JSON = OUT_DIR / "stage4_decision_ledger_v1.json"


def _choose_lane_family(rows: list[dict], *, preferred_kinds: list[str]) -> dict:
    for family_kind in preferred_kinds:
        for row in rows:
            if row["family_kind"] == family_kind and row["promotion_class"] in {"promotion_ready", "provisional_family"}:
                return row
    return rows[0]


def build_stage4_family_promotion_report() -> dict[str, str | int]:
    candidates = load_json(CANDIDATES)
    rows = sorted(list(candidates.get("rows") or []), key=candidate_rank)
    repo_rows = [row for row in rows if row["lane_id"] == "lane.repo_swe"]
    systems_rows = [row for row in rows if row["lane_id"] == "lane.systems"]

    promoted_repo = dict(_choose_lane_family(repo_rows, preferred_kinds=["topology", "tool_scope", "policy"]))
    promoted_systems = dict(_choose_lane_family(systems_rows, preferred_kinds=["policy", "topology"]))

    report_rows: list[dict] = []
    decision_records: list[dict] = []
    for row in rows:
        report_row = dict(row)
        if row["family_id"] == promoted_repo["family_id"] or row["family_id"] == promoted_systems["family_id"]:
            report_row["promotion_outcome"] = "promoted"
            decision_type = "promotion"
        elif row["promotion_class"] in {"promotion_ready", "provisional_family"}:
            report_row["promotion_outcome"] = "withheld"
            decision_type = "deprecation"
        else:
            report_row["promotion_outcome"] = "not_promoted"
            decision_type = "deprecation"
        report_row["decision_reason"] = (
            "selected_as_primary_family" if report_row["promotion_outcome"] == "promoted"
            else "family_not_selected_in_first_multi_family_tranche"
        )
        report_rows.append(report_row)
        decision_records.append(
            build_stage4_family_decision_record(
                decision_id=f"decision.stage4.{report_row['promotion_outcome']}.{report_row['lane_id']}.{report_row['family_kind']}.v0",
                lane_id=report_row["lane_id"],
                decision_type=decision_type,
                family_id=report_row["family_id"],
                candidate_ids=list(report_row["candidate_ids"]),
                evidence_refs=list(report_row["evidence_refs"]) + [path_ref(OUT_JSON)],
                replay_refs=[ref for ref in report_row["evidence_refs"] if ref.endswith("replay_checks_v0.json")],
                decision_basis={
                    "family_kind": report_row["family_kind"],
                    "promotion_class": report_row["promotion_class"],
                    "promotion_outcome": report_row["promotion_outcome"],
                    "decision_reason": report_row["decision_reason"],
                    "valid_comparison_count": report_row["valid_comparison_count"],
                    "positive_power_signal_count": report_row["positive_power_signal_count"],
                    "signal_rate": report_row["signal_rate"],
                    "replay_status": report_row["replay_status"],
                },
            )
        )

    payload = {
        "schema": "breadboard.darwin.stage4.family_promotion_report.v0",
        "row_count": len(report_rows),
        "rows": report_rows,
    }
    lines = [
        "# Stage-4 Family Promotion Report",
        "",
        f"- promoted repo family: `{promoted_repo['family_id']}`",
        f"- promoted systems family: `{promoted_systems['family_id']}`",
    ]
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    write_json(
        LEDGER_JSON,
        {
            "schema": "breadboard.darwin.stage4.decision_ledger.v1",
            "archive_is_derived": True,
            "decision_truth_scope": {
                "canonical_decision_types": ["promotion", "transfer", "deprecation"],
                "runtime_truth_owned_by": "breadboard_runtime",
                "archive_is_derived": True,
            },
            "decision_records": decision_records,
        },
    )
    return {"out_json": str(OUT_JSON), "ledger_json": str(LEDGER_JSON), "row_count": len(report_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 family promotion report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_family_promotion_report()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_promotion_report={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
