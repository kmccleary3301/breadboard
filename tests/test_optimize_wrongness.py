from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import WrongnessReport


def test_wrongness_report_round_trip() -> None:
    report = WrongnessReport(
        wrongness_id="wrong.001",
        wrongness_class="replay.conformance_drift",
        failure_locus="prompt.section.optimization_guidance",
        explanation="Replay output drifted relative to the expected lane.",
        confidence=0.9,
        likely_repair_locus="prompt.section.optimization_guidance",
    )

    restored = WrongnessReport.from_dict(report.to_dict())
    assert restored == report


def test_wrongness_report_rejects_invalid_class() -> None:
    with pytest.raises(ValueError, match="wrongness_class"):
        WrongnessReport(
            wrongness_id="wrong.002",
            wrongness_class="made.up.class",
            failure_locus="x",
            explanation="bad",
            confidence=0.5,
            likely_repair_locus="x",
        )


def test_wrongness_report_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        WrongnessReport(
            wrongness_id="wrong.003",
            wrongness_class="correctness.result_mismatch",
            failure_locus="x",
            explanation="bad",
            confidence=1.5,
            likely_repair_locus="x",
        )
