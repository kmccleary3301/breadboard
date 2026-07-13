from __future__ import annotations

from breadboard.rl.security import build_hardening_report
from tests.rl.security.helpers import load_swe_hardening_policy


def test_clean_workspace_hardening_passes_with_baseline_and_cleanup(tmp_path) -> None:
    policy = load_swe_hardening_policy()
    report = build_hardening_report(
        report_id="clean",
        workspace=tmp_path,
        policy=policy,
        clean_baseline_passed=True,
        reference_solution_passed=True,
        process_cleanup_observed=True,
    )

    assert report.status == "passed"
    assert report.findings == []


def test_hardening_fails_if_clean_baseline_breaks(tmp_path) -> None:
    policy = load_swe_hardening_policy()
    report = build_hardening_report(
        report_id="broken-clean",
        workspace=tmp_path,
        policy=policy,
        clean_baseline_passed=False,
        reference_solution_passed=True,
        process_cleanup_observed=True,
    )

    assert report.status == "failed"
    assert report.clean_baseline_passed is False


def test_missing_process_cleanup_quarantines_when_required(tmp_path) -> None:
    policy = load_swe_hardening_policy()
    report = build_hardening_report(
        report_id="no-cleanup",
        workspace=tmp_path,
        policy=policy,
        clean_baseline_passed=True,
        reference_solution_passed=True,
        process_cleanup_observed=False,
    )

    assert report.status == "quarantined"
    assert any(item.finding_id == "missing_process_cleanup" for item in report.findings)
