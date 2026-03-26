from __future__ import annotations

from typing import Any, Dict, List

from .candidate_a import build_candidate_a_plan
from .compiler import compile_ctree
from .downstream_task_pack import (
    build_phase11_downstream_pilot_tasks,
    resolve_phase11_downstream_base_scenario_id,
)
from .evaluation_baselines import build_deterministic_reranker_plan
from .evaluation_metrics import build_evaluation_prompt_planes, evaluate_support_result
from .holdout_generalization_pack import build_phase10_base_scenarios
from .practical_baseline_contract import build_phase11_practical_baseline_contract_status


def _scenario_by_base_id(base_scenario_id: str) -> Dict[str, Any]:
    for item in build_phase10_base_scenarios():
        if str(item.get("base_scenario_id") or "") == str(base_scenario_id):
            return item
    raise KeyError(f"missing base scenario {base_scenario_id}")


def _build_protocol_system_run(scenario: Dict[str, Any], system_id: str) -> Dict[str, Any]:
    store = scenario["store"]
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    compiled = compile_ctree(store, focus_node_id=focus_node_id)
    compiled_prompt_planes = dict(compiled.get("prompt_planes") or {})

    if system_id == "deterministic_reranker":
        plan = build_deterministic_reranker_plan(
            store,
            mode=mode,
            focus_node_id=focus_node_id,
        )
        bundle = dict(plan.get("rehydration_bundle") or {})
    elif system_id == "candidate_a":
        plan = build_candidate_a_plan(
            store,
            mode=mode,
            focus_node_id=focus_node_id,
        )
        bundle = dict(plan.get("rehydration_bundle") or {})
    else:
        raise KeyError(f"unsupported protocol system {system_id}")

    return {
        "system_id": system_id,
        "retrieval_substrate": dict(plan.get("retrieval_substrate") or {}),
        "support_bundle": bundle,
        "prompt_planes": build_evaluation_prompt_planes(compiled_prompt_planes, support_bundle=bundle),
    }


