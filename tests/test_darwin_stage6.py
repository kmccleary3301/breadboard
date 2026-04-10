from __future__ import annotations

from breadboard_ext.darwin.stage6 import (
    build_stage6_activation_probe_summary,
    build_stage6_activation_transfer_linkage,
    build_stage6_compounding_cases,
    build_stage6_comparison_envelope,
    build_stage6_failed_transfer_taxonomy,
    build_stage6_family_activation_rows,
    build_stage6_transferred_family_activation_row,
    build_stage6_transfer_cases,
    build_stage6_transfer_outcome_summary,
    build_stage6_transfer_quality_scorecard,
    classify_stage6_broader_compounding_outcome,
    select_stage6_search_policy_arms,
    summarize_stage6_provider_segmentation,
)


def test_build_stage6_family_activation_rows_maps_stage5_states() -> None:
    rows = build_stage6_family_activation_rows(
        family_rows=[
            {
                "family_id": "family.systems.policy",
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
                "family_id": "family.repo.topology",
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
                "family_id": "family.repo.tool",
                "lane_id": "lane.repo_swe",
                "family_kind": "tool_scope",
                "family_key": "policy.tool_scope.add_git_diff_v1",
                "stage5_family_state": "held_back",
                "lane_weight": "challenge_lane",
                "transfer_eligibility": {"allowed_target_lanes": []},
                "replay_status": "missing",
                "activation_readiness": "held",
            },
        ],
        compounding_quality_rows=[],
        cross_lane_review={},
    )
    row_map = {row["family_id"]: row for row in rows}
    assert row_map["family.systems.policy"]["activation_status"] == "active"
    assert "single_family_lockout" in row_map["family.systems.policy"]["allowed_comparison_modes"]
    assert row_map["family.repo.topology"]["activation_status"] == "challenge"
    assert row_map["family.repo.tool"]["activation_enabled"] is False


def test_select_stage6_search_policy_arms_adds_single_family_lockout_variant() -> None:
    selected = select_stage6_search_policy_arms(
        search_policy={
            "policy_id": "darwin.stage6.search_policy.systems.preview.v1",
            "lane_id": "lane.systems",
            "campaign_class": "C1 Discovery",
            "policy_digest": "digest",
            "max_mutation_arms": 1,
            "operator_priors": [{"operator_id": "mut.policy.shadow_memory_enable_v1", "priority": 1.0}],
            "family_priors": [
                {
                    "family_id": "family.systems.policy",
                    "family_kind": "policy",
                    "source_operator_id": "mut.policy.shadow_memory_enable_v1",
                    "priority": 0.98,
                    "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]},
                }
            ],
            "policy_tightening": {"enabled": False},
            "policy_stability_probe": {"enabled": False},
            "cross_lane_weighting": {"systems_primary": True},
            "family_probe": {"enabled": False},
            "activation_policy": {
                "mode": "systems_primary_control_envelope_lockout_v1",
                "active_family_id": "family.systems.policy",
                "blocked_family_ids": ["family.systems.policy"],
                "activation_rationale": "exercise_real_execution_family_lockout_for_systems_primary_family",
                "single_family_lockout_execution": {
                    "topology_id": "policy.topology.single_v0",
                    "policy_bundle_id": "policy.topology.single_v0",
                },
            },
        },
        candidate_rows=[
            {
                "campaign_arm_id": "arm.systems.control.stage5.v0",
                "lane_id": "lane.systems",
                "operator_id": "baseline_seed",
                "topology_id": "policy.control",
                "policy_bundle_id": "policy.control",
                "budget_class": "class_a",
                "control_tag": "control",
            },
            {
                "campaign_arm_id": "arm.systems.policy.stage5.v0",
                "lane_id": "lane.systems",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "topology_id": "policy.shadow_memory_enable_v1",
                "policy_bundle_id": "policy.shadow_memory_enable_v1",
                "budget_class": "class_a",
                "control_tag": "mutation",
            },
        ],
    )
    modes = {str(row.get("comparison_mode") or "") for row in selected}
    assert "warm_start" in modes
    assert "family_lockout" in modes
    assert "single_family_lockout" in modes
    single_lockout = next(row for row in selected if str(row.get("comparison_mode") or "") == "single_family_lockout")
    assert single_lockout["topology_id"] == "policy.topology.single_v0"
    assert single_lockout["policy_bundle_id"] == "policy.topology.single_v0"
    assert single_lockout["search_policy_selection"]["activation_policy_mode"] == "systems_primary_control_envelope_lockout_v1"


