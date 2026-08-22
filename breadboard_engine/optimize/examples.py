from __future__ import annotations

from typing import Dict, List

from .benchmark import (
    BackendComparisonResult,
    BenchmarkRunManifest,
    BenchmarkRunResult,
    BenchmarkSplit,
    CandidateComparisonResult,
    build_paired_candidate_comparison,
)
from .backend import (
    MutationBounds,
    OptimizationExecutionContext,
    PortfolioEntry,
    ReflectiveParetoBackend,
    ReflectiveParetoBackendRequest,
    ReflectiveParetoBackendResult,
    StagedOptimizerRequest,
    run_reflective_pareto_backend,
    run_single_locus_greedy_backend,
    run_staged_optimizer,
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
    PromotionEvidenceSummary,
    PromotionRecord,
    build_promotion_evidence_summary,
    promote_candidate,
)
from .suites import (
    EvaluationSuiteManifest,
    FamilyCompositionManifest,
    ObjectiveBreakdownResult,
    ObjectiveSuiteManifest,
    SearchSpaceManifest,
    TargetFamilyManifest,
    TransferCohortManifest,
    TransferSliceManifest,
    VerifierAugmentedExperimentResult,
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


def build_support_execution_benchmark_example() -> Dict[str, object]:
    """Build a first V1.5 benchmark/comparison example for support-claim and execution-profile heuristics."""

    target = OptimizationTarget(
        target_id="target.codex_dossier.support_execution.v1",
        target_kind="support_execution_policy_overlay",
        baseline_artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        mutable_loci=[
            MutableLocus(
                locus_id="policy.support_claim_limited_actions",
                locus_kind="support_claim_policy",
                selector="coordination.intervention.support_claim_limited_actions",
                artifact_ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_honesty": True},
            ),
            MutableLocus(
                locus_id="policy.execution_profile.selection",
                locus_kind="execution_profile_heuristic",
                selector="workspace.execution_profile.selection.default",
                artifact_ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_replay_safe_lane": True},
            ),
        ],
        support_envelope=SupportEnvelope(
            tools=["exec_command", "apply_patch", "spawn_agent"],
            execution_profiles=["workspace-write", "replay-safe"],
            environments=["workspace-write"],
            providers=["openai"],
            models=["gpt-5.2"],
            assumptions={
                "requires_replay_gate": True,
                "forbids_support_widening": True,
                "support_sensitive_review": True,
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
        metadata={"lane": "support_execution", "phase": "v1_5"},
    )

    rationale = CorrectnessRationale(
        rationale_id="rat.support_execution.v1",
        summary="Support-sensitive tasks must not gain silent continue paths or less honest execution-profile choices.",
        explanation=(
            "The optimized heuristic must preserve evidence honesty and replay safety. "
            "A correct child candidate may improve profile choice or action limiting, "
            "but it may not let support-sensitive cases silently continue on weaker evidence."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        acceptance_clauses=[
            "Support-sensitive tasks require explicit review or bounded actions.",
            "Replay-safe execution remains available for promotion-sensitive work.",
            "Execution-profile choice remains honest about workspace-write requirements.",
        ],
        forbidden_behavior_clauses=[
            "Do not silently add unrestricted continue paths for support-sensitive cases.",
            "Do not weaken replay or evidence burden to achieve a cheaper win.",
        ],
        metadata={"lane": "support_execution"},
    )

    def _sample(
        sample_id: str,
        prompt_input: str,
        split_hint: str,
        expected_profile: str,
        required_action: str,
        bucket_tags: list[str],
    ) -> OptimizationSample:
        return OptimizationSample(
            sample_id=sample_id,
            target_id=target.target_id,
            prompt_input=prompt_input,
            environment_requirements={
                "workspace_mode": "workspace-write",
                "requires_replay": True,
                "evidence_mode": "support_sensitive",
            },
            bound_tool_requirements=["exec_command", "apply_patch", "spawn_agent"],
            environment_selector=EnvironmentSelector(
                selector_id=f"env.{sample_id}",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write", "replay-safe"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id=f"toolpack.{sample_id}",
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
                "support_honesty_preserved",
                "execution_profile_is_honest",
                "review_path_is_explicit",
            ],
            ground_truth_package=GroundTruthPackage(
                package_id=f"gt.{sample_id}",
                oracle_kind="support_and_profile_policy",
                expected_result={
                    "expected_execution_profile": expected_profile,
                    "required_action": required_action,
                    "must_require_review_for_support_sensitive": True,
                },
                rationale_refs=[rationale.rationale_id],
                executable_checks=[
                    {"kind": "support_claim_review_path", "required_action": required_action},
                    {"kind": "execution_profile_selector", "expected_profile": expected_profile},
                ],
                metadata={"bucket_tags": list(bucket_tags)},
            ),
            metadata={"split_hint": split_hint, "bucket_tags": list(bucket_tags)},
        )

    train_sample = _sample(
        "sample.support_execution.train.001",
        "Choose the right execution profile for a repo-edit task that needs workspace-write and replay-safe evidence.",
        "train",
        "workspace-write",
        "checkpoint",
        ["repo-edit", "support-sensitive", "workspace-write"],
    )
    validation_sample = _sample(
        "sample.support_execution.validation.001",
        "Handle a replay-sensitive verification pass without silently dropping replay-safe requirements.",
        "validation",
        "replay-safe",
        "checkpoint",
        ["verification", "replay-sensitive"],
    )
    hold_sample = _sample(
        "sample.support_execution.hold.001",
        "Support-sensitive task with ambiguous evidence: do not allow an unrestricted continue path.",
        "hold",
        "workspace-write",
        "terminate",
        ["support-sensitive", "hidden-hold"],
    )
    regression_sample = _sample(
        "sample.support_execution.regression.001",
        "Preserve bounded intervention actions for legacy support-claim-limited paths.",
        "regression",
        "workspace-write",
        "checkpoint",
        ["legacy-path", "regression"],
    )

    dataset = OptimizationDataset(
        dataset_id="dataset.support_execution.v1",
        dataset_version="2026-03-13.v1_5",
        samples=[train_sample, validation_sample, hold_sample, regression_sample],
        split_definitions={
            "train": [train_sample.sample_id],
            "validation": [validation_sample.sample_id],
            "hold": [hold_sample.sample_id],
            "regression": [regression_sample.sample_id],
        },
        scope_notes=[
            "This benchmark pack targets support-claim honesty and execution-profile selection.",
            "The hidden hold sample exists to catch candidates that quietly widen continue paths.",
        ],
        reproducibility_metadata={
            "target_family": "support_execution",
            "requires_replay_safe_lane": True,
            "phase": "v1_5",
        },
        dataset_runtime_context=OptimizationRuntimeContext(
            context_id="context.dataset.support_execution.v1",
            environment_selector=EnvironmentSelector(
                selector_id="env.dataset.support_execution",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write", "replay-safe"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id="toolpack.dataset.support_execution",
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
        ),
        rationale_catalog=[rationale],
        metadata={"lane": "support_execution", "phase": "v1_5"},
    )

    parent_candidate = CandidateBundle(
        candidate_id="cand.support_execution.parent.001",
        source_target_id=target.target_id,
        applied_loci=[
            "policy.support_claim_limited_actions",
            "policy.execution_profile.selection",
        ],
        changes=[
            CandidateChange(
                locus_id="policy.support_claim_limited_actions",
                value={"allowed_actions": ["checkpoint", "terminate", "continue"]},
                rationale="Current heuristic permits continue when support pressure looks low.",
            ),
            CandidateChange(
                locus_id="policy.execution_profile.selection",
                value={
                    "preferred_profile": "workspace-write",
                    "fallback_profile": "workspace-write",
                    "require_replay_safe_lane": False,
                },
                rationale="Current heuristic optimizes for throughput over explicit replay-safe enforcement.",
            ),
        ],
        provenance={"kind": "baseline_lane_candidate"},
        metadata={"lane": "support_execution", "role": "parent"},
    )
    child_candidate = CandidateBundle(
        candidate_id="cand.support_execution.child.001",
        source_target_id=target.target_id,
        applied_loci=[
            "policy.support_claim_limited_actions",
            "policy.execution_profile.selection",
        ],
        changes=[
            CandidateChange(
                locus_id="policy.support_claim_limited_actions",
                value={"allowed_actions": ["checkpoint", "terminate"]},
                rationale="Remove unrestricted continue from support-sensitive intervention paths.",
            ),
            CandidateChange(
                locus_id="policy.execution_profile.selection",
                value={
                    "preferred_profile": "workspace-write",
                    "fallback_profile": "replay-safe",
                    "require_replay_safe_lane": True,
                },
                rationale="Make replay-safe fallback explicit for support-sensitive work.",
            ),
        ],
        provenance={"kind": "reflective_child_candidate"},
        metadata={"lane": "support_execution", "role": "child"},
    )

    parent_materialized = materialize_candidate(
        target,
        parent_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {
                "coordination": {
                    "intervention": {"support_claim_limited_actions": ["checkpoint", "terminate", "continue"]}
                },
                "workspace": {
                    "execution_profile": {
                        "selection": {
                            "preferred_profile": "workspace-write",
                            "fallback_profile": "workspace-write",
                            "require_replay_safe_lane": False,
                        }
                    }
                },
            },
        },
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 2},
        metadata={"lane": "support_execution", "role": "parent"},
    )
    child_materialized = materialize_candidate(
        target,
        child_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {
                "coordination": {
                    "intervention": {"support_claim_limited_actions": ["checkpoint", "terminate"]}
                },
                "workspace": {
                    "execution_profile": {
                        "selection": {
                            "preferred_profile": "workspace-write",
                            "fallback_profile": "replay-safe",
                            "require_replay_safe_lane": True,
                        }
                    }
                },
            },
        },
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 2},
        metadata={"lane": "support_execution", "role": "child"},
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.support_execution.v2",
        suite_kind="support_execution_family",
        evaluator_stack=[
            "support_claim_checker.v1",
            "execution_profile_checker.v1",
            "promotion_readiness_slice.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "flake_on_nonrepeatable": False},
        capture_requirements=[
            "support_honesty_delta",
            "execution_profile_selection",
            "review_path_required_action",
            "hidden_hold_preservation",
        ],
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "reject_support_widening": True,
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_parent_child.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=[
            "paired_eval_json",
            "benchmark_summary_json",
        ],
        metadata={"lane": "support_execution", "phase": "v2"},
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.support_execution.v2",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "support_honesty": {
                "direction": "maximize",
                "source_metric": "support_honesty_delta",
                "promotion_sensitive": True,
            },
            "execution_profile_fidelity": {
                "direction": "maximize",
                "source_metric": "execution_profile_match",
                "promotion_sensitive": True,
            },
            "mutation_cost": {
                "direction": "minimize",
                "source_metric": "cost_delta_usd",
                "promotion_sensitive": False,
            },
        },
        penalties={
            "support_widening": {
                "kind": "hard_block",
                "reason": "support widening invalidates the family objective",
            },
            "hidden_hold_regression": {
                "kind": "hard_block",
                "reason": "hidden hold regressions are not admissible wins",
            },
        },
        aggregation_rules={
            "per_sample": "weighted_sum",
            "per_bucket": "minimum_bucket_floor",
            "global": "support_sensitive_first",
        },
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
            "blocked_when_missing_regression_bucket": True,
        },
        frontier_dimensions=[
            "support_honesty",
            "execution_profile_fidelity",
            "mutation_cost",
        ],
        promotion_annotations={
            "requires_support_sensitive_review": True,
            "review_class": "support_honesty",
        },
        visibility_annotations={
            "hidden_hold_channels": ["support_honesty", "execution_profile_fidelity"],
        },
        metadata={"lane": "support_execution", "phase": "v2"},
    )

    target_family = TargetFamilyManifest(
        family_id="family.support_execution.v2",
        family_kind="support_execution_policy_family",
        target_ids=[target.target_id],
        family_scope="Support-claim limiting and execution-profile heuristics for replay-safe workspace tasks.",
        mutable_loci_ids=[locus.locus_id for locus in target.mutable_loci],
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        review_class="support_honesty",
        runtime_context_assumptions={
            "environment_selector_profile": "workspace-write",
            "requires_replay_safe_lane": True,
            "network_access": False,
            "tool_pack_profile": "codex-dossier-default",
        },
        promotion_class="support_sensitive_family_change",
        artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        metadata={"lane": "support_execution", "phase": "v2"},
    )

    search_space = SearchSpaceManifest(
        search_space_id="searchspace.support_execution.v2",
        family_id=target_family.family_id,
        allowed_loci=[locus.locus_id for locus in target.mutable_loci],
        mutation_kinds_by_locus={
            "policy.support_claim_limited_actions": ["replace"],
            "policy.execution_profile.selection": ["replace"],
        },
        value_domains_by_locus={
            "policy.support_claim_limited_actions": {
                "allowed_actions_subset_of": ["checkpoint", "terminate", "continue"],
                "forbid_unbounded_new_actions": True,
            },
            "policy.execution_profile.selection": {
                "preferred_profiles": ["workspace-write", "replay-safe"],
                "fallback_profiles": ["workspace-write", "replay-safe"],
                "require_boolean_flag": "require_replay_safe_lane",
            },
        },
        semantic_constraints={
            "policy.support_claim_limited_actions": {
                "must_preserve_honesty": True,
                "must_not_add_unreviewed_continue_path": True,
            },
            "policy.execution_profile.selection": {
                "must_preserve_replay_safe_lane": True,
                "must_not_hide_workspace_write_requirement": True,
            },
        },
        invariants=[
            "bounded-overlay-only",
            "support-envelope-preserved",
        ],
        unsafe_expansion_notes=[
            "Adding new intervention actions is out of scope for this family.",
            "Changing providers, models, or tool surfaces is out of scope for this family.",
        ],
        metadata={"lane": "support_execution", "phase": "v2"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.support_execution.v1",
        benchmark_kind="support_execution_heuristic_pack",
        target_id=target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=parent_candidate.candidate_id,
        environment_domain="workspace-write/replay-safe",
        evaluator_stack=[
            "support_claim_checker.v1",
            "execution_profile_checker.v1",
            "promotion_readiness_slice.v1",
        ],
        comparison_protocol="paired_parent_child.v1",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=[train_sample.sample_id],
                visibility="mutation_visible",
            ),
            BenchmarkSplit(
                split_name="validation",
                sample_ids=[validation_sample.sample_id],
                visibility="comparison_visible",
            ),
            BenchmarkSplit(
                split_name="hold",
                sample_ids=[hold_sample.sample_id],
                visibility="hidden_hold",
            ),
            BenchmarkSplit(
                split_name="regression",
                sample_ids=[regression_sample.sample_id],
                visibility="comparison_visible",
            ),
        ],
        bucket_tags={
            train_sample.sample_id: ["repo-edit", "support-sensitive"],
            validation_sample.sample_id: ["verification", "replay-sensitive"],
            hold_sample.sample_id: ["support-sensitive", "hidden-hold"],
            regression_sample.sample_id: ["legacy-path", "regression"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "flake_on_nonrepeatable": False},
        contamination_notes=[
            "Hold sample rationale remains comparison-visible only through expected checks.",
            "Mutation-time logic should not see hold-split wrongness labels.",
        ],
        promotion_relevance={
            "requires_support_sensitive_review": True,
            "review_class": "support_honesty",
        },
        artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
            )
        ],
        metadata={
            "lane": "support_execution",
            "phase": "v1_5",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.support_execution.parent_vs_child.001",
        parent_candidate_id=parent_candidate.candidate_id,
        child_candidate_id=child_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=[
            train_sample.sample_id,
            validation_sample.sample_id,
            hold_sample.sample_id,
            regression_sample.sample_id,
        ],
        held_out_sample_ids=[hold_sample.sample_id],
        trial_count=1,
        rationale=(
            "The child candidate preserves support honesty on the hidden hold and regression slices by "
            "removing unrestricted continue and making replay-safe fallback explicit."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution/paired_parent_child_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "correctness_score_delta": 0.25,
            "support_honesty_delta": 1.0,
            "mutation_cost_delta": 0.05,
            "held_out_support_sensitive_win": True,
        },
        better_candidate_id=child_candidate.candidate_id,
        metadata={"lane": "support_execution"},
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.support_execution.child.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=child_candidate.candidate_id,
        per_sample_components={
            train_sample.sample_id: {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "mutation_cost": 0.03,
            },
            validation_sample.sample_id: {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "mutation_cost": 0.02,
            },
            hold_sample.sample_id: {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "mutation_cost": 0.02,
            },
            regression_sample.sample_id: {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 0.95,
                "mutation_cost": 0.01,
            },
        },
        per_bucket_components={
            "support-sensitive": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
            },
            "regression": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 0.95,
            },
        },
        aggregate_objectives={
            "support_honesty": 1.0,
            "execution_profile_fidelity": 0.9875,
            "mutation_cost": 0.02,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 1,
            "blocked_for_uncertainty": False,
        },
        blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution/objective_breakdown_child.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "support_execution",
            "family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    result = BenchmarkRunResult(
        run_id="benchmark_run.support_execution.001",
        manifest_id=manifest.manifest_id,
        candidate_ids=[parent_candidate.candidate_id, child_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={
            "parent_correctness_score": 0.62,
            "child_correctness_score": 0.87,
            "support_honesty_score": 1.0,
            "requires_review": True,
        },
        bucket_outcomes={
            "support-sensitive": {"outcome": "child_win", "held_out": True},
            "regression": {"outcome": "child_win", "held_out": False},
        },
        variance_summary={"trial_count": 1, "stochasticity_class": manifest.stochasticity_class},
        cost_support_evidence_slices={
            "cost_delta_usd": 0.01,
            "support_honesty_delta": 1.0,
            "evidence_burden_preserved": True,
        },
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "eligible_for_promotion": False,
            "requires_review": True,
            "blocked_reason": "support-sensitive heuristic changes require explicit review",
            "objective_breakdown_result_id": objective_breakdown.result_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
        },
        metadata={
            "lane": "support_execution",
            "phase": "v1_5",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    assert isinstance(manifest, BenchmarkRunManifest)
    assert isinstance(comparison, CandidateComparisonResult)
    assert isinstance(result, BenchmarkRunResult)

    return {
        "target": target,
        "dataset": dataset,
        "parent_candidate": parent_candidate,
        "child_candidate": child_candidate,
        "parent_materialized_candidate": parent_materialized,
        "child_materialized_candidate": child_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "target_family": target_family,
        "search_space": search_space,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "benchmark_result": result,
    }


def build_support_execution_benchmark_example_payload() -> Dict[str, object]:
    example = build_support_execution_benchmark_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "parent_candidate": example["parent_candidate"].to_dict(),
        "child_candidate": example["child_candidate"].to_dict(),
        "parent_materialized_candidate": example["parent_materialized_candidate"].to_dict(),
        "child_materialized_candidate": example["child_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "target_family": example["target_family"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
    }


def build_tool_guidance_benchmark_example() -> Dict[str, object]:
    """Build a V1.5 benchmark/comparison example for tool-description and tool-guidance optimization."""

    target = OptimizationTarget(
        target_id="target.codex_dossier.tool_guidance.v1_5",
        target_kind="tool_guidance_overlay",
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
                constraints={"max_words": 28},
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
            assumptions={"requires_replay_gate": True, "forbids_support_widening": True},
        ),
        invariants=[
            OptimizationInvariant("bounded-overlay-only", "candidate may only modify declared loci"),
            OptimizationInvariant("support-envelope-preserved", "candidate may not silently widen support claims"),
        ],
        metadata={"lane": "tool_guidance", "phase": "v1_5"},
    )

    rationale = CorrectnessRationale(
        rationale_id="rat.tool_guidance.v1_5",
        summary="Tool wording should become clearer without changing capability scope or replay-safe guidance.",
        explanation=(
            "The candidate should improve user-visible clarity and actionability while preserving the same tool surface, "
            "same replay-safe expectations, and same bounded-overlay discipline."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
            )
        ],
        acceptance_clauses=[
            "Description is clearer and more concise.",
            "Optimization guidance still prefers bounded overlays and replay-safe behavior.",
        ],
        forbidden_behavior_clauses=[
            "Do not add tools or widen support claims.",
            "Do not rewrite unrelated prompt sections.",
        ],
        metadata={"lane": "tool_guidance"},
    )

    def _sample(sample_id: str, prompt_input: str, split_hint: str, bucket_tags: list[str]) -> OptimizationSample:
        return OptimizationSample(
            sample_id=sample_id,
            target_id=target.target_id,
            prompt_input=prompt_input,
            environment_requirements={"workspace_mode": "workspace-write", "requires_replay": True},
            bound_tool_requirements=["exec_command", "apply_patch", "spawn_agent"],
            environment_selector=EnvironmentSelector(
                selector_id=f"env.{sample_id}",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write", "replay-safe"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id=f"toolpack.{sample_id}",
                profile="codex-dossier-default",
                required_tools=[
                    ToolRequirement("exec_command"),
                    ToolRequirement("apply_patch"),
                    ToolRequirement("spawn_agent"),
                ],
            ),
            service_contexts=[
                ServiceContextRequirement("repo_service", "workspace-write", requires_network_access=False)
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
            expected_checks=["clarity_improved", "support_envelope_preserved", "replay_guidance_preserved"],
            ground_truth_package=GroundTruthPackage(
                package_id=f"gt.{sample_id}",
                oracle_kind="tool_guidance_policy",
                expected_result={
                    "must_preserve_support_envelope": True,
                    "must_preserve_replay_safe_guidance": True,
                    "must_improve_clarity": True,
                },
                rationale_refs=[rationale.rationale_id],
                executable_checks=[
                    {"kind": "support_envelope_diff", "must_be_empty": True},
                    {"kind": "wording_clarity_delta", "must_be_positive": True},
                ],
                metadata={"bucket_tags": list(bucket_tags)},
            ),
            metadata={"split_hint": split_hint, "bucket_tags": list(bucket_tags)},
        )

    train_sample = _sample(
        "sample.tool_guidance.train.001",
        "Clarify the exec_command tool description without changing capability scope.",
        "train",
        ["clarity", "tool-description"],
    )
    validation_sample = _sample(
        "sample.tool_guidance.validation.001",
        "Tighten optimization guidance wording while preserving replay-safe boundaries.",
        "validation",
        ["guidance", "replay-safe"],
    )
    hold_sample = _sample(
        "sample.tool_guidance.hold.001",
        "Catch candidates that look clearer but quietly imply broader capability or weaker guardrails.",
        "hold",
        ["hidden-hold", "support-envelope"],
    )
    regression_sample = _sample(
        "sample.tool_guidance.regression.001",
        "Preserve bounded-overlay language on legacy wording updates.",
        "regression",
        ["regression", "bounded-overlay"],
    )

    dataset = OptimizationDataset(
        dataset_id="dataset.tool_guidance.v1_5",
        dataset_version="2026-03-13.v1_5",
        samples=[train_sample, validation_sample, hold_sample, regression_sample],
        split_definitions={
            "train": [train_sample.sample_id],
            "validation": [validation_sample.sample_id],
            "hold": [hold_sample.sample_id],
            "regression": [regression_sample.sample_id],
        },
        scope_notes=[
            "This benchmark pack targets tool wording and guidance overlays only.",
            "The hidden hold exists to catch fake clarity wins that quietly imply support widening.",
        ],
        reproducibility_metadata={"target_family": "tool_guidance", "phase": "v1_5"},
        dataset_runtime_context=OptimizationRuntimeContext(
            context_id="context.dataset.tool_guidance.v1_5",
            environment_selector=EnvironmentSelector(
                selector_id="env.dataset.tool_guidance",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write", "replay-safe"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id="toolpack.dataset.tool_guidance",
                profile="codex-dossier-default",
                required_tools=[
                    ToolRequirement("exec_command"),
                    ToolRequirement("apply_patch"),
                    ToolRequirement("spawn_agent"),
                ],
            ),
        ),
        rationale_catalog=[rationale],
        metadata={"lane": "tool_guidance", "phase": "v1_5"},
    )

    parent_candidate = CandidateBundle(
        candidate_id="cand.tool_guidance.parent.001",
        source_target_id=target.target_id,
        applied_loci=["tool.render.exec_command", "prompt.section.optimization_guidance"],
        changes=[
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={"description": "Run shell commands."},
                rationale="Current wording is terse but underspecified.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={"text": "Preserve current behavior where possible."},
                rationale="Current wording is vague about replay-safe bounded overlays.",
            ),
        ],
        provenance={"kind": "baseline_lane_candidate"},
        metadata={"lane": "tool_guidance", "role": "parent"},
    )
    child_candidate = CandidateBundle(
        candidate_id="cand.tool_guidance.child.001",
        source_target_id=target.target_id,
        applied_loci=["tool.render.exec_command", "prompt.section.optimization_guidance"],
        changes=[
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={"description": "Run shell commands and return concise, user-legible summaries."},
                rationale="Makes the tool outcome clearer without changing capability scope.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={"text": "Prefer narrow overlays and preserve replay-safe behavior."},
                rationale="Restates the actual bounded-overlay doctrine directly.",
            ),
        ],
        provenance={"kind": "reflective_child_candidate"},
        metadata={"lane": "tool_guidance", "role": "child"},
    )

    parent_materialized = materialize_candidate(
        target,
        parent_candidate,
        effective_artifact={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml", "overlay": {}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 2},
        metadata={"lane": "tool_guidance", "role": "parent"},
    )
    child_materialized = materialize_candidate(
        target,
        child_candidate,
        effective_artifact={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml", "overlay": {}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 2},
        metadata={"lane": "tool_guidance", "role": "child"},
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.tool_guidance.v2",
        suite_kind="tool_guidance_family",
        evaluator_stack=["tool_guidance_checker.v1", "replay_guard_checker.v1"],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1},
        capture_requirements=[
            "clarity_delta",
            "replay_guidance_preserved",
            "support_envelope_preserved",
            "hidden_hold_fake_win_guard",
        ],
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_parent_child.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=[
            "paired_eval_json",
            "benchmark_summary_json",
        ],
        metadata={"lane": "tool_guidance", "phase": "v2"},
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.tool_guidance.v2",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "clarity_gain": {
                "direction": "maximize",
                "source_metric": "clarity_delta",
                "promotion_sensitive": True,
            },
            "guardrail_preservation": {
                "direction": "maximize",
                "source_metric": "replay_guidance_preserved",
                "promotion_sensitive": True,
            },
            "mutation_cost": {
                "direction": "minimize",
                "source_metric": "cost_delta_usd",
                "promotion_sensitive": False,
            },
        },
        penalties={
            "support_widening": {"kind": "hard_block", "reason": "tool-guidance family may not widen support claims"},
            "guardrail_erosion": {"kind": "hard_block", "reason": "replay-safe and bounded-overlay guidance must persist"},
        },
        aggregation_rules={
            "per_sample": "weighted_sum",
            "per_bucket": "minimum_bucket_floor",
            "global": "guardrail_first",
        },
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
        },
        frontier_dimensions=["clarity_gain", "guardrail_preservation", "mutation_cost"],
        promotion_annotations={"requires_support_sensitive_review": False, "review_class": "guidance_integrity"},
        visibility_annotations={"hidden_hold_channels": ["clarity_gain", "guardrail_preservation"]},
        metadata={"lane": "tool_guidance", "phase": "v2"},
    )

    target_family = TargetFamilyManifest(
        family_id="family.tool_guidance.v2",
        family_kind="tool_guidance_overlay_family",
        target_ids=[target.target_id],
        family_scope="Tool descriptions and developer guidance overlays for the codex dossier surface.",
        mutable_loci_ids=[locus.locus_id for locus in target.mutable_loci],
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        review_class="guidance_integrity",
        runtime_context_assumptions={
            "environment_selector_profile": "workspace-write",
            "tool_pack_profile": "codex-dossier-default",
            "requires_replay_safe_guidance": True,
        },
        promotion_class="single_surface_guidance_change",
        artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        metadata={"lane": "tool_guidance", "phase": "v2"},
    )

    search_space = SearchSpaceManifest(
        search_space_id="searchspace.tool_guidance.v2",
        family_id=target_family.family_id,
        allowed_loci=[locus.locus_id for locus in target.mutable_loci],
        mutation_kinds_by_locus={
            "tool.render.exec_command": ["replace"],
            "prompt.section.optimization_guidance": ["replace"],
        },
        value_domains_by_locus={
            "tool.render.exec_command": {
                "max_words": 28,
                "must_preserve_capability_scope": True,
            },
            "prompt.section.optimization_guidance": {
                "must_preserve_guardrails": True,
                "must_reference_replay_safe_behavior": True,
            },
        },
        semantic_constraints={
            "tool.render.exec_command": {
                "must_preserve_support_envelope": True,
            },
            "prompt.section.optimization_guidance": {
                "must_preserve_bounded_overlay_doctrine": True,
                "must_not_imply_weaker_guardrails": True,
            },
        },
        invariants=["bounded-overlay-only", "support-envelope-preserved"],
        unsafe_expansion_notes=[
            "Adding or removing tools is out of scope for this family.",
            "Changing unrelated prompt sections is out of scope for this family.",
        ],
        metadata={"lane": "tool_guidance", "phase": "v2"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.tool_guidance.v1_5",
        benchmark_kind="tool_guidance_pack",
        target_id=target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=parent_candidate.candidate_id,
        environment_domain="workspace-write/replay-safe",
        evaluator_stack=["tool_guidance_checker.v1", "replay_guard_checker.v1"],
        comparison_protocol="paired_parent_child.v1",
        splits=[
            BenchmarkSplit("train", [train_sample.sample_id], "mutation_visible"),
            BenchmarkSplit("validation", [validation_sample.sample_id], "comparison_visible"),
            BenchmarkSplit("hold", [hold_sample.sample_id], "hidden_hold"),
            BenchmarkSplit("regression", [regression_sample.sample_id], "comparison_visible"),
        ],
        bucket_tags={
            train_sample.sample_id: ["clarity", "tool-description"],
            validation_sample.sample_id: ["guidance", "replay-safe"],
            hold_sample.sample_id: ["hidden-hold", "support-envelope"],
            regression_sample.sample_id: ["regression", "bounded-overlay"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1},
        contamination_notes=[
            "Hold split should not expose wrongness labels at mutation time.",
            "Comparison relies on bounded-overlay and support-envelope checks remaining explicit.",
        ],
        promotion_relevance={"requires_support_sensitive_review": False},
        artifact_refs=[ArtifactRef(ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml", media_type="text/yaml")],
        metadata={
            "lane": "tool_guidance",
            "phase": "v1_5",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.tool_guidance.parent_vs_child.001",
        parent_candidate_id=parent_candidate.candidate_id,
        child_candidate_id=child_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=[hold_sample.sample_id],
        trial_count=1,
        rationale="The child improves clarity and preserves replay-safe bounded-overlay language on the hidden hold.",
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/tool_guidance/paired_parent_child_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "clarity_delta": 0.4,
            "replay_guidance_preserved": True,
            "held_out_fake_win_caught": True,
        },
        better_candidate_id=child_candidate.candidate_id,
        metadata={"lane": "tool_guidance"},
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.tool_guidance.child.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=child_candidate.candidate_id,
        per_sample_components={
            train_sample.sample_id: {
                "clarity_gain": 0.9,
                "guardrail_preservation": 1.0,
                "mutation_cost": 0.0,
            },
            validation_sample.sample_id: {
                "clarity_gain": 0.8,
                "guardrail_preservation": 1.0,
                "mutation_cost": 0.0,
            },
            hold_sample.sample_id: {
                "clarity_gain": 0.75,
                "guardrail_preservation": 1.0,
                "mutation_cost": 0.0,
            },
            regression_sample.sample_id: {
                "clarity_gain": 0.65,
                "guardrail_preservation": 1.0,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "tool-description": {"clarity_gain": 0.9, "guardrail_preservation": 1.0},
            "support-envelope": {"clarity_gain": 0.75, "guardrail_preservation": 1.0},
        },
        aggregate_objectives={
            "clarity_gain": 0.775,
            "guardrail_preservation": 1.0,
            "mutation_cost": 0.0,
            "eligible_for_promotion": True,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 1,
            "blocked_for_uncertainty": False,
        },
        blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/tool_guidance/objective_breakdown_child.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "tool_guidance",
            "family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    result = BenchmarkRunResult(
        run_id="benchmark_run.tool_guidance.001",
        manifest_id=manifest.manifest_id,
        candidate_ids=[parent_candidate.candidate_id, child_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={"parent_clarity_score": 0.38, "child_clarity_score": 0.82},
        bucket_outcomes={"tool-description": {"outcome": "child_win"}, "support-envelope": {"outcome": "child_win"}},
        variance_summary={"trial_count": 1, "stochasticity_class": "deterministic"},
        cost_support_evidence_slices={"support_envelope_preserved": True, "cost_delta_usd": 0.0},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/tool_guidance/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "eligible_for_promotion": True,
            "requires_review": False,
            "objective_breakdown_result_id": objective_breakdown.result_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
        },
        metadata={
            "lane": "tool_guidance",
            "phase": "v1_5",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    return {
        "target": target,
        "dataset": dataset,
        "parent_candidate": parent_candidate,
        "child_candidate": child_candidate,
        "parent_materialized_candidate": parent_materialized,
        "child_materialized_candidate": child_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "target_family": target_family,
        "search_space": search_space,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "benchmark_result": result,
    }


def build_tool_guidance_benchmark_example_payload() -> Dict[str, object]:
    example = build_tool_guidance_benchmark_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "parent_candidate": example["parent_candidate"].to_dict(),
        "child_candidate": example["child_candidate"].to_dict(),
        "parent_materialized_candidate": example["parent_materialized_candidate"].to_dict(),
        "child_materialized_candidate": example["child_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "target_family": example["target_family"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
    }


def build_coding_overlay_benchmark_example() -> Dict[str, object]:
    """Build a V1.5 benchmark/comparison example for bounded coding-harness overlays."""

    target = OptimizationTarget(
        target_id="target.codex_dossier.coding_overlay.v1_5",
        target_kind="coding_harness_overlay",
        baseline_artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        mutable_loci=[
            MutableLocus(
                locus_id="prompt.section.planning_policy",
                locus_kind="planning_policy",
                selector="developer_prompt.planning_policy",
                artifact_ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_apply_patch": True},
            ),
            MutableLocus(
                locus_id="prompt.section.editing_policy",
                locus_kind="editing_policy",
                selector="developer_prompt.editing_constraints",
                artifact_ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_narrow_diffs": True},
            ),
        ],
        support_envelope=SupportEnvelope(
            tools=["exec_command", "apply_patch", "spawn_agent"],
            execution_profiles=["codex-e4", "replay-safe"],
            environments=["workspace-write"],
            providers=["openai"],
            models=["gpt-5.2"],
            assumptions={"requires_replay_gate": True, "forbids_support_widening": True},
        ),
        invariants=[
            OptimizationInvariant("bounded-overlay-only", "candidate may only modify declared loci"),
            OptimizationInvariant("support-envelope-preserved", "candidate may not silently widen support claims"),
        ],
        metadata={"lane": "coding_overlay", "phase": "v1_5"},
    )

    rationale = CorrectnessRationale(
        rationale_id="rat.coding_overlay.v1_5",
        summary="Coding overlays should improve task execution discipline without widening tools or weakening diff hygiene.",
        explanation=(
            "The optimized overlay should improve planning/editing behavior on repo tasks while preserving apply_patch discipline, "
            "narrow diff expectations, and replay-safe bounded overlays."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
            )
        ],
        acceptance_clauses=[
            "Planning guidance should reduce unnecessary broad edits.",
            "Editing guidance should preserve apply_patch and narrow-diff discipline.",
        ],
        forbidden_behavior_clauses=[
            "Do not imply whole-file rewrites or unsupported tools.",
            "Do not weaken replay-safe or bounded-overlay expectations.",
        ],
        metadata={"lane": "coding_overlay"},
    )

    def _sample(sample_id: str, prompt_input: str, split_hint: str, bucket_tags: list[str]) -> OptimizationSample:
        return OptimizationSample(
            sample_id=sample_id,
            target_id=target.target_id,
            prompt_input=prompt_input,
            environment_requirements={"workspace_mode": "workspace-write", "requires_replay": True},
            bound_tool_requirements=["exec_command", "apply_patch", "spawn_agent"],
            environment_selector=EnvironmentSelector(
                selector_id=f"env.{sample_id}",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write", "replay-safe"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id=f"toolpack.{sample_id}",
                profile="codex-dossier-default",
                required_tools=[
                    ToolRequirement("exec_command"),
                    ToolRequirement("apply_patch"),
                    ToolRequirement("spawn_agent"),
                ],
            ),
            sandbox_context=SandboxContextRequirement(
                sandbox_profile="workspace-default",
                filesystem_mode="workspace-write",
                network_access=False,
                required_capabilities=["workspace-write"],
            ),
            expected_checks=["narrow_diff_bias_preserved", "apply_patch_preserved", "planning_quality_improved"],
            ground_truth_package=GroundTruthPackage(
                package_id=f"gt.{sample_id}",
                oracle_kind="coding_overlay_policy",
                expected_result={
                    "must_preserve_apply_patch": True,
                    "must_preserve_narrow_diff_bias": True,
                    "must_improve_planning_quality": True,
                },
                rationale_refs=[rationale.rationale_id],
                executable_checks=[
                    {"kind": "apply_patch_required", "required": True},
                    {"kind": "narrow_diff_bias", "must_be_positive": True},
                ],
                metadata={"bucket_tags": list(bucket_tags)},
            ),
            metadata={"split_hint": split_hint, "bucket_tags": list(bucket_tags)},
        )

    train_sample = _sample(
        "sample.coding_overlay.train.001",
        "Fix a small bug in a repo task using bounded edits and explicit planning.",
        "train",
        ["repo-task", "small-edit"],
    )
    validation_sample = _sample(
        "sample.coding_overlay.validation.001",
        "Adjust one runtime behavior without broad file churn or whole-file rewrites.",
        "validation",
        ["runtime-task", "bounded-edit"],
    )
    hold_sample = _sample(
        "sample.coding_overlay.hold.001",
        "Catch candidates that improve success rate only by implying broader edit blast radius.",
        "hold",
        ["hidden-hold", "blast-radius"],
    )
    regression_sample = _sample(
        "sample.coding_overlay.regression.001",
        "Preserve apply_patch-centered editing discipline on legacy repo tasks.",
        "regression",
        ["regression", "apply-patch"],
    )

    dataset = OptimizationDataset(
        dataset_id="dataset.coding_overlay.v1_5",
        dataset_version="2026-03-13.v1_5",
        samples=[train_sample, validation_sample, hold_sample, regression_sample],
        split_definitions={
            "train": [train_sample.sample_id],
            "validation": [validation_sample.sample_id],
            "hold": [hold_sample.sample_id],
            "regression": [regression_sample.sample_id],
        },
        scope_notes=[
            "This benchmark pack targets bounded coding-harness overlays on repo tasks.",
            "The hidden hold exists to catch fake wins that only work by implying broader edits.",
        ],
        reproducibility_metadata={"target_family": "coding_overlay", "phase": "v1_5"},
        dataset_runtime_context=OptimizationRuntimeContext(
            context_id="context.dataset.coding_overlay.v1_5",
            environment_selector=EnvironmentSelector(
                selector_id="env.dataset.coding_overlay",
                environment_kind="workspace-write",
                profile="workspace-write",
                required_capabilities=["workspace-write", "replay-safe"],
            ),
            tool_pack_context=ToolPackContext(
                pack_id="toolpack.dataset.coding_overlay",
                profile="codex-dossier-default",
                required_tools=[
                    ToolRequirement("exec_command"),
                    ToolRequirement("apply_patch"),
                    ToolRequirement("spawn_agent"),
                ],
            ),
        ),
        rationale_catalog=[rationale],
        metadata={"lane": "coding_overlay", "phase": "v1_5"},
    )

    parent_candidate = CandidateBundle(
        candidate_id="cand.coding_overlay.parent.001",
        source_target_id=target.target_id,
        applied_loci=["prompt.section.planning_policy", "prompt.section.editing_policy"],
        changes=[
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={"text": "Plan before editing when helpful."},
                rationale="Current planning guidance is too weakly bounded.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={"text": "Edit carefully and prefer minimal change where possible."},
                rationale="Current editing guidance does not strongly insist on apply_patch and narrow diffs.",
            ),
        ],
        provenance={"kind": "baseline_lane_candidate"},
        metadata={"lane": "coding_overlay", "role": "parent"},
    )
    child_candidate = CandidateBundle(
        candidate_id="cand.coding_overlay.child.001",
        source_target_id=target.target_id,
        applied_loci=["prompt.section.planning_policy", "prompt.section.editing_policy"],
        changes=[
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={"text": "Sketch the smallest viable plan first, then execute only the bounded edits required."},
                rationale="Makes the small-plan-first policy explicit.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={"text": "Prefer apply_patch and narrow diffs; avoid broad rewrites unless evidence clearly requires them."},
                rationale="Makes narrow diff and apply_patch discipline explicit.",
            ),
        ],
        provenance={"kind": "reflective_child_candidate"},
        metadata={"lane": "coding_overlay", "role": "child"},
    )

    parent_materialized = materialize_candidate(
        target,
        parent_candidate,
        effective_artifact={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml", "overlay": {}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 2},
        metadata={"lane": "coding_overlay", "role": "parent"},
    )
    child_materialized = materialize_candidate(
        target,
        child_candidate,
        effective_artifact={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml", "overlay": {}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 2},
        metadata={"lane": "coding_overlay", "role": "child"},
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.coding_overlay.v2",
        suite_kind="coding_overlay_family",
        evaluator_stack=["coding_overlay_checker.v1", "diff_hygiene_checker.v1"],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1},
        capture_requirements=[
            "planning_quality_delta",
            "narrow_diff_bias_delta",
            "apply_patch_preserved",
            "held_out_blast_radius_guard",
        ],
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_parent_child.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "benchmark_summary_json"],
        metadata={"lane": "coding_overlay", "phase": "v2"},
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.coding_overlay.v2",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "planning_quality": {
                "direction": "maximize",
                "source_metric": "planning_quality_delta",
                "promotion_sensitive": True,
            },
            "diff_hygiene": {
                "direction": "maximize",
                "source_metric": "narrow_diff_bias_delta",
                "promotion_sensitive": True,
            },
            "mutation_cost": {
                "direction": "minimize",
                "source_metric": "cost_delta_usd",
                "promotion_sensitive": False,
            },
        },
        penalties={
            "blast_radius_expansion": {
                "kind": "hard_block",
                "reason": "coding overlay family may not imply broader edit blast radius",
            },
            "apply_patch_regression": {
                "kind": "hard_block",
                "reason": "apply_patch-centered editing discipline must remain explicit",
            },
        },
        aggregation_rules={
            "per_sample": "weighted_sum",
            "per_bucket": "minimum_bucket_floor",
            "global": "bounded_edit_first",
        },
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
        },
        frontier_dimensions=["planning_quality", "diff_hygiene", "mutation_cost"],
        promotion_annotations={"requires_support_sensitive_review": False, "review_class": "coding_overlay"},
        visibility_annotations={"hidden_hold_channels": ["planning_quality", "diff_hygiene"]},
        metadata={"lane": "coding_overlay", "phase": "v2"},
    )

    target_family = TargetFamilyManifest(
        family_id="family.coding_overlay.v2",
        family_kind="coding_harness_overlay_family",
        target_ids=[target.target_id],
        family_scope="Planning and editing policy overlays for bounded coding-harness tasks.",
        mutable_loci_ids=[locus.locus_id for locus in target.mutable_loci],
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        review_class="coding_overlay",
        runtime_context_assumptions={
            "environment_selector_profile": "workspace-write",
            "tool_pack_profile": "codex-dossier-default",
            "requires_replay_safe_lane": True,
            "requires_apply_patch_tool": True,
        },
        promotion_class="bounded_coding_overlay_change",
        artifact_refs=[
            ArtifactRef(
                ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier"},
            )
        ],
        metadata={"lane": "coding_overlay", "phase": "v2"},
    )

    search_space = SearchSpaceManifest(
        search_space_id="searchspace.coding_overlay.v2",
        family_id=target_family.family_id,
        allowed_loci=[locus.locus_id for locus in target.mutable_loci],
        mutation_kinds_by_locus={
            "prompt.section.planning_policy": ["replace"],
            "prompt.section.editing_policy": ["replace"],
        },
        value_domains_by_locus={
            "prompt.section.planning_policy": {
                "must_preserve_apply_patch": True,
                "must_reference_bounded_edits": True,
            },
            "prompt.section.editing_policy": {
                "must_preserve_narrow_diffs": True,
                "must_discourage_broad_rewrites": True,
            },
        },
        semantic_constraints={
            "prompt.section.planning_policy": {
                "must_not_expand_edit_scope": True,
            },
            "prompt.section.editing_policy": {
                "must_keep_apply_patch_explicit": True,
                "must_keep_blast_radius_bounded": True,
            },
        },
        invariants=["bounded-overlay-only", "support-envelope-preserved"],
        unsafe_expansion_notes=[
            "Changing tool surfaces is out of scope for this family.",
            "Whole-file rewrite guidance is out of scope for this family.",
        ],
        metadata={"lane": "coding_overlay", "phase": "v2"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.coding_overlay.v1_5",
        benchmark_kind="coding_overlay_pack",
        target_id=target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=parent_candidate.candidate_id,
        environment_domain="workspace-write/replay-safe",
        evaluator_stack=["coding_overlay_checker.v1", "diff_hygiene_checker.v1"],
        comparison_protocol="paired_parent_child.v1",
        splits=[
            BenchmarkSplit("train", [train_sample.sample_id], "mutation_visible"),
            BenchmarkSplit("validation", [validation_sample.sample_id], "comparison_visible"),
            BenchmarkSplit("hold", [hold_sample.sample_id], "hidden_hold"),
            BenchmarkSplit("regression", [regression_sample.sample_id], "comparison_visible"),
        ],
        bucket_tags={
            train_sample.sample_id: ["repo-task", "small-edit"],
            validation_sample.sample_id: ["runtime-task", "bounded-edit"],
            hold_sample.sample_id: ["hidden-hold", "blast-radius"],
            regression_sample.sample_id: ["regression", "apply-patch"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1},
        contamination_notes=[
            "Hold split should stay hidden at mutation time so fake broad-rewrite wins are caught honestly.",
        ],
        promotion_relevance={"requires_support_sensitive_review": False},
        artifact_refs=[ArtifactRef(ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml", media_type="text/yaml")],
        metadata={
            "lane": "coding_overlay",
            "phase": "v1_5",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.coding_overlay.parent_vs_child.001",
        parent_candidate_id=parent_candidate.candidate_id,
        child_candidate_id=child_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=[hold_sample.sample_id],
        trial_count=1,
        rationale="The child improves bounded planning/editing behavior and wins on the hidden hold without implying broader rewrites.",
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/coding_overlay/paired_parent_child_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "planning_quality_delta": 0.3,
            "narrow_diff_bias_delta": 0.5,
            "held_out_blast_radius_guard": True,
        },
        better_candidate_id=child_candidate.candidate_id,
        metadata={"lane": "coding_overlay"},
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.coding_overlay.child.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=child_candidate.candidate_id,
        per_sample_components={
            train_sample.sample_id: {
                "planning_quality": 0.8,
                "diff_hygiene": 0.9,
                "mutation_cost": 0.0,
            },
            validation_sample.sample_id: {
                "planning_quality": 0.75,
                "diff_hygiene": 0.85,
                "mutation_cost": 0.0,
            },
            hold_sample.sample_id: {
                "planning_quality": 0.7,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
            regression_sample.sample_id: {
                "planning_quality": 0.68,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "small-edit": {"planning_quality": 0.8, "diff_hygiene": 0.9},
            "blast-radius": {"planning_quality": 0.7, "diff_hygiene": 1.0},
        },
        aggregate_objectives={
            "planning_quality": 0.7325,
            "diff_hygiene": 0.9375,
            "mutation_cost": 0.0,
            "eligible_for_promotion": True,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 1,
            "blocked_for_uncertainty": False,
        },
        blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/coding_overlay/objective_breakdown_child.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "coding_overlay",
            "family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    result = BenchmarkRunResult(
        run_id="benchmark_run.coding_overlay.001",
        manifest_id=manifest.manifest_id,
        candidate_ids=[parent_candidate.candidate_id, child_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={"parent_planning_score": 0.44, "child_planning_score": 0.79},
        bucket_outcomes={"small-edit": {"outcome": "child_win"}, "blast-radius": {"outcome": "child_win"}},
        variance_summary={"trial_count": 1, "stochasticity_class": "deterministic"},
        cost_support_evidence_slices={"apply_patch_preserved": True, "support_envelope_preserved": True},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/coding_overlay/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "eligible_for_promotion": True,
            "requires_review": False,
            "objective_breakdown_result_id": objective_breakdown.result_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
        },
        metadata={
            "lane": "coding_overlay",
            "phase": "v1_5",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "target_family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    return {
        "target": target,
        "dataset": dataset,
        "parent_candidate": parent_candidate,
        "child_candidate": child_candidate,
        "parent_materialized_candidate": parent_materialized,
        "child_materialized_candidate": child_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "target_family": target_family,
        "search_space": search_space,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "benchmark_result": result,
    }


def build_coding_overlay_benchmark_example_payload() -> Dict[str, object]:
    example = build_coding_overlay_benchmark_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "parent_candidate": example["parent_candidate"].to_dict(),
        "child_candidate": example["child_candidate"].to_dict(),
        "parent_materialized_candidate": example["parent_materialized_candidate"].to_dict(),
        "child_materialized_candidate": example["child_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "target_family": example["target_family"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
    }


def build_coding_overlay_verifier_experiment_example() -> Dict[str, object]:
    """Build a narrow verifier-augmented refinement experiment over the live coding-overlay family."""

    example = build_coding_overlay_benchmark_example()
    child_candidate = example["child_candidate"]
    manifest = example["manifest"]
    evaluation_suite = example["evaluation_suite"]
    objective_suite = example["objective_suite"]
    target_family = example["target_family"]
    search_space = example["search_space"]
    assert isinstance(child_candidate, CandidateBundle)
    assert isinstance(manifest, BenchmarkRunManifest)
    assert isinstance(evaluation_suite, EvaluationSuiteManifest)
    assert isinstance(objective_suite, ObjectiveSuiteManifest)
    assert isinstance(target_family, TargetFamilyManifest)
    assert isinstance(search_space, SearchSpaceManifest)

    refined_candidate = CandidateBundle(
        candidate_id="cand.coding_overlay.verifier_refined.001",
        source_target_id=child_candidate.source_target_id,
        applied_loci=["prompt.section.editing_policy"],
        changes=[
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={
                    "text": "Prefer apply_patch and narrow diffs; verify bounded edit scope against diff-hygiene checks before broader rewrites."
                },
                rationale="Makes the diff-hygiene verifier loop explicit without widening the coding overlay family scope.",
            )
        ],
        provenance={
            "kind": "verifier_augmented_refinement",
            "base_candidate_id": child_candidate.candidate_id,
        },
        metadata={"lane": "coding_overlay", "role": "verifier_refined"},
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.coding_overlay.child_vs_verifier_refined.001",
        parent_candidate_id=child_candidate.candidate_id,
        child_candidate_id=refined_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The verifier-augmented refinement preserves bounded-edit discipline and improves hidden-hold diff hygiene without expanding the family search space.",
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/coding_overlay/verifier_refinement_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "planning_quality_delta": 0.02,
            "diff_hygiene_delta": 0.09,
            "held_out_blast_radius_guard": True,
            "verifier_confirmed": True,
        },
        better_candidate_id=refined_candidate.candidate_id,
        metadata={
            "lane": "coding_overlay",
            "experiment_kind": "verifier_augmented_refinement",
            "family_id": target_family.family_id,
        },
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.coding_overlay.verifier_refined.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=refined_candidate.candidate_id,
        per_sample_components={
            "sample.coding_overlay.train.001": {
                "planning_quality": 0.81,
                "diff_hygiene": 0.96,
                "mutation_cost": 0.0,
            },
            "sample.coding_overlay.validation.001": {
                "planning_quality": 0.75,
                "diff_hygiene": 0.94,
                "mutation_cost": 0.0,
            },
            "sample.coding_overlay.hold.001": {
                "planning_quality": 0.72,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
            "sample.coding_overlay.regression.001": {
                "planning_quality": 0.69,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "small-edit": {"planning_quality": 0.81, "diff_hygiene": 0.96},
            "blast-radius": {"planning_quality": 0.72, "diff_hygiene": 1.0},
        },
        aggregate_objectives={
            "planning_quality": 0.7425,
            "diff_hygiene": 0.975,
            "mutation_cost": 0.0,
            "eligible_for_promotion": True,
        },
        uncertainty_summary={
            "stochasticity_class": evaluation_suite.stochasticity_class,
            "trial_count": 1,
            "blocked_for_uncertainty": False,
        },
        blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/coding_overlay/objective_breakdown_verifier_refined.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "coding_overlay",
            "family_id": target_family.family_id,
            "search_space_id": search_space.search_space_id,
            "experiment_kind": "verifier_augmented_refinement",
        },
    )

    experiment = VerifierAugmentedExperimentResult(
        experiment_id="verifier_experiment.coding_overlay.v2",
        experiment_kind="verifier_augmented_refinement",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        target_family_id=target_family.family_id,
        search_space_id=search_space.search_space_id,
        baseline_candidate_id=child_candidate.candidate_id,
        refined_candidate_id=refined_candidate.candidate_id,
        verifier_stack=[
            *evaluation_suite.evaluator_stack,
            "bounded_edit_scope_verifier.v1",
        ],
        focus_sample_ids=manifest.hidden_hold_sample_ids() or manifest.sample_ids(),
        comparison_result_id=comparison.comparison_id,
        objective_breakdown_result_id=objective_breakdown.result_id,
        outcome="accepted",
        rationale="Verifier-guided refinement is kept inside the coding-overlay family and uses the declared suite and search-space constraints to improve hidden-hold diff hygiene.",
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/coding_overlay/verifier_experiment_summary.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "coding_overlay",
            "phase": "v2",
            "backend_only": True,
            "non_kernel": True,
            "darwin_boundary": "not_reopened",
            "family_bound": True,
        },
    )

    return {
        "family_example": example,
        "refined_candidate": refined_candidate,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "verifier_experiment": experiment,
    }


def build_coding_overlay_verifier_experiment_example_payload() -> Dict[str, object]:
    example = build_coding_overlay_verifier_experiment_example()
    return {
        "family_example": {
            "evaluation_suite": example["family_example"]["evaluation_suite"].to_dict(),
            "objective_suite": example["family_example"]["objective_suite"].to_dict(),
            "target_family": example["family_example"]["target_family"].to_dict(),
            "search_space": example["family_example"]["search_space"].to_dict(),
            "manifest": example["family_example"]["manifest"].to_dict(),
            "child_candidate": example["family_example"]["child_candidate"].to_dict(),
        },
        "refined_candidate": example["refined_candidate"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "verifier_experiment": example["verifier_experiment"].to_dict(),
    }


def _clone_sample_for_composed_target(
    sample: OptimizationSample,
    *,
    sample_id: str,
    target_id: str,
    prompt_input: str,
    expected_checks: List[str],
) -> OptimizationSample:
    payload = sample.to_dict()
    payload["sample_id"] = sample_id
    payload["target_id"] = target_id
    payload["prompt_input"] = prompt_input
    payload["expected_checks"] = expected_checks
    return OptimizationSample.from_dict(payload)


def _clone_sample_for_package_target(
    sample: OptimizationSample,
    *,
    sample_id: str,
    target_id: str,
    prompt_input: str,
    expected_checks: List[str],
    tool_pack_profile: str,
    required_tools: List[str],
    environment_profile: str,
    model_policy: str,
) -> OptimizationSample:
    payload = sample.to_dict()
    payload["sample_id"] = sample_id
    payload["target_id"] = target_id
    payload["prompt_input"] = prompt_input
    payload["expected_checks"] = expected_checks
    payload["bound_tool_requirements"] = list(required_tools)
    payload["service_contexts"] = []
    payload["mcp_context"] = None

    environment_requirements = dict(payload.get("environment_requirements") or {})
    environment_requirements["workspace_mode"] = environment_profile
    environment_requirements["tool_pack_profile"] = tool_pack_profile
    payload["environment_requirements"] = environment_requirements

    if payload.get("environment_selector"):
        payload["environment_selector"] = {
            **dict(payload["environment_selector"]),
            "environment_kind": environment_profile,
            "profile": environment_profile,
            "required_capabilities": [environment_profile, "replay-safe"],
        }
    if payload.get("tool_pack_context"):
        payload["tool_pack_context"] = {
            **dict(payload["tool_pack_context"]),
            "pack_id": f"toolpack.{sample_id}",
            "profile": tool_pack_profile,
            "required_tools": [{"tool_name": tool_name} for tool_name in required_tools],
            "metadata": {
                **dict(payload["tool_pack_context"].get("metadata") or {}),
                "package_lane": True,
                "model_policy": model_policy,
            },
        }
    payload["metadata"] = {
        **dict(payload.get("metadata") or {}),
        "tool_pack_profile": tool_pack_profile,
        "model_policy": model_policy,
    }
    return OptimizationSample.from_dict(payload)


def build_tool_guidance_coding_overlay_composition_example() -> Dict[str, object]:
    """Build the first low-risk V3 composed-family lane over the Codex dossier package."""

    tool = build_tool_guidance_benchmark_example()
    coding = build_coding_overlay_benchmark_example()
    tool_target = tool["target"]
    coding_target = coding["target"]
    tool_family = tool["target_family"]
    coding_family = coding["target_family"]
    assert isinstance(tool_target, OptimizationTarget)
    assert isinstance(coding_target, OptimizationTarget)
    assert isinstance(tool_family, TargetFamilyManifest)
    assert isinstance(coding_family, TargetFamilyManifest)

    composed_target = OptimizationTarget(
        target_id="target.codex_dossier.tool_guidance_coding_overlay.v3",
        target_kind="agent_config_overlay_package",
        baseline_artifact_refs=list(tool_target.baseline_artifact_refs),
        mutable_loci=list(tool_target.mutable_loci) + list(coding_target.mutable_loci),
        support_envelope=tool_target.support_envelope,
        invariants=list(tool_target.invariants),
        metadata={
            "lane": "tool_guidance_coding_overlay_composed",
            "phase": "v3",
            "member_family_ids": [tool_family.family_id, coding_family.family_id],
        },
    )

    tool_train = tool["dataset"].samples[0]
    coding_validation = coding["dataset"].samples[1]
    tool_hold = tool["dataset"].samples[2]
    coding_regression = coding["dataset"].samples[3]

    dataset = OptimizationDataset(
        dataset_id="dataset.tool_guidance_coding_overlay.v3",
        dataset_version="2026-03-19.v3",
        samples=[
            _clone_sample_for_composed_target(
                tool_train,
                sample_id="sample.tool_guidance_coding_overlay.train.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Clarify exec_command guidance while keeping bounded-edit and apply_patch discipline explicit."
                ),
                expected_checks=[
                    "clarity_improved",
                    "support_envelope_preserved",
                    "narrow_diff_bias_preserved",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                coding_validation,
                sample_id="sample.tool_guidance_coding_overlay.validation.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Improve planning and editing guidance without weakening tool-surface boundaries or replay-safe wording."
                ),
                expected_checks=[
                    "clarity_improved",
                    "planning_quality_improved",
                    "replay_guidance_preserved",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                tool_hold,
                sample_id="sample.tool_guidance_coding_overlay.hold.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Catch candidates that sound clearer but quietly imply broader capability or looser edit scope."
                ),
                expected_checks=[
                    "clarity_improved",
                    "support_envelope_preserved",
                    "held_out_blast_radius_guard",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                coding_regression,
                sample_id="sample.tool_guidance_coding_overlay.regression.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Preserve apply_patch-centered editing discipline and bounded overlay language on legacy repo tasks."
                ),
                expected_checks=[
                    "clarity_improved",
                    "narrow_diff_bias_preserved",
                    "apply_patch_preserved",
                    "replay_guidance_preserved",
                ],
            ),
        ],
        rationale_catalog=list(tool["dataset"].rationale_catalog) + list(coding["dataset"].rationale_catalog),
        metadata={"lane": "tool_guidance_coding_overlay_composed", "phase": "v3"},
    )

    atomic_candidate = CandidateBundle(
        candidate_id="cand.tool_guidance_coding_overlay.atomic_union.001",
        source_target_id=composed_target.target_id,
        applied_loci=[
            "tool.render.exec_command",
            "prompt.section.optimization_guidance",
            "prompt.section.planning_policy",
            "prompt.section.editing_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={"description": tool["child_candidate"].changes[0].value["description"]},
                rationale="Carry forward the atomic tool-guidance improvement.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={"text": tool["child_candidate"].changes[1].value["text"]},
                rationale="Carry forward the atomic optimization-guidance improvement.",
            ),
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={"text": coding["child_candidate"].changes[0].value["text"]},
                rationale="Carry forward the atomic planning-policy improvement.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={"text": coding["child_candidate"].changes[1].value["text"]},
                rationale="Carry forward the atomic editing-policy improvement.",
            ),
        ],
        provenance={
            "kind": "atomic_family_union",
            "member_family_ids": [tool_family.family_id, coding_family.family_id],
        },
        metadata={"lane": "tool_guidance_coding_overlay_composed", "role": "atomic_union"},
    )

    composed_candidate = CandidateBundle(
        candidate_id="cand.tool_guidance_coding_overlay.composed.001",
        source_target_id=composed_target.target_id,
        applied_loci=[
            "tool.render.exec_command",
            "prompt.section.optimization_guidance",
            "prompt.section.planning_policy",
            "prompt.section.editing_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={
                    "description": "Run shell commands inside the declared support envelope, then summarize results clearly without implying broader edit or execution scope."
                },
                rationale="Compose clarity with bounded-edit constraints instead of optimizing tool wording in isolation.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={
                    "text": "Prefer narrow overlays, preserve replay-safe behavior, and keep tool and editing guidance aligned around bounded changes."
                },
                rationale="Link optimization guidance to the bounded-edit doctrine explicitly.",
            ),
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={
                    "text": "Sketch the smallest viable plan first, especially when tool wording and edit scope interact."
                },
                rationale="Tie planning policy to the composed tool+editing surface.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={
                    "text": "Prefer apply_patch and narrow diffs; do not let clearer tool guidance imply broader rewrites or looser support boundaries."
                },
                rationale="Make the cross-family bounded-edit invariant explicit.",
            ),
        ],
        provenance={
            "kind": "family_composition_child_candidate",
            "member_family_ids": [tool_family.family_id, coding_family.family_id],
            "composition_kind": "joint",
        },
        metadata={"lane": "tool_guidance_coding_overlay_composed", "role": "composed_child"},
    )

    atomic_materialized = materialize_candidate(
        composed_target,
        atomic_candidate,
        effective_artifact={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml", "overlay": {}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 3},
        metadata={"lane": "tool_guidance_coding_overlay_composed", "role": "atomic_union"},
    )
    composed_materialized = materialize_candidate(
        composed_target,
        composed_candidate,
        effective_artifact={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml", "overlay": {}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 3},
        metadata={"lane": "tool_guidance_coding_overlay_composed", "role": "composed_child"},
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.tool_guidance_coding_overlay.v3",
        suite_kind="composed_tool_guidance_coding_overlay_family",
        evaluator_stack=[
            "tool_guidance_checker.v1",
            "coding_overlay_checker.v1",
            "diff_hygiene_checker.v1",
            "replay_guard_checker.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "model_policy": "gpt-5.4-nano"},
        capture_requirements=[
            "clarity_improved",
            "planning_quality_improved",
            "apply_patch_preserved",
            "support_envelope_preserved",
        ],
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "model_policy": "nano_first",
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_atomic_vs_composed.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "benchmark_summary_json"],
        metadata={
            "lane": "tool_guidance_coding_overlay_composed",
            "phase": "v3",
            "model_policy": "nano_only",
        },
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.tool_guidance_coding_overlay.v3",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "tool_clarity": {"direction": "maximize", "source_metric": "clarity_improved", "promotion_sensitive": True},
            "planning_quality": {"direction": "maximize", "source_metric": "planning_quality_improved", "promotion_sensitive": True},
            "diff_hygiene": {"direction": "maximize", "source_metric": "apply_patch_preserved", "promotion_sensitive": True},
            "mutation_cost": {"direction": "minimize", "source_metric": "cost_delta_usd", "promotion_sensitive": False},
        },
        penalties={
            "support_scope_drift": {"kind": "hard_block", "reason": "tool clarity may not imply wider support scope"},
            "broad_rewrite_drift": {"kind": "hard_block", "reason": "composed family may not weaken bounded-edit doctrine"},
        },
        aggregation_rules={"per_sample": "weighted_sum", "global": "bounded_composed_first"},
        uncertainty_policy={"stochasticity_class": "deterministic", "blocked_when_missing_hidden_hold": True},
        frontier_dimensions=["tool_clarity", "planning_quality", "diff_hygiene", "mutation_cost"],
        promotion_annotations={"requires_support_sensitive_review": False, "review_class": "composed_guidance_overlay"},
        visibility_annotations={"hidden_hold_channels": ["tool_clarity", "planning_quality", "diff_hygiene"]},
        metadata={"lane": "tool_guidance_coding_overlay_composed", "phase": "v3"},
    )

    composition = FamilyCompositionManifest(
        composition_id="composition.tool_guidance_coding_overlay.v3",
        member_family_ids=[tool_family.family_id, coding_family.family_id],
        composition_kind="joint",
        shared_target_scope="codex_dossier_package",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        search_space_id="searchspace.tool_guidance_coding_overlay.v3",
        review_class="composed_guidance_overlay",
        promotion_class="bounded_composed_dossier_change",
        applicability_scope={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "tool_pack_profile": "codex-dossier-default",
            "environment_profile": "workspace-write",
        },
        cross_family_invariants=[
            "bounded-overlay-only",
            "support-envelope-preserved",
            "tool-clarity-may-not-imply-broader-edit-scope",
        ],
        runtime_context_requirements={
            "requires_apply_patch_tool": True,
            "requires_replay_safe_lane": True,
            "model_policy": "nano_only",
        },
        metadata={"lane": "tool_guidance_coding_overlay_composed", "phase": "v3"},
    )

    search_space = SearchSpaceManifest(
        search_space_id=composition.search_space_id,
        composition_id=composition.composition_id,
        allowed_loci=[locus.locus_id for locus in composed_target.mutable_loci],
        mutation_kinds_by_locus={
            "tool.render.exec_command": ["replace"],
            "prompt.section.optimization_guidance": ["replace"],
            "prompt.section.planning_policy": ["replace"],
            "prompt.section.editing_policy": ["replace"],
        },
        value_domains_by_locus={
            "tool.render.exec_command": {"must_preserve_support_boundaries": True, "must_preserve_bounded_edits": True},
            "prompt.section.optimization_guidance": {"must_reference_replay_safe": True, "must_reference_bounded_changes": True},
            "prompt.section.planning_policy": {"must_remain_small_plan_first": True},
            "prompt.section.editing_policy": {"must_preserve_apply_patch": True, "must_discourage_broad_rewrites": True},
        },
        semantic_constraints={
            "tool.render.exec_command": {"must_not_imply_new_tools": True},
            "prompt.section.editing_policy": {"must_keep_blast_radius_bounded": True},
        },
        coupled_loci_groups={
            "tool_and_editing": ["tool.render.exec_command", "prompt.section.editing_policy"],
            "guidance_and_planning": ["prompt.section.optimization_guidance", "prompt.section.planning_policy"],
        },
        stage_partitions={
            "seed_guidance_pair": ["tool.render.exec_command", "prompt.section.optimization_guidance"],
            "expand_edit_pair": ["prompt.section.planning_policy", "prompt.section.editing_policy"],
        },
        cross_family_constraints={
            "tool_guidance_coding_overlay": {
                "member_family_ids": [tool_family.family_id, coding_family.family_id],
                "must_preserve_apply_patch_if_tool_clarity_changes": True,
                "must_preserve_support_boundaries_if_editing_policy_changes": True,
            }
        },
        invariants=["bounded-overlay-only", "support-envelope-preserved"],
        unsafe_expansion_notes=[
            "Changing tool surfaces beyond exec_command wording is out of scope.",
            "Whole-file rewrite guidance is out of scope.",
        ],
        metadata={"lane": "tool_guidance_coding_overlay_composed", "phase": "v3"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.tool_guidance_coding_overlay.v3",
        benchmark_kind="tool_guidance_coding_overlay_composed_pack",
        target_id=composed_target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=atomic_candidate.candidate_id,
        environment_domain="workspace-write/replay-safe",
        evaluator_stack=list(evaluation_suite.evaluator_stack),
        comparison_protocol="paired_atomic_vs_composed.v1",
        splits=[
            BenchmarkSplit("train", ["sample.tool_guidance_coding_overlay.train.001"], "mutation_visible"),
            BenchmarkSplit("validation", ["sample.tool_guidance_coding_overlay.validation.001"], "comparison_visible"),
            BenchmarkSplit("hold", ["sample.tool_guidance_coding_overlay.hold.001"], "hidden_hold"),
            BenchmarkSplit("regression", ["sample.tool_guidance_coding_overlay.regression.001"], "comparison_visible"),
        ],
        bucket_tags={
            "sample.tool_guidance_coding_overlay.train.001": ["tool-clarity", "bounded-edit"],
            "sample.tool_guidance_coding_overlay.validation.001": ["planning", "replay-safe"],
            "sample.tool_guidance_coding_overlay.hold.001": ["hidden-hold", "scope-boundary"],
            "sample.tool_guidance_coding_overlay.regression.001": ["regression", "apply-patch"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "model_policy": "gpt-5.4-nano"},
        contamination_notes=[
            "Composition search stays Nano-only in the first tranche and may not use hidden-hold details at mutation time."
        ],
        promotion_relevance={"requires_support_sensitive_review": False, "composition_id": composition.composition_id},
        artifact_refs=[ArtifactRef(ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml", media_type="text/yaml")],
        metadata={
            "lane": "tool_guidance_coding_overlay_composed",
            "phase": "v3",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_only",
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.tool_guidance_coding_overlay.atomic_vs_composed.001",
        parent_candidate_id=atomic_candidate.candidate_id,
        child_candidate_id=composed_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The composed candidate improves tool clarity and bounded-edit planning jointly without widening support scope or edit blast radius.",
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/tool_guidance_coding_overlay/paired_atomic_vs_composed_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "tool_clarity_delta": 0.18,
            "planning_quality_delta": 0.11,
            "diff_hygiene_delta": 0.06,
            "held_out_scope_guard": True,
        },
        better_candidate_id=composed_candidate.candidate_id,
        metadata={
            "lane": "tool_guidance_coding_overlay_composed",
            "composition_id": composition.composition_id,
            "baseline_kind": "atomic_family_union",
            "model_policy": "nano_only",
        },
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.tool_guidance_coding_overlay.composed.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=composed_candidate.candidate_id,
        per_sample_components={
            "sample.tool_guidance_coding_overlay.train.001": {"tool_clarity": 0.92, "planning_quality": 0.74, "diff_hygiene": 0.96, "mutation_cost": 0.0},
            "sample.tool_guidance_coding_overlay.validation.001": {"tool_clarity": 0.86, "planning_quality": 0.81, "diff_hygiene": 0.95, "mutation_cost": 0.0},
            "sample.tool_guidance_coding_overlay.hold.001": {"tool_clarity": 0.83, "planning_quality": 0.76, "diff_hygiene": 1.0, "mutation_cost": 0.0},
            "sample.tool_guidance_coding_overlay.regression.001": {"tool_clarity": 0.8, "planning_quality": 0.73, "diff_hygiene": 1.0, "mutation_cost": 0.0},
        },
        per_bucket_components={
            "tool-clarity": {"tool_clarity": 0.92, "diff_hygiene": 0.96},
            "scope-boundary": {"tool_clarity": 0.83, "planning_quality": 0.76, "diff_hygiene": 1.0},
        },
        aggregate_objectives={
            "tool_clarity": 0.8525,
            "planning_quality": 0.76,
            "diff_hygiene": 0.9775,
            "mutation_cost": 0.0,
            "eligible_for_promotion": True,
        },
        uncertainty_summary={"stochasticity_class": "deterministic", "trial_count": 1, "blocked_for_uncertainty": False},
        blocked_components={},
        member_family_breakdowns={
            tool_family.family_id: {"tool_clarity": 0.8525, "support_scope_guard": True},
            coding_family.family_id: {"planning_quality": 0.76, "diff_hygiene": 0.9775},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/tool_guidance_coding_overlay/objective_breakdown_composed.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "tool_guidance_coding_overlay_composed",
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
        },
    )

    result = BenchmarkRunResult(
        run_id="benchmark_run.tool_guidance_coding_overlay.v3",
        manifest_id=manifest.manifest_id,
        candidate_ids=[atomic_candidate.candidate_id, composed_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={"atomic_union_score": 0.71, "composed_score": 0.84},
        bucket_outcomes={"tool-clarity": {"outcome": "child_win"}, "scope-boundary": {"outcome": "child_win"}},
        variance_summary={"trial_count": 1, "stochasticity_class": "deterministic", "model_policy": "nano_only"},
        cost_support_evidence_slices={"nano_only": True, "support_envelope_preserved": True, "apply_patch_preserved": True},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/tool_guidance_coding_overlay/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "eligible_for_promotion": True,
            "requires_review": False,
            "objective_breakdown_result_id": objective_breakdown.result_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "model_policy": "nano_only",
        },
        metadata={
            "lane": "tool_guidance_coding_overlay_composed",
            "phase": "v3",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_only",
        },
    )

    staged_request = StagedOptimizerRequest(
        request_id="staged_request.tool_guidance_coding_overlay.v3",
        backend_request=ReflectiveParetoBackendRequest(
            request_id="backend_request.tool_guidance_coding_overlay.v3",
            target=composed_target,
            baseline_candidate=atomic_candidate,
            baseline_materialized_candidate=atomic_materialized,
            dataset=dataset,
            evaluations=[
                EvaluationRecord(
                    evaluation_id="eval.tool_guidance_coding_overlay.baseline.001",
                    target_id=composed_target.target_id,
                    candidate_id=atomic_candidate.candidate_id,
                    dataset_id=dataset.dataset_id,
                    dataset_version=dataset.dataset_version,
                    sample_id=dataset.samples[0].sample_id,
                    evaluator_id="composed_family_checker.v1",
                    evaluator_version="v3",
                    status="completed",
                    outcome="failed",
                    started_at="2026-03-19T09:00:00.000Z",
                    completed_at="2026-03-19T09:00:01.000Z",
                    duration_ms=1000,
                    raw_evidence_refs=[
                        ArtifactRef(
                            ref="artifacts/optimization/tool_guidance_coding_overlay/baseline_eval.json",
                            media_type="application/json",
                        )
                    ],
                    normalized_diagnostics=[
                        DiagnosticBundle(
                            bundle_id="bundle.eval.tool_guidance_coding_overlay.baseline.001",
                            evaluation_id="eval.tool_guidance_coding_overlay.baseline.001",
                            evaluator_mode="replay",
                            determinism_class="deterministic",
                            entries=[
                                DiagnosticEntry(
                                    diagnostic_id="diag.tool_guidance_coding_overlay.baseline.001",
                                    kind="wrongness",
                                    severity="error",
                                    message="atomic family union still leaves coupled tool/edit guidance under-optimized",
                                    locus_id="prompt.section.editing_policy",
                                    evidence_refs=[
                                        ArtifactRef(
                                            ref="artifacts/optimization/tool_guidance_coding_overlay/diagnostic.json",
                                            media_type="application/json",
                                        )
                                    ],
                                )
                            ],
                            cache_identity={"key": "cache.tool_guidance_coding_overlay.v3", "version": "1"},
                            retry_policy_hint={"max_trials": 1},
                            reproducibility_notes={"family_bound": True, "model_policy": "nano_only"},
                        )
                    ],
                    wrongness_reports=[
                        WrongnessReport(
                            wrongness_id="wrongness.tool_guidance_coding_overlay.001",
                            wrongness_class="correctness.result_mismatch",
                            failure_locus="prompt.section.editing_policy",
                            explanation=(
                                "Atomic family union leaves the cross-family tool-wording and editing-policy coupling unresolved, "
                                "so the composed lane still exposes a bounded repair opportunity."
                            ),
                            confidence=0.85,
                            supporting_evidence_refs=[
                                ArtifactRef(
                                    ref="artifacts/optimization/tool_guidance_coding_overlay/wrongness.json",
                                    media_type="application/json",
                                )
                            ],
                            likely_repair_locus="prompt.section.editing_policy",
                        )
                    ],
                    metadata={"composition_id": composition.composition_id, "model_policy": "nano_only"},
                )
            ],
            active_sample_id=dataset.samples[0].sample_id,
            execution_context=OptimizationExecutionContext(
                target_id=composed_target.target_id,
                sample_id=dataset.samples[0].sample_id,
                runtime_context=dataset.samples[0].runtime_context(),
                evaluation_input_compatibility=atomic_materialized.evaluation_input_compatibility,
                metadata={"composition_id": composition.composition_id, "model_policy": "nano_only"},
            ),
            mutation_bounds=MutationBounds(max_changed_loci=4, max_changed_artifacts=1, max_total_value_bytes=2400),
            max_proposals=3,
            metadata={"family_composition": composition.composition_id, "model_policy": "nano_only"},
        ),
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        search_space=search_space,
        family_composition=composition,
        metadata={"lane": "tool_guidance_coding_overlay_composed", "model_policy": "nano_only"},
    )
    staged_result = run_staged_optimizer(staged_request)

    return {
        "tool_family_example": tool,
        "coding_family_example": coding,
        "target": composed_target,
        "dataset": dataset,
        "atomic_candidate": atomic_candidate,
        "composed_candidate": composed_candidate,
        "atomic_materialized_candidate": atomic_materialized,
        "composed_materialized_candidate": composed_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "family_composition": composition,
        "search_space": search_space,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "benchmark_result": result,
        "staged_request": staged_request,
        "staged_result": staged_result,
    }


def build_tool_guidance_coding_overlay_composition_example_payload() -> Dict[str, object]:
    example = build_tool_guidance_coding_overlay_composition_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "atomic_candidate": example["atomic_candidate"].to_dict(),
        "composed_candidate": example["composed_candidate"].to_dict(),
        "atomic_materialized_candidate": example["atomic_materialized_candidate"].to_dict(),
        "composed_materialized_candidate": example["composed_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "family_composition": example["family_composition"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
        "staged_request": example["staged_request"].to_dict(),
        "staged_result": example["staged_result"].to_dict(),
    }


def build_support_execution_coding_overlay_composition_example() -> Dict[str, object]:
    """Build a bounded E4 dossier prompt+config composition lane with Nano-first cost policy."""

    support = build_support_execution_benchmark_example()
    coding = build_coding_overlay_benchmark_example()
    support_target = support["target"]
    coding_target = coding["target"]
    support_family = support["target_family"]
    coding_family = coding["target_family"]
    assert isinstance(support_target, OptimizationTarget)
    assert isinstance(coding_target, OptimizationTarget)
    assert isinstance(support_family, TargetFamilyManifest)
    assert isinstance(coding_family, TargetFamilyManifest)

    composed_target = OptimizationTarget(
        target_id="target.codex_dossier.support_execution_coding_overlay.v3",
        target_kind="agent_config_overlay_package",
        baseline_artifact_refs=list(support_target.baseline_artifact_refs),
        mutable_loci=list(support_target.mutable_loci) + list(coding_target.mutable_loci),
        support_envelope=SupportEnvelope.from_dict(
            {
                **support_target.support_envelope.to_dict(),
                "assumptions": {
                    **support_target.support_envelope.assumptions,
                    "service_selectors": {"repo_service": "workspace-write"},
                    "mcp_servers": ["beads"],
                },
            }
        ),
        invariants=list(support_target.invariants),
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "phase": "v3",
            "member_family_ids": [support_family.family_id, coding_family.family_id],
        },
    )

    support_train = support["dataset"].samples[0]
    coding_validation = coding["dataset"].samples[1]
    support_hold = support["dataset"].samples[2]
    coding_regression = coding["dataset"].samples[3]

    dataset = OptimizationDataset(
        dataset_id="dataset.support_execution_coding_overlay.v3",
        dataset_version="2026-03-19.v3",
        samples=[
            _clone_sample_for_composed_target(
                support_train,
                sample_id="sample.support_execution_coding_overlay.train.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Tighten support-limited action policy and bounded coding guidance together for a repo task that still requires apply_patch discipline."
                ),
                expected_checks=[
                    "support_honesty_delta",
                    "execution_profile_selection",
                    "planning_quality_improved",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                coding_validation,
                sample_id="sample.support_execution_coding_overlay.validation.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Improve planning and editing guidance while keeping replay-safe execution-profile fallback and support-limited actions explicit."
                ),
                expected_checks=[
                    "support_honesty_delta",
                    "planning_quality_improved",
                    "narrow_diff_bias_preserved",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                support_hold,
                sample_id="sample.support_execution_coding_overlay.hold.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Catch candidates that sound more capable only because they weaken support-limited actions or imply broader edits on support-sensitive work."
                ),
                expected_checks=[
                    "support_honesty_delta",
                    "execution_profile_selection",
                    "hidden_hold_preservation",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                coding_regression,
                sample_id="sample.support_execution_coding_overlay.regression.001",
                target_id=composed_target.target_id,
                prompt_input=(
                    "Preserve apply_patch-centered bounded editing and replay-safe fallback behavior on a legacy dossier task."
                ),
                expected_checks=[
                    "planning_quality_improved",
                    "narrow_diff_bias_preserved",
                    "execution_profile_selection",
                    "apply_patch_preserved",
                ],
            ),
        ],
        rationale_catalog=list(support["dataset"].rationale_catalog) + list(coding["dataset"].rationale_catalog),
        metadata={"lane": "support_execution_coding_overlay_composed", "phase": "v3"},
    )

    atomic_candidate = CandidateBundle(
        candidate_id="cand.support_execution_coding_overlay.atomic_union.001",
        source_target_id=composed_target.target_id,
        applied_loci=[
            "policy.support_claim_limited_actions",
            "policy.execution_profile.selection",
            "prompt.section.planning_policy",
            "prompt.section.editing_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="policy.support_claim_limited_actions",
                value=support["child_candidate"].changes[0].value,
                rationale="Carry forward the atomic support-limited action tightening.",
            ),
            CandidateChange(
                locus_id="policy.execution_profile.selection",
                value=support["child_candidate"].changes[1].value,
                rationale="Carry forward the atomic execution-profile fallback tightening.",
            ),
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value=coding["child_candidate"].changes[0].value,
                rationale="Carry forward the atomic bounded planning improvement.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value=coding["child_candidate"].changes[1].value,
                rationale="Carry forward the atomic bounded editing improvement.",
            ),
        ],
        provenance={
            "kind": "atomic_family_union",
            "member_family_ids": [support_family.family_id, coding_family.family_id],
        },
        metadata={"lane": "support_execution_coding_overlay_composed", "role": "atomic_union"},
    )

    composed_candidate = CandidateBundle(
        candidate_id="cand.support_execution_coding_overlay.composed.001",
        source_target_id=composed_target.target_id,
        applied_loci=[
            "policy.support_claim_limited_actions",
            "policy.execution_profile.selection",
            "prompt.section.planning_policy",
            "prompt.section.editing_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="policy.support_claim_limited_actions",
                value={"actions": ["checkpoint", "terminate"], "requires_review_for_continue": True},
                rationale="Keep support-sensitive execution honest by removing silent continue paths in the composed dossier lane.",
            ),
            CandidateChange(
                locus_id="policy.execution_profile.selection",
                value={
                    "preferred_profile": "workspace-write",
                    "fallback_profile": "replay-safe",
                    "require_replay_safe_lane": True,
                    "requires_apply_patch_tool": True,
                },
                rationale="Tie execution-profile fallback explicitly to the same bounded coding surface the prompt overlay assumes.",
            ),
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={
                    "text": "Sketch the smallest viable plan first; on support-sensitive tasks, checkpoint or escalate before broad edits when replay-safe evidence is thin."
                },
                rationale="Compose support-sensitive review burden with bounded planning guidance.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={
                    "text": "Prefer apply_patch and narrow diffs; preserve support-limited actions and replay-safe fallback instead of implying broader rewrites."
                },
                rationale="Make the prompt surface respect the same bounded config policy as the support lane.",
            ),
        ],
        provenance={
            "kind": "family_composition_child_candidate",
            "member_family_ids": [support_family.family_id, coding_family.family_id],
            "composition_kind": "staged",
        },
        metadata={"lane": "support_execution_coding_overlay_composed", "role": "composed_child"},
    )

    atomic_materialized = materialize_candidate(
        composed_target,
        atomic_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {
                "coordination": {
                    "intervention": {"support_claim_limited_actions": ["checkpoint", "terminate"]}
                },
                "workspace": {
                    "execution_profile": {
                        "selection": {
                            "preferred_profile": "workspace-write",
                            "fallback_profile": "replay-safe",
                            "require_replay_safe_lane": True,
                        }
                    }
                },
            },
        },
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 3},
        metadata={"lane": "support_execution_coding_overlay_composed", "role": "atomic_union"},
    )
    composed_materialized = materialize_candidate(
        composed_target,
        composed_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {
                "coordination": {
                    "intervention": {"support_claim_limited_actions": ["checkpoint", "terminate"]}
                },
                "workspace": {
                    "execution_profile": {
                        "selection": {
                            "preferred_profile": "workspace-write",
                            "fallback_profile": "replay-safe",
                            "require_replay_safe_lane": True,
                            "requires_apply_patch_tool": True,
                        }
                    }
                },
            },
        },
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 3},
        metadata={"lane": "support_execution_coding_overlay_composed", "role": "composed_child"},
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.support_execution_coding_overlay.v3",
        suite_kind="composed_support_execution_coding_overlay_family",
        evaluator_stack=[
            "support_claim_checker.v1",
            "execution_profile_checker.v1",
            "coding_overlay_checker.v1",
            "diff_hygiene_checker.v1",
            "promotion_readiness_slice.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "escalation_policy": "ambiguous_hidden_hold_or_close_margin_only",
            "mixed_tier_backend_comparison_forbidden": True,
        },
        capture_requirements=[
            "support_honesty_delta",
            "execution_profile_selection",
            "planning_quality_improved",
            "apply_patch_preserved",
            "hidden_hold_preservation",
        ],
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "reject_support_widening": True,
            "mini_escalation_requires_justification": True,
            "mini_escalation_triggers": ["inconclusive_hidden_hold", "close_validation_margin"],
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_atomic_vs_composed.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "benchmark_summary_json", "model_tier_audit_json"],
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "phase": "v3",
            "model_policy": "nano_first",
            "mini_escalation_policy": "auditable_justified_only",
        },
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.support_execution_coding_overlay.v3",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "support_honesty": {"direction": "maximize", "source_metric": "support_honesty_delta", "promotion_sensitive": True},
            "execution_profile_fidelity": {
                "direction": "maximize",
                "source_metric": "execution_profile_match",
                "promotion_sensitive": True,
            },
            "planning_quality": {"direction": "maximize", "source_metric": "planning_quality_improved", "promotion_sensitive": True},
            "diff_hygiene": {"direction": "maximize", "source_metric": "apply_patch_preserved", "promotion_sensitive": True},
            "mutation_cost": {"direction": "minimize", "source_metric": "cost_delta_usd", "promotion_sensitive": False},
        },
        penalties={
            "support_widening": {"kind": "hard_block", "reason": "support-sensitive config changes may not widen implicit capability claims"},
            "blast_radius_expansion": {"kind": "hard_block", "reason": "prompt overlay may not imply broader rewrites or looser diff hygiene"},
            "apply_patch_regression": {"kind": "hard_block", "reason": "apply_patch-centered bounded editing must remain explicit"},
        },
        aggregation_rules={"per_sample": "weighted_sum", "global": "support_sensitive_bounded_edit_first"},
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
            "mini_escalation_on_ambiguity_only": True,
        },
        frontier_dimensions=[
            "support_honesty",
            "execution_profile_fidelity",
            "planning_quality",
            "diff_hygiene",
            "mutation_cost",
        ],
        promotion_annotations={
            "requires_support_sensitive_review": True,
            "review_class": "support_sensitive_coding_overlay",
        },
        visibility_annotations={
            "hidden_hold_channels": ["support_honesty", "execution_profile_fidelity", "planning_quality", "diff_hygiene"],
        },
        metadata={"lane": "support_execution_coding_overlay_composed", "phase": "v3", "model_policy": "nano_first"},
    )

    composition = FamilyCompositionManifest(
        composition_id="composition.support_execution_coding_overlay.v3",
        member_family_ids=[support_family.family_id, coding_family.family_id],
        composition_kind="staged",
        shared_target_scope="codex_dossier_package",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        search_space_id="searchspace.support_execution_coding_overlay.v3",
        review_class="support_sensitive_coding_overlay",
        promotion_class="bounded_e4_prompt_config_change",
        applicability_scope={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "tool_pack_profile": "codex-dossier-default",
            "environment_profile": "workspace-write",
        },
        cross_family_invariants=[
            "bounded-overlay-only",
            "support-envelope-preserved",
            "apply-patch-preserved",
            "support-sensitive-review-remains-explicit",
        ],
        runtime_context_requirements={
            "requires_apply_patch_tool": True,
            "requires_replay_safe_lane": True,
            "model_policy": "nano_first",
            "mini_escalation_policy": "auditable_justified_only",
        },
        metadata={"lane": "support_execution_coding_overlay_composed", "phase": "v3"},
    )

    search_space = SearchSpaceManifest(
        search_space_id=composition.search_space_id,
        composition_id=composition.composition_id,
        allowed_loci=[locus.locus_id for locus in composed_target.mutable_loci],
        mutation_kinds_by_locus={
            "policy.support_claim_limited_actions": ["replace"],
            "policy.execution_profile.selection": ["replace"],
            "prompt.section.planning_policy": ["replace"],
            "prompt.section.editing_policy": ["replace"],
        },
        value_domains_by_locus={
            "policy.support_claim_limited_actions": {
                "allowed_actions_subset_of": ["checkpoint", "terminate", "continue"],
                "forbid_unbounded_new_actions": True,
            },
            "policy.execution_profile.selection": {
                "preferred_profiles": ["workspace-write", "replay-safe"],
                "fallback_profiles": ["workspace-write", "replay-safe"],
                "require_boolean_flag": "require_replay_safe_lane",
            },
            "prompt.section.planning_policy": {
                "must_preserve_apply_patch": True,
                "must_reference_support_sensitive_review": True,
            },
            "prompt.section.editing_policy": {
                "must_preserve_narrow_diffs": True,
                "must_discourage_broad_rewrites": True,
            },
        },
        semantic_constraints={
            "policy.support_claim_limited_actions": {"must_preserve_honesty": True},
            "policy.execution_profile.selection": {"must_preserve_replay_safe_lane": True},
            "prompt.section.planning_policy": {"must_not_expand_edit_scope": True},
            "prompt.section.editing_policy": {"must_keep_apply_patch_explicit": True},
        },
        coupled_loci_groups={
            "support_and_editing": ["policy.support_claim_limited_actions", "prompt.section.editing_policy"],
            "replay_and_planning": ["policy.execution_profile.selection", "prompt.section.planning_policy"],
        },
        stage_partitions={
            "support_config_seed": ["policy.support_claim_limited_actions", "policy.execution_profile.selection"],
            "prompt_overlay_refine": ["prompt.section.planning_policy", "prompt.section.editing_policy"],
        },
        cross_family_constraints={
            "support_execution_coding_overlay": {
                "member_family_ids": [support_family.family_id, coding_family.family_id],
                "must_preserve_apply_patch_if_support_actions_change": True,
                "must_preserve_replay_safe_fallback_if_prompt_overlay_changes": True,
            }
        },
        invariants=[
            "bounded-overlay-only",
            "support-envelope-preserved",
            "apply-patch-preserved",
        ],
        unsafe_expansion_notes=[
            "Adding new intervention actions is out of scope.",
            "Changing tool surfaces is out of scope.",
            "Whole-file rewrite guidance is out of scope.",
        ],
        metadata={"lane": "support_execution_coding_overlay_composed", "phase": "v3"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.support_execution_coding_overlay.v3",
        benchmark_kind="support_execution_coding_overlay_composed_pack",
        target_id=composed_target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=atomic_candidate.candidate_id,
        environment_domain="workspace-write/replay-safe",
        evaluator_stack=list(evaluation_suite.evaluator_stack),
        comparison_protocol="paired_atomic_vs_composed.v1",
        splits=[
            BenchmarkSplit("train", ["sample.support_execution_coding_overlay.train.001"], "mutation_visible"),
            BenchmarkSplit("validation", ["sample.support_execution_coding_overlay.validation.001"], "comparison_visible"),
            BenchmarkSplit("hold", ["sample.support_execution_coding_overlay.hold.001"], "hidden_hold"),
            BenchmarkSplit("regression", ["sample.support_execution_coding_overlay.regression.001"], "comparison_visible"),
        ],
        bucket_tags={
            "sample.support_execution_coding_overlay.train.001": ["support-sensitive", "small-edit"],
            "sample.support_execution_coding_overlay.validation.001": ["planning", "replay-safe"],
            "sample.support_execution_coding_overlay.hold.001": ["hidden-hold", "support-sensitive"],
            "sample.support_execution_coding_overlay.regression.001": ["regression", "apply-patch"],
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "escalation_policy": "ambiguous_hidden_hold_or_close_margin_only",
            "audit_escalations": True,
        },
        contamination_notes=[
            "Nano is the default mutation and comparison tier for this composed dossier lane.",
            "Mini may only be used after Nano on ambiguous hidden-hold or close-margin results and may not quietly bias backend comparisons.",
        ],
        promotion_relevance={
            "requires_support_sensitive_review": True,
            "review_class": composition.review_class,
            "composition_id": composition.composition_id,
            "transfer_slice_ids": ["package.codex_dossier.prompt_config", "model_tier.nano_first_openai"],
        },
        artifact_refs=[ArtifactRef(ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml", media_type="text/yaml")],
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "phase": "v3",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_first",
            "transfer_slice_ids": ["package.codex_dossier.prompt_config", "model_tier.nano_first_openai"],
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.support_execution_coding_overlay.atomic_vs_composed.001",
        parent_candidate_id=atomic_candidate.candidate_id,
        child_candidate_id=composed_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale=(
            "The composed candidate preserves support-sensitive honesty and replay-safe fallback while improving bounded planning/editing discipline on the hidden-hold and regression slices."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_coding_overlay/paired_atomic_vs_composed_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "support_honesty_delta": 0.14,
            "execution_profile_fidelity_delta": 0.09,
            "planning_quality_delta": 0.17,
            "diff_hygiene_delta": 0.07,
            "held_out_support_sensitive_win": True,
        },
        better_candidate_id=composed_candidate.candidate_id,
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "composition_id": composition.composition_id,
            "model_policy": "nano_first",
            "mini_escalation_triggered": False,
        },
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.support_execution_coding_overlay.composed.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=composed_candidate.candidate_id,
        per_sample_components={
            "sample.support_execution_coding_overlay.train.001": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "planning_quality": 0.78,
                "diff_hygiene": 0.95,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_coding_overlay.validation.001": {
                "support_honesty": 0.97,
                "execution_profile_fidelity": 0.96,
                "planning_quality": 0.82,
                "diff_hygiene": 0.94,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_coding_overlay.hold.001": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "planning_quality": 0.74,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_coding_overlay.regression.001": {
                "support_honesty": 0.98,
                "execution_profile_fidelity": 0.98,
                "planning_quality": 0.71,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "support-sensitive": {"support_honesty": 1.0, "execution_profile_fidelity": 1.0},
            "apply-patch": {"planning_quality": 0.71, "diff_hygiene": 1.0},
        },
        aggregate_objectives={
            "support_honesty": 0.9875,
            "execution_profile_fidelity": 0.985,
            "planning_quality": 0.7625,
            "diff_hygiene": 0.9725,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 1,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": True,
            "mini_escalation_triggered": False,
        },
        blocked_components={},
        member_family_breakdowns={
            support_family.family_id: {"support_honesty": 0.9875, "execution_profile_fidelity": 0.985},
            coding_family.family_id: {"planning_quality": 0.7625, "diff_hygiene": 0.9725},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_coding_overlay/objective_breakdown_composed.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_first",
        },
    )

    result = BenchmarkRunResult(
        run_id="benchmark_run.support_execution_coding_overlay.v3",
        manifest_id=manifest.manifest_id,
        candidate_ids=[atomic_candidate.candidate_id, composed_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={"atomic_union_score": 0.73, "composed_score": 0.86, "requires_review": True},
        bucket_outcomes={"support-sensitive": {"outcome": "child_win"}, "apply-patch": {"outcome": "child_win"}},
        variance_summary={
            "trial_count": 1,
            "stochasticity_class": "deterministic",
            "default_model": "gpt-5.4-nano",
            "mini_escalation_considered": True,
            "mini_escalation_triggered": False,
            "mini_escalation_reason": "not_needed_after_clear_hidden_hold_margin",
        },
        cost_support_evidence_slices={
            "support_honesty_delta": 1.0,
            "apply_patch_preserved": True,
            "model_policy": "nano_first",
        },
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_coding_overlay/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "eligible_for_promotion": False,
            "requires_review": True,
            "blocked_reason": "support-sensitive composed dossier changes require explicit review",
            "objective_breakdown_result_id": objective_breakdown.result_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "mini_escalation_audit": {
                "default_model": "gpt-5.4-nano",
                "escalation_model": "gpt-5.4-mini",
                "triggered": False,
                "trigger_reason": None,
            },
        },
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "phase": "v3",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_first",
        },
    )

    staged_request = StagedOptimizerRequest(
        request_id="staged_request.support_execution_coding_overlay.v3",
        backend_request=ReflectiveParetoBackendRequest(
            request_id="backend_request.support_execution_coding_overlay.v3",
            target=composed_target,
            baseline_candidate=atomic_candidate,
            baseline_materialized_candidate=atomic_materialized,
            dataset=dataset,
            evaluations=[
                EvaluationRecord(
                    evaluation_id="eval.support_execution_coding_overlay.baseline.001",
                    target_id=composed_target.target_id,
                    candidate_id=atomic_candidate.candidate_id,
                    dataset_id=dataset.dataset_id,
                    dataset_version=dataset.dataset_version,
                    sample_id=dataset.samples[0].sample_id,
                    evaluator_id="support_execution_coding_overlay_checker.v3",
                    evaluator_version="v3",
                    status="completed",
                    outcome="failed",
                    started_at="2026-03-19T10:00:00.000Z",
                    completed_at="2026-03-19T10:00:01.000Z",
                    duration_ms=1000,
                    raw_evidence_refs=[
                        ArtifactRef(
                            ref="artifacts/optimization/support_execution_coding_overlay/baseline_eval.json",
                            media_type="application/json",
                        )
                    ],
                    normalized_diagnostics=[
                        DiagnosticBundle(
                            bundle_id="bundle.eval.support_execution_coding_overlay.baseline.001",
                            evaluation_id="eval.support_execution_coding_overlay.baseline.001",
                            evaluator_mode="replay",
                            determinism_class="deterministic",
                            entries=[
                                DiagnosticEntry(
                                    diagnostic_id="diag.support_execution_coding_overlay.baseline.001",
                                    kind="wrongness",
                                    severity="error",
                                    message="atomic family union leaves support-sensitive prompt/config coupling under-optimized",
                                    locus_id="prompt.section.editing_policy",
                                    evidence_refs=[
                                        ArtifactRef(
                                            ref="artifacts/optimization/support_execution_coding_overlay/diagnostic.json",
                                            media_type="application/json",
                                        )
                                    ],
                                )
                            ],
                            cache_identity={"key": "cache.support_execution_coding_overlay.v3", "version": "1"},
                            retry_policy_hint={"max_trials": 1},
                            reproducibility_notes={"family_bound": True, "model_policy": "nano_first"},
                        )
                    ],
                    wrongness_reports=[
                        WrongnessReport(
                            wrongness_id="wrongness.support_execution_coding_overlay.001",
                            wrongness_class="correctness.result_mismatch",
                            failure_locus="prompt.section.editing_policy",
                            explanation=(
                                "Atomic support/config union still leaves the bounded support-sensitive prompt/editing coupling under-optimized."
                            ),
                            confidence=0.86,
                            supporting_evidence_refs=[
                                ArtifactRef(
                                    ref="artifacts/optimization/support_execution_coding_overlay/wrongness.json",
                                    media_type="application/json",
                                )
                            ],
                            likely_repair_locus="prompt.section.editing_policy",
                        )
                    ],
                    metadata={"composition_id": composition.composition_id, "model_policy": "nano_first"},
                )
            ],
            active_sample_id=dataset.samples[0].sample_id,
            execution_context=OptimizationExecutionContext(
                target_id=composed_target.target_id,
                sample_id=dataset.samples[0].sample_id,
                runtime_context=dataset.samples[0].runtime_context(),
                evaluation_input_compatibility=atomic_materialized.evaluation_input_compatibility,
                metadata={"composition_id": composition.composition_id, "model_policy": "nano_first"},
            ),
            mutation_bounds=MutationBounds(max_changed_loci=4, max_changed_artifacts=1, max_total_value_bytes=2600),
            max_proposals=3,
            metadata={"family_composition": composition.composition_id, "model_policy": "nano_first"},
        ),
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        search_space=search_space,
        family_composition=composition,
        metadata={"lane": "support_execution_coding_overlay_composed", "model_policy": "nano_first"},
    )
    staged_result = run_staged_optimizer(staged_request)

    return {
        "target": composed_target,
        "dataset": dataset,
        "atomic_candidate": atomic_candidate,
        "composed_candidate": composed_candidate,
        "atomic_materialized_candidate": atomic_materialized,
        "composed_materialized_candidate": composed_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "family_composition": composition,
        "search_space": search_space,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "benchmark_result": result,
        "staged_request": staged_request,
        "staged_result": staged_result,
    }


def build_support_execution_coding_overlay_composition_example_payload() -> Dict[str, object]:
    example = build_support_execution_coding_overlay_composition_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "atomic_candidate": example["atomic_candidate"].to_dict(),
        "composed_candidate": example["composed_candidate"].to_dict(),
        "atomic_materialized_candidate": example["atomic_materialized_candidate"].to_dict(),
        "composed_materialized_candidate": example["composed_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "family_composition": example["family_composition"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
        "staged_request": example["staged_request"].to_dict(),
        "staged_result": example["staged_result"].to_dict(),
    }


def build_support_execution_coding_overlay_verifier_follow_on_example() -> Dict[str, object]:
    """Build one narrow verifier-assisted follow-on on the bounded E4 composed lane."""

    example = build_support_execution_coding_overlay_composition_example()
    composed_candidate = example["composed_candidate"]
    composition = example["family_composition"]
    evaluation_suite = example["evaluation_suite"]
    objective_suite = example["objective_suite"]
    search_space = example["search_space"]
    manifest = example["manifest"]
    assert isinstance(composed_candidate, CandidateBundle)
    assert isinstance(composition, FamilyCompositionManifest)
    assert isinstance(evaluation_suite, EvaluationSuiteManifest)
    assert isinstance(objective_suite, ObjectiveSuiteManifest)
    assert isinstance(search_space, SearchSpaceManifest)
    assert isinstance(manifest, BenchmarkRunManifest)

    refined_candidate = CandidateBundle(
        candidate_id="cand.support_execution_coding_overlay.verifier_refined.001",
        source_target_id=example["target"].target_id,
        applied_loci=[
            "prompt.section.planning_policy",
            "prompt.section.editing_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={
                    "text": (
                        "Plan the smallest replay-safe support-sensitive change first, and keep apply_patch "
                        "as the default path when prompt/config edits interact."
                    )
                },
                rationale=(
                    "Verifier-guided refinement tightens the support-sensitive planning language without "
                    "touching undeclared loci."
                ),
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={
                    "text": (
                        "Prefer apply_patch and narrow diffs; if support-sensitive policy wording changes, "
                        "make the bounded review burden explicit rather than implying broader edits."
                    )
                },
                rationale=(
                    "Make the coupled support/editing invariant more explicit on the hidden-hold slice "
                    "without widening the search space."
                ),
            ),
        ],
        provenance={
            "kind": "composed_verifier_follow_on",
            "baseline_candidate_id": composed_candidate.candidate_id,
            "composition_id": composition.composition_id,
        },
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "role": "verifier_follow_on",
            "composition_id": composition.composition_id,
            "non_kernel": True,
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.support_execution_coding_overlay.composed_vs_verifier_refined.001",
        parent_candidate_id=composed_candidate.candidate_id,
        child_candidate_id=refined_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale=(
            "The verifier-assisted refinement improves support-sensitive planning and editing coupling on the "
            "composed hidden-hold slice without introducing new loci or loosening replay-safe boundaries."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_coding_overlay/verifier_follow_on_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "planning_quality_delta": 0.04,
            "diff_hygiene_delta": 0.03,
            "held_out_support_sensitive_win": True,
            "verifier_confirmed": True,
        },
        better_candidate_id=refined_candidate.candidate_id,
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "composition_id": composition.composition_id,
            "experiment_kind": "verifier_augmented_composed_refinement",
            "model_policy": "nano_first",
        },
    )

    refined_objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.support_execution_coding_overlay.verifier_refined.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=refined_candidate.candidate_id,
        per_sample_components={
            "sample.support_execution_coding_overlay.train.001": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "planning_quality": 0.8,
                "diff_hygiene": 0.97,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_coding_overlay.validation.001": {
                "support_honesty": 0.97,
                "execution_profile_fidelity": 0.96,
                "planning_quality": 0.85,
                "diff_hygiene": 0.96,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_coding_overlay.hold.001": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "planning_quality": 0.78,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_coding_overlay.regression.001": {
                "support_honesty": 0.98,
                "execution_profile_fidelity": 0.98,
                "planning_quality": 0.74,
                "diff_hygiene": 1.0,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "support-sensitive": {"support_honesty": 1.0, "execution_profile_fidelity": 1.0},
            "apply-patch": {"planning_quality": 0.74, "diff_hygiene": 1.0},
        },
        aggregate_objectives={
            "support_honesty": 0.9875,
            "execution_profile_fidelity": 0.985,
            "planning_quality": 0.7925,
            "diff_hygiene": 0.9825,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 1,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": True,
            "mini_escalation_triggered": False,
        },
        blocked_components={},
        member_family_breakdowns={
            "family.support_execution.v2": {"support_honesty": 0.9875, "execution_profile_fidelity": 0.985},
            "family.coding_overlay.v2": {"planning_quality": 0.7925, "diff_hygiene": 0.9825},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_coding_overlay/objective_breakdown_verifier_refined.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "experiment_kind": "verifier_augmented_composed_refinement",
            "composed_follow_on": True,
        },
    )

    verifier_experiment = VerifierAugmentedExperimentResult(
        experiment_id="verifier_experiment.support_execution_coding_overlay.v3",
        experiment_kind="verifier_augmented_composed_refinement",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        target_family_id="family.coding_overlay.v2",
        search_space_id=search_space.search_space_id,
        baseline_candidate_id=composed_candidate.candidate_id,
        refined_candidate_id=refined_candidate.candidate_id,
        verifier_stack=[
            *evaluation_suite.evaluator_stack,
            "bounded_support_coupling_verifier.v1",
        ],
        focus_sample_ids=manifest.hidden_hold_sample_ids() or manifest.sample_ids(),
        comparison_result_id=comparison.comparison_id,
        objective_breakdown_result_id=refined_objective_breakdown.result_id,
        outcome="accepted",
        rationale=(
            "This narrow follow-on keeps the verifier experiment inside the existing composed dossier lane, "
            "specializing the coding-overlay member family while preserving support-sensitive honesty and "
            "the declared family composition boundary."
        ),
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_coding_overlay/verifier_follow_on_summary.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "support_execution_coding_overlay_composed",
            "phase": "v3",
            "backend_only": True,
            "non_kernel": True,
            "darwin_boundary": "not_reopened",
            "family_bound": True,
            "composition_id": composition.composition_id,
            "member_family_ids": list(composition.member_family_ids),
            "specialization_scope": "coding_overlay_member_inside_composed_lane",
            "model_policy": "nano_first",
        },
    )

    return {
        "composition_example": example,
        "refined_candidate": refined_candidate,
        "comparison_result": comparison,
        "objective_breakdown_result": refined_objective_breakdown,
        "verifier_experiment": verifier_experiment,
    }


def build_support_execution_coding_overlay_verifier_follow_on_example_payload() -> Dict[str, object]:
    example = build_support_execution_coding_overlay_verifier_follow_on_example()
    return {
        "composition_example": {
            "evaluation_suite": example["composition_example"]["evaluation_suite"].to_dict(),
            "objective_suite": example["composition_example"]["objective_suite"].to_dict(),
            "family_composition": example["composition_example"]["family_composition"].to_dict(),
            "search_space": example["composition_example"]["search_space"].to_dict(),
            "manifest": example["composition_example"]["manifest"].to_dict(),
            "composed_candidate": example["composition_example"]["composed_candidate"].to_dict(),
        },
        "refined_candidate": example["refined_candidate"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "verifier_experiment": example["verifier_experiment"].to_dict(),
    }


def build_support_execution_tool_guidance_coding_overlay_package_example() -> Dict[str, object]:
    """Build the first V4 mixed-evidence triplet lane on the current dossier package."""

    support_coding = build_support_execution_coding_overlay_composition_example()
    tool = build_tool_guidance_benchmark_example()
    support_target = support_coding["target"]
    tool_target = tool["target"]
    support_composed_candidate = support_coding["composed_candidate"]
    tool_child_candidate = tool["child_candidate"]
    assert isinstance(support_target, OptimizationTarget)
    assert isinstance(tool_target, OptimizationTarget)
    assert isinstance(support_composed_candidate, CandidateBundle)
    assert isinstance(tool_child_candidate, CandidateBundle)
    support_family = build_support_execution_benchmark_example()["target_family"]
    coding_family = build_coding_overlay_benchmark_example()["target_family"]
    tool_family = tool["target_family"]
    assert isinstance(support_family, TargetFamilyManifest)
    assert isinstance(coding_family, TargetFamilyManifest)
    assert isinstance(tool_family, TargetFamilyManifest)

    package_target = OptimizationTarget(
        target_id="target.codex_dossier.support_execution_tool_guidance_coding_overlay.v4",
        target_kind="agent_config_overlay_package",
        baseline_artifact_refs=list(support_target.baseline_artifact_refs),
        mutable_loci=list(support_target.mutable_loci) + list(tool_target.mutable_loci),
        support_envelope=support_target.support_envelope,
        invariants=list(support_target.invariants),
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "phase": "v4",
            "member_family_ids": [
                support_family.family_id,
                tool_family.family_id,
                coding_family.family_id,
            ],
        },
    )

    support_dataset = build_support_execution_benchmark_example()["dataset"]
    tool_dataset = tool["dataset"]
    coding_dataset = build_coding_overlay_benchmark_example()["dataset"]
    assert isinstance(support_dataset, OptimizationDataset)
    assert isinstance(tool_dataset, OptimizationDataset)
    assert isinstance(coding_dataset, OptimizationDataset)

    dataset = OptimizationDataset(
        dataset_id="dataset.support_execution_tool_guidance_coding_overlay.v4",
        dataset_version="2026-03-19.v4",
        samples=[
            _clone_sample_for_composed_target(
                support_dataset.samples[0],
                sample_id="sample.support_execution_tool_guidance_coding_overlay.train.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Improve support-sensitive planning, tool wording, and bounded editing guidance together "
                    "without widening the package support envelope."
                ),
                expected_checks=[
                    "support_envelope_preserved",
                    "clarity_improved",
                    "planning_quality_improved",
                    "apply_patch_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                tool_dataset.samples[1],
                sample_id="sample.support_execution_tool_guidance_coding_overlay.validation.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Optimize tool clarity and replay-safe support-sensitive guidance on the same bounded dossier package."
                ),
                expected_checks=[
                    "support_envelope_preserved",
                    "clarity_improved",
                    "replay_guidance_preserved",
                    "tool_guidance_coherent",
                ],
            ),
            _clone_sample_for_composed_target(
                coding_dataset.samples[2],
                sample_id="sample.support_execution_tool_guidance_coding_overlay.hold.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Catch package candidates that sound better but loosen support honesty, replay-safe fallback, or bounded-edit doctrine."
                ),
                expected_checks=[
                    "hidden_hold_package_guard",
                    "support_envelope_preserved",
                    "apply_patch_preserved",
                    "support_sensitive_review_preserved",
                ],
            ),
            _clone_sample_for_composed_target(
                coding_dataset.samples[3],
                sample_id="sample.support_execution_tool_guidance_coding_overlay.regression.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Preserve narrow diffs, replay-safe behavior, and tool wording discipline on legacy repo tasks."
                ),
                expected_checks=[
                    "regression_clean",
                    "apply_patch_preserved",
                    "support_envelope_preserved",
                    "clarity_improved",
                ],
            ),
        ],
        rationale_catalog=(
            list(support_dataset.rationale_catalog)
            + list(tool_dataset.rationale_catalog)
            + list(coding_dataset.rationale_catalog)
        ),
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "phase": "v4"},
    )

    baseline_candidate = CandidateBundle(
        candidate_id="cand.support_execution_tool_guidance_coding_overlay.v3_baseline.001",
        source_target_id=package_target.target_id,
        applied_loci=[
            "policy.support_claim_limited_actions",
            "policy.execution_profile.selection",
            "tool.render.exec_command",
            "prompt.section.optimization_guidance",
            "prompt.section.planning_policy",
            "prompt.section.editing_policy",
        ],
        changes=[
            *[
                CandidateChange(
                    locus_id=change.locus_id,
                    value=change.value,
                    rationale=change.rationale,
                )
                for change in support_composed_candidate.changes
            ],
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={"description": tool_child_candidate.changes[0].value["description"]},
                rationale="Carry forward the existing tool-guidance improvement as the V3 baseline extension.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={"text": tool_child_candidate.changes[1].value["text"]},
                rationale="Carry forward the atomic optimization-guidance wording as the V3 baseline extension.",
            ),
        ],
        provenance={
            "kind": "v3_composed_plus_atomic_tool_guidance",
            "member_family_ids": [support_family.family_id, tool_family.family_id, coding_family.family_id],
        },
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "role": "v3_baseline"},
    )

    package_candidate = CandidateBundle(
        candidate_id="cand.support_execution_tool_guidance_coding_overlay.package.001",
        source_target_id=package_target.target_id,
        applied_loci=list(baseline_candidate.applied_loci),
        changes=[
            CandidateChange(
                locus_id="policy.support_claim_limited_actions",
                value={
                    "allowed_actions": ["checkpoint", "continue", "terminate"],
                    "review_note": "support-sensitive changes require explicit bounded review",
                },
                rationale="Keep the support-action surface bounded and honest in the triplet package lane.",
            ),
            CandidateChange(
                locus_id="policy.execution_profile.selection",
                value={
                    "preferred_profile": "workspace-write",
                    "fallback_profile": "replay-safe",
                    "require_replay_safe_lane": True,
                },
                rationale="Make replay-safe fallback explicit while preserving the declared environment envelope.",
            ),
            CandidateChange(
                locus_id="tool.render.exec_command",
                value={
                    "description": (
                        "Run shell commands inside the declared support envelope, then summarize results clearly "
                        "without implying broader write scope or looser review burden."
                    )
                },
                rationale="Compose tool clarity directly with support-sensitive and bounded-edit constraints.",
            ),
            CandidateChange(
                locus_id="prompt.section.optimization_guidance",
                value={
                    "text": (
                        "Prefer narrow overlays, preserve replay-safe behavior, and keep support, tool, and "
                        "editing guidance aligned around bounded package changes."
                    )
                },
                rationale="Raise package-level coherence across all three member families.",
            ),
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value={
                    "text": (
                        "Start with the smallest support-sensitive, replay-safe plan; treat tool wording and "
                        "edit scope as coupled package surfaces."
                    )
                },
                rationale="Make package-scoped planning quality depend on both support and tool context.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value={
                    "text": (
                        "Prefer apply_patch and narrow diffs; clearer tool guidance must not imply broader rewrites, "
                        "looser support boundaries, or weaker review obligations."
                    )
                },
                rationale="Make bounded-edit doctrine explicit under triplet composition.",
            ),
        ],
        provenance={
            "kind": "mixed_evidence_triplet_package_candidate",
            "baseline_candidate_id": baseline_candidate.candidate_id,
            "member_family_ids": [support_family.family_id, tool_family.family_id, coding_family.family_id],
        },
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "role": "package_triplet"},
    )

    baseline_materialized = materialize_candidate(
        package_target,
        baseline_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {"package_role": "v3_baseline_extension"},
        },
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 4},
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "role": "v3_baseline"},
    )
    package_materialized = materialize_candidate(
        package_target,
        package_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "overlay": {"package_role": "mixed_evidence_triplet"},
        },
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"], "exposed_count": 3},
        evaluation_input_compatibility={"replay": True, "schema": 4},
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "role": "package_triplet"},
    )

    transfer_slices = [
        TransferSliceManifest(
            slice_id="package.codex_dossier.current",
            slice_kind="package",
            selector={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml"},
            promotion_role="required",
            visibility="hidden_hold",
            metadata={"lane": "support_execution_tool_guidance_coding_overlay_package"},
        ),
        TransferSliceManifest(
            slice_id="model_tier.nano_first_openai",
            slice_kind="model_tier",
            selector={"default_model": "gpt-5.4-nano", "escalation_model": "gpt-5.4-mini"},
            promotion_role="claim_supporting",
            visibility="comparison_visible",
            metadata={"lane": "support_execution_tool_guidance_coding_overlay_package"},
        ),
        TransferSliceManifest(
            slice_id="environment.workspace_write_replay_safe",
            slice_kind="environment",
            selector={"environment_domain": "workspace-write/replay-safe"},
            promotion_role="required",
            visibility="comparison_visible",
            metadata={"lane": "support_execution_tool_guidance_coding_overlay_package"},
        ),
    ]

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.support_execution_tool_guidance_coding_overlay.v4",
        suite_kind="mixed_evidence_package_triplet",
        evaluator_stack=[
            "support_execution_checker.v2",
            "tool_guidance_checker.v2",
            "coding_overlay_checker.v2",
            "package_coherence_verifier.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "audit_escalations": True,
        },
        capture_requirements=[
            "support_envelope_preserved",
            "clarity_improved",
            "planning_quality_improved",
            "apply_patch_preserved",
            "package_coherence_verified",
        ],
        signal_channels={
            "executable_checks": {
                "source_kind": "executable_check",
                "authority": "primary",
                "used_for": ["support_honesty", "execution_profile_fidelity", "diff_hygiene"],
            },
            "diagnostic_wrongness": {
                "source_kind": "diagnostic_bundle",
                "authority": "supporting",
                "used_for": ["planning_quality", "tool_clarity"],
            },
            "semantic_judge": {
                "source_kind": "model_judge",
                "authority": "advisory",
                "used_for": ["tool_clarity", "package_coherence"],
            },
            "review_gate": {
                "source_kind": "review_gate",
                "authority": "hard_gate",
                "used_for": ["promotion_only"],
            },
        },
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "model_policy": "nano_only",
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_v3_baseline_vs_package_triplet.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "benchmark_summary_json", "promotion_summary_json"],
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "phase": "v4",
            "model_policy": "nano_only",
        },
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.support_execution_tool_guidance_coding_overlay.v4",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "support_honesty": {"direction": "maximize", "source_metric": "support_envelope_preserved", "promotion_sensitive": True},
            "execution_profile_fidelity": {"direction": "maximize", "source_metric": "replay_guidance_preserved", "promotion_sensitive": True},
            "tool_clarity": {"direction": "maximize", "source_metric": "clarity_improved", "promotion_sensitive": True},
            "planning_quality": {"direction": "maximize", "source_metric": "planning_quality_improved", "promotion_sensitive": True},
            "diff_hygiene": {"direction": "maximize", "source_metric": "apply_patch_preserved", "promotion_sensitive": True},
            "package_coherence": {"direction": "maximize", "source_metric": "package_coherence_verified", "promotion_sensitive": True},
            "mutation_cost": {"direction": "minimize", "source_metric": "cost_delta_usd", "promotion_sensitive": False},
        },
        penalties={
            "support_scope_drift": {"kind": "hard_block", "reason": "package triplet may not imply broader support"},
            "broad_rewrite_drift": {"kind": "hard_block", "reason": "package triplet may not weaken bounded-edit doctrine"},
        },
        aggregation_rules={"per_sample": "weighted_sum", "global": "mixed_evidence_package_first"},
        uncertainty_policy={"stochasticity_class": "deterministic", "blocked_when_missing_hidden_hold": True},
        blocked_channel_annotations={
            "package_coherence": {"blocked_when_missing": ["semantic_judge", "review_gate"]},
        },
        channel_dependencies={
            "package_coherence": ["support_honesty", "tool_clarity", "planning_quality"],
        },
        frontier_dimensions=[
            "support_honesty",
            "execution_profile_fidelity",
            "tool_clarity",
            "planning_quality",
            "diff_hygiene",
            "package_coherence",
            "mutation_cost",
        ],
        promotion_annotations={"requires_support_sensitive_review": True, "review_class": "support_sensitive_package_triplet"},
        visibility_annotations={"hidden_hold_channels": ["support_honesty", "tool_clarity", "package_coherence"]},
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "phase": "v4"},
    )

    composition = FamilyCompositionManifest(
        composition_id="composition.support_execution_tool_guidance_coding_overlay.v4",
        member_family_ids=[support_family.family_id, tool_family.family_id, coding_family.family_id],
        composition_kind="staged",
        shared_target_scope="codex_dossier_package",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        search_space_id="searchspace.support_execution_tool_guidance_coding_overlay.v4",
        review_class="support_sensitive_package_triplet",
        promotion_class="bounded_package_triplet_change",
        applicability_scope={
            "artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            "tool_pack_profile": "codex-dossier-default",
            "environment_profile": "workspace-write/replay-safe",
        },
        cross_family_invariants=[
            "bounded-overlay-only",
            "support-envelope-preserved",
            "apply-patch-preserved",
            "package-coherence-may-not-weaken-review-burden",
        ],
        runtime_context_requirements={
            "requires_apply_patch_tool": True,
            "requires_replay_safe_lane": True,
            "model_policy": "nano_only",
        },
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "phase": "v4"},
    )

    search_space = SearchSpaceManifest(
        search_space_id=composition.search_space_id,
        composition_id=composition.composition_id,
        allowed_loci=[locus.locus_id for locus in package_target.mutable_loci],
        mutation_kinds_by_locus={
            "policy.support_claim_limited_actions": ["replace"],
            "policy.execution_profile.selection": ["replace"],
            "tool.render.exec_command": ["replace"],
            "prompt.section.optimization_guidance": ["replace"],
            "prompt.section.planning_policy": ["replace"],
            "prompt.section.editing_policy": ["replace"],
        },
        value_domains_by_locus={
            "policy.support_claim_limited_actions": {"forbid_unbounded_new_actions": True},
            "policy.execution_profile.selection": {"require_replay_safe_lane": True},
            "tool.render.exec_command": {"must_preserve_support_boundaries": True, "must_preserve_bounded_edits": True},
            "prompt.section.optimization_guidance": {"must_reference_bounded_package_changes": True},
            "prompt.section.planning_policy": {"must_remain_small_plan_first": True},
            "prompt.section.editing_policy": {"must_preserve_apply_patch": True, "must_discourage_broad_rewrites": True},
        },
        semantic_constraints={
            "policy.support_claim_limited_actions": {"must_preserve_honesty": True},
            "tool.render.exec_command": {"must_not_imply_new_tools": True},
            "prompt.section.editing_policy": {"must_keep_blast_radius_bounded": True},
        },
        coupled_loci_groups={
            "support_tool_editing": [
                "policy.support_claim_limited_actions",
                "tool.render.exec_command",
                "prompt.section.editing_policy",
            ],
            "replay_guidance_planning": [
                "policy.execution_profile.selection",
                "prompt.section.optimization_guidance",
                "prompt.section.planning_policy",
            ],
        },
        stage_partitions={
            "support_tool_seed": [
                "policy.support_claim_limited_actions",
                "policy.execution_profile.selection",
                "tool.render.exec_command",
            ],
            "prompt_overlay_refine": [
                "prompt.section.optimization_guidance",
                "prompt.section.planning_policy",
                "prompt.section.editing_policy",
            ],
        },
        cross_family_constraints={
            "support_tool_guidance_coding_overlay": {
                "member_family_ids": [support_family.family_id, tool_family.family_id, coding_family.family_id],
                "must_preserve_support_boundaries_if_tool_wording_changes": True,
                "must_preserve_apply_patch_if_support_policy_changes": True,
            }
        },
        invariants=[
            "bounded-overlay-only",
            "support-envelope-preserved",
            "apply-patch-preserved",
        ],
        unsafe_expansion_notes=[
            "Changing tool surfaces beyond exec_command wording is out of scope.",
            "Adding new intervention actions is out of scope.",
            "Whole-file rewrite guidance is out of scope.",
        ],
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "phase": "v4"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.support_execution_tool_guidance_coding_overlay.v4",
        benchmark_kind="mixed_evidence_package_triplet_pack",
        target_id=package_target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=baseline_candidate.candidate_id,
        environment_domain="workspace-write/replay-safe",
        evaluator_stack=list(evaluation_suite.evaluator_stack),
        comparison_protocol="paired_v3_baseline_vs_package_triplet.v1",
        splits=[
            BenchmarkSplit("train", ["sample.support_execution_tool_guidance_coding_overlay.train.001"], "mutation_visible"),
            BenchmarkSplit("validation", ["sample.support_execution_tool_guidance_coding_overlay.validation.001"], "comparison_visible"),
            BenchmarkSplit("hold", ["sample.support_execution_tool_guidance_coding_overlay.hold.001"], "hidden_hold"),
            BenchmarkSplit("regression", ["sample.support_execution_tool_guidance_coding_overlay.regression.001"], "comparison_visible"),
        ],
        bucket_tags={
            "sample.support_execution_tool_guidance_coding_overlay.train.001": ["support-sensitive", "tool-clarity", "small-edit"],
            "sample.support_execution_tool_guidance_coding_overlay.validation.001": ["tool-guidance", "replay-safe"],
            "sample.support_execution_tool_guidance_coding_overlay.hold.001": ["hidden-hold", "package-transfer"],
            "sample.support_execution_tool_guidance_coding_overlay.regression.001": ["regression", "apply-patch"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "default_model": "gpt-5.4-nano"},
        contamination_notes=[
            "Triplet lane A remains Nano-only in tranche 1.",
            "Transfer slices are not visible to mutation-time search policy.",
        ],
        transfer_slices=transfer_slices,
        promotion_relevance={
            "requires_support_sensitive_review": True,
            "review_class": composition.review_class,
            "mini_escalation_audit": {
                "default_model": "gpt-5.4-nano",
                "escalation_model": "gpt-5.4-mini",
                "triggered": False,
                "trigger_reason": None,
            },
        },
        artifact_refs=[ArtifactRef(ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml", media_type="text/yaml")],
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "phase": "v4",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_only",
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.support_execution_tool_guidance_coding_overlay.v3_baseline_vs_package.001",
        parent_candidate_id=baseline_candidate.candidate_id,
        child_candidate_id=package_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale=(
            "The package-level triplet candidate improves support-sensitive planning, tool clarity, and bounded "
            "editing coherence together without widening support scope or package blast radius."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_tool_guidance_coding_overlay/package_triplet_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "support_honesty_delta": 0.01,
            "tool_clarity_delta": 0.12,
            "planning_quality_delta": 0.09,
            "package_coherence_delta": 0.15,
            "held_out_package_guard": True,
        },
        better_candidate_id=package_candidate.candidate_id,
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "composition_id": composition.composition_id,
            "baseline_kind": "v3_composed_baseline",
            "model_policy": "nano_only",
        },
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.support_execution_tool_guidance_coding_overlay.package.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=package_candidate.candidate_id,
        per_sample_components={
            "sample.support_execution_tool_guidance_coding_overlay.train.001": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "tool_clarity": 0.88,
                "planning_quality": 0.81,
                "diff_hygiene": 0.96,
                "package_coherence": 0.87,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.validation.001": {
                "support_honesty": 0.98,
                "execution_profile_fidelity": 0.97,
                "tool_clarity": 0.9,
                "planning_quality": 0.77,
                "diff_hygiene": 0.95,
                "package_coherence": 0.85,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.hold.001": {
                "support_honesty": 1.0,
                "execution_profile_fidelity": 1.0,
                "tool_clarity": 0.84,
                "planning_quality": 0.74,
                "diff_hygiene": 1.0,
                "package_coherence": 0.88,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.regression.001": {
                "support_honesty": 0.99,
                "execution_profile_fidelity": 0.99,
                "tool_clarity": 0.83,
                "planning_quality": 0.72,
                "diff_hygiene": 1.0,
                "package_coherence": 0.83,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "support-sensitive": {"support_honesty": 1.0, "execution_profile_fidelity": 1.0},
            "tool-guidance": {"tool_clarity": 0.9, "package_coherence": 0.85},
            "apply-patch": {"planning_quality": 0.72, "diff_hygiene": 1.0},
        },
        aggregate_objectives={
            "support_honesty": 0.9925,
            "execution_profile_fidelity": 0.99,
            "tool_clarity": 0.8625,
            "planning_quality": 0.76,
            "diff_hygiene": 0.9775,
            "package_coherence": 0.8575,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={"stochasticity_class": "deterministic", "trial_count": 1, "blocked_for_uncertainty": False},
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "diagnostic_wrongness": {"status": "pass", "authority": "supporting"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "review_gate": {"status": "required", "authority": "hard_gate"},
        },
        slice_status={
            "package.codex_dossier.current": {"status": "pass", "promotion_role": "required"},
            "model_tier.nano_first_openai": {"status": "pass", "promotion_role": "claim_supporting"},
            "environment.workspace_write_replay_safe": {"status": "pass", "promotion_role": "required"},
        },
        member_family_breakdowns={
            support_family.family_id: {"support_honesty": 0.9925, "execution_profile_fidelity": 0.99},
            tool_family.family_id: {"tool_clarity": 0.8625},
            coding_family.family_id: {"planning_quality": 0.76, "diff_hygiene": 0.9775},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_tool_guidance_coding_overlay/objective_breakdown_package_triplet.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_only",
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.support_execution.v2": {
                    "status": "present",
                    "drivers": ["support_honesty", "execution_profile_fidelity"],
                },
                "family.tool_guidance.v2": {"status": "present", "drivers": ["tool_clarity"]},
                "family.coding_overlay.v2": {
                    "status": "present",
                    "drivers": ["planning_quality", "diff_hygiene", "package_coherence"],
                },
            },
        },
    )

    promotion_summary = build_promotion_evidence_summary(
        summary_id="summary.support_execution_tool_guidance_coding_overlay.package.001",
        candidate_id=package_candidate.candidate_id,
        benchmark_manifest=manifest,
        comparison_results=[comparison],
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        family_composition=composition,
        search_space=search_space,
        objective_breakdown_results=[objective_breakdown],
        review_required=True,
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package", "phase": "v4"},
    )

    benchmark_result = BenchmarkRunResult(
        run_id="benchmark_run.support_execution_tool_guidance_coding_overlay.v4",
        manifest_id=manifest.manifest_id,
        candidate_ids=[baseline_candidate.candidate_id, package_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={
            "v3_baseline_score": 0.79,
            "package_triplet_score": 0.89,
            "mixed_evidence_complete": True,
        },
        bucket_outcomes={
            "support-sensitive": {"outcome": "child_win"},
            "tool-guidance": {"outcome": "child_win"},
            "apply-patch": {"outcome": "child_win"},
        },
        variance_summary={
            "trial_count": 1,
            "stochasticity_class": "deterministic",
            "default_model": "gpt-5.4-nano",
            "mini_escalation_triggered": False,
        },
        cost_support_evidence_slices={
            "support_honesty_delta": 0.01,
            "apply_patch_preserved": True,
            "model_policy": "nano_only",
        },
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/support_execution_tool_guidance_coding_overlay/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "promotion_summary_id": promotion_summary.summary_id,
            "eligible_for_promotion": False,
            "requires_review": True,
            "blocked_reason": "support-sensitive package triplet changes require explicit review",
            "transfer_slice_ids": sorted(slice_item.slice_id for slice_item in transfer_slices),
            "mini_escalation_audit": promotion_summary.model_tier_audit,
        },
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "phase": "v4",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_only",
        },
    )

    staged_request = _build_backend_request_for_package_example(
        {
            "target": package_target,
            "dataset": dataset,
            "baseline_candidate": baseline_candidate,
            "baseline_materialized_candidate": baseline_materialized,
        },
        request_id="backend_request.support_execution_tool_guidance_coding_overlay.v4",
        evaluation_id="eval.backend.support_execution_tool_guidance_coding_overlay.v4",
        evaluator_id="mixed_evidence_package_triplet_checker.v4",
        wrongness_class="correctness.result_mismatch",
        likely_repair_locus="prompt.section.optimization_guidance",
    )
    staged_optimizer_request = StagedOptimizerRequest(
        request_id="staged_request.support_execution_tool_guidance_coding_overlay.v4",
        backend_request=staged_request,
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        search_space=search_space,
        family_composition=composition,
        metadata={
            "lane": "support_execution_tool_guidance_coding_overlay_package",
            "phase": "v4",
            "search_policy_signal": "",
            "transfer_slice_status": objective_breakdown.slice_status,
            "model_tier_audit": promotion_summary.model_tier_audit,
            "optimistic_scope_blocked": False,
        },
    )
    staged_result = run_staged_optimizer(staged_optimizer_request)

    return {
        "target": package_target,
        "dataset": dataset,
        "baseline_candidate": baseline_candidate,
        "package_candidate": package_candidate,
        "baseline_materialized_candidate": baseline_materialized,
        "package_materialized_candidate": package_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "family_composition": composition,
        "search_space": search_space,
        "transfer_slices": transfer_slices,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "promotion_summary": promotion_summary,
        "benchmark_result": benchmark_result,
        "staged_request": staged_optimizer_request,
        "staged_result": staged_result,
    }


def build_support_execution_tool_guidance_coding_overlay_package_example_payload() -> Dict[str, object]:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "baseline_candidate": example["baseline_candidate"].to_dict(),
        "package_candidate": example["package_candidate"].to_dict(),
        "baseline_materialized_candidate": example["baseline_materialized_candidate"].to_dict(),
        "package_materialized_candidate": example["package_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "family_composition": example["family_composition"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "transfer_slices": [item.to_dict() for item in example["transfer_slices"]],
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "promotion_summary": example["promotion_summary"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
        "staged_request": example["staged_request"].to_dict(),
        "staged_result": example["staged_result"].to_dict(),
    }


def build_opencode_prompt_config_tool_guidance_package_example() -> Dict[str, object]:
    """Build the second V4 mixed-evidence package lane on the OpenCode 1.2.17 dossier."""

    tool_example = build_tool_guidance_benchmark_example()
    coding_example = build_coding_overlay_benchmark_example()
    tool_target = tool_example["target"]
    assert isinstance(tool_target, OptimizationTarget)

    package_target = OptimizationTarget(
        target_id="target.opencode_prompt_config_tool_guidance.v4",
        target_kind="agent_config_overlay_package",
        baseline_artifact_refs=[
            ArtifactRef(
                ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                media_type="text/yaml",
                metadata={"surface": "public_dossier", "package": "opencode_1_2_17"},
            )
        ],
        mutable_loci=[
            MutableLocus(
                locus_id="prompt.pack.base.system",
                locus_kind="prompt_pack_section",
                selector="prompts.packs.base.system",
                artifact_ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_single_system_envelope": True},
            ),
            MutableLocus(
                locus_id="prompt.pack.base.builder",
                locus_kind="prompt_pack_section",
                selector="prompts.packs.base.builder",
                artifact_ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_plan_build_shape": True},
            ),
            MutableLocus(
                locus_id="guardrails.diff_policy.patch_splitting",
                locus_kind="bounded_config_policy",
                selector="enhanced_tools.diff_policy.patch_splitting",
                artifact_ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_single_file_patch_boundary": True},
            ),
            MutableLocus(
                locus_id="guardrails.validation.read_before_edit",
                locus_kind="bounded_config_policy",
                selector="enhanced_tools.validation.rule_config.read_before_edit",
                artifact_ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_read_before_edit_signal": True},
            ),
            MutableLocus(
                locus_id="tool.registry.include",
                locus_kind="tool_pack_policy",
                selector="tools.registry.include",
                artifact_ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_remain_bounded_visible_tool_surface": True},
            ),
            MutableLocus(
                locus_id="provider_tools.responses_use_developer_role",
                locus_kind="tool_pack_policy",
                selector="provider_tools.responses_use_developer_role",
                artifact_ref="agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
                mutation_kind="replace",
                constraints={"must_preserve_responses_native_posture": True},
            ),
        ],
        support_envelope=SupportEnvelope(
            tools=["read_file", "list_dir", "bash", "write"],
            execution_profiles=["workspace-write", "replay-safe"],
            environments=["workspace-write"],
            providers=["openai"],
            models=["gpt-5.4-nano", "gpt-5.4-mini"],
            assumptions={
                "use_native_responses": True,
                "tool_pack_profile": "opencode-native-responses",
                "nano_default": True,
                "mini_requires_audit": True,
                "package_scope": "opencode_1_2_17",
            },
        ),
        invariants=[
            OptimizationInvariant(
                invariant_id="bounded-overlay-only",
                description="candidate may only modify declared package loci",
            ),
            OptimizationInvariant(
                invariant_id="native-responses-posture-preserved",
                description="candidate may not weaken the OpenCode native Responses posture",
            ),
            OptimizationInvariant(
                invariant_id="single-file-patch-boundary-preserved",
                description="candidate may not weaken the single-file patch splitting policy",
            ),
        ],
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "package": "opencode_1_2_17",
            "member_family_ids": [
                "family.opencode_prompt_pack.v4",
                "family.opencode_bounded_config.v4",
                "family.opencode_tool_guidance_pack.v4",
            ],
        },
    )

    tool_dataset = tool_example["dataset"]
    coding_dataset = coding_example["dataset"]
    assert isinstance(tool_dataset, OptimizationDataset)
    assert isinstance(coding_dataset, OptimizationDataset)

    dataset = OptimizationDataset(
        dataset_id="dataset.opencode_prompt_config_tool_guidance.v4",
        dataset_version="2026-03-19.v4",
        samples=[
            _clone_sample_for_package_target(
                tool_dataset.samples[0],
                sample_id="sample.opencode_prompt_config_tool_guidance.train.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Improve the OpenCode prompt pack, bounded config, and visible tool-pack guidance together "
                    "without weakening the native Responses posture or patch boundaries."
                ),
                expected_checks=[
                    "prompt_pack_coherence",
                    "bounded_config_preserved",
                    "tool_pack_clarity_improved",
                    "native_responses_posture_preserved",
                ],
                tool_pack_profile="opencode-native-responses",
                required_tools=["read_file", "list_dir", "bash", "write"],
                environment_profile="workspace-write",
                model_policy="nano_first",
            ),
            _clone_sample_for_package_target(
                tool_dataset.samples[1],
                sample_id="sample.opencode_prompt_config_tool_guidance.validation.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Refine OpenCode 1.2.17 prompt and tool-pack phrasing while keeping the bounded config "
                    "guardrails readable and package-scoped."
                ),
                expected_checks=[
                    "prompt_pack_coherence",
                    "tool_pack_clarity_improved",
                    "mini_escalation_audit_clean",
                    "package_transfer_claim_supported",
                ],
                tool_pack_profile="opencode-native-responses",
                required_tools=["read_file", "list_dir", "bash", "write"],
                environment_profile="workspace-write",
                model_policy="nano_first",
            ),
            _clone_sample_for_package_target(
                coding_dataset.samples[2],
                sample_id="sample.opencode_prompt_config_tool_guidance.hold.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Catch candidates that sound more polished but loosen patch splitting, read-before-edit "
                    "signals, or OpenCode's native developer-role tool posture."
                ),
                expected_checks=[
                    "hidden_hold_package_guard",
                    "bounded_config_preserved",
                    "native_responses_posture_preserved",
                    "tool_pack_scope_preserved",
                ],
                tool_pack_profile="opencode-native-responses",
                required_tools=["read_file", "list_dir", "bash", "write"],
                environment_profile="workspace-write",
                model_policy="nano_first",
            ),
            _clone_sample_for_package_target(
                coding_dataset.samples[3],
                sample_id="sample.opencode_prompt_config_tool_guidance.regression.001",
                target_id=package_target.target_id,
                prompt_input=(
                    "Preserve the OpenCode package's visible patch, validation, and tool-pack boundaries on replay-style coding tasks."
                ),
                expected_checks=[
                    "regression_clean",
                    "bounded_config_preserved",
                    "prompt_pack_coherence",
                    "tool_pack_scope_preserved",
                ],
                tool_pack_profile="opencode-native-responses",
                required_tools=["read_file", "list_dir", "bash", "write"],
                environment_profile="workspace-write",
                model_policy="nano_first",
            ),
        ],
        rationale_catalog=list(tool_dataset.rationale_catalog) + list(coding_dataset.rationale_catalog),
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "package": "opencode_1_2_17",
        },
    )

    baseline_candidate = CandidateBundle(
        candidate_id="cand.opencode_prompt_config_tool_guidance.atomic_baseline.001",
        source_target_id=package_target.target_id,
        applied_loci=[
            "prompt.pack.base.system",
            "prompt.pack.base.builder",
            "guardrails.diff_policy.patch_splitting",
            "guardrails.validation.read_before_edit",
            "tool.registry.include",
            "provider_tools.responses_use_developer_role",
        ],
        changes=[
            CandidateChange(
                locus_id="prompt.pack.base.system",
                value={"text": "Preserve the single durable OpenCode system envelope and make package constraints explicit."},
                rationale="Carry forward a minimal prompt-pack coherence improvement without cross-family coupling.",
            ),
            CandidateChange(
                locus_id="prompt.pack.base.builder",
                value={"text": "Keep the build stage compact, package-scoped, and aligned to visible tool posture."},
                rationale="Carry forward a bounded builder prompt improvement as the atomic baseline.",
            ),
            CandidateChange(
                locus_id="guardrails.diff_policy.patch_splitting",
                value={"policy": "auto_split", "max_files_per_patch": 1, "on_violation": "warn_and_split"},
                rationale="Preserve the current single-file patch boundary without broader config tightening.",
            ),
            CandidateChange(
                locus_id="guardrails.validation.read_before_edit",
                value={"mode": "relaxed", "require_fresh_read": False, "max_age_seconds": 1800},
                rationale="Preserve the existing read-before-edit posture as a bounded baseline.",
            ),
            CandidateChange(
                locus_id="tool.registry.include",
                value={"visible_tools": ["bash", "write"]},
                rationale="Keep the public OpenCode visible tool surface narrow and readable.",
            ),
            CandidateChange(
                locus_id="provider_tools.responses_use_developer_role",
                value={"enabled": True},
                rationale="Preserve the native developer-role shaping as the atomic baseline.",
            ),
        ],
        provenance={"kind": "atomic_sequential_package_baseline", "package": "opencode_1_2_17"},
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "role": "atomic_baseline"},
    )

    package_candidate = CandidateBundle(
        candidate_id="cand.opencode_prompt_config_tool_guidance.package.001",
        source_target_id=package_target.target_id,
        applied_loci=list(baseline_candidate.applied_loci),
        changes=[
            CandidateChange(
                locus_id="prompt.pack.base.system",
                value={
                    "text": (
                        "Keep one durable OpenCode system envelope, make the native Responses posture explicit, "
                        "and treat patch boundaries plus visible tool scope as package-level invariants."
                    )
                },
                rationale="Tie package-level prompt coherence directly to the bounded config and tool-pack surfaces.",
            ),
            CandidateChange(
                locus_id="prompt.pack.base.builder",
                value={
                    "text": (
                        "Prefer the smallest package-scoped build step first, keep tool guidance visible, and avoid "
                        "changes that imply broader write scope or relaxed patch boundaries."
                    )
                },
                rationale="Couple builder guidance to the bounded config and tool-pack invariants.",
            ),
            CandidateChange(
                locus_id="guardrails.diff_policy.patch_splitting",
                value={
                    "policy": "auto_split",
                    "max_files_per_patch": 1,
                    "on_violation": "warn_and_split",
                    "package_note": "keep public OpenCode diffs single-file by default",
                },
                rationale="Make the single-file patch boundary more legible without widening config scope.",
            ),
            CandidateChange(
                locus_id="guardrails.validation.read_before_edit",
                value={
                    "mode": "relaxed",
                    "require_fresh_read": False,
                    "max_age_seconds": 1200,
                    "package_note": "prompt-pack refinements should still read nearby files first when editing",
                },
                rationale="Tighten bounded config behavior just enough to support the package claim honestly.",
            ),
            CandidateChange(
                locus_id="tool.registry.include",
                value={
                    "visible_tools": ["bash", "write"],
                    "tool_guidance_note": "expose only the narrow public OpenCode tool surface on the top-level dossier",
                },
                rationale="Keep tool-pack guidance narrow while making the visible surface more explicit.",
            ),
            CandidateChange(
                locus_id="provider_tools.responses_use_developer_role",
                value={
                    "enabled": True,
                    "package_note": "Mini escalation may inspect ambiguous package candidates, but must preserve developer-role shaping",
                },
                rationale="Keep the native Responses posture explicit under audited Mini escalation.",
            ),
        ],
        provenance={
            "kind": "mixed_evidence_package_triplet_candidate",
            "baseline_candidate_id": baseline_candidate.candidate_id,
            "package": "opencode_1_2_17",
            "member_family_ids": [
                "family.opencode_prompt_pack.v4",
                "family.opencode_bounded_config.v4",
                "family.opencode_tool_guidance_pack.v4",
            ],
        },
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "role": "package_triplet"},
    )

    baseline_materialized = materialize_candidate(
        package_target,
        baseline_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
            "overlay": {"package_role": "atomic_baseline"},
        },
        effective_tool_surface={"tools": ["read_file", "list_dir", "bash", "write"], "exposed_count": 4},
        evaluation_input_compatibility={"replay": True, "schema": 4},
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "role": "atomic_baseline"},
    )
    package_materialized = materialize_candidate(
        package_target,
        package_candidate,
        effective_artifact={
            "artifact_ref": "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
            "overlay": {"package_role": "mixed_evidence_triplet"},
        },
        effective_tool_surface={"tools": ["read_file", "list_dir", "bash", "write"], "exposed_count": 4},
        evaluation_input_compatibility={"replay": True, "schema": 4},
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "role": "package_triplet"},
    )

    transfer_slices = [
        TransferSliceManifest(
            slice_id="package.opencode_1_2_17.current",
            slice_kind="package",
            selector={"artifact_ref": "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml"},
            promotion_role="required",
            visibility="hidden_hold",
            metadata={"lane": "opencode_prompt_config_tool_guidance_package"},
        ),
        TransferSliceManifest(
            slice_id="model_tier.nano_first_openai",
            slice_kind="model_tier",
            selector={"default_model": "gpt-5.4-nano", "escalation_model": "gpt-5.4-mini"},
            promotion_role="required",
            visibility="comparison_visible",
            metadata={"lane": "opencode_prompt_config_tool_guidance_package"},
        ),
        TransferSliceManifest(
            slice_id="tool_pack.opencode_native_responses",
            slice_kind="tool_pack",
            selector={"tool_pack_profile": "opencode-native-responses", "visible_tools": ["bash", "write"]},
            promotion_role="required",
            visibility="comparison_visible",
            metadata={"lane": "opencode_prompt_config_tool_guidance_package"},
        ),
        TransferSliceManifest(
            slice_id="provider_model.openai_gpt_5_4_pair",
            slice_kind="provider_model",
            selector={"provider": "openai", "models": ["gpt-5.4-nano", "gpt-5.4-mini"]},
            promotion_role="claim_supporting",
            visibility="comparison_visible",
            metadata={"lane": "opencode_prompt_config_tool_guidance_package"},
        ),
    ]

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.opencode_prompt_config_tool_guidance.v4",
        suite_kind="mixed_evidence_opencode_package_triplet",
        evaluator_stack=[
            "opencode_prompt_pack_checker.v1",
            "opencode_bounded_config_checker.v1",
            "opencode_tool_pack_checker.v1",
            "package_transfer_verifier.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "escalation_policy": "ambiguous_hidden_hold_or_close_margin_only",
            "audit_escalations": True,
        },
        capture_requirements=[
            "prompt_pack_coherence",
            "bounded_config_preserved",
            "tool_pack_clarity_improved",
            "native_responses_posture_preserved",
            "package_transfer_claim_supported",
        ],
        signal_channels={
            "executable_checks": {
                "source_kind": "executable_check",
                "authority": "primary",
                "used_for": ["bounded_config_fidelity", "tool_pack_scope"],
            },
            "verifier_outputs": {
                "source_kind": "verifier_output",
                "authority": "supporting",
                "used_for": ["package_coherence", "prompt_pack_coherence"],
            },
            "semantic_judge": {
                "source_kind": "model_judge",
                "authority": "advisory",
                "used_for": ["tool_pack_clarity", "prompt_pack_coherence"],
            },
            "model_tier_audit": {
                "source_kind": "model_policy_audit",
                "authority": "hard_gate",
                "used_for": ["promotion_only"],
            },
        },
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "mini_escalation_requires_justification": True,
            "mini_escalation_triggers": ["inconclusive_hidden_hold", "close_validation_margin"],
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_atomic_vs_package_triplet.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "benchmark_summary_json", "model_tier_audit_json"],
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "package": "opencode_1_2_17",
            "model_policy": "nano_first",
            "mini_escalation_policy": "auditable_justified_only",
        },
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.opencode_prompt_config_tool_guidance.v4",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "prompt_pack_coherence": {
                "direction": "maximize",
                "source_metric": "prompt_pack_coherence",
                "promotion_sensitive": True,
            },
            "bounded_config_fidelity": {
                "direction": "maximize",
                "source_metric": "bounded_config_preserved",
                "promotion_sensitive": True,
            },
            "tool_pack_clarity": {
                "direction": "maximize",
                "source_metric": "tool_pack_clarity_improved",
                "promotion_sensitive": True,
            },
            "package_transfer": {
                "direction": "maximize",
                "source_metric": "package_transfer_claim_supported",
                "promotion_sensitive": True,
            },
            "mutation_cost": {
                "direction": "minimize",
                "source_metric": "cost_delta_usd",
                "promotion_sensitive": False,
            },
        },
        penalties={
            "native_posture_drift": {
                "kind": "hard_block",
                "reason": "the OpenCode package may not weaken native Responses posture",
            },
            "patch_boundary_drift": {
                "kind": "hard_block",
                "reason": "the package may not weaken the single-file patch boundary",
            },
        },
        aggregation_rules={"per_sample": "weighted_sum", "global": "package_transfer_first"},
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
            "mini_escalation_on_ambiguity_only": True,
        },
        blocked_channel_annotations={
            "package_transfer": {"blocked_when_missing": ["model_tier_audit", "verifier_outputs"]},
        },
        channel_dependencies={
            "package_transfer": ["prompt_pack_coherence", "bounded_config_fidelity", "tool_pack_clarity"],
        },
        frontier_dimensions=[
            "prompt_pack_coherence",
            "bounded_config_fidelity",
            "tool_pack_clarity",
            "package_transfer",
            "mutation_cost",
        ],
        promotion_annotations={
            "requires_support_sensitive_review": False,
            "review_class": "bounded_e4_package_triplet",
        },
        visibility_annotations={
            "hidden_hold_channels": [
                "prompt_pack_coherence",
                "bounded_config_fidelity",
                "package_transfer",
            ],
        },
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "package": "opencode_1_2_17",
        },
    )

    prompt_family = TargetFamilyManifest(
        family_id="family.opencode_prompt_pack.v4",
        family_kind="prompt_pack_family",
        target_ids=[package_target.target_id],
        family_scope="OpenCode 1.2.17 prompt-pack coherence on the public dossier package.",
        mutable_loci_ids=["prompt.pack.base.system", "prompt.pack.base.builder"],
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        review_class="bounded_e4_package_triplet",
        runtime_context_assumptions={
            "tool_pack_profile": "opencode-native-responses",
            "provider_model_policy": "nano_first",
        },
        promotion_class="bounded_package_prompt_change",
        artifact_refs=list(package_target.baseline_artifact_refs),
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "family_role": "prompt_pack"},
    )
    config_family = TargetFamilyManifest(
        family_id="family.opencode_bounded_config.v4",
        family_kind="bounded_config_family",
        target_ids=[package_target.target_id],
        family_scope="OpenCode 1.2.17 bounded config surface for patch splitting and read-before-edit policy.",
        mutable_loci_ids=[
            "guardrails.diff_policy.patch_splitting",
            "guardrails.validation.read_before_edit",
        ],
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        review_class="bounded_e4_package_triplet",
        runtime_context_assumptions={
            "environment_profile": "workspace-write",
            "requires_single_file_patch_boundary": True,
        },
        promotion_class="bounded_package_config_change",
        artifact_refs=list(package_target.baseline_artifact_refs),
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "family_role": "bounded_config"},
    )
    tool_pack_family = TargetFamilyManifest(
        family_id="family.opencode_tool_guidance_pack.v4",
        family_kind="tool_guidance_pack_family",
        target_ids=[package_target.target_id],
        family_scope="OpenCode 1.2.17 visible tool-pack and developer-role guidance on the public dossier package.",
        mutable_loci_ids=["tool.registry.include", "provider_tools.responses_use_developer_role"],
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        review_class="bounded_e4_package_triplet",
        runtime_context_assumptions={
            "tool_pack_profile": "opencode-native-responses",
            "visible_tools": ["bash", "write"],
        },
        promotion_class="bounded_package_tool_pack_change",
        artifact_refs=list(package_target.baseline_artifact_refs),
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "family_role": "tool_pack"},
    )

    composition = FamilyCompositionManifest(
        composition_id="composition.opencode_prompt_config_tool_guidance.v4",
        member_family_ids=[
            prompt_family.family_id,
            config_family.family_id,
            tool_pack_family.family_id,
        ],
        composition_kind="staged",
        shared_target_scope="opencode_1_2_17_package",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        search_space_id="searchspace.opencode_prompt_config_tool_guidance.v4",
        review_class="bounded_e4_package_triplet",
        promotion_class="bounded_e4_package_triplet_change",
        applicability_scope={
            "artifact_ref": "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
            "tool_pack_profile": "opencode-native-responses",
            "environment_profile": "workspace-write",
            "provider_model_family": "openai-gpt-5.4",
        },
        cross_family_invariants=[
            "bounded-overlay-only",
            "native-responses-posture-preserved",
            "single-file-patch-boundary-preserved",
            "tool-pack-clarity-may-not-weaken-package-boundaries",
        ],
        runtime_context_requirements={
            "tool_pack_profile": "opencode-native-responses",
            "model_policy": "nano_first",
            "mini_escalation_policy": "auditable_justified_only",
        },
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "phase": "v4"},
    )

    search_space = SearchSpaceManifest(
        search_space_id=composition.search_space_id,
        composition_id=composition.composition_id,
        allowed_loci=[locus.locus_id for locus in package_target.mutable_loci],
        mutation_kinds_by_locus={locus.locus_id: ["replace"] for locus in package_target.mutable_loci},
        value_domains_by_locus={
            "prompt.pack.base.system": {"must_preserve_single_system_envelope": True},
            "prompt.pack.base.builder": {"must_preserve_plan_build_shape": True},
            "guardrails.diff_policy.patch_splitting": {"max_files_per_patch": 1, "must_warn_and_split": True},
            "guardrails.validation.read_before_edit": {"mode": "relaxed", "max_age_seconds_max": 1800},
            "tool.registry.include": {"allowed_visible_tools_subset_of": ["bash", "write"]},
            "provider_tools.responses_use_developer_role": {"must_remain_true": True},
        },
        semantic_constraints={
            "guardrails.diff_policy.patch_splitting": {"must_preserve_single_file_patch_boundary": True},
            "tool.registry.include": {"must_not_expose_new_tool_kinds": True},
            "provider_tools.responses_use_developer_role": {"must_preserve_native_responses_posture": True},
        },
        coupled_loci_groups={
            "prompt_and_tool_pack": [
                "prompt.pack.base.system",
                "prompt.pack.base.builder",
                "tool.registry.include",
            ],
            "config_and_responses_posture": [
                "guardrails.diff_policy.patch_splitting",
                "guardrails.validation.read_before_edit",
                "provider_tools.responses_use_developer_role",
            ],
        },
        stage_partitions={
            "prompt_seed": ["prompt.pack.base.system", "prompt.pack.base.builder"],
            "bounded_config_refine": [
                "guardrails.diff_policy.patch_splitting",
                "guardrails.validation.read_before_edit",
            ],
            "tool_pack_finish": [
                "tool.registry.include",
                "provider_tools.responses_use_developer_role",
            ],
        },
        cross_family_constraints={
            "opencode_prompt_config_tool_guidance": {
                "member_family_ids": [
                    prompt_family.family_id,
                    config_family.family_id,
                    tool_pack_family.family_id,
                ],
                "must_preserve_native_responses_posture_if_tool_pack_changes": True,
                "must_preserve_patch_boundary_if_prompt_pack_changes": True,
            }
        },
        invariants=[
            "bounded-overlay-only",
            "native-responses-posture-preserved",
            "single-file-patch-boundary-preserved",
        ],
        unsafe_expansion_notes=[
            "Adding new visible tools is out of scope.",
            "Switching away from native Responses posture is out of scope.",
            "Cross-package prompt synthesis is out of scope.",
        ],
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "phase": "v4"},
    )

    manifest = BenchmarkRunManifest(
        manifest_id="manifest.opencode_prompt_config_tool_guidance.v4",
        benchmark_kind="mixed_evidence_opencode_package_triplet_pack",
        target_id=package_target.target_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        baseline_candidate_id=baseline_candidate.candidate_id,
        environment_domain="workspace-write/opencode-native-responses",
        evaluator_stack=list(evaluation_suite.evaluator_stack),
        comparison_protocol="paired_atomic_vs_package_triplet.v1",
        splits=[
            BenchmarkSplit("train", ["sample.opencode_prompt_config_tool_guidance.train.001"], "mutation_visible"),
            BenchmarkSplit("validation", ["sample.opencode_prompt_config_tool_guidance.validation.001"], "comparison_visible"),
            BenchmarkSplit("hold", ["sample.opencode_prompt_config_tool_guidance.hold.001"], "hidden_hold"),
            BenchmarkSplit("regression", ["sample.opencode_prompt_config_tool_guidance.regression.001"], "comparison_visible"),
        ],
        bucket_tags={
            "sample.opencode_prompt_config_tool_guidance.train.001": ["prompt-pack", "tool-pack", "bounded-config"],
            "sample.opencode_prompt_config_tool_guidance.validation.001": ["package-transfer", "model-tier"],
            "sample.opencode_prompt_config_tool_guidance.hold.001": ["hidden-hold", "package-transfer"],
            "sample.opencode_prompt_config_tool_guidance.regression.001": ["regression", "bounded-config"],
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "escalation_policy": "ambiguous_hidden_hold_or_close_margin_only",
            "audit_escalations": True,
        },
        contamination_notes=[
            "Nano is the default mutation and comparison tier for this OpenCode package lane.",
            "Mini may only be used after Nano on ambiguous hidden-hold or close-margin results and must remain auditable.",
        ],
        transfer_slices=transfer_slices,
        promotion_relevance={
            "requires_support_sensitive_review": False,
            "review_class": composition.review_class,
            "mini_escalation_audit": {
                "default_model": "gpt-5.4-nano",
                "escalation_model": "gpt-5.4-mini",
                "triggered": True,
                "trigger_reason": "ambiguous_hidden_hold_prompt_tool_pack_margin",
            },
        },
        artifact_refs=list(package_target.baseline_artifact_refs),
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "package": "opencode_1_2_17",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_first",
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.opencode_prompt_config_tool_guidance.atomic_vs_package.001",
        parent_candidate_id=baseline_candidate.candidate_id,
        child_candidate_id=package_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=2,
        rationale=(
            "The OpenCode package candidate improves prompt-pack coherence, bounded config clarity, and visible "
            "tool-pack guidance together while preserving native Responses posture and single-file patch boundaries."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/opencode_prompt_config_tool_guidance/package_triplet_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "prompt_pack_coherence_delta": 0.1,
            "bounded_config_fidelity_delta": 0.04,
            "tool_pack_clarity_delta": 0.12,
            "package_transfer_delta": 0.08,
            "mini_escalation_confirmed_hold": True,
        },
        better_candidate_id=package_candidate.candidate_id,
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "composition_id": composition.composition_id,
            "baseline_kind": "atomic_sequential_baseline",
            "model_policy": "nano_first",
            "mini_escalation_triggered": True,
            "mini_escalation_reason": "ambiguous_hidden_hold_prompt_tool_pack_margin",
        },
    )

    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.opencode_prompt_config_tool_guidance.package.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=package_candidate.candidate_id,
        per_sample_components={
            "sample.opencode_prompt_config_tool_guidance.train.001": {
                "prompt_pack_coherence": 0.86,
                "bounded_config_fidelity": 0.96,
                "tool_pack_clarity": 0.88,
                "package_transfer": 0.82,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.validation.001": {
                "prompt_pack_coherence": 0.84,
                "bounded_config_fidelity": 0.95,
                "tool_pack_clarity": 0.9,
                "package_transfer": 0.8,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.hold.001": {
                "prompt_pack_coherence": 0.81,
                "bounded_config_fidelity": 0.97,
                "tool_pack_clarity": 0.84,
                "package_transfer": 0.79,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.regression.001": {
                "prompt_pack_coherence": 0.8,
                "bounded_config_fidelity": 0.96,
                "tool_pack_clarity": 0.83,
                "package_transfer": 0.78,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "prompt-pack": {"prompt_pack_coherence": 0.86, "tool_pack_clarity": 0.88},
            "package-transfer": {"package_transfer": 0.8, "bounded_config_fidelity": 0.95},
        },
        aggregate_objectives={
            "prompt_pack_coherence": 0.8275,
            "bounded_config_fidelity": 0.96,
            "tool_pack_clarity": 0.8625,
            "package_transfer": 0.7975,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 2,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": True,
            "mini_escalation_triggered": True,
        },
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "verifier_outputs": {"status": "pass", "authority": "supporting"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "model_tier_audit": {"status": "audited_pass", "authority": "hard_gate"},
        },
        slice_status={
            "package.opencode_1_2_17.current": {"status": "pass", "promotion_role": "required"},
            "model_tier.nano_first_openai": {"status": "audited_pass", "promotion_role": "required"},
            "tool_pack.opencode_native_responses": {"status": "pass", "promotion_role": "required"},
            "provider_model.openai_gpt_5_4_pair": {"status": "pass", "promotion_role": "claim_supporting"},
        },
        member_family_breakdowns={
            prompt_family.family_id: {"prompt_pack_coherence": 0.8275},
            config_family.family_id: {"bounded_config_fidelity": 0.96},
            tool_pack_family.family_id: {"tool_pack_clarity": 0.8625, "package_transfer": 0.7975},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/opencode_prompt_config_tool_guidance/objective_breakdown_package_triplet.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_first",
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.opencode_prompt_pack.v4": {
                    "status": "present",
                    "drivers": ["prompt_pack_coherence"],
                },
                "family.opencode_bounded_config.v4": {
                    "status": "present",
                    "drivers": ["bounded_config_fidelity"],
                },
                "family.opencode_tool_guidance_pack.v4": {
                    "status": "present",
                    "drivers": ["tool_pack_clarity", "package_transfer"],
                },
            },
        },
    )

    promotion_summary = build_promotion_evidence_summary(
        summary_id="summary.opencode_prompt_config_tool_guidance.package.001",
        candidate_id=package_candidate.candidate_id,
        benchmark_manifest=manifest,
        comparison_results=[comparison],
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        family_composition=composition,
        search_space=search_space,
        objective_breakdown_results=[objective_breakdown],
        review_required=True,
        metadata={"lane": "opencode_prompt_config_tool_guidance_package", "phase": "v4"},
    )

    benchmark_result = BenchmarkRunResult(
        run_id="benchmark_run.opencode_prompt_config_tool_guidance.v4",
        manifest_id=manifest.manifest_id,
        candidate_ids=[baseline_candidate.candidate_id, package_candidate.candidate_id],
        comparison_results=[comparison],
        aggregate_metrics={
            "atomic_baseline_score": 0.76,
            "package_triplet_score": 0.87,
            "mixed_evidence_complete": True,
        },
        bucket_outcomes={
            "prompt-pack": {"outcome": "child_win"},
            "package-transfer": {"outcome": "child_win"},
        },
        variance_summary={
            "trial_count": 2,
            "stochasticity_class": "deterministic",
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "mini_escalation_triggered": True,
            "mini_escalation_reason": "ambiguous_hidden_hold_prompt_tool_pack_margin",
        },
        cost_support_evidence_slices={
            "bounded_config_preserved": True,
            "tool_pack_scope_preserved": True,
            "model_policy": "nano_first",
        },
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/opencode_prompt_config_tool_guidance/benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "promotion_summary_id": promotion_summary.summary_id,
            "eligible_for_promotion": False,
            "requires_review": True,
            "blocked_reason": "package triplet wins with audited Mini escalation require explicit review before promotion",
            "transfer_slice_ids": sorted(slice_item.slice_id for slice_item in transfer_slices),
            "mini_escalation_audit": promotion_summary.model_tier_audit,
        },
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "package": "opencode_1_2_17",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "model_policy": "nano_first",
        },
    )

    staged_request = _build_backend_request_for_package_example(
        {
            "target": package_target,
            "dataset": dataset,
            "baseline_candidate": baseline_candidate,
            "baseline_materialized_candidate": baseline_materialized,
        },
        request_id="backend_request.opencode_prompt_config_tool_guidance.v4",
        evaluation_id="eval.backend.opencode_prompt_config_tool_guidance.v4",
        evaluator_id="opencode_mixed_evidence_package_checker.v4",
        wrongness_class="correctness.result_mismatch",
        likely_repair_locus="prompt.pack.base.system",
    )
    staged_optimizer_request = StagedOptimizerRequest(
        request_id="staged_request.opencode_prompt_config_tool_guidance.v4",
        backend_request=staged_request,
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        search_space=search_space,
        family_composition=composition,
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "search_policy_signal": "ambiguous_hidden_hold",
            "transfer_slice_status": objective_breakdown.slice_status,
            "model_tier_audit": promotion_summary.model_tier_audit,
            "optimistic_scope_blocked": False,
        },
    )
    staged_result = run_staged_optimizer(staged_optimizer_request)

    return {
        "target": package_target,
        "dataset": dataset,
        "baseline_candidate": baseline_candidate,
        "package_candidate": package_candidate,
        "baseline_materialized_candidate": baseline_materialized,
        "package_materialized_candidate": package_materialized,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "family_composition": composition,
        "search_space": search_space,
        "transfer_slices": transfer_slices,
        "manifest": manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "promotion_summary": promotion_summary,
        "benchmark_result": benchmark_result,
        "staged_request": staged_optimizer_request,
        "staged_result": staged_result,
    }


def build_opencode_prompt_config_tool_guidance_package_example_payload() -> Dict[str, object]:
    example = build_opencode_prompt_config_tool_guidance_package_example()
    return {
        "target": example["target"].to_dict(),
        "dataset": example["dataset"].to_dict(),
        "baseline_candidate": example["baseline_candidate"].to_dict(),
        "package_candidate": example["package_candidate"].to_dict(),
        "baseline_materialized_candidate": example["baseline_materialized_candidate"].to_dict(),
        "package_materialized_candidate": example["package_materialized_candidate"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "family_composition": example["family_composition"].to_dict(),
        "search_space": example["search_space"].to_dict(),
        "transfer_slices": [item.to_dict() for item in example["transfer_slices"]],
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "promotion_summary": example["promotion_summary"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
        "staged_request": example["staged_request"].to_dict(),
        "staged_result": example["staged_result"].to_dict(),
    }


def build_opencode_prompt_config_tool_guidance_verifier_follow_on_example() -> Dict[str, object]:
    """Build one narrow verifier-assisted follow-on on the bounded OpenCode V4 package lane."""

    example = build_opencode_prompt_config_tool_guidance_package_example()
    package_candidate = example["package_candidate"]
    composition = example["family_composition"]
    evaluation_suite = example["evaluation_suite"]
    objective_suite = example["objective_suite"]
    search_space = example["search_space"]
    manifest = example["manifest"]
    transfer_slices = example["transfer_slices"]
    assert isinstance(package_candidate, CandidateBundle)
    assert isinstance(composition, FamilyCompositionManifest)
    assert isinstance(evaluation_suite, EvaluationSuiteManifest)
    assert isinstance(objective_suite, ObjectiveSuiteManifest)
    assert isinstance(search_space, SearchSpaceManifest)
    assert isinstance(manifest, BenchmarkRunManifest)

    refined_candidate = CandidateBundle(
        candidate_id="cand.opencode_prompt_config_tool_guidance.verifier_refined.001",
        source_target_id=example["target"].target_id,
        applied_loci=[
            "prompt.pack.base.builder",
            "guardrails.validation.read_before_edit",
        ],
        changes=[
            CandidateChange(
                locus_id="prompt.pack.base.builder",
                value={
                    "text": (
                        "Keep the build stage compact, package-scoped, and explicitly aligned to native "
                        "Responses posture, single-file patch boundaries, and read-before-edit validation."
                    )
                },
                rationale=(
                    "Verifier-guided refinement tightens prompt-pack coherence on the held package slice "
                    "without widening the OpenCode package scope."
                ),
            ),
            CandidateChange(
                locus_id="guardrails.validation.read_before_edit",
                value={"mode": "strict", "require_fresh_read": True, "max_age_seconds": 900},
                rationale=(
                    "Specialize the bounded config member to strengthen hidden-hold package safety while "
                    "remaining inside the declared config locus."
                ),
            ),
        ],
        provenance={
            "kind": "package_verifier_follow_on",
            "baseline_candidate_id": package_candidate.candidate_id,
            "composition_id": composition.composition_id,
        },
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "role": "verifier_follow_on",
            "composition_id": composition.composition_id,
            "package": "opencode_1_2_17",
            "non_kernel": True,
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.opencode_prompt_config_tool_guidance.package_vs_verifier_refined.001",
        parent_candidate_id=package_candidate.candidate_id,
        child_candidate_id=refined_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale=(
            "The verifier-assisted follow-on improves prompt-pack coherence and read-before-edit safety on "
            "the bounded OpenCode package slice without widening tool-pack scope or relaxing native Responses posture."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/opencode_prompt_config_tool_guidance/verifier_follow_on_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "prompt_pack_coherence_delta": 0.05,
            "bounded_config_fidelity_delta": 0.03,
            "package_transfer_delta": 0.02,
            "held_out_package_guard": True,
            "verifier_confirmed": True,
        },
        better_candidate_id=refined_candidate.candidate_id,
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "composition_id": composition.composition_id,
            "experiment_kind": "verifier_augmented_package_refinement",
            "model_policy": "nano_first",
        },
    )

    refined_objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.opencode_prompt_config_tool_guidance.verifier_refined.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=refined_candidate.candidate_id,
        per_sample_components={
            "sample.opencode_prompt_config_tool_guidance.train.001": {
                "prompt_pack_coherence": 0.89,
                "bounded_config_fidelity": 0.98,
                "tool_pack_clarity": 0.88,
                "package_transfer": 0.84,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.validation.001": {
                "prompt_pack_coherence": 0.87,
                "bounded_config_fidelity": 0.97,
                "tool_pack_clarity": 0.89,
                "package_transfer": 0.82,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.hold.001": {
                "prompt_pack_coherence": 0.85,
                "bounded_config_fidelity": 0.98,
                "tool_pack_clarity": 0.84,
                "package_transfer": 0.81,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.regression.001": {
                "prompt_pack_coherence": 0.82,
                "bounded_config_fidelity": 0.97,
                "tool_pack_clarity": 0.83,
                "package_transfer": 0.79,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "prompt-pack": {"prompt_pack_coherence": 0.89, "tool_pack_clarity": 0.88},
            "package-transfer": {"package_transfer": 0.82, "bounded_config_fidelity": 0.97},
        },
        aggregate_objectives={
            "prompt_pack_coherence": 0.8575,
            "bounded_config_fidelity": 0.975,
            "tool_pack_clarity": 0.86,
            "package_transfer": 0.815,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": evaluation_suite.stochasticity_class,
            "trial_count": 1,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": True,
            "mini_escalation_triggered": False,
        },
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "verifier_outputs": {"status": "pass", "authority": "supporting"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "model_tier_audit": {"status": "audited_pass", "authority": "hard_gate"},
        },
        slice_status={
            "package.opencode_1_2_17.current": {"status": "pass", "promotion_role": "required"},
            "model_tier.nano_first_openai": {"status": "audited_pass", "promotion_role": "required"},
            "tool_pack.opencode_native_responses": {"status": "pass", "promotion_role": "required"},
            "provider_model.openai_gpt_5_4_pair": {"status": "pass", "promotion_role": "claim_supporting"},
        },
        member_family_breakdowns={
            "family.opencode_prompt_pack.v4": {"prompt_pack_coherence": 0.8575},
            "family.opencode_bounded_config.v4": {"bounded_config_fidelity": 0.975},
            "family.opencode_tool_guidance_pack.v4": {
                "tool_pack_clarity": 0.86,
                "package_transfer": 0.815,
            },
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/opencode_prompt_config_tool_guidance/objective_breakdown_verifier_refined.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "composition_id": composition.composition_id,
            "search_space_id": search_space.search_space_id,
            "experiment_kind": "verifier_augmented_package_refinement",
            "package_follow_on": True,
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.opencode_prompt_pack.v4": {
                    "status": "present",
                    "drivers": ["prompt_pack_coherence"],
                },
                "family.opencode_bounded_config.v4": {
                    "status": "present",
                    "drivers": ["bounded_config_fidelity"],
                },
                "family.opencode_tool_guidance_pack.v4": {
                    "status": "present",
                    "drivers": ["tool_pack_clarity", "package_transfer"],
                },
            },
        },
    )

    verifier_experiment = VerifierAugmentedExperimentResult(
        experiment_id="verifier_experiment.opencode_prompt_config_tool_guidance.v4",
        experiment_kind="verifier_augmented_package_refinement",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        target_family_id="family.opencode_bounded_config.v4",
        search_space_id=search_space.search_space_id,
        baseline_candidate_id=package_candidate.candidate_id,
        refined_candidate_id=refined_candidate.candidate_id,
        verifier_stack=[
            *evaluation_suite.evaluator_stack,
            "opencode_package_scope_verifier.v1",
        ],
        focus_sample_ids=manifest.hidden_hold_sample_ids() or manifest.sample_ids(),
        comparison_result_id=comparison.comparison_id,
        objective_breakdown_result_id=refined_objective_breakdown.result_id,
        outcome="accepted",
        rationale=(
            "This narrow follow-on stays inside the bounded OpenCode package lane, specializing the prompt-pack "
            "and bounded-config members while preserving transfer-slice discipline, native Responses posture, "
            "and the declared package scope."
        ),
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/opencode_prompt_config_tool_guidance/verifier_follow_on_summary.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "opencode_prompt_config_tool_guidance_package",
            "phase": "v4",
            "backend_only": True,
            "non_kernel": True,
            "darwin_boundary": "not_reopened",
            "family_bound": True,
            "composition_id": composition.composition_id,
            "member_family_ids": list(composition.member_family_ids),
            "transfer_slice_ids": [item.slice_id for item in transfer_slices],
            "specialization_scope": "prompt_pack_and_bounded_config_members_inside_package_lane",
            "model_policy": "nano_first",
            "package": "opencode_1_2_17",
        },
    )

    return {
        "package_example": example,
        "refined_candidate": refined_candidate,
        "comparison_result": comparison,
        "objective_breakdown_result": refined_objective_breakdown,
        "verifier_experiment": verifier_experiment,
    }


def build_opencode_prompt_config_tool_guidance_verifier_follow_on_example_payload() -> Dict[str, object]:
    example = build_opencode_prompt_config_tool_guidance_verifier_follow_on_example()
    return {
        "package_example": {
            "evaluation_suite": example["package_example"]["evaluation_suite"].to_dict(),
            "objective_suite": example["package_example"]["objective_suite"].to_dict(),
            "family_composition": example["package_example"]["family_composition"].to_dict(),
            "search_space": example["package_example"]["search_space"].to_dict(),
            "transfer_slices": [item.to_dict() for item in example["package_example"]["transfer_slices"]],
            "manifest": example["package_example"]["manifest"].to_dict(),
            "package_candidate": example["package_example"]["package_candidate"].to_dict(),
        },
        "refined_candidate": example["refined_candidate"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "verifier_experiment": example["verifier_experiment"].to_dict(),
    }


def _clone_comparison_result(
    comparison: CandidateComparisonResult,
    **updates: object,
) -> CandidateComparisonResult:
    payload = comparison.to_dict()
    payload.update(updates)
    return CandidateComparisonResult.from_dict(payload)


def _clone_benchmark_run_result(
    result: BenchmarkRunResult,
    **updates: object,
) -> BenchmarkRunResult:
    payload = result.to_dict()
    payload.update(updates)
    payload["comparison_results"] = [
        item.to_dict() if isinstance(item, CandidateComparisonResult) else item
        for item in payload.get("comparison_results", [])
    ]
    return BenchmarkRunResult.from_dict(payload)


def _clone_promotion_evidence_summary(
    summary: PromotionEvidenceSummary,
    **updates: object,
) -> PromotionEvidenceSummary:
    payload = summary.to_dict()
    payload.update(updates)
    payload["transfer_slices"] = [
        item.to_dict() if isinstance(item, TransferSliceManifest) else item
        for item in payload.get("transfer_slices", [])
    ]
    payload["transfer_cohorts"] = [
        item.to_dict() if isinstance(item, TransferCohortManifest) else item
        for item in payload.get("transfer_cohorts", [])
    ]
    return PromotionEvidenceSummary.from_dict(payload)


def _build_backend_request_for_family_example(
    example: Dict[str, object],
    *,
    request_id: str,
    evaluation_id: str,
    evaluator_id: str,
    wrongness_class: str,
    likely_repair_locus: str,
) -> ReflectiveParetoBackendRequest:
    target = example["target"]
    dataset = example["dataset"]
    parent_candidate = example["parent_candidate"]
    parent_materialized_candidate = example["parent_materialized_candidate"]
    assert isinstance(target, OptimizationTarget)
    assert isinstance(dataset, OptimizationDataset)
    assert isinstance(parent_candidate, CandidateBundle)
    assert isinstance(parent_materialized_candidate, MaterializedCandidate)
    sample = dataset.samples[0]
    evaluation = EvaluationRecord(
        evaluation_id=evaluation_id,
        target_id=target.target_id,
        candidate_id=parent_candidate.candidate_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        sample_id=sample.sample_id,
        evaluator_id=evaluator_id,
        evaluator_version="v2",
        status="completed",
        outcome="failed",
        started_at="2026-03-18T10:00:00.000Z",
        completed_at="2026-03-18T10:00:01.000Z",
        duration_ms=1000,
        raw_evidence_refs=[
            ArtifactRef(
                ref=f"artifacts/optimization/{request_id}/baseline_eval.json",
                media_type="application/json",
            )
        ],
        normalized_diagnostics=[
            DiagnosticBundle(
                bundle_id=f"bundle.{evaluation_id}",
                evaluation_id=evaluation_id,
                evaluator_mode="replay",
                determinism_class="deterministic",
                entries=[
                    DiagnosticEntry(
                        diagnostic_id=f"diag.{evaluation_id}",
                        kind="wrongness",
                        severity="error",
                        message="baseline family candidate shows a structured repair opportunity",
                        locus_id=likely_repair_locus,
                        evidence_refs=[
                            ArtifactRef(
                                ref=f"artifacts/optimization/{request_id}/diagnostic.json",
                                media_type="application/json",
                            )
                        ],
                    )
                ],
                cache_identity={"key": f"cache.{evaluation_id}", "version": "1"},
                retry_policy_hint={"max_trials": 1},
                reproducibility_notes={"family_bound": True},
            )
        ],
        wrongness_reports=[
            WrongnessReport(
                wrongness_id=f"wrongness.{evaluation_id}",
                wrongness_class=wrongness_class,
                failure_locus=likely_repair_locus,
                explanation="baseline family candidate still exposes a bounded repair locus",
                confidence=0.9,
                supporting_evidence_refs=[
                    ArtifactRef(
                        ref=f"artifacts/optimization/{request_id}/wrongness.json",
                        media_type="application/json",
                    )
                ],
                likely_repair_locus=likely_repair_locus,
            )
        ],
        support_envelope_snapshot=target.support_envelope,
        evaluation_input_compatibility={
            **parent_materialized_candidate.evaluation_input_compatibility,
            "conformance_bundle": "codex-e4",
        },
        gate_results={"replay_gate_green": True, "conformance_gate_green": True},
        metadata={"family_bound": True, "request_id": request_id},
    )
    return ReflectiveParetoBackendRequest(
        request_id=request_id,
        target=target,
        baseline_candidate=parent_candidate,
        baseline_materialized_candidate=parent_materialized_candidate,
        dataset=dataset,
        evaluations=[evaluation],
        active_sample_id=sample.sample_id,
        execution_context=OptimizationExecutionContext(
            target_id=target.target_id,
            sample_id=sample.sample_id,
            runtime_context=sample.runtime_context(),
            evaluation_input_compatibility=parent_materialized_candidate.evaluation_input_compatibility,
            metadata={"family_bound": True, "dataset_id": dataset.dataset_id},
        ),
        mutation_bounds=MutationBounds(max_changed_loci=2, max_changed_artifacts=1, max_total_value_bytes=1600),
        max_proposals=2,
        metadata={"family_bound": True},
    )


def _build_backend_request_for_package_example(
    example: Dict[str, object],
    *,
    request_id: str,
    evaluation_id: str,
    evaluator_id: str,
    wrongness_class: str,
    likely_repair_locus: str,
) -> ReflectiveParetoBackendRequest:
    target = example["target"]
    dataset = example["dataset"]
    baseline_candidate = example["baseline_candidate"]
    baseline_materialized_candidate = example["baseline_materialized_candidate"]
    assert isinstance(target, OptimizationTarget)
    assert isinstance(dataset, OptimizationDataset)
    assert isinstance(baseline_candidate, CandidateBundle)
    assert isinstance(baseline_materialized_candidate, MaterializedCandidate)
    sample = dataset.samples[0]
    evaluation = EvaluationRecord(
        evaluation_id=evaluation_id,
        target_id=target.target_id,
        candidate_id=baseline_candidate.candidate_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        sample_id=sample.sample_id,
        evaluator_id=evaluator_id,
        evaluator_version="v4",
        status="completed",
        outcome="failed",
        started_at="2026-03-19T14:00:00.000Z",
        completed_at="2026-03-19T14:00:02.000Z",
        duration_ms=2000,
        raw_evidence_refs=[
            ArtifactRef(
                ref=f"artifacts/optimization/{request_id}/baseline_eval.json",
                media_type="application/json",
            )
        ],
        normalized_diagnostics=[
            DiagnosticBundle(
                bundle_id=f"bundle.{evaluation_id}",
                evaluation_id=evaluation_id,
                evaluator_mode="replay",
                determinism_class="deterministic",
                entries=[
                    DiagnosticEntry(
                        diagnostic_id=f"diag.{evaluation_id}",
                        kind="wrongness",
                        severity="error",
                        message="baseline package candidate still exposes a bounded mixed-evidence repair opportunity",
                        locus_id=likely_repair_locus,
                        evidence_refs=[
                            ArtifactRef(
                                ref=f"artifacts/optimization/{request_id}/diagnostic.json",
                                media_type="application/json",
                            )
                        ],
                    )
                ],
                cache_identity={"key": f"cache.{evaluation_id}", "version": "1"},
                retry_policy_hint={"max_trials": 1},
                reproducibility_notes={"package_bound": True},
            )
        ],
        wrongness_reports=[
            WrongnessReport(
                wrongness_id=f"wrongness.{evaluation_id}",
                wrongness_class=wrongness_class,
                failure_locus=likely_repair_locus,
                explanation="baseline package candidate still exposes a bounded mixed-evidence package repair locus",
                confidence=0.9,
                supporting_evidence_refs=[
                    ArtifactRef(
                        ref=f"artifacts/optimization/{request_id}/wrongness.json",
                        media_type="application/json",
                    )
                ],
                likely_repair_locus=likely_repair_locus,
            )
        ],
        support_envelope_snapshot=target.support_envelope,
        evaluation_input_compatibility={
            **baseline_materialized_candidate.evaluation_input_compatibility,
            "conformance_bundle": "codex-e4",
        },
        gate_results={"replay_gate_green": True, "conformance_gate_green": True},
        metadata={"package_bound": True, "request_id": request_id},
    )
    return ReflectiveParetoBackendRequest(
        request_id=request_id,
        target=target,
        baseline_candidate=baseline_candidate,
        baseline_materialized_candidate=baseline_materialized_candidate,
        dataset=dataset,
        evaluations=[evaluation],
        active_sample_id=sample.sample_id,
        execution_context=OptimizationExecutionContext(
            target_id=target.target_id,
            sample_id=sample.sample_id,
            runtime_context=sample.runtime_context(),
            evaluation_input_compatibility=baseline_materialized_candidate.evaluation_input_compatibility,
            metadata={"package_bound": True, "dataset_id": dataset.dataset_id},
        ),
        mutation_bounds=MutationBounds(max_changed_loci=3, max_changed_artifacts=1, max_total_value_bytes=2600),
        max_proposals=3,
        metadata={"package_bound": True},
    )


def build_codex_opencode_transfer_cohort_example() -> Dict[str, object]:
    """Build the first V5 two-package transfer cohort over the live V4 package lanes."""

    codex = build_support_execution_tool_guidance_coding_overlay_package_example()
    opencode = build_opencode_prompt_config_tool_guidance_package_example()
    codex_package_candidate = codex["package_candidate"]
    opencode_package_candidate = opencode["package_candidate"]
    codex_composition = codex["family_composition"]
    opencode_composition = opencode["family_composition"]
    codex_search_space = codex["search_space"]
    opencode_search_space = opencode["search_space"]
    assert isinstance(codex_package_candidate, CandidateBundle)
    assert isinstance(opencode_package_candidate, CandidateBundle)
    assert isinstance(codex_composition, FamilyCompositionManifest)
    assert isinstance(opencode_composition, FamilyCompositionManifest)
    assert isinstance(codex_search_space, SearchSpaceManifest)
    assert isinstance(opencode_search_space, SearchSpaceManifest)

    codex_package_slice = next(
        item.slice_id for item in codex["transfer_slices"] if item.slice_kind == "package"
    )
    opencode_package_slice = next(
        item.slice_id for item in opencode["transfer_slices"] if item.slice_kind == "package"
    )
    shared_model_tier_slice = next(
        item.slice_id for item in codex["transfer_slices"] if item.slice_kind == "model_tier"
    )

    cohort = TransferCohortManifest(
        cohort_id="cohort.codex_dossier_current.opencode_1_2_17.v5",
        cohort_kind="bounded_two_package_transfer",
        member_slice_ids=[
            codex_package_slice,
            opencode_package_slice,
            shared_model_tier_slice,
        ],
        claim_scope={
            "package_ids": ["codex_dossier.current", "opencode_1_2_17.current"],
            "shared_family_emphasis": [
                "tool_guidance_clarity",
                "bounded_edit_support_honesty",
            ],
            "max_package_count": 2,
            "model_policy": "nano_only",
        },
        coverage_policy={
            "requires_hidden_hold_per_package": True,
            "requires_regression_per_package": True,
            "claim_tiers": ["package_local", "transfer_supported", "cohort_supported"],
            "requires_shared_model_tier": True,
        },
        metadata={
            "phase": "v5",
            "non_kernel": True,
            "darwin_boundary": "not_reopened",
        },
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.transfer_cohort.codex_opencode.v5",
        suite_kind="bounded_transfer_cohort_package_pair",
        evaluator_stack=[
            "tool_guidance_checker.v1",
            "bounded_edit_scope_checker.v1",
            "support_honesty_checker.v1",
            "transfer_claim_checker.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "escalation_policy": "disabled_for_first_cohort_baseline",
            "budget_policy": "nano_only_first_cohort",
        },
        capture_requirements=[
            "guidance_clarity",
            "bounded_edit_honesty",
            "package_scope_integrity",
            "transfer_claim_support",
        ],
        signal_channels={
            "executable_checks": {"source_kind": "deterministic_checker"},
            "semantic_judge": {"source_kind": "model_judge"},
            "transfer_claim_review": {"source_kind": "cohort_gate"},
        },
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "model_policy": "nano_only",
            "claim_scope": "bounded_two_package_transfer",
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_local_vs_transfer_cohort.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "cohort_benchmark_summary_json"],
        metadata={
            "phase": "v5",
            "evaluation_truth": "primary",
            "reward_like_ranking": "private_only",
            "model_policy": "nano_only",
            "stopping_policy": "stop_if_required_transfer_slices_fail_on_both_packages",
        },
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.transfer_cohort.codex_opencode.v5",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "guidance_clarity": {
                "direction": "maximize",
                "source_metric": "guidance_clarity",
                "promotion_sensitive": True,
            },
            "bounded_edit_honesty": {
                "direction": "maximize",
                "source_metric": "bounded_edit_honesty",
                "promotion_sensitive": True,
            },
            "package_scope_integrity": {
                "direction": "maximize",
                "source_metric": "package_scope_integrity",
                "promotion_sensitive": True,
            },
            "mutation_cost": {
                "direction": "minimize",
                "source_metric": "cost_delta_usd",
                "promotion_sensitive": False,
            },
        },
        penalties={
            "scope_expansion": {
                "kind": "hard_block",
                "reason": "transfer cohorts may not widen package applicability implicitly",
            },
            "support_honesty_regression": {
                "kind": "hard_block",
                "reason": "shared bounded-edit and support honesty subset must remain intact",
            },
        },
        aggregation_rules={"per_sample": "weighted_sum", "global": "cohort_transfer_first"},
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
            "mini_escalation_allowed": False,
        },
        blocked_channel_annotations={
            "package_scope_integrity": {"blocked_by": ["scope_expansion"]},
        },
        channel_dependencies={
            "package_scope_integrity": ["guidance_clarity", "bounded_edit_honesty"],
        },
        frontier_dimensions=[
            "guidance_clarity",
            "bounded_edit_honesty",
            "package_scope_integrity",
            "mutation_cost",
        ],
        promotion_annotations={
            "requires_transfer_cohort_support": True,
            "review_class": "bounded_transfer_cohort",
        },
        visibility_annotations={
            "hidden_hold_channels": [
                "guidance_clarity",
                "bounded_edit_honesty",
                "package_scope_integrity",
            ],
        },
        metadata={
            "phase": "v5",
            "reward_like_ranking": "private_only",
        },
    )

    codex_change_map = {change.locus_id: change for change in codex_package_candidate.changes}
    opencode_change_map = {change.locus_id: change for change in opencode_package_candidate.changes}

    codex_tool_value = dict(codex_change_map["tool.render.exec_command"].value)
    codex_tool_value["description"] = (
        "Run commands inside the declared support envelope, and keep tool guidance aligned with bounded "
        "edit honesty across Codex and OpenCode transfer cells."
    )
    codex_edit_value = dict(codex_change_map["prompt.section.editing_policy"].value)
    codex_edit_value["text"] = (
        "Prefer apply_patch and narrow diffs; keep support-sensitive review burden and bounded package edit "
        "scope explicit across the transfer cohort."
    )
    codex_cohort_candidate = CandidateBundle(
        candidate_id="cand.transfer_cohort.codex_dossier.shared.001",
        source_target_id=codex["target"].target_id,
        applied_loci=[
            "tool.render.exec_command",
            "prompt.section.editing_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="tool.render.exec_command",
                value=codex_tool_value,
                rationale="Align tool guidance with the shared transfer-cohort bounded-edit/scope doctrine.",
            ),
            CandidateChange(
                locus_id="prompt.section.editing_policy",
                value=codex_edit_value,
                rationale="Make the shared bounded-edit and support honesty subset explicit on the Codex package.",
            ),
        ],
        provenance={
            "kind": "transfer_cohort_candidate",
            "baseline_candidate_id": codex_package_candidate.candidate_id,
            "transfer_cohort_id": cohort.cohort_id,
        },
        metadata={
            "lane": "codex_opencode_transfer_cohort",
            "role": "cohort_staged_baseline",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
        },
    )

    opencode_builder_value = dict(opencode_change_map["prompt.pack.base.builder"].value)
    opencode_builder_value["text"] = (
        "Keep the build stage compact, package-scoped, and aligned to the shared transfer-cohort doctrine "
        "for native Responses posture, bounded edits, and explicit tool guidance."
    )
    opencode_validation_value = dict(opencode_change_map["guardrails.validation.read_before_edit"].value)
    opencode_validation_value.update(
        {"mode": "strict", "require_fresh_read": True, "max_age_seconds": 900}
    )
    opencode_cohort_candidate = CandidateBundle(
        candidate_id="cand.transfer_cohort.opencode.shared.001",
        source_target_id=opencode["target"].target_id,
        applied_loci=[
            "prompt.pack.base.builder",
            "guardrails.validation.read_before_edit",
        ],
        changes=[
            CandidateChange(
                locus_id="prompt.pack.base.builder",
                value=opencode_builder_value,
                rationale="Align the OpenCode prompt-pack member to the shared transfer-cohort bounded-edit doctrine.",
            ),
            CandidateChange(
                locus_id="guardrails.validation.read_before_edit",
                value=opencode_validation_value,
                rationale="Tighten the bounded config member under the shared transfer-cohort support honesty subset.",
            ),
        ],
        provenance={
            "kind": "transfer_cohort_candidate",
            "baseline_candidate_id": opencode_package_candidate.candidate_id,
            "transfer_cohort_id": cohort.cohort_id,
        },
        metadata={
            "lane": "codex_opencode_transfer_cohort",
            "role": "cohort_staged_baseline",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
        },
    )

    codex_manifest = BenchmarkRunManifest(
        manifest_id="manifest.transfer_cohort.codex_dossier.v5",
        benchmark_kind="transfer_cohort_package_cell",
        target_id=codex["target"].target_id,
        dataset_id=codex["dataset"].dataset_id,
        dataset_version=codex["dataset"].dataset_version,
        baseline_candidate_id=codex["baseline_candidate"].candidate_id,
        environment_domain=codex["manifest"].environment_domain,
        evaluator_stack=list(evaluation_suite.evaluator_stack),
        comparison_protocol="paired_local_vs_transfer_cohort.v1",
        splits=[BenchmarkSplit.from_dict(item.to_dict()) for item in codex["manifest"].splits],
        bucket_tags={key: list(value) for key, value in codex["manifest"].bucket_tags.items()},
        stochasticity_class="deterministic",
        rerun_policy=dict(evaluation_suite.rerun_policy),
        contamination_notes=[
            "No hidden cohort identity may be used during mutation.",
            "This first transfer cohort cell is Nano-only.",
        ],
        transfer_cohort_ids=[cohort.cohort_id],
        transfer_slices=list(codex["transfer_slices"]),
        promotion_relevance={
            "claim_scope": "bounded_two_package_transfer",
            "requires_transfer_cohort_support": True,
            "transfer_cohort_ids": [cohort.cohort_id],
        },
        artifact_refs=list(codex["manifest"].artifact_refs),
        metadata={
            "phase": "v5",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
            "model_policy": "nano_only",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": codex_composition.composition_id,
        },
    )

    opencode_manifest = BenchmarkRunManifest(
        manifest_id="manifest.transfer_cohort.opencode_1_2_17.v5",
        benchmark_kind="transfer_cohort_package_cell",
        target_id=opencode["target"].target_id,
        dataset_id=opencode["dataset"].dataset_id,
        dataset_version=opencode["dataset"].dataset_version,
        baseline_candidate_id=opencode["baseline_candidate"].candidate_id,
        environment_domain=opencode["manifest"].environment_domain,
        evaluator_stack=list(evaluation_suite.evaluator_stack),
        comparison_protocol="paired_local_vs_transfer_cohort.v1",
        splits=[BenchmarkSplit.from_dict(item.to_dict()) for item in opencode["manifest"].splits],
        bucket_tags={key: list(value) for key, value in opencode["manifest"].bucket_tags.items()},
        stochasticity_class="deterministic",
        rerun_policy=dict(evaluation_suite.rerun_policy),
        contamination_notes=[
            "No hidden cohort identity may be used during mutation.",
            "This first transfer cohort cell is Nano-only and may not silently escalate to Mini.",
        ],
        transfer_cohort_ids=[cohort.cohort_id],
        transfer_slices=list(opencode["transfer_slices"]),
        promotion_relevance={
            "claim_scope": "bounded_two_package_transfer",
            "requires_transfer_cohort_support": True,
            "transfer_cohort_ids": [cohort.cohort_id],
        },
        artifact_refs=list(opencode["manifest"].artifact_refs),
        metadata={
            "phase": "v5",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
            "model_policy": "nano_only",
            "evaluation_suite_id": evaluation_suite.suite_id,
            "objective_suite_id": objective_suite.suite_id,
            "composition_id": opencode_composition.composition_id,
        },
    )

    codex_atomic_vs_local = build_paired_candidate_comparison(
        codex_manifest,
        comparison_id="comparison.transfer_cohort.codex.atomic_vs_local.001",
        parent_candidate_id=codex["baseline_candidate"].candidate_id,
        child_candidate_id=codex_package_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=codex_manifest.sample_ids(),
        held_out_sample_ids=codex_manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The V4 local Codex package candidate improves the shared guidance and bounded-edit subset over the atomic baseline.",
        metric_deltas={
            "guidance_clarity_delta": 0.11,
            "bounded_edit_honesty_delta": 0.09,
            "package_scope_integrity_delta": 0.04,
        },
        better_candidate_id=codex_package_candidate.candidate_id,
        metadata={"phase": "v5", "package": "codex_dossier.current", "model_policy": "nano_only"},
    )
    codex_local_vs_cohort = build_paired_candidate_comparison(
        codex_manifest,
        comparison_id="comparison.transfer_cohort.codex.local_vs_cohort.001",
        parent_candidate_id=codex_package_candidate.candidate_id,
        child_candidate_id=codex_cohort_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=codex_manifest.sample_ids(),
        held_out_sample_ids=codex_manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The cohort-aware Codex candidate improves the shared transfer subset while preserving package-local boundedness.",
        metric_deltas={
            "guidance_clarity_delta": 0.03,
            "bounded_edit_honesty_delta": 0.04,
            "package_scope_integrity_delta": 0.02,
            "transfer_claim_support_delta": 0.07,
        },
        better_candidate_id=codex_cohort_candidate.candidate_id,
        metadata={"phase": "v5", "package": "codex_dossier.current", "model_policy": "nano_only"},
    )

    opencode_atomic_vs_local = build_paired_candidate_comparison(
        opencode_manifest,
        comparison_id="comparison.transfer_cohort.opencode.atomic_vs_local.001",
        parent_candidate_id=opencode["baseline_candidate"].candidate_id,
        child_candidate_id=opencode_package_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=opencode_manifest.sample_ids(),
        held_out_sample_ids=opencode_manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The V4 local OpenCode package candidate improves the shared guidance and bounded-edit subset over the atomic baseline under Nano-only reevaluation.",
        metric_deltas={
            "guidance_clarity_delta": 0.09,
            "bounded_edit_honesty_delta": 0.07,
            "package_scope_integrity_delta": 0.05,
        },
        better_candidate_id=opencode_package_candidate.candidate_id,
        metadata={"phase": "v5", "package": "opencode_1_2_17.current", "model_policy": "nano_only"},
    )
    opencode_local_vs_cohort = build_paired_candidate_comparison(
        opencode_manifest,
        comparison_id="comparison.transfer_cohort.opencode.local_vs_cohort.001",
        parent_candidate_id=opencode_package_candidate.candidate_id,
        child_candidate_id=opencode_cohort_candidate.candidate_id,
        outcome="non_inferior",
        compared_sample_ids=opencode_manifest.sample_ids(),
        held_out_sample_ids=opencode_manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The cohort-aware OpenCode candidate stays non-inferior locally while strengthening the shared transfer claim under the Nano-only cohort policy.",
        metric_deltas={
            "guidance_clarity_delta": 0.02,
            "bounded_edit_honesty_delta": 0.03,
            "package_scope_integrity_delta": 0.01,
            "transfer_claim_support_delta": 0.08,
        },
        better_candidate_id=opencode_cohort_candidate.candidate_id,
        metadata={"phase": "v5", "package": "opencode_1_2_17.current", "model_policy": "nano_only"},
    )

    codex_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.transfer_cohort.codex.shared.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=codex_manifest.manifest_id,
        candidate_id=codex_cohort_candidate.candidate_id,
        per_sample_components={
            "sample.support_execution_tool_guidance_coding_overlay.train.001": {
                "guidance_clarity": 0.87,
                "bounded_edit_honesty": 0.96,
                "package_scope_integrity": 0.99,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.validation.001": {
                "guidance_clarity": 0.84,
                "bounded_edit_honesty": 0.95,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.hold.001": {
                "guidance_clarity": 0.82,
                "bounded_edit_honesty": 0.97,
                "package_scope_integrity": 1.0,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.regression.001": {
                "guidance_clarity": 0.8,
                "bounded_edit_honesty": 0.96,
                "package_scope_integrity": 1.0,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "tool-guidance": {"guidance_clarity": 0.87},
            "bounded-edit": {"bounded_edit_honesty": 0.96, "package_scope_integrity": 0.99},
        },
        aggregate_objectives={
            "guidance_clarity": 0.8325,
            "bounded_edit_honesty": 0.96,
            "package_scope_integrity": 0.9925,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={"stochasticity_class": "deterministic", "trial_count": 1, "blocked_for_uncertainty": False},
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "transfer_claim_review": {"status": "required", "authority": "hard_gate"},
        },
        slice_status={
            codex_package_slice: {"status": "pass", "promotion_role": "required"},
            shared_model_tier_slice: {"status": "pass", "promotion_role": "required"},
        },
        member_family_breakdowns={
            "family.support_execution.v2": {"bounded_edit_honesty": 0.96},
            "family.tool_guidance.v2": {"guidance_clarity": 0.8325},
            "family.coding_overlay.v2": {"package_scope_integrity": 0.9925},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/codex_objective_breakdown.json",
                media_type="application/json",
            )
        ],
        metadata={
            "phase": "v5",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.support_execution.v2": {"status": "present", "drivers": ["bounded_edit_honesty"]},
                "family.tool_guidance.v2": {"status": "present", "drivers": ["guidance_clarity"]},
                "family.coding_overlay.v2": {"status": "present", "drivers": ["package_scope_integrity"]},
            },
        },
    )

    opencode_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.transfer_cohort.opencode.shared.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=opencode_manifest.manifest_id,
        candidate_id=opencode_cohort_candidate.candidate_id,
        per_sample_components={
            "sample.opencode_prompt_config_tool_guidance.train.001": {
                "guidance_clarity": 0.85,
                "bounded_edit_honesty": 0.95,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.validation.001": {
                "guidance_clarity": 0.83,
                "bounded_edit_honesty": 0.94,
                "package_scope_integrity": 0.97,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.hold.001": {
                "guidance_clarity": 0.8,
                "bounded_edit_honesty": 0.96,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.regression.001": {
                "guidance_clarity": 0.79,
                "bounded_edit_honesty": 0.95,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "tool-guidance": {"guidance_clarity": 0.85},
            "bounded-edit": {"bounded_edit_honesty": 0.95, "package_scope_integrity": 0.98},
        },
        aggregate_objectives={
            "guidance_clarity": 0.8175,
            "bounded_edit_honesty": 0.95,
            "package_scope_integrity": 0.9775,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={"stochasticity_class": "deterministic", "trial_count": 1, "blocked_for_uncertainty": False},
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "transfer_claim_review": {"status": "required", "authority": "hard_gate"},
        },
        slice_status={
            opencode_package_slice: {"status": "pass", "promotion_role": "required"},
            shared_model_tier_slice: {"status": "pass", "promotion_role": "required"},
        },
        member_family_breakdowns={
            "family.opencode_prompt_pack.v4": {"guidance_clarity": 0.8175},
            "family.opencode_bounded_config.v4": {"bounded_edit_honesty": 0.95},
            "family.opencode_tool_guidance_pack.v4": {"package_scope_integrity": 0.9775},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_objective_breakdown.json",
                media_type="application/json",
            )
        ],
        metadata={
            "phase": "v5",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.opencode_prompt_pack.v4": {"status": "present", "drivers": ["guidance_clarity"]},
                "family.opencode_bounded_config.v4": {"status": "present", "drivers": ["bounded_edit_honesty"]},
                "family.opencode_tool_guidance_pack.v4": {"status": "present", "drivers": ["package_scope_integrity"]},
            },
        },
    )

    cohort_status = {
        cohort.cohort_id: {
            "status": "transfer_supported",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
            "model_policy": "nano_only",
            "shared_family_emphasis": list(cohort.claim_scope["shared_family_emphasis"]),
            "hidden_hold_per_package": True,
        }
    }

    codex_summary = build_promotion_evidence_summary(
        summary_id="summary.transfer_cohort.codex.shared.001",
        candidate_id=codex_cohort_candidate.candidate_id,
        benchmark_manifest=codex_manifest,
        comparison_results=[codex_atomic_vs_local, codex_local_vs_cohort],
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        family_composition=codex_composition,
        search_space=codex_search_space,
        transfer_cohorts=[cohort],
        objective_breakdown_results=[codex_breakdown],
        claim_tier="transfer_supported",
        transfer_cohort_status=cohort_status,
        review_required=True,
        metadata={"phase": "v5", "package": "codex_dossier.current"},
    )
    opencode_summary = build_promotion_evidence_summary(
        summary_id="summary.transfer_cohort.opencode.shared.001",
        candidate_id=opencode_cohort_candidate.candidate_id,
        benchmark_manifest=opencode_manifest,
        comparison_results=[opencode_atomic_vs_local, opencode_local_vs_cohort],
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        family_composition=opencode_composition,
        search_space=opencode_search_space,
        transfer_cohorts=[cohort],
        objective_breakdown_results=[opencode_breakdown],
        claim_tier="transfer_supported",
        transfer_cohort_status=cohort_status,
        review_required=True,
        metadata={"phase": "v5", "package": "opencode_1_2_17.current"},
    )

    codex_staged_request_payload = codex["staged_request"].to_dict()
    codex_staged_request_payload["metadata"] = {
        **dict(codex_staged_request_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "transfer_cohort_status": cohort_status,
        "claim_tier": "transfer_supported",
        "model_policy": "nano_only",
        "cohort_rollup": {
            "status": "supported",
            "model_policy": "nano_only",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
        },
    }
    codex_staged_request = StagedOptimizerRequest.from_dict(codex_staged_request_payload)
    codex_staged_result_payload = codex["staged_result"].to_dict()
    codex_staged_result_payload["metadata"] = {
        **dict(codex_staged_result_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "claim_tier": "transfer_supported",
        "model_policy": "nano_only",
    }
    codex_staged_result = ReflectiveParetoBackendResult.from_dict(codex_staged_result_payload)

    opencode_staged_request_payload = opencode["staged_request"].to_dict()
    opencode_staged_request_payload["metadata"] = {
        **dict(opencode_staged_request_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "transfer_cohort_status": cohort_status,
        "claim_tier": "transfer_supported",
        "model_policy": "nano_only",
        "cohort_rollup": {
            "status": "supported",
            "model_policy": "nano_only",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
        },
    }
    opencode_staged_request = StagedOptimizerRequest.from_dict(opencode_staged_request_payload)
    opencode_staged_result_payload = opencode["staged_result"].to_dict()
    opencode_staged_result_payload["metadata"] = {
        **dict(opencode_staged_result_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "claim_tier": "transfer_supported",
        "model_policy": "nano_only",
    }
    opencode_staged_result = ReflectiveParetoBackendResult.from_dict(opencode_staged_result_payload)

    codex_result = BenchmarkRunResult(
        run_id="benchmark_run.transfer_cohort.codex.v5",
        manifest_id=codex_manifest.manifest_id,
        candidate_ids=[
            codex["baseline_candidate"].candidate_id,
            codex_package_candidate.candidate_id,
            codex_cohort_candidate.candidate_id,
        ],
        comparison_results=[codex_atomic_vs_local, codex_local_vs_cohort],
        aggregate_metrics={
            "atomic_score": 0.71,
            "local_package_score": 0.83,
            "cohort_candidate_score": 0.87,
        },
        bucket_outcomes={
            "tool-guidance": {"outcome": "cohort_candidate_win"},
            "bounded-edit": {"outcome": "cohort_candidate_win"},
        },
        variance_summary={"trial_count": 1, "stochasticity_class": "deterministic", "model_policy": "nano_only"},
        cost_support_evidence_slices={"nano_only": True, "shared_transfer_subset": True},
        transfer_cohort_status=cohort_status,
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/codex_benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "claim_tier": "transfer_supported",
            "local_claim_tier": "package_local",
            "transfer_cohort_ids": [cohort.cohort_id],
            "transfer_cohort_status": cohort_status,
            "promotion_summary_id": codex_summary.summary_id,
        },
        metadata={
            "phase": "v5",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
            "model_policy": "nano_only",
        },
    )

    opencode_result = BenchmarkRunResult(
        run_id="benchmark_run.transfer_cohort.opencode.v5",
        manifest_id=opencode_manifest.manifest_id,
        candidate_ids=[
            opencode["baseline_candidate"].candidate_id,
            opencode_package_candidate.candidate_id,
            opencode_cohort_candidate.candidate_id,
        ],
        comparison_results=[opencode_atomic_vs_local, opencode_local_vs_cohort],
        aggregate_metrics={
            "atomic_score": 0.74,
            "local_package_score": 0.82,
            "cohort_candidate_score": 0.84,
        },
        bucket_outcomes={
            "tool-guidance": {"outcome": "cohort_candidate_non_inferior"},
            "bounded-edit": {"outcome": "cohort_candidate_win"},
        },
        variance_summary={"trial_count": 1, "stochasticity_class": "deterministic", "model_policy": "nano_only"},
        cost_support_evidence_slices={"nano_only": True, "shared_transfer_subset": True},
        transfer_cohort_status=cohort_status,
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_benchmark_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "claim_tier": "transfer_supported",
            "local_claim_tier": "package_local",
            "transfer_cohort_ids": [cohort.cohort_id],
            "transfer_cohort_status": cohort_status,
            "promotion_summary_id": opencode_summary.summary_id,
        },
        metadata={
            "phase": "v5",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
            "model_policy": "nano_only",
        },
    )

    return {
        "transfer_cohort": cohort,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "codex_cell": {
            "package_example": codex,
            "manifest": codex_manifest,
            "cohort_candidate": codex_cohort_candidate,
            "objective_breakdown_result": codex_breakdown,
            "promotion_summary": codex_summary,
            "benchmark_result": codex_result,
            "staged_request": codex_staged_request,
            "staged_result": codex_staged_result,
        },
        "opencode_cell": {
            "package_example": opencode,
            "manifest": opencode_manifest,
            "cohort_candidate": opencode_cohort_candidate,
            "objective_breakdown_result": opencode_breakdown,
            "promotion_summary": opencode_summary,
            "benchmark_result": opencode_result,
            "staged_request": opencode_staged_request,
            "staged_result": opencode_staged_result,
        },
        "cohort_rollup": {
            "cohort_id": cohort.cohort_id,
            "claim_tier": "transfer_supported",
            "status": "supported",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
            "model_policy": "nano_only",
            "shared_family_emphasis": list(cohort.claim_scope["shared_family_emphasis"]),
        },
    }


def build_codex_opencode_transfer_cohort_example_payload() -> Dict[str, object]:
    example = build_codex_opencode_transfer_cohort_example()
    return {
        "transfer_cohort": example["transfer_cohort"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "codex_cell": {
            "manifest": example["codex_cell"]["manifest"].to_dict(),
            "cohort_candidate": example["codex_cell"]["cohort_candidate"].to_dict(),
            "objective_breakdown_result": example["codex_cell"]["objective_breakdown_result"].to_dict(),
            "promotion_summary": example["codex_cell"]["promotion_summary"].to_dict(),
            "benchmark_result": example["codex_cell"]["benchmark_result"].to_dict(),
            "staged_request": example["codex_cell"]["staged_request"].to_dict(),
            "staged_result": example["codex_cell"]["staged_result"].to_dict(),
        },
        "opencode_cell": {
            "manifest": example["opencode_cell"]["manifest"].to_dict(),
            "cohort_candidate": example["opencode_cell"]["cohort_candidate"].to_dict(),
            "objective_breakdown_result": example["opencode_cell"]["objective_breakdown_result"].to_dict(),
            "promotion_summary": example["opencode_cell"]["promotion_summary"].to_dict(),
            "benchmark_result": example["opencode_cell"]["benchmark_result"].to_dict(),
            "staged_request": example["opencode_cell"]["staged_request"].to_dict(),
            "staged_result": example["opencode_cell"]["staged_result"].to_dict(),
        },
        "cohort_rollup": dict(example["cohort_rollup"]),
    }


def build_codex_opencode_replay_config_transfer_cohort_follow_on_example() -> Dict[str, object]:
    """Build a second bounded V5 cohort on the same package pair with a different shared emphasis."""

    codex = build_support_execution_tool_guidance_coding_overlay_package_example()
    opencode = build_opencode_prompt_config_tool_guidance_package_example()
    codex_package_candidate = codex["package_candidate"]
    opencode_package_candidate = opencode["package_candidate"]
    assert isinstance(codex_package_candidate, CandidateBundle)
    assert isinstance(opencode_package_candidate, CandidateBundle)

    codex_package_slice = next(
        item.slice_id for item in codex["transfer_slices"] if item.slice_kind == "package"
    )
    opencode_package_slice = next(
        item.slice_id for item in opencode["transfer_slices"] if item.slice_kind == "package"
    )
    shared_model_tier_slice = next(
        item.slice_id for item in codex["transfer_slices"] if item.slice_kind == "model_tier"
    )

    cohort = TransferCohortManifest(
        cohort_id="cohort.codex_dossier_current.opencode_1_2_17.replay_config.v5",
        cohort_kind="bounded_two_package_follow_on",
        member_slice_ids=[
            codex_package_slice,
            opencode_package_slice,
            shared_model_tier_slice,
        ],
        claim_scope={
            "package_ids": ["codex_dossier.current", "opencode_1_2_17.current"],
            "shared_family_emphasis": [
                "replay_safe_prompt_config_coherence",
                "package_scope_integrity",
            ],
            "max_package_count": 2,
            "model_policy": "nano_first",
        },
        coverage_policy={
            "requires_hidden_hold_per_package": True,
            "requires_regression_per_package": True,
            "claim_tiers": ["package_local", "transfer_supported"],
            "audited_mini_allowed_on_ambiguous_hold": True,
        },
        metadata={
            "phase": "v5",
            "non_kernel": True,
            "darwin_boundary": "not_reopened",
            "follow_on": True,
        },
    )

    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.transfer_cohort.codex_opencode.replay_config.v5",
        suite_kind="bounded_transfer_follow_on_package_pair",
        evaluator_stack=[
            "replay_safe_policy_checker.v1",
            "prompt_config_coherence_checker.v1",
            "package_scope_checker.v1",
            "model_tier_audit_checker.v1",
        ],
        split_visibility={
            "train": "mutation_visible",
            "validation": "comparison_visible",
            "hold": "hidden_hold",
            "regression": "comparison_visible",
        },
        stochasticity_class="deterministic",
        rerun_policy={
            "max_trials": 1,
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "escalation_policy": "audited_semantic_tie_break_only",
            "budget_policy": "nano_first_follow_on",
        },
        capture_requirements=[
            "workflow_coherence",
            "replay_safe_integrity",
            "package_scope_integrity",
            "mini_audit_clean",
        ],
        signal_channels={
            "executable_checks": {"source_kind": "deterministic_checker"},
            "semantic_judge": {"source_kind": "model_judge"},
            "model_tier_audit": {"source_kind": "model_policy_audit"},
        },
        adjudication_requirements={
            "requires_hidden_hold_review": True,
            "requires_regression_coverage": True,
            "mini_escalation_requires_justification": True,
            "mini_escalation_triggers": ["ambiguous_hidden_hold_semantics"],
        },
        comparison_protocol_defaults={
            "protocol_id": "paired_local_vs_transfer_follow_on.v1",
            "minimum_trial_count": 1,
            "requires_hidden_hold_bucket": True,
        },
        artifact_requirements=["paired_eval_json", "cohort_follow_on_summary_json"],
        metadata={
            "phase": "v5",
            "evaluation_truth": "primary",
            "reward_like_ranking": "private_only",
            "model_policy": "nano_first",
            "stopping_policy": "stop_if_mini_audit_does_not_change_transfer_status",
        },
    )

    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.transfer_cohort.codex_opencode.replay_config.v5",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "workflow_coherence": {
                "direction": "maximize",
                "source_metric": "workflow_coherence",
                "promotion_sensitive": True,
            },
            "replay_safe_integrity": {
                "direction": "maximize",
                "source_metric": "replay_safe_integrity",
                "promotion_sensitive": True,
            },
            "package_scope_integrity": {
                "direction": "maximize",
                "source_metric": "package_scope_integrity",
                "promotion_sensitive": True,
            },
            "mutation_cost": {
                "direction": "minimize",
                "source_metric": "cost_delta_usd",
                "promotion_sensitive": False,
            },
        },
        penalties={
            "scope_expansion": {
                "kind": "hard_block",
                "reason": "follow-on cohort may not widen package applicability",
            },
            "replay_safe_regression": {
                "kind": "hard_block",
                "reason": "replay-safe integrity must remain explicit across both packages",
            },
        },
        aggregation_rules={"per_sample": "weighted_sum", "global": "replay_config_follow_on_first"},
        uncertainty_policy={
            "stochasticity_class": "deterministic",
            "blocked_when_missing_hidden_hold": True,
            "mini_escalation_on_ambiguity_only": True,
        },
        channel_dependencies={
            "package_scope_integrity": ["workflow_coherence", "replay_safe_integrity"],
        },
        frontier_dimensions=[
            "workflow_coherence",
            "replay_safe_integrity",
            "package_scope_integrity",
            "mutation_cost",
        ],
        promotion_annotations={
            "requires_transfer_cohort_support": True,
            "review_class": "bounded_transfer_follow_on",
        },
        visibility_annotations={
            "hidden_hold_channels": [
                "workflow_coherence",
                "replay_safe_integrity",
                "package_scope_integrity",
            ],
        },
        metadata={"phase": "v5", "reward_like_ranking": "private_only"},
    )

    codex_change_map = {change.locus_id: change for change in codex_package_candidate.changes}
    opencode_change_map = {change.locus_id: change for change in opencode_package_candidate.changes}

    codex_execution_value = dict(codex_change_map["policy.execution_profile.selection"].value)
    codex_execution_value["preferred_profile"] = "workspace-write"
    codex_execution_value["fallback_profile"] = "replay-safe"
    codex_execution_value["require_replay_safe_lane"] = True
    codex_planning_value = dict(codex_change_map["prompt.section.planning_policy"].value)
    codex_planning_value["text"] = (
        "Start from the replay-safe path, keep the package scope bounded, and only widen from workspace-write "
        "when the declared support envelope explicitly allows it."
    )
    codex_follow_on_candidate = CandidateBundle(
        candidate_id="cand.transfer_cohort.codex.replay_config.001",
        source_target_id=codex["target"].target_id,
        applied_loci=[
            "policy.execution_profile.selection",
            "prompt.section.planning_policy",
        ],
        changes=[
            CandidateChange(
                locus_id="policy.execution_profile.selection",
                value=codex_execution_value,
                rationale="Tighten the replay-safe config member on the Codex cell without widening package scope.",
            ),
            CandidateChange(
                locus_id="prompt.section.planning_policy",
                value=codex_planning_value,
                rationale="Make replay-safe workflow coherence explicit in the Codex package prompt surface.",
            ),
        ],
        provenance={
            "kind": "transfer_cohort_follow_on_candidate",
            "baseline_candidate_id": codex_package_candidate.candidate_id,
            "transfer_cohort_id": cohort.cohort_id,
        },
        metadata={
            "lane": "codex_opencode_replay_config_transfer_cohort",
            "role": "cohort_follow_on",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
        },
    )

    opencode_system_value = dict(opencode_change_map["prompt.pack.base.system"].value)
    opencode_system_value["text"] = (
        "Preserve the single durable OpenCode system envelope and make replay-safe package boundaries explicit "
        "before any wider tool or config interpretation."
    )
    opencode_patch_value = dict(opencode_change_map["guardrails.diff_policy.patch_splitting"].value)
    opencode_patch_value["on_violation"] = "error"
    opencode_follow_on_candidate = CandidateBundle(
        candidate_id="cand.transfer_cohort.opencode.replay_config.001",
        source_target_id=opencode["target"].target_id,
        applied_loci=[
            "prompt.pack.base.system",
            "guardrails.diff_policy.patch_splitting",
        ],
        changes=[
            CandidateChange(
                locus_id="prompt.pack.base.system",
                value=opencode_system_value,
                rationale="Tighten replay-safe prompt-pack coherence on the OpenCode package under the shared cohort emphasis.",
            ),
            CandidateChange(
                locus_id="guardrails.diff_policy.patch_splitting",
                value=opencode_patch_value,
                rationale="Strengthen the bounded patch policy on the ambiguous OpenCode semantic hold slice.",
            ),
        ],
        provenance={
            "kind": "transfer_cohort_follow_on_candidate",
            "baseline_candidate_id": opencode_package_candidate.candidate_id,
            "transfer_cohort_id": cohort.cohort_id,
        },
        metadata={
            "lane": "codex_opencode_replay_config_transfer_cohort",
            "role": "cohort_follow_on",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
        },
    )

    codex_manifest_payload = codex["manifest"].to_dict()
    codex_manifest_payload.update(
        {
            "manifest_id": "manifest.transfer_cohort.codex_dossier.replay_config.v5",
            "benchmark_kind": "transfer_cohort_follow_on_package_cell",
            "baseline_candidate_id": codex_package_candidate.candidate_id,
            "evaluator_stack": list(evaluation_suite.evaluator_stack),
            "comparison_protocol": "paired_local_vs_transfer_follow_on.v1",
            "rerun_policy": dict(evaluation_suite.rerun_policy),
            "contamination_notes": [
                "No hidden cohort identity may be used during mutation.",
                "Codex follow-on remains Nano-only in this cohort tranche.",
            ],
            "transfer_cohort_ids": [cohort.cohort_id],
            "promotion_relevance": {
                "claim_scope": "bounded_two_package_transfer_follow_on",
                "requires_transfer_cohort_support": True,
                "transfer_cohort_ids": [cohort.cohort_id],
            },
            "metadata": {
                "phase": "v5",
                "package": "codex_dossier.current",
                "transfer_cohort_id": cohort.cohort_id,
                "model_policy": "nano_only",
                "evaluation_suite_id": evaluation_suite.suite_id,
                "objective_suite_id": objective_suite.suite_id,
                "composition_id": codex["family_composition"].composition_id,
            },
        }
    )
    codex_manifest = BenchmarkRunManifest.from_dict(codex_manifest_payload)

    opencode_manifest_payload = opencode["manifest"].to_dict()
    opencode_manifest_payload.update(
        {
            "manifest_id": "manifest.transfer_cohort.opencode_1_2_17.replay_config.v5",
            "benchmark_kind": "transfer_cohort_follow_on_package_cell",
            "baseline_candidate_id": opencode_package_candidate.candidate_id,
            "evaluator_stack": list(evaluation_suite.evaluator_stack),
            "comparison_protocol": "paired_local_vs_transfer_follow_on.v1",
            "rerun_policy": dict(evaluation_suite.rerun_policy),
            "contamination_notes": [
                "No hidden cohort identity may be used during mutation.",
                "OpenCode follow-on may escalate to Mini only on ambiguous hidden-hold semantics.",
            ],
            "transfer_cohort_ids": [cohort.cohort_id],
            "promotion_relevance": {
                "claim_scope": "bounded_two_package_transfer_follow_on",
                "requires_transfer_cohort_support": True,
                "transfer_cohort_ids": [cohort.cohort_id],
            },
            "metadata": {
                "phase": "v5",
                "package": "opencode_1_2_17.current",
                "transfer_cohort_id": cohort.cohort_id,
                "model_policy": "nano_first",
                "evaluation_suite_id": evaluation_suite.suite_id,
                "objective_suite_id": objective_suite.suite_id,
                "composition_id": opencode["family_composition"].composition_id,
            },
        }
    )
    opencode_manifest = BenchmarkRunManifest.from_dict(opencode_manifest_payload)

    codex_comparison = build_paired_candidate_comparison(
        codex_manifest,
        comparison_id="comparison.transfer_cohort.codex.replay_config.001",
        parent_candidate_id=codex_package_candidate.candidate_id,
        child_candidate_id=codex_follow_on_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=codex_manifest.sample_ids(),
        held_out_sample_ids=codex_manifest.hidden_hold_sample_ids(),
        trial_count=1,
        rationale="The Codex follow-on improves replay-safe prompt/config coherence while preserving the bounded package scope under Nano-only evaluation.",
        metric_deltas={
            "workflow_coherence_delta": 0.04,
            "replay_safe_integrity_delta": 0.05,
            "package_scope_integrity_delta": 0.01,
        },
        better_candidate_id=codex_follow_on_candidate.candidate_id,
        metadata={
            "phase": "v5",
            "package": "codex_dossier.current",
            "model_policy": "nano_only",
            "cohort_role": "follow_on",
        },
    )

    opencode_comparison = build_paired_candidate_comparison(
        opencode_manifest,
        comparison_id="comparison.transfer_cohort.opencode.replay_config.001",
        parent_candidate_id=opencode_package_candidate.candidate_id,
        child_candidate_id=opencode_follow_on_candidate.candidate_id,
        outcome="non_inferior",
        compared_sample_ids=opencode_manifest.sample_ids(),
        held_out_sample_ids=opencode_manifest.hidden_hold_sample_ids(),
        trial_count=2,
        rationale="The OpenCode follow-on stays non-inferior locally and improves replay-safe prompt/config coherence after an audited Mini tie-break on the hidden-hold semantic slice.",
        metric_deltas={
            "workflow_coherence_delta": 0.03,
            "replay_safe_integrity_delta": 0.05,
            "package_scope_integrity_delta": 0.02,
            "mini_audit_confirmed": True,
        },
        better_candidate_id=opencode_follow_on_candidate.candidate_id,
        metadata={
            "phase": "v5",
            "package": "opencode_1_2_17.current",
            "model_policy": "nano_first",
            "mini_escalation_triggered": True,
            "mini_escalation_reason": "ambiguous_hidden_hold_semantics",
            "cohort_role": "follow_on",
        },
    )

    codex_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.transfer_cohort.codex.replay_config.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=codex_manifest.manifest_id,
        candidate_id=codex_follow_on_candidate.candidate_id,
        per_sample_components={
            "sample.support_execution_tool_guidance_coding_overlay.train.001": {
                "workflow_coherence": 0.86,
                "replay_safe_integrity": 0.97,
                "package_scope_integrity": 0.99,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.validation.001": {
                "workflow_coherence": 0.84,
                "replay_safe_integrity": 0.96,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.hold.001": {
                "workflow_coherence": 0.82,
                "replay_safe_integrity": 0.98,
                "package_scope_integrity": 0.99,
                "mutation_cost": 0.0,
            },
            "sample.support_execution_tool_guidance_coding_overlay.regression.001": {
                "workflow_coherence": 0.8,
                "replay_safe_integrity": 0.97,
                "package_scope_integrity": 0.99,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "replay-safe": {"workflow_coherence": 0.86, "replay_safe_integrity": 0.97},
            "package-scope": {"package_scope_integrity": 0.99},
        },
        aggregate_objectives={
            "workflow_coherence": 0.83,
            "replay_safe_integrity": 0.97,
            "package_scope_integrity": 0.9875,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 1,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": False,
            "mini_escalation_triggered": False,
        },
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "model_tier_audit": {"status": "pass", "authority": "hard_gate"},
        },
        slice_status={
            codex_package_slice: {"status": "pass", "promotion_role": "required"},
            shared_model_tier_slice: {"status": "pass", "promotion_role": "required"},
        },
        member_family_breakdowns={
            "family.support_execution.v2": {"replay_safe_integrity": 0.97},
            "family.tool_guidance.v2": {"workflow_coherence": 0.83},
            "family.coding_overlay.v2": {"package_scope_integrity": 0.9875},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/codex_replay_config_breakdown.json",
                media_type="application/json",
            )
        ],
        metadata={
            "phase": "v5",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.support_execution.v2": {"status": "present", "drivers": ["replay_safe_integrity"]},
                "family.tool_guidance.v2": {"status": "present", "drivers": ["workflow_coherence"]},
                "family.coding_overlay.v2": {"status": "present", "drivers": ["package_scope_integrity"]},
            },
        },
    )

    opencode_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.transfer_cohort.opencode.replay_config.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=opencode_manifest.manifest_id,
        candidate_id=opencode_follow_on_candidate.candidate_id,
        per_sample_components={
            "sample.opencode_prompt_config_tool_guidance.train.001": {
                "workflow_coherence": 0.84,
                "replay_safe_integrity": 0.96,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.validation.001": {
                "workflow_coherence": 0.82,
                "replay_safe_integrity": 0.95,
                "package_scope_integrity": 0.97,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.hold.001": {
                "workflow_coherence": 0.8,
                "replay_safe_integrity": 0.96,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.regression.001": {
                "workflow_coherence": 0.79,
                "replay_safe_integrity": 0.95,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "replay-safe": {"workflow_coherence": 0.84, "replay_safe_integrity": 0.96},
            "package-scope": {"package_scope_integrity": 0.98},
        },
        aggregate_objectives={
            "workflow_coherence": 0.8125,
            "replay_safe_integrity": 0.955,
            "package_scope_integrity": 0.9775,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 2,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": True,
            "mini_escalation_triggered": True,
        },
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "model_tier_audit": {"status": "audited_pass", "authority": "hard_gate"},
        },
        slice_status={
            opencode_package_slice: {"status": "pass", "promotion_role": "required"},
            shared_model_tier_slice: {"status": "audited_pass", "promotion_role": "required"},
        },
        member_family_breakdowns={
            "family.opencode_prompt_pack.v4": {"workflow_coherence": 0.8125},
            "family.opencode_bounded_config.v4": {"replay_safe_integrity": 0.955},
            "family.opencode_tool_guidance_pack.v4": {"package_scope_integrity": 0.9775},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_replay_config_breakdown.json",
                media_type="application/json",
            )
        ],
        metadata={
            "phase": "v5",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.opencode_prompt_pack.v4": {"status": "present", "drivers": ["workflow_coherence"]},
                "family.opencode_bounded_config.v4": {"status": "present", "drivers": ["replay_safe_integrity"]},
                "family.opencode_tool_guidance_pack.v4": {"status": "present", "drivers": ["package_scope_integrity"]},
            },
        },
    )

    cohort_status = {
        cohort.cohort_id: {
            "status": "transfer_supported",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
            "model_policy": "nano_first",
            "shared_family_emphasis": list(cohort.claim_scope["shared_family_emphasis"]),
            "mini_audit_triggered": True,
        }
    }

    codex_summary = build_promotion_evidence_summary(
        summary_id="summary.transfer_cohort.codex.replay_config.001",
        candidate_id=codex_follow_on_candidate.candidate_id,
        benchmark_manifest=codex_manifest,
        comparison_results=[codex_comparison],
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        family_composition=codex["family_composition"],
        search_space=codex["search_space"],
        transfer_cohorts=[cohort],
        objective_breakdown_results=[codex_breakdown],
        claim_tier="transfer_supported",
        transfer_cohort_status=cohort_status,
        review_required=True,
        metadata={"phase": "v5", "package": "codex_dossier.current", "follow_on": True},
    )
    opencode_summary = build_promotion_evidence_summary(
        summary_id="summary.transfer_cohort.opencode.replay_config.001",
        candidate_id=opencode_follow_on_candidate.candidate_id,
        benchmark_manifest=opencode_manifest,
        comparison_results=[opencode_comparison],
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        family_composition=opencode["family_composition"],
        search_space=opencode["search_space"],
        transfer_cohorts=[cohort],
        objective_breakdown_results=[opencode_breakdown],
        claim_tier="transfer_supported",
        transfer_cohort_status=cohort_status,
        review_required=True,
        metadata={"phase": "v5", "package": "opencode_1_2_17.current", "follow_on": True},
    )

    codex_staged_request_payload = codex["staged_request"].to_dict()
    codex_staged_request_payload["metadata"] = {
        **dict(codex_staged_request_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "transfer_cohort_status": cohort_status,
        "claim_tier": "transfer_supported",
        "model_policy": "nano_only",
        "follow_on": True,
        "search_policy_signal": "",
        "cohort_rollup": {
            "status": "supported",
            "model_policy": "nano_only",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
            "mini_audit_triggered": False,
        },
    }
    codex_staged_request = StagedOptimizerRequest.from_dict(codex_staged_request_payload)
    codex_staged_result_payload = codex["staged_result"].to_dict()
    codex_staged_result_payload["metadata"] = {
        **dict(codex_staged_result_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "claim_tier": "transfer_supported",
        "model_policy": "nano_only",
        "follow_on": True,
    }
    codex_staged_result = ReflectiveParetoBackendResult.from_dict(codex_staged_result_payload)

    opencode_staged_request_payload = opencode["staged_request"].to_dict()
    opencode_staged_request_payload["metadata"] = {
        **dict(opencode_staged_request_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "transfer_cohort_status": cohort_status,
        "claim_tier": "transfer_supported",
        "model_policy": "nano_first",
        "follow_on": True,
        "search_policy_signal": "ambiguous_hidden_hold",
        "model_tier_audit": {
            "triggered": True,
            "reason": "ambiguous_hidden_hold_semantics",
            "audit_model": "gpt-5.4-mini",
        },
        "cohort_rollup": {
            "status": "supported",
            "model_policy": "nano_first",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
            "mini_audit_triggered": True,
        },
    }
    opencode_staged_request = StagedOptimizerRequest.from_dict(opencode_staged_request_payload)
    opencode_staged_result_payload = opencode["staged_result"].to_dict()
    opencode_staged_result_payload["metadata"] = {
        **dict(opencode_staged_result_payload.get("metadata") or {}),
        "phase": "v5",
        "transfer_cohort_ids": [cohort.cohort_id],
        "claim_tier": "transfer_supported",
        "model_policy": "nano_first",
        "follow_on": True,
    }
    opencode_staged_result = ReflectiveParetoBackendResult.from_dict(opencode_staged_result_payload)

    codex_result = BenchmarkRunResult(
        run_id="benchmark_run.transfer_cohort.codex.replay_config.v5",
        manifest_id=codex_manifest.manifest_id,
        candidate_ids=[codex_package_candidate.candidate_id, codex_follow_on_candidate.candidate_id],
        comparison_results=[codex_comparison],
        aggregate_metrics={
            "local_package_score": 0.83,
            "cohort_follow_on_score": 0.86,
        },
        bucket_outcomes={
            "replay-safe": {"outcome": "child_win"},
            "package-scope": {"outcome": "child_win"},
        },
        variance_summary={"trial_count": 1, "stochasticity_class": "deterministic", "model_policy": "nano_only"},
        cost_support_evidence_slices={"nano_only": True, "replay_follow_on": True},
        transfer_cohort_status=cohort_status,
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/codex_replay_config_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "claim_tier": "transfer_supported",
            "transfer_cohort_ids": [cohort.cohort_id],
            "transfer_cohort_status": cohort_status,
            "promotion_summary_id": codex_summary.summary_id,
            "follow_on": True,
        },
        metadata={
            "phase": "v5",
            "package": "codex_dossier.current",
            "transfer_cohort_id": cohort.cohort_id,
            "model_policy": "nano_only",
        },
    )

    opencode_result = BenchmarkRunResult(
        run_id="benchmark_run.transfer_cohort.opencode.replay_config.v5",
        manifest_id=opencode_manifest.manifest_id,
        candidate_ids=[opencode_package_candidate.candidate_id, opencode_follow_on_candidate.candidate_id],
        comparison_results=[opencode_comparison],
        aggregate_metrics={
            "local_package_score": 0.82,
            "cohort_follow_on_score": 0.84,
        },
        bucket_outcomes={
            "replay-safe": {"outcome": "child_non_inferior"},
            "package-scope": {"outcome": "child_win"},
        },
        variance_summary={
            "trial_count": 2,
            "stochasticity_class": "deterministic",
            "default_model": "gpt-5.4-nano",
            "escalation_model": "gpt-5.4-mini",
            "mini_escalation_triggered": True,
        },
        cost_support_evidence_slices={"nano_first": True, "mini_audited_follow_on": True},
        transfer_cohort_status=cohort_status,
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_replay_config_summary.json",
                media_type="application/json",
            )
        ],
        promotion_readiness_summary={
            "claim_tier": "transfer_supported",
            "transfer_cohort_ids": [cohort.cohort_id],
            "transfer_cohort_status": cohort_status,
            "promotion_summary_id": opencode_summary.summary_id,
            "follow_on": True,
        },
        metadata={
            "phase": "v5",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
            "model_policy": "nano_first",
        },
    )

    return {
        "transfer_cohort": cohort,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "codex_cell": {
            "package_example": codex,
            "manifest": codex_manifest,
            "cohort_candidate": codex_follow_on_candidate,
            "objective_breakdown_result": codex_breakdown,
            "promotion_summary": codex_summary,
            "benchmark_result": codex_result,
            "staged_request": codex_staged_request,
            "staged_result": codex_staged_result,
        },
        "opencode_cell": {
            "package_example": opencode,
            "manifest": opencode_manifest,
            "cohort_candidate": opencode_follow_on_candidate,
            "objective_breakdown_result": opencode_breakdown,
            "promotion_summary": opencode_summary,
            "benchmark_result": opencode_result,
            "staged_request": opencode_staged_request,
            "staged_result": opencode_staged_result,
        },
        "cohort_rollup": {
            "cohort_id": cohort.cohort_id,
            "claim_tier": "transfer_supported",
            "status": "supported",
            "packages_covered": ["codex_dossier.current", "opencode_1_2_17.current"],
            "model_policy": "nano_first",
            "shared_family_emphasis": list(cohort.claim_scope["shared_family_emphasis"]),
            "mini_audit_triggered": True,
        },
    }


def build_codex_opencode_replay_config_transfer_cohort_follow_on_example_payload() -> Dict[str, object]:
    example = build_codex_opencode_replay_config_transfer_cohort_follow_on_example()
    return {
        "transfer_cohort": example["transfer_cohort"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "codex_cell": {
            "manifest": example["codex_cell"]["manifest"].to_dict(),
            "cohort_candidate": example["codex_cell"]["cohort_candidate"].to_dict(),
            "objective_breakdown_result": example["codex_cell"]["objective_breakdown_result"].to_dict(),
            "promotion_summary": example["codex_cell"]["promotion_summary"].to_dict(),
            "benchmark_result": example["codex_cell"]["benchmark_result"].to_dict(),
            "staged_request": example["codex_cell"]["staged_request"].to_dict(),
            "staged_result": example["codex_cell"]["staged_result"].to_dict(),
        },
        "opencode_cell": {
            "manifest": example["opencode_cell"]["manifest"].to_dict(),
            "cohort_candidate": example["opencode_cell"]["cohort_candidate"].to_dict(),
            "objective_breakdown_result": example["opencode_cell"]["objective_breakdown_result"].to_dict(),
            "promotion_summary": example["opencode_cell"]["promotion_summary"].to_dict(),
            "benchmark_result": example["opencode_cell"]["benchmark_result"].to_dict(),
            "staged_request": example["opencode_cell"]["staged_request"].to_dict(),
            "staged_result": example["opencode_cell"]["staged_result"].to_dict(),
        },
        "cohort_rollup": dict(example["cohort_rollup"]),
    }


def build_codex_opencode_transfer_cohort_verifier_follow_on_example() -> Dict[str, object]:
    """Build one narrow verifier-assisted follow-on on the bounded V5 cohort follow-on lane."""

    example = build_codex_opencode_replay_config_transfer_cohort_follow_on_example()
    opencode_cell = example["opencode_cell"]
    opencode_package = opencode_cell["package_example"]
    cohort = example["transfer_cohort"]
    evaluation_suite = example["evaluation_suite"]
    objective_suite = example["objective_suite"]
    manifest = opencode_cell["manifest"]
    base_candidate = opencode_cell["cohort_candidate"]
    search_space = opencode_package["search_space"]
    assert isinstance(base_candidate, CandidateBundle)
    assert isinstance(evaluation_suite, EvaluationSuiteManifest)
    assert isinstance(objective_suite, ObjectiveSuiteManifest)
    assert isinstance(manifest, BenchmarkRunManifest)
    assert isinstance(search_space, SearchSpaceManifest)

    refined_candidate = CandidateBundle(
        candidate_id="cand.transfer_cohort.opencode.replay_config.verifier_refined.001",
        source_target_id=opencode_package["target"].target_id,
        applied_loci=[
            "prompt.pack.base.system",
            "guardrails.diff_policy.patch_splitting",
        ],
        changes=[
            CandidateChange(
                locus_id="prompt.pack.base.system",
                value={
                    "text": (
                        "Preserve the durable OpenCode system envelope, keep replay-safe package boundaries explicit, "
                        "and require any model-tier tie-break to remain auditable and package-scoped."
                    )
                },
                rationale=(
                    "Verifier guidance tightens replay-safe prompt coherence on the bounded cohort follow-on lane "
                    "without widening package or cohort scope."
                ),
            ),
            CandidateChange(
                locus_id="guardrails.diff_policy.patch_splitting",
                value={
                    "enabled": True,
                    "max_hunks": 4,
                    "on_violation": "error",
                },
                rationale=(
                    "Specialize the bounded patch policy on the audited OpenCode follow-on cell while preserving "
                    "the existing declared config locus and cohort boundary."
                ),
            ),
        ],
        provenance={
            "kind": "transfer_cohort_verifier_follow_on",
            "baseline_candidate_id": base_candidate.candidate_id,
            "transfer_cohort_id": cohort.cohort_id,
        },
        metadata={
            "lane": "codex_opencode_replay_config_transfer_cohort",
            "role": "verifier_follow_on",
            "package": "opencode_1_2_17.current",
            "transfer_cohort_id": cohort.cohort_id,
            "non_kernel": True,
        },
    )

    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.transfer_cohort.opencode.replay_config.verifier_refined.001",
        parent_candidate_id=base_candidate.candidate_id,
        child_candidate_id=refined_candidate.candidate_id,
        outcome="win",
        compared_sample_ids=manifest.sample_ids(),
        held_out_sample_ids=manifest.hidden_hold_sample_ids(),
        trial_count=2,
        rationale=(
            "The verifier-assisted refinement improves replay-safe prompt/config coherence on the bounded OpenCode "
            "cohort cell while preserving package scope, audited Mini discipline, and the existing transfer claim."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_replay_config_verifier_eval.json",
                media_type="application/json",
            )
        ],
        metric_deltas={
            "workflow_coherence_delta": 0.02,
            "replay_safe_integrity_delta": 0.03,
            "package_scope_integrity_delta": 0.01,
            "mini_audit_confirmed": True,
            "verifier_confirmed": True,
        },
        better_candidate_id=refined_candidate.candidate_id,
        metadata={
            "lane": "codex_opencode_replay_config_transfer_cohort",
            "transfer_cohort_id": cohort.cohort_id,
            "experiment_kind": "verifier_augmented_transfer_cohort_refinement",
            "model_policy": "nano_first",
        },
    )

    refined_objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreakdown.transfer_cohort.opencode.replay_config.verifier_refined.001",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=refined_candidate.candidate_id,
        per_sample_components={
            "sample.opencode_prompt_config_tool_guidance.train.001": {
                "workflow_coherence": 0.86,
                "replay_safe_integrity": 0.97,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.validation.001": {
                "workflow_coherence": 0.84,
                "replay_safe_integrity": 0.96,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.hold.001": {
                "workflow_coherence": 0.82,
                "replay_safe_integrity": 0.97,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
            "sample.opencode_prompt_config_tool_guidance.regression.001": {
                "workflow_coherence": 0.8,
                "replay_safe_integrity": 0.96,
                "package_scope_integrity": 0.98,
                "mutation_cost": 0.0,
            },
        },
        per_bucket_components={
            "replay-safe": {"workflow_coherence": 0.86, "replay_safe_integrity": 0.97},
            "package-scope": {"package_scope_integrity": 0.98},
        },
        aggregate_objectives={
            "workflow_coherence": 0.83,
            "replay_safe_integrity": 0.965,
            "package_scope_integrity": 0.98,
            "mutation_cost": 0.0,
            "eligible_for_promotion": False,
        },
        uncertainty_summary={
            "stochasticity_class": "deterministic",
            "trial_count": 2,
            "blocked_for_uncertainty": False,
            "mini_escalation_considered": True,
            "mini_escalation_triggered": True,
        },
        blocked_components={},
        signal_status={
            "executable_checks": {"status": "pass", "authority": "primary"},
            "semantic_judge": {"status": "pass", "authority": "advisory"},
            "model_tier_audit": {"status": "audited_pass", "authority": "hard_gate"},
            "verifier_outputs": {"status": "pass", "authority": "supporting"},
        },
        slice_status={
            "package.opencode_1_2_17.current": {"status": "pass", "promotion_role": "required"},
            "model_tier.nano_first_openai": {"status": "audited_pass", "promotion_role": "required"},
        },
        member_family_breakdowns={
            "family.opencode_prompt_pack.v4": {"workflow_coherence": 0.83},
            "family.opencode_bounded_config.v4": {"replay_safe_integrity": 0.965},
            "family.opencode_tool_guidance_pack.v4": {"package_scope_integrity": 0.98},
        },
        cross_family_blocked_components={},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_replay_config_verifier_breakdown.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "codex_opencode_replay_config_transfer_cohort",
            "transfer_cohort_id": cohort.cohort_id,
            "search_space_id": search_space.search_space_id,
            "experiment_kind": "verifier_augmented_transfer_cohort_refinement",
            "cohort_follow_on": True,
            "applicability_scope_status": "bounded",
            "member_family_attribution": {
                "family.opencode_prompt_pack.v4": {"status": "present", "drivers": ["workflow_coherence"]},
                "family.opencode_bounded_config.v4": {"status": "present", "drivers": ["replay_safe_integrity"]},
                "family.opencode_tool_guidance_pack.v4": {"status": "present", "drivers": ["package_scope_integrity"]},
            },
        },
    )

    verifier_experiment = VerifierAugmentedExperimentResult(
        experiment_id="verifier_experiment.transfer_cohort.opencode.replay_config.v5",
        experiment_kind="verifier_augmented_transfer_cohort_refinement",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_suite_id=objective_suite.suite_id,
        target_family_id="family.opencode_bounded_config.v4",
        search_space_id=search_space.search_space_id,
        baseline_candidate_id=base_candidate.candidate_id,
        refined_candidate_id=refined_candidate.candidate_id,
        verifier_stack=[
            *evaluation_suite.evaluator_stack,
            "transfer_cohort_scope_verifier.v1",
        ],
        focus_sample_ids=manifest.hidden_hold_sample_ids() or manifest.sample_ids(),
        comparison_result_id=comparison.comparison_id,
        objective_breakdown_result_id=refined_objective_breakdown.result_id,
        outcome="accepted",
        rationale=(
            "This narrow follow-on stays inside the bounded V5 transfer cohort lane, specializing the OpenCode "
            "replay-safe prompt/config members while preserving the declared cohort scope, audited Mini rules, "
            "and the existing transfer-supported claim."
        ),
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/transfer_cohort/opencode_replay_config_verifier_summary.json",
                media_type="application/json",
            )
        ],
        metadata={
            "lane": "codex_opencode_replay_config_transfer_cohort",
            "phase": "v5",
            "backend_only": True,
            "non_kernel": True,
            "darwin_boundary": "not_reopened",
            "family_bound": True,
            "transfer_cohort_id": cohort.cohort_id,
            "specialization_scope": "opencode_replay_safe_members_inside_bounded_transfer_cohort",
            "model_policy": "nano_first",
            "package": "opencode_1_2_17.current",
        },
    )

    return {
        "cohort_example": example,
        "refined_candidate": refined_candidate,
        "comparison_result": comparison,
        "objective_breakdown_result": refined_objective_breakdown,
        "verifier_experiment": verifier_experiment,
    }


def build_codex_opencode_transfer_cohort_verifier_follow_on_example_payload() -> Dict[str, object]:
    example = build_codex_opencode_transfer_cohort_verifier_follow_on_example()
    return {
        "cohort_example": {
            "transfer_cohort": example["cohort_example"]["transfer_cohort"].to_dict(),
            "evaluation_suite": example["cohort_example"]["evaluation_suite"].to_dict(),
            "objective_suite": example["cohort_example"]["objective_suite"].to_dict(),
            "opencode_cell": {
                "manifest": example["cohort_example"]["opencode_cell"]["manifest"].to_dict(),
                "cohort_candidate": example["cohort_example"]["opencode_cell"]["cohort_candidate"].to_dict(),
                "objective_breakdown_result": example["cohort_example"]["opencode_cell"]["objective_breakdown_result"].to_dict(),
                "promotion_summary": example["cohort_example"]["opencode_cell"]["promotion_summary"].to_dict(),
                "benchmark_result": example["cohort_example"]["opencode_cell"]["benchmark_result"].to_dict(),
            },
            "cohort_rollup": dict(example["cohort_example"]["cohort_rollup"]),
        },
        "refined_candidate": example["refined_candidate"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "verifier_experiment": example["verifier_experiment"].to_dict(),
    }


def build_codex_opencode_live_transfer_cohort_cell_example() -> Dict[str, object]:
    """Build the first V6 live cohort-cell study on the existing Codex/OpenCode pair."""

    example = build_codex_opencode_transfer_cohort_example()
    cohort = example["transfer_cohort"]
    codex_cell = example["codex_cell"]
    opencode_cell = example["opencode_cell"]

    live_cell = {
        "cell_id": "live_cell.codex_opencode.shared_transfer.v6",
        "cell_kind": "bounded_live_transfer_cohort_cell",
        "status": "complete",
        "phase": "v6",
        "stability": "experimental_helper",
        "public_artifact_additions_required": False,
        "package_pair": ["codex_dossier.current", "opencode_1_2_17.current"],
        "proposal_model_policy": "nano_only",
        "shared_emphasis": [
            "tool_guidance_clarity",
            "bounded_edit_support_honesty",
        ],
        "baselines": [
            {
                "baseline_id": "atomic_sequential",
                "source": "atomic_baseline",
                "per_package_outcome": {
                    "codex_dossier.current": "below_transfer_candidate",
                    "opencode_1_2_17.current": "below_transfer_candidate",
                },
            },
            {
                "baseline_id": "v4_local_package",
                "source": "package_candidate",
                "per_package_outcome": {
                    "codex_dossier.current": "win_but_package_local_only",
                    "opencode_1_2_17.current": "win_but_package_local_only",
                },
            },
            {
                "baseline_id": "v5_cohort_aware_staged",
                "source": "transfer_cohort_candidate",
                "per_package_outcome": {
                    "codex_dossier.current": "transfer_supported",
                    "opencode_1_2_17.current": "transfer_supported_non_inferior",
                },
            },
        ],
        "trial_plan": {
            "planned_nano_pairs": 8,
            "max_nano_pairs": 10,
            "mini_budget_fraction": 0.0,
            "top_slice_follow_on_fraction": 0.15,
            "repro_reruns_for_promotion_relevant_candidates": 1,
        },
        "budget_guardrails": {
            "max_live_cohort_studies": 2,
            "nano_token_share_target": "0.80-0.90",
            "mini_token_share_cap": 0.10,
        },
        "claim_reporting": {
            "codex_dossier.current": {
                "local_claim_tier": "package_local",
                "cell_claim_tier": codex_cell["promotion_summary"].claim_tier,
            },
            "opencode_1_2_17.current": {
                "local_claim_tier": "package_local",
                "cell_claim_tier": opencode_cell["promotion_summary"].claim_tier,
            },
        },
        "stopping_rules": [
            "stop_after_8_to_10_nano_pairs_without_credible_pattern",
            "stop_if_required_transfer_slices_fail_on_both_packages",
            "stop_if_package_local_success_remains_stronger_than_transfer_evidence",
        ],
        "contamination_rules": [
            "no_hidden_cohort_identity_during_mutation",
            "no_cross_package_gold_rationale_reuse_outside_allowed_splits",
            "no_mini_only_evaluation_path",
        ],
        "metadata": {
            "evaluation_truth": "primary",
            "reward_like_ranking": "private_only",
            "darwin_boundary": "not_reopened",
            "transfer_cohort_id": cohort.cohort_id,
        },
    }

    return {
        "transfer_cohort_example": example,
        "live_cell": live_cell,
    }


def build_codex_opencode_live_transfer_cohort_cell_example_payload() -> Dict[str, object]:
    example = build_codex_opencode_live_transfer_cohort_cell_example()
    base = example["transfer_cohort_example"]
    return {
        "transfer_cohort_example": {
            "transfer_cohort": base["transfer_cohort"].to_dict(),
            "evaluation_suite": base["evaluation_suite"].to_dict(),
            "objective_suite": base["objective_suite"].to_dict(),
            "codex_cell": {
                "manifest": base["codex_cell"]["manifest"].to_dict(),
                "cohort_candidate": base["codex_cell"]["cohort_candidate"].to_dict(),
                "objective_breakdown_result": base["codex_cell"]["objective_breakdown_result"].to_dict(),
                "promotion_summary": base["codex_cell"]["promotion_summary"].to_dict(),
                "benchmark_result": base["codex_cell"]["benchmark_result"].to_dict(),
                "staged_request": base["codex_cell"]["staged_request"].to_dict(),
                "staged_result": base["codex_cell"]["staged_result"].to_dict(),
            },
            "opencode_cell": {
                "manifest": base["opencode_cell"]["manifest"].to_dict(),
                "cohort_candidate": base["opencode_cell"]["cohort_candidate"].to_dict(),
                "objective_breakdown_result": base["opencode_cell"]["objective_breakdown_result"].to_dict(),
                "promotion_summary": base["opencode_cell"]["promotion_summary"].to_dict(),
                "benchmark_result": base["opencode_cell"]["benchmark_result"].to_dict(),
                "staged_request": base["opencode_cell"]["staged_request"].to_dict(),
                "staged_result": base["opencode_cell"]["staged_result"].to_dict(),
            },
            "cohort_rollup": dict(base["cohort_rollup"]),
        },
        "live_cell": dict(example["live_cell"]),
    }


def build_codex_opencode_live_replay_config_cell_example() -> Dict[str, object]:
    """Build the second V6 live cohort cell on the same pair with audited Mini escalation."""

    cohort_example = build_codex_opencode_replay_config_transfer_cohort_follow_on_example()
    verifier_follow_on = build_codex_opencode_transfer_cohort_verifier_follow_on_example()
    cohort = cohort_example["transfer_cohort"]
    codex_cell = cohort_example["codex_cell"]
    opencode_cell = cohort_example["opencode_cell"]

    live_cell = {
        "cell_id": "live_cell.codex_opencode.replay_config.v6",
        "cell_kind": "bounded_live_transfer_follow_on_cell",
        "status": "complete",
        "phase": "v6",
        "stability": "experimental_helper",
        "public_artifact_additions_required": False,
        "package_pair": ["codex_dossier.current", "opencode_1_2_17.current"],
        "proposal_model_policy": "nano_first",
        "shared_emphasis": [
            "replay_safe_prompt_config_coherence",
            "package_scope_integrity",
        ],
        "baselines": [
            {
                "baseline_id": "atomic_sequential",
                "source": "atomic_baseline",
                "per_package_outcome": {
                    "codex_dossier.current": "below_follow_on_candidate",
                    "opencode_1_2_17.current": "below_follow_on_candidate",
                },
            },
            {
                "baseline_id": "v4_local_package",
                "source": "package_candidate",
                "per_package_outcome": {
                    "codex_dossier.current": "package_local_only",
                    "opencode_1_2_17.current": "package_local_only",
                },
            },
            {
                "baseline_id": "v5_cohort_aware_staged",
                "source": "transfer_follow_on_candidate",
                "per_package_outcome": {
                    "codex_dossier.current": "transfer_supported",
                    "opencode_1_2_17.current": "transfer_supported_with_mini_audit",
                },
            },
            {
                "baseline_id": "v5_cohort_aware_staged_plus_verifier",
                "source": "bounded_verifier_follow_on",
                "per_package_outcome": {
                    "codex_dossier.current": "not_applicable",
                    "opencode_1_2_17.current": "top_slice_verifier_refinement",
                },
            },
        ],
        "trial_plan": {
            "planned_nano_pairs": 8,
            "max_nano_pairs": 10,
            "mini_budget_fraction_cap": 0.10,
            "top_slice_follow_on_fraction": 0.15,
            "paired_reevaluation_required_on_escalation": True,
        },
        "budget_guardrails": {
            "max_live_cohort_studies": 2,
            "nano_token_share_target": "0.80-0.90",
            "mini_token_share_cap": 0.10,
            "verifier_follow_on_cap": "top_10_15_percent_only",
        },
        "claim_reporting": {
            "codex_dossier.current": {
                "local_claim_tier": "package_local",
                "cell_claim_tier": codex_cell["promotion_summary"].claim_tier,
                "model_policy": "nano_only",
            },
            "opencode_1_2_17.current": {
                "local_claim_tier": "package_local",
                "cell_claim_tier": opencode_cell["promotion_summary"].claim_tier,
                "model_policy": "nano_first_with_audited_mini",
            },
        },
        "stopping_rules": [
            "stop_after_8_to_10_nano_pairs_without_credible_pattern",
            "freeze_cell_if_mini_cap_exceeded_without_promotion_relevant_change",
            "stop_if_ranking_depends_mainly_on_blocked_semantic_channels",
        ],
        "contamination_rules": [
            "no_hidden_cohort_identity_during_mutation",
            "no_promotion_only_scalarization_calibration",
            "report_nano_only_and_escalated_results_separately",
        ],
        "metadata": {
            "evaluation_truth": "primary",
            "reward_like_ranking": "private_only",
            "darwin_boundary": "not_reopened",
            "transfer_cohort_id": cohort.cohort_id,
            "mini_audit_triggered": True,
            "verifier_follow_on_id": verifier_follow_on["verifier_experiment"].experiment_id,
        },
    }

    return {
        "cohort_example": cohort_example,
        "verifier_follow_on": verifier_follow_on,
        "live_cell": live_cell,
    }


def build_codex_opencode_live_replay_config_cell_example_payload() -> Dict[str, object]:
    example = build_codex_opencode_live_replay_config_cell_example()
    base = example["cohort_example"]
    verifier = example["verifier_follow_on"]
    return {
        "cohort_example": {
            "transfer_cohort": base["transfer_cohort"].to_dict(),
            "evaluation_suite": base["evaluation_suite"].to_dict(),
            "objective_suite": base["objective_suite"].to_dict(),
            "codex_cell": {
                "manifest": base["codex_cell"]["manifest"].to_dict(),
                "cohort_candidate": base["codex_cell"]["cohort_candidate"].to_dict(),
                "objective_breakdown_result": base["codex_cell"]["objective_breakdown_result"].to_dict(),
                "promotion_summary": base["codex_cell"]["promotion_summary"].to_dict(),
                "benchmark_result": base["codex_cell"]["benchmark_result"].to_dict(),
                "staged_request": base["codex_cell"]["staged_request"].to_dict(),
                "staged_result": base["codex_cell"]["staged_result"].to_dict(),
            },
            "opencode_cell": {
                "manifest": base["opencode_cell"]["manifest"].to_dict(),
                "cohort_candidate": base["opencode_cell"]["cohort_candidate"].to_dict(),
                "objective_breakdown_result": base["opencode_cell"]["objective_breakdown_result"].to_dict(),
                "promotion_summary": base["opencode_cell"]["promotion_summary"].to_dict(),
                "benchmark_result": base["opencode_cell"]["benchmark_result"].to_dict(),
                "staged_request": base["opencode_cell"]["staged_request"].to_dict(),
                "staged_result": base["opencode_cell"]["staged_result"].to_dict(),
            },
            "cohort_rollup": dict(base["cohort_rollup"]),
        },
        "verifier_follow_on": {
            "refined_candidate": verifier["refined_candidate"].to_dict(),
            "comparison_result": verifier["comparison_result"].to_dict(),
            "objective_breakdown_result": verifier["objective_breakdown_result"].to_dict(),
            "verifier_experiment": verifier["verifier_experiment"].to_dict(),
        },
        "live_cell": dict(example["live_cell"]),
    }


def build_v6_live_result_boundary_example() -> Dict[str, object]:
    """Build the explicit V6 boundary between live results and durable optimizer behavior."""

    first_cell = build_codex_opencode_live_transfer_cohort_cell_example()
    second_cell = build_codex_opencode_live_replay_config_cell_example()
    first_base = first_cell["transfer_cohort_example"]
    second_base = second_cell["cohort_example"]
    verifier_follow_on = second_cell["verifier_follow_on"]

    boundary = {
        "boundary_id": "live_result_boundary.v6",
        "phase": "v6",
        "stability": "experimental_helper",
        "classification_order": [
            "experiment_only",
            "private_heuristic_candidate",
            "durable_backend_private_heuristic",
            "durable_doctrine_default_change",
        ],
        "classification_rules": {
            "experiment_only": {
                "requires_replication": False,
                "promotion_relevant_change_required": False,
                "private_trace_only_is_insufficient": True,
                "allowed_effect": "document_only",
            },
            "private_heuristic_candidate": {
                "requires_replication": False,
                "human_readable_rationale_required": True,
                "hidden_hold_clean_required": True,
                "allowed_effect": "private_candidate_only",
            },
            "durable_backend_private_heuristic": {
                "requires_replication": True,
                "minimum_confirming_cells": 2,
                "cost_stability_required": True,
                "allowed_effect": "backend_private_default",
            },
            "durable_doctrine_default_change": {
                "requires_replication": True,
                "human_review_required": True,
                "claim_discipline_impact": "kernel_doctrine",
                "allowed_effect": "documented_default_change",
            },
        },
        "attribution_policy": {
            "attribution_sufficient_when": [
                "claim_width_is_package_local_or_transfer_supported",
                "member_families_are_explicit",
                "mini_escalation_did_not_create_the_win",
            ],
            "ablation_required_when": [
                "claim_width_exceeds_package_local_or_transfer_supported",
                "multiple_member_families_materially_changed",
                "mini_escalation_is_decisive",
                "doctrine_or_default_change_is_requested",
            ],
        },
        "live_examples": [
            {
                "example_id": "result.codex_opencode.shared_transfer.v6",
                "source_cell_id": first_cell["live_cell"]["cell_id"],
                "candidate_id": first_base["codex_cell"]["cohort_candidate"].candidate_id,
                "classification": "experiment_only",
                "claim_tier": first_base["codex_cell"]["promotion_summary"].claim_tier,
                "reason": "one live cell is not enough to change durable behavior",
            },
            {
                "example_id": "result.codex_opencode.replay_config.opencode.v6",
                "source_cell_id": second_cell["live_cell"]["cell_id"],
                "candidate_id": second_base["opencode_cell"]["cohort_candidate"].candidate_id,
                "classification": "private_heuristic_candidate",
                "claim_tier": second_base["opencode_cell"]["promotion_summary"].claim_tier,
                "reason": "audited Mini tie-break can inform a private heuristic candidate but not a public default change",
            },
            {
                "example_id": "result.codex_opencode.repeated_cohort_stopping.v6",
                "source_cell_id": second_cell["live_cell"]["cell_id"],
                "candidate_id": second_base["opencode_cell"]["cohort_candidate"].candidate_id,
                "classification": "durable_backend_private_heuristic",
                "claim_tier": second_base["opencode_cell"]["promotion_summary"].claim_tier,
                "reason": "repeated bounded cells justify private stopping or ranking behavior if cost and hidden-hold remain stable",
            },
            {
                "example_id": "result.codex_opencode.live_doctrine.freeze.v6",
                "source_cell_id": second_cell["live_cell"]["cell_id"],
                "candidate_id": verifier_follow_on["refined_candidate"].candidate_id,
                "classification": "durable_doctrine_default_change",
                "claim_tier": second_base["opencode_cell"]["promotion_summary"].claim_tier,
                "reason": "only fairness or claim-discipline doctrine should change stable defaults from live-study pressure",
            },
        ],
        "metadata": {
            "evaluation_truth": "primary",
            "reward_like_ranking": "private_only",
            "darwin_boundary": "not_reopened",
            "public_artifact_additions_required": False,
        },
    }

    return {
        "first_live_cell": first_cell,
        "second_live_cell": second_cell,
        "boundary": boundary,
    }


def build_v6_live_result_boundary_example_payload() -> Dict[str, object]:
    example = build_v6_live_result_boundary_example()
    return {
        "first_live_cell": build_codex_opencode_live_transfer_cohort_cell_example_payload(),
        "second_live_cell": build_codex_opencode_live_replay_config_cell_example_payload(),
        "boundary": dict(example["boundary"]),
    }


def build_v6_live_cell_private_search_policy_examples() -> Dict[str, object]:
    """Build V6 live-cell staged requests that exercise bounded private stop/rank behavior."""

    first_cell = build_codex_opencode_live_transfer_cohort_cell_example()
    second_cell = build_codex_opencode_live_replay_config_cell_example()

    first_request_payload = first_cell["transfer_cohort_example"]["codex_cell"]["staged_request"].to_dict()
    first_request_payload["metadata"] = {
        **dict(first_request_payload.get("metadata") or {}),
        "phase": "v6",
        "live_cell_context": True,
        "live_cell_id": first_cell["live_cell"]["cell_id"],
        "nano_pairs_observed": 10,
        "max_nano_pairs": 10,
        "credible_pattern_absent": True,
        "stopping_policy": "stop_after_8_to_10_nano_pairs_without_credible_pattern",
    }
    first_request = StagedOptimizerRequest.from_dict(first_request_payload)
    first_result = run_staged_optimizer(first_request)

    second_request_payload = second_cell["cohort_example"]["opencode_cell"]["staged_request"].to_dict()
    second_request_payload["metadata"] = {
        **dict(second_request_payload.get("metadata") or {}),
        "phase": "v6",
        "live_cell_context": True,
        "live_cell_id": second_cell["live_cell"]["cell_id"],
        "blocked_semantic_channels_dominant": True,
        "mini_audit_no_status_change": True,
        "max_nano_pairs": 10,
        "nano_pairs_observed": 9,
    }
    second_request = StagedOptimizerRequest.from_dict(second_request_payload)
    second_result = run_staged_optimizer(second_request)

    return {
        "no_credible_pattern_case": {
            "live_cell": first_cell["live_cell"],
            "staged_request": first_request,
            "staged_result": first_result,
        },
        "blocked_semantic_case": {
            "live_cell": second_cell["live_cell"],
            "staged_request": second_request,
            "staged_result": second_result,
        },
    }


def build_v6_live_cell_private_search_policy_examples_payload() -> Dict[str, object]:
    example = build_v6_live_cell_private_search_policy_examples()
    return {
        "no_credible_pattern_case": {
            "live_cell": dict(example["no_credible_pattern_case"]["live_cell"]),
            "staged_request": example["no_credible_pattern_case"]["staged_request"].to_dict(),
            "staged_result": example["no_credible_pattern_case"]["staged_result"].to_dict(),
        },
        "blocked_semantic_case": {
            "live_cell": dict(example["blocked_semantic_case"]["live_cell"]),
            "staged_request": example["blocked_semantic_case"]["staged_request"].to_dict(),
            "staged_result": example["blocked_semantic_case"]["staged_result"].to_dict(),
        },
    }


def build_v6_stop_go_synthesis_example() -> Dict[str, object]:
    """Build the explicit V6 stop/go decision note from the current live-study evidence."""

    first_cell = build_codex_opencode_live_transfer_cohort_cell_example()
    second_cell = build_codex_opencode_live_replay_config_cell_example()
    boundary = build_v6_live_result_boundary_example()
    private_policy = build_v6_live_cell_private_search_policy_examples()

    synthesis = {
        "synthesis_id": "optimization_v6.stop_go.v1",
        "phase": "v6",
        "evidence_sources": [
            first_cell["live_cell"]["cell_id"],
            second_cell["live_cell"]["cell_id"],
            boundary["boundary"]["boundary_id"],
            "private_search_policy.v6",
        ],
        "repeated_shape_gap_detected": False,
        "recommended_outcome": "use_platform",
        "v7_required": False,
        "no_v7_criteria": [
            "live_cells_run_without_schema_pain",
            "no_repeated_public_artifact_gap",
            "claim_tiers_remain_honest",
            "mini_remains_rare_and_auditable",
            "more_value_from_running_cells_than_extending_kernel",
        ],
        "v7_triggers": [
            "two_or_more_live_studies_expose_same_missing_public_shape",
            "private_search_duplication_becomes_maintenance_burden",
            "claim_tier_or_generalization_reporting_breaks_repeatedly",
        ],
        "darwin_handoff_signals": [
            "many_cohort_orchestration",
            "adaptive_study_scheduling",
            "persistent_cross_run_archive_state",
            "diversity_or_novelty_search",
            "async_candidate_fleets",
        ],
        "metadata": {
            "evaluation_truth": "primary",
            "reward_like_ranking": "private_only",
            "darwin_boundary": "not_reopened",
            "first_live_cell_status": first_cell["live_cell"]["status"],
            "second_live_cell_status": second_cell["live_cell"]["status"],
            "boundary_classification_count": len(boundary["boundary"]["classification_order"]),
            "private_stop_reasons": [
                private_policy["no_credible_pattern_case"]["staged_result"].metadata["early_stop_reason"],
                private_policy["blocked_semantic_case"]["staged_result"].metadata["early_stop_reason"],
            ],
        },
    }

    return {
        "first_live_cell": first_cell,
        "second_live_cell": second_cell,
        "boundary": boundary,
        "private_policy": private_policy,
        "synthesis": synthesis,
    }


def build_v6_stop_go_synthesis_example_payload() -> Dict[str, object]:
    example = build_v6_stop_go_synthesis_example()
    return {
        "first_live_cell": build_codex_opencode_live_transfer_cohort_cell_example_payload(),
        "second_live_cell": build_codex_opencode_live_replay_config_cell_example_payload(),
        "boundary": build_v6_live_result_boundary_example_payload(),
        "private_policy": build_v6_live_cell_private_search_policy_examples_payload(),
        "synthesis": dict(example["synthesis"]),
    }


def build_next_frontier_optimize_cohort_packet() -> Dict[str, object]:
    """Build the first bounded optimize cohort packet over the executed DAG tranche."""

    manifest = BenchmarkRunManifest(
        manifest_id="optimize.next_frontier.cohort.dag_packet_compare.v1",
        benchmark_kind="dag_packet_comparison",
        target_id="target.next_frontier.optimize.dag_packet_selection",
        dataset_id="dataset.next_frontier.dag_packet_compare.v1",
        dataset_version="2026-04-08.v1",
        baseline_candidate_id="packet.search.replication_v1.got_sorting",
        environment_domain="synthetic_replay_audit",
        evaluator_stack=["structural_fidelity_checker", "bounded_compute_checker", "packet_review_judge"],
        comparison_protocol="paired_packet_meta_compare.v1",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=["sample.packet_compare.train.got", "sample.packet_compare.train.tot"],
                visibility="mutation_visible",
            ),
            BenchmarkSplit(
                split_name="validation",
                sample_ids=["sample.packet_compare.validation.moa"],
                visibility="comparison_visible",
            ),
            BenchmarkSplit(
                split_name="hold",
                sample_ids=["sample.packet_compare.hold.codetree"],
                visibility="hidden_hold",
            ),
        ],
        bucket_tags={
            "sample.packet_compare.train.got": ["graph_topology", "multi_parent_lineage"],
            "sample.packet_compare.train.tot": ["frontier_policy", "evaluator_control"],
            "sample.packet_compare.validation.moa": ["layered_fan_in"],
            "sample.packet_compare.hold.codetree": ["stage_heterogeneous_tree", "critic_feedback"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "model_policy": "gpt-5.4-mini", "budget_mode": "bounded_packet_compare"},
        contamination_notes=["cross-paper packet ids remain explicit", "no hidden evaluator stack changes"],
        promotion_relevance={"kind": "study_selection_only", "public_artifact_additions_required": False},
        artifact_refs=[
            ArtifactRef(ref="docs_tmp/DAG/replication_v1/DAG_REPLICATION_TRANCHE_V1_LEDGER.md", media_type="text/markdown"),
        ],
        metadata={"phase": "next_frontier_b", "workload_family": "dag_packet_comparison"},
    )
    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.next_frontier.cohort.dag_packet_compare.v1",
        parent_candidate_id="packet.search.replication_v1.got_sorting",
        child_candidate_id="packet.search.replication_v1.tot_game24",
        outcome="non_inferior",
        compared_sample_ids=[
            "sample.packet_compare.train.got",
            "sample.packet_compare.train.tot",
            "sample.packet_compare.validation.moa",
            "sample.packet_compare.hold.codetree",
        ],
        held_out_sample_ids=["sample.packet_compare.hold.codetree"],
        trial_count=1,
        rationale="Tree-of-Thoughts remains non-inferior on bounded packet-comparison objectives once evaluator control is explicit.",
        metric_deltas={"structural_fidelity": 0.02, "compute_efficiency": -0.01, "auditability": 0.03},
        better_candidate_id="packet.search.replication_v1.tot_game24",
        metadata={"claim_width": "cohort_only", "bounded_budget": True},
    )
    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.next_frontier.optimize.cohort.v1",
        suite_kind="bounded_packet_meta_compare",
        evaluator_stack=["structural_fidelity_checker", "bounded_compute_checker", "packet_review_judge"],
        split_visibility={"train": "mutation_visible", "validation": "comparison_visible", "hold": "hidden_hold"},
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "escalation_policy": "disabled"},
        capture_requirements=["fidelity_scorecard_alignment", "budget_envelope_adherence", "claim_limit_honesty"],
        signal_channels={
            "fidelity": {"source_kind": "scorecard"},
            "compute": {"source_kind": "ledger"},
            "packet_review": {"source_kind": "judge"},
        },
        adjudication_requirements={"requires_hidden_hold_review": True, "budget_mode": "bounded_packet_compare"},
        comparison_protocol_defaults={"protocol_id": manifest.comparison_protocol, "minimum_trial_count": 1},
        artifact_requirements=["paired_eval_json", "packet_meta_summary_json"],
        metadata={"phase": "next_frontier_b"},
    )
    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.next_frontier.optimize.cohort.v1",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "fidelity": {"direction": "maximize"},
            "compute_efficiency": {"direction": "maximize"},
            "auditability": {"direction": "maximize"},
        },
        penalties={"confound_risk": {"direction": "minimize"}},
        aggregation_rules={"fidelity_weight": 0.5, "compute_weight": 0.2, "auditability_weight": 0.3},
        uncertainty_policy={"requires_explicit_hold_review": True},
        blocked_channel_annotations={},
        channel_dependencies={"auditability": ["fidelity"]},
        frontier_dimensions=["fidelity", "compute_efficiency", "auditability"],
        promotion_annotations={"relevance": "study_selection_only"},
        visibility_annotations={"hold": "judge_only"},
        metadata={"phase": "next_frontier_b"},
    )
    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreak.next_frontier.optimize.cohort.v1",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=comparison.child_candidate_id,
        per_sample_components={
            "sample.packet_compare.train.got": {"fidelity": 0.79, "compute_efficiency": 0.75, "auditability": 0.84},
            "sample.packet_compare.train.tot": {"fidelity": 0.81, "compute_efficiency": 0.72, "auditability": 0.87},
            "sample.packet_compare.validation.moa": {"fidelity": 0.7, "compute_efficiency": 0.69, "auditability": 0.78},
        },
        per_bucket_components={
            "structural": {"fidelity": 0.8, "auditability": 0.85},
            "cost": {"compute_efficiency": 0.72},
        },
        aggregate_objectives={"fidelity": 0.77, "compute_efficiency": 0.72, "auditability": 0.83},
        uncertainty_summary={"hidden_hold_pending": True},
        blocked_components={},
        signal_status={"packet_review": {"status": "present"}},
        slice_status={"cohort_compare": {"status": "complete"}},
        artifact_refs=[ArtifactRef(ref="artifacts/optimization/next_frontier/cohort_compare_summary.json", media_type="application/json")],
        metadata={"phase": "next_frontier_b"},
    )
    return {
        "manifest": manifest,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "study_note": {
            "workload": "dag_packet_comparison_workload",
            "decision_signal": "tot_non_inferior_under_bounded_meta_objective",
            "pain_classification": "reporting_gap_only",
        },
    }


def build_next_frontier_optimize_cohort_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_cohort_packet()
    return {
        "manifest": example["manifest"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_optimize_transfer_packet() -> Dict[str, object]:
    """Build the first bounded optimize transfer packet using DAG tranche lessons."""

    cohort = build_next_frontier_optimize_cohort_packet()
    manifest = BenchmarkRunManifest(
        manifest_id="optimize.next_frontier.transfer.dag_packet_follow_on.v1",
        benchmark_kind="dag_packet_transfer_follow_on",
        target_id="target.next_frontier.optimize.dag_packet_transfer",
        dataset_id="dataset.next_frontier.dag_packet_transfer.v1",
        dataset_version="2026-04-08.v1",
        baseline_candidate_id="packet.search.replication_v1.moa_layered",
        environment_domain="synthetic_replay_audit",
        evaluator_stack=["transfer_claim_checker", "bounded_compute_checker", "packet_review_judge"],
        comparison_protocol="paired_transfer_follow_on_compare.v1",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=["sample.packet_transfer.train.moa"],
                visibility="mutation_visible",
            ),
            BenchmarkSplit(
                split_name="validation",
                sample_ids=["sample.packet_transfer.validation.codetree"],
                visibility="comparison_visible",
            ),
            BenchmarkSplit(
                split_name="hold",
                sample_ids=["sample.packet_transfer.hold.cross_domain"],
                visibility="hidden_hold",
            ),
        ],
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "model_policy": "gpt-5.4-mini", "transfer_enabled": True},
        contamination_notes=["transfer hints stay bounded to packet-level study doctrine"],
        metadata={"phase": "next_frontier_b", "workload_family": "transfer_follow_on"},
    )
    transfer_slice = TransferSliceManifest(
        slice_id="slice.next_frontier.transfer.packet_family.v1",
        slice_kind="repo_family",
        selector={"family": "dag_replication_packet", "source_packets": ["got_sorting", "tot_game24"]},
        promotion_role="claim_supporting",
        visibility="comparison_visible",
        metadata={"phase": "next_frontier_b"},
    )
    cohort_manifest = TransferCohortManifest(
        cohort_id="cohort.next_frontier.optimize.dag_follow_on.v1",
        cohort_kind="bounded_packet_family_transfer",
        member_slice_ids=[transfer_slice.slice_id],
        claim_scope={"source": "got_tot_gate1", "target": "moa_codetree_follow_on", "max_family_count": 1},
        coverage_policy={"requires_hidden_hold": True, "claim_tiers": ["package_local", "transfer_supported"]},
        metadata={"phase": "next_frontier_b"},
    )
    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.next_frontier.transfer.follow_on.v1",
        parent_candidate_id="packet.search.replication_v1.moa_layered",
        child_candidate_id="packet.search.replication_v1.codetree_patch",
        outcome="win",
        compared_sample_ids=[
            "sample.packet_transfer.train.moa",
            "sample.packet_transfer.validation.codetree",
            "sample.packet_transfer.hold.cross_domain",
        ],
        held_out_sample_ids=["sample.packet_transfer.hold.cross_domain"],
        trial_count=1,
        rationale="Carrying explicit gate-1 evaluator and lineage lessons forward improves the CodeTree follow-on packet under the bounded transfer objective.",
        metric_deltas={"transfer_gain": 0.08, "compute_efficiency": -0.02, "claim_honesty": 0.04},
        better_candidate_id="packet.search.replication_v1.codetree_patch",
        metadata={"transfer_enabled": True},
    )
    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreak.next_frontier.optimize.transfer.v1",
        objective_suite_id="objsuite.next_frontier.optimize.transfer.v1",
        manifest_id=manifest.manifest_id,
        candidate_id=comparison.child_candidate_id,
        per_sample_components={
            "sample.packet_transfer.train.moa": {"transfer_gain": 0.06, "claim_honesty": 0.81},
            "sample.packet_transfer.validation.codetree": {"transfer_gain": 0.08, "claim_honesty": 0.85},
        },
        per_bucket_components={"follow_on": {"transfer_gain": 0.07, "claim_honesty": 0.83}},
        aggregate_objectives={"transfer_gain": 0.07, "claim_honesty": 0.83},
        uncertainty_summary={"hidden_hold_pending": True},
        blocked_components={},
        signal_status={"transfer_claim": {"status": "supported"}},
        slice_status={"follow_on": {"status": "complete"}},
        artifact_refs=[ArtifactRef(ref="artifacts/optimization/next_frontier/transfer_follow_on_summary.json", media_type="application/json")],
        metadata={"phase": "next_frontier_b"},
    )
    evidence_summary = PromotionEvidenceSummary(
        summary_id="evidence_summary.next_frontier.transfer.v1",
        candidate_id=comparison.child_candidate_id,
        comparison_ids=[comparison.comparison_id],
        manifest_ids=[manifest.manifest_id],
        held_out_sample_ids=["sample.packet_transfer.hold.cross_domain"],
        evaluation_suite_ids=[cohort["evaluation_suite"].suite_id],
        objective_suite_ids=[cohort["objective_suite"].suite_id],
        objective_breakdown_result_ids=[objective_breakdown.result_id],
        transfer_slice_ids=[transfer_slice.slice_id],
        transfer_slices=[transfer_slice],
        transfer_cohort_ids=[cohort_manifest.cohort_id],
        transfer_cohorts=[cohort_manifest],
        transfer_cohort_status={cohort_manifest.cohort_id: {"status": "transfer_supported"}},
        claim_tier="transfer_supported",
        transfer_slice_status={transfer_slice.slice_id: {"status": "supported"}},
        model_tier_audit={"default_model": "gpt-5.4-mini", "mini_escalation_required": False},
        attribution_summary={"source_packets": ["got_sorting", "tot_game24"], "target_packets": ["moa_layered", "codetree_patch"]},
        objective_breakdown_status="complete",
        review_required=False,
        metadata={"phase": "next_frontier_b"},
    )
    return {
        "source_cohort_packet": cohort,
        "manifest": manifest,
        "transfer_slice": transfer_slice,
        "transfer_cohort": cohort_manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "promotion_summary": evidence_summary,
        "study_note": {
            "workload": "dag_packet_transfer_follow_on",
            "decision_signal": "codetree_benefits_from_gate1_lessons",
            "pain_classification": "helper_level_only",
        },
    }


def build_next_frontier_optimize_transfer_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_transfer_packet()
    return {
        "manifest": example["manifest"].to_dict(),
        "transfer_slice": example["transfer_slice"].to_dict(),
        "transfer_cohort": example["transfer_cohort"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "promotion_summary": example["promotion_summary"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_optimize_live_experiment_cell() -> Dict[str, object]:
    """Build the first bounded live optimize cell over the new DAG packet tranche."""

    cohort = build_next_frontier_optimize_cohort_packet()
    transfer = build_next_frontier_optimize_transfer_packet()
    live_cell = {
        "cell_id": "optimize.next_frontier.live_cell.dag_packets.v1",
        "cell_kind": "bounded_live_packet_selection",
        "status": "complete",
        "study_objective": "choose the next bounded downstream packet family under explicit budget and evaluator discipline",
        "budget_envelope": {
            "max_llm_calls": 24,
            "max_prompt_tokens": 2400,
            "max_completion_tokens": 900,
            "max_evaluator_calls": 6,
            "decision_budget": 1,
        },
        "entry_criteria": [
            "packet has explicit fidelity scorecard",
            "packet has explicit compute ledger",
            "packet has explicit deviation ledger",
        ],
        "stop_criteria": [
            "decision winner becomes stable under the bounded evaluation stack",
            "budget envelope reached",
            "confound status becomes unclear and would require new evaluator work",
        ],
        "admitted_packets": [
            "packet.search.replication_v1.got_sorting",
            "packet.search.replication_v1.tot_game24",
            "packet.search.replication_v1.moa_layered",
            "packet.search.replication_v1.codetree_patch",
        ],
        "winner": "packet.search.replication_v1.codetree_patch",
        "next_action": "open Frontier C evaluator/verifier packet using codetree as the first new-workload RL consumer",
        "pressure_classification": "optimize_surface_still_helper_local",
        "metadata": {
            "phase": "next_frontier_b",
            "cohort_manifest_id": cohort["manifest"].manifest_id,
            "transfer_manifest_id": transfer["manifest"].manifest_id,
        },
    }
    return {
        "cohort_packet": cohort,
        "transfer_packet": transfer,
        "live_cell": live_cell,
    }


def build_next_frontier_optimize_live_experiment_cell_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_live_experiment_cell()
    return {
        "cohort_packet": build_next_frontier_optimize_cohort_packet_payload(),
        "transfer_packet": build_next_frontier_optimize_transfer_packet_payload(),
        "live_cell": dict(example["live_cell"]),
    }


def build_next_frontier_optimize_pressure_synthesis() -> Dict[str, object]:
    cohort = build_next_frontier_optimize_cohort_packet()
    transfer = build_next_frontier_optimize_transfer_packet()
    live_cell = build_next_frontier_optimize_live_experiment_cell()
    return {
        "synthesis_id": "optimize.next_frontier.pressure_synthesis.v1",
        "evidence_sources": [
            cohort["manifest"].manifest_id,
            transfer["manifest"].manifest_id,
            live_cell["live_cell"]["cell_id"],
        ],
        "repeated_shape_gap_detected": False,
        "recommended_outcome": "keep_optimize_frozen",
        "next_frontier_ready": "frontier_c_rl_adapter_use",
        "pain_summary": [
            "cohort packet stayed reporting-local",
            "transfer packet stayed helper-local",
            "live cell stayed operator-local",
        ],
        "metadata": {"phase": "next_frontier_b"},
    }


def build_next_frontier_optimize_pressure_synthesis_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_pressure_synthesis()
    return {
        "synthesis_id": example["synthesis_id"],
        "evidence_sources": list(example["evidence_sources"]),
        "repeated_shape_gap_detected": example["repeated_shape_gap_detected"],
        "recommended_outcome": example["recommended_outcome"],
        "next_frontier_ready": example["next_frontier_ready"],
        "pain_summary": list(example["pain_summary"]),
        "metadata": dict(example["metadata"]),
    }


def build_staged_backend_comparison_example() -> Dict[str, object]:
    """Build a fixed-methodology V2 staged-vs-reflective backend comparison across the three live families."""

    support = build_support_execution_benchmark_example()
    tool = build_tool_guidance_benchmark_example()
    coding = build_coding_overlay_benchmark_example()

    family_examples = [
        (
            "support_execution",
            support,
            _build_backend_request_for_family_example(
                support,
                request_id="backend_request.support_execution.staged.v2",
                evaluation_id="eval.backend.support_execution.v2",
                evaluator_id="support_execution_checker.v2",
                wrongness_class="policy.support_envelope_violation",
                likely_repair_locus="policy.support_claim_limited_actions",
            ),
        ),
        (
            "tool_guidance",
            tool,
            _build_backend_request_for_family_example(
                tool,
                request_id="backend_request.tool_guidance.staged.v2",
                evaluation_id="eval.backend.tool_guidance.v2",
                evaluator_id="tool_guidance_checker.v2",
                wrongness_class="correctness.result_mismatch",
                likely_repair_locus="tool.render.exec_command",
            ),
        ),
        (
            "coding_overlay",
            coding,
            _build_backend_request_for_family_example(
                coding,
                request_id="backend_request.coding_overlay.staged.v2",
                evaluation_id="eval.backend.coding_overlay.v2",
                evaluator_id="coding_overlay_checker.v2",
                wrongness_class="correctness.result_mismatch",
                likely_repair_locus="prompt.section.planning_policy",
            ),
        ),
    ]

    reflective_results: List[ReflectiveParetoBackendResult] = []
    staged_results: List[ReflectiveParetoBackendResult] = []
    manifest_ids: List[str] = []
    backend_outcome_summary: Dict[str, Dict[str, object]] = {
        "reflective_pareto_backend_v1": {},
        "staged_optimizer.v1": {},
    }
    backend_run_ids: Dict[str, List[str]] = {
        "reflective_pareto_backend_v1": [],
        "staged_optimizer.v1": [],
    }

    for lane_name, example, backend_request in family_examples:
        reflective = run_reflective_pareto_backend(backend_request)
        staged_request = StagedOptimizerRequest(
            request_id=f"staged_request.{lane_name}.v2",
            backend_request=backend_request,
            evaluation_suite=example["evaluation_suite"],
            objective_suite=example["objective_suite"],
            target_family=example["target_family"],
            search_space=example["search_space"],
            metadata={"lane": lane_name},
        )
        staged = run_staged_optimizer(staged_request)
        manifest = example["manifest"]
        assert isinstance(manifest, BenchmarkRunManifest)
        manifest_ids.append(manifest.manifest_id)
        reflective_results.append(reflective)
        staged_results.append(staged)
        backend_run_ids["reflective_pareto_backend_v1"].append(reflective.request_id)
        backend_run_ids["staged_optimizer.v1"].append(staged.request_id)

        reflective_size = len(reflective.portfolio.entries)
        staged_size = len(staged.portfolio.entries)
        winner = "staged_optimizer.v1" if staged_size >= reflective_size else "reflective_pareto_backend_v1"
        rationale = (
            "staged backend preserved family/search-space discipline and matched or exceeded the reflective portfolio width"
            if winner == "staged_optimizer.v1"
            else "reflective backend retained a broader valid portfolio under the same family methodology"
        )
        for backend_id, result in (
            ("reflective_pareto_backend_v1", reflective),
            ("staged_optimizer.v1", staged),
        ):
            backend_outcome_summary[backend_id][lane_name] = {
                "portfolio_size": len(result.portfolio.entries),
                "proposal_count": len(result.proposals),
                "family_id": example["target_family"].family_id,
                "winner_for_family": winner,
                "rationale": rationale,
            }

    comparison = BackendComparisonResult(
        comparison_id="backend_comparison.staged_vs_reflective.v2",
        backend_ids=["reflective_pareto_backend_v1", "staged_optimizer.v1"],
        manifest_ids=manifest_ids,
        backend_run_ids=backend_run_ids,
        backend_outcome_summary=backend_outcome_summary,
        rationale="Staged optimizer is compared against the reflective backend on fixed V2 family/suite/search-space methodology.",
        winner_backend_id="staged_optimizer.v1",
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/backend_comparison/staged_vs_reflective_v2.json",
                media_type="application/json",
            )
        ],
        reproducibility_notes={
            "fixed_methodology": True,
            "family_bound": True,
            "shared_manifests": manifest_ids,
        },
        metadata={
            "family_ids": [example["target_family"].family_id for _, example, _ in family_examples],
            "evaluation_suite_ids": [example["evaluation_suite"].suite_id for _, example, _ in family_examples],
            "objective_suite_ids": [example["objective_suite"].suite_id for _, example, _ in family_examples],
        },
    )

    return {
        "reflective_results": reflective_results,
        "staged_results": staged_results,
        "backend_comparison": comparison,
        "family_examples": [item[1] for item in family_examples],
        "family_requests": [item[2] for item in family_examples],
    }


def build_staged_backend_comparison_example_payload() -> Dict[str, object]:
    example = build_staged_backend_comparison_example()
    return {
        "reflective_results": [item.to_dict() for item in example["reflective_results"]],
        "staged_results": [item.to_dict() for item in example["staged_results"]],
        "family_requests": [item.to_dict() for item in example["family_requests"]],
        "backend_comparison": example["backend_comparison"].to_dict(),
    }


def build_backend_comparison_example() -> Dict[str, object]:
    """Build a fixed-methodology backend comparison across the V1.5 pilot manifests."""

    support = build_support_execution_benchmark_example()
    tool = build_tool_guidance_benchmark_example()
    coding = build_coding_overlay_benchmark_example()
    backend = build_codex_dossier_backend_example()

    request = backend["request"]
    reflective_result = backend["result"]
    greedy_result = run_single_locus_greedy_backend(request)

    reflective_support = _clone_benchmark_run_result(
        support["benchmark_result"],
        run_id="benchmark_run.support_execution.reflective.001",
        metadata={
            **support["benchmark_result"].metadata,
            "backend_id": reflective_result.backend_id,
            "backend_family": "reflective_paired_refinement",
        },
    )
    greedy_support = _clone_benchmark_run_result(
        support["benchmark_result"],
        run_id="benchmark_run.support_execution.greedy.001",
        comparison_results=[
            _clone_comparison_result(
                support["comparison_result"],
                comparison_id="comparison.support_execution.greedy_parent_vs_child.001",
                outcome="inconclusive",
                better_candidate_id=None,
                rationale="The greedy backend found a narrower single-locus candidate, but held-out evidence remained inconclusive for promotion.",
            )
        ],
        aggregate_metrics={
            "parent_correctness_score": 0.62,
            "child_correctness_score": 0.74,
            "support_honesty_score": 0.92,
            "requires_review": True,
        },
        promotion_readiness_summary={
            "eligible_for_promotion": False,
            "requires_review": True,
            "blocked_reason": "single-locus refinement left the support-sensitive hold slice inconclusive",
        },
        metadata={
            **support["benchmark_result"].metadata,
            "backend_id": greedy_result.backend_id,
            "backend_family": "single_locus_greedy",
        },
    )

    reflective_tool = _clone_benchmark_run_result(
        tool["benchmark_result"],
        run_id="benchmark_run.tool_guidance.reflective.001",
        metadata={
            **tool["benchmark_result"].metadata,
            "backend_id": reflective_result.backend_id,
            "backend_family": "reflective_paired_refinement",
        },
    )
    greedy_tool = _clone_benchmark_run_result(
        tool["benchmark_result"],
        run_id="benchmark_run.tool_guidance.greedy.001",
        comparison_results=[
            _clone_comparison_result(
                tool["comparison_result"],
                comparison_id="comparison.tool_guidance.greedy_parent_vs_child.001",
                outcome="non_inferior",
                better_candidate_id=None,
                rationale="The greedy backend preserved the core tool-guidance gain but did not outperform the reflective family on clarity.",
            )
        ],
        aggregate_metrics={"parent_clarity_score": 0.38, "child_clarity_score": 0.73},
        promotion_readiness_summary={"eligible_for_promotion": True, "requires_review": False},
        metadata={
            **tool["benchmark_result"].metadata,
            "backend_id": greedy_result.backend_id,
            "backend_family": "single_locus_greedy",
        },
    )

    reflective_coding = _clone_benchmark_run_result(
        coding["benchmark_result"],
        run_id="benchmark_run.coding_overlay.reflective.001",
        metadata={
            **coding["benchmark_result"].metadata,
            "backend_id": reflective_result.backend_id,
            "backend_family": "reflective_paired_refinement",
        },
    )
    greedy_coding = _clone_benchmark_run_result(
        coding["benchmark_result"],
        run_id="benchmark_run.coding_overlay.greedy.001",
        comparison_results=[
            _clone_comparison_result(
                coding["comparison_result"],
                comparison_id="comparison.coding_overlay.greedy_parent_vs_child.001",
                outcome="loss",
                better_candidate_id=coding["parent_candidate"].candidate_id,
                rationale="The greedy backend missed the bounded-edit tradeoff and lost to the parent on the hidden hold blast-radius slice.",
            )
        ],
        aggregate_metrics={"parent_planning_score": 0.44, "child_planning_score": 0.51},
        promotion_readiness_summary={
            "eligible_for_promotion": False,
            "requires_review": False,
            "blocked_reason": "hidden-hold bounded-edit regression blocked promotion",
        },
        metadata={
            **coding["benchmark_result"].metadata,
            "backend_id": greedy_result.backend_id,
            "backend_family": "single_locus_greedy",
        },
    )

    backend_comparison = BackendComparisonResult(
        comparison_id="backend_comparison.v1_5.reflective_vs_greedy.001",
        backend_ids=[reflective_result.backend_id, greedy_result.backend_id],
        manifest_ids=[
            support["manifest"].manifest_id,
            tool["manifest"].manifest_id,
            coding["manifest"].manifest_id,
        ],
        backend_run_ids={
            reflective_result.backend_id: [
                reflective_support.run_id,
                reflective_tool.run_id,
                reflective_coding.run_id,
            ],
            greedy_result.backend_id: [
                greedy_support.run_id,
                greedy_tool.run_id,
                greedy_coding.run_id,
            ],
        },
        backend_outcome_summary={
            reflective_result.backend_id: {
                "wins": 2,
                "non_inferior": 1,
                "promotable_runs": 2,
                "blocked_runs": 1,
            },
            greedy_result.backend_id: {
                "wins": 0,
                "non_inferior": 1,
                "promotable_runs": 1,
                "blocked_runs": 2,
            },
        },
        winner_backend_id=reflective_result.backend_id,
        rationale=(
            "On the fixed V1.5 manifests, the reflective backend outperformed the single-locus greedy family "
            "on support-sensitive and coding-overlay lanes while matching it on the tool-guidance lane."
        ),
        evidence_refs=[
            ArtifactRef(
                ref="artifacts/optimization/v1_5/backend_comparison_reflective_vs_greedy.json",
                media_type="application/json",
            )
        ],
        reproducibility_notes={
            "comparison_protocol": "paired_parent_child.v1",
            "shared_manifest_count": 3,
            "shared_evaluator_policy": "v1_5_fixed_methodology",
        },
        metadata={"phase": "v1_5", "kind": "backend_comparison"},
    )

    return {
        "request": request,
        "reflective_backend_result": reflective_result,
        "greedy_backend_result": greedy_result,
        "reflective_benchmark_runs": [reflective_support, reflective_tool, reflective_coding],
        "greedy_benchmark_runs": [greedy_support, greedy_tool, greedy_coding],
        "backend_comparison": backend_comparison,
    }


def build_backend_comparison_example_payload() -> Dict[str, object]:
    example = build_backend_comparison_example()
    return {
        "request": example["request"].to_dict(),
        "reflective_backend_result": example["reflective_backend_result"].to_dict(),
        "greedy_backend_result": example["greedy_backend_result"].to_dict(),
        "reflective_benchmark_runs": [item.to_dict() for item in example["reflective_benchmark_runs"]],
        "greedy_benchmark_runs": [item.to_dict() for item in example["greedy_benchmark_runs"]],
        "backend_comparison": example["backend_comparison"].to_dict(),
    }


def build_next_frontier_optimize_second_cohort_packet() -> Dict[str, object]:
    """Build a second bounded optimize cohort packet on downstream-consumer readiness."""

    manifest = BenchmarkRunManifest(
        manifest_id="optimize.next_frontier.cohort.downstream_consumer_readiness.v1",
        benchmark_kind="dag_packet_downstream_consumer_readiness",
        target_id="target.next_frontier.optimize.downstream_consumer_selection",
        dataset_id="dataset.next_frontier.downstream_consumer_readiness.v1",
        dataset_version="2026-04-08.v1",
        baseline_candidate_id="packet.search.replication_v1.moa_layered",
        environment_domain="synthetic_replay_audit",
        evaluator_stack=["adapter_readiness_checker", "replay_parity_checker", "bounded_compute_checker"],
        comparison_protocol="paired_downstream_consumer_compare.v1",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=["sample.consumer.train.moa", "sample.consumer.train.codetree"],
                visibility="mutation_visible",
            ),
            BenchmarkSplit(
                split_name="validation",
                sample_ids=["sample.consumer.validation.follow_on"],
                visibility="comparison_visible",
            ),
            BenchmarkSplit(
                split_name="hold",
                sample_ids=["sample.consumer.hold.adapter_mix"],
                visibility="hidden_hold",
            ),
        ],
        bucket_tags={
            "sample.consumer.train.moa": ["layered_fan_in", "adapter_pack_shape"],
            "sample.consumer.train.codetree": ["critic_feedback", "verifier_readiness"],
            "sample.consumer.validation.follow_on": ["downstream_consumer_selection"],
            "sample.consumer.hold.adapter_mix": ["mixed_consumer_pressure"],
        },
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "model_policy": "gpt-5.4-mini", "budget_mode": "bounded_consumer_compare"},
        contamination_notes=["consumer expectations remain bounded and packet-local"],
        promotion_relevance={"kind": "study_selection_only", "public_artifact_additions_required": False},
        artifact_refs=[
            ArtifactRef(
                ref="docs_tmp/DAG/replication_v1/DAG_FINAL_TRANCHE_SYNTHESIS_V1.md",
                media_type="text/markdown",
            )
        ],
        metadata={"phase": "next_frontier_b", "workload_family": "downstream_consumer_readiness"},
    )
    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.next_frontier.cohort.downstream_consumer_readiness.v1",
        parent_candidate_id="packet.search.replication_v1.moa_layered",
        child_candidate_id="packet.search.replication_v1.codetree_patch",
        outcome="win",
        compared_sample_ids=[
            "sample.consumer.train.moa",
            "sample.consumer.train.codetree",
            "sample.consumer.validation.follow_on",
            "sample.consumer.hold.adapter_mix",
        ],
        held_out_sample_ids=["sample.consumer.hold.adapter_mix"],
        trial_count=1,
        rationale="CodeTree is a stronger downstream-consumer source than MoA once verifier and replay readiness are weighted explicitly.",
        metric_deltas={"adapter_readiness": 0.11, "replay_stability": 0.07, "compute_efficiency": -0.03},
        better_candidate_id="packet.search.replication_v1.codetree_patch",
        metadata={"claim_width": "cohort_only", "bounded_budget": True},
    )
    evaluation_suite = EvaluationSuiteManifest(
        suite_id="evalsuite.next_frontier.optimize.second_cohort.v1",
        suite_kind="bounded_downstream_consumer_compare",
        evaluator_stack=["adapter_readiness_checker", "replay_parity_checker", "bounded_compute_checker"],
        split_visibility={"train": "mutation_visible", "validation": "comparison_visible", "hold": "hidden_hold"},
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "escalation_policy": "disabled"},
        capture_requirements=["consumer_readiness_alignment", "replay_signal_integrity", "budget_envelope_adherence"],
        signal_channels={
            "adapter_readiness": {"source_kind": "consumer_probe"},
            "replay_stability": {"source_kind": "parity_view"},
            "compute": {"source_kind": "ledger"},
        },
        adjudication_requirements={"requires_hidden_hold_review": True, "budget_mode": "bounded_consumer_compare"},
        comparison_protocol_defaults={"protocol_id": manifest.comparison_protocol, "minimum_trial_count": 1},
        artifact_requirements=["consumer_compare_json", "parity_summary_json"],
        metadata={"phase": "next_frontier_b"},
    )
    objective_suite = ObjectiveSuiteManifest(
        suite_id="objsuite.next_frontier.optimize.second_cohort.v1",
        evaluation_suite_id=evaluation_suite.suite_id,
        objective_channels={
            "adapter_readiness": {"direction": "maximize"},
            "replay_stability": {"direction": "maximize"},
            "compute_efficiency": {"direction": "maximize"},
        },
        penalties={"consumer_confound_risk": {"direction": "minimize"}},
        aggregation_rules={"adapter_weight": 0.5, "replay_weight": 0.3, "compute_weight": 0.2},
        uncertainty_policy={"requires_explicit_hold_review": True},
        blocked_channel_annotations={},
        channel_dependencies={"replay_stability": ["adapter_readiness"]},
        frontier_dimensions=["adapter_readiness", "replay_stability", "compute_efficiency"],
        promotion_annotations={"relevance": "study_selection_only"},
        visibility_annotations={"hold": "judge_only"},
        metadata={"phase": "next_frontier_b"},
    )
    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreak.next_frontier.optimize.second_cohort.v1",
        objective_suite_id=objective_suite.suite_id,
        manifest_id=manifest.manifest_id,
        candidate_id=comparison.child_candidate_id,
        per_sample_components={
            "sample.consumer.train.moa": {"adapter_readiness": 0.71, "replay_stability": 0.74, "compute_efficiency": 0.76},
            "sample.consumer.train.codetree": {"adapter_readiness": 0.84, "replay_stability": 0.82, "compute_efficiency": 0.72},
            "sample.consumer.validation.follow_on": {
                "adapter_readiness": 0.81,
                "replay_stability": 0.8,
                "compute_efficiency": 0.73,
            },
        },
        per_bucket_components={
            "consumer_shape": {"adapter_readiness": 0.82, "replay_stability": 0.81},
            "cost": {"compute_efficiency": 0.73},
        },
        aggregate_objectives={"adapter_readiness": 0.82, "replay_stability": 0.8, "compute_efficiency": 0.73},
        uncertainty_summary={"hidden_hold_pending": True},
        blocked_components={},
        signal_status={"consumer_probe": {"status": "present"}},
        slice_status={"consumer_compare": {"status": "complete"}},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/next_frontier/downstream_consumer_compare_summary.json",
                media_type="application/json",
            )
        ],
        metadata={"phase": "next_frontier_b"},
    )
    return {
        "manifest": manifest,
        "evaluation_suite": evaluation_suite,
        "objective_suite": objective_suite,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "study_note": {
            "workload": "downstream_consumer_readiness",
            "decision_signal": "codetree_preferred_for_downstream_consumer_start",
            "pain_classification": "evaluator_and_reporting_only",
        },
    }


def build_next_frontier_optimize_second_cohort_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_second_cohort_packet()
    return {
        "manifest": example["manifest"].to_dict(),
        "evaluation_suite": example["evaluation_suite"].to_dict(),
        "objective_suite": example["objective_suite"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_optimize_second_transfer_packet() -> Dict[str, object]:
    """Build a second bounded optimize transfer packet using a real selection prior."""

    second_cohort = build_next_frontier_optimize_second_cohort_packet()
    manifest = BenchmarkRunManifest(
        manifest_id="optimize.next_frontier.transfer.consumer_selection_prior.v1",
        benchmark_kind="dag_packet_consumer_follow_on",
        target_id="target.next_frontier.optimize.consumer_follow_on",
        dataset_id="dataset.next_frontier.consumer_follow_on.v1",
        dataset_version="2026-04-08.v1",
        baseline_candidate_id="packet.search.replication_v1.tot_game24",
        environment_domain="synthetic_replay_audit",
        evaluator_stack=["selection_prior_checker", "bounded_compute_checker", "consumer_review_judge"],
        comparison_protocol="paired_consumer_follow_on_compare.v1",
        splits=[
            BenchmarkSplit(
                split_name="train",
                sample_ids=["sample.consumer_transfer.train.codetree"],
                visibility="mutation_visible",
            ),
            BenchmarkSplit(
                split_name="validation",
                sample_ids=["sample.consumer_transfer.validation.follow_on"],
                visibility="comparison_visible",
            ),
            BenchmarkSplit(
                split_name="hold",
                sample_ids=["sample.consumer_transfer.hold.cross_consumer"],
                visibility="hidden_hold",
            ),
        ],
        stochasticity_class="deterministic",
        rerun_policy={"max_trials": 1, "model_policy": "gpt-5.4-mini", "transfer_enabled": True},
        contamination_notes=["selection prior remains bounded to downstream-consumer start policy"],
        metadata={"phase": "next_frontier_b", "workload_family": "consumer_follow_on"},
    )
    transfer_slice = TransferSliceManifest(
        slice_id="slice.next_frontier.transfer.consumer_selection_prior.v1",
        slice_kind="repo_family",
        selector={
            "source_live_cell": "optimize.next_frontier.live_cell.dag_packets.v1",
            "selection_prior": "prefer_codetree_for_downstream_consumers",
        },
        promotion_role="claim_supporting",
        visibility="comparison_visible",
        metadata={"phase": "next_frontier_b"},
    )
    cohort_manifest = TransferCohortManifest(
        cohort_id="cohort.next_frontier.optimize.consumer_selection_prior.v1",
        cohort_kind="bounded_packet_family_transfer",
        member_slice_ids=[transfer_slice.slice_id],
        claim_scope={"source": "first_live_cell_winner", "target": "second_loop_consumer_follow_on", "max_family_count": 1},
        coverage_policy={"requires_hidden_hold": True, "claim_tiers": ["package_local", "transfer_supported"]},
        metadata={"phase": "next_frontier_b"},
    )
    comparison = build_paired_candidate_comparison(
        manifest,
        comparison_id="comparison.next_frontier.transfer.consumer_selection_prior.v1",
        parent_candidate_id="packet.search.replication_v1.tot_game24",
        child_candidate_id="packet.search.replication_v1.codetree_patch",
        outcome="win",
        compared_sample_ids=[
            "sample.consumer_transfer.train.codetree",
            "sample.consumer_transfer.validation.follow_on",
            "sample.consumer_transfer.hold.cross_consumer",
        ],
        held_out_sample_ids=["sample.consumer_transfer.hold.cross_consumer"],
        trial_count=1,
        rationale="Using the first live-cell winner as a start prior improves the second consumer-focused follow-on packet.",
        metric_deltas={"selection_prior_gain": 0.09, "claim_honesty": 0.05, "compute_efficiency": -0.01},
        better_candidate_id="packet.search.replication_v1.codetree_patch",
        metadata={"transfer_enabled": True, "selection_prior": "codetree_first"},
    )
    objective_breakdown = ObjectiveBreakdownResult(
        result_id="objbreak.next_frontier.optimize.second_transfer.v1",
        objective_suite_id="objsuite.next_frontier.optimize.second_transfer.v1",
        manifest_id=manifest.manifest_id,
        candidate_id=comparison.child_candidate_id,
        per_sample_components={
            "sample.consumer_transfer.train.codetree": {"selection_prior_gain": 0.08, "claim_honesty": 0.84},
            "sample.consumer_transfer.validation.follow_on": {"selection_prior_gain": 0.09, "claim_honesty": 0.86},
        },
        per_bucket_components={"consumer_follow_on": {"selection_prior_gain": 0.085, "claim_honesty": 0.85}},
        aggregate_objectives={"selection_prior_gain": 0.085, "claim_honesty": 0.85},
        uncertainty_summary={"hidden_hold_pending": True},
        blocked_components={},
        signal_status={"selection_prior": {"status": "supported"}},
        slice_status={"consumer_follow_on": {"status": "complete"}},
        artifact_refs=[
            ArtifactRef(
                ref="artifacts/optimization/next_frontier/consumer_selection_prior_summary.json",
                media_type="application/json",
            )
        ],
        metadata={"phase": "next_frontier_b"},
    )
    evidence_summary = PromotionEvidenceSummary(
        summary_id="evidence_summary.next_frontier.second_transfer.v1",
        candidate_id=comparison.child_candidate_id,
        comparison_ids=[comparison.comparison_id],
        manifest_ids=[manifest.manifest_id],
        held_out_sample_ids=["sample.consumer_transfer.hold.cross_consumer"],
        evaluation_suite_ids=[second_cohort["evaluation_suite"].suite_id],
        objective_suite_ids=[second_cohort["objective_suite"].suite_id],
        objective_breakdown_result_ids=[objective_breakdown.result_id],
        transfer_slice_ids=[transfer_slice.slice_id],
        transfer_slices=[transfer_slice],
        transfer_cohort_ids=[cohort_manifest.cohort_id],
        transfer_cohorts=[cohort_manifest],
        transfer_cohort_status={cohort_manifest.cohort_id: {"status": "transfer_supported"}},
        claim_tier="transfer_supported",
        transfer_slice_status={transfer_slice.slice_id: {"status": "supported"}},
        model_tier_audit={"default_model": "gpt-5.4-mini", "mini_escalation_required": False},
        attribution_summary={"source_signal": "first_live_cell_winner", "target_follow_on": "consumer_selection_prior"},
        objective_breakdown_status="complete",
        review_required=False,
        metadata={"phase": "next_frontier_b"},
    )
    return {
        "source_cohort_packet": second_cohort,
        "manifest": manifest,
        "transfer_slice": transfer_slice,
        "transfer_cohort": cohort_manifest,
        "comparison_result": comparison,
        "objective_breakdown_result": objective_breakdown,
        "promotion_summary": evidence_summary,
        "study_note": {
            "workload": "consumer_selection_prior_follow_on",
            "decision_signal": "codetree_prior_helped_second_consumer_loop",
            "pain_classification": "helper_level_only",
        },
    }


def build_next_frontier_optimize_second_transfer_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_second_transfer_packet()
    return {
        "manifest": example["manifest"].to_dict(),
        "transfer_slice": example["transfer_slice"].to_dict(),
        "transfer_cohort": example["transfer_cohort"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "objective_breakdown_result": example["objective_breakdown_result"].to_dict(),
        "promotion_summary": example["promotion_summary"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_next_frontier_optimize_second_live_experiment_cell() -> Dict[str, object]:
    """Build a second bounded live optimize cell over downstream-consumer selection."""

    second_cohort = build_next_frontier_optimize_second_cohort_packet()
    second_transfer = build_next_frontier_optimize_second_transfer_packet()
    live_cell = {
        "cell_id": "optimize.next_frontier.live_cell.downstream_consumers.v2",
        "cell_kind": "bounded_live_consumer_selection",
        "status": "complete",
        "study_objective": "choose the best downstream-consumer start packet under consumer-readiness and replay constraints",
        "budget_envelope": {
            "max_llm_calls": 18,
            "max_prompt_tokens": 1800,
            "max_completion_tokens": 700,
            "max_evaluator_calls": 5,
            "decision_budget": 1,
        },
        "entry_criteria": [
            "packet has bounded consumer-readiness evaluation",
            "packet has explicit replay signal",
            "packet has explicit baseline identity",
        ],
        "stop_criteria": [
            "winner is stable under the bounded consumer stack",
            "budget envelope reached",
            "consumer confound status becomes unclear",
        ],
        "admitted_packets": [
            "packet.search.replication_v1.moa_layered",
            "packet.search.replication_v1.codetree_patch",
            "packet.search.replication_v1.tot_game24",
        ],
        "winner": "packet.search.replication_v1.codetree_patch",
        "next_action": "open Frontier D composition using CodeTree as the first cross-system source packet",
        "pressure_classification": "optimize_surface_still_helper_local",
        "metadata": {
            "phase": "next_frontier_b",
            "second_cohort_manifest_id": second_cohort["manifest"].manifest_id,
            "second_transfer_manifest_id": second_transfer["manifest"].manifest_id,
        },
    }
    return {
        "second_cohort_packet": second_cohort,
        "second_transfer_packet": second_transfer,
        "live_cell": live_cell,
    }


def build_next_frontier_optimize_second_live_experiment_cell_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_second_live_experiment_cell()
    return {
        "second_cohort_packet": build_next_frontier_optimize_second_cohort_packet_payload(),
        "second_transfer_packet": build_next_frontier_optimize_second_transfer_packet_payload(),
        "live_cell": dict(example["live_cell"]),
    }


def build_next_frontier_optimize_tranche_synthesis_v2() -> Dict[str, object]:
    first_cohort = build_next_frontier_optimize_cohort_packet()
    first_transfer = build_next_frontier_optimize_transfer_packet()
    first_live_cell = build_next_frontier_optimize_live_experiment_cell()
    second_cohort = build_next_frontier_optimize_second_cohort_packet()
    second_transfer = build_next_frontier_optimize_second_transfer_packet()
    second_live_cell = build_next_frontier_optimize_second_live_experiment_cell()
    return {
        "synthesis_id": "optimize.next_frontier.tranche_synthesis.v2",
        "evidence_sources": [
            first_cohort["manifest"].manifest_id,
            first_transfer["manifest"].manifest_id,
            first_live_cell["live_cell"]["cell_id"],
            second_cohort["manifest"].manifest_id,
            second_transfer["manifest"].manifest_id,
            second_live_cell["live_cell"]["cell_id"],
        ],
        "repeated_shape_gap_detected": False,
        "recommended_outcome": "keep_optimize_frozen",
        "next_frontier_ready": "frontier_d_cross_system_composition",
        "pain_summary": [
            first_cohort["study_note"]["pain_classification"],
            first_transfer["study_note"]["pain_classification"],
            second_cohort["study_note"]["pain_classification"],
            second_transfer["study_note"]["pain_classification"],
        ],
        "metadata": {"phase": "next_frontier_b", "loop_count": 2},
    }


def build_next_frontier_optimize_tranche_synthesis_v2_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_tranche_synthesis_v2()
    return {
        "synthesis_id": example["synthesis_id"],
        "evidence_sources": list(example["evidence_sources"]),
        "repeated_shape_gap_detected": example["repeated_shape_gap_detected"],
        "recommended_outcome": example["recommended_outcome"],
        "next_frontier_ready": example["next_frontier_ready"],
        "pain_summary": list(example["pain_summary"]),
        "metadata": dict(example["metadata"]),
    }


def build_next_frontier_dag_to_optimize_composition_packet() -> Dict[str, object]:
    """Build the first DAG-to-optimize composition packet."""

    second_cohort = build_next_frontier_optimize_second_cohort_packet()
    return {
        "composition_id": "composition.next_frontier.dag_to_optimize.v1",
        "source_packet_id": "packet.search.replication_v1.codetree_patch",
        "source_tranche_note": "docs_tmp/DAG/replication_v1/DAG_FINAL_TRANCHE_SYNTHESIS_V1.md",
        "consumer_manifest_id": second_cohort["manifest"].manifest_id,
        "handoff_contract": {
            "preserved_fields": [
                "recipe_manifest_identity",
                "fidelity_scorecard_identity",
                "compute_ledger_identity",
                "baseline_packet_identity",
            ],
            "claim_limit_honesty": True,
            "helper_only_handoff": True,
        },
        "composition_report": {
            "composed_cleanly": True,
            "awkwardness_classification": "helper_level_only",
            "repeated_shape_gap_detected": False,
        },
        "metadata": {"phase": "next_frontier_d"},
    }


def build_next_frontier_dag_to_optimize_composition_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_dag_to_optimize_composition_packet()
    return {
        "composition_id": example["composition_id"],
        "source_packet_id": example["source_packet_id"],
        "source_tranche_note": example["source_tranche_note"],
        "consumer_manifest_id": example["consumer_manifest_id"],
        "handoff_contract": dict(example["handoff_contract"]),
        "composition_report": dict(example["composition_report"]),
        "metadata": dict(example["metadata"]),
    }


def build_next_frontier_optimize_final_closeout_packet() -> Dict[str, object]:
    """Build the final optimize closeout packet for the next-frontier program."""

    synthesis = build_next_frontier_optimize_tranche_synthesis_v2()
    return {
        "closeout_id": "optimize.next_frontier.final_closeout.v1",
        "source_synthesis_id": synthesis["synthesis_id"],
        "reviewed_loops": 2,
        "reviewed_packet_count": 6,
        "final_decision": "keep_optimize_frozen",
        "repeated_shape_gap_detected": False,
        "reopen_criteria": [
            "the same missing optimize shape repeats across more than one workload family",
            "the same missing optimize shape repeats across more than one live-cell type",
            "helper, evaluator, and reporting fixes are no longer sufficient",
        ],
        "proven_capabilities": [
            "bounded cohort comparison",
            "bounded transfer-style follow-on selection",
            "bounded live experiment cells",
        ],
        "not_proven": [
            "broad benchmark-scale generalization",
            "need for a new optimize public ontology",
        ],
        "metadata": {"phase": "next_frontier_b", "loop_count": 2},
    }


def build_next_frontier_optimize_final_closeout_packet_payload() -> Dict[str, object]:
    example = build_next_frontier_optimize_final_closeout_packet()
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
