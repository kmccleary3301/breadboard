from __future__ import annotations

from agentic_coder_prototype.ctrees.compiler import compile_ctree
from agentic_coder_prototype.ctrees.evaluation_metrics import (
    build_evaluation_prompt_planes,
    evaluate_support_result,
)
from agentic_coder_prototype.ctrees.microprobes import build_false_neighbor_store
from agentic_coder_prototype.ctrees.policy import build_rehydration_plan


def test_ctree_evaluation_metrics_emits_standardized_payload() -> None:
    store = build_false_neighbor_store()
    focus_node_id = str(store.snapshot()["last_node"]["id"])
    compiled = compile_ctree(store, focus_node_id=focus_node_id)
    plan = build_rehydration_plan(store, mode="active_continuation", focus_node_id=focus_node_id, lane_profile="frozen_core")
    prompt_planes = build_evaluation_prompt_planes(
        compiled["prompt_planes"],
        support_bundle=plan["rehydration_bundle"],
    )

    result = evaluate_support_result(
        store=store,
        scenario_id="metric_probe",
        family="resume_distractors",
        perturbation="base",
        system_id="frozen_core",
        baseline_id="stripped_support",
        mode="active_continuation",
        focus_node_id=focus_node_id,
        retrieval_substrate=plan["retrieval_substrate"],
        support_bundle=plan["rehydration_bundle"],
        prompt_planes=prompt_planes,
        expected_support_node_ids=["ctn_000004", "ctn_000002"],
        forbidden_support_node_ids=["ctn_000003"],
        expected_artifact_refs=["retrieval_contract.md"],
        forbidden_artifact_refs=["batch_router.md"],
        wrong_neighbor_node_ids=["ctn_000003"],
        max_support_share=0.50,
    )

    assert result["schema_version"] == "ctree_eval_result_v1"
    assert result["system_id"] == "frozen_core"
    assert result["metrics"]["support_precision"] >= 0.0
    assert result["metrics"]["support_precision"] <= 1.0
    assert result["metrics"]["support_recall"] >= 0.0
    assert result["metrics"]["support_recall"] <= 1.0
    assert result["metrics"]["support_token_share"] > 0.0
    assert "structural" in result["candidate_counts_by_lane"]
    assert "active_path_node_ids" in result["prompt_plane_summary"]
    assert isinstance(result["outcome"]["pass"], bool)
    assert isinstance(result["outcome"]["fail_reasons"], list)
