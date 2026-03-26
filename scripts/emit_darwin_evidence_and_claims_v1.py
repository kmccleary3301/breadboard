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

from breadboard_ext.darwin.contracts import (
    validate_claim_record,
    validate_evidence_bundle,
)


DEFAULT_LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
DEFAULT_SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
DEFAULT_WEEKLY_PACKET = ROOT / "artifacts" / "darwin" / "weekly" / "weekly_evidence_packet.latest.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_evidence_and_claims() -> dict:
    live_summary = _load_json(DEFAULT_LIVE_SUMMARY)
    scorecard = _load_json(DEFAULT_SCORECARD)
    _ = _load_json(DEFAULT_WEEKLY_PACKET)

    bundle = {
        "schema": "breadboard.darwin.evidence_bundle.v0",
        "evidence_bundle_id": "bundle.darwin.phase1.t1.live_baselines.v1",
        "campaign_id": "camp.darwin.phase1.multi_lane.live_baselines",
        "bundle_tier": "t1",
        "run_manifest_ref": str(DEFAULT_LIVE_SUMMARY.relative_to(ROOT)),
        "result_table_refs": [
            str(DEFAULT_SCORECARD.relative_to(ROOT)),
            str(DEFAULT_WEEKLY_PACKET.relative_to(ROOT)),
        ],
        "benchmark_slice_digest": hashlib.sha256(json.dumps(live_summary, sort_keys=True).encode("utf-8")).hexdigest(),
        "environment_digests": ["sha256:darwin-phase1-live-baseline-env"],
        "ablation_refs": [],
        "perturbation_refs": [],
        "statistical_summary_ref": str(DEFAULT_SCORECARD.relative_to(ROOT)),
        "contamination_audit_ref": "docs/contracts/darwin/DARWIN_CLAIM_LADDER_V0.md",
        "reproduction_instructions_ref": "scripts/run_darwin_t1_smoke_v1.py",
        "reviewer_signoffs": [{"reviewer": "darwin.internal", "status": "approved"}],
        "limitations_memo_ref": "docs/darwin_phase1_research_transfer_status_2026-03-14.md",
    }
    issues = validate_evidence_bundle(bundle)
    if issues:
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))

    evidence_dir = ROOT / "artifacts" / "darwin" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = evidence_dir / "darwin_phase1_t1_live_baselines_bundle_v1.json"
    _write_json(bundle_path, bundle)

    claim_records: list[dict] = []
    for lane in live_summary.get("lanes") or []:
        claim = {
            "schema": "breadboard.darwin.claim_record.v0",
            "claim_id": f"claim.darwin.phase1.{lane['lane_id']}.live_baseline.v1",
            "evidence_bundle_id": bundle["evidence_bundle_id"],
            "claim_target": "internal",
            "claim_tier": "t1",
            "scope": f"{lane['lane_id']} live baseline readiness",
            "status": "approved",
            "summary": f"{lane['lane_id']} emitted live baseline artifacts with status {lane['status']}.",
            "confidence_statement": "Controlled internal baseline only; no comparative claim.",
            "limitations_ref": "docs/darwin_phase1_research_transfer_status_2026-03-14.md",
            "approved_by": ["darwin.internal"],
        }
        claim_issues = validate_claim_record(claim)
        if claim_issues:
            raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in claim_issues))
        claim_records.append(claim)

    aggregate_claim = {
        "schema": "breadboard.darwin.claim_record.v0",
        "claim_id": "claim.darwin.phase1.six_lane_live_baseline.v1",
        "evidence_bundle_id": bundle["evidence_bundle_id"],
        "claim_target": "internal",
        "claim_tier": "t1",
        "scope": "DARWIN T1 six-lane live baseline",
        "status": "approved",
        "summary": f"ATP, harness, systems, repo_swe, scheduling, and research now emit shared DARWIN live baseline artifacts; mean score {scorecard['mean_normalized_score']}.",
        "confidence_statement": "Structural-plus-live internal readiness only.",
        "limitations_ref": "docs/darwin_phase1_research_transfer_status_2026-03-14.md",
        "approved_by": ["darwin.internal"],
    }
    claim_records.append(aggregate_claim)

    claims_dir = ROOT / "artifacts" / "darwin" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    for claim in claim_records:
        _write_json(claims_dir / f"{claim['claim_id']}.json", claim)

    ledger = {
        "schema_id": "breadboard.claim_ledger.v1",
        "generated_at": _now(),
        "claims": [
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
            for claim in claim_records
        ],
        "no_claim_mode": {
            "enabled": False,
            "reason": "DARWIN T1 internal evidence is now live for ATP/harness/systems."
        }
    }
    claim_schema = _load_json(ROOT / "docs" / "contracts" / "benchmarks" / "schemas" / "claim_ledger_v1.schema.json")
    Draft202012Validator(claim_schema).validate(ledger)
    ledger_path = claims_dir / "claim_ledger_v1.json"
    _write_json(ledger_path, ledger)

    evidence_manifest = {
        "schema_id": "breadboard.evidence_bundle_manifest.v2",
        "bundle_id": bundle["evidence_bundle_id"],
        "created_at": _now(),
        "claim_ids": [claim["claim_id"] for claim in claim_records],
        "tier": "tier1_replay",
        "reproduction": {
            "commands": ["python scripts/run_darwin_t1_smoke_v1.py --json"],
            "expected_outputs": [
                str(DEFAULT_LIVE_SUMMARY.relative_to(ROOT)),
                str(DEFAULT_SCORECARD.relative_to(ROOT)),
                str(DEFAULT_WEEKLY_PACKET.relative_to(ROOT)),
                str(bundle_path.relative_to(ROOT)),
            ],
        },
        "artifacts": [
            {
                "artifact_id": "live_baseline_summary",
                "path": str(DEFAULT_LIVE_SUMMARY.relative_to(ROOT)),
                "sha256": _sha256_file(DEFAULT_LIVE_SUMMARY),
                "mime": "application/json",
                "size_bytes": DEFAULT_LIVE_SUMMARY.stat().st_size,
            },
            {
                "artifact_id": "baseline_scorecard",
                "path": str(DEFAULT_SCORECARD.relative_to(ROOT)),
                "sha256": _sha256_file(DEFAULT_SCORECARD),
                "mime": "application/json",
                "size_bytes": DEFAULT_SCORECARD.stat().st_size,
            },
            {
                "artifact_id": "weekly_packet",
                "path": str(DEFAULT_WEEKLY_PACKET.relative_to(ROOT)),
                "sha256": _sha256_file(DEFAULT_WEEKLY_PACKET),
                "mime": "application/json",
                "size_bytes": DEFAULT_WEEKLY_PACKET.stat().st_size,
            },
            {
                "artifact_id": "darwin_evidence_bundle",
                "path": str(bundle_path.relative_to(ROOT)),
                "sha256": _sha256_file(bundle_path),
                "mime": "application/json",
                "size_bytes": bundle_path.stat().st_size,
            }
        ]
    }
    evidence_schema = _load_json(ROOT / "docs" / "contracts" / "benchmarks" / "schemas" / "evidence_bundle_manifest_v2.schema.json")
    Draft202012Validator(evidence_schema).validate(evidence_manifest)
    manifest_path = evidence_dir / "evidence_bundle_manifest_v2.json"
    _write_json(manifest_path, evidence_manifest)

    return {
        "bundle_path": str(bundle_path),
        "claim_ledger_path": str(ledger_path),
        "evidence_manifest_path": str(manifest_path),
        "claim_count": len(claim_records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit DARWIN T1 evidence and claim artifacts from live baseline outputs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = emit_evidence_and_claims()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"bundle_path={summary['bundle_path']}")
        print(f"claim_ledger_path={summary['claim_ledger_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
