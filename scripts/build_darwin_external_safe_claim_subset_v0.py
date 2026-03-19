from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAIM_LEDGER = ROOT / "artifacts" / "darwin" / "claims" / "claim_ledger_v1.json"
CLAIMS_DIR = ROOT / "artifacts" / "darwin" / "claims"
OUT_PATH = CLAIMS_DIR / "external_safe_claim_subset_v0.json"
POLICY_REF = "docs/contracts/darwin/DARWIN_EXTERNAL_SAFE_EVIDENCE_POLICY_V0.md"

INCLUDED = {
    "claim.darwin.phase1.six_lane_live_baseline.v1": "operational_substrate",
    "claim.darwin.phase1.typed_search_core_operational.v1": "operational_substrate",
    "claim.darwin.phase1.replay_audit_complete.v1": "reproducibility",
    "claim.darwin.phase1.compute_normalized_companion_complete.v1": "bounded_comparative_clarity",
    "claim.darwin.phase1.transfer_protocol_operational.v1": "bounded_transfer_protocol",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _claim_payload(claim_id: str, ledger_row: dict) -> dict:
    claim_path = CLAIMS_DIR / f"{claim_id}.json"
    if claim_path.exists():
        payload = _load_json(claim_path)
        payload["_source_path"] = str(claim_path.relative_to(ROOT))
        return payload
    return {
        "claim_id": claim_id,
        "summary": ledger_row["title"],
        "evidence_bundle_id": ledger_row["evidence_bundle_id"],
        "claim_target": "internal",
        "claim_tier": "t1",
        "scope": ledger_row.get("notes") or ledger_row["title"],
        "_source_path": str((CLAIMS_DIR / "claim_ledger_v1.json").relative_to(ROOT)),
    }


def _caution_labels(claim_class: str) -> list[str]:
    labels = [
        "internal_artifacts_repackaged_for_external_review",
        "bounded_lane_set",
        "additive_first",
        "replay_conditioned",
        "invalid_comparisons_disclosed",
        "local_cost_accounting_partial",
        "no_superiority_claim",
    ]
    if claim_class == "bounded_transfer_protocol":
        labels.append("descriptive_transfer_only")
    return labels


def _exclude_reason(claim_id: str) -> str:
    if ".promotion_cycle." in claim_id:
        return "promotion_path_claims_remain_internal_only"
    if ".search_smoke." in claim_id:
        return "search_smoke_claims_not_external_safe"
    if claim_id == "claim.darwin.phase1.final_program_operational.v1":
        return "aggregate_closure_claim_is_internal_only"
    if ".live_baseline." in claim_id or claim_id == "claim.darwin.phase1.three_lane_live_baseline.v1":
        return "lane_local_readiness_claims_are_supporting_only"
    return "not_selected_for_external_safe_subset"


def build_external_safe_claim_subset() -> dict:
    ledger = _load_json(CLAIM_LEDGER)
    included_claims = []
    excluded_claims = []
    for row in ledger.get("claims") or []:
        claim_id = row["claim_id"]
        payload = _claim_payload(claim_id, row)
        if claim_id in INCLUDED:
            claim_class = INCLUDED[claim_id]
            included_claims.append(
                {
                    "source_claim_id": claim_id,
                    "external_safe_class": claim_class,
                    "evidence_bundle_id": payload["evidence_bundle_id"],
                    "title": payload["summary"],
                    "scope": payload["scope"],
                    "source_path": payload["_source_path"],
                    "claim_target": payload.get("claim_target", "internal"),
                    "claim_tier": payload.get("claim_tier", "t1"),
                    "caution_labels": _caution_labels(claim_class),
                    "review_status": "included",
                    "rationale": "allowed under external-safe evidence policy as a bounded claim class",
                }
            )
        else:
            excluded_claims.append(
                {
                    "source_claim_id": claim_id,
                    "title": payload["summary"],
                    "source_path": payload["_source_path"],
                    "review_status": "excluded",
                    "reason": _exclude_reason(claim_id),
                }
            )
    return {
        "schema": "breadboard.darwin.external_safe_claim_subset.v0",
        "generated_at": _now(),
        "policy_ref": POLICY_REF,
        "included_count": len(included_claims),
        "excluded_count": len(excluded_claims),
        "included_claims": included_claims,
        "excluded_claims": excluded_claims,
    }


def write_external_safe_claim_subset(out_path: Path = OUT_PATH) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_external_safe_claim_subset()
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "included_count": payload["included_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN external-safe claim subset.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_external_safe_claim_subset()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"external_safe_claim_subset={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
