from __future__ import annotations

from typing import Dict

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
    run_reflective_pareto_backend,
    run_single_locus_greedy_backend,
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
from .suites import (
    EvaluationSuiteManifest,
    ObjectiveBreakdownResult,
    ObjectiveSuiteManifest,
    SearchSpaceManifest,
    TargetFamilyManifest,
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
        metadata={"lane": "tool_guidance", "phase": "v1_5"},
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
        promotion_readiness_summary={"eligible_for_promotion": True, "requires_review": False},
        metadata={"lane": "tool_guidance", "phase": "v1_5"},
    )

    return {
        "target": target,
        "dataset": dataset,
        "parent_candidate": parent_candidate,
        "child_candidate": child_candidate,
        "parent_materialized_candidate": parent_materialized,
        "child_materialized_candidate": child_materialized,
        "manifest": manifest,
        "comparison_result": comparison,
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
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
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
        metadata={"lane": "coding_overlay", "phase": "v1_5"},
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
        promotion_readiness_summary={"eligible_for_promotion": True, "requires_review": False},
        metadata={"lane": "coding_overlay", "phase": "v1_5"},
    )

    return {
        "target": target,
        "dataset": dataset,
        "parent_candidate": parent_candidate,
        "child_candidate": child_candidate,
        "parent_materialized_candidate": parent_materialized,
        "child_materialized_candidate": child_materialized,
        "manifest": manifest,
        "comparison_result": comparison,
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
        "manifest": example["manifest"].to_dict(),
        "comparison_result": example["comparison_result"].to_dict(),
        "benchmark_result": example["benchmark_result"].to_dict(),
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
