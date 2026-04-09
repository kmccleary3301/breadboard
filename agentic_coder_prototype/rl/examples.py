from __future__ import annotations

from typing import Any, Dict

from ..longrun.checkpoint import build_longrun_checkpoint_metadata_record
from ..search import (
    build_branch_execute_verify_reference_recipe,
    build_dag_replication_v1_codetree_packet,
    build_dag_replication_v1_got_sorting_packet,
    build_dag_replication_v1_moa_layered_packet,
    build_dag_replication_v1_tot_game24_packet,
)
from .conformance import (
    build_adapter_probe_report,
    build_export_conformance_packet,
    build_export_conformance_parity_view,
)
from .fidelity import (
    build_compaction_fidelity_report,
    build_delayed_evaluation_fidelity_report,
    build_evaluation_pack_manifest,
    build_export_manifest,
    build_export_manifest_parity_view,
)
from .graph import (
    build_credit_frame_from_trajectory_graph,
    build_trajectory_graph_core_parity_view,
    build_compaction_manifests_from_search_run,
    build_cost_ledger_from_search_run,
    build_default_rollout_descriptor_from_search_run,
    build_evaluation_annotations_from_search_run,
    project_live_search_run_to_trajectory_graph,
    project_replay_payload_to_trajectory_graph,
    project_search_run_to_trajectory_graph,
)
from .export import (
    build_dataset_export_unit_core_view,
    export_reference_unit_bundle,
    export_rl_transition_segment_unit,
    export_sft_distillation_unit,
    export_verifier_example_unit,
)
from .schema import (
    AdapterCapabilities,
    AdapterProbeReport,
    CreditFrame,
    DatasetExportUnit,
    EnvironmentDescriptor,
    EvaluationAnnotation,
    EvaluationPackManifest,
    ExportManifest,
    PolicyProvenance,
    RolloutDescriptor,
    TrainingFeedback,
    TrajectoryGraph,
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


def build_rl_v1_live_projection_example() -> Dict[str, object]:
    base = build_rl_v1_contract_pack_example()
    graph = project_live_search_run_to_trajectory_graph(
        run=base["run"],
        environment_descriptor=base["environment_descriptor"],
        policy_provenance=[base["policy_provenance"]],
        metadata={"phase": "rl_v1_phase2", "projection_case": "live"},
    )
    return {
        **base,
        "trajectory_graph": graph,
    }


def build_rl_v1_live_projection_example_payload() -> Dict[str, object]:
    example = build_rl_v1_live_projection_example()
    return {
        "run": example["run"].to_dict(),
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "environment_descriptor": example["environment_descriptor"].to_dict(),
        "policy_provenance": example["policy_provenance"].to_dict(),
    }


def build_rl_v1_replay_projection_example() -> Dict[str, object]:
    base = build_rl_v1_contract_pack_example()
    graph = project_replay_payload_to_trajectory_graph(
        run_payload=base["run"].to_dict(),
        environment_descriptor=base["environment_descriptor"],
        policy_provenance=[base["policy_provenance"]],
        metadata={"phase": "rl_v1_phase2", "projection_case": "replay"},
    )
    return {
        **base,
        "trajectory_graph": graph,
        "run_payload": base["run"].to_dict(),
    }


def build_rl_v1_replay_projection_example_payload() -> Dict[str, object]:
    example = build_rl_v1_replay_projection_example()
    return {
        "run_payload": dict(example["run_payload"]),
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "environment_descriptor": example["environment_descriptor"].to_dict(),
        "policy_provenance": example["policy_provenance"].to_dict(),
    }


def build_rl_v1_replay_parity_example() -> Dict[str, object]:
    contract_pack = build_rl_v1_contract_pack_example()
    live_graph = project_live_search_run_to_trajectory_graph(
        run=contract_pack["run"],
        environment_descriptor=contract_pack["environment_descriptor"],
        policy_provenance=[contract_pack["policy_provenance"]],
        metadata={"phase": "rl_v1_phase2", "projection_case": "live_parity"},
    )
    replay_graph = project_replay_payload_to_trajectory_graph(
        run_payload=contract_pack["run"].to_dict(),
        environment_descriptor=contract_pack["environment_descriptor"],
        policy_provenance=[contract_pack["policy_provenance"]],
        metadata={"phase": "rl_v1_phase2", "projection_case": "replay_parity"},
    )
    return {
        **contract_pack,
        "live_graph": live_graph,
        "replay_graph": replay_graph,
        "live_parity_view": build_trajectory_graph_core_parity_view(live_graph),
        "replay_parity_view": build_trajectory_graph_core_parity_view(replay_graph),
    }


def build_rl_v1_replay_parity_example_payload() -> Dict[str, object]:
    example = build_rl_v1_replay_parity_example()
    return {
        "live_graph": example["live_graph"].to_dict(),
        "replay_graph": example["replay_graph"].to_dict(),
        "live_parity_view": dict(example["live_parity_view"]),
        "replay_parity_view": dict(example["replay_parity_view"]),
    }


def build_rl_v1_alpha_exporters_example() -> Dict[str, object]:
    base = build_rl_v1_replay_parity_example()
    live_graph = base["live_graph"]
    replay_graph = base["replay_graph"]
    live_exports = {
        "sft": export_sft_distillation_unit(live_graph),
        "transition": export_rl_transition_segment_unit(live_graph),
        "verifier": export_verifier_example_unit(live_graph),
    }
    replay_exports = {
        "sft": export_sft_distillation_unit(replay_graph),
        "transition": export_rl_transition_segment_unit(replay_graph),
        "verifier": export_verifier_example_unit(replay_graph),
    }
    return {
        **base,
        "live_exports": live_exports,
        "replay_exports": replay_exports,
        "live_export_core_views": {key: build_dataset_export_unit_core_view(value) for key, value in live_exports.items()},
        "replay_export_core_views": {
            key: build_dataset_export_unit_core_view(value) for key, value in replay_exports.items()
        },
    }


def build_rl_v1_alpha_exporters_example_payload() -> Dict[str, object]:
    example = build_rl_v1_alpha_exporters_example()
    return {
        "live_exports": {key: value.to_dict() for key, value in example["live_exports"].items()},
        "replay_exports": {key: value.to_dict() for key, value in example["replay_exports"].items()},
        "live_export_core_views": dict(example["live_export_core_views"]),
        "replay_export_core_views": dict(example["replay_export_core_views"]),
    }


def build_rl_v1_multi_agent_async_hardening_example() -> Dict[str, object]:
    base = build_rl_v1_contract_pack_example()
    run = base["run"]
    root_track_id = f"{run.search_id}.rl.track.root"
    delayed_annotations = []
    for index, annotation in enumerate(base["evaluation_annotations"]):
        delayed_annotations.append(
            EvaluationAnnotation(
                annotation_id=annotation.annotation_id,
                subject_id=annotation.subject_id,
                subject_kind=annotation.subject_kind,
                channel=annotation.channel,
                status=annotation.status,
                score_value=annotation.score_value,
                text_feedback=annotation.text_feedback,
                artifact_refs=list(annotation.artifact_refs),
                delayed=index == 0,
                metadata={
                    **dict(annotation.metadata),
                    "delayed": index == 0,
                    "wake_track_id": root_track_id if index == 0 else None,
                    "background_task": "verifier_pool" if index == 0 else "inline",
                },
            )
        )
    checkpoint_pointer = build_longrun_checkpoint_metadata_record(
        path=f"meta/checkpoints/{run.search_id}_resume.json",
        episode=7,
        phase="verification_resume",
        updated_at=1700000000.0,
    )
    graph = project_search_run_to_trajectory_graph(
        run=run,
        rollout_descriptor=build_default_rollout_descriptor_from_search_run(
            run=run,
            origin_kind="live",
            metadata={
                "phase": "rl_v1_phase4",
                "projection_case": "multi_agent_async_hardening",
                "checkpoint_pointer": checkpoint_pointer,
            },
        ),
        environment_descriptor=base["environment_descriptor"],
        policy_provenance=[base["policy_provenance"]],
        evaluation_annotations=delayed_annotations,
        cost_ledger=base["cost_ledger"],
        compaction_manifests=base["compaction_manifests"],
        metadata={
            "phase": "rl_v1_phase4",
            "projection_case": "multi_agent_async_hardening",
            "checkpoint_pointer": checkpoint_pointer,
        },
    )
    credit_frame = build_credit_frame_from_trajectory_graph(
        graph,
        metadata={"phase": "rl_v1_phase4", "continuation_aligned": True},
    )
    return {
        **base,
        "trajectory_graph": graph,
        "credit_frame": credit_frame,
        "checkpoint_pointer": checkpoint_pointer,
    }


def build_rl_v1_multi_agent_async_hardening_example_payload() -> Dict[str, object]:
    example = build_rl_v1_multi_agent_async_hardening_example()
    return {
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "credit_frame": example["credit_frame"].to_dict(),
        "checkpoint_pointer": dict(example["checkpoint_pointer"]),
    }


def build_rl_v1_serving_inference_probe() -> Dict[str, object]:
    example = build_rl_v1_alpha_exporters_example()
    sft_export = example["live_exports"]["sft"]
    policy = example["policy_provenance"]
    adapter = example["adapter_capabilities"]
    return {
        "probe_id": "bb.rl.v1.serving_inference_probe.v1",
        "policy_provenance": policy,
        "adapter_capabilities": adapter,
        "export_unit": sft_export,
        "boundary": {
            "serving_owned_outside_breadboard": True,
            "trainer_specific_state_added": False,
            "provenance_fields": ["provider", "model_name", "prompt_ref", "config_ref"],
        },
    }


def build_rl_v1_serving_inference_probe_payload() -> Dict[str, object]:
    example = build_rl_v1_serving_inference_probe()
    return {
        "probe_id": example["probe_id"],
        "policy_provenance": example["policy_provenance"].to_dict(),
        "adapter_capabilities": example["adapter_capabilities"].to_dict(),
        "export_unit": example["export_unit"].to_dict(),
        "boundary": dict(example["boundary"]),
    }


def build_rl_v1_evaluator_verifier_probe() -> Dict[str, object]:
    example = build_rl_v1_alpha_exporters_example()
    verifier_export = example["live_exports"]["verifier"]
    return {
        "probe_id": "bb.rl.v1.evaluator_verifier_probe.v1",
        "export_unit": verifier_export,
        "annotation_channels": [item.channel for item in verifier_export.evaluation_annotations],
        "boundary": {
            "evaluator_pool_owned_outside_breadboard": True,
            "annotation_truth_stays_inside_breadboard": True,
            "verifier_examples_are_export_only": True,
        },
    }


def build_rl_v1_evaluator_verifier_probe_payload() -> Dict[str, object]:
    example = build_rl_v1_evaluator_verifier_probe()
    return {
        "probe_id": example["probe_id"],
        "export_unit": example["export_unit"].to_dict(),
        "annotation_channels": list(example["annotation_channels"]),
        "boundary": dict(example["boundary"]),
    }


def build_rl_v1_dataset_training_feedback_probe() -> Dict[str, object]:
    example = build_rl_v1_alpha_exporters_example()
    transition_export = example["live_exports"]["transition"]
    feedback = TrainingFeedback(
        feedback_id="bb.rl.v1.training_feedback_probe.v1",
        source_adapter_id="reference_training_feedback_adapter",
        target_export_unit_id=transition_export.export_unit_id,
        feedback_kind="trainer_batch_validation",
        status="accepted_for_batching",
        metric_payload={
            "transition_count": len(transition_export.record_payload["transitions"]),
            "replay_match": True,
            "graph_kind": transition_export.export_kind,
        },
        text_feedback="Batch accepted without requiring trainer-specific state inside BreadBoard.",
        metadata={"phase": "rl_v1_phase5", "delegated_owner": "training_adapter"},
    )
    return {
        "probe_id": "bb.rl.v1.dataset_training_feedback_probe.v1",
        "export_unit": transition_export,
        "training_feedback": feedback,
        "boundary": {
            "dataset_engine_owned_outside_breadboard": True,
            "training_feedback_supported": True,
            "trainer_specific_optimizer_state_added": False,
        },
    }


def build_rl_v1_dataset_training_feedback_probe_payload() -> Dict[str, object]:
    example = build_rl_v1_dataset_training_feedback_probe()
    return {
        "probe_id": example["probe_id"],
        "export_unit": example["export_unit"].to_dict(),
        "training_feedback": example["training_feedback"].to_dict(),
        "boundary": dict(example["boundary"]),
    }


def build_rl_v1_freeze_and_deferrals() -> Dict[str, object]:
    serving = build_rl_v1_serving_inference_probe()
    evaluator = build_rl_v1_evaluator_verifier_probe()
    feedback = build_rl_v1_dataset_training_feedback_probe()
    return {
        "freeze_decision": {
            "current_decision": "freeze_rl_v1",
            "open_rl_v2_now": False,
            "trainer_specific_runtime_mode_added": False,
        },
        "completed_probes": [
            serving["probe_id"],
            evaluator["probe_id"],
            feedback["probe_id"],
        ],
        "deferred_to_v2": [
            "trainer-specific packing and optimizer state",
            "counterfactual relabeling pack",
            "deep online learner control surfaces",
            "canonical RL-owned search overlay",
        ],
        "boundary_summary": {
            "serving_outside": serving["boundary"]["serving_owned_outside_breadboard"],
            "evaluator_outside": evaluator["boundary"]["evaluator_pool_owned_outside_breadboard"],
            "dataset_engine_outside": feedback["boundary"]["dataset_engine_owned_outside_breadboard"],
        },
    }


def build_rl_v1_freeze_and_deferrals_payload() -> Dict[str, object]:
    packet = build_rl_v1_freeze_and_deferrals()
    return {
        "freeze_decision": dict(packet["freeze_decision"]),
        "completed_probes": list(packet["completed_probes"]),
        "deferred_to_v2": list(packet["deferred_to_v2"]),
        "boundary_summary": dict(packet["boundary_summary"]),
    }


def build_rl_v2_freeze_and_scope_packet() -> Dict[str, object]:
    return {
        "packet_id": "bb.rl.v2.freeze_and_scope.v1",
        "v1_surfaces_frozen": [
            "overlay_not_kernel",
            "four_truth_surfaces",
            "graph_native_trajectory_model",
            "evaluation_cost_credit_separation",
            "flatten_only_at_edge",
            "search_deferred_and_non_duplicate",
            "delegated_serving_trainer_evaluator_boundaries",
        ],
        "v2_center": "export_data_fidelity",
        "support_ladder": [
            "probe",
            "experimental",
            "supported",
        ],
        "non_goals": [
            "no_new_kernel_nouns_by_default",
            "no_trainer_specific_packing_in_breadboard_truth",
            "no_dataset_engine_product_surface",
            "no_rl_owned_duplicate_search_ontology",
        ],
        "boundary": {
            "kernel_truth_frozen": True,
            "rl_overlay_mostly_frozen": True,
            "helper_level_additions_allowed": True,
            "adapter_local_losses_must_be_explicit": True,
        },
    }


def build_rl_v2_freeze_and_scope_packet_payload() -> Dict[str, object]:
    packet = build_rl_v2_freeze_and_scope_packet()
    return {
        "packet_id": packet["packet_id"],
        "v1_surfaces_frozen": list(packet["v1_surfaces_frozen"]),
        "v2_center": packet["v2_center"],
        "support_ladder": list(packet["support_ladder"]),
        "non_goals": list(packet["non_goals"]),
        "boundary": dict(packet["boundary"]),
    }


def build_rl_v2_replay_live_fidelity_example() -> Dict[str, object]:
    base = build_rl_v1_alpha_exporters_example()
    live_units = list(base["live_exports"].values())
    replay_units = list(base["replay_exports"].values())
    live_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.v2.evaluation_pack.reference.v1",
        annotations=base["live_graph"].evaluation_annotations,
        rubric_version="rl_v2_replay_live_v1",
        visibility_boundary="policy_view",
        reduction_context="export_fidelity_audit",
        metadata={"phase": "rl_v2_phase1", "projection_case": "live"},
    )
    replay_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.v2.evaluation_pack.reference.v1",
        annotations=base["replay_graph"].evaluation_annotations,
        rubric_version="rl_v2_replay_live_v1",
        visibility_boundary="policy_view",
        reduction_context="export_fidelity_audit",
        metadata={"phase": "rl_v2_phase1", "projection_case": "replay"},
    )
    live_manifest = build_export_manifest(
        export_manifest_id="bb.rl.v2.export_manifest.live_reference.v1",
        export_units=live_units,
        evaluation_pack_manifest=live_pack,
        split_kind="audit_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="rl_v2_phase1",
        contamination_controls=["task_root_split", "artifact_lineage_guard"],
        metadata={"phase": "rl_v2_phase1", "projection_case": "live"},
    )
    replay_manifest = build_export_manifest(
        export_manifest_id="bb.rl.v2.export_manifest.replay_reference.v1",
        export_units=replay_units,
        evaluation_pack_manifest=replay_pack,
        split_kind="audit_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="rl_v2_phase1",
        contamination_controls=["task_root_split", "artifact_lineage_guard"],
        metadata={"phase": "rl_v2_phase1", "projection_case": "replay"},
    )
    return {
        **base,
        "live_evaluation_pack": live_pack,
        "replay_evaluation_pack": replay_pack,
        "live_export_manifest": live_manifest,
        "replay_export_manifest": replay_manifest,
        "live_export_manifest_parity_view": build_export_manifest_parity_view(live_manifest),
        "replay_export_manifest_parity_view": build_export_manifest_parity_view(replay_manifest),
    }


