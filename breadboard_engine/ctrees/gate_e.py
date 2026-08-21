from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable, Dict, List

from .compiler import compile_ctree
from .microprobes import (
    build_false_neighbor_store,
    build_stale_verification_store,
    build_target_supersession_store,
    run_blocker_clearance_probe,
)


_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


def _rough_token_count(value: Any) -> int:
    try:
        blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except Exception:
        blob = str(value)
    return len(_TOKEN_RE.findall(blob))


def _prompt_eligible_support(bundle: Dict[str, Any] | None) -> Dict[str, Any]:
    source = bundle if isinstance(bundle, dict) else {}
    return {
        "support_node_ids": list(source.get("support_node_ids") or []),
        "constraints": [dict(item) for item in list(source.get("constraints") or []) if isinstance(item, dict)],
        "targets": list(source.get("targets") or []),
        "workspace_scope": list(source.get("workspace_scope") or []),
        "artifact_refs": list(source.get("artifact_refs") or []),
        "blocker_refs": list(source.get("blocker_refs") or []),
    }


def _stripped_planes(planes: Dict[str, Any]) -> Dict[str, Any]:
    stripped = copy.deepcopy(planes)
    stripped["support_bundle"] = {
        "schema_version": "ctree_support_bundle_v1",
        "mode": "stripped",
        "focus_node_id": None,
        "active_path_node_ids": [],
        "support_node_ids": [],
        "constraints": [],
        "targets": [],
        "workspace_scope": [],
        "artifact_refs": [],
        "blocker_refs": [],
        "candidate_provenance": [],
    }
    return stripped


def _flatten_bundle_terms(bundle: Dict[str, Any]) -> List[str]:
    terms: List[str] = []
    for item in list(bundle.get("targets") or []):
        text = str(item).strip()
        if text:
            terms.append(text)
    for item in list(bundle.get("workspace_scope") or []):
        text = str(item).strip()
        if text:
            terms.append(text)
    for item in list(bundle.get("artifact_refs") or []):
        text = str(item).strip()
        if text:
            terms.append(text)
    for item in list(bundle.get("blocker_refs") or []):
        text = str(item).strip()
        if text:
            terms.append(text)
    for constraint in list(bundle.get("constraints") or []):
        if not isinstance(constraint, dict):
            continue
        summary = str(constraint.get("summary") or "").strip()
        if summary:
            terms.append(summary)
    return terms


def _score_bundle(bundle: Dict[str, Any], *, expected: List[str], forbidden: List[str]) -> Dict[str, Any]:
    terms = _flatten_bundle_terms(bundle)
    term_set = set(terms)
    matched_expected = [item for item in expected if item in term_set]
    matched_forbidden = [item for item in forbidden if item in term_set]
    return {
        "matched_expected": matched_expected,
        "matched_forbidden": matched_forbidden,
        "score": len(matched_expected) - len(matched_forbidden),
    }


def _scenario_result(
    *,
    scenario: str,
    store_builder: Callable[[], Any],
    expected: List[str],
    forbidden: List[str],
    max_support_share: float,
) -> Dict[str, Any]:
    store = store_builder()
    compiled = compile_ctree(store)
    prompt_planes = dict(compiled.get("prompt_planes") or {})
    full_bundle = _prompt_eligible_support(prompt_planes.get("support_bundle"))
    stripped_planes = _stripped_planes(prompt_planes)
    stripped_bundle = _prompt_eligible_support(stripped_planes.get("support_bundle"))

    full_score = _score_bundle(full_bundle, expected=expected, forbidden=forbidden)
    stripped_score = _score_bundle(stripped_bundle, expected=expected, forbidden=forbidden)

    total_prompt_tokens = max(_rough_token_count(prompt_planes), 1)
    support_tokens = _rough_token_count(full_bundle)
    support_share = support_tokens / total_prompt_tokens

    return {
        "scenario": scenario,
        "expected": list(expected),
        "forbidden": list(forbidden),
        "full_bundle": full_bundle,
        "stripped_bundle": stripped_bundle,
        "full_score": full_score["score"],
        "stripped_score": stripped_score["score"],
        "delta": full_score["score"] - stripped_score["score"],
        "matched_expected": full_score["matched_expected"],
        "matched_forbidden": full_score["matched_forbidden"],
        "support_token_count": support_tokens,
        "total_prompt_token_count": total_prompt_tokens,
        "support_token_share": support_share,
        "support_share_within_budget": support_share <= max_support_share,
        "passed": (full_score["score"] > stripped_score["score"]) and not full_score["matched_forbidden"],
    }


def evaluate_gate_e(*, max_support_share: float = 0.45) -> Dict[str, Any]:
    value_scenarios = [
        _scenario_result(
            scenario="target_supersession",
            store_builder=build_target_supersession_store,
            expected=["active_target.py", "Use the new target only"],
            forbidden=["legacy_target.py"],
            max_support_share=max_support_share,
        ),
        _scenario_result(
            scenario="stale_verification_dominance",
            store_builder=build_stale_verification_store,
            expected=["verification_fresh.md", "Current verification result"],
            forbidden=["verification_old.md"],
            max_support_share=max_support_share,
        ),
        _scenario_result(
            scenario="false_neighbor_suppression",
            store_builder=build_false_neighbor_store,
            expected=["retrieval_contract.md", "ctrees/policy.py"],
            forbidden=["batch_router.md", "router/audit.py"],
            max_support_share=max_support_share,
        ),
    ]

    blocker_probe = run_blocker_clearance_probe()
    ready_operational = (
        bool(blocker_probe.get("blocked_needs_resolution"))
        and not bool(blocker_probe.get("cleared_needs_resolution"))
        and len(list(blocker_probe.get("cleared_ready_node_ids") or []))
        >= len(list(blocker_probe.get("blocked_ready_node_ids") or []))
    )

    return {
        "schema_version": "ctree_gate_e_v1",
        "max_support_share": max_support_share,
        "value_scenarios": value_scenarios,
        "value_pass_count": sum(1 for item in value_scenarios if bool(item.get("passed"))),
        "all_support_shares_within_budget": all(bool(item.get("support_share_within_budget")) for item in value_scenarios),
        "blocker_clearance_probe": blocker_probe,
        "ready_and_blocker_operational": ready_operational,
        "gate_e_pass": (
            any(int(item.get("delta") or 0) > 0 for item in value_scenarios)
            and all(bool(item.get("support_share_within_budget")) for item in value_scenarios)
            and ready_operational
        ),
    }
