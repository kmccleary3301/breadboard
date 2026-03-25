from __future__ import annotations

from typing import Any, Dict

from ..search import build_branch_execute_verify_reference_recipe
from .graph import (
    build_compaction_manifests_from_search_run,
    build_cost_ledger_from_search_run,
    build_evaluation_annotations_from_search_run,
    project_search_run_to_trajectory_graph,
)
from .schema import (
    AdapterCapabilities,
    EnvironmentDescriptor,
    PolicyProvenance,
    RolloutDescriptor,
)


def build_rl_v1_boundary_audit_packet() -> Dict[str, object]:
    return {
        "packet_id": "bb.rl.v1.boundary_audit.v1",
        "namespace_plan": {
            "overlay_package": "agentic_coder_prototype.rl",
            "kernel_truth_owner": "search.SearchRun and related search records",
            "rl_owns_search_semantics": False,
        },
        "superseded_or_reframed_surfaces": [
            {
                "surface": "agentic_coder_prototype/search/export.py::SearchTrajectoryExport",
                "decision": "reframed_as_search_study_export_only",
                "reason": "It is useful for search studies but is not the native RL truth surface.",
            },
            {
                "surface": "agentic_coder_prototype/optimize/trajectory_ir.py",
                "decision": "superseded_for_rl_truth",
                "reason": "The linear stub episode shape is too shallow to be the canonical RL surface.",
            },
            {
                "surface": "agentic_coder_prototype/rlm",
                "decision": "unchanged_and_outside_rl_truth",
                "reason": "RLM remains a tool/budget subsystem rather than a training-truth surface.",
            },
        ],
        "alignment": {
            "search_alignment": "RL projects from DAG/search truth rather than creating a parallel RL-owned search namespace.",
            "trainer_alignment": "BreadBoard owns semantic truth and export contracts, not trainer internals.",
            "dag_v3_alignment": "RL consumes the now-finished DAG V3 helper and export seams rather than redefining them.",
        },
        "anti_goals": [
            "no transcript_as_rl_truth",
            "no scalar_reward_only_truth",
            "no trainer_specific_optimizer_state_in_kernel_truth",
            "no rl_owned_duplicate_search_ontology",
        ],
    }


def build_rl_v1_boundary_audit_packet_payload() -> Dict[str, object]:
    packet = build_rl_v1_boundary_audit_packet()
    return {
        "packet_id": packet["packet_id"],
        "namespace_plan": dict(packet["namespace_plan"]),
        "superseded_or_reframed_surfaces": [dict(item) for item in packet["superseded_or_reframed_surfaces"]],
        "alignment": dict(packet["alignment"]),
        "anti_goals": list(packet["anti_goals"]),
    }


def build_rl_v1_contract_pack_example() -> Dict[str, object]:
    base = build_branch_execute_verify_reference_recipe()
    run = base["run"]
    rollout_descriptor = RolloutDescriptor(
        rollout_id=f"{run.search_id}.rl.rollout.v1",
        source_kind="search_run",
        source_ref=run.search_id,
        recipe_kind=run.recipe_kind,
        origin_kind="live",
        metadata={"phase": "rl_v1_phase1", "selected_candidate_id": run.selected_candidate_id},
    )
    environment_descriptor = EnvironmentDescriptor(
        environment_id=f"{run.search_id}.rl.environment.v1",
        environment_kind="code_agent_workspace",
        workspace_mode="branch_local_with_snapshots",
        tool_names=["apply_patch", "pytest", "verifier"],
        metadata={"search_id": run.search_id, "has_workspace_snapshots": bool(run.workspace_snapshots)},
    )
    policy_provenance = PolicyProvenance(
        policy_id=f"{run.search_id}.rl.policy.primary.v1",
        policy_kind="assistant_policy",
        provider="openai",
        model_name="gpt-5.4-mini",
        sequential_track_id=f"{run.search_id}.rl.track.root",
        prompt_ref=f"prompts/{run.recipe_kind}.md",
        config_ref=f"configs/{run.recipe_kind}.yaml",
        metadata={"phase": "rl_v1_phase1", "trainer_neutral": True},
    )
    adapter_capabilities = AdapterCapabilities(
        adapter_id="bb.rl.adapter_capabilities.v1.reference",
        adapter_kind="reference_export_adapter",
        supports_graph_trajectory=True,
        supports_async=True,
        supports_training_feedback=False,
        export_formats=["trajectory_graph_json", "dataset_export_unit_json"],
        metadata={"owner_boundary": "delegated"},
    )
    evaluation_annotations = build_evaluation_annotations_from_search_run(run)
    cost_ledger = build_cost_ledger_from_search_run(run)
    compaction_manifests = build_compaction_manifests_from_search_run(run)
    return {
        "run": run,
        "rollout_descriptor": rollout_descriptor,
        "environment_descriptor": environment_descriptor,
        "policy_provenance": policy_provenance,
        "evaluation_annotations": evaluation_annotations,
        "cost_ledger": cost_ledger,
        "compaction_manifests": compaction_manifests,
        "adapter_capabilities": adapter_capabilities,
    }


def build_rl_v1_contract_pack_example_payload() -> Dict[str, object]:
    example = build_rl_v1_contract_pack_example()
    return {
        "run": example["run"].to_dict(),
        "rollout_descriptor": example["rollout_descriptor"].to_dict(),
        "environment_descriptor": example["environment_descriptor"].to_dict(),
        "policy_provenance": example["policy_provenance"].to_dict(),
        "evaluation_annotations": [item.to_dict() for item in example["evaluation_annotations"]],
        "cost_ledger": example["cost_ledger"].to_dict(),
        "compaction_manifests": [item.to_dict() for item in example["compaction_manifests"]],
        "adapter_capabilities": example["adapter_capabilities"].to_dict(),
    }


def build_rl_v1_trajectory_graph_shell_example() -> Dict[str, object]:
    base = build_rl_v1_contract_pack_example()
    graph = project_search_run_to_trajectory_graph(
        run=base["run"],
        rollout_descriptor=base["rollout_descriptor"],
        environment_descriptor=base["environment_descriptor"],
        policy_provenance=[base["policy_provenance"]],
        evaluation_annotations=base["evaluation_annotations"],
        cost_ledger=base["cost_ledger"],
        compaction_manifests=base["compaction_manifests"],
        metadata={"phase": "rl_v1_phase2_1", "graph_shell_only": True},
    )
    return {
        **base,
        "trajectory_graph": graph,
    }


def build_rl_v1_trajectory_graph_shell_example_payload() -> Dict[str, object]:
    example = build_rl_v1_trajectory_graph_shell_example()
    return {
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "rollout_descriptor": example["rollout_descriptor"].to_dict(),
        "environment_descriptor": example["environment_descriptor"].to_dict(),
        "policy_provenance": example["policy_provenance"].to_dict(),
    }