def build_rl_v2_replay_live_fidelity_example_payload() -> Dict[str, object]:
    example = build_rl_v2_replay_live_fidelity_example()
    return {
        "live_evaluation_pack": example["live_evaluation_pack"].to_dict(),
        "replay_evaluation_pack": example["replay_evaluation_pack"].to_dict(),
        "live_export_manifest": example["live_export_manifest"].to_dict(),
        "replay_export_manifest": example["replay_export_manifest"].to_dict(),
        "live_export_manifest_parity_view": dict(example["live_export_manifest_parity_view"]),
        "replay_export_manifest_parity_view": dict(example["replay_export_manifest_parity_view"]),
    }


def build_rl_v2_compaction_fidelity_example() -> Dict[str, object]:
    base = build_rl_v1_alpha_exporters_example()
    graph = base["live_graph"]
    transition_export = base["live_exports"]["transition"]
    evaluation_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.v2.evaluation_pack.compaction.v1",
        annotations=graph.evaluation_annotations,
        rubric_version="rl_v2_compaction_v1",
        visibility_boundary="policy_view",
        reduction_context="compaction_audit",
        metadata={"phase": "rl_v2_phase1", "projection_case": "compaction"},
    )
    export_manifest = build_export_manifest(
        export_manifest_id="bb.rl.v2.export_manifest.compaction.v1",
        export_units=[transition_export],
        evaluation_pack_manifest=evaluation_pack,
        split_kind="audit_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="rl_v2_compaction_v1",
        contamination_controls=["task_root_split", "artifact_lineage_guard"],
        metadata={"phase": "rl_v2_phase1", "projection_case": "compaction"},
    )
    return {
        "trajectory_graph": graph,
        "evaluation_pack": evaluation_pack,
        "export_unit": transition_export,
        "export_manifest": export_manifest,
        "compaction_fidelity_report": build_compaction_fidelity_report(
            graph=graph,
            export_unit=transition_export,
            evaluation_pack_manifest=evaluation_pack,
            export_manifest=export_manifest,
        ),
    }


