from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import validate_claim_record, validate_evidence_bundle


SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
ARCHIVE_SNAPSHOT = ROOT / "artifacts" / "darwin" / "search" / "archive_snapshot_v1.json"
INVALID_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"
PROMOTION_DECISIONS = ROOT / "artifacts" / "darwin" / "search" / "promotion_decisions_v1.json"
REPLAY_AUDIT = ROOT / "artifacts" / "darwin" / "search" / "promotion_replay_audit_v1.json"
SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
WEEKLY = ROOT / "artifacts" / "darwin" / "weekly" / "weekly_evidence_packet.latest.json"
CLAIMS_LEDGER = ROOT / "artifacts" / "darwin" / "claims" / "claim_ledger_v1.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def emit_search_evidence() -> dict:
    search_summary = _load_json(SEARCH_SUMMARY)
    _ = _load_json(SCORECARD)
    bundle = {
        "schema": "breadboard.darwin.evidence_bundle.v0",
        "evidence_bundle_id": "bundle.darwin.phase1.t2.promotion_cycles.v1",
        "campaign_id": "camp.darwin.phase1.multi_lane.promotion_cycles",
        "bundle_tier": "t1",
        "run_manifest_ref": str(SEARCH_SUMMARY.relative_to(ROOT)),
        "result_table_refs": [
            str(SCORECARD.relative_to(ROOT)),
            str(WEEKLY.relative_to(ROOT)),
            str(ARCHIVE_SNAPSHOT.relative_to(ROOT)),
            str(PROMOTION_DECISIONS.relative_to(ROOT)),
            str(REPLAY_AUDIT.relative_to(ROOT)),
            str(INVALID_LEDGER.relative_to(ROOT)),
        ],
        "benchmark_slice_digest": hashlib.sha256(json.dumps(search_summary, sort_keys=True).encode("utf-8")).hexdigest(),
        "environment_digests": ["sha256:darwin-phase1-promotion-cycles-env"],
        "ablation_refs": [],
        "perturbation_refs": [str(SEARCH_SUMMARY.relative_to(ROOT))],
        "statistical_summary_ref": str(SCORECARD.relative_to(ROOT)),
        "contamination_audit_ref": str(INVALID_LEDGER.relative_to(ROOT)),
        "reproduction_instructions_ref": "scripts/run_darwin_phase1_orchestration_v1.py",
        "reviewer_signoffs": [{"reviewer": "darwin.internal", "status": "approved"}],
        "limitations_memo_ref": "docs/darwin_phase1_repo_swe_search_status_2026-03-14.md",
    }
    issues = validate_evidence_bundle(bundle)
    if issues:
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))

    evidence_dir = ROOT / "artifacts" / "darwin" / "evidence"
    bundle_path = evidence_dir / "darwin_phase1_t2_search_bundle_v1.json"
    _write_json(bundle_path, bundle)

    claim_records = [
        {
            "schema": "breadboard.darwin.claim_record.v0",
            "claim_id": "claim.darwin.phase1.typed_search_core_operational.v1",
            "evidence_bundle_id": bundle["evidence_bundle_id"],
            "claim_target": "internal",
            "claim_tier": "t1",
            "scope": "DARWIN typed search core operational on selected lanes",
            "status": "approved",
            "summary": "Mutation registry, archive snapshot, promotion decisions, budget enforcement, and comparative scorecard are live for the initial DARWIN promotion cycles.",
            "confidence_statement": "Internal operational claim only; no superiority claim.",
            "limitations_ref": "docs/contracts/darwin/DARWIN_CLAIM_LADDER_V0.md",
            "approved_by": ["darwin.internal"],
        }
    ]
    for row in search_summary.get("lanes") or []:
        claim_records.append(
            {
                "schema": "breadboard.darwin.claim_record.v0",
                "claim_id": f"claim.darwin.phase1.{row['lane_id']}.promotion_cycle.v1",
                "evidence_bundle_id": bundle["evidence_bundle_id"],
                "claim_target": "internal",
                "claim_tier": "t1",
                "scope": f"{row['lane_id']} typed-search promotion cycle",
                "status": "approved",
                "summary": f"{row['lane_id']} emitted baseline-vs-mutation comparative DARWIN artifacts with promotion status {row.get('promotion_status')}.",
                "confidence_statement": "Internal operational claim only; promotion claims require replay and remain non-superiority claims.",
                "limitations_ref": "docs/contracts/darwin/DARWIN_CLAIM_LADDER_V0.md",
                "approved_by": ["darwin.internal"],
            }
        )
    claim_records.append(
        {
            "schema": "breadboard.darwin.claim_record.v0",
            "claim_id": "claim.darwin.phase1.promotion_control_operational.v1",
            "evidence_bundle_id": bundle["evidence_bundle_id"],
            "claim_target": "internal",
            "claim_tier": "t1",
            "scope": "DARWIN promotion control operational",
            "status": "approved",
            "summary": "Promotion decisions, rollback targets, and replay audit are operational for the current DARWIN tranche.",
            "confidence_statement": "Operational control-path claim only.",
            "limitations_ref": "docs/contracts/darwin/DARWIN_PROMOTION_CONTROL_V1.md",
            "approved_by": ["darwin.internal"],
        }
    )
    for claim in claim_records:
        issues = validate_claim_record(claim)
        if issues:
            raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))

    claims_dir = ROOT / "artifacts" / "darwin" / "claims"
    for claim in claim_records:
        _write_json(claims_dir / f"{claim['claim_id']}.json", claim)

    ledger = _load_json(CLAIMS_LEDGER)
    existing = {claim["claim_id"] for claim in ledger.get("claims") or []}
    for claim in claim_records:
        if claim["claim_id"] in existing:
            continue
        ledger["claims"].append(
            {
                "claim_id": claim["claim_id"],
                "title": claim["summary"],
                "status": "accepted",
                "tier": "tier1_replay",
                "evidence_bundle_id": claim["evidence_bundle_id"],
                "owner": "darwin.internal",
                "last_updated": _now(),
                "notes": claim["scope"],
            }
        )
    claim_schema = _load_json(ROOT / "docs" / "contracts" / "benchmarks" / "schemas" / "claim_ledger_v1.schema.json")
    Draft202012Validator(claim_schema).validate(ledger)
    _write_json(CLAIMS_LEDGER, ledger)

    manifest = {
        "schema_id": "breadboard.evidence_bundle_manifest.v2",
        "bundle_id": bundle["evidence_bundle_id"],
        "created_at": _now(),
        "claim_ids": [claim["claim_id"] for claim in claim_records],
        "tier": "tier1_replay",
        "reproduction": {
            "commands": ["python scripts/run_darwin_phase1_orchestration_v1.py --json"],
            "expected_outputs": [
                str(SEARCH_SUMMARY.relative_to(ROOT)),
                str(ARCHIVE_SNAPSHOT.relative_to(ROOT)),
                str(INVALID_LEDGER.relative_to(ROOT)),
                str(bundle_path.relative_to(ROOT)),
            ],
        },
        "artifacts": [
            {
                "artifact_id": "search_smoke_summary",
                "path": str(SEARCH_SUMMARY.relative_to(ROOT)),
                "sha256": _sha256_file(SEARCH_SUMMARY),
                "mime": "application/json",
                "size_bytes": SEARCH_SUMMARY.stat().st_size,
            },
            {
                "artifact_id": "archive_snapshot",
                "path": str(ARCHIVE_SNAPSHOT.relative_to(ROOT)),
                "sha256": _sha256_file(ARCHIVE_SNAPSHOT),
                "mime": "application/json",
                "size_bytes": ARCHIVE_SNAPSHOT.stat().st_size,
            },
            {
                "artifact_id": "promotion_decisions",
                "path": str(PROMOTION_DECISIONS.relative_to(ROOT)),
                "sha256": _sha256_file(PROMOTION_DECISIONS),
                "mime": "application/json",
                "size_bytes": PROMOTION_DECISIONS.stat().st_size,
            },
            {
                "artifact_id": "promotion_replay_audit",
                "path": str(REPLAY_AUDIT.relative_to(ROOT)),
                "sha256": _sha256_file(REPLAY_AUDIT),
                "mime": "application/json",
                "size_bytes": REPLAY_AUDIT.stat().st_size,
            },
            {
                "artifact_id": "search_evidence_bundle",
                "path": str(bundle_path.relative_to(ROOT)),
                "sha256": _sha256_file(bundle_path),
                "mime": "application/json",
                "size_bytes": bundle_path.stat().st_size,
            },
        ],
    }
    manifest_schema = _load_json(ROOT / "docs" / "contracts" / "benchmarks" / "schemas" / "evidence_bundle_manifest_v2.schema.json")
    Draft202012Validator(manifest_schema).validate(manifest)
    manifest_path = evidence_dir / "search_evidence_bundle_manifest_v2.json"
    _write_json(manifest_path, manifest)
    return {
        "bundle_path": str(bundle_path),
        "claim_count": len(claim_records),
        "manifest_path": str(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit DARWIN search-smoke evidence and claims.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = emit_search_evidence()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"search_bundle={summary['bundle_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
