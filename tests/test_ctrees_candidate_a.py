from __future__ import annotations

from agentic_coder_prototype.ctrees.candidate_a import build_candidate_a_plan
from agentic_coder_prototype.ctrees.evaluation_baselines import build_deterministic_reranker_plan
from agentic_coder_prototype.ctrees.holdout_generalization_pack import build_phase10_base_scenarios


def _scenario_by_id(base_scenario_id: str):
    for item in build_phase10_base_scenarios():
        if item["base_scenario_id"] == base_scenario_id:
            return item
    raise AssertionError(f"missing scenario {base_scenario_id}")


def test_candidate_a_matches_deterministic_selection_but_packs_less_artifact_surface() -> None:
    scenario = _scenario_by_id("pilot_resume_distractor_v1")
    candidate_a = build_candidate_a_plan(
        scenario["store"],
        mode=scenario["mode"],
        focus_node_id=scenario["focus_node_id"],
    )
    deterministic = build_deterministic_reranker_plan(
        scenario["store"],
        mode=scenario["mode"],
        focus_node_id=scenario["focus_node_id"],
    )

    candidate_bundle = candidate_a["rehydration_bundle"]
    deterministic_bundle = deterministic["rehydration_bundle"]

    assert candidate_a["candidate_a_proposal"]["selected_support_node_ids"] == deterministic["reranker_proposal"][
        "selected_support_node_ids"
    ]
    assert candidate_bundle["support_node_ids"] == deterministic_bundle["support_node_ids"]
    assert candidate_bundle["constraints"] == []
    assert candidate_bundle["artifact_refs"] == ["resume_followup.md"]
    assert deterministic_bundle["artifact_refs"] == ["resume_followup.md", "resume_metrics_contract.md"]


def test_candidate_a_adds_direct_dependency_satellites_when_blocker_semantics_require_them() -> None:
    scenario = _scenario_by_id("pilot_dependency_noise_v1")
    candidate_a = build_candidate_a_plan(
        scenario["store"],
        mode=scenario["mode"],
        focus_node_id=scenario["focus_node_id"],
    )
    bundle = candidate_a["rehydration_bundle"]

    assert bundle["support_node_ids"] == ["ctn_000006", "ctn_000005", "ctn_000002", "ctn_000003"]
    assert bundle["validations"] == ["ctn_000002"]
    assert "compiler_schema_validation.md" in bundle["artifact_refs"]


def test_candidate_a_allows_one_extra_strong_subtree_support_slot() -> None:
    scenario = _scenario_by_id("pilot_subtree_salience_v1")
    candidate_a = build_candidate_a_plan(
        scenario["store"],
        mode=scenario["mode"],
        focus_node_id=scenario["focus_node_id"],
    )

    assert candidate_a["candidate_a_proposal"]["selected_limit"] == 3
    assert candidate_a["rehydration_bundle"]["support_node_ids"] == ["ctn_000004", "ctn_000002", "ctn_000003"]