def build_rl_v2_compaction_fidelity_example_payload() -> Dict[str, object]:
    example = build_rl_v2_compaction_fidelity_example()
    return {
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "evaluation_pack": example["evaluation_pack"].to_dict(),
        "export_unit": example["export_unit"].to_dict(),
        "export_manifest": example["export_manifest"].to_dict(),
        "compaction_fidelity_report": dict(example["compaction_fidelity_report"]),
    }


def build_rl_v2_delayed_evaluation_fidelity_example() -> Dict[str, object]:
    base = build_rl_v1_multi_agent_async_hardening_example()
    graph = base["trajectory_graph"]
    adjusted_annotations = []
    for index, annotation in enumerate(graph.evaluation_annotations):
        adjusted_annotations.append(
            EvaluationAnnotation(
                annotation_id=annotation.annotation_id,
                subject_id=annotation.subject_id,
                subject_kind=annotation.subject_kind,
                channel=annotation.channel,
                status=annotation.status,
                score_value=annotation.score_value,
                text_feedback=annotation.text_feedback,
                artifact_refs=list(annotation.artifact_refs),
                delayed=annotation.delayed,
                metadata={
                    **dict(annotation.metadata),
                    "available_at": f"turn_{index + 2}",
                    "visibility_boundary": "policy_view",
                },
            )
        )
    adjusted_graph = TrajectoryGraph(
        graph_id=graph.graph_id,
        rollout_descriptor=graph.rollout_descriptor,
        environment_descriptor=graph.environment_descriptor,
        policy_provenance=list(graph.policy_provenance),
        tracks=list(graph.tracks),
        observations=list(graph.observations),
        decisions=list(graph.decisions),
        effects=list(graph.effects),
        causal_edges=list(graph.causal_edges),
        evaluation_annotations=adjusted_annotations,
        cost_ledger=graph.cost_ledger,
        compaction_manifests=list(graph.compaction_manifests),
        metadata={**dict(graph.metadata), "phase": "rl_v2_phase1", "projection_case": "delayed_eval"},
    )
    verifier_export = export_verifier_example_unit(adjusted_graph)
    evaluation_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.v2.evaluation_pack.delayed_eval.v1",
        annotations=adjusted_graph.evaluation_annotations,
        rubric_version="rl_v2_delayed_eval_v1",
        visibility_boundary="policy_view",
        reduction_context="delayed_eval_audit",
        metadata={"phase": "rl_v2_phase1", "projection_case": "delayed_eval"},
    )
    export_manifest = build_export_manifest(
        export_manifest_id="bb.rl.v2.export_manifest.delayed_eval.v1",
        export_units=[verifier_export],
        evaluation_pack_manifest=evaluation_pack,
        split_kind="audit_holdout",
        canonicalization_policy="event_address_stable",
        transform_version="rl_v2_delayed_eval_v1",
        contamination_controls=["task_root_split", "future_leak_guard"],
        metadata={"phase": "rl_v2_phase1", "projection_case": "delayed_eval"},
    )
    return {
        "trajectory_graph": adjusted_graph,
        "evaluation_pack": evaluation_pack,
        "export_unit": verifier_export,
        "export_manifest": export_manifest,
        "delayed_evaluation_fidelity_report": build_delayed_evaluation_fidelity_report(
            graph=adjusted_graph,
            evaluation_pack_manifest=evaluation_pack,
            export_manifest=export_manifest,
        ),
    }


def build_rl_v2_delayed_evaluation_fidelity_example_payload() -> Dict[str, object]:
    example = build_rl_v2_delayed_evaluation_fidelity_example()
    return {
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "evaluation_pack": example["evaluation_pack"].to_dict(),
        "export_unit": example["export_unit"].to_dict(),
        "export_manifest": example["export_manifest"].to_dict(),
        "delayed_evaluation_fidelity_report": dict(example["delayed_evaluation_fidelity_report"]),
    }


