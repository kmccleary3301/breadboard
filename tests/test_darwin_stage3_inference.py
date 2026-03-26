from __future__ import annotations

from breadboard_ext.darwin.stage3_inference import (
    DEFAULT_FILTER_ROUTE,
    DEFAULT_WORKER_ROUTE,
    build_stage3_campaign_arm,
    build_stage3_proposal_prompt,
    build_stage3_usage_telemetry,
    resolve_stage3_route,
    validate_matched_budget_pair,
)


def test_resolve_stage3_route_defaults_to_mini_and_nano() -> None:
    worker = resolve_stage3_route(task_class="repo_patch_workspace")
    filter_route = resolve_stage3_route(task_class="repo_patch_workspace", role="filter")
    assert worker["route_id"] == DEFAULT_WORKER_ROUTE
    assert filter_route["route_id"] == DEFAULT_FILTER_ROUTE
    assert worker["execution_mode"] in {"live", "scaffold"}
    assert worker["claim_eligible"] is False


def test_build_stage3_usage_telemetry_normalizes_prompt_usage() -> None:
    route = resolve_stage3_route(task_class="repo_patch_workspace")
    telemetry = build_stage3_usage_telemetry(
        lane_id="lane.repo_swe",
        operator_id="mut.topology.single_to_pev_v1",
        route=route,
        proposal_prompt=build_stage3_proposal_prompt(
            lane_id="lane.repo_swe",
            operator_id="mut.topology.single_to_pev_v1",
            topology_id="policy.topology.pev_v0",
            budget_class="class_a",
            target_id="stage3.target.lane.repo_swe.tranche2.v0",
        ),
        campaign_arm_id="arm.repo_swe.topology.v0",
        repetition_index=1,
        control_tag="mutation",
        wall_clock_ms=1250,
    )
    assert telemetry["route_id"] == route["route_id"]
    assert telemetry["usage"]["prompt_tokens"] > 0
    assert telemetry["wall_clock_ms"] == 1250


def test_validate_matched_budget_pair_rejects_budget_mismatch() -> None:
    baseline = {
        "lane_id": "lane.repo_swe",
        "budget_class": "class_a",
        "comparison_class": "stage3_bounded_real_inference",
        "route_class": "default_worker",
        "execution_mode": "scaffold",
        "evaluator_pack_version": "stage4.evalpack.repo_swe.v1",
        "support_envelope_digest": "a" * 64,
        "task_id": "task.stage3.lane.repo_swe.arm",
        "control_reserve_policy": "replication=0.20;control=0.10",
    }
    candidate = dict(baseline)
    candidate["budget_class"] = "class_b"
    out = validate_matched_budget_pair(baseline=baseline, candidate=candidate)
    assert out.ok is False
    assert out.reason == "budget_class_mismatch"


def test_validate_matched_budget_pair_rejects_route_mismatch() -> None:
    baseline = {
        "lane_id": "lane.repo_swe",
        "budget_class": "class_a",
        "comparison_class": "stage3_bounded_real_inference",
        "route_class": "default_worker",
        "execution_mode": "scaffold",
        "evaluator_pack_version": "stage4.evalpack.repo_swe.v1",
        "support_envelope_digest": "a" * 64,
        "task_id": "task.stage3.lane.repo_swe.arm",
        "control_reserve_policy": "replication=0.20;control=0.10",
    }
    candidate = dict(baseline)
    candidate["route_class"] = "bulk_filter"
    out = validate_matched_budget_pair(baseline=baseline, candidate=candidate)
    assert out.ok is False
    assert out.reason == "route_class_mismatch"


def test_build_stage3_campaign_arm_preserves_route_metadata() -> None:
    route = resolve_stage3_route(task_class="systems_reward_runtime")
    arm = build_stage3_campaign_arm(
        arm_id="arm.systems.topology.v0",
        lane_id="lane.systems",
        operator_id="mut.topology.single_to_pev_v1",
        topology_id="policy.topology.pev_v0",
        budget_class="class_a",
        route=route,
        repetition_count=2,
        control_tag="mutation",
        task_class="systems_reward_runtime",
    )
    assert arm["route_id"] == route["route_id"]
    assert arm["repetition_count"] == 2
    assert arm["claim_eligible"] is False