def test_build_stage6_compounding_cases_prefers_single_family_lockout_when_present() -> None:
    cases = build_stage6_compounding_cases(
        comparison_rows=[
            {
                "lane_id": "lane.systems",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.systems.policy.warm_start",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -35,
                "delta_cost_usd": -0.0001,
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.systems.policy"], "blocked_family_ids": []},
                "evaluator_pack_version": "stage4.evalpack.systems.v1",
                "comparison_envelope_digest": "f" * 64,
                "provider_origin": "openai",
                "search_policy_selection": {"lane_weight": "primary_proving_lane"},
            },
            {
                "lane_id": "lane.systems",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.systems.policy.family_lockout",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -10,
                "delta_cost_usd": 0.0,
                "comparison_mode": "family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.systems.policy"]},
                "evaluator_pack_version": "stage4.evalpack.systems.v1",
                "comparison_envelope_digest": "f" * 64,
                "provider_origin": "openai",
            },
            {
                "lane_id": "lane.systems",
                "campaign_class": "C1 Discovery",
                "campaign_arm_id": "arm.systems.policy.single_family_lockout",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "repetition_index": 1,
                "comparison_valid": True,
                "claim_eligible": True,
                "delta_score": 0.0,
                "delta_runtime_ms": -5,
                "delta_cost_usd": 0.0,
                "comparison_mode": "single_family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.systems.policy"]},
                "evaluator_pack_version": "stage4.evalpack.systems.v1",
                "comparison_envelope_digest": "f" * 64,
                "provider_origin": "openai",
            },
        ],
        activation_rows=[
            {
                "family_id": "family.systems.policy",
                "family_kind": "policy",
                "activation_status": "active",
                "lane_id": "lane.systems",
                "lane_weight": "primary_proving_lane",
            }
        ],
    )
    assert len(cases) == 1
    assert cases[0]["comparison_mode_pair"] == ["warm_start", "single_family_lockout"]
    assert cases[0]["conclusion"] == "reuse_lift"
    assert cases[0]["comparison_envelope"]["schema"] == "breadboard.darwin.stage6.comparison_envelope.v1"


def test_build_stage6_transfer_cases_emits_activation_probe_for_positive_active_family() -> None:
    rows = build_stage6_transfer_cases(
        activation_rows=[
            {
                "family_id": "family.systems.policy",
                "lane_id": "lane.systems",
                "family_kind": "policy",
                "activation_status": "active",
                "lane_weight": "primary_proving_lane",
                "activation_enabled": True,
                "transfer_targets": ["lane.scheduling"],
                "replay_status": "supported",
            }
        ],
        compounding_cases=[
            {
                "compounding_case_id": "case.systems.policy.r1",
                "lane_id": "lane.systems",
                "comparison_envelope": {"comparison_envelope_digest": "a" * 64},
                "conclusion": "reuse_lift",
            }
        ],
    )
    assert len(rows) == 1
    assert rows[0]["transfer_status"] == "activation_probe"
    assert rows[0]["target_lane_id"] == "lane.scheduling"


