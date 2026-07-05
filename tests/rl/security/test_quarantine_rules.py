from __future__ import annotations

from breadboard.rl.security.hardening import HardeningFinding
from breadboard.rl.security.quarantine import quarantine_on_findings
from tests.rl.security.helpers import load_swe_hardening_policy


def test_policy_listed_finding_quarantines_row() -> None:
    decision = quarantine_on_findings(
        row_id="row-1",
        policy=load_swe_hardening_policy(),
        findings=[
            HardeningFinding(
                finding_id="sitecustomize_shadow",
                severity="medium",
                path="sitecustomize.py",
                message="detected",
            )
        ],
    )

    assert decision.quarantined is True
    assert "sitecustomize_shadow" in decision.reasons


def test_high_severity_unknown_finding_quarantines_by_default() -> None:
    decision = quarantine_on_findings(
        row_id="row-1",
        policy=load_swe_hardening_policy(),
        findings=[
            HardeningFinding(
                finding_id="unknown_high_risk",
                severity="high",
                path="x",
                message="detected",
            )
        ],
    )

    assert decision.quarantined is True
    assert "unknown_high_risk" in decision.reasons
