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
        repo_swe_family_ab={
            "family_selection_status": "settled_topology",
            "preferred_family_kind": "topology",
            "family_selection_reason": "unit_test_settled",
        },
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
        repo_swe_family_ab={
            "family_selection_status": "settled_topology",
            "preferred_family_kind": "topology",
            "family_selection_reason": "unit_test_settled",
        },
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


def test_build_stage5_search_policy_v2_adds_systems_stability_probe() -> None:
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
        policy_stability_rows=[
            {
                "lane_id": "lane.systems",
                "policy_review_conclusion": "continue",
            }
        ],
    )
    assert policy["policy_stability_probe"]["enabled"] is True
    assert policy["max_mutation_arms"] == 1
    assert policy["repetition_count"] >= 6
    assert policy["policy_stability_probe"]["reason"] == "systems_stability_probe_on_promoted_policy_family"


def test_build_stage5_search_policy_v2_uses_systems_primary_cross_lane_weight() -> None:
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
        cross_lane_review={
            "current_primary_lane_id": "lane.systems",
            "rows": [
                {
                    "lane_id": "lane.systems",
                    "lane_weight": "primary_proving_lane",
                    "lane_weight_reason": "systems_has_cleaner_current_compounding_surface",
                }
            ],
        },
    )
    assert policy["cross_lane_weighting"]["systems_primary"] is True
    assert policy["max_mutation_arms"] == 1
    assert policy["repetition_count"] >= 8
    assert policy["policy_stability_probe"]["reason"] == "systems_weighted_primary_probe"


def test_build_stage5_search_policy_v2_marks_systems_primary_family_confidence() -> None:
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
        cross_lane_review={
            "current_primary_lane_id": "lane.systems",
            "rows": [
                {
                    "lane_id": "lane.systems",
                    "lane_weight": "primary_proving_lane",
                    "lane_weight_reason": "systems_has_cleaner_current_compounding_surface",
                }
            ],
        },
    )
    assert policy["compounding_weighting"]["systems_primary_policy_boost"] is True
    assert policy["family_priors"][0]["confidence_class"] == "systems_primary_active_family"


def test_build_stage5_search_policy_v2_adds_repo_swe_family_probe() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.repo_swe",
        budget_class="class_a",
        repo_swe_family_ab={
            "family_selection_status": "settled_topology",
            "preferred_family_kind": "topology",
            "family_selection_reason": "unit_test_settled",
        },
        family_rows=[
            {
                "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
                "family_key": "policy.topology.pev_v0",
                "family_kind": "topology",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "promoted",
                "replay_status": "supported",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            },
            {
                "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
                "family_key": "policy.tool_scope.add_git_diff_v1",
                "family_kind": "tool_scope",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "withheld",
                "replay_status": "missing",
                "transfer_eligibility": {"allowed_target_lanes": []},
            },
        ],
        policy_stability_rows=[
            {
                "lane_id": "lane.repo_swe",
                "policy_review_conclusion": "continue",
                "stability_class": "mixed_negative",
                "flat_count": 5,
            }
        ],
    )
    assert policy["family_probe"]["enabled"] is True
    assert policy["max_mutation_arms"] == 1
    assert policy["repetition_count"] >= 6
    assert policy["family_priors"][0]["source_operator_id"] == "mut.tool_scope.add_git_diff_v1"


def test_build_stage5_search_policy_v2_suppresses_repo_swe_family_probe_for_challenge_lane() -> None:
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
            },
            {
                "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
                "family_key": "policy.tool_scope.add_git_diff_v1",
                "family_kind": "tool_scope",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "withheld",
                "replay_status": "missing",
                "transfer_eligibility": {"allowed_target_lanes": []},
            },
        ],
        policy_stability_rows=[
            {
                "lane_id": "lane.repo_swe",
                "policy_review_conclusion": "continue",
                "stability_class": "mixed_negative",
                "flat_count": 5,
            }
        ],
        cross_lane_review={
            "current_primary_lane_id": "lane.systems",
            "rows": [
                {
                    "lane_id": "lane.repo_swe",
                    "lane_weight": "challenge_lane",
                    "lane_weight_reason": "repo_swe_protocol_challenge_only",
                }
            ],
        },
    )
    assert policy["cross_lane_weighting"]["repo_swe_challenge"] is True
    assert policy["family_probe"]["enabled"] is False
    assert policy["max_mutation_arms"] == 1
    assert policy["repetition_count"] == 4


