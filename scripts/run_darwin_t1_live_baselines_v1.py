from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import (
    validate_candidate_artifact,
    validate_evaluation_record,
)


DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "live_baselines"
DEFAULT_BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"


LANE_COMMANDS = {
    "lane.atp": {
        "command": [
            sys.executable,
            "scripts/atp_ops_digest.py",
            "--out",
            "artifacts/darwin/live_baselines/lane.atp/atp_ops_digest.json",
        ],
        "kind": "json_overall_ok",
        "result_path": "artifacts/darwin/live_baselines/lane.atp/atp_ops_digest.json",
        "task_id": "task.darwin.atp.ops_digest",
    },
    "lane.harness": {
        "command": [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_extension_spine.py",
            "tests/test_parity_runner.py",
        ],
        "kind": "pytest_pass_ratio",
        "result_path": None,
        "task_id": "task.darwin.harness.parity_smoke",
    },
    "lane.systems": {
        "command": [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_reward_v1_aggregator.py",
            "tests/test_reward_metrics_recorder.py",
        ],
        "kind": "pytest_pass_ratio",
        "result_path": None,
        "task_id": "task.darwin.systems.reward_smoke",
    },
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_pytest_pass_ratio(stdout: str) -> tuple[float, dict]:
    passed_match = re.search(r"(\d+)\s+passed", stdout)
    failed_match = re.search(r"(\d+)\s+failed", stdout)
    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    total = passed + failed
    ratio = float(passed / total) if total else 0.0
    return ratio, {"passed": passed, "failed": failed, "total": total}


def _campaign_lookup() -> dict[str, dict]:
    manifest = _load_json(DEFAULT_BOOTSTRAP_MANIFEST)
    rows: dict[str, dict] = {}
    for row in manifest.get("specs") or []:
        spec = _load_json(ROOT / row["path"])
        rows[spec["lane_id"]] = spec
    return rows


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_lane(lane_id: str, spec: dict, out_dir: Path) -> dict:
    lane_dir = out_dir / lane_id
    lane_dir.mkdir(parents=True, exist_ok=True)
    lane_cfg = LANE_COMMANDS[lane_id]
    proc = subprocess.run(
        lane_cfg["command"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    stdout_path = lane_dir / "stdout.txt"
    stderr_path = lane_dir / "stderr.txt"
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")

    if lane_cfg["kind"] == "json_overall_ok":
        result_payload = _load_json(ROOT / lane_cfg["result_path"])
        primary_score = 1.0 if bool(result_payload.get("overall_ok")) else 0.0
        verifier_status = "passed" if bool(result_payload.get("overall_ok")) else "failed"
        secondary_metrics = {
            "decision_state": result_payload.get("decision_state"),
            "missing_count": result_payload.get("missing_count"),
            "returncode": proc.returncode,
        }
    else:
        primary_score, pytest_metrics = _parse_pytest_pass_ratio(proc.stdout or "")
        verifier_status = "passed" if proc.returncode == 0 else "failed"
        secondary_metrics = {
            "pytest": pytest_metrics,
            "returncode": proc.returncode,
        }

    candidate = {
        "schema": "breadboard.darwin.candidate_artifact.v0",
        "candidate_id": f"cand.{lane_id}.baseline.v1",
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "parent_ids": [],
        "artifact_type": "config",
        "artifact_ref": f"artifacts/darwin/live_baselines/{lane_id}/baseline_candidate_v1.json",
        "mutation_operator": "baseline_seed",
        "novelty_score": 0.0,
        "estimated_potential": primary_score,
        "tool_scope": spec["allowed_tools"],
        "state_hash": _sha256_text(json.dumps(spec, sort_keys=True)),
    }
    eval_record = {
        "schema": "breadboard.darwin.evaluation_record.v0",
        "candidate_id": candidate["candidate_id"],
        "task_id": lane_cfg["task_id"],
        "verifier_status": verifier_status,
        "primary_score": round(primary_score, 6),
        "secondary_metrics": secondary_metrics,
        "budget_used": {"wall_time_s": None, "class": spec["budget_class"]},
        "wall_clock_ms": 0,
        "token_counts": {},
        "cost_estimate": 0.0,
        "artifact_refs": [
            str(stdout_path.relative_to(ROOT)),
            str(stderr_path.relative_to(ROOT)),
        ],
        "perturbation_group": "nominal",
        "error_taxonomy": [] if proc.returncode == 0 else ["subprocess_nonzero"],
        "confidence_record": {
            "support_n": 1,
            "ci_low": round(primary_score, 6),
            "ci_high": round(primary_score, 6),
            "claim_tier": "t1",
        },
    }

    candidate_issues = validate_candidate_artifact(candidate)
    evaluation_issues = validate_evaluation_record(eval_record)
    if candidate_issues or evaluation_issues:
        parts = [f"{issue.path}: {issue.message}" for issue in candidate_issues + evaluation_issues]
        raise ValueError(f"invalid live baseline DARWIN artifacts for {lane_id}: {'; '.join(parts)}")

    candidate_path = ROOT / "artifacts" / "darwin" / "candidates" / f"{lane_id}.baseline_candidate_v1.json"
    evaluation_path = ROOT / "artifacts" / "darwin" / "evaluations" / f"{lane_id}.baseline_evaluation_v1.json"
    _write_json(candidate_path, candidate)
    _write_json(evaluation_path, eval_record)

    return {
        "lane_id": lane_id,
        "campaign_id": spec["campaign_id"],
        "candidate_ref": str(candidate_path.relative_to(ROOT)),
        "evaluation_ref": str(evaluation_path.relative_to(ROOT)),
        "primary_score": eval_record["primary_score"],
        "verifier_status": verifier_status,
        "status": "ready" if proc.returncode == 0 else "partial",
        "command": lane_cfg["command"],
        "run_started_at": _iso_now(),
    }


def run_live_baselines(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    lane_rows = [_run_lane(lane_id, campaigns[lane_id], out_dir) for lane_id in ["lane.atp", "lane.harness", "lane.systems"]]
    payload = {
        "schema": "breadboard.darwin.live_baseline_summary.v1",
        "generated_at": _iso_now(),
        "lane_count": len(lane_rows),
        "lanes": lane_rows,
    }
    summary_path = out_dir / "live_baseline_summary_v1.json"
    _write_json(summary_path, payload)
    return {"summary_path": str(summary_path), "lane_count": len(lane_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DARWIN T1 live micro baselines for ATP/harness/systems.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_live_baselines(Path(args.out_dir))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"live_baseline_summary={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
