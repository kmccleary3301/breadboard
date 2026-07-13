from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from breadboard.rl.m12 import (
    validate_m12_bootstrap_dry_run_report,
    write_m12_bootstrap_dry_run_report,
    write_m12_transfer_archive,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_m12_bootstrap_dry_run_report_executes_generated_bootstrap(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    output_path = tmp_path / "bootstrap" / "m12_bootstrap_dry_run_report.json"

    report = write_m12_bootstrap_dry_run_report(
        repo_root=REPO_ROOT,
        workspace_root=REPO_ROOT.parent,
        transfer_prep_dir=tmp_path / "prep",
        output_path=output_path,
    )

    assert output_path.exists()
    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_bootstrap_dry_run_report_v1"
    assert report["claim_boundary"] == "target_bootstrap_dry_run_not_m12_validation"
    assert report["status"] == "passed"
    assert report["scorecard_update_allowed"] is False
    assert report["m12_points_awarded"] is False
    assert report["repo_head_verified"] is True
    assert report["dirty_checkout_check_observed"] is True
    assert report["dirty_checkout_mode"] in {"clean", "override"}
    assert report["target_commands_skipped"] is True
    assert report["exit_code"] == 0
    assert report["input_hashes"]["bootstrap_script"].startswith("sha256:")
    assert report["input_hashes"]["transfer_manifest"].startswith("sha256:")
    assert report["input_hashes"]["archive_manifest"].startswith("sha256:")
    assert report["input_hashes"]["overlay_dry_run_report"].startswith("sha256:")
    assert report["overlay"]["status"] == "passed"
    assert report["overlay"]["dry_run"] is True
    assert report["overlay"]["written_count"] == 0
    assert validate_m12_bootstrap_dry_run_report(report) == []


def test_m12_bootstrap_dry_run_cli_writes_non_scoring_report(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    output_path = tmp_path / "out" / "m12_bootstrap_dry_run_report.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_bootstrap_dry_run.py",
            "--repo-root",
            str(REPO_ROOT),
            "--workspace-root",
            str(REPO_ROOT.parent),
            "--transfer-prep-dir",
            str(tmp_path / "prep"),
            "--output",
            str(output_path),
            "--require-pass",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "status=passed" in result.stdout
    assert "dirty_checkout_mode=" in result.stdout
    assert "target_commands_skipped=True" in result.stdout
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["dirty_checkout_check_observed"] is True
    assert report["dirty_checkout_mode"] in {"clean", "override"}
    assert report["target_commands_skipped"] is True
    assert report["input_hashes"]["bootstrap_script"].startswith("sha256:")
    assert report["input_hashes"]["overlay_dry_run_report"].startswith("sha256:")
    assert report["overlay"]["written_count"] == 0
    assert report["scorecard_update_allowed"] is False


def test_m12_bootstrap_validator_rejects_missing_input_hashes(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    output_path = tmp_path / "bootstrap" / "m12_bootstrap_dry_run_report.json"
    report = write_m12_bootstrap_dry_run_report(
        repo_root=REPO_ROOT,
        workspace_root=REPO_ROOT.parent,
        transfer_prep_dir=tmp_path / "prep",
        output_path=output_path,
    )

    report["input_hashes"]["bootstrap_script"] = None
    report["input_hashes"].pop("overlay_dry_run_report")

    errors = validate_m12_bootstrap_dry_run_report(report)

    assert "input_hashes.bootstrap_script must start with sha256:" in errors
    assert "input_hashes.overlay_dry_run_report must start with sha256:" in errors


def test_m12_bootstrap_validator_rejects_stale_input_hashes(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    output_path = tmp_path / "bootstrap" / "m12_bootstrap_dry_run_report.json"
    report = write_m12_bootstrap_dry_run_report(
        repo_root=REPO_ROOT,
        workspace_root=REPO_ROOT.parent,
        transfer_prep_dir=tmp_path / "prep",
        output_path=output_path,
    )

    report["input_hashes"]["transfer_manifest"] = "sha256:" + ("0" * 64)

    errors = validate_m12_bootstrap_dry_run_report(report)

    assert "input_hashes.transfer_manifest does not match current file" in errors


def test_m12_bootstrap_validator_rejects_overlay_summary_drift(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    output_path = tmp_path / "bootstrap" / "m12_bootstrap_dry_run_report.json"
    report = write_m12_bootstrap_dry_run_report(
        repo_root=REPO_ROOT,
        workspace_root=REPO_ROOT.parent,
        transfer_prep_dir=tmp_path / "prep",
        output_path=output_path,
    )

    report["overlay"]["would_write_count"] += 1

    errors = validate_m12_bootstrap_dry_run_report(report)

    assert "overlay.would_write_count must match overlay_dry_run_report" in errors


def test_m12_bootstrap_validator_rejects_stale_overlay_report_file(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    output_path = tmp_path / "bootstrap" / "m12_bootstrap_dry_run_report.json"
    report = write_m12_bootstrap_dry_run_report(
        repo_root=REPO_ROOT,
        workspace_root=REPO_ROOT.parent,
        transfer_prep_dir=tmp_path / "prep",
        output_path=output_path,
    )
    overlay_path = Path(report["overlay_dry_run_report"])
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    overlay["existing_destination_count"] += 1
    overlay_path.write_text(json.dumps(overlay, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_m12_bootstrap_dry_run_report(report)

    assert "input_hashes.overlay_dry_run_report does not match current file" in errors
    assert (
        "overlay_dry_run_report.existing_destination_count must equal entries with exists=true"
        in errors
    )