def build_rl_v2_export_conformance_example() -> Dict[str, object]:
    base = build_rl_v1_replay_parity_example()
    live_bundle = export_reference_unit_bundle(base["live_graph"])
    replay_bundle = export_reference_unit_bundle(base["replay_graph"])
    live_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.v2.evaluation_pack.conformance.v1",
        annotations=base["live_graph"].evaluation_annotations,
        rubric_version="rl_v2_conformance_v1",
        visibility_boundary="policy_view",
        reduction_context="export_conformance",
        metadata={"phase": "rl_v2_phase2", "projection_case": "live"},
    )
    replay_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.v2.evaluation_pack.conformance.v1",
        annotations=base["replay_graph"].evaluation_annotations,
        rubric_version="rl_v2_conformance_v1",
        visibility_boundary="policy_view",
        reduction_context="export_conformance",
        metadata={"phase": "rl_v2_phase2", "projection_case": "replay"},
    )
    controls = [
        "task_root_split",
        "artifact_lineage_guard",
        "teacher_student_origin_guard",
    ]
    live_manifest = build_export_manifest(
        export_manifest_id="bb.rl.v2.export_manifest.conformance.live.v1",
        export_units=list(live_bundle.values()),
        evaluation_pack_manifest=live_pack,
        split_kind="train_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="rl_v2_phase2",
        contamination_controls=controls,
        fidelity_tier="replay_parity_verified",
        metadata={"phase": "rl_v2_phase2", "projection_case": "live"},
    )
    replay_manifest = build_export_manifest(
        export_manifest_id="bb.rl.v2.export_manifest.conformance.replay.v1",
        export_units=list(replay_bundle.values()),
        evaluation_pack_manifest=replay_pack,
        split_kind="train_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="rl_v2_phase2",
        contamination_controls=controls,
        fidelity_tier="replay_parity_verified",
        metadata={"phase": "rl_v2_phase2", "projection_case": "replay"},
    )
    live_packet = build_export_conformance_packet(
        packet_id="bb.rl.v2.export_conformance.live.v1",
        export_units=list(live_bundle.values()),
        evaluation_pack_manifest=live_pack,
        export_manifest=live_manifest,
        metadata={"phase": "rl_v2_phase2", "projection_case": "live"},
    )
    replay_packet = build_export_conformance_packet(
        packet_id="bb.rl.v2.export_conformance.replay.v1",
        export_units=list(replay_bundle.values()),
        evaluation_pack_manifest=replay_pack,
        export_manifest=replay_manifest,
        metadata={"phase": "rl_v2_phase2", "projection_case": "replay"},
    )
    return {
        **base,
        "live_export_bundle": live_bundle,
        "replay_export_bundle": replay_bundle,
        "live_evaluation_pack": live_pack,
        "replay_evaluation_pack": replay_pack,
        "live_export_manifest": live_manifest,
        "replay_export_manifest": replay_manifest,
        "live_conformance_packet": live_packet,
        "replay_conformance_packet": replay_packet,
        "live_conformance_parity_view": build_export_conformance_parity_view(live_packet),
        "replay_conformance_parity_view": build_export_conformance_parity_view(replay_packet),
    }


def build_rl_v2_export_conformance_example_payload() -> Dict[str, object]:
    example = build_rl_v2_export_conformance_example()
    return {
        "live_export_bundle": {key: value.to_dict() for key, value in example["live_export_bundle"].items()},
        "replay_export_bundle": {key: value.to_dict() for key, value in example["replay_export_bundle"].items()},
        "live_evaluation_pack": example["live_evaluation_pack"].to_dict(),
        "replay_evaluation_pack": example["replay_evaluation_pack"].to_dict(),
        "live_export_manifest": example["live_export_manifest"].to_dict(),
        "replay_export_manifest": example["replay_export_manifest"].to_dict(),
        "live_conformance_packet": dict(example["live_conformance_packet"]),
        "replay_conformance_packet": dict(example["replay_conformance_packet"]),
        "live_conformance_parity_view": dict(example["live_conformance_parity_view"]),
        "replay_conformance_parity_view": dict(example["replay_conformance_parity_view"]),
    }


def build_rl_v2_adapter_probe_program_example() -> Dict[str, object]:
    conformance = build_rl_v2_export_conformance_example()
    serving_probe = build_rl_v1_serving_inference_probe()
    evaluator_probe = build_rl_v1_evaluator_verifier_probe()
    dataset_probe = build_rl_v1_dataset_training_feedback_probe()
    evaluator_capabilities = AdapterCapabilities(
        adapter_id="reference_evaluator_probe_adapter",
        adapter_kind="evaluator_probe_adapter",
        supports_graph_trajectory=True,
        supports_async=True,
        supports_training_feedback=False,
        export_formats=["verifier_example_json", "evaluation_annotation_json"],
        metadata={"owner_boundary": "delegated"},
    )
    dataset_capabilities = AdapterCapabilities(
        adapter_id="reference_dataset_pipeline_adapter",
        adapter_kind="dataset_pipeline_adapter",
        supports_graph_trajectory=True,
        supports_async=True,
        supports_training_feedback=False,
        export_formats=["dataset_export_unit_json", "export_manifest_json"],
        metadata={"owner_boundary": "delegated"},
    )

    serving_report = build_adapter_probe_report(
        probe_report_id="bb.rl.v2.adapter_probe.serving.v1",
        adapter_capabilities=serving_probe["adapter_capabilities"],
        probe_kind="serving_inference",
        workload_family="single_agent_code",
        export_manifest_id=conformance["live_export_manifest"].export_manifest_id,
        evaluation_pack_id=conformance["live_evaluation_pack"].evaluation_pack_id,
        fidelity_losses=["provider_cache_stats_not_modeled"],
        unsupported_fields=["serving_batch_id", "backend_cache_hit_rate"],
        metadata={"phase": "rl_v2_phase3", "support_claim": "probe_only"},
    )
    evaluator_report = build_adapter_probe_report(
        probe_report_id="bb.rl.v2.adapter_probe.evaluator.v1",
        adapter_capabilities=evaluator_capabilities,
        probe_kind="evaluator_verifier",
        workload_family="async_verifier",
        export_manifest_id=conformance["live_export_manifest"].export_manifest_id,
        evaluation_pack_id=conformance["live_evaluation_pack"].evaluation_pack_id,
        fidelity_losses=["judge_internal_prompt_not_exposed"],
        unsupported_fields=["judge_prompt_bundle_ref"],
        metadata={"phase": "rl_v2_phase3", "support_claim": "probe_only"},
    )
    dataset_report = build_adapter_probe_report(
        probe_report_id="bb.rl.v2.adapter_probe.dataset_pipeline.v1",
        adapter_capabilities=dataset_capabilities,
        probe_kind="dataset_pipeline",
        workload_family="trainer_neutral_export",
        export_manifest_id=conformance["live_export_manifest"].export_manifest_id,
        evaluation_pack_id=conformance["live_evaluation_pack"].evaluation_pack_id,
        fidelity_losses=["external_parquet_layout_delegated"],
        unsupported_fields=["parquet_row_group_config"],
        metadata={"phase": "rl_v2_phase3", "support_claim": "probe_only"},
    )
    trainer_feedback_report = build_adapter_probe_report(
        probe_report_id="bb.rl.v2.adapter_probe.trainer_feedback.v1",
        adapter_capabilities=AdapterCapabilities(
            adapter_id="reference_training_feedback_adapter",
            adapter_kind="trainer_feedback_adapter",
            supports_graph_trajectory=True,
            supports_async=False,
            supports_training_feedback=True,
            export_formats=["dataset_export_unit_json", "training_feedback_json"],
            metadata={"owner_boundary": "delegated"},
        ),
        probe_kind="trainer_feedback",
        workload_family="trainer_feedback_loop",
        export_manifest_id=conformance["live_export_manifest"].export_manifest_id,
        evaluation_pack_id=conformance["live_evaluation_pack"].evaluation_pack_id,
        fidelity_losses=["optimizer_state_omitted_by_design"],
        unsupported_fields=["optimizer_checkpoint_ref", "packed_minibatch_plan"],
        metadata={"phase": "rl_v2_phase3", "support_claim": "probe_only"},
    )
    return {
        "serving_probe": serving_probe,
        "evaluator_probe": evaluator_probe,
        "dataset_probe": dataset_probe,
        "live_export_manifest": conformance["live_export_manifest"],
        "live_evaluation_pack": conformance["live_evaluation_pack"],
        "probe_reports": {
            "serving": serving_report,
            "evaluator": evaluator_report,
            "dataset": dataset_report,
            "trainer_feedback": trainer_feedback_report,
        },
    }


def build_rl_v2_adapter_probe_program_example_payload() -> Dict[str, object]:
    example = build_rl_v2_adapter_probe_program_example()
    return {
        "live_export_manifest": example["live_export_manifest"].to_dict(),
        "live_evaluation_pack": example["live_evaluation_pack"].to_dict(),
        "probe_reports": {key: value.to_dict() for key, value in example["probe_reports"].items()},
    }


