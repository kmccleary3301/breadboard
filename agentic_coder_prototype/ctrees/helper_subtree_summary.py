from __future__ import annotations

from typing import Any, Dict, List


def build_helper_subtree_summary_input(
    *,
    parent_node: Dict[str, Any],
    parent_reduction: Dict[str, Any],
    child_states: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema_version": "ctree_helper_subtree_summary_input_v1",
        "parent_node_id": str(parent_node.get("id") or parent_reduction.get("node_id") or ""),
        "parent_title": str(parent_node.get("title") or parent_reduction.get("title") or ""),
        "parent_reduction": dict(parent_reduction or {}),
        "child_states": [dict(item) for item in child_states],
    }


def build_helper_subtree_summary_proposal(helper_input: Dict[str, Any]) -> Dict[str, Any]:
    parent_reduction = dict(helper_input.get("parent_reduction") or {})
    child_states = [dict(item) for item in list(helper_input.get("child_states") or []) if isinstance(item, dict)]
    ranked: List[Dict[str, Any]] = []

    for child in child_states:
        node_id = str(child.get("node_id") or "")
        status = str(child.get("status") or "")
        score = 0
        reasons: List[str] = []

        if status == "blocked":
            score += 50
            reasons.append("blocked_priority")
        if status == "active":
            score += 40
            reasons.append("active_priority")
        if status in {"done", "frozen"}:
            score += 20
            reasons.append("done_context")
        if status in {"superseded", "abandoned", "archived"}:
            score -= 45
            reasons.append("stale_penalty")

        if list(child.get("blocker_refs") or []):
            score += 25
            reasons.append("blocker_signal")
        if list(child.get("artifact_refs") or []):
            score += 20
            reasons.append("artifact_signal")
        if list(child.get("targets") or []):
            score += 15
            reasons.append("target_signal")
        if bool(child.get("final_spec_present")):
            score += 10
            reasons.append("final_spec_signal")

        ranked.append(
            {
                "node_id": node_id,
                "title": str(child.get("title") or ""),
                "status": status,
                "helper_score": score,
                "helper_reasons": reasons,
                "artifact_refs": list(child.get("artifact_refs") or []),
                "targets": list(child.get("targets") or []),
                "blocker_refs": list(child.get("blocker_refs") or []),
            }
        )

    ranked.sort(key=lambda item: (-int(item.get("helper_score") or 0), str(item.get("node_id") or "")))
    selected = [item for item in ranked if int(item.get("helper_score") or 0) > 0][:2]

    header_bits: List[str] = []
    support_bits: List[str] = []
    if selected:
        titles = [str(item.get("title") or "") for item in selected if str(item.get("title") or "")]
        if titles:
            header_bits.append(", ".join(titles[:2]))
        statuses = [str(item.get("status") or "") for item in selected if str(item.get("status") or "")]
        if statuses:
            support_bits.append("statuses=" + ",".join(statuses))

    if list(parent_reduction.get("unresolved_blocker_set") or []):
        support_bits.append("blockers=" + ",".join(list(parent_reduction.get("unresolved_blocker_set") or [])[:3]))
    if list(parent_reduction.get("artifact_summary") or []):
        support_bits.append("artifacts=" + ",".join(list(parent_reduction.get("artifact_summary") or [])[:3]))
    if list(parent_reduction.get("active_target_set") or []):
        support_bits.append("targets=" + ",".join(list(parent_reduction.get("active_target_set") or [])[:3]))

    return {
        "schema_version": "ctree_helper_subtree_summary_proposal_v1",
        "parent_node_id": str(helper_input.get("parent_node_id") or ""),
        "header_summary": "; ".join(header_bits[:1]),
        "support_summary": "; ".join(support_bits[:3]),
        "selected_child_ids": [str(item.get("node_id") or "") for item in selected if str(item.get("node_id") or "")],
        "ranked_children": ranked,
        "grounding": {
            "selected_count": len(selected),
            "child_count": len(child_states),
        },
    }
