from __future__ import annotations

from typing import Any, Dict

from ..longrun.checkpoint import build_longrun_checkpoint_metadata_record
from ..search import build_branch_execute_verify_reference_recipe
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
