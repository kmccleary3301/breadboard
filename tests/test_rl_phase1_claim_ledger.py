from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
PHASE_DIR = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1"
CLAIM_LEDGER = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
SCORECARD = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
CONFIDENCE_AUDIT = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_STRATEGY_CONFIDENCE_AUDIT.md"


def test_claim_ledger_exists_and_tracks_current_score() -> None:
    text = CLAIM_LEDGER.read_text(encoding="utf-8")
    scorecard = yaml.safe_load(SCORECARD.read_text(encoding="utf-8"))
    expected_score = f"{scorecard['current_verified_points']} / {scorecard['total_points']}"

    assert SCORECARD.exists()
    assert "Current verified score:" in text
    assert expected_score in text
    assert "Current Allowed Claims" in text
    assert "Forbidden Current Claims" in text
    assert "Claim Update Rule" in text


def test_claim_ledger_keeps_support_claims_forbidden_until_evidence() -> None:
    text = CLAIM_LEDGER.read_text(encoding="utf-8")

    forbidden_claims = [
        "BreadBoard supports production RL rollouts.",
        "BreadBoard is VeRL/GRPO/PPO ready.",
        "BreadBoard supports hardened SWE RL at scale.",
        "BreadBoard supports BenchFlow as a production integration.",
        "BreadBoard supports ORS/OpenReward as a production integration.",
        "BreadBoard supports NeMo Gym as a production integration.",
        "BreadBoard supports Prime Verifiers as a production integration.",
        "BreadBoard supports Toolathlon-Gym as a production integration.",
        "BreadBoard supports ProRL/Polar as a production integration.",
        "BreadBoard supports kernels RL.",
        "BreadBoard supports search RL.",
        "BreadBoard has generalized multi-family RL environment support.",
        "The RL Phase 1 strategy is guaranteed to succeed.",
        "The RL Phase 1 strategy is factually 100% certain in the predictive sense.",
    ]

    for claim in forbidden_claims:
        assert claim in text


def test_claim_ledger_requires_evidence_for_future_claim_promotion() -> None:
    text = CLAIM_LEDGER.read_text(encoding="utf-8")

    assert "Milestone id" in text
    assert "Evidence paths" in text
    assert "Test commands" in text
    assert "Known caveats" in text
    assert "Regression policy" in text
    assert "No claim may be promoted solely because a plan says it should be possible." in text


def test_claim_ledger_references_confidence_audit() -> None:
    text = CLAIM_LEDGER.read_text(encoding="utf-8")

    assert CONFIDENCE_AUDIT.exists()
    assert "STRATEGY_CONFIDENCE_AUDIT" in text
    assert "rejects literal 100% certainty" in text
    assert "fail-closed confidence protocol" in text


def test_claim_ledger_uses_bounded_confidence_classes() -> None:
    text = CLAIM_LEDGER.read_text(encoding="utf-8")

    assert "Deterministic local confidence" in text
    assert "Probe-backed conditional confidence" in text
    assert "External-infrastructure confidence" in text
    assert "Blocked/conditional until target-environment evidence exists." in text
