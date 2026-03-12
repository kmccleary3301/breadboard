from __future__ import annotations

from typing import Dict

from .backend import (
    MutationBounds,
    OptimizationExecutionContext,
    PortfolioEntry,
    ReflectiveParetoBackend,
    ReflectiveParetoBackendRequest,
    ReflectiveParetoBackendResult,
    run_reflective_pareto_backend,
)
from .context import (
    EnvironmentSelector,
    MCPContextRequirement,
    OptimizationRuntimeContext,
    SandboxContextRequirement,
    ServiceContextRequirement,
    ToolPackContext,
    ToolRequirement,
)
from .dataset import CorrectnessRationale, GroundTruthPackage, OptimizationDataset, OptimizationSample
from .diagnostics import DiagnosticBundle, DiagnosticEntry
from .evaluation import EvaluationRecord
from .promotion import (
    PromotionDecision,
    PromotionRecord,
    promote_candidate,
)
from .substrate import (
    ArtifactRef,
    CandidateBundle,
    CandidateChange,
    MaterializedCandidate,
    MutableLocus,
    OptimizationInvariant,
    OptimizationTarget,
    SupportEnvelope,
    materialize_candidate,
)
from .wrongness import WrongnessReport


def _clone_evaluation_with_updates(
    evaluation: EvaluationRecord,
    **updates: object,
) -> EvaluationRecord:
    payload = evaluation.to_dict()
    new_evaluation_id = updates.get("evaluation_id")
    if new_evaluation_id and "normalized_diagnostics" not in updates:
        normalized = []
        for bundle in payload.get("normalized_diagnostics", []):
            copied_bundle = dict(bundle)
            copied_bundle["evaluation_id"] = new_evaluation_id
            normalized.append(copied_bundle)
        payload["normalized_diagnostics"] = normalized
    payload.update(updates)
    return EvaluationRecord.from_dict(payload)


def build_codex_dossier_example() -> Dict[str, object]:
    """Build a small end-to-end substrate example around a Codex dossier overlay."""

    target = OptimizationTarget(
        target_id="target.codex_dossier.tool_render",
        target_kind="agent_config_overlay",
        baseline_artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        mutable_loci=[
            MutableLocus(
                locus_id="tool.render.exec_command",
                locus_kind="tool_description",
                selector="tools.exec_command.description",
                artifact_ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"max_words": 24},
            ),
            MutableLocus(
                locus_id="prompt.section.optimization_guidance",
                locus_kind="developer_prompt_section",
                selector="developer_prompt.optimization_guidance",
                artifact_ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_guardrails": True},
            ),
        ],
        support_envelope=SupportEnvelope(
            tools=["exec_command", "apply_patch", "spawn_agent"],
            execution_profiles=["codex-e4", "replay-safe"],
            environments=["workspace-write"],
            providers=["openai"],
            models=["gpt-5.2"],
            assumptions={
                "requires_replay_gate": True,
                "forbids_support_widening": True,
                "network_access": False,
                "service_selectors": {"repo_service": "workspace-write"},
                "mcp_servers": ["beads"],
            },
        ),
        invariants=[
            OptimizationInvariant(
                invariant_id="bounded-overlay-only",
                description="candidate may only modify declared loci",
            ),
            OptimizationInvariant(
                invariant_id="support-envelope-preserved",
                description="candidate may not silently widen support claims",
            ),
        ],
        metadata={"example": "codex_dossier"},
    )

    candidate = CandidateBundle(
        candidate_id="cand.codex_dossier.001",
        source_target_id=target.target_id,
        applied_loci=[
            "tool.render.exec_command",
            "prompt.section.optimization_guidance",
        ],
        changes=[
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={"description": "Run shell commands with concise, user-legible summaries."},
                rationale="Tighten visible phrasing without changing capability scope.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={"text": "Prefer narrow overlays and preserve replay-safe behavior."},
                rationale="Improve optimization guidance while keeping the blast radius bounded.",
            ),
        ],
        provenance={
            "backend": "reflective_pareto",
            "selection_reason": "no_regression_replay_lane",
        },
        metadata={"example": "codex_dossier"},
    )

    materialized = materialize_candidate(
        target,
        candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {
                "tools": {
                    "exec_command": {
                        "description": "Run shell commands with concise, user-legible summaries."
                    }
                },
                "developer_prompt": {
                    "optimization_guidance": "Prefer narrow overlays and preserve replay-safe behavior."
                },
            },
        },
        effective_tool_surface={
            "tools": ["exec_command", "apply_patch", "spawn_agent"],
            "exposed_count": 3,
        },
        evaluation_input_compatibility={
            "replay": True,
            "schema": 2,
            "conformance_bundle": "codex-e4",
        },
        metadata={"example": "codex_dossier"},
    )

    return {
        "target": target,
        "candidate": candidate,
        "materialized": materialized,
    }