def test_build_stage5_search_policy_v2_suppresses_repo_swe_family_probe_for_stale_family_surface() -> None:
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
            },
            {
                "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
                "family_key": "policy.tool_scope.add_git_diff_v1",
                "family_kind": "tool_scope",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "withheld",
                "replay_status": "missing",
                "transfer_eligibility": {"allowed_target_lanes": []},
            },
        ],
        policy_stability_rows=[
            {
                "lane_id": "lane.repo_swe",
                "policy_review_conclusion": "continue",
                "stability_class": "mixed_negative",
                "flat_count": 5,
            }
        ],
        repo_swe_family_ab={
            "completion_status": "stale_or_incomplete",
            "family_selection_status": "stale_or_incomplete",
            "family_selection_reason": "family_ab_bundle_contains_non_claim_eligible_or_missing_rounds",
        },
    )
    assert policy["family_probe"]["enabled"] is False
    assert policy["family_probe"]["reason"] == "repo_swe_family_surface_stale"
    assert policy["family_surface"]["status"] == "stale_or_incomplete"
    assert policy["family_surface"]["blocks_probe"] is True
    assert policy["max_mutation_arms"] == 1


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


def test_select_stage5_search_policy_arms_repo_swe_family_probe_prefers_tool_scope() -> None:
    policy = build_stage5_search_policy_v2(
        lane_id="lane.repo_swe",
        budget_class="class_a",
        repo_swe_family_ab={
            "family_selection_status": "settled_topology",
            "preferred_family_kind": "topology",
            "family_selection_reason": "unit_test_settled",
        },
        family_rows=[
            {
                "family_id": "component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0",
                "family_key": "policy.topology.pev_v0",
                "family_kind": "topology",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "promoted",
                "replay_status": "supported",
                "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]},
            },
            {
                "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
                "family_key": "policy.tool_scope.add_git_diff_v1",
                "family_kind": "tool_scope",
                "lane_id": "lane.repo_swe",
                "lifecycle_status": "withheld",
                "replay_status": "missing",
                "transfer_eligibility": {"allowed_target_lanes": []},
            },
        ],
        policy_stability_rows=[
            {
                "lane_id": "lane.repo_swe",
                "policy_review_conclusion": "continue",
                "stability_class": "mixed_negative",
                "flat_count": 5,
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
                "campaign_arm_id": "arm.repo_swe.tool_scope.stage5.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
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
    assert "arm.repo_swe.tool_scope.stage5.v0.warm-start.stage5.v0" in arm_ids
    assert "arm.repo_swe.tool_scope.stage5.v0.family-lockout.stage5.v0" in arm_ids
    assert not any(arm_id.startswith("arm.repo_swe.topology.stage5.v0") for arm_id in arm_ids)


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


def test_select_stage5_search_policy_arms_systems_stability_probe_limits_spillover() -> None:
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
        policy_stability_rows=[
            {
                "lane_id": "lane.systems",
                "policy_review_conclusion": "continue",
            }
        ],
    )
    selected = select_stage5_search_policy_arms(
        search_policy=policy,
        candidate_rows=[
            {
                "campaign_arm_id": "arm.systems.control.stage5.v0",
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
                "campaign_arm_id": "arm.systems.policy.stage5.v0",
                "lane_id": "lane.systems",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "systems_repair_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.systems.topology.stage5.v0",
                "lane_id": "lane.systems",
                "operator_id": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "systems_repair_workspace",
                "repetition_count": 2,
            },
        ],
    )
    arm_ids = [row["campaign_arm_id"] for row in selected]
    assert "arm.systems.control.stage5.v0.cold-start.stage5.v0" in arm_ids
    assert "arm.systems.policy.stage5.v0.warm-start.stage5.v0" in arm_ids
    assert "arm.systems.policy.stage5.v0.family-lockout.stage5.v0" in arm_ids
    assert not any(arm_id.startswith("arm.systems.topology.stage5.v0") for arm_id in arm_ids)


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
                "search_policy_selection": {
                    "policy_digest": "warm-digest",
                    "family_surface_status": "settled_topology",
                    "lane_weight": "challenge_lane",
                },
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
                "search_policy_selection": {
                    "policy_digest": "lockout-digest",
                    "family_surface_status": "settled_topology",
                    "lane_weight": "challenge_lane",
                },
            },
        ]
    )
    assert len(cases) == 1
    assert cases[0]["conclusion"] == "reuse_lift"
    assert cases[0]["policy_provenance"]["warm_start_policy_digest"] == "warm-digest"
    assert cases[0]["policy_provenance"]["family_lockout_policy_digest"] == "lockout-digest"
    assert cases[0]["policy_provenance"]["warm_start_family_surface_status"] == "settled_topology"
    assert cases[0]["policy_provenance"]["warm_start_lane_weight"] == "challenge_lane"


