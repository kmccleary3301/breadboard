from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAIM_SUBSET = ROOT / "artifacts" / "darwin" / "claims" / "external_safe_claim_subset_v0.json"
REVIEWER_SUMMARY = ROOT / "artifacts" / "darwin" / "reviews" / "external_safe_reviewer_summary_v0.json"
INVALIDITY = ROOT / "artifacts" / "darwin" / "reviews" / "external_safe_invalidity_summary_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "memos"
POLICY_REF = "docs/contracts/darwin/DARWIN_EXTERNAL_SAFE_EVIDENCE_POLICY_V0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_external_safe_memo() -> dict:
    claims = _load_json(CLAIM_SUBSET)
    reviewer = _load_json(REVIEWER_SUMMARY)
    invalidity = _load_json(INVALIDITY)
    included_classes = sorted({row["external_safe_class"] for row in claims.get("included_claims") or []})
    do_not_infer = [
        "Do not infer model superiority, benchmark dominance, or external cost superiority from this packet.",
        "Do not infer broad transfer learning beyond the bounded harness→research prompt-family case.",
        "Do not infer that audit lanes are proving lanes.",
    ]
    return {
        "schema": "breadboard.darwin.external_safe_memo.v0",
        "policy_ref": POLICY_REF,
        "scope_read": "bounded external-safe packaging of current DARWIN evidence only",
        "included_claim_classes": included_classes,
        "claimable_now": [row["source_claim_id"] for row in claims.get("included_claims") or []],
        "not_claimable_now": [row["source_claim_id"] for row in claims.get("excluded_claims") or [] if row["reason"] != "lane_local_readiness_claims_are_supporting_only"],
        "caution_labels": [
            "internal_artifacts_repackaged_for_external_review",
            "bounded_lane_set",
            "replay_conditioned",
            "invalid_comparisons_disclosed",
            "local_cost_accounting_partial",
            "no_superiority_claim",
        ],
        "replay_read": {
            "repo_swe": "rejected representative search candidate replay is stable and the invalid budget-mismatch trial remains explicitly excluded.",
            "scheduling": "promoted hybrid scheduling candidate replay is stable and remains the main lineage proving case.",
            "research_transfer": "bounded harness→research prompt-family transfer replay is stable and remains descriptive-only.",
            "atp": "ATP is audit-only and introduces no new replay obligation in this tranche.",
        },
        "cost_caution": {
            "exact": ["exact_local_zero", "exact_local_nonzero"],
            "estimated": ["estimated_local"],
            "unavailable": ["external_billing_unavailable", "local_cost_denominator_zero"],
            "rule": "Do not infer per-dollar superiority when external billing is unavailable or the local denominator is zero.",
        },
        "reviewer_summary_ref": str(REVIEWER_SUMMARY.relative_to(ROOT)),
        "invalidity_summary_ref": str(INVALIDITY.relative_to(ROOT)),
        "included_claim_count": claims["included_count"],
        "excluded_claim_count": claims["excluded_count"],
        "caveat_count": invalidity["caveat_count"],
        "lane_rows": reviewer["rows"],
        "do_not_infer": do_not_infer,
    }


def write_external_safe_memo(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_external_safe_memo()
    json_path = out_dir / "external_safe_evidence_memo_v0.json"
    md_path = out_dir / "external_safe_evidence_memo_v0.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# DARWIN External-Safe Evidence Memo V0",
        "",
        f"- scope: {payload['scope_read']}",
        f"- included claim classes: `{', '.join(payload['included_claim_classes'])}`",
        f"- included claims: `{', '.join(payload['claimable_now'])}`",
        f"- reviewer summary ref: `{payload['reviewer_summary_ref']}`",
        f"- invalidity summary ref: `{payload['invalidity_summary_ref']}`",
        "",
        "## Replay Read",
        "",
        f"- repo_swe: {payload['replay_read']['repo_swe']}",
        f"- scheduling: {payload['replay_read']['scheduling']}",
        f"- research transfer: {payload['replay_read']['research_transfer']}",
        f"- atp: {payload['replay_read']['atp']}",
        "",
        "## Cost Caution",
        "",
        f"- exact classes: `{', '.join(payload['cost_caution']['exact'])}`",
        f"- estimated classes: `{', '.join(payload['cost_caution']['estimated'])}`",
        f"- unavailable classes: `{', '.join(payload['cost_caution']['unavailable'])}`",
        f"- rule: {payload['cost_caution']['rule']}",
        "",
        "## Do Not Infer",
        "",
    ]
    for row in payload["do_not_infer"]:
        lines.append(f"- {row}")
    lines.extend(["", "## Lane Read", ""])
    for row in payload["lane_rows"]:
        lines.extend([
            f"### {row['lane_id']}",
            "",
            f"- role: `{row['role']}`",
            f"- baseline: {row['baseline_story']}",
            f"- changed: {row['what_changed']}",
            f"- do not infer: {row['do_not_infer']}",
            "",
        ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "included_claim_count": payload["included_claim_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN external-safe evidence memo.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_external_safe_memo()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"external_safe_evidence_memo={summary['json_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
