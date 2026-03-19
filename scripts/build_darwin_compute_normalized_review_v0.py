from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
COMPUTE_VIEW = ROOT / "artifacts" / "darwin" / "scorecards" / "compute_normalized_view_v2.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "reviews"
PROVING_LANES = ("lane.harness", "lane.repo_swe")
AUDIT_LANES = ("lane.scheduling", "lane.atp")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _review_read(row: dict) -> str:
    lane_id = row["lane_id"]
    status = row["comparison_status"]
    flags = set(row.get("interpretation_flags") or [])
    if lane_id == "lane.harness":
        if status == "baseline_retained_valid":
            return "Harness is score-saturated; runtime-normalized fields are descriptive and confirm baseline retention without comparison invalidity."
        return "Harness needs follow-up because its normalized view is not in the expected retained-baseline state."
    if lane_id == "lane.repo_swe":
        if status == "baseline_retained_with_invalid_trials" and "lane_contains_invalid_trials" in flags:
            return "Repo_SWE correctly isolates invalid trials while retaining a valid baseline comparison surface."
        return "Repo_SWE needs follow-up because invalid-trial handling is not explicit enough in the normalized surface."
    if lane_id == "lane.scheduling":
        return "Scheduling remains audit-only here; promoted-candidate runtime and score movement look coherent under the additive normalized surface."
    if lane_id == "lane.atp":
        return "ATP remains audit-only; normalized fields are descriptive and not a proving basis for this tranche."
    return "Internal comparative review row."


def _review_outcome(row: dict) -> str:
    lane_id = row["lane_id"]
    status = row["comparison_status"]
    if lane_id == "lane.harness":
        return "coherent" if status == "baseline_retained_valid" else "needs_followup"
    if lane_id == "lane.repo_swe":
        flags = set(row.get("interpretation_flags") or [])
        return "coherent" if status == "baseline_retained_with_invalid_trials" and "lane_contains_invalid_trials" in flags else "needs_followup"
    return "audit_only"


def build_review() -> dict:
    scorecard = _load_json(SCORECARD)
    compute_view = _load_json(COMPUTE_VIEW)
    scorecard_rows = {row["lane_id"]: row for row in scorecard.get("lanes") or []}
    compute_rows = {row["lane_id"]: row for row in compute_view.get("lanes") or []}

    def make_row(lane_id: str) -> dict:
        scorecard_row = scorecard_rows[lane_id]
        compute_row = compute_rows[lane_id]
        return {
            "lane_id": lane_id,
            "comparison_status": compute_row["comparison_status"],
            "comparison_valid": compute_row["comparison_valid"],
            "score_delta": compute_row["score_delta"],
            "runtime_rate_delta": compute_row["runtime_rate_delta"],
            "baseline_runtime_score_per_second": compute_row["baseline_runtime_score_per_second"],
            "active_runtime_score_per_second": compute_row["active_runtime_score_per_second"],
            "baseline_local_cost_classification": compute_row["baseline_local_cost_classification"],
            "active_local_cost_classification": compute_row["active_local_cost_classification"],
            "invalid_trial_count": compute_row["invalid_trial_count"],
            "interpretation_flags": compute_row["interpretation_flags"],
            "scorecard_status": scorecard_row["status"],
            "search_maturity": scorecard_row["search_maturity"],
            "review_outcome": _review_outcome(compute_row),
            "review_read": _review_read(compute_row),
        }

    proving_rows = [make_row(lane_id) for lane_id in PROVING_LANES]
    audit_rows = [make_row(lane_id) for lane_id in AUDIT_LANES]
    coherent_proving_count = sum(1 for row in proving_rows if row["review_outcome"] == "coherent")
    return {
        "schema": "breadboard.darwin.compute_normalized_review.v0",
        "scorecard_ref": str(SCORECARD.relative_to(ROOT)),
        "compute_view_ref": str(COMPUTE_VIEW.relative_to(ROOT)),
        "proving_lane_count": len(proving_rows),
        "audit_lane_count": len(audit_rows),
        "coherent_proving_count": coherent_proving_count,
        "proving_rows": proving_rows,
        "audit_rows": audit_rows,
        "tranche_read": "additive_only_surface_getting_clearer" if coherent_proving_count == len(proving_rows) else "needs_more_proving_clarity",
        "next_recommendation": "continue additive-only compute-normalized tranche; do not broaden transfer families yet",
    }


def _to_markdown(payload: dict) -> str:
    lines = [
        "# DARWIN Compute-Normalized Review v0",
        "",
        f"- scorecard_ref: `{payload['scorecard_ref']}`",
        f"- compute_view_ref: `{payload['compute_view_ref']}`",
        f"- coherent_proving_count: `{payload['coherent_proving_count']}/{payload['proving_lane_count']}`",
        f"- tranche_read: `{payload['tranche_read']}`",
        "",
        "## Proving Lanes",
        "",
    ]
    for row in payload["proving_rows"]:
        lines.append(
            f"- `{row['lane_id']}` — `{row['comparison_status']}` — runtime delta `{row['runtime_rate_delta']}` — invalid trials `{row['invalid_trial_count']}` — {row['review_read']}"
        )
    lines.extend(["", "## Audit Lanes", ""])
    for row in payload["audit_rows"]:
        lines.append(
            f"- `{row['lane_id']}` — `{row['comparison_status']}` — {row['review_read']}"
        )
    lines.extend(["", "## Recommendation", "", f"- {payload['next_recommendation']}"])
    return "\n".join(lines).rstrip() + "\n"


def write_review(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_review()
    out_json = out_dir / "compute_normalized_review_v0.json"
    out_md = out_dir / "compute_normalized_review_v0.md"
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    return {
        "out_json": str(out_json),
        "out_md": str(out_md),
        "coherent_proving_count": payload["coherent_proving_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN compute-normalized proving review.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_review()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"compute_normalized_review_json={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
