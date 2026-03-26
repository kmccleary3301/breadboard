from __future__ import annotations

import json

from scripts.build_darwin_stage4_operator_ev_report_v0 import build_stage4_operator_ev_report
from scripts.build_darwin_stage4_topology_ev_report_v0 import build_stage4_topology_ev_report
from scripts.run_darwin_stage4_live_economics_pilot_v0 import build_stage4_live_comparisons


def test_build_stage4_live_comparisons_uses_matching_repetition_baseline() -> None:
    run_rows = [
        {
            "lane_id": "lane.repo_swe",
            "campaign_arm_id": "arm.repo_swe.control.stage4.v0",
            "operator_id": "baseline_seed",
            "topology_id": "policy.topology.single_v0",
            "control_tag": "control",
            "repetition_index": 1,
            "primary_score": 1.0,
            "wall_clock_ms": 100,
            "route_class": "default_worker",
            "execution_mode": "live",
            "comparison_class": "stage4_live_economics",
            "budget_class": "class_a",
            "evaluator_pack_version": "stage4.evalpack.lane.repo_swe.task_stage4_lane_repo_swe.v1",
            "comparison_envelope_digest": "a" * 64,
            "control_reserve_policy": "replication=0.20;control=0.10",
            "verifier_status": "passed",
            "cost_source": "estimated_from_pricing_table",
            "cost_estimate": 0.002,
        },
        {
            "lane_id": "lane.repo_swe",
            "campaign_arm_id": "arm.repo_swe.control.stage4.v0",
            "operator_id": "baseline_seed",
            "topology_id": "policy.topology.single_v0",
            "control_tag": "control",
            "repetition_index": 2,
            "primary_score": 1.0,
            "wall_clock_ms": 300,
            "route_class": "default_worker",
            "execution_mode": "live",
            "comparison_class": "stage4_live_economics",
            "budget_class": "class_a",
            "evaluator_pack_version": "stage4.evalpack.lane.repo_swe.task_stage4_lane_repo_swe.v1",
            "comparison_envelope_digest": "a" * 64,
            "control_reserve_policy": "replication=0.20;control=0.10",
            "verifier_status": "passed",
            "cost_source": "estimated_from_pricing_table",
            "cost_estimate": 0.002,
        },
        {
            "lane_id": "lane.repo_swe",
            "campaign_arm_id": "arm.repo_swe.topology.stage4.v0",
            "operator_id": "mut.topology.single_to_pev_v1",
            "topology_id": "policy.topology.pev_v0",
            "control_tag": "mutation",
            "repetition_index": 2,
            "primary_score": 1.0,
            "wall_clock_ms": 250,
            "route_class": "default_worker",
            "execution_mode": "live",
            "comparison_class": "stage4_live_economics",
            "budget_class": "class_a",
            "evaluator_pack_version": "stage4.evalpack.lane.repo_swe.task_stage4_lane_repo_swe.v1",
            "comparison_envelope_digest": "a" * 64,
            "control_reserve_policy": "replication=0.20;control=0.10",
            "verifier_status": "passed",
            "cost_source": "estimated_from_pricing_table",
            "cost_estimate": 0.002,
            "search_policy_selection": {},
        },
    ]
    rows = build_stage4_live_comparisons(run_rows)
    assert len(rows) == 1
    assert rows[0]["repetition_index"] == 2
    assert rows[0]["delta_runtime_ms"] == -50
    assert rows[0]["positive_power_signal"] is True
    assert rows[0]["power_signal_class"] == "score_retained_runtime_improved"


def test_build_stage4_ev_reports_from_existing_artifacts() -> None:
    operator_summary = build_stage4_operator_ev_report()
    topology_summary = build_stage4_topology_ev_report()
    assert operator_summary["row_count"] >= 1
    assert topology_summary["row_count"] >= 1

    operator_payload = json.loads(open(operator_summary["out_json"], "r", encoding="utf-8").read())
    topology_payload = json.loads(open(topology_summary["out_json"], "r", encoding="utf-8").read())
    assert any(row["positive_power_signal_count"] >= 0 for row in operator_payload["rows"])
    assert any(row["positive_power_signal_count"] >= 0 for row in topology_payload["rows"])
