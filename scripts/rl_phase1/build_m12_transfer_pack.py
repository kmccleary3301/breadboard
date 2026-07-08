from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import build_m12_transfer_summary, write_m12_transfer_pack  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local M12 transfer-preparation manifest.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep"),
    )
    args = parser.parse_args()
    manifest = write_m12_transfer_pack(repo_root=REPO_ROOT, output_dir=args.output_dir)
    summary = build_m12_transfer_summary(manifest)
    print(
        "manifest="
        + manifest["manifest_id"]
        + f" artifacts_present={manifest['all_required_artifacts_present']} "
        + f"commands={len(manifest['test_commands'])}"
        + f"; required_artifacts={len(manifest['artifacts'])}"
        + f"; expected_outputs={len(manifest['expected_outputs'])}"
        + f"; readiness_summary={bool(summary['readiness_summary'])}"
        + f"; concrete_load_soak_scripts={summary['concrete_load_soak_scripts']}"
        + f"; load_soak_command_log_templates={summary['load_soak_command_log_templates']}"
        + f"; logged_command_wrapper={summary['logged_command_wrapper']}"
        + f"; bootstrap_dirty_checkout_guard={summary['bootstrap_dirty_checkout_guard']}"
        + f"; bootstrap_overlaid_test_commands_handoff={summary['bootstrap_overlaid_test_commands_handoff']}"
        + f"; bootstrap_repo_root_cwd_handoff={summary['bootstrap_repo_root_cwd_handoff']}"
        + f"; target_run_id_command_binding={summary['target_run_id_command_binding']}"
        + f"; target_run_log_reuse_guard={summary['target_run_log_reuse_guard']}"
        + f"; target_closeout_artifact_reuse_guard={summary['target_closeout_artifact_reuse_guard']}"
        + f"; final_report_failure_remediation_summary={summary['final_report_failure_remediation_summary']}"
        + f"; generated_script_manifest_consistent={summary['generated_script_manifest_consistent']}"
        + f"; archive_verifier_runs_first={summary['archive_verifier_runs_first']}"
        + f"; preflight_command_require_pass={summary['preflight_command_require_pass']}"
        + f"; final_command_explicit_target_artifact_args={summary['final_command_explicit_target_artifact_args']}"
        + f"; final_command_require_eligible={summary['final_command_require_eligible']}"
        + f"; promotion_audit_require_ready={summary['promotion_audit_require_ready']}"
        + f"; promotion_audit_explicit_score_inputs={summary['promotion_audit_explicit_score_inputs']}"
        + f"; promotion_audit_explicit_target_paths={summary['promotion_audit_explicit_target_paths']}"
        + f"; all_transfer_requirements_covered={summary['all_transfer_requirements_covered']}"
    )


if __name__ == "__main__":
    main()
