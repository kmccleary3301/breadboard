from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from breadboard_ext.darwin.stage4_family_program import FAMILY_OPERATOR_MAP
from breadboard_ext.darwin.stage5 import (
    _classify_stage5_compounding_outcome,
    build_stage5_search_policy_v2,
    load_stage5_cross_lane_review,
    load_stage5_family_registry_rows,
    select_stage5_search_policy_arms,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE5_COMPOUNDING_QUALITY = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_quality" / "compounding_quality_v0.json"

STAGE6_COMPARISON_MODES = ("cold_start", "warm_start", "family_lockout", "single_family_lockout")
_STAGE6_CONTROL_ENVELOPE_BY_LANE = {
    "lane.systems": {
        "operator_id": "baseline_seed",
        "topology_id": "policy.topology.single_v0",
        "policy_bundle_id": "policy.topology.single_v0",
    },
    "lane.repo_swe": {
        "operator_id": "baseline_seed",
        "topology_id": "policy.topology.single_v0",
        "policy_bundle_id": "policy.topology.single_v0",
    },
}


def _default_stage6_family_rows() -> list[dict[str, Any]]:
    return [
        {
            "family_id": "component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0",
            "lane_id": "lane.systems",
            "family_kind": "policy",
            "family_key": "policy.shadow_memory_enable_v1",
            "stage5_family_state": "active_proving",
            "lane_weight": "primary_proving_lane",
            "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]},
            "replay_status": "supported",
            "activation_readiness": "ready",
        },
        {
            "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
            "lane_id": "lane.repo_swe",
            "family_kind": "topology",
            "family_key": "policy.topology.pev_v0",
            "stage5_family_state": "challenge_only",
            "lane_weight": "challenge_lane",
            "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            "replay_status": "observed",
            "activation_readiness": "ready",
        },
        {
            "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
            "lane_id": "lane.repo_swe",
            "family_kind": "tool_scope",
            "family_key": "policy.tool_scope.add_git_diff_v1",
            "stage5_family_state": "held_back",
            "lane_weight": "challenge_lane",
            "transfer_eligibility": {"allowed_target_lanes": []},
            "replay_status": "missing",
            "activation_readiness": "held",
        },
    ]


def _default_stage6_stage5_family_rows() -> list[dict[str, Any]]:
    return [
        {
            "family_id": "component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0",
            "lane_id": "lane.systems",
            "family_kind": "policy",
            "family_key": "policy.shadow_memory_enable_v1",
            "lifecycle_status": "promoted",
            "replay_status": "supported",
            "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]},
        },
        {
            "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
            "lane_id": "lane.repo_swe",
            "family_kind": "topology",
            "family_key": "policy.topology.pev_v0",
            "lifecycle_status": "promoted",
            "replay_status": "observed",
            "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
        },
        {
            "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
            "lane_id": "lane.repo_swe",
            "family_kind": "tool_scope",
            "family_key": "policy.tool_scope.add_git_diff_v1",
            "lifecycle_status": "withheld",
            "replay_status": "missing",
            "transfer_eligibility": {"allowed_target_lanes": []},
        },
    ]


def _default_stage6_compounding_quality_rows() -> list[dict[str, Any]]:
    return [
        {
            "lane_id": "lane.systems",
            "lane_weight": "primary_proving_lane",
            "confidence_class": "mixed_positive",
            "reuse_lift_rate": 0.58,
        },
        {
            "lane_id": "lane.repo_swe",
            "lane_weight": "challenge_lane",
            "confidence_class": "mixed_negative",
            "reuse_lift_rate": 0.33,
        },
    ]


def _default_stage6_cross_lane_review() -> dict[str, Any]:
    return {
        "current_primary_lane_id": "lane.systems",
        "rows": [
            {
                "lane_id": "lane.systems",
                "lane_weight": "primary_proving_lane",
                "lane_weight_reason": "systems_remains_cleaner_primary_proving_lane",
            },
            {
                "lane_id": "lane.repo_swe",
                "lane_weight": "challenge_lane",
                "lane_weight_reason": "repo_swe_remains_bounded_challenge_lane",
            },
        ],
    }


def _family_operator_id(row: Mapping[str, Any]) -> str | None:
    family_key = str(row.get("family_key") or "")
    family_kind = str(row.get("family_kind") or "")
    for operator_id, mapping in FAMILY_OPERATOR_MAP.items():
        if str(mapping["family_key"]) == family_key and str(mapping["family_kind"]) == family_kind:
            return operator_id
    return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_stage6_compounding_quality_rows(path: Path = DEFAULT_STAGE5_COMPOUNDING_QUALITY) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _load_json(path)
    return list(payload.get("rows") or [])


def _activation_status(stage5_family_state: str) -> tuple[str, str]:
    if stage5_family_state == "active_proving":
        return "active", "primary_lane_active"
    if stage5_family_state == "challenge_only":
        return "challenge", "challenge_lane_active"
    if stage5_family_state == "transfer_candidate":
        return "candidate", "transfer_candidate_only"
    return "inactive", "not_authorized"


def build_stage6_family_activation_rows(
    *,
    family_rows: list[dict[str, Any]] | None = None,
    compounding_quality_rows: list[dict[str, Any]] | None = None,
    cross_lane_review: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    family_rows = list(load_stage5_family_registry_rows() if family_rows is None else family_rows)
    if not family_rows:
        family_rows = _default_stage6_family_rows()
    compounding_quality_rows = list(load_stage6_compounding_quality_rows() if compounding_quality_rows is None else compounding_quality_rows)
    if not compounding_quality_rows:
        compounding_quality_rows = _default_stage6_compounding_quality_rows()
    cross_lane_review = dict(load_stage5_cross_lane_review() or {}) if cross_lane_review is None else dict(cross_lane_review)
    if not cross_lane_review:
        cross_lane_review = _default_stage6_cross_lane_review()
    quality_map = {str(row.get("lane_id") or ""): dict(row) for row in compounding_quality_rows}
    review_rows = {str(row.get("lane_id") or ""): dict(row) for row in list(cross_lane_review.get("rows") or [])}

    rows: list[dict[str, Any]] = []
    for row in family_rows:
        lane_id = str(row.get("lane_id") or "")
        family_id = str(row.get("family_id") or "")
        family_kind = str(row.get("family_kind") or "")
        stage5_family_state = str(row.get("stage5_family_state") or "")
        activation_status, activation_mode = _activation_status(stage5_family_state)
        lane_quality = quality_map.get(lane_id, {})
        review_row = review_rows.get(lane_id, {})
        lane_weight = str(row.get("lane_weight") or review_row.get("lane_weight") or lane_quality.get("lane_weight") or "unset")
        source_operator_id = _family_operator_id(row)
        transfer_targets = list(dict(row.get("transfer_eligibility") or {}).get("allowed_target_lanes") or [])
        rows.append(
            {
                "schema": "breadboard.darwin.stage6.family_activation.v1",
                "activation_id": f"family_activation.{family_id}.v1",
                "family_id": family_id,
                "lane_id": lane_id,
                "lane_weight": lane_weight,
                "family_kind": family_kind,
                "family_key": str(row.get("family_key") or ""),
                "source_operator_id": source_operator_id,
                "stage5_family_state": stage5_family_state,
                "activation_status": activation_status,
                "activation_mode": activation_mode,
                "activation_enabled": activation_status in {"active", "challenge"},
                "allowed_comparison_modes": (
                    ["warm_start", "family_lockout", "single_family_lockout"]
                    if activation_status in {"active", "challenge"}
                    else []
                ),
                "transfer_targets": transfer_targets,
                "activation_readiness": str(row.get("activation_readiness") or ""),
                "replay_status": str(row.get("replay_status") or ""),
                "current_confidence": str(lane_quality.get("confidence_class") or "unknown"),
                "reuse_lift_rate": float(lane_quality.get("reuse_lift_rate") or 0.0),
            }
        )
    return rows


def select_stage6_search_policy_arms(
    *,
    search_policy: Mapping[str, Any],
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = [dict(row) for row in select_stage5_search_policy_arms(search_policy=search_policy, candidate_rows=candidate_rows)]
    augmented: list[dict[str, Any]] = []
    for row in selected:
        augmented.append(row)
        if str(row.get("comparison_mode") or "") != "warm_start":
            continue
        allowed_family_ids = list(dict(row.get("family_context") or {}).get("allowed_family_ids") or [])
        if not allowed_family_ids:
            continue
        blocked_family_ids = [allowed_family_ids[0]]
        remaining_allowed = [family_id for family_id in allowed_family_ids if family_id not in blocked_family_ids]
        single_lockout = dict(row)
        single_lockout["campaign_arm_id"] = str(row["campaign_arm_id"]).replace(".warm-start.", ".single-family-lockout.")
        single_lockout["comparison_mode"] = "single_family_lockout"
        activation_policy = dict(search_policy.get("activation_policy") or {})
        single_lockout["activation_policy"] = {
            "mode": str(activation_policy.get("mode") or "metadata_only"),
            "selected_active_family_id": allowed_family_ids[0],
            "blocked_family_ids": blocked_family_ids,
            "activation_rationale": str(activation_policy.get("activation_rationale") or "stage6_single_family_lockout_pair"),
        }
        lockout_execution = dict(activation_policy.get("single_family_lockout_execution") or {})
        if lockout_execution and str(search_policy.get("lane_id") or "") == "lane.systems":
            single_lockout["topology_id"] = str(lockout_execution.get("topology_id") or single_lockout.get("topology_id") or "")
            single_lockout["policy_bundle_id"] = str(lockout_execution.get("policy_bundle_id") or single_lockout.get("policy_bundle_id") or "")
        single_lockout["family_context"] = {
            "allowed_family_ids": remaining_allowed,
            "blocked_family_ids": blocked_family_ids,
        }
        selection = dict(single_lockout.get("search_policy_selection") or {})
        selection["comparison_mode"] = "single_family_lockout"
        selection["selection_reason"] = "stage6_single_family_lockout_execution_pair"
        selection["activation_policy_mode"] = str(activation_policy.get("mode") or "metadata_only")
        selection["selected_active_family_id"] = allowed_family_ids[0]
        selection["blocked_family_ids"] = blocked_family_ids
        selection["activation_rationale"] = str(activation_policy.get("activation_rationale") or "stage6_single_family_lockout_pair")
        single_lockout["search_policy_selection"] = selection
        augmented.append(single_lockout)
    return augmented


def build_stage6_comparison_envelope(
    *,
    lane_id: str,
    operator_id: str,
    family_id: str | None,
    family_kind: str | None,
    activation_status: str,
    lane_weight: str,
    comparison_mode_pair: list[str],
    evaluator_pack_version: str,
    comparison_envelope_digest: str,
    policy_digest: str | None = None,
    active_family_id: str | None = None,
    source_transfer_basis: str | None = None,
    target_lane_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "lane_id": lane_id,
        "operator_id": operator_id,
        "family_id": family_id,
        "family_kind": family_kind,
        "activation_status": activation_status,
        "lane_weight": lane_weight,
        "comparison_mode_pair": list(comparison_mode_pair),
        "evaluator_pack_version": evaluator_pack_version,
        "comparison_envelope_digest": comparison_envelope_digest,
        "policy_digest": policy_digest or "",
        "active_family_id": active_family_id or family_id or "",
        "source_transfer_basis": source_transfer_basis or "",
        "target_lane_id": target_lane_id or "",
    }
    envelope_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "schema": "breadboard.darwin.stage6.comparison_envelope.v1",
        **payload,
        "digest": envelope_digest,
    }


def classify_stage6_broader_compounding_outcome(
    *,
    score_lift_vs_family_lockout: float,
    score_lift_vs_single_lockout: float,
    runtime_lift_vs_family_lockout_ms: int,
    runtime_lift_vs_single_lockout_ms: int,
    cost_lift_vs_family_lockout_usd: float,
    cost_lift_vs_single_lockout_usd: float,
) -> tuple[str, str]:
    score_floor = 0.05
    flat_floor = 0.01
    runtime_limit_ms = 100
    cost_limit_usd = 0.001
    positive = (
        score_lift_vs_family_lockout >= score_floor
        and score_lift_vs_single_lockout >= score_floor
        and runtime_lift_vs_family_lockout_ms <= runtime_limit_ms
        and runtime_lift_vs_single_lockout_ms <= runtime_limit_ms
        and cost_lift_vs_family_lockout_usd <= cost_limit_usd
        and cost_lift_vs_single_lockout_usd <= cost_limit_usd
    )
    if positive:
        return "positive_broader_compounding", "beats_both_lockout_controls"
    flat = (
        abs(score_lift_vs_family_lockout) <= flat_floor
        and abs(score_lift_vs_single_lockout) <= flat_floor
    )
    if flat:
        return "flat_broader_compounding", "within_score_deadband_against_both_controls"
    if score_lift_vs_family_lockout < 0.0 or score_lift_vs_single_lockout < 0.0:
        return "negative_broader_compounding", "underperforms_at_least_one_lockout_control"
    return "inconclusive_broader_compounding", "mixed_or_margin_only_advantage"


def build_stage6_transferred_family_activation_row(
    *,
    transfer_case: Mapping[str, Any],
    target_lane_id: str,
) -> dict[str, Any]:
    family_id = str(transfer_case.get("family_id") or "")
    family_kind = str(transfer_case.get("family_kind") or "")
    transfer_status = str(transfer_case.get("transfer_status") or "")
    return {
        "schema": "breadboard.darwin.stage6.family_activation.v1",
        "activation_id": f"family_activation.{family_id}.{target_lane_id}.transferred.v1",
        "family_id": family_id,
        "lane_id": target_lane_id,
        "lane_weight": "retained_transfer_target",
        "family_kind": family_kind,
        "family_key": family_id.rsplit(".", 3)[0] if "." in family_id else family_id,
        "source_operator_id": "",
        "stage5_family_state": "retained_transfer_source",
        "activation_status": "active",
        "activation_mode": "transferred_family_active",
        "activation_enabled": transfer_status in {"retained", "degraded_but_valid"},
        "allowed_comparison_modes": [
            "warm_start",
            "family_lockout",
            "single_family_lockout",
            "transferred_family_active",
        ],
        "transfer_targets": [],
        "activation_readiness": "ready" if transfer_status in {"retained", "degraded_but_valid"} else "blocked",
        "replay_status": str(transfer_case.get("replay_status") or ""),
        "current_confidence": transfer_status,
        "reuse_lift_rate": 0.0,
        "source_lane_id": str(transfer_case.get("source_lane_id") or ""),
        "target_lane_id": target_lane_id,
        "source_transfer_case_id": str(transfer_case.get("transfer_case_id") or ""),
        "source_transfer_status": transfer_status,
        "transfer_authorization_basis": str(transfer_case.get("transfer_reason") or ""),
        "blocked_family_ids": [],
    }


def _pair_outcome(
    *,
    operator_id: str,
    warm: Mapping[str, Any],
    comparator: Mapping[str, Any] | None,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    if comparator is None:
        protocol = {
            "objective": "n/a",
            "runtime_lift_deadband_ms": 10,
            "cost_lift_deadband_usd": 0.0001,
        }
        return "inconclusive", "missing_pair", protocol, {"score_lift": 0.0, "runtime_lift_ms": 0, "cost_lift_usd": 0.0}
    score_lift = round(float(warm.get("delta_score") or 0.0) - float(comparator.get("delta_score") or 0.0), 6)
    runtime_lift_ms = int(warm.get("delta_runtime_ms") or 0) - int(comparator.get("delta_runtime_ms") or 0)
    cost_lift_usd = round(float(warm.get("delta_cost_usd") or 0.0) - float(comparator.get("delta_cost_usd") or 0.0), 8)
    if not bool(warm.get("comparison_valid")) or not bool(comparator.get("comparison_valid")):
        protocol = {
            "objective": "n/a",
            "runtime_lift_deadband_ms": 10,
            "cost_lift_deadband_usd": 0.0001,
        }
        return "invalid", "comparison_invalid", protocol, {"score_lift": score_lift, "runtime_lift_ms": runtime_lift_ms, "cost_lift_usd": cost_lift_usd}
    if not bool(warm.get("claim_eligible")) or not bool(comparator.get("claim_eligible")):
        protocol = {
            "objective": "n/a",
            "runtime_lift_deadband_ms": 10,
            "cost_lift_deadband_usd": 0.0001,
        }
        return "inconclusive", "claim_ineligible", protocol, {"score_lift": score_lift, "runtime_lift_ms": runtime_lift_ms, "cost_lift_usd": cost_lift_usd}
    conclusion, decision_basis, protocol = _classify_stage5_compounding_outcome(
        operator_id=operator_id,
        score_lift=score_lift,
        runtime_lift_ms=runtime_lift_ms,
        cost_lift_usd=cost_lift_usd,
    )
    return conclusion, decision_basis, protocol, {"score_lift": score_lift, "runtime_lift_ms": runtime_lift_ms, "cost_lift_usd": cost_lift_usd}


def build_stage6_compounding_cases(
    *,
    comparison_rows: list[dict[str, Any]],
    activation_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    activation_rows = list(activation_rows or [])
    activation_by_family = {str(row.get("family_id") or ""): dict(row) for row in activation_rows}
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in comparison_rows:
        mode = str(row.get("comparison_mode") or "")
        if mode not in {"warm_start", "family_lockout", "single_family_lockout"}:
            continue
        key = (
            str(row.get("lane_id") or ""),
            str(row.get("operator_id") or ""),
            int(row.get("repetition_index") or 0),
        )
        grouped.setdefault(key, {})[mode] = dict(row)

    cases: list[dict[str, Any]] = []
    for (lane_id, operator_id, repetition_index), rows in sorted(grouped.items()):
        warm = rows.get("warm_start")
        if warm is None:
            continue
        family_lockout = rows.get("family_lockout")
        single_family_lockout = rows.get("single_family_lockout")
        active_family_ids = list(dict(warm.get("family_context") or {}).get("allowed_family_ids") or [])
        family_id = active_family_ids[0] if active_family_ids else None
        activation_row = activation_by_family.get(str(family_id or ""))
        primary_conclusion, primary_basis, primary_protocol, primary_deltas = _pair_outcome(
            operator_id=operator_id,
            warm=warm,
            comparator=family_lockout,
        )
        single_conclusion, single_basis, single_protocol, single_deltas = _pair_outcome(
            operator_id=operator_id,
            warm=warm,
            comparator=single_family_lockout,
        )
        comparison_mode_pair = ["warm_start", "family_lockout"]
        final_conclusion = primary_conclusion
        final_basis = primary_basis
        final_protocol = primary_protocol
        final_deltas = primary_deltas
        if single_family_lockout is not None:
            comparison_mode_pair = ["warm_start", "single_family_lockout"]
            final_conclusion = single_conclusion
            final_basis = single_basis
            final_protocol = single_protocol
            final_deltas = single_deltas
        cases.append(
            {
                "schema": "breadboard.darwin.stage6.compounding_case.v2",
                "compounding_case_id": f"compounding_case.stage6.{lane_id}.{operator_id}.r{repetition_index}.v2",
                "lane_id": lane_id,
                "operator_id": operator_id,
                "family_id": family_id,
                "family_kind": str(activation_row.get("family_kind") or ""),
                "repetition_index": repetition_index,
                "campaign_class": str(warm.get("campaign_class") or ""),
                "comparison_mode_pair": comparison_mode_pair,
                "activation_status": str(activation_row.get("activation_status") or "unknown"),
                "lane_weight": str(activation_row.get("lane_weight") or dict(warm.get("search_policy_selection") or {}).get("lane_weight") or ""),
                "comparison_envelope": build_stage6_comparison_envelope(
                    lane_id=lane_id,
                    operator_id=operator_id,
                    family_id=family_id,
                    family_kind=str(activation_row.get("family_kind") or ""),
                    activation_status=str(activation_row.get("activation_status") or "unknown"),
                    lane_weight=str(activation_row.get("lane_weight") or dict(warm.get("search_policy_selection") or {}).get("lane_weight") or ""),
                    comparison_mode_pair=comparison_mode_pair,
                    evaluator_pack_version=str(warm.get("evaluator_pack_version") or ""),
                    comparison_envelope_digest=str(warm.get("comparison_envelope_digest") or ""),
                ),
                "family_lockout_outcome": {
                    "conclusion": primary_conclusion,
                    "decision_basis": primary_basis,
                    "decision_protocol": primary_protocol,
                    "outcome_deltas": primary_deltas,
                },
                "single_family_lockout_outcome": {
                    "conclusion": single_conclusion,
                    "decision_basis": single_basis,
                    "decision_protocol": single_protocol,
                    "outcome_deltas": single_deltas,
                },
                "decision_basis": final_basis,
                "decision_protocol": final_protocol,
                "decision_deadband": {
                    "runtime_lift_ms": int(final_protocol["runtime_lift_deadband_ms"]),
                    "cost_lift_usd": float(final_protocol["cost_lift_deadband_usd"]),
                },
                "outcome_deltas": final_deltas,
                "conclusion": final_conclusion,
                "route_context": {
                    "warm_start_provider_origin": str(warm.get("provider_origin") or ""),
                    "family_lockout_provider_origin": str((family_lockout or {}).get("provider_origin") or ""),
                    "single_family_lockout_provider_origin": str((single_family_lockout or {}).get("provider_origin") or ""),
                },
            }
        )
    return cases


def build_stage6_transfer_cases(
    *,
    activation_rows: list[dict[str, Any]],
    compounding_cases: list[dict[str, Any]],
    transfer_execution_rows: list[dict[str, Any]] | None = None,
    inactive_transfer_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    transfer_execution_rows = list(transfer_execution_rows or [])
    inactive_transfer_rows = list(inactive_transfer_rows or [])
    strongest_case_by_lane: dict[str, dict[str, Any]] = {}
    for row in compounding_cases:
        lane_id = str(row.get("lane_id") or "")
        current = strongest_case_by_lane.get(lane_id)
        rank = {"reuse_lift": 3, "flat": 2, "no_lift": 1, "inconclusive": 0, "invalid": -1}
        if current is None or rank.get(str(row.get("conclusion") or ""), -2) > rank.get(str(current.get("conclusion") or ""), -2):
            strongest_case_by_lane[lane_id] = dict(row)

    execution_by_transfer = {
        (
            str(row.get("source_lane_id") or ""),
            str(row.get("target_lane_id") or ""),
            str(row.get("family_id") or ""),
        ): dict(row)
        for row in transfer_execution_rows
    }

    rows: list[dict[str, Any]] = []
    for activation in activation_rows:
        if not bool(activation.get("activation_enabled")):
            continue
        lane_id = str(activation.get("lane_id") or "")
        family_id = str(activation.get("family_id") or "")
        source_case = strongest_case_by_lane.get(lane_id, {})
        for target_lane_id in list(activation.get("transfer_targets") or []):
            if not target_lane_id:
                continue
            transfer_key = (lane_id, target_lane_id, family_id)
            transfer_execution = execution_by_transfer.get(transfer_key, {})
            target_score_lift = float(transfer_execution.get("target_score_lift") or 0.0)
            if transfer_execution:
                if not bool(transfer_execution.get("comparison_valid")):
                    transfer_status = "invalid"
                    transfer_reason = str(transfer_execution.get("invalid_reason") or "target_comparison_invalid")
                elif target_score_lift >= 0.15:
                    transfer_status = "retained"
                    transfer_reason = "target_lane_score_improved_materially"
                elif target_score_lift > 0.0:
                    transfer_status = "degraded_but_valid"
                    transfer_reason = "target_lane_score_improved_but_not_materially"
                elif target_score_lift == 0.0:
                    transfer_status = "descriptive_only"
                    transfer_reason = "target_lane_score_unchanged"
                else:
                    transfer_status = "degraded_but_valid"
                    transfer_reason = "target_lane_score_degraded_but_transfer_is_still_valid"
            elif str(source_case.get("conclusion") or "") == "reuse_lift":
                transfer_status = "activation_probe"
                transfer_reason = "active_family_has_positive_stage6_compounding_signal"
            elif str(source_case.get("conclusion") or "") in {"flat", "no_lift"}:
                transfer_status = "descriptive_only"
                transfer_reason = "active_family_lacks_positive_stage6_compounding_signal"
            else:
                transfer_status = "inconclusive"
                transfer_reason = "missing_or_inconclusive_stage6_compounding_signal"
            rows.append(
                {
                    "schema": "breadboard.darwin.stage6.transfer_case.v2",
                    "transfer_case_id": f"transfer_case.stage6.{lane_id}.{target_lane_id}.{family_id}.v2",
                    "source_lane_id": lane_id,
                    "target_lane_id": target_lane_id,
                    "family_id": family_id,
                    "family_kind": str(activation.get("family_kind") or ""),
                    "activation_status": str(activation.get("activation_status") or ""),
                    "lane_weight": str(activation.get("lane_weight") or ""),
                    "transfer_status": transfer_status,
                    "transfer_reason": transfer_reason,
                    "allowed_by_policy": True,
                    "comparison_envelope_digest": str(dict(source_case.get("comparison_envelope") or {}).get("comparison_envelope_digest") or ""),
                    "source_compounding_case_id": str(source_case.get("compounding_case_id") or ""),
                    "replay_status": str(transfer_execution.get("replay_status") or activation.get("replay_status") or ""),
                    "transfer_policy_mode": str(transfer_execution.get("transfer_policy_mode") or "activation_only"),
                    "transfer_policy_rationale": str(transfer_execution.get("transfer_policy_rationale") or ""),
                    "source_conclusion": str(source_case.get("conclusion") or ""),
                    "target_baseline_primary_score": transfer_execution.get("target_baseline_primary_score"),
                    "target_transferred_primary_score": transfer_execution.get("target_transferred_primary_score"),
                    "target_score_lift": transfer_execution.get("target_score_lift"),
                    "target_execution_status": transfer_execution.get("target_execution_status"),
                    "target_candidate_ref": transfer_execution.get("target_candidate_ref"),
                    "target_evaluation_ref": transfer_execution.get("target_evaluation_ref"),
                }
            )
    for row in inactive_transfer_rows:
        rows.append(
            {
                "schema": "breadboard.darwin.stage6.transfer_case.v2",
                **dict(row),
            }
        )
    return rows


def build_stage6_activation_probe_summary(
    *,
    compounding_cases: list[dict[str, Any]],
    activation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    activation_by_lane = {
        str(row.get("lane_id") or ""): dict(row)
        for row in activation_rows
        if bool(row.get("activation_enabled"))
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in compounding_cases:
        grouped.setdefault(str(row.get("lane_id") or ""), []).append(dict(row))

    rows: list[dict[str, Any]] = []
    for lane_id, lane_cases in sorted(grouped.items()):
        activation = activation_by_lane.get(lane_id, {})
        reuse_lift_count = sum(1 for row in lane_cases if str(row.get("conclusion") or "") == "reuse_lift")
        flat_count = sum(1 for row in lane_cases if str(row.get("conclusion") or "") == "flat")
        no_lift_count = sum(1 for row in lane_cases if str(row.get("conclusion") or "") == "no_lift")
        inconclusive_count = sum(
            1
            for row in lane_cases
            if str(row.get("conclusion") or "") not in {"reuse_lift", "flat", "no_lift"}
        )
        if reuse_lift_count:
            classification = "positive_activation_probe"
            reason = "at_least_one_reuse_lift_case"
        elif no_lift_count and not flat_count:
            classification = "negative_activation_probe"
            reason = "only_no_lift_cases"
        elif flat_count and not no_lift_count:
            classification = "flat_activation_probe"
            reason = "only_flat_cases"
        else:
            classification = "inconclusive_activation_probe"
            reason = "mixed_flat_and_no_lift_cases"
        rows.append(
            {
                "schema": "breadboard.darwin.stage6.activation_probe_summary_row.v1",
                "lane_id": lane_id,
                "lane_weight": str(activation.get("lane_weight") or ""),
                "family_id": str(activation.get("family_id") or ""),
                "family_kind": str(activation.get("family_kind") or ""),
                "activation_status": str(activation.get("activation_status") or ""),
                "activation_probe_classification": classification,
                "activation_probe_reason": reason,
                "reuse_lift_count": reuse_lift_count,
                "flat_count": flat_count,
                "no_lift_count": no_lift_count,
                "inconclusive_count": inconclusive_count,
                "case_ids": [str(row.get("compounding_case_id") or "") for row in lane_cases],
            }
        )
    return {
        "schema": "breadboard.darwin.stage6.activation_probe_summary.v1",
        "row_count": len(rows),
        "rows": rows,
    }


def build_stage6_transfer_outcome_summary(
    *,
    transfer_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [
        {
            "schema": "breadboard.darwin.stage6.transfer_outcome_summary_row.v1",
            "transfer_case_id": str(row.get("transfer_case_id") or ""),
            "source_lane_id": str(row.get("source_lane_id") or ""),
            "target_lane_id": str(row.get("target_lane_id") or ""),
            "family_id": str(row.get("family_id") or ""),
            "family_kind": str(row.get("family_kind") or ""),
            "activation_status": str(row.get("activation_status") or ""),
            "lane_weight": str(row.get("lane_weight") or ""),
            "transfer_status": str(row.get("transfer_status") or ""),
            "transfer_reason": str(row.get("transfer_reason") or ""),
            "transfer_policy_mode": str(row.get("transfer_policy_mode") or ""),
            "replay_status": str(row.get("replay_status") or ""),
            "comparison_envelope_digest": str(row.get("comparison_envelope_digest") or ""),
        }
        for row in transfer_cases
    ]
    return {
        "schema": "breadboard.darwin.stage6.transfer_outcome_summary.v1",
        "row_count": len(rows),
        "retained_count": sum(1 for row in transfer_cases if str(row.get("transfer_status") or "") == "retained"),
        "degraded_count": sum(1 for row in transfer_cases if str(row.get("transfer_status") or "") == "degraded_but_valid"),
        "activation_probe_count": sum(1 for row in transfer_cases if str(row.get("transfer_status") or "") == "activation_probe"),
        "rows": rows,
    }


def build_stage6_failed_transfer_taxonomy(
    *,
    transfer_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in transfer_cases:
        status = str(row.get("transfer_status") or "")
        if status in {"retained", "activation_probe"}:
            continue
        reason = str(row.get("transfer_reason") or "unknown_reason")
        bucket = grouped.setdefault(
            reason,
            {
                "schema": "breadboard.darwin.stage6.failed_transfer_taxonomy_row.v1",
                "failure_reason": reason,
                "transfer_statuses": set(),
                "count": 0,
                "example_transfer_case_ids": [],
            },
        )
        bucket["count"] += 1
        bucket["transfer_statuses"].add(status)
        if len(bucket["example_transfer_case_ids"]) < 3:
            bucket["example_transfer_case_ids"].append(str(row.get("transfer_case_id") or ""))
    rows = []
    for reason, row in sorted(grouped.items()):
        rows.append(
            {
                **row,
                "transfer_statuses": sorted(row["transfer_statuses"]),
            }
        )
    return {
        "schema": "breadboard.darwin.stage6.failed_transfer_taxonomy.v1",
        "row_count": len(rows),
        "rows": rows,
    }


def build_stage6_transfer_quality_scorecard(
    *,
    transfer_cases: list[dict[str, Any]],
    provider_segmentation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider_segmentation = dict(provider_segmentation or {})
    rows = []
    for row in transfer_cases:
        rows.append(
            {
                "schema": "breadboard.darwin.stage6.transfer_quality_scorecard_row.v1",
                "source_lane_id": str(row.get("source_lane_id") or ""),
                "target_lane_id": str(row.get("target_lane_id") or ""),
                "lane_weight": str(row.get("lane_weight") or ""),
                "family_id": str(row.get("family_id") or ""),
                "family_kind": str(row.get("family_kind") or ""),
                "activation_status": str(row.get("activation_status") or ""),
                "source_conclusion": str(row.get("source_conclusion") or ""),
                "transfer_status": str(row.get("transfer_status") or ""),
                "transfer_reason": str(row.get("transfer_reason") or ""),
                "provider_segmentation_status": str(provider_segmentation.get("provider_segmentation_status") or ""),
                "replay_status": str(row.get("replay_status") or ""),
                "review_note": (
                    "systems_primary_transfer_focus"
                    if str(row.get("source_lane_id") or "") == "lane.systems"
                    else "challenge_lane_transfer_context"
                ),
            }
        )
    return {
        "schema": "breadboard.darwin.stage6.transfer_quality_scorecard.v1",
        "row_count": len(rows),
        "rows": rows,
    }


def build_stage6_activation_transfer_linkage(
    *,
    activation_probe_summary: Mapping[str, Any],
    transfer_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    probe_by_lane = {
        str(row.get("lane_id") or ""): dict(row)
        for row in list(activation_probe_summary.get("rows") or [])
    }
    rows = []
    for row in transfer_cases:
        lane_id = str(row.get("source_lane_id") or "")
        probe = probe_by_lane.get(lane_id, {})
        rows.append(
            {
                "schema": "breadboard.darwin.stage6.activation_transfer_linkage_row.v1",
                "source_lane_id": lane_id,
                "target_lane_id": str(row.get("target_lane_id") or ""),
                "family_id": str(row.get("family_id") or ""),
                "activation_probe_classification": str(probe.get("activation_probe_classification") or ""),
                "activation_probe_reason": str(probe.get("activation_probe_reason") or ""),
                "transfer_status": str(row.get("transfer_status") or ""),
                "transfer_reason": str(row.get("transfer_reason") or ""),
            }
        )
    return {
        "schema": "breadboard.darwin.stage6.activation_transfer_linkage.v1",
        "row_count": len(rows),
        "rows": rows,
    }


def summarize_stage6_provider_segmentation(
    *,
    telemetry_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_provider_origin_counts: dict[str, int] = {}
    provider_origin_counts: dict[str, int] = {}
    route_class_counts: dict[str, int] = {}
    execution_mode_counts: dict[str, int] = {}
    cost_source_counts: dict[str, int] = {}
    fallback_reason_counts: dict[str, int] = {}

    for row in telemetry_rows:
        requested_provider_origin = str(row.get("requested_provider_origin") or "unknown")
        provider_origin = str(row.get("provider_origin") or "unknown")
        route_class = str(row.get("route_class") or "unknown")
        execution_mode = str(row.get("execution_mode") or "unknown")
        cost_source = str(row.get("cost_source") or "unknown")
        fallback_reason = str(row.get("fallback_reason") or "")
        requested_provider_origin_counts[requested_provider_origin] = requested_provider_origin_counts.get(requested_provider_origin, 0) + 1
        provider_origin_counts[provider_origin] = provider_origin_counts.get(provider_origin, 0) + 1
        route_class_counts[route_class] = route_class_counts.get(route_class, 0) + 1
        execution_mode_counts[execution_mode] = execution_mode_counts.get(execution_mode, 0) + 1
        cost_source_counts[cost_source] = cost_source_counts.get(cost_source, 0) + 1
        if fallback_reason:
            fallback_reason_counts[fallback_reason] = fallback_reason_counts.get(fallback_reason, 0) + 1

    claim_rows = [row for row in run_rows if bool(row.get("claim_eligible"))]
    live_claim_rows = [row for row in claim_rows if str(row.get("execution_mode") or "") == "live"]
    canonical_fields = (
        "requested_route_id",
        "provider_origin",
        "route_id",
        "route_class",
        "provider_model",
        "execution_mode",
        "cost_source",
    )
    segmented_live_claim_rows = [
        row
        for row in live_claim_rows
        if all(row.get(field) not in {None, ""} for field in canonical_fields)
    ]

    if live_claim_rows:
        segmentation_status = (
            "claim_rows_segmented"
            if len(segmented_live_claim_rows) == len(live_claim_rows)
            else "claim_rows_incomplete"
        )
    elif claim_rows:
        segmentation_status = "claim_rows_not_live"
    elif telemetry_rows and all(str(row.get("execution_mode") or "") != "live" for row in telemetry_rows):
        segmentation_status = "scaffold_only"
    else:
        segmentation_status = "no_claim_rows"

    return {
        "schema": "breadboard.darwin.stage6.provider_segmentation_summary.v1",
        "telemetry_row_count": len(telemetry_rows),
        "run_row_count": len(run_rows),
        "requested_provider_origin_counts": requested_provider_origin_counts,
        "provider_origin_counts": provider_origin_counts,
        "route_class_counts": route_class_counts,
        "execution_mode_counts": execution_mode_counts,
        "cost_source_counts": cost_source_counts,
        "fallback_reason_counts": fallback_reason_counts,
        "claim_row_count": len(claim_rows),
        "live_claim_row_count": len(live_claim_rows),
        "segmented_live_claim_row_count": len(segmented_live_claim_rows),
        "provider_segmentation_status": segmentation_status,
        "claim_rows_have_canonical_provider_segmentation": bool(live_claim_rows) and len(segmented_live_claim_rows) == len(live_claim_rows),
    }


def build_stage6_search_policy_preview(*, lane_id: str, budget_class: str = "class_a") -> dict[str, Any]:
    family_rows = _default_stage6_stage5_family_rows()
    cross_lane_review = _default_stage6_cross_lane_review()
    repo_swe_family_ab = {
        "family_selection_status": "settled_topology",
        "preferred_family_kind": "topology",
        "family_selection_reason": "stage6_default_seed_surface",
    }
    policy = build_stage5_search_policy_v2(
        lane_id=lane_id,
        budget_class=budget_class,
        family_rows=family_rows,
        cross_lane_review=cross_lane_review,
        repo_swe_family_ab=repo_swe_family_ab,
    )
    quotas = dict(policy.get("comparison_mode_quotas") or {})
    quotas["single_family_lockout"] = 1
    preview = dict(policy)
    preview["schema"] = "breadboard.darwin.stage6.search_policy.preview.v1"
    preview["comparison_mode_quotas"] = quotas
    preview["consumes_family_activation"] = True
    preview["campaign_class"] = "C1 Activation"
    preview["repetition_count"] = min(int(preview.get("repetition_count") or 2), 2)
    preview["max_mutation_arms"] = 1
    active_family_row = next(
        (
            row
            for row in build_stage6_family_activation_rows(
                family_rows=_default_stage6_family_rows(),
                compounding_quality_rows=_default_stage6_compounding_quality_rows(),
                cross_lane_review=cross_lane_review,
            )
            if str(row.get("lane_id") or "") == lane_id and str(row.get("activation_status") or "") == "active"
        ),
        None,
    )
    control_envelope = dict(_STAGE6_CONTROL_ENVELOPE_BY_LANE.get(lane_id) or {})
    activation_policy = {
        "mode": "metadata_only",
        "active_family_id": str((active_family_row or {}).get("family_id") or ""),
        "active_family_kind": str((active_family_row or {}).get("family_kind") or ""),
        "blocked_family_ids": [str((active_family_row or {}).get("family_id") or "")] if active_family_row else [],
        "activation_rationale": "stage6_default_metadata_only_activation",
        "single_family_lockout_execution": {},
    }
    if lane_id == "lane.systems" and active_family_row is not None:
        activation_policy = {
            "mode": "systems_primary_control_envelope_lockout_v1",
            "active_family_id": str(active_family_row.get("family_id") or ""),
            "active_family_kind": str(active_family_row.get("family_kind") or ""),
            "blocked_family_ids": [str(active_family_row.get("family_id") or "")],
            "activation_rationale": "exercise_real_execution_family_lockout_for_systems_primary_family",
            "single_family_lockout_execution": control_envelope,
        }
    preview["activation_policy"] = activation_policy
    return preview
