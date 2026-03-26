from __future__ import annotations

from typing import Any, Dict, List, Optional

from .candidate_a import build_candidate_a_plan
from .compiler import compile_ctree
from .evaluation_baselines import build_deterministic_reranker_plan
from .evaluation_metrics import (
    build_empty_support_bundle,
    build_evaluation_prompt_planes,
    evaluate_support_result,
)
from .holdout_generalization_pack import build_phase10_base_scenarios
from .policy import build_rehydration_plan


_CANDIDATE_A_BASE_IDS = {
    "pilot_resume_distractor_v1",
    "resume_blocker_context_v1",
    "continuation_workspace_scope_v1",
    "resume_focus_shift_v1",
    "pilot_semantic_pivot_v1",
    "pivot_compile_build_v1",
    "pivot_restore_resume_v1",
    "pivot_validation_check_v1",
}


def build_candidate_a_pilot_scenarios() -> List[Dict[str, Any]]:
    return [
        item
        for item in build_phase10_base_scenarios()
        if str(item.get("base_scenario_id") or "") in _CANDIDATE_A_BASE_IDS
    ]


def _build_system_run(scenario: Dict[str, Any], system_id: str) -> Dict[str, Any]:
    store = scenario["store"]
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    token_budget = scenario.get("token_budget")
    compiled = compile_ctree(store, focus_node_id=focus_node_id)
    compiled_prompt_planes = dict(compiled.get("prompt_planes") or {})

    if system_id == "stripped_support":
        core_plan = build_rehydration_plan(
            store,
            mode=mode,
            focus_node_id=focus_node_id,
            lane_profile="frozen_core",
            token_budget=token_budget,
        )
        stripped_bundle = build_empty_support_bundle(
            mode=mode,
            focus_node_id=core_plan.get("focus_node_id"),
            active_path_node_ids=(core_plan.get("retrieval_policy") or {}).get("active_path_node_ids") or [],
        )
        return {
            "system_id": system_id,
            "retrieval_substrate": dict(core_plan.get("retrieval_substrate") or {}),
            "support_bundle": stripped_bundle,
            "prompt_planes": build_evaluation_prompt_planes(compiled_prompt_planes, support_bundle=stripped_bundle),
        }

    if system_id == "frozen_core":
        core_plan = build_rehydration_plan(
            store,
            mode=mode,
            focus_node_id=focus_node_id,
            lane_profile="frozen_core",
            token_budget=token_budget,
        )
        bundle = dict(core_plan.get("rehydration_bundle") or {})
        return {
            "system_id": system_id,
            "retrieval_substrate": dict(core_plan.get("retrieval_substrate") or {}),
            "support_bundle": bundle,
            "prompt_planes": build_evaluation_prompt_planes(compiled_prompt_planes, support_bundle=bundle),
        }

    if system_id == "deterministic_reranker":
        reranker_plan = build_deterministic_reranker_plan(
            store,
            mode=mode,
            token_budget=token_budget,
            focus_node_id=focus_node_id,
        )
        bundle = dict(reranker_plan.get("rehydration_bundle") or {})
        return {
            "system_id": system_id,
            "retrieval_substrate": dict(reranker_plan.get("retrieval_substrate") or {}),
            "support_bundle": bundle,
            "prompt_planes": build_evaluation_prompt_planes(compiled_prompt_planes, support_bundle=bundle),
            "plan": reranker_plan,
        }

    candidate_a_plan = build_candidate_a_plan(
        store,
        mode=mode,
        token_budget=token_budget,
        focus_node_id=focus_node_id,
    )
    bundle = dict(candidate_a_plan.get("rehydration_bundle") or {})
    return {
        "system_id": system_id,
        "retrieval_substrate": dict(candidate_a_plan.get("retrieval_substrate") or {}),
        "support_bundle": bundle,
        "prompt_planes": build_evaluation_prompt_planes(compiled_prompt_planes, support_bundle=bundle),
        "plan": candidate_a_plan,
    }


def _evaluate_system_run(scenario: Dict[str, Any], system_run: Dict[str, Any]) -> Dict[str, Any]:
    return evaluate_support_result(
        store=scenario["store"],
        scenario_id=str(scenario.get("scenario_id") or ""),
        family=str(scenario.get("family") or ""),
        perturbation=str(scenario.get("perturbation") or "base"),
        system_id=str(system_run.get("system_id") or ""),
        baseline_id="stripped_support" if str(system_run.get("system_id") or "") != "stripped_support" else None,
        mode=str(scenario.get("mode") or "active_continuation"),
        focus_node_id=scenario.get("focus_node_id"),
        retrieval_substrate=dict(system_run.get("retrieval_substrate") or {}),
        support_bundle=dict(system_run.get("support_bundle") or {}),
        prompt_planes=dict(system_run.get("prompt_planes") or {}),
        expected_support_node_ids=scenario.get("expected_support_node_ids"),
        forbidden_support_node_ids=scenario.get("forbidden_support_node_ids"),
        expected_artifact_refs=scenario.get("expected_artifact_refs"),
        forbidden_artifact_refs=scenario.get("forbidden_artifact_refs"),
        expected_blocker_refs=scenario.get("expected_blocker_refs"),
        expected_validation_ids=scenario.get("expected_validation_ids"),
        wrong_neighbor_node_ids=scenario.get("wrong_neighbor_node_ids"),
        max_support_share=float(scenario.get("max_support_share") or 0.30),
    )