def build_codex_dossier_example_payload() -> Dict[str, Dict[str, object]]:
    example = build_codex_dossier_example()
    target = example["target"]
    candidate = example["candidate"]
    materialized = example["materialized"]
    assert isinstance(target, OptimizationTarget)
    assert isinstance(candidate, CandidateBundle)
    assert isinstance(materialized, MaterializedCandidate)
    return {
        "target": target.to_dict(),
        "candidate": candidate.to_dict(),
        "materialized": materialized.to_dict(),
    }


def build_codex_dossier_dataset_example() -> Dict[str, object]:
    """Build a small dataset/truth example for the codex dossier optimization lane."""

    rationale = CorrectnessRationale(
        rationale_id="rat.codex_dossier.exec_command.v1",
        summary="The optimized wording must improve clarity without widening capabilities.",
        explanation=(
            "This sample exists to optimize wording and guidance surfaces, not tool semantics. "
            "A correct candidate keeps the same supported tools, keeps replay-safe behavior, "
            "and improves visible wording only inside the declared overlay loci."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        acceptance_clauses=[
            "The candidate only modifies declared overlay loci.",
            "The resulting support envelope is preserved.",
            "The wording remains consistent with replay-safe behavior.",
        ],
        forbidden_behavior_clauses=[
            "Do not add new tools to the supported tool surface.",
            "Do not change provider/model support claims.",
            "Do not rewrite unrelated prompt sections.",
        ],
        metadata={"example": "codex_dossier"},
    )

    truth = GroundTruthPackage(
        package_id="gt.codex_dossier.exec_command.v1",
        oracle_kind="spec_and_replay_policy",
        expected_result={
            "must_preserve_support_envelope": True,
            "must_change_only_declared_loci": True,
            "must_improve_user_visible_clarity": True,
        },
        expected_artifacts=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"role": "baseline_artifact"},
            )
        ],
        rationale_refs=[rationale.rationale_id],
        executable_checks=[
            {"kind": "replay_gate", "lane": "codex-e4", "required": True},
            {"kind": "support_envelope_diff", "must_be_empty": True},
        ],
        admissible_alternatives=[
            {"kind": "text_variation", "constraint": "meaning_preserving"},
        ],
        metadata={"example": "codex_dossier"},
    )

    sample = OptimizationSample(
        sample_id="sample.codex_dossier.exec_command.v1",
        target_id="target.codex_dossier.tool_render",
        prompt_input=(
            "Improve the `exec_command` tool description and optimization-guidance section so they are "
            "clearer to users while preserving all support-envelope and replay-safe constraints."
        ),
        environment_requirements={
            "workspace_mode": "workspace-write",
            "requires_replay": True,
            "schema": 2,
            "service_selectors": {"repo_service": "workspace-write"},
        },
        bound_tool_requirements=["exec_command", "apply_patch", "spawn_agent"],
        environment_selector=EnvironmentSelector(
            selector_id="env.codex_dossier.workspace_write",
            environment_kind="workspace-write",
            profile="workspace-write",
            required_capabilities=["workspace-write", "replay-safe"],
        ),
        tool_pack_context=ToolPackContext(
            pack_id="toolpack.codex_dossier.v1",
            profile="codex-dossier-default",
            required_tools=[
                ToolRequirement(tool_name="exec_command"),
                ToolRequirement(tool_name="apply_patch"),
                ToolRequirement(tool_name="spawn_agent"),
            ],
        ),
        service_contexts=[
            ServiceContextRequirement(
                service_id="repo_service",
                selector="workspace-write",
                requires_network_access=False,
            )
        ],
        sandbox_context=SandboxContextRequirement(
            sandbox_profile="workspace-default",
            filesystem_mode="workspace-write",
            network_access=False,
            required_capabilities=["workspace-write"],
        ),
        mcp_context=MCPContextRequirement(
            server_names=["beads"],
            required_resources=["issues"],
            requires_network_access=False,
        ),
        expected_checks=[
            "support_envelope_preserved",
            "declared_loci_only",
            "replay_gate_green",
        ],
        ground_truth_package=truth,
        metadata={"split_hint": "train"},
    )

    dataset = OptimizationDataset(
        dataset_id="dataset.codex_dossier.substrate.v1",
        dataset_version="2026-03-12.v1",
        samples=[sample],
        split_definitions={"train": [sample.sample_id], "eval": []},
        scope_notes=[
            "This dataset is intentionally narrow and evidence-backed.",
            "It is designed to optimize bounded overlay surfaces, not broad config rewrites.",
        ],
        reproducibility_metadata={
            "created_from": "phase_b_truth_example",
            "target_family": "codex_dossier",
            "requires_replay_safe_lane": True,
            "dataset_runtime_context_id": "context.dataset.codex_dossier.v1",
        },
        dataset_runtime_context=OptimizationRuntimeContext(
            context_id="context.dataset.codex_dossier.v1",
            environment_selector=EnvironmentSelector(
                selector_id="env.dataset.codex_dossier",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id="toolpack.dataset.codex_dossier",
                profile="codex-dossier-default",
                required_tools=[
                    ToolRequirement(tool_name="exec_command"),
                    ToolRequirement(tool_name="apply_patch"),
                    ToolRequirement(tool_name="spawn_agent"),
                ],
            ),
            service_contexts=[
                ServiceContextRequirement(
                    service_id="repo_service",
                    selector="workspace-write",
                )
            ],
            sandbox_context=SandboxContextRequirement(
                sandbox_profile="workspace-default",
                filesystem_mode="workspace-write",
                network_access=False,
                required_capabilities=["workspace-write"],
            ),
            mcp_context=MCPContextRequirement(
                server_names=["beads"],
                required_resources=["issues"],
                requires_network_access=False,
            ),
        ),
        rationale_catalog=[rationale],
        metadata={"example": "codex_dossier"},
    )

    return {
        "dataset": dataset,
        "sample": sample,
        "ground_truth_package": truth,
        "correctness_rationale": rationale,
    }