def test_build_stage5_compounding_cases_marks_small_deltas_flat() -> None:
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
                "delta_runtime_ms": -12,
                "delta_cost_usd": 0.00008,
                "power_signal_class": "score_retained_runtime_improved",
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.a"], "blocked_family_ids": []},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "b" * 64,
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
                "delta_runtime_ms": -8,
                "delta_cost_usd": 0.00003,
                "power_signal_class": "score_retained_runtime_improved",
                "comparison_mode": "family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.a"]},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "b" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
        ]
    )
    assert len(cases) == 1
    assert cases[0]["conclusion"] == "flat"
    assert cases[0]["outcome_deltas"]["runtime_lift_ms"] == -4


def test_build_stage5_compounding_cases_prefers_cost_when_runtime_is_neutral() -> None:
    cases = build_stage5_compounding_cases(
        comparison_rows=[
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.tool_scope.warm_start",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -2,
                "delta_cost_usd": -0.00025,
                "power_signal_class": "score_retained_cost_improved",
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.tool"], "blocked_family_ids": []},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "c" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.tool_scope.family_lockout",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -6,
                "delta_cost_usd": 0.00015,
                "power_signal_class": "score_retained_runtime_improved",
                "comparison_mode": "family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.tool"]},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "c" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
        ]
    )
    assert len(cases) == 1
    assert cases[0]["conclusion"] == "reuse_lift"
    assert cases[0]["decision_basis"] == "cost_better_runtime_neutral"


def test_build_stage5_compounding_cases_marks_cross_metric_tradeoff_flat() -> None:
    cases = build_stage5_compounding_cases(
        comparison_rows=[
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.tool_scope.warm_start",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": 12,
                "delta_cost_usd": -0.0003,
                "power_signal_class": "score_retained_cost_improved",
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.tool"], "blocked_family_ids": []},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "d" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.tool_scope.family_lockout",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -8,
                "delta_cost_usd": 0.00005,
                "power_signal_class": "score_retained_runtime_improved",
                "comparison_mode": "family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.tool"]},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "d" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
        ]
    )
    assert len(cases) == 1
    assert cases[0]["conclusion"] == "flat"
    assert cases[0]["decision_basis"] == "cross_metric_tradeoff"


def test_build_stage5_compounding_cases_uses_cost_first_for_tool_scope() -> None:
    cases = build_stage5_compounding_cases(
        comparison_rows=[
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.tool_scope.warm_start",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": 7,
                "delta_cost_usd": 0.0001,
                "power_signal_class": "no_signal",
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.tool"], "blocked_family_ids": []},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "e" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
            {
                "lane_id": "lane.repo_swe",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.repo_swe.tool_scope.family_lockout",
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "topology_id": "policy.topology.pev_v0",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -6,
                "delta_cost_usd": 0.00035,
                "power_signal_class": "score_retained_runtime_improved",
                "comparison_mode": "family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.tool"]},
                "evaluator_pack_version": "stage4.evalpack.repo.v1",
                "comparison_envelope_digest": "e" * 64,
                "provider_origin": "openai",
                "cost_source": "estimated_from_pricing_table",
            },
        ]
    )
    assert len(cases) == 1
    assert cases[0]["conclusion"] == "reuse_lift"
    assert cases[0]["decision_protocol"]["objective"] == "cost_first"
    assert cases[0]["decision_deadband"]["runtime_lift_ms"] == 15
