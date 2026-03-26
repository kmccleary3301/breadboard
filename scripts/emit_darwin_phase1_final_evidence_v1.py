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


ROLLUP = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_rollup_v0.json"
SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
COMPUTE_VIEW = ROOT / "artifacts" / "darwin" / "scorecards" / "compute_normalized_view_v1.json"
WEEKLY = ROOT / "artifacts" / "darwin" / "weekly" / "weekly_evidence_packet.latest.json"
DOSSIER = ROOT / "artifacts" / "darwin" / "dossiers" / "comparative_dossier_v1.json"
PROMOTION_HISTORY = ROOT / "artifacts" / "darwin" / "search" / "promotion_history_v1.json"
TRANSFER_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "transfer_ledger_v1.json"
REPLAY_AUDIT = ROOT / "artifacts" / "darwin" / "search" / "phase1_replay_audit_v1.json"
INVALID_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"
BASELINE_BUNDLE = ROOT / "artifacts" / "darwin" / "evidence" / "darwin_phase1_t1_live_baselines_bundle_v1.json"
SEARCH_BUNDLE = ROOT / "artifacts" / "darwin" / "evidence" / "darwin_phase1_t2_search_bundle_v1.json"
CLAIMS_LEDGER = ROOT / "artifacts" / "darwin" / "claims" / "claim_ledger_v1.json"
EVIDENCE_DIR = ROOT / "artifacts" / "darwin" / "evidence"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_final_evidence() -> dict:
    rollup = _load_json(ROLLUP)
    replay = _load_json(REPLAY_AUDIT)
    bundle = {
        "schema": "breadboard.darwin.evidence_bundle.v0",
        "evidence_bundle_id": "bundle.darwin.phase1.final_program_closure.v1",
        "campaign_id": "camp.darwin.phase1.program_closure",
        "bundle_tier": "t2",
        "run_manifest_ref": str(ROLLUP.relative_to(ROOT)),
        "result_table_refs": [
            str(SCORECARD.relative_to(ROOT)),
            str(COMPUTE_VIEW.relative_to(ROOT)),
            str(WEEKLY.relative_to(ROOT)),
            str(DOSSIER.relative_to(ROOT)),
            str(PROMOTION_HISTORY.relative_to(ROOT)),
            str(TRANSFER_LEDGER.relative_to(ROOT)),
            str(REPLAY_AUDIT.relative_to(ROOT)),
            str(INVALID_LEDGER.relative_to(ROOT)),
            str(BASELINE_BUNDLE.relative_to(ROOT)),
            str(SEARCH_BUNDLE.relative_to(ROOT)),
        ],
        "benchmark_slice_digest": hashlib.sha256(json.dumps(rollup, sort_keys=True).encode("utf-8")).hexdigest(),
        "environment_digests": ["sha256:darwin-phase1-final-closure-env"],
        "ablation_refs": [],
        "perturbation_refs": [str(PROMOTION_HISTORY.relative_to(ROOT)), str(TRANSFER_LEDGER.relative_to(ROOT))],
        "statistical_summary_ref": str(SCORECARD.relative_to(ROOT)),
        "contamination_audit_ref": str(INVALID_LEDGER.relative_to(ROOT)),
        "reproduction_instructions_ref": "scripts/run_darwin_phase1_orchestration_v1.py",
        "reviewer_signoffs": [{"reviewer": "darwin.internal", "status": "approved"}],
        "limitations_memo_ref": "docs/darwin_phase1_signoff_2026-03-14.md",
    }
    issues = validate_evidence_bundle(bundle)
    if issues:
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))

    bundle_path = EVIDENCE_DIR / "darwin_phase1_final_program_bundle_v1.json"
    _write_json(bundle_path, bundle)

    claim_records = [
        {
            "schema": "breadboard.darwin.claim_record.v0",
            "claim_id": "claim.darwin.phase1.final_program_operational.v1",
            "evidence_bundle_id": bundle["evidence_bundle_id"],
            "claim_target": "internal",
            "claim_tier": "t2",
            "scope": "DARWIN Phase-1 program operational closure",
            "status": "approved",
            "summary": "DARWIN Phase-1 closed with six live lanes, four search-enabled lanes, promotion history, transfer protocol, and final dossier artifacts.",
            "confidence_statement": "Internal comparative closure claim only.",
            "limitations_ref": "docs/darwin_phase1_signoff_2026-03-14.md",
            "approved_by": ["darwin.internal"],
        },
        {
            "schema": "breadboard.darwin.claim_record.v0",
            "claim_id": "claim.darwin.phase1.compute_normalized_companion_complete.v1",
            "evidence_bundle_id": bundle["evidence_bundle_id"],
            "claim_target": "internal",
            "claim_tier": "t2",
            "scope": "DARWIN compute-normalized comparative companion complete",
            "status": "approved",
            "summary": "A compute-normalized companion view is emitted for all active Phase-1 lanes with baseline-versus-active candidate accounting.",
            "confidence_statement": "Internal comparative support claim only; not a superiority claim.",
            "limitations_ref": "docs/darwin_phase1_signoff_2026-03-14.md",
            "approved_by": ["darwin.internal"],
        },
        {
            "schema": "breadboard.darwin.claim_record.v0",
            "claim_id": "claim.darwin.phase1.replay_audit_complete.v1",
            "evidence_bundle_id": bundle["evidence_bundle_id"],
            "claim_target": "internal",
            "claim_tier": "t2",
            "scope": "DARWIN Phase-1 replay audit complete",
            "status": "approved",
            "summary": f"Final replay audit completed with {replay['audit_count']} audited paths and all_stable={str(replay['all_stable']).lower()}.",
            "confidence_statement": "Internal replay-completeness claim only.",
            "limitations_ref": "docs/darwin_phase1_signoff_2026-03-14.md",
            "approved_by": ["darwin.internal"],
        },
    ]
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
        if claim["claim_id"] not in existing:
            ledger["claims"].append(
                {
                    "claim_id": claim["claim_id"],
                    "title": claim["summary"],
                    "status": "accepted",
                    "tier": "tier2_statistical",
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
        "tier": "tier2_statistical",
        "reproduction": {
            "commands": [
                "python scripts/run_darwin_phase1_orchestration_v1.py --json",
                "python scripts/run_darwin_phase1_replay_audit_v1.py --json",
            ],
            "expected_outputs": [
                str(ROLLUP.relative_to(ROOT)),
                str(SCORECARD.relative_to(ROOT)),
                str(COMPUTE_VIEW.relative_to(ROOT)),
                str(DOSSIER.relative_to(ROOT)),
                str(REPLAY_AUDIT.relative_to(ROOT)),
                str(bundle_path.relative_to(ROOT)),
            ],
        },
        "artifacts": [
            {"artifact_id": "rollup", "path": str(ROLLUP.relative_to(ROOT)), "sha256": _sha256_file(ROLLUP), "mime": "application/json", "size_bytes": ROLLUP.stat().st_size},
            {"artifact_id": "scorecard", "path": str(SCORECARD.relative_to(ROOT)), "sha256": _sha256_file(SCORECARD), "mime": "application/json", "size_bytes": SCORECARD.stat().st_size},
            {"artifact_id": "compute_view", "path": str(COMPUTE_VIEW.relative_to(ROOT)), "sha256": _sha256_file(COMPUTE_VIEW), "mime": "application/json", "size_bytes": COMPUTE_VIEW.stat().st_size},
            {"artifact_id": "weekly_packet", "path": str(WEEKLY.relative_to(ROOT)), "sha256": _sha256_file(WEEKLY), "mime": "application/json", "size_bytes": WEEKLY.stat().st_size},
            {"artifact_id": "comparative_dossier", "path": str(DOSSIER.relative_to(ROOT)), "sha256": _sha256_file(DOSSIER), "mime": "application/json", "size_bytes": DOSSIER.stat().st_size},
            {"artifact_id": "promotion_history", "path": str(PROMOTION_HISTORY.relative_to(ROOT)), "sha256": _sha256_file(PROMOTION_HISTORY), "mime": "application/json", "size_bytes": PROMOTION_HISTORY.stat().st_size},
            {"artifact_id": "transfer_ledger", "path": str(TRANSFER_LEDGER.relative_to(ROOT)), "sha256": _sha256_file(TRANSFER_LEDGER), "mime": "application/json", "size_bytes": TRANSFER_LEDGER.stat().st_size},
            {"artifact_id": "phase1_replay_audit", "path": str(REPLAY_AUDIT.relative_to(ROOT)), "sha256": _sha256_file(REPLAY_AUDIT), "mime": "application/json", "size_bytes": REPLAY_AUDIT.stat().st_size},
            {"artifact_id": "final_program_bundle", "path": str(bundle_path.relative_to(ROOT)), "sha256": _sha256_file(bundle_path), "mime": "application/json", "size_bytes": bundle_path.stat().st_size},
        ],
    }
    manifest_schema = _load_json(ROOT / "docs" / "contracts" / "benchmarks" / "schemas" / "evidence_bundle_manifest_v2.schema.json")
    Draft202012Validator(manifest_schema).validate(manifest)
    manifest_path = EVIDENCE_DIR / "darwin_phase1_final_evidence_manifest_v1.json"
    _write_json(manifest_path, manifest)
    return {"bundle_path": str(bundle_path), "manifest_path": str(manifest_path), "claim_count": len(claim_records)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the final DARWIN Phase-1 evidence and closure claims.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = emit_final_evidence()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"final_evidence_bundle={summary['bundle_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
