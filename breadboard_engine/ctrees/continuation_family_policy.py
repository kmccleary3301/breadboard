from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _trim_list(values: Iterable[Any], limit: int) -> List[Any]:
    items = list(values or [])
    if limit <= 0:
        return []
    return items[:limit]


def continuation_family_policy_for_base_task(base_task_id: str, *, variant: str = "v1") -> Dict[str, Any]:
    task_id = str(base_task_id or "").strip()
    policies = {
        "downstream_interrupt_repair_v1": {
            "policy_id": "interrupt_repair_balanced_v1",
            "support_node_cap": 2,
            "candidate_cap": 2,
            "validation_cap": 2,
            "blocker_cap": 2,
            "workspace_scope_cap": 4,
            "note": (
                "Interrupted continuation repair benefits from slightly broader recovery context: "
                "keep the primary target plus one supporting blocker or validation clue."
            ),
        },
        "downstream_dependency_shift_v1": {
            "policy_id": "dependency_shift_validation_preserving_v1",
            "support_node_cap": 2,
            "candidate_cap": 2,
            "validation_cap": 2,
            "blocker_cap": 2,
            "workspace_scope_cap": 4,
            "note": (
                "Dependency-shift continuation keeps one extra validation-support item so stale dependency assumptions "
                "can be corrected without carrying the full bundle."
            ),
        },
        "downstream_semantic_pivot_v1": {
            "policy_id": "semantic_pivot_sparse_v1",
            "support_node_cap": 1,
            "candidate_cap": 1,
            "validation_cap": 1,
            "blocker_cap": 1,
            "workspace_scope_cap": 3,
            "note": (
                "Semantic pivot stays sparse to suppress stale neighboring context and keep the active target dominant."
            ),
        },
        "downstream_subtree_pressure_v1": {
            "policy_id": "subtree_pressure_capped_v1",
            "support_node_cap": 1,
            "candidate_cap": 1,
            "validation_cap": 1,
            "blocker_cap": 1,
            "workspace_scope_cap": 3,
            "note": (
                "Subtree-pressure continuation stays tightly capped so sibling spillover does not crowd out the target child."
            ),
        },
    }
    if variant == "v1b":
        policies["downstream_dependency_shift_v1"] = {
            "policy_id": "dependency_shift_validation_sparse_v1b",
            "support_node_cap": 1,
            "candidate_cap": 1,
            "validation_cap": 2,
            "blocker_cap": 2,
            "workspace_scope_cap": 4,
            "note": (
                "Dependency-shift continuation keeps validation and blocker recovery broad, "
                "but narrows the explicit support bundle to one primary continuation target."
            ),
        }
    return dict(
        policies.get(
            task_id,
            {
                "policy_id": "default_balanced_v1",
                "support_node_cap": 2,
                "candidate_cap": 2,
                "validation_cap": 2,
                "blocker_cap": 2,
                "workspace_scope_cap": 4,
                "note": "Default balanced continuation policy.",
            },
        )
    )


def apply_continuation_family_policy(
    *,
    bundle: Dict[str, Any],
    base_task_id: str,
    variant: str = "v1",
) -> Dict[str, Any]:
    policy = continuation_family_policy_for_base_task(base_task_id, variant=variant)
    shaped = dict(bundle or {})
    shaped["support_node_ids"] = [
        str(item)
        for item in _trim_list(bundle.get("support_node_ids") or [], int(policy.get("support_node_cap") or 1))
        if str(item)
    ]
    shaped["candidate_provenance"] = [
        dict(item)
        for item in _trim_list(bundle.get("candidate_provenance") or [], int(policy.get("candidate_cap") or 1))
        if isinstance(item, dict)
    ]
    shaped["validations"] = [
        str(item)
        for item in _trim_list(bundle.get("validations") or [], int(policy.get("validation_cap") or 1))
        if str(item)
    ]
    shaped["blocker_refs"] = [
        str(item)
        for item in _trim_list(bundle.get("blocker_refs") or [], int(policy.get("blocker_cap") or 1))
        if str(item)
    ]
    shaped["workspace_scope"] = [
        str(item)
        for item in _trim_list(bundle.get("workspace_scope") or [], int(policy.get("workspace_scope_cap") or 1))
        if str(item)
    ]
    shaped["phase19_policy_id"] = str(policy.get("policy_id") or "")
    shaped["phase19_policy_note"] = str(policy.get("note") or "")
    return shaped
