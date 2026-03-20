from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agentic_coder_prototype.optimize import (
    ArtifactRef,
    CandidateBundle,
    CandidateChange,
    MutationBounds,
    MutableLocus,
    OptimizationInvariant,
    OptimizationTarget,
    SupportEnvelope,
    materialize_candidate,
    validate_bounded_candidate,
)

ROOT = Path(__file__).resolve().parents[2]

STAGE3_TARGETABLE_LANES = {"lane.repo_swe", "lane.systems"}
STAGE3_CONSUMED_LANES = {"lane.harness", "lane.repo_swe"}
STAGE3_MUTATION_CANARY_LANES = {"lane.repo_swe", "lane.systems"}

_TARGET_KINDS = {
    "lane.repo_swe": "repo_patch_workspace",
    "lane.systems": "systems_reward_runtime",
}

_MUTABLE_LOCI = {
    "lane.repo_swe": [
        MutableLocus(
            locus_id="topology.params",
            locus_kind="topology_parameter_vector",
            selector="execution_plan.execution_graph.topology_id",
            mutation_kind="replace",
            metadata={"component_kind": "topology"},
        ),
        MutableLocus(
            locus_id="operator.family",
            locus_kind="operator_family",
            selector="candidate.mutation_operator",
            mutation_kind="replace",
            metadata={"component_kind": "operator"},
        ),
        MutableLocus(
            locus_id="tool.scope",
            locus_kind="tool_scope",
            selector="execution_plan.bindings.tool_bindings",
            mutation_kind="replace",
            metadata={"component_kind": "policy"},
        ),
        MutableLocus(
            locus_id="policy.bundle",
            locus_kind="policy_bundle_ref",
            selector="effective_policy.resolved_policy_bundle_refs",
            mutation_kind="replace",
            metadata={"component_kind": "policy"},
        ),
        MutableLocus(
            locus_id="budget.class",
            locus_kind="budget_class_ref",
            selector="evaluator_pack.budget_envelope.budget_class",
            mutation_kind="replace",
            metadata={"component_kind": "policy"},
        ),
    ],
    "lane.systems": [
        MutableLocus(
            locus_id="topology.params",
            locus_kind="topology_parameter_vector",
            selector="execution_plan.execution_graph.topology_id",
            mutation_kind="replace",
            metadata={"component_kind": "topology"},
        ),
        MutableLocus(
            locus_id="operator.family",
            locus_kind="operator_family",
            selector="candidate.mutation_operator",
            mutation_kind="replace",
            metadata={"component_kind": "operator"},
        ),
        MutableLocus(
            locus_id="policy.bundle",
            locus_kind="policy_bundle_ref",
            selector="effective_policy.resolved_policy_bundle_refs",
            mutation_kind="replace",
            metadata={"component_kind": "policy"},
        ),
        MutableLocus(
            locus_id="evaluator.control",
            locus_kind="evaluator_control_pack",
            selector="evaluator_pack.control_pack",
            mutation_kind="replace",
            metadata={"component_kind": "evaluator_pack"},
        ),
        MutableLocus(
            locus_id="budget.class",
            locus_kind="budget_class_ref",
            selector="evaluator_pack.budget_envelope.budget_class",
            mutation_kind="replace",
            metadata={"component_kind": "policy"},
        ),
    ],
}

_INVARIANTS = {
    "lane.repo_swe": [
        OptimizationInvariant(
            invariant_id="same-workspace-shape",
            description="candidate must not silently widen workspace assumptions beyond the declared support envelope",
        ),
        OptimizationInvariant(
            invariant_id="bounded-tool-surface",
            description="candidate must not widen the supported tool surface outside the declared policy and support envelope",
        ),
    ],
    "lane.systems": [
        OptimizationInvariant(
            invariant_id="same-reward-contract",
            description="candidate must preserve the declared reward and aggregation contract unless the evaluator pack changes explicitly",
        ),
        OptimizationInvariant(
            invariant_id="bounded-execution-profile",
            description="candidate must stay within the supported systems execution profile and environment assumptions",
        ),
    ],
}


