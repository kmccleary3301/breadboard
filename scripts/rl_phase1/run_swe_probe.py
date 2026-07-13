from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.e2e import run_controlled_swe_probe


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RL Phase 1 controlled SWE toy probe.")
    parser.add_argument(
        "--package",
        type=Path,
        default=Path("examples/rl_env_packages/swe_toy_patch/env_package.yaml"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy"),
    )
    parser.add_argument("--run-id", default="m6_controlled_swe_toy")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    run = run_controlled_swe_probe(
        package_path=args.package,
        output_dir=args.output_dir,
        run_id=args.run_id,
        limit=args.limit,
    )
    print(
        f"run_id={run.run_id} rows={len(run.rows)} "
        f"accepted={sum(row.row_status == 'accepted' for row in run.rows)} "
        f"rejected={sum(row.row_status == 'rejected' for row in run.rows)} "
        f"quarantined={sum(row.row_status == 'quarantined' for row in run.rows)}"
    )


if __name__ == "__main__":
    main()
