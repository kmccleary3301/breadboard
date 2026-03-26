from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    DiagnosticBundle,
    EvaluationRecord,
    WrongnessReport,
    build_codex_dossier_evaluation_example,
    build_codex_dossier_evaluation_example_payload,
)


def test_evaluation_example_round_trip() -> None:
    payload = build_codex_dossier_evaluation_example_payload()
    evaluation = EvaluationRecord.from_dict(payload["evaluation"])
    diagnostics = DiagnosticBundle.from_dict(payload["diagnostics"])
    wrongness = WrongnessReport.from_dict(payload["wrongness_report"])

    assert evaluation.evaluation_id == diagnostics.evaluation_id
    assert evaluation.wrongness_reports[0] == wrongness
    assert evaluation.aggregate_outcome() == "rejected_by_gate"


def test_evaluation_example_lineage_is_explicit() -> None:
    example = build_codex_dossier_evaluation_example()
    evaluation = example["evaluation"]
    wrongness = example["wrongness_report"]

    assert isinstance(evaluation, EvaluationRecord)
    assert isinstance(wrongness, WrongnessReport)
    assert evaluation.target_id == "target.codex_dossier.tool_render"
    assert evaluation.dataset_id == "dataset.codex_dossier.substrate.v1"
    assert evaluation.sample_id == "sample.codex_dossier.exec_command.v1"
    assert wrongness.failure_locus in {"tool.render.exec_command", "prompt.section.optimization_guidance"}
    assert wrongness.likely_repair_locus == "tool.render.exec_command"


def test_evaluation_record_rejects_missing_terminal_timing() -> None:
    example = build_codex_dossier_evaluation_example()
    evaluation = example["evaluation"]
    payload = evaluation.to_dict()
    payload.pop("completed_at", None)

    with pytest.raises(ValueError, match="completed_at"):
        EvaluationRecord.from_dict(payload)
