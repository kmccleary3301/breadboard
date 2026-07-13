from __future__ import annotations

from breadboard.rl.replay import ReplayParityReport, decide_export_admission


def test_admission_accepts_exportable_debug_for_clean_replay() -> None:
    report = ReplayParityReport(parity_tier="T2_deterministic_runtime_verifier", passed=True)

    decision = decide_export_admission(replay_report=report)

    assert decision.exportable is True
    assert decision.trainable is False
    assert decision.blocked_reasons == []


def test_admission_rejects_replay_mismatch() -> None:
    report = ReplayParityReport(
        parity_tier="T2_deterministic_runtime_verifier",
        passed=False,
        mismatches=["reward_mismatch"],
    )

    decision = decide_export_admission(replay_report=report)

    assert decision.exportable is False
    assert decision.trainable is False
    assert "replay_mismatch" in decision.blocked_reasons


def test_admission_rejects_quarantine_and_bad_token_records() -> None:
    report = ReplayParityReport(parity_tier="T2_deterministic_runtime_verifier", passed=True)

    decision = decide_export_admission(
        replay_report=report,
        quarantine_status="quarantined",
        token_records_valid=False,
    )

    assert decision.exportable is False
    assert "quarantine_status=quarantined" in decision.blocked_reasons
    assert "token_records_invalid" in decision.blocked_reasons