def test_build_stage6_activation_probe_summary_classifies_positive_lane() -> None:
    payload = build_stage6_activation_probe_summary(
        activation_rows=[
            {
                "family_id": "family.systems.policy",
                "lane_id": "lane.systems",
                "family_kind": "policy",
                "activation_status": "active",
                "activation_enabled": True,
                "lane_weight": "primary_proving_lane",
            }
        ],
        compounding_cases=[
            {
                "compounding_case_id": "case.systems.policy.r1",
                "lane_id": "lane.systems",
                "conclusion": "reuse_lift",
            },
            {
                "compounding_case_id": "case.systems.policy.r2",
                "lane_id": "lane.systems",
                "conclusion": "flat",
            },
        ],
    )
    assert payload["row_count"] == 1
    row = payload["rows"][0]
    assert row["activation_probe_classification"] == "positive_activation_probe"
    assert row["reuse_lift_count"] == 1


def test_build_stage6_transfer_cases_uses_execution_rows_for_retained_status() -> None:
    rows = build_stage6_transfer_cases(
        activation_rows=[
            {
                "family_id": "family.systems.policy",
                "lane_id": "lane.systems",
                "family_kind": "policy",
                "activation_status": "active",
                "lane_weight": "primary_proving_lane",
                "activation_enabled": True,
                "transfer_targets": ["lane.scheduling"],
                "replay_status": "supported",
            }
        ],
        compounding_cases=[
            {
                "compounding_case_id": "case.systems.policy.r1",
                "lane_id": "lane.systems",
                "family_id": "family.systems.policy",
                "family_kind": "policy",
                "comparison_envelope": {"comparison_envelope_digest": "a" * 64},
                "conclusion": "flat",
            }
        ],
        transfer_execution_rows=[
            {
                "source_lane_id": "lane.systems",
                "target_lane_id": "lane.scheduling",
                "family_id": "family.systems.policy",
                "comparison_valid": True,
                "target_score_lift": 0.4,
                "target_execution_status": "complete",
                "replay_status": "supported",
                "transfer_policy_mode": "systems_primary_hybrid_scheduling_transfer_v1",
                "transfer_policy_rationale": "bounded systems transfer",
            }
        ],
    )
    assert rows[0]["transfer_status"] == "retained"
    assert rows[0]["transfer_reason"] == "target_lane_score_improved_materially"
    assert rows[0]["transfer_policy_mode"] == "systems_primary_hybrid_scheduling_transfer_v1"


def test_build_stage6_transfer_summary_taxonomy_and_linkage() -> None:
    transfer_cases = [
        {
            "transfer_case_id": "transfer.retained",
            "source_lane_id": "lane.systems",
            "target_lane_id": "lane.scheduling",
            "family_id": "family.systems.policy",
            "family_kind": "policy",
            "activation_status": "active",
            "lane_weight": "primary_proving_lane",
            "transfer_status": "retained",
            "transfer_reason": "target_lane_score_improved_materially",
            "transfer_policy_mode": "systems_primary",
            "replay_status": "supported",
            "source_conclusion": "flat",
            "comparison_envelope_digest": "a" * 64,
        },
        {
            "transfer_case_id": "transfer.invalid",
            "source_lane_id": "lane.repo_swe",
            "target_lane_id": "lane.systems",
            "family_id": "family.repo.tool",
            "family_kind": "tool_scope",
            "activation_status": "inactive",
            "lane_weight": "challenge_lane",
            "transfer_status": "invalid",
            "transfer_reason": "inactive_family_not_authorized_for_stage6_transfer",
            "transfer_policy_mode": "inactive_family_block_v1",
            "replay_status": "not_applicable",
            "source_conclusion": "inactive",
            "comparison_envelope_digest": "",
        },
    ]
    summary = build_stage6_transfer_outcome_summary(transfer_cases=transfer_cases)
    taxonomy = build_stage6_failed_transfer_taxonomy(transfer_cases=transfer_cases)
    scorecard = build_stage6_transfer_quality_scorecard(
        transfer_cases=transfer_cases,
        provider_segmentation={"provider_segmentation_status": "claim_rows_segmented"},
    )
    linkage = build_stage6_activation_transfer_linkage(
        activation_probe_summary={
            "rows": [
                {
                    "lane_id": "lane.systems",
                    "activation_probe_classification": "inconclusive_activation_probe",
                    "activation_probe_reason": "mixed_flat_and_no_lift_cases",
                },
                {
                    "lane_id": "lane.repo_swe",
                    "activation_probe_classification": "positive_activation_probe",
                    "activation_probe_reason": "at_least_one_reuse_lift_case",
                },
            ]
        },
        transfer_cases=transfer_cases,
    )
    assert summary["retained_count"] == 1
    assert taxonomy["row_count"] == 1
    assert taxonomy["rows"][0]["failure_reason"] == "inactive_family_not_authorized_for_stage6_transfer"
    assert scorecard["rows"][0]["provider_segmentation_status"] == "claim_rows_segmented"
    assert linkage["row_count"] == 2


