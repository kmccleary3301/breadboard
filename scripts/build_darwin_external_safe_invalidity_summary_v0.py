from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVALID_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"
COMPUTE_REVIEW = ROOT / "artifacts" / "darwin" / "reviews" / "compute_normalized_review_v0.json"
TRANSFER_REVIEW = ROOT / "artifacts" / "darwin" / "reviews" / "transfer_lineage_proving_review_v0.json"
OUT_PATH = ROOT / "artifacts" / "darwin" / "reviews" / "external_safe_invalidity_summary_v0.json"
POLICY_REF = "docs/contracts/darwin/DARWIN_EXTERNAL_SAFE_EVIDENCE_POLICY_V0.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_external_safe_invalidity_summary() -> dict:
    invalid = _load_json(INVALID_LEDGER)
    compute = _load_json(COMPUTE_REVIEW)
    transfer = _load_json(TRANSFER_REVIEW)
    caveats = []
    for row in invalid.get("rows") or []:
        caveats.append(
            {
                "lane_id": row["lane_id"],
                "caveat_class": "invalid_comparison",
                "severity": "material",
                "summary": f"{row['candidate_id']} is excluded from claim-bearing comparison because {row['reason']}.",
                "source_ref": str(INVALID_LEDGER.relative_to(ROOT)),
            }
        )
    for row in compute.get("proving_rows") or []:
        if row.get("invalid_trial_count", 0) > 0:
            caveats.append(
                {
                    "lane_id": row["lane_id"],
                    "caveat_class": "invalid_trial_pressure",
                    "severity": "material",
                    "summary": row["review_read"],
                    "source_ref": str(COMPUTE_REVIEW.relative_to(ROOT)),
                }
            )
    for row in transfer.get("rows") or []:
        if row["lane_id"] == "lane.research":
            caveats.append(
                {
                    "lane_id": row["lane_id"],
                    "caveat_class": "descriptive_only_transfer",
                    "severity": "caution",
                    "summary": "The bounded harness→research transfer remains replay-stable but must be read as bounded internal evidence rather than a broad transfer claim.",
                    "source_ref": str(TRANSFER_REVIEW.relative_to(ROOT)),
                }
            )
        if row["lane_id"] == "lane.atp":
            caveats.append(
                {
                    "lane_id": row["lane_id"],
                    "caveat_class": "audit_only_lane",
                    "severity": "caution",
                    "summary": row["review_read"],
                    "source_ref": str(TRANSFER_REVIEW.relative_to(ROOT)),
                }
            )
    return {
        "schema": "breadboard.darwin.external_safe_invalidity_summary.v0",
        "generated_at": _now(),
        "policy_ref": POLICY_REF,
        "caveat_count": len(caveats),
        "caveats": caveats,
    }


def write_external_safe_invalidity_summary(out_path: Path = OUT_PATH) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_external_safe_invalidity_summary()
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "caveat_count": payload["caveat_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN external-safe invalidity summary.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_external_safe_invalidity_summary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"external_safe_invalidity_summary={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
