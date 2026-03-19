from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPUTE_REVIEW = ROOT / "artifacts" / "darwin" / "reviews" / "compute_normalized_review_v0.json"
TRANSFER_REVIEW = ROOT / "artifacts" / "darwin" / "reviews" / "transfer_lineage_proving_review_v0.json"
INVALIDITY = ROOT / "artifacts" / "darwin" / "reviews" / "external_safe_invalidity_summary_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "reviews"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_external_safe_reviewer_summary() -> dict:
    compute = _load_json(COMPUTE_REVIEW)
    transfer = _load_json(TRANSFER_REVIEW)
    invalidity = _load_json(INVALIDITY)
    compute_rows = {row["lane_id"]: row for row in (compute.get("proving_rows") or []) + (compute.get("audit_rows") or [])}
    transfer_rows = {row["lane_id"]: row for row in transfer.get("rows") or []}
    invalid_by_lane: dict[str, list[str]] = {}
    for row in invalidity.get("caveats") or []:
        invalid_by_lane.setdefault(row["lane_id"], []).append(row["summary"])

    rows = []
    for lane_id, role, baseline_story, changed_story, do_not_infer in [
        (
            "lane.repo_swe",
            "primary_proving",
            "Baseline remained active while invalid comparison pressure was preserved rather than flattened.",
            "Transfer-lineage review now makes retained-baseline, invalid-trial, and replay-stable rejected candidates legible in one place.",
            "Do not infer repo-wide SWE superiority or cost superiority from this lane.",
        ),
        (
            "lane.scheduling",
            "primary_proving",
            "Scheduling is the multi-cycle lineage lane with two bounded promotions.",
            "Rollback target and supersession chain are now explicit enough for external-safe review.",
            "Do not infer generalized scheduling dominance or broader topology superiority.",
        ),
        (
            "lane.harness",
            "supporting_source",
            "Harness is score-saturated and acts as a bounded transfer source rather than a proving win.",
            "The external-safe layer keeps it as context for the bounded prompt-family transfer only.",
            "Do not infer capability improvement from flat-score harness rows.",
        ),
        (
            "lane.research",
            "supporting_target",
            "Research is the bounded target for the prompt-family transfer case.",
            "The transfer is replay-stable but still explicitly descriptive-only and bounded.",
            "Do not infer broad transfer learning or cross-lane generalization.",
        ),
        (
            "lane.atp",
            "audit_only",
            "ATP remains outside the proving center for this tranche.",
            "It stays in the package only as audit context.",
            "Do not infer ATP transfer-family conclusions from this tranche.",
        ),
    ]:
        compute_row = compute_rows.get(lane_id, {})
        transfer_row = transfer_rows.get(lane_id, {})
        rows.append(
            {
                "lane_id": lane_id,
                "role": role,
                "baseline_story": baseline_story,
                "what_changed": changed_story,
                "replay_support": "explicit" if transfer_row.get("decision_ids") or compute_row else "supporting_only",
                "descriptive_status": "bounded_descriptive_only" if lane_id in {"lane.harness", "lane.research", "lane.atp"} else "bounded_claim_supporting",
                "invalidity_notes": invalid_by_lane.get(lane_id, []),
                "do_not_infer": do_not_infer,
            }
        )
    return {
        "schema": "breadboard.darwin.external_safe_reviewer_summary.v0",
        "row_count": len(rows),
        "rows": rows,
        "source_refs": {
            "compute_review_ref": str(COMPUTE_REVIEW.relative_to(ROOT)),
            "transfer_review_ref": str(TRANSFER_REVIEW.relative_to(ROOT)),
            "invalidity_summary_ref": str(INVALIDITY.relative_to(ROOT)),
        },
    }


def write_external_safe_reviewer_summary(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_external_safe_reviewer_summary()
    json_path = out_dir / "external_safe_reviewer_summary_v0.json"
    md_path = out_dir / "external_safe_reviewer_summary_v0.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# DARWIN External-Safe Reviewer Summary V0", ""]
    for row in payload["rows"]:
        lines.extend(
            [
                f"## {row['lane_id']}",
                "",
                f"- role: `{row['role']}`",
                f"- baseline: {row['baseline_story']}",
                f"- what changed: {row['what_changed']}",
                f"- replay support: `{row['replay_support']}`",
                f"- status: `{row['descriptive_status']}`",
                f"- do not infer: {row['do_not_infer']}",
            ]
        )
        if row["invalidity_notes"]:
            lines.append(f"- invalidity notes: {'; '.join(row['invalidity_notes'])}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "row_count": payload["row_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN external-safe reviewer summary.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_external_safe_reviewer_summary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"external_safe_reviewer_summary={summary['json_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
