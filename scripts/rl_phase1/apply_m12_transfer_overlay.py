from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import apply_m12_transfer_overlay, validate_m12_transfer_overlay_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Safely dry-run or apply the non-scoring M12 RL Phase 1 source/control overlay. "
            "This does not validate M12 or update the scorecard."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep/m12_transfer_archive_manifest.json"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPO_ROOT.parent,
        help="Workspace directory corresponding to the archive's workspace/ root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep/m12_overlay_apply_report.json"),
    )
    parser.add_argument("--apply", action="store_true", help="Write overlay files. Default is dry-run only.")
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow existing destination files to be overwritten during --apply.",
    )
    args = parser.parse_args()

    report = apply_m12_transfer_overlay(
        manifest_path=args.manifest,
        workspace_root=args.workspace_root,
        dry_run=not args.apply,
        allow_overwrite=args.allow_overwrite,
    )
    validation_errors = validate_m12_transfer_overlay_report(report)
    if validation_errors:
        report = dict(report)
        report["status"] = "failed"
        report["errors"] = list(report.get("errors") or []) + validation_errors

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "report="
        + str(report["report_id"])
        + f" status={report['status']}"
        + f" dry_run={report['dry_run']}"
        + f" would_write={report['would_write_count']}"
        + f" written={report['written_count']}"
        + f" existing_destinations={report['existing_destination_count']}"
        + f" scorecard_update_allowed={report['scorecard_update_allowed']}"
        + f" m12_points_awarded={report['m12_points_awarded']}"
        + f" errors={len(report['errors'])}"
    )
    if report["status"] != "passed":
        for error in report["errors"]:
            print(f"error={error}")
        raise SystemExit(6)


if __name__ == "__main__":
    main()