def build_rl_v2_pressure_study_packet() -> Dict[str, object]:
    conformance = build_rl_v2_export_conformance_example()
    delayed_eval = build_rl_v2_delayed_evaluation_fidelity_example()
    async_graph = build_rl_v1_multi_agent_async_hardening_example()["trajectory_graph"]
    representative_workloads = [
        {
            "workload_family": "single_agent_code",
            "graph_id": conformance["live_graph"].graph_id,
            "export_manifest_id": conformance["live_export_manifest"].export_manifest_id,
            "qualities": ["graph_native", "compaction_aware", "replay_parity_ready"],
        },
        {
            "workload_family": "multi_agent_async",
            "graph_id": async_graph.graph_id,
            "export_manifest_id": delayed_eval["export_manifest"].export_manifest_id,
            "qualities": ["message_edges", "workspace_edges", "credit_frame_ready"],
        },
        {
            "workload_family": "delayed_evaluation_async",
            "graph_id": delayed_eval["trajectory_graph"].graph_id,
            "export_manifest_id": delayed_eval["export_manifest"].export_manifest_id,
            "qualities": ["delayed_eval", "available_at_explicit", "policy_view_safe"],
        },
    ]
    baselines = {
        "transcript_only": {
            "lost_capabilities": [
                "graph_topology",
                "track_lineage",
                "compaction_manifest_refs",
                "delayed_available_at",
                "credit_frame",
            ],
            "information_loss_count": 5,
        },
        "flattened_global_step": {
            "lost_capabilities": [
                "branch_lineage",
                "message_visibility_edges",
                "workspace_write_edges",
            ],
            "information_loss_count": 3,
        },
        "scalar_reward_only": {
            "lost_capabilities": [
                "text_feedback",
                "evidence_refs",
                "cost_lineage",
                "async_attribution",
                "compaction_provenance",
                "delayed_evaluation_timing",
            ],
            "information_loss_count": 6,
        },
        "graph_native_export": {
            "lost_capabilities": [],
            "information_loss_count": 0,
        },
    }
    comparison = {
        "winner": "graph_native_export",
        "graph_native_materially_better": True,
        "evidence": {
            "replay_parity_holds": (
                conformance["live_conformance_parity_view"] == conformance["replay_conformance_parity_view"]
            ),
            "delayed_eval_safe": delayed_eval["delayed_evaluation_fidelity_report"]["policy_view_safe"],
            "compaction_auditable": build_rl_v2_compaction_fidelity_example()["compaction_fidelity_report"][
                "all_compaction_refs_preserved"
            ],
        },
    }
    experiment_policy = {
        "default_model": "gpt-5.4-mini",
        "default_mode": "mini_default",
        "escalation_rule": "escalate_only_when_capability_gap_is_explicit_and_auditable",
        "baseline_fairness": "budget_matched_and_split_stable",
    }
    return {
        "packet_id": "bb.rl.v2.pressure_study_packet.v1",
        "representative_workloads": representative_workloads,
        "baselines": baselines,
        "comparison": comparison,
        "experiment_policy": experiment_policy,
    }


def build_rl_v2_pressure_study_packet_payload() -> Dict[str, object]:
    packet = build_rl_v2_pressure_study_packet()
    return {
        "packet_id": packet["packet_id"],
        "representative_workloads": [dict(item) for item in packet["representative_workloads"]],
        "baselines": {key: dict(value) for key, value in packet["baselines"].items()},
        "comparison": {
            "winner": packet["comparison"]["winner"],
            "graph_native_materially_better": packet["comparison"]["graph_native_materially_better"],
            "evidence": dict(packet["comparison"]["evidence"]),
        },
        "experiment_policy": dict(packet["experiment_policy"]),
    }


def build_rl_v2_freeze_and_deferrals() -> Dict[str, object]:
    return {
        "freeze_decision": {
            "current_decision": "freeze_rl_v2",
            "open_rl_v3_now": False,
            "new_kernel_nouns_added": False,
        },
        "surfaces_proven_under_pressure": [
            "evaluation_pack_manifest",
            "export_manifest",
            "adapter_probe_report",
        ],
        "surfaces_not_justified": [
            "policy_view_witness",
            "rl_owned_search_contract",
            "world_state_delta_canon",
            "study_manager_ontology",
            "trainer_specific_packing_surface",
        ],
        "public_claims_enabled": [
            "replay_stable_graph_native_rl_exports",
            "compaction_aware_and_delayed_eval_aware_export_fidelity",
            "bounded_probe_level_adapter_evidence",
        ],
        "deferred_after_v2": [
            "adapter expansions only when support pressure repeats",
            "observation_materialization only if compaction audits force it",
            "search_or_state_delta_work only under repeated cross-workload pressure",
        ],
    }


def build_rl_v2_freeze_and_deferrals_payload() -> Dict[str, object]:
    packet = build_rl_v2_freeze_and_deferrals()
    return {
        "freeze_decision": dict(packet["freeze_decision"]),
        "surfaces_proven_under_pressure": list(packet["surfaces_proven_under_pressure"]),
        "surfaces_not_justified": list(packet["surfaces_not_justified"]),
        "public_claims_enabled": list(packet["public_claims_enabled"]),
        "deferred_after_v2": list(packet["deferred_after_v2"]),
    }


def _build_next_frontier_fallback_annotations(run, *, workload_family: str) -> list[EvaluationAnnotation]:
    selected_candidate = None
    if run.selected_candidate_id:
        selected_candidate = next(
            (candidate for candidate in run.candidates if candidate.candidate_id == run.selected_candidate_id),
            None,
        )
    if selected_candidate is None and run.candidates:
        selected_candidate = run.candidates[-1]
    subject_id = selected_candidate.candidate_id if selected_candidate is not None else run.search_id
    artifact_refs = []
    payload_ref = getattr(selected_candidate, "payload_ref", None) if selected_candidate is not None else None
    if payload_ref:
        artifact_refs.append(payload_ref)
    return [
        EvaluationAnnotation(
            annotation_id=f"{run.search_id}.annotation.next_frontier.packet_review.v1",
            subject_id=subject_id,
            subject_kind="candidate" if selected_candidate is not None else "search_run",
            channel="packet_review",
            status="completed",
            score_value=0.84,
            text_feedback="Bounded next-frontier packet audit attached at the packet layer.",
            artifact_refs=artifact_refs,
            metadata={
                "phase": "next_frontier_c",
                "workload_family": workload_family,
                "synthetic_annotation": True,
            },
        )
    ]


def _build_next_frontier_graph_from_search_run(run, *, projection_case: str, workload_family: str) -> TrajectoryGraph:
    environment_descriptor = EnvironmentDescriptor(
        environment_id=f"{run.search_id}.rl.environment.next_frontier.v1",
        environment_kind="dag_packet_workspace",
        workspace_mode="bounded_packet_replay",
        tool_names=["search_runtime", "fidelity_scorecard", "compute_ledger"],
        metadata={"phase": "next_frontier_c", "workload_family": workload_family},
    )
    policy_provenance = PolicyProvenance(
        policy_id=f"{run.search_id}.rl.policy.next_frontier.v1",
        policy_kind="assistant_policy",
        provider="openai",
        model_name="gpt-5.4-mini",
        sequential_track_id=f"{run.search_id}.rl.track.root",
        prompt_ref=f"prompts/{run.recipe_kind}.md",
        config_ref=f"configs/{run.recipe_kind}.yaml",
        metadata={"phase": "next_frontier_c", "workload_family": workload_family},
    )
    if projection_case == "replay":
        graph = project_replay_payload_to_trajectory_graph(
            run_payload=run.to_dict(),
            environment_descriptor=environment_descriptor,
            policy_provenance=[policy_provenance],
            metadata={"phase": "next_frontier_c", "workload_family": workload_family, "projection_case": "replay"},
        )
    else:
        graph = project_live_search_run_to_trajectory_graph(
            run=run,
            environment_descriptor=environment_descriptor,
            policy_provenance=[policy_provenance],
            metadata={"phase": "next_frontier_c", "workload_family": workload_family, "projection_case": "live"},
        )
    evaluation_annotations = build_evaluation_annotations_from_search_run(run)
    if not evaluation_annotations:
        evaluation_annotations = _build_next_frontier_fallback_annotations(
            run,
            workload_family=workload_family,
        )
    return TrajectoryGraph(
        graph_id=graph.graph_id,
        rollout_descriptor=graph.rollout_descriptor,
        environment_descriptor=graph.environment_descriptor,
        policy_provenance=list(graph.policy_provenance),
        tracks=list(graph.tracks),
        observations=list(graph.observations),
        decisions=list(graph.decisions),
        effects=list(graph.effects),
        causal_edges=list(graph.causal_edges),
        evaluation_annotations=evaluation_annotations,
        cost_ledger=build_cost_ledger_from_search_run(run),
        compaction_manifests=build_compaction_manifests_from_search_run(run),
        metadata=dict(graph.metadata),
    )


