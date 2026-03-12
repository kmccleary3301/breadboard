from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    CorrectnessRationale,
    GroundTruthPackage,
    OptimizationDataset,
    OptimizationSample,
    build_codex_dossier_dataset_example,
    build_codex_dossier_dataset_example_payload,
)


def test_dataset_example_is_self_consistent() -> None:
    example = build_codex_dossier_dataset_example()
    dataset = example["dataset"]
    sample = example["sample"]
    truth = example["ground_truth_package"]
    rationale = example["correctness_rationale"]

    assert isinstance(dataset, OptimizationDataset)
    assert isinstance(sample, OptimizationSample)
    assert isinstance(truth, GroundTruthPackage)
    assert isinstance(rationale, CorrectnessRationale)
    assert dataset.sample_ids() == [sample.sample_id]
    assert truth.rationale_refs == [rationale.rationale_id]
    runtime_context = sample.runtime_context()
    assert runtime_context.environment_selector.profile == "workspace-write"
    assert runtime_context.tool_pack_context.required_tool_names() == [
        "exec_command",
        "apply_patch",
        "spawn_agent",
    ]
    assert dataset.dataset_runtime_context is not None


def test_dataset_example_payload_round_trips() -> None:
    payload = build_codex_dossier_dataset_example_payload()

    dataset = OptimizationDataset.from_dict(payload["dataset"])
    sample = OptimizationSample.from_dict(payload["sample"])
    truth = GroundTruthPackage.from_dict(payload["ground_truth_package"])
    rationale = CorrectnessRationale.from_dict(payload["correctness_rationale"])

    assert dataset.sample_ids() == [sample.sample_id]
    assert sample.ground_truth_package.package_id == truth.package_id
    assert truth.rationale_refs == [rationale.rationale_id]
    assert sample.environment_selector is not None
    assert sample.tool_pack_context is not None
    assert dataset.dataset_runtime_context is not None


def test_dataset_rejects_unknown_rationale_refs() -> None:
    truth = GroundTruthPackage(
        package_id="gt.unknown",
        oracle_kind="spec",
        expected_result={"ok": True},
        rationale_refs=["rat.missing"],
    )
    sample = OptimizationSample(
        sample_id="sample.unknown",
        target_id="target.unknown",
        prompt_input="Test sample",
        ground_truth_package=truth,
    )

    with pytest.raises(ValueError, match="unknown rationales"):
        OptimizationDataset(
            dataset_id="dataset.invalid",
            dataset_version="v1",
            samples=[sample],
            rationale_catalog=[],
        )
