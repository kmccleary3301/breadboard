from __future__ import annotations

from typing import Any, Dict, List

from .historical_bridge_summary import load_prompt_centric_bridge_summary
from .holdout_generalization_pack import run_phase10_full_holdout_pack, run_phase10_pilot_holdout_pack


def build_phase10_judgment_stack() -> Dict[str, Any]:
    pilot = run_phase10_pilot_holdout_pack()
    full = run_phase10_full_holdout_pack()
    bridge = load_prompt_centric_bridge_summary()

    optional_lane_summaries = dict(full.get("summary", {}).get("optional_lane_summaries") or {})
    retained_optional_lanes: List[str] = sorted(
        lane_id
        for lane_id, lane_summary in optional_lane_summaries.items()
        if int((lane_summary or {}).get("beats_deterministic_count") or 0) >= 3
    )
    family_summaries = dict(full.get("summary", {}).get("family_summaries") or {})
    family_delta_positive = all(
        float((family_summary or {}).get("frozen_core_vs_stripped_score_delta_sum") or 0.0) > 0.0
        for family_summary in family_summaries.values()
    )
    frozen_core_promotable = (
        int(full.get("summary", {}).get("frozen_core_beats_stripped_count") or 0) >= 12
        and int(full.get("summary", {}).get("frozen_core_pass_count") or 0) >= 12
        and family_delta_positive
    )
    deterministic_baseline_stronger = int(full.get("summary", {}).get("deterministic_pass_count") or 0) > int(
        full.get("summary", {}).get("frozen_core_pass_count") or 0
    )
    outcome = "promote_core" if frozen_core_promotable else "do_not_promote_core_yet"

    return {
        "schema_version": "ctree_phase10_judgment_stack_v2",
        "pilot_summary": {
            "scenario_count": int(pilot.get("summary", {}).get("scenario_count") or 0),
            "family_count": int(pilot.get("summary", {}).get("family_count") or 0),
            "frozen_core_beats_stripped_count": int(
                pilot.get("summary", {}).get("frozen_core_beats_stripped_count") or 0
            ),
            "optional_lane_beats_deterministic_count": int(
                pilot.get("summary", {}).get("optional_lane_beats_deterministic_count") or 0
            ),
        },
        "full_holdout_summary": {
            "scenario_count": int(full.get("summary", {}).get("scenario_count") or 0),
            "base_scenario_count": int(full.get("summary", {}).get("base_scenario_count") or 0),
            "family_count": int(full.get("summary", {}).get("family_count") or 0),
            "frozen_core_beats_stripped_count": int(
                full.get("summary", {}).get("frozen_core_beats_stripped_count") or 0
            ),
            "frozen_core_pass_count": int(full.get("summary", {}).get("frozen_core_pass_count") or 0),
            "deterministic_pass_count": int(full.get("summary", {}).get("deterministic_pass_count") or 0),
            "optional_lane_beats_deterministic_count": int(
                full.get("summary", {}).get("optional_lane_beats_deterministic_count") or 0
            ),
            "order_perturbation_pass_rate": float(
                full.get("summary", {}).get("order_perturbation_pass_rate") or 0.0
            ),
            "tight_budget_perturbation_pass_rate": float(
                full.get("summary", {}).get("tight_budget_perturbation_pass_rate") or 0.0
            ),
        },
        "historical_bridge_summary": {
            "scenario_count": int(bridge.get("scenario_count") or 0),
            "total_repeat_count": int(bridge.get("total_repeat_count") or 0),
            "pass_count": int(bridge.get("pass_count") or 0),
            "task_failure_count": int(bridge.get("task_failure_count") or 0),
            "tool_policy_failure_count": int(bridge.get("tool_policy_failure_count") or 0),
        },
        "judgment": {
            "prompt_centric_baseline_resolved": True,
            "prompt_centric_bridge_operational": int(bridge.get("scenario_count") or 0) > 0,
            "prompt_centric_bridge_adverse": int(bridge.get("pass_count") or 0) == 0,
            "deterministic_reranker_required": True,
            "deterministic_baseline_stronger_than_frozen_core": deterministic_baseline_stronger,
            "frozen_core_promotable": frozen_core_promotable,
            "retained_optional_lanes": retained_optional_lanes,
            "phase10_outcome": outcome,
            "next_gate": "phase10_promotion_decision",
        },
    }