def build_next_frontier_rl_trainer_facing_export_packet() -> Dict[str, object]:
    """Build the first trainer-facing RL packet over a DAG replication workload."""

    got_packet = build_dag_replication_v1_got_sorting_packet()
    graph = _build_next_frontier_graph_from_search_run(
        got_packet["run"],
        projection_case="live",
        workload_family="dag_got_sorting",
    )
    export_units = export_reference_unit_bundle(graph)
    evaluation_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.trainer.v1",
        annotations=graph.evaluation_annotations,
        rubric_version="next_frontier_trainer_v1",
        visibility_boundary="policy_view",
        reduction_context="trainer_facing_export",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_got_sorting"},
    )
    export_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.trainer.v1",
        export_units=list(export_units.values()),
        evaluation_pack_manifest=evaluation_pack,
        split_kind="train_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="next_frontier_trainer_v1",
        contamination_controls=["task_root_split", "artifact_lineage_guard", "packet_family_guard"],
        fidelity_tier="bounded_trainer_ready",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_got_sorting"},
    )
    return {
        "source_packet_id": got_packet["recipe_manifest"].manifest_id,
        "workload_family": "dag_got_sorting",
        "trajectory_graph": graph,
        "export_units": export_units,
        "evaluation_pack": evaluation_pack,
        "export_manifest": export_manifest,
        "bounded_loss_report": {
            "lost_fields": [],
            "preserved_fields": ["graph_topology", "evaluation_annotations", "compaction_manifests", "cost_ledger"],
            "acceptance_rule": "bounded_loss_only_if_trainer_local_packing_is_delegated",
        },
        "study_note": {
            "packet_kind": "trainer_facing_export",
            "pain_classification": "adapter_local_expectation_gap_only",
        },
    }


def build_next_frontier_rl_trainer_facing_export_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_trainer_facing_export_packet()
    return {
        "source_packet_id": example["source_packet_id"],
        "workload_family": example["workload_family"],
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "export_units": {key: value.to_dict() for key, value in example["export_units"].items()},
        "evaluation_pack": example["evaluation_pack"].to_dict(),
        "export_manifest": example["export_manifest"].to_dict(),
        "bounded_loss_report": dict(example["bounded_loss_report"]),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_rl_evaluator_verifier_packet() -> Dict[str, object]:
    """Build the first evaluator/verifier RL packet over a DAG frontier workload."""

    tot_packet = build_dag_replication_v1_tot_game24_packet()
    graph = _build_next_frontier_graph_from_search_run(
        tot_packet["run"],
        projection_case="live",
        workload_family="dag_tot_game24",
    )
    verifier_unit = export_verifier_example_unit(graph)
    evaluation_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.verifier.v1",
        annotations=graph.evaluation_annotations,
        rubric_version="next_frontier_verifier_v1",
        visibility_boundary="policy_view",
        reduction_context="evaluator_verifier_packet",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_tot_game24", "delayed_eval_policy": "none"},
    )
    export_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.verifier.v1",
        export_units=[verifier_unit],
        evaluation_pack_manifest=evaluation_pack,
        split_kind="audit_holdout",
        canonicalization_policy="event_address_stable",
        transform_version="next_frontier_verifier_v1",
        contamination_controls=["task_root_split", "future_leak_guard"],
        fidelity_tier="bounded_verifier_ready",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_tot_game24"},
    )
    return {
        "source_packet_id": tot_packet["recipe_manifest"].manifest_id,
        "workload_family": "dag_tot_game24",
        "trajectory_graph": graph,
        "export_unit": verifier_unit,
        "evaluation_pack": evaluation_pack,
        "export_manifest": export_manifest,
        "coherence_report": {
            "annotation_count": len(evaluation_pack.annotation_ids),
            "all_annotations_in_pack": set(evaluation_pack.annotation_ids)
            == {item.annotation_id for item in verifier_unit.evaluation_annotations},
            "delayed_eval_assumption": "none_in_first_packet",
        },
        "study_note": {
            "packet_kind": "evaluator_verifier",
            "pain_classification": "evaluator_local_only",
        },
    }


def build_next_frontier_rl_evaluator_verifier_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_evaluator_verifier_packet()
    return {
        "source_packet_id": example["source_packet_id"],
        "workload_family": example["workload_family"],
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "export_unit": example["export_unit"].to_dict(),
        "evaluation_pack": example["evaluation_pack"].to_dict(),
        "export_manifest": example["export_manifest"].to_dict(),
        "coherence_report": dict(example["coherence_report"]),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_rl_replay_live_parity_packet() -> Dict[str, object]:
    """Build the first replay/live parity packet on a new DAG code-tree workload."""

    codetree_packet = build_dag_replication_v1_codetree_packet()
    live_graph = _build_next_frontier_graph_from_search_run(
        codetree_packet["run"],
        projection_case="live",
        workload_family="dag_codetree_patch",
    )
    replay_graph = _build_next_frontier_graph_from_search_run(
        codetree_packet["run"],
        projection_case="replay",
        workload_family="dag_codetree_patch",
    )
    live_bundle = export_reference_unit_bundle(live_graph)
    replay_bundle = export_reference_unit_bundle(replay_graph)
    live_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.parity.v1",
        annotations=live_graph.evaluation_annotations,
        rubric_version="next_frontier_parity_v1",
        visibility_boundary="policy_view",
        reduction_context="new_workload_parity",
        metadata={"phase": "next_frontier_c", "projection_case": "live"},
    )
    replay_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.parity.v1",
        annotations=replay_graph.evaluation_annotations,
        rubric_version="next_frontier_parity_v1",
        visibility_boundary="policy_view",
        reduction_context="new_workload_parity",
        metadata={"phase": "next_frontier_c", "projection_case": "replay"},
    )
    live_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.parity.live.v1",
        export_units=list(live_bundle.values()),
        evaluation_pack_manifest=live_pack,
        split_kind="audit_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="next_frontier_parity_v1",
        contamination_controls=["task_root_split", "artifact_lineage_guard"],
        fidelity_tier="replay_parity_verified",
        metadata={"phase": "next_frontier_c", "projection_case": "live"},
    )
    replay_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.parity.replay.v1",
        export_units=list(replay_bundle.values()),
        evaluation_pack_manifest=replay_pack,
        split_kind="audit_holdout",
        canonicalization_policy="source_ref_exact",
        transform_version="next_frontier_parity_v1",
        contamination_controls=["task_root_split", "artifact_lineage_guard"],
        fidelity_tier="replay_parity_verified",
        metadata={"phase": "next_frontier_c", "projection_case": "replay"},
    )
    return {
        "source_packet_id": codetree_packet["recipe_manifest"].manifest_id,
        "workload_family": "dag_codetree_patch",
        "live_graph": live_graph,
        "replay_graph": replay_graph,
        "live_parity_view": build_trajectory_graph_core_parity_view(live_graph),
        "replay_parity_view": build_trajectory_graph_core_parity_view(replay_graph),
        "live_export_manifest": live_manifest,
        "replay_export_manifest": replay_manifest,
        "live_export_manifest_parity_view": build_export_manifest_parity_view(live_manifest),
        "replay_export_manifest_parity_view": build_export_manifest_parity_view(replay_manifest),
        "study_note": {
            "packet_kind": "replay_live_parity",
            "pain_classification": "no_repeated_rl_shape",
        },
    }


