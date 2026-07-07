from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE_DIR = REPO_ROOT.parent / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1"
SCORECARD = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
CLAIM_LEDGER = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
M12_REPORT = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md"


def test_m12_points_are_awarded_after_target_evidence_review() -> None:
    scorecard = yaml.safe_load(SCORECARD.read_text(encoding="utf-8"))
    m12 = next(milestone for milestone in scorecard["milestones"] if milestone["id"] == "M12")

    assert scorecard["current_verified_points"] == 1000
    assert m12["verified_points"] == 80
    assert m12["status"] == "completed"
    assert scorecard["current_verified_points"] == sum(
        milestone["verified_points"] for milestone in scorecard["milestones"]
    )


def test_claim_ledger_forbids_overbroad_production_claims() -> None:
    text = CLAIM_LEDGER.read_text(encoding="utf-8")

    assert "BreadBoard passed 8xMI300X final validation" in text
    assert "Target-node final report is `m12_score_eligible=true`" in text
    assert "M12 scorecard edit is separately reviewed" in text
    assert "BreadBoard supports production RL rollouts." in text
    assert "No production, external benchmark, trainer, or scale support claim has been earned yet." in text
    assert "transfer/preflight/final-report preparation only" in text


def test_m12_validation_report_records_target_promotion_boundary() -> None:
    text = M12_REPORT.read_text(encoding="utf-8")

    assert "Status: passed, target validation executed on 8xMI300X" in text
    assert "Score impact: 80 / 80 M12 points awarded" in text
    assert "Program score after this report: 1000 / 1000" in text
    assert "M12 is complete." in text
    assert "separate reviewed scorecard/claim-ledger update" in text
    assert "BreadBoard passed M12 8xMI300X final validation" in text
    assert "archive_sha256_recorded_in=m12_transfer_archive_manifest.json" in text
    assert "m12_score_eligible=true" in text
    assert "promotion_review_ready=true" in text
    assert "scorecard_update_allowed=false" in text
    assert "Human Review" in text
