from __future__ import annotations

from typing import Any, Callable, Dict, List

from .compiler import compile_ctree
from .microprobes import build_subtree_summary_store


def _score_helper_summary(
    summary: Dict[str, Any],
    *,
    expected_nodes: List[str],
    forbidden_nodes: List[str],
    expected_terms: List[str],
    forbidden_terms: List[str],
) -> Dict[str, Any]:
    selected = {str(item) for item in list(summary.get("selected_child_ids") or []) if str(item)}
    blob = " ".join(
        [
            str(summary.get("header_summary") or ""),
            str(summary.get("support_summary") or ""),
        ]
    )
    return {
        "score": len([item for item in expected_nodes if item in selected])
        - len([item for item in forbidden_nodes if item in selected])
        + len([item for item in expected_terms if item in blob])
        - len([item for item in forbidden_terms if item in blob]),
    }


def _baseline_summary(parent_reduction: Dict[str, Any]) -> Dict[str, Any]:
    reduction = dict(parent_reduction or {})
    return {
        "selected_child_ids": [],
        "header_summary": str((reduction.get("aggregate_summary") or {}).get("title") or ""),
        "support_summary": " ".join(
            [
                ",".join(list(reduction.get("unresolved_blocker_set") or [])),
                ",".join(list(reduction.get("artifact_summary") or [])),
                ",".join(list(reduction.get("active_target_set") or [])),
            ]
        ).strip(),
    }


def _comparison_result(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    expected_nodes: List[str],
    forbidden_nodes: List[str],
    expected_terms: List[str],
    forbidden_terms: List[str],
) -> Dict[str, Any]:
    store = store_builder()
    baseline = compile_ctree(store)
    helper = compile_ctree(store, helper_summary_enabled=True)
    baseline_summary = _baseline_summary(((baseline.get("stages", {}).get("SPEC", {}).get("parent_reductions") or [None])[0]) or {})
    helper_summary = ((helper.get("stages", {}).get("SPEC", {}).get("helper_subtree_summaries") or [None])[0]) or {}
    baseline_score = _score_helper_summary(
        baseline_summary,
        expected_nodes=expected_nodes,
        forbidden_nodes=forbidden_nodes,
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
    )
    helper_score = _score_helper_summary(
        helper_summary,
        expected_nodes=expected_nodes,
        forbidden_nodes=forbidden_nodes,
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
    )
    return {
        "scenario": scenario,
        "baseline_summary": baseline_summary,
        "helper_summary": helper_summary,
        "baseline_score": int(baseline_score.get("score") or 0),
        "helper_score": int(helper_score.get("score") or 0),
        "delta": int(helper_score.get("score") or 0) - int(baseline_score.get("score") or 0),
        "passed": int(helper_score.get("score") or 0) > int(baseline_score.get("score") or 0),
    }


def evaluate_helper_subtree_summary_canary() -> Dict[str, Any]:
    scenarios = [
        _comparison_result(
            scenario="mixed_child_selection",
            store_builder=build_subtree_summary_store,
            expected_nodes=["ctn_000002", "ctn_000004"],
            forbidden_nodes=["ctn_000003"],
            expected_terms=["Blocked child", "Active child"],
            forbidden_terms=["Superseded child"],
        ),
    ]
    return {
        "schema_version": "ctree_helper_subtree_summary_canary_v1",
        "family": "helper_subtree_summary_selection",
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
