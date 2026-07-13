from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import write_m12_preflight_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M12 target-node preflight probe.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight"),
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit nonzero unless the target preflight status is preflight_passed.",
    )
    args = parser.parse_args()
    report = write_m12_preflight_report(args.output_dir)
    print(f"status={report['status']} blockers={','.join(report['blockers']) or 'none'}")
    if args.require_pass and report["status"] != "preflight_passed":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
