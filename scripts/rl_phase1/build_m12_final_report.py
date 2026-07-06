from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import validate_m12_final_report, write_m12_final_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate the M12 final target-node report.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"),
    )
    parser.add_argument(
        "--archive-verify-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json"),
    )
    parser.add_argument(
        "--preflight-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight/m12_preflight_report.json"),
    )
    parser.add_argument(
        "--swe-run-summary",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe/run_summary.json"),
    )
    parser.add_argument(
        "--verl-smoke-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_verl_probe/smoke_consumer_report.json"),
    )
    parser.add_argument(
        "--ray-probe-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/ray_probe_report.json"),
    )
    parser.add_argument(
        "--warm-vs-cold-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/warm_vs_cold_report.json"),
    )
    parser.add_argument(
        "--load-ladder-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json"),
    )
    parser.add_argument(
        "--soak-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json"),
    )
    parser.add_argument(
        "--command-log-manifest",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"),
    )
    parser.add_argument(
        "--require-eligible",
        action="store_true",
        help="Exit nonzero unless the final report is M12 score-eligible.",
    )
    args = parser.parse_args()

    report = write_m12_final_report(
        output_path=args.output,
        archive_verify_report_path=args.archive_verify_report,
        preflight_report_path=args.preflight_report,
        swe_run_summary_path=args.swe_run_summary,
        verl_smoke_report_path=args.verl_smoke_report,
        ray_probe_report_path=args.ray_probe_report,
        warm_vs_cold_report_path=args.warm_vs_cold_report,
        load_ladder_report_path=args.load_ladder_report,
        soak_report_path=args.soak_report,
        command_log_manifest_path=args.command_log_manifest,
    )
    errors = validate_m12_final_report(report)
    if errors:
        raise SystemExit("invalid_m12_final_report: " + "; ".join(errors))
    print(
        "report="
        + report["report_id"]
        + f" score_eligible={report['m12_score_eligible']} "
        + f"missing_gate_remediations={len(report['missing_gate_remediations'])} "
        + "missing_gates="
        + (",".join(report["missing_gates"]) or "none")
    )
    if args.require_eligible and not report["m12_score_eligible"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