def build_codex_dossier_dataset_example_payload() -> Dict[str, Dict[str, object]]:
    example = build_codex_dossier_dataset_example()
    dataset = example["dataset"]
    sample = example["sample"]
    truth = example["ground_truth_package"]
    rationale = example["correctness_rationale"]
    assert isinstance(dataset, OptimizationDataset)
    assert isinstance(sample, OptimizationSample)
    assert isinstance(truth, GroundTruthPackage)
    assert isinstance(rationale, CorrectnessRationale)
    return {
        "dataset": dataset.to_dict(),
        "sample": sample.to_dict(),
        "ground_truth_package": truth.to_dict(),
        "correctness_rationale": rationale.to_dict(),
    }


def build_codex_dossier_evaluation_example() -> Dict[str, object]:
    """Build a canonical evaluation/wrongness example on top of the existing codex dossier examples."""

    substrate = build_codex_dossier_example()
    dataset_example = build_codex_dossier_dataset_example()

    target = substrate["target"]
    candidate = substrate["candidate"]
    materialized = substrate["materialized"]
    dataset = dataset_example["dataset"]
    sample = dataset_example["sample"]
    truth = dataset_example["ground_truth_package"]

    assert isinstance(target, OptimizationTarget)
    assert isinstance(candidate, CandidateBundle)
    assert isinstance(materialized, MaterializedCandidate)
    assert isinstance(dataset, OptimizationDataset)
    assert isinstance(sample, OptimizationSample)
    assert isinstance(truth, GroundTruthPackage)

    evaluation_id = "eval.codex_dossier.001"

    diagnostics = DiagnosticBundle(
        bundle_id="diag.codex_dossier.001",
        evaluation_id=evaluation_id,
        evaluator_mode="replay",
        determinism_class="deterministic",
        entries=[
            DiagnosticEntry(
                diagnostic_id="diag-entry.codex_dossier.001",
                kind="support_envelope_diff",
                severity="error",
                message="Candidate widened the effective tool surface relative to the declared support envelope.",
                evidence_refs=[
                    ArtifactRef(
                        ref="artifacts/optimization/codex_dossier/support_envelope_diff.json",
                        media_type="application/json",
                    )
                ],
                locus_id="tool.render.exec_command",
            ),
            DiagnosticEntry(
                diagnostic_id="diag-entry.codex_dossier.002",
                kind="replay_gate",
                severity="warning",
                message="Replay lane remained stable but surfaced a support-envelope mismatch.",
                evidence_refs=[
                    ArtifactRef(
                        ref="artifacts/optimization/codex_dossier/replay_summary.json",
                        media_type="application/json",
                    )
                ],
                locus_id="prompt.section.optimization_guidance",
            ),
        ],
        cache_identity={
            "key": "codex-dossier-replay-eval-001",
            "version": "v1",
            "sample_id": sample.sample_id,
        },
        retry_policy_hint={"max_retries": 0, "reason": "deterministic_replay_lane"},
        reproducibility_notes={
            "requires_replay_safe_lane": True,
            "schema": 2,
            "conformance_bundle": "codex-e4",
        },
    )

    wrongness = WrongnessReport(
        wrongness_id="wrong.codex_dossier.001",
        wrongness_class="policy.support_envelope_violation",
        failure_locus="tool.render.exec_command",
        explanation=(
            "The candidate wording change implied a broader tool surface than the target support envelope allows, "
            "so the result cannot be promoted even though the replay lane itself remained stable."
        ),
        confidence=0.97,
        supporting_evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/codex_dossier/support_envelope_diff.json",
                media_type="application/json",
            )
        ],
        likely_repair_locus="tool.render.exec_command",
    )

    evaluation = EvaluationRecord(
        evaluation_id=evaluation_id,
        target_id=target.target_id,
        candidate_id=candidate.candidate_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        sample_id=sample.sample_id,
        evaluator_id="replay.codex_dossier",
        evaluator_version="2026-03-12.v1",
        status="completed",
        outcome="rejected_by_gate",
        started_at="2026-03-12T12:00:00.000Z",
        completed_at="2026-03-12T12:00:00.250Z",
        duration_ms=250,
        raw_evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/codex_dossier/replay_summary.json",
                media_type="application/json",
            ),
            ArtifactRef(
                ref="artifacts/optimization/codex_dossier/support_envelope_diff.json",
                media_type="application/json",
            ),
        ],
        normalized_diagnostics=[diagnostics],
        wrongness_reports=[wrongness],
        support_envelope_snapshot=target.support_envelope,
        evaluation_input_compatibility=materialized.evaluation_input_compatibility,
        gate_results={
            "replay_gate_green": True,
            "support_envelope_preserved": False,
        },
        metadata={
            "example": "codex_dossier",
            "ground_truth_package_id": truth.package_id,
        },
    )

    return {
        "evaluation": evaluation,
        "diagnostics": diagnostics,
        "wrongness_report": wrongness,
        "target": target,
        "candidate": candidate,
        "dataset": dataset,
        "sample": sample,
    }


