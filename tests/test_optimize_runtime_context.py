from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    MCPContextRequirement,
    OptimizationDataset,
    OptimizationRuntimeContext,
    OptimizationSample,
    ReflectiveParetoBackendRequest,
    ReflectiveParetoBackendResult,
    SandboxContextRequirement,
    ServiceContextRequirement,
    ToolPackContext,
    ToolRequirement,
    build_codex_dossier_dataset_example,
    build_codex_dossier_runtime_context_examples,
    build_codex_dossier_runtime_context_examples_payload,
)


def test_runtime_context_examples_round_trip() -> None:
    payload = build_codex_dossier_runtime_context_examples_payload()
    compatible_request = ReflectiveParetoBackendRequest.from_dict(payload["compatible"]["request"])
    compatible_result = ReflectiveParetoBackendResult.from_dict(payload["compatible"]["result"])
    incompatible_request = ReflectiveParetoBackendRequest.from_dict(payload["incompatible"]["request"])
    incompatible_result = ReflectiveParetoBackendResult.from_dict(payload["incompatible"]["result"])
    incompatible_dataset = OptimizationDataset.from_dict(payload["incompatible"]["dataset"])
    incompatible_sample = OptimizationSample.from_dict(payload["incompatible"]["sample"])

    assert compatible_request.execution_context is not None
    assert compatible_result.execution_context.sample_id == compatible_request.active_sample_id
    assert incompatible_request.execution_context is not None
    assert incompatible_result.compatibility_results[0].status == "incompatible"
    assert incompatible_dataset.dataset_runtime_context is not None
    assert incompatible_sample.runtime_context().tool_pack_context.required_tool_names() == [
        "exec_command",
        "mcp_manager",
    ]


def test_runtime_context_compatible_backend_path_retains_compatible_results() -> None:
    example = build_codex_dossier_runtime_context_examples()
    compatible = example["compatible"]
    result = compatible["result"]

    assert all(item.status == "compatible" for item in result.compatibility_results)
    assert any(entry.metadata["runtime_compatibility"]["status"] == "compatible" for entry in result.portfolio.entries)


def test_runtime_context_incompatible_backend_path_is_blocked() -> None:
    example = build_codex_dossier_runtime_context_examples()
    incompatible = example["incompatible"]
    result = incompatible["result"]

    assert result.reflection_decision.should_mutate is False
    assert "runtime context" in str(result.reflection_decision.declined_reason)
    assert result.compatibility_results[0].status == "incompatible"
    categories = {issue.category for issue in result.compatibility_results[0].issues}
    assert {"tool_pack", "environment_selector", "service", "mcp", "sandbox"} <= categories


def test_runtime_context_rejects_contradictory_network_requirements() -> None:
    base = build_codex_dossier_dataset_example()
    truth = base["ground_truth_package"]
    with pytest.raises(ValueError, match="network access"):
        OptimizationSample(
            sample_id="sample.invalid.runtime_context",
            target_id="target.codex_dossier.tool_render",
            prompt_input="bad runtime context",
            tool_pack_context=ToolPackContext(
                pack_id="toolpack.invalid",
                profile="invalid",
                required_tools=[ToolRequirement(tool_name="exec_command")],
            ),
            service_contexts=[
                ServiceContextRequirement(
                    service_id="remote_service",
                    selector="remote",
                    requires_network_access=True,
                )
            ],
            sandbox_context=SandboxContextRequirement(
                sandbox_profile="workspace-default",
                filesystem_mode="workspace-write",
                network_access=False,
            ),
            mcp_context=MCPContextRequirement(
                server_names=["github"],
                required_resources=["repos"],
                requires_network_access=False,
            ),
            ground_truth_package=truth,
        )


def test_runtime_context_helper_preserves_typed_context() -> None:
    example = build_codex_dossier_dataset_example()
    sample = example["sample"]
    runtime_context = sample.runtime_context()

    assert isinstance(runtime_context, OptimizationRuntimeContext)
    assert runtime_context.service_contexts[0].service_id == "repo_service"
    assert runtime_context.mcp_context is not None
    assert runtime_context.mcp_context.requires_network_access is False
