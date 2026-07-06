from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import (  # noqa: E402
    validate_m12_bootstrap_dry_run_report,
    write_m12_bootstrap_dry_run_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the generated M12 target bootstrap in dry-run mode and write a non-scoring report."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--transfer-prep-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep"),
    )
    parser.add_argument("--workspace-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_bootstrap_dry_run/m12_bootstrap_dry_run_report.json"),
    )
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()

    report = write_m12_bootstrap_dry_run_report(
        repo_root=args.repo_root,
        transfer_prep_dir=args.transfer_prep_dir,
        workspace_root=args.workspace_root,
        output_path=args.output,
    )
    validation_errors = validate_m12_bootstrap_dry_run_report(report)
    if validation_errors:
        report = dict(report)
        report["status"] = "failed"
        report["errors"] = list(report.get("errors") or []) + validation_errors
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "report="
        + str(report["report_id"])
        + f" status={report['status']}"
        + f" exit_code={report['exit_code']}"
        + f" repo_head_verified={report['repo_head_verified']}"
        + f" dirty_checkout_mode={report['dirty_checkout_mode']}"
        + f" dirty_checkout_override_used={report['dirty_checkout_override_used']}"
        + f" target_commands_skipped={report['target_commands_skipped']}"
        + f" overlay_would_write={report['overlay']['would_write_count']}"
        + f" overlay_written={report['overlay']['written_count']}"
        + f" scorecard_update_allowed={report['scorecard_update_allowed']}"
        + f" m12_points_awarded={report['m12_points_awarded']}"
        + f" errors={len(report['errors'])}"
    )
    if args.require_pass and report["status"] != "passed":
        for error in report["errors"]:
            print(f"error={error}")
        raise SystemExit(6)


if __name__ == "__main__":
    main()
