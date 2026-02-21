#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_atp_benchmark_drift import run_drift_check
from scripts.validate_atp_artifacts import validate_state_ref_fastpath_summary


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _run_command(command: List[str], *, env: Dict[str, str] | None = None) -> None:
    subprocess.run(command, check=True, cwd=str(REPO_ROOT), env=env)


def _run_preflight(*, probe_pool: bool, min_healthy: int) -> None:
    _run_command([sys.executable, "scripts/atp_snapshot_preflight.py", "--json"])
    if probe_pool and (os.environ.get("FIRECRACKER_SNAPSHOT_DIRS") or "").strip():
        _run_command(
            [
                sys.executable,
                "scripts/atp_snapshot_pool_health.py",
                "--snapshot-dirs",
                os.environ["FIRECRACKER_SNAPSHOT_DIRS"],
                "--probe-restore",
                "--min-healthy",
                str(min_healthy),
                "--json",
            ]
        )


def _run_measured_pass(
    *,
    output_path: Path,
    concurrency: int | None,
    iters: int | None,
    burn_in_iters: int | None,
    repl_timeout: float | None,
) -> None:
    env = os.environ.copy()
    env["ATP_STATE_REF_BENCH_SUMMARY_PATH"] = str(output_path)
    if concurrency is not None:
        env["CONCURRENCY"] = str(concurrency)
    if iters is not None:
        env["ITERS"] = str(iters)
    if burn_in_iters is not None:
        env["BURN_IN_ITERS"] = str(burn_in_iters)
    if repl_timeout is not None:
        env["REPL_TIMEOUT"] = str(repl_timeout)
    _run_command([sys.executable, "scripts/atp_state_ref_fastpath_bench.py"], env=env)