def build_codex_dossier_evaluation_example_payload() -> Dict[str, Dict[str, object]]:
    example = build_codex_dossier_evaluation_example()
    evaluation = example["evaluation"]
    diagnostics = example["diagnostics"]
    wrongness = example["wrongness_report"]
    assert isinstance(evaluation, EvaluationRecord)
    assert isinstance(diagnostics, DiagnosticBundle)
    assert isinstance(wrongness, WrongnessReport)
    return {
        "evaluation": evaluation.to_dict(),
        "diagnostics": diagnostics.to_dict(),
        "wrongness_report": wrongness.to_dict(),
    }


def build_codex_dossier_backend_example() -> Dict[str, object]:
    """Build a canonical reflective-backend example over the codex dossier evaluation lane."""

    evaluation_example = build_codex_dossier_evaluation_example()
    target = evaluation_example["target"]
    candidate = evaluation_example["candidate"]
    dataset = evaluation_example["dataset"]
    evaluation = evaluation_example["evaluation"]
    assert isinstance(target, OptimizationTarget)
    assert isinstance(candidate, CandidateBundle)
    assert isinstance(dataset, OptimizationDataset)
    assert isinstance(evaluation, EvaluationRecord)

    substrate = build_codex_dossier_example()
    materialized = substrate["materialized"]
    assert isinstance(materialized, MaterializedCandidate)

    request = ReflectiveParetoBackendRequest(
        request_id="backend.codex_dossier.001",
        target=target,
        baseline_candidate=candidate,
        baseline_materialized_candidate=materialized,
        dataset=dataset,
        evaluations=[evaluation],
        mutation_bounds=MutationBounds(
            max_changed_loci=2,
            max_changed_artifacts=1,
            max_total_value_bytes=1000,
            metadata={"example": "codex_dossier"},
        ),
        max_proposals=2,
        metadata={"example": "codex_dossier"},
    )
    result = run_reflective_pareto_backend(request)
    assert isinstance(result, ReflectiveParetoBackendResult)

    return {
        "request": request,
        "result": result,
        "portfolio": result.portfolio,
        "reflection_decision": result.reflection_decision,
        "proposals": result.proposals,
    }


