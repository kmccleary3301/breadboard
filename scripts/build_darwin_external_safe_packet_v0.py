from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAIM_SUBSET = ROOT / "artifacts" / "darwin" / "claims" / "external_safe_claim_subset_v0.json"
INVALIDITY = ROOT / "artifacts" / "darwin" / "reviews" / "external_safe_invalidity_summary_v0.json"
REVIEWER_SUMMARY = ROOT / "artifacts" / "darwin" / "reviews" / "external_safe_reviewer_summary_v0.json"
MEMO = ROOT / "artifacts" / "darwin" / "memos" / "external_safe_evidence_memo_v0.json"
OUT_PATH = ROOT / "artifacts" / "darwin" / "evidence" / "external_safe_evidence_packet_v0.json"
POLICY_REF = "docs/contracts/darwin/DARWIN_EXTERNAL_SAFE_EVIDENCE_POLICY_V0.md"
REPRO_REF = "docs/darwin_phase2_external_safe_reproduction_note_2026-03-19.md"
ARTIFACT_INDEX_REF = "docs/darwin_phase2_external_safe_artifact_index_2026-03-19.md"
REPLAY_NOTE_REF = "docs/darwin_phase2_external_safe_replay_note_2026-03-19.md"
COST_CAUTION_REF = "docs/darwin_phase2_external_safe_cost_caution_2026-03-19.md"

CANONICAL_ARTIFACTS = [
    "artifacts/darwin/scorecards/t1_baseline_scorecard.latest.json",
    "artifacts/darwin/scorecards/compute_normalized_view_v2.json",
    "artifacts/darwin/reviews/compute_normalized_review_v0.json",
    "artifacts/darwin/memos/compute_normalized_memo_v0.json",
    "artifacts/darwin/search/evolution_ledger_v0.json",
    "artifacts/darwin/reviews/transfer_family_view_v0.json",
    "artifacts/darwin/reviews/lineage_review_v0.json",
    "artifacts/darwin/reviews/transfer_lineage_proving_review_v0.json",
    "artifacts/darwin/search/invalid_comparison_ledger_v1.json",
    "artifacts/darwin/search/phase1_replay_audit_v1.json",
    "artifacts/darwin/claims/external_safe_claim_subset_v0.json",
    "artifacts/darwin/reviews/external_safe_invalidity_summary_v0.json",
    "artifacts/darwin/reviews/external_safe_reviewer_summary_v0.json",
    "artifacts/darwin/memos/external_safe_evidence_memo_v0.json",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_external_safe_packet() -> dict:
    claims = _load_json(CLAIM_SUBSET)
    invalidity = _load_json(INVALIDITY)
    reviewer = _load_json(REVIEWER_SUMMARY)
    memo = _load_json(MEMO)
    return {
        "schema": "breadboard.darwin.external_safe_packet.v0",
        "packet_id": "packet.darwin.phase2.external_safe.v0",
        "generated_at": _now(),
        "policy_ref": POLICY_REF,
        "artifact_index_ref": ARTIFACT_INDEX_REF,
        "reproduction_ref": REPRO_REF,
        "replay_note_ref": REPLAY_NOTE_REF,
        "cost_caution_ref": COST_CAUTION_REF,
        "reviewer_labels": memo["caution_labels"],
        "claim_subset_ref": str(CLAIM_SUBSET.relative_to(ROOT)),
        "invalidity_summary_ref": str(INVALIDITY.relative_to(ROOT)),
        "reviewer_summary_ref": str(REVIEWER_SUMMARY.relative_to(ROOT)),
        "memo_ref": str(MEMO.relative_to(ROOT)),
        "included_claim_ids": [row["source_claim_id"] for row in claims.get("included_claims") or []],
        "canonical_artifact_refs": CANONICAL_ARTIFACTS,
        "lane_scope": [row["lane_id"] for row in reviewer.get("rows") or []],
        "caveat_count": invalidity["caveat_count"],
        "bundle_read": "bounded external-safe comparative packet only",
    }


def write_external_safe_packet(out_path: Path = OUT_PATH) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_external_safe_packet()
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "claim_count": len(payload["included_claim_ids"]), "artifact_count": len(payload["canonical_artifact_refs"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN external-safe evidence packet.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_external_safe_packet()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"external_safe_packet={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
