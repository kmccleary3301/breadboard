from __future__ import annotations

from typing import Any, Callable, Dict, List

from .gate_e import _rough_token_count
from .microprobes import build_dense_retrieval_store
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
    dense = build_rehydration_plan(
        store_builder(),
        mode="active_continuation",
        dense_enabled=True,
        helper_enabled=True,
    )
    baseline_bundle = baseline.get("rehydration_bundle") or {}
    dense_bundle = dense.get("rehydration_bundle") or {}
    baseline_score = _score_bundle(baseline_bundle, expected_nodes=expected_nodes, forbidden_nodes=forbidden_nodes)
    dense_score = _score_bundle(dense_bundle, expected_nodes=expected_nodes, forbidden_nodes=forbidden_nodes)
    support_surface = {
        "support_node_ids": dense_bundle.get("support_node_ids") or [],
        "artifact_refs": dense_bundle.get("artifact_refs") or [],
        "blocker_refs": dense_bundle.get("blocker_refs") or [],
        "constraints": dense_bundle.get("constraints") or [],
        "targets": dense_bundle.get("targets") or [],
    }
    support_share = _rough_token_count(support_surface) / max(_rough_token_count(dense), 1)
    return {
        "scenario": scenario,
        "baseline_bundle": baseline_bundle,
        "dense_bundle": dense_bundle,
        "baseline_score": int(baseline_score.get("score") or 0),
        "dense_score": int(dense_score.get("score") or 0),
        "delta": int(dense_score.get("score") or 0) - int(baseline_score.get("score") or 0),
        "dense_support_share": support_share,
        "dense_share_within_budget": support_share <= max_support_share,
        "passed": int(dense_score.get("score") or 0) > int(baseline_score.get("score") or 0)
        and support_share <= max_support_share,
    }


def evaluate_dense_retrieval_canary(*, max_support_share: float = 0.45) -> Dict[str, Any]:
    scenarios = [
        _comparison_result(
            scenario="semantic_match_recovery",
            store_builder=build_dense_retrieval_store,
            expected_nodes=["ctn_000003", "ctn_000004"],
            forbidden_nodes=["ctn_000002"],
            max_support_share=max_support_share,
        ),
    ]
    return {
        "schema_version": "ctree_dense_retrieval_canary_v1",
        "family": "dense_semantic_retrieval",
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