def _candidate_a_vs_deterministic(candidate: Dict[str, Any], deterministic: Dict[str, Any]) -> Dict[str, Any]:
    candidate_pass = bool((candidate.get("outcome") or {}).get("pass"))
    deterministic_pass = bool((deterministic.get("outcome") or {}).get("pass"))
    candidate_metrics = dict(candidate.get("metrics") or {})
    deterministic_metrics = dict(deterministic.get("metrics") or {})
    candidate_counts = dict(candidate.get("counts") or {})
    deterministic_counts = dict(deterministic.get("counts") or {})

    support_share_delta = float(candidate_metrics.get("support_token_share") or 0.0) - float(
        deterministic_metrics.get("support_token_share") or 0.0
    )
    support_count_delta = int(candidate_counts.get("promoted_support_count") or 0) - int(
        deterministic_counts.get("promoted_support_count") or 0
    )
    wrong_neighbor_delta = float(candidate_metrics.get("wrong_neighbor_rate") or 0.0) - float(
        deterministic_metrics.get("wrong_neighbor_rate") or 0.0
    )
    stale_delta = float(candidate_metrics.get("stale_support_inclusion_rate") or 0.0) - float(
        deterministic_metrics.get("stale_support_inclusion_rate") or 0.0
    )
    scenario_score_delta = float(candidate_metrics.get("scenario_score") or 0.0) - float(
        deterministic_metrics.get("scenario_score") or 0.0
    )

    candidate_better = False
    comparison_kind = "tie"
    if candidate_pass and not deterministic_pass:
        candidate_better = True
        comparison_kind = "win"
    elif not candidate_pass and deterministic_pass:
        comparison_kind = "loss"
    elif candidate_pass and deterministic_pass:
        if (
            scenario_score_delta >= 0.0
            and wrong_neighbor_delta <= 0.0
            and stale_delta <= 0.0
            and support_count_delta <= 0
            and support_share_delta <= -0.005
        ):
            candidate_better = True
            comparison_kind = "win"
        elif scenario_score_delta < 0.0 or wrong_neighbor_delta > 0.0 or stale_delta > 0.0 or support_count_delta > 0:
            comparison_kind = "loss"
    else:
        if scenario_score_delta > 0.0:
            candidate_better = True
            comparison_kind = "win"
        elif scenario_score_delta < 0.0:
            comparison_kind = "loss"

    return {
        "candidate_system_id": "candidate_a",
        "baseline_system_id": "deterministic_reranker",
        "scenario_id": str(candidate.get("scenario_id") or ""),
        "comparison_kind": comparison_kind,
        "candidate_better": candidate_better,
        "delta": {
            "support_token_share": support_share_delta,
            "promoted_support_count": support_count_delta,
            "wrong_neighbor_rate": wrong_neighbor_delta,
            "stale_support_inclusion_rate": stale_delta,
            "scenario_score": scenario_score_delta,
        },
    }


def run_candidate_a_pilot() -> Dict[str, Any]:
    scenario_results: List[Dict[str, Any]] = []
    comparison_kinds: List[str] = []
    family_deltas: Dict[str, Dict[str, float]] = {}

    for scenario in build_candidate_a_pilot_scenarios():
        result_by_system: Dict[str, Dict[str, Any]] = {}
        systems: List[Dict[str, Any]] = []
        for system_id in ("stripped_support", "frozen_core", "deterministic_reranker", "candidate_a"):
            system_run = _build_system_run(scenario, system_id)
            result = _evaluate_system_run(scenario, system_run)
            if "plan" in system_run:
                if system_id == "candidate_a":
                    result["candidate_a_proposal"] = dict((system_run["plan"] or {}).get("candidate_a_proposal") or {})
                if system_id == "deterministic_reranker":
                    result["reranker_proposal"] = dict((system_run["plan"] or {}).get("reranker_proposal") or {})
            systems.append(result)
            result_by_system[system_id] = result

        comparison = _candidate_a_vs_deterministic(
            result_by_system["candidate_a"],
            result_by_system["deterministic_reranker"],
        )
        comparison_kinds.append(str(comparison.get("comparison_kind") or "tie"))
        family = str(scenario.get("family") or "")
        family_deltas.setdefault(
            family,
            {"support_token_share": 0.0, "wrong_neighbor_rate": 0.0, "stale_support_inclusion_rate": 0.0},
        )
        family_deltas[family]["support_token_share"] += float(comparison["delta"]["support_token_share"])
        family_deltas[family]["wrong_neighbor_rate"] += float(comparison["delta"]["wrong_neighbor_rate"])
        family_deltas[family]["stale_support_inclusion_rate"] += float(comparison["delta"]["stale_support_inclusion_rate"])
        scenario_results.append(
            {
                "scenario_id": str(scenario.get("scenario_id") or ""),
                "base_scenario_id": str(scenario.get("base_scenario_id") or ""),
                "family": family,
                "mode": str(scenario.get("mode") or ""),
                "systems": systems,
                "candidate_a_vs_deterministic": comparison,
            }
        )

    win_count = sum(1 for item in comparison_kinds if item == "win")
    loss_count = sum(1 for item in comparison_kinds if item == "loss")
    tie_count = sum(1 for item in comparison_kinds if item == "tie")
    return {
        "schema_version": "phase11_candidate_a_pilot_v1",
        "scenario_count": len(scenario_results),
        "families": sorted({str(item.get("family") or "") for item in scenario_results}),
        "scenarios": scenario_results,
        "summary": {
            "win_count_vs_deterministic": win_count,
            "loss_count_vs_deterministic": loss_count,
            "tie_count_vs_deterministic": tie_count,
            "family_deltas": family_deltas,
            "passes_gate": bool(win_count >= 3 and loss_count <= 1),
        },
    }
