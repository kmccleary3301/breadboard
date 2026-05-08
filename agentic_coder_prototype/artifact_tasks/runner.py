from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .contracts import ArtifactContract, ArtifactValidationResult, validate_artifact_contract
from .evaluators import EvaluatorResult, EvaluatorSpec, run_evaluators
from .evidence import EvidenceBundleManifest, write_evidence_bundle
from .materialize import MaterializationResult, MaterializationSpec, materialize_response_artifact


@dataclass(frozen=True)
class ArtifactTaskSpec:
    task_id: str
    candidate_id: str
    task_text: str = ""
    task_file: str | None = None
    response_text: str = ""
    response_file: str | None = None
    artifact_contract: ArtifactContract | Mapping[str, Any] | None = None
    materialization: MaterializationSpec | Mapping[str, Any] | None = None
    evaluators: Sequence[EvaluatorSpec | Mapping[str, Any]] = ()
    workspace_root: str | None = None
    out_dir: str = ""
    route: Mapping[str, Any] = field(default_factory=dict)
    notes: Mapping[str, Any] = field(default_factory=dict)
    run_evaluators_on_artifact_failure: bool = False

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise ValueError("task_id must be non-empty")
        if not str(self.candidate_id or "").strip():
            raise ValueError("candidate_id must be non-empty")
        if not self.artifact_contract:
            raise ValueError("artifact_contract is required")
        if not self.out_dir:
            raise ValueError("out_dir is required")
        contract = (
            self.artifact_contract
            if isinstance(self.artifact_contract, ArtifactContract)
            else ArtifactContract(**dict(self.artifact_contract))
        )
        materialization = (
            self.materialization
            if isinstance(self.materialization, MaterializationSpec) or self.materialization is None
            else MaterializationSpec.from_dict(self.materialization)
        )
        evaluators = tuple(item if isinstance(item, EvaluatorSpec) else EvaluatorSpec.from_dict(item) for item in self.evaluators)
        object.__setattr__(self, "artifact_contract", contract)
        object.__setattr__(self, "materialization", materialization)
        object.__setattr__(self, "evaluators", evaluators)
        object.__setattr__(self, "route", dict(self.route or {}))
        object.__setattr__(self, "notes", dict(self.notes or {}))


@dataclass(frozen=True)
class ArtifactTaskResult:
    task_id: str
    candidate_id: str
    status: str
    ok: bool
    artifact_validation: ArtifactValidationResult
    materialization: MaterializationResult | None
    evaluators: tuple[EvaluatorResult, ...]
    evidence_manifest: EvidenceBundleManifest
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "ok": self.ok,
            "failure_reasons": list(self.failure_reasons),
            "artifact_validation": self.artifact_validation.to_dict(),
            "materialization": self.materialization.to_dict() if self.materialization else None,
            "evaluators": [item.to_dict() for item in self.evaluators],
            "evidence_manifest": self.evidence_manifest.to_dict(),
        }


def _read_optional_text(text: str, file_path: str | None, *, field_name: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    if text:
        return text
    raise ValueError(f"{field_name} or {field_name}_file must be provided")


def _status_from(
    materialization: MaterializationResult | None,
    validation: ArtifactValidationResult,
    evaluators: Sequence[EvaluatorResult],
) -> tuple[str, tuple[str, ...]]:
    failures: list[str] = []
    if materialization and not materialization.ok:
        failures.extend(f"materialization:{item}" for item in materialization.failure_reasons)
    if not validation.ok:
        failures.extend(f"artifact:{item}" for item in validation.failure_reasons)
    for result in evaluators:
        if result.required and not result.ok:
            failures.extend(f"evaluator:{result.name}:{item}" for item in result.failure_reasons or (result.status,))
    if failures:
        return "failed", tuple(failures)
    return "passed", ()


def run_artifact_task(spec: ArtifactTaskSpec) -> ArtifactTaskResult:
    task_text = _read_optional_text(spec.task_text, spec.task_file, field_name="task_text")
    response_text = _read_optional_text(spec.response_text, spec.response_file, field_name="response_text")
    workspace_root = Path(spec.workspace_root).resolve() if spec.workspace_root else Path(spec.out_dir).resolve() / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    out_dir = Path(spec.out_dir).resolve()
    bundle_dir = out_dir / "evidence_bundle"

    materialization_result: MaterializationResult | None = None
    if spec.artifact_contract.mode == "response_materialize":
        if spec.materialization is None:
            raise ValueError("materialization spec is required for response_materialize mode")
        materialization_result = materialize_response_artifact(response_text, spec.materialization, root=workspace_root)

    validation = validate_artifact_contract(spec.artifact_contract, root=workspace_root)
    evaluator_results: tuple[EvaluatorResult, ...] = ()
    materialization_ok = materialization_result is None or materialization_result.ok
    if spec.evaluators and (validation.ok and materialization_ok or spec.run_evaluators_on_artifact_failure):
        evaluator_results = run_evaluators(spec.evaluators, root=workspace_root, output_dir=out_dir / "evaluator_stage")

    status, failures = _status_from(materialization_result, validation, evaluator_results)
    manifest = write_evidence_bundle(
        bundle_dir=bundle_dir,
        task_id=spec.task_id,
        candidate_id=spec.candidate_id,
        status=status,
        task_text=task_text,
        response_text=response_text,
        artifact_root=workspace_root,
        validation=validation,
        materialization=materialization_result,
        evaluator_results=evaluator_results,
        route=spec.route,
        workspace={"workspace_root": str(workspace_root)},
        notes=spec.notes,
        failure_reasons=failures,
    )
    return ArtifactTaskResult(
        task_id=spec.task_id,
        candidate_id=spec.candidate_id,
        status=status,
        ok=status == "passed",
        artifact_validation=validation,
        materialization=materialization_result,
        evaluators=evaluator_results,
        evidence_manifest=manifest,
        failure_reasons=failures,
    )
