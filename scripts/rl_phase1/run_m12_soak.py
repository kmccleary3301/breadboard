from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12.load_soak import (  # noqa: E402
    build_m12_soak_report_from_package,
    validate_m12_soak_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M12 target soak probe.")
    parser.add_argument(
        "--package",
        type=Path,
        default=Path("examples/rl_env_packages/python_console_toy/env_package.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json"),
    )
    parser.add_argument("--duration-seconds", type=int, default=7200)
    parser.add_argument("--minimum-duration-seconds", type=int, default=7200)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--num-workers", type=int, default=20)
    parser.add_argument("--rows-per-iteration", type=int, default=10)
    parser.add_argument("--min-iterations", type=int, default=1)
    parser.add_argument(
        "--local-mode",
        action="store_true",
        help="Use Ray local_mode for local smoke tests. Target validation should omit this flag.",
    )
    parser.add_argument(
        "--smoke-mode",
        action="store_true",
        help="Allow local_mode and short durations for local smoke tests. Never use for M12 score promotion.",
    )
    args = parser.parse_args()
    report = build_m12_soak_report_from_package(
        package_path=args.package,
        duration_seconds=args.duration_seconds,
        minimum_duration_seconds=args.minimum_duration_seconds,
        interval_seconds=args.interval_seconds,
        num_workers=args.num_workers,
        rows_per_iteration=args.rows_per_iteration,
        min_iterations=args.min_iterations,
        local_mode=args.local_mode,
        target_run_id=os.environ.get("M12_TARGET_RUN_ID"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validation_errors = validate_m12_soak_report(
        report,
        minimum_duration_seconds=args.minimum_duration_seconds,
        require_distributed=not args.smoke_mode,
    )
    print(
        "report="
        + report["report_id"]
        + f" status={report['status']} "
        + f"duration_seconds={report['duration_seconds']} "
        + f"runtime_failure_count={report['runtime_failure_count']}"
    )
    if validation_errors:
        raise SystemExit("invalid_m12_soak_report: " + "; ".join(validation_errors))


if __name__ == "__main__":
    main()