def test_build_stage6_comparison_envelope_carries_tranche3_metadata() -> None:
    envelope = build_stage6_comparison_envelope(
        lane_id="lane.scheduling",
        operator_id="mut.policy.shadow_memory_enable_v1",
        family_id="family.systems.policy",
        family_kind="policy",
        activation_status="active",
        lane_weight="retained_transfer_target",
        comparison_mode_pair=["warm_start", "family_lockout", "single_family_lockout"],
        evaluator_pack_version="stage6.evalpack.scheduling.v1",
        comparison_envelope_digest="a" * 64,
        policy_digest="b" * 64,
        active_family_id="family.systems.policy",
        source_transfer_basis="transfer.systems.scheduling",
        target_lane_id="lane.scheduling",
    )
    assert envelope["policy_digest"] == "b" * 64
    assert envelope["source_transfer_basis"] == "transfer.systems.scheduling"
    assert envelope["target_lane_id"] == "lane.scheduling"


def test_classify_stage6_broader_compounding_outcome_marks_positive() -> None:
    status, rationale = classify_stage6_broader_compounding_outcome(
        score_lift_vs_family_lockout=0.2,
        score_lift_vs_single_lockout=0.4,
        runtime_lift_vs_family_lockout_ms=10,
        runtime_lift_vs_single_lockout_ms=25,
        cost_lift_vs_family_lockout_usd=0.0,
        cost_lift_vs_single_lockout_usd=0.0,
    )
    assert status == "positive_broader_compounding"
    assert rationale == "beats_both_lockout_controls"


def test_build_stage6_transferred_family_activation_row_marks_active_target() -> None:
    row = build_stage6_transferred_family_activation_row(
        transfer_case={
            "transfer_case_id": "transfer.systems.scheduling",
            "source_lane_id": "lane.systems",
            "family_id": "family.systems.policy",
            "family_kind": "policy",
            "transfer_status": "retained",
            "transfer_reason": "target_lane_score_improved_materially",
            "replay_status": "supported",
        },
        target_lane_id="lane.scheduling",
    )
    assert row["activation_enabled"] is True
    assert row["activation_mode"] == "transferred_family_active"
    assert row["source_transfer_status"] == "retained"


def test_summarize_stage6_provider_segmentation_marks_live_claim_rows_segmented() -> None:
    summary = summarize_stage6_provider_segmentation(
        telemetry_rows=[
            {
                "requested_provider_origin": "openrouter",
                "provider_origin": "openai",
                "route_class": "default_worker",
                "execution_mode": "live",
                "cost_source": "estimated_from_pricing_table",
                "fallback_reason": "openrouter_http_401",
            }
        ],
        run_rows=[
            {
                "claim_eligible": True,
                "execution_mode": "live",
                "requested_route_id": "openrouter/gpt-5.4-mini",
                "provider_origin": "openai",
                "route_id": "openai/gpt-5.4-mini",
                "route_class": "default_worker",
                "provider_model": "openai/gpt-5.4-mini",
                "cost_source": "estimated_from_pricing_table",
            }
        ],
    )
    assert summary["provider_segmentation_status"] == "claim_rows_segmented"
    assert summary["claim_rows_have_canonical_provider_segmentation"] is True
    assert summary["fallback_reason_counts"]["openrouter_http_401"] == 1
