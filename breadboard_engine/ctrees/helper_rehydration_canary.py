from __future__ import annotations

from typing import Any, Callable, Dict, List

from .compiler import compile_ctree
from .gate_e import _prompt_eligible_support, _rough_token_count, _score_bundle
from .microprobes import build_false_neighbor_store, build_graph_neighborhood_store, build_probe_store
from .policy import build_rehydration_plan


def _score_helper_bundle(
    bundle: Dict[str, Any],
    *,
    expected_terms: List[str],
    forbidden_terms: List[str],
    expected_nodes: List[str],
    forbidden_nodes: List[str],
) -> Dict[str, Any]:
    term_score = _score_bundle(bundle, expected=expected_terms, forbidden=forbidden_terms)
    support_node_ids = {str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)}
    matched_expected_nodes = [item for item in expected_nodes if item in support_node_ids]
    matched_forbidden_nodes = [item for item in forbidden_nodes if item in support_node_ids]
    return {
        "matched_expected_terms": term_score.get("matched_expected") or [],
        "matched_forbidden_terms": term_score.get("matched_forbidden") or [],
        "matched_expected_nodes": matched_expected_nodes,
        "matched_forbidden_nodes": matched_forbidden_nodes,
        "score": int(term_score.get("score") or 0) + len(matched_expected_nodes) - len(matched_forbidden_nodes),
    }


def _bundle_for_mode(
    store_builder: Callable[[], Any],
    *,
    mode: str,
    helper_enabled: bool,
    graph_neighborhood_enabled: bool = False,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    store = store_builder()
    prompt_planes = dict((compile_ctree(store).get("prompt_planes") or {}))
    plan = build_rehydration_plan(
        store,
        mode=mode,
        helper_enabled=helper_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
    )
    bundle = plan.get("rehydration_bundle") or {}
    prompt_planes["support_bundle"] = dict(bundle)
    return _prompt_eligible_support(bundle), prompt_planes, plan


def _comparison_result(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    mode: str,
    expected_terms: List[str],
    forbidden_terms: List[str],
    expected_nodes: List[str],
    forbidden_nodes: List[str],
    max_support_share: float,
    graph_neighborhood_enabled: bool = False,
) -> Dict[str, Any]:
    helper_bundle, helper_envelope, helper_plan = _bundle_for_mode(
        store_builder,
        mode=mode,
        helper_enabled=True,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
    )
    baseline_bundle, baseline_envelope, _ = _bundle_for_mode(
        store_builder,
        mode=mode,
        helper_enabled=False,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
    )
    helper_score = _score_helper_bundle(
        helper_bundle,
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
        expected_nodes=expected_nodes,
        forbidden_nodes=forbidden_nodes,
    )
    baseline_score = _score_helper_bundle(
        baseline_bundle,
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
        expected_nodes=expected_nodes,
        forbidden_nodes=forbidden_nodes,
    )
    helper_share = _rough_token_count(helper_bundle) / max(_rough_token_count(helper_envelope), 1)
    baseline_share = _rough_token_count(baseline_bundle) / max(_rough_token_count(baseline_envelope), 1)
    proposal = helper_plan.get("helper_proposal") or {}
    return {
        "scenario": scenario,
        "helper_bundle": helper_bundle,
        "baseline_bundle": baseline_bundle,
        "helper_score": int(helper_score.get("score") or 0),
        "baseline_score": int(baseline_score.get("score") or 0),
        "delta": int(helper_score.get("score") or 0) - int(baseline_score.get("score") or 0),
        "helper_support_share": helper_share,
        "baseline_support_share": baseline_share,
        "helper_share_within_budget": helper_share <= max_support_share,
        "proposal_selected_ids": list(proposal.get("selected_support_node_ids") or []),
        "proposal_decision_count": len(list(proposal.get("decisions") or [])),
        "passed": int(helper_score.get("score") or 0) > int(baseline_score.get("score") or 0)
        and helper_share <= max_support_share,
    }


def evaluate_helper_rehydration_canary(*, max_support_share: float = 0.30) -> Dict[str, Any]:
    scenarios = [
        _comparison_result(
            scenario="resume_focus_pruning",
            store_builder=build_probe_store,
            mode="resume",
            expected_terms=["retrieval_matrix.md", "Keep state deterministic"],
            forbidden_terms=[],
            expected_nodes=["ctn_000003"],
            forbidden_nodes=["ctn_000001", "ctn_000002"],
            max_support_share=max_support_share,
        ),
        _comparison_result(
            scenario="false_neighbor_pruning",
            store_builder=build_false_neighbor_store,
            mode="active_continuation",
            expected_terms=["retrieval_contract.md", "ctrees/policy.py"],
            forbidden_terms=[],
            expected_nodes=["ctn_000004", "ctn_000002"],
            forbidden_nodes=["ctn_000001", "ctn_000003"],
            max_support_share=max_support_share,
        ),
        _comparison_result(
            scenario="dependency_lookup_grounding",
            store_builder=build_graph_neighborhood_store,
            mode="dependency_lookup",
            expected_terms=["schema_validation.md", "replacement_schema.md"],
            forbidden_terms=[],
            expected_nodes=["ctn_000006", "ctn_000005"],
            forbidden_nodes=["ctn_000001", "ctn_000003"],
            max_support_share=max_support_share,
            graph_neighborhood_enabled=True,
        ),
    ]
    return {
        "schema_version": "ctree_helper_rehydration_canary_v1",
        "family": "helper_rehydration_selection",
        "max_support_share": max_support_share,
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