def build_next_frontier_rl_replay_live_parity_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_replay_live_parity_packet()
    return {
        "source_packet_id": example["source_packet_id"],
        "workload_family": example["workload_family"],
        "live_graph": example["live_graph"].to_dict(),
        "replay_graph": example["replay_graph"].to_dict(),
        "live_parity_view": dict(example["live_parity_view"]),
        "replay_parity_view": dict(example["replay_parity_view"]),
        "live_export_manifest": example["live_export_manifest"].to_dict(),
        "replay_export_manifest": example["replay_export_manifest"].to_dict(),
        "live_export_manifest_parity_view": dict(example["live_export_manifest_parity_view"]),
        "replay_export_manifest_parity_view": dict(example["replay_export_manifest_parity_view"]),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_rl_adapter_friction_synthesis() -> Dict[str, object]:
    trainer_packet = build_next_frontier_rl_trainer_facing_export_packet()
    verifier_packet = build_next_frontier_rl_evaluator_verifier_packet()
    parity_packet = build_next_frontier_rl_replay_live_parity_packet()
    return {
        "synthesis_id": "bb.rl.next_frontier.adapter_friction_synthesis.v1",
        "evidence_sources": [
            trainer_packet["export_manifest"].export_manifest_id,
            verifier_packet["export_manifest"].export_manifest_id,
            parity_packet["live_export_manifest"].export_manifest_id,
        ],
        "repeated_shape_gap_detected": False,
        "recommended_outcome": "keep_rl_frozen",
        "pain_summary": [
            trainer_packet["study_note"]["pain_classification"],
            verifier_packet["study_note"]["pain_classification"],
            parity_packet["study_note"]["pain_classification"],
        ],
        "metadata": {"phase": "next_frontier_c"},
    }


def build_next_frontier_rl_adapter_friction_synthesis_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_adapter_friction_synthesis()
    return {
        "synthesis_id": example["synthesis_id"],
        "evidence_sources": list(example["evidence_sources"]),
        "repeated_shape_gap_detected": example["repeated_shape_gap_detected"],
        "recommended_outcome": example["recommended_outcome"],
        "pain_summary": list(example["pain_summary"]),
        "metadata": dict(example["metadata"]),
    }


def build_next_frontier_rl_second_trainer_facing_export_packet() -> Dict[str, object]:
    """Build a second trainer-facing RL packet on a different DAG workload family."""

    moa_packet = build_dag_replication_v1_moa_layered_packet()
    graph = _build_next_frontier_graph_from_search_run(
        moa_packet["run"],
        projection_case="live",
        workload_family="dag_moa_layered",
    )
    export_units = export_reference_unit_bundle(graph)
    evaluation_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.trainer.v2",
        annotations=graph.evaluation_annotations,
        rubric_version="next_frontier_trainer_v2",
        visibility_boundary="policy_view",
        reduction_context="trainer_facing_export_second_loop",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_moa_layered"},
    )
    export_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.trainer.v2",
        export_units=list(export_units.values()),
        evaluation_pack_manifest=evaluation_pack,
        split_kind="train_holdout",
        canonicalization_policy="event_address_stable",
        transform_version="next_frontier_trainer_v2",
        contamination_controls=["task_root_split", "artifact_lineage_guard", "roster_visibility_guard"],
        fidelity_tier="bounded_trainer_ready",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_moa_layered"},
    )
    return {
        "source_packet_id": moa_packet["recipe_manifest"].manifest_id,
        "workload_family": "dag_moa_layered",
        "trajectory_graph": graph,
        "export_units": export_units,
        "evaluation_pack": evaluation_pack,
        "export_manifest": export_manifest,
        "bounded_loss_report": {
            "lost_fields": [],
            "preserved_fields": ["layered_fan_in", "evaluation_annotations", "compaction_manifests", "cost_ledger"],
            "acceptance_rule": "bounded_loss_only_if_consumer_local_roster_packing_is_delegated",
        },
        "study_note": {
            "packet_kind": "trainer_facing_export_second_loop",
            "pain_classification": "adapter_and_compaction_local_only",
        },
    }


def build_next_frontier_rl_second_trainer_facing_export_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_second_trainer_facing_export_packet()
    return {
        "source_packet_id": example["source_packet_id"],
        "workload_family": example["workload_family"],
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "export_units": {key: value.to_dict() for key, value in example["export_units"].items()},
        "evaluation_pack": example["evaluation_pack"].to_dict(),
        "export_manifest": example["export_manifest"].to_dict(),
        "bounded_loss_report": dict(example["bounded_loss_report"]),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_rl_second_evaluator_verifier_packet() -> Dict[str, object]:
    """Build a second evaluator/verifier packet on a different DAG workload."""

    codetree_packet = build_dag_replication_v1_codetree_packet()
    graph = _build_next_frontier_graph_from_search_run(
        codetree_packet["run"],
        projection_case="live",
        workload_family="dag_codetree_patch",
    )
    verifier_unit = export_verifier_example_unit(graph)
    evaluation_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.verifier.v2",
        annotations=graph.evaluation_annotations,
        rubric_version="next_frontier_verifier_v2",
        visibility_boundary="policy_view",
        reduction_context="evaluator_verifier_second_loop",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_codetree_patch", "delayed_eval_policy": "bounded"},
    )
    export_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.verifier.v2",
        export_units=[verifier_unit],
        evaluation_pack_manifest=evaluation_pack,
        split_kind="audit_holdout",
        canonicalization_policy="event_address_stable",
        transform_version="next_frontier_verifier_v2",
        contamination_controls=["task_root_split", "future_leak_guard", "execution_feedback_guard"],
        fidelity_tier="bounded_verifier_ready",
        metadata={"phase": "next_frontier_c", "source_workload": "dag_codetree_patch"},
    )
    return {
        "source_packet_id": codetree_packet["recipe_manifest"].manifest_id,
        "workload_family": "dag_codetree_patch",
        "trajectory_graph": graph,
        "export_unit": verifier_unit,
        "evaluation_pack": evaluation_pack,
        "export_manifest": export_manifest,
        "coherence_report": {
            "annotation_count": len(evaluation_pack.annotation_ids),
            "all_annotations_in_pack": set(evaluation_pack.annotation_ids)
            == {item.annotation_id for item in verifier_unit.evaluation_annotations},
            "delayed_eval_assumption": "bounded_and_explicit",
        },
        "study_note": {
            "packet_kind": "evaluator_verifier_second_loop",
            "pain_classification": "evaluator_and_compaction_local_only",
        },
    }


