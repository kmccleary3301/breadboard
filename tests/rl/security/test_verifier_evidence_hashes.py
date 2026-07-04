from __future__ import annotations

from breadboard.rl.security.reports import VerifierRunReport, build_verifier_run_report


def test_verifier_report_hash_matches_output() -> None:
    report = build_verifier_run_report(
        report_id="verify-1",
        verifier_id="pytest",
        status="passed",
        output="passed",
        rerun_output="passed",
    )

    assert report.verify_evidence_hash() is True
    assert report.rerun_agreement is True


def test_verifier_report_hash_mismatch_is_detected() -> None:
    report = VerifierRunReport(
        report_id="verify-1",
        verifier_id="pytest",
        status="passed",
        output="tampered",
        evidence_sha256="sha256:bad",
    )

    assert report.verify_evidence_hash() is False
    assert report.to_dict()["evidence_hash_valid"] is False