def _evaluate_candidates(
    *,
    baseline_payload: Dict[str, Any],
    candidate_paths: List[Path],
    max_regression_pct: float,
    allow_config_mismatch: bool,
    required_consecutive_pass: int,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    pass_streak = 0
    stable_candidate: str | None = None

    for path in candidate_paths:
        candidate_payload = _load_json(path)
        validate_state_ref_fastpath_summary(candidate_payload)
        drift = run_drift_check(
            baseline=baseline_payload,
            candidate=candidate_payload,
            max_regression_pct=max_regression_pct,
            allow_config_mismatch=allow_config_mismatch,
        )
        shape_match = all(bool(item.get("matches")) for item in drift["config_comparisons"].values())
        sample_depth_ok = candidate_payload.get("meets_recommended_sample_depth") is True
        failures = list(drift["failures"])
        if not sample_depth_ok:
            failures.append("candidate does not meet recommended sample depth")
        ok = len(failures) == 0
        pass_streak = pass_streak + 1 if ok else 0
        if stable_candidate is None and pass_streak >= required_consecutive_pass:
            stable_candidate = str(path.resolve())
        results.append(
            {
                "candidate_path": str(path.resolve()),
                "ok": ok,
                "pass_streak": pass_streak,
                "shape_match": shape_match,
                "sample_depth_ok": sample_depth_ok,
                "comparisons": drift["comparisons"],
                "config_comparisons": drift["config_comparisons"],
                "failures": failures,
            }
        )

    pass_count = sum(1 for item in results if item["ok"])
    fail_count = len(results) - pass_count
    if stable_candidate:
        classification = "stable_pass"
    elif pass_count and fail_count:
        classification = "unstable_mixed"
    elif fail_count:
        classification = "persistent_fail"
    else:
        classification = "no_runs"

    return {
        "results": results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "required_consecutive_pass": required_consecutive_pass,
        "stable_candidate": stable_candidate,
        "classification": classification,
        "ok": stable_candidate is not None,
    }


def _promote(
    *,
    candidate: Path,
    baseline_out: Path,
    meta_out: Path,
) -> None:
    _run_command(
        [
            sys.executable,
            "scripts/promote_atp_state_ref_baseline.py",
            "--candidate",
            str(candidate),
            "--baseline",
            str(baseline_out),
            "--meta-out",
            str(meta_out),
        ]
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run repeated measured ATP state-ref passes and decide promote/no-promote via consecutive drift passes."
    )
    parser.add_argument("--baseline", default="artifacts/atp_state_ref_fastpath_baseline.json")
    parser.add_argument("--candidate", action="append", default=[], help="Optional measured candidate JSON (repeatable).")
    parser.add_argument("--runs", type=int, default=3, help="Measured run count when --candidate is not provided.")
    parser.add_argument("--required-consecutive-pass", type=int, default=2)
    parser.add_argument("--max-regression-pct", type=float, default=10.0)
    parser.add_argument("--allow-config-mismatch", action="store_true")
    parser.add_argument("--promote-on-pass", action="store_true")
    parser.add_argument("--baseline-out", default="artifacts/atp_state_ref_fastpath_baseline.json")
    parser.add_argument("--meta-out", default="artifacts/atp_state_ref_fastpath_baseline.meta.json")
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--run-tag", default="c4")
    parser.add_argument("--report-out", default="")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--iters", type=int, default=None)
    parser.add_argument("--burn-in-iters", type=int, default=None)
    parser.add_argument("--repl-timeout", type=float, default=None)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--probe-pool", action="store_true")
    parser.add_argument("--pool-min-healthy", type=int, default=1)
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline).resolve()
    baseline_payload = _load_json(baseline_path)
    validate_state_ref_fastpath_summary(baseline_payload)

    artifact_dir = Path(args.artifact_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")

    candidate_paths: List[Path] = [Path(path).resolve() for path in args.candidate]
    if not candidate_paths:
        if not args.skip_preflight:
            _run_preflight(probe_pool=bool(args.probe_pool), min_healthy=max(1, int(args.pool_min_healthy)))
        for run_index in range(max(1, int(args.runs))):
            candidate_path = artifact_dir / f"atp_state_ref_fastpath_{args.run_tag}_{ts}_measured_r{run_index + 1}.json"
            _run_measured_pass(
                output_path=candidate_path,
                concurrency=args.concurrency,
                iters=args.iters,
                burn_in_iters=args.burn_in_iters,
                repl_timeout=args.repl_timeout,
            )
            candidate_paths.append(candidate_path)

    evaluation = _evaluate_candidates(
        baseline_payload=baseline_payload,
        candidate_paths=candidate_paths,
        max_regression_pct=float(args.max_regression_pct),
        allow_config_mismatch=bool(args.allow_config_mismatch),
        required_consecutive_pass=max(1, int(args.required_consecutive_pass)),
    )

    promoted = False
    promoted_candidate = None
    promotion_blocked_reason = None
    if args.promote_on_pass and evaluation["stable_candidate"]:
        selected = Path(str(evaluation["stable_candidate"])).resolve()
        selected_row = next(
            (
                row
                for row in evaluation["results"]
                if str(Path(str(row.get("candidate_path", ""))).resolve()) == str(selected)
            ),
            None,
        )
        if selected_row and not bool(selected_row.get("shape_match")):
            promotion_blocked_reason = "shape_mismatch"
        elif selected_row and not bool(selected_row.get("sample_depth_ok")):
            promotion_blocked_reason = "sample_depth_insufficient"
        else:
            _promote(
                candidate=selected,
                baseline_out=Path(args.baseline_out).resolve(),
                meta_out=Path(args.meta_out).resolve(),
            )
            promoted = True
            promoted_candidate = str(selected)

    report = {
        "generated_at": time.time(),
        "baseline_path": str(baseline_path),
        "candidate_paths": [str(path) for path in candidate_paths],
        "settings": {
            "runs": max(1, int(args.runs)),
            "required_consecutive_pass": max(1, int(args.required_consecutive_pass)),
            "max_regression_pct": float(args.max_regression_pct),
            "allow_config_mismatch": bool(args.allow_config_mismatch),
            "promote_on_pass": bool(args.promote_on_pass),
            "run_tag": args.run_tag,
            "concurrency": args.concurrency,
            "iters": args.iters,
            "burn_in_iters": args.burn_in_iters,
            "repl_timeout": args.repl_timeout,
        },
        "evaluation": evaluation,
        "promotion": {
            "promoted": promoted,
            "promoted_candidate": promoted_candidate,
            "blocked_reason": promotion_blocked_reason,
            "baseline_out": str(Path(args.baseline_out).resolve()),
            "meta_out": str(Path(args.meta_out).resolve()),
        },
    }

    report_path = Path(args.report_out).resolve() if args.report_out else artifact_dir / f"atp_state_ref_recovery_report_{ts}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    latest_path = artifact_dir / "atp_state_ref_recovery_report.latest.json"
    latest_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"recovery_report: {report_path}")
    print(f"recovery_report_latest: {latest_path}")
    print(f"recovery_classification: {evaluation['classification']}")
    if evaluation["stable_candidate"]:
        print(f"recovery_stable_candidate: {evaluation['stable_candidate']}")
    if promoted:
        print(f"baseline_promoted_from_recovery: {promoted_candidate}")
    elif promotion_blocked_reason:
        print(f"baseline_promotion_blocked: {promotion_blocked_reason}")

    return 0 if evaluation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
