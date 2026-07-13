from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import (  # noqa: E402
    validate_m12_evidence_consistency_report,
    write_m12_evidence_consistency_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local M12 blocked-state evidence consistency.")
    parser.add_argument(
        "--phase-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_evidence_consistency/m12_evidence_consistency.json"),
    )
    parser.add_argument(
        "--require-consistent",
        action="store_true",
        help="Exit nonzero unless the local blocked-state evidence is internally consistent.",
    )
    args = parser.parse_args()

    report = write_m12_evidence_consistency_report(phase_dir=args.phase_dir, output_path=args.output)
    validation_errors = validate_m12_evidence_consistency_report(report)
    if validation_errors:
        raise SystemExit("invalid_m12_evidence_consistency_report: " + "; ".join(validation_errors))
    print(
        "report="
        + report["report_id"]
        + f" consistent={report['consistent']} "
        + f"scorecard_update_allowed={report['scorecard_update_allowed']} "
        + f"m12_points_awarded={report['m12_points_awarded']} "
        + "errors="
        + (",".join(report["errors"]) or "none")
    )
    if args.require_consistent and not report["consistent"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
