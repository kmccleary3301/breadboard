from __future__ import annotations

from breadboard_ext.darwin.stage5 import (
    build_stage5_compounding_cases,
    build_stage5_search_policy_v2,
    select_stage5_search_policy_arms,
)


def test_build_stage5_search_policy_v2_consumes_promoted_family_state() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.repo_swe",
        budget_class="class_a",
        family_rows=[
            {
                "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
                "family_key": "policy.topology.pev_v0",
                "family_kind": "topology",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "promoted",
                "replay_status": "supported",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            }
        ],
    )
    assert policy["schema"] == "breadboard.darwin.stage5.search_policy.v2"
    assert policy["campaign_class"] == "C1 Discovery"
    assert policy["consumes_family_state"] is True
    assert policy["repetition_count"] >= 4
    assert policy["comparison_mode_quotas"]["warm_start"] == 1
    assert policy["family_priors"][0]["source_operator_id"] == "mut.topology.single_to_pev_v1"


def test_build_stage5_search_policy_v2_tightens_repo_swe_after_policy_stability() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.repo_swe",
        budget_class="class_a",
        family_rows=[
            {
                "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
                "family_key": "policy.topology.pev_v0",
                "family_kind": "topology",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "promoted",
                "replay_status": "supported",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            }
        ],
        policy_stability_rows=[
            {
                "lane_id": "lane.repo_swe",
                "policy_review_conclusion": "tighten",
            }
        ],
    )
    assert policy["policy_tightening"]["enabled"] is True
    assert policy["max_mutation_arms"] == 1
    assert policy["repetition_count"] >= 8
    assert policy["policy_tightening"]["repetition_count"] >= 8
    assert policy["policy_review_conclusion"] == "tighten"


def test_build_stage5_search_policy_v2_supports_systems_family_state() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.systems",
        budget_class="class_a",
        family_rows=[
            {
                "family_id": "component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0",
                "family_key": "policy.shadow_memory_enable_v1",
                "family_kind": "policy",
                "lane_id": "lane.systems",
                "lifecycle_status": "promoted",
                "replay_status": "missing",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]},
            }
        ],
    )
    assert policy["policy_id"] == "darwin.stage5.search_policy.systems.v2"
    assert policy["lane_id"] == "lane.systems"
    assert policy["family_priors"][0]["source_operator_id"] == "mut.policy.shadow_memory_enable_v1"


def test_select_stage5_search_policy_arms_emits_warm_and_lockout_pairs() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.repo_swe",
        budget_class="class_a",
        family_rows=[
            {
                "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
                "family_key": "policy.topology.pev_v0",
                "family_kind": "topology",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "promoted",
                "replay_status": "supported",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            }
        ],
    )
    selected = select_stage5_search_policy_arms(
        search_policy=policy,
        candidate_rows=[
            {
                "campaign_arm_id": "arm.repo_swe.control.stage4.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "baseline_seed",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "control_tag": "control",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.repo_swe.topology.stage4.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
        ],
    )
    modes = [row["comparison_mode"] for row in selected]
    assert "cold_start" in modes
    assert "warm_start" in modes
    assert "family_lockout" in modes
    warm = next(row for row in selected if row["comparison_mode"] == "warm_start")
    lockout = next(row for row in selected if row["comparison_mode"] == "family_lockout")
    assert warm["family_context"]["allowed_family_ids"]
    assert lockout["family_context"]["blocked_family_ids"]


def test_select_stage5_search_policy_arms_tightened_repo_swe_limits_spillover() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.repo_swe",
        budget_class="class_a",
        family_rows=[
            {
                "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
                "family_key": "policy.topology.pev_v0",
                "family_kind": "topology",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "promoted",
                "replay_status": "supported",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            }
        ],
        policy_stability_rows=[
            {
                "lane_id": "lane.repo_swe",
                "policy_review_conclusion": "tighten",
            }
        ],
    )
    selected = select_stage5_search_policy_arms(
        search_policy=policy,
        candidate_rows=[
            {
                "campaign_arm_id": "arm.repo_swe.control.stage5.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "baseline_seed",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "control_tag": "control",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.repo_swe.topology.stage5.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.repo_swe.policy.stage5.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
        ],
    )
    arm_ids = [row["campaign_arm_id"] for row in selected]
    assert "arm.repo_swe.control.stage5.v0.cold-start.stage5.v0" in arm_ids
    assert "arm.repo_swe.topology.stage5.v0.warm-start.stage5.v0" in arm_ids
    assert "arm.repo_swe.topology.stage5.v0.family-lockout.stage5.v0" in arm_ids
    assert not any(arm_id.startswith("arm.repo_swe.policy.stage5.v0") for arm_id in arm_ids)


def test_select_stage5_search_policy_arms_supports_systems() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.systems",
        budget_class="class_a",
        family_rows=[
            {
                "family_id": "component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0",
                "family_key": "policy.shadow_memory_enable_v1",
                "family_kind": "policy",
                "lane_id": "lane.systems",
                "lifecycle_status": "promoted",
                "replay_status": "missing",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]},
            }
        ],
    )
    selected = select_stage5_search_policy_arms(
        search_policy=policy,
        candidate_rows=[
            {
                "campaign_arm_id": "arm.systems.control.stage4.v0",
                "lane_id": "lane.systems",
                "operator_id": "baseline_seed",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "control_tag": "control",
                "task_class": "systems_repair_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.systems.policy.stage4.v0",
                "lane_id": "lane.systems",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "systems_repair_workspace",
                "repetition_count": 2,
            },
        ],
    )
    modes = [row["comparison_mode"] for row in selected]
    assert "cold_start" in modes
    assert "warm_start" in modes
    assert "family_lockout" in modes
    warm = next(row for row in selected if row["comparison_mode"] == "warm_start")
    assert warm["operator_id"] == "mut.policy.shadow_memory_enable_v1"


def test_build_stage5_compounding_cases_detects_reuse_lift() -> None:
    cases = build_stage5_compounding_cases(
        comparison_rows=[
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.topology.warm_start",
                "operator_id": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -50,
                "delta_cost_usd": -0.0001,
                "power_signal_class": "score_retained_runtime_improved",
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.a"], "blocked_family_ids": []},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "a" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.topology.family_lockout",
                "operator_id": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -10,
                "delta_cost_usd": 0.0,
                "power_signal_class": "no_signal",
                "comparison_mode": "family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.a"]},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "a" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
        ]
    )
    assert len(cases) == 1
    assert cases[0]["conclusion"] == "reuse_lift"
