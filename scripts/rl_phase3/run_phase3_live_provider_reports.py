from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase3.evidence import sha256_file, write_phase3_json
from breadboard.rl.phase3.integrations import HARBOR_BLOCKED_CLAIM_BOUNDARY, HARBOR_CLAIM_BOUNDARY, run_harbor_service_proof


def _component(*, milestone_id: str, component: str, points: int, claim_boundary: str, blocked_claim_boundary: str, target_run_id: str, provider_report_path: Path, provider_report: dict) -> dict:
    passed = provider_report.get("passed") is True
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": f"{milestone_id.lower()}_{component}",
        "milestone_id": milestone_id,
        "component": component,
        "claim_boundary": claim_boundary if passed else blocked_claim_boundary,
        "target_run_id": target_run_id,
        "points": points,
        "passed": passed,
        "blocked_reason": "" if passed else str(provider_report.get("blocked_reason") or "provider_report_failed"),
        "provider_report": provider_report,
        "provider_kind": provider_report.get("provider_kind") or provider_report.get("attestation_backend"),
        "input_hashes": {"provider_report": sha256_file(provider_report_path)},
        "artifact_paths": {"provider_report": str(provider_report_path)},
        "required_artifact_keys": ["provider_report"],
        "scorecard_update_allowed": False,
    }
    return report




def _p3m8_retired_provider_report(*, target_run_id: str) -> dict:
    return {
        "schema_version": "bb.rl.phase3.retired_provider_milestone.v1",
        "report_id": "phase3_p3m8_retired_provider_milestone",
        "claim_boundary": "phase3_retired_provider_milestone_pending_rubric_change_scope",
        "target_run_id": target_run_id,
        "provider_kind": "none",
        "blocked_reason": "retired_provider_milestone_pending_accepted_rubric_change",
        "scorecard_update_allowed": False,
        "passed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--rows-jsonl", type=Path)
    parser.add_argument("--env-package", type=Path)
    args = parser.parse_args()
    out = args.phase_dir / "runs" / "live_provider_reports"
    out.mkdir(parents=True, exist_ok=True)
    retired_p3m8 = _p3m8_retired_provider_report(target_run_id=args.target_run_id)
    retired_p3m8_path = out / "p3m8_retired_provider_milestone.json"
    write_phase3_json(retired_p3m8_path, retired_p3m8)
    env_package = args.env_package or out / "harbor_env_package_required.tar"
    harbor = run_harbor_service_proof(env_package, target_run_id=args.target_run_id)
    harbor_path = out / "harbor_service_proof.json"
    write_phase3_json(harbor_path, harbor)
    p3m8 = _component(
        milestone_id="P3-M8",
        component="retired_provider_milestone",
        points=70,
        claim_boundary="phase3_retired_provider_milestone_pending_rubric_change_scope",
        blocked_claim_boundary="phase3_retired_provider_milestone_pending_rubric_change_scope",
        target_run_id=args.target_run_id,
        provider_report_path=retired_p3m8_path,
        provider_report=retired_p3m8,
    )
    p3m9 = _component(
        milestone_id="P3-M9",
        component="harbor_nemo_gym",
        points=60,
        claim_boundary=HARBOR_CLAIM_BOUNDARY,
        blocked_claim_boundary=HARBOR_BLOCKED_CLAIM_BOUNDARY,
        target_run_id=args.target_run_id,
        provider_report_path=harbor_path,
        provider_report=harbor,
    )
    write_phase3_json(out / "P3-M8_retired_provider_milestone.json", p3m8)
    write_phase3_json(out / "P3-M9_harbor_nemo_gym.json", p3m9)
    print(json.dumps({"P3-M8": p3m8["passed"], "P3-M9": p3m9["passed"], "output_dir": str(out)}, sort_keys=True))
    return 0 if p3m8["passed"] and p3m9["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