def build_next_frontier_rl_second_evaluator_verifier_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_second_evaluator_verifier_packet()
    return {
        "source_packet_id": example["source_packet_id"],
        "workload_family": example["workload_family"],
        "trajectory_graph": example["trajectory_graph"].to_dict(),
        "export_unit": example["export_unit"].to_dict(),
        "evaluation_pack": example["evaluation_pack"].to_dict(),
        "export_manifest": example["export_manifest"].to_dict(),
        "coherence_report": dict(example["coherence_report"]),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_rl_second_replay_live_parity_packet() -> Dict[str, object]:
    """Build a second replay/live parity packet on a different workload family."""

    moa_packet = build_dag_replication_v1_moa_layered_packet()
    live_graph = _build_next_frontier_graph_from_search_run(
        moa_packet["run"],
        projection_case="live",
        workload_family="dag_moa_layered",
    )
    replay_graph = _build_next_frontier_graph_from_search_run(
        moa_packet["run"],
        projection_case="replay",
        workload_family="dag_moa_layered",
    )
    live_bundle = export_reference_unit_bundle(live_graph)
    replay_bundle = export_reference_unit_bundle(replay_graph)
    live_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.parity.v2",
        annotations=live_graph.evaluation_annotations,
        rubric_version="next_frontier_parity_v2",
        visibility_boundary="policy_view",
        reduction_context="second_workload_parity",
        metadata={"phase": "next_frontier_c", "projection_case": "live"},
    )
    replay_pack = build_evaluation_pack_manifest(
        evaluation_pack_id="bb.rl.next_frontier.evaluation_pack.parity.v2",
        annotations=replay_graph.evaluation_annotations,
        rubric_version="next_frontier_parity_v2",
        visibility_boundary="policy_view",
        reduction_context="second_workload_parity",
        metadata={"phase": "next_frontier_c", "projection_case": "replay"},
    )
    live_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.parity.live.v2",
        export_units=list(live_bundle.values()),
        evaluation_pack_manifest=live_pack,
        split_kind="audit_holdout",
        canonicalization_policy="event_address_stable",
        transform_version="next_frontier_parity_v2",
        contamination_controls=["task_root_split", "artifact_lineage_guard", "roster_visibility_guard"],
        fidelity_tier="replay_parity_verified",
        metadata={"phase": "next_frontier_c", "projection_case": "live"},
    )
    replay_manifest = build_export_manifest(
        export_manifest_id="bb.rl.next_frontier.export_manifest.parity.replay.v2",
        export_units=list(replay_bundle.values()),
        evaluation_pack_manifest=replay_pack,
        split_kind="audit_holdout",
        canonicalization_policy="event_address_stable",
        transform_version="next_frontier_parity_v2",
        contamination_controls=["task_root_split", "artifact_lineage_guard", "roster_visibility_guard"],
        fidelity_tier="replay_parity_verified",
        metadata={"phase": "next_frontier_c", "projection_case": "replay"},
    )
    return {
        "source_packet_id": moa_packet["recipe_manifest"].manifest_id,
        "workload_family": "dag_moa_layered",
        "live_graph": live_graph,
        "replay_graph": replay_graph,
        "live_parity_view": build_trajectory_graph_core_parity_view(live_graph),
        "replay_parity_view": build_trajectory_graph_core_parity_view(replay_graph),
        "live_export_manifest": live_manifest,
        "replay_export_manifest": replay_manifest,
        "live_export_manifest_parity_view": build_export_manifest_parity_view(live_manifest),
        "replay_export_manifest_parity_view": build_export_manifest_parity_view(replay_manifest),
        "study_note": {
            "packet_kind": "replay_live_parity_second_loop",
            "pain_classification": "no_repeated_rl_shape",
        },
    }


def build_next_frontier_rl_second_replay_live_parity_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_second_replay_live_parity_packet()
    return {
        "source_packet_id": example["source_packet_id"],
        "workload_family": example["workload_family"],
        "live_graph": example["live_graph"].to_dict(),
        "replay_graph": example["replay_graph"].to_dict(),
        "live_parity_view": dict(example["live_parity_view"]),
        "replay_parity_view": dict(example["replay_parity_view"]),
        "live_export_manifest": example["live_export_manifest"].to_dict(),
        "replay_export_manifest": example["replay_export_manifest"].to_dict(),
        "live_export_manifest_parity_view": dict(example["live_export_manifest_parity_view"]),
        "replay_export_manifest_parity_view": dict(example["replay_export_manifest_parity_view"]),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_rl_tranche_synthesis_v2() -> Dict[str, object]:
    first_trainer = build_next_frontier_rl_trainer_facing_export_packet()
    first_verifier = build_next_frontier_rl_evaluator_verifier_packet()
    first_parity = build_next_frontier_rl_replay_live_parity_packet()
    second_trainer = build_next_frontier_rl_second_trainer_facing_export_packet()
    second_verifier = build_next_frontier_rl_second_evaluator_verifier_packet()
    second_parity = build_next_frontier_rl_second_replay_live_parity_packet()
    return {
        "synthesis_id": "bb.rl.next_frontier.tranche_synthesis.v2",
        "evidence_sources": [
            first_trainer["export_manifest"].export_manifest_id,
            first_verifier["export_manifest"].export_manifest_id,
            first_parity["live_export_manifest"].export_manifest_id,
            second_trainer["export_manifest"].export_manifest_id,
            second_verifier["export_manifest"].export_manifest_id,
            second_parity["live_export_manifest"].export_manifest_id,
        ],
        "repeated_shape_gap_detected": False,
        "recommended_outcome": "keep_rl_frozen",
        "next_frontier_ready": "frontier_d_cross_system_composition",
        "pain_summary": [
            first_trainer["study_note"]["pain_classification"],
            first_verifier["study_note"]["pain_classification"],
            second_trainer["study_note"]["pain_classification"],
            second_verifier["study_note"]["pain_classification"],
        ],
        "metadata": {"phase": "next_frontier_c", "loop_count": 2},
    }


def build_next_frontier_rl_tranche_synthesis_v2_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_tranche_synthesis_v2()
    return {
        "synthesis_id": example["synthesis_id"],
        "evidence_sources": list(example["evidence_sources"]),
        "repeated_shape_gap_detected": example["repeated_shape_gap_detected"],
        "recommended_outcome": example["recommended_outcome"],
        "next_frontier_ready": example["next_frontier_ready"],
        "pain_summary": list(example["pain_summary"]),
        "metadata": dict(example["metadata"]),
    }


def build_next_frontier_dag_to_rl_composition_packet() -> Dict[str, object]:
    """Build the first DAG-to-RL composition packet."""

    second_trainer = build_next_frontier_rl_second_trainer_facing_export_packet()
    return {
        "composition_id": "composition.next_frontier.dag_to_rl.v1",
        "source_packet_id": second_trainer["source_packet_id"],
        "consumer_export_manifest_id": second_trainer["export_manifest"].export_manifest_id,
        "handoff_contract": {
            "preserved_fields": [
                "recipe_manifest_identity",
                "evaluation_pack_identity",
                "cost_ledger_identity",
                "compaction_manifest_identity",
            ],
            "provenance_continuity": True,
            "helper_only_handoff": True,
        },
        "composition_report": {
            "composed_cleanly": True,
            "awkwardness_classification": "adapter_local_only",
            "repeated_shape_gap_detected": False,
        },
        "metadata": {"phase": "next_frontier_d", "source_workload": second_trainer["workload_family"]},
    }


def build_next_frontier_dag_to_rl_composition_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_dag_to_rl_composition_packet()
    return {
        "composition_id": example["composition_id"],
        "source_packet_id": example["source_packet_id"],
        "consumer_export_manifest_id": example["consumer_export_manifest_id"],
        "handoff_contract": dict(example["handoff_contract"]),
        "composition_report": dict(example["composition_report"]),
        "metadata": dict(example["metadata"]),
    }


def build_next_frontier_rl_final_closeout_packet() -> Dict[str, object]:
    """Build the final RL closeout packet for the next-frontier program."""

    synthesis = build_next_frontier_rl_tranche_synthesis_v2()
    return {
        "closeout_id": "bb.rl.next_frontier.final_closeout.v1",
        "source_synthesis_id": synthesis["synthesis_id"],
        "reviewed_loops": 2,
        "reviewed_packet_count": 6,
        "final_decision": "keep_rl_frozen",
        "repeated_shape_gap_detected": False,
        "reopen_criteria": [
            "the same missing RL shape repeats across more than one workload family",
            "the same missing RL shape repeats across more than one consumer type",
            "the same missing RL shape appears in more than one packet family",
        ],
        "proven_capabilities": [
            "trainer-facing export packets",
            "evaluator/verifier pack packets",
            "replay/live parity packets",
        ],
        "not_proven": [
            "need for a new RL public ontology",
            "need for trainer-specific kernel truth",
        ],
        "metadata": {"phase": "next_frontier_c", "loop_count": 2},
    }


def build_next_frontier_rl_final_closeout_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_rl_final_closeout_packet()
    return {
        "closeout_id": example["closeout_id"],
        "source_synthesis_id": example["source_synthesis_id"],
        "reviewed_loops": example["reviewed_loops"],
        "reviewed_packet_count": example["reviewed_packet_count"],
        "final_decision": example["final_decision"],
        "repeated_shape_gap_detected": example["repeated_shape_gap_detected"],
        "reopen_criteria": list(example["reopen_criteria"]),
        "proven_capabilities": list(example["proven_capabilities"]),
        "not_proven": list(example["not_proven"]),
        "metadata": dict(example["metadata"]),
    }
