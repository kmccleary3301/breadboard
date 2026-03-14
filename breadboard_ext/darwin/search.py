from __future__ import annotations

from datetime import datetime, timezone


BUDGET_CLASS_LIMITS = {
    "class_a": {"max_wall_clock_ms": 300_000, "max_cost_estimate": 1.0},
    "class_b": {"max_wall_clock_ms": 900_000, "max_cost_estimate": 5.0},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_mutation_operator_registry() -> dict:
    return {
        "schema": "breadboard.darwin.mutation_operator_registry.v1",
        "generated_at": _now(),
        "operators": [
            {
                "operator_id": "mut.topology.single_to_pev_v1",
                "operator_class": "topology",
                "input_scope": "campaign.topology_family",
                "output_scope": "candidate.topology_id",
                "supported_lanes": ["lane.harness", "lane.repo_swe", "lane.atp", "lane.systems", "lane.scheduling"],
                "experimental_lanes": [],
                "prohibited_lanes": ["lane.research"],
                "reversible": True,
                "safety_constraints": ["policy_bundle_must_exist", "lane_topology_must_be_supported"],
            },
            {
                "operator_id": "mut.budget.class_a_to_class_b_v1",
                "operator_class": "budget",
                "input_scope": "campaign.budget_class",
                "output_scope": "candidate.budget_class",
                "supported_lanes": ["lane.repo_swe", "lane.systems"],
                "experimental_lanes": ["lane.scheduling"],
                "prohibited_lanes": ["lane.harness", "lane.atp", "lane.research"],
                "reversible": True,
                "safety_constraints": ["budget_policy_bundle_must_exist"],
            },
            {
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "operator_class": "tool_scope",
                "input_scope": "campaign.allowed_tools",
                "output_scope": "candidate.tool_scope",
                "supported_lanes": ["lane.repo_swe"],
                "experimental_lanes": [],
                "prohibited_lanes": ["lane.harness", "lane.atp", "lane.systems", "lane.scheduling", "lane.research"],
                "reversible": True,
                "safety_constraints": ["tool_must_be_allowed_by_lane_policy"],
            },
            {
                "operator_id": "mut.prompt.tighten_acceptance_v1",
                "operator_class": "prompt",
                "input_scope": "candidate.prompt_profile",
                "output_scope": "candidate.prompt_profile",
                "supported_lanes": ["lane.harness", "lane.repo_swe"],
                "experimental_lanes": ["lane.scheduling"],
                "prohibited_lanes": ["lane.atp", "lane.systems", "lane.research"],
                "reversible": True,
                "safety_constraints": ["claim_tier_cannot_increase_without_replay"],
            },
            {
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "operator_class": "policy_bundle",
                "input_scope": "campaign.memory_policy_id",
                "output_scope": "candidate.memory_policy_id",
                "supported_lanes": ["lane.repo_swe", "lane.systems"],
                "experimental_lanes": [],
                "prohibited_lanes": ["lane.atp", "lane.harness", "lane.scheduling", "lane.research"],
                "reversible": True,
                "safety_constraints": ["shadow_only", "no_claim_bearing_use_without_ablation"],
            },
            {
                "operator_id": "mut.scheduler.strategy_value_density_v1",
                "operator_class": "scheduler_strategy",
                "input_scope": "candidate.scheduler_strategy",
                "output_scope": "candidate.scheduler_strategy",
                "supported_lanes": ["lane.scheduling"],
                "experimental_lanes": [],
                "prohibited_lanes": ["lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.research"],
                "reversible": True,
                "safety_constraints": ["scenario_pack_must_match", "constraint_checker_must_match"],
            },
            {
                "operator_id": "mut.scheduler.strategy_slack_v1",
                "operator_class": "scheduler_strategy",
                "input_scope": "candidate.scheduler_strategy",
                "output_scope": "candidate.scheduler_strategy",
                "supported_lanes": ["lane.scheduling"],
                "experimental_lanes": [],
                "prohibited_lanes": ["lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.research"],
                "reversible": True,
                "safety_constraints": ["scenario_pack_must_match", "constraint_checker_must_match"],
            },
        ],
    }


def build_search_enabled_lane_selection() -> dict:
    return {
        "schema": "breadboard.darwin.search_enabled_lane_selection.v1",
        "generated_at": _now(),
        "lanes": [
            {
                "lane_id": "lane.harness",
                "why": "cheap objective regressions and parity checks make this the safest first search-enabled lane",
                "allowed_topologies": ["policy.topology.single_v0", "policy.topology.pev_v0"],
                "preferred_operators": ["mut.topology.single_to_pev_v1", "mut.prompt.tighten_acceptance_v1"],
            },
            {
                "lane_id": "lane.repo_swe",
                "why": "repo-scale patch-and-test work is the next strongest cross-domain lane after harness",
                "allowed_topologies": ["policy.topology.single_v0", "policy.topology.pev_v0", "policy.topology.pwrv_v0"],
                "preferred_operators": [
                    "mut.topology.single_to_pev_v1",
                    "mut.budget.class_a_to_class_b_v1",
                    "mut.tool_scope.add_git_diff_v1",
                ],
            },
            {
                "lane_id": "lane.scheduling",
                "why": "constraint-checked scheduling is the next deterministic lane for proving search generalization",
                "allowed_topologies": ["policy.topology.single_v0", "policy.topology.pev_v0"],
                "preferred_operators": [
                    "mut.scheduler.strategy_value_density_v1",
                    "mut.scheduler.strategy_slack_v1",
                ],
            },
        ],
    }


def validate_budget_usage(budget_class: str, wall_clock_ms: int, cost_estimate: float) -> dict:
    limits = BUDGET_CLASS_LIMITS[budget_class]
    violations: list[str] = []
    if wall_clock_ms > limits["max_wall_clock_ms"]:
        violations.append("wall_clock_ms")
    if cost_estimate > limits["max_cost_estimate"]:
        violations.append("cost_estimate")
    return {
        "budget_class": budget_class,
        "ok": not violations,
        "limits": limits,
        "violations": violations,
    }


def build_archive_snapshot(*, baseline_rows: list[dict], mutation_rows: list[dict]) -> dict:
    rows = []
    for row in baseline_rows + mutation_rows:
        rows.append(
            {
                "candidate_id": row["candidate_id"],
                "lane_id": row["lane_id"],
                "campaign_id": row["campaign_id"],
                "parent_ids": row.get("parent_ids") or [],
                "topology_id": row.get("topology_id"),
                "policy_bundle_id": row.get("policy_bundle_id"),
                "budget_class": row.get("budget_class"),
                "primary_score": row.get("primary_score"),
                "verifier_status": row.get("verifier_status"),
                "status": row.get("status", "candidate"),
                "promotion_state": row.get("promotion_state"),
                "candidate_ref": row.get("candidate_ref"),
                "evaluation_ref": row.get("evaluation_ref"),
            }
        )
    return {
        "schema": "breadboard.darwin.archive_snapshot.v1",
        "generated_at": _now(),
        "candidate_count": len(rows),
        "rows": rows,
    }


def build_promotion_decision(
    *,
    lane_id: str,
    baseline_row: dict,
    mutation_rows: list[dict],
    replay_audit: dict | None = None,
) -> dict:
    valid_rows = [row for row in mutation_rows if row.get("comparison_valid", True)]
    winner = baseline_row
    decision = "retain_baseline"
    improvement = 0.0
    if valid_rows:
        best = max(valid_rows, key=lambda row: (float(row.get("primary_score") or 0.0), -int(row.get("wall_clock_ms") or 0)))
        delta = float(best.get("primary_score") or 0.0) - float(baseline_row.get("primary_score") or 0.0)
        if delta > 0:
            winner = best
            decision = "promote_mutation"
            improvement = round(delta, 6)
    return {
        "lane_id": lane_id,
        "decision": decision,
        "baseline_candidate_id": baseline_row["candidate_id"],
        "winner_candidate_id": winner["candidate_id"],
        "winner_primary_score": winner.get("primary_score"),
        "baseline_primary_score": baseline_row.get("primary_score"),
        "improvement": improvement,
        "rollback_candidate_id": baseline_row["candidate_id"],
        "rejected_candidate_ids": [row["candidate_id"] for row in mutation_rows if row["candidate_id"] != winner["candidate_id"]],
        "evidence_tier": "t1",
        "replay_audit": replay_audit or {},
    }
