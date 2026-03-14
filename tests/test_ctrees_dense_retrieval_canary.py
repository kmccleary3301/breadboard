from __future__ import annotations

from agentic_coder_prototype.ctrees.dense_retrieval_canary import evaluate_dense_retrieval_canary


def test_ctree_dense_retrieval_canary_improves_over_helper_only_baseline() -> None:
    payload = evaluate_dense_retrieval_canary()

    assert payload["schema_version"] == "ctree_dense_retrieval_canary_v1"
    assert payload["family"] == "dense_semantic_retrieval"
    assert payload["scenario_count"] == 1
    assert payload["all_passed"] is True
    assert payload["scenarios"][0]["delta"] > 0
    assert payload["scenarios"][0]["dense_share_within_budget"] is True