def build_downstream_trial_result(
    *,
    task: Dict[str, Any],
    scenario: Dict[str, Any],
    system_id: str,
    system_run: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if system_run is None:
        return {
            "schema_version": "phase11_downstream_trial_result_v1",
            "task_id": str(task.get("task_id") or ""),
            "family": str(task.get("family") or ""),
            "system_id": system_id,
            "execution_status": "pending_external_baseline",
            "linked_base_scenario_id": str(scenario.get("base_scenario_id") or ""),
            "outcome": {"task_success": None, "fail_reasons": ["external_baseline_not_executed"]},
            "metrics": {},
        }

    support_result = evaluate_support_result(
        store=scenario["store"],
        scenario_id=str(scenario.get("scenario_id") or ""),
        family=str(task.get("family") or ""),
        perturbation="downstream_proxy",
        system_id=system_id,
        baseline_id=None,
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
    counts = dict(support_result.get("counts") or {})
    metrics = dict(support_result.get("metrics") or {})
    fail_reasons = list((support_result.get("outcome") or {}).get("fail_reasons") or [])
    intervention_count = len(fail_reasons)
    task_success = bool((support_result.get("outcome") or {}).get("pass"))
    practical_value_score = float(metrics.get("scenario_score") or 0.0) - (0.25 * intervention_count)

    return {
        "schema_version": "phase11_downstream_trial_result_v1",
        "task_id": str(task.get("task_id") or ""),
        "family": str(task.get("family") or ""),
        "system_id": system_id,
        "execution_status": "executed_local_proxy",
        "linked_base_scenario_id": str(scenario.get("base_scenario_id") or ""),
        "required_signals": list(task.get("required_signals") or []),
        "outcome": {
            "task_success": task_success,
            "fail_reasons": fail_reasons,
        },
        "metrics": {
            "practical_value_score": practical_value_score,
            "intervention_count": intervention_count,
            "support_token_share": float(metrics.get("support_token_share") or 0.0),
            "support_token_count": int(counts.get("support_token_count") or 0),
            "total_prompt_token_count": int(counts.get("total_prompt_token_count") or 0),
            "scenario_score": float(metrics.get("scenario_score") or 0.0),
        },
        "support_bundle": dict(support_result.get("support_bundle") or {}),
    }


def _compare_downstream_trials(candidate: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    if str(candidate.get("execution_status") or "") != "executed_local_proxy":
        return {
            "candidate_system_id": str(candidate.get("system_id") or ""),
            "baseline_system_id": str(baseline.get("system_id") or ""),
            "task_id": str(candidate.get("task_id") or ""),
            "candidate_better": False,
            "comparison_kind": "unavailable",
            "delta": {},
        }

    candidate_success = bool((candidate.get("outcome") or {}).get("task_success"))
    baseline_success = bool((baseline.get("outcome") or {}).get("task_success"))
    candidate_metrics = dict(candidate.get("metrics") or {})
    baseline_metrics = dict(baseline.get("metrics") or {})
    support_share_delta = float(candidate_metrics.get("support_token_share") or 0.0) - float(
        baseline_metrics.get("support_token_share") or 0.0
    )
    score_delta = float(candidate_metrics.get("practical_value_score") or 0.0) - float(
        baseline_metrics.get("practical_value_score") or 0.0
    )

    candidate_better = False
    comparison_kind = "tie"
    if candidate_success and not baseline_success:
        candidate_better = True
        comparison_kind = "win"
    elif not candidate_success and baseline_success:
        comparison_kind = "loss"
    elif candidate_success and baseline_success:
        if score_delta >= 0.0 and support_share_delta < 0.0:
            candidate_better = True
            comparison_kind = "win"
        elif score_delta < 0.0 or support_share_delta > 0.0:
            comparison_kind = "loss"
    else:
        if score_delta > 0.0:
            candidate_better = True
            comparison_kind = "win"
        elif score_delta < 0.0:
            comparison_kind = "loss"

    return {
        "candidate_system_id": str(candidate.get("system_id") or ""),
        "baseline_system_id": str(baseline.get("system_id") or ""),
        "task_id": str(candidate.get("task_id") or ""),
        "candidate_better": candidate_better,
        "comparison_kind": comparison_kind,
        "delta": {
            "practical_value_score": score_delta,
            "support_token_share": support_share_delta,
        },
    }


def run_phase11_downstream_pilot_proxy() -> Dict[str, Any]:
    task_results: List[Dict[str, Any]] = []
    candidate_vs_deterministic: List[Dict[str, Any]] = []
    contract_status = build_phase11_practical_baseline_contract_status()
    flagship_cell = next(item for item in contract_status["cells"] if str(item.get("model_tier") or "") == "flagship")
    practical_status = str(flagship_cell.get("status") or "unknown")

    for task in build_phase11_downstream_pilot_tasks():
        scenario = _scenario_by_base_id(resolve_phase11_downstream_base_scenario_id(str(task.get("task_id") or "")))
        practical = build_downstream_trial_result(
            task=task,
            scenario=scenario,
            system_id="practical_baseline",
            system_run=None,
        )
        deterministic = build_downstream_trial_result(
            task=task,
            scenario=scenario,
            system_id="deterministic_reranker",
            system_run=_build_protocol_system_run(scenario, "deterministic_reranker"),
        )
        candidate = build_downstream_trial_result(
            task=task,
            scenario=scenario,
            system_id="candidate_a",
            system_run=_build_protocol_system_run(scenario, "candidate_a"),
        )
        comparison = _compare_downstream_trials(candidate, deterministic)
        candidate_vs_deterministic.append(comparison)
        task_results.append(
            {
                "task_id": str(task.get("task_id") or ""),
                "family": str(task.get("family") or ""),
                "linked_base_scenario_id": str(scenario.get("base_scenario_id") or ""),
                "systems": [practical, deterministic, candidate],
                "practical_baseline_contract": {
                    "cell_id": str(flagship_cell.get("cell_id") or ""),
                    "status": practical_status,
                    "missing_env": list(flagship_cell.get("missing_env") or []),
                },
                "candidate_a_vs_deterministic": comparison,
            }
        )

    return {
        "schema_version": "phase11_downstream_pilot_proxy_v1",
        "task_count": len(task_results),
        "tasks": task_results,
        "practical_baseline_contract": contract_status,
        "summary": {
            "candidate_a_vs_deterministic_wins": sum(
                1 for item in candidate_vs_deterministic if str(item.get("comparison_kind") or "") == "win"
            ),
            "candidate_a_vs_deterministic_losses": sum(
                1 for item in candidate_vs_deterministic if str(item.get("comparison_kind") or "") == "loss"
            ),
            "candidate_a_vs_deterministic_ties": sum(
                1 for item in candidate_vs_deterministic if str(item.get("comparison_kind") or "") == "tie"
            ),
            "practical_baseline_status": practical_status,
        },
    }
