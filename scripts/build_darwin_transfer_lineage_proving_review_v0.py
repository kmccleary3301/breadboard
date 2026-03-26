from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "artifacts" / "darwin" / "search" / "evolution_ledger_v0.json"
TRANSFER_VIEW = ROOT / "artifacts" / "darwin" / "reviews" / "transfer_family_view_v0.json"
LINEAGE_REVIEW = ROOT / "artifacts" / "darwin" / "reviews" / "lineage_review_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "reviews"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _decision_lookup(records: list[dict]) -> dict[str, dict]:
    return {row["decision_id"]: row for row in records}


def build_transfer_lineage_proving_review() -> dict:
    ledger = _load_json(LEDGER)
    transfer_view = _load_json(TRANSFER_VIEW)
    lineage_review = _load_json(LINEAGE_REVIEW)

    decisions = _decision_lookup(ledger.get("decision_records") or [])
    lineage_rows = {row["lane_id"]: row for row in lineage_review.get("lanes") or []}
    transfer_rows = transfer_view.get("rows") or []
    transfer_row = transfer_rows[0] if transfer_rows else None

    repo_swe_retain = decisions["decision.retain.lane.repo_swe.cycle1.v1"]
    repo_swe_class_b = decisions["decision.deprecate.lane.repo_swe.mut.class_b.v1"]
    repo_swe_pev = decisions["decision.deprecate.lane.repo_swe.mut.pev.v1"]
    scheduling_cycle2 = decisions["decision.promote.lane.scheduling.cycle2.v1"]
    scheduling_lineage = lineage_rows["lane.scheduling"]

    rows = [
        {
            "lane_id": "lane.repo_swe",
            "role": "primary_proving",
            "review_kind": "invalid_sensitive_transfer_lineage",
            "decision_ids": [
                repo_swe_retain["decision_id"],
                repo_swe_class_b["decision_id"],
                repo_swe_pev["decision_id"],
            ],
            "review_outcome": "coherent_invalid_sensitive",
            "review_read": "baseline retention stays explicit while invalid and replay-stable rejected candidates remain separated",
            "evidence_refs": sorted(
                {
                    *repo_swe_retain["evidence_refs"],
                    *repo_swe_retain["replay_refs"],
                    *repo_swe_class_b["evidence_refs"],
                    *repo_swe_pev["evidence_refs"],
                    *repo_swe_pev["replay_refs"],
                }
            ),
        },
        {
            "lane_id": "lane.scheduling",
            "role": "primary_proving",
            "review_kind": "multi_cycle_lineage",
            "decision_ids": [
                "decision.promote.lane.scheduling.cycle1.v1",
                scheduling_cycle2["decision_id"],
            ],
            "review_outcome": "coherent_multi_cycle",
            "review_read": "promotion depth, supersession chain, and rollback target remain explicit for the active promoted candidate",
            "evidence_refs": sorted({*scheduling_cycle2["evidence_refs"], *scheduling_cycle2["replay_refs"]}),
            "promotion_history_depth": scheduling_lineage["promotion_history_depth"],
            "rollback_candidate_id": scheduling_lineage["rollback_candidate_id"],
            "superseded_candidate_ids": scheduling_lineage["superseded_candidate_ids"],
        },
        {
            "lane_id": "lane.harness",
            "role": "bounded_source",
            "review_kind": "bounded_transfer_source",
            "decision_ids": ["decision.retain.lane.harness.cycle1.v1"],
            "review_outcome": "bounded_source_validated",
            "review_read": "prompt-family source remains bounded and score-saturated rather than over-claimed as a proving win",
            "evidence_refs": decisions["decision.retain.lane.harness.cycle1.v1"]["evidence_refs"],
        },
        {
            "lane_id": "lane.research",
            "role": "bounded_target",
            "review_kind": "bounded_transfer_target",
            "decision_ids": ["decision.transfer.harness.prompt_to_research.v1"],
            "review_outcome": "bounded_transfer_validated",
            "review_read": "cross-lane prompt-family transfer is replay-stable and still treated as bounded internal evidence",
            "evidence_refs": sorted(
                {
                    *decisions["decision.transfer.harness.prompt_to_research.v1"]["evidence_refs"],
                    *decisions["decision.transfer.harness.prompt_to_research.v1"]["replay_refs"],
                }
            ),
            "transfer_family": transfer_row["transfer_family"] if transfer_row else None,
            "result_class": transfer_row["result_class"] if transfer_row else None,
        },
        {
            "lane_id": "lane.atp",
            "role": "audit_only",
            "review_kind": "audit_only",
            "decision_ids": [],
            "review_outcome": "audit_only_confirmed",
            "review_read": "ATP remains outside the transfer-family proving center and is only used for semantic audit context",
            "evidence_refs": ["docs/darwin_phase2_transfer_lineage_execution_plan_2026-03-18.md"],
        },
    ]

    return {
        "schema": "breadboard.darwin.transfer_lineage_proving_review.v0",
        "row_count": len(rows),
        "rows": rows,
        "source_refs": {
            "evolution_ledger_ref": str(LEDGER.relative_to(ROOT)),
            "transfer_family_view_ref": str(TRANSFER_VIEW.relative_to(ROOT)),
            "lineage_review_ref": str(LINEAGE_REVIEW.relative_to(ROOT)),
        },
    }


def write_transfer_lineage_proving_review(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_transfer_lineage_proving_review()
    json_path = out_dir / "transfer_lineage_proving_review_v0.json"
    md_path = out_dir / "transfer_lineage_proving_review_v0.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# DARWIN Transfer/Lineage Proving Review V0", ""]
    for row in payload["rows"]:
        lines.extend(
            [
                f"## {row['lane_id']}",
                "",
                f"- role: `{row['role']}`",
                f"- review kind: `{row['review_kind']}`",
                f"- outcome: `{row['review_outcome']}`",
                f"- read: {row['review_read']}",
            ]
        )
        if row.get("decision_ids"):
            lines.append(f"- decision ids: `{', '.join(row['decision_ids'])}`")
        if row.get("transfer_family"):
            lines.append(f"- transfer family: `{row['transfer_family']}`")
        if row.get("result_class"):
            lines.append(f"- result class: `{row['result_class']}`")
        if row.get("promotion_history_depth") is not None:
            lines.append(f"- promotion history depth: `{row['promotion_history_depth']}`")
        if row.get("rollback_candidate_id"):
            lines.append(f"- rollback candidate: `{row['rollback_candidate_id']}`")
        if row.get("superseded_candidate_ids"):
            lines.append(f"- superseded candidates: `{', '.join(row['superseded_candidate_ids'])}`")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "row_count": payload["row_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN transfer/lineage proving review.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_transfer_lineage_proving_review()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"transfer_lineage_proving_review={summary['json_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
