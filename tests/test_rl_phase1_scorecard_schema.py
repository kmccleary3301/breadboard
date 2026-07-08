from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
PHASE_DIR = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1"
SCORECARD = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
PLAN = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_EXECUTION_PLAN.md"
CONFIDENCE_AUDIT = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_STRATEGY_CONFIDENCE_AUDIT.md"


def load_scorecard() -> dict:
    return yaml.safe_load(SCORECARD.read_text(encoding="utf-8"))


def test_scorecard_totals_and_verified_points_are_consistent() -> None:
    scorecard = load_scorecard()
    milestones = scorecard["milestones"]

    assert scorecard["total_points"] == 1000
    assert sum(milestone["points"] for milestone in milestones) == 1000
    assert scorecard["current_verified_points"] == sum(
        milestone["verified_points"] for milestone in milestones
    )
    assert all(milestone["points"] >= 0 for milestone in milestones)
    assert all(milestone["verified_points"] >= 0 for milestone in milestones)
    assert all(
        milestone["verified_points"] <= milestone["points"] for milestone in milestones
    )


def test_scorecard_preserves_phase1_claim_and_evidence_policy() -> None:
    scorecard = load_scorecard()
    policy = scorecard["score_policy"]

    assert policy["planning_prose_scores"] is False
    assert policy["points_require_evidence"] is True
    assert "committed_or_written_artifact_path" in policy["evidence_required"]
    assert "test_command_or_validation_command" in policy["evidence_required"]
    assert "pass_fail_summary" in policy["evidence_required"]

    forbidden = "\n".join(scorecard["forbidden_current_claims"])
    assert "production RL rollouts" in forbidden
    assert "VeRL/GRPO/PPO ready" in forbidden
    assert "hardened SWE RL at scale" in forbidden


def test_scorecard_has_all_expected_milestones_in_order() -> None:
    scorecard = load_scorecard()
    milestone_ids = [milestone["id"] for milestone in scorecard["milestones"]]

    assert milestone_ids == [
        "M0",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
        "M9",
        "M10",
        "M11",
        "M12",
    ]
    assert scorecard["milestones"][-1]["status"] == "completed"


def test_control_plan_references_confidence_audit_and_hard_gates() -> None:
    plan_text = PLAN.read_text(encoding="utf-8")

    assert CONFIDENCE_AUDIT.exists()
    assert "Strategy Confidence Boundary" in plan_text
    assert "Source selection fallback ladder" in plan_text
    assert "Provider fidelity classes" in plan_text
    assert "Non-negotiable M12 preflight" in plan_text
    assert "Broad generalized RL support" in plan_text
    assert "Factually Bounded Confidence Protocol" in plan_text
    assert "What would falsify this milestone claim?" in plan_text


def test_confidence_audit_fail_closed_protocol_exists() -> None:
    audit_text = CONFIDENCE_AUDIT.read_text(encoding="utf-8")

    assert "Round 3: Execution-Closure Loopholes" in audit_text
    assert "Round 4: Final Confidence Test" in audit_text
    assert "Factually Bounded Confidence Protocol" in audit_text
    assert "Kill Switches" in audit_text
    assert "No, not in the predictive sense." in audit_text
    assert "fail-closed" in audit_text
