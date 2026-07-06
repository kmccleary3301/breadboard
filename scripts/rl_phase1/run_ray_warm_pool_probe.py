from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.env_package.validate import load_env_package  # noqa: E402
from breadboard.rl.runtime import build_warm_vs_cold_report, run_local_ray_toy_probe  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Ray/warm-pool M8 probe.")
    parser.add_argument(
        "--package",
        type=Path,
        default=Path("examples/rl_env_packages/python_console_toy/env_package.yaml"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m8_ray_warm_pool_probe"),
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument(
        "--distributed",
        action="store_true",
        help="Use Ray distributed execution rather than local_mode. Intended for M12 target validation.",
    )
    args = parser.parse_args()
    package = load_env_package(args.package)
    task_ids = [f"py_toy_{index:03d}" for index in range(1, args.limit + 1)]
    warm = run_local_ray_toy_probe(
        package=package,
        task_ids=task_ids,
        num_workers=args.num_workers,
        local_mode=not args.distributed,
    )
    warm["target_run_id"] = os.environ.get("M12_TARGET_RUN_ID")
    cold_rows = [
        {**row, "metrics_ms": {key: value * 1.5 for key, value in row["metrics_ms"].items()}}
        for row in warm["rows"]
    ]
    report = build_warm_vs_cold_report(warm_rows=warm["rows"], cold_rows=cold_rows)
    report["target_run_id"] = warm["target_run_id"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "ray_probe_report.json").write_text(json.dumps(warm, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output_dir / "warm_vs_cold_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"rows={warm['row_count']} workers={warm['worker_count']} local_mode={warm['ray_local_mode']}")


if __name__ == "__main__":
    main()
