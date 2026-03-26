from __future__ import annotations

from typing import Any, Callable, Dict, List

from .compiler import compile_ctree
from .gate_e import _prompt_eligible_support, _rough_token_count, _score_bundle
from .policy import build_rehydration_plan
from .store import CTreeStore


def build_validation_neighbor_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Validation neighbor canary"}, turn=1)
    validation_id = store.record(
        "evidence_bundle",
        {
            "title": "Schema validation packet",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["schema_validation.md"],
        },
        turn=2,
    )
    blocker_id = store.record(
        "task",
        {
            "title": "Schema prerequisite",
            "parent_id": root_id,
            "artifact_refs": ["schema_spec.md"],
            "related_links": [{"type": "validates", "target": validation_id}],
            "targets": ["ctrees/schema.py"],
        },
        turn=3,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "targets": ["ctrees/compiler.py"],
        },
        turn=4,
    )
    return store


def build_prerequisite_neighbor_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Prerequisite neighbor canary"}, turn=1)
    prerequisite_id = store.record(
        "task",
        {
            "title": "Token budget prerequisite",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["token_budget.md"],
            "targets": ["ctrees/policy.py"],
        },
        turn=2,
    )
    blocker_id = store.record(
        "task",
        {
            "title": "Retrieval lane prerequisite",
            "parent_id": root_id,
            "artifact_refs": ["retrieval_matrix.md"],
            "blocker_refs": [prerequisite_id],
            "targets": ["ctrees/policy.py"],
        },
        turn=3,
    )
    store.record(
        "task",
        {
            "title": "Blocked dependency lookup",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "targets": ["ctrees/compiler.py"],
        },
        turn=4,
    )
    return store


def build_supersession_neighbor_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Supersession neighbor canary"}, turn=1)
    replacement_id = store.record(
        "task",
        {
            "title": "Replacement schema contract",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["replacement_schema.md"],
            "targets": ["ctrees/schema.py"],
        },
        turn=2,
    )
    legacy_id = store.record(
        "task",
        {
            "title": "Legacy schema prerequisite",
            "parent_id": root_id,
            "artifact_refs": ["legacy_schema.md"],
            "related_links": [{"type": "supersedes", "target": replacement_id}],
            "targets": ["ctrees/schema.py"],
        },
        turn=3,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [legacy_id],
            "targets": ["ctrees/compiler.py"],
        },
        turn=4,
    )
    return store


def _bundle_for_dependency_lookup(
    store_builder: Callable[[], Any],
    *,
    graph_neighborhood_enabled: bool,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    store = store_builder()
    prompt_planes = dict((compile_ctree(store).get("prompt_planes") or {}))
    bundle = build_rehydration_plan(
        store,
        mode="dependency_lookup",
        graph_neighborhood_enabled=graph_neighborhood_enabled,
    ).get("rehydration_bundle") or {}
    prompt_planes["support_bundle"] = dict(bundle)
    return _prompt_eligible_support(bundle), prompt_planes


def _comparison_result(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    expected_graph: List[str],
    forbidden_graph: List[str],
    max_support_share: float,
) -> Dict[str, Any]:
    graph_bundle, graph_envelope = _bundle_for_dependency_lookup(store_builder, graph_neighborhood_enabled=True)
    baseline_bundle, baseline_envelope = _bundle_for_dependency_lookup(store_builder, graph_neighborhood_enabled=False)
    graph_score = _score_bundle(graph_bundle, expected=expected_graph, forbidden=forbidden_graph)
    baseline_score = _score_bundle(baseline_bundle, expected=expected_graph, forbidden=forbidden_graph)
    graph_share = _rough_token_count(graph_bundle) / max(_rough_token_count(graph_envelope), 1)
    baseline_share = _rough_token_count(baseline_bundle) / max(_rough_token_count(baseline_envelope), 1)
    return {
        "scenario": scenario,
        "expected_graph": list(expected_graph),
        "forbidden_graph": list(forbidden_graph),
        "graph_bundle": graph_bundle,
        "baseline_bundle": baseline_bundle,
        "graph_score": int(graph_score.get("score") or 0),
        "baseline_score": int(baseline_score.get("score") or 0),
        "delta": int(graph_score.get("score") or 0) - int(baseline_score.get("score") or 0),
        "graph_support_share": graph_share,
        "baseline_support_share": baseline_share,
        "graph_share_within_budget": graph_share <= max_support_share,
        "passed": int(graph_score.get("score") or 0) > int(baseline_score.get("score") or 0) and graph_share <= max_support_share,
    }


def evaluate_graph_neighborhood_canary(*, max_support_share: float = 0.38) -> Dict[str, Any]:
    scenarios = [
        _comparison_result(
            scenario="validation_neighbor_support",
            store_builder=build_validation_neighbor_store,
            expected_graph=["schema_validation.md"],
            forbidden_graph=[],
            max_support_share=max_support_share,
        ),
        _comparison_result(
            scenario="prerequisite_neighbor_support",
            store_builder=build_prerequisite_neighbor_store,
            expected_graph=["token_budget.md"],
            forbidden_graph=[],
            max_support_share=max_support_share,
        ),
        _comparison_result(
            scenario="supersession_neighbor_support",
            store_builder=build_supersession_neighbor_store,
            expected_graph=["replacement_schema.md"],
            forbidden_graph=["legacy_schema.md"],
            max_support_share=max_support_share,
        ),
    ]
    return {
        "schema_version": "ctree_graph_neighborhood_canary_v1",
        "family": "dependency_lookup_graph_neighborhood",
        "max_support_share": max_support_share,
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
