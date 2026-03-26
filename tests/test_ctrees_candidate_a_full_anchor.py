from __future__ import annotations

from agentic_coder_prototype.ctrees.candidate_a_full_anchor import run_candidate_a_full_anchor


def test_candidate_a_full_anchor_now_clears_registered_gate() -> None:
    payload = run_candidate_a_full_anchor()
    summary = payload["summary"]

    assert payload["base_scenario_count"] == 16
    assert summary["candidate_a_vs_deterministic_wins"] == 16
    assert summary["candidate_a_vs_deterministic_losses"] == 0
    assert summary["candidate_a_vs_deterministic_ties"] == 0
    assert summary["candidate_a_base_pass_count"] == 14
    assert summary["deterministic_base_pass_count"] == 8
    assert summary["candidate_a_order_pass_rate"] == 0.875
    assert summary["candidate_a_tight_budget_pass_rate"] == 0.875
    assert summary["family_deltas"]["resume_distractors"]["support_token_share"] < 0.0
    assert summary["family_deltas"]["semantic_pivot"]["support_token_share"] < 0.0
    assert summary["family_deltas"]["dependency_noise"]["scenario_score"] > 0.0
    assert summary["family_deltas"]["subtree_salience"]["scenario_score"] > 0.0
    assert summary["passes_full_gate"] is True