def build_codex_dossier_backend_example_payload() -> Dict[str, Dict[str, object]]:
    example = build_codex_dossier_backend_example()
    request = example["request"]
    result = example["result"]
    assert isinstance(request, ReflectiveParetoBackendRequest)
    assert isinstance(result, ReflectiveParetoBackendResult)
    return {
        "request": request.to_dict(),
        "result": result.to_dict(),
    }


def build_codex_dossier_runtime_context_examples() -> Dict[str, object]:
    """Build tool-pack/environment-aware backend examples for both compatible and incompatible runtime contexts."""

    compatible = build_codex_dossier_backend_example()
    compatible_request = compatible["request"]
    compatible_result = compatible["result"]
    assert isinstance(compatible_request, ReflectiveParetoBackendRequest)
    assert isinstance(compatible_result, ReflectiveParetoBackendResult)

    substrate = build_codex_dossier_example()
    dataset_example = build_codex_dossier_dataset_example()
    base_dataset = dataset_example["dataset"]
    base_truth = dataset_example["ground_truth_package"]
    base_evaluation = build_codex_dossier_evaluation_example()["evaluation"]
    target = substrate["target"]
    candidate = substrate["candidate"]
    materialized = substrate["materialized"]
    assert isinstance(base_dataset, OptimizationDataset)
    assert isinstance(base_truth, GroundTruthPackage)
    assert isinstance(base_evaluation, EvaluationRecord)
    assert isinstance(target, OptimizationTarget)
    assert isinstance(candidate, CandidateBundle)
    assert isinstance(materialized, MaterializedCandidate)

    incompatible_sample = OptimizationSample(
        sample_id="sample.codex_dossier.exec_command.runtime_incompatible.v1",
        target_id=target.target_id,
        prompt_input=(
            "Evaluate the same codex dossier candidate under a remote tool-pack profile that requires MCP, "
            "internet-enabled sandbox access, and a dedicated remote repo service selector."
        ),
        environment_requirements={
            "workspace_mode": "internet-enabled",
            "network_access": True,
        },
        bound_tool_requirements=["exec_command", "mcp_manager"],
        environment_selector=EnvironmentSelector(
            selector_id="env.codex_dossier.internet_enabled",
            environment_kind="internet-enabled",
            profile="internet-enabled",
            required_capabilities=["workspace-write", "internet-enabled"],
        ),
        tool_pack_context=ToolPackContext(
            pack_id="toolpack.codex_dossier.remote.v1",
            profile="codex-dossier-remote",
            required_tools=[
                ToolRequirement(tool_name="exec_command"),
                ToolRequirement(tool_name="mcp_manager"),
            ],
        ),
        service_contexts=[
            ServiceContextRequirement(
                service_id="repo_service",
                selector="remote-readwrite",
                requires_network_access=True,
            )
        ],
        sandbox_context=SandboxContextRequirement(
            sandbox_profile="remote-networked",
            filesystem_mode="internet-enabled",
            network_access=True,
            required_capabilities=["workspace-write", "internet-enabled"],
        ),
        mcp_context=MCPContextRequirement(
            server_names=["github"],
            required_resources=["repos", "pull_requests"],
            requires_network_access=True,
        ),
        expected_checks=["runtime_context_compatible"],
        ground_truth_package=base_truth,
        metadata={"split_hint": "eval", "runtime_variant": "incompatible"},
    )
    incompatible_dataset = OptimizationDataset(
        dataset_id="dataset.codex_dossier.substrate.runtime.v1",
        dataset_version="2026-03-12.v1",
        samples=[incompatible_sample],
        split_definitions={"eval": [incompatible_sample.sample_id]},
        scope_notes=list(base_dataset.scope_notes) + ["This variant stresses runtime-context incompatibility handling."],
        reproducibility_metadata={
            **base_dataset.reproducibility_metadata,
            "runtime_variant": "incompatible",
        },
        dataset_runtime_context=incompatible_sample.runtime_context(),
        rationale_catalog=list(base_dataset.rationale_catalog),
        metadata={"example": "codex_dossier_runtime_incompatible"},
    )
    incompatible_evaluation = _clone_evaluation_with_updates(
        base_evaluation,
        evaluation_id="eval.codex_dossier.runtime_incompatible.001",
        dataset_id=incompatible_dataset.dataset_id,
        sample_id=incompatible_sample.sample_id,
    )
    incompatible_request = ReflectiveParetoBackendRequest(
        request_id="backend.codex_dossier.runtime_incompatible.001",
        target=target,
        baseline_candidate=candidate,
        baseline_materialized_candidate=materialized,
        dataset=incompatible_dataset,
        evaluations=[incompatible_evaluation],
        active_sample_id=incompatible_sample.sample_id,
        mutation_bounds=MutationBounds(
            max_changed_loci=2,
            max_changed_artifacts=1,
            max_total_value_bytes=1000,
            metadata={"example": "codex_dossier_runtime_incompatible"},
        ),
        max_proposals=2,
        metadata={"example": "codex_dossier_runtime_incompatible"},
    )
    incompatible_result = ReflectiveParetoBackend().run(incompatible_request)
    assert isinstance(incompatible_result, ReflectiveParetoBackendResult)

    return {
        "compatible": compatible,
        "incompatible": {
            "request": incompatible_request,
            "result": incompatible_result,
            "dataset": incompatible_dataset,
            "sample": incompatible_sample,
            "evaluation": incompatible_evaluation,
        },
    }


