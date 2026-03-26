from __future__ import annotations

from agentic_coder_prototype.ctrees.candidate_a_pilot import run_candidate_a_pilot


def test_candidate_a_pilot_beats_deterministic_on_support_economy_without_regression() -> None:
    payload = run_candidate_a_pilot()
    summary = payload["summary"]

    assert payload["scenario_count"] == 8
    assert summary["win_count_vs_deterministic"] == 8
    assert summary["loss_count_vs_deterministic"] == 0
    assert summary["tie_count_vs_deterministic"] == 0
    assert summary["passes_gate"] is True
    assert summary["family_deltas"]["resume_distractors"]["support_token_share"] < 0.0
    assert summary["family_deltas"]["semantic_pivot"]["support_token_share"] < 0.0
