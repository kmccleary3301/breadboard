from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import (
    validate_effective_config,
    validate_execution_plan,
    validate_candidate_artifact,
    validate_evaluation_record,
)
from breadboard_ext.darwin.phase2 import (
    build_effective_config,
    build_execution_plan,
    should_emit_shadow_artifacts,
)


DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "live_baselines"
DEFAULT_BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
ACTIVE_LANES = ["lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.scheduling", "lane.research"]


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
    "lane.repo_swe": {
        "command": [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_opencode_patch_apply_codex.py",
            "tests/test_workspace_tracker.py",
            "tests/test_diff_terminal_semantics.py",
            "tests/test_langflow_patch.py",
        ],
        "kind": "pytest_pass_ratio",
        "result_path": None,
        "task_id": "task.darwin.repo_swe.patch_workspace_smoke",
    },
    "lane.scheduling": {
        "command": [
            sys.executable,
            "scripts/run_darwin_scheduling_lane_baseline_v0.py",
            "--strategy",
            "deadline_first",
            "--out",
            "artifacts/darwin/live_baselines/lane.scheduling/scheduling_baseline.json",
        ],
        "kind": "json_overall_ok",
        "result_path": "artifacts/darwin/live_baselines/lane.scheduling/scheduling_baseline.json",
        "task_id": "task.darwin.scheduling.constraint_objective_smoke",
    },
    "lane.research": {
        "command": [
            sys.executable,
            "scripts/run_darwin_research_lane_baseline_v0.py",
            "--strategy",
            "precision_first",
            "--out",
            "artifacts/darwin/live_baselines/lane.research/research_baseline.json",
        ],
        "kind": "json_overall_ok",
        "result_path": "artifacts/darwin/live_baselines/lane.research/research_baseline.json",
        "task_id": "task.darwin.research.evidence_synthesis_smoke",
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


def _join_issue_messages(*issue_groups: list) -> str:
    return "; ".join(f"{issue.path}: {issue.message}" for group in issue_groups for issue in group)


def run_named_lane(
    lane_id: str,
    spec: dict,
    out_dir: Path,
    *,
    candidate_id: str | None = None,
    mutation_operator: str = "baseline_seed",
    topology_id: str | None = None,
    policy_bundle_id: str | None = None,
    budget_class: str | None = None,
    perturbation_group: str = "nominal",
    task_id: str | None = None,
    trial_label: str = "baseline",
    command_override: list[str] | None = None,
    result_path_override: str | None = None,
    kind_override: str | None = None,
) -> dict:
    lane_dir = out_dir / lane_id
    lane_dir.mkdir(parents=True, exist_ok=True)
    lane_cfg = LANE_COMMANDS[lane_id]
    campaign_spec_ref = next(
        row["path"] for row in _load_json(DEFAULT_BOOTSTRAP_MANIFEST).get("specs") or [] if row["lane_id"] == lane_id
    )
    started_at = _iso_now()
    started_monotonic = time.perf_counter()
    command = command_override or lane_cfg["command"]
    result_path = result_path_override or lane_cfg.get("result_path")
    kind = kind_override or lane_cfg["kind"]
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    wall_clock_ms = int(round((time.perf_counter() - started_monotonic) * 1000))

    stdout_path = lane_dir / "stdout.txt"
    stderr_path = lane_dir / "stderr.txt"
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")

    if kind == "json_overall_ok":
        result_payload = _load_json(ROOT / result_path)
        primary_score = float(result_payload.get("primary_score") if result_payload.get("primary_score") is not None else (1.0 if bool(result_payload.get("overall_ok")) else 0.0))
        verifier_status = "passed" if bool(result_payload.get("overall_ok")) else "failed"
        secondary_metrics = {
            "decision_state": result_payload.get("decision_state"),
            "missing_count": result_payload.get("missing_count"),
            "strategy": result_payload.get("strategy"),
            "scenario_count": result_payload.get("scenario_count"),
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
        "candidate_id": candidate_id or f"cand.{lane_id}.{trial_label}.v1",
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "parent_ids": [],
        "artifact_type": "config",
        "artifact_ref": f"artifacts/darwin/live_baselines/{lane_id}/{trial_label}_candidate_v1.json",
        "mutation_operator": mutation_operator,
        "novelty_score": 0.0,
        "estimated_potential": primary_score,
        "tool_scope": spec["allowed_tools"],
        "state_hash": _sha256_text(
            json.dumps(
                {
                    "spec": spec,
                    "lane_id": lane_id,
                    "mutation_operator": mutation_operator,
                    "topology_id": topology_id or spec["topology_family"],
                    "policy_bundle_id": policy_bundle_id or spec["policy_bundle_id"],
                    "budget_class": budget_class or spec["budget_class"],
                },
                sort_keys=True,
            )
        ),
    }
    eval_record = {
        "schema": "breadboard.darwin.evaluation_record.v0",
        "candidate_id": candidate["candidate_id"],
        "task_id": task_id or lane_cfg["task_id"],
        "verifier_status": verifier_status,
        "primary_score": round(primary_score, 6),
        "secondary_metrics": secondary_metrics,
        "budget_used": {"wall_time_s": round(wall_clock_ms / 1000.0, 6), "class": budget_class or spec["budget_class"]},
        "wall_clock_ms": wall_clock_ms,
        "token_counts": {},
        "cost_estimate": 0.0,
        "artifact_refs": [
            str(stdout_path.relative_to(ROOT)),
            str(stderr_path.relative_to(ROOT)),
        ],
        "perturbation_group": perturbation_group,
        "error_taxonomy": [] if proc.returncode == 0 else ["subprocess_nonzero"],
        "confidence_record": {
            "support_n": 1,
            "ci_low": round(primary_score, 6),
            "ci_high": round(primary_score, 6),
            "claim_tier": "t1",
        },
    }

    candidate_path = ROOT / "artifacts" / "darwin" / "candidates" / f"{candidate['candidate_id']}.json"
    evaluation_path = ROOT / "artifacts" / "darwin" / "evaluations" / f"{candidate['candidate_id']}.evaluation_v1.json"
    candidate_ref = str(candidate_path.relative_to(ROOT))
    evaluation_ref = str(evaluation_path.relative_to(ROOT))

    shadow_refs: dict[str, str] = {}
    if should_emit_shadow_artifacts(lane_id):
        effective_config = build_effective_config(
            spec=spec,
            lane_id=lane_id,
            candidate_id=candidate["candidate_id"],
            trial_label=trial_label,
            task_id=task_id or lane_cfg["task_id"],
            command=command,
            campaign_spec_ref=campaign_spec_ref,
            topology_id=topology_id or spec["topology_family"],
            policy_bundle_id=policy_bundle_id or spec["policy_bundle_id"],
            budget_class=budget_class or spec["budget_class"],
        )
        effective_config_path = lane_dir / f"{trial_label}_effective_config_v0.json"
        _write_json(effective_config_path, effective_config)
        shadow_refs["effective_config"] = str(effective_config_path.relative_to(ROOT))

        execution_plan = build_execution_plan(
            spec=spec,
            lane_id=lane_id,
            candidate_id=candidate["candidate_id"],
            trial_label=trial_label,
            task_id=task_id or lane_cfg["task_id"],
            command=command,
            topology_id=topology_id or spec["topology_family"],
            budget_class=budget_class or spec["budget_class"],
            effective_config_ref=shadow_refs["effective_config"],
            candidate_ref=candidate_ref,
            evaluation_ref=evaluation_ref,
            stdout_ref=str(stdout_path.relative_to(ROOT)),
            stderr_ref=str(stderr_path.relative_to(ROOT)),
            out_dir=str(lane_dir.relative_to(ROOT)),
        )
        execution_plan_path = lane_dir / f"{trial_label}_execution_plan_v0.json"
        _write_json(execution_plan_path, execution_plan)
        shadow_refs["execution_plan"] = str(execution_plan_path.relative_to(ROOT))

        effective_config_issues = validate_effective_config(effective_config)
        execution_plan_issues = validate_execution_plan(execution_plan)
    else:
        effective_config_issues = []
        execution_plan_issues = []

    candidate_issues = validate_candidate_artifact(candidate)
    evaluation_issues = validate_evaluation_record(eval_record)
    if candidate_issues or evaluation_issues or effective_config_issues or execution_plan_issues:
        raise ValueError(
            f"invalid live baseline DARWIN artifacts for {lane_id}: "
            f"{_join_issue_messages(candidate_issues, evaluation_issues, effective_config_issues, execution_plan_issues)}"
        )

    _write_json(candidate_path, candidate)
    _write_json(evaluation_path, eval_record)

    return {
        "lane_id": lane_id,
        "campaign_id": spec["campaign_id"],
        "candidate_id": candidate["candidate_id"],
        "candidate_ref": candidate_ref,
        "evaluation_ref": evaluation_ref,
        "shadow_artifact_refs": shadow_refs,
        "primary_score": eval_record["primary_score"],
        "verifier_status": verifier_status,
        "status": "ready" if proc.returncode == 0 else "partial",
        "command": command,
        "run_started_at": started_at,
        "topology_id": topology_id or spec["topology_family"],
        "policy_bundle_id": policy_bundle_id or spec["policy_bundle_id"],
        "budget_class": budget_class or spec["budget_class"],
        "mutation_operator": mutation_operator,
        "wall_clock_ms": wall_clock_ms,
    }


def run_live_baselines(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    lane_rows = [run_named_lane(lane_id, campaigns[lane_id], out_dir, trial_label="baseline") for lane_id in ACTIVE_LANES]
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
    parser = argparse.ArgumentParser(description="Run DARWIN T1 live micro baselines for all active Phase-1 lanes.")
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
