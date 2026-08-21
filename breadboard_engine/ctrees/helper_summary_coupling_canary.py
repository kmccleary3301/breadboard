from __future__ import annotations

from typing import Any, Callable, Dict, List

from .gate_e import _rough_token_count
from .microprobes import build_summary_coupling_store
from .policy import build_rehydration_plan


def _score_bundle(
    bundle: Dict[str, Any],
    *,
    expected_nodes: List[str],
    forbidden_nodes: List[str],
) -> Dict[str, Any]:
    support_node_ids = {str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)}
    return {
        "score": len([item for item in expected_nodes if item in support_node_ids])
        - len([item for item in forbidden_nodes if item in support_node_ids]),
    }


def _comparison_result(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    expected_nodes: List[str],
    forbidden_nodes: List[str],
    max_support_share: float,
) -> Dict[str, Any]:
    baseline = build_rehydration_plan(store_builder(), mode="active_continuation", helper_enabled=True)
    coupled = build_rehydration_plan(
        store_builder(),
        mode="active_continuation",
        helper_enabled=True,
        helper_summary_coupling_enabled=True,
    )
    baseline_bundle = baseline.get("rehydration_bundle") or {}
    coupled_bundle = coupled.get("rehydration_bundle") or {}
    baseline_score = _score_bundle(baseline_bundle, expected_nodes=expected_nodes, forbidden_nodes=forbidden_nodes)
    coupled_score = _score_bundle(coupled_bundle, expected_nodes=expected_nodes, forbidden_nodes=forbidden_nodes)
    summary_tokens = _rough_token_count(coupled_bundle.get("summary_support") or [])
    support_surface = {
        "support_node_ids": coupled_bundle.get("support_node_ids") or [],
        "artifact_refs": coupled_bundle.get("artifact_refs") or [],
        "blocker_refs": coupled_bundle.get("blocker_refs") or [],
        "validations": coupled_bundle.get("validations") or [],
        "constraints": coupled_bundle.get("constraints") or [],
        "targets": coupled_bundle.get("targets") or [],
        "summary_support": coupled_bundle.get("summary_support") or [],
    }
    coupled_share = summary_tokens / max(_rough_token_count(support_surface), 1)
    return {
        "scenario": scenario,
        "baseline_bundle": baseline_bundle,
        "coupled_bundle": coupled_bundle,
        "baseline_score": int(baseline_score.get("score") or 0),
        "coupled_score": int(coupled_score.get("score") or 0),
        "delta": int(coupled_score.get("score") or 0) - int(baseline_score.get("score") or 0),
        "summary_support_count": len(list(coupled_bundle.get("summary_support") or [])),
        "coupled_support_share": coupled_share,
        "coupled_share_within_budget": coupled_share <= max_support_share,
        "passed": int(coupled_score.get("score") or 0) > int(baseline_score.get("score") or 0)
        and bool(coupled_bundle.get("summary_support"))
        and coupled_share <= max_support_share,
    }


def evaluate_helper_summary_coupling_canary(*, max_support_share: float = 0.75) -> Dict[str, Any]:
    scenarios = [
        _comparison_result(
            scenario="subtree_summary_guided_rehydration",
            store_builder=build_summary_coupling_store,
            expected_nodes=["ctn_000003", "ctn_000004"],
            forbidden_nodes=["ctn_000002"],
            max_support_share=max_support_share,
        ),
    ]
    return {
        "schema_version": "ctree_helper_summary_coupling_canary_v1",
        "family": "helper_summary_to_rehydration",
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
