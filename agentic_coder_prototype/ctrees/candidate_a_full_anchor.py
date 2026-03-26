from __future__ import annotations

from typing import Any, Dict, List

from .candidate_a import build_candidate_a_plan
from .candidate_a_pilot import _candidate_a_vs_deterministic
from .compiler import compile_ctree
from .evaluation_baselines import build_deterministic_reranker_plan
from .evaluation_metrics import (
    build_empty_support_bundle,
    build_evaluation_prompt_planes,
    evaluate_support_result,
)
from .holdout_generalization_pack import build_phase10_full_holdout_scenarios
from .policy import build_rehydration_plan


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


def run_candidate_a_full_anchor() -> Dict[str, Any]:
    scenario_results: List[Dict[str, Any]] = []
    base_comparisons: List[Dict[str, Any]] = []

    for scenario in build_phase10_full_holdout_scenarios():
        systems: List[Dict[str, Any]] = []
        result_by_id: Dict[str, Dict[str, Any]] = {}
        for system_id in ("stripped_support", "frozen_core", "deterministic_reranker", "candidate_a"):
            result = _evaluate_system_run(scenario, _build_system_run(scenario, system_id))
            systems.append(result)
            result_by_id[system_id] = result
        comparison = _candidate_a_vs_deterministic(result_by_id["candidate_a"], result_by_id["deterministic_reranker"])
        scenario_item = {
            "scenario_id": str(scenario.get("scenario_id") or ""),
            "base_scenario_id": str(scenario.get("base_scenario_id") or ""),
            "family": str(scenario.get("family") or ""),
            "perturbation": str(scenario.get("perturbation") or ""),
            "mode": str(scenario.get("mode") or ""),
            "systems": systems,
            "candidate_a_vs_deterministic": comparison,
        }
        scenario_results.append(scenario_item)
        if str(scenario.get("perturbation") or "") == "base":
            base_comparisons.append(comparison)

    base_items = [item for item in scenario_results if item["perturbation"] == "base"]
    order_items = [item for item in scenario_results if item["perturbation"] == "order"]
    budget_items = [item for item in scenario_results if item["perturbation"] == "tight_budget"]
    family_names = sorted({item["family"] for item in base_items})

    def _pass_rate(items: List[Dict[str, Any]]) -> float:
        if not items:
            return 0.0
        count = 0
        for item in items:
            result = next(x for x in item["systems"] if x["system_id"] == "candidate_a")
            if bool((result.get("outcome") or {}).get("pass")):
                count += 1
        return count / len(items)

    family_deltas: Dict[str, Dict[str, float]] = {}
    for family in family_names:
        comps = [item["candidate_a_vs_deterministic"] for item in base_items if item["family"] == family]
        family_deltas[family] = {
            "support_token_share": sum(float(item["delta"]["support_token_share"]) for item in comps),
            "wrong_neighbor_rate": sum(float(item["delta"]["wrong_neighbor_rate"]) for item in comps),
            "stale_support_inclusion_rate": sum(float(item["delta"]["stale_support_inclusion_rate"]) for item in comps),
            "scenario_score": sum(float(item["delta"]["scenario_score"]) for item in comps),
        }

    win_count = sum(1 for item in base_comparisons if item["comparison_kind"] == "win")
    loss_count = sum(1 for item in base_comparisons if item["comparison_kind"] == "loss")
    tie_count = sum(1 for item in base_comparisons if item["comparison_kind"] == "tie")
    candidate_a_base_pass_count = sum(
        1 for item in base_items if bool((next(x for x in item["systems"] if x["system_id"] == "candidate_a").get("outcome") or {}).get("pass"))
    )
    deterministic_base_pass_count = sum(
        1
        for item in base_items
        if bool((next(x for x in item["systems"] if x["system_id"] == "deterministic_reranker").get("outcome") or {}).get("pass"))
    )

    return {
        "schema_version": "phase11_candidate_a_full_anchor_v1",
        "scenario_count": len(scenario_results),
        "base_scenario_count": len(base_items),
        "order_perturbation_count": len(order_items),
        "tight_budget_perturbation_count": len(budget_items),
        "families": family_names,
        "scenarios": scenario_results,
        "summary": {
            "candidate_a_vs_deterministic_wins": win_count,
            "candidate_a_vs_deterministic_losses": loss_count,
            "candidate_a_vs_deterministic_ties": tie_count,
            "candidate_a_base_pass_count": candidate_a_base_pass_count,
            "deterministic_base_pass_count": deterministic_base_pass_count,
            "candidate_a_order_pass_rate": _pass_rate(order_items),
            "candidate_a_tight_budget_pass_rate": _pass_rate(budget_items),
            "family_deltas": family_deltas,
            "passes_full_gate": bool(
                candidate_a_base_pass_count >= 10
                and _pass_rate(order_items) >= 0.5
                and _pass_rate(budget_items) >= 0.5
                and all(float(values["scenario_score"]) >= 0.0 for values in family_deltas.values())
            ),
        },
    }
