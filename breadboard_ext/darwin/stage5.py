from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from breadboard_ext.darwin.stage4 import build_stage4_search_policy_v1
from breadboard_ext.darwin.stage4_family_program import FAMILY_OPERATOR_MAP


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE5_FAMILY_REGISTRY = ROOT / "artifacts" / "darwin" / "stage4" / "family_program" / "family_registry_v0.json"
DEFAULT_STAGE5_POLICY_STABILITY = ROOT / "artifacts" / "darwin" / "stage5" / "policy_stability" / "policy_stability_v0.json"

STAGE5_COMPARISON_MODES = ("cold_start", "warm_start", "family_lockout")

_FAMILY_PRIORITY_BASE = {
    "topology": 0.95,
    "policy": 0.88,
    "tool_scope": 0.82,
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_stage5_family_registry_rows(path: Path = DEFAULT_STAGE5_FAMILY_REGISTRY) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _load_json(path)
    return list(payload.get("rows") or [])


def load_stage5_policy_stability_rows(path: Path = DEFAULT_STAGE5_POLICY_STABILITY) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _load_json(path)
    return list(payload.get("rows") or [])


def _family_operator_id(row: Mapping[str, Any]) -> str | None:
    family_key = str(row.get("family_key") or "")
    family_kind = str(row.get("family_kind") or "")
    for operator_id, mapping in FAMILY_OPERATOR_MAP.items():
        if str(mapping["family_key"]) == family_key and str(mapping["family_kind"]) == family_kind:
            return operator_id
    return None


def _lane_policy_review_conclusion(
    lane_id: str,
    *,
    policy_stability_rows: list[dict[str, Any]] | None = None,
) -> str | None:
    rows = load_stage5_policy_stability_rows() if policy_stability_rows is None else list(policy_stability_rows)
    for row in rows:
        if str(row.get("lane_id") or "") == lane_id:
            conclusion = str(row.get("policy_review_conclusion") or "")
            return conclusion or None
    return None


def build_stage5_search_policy_v2(
    *,
    lane_id: str,
    budget_class: str,
    family_rows: list[dict[str, Any]] | None = None,
    policy_stability_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if lane_id not in {"lane.repo_swe", "lane.systems"}:
        raise ValueError("stage5 search policy v2 is only authorized for lane.repo_swe and lane.systems")
    family_rows = list(load_stage5_family_registry_rows() if family_rows is None else family_rows)
    policy_review_conclusion = _lane_policy_review_conclusion(lane_id, policy_stability_rows=policy_stability_rows)
    base_policy = build_stage4_search_policy_v1(lane_id=lane_id, budget_class=budget_class)
    promoted_rows = [
        row
        for row in family_rows
        if str(row.get("lane_id") or "") == lane_id and str(row.get("lifecycle_status") or "") == "promoted"
    ]
    family_priors = []
    for row in promoted_rows:
        operator_id = _family_operator_id(row)
        family_kind = str(row.get("family_kind") or "")
        family_priors.append(
            {
                "family_id": str(row["family_id"]),
                "family_kind": family_kind,
                "priority": float(_FAMILY_PRIORITY_BASE.get(family_kind, 0.75)),
                "source_operator_id": operator_id,
                "replay_status": str(row.get("replay_status") or ""),
                "transfer_eligibility": dict(row.get("transfer_eligibility") or {}),
            }
        )
    family_registry_ref = None
    if DEFAULT_STAGE5_FAMILY_REGISTRY.exists():
        family_registry_ref = str(DEFAULT_STAGE5_FAMILY_REGISTRY.relative_to(ROOT))
    tightened_repo_swe = lane_id == "lane.repo_swe" and policy_review_conclusion == "tighten"
    payload = {
        "schema": "breadboard.darwin.stage5.search_policy.v2",
        "policy_id": f"darwin.stage5.search_policy.{lane_id.split('.')[-1]}.v2",
        "lane_id": lane_id,
        "budget_class": budget_class,
        "campaign_class": "C1 Discovery",
        "worker_route_id": base_policy["worker_route_id"],
        "filter_route_id": base_policy["filter_route_id"],
        "max_mutation_arms": 1 if tightened_repo_swe else 2,
        "repetition_count": max(int(base_policy["repetition_count"]), 6 if tightened_repo_swe else 4),
        "operator_priors": list(base_policy["operator_priors"]),
        "topology_priors": list(base_policy["topology_priors"]),
        "family_priors": family_priors,
        "comparison_mode_quotas": {
            "cold_start": 1,
            "warm_start": 1,
            "family_lockout": 1,
        },
        "route_priors": [
            {
                "route_id": base_policy["worker_route_id"],
                "priority": 1.0,
                "reason": "default_live_worker_route",
            },
            {
                "route_id": base_policy["filter_route_id"],
                "priority": 0.7,
                "reason": "cheap_filter_and_scout_route",
            },
        ],
        "policy_stability_ref": str(DEFAULT_STAGE5_POLICY_STABILITY.relative_to(ROOT)) if DEFAULT_STAGE5_POLICY_STABILITY.exists() else None,
        "policy_review_conclusion": policy_review_conclusion,
        "policy_tightening": {
            "enabled": tightened_repo_swe,
            "lane_review_conclusion": policy_review_conclusion,
            "reason": "repo_swe_stability_requires_tighter_selection" if tightened_repo_swe else "not_required",
        },
        "abort_thresholds": {
            "matched_budget_invalidity_rate_gt": 0.25,
            "claim_ineligible_live_rows_gt": 0,
        },
        "consumes_family_state": True,
        "family_registry_ref": family_registry_ref,
    }
    payload["policy_digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return payload


def _stage5_variant(
    *,
    row: Mapping[str, Any],
    comparison_mode: str,
    search_policy: Mapping[str, Any],
    family_ids: list[str],
) -> dict[str, Any]:
    if comparison_mode not in STAGE5_COMPARISON_MODES:
        raise ValueError(f"unsupported comparison mode: {comparison_mode}")
    allowed_family_ids = list(family_ids) if comparison_mode == "warm_start" else []
    blocked_family_ids = list(family_ids) if comparison_mode == "family_lockout" else []
    suffix = comparison_mode.replace("_", "-")
    variant = dict(row)
    variant["campaign_arm_id"] = f"{row['campaign_arm_id']}.{suffix}.stage5.v0"
    variant["comparison_mode"] = comparison_mode
    variant["family_context"] = {
        "allowed_family_ids": allowed_family_ids,
        "blocked_family_ids": blocked_family_ids,
    }
    variant["search_policy_selection"] = {
        "selected": True,
        "selection_reason": "stage5_family_mode_pair" if comparison_mode != "cold_start" else "stage5_cold_start_control",
        "policy_id": search_policy["policy_id"],
        "campaign_class": search_policy["campaign_class"],
        "policy_digest": search_policy["policy_digest"],
        "comparison_mode": comparison_mode,
        "family_count": len(family_ids),
    }
    return variant


def select_stage5_search_policy_arms(
    *,
    search_policy: Mapping[str, Any],
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lane_id = str(search_policy["lane_id"])
    if lane_id not in {"lane.repo_swe", "lane.systems"}:
        raise ValueError("stage5 selection is only authorized for lane.repo_swe and lane.systems")
    family_ids = [str(row["family_id"]) for row in search_policy.get("family_priors") or []]
    family_operator_ids = {
        str(row.get("source_operator_id") or "")
        for row in search_policy.get("family_priors") or []
        if str(row.get("source_operator_id") or "")
    }
    priority_lookup = {
        str(row["operator_id"]): float(row["priority"])
        for row in search_policy.get("operator_priors") or []
    }
    lane_rows = [row for row in candidate_rows if row["lane_id"] == lane_id]
    other_rows = [row for row in candidate_rows if row["lane_id"] != lane_id]
    control_rows = [dict(row) for row in lane_rows if row["control_tag"] == "control"]
    mutation_rows = [dict(row) for row in lane_rows if row["control_tag"] != "control"]
    selected_rows: list[dict[str, Any]] = []
    for row in control_rows:
        selected_rows.append(
            _stage5_variant(
                row=row,
                comparison_mode="cold_start",
                search_policy=search_policy,
                family_ids=[],
            )
        )
    max_mutation_arms = max(0, int(search_policy.get("max_mutation_arms") or 0))
    ranked_rows = sorted(
        mutation_rows,
        key=lambda row: (
            str(row.get("operator_id") or "") not in family_operator_ids,
            -priority_lookup.get(str(row.get("operator_id") or ""), -1.0),
            str(row.get("operator_id") or ""),
        ),
    )
    policy_tightening = search_policy.get("policy_tightening")
    tightened_repo_swe = bool(policy_tightening.get("enabled")) if isinstance(policy_tightening, Mapping) else False
    if tightened_repo_swe and lane_id == "lane.repo_swe":
        max_mutation_arms = min(max_mutation_arms or 1, 1)
    for row in ranked_rows[:max_mutation_arms or 0]:
        if str(row.get("operator_id") or "") in family_operator_ids:
            selected_rows.append(
                _stage5_variant(
                    row=row,
                    comparison_mode="warm_start",
                    search_policy=search_policy,
                    family_ids=family_ids,
                )
            )
            selected_rows.append(
                _stage5_variant(
                    row=row,
                    comparison_mode="family_lockout",
                    search_policy=search_policy,
                    family_ids=family_ids,
                )
            )
        else:
            variant = dict(row)
            variant["comparison_mode"] = "cold_start"
            variant["family_context"] = {"allowed_family_ids": [], "blocked_family_ids": []}
            selected_rows.append(variant)
    if not (tightened_repo_swe and lane_id == "lane.repo_swe"):
        for row in other_rows:
            variant = dict(row)
            variant["comparison_mode"] = "cold_start"
            variant["family_context"] = {"allowed_family_ids": [], "blocked_family_ids": []}
            selected_rows.append(variant)
    return selected_rows


def build_stage5_compounding_cases(
    *,
    comparison_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in comparison_rows:
        mode = str(row.get("comparison_mode") or "")
        if mode not in {"warm_start", "family_lockout"}:
            continue
        key = (
            str(row.get("lane_id") or ""),
            str(row.get("operator_id") or ""),
            int(row.get("repetition_index") or 0),
        )
        grouped.setdefault(key, {})[mode] = row
    cases: list[dict[str, Any]] = []
    for (lane_id, operator_id, repetition_index), rows in sorted(grouped.items()):
        warm = rows.get("warm_start")
        lockout = rows.get("family_lockout")
        if warm is None or lockout is None:
            continue
        if not bool(warm.get("comparison_valid")) or not bool(lockout.get("comparison_valid")):
            conclusion = "invalid"
        elif not bool(warm.get("claim_eligible")) or not bool(lockout.get("claim_eligible")):
            conclusion = "inconclusive"
        else:
            warm_tuple = (
                float(warm.get("delta_score") or 0.0),
                -int(warm.get("delta_runtime_ms") or 0),
                -float(warm.get("delta_cost_usd") or 0.0),
            )
            lockout_tuple = (
                float(lockout.get("delta_score") or 0.0),
                -int(lockout.get("delta_runtime_ms") or 0),
                -float(lockout.get("delta_cost_usd") or 0.0),
            )
            conclusion = "reuse_lift" if warm_tuple > lockout_tuple else "no_lift"
        cases.append(
            {
                "schema": "breadboard.darwin.stage5.compounding_case.v1",
                "compounding_case_id": f"compounding_case.{lane_id}.{operator_id}.r{repetition_index}.v1",
                "lane_id": lane_id,
                "campaign_class": str(warm.get("campaign_class") or ""),
                "comparison_mode_pair": ["warm_start", "family_lockout"],
                "operator_id": operator_id,
                "topology_id": str(warm.get("topology_id") or ""),
                "family_context": dict(warm.get("family_context") or {}),
                "evaluator_pack_version": str(warm.get("evaluator_pack_version") or ""),
                "comparison_envelope_digest": str(warm.get("comparison_envelope_digest") or ""),
                "route_context": {
                    "warm_start_provider_origin": str(warm.get("provider_origin") or ""),
                    "family_lockout_provider_origin": str(lockout.get("provider_origin") or ""),
                    "warm_start_cost_source": str(warm.get("cost_source") or ""),
                    "family_lockout_cost_source": str(lockout.get("cost_source") or ""),
                },
                "warm_start": {
                    "campaign_arm_id": str(warm.get("campaign_arm_id") or ""),
                    "comparison_valid": bool(warm.get("comparison_valid")),
                    "claim_eligible": bool(warm.get("claim_eligible")),
                    "delta_score": float(warm.get("delta_score") or 0.0),
                    "delta_runtime_ms": int(warm.get("delta_runtime_ms") or 0),
                    "delta_cost_usd": float(warm.get("delta_cost_usd") or 0.0),
                    "power_signal_class": str(warm.get("power_signal_class") or ""),
                },
                "family_lockout": {
                    "campaign_arm_id": str(lockout.get("campaign_arm_id") or ""),
                    "comparison_valid": bool(lockout.get("comparison_valid")),
                    "claim_eligible": bool(lockout.get("claim_eligible")),
                    "delta_score": float(lockout.get("delta_score") or 0.0),
                    "delta_runtime_ms": int(lockout.get("delta_runtime_ms") or 0),
                    "delta_cost_usd": float(lockout.get("delta_cost_usd") or 0.0),
                    "power_signal_class": str(lockout.get("power_signal_class") or ""),
                },
                "outcome_deltas": {
                    "score_lift": round(float(warm.get("delta_score") or 0.0) - float(lockout.get("delta_score") or 0.0), 6),
                    "runtime_lift_ms": int(warm.get("delta_runtime_ms") or 0) - int(lockout.get("delta_runtime_ms") or 0),
                    "cost_lift_usd": round(float(warm.get("delta_cost_usd") or 0.0) - float(lockout.get("delta_cost_usd") or 0.0), 8),
                },
                "conclusion": conclusion,
            }
        )
    return cases