def build_stage3_optimization_target(
    *,
    lane_id: str,
    spec: Mapping[str, Any],
    baseline_artifact_ref: str,
    task_id: str,
    topology_id: str,
    policy_bundle_id: str,
) -> OptimizationTarget:
    if lane_id not in STAGE3_TARGETABLE_LANES:
        raise ValueError(f"unsupported Stage-3 optimization target lane: {lane_id}")

    support_envelope = SupportEnvelope(
        tools=list(spec.get("allowed_tools") or []),
        execution_profiles=[str(spec.get("claim_target") or "stage3-default")],
        environments=[str(spec.get("environment_digest") or "unknown-environment")],
        providers=["openai"],
        models=["gpt-5.4-mini", "gpt-5.4-nano"],
        assumptions={
            "lane_id": lane_id,
            "task_id": task_id,
            "topology_id": topology_id,
            "policy_bundle_id": policy_bundle_id,
            "budget_class": spec.get("budget_class"),
            "requires_replay_gate": True,
            "runtime_truth_mode": "singular_breadboard_runtime",
        },
        metadata={
            "source": "darwin_stage3_tranche1",
            "campaign_id": spec.get("campaign_id"),
        },
    )
    return OptimizationTarget(
        target_id=f"stage3.target.{lane_id}.tranche1.v1",
        target_kind=_TARGET_KINDS[lane_id],
        baseline_artifact_refs=[
            ArtifactRef(
                ref=baseline_artifact_ref,
                media_type="application/json",
                metadata={
                    "campaign_id": spec.get("campaign_id"),
                    "lane_id": lane_id,
                    "task_id": task_id,
                },
            )
        ],
        mutable_loci=list(_MUTABLE_LOCI[lane_id]),
        support_envelope=support_envelope,
        invariants=list(_INVARIANTS[lane_id]),
        metadata={
            "stage": "stage3",
            "tranche": "tranche1",
            "lane_id": lane_id,
            "campaign_id": spec.get("campaign_id"),
        },
    )


def build_stage3_budget_envelope(
    *,
    budget_class: str,
    wall_clock_ms: int,
    token_counts: Mapping[str, Any] | None = None,
    cost_estimate: float | int = 0.0,
    route_id: str | None = None,
    provider_model: str | None = None,
    comparison_class: str = "bounded_internal",
    replication_reserve_fraction: float = 0.2,
    control_reserve_fraction: float = 0.1,
) -> dict[str, Any]:
    token_counts_payload = dict(token_counts or {})
    prompt_tokens = int(token_counts_payload.get("prompt_tokens") or token_counts_payload.get("prompt") or 0)
    completion_tokens = int(token_counts_payload.get("completion_tokens") or token_counts_payload.get("completion") or 0)
    total_tokens = int(token_counts_payload.get("total_tokens") or (prompt_tokens + completion_tokens))
    normalized_token_counts = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    normalized_cost = float(cost_estimate)
    normalized_route = str(route_id).strip() if route_id else None
    normalized_model = str(provider_model).strip() if provider_model else None
    if normalized_route or normalized_model or total_tokens > 0:
        cost_classification = "estimated_route_priced" if normalized_cost > 0 else "usage_present_zero_cost"
    else:
        cost_classification = "exact_local_zero" if normalized_cost == 0.0 else "estimated_local_nonzero"
    return {
        "budget_class": str(budget_class),
        "wall_clock_ms": int(wall_clock_ms),
        "token_counts": normalized_token_counts,
        "cost_estimate": normalized_cost,
        "cost_classification": cost_classification,
        "comparison_class": str(comparison_class),
        "route_id": normalized_route,
        "provider_model": normalized_model,
        "replication_reserve_fraction": float(replication_reserve_fraction),
        "control_reserve_fraction": float(control_reserve_fraction),
    }


def consume_execution_plan_bindings(execution_plan: Mapping[str, Any]) -> dict[str, Any]:
    bindings = dict(execution_plan.get("bindings") or {})
    command = [str(item) for item in bindings.get("command") or []]
    cwd = str(bindings.get("cwd") or "").strip()
    out_dir = str(bindings.get("out_dir") or "").strip()
    tool_bindings = [str(item) for item in bindings.get("tool_bindings") or [] if str(item or "").strip()]
    budget_class = str(bindings.get("budget_class") or "").strip()
    if not command:
        raise ValueError("execution plan bindings.command must be non-empty")
    if not cwd:
        raise ValueError("execution plan bindings.cwd must be non-empty")
    if not out_dir:
        raise ValueError("execution plan bindings.out_dir must be non-empty")
    if not budget_class:
        raise ValueError("execution plan bindings.budget_class must be non-empty")
    return {
        "command": command,
        "cwd": cwd,
        "out_dir": out_dir,
        "tool_bindings": tool_bindings,
        "budget_class": budget_class,
        "consumed_fields": [
            "bindings.command",
            "bindings.cwd",
            "bindings.out_dir",
            "bindings.tool_bindings",
            "bindings.budget_class",
        ],
    }


