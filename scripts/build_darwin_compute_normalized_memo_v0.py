from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
COMPUTE_VIEW = ROOT / "artifacts" / "darwin" / "scorecards" / "compute_normalized_view_v2.json"
REVIEW = ROOT / "artifacts" / "darwin" / "reviews" / "compute_normalized_review_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "memos"
PROVING_LANES = ("lane.harness", "lane.repo_swe")
AUDIT_LANES = ("lane.scheduling", "lane.atp")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _lane_story(lane_id: str, compute_row: dict, review_row: dict) -> str:
    status = compute_row["comparison_status"]
    if lane_id == "lane.harness":
        return (
            "Harness remains score-saturated, but the new surface now explains that flat score does not mean no comparative information: "
            "the retained baseline is valid, runtime-normalized fields are explicit, and no invalid-comparison pressure is present."
        )
    if lane_id == "lane.repo_swe":
        return (
            "Repo_SWE is the key proving case because the surface now distinguishes a stable retained baseline from invalid trials. "
            "That makes the comparison decision-useful rather than merely documented."
        )
    if lane_id == "lane.scheduling":
        return (
            "Scheduling is still audit-only for this tranche, but its promoted-candidate row shows that the same normalized surface can carry real movement coherently."
        )
    if lane_id == "lane.atp":
        return (
            "ATP remains an audit lane. The normalized surface is descriptive only here and is intentionally not used as the proving center."
        )
    return review_row["review_read"]


def build_memo() -> dict:
    scorecard = _load_json(SCORECARD)
    compute_view = _load_json(COMPUTE_VIEW)
    review = _load_json(REVIEW)

    scorecard_rows = {row["lane_id"]: row for row in scorecard.get("lanes") or []}
    compute_rows = {row["lane_id"]: row for row in compute_view.get("lanes") or []}
    review_rows = {
        row["lane_id"]: row
        for row in (review.get("proving_rows") or []) + (review.get("audit_rows") or [])
    }

    def row_for(lane_id: str) -> dict:
        scorecard_row = scorecard_rows[lane_id]
        compute_row = compute_rows[lane_id]
        review_row = review_rows[lane_id]
        return {
            "lane_id": lane_id,
            "comparison_status": compute_row["comparison_status"],
            "comparison_valid": compute_row["comparison_valid"],
            "score_delta": compute_row["score_delta"],
            "runtime_rate_delta": compute_row["runtime_rate_delta"],
            "invalid_trial_count": compute_row["invalid_trial_count"],
            "review_outcome": review_row["review_outcome"],
            "search_maturity": scorecard_row["search_maturity"],
            "interpretation_flags": compute_row["interpretation_flags"],
            "lane_story": _lane_story(lane_id, compute_row, review_row),
        }

    proving_rows = [row_for(lane_id) for lane_id in PROVING_LANES]
    audit_rows = [row_for(lane_id) for lane_id in AUDIT_LANES]
    proving_clear_count = sum(1 for row in proving_rows if row["review_outcome"] == "coherent")

    return {
        "schema": "breadboard.darwin.compute_normalized_memo.v0",
        "scorecard_ref": str(SCORECARD.relative_to(ROOT)),
        "compute_view_ref": str(COMPUTE_VIEW.relative_to(ROOT)),
        "review_ref": str(REVIEW.relative_to(ROOT)),
        "proving_lane_count": len(proving_rows),
        "audit_lane_count": len(audit_rows),
        "proving_clear_count": proving_clear_count,
        "decision": "continue_compute_normalized_tranche",
        "unlock_transfer_lineage": False,
        "proving_rows": proving_rows,
        "audit_rows": audit_rows,
        "program_read": (
            "The compute-normalized tranche now improves decision quality on the proving lanes, but the strongest immediate value remains inside the current additive comparative layer."
        ),
        "next_read": "Continue the compute-normalized tranche; do not broaden transfer families yet.",
    }


def _to_markdown(payload: dict) -> str:
    lines = [
        "# DARWIN Compute-Normalized Memo v0",
        "",
        f"- scorecard_ref: `{payload['scorecard_ref']}`",
        f"- compute_view_ref: `{payload['compute_view_ref']}`",
        f"- review_ref: `{payload['review_ref']}`",
        f"- proving_clear_count: `{payload['proving_clear_count']}/{payload['proving_lane_count']}`",
        f"- decision: `{payload['decision']}`",
        f"- unlock_transfer_lineage: `{str(payload['unlock_transfer_lineage']).lower()}`",
        "",
        "## Proving Lanes",
        "",
    ]
    for row in payload["proving_rows"]:
        lines.append(
            f"- `{row['lane_id']}` — `{row['comparison_status']}` — runtime delta `{row['runtime_rate_delta']}` — {row['lane_story']}"
        )
    lines.extend(["", "## Audit Lanes", ""])
    for row in payload["audit_rows"]:
        lines.append(f"- `{row['lane_id']}` — {row['lane_story']}")
    lines.extend(["", "## Read", "", f"- {payload['program_read']}", f"- {payload['next_read']}"])
    return "\n".join(lines).rstrip() + "\n"


def write_memo(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_memo()
    out_json = out_dir / "compute_normalized_memo_v0.json"
    out_md = out_dir / "compute_normalized_memo_v0.md"
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    return {
        "out_json": str(out_json),
        "out_md": str(out_md),
        "proving_clear_count": payload["proving_clear_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN compute-normalized comparative memo.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_memo()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"compute_normalized_memo_json={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
