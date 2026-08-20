from __future__ import annotations

from typing import Any, Callable, Dict, List

from .compiler import compile_ctree
from .gate_e import _prompt_eligible_support, _rough_token_count, _score_bundle
from .policy import build_rehydration_plan
from .store import CTreeStore


def build_closed_blocker_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Closed blocker canary"}, turn=1)
    blocker_id = store.record(
        "task",
        {
            "title": "Closed schema prerequisite",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["schema_spec.md"],
            "targets": ["ctrees/schema.py"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "targets": ["ctrees/compiler.py"],
        },
        turn=3,
    )
    return store


def build_validation_link_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Validation link canary"}, turn=1)
    validation_id = store.record(
        "task",
        {
            "title": "Validation evidence",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["validation_report.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Dependency validation lookup",
            "parent_id": root_id,
            "related_links": [{"type": "validates", "target": validation_id}],
            "targets": ["ctrees/compiler.py"],
        },
        turn=3,
    )
    return store


def build_external_blocker_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "External blocker canary"}, turn=1)
    store.record(
        "task",
        {
            "title": "Blocked API contract follow-up",
            "parent_id": root_id,
            "blocker_refs": ["dep-api-contract"],
            "targets": ["ctrees/compiler.py"],
        },
        turn=2,
    )
    return store


def _bundle_for_dependency_lookup(store_builder: Callable[[], Any], *, graph_enabled: bool) -> tuple[Dict[str, Any], Dict[str, Any]]:
    store = store_builder()
    prompt_planes = dict((compile_ctree(store).get("prompt_planes") or {}))
    bundle = build_rehydration_plan(store, mode="dependency_lookup", graph_enabled=graph_enabled).get("rehydration_bundle") or {}
    prompt_planes["support_bundle"] = dict(bundle)
    return _prompt_eligible_support(bundle), prompt_planes


def _comparison_result(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    expected_graph: List[str],
    max_support_share: float,
) -> Dict[str, Any]:
    graph_bundle, graph_envelope = _bundle_for_dependency_lookup(store_builder, graph_enabled=True)
    baseline_bundle, baseline_envelope = _bundle_for_dependency_lookup(store_builder, graph_enabled=False)
    graph_score = _score_bundle(graph_bundle, expected=expected_graph, forbidden=[])
    baseline_score = _score_bundle(baseline_bundle, expected=expected_graph, forbidden=[])
    graph_share = _rough_token_count(graph_bundle) / max(_rough_token_count(graph_envelope), 1)
    baseline_share = _rough_token_count(baseline_bundle) / max(_rough_token_count(baseline_envelope), 1)
    return {
        "scenario": scenario,
        "expected_graph": list(expected_graph),
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


def evaluate_blocker_graph_canary(*, max_support_share: float = 0.35) -> Dict[str, Any]:
    scenarios = [
        _comparison_result(
            scenario="closed_blocker_support",
            store_builder=build_closed_blocker_store,
            expected_graph=["schema_spec.md"],
            max_support_share=max_support_share,
        ),
        _comparison_result(
            scenario="validation_link_support",
            store_builder=build_validation_link_store,
            expected_graph=["validation_report.md"],
            max_support_share=max_support_share,
        ),
        _comparison_result(
            scenario="external_blocker_reference",
            store_builder=build_external_blocker_store,
            expected_graph=["dep-api-contract"],
            max_support_share=max_support_share,
        ),
    ]
    return {
        "schema_version": "ctree_blocker_graph_canary_v1",
        "family": "blocker_graph_dependency_lookup",
        "max_support_share": max_support_share,
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