def dump_stage3_optimization_target(path: Path, target: OptimizationTarget) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(target.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stage3_mutation_locus_for_operator(*, lane_id: str, mutation_operator: str) -> str | None:
    mappings = {
        "lane.repo_swe": {
            "mut.topology.single_to_pev_v1": "topology.params",
            "mut.budget.class_a_to_class_b_v1": "budget.class",
            "mut.tool_scope.add_git_diff_v1": "tool.scope",
            "mut.policy.shadow_memory_enable_v1": "policy.bundle",
        },
        "lane.systems": {
            "mut.topology.single_to_pev_v1": "topology.params",
            "mut.budget.class_a_to_class_b_v1": "budget.class",
            "mut.policy.shadow_memory_enable_v1": "policy.bundle",
        },
    }
    return mappings.get(lane_id, {}).get(mutation_operator)


def _stage3_change_value_for_operator(*, mutation_operator: str, mutation_cfg: Mapping[str, Any]) -> dict[str, Any]:
    if mutation_operator == "mut.topology.single_to_pev_v1":
        return {"topology_id": mutation_cfg["topology_id"]}
    if mutation_operator == "mut.budget.class_a_to_class_b_v1":
        return {"budget_class": mutation_cfg["budget_class"]}
    if mutation_operator == "mut.tool_scope.add_git_diff_v1":
        return {"tool_scope_delta": "enable_git_diff"}
    if mutation_operator == "mut.policy.shadow_memory_enable_v1":
        return {"policy_bundle_id": mutation_cfg["policy_bundle_id"], "shadow_memory": True}
    raise ValueError(f"unsupported Stage-3 mutation canary operator: {mutation_operator}")


def supports_stage3_mutation_canary(*, lane_id: str, mutation_operator: str) -> bool:
    return lane_id in STAGE3_MUTATION_CANARY_LANES and _stage3_mutation_locus_for_operator(
        lane_id=lane_id,
        mutation_operator=mutation_operator,
    ) is not None


def build_stage3_mutation_canary(
    *,
    lane_id: str,
    spec: Mapping[str, Any],
    parent_candidate_id: str,
    parent_candidate_ref: str,
    mutation_cfg: Mapping[str, Any],
    candidate_ref: str,
    evaluation_ref: str,
    task_id: str,
) -> dict[str, Any]:
    mutation_operator = str(mutation_cfg["mutation_operator"])
    locus_id = _stage3_mutation_locus_for_operator(lane_id=lane_id, mutation_operator=mutation_operator)
    if not locus_id:
        raise ValueError(f"Stage-3 mutation canary is not supported for {lane_id}:{mutation_operator}")

    target = build_stage3_optimization_target(
        lane_id=lane_id,
        spec=spec,
        baseline_artifact_ref=parent_candidate_ref,
        task_id=task_id,
        topology_id=str(mutation_cfg["topology_id"]),
        policy_bundle_id=str(mutation_cfg["policy_bundle_id"]),
    )
    change = CandidateChange(
        locus_id=locus_id,
        value=_stage3_change_value_for_operator(mutation_operator=mutation_operator, mutation_cfg=mutation_cfg),
        rationale=f"bounded Stage-3 canary mutation for {mutation_operator}",
        metadata={
            "mutation_operator": mutation_operator,
            "overlay_only": True,
            "lane_id": lane_id,
        },
    )
    candidate = CandidateBundle(
        candidate_id=f"{mutation_cfg['candidate_id']}.substrate.v1",
        source_target_id=target.target_id,
        applied_loci=[locus_id],
        changes=[change],
        change_set_refs=[
            ArtifactRef(
                ref=candidate_ref,
                media_type="application/json",
                metadata={"lane_id": lane_id, "task_id": task_id},
            )
        ],
        provenance={
            "stage": "stage3",
            "lane_id": lane_id,
            "task_id": task_id,
            "mutation_operator": mutation_operator,
            "baseline_candidate_id": parent_candidate_id,
            "candidate_ref": candidate_ref,
            "evaluation_ref": evaluation_ref,
        },
        metadata={
            "canary": True,
            "trial_label": mutation_cfg["trial_label"],
        },
    )
    bounds = MutationBounds(
        max_changed_loci=1,
        max_changed_artifacts=1,
        max_total_value_bytes=512,
        metadata={"source": "darwin_stage3_tranche1_canary"},
    )
    materialized = materialize_candidate(
        target,
        candidate,
        effective_artifact={
            "candidate_ref": candidate_ref,
            "evaluation_ref": evaluation_ref,
            "mutation_operator": mutation_operator,
            "topology_id": mutation_cfg["topology_id"],
            "policy_bundle_id": mutation_cfg["policy_bundle_id"],
            "budget_class": mutation_cfg["budget_class"],
            "task_id": task_id,
        },
        effective_tool_surface={"allowed_tools": list(spec.get("allowed_tools") or [])},
        evaluation_input_compatibility={
            "lane_id": lane_id,
            "task_id": task_id,
            "budget_class": mutation_cfg["budget_class"],
        },
        metadata={"canary": True, "trial_label": mutation_cfg["trial_label"]},
    )
    blast_radius = validate_bounded_candidate(target, candidate, bounds, materialized=materialized)
    return {
        "target": target.to_dict(),
        "candidate_bundle": candidate.to_dict(),
        "materialized_candidate": materialized.to_dict(),
        "mutation_bounds": bounds.to_dict(),
        "blast_radius": blast_radius,
        "selected_locus_id": locus_id,
    }