def build_codex_dossier_runtime_context_examples_payload() -> Dict[str, Dict[str, object]]:
    example = build_codex_dossier_runtime_context_examples()
    compatible = example["compatible"]
    incompatible = example["incompatible"]
    return {
        "compatible": {
            "request": compatible["request"].to_dict(),
            "result": compatible["result"].to_dict(),
        },
        "incompatible": {
            "request": incompatible["request"].to_dict(),
            "result": incompatible["result"].to_dict(),
            "dataset": incompatible["dataset"].to_dict(),
            "sample": incompatible["sample"].to_dict(),
            "evaluation": incompatible["evaluation"].to_dict(),
        },
    }


def build_codex_dossier_promotion_examples() -> Dict[str, object]:
    """Build canonical promotion outcomes over the codex dossier backend lane."""

    backend_example = build_codex_dossier_backend_example()
    request = backend_example["request"]
    result = backend_example["result"]
    assert isinstance(request, ReflectiveParetoBackendRequest)
    assert isinstance(result, ReflectiveParetoBackendResult)

    evaluation_example = build_codex_dossier_evaluation_example()
    base_evaluation = evaluation_example["evaluation"]
    assert isinstance(base_evaluation, EvaluationRecord)

    promoted_entry = next(
        entry
        for entry in result.portfolio.entries
        if isinstance(entry, PortfolioEntry) and entry.candidate.candidate_id.endswith("repair.01")
    )
    tradeoff_entry = next(
        entry
        for entry in result.portfolio.entries
        if isinstance(entry, PortfolioEntry) and entry.candidate.candidate_id.endswith("repair.02")
    )

    promotable_evaluation = _clone_evaluation_with_updates(
        base_evaluation,
        evaluation_id="eval.codex_dossier.promotable.001",
        candidate_id=promoted_entry.candidate.candidate_id,
        outcome="passed",
        wrongness_reports=[],
        gate_results={
            "replay_gate_green": True,
            "conformance_gate_green": True,
            "support_envelope_preserved": True,
        },
        raw_evidence_refs=[
            {
                "ref": "artifacts/optimization/codex_dossier/replay_summary_green.json",
                "media_type": "application/json",
            },
            {
                "ref": "artifacts/optimization/codex_dossier/conformance_summary_green.json",
                "media_type": "application/json",
            },
        ],
        normalized_diagnostics=[
            {
                "bundle_id": "diag.codex_dossier.promotable.001",
                "evaluation_id": "eval.codex_dossier.promotable.001",
                "evaluator_mode": "replay",
                "determinism_class": "deterministic",
                "entries": [
                    {
                        "diagnostic_id": "diag-entry.codex_dossier.promotable.001",
                        "kind": "replay_gate",
                        "severity": "info",
                        "message": "Replay gate passed for the repaired candidate.",
                        "evidence_refs": [
                            {
                                "ref": "artifacts/optimization/codex_dossier/replay_summary_green.json",
                                "media_type": "application/json",
                            }
                        ],
                        "locus_id": "tool.render.exec_command",
                    },
                    {
                        "diagnostic_id": "diag-entry.codex_dossier.promotable.002",
                        "kind": "conformance_gate",
                        "severity": "info",
                        "message": "Conformance gate passed for the repaired candidate.",
                        "evidence_refs": [
                            {
                                "ref": "artifacts/optimization/codex_dossier/conformance_summary_green.json",
                                "media_type": "application/json",
                            }
                        ],
                        "locus_id": "tool.render.exec_command",
                    },
                ],
                "cache_identity": {
                    "key": "codex-dossier-promotable-eval-001",
                    "version": "v1",
                },
                "retry_policy_hint": {"max_retries": 0, "reason": "deterministic_replay_lane"},
                "reproducibility_notes": {
                    "requires_replay_safe_lane": True,
                    "schema": 2,
                    "conformance_bundle": "codex-e4",
                },
            }
        ],
    )

    insufficient_evaluation = _clone_evaluation_with_updates(
        base_evaluation,
        evaluation_id="eval.codex_dossier.frontier.001",
        candidate_id=tradeoff_entry.candidate.candidate_id,
        outcome="inconclusive",
        gate_results={
            "support_envelope_preserved": True,
        },
        raw_evidence_refs=[
            {
                "ref": "artifacts/optimization/codex_dossier/eval_without_replay.json",
                "media_type": "application/json",
            }
        ],
        normalized_diagnostics=[
            {
                "bundle_id": "diag.codex_dossier.frontier.001",
                "evaluation_id": "eval.codex_dossier.frontier.001",
                "evaluator_mode": "live",
                "determinism_class": "environment_volatile",
                "entries": [
                    {
                        "diagnostic_id": "diag-entry.codex_dossier.frontier.001",
                        "kind": "support_envelope_diff",
                        "severity": "info",
                        "message": "No support-envelope widening was observed, but replay evidence was unavailable.",
                        "evidence_refs": [
                            {
                                "ref": "artifacts/optimization/codex_dossier/eval_without_replay.json",
                                "media_type": "application/json",
                            }
                        ],
                        "locus_id": "tool.render.exec_command",
                    }
                ],
                "cache_identity": {
                    "key": "codex-dossier-frontier-eval-001",
                    "version": "v1",
                },
                "retry_policy_hint": {"max_retries": 1, "reason": "replay_evidence_missing"},
                "reproducibility_notes": {
                    "requires_replay_safe_lane": True,
                },
            }
        ],
        wrongness_reports=[],
        evaluation_input_compatibility={
            "replay": False,
            "schema": 2,
            "conformance_bundle": "codex-e4",
        },
    )

    support_failure_evaluation = _clone_evaluation_with_updates(
        base_evaluation,
        evaluation_id="eval.codex_dossier.support_fail.001",
        candidate_id=promoted_entry.candidate.candidate_id,
    )
    support_failure_materialized = MaterializedCandidate(
        candidate_id=promoted_entry.candidate.candidate_id,
        source_target_id=request.target.target_id,
        applied_loci=promoted_entry.candidate.applied_loci,
        effective_artifact=promoted_entry.materialized_candidate.effective_artifact,
        effective_tool_surface=promoted_entry.materialized_candidate.effective_tool_surface,
        support_envelope=SupportEnvelope(
            tools=request.target.support_envelope.tools + ["mcp_manager"],
            execution_profiles=request.target.support_envelope.execution_profiles,
            environments=request.target.support_envelope.environments,
            providers=request.target.support_envelope.providers,
            models=request.target.support_envelope.models,
            assumptions=dict(request.target.support_envelope.assumptions),
        ),
        evaluation_input_compatibility=promoted_entry.materialized_candidate.evaluation_input_compatibility,
    )

    promotable_record, promotable_decision = promote_candidate(
        record_id="promotion.codex_dossier.promotable.001",
        target=request.target,
        materialized_candidate=promoted_entry.materialized_candidate,
        evaluation=promotable_evaluation,
        created_at="2026-03-12T13:00:00.000Z",
        gated_at="2026-03-12T13:00:01.000Z",
        metadata={"example": "promotable"},
    )
    frontier_record, frontier_decision = promote_candidate(
        record_id="promotion.codex_dossier.frontier.001",
        target=request.target,
        materialized_candidate=tradeoff_entry.materialized_candidate,
        evaluation=insufficient_evaluation,
        created_at="2026-03-12T13:10:00.000Z",
        gated_at="2026-03-12T13:10:01.000Z",
        metadata={"example": "frontier_blocked"},
    )
    support_failure_record, support_failure_decision = promote_candidate(
        record_id="promotion.codex_dossier.support_fail.001",
        target=request.target,
        materialized_candidate=support_failure_materialized,
        evaluation=support_failure_evaluation,
        created_at="2026-03-12T13:20:00.000Z",
        gated_at="2026-03-12T13:20:01.000Z",
        metadata={"example": "support_fail"},
    )

    assert isinstance(promotable_record, PromotionRecord)
    assert isinstance(frontier_record, PromotionRecord)
    assert isinstance(support_failure_record, PromotionRecord)
    assert isinstance(promotable_decision, PromotionDecision)
    assert isinstance(frontier_decision, PromotionDecision)
    assert isinstance(support_failure_decision, PromotionDecision)

    return {
        "backend_request": request,
        "backend_result": result,
        "promotable": {
            "record": promotable_record,
            "decision": promotable_decision,
            "evaluation": promotable_evaluation,
            "materialized_candidate": promoted_entry.materialized_candidate,
        },
        "frontier_blocked": {
            "record": frontier_record,
            "decision": frontier_decision,
            "evaluation": insufficient_evaluation,
            "materialized_candidate": tradeoff_entry.materialized_candidate,
        },
        "support_fail": {
            "record": support_failure_record,
            "decision": support_failure_decision,
            "evaluation": support_failure_evaluation,
            "materialized_candidate": support_failure_materialized,
        },
    }


def build_codex_dossier_promotion_examples_payload() -> Dict[str, Dict[str, object]]:
    example = build_codex_dossier_promotion_examples()
    promotable = example["promotable"]
    frontier_blocked = example["frontier_blocked"]
    support_fail = example["support_fail"]
    return {
        "promotable": {
            "record": promotable["record"].to_dict(),
            "decision": promotable["decision"].to_dict(),
            "evaluation": promotable["evaluation"].to_dict(),
            "materialized_candidate": promotable["materialized_candidate"].to_dict(),
        },
        "frontier_blocked": {
            "record": frontier_blocked["record"].to_dict(),
            "decision": frontier_blocked["decision"].to_dict(),
            "evaluation": frontier_blocked["evaluation"].to_dict(),
            "materialized_candidate": frontier_blocked["materialized_candidate"].to_dict(),
        },
        "support_fail": {
            "record": support_fail["record"].to_dict(),
            "decision": support_fail["decision"].to_dict(),
            "evaluation": support_fail["evaluation"].to_dict(),
            "materialized_candidate": support_fail["materialized_candidate"].to_dict(),
        },
    }
