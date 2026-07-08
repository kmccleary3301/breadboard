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
    build_m12_load_ladder_report_from_package,
    validate_m12_load_ladder_report,
)


def _parse_levels(raw: str) -> list[int]:
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _parse_skip(raw_items: list[str]) -> dict[int, str]:
    skips: dict[int, str] = {}
    for raw in raw_items:
        if "=" not in raw:
            raise ValueError("--skip-level entries must be formatted LEVEL=reason")
        level, reason = raw.split("=", 1)
        skips[int(level)] = reason
    return skips


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M12 target load ladder probe.")
    parser.add_argument(
        "--package",
        type=Path,
        default=Path("examples/rl_env_packages/python_console_toy/env_package.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json"),
    )
    parser.add_argument("--levels", default="5,20,50,100")
    parser.add_argument("--skip-level", action="append", default=[])
    parser.add_argument("--min-rows-per-level", type=int, default=10)
    parser.add_argument(
        "--local-mode",
        action="store_true",
        help="Use Ray local_mode for local smoke tests. Target validation should omit this flag.",
    )
    parser.add_argument(
        "--smoke-mode",
        action="store_true",
        help="Validate only the supplied levels and allow local_mode. Never use for M12 score promotion.",
    )
    args = parser.parse_args()
    levels = _parse_levels(args.levels)
    skip_levels = _parse_skip(args.skip_level)
    report = build_m12_load_ladder_report_from_package(
        package_path=args.package,
        levels=levels,
        skip_levels=skip_levels,
        min_rows_per_level=args.min_rows_per_level,
        local_mode=args.local_mode,
        target_run_id=os.environ.get("M12_TARGET_RUN_ID"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validation_errors = validate_m12_load_ladder_report(
        report,
        required_levels=[level for level in levels if level not in skip_levels] if args.smoke_mode else None,
        optional_levels=list(skip_levels) if args.smoke_mode else None,
        require_distributed=not args.smoke_mode,
    )
    statuses = ",".join(f"{item['target_sessions']}:{item['status']}" for item in report["concurrency_levels"])
    print(
        "report="
        + report["report_id"]
        + f" levels={statuses} "
        + f"policy_version_integrity={report['policy_version_integrity']} "
        + f"queue_backpressure_integrity={report['queue_backpressure_integrity']}"
    )
    if validation_errors:
        raise SystemExit("invalid_m12_load_ladder_report: " + "; ".join(validation_errors))


if __name__ == "__main__":
    main()
