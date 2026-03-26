from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .store import CTreeStore


_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_STALE_STATUSES = {"superseded", "abandoned", "archived"}


def _rough_token_count(value: Any) -> int:
    try:
        blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except Exception:
        blob = str(value)
    return len(_TOKEN_RE.findall(blob))


def _node_by_id(store: CTreeStore, node_id: str) -> Optional[Dict[str, Any]]:
    mapping = getattr(store, "_node_by_id", None)
    if isinstance(mapping, dict) and node_id in mapping:
        value = mapping.get(node_id)
        if isinstance(value, dict):
            return value
    for node in list(getattr(store, "nodes", []) or []):
        if isinstance(node, dict) and str(node.get("id") or "") == str(node_id):
            return node
    return None


def _normalize_id_list(values: Optional[Iterable[Any]]) -> List[str]:
    seen = set()
    items: List[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _normalize_constraint_list(values: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()
    for value in list(values or []):
        if not isinstance(value, dict):
            continue
        key = (
            str(value.get("constraint_id") or "").strip(),
            str(value.get("summary") or "").strip(),
            str(value.get("scope") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        items.append(dict(value))
    return items


def _prompt_eligible_support(bundle: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = dict(bundle or {})
    return {
        "support_node_ids": _normalize_id_list(source.get("support_node_ids")),
        "constraints": _normalize_constraint_list(source.get("constraints")),
        "targets": _normalize_id_list(source.get("targets")),
        "workspace_scope": _normalize_id_list(source.get("workspace_scope")),
        "artifact_refs": _normalize_id_list(source.get("artifact_refs")),
        "blocker_refs": _normalize_id_list(source.get("blocker_refs")),
        "validations": _normalize_id_list(source.get("validations")),
        "graph_neighbor_ids": _normalize_id_list(source.get("graph_neighbor_ids")),
        "summary_support": copy.deepcopy(source.get("summary_support") or []),
    }


def _support_surface_has_content(bundle: Dict[str, Any]) -> bool:
    return any(
        bool(bundle.get(key))
        for key in (
            "support_node_ids",
            "constraints",
            "targets",
            "workspace_scope",
            "artifact_refs",
            "blocker_refs",
            "validations",
            "graph_neighbor_ids",
            "summary_support",
        )
    )


def build_empty_support_bundle(*, mode: str, focus_node_id: Optional[str], active_path_node_ids: Sequence[Any]) -> Dict[str, Any]:
    return {
        "schema_version": "ctree_support_bundle_v1",
        "mode": str(mode or "active_continuation"),
        "focus_node_id": str(focus_node_id or "").strip() or None,
        "active_path_node_ids": _normalize_id_list(active_path_node_ids),
        "support_node_ids": [],
        "constraints": [],
        "targets": [],
        "workspace_scope": [],
        "artifact_refs": [],
        "blocker_refs": [],
        "validations": [],
        "graph_neighbor_ids": [],
        "superseded_source_ids": [],
        "candidate_provenance": [],
        "summary_support": [],
    }


def build_evaluation_prompt_planes(
    compiled_prompt_planes: Optional[Dict[str, Any]],
    *,
    support_bundle: Dict[str, Any],
    include_summary_proposals: bool = False,
) -> Dict[str, Any]:
    source = dict(compiled_prompt_planes or {})
    prompt_planes = {
        "schema_version": str(source.get("schema_version") or "ctree_prompt_planes_v2"),
        "stable_rules": copy.deepcopy(source.get("stable_rules") or {}),
        "active_path": copy.deepcopy(source.get("active_path") or []),
        "reduced_task_state": copy.deepcopy(source.get("reduced_task_state") or {}),
        "support_bundle": copy.deepcopy(support_bundle or {}),
    }
    if include_summary_proposals and "subtree_summary_proposals" in source:
        prompt_planes["subtree_summary_proposals"] = copy.deepcopy(source.get("subtree_summary_proposals") or [])
    if "live_session_delta" in source:
        prompt_planes["live_session_delta"] = copy.deepcopy(source.get("live_session_delta") or {})
    return prompt_planes


def evaluate_support_result(
    *,
    store: CTreeStore,
    scenario_id: str,
    family: str,
    perturbation: str,
    system_id: str,
    baseline_id: Optional[str],
    mode: str,
    focus_node_id: Optional[str],
    retrieval_substrate: Dict[str, Any],
    support_bundle: Dict[str, Any],
    prompt_planes: Dict[str, Any],
    expected_support_node_ids: Optional[Sequence[Any]] = None,
    forbidden_support_node_ids: Optional[Sequence[Any]] = None,
    expected_artifact_refs: Optional[Sequence[Any]] = None,
    forbidden_artifact_refs: Optional[Sequence[Any]] = None,
    expected_blocker_refs: Optional[Sequence[Any]] = None,
    expected_validation_ids: Optional[Sequence[Any]] = None,
    wrong_neighbor_node_ids: Optional[Sequence[Any]] = None,
    max_support_share: Optional[float] = None,
) -> Dict[str, Any]:
    promoted_ids = _normalize_id_list(support_bundle.get("support_node_ids"))
    expected_ids = _normalize_id_list(expected_support_node_ids)
    forbidden_ids = _normalize_id_list(forbidden_support_node_ids)
    expected_artifacts = _normalize_id_list(expected_artifact_refs)
    forbidden_artifacts = _normalize_id_list(forbidden_artifact_refs)
    expected_blockers = _normalize_id_list(expected_blocker_refs)
    expected_validations = _normalize_id_list(expected_validation_ids)
    wrong_neighbor_ids = set(_normalize_id_list(wrong_neighbor_node_ids)) | set(forbidden_ids)

    promoted_set = set(promoted_ids)
    expected_set = set(expected_ids)
    forbidden_set = set(forbidden_ids)
    relevant_promoted_ids = [node_id for node_id in promoted_ids if node_id in expected_set]
    forbidden_promoted_ids = [node_id for node_id in promoted_ids if node_id in forbidden_set]

    stale_support_ids: List[str] = []
    for node_id in promoted_ids:
        node = _node_by_id(store, node_id)
        status = str((node or {}).get("status") or "")
        if status in _STALE_STATUSES:
            stale_support_ids.append(node_id)

    wrong_neighbor_promoted_ids = [node_id for node_id in promoted_ids if node_id in wrong_neighbor_ids]

    promoted_artifacts = _normalize_id_list(support_bundle.get("artifact_refs"))
    promoted_blockers = _normalize_id_list(support_bundle.get("blocker_refs"))
    promoted_validations = _normalize_id_list(support_bundle.get("validations"))
    promoted_artifact_set = set(promoted_artifacts)
    expected_artifact_hits = [value for value in expected_artifacts if value in promoted_artifact_set]
    forbidden_artifact_hits = [value for value in forbidden_artifacts if value in promoted_artifact_set]

    support_count = len(promoted_ids)
    candidate_counts = dict((retrieval_substrate.get("provenance") or {}).get("candidate_counts") or {})
    retrieved_candidate_count = sum(int(value or 0) for value in candidate_counts.values())
    promoted_candidate_count = len(_normalize_id_list(item.get("node_id") for item in list(support_bundle.get("candidate_provenance") or [])))

    prompt_eligible_bundle = _prompt_eligible_support(support_bundle)
    support_token_count = _rough_token_count(prompt_eligible_bundle) if _support_surface_has_content(prompt_eligible_bundle) else 0
    summary_token_count = (
        _rough_token_count(prompt_eligible_bundle.get("summary_support") or [])
        if bool(prompt_eligible_bundle.get("summary_support"))
        else 0
    )
    total_prompt_token_count = max(_rough_token_count(prompt_planes), 1)
    support_surface_token_count = max(support_token_count + summary_token_count, 1)
    support_token_share = support_token_count / total_prompt_token_count
    summary_support_share = summary_token_count / support_surface_token_count

    precision = len(relevant_promoted_ids) / support_count if support_count else 0.0
    recall = len(relevant_promoted_ids) / len(expected_set) if expected_set else 1.0
    stale_support_rate = len(stale_support_ids) / support_count if support_count else 0.0
    wrong_neighbor_rate = len(wrong_neighbor_promoted_ids) / support_count if support_count else 0.0
    blocker_coverage = (
        len([value for value in expected_blockers if value in set(promoted_blockers)]) / len(expected_blockers)
        if expected_blockers
        else 1.0
    )
    validation_coverage = (
        len([value for value in expected_validations if value in set(promoted_validations)]) / len(expected_validations)
        if expected_validations
        else 1.0
    )
    retrieved_but_unused_rate = (
        max(retrieved_candidate_count - promoted_candidate_count, 0) / retrieved_candidate_count
        if retrieved_candidate_count
        else 0.0
    )
    over_budget = bool(max_support_share is not None and support_token_share > float(max_support_share))

    scenario_score = (
        len(relevant_promoted_ids)
        + len(expected_artifact_hits)
        + (blocker_coverage if expected_blockers else 0.0)
        + (validation_coverage if expected_validations else 0.0)
        - len(forbidden_promoted_ids)
        - len(forbidden_artifact_hits)
        - len(stale_support_ids)
        - len(wrong_neighbor_promoted_ids)
    )

    fail_reasons: List[str] = []
    if expected_set and len(relevant_promoted_ids) < len(expected_set):
        fail_reasons.append("missing_expected_support")
    if expected_artifacts and len(expected_artifact_hits) < len(expected_artifacts):
        fail_reasons.append("missing_expected_artifacts")
    if expected_blockers and blocker_coverage < 1.0:
        fail_reasons.append("missing_expected_blockers")
    if expected_validations and validation_coverage < 1.0:
        fail_reasons.append("missing_expected_validations")
    if forbidden_promoted_ids:
        fail_reasons.append("forbidden_support_present")
    if forbidden_artifact_hits:
        fail_reasons.append("forbidden_artifacts_present")
    if stale_support_ids:
        fail_reasons.append("stale_support_present")
    if wrong_neighbor_promoted_ids:
        fail_reasons.append("wrong_neighbor_present")
    if over_budget:
        fail_reasons.append("support_over_budget")
    scenario_pass = not fail_reasons

    return {
        "schema_version": "ctree_eval_result_v1",
        "scenario_id": str(scenario_id),
        "family": str(family),
        "perturbation": str(perturbation),
        "baseline_id": str(baseline_id) if baseline_id else None,
        "system_id": str(system_id),
        "mode": str(mode),
        "focus_node_id": str(focus_node_id or "").strip() or None,
        "support_node_ids": promoted_ids,
        "expected_support_node_ids": expected_ids,
        "forbidden_support_node_ids": forbidden_ids,
        "expected_artifact_refs": expected_artifacts,
        "forbidden_artifact_refs": forbidden_artifacts,
        "expected_blocker_refs": expected_blockers,
        "expected_validation_ids": expected_validations,
        "candidate_counts_by_lane": candidate_counts,
        "metrics": {
            "support_precision": precision,
            "support_recall": recall,
            "stale_support_inclusion_rate": stale_support_rate,
            "wrong_neighbor_rate": wrong_neighbor_rate,
            "blocker_coverage": blocker_coverage,
            "validation_coverage": validation_coverage,
            "support_token_share": support_token_share,
            "summary_support_share": summary_support_share,
            "retrieved_but_unused_rate": retrieved_but_unused_rate,
            "over_budget_rate": 1.0 if over_budget else 0.0,
            "scenario_score": scenario_score,
        },
        "counts": {
            "retrieved_candidate_count": retrieved_candidate_count,
            "promoted_candidate_count": promoted_candidate_count,
            "promoted_support_count": support_count,
            "relevant_promoted_count": len(relevant_promoted_ids),
            "forbidden_promoted_count": len(forbidden_promoted_ids),
            "stale_support_count": len(stale_support_ids),
            "wrong_neighbor_count": len(wrong_neighbor_promoted_ids),
            "support_token_count": support_token_count,
            "summary_token_count": summary_token_count,
            "total_prompt_token_count": total_prompt_token_count,
        },
        "matched": {
            "relevant_promoted_ids": relevant_promoted_ids,
            "forbidden_promoted_ids": forbidden_promoted_ids,
            "stale_support_ids": stale_support_ids,
            "wrong_neighbor_promoted_ids": wrong_neighbor_promoted_ids,
            "expected_artifact_hits": expected_artifact_hits,
            "forbidden_artifact_hits": forbidden_artifact_hits,
            "covered_blocker_refs": [value for value in expected_blockers if value in set(promoted_blockers)],
            "covered_validation_ids": [value for value in expected_validations if value in set(promoted_validations)],
        },
        "support_bundle": {
            "support_node_ids": promoted_ids,
            "constraints": _normalize_constraint_list(support_bundle.get("constraints")),
            "targets": _normalize_id_list(support_bundle.get("targets")),
            "workspace_scope": _normalize_id_list(support_bundle.get("workspace_scope")),
            "artifact_refs": promoted_artifacts,
            "blocker_refs": promoted_blockers,
            "validations": promoted_validations,
            "summary_support": copy.deepcopy(support_bundle.get("summary_support") or []),
        },
        "prompt_plane_summary": {
            "active_path_node_ids": _normalize_id_list((prompt_planes.get("reduced_task_state") or {}).get("active_path_node_ids")),
            "needs_resolution": bool((prompt_planes.get("reduced_task_state") or {}).get("needs_resolution")),
            "has_summary_proposals": "subtree_summary_proposals" in prompt_planes,
        },
        "outcome": {
            "pass": scenario_pass,
            "fail_reasons": fail_reasons,
        },
    }


def compare_result_pair(candidate: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    candidate_metrics = dict(candidate.get("metrics") or {})
    baseline_metrics = dict(baseline.get("metrics") or {})
    metric_keys = (
        "support_precision",
        "support_recall",
        "stale_support_inclusion_rate",
        "wrong_neighbor_rate",
        "blocker_coverage",
        "validation_coverage",
        "support_token_share",
        "summary_support_share",
        "retrieved_but_unused_rate",
        "scenario_score",
    )
    deltas = {
        key: float(candidate_metrics.get(key) or 0.0) - float(baseline_metrics.get(key) or 0.0)
        for key in metric_keys
    }
    return {
        "candidate_system_id": str(candidate.get("system_id") or ""),
        "baseline_system_id": str(baseline.get("system_id") or ""),
        "scenario_id": str(candidate.get("scenario_id") or ""),
        "delta": deltas,
        "candidate_better": (
            deltas["scenario_score"] > 0.0
            and deltas["support_recall"] >= 0.0
            and deltas["wrong_neighbor_rate"] <= 0.0
            and deltas["stale_support_inclusion_rate"] <= 0.0
        ),
    }
