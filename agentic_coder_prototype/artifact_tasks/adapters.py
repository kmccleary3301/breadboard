from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.optimize.diagnostics import DiagnosticBundle, DiagnosticEntry
from agentic_coder_prototype.optimize.evaluation import EvaluationRecord
from agentic_coder_prototype.optimize.substrate import ArtifactRef
from agentic_coder_prototype.optimize.trajectory_ir import TrajectoryEpisode, TrajectoryStep
from agentic_coder_prototype.optimize.wrongness import WrongnessReport

from .evidence import utc_now
from .contracts import hash_file
from .runner import ArtifactTaskResult


def artifact_task_result_to_candidate_packet(result: ArtifactTaskResult) -> Dict[str, Any]:
    return {
        "packet_kind": "artifact_task_candidate.v1",
        "task_id": result.task_id,
        "candidate_id": result.candidate_id,
        "status": result.status,
        "ok": result.ok,
        "failure_reasons": list(result.failure_reasons),
        "artifact_validation": result.artifact_validation.to_dict(),
        "materialization": result.materialization.to_dict() if result.materialization else None,
        "evaluators": [item.to_dict() for item in result.evaluators],
        "evidence_manifest_path": result.evidence_manifest.manifest_path,
    }


def artifact_task_result_to_optimize_evaluation_record(
    result: ArtifactTaskResult,
    *,
    target_id: str = "artifact_task_target",
    dataset_id: str = "artifact_task_dataset",
    dataset_version: str = "v1",
    evaluator_id: str = "artifact_task_external_evaluators",
    evaluator_version: str = "v1",
) -> EvaluationRecord:
    evaluation_id = f"artifact-task::{result.task_id}::{result.candidate_id}"
    evidence_ref = ArtifactRef(
        ref=result.evidence_manifest.manifest_path,
        digest=hash_file(Path(result.evidence_manifest.manifest_path)),
        media_type="application/json",
        metadata={"schema_version": result.evidence_manifest.schema_version},
    )
    severity = "info" if result.ok else "error"
    diagnostic = DiagnosticBundle(
        bundle_id=f"{evaluation_id}::diagnostics",
        evaluation_id=evaluation_id,
        evaluator_mode="replay",
        determinism_class="deterministic",
        entries=[
            DiagnosticEntry(
                diagnostic_id=f"{evaluation_id}::status",
                kind="artifact_task_status",
                severity=severity,
                message=f"artifact task {result.status}",
                evidence_refs=[evidence_ref],
                metadata={"failure_reasons": list(result.failure_reasons)},
            )
        ],
        cache_identity={"key": evaluation_id, "version": "v1"},
        metadata={"source": "artifact_tasks"},
    )
    wrongness = []
    if not result.ok:
        wrongness.append(
            WrongnessReport(
                wrongness_id=f"{evaluation_id}::wrongness",
                wrongness_class="correctness.missing_required_output",
                failure_locus="artifact_task",
                explanation="Artifact task did not satisfy required artifact/evaluator gates.",
                confidence=1.0,
                supporting_evidence_refs=[evidence_ref],
                likely_repair_locus="artifact_task",
                metadata={"failure_reasons": list(result.failure_reasons)},
            )
        )
    now = utc_now()
    return EvaluationRecord(
        evaluation_id=evaluation_id,
        target_id=target_id,
        candidate_id=result.candidate_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        sample_id=result.task_id,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        status="completed" if result.ok else "failed",
        outcome="passed" if result.ok else "failed",
        started_at=now,
        completed_at=now,
        duration_ms=0,
        raw_evidence_refs=[evidence_ref],
        normalized_diagnostics=[diagnostic],
        wrongness_reports=wrongness,
        gate_results={"artifact_task": result.ok},
        metadata=artifact_task_result_to_candidate_packet(result),
    )


def artifact_task_result_to_rl_episode(result: ArtifactTaskResult) -> TrajectoryEpisode:
    reward = 1.0 if result.ok else 0.0
    step = TrajectoryStep(
        turn=0,
        observation={
            "task_id": result.task_id,
            "candidate_id": result.candidate_id,
            "status": result.status,
            "evidence_manifest_path": result.evidence_manifest.manifest_path,
        },
        action={"artifact_task_result": artifact_task_result_to_candidate_packet(result)},
        reward=reward,
        metadata={"failure_reasons": list(result.failure_reasons)},
    )
    return TrajectoryEpisode(
        run_id=f"artifact-task::{result.task_id}::{result.candidate_id}",
        steps=[step],
        summary={"ok": result.ok, "status": result.status},
        reward_v1={"reward": reward, "source": "artifact_task_status"},
        notes={"trajectory_version": "artifact-task-v1"},
    )
