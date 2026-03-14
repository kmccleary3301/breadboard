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
                "applies_to_lanes": ["lane.harness", "lane.repo_swe", "lane.atp", "lane.systems"],
                "reversible": True,
                "safety_constraints": ["policy_bundle_must_exist", "lane_topology_must_be_supported"],
            },
            {
                "operator_id": "mut.budget.class_a_to_class_b_v1",
                "operator_class": "budget",
                "input_scope": "campaign.budget_class",
                "output_scope": "candidate.budget_class",
                "applies_to_lanes": ["lane.repo_swe", "lane.systems"],
                "reversible": True,
                "safety_constraints": ["budget_policy_bundle_must_exist"],
            },
            {
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "operator_class": "tool_scope",
                "input_scope": "campaign.allowed_tools",
                "output_scope": "candidate.tool_scope",
                "applies_to_lanes": ["lane.repo_swe"],
                "reversible": True,
                "safety_constraints": ["tool_must_be_allowed_by_lane_policy"],
            },
            {
                "operator_id": "mut.prompt.tighten_acceptance_v1",
                "operator_class": "prompt",
                "input_scope": "candidate.prompt_profile",
                "output_scope": "candidate.prompt_profile",
                "applies_to_lanes": ["lane.harness", "lane.repo_swe"],
                "reversible": True,
                "safety_constraints": ["claim_tier_cannot_increase_without_replay"],
            },
            {
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "operator_class": "policy_bundle",
                "input_scope": "campaign.memory_policy_id",
                "output_scope": "candidate.memory_policy_id",
                "applies_to_lanes": ["lane.repo_swe", "lane.systems"],
                "reversible": True,
                "safety_constraints": ["shadow_only", "no_claim_bearing_use_without_ablation"],
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
