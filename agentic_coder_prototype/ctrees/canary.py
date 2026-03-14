from __future__ import annotations

from typing import Any, Callable, Dict, List

from .compiler import compile_ctree
from .gate_e import _prompt_eligible_support, _rough_token_count, _score_bundle, _stripped_planes
from .microprobes import (
    build_false_neighbor_store,
    build_stale_verification_store,
    build_target_supersession_store,
)
from .store import CTreeStore
from .policy import build_rehydration_plan


def build_recovery_family_store() -> CTreeStore:
    store = CTreeStore()
    root_id = store.record(
        "objective",
        {
            "title": "Recovery family",
            "constraints": [{"summary": "Keep state deterministic", "scope": "global"}],
        },
        turn=1,
    )
    store.record(
        "task",
        {
            "title": "Blocked dependency check",
            "parent_id": root_id,
            "blocker_refs": ["dep-lane-matrix"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Continue architecture spec",
            "parent_id": root_id,
            "constraints": [{"summary": "Preserve deterministic reducers"}],
            "targets": ["ctrees/compiler.py"],
            "artifact_refs": ["architecture_spec.md"],
        },
        turn=3,
    )
    return store


def _support_bundle_for_mode(store_builder: Callable[[], Any], *, mode: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    store = store_builder()
    compiled = compile_ctree(store)
    prompt_planes = dict(compiled.get("prompt_planes") or {})
    if mode == "active_continuation":
        return _prompt_eligible_support(prompt_planes.get("support_bundle")), prompt_planes
    plan = build_rehydration_plan(store, mode=mode)
    envelope = dict(prompt_planes)
    envelope["support_bundle"] = dict(plan.get("rehydration_bundle") or {})
    return _prompt_eligible_support(plan.get("rehydration_bundle")), envelope


def _stripped_bundle() -> Dict[str, Any]:
    return _prompt_eligible_support(_stripped_planes({"support_bundle": {}}).get("support_bundle"))


def _family_scenario(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    mode: str,
    expected: List[str],
    forbidden: List[str],
    max_support_share: float,
    max_support_node_ids: int | None = None,
) -> Dict[str, Any]:
    full_bundle, prompt_envelope = _support_bundle_for_mode(store_builder, mode=mode)
    stripped_bundle = _stripped_bundle()
    full_score = _score_bundle(full_bundle, expected=expected, forbidden=forbidden)
    stripped_score = _score_bundle(stripped_bundle, expected=expected, forbidden=forbidden)
    support_token_count = _rough_token_count(full_bundle)
    total_prompt_token_count = max(_rough_token_count(prompt_envelope), 1)
    support_share = support_token_count / total_prompt_token_count
    support_node_ids = list(full_bundle.get("support_node_ids") or [])
    within_node_budget = True if max_support_node_ids is None else len(support_node_ids) <= max_support_node_ids
    return {
        "scenario": scenario,
        "mode": mode,
        "expected": list(expected),
        "forbidden": list(forbidden),
        "full_bundle": full_bundle,
        "stripped_bundle": stripped_bundle,
        "full_score": int(full_score.get("score") or 0),
        "stripped_score": int(stripped_score.get("score") or 0),
        "delta": int(full_score.get("score") or 0) - int(stripped_score.get("score") or 0),
        "matched_expected": list(full_score.get("matched_expected") or []),
        "matched_forbidden": list(full_score.get("matched_forbidden") or []),
        "support_token_count": support_token_count,
        "total_prompt_token_count": total_prompt_token_count,
        "support_token_share": support_share,
        "support_share_within_budget": support_share <= max_support_share,
        "support_node_count": len(support_node_ids),
        "support_node_budget": max_support_node_ids,
        "support_node_count_within_budget": within_node_budget,
        "passed": (
            int(full_score.get("score") or 0) > int(stripped_score.get("score") or 0)
            and not list(full_score.get("matched_forbidden") or [])
            and within_node_budget
            and support_share <= max_support_share
        ),
    }


def evaluate_recovery_family_canary(*, max_support_share: float = 0.60) -> Dict[str, Any]:
    scenarios = [
        _family_scenario(
            scenario="continuation_core",
            store_builder=build_recovery_family_store,
            mode="active_continuation",
            expected=["architecture_spec.md", "Preserve deterministic reducers"],
            forbidden=[],
            max_support_share=max_support_share,
        ),
        _family_scenario(
            scenario="resume_restore",
            store_builder=build_recovery_family_store,
            mode="resume",
            expected=["architecture_spec.md", "Preserve deterministic reducers"],
            forbidden=[],
            max_support_share=max_support_share,
        ),
        _family_scenario(
            scenario="pivot_minimal",
            store_builder=build_recovery_family_store,
            mode="pivot",
            expected=["architecture_spec.md", "Preserve deterministic reducers"],
            forbidden=[],
            max_support_share=max_support_share,
            max_support_node_ids=4,
        ),
        _family_scenario(
            scenario="target_supersession",
            store_builder=build_target_supersession_store,
            mode="active_continuation",
            expected=["active_target.py", "Use the new target only"],
            forbidden=["legacy_target.py"],
            max_support_share=max_support_share,
        ),
        _family_scenario(
            scenario="stale_verification_dominance",
            store_builder=build_stale_verification_store,
            mode="active_continuation",
            expected=["verification_fresh.md", "Current verification result"],
            forbidden=["verification_old.md"],
            max_support_share=max_support_share,
        ),
        _family_scenario(
            scenario="false_neighbor_suppression",
            store_builder=build_false_neighbor_store,
            mode="active_continuation",
            expected=["retrieval_contract.md", "ctrees/policy.py"],
            forbidden=["batch_router.md", "router/audit.py"],
            max_support_share=max_support_share,
        ),
    ]

    return {
        "schema_version": "ctree_recovery_family_canary_v1",
        "family": "tranche1_recovery_family",
        "max_support_share": max_support_share,
        "scenario_count": len(scenarios),
        "pass_count": sum(1 for item in scenarios if bool(item.get("passed"))),
        "all_passed": all(bool(item.get("passed")) for item in scenarios),
        "scenarios": scenarios,
    }
